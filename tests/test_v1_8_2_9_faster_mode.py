"""1.8.2.9 (E4, decisions/0007) - "Faster" replaces "Fastest".

The stored value stays `max`. Until 1.8.2.8 it sent a second request at the first instant
of EVERY reply and never looked at the budget. Now a healthy reply sends one request: the
first backup goes out at 1.5x the model's usual first word (cold start: half the class's
number), the second at 3x, a non-streamed call hedges at 0.75 of the learned time, the
budget is three times Balanced's and really consumed, and a rate-limit or quota refusal on
any leg puts that model back to Balanced for ten minutes.

Driven through the tracker and through `_kame_carousel` with the 1.8.2.7 harness. No
Agent Zero installation, no network.
"""
from __future__ import annotations

import time

import pytest

from test_v1_8_2_7_speed_fallback import (  # noqa: F401  (fixtures are used by name)
    ID, KEYS, Refusal, clean, fake_models, make_ctx, run, streaming, _first_then_rest,
)
import kame_engine as K
import kame_hedging as H

MODEL = "google:gemini/gemini-3.8-flash"


@pytest.fixture(autouse=True)
def tracker():
    H.HEDGE_TRACKER.clear()
    H.configure(hedge_mode="adaptive", hedge_budget_pct=10.0, hedge_burst=3.0,
                hedge_cold_s=6.0, hedge_cold_reasoning_s=30.0)
    yield
    H.HEDGE_TRACKER.clear()
    H.configure(hedge_mode="adaptive", hedge_budget_pct=10.0)


def _learn(identity, seconds, n=30, reasoning=False):
    for _ in range(n):
        H.HEDGE_TRACKER.record_latency(identity, seconds, stream=True, reasoning=reasoning)


def test_faster_never_fires_from_dispatch():
    H.configure(hedge_mode="max")
    assert H.HEDGE_TRACKER.fires_from_dispatch(MODEL) is False
    H.configure(hedge_mode="adaptive")
    assert H.HEDGE_TRACKER.fires_from_dispatch(MODEL) is False


def test_faster_stream_triggers_follow_the_median():
    H.configure(hedge_mode="max")
    _learn(MODEL, 2.0)
    assert H.HEDGE_TRACKER.stream_triggers(MODEL, reasoning=False) == (3.0, 6.0)


def test_faster_cold_start_is_half_the_classes_number():
    H.configure(hedge_mode="max", hedge_cold_s=6.0)
    assert H.HEDGE_TRACKER.stream_triggers("p:never-seen", reasoning=False) == (3.0, 6.0)
    H.configure(hedge_cold_reasoning_s=30.0)
    assert H.HEDGE_TRACKER.stream_triggers("p:never-seen", reasoning=True) == (15.0, 30.0)


def test_balanced_triggers_are_unchanged():                                    # (guard)
    _learn(MODEL, 2.0)
    assert H.HEDGE_TRACKER.stream_triggers(MODEL, reasoning=False) == (4.0, 8.0)
    assert H.HEDGE_TRACKER.stream_triggers("p:never-seen", reasoning=False) == (6.0, 12.0)


def test_faster_hedges_a_non_streamed_call_at_three_quarters_of_the_threshold():
    base = H.HEDGE_TRACKER.threshold("p:cold", stream=False, reasoning=False)
    H.configure(hedge_mode="max")
    assert H.HEDGE_TRACKER.threshold("p:cold", stream=False, reasoning=False) == pytest.approx(base * 0.75)
    # a stream's threshold is its own triggers, not scaled
    assert H.HEDGE_TRACKER.threshold("p:cold", stream=True, reasoning=False) == base


def test_a_healthy_reply_sends_one_request_in_faster(monkeypatch):
    K.set_hedge_mode("max")
    log = []
    ctx, forwarded = make_ctx(streaming({k: 0.05 for k in KEYS}, log=log))
    result = run(ctx)
    assert result[0].startswith("answer from")
    assert [k for k in log if not k.startswith("cancelled:")] == [log[0]]       # one request, no backup at t=0
    assert K._KAME_SPEED_STATS["backups"] == 0


def _replies_with_backup(mode, replies=1000):
    """Consumed budget tokens over `replies` answered calls, one backup question each."""
    H.configure(hedge_mode=mode, hedge_budget_pct=10.0, hedge_burst=3.0)
    allowed = 0
    for _ in range(replies):
        H.HEDGE_TRACKER.record_latency(MODEL, 1.0, stream=True, reasoning=False)
        if H.HEDGE_TRACKER.may_start_backup(MODEL, healthy_count=3, under_pressure=False,
                                            stream=True, reasoning=False):
            allowed += 1
    return allowed


def test_faster_spends_three_times_balanceds_budget_and_no_more():
    balanced = _replies_with_backup("adaptive")
    H.HEDGE_TRACKER.clear()
    faster = _replies_with_backup("max")
    assert 0.09 <= balanced / 1000 <= 0.12, balanced      # 10% of replies, plus the opening burst
    assert 0.29 <= faster / 1000 <= 0.33, faster          # 30%: the budget is consulted, not skipped


def test_the_faster_budget_never_passes_a_hundred_percent():
    H.configure(hedge_mode="max", hedge_budget_pct=50.0, hedge_burst=3.0)
    allowed = 0
    for _ in range(400):
        H.HEDGE_TRACKER.record_latency(MODEL, 1.0, stream=True, reasoning=False)
        allowed += bool(H.HEDGE_TRACKER.may_start_backup(
            MODEL, healthy_count=3, under_pressure=False, stream=True, reasoning=False))
    assert allowed <= 400 + 3


def test_a_quota_refusal_on_any_leg_steps_faster_back_for_ten_minutes():
    H.configure(hedge_mode="max")
    _learn(MODEL, 2.0)
    assert H.HEDGE_TRACKER.stream_triggers(MODEL, reasoning=False) == (3.0, 6.0)
    H.HEDGE_TRACKER.note_quota_refusal(MODEL)
    assert H.HEDGE_TRACKER.stream_triggers(MODEL, reasoning=False) == (4.0, 8.0)     # Balanced
    assert 590 < H.HEDGE_TRACKER.faster_paused_for(MODEL) <= 600
    later = time.monotonic() + 601
    assert H.HEDGE_TRACKER.stream_triggers(MODEL, reasoning=False, now=later) == (3.0, 6.0)   # by itself


def test_a_daily_quota_refusal_on_the_main_leg_steps_faster_back(monkeypatch):
    K.set_hedge_mode("max")
    broke = lambda: Refusal("Error code: 402", 402, {"error": {"message": "insufficient balance"}})
    ctx, _ = make_ctx(streaming({}, fail={KEYS[0]: broke}))
    monkeypatch.setattr(K, "_get_best_key", _first_then_rest(KEYS[0]))
    result = run(ctx)
    assert result[0].startswith("answer from")
    assert H.HEDGE_TRACKER.faster_paused_for(ID) > 590


def test_a_429_on_a_backup_leg_steps_faster_back_too(monkeypatch):
    K.set_hedge_mode("max")
    monkeypatch.setattr(H.HEDGE_TRACKER, "stream_triggers", lambda *a, **k: (0.05, 0.5))
    first, second = KEYS[0], KEYS[1]
    fail = {second: lambda: Refusal("Error code: 429", 429, {"error": {"message": "slow down"}},
                                    {"retry-after": "30"})}
    ctx, _ = make_ctx(streaming({first: 0.3}, fail=fail))
    monkeypatch.setattr(K, "_get_best_key", _first_then_rest(first))
    run(ctx)
    assert H.HEDGE_TRACKER.faster_paused_for(ID) > 590


def test_a_busy_model_does_not_step_faster_back(monkeypatch):
    K.set_hedge_mode("max")
    busy = lambda: Refusal("Error code: 503", 503, {"error": {"message": "The model is overloaded.",
                                                              "status": "UNAVAILABLE"}})
    ctx, _ = make_ctx(streaming({}, fail={KEYS[0]: busy}))
    monkeypatch.setattr(K, "_get_best_key", _first_then_rest(KEYS[0]))
    run(ctx)
    assert H.HEDGE_TRACKER.faster_paused_for(ID) == 0.0     # racing is exactly what helps on a busy model


def test_speed_multipliers_follow_the_budget():
    assert H.speed_multipliers(10) == pytest.approx((1.1, 1.3))
    assert H.speed_multipliers(0) == pytest.approx((1.0, 1.0))
    assert H.speed_multipliers(50) == pytest.approx((1.5, 2.0))       # Faster never exceeds 2x
    assert H.speed_multipliers(25) == pytest.approx((1.25, 1.75))

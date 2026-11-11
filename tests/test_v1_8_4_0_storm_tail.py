"""1.8.4.0 (E8) - the tail of a storm is raced, not knocked.

Replies a minute apart land in the TAIL of the busy window: after ``busy_until``, within
``max(first window, window)`` more. Since 1.8.3.1 one sequential knock on the best model decided there
whether the storm was still on, and the person waited for the 503. Now, where the start probe is allowed,
the reply starts on the first free model of the list at once and the best model is asked BESIDE it,
exactly as inside the window. Where the probe is not allowed the tail is the sequential knock, as before.

A virtual clock (kame_vclock.py). No Agent Zero installation, no network.
"""
from __future__ import annotations

import asyncio
import time

import pytest

from test_v1_8_2_7_speed_fallback import (  # noqa: F401  (fixtures are used by name)
    FALLBACK_BEHAVIOUR, ID, KEYS, Refusal, clean, fake_models, make_ctx, streaming,
)
from kame_vclock import vt  # noqa: F401
import kame_engine as K

NEXT = "google/gemini-3.7-flash"


def overloaded():
    return Refusal("Error code: 503", 503,
                   {"error": {"code": 503, "message": "The model is overloaded. Please try again later.",
                              "status": "UNAVAILABLE"}})


@pytest.fixture(autouse=True)
def calm():
    K._KAME_BUSY_UNTIL.clear()
    K._KAME_BUSY_WINDOW.clear()
    K._KAME_PROBED_AT.clear()
    K._KAME_REPLIES.clear()
    K._KAME_TURNS.clear()
    yield
    K._KAME_BUSY_UNTIL.clear()
    K._KAME_BUSY_WINDOW.clear()
    K._KAME_PROBED_AT.clear()


def ended_ago(seconds=5.0, window=30.0):
    """The best model's window ended ``seconds`` ago; it was last asked when it was opened (long ago)."""
    K._KAME_BUSY_UNTIL[ID] = time.time() - seconds
    K._KAME_BUSY_WINDOW[ID] = window
    K._KAME_PROBED_AT[ID] = time.time() - seconds - window


def requests(log):
    return [k for k in log if not k.startswith("cancelled:")]


# --- the rule -----------------------------------------------------------------------------------------

def test_a_reply_in_the_tail_starts_on_the_next_model_and_asks_the_best_beside_it(fake_models, vt):
    K.set_fallback_models(NEXT)
    ended_ago()
    log = []
    ctx, _ = make_ctx(streaming({k: 2.0 for k in KEYS}, log=log))        # the best model is slow today
    result = vt.run(K._kame_carousel(None, ctx), limit=30)
    assert result[0] == f"fallback answer from {NEXT}"
    assert len(requests(log)) == 1 and len(fake_models) == 1             # both went out at t = 0 ...
    assert vt.elapsed() < 0.5                                            # ... and the list model answered
    assert ctx.get("probe_primary") is True


def test_the_best_model_answering_first_is_shown_and_its_window_closes(fake_models, vt):
    K.set_fallback_models(NEXT)
    FALLBACK_BEHAVIOUR[NEXT] = "slow"                                    # 3 s
    ended_ago()
    log = []
    ctx, _ = make_ctx(streaming({}, log=log))
    result = vt.run(K._kame_carousel(None, ctx), limit=30)
    assert result[0].startswith("answer from ") and not result[0].endswith("flash")
    assert len(requests(log)) == 1                                       # the best model WAS asked
    assert ID not in K._KAME_BUSY_UNTIL and ID not in K._KAME_PROBED_AT


def test_a_503_from_the_probe_in_the_tail_doubles_the_window_and_the_reply_is_unharmed(fake_models, vt):
    K.set_fallback_models(NEXT)
    FALLBACK_BEHAVIOUR[NEXT] = "slow"
    ended_ago(window=30.0)
    ctx, _ = make_ctx(streaming({}, fail={k: overloaded for k in KEYS}))
    result = vt.run(K._kame_carousel(None, ctx), limit=30)
    assert result[0] == f"fallback answer from {NEXT}"
    assert K._KAME_BUSY_WINDOW[ID] == 60.0                               # 30 -> 60, once
    assert K._KAME_BUSY_UNTIL[ID] > time.time()                          # and it is open again


def test_the_probe_is_stamped_so_the_tail_asks_at_most_once_per_twenty_seconds(fake_models, vt):
    K.set_fallback_models(NEXT)
    ended_ago()
    first_ctx, _ = make_ctx(streaming({k: 2.0 for k in KEYS}))
    assert K._kame_start_probe_allowed(first_ctx, ID) is True
    second_ctx, _ = make_ctx(streaming({}))
    assert K._kame_start_probe_allowed(second_ctx, ID) is False          # not due again: today's knock


# --- where nothing changes ------------------------------------------------------------------------------

def test_a_window_that_ended_longer_ago_than_the_tail_is_a_sequential_knock(fake_models, vt):
    K.set_fallback_models(NEXT)
    ended_ago(seconds=40.0, window=30.0)                                 # the tail is 30 s: over
    log = []
    ctx, _ = make_ctx(streaming({}, log=log))
    result = vt.run(K._kame_carousel(None, ctx), limit=30)
    assert result[0].startswith("answer from ") and ctx.get("probe_primary") is not True
    assert len(requests(log)) == 1 and not fake_models


def test_a_doubled_window_has_a_longer_tail(fake_models, vt):
    K.set_fallback_models(NEXT)
    ended_ago(seconds=40.0, window=120.0)                                # tail is 120 s: still inside
    ctx, _ = make_ctx(streaming({k: 2.0 for k in KEYS}))
    assert vt.run(K._kame_carousel(None, ctx), limit=30)[0] == f"fallback answer from {NEXT}"
    assert ctx.get("probe_primary") is True


def test_a_model_that_never_had_a_window_has_no_tail():
    assert K._kame_in_tail(ID, time.time()) is False
    K._KAME_BUSY_WINDOW[ID] = 30.0
    assert K._kame_in_tail(ID, time.time()) is False                     # busy_until is 0


def test_the_busy_state_other_callers_read_is_unchanged_by_the_tail():
    ended_ago()
    assert K._kame_busy_state(ID, time.time())[0] is False               # the window alone
    assert K._kame_busy_window_or_tail(ID, time.time())[0] is True


def test_saver_keeps_the_sequential_knock_in_the_tail(fake_models, vt):
    K.set_fallback_models(NEXT)
    K.set_hedge_mode("off")
    ended_ago()
    log = []
    ctx, _ = make_ctx(streaming({}, log=log))
    assert vt.run(K._kame_carousel(None, ctx), limit=30)[0].startswith("answer from ")
    assert len(requests(log)) == 1 and not fake_models


def test_a_background_call_keeps_the_sequential_knock_in_the_tail(fake_models, vt):
    K.set_fallback_models(NEXT)
    ended_ago()
    log = []
    ctx, _ = make_ctx(streaming({}, log=log), call_type="Util")
    assert vt.run(K._kame_carousel(None, ctx), limit=30)[0].startswith("answer from ")
    assert len(requests(log)) == 1 and not fake_models
    assert ctx.get("probe_primary") is not True


def test_a_non_stream_call_keeps_the_sequential_knock_in_the_tail(fake_models, vt):
    K.set_fallback_models(NEXT)
    ended_ago()
    log = []
    ctx, _ = make_ctx(streaming({}, log=log))
    ctx["kwargs"] = {"a0_api_mode": "responses", "previous_response_id": "resp_1"}
    assert vt.run(K._kame_carousel(None, ctx), limit=30)[0].startswith("answer from ")
    assert len(requests(log)) == 1 and not fake_models and ctx.get("probe_primary") is not True


def test_never_keeps_the_sequential_knock_in_the_tail(fake_models, vt):
    K.set_fallback_models(NEXT)
    K.set_model_switch("never")
    ended_ago()
    log = []
    ctx, _ = make_ctx(streaming({}, log=log))
    assert vt.run(K._kame_carousel(None, ctx), limit=30)[0].startswith("answer from ")
    assert len(requests(log)) == 1 and not fake_models


def test_a_probe_that_is_not_allowed_in_the_tail_does_not_hand_the_reply_over(fake_models, vt):
    # Saver in the tail: the best model is knocked, and the list model is not called (no hand-over without a request).
    K.set_fallback_models(NEXT)
    K.set_hedge_mode("off")
    ended_ago()
    ctx, _ = make_ctx(streaming({}))
    vt.run(K._kame_carousel(None, ctx), limit=30)
    assert fake_models == []


# --- a five-minute storm, a reply a minute -------------------------------------------------------------

def test_replies_a_minute_apart_through_a_five_minute_storm_start_at_once_from_the_second_on(fake_models, vt):
    K.set_fallback_models(NEXT)
    FALLBACK_BEHAVIOUR[NEXT] = "late"          # 0.35 s: the best model's 503 (10 ms) is seen before the list model answers
    seen = []

    async def conversation():
        for n in range(6):
            started = vt.elapsed()
            ctx, _ = make_ctx(streaming({}, fail={k: overloaded for k in KEYS}))
            result = await K._kame_carousel(None, ctx)
            assert result[0] == f"fallback answer from {NEXT}"
            seen.append((n, vt.elapsed() - started, bool(ctx.get("probe_primary"))))
            await asyncio.sleep(max(0.0, 60.0 - (vt.elapsed() - started)))   # a reply a minute

    vt.run(conversation(), limit=600)
    # from the second reply on, the list model starts at t = 0 beside the probe: no 503 round trip first
    for n, took, probed in seen[1:]:
        assert probed is True, seen
        assert took < 0.5, seen

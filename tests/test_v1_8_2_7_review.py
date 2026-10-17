"""1.8.2.7 - every finding of the adversarial review (the first review pass),
pinned. Each test failed on the build the review read."""
from __future__ import annotations

import asyncio
import sys
import time
import types

import pytest

if not any(name in sys.modules for name in ("test_v1_8_1_1_reliability", "tests.test_v1_8_1_1_reliability")):
    import test_v1_8_1_1_reliability  # noqa: F401
try:
    import test_v1_8_2_7_speed_fallback as T
except ImportError:  # collected as a package
    from tests import test_v1_8_2_7_speed_fallback as T
import kame_engine as K
import kame_hedging as H

clean = T.clean            # the same fixture: fresh pools, no disk, defaults
fake_models = T.fake_models
KEYS, ID, Refusal = T.KEYS, T.ID, T.Refusal


def billing():
    return Refusal("Error code: 402", 402, {"error": {"message": "insufficient balance"}})


def per_minute():
    return Refusal("Error code: 429", 429, {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED",
                   "message": "Quota exceeded for metric: generate_content requests per minute",
                   "details": [{"@type": "type.googleapis.com/google.rpc.QuotaFailure",
                                "violations": [{"quotaId": "GenerateRequestsPerMinutePerProjectPerModel"}]}]}},
                   {"retry-after": "65"})


def test_h1_every_leg_refused_in_one_tick_is_judged(monkeypatch):
    # A burst of eight refused at once: every key rests, not only the last one.
    monkeypatch.setattr(H.HEDGE_TRACKER, "stream_triggers", lambda *a, **k: (30.0, 60.0))
    ctx, _ = T.make_ctx(T.streaming({}, fail={k: billing for k in KEYS}))
    race_keys = list(KEYS)

    async def go():
        with pytest.raises(Refusal) as info:
            await K._kame_raced_attempt(None, ctx, race_keys[0], burst_keys=race_keys[1:])
        return info.value

    exc = asyncio.run(go())
    loser_key = exc._kame_leg_key
    now = time.time()
    rested = [k for k in KEYS if float(K._KAME_KEY_HEALTH[ID]["keys"][k].get("sick_until") or 0) > now]
    # the race judged seven; the carousel judges the one it is handed
    assert len(rested) == 7 and loser_key not in rested


def test_h1_an_answer_beside_a_failure_in_one_tick_wins(monkeypatch):
    monkeypatch.setattr(H.HEDGE_TRACKER, "threshold", lambda *a, **k: 30.0)
    ctx, _ = T.make_ctx(T.streaming({k: 0.01 for k in KEYS[1:]}, fail={KEYS[0]: billing}), call_type="Util")

    async def go():
        return await K._kame_raced_attempt(None, ctx, KEYS[0], burst_keys=[KEYS[1]])

    result, key = asyncio.run(go())
    assert key == KEYS[1] and result[0].startswith("answer from")
    assert float(K._KAME_KEY_HEALTH[ID]["keys"][KEYS[0]]["sick_until"]) > time.time() + 60


def test_h2_per_minute_rests_never_start_a_burst(monkeypatch):
    monkeypatch.setattr(H.HEDGE_TRACKER, "stream_triggers", lambda *a, **k: (30.0, 60.0))
    active = {"now": 0, "max": 0}
    ctx, _ = T.make_ctx(T.streaming({}, fail={k: per_minute for k in KEYS[:3]}, active=active))
    monkeypatch.setattr(K, "_get_best_key", T._in_order())
    T.run(ctx)
    assert active["max"] == 1 and K._KAME_SPEED_STATS["probe_bursts"] == 0


def test_h2_a_large_prompt_is_never_burst(monkeypatch):
    monkeypatch.setattr(H.HEDGE_TRACKER, "stream_triggers", lambda *a, **k: (30.0, 60.0))
    active = {"now": 0, "max": 0}
    ctx, _ = T.make_ctx(T.streaming({}, fail={k: billing for k in KEYS[:3]}, active=active))
    ctx["messages"] = ["x" * 400000]          # ~100k tokens
    monkeypatch.setattr(K, "_get_best_key", T._in_order())
    T.run(ctx)
    assert active["max"] == 1


def test_h3_no_switch_after_an_earlier_attempt_showed_text(fake_models, monkeypatch):
    K.set_fallback_models("groq/b")
    K.set_hedge_mode("off")
    monkeypatch.setattr(K, "_KAME_REAL_TIMEOUT_MIN_S", 0.0)
    calls = {"n": 0}

    async def attempt(owner, key, ctx):
        calls["n"] += 1
        if calls["n"] == 1:
            await ctx["response_callback"]("Hel", "Hel")       # shown, then the stream dies
            raise Refusal("Error code: 504", 504, {"error": "gateway timeout"})
        if calls["n"] == 2:
            raise Refusal("Error code: 504", 504, {"error": "gateway timeout"})
        return ("answer from the configured model", "")

    ctx, _ = T.make_ctx(attempt)
    assert T.run(ctx)[0] == "answer from the configured model" and not fake_models


def test_m1_a_fallback_call_is_bounded(monkeypatch):
    monkeypatch.setattr(K, "_KAME_HOP_BOUND_MIN_S", 0.0)
    K.set_failover_after(0.001)
    busy = lambda: Refusal("Error code: 503", 503, {"error": {"message": "Service temporarily unavailable"}})
    ctx, _ = T.make_ctx(T.streaming({}, fail={k: busy for k in KEYS}))
    token = K._KAME_FALLBACK_HOP.set({"origin": "x"})
    try:
        with pytest.raises(K._KameSwitchModel):
            T.run(ctx)
    finally:
        K._KAME_FALLBACK_HOP.reset(token)


def test_m2_a_backup_is_never_a_retired_key():
    K._get_identity_state(ID, KEYS)
    for k in KEYS[:6]:
        K._KAME_KEY_HEALTH[ID]["keys"][k]["retired_at"] = time.time()
    picked = K._kame_pick_more(ID, KEYS, {KEYS[6]}, 3)
    assert picked == [KEYS[7]]


def test_m3_a_closed_door_touches_no_ladder():
    K._get_identity_state(ID, KEYS)
    kd = K._KAME_KEY_HEALTH[ID]["keys"][KEYS[0]]
    kd["consecutive_refusals"], kd["last_hold_kind"] = 2, "per_minute"
    K._kame_rest_pool_for_door(ID, KEYS, 30)
    assert kd["consecutive_refusals"] == 2 and kd["last_hold_kind"] == "per_minute"
    assert all(float(K._KAME_KEY_HEALTH[ID]["keys"][k]["sick_until"]) >= time.time() + 25 for k in KEYS)


def test_m4_only_the_conversation_is_the_chat():
    def call_chat_model():
        return K._kame_is_main_chat(True)

    def call_utility_model():
        return K._kame_is_main_chat(True)

    def _compact_single_pass():
        return K._kame_is_main_chat(True)

    assert call_chat_model() is True
    assert call_utility_model() is False
    assert _compact_single_pass() is False
    assert K._kame_is_main_chat(False) is False


def test_m4_a_background_stream_never_switches(fake_models, monkeypatch):
    K.set_fallback_models("groq/b")

    async def sleep(*a, **k):
        raise T.StopTest()

    monkeypatch.setattr(K, "_kame_sleep_on_exhaustion", sleep)
    ctx, _ = T.make_ctx(T.streaming({}))
    ctx["main_chat"] = False                    # a streaming utility call
    T._rest_every_key()
    with pytest.raises(T.StopTest):
        T.run(ctx)
    assert not fake_models


def test_m5_a_cut_leg_is_learned_at_the_cut(monkeypatch):
    recorded = []
    monkeypatch.setattr(H.HEDGE_TRACKER, "record_latency",
                        lambda identity, s, **k: recorded.append((round(s, 2), k.get("censored", False))))
    race = K._KameRace({"identity": ID})
    now = time.monotonic()
    # the loser started with the winner, the winner spoke after 0.1 s and then
    # answered for 4.9 s more: the loser is known slower than 0.1 s, not 5 s
    race.started = {0: now - 5.0, 1: now - 5.0}
    race.progress = {0: {"first_sign_at": time.time() - 4.9}, 1: {}}
    race.claim_at = now - 4.9
    K._kame_race_learn({"identity": ID}, race, 0, True, False)
    cut = [s for s, c in recorded if c]
    assert cut and cut[0] <= 0.1


def test_low_a_streamed_backup_win_is_counted(monkeypatch):
    monkeypatch.setattr(H.HEDGE_TRACKER, "stream_triggers", lambda *a, **k: (0.05, 0.10))
    first = KEYS[0]
    ctx, _ = T.make_ctx(T.streaming({k: (5.0 if k == first else 0.0) for k in KEYS}))
    monkeypatch.setattr(K, "_get_best_key", T._first_then_rest(first))
    T.run(ctx)
    assert K._KAME_SPEED_STATS["backup_wins"] == 1


def test_low_an_at_sign_inside_a_model_name_is_kept():
    rows = K.parse_fallback_models("vertex_ai/claude-sonnet-4@20250514\nollama/qwen3@http://h:11434")
    assert rows[0]["name"] == "claude-sonnet-4@20250514" and rows[0]["api_base"] == ""
    assert rows[1]["name"] == "qwen3" and rows[1]["api_base"] == "http://h:11434"


# --- second pass (the second review pass) -------------------

def no_plan():
    return Refusal("Error code: 429", 429, {"error": {"type": "usage_limit_reached", "code": "usage_not_included",
                   "message": "The usage_not_included plan does not include this service"}})


def test_n1_keys_refused_inside_a_burst_still_hand_back_a_plan_refusal(monkeypatch):
    # Every key: "your plan does not include this". With the speed boost on, two
    # bursts used to judge the keys inside the race, the carousel never saw them,
    # and the call waited an hour instead of handing the error back (1.8.1.2 rule).
    monkeypatch.setattr(H.HEDGE_TRACKER, "stream_triggers", lambda *a, **k: (30.0, 60.0))
    assert K._no_clock_fixes(no_plan())

    async def never_sleep(*a, **k):
        raise AssertionError("must hand back, not wait")

    monkeypatch.setattr(K, "_kame_sleep_on_exhaustion", never_sleep)
    ctx, _ = T.make_ctx(T.streaming({}, fail={k: no_plan for k in KEYS}))
    monkeypatch.setattr(K, "_get_best_key", T._in_order())
    with pytest.raises(Refusal):
        T.run(ctx)


def test_n2_a_leg_failing_beside_the_winners_first_word_is_judged(monkeypatch):
    # Leg 0 fails and leg 1 says its first word in the same loop tick: the race
    # sees the claim first, and the failed leg must still be judged.
    monkeypatch.setattr(H.HEDGE_TRACKER, "stream_triggers", lambda *a, **k: (30.0, 60.0))
    ctx, _ = T.make_ctx(T.streaming({}))

    async def attempt(owner, key, c):
        await asyncio.sleep(0.02)
        if key == KEYS[1]:
            raise billing()
        await c["response_callback"]("hi", "hi")
        await asyncio.sleep(0.02)
        return ("answer", "")

    ctx["attempt"] = attempt
    result, key = asyncio.run(K._kame_raced_attempt(None, ctx, KEYS[1], burst_keys=[KEYS[0]]))
    assert key == KEYS[0]
    assert float(K._KAME_KEY_HEALTH[ID]["keys"][KEYS[1]].get("sick_until") or 0) > time.time() + 60


def test_n3_a_short_cut_is_not_learned_as_a_sample(monkeypatch):
    recorded = []
    monkeypatch.setattr(H.HEDGE_TRACKER, "record_latency",
                        lambda identity, s, **k: recorded.append((round(s, 2), k.get("censored", False))))
    race = K._KameRace({"identity": ID})
    now = time.monotonic()
    race.started = {0: now - 0.4, 1: now - 0.1}          # the backup started late
    race.progress = {0: {"first_sign_at": time.time() - 0.1}, 1: {}}
    race.claim_at = now - 0.1
    K._kame_race_learn({"identity": ID}, race, 0, True, False)
    assert [c for _s, c in recorded].count(True) == 0     # 0.0 s is no lower bound for a 0.3 s model


def test_n3_burst_losers_teach_nothing(monkeypatch):
    recorded = []
    monkeypatch.setattr(H.HEDGE_TRACKER, "record_latency",
                        lambda identity, s, **k: recorded.append(k.get("censored", False)))
    race = K._KameRace({"identity": ID})
    race.burst = True
    now = time.monotonic()
    race.started = {i: now - 1.0 for i in range(8)}
    race.progress = {i: {} for i in range(8)}
    race.progress[3] = {"first_sign_at": time.time() - 0.5}
    race.claim_at = now - 0.5
    K._kame_race_learn({"identity": ID}, race, 3, True, False)
    assert recorded == [False]


def test_n4_a_real_failure_is_not_lost_to_a_cancelled_leg():
    ctx, _ = T.make_ctx(T.streaming({}))

    async def attempt(owner, key, c):
        if key == KEYS[0]:
            raise billing()
        await asyncio.sleep(0.01)
        raise asyncio.CancelledError()

    ctx["attempt"] = attempt
    with pytest.raises(Refusal) as info:
        asyncio.run(K._kame_raced_attempt(None, ctx, KEYS[0], burst_keys=[KEYS[1]]))
    assert getattr(info.value, "_kame_judged", None)       # judged once, inside the race


def test_n6_a_burst_win_is_not_a_backup_win(monkeypatch):
    monkeypatch.setattr(H.HEDGE_TRACKER, "stream_triggers", lambda *a, **k: (30.0, 60.0))
    ctx, _ = T.make_ctx(T.streaming({KEYS[0]: 0.2}))
    asyncio.run(K._kame_raced_attempt(None, ctx, KEYS[0], burst_keys=KEYS[1:4]))
    assert K._KAME_SPEED_STATS["backup_wins"] == 0


def test_n8_a_closed_door_is_kept_on_disk(monkeypatch):
    noted = []
    monkeypatch.setattr(K._KJ, "note_hold", lambda *a, **k: noted.append(a[1]))
    K._get_identity_state(ID, KEYS)
    K._kame_rest_pool_for_door(ID, KEYS, 30)
    assert sorted(noted) == sorted(KEYS)


def test_n10_a_stopped_call_closes_the_waiting_notice(monkeypatch):
    closed = []
    monkeypatch.setattr(K, "_kame_wait_notice_finish", lambda st, how: closed.append(how))

    async def attempt(owner, key, c):
        raise asyncio.CancelledError()

    K.set_hedge_mode("off")
    ctx, _ = T.make_ctx(attempt)
    with pytest.raises(asyncio.CancelledError):
        T.run(ctx)
    assert "stopped" in closed


# --- third pass (the third review pass) ----------------------

def test_p1_refusals_in_a_race_that_ended_empty_still_count(monkeypatch):
    monkeypatch.setattr(H.HEDGE_TRACKER, "stream_triggers", lambda *a, **k: (30.0, 60.0))
    seen = {}

    async def attempt(owner, key, c):
        seen[key] = seen.get(key, 0) + 1
        await asyncio.sleep(0.005)
        if key == KEYS[2] and seen[key] == 1:
            return ("", "")                 # one empty answer inside the burst
        raise no_plan()

    async def never_sleep(*a, **k):
        raise AssertionError("must hand back, not wait")

    monkeypatch.setattr(K, "_kame_sleep_on_exhaustion", never_sleep)
    monkeypatch.setattr(K, "_get_best_key", T._in_order())
    ctx, _ = T.make_ctx(attempt)
    with pytest.raises(Refusal):
        T.run(ctx)


def test_p2_a_shared_exception_object_is_judged_for_each_key():
    shared = billing()
    shared._kame_judged = (KEYS[0], 3600.0, "insufficient_quota", 402, "", "kame")
    ctx, _ = T.make_ctx(T.streaming({}))
    K.set_hedge_mode("off")
    K.set_carousel_disabled(True)
    try:
        async def attempt(owner, key, c):
            raise shared
        ctx["attempt"] = attempt
        monkey_pick = lambda identity, keys: (KEYS[1], "SUCCESS")
        original = K._get_best_key
        K._get_best_key = monkey_pick
        try:
            with pytest.raises(Refusal):
                T.run(ctx)
        finally:
            K._get_best_key = original
    finally:
        K.set_carousel_disabled(False)
    assert float(K._KAME_KEY_HEALTH[ID]["keys"][KEYS[1]].get("sick_until") or 0) > time.time() + 60


def test_p3_a_request_fault_on_a_losing_leg_rests_no_key(monkeypatch):
    # Leg 0 fails with a request fault in the same tick leg 1 says its first
    # word: the winner stands, and no key is rested for the request's fault.
    monkeypatch.setattr(H.HEDGE_TRACKER, "stream_triggers", lambda *a, **k: (30.0, 60.0))
    ctx, _ = T.make_ctx(T.streaming({}))

    async def attempt(owner, key, c):
        await asyncio.sleep(0.02)
        if key == KEYS[1]:
            raise Refusal("Error code: 400", 400, {"error": {"code": "context_length_exceeded",
                                                              "message": "prompt is too long"}})
        await c["response_callback"]("hi", "hi")
        await asyncio.sleep(0.02)
        return ("answer", "")

    ctx["attempt"] = attempt
    result, key = asyncio.run(K._kame_raced_attempt(None, ctx, KEYS[1], burst_keys=[KEYS[0]]))
    assert key == KEYS[0]
    assert float(K._KAME_KEY_HEALTH[ID]["keys"][KEYS[1]].get("sick_until") or 0) < time.time()

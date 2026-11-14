"""1.9.0.1 (A11-A14) - the audit matrix, Agent Zero side: what a reply does for every kind of user.

Ported from the Hermes ``tests/test_1_9_0_1_matrix.py`` into this port's harness (a virtual clock, scripted
legs). The users differ in the shape of the owner's list (a later model, one model, no list), the number of keys
(one, four), "Never switch", and the way the provider fails (thinks then stalls, 503, text then silence).
A rule that serves a 14-key pool and leaves a one-key user waiting fails here.

Quiet limit 2 s (the ``scene`` fixture): rescue 1 opens at about 2 s, rescue 2 about 4 s after it, and both have
had their turn about 4 s after that. Outcomes: the reply ends with a result, or it is still waiting at the cap
(``TimeoutError`` of the virtual clock), which is what "nothing else exists to ask" looks like.
"""
from __future__ import annotations

import asyncio

import pytest

from test_v1_8_2_7_speed_fallback import ID, KEYS, Refusal, clean, make_ctx  # noqa: F401
from test_v1_8_4_3_quiet_guard import (  # noqa: F401  (fixtures are used by name)
    NEXT, NEXT_ID, THIRD, THIRD_ID, Scene, ledger, events, overloaded, scene,
)
from kame_vclock import vt  # noqa: F401
import kame_engine as K
import kame_settings as S

ONE = KEYS[:1]
FOUR = KEYS[:4]
STALL = [("think", "a thought"), ("sleep", 500)]


def per_minute():
    return Refusal("Error code: 429", 429, {"error": {"code": 429, "message": "Quota exceeded for metric",
                                                         "status": "RESOURCE_EXHAUSTED"}})


def go(scene, scripts, keys=FOUR, limit=60.0, **extra):
    """One reply. Returns (``"ok"`` | ``"waits"``, result, virtual seconds, ctx, the keys the home model was asked)."""
    scene.home_scripts = scripts
    asked = []
    ctx, _ = make_ctx(scene.home, keys=keys)

    real = ctx["attempt"]

    async def attempt(owner, key, leg_ctx):
        asked.append(key)
        return await real(owner, key, leg_ctx)

    ctx["attempt"] = attempt

    async def on_text(delta, full):
        scene.seen.append((scene.vt.elapsed(), "text", delta))

    async def on_think(delta, full):
        scene.seen.append((scene.vt.elapsed(), "think", delta))

    ctx["response_callback"], ctx["reasoning_callback"], ctx["tokens_callback"] = \
        K._kame_wrap_callbacks(ctx, on_text, on_think, None)
    ctx.update(extra)
    try:
        result = scene.vt.run(K._kame_carousel(None, ctx), limit=limit)
    except (TimeoutError, asyncio.TimeoutError):
        return "waits", None, scene.vt.elapsed(), ctx, asked
    return "ok", result, scene.vt.elapsed(), ctx, asked


def texts(scene):
    return "".join(d for _t, k, d in scene.seen if k == "text")


# --- A11 (R1) - both rescues exhausted: the reply rotates to the next free model -------------------------------

def test_a11_1_a_stall_that_nothing_beside_it_answers_is_handed_to_the_next_free_model(scene):
    """The list is [NEXT, THIRD]: rescue 1 (another key) stalls, rescue 2 (NEXT) stalls, THIRD answers."""
    scene.scripts[NEXT] = list(STALL)
    scene.scripts[THIRD] = [("say", "from the third")]
    out, result, took, ctx, asked = go(scene, [STALL, STALL])
    assert out == "ok" and result is scene.objs[THIRD], (out, took, scene.starts)
    assert took < 14.0, took                                   # at the second rescue's late point, not the timeout
    assert len(scene.started(NEXT)) == 1 and len(scene.started(THIRD)) == 1
    assert len(asked) == 2                                     # the home model: the reply's own key and rescue 1
    assert NEXT in scene.cancelled and "home0" in scene.cancelled and "home1" in scene.cancelled
    assert ctx["rescues_asked"] == 2


def test_a11_2_the_home_model_and_the_rescue_that_never_worked_are_left_alone_for_a_while(scene):
    scene.scripts[NEXT] = [("sleep", 500)]                     # never shows a sign of work
    scene.scripts[THIRD] = [("say", "from the third")]
    out, result, took, ctx, asked = go(scene, [STALL, STALL])
    assert out == "ok" and result is scene.objs[THIRD]
    assert ID in K._KAME_BUSY_UNTIL                            # the home model stopped mid-thought: slow, not asked first
    assert NEXT_ID in K._KAME_BUSY_UNTIL                       # a rescue that never worked is left alone too


def test_a11_3_the_event_says_what_happened(scene):
    scene.scripts[NEXT] = list(STALL)
    scene.scripts[THIRD] = [("say", "from the third")]
    go(scene, [STALL, STALL])
    assert any("stopped mid-thought" in e and "nothing beside it answered" in e for e in events()), events()


def test_a11_4_with_no_later_model_the_reply_keeps_waiting(scene):
    K.set_fallback_models(NEXT)                                # the list is this one model: it is rescue 2
    scene.scripts[NEXT] = list(STALL)
    out, result, took, ctx, asked = go(scene, [STALL, STALL], limit=40.0)
    assert out == "waits"
    assert len(scene.started(THIRD)) == 0 and len(scene.started(NEXT)) == 1


def test_a11_5_a_rescue_that_is_still_working_is_never_cut(scene):
    thinking = [x for _ in range(14) for x in (("think", "t"), ("sleep", 1.0))] + [("say", "late but alive")]
    scene.scripts[NEXT] = list(thinking)
    scene.scripts[THIRD] = [("say", "from the third")]
    out, result, took, ctx, asked = go(scene, [STALL, STALL])
    assert out == "ok" and result is scene.objs[NEXT], (result, scene.starts)
    assert len(scene.started(THIRD)) == 0


def test_a11_6_a_rescue_that_answers_just_before_the_point_wins(scene):
    scene.scripts[NEXT] = [("think", "x"), ("sleep", 3.5), ("say", "next answers")]
    scene.scripts[THIRD] = [("say", "from the third")]
    out, result, took, ctx, asked = go(scene, [STALL, STALL])
    assert out == "ok" and result is scene.objs[NEXT]


def test_a11_7_never_switch_hands_nothing_over(scene):
    K.set_model_switch("never")
    scene.scripts[NEXT] = list(STALL)
    scene.scripts[THIRD] = [("say", "from the third")]
    out, *_rest = go(scene, [STALL, STALL, STALL, STALL], limit=40.0)
    assert out == "waits"
    assert len(scene.started(NEXT)) == 0 and len(scene.started(THIRD)) == 0


def test_a11_8_a_burst_or_a_background_job_is_not_handed_over(scene):
    scene.scripts[THIRD] = [("say", "from the third")]
    K.set_hedge_mode("off")
    try:
        out, *_rest = go(scene, [STALL], limit=40.0)
    finally:
        K.set_hedge_mode("adaptive")
    assert out == "waits"


# --- A12 (R2) - a rescue is never impossible for lack of a second key or model ------------------------------------

def test_a12_1_one_key_no_list_the_same_key_is_asked_again(scene):
    K.set_fallback_models("")
    out, result, took, ctx, asked = go(scene, [STALL, [("say", "second ask")]], keys=ONE)
    assert out == "ok" and result is scene.objs["home1"], (out, took)
    assert took < 6.0 and asked == [ONE[0], ONE[0]]
    assert ctx["rescued"] is True and ledger()["rescue"] == 1


def test_a12_2_four_keys_no_list_the_second_rescue_is_another_key_of_the_same_model(scene):
    K.set_fallback_models("")
    out, result, took, ctx, asked = go(scene, [STALL, STALL, [("say", "third key")]])
    assert out == "ok" and result is scene.objs["home2"], (out, took, scene.starts)
    assert len(asked) == 3 and len(set(asked)) == 3             # three different keys of the one model
    assert ledger()["rescue"] == 2 and ctx["rescues_asked"] == 2


def test_a12_3_never_switch_the_second_rescue_is_another_key_never_the_sibling(scene):
    K.set_model_switch("never")
    scene.scripts[NEXT] = [("say", "sibling")]
    out, result, took, ctx, asked = go(scene, [STALL, STALL, [("say", "third key")]])
    assert out == "ok" and result is scene.objs["home2"], (out, took)
    assert len(scene.started(NEXT)) == 0 and len(set(asked)) == 3


def test_a12_4_one_key_and_a_list_the_same_key_then_the_next_model(scene):
    scene.scripts[NEXT] = [("say", "next answers")]
    out, result, took, ctx, asked = go(scene, [STALL, STALL], keys=ONE)
    assert out == "ok" and result is scene.objs[NEXT], (out, took, scene.starts)
    assert asked == [ONE[0], ONE[0]]                            # rescue 1 on the same key, rescue 2 on the list


def test_a12_5_at_most_two_extra_requests_and_they_are_counted(scene):
    K.set_fallback_models("")
    out, *_rest = go(scene, [STALL] * 8, limit=40.0)
    assert out == "waits"
    assert scene.homes == 3                                      # the reply's own request and two rescues
    assert ledger()["rescue"] == 2


def test_a12_6_a_healthy_thinking_one_key_reply_asks_nothing_more(scene):
    K.set_fallback_models("")
    thinking = [x for _ in range(6) for x in (("think", "t"), ("sleep", 0.5))] + [("say", "answer")]
    out, result, took, ctx, asked = go(scene, [thinking], keys=ONE)
    assert out == "ok" and result is scene.objs["home0"] and scene.homes == 1
    assert ledger()["rescue"] == 0


def test_a12_7_a_reply_with_no_thought_and_no_list_is_left_alone(scene):
    """Silent from the start with one key and no list: nothing else exists, the wait is the provider's (kept)."""
    K.set_fallback_models("")
    out, *_rest = go(scene, [[("sleep", 500)]], keys=ONE, limit=60.0)
    assert out == "waits" and scene.homes == 1


# --- A13 (R3) - the only key busy is the pool busy ---------------------------------------------------------------

def test_a13_1_the_rule_counts_every_key_the_pool_has_at_most_two():
    k1, k2, k3 = KEYS[:3]
    assert K._kame_is_busy("x:one", {k1}, [k1]) is True
    assert K._kame_is_busy("x:one", set(), [k1]) is False
    assert K._kame_is_busy("x:two", {k1}, [k1, k2]) is False
    assert K._kame_is_busy("x:two", {k1, k2}, [k1, k2]) is True
    assert K._kame_is_busy("x:three", {k1}, [k1, k2, k3]) is False
    assert K._kame_is_busy("x:three", {k1, k2}, [k1, k2, k3]) is True
    assert K._kame_is_busy("x:old", {k1}) is False                       # no keys given: today's "two"
    assert K._kame_is_busy("x:old", {k1, k2}) is True


def test_a13_2_a_one_key_user_whose_model_says_503_is_handed_over_at_once(scene):
    scene.scripts[NEXT] = [("say", "next answers")]
    out, result, took, ctx, asked = go(scene, [[("fail", overloaded)], [("say", "late")]], keys=ONE)
    assert out == "ok" and result is scene.objs[NEXT], (out, took, scene.starts)
    assert took < 3.0
    assert asked == [ONE[0]]                                              # not asked again beside the hand-over
    assert ID in K._KAME_BUSY_UNTIL                                       # the model is busy: the window opened


def test_a13_3_a_429_on_the_only_key_is_not_busy(scene):
    K.set_fallback_models("")
    go(scene, [[("fail", per_minute)], [("say", "later")]], keys=ONE, limit=200.0)
    assert ID not in K._KAME_BUSY_UNTIL


def test_a13_4_two_keys_still_need_two_busy_keys(scene):
    scene.scripts[NEXT] = [("sleep", 5.0), ("say", "next answers")]
    out, result, took, ctx, asked = go(scene, [[("fail", overloaded)], [("say", "second key")]], keys=KEYS[:2])
    assert out == "ok" and result is scene.objs["home1"], (out, scene.starts)
    assert ID not in K._KAME_BUSY_UNTIL                                   # one busy key of two: not yet the model


# --- A14 (R4) - a visible answer that goes silent is cut and continued ---------------------------------------------

def quiet(seconds):
    K.set_answer_quiet(seconds)


@pytest.fixture(autouse=True)
def an_assistant_message_type(monkeypatch):
    """A real Agent Zero has one, so a cut answer is continued; the stand-in modules do not."""
    if K.AIMessage is None:
        class _AIMessage:
            def __init__(self, content=""):
                self.content = content
        monkeypatch.setattr(K, "AIMessage", _AIMessage)


@pytest.fixture(autouse=True)
def _reset_answer_quiet():
    yield
    K.set_answer_quiet(None)


VISIBLE_THEN_SILENT = [("say", "visible "), ("sleep", 500)]


def test_a14_0_the_setting_is_in_the_table():
    kind, default, low, high, _choices, env, title, sentence = S.SETTINGS["answer_quiet_s"]
    assert (kind, default, low, high, env) == ("number", 45, 0, 600, "KAME_ANSWER_QUIET_S")
    assert S.parse("answer_quiet_s", "0")[:2] == (True, 0)
    assert S.parse("answer_quiet_s", "601")[0] is False
    assert "answer_quiet_s" in S.GROUPS[1][3]
    assert K.current_settings()["answer_quiet_s"] == 45.0


def test_a14_0b_unset_is_45_zero_is_off_a_number_is_that_number_and_the_environment_reaches_it(monkeypatch):
    K.set_answer_quiet(None)
    assert K._kame_answer_quiet_s() == 45.0
    K.set_answer_quiet(7)
    assert K._kame_answer_quiet_s() == 7.0
    K.set_answer_quiet("0")
    assert K._kame_answer_quiet_s() == 0.0
    K.set_answer_quiet("garbage")
    assert K._kame_answer_quiet_s() == 0.0
    monkeypatch.setenv("KAME_ANSWER_QUIET_S", "9")
    K._kame_apply_env_overrides()
    assert K._kame_answer_quiet_s() == 9.0


@pytest.mark.parametrize("keys", [FOUR, ONE], ids=["four keys", "one key"])
def test_a14_1_text_then_silence_is_cut_and_continued(scene, keys):
    K.set_fallback_models("")
    quiet(3.0)
    out, result, took, ctx, asked = go(scene, [VISIBLE_THEN_SILENT, [("say", "visible resumed")]], keys=keys)
    assert out == "ok", (out, took)
    assert 3.0 <= took < 12.0, took
    assert texts(scene).count("visible") == 1 and "resumed" in texts(scene)      # shown once, continued
    assert scene.homes == 2
    if len(keys) == 1:
        assert asked == [keys[0], keys[0]]                                       # the same key, when it is the only one


def test_a14_2_a_stream_that_keeps_sending_is_never_cut(scene):
    K.set_fallback_models("")
    quiet(3.0)
    steady = [("say", "visible ")] + [x for _ in range(8) for x in (("sleep", 1.0), ("say", "w "))]
    out, result, took, ctx, asked = go(scene, [steady])
    assert out == "ok" and scene.homes == 1 and took > 7.0


def test_a14_3_any_item_the_provider_sends_proves_the_stream_alive(scene):
    """F1: an empty delta (a usage chunk, a role chunk, a ping) stamps the clock; so does a thought."""
    K.set_fallback_models("")
    quiet(3.0)
    alive = ([("say", "visible ")]
             + [x for _ in range(5) for x in (("sleep", 1.0), ("say", ""))]
             + [x for _ in range(5) for x in (("sleep", 1.0), ("think", "still here"))]
             + [("say", "done")])
    out, result, took, ctx, asked = go(scene, [alive])
    assert out == "ok" and scene.homes == 1 and took >= 10.0


def test_a14_4_zero_turns_it_off(scene):
    K.set_fallback_models("")
    quiet(0)
    out, *_rest = go(scene, [VISIBLE_THEN_SILENT], limit=120.0)
    assert out == "waits" and scene.homes == 1


def test_a14_5_never_before_the_first_visible_text(scene):
    """The mid-thought stall is the quiet watch's, not this one's: no visible text, nothing armed."""
    K.set_fallback_models("")
    quiet(3.0)
    K.set_thought_quiet(0)                                     # the other watch off: a silent thinker waits
    out, *_rest = go(scene, [STALL], limit=120.0)
    assert out == "waits" and scene.homes == 1


def test_a14_6_never_for_a_local_endpoint(scene, monkeypatch):
    K.set_fallback_models("")
    quiet(3.0)
    monkeypatch.setattr(K, "_kame_looks_local", lambda base: True)
    out, *_rest = go(scene, [VISIBLE_THEN_SILENT], limit=120.0)
    assert out == "waits" and scene.homes == 1


def test_a14_7_the_cut_reads_as_a_timeout_and_a_mid_answer_drop(scene):
    exc = K._KameAnswerQuiet("kame: read timed out - the answer was silent for 3s after it had begun")
    assert isinstance(exc, ConnectionError)
    assert K._classify_error(exc)[1] == "timeout"
    K.set_fallback_models("")
    quiet(3.0)
    before = K._KAME_COUNTS["stream_drops"]
    go(scene, [VISIBLE_THEN_SILENT, [("say", "visible resumed")]])
    assert K._KAME_COUNTS["stream_drops"] == before + 1


def test_a14_8_a_call_that_is_not_a_chat_stream_is_not_watched(scene):
    K.set_fallback_models("")
    quiet(3.0)
    ctx, _ = make_ctx(scene.home, call_type="Utility")
    assert K._kame_answer_quiet_for(None, ctx) == 0.0


# --- review (2026-11-14): Python 3.10 has no asyncio.timeout - the watch steps aside, the call still runs ----------

def test_without_asyncio_timeout_the_answer_watch_steps_aside(monkeypatch):
    monkeypatch.delattr(asyncio, "timeout", raising=False)
    monkeypatch.setattr(K, "_kame_answer_quiet_for", lambda owner, ctx: 45.0)
    calls = []

    async def attempt():
        calls.append(1)
        return "answered"

    ctx = {"progress": {}}
    assert asyncio.run(K._kame_with_answer_watch(None, ctx, attempt)) == "answered"
    assert calls == [1]

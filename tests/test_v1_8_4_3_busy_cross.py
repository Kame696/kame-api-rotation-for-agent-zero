"""1.8.4.3 (A9, PLAN section 7) - a model that said "busy" is raced at once (Agent Zero). Released as 1.9.0.0.

When the model in use has answered **busy** on a key in this reply (a provider-side refusal: a 5xx / "overloaded",
or a timeout the provider answered - never a throttle, never our own timeout), the NEXT attempt on that model is
raced by the next model of the list from its first instant, instead of waiting for the cross timer (3 x the usual
first word, 8 s at least) to collect the evidence the refusal already gave. Who may be raced is still the cross
list (same provider, free, hedge mode not off; none for a background job, under "Never switch", a continuation or
a tool-call replay); the request is counted as a race request; a model already refused busy in this reply is not
the one moved.

A virtual clock (kame_vclock.py). No Agent Zero installation, no network.
"""
from __future__ import annotations

import pytest

from test_v1_8_2_7_speed_fallback import (  # noqa: F401  (fixtures are used by name)
    ID, KEYS, Refusal, clean, make_ctx, streaming,
)
from test_v1_8_4_3_quiet_guard import (  # noqa: F401  (fixtures are used by name)
    NEXT, NEXT_ID, THIRD, THIRD_ID, Scene, events, ledger, overloaded, scene,
)
from kame_vclock import vt  # noqa: F401
import kame_engine as K


def throttled():
    return Refusal("Error code: 429", 429,
                   {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED",
                              "message": "Quota exceeded for metric requests per minute. Please retry in 20s"}})


@pytest.fixture(autouse=True)
def no_watch_here(scene):
    """These cases are about the retry, not about thoughts: the watch is off unless a case turns it on."""
    K.set_thought_quiet(0)
    yield


# 1 --------------------------------------------------------------------------------------------------------

def test_1_a_503_then_a_silent_retry_is_raced_by_the_next_model_at_once(scene):
    scene.scripts[NEXT] = [("sleep", 0.05), ("say", "from the next model")]
    ctx, result = scene.reply([[("fail", overloaded)], [("sleep", 3.0), ("say", "the silent retry")]])
    assert result is scene.objs[NEXT]
    assert scene.vt.elapsed() < 2.0                                  # well under the 8 s floor of the cross timer
    assert [w for _t, w in scene.starts] == ["home0", "home1", NEXT]
    assert abs(scene.started(NEXT)[0] - scene.started("home1")[0]) < 0.1     # asked beside the retry, at its first instant
    said = [e for e in events() if "asked beside the retry" in e]
    assert said and "said it is busy (503)" in said[0], events()
    assert any(e.startswith("your model said it is busy: ") and "answered first" in e for e in events()), events()
    assert not any("no first word after" in e for e in events())
    assert K._KAME_LAST_MODEL_SWITCH["reason"] == "your model said it is busy: raced"
    assert scene.hops[-1].get("why") == "busy"
    assert ledger()["race"] == 1                                    # one race request, shown in the cost readout
    assert ID in K._KAME_BUSY_UNTIL                                 # the home model is inside a busy window


# 2 --------------------------------------------------------------------------------------------------------

def test_2_when_the_retry_answers_first_nothing_is_noted_against_either_model(scene):
    scene.scripts[NEXT] = [("sleep", 3.0), ("say", "from the next model")]
    ctx, result = scene.reply([[("fail", overloaded)], [("sleep", 0.05), ("say", "the retry answers")]])
    assert result is scene.objs["home1"]
    assert NEXT in scene.cancelled                                  # the next model's leg was cut
    assert ID not in K._KAME_BUSY_UNTIL and NEXT_ID not in K._KAME_BUSY_UNTIL
    assert not any("answered first" in e for e in events()), events()
    assert not ctx.get("answered_by")


# 3 --------------------------------------------------------------------------------------------------------

def test_3_differential_a_slow_first_try_never_asks_the_next_model_before_its_timer(scene):
    scene.scripts[NEXT] = [("say", "never asked")]
    ctx, result = scene.reply([[("sleep", 1.0), ("say", "slow but fine")]])
    assert result is scene.objs["home0"]
    assert [w for _t, w in scene.starts] == ["home0"]
    assert ledger()["race"] == 0


# 4 --------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("failure", [throttled, lambda: TimeoutError("KAME: no first token within 5s on this key")],
                         ids=["a 429 per-minute throttle", "our own timeout"])
def test_4_a_failure_that_is_not_busy_does_not_race_the_retry(scene, failure):
    scene.scripts[NEXT] = [("say", "never asked")]
    ctx, result = scene.reply([[("fail", failure)], [("sleep", 3.0), ("say", "the retry")]])
    assert result is scene.objs["home1"]
    assert not scene.started(NEXT)
    assert not any("said it is busy" in e for e in events())


# 5 --------------------------------------------------------------------------------------------------------

def test_5_hedge_mode_off_never_switch_and_a_background_job_run_the_retry_alone(scene):
    scene.scripts[NEXT] = [("say", "never asked")]
    K.set_hedge_mode("off")
    scene.reply([[("fail", overloaded)], [("sleep", 3.0), ("say", "the retry")]])
    assert not scene.started(NEXT)
    K.set_hedge_mode("adaptive")
    scene.starts.clear()
    scene.homes = 0
    K.set_model_switch("never")
    scene.reply([[("fail", overloaded)], [("sleep", 3.0), ("say", "the retry")]])
    assert not scene.started(NEXT)
    K.set_model_switch("auto")
    scene.starts.clear()
    scene.homes = 0
    aux = type("Aux", (), {})()
    ctx = scene.ctx()
    base_entries = K._kame_cross_entry(ctx, ID, False, 30.0)
    assert base_entries, "the list has a model to race"
    ctx["aux_task"] = aux                                          # a background job
    assert K._kame_cross_entry(ctx, ID, False, 0.0) is None        # no slow bound for a job: no cross list to move


# 6 and 7 ----------------------------------------------------------------------------------------------------

def moved(ctx, busy_first, leg_busy=None):
    """What ``_kame_raced_attempt`` would race from the first instant: the cross list after A9's move."""
    ctx = dict(ctx)
    if leg_busy:
        ctx["leg_busy"] = list(leg_busy)
    reasoning = K._kame_reasoning_call(ctx)
    slow = K._kame_slow_after(ctx, ID, reasoning)
    return K._kame_busy_cross(ctx, K._kame_cross_entry(ctx, ID, reasoning, slow), busy_first)


def test_6_an_entry_already_refused_busy_as_a_race_leg_in_this_reply_is_not_the_one_moved(scene):
    ctx = scene.ctx()
    ordered, picked = moved(ctx, True, leg_busy=[NEXT_ID])
    assert picked == THIRD_ID and ordered[0][0]["identity"] == THIRD_ID and ordered[0][1] == 0.0
    ordered, picked = moved(ctx, True, leg_busy=[NEXT_ID, THIRD_ID])
    assert picked is None                                          # every entry heard busy: the list is left as it is
    ordered, picked = moved(ctx, True)
    assert picked == NEXT_ID and ordered[0][0]["identity"] == NEXT_ID and ordered[0][1] == 0.0


def test_7_the_second_cross_entry_keeps_its_own_time(scene):
    ctx = scene.ctx()
    plain = K._kame_cross_entry(ctx, ID, K._kame_reasoning_call(ctx), K._kame_slow_after(ctx, ID, False))
    ordered, picked = moved(ctx, True)
    assert len(plain) == 2 and len(ordered) == 2
    assert ordered[1][0]["identity"] == THIRD_ID and ordered[1][1] == plain[1][1] > 0.0
    again, none = moved(ctx, False)                                 # no busy refusal: the list is as it was
    assert none is None and [(e["identity"], t) for e, t in again] == [(e["identity"], t) for e, t in plain]


# 8 --------------------------------------------------------------------------------------------------------

def test_8_under_the_quiet_watch_the_busy_cross_leg_that_thinks_and_goes_quiet_is_rescued(scene):
    K.set_thought_quiet(1.0)
    scene.scripts[NEXT] = [("sleep", 0.05), ("think", "the next model thinks"), ("sleep", 500)]
    scene.scripts[THIRD] = [("sleep", 0.1), ("say", "the rescue answers")]
    ctx, result = scene.reply([[("fail", overloaded)], [("sleep", 500)]])
    assert result is scene.objs[THIRD]
    assert scene.started(THIRD) and NEXT in scene.cancelled
    assert ctx["rescued"] is True and ledger()["rescue"] == 1
    assert THIRD_ID not in K._KAME_BUSY_UNTIL

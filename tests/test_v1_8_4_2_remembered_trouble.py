"""1.8.4.2 (S1b, S1c, S1d) - remembered trouble starts a reply on the next model (Agent Zero).

A model that keeps failing (``kame_model_health``: latest judged outcome a failure, two or more failures in the
last ten within a week) does not LEAD a reply. Where the start probe is allowed the reply starts on the first model
of the list that is not remembered as failing and the failing one is asked BESIDE it; where it is not (asked 5 s
ago), the hand-over starts the reply on the next model without a request. Never for a background job, never under
"Never switch" / "Ignore my fallback list", never able to stop a reply (no list -> the model itself).

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
import kame_model_health as mh

NEXT = "google/gemini-3.7-flash"
NEXT_ID = "google:gemini-3.7-flash"
THIRD = "google/gemini-3.6-flash"
THIRD_ID = "google:gemini-3.6-flash"


def overloaded():
    return Refusal("Error code: 503", 503,
                   {"error": {"code": 503, "message": "The model is overloaded. Please try again later.",
                              "status": "UNAVAILABLE"}})


@pytest.fixture(autouse=True)
def memory(tmp_path, monkeypatch):
    """Remembered trouble ON, in a temp directory, and every in-memory window clean."""
    monkeypatch.delenv("KAME_NO_REMEMBERED_TROUBLE", raising=False)
    assert K._KMH is mh
    mh.HEALTH.configure(directory=tmp_path, calls_path=tmp_path / "no-calls.jsonl")
    K._KAME_BUSY_UNTIL.clear()
    K._KAME_BUSY_WINDOW.clear()
    K._KAME_BUSY_KIND.clear()
    K._KAME_PROBED_AT.clear()
    K._KAME_REPLIES.clear()
    K._KAME_TURNS.clear()
    yield
    mh.HEALTH.configure(directory=None)
    K._KAME_BUSY_UNTIL.clear()
    K._KAME_BUSY_WINDOW.clear()
    K._KAME_PROBED_AT.clear()


def risky(identity, fails=2):
    """The model failed ``fails`` times lately and its latest outcome is a failure."""
    now = time.time()
    for i in range(fails):
        mh.HEALTH.note(identity, False, now=now - 60 + i)
    assert mh.HEALTH.risky(identity) is (fails >= 2)


def requests(log):
    return [k for k in log if not k.startswith("cancelled:")]


def events():
    return [e["reason"] for e in K._KJ.EVENTS.recent(80)]


# --- S1c: the start decision ------------------------------------------------------------------------

def test_a_risky_best_model_starts_the_reply_on_the_next_model_and_is_asked_beside(fake_models, vt):
    K.set_fallback_models(NEXT)
    risky(ID)
    log = []
    ctx, _ = make_ctx(streaming({k: 2.0 for k in KEYS}, log=log))        # the best model is slow today
    result = vt.run(K._kame_carousel(None, ctx), limit=30)
    assert result[0] == f"fallback answer from {NEXT}"
    assert len(requests(log)) == 1 and len(fake_models) == 1             # both went out at t = 0 ...
    assert vt.elapsed() < 0.5                                            # ... and the list model answered
    assert ctx.get("probe_primary") is True


def test_a_model_that_is_not_risky_leads_the_reply_alone(fake_models, vt):
    K.set_fallback_models(NEXT)
    log = []
    ctx, _ = make_ctx(streaming({}, log=log))
    assert vt.run(K._kame_carousel(None, ctx), limit=30)[0].startswith("answer from ")
    assert len(requests(log)) == 1 and not fake_models and ctx.get("probe_primary") is not True


def test_one_failure_is_not_trouble(fake_models, vt):
    K.set_fallback_models(NEXT)
    risky(ID, fails=1)
    log = []
    ctx, _ = make_ctx(streaming({}, log=log))
    assert vt.run(K._kame_carousel(None, ctx), limit=30)[0].startswith("answer from ")
    assert not fake_models


def test_risky_and_the_probe_not_due_the_next_model_alone_no_request_on_the_best(fake_models, vt):
    K.set_fallback_models(NEXT)
    risky(ID)
    K._KAME_PROBED_AT[ID] = time.time() - 5.0                            # asked 5 s ago
    log = []
    ctx, _ = make_ctx(streaming({}, log=log))
    result = vt.run(K._kame_carousel(None, ctx), limit=30)
    assert result[0] == f"fallback answer from {NEXT}"
    assert requests(log) == [] and len(fake_models) == 1
    assert ctx.get("probe_primary") is not True


def test_the_ask_of_a_risky_model_is_stamped_once_per_twenty_seconds(fake_models, vt):
    K.set_fallback_models(NEXT)
    risky(ID)
    first, _ = make_ctx(streaming({k: 2.0 for k in KEYS}))
    assert K._kame_start_probe_allowed(first, ID) is True
    assert first["probe_risky"] == (2, 2)
    second, _ = make_ctx(streaming({}))
    assert K._kame_start_probe_allowed(second, ID) is False              # not due again
    vt.advance(21.0)
    third, _ = make_ctx(streaming({}))
    assert K._kame_start_probe_allowed(third, ID) is True                # due again after twenty seconds


def test_a_background_job_never_gets_the_memory(fake_models, vt):
    K.set_fallback_models(NEXT)
    risky(ID)
    log = []
    ctx, _ = make_ctx(streaming({}, log=log), call_type="Util")
    assert vt.run(K._kame_carousel(None, ctx), limit=30)[0].startswith("answer from ")
    assert len(requests(log)) == 1 and not fake_models and ctx.get("probe_primary") is not True


def test_never_switch_keeps_the_best_model(fake_models, vt):
    K.set_fallback_models(NEXT)
    K.set_model_switch("never")
    risky(ID)
    log = []
    ctx, _ = make_ctx(streaming({}, log=log))
    assert vt.run(K._kame_carousel(None, ctx), limit=30)[0].startswith("answer from ")
    assert len(requests(log)) == 1 and not fake_models


def test_ignore_my_fallback_list_keeps_the_best_model(fake_models, vt):
    K.set_fallback_models(NEXT)
    K.set_no_model_fallback(True)
    try:
        risky(ID)
        log = []
        ctx, _ = make_ctx(streaming({}, log=log))
        assert vt.run(K._kame_carousel(None, ctx), limit=30)[0].startswith("answer from ")
        assert len(requests(log)) == 1 and not fake_models
    finally:
        K.set_no_model_fallback(False)


def test_a_model_with_no_list_is_never_stopped_by_the_memory(vt):
    risky(ID)
    log = []
    ctx, _ = make_ctx(streaming({}, log=log))
    assert vt.run(K._kame_carousel(None, ctx), limit=30)[0].startswith("answer from ")
    assert len(requests(log)) == 1


def test_the_kill_switch_is_the_1841_start_exactly(fake_models, vt, monkeypatch):
    K.set_fallback_models(NEXT)
    risky(ID)
    monkeypatch.setenv("KAME_NO_REMEMBERED_TROUBLE", "1")
    log = []
    ctx, _ = make_ctx(streaming({}, log=log))
    assert vt.run(K._kame_carousel(None, ctx), limit=30)[0].startswith("answer from ")
    assert len(requests(log)) == 1 and not fake_models and ctx.get("probe_primary") is not True


def test_a_window_that_is_open_is_the_old_rule_not_the_memory(fake_models, vt):
    K.set_fallback_models(NEXT)
    risky(ID)
    K._KAME_BUSY_UNTIL[ID] = time.time() + 20.0
    K._KAME_BUSY_WINDOW[ID] = 30.0
    K._KAME_PROBED_AT[ID] = time.time() - 25.0
    ctx, _ = make_ctx(streaming({k: 2.0 for k in KEYS}))
    assert vt.run(K._kame_carousel(None, ctx), limit=30)[0] == f"fallback answer from {NEXT}"
    assert not any("failed 2 of its last" in e for e in events()), events()     # busy, not "keeps failing"


# --- S1d: a model that is not remembered as failing first -----------------------------------------------

def test_the_second_model_risky_too_the_third_is_the_target(fake_models, vt):
    K.set_fallback_models(f"{NEXT}\n{THIRD}")
    risky(ID)
    risky(NEXT_ID)
    ctx, _ = make_ctx(streaming({k: 2.0 for k in KEYS}))
    result = vt.run(K._kame_carousel(None, ctx), limit=30)
    assert result[0] == f"fallback answer from {THIRD}"
    assert [m for m, _k in fake_models] == [THIRD]


def test_every_model_risky_the_choice_is_todays(fake_models, vt):
    K.set_fallback_models(f"{NEXT}\n{THIRD}")
    for ident in (ID, NEXT_ID, THIRD_ID):
        risky(ident)
    ctx, _ = make_ctx(streaming({k: 2.0 for k in KEYS}))
    assert vt.run(K._kame_carousel(None, ctx), limit=30)[0] == f"fallback answer from {NEXT}"


def test_the_ladder_puts_risky_entries_last_in_the_users_order_and_never_drops_one():
    K.set_fallback_models(f"{NEXT}\n{THIRD}")
    ctx, _ = make_ctx(streaming({}))
    assert [e["identity"] for e in K._kame_ladder_after(ctx)] == [NEXT_ID, THIRD_ID]
    risky(NEXT_ID)
    assert [e["identity"] for e in K._kame_ladder_after(ctx)] == [THIRD_ID, NEXT_ID]
    risky(THIRD_ID)
    assert [e["identity"] for e in K._kame_ladder_after(ctx)] == [NEXT_ID, THIRD_ID]


def test_never_switch_does_not_reorder_the_ladder():
    K.set_fallback_models(f"{NEXT}\n{THIRD}")
    ctx, _ = make_ctx(streaming({}))
    risky(NEXT_ID)
    K.set_model_switch("never")
    assert [e["identity"] for e in K._kame_ladder_after(ctx)] == [NEXT_ID, THIRD_ID]


# --- the event ----------------------------------------------------------------------------------------

def test_one_event_per_risky_reply_says_the_counts_and_who_starts(fake_models, vt):
    K.set_fallback_models(NEXT)
    risky(ID, fails=3)
    ctx, _ = make_ctx(streaming({k: 2.0 for k in KEYS}))
    vt.run(K._kame_carousel(None, ctx), limit=30)
    said = [e for e in events() if "failed 3 of its last 3 tries" in e]
    assert len(said) == 1, events()
    assert "this reply starts on" in said[0] and "and asks it beside" in said[0]
    assert not any("your best model was busy" in e for e in events())


def test_the_handover_start_says_it_too_without_asking_beside(fake_models, vt):
    K.set_fallback_models(NEXT)
    risky(ID)
    K._KAME_PROBED_AT[ID] = time.time() - 5.0
    ctx, _ = make_ctx(streaming({}))
    vt.run(K._kame_carousel(None, ctx), limit=30)
    said = [e for e in events() if "failed 2 of its last 2 tries" in e]
    assert len(said) == 1, events()
    assert "this reply starts on" in said[0] and "asks it beside" not in said[0]
    assert not any(e.endswith("answering with " + NEXT) for e in events())


# --- "Try this model now" -------------------------------------------------------------------------------

def test_forget_busy_forgets_the_trouble_and_the_next_reply_starts_on_the_best_model(fake_models, vt):
    K.set_fallback_models(NEXT)
    risky(ID)
    K.forget_busy(ID)
    assert mh.HEALTH.risky(ID) is False
    log = []
    ctx, _ = make_ctx(streaming({}, log=log))
    assert vt.run(K._kame_carousel(None, ctx), limit=30)[0].startswith("answer from ")
    assert len(requests(log)) == 1 and not fake_models


# --- S1b: what counts -------------------------------------------------------------------------------------

def test_an_answered_reply_is_one_success(fake_models, vt):
    ctx, _ = make_ctx(streaming({}))
    vt.run(K._kame_carousel(None, ctx), limit=30)
    assert mh.HEALTH.summary(ID) == (0, 1)


def test_a_background_job_that_answered_is_a_success_too(fake_models, vt):
    ctx, _ = make_ctx(streaming({}), call_type="Util")
    vt.run(K._kame_carousel(None, ctx), limit=30)
    assert mh.HEALTH.summary(ID) == (0, 1)


def test_a_busy_window_opening_is_one_failure_per_call(vt):
    for _ in range(3):
        K._kame_note_busy(ID, call_id="c1")        # three places of one call prove the same busy model
    assert mh.HEALTH.summary(ID) == (1, 1)
    vt.advance(1.0)                                  # a virtual clock stands still: two instants, not one
    K._kame_note_busy(ID, call_id="c2")
    assert mh.HEALTH.summary(ID) == (2, 2) and mh.HEALTH.risky(ID)


def test_a_reply_that_saw_a_503_and_then_answered_on_the_same_model_is_one_success(fake_models, vt):
    calls = {"n": 0}

    async def attempt(owner, key, ctx):
        calls["n"] += 1
        if calls["n"] <= 2:
            await asyncio.sleep(0.01)
            raise overloaded()
        await ctx["response_callback"]("ok", "ok")
        return ("answer", "")

    ctx, _ = make_ctx(attempt)
    vt.run(K._kame_carousel(None, ctx), limit=60)
    assert mh.HEALTH.summary(ID) == (0, 1)


def test_the_best_model_failing_two_replies_in_a_row_becomes_risky(fake_models, vt):
    K.set_fallback_models(NEXT)
    FALLBACK_BEHAVIOUR[NEXT] = "late"
    for _ in range(2):
        ctx, _ = make_ctx(streaming({}, fail={k: overloaded for k in KEYS}))
        vt.run(K._kame_carousel(None, ctx), limit=60)
        vt.advance(100.0)
        K._KAME_BUSY_UNTIL.clear()
        K._KAME_BUSY_WINDOW.clear()
        K._KAME_PROBED_AT.clear()
    assert mh.HEALTH.risky(ID) is True
    assert mh.HEALTH.summary(ID)[0] == 2

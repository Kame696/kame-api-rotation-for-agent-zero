"""1.8.2.9 (E7, decisions/0008) - background jobs get a budget.

In 1.8.2.8 every utility-model call of Agent Zero (chat naming, memory recall, memorize,
consolidation, compression, document query, infection check) got the chat's eternal wait and
its backup races. On Hermes the same design let one title job send 167 requests in 12 minutes
on the best model. Now a cosmetic job (chat naming) gets 30 s, 3 requests and never waits;
every other job 300 s, 6 requests and may wait; none races or asks keys together; one that
stops raises `KameAuxTimeout` (a timeout, with none of the words a host reads as "quota")
and, when it matters (not cosmetic), goes down KAME's own fallback list once first. The chat
is never budgeted. `KAME_AUX_UNBOUNDED=1` brings 1.8.2.8 back.

The calls run inside functions named like Agent Zero's own (that is how KAME names a job:
from the call stack), on a virtual clock (kame_vclock.py). No Agent Zero, no network.
"""
from __future__ import annotations

import sys
import types

import pytest

from test_v1_8_2_7_speed_fallback import (  # noqa: F401  (fixtures are used by name)
    FALLBACK_BEHAVIOUR, ID, KEYS, Refusal, clean, fake_models, make_ctx, streaming,
    _first_then_rest, _rest_every_key,
)
from kame_vclock import vt  # noqa: F401
import kame_engine as K
import kame_hedging as H

NEXT = "google/gemini-3.7-flash"
FORBIDDEN_WORDS = ("credit", "quota", "rate", "limit", "429")


def overloaded():
    return Refusal("Error code: 503", 503,
                   {"error": {"code": 503, "message": "The model is overloaded. Please try again later.",
                              "status": "UNAVAILABLE"}})


# Stand-ins for Agent Zero's own frames: the carousel is awaited from inside each one.
async def generate_name(ctx):
    return await K._kame_carousel(None, ctx)


async def summarize_messages(ctx):
    return await K._kame_carousel(None, ctx)


async def search_memories(ctx):
    return await K._kame_carousel(None, ctx)


async def call_chat_model(ctx):
    return await K._kame_carousel(None, ctx)


@pytest.fixture(autouse=True)
def calm(monkeypatch):
    K._KAME_BUSY_UNTIL.clear()
    K._KAME_BUSY_WINDOW.clear()
    getattr(K, "_KAME_PROBED_AT", {}).clear()
    getattr(K, "_KAME_AUX_MEMO", {}).clear()
    getattr(K, "_KAME_AUX_STOPPED", {}).clear()
    monkeypatch.delenv("KAME_AUX_UNBOUNDED", raising=False)
    K._KAME_AUX_UNBOUNDED = False if hasattr(K, "_KAME_AUX_UNBOUNDED") else None
    yield
    K._KAME_BUSY_UNTIL.clear()
    K._KAME_BUSY_WINDOW.clear()
    if hasattr(K, "_KAME_AUX_UNBOUNDED"):
        K._KAME_AUX_UNBOUNDED = False


def requests(log):
    return [k for k in log if not k.startswith("cancelled:")]


def util_ctx(attempt, **kw):
    return make_ctx(attempt, call_type="Util", **kw)[0]


# --- the cosmetic class: chat naming ----------------------------------------------------

def test_a_title_job_on_a_pool_that_is_resting_stops_at_once_with_no_request(vt):
    log = []
    ctx = util_ctx(streaming({}, log=log))
    _rest_every_key()                                     # every key out for an hour
    with pytest.raises(K.KameAuxTimeout):
        vt.run(generate_name(ctx), limit=60)
    assert log == [] and vt.elapsed() < 0.1


def test_a_title_job_on_a_busy_model_sends_three_requests_and_stops_within_thirty_seconds(vt):
    log = []
    ctx = util_ctx(streaming({}, fail={k: overloaded for k in KEYS}, log=log))
    with pytest.raises(K.KameAuxTimeout) as caught:
        vt.run(generate_name(ctx), limit=120)
    assert len(requests(log)) <= 3 and vt.elapsed() <= 30.0
    assert K._KAME_AUX_STOPPED["title_generation"] == 1
    assert isinstance(caught.value, TimeoutError)
    assert any(e["kind"] == "gave_up" for e in K._KJ.EVENTS.recent(30))


def test_the_stop_is_a_timeout_shaped_error_without_the_words_a_host_reads_as_a_quota(vt):
    ctx = util_ctx(streaming({}, fail={k: overloaded for k in KEYS}))
    with pytest.raises(K.KameAuxTimeout) as caught:
        vt.run(generate_name(ctx), limit=120)
    text = str(caught.value).lower()
    assert "title_generation" in text and "keys are kept for your chat" in text
    assert not [w for w in FORBIDDEN_WORDS if w in text], text
    assert not hasattr(caught.value, "status_code") and not hasattr(caught.value, "response")


def test_a_second_title_job_within_thirty_seconds_stops_with_no_request(vt):
    log = []
    first = util_ctx(streaming({}, fail={k: overloaded for k in KEYS}, log=log))
    with pytest.raises(K.KameAuxTimeout):
        vt.run(generate_name(first), limit=120)
    asked = len(log)
    second = util_ctx(streaming({}, fail={k: overloaded for k in KEYS}, log=log))
    with pytest.raises(K.KameAuxTimeout):
        vt.run(generate_name(second), limit=120)
    assert len(log) == asked                              # the host's own retry costs nothing
    vt.advance(31.0)
    third = util_ctx(streaming({}, fail={k: overloaded for k in KEYS}, log=log))
    with pytest.raises(K.KameAuxTimeout):
        vt.run(generate_name(third), limit=120)
    assert len(log) > asked                               # after the memo, the model is asked again


def test_a_title_job_never_goes_down_the_fallback_list(fake_models, vt):
    K.set_fallback_models(NEXT)
    ctx = util_ctx(streaming({}, fail={k: overloaded for k in KEYS}))
    with pytest.raises(K.KameAuxTimeout):
        vt.run(generate_name(ctx), limit=120)
    assert fake_models == []


# --- the important class: everything else -----------------------------------------------

def test_a_compression_on_a_busy_model_waits_on_the_probe_clock_and_stops_within_five_minutes(vt):
    # review H1 (this test used to pin the bug): the provider's "busy" costs no quota, so it does not
    # count toward the six requests; the job asks on the 20-second clock and stops at 300 s, with no
    # memo behind (the next important job is asked, not turned away). Details: test_..._review_fixes.py
    log = []
    ctx = util_ctx(streaming({}, fail={k: overloaded for k in KEYS}, log=log))
    with pytest.raises(K.KameAuxTimeout):
        vt.run(summarize_messages(ctx), limit=600)
    assert 270.0 <= vt.elapsed() <= 300.5   # stops when the next ask cannot fit
    assert K._KAME_AUX_STOPPED["compression"] == 1
    assert ("google:gemini/gemini-3.8-flash", "important") not in K._KAME_AUX_MEMO


def test_a_compression_that_stops_goes_down_the_list_once_and_the_summary_comes_from_it(fake_models, vt):
    K.set_fallback_models(NEXT)
    log = []
    ctx = util_ctx(streaming({}, fail={k: overloaded for k in KEYS}, log=log))
    result = vt.run(summarize_messages(ctx), limit=600)
    assert result[0] == f"fallback answer from {NEXT}"
    assert len(requests(log)) <= 6 and len(fake_models) == 1
    assert fake_models[0][0] == NEXT                      # the one list model was asked, once


def test_a_job_that_waits_for_a_resting_key_does_so_inside_its_budget(vt):
    ctx = util_ctx(streaming({}))
    _rest_every_key(40, "per_minute")                     # the keys are back in 40 s: well inside 300 s
    result = vt.run(search_memories(ctx), limit=600)
    assert result[0].startswith("answer from ") and 30.0 < vt.elapsed() < 120.0


def test_a_job_whose_next_key_is_back_after_its_budget_stops_at_once(vt):
    log = []
    ctx = util_ctx(streaming({}, log=log))
    _rest_every_key()                                     # an hour: past 300 s
    with pytest.raises(K.KameAuxTimeout):
        vt.run(search_memories(ctx), limit=60)
    assert log == [] and vt.elapsed() < 0.1


def test_an_unknown_job_is_never_budgeted(vt):
    # review H2: no Agent Zero frame on the stack (an agent tool, a mail handler, a third-party
    # plugin): the stop could end a turn, so the job keeps the 1.8.2.8 wait. Never cosmetic either.
    assert K._kame_aux_task() == ("utility", False)
    log = []
    ctx = util_ctx(streaming({}, fail={k: overloaded for k in KEYS}, log=log))
    with pytest.raises(TimeoutError) as caught:
        vt.run(K._kame_carousel(None, ctx), limit=120)    # waits like 1.8.2.8: only the test's clock stops it
    assert not isinstance(caught.value, K.KameAuxTimeout)
    assert len(requests(log)) > 6 and vt.elapsed() >= 119.0
    assert ctx.get("aux") is None and ctx["aux_task"] == "utility"


# --- no races, no bursts, no hedging for a background job -------------------------------

def test_no_backup_leg_is_ever_started_for_a_background_call_even_in_faster_mode(vt, monkeypatch):
    K.set_hedge_mode("max")
    monkeypatch.setattr(H.HEDGE_TRACKER, "threshold", lambda *a, **k: 0.05)
    first = KEYS[0]
    log = []
    ctx = util_ctx(streaming({k: 2.0 for k in KEYS}, log=log))
    monkeypatch.setattr(K, "_get_best_key", _first_then_rest(first))

    async def document_qa():
        return await K._kame_carousel(None, ctx)

    result = vt.run(document_qa(), limit=60)
    assert result[0].startswith("answer from ")
    assert len(requests(log)) == 1                        # 1.8.2.8 sent a second request at 0.05 s


# --- the switch --------------------------------------------------------------------------

def test_the_unbounded_switch_brings_back_the_old_wait(vt, monkeypatch):
    monkeypatch.setenv("KAME_AUX_UNBOUNDED", "1")
    K._kame_apply_env_overrides()
    log = []
    ctx = util_ctx(streaming({}, fail={k: overloaded for k in KEYS}, log=log))
    with pytest.raises(TimeoutError) as caught:
        vt.run(generate_name(ctx), limit=60)
    assert not isinstance(caught.value, K.KameAuxTimeout)
    assert len(requests(log)) > 3 and vt.elapsed() >= 59.0


# --- the chat is never budgeted ------------------------------------------------------------

def test_the_chat_is_never_budgeted(vt):
    log = []
    ctx, _ = make_ctx(streaming({}, fail={k: overloaded for k in KEYS}, log=log))   # a "Chat" call
    with pytest.raises(TimeoutError) as caught:
        vt.run(call_chat_model(ctx), limit=120)
    assert not isinstance(caught.value, getattr(K, "KameAuxTimeout", ()))
    assert len(requests(log)) > 6 and vt.elapsed() >= 119.0


# --- naming a job from the call stack ---------------------------------------------------------

FRAMES = [
    ("generate_name", "title_generation", "cosmetic"),
    ("change_name", "title_generation", "cosmetic"),
    ("search_memories", "memory_recall", "important"),
    ("_extract_search_keywords", "memory_consolidation", "important"),
    ("_analyze_memory_consolidation", "memory_consolidation", "important"),
    ("summarize_messages", "compression", "important"),
    ("summarize", "compression", "important"),
    ("_compact_single_pass", "compression", "important"),
    ("_compact_large_history", "compression", "important"),
    ("document_qa", "document_query", "important"),
    ("_answer_questions_from_content", "document_query", "important"),
    ("_run_check", "infection_check", "important"),
]


def _frame_named(name, module="agent.thing"):
    namespace = {"K": K, "__name__": module}
    exec("def probe():\n    return K._kame_aux_task()\n", namespace)
    fn = namespace["probe"]
    fn.__code__ = fn.__code__.replace(co_name=name)
    return fn


@pytest.mark.parametrize("name, task, klass", FRAMES)
def test_each_agent_zero_frame_maps_to_its_task_and_class(name, task, klass):
    assert _frame_named(name)() == (task, True)
    from kame_aux import budget_for
    assert budget_for(task, True, None, 0).klass == klass


@pytest.mark.parametrize("module, task", [
    ("usr.plugins._memory.extensions._50_memorize_fragments", "memorize_fragments"),
    ("usr.plugins._memory.extensions._51_memorize_solutions", "memorize_solutions"),
])
def test_memorize_is_told_apart_by_the_extension_that_runs_it(module, task):
    assert _frame_named("memorize", module)() == (task, True)


def test_the_nearest_agent_zero_frame_wins_and_call_utility_model_alone_names_nothing():
    inner = _frame_named("summarize_messages")
    ns = {"K": K, "inner": inner, "__name__": "agent.x"}
    exec("def outer():\n    return inner()\n", ns)
    ns["outer"].__code__ = ns["outer"].__code__.replace(co_name="generate_name")
    assert ns["outer"]() == ("compression", True)                      # the nearer frame
    assert _frame_named("call_utility_model")() == ("utility", False)  # it is every job's frame

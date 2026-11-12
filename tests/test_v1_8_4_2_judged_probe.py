"""1.8.4.2 (S1e) - a start probe that lost the race is heard to its end (Agent Zero).

At the start of a reply in the busy tail the best model is asked BESIDE the next model; whoever speaks first is shown.
The probe was cut the moment the reply's own leg brought a word, long before a recovered best model could show one,
so "the best model is back" could never be learned. Now the probe that lost is kept (by its own task, off the
reply) until its first WORKING token (text or reasoning: an empty delta is no word), its normal end, its error, or
30 s, then cut. ok -> the busy window ends and the record gets a success; failed -> the normal failure path;
silent -> the window opens.

A virtual clock (kame_vclock.py). No Agent Zero installation, no network.
"""
from __future__ import annotations

import asyncio
import sys
import time
import types

import pytest

from test_v1_8_2_7_speed_fallback import (  # noqa: F401  (fixtures are used by name)
    FALLBACK_BEHAVIOUR, ID, KEYS, Refusal, clean, fake_models, make_ctx, streaming,
)
from kame_vclock import vt  # noqa: F401
import kame_engine as K
import kame_model_health as mh

NEXT = "google/gemini-3.7-flash"


def overloaded():
    return Refusal("Error code: 503", 503,
                   {"error": {"code": 503, "message": "The model is overloaded. Please try again later.",
                              "status": "UNAVAILABLE"}})


@pytest.fixture(autouse=True)
def memory(tmp_path, monkeypatch):
    monkeypatch.delenv("KAME_NO_REMEMBERED_TROUBLE", raising=False)
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


def ended_ago(seconds=5.0, window=30.0):
    """The best model's window ended ``seconds`` ago: the reply is in the tail, so the start probe is allowed."""
    K._KAME_BUSY_UNTIL[ID] = time.time() - seconds
    K._KAME_BUSY_WINDOW[ID] = window
    K._KAME_PROBED_AT[ID] = time.time() - seconds - window


def events():
    return [e["reason"] for e in K._KJ.EVENTS.recent(80)]


def reply(vt, attempt, **ctx_kw):
    """One reply that starts on the next model (it answers at once) with the best model asked beside it."""
    ctx, forwarded = make_ctx(attempt, **ctx_kw)
    result = vt.run(K._kame_carousel(None, ctx), limit=30)
    assert result[0] == f"fallback answer from {NEXT}" and ctx.get("probe_primary") is True
    return ctx, forwarded


# --- the four verdicts ---------------------------------------------------------------------------------

def test_a_probe_that_speaks_late_is_heard_and_the_best_model_is_back(fake_models, vt):
    K.set_fallback_models(NEXT)
    ended_ago()
    log = []
    reply(vt, streaming({k: 4.0 for k in KEYS}, log=log))
    assert vt.elapsed() < 0.5                                       # the reply did not wait for the probe
    assert len(K._KAME_PROBE_JUDGES) == 1                           # the judge is listening
    assert ID in K._KAME_BUSY_UNTIL                                 # not yet known to be back
    vt.advance(5.0)
    assert ID not in K._KAME_BUSY_UNTIL and ID not in K._KAME_PROBED_AT and ID not in K._KAME_BUSY_WINDOW
    assert mh.HEALTH.summary(ID) == (0, 1)                          # a success in the record
    assert any("answered again beside the last reply - the next reply starts on it" in e for e in events()), events()
    assert not K._KAME_PROBE_JUDGES
    assert any(k.startswith("cancelled:") for k in log)             # then it was cut


def test_the_next_reply_starts_on_the_best_model_that_answered_again(fake_models, vt):
    K.set_fallback_models(NEXT)
    ended_ago()
    reply(vt, streaming({k: 4.0 for k in KEYS}))
    vt.advance(5.0)
    log = []
    ctx, _ = make_ctx(streaming({}, log=log))
    assert vt.run(K._kame_carousel(None, ctx), limit=30)[0].startswith("answer from ")
    assert len([k for k in log if not k.startswith("cancelled:")]) == 1 and len(fake_models) == 1
    assert ctx.get("probe_primary") is not True


def test_a_probe_silent_to_thirty_seconds_opens_the_window(fake_models, vt):
    K.set_fallback_models(NEXT)
    ended_ago()
    log = []
    reply(vt, streaming({k: 100.0 for k in KEYS}, log=log))
    vt.advance(29.0)
    assert len(K._KAME_PROBE_JUDGES) == 1                           # still listening at 29 s
    assert mh.HEALTH.summary(ID) == (0, 0)
    vt.advance(2.0)
    assert not K._KAME_PROBE_JUDGES                                 # 30 s: judged and cut
    assert K._KAME_BUSY_UNTIL[ID] > time.time()                     # the window is open again
    assert mh.HEALTH.summary(ID) == (1, 1)
    assert any(k.startswith("cancelled:") for k in log)


def test_a_probe_that_fails_with_a_503_goes_the_normal_failure_path(fake_models, vt):
    K.set_fallback_models(NEXT)
    ended_ago(window=30.0)

    async def attempt(owner, key, ctx):
        await asyncio.sleep(3.0)
        raise overloaded()

    reply(vt, attempt)
    vt.advance(4.0)
    assert not K._KAME_PROBE_JUDGES
    assert K._KAME_BUSY_WINDOW[ID] == 60.0 and K._KAME_BUSY_UNTIL[ID] > time.time()     # 30 -> 60, once
    assert mh.HEALTH.summary(ID) == (1, 1)
    assert any(e["kind"] == "rotation" and e["code"] == 503 for e in K._KJ.EVENTS.recent(80))   # judged like any leg


def test_a_probe_that_wins_the_race_is_the_reply_and_is_not_judged(fake_models, vt):
    K.set_fallback_models(NEXT)
    FALLBACK_BEHAVIOUR[NEXT] = "slow"                               # 3 s
    ended_ago()
    log = []
    ctx, _ = make_ctx(streaming({k: 0.1 for k in KEYS}, log=log))
    result = vt.run(K._kame_carousel(None, ctx), limit=30)
    assert result[0].startswith("answer from ") and not result[0].endswith("flash")
    assert not K._KAME_PROBE_JUDGES
    assert ID not in K._KAME_BUSY_UNTIL                             # the reply's answer closed the window as before


def test_an_empty_delta_is_no_word_and_the_probe_goes_silent(fake_models, vt):
    K.set_fallback_models(NEXT)
    ended_ago()

    async def attempt(owner, key, ctx):
        await asyncio.sleep(1.0)
        await ctx["response_callback"]("", "")                     # a role chunk: the stream is open, nothing said
        await asyncio.sleep(100.0)
        return ("never", "")

    reply(vt, attempt)
    vt.advance(5.0)
    assert len(K._KAME_PROBE_JUDGES) == 1 and ID in K._KAME_BUSY_UNTIL
    assert mh.HEALTH.summary(ID) == (0, 0)                          # no word yet, no success
    vt.advance(30.0)
    assert not K._KAME_PROBE_JUDGES
    assert mh.HEALTH.summary(ID) == (1, 1)                          # silent: a failure


def test_reasoning_is_a_working_token(fake_models, vt):
    K.set_fallback_models(NEXT)
    ended_ago()

    async def attempt(owner, key, ctx):
        await asyncio.sleep(2.0)
        await ctx["reasoning_callback"]("thinking", "thinking")
        await asyncio.sleep(100.0)
        return ("never", "")

    async def on_text(delta, full):
        return None

    async def on_reason(delta, full):
        return None

    ctx, _ = make_ctx(attempt)
    ctx["response_callback"], ctx["reasoning_callback"], ctx["tokens_callback"] = \
        K._kame_wrap_callbacks(ctx, on_text, on_reason, None)
    assert vt.run(K._kame_carousel(None, ctx), limit=30)[0] == f"fallback answer from {NEXT}"
    vt.advance(3.0)
    assert ID not in K._KAME_BUSY_UNTIL and mh.HEALTH.summary(ID) == (0, 1)


def test_a_probe_that_ends_normally_before_the_reply_does_is_heard_too(fake_models, vt):
    K.set_fallback_models(NEXT)
    ended_ago()

    async def attempt(owner, key, ctx):
        await asyncio.sleep(2.0)
        return ("an answer with no stream", "")                      # a non-stream call: it ends with its answer

    reply(vt, attempt)
    vt.advance(3.0)
    assert not K._KAME_PROBE_JUDGES
    assert ID not in K._KAME_BUSY_UNTIL and mh.HEALTH.summary(ID) == (0, 1)


# --- off the reply's thread -----------------------------------------------------------------------------

def test_a_cancelled_turn_does_not_end_the_judge_it_ends_by_its_deadline(fake_models, vt, monkeypatch):
    K.set_fallback_models(NEXT)
    ended_ago()

    class StreamingNext:
        def __init__(self, provider, name):
            self.a0_model_conf = types.SimpleNamespace(provider=provider)
            self.model_name = f"{provider}/{name}"

        async def unified_call(self, **kwargs):
            await asyncio.sleep(0.1)
            await kwargs["response_callback"]("x", "x")             # the list model's first word wins the race
            await asyncio.sleep(10.0)                               # ... and its reply goes on
            return ("fallback answer", "")

    monkeypatch.setattr(sys.modules["models"], "get_chat_model",
                        lambda provider, name, model_config=None, **kw: StreamingNext(provider, name))
    ctx, _ = make_ctx(streaming({k: 100.0 for k in KEYS}))

    async def scenario():
        task = asyncio.ensure_future(K._kame_carousel(None, ctx))
        await asyncio.sleep(1.0)
        assert len(K._KAME_PROBE_JUDGES) == 1                       # the probe lost at 0.1 s and is kept
        task.cancel()                                               # the user stops the turn
        await asyncio.gather(task, return_exceptions=True)
        assert len(K._KAME_PROBE_JUDGES) == 1                       # the judge is not the reply's: it goes on
        await asyncio.sleep(35.0)

    vt.run(scenario(), limit=80)
    assert not K._KAME_PROBE_JUDGES
    assert K._KAME_BUSY_UNTIL[ID] > time.time()                     # silent to the deadline: the window opened


def test_the_race_never_leaves_a_judge_without_a_deadline(fake_models, vt):
    # every scenario above ends with no judge; this one runs three replies in a row and checks nothing piles up
    K.set_fallback_models(NEXT)
    for delay in (1.0, 100.0, 4.0):
        ended_ago()
        reply(vt, streaming({k: delay for k in KEYS}))
        vt.advance(35.0)
        assert not K._KAME_PROBE_JUDGES


def test_the_reply_is_not_touched_by_the_kept_probe(fake_models, vt):
    K.set_fallback_models(NEXT)
    ended_ago()
    ctx, forwarded = reply(vt, streaming({k: 1.0 for k in KEYS}))
    vt.advance(5.0)
    assert forwarded == []              # nothing of the probe's words reached the screen (the list model's did not stream)

"""v1.9.0.2 (A11, Hermes H11) - a model due a second look is asked BESIDE the reply, never in front of it.

Live 2026-11-15 20:10 on Hermes (the same rule lives here as A10): the best model failed 10 of 10, the second failed
too but its second look was due, the third was healthy. The reply started on the second (a model due a second look
counted as not failing), the best was asked beside it, both stayed silent, and the healthy third only joined at the
cross bound: 14.3 s to a first word that the third gives in about two.

Owner, 2026-11-15: fixed before the release. A reply never starts on a model remembered as failing while one that is
not can start it; the model due a second look is asked beside the start probe from its first instant, kept and judged
like it: an answer clears its record (and, if first, the reply is its own), silence or a failure counts.

A virtual clock (kame_vclock.py). No Agent Zero installation, no network.
"""
from __future__ import annotations

import asyncio
import sys
import time
import types

import pytest

from test_v1_8_2_7_speed_fallback import (  # noqa: F401  (fixtures are used by name)
    ID, KEYS, clean, make_ctx, streaming,
)
from kame_vclock import vt  # noqa: F401
import kame_engine as K
import kame_model_health as mh

E2, E3 = "google/gemini-3.7-flash", "google/gemini-3.6-flash"
I2, I3 = "google:gemini-3.7-flash", "google:gemini-3.6-flash"
HOUR = 3600.0

DELAY = {}      # model name -> seconds before it answers
ASKED = []      # (model name, monotonic time asked)


class Model:
    def __init__(self, provider, name):
        self.a0_model_conf = types.SimpleNamespace(provider=provider)
        self.model_name = f"{provider}/{name}"

    async def unified_call(self, **kwargs):
        ASKED.append((self.model_name, time.monotonic()))
        await asyncio.sleep(DELAY.get(self.model_name, 0.0))
        return (f"fallback answer from {self.model_name}", "")


@pytest.fixture
def models(monkeypatch):
    DELAY.clear()
    ASKED.clear()
    mod = types.ModuleType("models")

    class ModelType:
        CHAT = "Chat"

    class ModelConfig:
        def __init__(self, **kw):
            self.__dict__.update(kw)

        def build_kwargs(self):
            return {}

    mod.ModelType, mod.ModelConfig = ModelType, ModelConfig
    mod.get_chat_model = lambda provider, name, model_config=None, **kw: Model(provider, name)
    monkeypatch.setitem(sys.modules, "models", mod)
    return ASKED


@pytest.fixture(autouse=True)
def memory(tmp_path, monkeypatch):
    monkeypatch.delenv("KAME_NO_REMEMBERED_TROUBLE", raising=False)
    mh.HEALTH.configure(directory=tmp_path, calls_path=tmp_path / "no-calls.jsonl")
    for table in (K._KAME_BUSY_UNTIL, K._KAME_BUSY_WINDOW, K._KAME_BUSY_KIND, K._KAME_PROBED_AT):
        table.clear()
    K._KAME_REPLIES.clear()
    K._KAME_TURNS.clear()
    yield
    mh.HEALTH.configure(directory=None)
    for table in (K._KAME_BUSY_UNTIL, K._KAME_BUSY_WINDOW, K._KAME_BUSY_KIND, K._KAME_PROBED_AT):
        table.clear()


def record(identity, pattern, last_age):
    """``pattern`` oldest first, 1 = answered, 0 = failed; the last one ``last_age`` seconds old, a minute apart."""
    now = time.time()
    n = len(pattern)
    for i, ch in enumerate(pattern):
        mh.HEALTH.note(identity, ch == "1", now=now - last_age - (n - 1 - i) * 60.0)


def the_2010_record():
    record(ID, "0" * 10, 60.0)              # the best model: dead, its start probe due
    record(I2, "100", 8 * HOUR)             # failing, its second look due
    record(I3, "111", 60.0)                 # healthy


def asked(name):
    return [at for model, at in ASKED if model == name]


def events():
    return [e["reason"] for e in K._KJ.EVENTS.recent(80)]


def reply(vt):
    ctx, _ = make_ctx(streaming({k: 100.0 for k in KEYS}))     # the best model stays silent
    started = time.monotonic()
    result = vt.run(K._kame_carousel(None, ctx), limit=30)
    return ctx, result, started


# -- where the reply starts --------------------------------------------------------------------------------

def test_the_start_goes_to_the_healthy_model_and_the_retest_is_asked_beside_at_once(models, vt):
    K.set_fallback_models(f"{E2}\n{E3}")
    the_2010_record()
    DELAY.update({E3: 0.05, E2: 0.5})
    ctx, result, started = reply(vt)
    assert result[0] == f"fallback answer from {E3}"
    assert ctx.get("probe_primary") is True and ctx.get("probe_retest") == I2
    assert asked(E2) and asked(E2)[0] - started < 0.5, "beside the reply from its first instant"
    assert vt.elapsed() < 0.5, "nobody waited for it"
    assert any("failed before and is due another look" in e for e in events()), events()


def test_with_no_healthy_model_the_one_due_a_retest_still_starts(models, vt):
    K.set_fallback_models(f"{E2}\n{E3}")
    record(ID, "0" * 10, 60.0)
    record(I2, "100", 8 * HOUR)             # due
    record(I3, "100", 60.0)                 # failing, not due
    DELAY.update({E2: 0.05})
    ctx, result, _ = reply(vt)
    assert result[0] == f"fallback answer from {E2}", "nothing is excluded: the best of what is left"
    assert not ctx.get("probe_retest"), "the model the reply starts on is not also its own second look"


def test_a_retested_model_that_answers_first_takes_the_reply(models, vt):
    K.set_fallback_models(f"{E2}\n{E3}")
    the_2010_record()
    DELAY.update({E2: 0.05, E3: 0.6})
    _, result, _ = reply(vt)
    assert result[0] == f"fallback answer from {E2}"


def test_a_second_look_heard_after_the_reply_clears_the_model(models, vt):
    K.set_fallback_models(f"{E2}\n{E3}")
    the_2010_record()
    DELAY.update({E3: 0.05, E2: 3.0})
    _, result, _ = reply(vt)
    assert result[0] == f"fallback answer from {E3}"
    assert mh.HEALTH.risky(I2), "not heard yet"
    vt.advance(4.0)
    assert not mh.HEALTH.risky(I2), "heard to its end: its answer is in the record"
    assert any("answered again beside the last reply" in e and "gemini-3.7" in e for e in events()), events()


def test_a_silent_second_look_counts_and_the_next_waits_longer(models, vt):
    K.set_fallback_models(f"{E2}\n{E3}")
    the_2010_record()
    DELAY.update({E3: 0.05, E2: 100.0})
    reply(vt)
    vt.advance(31.0)
    assert mh.HEALTH.risky(I2) and not mh.HEALTH.retest_due(I2), "its silence counted"


def test_the_silent_healthy_losers_rule_does_not_note_the_second_look_twice(models, vt):
    K.set_fallback_models(f"{E2}\n{E3}")
    the_2010_record()
    DELAY.update({E3: 0.05, E2: 3.0})
    reply(vt)
    assert mh.HEALTH.summary(I2)[0] == 2, "the cross win did not note the kept leg as a silent loser"


# -- when it is not asked ---------------------------------------------------------------------------------

def test_a_fresh_failure_is_not_asked_beside(models, vt):
    K.set_fallback_models(f"{E2}\n{E3}")
    record(ID, "0" * 10, 60.0)
    record(I2, "100", 120.0)                # two minutes ago: not due
    record(I3, "111", 60.0)
    DELAY.update({E3: 0.05})
    ctx, result, _ = reply(vt)
    assert result[0] == f"fallback answer from {E3}"
    assert not asked(E2) and not ctx.get("probe_retest")


def test_a_model_ranked_below_the_start_is_not_looked_at(models, vt):
    K.set_fallback_models(f"{E3}\n{E2}")    # the healthy model first in the user's list
    the_2010_record()
    DELAY.update({E3: 0.05})
    ctx, result, _ = reply(vt)
    assert result[0] == f"fallback answer from {E3}"
    assert not asked(E2) and not ctx.get("probe_retest")


def test_one_look_per_twenty_seconds_across_conversations(models):
    K.set_fallback_models(f"{E2}\n{E3}")
    the_2010_record()
    ctx, _ = make_ctx(streaming({}))
    start = {"identity": I3, "label": E3}
    first = K._kame_retest_beside(ctx, ID, start)
    assert first is not None and first["identity"] == I2
    assert K._kame_retest_beside(ctx, ID, start) is None
    assert K._kame_retest_beside(ctx, ID, start, now=time.time() + 21.0) is not None


def test_never_switch_asks_nothing_beside(models, monkeypatch):
    K.set_fallback_models(f"{E2}\n{E3}")
    the_2010_record()
    monkeypatch.setattr(K, "_KAME_MODEL_SWITCH", "never")
    ctx, _ = make_ctx(streaming({}))
    assert K._kame_retest_beside(ctx, ID, {"identity": I3, "label": E3}) is None


def test_when_the_second_look_wins_the_model_the_reply_started_on_is_not_noted_slow(models, vt):
    K.set_fallback_models(f"{E2}\n{E3}")
    the_2010_record()
    DELAY.update({E2: 0.05, E3: 0.6})
    _, result, _ = reply(vt)
    assert result[0] == f"fallback answer from {E2}"
    assert mh.HEALTH.summary(I3) == (0, 3), "asked at the same instant: losing says nothing about it"
    assert I3 not in K._KAME_BUSY_UNTIL

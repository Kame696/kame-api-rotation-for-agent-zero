"""1.9.0.4 (R2, R3) - the race asks models by their record, and the next model joins when IT usually answers
(Agent Zero).

Live 2026-11-18 (Hermes): the main model took 22 s, the model below it that had not answered once in ten days
(remembered failing) was raced on every slow reply, and the one that answers in 5 s was never reached.
``_kame_cross_entry`` now leaves a remembered failing model out, scans the whole list and orders by what is
learned, and times the first raced model by its OWN usual first word.

A virtual clock (kame_vclock.py) for the end-to-end case. No Agent Zero installation, no network.
"""
from __future__ import annotations

import asyncio
import sys
import time
import types

import pytest

from test_v1_8_2_7_speed_fallback import (  # noqa: F401  (fixtures are used by name)
    ID, KEYS, Refusal, clean, make_ctx, streaming,
)
from kame_vclock import vt  # noqa: F401
import kame_engine as K
import kame_model_health as mh

FALL = {n: f"google/gemini-race-{n}" for n in ("two", "three", "four", "five")}
FID = {n: f"google:gemini-race-{n}" for n in ("two", "three", "four", "five")}
LIST = "\n".join(FALL.values())


@pytest.fixture(autouse=True)
def health(monkeypatch, tmp_path):
    monkeypatch.delenv("KAME_NO_REMEMBERED_TROUBLE", raising=False)
    mh.HEALTH.configure(directory=tmp_path, calls_path=tmp_path / "no-calls.jsonl")
    K._KAME_BUSY_UNTIL.clear()
    K._KAME_BUSY_WINDOW.clear()
    K._KAME_PROBED_AT.clear()
    # the real-world constants: 12 s cold, 8 s at least, 6 s a step
    monkeypatch.setattr(K, "_KAME_CROSS_COLD_S", 12.0)
    monkeypatch.setattr(K, "_KAME_CROSS_MIN_S", 8.0)
    monkeypatch.setattr(K, "_KAME_CROSS_STEP_S", 6.0)
    K.set_fallback_models(LIST)
    K.set_hedge_mode("adaptive")
    yield
    K.set_hedge_mode("adaptive")
    mh.HEALTH.configure(directory=None)
    K._KAME_BUSY_UNTIL.clear()
    K._KAME_BUSY_WINDOW.clear()
    K._KAME_PROBED_AT.clear()


def learned(monkeypatch, table):
    """``{identity: median}`` -> the tracker answers ``(median, 2 x median)`` for it, None otherwise."""
    def stats(identity, reasoning):
        for prefix, median in table.items():
            if identity == prefix or identity.startswith(prefix + ":"):
                return (median, 2.0 * median)
        return None
    monkeypatch.setattr(K, "_kame_first_word_stats", stats)


def fail_twice(identity):
    now = time.time()
    mh.HEALTH.note(identity, False, now=now - 30)
    mh.HEALTH.note(identity, False, now=now - 20)
    assert mh.HEALTH.risky(identity)


def picks(slow=60.0):
    ctx, _ = make_ctx(streaming({}))
    out = K._kame_cross_entry(ctx, ID, False, slow)
    return [(e["identity"], round(at, 3)) for e, at in (out or [])]


def ids(slow=60.0):
    return [i for i, _ in picks(slow)]


# --- R2: by record, not by place ----------------------------------------------------------------------------------

def test_a_remembered_failing_model_is_left_out_and_the_next_one_races():
    fail_twice(FID["two"])
    assert ids() == [FID["three"], FID["four"]]


def test_a_failing_model_whose_retest_is_due_may_race():
    now = time.time()
    for at in (now - 5000, now - 4900, now - 4800):
        mh.HEALTH.note(FID["two"], False, now=at)
    assert mh.HEALTH.risky(FID["two"]) and mh.HEALTH.retest_due(FID["two"])
    # 1.9.0.2: a model due a second look comes after the healthy ones, but it is not left out
    assert ids() == [FID["three"], FID["four"]]
    K.set_fallback_models("\n".join([FALL["two"], FALL["three"]]))
    assert ids() == [FID["three"], FID["two"]]


def test_the_off_switch_keeps_the_1_9_0_3_order_and_timing(monkeypatch):
    fail_twice(FID["two"])
    monkeypatch.setenv("KAME_NO_REMEMBERED_TROUBLE", "1")
    learned(monkeypatch, {FID["four"]: 1.0})
    # list order, the 12 s cold join, the 6 s step; nothing learned or remembered is consulted
    assert picks() == [(FID["two"], 12.0), (FID["three"], 18.0)]


def test_the_fastest_learned_model_comes_first_even_fourth_in_the_list(monkeypatch):
    learned(monkeypatch, {ID: 22.0, FID["three"]: 15.0, FID["five"]: 3.0})
    assert ids() == [FID["five"], FID["three"]]


def test_unknown_models_keep_list_order_and_slower_ones_come_last(monkeypatch):
    learned(monkeypatch, {ID: 5.0, FID["two"]: 30.0})
    assert ids() == [FID["three"], FID["four"]], "two is learned slower than main: after the unknown ones"
    learned(monkeypatch, {ID: 5.0, FID["two"]: 30.0, FID["three"]: 31.0, FID["four"]: 32.0, FID["five"]: 33.0})
    assert ids() == [FID["two"], FID["three"]], "all slower: list order"


def test_main_with_nothing_learned_takes_any_learned_model_first(monkeypatch):
    learned(monkeypatch, {FID["four"]: 9.0})
    assert ids()[0] == FID["four"]


def test_the_main_model_listed_again_is_never_raced_against_itself():
    K.set_fallback_models(f"google/gemini/gemini-3.8-flash\n{FALL['two']}")
    assert ids() == [FID["two"]]


# --- R3: joins when it usually answers ----------------------------------------------------------------------------

def test_balanced_joins_at_its_own_median_first_word(monkeypatch):
    learned(monkeypatch, {ID: 22.0, FID["three"]: 5.4})
    assert picks()[0] == (FID["three"], 5.4)


def test_faster_joins_at_half_its_median(monkeypatch):
    K.set_hedge_mode("max")
    learned(monkeypatch, {ID: 22.0, FID["three"]: 5.4})
    assert picks()[0] == (FID["three"], 2.7)
    learned(monkeypatch, {ID: 22.0, FID["three"]: 1.2})
    assert picks()[0] == (FID["three"], 1.0), "the floor of Faster is 1 s"


def test_the_floor_in_balanced_is_two_seconds(monkeypatch):
    learned(monkeypatch, {ID: 22.0, FID["three"]: 0.8})
    assert picks()[0] == (FID["three"], 2.0)


def test_nothing_learned_about_the_candidate_keeps_todays_time(monkeypatch):
    learned(monkeypatch, {ID: 22.0})
    # main median 22 s: after = min(3 x 22, 0.5 x 60) = 30
    assert picks()[0] == (FID["two"], 30.0)
    learned(monkeypatch, {})
    assert picks()[0] == (FID["two"], 12.0), "nothing learned at all: the cold 12 s"


def test_a_candidate_slower_than_main_keeps_todays_time(monkeypatch):
    learned(monkeypatch, {ID: 5.0, FID["two"]: 35.0, FID["three"]: 36.0, FID["four"]: 37.0, FID["five"]: 38.0})
    assert picks()[0] == (FID["two"], 15.0)             # after = max(8, 3 x 5)


def test_never_later_than_todays_after(monkeypatch):
    learned(monkeypatch, {FID["two"]: 20.0})            # main has nothing learned: today's after is the cold 12 s
    assert picks()[0] == (FID["two"], 12.0)


def test_the_second_leg_steps_after_the_first_and_the_085_cut_stays(monkeypatch):
    learned(monkeypatch, {ID: 22.0, FID["three"]: 5.4})
    got = picks()
    assert got[1][1] == pytest.approx(5.4 + max(6.0, 0.5 * 5.4))
    # a bound of 12 s: the second would join at 11.4 s >= 0.85 x 12 = 10.2 -> only one leg
    assert len(picks(slow=12.0)) == 1


def test_saver_races_nothing():
    K.set_hedge_mode("off")
    assert picks() == []


# --- end to end: the faster model's reply is the one returned -----------------------------------------------------

class World:
    def __init__(self, clock):
        self.vt = clock
        self.starts = []             # (virtual seconds, model_name)


class ListModel:
    def __init__(self, provider, name, world):
        self.a0_model_conf = types.SimpleNamespace(provider=provider)
        self.model_name = f"{provider}/{name}"
        self.world = world

    async def unified_call(self, **kwargs):
        w = self.world
        w.starts.append((w.vt.elapsed(), self.model_name))
        await asyncio.sleep(0.05)
        callback = kwargs.get("response_callback")
        if callback is not None:
            await callback("x", "x")
        return (f"answer from {self.model_name}", "")


def test_the_faster_listed_model_answers_first_and_its_reply_is_returned(vt, monkeypatch):
    w = World(vt)
    mod = types.ModuleType("models")

    class ModelType:
        CHAT = "Chat"

    class ModelConfig:
        def __init__(self, **kw):
            self.__dict__.update(kw)

        def build_kwargs(self):
            return {}

    mod.ModelType, mod.ModelConfig = ModelType, ModelConfig
    mod.get_chat_model = lambda provider, name, model_config=None, **kw: ListModel(provider, name, w)
    monkeypatch.setitem(sys.modules, "models", mod)
    monkeypatch.setattr(K, "_KAME_SLOW_S", {False: (20.0, 20.0, 20.0), True: (20.0, 20.0, 20.0)})
    monkeypatch.setattr(K, "_KAME_CROSS_FAST_MIN_S", 0.25)
    learned(monkeypatch, {ID: 22.0, FID["three"]: 0.25})
    fail_twice(FID["two"])
    K._KJ.EVENTS.clear()

    async def main_model_thinks(owner, key, ctx):
        await asyncio.sleep(30.0)
        return ("main answer", "")

    ctx, _ = make_ctx(main_model_thinks)
    result = vt.run(K._kame_carousel(None, ctx), limit=120)
    assert result[0] == f"answer from google/gemini-race-three"
    assert vt.elapsed() < 1.0, vt.elapsed()
    assert ctx.get("answered_by") == FID["three"]
    assert [m for _t, m in w.starts] == ["google/gemini-race-three"], w.starts   # two (failing) was never asked
    assert w.starts[0][0] == pytest.approx(0.25, abs=0.05)


# --- review: a faster model that answers inside the main model's own usual time does not make it "slow" ---------
def _race_world(vt, monkeypatch, table, main_s=30.0):
    w = World(vt)
    mod = types.ModuleType("models")

    class ModelType:
        CHAT = "Chat"

    class ModelConfig:
        def __init__(self, **kw):
            self.__dict__.update(kw)

        def build_kwargs(self):
            return {}

    mod.ModelType, mod.ModelConfig = ModelType, ModelConfig
    mod.get_chat_model = lambda provider, name, model_config=None, **kw: ListModel(provider, name, w)
    monkeypatch.setitem(sys.modules, "models", mod)
    monkeypatch.setattr(K, "_KAME_SLOW_S", {False: (20.0, 20.0, 20.0), True: (20.0, 20.0, 20.0)})
    monkeypatch.setattr(K, "_KAME_CROSS_FAST_MIN_S", 0.25)
    learned(monkeypatch, table)
    K._KJ.EVENTS.clear()

    async def main_model_thinks(owner, key, ctx):
        await asyncio.sleep(main_s)
        return ("main answer", "")

    ctx, _ = make_ctx(main_model_thinks)
    return w, ctx


def test_beaten_inside_its_own_time_the_main_model_keeps_a_clean_record(vt, monkeypatch):
    _w, ctx = _race_world(vt, monkeypatch, {ID: 22.0, FID["three"]: 0.25})   # main late only after 10 s
    result = vt.run(K._kame_carousel(None, ctx), limit=120)
    assert result[0] == "answer from google/gemini-race-three"
    assert K._KMH.HEALTH.summary(ID) == (0, 0)                  # no failure in its record
    assert K._KAME_BUSY_UNTIL.get(ID, 0.0) <= time.monotonic()  # and no busy window: the next reply starts on it
    assert any("so it is not marked slow" in (e.get("reason") or "") for e in K._KJ.EVENTS.recent(30))

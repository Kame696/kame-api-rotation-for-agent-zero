"""1.9.0.4 (R4) - a raced leg that fails is replaced at once (Agent Zero).

Live 2026-11-18 (Hermes): the two raced models both ended 503 and nothing replaced them, so the reply waited for the
slow main model alone. A leg that asks the next model of the list and is refused (not for the request itself) now
gets ONE replacement on another key of the same model, started in the same instant: at most once per model per
race, never for a start probe, a rescue or a widen, never when no other key of that model is ready.

A virtual clock (kame_vclock.py). No Agent Zero installation, no network.
"""
from __future__ import annotations

import asyncio
import sys
import types

import pytest

from test_v1_8_2_7_speed_fallback import (  # noqa: F401  (fixtures are used by name)
    ID, KEYS, Refusal, clean, make_ctx, streaming,
)
from kame_vclock import vt  # noqa: F401
import kame_engine as K
import kame_model_health as mh

NEXT = "google/gemini-3.7-flash"
NEXT_ID = "google:gemini-3.7-flash"
SLOW = {False: (4.0, 4.0, 4.0), True: (4.0, 4.0, 4.0)}


def overloaded():
    return Refusal("Error code: 503", 503,
                   {"error": {"code": 503, "message": "The model is overloaded. Please try again later.",
                              "status": "UNAVAILABLE"}})


def gone():
    return Refusal("Error code: 410", 410, {"error": {"code": 410, "message": "The model is gone."}})


def bad_request():
    return Refusal("Error code: 400", 400, {"error": {"code": 400, "message": "Invalid argument: bad request",
                                                      "status": "INVALID_ARGUMENT"}})


class World:
    def __init__(self, vt):
        self.vt = vt
        self.plan = {}               # model_name -> [(seconds, "answer" | exception factory), ...] in call order
        self.starts = []             # (virtual seconds, model_name)
        self.record_keys = ()        # keys the stub says its call used (a real call's carousel does it itself)
        self.seen_at_second_ask = None


class ListModel:
    def __init__(self, provider, name, world):
        self.a0_model_conf = types.SimpleNamespace(provider=provider)
        self.model_name = f"{provider}/{name}"
        self.world = world

    async def unified_call(self, **kwargs):
        w = self.world
        w.starts.append((w.vt.elapsed(), self.model_name))
        hop = K._KAME_FALLBACK_HOP.get()
        for key in w.record_keys:
            hop["used"].add(key)
        asked = len([1 for _t, m in w.starts if m == self.model_name])
        if asked == 2:
            w.seen_at_second_ask = mh.HEALTH.summary(NEXT_ID)
        steps = w.plan.get(self.model_name) or [(0.01, "answer")]
        seconds, what = steps[min(asked, len(steps)) - 1]
        await asyncio.sleep(seconds)
        if what != "answer":
            raise what()
        callback = kwargs.get("response_callback")
        if callback is not None:
            await callback("x", "x")
        return (f"answer from {self.model_name} (ask {asked})", "")


@pytest.fixture
def world(vt, monkeypatch, tmp_path):
    monkeypatch.delenv("KAME_NO_REMEMBERED_TROUBLE", raising=False)
    mh.HEALTH.configure(directory=tmp_path, calls_path=tmp_path / "no-calls.jsonl")
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
    monkeypatch.setattr(K, "_KAME_SLOW_S", SLOW)
    monkeypatch.setattr(K, "_KAME_CROSS_COLD_S", 1.0)       # the next model joins a silent reply at 1 s
    monkeypatch.setattr(K, "_KAME_CROSS_STEP_S", 100.0)
    monkeypatch.setattr(K, "_KAME_CROSS_MIN_S", 0.5)
    K._KAME_BUSY_UNTIL.clear()
    K._KAME_BUSY_WINDOW.clear()
    K._KAME_PROBED_AT.clear()
    K._KJ.EVENTS.clear()
    K.set_fallback_models(NEXT)
    yield w
    mh.HEALTH.configure(directory=None)
    K._KAME_BUSY_UNTIL.clear()
    K._KAME_BUSY_WINDOW.clear()
    K._KAME_PROBED_AT.clear()


def main_model_thinks(seconds):
    async def attempt(owner, key, ctx):
        await asyncio.sleep(seconds)
        return ("main answer", "")
    return attempt


def asked(world, name=NEXT):
    return [t for t, model in world.starts if model == name]


def rows_saying(text):
    return [e for e in K._KJ.EVENTS.recent(200) if text in str(e.get("reason", ""))]


def pool(*names):
    K._get_identity_state(NEXT_ID, list(names))


# --- the shape of the owner's evening -------------------------------------------------------------------------

def test_a_raced_leg_that_503s_is_replaced_at_once_and_the_replacements_answer_wins(world):
    world.plan[NEXT] = [(0.5, overloaded), (0.05, "answer")]       # asked at 1 s, refused at 1.5 s
    ctx, _ = make_ctx(main_model_thinks(30.0))
    result = world.vt.run(K._kame_carousel(None, ctx), limit=120)
    assert result[0] == f"answer from {NEXT} (ask 2)"
    first, second = asked(world)
    assert first == pytest.approx(1.0, abs=0.1)
    assert second == pytest.approx(1.5, abs=0.1), "the replacement starts in the instant the leg fails"
    assert world.vt.elapsed() < 2.0
    assert rows_saying("another key of"), "the panel says a replacement was asked"


def test_the_failed_legs_verdict_is_applied_before_the_replacement_runs(world):
    world.plan[NEXT] = [(0.5, overloaded), (0.05, "answer")]
    ctx, _ = make_ctx(main_model_thinks(30.0))
    world.vt.run(K._kame_carousel(None, ctx), limit=120)
    assert world.seen_at_second_ask == (1, 1), world.seen_at_second_ask      # the refusal was noted at 1.5 s


def test_only_one_replacement_per_model_per_race(world):
    world.plan[NEXT] = [(0.5, overloaded), (0.5, overloaded), (0.5, overloaded)]
    ctx, _ = make_ctx(main_model_thinks(6.0))
    result = world.vt.run(K._kame_carousel(None, ctx), limit=120)
    assert result[0] == "main answer"
    assert len(asked(world)) == 2, world.starts          # the leg and its one replacement, never a third


@pytest.mark.parametrize("refusal", [gone, bad_request])
def test_no_replacement_when_the_request_itself_was_refused(world, refusal):
    world.plan[NEXT] = [(0.5, refusal), (0.05, "answer")]
    ctx, _ = make_ctx(main_model_thinks(6.0))
    result = world.vt.run(K._kame_carousel(None, ctx), limit=120)
    assert result[0] == "main answer"
    assert len(asked(world)) == 1, world.starts
    assert not rows_saying("another key of")


# --- the keys ---------------------------------------------------------------------------------------------------

def test_a_replacement_needs_another_key_of_that_model_that_is_ready(world):
    pool("k1", "k2")
    world.record_keys = ("k1",)                          # the failed leg's call used k1
    world.plan[NEXT] = [(0.5, overloaded), (0.05, "answer")]
    ctx, _ = make_ctx(main_model_thinks(30.0))
    result = world.vt.run(K._kame_carousel(None, ctx), limit=120)
    assert result[0] == f"answer from {NEXT} (ask 2)"


def test_a_one_key_pool_starts_nothing(world):
    pool("k1")
    world.record_keys = ("k1",)
    world.plan[NEXT] = [(0.5, overloaded), (0.05, "answer")]
    ctx, _ = make_ctx(main_model_thinks(6.0))
    result = world.vt.run(K._kame_carousel(None, ctx), limit=120)
    assert result[0] == "main answer"
    assert len(asked(world)) == 1
    assert not rows_saying("another key of")


def test_every_key_already_used_starts_nothing(world):
    pool("k1", "k2")
    world.record_keys = ("k1", "k2")
    world.plan[NEXT] = [(0.5, overloaded), (0.05, "answer")]
    ctx, _ = make_ctx(main_model_thinks(6.0))
    result = world.vt.run(K._kame_carousel(None, ctx), limit=120)
    assert result[0] == "main answer"
    assert len(asked(world)) == 1


def test_the_other_key_resting_starts_nothing(world):
    pool("k1", "k2")
    world.record_keys = ("k1",)
    K._KAME_KEY_HEALTH[NEXT_ID]["keys"]["k2"]["sick_until"] = world.vt.WALL0 + 10_000.0
    world.plan[NEXT] = [(0.5, overloaded), (0.05, "answer")]
    ctx, _ = make_ctx(main_model_thinks(6.0))
    result = world.vt.run(K._kame_carousel(None, ctx), limit=120)
    assert result[0] == "main answer"
    assert len(asked(world)) == 1


# --- what a replacement asks -----------------------------------------------------------------------------------

def test_the_replacement_avoids_the_keys_the_failed_leg_used(world):
    """The hop of the replacement carries the keys of that model already used; its call's pick skips them."""
    pool("k1", "k2")
    world.record_keys = ("k1",)
    seen = []

    class Spy(ListModel):
        async def unified_call(self, **kwargs):
            seen.append(sorted(K._KAME_FALLBACK_HOP.get()["avoid"]))
            return await super().unified_call(**kwargs)

    mod = sys.modules["models"]
    mod.get_chat_model = lambda provider, name, model_config=None, **kw: Spy(provider, name, world)
    world.plan[NEXT] = [(0.5, overloaded), (0.05, "answer")]
    ctx, _ = make_ctx(main_model_thinks(30.0))
    world.vt.run(K._kame_carousel(None, ctx), limit=120)
    assert seen == [[], ["k1"]], seen


def test_saver_and_the_off_list_race_nothing_so_nothing_is_replaced(world):
    K.set_hedge_mode("off")
    try:
        world.plan[NEXT] = [(0.5, overloaded), (0.05, "answer")]
        ctx, _ = make_ctx(main_model_thinks(3.0))
        result = world.vt.run(K._kame_carousel(None, ctx), limit=120)
        assert result[0] == "main answer"
        assert asked(world) == []
    finally:
        K.set_hedge_mode("adaptive")

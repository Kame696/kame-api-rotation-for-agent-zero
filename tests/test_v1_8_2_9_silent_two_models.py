"""1.8.2.9 (E3, decisions/0006) - no reply waits behind two silent models.

1.8.2.8 raced the next model of the list against a silent reply and, when the slow bound
passed, moved the bound to "the last raced model's start + the whole bound": a reply could
wait behind two silent models for two bounds, and at the end the race was cut even when no
other model could answer. Now each raced model keeps the reply waiting only to its own late
point (half its slow bound, 8 s at least), one more model of the list joins at the slow
bound, and nothing is cut when nothing else could answer.

A virtual clock (kame_vclock.py) makes the seconds exact. No Agent Zero installation.
"""
from __future__ import annotations

import asyncio
import sys
import types

import pytest

from test_v1_8_2_7_speed_fallback import (  # noqa: F401  (fixtures are used by name)
    ID, KEYS, clean, make_ctx, streaming,
)
from kame_vclock import vt  # noqa: F401
import kame_engine as K

SLOW = {False: (4.0, 4.0, 4.0), True: (4.0, 4.0, 4.0)}


class World:
    def __init__(self, vt):
        self.vt = vt
        self.delay = {}              # model_name -> seconds before it speaks
        self.starts = []             # (virtual seconds, model_name)
        self.cancelled = []
        self.active = 0
        self.max_active = 0


class SilentModel:
    def __init__(self, provider, name, world):
        self.a0_model_conf = types.SimpleNamespace(provider=provider)
        self.model_name = f"{provider}/{name}"
        self.world = world

    async def unified_call(self, **kwargs):
        w = self.world
        w.starts.append((w.vt.elapsed(), self.model_name))
        w.active += 1
        w.max_active = max(w.max_active, w.active)
        try:
            await asyncio.sleep(w.delay.get(self.model_name, 0.01))
            callback = kwargs.get("response_callback")
            if callback is not None:
                await callback("x", "x")
            return (f"answer from {self.model_name}", "")
        except asyncio.CancelledError:
            w.cancelled.append(self.model_name)
            raise
        finally:
            w.active -= 1


@pytest.fixture
def world(vt, monkeypatch):
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
    mod.get_chat_model = lambda provider, name, model_config=None, **kw: SilentModel(provider, name, w)
    monkeypatch.setitem(sys.modules, "models", mod)
    monkeypatch.setattr(K, "_KAME_SLOW_S", SLOW)
    monkeypatch.setattr(K, "_KAME_CROSS_COLD_S", 1.0)       # the next model joins a silent reply at 1 s
    monkeypatch.setattr(K, "_KAME_CROSS_STEP_S", 100.0)     # ... and the one after it much later
    monkeypatch.setattr(K, "_KAME_CROSS_MIN_S", 0.5)
    K._KAME_BUSY_UNTIL.clear()
    K._KAME_BUSY_WINDOW.clear()
    getattr(K, "_KAME_PROBED_AT", {}).clear()
    K._KJ.EVENTS.clear()
    yield w
    K._KAME_BUSY_UNTIL.clear()
    K._KAME_BUSY_WINDOW.clear()


def events():
    return [e["reason"] for e in K._KJ.EVENTS.recent(80)]


def test_the_primary_and_the_first_raced_model_silent_the_next_free_model_joins_at_the_slow_bound(world):
    K.set_fallback_models("google/gemini-3.7-flash\ngoogle/gemini-3.6-flash")
    world.delay["google/gemini-3.7-flash"] = 30.0           # the raced model is silent too
    log = []
    ctx, _ = make_ctx(streaming({k: 30.0 for k in KEYS}, log=log))
    result = world.vt.run(K._kame_carousel(None, ctx), limit=60)
    assert result[0] == "answer from google/gemini-3.6-flash"
    assert ctx.get("answered_by") == "google:gemini-3.6-flash"
    # 1.8.2.8 moved the bound to (last raced start + the whole bound) = 5 s and cut there
    assert world.vt.elapsed() < 4.5
    joined = [t for t, name in world.starts if name == "google/gemini-3.6-flash"]
    assert joined and 3.9 <= joined[0] <= 4.2               # at the slow bound
    assert any("joins" in text for text in events())


def test_when_no_other_model_could_answer_nothing_is_cut_and_the_reply_arrives(world):
    K.set_fallback_models("google/gemini-3.7-flash")
    world.delay["google/gemini-3.7-flash"] = 8.0            # silent, but alive
    log = []
    ctx, _ = make_ctx(streaming({k: 7.0 for k in KEYS}, log=log))
    result = world.vt.run(K._kame_carousel(None, ctx), limit=60)
    assert result[0].startswith("answer from ") and not result[0].endswith("flash")
    assert 6.5 <= world.vt.elapsed() <= 7.6
    assert not any(entry.startswith("cancelled:") for entry in log)     # the first model was never cut
    assert ID not in K._KAME_BUSY_UNTIL                                 # and not left alone as slow
    assert not any("slow right now" in text for text in events())


def test_a_model_keeps_the_reply_waiting_only_to_its_own_late_point():
    assert K._kame_late_point(10.0) == 8.0                  # half the bound is 5 s: the 8 s floor
    assert K._kame_late_point(40.0) == 20.0                 # half the bound
    assert K._kame_late_point(0.0) == 8.0
    assert K._KAME_WIDEN_MAX == 1


@pytest.mark.parametrize("floor, joins_at", [
    (0.5, 4.0),     # late point 1 + 2.0 is before the primary's bound: the bound stays
    (3.5, 4.5),     # the raced model's own late point (1 + 3.5) moves the bound a little
    (20.0, 5.0),    # never later than 1.8.2.8 (the raced start + the whole bound)
])
def test_the_extended_bound_is_the_raced_models_own_late_point_never_the_old_whole_bound(
        world, monkeypatch, floor, joins_at):
    monkeypatch.setattr(K, "_KAME_CROSS_MIN_S", floor)
    K.set_fallback_models("google/gemini-3.7-flash\ngoogle/gemini-3.6-flash")
    world.delay["google/gemini-3.7-flash"] = 30.0
    ctx, _ = make_ctx(streaming({k: 30.0 for k in KEYS}))
    result = world.vt.run(K._kame_carousel(None, ctx), limit=60)
    assert result[0] == "answer from google/gemini-3.6-flash"
    joined = [t for t, name in world.starts if name == "google/gemini-3.6-flash"][0]
    assert joins_at - 0.1 <= joined <= joins_at + 0.2, joined


def test_never_more_than_three_models_raced_for_one_reply(world, monkeypatch):
    monkeypatch.setattr(K, "_KAME_CROSS_STEP_S", 0.3)       # two list models join at 1.0 and 1.5 s
    K.set_fallback_models("p1/a\np2/b\np3/c\np4/d\np5/e")
    for name in ("p1/a", "p2/b", "p3/c", "p4/d", "p5/e"):
        world.delay[name] = 30.0
    ctx, _ = make_ctx(streaming({k: 30.0 for k in KEYS}))
    world.vt.run(K._kame_carousel(None, ctx), limit=80)
    raced = [name for t, name in world.starts if t <= 4.5]
    assert len(raced) <= 3, raced
    assert world.max_active <= 3
    assert len(raced) == 3                                  # two by the usual rule, one at the slow bound


def test_a_first_word_before_the_bound_widens_and_cuts_nothing(world):
    K.set_fallback_models("google/gemini-3.7-flash\ngoogle/gemini-3.6-flash")
    world.delay["google/gemini-3.7-flash"] = 30.0
    log = []
    ctx, _ = make_ctx(streaming({k: 2.0 for k in KEYS}, log=log))
    result = world.vt.run(K._kame_carousel(None, ctx), limit=60)
    assert result[0].startswith("answer from ") and not result[0].endswith("flash")
    assert [name for t, name in world.starts if t > 2.5] == []
    assert not any("joins" in text for text in events())
    assert ID not in K._KAME_BUSY_UNTIL

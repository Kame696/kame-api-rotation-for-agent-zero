"""1.8.4.2 (L1) - a model whose race leg was refused BUSY is not handed the same reply (Agent Zero).

The gap (live on Hermes, 13:34): the reply's own model silent then 503, the next model raced beside it answered 503
too, and the hand-over that followed went straight back to the model that had just refused. A raced list model's
failure is its own (1.8.2.8 D12: never judged on this pool's keys), so nothing told the hand-over. Now a leg of a
list model that ended refused busy (the provider's 5xx, or its own carousel's last busy refusal) goes on the
reply's ``leg_busy`` list; ``_kame_ladder_after`` (the hand-over, the race of the next model, the widen) skips it for
THIS reply only. The model gets one failure in the remembered record. A 429 is not busy and stays a candidate.

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
THIRD = "google/gemini-3.6-flash"
THIRD_ID = "google:gemini-3.6-flash"
SLOW = {False: (4.0, 4.0, 4.0), True: (4.0, 4.0, 4.0)}


def overloaded():
    return Refusal("Error code: 503", 503,
                   {"error": {"code": 503, "message": "The model is overloaded. Please try again later.",
                              "status": "UNAVAILABLE"}})


def throttled():
    return Refusal("Error code: 429", 429,
                   {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED",
                              "message": "Quota exceeded for metric requests per minute. Please retry in 20s"}})


class World:
    def __init__(self, vt):
        self.vt = vt
        self.behaviour = {}          # model_name -> (seconds, "answer" | "503" | "429")
        self.starts = []             # (virtual seconds, model_name)


class ListModel:
    def __init__(self, provider, name, world):
        self.a0_model_conf = types.SimpleNamespace(provider=provider)
        self.model_name = f"{provider}/{name}"
        self.world = world

    async def unified_call(self, **kwargs):
        w = self.world
        w.starts.append((w.vt.elapsed(), self.model_name))
        seconds, what = w.behaviour.get(self.model_name, (0.01, "answer"))
        await asyncio.sleep(seconds)
        if what == "503":
            raise overloaded()
        if what == "429":
            raise throttled()
        callback = kwargs.get("response_callback")
        if callback is not None:
            await callback("x", "x")
        return (f"answer from {self.model_name}", "")


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
    yield w
    mh.HEALTH.configure(directory=None)
    K._KAME_BUSY_UNTIL.clear()
    K._KAME_BUSY_WINDOW.clear()
    K._KAME_PROBED_AT.clear()


def fails_after(seconds):
    async def attempt(owner, key, ctx):
        await asyncio.sleep(seconds)
        raise overloaded()
    return attempt


def asked(world, name):
    return [t for t, model in world.starts if model == name]


# --- the 13:34 shape ---------------------------------------------------------------------------------------

def test_the_model_that_refused_busy_beside_the_reply_is_not_handed_the_same_reply(world):
    K.set_fallback_models(f"{NEXT}\n{THIRD}")
    world.behaviour[NEXT] = (0.5, "503")                     # raced at 1 s, refused at 1.5 s
    ctx, _ = make_ctx(fails_after(3.0))                      # the reply's own model: silent, then 503
    result = world.vt.run(K._kame_carousel(None, ctx), limit=60)
    assert result[0] == f"answer from {THIRD}"
    assert len(asked(world, NEXT)) == 1, world.starts        # asked once, beside the reply - never again
    assert ctx.get("leg_busy") == [NEXT_ID]
    assert mh.HEALTH.summary(NEXT_ID) == (1, 1)              # one failure in the record, for one reply


def test_a_429_on_the_raced_leg_is_not_busy_and_the_model_stays_a_candidate(world):
    K.set_fallback_models(f"{NEXT}\n{THIRD}")
    world.behaviour[NEXT] = (0.5, "429")
    ctx, _ = make_ctx(fails_after(3.0))
    world.vt.run(K._kame_carousel(None, ctx), limit=60)
    assert not ctx.get("leg_busy")
    assert NEXT_ID in [e["identity"] for e in K._kame_ladder_after(ctx)]
    assert mh.HEALTH.summary(NEXT_ID) == (0, 0)


def test_the_avoid_list_is_this_replys_only(world):
    K.set_fallback_models(f"{NEXT}\n{THIRD}")
    world.behaviour[NEXT] = (0.5, "503")
    ctx, _ = make_ctx(fails_after(3.0))
    world.vt.run(K._kame_carousel(None, ctx), limit=60)
    other, _ = make_ctx(streaming({}))
    assert [e["identity"] for e in K._kame_ladder_after(other)] == [NEXT_ID, THIRD_ID]
    assert NEXT_ID not in [e["identity"] for e in K._kame_ladder_after(ctx)]


def test_with_nothing_else_in_the_list_the_ladder_is_empty_as_today(world):
    K.set_fallback_models(NEXT)
    ctx, _ = make_ctx(streaming({}))
    ctx["leg_busy"] = [NEXT_ID]
    assert K._kame_ladder_after(ctx) == []


# --- the classification, unit by unit ---------------------------------------------------------------------

def finished(vt, exc, entry_identity=NEXT_ID):
    """A race with one list-model leg that ended with ``exc``."""
    future = vt.loop.create_future()
    future.set_exception(exc)
    future.exception()                                       # retrieved: no "never retrieved" noise
    race = types.SimpleNamespace(tasks={1: future}, cross_legs={1: {"identity": entry_identity}})
    ctx, _ = make_ctx(streaming({}))
    ctx["kame_call_id"] = "c1"
    return ctx, race


def test_a_leg_that_ended_with_its_own_carousels_last_busy_refusal_counts(vt, world):
    ctx, race = finished(vt, K._KameSwitchModel("no answer in time on this fallback model", overloaded()))
    K._kame_note_leg_busy(ctx, race, 1)
    assert ctx["leg_busy"] == [NEXT_ID]


def test_a_leg_that_stopped_for_a_reserve_key_is_not_busy(vt, world):
    ctx, race = finished(vt, K._KameSwitchModel("race leg: only a reserve key is free", None))
    K._kame_note_leg_busy(ctx, race, 1)
    assert not ctx.get("leg_busy") and mh.HEALTH.summary(NEXT_ID) == (0, 0)


def test_a_bad_request_is_not_busy(vt, world):
    ctx, race = finished(vt, Refusal("Error code: 400", 400, {"error": {"message": "bad request"}}))
    K._kame_note_leg_busy(ctx, race, 1)
    assert not ctx.get("leg_busy")


def test_a_model_whose_own_carousel_already_opened_its_window_is_not_counted_twice(vt, world):
    K._kame_note_busy(NEXT_ID, call_id="inner-call")         # the leg's own carousel noted it (a failure)
    assert mh.HEALTH.summary(NEXT_ID) == (1, 1)
    ctx, race = finished(vt, K._KameSwitchModel("busy", overloaded()))
    K._kame_note_leg_busy(ctx, race, 1)
    assert ctx["leg_busy"] == [NEXT_ID]                      # still kept off this reply ...
    assert mh.HEALTH.summary(NEXT_ID) == (1, 1)              # ... and counted once


def test_the_same_model_twice_is_listed_once(vt, world):
    ctx, race = finished(vt, overloaded())
    K._kame_note_leg_busy(ctx, race, 1)
    K._kame_note_leg_busy(ctx, race, 1)
    assert ctx["leg_busy"] == [NEXT_ID]

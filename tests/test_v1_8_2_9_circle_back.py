"""1.8.2.9 (E1, decisions/0005) - circle back to the best model instead of hammering it.

Until 1.8.2.8 a busy best model (503 on every key) was asked once per key round trip, many
times a minute, while the list was walked again only every 30 s. Now a busy model - the
best one or a list model - is asked at most once per 20 s across every conversation, best
first, and the wait between two asks is spent on the status line saying so.

The list models here are real KAME calls (their own carousel, their own keys) so the walk,
the hop and the probe flag are exercised end to end, on a virtual clock (kame_vclock.py).
No Agent Zero installation, no network.
"""
from __future__ import annotations

import asyncio
import random
import sys
import types

import pytest

from test_v1_8_2_7_speed_fallback import (  # noqa: F401  (fixtures are used by name)
    ID, KEYS, Refusal, clean, make_ctx, streaming,
)
from kame_vclock import vt  # noqa: F401
import kame_engine as K

E1 = "google/gemini-3.7-flash"
E2 = "google/gemini-3.6-flash"
PRIMARY = "primary"
PKEYS = KEYS[:4]


def overloaded():
    return Refusal("Error code: 503", 503,
                   {"error": {"code": 503, "message": "The model is overloaded. Please try again later.",
                              "status": "UNAVAILABLE"}})


class World:
    """Three models. Each answers 503 until the second in ``healthy_from``."""

    def __init__(self, vt):
        self.vt = vt
        self.log = []                                   # (virtual seconds, model, key)
        self.healthy_from = {}
        self.keys = {E1: [f"E1KEY-{c * 10}" for c in "abcd"], E2: [f"E2KEY-{c * 10}" for c in "abcd"]}

    def attempt(self, model):
        async def attempt(owner, key, ctx):
            self.log.append((self.vt.elapsed(), model, key))
            await asyncio.sleep(0.01)
            if self.vt.elapsed() < self.healthy_from.get(model, float("inf")):
                raise overloaded()
            await ctx["response_callback"]("ok", "ok")
            return (f"answer from {model}", "")
        return attempt

    def asked(self, model, after=0.0, before=1e9):
        return [t for t, m, _k in self.log if m == model and after < t <= before]


class ListModel:
    """A list model that runs KAME's own carousel for its own keys, as the real wrapper does."""

    def __init__(self, provider, name, world):
        self.a0_model_conf = types.SimpleNamespace(provider=provider)
        self.model_name = f"{provider}/{name}"
        self.world = world

    async def unified_call(self, **kwargs):
        ident = K._kame_identity_of(self)
        keys = self.world.keys[self.model_name]
        K._get_identity_state(ident, keys)
        ctx = {"identity": ident, "all_keys": list(keys), "call_type": "Chat", "model_short": "m",
               "attempt": self.world.attempt(self.model_name),
               "progress": {"any": False, "reasoning": False},
               "messages": list(kwargs.get("messages") or []), "kwargs": {}, "entry_name": "unified_call",
               "rate_limiter_callback": None}
        (ctx["response_callback"], ctx["reasoning_callback"],
         ctx["tokens_callback"]) = K._kame_wrap_callbacks(ctx, kwargs.get("response_callback"), None, None)
        return await K._kame_carousel(self, ctx)


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
    mod.get_chat_model = lambda provider, name, model_config=None, **kw: ListModel(provider, name, w)
    monkeypatch.setitem(sys.modules, "models", mod)
    monkeypatch.setattr(K, "_get_all_api_keys",
                        lambda model: list(w.keys.get(getattr(model, "model_name", ""), [])))
    K.set_fallback_models(f"{E1}\n{E2}")
    return w


@pytest.fixture(autouse=True)
def calm():
    for table in (K._KAME_BUSY_UNTIL, K._KAME_BUSY_WINDOW, getattr(K, "_KAME_PROBED_AT", {})):
        table.clear()
    K._KAME_REPLIES.clear()
    K._KAME_TURNS.clear()
    yield
    for table in (K._KAME_BUSY_UNTIL, K._KAME_BUSY_WINDOW, getattr(K, "_KAME_PROBED_AT", {})):
        table.clear()


def primary_ctx(world, **kw):
    return make_ctx(world.attempt(PRIMARY), keys=PKEYS, **kw)[0]


def drive(world, ctx, limit):
    return world.vt.run(K._kame_carousel(None, ctx), limit=limit)


# --- the cadence -------------------------------------------------------------------

def test_a_busy_best_model_is_asked_on_a_clock_not_once_per_key_round_trip(world):
    # Everything answers 503. After the two requests that prove a model busy, each model is
    # asked at most once per 20 s: in a minute, three more asks at the very most (1.8.2.8:
    # dozens on the best model, none on the list for 30 s at a time).
    ctx = primary_ctx(world)
    with pytest.raises(TimeoutError):
        drive(world, ctx, limit=60)
    for model in (PRIMARY, E1, E2):
        proof = world.asked(model, before=1.0)
        later = world.asked(model, after=1.0)
        assert 1 <= len(proof) <= 2, (model, proof)
        assert 1 <= len(later) <= 3, (model, later)
        assert all(b - a >= 19.0 for a, b in zip(later, later[1:])), (model, later)
        assert len(proof) + len(later) <= 5


def test_a_list_model_that_recovered_answers_at_the_next_ask_not_thirty_seconds_later(world):
    world.healthy_from[E1] = 15.0
    ctx = primary_ctx(world)
    result = drive(world, ctx, limit=120)
    assert result[0] == f"answer from {E1}"
    assert world.vt.elapsed() < 22.0               # 1.8.2.8: the list was not looked at again before 30 s
    assert world.asked(PRIMARY, after=world.asked(E1)[-1]) == []   # no primary request after the answer


def test_a_list_model_back_at_25_seconds_answers_by_the_second_ask(world):
    world.healthy_from[E1] = 25.0
    ctx = primary_ctx(world)
    result = drive(world, ctx, limit=120)
    assert result[0] == f"answer from {E1}"
    assert world.vt.elapsed() <= 41.0
    assert world.asked(PRIMARY, after=world.asked(E1)[-1]) == []     # nothing asks the best model after the answer


def test_when_the_best_and_the_next_model_are_both_due_the_best_is_asked_first(world):
    ctx = primary_ctx(world)
    with pytest.raises(TimeoutError):
        drive(world, ctx, limit=30)
    asked_primary = world.asked(PRIMARY, after=1.0)
    asked_e1 = world.asked(E1, after=1.0)
    assert asked_primary and asked_e1
    assert asked_primary[0] <= asked_e1[0]
    assert asked_e1[0] - asked_primary[0] < 1.0     # one walk: the best, then the list at once


def test_two_conversations_on_the_same_busy_model_ask_it_once_per_twenty_seconds_together(world):
    ctx_a, ctx_b = primary_ctx(world), primary_ctx(world)

    async def both():
        return await asyncio.gather(K._kame_carousel(None, ctx_a), K._kame_carousel(None, ctx_b),
                                    return_exceptions=True)

    with pytest.raises(TimeoutError):
        world.vt.run(both(), limit=70)
    asks = world.asked(PRIMARY, after=1.5)
    assert 2 <= len(asks) <= 4, asks
    assert all(b - a >= 19.0 for a, b in zip(asks, asks[1:])), asks


def test_the_wait_between_two_asks_is_said_on_the_status_line(world, monkeypatch):
    said = []
    # 1.8.3.1 (D2): the wait between two asks is the live line's "waiting" state, in the shared words
    monkeypatch.setattr(K, "_kame_status_put", lambda ctx, text, st=None, key=None: said.append(text))
    ctx = primary_ctx(world)
    with pytest.raises(TimeoutError):
        drive(world, ctx, limit=45)
    waiting = [t for t in said if t and "next key in" in t]
    assert waiting, said
    assert all(t.startswith("⏳ Every key of ") for t in waiting)
    assert said[-1] is None or "next key in" in said[-1]      # cleared when the wait ends


# --- guards: 1.8.2.8 stays where the SPEC says "unchanged" ----------------------------

def test_never_keeps_rotating_the_best_models_own_keys_exactly_as_before(world):
    random.seed(7)
    K.set_model_switch("never")
    ctx = primary_ctx(world)
    with pytest.raises(TimeoutError):
        drive(world, ctx, limit=3)
    assert world.asked(E1) == [] and world.asked(E2) == []
    assert len(world.asked(PRIMARY)) == BASELINE_NEVER_REQUESTS_IN_3_SECONDS


def test_a_background_call_never_walks_the_list_while_the_best_model_is_busy(world, monkeypatch):
    # review L1: the list models are FREE here (healthy, not busy), so only the call's own class keeps
    # the list unasked. Without the budget (1.8.2.8) the background call waits on its own model.
    world.healthy_from[E1] = world.healthy_from[E2] = 0.0
    monkeypatch.setattr(K, "_KAME_AUX_UNBOUNDED", True)
    ctx = primary_ctx(world, call_type="Util")
    with pytest.raises(TimeoutError):
        drive(world, ctx, limit=120)
    assert world.asked(E1) == [] and world.asked(E2) == []
    assert len(world.asked(PRIMARY)) > 3


async def summarize_messages(ctx):                  # a frame Agent Zero's compression is named from
    return await K._kame_carousel(None, ctx)


def test_a_budgeted_background_call_asks_the_list_only_in_its_two_walks(world):
    # review L1: the guard accepts only the budget's own stop (not any TimeoutError). The list is
    # walked at most twice - once when the best model is proven busy, once at the stop - and never
    # in between, beside the best model, as the conversation's circle does.
    ctx = primary_ctx(world, call_type="Util")
    with pytest.raises(K.KameAuxTimeout):
        world.vt.run(summarize_messages(ctx), limit=600)
    for model in (E1, E2):
        asks = world.asked(model)
        assert 1 <= len(asks) <= 4, (model, asks)
        assert [t for t in asks if 30.0 < t < world.vt.elapsed() - 30.0] == [], (model, asks)


def test_text_already_on_the_screen_never_circles_and_never_leaves_the_model(world, monkeypatch):
    said = []
    monkeypatch.setattr(K, "_kame_status_put", lambda ctx, text, st=None, key=None: said.append(text), raising=False)

    def attempt_after_text():
        async def attempt(owner, key, ctx):
            world.log.append((world.vt.elapsed(), PRIMARY, key))
            await ctx["response_callback"]("partial", "partial")
            await asyncio.sleep(0.01)
            raise overloaded()
        return attempt

    ctx = make_ctx(attempt_after_text(), keys=PKEYS)[0]
    with pytest.raises(TimeoutError):
        drive(world, ctx, limit=3)
    assert world.asked(E1) == [] and world.asked(E2) == []
    # 1.8.3.1 (D2): the only wait said is the keys' own short rest (seconds), never a circle wait (20 s and more)
    assert not [t for t in said if t and "next key in" in t and "m " in t.split("next key in")[1][:6]]
    assert not [t for t in said if t and "next key in" in t
                and int("".join(c for c in t.split("next key in")[1].split("s")[0] if c.isdigit()) or 0) > 5]
    assert len(world.asked(PRIMARY)) >= 3          # it keeps rotating the keys it has (1.8.2.8: 3 in 3 s)


BASELINE_NEVER_REQUESTS_IN_3_SECONDS = 7        # measured on the 1.8.2.8 code (seed 7) before it was changed

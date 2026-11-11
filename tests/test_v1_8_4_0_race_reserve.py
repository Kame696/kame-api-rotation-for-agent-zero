"""1.8.4.0 round 2 (R1) - a reserve key is never a race leg on another model either.

E4 says a reserve key is never used for a backup request. The same-model backups and the keys asked
together filter it (``_kame_spend_first``). The legs that ask ANOTHER model of the list - the
cross-model race (1.8.2.8), the widened race (1.8.2.9) and the start probe (1.8.2.9, E8 in the tail)
- run a whole KAME call on that model, whose own pick offers a reserve key when no other key of that
model is ready. A race leg is a backup request, so it does not start then. Every key reserved still
reads the setting as not set. Same rule as Hermes ``tests/test_1_8_4_0_race_reserve.py``.

A virtual clock (kame_vclock.py). No Agent Zero installation, no network.
"""
from __future__ import annotations

import asyncio
import time

import pytest

from test_v1_8_2_7_speed_fallback import (  # noqa: F401  (fixtures are used by name)
    FALLBACK_BEHAVIOUR, ID, KEYS, clean, fake_models, make_ctx, streaming,
)
from test_v1_8_4_0_storm_tail import NEXT, calm, ended_ago, requests  # noqa: F401
from kame_vclock import vt  # noqa: F401
import kame_engine as K
import kame_pacing as P


def short(key):
    return K._key_short_id(key)


@pytest.fixture(autouse=True)
def no_reserve(clean):
    K.set_key_reserve("")
    P.PACER.clear()
    yield
    K.set_key_reserve("")
    P.PACER.clear()


def only_reserve_left(identity, reserve_key, keys=KEYS):
    K._get_identity_state(identity, keys)
    pool = K._KAME_KEY_HEALTH[identity]["keys"]
    for k in keys:
        if k != reserve_key:
            pool[k]["sick_until"] = time.time() + 600


# --- the rule, as a question -------------------------------------------------------------------------

def test_no_reserve_set_is_always_ready():
    only_reserve_left(ID, KEYS[0])
    assert K._kame_race_ready(ID) is True


def test_only_the_reserve_key_free_is_not_ready():
    only_reserve_left(ID, KEYS[0])
    K.set_key_reserve(short(KEYS[0]))
    assert K._kame_race_ready(ID) is False


def test_an_ordinary_key_free_is_ready():
    K._get_identity_state(ID, KEYS)
    K.set_key_reserve(short(KEYS[0]))
    assert K._kame_race_ready(ID) is True


def test_a_retired_ordinary_key_does_not_count():
    only_reserve_left(ID, KEYS[0])
    pool = K._KAME_KEY_HEALTH[ID]["keys"]
    pool[KEYS[1]]["sick_until"] = 0.0
    pool[KEYS[1]]["retired_at"] = time.time()
    K.set_key_reserve(short(KEYS[0]))
    assert K._kame_race_ready(ID) is False


def test_every_key_reserved_reads_the_setting_as_not_set():
    only_reserve_left(ID, KEYS[0])
    K.set_key_reserve(", ".join(short(k) for k in KEYS))
    assert K._kame_race_ready(ID) is True


def test_a_pool_kame_has_not_seen_is_ready():
    K.set_key_reserve(short(KEYS[0]))
    assert K._kame_race_ready("google:gemini/never-seen") is True


# --- where it is asked ----------------------------------------------------------------------------------

def test_no_start_probe_when_only_a_reserve_key_of_the_busy_model_is_free(fake_models, vt):
    K.set_fallback_models(NEXT)
    ended_ago()
    ctx, _ = make_ctx(streaming({k: 0.1 for k in KEYS}))
    only_reserve_left(ID, KEYS[0])
    K.set_key_reserve(short(KEYS[0]))
    assert K._kame_start_probe_allowed(ctx, ID) is False


def test_the_start_probe_still_runs_when_an_ordinary_key_is_free(fake_models, vt):
    K.set_fallback_models(NEXT)
    ended_ago()
    ctx, _ = make_ctx(streaming({k: 0.1 for k in KEYS}))
    K.set_key_reserve(short(KEYS[0]))
    assert K._kame_start_probe_allowed(ctx, ID) is True


def test_a_widen_past_the_slow_bound_joins_on_the_reserve_key_and_no_leg_is_cut(fake_models, vt, monkeypatch):
    """Round 2 review F1: R1 stays on the cross leg and the start probe; the widen joins as it did
    before R1. A widen R1 turned down fell through to the hand-over, which cut every running leg."""
    K.set_fallback_models(NEXT)
    ctx, _ = make_ctx(streaming({k: 400.0 for k in KEYS}))         # the best model is silent
    entry = K._kame_ladder_after(ctx, ID)[0]
    seen = []
    real = K._kame_race_ready

    def spy(identity):
        seen.append(identity)
        return False if identity == entry["identity"] else real(identity)

    monkeypatch.setattr(K, "_kame_race_ready", spy)
    K._KJ.EVENTS.clear()
    t0 = vt.elapsed()
    result = vt.run(K._kame_carousel(None, ctx), limit=600)
    assert entry["identity"] in seen, "the cross launch still asked the question before it started a leg"
    # no leg at the race's time (12 s cold); the widen joins at the slow bound
    assert result[0] == f"fallback answer from {NEXT}"
    assert vt.elapsed() - t0 >= 20.0, vt.elapsed() - t0
    reasons = [e["reason"] for e in K._KJ.EVENTS.recent(80)]
    assert any("one more model of your list" in text and "joins" in text for text in reasons), reasons


# --- round 2 review F2: a cross leg is a whole carousel on that model; it never rotates onto a reserve key ----

def _in_hop(hop, coro_factory):
    async def inner():
        token = K._KAME_FALLBACK_HOP.set(hop)
        try:
            return await coro_factory()
        finally:
            K._KAME_FALLBACK_HOP.reset(token)
    return inner()


def _two_key_ctx(log):
    """Two keys, the first one reserved. The ordinary key answers 503; the reserve key would answer."""
    keys = KEYS[:2]
    K.set_key_reserve(short(keys[0]))
    ctx, _ = make_ctx(streaming({}, fail={keys[1]: overloaded}, log=log), keys=keys)
    return ctx, keys


def overloaded():
    from test_v1_8_2_7_speed_fallback import Refusal
    return Refusal("Error code: 503", 503,
                   {"error": {"code": 503, "message": "The model is overloaded. Please try again later.",
                              "status": "UNAVAILABLE"}})


def test_a_cross_leg_whose_only_ordinary_key_answers_503_ends_without_asking_the_reserve(fake_models, vt):
    log = []
    ctx, keys = _two_key_ctx(log)
    hop = {"origin": "someone", "to": ID, "probe": False, "race": True}
    with pytest.raises(K._KameSwitchModel):
        vt.run(_in_hop(hop, lambda: K._kame_carousel(None, ctx)), limit=120)
    assert keys[0] not in log, "the race leg never asked the reserve key"
    assert keys[1] in log


def test_the_reply_itself_may_still_take_the_reserve_key(fake_models, vt):
    log = []
    ctx, keys = _two_key_ctx(log)
    result = vt.run(K._kame_carousel(None, ctx), limit=120)
    assert result[0] == f"answer from {keys[0][-4:]}", result
    assert keys[0] in log


def test_a_leg_that_is_not_a_race_leg_may_take_the_reserve_key(fake_models, vt):
    log = []
    ctx, keys = _two_key_ctx(log)
    hop = {"origin": "someone", "to": ID, "probe": False, "race": False}     # a widened leg or a hand-over
    result = vt.run(_in_hop(hop, lambda: K._kame_carousel(None, ctx)), limit=120)
    assert result[0] == f"answer from {keys[0][-4:]}", result
    assert keys[0] in log


def test_a_race_leg_on_a_pool_that_is_all_reserve_reads_the_setting_as_not_set(fake_models, vt):
    log = []
    keys = KEYS[:2]
    K.set_key_reserve(", ".join(short(k) for k in keys))
    ctx, _ = make_ctx(streaming({}, log=log), keys=keys)
    hop = {"origin": "someone", "to": ID, "probe": False, "race": True}
    result = vt.run(_in_hop(hop, lambda: K._kame_carousel(None, ctx)), limit=120)
    assert result[0].startswith("answer from")


def test_the_race_flag_is_true_for_the_cross_leg_and_false_for_the_widen(fake_models, vt, monkeypatch):
    import test_v1_8_2_7_speed_fallback as SF
    hops = []
    real = SF.FakeModel.unified_call

    async def recording(self, **kwargs):
        hops.append((self.model_name, dict(K._KAME_FALLBACK_HOP.get() or {})))
        if self.model_name == "google/gemini-3.7-flash":
            await asyncio.sleep(400.0)           # the raced model is silent too
        return await real(self, **kwargs)

    monkeypatch.setattr(SF.FakeModel, "unified_call", recording)
    monkeypatch.setattr(K, "_KAME_SLOW_S", {False: (4.0, 4.0, 4.0), True: (4.0, 4.0, 4.0)})
    monkeypatch.setattr(K, "_KAME_CROSS_COLD_S", 1.0)
    monkeypatch.setattr(K, "_KAME_CROSS_STEP_S", 100.0)
    monkeypatch.setattr(K, "_KAME_CROSS_MIN_S", 0.5)
    K.set_fallback_models("google/gemini-3.7-flash\ngoogle/gemini-3.6-flash")
    ctx, _ = make_ctx(streaming({k: 400.0 for k in KEYS}))
    result = vt.run(K._kame_carousel(None, ctx), limit=120)
    assert result[0] == "fallback answer from google/gemini-3.6-flash"
    flags = {name: hop.get("race") for name, hop in hops}
    assert flags == {"google/gemini-3.7-flash": True, "google/gemini-3.6-flash": False}, flags

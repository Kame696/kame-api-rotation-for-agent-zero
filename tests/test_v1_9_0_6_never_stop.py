"""1.9.0.6 (Agent Zero decisions/0019 A3) - KAME never stops a reply by default.

With the defaults, in every storm the provider can throw (busy, throttled with or without a return time, silent
past the slow bound, dropped connections), on a one-provider list and on a mixed list, the call wrapper lets
nothing out to Agent Zero and the reply is answered when a model comes back. A 2 h wait with every key resting
goes on waiting and keeps the chat line. What the user sets (`max_total_wait_seconds`) still ends it, with the
provider's words.

Agent Zero's own timeouts (LiteLLM `timeout`, the agent loop) are not in this repo's host baselines
(`tests/host_baselines/*.json` hold fingerprints of Agent Zero's source, not its settings), so none is modelled
here: the fake provider answers or refuses on its own clock.

A virtual clock (kame_vclock.py). No Agent Zero installation, no network.
"""
from __future__ import annotations

import asyncio
import sys
import time
import types

import pytest

from test_v1_8_2_7_speed_fallback import (  # noqa: F401  (fixtures are used by name)
    ID, KEYS, Refusal, clean, make_ctx,
)
from test_v1_8_4_3_quiet_guard import NEXT, NEXT_ID, THIRD, THIRD_ID, overloaded  # noqa: F401
from kame_vclock import vt  # noqa: F401
import kame_engine as K
import kame_hedging as H

ONE_PROVIDER = f"{NEXT}\n{THIRD}"
MIXED = "openai/gpt-5.1\nanthropic/claude-sonnet-5"
MIXED_IDS = ["openai:gpt-5.1", "anthropic:claude-sonnet-5"]
STORM_S = 60.0
HOST_TIMEOUT_S = 30.0        # stand-in for Agent Zero's own timeout on a silent connection (not in this repo)
TWO_HOURS = 7200.0


def throttled_with_time():
    return Refusal("Error code: 429", 429,
                   {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED",
                              "message": "Quota exceeded for metric requests per minute. Please retry in 20s"}})


def throttled_without_time():
    return Refusal("Error code: 429", 429, {"error": {"message": "Too many requests. Slow down."}})


def dropped():
    return ConnectionError("Connection reset by peer")


STORMS = {
    "every model busy (503)": overloaded,
    "every key throttled, return time given": throttled_with_time,
    "every key throttled, no return time": throttled_without_time,
    "connection drops": dropped,
    "silent past the slow bound": "silent",
}


class Provider:
    """Every request of every model: fails in the storm's way until ``back_at`` (virtual s), then answers."""

    def __init__(self, vt):
        self.vt = vt
        self.fail = overloaded
        self.back_at = {}            # who -> virtual second it answers from; missing = never down
        self.requests = []           # (virtual s, who)

    async def serve(self, who, ctx):
        self.requests.append((self.vt.elapsed(), who))
        if self.vt.elapsed() < self.back_at.get(who, 0.0):
            if self.fail == "silent":
                # the connection is open and says nothing. KAME trusts the connection; what ends a connection that
                # stays silent is the host's own timeout (LiteLLM `timeout`), which this repo does not hold (see the
                # module docstring), so a stand-in: HOST_TIMEOUT_S, then the host's timeout error. When the provider
                # is back before that, the silent request is answered.
                left = self.back_at[who] - self.vt.elapsed()
                if left <= HOST_TIMEOUT_S:
                    await asyncio.sleep(left)
                else:
                    await asyncio.sleep(HOST_TIMEOUT_S)
                    raise TimeoutError("host timeout: the connection stayed silent")
            else:
                await asyncio.sleep(0.05)
                raise self.fail()
        await asyncio.sleep(0.05)
        callback = ctx.get("response_callback")
        if callback is not None:
            await callback(f"words of {who}", f"words of {who}")
        return (f"answer from {who}", "")

    async def home(self, owner, key, ctx):
        return await self.serve("home", ctx)


class Model:
    def __init__(self, provider, name, prov):
        self.a0_model_conf = types.SimpleNamespace(provider=provider)
        self.model_name = f"{provider}/{name}"
        self.prov = prov

    async def unified_call(self, **kwargs):
        return await self.prov.serve(self.model_name, kwargs)


@pytest.fixture
def prov(vt, monkeypatch, tmp_path):
    monkeypatch.setenv("KAME_DATA_DIR", str(tmp_path))
    p = Provider(vt)
    mod = types.ModuleType("models")

    class ModelType:
        CHAT = "Chat"

    class ModelConfig:
        def __init__(self, **kw):
            self.__dict__.update(kw)

        def build_kwargs(self):
            return {}

    mod.ModelType, mod.ModelConfig = ModelType, ModelConfig
    mod.get_chat_model = lambda provider, name, model_config=None, **kw: Model(provider, name, p)
    mod.rate_limiters = {}
    monkeypatch.setitem(sys.modules, "models", mod)
    for table in (K._KAME_BUSY_UNTIL, K._KAME_BUSY_WINDOW, K._KAME_BUSY_KIND, K._KAME_PROBED_AT, K._KAME_REPLIES,
                  K._KAME_TURNS, K._KAME_SEEDED, K._KAME_QUIET_SEEDED, K._KAME_KEY_HEALTH, K._KAME_STORM):
        table.clear()
    H.SPEED_LEDGER.clear()
    K.set_thought_quiet(0)
    yield p
    K.set_fallback_models("")
    K.set_thought_quiet(None)
    K.set_max_total_wait(0)
    K.set_wait_notice(False)
    for table in (K._KAME_BUSY_UNTIL, K._KAME_BUSY_WINDOW, K._KAME_PROBED_AT):
        table.clear()
    H.SPEED_LEDGER.clear()


def assert_bounded(vt, prov, refusing=None, home_rate=None):
    """Request-count bounds (designer review R1): no model is asked at loop speed.

    Every LIST model is asked at most len(keys) + ceil(elapsed / probe clock) + 2 times. A model that refused every key
    of this request (``refusing``) is asked at most len(keys) times in all. The best model in a storm
    (``home_rate``) is paced by its keys' own rests (a 503 rests a key 1 s, a dropped connection none, a throttle with
    no return time 20-30 s - the 1.8.x rotation, unchanged), so it is bounded by a rate instead: len(keys) +
    home_rate x elapsed. The fake answers in 0.05 s, so a loop-speed spin would show as 20 asks a second; the rate
    allowed here is 3."""
    import math
    cap = len(KEYS) + math.ceil(vt.elapsed() / K._KAME_PROBE_EVERY_S) + 2
    counts = {}
    for _t, who in prov.requests:
        counts[who] = counts.get(who, 0) + 1
    over = {who: n for who, n in counts.items() if n > cap and not (who == "home" and home_rate is not None)}
    assert not over, f"asked too often (cap {cap} in {vt.elapsed():.0f} s): {over}"
    if home_rate is not None:
        allowed = len(KEYS) + home_rate * vt.elapsed()
        assert counts.get("home", 0) <= allowed, f"home asked {counts.get('home')} times in {vt.elapsed():.0f} s"
    if refusing is not None:
        assert counts.get(refusing, 0) <= len(KEYS), f"{refusing} asked {counts.get(refusing)} times after refusing"


def flight():
    return {"job": "chat", "identity": ID, "started": time.time(), "refusals": 0, "last": "", "fallback": False}


def reply(vt, prov, limit=3600.0):
    """One main-chat reply with the defaults. Returns (result or the exception it raised, ctx, chat line)."""
    ctx, lines = make_ctx(prov.home)
    ctx["flight"] = flight()
    try:
        result = vt.run(K._kame_carousel(None, ctx), limit=limit)
    except BaseException as exc:                        # noqa: BLE001 - the point is what comes out
        result = exc
    return result, ctx, lines


# --- the defaults ---------------------------------------------------------------------------------------------

def test_a3_the_defaults_have_no_ceiling_and_the_walk_is_on(prov):
    assert K._KAME_MAX_TOTAL_WAIT_S == 0
    assert K._KAME_MODEL_SWITCH != "never" and not K._KAME_CAROUSEL_DISABLED and not K._KAME_ROTATION_DISABLED


# --- every storm, both lists ----------------------------------------------------------------------------------

@pytest.mark.parametrize("storm", sorted(STORMS))
@pytest.mark.parametrize("listing, list_ids", [(ONE_PROVIDER, [NEXT_ID, THIRD_ID]), (MIXED, MIXED_IDS)],
                         ids=["one-provider list", "mixed list"])
@pytest.mark.parametrize("who_returns", ["home", "list"])
def test_a3_a_storm_never_reaches_the_host_and_the_reply_comes_when_a_model_returns(
        vt, prov, storm, listing, list_ids, who_returns):
    K.set_fallback_models(listing)
    prov.fail = STORMS[storm]
    names = [e.replace(":", "/", 1) for e in list_ids]
    everybody = ["home"] + names
    prov.back_at = {who: 10 ** 9 for who in everybody}
    prov.back_at["home" if who_returns == "home" else names[0]] = STORM_S
    result, ctx, lines = reply(vt, prov)
    assert not isinstance(result, BaseException), f"{type(result).__name__}: {result}"
    assert result[0].startswith("answer from")
    assert lines, "the chat line got the answer's words"
    assert vt.elapsed() >= STORM_S - 1.0                     # it did wait for the model to come back
    assert vt.elapsed() < STORM_S + 600.0                    # and did not sit on it past a probe round
    assert_bounded(vt, prov, home_rate=3)


# --- the long wait ---------------------------------------------------------------------------------------------

def rest_every_key(seconds):
    for key in KEYS:
        K._mark_key_health(ID, key, success=False, delay=seconds, kind="daily")


def test_a3_two_hours_with_every_key_resting_keeps_waiting_and_keeps_the_chat_line(vt, prov):
    K.set_wait_notice(True)
    K._get_identity_state(ID, KEYS)
    rest_every_key(TWO_HOURS)
    # the 1.9.0.5 re-test asks one resting key again now and then: the provider still refuses until the 2 h are over
    prov.fail = lambda: Refusal("Error code: 429", 429,
                                {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED",
                                           "message": "You exceeded your current quota for today."}})
    prov.back_at = {"home": TWO_HOURS}
    ctx, lines = make_ctx(prov.home)
    ctx["flight"] = flight()
    samples = []

    async def watch():
        while True:
            await asyncio.sleep(600.0)
            samples.append((vt.elapsed(), ctx.get("status_text")))

    async def run():
        watcher = asyncio.ensure_future(watch())
        try:
            return await K._kame_carousel(None, ctx)
        finally:
            watcher.cancel()

    try:
        result = vt.run(run(), limit=3 * 3600.0)
    except BaseException as exc:                              # noqa: BLE001
        result = exc
    assert not isinstance(result, BaseException), f"{type(result).__name__}: {result}"
    assert result[0] == "answer from home"
    assert TWO_HOURS - 5 <= vt.elapsed() <= TWO_HOURS + 700    # waited it out, answered when the keys came back
    waiting = [(t, s) for t, s in samples if t < TWO_HOURS - 700]
    assert len(waiting) >= 10
    assert all(s for _t, s in waiting), f"the chat line went blank during the wait: {waiting[:3]}"


def test_a3_the_users_own_ceiling_still_ends_the_wait_at_60_s_with_the_providers_words(vt, prov):
    K.set_max_total_wait(60)
    prov.fail = lambda: Refusal("Error code: 429", 429,
                                {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED",
                                           "message": "You exceeded your current quota for today. "
                                                      "Please retry in 7200s"}},
                                {"retry-after": "7200"})
    prov.back_at = {"home": 10 ** 9}
    result, ctx, lines = reply(vt, prov, limit=3600.0)
    assert isinstance(result, BaseException), "the user's ceiling must end the wait"
    assert "quota" in str(result).lower()
    assert 59.0 <= vt.elapsed() <= 75.0, vt.elapsed()


# --- what is handed to the host on purpose ----------------------------------------------------------------------

def denied():
    return Refusal("Error code: 403", 403,
                   {"error": {"code": 403, "status": "PERMISSION_DENIED",
                              "message": "Your plan does not allow this model."}})


def test_a3_every_key_denied_this_model_waits_for_a_busy_list_model_instead_of_ending_the_reply(vt, prov):
    """Every key of the best model is refused the same way, so waiting on it is pointless - but the list is
    only BUSY (a state), not refusing. The reply waits for the list and is answered; it is handed to the host
    only when there is no list model left that could answer (the next test)."""
    K.set_fallback_models(ONE_PROVIDER)
    prov.back_at = {"home": 10 ** 9, "google/gemini-3.7-flash": 40.0, "google/gemini-3.6-flash": 40.0}

    original = prov.serve

    async def serve(who, ctx):
        if who == "home":
            prov.requests.append((vt.elapsed(), who))
            await asyncio.sleep(0.05)
            raise denied()
        prov.fail = overloaded
        return await original(who, ctx)

    prov.serve = serve
    result, ctx, lines = reply(vt, prov, limit=1800.0)
    assert not isinstance(result, BaseException), f"{type(result).__name__}: {result}"
    assert result[0].startswith("answer from google/gemini")
    assert vt.elapsed() >= 39.0
    assert_bounded(vt, prov, refusing="home")


def test_a3_every_key_denied_and_no_list_is_handed_to_the_host_with_the_providers_words(vt, prov):
    """By design: a request nobody can answer and nothing else to try is the host's to hear (unchanged)."""
    async def serve(who, ctx):
        prov.requests.append((vt.elapsed(), who))
        await asyncio.sleep(0.05)
        raise denied()

    prov.serve = serve
    result, ctx, lines = reply(vt, prov, limit=600.0)
    assert isinstance(result, BaseException) and "403" in str(result)
    assert_bounded(vt, prov, refusing="home")


def test_a3_a_request_refused_as_malformed_is_handed_back_at_once(vt, prov):
    """By design: a 400 that names the request is terminal. (0019 words it as 'once every model refused it';
    the code hands it back from the first model - see the report.)"""
    async def serve(who, ctx):
        prov.requests.append((vt.elapsed(), who))
        await asyncio.sleep(0.05)
        raise Refusal("Error code: 400", 400, {"error": {"code": 400, "status": "INVALID_ARGUMENT",
                                                         "message": "Request contains an invalid argument."}})

    K.set_fallback_models(ONE_PROVIDER)
    prov.serve = serve
    result, ctx, lines = reply(vt, prov, limit=600.0)
    assert isinstance(result, BaseException) and "400" in str(result)

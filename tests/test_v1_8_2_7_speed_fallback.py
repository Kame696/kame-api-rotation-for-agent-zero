"""1.8.2.7 - backups, keys asked together, and KAME's own model fallback list.

Driven through `_kame_carousel` with fake attempts that stream through the
call's own callbacks, the way Agent Zero's `unified_call` does. No Agent Zero
installation, no network.
"""
from __future__ import annotations

import asyncio
import sys
import time
import types

import pytest

if not any(name in sys.modules for name in ("test_v1_8_1_1_reliability", "tests.test_v1_8_1_1_reliability")):
    import test_v1_8_1_1_reliability  # noqa: F401
import kame_engine as K
import kame_hedging as H

ID = "google:gemini/gemini-3.8-flash"
KEYS = [f"AIzaSyTEST1827-{c * 10}" for c in "abcdefgh"]


class Refusal(Exception):
    def __init__(self, message, status, body=None, headers=None):
        super().__init__(message)
        self.status_code = status
        if body is not None:
            self.body = body
        self.response = types.SimpleNamespace(text=str(body or message), status_code=status,
                                              headers=headers or {})


class StopTest(BaseException):
    """Ends a carousel that would otherwise wait for ever."""


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    K._KAME_KEY_HEALTH.clear()
    K._KAME_ACCOUNT_HOLDS.clear()
    K._KAME_STATED_RL.clear()
    K._KAME_NO_ANSWER_SINCE.clear()
    if K._KJ is not None:
        K._KJ._reset_for_tests()
        # holds kept on disk would carry one test's rests into the next
        monkeypatch.setattr(K._KJ, "SHARE_HEALTH_ON", False)
    K._KAME_FALLBACK_SKIP.clear()
    K._KAME_FALLBACK_MODEL_CACHE.clear()
    K._KAME_LAST_MODEL_SWITCH.clear()
    for name in K._KAME_SPEED_STATS:
        K._KAME_SPEED_STATS[name] = 0
    H.HEDGE_TRACKER.clear()
    K.set_log_level("silent")
    K.set_wait_notice(False)
    K.set_hedge_mode("adaptive")
    K.set_hedge_max_legs(3)
    K.set_probe_burst(16)
    K.set_failover_after(60)
    K.set_model_switch("auto")
    K.set_first_token_wait("auto")
    K.set_max_total_wait(0)
    K.set_fallback_models("")
    if K._KJ is not None:
        K._KJ.EVENTS.clear()

    async def no_intervention(*_a, **_k):
        return None

    monkeypatch.setattr(K, "_kame_honor_intervention", no_intervention)
    yield
    K.set_fallback_models("")
    K.set_hedge_mode("adaptive")


def make_ctx(attempt, keys=KEYS, call_type="Chat", forwarded=None):
    K._get_identity_state(ID, keys)
    ctx = {"identity": ID, "all_keys": list(keys), "call_type": call_type, "model_short": "m",
           "attempt": attempt, "progress": {"any": False, "reasoning": False},
           "messages": ["hello"], "kwargs": {}, "entry_name": "unified_call",
           "rate_limiter_callback": None}
    forwarded = forwarded if forwarded is not None else []

    async def on_text(delta, full):
        forwarded.append(delta)
        return None

    if call_type == "Chat":
        ctx["response_callback"], ctx["reasoning_callback"], ctx["tokens_callback"] = \
            K._kame_wrap_callbacks(ctx, on_text, None, None)
    else:
        ctx["response_callback"] = ctx["reasoning_callback"] = ctx["tokens_callback"] = None
    return ctx, forwarded


def run(ctx, owner=None):
    return asyncio.run(K._kame_carousel(owner, ctx))


def streaming(delays, *, fail=None, log=None, active=None):
    """An attempt that says its key's name after ``delays[key]`` seconds."""
    async def attempt(owner, key, ctx):
        if log is not None:
            log.append(key)
        if active is not None:
            active["now"] += 1
            active["max"] = max(active["max"], active["now"])
        try:
            if fail and key in fail:
                await asyncio.sleep(0.01)
                raise fail[key]()
            await asyncio.sleep(delays.get(key, 0.0))
            if ctx.get("response_callback") is not None:
                await ctx["response_callback"](key[-4:], key[-4:])
            return (f"answer from {key[-4:]}", "")
        except asyncio.CancelledError:
            if log is not None:
                log.append("cancelled:" + key)
            raise
        finally:
            if active is not None:
                active["now"] -= 1
    return attempt


# --- backups -----------------------------------------------------------------

def test_a_lone_fast_call_is_not_raced():
    log = []
    ctx, forwarded = make_ctx(streaming({}, log=log))
    result = run(ctx)
    assert result[0].startswith("answer from")
    assert len(log) == 1 and forwarded == [log[0][-4:]]
    assert K._KAME_SPEED_STATS["backups"] == 0


def test_a_silent_first_word_gets_a_backup_and_the_first_to_speak_wins(monkeypatch):
    monkeypatch.setattr(H.HEDGE_TRACKER, "stream_triggers", lambda *a, **k: (0.05, 0.10))
    log = []
    first = KEYS[0]
    delays = {k: (5.0 if k == first else 0.0) for k in KEYS}
    ctx, forwarded = make_ctx(streaming(delays, log=log))
    # make the slow key the one picked first
    monkeypatch.setattr(K, "_get_best_key", _first_then_rest(first))
    t0 = time.monotonic()
    result = run(ctx)
    assert time.monotonic() - t0 < 2.0
    assert not result[0].endswith(first[-4:])
    assert ("cancelled:" + first) in log            # the slow request was closed
    assert len(forwarded) == 1 and forwarded[0] != first[-4:]  # only the winner reached the chat
    assert K._KAME_SPEED_STATS["backups"] >= 1


def test_speed_boost_off_never_races(monkeypatch):
    K.set_hedge_mode("off")
    monkeypatch.setattr(H.HEDGE_TRACKER, "stream_triggers", lambda *a, **k: (0.01, 0.02))
    log = []
    ctx, _ = make_ctx(streaming({k: 0.2 for k in KEYS}, log=log))
    run(ctx)
    assert len(log) == 1


def test_a_slow_background_call_gets_one_backup(monkeypatch):
    monkeypatch.setattr(H.HEDGE_TRACKER, "threshold", lambda *a, **k: 0.05)
    first = KEYS[0]
    log = []
    ctx, _ = make_ctx(streaming({k: (5.0 if k == first else 0.0) for k in KEYS}, log=log), call_type="Util")
    monkeypatch.setattr(K, "_get_best_key", _first_then_rest(first))
    result = run(ctx)
    assert not result[0].endswith(first[-4:])
    assert len([k for k in log if not k.startswith("cancelled:")]) == 2   # never more than one backup


def test_a_backup_that_fails_is_judged_on_its_own_key(monkeypatch):
    monkeypatch.setattr(H.HEDGE_TRACKER, "stream_triggers", lambda *a, **k: (0.05, 0.5))
    first, second = KEYS[0], KEYS[1]
    fail = {second: lambda: Refusal("Error code: 429", 429, {"error": {"message": "slow down"}},
                                    {"retry-after": "30"})}
    ctx, _ = make_ctx(streaming({first: 0.3}, fail=fail))
    monkeypatch.setattr(K, "_get_best_key", _first_then_rest(first))
    result = run(ctx)
    assert result[0].endswith(first[-4:])
    kd = K._KAME_KEY_HEALTH[ID]["keys"][second]
    assert float(kd["sick_until"]) > time.time() + 20      # the backup's key rests its own 30 s
    assert float(K._KAME_KEY_HEALTH[ID]["keys"][first]["sick_until"] or 0) < time.time()


# --- keys asked together -----------------------------------------------------

def test_a_pool_running_dry_asks_keys_together(monkeypatch):
    monkeypatch.setattr(H.HEDGE_TRACKER, "stream_triggers", lambda *a, **k: (30.0, 60.0))
    dry = set(KEYS[:2])
    billing = lambda: Refusal("Error code: 402", 402, {"error": {"message": "insufficient balance"}})
    active = {"now": 0, "max": 0}
    log = []
    ctx, _ = make_ctx(streaming({k: 0.05 for k in KEYS}, fail={k: billing for k in dry},
                                log=log, active=active))
    monkeypatch.setattr(K, "_get_best_key", _in_order())
    result = run(ctx)
    assert result[0].startswith("answer from")
    assert active["max"] >= 4                       # the third attempt asked four keys at once
    assert K._KAME_SPEED_STATS["probe_bursts"] == 1


def test_background_calls_never_burst(monkeypatch):
    monkeypatch.setattr(H.HEDGE_TRACKER, "threshold", lambda *a, **k: 30.0)
    billing = lambda: Refusal("Error code: 402", 402, {"error": {"message": "insufficient balance"}})
    active = {"now": 0, "max": 0}
    ctx, _ = make_ctx(streaming({}, fail={k: billing for k in KEYS[:3]}, active=active), call_type="Util")
    monkeypatch.setattr(K, "_get_best_key", _in_order())
    run(ctx)
    assert active["max"] == 1


# --- the fallback list -------------------------------------------------------

class FakeModel:
    def __init__(self, provider, name, answers):
        self.a0_model_conf = types.SimpleNamespace(provider=provider)
        self.model_name = f"{provider}/{name}"
        self.answers = answers

    async def unified_call(self, **kwargs):
        self.answers.append((self.model_name, kwargs))
        behaviour = FALLBACK_BEHAVIOUR.get(self.model_name, "answer")
        if behaviour == "resting":
            raise K._KameSwitchModel("every key resting")
        if behaviour == "error":
            raise Refusal("Error code: 400", 400, {"error": {"message": "bad request"}})
        if behaviour == "slow":   # 1.8.2.8: a fallback model as slow as the first one
            await asyncio.sleep(3.0)
        if behaviour == "late":   # 1.8.2.8: answers, a little after the first slow bound
            await asyncio.sleep(0.35)
        return (f"fallback answer from {self.model_name}", "")


FALLBACK_BEHAVIOUR = {}


@pytest.fixture
def fake_models(monkeypatch):
    answers = []
    FALLBACK_BEHAVIOUR.clear()
    mod = types.ModuleType("models")

    class ModelType:
        CHAT = "Chat"

    class ModelConfig:
        def __init__(self, **kw):
            self.__dict__.update(kw)

        def build_kwargs(self):
            return {}

    mod.ModelType, mod.ModelConfig = ModelType, ModelConfig
    mod.get_chat_model = lambda provider, name, model_config=None, **kw: FakeModel(provider, name, answers)
    monkeypatch.setitem(sys.modules, "models", mod)
    return answers


def _rest_every_key(seconds=3600, kind="insufficient_quota"):
    for k in KEYS:
        K._mark_key_health(ID, k, False, seconds, kind)


def test_every_key_resting_long_answers_with_the_next_model(fake_models, monkeypatch):
    K.set_fallback_models("openrouter/meta-llama/llama-4\ngroq/llama-3.3-70b")
    ctx, _ = make_ctx(streaming({}))
    _rest_every_key()
    result = run(ctx)
    assert result[0] == "fallback answer from openrouter/meta-llama/llama-4"
    assert K._KAME_LAST_MODEL_SWITCH["to"] == "openrouter/meta-llama/llama-4"
    kinds = [e["kind"] for e in K._KJ.EVENTS.recent(20)]
    assert "model_switch" in kinds
    # the conversation went with it, the provider state of the old model did not
    _model, kwargs = fake_models[0]
    assert kwargs["messages"] == ["hello"] and "previous_response_id" not in kwargs


def test_an_entry_that_cannot_answer_either_passes_to_the_next(fake_models):
    K.set_fallback_models("openrouter/a, groq/b")
    FALLBACK_BEHAVIOUR["openrouter/a"] = "resting"
    ctx, _ = make_ctx(streaming({}))
    _rest_every_key()
    assert run(ctx)[0] == "fallback answer from groq/b"


def test_an_entry_that_errors_passes_to_the_next(fake_models):
    K.set_fallback_models("openrouter/a, groq/b")
    FALLBACK_BEHAVIOUR["openrouter/a"] = "error"
    ctx, _ = make_ctx(streaming({}))
    _rest_every_key()
    assert run(ctx)[0] == "fallback answer from groq/b"


def test_no_list_waits_as_before(fake_models, monkeypatch):
    waited = []

    async def sleep(*a, **k):
        waited.append(1)
        raise StopTest()

    monkeypatch.setattr(K, "_kame_sleep_on_exhaustion", sleep)
    ctx, _ = make_ctx(streaming({}))
    _rest_every_key()
    with pytest.raises(StopTest):
        run(ctx)
    assert waited and not fake_models


def test_a_short_rest_waits_instead_of_switching(fake_models, monkeypatch):
    K.set_fallback_models("groq/b")
    waited = []

    async def sleep(*a, **k):
        waited.append(1)
        raise StopTest()

    monkeypatch.setattr(K, "_kame_sleep_on_exhaustion", sleep)
    ctx, _ = make_ctx(streaming({}))
    _rest_every_key(4, "per_minute")    # back in 4 s: well under the switch time (auto: 10 s cold)
    with pytest.raises(StopTest):
        run(ctx)
    assert waited and not fake_models


def test_failover_zero_never_switches(fake_models, monkeypatch):
    K.set_fallback_models("groq/b")
    K.set_failover_after(0)

    async def sleep(*a, **k):
        raise StopTest()

    monkeypatch.setattr(K, "_kame_sleep_on_exhaustion", sleep)
    ctx, _ = make_ctx(streaming({}))
    _rest_every_key()
    with pytest.raises(StopTest):
        run(ctx)
    assert not fake_models


def test_background_calls_never_switch_model(fake_models, monkeypatch):
    K.set_fallback_models("groq/b")

    async def sleep(*a, **k):
        raise StopTest()

    monkeypatch.setattr(K, "_kame_sleep_on_exhaustion", sleep)
    ctx, _ = make_ctx(streaming({}), call_type="Util")
    _rest_every_key()
    with pytest.raises(StopTest):
        run(ctx)
    assert not fake_models


def test_never_switch_once_the_answer_has_started():
    ctx, _ = make_ctx(streaming({}))
    ctx["progress"]["any"] = True
    K.set_fallback_models("groq/b")
    assert not K._kame_can_switch(ctx)


def test_inside_a_fallback_call_the_list_is_not_walked_again():
    ctx, _ = make_ctx(streaming({}))
    K.set_fallback_models("groq/b")
    token = K._KAME_FALLBACK_HOP.set({"origin": "x"})
    try:
        with pytest.raises(K._KameSwitchModel):
            asyncio.run(K._kame_switch_model(None, ctx, "every key resting"))
    finally:
        K._KAME_FALLBACK_HOP.reset(token)


def test_a_closed_door_switches_model(fake_models):
    K.set_fallback_models("groq/b")
    door = lambda: Refusal("Error code: 503", 503, {"error": {"message": "No instances available for this model"}})
    ctx, _ = make_ctx(streaming({}, fail={k: door for k in KEYS}))
    assert run(ctx)[0] == "fallback answer from groq/b"


def test_a_closed_door_without_a_list_rests_the_whole_pool(monkeypatch):
    door = lambda: Refusal("Error code: 503", 503, {"error": {"message": "No instances available for this model"}})
    calls = []

    async def sleep(*a, **k):
        raise StopTest()

    monkeypatch.setattr(K, "_kame_sleep_on_exhaustion", sleep)
    ctx, _ = make_ctx(streaming({}, fail={k: door for k in KEYS}, log=calls))
    with pytest.raises(StopTest):
        run(ctx)
    assert len(calls) == 1                           # one knock, not one per key
    for k in KEYS:
        assert float(K._KAME_KEY_HEALTH[ID]["keys"][k]["sick_until"]) >= time.time() + 25


def test_two_timeouts_in_a_row_switch_model(fake_models, monkeypatch):
    K.set_fallback_models("groq/b")
    K.set_hedge_mode("off")
    timeout = lambda: Refusal("Error code: 504", 504, {"error": {"message": "gateway timeout"}})
    log = []
    ctx, _ = make_ctx(streaming({}, fail={k: timeout for k in KEYS}, log=log))
    monkeypatch.setattr(K, "_KAME_REAL_TIMEOUT_MIN_S", 0.0)
    assert run(ctx)[0] == "fallback answer from groq/b"
    assert len(log) == 2


def test_timeouts_without_a_list_keep_rotating(monkeypatch):
    K.set_hedge_mode("off")
    timeout = lambda: Refusal("Error code: 504", 504, {"error": {"message": "gateway timeout"}})
    log = []
    ctx, _ = make_ctx(streaming({}, fail={k: timeout for k in KEYS[:4]}, log=log))
    monkeypatch.setattr(K, "_KAME_REAL_TIMEOUT_MIN_S", 0.0)
    for k in KEYS[4:]:
        K._KAME_KEY_HEALTH[ID]["keys"][k]["request_log"].extend([time.time()] * 5)  # picked last
    result = run(ctx)
    assert result[0].startswith("answer from") and len(log) >= 3   # rotated past two timeouts


def test_every_key_refusing_the_model_is_handed_back(monkeypatch):
    K.set_hedge_mode("off")
    denied = lambda: Refusal("Error code: 403", 403, {"error": {"message": "Your plan does not include this model"}})
    keys = KEYS[:3]
    ctx, _ = make_ctx(streaming({}, fail={k: denied for k in keys}), keys=keys)
    with pytest.raises(Refusal):
        run(ctx)


def test_max_total_wait_stops_waiting(monkeypatch):
    K.set_max_total_wait(5)
    ctx, _ = make_ctx(streaming({}))
    _rest_every_key()

    async def sleep(identity, all_keys, call_type, model_short, st):
        st.cold_since = time.time() - 10
        st.sleep_count += 1

    monkeypatch.setattr(K, "_kame_sleep_on_exhaustion", sleep)
    with pytest.raises(RuntimeError, match="stopped waiting"):
        run(ctx)


# --- settings ----------------------------------------------------------------

def test_the_fallback_list_reads_lines_commas_and_bases():
    rows = K.parse_fallback_models(
        "openrouter/meta-llama/llama-4\n# a comment\ngroq/llama-3.3, ollama/qwen3 @ http://localhost:11434;"
        "nonsense; GROQ/llama-3.3")
    assert [r["label"] for r in rows] == ["openrouter/meta-llama/llama-4", "groq/llama-3.3", "ollama/qwen3"]
    assert rows[2]["api_base"] == "http://localhost:11434"


def test_settings_report_the_new_values():
    K.set_fallback_models("groq/b")
    K.set_failover_after(90)
    K.set_hedge_mode("max")
    now = K.current_settings()
    assert now["fallback_models"] == "groq/b"
    assert now["failover_after_seconds"] == 90
    assert now["hedge_mode"] == "max" and H.SETTINGS["hedge_mode"] == "max"


# --- helpers -----------------------------------------------------------------

def _first_then_rest(first):
    original = K._get_best_key

    def pick(identity, keys):
        if first in keys and not any(K._KAME_KEY_HEALTH[identity]["keys"][first].get("_picked") for _ in [0]):
            K._KAME_KEY_HEALTH[identity]["keys"][first]["_picked"] = True
            return first, "SUCCESS"
        rest = [k for k in keys if k != first]
        return original(identity, rest or keys)
    return pick


def _in_order():
    def pick(identity, keys):
        now = time.time()
        pool = K._KAME_KEY_HEALTH[identity]["keys"]
        for k in keys:
            if float(pool[k].get("sick_until") or 0) < now:
                return k, "SUCCESS"
        return keys[0], "EXHAUSTED_RETRY"
    return pick


# --- every setting has a reader (Hermes 1.8.2.4 meta-test) ---------------------

def test_every_setting_is_read_by_something():
    import os
    import kame_settings as S
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    readers = open(os.path.join(root, "kame_activation.py"), encoding="utf-8").read()
    for dirpath, _dirs, files in os.walk(os.path.join(root, "extensions", "python")):
        for name in files:
            if name.endswith(".py"):
                readers += open(os.path.join(dirpath, name), encoding="utf-8").read()
    unread = [n for n in S.SETTINGS if f'"{n}"' not in readers]
    assert not unread, unread


def test_the_panel_and_the_screen_know_every_setting():
    import os
    import kame_settings as S
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    page = open(os.path.join(root, "webui", "config.html"), encoding="utf-8").read()
    assert all(f"config.{n}" in page for n in S.SETTINGS)
    assert set(S.VISIBLE) <= set(S.SETTINGS) and set(S.TROUBLESHOOTING) <= set(S.SETTINGS)
    assert "keys share" not in page.lower()   # one key = one account here (Hermes 0027 s4)


def test_out_of_credit_skips_a_fallback_on_the_same_account(fake_models):
    # An out-of-credit hold belongs to the account: a fallback model of the same
    # provider with the same keys cannot answer either, so the walk goes on.
    K.set_fallback_models("google/gemini-other\ngroq/b")
    FALLBACK_BEHAVIOUR["google/gemini-other"] = "resting"   # its own carousel would say so
    ctx, _ = make_ctx(streaming({}))
    _rest_every_key()                                          # insufficient_quota, account-wide
    assert run(ctx)[0] == "fallback answer from groq/b"

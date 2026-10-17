"""1.8.2.7 - the rest of the Hermes 1.8.2.3 - 1.8.2.7 behaviour, on Agent Zero.

Retry hints the agent can read, wait for the first token, continuing a cut
answer (stitching), learned per-minute pacing, the latency tiebreak, the region
in the pool identity, the backup trigger seeded from history, and the Hermes
switches that apply here.
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
import types

import pytest

if not any(name in sys.modules for name in ("test_v1_8_1_1_reliability", "tests.test_v1_8_1_1_reliability")):
    import test_v1_8_1_1_reliability  # noqa: F401
try:
    import test_v1_8_2_7_speed_fallback as T
except ImportError:  # collected as a package
    from tests import test_v1_8_2_7_speed_fallback as T
import kame_engine as K
import kame_hedging as H

clean = T.clean
KEYS, ID, Refusal = T.KEYS, T.ID, T.Refusal


class _AI:
    type = "ai"

    def __init__(self, content=""):
        self.content = content


class _Human:
    type = "human"

    def __init__(self, content=""):
        self.content = content


@pytest.fixture(autouse=True)
def parity(monkeypatch):
    monkeypatch.setattr(K, "AIMessage", _AI)
    monkeypatch.setattr(K, "HumanMessage", _Human)
    K._KAME_NO_PREFILL.clear()
    K._KAME_SEEDED.clear()
    if K._KP is not None:
        K._KP.PACER.clear()
    if K._KL is not None:
        K._KL.LATENCY.clear()
    K.set_stream_silence_timeout(0)
    K.set_model_switch("auto")
    K.set_first_token_wait("auto")
    K.set_stream_resume_limit(-1)
    K.set_stream_stitch_disabled(False)
    K.set_hedging_disabled(False)
    K.set_pacing_rpm_overrides("")
    K.set_key_region_overrides("")
    yield
    K.set_stream_silence_timeout(0)
    K.set_model_switch("auto")
    K.set_first_token_wait("auto")
    K.set_hedging_disabled(False)


# --- retry hints -------------------------------------------------------------

def test_a_temporary_failure_handed_back_says_retry():
    K.set_carousel_disabled(True)
    try:
        busy = lambda: Refusal("Error code: 503", 503, {"error": {"message": "Service temporarily unavailable"}})
        ctx, _ = T.make_ctx(T.streaming({}, fail={k: busy for k in KEYS}))
        with pytest.raises(Refusal) as info:
            T.run(ctx)
    finally:
        K.set_carousel_disabled(False)
    assert "Retrying the same tool call will very likely succeed" in str(info.value)
    assert info.value.status_code == 503            # the same exception, type and status kept


def test_a_plan_refusal_says_do_not_keep_retrying():
    K.set_hedge_mode("off")
    no_plan = lambda: Refusal("Error code: 429", 429, {"error": {"code": "usage_not_included",
                              "message": "The usage_not_included plan does not include this service"}})
    keys = KEYS[:3]
    ctx, _ = T.make_ctx(T.streaming({}, fail={k: no_plan for k in keys}), keys=keys)
    with pytest.raises(Refusal) as info:
        T.run(ctx)
    assert "Do NOT keep retrying" in str(info.value)


def test_a_refused_model_says_retrying_will_not_help():
    K.set_hedge_mode("off")
    denied = lambda: Refusal("Error code: 403", 403, {"error": {"message": "Your plan does not include this model"}})
    keys = KEYS[:3]
    ctx, _ = T.make_ctx(T.streaming({}, fail={k: denied for k in keys}), keys=keys)
    with pytest.raises(Refusal) as info:
        T.run(ctx)
    assert "Retrying will not help" in str(info.value)


def test_a_request_fault_gets_no_hint_and_hints_never_double():
    exc = Refusal("Error code: 400", 400, {"error": {"message": "bad"}})
    assert "Retry" not in str(K._kame_with_retry_hint(exc, "other"))
    K._kame_with_retry_hint(exc, "server")
    K._kame_with_retry_hint(exc, "server")
    assert str(exc).count("Retrying the same tool call") == 1


def test_the_hint_reaches_a_litellm_style_error_text():
    # LiteLLM builds str() from `.message`; Agent Zero's _error_retry shows the
    # agent format_error(exc), which is str(). The hint must be in it.
    class LiteLLMLike(Exception):
        def __init__(self, message):
            self.message = message
            super().__init__(message)

        def __str__(self):
            return self.message

    exc = LiteLLMLike("litellm.ServiceUnavailableError: busy")
    K._kame_with_retry_hint(exc, "server")
    K._kame_with_retry_hint(exc, "server")
    import traceback
    text = "".join(traceback.format_exception(type(exc), exc, None))
    assert text.count("Retrying the same tool call will very likely succeed") == 1


# --- wait for the first token -----------------------------------------------

def test_a_silent_key_is_dropped_after_the_first_token_wait(monkeypatch):
    K.set_hedge_mode("off")
    K.set_stream_silence_timeout(0.2)
    first = KEYS[0]
    log = []
    ctx, _ = T.make_ctx(T.streaming({first: 5.0}, log=log))
    monkeypatch.setattr(K, "_get_best_key", T._first_then_rest(first))
    monkeypatch.setattr(K, "_KAME_REAL_TIMEOUT_MIN_S", 0.0)
    t0 = time.monotonic()
    result = T.run(ctx)
    assert time.monotonic() - t0 < 2.0 and not result[0].endswith(first[-4:])
    assert ("cancelled:" + first) in log
    kd = K._KAME_KEY_HEALTH[ID]["keys"][first]
    assert kd.get("last_hold_kind") in ("timeout", None, "")   # a timeout: no bench


def test_the_first_token_wait_leaves_a_local_model_alone():
    K.set_stream_silence_timeout(5)
    ctx, _ = T.make_ctx(T.streaming({}))
    ctx["kwargs"] = {"api_base": "http://localhost:11434"}
    assert K._kame_silence_for(None, ctx, 1) == 0.0
    ctx["kwargs"] = {}
    assert K._kame_silence_for(None, ctx, 1) == 5.0
    assert K._kame_silence_for(None, ctx, 3) == 5.0      # max(5, 5 * 0.25)
    K.set_stream_silence_timeout(60)
    assert K._kame_silence_for(None, ctx, 3) == 15.0


# --- continuing a cut answer -------------------------------------------------

def _cut_then(continuations, sent):
    calls = {"n": 0}

    async def attempt(owner, key, ctx):
        calls["n"] += 1
        sent.append(list(ctx.get("attempt_messages") or ctx["messages"]))
        if calls["n"] == 1:
            await ctx["response_callback"]("Hello wor", "Hello wor")
            raise Refusal("Error code: 503", 503, {"error": {"message": "Service temporarily unavailable"}})
        step = continuations[calls["n"] - 2]
        if isinstance(step, Exception):
            raise step
        await ctx["response_callback"](step, step)
        return (step, "")
    return attempt


def test_a_cut_answer_is_continued_and_shown_once():
    K.set_hedge_mode("off")
    sent, shown = [], []
    ctx, forwarded = T.make_ctx(_cut_then(["ld, how are you?"], sent))

    async def on_text(delta, full):
        shown.append(full)
        return None

    ctx["response_callback"], ctx["reasoning_callback"], ctx["tokens_callback"] = \
        K._kame_wrap_callbacks(ctx, on_text, None, None)
    result = T.run(ctx)
    assert result[0] == "Hello world, how are you?"
    assert shown[-1] == "Hello world, how are you?" and shown[0] == "Hello wor"
    last = sent[-1][-1]
    assert isinstance(last, _AI) and last.content == "Hello wor"     # continued, not restarted


def test_a_provider_that_refuses_a_prefill_gets_a_user_turn():
    K.set_hedge_mode("off")
    sent = []
    refuse = Refusal("Error code: 400", 400,
                     {"error": {"message": "Please ensure that multiturn requests alternate; Requests ending with a model turn are not supported"}})
    ctx, _ = T.make_ctx(_cut_then([refuse, "ld!"], sent))
    result = T.run(ctx)
    assert result[0] == "Hello world!"
    assert isinstance(sent[-1][-1], _Human) and ID in K._KAME_NO_PREFILL
    healthy = [k for k in KEYS if float(K._KAME_KEY_HEALTH[ID]["keys"][k].get("sick_until") or 0) < time.time()]
    assert len(healthy) >= len(KEYS) - 1      # only the cut key rests; the prefill refusal rests none


def test_a_responses_api_call_starts_again_instead():
    K.set_hedge_mode("off")
    sent = []
    ctx, _ = T.make_ctx(_cut_then(["Hello world"], sent))
    ctx["kwargs"] = {"a0_api_mode": "responses"}
    result = T.run(ctx)
    assert result[0] == "Hello world" and not isinstance(sent[-1][-1], _AI)


def test_stitching_off_starts_again():
    K.set_hedge_mode("off")
    K.set_stream_stitch_disabled(True)
    sent = []
    ctx, _ = T.make_ctx(_cut_then(["Hello world"], sent))
    assert T.run(ctx)[0] == "Hello world" and not isinstance(sent[-1][-1], _AI)


def test_a_chat_result_object_is_rebuilt_whole():
    class Result:
        mode = "chat_completions"
        function_calls = []
        reasoning, usage, input_items, provider_model_key, capability, history_extras = "", {}, [], "m", {}, ""

        @classmethod
        def from_chat(cls, **kw):
            r = cls()
            r.response = kw["response"]
            return r

    rebuilt = K._kame_stitched_result(Result(), "the whole answer")
    assert rebuilt.response == "the whole answer"


# --- pacing, latency, region, seeding ---------------------------------------

def test_a_key_at_its_learned_limit_waits_behind_a_free_one():
    if K._KP is None:
        pytest.skip("pacing module missing")
    K._get_identity_state(ID, KEYS[:2])
    a, b = KEYS[0], KEYS[1]
    K._KP.PACER.note_limit(ID, a, 1)
    K._KP.PACER.stamp(ID, a)
    K._KAME_KEY_HEALTH[ID]["keys"][b]["request_log"].extend([time.time()] * 3)   # b is busier
    key, status = K._get_best_key(ID, [a, b])
    assert key == b


def test_a_per_minute_refusal_teaches_the_pacer():
    if K._KP is None:
        pytest.skip("pacing module missing")
    K._get_identity_state(ID, KEYS[:2])
    for _ in range(3):
        K._KP.PACER.stamp(ID, KEYS[0])
    exc = Refusal("Error code: 429", 429, {"error": {"code": 429, "message": "Resource has been exhausted"}})
    K._kame_decide_failure(ID, KEYS[0], exc)
    assert K._KP.PACER._rpm.get((ID, KEYS[0])) == 2


def test_between_equally_free_keys_the_faster_one_goes_first():
    if K._KL is None:
        pytest.skip("latency module missing")
    K._get_identity_state(ID, KEYS[:2])
    for k in KEYS[:2]:
        K._KAME_KEY_HEALTH[ID]["keys"][k]["last_used"] = 0
        K._KAME_KEY_HEALTH[ID]["keys"][k]["request_log"] = []
    K._KL.LATENCY.record(ID, KEYS[0], 5.0)
    K._KL.LATENCY.record(ID, KEYS[1], 0.5)
    assert K._get_best_key(ID, KEYS[:2])[0] == KEYS[1]


def test_a_regional_endpoint_gets_its_own_pool():
    owner = types.SimpleNamespace(kwargs={}, a0_model_conf=types.SimpleNamespace(api_base=""))
    base = "https://us-central1-aiplatform.googleapis.com/v1/projects/p/locations/europe-west4/publishers/google"
    assert K._kame_identity_with_region("vertex:gemini", owner, {"api_base": base}, "gemini", KEYS) \
        == "vertex:gemini:europe-west4"
    assert K._kame_identity_with_region("openai:gpt", owner, {}, "gpt", KEYS) == "openai:gpt"
    K.set_key_region_overrides(f"{K._key_short_id(KEYS[0])}=us-east5")
    assert K._kame_identity_with_region("vertex:gemini", owner, {}, "gemini", KEYS) == "vertex:gemini:us-east5"


def test_the_backup_trigger_is_seeded_from_recorded_first_words(monkeypatch, tmp_path):
    rows = [{"at": time.time(), "identity": ID, "outcome": "ok", "ms_to_first_sign": 800 + i}
            for i in range(30)]
    calls = tmp_path / "calls.jsonl"
    calls.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    monkeypatch.setattr(K._KJ, "_path", lambda name: calls if name == K._KJ.CALLS_FILE else None)
    history = K._KJ.first_word_history()
    assert len(history[ID]) == 30
    K._kame_seed_triggers(ID, False)
    first, _second = H.HEDGE_TRACKER.stream_triggers(ID, reasoning=False)
    assert 1.0 <= first < 6.0           # learned (2 x median), not the 6 s cold start


def test_an_answer_teaches_pacing_and_latency():
    if K._KP is None or K._KL is None:
        pytest.skip("modules missing")
    K.set_hedge_mode("off")
    ctx, _ = T.make_ctx(T.streaming({}))
    T.run(ctx)
    assert any(pair[0] == ID for pair in K._KP.PACER._log)
    assert any(pair[0] == ID for pair in K._KL.LATENCY._state)


def test_hedging_disabled_is_one_key_at_a_time(monkeypatch):
    K.set_hedging_disabled(True)
    monkeypatch.setattr(H.HEDGE_TRACKER, "stream_triggers", lambda *a, **k: (0.01, 0.02))
    log = []
    ctx, _ = T.make_ctx(T.streaming({k: 0.2 for k in KEYS}, log=log))
    T.run(ctx)
    assert len(log) == 1 and H.SETTINGS["hedge_mode"] == "off"


def test_the_hedge_numbers_reach_the_tracker():
    K.set_hedge_number("hedge_cold_s", 9)
    K.set_hedge_number("hedge_budget_pct", 500)          # clamped to 50
    assert H.SETTINGS["hedge_cold_s"] == 9 and H.SETTINGS["hedge_budget_pct"] == 50
    K.set_hedge_number("hedge_cold_s", 6)
    K.set_hedge_number("hedge_budget_pct", 10)

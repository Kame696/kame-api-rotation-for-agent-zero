"""1.8.2.8 - adaptive speed and switching (Hermes decisions/0028), Agent Zero port.

The owner's case: fallback models set, the configured model answering "the
model is overloaded" (503). A 503 rests a key 1 s, so "every key resting long"
never came and the list was never used. Driven through `_kame_carousel` with the
1.8.2.7 harness. No Agent Zero installation, no network.
"""
from __future__ import annotations

import asyncio
import time
import types

import pytest

from test_v1_8_2_7_speed_fallback import (  # noqa: F401  (fixtures are used by name)
    FALLBACK_BEHAVIOUR, ID, KEYS, Refusal, StopTest, clean, fake_models, make_ctx, run, streaming,
)
import kame_engine as K
import kame_hedging as H
import kame_settings as S


def overloaded():
    return Refusal("Error code: 503", 503,
                   {"error": {"code": 503, "message": "The model is overloaded. Please try again later.",
                              "status": "UNAVAILABLE"}})


@pytest.fixture(autouse=True)
def calm():
    K._KAME_BUSY_UNTIL.clear()
    K._KAME_BUSY_WINDOW.clear()
    K._KAME_REPLIES.clear()
    K._KAME_TURNS.clear()
    K.set_reply_notice("always")
    yield
    K._KAME_BUSY_UNTIL.clear()
    K._KAME_BUSY_WINDOW.clear()


# --- the owner's case -------------------------------------------------------------

def test_an_overloaded_model_moves_to_the_next_model_after_two_keys(fake_models):
    K.set_fallback_models("google/gemini-3.7-flash")
    log = []
    ctx, _ = make_ctx(streaming({}, fail={k: overloaded for k in KEYS}, log=log))
    result = run(ctx)
    assert result[0] == "fallback answer from google/gemini-3.7-flash"
    assert len(log) == 2
    assert "busy" in K._KAME_LAST_MODEL_SWITCH["reason"]
    assert time.time() < K._KAME_BUSY_UNTIL[ID]                 # the busy model is remembered


def test_without_a_list_an_overloaded_model_rotates_one_key_at_a_time():
    active = {"now": 0, "max": 0}
    log = []
    fail = {k: overloaded for k in KEYS[:3]}
    ctx, _ = make_ctx(streaming({}, fail=fail, log=log, active=active), keys=KEYS[:4])
    K.set_hedge_mode("off")   # no backup races: what is measured is the rotation itself
    result = run(ctx)
    assert result[0].startswith("answer from")
    assert log == KEYS[:4] and active["max"] == 1


def test_never_keeps_the_overloaded_model(fake_models, monkeypatch):
    K.set_fallback_models("google/gemini-3.7-flash")
    K.set_model_switch("never")
    fail = {k: overloaded for k in KEYS[:3]}
    ctx, _ = make_ctx(streaming({}, fail=fail), keys=KEYS[:4])
    result = run(ctx)
    assert result[0].startswith("answer from") and not fake_models


def test_after_my_time_counts_the_users_wait_not_only_the_resting(fake_models, monkeypatch):
    K.set_fallback_models("google/gemini-3.7-flash")
    K.set_failover_after(0.3)                  # "After my time"
    assert K._KAME_MODEL_SWITCH == "timed"

    def slow_fail():
        time.sleep(0.2)                        # each refusal takes a while to arrive
        return Refusal("Error code: 500", 500, {"error": {"message": "internal"}})

    fail = {k: slow_fail for k in KEYS}
    ctx, _ = make_ctx(streaming({}, fail=fail))
    result = run(ctx)
    assert result[0] == "fallback answer from google/gemini-3.7-flash"


def test_the_busy_memory_moves_the_next_call_after_one_knock(fake_models):
    K.set_fallback_models("google/gemini-3.7-flash")
    K._kame_note_busy(ID)
    log = []
    ctx, _ = make_ctx(streaming({}, fail={k: overloaded for k in KEYS}, log=log))
    assert run(ctx)[0] == "fallback answer from google/gemini-3.7-flash"
    assert len(log) == 1


def test_an_answer_clears_the_busy_memory():
    K._kame_note_busy(ID)
    ctx, _ = make_ctx(streaming({}))
    run(ctx)
    assert ID not in K._KAME_BUSY_UNTIL


# --- automatic numbers -----------------------------------------------------------

def _learn(seconds, reasoning, n=40):
    for _ in range(n):
        H.HEDGE_TRACKER.record_latency(ID, seconds, stream=True, reasoning=reasoning)


def test_the_automatic_switch_time_follows_the_models_usual_first_word():
    ctx, _ = make_ctx(streaming({}))
    reasoning = K._kame_reasoning_call(ctx)
    assert K._kame_switch_threshold(ID, ctx) == K._KAME_AUTO_SWITCH_COLD_S
    _learn(4.0, reasoning)
    assert K._kame_switch_threshold(ID, ctx) == 12.0
    H.HEDGE_TRACKER.clear()
    _learn(30.0, reasoning)
    assert K._kame_switch_threshold(ID, ctx) == K._KAME_AUTO_SWITCH_MAX_S


def test_the_first_token_wait_modes():
    ctx, _ = make_ctx(streaming({}))
    thinking = K._kame_reasoning_call(ctx)
    cold = 300.0 if thinking else 180.0
    assert K._kame_silence_for(None, ctx, 1) == cold                  # automatic, cold
    background, _ = make_ctx(streaming({}))
    background["main_chat"] = False                                   # a compaction or utility stream
    assert K._kame_silence_for(None, background, 1) == 0.0
    ctx["kwargs"] = {"api_base": "http://localhost:11434"}
    assert K._kame_silence_for(None, ctx, 1) == 0.0                   # never a local model
    ctx["kwargs"] = {}
    K.set_first_token_wait("off")
    assert K._kame_silence_for(None, ctx, 1) == 0.0
    K.set_stream_silence_timeout(40)                                  # a number = "My time"
    assert K._KAME_FIRST_TOKEN_MODE == "timed"
    assert K._kame_silence_for(None, ctx, 1) == 40.0
    assert K._kame_silence_for(None, ctx, 3) == 10.0
    utility, _ = make_ctx(streaming({}), call_type="Utility")
    K.set_first_token_wait("auto")
    assert K._kame_silence_for(None, utility, 1) == 0.0                # streams of the chat only


@pytest.mark.parametrize("cfg, model_switch, first_token", [
    ({}, "auto", "auto"),
    ({"failover_after_seconds": 60, "stream_silence_timeout_seconds": 0}, "auto", "auto"),
    ({"failover_after_seconds": 90, "stream_silence_timeout_seconds": 45}, "timed", "timed"),
    ({"failover_after_seconds": 0}, "never", "auto"),
    ({"model_switch": "auto", "failover_after_seconds": 90}, "auto", "auto"),
    ({"model_switch": "never", "first_token_wait": "off"}, "never", "off"),
])
def test_a_configuration_saved_before_1828_keeps_what_its_numbers_meant(cfg, model_switch, first_token):
    assert S.choice_from_config(cfg, "model_switch") == model_switch
    assert S.choice_from_config(cfg, "first_token_wait") == first_token


# --- the reply line ----------------------------------------------------------------

class _Log:
    def __init__(self):
        self.rows = []

    def log(self, **kw):
        self.rows.append(kw)


def _agent(number=0, context=None):
    return types.SimpleNamespace(number=number, context=context or types.SimpleNamespace(log=_Log()))


def test_the_turn_ends_with_one_line_naming_the_model_and_the_time():
    agent = _agent()
    K.turn_started(agent)
    token = K._KAME_CURRENT_AGENT.set(agent)
    try:
        for _ in range(2):
            ctx, _ = make_ctx(streaming({}))
            run(ctx)
    finally:
        K._KAME_CURRENT_AGENT.reset(token)
    line = K.reply_line(agent)
    assert line.startswith("⏱ ") and "gemini/gemini-3.8-flash (2 steps)" in line
    assert K.reply_line(agent) is None                       # one line per turn


def test_a_fallback_step_says_which_model_it_answered_for():
    agent = _agent()
    K.turn_started(agent)
    token = K._KAME_CURRENT_AGENT.set(agent)
    hop = K._KAME_FALLBACK_HOP.set({"origin": "google:gemini/gemini-3.8-flash", "to": ID})
    try:
        ctx, _ = make_ctx(streaming({}))
        run(ctx)
    finally:
        K._KAME_FALLBACK_HOP.reset(hop)
        K._KAME_CURRENT_AGENT.reset(token)
    assert "for gemini/gemini-3.8-flash" in K.reply_line(agent)
    assert K.replies_report()[0]["for"] == "gemini/gemini-3.8-flash"


@pytest.mark.parametrize("mode, shown", [("never", False), ("switches", False), ("always", True)])
def test_the_reply_notice_setting(mode, shown):
    K.set_reply_notice(mode)
    agent = _agent()
    K.turn_started(agent)
    token = K._KAME_CURRENT_AGENT.set(agent)
    try:
        ctx, _ = make_ctx(streaming({}))
        run(ctx)
    finally:
        K._KAME_CURRENT_AGENT.reset(token)
    assert bool(K.reply_line(agent)) is shown
    assert K.replies_report()                              # the panel keeps it whatever


def test_a_subordinate_agent_does_not_restart_the_turn_and_has_no_line_of_its_own():
    top = _agent()
    K.turn_started(top)
    sub = _agent(number=1, context=top.context)
    K.turn_started(sub)
    token = K._KAME_CURRENT_AGENT.set(sub)
    try:
        ctx, _ = make_ctx(streaming({}))
        run(ctx)
    finally:
        K._KAME_CURRENT_AGENT.reset(token)
    assert K.reply_line(sub) is None
    assert "gemini" in K.reply_line(top)


def test_a_background_call_is_never_named():
    agent = _agent()
    K.turn_started(agent)
    token = K._KAME_CURRENT_AGENT.set(agent)
    try:
        ctx, _ = make_ctx(streaming({}), call_type="Utility")
        run(ctx)
    finally:
        K._KAME_CURRENT_AGENT.reset(token)
    assert K.reply_line(agent) is None and not K.replies_report()


def test_the_extension_writes_the_line_to_the_log_not_the_history():
    import importlib.util
    import os
    import sys
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    helpers = types.ModuleType("helpers")
    ext = types.ModuleType("helpers.extension")

    class Extension:
        def __init__(self, agent=None):
            self.agent = agent

    ext.Extension = Extension
    sys.modules.setdefault("helpers", helpers)
    old = sys.modules.get("helpers.extension")
    sys.modules["helpers.extension"] = ext
    try:
        spec = importlib.util.spec_from_file_location(
            "kame_reply_line_ext", os.path.join(root, "extensions", "python", "monologue_end",
                                                "_90_kame_reply_line.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        if old is not None:
            sys.modules["helpers.extension"] = old
    source = open(spec.origin, encoding="utf-8").read()
    assert "context.log.log(" in source and "hist_add" not in source



# --- review fixes (2026-10-17) -------------------------------------------------

@pytest.mark.parametrize("kind, status, busy", [
    ("server", 503, True), ("timeout", 504, True), ("timeout", None, False),
    ("per_minute", 429, False), ("rate_limit", 429, False),
])
def test_busy_is_the_provider_saying_so(kind, status, busy):
    assert K._kame_busy_failure(ID, KEYS[0], kind, status) is busy


def test_a_long_prompt_gets_more_patience_and_a_thinking_model_a_generous_floor():
    assert K._kame_auto_silence(ID, False, 0) == 180.0                 # cold
    for _ in range(40):
        H.HEDGE_TRACKER.record_latency(ID, 1.0, stream=True, reasoning=False)
    assert K._kame_auto_silence(ID, False, 0) == 30.0
    assert K._kame_auto_silence(ID, False, 80_000) == 70.0
    for _ in range(40):
        H.HEDGE_TRACKER.record_latency(ID, 5.0, stream=True, reasoning=True)
    assert K._kame_auto_silence(ID, True, 0) == 180.0


def test_after_my_time_still_moves_a_busy_model_at_once(fake_models):
    K.set_fallback_models("google/gemini-3.7-flash")
    K.set_failover_after(90)
    assert K._KAME_MODEL_SWITCH == "timed"
    log = []
    ctx, _ = make_ctx(streaming({}, fail={k: overloaded for k in KEYS}, log=log))
    assert run(ctx)[0] == "fallback answer from google/gemini-3.7-flash"
    assert len(log) == 2


def test_an_explicit_choice_in_the_environment_beats_a_legacy_number(monkeypatch):
    monkeypatch.setenv("KAME_FIRST_TOKEN_PATIENCE", "40")
    monkeypatch.setenv("KAME_FIRST_TOKEN_WAIT", "auto")
    K._kame_apply_env_overrides()
    assert K._KAME_FIRST_TOKEN_MODE == "auto"


def test_the_settings_page_keeps_a_time_saved_before_the_choice_existed():
    import os
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    page = open(os.path.join(root, "webui", "config.html"), encoding="utf-8").read()
    assert "Number(config.failover_after_seconds) <= 0 ? 'never' : 'timed'" in page
    assert "Number(config.stream_silence_timeout_seconds) === 0) ? 'auto' : 'timed'" in page

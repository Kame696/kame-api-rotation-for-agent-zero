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


def test_inside_the_busy_window_the_next_call_moves_without_a_request(fake_models):
    # Owner, live test on Hermes: every reply starts on the best model; one KAME
    # knows busy is passed down the list at once, without asking a key.
    K.set_fallback_models("google/gemini-3.7-flash")
    K._kame_note_busy(ID)
    log = []
    ctx, _ = make_ctx(streaming({}, fail={k: overloaded for k in KEYS}, log=log))
    assert run(ctx)[0] == "fallback answer from google/gemini-3.7-flash"
    assert log == []


def test_after_the_busy_window_one_knock_is_enough(fake_models):
    K.set_fallback_models("google/gemini-3.7-flash")
    K._kame_note_busy(ID)
    K._KAME_BUSY_UNTIL[ID] = time.time() - 1   # the window ran out just now
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
    # 1.8.3.1 (review R5): Automatic is Hermes' 120 s ceiling for both classes (it was 180 s / 300 s cold)
    assert K._kame_silence_for(None, ctx, 1) == 120.0                 # automatic, cold
    background, _ = make_ctx(streaming({}))
    background["main_chat"] = False                                   # a compaction or utility stream
    assert K._kame_silence_for(None, background, 1) == 0.0
    ctx["kwargs"] = {"api_base": "http://localhost:11434"}
    assert K._kame_silence_for(None, ctx, 1) == 0.0                   # never a local model
    ctx["kwargs"] = {}
    K.set_first_token_wait("off")
    assert K._kame_silence_for(None, ctx, 1) == 120.0                 # 1.8.3.1 (D4): Off is Hermes' 120 s, not "no limit"
    K.set_stream_silence_timeout(40)                                 # a number = "My time"
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
    assert K._kame_auto_silence(ID, False, 0) == 120.0                 # cold (1.8.3.1 review R5: Hermes' ceiling; was 180)
    for _ in range(40):
        H.HEDGE_TRACKER.record_latency(ID, 1.0, stream=True, reasoning=False)
    assert K._kame_auto_silence(ID, False, 0) == 30.0
    assert K._kame_auto_silence(ID, False, 80_000) == 70.0
    for _ in range(40):
        H.HEDGE_TRACKER.record_latency(ID, 5.0, stream=True, reasoning=True)
    assert K._kame_auto_silence(ID, True, 0) == 120.0                  # a thinking model: floor and ceiling are 120 s


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


# --- a model that is slow right now (Hermes decisions/0028 D11) ----------------
# Owner's live test on Hermes, 2026-10-18: gemini-3.8-flash took about two minutes
# to a one-word answer on every key while gemini-3.7-flash answered in seconds.

QUICK_SLOW = {False: (0.3, 0.3, 0.3), True: (0.3, 0.3, 0.3)}


def test_a_silent_model_falls_back_at_its_slow_bound(fake_models, monkeypatch):
    monkeypatch.setattr(K, "_KAME_SLOW_S", QUICK_SLOW)
    K.set_fallback_models("google/gemini-3.7-flash")
    log = []
    ctx, _ = make_ctx(streaming({k: 3.0 for k in KEYS}, log=log))
    started = time.monotonic()
    assert run(ctx)[0] == "fallback answer from google/gemini-3.7-flash"
    assert time.monotonic() - started < 1.5
    assert any(entry.startswith("cancelled:") for entry in log)     # the slow request was cut
    assert K._KAME_BUSY_UNTIL[ID] > time.time() + 20                # and the model left alone


def test_without_a_list_a_slow_model_is_waited_for(monkeypatch):
    monkeypatch.setattr(K, "_KAME_SLOW_S", QUICK_SLOW)
    ctx, _ = make_ctx(streaming({k: 0.6 for k in KEYS}))
    assert run(ctx)[0].startswith("answer from")


def test_never_waits_for_a_slow_model(fake_models, monkeypatch):
    monkeypatch.setattr(K, "_KAME_SLOW_S", QUICK_SLOW)
    K.set_fallback_models("google/gemini-3.7-flash")
    K.set_model_switch("never")
    ctx, _ = make_ctx(streaming({k: 0.6 for k in KEYS}))
    assert run(ctx)[0].startswith("answer from")


def test_when_the_next_model_is_known_slow_too_the_first_is_waited_for(fake_models, monkeypatch):
    monkeypatch.setattr(K, "_KAME_SLOW_S", QUICK_SLOW)
    K.set_fallback_models("google/gemini-3.7-flash")
    K._kame_note_busy("google:gemini/gemini-3.7-flash")   # as LiteLLM names the running model
    ctx, _ = make_ctx(streaming({k: 0.6 for k in KEYS}))
    assert K._kame_slow_after(ctx, ID, False) == 0.0
    assert run(ctx)[0].startswith("answer from")


def test_set_time_is_the_users_number_and_automatic_learns(fake_models):
    K.set_fallback_models("google/gemini-3.7-flash")
    ctx, _ = make_ctx(streaming({}))
    assert K._kame_slow_after(ctx, ID, False) == pytest.approx(30.0, abs=0.01)   # nothing learned yet
    K.set_model_switch("timed")
    K.set_failover_after(45)
    assert K._kame_slow_after(ctx, ID, False) == 45.0
    K.set_model_switch("auto")
    for _ in range(40):
        H.HEDGE_TRACKER.record_latency(ID, 30.0, stream=True, reasoning=False)
    assert K._kame_slow_after(ctx, ID, False) == pytest.approx(45.0, abs=0.01)   # the ceiling


def test_a_background_call_never_falls_back_for_slowness(fake_models):
    K.set_fallback_models("google/gemini-3.7-flash")
    ctx, _ = make_ctx(streaming({}), call_type="Utility")
    assert K._kame_slow_after(ctx, ID, False) == 0.0


def test_a_slow_model_is_left_alone_for_minutes(fake_models, monkeypatch):
    monkeypatch.setattr(K, "_KAME_SLOW_S", QUICK_SLOW)
    K.set_hedge_mode("off")     # Saver: no race with the next model, the slow cut alone
    K.set_fallback_models("google/gemini-3.7-flash")
    ctx, _ = make_ctx(streaming({k: 3.0 for k in KEYS}))
    run(ctx)
    assert K._KAME_BUSY_WINDOW[ID] == K._KAME_SLOW_FIRST_S
    K._KAME_BUSY_UNTIL[ID] = time.time() - 60          # caught again a minute after it ended
    K._kame_note_busy(ID, first=K._KAME_SLOW_FIRST_S, ceiling=K._KAME_SLOW_MAX_S)
    assert K._KAME_BUSY_WINDOW[ID] == 2 * K._KAME_SLOW_FIRST_S


# --- D12: the next model races a silent reply -------------------------------------

def test_the_next_model_races_a_silent_reply_and_answers_it(fake_models, monkeypatch):
    monkeypatch.setattr(K, "_KAME_SLOW_S", {False: (5.0, 5.0, 5.0), True: (5.0, 5.0, 5.0)})
    monkeypatch.setattr(K, "_KAME_CROSS_COLD_S", 0.2)
    K.set_fallback_models("google/gemini-3.7-flash")
    log = []
    ctx, _ = make_ctx(streaming({k: 3.0 for k in KEYS}, log=log))
    started = time.monotonic()
    assert run(ctx)[0] == "fallback answer from google/gemini-3.7-flash"
    assert time.monotonic() - started < 1.5               # no slow bound waited, no restart
    assert any(entry.startswith("cancelled:") for entry in log)
    assert ctx.get("answered_by") == "google:gemini-3.7-flash"
    assert K._KAME_BUSY_UNTIL[ID] > time.time()          # the next replies go straight on


def test_a_first_model_in_time_is_never_raced(fake_models, monkeypatch):
    monkeypatch.setattr(K, "_KAME_CROSS_COLD_S", 0.5)
    K.set_fallback_models("google/gemini-3.7-flash")
    ctx, _ = make_ctx(streaming({k: 0.05 for k in KEYS}))
    assert run(ctx)[0].startswith("answer from")
    assert fake_models == []


def test_saver_never_races_another_model(fake_models):
    K.set_fallback_models("google/gemini-3.7-flash")
    K.set_hedge_mode("off")
    ctx, _ = make_ctx(streaming({}))
    assert K._kame_cross_entry(ctx, ID, False, 30.0) is None
    K.set_hedge_mode("adaptive")
    [(entry, after)] = K._kame_cross_entry(ctx, ID, False, 30.0)
    assert entry["label"] == "google/gemini-3.7-flash" and after == 12.0


def test_when_the_raced_model_is_silent_too_the_one_after_it_joins(fake_models, monkeypatch):
    K.set_fallback_models("google/gemini-3.7-flash\ngoogle/gemini-3.6-flash")
    ctx, _ = make_ctx(streaming({}))
    assert [at for _e, at in K._kame_cross_entry(ctx, ID, False, 30.0)] == [12.0, 18.0]
    monkeypatch.setattr(K, "_KAME_SLOW_S", {False: (5.0, 5.0, 5.0), True: (5.0, 5.0, 5.0)})
    monkeypatch.setattr(K, "_KAME_CROSS_COLD_S", 0.2)
    monkeypatch.setattr(K, "_KAME_CROSS_STEP_S", 0.2)
    FALLBACK_BEHAVIOUR["google/gemini-3.7-flash"] = "slow"
    ctx, _ = make_ctx(streaming({k: 3.0 for k in KEYS}))
    started = time.monotonic()
    assert run(ctx)[0] == "fallback answer from google/gemini-3.6-flash"
    assert time.monotonic() - started < 2.0
    assert ctx.get("answered_by") == "google:gemini-3.6-flash"


def test_a_next_model_that_fails_never_rests_the_first_models_keys(fake_models, monkeypatch):
    monkeypatch.setattr(K, "_KAME_SLOW_S", {False: (5.0, 5.0, 5.0), True: (5.0, 5.0, 5.0)})
    monkeypatch.setattr(K, "_KAME_CROSS_COLD_S", 0.1)
    K.set_fallback_models("google/gemini-3.7-flash")
    FALLBACK_BEHAVIOUR["google/gemini-3.7-flash"] = "error"
    ctx, _ = make_ctx(streaming({k: 0.6 for k in KEYS}))
    assert run(ctx)[0].startswith("answer from")         # the first model still answers
    assert K._pool_has_ready_key(ID, KEYS)


# --- review fixes (2026-10-18) ----------------------------------------------------

def test_the_chat_model_listed_again_is_never_raced_against_itself(fake_models):
    K.set_fallback_models("google/gemini-3.8-flash\ngoogle/gemini-3.7-flash")
    ctx, _ = make_ctx(streaming({}))
    [(entry, _at)] = K._kame_cross_entry(ctx, ID, False, 30.0)
    assert entry["label"] == "google/gemini-3.7-flash"


def test_an_answer_ends_the_busy_window_under_either_spelling():
    K._kame_note_busy("google:gemini-3.8-flash")
    K._kame_clear_busy(ID)
    assert "google:gemini-3.8-flash" not in K._KAME_BUSY_UNTIL


def test_a_next_model_win_is_not_recorded_on_the_first_pool(fake_models, monkeypatch):
    monkeypatch.setattr(K, "_KAME_SLOW_S", {False: (5.0, 5.0, 5.0), True: (5.0, 5.0, 5.0)})
    monkeypatch.setattr(K, "_KAME_CROSS_COLD_S", 0.2)
    K.set_fallback_models("google/gemini-3.7-flash")
    ctx, _ = make_ctx(streaming({k: 3.0 for k in KEYS}))
    before = len(K._KAME_REPLIES)
    run(ctx)
    assert len(K._KAME_REPLIES) == before        # the nested call names it, not the outer one twice


def test_a_raced_model_keeps_its_own_whole_bound(fake_models, monkeypatch):
    # the slow bound fires 0.4 s after the request; the next model joined at 0.2 s
    # and answers at 0.55 s - it is not cut at 0.4 s.
    monkeypatch.setattr(K, "_KAME_SLOW_S", {False: (0.4, 0.4, 0.4), True: (0.4, 0.4, 0.4)})
    monkeypatch.setattr(K, "_KAME_CROSS_COLD_S", 0.2)
    K.set_fallback_models("google/gemini-3.7-flash")
    FALLBACK_BEHAVIOUR["google/gemini-3.7-flash"] = "late"
    ctx, _ = make_ctx(streaming({k: 3.0 for k in KEYS}))
    assert run(ctx)[0] == "fallback answer from google/gemini-3.7-flash"
    assert ctx.get("answered_by") == "google:gemini-3.7-flash"

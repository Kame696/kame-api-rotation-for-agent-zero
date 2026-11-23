"""1.9.0.5 (E3, decisions/0036): a pool reset says what it cleared - counts only, never key material.

`reset_pool()` still returns the number of key rows it started again; before clearing it works out how many
physical keys were resting (by kind), the longest hold left, the account holds and the busy windows still open,
emits ONE `setting` event with that sentence and keeps it for the command and the page to show.
"""
from __future__ import annotations

import asyncio
import json
import time

import pytest

from test_v1_8_2_7_speed_fallback import clean  # noqa: F401  (autouse: resets the carousel state)
import kame_engine as K

KEYS = [f"AIzaSyRESET{i}" + "0" * 28 for i in range(4)]
A, B = "gemini:model-a", "gemini:model-b"


@pytest.fixture(autouse=True)
def pools(clean):  # noqa: F811
    K._KAME_ACCOUNT_PENDING.clear()
    K._KAME_BUSY_UNTIL.clear()
    for ident in (A, B):
        K._get_identity_state(ident, KEYS)
    yield
    K._KAME_BUSY_UNTIL.clear()


def sentence():
    rows = [r for r in K._KJ.EVENTS.recent(50) if r["kind"] == "setting" and r["reason"].startswith("pool reset")]
    assert len(rows) == 1, rows
    return rows[0]["reason"]


def test_an_empty_pool_says_nothing_was_resting():
    K.reset_pool()
    assert sentence() == "pool reset: no key was resting"
    assert K.last_reset_sentence() == "pool reset: no key was resting"


def test_the_sentence_counts_keys_by_kind_the_longest_hold_account_holds_and_busy_windows():
    K._KAME_NO_ANSWER_SINCE[A] = time.time() - 10_000.0
    for key in KEYS[:2]:
        K._mark_key_health(A, key, False, 3600.0, "daily")                       # two daily labels, 1 h
    K._mark_key_health(A, KEYS[2], False, 120.0, "per_minute", sized_by="provider")
    K._mark_key_health(A, KEYS[3], False, 300.0, "insufficient_quota")         # money: an account hold
    K._KAME_BUSY_UNTIL[A] = time.time() + 90.0
    K._KAME_BUSY_UNTIL[B] = time.time() - 5.0                                  # closed: not counted
    count = K.reset_pool()
    assert count == 8                                                          # key rows: 4 keys on 2 models
    assert sentence() == ("pool reset: 4 keys resting (daily 2, out of credit 1, per minute 1), "
                          "longest 60 min, 1 account hold, 1 busy window")
    assert K._KAME_ACCOUNT_HOLDS == {}
    assert K._pool_has_ready_key(A, KEYS) and K._pool_has_ready_key(B, KEYS)


def test_one_key_resting_on_two_models_counts_once():
    K._mark_key_health(A, KEYS[0], False, 120.0, "per_minute", sized_by="provider")
    K._mark_key_health(B, KEYS[0], False, 120.0, "per_minute", sized_by="provider")
    K.reset_pool()
    assert sentence().startswith("pool reset: 1 key resting (per minute 1), longest 2 min")


def test_the_sentence_holds_no_key_material_and_the_event_is_one_row():
    K._mark_key_health(A, KEYS[0], False, 300.0, "daily")
    K.reset_pool()
    blob = json.dumps(K._KJ.EVENTS.recent(50)) + K.last_reset_sentence()
    assert all(key not in blob and key[:12] not in blob for key in KEYS)
    assert len([r for r in K._KJ.EVENTS.recent(50) if r["reason"].startswith("pool reset")]) == 1


def test_the_page_action_and_the_commands_show_the_sentence(monkeypatch, tmp_path):
    import sys
    import types
    K._mark_key_health(A, KEYS[0], False, 600.0, "per_minute", sized_by="provider")
    helpers_api = types.ModuleType("helpers.api")
    helpers_api.ApiHandler = type("ApiHandler", (), {})
    helpers_api.Input = helpers_api.Output = helpers_api.Request = object
    monkeypatch.setitem(sys.modules, "helpers.api", helpers_api)
    pkg = types.ModuleType("usr.plugins.api_rotation_by_kame")
    pkg.kame_engine = K
    for name in ("usr", "usr.plugins"):
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    monkeypatch.setitem(sys.modules, "usr.plugins.api_rotation_by_kame", pkg)
    monkeypatch.setitem(sys.modules, "usr.plugins.api_rotation_by_kame.kame_engine", K)
    import importlib
    import kame_keys as KK
    pkg.kame_keys = KK
    monkeypatch.setitem(sys.modules, "usr.plugins.api_rotation_by_kame.kame_keys", KK)
    dotenv = types.ModuleType("helpers.dotenv")
    dotenv.get_dotenv_file_path = lambda: str(tmp_path / ".env")
    dotenv.save_dotenv_value = lambda *a: None
    monkeypatch.setitem(sys.modules, "helpers.dotenv", dotenv)
    monkeypatch.setattr(sys.modules["helpers"], "dotenv", dotenv, raising=False)
    action = importlib.import_module("api.kame_pool_action")
    out = asyncio.run(action.KamePoolAction().process({"action": "clear_pool"}, None))
    assert out["ok"] and "pool reset: 1 key resting (per minute 1)" in out["message"]
    K._mark_key_health(A, KEYS[1], False, 600.0, "per_minute", sized_by="provider")
    command = importlib.import_module("commands.kame_command")
    shown = command.run({"invocation": {"raw_arguments": "clear-pool"}})
    assert "pool reset: 1 key resting" in json.dumps(shown)
    K._mark_key_health(A, KEYS[2], False, 600.0, "per_minute", sized_by="provider")
    keys_command = importlib.import_module("commands.kame_keys_command")
    shown = keys_command.run({"invocation": {"raw_arguments": "reset"}})
    assert "pool reset: 1 key resting" in json.dumps(shown)

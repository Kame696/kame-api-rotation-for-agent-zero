"""1.9.0.5 (D1, decisions/0036): leave Agent Zero clean.

KAME keeps a provider's keys in ONE `usr/.env` line, comma-separated (`API_KEY_GOOGLE=k1,k2,k3`). If the plugin
is removed that line stays, and Agent Zero would send the whole list as one key. `/kame-keys undo [provider]`
(and the page's "Remove API Rotation's keys" button) keeps only the FIRST key of every such line, after a
backup, and says what it did in counts and masked keys.
"""
from __future__ import annotations

import asyncio
import importlib
import json
import re
import sys
import types

import pytest

import test_v1_8_1_1_reliability  # noqa: F401  (stubs the Agent Zero modules the engine imports)
import kame_keys as KK

G = [f"AIzaSyUNDO{i}".ljust(39, "g") for i in range(3)]
O = ["sk-undo-" + c * 30 for c in "ab"]
ENV = (f"# my keys\nOTHER_SETTING=keep me\nAPI_KEY_GOOGLE={','.join(G)}\n"
       f"API_KEY_OPENAI={','.join(O)}\nAPI_KEY_ANTHROPIC=sk-ant-single-key-0000000000\nZED=also kept\n")


@pytest.fixture()
def env(tmp_path):
    path = tmp_path / ".env"
    path.write_text(ENV, encoding="utf-8")

    def save(name, value):
        text = path.read_text(encoding="utf-8")
        path.write_text(re.sub(rf"^{name}=.*$", lambda m: f"{name}={value}", text, flags=re.M), encoding="utf-8")
    return path, save


def lines(path):
    return dict(line.split("=", 1) for line in path.read_text(encoding="utf-8").splitlines() if "=" in line)


def test_every_managed_line_keeps_only_its_first_key_and_the_rest_of_the_file_is_untouched(env, tmp_path):
    path, save = env
    text = KK.undo(path, "", save)
    now = lines(path)
    assert now["API_KEY_GOOGLE"] == G[0] and now["API_KEY_OPENAI"] == O[0]
    assert now["API_KEY_ANTHROPIC"] == "sk-ant-single-key-0000000000"
    assert now["OTHER_SETTING"] == "keep me" and now["ZED"] == "also kept"
    assert path.read_text(encoding="utf-8").startswith("# my keys\n")
    backups = sorted(tmp_path.glob(".env.kame-*.bak"))
    assert len(backups) == 1 and backups[0].read_text(encoding="utf-8") == ENV
    # counts and masked keys, never a key; and what to do next
    assert "API_KEY_GOOGLE" in text and "API_KEY_OPENAI" in text
    assert "kept 1 of 3" in text and "kept 1 of 2" in text
    assert KK.mask(G[1]) in text and KK.mask(G[2]) in text and KK.mask(O[1]) in text
    assert all(key not in text for key in G + O)
    assert "you can now remove the plugin" in text.lower()
    assert ".env.kame-" in text            # where the other keys are kept


def test_a_named_provider_touches_only_its_own_line(env):
    path, save = env
    text = KK.undo(path, "gemini", save)           # an alias, like everywhere else
    now = lines(path)
    assert now["API_KEY_GOOGLE"] == G[0]
    assert now["API_KEY_OPENAI"] == ",".join(O)
    assert "API_KEY_OPENAI" not in text


def test_nothing_to_undo_changes_nothing_and_writes_no_backup(tmp_path):
    path = tmp_path / ".env"
    path.write_text("API_KEY_GOOGLE=" + G[0] + "\nZED=1\n", encoding="utf-8")
    text = KK.undo(path, "", lambda *a: pytest.fail("nothing may be written"))
    assert "Nothing to undo" in text
    assert list(tmp_path.glob(".env.kame-*.bak")) == []


def test_a_provider_without_a_line_is_said(env):
    path, save = env
    text = KK.undo(path, "mistral", save)
    assert "no key line for `mistral`" in text.lower()
    assert lines(path)["API_KEY_GOOGLE"] == ",".join(G)


def test_a_failed_backup_stops_before_any_write(env, monkeypatch):
    path, save = env
    monkeypatch.setattr(KK, "backup", lambda p: None)
    text = KK.undo(path, "", lambda *a: pytest.fail("no write without a backup"))
    assert "Could not create a backup" in text and "Nothing changed" in text


def stub_host(monkeypatch, env_file, saved):
    for name in ("usr", "usr.plugins"):
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    import kame_engine as E
    pkg = types.ModuleType("usr.plugins.api_rotation_by_kame")
    pkg.kame_keys, pkg.kame_engine = KK, E
    monkeypatch.setitem(sys.modules, "usr.plugins.api_rotation_by_kame", pkg)
    monkeypatch.setitem(sys.modules, "usr.plugins.api_rotation_by_kame.kame_keys", KK)
    monkeypatch.setitem(sys.modules, "usr.plugins.api_rotation_by_kame.kame_engine", E)
    dotenv = types.ModuleType("helpers.dotenv")
    dotenv.get_dotenv_file_path = lambda: str(env_file)
    dotenv.save_dotenv_value = saved
    helpers = sys.modules.setdefault("helpers", types.ModuleType("helpers"))
    monkeypatch.setitem(sys.modules, "helpers.dotenv", dotenv)
    monkeypatch.setattr(helpers, "dotenv", dotenv, raising=False)
    api = types.ModuleType("helpers.api")
    api.ApiHandler = type("ApiHandler", (), {})
    api.Input = api.Output = api.Request = object
    monkeypatch.setitem(sys.modules, "helpers.api", api)


def test_the_command_verb(env, monkeypatch):
    path, save = env
    stub_host(monkeypatch, path, save)
    command = importlib.import_module("commands.kame_keys_command")
    shown = command.run({"invocation": {"raw_arguments": "undo"}})
    blob = json.dumps(shown)
    assert "you can now remove the plugin" in blob.lower()
    assert lines(path)["API_KEY_GOOGLE"] == G[0]
    assert all(key not in blob for key in G + O)
    again = command.run({"invocation": {"raw_arguments": "undo"}})
    assert "Nothing to undo" in json.dumps(again)
    only = command.run({"invocation": {"raw_arguments": "undo openai"}})
    assert "Nothing to undo" in json.dumps(only)


def test_the_page_action_is_the_same_undo(env, monkeypatch):
    path, save = env
    stub_host(monkeypatch, path, save)
    action = importlib.import_module("api.kame_pool_action")
    out = asyncio.run(action.KamePoolAction().process({"action": "undo_keys"}, None))
    assert out["ok"] and "you can now remove the plugin" in out["message"].lower()
    assert lines(path)["API_KEY_OPENAI"] == O[0]
    assert all(key not in out["message"] for key in G + O)


def test_the_page_has_the_button_with_a_confirm_and_the_help_names_the_command():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    page = (root / "webui" / "kame-panel.html").read_text(encoding="utf-8")
    assert "Remove API Rotation's keys" in page and "confirming = 'undo_keys'" in page
    assert "act('undo_keys')" in page
    help_text = (root / "commands" / "kame_command.py").read_text(encoding="utf-8")
    assert "/kame-keys undo" in help_text
    yaml = (root / "commands" / "kame_keys.command.yaml").read_text(encoding="utf-8")
    assert "undo" in yaml

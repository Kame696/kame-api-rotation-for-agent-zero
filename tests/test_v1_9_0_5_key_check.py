"""1.9.0.5 (D2, decisions/0036): the keys are checked when they are added, never at start.

`shape_problem(provider, key)` is pure and offline (Google's two shapes; every other provider: not empty).
`/kame-keys add` and `import` drop empty items and repeats, name each key of the wrong shape and do not write
it, then ask Google ONCE per NEW Gemini key (`GET .../v1beta/models?pageSize=1`, the key in the
`x-goog-api-key` header, never in the URL, 10 s). The HTTP call is one small function the tests replace.
"""
from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest

import test_v1_8_1_1_reliability  # noqa: F401  (stubs the Agent Zero modules the engine imports)
import kame_keys as KK

PROBE_URL = "https://generativelanguage.googleapis.com/v1beta/models?pageSize=1"


def gk(n: int) -> str:
    """A key shaped like Google's classic one: AIza + 35 characters = 39."""
    return f"AIzaSyTEST{n:02d}".ljust(39, "x")


def aq(n: int) -> str:
    """Google's newer shape: AQ. and at least 40 characters in all."""
    return f"AQ.Ab{n:02d}".ljust(48, "y")


class Http:
    """The replaced HTTP function: records every call, answers per key."""

    def __init__(self, answers=None, default=200):
        self.calls = []
        self.answers = answers or {}
        self.default = default

    def __call__(self, url, headers=None, timeout=10.0):
        self.calls.append({"url": url, "headers": dict(headers or {}), "timeout": timeout})
        return self.answers.get((headers or {}).get("x-goog-api-key"), self.default)


@pytest.fixture()
def http(monkeypatch):
    fake = Http()
    monkeypatch.setattr(KK, "http_status", fake)
    return fake


@pytest.fixture()
def env(tmp_path):
    path = tmp_path / ".env"
    saved = {}

    def save(name, value):
        saved[name] = value
        path.write_text(f"{name}={value}\n", encoding="utf-8")
    return path, saved, save


def test_the_shape_rule_is_google_only_and_not_empty_for_everyone_else():
    for provider in ("google", "gemini", "Google"):
        assert KK.shape_problem(provider, gk(1)) is None
        assert KK.shape_problem(provider, "AIza" + "x" * 34) == "does not look like a Google key"      # 38
        assert KK.shape_problem(provider, "AIza" + "x" * 36) == "does not look like a Google key"      # 40: exact
        assert KK.shape_problem(provider, "sk-" + "x" * 40) == "does not look like a Google key"
    assert KK.shape_problem("google", aq(1)) is None
    assert KK.shape_problem("google", "AQ." + "x" * 36) is not None                                     # 39: too short
    assert KK.shape_problem("google", "AQ." + "x" * 60) is None                                         # a minimum, not exact
    for provider in ("openai", "deepseek", "openrouter"):
        assert KK.shape_problem(provider, "anything-at-all-123") is None
    assert KK.shape_problem("openai", "") is not None and KK.shape_problem("google", "   ") is not None


def test_a_key_of_the_wrong_shape_is_named_and_still_written(env, http):
    path, saved, save = env
    bad = "AIza" + "x" * 30 + "abcd"                       # 38 characters: ends in abcd
    text = KK.add(path, "google", [gk(1), bad], save, check=True)
    assert saved["API_KEY_GOOGLE"] == f"{gk(1)},{bad}"     # the user decides (as on the Hermes port)
    assert "key ...abcd does not look like a Google key" in text
    assert bad not in text and bad[:10] not in text
    assert len(http.calls) == 2                           # Google is asked about both


def test_a_misshapen_key_alone_is_named_and_written(env, http):
    path, saved, save = env
    text = KK.add(path, "google", ["AIza" + "x" * 30 + "zzzz"], save, check=True)
    assert saved["API_KEY_GOOGLE"].endswith("zzzz")
    assert "key ...zzzz does not look like a Google key" in text


def test_each_new_google_key_is_asked_once_with_the_key_in_a_header_never_the_url(env, http):
    path, saved, save = env
    keys = [gk(1), gk(2), aq(3)]
    text = KK.add(path, "google", keys, save, check=True)
    assert saved["API_KEY_GOOGLE"] == ",".join(keys)
    assert len(http.calls) == 3
    for call, key in zip(http.calls, keys):
        assert call["url"] == PROBE_URL and key not in call["url"]
        assert call["headers"] == {"x-goog-api-key": key}
        assert call["timeout"] == 10.0
    assert "rejected by Google" not in text and "could not check" not in text
    assert all(key not in text for key in keys)


@pytest.mark.parametrize("status", [400, 401, 403])
def test_a_key_google_refuses_is_named_and_still_written(env, http, status):
    path, saved, save = env
    http.answers = {gk(2): status}
    text = KK.add(path, "google", [gk(1), gk(2)], save, check=True)
    assert saved["API_KEY_GOOGLE"] == f"{gk(1)},{gk(2)}"
    assert text.count("rejected by Google") == 1
    assert f"...{gk(2)[-4:]}" in text and gk(2) not in text


def test_no_answer_or_a_busy_google_is_no_verdict(env, http):
    path, saved, save = env
    http.answers = {gk(1): None, gk(2): 429, gk(3): 503}
    text = KK.add(path, "google", [gk(1), gk(2), gk(3)], save, check=True)
    assert saved["API_KEY_GOOGLE"].count(",") == 2
    assert "rejected by Google" not in text
    assert "could not check 3 key(s)" in text


def test_a_key_already_in_the_pool_is_not_asked_again(env, http):
    path, saved, save = env
    save("API_KEY_GOOGLE", gk(1))
    http.calls.clear()
    KK.add(path, "google", [gk(1), gk(2)], save, check=True)
    assert [c["headers"]["x-goog-api-key"] for c in http.calls] == [gk(2)]


def test_empty_items_and_repeats_are_dropped_before_anything(env, http):
    path, saved, save = env
    KK.add(path, "google", ["", gk(1), gk(1), "  ", gk(2)], save, check=True)
    assert saved["API_KEY_GOOGLE"] == f"{gk(1)},{gk(2)}"
    assert len(http.calls) == 2


def test_other_providers_are_never_asked(env, http):
    path, saved, save = env
    KK.add(path, "openai", ["sk-" + "a" * 40], save, check=True)
    assert saved["API_KEY_OPENAI"] and http.calls == []


def test_without_check_nothing_is_asked_and_the_shape_is_still_judged(env, http):
    path, saved, save = env
    text = KK.add(path, "google", [gk(1), "AIza" + "x" * 30 + "abcd"], save)      # the default: no network
    assert http.calls == [] and saved["API_KEY_GOOGLE"].startswith(gk(1))
    assert "key ...abcd does not look like a Google key" in text


def test_the_real_http_function_reduces_every_failure_to_none_and_sends_the_key_in_a_header(monkeypatch):
    import urllib.request
    seen = {}

    class Reply:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_open(request, timeout=None):
        seen["url"], seen["headers"], seen["timeout"] = request.full_url, dict(request.header_items()), timeout
        return Reply()
    monkeypatch.setattr(urllib.request, "urlopen", fake_open)
    assert KK.http_status(PROBE_URL, {"x-goog-api-key": "KEY-VALUE"}, 10.0) == 200
    assert "KEY-VALUE" not in seen["url"] and seen["headers"].get("X-goog-api-key") == "KEY-VALUE"
    assert seen["timeout"] == 10.0

    def boom(request, timeout=None):
        raise OSError("no network")
    monkeypatch.setattr(urllib.request, "urlopen", boom)
    assert KK.http_status(PROBE_URL, {"x-goog-api-key": "KEY-VALUE"}) is None


def test_the_command_checks_on_add_and_on_import(monkeypatch, tmp_path, http):
    for name in ("usr", "usr.plugins"):
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    import kame_engine as E
    pkg = types.ModuleType("usr.plugins.api_rotation_by_kame")
    pkg.kame_keys, pkg.kame_engine = KK, E
    monkeypatch.setitem(sys.modules, "usr.plugins.api_rotation_by_kame", pkg)
    monkeypatch.setitem(sys.modules, "usr.plugins.api_rotation_by_kame.kame_keys", KK)
    monkeypatch.setitem(sys.modules, "usr.plugins.api_rotation_by_kame.kame_engine", E)
    env_file = tmp_path / ".env"
    dotenv = types.ModuleType("helpers.dotenv")
    dotenv.get_dotenv_file_path = lambda: str(env_file)
    dotenv.save_dotenv_value = lambda name, value: env_file.write_text(f"{name}={value}\n", encoding="utf-8")
    helpers = sys.modules.setdefault("helpers", types.ModuleType("helpers"))
    monkeypatch.setitem(sys.modules, "helpers.dotenv", dotenv)
    monkeypatch.setattr(helpers, "dotenv", dotenv, raising=False)
    import importlib
    command = importlib.import_module("commands.kame_keys_command")
    http.answers = {gk(2): 400}
    shown = command.run({"invocation": {"raw_arguments": f"add google {gk(1)},{gk(2)}"}})
    blob = json.dumps(shown)
    assert len(http.calls) == 2 and "rejected by Google" in blob
    assert gk(1) not in blob and gk(2) not in blob
    source = tmp_path / "keys.txt"
    source.write_text(f"API_KEY_GOOGLE={gk(5)},{gk(6)}\n", encoding="utf-8")
    http.calls.clear()
    shown = command.run({"invocation": {"raw_arguments": f"import google {source}"}})
    assert len(http.calls) == 2 and "rejected by Google" not in json.dumps(shown)

"""1.8.1.4 - A0 port: a busy server is a server, and its stated wait is obeyed;
the plaintext .env backup is owner-only from birth.

The first half was found by replaying every refusal the Hermes suite hands its
classifier through both real engines: a 429 saying "overloaded" rested a healthy
key 30s as a throttle here and 1s as a busy server on Hermes, and a 5xx with
`Retry-After: 7` rested 1s here and 7s there. Hermes' rules, ported: busy prose
is a server unless a structured type says rate_limit (the Kimi case), and on a
server failure only a retry instruction counts (R26), never quota-reset
telemetry.

`/kame-keys add|import` copies usr/.env aside before changing it. The copy was
created with `open("xb")` (the umask's 0644) and only then given the .env's own
mode by copystat, so for that moment every key sat in a world-readable file.

    python -m pytest tests/test_v1_8_1_4.py
"""
import importlib.util
import os
import stat
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _keys():
    spec = importlib.util.spec_from_file_location("kame_keys_under_test_1814", ROOT / "kame_keys.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
def test_the_env_backup_is_owner_only_from_the_moment_it_exists(tmp_path, monkeypatch):
    keys = _keys()
    env = tmp_path / ".env"
    env.write_text("API_KEY_OPENAI=sk-one,sk-two\n")
    env.chmod(0o600)
    monkeypatch.setattr(keys.shutil, "copystat", lambda *a, **k: None)
    old = os.umask(0o022)
    try:
        name = keys.backup(env)
    finally:
        os.umask(old)
    assert name
    backup = tmp_path / name
    assert backup.read_bytes() == env.read_bytes()
    assert stat.S_IMODE(backup.stat().st_mode) == 0o600


def test_a_second_backup_in_the_same_instant_gets_its_own_name(tmp_path):
    keys = _keys()
    env = tmp_path / ".env"
    env.write_text("API_KEY_OPENAI=sk-one\n")
    first, second = keys.backup(env), keys.backup(env)
    assert first and second and first != second


# -- busy servers (engine) ----------------------------------------------------
if not any(name in sys.modules for name in ("test_v1_8_1_1_reliability", "tests.test_v1_8_1_1_reliability")):
    import test_v1_8_1_1_reliability  # noqa: F401,E402  (installs the Agent Zero stubs)
import kame_engine as engine  # noqa: E402


class _Refusal(Exception):
    def __init__(self, message, status, body=None, headers=None):
        super().__init__(message)
        self.status_code = status
        if body is not None:
            self.body = body
        if headers is not None:
            self.headers = headers


def test_a_429_that_says_overloaded_is_a_busy_server():
    delay, kind, status = engine._classify_error(
        _Refusal("overloaded", 429, {"error": {"code": 429, "message": "overloaded"}}))
    assert (kind, status) == ("server", 429)
    assert delay == engine._KAME_SERVER_BASE_S


def test_a_structured_rate_limit_type_still_wins_over_overloaded_prose():
    # Kimi: HTTP 429, type rate_limit_error, "The engine is currently overloaded".
    exc = _Refusal("The engine is currently overloaded, please try again later.", 429,
                   {"error": {"type": "rate_limit_error",
                              "message": "The engine is currently overloaded, please try again later."}})
    _delay, kind, _status = engine._classify_error(exc)
    assert kind == "per_minute"


@pytest.mark.parametrize("status", [500, 502, 503, 504, 529])
def test_a_server_failure_that_names_its_wait_is_obeyed(status):
    exc = _Refusal("Service temporarily unavailable", status, headers={"retry-after": "7"})
    assert engine._classify_error(exc)[:2] == (7.0, "server")
    assert engine._delay_source(exc, "server") == "provider"


def test_retry_after_ms_is_read_too():
    exc = _Refusal("Overloaded", 529, headers={"retry-after-ms": "2500"})
    assert engine._classify_error(exc)[:2] == (2.5, "server")


@pytest.mark.parametrize("status", [500, 503, 529])
def test_quota_reset_telemetry_is_not_a_server_retry_instruction(status):
    exc = _Refusal("Service temporarily unavailable", status,
                   headers={"x-ratelimit-reset-requests": "180s", "x-ratelimit-remaining-requests": "999"})
    assert engine._classify_error(exc)[:2] == (engine._KAME_SERVER_BASE_S, "server")
    assert engine._delay_source(exc, "server") == "kame"

"""1.8.1.5 - A0 port: `/kame-keys add|import` writes only what looks like a key.

`split_keys` handed the .env writer every token it was given. Pasting
`minhas chaves: K1, K2` wrote `minhas` and `chaves:` into API_KEY_<P> beside the
keys; a whole `OPENAI_API_KEY=sk-...` line went in as one "key"; a key carrying
a zero-width space or smart quotes from a mangled copy went in as is, and each
of them then sat in the pool failing at the provider. The Hermes port has
filtered pastes since its first import command (core/keys.py); this is that
filter, ported: unwrap a BOM, a `NAME=value` and matching quotes, then keep a
token only if it is 16..512 printable-ASCII characters and not a URL or comment.
Tokens long enough to have been meant as a key are reported, masked.

    python -m pytest tests/test_v1_8_1_5.py
"""
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location("kame_keys_under_test_1815", ROOT / "kame_keys.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


kk = _load()
K1 = "sk-proj-AAAAAAAAAAAAAAAAAAAAAAAA"
K2 = "sk-proj-BBBBBBBBBBBBBBBBBBBBBBBB"


def test_the_words_around_a_paste_are_not_keys():
    assert kk.pasted_keys(f"minhas chaves: {K1}, {K2}") == ([K1, K2], [])


@pytest.mark.parametrize("wrapped", [
    f"OPENAI_API_KEY={K1}", f"OPENAI_API_KEY: {K1}", f'"{K1}"', f"'{K1}'", f"`{K1}`", f"﻿{K1}",
])
def test_a_wrapped_key_is_unwrapped(wrapped):
    assert kk.pasted_keys(wrapped) == ([K1], [])


@pytest.mark.parametrize("mangled", [
    K1 + "​", "“" + K1 + "”", K1[:10] + "�" + K1[10:], K1[:10] + "é" + K1[10:],
])
def test_a_mangled_key_is_rejected_not_written(mangled):
    assert kk.pasted_keys(mangled) == ([], [mangled])


@pytest.mark.parametrize("junk", ["https://api.openai.com/v1/chat", "--------------------------", "#" * 20])
def test_long_things_that_are_not_keys_are_rejected(junk):
    keys, rejected = kk.pasted_keys(junk)
    assert keys == [] and rejected


def test_repeats_collapse_in_order():
    assert kk.pasted_keys(f"{K2},{K1};{K2}") == ([K2, K1], [])


def test_import_reports_what_it_skipped():
    rejected = []
    provider, found = kk.parse_import(f'API_KEY_OPENAI="{K1},{K2}​"\n', "", rejected)
    assert (provider, found) == ("openai", [K1])
    assert rejected == [K2 + "​"]


def test_import_without_the_list_still_filters():
    assert kk.parse_import(f"minhas chaves {K1}", "openai") == ("openai", [K1])


def test_add_names_the_skipped_token_masked(tmp_path):
    env = tmp_path / ".env"
    saved = {}
    message = kk.add(env, "openai", [K1], lambda name, value: saved.__setitem__(name, value),
                     [K2 + "​"])
    assert saved == {"API_KEY_OPENAI": K1}
    assert "does not look like an API key" in message
    assert K2 not in message


def test_nothing_written_when_every_token_was_refused(tmp_path):
    env = tmp_path / ".env"
    saved = {}
    message = kk.add(env, "openai", [], lambda name, value: saved.__setitem__(name, value), [K1 + "​"])
    assert saved == {}
    assert "No key found" in message and "does not look like an API key" in message


def test_an_existing_line_is_not_filtered_by_a_merge(tmp_path):
    # Only what the person is adding is judged; an old value already in the
    # .env stays exactly as it was, whatever it looks like.
    env = tmp_path / ".env"
    env.write_text("API_KEY_OPENAI=short-old\n", encoding="utf-8")
    saved = {}
    kk.add(env, "openai", [K1], lambda name, value: saved.__setitem__(name, value))
    assert saved == {"API_KEY_OPENAI": f"short-old,{K1}"}


# -- A rest never outlasts the ceiling measured from now ------------------------
#
# Holds are absolute wall-clock deadlines. Every one is stored at most
# `_KAME_MAX_HOLD_S` ahead of the moment it was set, but a wall clock that steps
# back afterwards (NTP, a resume, a hand-set clock) moved it further out: a 30s
# rest read 7229s after a two-hour step, past the one-hour ceiling, on every key
# resting at that moment (experiments/clock_step_back.py; Hermes the same). A
# lowered ceiling was likewise not honoured until the key's next refusal.

if not any(name in sys.modules for name in ("test_v1_8_1_1_reliability", "tests.test_v1_8_1_1_reliability")):
    import test_v1_8_1_1_reliability  # noqa: F401,E402  (installs the Agent Zero stubs)
import kame_engine as engine  # noqa: E402


class _Clock:
    def __init__(self, now):
        self.now = now

    def time(self):
        return self.now

    def __getattr__(self, name):
        import time as _time
        return getattr(_time, name)


class _Throttle(Exception):
    status_code = 429

    def __init__(self, message="Rate limit exceeded. Please retry after 30s."):
        super().__init__(message)


@pytest.fixture
def clock(monkeypatch):
    fake = _Clock(2_000_000_000.0)
    monkeypatch.setattr(engine, "time", fake)
    return fake


def test_a_clock_stepped_back_cannot_stretch_a_rest_past_the_ceiling(clock):
    ident, key = "openai:clock-step", "sk-clock-step-00000000000001"
    engine._get_identity_state(ident, [key])
    engine._kame_decide_failure(ident, key, _Throttle())
    clock.now -= 7200.0
    engine._get_identity_state(ident, [key])
    assert engine._next_recovery_seconds(ident, [key]) <= engine._KAME_MAX_HOLD_S


def test_an_account_hold_is_bounded_the_same_way(clock):
    ident, key = "openai:clock-step-account", "sk-clock-step-00000000000002"
    engine._get_identity_state(ident, [key])
    engine._kame_decide_failure(ident, key, _Throttle("You exceeded your current quota (insufficient_quota)"))
    clock.now -= 7200.0
    engine._get_identity_state(ident, [key])
    assert engine._next_recovery_seconds(ident, [key]) <= engine._KAME_MAX_HOLD_S


def test_a_lowered_ceiling_applies_to_a_rest_already_running(clock, monkeypatch):
    ident, key = "openai:clock-dial", "sk-clock-dial-000000000000003"
    engine._get_identity_state(ident, [key])
    engine._kame_decide_failure(ident, key, _Throttle("Rate limit exceeded. Please retry after 3000s."))
    monkeypatch.setattr(engine, "_KAME_MAX_HOLD_S", 600.0)
    engine._get_identity_state(ident, [key])
    assert engine._next_recovery_seconds(ident, [key]) <= 600.0


def test_an_ordinary_rest_is_untouched(clock):
    ident, key = "openai:clock-plain", "sk-clock-plain-00000000000004"
    engine._get_identity_state(ident, [key])
    applied = engine._kame_decide_failure(ident, key, _Throttle())[0]
    clock.now += 1.0
    engine._get_identity_state(ident, [key])
    assert engine._next_recovery_seconds(ident, [key]) == pytest.approx(applied - 1.0)

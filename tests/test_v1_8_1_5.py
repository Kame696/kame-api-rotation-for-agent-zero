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

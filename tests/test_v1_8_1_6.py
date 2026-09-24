"""1.8.1.6 - A0 port: a stray first word is not a provider; every Unicode file imports.

`/kame-keys add minhas chaves sk-...` took `minhas` as the provider and wrote the
key into API_KEY_MINHAS, a variable nothing reads. A first word now counts as a
provider only when Agent Zero knows it (its own list, plugins included, or the
ids and aliases KAME carries) or the .env already has a key line for it. The
Hermes port checks against the host's providers the same way.

`/kame-keys import` decoded UTF-8 and UTF-16 LE only; a UTF-16 BE or UTF-32 file
became text with a NUL in every key. The Hermes port's `decode_text` is ported.

    python -m pytest tests/test_v1_8_1_6.py
"""
import codecs
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location("kame_keys_under_test_1816", ROOT / "kame_keys.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


kk = _load()
KEY = "sk-ds-1234567890123456789012"


@pytest.mark.parametrize("text", [f"minhas chaves {KEY}", f"my keys: {KEY}", f"please {KEY}"])
def test_a_word_of_the_sentence_is_not_a_provider(text):
    assert kk.split_provider(text)[0] == ""


@pytest.mark.parametrize("word", ["deepseek", "openai", "openrouter", "nvidia_nim", "gemini", "nim", "claude"])
def test_agent_zeros_providers_and_their_aliases_still_are(word):
    assert kk.split_provider(f"{word} {KEY}") == (word, KEY)


def test_a_provider_the_running_agent_zero_lists_is_accepted():
    assert kk.split_provider(f"myproxy {KEY}", known={"myproxy"})[0] == "myproxy"


def test_a_provider_the_env_already_holds_keys_for_is_accepted():
    assert kk.split_provider(f"myproxy {KEY}", env={"API_KEY_MYPROXY": "sk-old"})[0] == "myproxy"
    assert kk.split_provider(f"myproxy {KEY}", env={"API_KEY_MYPROXY": "None"})[0] == ""


def test_a_stray_word_then_asks_which_provider(tmp_path):
    provider, text = kk.split_provider(f"minhas chaves {KEY}")
    found, _rejected = kk.pasted_keys(text)
    saved = {}
    message = kk.add(tmp_path / ".env", provider, found, lambda n, v: saved.__setitem__(n, v))
    assert saved == {} and "Which provider" in message


BODY = f"API_KEY_DEEPSEEK={KEY}\n"


@pytest.mark.parametrize("bom,encoding", [
    (codecs.BOM_UTF16_BE, "utf-16-be"), (codecs.BOM_UTF16_LE, "utf-16-le"),
    (codecs.BOM_UTF32_LE, "utf-32-le"), (codecs.BOM_UTF32_BE, "utf-32-be"),
    (codecs.BOM_UTF8, "utf-8"), (b"", "utf-8"),
])
def test_every_marked_file_imports_its_key(bom, encoding):
    text = kk.decode_text(bom + BODY.encode(encoding))
    assert kk.parse_import(text) == ("deepseek", [KEY])


def test_a_file_that_is_not_text_is_refused_not_raised():
    rejected = []
    assert kk.parse_import(kk.decode_text(b"\xff" * 64 + b"\x00\x80" * 16), "deepseek", rejected)[1] == []

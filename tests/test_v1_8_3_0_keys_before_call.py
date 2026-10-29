"""1.8.3.0 (A3 / D4) - Agent Zero knows its keys before the first call.

The page used to read "0 of 0 keys ready" after a restart, because keys were only learned by a call. Now the
view also counts the keys in Agent Zero's own provider key field for every model it is configured with (chat,
utility and embedding of the configured preset, or the models Agent Zero's settings name on a version without
the `_model_config` plugin) and for every model of KAME's fallback list. They show as ready keys of a model
that is "not used yet". Fingerprints only: no key, and no piece of one, is ever in the output.

Agent Zero is stubbed (`helpers.dotenv`, `plugins._model_config...`, `helpers.settings`); no network.
"""
from __future__ import annotations

import json
import re
import sys
import types

import pytest

from test_v1_8_2_7_speed_fallback import clean  # noqa: F401  (fixture used by name)
from test_v1_8_2_9_page_store import run_store, report, STATUS_PATH  # noqa: F401
import kame_api_stub as stub
import kame_engine as K

GEM = ["AIzaSyKNOWN-gem-one-0000000000", "AIzaSyKNOWN-gem-two-0000000000", "AIzaSyKNOWN-gem-three-00000"]
OPENAI = ["sk-KNOWN-openai-key-0000000000"]
ENV = {"API_KEY_GOOGLE": ",".join(GEM), "API_KEY_OPENAI": OPENAI[0]}
RESOLVER = "plugins._model_config.helpers.model_config"


@pytest.fixture(autouse=True)
def calm(monkeypatch):
    K._KAME_IDENTITY_BASE.clear()
    K._KAME_PRIMARY["identity"] = ""
    K._KAME_IN_FLIGHT.clear()
    K._KAME_KNOWN["at"] = None
    stub.reset()
    helpers = sys.modules.get("helpers") or types.ModuleType("helpers")
    monkeypatch.setitem(sys.modules, "helpers", helpers)
    dotenv = types.ModuleType("helpers.dotenv")
    dotenv.get_dotenv_value = lambda name, default=None: ENV.get(name, default)
    monkeypatch.setitem(sys.modules, "helpers.dotenv", dotenv)
    monkeypatch.setattr(helpers, "dotenv", dotenv, raising=False)
    yield
    K._KAME_KNOWN["at"] = None
    K._KAME_PRIMARY["identity"] = ""
    stub.reset()


def preset(monkeypatch, chat=None, utility=None, embedding=None):
    """Agent Zero's `_model_config` resolver, answering for the default scope."""
    for name in ("plugins", "plugins._model_config", "plugins._model_config.helpers"):
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    mod = types.ModuleType(RESOLVER)
    mod.get_chat_model_config = lambda agent=None: dict(chat or {})
    mod.get_utility_model_config = lambda agent=None: dict(utility or {})
    mod.get_embedding_model_config = lambda agent=None: dict(embedding or {})
    monkeypatch.setitem(sys.modules, RESOLVER, mod)


def slot(provider, name, api_base=""):
    return {"provider": provider, "name": name, "api_base": api_base}


def view():
    return K.status_view()


def pool_of(v, needle):
    return next(p for p in v["pools"] if needle in p["identity"])


# -- the count ------------------------------------------------------------------------------------

def test_three_keys_before_any_call_are_three_of_three(monkeypatch):
    preset(monkeypatch, chat=slot("google", "gemini-3.5-flash"))
    v = view()
    assert v["totals"]["keys"] == 3 and v["totals"]["ready"] == 3
    assert v["totals"]["phrase"] == "3 of 3 keys ready"
    pool = pool_of(v, "gemini-3.5-flash")
    assert pool["keys"] == 3 and pool["healthy"] == 3 and pool["unused"] is True
    assert [row[1] for row in pool["key_states"]] == ["r", "r", "r"]


def test_no_key_and_no_piece_of_one_is_in_the_output(monkeypatch):
    preset(monkeypatch, chat=slot("google", "gemini-3.5-flash"), utility=slot("openai", "gpt-5.4-nano"))
    K.set_fallback_models("openai/gpt-5.4")
    text = json.dumps({"view": view(), "report": K.pool_report()}, default=str)
    for secret in GEM + OPENAI:
        for size in (len(secret), 12, 8, 6):
            assert secret[:size] not in text and secret[-size:] not in text
    for fingerprint in re.findall(r'"(k[0-9a-f]{5})"', text):
        assert fingerprint not in "".join(GEM + OPENAI)


def test_models_that_share_a_provider_key_field_share_the_keys(monkeypatch):
    preset(monkeypatch, chat=slot("google", "gemini-3.5-flash"), utility=slot("google", "gemini-3.5-flash-lite"))
    v = view()
    assert len(v["pools"]) == 2 and all(p["keys"] == 3 and p["unused"] for p in v["pools"])
    assert v["totals"]["keys"] == 3 and v["totals"]["phrase"] == "3 of 3 keys ready", "a key is one key, whatever the models"


def test_utility_embedding_and_the_fallback_list_each_bring_their_keys(monkeypatch):
    preset(monkeypatch, chat=slot("google", "gemini-3.5-flash"), utility=slot("google", "gemini-3.5-flash-lite"),
           embedding=slot("huggingface", "sentence-transformers/all-MiniLM-L6-v2"))
    K.set_fallback_models("openai/gpt-5.4")
    v = view()
    labels = [p["identity"] for p in v["pools"]]
    assert len(labels) == 3, labels                     # the local embedding model has no key: no ring
    assert v["totals"]["keys"] == 4 and v["totals"]["phrase"] == "4 of 4 keys ready"
    assert pool_of(v, "gpt-5.4")["keys"] == 1 and pool_of(v, "gpt-5.4")["unused"]


def test_your_model_comes_first_before_it_has_answered(monkeypatch):
    preset(monkeypatch, chat=slot("google", "gemini-3.5-flash"), utility=slot("openai", "gpt-5.4-nano"))
    K.set_fallback_models("openai/gpt-5.4")
    v = view()
    assert "gemini-3.5-flash" in v["pools"][0]["identity"]
    roles = [(e["role"], e["identity"]) for e in v["fallback"]["entries"]]
    assert roles[0][0] == "primary" and "gemini-3.5-flash" in roles[0][1]


def test_a_model_that_has_answered_is_not_counted_twice(monkeypatch):
    preset(monkeypatch, chat=slot("google", "gemini-3.5-flash"))
    K._get_identity_state("google:gemini/gemini-3.5-flash", GEM)       # LiteLLM's spelling, after a call
    K._KAME_PRIMARY["identity"] = "google:gemini/gemini-3.5-flash"
    v = view()
    assert len(v["pools"]) == 1 and not v["pools"][0].get("unused")
    assert v["totals"]["keys"] == 3


def test_a_key_the_used_pool_holds_is_the_same_physical_key(monkeypatch):
    preset(monkeypatch, chat=slot("google", "gemini-3.5-flash"), utility=slot("google", "gemini-3.5-flash-lite"))
    K._get_identity_state("google:gemini/gemini-3.5-flash", GEM)
    K._KAME_IDENTITY_BASE["google:gemini/gemini-3.5-flash"] = "https://generativelanguage.googleapis.com"
    K._KAME_PRIMARY["identity"] = "google:gemini/gemini-3.5-flash"
    v = view()
    assert len(v["pools"]) == 2 and v["totals"]["keys"] == 3, "the unused utility model's keys are the chat model's keys"


def test_a_rested_key_stays_rested_beside_the_unused_ones(monkeypatch):
    preset(monkeypatch, chat=slot("google", "gemini-3.5-flash"), utility=slot("openai", "gpt-5.4-nano"))
    K._get_identity_state("google:gemini/gemini-3.5-flash", GEM)
    K._KAME_PRIMARY["identity"] = "google:gemini/gemini-3.5-flash"
    K._mark_key_health("google:gemini/gemini-3.5-flash", GEM[0], False, 3600, "daily")
    t = view()["totals"]
    assert (t["keys"], t["ready"]) == (4, 3)


# -- where the models come from -------------------------------------------------------------------

def test_without_the_model_config_plugin_the_models_come_from_agent_zeros_settings(monkeypatch):
    for name in ("plugins", "plugins._model_config", "plugins._model_config.helpers", RESOLVER):
        monkeypatch.delitem(sys.modules, name, raising=False)
    helpers = sys.modules["helpers"]
    settings = types.ModuleType("helpers.settings")
    settings.get_settings = lambda: {
        "chat_model_provider": "google", "chat_model_name": "gemini-3.5-flash", "chat_model_api_base": "",
        "util_model_provider": "openai", "util_model_name": "gpt-5.4-nano",
        "embed_model_provider": "huggingface", "embed_model_name": "sentence-transformers/all-MiniLM-L6-v2",
        "browser_model_provider": "google", "browser_model_name": "gemini-3.5-flash",
    }
    monkeypatch.setitem(sys.modules, "helpers.settings", settings)
    monkeypatch.setattr(helpers, "settings", settings, raising=False)
    v = view()
    assert v["totals"]["phrase"] == "4 of 4 keys ready"
    assert len(v["pools"]) == 2


def test_a_lookup_that_fails_leaves_the_view_as_it_was(monkeypatch):
    preset(monkeypatch, chat=slot("google", "gemini-3.5-flash"))

    def boom(name, default=None):
        raise RuntimeError(".env unreadable")

    sys.modules["helpers.dotenv"].get_dotenv_value = boom
    v = view()
    assert v["pools"] == [] and v["totals"]["keys"] == 0


def no_dotenv(monkeypatch):
    monkeypatch.delitem(sys.modules, "helpers.dotenv", raising=False)
    monkeypatch.delattr(sys.modules["helpers"], "dotenv", raising=False)


def test_nothing_of_agent_zero_present_is_the_old_view(monkeypatch):
    no_dotenv(monkeypatch)
    v = view()
    assert v["pools"] == [] and v["totals"]["phrase"] == "0 of 0 keys ready"


def test_the_wait_line_does_not_read_agent_zeros_settings(monkeypatch):
    preset(monkeypatch, chat=slot("google", "gemini-3.5-flash"))
    assert K._kame_key_view(known=False)["totals"]["keys"] == 0


def test_the_known_models_are_read_once_in_a_few_seconds(monkeypatch):
    preset(monkeypatch, chat=slot("google", "gemini-3.5-flash"))
    reads = []
    real = K._kame_provider_keys
    monkeypatch.setattr(K, "_kame_provider_keys", lambda p: reads.append(p) or real(p))
    view()
    view()
    view()
    assert len(reads) == 1


# -- the handler and the page ---------------------------------------------------------------------

def test_the_status_handler_answers_with_the_keys_and_never_a_key(monkeypatch):
    preset(monkeypatch, chat=slot("google", "gemini-3.5-flash"))
    out = report()
    assert out["view"]["totals"]["phrase"] == "3 of 3 keys ready"
    assert out["view"]["pools"][0]["unused"] is True
    text = json.dumps(out)
    for secret in GEM:
        assert secret not in text and secret[:10] not in text


def test_the_page_header_says_three_of_three_and_the_model_says_not_used_yet(monkeypatch):
    preset(monkeypatch, chat=slot("google", "gemini-3.5-flash"))
    responses = {STATUS_PATH: report()}
    out = run_store(
        "await store.refresh();"
        "const card = store.ringGroups.main[0];"
        "store.toggleRing(card.identity);"
        "return { header: store.headerStatus.text, word: card.word, tone: card.tone, aria: card.aria,"
        "         table: store.keyTable, line1: store.said.line1 };", responses)
    assert out["header"] == "3 of 3 keys ready"
    assert out["word"] == "not used yet"
    assert "3 of 3 keys ready" in out["aria"]
    assert out["table"]["empty"] == "" and len(out["table"]["keys"]) == 3
    assert "not used yet" in out["table"]["summary"] and out["table"]["unused"] is True
    assert out["line1"].startswith("Ready"), "keys that have not been asked are ready, not missing"


def test_the_empty_pool_still_says_so(monkeypatch):
    no_dotenv(monkeypatch)
    out = run_store("await store.refresh(); return store.headerStatus.text;", {STATUS_PATH: report()})
    assert out == "0 of 0 keys ready"

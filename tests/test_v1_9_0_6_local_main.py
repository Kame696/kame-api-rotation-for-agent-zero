"""1.9.0.6 (issue #1, reported on 1.9.0.1) - a local main model is never a switch target.

A user ran llama.cpp as the preset's main model and two Gemini models in KAME's fallback list, then picked
Gemini for the chat. When Gemini was busy, KAME moved the reply to the main model (the 1.8.3.1 ladder puts
the main model right after the pick) and llama.cpp reloaded its model for minutes. A local main model the
user did not write in the list has no key to rotate and no rate limit: it is never where a reply is moved
or raced to. A local model written in the list is used like any other. Twin of Hermes'
tests/test_1_9_0_6_local_main.py.
"""
from __future__ import annotations

import pytest

from test_v1_8_2_7_speed_fallback import clean, fake_models  # noqa: F401  (fixtures used by name)
from test_v1_8_3_1_any_model import resolver, ladder_of  # noqa: F401
import kame_engine as K

G1 = "google:gemini-3.1-flash-lite"
G2 = "google:gemini-3-flash-preview"


def local_main(resolver, provider="llama_cpp", api_base=""):
    resolver.main = {"provider": provider, "name": "qwen3.6-35b-a3b", "api_base": api_base}


def test_a_hand_picked_list_model_never_moves_to_a_local_main_model(fake_models, resolver):
    local_main(resolver)
    K.set_fallback_models("google/gemini-3.1-flash-lite, google/gemini-3-flash-preview")
    ctx = {"call_type": "Chat", "main_chat": True, "identity": G1}
    assert ladder_of(ctx) == [G1, G2]


def test_a_main_model_on_a_local_address_is_local_too(fake_models, resolver):
    local_main(resolver, provider="openai", api_base="http://host.docker.internal:8080/v1")
    K.set_fallback_models("google/gemini-3.1-flash-lite, google/gemini-3-flash-preview")
    ctx = {"call_type": "Chat", "main_chat": True, "identity": G2}
    assert ladder_of(ctx) == [G2, G1]


def test_a_cloud_main_model_keeps_the_ladder_of_1831(fake_models, resolver):
    local_main(resolver, provider="openrouter")
    K.set_fallback_models("google/gemini-3.1-flash-lite, google/gemini-3-flash-preview")
    ctx = {"call_type": "Chat", "main_chat": True, "identity": G2}
    assert ladder_of(ctx) == [G2, "openrouter:qwen3.6-35b-a3b", G1]


def test_a_reply_that_starts_on_the_local_main_model_still_walks_the_list(fake_models, resolver):
    local_main(resolver)
    K.set_fallback_models("google/gemini-3.1-flash-lite, google/gemini-3-flash-preview")
    ctx = {"call_type": "Chat", "main_chat": True, "identity": "llama_cpp:qwen3.6-35b-a3b"}
    assert ladder_of(ctx) == ["llama_cpp:qwen3.6-35b-a3b", G1, G2]


def test_a_local_model_the_user_put_in_the_list_is_used(fake_models, resolver):
    local_main(resolver)
    K.set_fallback_models("google/gemini-3-flash-preview, llama_cpp/qwen3.6-35b-a3b")
    ctx = {"call_type": "Chat", "main_chat": True, "identity": G1}
    assert ladder_of(ctx) == [G1, G2, "llama_cpp:qwen3.6-35b-a3b"]


@pytest.mark.parametrize("entry, local", [
    ({"provider": "ollama"}, True), ({"provider": "LM_Studio"}, True), ({"provider": "vllm"}, True),
    ({"provider": "openai", "api_base": "http://localhost:11434/v1"}, True),
    ({"provider": "openai", "api_base": "http://192.168.1.20:8080"}, True),
    ({"provider": "openai", "api_base": "http://172.20.0.3:8000/v1"}, True),
    ({"provider": "openai", "api_base": "http://gpu-box.local:8000"}, True),
    ({"provider": "openrouter"}, False), ({"provider": "openai", "api_base": "https://api.together.xyz/v1"}, False),
    ({"provider": "openai", "api_base": "http://172.32.0.1:8000"}, False), (None, False),
])
def test_what_counts_as_local(entry, local):
    assert K._kame_entry_is_local(entry) is local

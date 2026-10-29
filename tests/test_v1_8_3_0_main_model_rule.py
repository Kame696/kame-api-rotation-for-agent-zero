"""1.8.3.0 (A1 / D1) - the fallback list belongs to the main model.

A reply may be answered by the fallback list only if it STARTED on the main model: the chat slot of
the preset Agent Zero has configured for that chat's scope. A chat that runs another model (a per-chat
override, another preset) uses only its own model's keys - "Never" for that reply. A walk inside a reply
that did start on the main model goes on down the list as it did in 1.8.2.9. Where Agent Zero has no
`_model_config` plugin (older versions, an import error) nothing changes.

The resolver is stubbed in `sys.modules` under Agent Zero's own names; no Agent Zero, no network.
"""
from __future__ import annotations

import asyncio
import sys
import types

import pytest

from test_v1_8_2_7_speed_fallback import (  # noqa: F401  (fixtures used by name)
    clean, fake_models, make_ctx, run, streaming, KEYS, ID, FALLBACK_BEHAVIOUR, StopTest, _rest_every_key,
)
import kame_engine as K

RESOLVER = "plugins._model_config.helpers.model_config"


class FakeAgent:
    def __init__(self, name="A0"):
        self.name = name
        self.context = types.SimpleNamespace(id="ctx-1")


class Resolver:
    """What `plugins._model_config.helpers.model_config` answers, for one test."""

    def __init__(self):
        self.main = {"provider": "google", "name": "gemini/gemini-3.8-flash"}
        self.effective = None           # None = no override: the chat runs the main model
        self.preset = "Default"
        self.calls = []

    def get_config(self, agent=None, project_name=None, agent_profile=None):
        self.calls.append("get_config")
        return {"chat_model": dict(self.main), "model_preset": self.preset}

    def get_configured_preset_name(self, agent=None, project_name=None, agent_profile=None):
        self.calls.append("get_configured_preset_name")
        return self.preset

    def get_chat_model_config(self, agent=None):
        self.calls.append("get_chat_model_config")
        return dict(self.effective or self.main)


def install(monkeypatch, resolver):
    chain = ["plugins", "plugins._model_config", "plugins._model_config.helpers", RESOLVER]
    for name in chain[:-1]:
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    mod = types.ModuleType(RESOLVER)
    mod.get_config = resolver.get_config
    mod.get_configured_preset_name = resolver.get_configured_preset_name
    mod.get_chat_model_config = resolver.get_chat_model_config
    monkeypatch.setitem(sys.modules, RESOLVER, mod)


@pytest.fixture
def resolver(monkeypatch):
    r = Resolver()
    install(monkeypatch, r)
    token = K._KAME_CURRENT_AGENT.set(FakeAgent())
    yield r
    K._KAME_CURRENT_AGENT.reset(token)


def switch_run():
    K.set_fallback_models("openrouter/meta-llama/llama-4\ngroq/llama-3.3-70b")
    ctx, _ = make_ctx(streaming({}))
    _rest_every_key()
    return ctx


# -- the rule ------------------------------------------------------------------------------------

def test_a_reply_on_the_main_model_may_use_the_list(fake_models, resolver):
    ctx = switch_run()
    assert run(ctx)[0] == "fallback answer from openrouter/meta-llama/llama-4"


def test_a_chat_on_another_model_never_falls_back(fake_models, resolver, monkeypatch):
    """A per-chat override: the reply started on a model that is not the main one."""
    resolver.effective = {"provider": "anthropic", "name": "claude-x"}
    waited = []

    async def sleep(*a, **k):
        waited.append(1)
        raise StopTest()

    monkeypatch.setattr(K, "_kame_sleep_on_exhaustion", sleep)
    ctx = switch_run()
    with pytest.raises(StopTest):
        run(ctx)
    assert waited and not fake_models


def test_the_main_model_is_matched_by_provider_and_bare_name(fake_models, resolver):
    K.set_fallback_models("groq/b")
    """Agent Zero writes `gemini-3.8-flash`, LiteLLM names it `gemini/gemini-3.8-flash`: the same model."""
    resolver.main = {"provider": "Google", "name": "Gemini-3.8-Flash"}
    resolver.effective = {"provider": "google", "name": "gemini/gemini-3.8-flash"}
    assert K._kame_reply_on_main({"call_type": "Chat", "main_chat": True}) is True


def test_a_walk_inside_a_main_reply_goes_on(fake_models, resolver):
    FALLBACK_BEHAVIOUR["openrouter/a"] = "resting"
    K.set_fallback_models("openrouter/a, groq/b")
    ctx, _ = make_ctx(streaming({}))
    _rest_every_key()
    assert run(ctx)[0] == "fallback answer from groq/b"


def test_the_verdict_is_read_once_per_reply(fake_models, resolver):
    K.set_fallback_models("groq/b")
    ctx = {"call_type": "Chat", "main_chat": True}
    assert K._kame_reply_on_main(ctx) is True
    first = len(resolver.calls)
    resolver.effective = {"provider": "other", "name": "m"}   # changes after the reply started: not looked at again
    assert K._kame_reply_on_main(ctx) is True
    assert len(resolver.calls) == first


def test_the_other_ways_the_list_joins_a_reply_obey_the_same_rule(fake_models, resolver):
    K.set_fallback_models("groq/b")
    ctx = {"call_type": "Chat", "main_chat": True, "kwargs": {}}
    assert K._kame_cross_allowed(dict(ctx)) is True
    resolver.effective = {"provider": "other", "name": "m"}
    assert K._kame_cross_allowed(dict(ctx)) is False
    assert K._kame_can_switch(dict(ctx, entry_name="unified_call", identity=ID)) is False
    ctx_slow = dict(ctx, progress={"any": False, "reasoning": False}, identity=ID)
    assert K._kame_slow_after(ctx_slow, ID, False) == 0.0


# -- what does not change ----------------------------------------------------------------------

def test_without_a_list_the_settings_are_not_read(fake_models, resolver):
    resolver.effective = {"provider": "other", "name": "m"}
    assert K._kame_reply_on_main({"call_type": "Chat", "main_chat": True}) is True
    assert not resolver.calls


def test_a_background_job_is_not_asked(fake_models, resolver):
    K.set_fallback_models("groq/b")
    """The rule is about the conversation's reply; background jobs keep their own rules."""
    resolver.effective = {"provider": "other", "name": "m"}
    assert K._kame_reply_on_main({"call_type": "Utility", "main_chat": False}) is True
    assert not resolver.calls


def test_no_agent_is_the_1829_behaviour(fake_models, monkeypatch):
    K.set_fallback_models("groq/b")
    r = Resolver()
    r.effective = {"provider": "other", "name": "m"}
    install(monkeypatch, r)
    assert K._kame_reply_on_main({"call_type": "Chat", "main_chat": True}) is True


@pytest.mark.parametrize("how", ["absent", "raises", "empty"])
def test_a_missing_or_broken_resolver_keeps_the_1829_behaviour(fake_models, monkeypatch, how):
    K.set_fallback_models("groq/b")
    token = K._KAME_CURRENT_AGENT.set(FakeAgent())
    try:
        for name in ("plugins._model_config.helpers.model_config", "plugins._model_config.helpers",
                     "plugins._model_config", "plugins"):
            monkeypatch.delitem(sys.modules, name, raising=False)
        if how != "absent":     # "absent": nothing under that name can be imported
            r = Resolver()
            install(monkeypatch, r)
            if how == "raises":
                def boom(*a, **k):
                    raise RuntimeError("settings unreadable")
                sys.modules[RESOLVER].get_chat_model_config = boom
            else:
                r.main = {}
                r.effective = {"provider": "other", "name": "m"}
        assert K._kame_reply_on_main({"call_type": "Chat", "main_chat": True}) is True
    finally:
        K._KAME_CURRENT_AGENT.reset(token)


def test_a_resolver_without_get_config_reads_the_configured_preset(fake_models, monkeypatch):
    K.set_fallback_models("groq/b")
    """Older shape: only the preset lookups exist; the main model is the preset's chat slot."""
    r = Resolver()
    install(monkeypatch, r)
    mod = sys.modules[RESOLVER]
    del mod.get_config
    mod.resolve_preset = lambda name: {"name": name, "chat": dict(r.main)}
    token = K._KAME_CURRENT_AGENT.set(FakeAgent())
    try:
        assert K._kame_reply_on_main({"call_type": "Chat", "main_chat": True}) is True
        r.effective = {"provider": "other", "name": "m"}
        assert K._kame_reply_on_main({"call_type": "Chat", "main_chat": True}) is False
    finally:
        K._KAME_CURRENT_AGENT.reset(token)


def test_the_note_of_a_starting_call_primes_the_verdict(fake_models, resolver):
    K.set_fallback_models("groq/b")
    ctx = {"call_type": "Chat", "main_chat": True, "flight": {}}
    K._kame_note_call(ctx, None, ID)
    assert ctx.get("on_main") is True

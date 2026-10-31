"""1.8.3.0 (A1 / D1), REWRITTEN for 1.8.3.1 (D1) - the fallback list and the model in use.

1.8.3.0 let the fallback list answer only a reply that STARTED on the main model: a chat that ran another
model used only its own keys ("Never" for that reply). 1.8.3.1 reverses that: the list serves whatever model
the chat runs - the ladder is ``[model in use] + ([main] + list)``. What stays here is the part of the 1.8.3.0
contract that did not change: the main model is read from Agent Zero's own resolver, matched by provider and
bare model name, read once per reply, never read for a background job or with no list, and where the seam is
absent, broken or empty nothing is lost (the 1.8.2.9 order). The new ladder itself is pinned in
``test_v1_8_3_1_any_model.py``; the decision file ``decisions/0017-1831-any-model-ladder.md`` lists what each
assertion here said before and says now.

The resolver is stubbed in ``sys.modules`` under Agent Zero's own names; no Agent Zero, no network.
"""
from __future__ import annotations

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


def test_a_chat_on_another_model_now_falls_back_main_model_first(fake_models, resolver):
    """1.8.3.0: a per-chat override never fell back ("Never" for that reply). 1.8.3.1: the list serves it,
    and the main model is the first rung of its ladder."""
    other = "anthropic:claude-x"
    keys = ["sk-ant-TEST1830-" + c * 8 for c in "ab"]
    resolver.effective = {"provider": "anthropic", "name": "claude-x"}
    K.set_fallback_models("openrouter/meta-llama/llama-4\ngroq/llama-3.3-70b")
    ctx, _ = make_ctx(streaming({}))
    ctx["identity"], ctx["all_keys"] = other, keys
    K._get_identity_state(other, keys)
    for key in keys:
        K._mark_key_health(other, key, False, 3600, "insufficient_quota")
    assert run(ctx)[0] == "fallback answer from google/gemini/gemini-3.8-flash"


def test_the_main_model_is_matched_by_provider_and_bare_name(fake_models, resolver):
    """Agent Zero writes `gemini-3.8-flash`, LiteLLM names it `gemini/gemini-3.8-flash`: the same model,
    so the main model is not a rung of its own ladder."""
    K.set_fallback_models("groq/b")
    resolver.main = {"provider": "Google", "name": "Gemini-3.8-Flash"}
    assert K._kame_ladder({"call_type": "Chat", "main_chat": True, "identity": ID}) == [ID, "groq:b"]


def test_a_walk_inside_a_main_reply_goes_on(fake_models, resolver):
    FALLBACK_BEHAVIOUR["openrouter/a"] = "resting"
    K.set_fallback_models("openrouter/a, groq/b")
    ctx, _ = make_ctx(streaming({}))
    _rest_every_key()
    assert run(ctx)[0] == "fallback answer from groq/b"


def test_the_main_model_is_read_once_per_reply(fake_models, resolver):
    K.set_fallback_models("groq/b")
    ctx = {"call_type": "Chat", "main_chat": True, "identity": ID}
    assert K._kame_main_entry(ctx)["identity"] == "google:gemini/gemini-3.8-flash"
    first = len(resolver.calls)
    resolver.main = {"provider": "other", "name": "m"}   # changes after the reply started: not looked at again
    assert K._kame_main_entry(ctx)["identity"] == "google:gemini/gemini-3.8-flash"
    assert len(resolver.calls) == first


def test_the_other_ways_the_list_joins_a_reply_serve_the_model_in_use(fake_models, resolver):
    """1.8.3.0: a chat on another model got False/0.0 from every one of these. Now the list serves it."""
    K.set_fallback_models("groq/b")
    resolver.effective = {"provider": "other", "name": "m"}
    ctx = {"call_type": "Chat", "main_chat": True, "kwargs": {}, "identity": "other:m"}
    assert K._kame_cross_allowed(dict(ctx)) is True
    assert K._kame_can_switch(dict(ctx, entry_name="unified_call"))
    ctx_slow = dict(ctx, progress={"any": False, "reasoning": False})
    assert K._kame_slow_after(ctx_slow, "other:m", False) > 0.0


# -- what does not change ----------------------------------------------------------------------

def test_without_a_list_the_settings_are_not_read(fake_models, resolver):
    resolver.effective = {"provider": "other", "name": "m"}
    assert K._kame_main_entry({"call_type": "Chat", "main_chat": True}) is None
    assert not resolver.calls


def test_a_background_job_is_not_asked(fake_models, resolver):
    """The main model is the conversation's; a background job keeps the list alone."""
    K.set_fallback_models("groq/b")
    assert K._kame_main_entry({"call_type": "Utility", "main_chat": False}) is None
    assert not resolver.calls


def test_no_agent_is_the_1829_behaviour(fake_models, monkeypatch):
    K.set_fallback_models("groq/b")
    r = Resolver()
    install(monkeypatch, r)
    assert K._kame_main_entry({"call_type": "Chat", "main_chat": True}) is None


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
                sys.modules[RESOLVER].get_config = boom
            else:
                r.main = {}
        assert K._kame_main_entry({"call_type": "Chat", "main_chat": True}) is None
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
        entry = K._kame_main_entry({"call_type": "Chat", "main_chat": True})
        assert entry and entry["identity"] == "google:gemini/gemini-3.8-flash"
    finally:
        K._KAME_CURRENT_AGENT.reset(token)


def test_the_note_of_a_starting_call_settles_the_main_model(fake_models, resolver):
    K.set_fallback_models("groq/b")
    ctx = {"call_type": "Chat", "main_chat": True, "flight": {}}
    K._kame_note_call(ctx, None, ID)
    assert ctx["main_entry"]["identity"] == "google:gemini/gemini-3.8-flash"

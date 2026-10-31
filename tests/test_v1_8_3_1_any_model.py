"""1.8.3.1 (A1 / D1) - the fallback list serves the model in use, whichever it is.

``ladder(X) = [X] + ([main] + list, in the user's order, X removed)``. A reply that starts on the main
model walks ``[main] + list`` as in 1.8.3.0; a reply that starts on a list model, or on any model nobody
listed (a per-chat override, another preset), walks the main model first and the rest of the list after it.
The main model comes from Agent Zero's own resolver (the chat slot of the configured preset). Where the
resolver is absent, fails or names nothing, the ladder is ``[origin] + list`` (the 1.8.2.9 order). KAME never
builds a model the user did not write: the main model and the list are the user's own words.

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
PICKED = "groq:groq/a"                  # a list model the user picked by hand
PICKED_KEYS = [f"gsk_TEST1831-{c * 10}" for c in "abc"]


class FakeAgent:
    def __init__(self, name="A0"):
        self.name = name
        self.context = types.SimpleNamespace(id="ctx-1")


class Resolver:
    """What `plugins._model_config.helpers.model_config` answers, for one test."""

    def __init__(self):
        self.main = {"provider": "google", "name": "gemini-3.8-flash", "api_base": ""}
        self.effective = None
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


def on_picked(ctx):
    """Turn a made context into a reply that STARTED on the list model ``groq/a``."""
    ctx["identity"] = PICKED
    ctx["all_keys"] = list(PICKED_KEYS)
    K._get_identity_state(PICKED, PICKED_KEYS)
    return ctx


def rest_picked(seconds=3600):
    for key in PICKED_KEYS:
        K._mark_key_health(PICKED, key, False, seconds, "insufficient_quota")


def ladder_of(ctx):
    return K._kame_ladder(ctx)


# -- the ladder ------------------------------------------------------------------------------------

def test_the_main_model_keeps_the_1830_order(fake_models, resolver):
    K.set_fallback_models("groq/a, groq/b, groq/c")
    ctx = {"call_type": "Chat", "main_chat": True, "identity": ID}
    assert ladder_of(ctx) == [ID, "groq:a", "groq:b", "groq:c"]


def test_a_list_model_picked_by_hand_goes_main_first_then_the_rest(fake_models, resolver):
    """The owner's example: list 1,2,3,4 with 3 picked -> 3, then main, 1, 2, 4."""
    K.set_fallback_models("groq/one, groq/two, groq/three, groq/four")
    ctx = {"call_type": "Chat", "main_chat": True, "identity": "groq:groq/three"}
    assert ladder_of(ctx) == ["groq:groq/three", "google:gemini-3.8-flash", "groq:one", "groq:two", "groq:four"]


def test_a_model_nobody_listed_gets_the_whole_ladder(fake_models, resolver):
    """A per-chat override on a model outside the list: main, then the list."""
    K.set_fallback_models("groq/b, groq/c")
    resolver.effective = {"provider": "anthropic", "name": "claude-x"}
    ctx = {"call_type": "Chat", "main_chat": True, "identity": "anthropic:claude-x"}
    assert ladder_of(ctx) == ["anthropic:claude-x", "google:gemini-3.8-flash", "groq:b", "groq:c"]


def test_the_main_model_listed_twice_appears_once_and_first(fake_models, resolver):
    K.set_fallback_models("groq/a, google/gemini-3.8-flash, groq/c")
    picked = {"call_type": "Chat", "main_chat": True, "identity": PICKED}
    assert ladder_of(picked) == [PICKED, "google:gemini-3.8-flash", "groq:c"]
    main = {"call_type": "Chat", "main_chat": True, "identity": ID}
    assert ladder_of(main) == [ID, "groq:a", "groq:c"]


def test_the_main_model_is_matched_by_provider_and_bare_name(fake_models, resolver):
    """Agent Zero writes `gemini-3.8-flash`, LiteLLM names it `gemini/gemini-3.8-flash`: one model."""
    K.set_fallback_models("groq/b")
    resolver.main = {"provider": "Google", "name": "Gemini-3.8-Flash"}
    ctx = {"call_type": "Chat", "main_chat": True, "identity": ID}
    assert ladder_of(ctx) == [ID, "groq:b"]


def test_the_main_entry_is_the_users_own_words(fake_models, resolver):
    """The entry the carousel builds a model from: provider, name and endpoint exactly as configured."""
    K.set_fallback_models("groq/b")
    resolver.main = {"provider": "openrouter", "name": "meta-llama/llama-4", "api_base": "https://x.example/v1"}
    ctx = {"call_type": "Chat", "main_chat": True, "identity": PICKED}
    entry = K._kame_ladder_after(ctx)[0]
    assert entry["provider"] == "openrouter" and entry["name"] == "meta-llama/llama-4"
    assert entry["api_base"] == "https://x.example/v1"
    assert entry["identity"] == "openrouter:meta-llama/llama-4"
    assert entry["label"] == "openrouter/meta-llama/llama-4"


def test_the_seam_is_read_once_per_reply(fake_models, resolver):
    K.set_fallback_models("groq/b")
    ctx = {"call_type": "Chat", "main_chat": True, "identity": PICKED}
    K._kame_ladder(ctx)
    first = len(resolver.calls)
    resolver.main = {"provider": "other", "name": "m"}
    assert K._kame_ladder(ctx) == [PICKED, "google:gemini-3.8-flash", "groq:b"]
    assert len(resolver.calls) == first


def test_without_a_list_there_is_no_ladder_and_nothing_is_read(fake_models, resolver):
    ctx = {"call_type": "Chat", "main_chat": True, "identity": PICKED}
    assert K._kame_ladder_after(ctx) == []
    assert not resolver.calls


def test_a_background_job_has_only_the_list(fake_models, resolver):
    """A utility call is not the conversation: it has no 'main model' to go back to."""
    K.set_fallback_models("groq/b")
    ctx = {"call_type": "Utility", "main_chat": False, "identity": PICKED}
    assert K._kame_ladder(ctx) == [PICKED, "groq:b"]
    assert not resolver.calls


def test_no_agent_on_the_stack_is_the_1829_order(fake_models, monkeypatch):
    K.set_fallback_models("groq/b")
    install(monkeypatch, Resolver())
    ctx = {"call_type": "Chat", "main_chat": True, "identity": PICKED}
    assert K._kame_ladder(ctx) == [PICKED, "groq:b"]


@pytest.mark.parametrize("how", ["absent", "raises", "empty"])
def test_a_missing_or_broken_resolver_is_origin_plus_list(fake_models, monkeypatch, how):
    K.set_fallback_models("groq/b")
    token = K._KAME_CURRENT_AGENT.set(FakeAgent())
    try:
        for name in (RESOLVER, "plugins._model_config.helpers", "plugins._model_config", "plugins"):
            monkeypatch.delitem(sys.modules, name, raising=False)
        if how != "absent":
            r = Resolver()
            install(monkeypatch, r)
            if how == "raises":
                def boom(*a, **k):
                    raise RuntimeError("settings unreadable")
                sys.modules[RESOLVER].get_config = boom
            else:
                r.main = {}
        ctx = {"call_type": "Chat", "main_chat": True, "identity": PICKED}
        assert K._kame_ladder(ctx) == [PICKED, "groq:b"]
    finally:
        K._KAME_CURRENT_AGENT.reset(token)


def test_a_resolver_without_get_config_reads_the_configured_preset(fake_models, monkeypatch):
    K.set_fallback_models("groq/b")
    r = Resolver()
    install(monkeypatch, r)
    mod = sys.modules[RESOLVER]
    del mod.get_config
    mod.resolve_preset = lambda name: {"name": name, "chat": dict(r.main)}
    token = K._KAME_CURRENT_AGENT.set(FakeAgent())
    try:
        ctx = {"call_type": "Chat", "main_chat": True, "identity": PICKED}
        assert K._kame_ladder(ctx) == [PICKED, "google:gemini-3.8-flash", "groq:b"]
    finally:
        K._KAME_CURRENT_AGENT.reset(token)


def test_the_note_of_a_starting_call_settles_the_ladder_where_the_chat_is_on_the_stack(fake_models, resolver):
    K.set_fallback_models("groq/b")
    ctx = {"call_type": "Chat", "main_chat": True, "flight": {}, "identity": PICKED}
    K._kame_note_call(ctx, None, PICKED)
    assert ctx["main_entry"]["identity"] == "google:gemini-3.8-flash"


# -- the walk --------------------------------------------------------------------------------------

def test_a_picked_model_whose_keys_rest_is_answered_by_the_main_model_first(fake_models, resolver):
    K.set_fallback_models("groq/a, groq/b, groq/c")
    ctx, _ = make_ctx(streaming({}))
    on_picked(ctx)
    rest_picked()
    assert run(ctx)[0] == "fallback answer from google/gemini-3.8-flash"


def test_then_the_rest_of_the_list_in_the_users_order(fake_models, resolver):
    K.set_fallback_models("groq/a, groq/b, groq/c")
    FALLBACK_BEHAVIOUR["google/gemini-3.8-flash"] = "resting"
    ctx, _ = make_ctx(streaming({}))
    on_picked(ctx)
    rest_picked()
    assert run(ctx)[0] == "fallback answer from groq/b"
    asked = [name for name, _kw in fake_models]
    assert asked == ["google/gemini-3.8-flash", "groq/b"]


def test_and_the_last_one_after_that(fake_models, resolver):
    K.set_fallback_models("groq/a, groq/b, groq/c")
    FALLBACK_BEHAVIOUR["google/gemini-3.8-flash"] = "resting"
    FALLBACK_BEHAVIOUR["groq/b"] = "resting"
    ctx, _ = make_ctx(streaming({}))
    on_picked(ctx)
    rest_picked()
    assert run(ctx)[0] == "fallback answer from groq/c"


def test_the_picked_model_is_never_asked_as_its_own_fallback(fake_models, resolver):
    K.set_fallback_models("groq/a, groq/b")
    FALLBACK_BEHAVIOUR["google/gemini-3.8-flash"] = "resting"
    ctx, _ = make_ctx(streaming({}))
    on_picked(ctx)
    rest_picked()
    run(ctx)
    assert "groq/a" not in [name for name, _kw in fake_models]


def test_a_reply_on_the_main_model_walks_the_list_as_before(fake_models, resolver):
    K.set_fallback_models("openrouter/meta-llama/llama-4\ngroq/llama-3.3-70b")
    ctx, _ = make_ctx(streaming({}))
    _rest_every_key()
    assert run(ctx)[0] == "fallback answer from openrouter/meta-llama/llama-4"


def test_an_override_on_a_model_outside_the_list_uses_the_ladder(fake_models, resolver):
    """1.8.3.0 sent this reply to 'Never'; the list now serves whatever model the chat runs."""
    other = "anthropic:claude-x"
    keys = ["sk-ant-TEST1831-" + c * 8 for c in "ab"]
    K.set_fallback_models("groq/b")
    ctx, _ = make_ctx(streaming({}))
    ctx["identity"], ctx["all_keys"] = other, keys
    K._get_identity_state(other, keys)
    for key in keys:
        K._mark_key_health(other, key, False, 3600, "insufficient_quota")
    assert run(ctx)[0] == "fallback answer from google/gemini-3.8-flash"


def test_without_the_seam_a_picked_model_walks_origin_plus_list(fake_models, monkeypatch):
    K.set_fallback_models("groq/a, groq/b")
    for name in (RESOLVER, "plugins._model_config.helpers", "plugins._model_config", "plugins"):
        monkeypatch.delitem(sys.modules, name, raising=False)
    token = K._KAME_CURRENT_AGENT.set(FakeAgent())
    try:
        ctx, _ = make_ctx(streaming({}))
        on_picked(ctx)
        rest_picked()
        assert run(ctx)[0] == "fallback answer from groq/b"
    finally:
        K._KAME_CURRENT_AGENT.reset(token)


# -- what stays forbidden -----------------------------------------------------------------------------

@pytest.mark.parametrize("setting", ["never", "flag"])
def test_never_and_the_ignore_flag_forbid_every_switch(fake_models, resolver, monkeypatch, setting):
    K.set_fallback_models("groq/a, groq/b")
    if setting == "never":
        K.set_model_switch("never")
    else:
        monkeypatch.setattr(K, "_KAME_NO_MODEL_FALLBACK", True)
    waited = []

    async def sleep(*a, **k):
        waited.append(1)
        raise StopTest()

    monkeypatch.setattr(K, "_kame_sleep_on_exhaustion", sleep)
    ctx, _ = make_ctx(streaming({}))
    on_picked(ctx)
    rest_picked()
    with pytest.raises(StopTest):
        run(ctx)
    assert waited and not fake_models


def test_text_already_on_screen_forbids_the_switch(fake_models, resolver):
    K.set_fallback_models("groq/a, groq/b")
    ctx, _ = make_ctx(streaming({}))
    on_picked(ctx)
    ctx["progress"]["any"] = True
    assert K._kame_can_switch(dict(ctx)) is False


def test_the_other_ways_the_list_joins_a_reply_serve_the_picked_model_too(fake_models, resolver):
    K.set_fallback_models("groq/a, groq/b")
    base = {"call_type": "Chat", "main_chat": True, "kwargs": {}, "identity": PICKED,
            "progress": {"any": False, "reasoning": False}, "entry_name": "unified_call"}
    assert K._kame_cross_allowed(dict(base)) is True
    assert K._kame_can_switch(dict(base))
    assert K._kame_slow_after(dict(base), PICKED, False) > 0.0
    race = K._kame_cross_entry(dict(base), PICKED, False, 30.0)
    assert race and race[0][0]["identity"] == "google:gemini-3.8-flash"


def test_the_circle_looks_back_up_the_ladder_main_included(fake_models, resolver):
    K.set_fallback_models("groq/a, groq/b")
    ctx = {"call_type": "Chat", "main_chat": True, "identity": PICKED, "all_keys": list(PICKED_KEYS)}
    K._get_identity_state(PICKED, PICKED_KEYS)
    for ident in ("groq:b",):
        K._KAME_FALLBACK_SKIP[ident] = __import__("time").monotonic() + 500
    for key in PICKED_KEYS:
        K._mark_key_health(PICKED, key, False, 3600, "insufficient_quota")
    due, label = K._kame_circle_due_in(ctx)
    assert label == "google/gemini-3.8-flash"
    assert due < 400.0


def test_a_background_job_walks_the_list_only(fake_models, resolver):
    K.set_fallback_models("groq/a, groq/b")
    ctx = {"call_type": "Utility", "main_chat": False, "identity": PICKED}
    assert [e["identity"] for e in K._kame_ladder_after(ctx)] == ["groq:b"]


# -- what the screens say about it ----------------------------------------------------------------

def test_the_view_puts_the_main_model_first_when_the_chat_runs_a_picked_model(fake_models, resolver):
    """The chip's 'Your models' is the ladder of the chat in view: main first, the list in the user's
    order; the model in use is told apart by the activity, not by moving it to the top."""
    import time
    K.set_fallback_models("groq/a, groq/b")
    K._KAME_PRIMARY["identity"] = ""
    ctx = {"call_type": "Chat", "main_chat": True, "flight": {}, "identity": PICKED}
    K._kame_note_call(ctx, None, PICKED)
    entries = K._kame_fallback_view(time.time())["entries"]
    assert [e["role"] for e in entries] == ["primary", "fallback", "fallback"]
    assert K._kame_names_match(entries[0]["identity"], "google:gemini-3.8-flash")
    assert [e["identity"] for e in entries[1:]] == ["groq:a", "groq:b"]


def test_a_chat_on_the_main_model_keeps_the_view_as_it_was(fake_models, resolver):
    import time
    K.set_fallback_models("groq/a")
    K._KAME_PRIMARY["identity"] = ""
    ctx = {"call_type": "Chat", "main_chat": True, "flight": {}, "identity": ID}
    K._kame_note_call(ctx, None, ID)
    assert K._KAME_PRIMARY["identity"] == ID
    assert K._kame_fallback_view(time.time())["entries"][0]["identity"] == ID

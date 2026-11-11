"""1.8.4.0 E1: prompt caching is honoured, and a model that refuses it is remembered.

The rule reads the refusal's text, never a provider's name. No Agent Zero, no network.
"""
from __future__ import annotations

import asyncio
import json
import time
import types

import pytest

from test_v1_8_2_7_speed_fallback import ID, KEYS, Refusal, clean  # noqa: F401
import kame_engine as K
import kame_journal as J

GOOGLE_REFUSAL = ("429 RESOURCE_EXHAUSTED: TotalCachedContentStorageTokensPerModelFreeTier "
                  "limit=0, model=gemini-3.8-flash")


@pytest.fixture(autouse=True)
def calm(clean, tmp_path, monkeypatch):
    monkeypatch.setenv("KAME_DATA_DIR", str(tmp_path))
    K.set_prompt_caching("auto")
    K._kame_cache_forget()
    yield
    K.set_prompt_caching("auto")
    K._kame_cache_forget()


def make(asked, outcomes, caller=True, progress=None):
    """A ctx whose host call records the explicit_caching it was given."""
    calls = []

    async def orig(self, **kw):
        calls.append((kw["api_key"], kw["explicit_caching"]))
        out = outcomes.pop(0) if outcomes else "ok"
        if isinstance(out, Exception):
            raise out
        return out

    ctx = {"identity": ID, "orig": orig, "kwargs": {}, "retry_knobs": {}, "messages": ["x"],
           "response_callback": None, "reasoning_callback": None, "tokens_callback": None,
           "rate_limiter_callback": None, "explicit_caching": caller,
           "progress": progress if progress is not None else {"any": False, "reasoning": False}}
    return ctx, calls


def go(ctx, key=KEYS[0]):
    return asyncio.run(K._kame_attempt_delegated(None, key, ctx))


def test_caller_true_reaches_the_host_true():
    ctx, calls = make(True, [])
    go(ctx)
    assert calls == [(KEYS[0], True)]


def test_caller_false_stays_false():
    ctx, calls = make(True, [], caller=False)
    go(ctx)
    assert calls == [(KEYS[0], False)]


def test_a_cache_refusal_retries_the_same_key_once_without_it_and_remembers():
    ctx, calls = make(True, [Refusal(GOOGLE_REFUSAL, 429)])
    assert go(ctx) == "ok"
    assert calls == [(KEYS[0], True), (KEYS[0], False)]
    # nothing was marked, no failure event
    assert not [e for e in J.EVENTS.recent(50) if e.get("kind") in ("failure", "rest")]
    kinds = [e for e in J.EVENTS.recent(50) if e.get("kind") == "cache_off"]
    assert len(kinds) == 1 and "Prompt cache refused by" in kinds[0].get("reason", kinds[0].get("text", ""))
    ctx2, calls2 = make(True, [])
    go(ctx2)
    assert calls2 == [(KEYS[0], False)]


def test_the_memory_survives_a_reload_and_old_entries_are_ignored(tmp_path):
    ctx, _ = make(True, [Refusal(GOOGLE_REFUSAL, 429)])
    go(ctx)
    K._kame_cache_forget(keep_file=True)
    ctx2, calls2 = make(True, [])
    go(ctx2)
    assert calls2 == [(KEYS[0], False)]
    path = tmp_path / "no-explicit-cache.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data[ID] = time.time() - 31 * 86400
    path.write_text(json.dumps(data), encoding="utf-8")
    K._kame_cache_forget(keep_file=True)
    ctx3, calls3 = make(True, [])
    go(ctx3)
    assert calls3 == [(KEYS[0], True)]


def test_an_unreadable_file_means_an_empty_set(tmp_path):
    (tmp_path / "no-explicit-cache.json").write_text("{ nope", encoding="utf-8")
    K._kame_cache_forget(keep_file=True)
    ctx, calls = make(True, [])
    go(ctx)
    assert calls == [(KEYS[0], True)]


def test_the_setting_off_sends_false():
    K.set_prompt_caching("off")
    ctx, calls = make(True, [])
    go(ctx)
    assert calls == [(KEYS[0], False)]


def test_a_refusal_after_progress_is_not_retried():
    ctx, calls = make(True, [Refusal(GOOGLE_REFUSAL, 429)], progress={"any": True, "reasoning": False})
    with pytest.raises(Refusal):
        go(ctx)
    assert calls == [(KEYS[0], True)]


def test_a_429_that_does_not_name_the_cache_is_untouched():
    ctx, calls = make(True, [Refusal("429 too many requests", 429)])
    with pytest.raises(Refusal):
        go(ctx)
    assert calls == [(KEYS[0], True)]


def test_anthropics_cache_control_400_counts():
    assert K._kame_is_cache_refusal(Refusal("cache_control: too many blocks", 400))
    assert K._kame_is_cache_refusal(Refusal(GOOGLE_REFUSAL, 429))
    assert not K._kame_is_cache_refusal(Refusal("cache_control", 500))
    assert not K._kame_is_cache_refusal(Refusal("invalid api key", 401))


def test_the_setting_is_declared():
    import kame_settings as S
    row = S.SETTINGS["prompt_caching"]
    assert row[1] == "auto" and row[4] == ("auto", "off") and row[5] == "KAME_PROMPT_CACHING"
    assert row[7] == ("Lets Agent Zero use the provider's prompt cache, which makes repeated long prompts "
                      "much cheaper. Automatic turns it off by itself for a model that refuses it. "
                      "Off never uses it.")
    assert dict(S.CHOICE_LABELS["prompt_caching"]) == {"auto": "Automatic", "off": "Off"}
    assert S.group_of("prompt_caching") == "agentzero" and S.IMPACT["prompt_caching"] == "low"


# -- 1.8.4.1 K4: a refusal is remembered when the cache was the reason --------------------------
def test_a_cache_refusal_then_another_error_is_remembered_and_the_error_goes_on():
    other = Refusal("429 too many requests", 429)
    ctx, calls = make(True, [Refusal(GOOGLE_REFUSAL, 429), other])
    with pytest.raises(Refusal) as raised:
        go(ctx)
    assert raised.value is other
    assert calls == [(KEYS[0], True), (KEYS[0], False)]
    assert K._kame_cache_remembered(ID)
    assert len([e for e in J.EVENTS.recent(50) if e.get("kind") == "cache_off"]) == 1
    ctx2, calls2 = make(True, [])
    go(ctx2)
    assert calls2 == [(KEYS[0], False)]


def test_a_cache_refusal_then_the_same_refusal_remembers_nothing():
    again = Refusal(GOOGLE_REFUSAL, 429)
    ctx, calls = make(True, [Refusal(GOOGLE_REFUSAL, 429), again])
    with pytest.raises(Refusal) as raised:
        go(ctx)
    assert raised.value is again
    assert calls == [(KEYS[0], True), (KEYS[0], False)]
    assert not K._kame_cache_remembered(ID)
    assert not [e for e in J.EVENTS.recent(50) if e.get("kind") == "cache_off"]
    ctx2, calls2 = make(True, [])
    go(ctx2)
    assert calls2 == [(KEYS[0], True)]      # the next call asks for the cache again


def test_a_leg_cancelled_during_the_second_call_still_remembers():
    """A race leg is cancelled when another leg answers first. The first refusal named the cache, so the
    model is remembered; forgetting it would pay one refused request on every reply the other leg wins."""
    calls = []

    async def orig(self, **kw):
        calls.append((kw["api_key"], kw["explicit_caching"]))
        if kw["explicit_caching"]:
            raise Refusal(GOOGLE_REFUSAL, 429)
        raise asyncio.CancelledError()

    ctx, _ = make(True, [])
    ctx["orig"] = orig

    async def leg():
        with pytest.raises(asyncio.CancelledError):
            await K._kame_attempt_delegated(None, KEYS[0], ctx)

    asyncio.run(leg())
    assert calls == [(KEYS[0], True), (KEYS[0], False)]
    assert K._kame_cache_remembered(ID)
    assert len([e for e in J.EVENTS.recent(50) if e.get("kind") == "cache_off"]) == 1

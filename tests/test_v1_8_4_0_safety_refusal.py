"""E7 (1.8.4.0): a prompt no key can answer stops at once, and rests nothing.

The phrases are the host's own (Hermes `_CONTENT_POLICY_BLOCKED_PATTERNS`, v0.23.0). A 403 is read as
auth first, so each of them walked every key and two never stopped. The long-context 429 is the one
the host cures by compressing, which it could not do while KAME kept asking. The same list is in the
Hermes port (`REQUEST_CONTENT_BLOCK_INDICATORS`); Agent Zero keeps one older entry on top.
"""
from __future__ import annotations

import asyncio
import re
import time
from pathlib import Path

import pytest

from test_v1_8_2_7_speed_fallback import ID, KEYS, Refusal, StopTest, clean, make_ctx  # noqa: F401
import kame_engine as K

SAFETY_PHRASES = (
    "flagged for possible cybersecurity risk",
    "trusted access for cyber",
    "violates our usage policies",
    "violates openai's usage policies",
    "your request was flagged by",
    "prompt was flagged by our safety",
    "responses cannot be generated due to safety",
    "content_filter",
    "responsibleaipolicyviolation",
    "new_sensitive",
    "content exists risk",
    "data may contain inappropriate content",
    "data_inspection_failed",
)
LONG_CONTEXT = "Extra usage is required for long context requests over 200k tokens"


def carousel_run(message, status):
    attempts = []

    async def attempt(owner, key, ctx):
        attempts.append(key)
        if len(attempts) > 12:
            raise StopTest()
        raise Refusal(message, status)

    ctx, _ = make_ctx(attempt)
    try:
        asyncio.run(asyncio.wait_for(K._kame_carousel(None, ctx), 20))
    except (Refusal, StopTest):
        pass
    now = time.time()
    pool = K._KAME_KEY_HEALTH.get(ID, {}).get("keys", {})
    rested = [k for k, kd in pool.items() if float(kd.get("sick_until") or 0) > now]
    return attempts, rested


@pytest.mark.parametrize("status", [400, 403])
@pytest.mark.parametrize("phrase", SAFETY_PHRASES)
def test_the_hosts_safety_phrases_ask_one_key_and_rest_none(phrase, status):
    attempts, rested = carousel_run(f"Error: {phrase} (request id 7)", status)
    assert len(attempts) == 1 and rested == []


@pytest.mark.parametrize("phrase", SAFETY_PHRASES)
def test_the_match_ignores_case(phrase):
    assert K._is_terminal_error(Refusal(phrase.upper(), 403))


def test_the_long_context_429_goes_back_to_the_host_to_compress():
    attempts, rested = carousel_run(LONG_CONTEXT, 429)
    assert len(attempts) == 1 and rested == []


@pytest.mark.parametrize("message", [
    "API key blocked by admin",
    "key suspended for violating our content policy",
    "blocked for a previous policy violation",
    "Permission denied",
])
def test_a_refusal_of_the_key_still_tries_the_next_key(message):
    assert not K._is_terminal_error(Refusal(message, 403))


@pytest.mark.parametrize("message", [
    "Request blocked by rate limiting rule",
    "Too many requests: blocked by rate limiter",
    "Resource has been exhausted (e.g. check quota).",
])
def test_a_throttle_stays_a_throttle(message):
    assert not K._is_terminal_error(Refusal(message, 429))


def test_the_list_holds_the_hosts_and_keeps_what_agent_zero_had():
    hermes = Path(__file__).resolve().parents[3] / "hermes" / "hermes-kame-api-rotation" / "core" / "carousel.py"
    if not hermes.exists():
        pytest.skip("the Hermes source is not beside this tree")
    block = re.search(r"REQUEST_CONTENT_BLOCK_INDICATORS = \((.*?)\n\)", hermes.read_text(encoding="utf-8"), re.S).group(1)
    theirs = tuple(re.findall(r'"([^"]+)"', block))
    ours = K._CONTENT_BLOCK_INDICATORS
    assert all(p in ours for p in theirs)
    assert set(ours) - set(theirs) <= {"content_policy"}       # the one older, wider entry
    for old in ("content_policy", "content filter", "content_filter", "safety filter"):
        assert old in ours

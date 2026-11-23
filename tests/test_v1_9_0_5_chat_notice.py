"""1.9.0.5 (C): the chat notice says the re-test - "every key resting - asking key ...abcd again (re-test 2)".

The status line of the chat call (the one Agent Zero shows as "Calling LLM...") already says "Every key of X is
resting - next key in 59m". While a re-test is in flight it says which key is asked again and which re-test it is.
No other surface changes.
"""
from __future__ import annotations

import asyncio
import time

import pytest

from test_v1_8_2_7_speed_fallback import ID, KEYS as ALL_KEYS, clean, make_ctx  # noqa: F401
from test_v1_8_2_9_status_line import FakeAgent, PLAIN  # noqa: F401
from test_v1_8_3_1_live_line import calm, lines, NAME  # noqa: F401
from test_v1_9_0_5_retest import Provider, rest_on_a_label, KEYS  # noqa: F401
from kame_vclock import vt  # noqa: F401
import kame_engine as K


def test_the_words_of_the_retest_line():
    row = {"kind": "retesting", "model": "m", "tail": "...abcd", "n": 2}
    assert K._kame_live_line(row) == "↻ m: every key resting - asking key ...abcd again (re-test 2)"
    assert K._kame_live_line({"kind": "retesting", "model": "m", "n": 1}) == \
        "↻ m: every key resting - asking one key again (re-test 1)"


def test_a_refused_re_test_shows_its_number_and_the_key_it_asked(vt):
    K.set_hedge_mode("off")
    K._KAME_RETEST_INFLIGHT.clear()
    agent = FakeAgent()
    provider = Provider(vt, healthy=False, takes=3.0)
    ctx, _ = make_ctx(provider, keys=KEYS)
    rest_on_a_label()
    with pytest.raises(asyncio.TimeoutError):
        agent_run = agent.call_chat_model(K._kame_carousel(None, ctx))
        vt.run(agent_run, limit=70)
    said = [line for line in lines(agent) if "re-test" in line]
    assert said, lines(agent)
    first = provider.asked[0][1]
    assert f"asking key ...{first[-4:]} again (re-test 1)" in said[0]
    assert any("(re-test 2)" in line for line in said)
    assert all(first not in line for line in said)             # four characters, never the key
    K._KAME_RETEST_INFLIGHT.clear()


def test_the_notice_is_gone_when_the_answer_comes_back(vt):
    K.set_hedge_mode("off")
    K._KAME_RETEST_INFLIGHT.clear()
    agent = FakeAgent()
    provider = Provider(vt, healthy=True)
    ctx, _ = make_ctx(provider, keys=KEYS)
    rest_on_a_label()
    result = vt.run(agent.call_chat_model(K._kame_carousel(None, ctx)), limit=300)
    assert result[0] == "back"
    assert agent.headings()[-1] == PLAIN
    assert "kame_retest" not in ctx and "kame_retest_tail" not in ctx

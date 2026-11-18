"""1.9.0.3 (C2) - the live line keeps saying what is happening while the model is silent.

Evidence on the Hermes side: 35 answered calls had a median first sign of 19.5 s, six answers were silent for
85-218 s, and the line drawn once at the start of an attempt was gone long before. Here, while a chat reply has
had no first word on any leg, the line of "Calling LLM..." is redrawn every 5 s with the seconds since the reply's
first attempt, the keys ready, and what else is asking ("a backup key", another model of the list). Not for a
background job, not once a word has come, not after a stop, and not when the status line is switched off.
A virtual clock makes the seconds exact.
"""
from __future__ import annotations

import pytest

from test_v1_8_2_7_speed_fallback import ID, KEYS, clean, make_ctx, streaming  # noqa: F401
from test_v1_8_2_9_status_line import FakeAgent, PLAIN, waiting_call  # noqa: F401
from test_v1_8_3_1_live_line import calm, lines, NAME  # noqa: F401
from kame_vclock import vt  # noqa: F401
import kame_engine as K
import kame_hedging as H


def silent(lines_):
    return [line for line in lines_ if "no first word yet" in line]


def test_the_words_of_the_silent_line():
    base = {"kind": "silent", "model": "m", "ready": 3, "keys": 4, "seconds": 12.4}
    assert K._kame_live_line(base) == "⏳ Waiting on m · 3 of 4 keys ready, no first word yet (12s)"
    assert K._kame_live_line({**base, "extras": ["a backup key is asking too"]}) == \
        "⏳ Waiting on m · 3 of 4 keys ready, no first word yet (12s); a backup key is asking too"
    assert K._kame_live_line({**base, "extras": ["a backup key is asking too", "n is asking too"]}) == \
        "⏳ Waiting on m · 3 of 4 keys ready, no first word yet (12s); a backup key is asking too; n is asking too"
    assert K._kame_live_line({**base, "seconds": None}).endswith("no first word yet (0s)")


def test_a_silent_reply_redraws_the_line_every_five_seconds_with_the_seconds_counting_up(vt):
    K.set_hedge_mode("off")
    agent = FakeAgent()
    ctx, _ = make_ctx(streaming({KEYS[0]: 12.0}), keys=KEYS[:1])
    result = vt.run(agent.call_chat_model(K._kame_carousel(None, ctx)), limit=60)
    assert result[0].startswith("answer from")
    said = silent(lines(agent))
    assert said == [f"⏳ Waiting on {NAME} · 1 of 1 keys ready, no first word yet (5s)",
                    f"⏳ Waiting on {NAME} · 1 of 1 keys ready, no first word yet (10s)"]
    assert agent.headings()[-1] == PLAIN


def test_none_after_the_first_word_has_come(vt):
    K.set_hedge_mode("off")
    agent = FakeAgent()
    ctx, _ = make_ctx(streaming({KEYS[0]: 3.0}), keys=KEYS[:1])
    vt.run(agent.call_chat_model(K._kame_carousel(None, ctx)), limit=60)
    assert silent(lines(agent)) == []


def test_a_backup_key_asking_too_is_told(vt, monkeypatch):
    monkeypatch.setattr(H.HEDGE_TRACKER, "stream_triggers", lambda *a, **k: (0.5, 500.0))
    agent = FakeAgent()
    ctx, _ = make_ctx(streaming({k: 14.0 for k in KEYS}))
    vt.run(agent.call_chat_model(K._kame_carousel(None, ctx)), limit=60)
    said = silent(lines(agent))
    assert said and all(line.endswith("a backup key is asking too") for line in said)
    seconds = [int(line.split("(")[1].split("s)")[0]) for line in said]
    assert seconds == sorted(seconds) and len(set(seconds)) == len(seconds) and seconds[0] == 5


def test_a_background_job_never_rewrites_the_line_of_the_chat(vt):
    K.set_hedge_mode("off")
    agent = FakeAgent()
    ctx, _ = make_ctx(streaming({KEYS[0]: 12.0}), keys=KEYS[:1])
    ctx["main_chat"] = False
    vt.run(agent.call_chat_model(K._kame_carousel(None, ctx)), limit=60)
    assert silent(lines(agent)) == []


def test_after_a_stop_no_more_lines_and_agent_zeros_words_are_back(vt):
    K.set_hedge_mode("off")
    agent = FakeAgent()
    ctx, _ = make_ctx(streaming({KEYS[0]: 40.0}), keys=KEYS[:1])
    waiting_call(vt, agent, ctx, 12)        # cancelled at 12 s
    said = silent(lines(agent))
    assert said and said[-1].endswith("(10s)")
    assert agent.headings()[-1] == PLAIN
    before = len(agent.headings())
    vt.advance(30)
    assert len(agent.headings()) == before


def test_with_the_status_line_switched_off_nothing_is_drawn(vt):
    K.set_hedge_mode("off")
    K.set_wait_notice(False)
    agent = FakeAgent()
    ctx, _ = make_ctx(streaming({KEYS[0]: 12.0}), keys=KEYS[:1])
    vt.run(agent.call_chat_model(K._kame_carousel(None, ctx)), limit=60)
    assert agent.headings() == []

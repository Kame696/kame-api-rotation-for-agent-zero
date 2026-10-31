"""1.8.3.1 (A2 / D2) - one live line on "Calling LLM...", the same words as the Hermes composer line.

``tests/fixtures/live_words_1831.json`` is the shared table (byte-identical in the Hermes port);
``_kame_live_line(activity)`` renders a row of it. The heading of the chat's "Calling LLM..." item becomes
``{agent}: {line}`` for every state from the first attempt: asking, trying another key, a backup key racing,
a ladder model answering instead, every key resting (with the time to the next one), the answer being
continued, and the model being back after a wait. The agent's own words come back when the call ends or when
Agent Zero's stream handlers take the heading over (text on screen). A virtual clock makes the seconds exact.
"""
from __future__ import annotations

import asyncio
import json
import time
import types
from pathlib import Path

import pytest

from test_v1_8_2_7_speed_fallback import ID, KEYS, Refusal, clean, make_ctx, streaming, _in_order  # noqa: F401
from test_v1_8_2_9_silent_two_models import world  # noqa: F401  (fixture used by name)
from test_v1_8_2_9_status_line import FakeAgent, FakeItem, PLAIN, rest_all, waiting_call  # noqa: F401
from kame_vclock import vt  # noqa: F401
import kame_engine as K

FIXTURE = Path(__file__).with_name("fixtures") / "live_words_1831.json"
TABLE = json.loads(FIXTURE.read_text(encoding="utf-8"))
NAME = "gemini-3.8-flash"


@pytest.fixture(autouse=True)
def calm(clean):
    K.set_current_agent(None)
    K.set_wait_notice(True)
    K._KAME_IN_FLIGHT.clear()
    yield
    K.set_current_agent(None)
    K.set_wait_notice(False)
    K._KAME_IN_FLIGHT.clear()


def lines(agent):
    """What the heading said, without the agent's prefix and without Agent Zero's own words."""
    return [h.split(": ", 1)[1] for h in agent.headings() if h != PLAIN]


# -- the words --------------------------------------------------------------------------------------

@pytest.mark.parametrize("row", TABLE["live"], ids=lambda r: f"{r['kind']}-{r['line'][:24]}")
def test_every_fixture_row_renders_to_its_words(row):
    assert K._kame_live_line(row["activity"]) == row["line"]


def test_the_table_has_every_state_of_the_plan():
    assert {r["kind"] for r in TABLE["live"]} == {
        "calling", "retrying", "racing", "standin", "waiting", "stitching", "recovered"}


def test_an_activity_with_the_other_ports_field_names_renders_the_same_words():
    assert K._kame_live_line({"kind": "calling", "model": "m", "healthy": 4, "keys": 4, "attempt": 1}) == \
        "⏳ Asking m · 4 of 4 keys ready"
    assert K._kame_live_line({"kind": "calling", "model": "m", "healthy": 3, "keys": 4, "attempt": 1}) == \
        "↻ m: trying another key · 3 of 4 keys ready"
    assert K._kame_live_line({"kind": "stitching", "model": "m", "resume": 2, "budget": 5}) == \
        "↻ The answer was cut off - continuing it on another key (2 of 5)"
    assert K._kame_live_line({"kind": "stitching", "model": "m", "resume": 2, "budget": None}) == \
        "↻ The answer was cut off - continuing it on another key"
    assert K._kame_live_line({"kind": "recovered", "model": "m", "waited_s": 130}) == "↻ m is back after 2m 10s"


def test_the_phase_of_a_calling_activity_picks_the_row():
    assert K._kame_live_line({"kind": "calling", "phase": "racing", "model": "m", "healthy": 3, "keys": 3}) == \
        "⚡ m is slow to start - a backup key is asking too"
    assert K._kame_live_line({"kind": "calling", "phase": "standin", "model": "m", "other": "n",
                              "healthy": 3, "keys": 3}) == "↪ m can't answer now - n is answering instead"


def test_nothing_to_say_is_an_empty_line():
    assert K._kame_live_line({"kind": "idle"}) == ""
    assert K._kame_live_line({}) == ""
    assert K._kame_live_line(None) == ""


@pytest.mark.parametrize("seconds,text", [
    (0, "0s"), (0.4, "0s"), (0.5, "1s"), (45, "45s"), (59.4, "59s"), (59.6, "1m 00s"), (60, "1m 00s"),
    (125, "2m 05s"), (130, "2m 10s"), (3599, "59m 59s"), (3600, "1h 00m"), (3725, "1h 02m"), (None, "0s")])
def test_the_durations_are_the_pages_own(seconds, text):
    assert K._kame_live_duration(seconds) == text


@pytest.mark.parametrize("identity,name", [
    ("google:gemini/gemini-3.8-flash", "gemini-3.8-flash"),     # LiteLLM's route is not part of the name
    ("google:gemini-3.7-flash", "gemini-3.7-flash"),            # as the user wrote it
    ("openrouter:openrouter/meta-llama/llama-4", "meta-llama/llama-4"),
    ("groq:groq/llama-3.3-70b", "llama-3.3-70b"),
    ("openrouter:meta-llama/llama-4", "meta-llama/llama-4"),    # a vendor path is the model's own
    ("model-without-provider", "model-without-provider"),
])
def test_the_model_is_named_as_the_user_wrote_it_without_the_provider(identity, name):
    assert K._kame_live_name(identity) == name


# -- the heading ------------------------------------------------------------------------------------

def test_the_heading_is_the_agent_then_the_line(vt):
    agent = FakeAgent()

    async def go():
        K._kame_write_heading("⏳ Asking m · 1 of 1 keys ready", ("calling",))
    vt.run(agent.call_chat_model(go()))
    assert agent.item.heading == "A0: ⏳ Asking m · 1 of 1 keys ready"


def test_a_subordinate_agent_keeps_its_own_prefix(vt):
    sub = FakeAgent("A1")

    async def go():
        K._kame_write_heading("⏳ Asking m · 1 of 1 keys ready", ("calling",))
        K._kame_write_heading(None)
    vt.run(sub.call_chat_model(go()))
    assert sub.headings() == ["A1: ⏳ Asking m · 1 of 1 keys ready", "A1: Calling LLM..."]


def test_the_heading_is_cut_at_120_characters_the_line_never_the_agent(vt):
    agent = FakeAgent("A12")

    async def go():
        K._kame_write_heading("⏳ Asking " + "m" * 300, ("calling",))
    vt.run(agent.call_chat_model(go()))
    heading = agent.item.heading
    assert len(heading) <= 120 and heading.startswith("A12: ⏳ Asking m") and heading.endswith("...")


def test_the_same_state_changes_at_most_once_a_second_and_a_new_state_at_once(vt):
    agent = FakeAgent()

    async def go():
        for i in range(8):
            K._kame_write_heading(f"line {i}", ("waiting",))      # a countdown: every 0.25 s
            await asyncio.sleep(0.25)
        K._kame_write_heading("another state", ("calling",))       # a different state is not held back
    vt.run(agent.call_chat_model(go()))
    stamps = [t for t, h in agent.item.updates if h.startswith("A0: line")]
    assert len(stamps) == 2 and stamps[1] - stamps[0] >= 0.99
    assert agent.item.updates[-1][1] == "A0: another state"


def test_the_line_changes_at_most_every_second_while_the_keys_rest(vt):
    rest_all(KEYS[:1], 30)
    agent = FakeAgent()
    ctx, _ = make_ctx(streaming({}), keys=KEYS[:1])
    waiting_call(vt, agent, ctx, 12)
    stamps = [t for t, h in agent.item.updates if "resting" in h]
    assert 9 <= len(stamps) <= 13
    assert all(b - a >= 0.99 for a, b in zip(stamps, stamps[1:]))


# -- the states, from the first attempt -------------------------------------------------------------------

def test_a_plain_call_says_asking_from_the_first_attempt_and_gives_the_words_back(vt):
    agent = FakeAgent()
    ctx, _ = make_ctx(streaming({}))
    result = vt.run(agent.call_chat_model(K._kame_carousel(None, ctx)), limit=60)
    assert result[0].startswith("answer from")
    assert lines(agent) == [f"⏳ Asking {NAME} · 8 of 8 keys ready"]
    assert agent.headings()[-1] == PLAIN and agent.item.heading == PLAIN


def test_a_key_that_fails_then_a_key_that_answers_says_trying_another_key(vt, monkeypatch):
    slow = lambda: Refusal("Error code: 429", 429, {"error": {"message": "slow down"}}, {"retry-after": "30"})
    monkeypatch.setattr(K, "_get_best_key", _in_order())
    agent = FakeAgent()
    ctx, _ = make_ctx(streaming({}, fail={KEYS[0]: slow}))
    result = vt.run(agent.call_chat_model(K._kame_carousel(None, ctx)), limit=60)
    assert result[0].startswith("answer from")
    assert lines(agent) == [f"⏳ Asking {NAME} · 8 of 8 keys ready",
                            f"↻ {NAME}: trying another key · 7 of 8 keys ready"]
    assert agent.headings()[-1] == PLAIN


def test_every_key_resting_says_the_time_to_the_next_key_in_the_pages_format(vt):
    rest_all(KEYS[:1], 2700)
    agent = FakeAgent()
    ctx, _ = make_ctx(streaming({}), keys=KEYS[:1])
    waiting_call(vt, agent, ctx, 5)
    first = next(h for h in agent.headings() if h != PLAIN)
    assert first == f"A0: ⏳ Every key of {NAME} is resting · next key in 45m 00s"
    assert agent.headings()[-1] == PLAIN        # cancelled mid-wait: Agent Zero's words are back


def test_a_key_that_comes_back_says_the_model_is_back_after_the_wait(vt):
    rest_all(KEYS[:1], 5)
    agent = FakeAgent()
    ctx, _ = make_ctx(streaming({}), keys=KEYS[:1])
    result = vt.run(agent.call_chat_model(K._kame_carousel(None, ctx)), limit=120)
    assert result[0].startswith("answer from")
    said = lines(agent)
    assert any("resting" in line for line in said)
    assert said[-1].startswith(f"↻ {NAME} is back after ") and said[-1].endswith("s")
    assert agent.headings()[-1] == PLAIN and agent.item.heading == PLAIN


def test_a_ladder_model_answering_for_the_one_in_use_says_so(world, vt):
    K.set_fallback_models("google/gemini-3.7-flash")
    rest_all(KEYS, 3600)
    agent = FakeAgent()
    ctx, _ = make_ctx(streaming({}))
    result = vt.run(agent.call_chat_model(K._kame_carousel(None, ctx)), limit=60)
    assert result[0] == "answer from google/gemini-3.7-flash"
    assert f"↪ {NAME} can't answer now - gemini-3.7-flash is answering instead" in lines(agent)
    assert agent.headings()[-1] == PLAIN


def test_a_backup_key_racing_the_first_says_so(vt, monkeypatch):
    from test_v1_8_2_7_speed_fallback import _first_then_rest
    import kame_hedging as H
    monkeypatch.setattr(H.HEDGE_TRACKER, "stream_triggers", lambda *a, **k: (0.5, 5.0))
    first = KEYS[0]
    agent = FakeAgent()
    ctx, _ = make_ctx(streaming({k: (20.0 if k == first else 0.0) for k in KEYS}))
    monkeypatch.setattr(K, "_get_best_key", _first_then_rest(first))
    result = vt.run(agent.call_chat_model(K._kame_carousel(None, ctx)), limit=60)
    assert not result[0].endswith(first[-4:])
    said = lines(agent)
    assert f"⚡ {NAME} is slow to start - a backup key is asking too" in said
    assert said.index(f"⏳ Asking {NAME} · 8 of 8 keys ready") < said.index(f"⚡ {NAME} is slow to start - a backup key is asking too")


def test_a_continued_answer_says_so_while_the_heading_is_still_ours(vt):
    agent = FakeAgent()
    ctx, _ = make_ctx(streaming({}))
    ctx["flight"] = {}

    async def go():
        K._kame_say(ctx, "stitching", n=1, max=3)
    vt.run(agent.call_chat_model(go()))
    assert lines(agent) == ["↻ The answer was cut off - continuing it on another key (1 of 3)"]


def test_the_view_carries_what_the_page_needs_to_say_the_same_line(world, vt, monkeypatch):
    """The chip's popover says the live line too: while a ladder model answers, the activity carries the
    phase and the other model, and names the model the reply started on."""
    from test_v1_8_2_9_silent_two_models import SilentModel
    K.set_fallback_models("google/gemini-3.7-flash")
    rest_all(KEYS, 3600)
    agent = FakeAgent()
    ctx, _ = make_ctx(streaming({}))
    ctx["flight"] = {"job": "chat", "identity": ID, "started": time.time(), "context": "chat-1"}
    K._KAME_IN_FLIGHT["t"] = ctx["flight"]
    seen = []
    original = SilentModel.unified_call

    async def spy(self, **kwargs):
        seen.append(K._kame_activity(K._kame_key_view(), "chat-1", time.time()))
        return await original(self, **kwargs)
    monkeypatch.setattr(SilentModel, "unified_call", spy)
    vt.run(agent.call_chat_model(K._kame_carousel(None, ctx)), limit=60)
    activity = seen[0]
    assert activity["phase"] == "standin" and activity["other"] == "gemini-3.7-flash"
    assert activity["name"] == NAME
    assert K._kame_live_line({**activity, "model": activity["name"]}) ==         f"↪ {NAME} can't answer now - gemini-3.7-flash is answering instead"
    # and the line is given back with the call: nothing of it is left on the flight
    assert K._kame_activity(K._kame_key_view(), "chat-1", time.time())["phase"] is None


# -- when the heading is not ours -----------------------------------------------------------------------

def test_a_heading_the_stream_handlers_took_is_never_overwritten(vt, monkeypatch):
    slow = lambda: Refusal("Error code: 429", 429, {"error": {"message": "slow down"}}, {"retry-after": "30"})
    monkeypatch.setattr(K, "_get_best_key", _in_order())
    agent = FakeAgent()

    base = streaming({}, fail={KEYS[0]: slow})

    async def attempt(owner, key, ctx):
        if key == KEYS[0]:
            agent.item.heading = "A0: Generating response..."      # text started: Agent Zero's own words
        return await base(owner, key, ctx)

    ctx, _ = make_ctx(attempt)
    vt.run(agent.call_chat_model(K._kame_carousel(None, ctx)), limit=60)
    assert agent.item.heading == "A0: Generating response..."
    assert agent.headings() == [f"A0: ⏳ Asking {NAME} · 8 of 8 keys ready"]


def test_the_setting_off_leaves_the_heading_alone(vt):
    K.set_wait_notice(False)
    agent = FakeAgent()
    ctx, _ = make_ctx(streaming({}))
    vt.run(agent.call_chat_model(K._kame_carousel(None, ctx)), limit=60)
    assert agent.item.updates == [] and agent.context.log.progress == []


def test_a_background_job_never_rewrites_the_chats_line(vt):
    agent = FakeAgent()
    ctx, _ = make_ctx(streaming({}), call_type="Util")
    vt.run(agent.call_chat_model(K._kame_carousel(None, ctx)), limit=60)
    assert agent.item.updates == []


def test_without_the_log_item_the_logs_progress_line_is_used(vt):
    rest_all(KEYS[:1], 2700)
    agent = FakeAgent(item=False)
    ctx, _ = make_ctx(streaming({}), keys=KEYS[:1])
    waiting_call(vt, agent, ctx, 3)
    log = agent.context.log
    assert log.progress[0][0].startswith("A0: ⏳ Asking") or log.progress[0][0].startswith("A0: ⏳ Every key")
    assert log.progress[-1] == ("", False)


def test_a_log_that_raises_never_touches_rotation(vt):
    agent = FakeAgent()
    agent.item.update = lambda **kw: (_ for _ in ()).throw(RuntimeError("the log is having a bad day"))
    ctx, _ = make_ctx(streaming({}))
    result = vt.run(agent.call_chat_model(K._kame_carousel(None, ctx)), limit=60)
    assert result[0].startswith("answer from")

"""1.8.2.9 (E10, decisions/0009) - the wait lives on the status line, never in a toast.

Agent Zero's own "A0: Calling LLM..." is the heading of the log item it makes for the chat call.
KAME writes what it waits for into that heading (time LEFT only, the count of physical keys the chip
shows) and gives Agent Zero's words back when the wait ends. The toast (and the chat row after ninety
seconds) are gone. A virtual clock makes the seconds exact. No Agent Zero installation.
"""
from __future__ import annotations

import asyncio
import importlib.util
import re
import sys
import time
import types
from pathlib import Path

import pytest

from test_v1_8_2_7_speed_fallback import ID, KEYS, clean, make_ctx, streaming  # noqa: F401
from test_v1_8_2_9_silent_two_models import world  # noqa: F401  (fixture used by name)
from kame_vclock import vt  # noqa: F401
import kame_engine as K

ROOT = Path(__file__).resolve().parents[1]
# 1.8.3.1 (D2): the wait is the live line's "waiting" state: the shared words, the page's duration format
WAIT_45 = "A0: \u23f3 Every key of gemini-3.8-flash is resting \u00b7 next key in 45m 00s"
PLAIN = "A0: Calling LLM..."


class FakeItem:
    """Agent Zero's LogItem: its heading is edited in place."""

    def __init__(self):
        self.heading = PLAIN
        self.updates = []           # (time, heading)

    def update(self, heading=None, **kw):
        self.heading = heading
        self.updates.append((time.time(), heading))


class FakeLog:
    def __init__(self, progress=True):
        self.logged = []
        self.progress = []
        if progress:
            self.set_progress = lambda text, no=0, active=True: self.progress.append((text, active))

    def log(self, **kw):
        self.logged.append(kw)
        return FakeItem()


class FakeAgent:
    """The part of Agent Zero's Agent the status line touches."""

    def __init__(self, name="A0", item=True, log=None):
        self.agent_name = name
        self.number = int(name[1:])
        self.context = types.SimpleNamespace(id="chat-1", log=log if log is not None else FakeLog())
        self.item = FakeItem()
        self.loop_data = types.SimpleNamespace(
            params_temporary={"log_item_generating": self.item} if item else {})

    async def call_chat_model(self, work):
        return await work

    def headings(self):
        return [h for _t, h in self.item.updates]


@pytest.fixture(autouse=True)
def calm(clean):
    K.set_current_agent(None)
    K.set_wait_notice(True)
    K._KAME_IN_FLIGHT.clear()
    yield
    K.set_current_agent(None)
    K.set_wait_notice(False)
    K._KAME_IN_FLIGHT.clear()


def rest_all(keys, seconds, identity=ID):
    """Every key rests ``seconds`` from now (set the deadline itself: the classifier would size a daily
    label by its own rules, and what is under test is the words)."""
    K._get_identity_state(identity, keys)
    with K._KAME_LOCK:
        for key in keys:
            K._KAME_KEY_HEALTH[identity]["keys"][key]["sick_until"] = time.time() + seconds


def waiting_call(vt, agent, ctx, limit):
    """Run the carousel inside the agent's chat call until ``limit`` virtual seconds pass."""
    with pytest.raises(asyncio.TimeoutError):
        vt.run(agent.call_chat_model(K._kame_carousel(None, ctx)), limit=limit)


# --- the words -----------------------------------------------------------------------------

def test_every_key_resting_45_minutes_is_said_on_the_status_line_within_two_seconds(vt):
    # (1.8.3.1: the first write comes with the first tick; the line then counts down once a second)
    rest_all(KEYS[:1], 2700)
    agent = FakeAgent()
    ctx, _ = make_ctx(streaming({}), keys=KEYS[:1])
    waiting_call(vt, agent, ctx, 5)
    first = next((t, h) for t, h in agent.item.updates if h != PLAIN)
    assert first[1] == WAIT_45
    assert first[0] - vt.WALL0 <= 2.0
    # cancelled mid-wait (stop pressed): Agent Zero's own words are back, nothing left standing
    assert agent.item.updates[-1][1] == PLAIN and agent.item.heading == PLAIN


def test_the_line_counts_the_keys_of_the_model_it_waits_on_the_chip_counts_all(vt):
    # review M2 (this test used to say "1 of 2" on the line): a ready key of ANOTHER model does not make
    # the waited-on model's line say a key is ready. The chip keeps the global physical count.
    rest_all(KEYS[:1], 2700)
    K._get_identity_state("gemini:gemini-3.5-flash", ["AIzaSyLINE-other-000000"])      # a second, ready key
    agent = FakeAgent()
    ctx, _ = make_ctx(streaming({}), keys=KEYS[:1])
    waiting_call(vt, agent, ctx, 3)
    # 1.8.3.1 (D2): the line names the model whose keys all rest; it carries no count (the asking line does)
    assert any("Every key of gemini-3.8-flash is resting \u00b7 next key in 45m 00s" in h for h in agent.headings())
    assert not any("keys ready" in h for h in agent.headings() if "resting" in h)
    assert K.status_view()["totals"]["phrase"] == "1 of 2 keys ready"


def test_a_120_hour_wait_still_says_the_time_left_never_the_age(vt):
    rest_all(KEYS[:1], 2700)
    agent = FakeAgent()
    st = K._KameSleepState()
    st.cold_since = time.time() - 120 * 3600           # the wait is five days old
    async def go():
        K._kame_wait_notice_tick(st, ID, KEYS[:1], "chat", "m")
    vt.run(agent.call_chat_model(go()))
    assert agent.item.heading == WAIT_45
    assert "120" not in agent.item.heading and "h)" not in agent.item.heading and "since" not in agent.item.heading
    # ... and the age is on the page, as "waiting since", from the same call
    assert st.cold_since < time.time() - 119 * 3600


@pytest.mark.parametrize("seconds, words", [
    (0.2, "1 s"), (12, "12 s"), (59, "59 s"), (60, "1 min"), (2699, "45 min"), (3600, "1 h 00 min"),
    (3900, "1 h 05 min"), (7199, "2 h 00 min"),
])
def test_one_formatter_for_a_time_left(seconds, words):
    assert K._kame_fmt_left(seconds) == words


def test_past_three_hours_the_clock_time_is_said_not_decimals_of_hours():
    text = K._kame_fmt_left(5 * 3600 + 7 * 60, now=time.mktime((2027, 1, 15, 9, 0, 0, 0, 0, -1)))
    assert text == "back at 14:07"
    # 1.8.3.1 (D2): the live line counts the wait down in the page's format (45m 00s, 1h 02m), never decimals
    line = K._kame_live_line({"kind": "waiting", "model": "m", "eta_s": 30 * 3600})
    assert line.startswith("\u23f3 Every key of m is resting \u00b7 next key in ")
    assert not re.search(r"\d\.\dh", line)


def test_no_next_key_estimate_is_not_invented():
    assert K._kame_live_line({"kind": "waiting", "model": "m", "eta_s": None}) == \
        "\u23f3 Every key of m is resting - waiting for one to come back"


# --- no toast, no chat row ------------------------------------------------------------------

def test_no_notification_is_sent_at_15_s_90_s_or_two_hours(vt, monkeypatch):
    sent = []
    notification = types.ModuleType("helpers.notification")

    class NotificationManager:
        @staticmethod
        def send_notification(*a, **k):
            sent.append((a, k))

    class Anything:
        def __init__(self, *a):
            pass

    notification.NotificationManager = NotificationManager
    notification.NotificationType = notification.NotificationPriority = Anything
    monkeypatch.setitem(sys.modules, "helpers.notification", notification)
    monkeypatch.setattr(K, "_KAME_MAX_HOLD_S", 20 * 3600.0)      # the default ceiling is one hour
    rest_all(KEYS[:1], 10 * 3600)
    agent = FakeAgent()
    ctx, _ = make_ctx(streaming({}), keys=KEYS[:1])
    waiting_call(vt, agent, ctx, 2 * 3600 + 60)
    assert sent == []
    assert agent.context.log.logged == []           # and no row in the chat after 90 s either (Q2)
    assert not hasattr(K, "_kame_toast") and not hasattr(K, "_kame_wait_notice_toast")


def test_nothing_shipped_raises_a_notification_for_a_wait():
    shipped = [p for p in ROOT.rglob("*") if p.suffix in (".py", ".html", ".js")
               and not {"tests", "__pycache__"} & set(p.relative_to(ROOT).parts)]
    for path in shipped:
        text = path.read_text(encoding="utf-8", errors="replace")
        assert "NotificationManager" not in text, path
        assert "kame-rotation-wait" not in text, path
        assert "_kame_toast(" not in text, path


# --- giving the line back -------------------------------------------------------------------

def test_a_key_that_comes_back_before_any_text_gives_agent_zeros_words_back(vt):
    rest_all(KEYS[:1], 5)
    agent = FakeAgent()
    ctx, _ = make_ctx(streaming({}), keys=KEYS[:1])
    result = vt.run(agent.call_chat_model(K._kame_carousel(None, ctx)), limit=120)
    assert result[0].startswith("answer from")
    headings = agent.headings()
    assert any("is resting" in h for h in headings)
    assert headings[-1] == PLAIN and agent.item.heading == PLAIN


def test_a_subordinate_agent_keeps_its_own_prefix(vt):
    rest_all(KEYS[:1], 2700)
    sub = FakeAgent("A1")
    ctx, _ = make_ctx(streaming({}), keys=KEYS[:1])
    waiting_call(vt, sub, ctx, 3)
    assert WAIT_45.replace("A0:", "A1:") in [h for _t, h in sub.item.updates]
    assert all(h.startswith("A1: ") for _t, h in sub.item.updates)
    assert sub.item.updates[-1][1] == "A1: Calling LLM..."


def test_without_the_log_item_the_logs_progress_line_is_used(vt):
    rest_all(KEYS[:1], 2700)
    agent = FakeAgent(item=False)
    ctx, _ = make_ctx(streaming({}), keys=KEYS[:1])
    waiting_call(vt, agent, ctx, 3)
    log = agent.context.log
    assert log.progress[0] == (WAIT_45, True)       # (the same words reach the progress line)
    assert log.progress[-1] == ("", False)


def test_without_any_seam_nothing_is_raised(vt):
    rest_all(KEYS[:1], 2700)
    st = K._KameSleepState()
    K._kame_wait_notice_tick(st, ID, KEYS[:1], "chat", "m")                 # no agent at all
    K._kame_wait_notice_finish(st, "stopped")
    bare = FakeAgent(item=False, log=FakeLog(progress=False))               # a log without set_progress
    async def go():
        K._kame_wait_notice_tick(st, ID, KEYS[:1], "chat", "m")
        K._kame_wait_notice_finish(st, "resumed")
    vt.run(bare.call_chat_model(go()))
    odd = FakeAgent()
    odd.item.update = lambda **kw: (_ for _ in ()).throw(RuntimeError("the log is having a bad day"))
    async def go2():
        K._kame_wait_notice_tick(st, ID, KEYS[:1], "chat", "m")
        K._kame_wait_notice_finish(st, "stopped")
    vt.run(odd.call_chat_model(go2()))


def test_the_setting_off_leaves_the_heading_alone(vt):
    K.set_wait_notice(False)
    rest_all(KEYS[:1], 2700)
    agent = FakeAgent()
    ctx, _ = make_ctx(streaming({}), keys=KEYS[:1])
    waiting_call(vt, agent, ctx, 5)
    assert agent.item.updates == [] and agent.context.log.progress == []
    assert ctx["status_text"] is None or "is resting" in str(ctx["status_text"])    # the chip still knows


def test_a_background_job_never_rewrites_the_chats_line(vt):
    rest_all(KEYS[:1], 2700)
    agent = FakeAgent()
    ctx, _ = make_ctx(streaming({}), keys=KEYS[:1], call_type="Util")
    with pytest.raises(asyncio.TimeoutError):
        vt.run(agent.call_chat_model(K._kame_carousel(None, ctx)), limit=5)
    assert agent.item.updates == []


def test_the_line_changes_at_most_once_a_second(vt):
    rest_all(KEYS[:1], 30)                      # "next key in 30s", "29s" ... changes every second
    agent = FakeAgent()
    ctx, _ = make_ctx(streaming({}), keys=KEYS[:1])
    waiting_call(vt, agent, ctx, 12)
    stamps = [t for t, h in agent.item.updates if h != PLAIN]
    assert 10 <= len(stamps) <= 13              # 1.8.3.1 (D2): the page counts in seconds, so does the line
    assert all(b - a >= 0.99 for a, b in zip(stamps, stamps[1:]))


def test_the_same_words_are_not_written_twice(vt):
    rest_all(KEYS[:1], 2700)
    agent = FakeAgent()
    ctx, _ = make_ctx(streaming({}), keys=KEYS[:1])
    waiting_call(vt, agent, ctx, 30)
    waiting = [h for h in agent.headings() if h != PLAIN]
    assert waiting.count(WAIT_45) == 1


# --- who races the slow model -----------------------------------------------------------------

def test_a_slow_model_raced_by_the_next_one_is_said_and_given_back(world, vt):
    K.set_fallback_models("google/gemini-3.7-flash")
    agent = FakeAgent()
    ctx, _ = make_ctx(streaming({k: 30.0 for k in KEYS}))
    result = vt.run(agent.call_chat_model(K._kame_carousel(None, ctx)), limit=60)
    assert result[0] == "answer from google/gemini-3.7-flash"
    # 1.8.3.1 (D2): the cross race has no sentence of its own; the line says who is asked, and the agent's
    # own words come back at the end
    assert any(h.startswith("A0: \u23f3 Asking ") for h in agent.headings()), agent.headings()
    assert agent.headings()[-1] == PLAIN


# --- the circle step uses the same words ---------------------------------------------------------

def test_the_busy_best_model_line_uses_the_one_formatter():
    assert K._kame_live_line({"kind": "waiting", "model": "m", "eta_s": 12}) == \
        "\u23f3 Every key of m is resting \u00b7 next key in 12s"


# --- the host seams the status line stands on ----------------------------------------------------

def _checker():
    spec = importlib.util.spec_from_file_location("kame_a0_check_for_status_line", ROOT / "tools" / "a0_upgrade_check.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_seams_are_in_the_upgrade_checker_and_the_compatibility_file():
    facts = {f["id"]: f for f in _checker().HOST_FACTS}
    needles = " ".join(str(f["needle"]) for f in facts.values())
    for seam in ("log_item_generating", "def update(", "def set_progress"):
        assert seam in needles, seam
    assert not any("notification" in f["id"].lower() for f in facts.values())
    text = (ROOT / "COMPATIBILITY.md").read_text(encoding="utf-8")
    for seam in ("log_item_generating", "LogItem.update", "Log.set_progress"):
        assert seam in text, seam


V212 = ROOT.parent / "research" / "1.8.1.1" / "a0-v2.12"


@pytest.mark.skipif(not V212.is_dir(), reason="no Agent Zero checkout on this machine")
def test_the_seams_hold_on_the_agent_zero_checkout_on_this_machine():
    ok, broken = _checker().check_host_facts(str(V212))
    status = [b for b in broken if any(w in b["id"] for w in ("status line", "heading", "progress"))]
    assert status == []

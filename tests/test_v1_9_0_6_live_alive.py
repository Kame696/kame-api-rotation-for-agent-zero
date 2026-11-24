"""1.9.0.6 (0019 A5, parity with Hermes 0037 C21) - the live line looks alive.

Every in-flight state ends with the reply's running time (``· 23s``), the chip's dot pulses while a reply is in
flight, and a reply that ends WITHOUT an answer says ``✕ no answer - <reason>`` for ten seconds, then clears; a new
reply of the same conversation replaces it at once.
"""
from __future__ import annotations

import time

import pytest

from test_v1_8_2_7_speed_fallback import clean  # noqa: F401  (loads the host stubs first)
from test_v1_8_3_1_chip_popover import NODE, report, run_js
import kame_engine as K

KEYS = {"pools": []}


@pytest.fixture(autouse=True)
def calm():
    with K._KAME_LOCK:
        K._KAME_IN_FLIGHT.clear()
        K._KAME_LAST_END.clear()
    yield
    with K._KAME_LOCK:
        K._KAME_IN_FLIGHT.clear()
        K._KAME_LAST_END.clear()


def test_the_heading_carries_the_replys_running_time():
    ctx = {"main_chat": True, "identity": "google:gemini-3.8-flash",
           "flight": {"started": time.time() - 23, "job": "chat"}}
    K._kame_say(ctx, "calling", ready=3, keys=3)
    assert ctx["status_text"] == "⏳ Asking gemini-3.8-flash · 3 of 3 keys ready · 23s"


def test_the_silent_line_keeps_its_own_seconds_only():
    ctx = {"main_chat": True, "identity": "google:gemini-3.8-flash",
           "flight": {"started": time.time() - 23, "job": "chat"}}
    K._kame_say(ctx, "silent", ready=3, keys=3, seconds=12)
    assert ctx["status_text"].endswith("no first word yet (12s)")


def test_a_reply_without_an_answer_is_said_for_ten_seconds_then_clears():
    ctx = {"main_chat": True, "identity": "google:gemini-3.8-flash", "flight": {"context": "chat-1"}}
    K._kame_note_failed(ctx, TimeoutError("timed out"))
    at = K._KAME_LAST_END["chat-1"][0]
    shown = K._kame_activity(KEYS, "chat-1", at + 3)
    assert shown["kind"] == "ended" and K._kame_live_line(shown) == "✕ no answer - it timed out"
    assert K._kame_activity(KEYS, "chat-1", at + 11)["kind"] == "idle"
    assert K._kame_activity(KEYS, "chat-2", at + 3)["kind"] == "idle", "another chat never shows it"


def test_a_stop_is_not_an_end_state():
    import asyncio

    ctx = {"main_chat": True, "identity": "google:gemini-3.8-flash", "flight": {"context": "chat-1"}}
    K._kame_note_failed(ctx, asyncio.CancelledError())
    assert "chat-1" not in K._KAME_LAST_END


def test_an_in_flight_reply_wins_over_the_end_line():
    with K._KAME_LOCK:
        K._KAME_LAST_END["chat-1"] = (time.time(), "it timed out")
        K._KAME_IN_FLIGHT["x"] = {"job": "chat", "context": "chat-1", "identity": "google:m", "started": time.time()}
    assert K._kame_activity(KEYS, "chat-1", time.time())["kind"] == "calling"


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_the_chip_counts_the_running_time_pulses_and_shows_the_end():
    now_s = 1_800_000_000
    data = report()
    data["view"]["generated_at"] = now_s
    data["view"]["activity"] = {"kind": "calling", "model": "gemini-3.8-flash", "name": "gemini-3.8-flash",
                                "healthy": 3, "keys": 3, "attempt": 1, "started_at": now_s - 125}
    out = run_js(f"const p = V.chipPopover(data, {now_s * 1000}); return [p.sentence, p.live];", data)
    assert out == ["⏳ Asking gemini-3.8-flash · 3 of 3 keys ready · 2m 05s", True]
    data["view"]["activity"] = {"kind": "ended", "reason": "it timed out"}
    out = run_js(f"const p = V.chipPopover(data, {now_s * 1000}); return [p.sentence, p.live];", data)
    assert out == ["✕ no answer - it timed out", False]

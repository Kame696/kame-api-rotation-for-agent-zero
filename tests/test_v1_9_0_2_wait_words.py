"""v1.9.0.2 (Hermes H4 parity) - the wait is said in the provider's words. Wording only.

1. Daily case: every key resting and the provider itself named each key's return (Google names the next midnight,
   US Pacific - 68,173 s seen 2026-11-15) while ``max_hold_seconds`` rests a key an hour: the waiting line names
   the provider's time beside KAME's next try. Only when EVERY key carries a named return still ahead and the
   soonest is later than the next try - otherwise some key may be back sooner.
2. Bare case: every key of the model refused THIS call naming no time, for more than 60 s: the line says so
   ("the provider gave no time") instead of only "trying another key". No time is invented.
Nothing selects, rests or waits differently.
"""
from __future__ import annotations

import time

import pytest

import test_v1_8_2_7_speed_fallback  # noqa: F401  (the Agent Zero stubs kame_engine imports)
import kame_engine as K

ID = "google:gemini-3.8-flash"
KEYS = ["AIzaSyTEST1902-a", "AIzaSyTEST1902-b"]


@pytest.fixture(autouse=True)
def clean_tables():
    K._KAME_STATED_BACK.clear()
    yield
    K._KAME_STATED_BACK.clear()


# -- 1. the provider's own return time -------------------------------------------------------------------

def test_every_key_with_a_named_return_far_ahead_says_the_providers_time():
    for k in KEYS:
        K._kame_note_stated_back(ID, k, "daily_quota", 68173.0, "provider")
    note = K._kame_provider_back_note(ID, KEYS, 3000.0)
    back = time.strftime("%H:%M", time.localtime(time.time() + 68173.0))
    assert note == f" - Google says the quota is back around {back}"
    line = K._kame_live_line({"kind": "waiting", "model": "gemini-3.8-flash", "eta_s": 3000.0, "back": note})
    assert line.startswith("⏳ Every key of gemini-3.8-flash is resting · next key in ")
    assert line.endswith(note)


def test_one_key_without_a_named_return_says_nothing_more():
    K._kame_note_stated_back(ID, KEYS[0], "daily_quota", 68173.0, "provider")
    assert K._kame_provider_back_note(ID, KEYS, 3000.0) == ""


def test_a_return_kame_would_reach_first_anyway_is_not_said():
    for k in KEYS:
        K._kame_note_stated_back(ID, k, "per_minute", 30.0, "provider")
    assert K._kame_provider_back_note(ID, KEYS, 40.0) == ""


def test_kames_own_number_is_never_said_as_the_providers():
    for k in KEYS:
        K._kame_note_stated_back(ID, k, "daily_quota", 3600.0, "kame")
    assert K._kame_provider_back_note(ID, KEYS, 60.0) == ""


def test_an_answer_forgets_the_named_return():
    for k in KEYS:
        K._kame_note_stated_back(ID, k, "daily_quota", 68173.0, "provider")
    K._get_identity_state(ID, KEYS)
    K._mark_key_health(ID, KEYS[0], True)
    assert (ID, KEYS[0]) not in K._KAME_STATED_BACK
    assert K._kame_provider_back_note(ID, KEYS, 60.0) == ""


def test_the_waiting_line_without_a_named_return_is_word_for_word_as_before():
    line = K._kame_live_line({"kind": "waiting", "model": "m", "eta_s": 54.0})
    assert line == f"⏳ Every key of m is resting · next key in {K._kame_live_duration(54.0)}"
    assert K._kame_live_line({"kind": "waiting", "model": "m"}) == \
        "⏳ Every key of m is resting - waiting for one to come back"


# -- 2. every key refused naming no time ------------------------------------------------------------------

def ctx(age_s):
    return {"identity": ID, "all_keys": list(KEYS), "kame_started_perf": time.perf_counter() - age_s}


def test_every_key_refused_with_no_time_for_over_a_minute_is_said():
    c = ctx(75.0)
    for k in KEYS:
        K._kame_note_unsized(c, ID, k, "per_minute", "kame")
    waited = K._kame_no_time_given(c)
    assert waited is not None and waited >= 60.0
    line = K._kame_live_line({"kind": "calling", "model": "m", "attempt": 5, "ready": 0, "keys": 2,
                              "no_time_s": waited})
    assert line.startswith("↻ m: every key refused for ") and "the provider gave no time · 0 of 2 keys ready" in line


def test_under_a_minute_or_with_one_key_not_yet_refused_the_line_is_as_before():
    c = ctx(30.0)
    for k in KEYS:
        K._kame_note_unsized(c, ID, k, "per_minute", "kame")
    assert K._kame_no_time_given(c) is None
    c = ctx(90.0)
    K._kame_note_unsized(c, ID, KEYS[0], "per_minute", "kame")
    assert K._kame_no_time_given(c) is None
    line = K._kame_live_line({"kind": "calling", "model": "m", "attempt": 2, "ready": 1, "keys": 2})
    assert line == "↻ m: trying another key · 1 of 2 keys ready"


def test_a_refusal_that_names_a_time_takes_the_key_out_of_the_count():
    c = ctx(90.0)
    for k in KEYS:
        K._kame_note_unsized(c, ID, k, "per_minute", "kame")
    K._kame_note_unsized(c, ID, KEYS[1], "per_minute", "provider")
    assert K._kame_no_time_given(c) is None


def test_a_call_with_no_start_or_no_keys_says_nothing():
    assert K._kame_no_time_given({"identity": ID, "all_keys": KEYS}) is None
    assert K._kame_no_time_given({"identity": ID, "all_keys": [], "kame_started_perf": 0.0}) is None

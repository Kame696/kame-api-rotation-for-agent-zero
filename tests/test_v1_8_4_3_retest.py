"""1.8.4.3 (A10, PLAN section 8) - a model left out is looked at again; a dead best model is asked less often.
Released as 1.9.0.0.

1. Where KAME chooses among the models of the list that are NOT the one the reply was asked for (the order of the
   ladder: the start target, the stand-in, the circle order, a moved title), a model remembered as failing is left
   behind only until its retest is due: its latest failure + 600 s x 2^(k-1), k = its trailing consecutive failures,
   at most 7200 s. Then it is a candidate in the list's own order. The model the reply was asked for keeps 1.8.4.2's
   rule: asked beside, never made to lead.
2. The start probe of a remembered-failing best model that is NOT in a busy window (or its tail) is due every
   20 s x 2^(k-2) for k >= 2, at most 300 s. Inside a busy window or its tail it stays every 20 s.

A virtual clock (kame_vclock.py). No Agent Zero installation, no network.
"""
from __future__ import annotations

import time

import pytest

from test_v1_8_2_7_speed_fallback import (  # noqa: F401  (fixtures are used by name)
    ID, KEYS, clean, fake_models, make_ctx, streaming,
)
from kame_vclock import vt  # noqa: F401
import kame_engine as K
import kame_model_health as mh

E1 = "google/gemini-3.8-flash"
E2 = "google/gemini-3.7-flash"
E3 = "google/gemini-3.6-flash"
I1, I2, I3 = "google:gemini-3.8-flash", "google:gemini-3.7-flash", "google:gemini-3.6-flash"


@pytest.fixture(autouse=True)
def memory(tmp_path, monkeypatch):
    monkeypatch.delenv("KAME_NO_REMEMBERED_TROUBLE", raising=False)
    mh.HEALTH.configure(directory=tmp_path, calls_path=tmp_path / "no-calls.jsonl")
    for table in (K._KAME_BUSY_UNTIL, K._KAME_BUSY_WINDOW, K._KAME_BUSY_KIND, K._KAME_PROBED_AT):
        table.clear()
    K._KAME_REPLIES.clear()
    K._KAME_TURNS.clear()
    yield
    mh.HEALTH.configure(directory=None)
    for table in (K._KAME_BUSY_UNTIL, K._KAME_BUSY_WINDOW, K._KAME_PROBED_AT):
        table.clear()


def record(identity, pattern, last_age):
    """``pattern`` is oldest first, 1 = answered, 0 = failed (the owner's 20:30 record: 3.6 ``1110111110``);
    the last outcome is ``last_age`` seconds old, the others a minute apart before it."""
    now = time.time()
    n = len(pattern)
    for i, ch in enumerate(pattern):
        mh.HEALTH.note(identity, ch == "1", now=now - last_age - (n - 1 - i) * 60.0)


def order(entries):
    return [e["identity"] for e in K._kame_prefer_not_risky(
        [{"identity": i, "label": i} for i in entries])]


# --- the schedule ----------------------------------------------------------------------------------------

def test_the_constants_and_the_trailing_count():
    assert (mh.RETEST_FIRST_S, mh.RETEST_MAX_S) == (600.0, 7200.0)
    record(I1, "1110111110", 100.0)
    assert mh.HEALTH.trailing_failures(I1) == 1
    record(I2, "0000000000", 100.0)
    assert mh.HEALTH.trailing_failures(I2) == 10
    record(I3, "01", 100.0)
    assert mh.HEALTH.trailing_failures(I3) == 0                      # the latest outcome is an answer
    assert mh.HEALTH.trailing_failures("google:never-seen") == 0


@pytest.mark.parametrize("fails, wait", [(2, 1200.0), (3, 2400.0), (4, 4800.0), (5, 7200.0), (9, 7200.0)])
def test_the_wait_doubles_with_the_trailing_failures_and_stops_at_two_hours(fails, wait):
    record(I1, "0" * fails, 0.0)
    now = time.time()
    assert mh.HEALTH.retest_due(I1, now=now + wait - 5.0) is False
    assert mh.HEALTH.retest_due(I1, now=now + wait + 5.0) is True


def test_one_failure_in_ten_is_not_remembered_failing_so_there_is_no_retest():
    record(I1, "1111111110", 0.0)
    assert mh.HEALTH.risky(I1) is False and mh.HEALTH.retest_due(I1) is False


def test_k1_waits_ten_minutes():
    record(I1, "1110111110", 0.0)                                    # k = 1, but two failures in ten: failing
    assert mh.HEALTH.risky(I1) is True
    assert mh.HEALTH.retest_due(I1, now=time.time() + 590.0) is False
    assert mh.HEALTH.retest_due(I1, now=time.time() + 610.0) is True


def test_a_success_clears_it_at_once():
    record(I1, "0000", 0.0)
    assert mh.HEALTH.risky(I1) is True
    mh.HEALTH.note(I1, True)
    assert mh.HEALTH.risky(I1) is False and mh.HEALTH.retest_due(I1) is False


def test_the_kill_switch_is_unchanged(monkeypatch):
    record(I1, "0000", 100000.0)
    monkeypatch.setenv("KAME_NO_REMEMBERED_TROUBLE", "1")
    assert mh.HEALTH.retest_due(I1) is False and mh.HEALTH.risky(I1) is False
    assert K._kame_probe_every(ID) == 20.0


# --- 1: the choices among the other models -----------------------------------------------------------------

def test_the_20_30_record_makes_the_fast_model_a_candidate_again():
    record(I3, "1110111110", 8 * 3600.0)                              # 3.6: eight of ten answered, failed 8 h ago
    record(I1, "0000000000", 5 * 86400.0 - 3600.0)                    # 3.8: ten in a row, long ago
    record(I2, "1111111111", 10.0)                                    # 3.7 healthy
    assert mh.HEALTH.risky(I3) is True and mh.HEALTH.retest_due(I3) is True
    # S1d of 1.8.4.2 left 3.6 behind for ever; its retest is due, so it is in the list's own order again
    assert order([I3, I2]) == [I3, I2]
    assert order([I1, I3, I2]) == [I1, I3, I2]                        # 3.8's retest is due too: all candidates
    record(I1, "0000000000", 60.0)                                    # 3.8 failed a minute ago: still left behind
    assert order([I1, I3, I2]) == [I3, I2, I1]


def test_a_fresh_failure_still_leaves_the_model_behind():
    record(I3, "1110111110", 120.0)                                   # failed two minutes ago
    record(I2, "1111111111", 10.0)
    assert order([I3, I2]) == [I2, I3]


def test_a_retest_that_is_due_only_after_a_longer_wait_for_k2():
    record(I3, "0111111100", 0.0)                                    # k = 2: twenty minutes
    record(I2, "1111111111", 10.0)
    assert order([I3, I2]) == [I2, I3]
    old = time.time()
    # twenty minutes later the same record: due
    assert mh.HEALTH.retest_due(I3, now=old + 1210.0) is True
    assert mh.HEALTH.retest_due(I3, now=old + 1190.0) is False


def test_never_drops_one_and_every_entry_risky_is_todays_order():
    record(I1, "0000", 8 * 3600.0)
    record(I2, "0000", 60.0)
    assert sorted(order([I2, I1])) == sorted([I1, I2])                # nothing is ever left out


def test_the_cap_is_two_hours_whatever_the_count():
    record(I3, "0000000000", 7300.0)                                  # ten in a row, 2 h and 100 s ago
    record(I2, "1111111111", 10.0)
    assert order([I3, I2]) == [I3, I2]                                # due: back in the list's order


def test_the_home_model_with_an_old_failure_is_still_asked_beside_s1_kept():
    record(ID, "0000", 8 * 3600.0)                                    # retest long due
    ctx, _ = make_ctx(streaming({}))
    assert K._kame_remembered_risky(ID, ctx) is True                  # asked beside, never made to lead
    assert K._kame_entry_risky(ID) is True                            # the plain question is unchanged
    assert K._kame_entry_risky(ID, retest=True) is False              # the choices among the others read the retest


# --- 2: the start probe of a dead best model -----------------------------------------------------------------

@pytest.mark.parametrize("fails, expected", [(1, 20.0), (2, 20.0), (3, 40.0), (4, 80.0), (5, 160.0),
                                             (6, 300.0), (10, 300.0)])
def test_the_ask_beside_backs_off_20_40_80_160_then_every_five_minutes(fails, expected):
    record(ID, "0" * fails if fails > 1 else "10", 5.0)
    assert K._kame_probe_every(ID) == expected


def test_inside_a_busy_window_or_its_tail_it_stays_every_twenty_seconds():
    record(ID, "0" * 10, 5.0)
    assert K._kame_probe_every(ID) == 300.0
    K._KAME_BUSY_UNTIL[ID] = time.time() + 20.0
    K._KAME_BUSY_WINDOW[ID] = 30.0
    assert K._kame_probe_every(ID) == 20.0                            # a storm: the window
    K._KAME_BUSY_UNTIL[ID] = time.time() - 5.0
    assert K._kame_probe_every(ID) == 20.0                            # its tail (E8)
    K._KAME_BUSY_UNTIL[ID] = time.time() - 1000.0
    assert K._kame_probe_every(ID) == 300.0                           # long past it: dead, not busy


def test_the_start_probe_waits_its_back_off_before_it_asks_a_dead_best_model_again(fake_models, vt):
    K.set_fallback_models(E2)
    record(ID, "0" * 5, 5.0)                                          # k = 5: every 160 s
    ctx, _ = make_ctx(streaming({}))
    K._KAME_PROBED_AT[ID] = time.time() - 100.0                       # asked 100 s ago
    assert K._kame_start_probe_allowed(ctx, ID) is False              # 20 s would have asked again
    ctx, _ = make_ctx(streaming({}))
    K._KAME_PROBED_AT[ID] = time.time() - 170.0
    assert K._kame_start_probe_allowed(ctx, ID) is True
    assert ctx.get("probe_risky")


def test_k2_keeps_the_twenty_second_clock_of_1842(fake_models, vt):
    K.set_fallback_models(E2)
    record(ID, "00", 5.0)
    ctx, _ = make_ctx(streaming({}))
    K._KAME_PROBED_AT[ID] = time.time() - 25.0
    assert K._kame_start_probe_allowed(ctx, ID) is True
    ctx, _ = make_ctx(streaming({}))
    K._KAME_PROBED_AT[ID] = time.time() - 10.0
    assert K._kame_start_probe_allowed(ctx, ID) is False

"""1.9.0.3 (C3) - the Speed cost line says what it measures.

Only a backup request draws from the Speed budget, so only backups are compared with what Speed allows. Keys
asked together while the keys were running dry and other models of the list asked beside a late reply are told
apart ("Not limited by Speed"); a re-ask of a reply that stopped mid-thought has its own neutral sentence. The
words are the Hermes words, with the host's name.
"""
from __future__ import annotations

import shutil

import pytest

import kame_hedging as H

NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")


def ledger(**counts):
    ledger = H.SpeedLedger()
    for kind, n in counts.items():
        ledger.note(kind, n)
    return ledger


def test_the_pack_keeps_every_field_and_adds_what_speed_limits():
    view = ledger(replies=8, burst=26, race=1).view()["hour"]
    assert view["extra_pct"] == 337.5                      # the sum the page used to compare with the cap
    assert view["limited"] == 0 and view["limited_pct"] == 0.0
    for name in ("replies", "backup", "race", "burst", "circle", "rescue", "rescue_won"):
        assert name in view


def test_only_backups_are_limited_by_speed_and_a_rescue_is_not_counted_as_limited():
    view = ledger(replies=10, backup=3, race=2, burst=4, rescue=5, rescue_won=1).view()["hour"]
    assert view["limited"] == 3 and view["limited_pct"] == 30.0
    assert view["extra_pct"] == 140.0


def test_without_replies_there_is_no_percentage():
    view = ledger().view()["hour"]
    assert view["limited"] == 0 and view["limited_pct"] is None and view["extra_pct"] is None


def run_line(speed):
    from test_v1_8_2_9_view_logic import run_js
    return run_js("return V.speedCostLine(data);", speed)


@needs_node
def test_the_owners_numbers_are_not_over_the_cap_and_name_what_speed_does_not_limit():
    speed = {"cap_pct": 10, "hour": {"replies": 8, "backup": 0, "race": 1, "burst": 26, "rescue": 0,
                                     "extra_pct": 337.5, "limited": 0, "limited_pct": 0.0},
             "since_start": {"replies": 8, "limited_pct": 0.0, "extra_pct": 337.5}}
    out = run_line(speed)
    assert out["over"] is False
    assert out["text"] == ("Backup requests this hour: +0% (8 replies). Speed allows about +10%. "
                           "Not limited by Speed: 26 asked together while keys were running dry, "
                           "1 asked of another model of your list. "
                           "Since Agent Zero started: +0% backup requests.")


@needs_node
def test_a_case_over_the_cap_is_marked_over():
    speed = {"cap_pct": 10, "hour": {"replies": 10, "backup": 3, "limited": 3, "limited_pct": 30.0},
             "since_start": {"replies": 10, "limited_pct": 30.0}}
    out = run_line(speed)
    assert out["over"] is True
    assert out["text"].startswith("Backup requests this hour: +30% (10 replies). Speed allows about +10%.")
    # exactly at one and a half times the cap is not over; the old sum never decides it
    speed["hour"].update(limited=1, limited_pct=15.0, extra_pct=500.0)
    assert run_line(speed)["over"] is False


@needs_node
def test_the_rescues_have_their_own_neutral_sentence_and_the_won_ones_are_told():
    speed = {"cap_pct": 10, "hour": {"replies": 12, "limited_pct": 4.0, "rescue": 3, "rescue_won": 1},
             "since_start": {"replies": 30, "limited_pct": 6.0}}
    text = run_line(speed)["text"]
    assert text == ("Backup requests this hour: +4% (12 replies). Speed allows about +10%. "
                    "3 re-asked replies that stopped mid-thought. 1 of the re-asks answered first. "
                    "Since Agent Zero started: +6% backup requests.")
    speed["hour"].update(rescue=1, rescue_won=0)
    one = run_line(speed)["text"]
    assert "1 re-asked a reply that stopped mid-thought." in one and "answered first" not in one
    speed["hour"].update(rescue=0, rescue_won=0)
    assert "mid-thought" not in run_line(speed)["text"]


@needs_node
def test_no_replies_this_hour_says_what_speed_allows_in_backup_requests():
    out = run_line({"cap_pct": 10, "hour": {"replies": 0, "limited_pct": None}})
    assert out["text"] == "No replies this hour yet. Speed allows about +10% backup requests."
    assert run_line({"cap_pct": 0, "hour": {}}) is None

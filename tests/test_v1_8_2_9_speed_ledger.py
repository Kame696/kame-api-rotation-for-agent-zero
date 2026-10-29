"""1.8.2.9 (E5, decisions/0007) - "Extra requests this hour".

A ledger of 60 one-minute buckets counts replies and the extra requests KAME sent for
speed (a backup key, the next model racing a slow reply, keys asked together, a circle
back to the best model). The panel shows the share next to what the setting allows. The
field names are Hermes' (`speed.hour.extra_pct`, ...), so the page code is the same.
"""
from __future__ import annotations

import pytest

from test_v1_8_2_7_speed_fallback import (  # noqa: F401  (fixtures are used by name)
    ID, KEYS, clean, fake_models, make_ctx, run, streaming, _first_then_rest,
)
import kame_engine as K
import kame_hedging as H

FIELDS = {"replies", "backup", "race", "burst", "circle", "extra_pct"}


@pytest.fixture(autouse=True)
def ledger():
    H.SPEED_LEDGER.clear()
    yield
    H.SPEED_LEDGER.clear()


def test_a_note_older_than_an_hour_drops_out_of_the_hour_and_stays_in_the_total():
    led = H.SpeedLedger()
    led.note("replies", 4, now=0.0)
    assert led.view(now=59 * 60 + 30)["hour"]["replies"] == 4        # minute 59: still inside
    assert led.view(now=61 * 60)["hour"]["replies"] == 0             # minute 61: gone
    assert led.view(now=61 * 60)["since_start"]["replies"] == 4


def test_old_minutes_are_forgotten_not_kept_for_ever():
    led = H.SpeedLedger()
    for minute in range(0, 300):
        led.note("replies", 1, now=minute * 60.0)
    assert len(led._minutes) <= 62


def test_extra_pct_is_none_without_replies_and_a_share_with_them():
    led = H.SpeedLedger()
    assert led.view(now=10.0)["hour"]["extra_pct"] is None
    led.note("backup", 1, now=10.0)
    assert led.view(now=10.0)["hour"]["extra_pct"] is None           # extra with no reply: no share to state
    led.note("replies", 10, now=11.0)
    assert led.view(now=12.0)["hour"]["extra_pct"] == 10.0


def test_extra_counts_backup_race_and_burst_but_not_circle():
    led = H.SpeedLedger()
    led.note("replies", 100, now=1.0)
    for kind, n in (("backup", 2), ("race", 3), ("burst", 5), ("circle", 40)):
        led.note(kind, n, now=2.0)
    hour = led.view(now=3.0)["hour"]
    assert hour["extra_pct"] == 10.0 and hour["circle"] == 40


def test_the_view_has_hermes_field_names():
    led = H.SpeedLedger()
    led.note("replies", 1, now=1.0)
    view = led.view(now=2.0)
    assert set(view) == {"hour", "since_start"}
    assert set(view["hour"]) == FIELDS and set(view["since_start"]) == FIELDS


def test_an_unknown_kind_is_ignored_and_never_raises():
    led = H.SpeedLedger()
    led.note("coffee", 1, now=1.0)
    led.note("replies", "x", now=1.0)
    assert led.view(now=2.0)["hour"]["replies"] == 0


def test_the_engine_counts_a_backup_on_ten_replies_as_ten_percent(monkeypatch):
    monkeypatch.setattr(H.HEDGE_TRACKER, "stream_triggers", lambda *a, **k: (30.0, 60.0))
    for _ in range(9):
        ctx, _f = make_ctx(streaming({}))
        run(ctx)
    monkeypatch.setattr(H.HEDGE_TRACKER, "stream_triggers", lambda *a, **k: (0.05, 5.0))
    first = KEYS[0]
    ctx, _f = make_ctx(streaming({first: 0.5}))
    monkeypatch.setattr(K, "_get_best_key", _first_then_rest(first))
    run(ctx)
    hour = K.speed_report()["hour"]
    assert hour["replies"] == 10 and hour["backup"] == 1 and hour["extra_pct"] == 10.0


def test_a_background_call_is_not_a_reply():
    ctx, _f = make_ctx(streaming({}), call_type="Util")
    run(ctx)
    assert K.speed_report()["hour"]["replies"] == 0


def test_the_speed_report_carries_mode_cap_and_pauses():
    K.set_hedge_mode("off")
    assert K.speed_report()["cap_pct"] == 0 and K.speed_report()["mode"] == "off"
    K.set_hedge_mode("adaptive")
    assert K.speed_report()["cap_pct"] == 10 and K.speed_report()["mode"] == "adaptive"
    K.set_hedge_mode("max")
    report = K.speed_report()
    assert report["cap_pct"] == 30 and report["mode"] == "max" and report["paused_until"] == {}
    H.HEDGE_TRACKER.note_quota_refusal(ID)
    paused = K.speed_report()["paused_until"]
    assert set(paused) == {ID} and paused[ID] > __import__("time").time() + 590


def test_the_cap_follows_the_budget_setting():
    K.set_hedge_mode("max")
    H.configure(hedge_budget_pct=20.0)
    try:
        assert K.speed_report()["cap_pct"] == 60
    finally:
        H.configure(hedge_budget_pct=10.0)


def test_pool_report_speed_has_the_ledger():
    speed = K.pool_report()["speed"]
    assert {"mode", "cap_pct", "paused_until", "hour", "since_start"} <= set(speed)
    assert set(speed["hour"]) == FIELDS
    assert {"backups", "probe_bursts", "model_switches", "hedge_mode"} <= set(speed)   # (guard) 1.8.2.8 fields stay

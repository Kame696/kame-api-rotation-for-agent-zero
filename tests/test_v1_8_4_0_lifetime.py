"""1.8.4.0 E5 and E6: what KAME did over time, sent in the last 24 hours, the provider's own daily number.

Display only. The counters live in ``lifetime.json`` beside the plugin's other data. The same
table of cases as the Hermes port (``tests/test_1_8_4_0_lifetime.py``).
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from test_v1_8_2_7_speed_fallback import ID, KEYS, Refusal, clean, make_ctx, streaming  # noqa: F401
import kame_engine as K
import kame_evidence as E
import kame_lifetime as L

IDENT = "gemini:gemini-3.8-flash"
NOW = 1_800_000_000.0

GOOGLE = {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "details": [
    {"@type": "type.googleapis.com/google.rpc.QuotaFailure", "violations": [{
        "quotaMetric": "generativelanguage.googleapis.com/generate_content_free_tier_requests",
        "quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier",
        "quotaDimensions": {"location": "global", "model": "gemini-3.8-flash"},
        "quotaValue": "20"}]},
    {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "5s"}]}}


class Clock:
    def __init__(self):
        self.now = NOW

    def __call__(self):
        return self.now


@pytest.fixture()
def box(tmp_path):
    clock = Clock()
    path = tmp_path / "lifetime.json"
    return L.Lifetime(path=lambda: path, clock=clock), path, clock


# -- E5 ----------------------------------------------------------------------------------------
def test_a_clean_reply_adds_one_to_replies_only(box):
    life, _, _ = box
    life.reply()
    snap = life.snapshot()
    assert (snap["replies"], snap["saved"]) == (1, 0)


def test_a_reply_after_a_failed_attempt_is_saved(box):
    life, _, _ = box
    life.reply(saved=True, key_switches=1)
    snap = life.snapshot()
    assert (snap["replies"], snap["saved"], snap["key_switches"]) == (1, 1, 1)


def test_the_write_is_throttled_to_once_a_minute(box):
    life, path, clock = box
    life.reply()
    assert life.writes == 1
    for _ in range(5):
        clock.now += 5
        life.reply()
    assert life.writes == 1
    clock.now += 60
    life.reply()
    assert life.writes == 2
    assert json.loads(path.read_text(encoding="utf-8"))["replies"] == 7


def test_a_clean_exit_writes_what_is_left(box):
    life, path, clock = box
    life.reply()
    clock.now += 1
    life.reply()
    life.flush(True)
    assert json.loads(path.read_text(encoding="utf-8"))["replies"] == 2


def test_the_file_survives_a_restart_and_two_writers_add_up(box):
    life, path, clock = box
    life.reply()
    other = L.Lifetime(path=lambda: path, clock=clock)
    other.reply(saved=True)
    other.flush(True)
    life.reply()
    life.flush(True)
    final = json.loads(path.read_text(encoding="utf-8"))
    assert (final["replies"], final["saved"]) == (3, 1)
    assert L.Lifetime(path=lambda: path, clock=clock).snapshot()["replies"] == 3


def test_a_corrupt_file_starts_from_zero(box):
    life, path, _ = box
    path.write_text("{ this is not json", encoding="utf-8")
    assert life.snapshot()["replies"] == 0
    life.reply()
    life.flush(True)
    assert json.loads(path.read_text(encoding="utf-8"))["replies"] == 1


def test_a_store_that_cannot_be_written_loses_the_count_quietly(tmp_path):
    blocked = tmp_path / "file-not-dir"
    blocked.write_text("x", encoding="utf-8")
    life = L.Lifetime(path=lambda: blocked / "lifetime.json", clock=Clock())
    life.reply()
    assert life.snapshot()["replies"] == 1


def test_no_key_text_in_the_file(box):
    life, path, _ = box
    life.reply(saved=True)
    life.sent(IDENT)
    life.note_limit(IDENT, GOOGLE)
    life.flush(True)
    text = path.read_text(encoding="utf-8")
    assert "AIza" not in text and "key:" not in text
    assert set(json.loads(text)) == {"since", "replies", "saved", "key_switches", "model_switches", "sent", "limits"}


def test_a_file_that_cannot_be_read_right_now_keeps_the_counts(box, monkeypatch):
    life, _path, _clock = box
    life.reply()
    life.flush(True)
    life.reply()
    real = Path.read_text

    def busy(self, *a, **k):
        raise PermissionError("held by another process")

    monkeypatch.setattr(Path, "read_text", busy)
    assert life.flush(True) is False
    monkeypatch.setattr(Path, "read_text", real)
    assert life.flush(True)
    assert life.snapshot()["replies"] == 2


# -- E6 ----------------------------------------------------------------------------------------
def test_buckets_prune_past_25_hours(box):
    life, path, _ = box
    for ago in (30, 26, 24.5, 1, 0):
        life.sent(IDENT, now=NOW - ago * 3600)
    life.flush(True)
    hours = json.loads(path.read_text(encoding="utf-8"))["sent"][IDENT]
    assert len(hours) == 3 and min(int(h) for h in hours) >= int(NOW // 3600) - 25


def test_sent_24h_counts_only_the_last_24_hours(box):
    life, _, _ = box
    for ago in (0, 1, 23):
        life.sent(IDENT, now=NOW - ago * 3600)
    life.sent(IDENT, now=NOW - 24.5 * 3600)
    assert life.view(IDENT)["sent_24h"] == 3


def test_the_real_google_shape_gives_20_per_project():
    assert E.stated_daily_limit(GOOGLE) == (20, "project")


def test_a_per_key_day_limit_is_the_keys():
    body = {"error": {"details": [{"violations": [{"quotaId": "RequestsPerDay-FreeTier", "quotaValue": "50"}]}]}}
    assert E.stated_daily_limit(body) == (50, "key")


@pytest.mark.parametrize("body", [
    None, "", "text", 5, [], {}, {"error": "x"},
    {"error": {"details": [{"violations": [{"quotaId": "PerMinute", "quotaValue": "5"}]}]}},
    {"error": {"details": [{"violations": [{"quotaId": "GenerateRequestsPerDay", "quotaValue": "many"}]}]}},
    {"error": {"details": [{"violations": [{"quotaId": "GenerateRequestsPerDay", "quotaValue": "0"}]}]}},
    {"error": {"details": [{"violations": [{"quotaId": "GenerateRequestsPerDay"}]}]}},
    {"error": {"details": [{"violations": "oops"}]}},
    {"error": {"details": [{"violations": [
        {"quotaId": "GenerateContentInputTokensPerDayPerProjectPerModel-FreeTier", "quotaValue": "1000000"}]}]}},
])
def test_malformed_and_token_bodies_give_none(body):
    assert E.stated_daily_limit(body) is None


def test_latest_stated_limit_wins_and_persists(box):
    life, path, clock = box
    life.note_limit(IDENT, GOOGLE)
    clock.now += 10
    later = json.loads(json.dumps(GOOGLE))
    later["error"]["details"][0]["violations"][0]["quotaValue"] = "250"
    life.note_limit(IDENT, later)
    life.flush(True)
    again = L.Lifetime(path=lambda: path, clock=clock)
    assert again.view(IDENT)["daily_limit"] == 250 and again.view(IDENT)["daily_scope"] == "project"
    assert again.view("other:model")["daily_limit"] is None


def test_a_body_without_a_stated_limit_changes_nothing(box):
    life, _, _ = box
    life.note_limit(IDENT, GOOGLE)
    life.note_limit(IDENT, {"error": {"message": "slow down"}})
    assert life.view(IDENT)["daily_limit"] == 20


# -- wiring in the engine ----------------------------------------------------------------------
@pytest.fixture()
def wired(tmp_path, monkeypatch):
    life = L.Lifetime(path=lambda: tmp_path / "l.json", clock=Clock())
    monkeypatch.setattr(K, "_KLIFE", L)
    monkeypatch.setattr(L, "LIFETIME", life)
    K._KAME_REQUESTS.clear()
    return life


def answer(ctx, identity=ID, facts=None, resumes=0):
    ctx["facts"] = facts or {"attempts": 1}
    ctx["resumes"] = resumes
    K._kame_after_answer(identity, ctx, time.perf_counter())


def chat_ctx():
    ctx, _ = make_ctx(streaming({}))
    return ctx


def test_a_chat_reply_is_counted_and_a_background_job_is_not(wired):
    chat = chat_ctx()
    aux, _ = make_ctx(streaming({}), call_type="Util")
    answer(chat)
    answer(aux, facts={"attempts": 3})
    snap = wired.snapshot()
    assert (snap["replies"], snap["saved"]) == (1, 0)
    answer(chat_ctx(), facts={"attempts": 2})
    snap = wired.snapshot()
    assert (snap["replies"], snap["saved"], snap["key_switches"]) == (2, 1, 1)


def test_a_cut_answer_finished_elsewhere_is_saved(wired):
    answer(chat_ctx(), resumes=1)
    assert wired.snapshot()["saved"] == 1


def test_a_race_won_by_being_faster_is_not_saved_from_an_error(wired):
    token = K._KAME_FALLBACK_HOP.set({"origin": ID, "to": "x:y", "why": "slow"})
    try:
        answer(chat_ctx(), identity="google:gemini/other")
    finally:
        K._KAME_FALLBACK_HOP.reset(token)
    snap = wired.snapshot()
    assert (snap["replies"], snap["saved"], snap["model_switches"]) == (1, 0, 1)
    token = K._KAME_FALLBACK_HOP.set({"origin": ID, "to": "x:y", "why": "resting"})
    try:
        answer(chat_ctx(), identity="google:gemini/other")
    finally:
        K._KAME_FALLBACK_HOP.reset(token)
    assert wired.snapshot()["saved"] == 1


def test_a_request_counts_as_sent_and_a_hedge_leg_too(wired):
    ctx = chat_ctx()
    K._kame_count_request(ctx, ID)
    K._kame_count_request(ctx, ID)
    assert wired.view(ID)["sent_24h"] == 2


def test_a_refusal_that_states_a_daily_limit_is_kept(wired):
    error = Refusal("429 quota", 429, GOOGLE)
    K._kame_rest_for_failure(ID, KEYS[0], error)
    assert wired.view(ID)["daily_limit"] == 20 and wired.view(ID)["daily_scope"] == "project"


def test_the_pool_report_carries_the_numbers_and_clear_pool_keeps_them(wired):
    K._get_identity_state(ID, KEYS)
    answer(chat_ctx())
    K._kame_count_request(chat_ctx(), ID)
    K._kame_rest_for_failure(ID, KEYS[0], Refusal("429 quota", 429, GOOGLE))
    report = K.pool_report()
    pool = next(p for p in report["pools"] if p["identity"] == ID)
    assert pool["sent_24h"] == 1 and pool["daily_limit"] == 20 and pool["daily_scope"] == "project"
    assert report["lifetime"]["replies"] == 1 and report["lifetime"]["since"]
    K.reset_pool()
    K._KJ.EVENTS.clear()
    assert wired.snapshot()["replies"] == 1


def test_a_failing_store_never_costs_a_reply(monkeypatch):
    class Broken:
        def reply(self, *a, **k):
            raise RuntimeError("disk")

        sent = note_limit = reply

    monkeypatch.setattr(K, "_KLIFE", type("M", (), {"LIFETIME": Broken()}))
    answer(chat_ctx())
    K._kame_count_request(chat_ctx(), ID)


# -- 1.8.4.1 K3: the file is read again when it changes ------------------------------------
def _bump_mtime(path, step=5):
    """Make the file's signature differ even on a coarse clock."""
    stat = os.stat(path)
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + step * 1_000_000_000))


def test_an_idle_process_shows_what_another_process_wrote(tmp_path):
    path = tmp_path / "lifetime.json"
    a = L.Lifetime(path=lambda: path, clock=Clock())
    b = L.Lifetime(path=lambda: path, clock=Clock())
    assert b.snapshot()["replies"] == 0          # B read the (missing) file once
    a.reply()
    a.flush(True)
    assert b.snapshot()["replies"] == 1          # the file changed: B reads it again
    a.reply()
    a.reply()
    a.flush(True)
    _bump_mtime(path)
    assert b.snapshot()["replies"] == 3


def test_a_process_own_unflushed_counts_are_added_once(tmp_path):
    path = tmp_path / "lifetime.json"
    a = L.Lifetime(path=lambda: path, clock=Clock())
    b = L.Lifetime(path=lambda: path, clock=Clock())
    a.reply()
    a.flush(True)
    b.reply()                                     # B's own increment, not in the file
    assert b.snapshot()["replies"] == 2
    assert b.snapshot()["replies"] == 2           # asking twice counts nothing twice
    a.reply()
    a.flush(True)
    _bump_mtime(path)
    assert b.snapshot()["replies"] == 3


def test_a_file_that_vanishes_reads_as_empty(tmp_path):
    path = tmp_path / "lifetime.json"
    a = L.Lifetime(path=lambda: path, clock=Clock())
    b = L.Lifetime(path=lambda: path, clock=Clock())
    a.reply()
    a.flush(True)
    assert b.snapshot()["replies"] == 1
    path.unlink()
    assert b.snapshot()["replies"] == 0


def test_a_read_that_raises_keeps_the_last_good_numbers(tmp_path, monkeypatch):
    path = tmp_path / "lifetime.json"
    a = L.Lifetime(path=lambda: path, clock=Clock())
    b = L.Lifetime(path=lambda: path, clock=Clock())
    a.reply()
    a.flush(True)
    assert b.snapshot()["replies"] == 1
    a.reply()
    a.flush(True)
    _bump_mtime(path)
    real = b._read

    def broken():
        raise PermissionError("held by another process")

    monkeypatch.setattr(b, "_read", broken)
    assert b.snapshot()["replies"] == 1           # the last good numbers, nothing raised
    monkeypatch.setattr(b, "_read", real)
    assert b.snapshot()["replies"] == 2           # the signature was not kept: tried again


def test_flush_keeps_the_signature_so_the_next_call_does_not_read(tmp_path, monkeypatch):
    path = tmp_path / "lifetime.json"
    a = L.Lifetime(path=lambda: path, clock=Clock())
    a.reply()
    a.flush(True)
    reads = []
    real = a._read
    monkeypatch.setattr(a, "_read", lambda: (reads.append(1), real())[1])
    assert a.snapshot()["replies"] == 1
    assert reads == []

"""1.9.0.4 (R1) - first-word times remembered on disk (Agent Zero).

``kame_model_health.py`` keeps, beside the judged outcomes, the last first-word times of each (model, class);
``HedgeTracker.first_word_stats`` falls back to them when memory has fewer than ``_MIN_SAMPLES``.
"""
from __future__ import annotations

import json
import time

import pytest

import kame_hedging as hedging
import kame_model_health as mh

ID = "google:gemini-3.6-flash"
NOW = 1_800_000_000.0


@pytest.fixture(autouse=True)
def _on(monkeypatch):
    monkeypatch.delenv("KAME_NO_REMEMBERED_TROUBLE", raising=False)


def make(tmp_path):
    return mh.ModelHealth(directory=tmp_path, calls_path=tmp_path / "absent-calls.jsonl")


def feed(h, values, reasoning=False, start=NOW):
    for i, v in enumerate(values):
        h.note_first_word(ID, reasoning, v, now=start + i)


def test_median_and_p95(tmp_path):
    h = make(tmp_path)
    feed(h, [2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0, 11.0])
    median, p95 = h.first_word_stats(ID, False, now=NOW + 20)
    assert median == 7.0 and p95 == 11.0


def test_under_five_samples_says_nothing(tmp_path):
    h = make(tmp_path)
    feed(h, [2.0, 3.0, 4.0, 5.0])
    assert h.first_word_stats(ID, False, now=NOW + 20) is None
    h.note_first_word(ID, False, 6.0, now=NOW + 10)
    assert h.first_word_stats(ID, False, now=NOW + 20) is not None


def test_the_classes_are_kept_apart(tmp_path):
    h = make(tmp_path)
    feed(h, [2.0] * 6, reasoning=False)
    assert h.first_word_stats(ID, True, now=NOW + 20) is None
    assert h.first_word_stats(ID, False, now=NOW + 20) == (2.0, 2.0)


def test_nothing_that_is_not_a_positive_finite_number(tmp_path):
    h = make(tmp_path)
    for bad in (0.0, -1.0, float("inf"), float("nan"), "x", None):
        h.note_first_word(ID, False, bad, now=NOW)
    assert h.first_word_stats(ID, False, now=NOW) is None
    assert h._words == {}


def test_at_most_twenty_newest_kept(tmp_path):
    h = make(tmp_path)
    feed(h, [100.0] * 10 + [1.0] * 20)
    rows = h._words[f"{ID}|0"]
    assert len(rows) == 20 and all(sec == 1.0 for _at, sec in rows)
    assert h.first_word_stats(ID, False, now=NOW + 60) == (1.0, 1.0)


def test_samples_age_out_after_seven_days(tmp_path):
    h = make(tmp_path)
    feed(h, [2.0] * 6)
    assert h.first_word_stats(ID, False, now=NOW + 7 * 86400.0 - 100) is not None
    assert h.first_word_stats(ID, False, now=NOW + 7 * 86400.0 + 100) is None


def test_written_to_the_same_document_and_read_back(tmp_path):
    h = make(tmp_path)
    h.note(ID, True, now=NOW)
    feed(h, [2.0, 3.0, 4.0, 5.0, 6.0], start=time.time() - 50)
    h.flush()
    doc = json.loads((tmp_path / mh.FILE_NAME).read_text(encoding="utf-8"))
    assert doc["schema"] == 1 and "models" in doc
    assert len(doc["first_words"][f"{ID}|0"]) == 5
    again = make(tmp_path)
    assert again.first_word_stats(ID, False) is not None


def test_an_old_document_without_first_words_still_reads(tmp_path):
    (tmp_path / mh.FILE_NAME).write_text(
        json.dumps({"schema": 1, "models": {ID: [[time.time() - 5, 0], [time.time() - 3, 0]]}}), encoding="utf-8")
    h = make(tmp_path)
    assert h.risky(ID) is True
    assert h.first_word_stats(ID, False) is None


def test_two_processes_files_merge_without_doubling(tmp_path):
    base = time.time() - 100
    a = make(tmp_path)
    feed(a, [2.0, 3.0, 4.0], start=base)
    a.flush()
    b = make(tmp_path)           # a second process starts from a's file, then adds its own
    b.ensure_loaded()
    feed(b, [5.0, 6.0], start=base + 50)
    b.flush()
    a.note_first_word(ID, False, 7.0, now=base + 80)      # a never saw b's samples
    a.flush()
    c = make(tmp_path)
    c.ensure_loaded()
    assert sorted(sec for _at, sec in c._words[f"{ID}|0"]) == [2.0, 3.0, 4.0, 5.0, 6.0, 7.0]
    assert c.first_word_stats(ID, False) is not None


def test_forget_drops_the_samples_and_a_tombstone_keeps_them_from_returning(tmp_path):
    base = time.time() - 100
    a = make(tmp_path)
    feed(a, [2.0] * 6, start=base)
    a.flush()
    stale = make(tmp_path)           # another process still holding the old samples
    stale.ensure_loaded()
    assert a.forget(lambda ident: ident == ID) == 1
    assert a.first_word_stats(ID, False) is None
    stale._dirty = True
    stale.flush()
    b = make(tmp_path)
    assert b.first_word_stats(ID, False) is None, "the tombstone beats the stale process"
    b.note_first_word(ID, False, 4.0)           # a live note after the Clear counts
    assert len(b._words[f"{ID}|0"]) == 1


def test_the_off_switch(tmp_path, monkeypatch):
    monkeypatch.setenv("KAME_NO_REMEMBERED_TROUBLE", "1")
    h = make(tmp_path)
    feed(h, [2.0] * 8)
    assert h.first_word_stats(ID, False, now=NOW + 20) is None
    assert h._words == {}


def test_tracker_falls_back_to_the_disk(tmp_path, monkeypatch):
    disk = make(tmp_path)
    feed(disk, [4.0] * 6, start=time.time() - 50)
    monkeypatch.setattr(mh, "HEALTH", disk)
    tracker = hedging.HedgeTracker()
    assert tracker.first_word_stats(ID, reasoning=False) == (4.0, 4.0)
    assert tracker.first_word_stats(ID, reasoning=True) is None


def test_tracker_memory_with_enough_samples_wins(tmp_path, monkeypatch):
    disk = make(tmp_path)
    feed(disk, [4.0] * 6, start=time.time() - 50)
    monkeypatch.setattr(mh, "HEALTH", disk)
    tracker = hedging.HedgeTracker()
    for _ in range(hedging._MIN_SAMPLES):
        tracker.record_latency(ID, 9.0, stream=True, reasoning=False)
    assert tracker.first_word_stats(ID, reasoning=False) == (9.0, 9.0)


# --- the engine's call sites -------------------------------------------------------------------------------------

def test_a_winner_leaves_a_sample_but_a_background_job_a_rescue_and_a_non_stream_call_do_not(tmp_path, monkeypatch):
    import test_v1_8_2_7_speed_fallback  # noqa: F401  (stubs Agent Zero before the engine loads)
    import kame_engine as K

    disk = make(tmp_path)
    monkeypatch.setattr(mh, "HEALTH", disk)
    K._kame_remember_first_word({"call_type": "Chat"}, ID, False, 3.5)
    K._kame_remember_first_word({"call_type": "Chat", "aux_task": object()}, ID, False, 9.5)   # a background job
    K._kame_remember_first_word({"call_type": "Utility"}, ID, False, 8.5)                      # not a chat stream
    assert [sec for _at, sec in disk._words[f"{ID}|0"]] == [3.5]


# --- review: a file written before 1.9.0.4 has no first words; they are learned once from calls.jsonl ------------

def _calls(tmp_path, rows):
    path = tmp_path / "calls.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return path


def test_first_words_are_seeded_from_the_call_log_when_the_file_has_none(tmp_path):
    now = time.time()
    rows = [{"at": now - 100 + i, "identity": ID, "outcome": "ok", "ms_to_first_sign": ms}
            for i, ms in enumerate([2700, 5400, 4000, 11200, 3000, 6000])]
    rows.append({"at": now - 50, "identity": ID, "outcome": "ok", "ms_to_first_sign": 90000, "rescued": True})
    rows.append({"at": now - 45, "identity": ID, "outcome": "ok", "ms_to_first_sign": 70000, "channel": "secondary"})
    rows.append({"at": now - 40, "identity": ID, "outcome": "refused", "kind": "per_minute", "ms_total": 800})
    rows.append({"at": now - 8 * 86400, "identity": ID, "outcome": "ok", "ms_to_first_sign": 99000})
    calls = _calls(tmp_path, rows)
    (tmp_path / mh.FILE_NAME).write_text(json.dumps({"schema": mh.SCHEMA, "models": {}}), encoding="utf-8")
    h = mh.ModelHealth(directory=tmp_path, calls_path=calls)
    reasoning = hedging.reasoning_model("gemini-3.6-flash")
    median, p95 = h.first_word_stats(ID, reasoning)
    assert median == 5.4 and p95 == 11.2          # the rescued, background, refused and week-old rows are not samples
    h.flush()
    on_disk = json.loads((tmp_path / mh.FILE_NAME).read_text(encoding="utf-8"))
    assert f"{ID}|{1 if reasoning else 0}" in on_disk.get("first_words", {})


def test_first_words_on_disk_are_not_seeded_again(tmp_path):
    now = time.time()
    calls = _calls(tmp_path, [{"at": now - 10 + i, "identity": ID, "outcome": "ok", "ms_to_first_sign": 1000}
                              for i in range(6)])
    reasoning = hedging.reasoning_model("gemini-3.6-flash")
    wkey = f"{ID}|{1 if reasoning else 0}"
    words = [[round(now - 5 + i * 0.1, 3), v] for i, v in enumerate([20.0, 21.0, 22.0, 23.0, 24.0])]
    (tmp_path / mh.FILE_NAME).write_text(json.dumps({"schema": mh.SCHEMA, "models": {}, "first_words": {wkey: words}}),
                                         encoding="utf-8")
    h = mh.ModelHealth(directory=tmp_path, calls_path=calls)
    median, _ = h.first_word_stats(ID, reasoning)
    assert median == 22.0          # the call log's 1 s rows were not added on top

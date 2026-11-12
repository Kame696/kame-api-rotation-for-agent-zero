"""1.8.4.2 (S1a, S1f) - remembered trouble: the record of which models keep failing (Agent Zero).

``kame_model_health.py`` keeps the last judged outcomes of each model across processes and restarts.
A model is risky when its latest judged outcome is a failure and at least two of its last ten outcomes
within seven days are failures.
"""
from __future__ import annotations

import json
import threading
import time

import pytest

import kame_model_health as mh
ID = "google:gemini-3.8-flash"
NOW = 1_800_000_000.0


@pytest.fixture(autouse=True)
def _on(monkeypatch):
    monkeypatch.delenv("KAME_NO_REMEMBERED_TROUBLE", raising=False)


def _wait(predicate, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


def make(tmp_path, **kw):
    return mh.ModelHealth(directory=tmp_path, calls_path=kw.get("calls_path", tmp_path / "absent-calls.jsonl"))


def test_empty_is_not_risky(tmp_path):
    assert make(tmp_path).risky(ID, now=NOW) is False


def test_one_failure_is_not_risky(tmp_path):
    h = make(tmp_path)
    h.note(ID, False, now=NOW)
    assert h.risky(ID, now=NOW + 1) is False


def test_two_failures_latest_failure_is_risky(tmp_path):
    h = make(tmp_path)
    h.note(ID, False, now=NOW)
    h.note(ID, False, now=NOW + 5)
    assert h.risky(ID, now=NOW + 6) is True
    assert h.summary(ID, now=NOW + 6) == (2, 2)


def test_two_failures_then_a_success_is_not_risky(tmp_path):
    h = make(tmp_path)
    h.note(ID, False, now=NOW)
    h.note(ID, False, now=NOW + 5)
    h.note(ID, True, now=NOW + 9)
    assert h.risky(ID, now=NOW + 10) is False


def test_failures_older_than_seven_days_are_ignored(tmp_path):
    h = make(tmp_path)
    h.note(ID, False, now=NOW)
    h.note(ID, False, now=NOW + 5)
    assert h.risky(ID, now=NOW + 6) is True
    assert h.risky(ID, now=NOW + mh.MAX_AGE_S + 10) is False
    # one old failure and one fresh failure: only one counts
    h.note(ID, False, now=NOW + mh.MAX_AGE_S + 20)
    assert h.risky(ID, now=NOW + mh.MAX_AGE_S + 21) is False


def test_only_the_last_ten_count(tmp_path):
    h = make(tmp_path)
    for i in range(2):
        h.note(ID, False, now=NOW + i)
    for i in range(9):
        h.note(ID, True, now=NOW + 10 + i)
    h.note(ID, False, now=NOW + 30)
    # the window is the last ten: eight successes... the two old failures have slid out; one failure left
    assert h.summary(ID, now=NOW + 31) == (1, 10)
    assert h.risky(ID, now=NOW + 31) is False


def test_a_call_that_saw_a_503_then_answered_is_one_success(tmp_path):
    h = make(tmp_path)
    h.note(ID, False, call_id="c1", now=NOW)
    h.note(ID, True, call_id="c1", now=NOW + 4)
    assert h.summary(ID, now=NOW + 5) == (0, 1)
    h.flush()
    assert make(tmp_path).summary(ID, now=NOW + 5) == (0, 1), "the replaced failure does not come back from the file"


def test_answered_then_a_late_failure_is_still_a_success(tmp_path):
    h = make(tmp_path)
    h.note(ID, True, call_id="c1", now=NOW)
    h.note(ID, False, call_id="c1", now=NOW + 2)
    assert h.summary(ID, now=NOW + 3) == (0, 1)


def test_another_call_is_another_event(tmp_path):
    h = make(tmp_path)
    h.note(ID, False, call_id="c1", now=NOW)
    h.note(ID, False, call_id="c2", now=NOW + 3)
    assert h.risky(ID, now=NOW + 4) is True


def test_persistence_round_trip(tmp_path):
    h = make(tmp_path)
    h.note(ID, False, now=NOW)
    h.note(ID, False, now=NOW + 5)
    h.flush()
    document = json.loads((tmp_path / mh.FILE_NAME).read_text(encoding="utf-8"))
    assert document["schema"] == 1 and ID in document["models"]
    again = make(tmp_path)
    assert again.risky(ID, now=NOW + 6) is True


def test_a_second_writers_events_are_merged(tmp_path):
    a, b = make(tmp_path), make(tmp_path)
    a.note(ID, False, now=NOW)
    a.flush()
    b.note("google:gemini-3.7-flash", False, now=NOW + 1)
    b.note(ID, False, now=NOW + 2)
    b.flush()
    c = make(tmp_path)
    assert c.risky(ID, now=NOW + 3) is True, "the first writer's failure and the second's are both there"
    assert c.summary("google:gemini-3.7-flash", now=NOW + 3) == (1, 1)
    # the first process notices the file changed and re-reads it
    a.ensure_loaded()
    assert a.summary(ID, now=NOW + 3) == (2, 2)


def test_a_corrupt_file_is_empty_and_never_raises(tmp_path):
    (tmp_path / mh.FILE_NAME).write_text("{not json", encoding="utf-8")
    h = make(tmp_path)
    assert h.risky(ID, now=NOW) is False
    h.note(ID, False, now=NOW)
    h.note(ID, False, now=NOW + 1)
    assert h.risky(ID, now=NOW + 2) is True
    (tmp_path / mh.FILE_NAME).write_text(json.dumps({"schema": 99, "models": {ID: [[NOW, 0]]}}), encoding="utf-8")
    assert make(tmp_path).risky(ID, now=NOW + 2) is False, "an unknown schema is treated as empty"


def test_a_read_only_directory_is_memory_only(tmp_path):
    target = tmp_path / "blocked"
    target.write_text("a file where the directory should be", encoding="utf-8")    # mkdir / mkstemp cannot work
    h = mh.ModelHealth(directory=target / "sub", calls_path=tmp_path / "none.jsonl")
    h.note(ID, False, now=NOW)
    h.note(ID, False, now=NOW + 1)
    h.flush()
    assert h.risky(ID, now=NOW + 2) is True, "kept in memory"


def test_the_kill_switch(tmp_path, monkeypatch):
    h = make(tmp_path)
    monkeypatch.setenv("KAME_NO_REMEMBERED_TROUBLE", "1")
    h.note(ID, False, now=NOW)
    h.note(ID, False, now=NOW + 1)
    h.flush()
    assert h.risky(ID, now=NOW + 2) is False
    assert not (tmp_path / mh.FILE_NAME).exists(), "nothing written"
    monkeypatch.delenv("KAME_NO_REMEMBERED_TROUBLE")
    assert h.risky(ID, now=NOW + 2) is False, "nothing was kept while it was off"


def test_forget_drops_the_model_here_and_in_the_file(tmp_path):
    h = make(tmp_path)
    h.note(ID, False, now=time.time() - 50)
    h.note(ID, False, now=time.time() - 40)
    h.note("google:other", False, now=time.time() - 30)
    h.flush()
    other = make(tmp_path)
    assert other.risky(ID) is True
    assert h.forget(lambda ident: "3.8" in ident) == 1
    assert h.risky(ID) is False
    other.ensure_loaded()
    assert other.risky(ID) is False, "another process that holds the old events sees the tombstone"
    assert other.summary("google:other") == (1, 1)
    h.note(ID, False)
    assert h.summary(ID) == (1, 1), "an outcome after the forget counts again"


def test_the_save_runs_off_the_callers_thread_at_most_once_per_two_seconds(tmp_path):
    h = make(tmp_path)
    h.note(ID, False, now=NOW)
    assert h.writes == 0, "the caller (Agent Zero's event loop) never waits for the disk"
    assert _wait(lambda: h.writes == 1, 2.0), "a timer writes it a moment later"
    h.note(ID, False, now=NOW + 1)
    h.note(ID, True, now=NOW + 2)
    assert h.writes == 1, "two more notes inside two seconds are not written yet"
    h.flush()
    assert h.writes == 2
    assert make(tmp_path).summary(ID, now=NOW + 3) == (2, 3)


def test_risky_entry_reads_every_identity_of_the_entry(tmp_path):
    h = make(tmp_path)
    h.note("google:gemini-3.8-flash", False, now=NOW)
    h.note("google:gemini-3.8-flash", False, now=NOW + 1)
    def same(a, b):          # the engine's rule: same provider, same last name of the model
        a, b = a.lower(), b.lower()
        if a == b:
            return True
        (pa, ma), (pb, mb) = a.split(":", 1), b.split(":", 1)
        return pa == pb and ma.split("/")[-1] == mb.split("/")[-1]
    assert h.risky_entry("google:gemini/gemini-3.8-flash", same) is True
    assert h.risky_entry("google:gemini/gemini-3.7-flash", same) is False


# --- S1f: seeding ----------------------------------------------------------------------------------

def _row(identity, outcome, kind="", at=None, call=""):  # each row a distinct instant
    _row.n += 1
    return json.dumps({"at": at + _row.n if at is not None else time.time() - 3600 + _row.n, "identity": identity,
                       "outcome": outcome, "kind": kind, "call": call})


_row.n = 0


def test_seeded_from_calls_jsonl_on_first_run(tmp_path):
    calls = tmp_path / "calls.jsonl"
    old = time.time() - 9 * 86400
    calls.write_text("\n".join([
        _row("google:a", "refused", "server"),
        _row("google:a", "refused", "server"),
        _row("google:a", "refused", "timeout"),
        _row("google:b", "ok"),
        _row("google:c", "refused", "server", at=old),       # too old
        _row("google:c", "refused", "server", at=old),
        _row("google:d", "refused", "per_minute"),             # a 429: neutral
        _row("google:d", "refused", "per_minute"),
        "not json at all",
    ]) + "\n", encoding="utf-8")
    h = mh.ModelHealth(directory=tmp_path, calls_path=calls)
    assert h.risky("google:a") is True
    assert h.risky("google:b") is False and h.summary("google:b") == (0, 1)
    assert h.risky("google:c") is False and h.summary("google:c") == (0, 0)
    assert h.summary("google:d") == (0, 0)
    assert _wait(lambda: (tmp_path / mh.FILE_NAME).exists(), 2.0), "the seeded record is written"


def test_no_seeding_when_the_file_exists(tmp_path):
    calls = tmp_path / "calls.jsonl"
    calls.write_text(_row("google:a", "refused", "server") + "\n" + _row("google:a", "refused", "server")
                     + "\n", encoding="utf-8")
    (tmp_path / mh.FILE_NAME).write_text(json.dumps({"schema": 1, "models": {}}), encoding="utf-8")
    h = mh.ModelHealth(directory=tmp_path, calls_path=calls)
    assert h.risky("google:a") is False


def test_seeding_keeps_one_event_per_identity_and_call(tmp_path):
    calls = tmp_path / "calls.jsonl"
    calls.write_text("\n".join([
        _row("google:a", "refused", "server", call="c1"),
        _row("google:a", "refused", "server", call="c1"),      # the same reply, three keys refused: one failure
        _row("google:a", "refused", "server", call="c1"),
        _row("google:a", "ok", call="c2"),
        _row("google:a", "refused", "server", call="c2"),      # a refusal and then an answer: one success
    ]) + "\n", encoding="utf-8")
    h = mh.ModelHealth(directory=tmp_path, calls_path=calls)
    assert h.summary("google:a") == (1, 2)


def test_the_directory_is_the_journals_data_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("KAME_DATA_DIR", str(tmp_path))
    h = mh.ModelHealth()
    h.note(ID, False, now=NOW)
    h.flush()
    assert (tmp_path / mh.FILE_NAME).exists()


# --- review (1.8.4.2): the disk is never on the caller's thread, never under the memory lock -------------

def test_the_disk_is_never_touched_on_the_thread_that_notes(tmp_path, monkeypatch):
    h = make(tmp_path)
    writers = []
    original = h._write

    def write(path):
        writers.append(threading.current_thread())
        return original(path)

    monkeypatch.setattr(h, "_write", write)
    for n in range(3):
        h.note("google:y", False, now=NOW + n)
    h.risky("google:y")
    assert writers == [], "no write on the caller's thread"
    assert _wait(lambda: len(writers) == 1, 3.0)
    assert writers[0] is not threading.current_thread()


def test_reading_the_record_never_waits_for_a_slow_disk(tmp_path, monkeypatch):
    h = make(tmp_path)
    in_disk = threading.Event()
    original = mh.ModelHealth._put

    def slow_put(path, document):
        in_disk.set()
        time.sleep(0.6)
        return original(path, document)

    monkeypatch.setattr(mh.ModelHealth, "_put", staticmethod(slow_put))
    h.note("google:slow", False, now=NOW)
    assert in_disk.wait(2.0), "the timer is writing"
    began = time.perf_counter()
    h.risky("google:slow")
    h.note("google:slow", False, now=NOW + 1)
    assert time.perf_counter() - began < 0.2, "a read and a note while the disk is busy do not wait for it"
    assert _wait(lambda: h.writes >= 1, 3.0)


def test_a_throttled_note_is_written_by_a_timer(tmp_path, monkeypatch):
    monkeypatch.setattr(mh, "SAVE_EVERY_S", 0.3)
    h = make(tmp_path)
    h.note(ID, False, now=NOW)
    assert _wait(lambda: h.writes == 1, 2.0)
    h.note(ID, False, now=NOW + 1)
    assert h.writes == 1
    assert _wait(lambda: h.writes == 2, 3.0), "written by the timer, with no further note"
    assert make(tmp_path).summary(ID, now=NOW + 2) == (2, 2)


def test_a_timer_does_not_carry_one_directorys_outcomes_into_another(tmp_path, monkeypatch):
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    where = {"dir": first}
    h = mh.ModelHealth(calls_path=tmp_path / "none.jsonl")
    monkeypatch.setattr(h, "_directory", lambda: where["dir"])
    h.note("google:z", False, now=NOW)
    h.note("google:z", False, now=NOW + 1)
    where["dir"] = second
    h.risky("google:z")
    time.sleep(0.4)
    assert not (second / mh.FILE_NAME).exists(), "the old directory's outcomes did not land in the new one"

"""1.8.2.9 (E11, decisions/0011) - the instrument logs rotate instead of going silent.

Until 1.8.2.8 `kame_journal._append` returned False once `calls.jsonl` reached 4 MB
or `refusals.jsonl` 8 MB and said nothing: from that day on nothing was timed and
nobody knew. Now the current file plus ONE previous generation are kept
(`calls.1.jsonl`), 2 MB and 4 MB each, so the disk total is what it was. The size is
checked on the first write and every 64 writes; each rotation and each failure
prints one console line. Framework-free: no Agent Zero, no network.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import kame_journal as J  # noqa: E402
import kame_settings as S  # noqa: E402

MB = 1024 * 1024


@pytest.fixture(autouse=True)
def folder(tmp_path, monkeypatch):
    monkeypatch.setenv("KAME_DATA_DIR", str(tmp_path))
    J._reset_for_tests()
    yield tmp_path
    J._reset_for_tests()


def _names(folder):
    return sorted(p.name for p in folder.iterdir())


def _row(i, pad=0):
    return {"n": i, "pad": "x" * pad}


def test_the_ceilings_are_half_the_old_totals():
    assert J.CALLS_CEILING == 2 * MB
    assert J.REFUSALS_CEILING == 4 * MB


def test_writing_past_the_ceiling_rotates_and_new_rows_keep_landing(folder):
    # 70 rows of ~40 KB: the size check at the 65th write finds the file past 2 MB.
    for i in range(70):
        assert J._append(J.CALLS_FILE, J.CALLS_CEILING, _row(i, 40000)) is True
    assert _names(folder) == ["calls.1.jsonl", "calls.jsonl"]
    assert (folder / "calls.1.jsonl").stat().st_size >= J.CALLS_CEILING
    current = [json.loads(l) for l in (folder / "calls.jsonl").read_text(encoding="utf-8").splitlines()]
    assert current and current[-1]["n"] == 69          # rows written after the rotation are there


def test_the_first_write_after_start_checks_the_size(folder):
    big = folder / J.CALLS_FILE
    big.write_text(("y" * 999 + "\n") * 3000, encoding="utf-8")        # ~3 MB left by a previous run
    assert J._append(J.CALLS_FILE, J.CALLS_CEILING, _row(1)) is True
    assert (folder / "calls.1.jsonl").stat().st_size > 2 * MB
    assert json.loads((folder / "calls.jsonl").read_text(encoding="utf-8")) == _row(1)


def test_no_third_generation_and_the_older_rows_are_the_ones_dropped(folder):
    for i in range(400):
        J._append(J.CALLS_FILE, 1000, _row(i, 50))
    assert _names(folder) == ["calls.1.jsonl", "calls.jsonl"]
    old = [json.loads(l)["n"] for l in (folder / "calls.1.jsonl").read_text(encoding="utf-8").splitlines()]
    new = [json.loads(l)["n"] for l in (folder / "calls.jsonl").read_text(encoding="utf-8").splitlines()]
    assert old and new and old[-1] < new[0]
    assert new[-1] == 399 and len(old) + len(new) < 400


def test_refusals_rotate_the_same_way(folder):
    for i in range(70):
        assert J._append(J.REFUSALS_FILE, 1000000, _row(i, 30000)) is True
    assert _names(folder) == ["refusals.1.jsonl", "refusals.jsonl"]


def test_each_rotation_says_so_once_on_the_console(folder, capsys):
    for i in range(130):
        J._append(J.CALLS_FILE, 1000, _row(i, 40))
    lines = [l for l in capsys.readouterr().out.splitlines() if l.strip()]
    assert lines, "a rotation must be visible on the console"
    assert all(re.fullmatch(r"\[KAME\] calls\.jsonl rotated \(\d+\.\d MB\)", l) for l in lines), lines


def test_first_word_history_reads_the_previous_generation_then_the_current(folder):
    ident = "google:gemini/gemini-3.8-flash"

    def line(seconds):
        return json.dumps({"at": __import__("time").time(), "identity": ident, "outcome": "ok",
                           "ms_to_first_sign": int(seconds * 1000)}) + "\n"

    (folder / "calls.1.jsonl").write_text(line(1.0) + line(2.0), encoding="utf-8")
    (folder / "calls.jsonl").write_text(line(3.0), encoding="utf-8")
    assert J.first_word_history() == {ident: [1.0, 2.0, 3.0]}


def test_first_word_history_works_with_only_the_previous_generation(folder):
    ident = "p:m"
    (folder / "calls.1.jsonl").write_text(json.dumps(
        {"at": __import__("time").time(), "identity": ident, "outcome": "ok", "ms_to_first_sign": 1500}) + "\n",
        encoding="utf-8")
    assert J.first_word_history() == {ident: [1.5]}


def test_a_rotation_that_fails_prints_one_line_and_never_raises(folder, monkeypatch, capsys):
    (folder / J.CALLS_FILE).write_text(("y" * 999 + "\n") * 3000, encoding="utf-8")

    def refuse(*_a, **_k):
        raise OSError("file is in use")

    monkeypatch.setattr(J.os, "replace", refuse)
    for i in range(5):
        J.record_call(identity="p:m", key="k", outcome="ok", started_at=1.0, ended_at=2.0)
    lines = [l for l in capsys.readouterr().out.splitlines() if l.strip()]
    assert len(lines) == 1 and lines[0].startswith("[KAME] calls.jsonl") and "rotate" in lines[0]
    assert "in use" in lines[0]


def test_a_failing_write_says_so_once_and_recovers(folder, monkeypatch, capsys):
    real_open = open
    state = {"fail": True}

    def flaky(path, *a, **k):
        if state["fail"] and str(path).endswith(J.CALLS_FILE):
            raise OSError("disk full")
        return real_open(path, *a, **k)

    monkeypatch.setattr("builtins.open", flaky)
    for _ in range(3):
        J.record_call(identity="p:m", key="k", outcome="ok", started_at=1.0, ended_at=2.0)
    first = [l for l in capsys.readouterr().out.splitlines() if l.strip()]
    assert len(first) == 1 and "disk full" in first[0]
    state["fail"] = False
    J.record_call(identity="p:m", key="k", outcome="ok", started_at=1.0, ended_at=2.0)
    assert (folder / J.CALLS_FILE).stat().st_size > 0          # not silenced for the rest of the process


def test_the_setting_sentences_say_what_is_kept():
    recorder = S.SETTINGS["refusal_recorder_disabled"][7]
    timings = S.SETTINGS["call_timings_disabled"][7]
    assert "last ~8 MB" in recorder and "at most" not in recorder
    assert "last ~4 MB" in timings and "at most" not in timings

"""1.8.2.9 (E8, decisions/0009) - how much of a model's quota goes to background jobs.

A background job (chat naming, memory recall, compression) spends the same per-model daily
quota as the chat. Nothing told the owner. Now every request is counted per model and per
hour as `main` (the chat) or `side` (a background job, with its task), the journal rows say
which channel they were, `pool_report()` carries the 24 h tally, and when background jobs
are a real share of a model's requests KAME writes ONE read-only sentence: which job to move
and to which model of the owner's own list. It changes nothing by itself.

No Agent Zero installation, no network.
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from test_v1_8_2_7_speed_fallback import (  # noqa: F401  (fixtures are used by name)
    ID, KEYS, clean, make_ctx, run, streaming,
)
import kame_engine as K
import kame_journal as J

ROOT = Path(__file__).resolve().parents[1]
NOW = 1_900_000_000.0


@pytest.fixture(autouse=True)
def tally_clean(monkeypatch, tmp_path):
    monkeypatch.setenv("KAME_DATA_DIR", str(tmp_path))
    for name in ("_KAME_REQUESTS", "_KAME_REQUEST_TASKS", "_KAME_AUX_STOPPED"):
        table = getattr(K, name, None)
        if table is not None:
            table.clear()
    yield
    for name in ("_KAME_REQUESTS", "_KAME_REQUEST_TASKS", "_KAME_AUX_STOPPED"):
        table = getattr(K, name, None)
        if table is not None:
            table.clear()


def count(identity, *, main=0, side=0, task="title_generation", now=NOW):
    for _ in range(main):
        K._kame_count_request({"call_type": "Chat"}, identity, now)
    for _ in range(side):
        K._kame_count_request({"call_type": "Util", "aux_task": task}, identity, now)


def view(now=NOW):
    return K.quota_use_view(now)


# --- the journal says which channel a row was ------------------------------------------------

def _rows(tmp_path):
    return [json.loads(line) for line in (tmp_path / "calls.jsonl").read_text(encoding="utf-8").splitlines()]


def test_a_journal_row_says_main_or_secondary_and_which_task(tmp_path):
    J.record_call(identity=ID, key="k", attempt=1, outcome="ok", started_at=NOW, ended_at=NOW + 1,
                  channel="secondary", task="memory_recall")
    J.record_call(identity=ID, key="k", attempt=1, outcome="ok", started_at=NOW, ended_at=NOW + 1)
    side, chat = _rows(tmp_path)
    assert side["channel"] == "secondary" and side["task"] == "memory_recall"
    assert chat["channel"] == "main" and chat["task"] == "chat"


def test_the_engine_fills_channel_and_task_from_the_call(tmp_path):
    chat, _ = make_ctx(streaming({}))
    util, _ = make_ctx(streaming({}), call_type="Util")
    util["aux_task"] = "compression"
    for ctx in (chat, util):
        K._kame_timing(ID, KEYS[0], 1, "ok", "", None, NOW, ctx, time.perf_counter(), None, None, "", "c1")
    first, second = _rows(tmp_path)
    assert (first["channel"], first["task"]) == ("main", "chat")
    assert (second["channel"], second["task"]) == ("secondary", "compression")


def test_the_first_word_history_ignores_background_rows(tmp_path):
    for channel, ms in (("main", 1200), ("secondary", 9000)):
        J.record_call(identity=ID, key="k", attempt=1, outcome="ok", started_at=NOW, ended_at=NOW + ms / 1000,
                      first_sign_at=NOW + ms / 1000, channel=channel, task="chat" if channel == "main" else "x")
    seen = J.first_word_history(time.time())
    assert seen[ID] == [pytest.approx(1.2, abs=0.01)]


# --- the 24 hour tally ---------------------------------------------------------------------------

def test_every_request_is_counted_per_model_as_main_or_side_with_its_task():
    count(ID, main=7, side=2, task="title_generation")
    count(ID, side=3, task="memory_recall")
    row = view()["models"][ID]
    assert row["main"] == 7 and row["side"] == 5
    assert row["tasks"] == {"title_generation": 2, "memory_recall": 3}


def test_a_lone_chat_request_and_a_lone_background_request_each_count_once():
    async def generate_name(ctx):
        return await K._kame_carousel(None, ctx)

    chat, _ = make_ctx(streaming({}))
    run(chat)
    util, _ = make_ctx(streaming({}), call_type="Util")
    asyncio.run(generate_name(util))
    row = K.quota_use_view()["models"][ID]
    assert row["main"] == 1 and row["side"] == 1 and row["tasks"] == {"title_generation": 1}


def test_the_tally_keeps_twenty_four_hours_and_no_more():
    count(ID, side=4, now=NOW - 25 * 3600)
    count(ID, main=2, now=NOW - 23 * 3600)
    count(ID, main=1, now=NOW)
    out = view(NOW)
    assert out["models"][ID] == {"main": 3, "side": 0, "tasks": {}}
    assert out["since"] >= NOW - 24 * 3600


def test_since_is_the_start_of_the_plugin_when_that_is_less_than_a_day_ago(monkeypatch):
    monkeypatch.setattr(K, "_KAME_STARTED_AT", NOW - 3600, raising=False)
    assert view(NOW)["since"] == NOW - 3600


def test_stopped_background_jobs_are_reported():
    K._KAME_AUX_STOPPED["title_generation"] += 2
    assert view()["stopped"] == {"title_generation": 2}


def test_pool_report_carries_the_tally_and_still_has_every_old_field():
    count(ID, main=1, side=1)
    report = K.pool_report()
    assert "quota_use" in report and ID in report["quota_use"]["models"]
    for old in ("version", "build", "pools", "totals"):
        assert old in report


# --- the sentence ----------------------------------------------------------------------------------

def _list(text):
    K.set_fallback_models(text)


def test_below_twenty_background_requests_nothing_is_suggested():
    _list("google/gemini-3.7-flash")
    count(ID, main=20, side=19)
    assert view()["suggestion"] is None


def test_below_a_share_of_fifteen_percent_nothing_is_suggested():
    _list("google/gemini-3.7-flash")
    count(ID, main=200, side=30)          # 13%
    assert view()["suggestion"] is None


def test_without_a_fallback_list_nothing_is_suggested():
    _list("")
    count(ID, main=20, side=30)
    assert view()["suggestion"] is None


def test_the_sentence_names_the_job_the_model_of_the_owners_list_and_changes_nothing():
    _list("google/gemini-3.7-flash\ngroq/llama-3.3-70b")
    count(ID, main=60, side=40, task="title_generation")
    text = view()["suggestion"]
    assert text and "title_generation" in text and "gemini-3.7-flash" in text
    assert "40%" in text
    assert "Utility model" in text and "KAME never changes this for you." in text
    assert "auxiliary" not in text and "config.yaml" not in text      # that is Hermes' menu, not Agent Zero's


def test_a_cosmetic_job_goes_to_the_last_entry_and_an_important_one_to_the_first():
    _list("google/gemini-3.7-flash\ngoogle/gemini-3.6-flash")
    count(ID, main=60, side=40, task="title_generation")
    assert "gemini-3.6-flash" in view()["suggestion"]
    K._KAME_REQUESTS.clear()
    K._KAME_REQUEST_TASKS.clear()
    count(ID, main=60, side=40, task="compression")
    text = view()["suggestion"]
    assert "gemini-3.7-flash" in text and "gemini-3.6-flash" not in text


def test_the_sentence_never_names_a_model_outside_the_owners_list():
    _list("groq/llama-3.3-70b")
    count(ID, main=60, side=40, task="title_generation")
    text = view()["suggestion"]
    assert text is None or "llama-3.3-70b" in text


def test_the_most_frequent_job_is_the_one_named():
    _list("google/gemini-3.7-flash")
    count(ID, main=60, side=10, task="memory_recall")
    count(ID, side=30, task="compression")
    assert "compression" in view()["suggestion"]


# --- tools/speed.py ---------------------------------------------------------------------------------

def _speed(tmp_path, *args):
    env = dict(os.environ, KAME_DATA_DIR=str(tmp_path))
    proc = subprocess.run([sys.executable, "-B", str(ROOT / "tools" / "speed.py"), *args], cwd=str(ROOT), env=env,
                          capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert proc.returncode == 0, proc.stderr[-2000:]
    return proc.stdout


def _write(path, rows):
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def _row(at, identity, **kw):
    base = {"at": at, "host": "agent-zero", "identity": identity, "key": "k1", "attempt": 1, "outcome": "ok",
            "kind": "", "status": None, "ms_to_first_sign": 800, "ms_to_first_text": 900, "ms_total": 1500}
    base.update(kw)
    return base


def test_speed_json_reads_both_log_generations_and_splits_main_from_side(tmp_path):
    now = time.time()
    _write(tmp_path / "calls.1.jsonl", [
        _row(now - 100, "google:gemini-3.8", channel="main", task="chat"),
        _row(now - 99, "google:gemini-3.8", channel="secondary", task="title_generation"),
    ])
    _write(tmp_path / "calls.jsonl", [
        _row(now - 50, "google:gemini-3.8", channel="main", task="chat"),
        _row(now - 49, "google:gemini-3.8", channel="secondary", task="memory_recall"),
        _row(now - 48, "google:gemini-3.8", channel="secondary", task="memory_recall"),
        _row(now - 47, "groq:llama", channel="main", task="chat"),
        _row(now - 46, "groq:old"),                                     # a row from before 1.8.2.9: main
    ])
    out = json.loads(_speed(tmp_path, "--json"))
    assert out["rows"] == 7
    gem = out["models"]["google:gemini-3.8"]
    assert gem["main"] == 2 and gem["side"] == 3
    assert gem["tasks"] == {"memory_recall": 2, "title_generation": 1}
    assert out["models"]["groq:llama"]["side"] == 0 and out["models"]["groq:old"]["main"] == 1


def test_speed_plain_text_names_the_models_and_the_background_share(tmp_path):
    now = time.time()
    _write(tmp_path / "calls.jsonl", [
        _row(now - 3, "google:gemini-3.8", channel="main", task="chat"),
        _row(now - 2, "google:gemini-3.8", channel="secondary", task="title_generation"),
    ])
    text = _speed(tmp_path)
    assert "google:gemini-3.8" in text and "title_generation" in text


def test_speed_since_keeps_only_the_recent_rows(tmp_path):
    now = time.time()
    _write(tmp_path / "calls.jsonl", [
        _row(now - 3 * 86400, "google:gemini-3.8", channel="main", task="chat"),
        _row(now - 1, "google:gemini-3.8", channel="main", task="chat"),
    ])
    out = json.loads(_speed(tmp_path, "--json", "--since", "00:00"))     # today only
    assert out["rows"] == 1


def test_speed_says_so_when_there_is_nothing_yet(tmp_path):
    text = _speed(tmp_path)
    assert "nothing recorded" in text.lower()

"""1.8.2.9 (M1, M2, decisions/0012) - the two instruments that decide A2 and A12.

`tools/bench_reply_path.py` times KAME's own milliseconds per healthy reply with the journal
on and off; `tools/bench_status.py` times `pool_report()` on a populated state and says how
much of it is `integrity.report()`. They are instruments, not features: this suite only
proves they run offline on a stubbed host, finish quickly and print the fields the decision
rules read. It records no number - the numbers are taken on the server and written in
PARITY_CHECKLIST_1.8.2.9.md.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _run(tool, *args):
    proc = subprocess.run([sys.executable, "-B", str(ROOT / "tools" / tool), *args], cwd=str(ROOT),
                          capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert proc.returncode == 0, proc.stderr[-2000:]
    return json.loads(proc.stdout)


def _percentiles(block):
    assert set(block) >= {"p50_ms", "p95_ms", "p99_ms"}
    assert all(isinstance(block[k], (int, float)) and block[k] >= 0 for k in ("p50_ms", "p95_ms", "p99_ms"))
    assert block["p50_ms"] <= block["p95_ms"] <= block["p99_ms"]


def test_bench_reply_path_prints_both_runs_and_the_journal_share():
    out = _run("bench_reply_path.py", "--replies", "120")
    assert out["replies"] == 120
    _percentiles(out["journal_on"])
    _percentiles(out["journal_off"])
    share = out["journal_share"]
    assert set(share) >= {"append_ms_per_reply", "flush_health_ms_per_reply", "share_pct"}
    assert out["decision"]["limit_p95_ms"] == 2.0
    assert isinstance(out["decision"]["added_p95_ms"], (int, float))
    assert isinstance(out["decision"]["move_to_writer_thread"], bool)


def test_bench_reply_path_journal_on_really_writes_and_off_really_does_not():
    out = _run("bench_reply_path.py", "--replies", "40")
    assert out["rows_written"]["journal_on"] >= 40
    assert out["rows_written"]["journal_off"] == 0


def test_bench_status_prints_the_latencies_and_the_integrity_share():
    out = _run("bench_status.py", "--calls", "30")
    assert out["calls"] == 30
    assert out["state"] == {"models": 6, "keys": 17, "events": 2000}
    _percentiles(out["pool_report"])
    assert set(out["integrity"]) >= {"p50_ms", "p95_ms", "share_pct", "files_hashed"}
    assert out["integrity"]["files_hashed"] >= 1
    assert out["decision"]["limit_p95_ms"] == 20.0
    assert isinstance(out["decision"]["cache_integrity_report"], bool)


def test_the_tools_name_what_they_measured():
    assert _run("bench_reply_path.py", "--replies", "10")["tool"] == "bench_reply_path"
    assert _run("bench_status.py", "--calls", "5")["tool"] == "bench_status"

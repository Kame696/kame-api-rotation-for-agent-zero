"""Read `calls.jsonl` and answer "is it slow, and whose requests are these".

KAME writes one line per attempt; this turns the pile into the numbers that settle an argument:

* the first sign and the first visible text of each model's answered attempts;
* how many of each model's recorded attempts were the conversation (`main`) and how many were a
  background job of Agent Zero (`side`: chat naming, memory, compression ...), and which jobs;
* what the attempts that did not become an answer were.

Reads both log generations (`calls.jsonl` and the previous `calls.1.jsonl` the log rotated to),
so a rotation never hides rows. Read-only. With no argument it reads the plugin's own data folder
(`KAME_DATA_DIR` overrides it); pass a path to read a copy taken off another machine, `--since HH:MM`
to look at one stretch of today, `--json` for machine-readable output.

    python tools/speed.py
    python tools/speed.py --since 12:40
    python tools/speed.py --json

A raced backup leg is not a journal row, so the counts are a floor, not the provider's own count.
"""

from __future__ import annotations

import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

out = sys.stdout.buffer


def say(text: str = "") -> None:
    out.write((text + "\n").encode("utf-8"))


def _files(path: Optional[Path]) -> List[Path]:
    """The files that hold the rows, OLDEST FIRST."""
    import kame_journal as journal

    if path is None:
        return journal.generations(journal.CALLS_FILE)
    previous = path.with_name(journal.previous_name(path.name))
    return [p for p in (previous, path) if p.is_file()]


def load(files: List[Path], since: Optional[float]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    now = time.time()
    for path in files:
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            at = row.get("at") or 0
            # Fixtures that leaked into a production file with future epochs are not evidence.
            if at > now or str(row.get("identity", "")).startswith("sim:"):
                continue
            if since is not None and at < since:
                continue
            rows.append(row)
    return rows


def summarize(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    models: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        name = str(row.get("identity") or "?")
        entry = models.setdefault(name, {"main": 0, "side": 0, "tasks": {}, "answered": 0,
                                         "_sign": [], "_text": []})
        if row.get("channel") == "secondary":
            entry["side"] += 1
            task = str(row.get("task") or "utility")
            entry["tasks"][task] = entry["tasks"].get(task, 0) + 1
        else:
            entry["main"] += 1
        if row.get("outcome") == "ok":
            entry["answered"] += 1
            if row.get("channel") != "secondary":
                if isinstance(row.get("ms_to_first_sign"), (int, float)):
                    entry["_sign"].append(row["ms_to_first_sign"])
                if isinstance(row.get("ms_to_first_text"), (int, float)):
                    entry["_text"].append(row["ms_to_first_text"])
    for entry in models.values():
        sign, text = entry.pop("_sign"), entry.pop("_text")
        entry["first_sign_median_ms"] = round(statistics.median(sign)) if sign else None
        entry["first_text_median_ms"] = round(statistics.median(text)) if text else None
        entry["first_text_worst_ms"] = round(max(text)) if text else None
    return {"rows": len(rows), "models": models}


def report(rows: List[Dict[str, Any]]) -> None:
    if not rows:
        say("nothing recorded yet - use Agent Zero a little and run this again.")
        return
    first, last = rows[0].get("at") or 0, rows[-1].get("at") or 0
    say("%d attempts   %s -> %s" % (
        len(rows),
        time.strftime("%d/%m %H:%M:%S", time.localtime(first)),
        time.strftime("%H:%M:%S", time.localtime(last)),
    ))
    say()
    summary = summarize(rows)["models"]

    say("SPEED - what the model itself took (the conversation's answered attempts)")
    say("%-30s %8s %9s %11s %9s" % ("model", "answered", "1st sign", "1st word", "worst"))
    for name, entry in sorted(summary.items()):
        def seconds(ms):
            return "%.1fs" % (ms / 1000.0) if ms is not None else "-"
        say("%-30s %8d %9s %11s %9s" % (name[:30], entry["answered"], seconds(entry["first_sign_median_ms"]),
                                        seconds(entry["first_text_median_ms"]), seconds(entry["first_text_worst_ms"])))
    say()

    say("WASTE - attempts that did not become an answer")
    kinds: Dict[str, int] = {}
    for row in rows:
        if row.get("outcome") == "ok":
            continue
        label = str(row.get("kind") or row.get("outcome") or "?")
        kinds[label] = kinds.get(label, 0) + 1
    if not kinds:
        say("  none")
    for label, count in sorted(kinds.items(), key=lambda kv: -kv[1]):
        say("  %-24s %5d" % (label, count))
    say()

    say("BACKGROUND WORK - recorded attempts that were a job of Agent Zero, not your chat")
    say("%-30s %8s %8s %7s  %s" % ("model", "chat", "side", "share", "main jobs"))
    for name, entry in sorted(summary.items()):
        total = entry["main"] + entry["side"]
        top = ", ".join("%s %d" % (t, n) for t, n in sorted(entry["tasks"].items(), key=lambda kv: -kv[1])[:3])
        say("%-30s %8d %8d %6.0f%%  %s" % (name[:30], entry["main"], entry["side"],
                                           100.0 * entry["side"] / total if total else 0.0, top))


def main() -> int:
    args = sys.argv[1:]
    since: Optional[float] = None
    path: Optional[Path] = None
    as_json = False
    while args:
        item = args.pop(0)
        if item == "--json":
            as_json = True
        elif item == "--since" and args:
            clock = args.pop(0)
            today = time.localtime()
            try:
                hour, minute = (int(part) for part in clock.split(":"))
            except ValueError:
                say("--since wants HH:MM")
                return 2
            since = time.mktime((today.tm_year, today.tm_mon, today.tm_mday, hour, minute, 0, 0, 0, -1))
        else:
            path = Path(item)

    files = _files(path)
    rows = load(files, since)
    if as_json:
        summary = summarize(rows)
        summary["files"] = [str(p) for p in files]
        say(json.dumps(summary, sort_keys=True))
        return 0
    say("files: %s" % (", ".join(str(p) for p in files) or "none found"))
    say()
    report(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

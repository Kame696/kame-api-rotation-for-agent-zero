"""How many milliseconds does KAME add to one healthy reply? (1.8.2.9, decisions/0012, A2)

Runs KAME's real engine against a stubbed Agent Zero and a fake model that answers at once,
``--replies`` times (default 2000), with the journal ON (the default) and OFF
(``call_timings_disabled`` and ``refusal_recorder_disabled``). For each run it prints the
p50 / p95 / p99 of KAME's OWN time per reply: the wall time of the engine's entry point minus
the time the fake model itself took. A third, profiled run says how much of that is the
journal (``kame_journal._append``) and the health-file flush (``kame_journal._flush_health``).

Decision rule (decisions/0012): if the journal and the health flush add more than 2 ms at
p95 per reply, move them to a writer thread (a queue, flushed every second and at exit);
otherwise the reply path needs no change. The tool prints the numbers and which side of the
line they fall on; it changes nothing.

    python tools/bench_reply_path.py [--replies 2000]

Prints one JSON object on stdout. Needs no Agent Zero, no network and no key.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import cProfile
import json
import sys
import time
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bench_host  # noqa: E402

PROVIDER = "google"
MODEL = "gemini/gemini-3.8-flash"
KEYS = [f"AIzaSyBENCH{c * 10}" for c in "abc"]
LIMIT_P95_MS = 2.0
WARMUP = 20


def _count_rows(folder: Path) -> int:
    total = 0
    for name in ("calls.jsonl", "calls.1.jsonl", "refusals.jsonl", "refusals.1.jsonl"):
        path = folder / name
        if path.is_file():
            with open(path, encoding="utf-8", errors="replace") as handle:
                total += sum(1 for _ in handle)
    return total


def _build(K):
    """The engine's real entry wrapper around a fake model that answers at once."""
    spent = {"model": 0.0}

    async def original(self, system_message="", user_message="", messages=None, response_callback=None,
                       reasoning_callback=None, tokens_callback=None, rate_limiter_callback=None,
                       explicit_caching=False, **kwargs):
        started = time.perf_counter()
        callbacks = 0.0
        if response_callback is not None:
            mark = time.perf_counter()
            await response_callback("ok", "ok")      # the engine's own wrapper: KAME's time, not the model's
            callbacks = time.perf_counter() - mark
        spent["model"] += (time.perf_counter() - started) - callbacks
        return ("ok", "")

    entry = K._kame_make_entry_wrapper("unified_call", original, None)
    host = types.SimpleNamespace(a0_model_conf=types.SimpleNamespace(provider=PROVIDER), model_name=MODEL)

    async def call_chat_model():
        # The frame name is how the engine tells the conversation from a background job.
        async def sink(delta, full):
            return None
        return await entry(host, messages=["hello"], response_callback=sink)

    return call_chat_model, spent


async def _replies(call, spent, count):
    own = []
    for _ in range(count):
        spent["model"] = 0.0
        started = time.perf_counter()
        await call()
        own.append(max(0.0, (time.perf_counter() - started) - spent["model"]))
    return own


async def _measure(K, replies):
    bench_host.set_keys(PROVIDER, KEYS)
    call, spent = _build(K)
    await _replies(call, spent, WARMUP)
    out = {}
    rows = {}
    for label, journal_on in (("journal_on", True), ("journal_off", False)):
        folder = bench_host.new_data_dir()
        K.set_call_timings_disabled(not journal_on)
        K.set_recorder_disabled(not journal_on)
        out[label] = bench_host.percentiles(await _replies(call, spent, replies))
        rows[label] = _count_rows(folder)
        bench_host.drop(folder)
    # profiled run: where the journal's time goes (the profile slows everything, so it gives
    # a share and a per-reply cost of the journal functions, never the percentiles above)
    folder = bench_host.new_data_dir()
    K.set_call_timings_disabled(False)
    K.set_recorder_disabled(False)
    profile = cProfile.Profile()
    started = time.perf_counter()
    profile.enable()
    await _replies(call, spent, replies)
    profile.disable()
    wall = time.perf_counter() - started
    bench_host.drop(folder)
    append_s = bench_host.profile_share(profile, "kame_journal.py", {"_append"})
    flush_s = bench_host.profile_share(profile, "kame_journal.py", {"_flush_health"})
    return out, rows, {
        "append_ms_per_reply": round(append_s / replies * 1000.0, 4),
        "flush_health_ms_per_reply": round(flush_s / replies * 1000.0, 4),
        "share_pct": round(100.0 * (append_s + flush_s) / wall, 2) if wall else 0.0,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--replies", type=int, default=2000, help="healthy replies per run (default 2000)")
    args = parser.parse_args(argv)
    replies = max(1, args.replies)
    with contextlib.redirect_stdout(sys.stderr):      # the engine may print; stdout is the JSON
        K = bench_host.load_engine()
        runs, rows, share = asyncio.run(_measure(K, replies))
    added = round(runs["journal_on"]["p95_ms"] - runs["journal_off"]["p95_ms"], 4)
    report = {
        "tool": "bench_reply_path",
        "replies": replies,
        "journal_on": runs["journal_on"],
        "journal_off": runs["journal_off"],
        "rows_written": rows,
        "journal_share": share,
        "decision": {
            "limit_p95_ms": LIMIT_P95_MS,
            "added_p95_ms": added,
            "move_to_writer_thread": added > LIMIT_P95_MS,
        },
    }
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

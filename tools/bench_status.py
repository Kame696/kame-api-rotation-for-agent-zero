"""How long does one status call take? (1.8.2.9, decisions/0012, A12 - the offline half)

The chip above the message box asks ``pool_report()`` every 1.5-15 s and an open KAME page
every 3 s. The one repeated cost found by reading the code is ``integrity.report()``, which
hashes about 40 plugin files on every call. This tool builds a populated state (6 models,
17 keys, 2,000 events), calls ``pool_report()`` ``--calls`` times (default 500) and prints
p50 / p95 / p99 in milliseconds, then the same for ``integrity.report()`` alone and the share
of a ``pool_report()`` call it takes (from a profiled run).

Decision rule (decisions/0012): if ``pool_report()`` is 20 ms or more at p95 - or the idle
process on the real server shows KAME using 1% of a core or more (the live half: native CI,
``top -H`` on the server) - cache ``integrity.report()`` by (path, mtime, size) so each file is
hashed once per change; otherwise nothing changes. This tool prints the offline number and
which side of the line it falls on; it changes nothing.

    python tools/bench_status.py [--calls 500]

Prints one JSON object on stdout. Needs no Agent Zero, no network and no key.
"""

from __future__ import annotations

import argparse
import contextlib
import cProfile
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bench_host  # noqa: E402

MODELS = 6
KEYS_PER_MODEL = (3, 3, 3, 3, 3, 2)          # 17 keys
EVENTS = 2000
LIMIT_P95_MS = 20.0


def _populate(K):
    """Six models, seventeen keys, some resting or retired, and a long event history."""
    for index, count in enumerate(KEYS_PER_MODEL):
        identity = f"provider{index}:model-{index}"
        keys = [f"BENCH-KEY-{index}-{n}-{'x' * 24}" for n in range(count)]
        K._get_identity_state(identity, keys)
        K._mark_key_health(identity, keys[0], False, 60, "per_minute")
        if count > 2 and index % 2:
            K._mark_key_health(identity, keys[1], False, 3600, "daily")
    for n in range(EVENTS):
        K._KJ.EVENTS.add("rotation", identity=f"provider{n % MODELS}:model-{n % MODELS}", key=f"k{n % 97:04x}",
                         reason="rate limited", code=429, seconds=20.0)


def _time(call, count):
    took = []
    for _ in range(count):
        started = time.perf_counter()
        call()
        took.append(time.perf_counter() - started)
    return took


def _measure(K, calls):
    import integrity

    _populate(K)
    report = K.pool_report()
    keys = sum(len(p["keys"]) for p in report["pools"])
    _time(K.pool_report, 5)                                    # warm up
    pool = _time(K.pool_report, calls)
    hashing = _time(integrity.report, calls)
    files = integrity.report()["files"]
    profile = cProfile.Profile()
    started = time.perf_counter()
    profile.enable()
    for _ in range(calls):
        K.pool_report()
    profile.disable()
    wall = time.perf_counter() - started
    hashed = bench_host.profile_share(profile, "integrity.py", {"report"})
    return report, keys, pool, hashing, sum(1 for digest in files.values() if digest), \
        (100.0 * hashed / wall if wall else 0.0)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--calls", type=int, default=500, help="status calls to time (default 500)")
    args = parser.parse_args(argv)
    calls = max(1, args.calls)
    with contextlib.redirect_stdout(sys.stderr):      # the engine may print; stdout is the JSON
        K = bench_host.load_engine()
        report, keys, pool, hashing, files_hashed, share = _measure(K, calls)
    pool_block = bench_host.percentiles(pool)
    out = {
        "tool": "bench_status",
        "calls": calls,
        "state": {"models": len(report["pools"]), "keys": keys, "events": EVENTS},
        "events_kept": len(K._KJ.EVENTS.recent(10 ** 6)),
        "pool_report": pool_block,
        "integrity": dict(bench_host.percentiles(hashing), share_pct=round(share, 2), files_hashed=files_hashed),
        "decision": {
            "limit_p95_ms": LIMIT_P95_MS,
            "pool_report_p95_ms": pool_block["p95_ms"],
            "cache_integrity_report": pool_block["p95_ms"] >= LIMIT_P95_MS,
        },
    }
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

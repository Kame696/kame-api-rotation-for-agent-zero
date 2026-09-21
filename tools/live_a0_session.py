"""A real session: real Agent Zero code, real KAME, real litellm, real provider.

    python tools/live_a0_session.py /path/to/agent-zero --env /path/to/.env \
        --var GOOGLE_API_KEY --provider gemini --model gemini-3.8-flash \
        --calls 8 --burst 8

`tests/test_a0_compat.py` proves KAME binds to a real Agent Zero checkout, but
it fakes the network. This one does not: every call goes through Agent Zero's
own `LiteLLMChatWrapper.unified_call`, KAME's wrapper around it, the installed
litellm, and out to the provider with the keys in `--env`. It is the "survived a
real session" gate the changelog asks for before an Agent Zero release.

It prints key fingerprints and counts only - never a key - and reads the keys
from the file you point it at. It makes `--calls` sequential calls and then
`--burst` concurrent ones, so both the carousel and the anti-dogpile spread are
exercised against live quota. Keep the numbers small: every call spends real
quota on real keys.

Exit 0 when every call answered, 1 otherwise, 2 when it could not run.
"""
from __future__ import annotations

import argparse
import asyncio
import collections
import contextlib
import importlib.util
import io
import os
import re
import sys
import time
import types

HERE = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _read_keys(path: str, var: str) -> list:
    for line in open(path, encoding="utf-8", errors="replace"):
        if line.lstrip().startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        if name.strip() == var:
            value = value.strip().strip('"').strip("'")
            return [k.strip() for k in value.split(",") if k.strip()]
    return []


class _Any:
    def __init__(self, *a, **k): pass
    def __getattr__(self, item): return _Any()
    def __call__(self, *a, **k): return _Any()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("a0")
    ap.add_argument("--env", required=True)
    ap.add_argument("--var", default="GOOGLE_API_KEY")
    ap.add_argument("--provider", default="gemini")
    ap.add_argument("--model", default="gemini-3.8-flash")
    ap.add_argument("--calls", type=int, default=8)
    ap.add_argument("--burst", type=int, default=8)
    ap.add_argument("--no-capture", action="store_true", help="let KAME's log go straight to the console")
    ap.add_argument("--full-errors", action="store_true", help="print each raw provider error KAME sees")
    args = ap.parse_args()

    keys = _read_keys(args.env, args.var)
    if not keys:
        print(f"no keys under {args.var} in the given file")
        return 2

    # nest_asyncio (which Agent Zero applies on import) breaks
    # asyncio.current_task() on Python 3.14, and httpx's timeouts need it: every
    # request dies instantly with "Timeout should be used inside a task". Agent
    # Zero's own Docker image runs an older Python where the shim works. This
    # session never nests event loops, so on 3.14 the shim is replaced by a
    # no-op - the model layer, the transport and KAME stay exactly as shipped.
    if sys.version_info >= (3, 14) and "nest_asyncio" not in sys.modules:
        shim = types.ModuleType("nest_asyncio")
        shim.apply = lambda *a, **k: None
        sys.modules["nest_asyncio"] = shim
        print("note: nest_asyncio replaced by a no-op (Python 3.14); nothing else is stubbed")

    sys.path.insert(0, args.a0)
    os.chdir(args.a0)
    for name in ("sentence_transformers", "whisper"):
        if name not in sys.modules:
            stub = types.ModuleType(name)
            stub.SentenceTransformer = _Any
            stub.load_model = _Any
            sys.modules[name] = stub

    import models  # noqa: E402

    # v1.8.1.0: the plugin directory on the path, last, so the engine's
    # optional modules (kame_evidence, kame_journal) load here exactly as they
    # do in an install. Without it they were silently absent from this run:
    # the error reader fell back to the legacy rules and no event, refusal or
    # timing was recorded.
    sys.path.append(HERE)
    spec = importlib.util.spec_from_file_location("kame_engine", os.path.join(HERE, "kame_engine.py"))
    kame = importlib.util.module_from_spec(spec)
    sys.modules["kame_engine"] = kame
    spec.loader.exec_module(kame)
    kame._KAME_KEY_HEALTH = {}
    kame.set_log_level("normal")
    kame.set_key_log_style("fingerprint")
    if args.full_errors:
        kame.set_log_full_errors(True)
    if not kame.apply_kame_patch():
        print("KAME did not bind to this Agent Zero")
        return 2
    kame._get_all_api_keys = lambda self: list(keys)
    fp = {k: kame._key_short_id(k) for k in keys}

    print(f"KAME {kame.KAME_VERSION} on Agent Zero at {os.path.basename(os.path.abspath(args.a0))}")
    print(f"provider {args.provider}, model {args.model}, {len(keys)} keys (fingerprints only)")

    wrapper = models.LiteLLMChatWrapper(model=args.model, provider=args.provider)
    log = io.StringIO()

    async def one(n: int) -> tuple:
        started = time.time()
        try:
            answer, _reasoning = await models.LiteLLMChatWrapper.unified_call(
                wrapper,
                system_message="You are a terse test endpoint.",
                user_message=f"Reply with exactly: OK {n}",
                a0_api_mode="chat_completions",
            )
            return n, True, time.time() - started, (answer or "").strip()[:40]
        except Exception as exc:  # noqa: BLE001 - reported, never raised
            return n, False, time.time() - started, f"{type(exc).__name__}"

    async def session() -> list:
        results = []
        for n in range(1, args.calls + 1):
            results.append(await one(n))
        burst = await asyncio.gather(*(one(100 + n) for n in range(1, args.burst + 1)))
        results.extend(burst)
        return results

    # No asyncio.wait_for here: Agent Zero installs nest_asyncio, which breaks
    # current_task() on Python 3.14, so an asyncio timeout cannot run inside it.
    # Bound the whole run from outside instead (`timeout 600 python ...`).
    t0 = time.time()
    if args.no_capture:
        results = asyncio.run(session())
    else:
        with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
            results = asyncio.run(session())
    wall = time.time() - t0
    text = log.getvalue()
    for k, f in fp.items():  # belt and braces: nothing key-shaped leaves this process
        text = text.replace(k, f"<{f}>")

    print(f"\n{len(results)} calls in {wall:.1f}s")
    for n, ok, secs, what in results:
        kind = "burst" if n > 100 else "seq"
        print(f"  {kind} {n % 100:>2}  {'answered' if ok else 'FAILED  '}  {secs:6.1f}s  {what}")

    report = kame.pool_report()
    used = {}
    for ident, pool in kame._KAME_KEY_HEALTH.items():
        for k, v in pool.get("keys", {}).items():
            if v.get("request_log"):
                used[fp.get(k, "?")] = used.get(fp.get(k, "?"), 0) + len(v["request_log"])
    print(f"\nkeys that carried calls: {len(used)} of {len(keys)}  {dict(sorted(used.items()))}")
    stats = {k: v for k, v in kame._KAME_STATS.items() if v}
    print(f"engine counters: {stats}")
    lines = [ln for ln in text.splitlines() if "[KAME]" in ln]
    rungs = collections.Counter(m.group(1) for ln in lines for m in re.finditer(r"\[(backoff\.\d+)\]", ln))
    kinds = collections.Counter(m.group(1) for ln in lines for m in re.finditer(r"(per-minute|daily-quota|server-busy|timeout|insufficient_quota|refused|not a valid key)", ln))
    print(f"refusal lines: {len(lines)}  kinds: {dict(kinds)}  ladder rungs: {dict(rungs)}")
    for ln in lines[:12]:
        print("   ", ln.strip()[:170])
    print(f"report keys: {sorted(report)[:8]}")
    # v1.8.1.0: say which optional parts actually ran, and what they recorded.
    print(f"error reader loaded: {getattr(kame, '_KE', None) is not None} · "
          f"journal loaded: {getattr(kame, '_KJ', None) is not None}")
    events = report.get("events") or []
    print(f"events: {dict(collections.Counter(e.get('kind') for e in events))} · "
          f"files: {report.get('data_dir') or 'none (set KAME_DATA_DIR to write them)'}")

    answered = sum(1 for r in results if r[1])
    total = args.calls + args.burst
    print(f"\nRESULT: {answered}/{total} answered")
    return 0 if answered == total else 1


if __name__ == "__main__":
    raise SystemExit(main())

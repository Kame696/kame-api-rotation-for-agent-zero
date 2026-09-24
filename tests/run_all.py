"""Run every offline suite in this folder, one process each, and summarise.

Most suites here are scripts, not pytest modules: each one stubs Agent Zero in
its own ``sys.modules`` and ends with ``sys.exit(0)``. Two of them in one
interpreter would share those stubs, and pytest cannot collect a module that
exits on import. So each runs alone, exactly the way the upgrade runbook runs
them by hand.

The 1.8.1.1 and 1.8.1.2 suites are the other kind: plain ``def test_*``
functions that take pytest fixtures (``monkeypatch``) and never call anything
at import. Run as a script, such a file defines its tests and exits 0 having
executed none of them -- a pass that proves nothing. A file with top-level
test functions is therefore handed to ``python -m pytest``, which needs
pytest installed (``pip install pytest``).

``test_a0_compat.py`` is the live harness and needs a real Agent Zero checkout;
it runs only when one is given (``A0_PATH`` or ``--a0 <path>``) and is skipped
otherwise, the same way it skips itself.

    python tests/run_all.py
    python tests/run_all.py --a0 /path/to/agent-zero

Exit 0 when every suite passed, 1 otherwise.
"""
import argparse
import ast
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
LIVE = "test_a0_compat.py"


def is_pytest_module(path: Path) -> bool:
    """True when the file's tests are functions only pytest would call."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return False
    return any(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith("test_")
        for node in tree.body
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--a0", default=os.environ.get("A0_PATH", ""),
                        help="Agent Zero checkout for the live harness")
    parser.add_argument("--timeout", type=float, default=600.0,
                        help="seconds allowed per suite")
    args = parser.parse_args()

    suites = sorted(p for p in HERE.glob("test_*.py") if p.name != LIVE)
    if args.a0:
        suites.append(HERE / LIVE)

    env = dict(os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    failed = []
    for suite in suites:
        if is_pytest_module(suite):
            argv = [sys.executable, "-B", "-m", "pytest", "-q", "-p", "no:cacheprovider", str(suite)]
        else:
            argv = [sys.executable, "-B", str(suite)]
        if suite.name == LIVE:
            argv.append(args.a0)
        started = time.monotonic()
        try:
            proc = subprocess.run(argv, cwd=str(HERE.parent), env=env,
                                  capture_output=True, text=True,
                                  encoding="utf-8", errors="replace",
                                  timeout=args.timeout)
            code, out = proc.returncode, proc.stdout + proc.stderr
        except subprocess.TimeoutExpired as exc:
            code = "timeout"
            out = (exc.stdout or "") + (exc.stderr or "")
            if isinstance(out, bytes):
                out = out.decode("utf-8", "replace")
        took = time.monotonic() - started
        ok = code == 0
        last = next((l.strip() for l in reversed(out.splitlines()) if l.strip()), "")
        print(f"{'PASS' if ok else 'FAIL'}  {suite.name:<36} {took:6.1f}s  {last[:60]}")
        if not ok:
            failed.append(suite.name)
            tail = "\n".join(out.strip().splitlines()[-25:])
            print("      " + tail.replace("\n", "\n      "))

    print()
    if failed:
        print(f"{len(failed)} of {len(suites)} suites FAILED: {', '.join(failed)}")
        return 1
    print(f"all {len(suites)} suites passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())

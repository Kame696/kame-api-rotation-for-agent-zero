"""KAME <-> Agent Zero upgrade checker.

Answers ONE question in one command: *did the new Agent Zero break KAME, and
where exactly?* Run it every time A0 ships a release.

It does three things, in order, and each one is independently useful:

  1. VERSION      - asks GitHub for A0's newest tag and compares it to the
                    version KAME is pinned as verified against (a0_compat.json).
  2. FINGERPRINTS - hashes the source of every A0 symbol KAME monkey-patches,
                    calls, or depends on the shape of, and diffs those hashes
                    against the recorded baseline. A changed hash names the
                    exact function to re-read - no guessing which of A0's
                    thousands of files matter.
  3. LIVE TESTS   - runs tests/test_a0_compat.py against the checkout, which
                    applies and reverts KAME's real patches on the real classes.

Usage
-----
    # full check against a local Agent Zero checkout
    python tools/a0_upgrade_check.py /path/to/agent-zero

    # version check only (no checkout needed, hits the GitHub API)
    python tools/a0_upgrade_check.py --latest

    # after auditing a new A0 and fixing whatever broke: re-pin the baseline
    python tools/a0_upgrade_check.py /path/to/agent-zero --update-baseline v2.8

Exit codes: 0 = clean, 1 = something needs a human, 2 = could not run.

No third-party dependencies. Importing A0 needs A0's own runtime deps
(litellm, langchain-core, pillow, nest_asyncio, ...) importable; the
fingerprint stage falls back to plain source-file parsing when they are not,
so stages 1 and 2 still work in a bare environment.
"""
import argparse
import ast
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
KAME = os.path.dirname(HERE)
BASELINE = os.path.join(KAME, "a0_compat.json")
A0_REPO = "agent0ai/agent-zero"

OK = "  OK   "
WARN = " CHECK "
BAD = " BROKE "


def _load_baseline():
    with open(BASELINE, encoding="utf-8") as fh:
        return json.load(fh)


# --- stage 1: what is the newest Agent Zero? --------------------------------

def latest_a0_tag():
    """Newest non-prerelease A0 tag, or None if GitHub is unreachable."""
    url = f"https://api.github.com/repos/{A0_REPO}/tags?per_page=100"
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json",
                                               "User-Agent": "kame-upgrade-check"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            tags = json.loads(resp.read().decode())
    except (urllib.error.URLError, OSError, ValueError) as exc:
        print(f"{WARN} could not reach GitHub: {exc}")
        return None

    def key(name):
        nums = re.findall(r"\d+", name)
        return [int(n) for n in nums] or [0]

    stable = [t["name"] for t in tags if re.fullmatch(r"v?\d+(\.\d+)*", t["name"])]
    return max(stable, key=key) if stable else None


# --- stage 2: fingerprint the symbols KAME depends on -----------------------

def _source_of(a0_path, dotted, symbol):
    """Return the source text of `symbol` inside module `dotted`, or None.

    Parses the file with `ast` instead of importing it, so this works without
    A0's runtime dependencies installed. `symbol` is "Class.method", "Class",
    or a plain function name.
    """
    path = os.path.join(a0_path, *dotted.split(".")) + ".py"
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as fh:
        src = fh.read()
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return None

    parts = symbol.split(".")

    def find(nodes, want):
        for node in nodes:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) \
                    and node.name == want:
                return node
        return None

    node = find(tree.body, parts[0])
    for part in parts[1:]:
        if node is None:
            return None
        node = find(getattr(node, "body", []), part)
    if node is None:
        return None
    return ast.get_source_segment(src, node)


def _fingerprint(text):
    """Whitespace- and comment-insensitive hash, so cosmetic edits stay quiet."""
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        lines.append(re.sub(r"\s+", " ", stripped))
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()[:16]


def fingerprints(a0_path, watch):
    out, missing = {}, []
    for entry in watch:
        src = _source_of(a0_path, entry["module"], entry["symbol"])
        if src is None:
            missing.append(entry)
            continue
        out[entry["id"]] = _fingerprint(src)
    return out, missing


# --- stage 3: the live harness ----------------------------------------------

def run_live_tests(a0_path):
    harness = os.path.join(KAME, "tests", "test_a0_compat.py")
    if not os.path.isfile(harness):
        print(f"{WARN} tests/test_a0_compat.py not found - skipping live tests")
        return None
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    # utf-8 + replace: the harness prints A0/KAME output that can contain emoji,
    # and the default cp1252 decode on Windows would blow up reading it.
    proc = subprocess.run([sys.executable, harness, a0_path],
                          capture_output=True, text=True, env=env,
                          encoding="utf-8", errors="replace")
    return proc


def main():
    ap = argparse.ArgumentParser(description="Check KAME against a new Agent Zero.")
    ap.add_argument("a0_path", nargs="?", help="path to an Agent Zero checkout")
    ap.add_argument("--latest", action="store_true",
                    help="only ask GitHub for the newest A0 tag, then exit")
    ap.add_argument("--update-baseline", metavar="VERSION",
                    help="re-pin a0_compat.json to this checkout + version "
                         "(do this ONLY after the audit is green)")
    ap.add_argument("--skip-tests", action="store_true")
    args = ap.parse_args()

    try:
        base = _load_baseline()
    except (OSError, ValueError) as exc:
        print(f"{BAD} cannot read {BASELINE}: {exc}")
        return 2

    pinned = base["verified_against"]
    print(f"KAME {base['kame_version']} - verified against Agent Zero {pinned}")

    newest = latest_a0_tag()
    if newest:
        if newest == pinned:
            print(f"{OK} Agent Zero's newest tag is {newest} - nothing to do.")
        else:
            print(f"{WARN} Agent Zero shipped {newest} (KAME is pinned at {pinned}).")
    if args.latest:
        return 0

    if not args.a0_path:
        print("\nGive me a checkout to audit:")
        print(f"  git clone --depth 1 --branch {newest or '<tag>'} "
              f"https://github.com/{A0_REPO}.git /tmp/a0")
        print(f"  python tools/a0_upgrade_check.py /tmp/a0")
        return 1
    if not os.path.isdir(args.a0_path):
        print(f"{BAD} not a directory: {args.a0_path}")
        return 2

    print(f"\nFingerprinting {len(base['watch'])} patch points in {args.a0_path}")
    current, missing = fingerprints(args.a0_path, base["watch"])
    by_id = {e["id"]: e for e in base["watch"]}
    recorded = base["fingerprints"]

    gone, changed, same = [], [], []
    for entry in missing:
        gone.append(entry["id"])
    for key, value in current.items():
        (same if recorded.get(key) == value else changed).append(key)

    for key in sorted(gone):
        entry = by_id[key]
        print(f"{BAD} MISSING  {key}")
        print(f"         {entry['module'].replace('.', '/')}.py :: {entry['symbol']}")
        print(f"         why KAME cares: {entry['why']}")
    for key in sorted(changed):
        entry = by_id[key]
        print(f"{WARN} CHANGED  {key}  {recorded.get(key, '-')} -> {current[key]}")
        print(f"         {entry['module'].replace('.', '/')}.py :: {entry['symbol']}")
        print(f"         why KAME cares: {entry['why']}")
    print(f"{OK} unchanged: {len(same)}/{len(base['watch'])}")

    tests_failed = False
    if not args.skip_tests:
        print("\nRunning tests/test_a0_compat.py against the checkout")
        proc = run_live_tests(args.a0_path)
        if proc is not None:
            tail = [ln for ln in proc.stdout.splitlines() if ln.strip()][-6:]
            for line in tail:
                print("   " + line)
            if proc.returncode != 0:
                tests_failed = True
                print(f"{BAD} live harness FAILED (exit {proc.returncode})")
                if proc.stderr.strip():
                    print("   " + proc.stderr.strip().splitlines()[-1])
            else:
                print(f"{OK} live harness green")

    dirty = bool(gone or changed or tests_failed)

    if args.update_baseline:
        # Changed hashes are EXPECTED here - re-pinning is what you do after
        # auditing them. Only a symbol KAME can no longer find, or a red live
        # harness, means the new A0 is genuinely unsupported.
        if gone or tests_failed:
            print(f"\n{BAD} refusing to re-pin the baseline while something is "
                  f"broken. Fix it, get a clean run, then re-pin.")
            return 1
        if changed:
            print(f"\n{WARN} re-pinning over {len(changed)} changed symbol(s) - "
                  f"do this only after reading each one above.")
        base["verified_against"] = args.update_baseline
        base["fingerprints"] = current
        with open(BASELINE, "w", encoding="utf-8") as fh:
            json.dump(base, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        print(f"\n{OK} baseline re-pinned to Agent Zero {args.update_baseline}.")
        print("   Now update: plugin.yaml description tag, README badge, "
              "CHANGELOG, STATE.md.")
        return 0

    if dirty:
        print("\nNext: read each flagged symbol, then follow "
              "COMPATIBILITY.md -> 'Upgrade runbook'.")
        return 1
    print(f"\n{OK} KAME is compatible with this Agent Zero checkout.")
    if newest and newest != pinned:
        print(f"   Re-pin it:  python tools/a0_upgrade_check.py "
              f"{args.a0_path} --update-baseline {newest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

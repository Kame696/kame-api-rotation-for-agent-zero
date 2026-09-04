"""KAME <-> Agent Zero upgrade checker.

Answers ONE question in one command: *did the new Agent Zero break KAME, and
where exactly?* Run it every time A0 ships a release.

It does four things, in order, and each one is independently useful:

  1. VERSION      - asks GitHub for A0's newest tag and compares it to the
                    version KAME is pinned as verified against (a0_compat.json).
  2. FINGERPRINTS - hashes the source of every A0 symbol KAME monkey-patches,
                    calls, or depends on the shape of, and diffs those hashes
                    against the recorded baseline. A changed hash names the
                    exact function to re-read - no guessing which of A0's
                    thousands of files matter.
  2b. HOST FACTS  - (v1.6.0.1) asserts every assumption KAME reasons about that
                    is NOT a symbol: "the injected api_key wins", "A0 handles
                    its own empty responses", "the snapshot is strict so the
                    panel reads its own endpoint". Those used to live in
                    comments, and a comment cannot fail. Now a door that opens
                    fails by name instead of rotting.
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
import datetime
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


# --- stage 2b: host-fact tripwires (v1.6.0.1) --------------------------------
#
# A fingerprint says a symbol CHANGED. It cannot say that an assumption KAME
# reasons about stopped being true, because most of those assumptions are not
# symbols — they are sentences in comments: "we did not port this because Agent
# Zero already does it", "the injected api_key wins", "the snapshot is strict so
# the panel reads its own endpoint".
#
# A comment cannot fail. Every one of these used to be a sentence somebody would
# have to remember to re-check, and the failure mode of forgetting is silent: the
# door opens, the workaround stays, and nothing anywhere says so.
#
# So each is asserted against the installed checkout, and a door that opens now
# fails BY NAME.
#
#   critical — if this stopped being true, KAME does not rotate
#   degraded — one shield or one screen stops; rotation is untouched
#   note     — a deliberate non-port became portable. Nothing is broken; there
#              is something worth doing.
HOST_FACTS = (
    {
        "id": "api_key injection wins over the resolver",
        "file": "models.py",
        "needle": 'kwargs.pop("api_key", None) or get_api_key(',
        "severity": "critical",
        "why": "THE load-bearing assumption since 1.0.9. KAME chooses a key and "
               "passes it as api_key=; Agent Zero's own resolver must yield to it. "
               "If this line changes shape, KAME picks keys that are then ignored "
               "and every call goes out on whichever key A0 round-robins to.",
    },
    {
        "id": "agent_init is still a named extension point",
        "file": "agent.py",
        "needle": '"agent_init"',
        "severity": "critical",
        "why": "Activation door 1 of 3. All three doors closing means KAME never "
               "installs — silently, because there is nothing to raise.",
    },
    {
        "id": "monologue_start is still a named extension point",
        "file": "agent.py",
        "needle": '"monologue_start"',
        "severity": "critical",
        "why": "Activation door 2 of 3.",
    },
    {
        "id": "the rate limiter still has the shape KAME patches",
        "file": "helpers/rate_limiter.py",
        "needle": ["self._lock", "self.values", "self.timeframe",
                   "async def cleanup", "async def get_total"],
        "severity": "degraded",
        "why": "_patch_rate_limiters() swaps an asyncio.Lock for a threading.Lock "
               "to stop a deadlock. If the shape moved, the patch no-ops and the "
               "deadlock it fixes comes back.",
    },
    {
        "id": "the unusable-response guard still stores its count where KAME reads it",
        "file": "extensions/python/_functions/agent/Agent/hist_add_warning/end/"
                "_90_stop_unusable_response_loop.py",
        "needle": '"_unusable_response_failures"',
        "severity": "degraded",
        "why": "KAME's floor reads A0's own counter out of "
               "loop_data.params_persistent under this key. A rename makes the "
               "floor silently stop lifting and A0's own limit applies again.",
    },
    {
        "id": "the notification manager can still push a live toast",
        "file": "helpers/notification.py",
        "needle": ["def send_notification", "PROGRESS", "mark_dirty_all"],
        "severity": "degraded",
        "why": "v1.6.0.1 narrates a wait through a PROGRESS notification with a "
               "stable id, which updates in place and is pushed over the "
               "WebSocket. Without it the wait is only said at ninety seconds, "
               "in the chat — which is where it was said before, and why nobody "
               "ever saw it.",
    },
    {
        "id": "the WebUI plugin slot the rotation chip lives in still exists",
        "file": "helpers/extension.py",
        "needle": "extensions/webui",
        "severity": "degraded",
        "why": "Added in Agent Zero v2.11. It is what puts the rotation on screen "
               "beside the composer. If it goes, the chip silently never renders.",
    },
    {
        "id": "plugin-contributed slash commands are still discovered",
        "file": "plugins/_commands/helpers/commands.py",
        "needle": "_discover_plugin_commands",
        "severity": "degraded",
        "why": "How /kame and /kame doctor ship. Without it the diagnostic is "
               "back to being a script in a repository, which is the one place "
               "it is guaranteed not to be when it is wanted.",
    },
    {
        "id": "plugin API handlers are still routed at plugins/<name>/<handler>",
        "file": "helpers/api.py",
        "needle": 'path.startswith("plugins/")',
        "severity": "degraded",
        "why": "The route the rotation chip reads. A change here leaves the chip "
               "rendering its last good view for ever.",
    },
    {
        "id": "the state snapshot is still schema-strict",
        "file": "helpers/state_snapshot.py",
        "needle": "unexpected=",
        "severity": "note",
        "why": "A plugin CANNOT add a field to Agent Zero's WebSocket snapshot, "
               "which is why the chip reads its own endpoint on the snapshot's "
               "cadence instead of riding it. If this strictness ever goes, "
               "riding the snapshot becomes possible and is strictly cheaper.",
    },
    {
        "id": "Agent Zero still handles its own empty responses",
        "file": "extensions/python/message_loop_result/_20_empty_response.py",
        "needle": "fw.msg_empty_response.md",
        "severity": "note",
        "why": "KAME rotates on an empty answer INSIDE the call, so A0's own "
               "guard only ever sees the pool's verdict. Recorded so the "
               "ordering is a checked fact rather than a remembered one. See "
               "decisions/0003.",
    },
    {
        "id": "the _error_retry plugin still sits ABOVE KAME",
        "file": "plugins/_error_retry/extensions/python/_functions/agent/Agent/"
                "handle_exception/end/_80_retry_critical_exception.py",
        "needle": "class ",
        "severity": "note",
        "why": "It retries a whole monologue after an exception escapes. With "
               "KAME installed it should almost never fire, and when it does it "
               "means the carousel gave up. Not a conflict — but do not debug "
               "one while looking at the other.",
    },
)


def check_host_facts(a0_path):
    """Assert every 'we did not port this because A0 does X' against the host.

    Returns (ok, broken) where broken carries the fact dicts that no longer
    hold, each with the severity that says how much it costs.
    """
    ok, broken = [], []
    for fact in HOST_FACTS:
        path = os.path.join(a0_path, *fact["file"].split("/"))
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError:
            broken.append(dict(fact, detail="file not found"))
            continue
        needles = fact["needle"]
        if isinstance(needles, str):
            needles = [needles]
        missing = [n for n in needles if n not in text]
        if missing:
            broken.append(dict(fact, detail=f"not found: {', '.join(missing)}"))
        else:
            ok.append(fact["id"])
    return ok, broken


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

    gone, absent, changed, same = [], [], [], []
    for entry in missing:
        # `optional` symbols exist only from some A0 version onward (e.g. the
        # unusable-response guard, added in v2.4). Not finding one on an older
        # checkout is the expected shape of the world, not a break — the shield
        # that watches it is inert there by design. Report, never fail.
        (absent if entry.get("optional") else gone).append(entry["id"])
    for key, value in current.items():
        (same if recorded.get(key) == value else changed).append(key)

    def _sev(entry):
        return entry.get("severity", "critical")

    for key in sorted(gone):
        entry = by_id[key]
        print(f"{BAD} MISSING  [{_sev(entry)}] {key}")
        print(f"         {entry['module'].replace('.', '/')}.py :: {entry['symbol']}")
        print(f"         why KAME cares: {entry['why']}")
    for key in sorted(absent):
        entry = by_id[key]
        print(f"{OK} not present (optional) {key}")
        print(f"         this Agent Zero predates it; KAME's matching shield "
              f"stays inert. Nothing to do.")
    for key in sorted(changed):
        entry = by_id[key]
        print(f"{WARN} CHANGED  [{_sev(entry)}] {key}  {recorded.get(key, '-')} -> {current[key]}")
        print(f"         {entry['module'].replace('.', '/')}.py :: {entry['symbol']}")
        print(f"         why KAME cares: {entry['why']}")
    print(f"{OK} unchanged: {len(same)}/{len(base['watch']) - len(absent)}"
          + (f" ({len(absent)} optional symbol(s) not on this build)" if absent else ""))

    # v1.0.9: a changed fingerprint on an `adaptive` symbol is EXPECTED noise -
    # KAME delegates the call and finds the entry points by shape, so A0 is free
    # to rewrite those bodies. Say so, instead of letting it read as a red flag.
    _adaptive = [k for k in changed if _sev(by_id[k]) == "adaptive"]
    if _adaptive:
        print(f"{OK} {len(_adaptive)} of those are 'adaptive' - KAME handles them "
              f"automatically (delegation + shape-based binding). The live harness "
              f"below is the real verdict for these.")
    _serious = [k for k in changed if _sev(by_id[k]) != "adaptive"]
    if _serious:
        print(f"{WARN} {len(_serious)} changed symbol(s) need a human read: "
              f"{', '.join(sorted(_serious))}")

    # --- stage 2b: the assumptions that are sentences, not symbols ----------
    print(f"\nChecking {len(HOST_FACTS)} host facts KAME reasons about")
    facts_ok, facts_broken = check_host_facts(args.a0_path)
    facts_failed = False
    for fact in facts_broken:
        marker = BAD if fact["severity"] == "critical" else WARN
        print(f"{marker} [{fact['severity']}] {fact['id']}")
        print(f"         {fact['file']} — {fact['detail']}")
        print(f"         why KAME cares: {fact['why']}")
        if fact["severity"] == "critical":
            facts_failed = True
    if not facts_broken:
        print(f"{OK}  all {len(facts_ok)} still hold")
    else:
        print(f"{OK}  {len(facts_ok)}/{len(HOST_FACTS)} still hold")

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

    dirty = bool(gone or changed or tests_failed or facts_failed)

    if args.update_baseline:
        # Changed hashes are EXPECTED here - re-pinning is what you do after
        # auditing them. Only a symbol KAME can no longer find, or a red live
        # harness, means the new A0 is genuinely unsupported.
        if gone or tests_failed or facts_failed:
            print(f"\n{BAD} refusing to re-pin the baseline while something is "
                  f"broken. Fix it, get a clean run, then re-pin.")
            return 1
        if changed:
            print(f"\n{WARN} re-pinning over {len(changed)} changed symbol(s) - "
                  f"do this only after reading each one above.")
        base["verified_against"] = args.update_baseline
        # stamp the date too - a baseline that says v2.8 but still carries the v2.7
        # date is worse than no date at all.
        base["verified_on"] = datetime.date.today().isoformat()
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

"""Machine check that the Agent Zero port and the Hermes port of KAME say and time the same things.

A development tool (it is not shipped in the plugin zip). It READS the Hermes sources and the Agent Zero ones
and never writes either. The rule it enforces (1.8.3.1, PLAN D5): one product for two frameworks - the same
version number gives the same logic, defaults, timings, words, order and look - except where the framework
forces a difference, and every such difference is WRITTEN here, with its reason.

    python tools/parity_check.py             # print the report; exit 1 on a difference not in ALLOWED
    python tools/parity_check.py --json      # the same, as JSON

What it compares:

  * every setting both ports have: title (the host's name is the only change allowed), kind, default, choices
    and their order, range (lo / hi), advanced, shelf, and the order inside the shelf;
  * a named list of timing constants read from both sources: the engine's seconds (slow bound, probe clock,
    backoffs, holds, hedging multipliers, the first-token wait) and the page and chip counts (clock tick, models
    and replies listed).

A difference that is not in ``ALLOWED`` fails the check. An ``ALLOWED`` entry whose difference is gone fails it
too, so the list cannot rot: when the ports meet, the entry goes.

The Hermes tree is ``../../hermes/hermes-kame-api-rotation`` from ``current/``. Without it (the public repo, the
shipped zip) the tool reports that and exits 0: there is nothing to compare against.
"""

from __future__ import annotations

import argparse
import ast
import importlib.util
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent                                     # .../agent-zero/current
HERMES = ROOT.parent.parent / "hermes" / "hermes-kame-api-rotation"

#: The host's name is the only difference allowed in a sentence both ports share.
SUBSTITUTIONS = [["Hermes’", "Agent Zero’s"], ["Hermes'", "Agent Zero's"], ["Hermes", "Agent Zero"]]

#: Hermes setting key -> Agent Zero key, where the name is not the same.
RENAMED = {"disabled": "rotation_disabled"}

#: Hermes settings with no function in Agent Zero (nothing to compare).
HERMES_ONLY = {"resolver_disabled", "quota_id_disabled"}

#: Settings only Agent Zero has (its own log and chat settings, the fallback list's own tab).
AGENT_ZERO_ONLY = {"fallback_models", "kame_log_level", "key_log_style", "kame_log_full_errors",
                   "kame_unusable_response_limit"}

# -- the named timing constants ---------------------------------------------------------------------------
#
# (label, (Agent Zero file, name), (Hermes file, name), path into the value or None). Python constants are read
# with ``ast`` (a literal at the top of the module), JavaScript ones with a regular expression
# (``const NAME = <number>``). ``path`` picks one element of a tuple / dict value in the Hermes constant.

ENGINE = "kame_engine.py"
TRANSPORT = "transport.py"
CAROUSEL = "core/carousel.py"
HEDGING = ("kame_hedging.py", "core/hedging.py")
LATENCY = ("kame_latency.py", "core/latency.py")
PACING = ("kame_pacing.py", "core/pacing.py")
AUX = ("kame_aux.py", "core/aux_budget.py")
STITCH = ("kame_stitch.py", "core/stitch.py")

PY_CONSTANTS = [
    # the engine: when a model is slow, busy, probed, widened
    ("slow bound (floor, ceiling, cold)", (ENGINE, "_KAME_SLOW_S"), (TRANSPORT, "_SLOW_S"), None),
    ("slow bound, first mark", (ENGINE, "_KAME_SLOW_FIRST_S"), (TRANSPORT, "_SLOW_FIRST_S"), None),
    ("slow bound, ceiling", (ENGINE, "_KAME_SLOW_MAX_S"), (TRANSPORT, "_SLOW_MAX_S"), None),
    ("slow bound, tokens per second", (ENGINE, "_KAME_SLOW_TOKENS_PER_S"), (TRANSPORT, "_SLOW_TOKENS_PER_S"), None),
    ("first-token wait, automatic (floor, ceiling, cold)", (ENGINE, "_KAME_AUTO_SILENCE_S"), (TRANSPORT, "_AUTO_SILENCE_S"), None),
    ("first-token wait, tokens per second", (ENGINE, "_KAME_AUTO_SILENCE_TOKENS_PER_S"), (TRANSPORT, "_AUTO_SILENCE_TOKENS_PER_S"), None),
    ("first-token wait, Off (1.8.3.1)", (ENGINE, "_KAME_FIRST_TOKEN_OFF_S"), (TRANSPORT, "_AUTO_SILENCE_S"), (False, 1)),
    ("automatic switch, cold", (ENGINE, "_KAME_AUTO_SWITCH_COLD_S"), (TRANSPORT, "_AUTO_SWITCH_COLD_S"), None),
    ("automatic switch, ceiling", (ENGINE, "_KAME_AUTO_SWITCH_MAX_S"), (TRANSPORT, "_AUTO_SWITCH_MAX_S"), None),
    ("automatic switch, floor", (ENGINE, "_KAME_AUTO_SWITCH_MIN_S"), (TRANSPORT, "_AUTO_SWITCH_MIN_S"), None),
    ("a busy model, first rest", (ENGINE, "_KAME_BUSY_FIRST_S"), (TRANSPORT, "_BUSY_FIRST_S"), None),
    ("a busy model, longest rest", (ENGINE, "_KAME_BUSY_MAX_S"), (TRANSPORT, "_BUSY_MAX_S"), None),
    ("a busy model, asked again every", (ENGINE, "_KAME_PROBE_EVERY_S"), (TRANSPORT, "_PROBE_EVERY_S"), None),
    ("burst, long rest", (ENGINE, "_KAME_BURST_LONG_REST_S"), (TRANSPORT, "_BURST_LONG_REST_S"), None),
    ("next model joins a silent reply at", (ENGINE, "_KAME_CROSS_COLD_S"), (TRANSPORT, "_CROSS_COLD_S"), None),
    ("next model joins, minimum", (ENGINE, "_KAME_CROSS_MIN_S"), (TRANSPORT, "_CROSS_MIN_S"), None),
    ("next model joins, step", (ENGINE, "_KAME_CROSS_STEP_S"), (TRANSPORT, "_CROSS_STEP_S"), None),
    ("models raced beside one reply", (ENGINE, "_KAME_CROSS_MAX_LEGS"), (TRANSPORT, "_CROSS_MAX_LEGS"), None),
    ("models added at the slow bound", (ENGINE, "_KAME_WIDEN_MAX"), (TRANSPORT, "_WIDEN_MAX"), None),
    ("a wait worth telling in the last replies", (ENGINE, "_KAME_REPLY_WAITED_NOTE_S"), (TRANSPORT, "_REPLY_WAITED_NOTE_S"), None),
    # the carousel: backoffs and holds
    ("daily quota rest", (ENGINE, "_KAME_DAILY_COOLDOWN_S"), (CAROUSEL, "DAILY_COOLDOWN_S"), None),
    ("empty answers before a key rests", (ENGINE, "_KAME_EMPTY_RETRY_BUDGET"), (CAROUSEL, "EMPTY_RETRY_BUDGET"), None),
    ("longest delay of any kind", (ENGINE, "_KAME_HARD_DELAY_CAP_S"), (CAROUSEL, "HARD_DELAY_CAP_S"), None),
    ("longest hold", (ENGINE, "_KAME_MAX_HOLD_S"), (CAROUSEL, "MAX_HOLD_S"), None),
    ("silence before the day's quota", (ENGINE, "_KAME_POOL_SILENCE_BEFORE_THE_DAY_S"), (CAROUSEL, "POOL_SILENCE_BEFORE_THE_DAY_S"), None),
    ("refusals before a key is retired", (ENGINE, "_KAME_REFUSALS_BEFORE_RETIRING"), (CAROUSEL, "REFUSALS_BEFORE_RETIRING"), None),
    ("rate-limit backoff cap", (ENGINE, "_KAME_RL_BACKOFF_CAP_S"), (CAROUSEL, "RL_BACKOFF_CAP_S"), None),
    ("server backoff cap", (ENGINE, "_KAME_SERVER_BACKOFF_CAP_S"), (CAROUSEL, "SERVER_BACKOFF_CAP_S"), None),
    ("server backoff base", (ENGINE, "_KAME_SERVER_BASE_S"), (CAROUSEL, "SERVER_BASE_S"), None),
    ("unsized backoff, longest", (ENGINE, "_KAME_UNSIZED_BACKOFF_MAX_S"), (CAROUSEL, "UNSIZED_BACKOFF_MAX_S"), None),
    ("unsized throttle rest", (ENGINE, "_KAME_UNSIZED_THROTTLE_REST_S"), (CAROUSEL, "UNSIZED_THROTTLE_REST_S"), None),
    # hedging, latency, pacing, background jobs, stitching
    *[(f"hedging {name.strip('_').lower()}", (HEDGING[0], name), (HEDGING[1], name), None) for name in (
        "_FASTER_BUDGET_X", "_FASTER_MULTIPLIER", "_FASTER_NONSTREAM", "_HEDGE_COOLDOWN_S", "_MAX_MODE_PENALTY_S",
        "_MAX_SAMPLES", "_MIN_SAMPLES", "_SAMPLE_WINDOW_S", "_STREAM_MIN_TRIGGER_S", "_STREAM_MULTIPLIER")],
    *[(f"latency {name.strip('_').lower()}", (LATENCY[0], name), (LATENCY[1], name), None) for name in (
        "_RING", "_SLOW_FAILURE_MIN_S", "_STALE_S", "_TAU_S")],
    *[(f"pacing {name.strip('_').lower()}", (PACING[0], name), (PACING[1], name), None) for name in (
        "_SKEW_S", "_WINDOW_S")],
    *[(f"background jobs {name.strip('_').lower()}", (AUX[0], name), (AUX[1], name), None) for name in (
        "FLOOR_S", "MEMO_S", "SUGGEST_MIN_SHARE", "SUGGEST_MIN_SIDE")],
    *[(f"stitching {name.strip('_').lower()}", (STITCH[0], name), (STITCH[1], name), None) for name in (
        "MIN_OVERLAP", "PROBE_CHARS", "RESTART_HEAD")],
]

JS_CONSTANTS = [
    ("page and chip read the status every (ms)", ("webui/kame-panel-store.js", "REFRESH_MS"), ("desktop/plugin.js", "TICK_MS")),
    ("models the popover lists", ("webui/kame-view.js", "CHIP_MODELS_MAX"), ("desktop/plugin.js", "CHIP_MODELS_MAX")),
    ("replies the popover lists", ("webui/kame-view.js", "CHIP_REPLIES_MAX"), ("desktop/plugin.js", "CHIP_REPLIES_MAX")),
    ("a reading is called old after (s)", ("webui/kame-view.js", "STALE_READING_S"), ("desktop/plugin.js", "STALE_AFTER_S")),
]

# -- the written differences ----------------------------------------------------------------------------------
#
# (kind, subject, field) -> why the ports differ. ``kind`` is ``setting`` or ``constant`` ; ``subject`` is the
# setting key (Agent Zero's) or the constant's label above.

ALLOWED = {
    ("setting", "share_pool_health", "title"):
        "Agent Zero has no profiles; the switch keeps holds across restarts and processes "
        "(tests/test_v1_8_3_0_settings_words.py writes the same reason)",
    ("constant", "a reading is called old after (s)"):
        "not the same clock: Hermes re-reads a file its backend republishes every 20 s (75 s = three misses); "
        "Agent Zero's page fetches the live status every second, so 30 s without a good read already means the "
        "process is gone",
}


# -- reading the sources -----------------------------------------------------------------------------------------

def read_py_constants(path: Path) -> dict:
    """Top-level ``NAME = <literal>`` of a Python module, without importing it."""
    out = {}
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return out
    for node in tree.body:
        target = value = None
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            target, value = node.targets[0].id, node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None:
            target, value = node.target.id, node.value
        if target is None:
            continue
        try:
            out[target] = ast.literal_eval(value)
        except (ValueError, SyntaxError):
            continue
    return out


def read_js_constant(path: Path, name: str):
    """``const NAME = <number>`` (optionally ``export``) of a JavaScript module; ``None`` when absent."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    match = re.search(rf"^(?:export\s+)?const\s+{re.escape(name)}\s*=\s*(-?[0-9][0-9_]*(?:\.[0-9]+)?)", text, re.M)
    if not match:
        return None
    number = float(match.group(1).replace("_", ""))
    return int(number) if number == int(number) else number


def load_hermes_settings(hermes: Path):
    """The Hermes ``settings.py`` module, loaded by path (read-only; no Hermes install is involved)."""
    settings_py = hermes / "settings.py"
    sys.path.insert(0, str(hermes))
    try:
        spec = importlib.util.spec_from_file_location("kame_hermes_settings_for_parity", settings_py)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(hermes))
    return module


def host_words(text: str) -> str:
    for old, new in SUBSTITUTIONS:
        text = text.replace(old, new)
    return re.sub(r"\s+", " ", text or "").strip()


def hermes_rows(hermes: Path):
    module = load_hermes_settings(hermes)
    rows = {}
    for item in module.describe_all():
        kind = item.get("kind")
        if kind == "string":
            kind = "choice" if item.get("choice_order") else "text"
        rows[item["key"]] = {
            "title": item.get("title"), "kind": kind, "default": item.get("default"),
            "choices": list(item.get("choice_order") or []),
            "lo": item.get("min") if kind == "number" else None, "hi": item.get("max") if kind == "number" else None,
            "advanced": bool(item.get("advanced")), "group": item.get("group"),
        }
    order = {g["id"]: list(g["keys"]) for g in module.groups()}
    return rows, order


def agent_zero_rows():
    sys.path.insert(0, str(ROOT))
    try:
        import kame_settings as S
    finally:
        sys.path.remove(str(ROOT))
    rows = {}
    for key, spec in S.SETTINGS.items():
        kind, default, lo, hi, choices, _env, title = spec[:7]
        rows[key] = {
            "title": title, "kind": kind, "default": default, "choices": list(choices or []),
            "lo": lo if kind == "number" else None, "hi": hi if kind == "number" else None,
            "advanced": key in S.ADVANCED, "group": S.group_of(key),
        }
    order = {gid: list(keys) for gid, _t, _n, keys in S.GROUPS}
    return rows, order


# -- the comparison --------------------------------------------------------------------------------------------------

def _same_number(a, b) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return float(a) == float(b)
    return a == b


def compare_settings(hermes: Path):
    """``[(subject, field, agent_zero_value, hermes_value)]`` for every setting field that differs."""
    h_rows, h_order = hermes_rows(hermes)
    a_rows, a_order = agent_zero_rows()
    found = []
    twins = {}
    for h_key in h_rows:
        if h_key in HERMES_ONLY:
            continue
        a_key = RENAMED.get(h_key, h_key)
        twins[h_key] = a_key
        if a_key not in a_rows:
            found.append((a_key, "exists", False, True))
            continue
        h, a = h_rows[h_key], a_rows[a_key]
        for field in ("kind", "default", "choices", "lo", "hi", "advanced", "group"):
            if field == "choices":
                same = tuple(a[field]) == tuple(h[field])
            else:
                same = _same_number(a[field], h[field])
            if not same:
                found.append((a_key, field, a[field], h[field]))
        if host_words(a["title"]) != host_words(h["title"]):
            found.append((a_key, "title", a["title"], h["title"]))
    for key in a_rows:
        if key not in twins.values() and key not in AGENT_ZERO_ONLY:
            found.append((key, "exists", True, False))
    # the order inside a shelf: Hermes' keys in Hermes' order, then the Agent Zero ones
    for gid, h_keys in h_order.items():
        wanted = [RENAMED.get(k, k) for k in h_keys if k not in HERMES_ONLY]
        mine = [k for k in a_order.get(gid, []) if k not in AGENT_ZERO_ONLY and k in twins.values()]
        if mine != wanted:
            found.append((f"shelf {gid}", "order", mine, wanted))
    return found


def compare_constants(hermes: Path):
    """``[(label, agent_zero_value, hermes_value)]`` for every named constant that differs, plus the ones
    that could not be read (a renamed constant must stop this check, not pass it)."""
    found = []
    a_cache, h_cache = {}, {}

    def py(cache, base: Path, rel: str):
        if (base, rel) not in cache:
            cache[(base, rel)] = read_py_constants(base / rel)
        return cache[(base, rel)]

    for label, (a_file, a_name), (h_file, h_name), path in PY_CONSTANTS:
        a_val = py(a_cache, ROOT, a_file).get(a_name, "<missing>")
        h_val = py(h_cache, hermes, h_file).get(h_name, "<missing>")
        if path is not None and h_val != "<missing>":
            try:
                for step in path:
                    h_val = h_val[step]
            except (KeyError, IndexError, TypeError):
                h_val = "<missing>"
        if a_val == "<missing>" or h_val == "<missing>" or not _same_value(a_val, h_val):
            found.append((label, a_val, h_val))
    for label, (a_file, a_name), (h_file, h_name) in JS_CONSTANTS:
        a_val = read_js_constant(ROOT / a_file, a_name)
        h_val = read_js_constant(hermes / h_file, h_name)
        if a_val is None or h_val is None or not _same_number(a_val, h_val):
            found.append((label, "<missing>" if a_val is None else a_val, "<missing>" if h_val is None else h_val))
    return found


def _same_value(a, b) -> bool:
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_same_value(a[k], b[k]) for k in a)
    if isinstance(a, (tuple, list)) and isinstance(b, (tuple, list)):
        return len(a) == len(b) and all(_same_value(x, y) for x, y in zip(a, b))
    return _same_number(a, b)


def check(hermes: Path = HERMES):
    """``{"skipped": bool, "differences": [...], "allowed": [...], "stale_allowed": [...]}``."""
    if not (hermes / "settings.py").is_file():
        return {"skipped": True, "differences": [], "allowed": [], "stale_allowed": [], "constants_checked": 0}
    differences, allowed, seen = [], [], set()
    for subject, field, mine, theirs in compare_settings(hermes):
        row = {"kind": "setting", "subject": subject, "field": field, "agent_zero": mine, "hermes": theirs}
        key = ("setting", subject, field)
        (allowed if key in ALLOWED else differences).append(row)
        seen.add(key)
    for label, mine, theirs in compare_constants(hermes):
        row = {"kind": "constant", "subject": label, "field": "value", "agent_zero": mine, "hermes": theirs}
        key = ("constant", label)
        (allowed if key in ALLOWED else differences).append(row)
        seen.add(key)
    stale = sorted(str(key) for key in ALLOWED if key not in seen)
    return {"skipped": False, "differences": differences, "allowed": allowed, "stale_allowed": stale,
            "constants_checked": len(PY_CONSTANTS) + len(JS_CONSTANTS)}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    args = parser.parse_args(argv)
    report = check()
    if args.json:
        print(json.dumps(report, indent=2, default=str, ensure_ascii=False))
    elif report["skipped"]:
        print("parity_check: no Hermes tree next to this folder - nothing to compare against.")
    else:
        print(f"parity_check: {report['constants_checked']} named constants and every setting both ports have")
        for row in report["allowed"]:
            print(f"  allowed  {row['kind']}: {row['subject']} ({row['field']}): {row['agent_zero']!r} vs {row['hermes']!r}")
            print(f"           {ALLOWED[(row['kind'], row['subject'], row['field']) if row['kind'] == 'setting' else (row['kind'], row['subject'])]}")
        for row in report["differences"]:
            print(f"  DIFF     {row['kind']}: {row['subject']} ({row['field']}): Agent Zero {row['agent_zero']!r}, Hermes {row['hermes']!r}")
        for entry in report["stale_allowed"]:
            print(f"  STALE    allowlist entry no longer needed: {entry}")
        print("parity_check: " + ("clean (allowlist only)" if not report["differences"] and not report["stale_allowed"] else "FAILED"))
    return 0 if report["skipped"] or not (report["differences"] or report["stale_allowed"]) else 1


if __name__ == "__main__":
    sys.exit(main())

r"""Grade the Agent Zero port against documented provider errors (1.8.2.7).

    python -I tools/provider_doc_gate.py [--key tests/provider_keys/verdicts-providers.jsonl ...]
                                         [--out report.json] [--only <provider id>] [--quiet]

The answer keys (`tests/provider_keys/verdicts-*.jsonl`) are the ones the
Hermes port is graded on: 82 providers, written by reviewers who never opened
either plugin, from each provider's own documentation. Each row carries the
response a provider documents (status, headers, body) and the verdict it
deserves, in the 1.8.0.0 vocabulary (family / action / window / scope).

For every row this builds the exception LiteLLM hands Agent Zero (``status_code``,
``body``, ``response.headers`` / ``response.text``, ``str(exc)``), runs it
through the decision path the carousel runs (`_is_terminal_error`, then
`_kame_decide_failure`) on a fresh engine state, and maps the result onto the
vocabulary with the same table the Hermes gate uses (copied below; Agent Zero's
kind words are a subset of the Hermes carousel's). Holds are graded only where
the response states a number (``stated_seconds``), capped at one hour.

Isolation: KAME_DATA_DIR goes to a fresh temp dir before the engine is
imported; Agent Zero is stubbed; nothing touches the network.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import types
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
KEYS_DIR = HERE / "tests" / "provider_keys"


# --- the vocabulary table (same as hermes tools/expected_gate.py) ---------------
_KIND_TO_FAMILY = {
    "server": "server", "timeout": "timeout", "auth": "auth_dead", "revoked": "auth_dead",
    "denied": "denial", "insufficient_quota": "billing", "daily": "throttle",
    "per_minute": "throttle", "rate_limit": "throttle", "model_not_ready": "model_not_ready",
}
_KIND_DEFAULT_SCOPE = {
    "daily": "model", "per_minute": "model", "rate_limit": "model", "denied": "model",
    "auth": "credential", "revoked": "credential", "insufficient_quota": "account",
}
_SCOPE_WINDOW_FAMILIES = frozenset({"throttle", "billing"})
_SCOPE_WORD = {"per_model": "model", "model": "model", "account": "account",
               "credential": "credential", "project": "project"}
_WINDOW_WORD = {"per_minute", "tokens_per_minute", "per_hour", "per_day", "per_week", "per_month"}


def map_family(kind, terminal):
    kind = str(kind or "")
    if kind == "other":
        return "request_fault" if terminal else "unknown"
    if terminal and kind not in _KIND_TO_FAMILY:
        return "request_fault"
    return _KIND_TO_FAMILY.get(kind, "unknown")


def map_window(family, kind, window):
    if family == "unknown":
        return "unknown"
    if family != "throttle":
        return "none"
    if window in _WINDOW_WORD:
        return window
    return "per_day" if kind == "daily" else "unknown"


def map_scope(kind, scope):
    if scope and str(scope) in _SCOPE_WORD:
        return _SCOPE_WORD[str(scope)]
    return _KIND_DEFAULT_SCOPE.get(str(kind or ""), "unknown")


def _tolerance(expected):
    return max(2.0, 0.05 * abs(expected))


# --- engine loading (Agent Zero stubbed, as the suite does) ---------------------
def _stub(name):
    module = sys.modules.get(name)
    if module is None:
        module = types.ModuleType(name)
        sys.modules[name] = module
    return module


def load_engine():
    if "kame_engine" in sys.modules:
        return sys.modules["kame_engine"]
    for name in ("openai", "langchain_core", "helpers"):
        _stub(name)
    lit = _stub("litellm")
    lit.suppress_debug_info = False
    lit.acompletion = getattr(lit, "acompletion", lambda *a, **k: None)
    lc = _stub("langchain_core.messages")
    if not hasattr(lc, "SystemMessage"):
        lc.SystemMessage = lc.HumanMessage = type("_Msg", (), {})
    ps = _stub("helpers.print_style")
    if not hasattr(ps, "PrintStyle"):
        ps.PrintStyle = type("_PrintStyle", (), {
            "__init__": lambda self, *a, **k: None, "print": lambda self, *a, **k: None,
            "warning": staticmethod(lambda *a, **k: None), "error": staticmethod(lambda *a, **k: None),
            "success": staticmethod(lambda *a, **k: None)})
    errs = _stub("helpers.errors")
    for name in ("InterventionException", "RepairableException", "HandledException"):
        if not hasattr(errs, name):
            setattr(errs, name, type(name, (Exception,), {}))
    os.environ.setdefault("KAME_DATA_DIR", tempfile.mkdtemp(prefix="kame-a0-doc-gate-"))
    if str(HERE) not in sys.path:
        sys.path.insert(0, str(HERE))
    import kame_engine  # noqa: E402
    return kame_engine


def _fresh(K):
    for name in ("_KAME_KEY_HEALTH", "_KAME_ACCOUNT_HOLDS", "_KAME_STATED_RL", "_KAME_NO_ANSWER_SINCE"):
        store = getattr(K, name, None)
        if hasattr(store, "clear"):
            store.clear()
    try:
        import kame_journal as J
        J._reset_for_tests()
    except Exception:
        pass


# --- the rows ------------------------------------------------------------------
class _Headers(dict):
    """Case-insensitive like ``httpx.Headers``."""

    def __init__(self, data=None):
        super().__init__({str(k).lower(): str(v) for k, v in (data or {}).items()})

    def get(self, key, default=None):
        return super().get(str(key).lower(), default)

    def __getitem__(self, key):
        return super().__getitem__(str(key).lower())

    def __contains__(self, key):
        return super().__contains__(str(key).lower())


def build_exception(row):
    name = str(row.get("exception_type") or "APIStatusError")
    cls = type(name, (Exception,), {})
    status = row.get("status")
    body = row.get("body")
    if isinstance(body, (dict, list)):
        text = json.dumps(body)
        message = f"Error code: {status} - {body!r}" if status is not None else str(row.get("message_template") or text)
    else:
        text = str(body or "")
        message = str(row.get("message_template") or text or name)
    exc = cls(message)
    if isinstance(status, int):
        exc.status_code = status
    if body is not None:
        exc.body = body
    if isinstance(body, dict):
        err = body.get("error") if isinstance(body.get("error"), dict) else body
        code = err.get("code") if isinstance(err, dict) else None
        if isinstance(code, str) and code.strip():
            exc.code = code.strip()
    exc.response = types.SimpleNamespace(text=text, status_code=status, headers=_Headers(row.get("headers") or {}))
    return exc


_LITELLM_CLASS = {400: "BadRequestError", 401: "AuthenticationError", 403: "PermissionDeniedError",
                  404: "NotFoundError", 408: "Timeout", 422: "UnprocessableEntityError",
                  429: "RateLimitError", 500: "InternalServerError", 503: "ServiceUnavailableError"}


def build_litellm_exception(row):
    """The same row as LiteLLM hands it to Agent Zero: a mapped class, the body
    only inside the message (``litellm.<Class>: <Provider>Exception - <text>``)
    and on ``response.text``, no ``.body`` attribute."""
    status = row.get("status")
    name = _LITELLM_CLASS.get(status, "APIError" if isinstance(status, int) else "APIConnectionError")
    cls = type(name, (Exception,), {})
    body = row.get("body")
    text = json.dumps(body) if isinstance(body, (dict, list)) else str(body or row.get("message_template") or "")
    provider = str(row.get("provider") or "x").split(" ", 1)[0].split("(", 1)[0].strip().title().replace("-", "")
    exc = cls(f"litellm.{name}: {provider}Exception - {text}")
    if isinstance(status, int):
        exc.status_code = status
    exc.llm_provider = provider.lower()
    exc.response = types.SimpleNamespace(text=text, status_code=status, headers=_Headers(row.get("headers") or {}))
    return exc


def run_row(K, row, shape="sdk"):
    _fresh(K)
    provider = str(row.get("provider") or "x").split(" ", 1)[0]
    identity = f"{provider}:doc-model"
    exc = build_litellm_exception(row) if shape == "litellm" else build_exception(row)
    with K._KAME_LOCK:
        # A pool of two, as the carousel sees it: an unregistered identity would
        # skip every stored ceiling and report the raw number.
        K._get_identity_state(identity, ["k-doc", "k-doc-2"])
    if K._kame_closed_door(exc):
        # The carousel stops asking keys: the fallback list answers, or the
        # whole pool waits for the door (Agent Zero has no host retry to hand
        # it to; a raise ends the task). Graded as the answer key's "no key
        # helps", which is what it is.
        return {"terminal": True, "kind": "model_not_ready", "hold_s": None, "scope": "", "window": ""}
    if K._is_terminal_error(exc):
        return {"terminal": True, "kind": "other", "hold_s": None, "scope": "", "window": ""}
    seen = {}
    original = K._mark_key_health

    def capture(*args, **kwargs):
        seen.update(scope=kwargs.get("scope", ""), window=kwargs.get("window", ""))
        return original(*args, **kwargs)

    K._mark_key_health = capture
    try:
        applied, kind, _status, _label, _sized = K._kame_decide_failure(identity, "k-doc", exc)
    finally:
        K._mark_key_health = original
    try:
        hold = float(applied) if applied is not None else None
    except (TypeError, ValueError):
        hold = None
    return {"terminal": False, "kind": kind, "hold_s": hold,
            "scope": seen.get("scope") or "", "window": seen.get("window") or ""}


def grade(row, out):
    family = map_family(out["kind"], out["terminal"])
    produced = {
        "family": family,
        "action": "raise_to_host" if out["terminal"] else "rotate_key",
        "window": map_window(family, out["kind"], out["window"]),
        "scope": map_scope(out["kind"], out["scope"]),
    }
    exp_family = str(row.get("family") or "")
    agree = {}
    for field in ("family", "action", "window", "scope"):
        if field in ("window", "scope") and exp_family not in _SCOPE_WINDOW_FAMILIES:
            agree[field] = None
            continue
        expected = row.get(field)
        if field == "action" and expected == "defer_host_retry":
            # Agent Zero has no host-owned recovery to defer to: handing the error
            # back is the same act as raising it.
            expected = "raise_to_host"
        agree[field] = None if expected in (None, "", "unclear") else produced[field] == expected
    stated = row.get("stated_seconds")
    if isinstance(stated, (int, float)) and produced["action"] == "rotate_key":
        expected_hold = min(float(stated), 3600.0)
        agree["rest"] = out["hold_s"] is not None and abs(out["hold_s"] - expected_hold) <= _tolerance(expected_hold)
    else:
        agree["rest"] = None
    return produced, agree


def evaluate(paths=None, only=None, shape="sdk"):
    """Grade every row; returns ``(report, tally)``. Tests call this."""
    K = load_engine()
    previous = getattr(K, "_KAME_LOG_LEVEL", None)
    try:
        K.set_log_level("silent")
    except Exception:
        pass
    paths = list(paths or sorted(KEYS_DIR.glob("verdicts-*.jsonl")))
    report, tally = [], Counter()
    for path in paths:
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if only and not str(row.get("provider", "")).startswith(only):
                continue
            out = run_row(K, row, shape)
            produced, agree = grade(row, out)
            report.append({"key": Path(path).name, "shape_id": row["shape_id"],
                           "expected": {f: row.get(f) for f in ("family", "action", "window", "scope")},
                           "stated_seconds": row.get("stated_seconds"), "produced": produced,
                           "kind": out["kind"], "hold_s": out["hold_s"], "agree": agree})
            for field, ok in agree.items():
                tally[(field, "n/a" if ok is None else ("agree" if ok else "DISAGREE"))] += 1
    _fresh(K)
    if previous is not None:
        try:
            K.set_log_level(previous)
        except Exception:
            pass
    return report, tally


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--key", type=Path, action="append")
    ap.add_argument("--out", type=Path)
    ap.add_argument("--only")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--shape", choices=("sdk", "litellm"), default="sdk",
                    help="sdk: the openai-SDK exception (the Hermes gate's); litellm: what Agent Zero receives")
    args = ap.parse_args(argv)
    report, tally = evaluate(args.key, args.only, args.shape)
    bad = [e for e in report if any(ok is False for ok in e["agree"].values())]
    print(f"{len(report)} documented shapes graded on the carousel's path (_is_terminal_error, _kame_decide_failure)")
    for field in ("family", "action", "window", "scope", "rest"):
        a, d = tally[(field, "agree")], tally[(field, "DISAGREE")]
        pct = 100.0 * a / (a + d) if a + d else 100.0
        print(f"  {field:7} agree={a:4}  DISAGREE={d:3}  n/a={tally[(field, 'n/a')]:4}  ({pct:.2f}%)")
    print(f"\nshapes with a disagreement: {len(bad)}")
    if not args.quiet:
        for e in bad:
            diffs = [f"{f}: expected {e['expected'].get(f) if f != 'rest' else e['stated_seconds']} got "
                     f"{e['produced'].get(f) if f != 'rest' else e['hold_s']}" for f, ok in e["agree"].items() if ok is False]
            print(f"  {e['shape_id']:52} kind={e['kind']:<18} hold={e['hold_s']}  " + "; ".join(diffs))
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

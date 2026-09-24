"""What KAME keeps beyond the current process — v1.8.1.2 (A0 port).

The Hermes port has four instruments this one lacked until 1.8.1.0, and each is
ported here with the same contract, the same file names and the same row shape,
so one reader (``tools/a0_expected_gate.py``, the corpus tools) reads both:

``Events``
    The last 150 decisions, newest first: which key was refused and why, which
    key took over, when the pool waited, when a setting changed. A ring in
    memory, never on disk. ``/kame events`` and the status chip read it.

``refusals.jsonl``
    One line per refusal a provider sent: status, type, code, message, body and
    an allowlisted header block, **redacted before it touches disk**. Decides
    nothing. Stops at 8 MB. Off with ``refusal_recorder_disabled`` /
    ``KAME_RECORDER_DISABLED``.

``calls.jsonl``
    One line per attempt: durations, outcome, rest, a key fingerprint. No key,
    no prompt, no answer text. Stops at 4 MB. Off with
    ``call_timings_disabled`` / ``KAME_CALL_TIMINGS_DISABLED``.

``pool-health.json``
    Holds that survive restarting Agent Zero. Keyed by a hash of the key, never
    the key. A hold read back is never longer than the ceiling allows, and a
    file that is missing, half-written or not this shape reads as "nothing
    known" — memory only, exactly as before 1.8.1.0. Off with
    ``share_pool_health: false`` / ``KAME_SHARE_POOL_HEALTH=0``.

Where the files live: ``usr/plugin-data/api_rotation_by_kame/`` — outside the
plugin directory, because the Plugin Hub updates that directory with
``git pull`` and a data file inside it would be a local change in the way of
every update. ``KAME_DATA_DIR`` overrides it (tests use this).

Framework-free and never raises: every public function sits under
``except Exception``. Writing evidence is never worth the price of a turn.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, Tuple

PLUGIN_NAME = "api_rotation_by_kame"
REFUSALS_FILE = "refusals.jsonl"
CALLS_FILE = "calls.jsonl"
HEALTH_FILE = "pool-health.json"
REFUSALS_CEILING = 8 * 1024 * 1024
CALLS_CEILING = 4 * 1024 * 1024

# Switches. Set by the engine's setters; read on every write.
RECORDER_ON = True
TIMINGS_ON = True
SHARE_HEALTH_ON = True


# ---------------------------------------------------------------------------
# Where
# ---------------------------------------------------------------------------
def data_dir() -> Optional[Path]:
    """``usr/plugin-data/api_rotation_by_kame``, or None when it cannot be named.

    The plugin sits at ``<a0>/usr/plugins/api_rotation_by_kame``; ``usr`` is two
    levels up. Anywhere else (a test, a copy on a desktop) there is no Agent
    Zero to write beside, and None means "write nothing".
    """
    try:
        override = os.environ.get("KAME_DATA_DIR", "").strip()
        if override:
            return Path(override)
        here = Path(__file__).resolve().parent
        if here.parent.name == "plugins" and here.parent.parent.name == "usr":
            return here.parent.parent / "plugin-data" / PLUGIN_NAME
    except Exception:
        pass
    return None


def _path(name: str) -> Optional[Path]:
    folder = data_dir()
    if folder is None:
        return None
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except Exception:
        return None
    return folder / name


def fingerprint(key: str) -> str:
    """The same 6-character id the logs and the chip already show."""
    if not key:
        return "------"
    return "k" + hashlib.sha256(key.encode("utf-8", errors="replace")).hexdigest()[:5]


def _long_hash(key: str) -> str:
    """A longer hash for the health file: six characters collide across pools."""
    return hashlib.sha256(("kame:" + (key or "")).encode("utf-8", errors="replace")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Redaction — port of Hermes core/redact.py and recorder._redact
# ---------------------------------------------------------------------------
_PREFIXED = re.compile(
    r"\b("
    r"AIzaSy[A-Za-z0-9_\-]{4,}"
    r"|sk-[A-Za-z0-9_\-*]{4,}"
    r"|nvapi-[A-Za-z0-9_\-*]{4,}"
    r"|gsk_[A-Za-z0-9_\-*]{4,}"
    r"|xai-[A-Za-z0-9_\-*]{4,}"
    r"|hf_[A-Za-z0-9_\-*]{4,}"
    r"|glpat-[A-Za-z0-9_\-*]{4,}"
    r"|Bearer\s+[A-Za-z0-9._\-]{8,}"
    r")",
    re.I,
)
_LONG_TOKEN = re.compile(
    # quotaId is provider evidence, not an opaque credential. Prefix/field
    # redaction still runs first, even inside this field.
    r'''(?P<quota>["']?\bquotaId["']?\s*[:=]\s*(?:"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|[^\s,;}]+))'''
    r"|\b(?=[A-Za-z0-9_\-]*\d)[A-Za-z0-9_\-]{32,}\b",
    re.I,
)
#: 1.8.1.4, as in the Hermes port: a credential in a URL query parameter
#: (Google's ?key=, and api_key/token elsewhere) is a secret by position.
_QUERY_SECRET = re.compile(
    r"([?&](?:key|api[_-]?key|apikey|access[_-]?token|token|auth)=)[^&\s#\"'<>]+",
    re.I,
)
# 1.8.1.4: `x-api-key` / `x-goog-api-key` are header names that carry the key
# itself; the lookbehind below kept them out when they appeared as JSON fields.
_SECRET_FIELD = re.compile(
    r'''((?<![\w-])["']?(?:(?:x-(?:goog-)?)?api[_-]?key|authorization|access[_-]?token|refresh[_-]?token'''
    r'''|secret|password|token|bearer)["']?\s*[:=]\s*)'''
    r'''(?:"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|(?:Bearer[ \t]+)?[^\s,;&}\]"']+)''',
    re.I,
)
_KEY = re.compile(r"\b(?:AIza|sk-|nvapi-|gsk_|xai-|sk-ant-|hf_)[A-Za-z0-9_\-]{12,}", re.I)
_KEY_FIELD = re.compile(
    r"((?:api[_-]?key|access[_-]?token|authorization|bearer|secret)\s*[=:\"']{1,3}\s*)"
    r"([^\s,;&\"']{8,})",
    re.I,
)


def redact(text: Any, limit: int = 600) -> str:
    """Scrub a payload of anything shaped like a credential, then bound it."""
    try:
        if text is None:
            return ""
        # 1.8.1.2: the same shapes as Hermes' core/redact.py, so both ports'
        # refusals.jsonl read alike: containers as JSON, bytes decoded, any
        # other object by str() — never JSON-quoted.
        if isinstance(text, (bytes, bytearray)):
            text = bytes(text).decode("utf-8", "replace")
        if isinstance(text, (dict, list, tuple)):
            raw = json.dumps(text, ensure_ascii=False, default=str)
        else:
            raw = text if isinstance(text, str) else str(text)
        if raw.lstrip().startswith(("{", "[")):
            try:
                raw = json.dumps(_scrub_fields(json.loads(raw)), ensure_ascii=False, default=str)
            except (ValueError, TypeError):
                pass
        raw = _QUERY_SECRET.sub(lambda m: m.group(1) + "[redacted]", raw)
        raw = _SECRET_FIELD.sub(r'\1"[redacted]"', raw)
        raw = re.sub(r"\bBearer[ \t]+[^\s,;\"'}\]]+", "Bearer [redacted]", raw, flags=re.I)
        raw = _KEY.sub("[redacted]", raw)
        raw = _PREFIXED.sub("[redacted]", raw)
        raw = _LONG_TOKEN.sub(lambda m: m.group(0) if m.group("quota") else "[redacted]", raw)
        raw = raw.strip()
        if limit and len(raw) > limit:
            return raw[:limit].rstrip() + " …"
        return raw
    except Exception:
        return ""


def _redact_corpus(text: str) -> str:
    """Use the same credential scrubber for events and disk evidence."""
    return redact(text, limit=0)


def _scrub_fields(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): "[redacted]" if re.fullmatch(
            r"api[_-]?key|authorization|access[_-]?token|refresh[_-]?token|secret|password|token|cookie|set-cookie",
            str(k), re.I) else _scrub_fields(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_scrub_fields(v) for v in value]
    if isinstance(value, str):
        return redact(value, limit=0)
    return value


# ---------------------------------------------------------------------------
# Events — port of Hermes core/events.py
# ---------------------------------------------------------------------------
MAX_EVENTS = 150
ROTATION = "rotation"
SWITCH = "switch"
QUARANTINE = "quarantine"
INVALID_KEY = "invalid_key"
DENIED_MODEL = "denied_model"
STORM = "storm"
STREAM_DROP = "stream_drop"
WAIT = "wait"
RECOVERY = "recovery"
SURFACED = "surfaced"
SETTING = "setting"
_KINDS = frozenset({ROTATION, SWITCH, QUARANTINE, INVALID_KEY, DENIED_MODEL,
                    STORM, STREAM_DROP, WAIT, RECOVERY, SURFACED, SETTING})
GOOD_KINDS = frozenset({SWITCH, RECOVERY, WAIT, SETTING})


class Events:
    """A fixed-size, thread-safe record of what the carousel decided."""

    def __init__(self, limit: int = MAX_EVENTS) -> None:
        self._lock = threading.Lock()
        self._items: Deque[Dict[str, Any]] = deque(maxlen=max(1, int(limit)))
        self._seq = 0

    def add(self, kind: str, *, identity: str = "", key: str = "",
            reason: str = "", code: Optional[int] = None,
            seconds: Optional[float] = None, at: Optional[float] = None,
            detail: str = "", sized_by: str = "") -> Dict[str, Any]:
        """Record one event. ``key`` must already be a fingerprint; it is
        truncated anyway, so a raw key passed by mistake is useless, not leaked.
        """
        try:
            row = {
                "seq": 0,
                "at": float(time.time() if at is None else at),
                "kind": str(kind if kind in _KINDS else ROTATION),
                "identity": str(identity or ""),
                "key": str(key or "")[:8],
                "reason": str(reason or "")[:120],
                "code": int(code) if isinstance(code, int) and not isinstance(code, bool) else None,
                "seconds": round(float(seconds), 1) if seconds is not None else None,
                "detail": redact(detail) if detail else "",
                "sized_by": str(sized_by or "")[:24],
            }
        except Exception:
            return {}
        with self._lock:
            self._seq += 1
            row["seq"] = self._seq
            self._items.append(row)
            return dict(row)

    def recent(self, limit: int = MAX_EVENTS) -> List[Dict[str, Any]]:
        with self._lock:
            rows = list(self._items)
        rows.reverse()
        return rows[: max(0, int(limit))]

    def clear(self) -> None:
        with self._lock:
            self._items.clear()

    @property
    def total(self) -> int:
        with self._lock:
            return self._seq


EVENTS = Events()


# ---------------------------------------------------------------------------
# refusals.jsonl — port of Hermes recorder.py
# ---------------------------------------------------------------------------
_HEADER_ALLOW = re.compile(
    r"^(?:retry-after(?:-ms)?|date|(?:x-)?request-id|.*(?:rate-?limit|quota|usage)[a-z0-9\-]*)$",
    re.I,
)
_HEADER_DENY_MARKERS = ("authorization", "api-key", "apikey", "cookie", "secret",
                        "credential", "password", "bearer")
_OPAQUE_VALUE = re.compile(r"^[A-Za-z0-9_\-\.]{40,}$")
_HEX_TOKEN = re.compile(r"^[0-9a-f]{24,}$", re.I)
_ADDRESS = re.compile(r"[^@\s]+@[^@\s]+\.[A-Za-z]{2,}")
_refusals_silenced = False
_calls_silenced = False


def _safe_attr(obj: Any, name: str) -> Any:
    try:
        return getattr(obj, name, None)
    except Exception:
        return None


def _raw_headers(error: Any) -> Any:
    headers = _safe_attr(error, "headers")
    if headers is None:
        headers = _safe_attr(error, "response_headers")
    if headers is None:
        response = _safe_attr(error, "response")
        if response is not None:
            headers = _safe_attr(response, "headers")
    return headers


def _header_items(headers: Any) -> List[Tuple[str, Any]]:
    if headers is None:
        return []
    try:
        if hasattr(headers, "items"):
            return [(str(k), v) for k, v in headers.items()]
        if isinstance(headers, (list, tuple)):
            return [(str(k), v) for k, v in headers if k is not None]
    except Exception:
        return []
    return []


def safe_headers(headers: Any) -> Dict[str, str]:
    """Allowlist first, deny second, cap last — as on Hermes."""
    kept: Dict[str, str] = {}
    for name, value in _header_items(headers):
        if len(kept) >= 20:
            break
        clean = str(name or "").strip()[:80]
        if not clean or not _HEADER_ALLOW.match(clean):
            continue
        lowered = clean.lower()
        text = str(value if value is not None else "")
        stripped = text.strip()
        if (any(m in lowered for m in _HEADER_DENY_MARKERS) or _KEY.search(text)
                or _OPAQUE_VALUE.match(stripped) or _HEX_TOKEN.match(stripped)
                or _ADDRESS.search(stripped)):
            continue
        kept[lowered] = _redact_corpus(text)[:200]
    return kept


def _safe_text(value: Any, limit: int = 20000) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        try:
            value = json.dumps(value, ensure_ascii=False, default=str)
        except Exception:
            value = str(value)
    return _redact_corpus(str(value))[:limit]


def _append(name: str, ceiling: int, row: Dict[str, Any]) -> bool:
    """Append one JSON line; False when the ceiling is reached or nothing can be written."""
    path = _path(name)
    if path is None:
        return False
    try:
        if path.exists() and path.stat().st_size >= ceiling:
            return False
    except OSError:
        pass
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    return True


def record_refusal(*, identity: str = "", key: str = "", exc: Any = None,
                   status: Any = None, message: str = "", body: Any = None,
                   kind: str = "", rest: Optional[float] = None,
                   sized_by: str = "") -> None:
    """Write one refusal down. Decides nothing, never raises."""
    global _refusals_silenced
    try:
        if _refusals_silenced or not RECORDER_ON:
            return
        provider, _, model = str(identity or "").partition(":")
        row: Dict[str, Any] = {
            "at": round(time.time(), 3),
            "provider": _safe_text(provider, 256),
            "model": _safe_text(model, 256),
            "status": status if isinstance(status, int) and not isinstance(status, bool) else None,
            "type": type(exc).__name__ if exc is not None else "",
            "code": _safe_text(_safe_attr(exc, "code"), 256) if exc is not None else "",
            "message": _safe_text(message or (str(exc) if exc is not None else "")),
            "body": _safe_text(body if body is not None else _safe_attr(exc, "body")),
            "response": "",
            "host": "agent-zero",
            "key": fingerprint(key),
            "kind": str(kind or ""),
            "rest_s": round(float(rest), 1) if rest is not None else None,
            "sized_by": str(sized_by or ""),
        }
        headers = safe_headers(_raw_headers(exc))
        if headers:
            row["headers"] = headers
        if not _append(REFUSALS_FILE, REFUSALS_CEILING, row):
            if data_dir() is not None:
                _refusals_silenced = True
    except Exception:
        pass


def _ms(seconds: Optional[float]) -> Optional[int]:
    if seconds is None:
        return None
    try:
        value = float(seconds)
    except (TypeError, ValueError):
        return None
    if value != value or value < 0:
        return None
    return int(round(value * 1000.0))


def record_call(*, identity: str = "", key: str = "", attempt: int = 0,
                outcome: str = "", kind: str = "", status: Any = None,
                started_at: Optional[float] = None, ended_at: Optional[float] = None,
                first_sign_at: Optional[float] = None,
                first_text_at: Optional[float] = None,
                elapsed_before_s: Optional[float] = None,
                pool_waited_before_s: Optional[float] = None,
                rest_s: Optional[float] = None, rest_source: str = "",
                call_id: str = "") -> None:
    """One attempt, as durations. Never a key, a prompt or an answer."""
    global _calls_silenced
    try:
        if _calls_silenced or not TIMINGS_ON:
            return
        start = started_at if started_at is not None else ended_at
        row: Dict[str, Any] = {
            "at": round(time.time(), 3),
            "host": "agent-zero",
            "call": str(call_id or ""),
            "identity": str(identity or ""),
            "key": fingerprint(key),
            "attempt": int(attempt or 0),
            "outcome": str(outcome or ""),
            "kind": str(kind or ""),
            "status": status if isinstance(status, int) and not isinstance(status, bool) else None,
            "ms_to_first_sign": _ms(first_sign_at - start) if (first_sign_at and start) else None,
            "ms_to_first_text": _ms(first_text_at - start) if (first_text_at and start) else None,
            "ms_total": _ms(ended_at - start) if (ended_at and start) else None,
            "ms_elapsed_before": _ms(elapsed_before_s),
            "ms_pool_waited_before": _ms(pool_waited_before_s),
        }
        if rest_s is not None:
            row["rest_s"] = round(float(rest_s), 1)
            if rest_source:
                row["rest_source"] = str(rest_source)
        if not _append(CALLS_FILE, CALLS_CEILING, row):
            if data_dir() is not None:
                _calls_silenced = True
    except Exception:
        pass


# ---------------------------------------------------------------------------
# pool-health.json — holds that survive a restart
# ---------------------------------------------------------------------------
_HEALTH_LOCK = threading.Lock()
_HEALTH: Dict[str, Dict[str, Dict[str, Any]]] = {}
_ACCOUNT_HEALTH: Dict[str, Dict[str, Dict[str, Any]]] = {}
_HEALTH_LOADED = False
_HEALTH_DIRTY = False
_HEALTH_LAST_WRITE = 0.0
_HEALTH_WRITE_EVERY_S = 1.0
_HEALTH_MAX_ENTRIES = 4096
_HEALTH_SCHEMA = 2


class ForgetHoldsResult(int):
    """Integer-compatible reset count with persistence status attached."""

    def __new__(cls, value: int, persistence_ok: bool = True, error: str = ""):
        result = int.__new__(cls, int(value))
        result.persistence_ok = bool(persistence_ok)
        result.error = str(error or "")
        return result


def _provider(identity: str) -> str:
    return str(identity or "").split(":", 1)[0]


def _clean_health_row(row: Any, now: float, *, account: bool = False) -> Optional[Dict[str, Any]]:
    """Return one live, bounded-shape row; malformed/expired rows are ignored."""
    if not isinstance(row, dict):
        return None
    try:
        raw_until = row.get("until", 0)
        if isinstance(raw_until, bool):
            return None
        until = float(raw_until)
        if not (until > now and until < float("inf")):
            return None
        raw_at = row.get("at", now)
        at = float(raw_at) if not isinstance(raw_at, bool) else now
        if not (at == at and abs(at) < float("inf")):
            at = now
    except Exception:
        return None
    return {
        "until": until,
        "kind": str(row.get("kind", "") or "")[:24],
        "scope": "account" if account else str(row.get("scope", "") or "")[:12],
        "at": at,
    }


def _remember_row(store: Dict[str, Dict[str, Dict[str, Any]]], outer: str,
                  key_hash: str, row: Dict[str, Any]) -> None:
    """Keep the longest duplicate when schema-1 account rows collapse by provider."""
    rows = store.setdefault(str(outer), {})
    current = rows.get(str(key_hash))
    if current is None or float(row.get("until", 0)) >= float(current.get("until", 0)):
        rows[str(key_hash)] = row


def _load_health() -> None:
    global _HEALTH_LOADED, _HEALTH, _ACCOUNT_HEALTH
    if _HEALTH_LOADED:
        return
    _HEALTH_LOADED = True
    path = _path(HEALTH_FILE)
    if path is None:
        return
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return  # missing, half-written, not JSON: memory only
    if not isinstance(document, dict) or document.get("schema") not in (1, _HEALTH_SCHEMA):
        return
    holds = document.get("holds")
    if not isinstance(holds, dict):
        return
    now = time.time()
    for identity, rows in holds.items():
        if not isinstance(rows, dict):
            continue
        for key_hash, row in rows.items():
            clean = _clean_health_row(
                row,
                now,
                account=isinstance(row, dict) and row.get("scope") == "account",
            )
            if clean is None:
                continue
            if clean["scope"] == "account":
                _remember_row(_ACCOUNT_HEALTH, _provider(str(identity)), str(key_hash), clean)
            else:
                _remember_row(_HEALTH, str(identity), str(key_hash), clean)
    if document.get("schema") == _HEALTH_SCHEMA:
        account_holds = document.get("account_holds", {})
        if isinstance(account_holds, dict):
            for provider, rows in account_holds.items():
                if not isinstance(rows, dict):
                    continue
                for key_hash, row in rows.items():
                    clean = _clean_health_row(row, now, account=True)
                    if clean is not None:
                        _remember_row(_ACCOUNT_HEALTH, str(provider), str(key_hash), clean)


def _flush_health(force: bool = False) -> Tuple[bool, str]:
    global _HEALTH_DIRTY, _HEALTH_LAST_WRITE
    now = time.time()
    if not _HEALTH_DIRTY or (not force and now - _HEALTH_LAST_WRITE < _HEALTH_WRITE_EVERY_S):
        return True, ""
    folder = data_dir()
    if folder is None:
        # No host data directory means the journal is memory-only.
        _HEALTH_DIRTY = False
        return True, ""
    path = _path(HEALTH_FILE)
    if path is None:
        return False, "pool-health data directory is unavailable"
    holds: Dict[str, Dict[str, Dict[str, Any]]] = {}
    account_holds: Dict[str, Dict[str, Dict[str, Any]]] = {}
    count = 0
    for source, target in ((_HEALTH, holds), (_ACCOUNT_HEALTH, account_holds)):
        for outer, rows in source.items():
            live: Dict[str, Dict[str, Any]] = {}
            for key_hash, row in rows.items():
                clean = _clean_health_row(row, now, account=(source is _ACCOUNT_HEALTH))
                if clean is not None:
                    live[str(key_hash)] = clean
            if live:
                target[str(outer)] = live
                count += len(live)
    if count > _HEALTH_MAX_ENTRIES:
        return False, "pool-health entry limit exceeded"
    temporary: Optional[str] = None
    try:
        handle, temporary = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump({"schema": _HEALTH_SCHEMA, "holds": holds,
                       "account_holds": account_holds}, stream, sort_keys=True)
        os.replace(temporary, path)
        _HEALTH_DIRTY = False
        _HEALTH_LAST_WRITE = now
        return True, ""
    except Exception as exc:
        if temporary:
            try:
                os.unlink(temporary)
            except OSError:
                pass
        return False, (f"{type(exc).__name__}: {exc}")[:240]


def note_hold(identity: str, key: str, until: float, kind: str = "",
              scope: str = "") -> None:
    """Remember a hold so the next process sees it. Never raises."""
    global _HEALTH_DIRTY
    try:
        if not SHARE_HEALTH_ON or data_dir() is None:
            return
        with _HEALTH_LOCK:
            _load_health()
            deadline = float(until)
            now = time.time()
            digest = _long_hash(key)
            account = str(scope or "") == "account"
            store = _ACCOUNT_HEALTH if account else _HEALTH
            outer = _provider(identity) if account else str(identity)
            rows = store.setdefault(outer, {})
            if deadline > now and deadline < float("inf"):
                rows[digest] = {
                    "until": deadline,
                    "kind": str(kind or "")[:24],
                    "scope": "account" if account else str(scope or "")[:12],
                    "at": now,
                }
            else:
                rows.pop(digest, None)
            _HEALTH_DIRTY = True
            _flush_health(force=True)
    except Exception:
        pass


def note_answer(identity: str, key: str) -> None:
    """Clear this model hold plus this provider/key account hold, and only those."""
    global _HEALTH_DIRTY
    try:
        if not SHARE_HEALTH_ON or data_dir() is None:
            return
        with _HEALTH_LOCK:
            _load_health()
            digest = _long_hash(key)
            model_rows = _HEALTH.get(str(identity)) or {}
            account_rows = _ACCOUNT_HEALTH.get(_provider(identity)) or {}
            changed = digest in model_rows or digest in account_rows
            model_rows.pop(digest, None)
            account_rows.pop(digest, None)
            if changed:
                # Written at once: a release lost to a restart would keep a
                # working key out for up to the ceiling. Rare - only a key
                # that was actually held gets here.
                _HEALTH_DIRTY = True
                _flush_health(force=True)
    except Exception:
        pass


def hold_for(identity: str, key: str) -> Optional[Dict[str, Any]]:
    """Effective hold plus independent ``model`` and ``account`` components."""
    global _HEALTH_DIRTY
    try:
        if not SHARE_HEALTH_ON or data_dir() is None:
            return None
        with _HEALTH_LOCK:
            _load_health()
            now = time.time()
            digest = _long_hash(key)
            model_rows = _HEALTH.get(str(identity)) or {}
            account_rows = _ACCOUNT_HEALTH.get(_provider(identity)) or {}
            model = _clean_health_row(model_rows.get(digest), now)
            account = _clean_health_row(account_rows.get(digest), now, account=True)
            if model is None and digest in model_rows:
                model_rows.pop(digest, None)
                _HEALTH_DIRTY = True
            if account is None and digest in account_rows:
                account_rows.pop(digest, None)
                _HEALTH_DIRTY = True
            if model is None and account is None:
                return None
            effective = model
            if effective is None or (account is not None and account["until"] > effective["until"]):
                effective = account
            result = dict(effective or {})
            result["model"] = dict(model) if model is not None else None
            result["account"] = dict(account) if account is not None else None
            return result
    except Exception:
        pass
    return None


def forget_holds() -> ForgetHoldsResult:
    """Drop every hold; return an int-compatible count plus persistence status."""
    global _HEALTH_DIRTY
    try:
        with _HEALTH_LOCK:
            _load_health()
            count = (sum(len(r) for r in _HEALTH.values())
                     + sum(len(r) for r in _ACCOUNT_HEALTH.values()))
            _HEALTH.clear()
            _ACCOUNT_HEALTH.clear()
            _HEALTH_DIRTY = True
            ok, error = _flush_health(force=True)
            return ForgetHoldsResult(count, ok, error)
    except Exception as exc:
        return ForgetHoldsResult(0, False, (f"{type(exc).__name__}: {exc}")[:240])


def _reset_for_tests() -> None:
    global _HEALTH_LOADED, _HEALTH_DIRTY, _HEALTH_LAST_WRITE, _refusals_silenced, _calls_silenced
    with _HEALTH_LOCK:
        _HEALTH.clear()
        _ACCOUNT_HEALTH.clear()
        _HEALTH_LOADED = False
        _HEALTH_DIRTY = False
        _HEALTH_LAST_WRITE = 0.0
    _refusals_silenced = False
    _calls_silenced = False
    EVENTS.clear()

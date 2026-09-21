"""What KAME keeps beyond the current process — v1.8.1.0 (A0 port).

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
_LONG_TOKEN = re.compile(r"\b(?=[A-Za-z0-9_\-]*\d)[A-Za-z0-9_\-]{32,}\b")
_SECRET_FIELD = re.compile(
    r'("(?:api[_-]?key|apikey|authorization|access[_-]?token|refresh[_-]?token'
    r'|secret|password|token)"\s*:\s*)"[^"]*"',
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
        raw = text if isinstance(text, str) else str(text)
        raw = _SECRET_FIELD.sub(r'\1"[redacted]"', raw)
        raw = _PREFIXED.sub("[redacted]", raw)
        raw = _LONG_TOKEN.sub("[redacted]", raw)
        raw = raw.strip()
        if limit and len(raw) > limit:
            return raw[:limit].rstrip() + " …"
        return raw
    except Exception:
        return ""


def _redact_corpus(text: str) -> str:
    """The recorder's redaction — same as Hermes', so both corpora read alike."""
    text = _KEY.sub("<KEY>", text)
    return _KEY_FIELD.sub(lambda m: m.group(1) + "<KEY>", text)


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
            "provider": provider,
            "model": model,
            "status": status if isinstance(status, int) and not isinstance(status, bool) else None,
            "type": type(exc).__name__ if exc is not None else "",
            "code": str(_safe_attr(exc, "code") or "") if exc is not None else "",
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
_HEALTH_LOADED = False
_HEALTH_DIRTY = False
_HEALTH_LAST_WRITE = 0.0
_HEALTH_WRITE_EVERY_S = 1.0
_HEALTH_MAX_ENTRIES = 4096


def _load_health() -> None:
    global _HEALTH_LOADED, _HEALTH
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
    if not isinstance(document, dict) or document.get("schema") != 1:
        return
    holds = document.get("holds")
    if not isinstance(holds, dict):
        return
    now = time.time()
    for identity, rows in holds.items():
        if not isinstance(rows, dict):
            continue
        for key_hash, row in rows.items():
            try:
                until = float(row.get("until", 0))
            except Exception:
                continue
            if until > now:
                _HEALTH.setdefault(str(identity), {})[str(key_hash)] = {
                    "until": until,
                    "kind": str(row.get("kind", ""))[:24],
                    "scope": str(row.get("scope", ""))[:12],
                    "at": float(row.get("at", now) or now),
                }


def _flush_health(force: bool = False) -> None:
    global _HEALTH_DIRTY, _HEALTH_LAST_WRITE
    now = time.time()
    if not _HEALTH_DIRTY or (not force and now - _HEALTH_LAST_WRITE < _HEALTH_WRITE_EVERY_S):
        return
    path = _path(HEALTH_FILE)
    if path is None:
        _HEALTH_DIRTY = False
        return
    holds = {}
    count = 0
    for identity, rows in _HEALTH.items():
        live = {h: r for h, r in rows.items() if float(r.get("until", 0)) > now}
        if live:
            holds[identity] = live
            count += len(live)
    if count > _HEALTH_MAX_ENTRIES:
        return
    handle, temporary = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump({"schema": 1, "holds": holds}, stream, sort_keys=True)
        os.replace(temporary, path)
        _HEALTH_DIRTY = False
        _HEALTH_LAST_WRITE = now
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass


def note_hold(identity: str, key: str, until: float, kind: str = "",
              scope: str = "") -> None:
    """Remember a hold so the next process sees it. Never raises."""
    global _HEALTH_DIRTY
    try:
        if not SHARE_HEALTH_ON or data_dir() is None:
            return
        with _HEALTH_LOCK:
            _load_health()
            _HEALTH.setdefault(str(identity), {})[_long_hash(key)] = {
                "until": float(until), "kind": str(kind or "")[:24],
                "scope": str(scope or "")[:12], "at": time.time(),
            }
            _HEALTH_DIRTY = True
            _flush_health(force=True)
    except Exception:
        pass


def note_answer(identity: str, key: str) -> None:
    """A key answered: its holds on this identity (and account holds) are gone."""
    global _HEALTH_DIRTY
    try:
        if not SHARE_HEALTH_ON or data_dir() is None:
            return
        with _HEALTH_LOCK:
            _load_health()
            digest = _long_hash(key)
            provider = str(identity).split(":", 1)[0]
            changed = False
            for other, rows in _HEALTH.items():
                row = rows.get(digest)
                if row is None:
                    continue
                if other == identity or (str(other).split(":", 1)[0] == provider
                                         and row.get("scope") == "account"):
                    rows.pop(digest, None)
                    changed = True
            if changed:
                # Written at once: a release lost to a restart would keep a
                # working key out for up to the ceiling. Rare - only a key
                # that was actually held gets here.
                _HEALTH_DIRTY = True
                _flush_health(force=True)
    except Exception:
        pass


def hold_for(identity: str, key: str) -> Optional[Dict[str, Any]]:
    """A remembered hold still running for this key on this identity, or None."""
    try:
        if not SHARE_HEALTH_ON or data_dir() is None:
            return None
        with _HEALTH_LOCK:
            _load_health()
            row = (_HEALTH.get(str(identity)) or {}).get(_long_hash(key))
            if row and float(row.get("until", 0)) > time.time():
                return dict(row)
    except Exception:
        pass
    return None


def forget_holds() -> int:
    """Drop every remembered hold (``/kame-quota reset``). Returns how many."""
    global _HEALTH_DIRTY
    try:
        with _HEALTH_LOCK:
            _load_health()
            count = sum(len(r) for r in _HEALTH.values())
            _HEALTH.clear()
            _HEALTH_DIRTY = True
            _flush_health(force=True)
            return count
    except Exception:
        return 0


def _reset_for_tests() -> None:
    global _HEALTH_LOADED, _HEALTH_DIRTY, _HEALTH_LAST_WRITE, _refusals_silenced, _calls_silenced
    with _HEALTH_LOCK:
        _HEALTH.clear()
        _HEALTH_LOADED = False
        _HEALTH_DIRTY = False
        _HEALTH_LAST_WRITE = 0.0
    _refusals_silenced = False
    _calls_silenced = False
    EVENTS.clear()

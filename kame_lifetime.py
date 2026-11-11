"""What KAME did over time, and what the provider itself says about a day (1.8.4.0, E5 and E6).

Display only. No selection, rest or wait anywhere reads a number from this module.

Two things are kept in one small file, ``lifetime.json``:

* **E5, lifetime counters** - ``replies`` (a chat reply answered), ``saved`` (a chat reply
  answered after at least one failed attempt in that call) and the two switch counters. A
  background job is never counted. The same definition as ``audit/.../probes/time_lost.py``.
* **E6, the last 24 hours** - requests sent per identity in hourly buckets, pruned past 25
  hours, and the provider's own stated daily limit per identity, latest wins.

The file lives beside the plugin's other data (``usr/plugin-data``). ``Clear
pool``, reset and ``Clear events`` do not touch it, and nothing in it leaves the machine: no
key, no fingerprint of a key, no text of a conversation - counters, identities
(``provider:model``) and epochs only.

**Nothing here raises.** A storage failure loses the count quietly: this is a readout, and a
readout must never cost a reply.

Several processes may load the plugin. Each
keeps its increments apart from what the file held and writes them *added to* the file's
current content, so two writers do not overwrite each other's counts.
"""

from __future__ import annotations

import atexit
import json
import logging
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional

try:
    from usr.plugins.api_rotation_by_kame.kame_evidence import stated_daily_limit
except Exception:
    from kame_evidence import stated_daily_limit  # running from the plugin directory, e.g. in tests

logger = logging.getLogger(__name__)

__all__ = ["Lifetime", "LIFETIME"]

#: Written at most this often (plus once at a clean exit).
WRITE_EVERY_S = 60.0
#: Hourly buckets older than this are dropped (24 h shown, one hour of slack).
KEEP_HOURS = 25
_COUNTERS = ("replies", "saved", "key_switches", "model_switches")


def _empty() -> Dict[str, Any]:
    return {"since": 0.0, "replies": 0, "saved": 0, "key_switches": 0, "model_switches": 0,
            "sent": {}, "limits": {}}


def _clean(raw: Any) -> Dict[str, Any]:
    """A document read from disk, made safe to add to. Anything unreadable becomes empty."""
    out = _empty()
    if not isinstance(raw, dict):
        return out
    try:
        out["since"] = float(raw.get("since") or 0.0)
    except (TypeError, ValueError):
        out["since"] = 0.0
    for name in _COUNTERS:
        try:
            out[name] = max(0, int(raw.get(name) or 0))
        except (TypeError, ValueError):
            out[name] = 0
    sent = raw.get("sent")
    if isinstance(sent, dict):
        for identity, hours in sent.items():
            if not isinstance(identity, str) or not isinstance(hours, dict):
                continue
            row: Dict[str, int] = {}
            for hour, count in hours.items():
                try:
                    row[str(int(hour))] = max(0, int(count))
                except (TypeError, ValueError):
                    continue
            if row:
                out["sent"][identity] = row
    limits = raw.get("limits")
    if isinstance(limits, dict):
        for identity, row in limits.items():
            if not isinstance(identity, str) or not isinstance(row, dict):
                continue
            try:
                value = int(row.get("value"))
                at = float(row.get("at") or 0.0)
            except (TypeError, ValueError):
                continue
            scope = row.get("scope")
            if value > 0 and scope in ("project", "key"):
                out["limits"][identity] = {"value": value, "scope": scope, "at": at}
    return out


def _default_path() -> Optional[Path]:
    """``lifetime.json`` in the plugin data folder, or None when there is no home."""
    try:
        try:
            from usr.plugins.api_rotation_by_kame import kame_journal as journal
        except Exception:
            import kame_journal as journal  # running from the plugin directory, e.g. in tests
        directory = journal.data_dir()
        return None if directory is None else Path(directory) / "lifetime.json"
    except Exception:
        return None


class Lifetime:
    """The counters, in memory, with their increments written through now and then."""

    def __init__(self, path: Optional[Callable[[], Optional[Path]]] = None,
                 clock: Callable[[], float] = time.time) -> None:
        self._path_fn = path or _default_path
        self._clock = clock
        self._lock = threading.RLock()
        self._base: Optional[Dict[str, Any]] = None     # what the file held when last read or written
        self._signed: Optional[tuple] = None            # the file's (mtime_ns, size) when _base was taken
        self._delta = _empty()                          # what this process added since
        self._delta_limits: Dict[str, Dict[str, Any]] = {}
        self._last_write = 0.0
        self._dirty = False
        self.writes = 0
        self._registered = False

    # -- reading the file ---------------------------------------------------------

    def _read(self) -> Dict[str, Any]:
        """The file's content. Corrupt means empty; a file that cannot be opened right now raises.

        The two are kept apart on purpose. Another process can hold the file for a moment, and
        reading that as empty would make the next write replace every count with this process's.
        """
        path = self._path_fn()
        if path is None or not Path(path).exists():
            return _empty()
        try:
            return _clean(json.loads(Path(path).read_text(encoding="utf-8")))
        except ValueError:
            return _empty()

    def _signature(self) -> Optional[tuple]:
        """``(st_mtime_ns, st_size)`` of the file, or None when there is no path or no file."""
        try:
            path = self._path_fn()
            if path is None:
                return None
            stat = os.stat(path)
            return (stat.st_mtime_ns, stat.st_size)
        except Exception:
            return None

    def _loaded(self) -> Dict[str, Any]:
        # 1.8.4.1 (K3): the file was read once and kept, so a process that counted nothing itself
        # showed old numbers for as long as it ran (the module header names several processes per
        # profile). One ``os.stat`` per call tells whether another process wrote since; this
        # process's own increments are never in the file it reads, so nothing is counted twice.
        signature = self._signature()
        if self._base is not None and signature == self._signed:
            return self._base
        try:
            base = self._read()
        except Exception:
            # Keep the last good numbers; the new signature is not kept, so the next call tries again.
            return self._base if self._base is not None else _empty()
        self._base, self._signed = base, signature
        return base

    # -- counting (E5) ------------------------------------------------------------

    def reply(self, saved: bool = False, key_switches: int = 0, model_switches: int = 0) -> None:
        """A chat reply was answered. Background jobs are never passed here."""
        try:
            with self._lock:
                self._delta["replies"] += 1
                if saved:
                    self._delta["saved"] += 1
                self._delta["key_switches"] += max(0, int(key_switches))
                self._delta["model_switches"] += max(0, int(model_switches))
                self._touch()
        except Exception:  # pragma: no cover - a readout must never cost a reply
            logger.debug("kame: lifetime reply not counted", exc_info=True)

    # -- counting (E6) ------------------------------------------------------------

    def sent(self, identity: str, now: Optional[float] = None) -> None:
        """One request went out for ``identity`` (a backup or a race leg counts as sent)."""
        try:
            if not identity:
                return
            now = self._clock() if now is None else now
            hour = str(int(now // 3600))
            with self._lock:
                row = self._delta["sent"].setdefault(str(identity), {})
                row[hour] = row.get(hour, 0) + 1
                self._touch()
        except Exception:  # pragma: no cover
            logger.debug("kame: lifetime request not counted", exc_info=True)

    def note_limit(self, identity: str, body: Any, now: Optional[float] = None) -> None:
        """Keep the daily limit a refusal body states for ``identity``; the latest wins."""
        try:
            stated = stated_daily_limit(body)
            if stated is None or not identity:
                return
            now = self._clock() if now is None else now
            with self._lock:
                self._delta_limits[str(identity)] = {"value": stated[0], "scope": stated[1], "at": now}
                self._touch()
        except Exception:  # pragma: no cover
            logger.debug("kame: lifetime limit not kept", exc_info=True)

    # -- reading (display) --------------------------------------------------------

    def snapshot(self, now: Optional[float] = None) -> Dict[str, Any]:
        """The totals: ``since``, the four counters, and per-identity ``sent_24h`` and limits."""
        try:
            now = self._clock() if now is None else now
            with self._lock:
                merged = self._merged(self._loaded(), now)
            hours_from = int(now // 3600) - 23
            sent_24h = {identity: sum(n for h, n in row.items() if int(h) >= hours_from)
                        for identity, row in merged["sent"].items()}
            return {
                "since": merged["since"] or None,
                **{name: merged[name] for name in _COUNTERS},
                "sent_24h": {k: v for k, v in sent_24h.items() if v},
                "limits": {k: dict(v) for k, v in merged["limits"].items()},
            }
        except Exception:
            return {"since": None, **{name: 0 for name in _COUNTERS}, "sent_24h": {}, "limits": {}}

    def view(self, identity: str, now: Optional[float] = None) -> Dict[str, Any]:
        """``sent_24h``, ``daily_limit`` and ``daily_scope`` for one pool (None when not stated)."""
        snap = self.snapshot(now)
        limit = snap["limits"].get(identity)
        return {"sent_24h": int(snap["sent_24h"].get(identity, 0)),
                "daily_limit": limit["value"] if limit else None,
                "daily_scope": limit["scope"] if limit else None}

    # -- writing ------------------------------------------------------------------

    def _merged(self, base: Dict[str, Any], now: float) -> Dict[str, Any]:
        """``base`` plus this process's increments, pruned. Neither input is changed."""
        out = _empty()
        out["since"] = base["since"] or self._delta["since"]
        for name in _COUNTERS:
            out[name] = base[name] + self._delta[name]
        floor = int(now // 3600) - KEEP_HOURS
        for source in (base["sent"], self._delta["sent"]):
            for identity, row in source.items():
                target = out["sent"].setdefault(identity, {})
                for hour, count in row.items():
                    if int(hour) >= floor:
                        target[hour] = target.get(hour, 0) + count
        out["sent"] = {k: v for k, v in out["sent"].items() if v}
        out["limits"] = {k: dict(v) for k, v in base["limits"].items()}
        for identity, row in self._delta_limits.items():
            held = out["limits"].get(identity)
            if held is None or row["at"] >= held.get("at", 0.0):
                out["limits"][identity] = dict(row)
        return out

    def _touch(self) -> None:
        """Called under the lock after a change: register the exit write, write if it is time."""
        self._dirty = True
        if not self._delta["since"]:
            self._delta["since"] = self._clock()
        if not self._registered:
            self._registered = True
            try:
                atexit.register(self.flush, True)
            except Exception:  # pragma: no cover
                pass
        if self._clock() - self._last_write >= WRITE_EVERY_S:
            self.flush()

    def flush(self, force: bool = False) -> bool:
        """Write the increments added to the file's current content. Atomic. False = nothing written."""
        try:
            with self._lock:
                if not self._dirty:
                    return False
                now = self._clock()
                if not force and now - self._last_write < WRITE_EVERY_S:
                    return False
                path = self._path_fn()
                if path is None:
                    self._last_write = now      # no home: try again in a minute, not on every change
                    return False
                path = Path(path)
                merged = self._merged(self._read(), now)
                path.parent.mkdir(parents=True, exist_ok=True)
                descriptor, temporary = tempfile.mkstemp(dir=str(path.parent), prefix=".lifetime-", suffix=".tmp")
                try:
                    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                        json.dump(merged, handle, separators=(",", ":"), sort_keys=True)
                    # 1.8.4.1 (K3): the signature of the bytes THIS process wrote, taken before the
                    # rename (a rename keeps the time and the size). Taken after it, another
                    # process's write in between would be signed as ours and stay unread.
                    written = os.stat(temporary)
                    signed = (written.st_mtime_ns, written.st_size)
                    os.replace(temporary, path)
                except BaseException:
                    try:
                        os.unlink(temporary)
                    except OSError:
                        pass
                    raise
                self._base = merged
                self._signed = signed
                self._delta = _empty()
                self._delta_limits = {}
                self._dirty = False
                self._last_write = now
                self.writes += 1
                return True
        except Exception:
            # Lose the count quietly; try again after the next interval, not on every change.
            self._last_write = self._clock()
            logger.debug("kame: lifetime not written", exc_info=True)
            return False

    def reset_for_tests(self) -> None:
        with self._lock:
            self._base, self._delta, self._delta_limits = None, _empty(), {}
            self._signed = None
            self._last_write, self._dirty, self.writes = 0.0, False, 0


LIFETIME = Lifetime()

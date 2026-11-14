"""Remembered trouble: which models of the user's list keep failing (1.8.4.2, S1a; the Agent Zero twin of
Hermes ``core/model_health.py``).

The busy windows in ``kame_engine`` live in memory and last minutes; the owner's pauses last hours and
every restart starts clean, so the first reply after a pause was sent to the same model that had been
failing all week and waited for it. This module keeps one small record per model - its last judged
outcomes - across processes and restarts, so the first reply can start on a model that answers while
the one that keeps failing is still asked beside it.

The rule is evidence, never identity: a model is "risky" when its latest judged outcome is a failure
and at least ``MIN_FAILS`` of its last ``MAX_EVENTS`` outcomes within ``MAX_AGE_S`` are failures. Only
the provider saying busy, or making the user wait past the race trigger, counts as a failure; a 429, a
daily quota, a bad request, an auth error or a stop is neutral and never reaches this module.

Nothing here may cost a reply: every public method swallows its own errors. ``KAME_NO_REMEMBERED_TROUBLE=1``
turns the whole thing off (``risky`` is always False and nothing is written).
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
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)

MAX_EVENTS = 10
MAX_AGE_S = 7 * 86400.0
MIN_FAILS = 2
#: 1.8.4.3 (A10): a model left out as failing is looked at again after its latest failure + RETEST_FIRST_S x 2^(k-1)
#: (k = trailing failures), at most RETEST_MAX_S.
RETEST_FIRST_S = 600.0
RETEST_MAX_S = 7200.0
FILE_NAME = "model-health.json"
SCHEMA = 1
ENV_OFF = "KAME_NO_REMEMBERED_TROUBLE"
#: At most one write per this many seconds. Every write runs on a timer thread, never on the thread that noted
#: the outcome: a reply never waits for the disk, and here that thread is Agent Zero's event loop (review: one
#: write took 18 ms median and 163 ms at worst on the owner's PC, measured on the Hermes twin).
SAVE_EVERY_S = 2.0

_TRUE = ("1", "true", "yes", "on")

Event = Tuple[float, int]


def disabled() -> bool:
    """The environment kill switch, read fresh on every call."""
    return os.environ.get(ENV_OFF, "").strip().lower() in _TRUE


def _clean(events: Iterable[Any], cleared_at: float = 0.0) -> List[Event]:
    """Union by ``at`` (a success beats a failure at the same instant), sorted, malformed rows dropped."""
    by_at: Dict[float, int] = {}
    for row in events:
        try:
            at = round(float(row[0]), 3)
            ok = 1 if int(row[1]) else 0
        except (TypeError, ValueError, IndexError, KeyError):
            continue
        if at <= cleared_at:
            continue
        by_at[at] = max(by_at.get(at, 0), ok)
    return sorted(by_at.items())


class ModelHealth:
    """Per-model recent outcomes, kept in ``model-health.json`` (see the module docstring)."""

    def __init__(self, directory: Optional[Any] = None, calls_path: Optional[Any] = None) -> None:
        self._lock = threading.RLock()
        self._io_lock = threading.Lock()       # one writer at a time; never taken while ``_lock`` is held
        self._directory_override = Path(directory) if directory is not None else None
        self._calls_override = calls_path
        self._events: Dict[str, List[Event]] = {}
        self._cleared: Dict[str, float] = {}
        self._last_call: Dict[str, str] = {}
        self._loaded = False
        self._loaded_path: Optional[Path] = None
        self._sig: Optional[Tuple[int, int]] = None
        self._dirty = False
        self._last_save = -1e9
        self._timer: Optional[threading.Timer] = None
        self.writes = 0     # files written, for tests and the doctor

    # -- location -------------------------------------------------------------

    def configure(self, directory: Optional[Any] = None, calls_path: Optional[Any] = None) -> None:
        """Point at another directory (tests); forgets everything held in memory."""
        with self._lock:
            self._cancel_timer()
            self._directory_override = Path(directory) if directory is not None else None
            self._calls_override = calls_path
            self._events.clear()
            self._cleared.clear()
            self._last_call.clear()
            self._loaded = False
            self._loaded_path = None
            self._sig = None
            self._dirty = False
            self._last_save = -1e9

    def _directory(self) -> Optional[Path]:
        if self._directory_override is not None:
            return self._directory_override
        # the plugin's own data directory: where calls.jsonl, pool-health.json and no-explicit-cache.json live
        try:
            try:
                from usr.plugins.api_rotation_by_kame import kame_journal as journal
            except Exception:
                import kame_journal as journal  # running from the plugin directory, e.g. in tests
            directory = journal.data_dir()
            return Path(directory) if directory is not None else None
        except Exception:
            logger.debug("kame: could not resolve the plugin data directory", exc_info=True)
        return None

    def _file(self) -> Optional[Path]:
        directory = self._directory()
        return None if directory is None else directory / FILE_NAME

    def _calls_files(self) -> List[Path]:
        """The journal's call rows, oldest generation first (``calls.1.jsonl`` then ``calls.jsonl``)."""
        if self._calls_override is not None:
            path = Path(self._calls_override)
            return [path] if path.is_file() else []
        directory = self._directory()
        if directory is None:
            return []
        out: List[Path] = []
        for name in ("calls.1.jsonl", "calls.jsonl"):
            path = directory / name
            try:
                if path.is_file():
                    out.append(path)
            except OSError:
                pass
        return out

    # -- file -----------------------------------------------------------------

    @staticmethod
    def _stat(path: Path) -> Optional[Tuple[int, int]]:
        try:
            st = path.stat()
            return (st.st_mtime_ns, st.st_size)
        except OSError:
            return None

    @staticmethod
    def _read_document(path: Path) -> Tuple[Dict[str, List[Event]], Dict[str, float]]:
        try:
            with open(path, encoding="utf-8") as handle:
                document = json.load(handle)
            if not isinstance(document, dict) or document.get("schema") != SCHEMA:
                return {}, {}
            models = document.get("models")
            cleared_doc = document.get("cleared")
            cleared: Dict[str, float] = {}
            if isinstance(cleared_doc, dict):
                for ident, at in cleared_doc.items():
                    try:
                        cleared[str(ident)] = float(at)
                    except (TypeError, ValueError):
                        continue
            out: Dict[str, List[Event]] = {}
            if isinstance(models, dict):
                for ident, rows in models.items():
                    if isinstance(rows, list):
                        out[str(ident)] = _clean(rows, cleared.get(str(ident), 0.0))
            return out, cleared
        except FileNotFoundError:
            return {}, {}
        except Exception:
            logger.debug("kame: model-health.json is not readable; treated as empty", exc_info=True)
            return {}, {}

    def _merge_into_memory(self, models: Dict[str, List[Event]], cleared: Dict[str, float]) -> None:
        for ident, at in cleared.items():
            if at > self._cleared.get(ident, 0.0):
                self._cleared[ident] = at
        for ident in set(self._events) | set(models):
            merged = _clean(list(self._events.get(ident, [])) + list(models.get(ident, [])),
                            self._cleared.get(ident, 0.0))
            merged = merged[-MAX_EVENTS:]
            if merged:
                self._events[ident] = merged
            else:
                self._events.pop(ident, None)

    def _document(self) -> Dict[str, Any]:
        """What the file holds, built from memory. Lock held."""
        now = time.time()
        models = {ident: [[at, ok] for at, ok in events if now - at <= MAX_AGE_S or at > now][-MAX_EVENTS:]
                  for ident, events in self._events.items()}
        models = {ident: rows for ident, rows in models.items() if rows}
        document: Dict[str, Any] = {"schema": SCHEMA, "models": models}
        if self._cleared:
            document["cleared"] = {ident: at for ident, at in self._cleared.items() if now - at <= MAX_AGE_S}
        return document

    @staticmethod
    def _put(path: Path, document: Dict[str, Any]) -> bool:
        """Write ``document`` atomically. No lock: this is the disk."""
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            handle, temp = tempfile.mkstemp(prefix=FILE_NAME + ".", suffix=".tmp", dir=str(path.parent))
            try:
                with os.fdopen(handle, "w", encoding="utf-8") as out:
                    json.dump(document, out, separators=(",", ":"))
                os.replace(temp, path)
            except BaseException:
                try:
                    os.unlink(temp)
                except OSError:
                    pass
                raise
            return True
        except Exception:
            logger.debug("kame: could not write model-health.json; kept in memory", exc_info=True)
            return False

    def _write(self, path: Path) -> bool:
        """Merge with what is on disk and write atomically.

        Review: the disk is touched with the memory lock RELEASED - the reply reads the memory at its start
        (``risky``), and a write holding the lock made that read wait for the disk (on the Hermes twin a stress run
        of the storm test lost its 0.15 s start 2 times in 6, never on 1.8.4.1). ``_io_lock`` keeps one writer at
        a time; nothing that holds ``_lock`` ever takes it."""
        with self._io_lock:
            with self._lock:
                if path != self._loaded_path:
                    return False        # the data directory moved (configure, a test)
            on_disk, cleared = self._read_document(path)
            with self._lock:
                if path != self._loaded_path:
                    return False
                self._merge_into_memory(on_disk, cleared)
                document = self._document()
                self._dirty = False     # a note from now on is dirty again and schedules its own write
            ok = self._put(path, document)
            sig = self._stat(path) if ok else None
            with self._lock:
                if ok and path == self._loaded_path:
                    self._sig = sig
                    self.writes += 1
                self._last_save = time.monotonic()  # a failed write is not retried on every note
            return ok

    def _seed(self) -> None:
        """First run with no file: learn from the journal's ``calls.jsonl`` (S1f). Called with the lock held."""
        files = self._calls_files()
        if not files:
            return
        now = time.time()
        rows_read = 0
        seeded: Dict[str, List[Event]] = {}
        # review: one event per (identity, call) here too, as ``note`` keeps it live - three keys refused in
        # one reply are one failure, and a reply that answered after a refusal is one success.
        per_call: Dict[Tuple[str, str], int] = {}
        for each in files:
            try:
                with open(each, encoding="utf-8", errors="replace") as handle:
                    for line in handle:
                        try:
                            row = json.loads(line)
                        except ValueError:
                            continue
                        if not isinstance(row, dict):
                            continue
                        identity = row.get("identity")
                        at = row.get("at")
                        if not identity or not isinstance(at, (int, float)) or now - at > MAX_AGE_S:
                            continue
                        if row.get("outcome") == "ok":
                            ok = 1
                        elif row.get("kind") in ("server", "timeout"):
                            ok = 0
                        else:
                            continue
                        rows_read += 1
                        events = seeded.setdefault(str(identity), [])
                        call_id = row.get("call")
                        slot = (str(identity), str(call_id)) if call_id else None
                        if slot is not None and slot in per_call:
                            index = per_call[slot]
                            events[index] = (events[index][0], max(events[index][1], ok))
                            continue
                        if slot is not None:
                            per_call[slot] = len(events)
                        events.append((round(float(at), 3), ok))
            except OSError:
                logger.debug("kame: could not read call timings to seed remembered trouble", exc_info=True)
        if not seeded:
            return
        for ident, events in seeded.items():
            self._events[ident] = _clean(events)[-MAX_EVENTS:]
        logger.info("kame: remembered trouble seeded for %d models from %d rows", len(self._events), rows_read)

    def ensure_loaded(self) -> None:
        """Lazy first load; re-read when another process changed the file."""
        try:
            if disabled():
                return
            with self._lock:
                path = self._file()
                if self._loaded and path != self._loaded_path:
                    # another data directory (a test, a moved data directory): nothing carries over
                    self._cancel_timer()
                    self._events.clear()
                    self._cleared.clear()
                    self._last_call.clear()
                    self._loaded = False
                    self._sig = None
                    self._dirty = False
                    self._last_save = -1e9
                self._loaded_path = path
                if path is None:
                    self._loaded = True
                    return
                sig = self._stat(path)
                if not self._loaded:
                    self._loaded = True
                    if sig is None:
                        self._seed()
                        if self._events:
                            self._dirty = True
                            self._maybe_save()
                    else:
                        models, cleared = self._read_document(path)
                        self._merge_into_memory(models, cleared)
                        self._sig = sig
                elif sig is not None and sig != self._sig:
                    models, cleared = self._read_document(path)
                    self._merge_into_memory(models, cleared)
                    self._sig = sig
        except Exception:
            logger.debug("kame: could not load remembered trouble", exc_info=True)

    # -- public ---------------------------------------------------------------

    def note(self, identity: str, ok: bool, *, call_id: Optional[str] = None,
             now: Optional[float] = None) -> None:
        """Add one judged outcome of ``identity``. One event per (identity, call): see the plan, S1a."""
        try:
            if disabled() or not identity:
                return
            at = round(time.time() if now is None else float(now), 3)
            with self._lock:
                self.ensure_loaded()
                if now is None:
                    # An outcome noted live after a Clear pool counts, even inside the forget's own millisecond
                    # (the tombstone drops every event at or before it; found by CI on Linux).
                    cleared_at = self._cleared.get(identity, 0.0)
                    if at <= cleared_at:
                        at = round(cleared_at + 0.001, 3)
                events = self._events.setdefault(identity, [])
                if call_id and events and self._last_call.get(identity) == call_id:
                    prev_at, prev_ok = events[-1]
                    if prev_ok and not ok:
                        return          # answered, then a late failure: still one success
                    events[-1] = (prev_at, 1 if ok else 0)     # keeps its instant: a merge dedupes on it
                else:
                    events.append((at, 1 if ok else 0))
                if call_id:
                    self._last_call[identity] = call_id
                self._events[identity] = _clean(events, self._cleared.get(identity, 0.0))[-MAX_EVENTS:]
                self._dirty = True
                self._maybe_save()
        except Exception:
            logger.debug("kame: could not note an outcome", exc_info=True)

    def _maybe_save(self) -> None:
        """Schedule the pending write on a timer thread (at most one per ``SAVE_EVERY_S``). Lock held.

        Review: never on the caller's thread - that is Agent Zero's event loop, on the way to the reply's first
        word. A process that is killed loses at most the last ``SAVE_EVERY_S``; a clean exit flushes (``atexit``)."""
        if not self._dirty or self._timer is not None:
            return
        try:
            # never longer than the throttle itself: a clock that jumped (a test's fake clock) once asked a
            # timer to wait longer than the platform allows ("timeout value is too large")
            wait = min(SAVE_EVERY_S, max(0.05, SAVE_EVERY_S - (time.monotonic() - self._last_save)))
            timer = threading.Timer(wait, self._timed_flush)
            timer.daemon = True
            self._timer = timer
            timer.start()
        except Exception:
            # no thread to be had: it stays pending - the next note schedules again, a clean exit flushes. Never
            # written here: this runs with the memory lock held (see ``_write``).
            self._timer = None

    def _cancel_timer(self) -> None:
        if self._timer is not None:
            try:
                self._timer.cancel()
            except Exception:
                pass
            self._timer = None

    def _timed_flush(self) -> None:
        with self._lock:
            self._timer = None
        self.flush()

    def flush(self) -> None:
        """Write what is pending now (tests, shutdown)."""
        try:
            if disabled():
                return
            with self._lock:
                if not self._dirty:
                    return
                # the file the memory was loaded from: a timer that fires after the data directory moved (a
                # test) must not carry one directory's outcomes into another
                path = self._loaded_path
                if path is None:
                    self._dirty = False
                    return
            self._write(path)       # outside the lock: see ``_write``
        except Exception:
            logger.debug("kame: could not flush remembered trouble", exc_info=True)

    def _window(self, identity: str, now: Optional[float]) -> List[Event]:
        stamp = time.time() if now is None else float(now)
        events = [e for e in self._events.get(identity, []) if stamp - e[0] <= MAX_AGE_S]
        return events[-MAX_EVENTS:]

    def risky(self, identity: str, now: Optional[float] = None) -> bool:
        """Latest judged outcome is a failure and at least ``MIN_FAILS`` failures in the window."""
        try:
            if disabled() or not identity:
                return False
            with self._lock:
                self.ensure_loaded()
                window = self._window(identity, now)
                if not window or window[-1][1]:
                    return False
                return sum(1 for _at, ok in window if not ok) >= MIN_FAILS
        except Exception:
            return False

    def summary(self, identity: str, now: Optional[float] = None) -> Tuple[int, int]:
        """``(failures, total)`` of the same window ``risky`` reads."""
        try:
            if disabled():
                return (0, 0)
            with self._lock:
                self.ensure_loaded()
                window = self._window(identity, now)
                return (sum(1 for _at, ok in window if not ok), len(window))
        except Exception:
            return (0, 0)

    def identities(self) -> List[str]:
        try:
            with self._lock:
                self.ensure_loaded()
                return list(self._events)
        except Exception:
            return []

    def trailing_failures(self, identity: str, now: Optional[float] = None) -> int:
        """1.8.4.3 (A10): how many of the latest outcomes in the window are failures in a row (0 when the
        latest is an answer, or nothing is known)."""
        try:
            if disabled() or not identity:
                return 0
            with self._lock:
                self.ensure_loaded()
                k = 0
                for _at, ok in reversed(self._window(identity, now)):
                    if ok:
                        break
                    k += 1
                return k
        except Exception:
            return 0

    def retest_due(self, identity: str, now: Optional[float] = None) -> bool:
        """1.8.4.3 (A10): is a model remembered as failing due another look? Its latest failure plus
        ``RETEST_FIRST_S`` x 2^(k-1), k = ``trailing_failures``, at most ``RETEST_MAX_S``. False for a model that is
        not remembered as failing (nothing to retest)."""
        try:
            stamp = time.time() if now is None else float(now)
            if not self.risky(identity, now=stamp):
                return False
            with self._lock:
                window = self._window(identity, stamp)
                if not window:
                    return False
                last_at = window[-1][0]
            k = max(1, self.trailing_failures(identity, now=stamp))
            wait = min(RETEST_MAX_S, RETEST_FIRST_S * (2.0 ** (k - 1)))
            return stamp - last_at >= wait
        except Exception:
            return False

    def risky_entry(self, entry: str, same: Callable[[str, str], bool], retest: bool = False) -> bool:
        """Any known identity that ``same(identity, entry)`` calls this chain entry is risky. With ``retest``
        (1.8.4.3, A10) an identity whose retest is due does not count: the choices among the models that are not
        the one in use look at it again."""
        try:
            if disabled():
                return False
            return any(self.risky(ident) and not (retest and self.retest_due(ident))
                       for ident in self.identities() if same(ident, entry))
        except Exception:
            return False

    def entry_summary(self, entry: str, same: Callable[[str, str], bool]) -> Tuple[int, int]:
        """``summary`` of the risky identity of this entry (the one with most failures)."""
        best = (0, 0)
        try:
            for ident in self.identities():
                if same(ident, entry):
                    got = self.summary(ident)
                    if got[0] >= best[0]:
                        best = got
        except Exception:
            pass
        return best

    def forget(self, match: Callable[[str], bool]) -> int:
        """Drop every identity ``match`` is True for, here and (by a tombstone) in the file."""
        try:
            if disabled():
                return 0
            with self._lock:
                self.ensure_loaded()
                dropped = 0
                stamp = round(time.time(), 3)
                for ident in [i for i in list(self._events) if match(i)]:
                    self._events.pop(ident, None)
                    self._last_call.pop(ident, None)
                    self._cleared[ident] = stamp
                    dropped += 1
                if dropped:
                    self._dirty = True
                path = self._loaded_path
            if dropped:
                # the user's own action (Clear pool): written now, with the memory lock released
                if path is not None:
                    self._write(path)
                else:
                    with self._lock:
                        self._dirty = False
            return dropped
        except Exception:
            logger.debug("kame: could not forget a model's trouble", exc_info=True)
            return 0


HEALTH = ModelHealth()
atexit.register(HEALTH.flush)

__all__ = ["HEALTH", "ModelHealth", "MAX_EVENTS", "MAX_AGE_S", "MIN_FAILS", "FILE_NAME", "SCHEMA", "ENV_OFF",
           "disabled"]

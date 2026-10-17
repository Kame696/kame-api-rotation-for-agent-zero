# Agent Zero port of the Hermes 1.8.2.7 `core/latency.py`, verbatim except `_fp`.
"""Per-key latency tracking using Peak EWMA (Linkerd/Envoy).

Peak EWMA keeps a weighted moving average that responds quickly to latency
spikes and decays slowly, which biases selection toward keys that are
consistently fast rather than keys that were fast once and slow since.

This module deliberately tracks *time to first token* (TTFT) for streaming
calls and *total time* for non-streaming — the two things a user waiting for
an answer actually experiences.

changes from the design pass:

* **One canonical key form.** ``record`` and ``get`` both canonicalize the
  key through :func:`core.carousel.fingerprint` internally, so the write
  path and the read path can never disagree about the key's shape again —
  the v2 bug where the transport wrote ``fingerprint(key)`` while the
  carousel read the raw key made every lookup return "no data" and the
  whole tiebreaker inert.
* **Censored samples.** A hedged call's loser only proves a *lower bound*
  ("still not done at t"), so it may raise the estimate but never lower it.
  Without this, survivorship bias drags the estimate down, which hedges
  more, which drags it further — a feedback loop.
* **Slow failures count.** An error that took longer than the key's own
  estimate (or 2s) was slow whatever it died of; it is recorded as latency.
  Fast failures stay invisible (fast failures look fast and bias downward).
* **Stale means unknown.** Ten minutes without an observation and the key
  scores ``None`` again — stale data from a previous context is worse than
  no data, and "no data" has a defined, neutral treatment in selection.
* **A display ring.** The last 64 samples per key, for p50/p95 in the panel.

Thread-safe, bounded, and memory-only: a process restart loses the history,
which is correct because the latency of the previous session has no bearing
on the latency of this one. Cross-process sharing goes through
``shared_health`` like everything else that survives a restart.

References
----------
* Linkerd "Beyond Round Robin: Load Balancing for Latency"
* Envoy Peak EWMA load balancer
* Dean & Barroso "The Tail at Scale" (CACM 2013)
"""

from __future__ import annotations

import math
import threading
import time
from collections import deque
from typing import Dict, Optional, Tuple

#: The decay half-life. tau = 60s: a free-tier key busy at one call every
#: 5-15s gets smoothing over ~5-10 samples and a spike halves in about a
#: minute; a sparsely-used key's old samples decay to nothing, which is
#: right, because STALE below retires them anyway.
_TAU_S = 60.0

#: After this long without an observation, the score is unknown again.
_STALE_S = 600.0

#: Sentinel for keys with no usable history. Inf sorts last under ``min``.
_NO_LATENCY = float("inf")

#: A slow failure is recorded as latency when it took at least this long,
#: even with no prior estimate — "it died, but it died slowly" is still
#: evidence the key was slow.
_SLOW_FAILURE_MIN_S = 2.0

#: Display ring per key, for p50/p95. Small: the panel needs a shape, not a
#: histogram.
_RING = 64


#: The fingerprint's own shape — ``key:`` plus six hex characters. A value
#: already in this form is its own fingerprint (idempotence: callers may
#: pass either form, and double-hashing a fingerprint would make the two
#: forms disagree again, which is the exact bug this module kills).
import re as _re

_FP_SHAPE = _re.compile(r"^key:[0-9a-f]{6}$")


def _fp(key: str) -> str:
    """The one key form this tracker stores — the panel-safe fingerprint.

    Idempotent: a value that already carries the fingerprint's shape is
    stored as-is, so writing ``fingerprint(k)`` and writing ``k`` land in
    the same bucket. Imported lazily to avoid a circular import at module
    level; any import failure falls back to the raw key on BOTH sides,
    which keeps write and read in agreement even in a broken environment.
    """
    text = str(key)
    if _FP_SHAPE.match(text):
        return text
    # Agent Zero port: the same digest the Hermes carousel's `fingerprint`
    # uses (sha256, six hex characters), computed here.
    import hashlib as _hashlib
    return "key:" + _hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:6]


class LatencyTracker:
    """Per-``(identity, key)`` Peak EWMA latency state."""

    def __init__(self, tau: float = _TAU_S) -> None:
        self._lock = threading.Lock()
        self._tau = max(1.0, float(tau))
        # (identity, fp) -> (peak_ewma, last_update_time, n)
        self._state: Dict[Tuple[str, str], Tuple[float, float, int]] = {}
        # (identity, fp) -> deque of (wall_time, seconds) for display
        self._ring: Dict[Tuple[str, str], "deque"] = {}

    # -- writes -----------------------------------------------------------

    def record(
        self,
        identity: str,
        key: str,
        latency_s: float,
        *,
        censored: bool = False,
        now: Optional[float] = None,
    ) -> None:
        """Record one observation for this key.

        ``censored=True`` is a hedge loser's lower bound: it may raise the
        estimate but never lower it. A normal sample is the full Peak EWMA
        update — jump up immediately on a spike, decay toward the sample
        otherwise. Non-finite and negative values are dropped silently:
        a clock glitch must not poison a key's estimate.
        """
        if latency_s < 0 or not math.isfinite(latency_s):
            return
        now = time.monotonic() if now is None else now
        pair = (str(identity), _fp(key))
        with self._lock:
            ewma, last_t, n = self._state.get(pair, (0.0, now, 0))
            if censored:
                if n == 0 or latency_s > ewma:
                    self._state[pair] = (latency_s, now, max(n, 1))
                return
            if n == 0 or latency_s > ewma:
                ewma = latency_s  # peak: jump up immediately
            else:
                w = math.exp(-max(0.0, now - last_t) / self._tau)
                ewma = w * ewma + (1.0 - w) * latency_s
            self._state[pair] = (ewma, now, n + 1)
            ring = self._ring.get(pair)
            if ring is None:
                ring = self._ring[pair] = deque(maxlen=_RING)
            ring.append((time.time(), latency_s))

    def note_slow_failure(
        self, identity: str, key: str, elapsed_s: float, *, now: Optional[float] = None
    ) -> None:
        """An error that took a while is still evidence the key was slow.

        Only fires when the call ran longer than the key's current estimate
        (or the absolute floor when there is no estimate) — a fast failure
        is not a latency observation at all.
        """
        current = self.get(identity, key, now=now)
        threshold = current if math.isfinite(current) else _SLOW_FAILURE_MIN_S
        if elapsed_s >= threshold:
            self.record(identity, key, elapsed_s, now=now)

    # -- reads ------------------------------------------------------------

    def get(
        self, identity: str, key: str, *, now: Optional[float] = None
    ) -> float:
        """Current Peak EWMA for this key, or ``_NO_LATENCY`` if unknown.

        The estimate decays while the key sits idle, so a key whose last
        sample was excellent an hour ago does not outrank one measured a
        second ago. Past ``_STALE_S`` without any observation, unknown.
        """
        now = time.monotonic() if now is None else now
        pair = (str(identity), _fp(key))
        with self._lock:
            state = self._state.get(pair)
            if state is None:
                return _NO_LATENCY
            ewma, last_time, _n = state
            if now - last_time > _STALE_S:
                return _NO_LATENCY
            dt = max(0.0, now - last_time)
            return ewma * math.exp(-dt / self._tau)

    def is_known(self, identity: str, key: str, *, now: Optional[float] = None) -> bool:
        now = time.monotonic() if now is None else now
        return math.isfinite(self.get(identity, key, now=now))

    def rank(
        self,
        identity: str,
        candidates: list,
        *,
        now: Optional[float] = None,
        tolerance: float = 1.5,
    ) -> list:
        """Sort ``candidates`` (keys) by latency, grouping ties.

        Returns the input list reordered so the fastest key comes first.
        Keys within ``tolerance`` of the fastest are treated as equal and
        left in their original relative order.
        """
        if len(candidates) <= 1:
            return candidates
        now = time.monotonic() if now is None else now
        scored = [(k, self.get(identity, k, now=now)) for k in candidates]
        best = min(s for _, s in scored)
        if best == _NO_LATENCY:
            return candidates  # no data at all — leave original order
        threshold = best * tolerance
        tied = [k for k, s in scored if s <= threshold]
        rest = [k for k, s in scored if s > threshold]
        rest.sort(key=lambda k: self.get(identity, k, now=now))
        return tied + rest

    def clear(self) -> None:
        with self._lock:
            self._state.clear()
            self._ring.clear()

    def snapshot(self, identity: str, keys: list, *, now: Optional[float] = None) -> Dict[str, Dict[str, object]]:
        """``{key_fingerprint: {p50_ms, p95_ms, n, score_ms}}`` for the panel.

        Percentiles come from the display ring restricted to the last 30
        minutes; the score is the live EWMA. People read seconds, not EWMA
        internals, so the panel gets the numbers it can label.
        """
        now = time.monotonic() if now is None else now
        out: Dict[str, Dict[str, object]] = {}
        cutoff_wall = time.time() - 1800.0
        for k in keys:
            fp = _fp(k)
            score = self.get(identity, k, now=now)
            entry: Dict[str, object] = {}
            if math.isfinite(score):
                entry["score_ms"] = round(score * 1000, 1)
            with self._lock:
                ring = self._ring.get((str(identity), fp))
                samples = [s for (t, s) in (ring or ()) if t >= cutoff_wall]
            if samples:
                samples.sort()
                entry["p50_ms"] = round(samples[len(samples) // 2] * 1000, 1)
                entry["p95_ms"] = round(samples[min(len(samples) - 1, math.ceil(0.95 * len(samples)) - 1)] * 1000, 1)
                entry["n"] = len(samples)
            if entry:
                out[fp] = entry
        return out


#: Module-level singleton, shared across threads like ``ENGINE``.
LATENCY = LatencyTracker()

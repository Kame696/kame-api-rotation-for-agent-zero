"""Agent Zero port of the Hermes 1.8.2.7 `core/hedging.py` (verbatim except where it
reads its settings: see :data:`SETTINGS`).

Adaptive hedging — send a backup request when the primary is slow.

Inspired by gRPC request hedging, hedge-python (PyPI), and "The Tail at
Scale" (Dean & Barroso, CACM 2013): a backup leg is sent only when the
current call exceeds the learned **p90** time-to-first-token for this
pool:model. The first response wins; the backup is cancelled.

changes from the design pass:

* **Buckets per (identity, mode, class).** A stream's TTFT and a
  non-stream's total time are different random variables (total grows
  with output length), and a thinking model's answer time is a different
  beast again — each gets its own samples and its own threshold, so a long
  thinking phase never triggers a backup and a huge completion never
  normalises a short one.
* **Cold-start thresholds per class.** With no samples yet, a call slower
  than 6s (normal) / 30s (thinking) still hedges — conservative numbers
  that only catch genuinely stuck calls, never a merely slow first answer.
* **Censored loser samples.** A primary that lost a race was slower than
  the winner's time, which is a lower bound, not a measurement — it is
  added as one, so survivorship bias cannot drag the threshold down and
  make the hedger hedge ever more (the feedback loop the design warned
  about).
* **429 back-pressure.** A hedge leg that gets a per-minute refusal
  penalises the budget and cools hedges for this identity for 60s —
  hedging into a rate-limited pool deepens the very exhaustion that made
  the primary slow.
* **Settings-driven.** Quantile, budget percent, burst, max legs and both
  cold thresholds read from settings at decision time, so a panel edit
  applies without a restart. ``hedge_mode`` picks off / adaptive / max:
  ``max`` fires legs from dispatch for latency-critical work, auto-
  downgrading for ten minutes when 429 pressure rises.

Key design constraints (unchanged from v2):

- Works with the existing carousel's select() — each leg gets a properly
  stamped key from the pool, respecting anti-dogpile.
- Budget: a per-identity **token bucket** limits hedge traffic to ~10% of
  calls, burst capped. Follows the hedge-python pattern.
- Disabled automatically under 429 pressure.
- Every leg is classified on failure; a cancelled leg is counted as
  dispatched quota but never benched (``Outcome.CANCELLED``).
- p90 rather than p95 — hedge-python's default, better for tail latency.

References
----------
* hedge-python: DDSketch + token bucket, fires at p90
* gRPC A6 proposal: hedgingDelay (time-based, complementary approach)
* "Stragglers Not Failures": adaptive DDSketch + token bucket, 74% p99 reduction
* Dean & Barroso "The Tail at Scale" (CACM 2013)
"""

from __future__ import annotations

import math
import re
import threading
import time
from typing import Any, Dict, Optional, Tuple

__all__ = ["HedgeTracker", "HEDGE_TRACKER", "reasoning_model", "bucket_key", "configure", "SETTINGS"]

#: Agent Zero port (1.8.2.7): the numbers the Hermes tracker reads from its
#: settings module, set by `kame_engine` from the plugin settings. Same names
#: and defaults as Hermes `settings.py`.
SETTINGS: Dict[str, Any] = {
    "hedge_mode": "adaptive",
    "hedge_budget_pct": 10.0,
    "hedge_burst": 3.0,
    "hedge_quantile": 90.0,
    "hedge_cold_s": 6.0,
    "hedge_cold_reasoning_s": 30.0,
    "hedge_max_prompt_tokens": 32000.0,
}


def configure(**values: Any) -> None:
    """Set any of :data:`SETTINGS` (unknown names are ignored)."""
    for name, value in values.items():
        if name in SETTINGS and value is not None:
            SETTINGS[name] = value

#: After this many samples the percentile estimate is trusted. Below it,
#: the cold-start thresholds decide.
_MIN_SAMPLES = 20

#: Samples kept per bucket; the age window below does the real pruning.
_MAX_SAMPLES = 200

#: A sample older than this stops counting; stale data from a different
#: context is worse than the cold start.
_SAMPLE_WINDOW_S = 1800.0

#: A hedge leg refused with a per-minute 429 cools hedging for this long.
_HEDGE_COOLDOWN_S = 60.0

#: ``max`` mode auto-downgrades for this long when the 429 rate rises.
_MAX_MODE_PENALTY_S = 600.0

#: 1.8.2.5. A stream's first backup goes out at this many times the model's
#: median time-to-first-word (the second at twice that).
_STREAM_MULTIPLIER = 2.0

#: 1.8.2.5. No stream backup before this, whatever the median says: below it
#: the backup would race ordinary network jitter, not a stuck request.
_STREAM_MIN_TRIGGER_S = 1.0


def bucket_key(identity: str, stream: bool, reasoning: bool) -> str:
    """The full bucket id: identity + mode + class, pipe-separated."""
    mode = "ttft" if stream else "total"
    cls = "reasoning" if reasoning else "std"
    return f"{identity}|{mode}|{cls}"


#: 1.8.2.4. Gemini 2.5 and every later generation think by default; only the
#: ``-lite`` tiers ship with thinking off. Their names never say "thinking",
#: so 1.8.2.3 classed the owner's own pool as a plain model and gave it the
#: 6 s cold threshold meant for answers that do not reason first.
_GEMINI_THINKS = re.compile(r"gemini-(?:2\.5|[3-9](?:\.\d+)?)(?![\d.])")

#: 1.8.2.4. Request fields that switch thinking on whatever the model's name.
_THINKING_FIELDS = ("reasoning_effort", "reasoning", "thinking", "thinking_config")


def _asks_for_thinking(kwargs: Any) -> bool:
    """Whether the request itself turns thinking on (top level or ``extra_body``)."""
    if not isinstance(kwargs, dict):
        return False
    for where in (kwargs, kwargs.get("extra_body") or {}):
        if not isinstance(where, dict):
            continue
        for name in _THINKING_FIELDS:
            value = where.get(name)
            if value in (None, False, "", "none", "off", 0):
                continue
            if isinstance(value, dict) and value.get("type") == "disabled":
                continue
            return True
    return False


def reasoning_model(model: str, kwargs: Any = None) -> bool:
    """Whether this call is a thinking call: from the model's name, or from
    the request asking for thinking.

    First match wins. Explicit budget-zero thinking still counts for a
    family that thinks — a model *family* that thinks is classed by family.
    """
    if _asks_for_thinking(kwargs):
        return True
    text = str(model or "").strip().lower()
    if not text:
        return False
    for token in ("o1", "o3", "o4", "r1", "qwq", "deepseek-reasoner",
                  "thinking", "gpt-5", "magistral", "reasoning"):
        if token in text:
            return True
    if _GEMINI_THINKS.search(text) and "lite" not in text:
        return True
    return False


class _TokenBucket:
    """Simple token bucket, refilled by observed successful calls."""

    __slots__ = ("tokens", "max_tokens", "refill_rate", "_fractional")

    def __init__(self, initial: float, max_tokens: float, refill_rate: float) -> None:
        self.tokens = initial
        self.max_tokens = max_tokens
        self.refill_rate = refill_rate
        self._fractional = 0.0

    def refill(self, calls: int = 1) -> None:
        self._fractional += calls * self.refill_rate
        whole = int(self._fractional)
        if whole > 0:
            self._fractional -= whole
            self.tokens = min(self.max_tokens, self.tokens + whole)

    def try_consume(self) -> bool:
        if self.tokens >= 1.0:
            self.tokens -= 1.0
            return True
        return False

    def penalize(self) -> None:
        self.tokens = max(-self.max_tokens, self.tokens - 1.0)


class HedgeTracker:
    """Decides whether a hedge should be sent, per identity/mode/class."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # bucket -> list of (monotonic, latency_s)
        self._samples: Dict[str, list] = {}
        # bucket -> _TokenBucket
        self._budgets: Dict[str, _TokenBucket] = {}
        # bucket -> int
        self._hedge_count: Dict[str, int] = {}
        # identity -> monotonic time until which hedging is cooled down
        self._cool_until: Dict[str, float] = {}
        # identity -> monotonic time until which "max" mode is downgraded
        self._max_downgraded_until: Dict[str, float] = {}

    # -- settings read (call-time, never cached at import) ----------------

    @staticmethod
    def _setting(name: str, default: float) -> float:
        # Agent Zero port: the engine sets these from the plugin settings
        # (`configure`); Hermes reads its own settings module here.
        try:
            return float(SETTINGS.get(name, default))
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _mode() -> str:
        return str(SETTINGS.get("hedge_mode") or "adaptive")

    # -- bucket plumbing ----------------------------------------------------

    def _bucket_for(self, key: str, *, budget_pct: float, burst: float) -> _TokenBucket:
        bucket = self._budgets.get(key)
        if bucket is None:
            rate = max(0.0, budget_pct) / 100.0
            # 1.8.2.5: a new bucket starts full — a process that has just
            # started is the "idle period" the burst setting describes. It
            # started with one token, and live on the owner's Gemini pool
            # (2026-10-12) the first slow reply after a backup found the
            # budget empty and waited 31.9 s alone. The long-run share is
            # unchanged: refills still come at ``budget_pct`` per answer.
            bucket = _TokenBucket(max(1.0, burst), max(1.0, burst), rate)
            self._budgets[key] = bucket
        return bucket

    # -- recording -----------------------------------------------------------

    def record_latency(
        self,
        identity: str,
        latency_s: float,
        *,
        stream: bool = False,
        reasoning: bool = False,
        censored: bool = False,
        now: Optional[float] = None,
    ) -> None:
        """Record one observation for this identity's bucket.

        Also refills the bucket's budget — each observed call earns a
        fraction of a hedge token. ``censored`` is a race loser's lower
        bound: kept for the percentile as-is (it may only ever push the
        threshold up), never allowed to lower it.
        """
        if latency_s < 0 or not math.isfinite(latency_s):
            return
        now = time.monotonic() if now is None else now
        key = bucket_key(identity, stream, reasoning)
        budget_pct = self._setting("hedge_budget_pct", 10.0)
        burst = self._setting("hedge_burst", 3.0)
        with self._lock:
            samples = self._samples.setdefault(key, [])
            if censored:
                # a lower bound is still evidence of slowness; add it only if
                # it exceeds the current p90-relevant mass by simply keeping
                # it — the percentile read below never lets a censored value
                # pull the estimate down because we store it as the value it
                # bounds.
                samples.append((now, latency_s))
            else:
                samples.append((now, latency_s))
                self._bucket_for(key, budget_pct=budget_pct, burst=burst).refill(1)
            if len(samples) > _MAX_SAMPLES:
                samples[:] = samples[-_MAX_SAMPLES:]

    def note_slow_call(self, identity: str, *, stream: bool, reasoning: bool,
                       elapsed_s: float, now: Optional[float] = None) -> None:
        """A call that was slow (hedged or not) still teaches the bucket."""
        self.record_latency(identity, elapsed_s, stream=stream, reasoning=reasoning, now=now)

    # -- the threshold -------------------------------------------------------

    def threshold(self, identity: str, *, stream: bool, reasoning: bool,
                  now: Optional[float] = None) -> Optional[float]:
        """The hedge trigger for this bucket, or ``None`` when it would
        mean "never hedge" (no data and cold-start disabled)."""
        now = time.monotonic() if now is None else now
        key = bucket_key(identity, stream, reasoning)
        cold = self._setting(
            "hedge_cold_reasoning_s" if reasoning else "hedge_cold_s",
            30.0 if reasoning else 6.0,
        )
        with self._lock:
            samples = self._samples.get(key, [])
            recent = [l for t, l in samples if now - t < _SAMPLE_WINDOW_S]
            if len(recent) < _MIN_SAMPLES:
                # Cold start. Samples older than the window are evidence of
                # a different context, not of this one: they are never
                # fallback material (a stale threshold hedges wrong on both
                # sides — too eager for a faster pool, too shy for slower).
                return cold if cold > 0 else None
            recent.sort()
            pct = self._setting("hedge_quantile", 90.0) / 100.0
            idx = min(len(recent) - 1, int(len(recent) * pct))
            learned = recent[idx]
            # clamp the learned threshold to [cold, 4*cold] so a single
            # pathological sample window cannot turn hedging off entirely
            # or make it fire on every call
            if cold > 0:
                return min(max(learned, cold * 0.5), cold * 4.0)
            return learned

    def percentile(self, identity: str, *, now: Optional[float] = None,
                   pct: float = 0.90, stream: bool = False, reasoning: bool = False) -> Optional[float]:
        """Compatibility alias: the p90 threshold for this identity's bucket."""
        return self.threshold(identity, stream=stream, reasoning=reasoning, now=now)

    # keep the v2 alias working for the transport's existing call sites
    def p95(self, identity: str, *, now: Optional[float] = None) -> Optional[float]:
        return self.threshold(identity, stream=False, reasoning=False, now=now)

    # -- the decision ----------------------------------------------------------

    def should_hedge(
        self,
        identity: str,
        elapsed_s: float,
        *,
        healthy_count: int,
        under_pressure: bool = False,
        stream: bool = False,
        reasoning: bool = False,
        est_prompt_tokens: int = 0,
        now: Optional[float] = None,
    ) -> bool:
        """Whether to send a backup request right now.

        True only when ALL hold: the pool has a spare healthy key; it is
        not under 429 pressure; this call has exceeded its bucket's
        threshold (learned p90, or the cold-start number before enough
        samples exist); the prompt is not too large to double; the budget
        has a token; and this identity is not in a hedge cooldown.
        """
        now = time.monotonic() if now is None else now
        mode = self._mode()
        if mode == "off":
            return False
        if under_pressure or healthy_count < 2:
            return False
        if now < self._cool_until.get(identity, 0.0):
            return False
        max_tokens = self._setting("hedge_max_prompt_tokens", 32000.0)
        if est_prompt_tokens and est_prompt_tokens > max_tokens:
            return False
        if mode == "max":
            if now < self._max_downgraded_until.get(identity, 0.0):
                pass  # downgraded: fall through to the adaptive rules
            else:
                # max mode: fire from dispatch, only the saturation and
                # prompt-size guards above apply, and the budget is not
                # consulted — but it is not refilled either, so an
                # auto-downgrade restores order quickly.
                return True
        threshold = self.threshold(identity, stream=stream, reasoning=reasoning, now=now)
        if threshold is None:
            return False
        if elapsed_s < threshold:
            return False
        key = bucket_key(identity, stream, reasoning)
        budget_pct = self._setting("hedge_budget_pct", 10.0)
        burst = self._setting("hedge_burst", 3.0)
        with self._lock:
            return self._bucket_for(key, budget_pct=budget_pct, burst=burst).try_consume()

    # -- 1.8.2.5: streams ----------------------------------------------------

    def stream_triggers(self, identity: str, *, reasoning: bool,
                        now: Optional[float] = None) -> Tuple[float, float]:
        """Seconds without a first word after which a stream gets a first and
        a second backup leg.

        Learned from this model's own time-to-first-word: the first backup at
        ``_STREAM_MULTIPLIER`` times the median, the second at twice that.
        The median and not a high quantile, because the owner's real traffic
        (2026-10-12, 3,275 answered attempts) has a heavy tail — a quarter of
        attempts on every main model take more than twice the median — and a
        p90 sits *inside* that tail, so a backup timed on it goes out after
        most of the wait has already been paid. Before ``_MIN_SAMPLES``
        exist, the cold-start number of the class. ``max`` mode starts the
        first backup at once.
        """
        now = time.monotonic() if now is None else now
        cold = self._setting(
            "hedge_cold_reasoning_s" if reasoning else "hedge_cold_s",
            30.0 if reasoning else 6.0,
        )
        cold = max(cold, _STREAM_MIN_TRIGGER_S)
        if self.fires_from_dispatch(identity, now=now):
            return 0.0, cold
        key = bucket_key(identity, True, reasoning)
        with self._lock:
            # The last ``_MAX_SAMPLES`` first-word times, whatever their age:
            # a model's usual first-word time moves over days, not minutes,
            # and the recorded history (``seed_stream``) is exactly what a
            # fresh process needs not to start the race cold.
            recent = sorted(l for t, l in self._samples.get(key, []))
        if len(recent) < _MIN_SAMPLES:
            first = cold
        else:
            median = recent[len(recent) // 2]
            first = min(max(_STREAM_MIN_TRIGGER_S, _STREAM_MULTIPLIER * median), cold * 4.0)
        return first, first * 2.0

    def first_word_stats(self, identity: str, *, reasoning: bool) -> Optional[Tuple[float, float]]:
        """``(median, p95)`` of this model's learned first-word times, or ``None``
        (Hermes 1.8.2.8, decisions/0028: the automatic first-token wait and the
        automatic model switch are sized from it)."""
        key = bucket_key(identity, True, reasoning)
        with self._lock:
            recent = sorted(l for t, l in self._samples.get(key, []))
        if len(recent) < _MIN_SAMPLES:
            return None
        median = recent[len(recent) // 2]
        p95 = recent[min(len(recent) - 1, int(len(recent) * 0.95))]
        return median, p95

    def seed_stream(self, identity: str, samples, *, reasoning: bool,
                    now: Optional[float] = None) -> int:
        """Give this model's stream bucket its recorded first-word times.

        Only into an empty bucket (live samples always win), at most
        ``_MAX_SAMPLES``. Seeded samples teach the trigger; they never refill
        the backup budget — only answered calls in this process do.
        Returns how many were added.
        """
        now = time.monotonic() if now is None else now
        key = bucket_key(identity, True, reasoning)
        values = [float(s) for s in list(samples)[-_MAX_SAMPLES:]
                  if isinstance(s, (int, float)) and math.isfinite(s) and s > 0]
        with self._lock:
            bucket = self._samples.setdefault(key, [])
            if bucket or not values:
                return 0
            bucket.extend((now, value) for value in values)
            return len(values)

    def may_start_backup(
        self,
        identity: str,
        *,
        healthy_count: int,
        under_pressure: bool,
        stream: bool,
        reasoning: bool,
        est_prompt_tokens: int = 0,
        now: Optional[float] = None,
    ) -> bool:
        """Every guard of :meth:`should_hedge` except the timing, which the
        caller already decided (a stream's triggers are its own). Consumes a
        budget token when it says yes, outside ``max`` mode."""
        now = time.monotonic() if now is None else now
        mode = self._mode()
        if mode == "off" or under_pressure or healthy_count < 2:
            return False
        if now < self._cool_until.get(identity, 0.0):
            return False
        max_tokens = self._setting("hedge_max_prompt_tokens", 32000.0)
        if est_prompt_tokens and est_prompt_tokens > max_tokens:
            return False
        if self.fires_from_dispatch(identity, now=now):
            return True
        key = bucket_key(identity, stream, reasoning)
        budget_pct = self._setting("hedge_budget_pct", 10.0)
        burst = self._setting("hedge_burst", 3.0)
        with self._lock:
            return self._bucket_for(key, budget_pct=budget_pct, burst=burst).try_consume()

    def fires_from_dispatch(self, identity: str, *, now: Optional[float] = None) -> bool:
        """Whether ``max`` mode is in force for this identity right now.

        1.8.2.4: the setting promises "fires backups from the start", but the
        transport waited for the threshold before asking, so ``max`` only
        skipped the budget. The transport now asks this first and, when it
        is true, considers the backup without waiting.
        """
        now = time.monotonic() if now is None else now
        if self._mode() != "max":
            return False
        with self._lock:
            return now >= self._max_downgraded_until.get(identity, 0.0)

    # -- diagnostics and pressure --------------------------------------------

    def note_hedge(self, identity: str, *, now: Optional[float] = None) -> None:
        """Record that a hedge was sent."""
        now = time.monotonic() if now is None else now
        with self._lock:
            self._hedge_count[identity] = self._hedge_count.get(identity, 0) + 1

    def note_hedge_429(self, identity: str, *, now: Optional[float] = None) -> None:
        """A hedge leg was refused with a per-minute 429.

        Penalises the budget (a wasted hedge), cools hedging for this
        identity for a minute, and auto-downgrades ``max`` mode for ten
        minutes — hedging into a rate-limited pool is the one way hedging
        makes things worse.
        """
        now = time.monotonic() if now is None else now
        with self._lock:
            self._cool_until[identity] = now + _HEDGE_COOLDOWN_S
            self._max_downgraded_until[identity] = now + _MAX_MODE_PENALTY_S
            for key, bucket in self._budgets.items():
                if key.split("|", 1)[0] == identity:
                    bucket.penalize()

    def hedge_count(self, identity: str) -> int:
        with self._lock:
            return self._hedge_count.get(identity, 0)

    def clear(self) -> None:
        with self._lock:
            self._samples.clear()
            self._budgets.clear()
            self._hedge_count.clear()
            self._cool_until.clear()
            self._max_downgraded_until.clear()

    def snapshot(self) -> Dict[str, object]:
        """For the panel: per-bucket threshold, samples, budget, cooldown."""
        now = time.monotonic()
        out: Dict[str, object] = {}
        with self._lock:
            for key, samples in self._samples.items():
                recent = [l for t, l in samples if now - t < _SAMPLE_WINDOW_S]
                bucket = self._budgets.get(key)
                out[key] = {
                    "samples": len(recent),
                    "budget_tokens": round(bucket.tokens, 1) if bucket else None,
                    "hedges": self._hedge_count.get(key.split("|", 1)[0], 0),
                    "cooling": now < self._cool_until.get(key.split("|", 1)[0], 0.0),
                }
        return out


#: Module-level singleton.
HEDGE_TRACKER = HedgeTracker()

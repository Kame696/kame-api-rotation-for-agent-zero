"""KAME API Rotation & Stability Engine v1.0.1.

Full-Spectrum Protections:
1. Identity-Aware Health (Tracks health by Model ID to isolate Chat/Utility)
2. Eternal Rotation (Infinite Loop - NEVER turns off)
3. RPM-Aware Predictive Selection (Picks key with most remaining capacity)
4. Anti-Dogpile Guard (Marks key used at pick time for concurrent safety)
5. Anti-Thundering-Herd (Pending counter prevents concurrent key collision)
6. Trust the Connection (No artificial timeouts - if accepted, let it finish)
7. Hybrid Learning Jitter (Smart Penalty Box + ETA-driven sleep + anti-bot jitter)
8. KAME-Aware Compression Guard (UI Status Reporting + context-aware)
9. Rate Limiter Deadlock Fix (threading.Lock replaces asyncio.Lock)
10. Token Callback Support (framework token counters work correctly)
11. Friendly Error Reporting (clean messages, honest status, real error class)
12. Daily-Quota & Account-Limit Aware (multi-provider, v1.0.1)
13. Adaptive Backoff (provider-agnostic safety net, v1.0.1)

v1.0.1 BEHAVIORAL ADDITIONS (engine selection/rotation path UNCHANGED):
- Daily-quota / account-limit awareness. Some providers (notably Google)
  return a MISLEADING short retryDelay on a daily-quota 429 (real example:
  daily 250/250 exhausted, but the body says "retryDelay: 1s"). Trusting
  that value made prior versions re-probe a dead key roughly once per
  second. v1.0.1 detects strict daily/account markers
  (PerDay / per day / RPD / daily / insufficient_quota) and applies a long
  cooldown (default 3600s, configurable) INSTEAD of the misleading value.
- Adaptive backoff: when KAME is "blind" (the provider stripped the error
  details, or it is a provider we have no specific rule for) and the SAME
  key keeps failing with a rate-limit error, the cooldown escalates
  (20s -> 40s -> 80s -> ... up to the ceiling) and RESETS on any success.
  This kills the re-probe burst without needing to detect "daily", and it
  works on ANY provider. The per-minute fast path is untouched: a key that
  recovers after its honest retryDelay never escalates.
- Retry parser hardened: reads the structured ``exc.retry_delay`` attribute
  (Google RetryInfo), parses compound durations ("6m 11.52s", "2h 30m" -
  Groq style), and the accepted ceiling was raised 3600s -> 86400s (24h) so
  legitimately long, honest values (OpenAI/Groq daily) are respected instead
  of discarded down to the 20s fallback.
- Richer logging: failure lines now show the REAL error (status + kind +
  action), e.g. "429 daily-quota -> cooling 1h" / "429 per-minute -> wait
  37s" / "insufficient_quota -> cooling 24h". Key id display is configurable
  (anonymized fingerprint by default, never leaks the secret). Optional
  per-session summary in verbose mode. Long-cooling sleeps no longer spam
  the log (one notice per outage instead of one every 30s).
- Log overhaul: a tri-state ``kame_log_level`` (silent / normal / verbose)
  replaces the old ``verbose_trace`` checkbox (kept as an alias). "normal"
  (default) prints one line per SUCCESSFUL call plus events, showing the pool
  count only when degraded; "silent" keeps KAME out of the Docker log entirely
  (hard errors only); "verbose" adds the heartbeat, per-call timing, full pool
  snapshot, cascade and session summary. The success line now states rotations
  in plain words ("2 rotations") instead of the ambiguous "(N attempts)".

v1.0.1 RELIABILITY FIXES (engine selection/rotation path still UNCHANGED):
- Intervention passthrough: the streaming callbacks raise InterventionException
  when the user sends a message mid-generation. KAME's broad ``except Exception``
  used to swallow it, so the message was ignored until the user pressed the
  "nudge agent" button. KAME now re-raises A0 control-flow exceptions
  (Intervention / Repairable / Handled), restoring native behavior - no nudge.
- got_any_chunk guard: if a stream fails AFTER emitting content, KAME no longer
  rotates-and-re-streams from scratch on another key; it re-raises so A0
  restarts the turn cleanly (mirrors vanilla A0). Rate-limit / 503 storms fail
  at connect time (before any chunk), so the carousel is untouched for them.
- Server-error escalation: a SUSTAINED 503/500 outage on a large key pool no
  longer spins at a flat 5s forever. A gentle per-key escalation
  (5 -> 10 -> 20 -> 40 -> 80s, reset on success) lets the pool go cold so the
  ETA-driven sleep takes over. Transient blips still recover in ~5s.

v0.5.8.0 BEHAVIORAL FIX - ETA-driven sleep on exhausted pool (kept):
- When ALL keys are sick AND the soonest sick_until > 3s, sleep until that
  key recovers (capped) instead of pulsing every 2s. After the sleep the
  loop `continue`s - KAME NEVER calls acompletion() with a sick key.

v0.5.7.4 UX refinements (kept):
- Verbose trace mode — now the `verbose` tier of `kame_log_level`
  (the legacy `verbose_trace: true` still maps to it).
- Compression-aware light filter in `_get_best_key`.

When at least 1 key is healthy: selection/request behavior is IDENTICAL to
v1.0.0. The v1.0.1 changes only affect cooldown duration on failures, the
all-keys-sick sleep cadence, and log text.

Compatible with Agent Zero v1.14+ (verified through v1.18).
"""

import asyncio, contextvars, hashlib, threading, time, logging, re
from typing import Any, Awaitable, Callable, List, Optional, Tuple
import openai
import litellm
from litellm import acompletion
from langchain_core.messages import SystemMessage, HumanMessage
from helpers.print_style import PrintStyle

# --- v1.0.1: A0 control-flow exceptions that must PASS THROUGH the carousel ---
# A0 signals some events by raising an exception that is meant to PROPAGATE, not
# to be treated as a failed API call. The most important is InterventionException:
# the streaming callbacks call handle_intervention() on every chunk, and when the
# user sends a message mid-generation it raises InterventionException so the
# monologue can stop and fold the new message in. KAME's broad ``except Exception``
# used to swallow it (rotating to the next key), which is exactly why a mid-run
# message did nothing until the user pressed the "nudge agent" button. We re-raise
# these so native A0 behavior is restored (no nudge needed). RepairableException
# and HandledException are likewise control-flow signals A0 expects to propagate.
# Guarded so the engine still imports in a standalone (no-A0) test harness.
try:
    from helpers.errors import (
        InterventionException as _KameInterventionException,
        RepairableException as _KameRepairableException,
        HandledException as _KameHandledException,
    )
    _KAME_PASSTHROUGH_EXC = (
        _KameInterventionException,
        _KameRepairableException,
        _KameHandledException,
    )
except Exception:
    _KAME_PASSTHROUGH_EXC = ()

# --- GLOBAL REGISTRY ---
_KAME_KEY_HEALTH = {}  # { "provider:model": { "keys": {key: {sick_until, last_used, request_log, last_sick_at, consecutive_rl}} } }
_KAME_LOCK = threading.Lock()
_KAME_PATCHED = False
_KAME_CALL_CONTEXT = contextvars.ContextVar('kame_ctx', default='')

# --- v1.0.1: log verbosity level (replaces the old verbose_trace toggle) ---
# Set by the activation extension from the `kame_log_level` plugin setting:
#   "silent"  - no routine output (not even the activation banner); only hard
#               errors (e.g. a failed patch) still print. The clean-Docker-log
#               escape hatch for users who never want to see KAME.
#   "normal"  - (default) speaks only when it matters: one compact line per
#               SUCCESSFUL call, plus rotations / limits / sleeps / errors, and
#               the pool count ONLY when the pool is degraded. No "Calling..."
#               heartbeat, no attempt counter.
#   "verbose" - everything: the "Calling..." heartbeat, the key-picked line,
#               per-call wall time + full pool snapshot + cascade, and a
#               periodic session summary. Pure instrumentation, no algorithm
#               change.
# Back-compat: a legacy `verbose_trace: true` maps to "verbose".
_KAME_LOG_LEVEL = "normal"

# Window (seconds) within which a recently-recovered key is de-prioritized
# from COMPRESSION calls only. Chat/Utility paths are unaffected.
_KAME_COMPRESS_FRESH_WINDOW_S = 5.0

# --- v1.0.1: daily-quota / account-limit cooldown (configurable) ---
# When a daily or account (out-of-credit) limit is detected, KAME ignores any
# misleading short retryDelay and cools the key for at least this long. This
# value is ALSO the ceiling for the adaptive backoff escalation. Default 1h.
_KAME_DAILY_COOLDOWN_S = 3600.0

# Absolute hard ceiling for ANY parsed retry value. Rejects garbage like
# 99999999 while still respecting honest long values (e.g. OpenAI 24h).
_KAME_HARD_DELAY_CAP_S = 86400.0  # 24h

# --- v1.0.1 fix: ceiling for the gentle 503/500 server-error escalation ---
# A 503 ("server busy") is the provider being momentarily overloaded, NOT the
# key being spent, so the first failure stays short (~5s). But on a LARGE pool
# the fast lap (e.g. 15 keys ~2.5s each) means a flat 5s cooldown recovers every
# key before it is re-tried, so the pool never goes cold and KAME would rotate
# 503->5s forever during a sustained outage. A gentle per-key escalation
# (5 -> 10 -> 20 -> 40 -> 80, capped here) lets a SUSTAINED outage take the pool
# cold so the ETA-driven sleep takes over; any success resets it, so transient
# blips never escalate.
_KAME_SERVER_BACKOFF_CAP_S = 90.0

# Minimum real-time gap between repeated "all keys cooling" sleep log lines
# while the soonest recovery is still near (avoids per-cycle spam).
_KAME_SLEEP_LOG_MIN_INTERVAL_S = 5.0

# --- v1.0.1: key id display style (configurable) ---
# "fingerprint" (default): anonymized stable hash id, never leaks the secret.
# "prefix8": first 8 chars of the real key (recognizable, mild leak).
# "full": the whole key (debug only; leaks the secret into logs).
_KAME_KEY_LOG_STYLE = "fingerprint"

# --- v1.0.1: lightweight in-memory session stats (for verbose summary) ---
_KAME_STATS = {
    "ok": 0, "per_minute": 0, "daily": 0, "insufficient_quota": 0,
    "server": 0, "timeout": 0, "auth": 0, "other": 0, "long_sleeps": 0,
}
_KAME_CALL_COUNT = 0


def set_log_level(level) -> None:
    """Set the log verbosity: 'silent' | 'normal' | 'verbose'.

    Called by the activation extension from the `kame_log_level` plugin
    setting. Invalid input is ignored (keeps the current level).
    """
    global _KAME_LOG_LEVEL
    s = str(level or "").strip().lower()
    if s in ("silent", "normal", "verbose"):
        _KAME_LOG_LEVEL = s


def set_verbose_trace(enabled) -> None:
    """Back-compat shim for the retired `verbose_trace` boolean setting.

    A truthy value selects the richest level ('verbose'); a falsy value is a
    no-op so it never overrides an explicit `kame_log_level`.
    """
    global _KAME_LOG_LEVEL
    if bool(enabled):
        _KAME_LOG_LEVEL = "verbose"


def _lvl_normal() -> bool:
    """True when routine status lines should print (level normal or verbose)."""
    return _KAME_LOG_LEVEL in ("normal", "verbose")


def _lvl_verbose() -> bool:
    """True only at the richest level."""
    return _KAME_LOG_LEVEL == "verbose"


def set_daily_cooldown(seconds) -> None:
    """Set the daily/account-limit cooldown (and adaptive backoff ceiling).

    Called by the activation extension from the `daily_quota_cooldown_seconds`
    plugin setting. Clamped to a sane range; bad input is ignored.
    """
    global _KAME_DAILY_COOLDOWN_S
    try:
        v = float(seconds)
        if 1.0 <= v <= _KAME_HARD_DELAY_CAP_S:
            _KAME_DAILY_COOLDOWN_S = v
    except (ValueError, TypeError):
        pass


def set_key_log_style(style) -> None:
    """Set how API keys are shown in logs. Called by the activation extension."""
    global _KAME_KEY_LOG_STYLE
    s = str(style or "").strip().lower()
    if s in ("fingerprint", "prefix8", "full"):
        _KAME_KEY_LOG_STYLE = s


def _key_short_id(key: str) -> str:
    """Return a 6-char content-hash identifier for a key, stable across runs.

    Hash so we never echo a key prefix/suffix that could leak the secret.
    """
    if not key:
        return "------"
    h = hashlib.sha256(key.encode("utf-8", errors="replace")).hexdigest()
    return "k" + h[:5]


def _key_display(key: str) -> str:
    """Render a key for logs according to the configured `key_log_style`.

    Default ("fingerprint") returns an anonymized stable id (NOT the real key).
    """
    if not key:
        return "------"
    style = _KAME_KEY_LOG_STYLE
    if style == "full":
        return key
    if style == "prefix8":
        return key[:8] + "..."
    return _key_short_id(key)


def _fmt_duration(seconds) -> str:
    """Human-friendly compact duration: 45s / 6m / 2h."""
    try:
        s = float(seconds)
    except (ValueError, TypeError):
        return "?"
    if s < 60:
        return f"{s:.0f}s"
    if s < 3600:
        return f"{s / 60:.0f}m"
    return f"{s / 3600:.0f}h"


def _pool_snapshot(identity: str, all_keys: list) -> str:
    """Read-only one-liner about the pool state - for verbose logs only.

    Example: "pool 14/15 healthy, 1 cooling (next in 38s)"
    """
    now = time.time()
    with _KAME_LOCK:
        state = _KAME_KEY_HEALTH.get(identity, {}).get("keys", {})
        total = len(all_keys)
        healthy = 0
        soonest_recovery = None
        for k in all_keys:
            ks = state.get(k) or {}
            sick_until = float(ks.get("sick_until") or 0)
            if sick_until < now:
                healthy += 1
            else:
                eta = sick_until - now
                if soonest_recovery is None or eta < soonest_recovery:
                    soonest_recovery = eta
    cooling = total - healthy
    if cooling == 0:
        return f"pool {healthy}/{total} healthy"
    eta_s = _fmt_duration(soonest_recovery) if soonest_recovery is not None else "?"
    return f"pool {healthy}/{total} healthy, {cooling} cooling (next in {eta_s})"


def _pool_snapshot_if_degraded(identity, all_keys) -> str:
    """Compact pool line for NORMAL mode - empty string when all keys healthy.

    Lets the normal log stay quiet about pool health while everything is fine,
    and surface "pool 12/15 healthy" the moment a key starts cooling.
    """
    now = time.time()
    with _KAME_LOCK:
        state = _KAME_KEY_HEALTH.get(identity, {}).get("keys", {})
        total = len(all_keys)
        healthy = sum(
            1 for k in all_keys
            if float((state.get(k) or {}).get("sick_until") or 0) < now
        )
    if total == 0 or healthy >= total:
        return ""
    return f"pool {healthy}/{total} healthy"


def _cascade_str(attempt_no, sleep_count, overhead_s) -> str:
    """Human-readable summary of what it took to land a successful call.

    Returns e.g. "2 rotations, 1 sleep" - or "" for the happy path (first key
    worked). `attempt_no` counts EVERY loop iteration (key tries AND sleep
    cycles), so the true rotation count is the iterations that were neither the
    final success nor a sleep: attempt_no - 1 - sleep_count. (The old log said
    "{attempt_no} attempts", which both read like same-key retries AND silently
    folded sleeps into the rotation count - this fixes both.)
    """
    rotations = attempt_no - 1 - sleep_count
    if rotations < 0:
        rotations = 0
    parts = []
    if rotations:
        parts.append(f"{rotations} rotation" + ("s" if rotations != 1 else ""))
    if sleep_count:
        parts.append(f"{sleep_count} sleep" + ("s" if sleep_count != 1 else ""))
    if overhead_s > 0.1:
        parts.append(f"{overhead_s:.1f}s local wait")
    return ", ".join(parts)


def _next_recovery_seconds(identity: str, all_keys: list):
    """Smallest `sick_until - now` across all keys. None if all healthy."""
    now = time.time()
    with _KAME_LOCK:
        state = _KAME_KEY_HEALTH.get(identity, {}).get("keys", {})
        best = None
        for k in all_keys:
            ks = state.get(k) or {}
            sick_until = float(ks.get("sick_until") or 0)
            if sick_until > now:
                eta = sick_until - now
                if best is None or eta < best:
                    best = eta
    return best


def _session_summary_line() -> str:
    """Aggregate one-liner for the verbose periodic summary."""
    s = _KAME_STATS
    limited = s["per_minute"] + s["daily"] + s["insufficient_quota"]
    return (
        f"[KAME] Session: {s['ok']} ok · {limited} limited "
        f"(min {s['per_minute']}, daily {s['daily']}, quota {s['insufficient_quota']}) · "
        f"{s['long_sleeps']} long-sleep{'s' if s['long_sleeps'] != 1 else ''} · "
        f"{s['server']} server · {s['timeout']} timeout · {s['auth']} auth · {s['other']} other"
    )


_RATE_LIMIT_INDICATORS = (
    "429", "too many requests", "rate limit", "rate_limit",
    "quota exceeded", "quota left", "no quota",
    "resource exhausted", "resource_exhausted",
    "tokens per min", "requests per min", "quota_exceeded",
)

# --- v1.0.1: STRICT daily / account-limit markers (multi-provider) ---
# These are intentionally narrow so a per-minute (RPM/TPM) error is NEVER
# misclassified as daily. Google per-minute errors say "PerMinute" and contain
# the word "quota" but NOT any of these tokens.
_DAILY_LIMIT_INDICATORS = (
    "perday",            # Google quotaId: GenerateRequestsPerDayPerProjectPerModel
    "per day",
    "per-day",
    "/day",              # quota_metric .../generate_content_daily_requests is also caught by "daily"
    "requests per day",
    "tokens per day",
    "daily",             # generic / Groq "daily limit"
    "rpd",               # Requests Per Day
    "insufficient_quota",  # OpenAI: out of credits / account quota
    "insufficient quota",
)


def _is_daily_or_account_limit(exc) -> bool:
    """True if the error looks like a daily quota OR an account/credit limit.

    Strict on purpose: matches only tokens that cannot appear in a normal
    per-minute rate-limit message. Used to override a misleading short
    retryDelay with a proper long cooldown.
    """
    text = str(exc).lower()
    return any(ind in text for ind in _DAILY_LIMIT_INDICATORS)


def _parse_duration_to_seconds(text):
    """Parse a duration EXPRESSION into seconds.

    Handles compound provider formats: "6m 11.52s" (Groq), "2h 30m", "1h",
    "45s", "2970.938289688s", and a bare number ("90" -> 90s). Returns float
    or None. IMPORTANT: only call this on a substring already isolated as a
    duration (e.g. captured right after a "retry"/"try again in" keyword) -
    never on a whole error message, or stray digits (model names, ids) would
    be misread as seconds.
    """
    if not text:
        return None
    total = 0.0
    found = False
    for val, unit in re.findall(
        r'(\d+(?:\.\d+)?)\s*(h|hr|hrs|hour|hours|m|min|mins|minute|minutes|s|sec|secs|second|seconds)?',
        text.lower(),
    ):
        if val == "":
            continue
        v = float(val)
        if unit in ("h", "hr", "hrs", "hour", "hours"):
            total += v * 3600.0
        elif unit in ("m", "min", "mins", "minute", "minutes"):
            total += v * 60.0
        else:  # s / sec / bare number -> seconds
            total += v
        found = True
    return total if found else None


# --- KEY HEALTH MANAGEMENT ---

def _get_identity_state(identity, all_keys):
    global _KAME_KEY_HEALTH
    if identity not in _KAME_KEY_HEALTH:
        _KAME_KEY_HEALTH[identity] = {"keys": {}}
    state = _KAME_KEY_HEALTH[identity]
    for k in all_keys:
        if k not in state["keys"]:
            state["keys"][k] = {
                "sick_until": 0,
                "last_used": 0,
                "request_log": [],  # timestamps of requests in 60s window
                # timestamp of the most recent transition into sickness.
                # Used ONLY by the compression-aware filter in _get_best_key.
                "last_sick_at": 0,
                # v1.0.1: consecutive rate-limit failures (resets on success).
                # Drives the adaptive backoff escalation.
                "consecutive_rl": 0,
                # v1.0.1 fix: consecutive 503/500 server failures (resets on
                # success). Separate from consecutive_rl so a server outage and a
                # quota storm never inflate each other's cooldown.
                "consecutive_server": 0,
            }
        else:
            # Defensive: backfill for keys created on earlier versions.
            state["keys"][k].setdefault("last_sick_at", 0)
            state["keys"][k].setdefault("consecutive_rl", 0)
            state["keys"][k].setdefault("consecutive_server", 0)
    return state


def _mark_key_health(identity, key, success=True, delay=20, kind="other"):
    """Update health state for a key after a completed (or failed) attempt.

    Returns the ACTUAL delay applied (after any adaptive-backoff escalation)
    so the caller can log the real cooldown.

    Design note (kept from v0.5.6):
      On success we DO append `now` to request_log here, on top of the append
      already done at selection time in `_get_best_key`. This intentionally
      gives successful keys slightly heavier "weight" in the 60s window, which
      biases `_get_best_key` toward more even dispersion across the pool.

    v1.0.1 adaptive backoff:
      For rate-limit-family failures (per_minute / daily / insufficient_quota)
      the per-key `consecutive_rl` counter escalates the cooldown
      (20s -> 40s -> ... capped at _KAME_DAILY_COOLDOWN_S). A success resets
      it. The escalation only ever RAISES the cooldown, so an honest
      per-minute retryDelay is respected on the first failure; escalation only
      bites a key that keeps failing despite waiting (the "blind daily" case).
      v1.0.1 fix: server errors (503/500) get their own gentler escalation
      (5s -> 10s -> ... capped at _KAME_SERVER_BACKOFF_CAP_S) via a separate
      `consecutive_server` counter, so a sustained outage on a big pool stops
      spinning and lets the ETA-driven sleep take over.
    """
    global _KAME_KEY_HEALTH, _KAME_CALL_COUNT
    now = time.time()
    applied = delay
    with _KAME_LOCK:
        if identity not in _KAME_KEY_HEALTH:
            return applied
        state = _KAME_KEY_HEALTH[identity]
        if key not in state["keys"]:
            return applied
        kd = state["keys"][key]
        if success:
            kd["last_used"] = now
            kd["sick_until"] = 0
            kd["request_log"].append(now)
            kd["consecutive_rl"] = 0       # rate-limit backoff reset
            kd["consecutive_server"] = 0   # server backoff reset
            _KAME_STATS["ok"] += 1
            _KAME_CALL_COUNT += 1
        else:
            applied = float(delay)
            if kind in ("per_minute", "daily", "insufficient_quota"):
                cnt = int(kd.get("consecutive_rl", 0)) + 1
                kd["consecutive_rl"] = cnt
                escalated = min(20.0 * (2 ** (cnt - 1)), _KAME_DAILY_COOLDOWN_S)
                applied = max(applied, escalated)
            elif kind == "server":
                # v1.0.1 fix: gentle escalation for a SUSTAINED 503/500 outage.
                # First failure stays ~5s; a key that keeps failing climbs
                # 5 -> 10 -> 20 -> 40 -> 80 (capped) so the pool eventually goes
                # cold and the EXHAUSTED_RETRY ETA-sleep takes over instead of
                # spinning forever. Any success resets the counter.
                cnt = int(kd.get("consecutive_server", 0)) + 1
                kd["consecutive_server"] = cnt
                escalated = min(5.0 * (2 ** (cnt - 1)), _KAME_SERVER_BACKOFF_CAP_S)
                applied = max(applied, escalated)
            kd["sick_until"] = now + applied
            kd["last_sick_at"] = now
            _KAME_STATS[kind] = _KAME_STATS.get(kind, 0) + 1
    return applied


def _extract_retry_delay(exc):
    """Extract retry-after from an API error. Falls back to 20s default.

    Order: structured attributes -> HTTP headers -> regex on message text.
    Accepted range: 0 < val <= 86400s (24h). The ceiling rejects absurd /
    parse-error values while respecting honest long waits (daily quotas).

    v1.0.1: also reads the structured ``exc.retry_delay`` (Google RetryInfo,
    a Duration-like object with .seconds/.nanos) and parses compound duration
    expressions ("6m 11.52s", "2h 30m") via _parse_duration_to_seconds.
    """
    cap = _KAME_HARD_DELAY_CAP_S

    # 1a. litellm/openai retry_after attribute (usually an int of seconds)
    retry_after = getattr(exc, "retry_after", None)
    if retry_after is not None:
        try:
            val = float(retry_after)
            if 0 < val <= cap:
                return val
        except (ValueError, TypeError):
            pass

    # 1b. v1.0.1: structured retry_delay (Google RetryInfo Duration)
    rd = getattr(exc, "retry_delay", None)
    if rd is not None:
        secs = None
        if hasattr(rd, "seconds") or hasattr(rd, "nanos"):
            try:
                secs = float(getattr(rd, "seconds", 0) or 0) + float(getattr(rd, "nanos", 0) or 0) / 1e9
            except (ValueError, TypeError):
                secs = None
        else:
            try:
                secs = float(rd)
            except (ValueError, TypeError):
                secs = _parse_duration_to_seconds(str(rd))
        if secs is not None and 0 < secs <= cap:
            return secs

    # 2. HTTP response headers
    headers = getattr(exc, "headers", None) or getattr(exc, "response_headers", None)
    if headers:
        ra = None
        if isinstance(headers, dict):
            ra = headers.get("retry-after") or headers.get("Retry-After")
        elif hasattr(headers, "get"):
            ra = headers.get("retry-after") or headers.get("Retry-After")
        if ra:
            try:
                val = float(ra)
                if 0 < val <= cap:
                    return val
            except (ValueError, TypeError):
                pass

    # 3. Parse from error message text. Capture the duration EXPRESSION right
    #    after a retry keyword, then parse it (handles "6m 11.52s", "37s",
    #    "2970.93s", "retryDelay: 1s", "Please try again in 2h 30m").
    err_msg = str(exc)
    match = re.search(
        r'(?:retry[_\s-]*(?:after|delay|in)|try\s+again\s+in)[:\s"\']*([0-9][0-9hms\.\s]*)',
        err_msg,
        re.IGNORECASE,
    )
    if match:
        dur = _parse_duration_to_seconds(match.group(1))
        if dur is not None and 0 < dur <= cap:
            return dur

    return 20  # Safe default


def _classify_error(exc):
    """Classify an error into (delay_seconds, kind, status_code).

    kind in: 'timeout', 'per_minute', 'daily', 'insufficient_quota',
             'server', 'other'.  (auth is handled separately in the loop.)

    Rate-Limit Intelligence:
    - per-minute 429: trust the provider's parsed retryDelay (exact guidance).
    - daily / account 429: the provider's retryDelay is often MISLEADINGLY
      short (e.g. Google sends "1s" on a daily-exhausted key). We detect the
      daily/account marker and floor the cooldown at _KAME_DAILY_COOLDOWN_S,
      ignoring the misleading value. (Adaptive backoff in _mark_key_health is
      the fallback when no marker is present.)
    """
    # Timeouts: the key isn't broken, just slow/busy
    if isinstance(exc, (asyncio.TimeoutError, asyncio.CancelledError)):
        return 3, "timeout", None
    err_msg = str(exc).lower()
    if "timeout" in err_msg or "timed out" in err_msg:
        return 3, "timeout", None

    status_code = getattr(exc, "status_code", None)

    # Rate limits / quota
    if status_code == 429 or any(ind in err_msg for ind in _RATE_LIMIT_INDICATORS):
        parsed = _extract_retry_delay(exc)
        if _is_daily_or_account_limit(exc):
            kind = "insufficient_quota" if "insufficient" in err_msg else "daily"
            delay = max(parsed, _KAME_DAILY_COOLDOWN_S)
            return delay, kind, (status_code or 429)
        return parsed, "per_minute", (status_code or 429)

    # Server errors: API temporarily busy, doesn't cost RPM, recovers fast
    if status_code == 503 or "service unavailable" in err_msg or "serviceunavailable" in err_msg:
        return 5, "server", (status_code or 503)
    if status_code == 500 or "internal server error" in err_msg:
        return 5, "server", (status_code or 500)

    # Everything else
    return 20, "other", status_code


def _classify_error_delay(exc):
    """Backward-compatible thin wrapper: just the delay."""
    return _classify_error(exc)[0]


def _friendly_error_msg(kind, delay, status_code=None, exc=None):
    """Build a clean, honest one-line status from the classified error.

    Shows the REAL error (status + kind + action), e.g.
      "429 per-minute -> wait 37s - next key..."
      "429 daily-quota -> cooling 1h - next key..."
      "insufficient_quota -> cooling 24h - next key..."
    """
    d = _fmt_duration(delay)
    if kind == "timeout":
        return f"⏳ timeout → retry {d} · next key..."
    if kind == "per_minute":
        sc = status_code or 429
        return f"⏳ {sc} per-minute → wait {d} · next key..."
    if kind == "daily":
        sc = status_code or 429
        return f"⏳ {sc} daily-quota → cooling {d} · next key..."
    if kind == "insufficient_quota":
        return f"⏳ insufficient_quota → cooling {d} · next key..."
    if kind == "server":
        sc = status_code or 503
        return f"⏳ {sc} server-busy → retry {d} · next key..."
    if kind == "auth":
        return f"\U0001f512 invalid key → quarantined {d}"
    name = type(exc).__name__ if exc is not None else "error"
    return f"⚠️ {name} → cooling {d} · next key..."


def _get_best_key(identity, all_keys):
    """RPM-aware predictive selection: picks the key with most remaining capacity.

    UNCHANGED from v1.0.0. When the call context is a compression call
    (Compress), keys that just transitioned out of sickness within the last
    _KAME_COMPRESS_FRESH_WINDOW_S seconds are de-prioritized - IFF at least one
    fully-rested healthy key remains.
    """
    global _KAME_KEY_HEALTH
    now = time.time()
    cutoff = now - 60  # 60-second sliding window
    is_compress_ctx = "Compress" in (_KAME_CALL_CONTEXT.get() or "")

    with _KAME_LOCK:
        state = _get_identity_state(identity, all_keys)
        pool = state["keys"]

        # 1. Clean expired request timestamps (>60s old) for all keys
        for k in all_keys:
            pool[k]["request_log"] = [t for t in pool[k]["request_log"] if t > cutoff]

        # 2. Filter healthy keys (not sick/quarantined)
        healthy = [k for k in all_keys if pool[k]["sick_until"] < now]

        if not healthy:
            # Eternal fallback: pick the one recovering soonest
            best = min(all_keys, key=lambda k: pool[k]["sick_until"])
            return best, "EXHAUSTED_RETRY"

        # 2b. Compression-aware filter (additive, never empties the pool).
        if is_compress_ctx and len(healthy) > 1:
            fresh = [
                k for k in healthy
                if (pool[k].get("last_sick_at") or 0) == 0
                   or (now - pool[k]["last_sick_at"]) > _KAME_COMPRESS_FRESH_WINDOW_S
            ]
            if fresh:
                healthy = fresh

        # 3. RPM-aware selection: fewest recent requests = most remaining capacity
        #    Tie-break: least recently used (LRU) for even spreading
        best_key = min(healthy, key=lambda k: (
            len(pool[k]["request_log"]),
            pool[k]["last_used"],
        ))

        # 4. Anti-dogpile: mark as used NOW so concurrent calls pick different keys
        pool[best_key]["last_used"] = now

        # 5. Anti-thundering-herd: count as pending NOW so concurrent threads
        #    see this key as "busier" and pick different keys instead
        pool[best_key]["request_log"].append(now)

        return best_key, "SUCCESS"


def _is_terminal_error(exc: Exception) -> bool:
    """Classify errors as terminal (don't retry) or transient (rotate key)."""
    err_msg = str(exc).lower()
    # Rate-limit indicators always mean "try another key", never terminal
    if any(ind in err_msg for ind in _RATE_LIMIT_INDICATORS):
        return False
    status_code = getattr(exc, "status_code", None)
    if isinstance(status_code, int):
        # 401 = invalid/expired key - not terminal, rotate to next
        if status_code == 401:
            return False
        if status_code in (400, 404, 422):
            return True
    if "content_policy" in err_msg or "content filter" in err_msg:
        return True
    return False


def _is_auth_error(exc: Exception) -> bool:
    """Check if this is an authentication error (invalid key)."""
    status_code = getattr(exc, "status_code", None)
    if isinstance(status_code, int) and status_code == 401:
        return True
    err_msg = str(exc).lower()
    return "unauthorized" in err_msg or "invalid api key" in err_msg or "invalid_api_key" in err_msg


def _get_all_api_keys(model_instance) -> list:
    """Extract ALL comma-separated API keys for this model's provider."""
    try:
        from helpers import dotenv as _dotenv
        if not model_instance.a0_model_conf:
            return []
        provider = model_instance.a0_model_conf.provider
        if not provider:
            return []
        raw_key = (
            _dotenv.get_dotenv_value(f"API_KEY_{provider.upper()}")
            or _dotenv.get_dotenv_value(f"{provider.upper()}_API_KEY")
            or _dotenv.get_dotenv_value(f"{provider.upper()}_API_TOKEN")
            or ""
        )
        if "," in raw_key:
            return [k.strip() for k in raw_key.split(",") if k.strip()]
        elif raw_key and raw_key != "None":
            return [raw_key]
        return []
    except Exception:
        return []


# --- THE COMMANDER ---

async def _kame_unified_call(
    self,
    system_message="",
    user_message="",
    messages: List[Any] | None = None,
    response_callback=None,
    reasoning_callback=None,
    tokens_callback=None,
    rate_limiter_callback=None,
    explicit_caching=False,
    **kwargs,
):
    from models import (
        turn_off_logging, _parse_chunk, approximate_tokens, ChatGenerationResult,
    )
    turn_off_logging()
    litellm.suppress_debug_info = True
    logging.getLogger("litellm").setLevel(logging.CRITICAL)
    logging.getLogger("openai").setLevel(logging.CRITICAL)

    provider = (self.a0_model_conf.provider if self.a0_model_conf else "unknown").lower()
    model = (self.model_name or "unknown").lower()
    identity = f"{provider}:{model}"

    all_keys = _get_all_api_keys(self)

    if not all_keys:
        # No multi-key config - fall through to original framework method
        return await self._kame_original_unified_call(
            system_message=system_message, user_message=user_message,
            messages=messages, response_callback=response_callback,
            reasoning_callback=reasoning_callback, tokens_callback=tokens_callback,
            rate_limiter_callback=rate_limiter_callback,
            explicit_caching=explicit_caching, **kwargs,
        )

    # Prevent message mutation: work on a copy
    active_msgs = list(messages) if messages else []
    if system_message:
        active_msgs.insert(0, SystemMessage(content=system_message))
    if user_message:
        active_msgs.append(HumanMessage(content=user_message))

    msgs_conv = self._convert_messages(active_msgs, explicit_caching=explicit_caching)
    stream = (reasoning_callback is not None or response_callback is not None or tokens_callback is not None)

    # Logging labels - Chat streams, Utility doesn't
    call_type = "Chat" if stream else "Util"
    model_short = model.split("/")[-1][:25]

    # Strip A0-only retry params before passing to LiteLLM
    call_kwargs: dict[str, Any] = {**self.kwargs, **kwargs}
    call_kwargs.pop("a0_retry_attempts", None)
    call_kwargs.pop("a0_retry_delay_seconds", None)

    # "Calling..." heartbeat - VERBOSE only. It shows KAME is alive during the
    # gap before a slow call returns; in normal mode we stay quiet until the
    # result line, which already implies a call happened.
    _ctx = _KAME_CALL_CONTEXT.get()
    _ctx_label = f" {_ctx}" if _ctx else ""
    if _lvl_verbose():
        PrintStyle(font_color="#85C1E9").print(
            f"[KAME] {call_type}|{model_short}{_ctx_label} ➡ Calling..."
        )

    # cascade summary tracking
    _call_started_at = time.perf_counter()
    _cooldown_overhead_s = 0.0
    _sleep_count = 0
    _long_cool_logged = False   # v1.0.1: dedupe long-outage sleep logging
    _last_sleep_log_at = 0.0    # v1.0.1: throttle near-recovery sleep logging

    attempt_no = 0
    while True:  # ETERNAL CAROUSEL - all call types use same robust rotation
        attempt_no += 1

        _select_t0 = time.perf_counter()
        key, status = _get_best_key(identity, all_keys)
        _select_ms = (time.perf_counter() - _select_t0) * 1000.0

        if status == "EXHAUSTED_RETRY":
            # v0.5.8.0 ETA-driven sleep (v1.0.1 quieter logging).
            # All keys sick. Sleep until the SOONEST key recovers (capped)
            # instead of pulsing. After sleep we `continue` so we re-select -
            # we NEVER call acompletion() with a sick key. Jitter preserved.
            import random
            _soonest_eta = _next_recovery_seconds(identity, all_keys)
            if _soonest_eta is not None and _soonest_eta > 3.0:
                # Cap at 60s so very long daily cooldowns still re-check
                # periodically, but we do not spin.
                wait = min(_soonest_eta + 0.5, 60.0) + random.uniform(0.1, 1.5)
            else:
                wait = 2.0 + random.uniform(0.1, 1.5)
            _sleep_count += 1
            _sleep_started = time.perf_counter()

            _now_t = time.time()
            _eta_known = _soonest_eta is not None
            _is_long = _eta_known and _soonest_eta > 120.0
            _wake_at = time.strftime("%H:%M:%S", time.localtime(_now_t + wait))
            _eta_label = _fmt_duration(_soonest_eta) if _eta_known else "unknown"

            if _is_long:
                # Long outage (e.g. daily quota): announce ONCE, then wait
                # quietly. Avoids ~120 log lines/hour while still showing the
                # operator that KAME is intentionally waiting (not stuck).
                if not _long_cool_logged:
                    _long_cool_logged = True
                    with _KAME_LOCK:
                        _KAME_STATS["long_sleeps"] += 1
                    if _lvl_normal():
                        PrintStyle.warning(
                            f"[KAME] {call_type}|{model_short} \U0001f4a4 All keys cooling — "
                            f"next recovery in ~{_eta_label}. Waiting quietly (no API calls), "
                            f"retry around {_wake_at}."
                        )
            else:
                # Near recovery: log each cycle but throttle to avoid spam.
                _long_cool_logged = False
                if _lvl_normal() and (_now_t - _last_sleep_log_at) >= _KAME_SLEEP_LOG_MIN_INTERVAL_S:
                    _last_sleep_log_at = _now_t
                    PrintStyle.warning(
                        f"[KAME] {call_type}|{model_short} \U0001f4a4 All keys cooling. "
                        f"Sleeping {wait:.1f}s (no API calls) — next key in ~{_eta_label} "
                        f"(wake {_wake_at})"
                    )

            await asyncio.sleep(wait)
            _cooldown_overhead_s += (time.perf_counter() - _sleep_started)
            continue   # re-select after sleep; never call API with a sick key.
        elif _lvl_verbose():
            # Additive trace line: which key was picked + selection time.
            PrintStyle(font_color="#85C1E9").print(
                f"[KAME] {call_type}|{model_short}{_ctx_label} ➡ "
                f"{_key_display(key)} picked in {_select_ms:.2f}ms"
            )

        result = ChatGenerationResult()
        got_any_chunk = False  # v1.0.1 fix: gates the re-raise in the except blocks
        _completion = None
        try:
            current_call_kwargs = {**call_kwargs, "api_key": key, "stream": stream}

            _completion = await acompletion(
                model=self.model_name, messages=msgs_conv, **current_call_kwargs
            )

            if stream:
                try:
                    _stream_iter = _completion.__aiter__()
                    while True:
                        try:
                            chunk = await _stream_iter.__anext__()
                        except StopAsyncIteration:
                            break  # Stream finished normally

                        got_any_chunk = True  # v1.0.1 fix: content has started
                        parsed = _parse_chunk(chunk)
                        output = result.add_chunk(parsed)

                        if output["reasoning_delta"]:
                            if reasoning_callback:
                                await reasoning_callback(output["reasoning_delta"], result.reasoning)
                            if tokens_callback:
                                await tokens_callback(
                                    output["reasoning_delta"],
                                    approximate_tokens(output["reasoning_delta"]),
                                )

                        if output["response_delta"]:
                            if response_callback:
                                await response_callback(
                                    output["response_delta"], result.response
                                )
                            if tokens_callback:
                                await tokens_callback(
                                    output["response_delta"],
                                    approximate_tokens(output["response_delta"]),
                                )

                    # If stream completed but produced no content, retry with next key
                    if not result.response and not result.reasoning:
                        continue

                except Exception as stream_err:
                    # Fix A (v1.0.1): NEVER swallow A0 control-flow exceptions
                    # (InterventionException etc.). Re-raise so a mid-run user
                    # message is honored without the "nudge agent" button.
                    if _KAME_PASSTHROUGH_EXC and isinstance(stream_err, _KAME_PASSTHROUGH_EXC):
                        raise
                    # Fix C (v1.0.1): if content was already streamed to the live
                    # view, a mid-stream failure must NOT rotate-and-re-stream
                    # (that re-generates the whole response from scratch on
                    # another key). Mirror vanilla A0's got_any_chunk contract:
                    # re-raise so A0 restarts the turn cleanly with intact
                    # history. Rate-limit / 503 storms fail at connect time
                    # (outer except, before any chunk), so the carousel stays
                    # fully intact for them.
                    if got_any_chunk:
                        raise
                    # Mid-stream failure before any content: smart quarantine.
                    delay, kind, sc = _classify_error(stream_err)
                    applied = _mark_key_health(identity, key, False, delay, kind)
                    if _lvl_normal():
                        PrintStyle.warning(
                            f"[KAME] {call_type}|{model_short} {_key_display(key)} "
                            f"{_friendly_error_msg(kind, applied, sc, stream_err)}"
                        )
                    continue
            else:
                parsed = _parse_chunk(_completion)
                result.add_chunk(parsed)

            _mark_key_health(identity, key, True)
            _ctx = _KAME_CALL_CONTEXT.get()
            _ctx_label = f" {_ctx}" if _ctx else ""
            _cascade = _cascade_str(attempt_no, _sleep_count, _cooldown_overhead_s)
            if _lvl_verbose():
                # Verbose success line - key id, full pool snapshot, wall time, cascade.
                _total_s = time.perf_counter() - _call_started_at
                _snap = _pool_snapshot(identity, all_keys)
                _tail = f" | {_cascade}" if _cascade else ""
                PrintStyle(font_color="#85C1E9").print(
                    f"[KAME] {call_type}|{model_short}{_ctx_label} ✅ {_key_display(key)} "
                    f"in {_total_s:.1f}s | {_snap}{_tail}"
                )
                # Periodic session summary (every ~100 successful calls).
                if _KAME_CALL_COUNT > 0 and _KAME_CALL_COUNT % 100 == 0:
                    PrintStyle(font_color="#85C1E9").print(_session_summary_line())
            elif _lvl_normal():
                # Normal success line: clean on the happy path; explicit about a
                # cascade when one happened; pool count ONLY when degraded.
                _bits = [b for b in (_cascade, _pool_snapshot_if_degraded(identity, all_keys)) if b]
                _tail = (" · " + " · ".join(_bits)) if _bits else ""
                PrintStyle(font_color="#85C1E9").print(
                    f"[KAME] {call_type}|{model_short}{_ctx_label} ✅ {_key_display(key)}{_tail}"
                )
            # silent: print nothing on a successful call.
            return result.response, result.reasoning

        except Exception as e:
            # Fix A (v1.0.1): A0 control-flow exceptions must propagate, never be
            # retried as a failed API call (restores native nudge handling). This
            # also catches a passthrough re-raised from the stream block above.
            if _KAME_PASSTHROUGH_EXC and isinstance(e, _KAME_PASSTHROUGH_EXC):
                raise
            # Fix C (v1.0.1): a got_any_chunk re-raise from the stream block lands
            # here; if content was already streamed, propagate (clean restart)
            # instead of rotating-and-re-streaming.
            if got_any_chunk:
                raise
            if _is_terminal_error(e):
                raise e

            # Auth errors: quarantine the key for a long time (likely permanently bad)
            if _is_auth_error(e):
                applied = _mark_key_health(identity, key, False, _KAME_DAILY_COOLDOWN_S, "auth")
                if _lvl_normal():
                    PrintStyle.warning(
                        f"[KAME] {call_type}|{model_short} {_key_display(key)} "
                        f"{_friendly_error_msg('auth', applied, getattr(e, 'status_code', None), e)}"
                    )
            else:
                delay, kind, sc = _classify_error(e)
                applied = _mark_key_health(identity, key, False, delay, kind)
                if _lvl_normal():
                    PrintStyle.warning(
                        f"[KAME] {call_type}|{model_short} {_key_display(key)} "
                        f"{_friendly_error_msg(kind, applied, sc, e)}"
                    )

            await asyncio.sleep(0.05)
            continue


# --- SHIELDS: COMPRESSION TIMEOUT GUARD ---

async def _kame_summarize_messages(self, messages):
    """KAME-patched Topic.summarize_messages - Trust the Connection.

    No artificial timeout: the eternal carousel rotates keys on real errors
    only. Massive compressions (90k+ tokens) run for however long the provider
    needs. On total failure, a best-effort fallback summary is generated.
    """
    _ctx_token = _KAME_CALL_CONTEXT.set('\U0001f4e6 Compress')
    try:
        msg_txt = [m.output_text() for m in messages]
        try:
            summary = await self.history.agent.call_utility_model(
                system=self.history.agent.read_prompt("fw.topic_summary.sys.md"),
                message=self.history.agent.read_prompt("fw.topic_summary.msg.md", content=msg_txt),
            )
        except Exception as e:
            PrintStyle.error(f"[KAME] Compression failed: {e}")
            summary = "[Summary unavailable - " + " | ".join(str(t)[:200] for t in msg_txt[:3]) + "]"
        return summary
    finally:
        _KAME_CALL_CONTEXT.reset(_ctx_token)


async def _kame_bulk_summarize(self):
    """KAME-patched Bulk.summarize - Trust the Connection.

    Same philosophy as _kame_summarize_messages: no artificial timeout.
    """
    _ctx_token = _KAME_CALL_CONTEXT.set('\U0001f4e6 Compress')
    try:
        content = self.output_text()
        try:
            self.summary = await self.history.agent.call_utility_model(
                system=self.history.agent.read_prompt("fw.topic_summary.sys.md"),
                message=self.history.agent.read_prompt("fw.topic_summary.msg.md", content=content),
            )
        except Exception:
            PrintStyle.error(f"[KAME] Bulk compression failed.")
            self.summary = "[Bulk summary unavailable]"
        return self.summary
    finally:
        _KAME_CALL_CONTEXT.reset(_ctx_token)


# --- SHIELD: RATE LIMITER DEADLOCK FIX ---

def _patch_rate_limiters():
    """Replace asyncio.Lock with threading.Lock on RateLimiter class."""
    try:
        from helpers.rate_limiter import RateLimiter
        import models
        _orig_init = RateLimiter.__init__

        def _kame_init(self_rl, seconds=60, **limits):
            _orig_init(self_rl, seconds, **limits)
            self_rl._lock = threading.Lock()
        RateLimiter.__init__ = _kame_init

        async def _kame_cleanup(self_rl):
            with self_rl._lock:
                now = time.time()
                cutoff = now - self_rl.timeframe
                for key in self_rl.values:
                    self_rl.values[key] = [(t, v) for t, v in self_rl.values[key] if t > cutoff]

        async def _kame_get_total(self_rl, key: str) -> int:
            with self_rl._lock:
                if key not in self_rl.values:
                    return 0
                return sum(value for _, value in self_rl.values[key])

        RateLimiter.cleanup = _kame_cleanup
        RateLimiter.get_total = _kame_get_total

        if hasattr(models, "rate_limiters"):
            for rl in models.rate_limiters.values():
                if isinstance(rl, RateLimiter) and isinstance(rl._lock, asyncio.Lock):
                    rl._lock = threading.Lock()
        return True
    except Exception:
        return False


# --- PATCH APPLICATION ---

def apply_kame_patch():
    global _KAME_PATCHED
    if _KAME_PATCHED:
        return False
    try:
        from models import LiteLLMChatWrapper
        from helpers.history import Topic, Bulk

        # Shield 1-4: API Rotation (monkey-patch unified_call)
        if not hasattr(LiteLLMChatWrapper, "_kame_original_unified_call"):
            LiteLLMChatWrapper._kame_original_unified_call = LiteLLMChatWrapper.unified_call
        LiteLLMChatWrapper.unified_call = _kame_unified_call

        # Shield 5: Compression Timeout Guard (summarize calls only)
        if not hasattr(Topic, "_kame_original_summarize_messages"):
            Topic._kame_original_summarize_messages = Topic.summarize_messages
        if not hasattr(Bulk, "_kame_original_summarize"):
            Bulk._kame_original_summarize = Bulk.summarize

        Topic.summarize_messages = _kame_summarize_messages
        Bulk.summarize = _kame_bulk_summarize

        # Shield 6: Rate Limiter Deadlock Fix
        _patch_rate_limiters()

        _KAME_PATCHED = True
        if _KAME_LOG_LEVEL != "silent":
            _print_shield_status()
        return True
    except Exception as e:
        PrintStyle.error(f"[KAME v1.0.1] Patch Failed: {e}")
        return False


def remove_kame_patch():
    """Clean uninstall: restore all original methods."""
    global _KAME_PATCHED
    try:
        from models import LiteLLMChatWrapper
        from helpers.history import Topic, Bulk
        if hasattr(LiteLLMChatWrapper, "_kame_original_unified_call"):
            LiteLLMChatWrapper.unified_call = LiteLLMChatWrapper._kame_original_unified_call
        if hasattr(Topic, "_kame_original_summarize_messages"):
            Topic.summarize_messages = Topic._kame_original_summarize_messages
        if hasattr(Bulk, "_kame_original_summarize"):
            Bulk.summarize = Bulk._kame_original_summarize
        # Best-effort: print a final session summary if anything happened.
        try:
            if _lvl_verbose() and _KAME_CALL_COUNT > 0:
                PrintStyle(font_color="#96E").print(_session_summary_line())
        except Exception:
            pass
        _KAME_PATCHED = False
        return True
    except Exception:
        return False


def _print_shield_status():
    PrintStyle(font_color="#96E").print("=" * 55)
    PrintStyle(font_color="#96E").print("  \U0001f422⚡ KAME v1.0.1 — ACTIVE")
    shields = [
        "Identity-Aware Health",
        "Eternal Carousel Rotation",
        "RPM-Aware Predictive Selection",
        "Anti-Dogpile Guard",
        "Anti-Thundering-Herd (Pending Counter)",
        "Trust the Connection (No Artificial Timeouts)",
        "KAME-Aware Compression Guard",
        "Hybrid Learning (Parsed retry-delay + ETA-driven sleep)",
        "Daily-Quota & Account-Limit Aware (multi-provider)",
        "Adaptive Backoff (provider-agnostic safety net)",
        "Rate Limiter Lock Fix",
        "Token Callback Support",
        "Friendly Error Reporting (real status + kind)",
    ]
    for s in shields:
        PrintStyle.success(f"  ✓ {s}")
    if _KAME_KEY_LOG_STYLE == "fingerprint":
        PrintStyle(font_color="#96E").print(
            "  Note: keys are shown as anonymized ids (e.g. 'k3f9a1') — NOT your real keys."
        )
    PrintStyle(font_color="#96E").print("  API Rotation — Want to donate? BTC: 36BGYhMEVFgY8PLGMVux93pjGt92KVM6dJ")
    PrintStyle(font_color="#96E").print("=" * 55)

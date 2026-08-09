"""KAME API Rotation & Stability Engine v1.0.9.

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

v1.0.9 — DELEGATED EXECUTION (the rotation brain is UNCHANGED — what changed is
who makes the network call). Up to 1.0.8 KAME re-implemented Agent Zero's whole
model call: it built the litellm request, opened the stream, parsed every chunk
and re-assembled the result. That is why each A0 release could break it: KAME was
a parallel copy of A0's most-refactored file. From 1.0.9 KAME does ONE thing —
choose the healthiest key — and then calls A0's OWN model method with that key
injected. A0 owns the request, the stream, the parsing and the result object.
- Compatibility: the fragile symbols are gone. KAME no longer touches
  ``models._parse_chunk``, ``models.ChatGenerationResult``,
  ``ChatCompletionsTransport.parse``, ``LLMResult.from_chat`` or ``litellm``
  itself. Whatever A0 does inside the call — new transport, new parser, new
  provider, new result type — KAME transports it untouched.
- Shape-based binding: the entry points are found by SIGNATURE (a coroutine on
  the model class taking messages + response/reasoning/tokens callbacks), not by
  the names ``unified_call`` / ``unified_turn``. An upstream rename no longer
  disables rotation.
- Three layers, and layer 3 is safe: 1 = bound by shape, 2 = bound by legacy
  name, 3 = KAME wraps NOTHING and says so once in the console. KAME never
  leaves Agent Zero half-patched, and never emails, notifies or phones home.
- A0's own retry loop is switched off per call (``a0_retry_attempts=0``), because
  waiting 2 attempts × 1.5s on a key KAME already knows is rate-limited is exactly
  what KAME exists to avoid. The knob names are read from A0's source at runtime,
  so a rename there is picked up automatically.

v1.0.4 — AGENT ZERO V2 COMPATIBILITY (superseded by v1.0.9's delegation: the
chunk-mode detection described below no longer exists — KAME does not parse
chunks at all any more. Kept for history):
- A0 V2 refactored model streaming into a transport layer and REMOVED
  ``models._parse_chunk``. KAME 1.0.3 hard-imported it on the first line of every
  patched call, so on A0 V2 every chat/utility call raised ImportError — the patch
  still "installed" (KAME printed ACTIVE), then failed every call. 1.0.4 detects the
  A0 version ONCE and uses ``helpers.litellm_transport.LiteLLMTransport`` on V2, or
  the legacy ``acompletion()+_parse_chunk`` path on A0 v1.x. ONE engine, BOTH A0
  majors; behavior on A0 v1.x is byte-for-byte identical to v1.0.3.
- Stream-path auth/terminal awareness: on V2 the connect happens on the FIRST
  transport chunk, so a connect-time invalid-key / terminal error now surfaces in
  the stream handler — KAME mirrors the outer handling there (quarantine+rotate a
  bad key; abort cleanly on a terminal error). Harmless no-op on v1.x.

v1.0.3 ADDITIONS (observability + faster outage recovery — the SELECTION path
``_get_best_key`` is STILL UNCHANGED; the happy path is identical to v1.0.2):
- Full raw-error log toggle (``kame_log_full_errors``, off by default): every
  failed call can ALSO print the raw exception (type, status code, retry attrs,
  FULL untruncated message) beside the classification KAME assigned (kind +
  cooldown), so the operator can VERIFY there is no misclassification (e.g. a
  503 that is really a quota/network error). Orthogonal to ``kame_log_level``
  (prints even in ``silent``); pure observability, zero behavior change.
- Precise durations: ``_fmt_duration`` now shows the seconds component under an
  hour (``90s`` / ``1m30s``) instead of rounding to the nearest minute. The old
  rounding showed the 90s server-backoff cap as a misleading "2m" (and 80s as
  "1m"); the log now states the true cooldown. Clearer failure/outage wording
  ("key cooled 1m30s · rotating to next key", "Provider outage — … will resume
  the instant a key answers").
- Fast pool recovery: when a call succeeds right after KAME had to sleep on a
  fully-cold pool (an outage just ended), the other 5xx-cooled keys are thawed
  forward to a few seconds from now, so the pool snaps back to healthy at once
  instead of trickling back one ~90s cooldown at a time ("rotate for hours, but
  resume ASAP"). Scoped to ``server`` cooldowns ONLY — daily / quota / auth
  cooldowns are never cleared by another key's success; it only ever SHORTENS a
  cooldown, never extends one or makes a key sick.

v1.0.2 FIXES (cooldown / classification / logging / interruption only — the
engine SELECTION/ROTATION path is STILL UNCHANGED; behavior with >=1 healthy
key is identical to v1.0.0/1.0.1):
- 5xx is ALWAYS classified 'server' (short, escalating), regardless of the
  error body. A transient Gemini 503 whose verbose body happened to carry
  quota/resource_exhausted/daily tokens was being routed into the daily-quota
  branch and cooling a HEALTHY key for 1h — taking the whole chat pool cold
  while the same keys stayed healthy on other models. A real daily quota is a
  429, never a 5xx, so the status code now wins.
- Interruptible cooling (the deeper "nudge" fix): while the WHOLE pool is
  cooling there is no active stream to carry A0's handle_intervention() check,
  so a user message / "nudge" was slept through. The activation extension now
  stashes the live agent; the all-keys-sick sleep is sliced and honors an
  intervention between slices, so KAME yields immediately.
- Honest waiting: the long-outage line shows the REAL earliest-recovery clock
  (not the next 60s re-check, which the old "retry around" misleadingly showed)
  and emits a periodic heartbeat instead of going fully silent.
- Gentler per-minute backoff: per-minute escalation has its OWN lower ceiling
  and trusts the provider's honest first delay; only daily/account limits floor
  at the configured daily cooldown. A healthy-but-busy RPM key is never cooled
  toward 1h.
- Empty-stream guard: an empty stream rests the key briefly before rotating, so
  an all-empty pool can't tight-spin.

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

Compatible with Agent Zero v1.14+ through the v1.x line AND Agent Zero V2 (the
transport-layer refactor). v1.0.4 auto-detects which is installed and adapts.
"""

import asyncio, contextvars, hashlib, json, os, threading, time, re
from typing import Any, List
# v1.0.9: KAME no longer imports litellm, openai or logging. It used to call
# `acompletion()` itself and silence litellm's loggers; now Agent Zero makes the
# call, so those are A0's dependencies alone and a breaking change in any of them
# can no longer stop this plugin from even importing.
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

# --- v1.0.9: single source of truth for the version string ---
# Before 1.0.9 the version was typed by hand in the banner, in the patch-failure
# line and in the docs; one of them always drifted. Everything that prints a
# version reads THIS constant now.
KAME_VERSION = "1.0.9"

# --- GLOBAL REGISTRY ---
_KAME_KEY_HEALTH = {}  # { "provider:model": { "keys": {key: {sick_until, last_used, request_log, last_sick_at, consecutive_rl}} } }
_KAME_LOCK = threading.Lock()
_KAME_PATCHED = False
_KAME_CALL_CONTEXT = contextvars.ContextVar('kame_ctx', default='')
# v1.0.2: the live A0 agent for the current async task, stashed by the activation
# extension at monologue start. Lets the all-keys-cooling sleep honor a user
# message / "nudge" (InterventionException) instead of sleeping through it.
_KAME_CURRENT_AGENT = contextvars.ContextVar('kame_agent', default=None)

# --- v1.0.9: which layer the engine engaged on (see _kame_bind_entry_points) ---
#   1 = delegating carousel bound to A0's own model entry points  (normal)
#   2 = same carousel, entry points found only by their legacy names (older A0)
#   3 = not engaged at all — A0 runs natively, KAME is out of the way
# Layer 3 is a deliberate, SAFE end state: KAME never leaves A0 half-patched.
_KAME_LAYER = 3
_KAME_BOUND_ENTRY_POINTS: list = []   # names KAME actually wrapped, for the banner

# --- v1.0.9: how many empty (no-content) answers may trigger a rotation per call.
# A0 itself returns an empty string when a provider streams nothing; KAME rotates
# once or twice in case it is a bad key, then accepts the empty answer exactly like
# native A0 would. Bounded so a legitimately blank early-stop can never loop.
_KAME_EMPTY_RETRY_BUDGET = 2

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

# --- v1.0.2: per-minute adaptive-backoff ceiling (separate from the daily one) ---
# Per-minute (RPM) limits recover in ~60s. The blind-daily safety net may still
# escalate a key that keeps failing, but a genuinely per-minute key must NOT climb
# toward the 1h daily ceiling. Cap per-minute escalation here instead.
_KAME_RL_BACKOFF_CAP_S = 300.0  # 5 min

# --- v1.0.2: heartbeat cadence while the WHOLE pool cools for a long outage
# (longer than the 60s re-check). Instead of one line then full silence, KAME
# re-states "still cooling, ~Xm left, recovery around HH:MM" this often, so the
# operator never mistakes a healthy cooldown for a hang.
_KAME_LONG_HEARTBEAT_S = 300.0  # 5 min

# Minimum real-time gap between repeated "all keys cooling" sleep log lines
# while the soonest recovery is still near (avoids per-cycle spam).
_KAME_SLEEP_LOG_MIN_INTERVAL_S = 5.0

# --- v1.0.1: key id display style (configurable) ---
# "fingerprint" (default): anonymized stable hash id, never leaks the secret.
# "prefix8": first 8 chars of the real key (recognizable, mild leak).
# "full": the whole key (debug only; leaks the secret into logs).
_KAME_KEY_LOG_STYLE = "fingerprint"

# --- v1.0.3: full raw-error logging (configurable, debug) ---
# Off by default. When ON, every failed call ALSO prints the RAW error — the
# exception type, status code, structured retry attributes, and the full
# (untruncated) message — right beside the classification KAME gave it
# (kind + applied cooldown). This is the precise-data escape hatch: it lets the
# operator VERIFY there is no misclassification (e.g. a 503 that is really a
# quota / network error). Orthogonal to `kame_log_level` and printed regardless
# of it (so `silent` + full-errors yields errors-only), since it is an explicit
# opt-in. It does NOT change any rotation/cooldown behavior — pure observability.
_KAME_LOG_FULL_ERRORS = False

# --- v1.0.3: 503-storm log collapse (configurable) ---
# On by default. During a sustained error storm (e.g. a provider-wide 503
# outage) the per-rotation failure lines are near-identical and can number in
# the hundreds (one real Gemini outage logged 1,063 of them in 83 minutes).
# When ON, at `normal` level KAME prints the FIRST failure of a storm verbatim,
# then counts repeats silently and emits ONE throttled aggregate line at most
# every _KAME_STORM_LOG_INTERVAL_S, plus one "storm over" recap on recovery.
# `verbose` still prints every line; `kame_log_full_errors` bypasses the
# collapse (you asked for everything); `auth` lines are never collapsed. PURE
# observability — the rotation/cooldown/selection path is UNCHANGED.
_KAME_COLLAPSE_STORM_LOGS = True
_KAME_STORM_LOG_INTERVAL_S = 20.0   # at most one aggregate line per storm per this
_KAME_STORM_GAP_S = 30.0            # a quiet gap longer than this ends a storm
_KAME_STORM_MIN_FOR_SUMMARY = 3     # only recap a storm that had >= this many fails
# identity -> {count, first_at, last_err_at, last_emit_at, kinds:{kind:count}}
_KAME_STORM = {}

# --- v1.0.1: lightweight in-memory session stats (for verbose summary) ---
_KAME_STATS = {
    "ok": 0, "per_minute": 0, "daily": 0, "insufficient_quota": 0,
    "server": 0, "timeout": 0, "auth": 0, "other": 0, "long_sleeps": 0,
}
_KAME_CALL_COUNT = 0


def set_log_level(level) -> None:
    """Set the log verbosity: 'silent' | 'normal' | 'verbose' | 'verbose+errors'.

    Called by the activation extension from the `kame_log_level` plugin setting.
    `verbose+errors` (v1.0.4) = full verbose output PLUS the raw exception dumped
    on every failure (it turns on _KAME_LOG_FULL_ERRORS for you), so you see the
    actual error in the Docker log instead of only KAME's one-line classification.
    The plain `verbose` level is unchanged. Invalid input keeps the current level.
    """
    global _KAME_LOG_LEVEL, _KAME_LOG_FULL_ERRORS
    s = str(level or "").strip().lower().replace(" ", "")
    if s in ("verbose+errors", "verbose_errors", "verboseerrors", "verbose+error", "debug"):
        _KAME_LOG_LEVEL = "verbose"        # behaves exactly like verbose, plus:
        _KAME_LOG_FULL_ERRORS = True       # also dump the full raw error per failure
        return
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


def set_log_full_errors(enabled) -> None:
    """Enable/disable raw full-error logging (v1.0.3, debug).

    Called by the activation extension from the `kame_log_full_errors` plugin
    setting. Accepts a bool or a truthy string ('true'/'1'/'yes'/'on'). Pure
    observability — never changes rotation or cooldown behavior.
    """
    global _KAME_LOG_FULL_ERRORS
    if isinstance(enabled, str):
        _KAME_LOG_FULL_ERRORS = enabled.strip().lower() in ("true", "1", "yes", "on")
    else:
        _KAME_LOG_FULL_ERRORS = bool(enabled)


def set_collapse_storm_logs(enabled) -> None:
    """Enable/disable 503-storm log collapse (v1.0.3).

    Called by the activation extension from the `kame_collapse_storm_logs`
    plugin setting. Accepts a bool or a truthy string ('true'/'1'/'yes'/'on').
    Pure observability — never changes rotation or cooldown behavior; only how
    many near-identical storm lines reach the log at `normal` level.
    """
    global _KAME_COLLAPSE_STORM_LOGS
    if isinstance(enabled, str):
        _KAME_COLLAPSE_STORM_LOGS = enabled.strip().lower() in ("true", "1", "yes", "on")
    else:
        _KAME_COLLAPSE_STORM_LOGS = bool(enabled)


def set_current_agent(agent) -> None:
    """Stash the live A0 agent for the current async task (v1.0.2).

    Called by the activation extension at monologue start. Lets the
    all-keys-cooling sleep honor a queued user message / "nudge" instead of
    sleeping through it (see _kame_honor_intervention). Best-effort: the
    contextvar is task-local, so concurrent agents/contexts never collide.
    """
    try:
        _KAME_CURRENT_AGENT.set(agent)
    except Exception:
        pass


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


def _key_display_auth(key: str) -> str:
    """Key display for AUTH/invalid-key events specifically (v1.0.6).

    A dead/expired key is a PERMANENT, actionable problem — you need to find
    and replace it in your provider console. The default 'fingerprint' style
    (an opaque hash like 'k3f9a1') is great for routine rotation logs (privacy)
    but useless here: you cannot look up a hash in Google/OpenAI's dashboard.
    So for this event ONLY, 'fingerprint' is upgraded to a partial reveal
    (first 10 + last 4 chars — enough to recognize the real key, not the whole
    secret). If the user already configured 'prefix8' or 'full', that choice
    is respected as-is (no change). Never affects any other log line.
    """
    if not key:
        return "------"
    if _KAME_KEY_LOG_STYLE in ("full", "prefix8"):
        return _key_display(key)
    if len(key) <= 16:
        return key
    return f"{key[:10]}...{key[-4:]}"


def _fmt_duration(seconds) -> str:
    """Human-friendly PRECISE duration: 45s / 1m30s / 2m / 1h / 1.5h.

    v1.0.3: shows the seconds component under an hour instead of rounding to the
    nearest minute. The old code rounded 90s -> "2m" and 80s -> "1m", which hid
    the true cooldown during log analysis (the 90s server-backoff cap looked
    like a deliberate "2 minutes"). Now the log states the real value.
    """
    try:
        s = float(seconds)
    except (ValueError, TypeError):
        return "?"
    if s < 60:
        return f"{s:.0f}s"
    if s < 3600:
        m, sec = divmod(int(round(s)), 60)
        return f"{m}m{sec}s" if sec else f"{m}m"
    hrs = s / 3600.0
    return f"{hrs:.0f}h" if abs(hrs - round(hrs)) < 0.05 else f"{hrs:.1f}h"


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


def _extract_quota_marker(exc) -> str:
    """Return a SHORT tag naming WHICH quota the provider reported (v1.0.6).

    Purpose: make KAME's daily/per-minute classification eyeball-verifiable in
    the normal log without dumping the raw JSON. A Gemini 429, for example,
    carries `"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"` →
    this returns "PerDay". A per-minute quota → "PerMinute". Returns "" when the
    error names no quota (nothing is appended). Never raises — logging must not
    break rotation.
    """
    try:
        text = str(exc)
        m = re.search(r'"quotaId"\s*:\s*"([^"]+)"', text)
        raw = m.group(1) if m else ""
        hay = (raw or text).lower()
        if any(t in hay for t in ("perday", "per day", "per-day", "/day",
                                  "requests per day", "tokens per day", "rpd")):
            return "PerDay"
        if any(t in hay for t in ("perminute", "per minute", "per-minute",
                                  "per min", "requests per min", "tokens per min", "rpm")):
            return "PerMinute"
        if "insufficient" in hay:
            return "InsufficientQuota"
        return raw[:40] if raw else ""
    except Exception:
        return ""


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

    Adaptive backoff (v1.0.2 — per-kind ceilings):
      The per-key `consecutive_rl` / `consecutive_server` counters escalate
      cooldowns and reset on any success.
      - daily / insufficient_quota: escalate 20s -> 40s -> ... capped at
        _KAME_DAILY_COOLDOWN_S (the classifier already floors these at the daily
        cooldown; the escalation is just a belt-and-suspenders net).
      - per_minute: the FIRST strike trusts the provider's honest retryDelay (no
        floor); repeats escalate only up to the much lower _KAME_RL_BACKOFF_CAP_S,
        so a healthy-but-busy RPM key is never cooled toward the 1h daily ceiling
        (the v1.0.1 bug). The "blind daily" case — a daily-dead key with no
        marker, classified per_minute — still escalates, just bounded here.
      - server (5xx): a separate `consecutive_server` counter escalates
        5s -> 10s -> ... capped at _KAME_SERVER_BACKOFF_CAP_S, so a sustained
        outage on a big pool stops spinning and lets the ETA-driven sleep take
        over. A transient blip still recovers in ~5s.
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
            if kind in ("daily", "insufficient_quota"):
                # Real daily / account limit: the classifier already floored this
                # at the daily cooldown; the escalation is a belt-and-suspenders
                # safety net, capped at the SAME daily ceiling.
                cnt = int(kd.get("consecutive_rl", 0)) + 1
                kd["consecutive_rl"] = cnt
                escalated = min(20.0 * (2 ** (cnt - 1)), _KAME_DAILY_COOLDOWN_S)
                applied = max(applied, escalated)
            elif kind == "per_minute":
                # v1.0.2: per-minute (RPM) keys recover in ~60s. Trust the
                # provider's honest delay on the FIRST strike (no 20s floor), and
                # if the SAME key keeps failing, escalate only up to the
                # per-minute ceiling (NOT the 1h daily one) — so a healthy-but-
                # busy RPM key is never cooled toward an hour. (The blind-daily
                # case — a daily-dead key with no marker — still escalates here,
                # just bounded at _KAME_RL_BACKOFF_CAP_S instead of 1h.)
                cnt = int(kd.get("consecutive_rl", 0)) + 1
                kd["consecutive_rl"] = cnt
                if cnt >= 2:
                    escalated = min(20.0 * (2 ** (cnt - 2)), _KAME_RL_BACKOFF_CAP_S)
                    applied = max(applied, escalated)
            elif kind == "server":
                # v1.0.1 fix: gentle escalation for a SUSTAINED 5xx outage.
                # First failure stays ~5s; a key that keeps failing climbs
                # 5 -> 10 -> 20 -> 40 -> 80 (capped) so the pool eventually goes
                # cold and the EXHAUSTED_RETRY ETA-sleep takes over instead of
                # spinning forever. Any success resets the counter.
                cnt = int(kd.get("consecutive_server", 0)) + 1
                kd["consecutive_server"] = cnt
                escalated = min(5.0 * (2 ** (cnt - 1)), _KAME_SERVER_BACKOFF_CAP_S)
                applied = max(applied, escalated)
            # v1.0.5: never SHORTEN an existing cooldown. A long daily-quota protection
            # (e.g. set by a previous daily hit) must never be overwritten by a shorter
            # server-busy (10s) or per-minute hit on the same key. Without this, a 503
            # on a daily-exhausted key would wipe the 1h protection and the key would
            # be re-probed 50 minutes too early (confirmed from log6 overnight analysis).
            kd["sick_until"] = max(kd.get("sick_until", 0), now + applied)
            kd["last_sick_at"] = now
            _KAME_STATS[kind] = _KAME_STATS.get(kind, 0) + 1
    return applied


def _thaw_server_cooled_keys(identity, exclude_key, new_cooldown=3.0):
    """Fast pool recovery after a server outage (v1.0.3).

    Called when a call SUCCEEDS right after KAME had to sleep on a fully-cold
    pool — i.e. an outage just ended. It brings every OTHER still-cooling key
    that was cooled by a 5xx (``consecutive_server > 0``) FORWARD to a few
    seconds from now, so the whole pool snaps back to healthy almost at once
    instead of trickling back one ~90s cooldown at a time. "Rotate for hours,
    but resume as soon as possible."

    Strictly scoped + conservative:
      * Only ``server``-cooled keys (consecutive_server > 0) are touched —
        daily / per-minute / quota / auth cooldowns (which are REAL per-key
        limits, tracked separately) are NEVER cleared by another key's success.
      * It only ever SHORTENS a cooldown (``min`` with the current value), never
        extends one, and never makes a key sick.
      * The SELECTION path (``_get_best_key``) is untouched. Worst case if the
        recovery was a fluke: a few keys get re-probed a bit early and re-cool —
        bounded, self-correcting.

    Returns the number of keys thawed.
    """
    now = time.time()
    thawed = 0
    with _KAME_LOCK:
        state = _KAME_KEY_HEALTH.get(identity)
        if not state:
            return 0
        for i, (k, kd) in enumerate(state["keys"].items()):
            if k == exclude_key:
                continue
            if int(kd.get("consecutive_server", 0)) > 0 and float(kd.get("sick_until", 0) or 0) > now:
                # small per-key stagger so they don't all re-probe in lockstep
                target = now + new_cooldown + (i % 5) * 0.4
                if target < float(kd["sick_until"]):
                    kd["sick_until"] = target
                    thawed += 1
    return thawed


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


# --- v1.0.8: permanently DENIED key/project (403) ---
# Distinct from an invalid key (400/401 -> `auth`) and from a quota hit (429).
# A 403 PERMISSION_DENIED means the provider is refusing this key for this
# model on purpose: the project was suspended ("Your project has been denied
# access. Please contact support."), the API was never enabled for it, or the
# model is not authorized for that key's tier. None of that clears in 20s.
# Before v1.0.8 this landed in the generic `other` bucket (20s cooldown), so a
# permanently-dead key came back to the front of the carousel three times a
# minute and burned a full round trip on EVERY user turn — confirmed in a
# 15-key production pool where one denied key was picked first on 8 consecutive
# calls. Now it is quarantined for the daily cooldown like an invalid key, so
# it is re-probed about once an hour (still self-healing if the user fixes the
# project) instead of every 20 seconds.
_PERMANENT_DENIAL_INDICATORS = (
    "permission_denied",
    "denied access",
    "consumer_suspended",
    "service_disabled",
    "api_key_service_blocked",
    "has not been used in project",
    "is disabled for this project",
)


def _is_permanent_denial(exc: Exception) -> bool:
    """True for a 403-class refusal that another 20s will not fix (v1.0.8).

    Matches on the status code (403) OR on the provider's own textual marker —
    litellm sometimes rewraps a Gemini 403 as a `BadRequestError` whose
    `status_code` is not carried over, so the text check is not redundant.
    Never matches a 429: quota is classified before this in `_classify_error`.
    """
    if getattr(exc, "status_code", None) == 403:
        return True
    err_msg = str(exc).lower()
    return any(ind in err_msg for ind in _PERMANENT_DENIAL_INDICATORS)


def _classify_error(exc):
    """Classify an error into (delay_seconds, kind, status_code).

    kind in: 'timeout', 'per_minute', 'daily', 'insufficient_quota',
             'server', 'denied', 'other'.  (auth is handled separately in the
             loop.)

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

    # Server / transient errors FIRST (v1.0.2). A 5xx is the provider being
    # momentarily overloaded, NOT the key being spent. Some providers (notably
    # Google) put quota / resource_exhausted / daily text in a 503 body; the
    # v1.0.1 order checked that text BEFORE the status code, so such a 503 fell
    # into the daily-quota branch and cooled a HEALTHY key for an hour — taking
    # the whole pool cold while the same keys stayed healthy on other models. A
    # real daily quota is a 429, never a 5xx, so the status code wins here. The
    # 'server' kind gets the gentle escalating cooldown, not the 1h daily floor.
    if status_code in (500, 502, 503, 504, 529) \
            or "service unavailable" in err_msg or "serviceunavailable" in err_msg \
            or "internal server error" in err_msg or "bad gateway" in err_msg \
            or "gateway timeout" in err_msg:
        return 5, "server", (status_code or 503)

    # Rate limits / quota (only when it is NOT an explicit server 5xx above)
    if status_code == 429 or any(ind in err_msg for ind in _RATE_LIMIT_INDICATORS):
        parsed = _extract_retry_delay(exc)
        if _is_daily_or_account_limit(exc):
            kind = "insufficient_quota" if "insufficient" in err_msg else "daily"
            # v1.0.5: always use the configured daily cooldown interval regardless of
            # Google's retryDelay. The configured 1h cap is intentional — it means
            # "test this key again every hour." Google's retryDelay for daily quotas
            # is often misleading (too short OR too long — we saw both in production).
            # The _KAME_DAILY_COOLDOWN_S setting is the single source of truth for how
            # often we probe exhausted daily-quota keys. (In v1.0.1-1.0.4 we used
            # max(parsed, _KAME_DAILY_COOLDOWN_S) which respected Google's 9h hint;
            # that caused keys to be locked out for ~10h when probing every hour was
            # both correct and what the setting was designed for.)
            delay = _KAME_DAILY_COOLDOWN_S
            return delay, kind, (status_code or 429)
        return parsed, "per_minute", (status_code or 429)

    # v1.0.8: a 403-class refusal (suspended project / API not enabled / model
    # not authorized for this key). Checked AFTER the quota branch so a 429 is
    # never mistaken for it. Quarantined for the daily cooldown, not 20s.
    if _is_permanent_denial(exc):
        return _KAME_DAILY_COOLDOWN_S, "denied", (status_code or 403)

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
    # v1.0.6: for quota errors, append the provider's own quota tag (PerDay /
    # PerMinute / ...) so the classification is verifiable at a glance — if a
    # line ever says "daily-quota" but the tag reads "[quota: PerMinute]", that
    # is a misclassification you can spot without enabling verbose+errors.
    _qm = _extract_quota_marker(exc) if kind in ("per_minute", "daily", "insufficient_quota") else ""
    _qtag = f" [quota: {_qm}]" if _qm else ""
    if kind == "timeout":
        return f"⏳ timeout → key cooled {d} · rotating to next key..."
    if kind == "per_minute":
        sc = status_code or 429
        return f"⏳ {sc} per-minute → key waits {d} · rotating to next key...{_qtag}"
    if kind == "daily":
        sc = status_code or 429
        return f"⏳ {sc} daily-quota → key cooled {d} · rotating to next key...{_qtag}"
    if kind == "insufficient_quota":
        return f"⏳ insufficient_quota → key cooled {d} · rotating to next key...{_qtag}"
    if kind == "server":
        sc = status_code or 503
        return f"⏳ {sc} server-busy → key cooled {d} · rotating to next key..."
    if kind == "auth":
        return f"\U0001f512 invalid key → quarantined {d}"
    if kind == "denied":
        sc = status_code or 403
        return (f"\U0001f6ab {sc} access denied for this key/model → quarantined {d} "
                f"(project suspended, API not enabled, or model not authorized)")
    name = type(exc).__name__ if exc is not None else "error"
    return f"⚠️ {name} → cooling {d} · next key..."


def _raw_error_detail(exc, kind=None, applied=None, status_code=None) -> str:
    """Full, untruncated raw-error dump for the debug log (v1.0.3).

    Shows the exception TYPE, status code, any structured retry attributes, and
    the COMPLETE message — beside the classification KAME assigned (kind +
    applied cooldown). Reading the raw error next to the verdict is how you spot
    a MISCLASSIFICATION (e.g. a 503 that is really a quota / network error).
    Never raises (best-effort; logging must not break rotation).
    """
    try:
        parts = [f"type={type(exc).__name__}"]
        sc = status_code if status_code is not None else getattr(exc, "status_code", None)
        if sc is not None:
            parts.append(f"status={sc}")
        if kind is not None:
            parts.append(f"classified={kind}")
        if applied is not None:
            parts.append(f"cooled={_fmt_duration(applied)}")
        for attr in ("retry_after", "retry_delay", "code"):
            v = getattr(exc, attr, None)
            if v is not None:
                parts.append(f"{attr}={v!r}")
        head = " | ".join(parts)
        body = str(exc)  # FULL — the whole point is to see everything, untruncated
        return f"{head}\n      raw: {body}"
    except Exception:
        return f"type={type(exc).__name__ if exc is not None else 'error'} (raw detail unavailable)"


def _maybe_log_full_error(call_type, model_short, key, exc, kind=None, applied=None, status_code=None) -> None:
    """Print the raw error when `kame_log_full_errors` is on (v1.0.3).

    Gated PURELY on the toggle (independent of `kame_log_level`), so it is a true
    debug escape hatch — `silent` + full-errors yields errors-only. Best-effort.
    """
    if not _KAME_LOG_FULL_ERRORS:
        return
    try:
        PrintStyle.warning(
            f"[KAME] {call_type}|{model_short} {_key_display(key)} "
            f"\U0001f50d RAW: {_raw_error_detail(exc, kind, applied, status_code)}"
        )
    except Exception:
        pass


def _storm_tick(identity, kind) -> str:
    """Count one failure into the per-identity storm; decide what to log.

    Returns 'first' (a storm just began or resumed after a quiet gap — print the
    line verbatim), 'summary' (enough time elapsed — print one aggregate line),
    or 'suppress' (collapsed — print nothing). Lock-guarded and fast; the
    aggregate text is built by the caller OUTSIDE the lock, because the pool
    snapshot takes this same (non-reentrant) lock.
    """
    now = time.time()
    with _KAME_LOCK:
        st = _KAME_STORM.get(identity)
        if st is None or (now - st["last_err_at"]) > _KAME_STORM_GAP_S:
            _KAME_STORM[identity] = {
                "count": 1, "first_at": now, "last_err_at": now,
                "last_emit_at": now, "kinds": {kind: 1},
            }
            return "first"
        st["count"] += 1
        st["last_err_at"] = now
        st["kinds"][kind] = st["kinds"].get(kind, 0) + 1
        if (now - st["last_emit_at"]) >= _KAME_STORM_LOG_INTERVAL_S:
            st["last_emit_at"] = now
            return "summary"
        return "suppress"


def _storm_summary_line(identity, all_keys, call_type, model_short):
    """Build the throttled aggregate line for an ongoing storm (call OUTSIDE lock)."""
    now = time.time()
    with _KAME_LOCK:
        st = _KAME_STORM.get(identity)
        if not st:
            return None
        count = st["count"]
        span = now - st["first_at"]
        kinds = dict(st["kinds"])
    top = max(kinds, key=kinds.get) if kinds else "error"  # dominant error kind
    eta = _next_recovery_seconds(identity, all_keys)
    eta_s = _fmt_duration(eta) if eta is not None else "?"
    snap = _pool_snapshot(identity, all_keys)
    return (
        f"[KAME] {call_type}|{model_short} \U0001f300 {top} storm "
        f"×{count} in {_fmt_duration(span)} · {snap} · "
        f"earliest recovery ~{eta_s} · (collapsed — verbose or "
        f"kame_log_full_errors shows every line)"
    )


def _storm_end(identity):
    """Close any active storm for this identity (called on a success).

    Always pops the state (so it never leaks across modes). Returns
    (count, span_seconds) when the storm that just ended had at least
    _KAME_STORM_MIN_FOR_SUMMARY failures, so the caller can print a one-line
    recovery recap; otherwise None.
    """
    now = time.time()
    with _KAME_LOCK:
        st = _KAME_STORM.pop(identity, None)
    if st and st["count"] >= _KAME_STORM_MIN_FOR_SUMMARY:
        return st["count"], now - st["first_at"]
    return None


def _log_failure(call_type, model_short, key, exc, kind, applied, sc, identity, all_keys):
    """Single funnel for a per-rotation failure line (v1.0.3 storm-collapse).

    Always runs the raw-error dump (its own toggle). Then, for the human line:
      * silent  -> nothing.
      * verbose -> every line, verbatim (full diagnostics, no collapse).
      * normal + collapse OFF (or kame_log_full_errors ON) -> every line.
      * normal + collapse ON -> first failure of a storm verbatim, repeats
        collapsed into a throttled aggregate. `auth` is never collapsed (an
        invalid key is rare and worth seeing every time).
    v1.0.8: `denied` (403) is treated exactly like `auth` here — it is a
    PERMANENT, operator-actionable problem (suspended project / API not
    enabled), so it is never collapsed and is shown even at 'silent', matching
    the documented "silent still shows hard errors" promise.
    The rotation/cooldown decision already happened in the caller; this only
    decides what reaches the log.
    """
    _maybe_log_full_error(call_type, model_short, key, exc, kind, applied, sc)
    if not _lvl_normal() and kind != "denied":
        return
    line = (
        f"[KAME] {call_type}|{model_short} "
        f"{(_key_display_auth if kind == 'denied' else _key_display)(key)} "
        f"{_friendly_error_msg(kind, applied, sc, exc)}"
    )
    if (_lvl_verbose() or not _KAME_COLLAPSE_STORM_LOGS
            or _KAME_LOG_FULL_ERRORS or kind in ("auth", "denied")):
        PrintStyle.warning(line)
        return
    decision = _storm_tick(identity, kind)
    if decision == "first":
        PrintStyle.warning(line)
    elif decision == "summary":
        summary = _storm_summary_line(identity, all_keys, call_type, model_short)
        if summary:
            PrintStyle.warning(summary)
    # 'suppress' -> collapsed; print nothing


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


# --- v1.0.3: invalid / expired KEY markers (provider-agnostic) ---
# A bad key is "terminal for the KEY", NOT for the RUN: KAME must quarantine it
# and rotate to the next key, never abort the whole run. The catch: most
# providers DON'T use 401 for this. Google/Gemini packs an invalid or expired
# key into a 400 (status INVALID_ARGUMENT, reason API_KEY_INVALID, message
# "API key not valid. Please pass a valid API key." or "API key expired. Please
# renew the API key."). A status-code-only check (401) misses these, and
# _is_terminal_error would then treat the 400 as terminal and ABORT the run on a
# single bad key in the pool. These text markers route them to the auth path.
_INVALID_KEY_INDICATORS = (
    "api key not valid",
    "api key expired",
    "api_key_invalid",
    "api key not found",
    "invalid api key",
    "invalid_api_key",
    "please renew the api key",
    "unauthorized",
)


def _is_auth_error(exc: Exception) -> bool:
    """Check if this is an authentication / invalid-key error (THIS key is bad).

    Covers a real 401 PLUS the provider variants that do NOT use 401 — notably
    Google/Gemini, which returns a 400 (reason API_KEY_INVALID, message "API key
    not valid" / "API key expired. Please renew the API key.") for a bad or
    expired key. Such a call must be handled as auth (quarantine the key + rotate
    to the next), NOT as a terminal 400 that aborts the run. (v1.0.3)
    """
    status_code = getattr(exc, "status_code", None)
    if isinstance(status_code, int) and status_code == 401:
        return True
    err_msg = str(exc).lower()
    return any(ind in err_msg for ind in _INVALID_KEY_INDICATORS)


def _is_terminal_error(exc: Exception) -> bool:
    """Classify errors as terminal (don't retry) or transient (rotate key)."""
    err_msg = str(exc).lower()
    # Rate-limit indicators always mean "try another key", never terminal
    if any(ind in err_msg for ind in _RATE_LIMIT_INDICATORS):
        return False
    # v1.0.3: an invalid/expired KEY is terminal for the key, not the run. Gemini
    # packs it into a 400; without this check the 400 branch below would abort the
    # whole run on one bad key instead of quarantining it and rotating. Let it
    # fall through to the auth path. (Checked BEFORE the 400 -> terminal rule.)
    if _is_auth_error(exc):
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


# --- v1.0.2: honor user intervention / "nudge" during retries & cooling ---

async def _kame_honor_intervention():
    """Let A0's InterventionException surface during retries / cooling (v1.0.2).

    When the user sends a message or presses "nudge" while KAME is rotating or
    sleeping on a cold pool, there is no active stream to carry A0's
    handle_intervention() check — so the message was slept through. We call it
    here against the agent stashed at monologue start, so the carousel yields
    immediately. ONLY A0 control-flow exceptions propagate; any other error from
    the check is swallowed so it can never break rotation. No-op when there is
    no agent handle or A0 lacks the method (e.g. a standalone test harness).
    """
    if not _KAME_PASSTHROUGH_EXC:
        return
    try:
        agent = _KAME_CURRENT_AGENT.get()
    except Exception:
        agent = None
    if agent is None:
        return

    # v1.0.5: honor chat PAUSE. When the user pauses the chat, A0 sets
    # context.paused = True. KAME's carousel kept running through the pause
    # because it was mid-call inside the eternal sleep loop (log6: ran 10h
    # overnight through a pause). We wait here — in short async slices so we
    # stay cooperative — until the chat is unpaused, then resume normally.
    # This never aborts the carousel or changes cooldowns; it just holds.
    try:
        ctx = getattr(agent, "context", None)
        if ctx is not None and getattr(ctx, "paused", False):
            while getattr(ctx, "paused", False):
                await asyncio.sleep(0.5)
    except Exception:
        pass

    handler = getattr(agent, "handle_intervention", None)
    if handler is None:
        return
    try:
        await handler()
    except _KAME_PASSTHROUGH_EXC:
        raise
    except Exception:
        return


# --- v1.0.9: entry-point discovery + delegation --------------------------------
#
# THE CHANGE THAT MAKES 1.0.9 WORTH SHIPPING.
#
# Up to 1.0.8 KAME re-implemented the whole model call: it built the litellm
# request itself, iterated the raw stream, parsed every chunk with the installed
# A0's parser, and re-assembled the result. That is a COPY of A0's own code living
# inside the plugin, so every A0 release that touched request building, streaming,
# chunk parsing or result assembly could break KAME — and did, repeatedly (1.0.4
# was an emergency fix for exactly that, twice).
#
# 1.0.9 stops copying. KAME now only decides WHICH KEY to use and then calls A0's
# OWN ``unified_call`` / ``unified_turn`` with that key injected. Request building,
# streaming, parsing, the early-stop contract, prompt caching and result assembly
# are A0's job again — whatever A0 does, KAME inherits.
#
# Verified against A0 v1.14, v1.20 and v2.8: all of them
#   * merge the caller's kwargs OVER ``self.kwargs`` when building the litellm call
#     (v1.x: ``{**self.kwargs, **kwargs}``; v2.8: ``_merge_litellm_call_kwargs``),
#     so passing ``api_key=<chosen>`` reliably overrides the key baked in at model
#     construction, and
#   * read a retry budget out of the same kwargs, so KAME can switch A0's internal
#     retry OFF and keep rotation instantaneous (this was the ONLY reason 1.0.4 had
#     to bypass A0's transport — A0's own retry, not the transport itself).
#
# Symbols this removes from KAME's compatibility surface entirely:
#   models._parse_chunk · models.ChatGenerationResult ·
#   helpers.litellm_transport.ChatCompletionsTransport.parse ·
#   helpers.llm_result.LLMResult.from_chat · litellm.acompletion · the whole
#   chunk-mode detection.

# Matches whatever A0 calls its "how many times do I retry internally" knobs, so a
# rename upstream (a0_retry_attempts -> a0_retry_count, ...) does not silently cost
# KAME its fast failover. Falls back to no knob at all, which is still CORRECT —
# just as slow as native A0.
_KAME_RETRY_KNOB_RE = re.compile(r"""pop\(\s*["'](a0_[a-z0-9_]*retry[a-z0-9_]*)["']""")

_KAME_RETRY_KNOBS_CACHE: dict = {}


def _kame_retry_knobs(fn) -> dict:
    """The kwargs that switch A0's OWN retry loop off, for this A0 build (cached).

    KAME rotates to a different key the instant a call fails, so A0 retrying the
    SAME key first would just add dead latency (2 attempts x 1.5s by default). We
    read the knob names straight out of A0's source instead of hardcoding them.
    Anything unexpected -> ``{}``, i.e. leave A0's retry alone.
    """
    cache_key = getattr(fn, "__qualname__", repr(fn))
    if cache_key in _KAME_RETRY_KNOBS_CACHE:
        return _KAME_RETRY_KNOBS_CACHE[cache_key]
    knobs: dict = {}
    try:
        import inspect as _inspect
        src = _inspect.getsource(_inspect.unwrap(fn))
        for name in _KAME_RETRY_KNOB_RE.findall(src):
            # attempts/count -> 0 ; delay/seconds -> 0.0
            knobs[name] = 0.0 if ("delay" in name or "second" in name) else 0
    except Exception:
        knobs = {}
    _KAME_RETRY_KNOBS_CACHE[cache_key] = knobs
    return knobs


def _kame_find_entry_points(cls) -> list:
    """Find A0's model entry points BY SHAPE, not by name (v1.0.9).

    KAME used to hardcode ``unified_call`` / ``unified_turn``. If A0 ever renames
    them, a name-based patch silently stops engaging: KAME prints ACTIVE and never
    rotates again. So we look for what the methods ARE instead — a coroutine on the
    chat wrapper that takes A0's model-call parameters:

        (messages, response_callback, reasoning_callback, tokens_callback, ...)

    Returns the attribute names, in declaration order. Empty list means "shape
    detection found nothing" and the caller falls back to the legacy names.
    """
    found = []
    try:
        import inspect as _inspect
        for name, attr in list(vars(cls).items()):
            if name.startswith("_") or name.startswith("_kame"):
                continue
            target = _inspect.unwrap(attr) if callable(attr) else None
            if target is None or not _inspect.iscoroutinefunction(target):
                continue
            try:
                params = _inspect.signature(target).parameters
            except Exception:
                continue
            # The distinguishing shape of an A0 model entry point.
            if (
                "messages" in params
                and "response_callback" in params
                and "reasoning_callback" in params
                and "tokens_callback" in params
            ):
                found.append(name)
    except Exception:
        return []
    return found


def _kame_result_is_empty(res) -> bool:
    """True when A0's answer carries no content at all (tuple OR result object).

    ``unified_call`` returns ``(response, reasoning)``; ``unified_turn`` returns an
    LLMResult with ``.response`` / ``.reasoning``. KAME reads both WITHOUT importing
    either type, so neither is part of its compatibility surface any more.
    """
    try:
        if isinstance(res, tuple):
            response = res[0] if len(res) > 0 else ""
            reasoning = res[1] if len(res) > 1 else ""
        else:
            response = getattr(res, "response", "")
            reasoning = getattr(res, "reasoning", "")
        return not (str(response or "").strip() or str(reasoning or "").strip())
    except Exception:
        return False


async def _kame_attempt_delegated(self, key, ctx):
    """ONE attempt on ONE key — executed by A0's own model method (v1.0.9).

    Two details that are NOT cosmetic:

    * ``messages`` is passed as a FRESH COPY every attempt. A0's ``unified_call``
      does ``messages.insert(0, SystemMessage(...))`` / ``messages.append(...)`` —
      it MUTATES the list it is handed. Re-using one list across carousel attempts
      would duplicate the system prompt and the user message once per rotation.
      For the same reason ``system_message`` / ``user_message`` are passed empty:
      KAME has already merged them into the list.
    * ``explicit_caching`` stays OFF, preserving the v1.0.4 behavior. Free-tier
      Gemini/Vertex keys have ZERO cached-content storage, so A0's prompt caching
      429s on cache-create for EVERY key and no amount of rotation helps.
    """
    call_kwargs = dict(ctx["kwargs"])
    call_kwargs["api_key"] = key
    call_kwargs.update(ctx["retry_knobs"])
    return await ctx["orig"](
        self,
        system_message="",
        user_message="",
        messages=list(ctx["messages"]),
        response_callback=ctx["response_callback"],
        reasoning_callback=ctx["reasoning_callback"],
        tokens_callback=ctx["tokens_callback"],
        rate_limiter_callback=ctx["rate_limiter_callback"],
        explicit_caching=False,
        **call_kwargs,
    )


# --- THE COMMANDER ---


class _KameSleepState:
    """Carries the cross-iteration sleep/cascade bookkeeping for one carousel run
    (so the ETA-driven sleep can be shared between the unified_call and the V2.1
    unified_turn carousels without duplicating the logic)."""
    __slots__ = ("sleep_count", "cooldown_overhead_s", "long_cool_logged",
                 "last_sleep_log_at", "last_long_heartbeat_at")

    def __init__(self):
        self.sleep_count = 0
        self.cooldown_overhead_s = 0.0
        self.long_cool_logged = False
        self.last_sleep_log_at = 0.0
        self.last_long_heartbeat_at = 0.0


async def _kame_sleep_on_exhaustion(identity, all_keys, call_type, model_short, st):
    """ETA-driven, interruptible sleep when every key is cooling (v0.5.8.0 / v1.0.2).

    Sleeps until the SOONEST key recovers (capped at 60s, re-checked after) instead
    of pulsing the API with sick keys. Honors a queued user message / nudge between
    short slices. Mutates `st` (a _KameSleepState). Behavior is byte-identical to the
    block originally inlined in _kame_unified_call — extracted in v1.0.4 so the new
    unified_turn carousel reuses the exact same recovery logic."""
    import random
    _soonest_eta = _next_recovery_seconds(identity, all_keys)
    if _soonest_eta is not None and _soonest_eta > 3.0:
        wait = min(_soonest_eta + 0.5, 60.0) + random.uniform(0.1, 1.5)
    else:
        wait = 2.0 + random.uniform(0.1, 1.5)
    st.sleep_count += 1
    _sleep_started = time.perf_counter()

    _now_t = time.time()
    _eta_known = _soonest_eta is not None
    _is_long = _eta_known and _soonest_eta > 120.0
    _eta_label = _fmt_duration(_soonest_eta) if _eta_known else "unknown"
    _recovery_at = (
        time.strftime("%H:%M:%S", time.localtime(_now_t + _soonest_eta))
        if _eta_known else "unknown"
    )

    if _is_long:
        _need_announce = not st.long_cool_logged
        _need_heartbeat = (
            st.long_cool_logged
            and (_now_t - st.last_long_heartbeat_at) >= _KAME_LONG_HEARTBEAT_S
        )
        if _need_announce:
            st.long_cool_logged = True
            with _KAME_LOCK:
                _KAME_STATS["long_sleeps"] += 1
        if (_need_announce or _need_heartbeat) and _lvl_normal():
            st.last_long_heartbeat_at = _now_t
            _verb = "Provider outage — all keys cooling" if _need_announce else "Still cooling"
            PrintStyle.warning(
                f"[KAME] {call_type}|{model_short} \U0001f4a4 {_verb} — earliest "
                f"recovery in ~{_eta_label} (around {_recovery_at}). Re-checking "
                f"every ~60s (no API calls); will resume the instant a key answers."
            )
    else:
        st.long_cool_logged = False
        if _lvl_normal() and (_now_t - st.last_sleep_log_at) >= _KAME_SLEEP_LOG_MIN_INTERVAL_S:
            st.last_sleep_log_at = _now_t
            PrintStyle.warning(
                f"[KAME] {call_type}|{model_short} \U0001f4a4 All keys cooling. "
                f"Sleeping {wait:.1f}s (no API calls) — earliest recovery ~{_eta_label}."
            )

    _slept = 0.0
    while _slept < wait:
        _slice = min(1.0, wait - _slept)
        await asyncio.sleep(_slice)
        _slept += _slice
        await _kame_honor_intervention()
    st.cooldown_overhead_s += (time.perf_counter() - _sleep_started)


async def _kame_carousel(self, ctx):
    """The eternal carousel: pick the healthiest key, let A0 make the call, learn.

    Every line of health tracking, cooldown maths, storm collapsing and logging in
    here is the SAME logic KAME has run since 1.0.0. What changed in 1.0.9 is one
    thing only: the attempt itself (``ctx["attempt"]``) now delegates to A0's own
    model method instead of KAME re-implementing the call.
    """
    identity = ctx["identity"]
    all_keys = ctx["all_keys"]
    call_type = ctx["call_type"]
    model_short = ctx["model_short"]

    _ctx = _KAME_CALL_CONTEXT.get()
    _ctx_label = f" {_ctx}" if _ctx else ""

    # "Calling..." heartbeat - VERBOSE only. It shows KAME is alive during the
    # gap before a slow call returns; in normal mode we stay quiet until the
    # result line, which already implies a call happened.
    if _lvl_verbose():
        PrintStyle(font_color="#85C1E9").print(
            f"[KAME] {call_type}|{model_short}{_ctx_label} ➡ Calling..."
        )

    _call_started_at = time.perf_counter()
    st = _KameSleepState()          # sleep_count + cooldown_overhead_s + log throttles
    _empty_counts = {}              # v1.0.6: per-key empty-answer count this call
    _empty_budget = _KAME_EMPTY_RETRY_BUDGET

    attempt_no = 0
    while True:  # ETERNAL CAROUSEL - all call types use the same robust rotation
        attempt_no += 1

        # v1.0.2: once past the clean first attempt (i.e. we are rotating or
        # cooling), honor a queued user message / "nudge" so KAME never sleeps
        # through an intervention. The happy path (first attempt) is untouched.
        if attempt_no > 1:
            await _kame_honor_intervention()

        _select_t0 = time.perf_counter()
        key, status = _get_best_key(identity, all_keys)
        _select_ms = (time.perf_counter() - _select_t0) * 1000.0

        if status == "EXHAUSTED_RETRY":
            # v0.5.8.0 ETA-driven sleep (v1.0.2: honest logging + interruptible).
            # All keys sick. Sleep until the SOONEST key recovers (capped) instead
            # of pulsing the API with sick keys. After the sleep we `continue` so we
            # re-select - we NEVER call the model with a key we know is cooling.
            await _kame_sleep_on_exhaustion(identity, all_keys, call_type, model_short, st)
            continue
        elif _lvl_verbose():
            # Additive trace line: which key was picked + selection time.
            PrintStyle(font_color="#85C1E9").print(
                f"[KAME] {call_type}|{model_short}{_ctx_label} ➡ "
                f"{_key_display(key)} picked in {_select_ms:.2f}ms"
            )

        # v1.0.9: reset the per-attempt progress flag. ctx["progress"] is flipped by
        # the callback shims the moment A0 streams anything to the UI, which is how
        # KAME still knows a failure happened MID-stream (it no longer owns the
        # stream, so it cannot see chunks directly).
        ctx["progress"]["any"] = False

        try:
            result = await ctx["attempt"](self, key, ctx)

            # v1.0.6 / v1.0.9: an answer with no content at all. Usually a transient
            # provider hiccup or a safety-filtered completion - the KEY is fine - so
            # the first empty from a key never penalizes it, we just rotate on. A
            # SECOND empty from the SAME key this call rests it 3s. The whole thing
            # is bounded by _empty_budget: once spent, KAME returns the empty answer
            # exactly like native A0 would, so this can never turn into a loop.
            #
            # `not progress["any"]` is what keeps the v1.0.8 early-stop contract
            # intact: if ANY callback fired, content really did stream and A0
            # deliberately returned a blank result (a blank early stop). That is a
            # valid answer from a HEALTHY key - never rotate on it. Only a call
            # where literally nothing streamed can be a dead-key symptom.
            if (_empty_budget > 0 and not ctx["progress"]["any"]
                    and _kame_result_is_empty(result)):
                _empty_budget -= 1
                _empty_counts[key] = _empty_counts.get(key, 0) + 1
                if _empty_counts[key] >= 2:
                    _mark_key_health(identity, key, False, 3, "other")
                    if _lvl_verbose():
                        PrintStyle.warning(
                            f"[KAME] {call_type}|{model_short} {_key_display(key)} "
                            f"⚠️ empty answer ×{_empty_counts[key]} → rest 3s · next key..."
                        )
                elif _lvl_verbose():
                    PrintStyle.warning(
                        f"[KAME] {call_type}|{model_short} {_key_display(key)} "
                        f"⚠️ empty answer → retry (key not penalized)..."
                    )
                await asyncio.sleep(0)
                continue

            _mark_key_health(identity, key, True)
            # v1.0.3: a success ends any error storm — close it and, in collapse
            # mode, print a one-line recap so the operator sees how big it was.
            # (_storm_end always pops the state; it returns a recap only for a
            # storm of >= _KAME_STORM_MIN_FOR_SUMMARY failures.)
            _storm_recap = _storm_end(identity)
            if (_storm_recap and _lvl_normal() and not _lvl_verbose()
                    and _KAME_COLLAPSE_STORM_LOGS and not _KAME_LOG_FULL_ERRORS):
                _sc_n, _sc_span = _storm_recap
                PrintStyle.success(
                    f"[KAME] {call_type}|{model_short} ☀️ storm over — "
                    f"{_sc_n} failures over {_fmt_duration(_sc_span)} · resuming"
                )
            # v1.0.3: if this success ended an outage (we slept on a fully-cold
            # pool at least once), thaw the other server-cooled keys so the pool
            # snaps back fast instead of trickling. Scoped to 5xx cooldowns only.
            if st.sleep_count > 0:
                _thawed = _thaw_server_cooled_keys(identity, key)
                if _thawed and _lvl_normal():
                    PrintStyle.success(
                        f"[KAME] {call_type}|{model_short} ☀️ recovery — thawed "
                        f"{_thawed} server-cooled key{'s' if _thawed != 1 else ''} for fast pool refill"
                    )
            _ctx = _KAME_CALL_CONTEXT.get()
            _ctx_label = f" {_ctx}" if _ctx else ""
            _cascade = _cascade_str(attempt_no, st.sleep_count, st.cooldown_overhead_s)
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
            return result

        except Exception as e:
            # Fix A (v1.0.1): A0 control-flow exceptions must propagate, never be
            # retried as a failed API call (restores native nudge handling). With
            # delegation these are raised by A0's own streaming callbacks, exactly
            # as they are without KAME installed.
            if _KAME_PASSTHROUGH_EXC and isinstance(e, _KAME_PASSTHROUGH_EXC):
                raise
            # Only a genuinely TERMINAL error surfaces — everything else (incl. a
            # transient mid-stream drop) is cooled + rotated, never re-raised
            # (the v1.0.4 eternal-carousel promise).
            if _is_terminal_error(e):
                raise e
            if ctx["progress"]["any"] and _lvl_normal():
                PrintStyle.warning(
                    f"[KAME] {call_type}|{model_short} {_key_display(key)} "
                    f"⚠️ mid-stream drop after partial output → rotating + retrying"
                )

            # Auth errors: quarantine the key for a long time (likely permanently bad)
            if _is_auth_error(e):
                _auth_sc = getattr(e, "status_code", None)
                applied = _mark_key_health(identity, key, False, _KAME_DAILY_COOLDOWN_S, "auth")
                # v1.0.6: an invalid/expired key is a PERMANENT, actionable problem —
                # always shown, even at 'silent' (matches the documented "silent still
                # shows hard errors" promise).
                PrintStyle.warning(
                    f"[KAME] {call_type}|{model_short} {_key_display_auth(key)} "
                    f"{_friendly_error_msg('auth', applied, _auth_sc, e)}"
                )
                _maybe_log_full_error(call_type, model_short, key, e, "auth", applied, _auth_sc)
            else:
                delay, kind, sc = _classify_error(e)
                applied = _mark_key_health(identity, key, False, delay, kind)
                _log_failure(call_type, model_short, key, e,
                             kind, applied, sc, identity, all_keys)

            # v1.0.6: rotate to the next key IMMEDIATELY. asyncio.sleep(0) yields to
            # the event loop (so we never spin the CPU or starve other tasks) without
            # any wall-clock delay: the failed key is already marked sick, so the next
            # iteration picks a DIFFERENT key, and once all keys are sick
            # _get_best_key returns EXHAUSTED_RETRY and the ETA-driven sleep takes
            # over. Net effect: near-instant failover.
            await asyncio.sleep(0)
            continue


def _kame_wrap_callbacks(ctx, response_callback, reasoning_callback, tokens_callback):
    """Shim A0's streaming callbacks so KAME can tell "nothing streamed yet" from
    "it died halfway through the answer" (v1.0.9).

    KAME no longer owns the stream, so it cannot count chunks. It watches the
    callbacks instead. The shims are transparent: the RETURN VALUE of
    ``response_callback`` is passed straight back, which is what carries A0's
    early-stop signal ("a complete tool request has streamed, stop now").
    """
    progress = ctx["progress"]

    if response_callback is None:
        wrapped_response = None
    else:
        async def wrapped_response(delta, full):
            progress["any"] = True
            return await response_callback(delta, full)

    if reasoning_callback is None:
        wrapped_reasoning = None
    else:
        async def wrapped_reasoning(delta, full):
            progress["any"] = True
            return await reasoning_callback(delta, full)

    if tokens_callback is None:
        wrapped_tokens = None
    else:
        async def wrapped_tokens(text, tokens):
            progress["any"] = True
            return await tokens_callback(text, tokens)

    return wrapped_response, wrapped_reasoning, wrapped_tokens


def _kame_make_entry_wrapper(entry_name, original):
    """Build KAME's replacement for ONE A0 model entry point (v1.0.9).

    The same wrapper serves ``unified_call`` (returns a ``(response, reasoning)``
    tuple) and ``unified_turn`` (returns an LLMResult) — and any future entry point
    with the same shape — because KAME returns A0's own result object untouched
    instead of rebuilding it.
    """

    async def _kame_entry(
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
        provider = (self.a0_model_conf.provider if getattr(self, "a0_model_conf", None) else "unknown").lower()
        model = (getattr(self, "model_name", "") or "unknown").lower()
        identity = f"{provider}:{model}"

        all_keys = _get_all_api_keys(self)

        if not all_keys:
            # No multi-key config - nothing to rotate. Hand the call straight back
            # to A0, unchanged, as if KAME were not installed.
            return await original(
                self,
                system_message=system_message, user_message=user_message,
                messages=messages, response_callback=response_callback,
                reasoning_callback=reasoning_callback, tokens_callback=tokens_callback,
                rate_limiter_callback=rate_limiter_callback,
                explicit_caching=explicit_caching, **kwargs,
            )

        # Merge system/user into ONE message list here, once. Each attempt then gets
        # a fresh copy of it (A0 mutates the list it is given - see
        # _kame_attempt_delegated) and empty system_message/user_message, so a
        # rotation can never duplicate the prompt.
        active_msgs = list(messages) if messages else []
        if system_message:
            active_msgs.insert(0, SystemMessage(content=system_message))
        if user_message:
            active_msgs.append(HumanMessage(content=user_message))

        stream = (
            reasoning_callback is not None
            or response_callback is not None
            or tokens_callback is not None
        )

        # Logging labels - Chat streams, Utility doesn't
        call_type = "Chat" if stream else "Util"
        model_short = model.split("/")[-1][:25]

        # v1.0.4 (A0 V2.1): never let prompt caching reach the provider — free-tier
        # keys have zero cached-content storage and 429 on cache-create, on every
        # key, so rotation cannot help. explicit_caching=False is forced in
        # _kame_attempt_delegated; strip the raw flag here too in case a caller set
        # it directly.
        forwarded = dict(kwargs)
        forwarded.pop("a0_explicit_prompt_caching", None)

        ctx = {
            "identity": identity,
            "all_keys": all_keys,
            "call_type": call_type,
            "model_short": model_short,
            "orig": original,
            "attempt": _kame_attempt_delegated,
            "messages": active_msgs,
            "kwargs": forwarded,
            "rate_limiter_callback": rate_limiter_callback,
            "retry_knobs": _kame_retry_knobs(original),
            "progress": {"any": False},
        }
        (ctx["response_callback"],
         ctx["reasoning_callback"],
         ctx["tokens_callback"]) = _kame_wrap_callbacks(
            ctx, response_callback, reasoning_callback, tokens_callback
        )

        return await _kame_carousel(self, ctx)

    _kame_entry.__name__ = f"_kame_{entry_name}"
    _kame_entry.__qualname__ = f"_kame_{entry_name}"
    _kame_entry.__doc__ = (
        f"KAME rotation carousel wrapping Agent Zero's {entry_name}(). "
        f"Selects the healthiest API key, then delegates the actual call to A0."
    )
    return _kame_entry


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

# v1.0.9: the entry points KAME looked for by name before shape-detection existed.
# Only used as the layer-2 fallback (see _kame_bind_entry_points).
_KAME_LEGACY_ENTRY_NAMES = ("unified_turn", "unified_call")


def _kame_bind_entry_points(cls) -> int:
    """Wrap A0's model entry points with KAME's carousel. Returns the layer engaged.

    Layer 1 — found the entry points BY SHAPE (a coroutine on the model class that
              takes messages + the three streaming callbacks). This is what keeps
              working when upstream renames `unified_call`/`unified_turn`.
    Layer 2 — shape detection found nothing (A0 refactored the signature); fall back
              to the two historical names.
    Layer 3 — neither worked. Nothing is wrapped and the caller must leave A0 exactly
              as it found it.

    Binding is all-or-nothing per entry point and each original is stashed under
    `_kame_original_<name>` so `remove_kame_patch()` can always undo it.
    """
    global _KAME_BOUND_ENTRY_POINTS

    names = _kame_find_entry_points(cls)
    layer = 1
    if not names:
        names = [n for n in _KAME_LEGACY_ENTRY_NAMES if callable(getattr(cls, n, None))]
        layer = 2
    if not names:
        _KAME_BOUND_ENTRY_POINTS = []
        return 3

    bound = []
    for name in names:
        try:
            original = getattr(cls, name)
            stash = f"_kame_original_{name}"
            if not hasattr(cls, stash):
                setattr(cls, stash, original)
            # Re-binding after a hot reload must wrap the ORIGINAL, never a wrapper.
            setattr(cls, name, _kame_make_entry_wrapper(name, getattr(cls, stash)))
            bound.append(name)
        except Exception:
            continue

    _KAME_BOUND_ENTRY_POINTS = bound
    return layer if bound else 3


def _kame_unbind_entry_points(cls) -> None:
    """Restore every entry point KAME wrapped (used by both uninstall and rollback)."""
    global _KAME_BOUND_ENTRY_POINTS
    for name in list(_KAME_BOUND_ENTRY_POINTS) or list(_KAME_LEGACY_ENTRY_NAMES):
        stash = f"_kame_original_{name}"
        if hasattr(cls, stash):
            try:
                setattr(cls, name, getattr(cls, stash))
            except Exception:
                pass
    _KAME_BOUND_ENTRY_POINTS = []


def apply_kame_patch():
    global _KAME_PATCHED, _KAME_LAYER
    if _KAME_PATCHED:
        return False

    # --- CORE (Shields 1-4): rotation. If this fails, KAME does not install. ---
    try:
        from models import LiteLLMChatWrapper
        _KAME_LAYER = _kame_bind_entry_points(LiteLLMChatWrapper)
    except Exception as e:
        _KAME_LAYER = 3
        PrintStyle.error(f"[KAME v{KAME_VERSION}] Patch Failed: {e}")
        return False

    if _KAME_LAYER == 3:
        # v1.0.9: layer 3 is a DELIBERATE, SAFE end state. We could not identify
        # A0's model entry points, so we wrap nothing at all — Agent Zero runs
        # natively and the user loses rotation, but never gets a half-patched,
        # error-spewing runtime. Announced once, in the console, on purpose:
        # KAME does not send mail and does not phone home.
        PrintStyle.warning(
            f"[KAME v{KAME_VERSION}] This Agent Zero build changed its model layer "
            f"in a way KAME does not recognize, so KAME stayed OUT of the way — "
            f"Agent Zero is running natively and unmodified (no rotation). "
            f"Please report this at https://github.com/Kame696/kame-api-engine/issues"
        )
        return False

    # --- ACCESSORY SHIELDS: each isolated. A failure here degrades one feature, ---
    # --- never the rotation core, and never leaves A0 half-patched (v1.0.9).    ---
    try:
        # Shield 5: Compression Timeout Guard (summarize calls only)
        from helpers.history import Topic, Bulk
        if not hasattr(Topic, "_kame_original_summarize_messages"):
            Topic._kame_original_summarize_messages = Topic.summarize_messages
        if not hasattr(Bulk, "_kame_original_summarize"):
            Bulk._kame_original_summarize = Bulk.summarize
        Topic.summarize_messages = _kame_summarize_messages
        Bulk.summarize = _kame_bulk_summarize
    except Exception:
        pass

    try:
        # Shield 6: Rate Limiter Deadlock Fix
        _patch_rate_limiters()
    except Exception:
        pass

    _KAME_PATCHED = True
    # v1.0.8: the banner is COSMETIC — it must never decide whether KAME is
    # considered installed. On a non-UTF-8 console (a native Windows run
    # with a cp1252 code page) the emoji in the shield banner raises
    # UnicodeEncodeError; before this guard that exception escaped into the
    # outer handler, which printed "Patch Failed" and returned False even
    # though every patch above had already been applied successfully.
    if _KAME_LOG_LEVEL != "silent":
        try:
            _print_shield_status()
        except Exception:
            pass
    return True


def remove_kame_patch():
    """Clean uninstall: restore all original methods.

    v1.0.9: mirrors apply_kame_patch()'s isolation. Each revert stands alone, so
    one unimportable module can no longer leave the model entry points wrapped
    while the plugin folder is being deleted — which is the one state that WOULD
    break a running Agent Zero. Returns True only if the rotation core came off.
    """
    global _KAME_PATCHED, _KAME_LAYER
    unbound = False
    try:
        from models import LiteLLMChatWrapper
        _kame_unbind_entry_points(LiteLLMChatWrapper)
        unbound = True
    except Exception:
        pass

    try:
        from helpers.history import Topic, Bulk
        if hasattr(Topic, "_kame_original_summarize_messages"):
            Topic.summarize_messages = Topic._kame_original_summarize_messages
        if hasattr(Bulk, "_kame_original_summarize"):
            Bulk.summarize = Bulk._kame_original_summarize
    except Exception:
        pass

    _KAME_LAYER = 3
    _KAME_PATCHED = False

    # Best-effort: print a final session summary if anything happened.
    try:
        if _lvl_verbose() and _KAME_CALL_COUNT > 0:
            PrintStyle(font_color="#96E").print(_session_summary_line())
    except Exception:
        pass
    return unbound


def _kame_a0_version() -> str:
    """The Agent Zero version KAME is running inside, or '' when it cannot tell."""
    try:
        from helpers.git import get_version
        v = str(get_version() or "").strip()
        return "" if v in ("", "unknown") else v
    except Exception:
        pass
    try:
        from helpers.git import get_git_info
        return str((get_git_info() or {}).get("short_tag", "") or "").strip()
    except Exception:
        return ""


def _kame_verified_against() -> str:
    """The A0 version this KAME build was actually tested against (a0_compat.json)."""
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "a0_compat.json"), "r", encoding="utf-8") as fh:
            return str(json.load(fh).get("verified_against", "") or "").strip()
    except Exception:
        return ""


def _print_shield_status():
    PrintStyle(font_color="#96E").print("=" * 55)
    PrintStyle(font_color="#96E").print(f"  \U0001f422⚡ KAME v{KAME_VERSION} — ACTIVE")
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
        "Agent Zero V2.1+ Aware (turn-based calls + free-tier cache-safe)",
        "Rate Limiter Lock Fix",
        "Token Callback Support",
        "Friendly Error Reporting (real status + kind)",
        "Delegated Execution (Agent Zero makes the call, KAME picks the key)",
    ]
    for s in shields:
        PrintStyle.success(f"  ✓ {s}")

    # --- v1.0.9: how KAME attached itself, and to what. Console only. -----------
    # This block is the ENTIRE "is it still compatible?" report. There is no email,
    # no webhook, no telemetry and no background check — if you can see the banner,
    # you have the answer.
    try:
        _bound = ", ".join(_KAME_BOUND_ENTRY_POINTS) or "none"
        if _KAME_LAYER == 1:
            PrintStyle.success(
                f"  ✓ Bound to Agent Zero's model layer by shape: {_bound}"
            )
        elif _KAME_LAYER == 2:
            PrintStyle.warning(
                f"  ! Bound by legacy name: {_bound} — this Agent Zero build changed "
                f"its model signature. Rotation is fully active; please report it."
            )
        _a0 = _kame_a0_version()
        _verified = _kame_verified_against()
        if _a0 and _verified and _a0 != _verified:
            PrintStyle(font_color="#96E").print(
                f"  Agent Zero {_a0} detected (this KAME was verified on {_verified}). "
                f"KAME delegates the call to Agent Zero itself, so this is normally fine."
            )
    except Exception:
        pass

    if _KAME_KEY_LOG_STYLE == "fingerprint":
        PrintStyle(font_color="#96E").print(
            "  Note: keys are shown as anonymized ids (e.g. 'k3f9a1') — NOT your real keys."
        )
    PrintStyle(font_color="#96E").print("  API Rotation — Want to donate? BTC: 36BGYhMEVFgY8PLGMVux93pjGt92KVM6dJ")
    PrintStyle(font_color="#96E").print("=" * 55)

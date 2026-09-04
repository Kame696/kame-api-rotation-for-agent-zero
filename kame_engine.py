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
KAME_VERSION = "1.6.0.4"

# --- GLOBAL REGISTRY ---
_KAME_KEY_HEALTH = {}  # { "provider:model": { "keys": {key: {sick_until, last_used, request_log, last_sick_at, consecutive_rl}} } }
_KAME_LOCK = threading.Lock()

#: The longest rest each ``provider:model`` has ever been **told** to take on a
#: throttle. v1.6.0.3, and it is not a cooldown — nothing rests for it. It is
#: the answer to "what does this provider's short window actually look like",
#: used only when a later throttle arrives naming no number at all.
#:
#: Google is why it exists. Its free-tier 429 comes in two wordings: spelled
#: out with ``Please retry in Ns``, and terse — ``Resource has been exhausted
#: (e.g. check quota).`` — with nothing at all. They are the same condition,
#: and in a 46-minute Hermes run 168 arrived spelled out (never above 59.8s)
#: and 232 arrived terse. Inventing a number for the terse ones while holding
#: 168 measurements of the same window is not caution.
#:
#: Read and written under :data:`_KAME_LOCK`. Only throttles teach it, and
#: only ones at or under :data:`_KAME_RL_BACKOFF_CAP_S`: a *daily* cap on
#: Gemini also classifies as a rate limit and arrives sized at an hour, and
#: one exhausted day must not teach every terse throttle afterwards to rest
#: for an hour.
_KAME_STATED_RL = {}  # { "provider:model": float seconds }
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

# --- v1.6.0.1: the wait, said before ninety seconds have passed --------------
#
# The chat item in `_kame_wait_notice_tick` waits ninety seconds because it
# writes a permanent row into the conversation, and a row for every twenty-
# second throttle would bury the conversation in bookkeeping. That threshold is
# right for what it guards and wrong as the only surface: with a real pool the
# whole thing is rarely cold for ninety seconds at once, so in practice the
# notice almost never appeared and the owner's reasonable conclusion was that
# nothing was working.
#
# Agent Zero's notification manager is the better home for the early part of a
# wait: `NotificationType.PROGRESS`, a stable id so it UPDATES in place instead
# of stacking, and `add_notification` marks the WebUI state dirty, so it is
# pushed over the WebSocket rather than waiting for a poll. Fifteen seconds is
# long enough that a normal rotation never raises one — a rotation is
# milliseconds — and short enough that a person who has started to wonder is
# answered before they reach for the restart.
_KAME_NOTICE_TOAST_AFTER_S = 15.0

# --- v1.6.0.1: a refusal is not a clock -------------------------------------
#
# KAME's cooldowns divide in two, and until this release both got the same hour:
#
#   CLOCKS   — a per-minute throttle, a daily cap, an account allowance. The
#              provider is metering time, and only time helps.
#   REFUSALS — a 401, a revoked key, a 403 saying this key may not have this
#              model. The provider is describing the credential. Waiting fixes
#              nothing.
#
# The reasoning for giving a refusal the daily hour was that since waiting
# cannot repair a refused key, the length hardly matters. It matters, because
# the two ways of being wrong are not the same size:
#
#   too long  — the provider had an incident, or the 401 was transient
#               → costs a HEALTHY KEY, for an hour
#   too short — the key really is dead
#               → costs ONE REQUEST, refused in milliseconds, never metered
#
# Twenty seconds is not an invented number: it is the base the escalation ladder
# in `_mark_key_health` already applies to this kind. Measured side by side with
# the five minutes tried first, the invented value did no work at all —
#
#     base  20s ->  20  40  80 160 320 640 1280 2560 3600 ...
#     base 300s -> 300 300 300 300 320 640 1280 2560 3600 ...
#
# — both reach the hourly re-probe at the same point, and all the larger base
# bought was flattening the first four strikes at five minutes, precisely the
# window in which a re-check is most likely to find a transient refusal already
# cleared.
#
# WARNING: this shorter bench is WRONG without the demotion in `_get_best_key`.
# A key that answered 401 comes back with an empty request window and the oldest
# `last_used` in the pool, which is exactly the profile the least-loaded /
# least-recently-used rule reaches for — so the one key known not to work would
# be the FIRST one tried, every twenty seconds. There is a test that fails when
# the demotion is removed.
_KAME_REFUSAL_REST_S = 20.0

# How many consecutive refusals, with NO successful call in between, take a
# credential out of rotation. Any success — or any failure of another kind —
# resets the count to zero.
#
# Three, not one, because a bare 401 is ambiguous: it is what a proxy, a
# gateway, and an OAuth token one second from refreshing all produce. A
# provider that has genuinely retired a key says so in words, and that case
# does not wait for three (see `_KAME_RETIRE_ON_SIGHT_KINDS`).
_KAME_REFUSALS_BEFORE_RETIRING = 3

# Kinds that can take a key out of rotation at all.
#
# `denied` is deliberately ABSENT, and this is the most expensive line in the
# file to get wrong. A 403 saying "this key may not use THIS MODEL" is about the
# PAIRING — a suspended project, an API never switched on, a model outside the
# tier the key pays for. The same credential is very often the healthiest one in
# the account on every other model, and retiring it would throw away a working
# key over a permission that was never about the key. The Hermes port shipped
# exactly that bug for one release: a model-scoped 403 reached the retirement
# check labelled `auth`, and three of them retired a credential that worked
# everywhere else.
_KAME_RETIRING_KINDS = frozenset({"auth", "revoked"})

# ...and the kind that does not wait for three. `revoked` means the provider
# used the words — "API key not valid", "invalid api key", "key is no longer
# valid". That is not ambiguous and one is enough.
_KAME_RETIRE_ON_SIGHT_KINDS = frozenset({"revoked"})

# --- v1.0.2: heartbeat cadence while the WHOLE pool cools for a long outage
# (longer than the 60s re-check). Instead of one line then full silence, KAME
# re-states "still cooling, ~Xm left, recovery around HH:MM" this often, so the
# operator never mistakes a healthy cooldown for a hang.
_KAME_LONG_HEARTBEAT_S = 300.0  # 5 min

# Minimum real-time gap between repeated "all keys cooling" sleep log lines
# while the soonest recovery is still near (avoids per-cycle spam).
_KAME_SLEEP_LOG_MIN_INTERVAL_S = 5.0

# --- v1.2.0: the wait, said where the user is actually looking ---
#
# Everything above writes to the console. That is the right place for an
# operator reading a Docker log and the wrong place for the person watching the
# chat: to them a pool that is waiting out a daily quota is an agent that has
# stopped, with no way to tell it from a hang. ADR 0002 removed the rotation
# ceiling and named this as what it leaves open — "the user typically restarts
# A0 in that scenario" — and a restart is not a decision the user made, it is
# one the silence made for them.
#
# So the same facts the console already gets are put into the chat as ONE log
# item that keeps updating: how many keys are resting, when the earliest is
# expected, how long the wait has run, and that stop cancels. Counts and
# fingerprints only, never a key, so the line is safe in a screenshot.
_KAME_WAIT_NOTICE = True
# A short wait says nothing at all. Under this, the pool recovers before anybody
# has time to wonder, and a notice would be noise on every rotation.
_KAME_WAIT_NOTICE_AFTER_S = 90.0
# How often the one item is refreshed once it is on screen. It is an update to
# an existing item, not a new message, so this is a countdown rather than spam.
_KAME_WAIT_NOTICE_REFRESH_S = 10.0

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
    # v1.6.0.1: the three refusals counted apart, because grouping them answers
    # "is KAME reading these" and cannot answer the question an owner asks
    # first — WHAT keeps happening, and is waiting even the right answer to it.
    "revoked": 0, "denied": 0,
    # How many times a credential left rotation this session. Zero is the only
    # value that needs no explanation.
    "retired": 0,
}
_KAME_CALL_COUNT = 0

# --- v1.6.0.1: where each cooldown actually came from ------------------------
#
# Counting failures answers "is anything going wrong". It cannot answer the
# question that matters after an upstream change: **is KAME still reading these,
# or is it guessing?** A provider that renames a field, or a host that stops
# forwarding a body, does not raise anything — it just quietly moves every
# refusal into the generic bucket, and the plugin goes on rotating with a number
# it invented. Nothing about that looks broken from the outside.
#
# So every failure is filed under where its deadline came from:
#
#   provider — the number is the provider's own (Retry-After, RetryInfo,
#              a duration in the sentence). The best case.
#   kame     — KAME recognised the KIND and applied its own rule (a daily cap
#              gets an hour, a refusal gets twenty seconds). Correct, but the
#              number is ours.
#   default  — nothing was recognised at all. One or two is normal; a RISING
#              share here is the shape of an install that has gone quiet.
#
# Counts only, keyed by pool. Never a key, never an error message.
_KAME_TALLY = {}


def _delay_source(exc, kind: str) -> str:
    """provider / kame / default — see `_KAME_TALLY`."""
    if kind == "other":
        return "default"
    if kind == "per_minute":
        try:
            return "provider" if _extract_retry_delay(exc, with_source=True)[1] != "default" else "kame"
        except Exception:
            return "kame"
    return "kame"


def _tally_failure(identity: str, kind: str, status, source: str) -> None:
    """Record one failure under its pool, its kind, its status and its source."""
    try:
        with _KAME_LOCK:
            row = _KAME_TALLY.setdefault(
                identity or "?",
                {"total": 0, "provider": 0, "kame": 0, "default": 0,
                 "kinds": {}, "statuses": {}},
            )
            row["total"] += 1
            row[source] = row.get(source, 0) + 1
            row["kinds"][kind] = row["kinds"].get(kind, 0) + 1
            key = str(status) if status is not None else "none"
            row["statuses"][key] = row["statuses"].get(key, 0) + 1
    except Exception:
        pass  # a counter must never be able to end a run


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
    """Set how API keys are shown in logs. Called by the activation extension.

    v1.6.0.1 REMOVED the `full` style, which wrote the entire secret into the
    log. Three things made it indefensible rather than merely risky:

    * Agent Zero v2.11 masks secrets in error output itself
      (`extensions/python/error_format/_10_mask_errors.py`,
      `helpers/secrets.py`), so KAME's option had become the only thing in the
      stack that deliberately un-redacted a credential.
    * The Hermes port of this plugin has never had it, redacts on every path
      including the error path, and has not once wanted it back.
    * A log is copied into bug reports, screenshots and support bundles by
      people who are not thinking about what is in it. A debug switch whose
      worst case is "the user pastes their key into a GitHub issue" is not a
      debug switch worth keeping.

    A config that still says `full` is not an error and does not fail: it is
    read as `prefix8`, which answers the question `full` was actually used for
    — *which of my keys is this* — without answering it to everyone who ever
    reads the log. `kame_log_full_errors` still exists and still prints the
    provider's untruncated message beside KAME's classification; that is the
    setting people reached for `full` to get.
    """
    global _KAME_KEY_LOG_STYLE
    s = str(style or "").strip().lower()
    if s == "full":
        s = "prefix8"
    if s in ("fingerprint", "prefix8"):
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


def set_wait_notice(enabled) -> None:
    """Enable/disable the chat-side "waiting for a key" notice (v1.2.0).

    Called by the activation extension from the `kame_wait_notice` plugin
    setting. Accepts a bool or a truthy string ('true'/'1'/'yes'/'on'). Pure
    observability — it never changes which key is picked, how long anything
    rests, or when the carousel gives up. Turning it off only means a long wait
    is announced on the console and nowhere else.
    """
    global _KAME_WAIT_NOTICE
    if isinstance(enabled, str):
        _KAME_WAIT_NOTICE = enabled.strip().lower() in ("true", "1", "yes", "on")
    else:
        _KAME_WAIT_NOTICE = bool(enabled)


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
    # v1.6.0.1: there is no branch here that returns the whole key any more.
    # `set_key_log_style` folds a legacy `full` into `prefix8`, and this
    # function has no way to express `full` even if something set the global
    # directly — which is the point of removing it here as well as there.
    if _KAME_KEY_LOG_STYLE == "prefix8":
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
    secret). If the user already configured 'prefix8', that choice is respected
    as-is. Never affects any other log line.

    v1.6.0.1: `full` is gone (see `set_key_log_style`), so the widest reveal
    any path in this file can produce is this one — a head and a tail, never
    the middle.
    """
    if not key:
        return "------"
    if _KAME_KEY_LOG_STYLE == "prefix8":
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
    refused = s["auth"] + s.get("revoked", 0) + s.get("denied", 0)
    tail = ""
    if s.get("retired"):
        tail = f" · {s['retired']} left rotation"
    return (
        f"[KAME] Session: {s['ok']} ok · {limited} limited "
        f"(min {s['per_minute']}, daily {s['daily']}, quota {s['insufficient_quota']}) · "
        f"{s['long_sleeps']} long-sleep{'s' if s['long_sleeps'] != 1 else ''} · "
        f"{s['server']} server · {s['timeout']} timeout · "
        f"{refused} refused (401 {s['auth']}, revoked {s.get('revoked', 0)}, "
        f"model {s.get('denied', 0)}) · {s['other']} other{tail}"
    )


def _build_report() -> dict:
    """The integrity report, or an honest blank when it cannot be produced.

    Fetched by name rather than imported at module scope, for the reason every
    optional KAME import uses: a half-copied plugin directory must degrade to
    one missing line, never to a plugin that will not load. `integrity.py` is
    the module whose whole job is noticing a half-copied directory, so it would
    be a poor joke if its absence took the engine down with it.
    """
    try:
        from usr.plugins.api_rotation_by_kame import integrity
        return integrity.report()
    except Exception:
        try:
            import integrity  # running from the plugin directory, e.g. in tests
            return integrity.report()
        except Exception:
            return {"fingerprint": "------------", "complete": None,
                    "missing": [], "degraded": []}


def pool_report() -> dict:
    """Everything KAME can say about its own pools, for a screen.

    v1.6.0.1. The reason this exists is that ordinary rotation was invisible.
    The only thing this plugin ever put in the chat was the wait notice, and it
    is gated three ways — ninety seconds of the WHOLE pool being cold, reached
    only from the exhaustion sleep, and silently disabled for the rest of a call
    if `context.log` ever throws. With fourteen keys that state is rare and
    short, so a key being swapped, a key resting twenty seconds, and a key
    leaving rotation all happened where nobody was looking.

    **Counts and fingerprints only. Never a key, on any path.**

    `_key_short_id` is used directly rather than `_key_display`, on purpose:
    `_key_display` honours `key_log_style`, and a rendering setting meant for a
    developer's console must never be able to put a secret on a web page. That
    is a structural guarantee here, not a default.

    Pure and framework-free: no Agent Zero import, no request, no clock beyond
    `time.time()`. That is what lets it be called from an API handler, from a
    slash command, and from a test with equal confidence.
    """
    now = time.time()
    pools = []
    totals = {"keys": 0, "ready": 0, "resting": 0, "retired": 0}
    with _KAME_LOCK:
        snapshot = {
            ident: dict(state.get("keys") or {})
            for ident, state in _KAME_KEY_HEALTH.items()
        }
        stats = dict(_KAME_STATS)
        tally = {
            ident: {
                "total": row.get("total", 0),
                "provider": row.get("provider", 0),
                "kame": row.get("kame", 0),
                "default": row.get("default", 0),
                "kinds": dict(row.get("kinds") or {}),
                "statuses": dict(row.get("statuses") or {}),
            }
            for ident, row in _KAME_TALLY.items()
        }
    for identity in sorted(snapshot):
        keys = snapshot[identity]
        rows, eta = [], None
        ready = resting = retired = 0
        for key in sorted(keys, key=_key_short_id):
            kd = keys[key] or {}
            sick_until = float(kd.get("sick_until") or 0)
            is_retired = bool(kd.get("retired_at"))
            if is_retired:
                retired += 1
                state = "retired"
                left = None
            elif sick_until > now:
                resting += 1
                state = "resting"
                left = round(sick_until - now, 1)
                if eta is None or left < eta:
                    eta = left
            else:
                ready += 1
                state = "ready"
                left = None
            rows.append({
                "id": _key_short_id(key),
                "state": state,
                "seconds_left": left,
                # How close this credential is to leaving rotation. Shown so a
                # reader can tell "being tried" from "given up on" without
                # having to know the threshold.
                "strikes": int(kd.get("consecutive_refusals") or 0),
                "limit": _KAME_REFUSALS_BEFORE_RETIRING,
            })
        total = len(keys)
        pools.append({
            "identity": identity,
            "total": total,
            "ready": ready,
            "resting": resting,
            "retired": retired,
            # None when nothing is resting. The panel reads this as "no wait to
            # report", which is not the same as "a wait of zero".
            "eta": eta,
            "keys": rows,
        })
        totals["keys"] += total
        totals["ready"] += ready
        totals["resting"] += resting
        totals["retired"] += retired
    return {
        "version": KAME_VERSION,
        # v1.6.0.1: the version is what somebody typed; the build is what is on
        # disk. When they disagree, the build is the one telling the truth.
        "build": _build_report(),
        # Layer 3 means KAME bound nothing and Agent Zero is running natively.
        # It is a safe end state, not a crash — but it is also the single most
        # useful thing to see on screen when somebody reports "the plugin does
        # nothing", so it is reported rather than hidden.
        "active": bool(_KAME_PATCHED) and _KAME_LAYER in (1, 2),
        "layer": _KAME_LAYER,
        "bound": list(_KAME_BOUND_ENTRY_POINTS),
        "pools": pools,
        "totals": totals,
        "stats": stats,
        # Where each cooldown came from, per pool. A rising `default` share is
        # the shape of an install that has gone quiet after an upstream change.
        "tally": tally,
        "generated_at": now,
    }


_RATE_LIMIT_INDICATORS = (
    "429", "too many requests", "rate limit", "rate_limit",
    "quota exceeded", "quota left", "no quota",
    "resource exhausted", "resource_exhausted",
    "tokens per min", "requests per min", "quota_exceeded",
    # --- v1.6.0.1: families whose 429 says none of the words above ----------
    # Every entry here is a spent counter on THIS credential, which is the
    # definition of "rotate to another key". Before this release each one fell
    # into the generic `other` bucket at a flat 20s, with no escalation and no
    # daily/per-minute distinction — a whole provider's throttling read as an
    # unrecognised error.
    #
    # `throttl` is a bare stem on purpose. Alibaba spells its entire 429 family
    # with this one word and never with "rate limit": Throttling,
    # Throttling.RateQuota, Throttling.BurstRate, Throttling.AllocationQuota.
    # It arrives in the error CODE rather than the sentence, which is why the
    # stem is worth more than any full phrase.
    "throttl",
    # Z.AI 1308 / 1310. A spent counter described without either of the two
    # phrases every entry above depends on.
    "usage limit reached",
    "limit exhausted",
    # A concurrency ceiling is a per-credential counter like any other — the
    # next key has its own. Named by Kimi and MiniMax alongside RPM and TPM.
    "concurrent limit", "concurrency limit", "concurrent requests limit",
    # A counter named by its unit and its window and by nothing else:
    # "You have exceeded your limit of 200000 tokens per day". The daily
    # markers below already read the window out of the same words; nothing
    # upstream had ever called this a quota failure, so they were never asked.
    "tokens per day", "tokens per hour", "tokens per week", "tokens per month",
    "requests per day", "requests per hour",
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
    text = _evidence_text(exc)
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
        # v1.6.0.1: the quota id often survives only in a PARSED payload,
        # because the adapter that raised this already spent the body — so the
        # search runs over the structured evidence too, and accepts the
        # snake_case spelling a parsed dict uses as well as the JSON one.
        text = _evidence_text(exc)
        m = (re.search(r'"quotaid"\s*:\s*"([^"]+)"', text)
             or re.search(r"'quota_?id'\s*:\s*'([^']+)'", text)
             or re.search(r'quota_?limit["\']?\s*[:=]\s*["\']([^"\']+)', text))
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


#: The three spellings the quota id arrives under. ``quotaId`` is what Google
#: puts in ``google.rpc.QuotaFailure.violations[]``; ``quota_limit`` is the
#: snake_case form a parsed payload uses; ``quotaLimit`` is the camelCase one
#: some adapters keep. All three name the same field.
_QUOTA_ID_PATTERNS = (
    re.compile(r'"quotaid"\s*:\s*"([^"]+)"'),
    re.compile(r"'quota_?id'\s*:\s*'([^']+)'"),
    re.compile(r'quota_?limit["\']?\s*[:=]\s*["\']([^"\']+)'),
)


def _quota_window_from_id(exc) -> str:
    """``"per_day"``, ``"per_minute"`` or ``""`` — from the quota id ALONE.

    v1.6.0.3, and the difference from :func:`_extract_quota_marker` is the
    whole point: that function falls back to scanning the entire message when
    it finds no id, which is right for a log tag and wrong for a decision.
    This one reads the provider's own field or says nothing.

    Why it has to exist. Google reports **both** free-tier quotas under the
    identical metric name —
    ``generativelanguage.googleapis.com/generate_content_free_tier_requests``
    — and separates them only here:

    ====================================================  ================
    quotaId                                               what it means
    ====================================================  ================
    ``GenerateRequestsPerMinutePerProjectPerModel-...``   seconds
    ``GenerateRequestsPerDayPerProjectPerModel-...``      the rest of the day
    ====================================================  ================

    :func:`_is_daily_or_account_limit` decides the same question by searching
    the *whole* message for ``"daily"``, ``"per day"``, ``"/day"`` or
    ``"rpd"``. Its docstring calls that strict, on the reasoning that a
    per-minute message cannot contain those words. It can. A host may append
    its own prose — Hermes welds *"a few hundred requests/day for Gemini Flash
    models"* onto the sentence — and a quota list may name several counters
    while violating one. Either way a per-minute throttle is read as a daily
    cap and a healthy key sits out an hour.

    So when the provider names the window, the provider decides, in **both**
    directions: a ``PerMinute`` id means this is not daily no matter what the
    prose says, and a ``PerDay`` id means it is daily even if the prose is
    silent. The substring heuristic keeps its job for everything that files no
    id at all — most providers, most of the time.

    Never raises. Classification must not be the thing that ends a turn.
    """
    try:
        text = _evidence_text(exc)
        raw = ""
        for pattern in _QUOTA_ID_PATTERNS:
            match = pattern.search(text)
            if match:
                raw = match.group(1).lower()
                break
        if not raw:
            return ""
        if any(t in raw for t in ("perday", "per_day", "per-day", "per day", "/day", "rpd")):
            return "per_day"
        if any(t in raw for t in ("perminute", "per_minute", "per-minute",
                                  "per minute", "/minute", "rpm")):
            return "per_minute"
        return ""
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

# v1.6.0.1: how long a row survives after nothing offers it any more.
#
# The grace is load-bearing. Two agents can hold different key lists for one
# identity — a settings edit that has reached one and not the other, a subagent
# built from an older config — and dropping a row the moment one of them looks
# away would erase a cooldown the other one just earned. Five minutes is longer
# than any plausible gap between two callers and far shorter than the daily
# bench it protects.
_KAME_POOL_GRACE_S = 300.0


def _get_identity_state(identity, all_keys):
    global _KAME_KEY_HEALTH
    if identity not in _KAME_KEY_HEALTH:
        _KAME_KEY_HEALTH[identity] = {"keys": {}}
    state = _KAME_KEY_HEALTH[identity]
    now = time.time()
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
                # v1.6.0.1: consecutive CREDENTIAL refusals — a bare 401 or a
                # revoked key. Reset by any success and by any failure of
                # another kind, because "three in a row" has to mean in a row.
                "consecutive_refusals": 0,
                # v1.6.0.1: the same ladder for `denied`, kept in its own
                # counter so a model-scoped 403 can never feed retirement.
                "consecutive_denials": 0,
                # v1.6.0.1: when this key left rotation, 0 while it has not.
                # Retiring is not deleting: the row stays, the config is
                # untouched, and one successful call brings it straight back.
                "retired_at": 0,
                # v1.6.0.1: the last time this key was among the candidates.
                # Drives the pruning below.
                "last_offered": now,
            }
        else:
            # Defensive: backfill for keys created on earlier versions.
            state["keys"][k].setdefault("last_sick_at", 0)
            state["keys"][k].setdefault("consecutive_rl", 0)
            state["keys"][k].setdefault("consecutive_server", 0)
            state["keys"][k].setdefault("consecutive_refusals", 0)
            state["keys"][k].setdefault("consecutive_denials", 0)
            state["keys"][k].setdefault("retired_at", 0)
            state["keys"][k]["last_offered"] = now

    # v1.6.0.1: the pool now mirrors the live candidate list instead of only
    # ever growing.
    #
    # Until this release nothing anywhere removed from `state["keys"]` — the
    # only `pop` in this file is `_KAME_STORM`, which is the log collapser. So a
    # key edited out of the .env kept its `sick_until`, kept its
    # `consecutive_rl` ladder, and went on being counted in every "N of M keys
    # resting" the user was shown. It becomes visible the moment that count
    # reaches a screen, which is what v1.6.0.1 does.
    #
    # An EMPTY `all_keys` mirrors nothing at all. A loader that failed once — a
    # settings read mid-write, a provider block momentarily absent — is not
    # evidence that every key was deleted, and treating it as such would wipe a
    # pool's entire cooldown history over a transient read.
    if all_keys:
        stale = [
            k for k, kd in state["keys"].items()
            if (now - float(kd.get("last_offered") or 0)) > _KAME_POOL_GRACE_S
        ]
        for k in stale:
            state["keys"].pop(k, None)
    return state


def _mark_key_health(identity, key, success=True, delay=20, kind="other",
                     sized_by: str = ""):
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
            # v1.6.0.1: one answer is the whole of the evidence that a
            # credential works. It clears the refusal streak AND brings a
            # retired key straight back — retiring was never a deletion.
            kd["consecutive_refusals"] = 0
            kd["consecutive_denials"] = 0
            kd["retired_at"] = 0
            _KAME_STATS["ok"] += 1
            _KAME_CALL_COUNT += 1
        else:
            applied = float(delay)
            # v1.6.0.1: a refusal is not a clock. `auth`, `revoked` and `denied`
            # all start at the refusal bench and climb the same doubling ladder
            # toward the daily ceiling, so a permission that really is permanent
            # reaches an hour by itself while a plan somebody upgrades, or a
            # token that was mid-refresh, comes back in the seconds it actually
            # took.
            #
            # The two counters are kept apart on purpose. `consecutive_refusals`
            # is what retirement reads, and `denied` must never feed it: a model
            # the key is not entitled to is not evidence about the key.
            if kind in ("auth", "revoked"):
                cnt = int(kd.get("consecutive_refusals", 0)) + 1
                kd["consecutive_refusals"] = cnt
                escalated = min(
                    _KAME_REFUSAL_REST_S * (2 ** (cnt - 1)), _KAME_DAILY_COOLDOWN_S
                )
                applied = max(applied, escalated)
                if kind in _KAME_RETIRE_ON_SIGHT_KINDS or cnt >= _KAME_REFUSALS_BEFORE_RETIRING:
                    if not kd.get("retired_at"):
                        kd["retired_at"] = now
                        _KAME_STATS["retired"] = _KAME_STATS.get("retired", 0) + 1
            elif kind == "denied":
                cnt = int(kd.get("consecutive_denials", 0)) + 1
                kd["consecutive_denials"] = cnt
                escalated = min(
                    _KAME_REFUSAL_REST_S * (2 ** (cnt - 1)), _KAME_DAILY_COOLDOWN_S
                )
                applied = max(applied, escalated)
                # No retirement, ever. See _KAME_RETIRING_KINDS.
            elif kind in ("daily", "insufficient_quota"):
                # Real daily / account limit: the classifier already floored this
                # at the daily cooldown; the escalation is a belt-and-suspenders
                # safety net, capped at the SAME daily ceiling.
                cnt = int(kd.get("consecutive_rl", 0)) + 1
                kd["consecutive_rl"] = cnt
                escalated = min(20.0 * (2 ** (cnt - 1)), _KAME_DAILY_COOLDOWN_S)
                applied = max(applied, escalated)
            elif kind == "per_minute":
                # v1.6.0.3. This used to raise a floor of 20s, 40s, 80s ... over
                # the deadline from the second strike on, whatever the provider
                # had said. `applied` starts as the classifier's number, so a
                # provider asking for 41s and refusing four times in a row was
                # held for 80 — a number nobody stated, over one that was.
                #
                # Repeating a throttle is not evidence that the provider lied.
                # On a rolling window it is the ordinary case: the key is asked
                # again while its window is still full, and the provider
                # recomputes and answers correctly again. Escalating on that
                # reads a restatement as a refutation.
                #
                # So the streak is still counted — the reports read it, and a
                # key failing five times in a row is worth seeing — but it no
                # longer sizes anything the provider already sized. When the
                # provider named nothing, `_extract_retry_delay` has already
                # supplied its flat 20s default and that stands: a rate limit
                # is a rolling window (seconds, and the provider says so) or a
                # daily cap (hours, a different counter, handled above), and
                # nothing lives between them for a ladder to climb through.
                #
                # The same rule, in the same words, governs
                # `core/carousel.py::_escalate` in the Hermes port. See
                # PARITY.md.
                cnt = int(kd.get("consecutive_rl", 0)) + 1
                kd["consecutive_rl"] = cnt
                if sized_by == "provider":
                    # Stated for this very refusal. Learn the shape of this
                    # provider's short window, and change nothing else.
                    if 0 < applied <= _KAME_RL_BACKOFF_CAP_S:
                        _KAME_STATED_RL[identity] = max(
                            _KAME_STATED_RL.get(identity, 0.0), float(applied)
                        )
                else:
                    # Nothing was stated. Before inventing anything, ask what
                    # this provider has said about this model before — that is
                    # a measurement, and a ladder is not.
                    learned = _KAME_STATED_RL.get(identity, 0.0)
                    if learned > 0:
                        applied = min(learned, _KAME_RL_BACKOFF_CAP_S)
                    # And when it has never said anything at all, the flat 20s
                    # `_extract_retry_delay` already supplied stands. No climb:
                    # a rate limit is a rolling window (seconds) or a daily cap
                    # (hours, handled above), and a ladder from 20s to 300s
                    # spends its whole range between two regimes that do not
                    # meet. Erring short costs one request that fails in
                    # milliseconds; erring long costs a healthy key, silently.
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
            # v1.6.0.1: "three in a row" has to mean in a row. A 429 or a 503
            # between two 401s is evidence that the credential reached the
            # provider and was metered, which is exactly what a dead key cannot
            # do — so it resets the streak.
            if kind not in ("auth", "revoked"):
                kd["consecutive_refusals"] = 0
            if kind != "denied":
                kd["consecutive_denials"] = 0
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


# --- v1.6.0.1: the evidence that was already on the exception ----------------
#
# Everything before this release read `str(exc)` and the response headers. That
# is most of the evidence and not all of it, and the part it missed is the part
# providers have been moving TOWARDS: a parsed error object filed on the
# exception, whose body has already been consumed by the adapter that raised it.
# When that happens `str(exc)` is a human sentence and every machine-readable
# field — the quota id, the reason, the RetryInfo — is sitting one attribute
# away, unread.
#
# Two rules keep this from becoming a fishing expedition:
#
#   1. Only NAMED, structured fields are read. Not `__dict__`, not every
#      attribute — a classifier that reads whatever it finds will eventually
#      match a marker inside something that is not an error description.
#   2. The result is BOUNDED. A provider that returns a megabyte of HTML must
#      not turn every substring check in this file into a scan of it.
_EVIDENCE_ATTRS = ("details", "body", "error", "code", "reason", "status")
_EVIDENCE_MAX_CHARS = 4000


def _evidence_text(exc) -> str:
    """`str(exc)` plus the structured fields, lowercased and length-bounded.

    Used everywhere this file used to call `str(exc).lower()` for a marker
    check, so a marker that lives in a parsed payload is found by exactly the
    same vocabulary that finds it in a sentence.
    """
    parts = [str(exc)]
    try:
        for name in _EVIDENCE_ATTRS:
            value = getattr(exc, name, None)
            if value is None or callable(value):
                continue
            if isinstance(value, (str, int, float)):
                parts.append(str(value))
            elif isinstance(value, (dict, list, tuple)):
                parts.append(repr(value))
        # The response object, when the SDK kept one. Body first: a provider
        # that files its quota id anywhere files it there.
        response = getattr(exc, "response", None)
        if response is not None:
            for name in ("text", "content"):
                raw = getattr(response, name, None)
                if isinstance(raw, (str, bytes)) and raw:
                    parts.append(raw.decode("utf-8", "replace")
                                 if isinstance(raw, bytes) else raw)
                    break
            headers = getattr(response, "headers", None)
            if isinstance(headers, dict):
                parts.append(repr(headers))
    except Exception:
        pass  # evidence gathering must never be the thing that raises
    return " ".join(parts)[:_EVIDENCE_MAX_CHARS].lower()


def _evidence_status(exc):
    """The HTTP status, walking `__cause__` / `__context__` when it is not here.

    litellm and the OpenAI SDK both re-wrap provider errors, and the wrapper
    does not always carry the status the wrapped one had. Five levels, because
    the chain is short in practice and an unbounded walk on a cyclic chain is a
    hang rather than a bug.
    """
    # Wrapped whole, for the same reason `_evidence_text` is: `getattr` runs
    # arbitrary provider code when the attribute is a property, and an SDK whose
    # `.response` raises once the body is consumed would otherwise turn a
    # recoverable 429 into an exception thrown from inside the classifier — a
    # crash in the code whose entire job is to keep the run alive. Found by
    # `tests/test_v1_6_0_1.py`, not by a provider.
    try:
        seen = set()
        current = exc
        for _ in range(5):
            if current is None or id(current) in seen:
                break
            seen.add(id(current))
            for attr in ("status_code", "code", "http_status"):
                try:
                    value = getattr(current, attr, None)
                except Exception:
                    continue
                if isinstance(value, int) and 100 <= value <= 599:
                    return value
                if isinstance(value, str) and value.isdigit() and 100 <= int(value) <= 599:
                    return int(value)
            try:
                response = getattr(current, "response", None)
                value = getattr(response, "status_code", None)
            except Exception:
                value = None
            if isinstance(value, int) and 100 <= value <= 599:
                return value
            current = (getattr(current, "__cause__", None)
                       or getattr(current, "__context__", None))
    except Exception:
        return None
    return None


def _extract_retry_delay(exc, with_source: bool = False):
    """Extract retry-after from an API error. Falls back to 20s default.

    v1.6.0.1 adds ``with_source``. When true this returns ``(value, source)``
    where source is one of ``attr`` / ``retry_delay`` / ``header`` / ``text`` /
    ``default``. Nothing about the number changes; what changes is that the
    caller can now tell a deadline the PROVIDER stated from one this plugin
    invented, which is the difference between a tally that means something and
    a tally that only counts failures.


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
                return (val, "attr") if with_source else val
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
            return (secs, "retry_delay") if with_source else secs

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
                    return (val, "header") if with_source else val
            except (ValueError, TypeError):
                pass

    # 3. Parse from error message text. Capture the duration EXPRESSION right
    #    after a retry keyword, then parse it (handles "6m 11.52s", "37s",
    #    "2970.93s", "retryDelay: 1s", "Please try again in 2h 30m").
    #
    #    v1.6.0.3 reads the STRUCTURED evidence here, not just `str(exc)`.
    #    Every other reader in this file moved to `_evidence_text` when it
    #    turned out that adapters spend the response body and leave the
    #    provider's own fields reachable only through the parsed payload or
    #    `exc.response` — and this one, the reader whose whole job is to find
    #    the number, was left behind. On a Gemini 429 that meant
    #    `"retryDelay": "41.3s"` sitting in `exc.response.text`, three lines
    #    from a `quotaId` this file already reads from exactly there, while
    #    the function returned its 20-second default and reported the source
    #    as `default` — so the tally recorded "the provider said nothing"
    #    about a refusal in which the provider had said precisely how long.
    #
    #    The regex is unchanged and still anchors on a retry keyword before
    #    capturing, so widening the haystack cannot turn a model name or an
    #    id into a duration.
    err_msg = _evidence_text(exc) or str(exc)
    match = re.search(
        r'(?:retry[_\s-]*(?:after|delay|in)|try\s+again\s+in)[:\s"\']*([0-9][0-9hms\.\s]*)',
        err_msg,
        re.IGNORECASE,
    )
    if match:
        dur = _parse_duration_to_seconds(match.group(1))
        if dur is not None and 0 < dur <= cap:
            return (dur, "text") if with_source else dur

    return (20, "default") if with_source else 20  # Safe default


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
    "permission denied",
    "denied access",
    "consumer_suspended",
    "service_disabled",
    "api_key_service_blocked",
    "has not been used in project",
    "is disabled for this project",
    # --- v1.6.0.1 additions -------------------------------------------------
    # A model outside the tier the key pays for. This is about the PAIRING, not
    # about the credential: the same key is very often the healthiest one in the
    # account on every other model, which is why `denied` is scoped per
    # provider:model and is never allowed to retire a key.
    "model not authorized",
    "model not available",
    # Z.AI 1311: "Your current subscription plan does not yet include access to
    # ${model_name}". Per-model by construction — the plan is fine, this one
    # model is outside it. Both spellings, because a substring match cannot see
    # past the "yet".
    "does not include access to",
    "does not yet include access to",
)

# Deliberately NOT in the tuple above, with the reason attached, because an
# omission nobody wrote down gets "fixed" by the next reader:
#
#   "model not found" — it is about the MODEL NAME, not the credential. The
#     Hermes port had it in this family and its own corpus caught the cost: the
#     host says `model_not_found` (try a different model, do not touch the key),
#     KAME said `auth` and rotated — walking the entire pool over a misspelt
#     model name and benching every key in it. Agent Zero already routes 404 to
#     `_is_terminal_error`, which is the correct answer, and
#     `tests/test_v1_6_0_1.py` asserts it stays that way.
#
#   The exception CLASS names `AuthenticationError` / `PermissionDeniedError` —
#     these are the classes of every 401 and every 403 respectively, not a
#     statement about this key. Mapping them would recreate, by a different
#     road, the exact defect that removing `"unauthorized"` above repairs.
_DENIAL_EXCLUDED_ON_PURPOSE = (
    "model not found",
    "AuthenticationError",
    "PermissionDeniedError",
)


# v1.6.0.1: exception classes that mean "the connection did not happen".
# Curated, never folded into a general lookup — see the note in
# `_classify_error` for why a class name must not be allowed to state a
# provider's verdict. Names only, so litellm/openai/httpx do not have to be
# importable for this to work.
_TRANSPORT_EXCEPTION_CLASSES = frozenset({
    "APIConnectionError",
    "APITimeoutError",
    "ConnectError",
    "ConnectTimeout",
    "ConnectionError",
    "ConnectionResetError",
    "ReadError",
    "ReadTimeout",
    "RemoteProtocolError",
    "WriteError",
    "WriteTimeout",
    "PoolTimeout",
    "ProxyError",
    "SSLError",
    "Timeout",
    "TimeoutException",
})


def _is_permanent_denial(exc: Exception) -> bool:
    """True for a 403-class refusal that another 20s will not fix (v1.0.8).

    Matches on the status code (403) OR on the provider's own textual marker —
    litellm sometimes rewraps a Gemini 403 as a `BadRequestError` whose
    `status_code` is not carried over, so the text check is not redundant.
    Never matches a 429: quota is classified before this in `_classify_error`.
    """
    if _evidence_status(exc) == 403:
        return True
    err_msg = _evidence_text(exc)
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
    err_msg = _evidence_text(exc)
    if "timeout" in err_msg or "timed out" in err_msg:
        return 3, "timeout", None

    # v1.6.0.1: the class name, for the one failure that carries nothing else.
    # A transport error has no status code and no body, ever — the socket never
    # got far enough to produce one — so every branch below this point finds
    # nothing to read and it used to land in the generic `other` bucket at 20s.
    # `type(exc).__name__` is the only evidence it has, and three seconds is the
    # whole of the right answer: nothing is wrong with the key, the connection
    # simply did not open.
    #
    # Only connection-shaped classes are listed. Classes that describe a
    # PROVIDER verdict (RateLimitError, AuthenticationError,
    # PermissionDeniedError) are deliberately absent — see
    # `_DENIAL_EXCLUDED_ON_PURPOSE`. Reading a verdict off a class name would
    # shadow the status code and the body, which say the same thing better.
    if type(exc).__name__ in _TRANSPORT_EXCEPTION_CLASSES:
        return 3, "timeout", None

    # v1.6.0.1: the status may be on a wrapper's cause rather than on the
    # wrapper. litellm and the OpenAI SDK both re-raise, and the outer object
    # does not always carry the status the inner one had.
    status_code = _evidence_status(exc)

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
        # v1.6.0.3. The provider's own field first, and it settles the question
        # in both directions. A ``PerMinute`` quota id means this is a rolling
        # window whatever words surround it — which is what stops a host
        # footer saying "a few hundred requests/day" from turning a 40-second
        # throttle into an hour — and a ``PerDay`` id means a daily cap even
        # when the prose never says so. Only when the provider files no id at
        # all does the substring heuristic below get to decide.
        named_window = _quota_window_from_id(exc)
        if named_window == "per_minute":
            return parsed, "per_minute", (status_code or 429)
        if named_window == "per_day" or _is_daily_or_account_limit(exc):
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
    # v1.6.0.1: the OPENING rest, not the whole story. `denied` is on the
    # doubling ladder in `_mark_key_health`, so a permission that really is
    # permanent reaches the hourly re-probe by itself, while a plan somebody
    # upgrades — or an API somebody switches on — comes back in the seconds it
    # actually took rather than costing an hour of a key that was never broken.
    # The hour was never load-bearing: only the ladder's ceiling is.
    if _is_permanent_denial(exc):
        return _KAME_REFUSAL_REST_S, "denied", (status_code or 403)

    # Everything else
    return 20, "other", status_code


def _classify_error_delay(exc):
    """Backward-compatible thin wrapper: just the delay."""
    return _classify_error(exc)[0]


def _retirement_suffix(identity: str, key: str) -> str:
    """What to add after a refusal line: has this key left rotation, or not yet?

    v1.6.0.1. Two sentences, never one, because they ask the reader for opposite
    things — *act on this* versus *do not act on this yet*. Counts only; the key
    itself is already rendered by the caller through `_key_display_auth`.
    """
    try:
        with _KAME_LOCK:
            pool = _KAME_KEY_HEALTH.get(identity, {}).get("keys", {})
            kd = pool.get(key) or {}
            retired_here = bool(kd.get("retired_at"))
            strikes = int(kd.get("consecutive_refusals") or 0)
            total = len(pool)
            retired_total = sum(1 for v in pool.values() if v.get("retired_at"))
            live = total - retired_total
    except Exception:
        return ""
    if retired_here:
        if live <= 0:
            # The escape hatch. Every key refused means every key is offered
            # again, so saying "left rotation" here would be a lie.
            return (" — every key in this pool has now been refused, so all of "
                    "them are being offered again and the provider's own error "
                    "will come back")
        return (f" — {retired_total} of {total} keys have left rotation, {live} "
                f"still in it. Nothing was deleted: paste a replacement over it "
                f"and it comes back by itself.")
    if strikes:
        return (f" — refusal {strikes} of {_KAME_REFUSALS_BEFORE_RETIRING}; it is "
                f"still being tried, because one refusal is not proof")
    return ""


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
    # v1.6.0.1: three sentences, because the three refusals ask the reader for
    # three different things. Saying "invalid key — replace it" about a bare 401
    # was false often enough to cost real keys, and saying it about a 403 that
    # names one model was false twice over.
    if kind == "revoked":
        sc = status_code or 401
        return (f"\U0001f512 {sc} the provider says this is not a valid key → "
                f"out of rotation (nothing was deleted — paste a replacement "
                f"over it and it comes back by itself)")
    if kind == "auth":
        sc = status_code or 401
        return (f"\U0001f512 {sc} refused with no explanation → key rests {d} and "
                f"is offered last · rotating to next key...")
    if kind == "denied":
        sc = status_code or 403
        return (f"\U0001f6ab {sc} this key may not use THIS model → rested {d} "
                f"for this model only; it is untouched everywhere else "
                f"(project suspended, API not enabled, or model outside the "
                f"key's tier)")
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

        # 0. v1.6.0.1: RETIREMENT OUTRANKS READINESS, and that is the whole
        #    reason it is worth having.
        #
        #    The demotion in step 3 handles the easy case, where a working key
        #    is sitting there unused. The case it gets wrong is the one that
        #    actually happens: the working key is resting twenty seconds off a
        #    throttle, the refused key's own rest has lapsed, so the refused key
        #    is the only thing "ready" — and the call goes to a credential the
        #    provider has already told us is dead. That spends a request and
        #    hands back an error where waiting twenty seconds would have handed
        #    back an answer.
        #
        #    THE ESCAPE HATCH IS THE WHOLE SAFETY ARGUMENT. The rule applies
        #    only while some key is not retired. If every key in a pool has been
        #    refused, every key is offered again — the request goes out and the
        #    provider's own error comes back, exactly as it would with no plugin
        #    installed. Retiring can never take a pool to zero, so the worst
        #    case of a wrong verdict is no worse than not having the rule.
        candidates = [k for k in all_keys if not pool[k].get("retired_at")]
        if not candidates:
            candidates = list(all_keys)

        # 1. Clean expired request timestamps (>60s old) for all keys
        for k in all_keys:
            pool[k]["request_log"] = [t for t in pool[k]["request_log"] if t > cutoff]

        # 2. Filter healthy keys (not sick/quarantined)
        healthy = [k for k in candidates if pool[k]["sick_until"] < now]

        if not healthy:
            # Eternal fallback: pick the one recovering soonest
            best = min(candidates, key=lambda k: pool[k]["sick_until"])
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
        #
        #    v1.6.0.1 adds the DEMOTION as the first term. A key whose most
        #    recent answer was a refusal is offered LAST among the ready ones —
        #    never removed, so a pool of nothing but refused keys still works,
        #    but never preferred either.
        #
        #    Without it the shorter refusal bench is worse than the hour it
        #    replaced: a key that answered 401 comes back with an empty request
        #    window and the oldest `last_used` in the pool, which is precisely
        #    what the two terms below reach for. The one key known not to work
        #    would be the first one tried, every twenty seconds. There is a test
        #    that fails when this term is removed.
        #
        #    Both counters reset on any success and on any failure of another
        #    kind, so "was refused" means "the last thing it did was refuse",
        #    not "was refused once, an hour ago".
        best_key = min(healthy, key=lambda k: (
            1 if (pool[k].get("consecutive_refusals") or pool[k].get("consecutive_denials")) else 0,
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
    # --- v1.6.0.1 additions -------------------------------------------------
    "invalid authentication",
    "incorrect api key",
    "api key is no longer valid",
)

# `"unauthorized"` was in that tuple until v1.6.0.1 and is not any more.
#
# It is the HTTP reason phrase for 401, so it arrives on EVERY bare 401 — a
# proxy in front of the provider, a gateway, an OAuth token one second from
# refreshing. Reading it as "this key is not a key" quarantines a healthy
# credential over a refresh that was going to succeed. The Hermes port measured
# the cost before removing it there in its own 1.4.0: **twenty-one healthy keys
# quarantined for an hour each**, every one of them working again on the next
# call. A provider that has genuinely retired a key always says more than
# "Unauthorized", and each of those sentences is matched above or below.
#
# Removing the word does not make a 401 invisible: `_is_auth_error` still
# returns True on `status_code == 401`. What changes in v1.6.0.1 is what a BARE
# 401 costs — see `_classify_auth_kind` and the `auth` / `revoked` split.

# Two providers state the same fact with the words the other way round, and no
# substring in the tuple above can reach them:
#
#     Anthropic  "API key is invalid."
#     DeepSeek   "Your api key: ****0000 is invalid"
#
# Every entry above reads "invalid key"; neither of those says that, so a
# genuinely dead key on either provider used to be handed back as an ordinary
# failure and rotated forever. The gap is bounded rather than open — the key
# and the verdict must sit in the SAME clause, so a sentence that merely
# mentions a key somewhere and the word "invalid" somewhere else does not
# qualify. Twenty-four characters of slack because the widest real one is
# twelve (DeepSeek's redacted `: ****0000 `), and every character past the
# evidence is a sentence about something else this pattern can reach into.
_INVALID_KEY_PATTERNS = (
    re.compile(
        r"(?:api[\s_-]*)?key\b[^.\n]{0,24}?\b(?:is|was)\s+"
        r"(?:no[\s_-]*longer[\s_-]*valid|not[\s_-]*valid|invalid|revoked|expired)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:api[\s_-]*)?key[\s_-]*(?:has[\s_-]*been[\s_-]*)?"
        r"(?:expired|revoked|deleted|disabled)",
        re.IGNORECASE,
    ),
)


def _is_auth_error(exc: Exception) -> bool:
    """Check if this is an authentication / invalid-key error (THIS key is bad).

    Covers a real 401 PLUS the provider variants that do NOT use 401 — notably
    Google/Gemini, which returns a 400 (reason API_KEY_INVALID, message "API key
    not valid" / "API key expired. Please renew the API key.") for a bad or
    expired key. Such a call must be handled as auth (quarantine the key + rotate
    to the next), NOT as a terminal 400 that aborts the run. (v1.0.3)

    v1.6.0.1: this answers "is this about the credential at all". It no longer
    answers "is the credential dead" — `_classify_auth_kind` does that, and the
    two questions have different answers for a bare 401.
    """
    if _evidence_status(exc) == 401:
        return True
    err_msg = _evidence_text(exc)
    if any(ind in err_msg for ind in _INVALID_KEY_INDICATORS):
        return True
    return any(pat.search(err_msg) for pat in _INVALID_KEY_PATTERNS)


def _is_revoked_key(exc: Exception) -> bool:
    """True only when the provider USED THE WORDS: this is not a key.

    The distinction this function exists for is the whole of v1.6.0.1's auth
    story. A bare 401 is ambiguous — it is what a proxy, a gateway and an
    expiring OAuth token all produce. A sentence naming the key as invalid,
    expired or revoked is not ambiguous, and it is the only evidence strong
    enough to take a credential out of rotation on the first refusal.

    Deliberately does NOT look at the status code. A 401 with no explanation is
    exactly the case this must not fire on.
    """
    err_msg = _evidence_text(exc)
    if any(ind in err_msg for ind in _INVALID_KEY_INDICATORS):
        return True
    return any(pat.search(err_msg) for pat in _INVALID_KEY_PATTERNS)


def _is_terminal_error(exc: Exception) -> bool:
    """Classify errors as terminal (don't retry) or transient (rotate key)."""
    err_msg = _evidence_text(exc)
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

    The scan walks the whole MRO, not just ``vars(cls)``. A rename alone is caught
    either way, but a rename PLUS a move into a base class would otherwise slip
    past shape detection and drop KAME to layer 3 — the exact scenario this
    function exists to survive. Patching still happens on ``cls`` itself, which
    correctly shadows the inherited method.
    """
    found = []
    try:
        import inspect as _inspect
        seen = set()
        for klass in getattr(cls, "__mro__", (cls,)):
            if klass is object:
                continue
            for name, attr in list(vars(klass).items()):
                if name.startswith("_") or name in seen:
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
                    seen.add(name)
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
                 "last_sleep_log_at", "last_long_heartbeat_at",
                 # v1.2.0 — the chat-side wait notice (see _kame_wait_notice_tick)
                 "cold_since", "notice_item", "notice_refreshed_at", "notice_broken",
                 # v1.6.0.1 — the live toast, which arrives long before the
                 # ninety-second chat item and is pushed over the WebSocket
                 "toast_refreshed_at", "toast_shown", "toast_broken")

    def __init__(self):
        self.sleep_count = 0
        self.cooldown_overhead_s = 0.0
        self.long_cool_logged = False
        self.last_sleep_log_at = 0.0
        self.last_long_heartbeat_at = 0.0
        self.cold_since = 0.0
        self.notice_item = None
        self.notice_refreshed_at = 0.0
        self.notice_broken = False
        self.toast_refreshed_at = 0.0
        self.toast_shown = False
        self.toast_broken = False


def _kame_pool_counts(identity: str, all_keys: list):
    """(healthy, total) for one pool, right now. Counts only — never a key."""
    now = time.time()
    with _KAME_LOCK:
        state = _KAME_KEY_HEALTH.get(identity, {}).get("keys", {})
        total = len(all_keys)
        healthy = sum(
            1 for k in all_keys
            if float((state.get(k) or {}).get("sick_until") or 0) < now
        )
    return healthy, total


#: One stable id, so every update REPLACES the previous notification rather
#: than stacking a new one beside it. `add_notification` looks an existing id up
#: and overwrites it in place, which is the whole reason a live wait can be
#: narrated without turning into a wall of toasts.
_KAME_TOAST_ID = "kame-rotation-wait"


def _kame_toast(kind: str, title: str, message: str, seconds: int = 4) -> bool:
    """Push one live notification, updating the previous one in place.

    v1.6.0.1. Best-effort by construction and returns False rather than raising:
    a wait that is not narrated is a worse experience, a wait that is not
    survived is a bug, and that ordering has been this plugin's rule since the
    chat notice was written.

    Counts only — the caller composes the text and no caller passes a key.
    """
    try:
        from helpers.notification import (
            NotificationManager, NotificationType, NotificationPriority,
        )
        NotificationManager.send_notification(
            type=NotificationType(kind),
            priority=NotificationPriority.NORMAL,
            message=message,
            title=title,
            display_time=seconds,
            group="kame",
            id=_KAME_TOAST_ID,
        )
        return True
    except Exception:
        # Older Agent Zero, a renamed helper, or no manager yet. The chat item
        # and the console still say everything this said.
        return False


def _kame_wait_notice_toast(st, identity, all_keys, waited: float) -> None:
    """The early half of the wait, on a surface that is pushed, not polled."""
    if st.toast_broken:
        return
    now = time.time()
    if waited < _KAME_NOTICE_TOAST_AFTER_S:
        return
    if st.toast_shown and (now - st.toast_refreshed_at) < _KAME_WAIT_NOTICE_REFRESH_S:
        return
    healthy, total = _kame_pool_counts(identity, all_keys)
    resting = total - healthy
    eta = _next_recovery_seconds(identity, all_keys)
    when = f" · next back in ~{_fmt_duration(eta)}" if eta else ""
    ok = _kame_toast(
        "progress",
        f"KAME — waiting for a key ({_fmt_duration(waited)})",
        f"{resting} of {total} keys resting on {identity}{when}. "
        f"No requests are being made while they cool; this resumes by itself.",
        # Just past the refresh cadence, so the toast is continuously replaced
        # while the wait lasts and disappears on its own if the process dies
        # mid-wait rather than hanging on screen for ever.
        seconds=int(_KAME_WAIT_NOTICE_REFRESH_S) + 5,
    )
    if not ok:
        st.toast_broken = True
        return
    st.toast_shown = True
    st.toast_refreshed_at = now


def _kame_wait_notice_tick(st, identity, all_keys, call_type, model_short) -> None:
    """Show — and keep refreshing — one chat log item while the pool is cold.

    Everything else KAME says about a wait goes to the console, which the person
    watching the chat is not reading. To them a pool waiting out a daily quota
    looks exactly like a hung agent, and the only move that looks available is
    restarting Agent Zero — which throws away the wait and the context with it.

    So the facts already in the console are put where the decision is made, as a
    SINGLE item that updates in place: how many keys are resting, when the first
    one is expected back, how long this has run, and that stop still works. The
    item carries counts and a pool name only, so it is safe on screen and in a
    screenshot, and it is UI-only — `context.log` never enters the model's
    history, so nothing here can change what the agent thinks it was told.

    Best-effort by construction: any failure marks the notice broken for the
    rest of this call and rotation carries on untouched. A wait that is not
    narrated is a worse experience; a wait that is not survived is a bug.
    """
    if not _KAME_WAIT_NOTICE:
        return
    now = time.time()
    if not st.cold_since:
        st.cold_since = now
    waited = now - st.cold_since

    # v1.6.0.1: the live toast comes first and much earlier. It is a separate
    # surface with its own failure flag, so a chat item that cannot be written
    # (a CLI run, a task runner, a test) does not also silence the toast, and
    # vice versa.
    _kame_wait_notice_toast(st, identity, all_keys, waited)

    if st.notice_broken:
        return
    if waited < _KAME_WAIT_NOTICE_AFTER_S:
        return
    if st.notice_item is not None and (now - st.notice_refreshed_at) < _KAME_WAIT_NOTICE_REFRESH_S:
        return

    try:
        agent = _KAME_CURRENT_AGENT.get()
        log = getattr(getattr(agent, "context", None), "log", None)
        if log is None:
            st.notice_broken = True     # no chat to talk to (CLI, tests, a task runner)
            return

        healthy, total = _kame_pool_counts(identity, all_keys)
        eta = _next_recovery_seconds(identity, all_keys)
        if eta is None:
            eta_line = "A key is free now — resuming."
        else:
            eta_line = (
                f"Earliest key expected back in ~{_fmt_duration(eta)} "
                f"(around {time.strftime('%H:%M:%S', time.localtime(now + eta))})."
            )
        resting = total - healthy
        content = (
            f"{resting} of {total} keys are resting on {identity}. {eta_line}\n"
            f"Waited {_fmt_duration(waited)} so far. No API calls are being made "
            f"while they cool.\n"
            f"This resumes by itself the instant a key answers. Press stop to cancel."
        )
        kvps = {
            "pool": identity,
            "model": f"{call_type}|{model_short}",
            "keys resting": f"{resting} of {total}",
            "earliest recovery": "now" if eta is None else f"~{_fmt_duration(eta)}",
            "waited": _fmt_duration(waited),
        }
        heading = f"KAME — waiting for an API key ({_fmt_duration(waited)})"

        if st.notice_item is None:
            st.notice_item = log.log(
                type="util", heading=heading, content=content, kvps=kvps
            )
        else:
            st.notice_item.update(heading=heading, content=content, kvps=kvps)
        st.notice_refreshed_at = now
    except Exception:
        st.notice_broken = True


def _kame_wait_notice_finish(st, outcome: str) -> None:
    """Close the wait notice: 'resumed' after a key answered, 'stopped' otherwise.

    Called on every way out of the carousel, so the chat never keeps a "waiting"
    item that is no longer true. A no-op when no notice was ever shown (the
    common case: the pool recovered before anybody could wonder)."""
    # v1.6.0.1: close the toast too, and close it FIRST — it is the surface most
    # likely to have been shown, because it starts at fifteen seconds rather
    # than ninety. A "waiting" toast left on screen after the wait ended is
    # worse than never having shown one.
    if st.toast_shown:
        st.toast_shown = False
        _waited = _fmt_duration(time.time() - st.cold_since) if st.cold_since else "?"
        if outcome == "resumed":
            _kame_toast("success", "KAME — a key came back",
                        f"A key answered after {_waited}. Continuing.", seconds=4)
        else:
            _kame_toast("info", "KAME — stopped waiting",
                        f"The wait ended after {_waited} without a key coming back.",
                        seconds=6)

    item = st.notice_item
    if item is None:
        return
    st.notice_item = None
    try:
        waited = _fmt_duration(time.time() - st.cold_since) if st.cold_since else "?"
        if outcome == "resumed":
            item.update(
                heading=f"KAME — a key came back after {waited}",
                content=f"A key answered after {waited} of waiting. Continuing.",
                kvps={"waited": waited, "outcome": "resumed"},
            )
        else:
            item.update(
                heading=f"KAME — stopped waiting after {waited}",
                content=f"The wait ended after {waited} without a key coming back.",
                kvps={"waited": waited, "outcome": "stopped"},
            )
    except Exception:
        st.notice_broken = True


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

    if not st.cold_since:
        st.cold_since = _now_t

    _slept = 0.0
    while _slept < wait:
        _slice = min(1.0, wait - _slept)
        await asyncio.sleep(_slice)
        _slept += _slice
        await _kame_honor_intervention()
        # v1.2.0: refresh the chat-side notice from inside the slice loop rather
        # than once per sleep. A sleep can be a full minute long; a countdown
        # that only moves every minute reads as frozen, which is the exact
        # impression the notice exists to remove.
        _kame_wait_notice_tick(st, identity, all_keys, call_type, model_short)
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
            try:
                await _kame_sleep_on_exhaustion(identity, all_keys, call_type, model_short, st)
            except BaseException:
                # The user pressed stop / nudged mid-sleep (InterventionException
                # comes out of _kame_honor_intervention, not out of the call
                # below), so the "waiting" item must not be left standing.
                _kame_wait_notice_finish(st, "stopped")
                raise
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
        #
        # v1.6.0.4: "anything" means the ANSWER. Reasoning is reset alongside it
        # and recorded apart, because a model that thinks and then says nothing is
        # the empty answer this loop is here to rotate around.
        ctx["progress"]["any"] = False
        ctx["progress"]["reasoning"] = False

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
                _kame_wait_notice_finish(st, "resumed")
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
                _kame_wait_notice_finish(st, "stopped")
                raise
            # Only a genuinely TERMINAL error surfaces — everything else (incl. a
            # transient mid-stream drop) is cooled + rotated, never re-raised
            # (the v1.0.4 eternal-carousel promise).
            if _is_terminal_error(e):
                _kame_wait_notice_finish(st, "stopped")
                raise e
            # v1.6.0.4: "partial output" has to mean output. Until now this line
            # was printed for any turn where the model had merely thought, which
            # on a reasoning model is every turn, and it told the reader their
            # answer had been cut when nothing had been shown at all.
            if ctx["progress"]["any"] and _lvl_normal():
                PrintStyle.warning(
                    f"[KAME] {call_type}|{model_short} {_key_display(key)} "
                    f"⚠️ mid-stream drop after partial output → rotating + retrying"
                )

            # Credential refusals. v1.6.0.1 splits what used to be one bucket,
            # because the strongest evidence this plugin can gather was being
            # worked out and then thrown away one line later:
            #
            #   revoked — the provider USED THE WORDS. Not a key. Out of
            #             rotation on the first one.
            #   auth    — a bare 401, no explanation. Could be a token mid-
            #             refresh, a proxy, an incident. Twenty seconds,
            #             offered last, out after three in a row.
            #
            # A 403 saying "this key may not use THIS MODEL" is neither: it is
            # classified as `denied` below, is scoped per provider:model, and is
            # never allowed to retire anything.
            if _is_auth_error(e):
                _auth_sc = getattr(e, "status_code", None)
                _auth_kind = "revoked" if _is_revoked_key(e) else "auth"
                applied = _mark_key_health(
                    identity, key, False, _KAME_REFUSAL_REST_S, _auth_kind
                )
                # v1.0.6: a refused credential is an actionable problem — always
                # shown, even at 'silent' (matches the documented "silent still
                # shows hard errors" promise).
                PrintStyle.warning(
                    f"[KAME] {call_type}|{model_short} {_key_display_auth(key)} "
                    f"{_friendly_error_msg(_auth_kind, applied, _auth_sc, e)}"
                    f"{_retirement_suffix(identity, key)}"
                )
                _maybe_log_full_error(
                    call_type, model_short, key, e, _auth_kind, applied, _auth_sc
                )
                # A credential refusal is always KAME's own number: no provider
                # tells you when a rejected key will start working.
                _tally_failure(identity, _auth_kind, _auth_sc, "kame")
            else:
                delay, kind, sc = _classify_error(e)
                # v1.6.0.3: computed once and used twice. The tally has always
                # wanted to know whether the provider named this number; so
                # does `_mark_key_health`, which must not raise a floor over a
                # deadline the provider stated.
                _sized_by = _delay_source(e, kind)
                applied = _mark_key_health(
                    identity, key, False, delay, kind, sized_by=_sized_by
                )
                _tally_failure(identity, kind, sc, _sized_by)
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

    v1.6.0.4 stopped counting REASONING as output. ``progress["any"]`` gates
    two things and both are about the answer: whether an empty result may be
    retried on another key, and whether the log may say "after partial output".
    A thinking model reasons on every turn and then, sometimes, returns nothing
    at all -- Gemini spending its whole budget on thoughts is exactly the
    empty-answer case this plugin exists to rotate around, and reasoning was
    switching that rotation off on precisely the models that need it. Thoughts
    now record ``progress["reasoning"]``, which nothing gates on and which is
    there so a reader of the ctx can still tell a silent model from a thinking
    one. The Hermes port carried the same confusion with a worse consequence
    and fixed it in the same release.
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
            progress["reasoning"] = True
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
            "progress": {"any": False, "reasoning": False},
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
            f"Please report this at https://github.com/Kame696/kame-api-rotation-for-agent-zero/issues"
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
    # v1.6.0.1: the version beside the build. The version is what somebody
    # typed into plugin.yaml; the build is a hash of the modules that are
    # actually on this disk. The one check worth making after an upgrade is
    # whether this string changed, and it belongs where the upgrade is watched.
    try:
        _b = _build_report()
        _line = f"     build {_b.get('fingerprint', '?')}"
        if _b.get("complete") is False:
            _line += f"  ⚠ INCOMPLETE — missing: {', '.join(_b.get('missing') or [])}"
        elif _b.get("degraded"):
            _line += f"  ({len(_b['degraded'])} optional file(s) absent)"
        PrintStyle(font_color="#96E").print(_line)
    except Exception:
        pass
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

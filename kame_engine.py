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

import asyncio, collections, contextvars, hashlib, json, math, os, secrets, sys, threading, time, re
from typing import Any, List
# v1.0.9: KAME no longer imports litellm, openai or logging. It used to call
# `acompletion()` itself and silence litellm's loggers; now Agent Zero makes the
# call, so those are A0's dependencies alone and a breaking change in any of them
# can no longer stop this plugin from even importing.
from langchain_core.messages import SystemMessage, HumanMessage
try:  # v1.8.2.7: the continuation of a cut answer is sent as the model's own turn
    from langchain_core.messages import AIMessage
except Exception:  # pragma: no cover - a stubbed langchain without it
    AIMessage = None
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
KAME_VERSION = "1.8.3.0"

# --- GLOBAL REGISTRY ---
_KAME_KEY_HEALTH = {}  # { "provider:model": { "keys": {key: {sick_until, last_used, request_log, last_sick_at, consecutive_rl}} } }
_KAME_ACCOUNT_HOLDS = {}  # {(provider, key): {until, kind, scope, at}}
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
_KAME_STATED_RL = {}  # { ("provider:model", key): float seconds } - learned per key (v1.8.1.0)
_KAME_PATCHED = False
_KAME_CALL_CONTEXT = contextvars.ContextVar('kame_ctx', default='')
#: v1.8.2.7: which job the running call is ("chat", "utility", "compress"), so
#: the Events view can keep the conversation apart from background calls.
_KAME_JOB = contextvars.ContextVar('kame_job', default='')
#: 1.8.3.0: which named background job (title_generation, compression ...) the running call is.
_KAME_TASK = contextvars.ContextVar('kame_task', default='')
#: v1.8.2.7: set while a call runs on a model of KAME's fallback list, so that
#: call never walks the list itself (one walk, owned by the outer call).
_KAME_FALLBACK_HOP = contextvars.ContextVar('kame_fallback_hop', default=None)
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

# --- what a 503/500 costs a key: one second, flat, forever ---
#
# The number is one second because a 503 is not metered and because, measured,
# five never did anything: on the owner's pool of fourteen the gap between a
# key's 503 and that same key being offered again was NEVER below 7.2 seconds
# across 59 real episodes. The carousel has thirteen others to try first and a
# lap costs more than the rest did. The constant only binds when the lap is
# shorter than the rest — a pool of one or two keys — and there the old five
# was four dead seconds per blip on the only credential available.
#
# One second rather than zero because a rest below a second is a spin, not a
# cooldown. It is the same floor the per-minute branch uses.
# A 503 ("server busy") is the provider being momentarily overloaded, NOT the
# key being spent — so it rests briefly and the carousel moves on.
#
# 07/09/2026: this is now FLAT. v1.0.1 had a per-key ladder behind it
# (5 -> 10 -> 20 -> 40 -> 80, capped at 90) whose purpose is worth keeping on
# the record, because removing it costs exactly what that comment predicted:
# on a LARGE pool the fast lap (15 keys at ~2.5s each) means a flat 5s cooldown
# recovers every key before it is re-tried, so the pool never goes cold, the
# ETA-driven sleep never takes over, and KAME rotates 503 -> 5s for as long as
# the outage lasts. An 83-minute Gemini outage is on record.
#
# The owner ended the ladder anyway, and the arithmetic is his: **a 503 is not
# metered.** It costs no quota, so a longer rest buys nothing and only holds
# back a credential that was never at fault. What made it more than a
# preference was the ladder climbing on a HEALTHY pool: the strike counter is
# per key and only that key's own success clears it, so a key that caught two
# blips while thirteen others answered normally still climbed. Measured in the
# Hermes port on the owner's real keys, 7 of 16 escalations above the base
# happened with another key answering inside the previous two minutes — one
# sidelining a healthy key for 40 seconds while its neighbour was serving.
#
# So the trade is: never punish a key for the model's bad minute, and accept
# that a long outage turns the carousel instead of sleeping through it. That is
# `decisions/0002-eternal-carousel-no-timeout` behaving as specified, it spends
# no quota, and `kame_collapse_storm_logs` keeps the log readable.
_KAME_SERVER_BASE_S = 1.0

# Kept only because `tests/test_v1_0_2_fixes.py` pins it and because a 5xx that
# states its own wait should still not be able to bench a key for an hour.
# Nothing invented reaches it any more.
_KAME_SERVER_BACKOFF_CAP_S = 90.0

# --- 1.7.0.4: um rotulo diario e evidencia, nao prova -----------------------
# Ported from the Hermes side, where it was measured on the owner's own
# fourteen keys rather than reasoned about. A key Google refuses with
# `GenerateRequestsPerDayPerProjectPerModel-FreeTier` answered again 6 to 36
# minutes later, twenty-one times out of twenty-one, and not one of those
# intervals reached the hour it was being given. Nothing in the payload tells
# the real exhaustions apart from the rest: same quotaId, same quotaValue,
# same prose, same retry hint.
#
# So the decision moves off the label and onto the pool. While ANY key on that
# provider:model is still answering, a daily label costs a re-probe. The hour
# is bought only once the whole pool has gone quiet, because a pool that has
# been silent this long is not being throttled, it is out.
#
# The silence threshold is measured too: probing fourteen keys every two
# minutes across four models, the longest the entire pool ever went without a
# single answer WHILE the model still had capacity was fifteen minutes
# (3.8-flash), fourteen (3.7) and twelve (3.6). Twenty minutes is that with
# margin.
#
# Erring long here is the cheap direction, unusually: being slow to believe
# the label costs refused requests, which are free of quota and take about a
# second, while believing it early costs every key in the pool for an hour.
_KAME_DAILY_REPROBE_S = 300.0
_KAME_POOL_SILENCE_BEFORE_THE_DAY_S = 1200.0

#: identity -> when this pool last went quiet. Cleared by any success on that
#: identity. Module level rather than per key on purpose: the question is
#: about the POOL, and a key on the bench is never asked, so counting its
#: refusals would measure what did not happen.
_KAME_NO_ANSWER_SINCE = {}

# --- v1.0.2: per-minute adaptive-backoff ceiling (separate from the daily one) ---
# Per-minute (RPM) limits recover in ~60s. The blind-daily safety net may still
# escalate a key that keeps failing, but a genuinely per-minute key must NOT climb
# toward the 1h daily ceiling. Cap per-minute escalation here instead.
_KAME_RL_BACKOFF_CAP_S = 300.0  # 5 min

# v1.8.0.0 (Hermes parity). No credential sits out longer than this, whatever
# set the hold: a provider's own stated wait, the daily cooldown, or this
# engine's escalation. Five real Codex refusals were measured holding a key for
# 3h+ on the provider's word alone. Past the ceiling the key is simply offered
# again; if the refusal repeats it is held again, so a genuinely long outage
# costs one refused request per interval instead of a healthy key sitting out
# for however long a provider claimed. Setting: ``max_hold_seconds``
# (KAME_MAX_HOLD), clamped to 60-86400.
_KAME_MAX_HOLD_S = 3600.0

# v1.8.0.2 (Hermes parity). A throttle that names no wait at all. Measured on
# the owner's own traffic: retried within 30s, a refused key answered 0 of 73
# times; over the whole recorded corpus 30s avoids 388 of 413 avoidable refused
# calls while delaying 6 answers. Setting: ``unsized_throttle_rest_seconds``
# (KAME_UNSIZED_REST), clamped to 0-300. Was a flat 20s.
_KAME_UNSIZED_THROTTLE_REST_S = 30.0

# v1.8.1.0 (Hermes parity). Gemini's bare ``429 RESOURCE_EXHAUSTED`` - no
# retryDelay, no Retry-After, no quotaId - rests the refused key 1s, then 2, 4,
# 8, 16, 32, 64 and holds at 64, instead of the flat rest above. It arrives on
# many keys at once and clears on all of them together, which points at a busy
# model rather than a spent quota; in real use the ladder made fewer calls per
# minute than the flat rest (24 against 29) without answering less. Only that
# shape (``_is_bare_resource_exhausted``), reset by an answer, and a number the
# provider states is always obeyed and never multiplied. Settings:
# ``unsized_throttle_backoff`` (KAME_UNSIZED_BACKOFF) and
# ``unsized_backoff_max_seconds`` (KAME_UNSIZED_BACKOFF_MAX, 1-3600).
_KAME_UNSIZED_BACKOFF = True

# v1.8.1.0. A timeout earns the zero rest above only if the attempt actually
# waited. Found by the first real session on Agent Zero v2.12: an
# APIConnectionError whose text mentioned "Timeout" failed in half a second,
# read as a timeout, rested 0s, and the pool spun through fourteen keys about
# twice a second. A failure faster than this is not a provider going quiet; it
# keeps the three seconds a connection error always had.
_KAME_REAL_TIMEOUT_MIN_S = 5.0

# v1.8.1.0 (Hermes RL_BASE_S). A per-minute rest the provider stated below a
# second is a spin, not a cooldown - floored here, and so is what it teaches.
_KAME_RL_FLOOR_S = 1.0
_KAME_FAST_FAILURE_REST_S = 3.0
_KAME_UNSIZED_BACKOFF_MAX_S = 64.0

# v1.8.1.0 — measured refutation (the Hermes port's `escalate.stretch`, decision
# 0004 D3). Every other number this engine uses is READ off a refusal; this is
# the only one it learns, and only from the one sequence that proves a deadline
# was too short: the key was held until its deadline, handed back, and refused
# the same way within three minutes — twice in a row. Then the next hold is
# doubled (x2, x4, capped x8), bounded per window and by the one-hour ceiling,
# and one answer from the key forgets it all.
#
# Narrower than the Hermes port on purpose: it widens only a wait that was
# MEASURED — a number the provider stated (or stated earlier for this key), or
# one named window's default. Gemini's bare-RESOURCE_EXHAUSTED ladder and the
# daily-label re-probe already have their own measured schedules, owner's rules
# both, and compounding a second doubling on top of them would change numbers
# that were validated in real sessions.
_KAME_SHORT_GRACE_S = 180.0
_KAME_STRETCH_MAX_FACTOR = 8.0
_KAME_STRETCH_CAPS = {"per_minute": 300.0, "tokens_per_minute": 300.0, "per_hour": 3600.0}

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

# --- v1.2.0 / 1.8.2.9: the wait, said where the user is actually looking ---
#
# Everything above writes to the console. That is the right place for an
# operator reading a Docker log and the wrong place for the person watching the
# chat: to them a pool that is waiting out a daily quota is an agent that has
# stopped, with no way to tell it from a hang.
#
# 1.2.0 put the facts in the chat as one log item after ninety seconds and 1.6.0.1
# raised a toast after fifteen. Both are gone (1.8.2.9, owner's order): the wait now
# lives on the chat's own status line, the "A0: Calling LLM..." heading of the log
# item Agent Zero already shows for the call, and in the chip. Time left only, the
# count of physical keys the chip shows, and nothing a person has to dismiss.
_KAME_WAIT_NOTICE = True
# How often the heading may change: a countdown, not a flood of updates.
_KAME_STATUS_LINE_EVERY_S = 2.0

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
#: 1.8.3.0: when the last call was answered (the Events tab's proof of life)
_KAME_CALLS = {"last_call_at": 0.0}
#: 1.8.3.0 (A9): what happened in this process, for the "This Agent Zero process" card of the Details fold
#: (Hermes' counters of the same names). Counted where the event is written, so the card and the Events
#: tab can never disagree.
_KAME_COUNTS = {"rotations": 0, "recovered": 0, "surfaced": 0, "waits": 0, "waited_s": 0.0,
                "stream_drops": 0, "stitched": 0}
_KAME_COUNT_OF_KIND = {"rotation": "rotations", "invalid_key": "rotations", "denied_model": "rotations",
                       "recovery": "recovered", "surfaced": "surfaced", "wait": "waits", "stitch": "stitched"}

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


def _stated_server_delay(exc):
    """The wait a busy provider named itself, or None. v1.8.1.4.

    Hermes' rule R26, ported: on a server failure only a *retry instruction*
    counts -- the SDK's own retry attribute, `Retry-After` / `Retry-After-Ms`,
    or the provider's sentence ("try again in 7s"). Quota-reset telemetry
    (`x-ratelimit-reset-requests: 180s` beside a 503) describes a counter, not
    the outage, and must not turn a one-second rest into three minutes.
    """
    if _KE is None:
        return None
    try:
        now = time.time()
        headers = [(k, v) for k, v in _KE._header_items(_KE.headers_of(exc))
                   if str(k).strip().lower() in ("retry-after", "retry-after-ms")]
        for secs, _src in (_KE._from_exception(exc), _KE._from_headers(headers, now),
                           _KE._from_text(_evidence_text(exc) or str(exc))):
            if secs is not None and 0 < secs <= _KAME_HARD_DELAY_CAP_S:
                return float(secs)
    except Exception:
        return None
    return None


def _delay_source(exc, kind: str) -> str:
    """provider / kame / default — see `_KAME_TALLY`."""
    if kind == "other":
        return "default"
    if kind == "server":
        return "provider" if _stated_server_delay(exc) is not None else "kame"
    # v1.8.1.0: a throttle the evidence reader sized says where its number
    # came from — the provider's field, header or sentence, or one window's
    # default (KAME's number). A long daily reset the provider stated is the
    # provider's, and it is served rather than re-probed.
    judgment = _kame_judgment(exc)
    if judgment is not None and judgment.family == "throttle" and judgment.seconds is not None:
        return "provider" if judgment.provider_sized else "kame"
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


def _kame_clamped(value, low, high):
    """``value`` as a float inside [low, high], or None when it is not a number."""
    if isinstance(value, bool):
        return None
    try:
        v = float(value)
    except (ValueError, TypeError, OverflowError):
        return None
    if v != v:  # NaN
        return None
    return max(low, min(high, v))


def set_max_hold(seconds) -> None:
    """v1.8.0.0: the ceiling on any hold. Clamped to 60-86400; junk is ignored."""
    global _KAME_MAX_HOLD_S
    v = _kame_clamped(seconds, 60.0, 86400.0)
    if v is not None:
        _KAME_MAX_HOLD_S = v


def set_unsized_throttle_rest(seconds) -> None:
    """v1.8.0.2: the rest after a throttle that names no wait. Clamped to 0-300."""
    global _KAME_UNSIZED_THROTTLE_REST_S
    v = _kame_clamped(seconds, 0.0, 300.0)
    if v is not None:
        _KAME_UNSIZED_THROTTLE_REST_S = v


def set_unsized_backoff(enabled) -> None:
    """v1.8.1.0: the RESOURCE_EXHAUSTED ladder on or off."""
    global _KAME_UNSIZED_BACKOFF
    flag = _kame_flag(enabled)
    if flag is not None:
        _KAME_UNSIZED_BACKOFF = flag


def set_unsized_backoff_max(seconds) -> None:
    """v1.8.1.0: where the RESOURCE_EXHAUSTED ladder stops growing. Clamped to 1-3600."""
    global _KAME_UNSIZED_BACKOFF_MAX_S
    v = _kame_clamped(seconds, 1.0, 3600.0)
    if v is not None:
        _KAME_UNSIZED_BACKOFF_MAX_S = v


# --- v1.8.1.0: the Hermes switches ------------------------------------------
# Same names, same meaning, same environment variables as the Hermes port, so
# one `.env` line reads the same on both hosts. Every one defaults to what
# KAME did before it existed.
_KAME_ROTATION_DISABLED = False   # KAME off: every call goes straight to A0
_KAME_SPREAD_DISABLED = False     # first ready key in list order, not the least loaded
_KAME_CAROUSEL_DISABLED = False   # one attempt per call: mark the key, hand A0 the error

# `kame_journal.py` holds the events ring, the refusal recorder, the call
# timings and the key-health file. Optional like every other KAME module: a
# half-copied directory costs those four and nothing else.
try:
    from usr.plugins.api_rotation_by_kame import kame_journal as _KJ
except Exception:
    try:
        import kame_journal as _KJ  # running from the plugin directory, e.g. in tests
    except Exception:
        _KJ = None


def set_rotation_disabled(enabled) -> None:
    """v1.8.1.0: turn KAME off without uninstalling it (`KAME_ROTATION_DISABLED`)."""
    global _KAME_ROTATION_DISABLED
    flag = _kame_flag(enabled)
    if flag is not None:
        _KAME_ROTATION_DISABLED = flag


def set_spread_disabled(enabled) -> None:
    """v1.8.1.0: first ready key in list order instead of the least loaded."""
    global _KAME_SPREAD_DISABLED
    flag = _kame_flag(enabled)
    if flag is not None:
        _KAME_SPREAD_DISABLED = flag


def set_carousel_disabled(enabled) -> None:
    """v1.8.1.0: one attempt per call; the refusal is sized and handed to A0."""
    global _KAME_CAROUSEL_DISABLED
    flag = _kame_flag(enabled)
    if flag is not None:
        _KAME_CAROUSEL_DISABLED = flag


def set_recorder_disabled(enabled) -> None:
    """v1.8.1.0: stop writing refusals.jsonl."""
    flag = _kame_flag(enabled)
    if flag is not None and _KJ is not None:
        _KJ.RECORDER_ON = not flag


def set_call_timings_disabled(enabled) -> None:
    """v1.8.1.0: stop writing calls.jsonl."""
    flag = _kame_flag(enabled)
    if flag is not None and _KJ is not None:
        _KJ.TIMINGS_ON = not flag


def set_share_pool_health(enabled) -> None:
    """v1.8.1.0: keep key holds on disk so they survive a restart (default on)."""
    flag = _kame_flag(enabled)
    if flag is not None and _KJ is not None:
        _KJ.SHARE_HEALTH_ON = flag


# --- v1.8.2.7: the model fallback list, speed and the wait bound ------------
# Agent Zero has no model fallback chain of its own (read on v2.14), so the list
# lives in KAME's settings: models the user picked, in order. Empty = nothing
# ever switches (the 1.8.1.8 behaviour). Every call starts on the configured
# model; the list is used only while that model cannot answer.
_KAME_FALLBACK_MODELS: list = []      # [{"provider", "name", "api_base", "label"}]
_KAME_FAILOVER_AFTER_S = 60.0         # every key resting longer than this -> next model; 0 = never
_KAME_MAX_TOTAL_WAIT_S = 0.0          # stop waiting and show the error after this; 0 = never
_KAME_HEDGE_MODE = "adaptive"         # off | adaptive | max ("speed boost")
_KAME_HEDGE_MAX_LEGS = 3              # one call + at most two backups
_KAME_PROBE_BURST_MAX = 16            # keys asked together when a pool runs dry; 0 = one by one

try:
    from usr.plugins.api_rotation_by_kame import kame_hedging as _KH
except Exception:
    try:
        import kame_hedging as _KH  # running from the plugin directory, e.g. in tests
    except Exception:
        _KH = None


def _kame_optional(name):
    """One optional KAME module, installed path first (never raises)."""
    import importlib as _importlib
    for qualified in (f"usr.plugins.api_rotation_by_kame.{name}", name):
        try:
            return _importlib.import_module(qualified)
        except Exception:
            continue
    return None


# v1.8.2.7 (Hermes 1.8.2.3 - 1.8.2.7 parity): learned per-minute pacing, the
# latency tiebreak, the region in the pool identity and the stitcher that
# continues a cut answer. Each is optional like every other KAME module.
_KP = _kame_optional("kame_pacing")
_KL = _kame_optional("kame_latency")
_KR = _kame_optional("kame_region")
_KS = _kame_optional("kame_stitch")

_KAME_STREAM_SILENCE_S = 0.0          # wait for the first token; 0 = off (Hermes default)
_KAME_STREAM_RESUME_LIMIT = -1        # continuations of one cut answer; -1 = no limit, 0 = off
_KAME_STREAM_STITCH_DISABLED = False
_KAME_HEDGING_DISABLED = False        # the Hermes switch; same as hedge_mode off
_KAME_PACING_RPM_OVERRIDES = ""
_KAME_KEY_REGION_OVERRIDES = ""
_KAME_NO_PREFILL = set()              # identities whose provider refused a trailing assistant turn
_KAME_SEEDED = set()                  # (identity, reasoning) whose backup trigger was seeded
_KAME_HEDGE_NUMBERS = {               # Hermes names, defaults and ranges
    "hedge_budget_pct": (10.0, 0.0, 50.0),
    "hedge_burst": (3.0, 1.0, 10.0),
    "hedge_quantile": (90.0, 50.0, 99.0),
    "hedge_cold_s": (6.0, 0.5, 120.0),
    "hedge_cold_reasoning_s": (30.0, 2.0, 300.0),
    "hedge_max_prompt_tokens": (32000.0, 1000.0, 1000000.0),
}
_KAME_HEDGE_VALUES = {k: v[0] for k, v in _KAME_HEDGE_NUMBERS.items()}


def _kame_hedge_mode() -> str:
    return "off" if _KAME_HEDGING_DISABLED else _KAME_HEDGE_MODE


def _kame_sync_hedging():
    if _KH is not None:
        _KH.configure(hedge_mode=_kame_hedge_mode(), **_KAME_HEDGE_VALUES)


#: 1.8.2.9: the kinds of refusal that step "Faster" back to Balanced for the model.
_KAME_QUOTA_KINDS = _KH.QUOTA_KINDS if _KH is not None else frozenset()


def _kame_ledger(kind, n=1) -> None:
    """1.8.2.9: one add to the speed ledger (replies, backup, race, burst, circle). Never raises."""
    try:
        if _KH is not None:
            _KH.SPEED_LEDGER.note(kind, n)
    except Exception:
        pass


def set_hedge_number(name, value) -> None:
    """v1.8.2.7: one of the Hermes speed-boost numbers (budget, burst, quantile, cold, prompt)."""
    row = _KAME_HEDGE_NUMBERS.get(name)
    if row is None:
        return
    v = _kame_clamped(value, row[1], row[2])
    if v is not None:
        _KAME_HEDGE_VALUES[name] = v
        _kame_sync_hedging()


#: v1.8.2.8 (Hermes decisions/0028): three choices that replace a number a
#: person had to guess. The numbers above are read only under "timed".
_KAME_MODEL_SWITCH = "auto"      # auto | timed | never
_KAME_FIRST_TOKEN_MODE = "auto"  # auto | timed | off
_KAME_REPLY_NOTICE = "always"    # always | switches | never
_KAME_CHOICES = {
    "model_switch": ("auto", "timed", "never"),
    "first_token_wait": ("auto", "timed", "off"),
    "reply_notice": ("always", "switches", "never"),
}


def _kame_choice(name, value, current):
    v = str(value or "").strip().lower()
    return v if v in _KAME_CHOICES[name] else current


def set_model_switch(value) -> None:
    """v1.8.2.8: when the fallback list answers - auto / timed / never."""
    global _KAME_MODEL_SWITCH
    _KAME_MODEL_SWITCH = _kame_choice("model_switch", value, _KAME_MODEL_SWITCH)


def set_first_token_wait(value) -> None:
    """v1.8.2.8: how long a silent key is waited for - auto / timed / off."""
    global _KAME_FIRST_TOKEN_MODE
    _KAME_FIRST_TOKEN_MODE = _kame_choice("first_token_wait", value, _KAME_FIRST_TOKEN_MODE)


def set_reply_notice(value) -> None:
    """v1.8.2.8: the line naming the model of each reply - always / switches / never."""
    global _KAME_REPLY_NOTICE
    _KAME_REPLY_NOTICE = _kame_choice("reply_notice", value, _KAME_REPLY_NOTICE)


def set_stream_silence_timeout(seconds) -> None:
    """v1.8.2.7: wait for the first token - a key silent this long is dropped (0 = off).
    v1.8.2.8: a number is a choice of its own - above 0 is "timed", 0 turns a
    timed wait off (`first_token_wait`, set after it, still wins)."""
    global _KAME_STREAM_SILENCE_S, _KAME_FIRST_TOKEN_MODE
    v = _kame_clamped(seconds, 0.0, 3600.0)
    if v is not None:
        _KAME_STREAM_SILENCE_S = v
        if v > 0:
            _KAME_FIRST_TOKEN_MODE = "timed"
        elif _KAME_FIRST_TOKEN_MODE == "timed":
            _KAME_FIRST_TOKEN_MODE = "off"


def set_stream_resume_limit(n) -> None:
    """v1.8.2.7: how often one cut answer may be continued (-1 = no limit, 0 = never)."""
    global _KAME_STREAM_RESUME_LIMIT
    v = _kame_clamped(n, -1.0, 10.0)
    if v is not None:
        _KAME_STREAM_RESUME_LIMIT = int(v)


#: 1.8.3.0 (A6): "Ignore my fallback list" (Hermes `never_fall_back_to_another_model`). On: the list is
#: never used and KAME waits for the keys, however long that takes.
_KAME_NO_MODEL_FALLBACK = False


def set_no_model_fallback(enabled) -> None:
    global _KAME_NO_MODEL_FALLBACK
    flag = _kame_flag(enabled)
    if flag is not None:
        _KAME_NO_MODEL_FALLBACK = flag


def set_stream_stitch_disabled(enabled) -> None:
    global _KAME_STREAM_STITCH_DISABLED
    flag = _kame_flag(enabled)
    if flag is not None:
        _KAME_STREAM_STITCH_DISABLED = flag


def set_hedging_disabled(enabled) -> None:
    global _KAME_HEDGING_DISABLED
    flag = _kame_flag(enabled)
    if flag is not None:
        _KAME_HEDGING_DISABLED = flag
        _kame_sync_hedging()


def set_pacing_rpm_overrides(raw) -> None:
    """v1.8.2.7: ``provider:model-pattern=rpm`` entries (comma separated)."""
    global _KAME_PACING_RPM_OVERRIDES
    _KAME_PACING_RPM_OVERRIDES = str(raw or "").strip()[:4000]
    if _KP is not None:
        _KP.SETTINGS["pacing_rpm_overrides"] = _KAME_PACING_RPM_OVERRIDES
        try:
            _KP.PACER._synced_at.clear()
        except Exception:
            pass


def set_key_region_overrides(raw) -> None:
    """v1.8.2.7: ``key-fingerprint=region`` entries, for an endpoint that hides its region."""
    global _KAME_KEY_REGION_OVERRIDES
    _KAME_KEY_REGION_OVERRIDES = str(raw or "").strip()[:4000]


def parse_fallback_models(raw) -> list:
    """The fallback list from the setting: one ``provider/model`` per line (or
    comma / semicolon separated), optionally ``provider/model @ https://api-base``.
    The model part may itself contain slashes (``openrouter/meta-llama/x``).
    Junk entries are dropped; duplicates keep their first place. Never raises."""
    items = raw if isinstance(raw, (list, tuple)) else re.split(r"[\n,;]+", str(raw or ""))
    out, seen = [], set()
    for item in items:
        if isinstance(item, dict):
            provider = str(item.get("provider") or "").strip().lower()
            name = str(item.get("name") or item.get("model") or "").strip()
            base = str(item.get("api_base") or "").strip()
        else:
            text = str(item or "").strip()
            if not text or text.startswith("#"):
                continue
            base = ""
            # " @ base" or "@http(s)://": a bare "@" belongs to the model name
            # (vertex_ai/claude-sonnet-4@20250514, review 2026-10-17).
            parts = re.split(r"\s+@\s*|@(?=https?://)", text, maxsplit=1)
            if len(parts) == 2:
                text, base = parts[0].strip(), parts[1].strip()
            if "/" not in text:
                continue
            provider, name = (part.strip() for part in text.split("/", 1))
            provider = provider.lower()
        if not provider or not name or not re.fullmatch(r"[a-z0-9_.\-]+", provider):
            continue
        ident = f"{provider}:{name.lower()}"
        if ident in seen:
            continue
        seen.add(ident)
        out.append({"provider": provider, "name": name, "api_base": base,
                    "label": f"{provider}/{name}", "identity": ident})
    return out[:12]


_FALLBACK_CAP = 12


def merge_fallback_rows(rows, existing="") -> dict:
    """1.8.2.9 (U5): the saved list text for the rows the editor sends, merged with what is saved.

    ``rows`` is ``[{provider, name, api_base?}]`` in the order shown. A row with a provider AND a
    model becomes one ``provider/name[ @ api_base]`` line; a row whose provider and model match a
    saved line keeps that line as written (casing, a hand-written ``@ api_base``) unless the row
    carries an endpoint of its own. Every saved item `parse_fallback_models` cannot read (a
    comment, a note) is kept after the list, untouched. At most 12 entries are saved; the reply
    counts the half-filled rows and the ones left out. Pure; never raises."""
    saved = {}
    junk = []
    for item in re.split(r"[\n,;]+", str(existing or "")):
        text = item.strip()
        if not text:
            continue
        parsed = parse_fallback_models(text)
        if parsed:
            saved.setdefault((parsed[0]["provider"], parsed[0]["name"].lower()), (text, parsed[0]["api_base"]))
        else:
            junk.append(text)
    lines, seen = [], set()
    incomplete = left_out = 0
    for row in rows if isinstance(rows, (list, tuple)) else []:
        row = row if isinstance(row, dict) else {}
        provider = str(row.get("provider") or "").strip().lower()
        name = str(row.get("name") or row.get("model") or "").strip()
        base = str(row.get("api_base") or "").strip()
        if (not provider or not name or not re.fullmatch(r"[a-z0-9_.\-]+", provider)
                or re.search(r"[\n\r,;]", name) or re.search(r"[\n\r,;]", base)):
            # a separator inside a name or an endpoint would split into extra entries on save (review L4)
            incomplete += 1
            continue
        slot = (provider, name.lower())
        if slot in seen:
            continue
        seen.add(slot)
        if len(lines) >= _FALLBACK_CAP:
            left_out += 1
            continue
        old = saved.get(slot)
        if old and not base:
            lines.append(old[0])
        else:
            lines.append(f"{provider}/{name}" + (f" @ {base}" if base else ""))
    text = "\n".join(lines + junk)
    return {"text": text, "entries": parse_fallback_models(text), "saved": len(lines),
            "incomplete": incomplete, "left_out": left_out, "kept": len(junk)}


def set_fallback_models(raw) -> None:
    """v1.8.2.7: KAME's own model fallback list (see `parse_fallback_models`)."""
    global _KAME_FALLBACK_MODELS
    try:
        _KAME_FALLBACK_MODELS = parse_fallback_models(raw)
    except Exception:
        pass


def set_failover_after(seconds) -> None:
    """v1.8.2.7: how long every key may rest before the next model answers (0-3600).
    v1.8.2.8: a number is a choice of its own - 0 is "never", a time other than
    the default is "timed" (`model_switch`, set after it, still wins)."""
    global _KAME_FAILOVER_AFTER_S, _KAME_MODEL_SWITCH
    v = _kame_clamped(seconds, 0.0, 3600.0)
    if v is not None:
        _KAME_FAILOVER_AFTER_S = v
        if v <= 0:
            _KAME_MODEL_SWITCH = "never"
        elif v != 60.0:
            _KAME_MODEL_SWITCH = "timed"


def set_max_total_wait(seconds) -> None:
    """v1.8.2.7: stop waiting and hand Agent Zero the last error after this (0 = never)."""
    global _KAME_MAX_TOTAL_WAIT_S
    v = _kame_clamped(seconds, 0.0, 604800.0)
    if v is not None:
        _KAME_MAX_TOTAL_WAIT_S = v


def set_hedge_mode(mode) -> None:
    """v1.8.2.7: speed boost - off, adaptive (default) or max."""
    global _KAME_HEDGE_MODE
    text = str(mode or "").strip().lower()
    if text in ("off", "adaptive", "max"):
        _KAME_HEDGE_MODE = text
        _kame_sync_hedging()


def set_hedge_max_legs(n) -> None:
    """v1.8.2.7: one call plus at most n-1 backups (1-3)."""
    global _KAME_HEDGE_MAX_LEGS
    v = _kame_clamped(n, 1.0, 3.0)
    if v is not None:
        _KAME_HEDGE_MAX_LEGS = int(v)


def set_probe_burst(n) -> None:
    """v1.8.2.7: the most keys asked together when a pool runs dry (0-32)."""
    global _KAME_PROBE_BURST_MAX
    v = _kame_clamped(n, 0.0, 32.0)
    if v is not None:
        _KAME_PROBE_BURST_MAX = int(v)


def current_settings() -> dict:
    """Every setting KAME understands, as it is in force right now."""
    return {
        "fallback_models": "\n".join(e["label"] + (f" @ {e['api_base']}" if e.get("api_base") else "")
                                     for e in _KAME_FALLBACK_MODELS),
        "model_switch": _KAME_MODEL_SWITCH,
        "failover_after_seconds": _KAME_FAILOVER_AFTER_S,
        "max_total_wait_seconds": _KAME_MAX_TOTAL_WAIT_S,
        "first_token_wait": _KAME_FIRST_TOKEN_MODE,
        "reply_notice": _KAME_REPLY_NOTICE,
        "hedge_mode": _KAME_HEDGE_MODE,
        "hedge_max_legs": _KAME_HEDGE_MAX_LEGS,
        "probe_burst_max_legs": _KAME_PROBE_BURST_MAX,
        "stream_silence_timeout_seconds": _KAME_STREAM_SILENCE_S,
        "stream_resume_limit": _KAME_STREAM_RESUME_LIMIT,
        "stream_stitch_disabled": _KAME_STREAM_STITCH_DISABLED,
        "hedging_disabled": _KAME_HEDGING_DISABLED,
        "pacing_rpm_overrides": _KAME_PACING_RPM_OVERRIDES,
        "key_region_overrides": _KAME_KEY_REGION_OVERRIDES,
        **{name: _KAME_HEDGE_VALUES[name] for name in _KAME_HEDGE_NUMBERS},
        "kame_log_level": _KAME_LOG_LEVEL,
        "daily_quota_cooldown_seconds": _KAME_DAILY_COOLDOWN_S,
        "max_hold_seconds": _KAME_MAX_HOLD_S,
        "unsized_throttle_rest_seconds": _KAME_UNSIZED_THROTTLE_REST_S,
        "unsized_throttle_backoff": _KAME_UNSIZED_BACKOFF,
        "unsized_backoff_max_seconds": _KAME_UNSIZED_BACKOFF_MAX_S,
        "key_log_style": _KAME_KEY_LOG_STYLE,
        "kame_log_full_errors": _KAME_LOG_FULL_ERRORS,
        "kame_collapse_storm_logs": _KAME_COLLAPSE_STORM_LOGS,
        "kame_wait_notice": _KAME_WAIT_NOTICE,
        # 1.8.3.0: the same two, under Hermes' names (the opposite switch)
        "storm_collapse_disabled": not _KAME_COLLAPSE_STORM_LOGS,
        "live_status_disabled": not _KAME_WAIT_NOTICE,
        "never_fall_back_to_another_model": _KAME_NO_MODEL_FALLBACK,
        "rotation_disabled": _KAME_ROTATION_DISABLED,
        "spread_disabled": _KAME_SPREAD_DISABLED,
        "carousel_disabled": _KAME_CAROUSEL_DISABLED,
        "refusal_recorder_disabled": (not _KJ.RECORDER_ON) if _KJ is not None else True,
        "call_timings_disabled": (not _KJ.TIMINGS_ON) if _KJ is not None else True,
        "share_pool_health": bool(_KJ.SHARE_HEALTH_ON) if _KJ is not None else False,
    }


_KAME_LAST_SETTINGS = None


def note_setting_changes() -> list:
    """Put every setting that changed since the last activation on the timeline.

    Called by the activation after the config and the environment are applied.
    The first call only remembers; later calls record one `setting` event per
    changed value, so "did that setting help?" can be read against the events
    around it instead of guessed. Returns the changed names.
    """
    global _KAME_LAST_SETTINGS
    now_settings = current_settings()
    before, _KAME_LAST_SETTINGS = _KAME_LAST_SETTINGS, now_settings
    if before is None:
        return []
    changed = [k for k in now_settings if before.get(k) != now_settings[k]]
    for name in changed:
        _kame_event("setting", reason=f"{name}: {before.get(name)} -> {now_settings[name]}")
    return changed


def _kame_event(kind, identity="", key="", reason="", code=None, seconds=None,
                detail="", sized_by=""):
    """One row on the events timeline. Never raises; never a raw key."""
    try:
        counted = _KAME_COUNT_OF_KIND.get(kind)
        if counted:
            _KAME_COUNTS[counted] += 1
            if kind == "wait" and isinstance(seconds, (int, float)) and not isinstance(seconds, bool):
                _KAME_COUNTS["waited_s"] += max(0.0, float(seconds))
    except Exception:
        pass
    if _KJ is None:
        return
    try:
        try:
            job = _KAME_JOB.get()
        except Exception:
            job = ""
        try:
            _KJ.EVENTS.add(kind, identity=identity, key=_key_short_id(key) if key else "",
                           reason=reason, code=code, seconds=seconds, detail=detail,
                           sized_by=sized_by, job=job, task=_KAME_TASK.get())
        except TypeError:  # an older kame_journal.py beside this engine
            _KJ.EVENTS.add(kind, identity=identity, key=_key_short_id(key) if key else "",
                           reason=reason, code=code, seconds=seconds, detail=detail,
                           sized_by=sized_by)
    except Exception:
        pass


# What each failure kind is called on the timeline. Short, and about the key.
_KAME_EVENT_REASON = {
    "per_minute": "throttled",
    "daily": "daily quota",
    "insufficient_quota": "out of credit",
    "server": "provider busy",
    "timeout": "no answer in time",
    "auth": "credential refused",
    "revoked": "not a valid key",
    "denied": "not allowed on this model",
    "other": "unrecognised error",
}


def _kame_event_kind(kind: str) -> str:
    if kind in ("auth", "revoked"):
        return "invalid_key"
    if kind == "denied":
        return "denied_model"
    return "rotation"


def quota_report() -> dict:
    """What `/kame-quota` shows: the pool report, whose rows carry why and how."""
    return pool_report()


def reset_counts() -> None:
    """`/kame-quota reset`: session counters and the tally start again.

    As on Hermes, live rests are left alone — `reset_pool` is the one that
    starts every key from zero.
    """
    with _KAME_LOCK:
        for name in list(_KAME_STATS):
            _KAME_STATS[name] = 0
        for name in list(_KAME_COUNTS):
            _KAME_COUNTS[name] = 0.0 if name == "waited_s" else 0
        _KAME_TALLY.clear()


def _kame_zero_key(kd) -> None:
    """One key row starts again as if it had never been tried. Call under `_KAME_LOCK`."""
    for field in ("sick_until", "last_sick_at", "consecutive_rl",
                  "consecutive_server", "consecutive_refusals",
                  "consecutive_denials", "consecutive_unsized",
                  "retired_at", "short_streak", "last_hold_until"):
        kd[field] = 0
    for field in ("rest_label", "hold_kind", "hold_scope",
                  "last_hold_kind", "last_hold_window"):
        kd[field] = ""
    kd["model_until"] = 0
    kd["model_kind"] = kd["model_scope"] = ""
    kd["_hold_projection"] = 0


def wake_model(identity) -> int:
    """1.8.2.9 (U2, "Wake this model"): the keys of ONE model start again, and the model is no
    longer known busy. Returns how many keys. Never touches a key or a setting.

    The holds kept on disk for those keys go too (as `note_answer` does after a good answer);
    the other models' keys keep their rests, except that a key shared with another model of the
    same provider loses its account-wide hold, which is what "this key is fine again" means.
    """
    keys = []
    with _KAME_LOCK:
        for ident, state in _KAME_KEY_HEALTH.items():
            if not _kame_names_match(ident, identity):
                continue
            provider = str(ident).split(":", 1)[0]
            for key, kd in (state.get("keys") or {}).items():
                _kame_zero_key(kd)
                keys.append((ident, key))
                _KAME_ACCOUNT_HOLDS.pop((provider, key), None)
                _KAME_STATED_RL.pop((ident, key), None)
        for ident in [i for i in _KAME_NO_ANSWER_SINCE if _kame_names_match(i, identity)]:
            _KAME_NO_ANSWER_SINCE.pop(ident, None)
    _kame_clear_busy(identity)
    if _KJ is not None:
        for ident, key in keys:
            try:
                _KJ.note_answer(ident, key)
            except Exception:
                pass
    _kame_event("setting", reason=f"{len(keys)} key(s) of {_kame_model_label(identity)} start again from zero")
    return len(keys)


def forget_busy(identity) -> bool:
    """1.8.2.9 (U2, "Try this model now"): the model is not known busy any more, so the next
    reply starts on it. Its keys are not touched. True when it was known busy."""
    now = time.time()
    with _KAME_LOCK:
        was_busy, _stamp = _kame_busy_state(identity, now)
    _kame_clear_busy(identity)
    if was_busy:
        _kame_event("setting", reason=f"{_kame_model_label(identity)} will be asked again now")
    return was_busy


def reset_pool() -> int:
    """Every key starts again as if it had never been tried. Returns how many.

    What `/kame-quota reset` and the settings page's button do. Clears the
    rests, the ladders, the refusal streaks, retirements, what each key was
    told, the daily doubt, and the holds kept on disk — the Hermes port found
    the hard way that clearing memory alone lets the file put every bench
    straight back. Never touches a key or a setting.
    """
    count = 0
    with _KAME_LOCK:
        for state in _KAME_KEY_HEALTH.values():
            for kd in (state.get("keys") or {}).values():
                count += 1
                _kame_zero_key(kd)
        _KAME_ACCOUNT_HOLDS.clear()
        _KAME_STATED_RL.clear()
        _KAME_NO_ANSWER_SINCE.clear()
        _KAME_PROBED_AT.clear()   # 1.8.2.9: a pool that starts again has not been asked while busy
        # Serialize reset with health writes: a concurrent refusal must not be
        # removed from disk after it was already applied to memory.
        if _KJ is not None:
            try:
                result = _KJ.forget_holds()
                if getattr(result, "persistence_ok", True) is False:
                    raise OSError("pool-health persistence failed")
            except Exception as exc:
                _kame_event("setting", reason="pool reset incomplete: memory cleared; persistence failed")
                raise RuntimeError(
                    f"Pool reset incomplete: {count} in-memory key(s) cleared; "
                    "persisted holds could not be cleared. Retry the reset."
                ) from exc
    _kame_event("setting", reason=f"pool cleared: {count} key(s) start again from zero")
    return count


def _kame_flag(value):
    """True / False from a bool or the usual spellings, None when unreadable."""
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in ("1", "true", "on", "yes"):
        return True
    if text in ("0", "false", "off", "no"):
        return False
    return None


def _kame_apply_env_overrides() -> None:
    """v1.8.1.0: the environment wins over the settings page, as on Hermes.

    Same variable names as the Hermes port, so one ``.env`` line means the same
    thing on both hosts. Called by the activation after the plugin config.
    """
    def _inverse(setter):
        def apply(raw):
            flag = _kame_flag(raw)
            if flag is not None:
                setter(not flag)
        return apply

    readers = (
        ("KAME_MAX_HOLD", set_max_hold),
        ("KAME_UNSIZED_REST", set_unsized_throttle_rest),
        ("KAME_UNSIZED_BACKOFF", set_unsized_backoff),
        ("KAME_UNSIZED_BACKOFF_MAX", set_unsized_backoff_max),
        # v1.8.1.0: the rest of the Hermes names.
        ("KAME_DAILY_COOLDOWN", set_daily_cooldown),
        ("KAME_ROTATION_DISABLED", set_rotation_disabled),
        ("KAME_SPREAD_DISABLED", set_spread_disabled),
        ("KAME_CAROUSEL_DISABLED", set_carousel_disabled),
        ("KAME_RECORDER_DISABLED", set_recorder_disabled),
        ("KAME_CALL_TIMINGS_DISABLED", set_call_timings_disabled),
        ("KAME_SHARE_POOL_HEALTH", set_share_pool_health),
        ("KAME_NO_MODEL_FALLBACK", set_no_model_fallback),
        ("KAME_STORM_COLLAPSE_DISABLED", _inverse(set_collapse_storm_logs)),
        ("KAME_LIVE_STATUS_DISABLED", _inverse(set_wait_notice)),
        # v1.8.2.7: the Hermes names where the two hosts share a meaning.
        ("KAME_FAILOVER_AFTER", set_failover_after),
        ("KAME_MAX_TOTAL_WAIT", set_max_total_wait),
        ("KAME_HEDGE_MODE", set_hedge_mode),
        ("KAME_PROBE_BURST", set_probe_burst),
        ("KAME_FALLBACK_MODELS", set_fallback_models),
        ("KAME_SILENT_STREAM_PATIENCE", set_stream_silence_timeout),   # Hermes' legacy names
        ("KAME_FIRST_TOKEN_PATIENCE", set_stream_silence_timeout),
        ("KAME_STREAM_SILENCE_TIMEOUT", set_stream_silence_timeout),
        # v1.8.2.8: after the numbers - a number means "timed", an explicit
        # choice set beside it still wins.
        ("KAME_MODEL_SWITCH", set_model_switch),
        ("KAME_FIRST_TOKEN_WAIT", set_first_token_wait),
        ("KAME_REPLY_NOTICE", set_reply_notice),
        ("KAME_STREAM_RESUME_LIMIT", set_stream_resume_limit),
        ("KAME_STREAM_STITCH_DISABLED", set_stream_stitch_disabled),
        ("KAME_HEDGING_DISABLED", set_hedging_disabled),
        ("KAME_HEDGE_MAX_LEGS", set_hedge_max_legs),
        ("KAME_PACING_RPM_OVERRIDES", set_pacing_rpm_overrides),
        ("KAME_KEY_REGION_OVERRIDES", set_key_region_overrides),
        ("KAME_HEDGE_BUDGET_PCT", lambda v: set_hedge_number("hedge_budget_pct", v)),
        ("KAME_HEDGE_BURST", lambda v: set_hedge_number("hedge_burst", v)),
        ("KAME_HEDGE_QUANTILE", lambda v: set_hedge_number("hedge_quantile", v)),
        ("KAME_HEDGE_COLD_S", lambda v: set_hedge_number("hedge_cold_s", v)),
        ("KAME_HEDGE_COLD_REASONING_S", lambda v: set_hedge_number("hedge_cold_reasoning_s", v)),
        ("KAME_HEDGE_MAX_PROMPT_TOKENS", lambda v: set_hedge_number("hedge_max_prompt_tokens", v)),
        # v1.8.2.9: 1 = background jobs wait like the chat (the 1.8.2.8 behaviour).
        ("KAME_AUX_UNBOUNDED", lambda v: set_aux_unbounded(v)),
    )
    for name, setter in readers:
        raw = os.environ.get(name)
        if raw is not None and str(raw).strip() != "":
            setter(raw)


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


def _pool_has_ready_key(identity: str, all_keys: list) -> bool:
    """Read-only readiness check used to interrupt a stale exhaustion sleep."""
    now = time.time()
    with _KAME_LOCK:
        pool = _KAME_KEY_HEALTH.get(identity, {}).get("keys", {})
        candidates = [k for k in all_keys if k in pool and not pool[k].get("retired_at")]
        if not candidates:
            candidates = [k for k in all_keys if k in pool]
        return any(float(pool[k].get("sick_until") or 0) < now for k in candidates)


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


def pool_report(context_id=None) -> dict:
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
                # v1.8.1.0: why it rests and which rung sized it, for /kame-quota.
                "why": (kd.get("hold_kind") or "") if state == "resting" else "",
                "label": (kd.get("rest_label") or "") if state == "resting" else "",
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
    active = bool(_KAME_PATCHED) and _KAME_LAYER in (1, 2)
    return {
        "version": KAME_VERSION,
        # 1.8.3.0 (A9): the process the numbers belong to, and, when KAME is loaded but not rotating, why
        # (Hermes' footer line and its "KAME is loaded but every call keeps the key ..." note).
        "pid": os.getpid(),
        # how many refusals in a row take a credential out of rotation (the page quotes it)
        "refusals_before_retiring": _KAME_REFUSALS_BEFORE_RETIRING,
        "reason": ("active" if active else
                   ("KAME has not bound to Agent Zero yet" if not _KAME_PATCHED
                    else f"KAME could not bind to Agent Zero's model calls (layer {_KAME_LAYER})")),
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
        # v1.8.1.0: the last decisions, newest first, and every setting as it
        # is in force. Fingerprints and KAME's own phrases only.
        "events": _KJ.EVENTS.recent(50) if _KJ is not None else [],
        # 1.8.3.0: the proof-of-life line of the Events tab (calls answered, and when the last one was)
        "counters": {
            "calls": _KAME_CALL_COUNT, "last_call_at": _KAME_CALLS["last_call_at"],
            **{name: (round(value, 1) if isinstance(value, float) else value) for name, value in _KAME_COUNTS.items()},
            "handed_over": _KAME_SPEED_STATS.get("model_switches", 0),
            "probe_bursts": _KAME_SPEED_STATS.get("probe_bursts", 0),
        },
        "settings": current_settings(),
        # v1.8.2.7: backups, keys asked together, model switches, the list.
        "speed": speed_report(),
        # v1.8.2.7: calls running longer than a minute, and why.
        "in_flight": _kame_in_flight_rows(now),
        # v1.8.2.7: what KAME found in Agent Zero when it bound.
        "host": host_seams(),
        # v1.8.2.8: the last replies - which model wrote each, how long it took.
        "replies": replies_report(),
        # v1.8.2.9: requests per model in the last 24 h, the chat's against background jobs.
        "quota_use": quota_use_view(now),
        # 1.8.2.9 (E9): the ONE view the chip, the status line, the menu and the page read.
        "view": _kame_safe_status_view(context_id, now),
        "data_dir": (str(_KJ.data_dir()) if (_KJ is not None and _KJ.data_dir() is not None) else ""),
        "generated_at": now,
    }


def _kame_safe_status_view(context_id, now) -> dict:
    """review L6: a view that fails must not take the whole status report (and every 1.8.2.8 field
    the Events and Background tabs read) down with it."""
    try:
        return status_view(context_id, now)
    except Exception:
        return {}


# --- 1.8.2.9 (E9): ONE view for every surface -------------------------------------------------
#
# The status line, the chip above the message box, the menu and the page all read the numbers in
# `status_view()` - never their own. Until 1.8.2.8 the toast counted one pool ("1 of 1 keys"), the
# chip summed every pool ("4 of 6 resting") and the toast title was the AGE of a wait while its
# body was the time LEFT. Here a key is a PHYSICAL key (provider + secret + endpoint): four models
# that share one key are one key, and a key is counted once whatever number of models use it.
# Field names are Hermes' panel snapshot names, so the page logic ports function for function.
# Counts and fingerprints only - never a key.

#: identity -> the endpoint (api_base) the model was last called with: part of what makes a key physical
_KAME_IDENTITY_BASE = {}
#: the identity of the conversation's own model (not a list model that answered for it)
_KAME_PRIMARY = {"identity": ""}

#: Hermes' key codes (`core/carousel.py` key_states): r ready, f answering now, s resting,
#: d out for today (daily / credit), x credential refused and still tried, o out of rotation
_KEY_STATE_WORD = {"r": "ready", "f": "answering", "s": "resting", "d": "out_today",
                   "x": "rejected", "o": "retired"}
_KEY_DAY_KINDS = frozenset({"daily", "insufficient_quota"})


def _kame_note_call(ctx, owner, identity) -> None:
    """Remember what the view needs from a call that starts: the endpoint, which model is the
    conversation's own, and the Agent Zero chat it belongs to. Never raises."""
    if ctx.get("main_chat"):
        _kame_reply_on_main(ctx)    # 1.8.3.0 (D1): settled where the reply starts, with its chat on the stack
    try:
        base = _kame_api_base(owner, ctx)
        with _KAME_LOCK:
            _KAME_IDENTITY_BASE[identity] = base
        if ctx.get("main_chat") and not _KAME_FALLBACK_HOP.get():
            _KAME_PRIMARY["identity"] = identity
        agent = _kame_frame_agent()
        chat = getattr(getattr(agent, "context", None), "id", None)
        fl = ctx.get("flight")
        if chat and isinstance(fl, dict):
            fl["context"] = str(chat)
    except Exception:
        pass


def _kame_key_code(sick_until, kind, retired, now) -> str:
    resting = sick_until > now
    if retired:
        return "o"
    if kind in _KAME_RETIRING_KINDS:
        return "x"
    if resting and kind in _KEY_DAY_KINDS:
        return "d"
    return "s" if resting else "r"


def _kame_view_order(identities) -> list:
    """The conversation's own model first, then the fallback list in the user's order, then the rest."""
    ordered, left = [], list(identities)

    def take(want):
        for ident in list(left):
            if _kame_names_match(ident, want):
                left.remove(ident)
                ordered.append(ident)
                return

    primary = _kame_primary_identity()
    if primary:
        take(primary)
    for entry in _KAME_FALLBACK_MODELS:
        take(entry["identity"])
    return ordered + sorted(left)


# --- 1.8.3.0 (D4): the keys Agent Zero holds are known before the first call -----------------

#: When the configured models were last read (monotonic; ``None`` = never) and what they were.
_KAME_KNOWN = {"at": None, "models": []}
_KAME_KNOWN_TTL_S = 5.0
#: Agent Zero's own settings names, for a version without the `_model_config` plugin.
_KAME_SETTINGS_SLOTS = (("chat", "chat_model"), ("utility", "util_model"), ("embedding", "embed_model"),
                        ("browser", "browser_model"))


def _kame_configured_slots() -> list:
    """``[(role, provider, name, api_base)]``: the models Agent Zero is configured with. Read from its
    own resolver (`plugins._model_config`, the configured preset's chat, utility and embedding slots),
    else from its settings (`chat_model_provider`...). Never raises; ``[]`` when nothing can be read."""
    try:
        import importlib
        mc = importlib.import_module("plugins._model_config.helpers.model_config")
        out = []
        for role, fn in (("chat", "get_chat_model_config"), ("utility", "get_utility_model_config"),
                         ("embedding", "get_embedding_model_config")):
            cfg = getattr(mc, fn)() or {}
            out.append((role, cfg.get("provider"), cfg.get("name"), cfg.get("api_base")))
        return out
    except Exception:
        pass
    try:
        from helpers import settings as _settings
        cfg = _settings.get_settings() or {}
        return [(role, cfg.get(f"{prefix}_provider"), cfg.get(f"{prefix}_name"), cfg.get(f"{prefix}_api_base"))
                for role, prefix in _KAME_SETTINGS_SLOTS]
    except Exception:
        return []


def _kame_known_models(force=False) -> list:
    """``[{identity, provider, api_base, role, keys}]``: every model Agent Zero is configured with and
    every model of the fallback list that has at least one key in Agent Zero's key field, read with the
    same lookup the call makes (`_kame_provider_keys`). Kept for a few seconds: the page polls. Raw
    keys stay inside this module's view builder; nothing of them is ever sent out."""
    now = time.monotonic()
    with _KAME_LOCK:
        if not force and _KAME_KNOWN["at"] is not None and now - _KAME_KNOWN["at"] < _KAME_KNOWN_TTL_S:
            return list(_KAME_KNOWN["models"])
    wanted = [(role, str(provider or "").strip().lower(), str(name or "").strip(), str(api_base or "").strip())
              for role, provider, name, api_base in _kame_configured_slots()]
    wanted += [("fallback", e["provider"].lower(), e["name"], e.get("api_base", "") or "")
               for e in list(_KAME_FALLBACK_MODELS)]
    models = []
    for role, provider, name, api_base in wanted:
        if not provider or not name:
            continue
        identity = f"{provider}:{name}".lower()
        if any(_kame_names_match(m["identity"], identity) for m in models):
            continue
        keys = _kame_provider_keys(provider)
        if keys:
            models.append({"identity": identity, "provider": provider, "api_base": api_base,
                           "role": role, "keys": keys})
    with _KAME_LOCK:
        _KAME_KNOWN["at"], _KAME_KNOWN["models"] = now, list(models)
    return models


def _kame_primary_identity() -> str:
    """The conversation's own model: the last one that answered, else (before any call) the chat model
    Agent Zero is configured with, so "your model" is first on screen from the start."""
    if _KAME_PRIMARY["identity"]:
        return _KAME_PRIMARY["identity"]
    try:
        for model in _kame_known_models():
            if model["role"] == "chat":
                return model["identity"]
    except Exception:
        pass
    return ""


def _kame_key_view(now=None, known=True) -> dict:
    """``{pools, key_groups, totals}`` over every model KAME has a key table for. 1.8.3.0: ``known``
    adds the models Agent Zero is configured with and that have not been called yet, with the keys in
    its key field, as ready keys of a pool marked ``unused`` (the status line passes ``False``: it
    does not read Agent Zero's settings from inside a wait)."""
    now = time.time() if now is None else float(now)
    with _KAME_LOCK:
        table = {
            ident: [(k, float(kd.get("sick_until") or 0), str(kd.get("hold_kind") or ""),
                     bool(kd.get("retired_at")), float(kd.get("last_used") or 0))
                    for k, kd in (state.get("keys") or {}).items()]
            for ident, state in _KAME_KEY_HEALTH.items()
        }
        bases = dict(_KAME_IDENTITY_BASE)
        live = {}
        for fl in _KAME_IN_FLIGHT.values():
            if fl.get("key") and fl.get("job") == "chat":
                live.setdefault(fl.get("identity"), set()).add(fl["key"])
    unused = set()
    if known:
        try:
            for model in _kame_known_models():
                if any(_kame_names_match(i, model["identity"]) for i in table):
                    continue
                base = model["api_base"]
                for ident, held in table.items():     # a key a used model holds is the same physical key
                    if ident.split(":", 1)[0] == model["provider"] and any(r[0] in model["keys"] for r in held):
                        base = bases.get(ident, base)
                        break
                table[model["identity"]] = [(k, 0.0, "", False, 0.0) for k in model["keys"]]
                bases[model["identity"]] = base
                unused.add(model["identity"])
        except Exception:
            pass
    pools, groups, physical = [], {}, {}
    for identity in _kame_view_order(list(table)):
        rows = sorted(table[identity], key=lambda r: _key_short_id(r[0]))
        provider = identity.split(":", 1)[0]
        base = bases.get(identity, "")
        label = _kame_model_label(identity)
        key_states, healthy, soonest, last_used = [], 0, None, 0.0
        for key, sick_until, kind, retired, used in rows:
            code = _kame_key_code(sick_until, kind, retired, now)
            fingerprint = _key_short_id(key)
            if code == "r" and fingerprint in live.get(identity, ()):
                code = "f"
            until = sick_until if sick_until > now else None
            key_states.append([fingerprint, code, until, kind])
            if code in ("r", "f"):
                healthy += 1
            elif until is not None and (soonest is None or until - now < soonest):
                soonest = until - now
            last_used = max(last_used, used)
            slot = (provider, key, base)
            physical.setdefault(slot, []).append(code)
            row = groups.setdefault((slot, "r" if code == "f" else code, kind if code != "r" else ""), {
                "provider": provider, "fingerprint": fingerprint, "api_base": base,
                "models": [], "code": "r" if code == "f" else code, "until_epoch": until, "kind": kind,
            })
            if label not in row["models"]:
                row["models"].append(label)
            if until is not None and (row["until_epoch"] is None or until < row["until_epoch"]):
                row["until_epoch"] = until      # the soonest the key is back for any of its models
        pools.append({
            "identity": identity, "label": label, "keys": len(rows), "healthy": healthy,
            "unused": identity in unused,
            "in_flight": sum(1 for s in key_states if s[1] == "f"),
            "resting": len(rows) - healthy,
            "soonest_s": round(soonest, 1) if (soonest is not None and not healthy) else None,
            "idle_for_s": round(now - last_used, 1) if last_used else None,
            "key_states": key_states,
        })
    for entry in _KAME_FALLBACK_MODELS:      # a list model KAME has not served yet: a ring with no keys
        if not any(_kame_names_match(p["identity"], entry["identity"]) for p in pools):
            pools.append({"identity": entry["identity"], "label": entry["name"], "keys": 0, "healthy": 0,
                          "in_flight": 0, "soonest_s": None, "idle_for_s": None, "key_states": []})
    pools = sorted(pools, key=lambda p: _kame_view_order_index(p["identity"]))
    key_groups = sorted(groups.values(), key=lambda g: (g["provider"], g["fingerprint"], g["code"]))
    for g in key_groups:
        g["state"] = _KEY_STATE_WORD[g["code"]]
        g["models"] = sorted(g["models"])
    totals = {"keys": len(physical), "ready": 0, "resting": 0, "out_today": 0, "rejected": 0, "retired": 0}
    for codes in physical.values():
        if any(c in ("r", "f") for c in codes):
            totals["ready"] += 1
            continue
        totals["resting"] += 1       # not ready: resting, out for today, refused or out of rotation
        if "d" in codes:
            totals["out_today"] += 1
        elif "x" in codes:
            totals["rejected"] += 1
        elif all(c == "o" for c in codes):
            totals["retired"] += 1
    totals["phrase"] = _kame_totals_phrase(totals)
    return {"pools": pools, "key_groups": key_groups, "totals": totals}


def _kame_view_order_index(identity) -> int:
    primary = _kame_primary_identity()
    names = ([primary] if primary else []) + [e["identity"] for e in _KAME_FALLBACK_MODELS]
    for i, want in enumerate(names):
        if _kame_names_match(identity, want):
            return i
    return len(names)


def _kame_every_words(seconds) -> str:
    """``every hour`` / ``every 12 min`` / ``every 20 s``: how often a repeated decision came."""
    s = float(seconds)
    if s >= 5400:
        hours = int(round(s / 3600.0))
        return "every hour" if hours == 1 else f"every {hours} h"
    if s >= 3300:
        return "every hour"
    return "every " + _kame_fmt_left(s)


def _kame_group_moves(events) -> list:
    """Repeated decisions as one row: consecutive events (newest first) with the same kind, the
    same key (or model, when the event names no key) and the same reason become
    ``{text, count, first_at, last_at, every_s, repeat}``; the page reads it as
    "6 times, every hour"."""
    out = []
    for ev in events:
        who = ev.get("key") or _kame_model_label(ev.get("identity", ""))
        sig = (ev.get("kind", ""), who, ev.get("reason", ""))
        if out and out[-1]["_sig"] == sig:
            row = out[-1]
            row["count"] += 1
            row["first_at"] = ev.get("at", row["first_at"])
            continue
        out.append({
            "_sig": sig,
            "text": " ".join(part for part in (str(ev.get("kind", "")).replace("_", " "), who,
                                                ev.get("reason", "")) if part),
            "kind": ev.get("kind", ""), "identity": ev.get("identity", ""), "key": ev.get("key", ""),
            "reason": ev.get("reason", ""), "job": ev.get("job", ""), "count": 1,
            "first_at": ev.get("at"), "last_at": ev.get("at"),
        })
    for row in out:
        row.pop("_sig", None)
        gap = (row["last_at"] - row["first_at"]) if row["count"] > 1 and row["last_at"] and row["first_at"] else 0
        row["every_s"] = round(gap / (row["count"] - 1), 1) if gap > 0 else None
        row["repeat"] = (f"{row['count']} times, {_kame_every_words(row['every_s'])}"
                         if row["every_s"] else (f"{row['count']} times" if row["count"] > 1 else ""))
    return out


def _kame_activity(key_view, context_id, now) -> dict:
    """What the selected chat (else the newest conversation call) is doing right now."""
    with _KAME_LOCK:
        flights = [dict(f) for f in _KAME_IN_FLIGHT.values() if f.get("job") == "chat"]
    if context_id:
        flights = [f for f in flights if f.get("context") == str(context_id)]
    out = {"kind": "idle", "identity": None, "model": None, "attempt": None, "healthy": None,
           "keys": None, "key": None, "started_at": None, "eta_s": None, "racing": [], "probe": None,
           "waiting_since": None, "text": None}
    if not flights:
        return out
    fl = max(flights, key=lambda f: float(f.get("started") or 0))
    pool = next((p for p in key_view["pools"] if p["identity"] == fl.get("identity")), None)
    eta = None if fl.get("eta_until") is None else max(0.0, round(float(fl["eta_until"]) - now, 1))
    out.update({
        "kind": "waiting" if fl.get("waiting") else "calling",
        "identity": fl.get("identity"), "model": _kame_model_label(fl.get("identity")),
        "attempt": fl.get("attempt"), "healthy": pool["healthy"] if pool else None,
        "keys": pool["keys"] if pool else None, "key": fl.get("key"),
        "started_at": fl.get("started"), "eta_s": eta, "racing": list(fl.get("racing") or []),
        "probe": fl.get("probe"), "waiting_since": fl.get("waiting_since"), "text": fl.get("status"),
    })
    return out


def _kame_fallback_view(now) -> dict:
    entries = []
    primary = _kame_primary_identity()
    for role, entry in ([("primary", {"identity": primary})] if primary else []) + \
            [("fallback", e) for e in _KAME_FALLBACK_MODELS]:
        identity = entry["identity"]
        with _KAME_LOCK:
            busy, probed = _kame_busy_state(identity, now)
            until = max([u for i, u in _KAME_BUSY_UNTIL.items() if _kame_names_match(i, identity)] or [0.0])
            kind = next((k for i, k in _KAME_BUSY_KIND.items() if _kame_names_match(i, identity)), "busy")
        entries.append({
            "identity": identity, "label": _kame_model_label(identity), "role": role,
            "busy_kind": kind if busy else None, "busy_until": until if busy else None,
            "probe_at": (probed + _KAME_PROBE_EVERY_S) if (busy and probed is not None) else None,
        })
    mode = _KAME_MODEL_SWITCH
    if not _KAME_FALLBACK_MODELS:
        state, why = "no_chain", ("Switching: off - no fallback models are set in Agent Zero, so KAME waits for your keys. "
                                  "Add some in the Fallback models tab and switching is automatic.")
    elif _KAME_NO_MODEL_FALLBACK:
        state, why = "off_by_flag", "Switching: off - \"Ignore my fallback list\" is on, so KAME waits for your keys."
    elif mode == "never":
        state, why = "off_by_zero", "Fallback: off - 'Fallback' is set to Never."
    elif mode == "timed" and _KAME_FAILOVER_AFTER_S <= 0:
        state, why = "off_by_zero", "Switching: off - your time is 0."
    elif mode == "timed":
        state, why = "on", (f"Switching: after your time - once you have waited {int(_KAME_FAILOVER_AFTER_S)} s with nothing "
                            "on screen, Agent Zero answers with the next model below, and comes back to your model "
                            "as soon as it can answer.")
    else:
        state, why = "on", ("Switching: automatic - when your provider is busy, Agent Zero answers with the next "
                            "model below at once; when your keys are resting, after a few seconds (KAME learns "
                            "how long your model usually takes). Your model comes back as soon as it can answer, "
                            "and the first model in the list is always tried first.")
    return {"state": state, "why": why, "warning": "", "entries": entries}


def status_view(context_id=None, now=None) -> dict:
    """The ONE view every surface reads (see the block comment above). Pure: builds, never writes."""
    now = time.time() if now is None else float(now)
    keys = _kame_key_view(now)
    events = _KJ.EVENTS.recent(50) if _KJ is not None else []
    return {
        "pools": keys["pools"],
        "key_groups": keys["key_groups"],
        "totals": keys["totals"],
        "activity": _kame_activity(keys, context_id, now),
        "fallback": _kame_fallback_view(now),
        "events": events,
        "moves": _kame_group_moves(events),
        "replies": replies_report(),
        "speed": speed_report(),
        "quota_use": quota_use_view(now),
        "generated_at": now,
    }


#: v1.8.1.4. The status code written in the text ("Error code: 429", "HTTP 429"),
#: never three digits inside another number. As a substring of this list, "429"
#: matched "you requested 34290 tokens", "prompt is too long: 214290 tokens"
#: and "token count (1429000)": a context-length 400 read as a throttle, was
#: never terminal, and every key was rested while the oversized request went
#: out again. Hermes had the same substring and gets the same rule.
_STATUS_429_IN_TEXT = re.compile(r"(?<![0-9.])429(?![0-9])")


def _names_a_throttle(err_msg: str, strict: bool = False) -> bool:
    """Whether the words name a throttle. `strict` (v1.8.1.4, as in Hermes): the
    phrases only, for a status that already blames the request -- under a 400,
    "Invalid value 429 for parameter max_tokens" is a value, not a status."""
    if strict:
        return any(ind in err_msg for ind in _RATE_LIMIT_INDICATORS)
    return (any(ind in err_msg for ind in _RATE_LIMIT_INDICATORS)
            or bool(_STATUS_429_IN_TEXT.search(err_msg)))


_REQUEST_FAULT_STATUSES = (400, 404, 405, 410, 413, 415, 422, 451, 501)


_RATE_LIMIT_INDICATORS = (
    "too many requests", "rate limit", "rate_limit",
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
    "45s", "683.050353ms" (Gemini, whenever the wait is under a second),
    "2970.938289688s", and a bare number ("90" -> 90s). Returns float or None.
    IMPORTANT: only call this on a substring already isolated as a duration
    (e.g. captured right after a "retry"/"try again in" keyword) - never on a
    whole error message, or stray digits (model names, ids) would be misread
    as seconds.

    07/09/2026: the millisecond spellings lead the alternation, and the order
    is the fix. Python takes the leftmost branch that matches, so with `m` ahead
    of `ms` the text "683.050353ms" surrendered its `m`, the trailing `s` was
    left for a pass that needed a digit first, and the hint was read as 683
    MINUTES. Found in the Hermes port on the owner's own pool, 07/09/2026:
    five keys benched between 4 and 12 hours off sub-second hints, and only a
    manual pool reset let them back in. This file had the identical defect and
    the identical exposure - `_extract_retry_delay` feeds `_apply_cooldown`
    directly - so it is corrected here in the same breath rather than left as
    a known bug in the other port.
    """
    if not text:
        return None
    total = 0.0
    found = False
    for val, unit in re.findall(
        r'(\d+(?:\.\d+)?)\s*'
        r'(milliseconds?|millis|msecs?|ms'
        r'|h|hr|hrs|hour|hours'
        r'|m|min|mins|minute|minutes'
        r'|s|sec|secs|second|seconds)?',
        text.lower(),
    ):
        if val == "":
            continue
        v = float(val)
        if unit in ("ms", "msec", "msecs", "milli", "millis",
                    "millisecond", "milliseconds"):
            total += v / 1000.0
        elif unit in ("h", "hr", "hrs", "hour", "hours"):
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


def _model_hold(kd):
    """Read the independent model component, accepting legacy state rows.

    sick_until remains an effective projection for older diagnostics. A legacy
    caller explicitly changing it supplies a new model deadline, not an account
    deadline; the provider component remains authoritative and independent.
    Call under _KAME_LOCK, like all health helpers.
    """
    until = float(kd.get("sick_until") or 0)
    projection = float(kd.get("_hold_projection", until) or 0)
    if "model_until" not in kd or until != projection:
        kd["model_until"] = until
        kd["model_kind"] = kd.get("hold_kind") or ("server" if kd.get("consecutive_server") else "")
        kd["model_scope"] = kd.get("hold_scope", "")
        # Record that this legacy/external sick_until value has been absorbed
        # into the model component. Without this acknowledgement a caller that
        # immediately shortens model_until (server thaw) would have that change
        # overwritten by the same stale projected sick_until on the next read.
        kd["_hold_projection"] = until
    return float(kd.get("model_until") or 0)


def _effective_hold_until(identity, key, kd):
    """Project max(model, account) without destroying either provenance."""
    # v1.8.1.5: never past the ceiling from *now*. Every hold is stored at most
    # `_KAME_MAX_HOLD_S` ahead of the moment it was set, so a component further
    # out than that means the wall clock stepped back since (NTP, a resume, a
    # hand-set clock) or the ceiling was lowered. Measured: a 30s rest read
    # 7229s after a two-hour step back. Every selection passes through here
    # (`_get_identity_state`), so this is where the hold is trimmed.
    ceiling = time.time() + _KAME_MAX_HOLD_S
    model_until = _model_hold(kd)
    if model_until > ceiling:
        kd["model_until"] = model_until = ceiling
    account = _KAME_ACCOUNT_HOLDS.get((str(identity).split(":", 1)[0], key), {})
    account_until = float(account.get("until") or 0)
    if account_until > ceiling:
        account["until"] = account_until = ceiling
    if account_until > model_until:
        kd["hold_kind"] = account.get("kind", "")
        kd["hold_scope"] = "account"
    else:
        kd["hold_kind"] = kd.get("model_kind", "")
        kd["hold_scope"] = kd.get("model_scope", "")
    kd["sick_until"] = kd["_hold_projection"] = max(model_until, account_until)
    return kd["sick_until"]


def _refresh_account_projections(provider, key):
    for ident, state in _KAME_KEY_HEALTH.items():
        if str(ident).split(":", 1)[0] == provider and key in state.get("keys", {}):
            _effective_hold_until(ident, key, state["keys"][key])


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
            # v1.8.1.0: a hold the previous process was serving comes back,
            # never longer than the ceiling allows now.
            if _KJ is not None:
                try:
                    held = _KJ.hold_for(identity, k)
                except Exception:
                    held = None
                if held:
                    kd_new = state["keys"][k]
                    model = held.get("model")
                    account = held.get("account")
                    if "model" not in held and "account" not in held:
                        if held.get("scope") == "account":
                            account = held
                        else:
                            model = held
                    if model:
                        kd_new["model_until"] = min(float(model["until"]), now + _KAME_MAX_HOLD_S)
                        kd_new["model_kind"] = model.get("kind", "")
                        kd_new["model_scope"] = model.get("scope", "")
                    if account:
                        account = dict(account)
                        account["until"] = min(float(account["until"]), now + _KAME_MAX_HOLD_S)
                        slot = (str(identity).split(":", 1)[0], k)
                        if account["until"] > _KAME_ACCOUNT_HOLDS.get(slot, {}).get("until", 0):
                            _KAME_ACCOUNT_HOLDS[slot] = account
                    kd_new["last_sick_at"] = float(held.get("at", now))
        else:
            # Defensive: backfill for keys created on earlier versions.
            state["keys"][k].setdefault("last_sick_at", 0)
            state["keys"][k].setdefault("consecutive_rl", 0)
            state["keys"][k].setdefault("consecutive_server", 0)
            state["keys"][k].setdefault("consecutive_refusals", 0)
            state["keys"][k].setdefault("consecutive_denials", 0)
            state["keys"][k].setdefault("retired_at", 0)
            state["keys"][k]["last_offered"] = now
        _effective_hold_until(identity, k, state["keys"][k])

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
                     sized_by: str = "", bare: bool = False, scope: str = "",
                     window: str = ""):
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
      - per_minute: **does not escalate** since v1.6.0.3. Every strike trusts
        the provider's own retryDelay, and when it named none the classifier's
        flat 20s stands. Repeating a throttle is not evidence the provider
        lied — on a rolling window it is the ordinary case. The streak is still
        counted, for the reports, but it no longer sizes anything.
      - server (5xx): **does not escalate** since 07/09/2026. Flat
        _KAME_SERVER_BASE_S, or a longer wait the provider itself stated. A 503
        is not metered, so a longer rest buys nothing; and the old per-key
        ladder was measured climbing while the rest of the pool answered fine.
        See the constant for the full reasoning and for what this costs during
        a sustained outage. `consecutive_server` is still counted because
        `_thaw_server_cooled_keys` reads it.
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
        _model_hold(kd)
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
            kd["consecutive_unsized"] = 0  # v1.8.1.0: the ladder starts again at 1s
            kd["rest_label"] = ""
            kd["hold_kind"] = ""
            kd["model_until"] = kd["_hold_projection"] = 0
            kd["model_kind"] = kd["model_scope"] = kd["hold_scope"] = ""
            # v1.8.1.0: the key answered, so no deadline of its is proven short.
            kd["short_streak"] = 0
            kd["last_hold_until"] = 0
            # v1.8.1.0: what this key was told is forgotten the moment it
            # answers, so the memory cannot outlive its evidence by one call.
            _KAME_STATED_RL.pop((identity, key), None)
            kd["retired_at"] = 0
            # 1.7.0.4. The pool is demonstrably serving, so any run of daily
            # labels that was accumulating doubt starts over. One answer on
            # ANY key of this identity is the whole of the evidence.
            _KAME_NO_ANSWER_SINCE.pop(identity, None)
            # v1.8.1.0: an account-wide hold this key carries on the provider's
            # other models is refuted by this answer — the account is serving.
            provider = str(identity).split(":", 1)[0]
            _KAME_ACCOUNT_HOLDS.pop((provider, key), None)
            _refresh_account_projections(provider, key)
            _KAME_STATS["ok"] += 1
            _KAME_CALL_COUNT += 1
            _KAME_CALLS["last_call_at"] = time.time()
            if _KJ is not None:
                _KJ.note_answer(identity, key)
        else:
            applied = float(delay)
            measured = False  # v1.8.1.0: may measured refutation widen this?
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
            elif kind == "insufficient_quota":
                # Money, not a window. No amount of pool liveness makes a key
                # with no credit work, so this one keeps the full cooldown
                # unconditionally — which is why 1.7.0.4 splits it out of the
                # branch below rather than leaving the two sharing a verdict
                # they no longer share a reason for.
                cnt = int(kd.get("consecutive_rl", 0)) + 1
                kd["consecutive_rl"] = cnt
                applied = max(applied, _KAME_DAILY_COOLDOWN_S)
            elif kind == "daily":
                # 1.7.0.4. **A daily label is evidence, not proof.** Ported
                # from the Hermes side with the measurements that produced it;
                # see `_KAME_DAILY_REPROBE_S` for the numbers.
                #
                # Until now this branch read `max(applied, escalated)` with
                # `applied` already forced to the full cooldown by
                # `_classify_error`, so the ladder behind it could never fire
                # and the first daily label cost the hour outright. That is the
                # behaviour being replaced, not a ladder being removed.
                #
                # `_delay_source` reports `"kame"` for every daily refusal, and
                # it is right: this file deliberately discards Google's daily
                # retryDelay (v1.0.5, and the Hermes port later measured why —
                # 107 of 114 of those hints were exactly the seconds left in
                # the current clock minute). So the number arriving here is
                # always ours, and ours is the one to reconsider.
                cnt = int(kd.get("consecutive_rl", 0)) + 1
                kd["consecutive_rl"] = cnt
                since = _KAME_NO_ANSWER_SINCE.setdefault(identity, now)
                pool_alive = (now - since) < _KAME_POOL_SILENCE_BEFORE_THE_DAY_S
                if sized_by == "provider":
                    measured = True
                    # v1.8.1.0 (Hermes 1.7.0.2's rule, ported): a long reset the
                    # provider itself STATED — OpenRouter's free-models-per-day
                    # moment, a `resets_at` — is served, not re-probed. Only a
                    # bare daily label was ever the doubtful case. The ceiling
                    # below still bounds it.
                    applied = float(applied)
                elif pool_alive:
                    # The label says the day is over and the pool says
                    # otherwise, by answering. Re-probe.
                    applied = min(_KAME_DAILY_REPROBE_S, _KAME_DAILY_COOLDOWN_S)
                else:
                    # Nobody has answered on this identity for the whole
                    # silence window. The label is no longer contradicted.
                    applied = max(applied, _KAME_DAILY_COOLDOWN_S)
            elif kind == "per_minute":
                # v1.6.0.3: repeating a throttle is not evidence the provider
                # lied, so a number the provider named is never multiplied; the
                # streak is counted for the reports and sizes nothing it named.
                #
                # v1.8.1.0, in the order the Hermes port decides it
                # (``core/carousel.py::_escalate``):
                #   1. stated for THIS refusal      -> that number, floored at 1s
                #   2. stated earlier for THIS key  -> that number, floored at 1s
                #      on this model (learned per key, forgotten on an answer)
                #   3. never stated, Gemini's bare  -> the ladder 1, 2, 4 ... 64
                #      RESOURCE_EXHAUSTED shape
                #   4. never stated, anything else  -> the flat unsized rest (30s)
                # A sub-second rest is a spin rather than a cooldown, hence the
                # floor; and only a number the provider named resets the ladder.
                kd["consecutive_rl"] = int(kd.get("consecutive_rl", 0)) + 1
                learned_at = (identity, key)
                learned = float(_KAME_STATED_RL.get(learned_at, 0.0) or 0.0)
                if sized_by == "provider":
                    if 0 < applied <= _KAME_RL_BACKOFF_CAP_S:
                        _KAME_STATED_RL[learned_at] = max(learned, float(applied))
                    applied = max(float(applied), _KAME_RL_FLOOR_S)
                    kd["consecutive_unsized"] = 0
                    kd["rest_label"] = ""
                    measured = True
                elif learned > 0:
                    applied = max(min(learned, _KAME_RL_BACKOFF_CAP_S), _KAME_RL_FLOOR_S)
                    kd["consecutive_unsized"] = 0
                    kd["rest_label"] = ""
                    measured = True
                elif bare and _KAME_UNSIZED_BACKOFF:
                    # Saturated before exponentiation so a long streak cannot
                    # overflow ``2.0 ** n`` before ``min`` ever runs.
                    rung = int(kd.get("consecutive_unsized", 0)) + 1
                    kd["consecutive_unsized"] = rung
                    cap = max(1.0, min(_KAME_MAX_HOLD_S, _KAME_UNSIZED_BACKOFF_MAX_S))
                    exponent = rung - 1
                    applied = cap if exponent >= math.log2(cap) else min(2.0 ** exponent, cap)
                    kd["rest_label"] = "backoff.%d" % rung
                else:
                    # The flat rest `_extract_retry_delay` already supplied —
                    # or, v1.8.1.0, one named window's default (a PerMinute
                    # quota id with no number rests 65s), which is measured.
                    kd["consecutive_unsized"] = 0
                    kd["rest_label"] = ""
                    measured = window in _KAME_STRETCH_CAPS
            elif kind == "server":
                # 07/09/2026: **a 5xx never escalates.** The owner's rule, and
                # the arithmetic is his: a 503 is not metered. It costs no
                # quota, so resting a key longer buys nothing at all and only
                # holds back a credential that was never at fault.
                #
                # v1.0.1 had put a ladder here — 5 -> 10 -> 20 -> 40 -> 80,
                # capped — so that a SUSTAINED outage would eventually take the
                # pool cold and hand over to the EXHAUSTED_RETRY ETA-sleep
                # instead of spinning. It was written after a real 83-minute
                # Gemini outage and it did work for that case.
                #
                # What ended it is the case it was never asked about. The
                # counter is per key and only that key's own success clears
                # it, so a key that caught two blips while the rest of the pool
                # answered normally still climbed. Measured in the Hermes port
                # on the owner's own keys: 7 of 16 escalations above the base
                # happened with another key answering inside the previous two
                # minutes, one of them sidelining a healthy key for 40 seconds
                # while its neighbour was serving. A 5xx is the MODEL's
                # condition, not the credential's — this file has said so since
                # v1.0.2 — and a counter asking "how often has this key seen
                # one" is asking about the credential.
                #
                # The trade, named rather than left to be discovered: during a
                # genuinely sustained outage the pool no longer goes cold, so
                # the ETA-sleep does not engage and the carousel keeps turning.
                # That is `decisions/0002-eternal-carousel-no-timeout`, it
                # spends no quota, and the storm-collapse keeps the log
                # readable. The owner made this trade knowingly.
                #
                # The counter itself is still kept: `_thaw_server_cooled_keys`
                # uses it to tell a key benched by an outage from a key benched
                # by its own quota, and thawing the second kind would wipe a
                # real cooldown.
                kd["consecutive_server"] = int(kd.get("consecutive_server", 0)) + 1
                applied = max(applied, _KAME_SERVER_BASE_S)
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
            if kind != "per_minute":
                # Not a throttle at all (a 503, a timeout, a refusal): the
                # ladder's count is left where it is - a busy model mixes 503s
                # into its bare 429s - but this rest is not a rung.
                kd["rest_label"] = ""
            # v1.8.1.0: measured refutation. See `_KAME_SHORT_GRACE_S`.
            if kind in ("per_minute", "daily"):
                lane = window or ("per_day" if kind == "daily" else "per_minute")
                prev_until = float(kd.get("last_hold_until", 0) or 0)
                landed = (prev_until > 0 and kd.get("last_hold_kind") == kind
                          and kd.get("last_hold_window") == lane
                          and 0.0 <= now - prev_until <= _KAME_SHORT_GRACE_S)
                streak = int(kd.get("short_streak", 0)) + 1 if landed else 0
                kd["short_streak"] = streak
                if measured and streak >= 2:
                    factor = (_KAME_STRETCH_MAX_FACTOR if streak - 1 >= math.log2(_KAME_STRETCH_MAX_FACTOR)
                              else 2.0 ** (streak - 1))
                    widened = min(float(applied) * factor, _KAME_STRETCH_CAPS.get(lane, _KAME_HARD_DELAY_CAP_S))
                    if widened > applied:
                        applied = widened
                        kd["rest_label"] = "stretch.x%d" % int(factor)
            else:
                # A different kind of failure breaks the chain: a 503 between
                # two throttles says nothing about the throttle's deadline.
                kd["short_streak"] = 0
                lane = ""
            # v1.8.0.0: the ceiling, on what is STORED and not only on what is
            # reported. Every branch above ends here.
            applied = min(float(applied), _KAME_MAX_HOLD_S, _KAME_HARD_DELAY_CAP_S)
            kd["last_hold_until"] = now + applied
            kd["last_hold_kind"] = kind
            kd["last_hold_window"] = lane
            kd["last_sick_at"] = now
            if kind == "insufficient_quota" or scope == "account":
                # v1.8.0.0: out of credit belongs to the account, not to the
                # model. The same key is benched on every model of this
                # provider it has been seen on; other keys are untouched.
                # v1.8.1.0: so does a throttle the evidence names account-wide
                # (Codex `usage_limit_reached`, OpenRouter's free-models bucket,
                # a `PerProject` quota) — bounded by the same ceiling, and
                # cleared on every model by this key's next answer.
                provider = str(identity).split(":", 1)[0]
                slot = (provider, key)
                previous = _KAME_ACCOUNT_HOLDS.get(slot, {})
                deadline = max(float(previous.get("until", 0) or 0), now + applied)
                _KAME_ACCOUNT_HOLDS[slot] = {
                    "until": deadline, "kind": kind, "scope": "account", "at": now,
                }
                _refresh_account_projections(provider, key)
                if _KJ is not None:
                    _KJ.note_hold(identity, key, deadline, kind, "account")
            else:
                previous_until = float(kd.get("model_until", 0) or 0)
                deadline = max(previous_until, now + applied)
                kd["model_until"] = deadline
                if now + applied >= previous_until:
                    kd["model_kind"] = kind
                    kd["model_scope"] = str(scope or "")
                _effective_hold_until(identity, key, kd)
                if _KJ is not None:
                    _KJ.note_hold(identity, key, deadline, kd.get("model_kind", kind), kd.get("model_scope", ""))
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
            model_until = _model_hold(kd)
            if (int(kd.get("consecutive_server", 0)) > 0
                    and kd.get("model_kind", "server") == "server"
                    and model_until > now):
                # small per-key stagger so they don't all re-probe in lockstep
                target = now + new_cooldown + (i % 5) * 0.4
                if target < model_until:
                    kd["model_until"] = target
                    kd["model_kind"] = "server"
                    kd["model_scope"] = ""
                    _effective_hold_until(identity, k, kd)
                    if _KJ is not None:
                        _KJ.note_hold(identity, k, target, "server", "")
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
    # " | " rather than " ": a retry hint at the end of one part must not
    # read the next part's digits as more of its duration.
    return " | ".join(parts)[:_EVIDENCE_MAX_CHARS].lower()


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


# --- v1.8.1.0: the evidence reader both ports judge with ---
# `kame_evidence.py` is the Hermes port's error reader (its catalogue of
# provider field values, its prose tables and its sizing cascade), ported so
# the two hosts give the same verdict on the same payload. It is consulted
# first; when it has nothing to stake a verdict on it returns None and every
# rule below decides exactly as it did before.
try:
    from usr.plugins.api_rotation_by_kame import kame_evidence as _KE
except Exception:
    try:
        import kame_evidence as _KE  # running from the plugin directory, e.g. in tests
    except Exception:
        _KE = None


# v1.8.2.7: the Hermes port's provider quirks (`core/provider_quirks.py`, 82
# providers learned from their own documentation and graded against independent
# answer keys). Evidence only: no rule looks at the provider's name (R01).
try:
    from usr.plugins.api_rotation_by_kame import kame_quirks as _KQ
except Exception:
    try:
        import kame_quirks as _KQ  # running from the plugin directory, e.g. in tests
    except Exception:
        _KQ = None

#: v1.8.2.7 (Hermes decisions/0024): statuses that ARE a timeout, whatever the
#: body says. The key is fine: rest 0 s, rotate once; the second in a row goes
#: back to Agent Zero (or to the next model of the fallback list).
_KAME_TIMEOUT_STATUSES = frozenset({408, 499, 504, 524})


def _quirk_body(exc):
    """The structured body a provider sent, for `kame_quirks` (never raises)."""
    try:
        body = getattr(exc, "body", None)
        if body:
            return body
        response = getattr(exc, "response", None)
        if response is not None:
            raw = getattr(response, "text", None)
            if isinstance(raw, (str, bytes)) and raw:
                return raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
    except Exception:
        pass
    return None


def _kame_quirk(exc, kind=""):
    """`kame_quirks.reinterpret` for one exception: a better kind, or None."""
    if _KQ is None or exc is None:
        return None
    try:
        return _KQ.reinterpret(_evidence_status(exc), kind or "other", str(exc), _quirk_body(exc))
    except Exception:
        return None


def _kame_closed_door(exc):
    """A 503 / no-instance / local-model-not-loaded: every key meets the same door."""
    if _KQ is None or exc is None:
        return False
    try:
        return bool(_KQ.names_maintenance(_evidence_status(exc), str(exc), _quirk_body(exc)))
    except Exception:
        return False


def _kame_judgment(exc):
    """`kame_evidence.judge()` for one exception, or None. Never raises."""
    if _KE is None or exc is None:
        return None
    try:
        return _KE.judge(exc, status=_evidence_status(exc), now=time.time())
    except Exception:
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
    headers = (getattr(exc, "headers", None) or getattr(exc, "response_headers", None)
               or getattr(exc, "litellm_response_headers", None)
               or getattr(getattr(exc, "response", None), "headers", None))
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
    #
    # 2b. v1.8.1.0: every other place a provider states a wait, read the way
    #     the Hermes port reads it — `retry-after-ms`, a `*ratelimit*reset*`
    #     header (an absolute epoch or a duration), Codex's `resets_at` /
    #     `resets_in_seconds` pair, an aggregator's reset header folded into
    #     the body, and a dated moment in the sentence ("regain access on
    #     2026-10-01 at 00:00 UTC").
    if _KE is not None:
        try:
            candidates = _KE.delay_candidates(
                message=str(exc), body=_KE.body_of(exc), headers=_KE.headers_of(exc),
                exc=exc, now=time.time(),
            )
        except Exception:
            candidates = []
        for secs, src in candidates:
            if 0 < secs <= cap:
                tag = ("header" if src.startswith("header") else "body" if src.startswith("body")
                       else "attr" if src.startswith("exception") else "text")
                return (secs, tag) if with_source else secs
    err_msg = _evidence_text(exc) or str(exc)
    match = re.search(
        # v1.8.1.0: a number, its unit, and further number+unit pairs ("6m
        # 11.52s") - never a sentence's full stop followed by the next digits.
        r'(?:retry[_\s-]*(?:after|delay|in)|try\s+again\s+in)[:\s"\']*'
        r'([0-9]+(?:\.[0-9]+)?\s*(?:ms|[hms])?(?:[ \t]*[0-9]+(?:\.[0-9]+)?\s*(?:ms|[hms]))*)',
        err_msg,
        re.IGNORECASE,
    )
    if match:
        dur = _parse_duration_to_seconds(match.group(1))
        if dur is not None and 0 < dur <= cap:
            return (dur, "text") if with_source else dur

    # v1.8.0.2: the owner's measured unsized-throttle rest, not a flat 20.
    rest = _KAME_UNSIZED_THROTTLE_REST_S
    return (rest, "default") if with_source else rest


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


def _is_bare_resource_exhausted(exc) -> bool:
    """v1.8.1.0: exactly Gemini's bare refusal, judged by its payload.

    HTTP 429, a status of RESOURCE_EXHAUSTED, and nothing the provider stated
    about when to come back: no retryDelay, no Retry-After, no quotaId, no
    "retry in N" sentence. Evidence, never the provider's name - any provider
    refusing in exactly this shape is treated the same, and a Gemini refusal
    that carries a number or a quotaId is not.
    """
    try:
        status = _evidence_status(exc)
        text = (_evidence_text(exc) or str(exc)).lower()
    except Exception:
        return False
    if status is not None and status != 429:
        return False
    if status is None and not _STATUS_429_IN_TEXT.search(text):
        return False
    # The STRUCTURED status - the provider's JSON field or the host's own
    # "429 (RESOURCE_EXHAUSTED)" rendering - not the word anywhere in prose,
    # where a relay can quote it about somebody else's refusal.
    #
    # v1.8.1.4: either quote. `_evidence_text` renders a parsed body with
    # repr(), so an SDK that hands the payload over as a dict reads
    # 'status': 'resource_exhausted' -- single quotes, which the JSON-only
    # pattern never matched, and that refusal skipped the 1-2-4s ladder for a
    # flat 30s. The quota-id patterns below already accept both spellings.
    if not re.search(r'["\']status["\']\s*:\s*["\']resource_exhausted["\']|\(resource_exhausted\)|429\s+resource_exhausted', text):
        return False
    # v1.8.1.4: the quota id under all three spellings `_QUOTA_ID_PATTERNS`
    # reads -- a parsed payload files it as `quota_limit`, and a refusal that
    # names `GenerateRequestsPerMinute...` there is a named window, not bare.
    if ("quotaid" in text or "quota_id" in text or "quota_limit" in text or "quotalimit" in text
            or "retrydelay" in text or "retry_delay" in text):
        return False
    try:
        if _extract_retry_delay(exc, with_source=True)[1] != "default":
            return False
    except Exception:
        return False
    return True


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
    # v1.8.0.0 (Hermes parity): a timeout rotates and does not bench. The
    # provider went quiet; the key is fine and is selectable again at once. The
    # attempt itself already cost the wait, so a zero rest cannot spin.
    if isinstance(exc, (asyncio.TimeoutError, asyncio.CancelledError)):
        return 0, "timeout", None
    err_msg = _evidence_text(exc)
    if "timeout" in err_msg or "timed out" in err_msg:
        # v1.8.2.7: the word only where no status contradicts it. A 403 that
        # says "your IP has been put into timeout" is a refusal (row
        # doc-ai-horde-403-timeout-ip-and-abuse-bans), and a 400 naming a
        # "timeout" parameter is the request's fault.
        _st_t = _evidence_status(exc)
        if _st_t is None or _st_t >= 500 or _st_t in _KAME_TIMEOUT_STATUSES:
            return 0, "timeout", None

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
    # v1.8.2.7: only when the exception really carries nothing else. LiteLLM
    # raises `APIConnectionError` (status 500 by default) for provider errors
    # it could not map, with the provider's whole body in the message: a 402
    # "insufficient balance" or a mid-stream throttle frame was read as a
    # dropped socket and rested 3 s on every key (774-row gate, LiteLLM shape).
    _st_c = _evidence_status(exc)
    if type(exc).__name__ in _TRANSPORT_EXCEPTION_CLASSES and (_st_c is None or _st_c == 500) \
            and _kame_judgment(exc) is None and _kame_quirk(exc, "other") is None \
            and not _kame_closed_door(exc):
        # v1.8.0.0: a class that names a timeout has already spent its wait and
        # rotates at once. A connection that never opened fails instantly, so
        # it keeps three seconds: with the network down, a zero rest would turn
        # the pool into a tight loop of refused sockets.
        if "timeout" in type(exc).__name__.lower():
            return 0, "timeout", None
        return 3, "timeout", None

    # v1.6.0.1: the status may be on a wrapper's cause rather than on the
    # wrapper. litellm and the OpenAI SDK both re-raise, and the outer object
    # does not always carry the status the inner one had.
    status_code = _evidence_status(exc)

    # v1.8.2.7 (Hermes decisions/0024): 408 / 499 / 504 / 524 are timeouts. 504
    # used to rest 1 s as a "server" error and 408/499/524 fell into `other` at
    # 20 s - a healthy key benched for a slow provider.
    if status_code in _KAME_TIMEOUT_STATUSES:
        _stated_t = _stated_server_delay(exc)
        if _stated_t:
            # A wait the provider named itself still wins (its number rules).
            return _stated_t, "server", status_code
        return 0, "timeout", status_code

    # v1.8.3.0 (the owner's closed rule, "503 never escalates"; Hermes reads a 5xx
    # before any words): a 503 is ALWAYS a busy server, whatever its body says. A
    # 503 whose body said "payment required" was filed as a spent account and
    # rested a healthy key for an hour. A wait the provider names is still obeyed.
    if status_code == 503:
        _stated_503 = _stated_server_delay(exc)
        return (_stated_503 if _stated_503 else _KAME_SERVER_BASE_S), "server", 503

    # v1.8.1.0: the evidence reader speaks first, and only when a field or a
    # sentence it trusts named the failure. See `kame_evidence.judge`.
    judgment = _kame_judgment(exc)
    if judgment is not None and judgment.family == "throttle" and not judgment.source \
            and status_code == 429 and judgment.seconds is None and "overloaded" in err_msg \
            and "rate_limit" not in err_msg:
        # v1.8.2.7 (Hermes classify parity): a bare 429 whose words say the
        # model or engine is overloaded is congestion, not a spent counter; the
        # server rule below rests the key one second. Only when the reader had
        # nothing but the status to go on, and no structured type says rate_limit
        # (Kimi, tests/test_v1_8_1_4.py). Rows: doc-venice-429-model-overloaded-429,
        # doc-naver-clova-429-overloaded-42902, doc-novita-429-engine-overloaded-relayed.
        judgment = None
    if judgment is not None:
        family = judgment.family
        if family == "upstream":
            # An aggregator relaying somebody else's refusal: our key is fine,
            # so it rests like a busy server and the next key is tried.
            return _KAME_SERVER_BASE_S, "server", (status_code or 502)
        if family == "billing":
            return _KAME_DAILY_COOLDOWN_S, "insufficient_quota", (status_code or 402)
        if family == "denial":
            return _KAME_REFUSAL_REST_S, "denied", (status_code or 403)
        if family == "throttle":
            if judgment.seconds is None:
                # A throttle nothing sizes: the unsized rest, or Gemini's
                # bare-RESOURCE_EXHAUSTED ladder in `_mark_key_health`.
                return _extract_retry_delay(exc), "per_minute", (status_code or 429)
            if judgment.window in ("per_day", "per_week", "per_month"):
                return float(judgment.seconds), "daily", (status_code or 429)
            return float(judgment.seconds), "per_minute", (status_code or 429)
        # `auth_dead` is the loop's credential branch: see `_is_auth_error`.

    # Server / transient errors FIRST (v1.0.2). A 5xx is the provider being
    # momentarily overloaded, NOT the key being spent. Some providers (notably
    # Google) put quota / resource_exhausted / daily text in a 503 body; the
    # v1.0.1 order checked that text BEFORE the status code, so such a 503 fell
    # into the daily-quota branch and cooled a HEALTHY key for an hour — taking
    # the whole pool cold while the same keys stayed healthy on other models. A
    # real daily quota is a 429, never a 5xx, so the status code wins here. The
    # 'server' kind gets the gentle escalating cooldown, not the 1h daily floor.
    # 498 (v1.8.1.0): Groq's "flex tier capacity exceeded" — a busy server in
    # a status no standard set lists, which used to land in `other` at 20s.
    #
    # v1.8.1.4 (Hermes parity, found by a cross-port replay): two rules the
    # Hermes table has and this one did not. "overloaded" is a busy server
    # whatever status carries it -- `kame_evidence.judge` already declines a
    # 429 that says so (unless a structured type says rate_limit, the Kimi
    # case, which it returns as a throttle before this line), and without the
    # word here the 429 fell through to the quota branch and rested a healthy
    # key 30s, escalating. And a 5xx that names its own wait is obeyed, like
    # every other refusal that names one: this branch returned 1s even beside
    # `Retry-After: 30`.
    if status_code in (498, 500, 502, 503, 504, 529) \
            or "service unavailable" in err_msg or "serviceunavailable" in err_msg \
            or "internal server error" in err_msg or "bad gateway" in err_msg \
            or "gateway timeout" in err_msg or "overloaded" in err_msg:
        stated = _stated_server_delay(exc)
        if stated is not None:
            return stated, "server", (status_code or 503)
        return _KAME_SERVER_BASE_S, "server", (status_code or 503)

    # Rate limits / quota (only when it is NOT an explicit server 5xx above)
    if status_code == 429 or _names_a_throttle(err_msg):
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


def _kame_rest_for_failure(identity, key, exc, elapsed=None):
    """v1.8.1.0: classify one failure and rest its key - the one path the loop uses.

    Returns ``(applied, kind, status_code, label, sized_by)``. ``label`` names
    the ladder rung (``backoff.3``) when the RESOURCE_EXHAUSTED ladder sized the
    rest, else "". Tests call this exact function, so what is tested is what runs.
    """
    delay, kind, sc = _classify_error(exc)
    # v1.8.2.7: what a provider keeps in a status or a phrase the tables missed.
    quirk = None if sc == 503 else _kame_quirk(exc, "auth" if (kind == "other" and _is_auth_error(exc)) else kind)
    if quirk in ("billing", "per_minute", "server") and (sc or _evidence_status(exc)) == 429 \
            and _delay_source(exc, "per_minute") == "provider":
        quirk = None  # a number the provider STATED outranks one a text rule would invent
    if quirk == "denied":
        delay, kind = _KAME_REFUSAL_REST_S, "denied"
    elif quirk == "billing":
        delay, kind = _KAME_DAILY_COOLDOWN_S, "insufficient_quota"
    elif quirk == "per_minute":
        delay, kind = _extract_retry_delay(exc), "per_minute"
    elif quirk == "server":
        _stated_q = _stated_server_delay(exc)
        delay, kind = (_stated_q if _stated_q else _KAME_SERVER_BASE_S), "server"
    if quirk in ("denied", "billing", "per_minute", "server") and sc is None:
        sc = _evidence_status(exc)
    if kind == "timeout" and not delay and elapsed is not None and elapsed < _KAME_REAL_TIMEOUT_MIN_S:
        # A "timeout" that took no time did not wait for anything: never a
        # zero rest, or a fast-failing pool turns into a tight loop.
        delay = _KAME_FAST_FAILURE_REST_S
    sized_by = _delay_source(exc, kind)
    bare = kind == "per_minute" and sized_by != "provider" and _is_bare_resource_exhausted(exc)
    judgment = _kame_judgment(exc)
    scope = judgment.scope if judgment is not None and judgment.family in ("throttle", "billing") else ""
    window = ""
    if judgment is not None and judgment.family == "throttle" and judgment.source == "window":
        window = judgment.window
    elif judgment is not None and judgment.family == "throttle" and judgment.window in (
            "per_minute", "tokens_per_minute", "per_hour", "per_day"):
        window = judgment.window
    applied = _mark_key_health(identity, key, False, delay, kind, sized_by=sized_by, bare=bare,
                               scope=scope, window=window)
    label = ""
    try:
        with _KAME_LOCK:
            kd = _KAME_KEY_HEALTH.get(identity, {}).get("keys", {}).get(key) or {}
            label = kd.get("rest_label", "") or ""
    except Exception:
        label = ""
    return applied, kind, sc, label, sized_by


def _kame_decide_failure(identity, key, exc, elapsed=None):
    """v1.8.1.0: the whole of what the carousel does with one non-terminal failure.

    The credential branch (a key the provider refused) and every other kind,
    in one function the loop calls and the tests and the answer-key gate call
    too. Returns ``(applied, kind, status_code, label, sized_by)``.
    """
    auth_like = _is_auth_error(exc)
    if not auth_like and _KQ is not None and _evidence_status(exc) in (401, 403):
        # v1.8.2.7: "Authentication failed", "API key not provided": the words
        # say the KEY is bad, whatever a 403 would otherwise mean. Rows:
        # doc-baseten-403-authentication-failed, doc-novita-403-invalid-api-key.
        try:
            auth_like = bool(_KQ.names_bad_key(str(exc), _quirk_body(exc)))
        except Exception:
            auth_like = False
    kind = ("revoked" if _is_revoked_key(exc) else "auth") if auth_like else ""
    # v1.8.2.7: a 403 that does not say the key is bad is a refused model, plan
    # or policy (RFC 9110), and money in words is billing; neither retires a key.
    quirk = _kame_quirk(exc, kind or "other")
    if (auth_like and quirk not in ("denied", "billing", "per_minute", "server")) or (
            not auth_like and quirk == "auth"):
        kind = kind or "auth"
        applied = _mark_key_health(identity, key, False, _KAME_REFUSAL_REST_S, kind)
        # A credential refusal is always KAME's own number: no provider tells
        # you when a rejected key will start working.
        return applied, kind, getattr(exc, "status_code", None) or _evidence_status(exc), "", "kame"
    result = _kame_rest_for_failure(identity, key, exc, elapsed=elapsed)
    if result[1] in _KAME_QUOTA_KINDS and _KH is not None:
        # 1.8.2.9: a quota or rate-limit refusal on ANY leg of this model (the main request, a
        # backup, a raced model) steps Faster back to Balanced for ten minutes.
        try:
            _KH.HEDGE_TRACKER.note_quota_refusal(identity)
        except Exception:
            pass
    if result[1] == "per_minute" and _KP is not None:
        # v1.8.2.7 (Hermes 1.8.2.3): the one event that teaches this key's real RPM.
        try:
            _KP.PACER.on_minute_429(identity, key)
        except Exception:
            pass
    if _KL is not None and elapsed:
        try:
            _KL.LATENCY.note_slow_failure(identity, key, float(elapsed))
        except Exception:
            pass
    return result


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


def _scrub_for_log(value) -> str:
    """`str(value)` with every credential shape removed, never truncated. v1.8.1.4.

    The journal's scrubber (the same one refusals.jsonl and events use), so a
    console line and a file line agree about what a secret looks like.
    """
    try:
        if _KJ is not None:
            return _KJ.redact(value, limit=0)
    except Exception:
        pass
    return re.sub(r"(?i)\b(?:AIza|sk-|nvapi-|gsk_|xai-|hf_)[A-Za-z0-9_\-]{8,}", "[redacted]", str(value))


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
        # FULL — the whole point is to see everything, untruncated. v1.8.1.4:
        # untruncated, not unredacted. Agent Zero masks the secrets it knows,
        # but it knows a pool field as one comma-joined value; a single key in
        # an error (litellm puts Gemini's `?key=` URL in some) is not that
        # value and would reach the log, the bug report and the screenshot.
        body = _scrub_for_log(exc)
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


def _log_failure(call_type, model_short, key, exc, kind, applied, sc, identity, all_keys, label=""):
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
        f"{(' [' + label + ']') if label else ''}"
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


def _kame_pacing_and_latency(identity):
    """The two Hermes 1.8.2.3 selection terms for one pool (never raise)."""
    mono = time.monotonic()
    ready = lambda k: 0  # noqa: E731
    speed = lambda k: 0.0  # noqa: E731
    if _KP is not None:
        try:
            _KP.PACER.sync_identity_settings(identity)
            ready = lambda k: 0 if _KP.PACER.is_ready(identity, k, now=mono) else 1  # noqa: E731
        except Exception:
            pass
    if _KL is not None:
        try:
            speed = lambda k: _KL.LATENCY.get(identity, k, now=mono)  # noqa: E731
        except Exception:
            pass
    return ready, speed


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
        if _KAME_SPREAD_DISABLED:
            # v1.8.1.0: the first ready key in the order the user wrote them.
            # Health still decides WHICH keys are ready; only the choice among
            # them is given back.
            best_key = healthy[0]
        else:
            # v1.8.2.7 (Hermes 1.8.2.3): a key whose learned per-minute limit
            # is reached waits its turn behind one that is not, and between
            # equally free keys the one that answered faster lately goes first.
            # With nothing learned both terms are equal for every key and the
            # order is exactly 1.8.1.8's.
            _ready, _speed = _kame_pacing_and_latency(identity)
            best_key = min(healthy, key=lambda k: (
                1 if (pool[k].get("consecutive_refusals") or pool[k].get("consecutive_denials")) else 0,
                _ready(k),
                len(pool[k]["request_log"]),
                pool[k]["last_used"],
                _speed(k),
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

    v1.8.1.0: a field the provider filled in settles it first — a key it named
    dead (`API_KEY_INVALID`, `authentication_error`, "Account suspended") is a
    credential problem whatever the status, and an empty balance, a denied
    model, a throttle or an aggregator's relayed failure is not one even on a
    401.
    """
    judgment = _kame_judgment(exc)
    if judgment is not None:
        if judgment.family == "auth_dead":
            return True
        if judgment.family in ("billing", "denial", "throttle", "upstream"):
            return False
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
    judgment = _kame_judgment(exc)
    if judgment is not None:
        return judgment.family == "auth_dead"
    err_msg = _evidence_text(exc)
    if any(ind in err_msg for ind in _INVALID_KEY_INDICATORS):
        return True
    return any(pat.search(err_msg) for pat in _INVALID_KEY_PATTERNS)


def _no_clock_fixes(exc) -> bool:
    """v1.8.1.2: a billing refusal no wait fixes (evidence only, never a name).

    `usage_not_included` (the plan does not include the service) or
    `FAILED_PRECONDITION` (the free tier is not offered where the account is).
    """
    parts = [str(exc)]
    for attribute in ("body", "message", "response"):
        try:
            value = getattr(exc, attribute, None)
        except Exception:
            value = None
        if value is not None:
            try:
                parts.append(json.dumps(value, default=str) if isinstance(value, (dict, list)) else str(value))
            except Exception:
                pass
    text = " ".join(parts).lower()
    return "usage_not_included" in text or "failed_precondition" in text


def _is_terminal_error(exc: Exception) -> bool:
    """Classify errors as terminal (don't retry) or transient (rotate key)."""
    # v1.8.1.0: a verdict about the KEY or its account is never terminal for
    # the run, whatever status it arrived on — Google's FAILED_PRECONDITION
    # "enable billing" and Anthropic's spend limit both come as a 400 — and a
    # field naming the REQUEST as the problem (`context_length_exceeded`,
    # `model_not_found`, `invalid_prompt`) is terminal whatever status it has.
    # v1.8.2.7: the provider quirks speak first - a request fault dressed as
    # something else is raised; a refused model, a billing word, a busy server
    # or a per-minute counter is never terminal.
    if _kame_closed_door(exc):
        # Maintenance, no instance, no provider for the model, a local model
        # not loaded: every key meets the same closed door. Not terminal here:
        # the carousel sends the call to KAME's fallback list, or rests the
        # whole pool for the door (Agent Zero would end the task on a raise).
        return False
    _q = _kame_quirk(exc, "auth" if _is_auth_error(exc) else "other")
    if _q == "request_fault":
        return True
    if "blockreason" in _evidence_text(exc).replace("_", ""):
        return True  # Gemini `promptFeedback.blockReason`: the prompt was refused
    if _q in ("denied", "billing", "per_minute", "server", "auth"):
        return False
    if _evidence_status(exc) == 416:
        return True  # range not satisfiable: the request, never the key
    if _KE is not None:
        try:
            if _KE.catalog_terminal(exc):
                return True
        except Exception:
            pass
    judgment = _kame_judgment(exc)
    if judgment is not None and judgment.family in ("billing", "denial", "auth_dead", "throttle", "upstream"):
        return False
    err_msg = _evidence_text(exc)
    # Rate-limit indicators always mean "try another key", never terminal
    _st = getattr(exc, "status_code", None)
    if _names_a_throttle(err_msg, strict=_st in _REQUEST_FAULT_STATUSES):
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
        # v1.8.1.0: the Hermes port's set. 410 is a retired model ("has
        # reached its end of life") and 413 a request too large — no key on
        # earth answers either, so rotating only walks the pool.
        if status_code in (400, 404, 405, 410, 413, 415, 422, 451, 501):
            return True
    # v1.8.1.4: phrases that describe the PROMPT or the RESPONSE being filtered,
    # nothing broader. "response blocked by safety filter" with no status used
    # to be rotated across every key and rested each one 20s; Hermes hands it
    # back at once. Hermes' wider words ("blocked by", "safety", "content
    # policy") are safe there only because a 403 is read as auth before them;
    # here a bare 403 is not, and "API key blocked by admin" or "key suspended
    # for violating our content policy" would have ended a turn that the next
    # key could answer. Both are tested to keep rotating.
    if any(ind in err_msg for ind in _CONTENT_BLOCK_INDICATORS):
        return True
    return False


#: A moderation refusal of the request itself. Mirrors Hermes'
#: `REQUEST_CONTENT_BLOCK_INDICATORS` (1.8.1.4).
_CONTENT_BLOCK_INDICATORS = ("content_policy", "content filter", "content_filter",
                             "safety filter")


def _get_all_api_keys(model_instance) -> list:
    """Extract ALL comma-separated API keys for this model's provider."""
    try:
        if not model_instance.a0_model_conf:
            return []
        provider = model_instance.a0_model_conf.provider
        if not provider:
            return []
    except Exception:
        return []
    return _kame_provider_keys(provider)


def _kame_provider_keys(provider) -> list:
    """The keys in Agent Zero's key field for one provider (1.8.3.0: the lookup `_get_all_api_keys`
    makes at call time, by provider, so the page can count them before any call)."""
    try:
        from helpers import dotenv as _dotenv
        provider = str(provider or "")
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
        messages=list(ctx.get("attempt_messages") or ctx["messages"]),
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
                 # v1.2.0 - when the wait began (the age of a wait is shown on the page only)
                 "cold_since",
                 # 1.8.2.9 - the call this run belongs to, and whether it has written the
                 # status line (so every way out of the carousel knows to give it back)
                 "ctx", "line_on")

    def __init__(self):
        self.sleep_count = 0
        self.cooldown_overhead_s = 0.0
        self.long_cool_logged = False
        self.last_sleep_log_at = 0.0
        self.last_long_heartbeat_at = 0.0
        self.cold_since = 0.0
        self.ctx = None
        self.line_on = False


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


# --- 1.8.2.9 (E10): the wait lives on the status line --------------------------------------
#
# Agent Zero shows "A0: Calling LLM..." as the heading of the log item it makes for every
# chat call (`before_main_llm_call/_10_log_for_stream.py`, kept in
# `loop_data.params_temporary["log_item_generating"]`). KAME writes what it waits for into
# that heading and gives Agent Zero's own words back when the wait ends; Agent Zero's stream
# handlers overwrite it the moment text arrives. One time (time LEFT) and one count (physical
# keys, from the same view the chip and the page read). No toast, no extra chat row.

def _kame_frame_agent():
    """The Agent whose chat call is running: ``self`` of the nearest ``call_chat_model`` /
    ``call_chat_model_turn`` frame (a subordinate agent is its own ``A1``, ``A2``...), else the
    agent stashed at monologue start. ``None`` outside a chat (a CLI run, a test). Never raises."""
    try:
        frame = sys._getframe(1)
        depth = 0
        while frame is not None and depth < 80:
            if frame.f_code.co_name in _KAME_CHAT_FRAMES:
                agent = frame.f_locals.get("self")
                if agent is not None and hasattr(agent, "context"):
                    return agent
            frame = frame.f_back
            depth += 1
    except Exception:
        pass
    try:
        return _KAME_CURRENT_AGENT.get()
    except Exception:
        return None


def _kame_fmt_left(seconds, now=None) -> str:
    """The ONE formatter of a time LEFT: ``45 s``, ``12 min``, ``1 h 05 min``, and past three
    hours the clock time it ends (``back at 15:40``) - never decimals of hours."""
    try:
        s = max(0.0, float(seconds))
    except (TypeError, ValueError):
        return "a while"
    if s < 59.5:
        return f"{max(1, int(round(s)))} s"
    if s < 3570:
        return f"{int(round(s / 60.0))} min"
    if s < 3 * 3600:
        hours, minutes = divmod(int(round(s / 60.0)), 60)
        return f"{hours} h {minutes:02d} min"
    now = time.time() if now is None else now
    return "back at " + time.strftime("%H:%M", time.localtime(now + s))


def _kame_in_words(seconds, tilde=False, now=None) -> str:
    """``in 12 s`` / ``in ~45 min`` / ``back at 15:40``: a time left, ready to follow a verb."""
    text = _kame_fmt_left(seconds, now)
    if text.startswith("back at"):
        return text
    return f"in {'~' if tilde else ''}{text}"


def _kame_totals_phrase(totals) -> str:
    """``1 of 3 keys ready``: the one count every surface says, over physical keys."""
    totals = totals or {}
    return f"{int(totals.get('ready') or 0)} of {int(totals.get('keys') or 0)} keys ready"


def _kame_wait_line(totals, eta) -> str:
    text = f"waiting on a key to come back - {_kame_totals_phrase(totals)}"
    if eta is not None:
        text += ", next key " + _kame_in_words(eta, tilde=True)
    return text


def _kame_flight_wait(ctx, since, eta) -> None:
    """What the chip and the page read about a wait: since when, and when it may end."""
    try:
        fl = ctx.get("flight") if isinstance(ctx, dict) else None
        if isinstance(fl, dict):
            fl["waiting"] = True
            fl["waiting_since"] = since
            fl["eta_until"] = (time.time() + float(eta)) if eta is not None else None
    except Exception:
        pass


def _kame_write_heading(text) -> None:
    """Write ``text`` (or Agent Zero's own words, when ``None``) into the heading of the chat's
    "Calling LLM..." item; at most one update per `_KAME_STATUS_LINE_EVERY_S` and only when the
    words changed. Without that item (an older host, a task runner) the log's own progress line
    is used. Never raises."""
    try:
        agent = _kame_frame_agent()
        if agent is None:
            return
        name = getattr(agent, "agent_name", None) or f"A{int(getattr(agent, 'number', 0) or 0)}"
        now = time.time()
        item = None
        try:
            item = agent.loop_data.params_temporary.get("log_item_generating")
        except Exception:
            item = None
        holder = item
        if holder is None:
            holder = getattr(getattr(agent, "context", None), "log", None)
            if holder is None or not callable(getattr(holder, "set_progress", None)):
                return
        state = getattr(holder, "_kame_line", None)
        written = None
        if text is None:
            if not state or state[0] is None:
                return              # nothing of ours is on the line: leave Agent Zero's alone
            if item is not None:
                # Agent Zero's stream handlers may have taken the heading over (text started): theirs stays.
                if getattr(item, "heading", state[2]) == state[2]:
                    item.update(heading=f"{name}: Calling LLM...")
            else:
                holder.set_progress("", active=False)
        else:
            if state and state[0] == text:
                return
            if state and state[0] is not None and now - state[1] < _KAME_STATUS_LINE_EVERY_S:
                return
            written = f"{name}: Calling LLM... {text}"
            if len(written) > 120:      # Agent Zero's heading limit: never let it cut our words
                written = written[:117] + "..."
            if item is not None:
                item.update(heading=written)
            else:
                holder.set_progress(written)
        try:
            holder._kame_line = [text, now, written]
        except Exception:
            pass
    except Exception:
        pass


def _kame_status_line(ctx, text, st=None) -> None:
    """Say on the status line what a conversation waits for; ``None`` gives the line back.
    The words are kept on the call (the chip and the page read them) and, when the setting is on,
    written into Agent Zero's "Calling LLM..." heading. Never raises."""
    try:
        if isinstance(ctx, dict):
            ctx["status_text"] = text
            fl = ctx.get("flight")
            if isinstance(fl, dict):
                fl["status"] = text
                if text is None:
                    fl["waiting"] = False
                    fl["waiting_since"] = None
                    fl["eta_until"] = None
        if st is not None and text is not None:
            st.line_on = True
    except Exception:
        pass
    if _KAME_WAIT_NOTICE:
        _kame_write_heading(text)


def _kame_wait_notice_tick(st, identity, all_keys, call_type, model_short) -> None:
    """Every key rests: say what the conversation waits for, and for how long, on its status line.

    Called from inside the sleep slices, so the time left counts down. Only the conversation
    speaks here - a background job (a chat title, a memory) that waits does not rewrite the line of
    the chat it is not part of. Counts come from the same view as the chip. Best-effort by
    construction: a line that cannot be written never touches rotation.
    """
    if not st.cold_since:
        st.cold_since = time.time()
    ctx = st.ctx
    if ctx is not None and not _kame_main(ctx):
        return
    try:
        eta = _next_recovery_seconds(identity, all_keys)
        view = _kame_key_view(known=False)
        totals = view["totals"]
        # review M2: the keys of the model being waited on, not every key KAME holds
        pool = next((p for p in view["pools"] if _kame_names_match(p["identity"], identity)), None)
        if pool is not None and pool.get("keys"):
            totals = {"ready": int(pool.get("healthy") or 0), "keys": int(pool["keys"])}
        _kame_flight_wait(ctx, st.cold_since, eta)
        _kame_status_line(ctx, _kame_wait_line(totals, eta), st)
    except Exception:
        pass


def _kame_wait_notice_finish(st, outcome: str) -> None:
    """Give the status line back: called on every way out of the carousel, so the chat never keeps
    a "waiting" heading that is no longer true. A no-op when KAME never wrote one (the common
    case: the pool recovered before anybody could wonder). ``outcome`` is 'resumed' or 'stopped'."""
    _kame_status_release(st)


def _kame_status_release(st) -> None:
    """The status line goes back to Agent Zero if this run ever wrote it."""
    if not st.line_on:
        return
    st.line_on = False
    _kame_status_line(st.ctx, None)


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

    # 1.8.2.9: the status line says so from the first second, not after the first slice
    _kame_wait_notice_tick(st, identity, all_keys, call_type, model_short)
    _slept = 0.0
    while _slept < wait:
        _slice = min(1.0, wait - _slept)
        await asyncio.sleep(_slice)
        _slept += _slice
        await _kame_honor_intervention()
        # A reset or a successful concurrent call may release a key before the
        # deadline observed when this sleep began. Re-check every slice so a
        # stale 60-second sleep never survives the fact that the pool recovered.
        # 1.8.1.2: early recovery only, as Hermes does (`slept < eta`). Waking
        # on ordinary expiry here dropped the +0.5s padding and 0.1-1.5s
        # jitter this wait was sized with, so concurrent sleepers re-probed a
        # just-expired key together.
        if (_soonest_eta is not None and _slept < _soonest_eta
                and _pool_has_ready_key(identity, all_keys)):
            break
        # v1.2.0: refresh the chat-side notice from inside the slice loop rather
        # than once per sleep. A sleep can be a full minute long; a countdown
        # that only moves every minute reads as frozen, which is the exact
        # impression the notice exists to remove.
        _kame_wait_notice_tick(st, identity, all_keys, call_type, model_short)
    st.cooldown_overhead_s += (time.perf_counter() - _sleep_started)


def _kame_timing(identity, key, attempt, outcome, kind, status, started_wall, ctx,
                 call_started_perf, st, rest, rest_source, call_id) -> None:
    """One row of calls.jsonl for the attempt that just ended. Never raises."""
    if _KJ is None:
        return
    try:
        progress = ctx.get("progress") or {}
        main = _kame_main(ctx)
        extra = {} if main else {"channel": "secondary", "task": ctx.get("aux_task") or "utility"}
        _KJ.record_call(
            identity=identity, key=key, attempt=attempt, outcome=outcome, kind=kind,
            status=status, started_at=started_wall, ended_at=time.time(),
            first_sign_at=progress.get("first_sign_at"),
            first_text_at=progress.get("first_text_at"),
            # Time the call had already spent before this attempt began.
            elapsed_before_s=max(0.0, (time.perf_counter() - call_started_perf)
                                 - (time.time() - started_wall)),
            pool_waited_before_s=getattr(st, "cooldown_overhead_s", None),
            rest_s=rest, rest_source=rest_source, call_id=call_id, **extra,
        )
    except TypeError:   # an older kame_journal.py beside this engine
        try:
            _KJ.record_call(
                identity=identity, key=key, attempt=attempt, outcome=outcome, kind=kind,
                status=status, started_at=started_wall, ended_at=time.time(),
                first_sign_at=(ctx.get("progress") or {}).get("first_sign_at"),
                first_text_at=(ctx.get("progress") or {}).get("first_text_at"),
                rest_s=rest, rest_source=rest_source, call_id=call_id)
        except Exception:
            pass
    except Exception:
        pass


# --- v1.8.2.7: backups, keys asked together, and the model fallback list ------
#
# Three things Hermes 1.8.2.3-1.8.2.7 does that Agent Zero 1.8.1.8 never did,
# built on Agent Zero's own model call (the attempt stays delegated):
#
# * **Backups for a slow first word.** A chat reply still silent after twice
#   its model's usual first-word time gets a backup key, then a third. The
#   first leg that shows any sign of work (text, a tool call, visible
#   reasoning) wins; the other legs are cancelled, which closes their HTTP
#   request. Nothing changes until a backup is needed: a lone call passes
#   straight through. Budgeted (~10% extra requests), never under 429
#   pressure, never for a prompt too large to send twice. A background call
#   that does not stream may get one backup when slower than its usual time.
# * **Keys asked together when a pool runs dry.** After two long refusals in a
#   row (a rest of a minute or more), the next attempts of a chat call ask 4,
#   then 8, then 16 ready keys at once instead of one by one. First answer
#   wins. Off with `hedge_mode: off`. Background calls never do this.
# * **The fallback list.** When the chat model cannot answer - every key
#   resting longer than `failover_after_seconds`, the provider's door closed,
#   two timeouts in a row, every key refused the model, or no wait can fix it
#   - and nothing has been shown yet, KAME answers with the first model of
#   its own list that has a usable key, through Agent Zero's own model class.
#   The next call starts on the configured model again.
#
# Agent Zero ends the task on any error a model call raises
# (`_90_handle_critical_exception`), so on this host "hand it back" is never
# the gentle option it is on Hermes: without a list KAME keeps waiting, as
# 1.8.1.8 did, except where waiting is known to fix nothing.

_KAME_CLOSED_DOOR_REST_S = 30.0      # a closed door rests every key of the pool this long
_KAME_HOP_BOUND_MIN_S = 30.0         # a call on a fallback model gives up after at least this long
_KAME_BURST_LONG_REST_S = 60.0      # a rest at least this long counts toward a burst
_KAME_BURST_SIZES = (4, 8, 16)
_KAME_FALLBACK_RETRY_S = 30.0        # a list entry that could not answer is skipped this long
_KAME_FALLBACK_SKIP = {}             # identity -> monotonic time until which it is skipped
_KAME_FALLBACK_MODEL_CACHE = {}      # (provider, name, api_base) -> model instance
_KAME_LAST_MODEL_SWITCH = {}         # what the chip shows: from, to, reason, at
_KAME_SPEED_STATS = {"backups": 0, "backup_wins": 0, "probe_bursts": 0, "model_switches": 0}
_KAME_PRESSURE_WINDOW_S = 60.0
_KAME_NON_STREAM_MAX_LEGS = 2
_KAME_UNLIMITED_PROMPT_TOKENS = 0


class _KameSwitchModel(Exception):
    """Raised inside a call that is running on a fallback model, to tell the outer
    call this model cannot answer either. Never reaches Agent Zero."""

    def __init__(self, reason, cause=None):
        super().__init__(reason)
        self.reason = reason
        self.cause = cause


class _KameSlowModel(Exception):
    """v1.8.2.8 (Hermes decisions/0028 D11): no leg of this attempt showed any sign
    of work by the model's slow bound - the model is slow right now, not the key.
    Caught by the carousel, which answers with the next model of the list."""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


#: v1.8.2.8 (D11): "Automatic" slow-model watch, (floor, ceiling, cold) seconds,
#: per class (plain, thinking) - Hermes' `_SLOW_S`.
_KAME_SLOW_S = {False: (20.0, 45.0, 30.0), True: (25.0, 60.0, 45.0)}


def _kame_names_match(a, b) -> bool:
    """v1.8.2.8 (review R5): the same model whether the list wrote it
    ("google:gemini-3.7-flash") or LiteLLM named it ("google:gemini/gemini-3.7-flash")."""
    a, b = str(a or "").lower(), str(b or "").lower()
    if a == b:
        return True
    if ":" not in a or ":" not in b:
        return False
    (pa, ma), (pb, mb) = a.split(":", 1), b.split(":", 1)
    return pa == pb and ma.split("/")[-1] == mb.split("/")[-1]


def _kame_clear_busy(identity) -> None:
    """An answer ends the busy window of this model under either spelling."""
    with _KAME_LOCK:
        for ident in [i for i in _KAME_BUSY_UNTIL if _kame_names_match(i, identity)]:
            _KAME_BUSY_UNTIL.pop(ident, None)
            _KAME_BUSY_WINDOW.pop(ident, None)
            _KAME_BUSY_KIND.pop(ident, None)
        for ident in [i for i in _KAME_PROBED_AT if _kame_names_match(i, identity)]:
            _KAME_PROBED_AT.pop(ident, None)


# --- v1.8.2.9 (E1): a busy model is asked on a clock, not once per key ------------------
#
# Until 1.8.2.8 a model KAME knew busy was either skipped for its whole window or hammered
# once per key round trip. Now every busy model - the best one or a list model - is asked at
# most once per `_KAME_PROBE_EVERY_S` across every conversation, and the wait between two
# asks is said on the status line.

_KAME_PROBE_EVERY_S = 20.0
_KAME_PROBED_AT = {}                 # identity -> wall time of the last ask while it was known busy
_KAME_STARTED_AT = time.time()       # E8: the tally starts here when that is less than a day ago


def _kame_busy_state(identity, now):
    """``(busy, probed_at)`` for a model under either spelling. Reads only; the caller
    holds ``_KAME_LOCK`` when it needs the answer to stay true."""
    busy = False
    for ident, until in list(_KAME_BUSY_UNTIL.items()):
        if now < until and _kame_names_match(ident, identity):
            busy = True
            break
    if not busy:
        return False, None
    stamp = None
    for ident, at in list(_KAME_PROBED_AT.items()):
        if _kame_names_match(ident, identity):
            stamp = at if stamp is None else max(stamp, at)
    return True, stamp


def _kame_stamp_is_due(stamp, now) -> bool:
    return stamp is None or now < stamp or now - stamp >= _KAME_PROBE_EVERY_S


def _kame_probe_due(identity, now=None) -> bool:
    """True when the model is not known busy, or it was last asked 20 s ago or more."""
    now = time.time() if now is None else now
    busy, stamp = _kame_busy_state(identity, now)
    return (not busy) or _kame_stamp_is_due(stamp, now)


def _kame_claim_probe(identity, now=None) -> bool:
    """Atomic: the ask of a busy model goes to exactly one conversation per 20 s. A model
    that is not busy needs no claim (True, nothing stamped)."""
    now = time.time() if now is None else now
    with _KAME_LOCK:
        busy, stamp = _kame_busy_state(identity, now)
        if not busy:
            return True
        if not _kame_stamp_is_due(stamp, now):
            return False
        _KAME_PROBED_AT[identity] = now
        return True


def _kame_pool_of(identity):
    """``(pool identity, its keys)`` as KAME already knows them, under either spelling."""
    with _KAME_LOCK:
        state = _KAME_KEY_HEALTH.get(identity)
        if state is not None:
            return identity, list(state.get("keys") or {})
        for ident, state in _KAME_KEY_HEALTH.items():
            if _kame_names_match(ident, identity):
                return ident, list(state.get("keys") or {})
    return identity, []


def _kame_entry_free(identity, keys=None, now=None) -> bool:
    """A list model that can be asked now: usable keys, and not known busy unless it is due
    another look (Hermes decisions/0032 B4)."""
    now = time.time() if now is None else now
    pool_id, known = _kame_pool_of(identity)
    use = known if keys is None else keys
    return bool(_kame_entry_usable(pool_id if keys is None else identity, use)
                and _kame_probe_due(identity, now))


def _kame_pool_wait(identity, keys) -> float:
    """Seconds until a key of this pool is ready (0 when one is, or nothing is known)."""
    if not keys or _pool_has_ready_key(identity, keys):
        return 0.0
    eta = _next_recovery_seconds(identity, keys)
    return float(eta) if eta else 0.0


def _kame_skip_left(identity, now_m=None) -> float:
    now_m = time.monotonic() if now_m is None else now_m
    left = 0.0
    for ident, until in list(_KAME_FALLBACK_SKIP.items()):
        if _kame_names_match(ident, identity):
            left = max(left, until - now_m)
    return left


def _kame_circle_due_in(ctx, now=None, primary_only=False):
    """``(seconds, label)``: how long until the first model of ``[best] + the list`` can be
    asked again, and which one it is. ``(0.0, label)`` when one can be asked now.
    ``primary_only``: the list may not be asked beside this reply (G1); only the best model counts."""
    now = time.time() if now is None else now
    now_m = time.monotonic()
    primary = ctx["identity"]
    rows = [(primary, _kame_model_label(primary), list(ctx.get("all_keys") or []), True)]
    for entry in ([] if primary_only else list(_KAME_FALLBACK_MODELS)):
        if not _kame_names_match(entry["identity"], primary):
            rows.append((entry["identity"], entry["label"], None, False))
    best = None
    for ident, label, keys, is_primary in rows:
        wait = 0.0
        busy, stamp = _kame_busy_state(ident, now)
        if busy and stamp is not None and not _kame_stamp_is_due(stamp, now):
            wait = stamp + _KAME_PROBE_EVERY_S - now
        pool_id, known = (ident, keys) if is_primary else _kame_pool_of(ident)
        pool = _kame_pool_wait(pool_id, known if keys is None else keys)
        if pool > 0 and not is_primary:
            # a list model whose keys return inside the hop bound is simply asked: its own call waits
            pool = max(0.0, pool - max(_KAME_HOP_BOUND_MIN_S, _kame_switch_threshold(pool_id)))
        wait = max(wait, pool)
        if not is_primary:
            wait = max(wait, _kame_skip_left(ident, now_m))
        if best is None or wait < best[0]:
            best = (wait, label)
    return (float(best[0]), best[1]) if best else (0.0, "")


def _kame_entry_busy(entry, now=None) -> bool:
    """Whether KAME knows this list entry busy or slow right now. The running model
    is named by LiteLLM ("google:gemini/gemini-3.7-flash"), the list by the user
    ("google/gemini-3.7-flash"): the provider and the model's last name decide."""
    now = time.time() if now is None else now
    provider, name = entry["provider"].lower(), entry["name"].lower()
    for ident, until in list(_KAME_BUSY_UNTIL.items()):
        if until <= now or ":" not in ident:
            continue
        p, m = ident.split(":", 1)
        if p == provider and (m == name or m.endswith("/" + name.split("/")[-1])):
            return True
    return False


def _kame_slow_number(ctx, identity, reasoning) -> float:
    """The slow bound of ONE model for this prompt: "Set time" = the user's number,
    "Automatic" = 3 x the median of that model's own first-word times, clamped (not
    the p95: a model slow today fills its tail with it). 1.8.2.9 split it out so the
    bound of each raced model can be read (E3)."""
    if _KAME_MODEL_SWITCH == "timed":
        return float(_KAME_FAILOVER_AFTER_S) if _KAME_FAILOVER_AFTER_S > 0 else 0.0
    floor, ceiling, cold = _KAME_SLOW_S[bool(reasoning)]
    extra = _kame_est_prompt_tokens(ctx.get("messages")) / _KAME_SLOW_TOKENS_PER_S  # review R9
    stats = _kame_first_word_stats(identity, reasoning)
    if not stats:
        return cold + extra
    return float(min(ceiling, max(floor, 3.0 * stats[0])) + extra)


def _kame_slow_after(ctx, identity, reasoning) -> float:
    """Seconds without a first sign of work after which the reply falls back to the
    next model of the list; 0 = wait as before. Only the conversation's own reply,
    only when it could fall back (a list entry that can be asked: usable keys, and not
    known busy unless it is due another look)."""
    try:
        if ctx.get("call_type") != "Chat" or not _kame_main(ctx) or not _kame_nothing_shown(ctx):
            return 0.0
        if not _KAME_FALLBACK_MODELS or not _kame_switch_on() or _KAME_CAROUSEL_DISABLED:
            return 0.0
        if not _kame_reply_on_main(ctx):    # 1.8.3.0 (D1)
            return 0.0
        now_t, now_m = time.time(), time.monotonic()
        hop = _KAME_FALLBACK_HOP.get()
        usable = [e for e in _KAME_FALLBACK_MODELS
                  if not _kame_names_match(e["identity"], identity)
                  and now_m >= _KAME_FALLBACK_SKIP.get(e["identity"], 0.0)
                  and _kame_entry_free(e["identity"], None, now_t)
                  and not (hop and e["identity"] == hop.get("origin"))]
        if not usable:
            return 0.0
        return _kame_slow_number(ctx, identity, reasoning)
    except Exception:
        return 0.0


#: 1.8.2.9 (E3, Hermes decisions/0032 B): a raced model keeps the reply waiting only to
#: its own late point, and ONE more model of the list joins at the slow bound.
_KAME_WIDEN_MAX = 1
_KAME_RACE_MAX_CROSS = 3     # cross legs per reply: the usual 2 plus the one that joins at the bound


def _kame_late_point(slow_s) -> float:
    """How long a raced model may stay silent before the reply stops waiting for it:
    half its own slow bound, at least `_KAME_CROSS_MIN_S`."""
    return max(_KAME_CROSS_MIN_S, 0.5 * float(slow_s or 0.0))


def _kame_extended_bound(ctx, identity, reasoning, race, live, slow, slow_at) -> float:
    """The primary's slow bound once raced models are in: the latest of their OWN late points
    (and the bound it already had), never later than 1.8.2.8 waited - the last raced start plus
    the whole bound."""
    r3 = max(race.started[l] for l in live) + slow
    late = slow_at
    for l in live:
        entry = race.cross_legs[l]
        s_x = _kame_slow_number(ctx, entry["identity"], reasoning) or slow
        late = max(late, race.started[l] + _kame_late_point(s_x))
    return min(r3, late)


def _kame_cross_allowed(ctx) -> bool:
    """1.8.2.9 (G1): on Agent Zero's Responses transport with native function tools a
    Gemini function-call turn needs its own signature, so no other model may be asked
    beside the reply. The sequential switch to the next model is unchanged."""
    kwargs = ctx.get("kwargs") or {}
    if not _kame_reply_on_main(ctx):    # 1.8.3.0 (D1): the list belongs to the main model
        return False
    if kwargs.get("responses_input_items") or kwargs.get("previous_response_id"):
        return False
    if kwargs.get("a0_responses_function_tools"):
        return False
    return str(kwargs.get("a0_api_mode") or "").lower() != "responses"


#: v1.8.2.8 (D12): when the next model of the list joins a silent reply.
_KAME_CROSS_MIN_S = 8.0
_KAME_CROSS_COLD_S = 12.0
_KAME_CROSS_STEP_S = 6.0
_KAME_SLOW_TOKENS_PER_S = 4000.0   # one more second of slow bound per this many prompt tokens
_KAME_CROSS_MAX_LEGS = 2


def _kame_cross_entry(ctx, identity, reasoning, slow):
    """``[(entry, at_s), ...]``: the next models of the list to race a silent reply, or None.
    The first joins at ``after_s``; if it is silent too, the one after it joins half
    that later (6 s at least, before the slow bound) - live on Hermes, gemini-3.7 was
    as slow as 3.8 while 3.6 answered in 2 s.
    Speed before the first model's priority (owner, 2026-10-18): racing the next
    model once the first is clearly late costs one extra request and no wait.
    Only at the top of a reply (not inside a fallback hop), not in Saver mode,
    an entry neither skipped nor known busy; 3 x this model's usual first word,
    after a backup key of the SAME model had its chance (2 x),
    8 s at least, 12 s before anything is learned, well before the slow bound."""
    try:
        if not slow or _KAME_FALLBACK_HOP.get() is not None or _kame_hedge_mode() == "off":
            return None
        if not _kame_cross_allowed(ctx):   # 1.8.2.9 (G1): no other model beside a Responses-tools reply
            return None
        now_m = time.monotonic()
        stats = _kame_first_word_stats(identity, reasoning)
        after = float(min(_KAME_CROSS_COLD_S if not stats else max(_KAME_CROSS_MIN_S, 3.0 * stats[0]),
                          0.5 * slow))
        step = max(_KAME_CROSS_STEP_S, 0.5 * after)
        out = []
        for entry in _KAME_FALLBACK_MODELS:
            if _kame_names_match(entry["identity"], identity) \
                    or now_m < _KAME_FALLBACK_SKIP.get(entry["identity"], 0.0):
                continue
            if not _kame_entry_free(entry["identity"]):   # 1.8.2.9 (B4): busy only if not due
                continue
            at = after + step * len(out)
            if out and at >= 0.85 * slow:
                break
            out.append((entry, float(at)))
            if len(out) == _KAME_CROSS_MAX_LEGS:
                break
        return out or None
    except Exception:
        return None


def _kame_est_prompt_tokens(messages) -> int:
    try:
        return int(sum(len(str(getattr(m, "content", m) or "")) for m in (messages or [])) / 4)
    except Exception:
        return 0


def _kame_under_pressure(identity, all_keys) -> bool:
    """A per-minute refusal in this pool in the last minute: backups would deepen it."""
    now = time.time()
    with _KAME_LOCK:
        pool = _KAME_KEY_HEALTH.get(identity, {}).get("keys", {})
        for k in all_keys:
            kd = pool.get(k) or {}
            if kd.get("last_hold_kind") == "per_minute" and \
                    now - float(kd.get("last_sick_at") or 0) < _KAME_PRESSURE_WINDOW_S:
                return True
    return False


def _kame_pick_more(identity, all_keys, used, n):
    """Up to ``n`` more ready keys, best first, none of them in ``used``."""
    picked = []
    with _KAME_LOCK:
        pool = _KAME_KEY_HEALTH.get(identity, {}).get("keys", {})
        # A key out of rotation is never a backup. `_get_best_key` offers a
        # retired key when EVERY key it is given is retired (its escape hatch
        # for the call itself); asked about a subset, that hatch would fire on
        # the subset (review 2026-10-17, M2).
        live = [k for k in all_keys if not (pool.get(k) or {}).get("retired_at")]
    for _ in range(max(0, int(n))):
        rest = [k for k in live if k not in used and k not in picked]
        if not rest:
            break
        key, status = _get_best_key(identity, rest)
        if status == "EXHAUSTED_RETRY" or key is None or key not in rest:
            break
        picked.append(key)
    return picked


def _kame_burst_allowed(identity, all_keys, ctx) -> bool:
    """The guards a backup has, for keys asked together: no 429 pressure in the
    pool, no prompt too large to send several times, no backup cooldown."""
    if _KH is None or _kame_under_pressure(identity, all_keys):
        return False
    try:
        limit = float(_KH.SETTINGS.get("hedge_max_prompt_tokens", 32000))
        if limit and _kame_est_prompt_tokens(ctx.get("messages")) > limit:
            return False
        cool = getattr(_KH.HEDGE_TRACKER, "_cool_until", {}).get(identity, 0.0)
        return time.monotonic() >= cool
    except Exception:
        return False


def _kame_reasoning_call(ctx) -> bool:
    if _KH is None:
        return False
    try:
        model = str(ctx["identity"]).split(":", 1)[-1]
        return bool(_KH.reasoning_model(model, ctx.get("kwargs")))
    except Exception:
        return False


class _KameRace:
    """One raced attempt: legs on different keys, the first to show work wins."""

    def __init__(self, ctx):
        self.ctx = ctx
        self.winner = None          # leg id that claimed the stream
        self.tasks = {}             # leg id -> task
        self.keys = {}              # leg id -> key
        self.started = {}           # leg id -> monotonic start
        self.progress = {}          # leg id -> that leg's progress dict
        self.claimed = asyncio.Event()
        self.claim_at = None        # monotonic time the winner showed its first sign
        self.burst = False          # legs asked together, not backups
        self.last_failure = None    # the last real failure judged inside the race
        self.cross_legs = {}        # v1.8.2.8 (D12): leg id -> the list entry it runs

    def _claim(self, leg):
        if self.winner is None:
            self.winner = leg
            self.claim_at = time.monotonic()
            self.claimed.set()
            for other, task in self.tasks.items():
                if other != leg and not task.done():
                    task.cancel()
        return self.winner == leg

    def shims(self, leg, progress):
        """Callbacks for one leg: the first chunk claims the race; only the
        winner's chunks reach Agent Zero (through the call's own shims)."""
        outer = self.ctx

        def make(name, marks_answer):
            target = outer.get(name)
            if target is None:
                return None

            async def shim(a, b):
                now = time.time()
                if not progress.get("first_sign_at"):
                    progress["first_sign_at"] = now
                if marks_answer:
                    progress["any"] = True
                    if not progress.get("first_text_at"):
                        progress["first_text_at"] = now
                else:
                    progress["reasoning"] = True
                if not self._claim(leg):
                    raise asyncio.CancelledError()  # a loser: its task is being cancelled
                return await target(a, b)
            return shim

        return (make("response_callback", True), make("reasoning_callback", False),
                make("tokens_callback", True))

    def start(self, owner, leg, key):
        _kame_count_request(self.ctx, self.ctx.get("identity"))   # 1.8.2.9 (E8)
        progress = {"any": False, "reasoning": False, "first_sign_at": None, "first_text_at": None}
        leg_ctx = dict(self.ctx)
        leg_ctx["progress"] = progress
        (leg_ctx["response_callback"], leg_ctx["reasoning_callback"],
         leg_ctx["tokens_callback"]) = self.shims(leg, progress)
        self.keys[leg] = key
        self.progress[leg] = progress
        self.started[leg] = time.monotonic()
        self.tasks[leg] = asyncio.ensure_future(self.ctx["attempt"](owner, key, leg_ctx))
        return self.tasks[leg]

    def start_cross(self, owner, leg, entry, probe=False):
        """v1.8.2.8 (Hermes D12): a leg that asks the NEXT MODEL of the list - a whole
        KAME call on that model, inside a fallback hop, its words shown only if it
        shows work first. Its failure is its own: never judged on this pool's keys.
        ``probe`` (1.8.2.9): the model was known busy and this ask is its probe."""
        ctx = self.ctx
        _kame_ledger("race")   # 1.8.2.9: the next model beside the reply is an extra request
        progress = {"any": False, "reasoning": False, "first_sign_at": None, "first_text_at": None}
        rc, rsc, tc = self.shims(leg, progress)

        async def run():
            token = _KAME_FALLBACK_HOP.set({"origin": ctx["identity"], "to": entry["identity"],
                                            "probe": bool(probe)})
            main_token = _KAME_RACED_MAIN.set(True)
            try:
                model = _kame_fallback_model(owner, entry)
                method = getattr(model, ctx["entry_name"])
                kwargs = dict(ctx.get("kwargs") or {})
                kwargs.pop("previous_response_id", None)
                kwargs.pop("responses_input_items", None)
                return await method(system_message="", user_message="", messages=list(ctx["messages"]),
                                    response_callback=rc, reasoning_callback=rsc, tokens_callback=tc,
                                    rate_limiter_callback=ctx.get("rate_limiter_callback"), **kwargs)
            finally:
                _KAME_RACED_MAIN.reset(main_token)
                _KAME_FALLBACK_HOP.reset(token)

        self.keys[leg] = self.keys.get(0)
        self.progress[leg] = progress
        self.started[leg] = time.monotonic()
        self.cross_legs[leg] = entry
        self.tasks[leg] = asyncio.ensure_future(run())
        return self.tasks[leg]

    def running(self):
        return [leg for leg, t in self.tasks.items() if not t.done()]

    async def cancel_all(self, except_leg=None):
        pending = [t for leg, t in self.tasks.items() if leg != except_leg and not t.done()]
        for t in pending:
            t.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)


def _kame_judge_leg(identity, key, exc, elapsed, ctx, attempt_no, call_id, started_wall,
                    call_started_at, st):
    """A backup leg that failed while another leg was still running: rest its key
    with the same decision the carousel uses, put it on the timeline, keep going."""
    try:
        applied, kind, sc, rung, sized_by = _kame_decide_failure(identity, key, exc, elapsed=elapsed)
        _tally_failure(identity, kind, sc, sized_by)
        # Second review N1/N4: the carousel's "every key refused" rules must see
        # the keys refused inside a race too, and an exception judged here is
        # never judged a second time if it is the one the carousel receives.
        try:
            exc._kame_judged = (key, applied, kind, sc, rung, sized_by)
        except Exception:
            pass
        ctx.setdefault("race_judged", []).append((key, kind, sc, exc))
        if ctx.get("probe_primary") and not ctx.get("probe_noted") and _kame_busy_failure(identity, key, kind, sc):
            # 1.8.2.9 (E2): the start probe was answered "busy": that IS proof, the window doubles.
            ctx["probe_noted"] = True
            _kame_note_busy(identity)
        if kind in ("auth", "revoked"):
            PrintStyle.warning(
                f"[KAME] {ctx.get('call_type', '')}|{ctx.get('model_short', '')} {_key_display_auth(key)} "
                f"{_friendly_error_msg(kind, applied, sc, exc)}{_retirement_suffix(identity, key)}")
        _kame_event(_kame_event_kind(kind), identity, key,
                    reason=_KAME_EVENT_REASON.get(kind, kind) + " (backup)", code=sc,
                    seconds=applied, detail=str(exc), sized_by=rung or sized_by)
        _kame_timing(identity, key, attempt_no, "refused", kind, sc, started_wall, ctx,
                     call_started_at, st, applied, rung or sized_by, call_id)
        if _KJ is not None:
            _KJ.record_refusal(identity=identity, key=key, exc=exc, status=sc, kind=kind,
                               rest=applied, sized_by=rung or sized_by)
        if kind == "per_minute" and _KH is not None:
            _KH.HEDGE_TRACKER.note_hedge_429(identity)
    except Exception:
        pass



# --- v1.8.2.7: Hermes parity helpers (retry hints, first token, continuation) ---

#: Hermes 1.8.2.3 `_with_retry_hint`, ported with Agent Zero's kind words. An
#: error KAME hands back reaches the agent itself (Agent Zero puts the error
#: text in front of the model when it retries the turn, and a tool that called
#: a model returns it as the tool's result), so the text says which of three
#: things it is: passes on its own (retry), a spent quota (do not hammer it),
#: or something only a person fixes. Unknown kinds get no hint: a confident
#: wrong hint is worse than silence.
_KAME_TRANSIENT_KINDS = frozenset({"timeout", "server", "per_minute", "rate_limit", "empty"})
_KAME_QUOTA_WALL_KINDS = frozenset({"daily", "insufficient_quota", "quota"})
_KAME_PERMANENT_KINDS = frozenset({"auth", "revoked", "denied", "billing", "permission"})
_KAME_RETRY_HINT = (
    "\n\n\u26a0\ufe0f This appears to be a temporary API issue.  "
    "Retrying the same tool call will very likely succeed."
)
_KAME_QUOTA_HINT = (
    "\n\n\u26d4 This is a spent daily/monthly quota, not a temporary failure.  "
    "Do NOT keep retrying this model now \u2014 switch model or wait for the quota "
    "to roll over."
)
_KAME_PERMANENT_HINT = (
    "\n\n\u26d4 This failure will repeat until its cause is fixed (a bad or "
    "unauthorised key, or a model this key may not use).  Retrying will not help."
)
_KAME_HINT_MARKERS = ("Retrying the same tool call", "Do NOT keep retrying", "Retrying will not help")


def _kame_with_retry_hint(exc, kind="", quota_window=""):
    """Append the hint that matches what the error is. Never a new exception:
    ``args`` change in place, so type, status and traceback are kept. Idempotent."""
    try:
        msg = str(exc)
        if any(marker in msg for marker in _KAME_HINT_MARKERS):
            return exc
        if kind in _KAME_QUOTA_WALL_KINDS or quota_window in ("per_day", "per_month"):
            hint = _KAME_QUOTA_HINT
        elif kind in _KAME_TRANSIENT_KINDS:
            hint = _KAME_RETRY_HINT
        elif kind in _KAME_PERMANENT_KINDS:
            hint = _KAME_PERMANENT_HINT
        else:
            return exc
        exc.args = (msg + hint,) + tuple(exc.args[1:])
        # LiteLLM's exceptions build str() from `.message`, not `args`; Agent
        # Zero's `_error_retry` hands the agent `format_error(exc)`, i.e. str().
        if isinstance(getattr(exc, "message", None), str) and hint not in exc.message:
            exc.message = exc.message + hint
    except Exception:
        pass
    return exc


def _kame_api_base(owner, ctx):
    try:
        base = (ctx.get("kwargs") or {}).get("api_base") or (getattr(owner, "kwargs", None) or {}).get("api_base")
        if not base:
            conf = getattr(owner, "a0_model_conf", None)
            base = getattr(conf, "api_base", "") if conf is not None else ""
        return str(base or "")
    except Exception:
        return ""


def _kame_looks_local(base) -> bool:
    base = str(base or "").lower()
    return any(mark in base for mark in ("localhost", "127.0.0.1", "0.0.0.0", "[::1]", "host.docker.internal"))


#: v1.8.2.8 automatic first-token wait, per class: (floor, ceiling, cold) s.
#: Review H2: a thinking model's silent thinking and a long prompt's reading
#: are honest silences - generous floors, and one more second per 2,000
#: prompt tokens. 1.8.2.7 had no limit at all here.
_KAME_AUTO_SILENCE_S = {False: (30.0, 180.0, 180.0), True: (180.0, 600.0, 300.0)}
_KAME_AUTO_SILENCE_TOKENS_PER_S = 2000.0


def _kame_first_word_stats(identity, reasoning):
    try:
        if _KH is None:
            return None
        return _KH.HEDGE_TRACKER.first_word_stats(identity, reasoning=bool(reasoning))
    except Exception:
        return None


def _kame_auto_silence(identity, reasoning, prompt_tokens=0) -> float:
    """'Automatic': far past this model's usual first word - max(4 x median,
    1.5 x p95), clamped per class. The backup race (2 x median) does the
    speeding up; this only ends a dead connection."""
    floor, ceiling, cold = _KAME_AUTO_SILENCE_S[bool(reasoning)]
    floor = floor + max(0, int(prompt_tokens or 0)) / _KAME_AUTO_SILENCE_TOKENS_PER_S
    stats = _kame_first_word_stats(identity, reasoning)
    if not stats:
        return float(min(ceiling, max(cold, floor)))
    median, p95 = stats
    return float(min(ceiling, max(floor, 4.0 * median, 1.5 * p95)))


def _kame_silence_for(owner, ctx, attempt_no) -> float:
    """Hermes 1.8.2.8 `attempt_read_timeout`: the wait for a first token, or 0.
    Only for a stream, never for a model running on this machine (a large prompt
    can take minutes to prefill there). `first_token_wait` decides: off; timed =
    the number, a quarter of it from the third attempt on (the provider is
    proven slow); auto (default) = learned, halved from the third attempt but
    never under its floor."""
    if _KAME_FIRST_TOKEN_MODE == "off" or ctx.get("call_type") != "Chat":
        return 0.0
    if _kame_looks_local(_kame_api_base(owner, ctx)):
        return 0.0
    if _KAME_FIRST_TOKEN_MODE == "auto":
        # Review H1: a utility call or the chat compactor streams too, over a
        # long history; only the conversation's own reply is cut automatically.
        if not _kame_main(ctx):
            return 0.0
        reasoning = _kame_reasoning_call(ctx)
        seconds = _kame_auto_silence(ctx.get("identity", ""), reasoning,
                                     _kame_est_prompt_tokens(ctx.get("messages")))
        if attempt_no >= 3:
            seconds = max(_KAME_AUTO_SILENCE_S[bool(reasoning)][0], seconds * 0.5)
        return seconds
    seconds = float(_KAME_STREAM_SILENCE_S or 0)
    if seconds <= 0:
        return 0.0
    if attempt_no >= 3:
        seconds = max(5.0, seconds * 0.25)
    return seconds


def _kame_identity_with_region(identity, owner, kwargs, model, keys):
    """Hermes 1.8.2.3 PC-01: a regional provider (Vertex, Bedrock, Azure) meters a
    key per region, so the region is the identity's third segment. ``global``
    adds nothing: a flat provider keeps the two-part identity 1.8.1.8 wrote."""
    if _KR is None:
        return identity
    try:
        base = (kwargs or {}).get("api_base") or (getattr(owner, "kwargs", None) or {}).get("api_base")
        if not base:
            conf = getattr(owner, "a0_model_conf", None)
            base = getattr(conf, "api_base", "") if conf is not None else ""
        region, source = _KR.parse_region(str(base or ""), str(model or ""))
        if _KAME_KEY_REGION_OVERRIDES:
            for k in keys or []:
                override = _KR.region_from_overrides(_KAME_KEY_REGION_OVERRIDES, _key_short_id(k))
                if override:
                    region, source = _KR.parse_region("", "", override=override)
                    break
        segment = _KR.identity_segment(region, source)
        return f"{identity}:{segment}" if segment else identity
    except Exception:
        return identity


def _kame_seed_triggers(identity, reasoning):
    """Hermes 1.8.2.5: once per model per process, the backup trigger learns the
    first-word times this machine already recorded (calls.jsonl), so it is tuned
    from the first call after a restart instead of after twenty."""
    if _KH is None or _KJ is None or (identity, reasoning) in _KAME_SEEDED:
        return
    _KAME_SEEDED.add((identity, reasoning))
    try:
        history = getattr(_KJ, "first_word_history", None)
        samples = (history() or {}).get(identity) if history is not None else None
        if samples:
            _KH.HEDGE_TRACKER.seed_stream(identity, samples, reasoning=reasoning)
    except Exception:
        pass


#: v1.8.2.8 (Hermes decisions/0028 D6): the last replies of the conversation
#: for the panel, and per Agent Zero context the steps of the turn in flight,
#: for the one line shown when the turn ends.
_KAME_REPLIES = collections.deque(maxlen=30)
_KAME_TURNS = {}
_KAME_TURNS_KEEP = 64


def _kame_model_label(identity) -> str:
    s = str(identity or "")
    return s.split(":", 1)[1] if ":" in s else s


def _kame_after_answer(identity, ctx, call_started_perf) -> None:
    """An answer: the provider is not busy any more, and the reply is named."""
    if ctx.get("answered_by"):
        # D12: the next model won the race; its own call (inside the hop) already
        # named the reply "for" this model, and this model stays left alone.
        return
    _kame_clear_busy(identity)
    if not _kame_main(ctx):
        return
    _kame_ledger("replies")   # 1.8.2.9: one answered reply of the conversation
    try:
        now_perf = time.perf_counter()
        total = now_perf - float(call_started_perf)
        progress = ctx.get("progress") or {}
        first = progress.get("first_text_at") or progress.get("first_sign_at")
        started_wall = time.time() - total
        first_s = (float(first) - started_wall) if first else None
        hop = _KAME_FALLBACK_HOP.get()
        row = {"at": time.time(), "model": _kame_model_label(identity),
               "for": _kame_model_label(hop.get("origin")) if hop else "",
               "first_s": first_s if (first_s is None or first_s >= 0) else None,
               "total_s": total,
               # 1.8.3.0: the conversation's own model, as Hermes' reply row carries it
               "main": _kame_main_pair(hop.get("origin") if hop else identity)}
        with _KAME_LOCK:
            _KAME_REPLIES.append(row)
        agent = _KAME_CURRENT_AGENT.get()
        context = getattr(agent, "context", None)
        if context is not None:
            with _KAME_LOCK:
                turn = _KAME_TURNS.get(id(context))
                if turn is not None:
                    turn["steps"].append(row)
    except Exception:
        pass


def turn_started(agent) -> None:
    """Agent Zero's ``monologue_start`` for the top agent: a new turn begins."""
    try:
        if agent is None or int(getattr(agent, "number", 0) or 0) != 0:
            return
        context = getattr(agent, "context", None)
        if context is None:
            return
        with _KAME_LOCK:
            _KAME_TURNS[id(context)] = {"started": time.time(), "steps": []}
            while len(_KAME_TURNS) > _KAME_TURNS_KEEP:
                _KAME_TURNS.pop(next(iter(_KAME_TURNS)))
    except Exception:
        pass


def _kame_secs(seconds) -> str:
    if seconds is None:
        return "-"
    if seconds < 60:
        return f"{seconds:.1f} s"
    m, s = divmod(int(round(seconds)), 60)
    return f"{m} min {s:02d} s"


def reply_line(agent):
    """The one line shown when the top agent's turn ends, or ``None``:
    the model(s) that answered, how many steps each, and the turn's time.
    `reply_notice`: always / switches (only when a fallback model answered) /
    never. Shown in Agent Zero's log, never in the model's history."""
    try:
        if agent is None or int(getattr(agent, "number", 0) or 0) != 0:
            return None
        context = getattr(agent, "context", None)
        with _KAME_LOCK:
            turn = _KAME_TURNS.pop(id(context), None) if context is not None else None
        if not turn or not turn["steps"] or _KAME_REPLY_NOTICE == "never":
            return None
        steps = turn["steps"]
        stand_in = any(s.get("for") for s in steps)
        if _KAME_REPLY_NOTICE == "switches" and not stand_in:
            return None
        order, counts = [], {}
        for s in steps:
            name = s["model"] + (f" for {s['for']}" if s.get("for") else "")
            if name not in counts:
                order.append(name)
                counts[name] = 0
            counts[name] += 1
        parts = [f"{name}" + (f" ({counts[name]} steps)" if counts[name] > 1 else "") for name in order]
        first = steps[0].get("first_s")
        took = time.time() - float(turn["started"])
        line = " · ".join(parts)
        tail = f"{_kame_secs(took)}" + (f" · first word {_kame_secs(first)}" if first is not None else "")
        return f"⏱ {tail} · {line}"
    except Exception:
        return None


def _kame_main_pair(identity) -> dict:
    """``provider:model`` as ``{"provider", "model"}`` (empty when the identity has no provider)."""
    text = str(identity or "")
    provider, sep, model = text.partition(":")
    return {"provider": provider, "model": model} if sep else {"provider": "", "model": text}


def replies_report() -> list:
    with _KAME_LOCK:
        rows = list(_KAME_REPLIES)
    return [dict(r) for r in reversed(rows[-12:])]


def _kame_learn_key(identity, key, ctx, started_wall):
    """An answered attempt teaches the pacer (one dispatch in this key's window)
    and the latency tiebreak (time to the first sign for a stream, the whole
    call otherwise) - Hermes `_learn_latency` / `PACER.stamp`."""
    try:
        if _KP is not None:
            _KP.PACER.stamp(identity, key)
    except Exception:
        pass
    try:
        if _KL is None:
            return
        progress = ctx.get("progress") or {}
        if ctx.get("call_type") == "Chat":
            first = progress.get("first_text_at") or progress.get("first_sign_at")
            if not first:
                return
            sample = float(first) - float(started_wall)
        else:
            sample = time.time() - float(started_wall)
        _KL.LATENCY.record(identity, key, sample)
    except Exception:
        pass


def _kame_can_continue(owner, ctx) -> bool:
    """Hermes 1.1.1 / 1.8.2.7 stitching, where Agent Zero allows it: a chat
    stream on the Chat Completions transport (Agent Zero's default; a
    Responses-API call keeps provider state that a continuation would break),
    the stitcher and an assistant message type available, the switch on, and
    the continuation budget not spent. Otherwise the answer starts again, as
    in 1.8.1.8."""
    if _KS is None or AIMessage is None or _KAME_STREAM_STITCH_DISABLED or _KAME_STREAM_RESUME_LIMIT == 0:
        return False
    if ctx.get("call_type") != "Chat":
        return False
    if 0 < _KAME_STREAM_RESUME_LIMIT <= int(ctx.get("resumes") or 0):
        return False
    kwargs = ctx.get("kwargs") or {}
    mode = kwargs.get("a0_api_mode") or (getattr(owner, "kwargs", None) or {}).get("a0_api_mode") or ""
    if str(mode).lower().strip() in ("response", "responses", "responses_api"):
        return False
    if kwargs.get("a0_responses_function_tools") or kwargs.get("previous_response_id"):
        return False
    return True


def _kame_prepare_continuation(owner, ctx, identity):
    """Before an attempt: continue the answer the user already saw, or start clean."""
    ctx.pop("stitcher", None)
    ctx.pop("attempt_messages", None)
    seen = ctx.get("stitch_seen") or ""
    if not seen.strip() or not _kame_can_continue(owner, ctx):
        return
    try:
        messages = list(ctx["messages"]) + [AIMessage(content=seen.rstrip())]
        if identity in _KAME_NO_PREFILL:
            messages.append(HumanMessage(content=_KS.CONTINUE_INSTRUCTION))
        ctx["attempt_messages"] = messages
        ctx["stitcher"] = _KS.Stitcher(seen)
        ctx["resumes"] = int(ctx.get("resumes") or 0) + 1
    except Exception:
        ctx.pop("stitcher", None)
        ctx.pop("attempt_messages", None)


def _kame_stitched_result(result, text):
    """Agent Zero's own result with the whole answer the user saw."""
    try:
        if isinstance(result, tuple):
            return (text,) + tuple(result[1:]) if result else (text, "")
        cls = type(result)
        if getattr(result, "mode", "") == "chat_completions" and hasattr(cls, "from_chat") \
                and not getattr(result, "function_calls", None):
            rebuilt = cls.from_chat(
                response=text, reasoning=getattr(result, "reasoning", "") or "",
                usage=getattr(result, "usage", None), input_items=getattr(result, "input_items", None),
                provider_model_key=getattr(result, "provider_model_key", "") or "",
                capability=getattr(result, "capability", None))
            if getattr(result, "history_extras", ""):
                rebuilt.history_extras = result.history_extras
            return rebuilt
        result.response = text
    except Exception:
        pass
    return result


async def _kame_finish_continuation(ctx, result):
    """A continued answer ended: show what the stitcher still held, and hand
    Agent Zero one whole answer."""
    stitcher = ctx.pop("stitcher", None)
    ctx.pop("attempt_messages", None)
    if stitcher is None:
        return result
    seen = ctx.pop("stitch_seen", "") or ""
    try:
        tail = stitcher.flush()
        callback = ctx.get("a0_response_callback")
        if tail and callback is not None:
            await callback(tail, seen + stitcher.forwarded)
        _kame_event("stitch", ctx.get("identity", ""),
                    reason=f"cut answer continued ({stitcher.mode or 'new'}), shown once")
        return _kame_stitched_result(result, seen + stitcher.forwarded)
    except Exception:
        return result

async def _kame_raced_attempt(owner, ctx, key, *, burst_keys=None, attempt_no=0, call_id="",
                              call_started_at=0.0, st=None):
    """One attempt that may grow backup legs. Returns ``(result, winning_key)``.

    Raises the exception of the LAST leg to fail, with ``_kame_leg_key`` and
    ``_kame_leg_elapsed`` set on it, so the carousel judges exactly one failure
    the way it always has; the other failed legs were judged here.

    With no backup due and no burst, this is ``await ctx["attempt"](...)`` and
    nothing else: the lone call is not wrapped, shimmed or timed differently.
    """
    identity = ctx["identity"]
    all_keys = ctx["all_keys"]
    stream = ctx["call_type"] == "Chat"
    reasoning = _kame_reasoning_call(ctx)
    # 1.8.2.9 (E7): a background job never asks keys together, never races a backup (a job that
    # KAME does not budget, review H2, is still a background job: it only keeps the old wait).
    hedging_on = (_KH is not None and _kame_hedge_mode() != "off" and not _KAME_CAROUSEL_DISABLED
                  and len(all_keys) >= 2 and ctx.get("aux_task") is None)
    silence = _kame_silence_for(owner, ctx, attempt_no)
    slow = _kame_slow_after(ctx, identity, reasoning) if stream else 0.0
    cross = list(_kame_cross_entry(ctx, identity, reasoning, slow) or []) if slow else []
    # 1.8.2.9 (E2): the start probe asks the next free model at once, beside the busy best model.
    probe_cross = (list(ctx.get("probe_cross") or [])
                   if (stream and ctx.get("probe_primary") and not burst_keys) else [])
    if probe_cross:
        cross = probe_cross
    if hedging_on and stream:
        _kame_seed_triggers(identity, reasoning)
    if not hedging_on and not burst_keys and not silence and not slow and not cross:
        t0 = time.monotonic()
        _kame_count_request(ctx, identity)   # 1.8.2.9 (E8)
        try:
            result = await ctx["attempt"](owner, key, ctx)
        except BaseException as exc:
            _kame_tag(exc, key, time.monotonic() - t0)
            raise
        _kame_learn_lone(ctx, t0, stream)
        return result, key

    race = _KameRace(ctx)
    race.burst = bool(burst_keys)
    race_wall = time.time()

    def sweep(skip=None):
        """Judge every leg that ended and was not looked at yet (second review N2)."""
        for leg in sorted(race.tasks):
            task = race.tasks[leg]
            if leg == skip or not task.done() or getattr(task, "_kame_seen", False):
                continue
            if leg in race.cross_legs:   # the next model's failure is its own (D12)
                task._kame_seen = True
                if not task.cancelled():
                    task.exception()
                continue
            task._kame_seen = True
            if task.cancelled():
                continue
            exc = task.exception()
            if exc is None or (_KAME_PASSTHROUGH_EXC and isinstance(exc, _KAME_PASSTHROUGH_EXC)) \
                    or isinstance(exc, (asyncio.CancelledError, _KameSwitchModel)) \
                    or _is_terminal_error(exc):
                continue   # a request fault on a loser rests no key (third review P3)
            elapsed = time.monotonic() - race.started[leg]
            _kame_judge_leg(identity, race.keys[leg], exc, elapsed, ctx, attempt_no,
                            call_id, race_wall, call_started_at, st)
            _kame_tag(exc, race.keys[leg], elapsed)
            race.last_failure = exc

    legs = [key] + list(burst_keys or [])
    for i, k in enumerate(legs):
        race.start(owner, i, k)
    if burst_keys:
        _kame_ledger("burst", len(legs) - 1)   # the keys beyond the first are the extra requests
        with _KAME_LOCK:
            _KAME_SPEED_STATS["probe_bursts"] += 1
        _kame_event("backup", identity, reason=f"pool running dry: {len(legs)} keys asked together",
                    seconds=None)
    max_legs = 1 if (burst_keys or not hedging_on) else (
        _KAME_HEDGE_MAX_LEGS if stream else min(_KAME_HEDGE_MAX_LEGS, _KAME_NON_STREAM_MAX_LEGS))
    est_tokens = _kame_est_prompt_tokens(ctx.get("messages"))
    if not hedging_on:
        triggers = []
    elif stream and not burst_keys:
        first_t, second_t = _KH.HEDGE_TRACKER.stream_triggers(identity, reasoning=reasoning)
        triggers = [first_t, second_t]
    elif not burst_keys:
        threshold = _KH.HEDGE_TRACKER.threshold(identity, stream=False, reasoning=reasoning)
        triggers = [threshold] if threshold is not None else []
    else:
        triggers = []
    empties = []   # legs that answered with nothing, kept in case nobody does better
    slow_at = None    # monotonic time of the slow bound (review R3: may move for a raced model)
    slow_extended = False
    widened = 0       # 1.8.2.9 (E3): models added at the slow bound (at most _KAME_WIDEN_MAX)
    said_waiting = False
    try:
        while True:
            running = race.running()
            if race.winner is not None:
                win = race.tasks[race.winner]
                sweep(skip=race.winner)
                await race.cancel_all(except_leg=race.winner)
                try:
                    result = await win
                except BaseException as exc:
                    _kame_tag(exc, race.keys[race.winner], time.monotonic() - race.started[race.winner])
                    raise
                if race.winner in race.cross_legs:
                    _kame_cross_won(ctx, identity, race)
                    return result, race.keys[race.winner]
                _kame_race_learn(ctx, race, race.winner, stream, reasoning)
                if race.winner > 0 and not race.burst:
                    with _KAME_LOCK:
                        _KAME_SPEED_STATS["backup_wins"] += 1
                return result, race.keys[race.winner]
            # Every leg that ended since the last look, read together (several
            # can end in one tick: a burst of eight refused at once). Each
            # exception is retrieved, an answer is preferred, and every failure
            # is judged on its own key - except the one handed to the carousel.
            ended = []
            for leg in sorted(race.tasks):
                task = race.tasks[leg]
                if not task.done() or getattr(task, "_kame_seen", False):
                    continue
                task._kame_seen = True
                if task.cancelled():
                    continue
                if leg in race.cross_legs:
                    exc = task.exception()
                    if exc is None and (race.progress[leg]["any"] or not _kame_result_is_empty(task.result())):
                        race.winner = leg       # it answered without streaming: still the answer
                    continue                    # its failure is its own (D12)
                ended.append((leg, task.exception(), time.monotonic() - race.started[leg]))
            if race.winner is not None and race.winner in race.cross_legs:
                continue
            for leg, exc, _e in ended:
                if exc is not None and (
                        (_KAME_PASSTHROUGH_EXC and isinstance(exc, _KAME_PASSTHROUGH_EXC))
                        or isinstance(exc, (asyncio.CancelledError, _KameSwitchModel))):
                    _kame_race_judge_rest(race, ended, leg, identity, ctx, attempt_no, call_id,
                                          race_wall, call_started_at, st)
                    await race.cancel_all()
                    raise exc
            answers = [(leg, race.tasks[leg].result()) for leg, exc, _e in ended if exc is None]
            useful = [(leg, r) for leg, r in answers
                      if race.progress[leg]["any"] or not _kame_result_is_empty(r)]
            failures = [(leg, exc, el) for leg, exc, el in ended if exc is not None]
            if useful:
                leg, result = useful[0]
                race.winner = leg
                for f_leg, f_exc, f_el in failures:
                    if _is_terminal_error(f_exc):
                        continue   # the request's fault, not the key's (third review P3)
                    _kame_judge_leg(identity, race.keys[f_leg], f_exc, f_el, ctx, attempt_no,
                                    call_id, race_wall, call_started_at, st)
                await race.cancel_all(except_leg=leg)
                _kame_race_learn(ctx, race, leg, stream, reasoning)
                if leg > 0 and not race.burst:
                    with _KAME_LOCK:
                        _KAME_SPEED_STATS["backup_wins"] += 1
                return result, race.keys[leg]
            empties.extend(answers)  # an empty leg waits for the others (Hermes 1.8.2.4)
            terminal = next(((l, x, e) for l, x, e in failures if _is_terminal_error(x)), None)
            running = race.running()
            if terminal is not None or (failures and not running and not empties):
                # This failure goes to the carousel; the others are judged here.
                last = terminal or failures[-1]
                for f_leg, f_exc, f_el in failures:
                    if f_leg != last[0]:
                        _kame_judge_leg(identity, race.keys[f_leg], f_exc, f_el, ctx, attempt_no,
                                        call_id, race_wall, call_started_at, st)
                await race.cancel_all(except_leg=last[0])
                _kame_tag(last[1], race.keys[last[0]], last[2])
                raise last[1]
            for f_leg, f_exc, f_el in failures:
                _kame_judge_leg(identity, race.keys[f_leg], f_exc, f_el, ctx, attempt_no,
                                call_id, race_wall, call_started_at, st)
                _kame_tag(f_exc, race.keys[f_leg], f_el)
                race.last_failure = f_exc
            if not running:
                if empties:
                    # Every leg that answered answered empty: hand back the first.
                    leg, result = empties[0]
                    return result, race.keys[leg]
                # Only cancelled legs are left. A real failure seen on the way is
                # what this attempt ended with (already judged: second review N4).
                if race.last_failure is not None:
                    raise race.last_failure
                raise asyncio.CancelledError()
            # Wait for the next event: a leg ends, the race is claimed, or a trigger.
            wait_for = None
            if triggers and len(race.tasks) < max_legs:
                due = race.started[0] + triggers[0]
                wait_for = max(0.0, due - time.monotonic())
            if silence and race.winner is None:
                s_left = max(0.0, race.started[0] + silence - time.monotonic())
                wait_for = s_left if wait_for is None else min(wait_for, s_left)
            if slow and slow_at is None:
                slow_at = race.started[0] + slow
            if slow and race.winner is None:
                s_left = max(0.0, slow_at - time.monotonic())
                wait_for = s_left if wait_for is None else min(wait_for, s_left)
            if cross and race.winner is None:
                c_left = max(0.0, race.started[0] + cross[0][1] - time.monotonic())
                wait_for = c_left if wait_for is None else min(wait_for, c_left)
            claim_waiter = asyncio.ensure_future(race.claimed.wait())
            try:
                done, _ = await asyncio.wait([race.tasks[l] for l in running] + [claim_waiter],
                                             timeout=wait_for, return_when=asyncio.FIRST_COMPLETED)
            finally:
                if not claim_waiter.done():
                    claim_waiter.cancel()
            if done or race.winner is not None:
                continue
            # v1.8.2.8 (Hermes D12): the next model of the list races a silent reply.
            if cross and time.monotonic() >= race.started[0] + cross[0][1] and \
                    not any(p.get("first_sign_at") for p in race.progress.values()):
                entry, _at = cross.pop(0)
                if probe_cross:
                    probe = bool(ctx.get("probe_cross_claimed"))   # claimed when the probe was allowed
                else:
                    probe = _kame_claim_entry(entry)     # 1.8.2.9: a busy model is asked only if due
                    if probe is None:
                        continue                         # another conversation took this model's ask
                race.start_cross(owner, len(race.tasks), entry, probe)
                _fl = ctx.get("flight")
                if isinstance(_fl, dict):   # data for the status line (phase C): who is racing the reply
                    _fl["racing"] = [e["label"] for e in race.cross_legs.values()]
                    if probe_cross:
                        _fl["probe"] = _kame_model_label(identity)
                if not probe_cross:
                    # 1.8.2.9 (E10): the chat's status line names who races the slow model
                    ctx["race_line"] = True
                    _kame_status_line(ctx, f"{_kame_model_label(identity)} is slow - "
                                           f"{_kame_model_label(entry['identity'])} races it", st)
                if probe_cross:
                    _kame_event("model_switch", identity,
                                reason=f"your best model was busy: asked again beside {entry['label']}, "
                                       "the first to answer is shown")
                else:
                    _kame_event("model_switch", identity,
                                reason=f"no first word after {_fmt_duration(time.monotonic() - race.started[0])}: "
                                       f"{entry['label']} (a next model of your list) races it")
                continue
            # v1.8.2.8 (Hermes D11): the model is slow right now - no leg shows
            # any sign of work by its slow bound. The next model answers.
            if slow and time.monotonic() >= slow_at and \
                    not any(p.get("first_sign_at") for p in race.progress.values()):
                now_m = time.monotonic()
                live = [l for l in race.cross_legs if not race.tasks[l].done()]
                if live and not slow_extended:
                    # 1.8.2.9 (E3 B1): a raced model keeps the reply waiting only to ITS late point,
                    # never past what 1.8.2.8 waited (the last raced start + the whole bound).
                    slow_extended = True
                    later = _kame_extended_bound(ctx, identity, reasoning, race, live, slow, slow_at)
                    if later > now_m:
                        slow_at = later
                        continue
                if (widened < _KAME_WIDEN_MAX and len(race.cross_legs) < _KAME_RACE_MAX_CROSS
                        and not race.burst and _kame_hedge_mode() != "off" and _kame_cross_allowed(ctx)):
                    # B2: ONE more model of the list joins at the bound; nothing is cut.
                    entry = _kame_free_entry_outside(ctx, identity, race)
                    probe = _kame_claim_entry(entry) if entry is not None else None
                    if entry is not None and probe is not None:
                        widened += 1
                        slow_extended = True
                        race.start_cross(owner, len(race.tasks), entry, probe)
                        _kame_event("model_switch", identity,
                                    reason=f"no first word after {_fmt_duration(now_m - race.started[0])}: "
                                           f"{entry['label']} (one more model of your list) joins")
                        live = [l for l in race.cross_legs if not race.tasks[l].done()]
                        slow_at = max(now_m, _kame_extended_bound(ctx, identity, reasoning, race, live,
                                                                 slow, now_m))
                        continue
                if _kame_free_entry_outside(ctx, identity, race) is None:
                    # B3: nothing else could answer - the legs that run are waited for, none is cut,
                    # nothing is marked slow. The first to show work wins.
                    if not said_waiting:
                        said_waiting = True
                        _kame_event("model_switch", identity,
                                    reason=f"no first word after {_fmt_duration(now_m - race.started[0])}: "
                                           "no other model of your list can answer, still waiting")
                    slow = 0.0
                    slow_at = None
                    continue
                waited = time.monotonic() - race.started[0]
                await race.cancel_all()
                _kame_note_busy(identity, first=_KAME_SLOW_FIRST_S, ceiling=_KAME_SLOW_MAX_S, kind="slow")
                for entry in race.cross_legs.values():   # the models raced in were silent too
                    _kame_note_busy(entry["identity"], first=_KAME_SLOW_FIRST_S, ceiling=_KAME_SLOW_MAX_S,
                                 kind="slow")
                raise _KameSlowModel(f"{identity.split(':', 1)[-1]} is slow right now "
                                     f"(no first word after {_fmt_duration(waited)})")
            # v1.8.2.7 (Hermes `stream_silence_timeout_seconds`, "wait for the
            # first token"): every leg silent that long - the key accepted the
            # request and sends nothing. Drop it as a timeout; the next key is tried.
            if silence and time.monotonic() >= race.started[0] + silence and \
                    not any(p.get("first_sign_at") for p in race.progress.values()):
                await race.cancel_all()
                exc = TimeoutError(
                    f"KAME: no first token within {silence:.0f}s on this key "
                    "(stream_silence_timeout_seconds)")
                _kame_tag(exc, race.keys[0], time.monotonic() - race.started[0])
                raise exc
            if not triggers:
                continue
            # A trigger passed with no sign of work: maybe start a backup.
            triggers.pop(0)
            healthy, _total = _kame_pool_counts(identity, all_keys)
            if not _KH.HEDGE_TRACKER.may_start_backup(
                    identity, healthy_count=healthy,
                    under_pressure=_kame_under_pressure(identity, all_keys),
                    stream=stream, reasoning=reasoning, est_prompt_tokens=est_tokens):
                continue
            more = _kame_pick_more(identity, all_keys, set(race.keys.values()), 1)
            if not more:
                continue
            leg = len(race.tasks)
            race.start(owner, leg, more[0])
            _KH.HEDGE_TRACKER.note_hedge(identity)
            _kame_ledger("backup")
            with _KAME_LOCK:
                _KAME_SPEED_STATS["backups"] += 1
            _kame_event("backup", identity, more[0],
                        reason=("no first word after %.0fs: backup key started" % (time.monotonic() - race.started[0]))
                        if stream else ("slower than usual after %.0fs: backup key started" % (time.monotonic() - race.started[0])))
    except BaseException:
        await race.cancel_all(except_leg=None if race.winner is None else race.winner)
        raise


def _kame_cross_won(ctx, identity, race):
    """The next model of the list answered first (D12): name it, leave the slow
    model alone like a busy one so the next replies go straight on."""
    entry = race.cross_legs.get(race.winner) or {}
    ctx["answered_by"] = entry.get("identity")
    if not ctx.get("probe_primary"):
        # 1.8.2.9 (E2): losing the START probe is not proof the model is still busy.
        _kame_note_busy(identity)
    for leg, other in race.cross_legs.items():   # raced in before the winner and still silent
        if leg != race.winner and not race.progress.get(leg, {}).get("first_sign_at"):
            _kame_note_busy(other["identity"])
    why = "your provider is busy" if ctx.get("probe_primary") else "slower than usual"
    with _KAME_LOCK:
        _KAME_SPEED_STATS["model_switches"] = _KAME_SPEED_STATS.get("model_switches", 0) + 1
        _KAME_LAST_MODEL_SWITCH.update({"from": identity, "to": entry.get("label", ""),
                                        "reason": f"{why}: raced", "at": time.time()})
    _kame_event("model_switch", identity,
                reason=f"{why}: {entry.get('label', 'the next model')} answered first, "
                       f"{_fmt_duration(time.monotonic() - race.started[0])} after the request")


def _kame_race_judge_rest(race, ended, raising_leg, identity, ctx, attempt_no, call_id,
                          race_wall, call_started_at, st):
    """Before a stop/intervention leaves the race: judge the other legs that failed."""
    for leg, exc, elapsed in ended:
        if leg == raising_leg or exc is None:
            continue
        if (_KAME_PASSTHROUGH_EXC and isinstance(exc, _KAME_PASSTHROUGH_EXC)) or \
                isinstance(exc, (asyncio.CancelledError, _KameSwitchModel)):
            continue
        _kame_judge_leg(identity, race.keys[leg], exc, elapsed, ctx, attempt_no, call_id,
                        race_wall, call_started_at, st)


def _kame_tag(exc, key, elapsed):
    try:
        exc._kame_leg_key = key
        exc._kame_leg_elapsed = elapsed
    except Exception:
        pass


def _kame_race_learn(ctx, race, winner, stream, reasoning):
    """Teach the latency tracker: the winner's time to first sign (stream) or to
    the answer, and every cut leg as a lower bound (censored)."""
    if _KH is None:
        return
    try:
        identity = ctx["identity"]
        now = time.monotonic()
        w_started = race.started[winner]
        first = race.progress[winner].get("first_sign_at")
        if stream and first:
            took = max(0.0, first - (time.time() - (now - w_started)))
        else:
            took = now - w_started
        _KH.HEDGE_TRACKER.record_latency(identity, took, stream=stream, reasoning=reasoning)
        if race.burst:
            return  # legs asked together started at once: a loser says nothing new
        for leg, started in race.started.items():
            if leg == winner:
                continue
            else:
                # A cut leg was slower than the winner's FIRST SIGN, not than
                # its whole answer: the lower bound is taken at the cut.
                cut_at = race.claim_at if race.claim_at is not None else now
                bound = max(0.0, cut_at - started)
                # Only a real lower bound: a backup cut 0.1 s after it started
                # says nothing about a model the winner needed 0.3 s for, and
                # stored as a sample it would pull the trigger down (second review N3).
                if bound >= took * 0.95:   # started with the winner (clock noise aside)
                    _KH.HEDGE_TRACKER.record_latency(identity, bound, stream=stream,
                                                     reasoning=reasoning, censored=True)
    except Exception:
        pass


def _kame_learn_lone(ctx, started_mono, stream):
    """A call that ran alone still teaches its model's usual time."""
    if _KH is None:
        return
    try:
        reasoning = _kame_reasoning_call(ctx)
        if stream:
            first = (ctx.get("progress") or {}).get("first_sign_at")
            if not first:
                return
            took = max(0.0, first - (time.time() - (time.monotonic() - started_mono)))
        else:
            took = time.monotonic() - started_mono
        _KH.HEDGE_TRACKER.record_latency(ctx["identity"], took, stream=stream, reasoning=reasoning)
    except Exception:
        pass


def _kame_fallback_model(owner, entry):
    """A model instance for one list entry, built by Agent Zero's own factory."""
    cache_key = (entry["provider"], entry["name"], entry.get("api_base", ""))
    model = _KAME_FALLBACK_MODEL_CACHE.get(cache_key)
    if model is not None:
        return model
    import models as _models  # Agent Zero's module; never imported at load time
    base_conf = getattr(owner, "a0_model_conf", None)
    mc = _models.ModelConfig(
        type=_models.ModelType.CHAT, provider=entry["provider"], name=entry["name"],
        api_base=entry.get("api_base", "") or "",
        ctx_length=int(getattr(base_conf, "ctx_length", 0) or 0),
        vision=bool(getattr(base_conf, "vision", False)),
    )
    model = _models.get_chat_model(entry["provider"], entry["name"], model_config=mc, **mc.build_kwargs())
    _KAME_FALLBACK_MODEL_CACHE[cache_key] = model
    return model


def _kame_rest_pool_for_door(identity, keys, seconds):
    """A closed door rests every key of the pool until it may open, and nothing
    else: no strike, no ladder, no counter of any key is touched, and the last
    refusal each key recorded stays its own (review 2026-10-17, M3)."""
    now = time.time()
    until = now + min(float(seconds), _KAME_MAX_HOLD_S)
    with _KAME_LOCK:
        state = _get_identity_state(identity, keys)
        for k in keys:
            kd = state["keys"].get(k)
            if kd is None:
                continue
            if float(kd.get("model_until", 0) or 0) < until:
                kd["model_until"] = until
                kd["model_kind"] = "server"
                kd["model_scope"] = ""
                if _KJ is not None:
                    try:
                        _KJ.note_hold(identity, k, until, "server", "")
                    except Exception:
                        pass
            _effective_hold_until(identity, k, kd)


def _kame_identity_of(model) -> str:
    """The pool identity KAME's wrapper gives this model (``provider:model_name``)."""
    conf = getattr(model, "a0_model_conf", None)
    provider = (getattr(conf, "provider", "") if conf is not None else "") or "unknown"
    return f"{str(provider).lower()}:{(getattr(model, 'model_name', '') or 'unknown').lower()}"


#: v1.8.2.8 (Hermes decisions/0028). A busy provider: failures of these kinds
#: on two different keys in one call, or a throttle resting its key this short.
_KAME_PROVIDER_TIMEOUT_STATUSES = frozenset({408, 499, 504, 524})
_KAME_BUSY_FIRST_S = 30.0
_KAME_BUSY_MAX_S = 300.0
#: v1.8.2.8 (D11): a slow model stays slow for minutes and each re-check costs its
#: whole slow bound - 2 minutes, doubling while it stays slow, at most 10.
_KAME_SLOW_FIRST_S = 120.0
_KAME_SLOW_MAX_S = 600.0
_KAME_BUSY_UNTIL = {}
_KAME_BUSY_WINDOW = {}
_KAME_BUSY_KIND = {}      # identity -> "busy" (the provider said so) or "slow" (no first word)
#: "Automatic" switch: about three times the usual first word, within these.
_KAME_AUTO_SWITCH_MIN_S = 6.0
_KAME_AUTO_SWITCH_MAX_S = 20.0
_KAME_AUTO_SWITCH_COLD_S = 10.0


def _kame_switch_on() -> bool:
    if _KAME_MODEL_SWITCH == "never" or _KAME_NO_MODEL_FALLBACK:
        return False
    return _KAME_MODEL_SWITCH == "auto" or _KAME_FAILOVER_AFTER_S > 0


def _kame_switch_threshold(identity, ctx=None) -> float:
    """Seconds of the user's wait after which the next model answers; 0 = never."""
    if _KAME_MODEL_SWITCH == "never":
        return 0.0
    if _KAME_MODEL_SWITCH == "timed":
        return float(_KAME_FAILOVER_AFTER_S)
    if ctx:
        reasoning = _kame_reasoning_call(ctx)
    else:
        try:
            reasoning = bool(_KH and _KH.reasoning_model(str(identity).split(":", 1)[-1], None))
        except Exception:
            reasoning = False
    stats = _kame_first_word_stats(identity, reasoning)
    if not stats:
        return _KAME_AUTO_SWITCH_COLD_S
    return float(min(_KAME_AUTO_SWITCH_MAX_S, max(_KAME_AUTO_SWITCH_MIN_S, 3.0 * stats[0])))


def _kame_busy_failure(identity, key, kind, status=None) -> bool:
    """The PROVIDER says it is busy: a 5xx / "overloaded", or a timeout it
    answered with itself (408 / 499 / 504 / 524). Not a cut on our side (KAME's
    first-token wait, a client timeout - review H2) and not a throttle, which
    can be one key's own quota (review M5)."""
    if kind == "server":
        return True
    if kind == "timeout":
        try:
            return int(status) in _KAME_PROVIDER_TIMEOUT_STATUSES
        except (TypeError, ValueError):
            return False
    return False


def _kame_note_busy(identity, first=None, ceiling=None, kind="busy") -> None:
    """Open, or double, the busy (or slow) window. It doubles when the model is caught
    again within one window of the last one ending, not only 30 s."""
    first = _KAME_BUSY_FIRST_S if first is None else first
    ceiling = _KAME_BUSY_MAX_S if ceiling is None else ceiling
    now = time.time()
    with _KAME_LOCK:
        until = _KAME_BUSY_UNTIL.get(identity, 0.0)
        window = _KAME_BUSY_WINDOW.get(identity, 0.0)
        window = min(ceiling, max(first, window * 2.0)) \
            if (window and now < until + max(_KAME_BUSY_FIRST_S, window)) else first
        _KAME_BUSY_WINDOW[identity] = window
        _KAME_BUSY_UNTIL[identity] = now + window
        _KAME_BUSY_KIND[identity] = kind    # 1.8.2.9: the page says busy or slow
        # 1.8.2.9: the failure that proves a model busy is its last ask; it is asked again
        # on the probe clock (`_KAME_PROBE_EVERY_S`), not once per key round trip.
        _KAME_PROBED_AT[identity] = now


def _kame_is_busy(identity, busy_keys) -> bool:
    """Two keys busy in this call, or one while the provider is known busy:
    the window and as long again after it (inside the window a call is passed
    down the list without a request; the first knock after it decides alone)."""
    if len(busy_keys) >= 2:
        return True
    return bool(busy_keys) and time.time() < _KAME_BUSY_UNTIL.get(identity, 0.0) + _KAME_BUSY_FIRST_S


def _kame_entry_usable(identity, keys) -> bool:
    if not keys:
        return True  # one key or none: Agent Zero calls it as it is
    if _pool_has_ready_key(identity, keys):
        return True
    eta = _next_recovery_seconds(identity, keys)
    if eta is None:
        # never tried: every key is ready
        with _KAME_LOCK:
            known = identity in _KAME_KEY_HEALTH
        return not known
    return eta <= max(_KAME_HOP_BOUND_MIN_S, _kame_switch_threshold(identity))


#: The Agent Zero methods a conversation turn is made from, and the ones a
#: background job is. A utility call or the chat compactor may stream too
#: (`call_utility_model(callback=...)`, `_chat_compaction`), so streaming alone
#: does not make a call the conversation (review 2026-10-17, M4).
_KAME_CHAT_FRAMES = frozenset({"call_chat_model", "call_chat_model_turn"})
_KAME_BACKGROUND_FRAMES = frozenset({"call_utility_model", "summarize_messages", "summarize",
                                     "compact_chat", "_compact_single_pass", "_compact_large_history"})


_KAME_RACED_MAIN = contextvars.ContextVar("kame_raced_main", default=False)


def _kame_is_main_chat(stream) -> bool:
    """Whether the running call is the conversation itself (Agent Zero's
    `call_chat_model` / `call_chat_model_turn` on the stack, nearest wins)."""
    if not stream or "Compress" in (_KAME_CALL_CONTEXT.get() or ""):
        return False
    if _KAME_RACED_MAIN.get():
        return True   # review R6: a next model racing the reply runs in its own task
    try:
        import sys as _sys
        frame = _sys._getframe(1)
    except Exception:
        return bool(stream)
    depth = 0
    while frame is not None and depth < 60:
        name = frame.f_code.co_name
        if name in _KAME_CHAT_FRAMES:
            return True
        if name in _KAME_BACKGROUND_FRAMES:
            return False
        frame = frame.f_back
        depth += 1
    return False


def _kame_main(ctx) -> bool:
    return bool(ctx.get("main_chat", ctx.get("call_type") == "Chat"))


def _kame_nothing_shown(ctx) -> bool:
    """Nothing of this CALL has reached the screen, on any attempt so far."""
    progress = ctx.get("progress") or {}
    return not (ctx.get("shown") or progress.get("any") or progress.get("reasoning"))


def _kame_slot_identity(slot) -> str:
    """``provider:name`` (lower case) of one `_model_config` slot dict, ``""`` when it names no model."""
    if not isinstance(slot, dict):
        return ""
    provider, name = str(slot.get("provider") or "").strip(), str(slot.get("name") or "").strip()
    return f"{provider}:{name}".lower() if provider and name else ""


def _kame_main_model_verdict(agent) -> bool:
    """1.8.3.0 (D1): does this chat run the MAIN model - the chat slot of the preset Agent Zero has
    configured for the chat's scope - and not a per-chat override or another preset?

    Read from Agent Zero's own resolver (`plugins._model_config.helpers.model_config`, v2.12 and
    later), never from its files. A seam that is absent, fails or names no model counts as "yes":
    the 1.8.2.9 behaviour, never a silent loss of the fallback list."""
    import importlib
    mc = importlib.import_module("plugins._model_config.helpers.model_config")
    if hasattr(mc, "get_config"):
        main = _kame_slot_identity((mc.get_config(agent) or {}).get("chat_model"))
    else:
        preset = mc.resolve_preset(mc.get_configured_preset_name(agent))
        main = _kame_slot_identity((preset or {}).get("chat"))
    effective = _kame_slot_identity(mc.get_chat_model_config(agent))
    if not main or not effective:
        return True
    return _kame_names_match(main, effective)


def _kame_reply_on_main(ctx) -> bool:
    """1.8.3.0 (D1): the fallback list may answer only a reply that STARTED on the main model.
    Asked once per reply (kept on ``ctx``); a walk down the list inside that reply is not asked
    again. A background job is not a reply of the conversation: it keeps its own rules."""
    if not _kame_main(ctx) or not _KAME_FALLBACK_MODELS:
        return True     # no list: nothing to guard, and Agent Zero's settings are not read for nothing
    known = ctx.get("on_main")
    if known is not None:
        return bool(known)
    verdict = True
    try:
        agent = _kame_frame_agent()
        if agent is not None:
            verdict = _kame_main_model_verdict(agent)
    except Exception:
        verdict = True
    ctx["on_main"] = verdict
    return verdict


def _kame_can_switch(ctx) -> bool:
    return bool(_KAME_FALLBACK_MODELS) and _kame_switch_on() \
        and _kame_main(ctx) and not _KAME_CAROUSEL_DISABLED \
        and _kame_reply_on_main(ctx) \
        and _kame_nothing_shown(ctx) \
        and ctx.get("entry_name")


_KAME_NO_SWITCH = object()


def _kame_walk_arm(ctx, tried_any=None) -> None:
    """The 1.8.2.8 delay before the list is walked again (30 s after a walk that asked a
    model, 5 s after one that asked none). 1.8.2.9: only for a walk that did NOT end
    because every model is busy or resting - that wait is the probe clock's."""
    if tried_any is None:
        tried_any = bool(ctx.pop("circle_tried", False))
    ctx.pop("circle", None)
    ctx["fallback_next_walk"] = time.monotonic() + (_KAME_FALLBACK_RETRY_S if tried_any else 5.0)


async def _kame_switch_model(owner, ctx, reason, cause=None, aux=None):
    """Answer this call with the next model of the list, or ``_KAME_NO_SWITCH``.

    Inside a call that already runs on a list model, raises ``_KameSwitchModel``
    so the outer call moves on to the entry after it.

    1.8.2.9 (E1): a model known busy is not built, asked or counted unless it is due another
    look and this call wins the claim (the request IS the probe). A walk that ends only
    because every model is busy, resting or just failed sets ``ctx["circle"]`` instead of
    arming the 30 s delay: the carousel then waits on the probe clock. ``aux`` (E7) is a
    background job that stopped: it walks the list once with what is left of its budget.
    """
    ctx.pop("circle", None)
    ctx.pop("circle_tried", None)
    if _KAME_FALLBACK_HOP.get() is not None:
        if aux is None and _kame_main(ctx) and _kame_nothing_shown(ctx):
            raise _KameSwitchModel(reason, cause)
        return _KAME_NO_SWITCH
    if aux is None:
        if not _kame_can_switch(ctx):
            return _KAME_NO_SWITCH
    elif not (_KAME_FALLBACK_MODELS and _kame_switch_on() and not _KAME_CAROUSEL_DISABLED
              and ctx.get("entry_name")):
        return _KAME_NO_SWITCH
    now_m = time.monotonic()
    if aux is None and now_m < ctx.get("fallback_next_walk", 0.0):
        return _KAME_NO_SWITCH
    origin = ctx["identity"]
    tried_any = False
    blocked = False     # busy, resting or just failed: waiting on the probe clock helps
    other = False       # could not be built, or an ordinary error: the 1.8.2.8 delay applies
    for entry in list(_KAME_FALLBACK_MODELS):
        ident = entry["identity"]
        now_m = time.monotonic()
        now_t = time.time()
        if _kame_skip_left(ident, now_m) > 0:
            blocked = True
            continue
        if not _kame_probe_due(ident, now_t):
            blocked = True      # known busy and asked a moment ago: no request, no model built
            continue
        try:
            model = _kame_fallback_model(owner, entry)
        except Exception as exc:
            _kame_event("surfaced", ident, reason="fallback model could not be built: skipped",
                        detail=f"{type(exc).__name__}: {exc}")
            _KAME_FALLBACK_SKIP[ident] = now_m + 300.0
            other = True
            continue
        ident = _kame_identity_of(model)
        if ident == origin:
            continue
        if _kame_skip_left(ident, now_m) > 0:
            blocked = True
            continue
        try:
            keys = _get_all_api_keys(model)
        except Exception:
            keys = []
        if not _kame_entry_usable(ident, keys):
            blocked = True
            continue
        method = getattr(model, ctx["entry_name"], None)
        if method is None:
            continue
        probe = False
        if _kame_busy_state(ident, now_t)[0]:
            if not _kame_claim_probe(ident, now_t):
                blocked = True   # another conversation took this model's ask
                continue
            probe = True
            _kame_ledger("circle")
        tried_any = True
        kwargs = dict(ctx.get("kwargs") or {})
        # The previous answer's provider-side state belongs to the configured
        # model; the fallback model reads the whole conversation instead.
        kwargs.pop("previous_response_id", None)
        kwargs.pop("responses_input_items", None)
        if aux is None:
            _kame_event("model_switch", origin, reason=f"{reason}: answering with {entry['label']}")
        hop = {"origin": origin, "to": ident, "probe": probe}
        if aux is not None:
            hop["aux"] = dict(aux)
        token = _KAME_FALLBACK_HOP.set(hop)
        try:
            result = await method(
                system_message="", user_message="", messages=list(ctx["messages"]),
                response_callback=ctx.get("response_callback"),
                reasoning_callback=ctx.get("reasoning_callback"),
                tokens_callback=ctx.get("tokens_callback"),
                rate_limiter_callback=ctx.get("rate_limiter_callback"),
                **kwargs)
        except _KameSwitchModel as exc:
            blocked = True
            # A model now known busy is on the probe clock; any other reason keeps the 30 s skip.
            if aux is None and not _kame_busy_state(ident, time.time())[0]:
                _KAME_FALLBACK_SKIP[ident] = time.monotonic() + _KAME_FALLBACK_RETRY_S
            _kame_event("model_switch", ident, reason=f"{entry['label']} cannot answer either ({exc.reason}): next")
            continue
        except Exception as exc:
            if _KAME_PASSTHROUGH_EXC and isinstance(exc, _KAME_PASSTHROUGH_EXC):
                raise
            if (ctx.get("progress") or {}).get("any") or (ctx.get("progress") or {}).get("reasoning"):
                raise  # it started answering, then failed: that error is the answer
            if aux is None:
                _KAME_FALLBACK_SKIP[ident] = time.monotonic() + _KAME_FALLBACK_RETRY_S
            other = True
            _kame_event("surfaced", ident, reason=f"{entry['label']} failed: next model",
                        code=_evidence_status(exc), detail=str(exc))
            continue
        finally:
            _KAME_FALLBACK_HOP.reset(token)
        if aux is not None:
            _kame_event("model_switch", origin, reason=f"{reason}: answered by {entry['label']}")
            return result
        with _KAME_LOCK:
            _KAME_SPEED_STATS["model_switches"] += 1
            _KAME_LAST_MODEL_SWITCH.clear()
            _KAME_LAST_MODEL_SWITCH.update({"from": origin, "to": entry["label"], "reason": reason,
                                            "at": time.time()})
        if _lvl_normal():
            PrintStyle.warning(f"[KAME] Chat|{ctx.get('model_short', '')} ↪ answered by "
                               f"{entry['label']} ({reason})")
        return result
    if aux is not None:
        return _KAME_NO_SWITCH
    if blocked and not other:
        ctx["circle"] = True
        ctx["circle_tried"] = tried_any
    else:
        _kame_walk_arm(ctx, tried_any)
    return _KAME_NO_SWITCH


def _kame_start_probe_allowed(ctx, identity) -> bool:
    """1.8.2.9 (E2): at the start of a conversation reply, a busy best model that is due another
    look is asked on its best key AT THE SAME TIME as the first free model of the list: whoever
    speaks first is shown. True only when everything below holds; then the ask is claimed (one
    per busy model per 20 s across every conversation) and the race is prepared on ``ctx``."""
    try:
        if ctx.get("call_type") != "Chat" or not _kame_main(ctx) or not _kame_nothing_shown(ctx):
            return False
        if _kame_hedge_mode() == "off" or _KAME_MODEL_SWITCH == "never" or _KAME_CAROUSEL_DISABLED:
            return False
        if not _KAME_FALLBACK_MODELS or _KAME_FALLBACK_HOP.get() is not None or not _kame_cross_allowed(ctx):
            return False
        now = time.time()
        if not _kame_busy_state(identity, now)[0] or not _kame_probe_due(identity, now):
            return False
        reasoning = _kame_reasoning_call(ctx)
        slow = _kame_slow_number(ctx, identity, reasoning) or 30.0
        first = _kame_cross_entry(ctx, identity, reasoning, slow)
        if not first:
            return False
        entry = first[0][0]
        if not _kame_claim_probe(identity, now):
            return False
        entry_busy = _kame_busy_state(entry["identity"], now)[0]
        if entry_busy and not _kame_claim_probe(entry["identity"], now):
            return False
        ctx["probe_primary"] = True
        ctx["probe_cross"] = [(entry, 0.0)]
        ctx["probe_cross_claimed"] = bool(entry_busy)
        return True
    except Exception:
        return False


def _kame_claim_entry(entry, now=None):
    """``False`` when the list model is not busy (just ask it), ``True`` when it is busy and this
    call won the claim (the request is the probe), ``None`` when another conversation took it."""
    now = time.time() if now is None else now
    if not _kame_busy_state(entry["identity"], now)[0]:
        return False
    return True if _kame_claim_probe(entry["identity"], now) else None


def _kame_free_entry_outside(ctx, identity, race, now=None):
    """The first list model that could still answer and is not in this race, or None."""
    now = time.time() if now is None else now
    now_m = time.monotonic()
    hop = _KAME_FALLBACK_HOP.get()
    taken = [e for e in race.cross_legs.values()]
    for entry in _KAME_FALLBACK_MODELS:
        if _kame_names_match(entry["identity"], identity):
            continue
        if any(_kame_names_match(entry["identity"], t["identity"]) for t in taken):
            continue
        if _kame_skip_left(entry["identity"], now_m) > 0:
            continue
        if hop and entry["identity"] == hop.get("origin"):
            continue
        if not _kame_entry_free(entry["identity"], None, now):
            continue
        return entry
    return None


async def _kame_circle_step(owner, ctx, identity, all_keys, last_exc=None, last_kind=""):
    """1.8.2.9 (E1): every model of ``[best] + the list`` is busy or resting. Instead of asking the
    best model's next key at once (dozens of asks a minute), wait on the probe clock, say so on the
    status line, then ask the best model first - one request - and the list after it. Returns a
    result when a list model answered, or ``None`` when the caller should ask the best model now."""
    since = ctx.setdefault("circle_since", time.time())
    while True:
        if _KAME_MAX_TOTAL_WAIT_S > 0 and time.time() - since >= _KAME_MAX_TOTAL_WAIT_S:
            _kame_status_line(ctx, None)
            _kame_event("surfaced", identity,
                        reason=f"stopped waiting after {_fmt_duration(_KAME_MAX_TOTAL_WAIT_S)} "
                               "(max_total_wait_seconds)")
            if last_exc is not None:
                raise _kame_with_retry_hint(last_exc, last_kind)
            raise RuntimeError(
                f"KAME: every model is busy; stopped waiting after "
                f"{_fmt_duration(_KAME_MAX_TOTAL_WAIT_S)} (max_total_wait_seconds). "
                "They come back on their own; retrying later will very likely succeed.")
        crossing = _kame_cross_allowed(ctx)
        began = time.monotonic()
        waited = 0.0
        due, label = _kame_circle_due_in(ctx, primary_only=not crossing)
        try:
            while due > 0 and waited < _KAME_PROBE_EVERY_S:
                _kame_flight_wait(ctx, since, due)
                _kame_status_line(ctx, f"waiting on {label} - busy, asked again {_kame_in_words(due)}")
                piece = min(1.0, due + 0.05)
                await asyncio.sleep(piece)
                waited += piece
                await _kame_honor_intervention()
                due, label = _kame_circle_due_in(ctx, primary_only=not crossing)
        except BaseException:
            # review M1: a stop or a new message in the middle of the wait gives Agent Zero's words back
            _kame_status_line(ctx, None)
            raise
        _kame_status_line(ctx, None)
        # The best model first: one ask, one key.
        if _kame_claim_probe(identity):
            ctx["circle_probe"] = True
            _kame_ledger("circle")
            ctx.pop("circle", None)
            ctx.pop("circle_tried", None)
            return None
        if crossing:
            switched = await _kame_switch_model(owner, ctx, "your provider is busy", last_exc)
            if switched is not _KAME_NO_SWITCH:
                return switched
            if not ctx.get("circle"):
                return None        # the walk armed its own delay: the best model's keys go on
        if time.monotonic() - began < 1.0:
            await asyncio.sleep(1.0)   # nothing was askable and no time passed: never spin
            await _kame_honor_intervention()


# --- v1.8.2.9 (E7): background jobs get a budget ----------------------------------------

class KameAuxTimeout(TimeoutError):
    """A background job (chat naming, memory, compression ...) used up its budget. Agent Zero
    catches every such call and goes on without it; the keys are kept for the chat."""


_KAB = _kame_optional("kame_aux")
_KAME_AUX_UNBOUNDED = False            # KAME_AUX_UNBOUNDED=1 brings 1.8.2.8 back
_KAME_AUX_MEMO = {}                    # (identity, class) -> wall time until which a stopped job answers "stopped"
_KAME_AUX_ROUND_S = 10.0               # an important job starts a new pass over the keys at most this often
_KAME_AUX_STOPPED = collections.Counter()   # task -> jobs stopped, for the readout


def set_aux_unbounded(raw) -> None:
    """KAME_AUX_UNBOUNDED=1 (environment): background calls wait like the chat, as in 1.8.2.8."""
    global _KAME_AUX_UNBOUNDED
    flag = _kame_flag(raw)
    if flag is not None:
        _KAME_AUX_UNBOUNDED = flag


#: Agent Zero's own jobs by the function that makes the call (nearest frame wins).
_KAME_AUX_FRAMES = {
    "generate_name": "title_generation", "change_name": "title_generation",
    "search_memories": "memory_recall",
    "_extract_search_keywords": "memory_consolidation",
    "_analyze_memory_consolidation": "memory_consolidation",
    "summarize_messages": "compression", "summarize": "compression",
    "_compact_single_pass": "compression", "_compact_large_history": "compression",
    "_kame_summarize_messages": "compression", "_kame_bulk_summarize": "compression",
    "document_qa": "document_query", "_answer_questions_from_content": "document_query",
    "_run_check": "infection_check",
}
#: `memorize` exists in two extensions; the module that holds it says which job it is.
_KAME_AUX_MEMORIZE = {"_50_memorize_fragments": "memorize_fragments",
                      "_51_memorize_solutions": "memorize_solutions"}


def _kame_aux_task():
    """``(task, known)`` of the background call running now, read from the call stack the same way
    `_kame_is_main_chat` reads it. A job nobody recognises is ``("utility", False)``: it is NOT
    budgeted (review H2) - only the frames of the table were read and are known to catch the
    stop; an agent tool, a mail handler or a third-party plugin may not, and would end a turn."""
    try:
        import sys as _sys
        frame = _sys._getframe(1)
    except Exception:
        return "utility", False
    depth = 0
    while frame is not None and depth < 80:
        name = frame.f_code.co_name
        task = _KAME_AUX_FRAMES.get(name)
        if task:
            return task, True
        if name == "memorize":
            where = f"{frame.f_globals.get('__name__', '')} {frame.f_code.co_filename}"
            for stem, job in _KAME_AUX_MEMORIZE.items():
                if stem in where:
                    return job, True
        frame = frame.f_back
        depth += 1
    if "Compress" in (_KAME_CALL_CONTEXT.get() or ""):
        return "compression", True
    return "utility", False


class _KameAux:
    """What one background call has spent: requests and seconds."""

    def __init__(self, budget, task, known):
        self.budget, self.task, self.known = budget, task, known
        self.started = time.monotonic()
        self.requests = 0           # the requests that count toward a cosmetic job's cap
        self.sent = 0               # the requests really sent, for the words (review N2)
        self.round_at = self.started    # when the job's latest pass over the keys began (review N6)
        self.paced = False
        self.busy_last = False      # review H1: the last attempt ended on the provider's "busy"

    def elapsed(self) -> float:
        return time.monotonic() - self.started

    def remaining(self) -> float:
        return max(0.0, self.budget.total_s - self.elapsed())

    def spent(self) -> bool:
        # review N6: an important job is bounded by TIME (total_s), not by a count of requests or of
        # waits; only chat naming keeps its few requests
        capped = self.budget.klass == "cosmetic" and self.requests >= self.budget.max_requests
        return capped or self.elapsed() >= self.budget.total_s


def _kame_aux_begin(ctx):
    """The budget of this call, or ``None`` for the chat, for ``KAME_AUX_UNBOUNDED``, when the
    module is missing and for a job the stack does not name (review H2). Sets ``ctx["aux_task"]``
    and ``ctx["aux_known"]`` for every background call, and ``ctx["aux"]`` for a budgeted one."""
    if _KAME_AUX_UNBOUNDED or _KAB is None or _KAME_CAROUSEL_DISABLED or _kame_main(ctx):
        return None
    task, known = _kame_aux_task()
    ctx["aux_task"], ctx["aux_known"] = task, known
    if known:
        _KAME_TASK.set(task)
        if isinstance(ctx.get("flight"), dict):
            ctx["flight"]["task"] = task
    if not known:
        return None     # review H2: a job nobody vetted keeps the 1.8.2.8 wait (no budget, still no backups)
    budget = _KAB.budget_for(task, known, None, _KAME_MAX_TOTAL_WAIT_S)
    hop = _KAME_FALLBACK_HOP.get()
    carried = hop.get("aux") if hop else None
    if carried:     # a stopped job asking the list: what is left of the same budget
        budget = _KAB.Budget(budget.klass, max(_KAB.FLOOR_S, float(carried["deadline"]) - time.monotonic()),
                             max(1, int(carried["requests"])), budget.may_wait)
    aux = _KameAux(budget, task, known)
    ctx["aux"] = budget
    return aux


async def _kame_aux_stop(owner, ctx, aux, identity, memo=True, walk=True, rest=False):
    """A background job is over budget. An important one goes down KAME's own list once with what
    is left of its budget (the summary matters more than the wait); a cosmetic one never does.
    Otherwise say so and raise `KameAuxTimeout`. The stop is remembered for 30 s (the next call of the
    same class is turned away without a request). An important job writes that memo only when the stop
    found every key resting past what was left of its budget (``rest``, review N1/H1): a busy provider
    or a spent request count says nothing about the keys, and the next important job must be asked."""
    hop = _KAME_FALLBACK_HOP.get()
    if hop is not None and hop.get("aux"):
        raise _KameSwitchModel("background job budget spent")
    klass = aux.budget.klass
    if walk and klass == "important" and aux.budget.may_wait:
        switched = await _kame_switch_model(
            owner, ctx, "background job stopped", None,
            aux={"deadline": time.monotonic() + max(_KAB.FLOOR_S, aux.remaining()),
                 "requests": max(1, aux.budget.max_requests - aux.requests)})
        if switched is not _KAME_NO_SWITCH:
            return switched
    elapsed = aux.elapsed()
    if memo:
        if rest or klass != "important":
            _KAME_AUX_MEMO[(identity, klass)] = time.time() + _KAB.MEMO_S
        with _KAME_LOCK:
            _KAME_AUX_STOPPED[aux.task] += 1
        _kame_event("gave_up", identity, reason=(
            f"background job {aux.task} stopped after {aux.sent} tries in {int(elapsed)}s - "
            "its keys are kept for your chat"))
    raise KameAuxTimeout(_KAB.stop_message(aux.task, _kame_model_label(identity), aux.sent, elapsed))


# --- v1.8.2.9 (E8): where the quota went -------------------------------------------------

_KAME_REQUESTS = {}          # hour -> {identity: [main, side]}
_KAME_REQUEST_TASKS = {}     # hour -> {(identity, task): n}   (background requests only)


def _kame_count_request(ctx, identity=None, now=None) -> None:
    """One request sent to a model, counted as the chat's (`main`) or a background job's (`side`).
    Counts only; never a key, a prompt or an answer. Never raises."""
    try:
        now = time.time() if now is None else now
        identity = identity or ctx.get("identity") or ""
        main = _kame_main(ctx)
        task = ctx.get("aux_task") or ("chat" if main else "utility")
        hour = int(now // 3600)
        with _KAME_LOCK:
            row = _KAME_REQUESTS.setdefault(hour, {}).setdefault(identity, [0, 0])
            row[0 if main else 1] += 1
            if not main:
                tasks = _KAME_REQUEST_TASKS.setdefault(hour, {})
                tasks[(identity, task)] = tasks.get((identity, task), 0) + 1
            for table in (_KAME_REQUESTS, _KAME_REQUEST_TASKS):
                for old in [h for h in table if h < hour - 24]:
                    table.pop(old, None)
    except Exception:
        pass


def quota_use_view(now=None) -> dict:
    """The last 24 hours of requests per model: ``{since, models: {identity: {main, side, tasks}},
    stopped, suggestion}`` (the Hermes `quota_use` shape)."""
    now = time.time() if now is None else now
    horizon = now - 24 * 3600.0
    models = {}
    with _KAME_LOCK:
        for hour, rows in _KAME_REQUESTS.items():
            if (hour + 1) * 3600 <= horizon:
                continue
            for identity, (main, side) in rows.items():
                row = models.setdefault(identity, {"main": 0, "side": 0, "tasks": {}})
                row["main"] += main
                row["side"] += side
        for hour, rows in _KAME_REQUEST_TASKS.items():
            if (hour + 1) * 3600 <= horizon:
                continue
            for (identity, task), n in rows.items():
                row = models.setdefault(identity, {"main": 0, "side": 0, "tasks": {}})
                row["tasks"][task] = row["tasks"].get(task, 0) + n
        stopped = {task: n for task, n in _KAME_AUX_STOPPED.items() if n}
    since = max(_KAME_STARTED_AT, horizon)
    return {"since": since, "models": models, "stopped": stopped,
            "suggestion": _kame_quota_suggestion(models, since)}


def _kame_quota_suggestion(models, since):
    """One read-only sentence, or ``None`` (see `kame_aux.suggest`). The best model is the one that
    has both chat and background requests; the model named is always one of the owner's own list."""
    try:
        if _KAB is None or not _KAME_FALLBACK_MODELS:
            return None
        ranked = sorted((row["side"], ident) for ident, row in models.items()
                        if row.get("main") and row.get("side"))
        if not ranked:
            return None
        primary = ranked[-1][1]
        chain = [e["identity"] for e in _KAME_FALLBACK_MODELS]
        return _KAB.suggest(models, primary, chain, lambda ident: _kame_model_label(ident), since)
    except Exception:
        return None


#: v1.8.2.7 (Hermes 1.8.2.6 "still waiting"): every call KAME is running, so
#: the panel can say which job has been waiting, on which model, for how long,
#: and after how many refusals. Counts and KAME's own words only.
_KAME_IN_FLIGHT = {}
_KAME_IN_FLIGHT_SHOW_AFTER_S = 60.0


def _kame_in_flight_rows(now=None, min_age=_KAME_IN_FLIGHT_SHOW_AFTER_S) -> list:
    now = time.time() if now is None else now
    rows = []
    with _KAME_LOCK:
        flights = [dict(v) for v in _KAME_IN_FLIGHT.values()]
    for f in flights:
        age = now - float(f.get("started") or now)
        if age >= min_age:
            f["seconds"] = round(age, 1)
            rows.append(f)
    rows.sort(key=lambda r: -r["seconds"])
    return rows[:20]


def speed_report() -> dict:
    """For the panel: backups, bursts, model switches and the last switch."""
    with _KAME_LOCK:
        out = dict(_KAME_SPEED_STATS)
        out["last_model_switch"] = dict(_KAME_LAST_MODEL_SWITCH)
    out["hedge_mode"] = _KAME_HEDGE_MODE
    out["fallback_models"] = [dict(e) for e in _KAME_FALLBACK_MODELS]
    out["failover_after_seconds"] = _KAME_FAILOVER_AFTER_S
    # 1.8.2.9 (Hermes `_speed_view`): the mode in force, what it may spend, where Faster is
    # stepped back, and what the last hour really spent.
    mode = _kame_hedge_mode()
    out["mode"] = mode
    out["cap_pct"] = 0
    out["paused_until"] = {}
    out["hour"] = {"replies": 0, "backup": 0, "race": 0, "burst": 0, "circle": 0, "extra_pct": None}
    out["since_start"] = dict(out["hour"])
    if _KH is not None:
        try:
            balanced, faster = _KH.speed_multipliers(_KH.SETTINGS.get("hedge_budget_pct", 10.0))
            out["cap_pct"] = {"off": 0, "adaptive": round((balanced - 1.0) * 100),
                              "max": round((faster - 1.0) * 100)}.get(mode, 0)
            out["paused_until"] = _KH.HEDGE_TRACKER.faster_paused_until()
            out.update(_KH.SPEED_LEDGER.view())
        except Exception:
            pass
    return out


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
    st.ctx = ctx                    # 1.8.2.9: the status line belongs to this call
    _empty_counts = {}              # v1.0.6: per-key empty-answer count this call
    _empty_budget = _KAME_EMPTY_RETRY_BUDGET

    attempt_no = 0
    _call_id = secrets.token_hex(8)   # collision-resistant id tying calls.jsonl rows together
    _no_clock_fix = set()   # v1.8.1.2: keys whose refusal this call no wait can fix
    _last_failed_key = None
    # v1.8.2.7: what the fallback list, the burst and the wait bound read.
    _last_exc = None        # the last refusal, for "stop waiting" and the fallback walk
    _last_kind = ""
    _timeouts = 0           # timeouts in a row on this call
    _long_rests = 0         # refusals in a row that rested a key a minute or more
    _burst_round = 0
    _denied = {}            # key -> status, for keys that refused this model on this call
    _busy_keys = set()      # v1.8.2.8: keys that failed on the provider's side (busy)
    _busy_noted = [False]   # the busy window opened once per call
    _aux_walked = False     # 1.8.2.9 review H1: an important job tried the list once while the model was busy

    def _absorb_race(skip=None):
        """The refusals a race judged on its own legs feed the 'every key refused'
        rules and the in-flight counter, whatever the attempt ended with (an
        error, or an empty answer: third review P1/P4)."""
        judged = ctx.pop("race_judged", None) or []
        for _rk, _rkind, _rsc, _rexc in judged:
            if _rk == skip:
                continue
            if _rkind == "denied":
                _denied[_rk] = _rsc
            else:
                _denied.pop(_rk, None)
            if _rkind == "insufficient_quota" and _no_clock_fixes(_rexc):
                _no_clock_fix.add(_rk)
            else:
                _no_clock_fix.discard(_rk)
            # v1.8.2.8 (Hermes, live 2026-10-17): a raced leg's 503 is the
            # provider's answer too; a busy model moves after the first round.
            if _kame_busy_failure(identity, _rk, _rkind, _rsc):
                _busy_keys.add(_rk)
        if len(_busy_keys) >= 2 and not _busy_noted[0] and not ctx.get("probe_noted"):
            _busy_noted[0] = True
            _kame_note_busy(identity)
        _fl = ctx.get("flight")
        if judged and isinstance(_fl, dict):
            _fl["refusals"] = int(_fl.get("refusals") or 0) + len([j for j in judged if j[0] != skip])

    # 1.8.2.9 (E7): a background call (title, memory, compression ...) has a budget; the chat has none.
    _aux = _kame_aux_begin(ctx)
    if _aux is not None:
        _memo_until = _KAME_AUX_MEMO.get((identity, _aux.budget.klass), 0.0)
        if _memo_until > time.time() and _KAME_FALLBACK_HOP.get() is None:
            # the host's own retry of a job that was just stopped costs no request
            await _kame_aux_stop(self, ctx, _aux, identity, memo=False, walk=False)
        _gone = (not _pool_has_ready_key(identity, all_keys)) and _next_recovery_seconds(identity, all_keys) is not None
        _back_in = _next_recovery_seconds(identity, all_keys) if _gone else 0.0
        if _gone and (not _aux.budget.may_wait or (_back_in or 0.0) > _aux.remaining()):
            _stopped = await _kame_aux_stop(self, ctx, _aux, identity, rest=True)
            return _stopped

    # v1.8.2.8 (owner, live test on Hermes): every reply starts on the best
    # model; one KAME still knows busy (inside its busy window) is passed down
    # the list at once, without spending a request on it.
    # 1.8.2.9 (E2): when the best model is due another look it is asked beside the first free
    # model of the list, whoever speaks first is shown; (E1) a list call that IS a probe goes on.
    _hop_now = _KAME_FALLBACK_HOP.get()
    if (_KAME_MODEL_SWITCH != "never" and time.time() < _KAME_BUSY_UNTIL.get(identity, 0.0)
            and not (_hop_now and _hop_now.get("probe"))):
        if not _kame_start_probe_allowed(ctx, identity):
            _switched = await _kame_switch_model(self, ctx, "your provider is busy", None)
            if _switched is not _KAME_NO_SWITCH:
                return _switched
            if ctx.get("circle"):
                _switched = await _kame_circle_step(self, ctx, identity, all_keys)
                if _switched is not None:
                    return _switched

    while True:  # ETERNAL CAROUSEL - all call types use the same robust rotation
        attempt_no += 1
        if attempt_no > 1:
            for _flag in ("probe_primary", "probe_cross", "probe_noted"):
                ctx.pop(_flag, None)             # the start probe is the first attempt only
        if _aux is not None and _aux.spent():
            return await _kame_aux_stop(self, ctx, _aux, identity)

        # v1.8.2.7: a call running on a model of the fallback list is bounded.
        # Without this, a storm of short refusals there (busy server, 1 s each)
        # rotated for ever and the configured model was never tried again in
        # this call (review 2026-10-17, M1). Back to the outer call, which tries
        # the next entry or waits for the configured model.
        if (_KAME_FALLBACK_HOP.get() is not None and _kame_main(ctx) and _kame_nothing_shown(ctx)
                and time.perf_counter() - _call_started_at > max(_kame_switch_threshold(identity, ctx),
                                                                 _KAME_HOP_BOUND_MIN_S)):
            _kame_wait_notice_finish(st, "stopped")
            raise _KameSwitchModel("no answer in time on this fallback model", _last_exc)

        # v1.0.2: once past the clean first attempt (i.e. we are rotating or
        # cooling), honor a queued user message / "nudge" so KAME never sleeps
        # through an intervention. The happy path (first attempt) is untouched.
        if attempt_no > 1:
            await _kame_honor_intervention()

        _select_t0 = time.perf_counter()
        key, status = _get_best_key(identity, all_keys)
        _select_ms = (time.perf_counter() - _select_t0) * 1000.0

        if status == "EXHAUSTED_RETRY" and not _KAME_CAROUSEL_DISABLED:
            # v1.8.2.7: every key resting longer than `failover_after_seconds`
            # (or resting that long already): the next model of KAME's list
            # answers, if the list has one that can.
            # v1.8.2.8: the clock is the user's wait (a 503 rests a key 1 s,
            # so "every key resting long" never came in an overload storm).
            _eta_fo = _next_recovery_seconds(identity, all_keys)
            _thr = _kame_switch_threshold(identity, ctx)
            _waited = time.perf_counter() - _call_started_at
            _busy_now = _KAME_MODEL_SWITCH != "never" and _kame_is_busy(identity, _busy_keys)
            if _aux is not None:
                # 1.8.2.9 (E7): a background job waits for a key only when that fits its budget.
                if (not _aux.budget.may_wait) or (_eta_fo or 0.0) > _aux.remaining():
                    return await _kame_aux_stop(self, ctx, _aux, identity, rest=True)
                if not _aux.paced and not _aux.busy_last:     # busy has its own cadence (N3)
                    # review N6: a new pass over the keys does not begin sooner than _KAME_AUX_ROUND_S
                    # after the last one began (the job waits, it does not stop)
                    _aux.paced = True
                    _gap = _KAME_AUX_ROUND_S - (time.monotonic() - _aux.round_at)
                    if _gap > 0:
                        if _gap >= _aux.remaining():
                            return await _kame_aux_stop(self, ctx, _aux, identity)
                        await asyncio.sleep(_gap)
            if _thr > 0 and (_busy_now or _eta_fo is None or _eta_fo > _thr or _waited + (_eta_fo or 0) > _thr):
                _switched = await _kame_switch_model(
                    self, ctx, "your provider is busy" if _busy_now else "every key resting", _last_exc)
                if _switched is not _KAME_NO_SWITCH:
                    _kame_wait_notice_finish(st, "resumed")
                    return _switched
                if ctx.get("circle"):
                    if _busy_now:
                        # 1.8.2.9 (E1): every model is busy: wait on the probe clock, not on the keys
                        _switched = await _kame_circle_step(self, ctx, identity, all_keys, _last_exc, _last_kind)
                        if _switched is not None:
                            _kame_wait_notice_finish(st, "resumed")
                            return _switched
                        continue
                    _kame_walk_arm(ctx)
            # v1.8.2.7: the bound the user set on waiting (0 = wait for ever).
            if _KAME_MAX_TOTAL_WAIT_S > 0 and st.cold_since and \
                    time.time() - st.cold_since >= _KAME_MAX_TOTAL_WAIT_S:
                _kame_wait_notice_finish(st, "stopped")
                _kame_event("surfaced", identity,
                            reason=f"stopped waiting after {_fmt_duration(_KAME_MAX_TOTAL_WAIT_S)} "
                                   "(max_total_wait_seconds)")
                if _last_exc is not None:
                    raise _kame_with_retry_hint(_last_exc, _last_kind)
                raise RuntimeError(
                    f"KAME: every key of {identity} is resting; stopped waiting after "
                    f"{_fmt_duration(_KAME_MAX_TOTAL_WAIT_S)} (max_total_wait_seconds). "
                    "The keys come back on their own; retrying later will very likely succeed.")
            if st.sleep_count == 0:
                _eta_now = _next_recovery_seconds(identity, all_keys)
                _kame_event("wait", identity,
                            reason="every key resting" + (
                                f", next back in ~{_fmt_duration(_eta_now)}" if _eta_now else ""),
                            seconds=_eta_now)
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
        # 1.8.2.9 (E10): a key is ready - the wait is over, Agent Zero's own words come back
        # now, not when the first text arrives; and the page learns which key this call uses.
        _kame_status_release(st)
        if ctx.pop("race_line", None):
            _kame_status_line(ctx, None)
        _fl_now = ctx.get("flight")
        if isinstance(_fl_now, dict):
            _fl_now["attempt"] = attempt_no
            _fl_now["key"] = _key_short_id(key)
        if _last_failed_key is not None and key != _last_failed_key:
            # v1.8.1.0: the half of the story the timeline used to miss —
            # which key took over.
            _kame_event("switch", identity, key, reason="took over")
        if status != "EXHAUSTED_RETRY" and _lvl_verbose():
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
        if ctx["progress"].get("any") or ctx["progress"].get("reasoning"):
            ctx["shown"] = True   # v1.8.2.7: never switch model after anything reached the screen
        ctx["progress"]["any"] = False
        ctx["progress"]["reasoning"] = False
        ctx["progress"]["first_sign_at"] = None
        ctx["progress"]["first_text_at"] = None

        # v1.8.2.7: two long rests in a row means the pool is running dry: ask
        # 4, then 8, then 16 ready keys at once (chat only; off with
        # hedge_mode off). First answer wins; the rest are cancelled.
        _burst = []
        if (_long_rests >= 2 and _kame_main(ctx) and _KAME_PROBE_BURST_MAX > 0
                and _kame_hedge_mode() != "off" and not _KAME_CAROUSEL_DISABLED
                and _kame_burst_allowed(identity, all_keys, ctx)):
            _size = min(_KAME_BURST_SIZES[min(_burst_round, len(_KAME_BURST_SIZES) - 1)],
                        _KAME_PROBE_BURST_MAX)
            _burst = _kame_pick_more(identity, all_keys, {key}, _size - 1)
            if _burst:
                _burst_round += 1

        _attempt_t0 = time.perf_counter()
        _attempt_wall = time.time()
        _circle_probe = bool(ctx.pop("circle_probe", False))   # 1.8.2.9: this attempt IS the probe
        if _aux is not None:
            _aux.requests += 1       # 1.8.2.9 (E7): counts toward a cosmetic job's cap only
            _aux.sent += 1
            if _aux.paced:           # a new pass over the keys begins (review N6)
                _aux.paced = False
                _aux.round_at = time.monotonic()
            _aux.busy_last = False
        _absorb_race()   # nothing judged before this attempt may be lost
        _kame_prepare_continuation(self, ctx, identity)
        try:
            result, key = await _kame_raced_attempt(
                self, ctx, key, burst_keys=_burst, attempt_no=attempt_no, call_id=_call_id,
                call_started_at=_call_started_at, st=st)

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
            if ctx.get("stitcher") is not None:
                # v1.8.2.7: a continued answer is the whole answer the user saw.
                result = await _kame_finish_continuation(ctx, result)
            if (_empty_budget > 0 and not ctx["progress"]["any"]
                    and _kame_result_is_empty(result)):
                _absorb_race()
                _empty_budget -= 1
                _empty_counts[key] = _empty_counts.get(key, 0) + 1
                _kame_timing(identity, key, attempt_no, "empty", "", None, _attempt_wall,
                             ctx, _call_started_at, st, None, "", _call_id)
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

            # review R4: when the next model won the race, its own call already
            # recorded the answer; nothing of it is this pool's key's.
            _by_next = bool(ctx.get("answered_by"))
            _kame_status_release(st)     # 1.8.2.9: a line KAME wrote (a race) is Agent Zero's again
            if not _by_next:
                _mark_key_health(identity, key, True)
                _kame_learn_key(identity, key, ctx, _attempt_wall)
            _kame_after_answer(identity, ctx, _call_started_at)
            if not _by_next:
                _kame_timing(identity, key, attempt_no, "ok", "", None, _attempt_wall,
                             ctx, _call_started_at, st, None, "", _call_id)
            if _last_failed_key is not None:
                _kame_event("recovery", identity, key,
                            reason=f"answered on attempt {attempt_no}")
            # v1.0.3: a success ends any error storm — close it and, in collapse
            # mode, print a one-line recap so the operator sees how big it was.
            # (_storm_end always pops the state; it returns a recap only for a
            # storm of >= _KAME_STORM_MIN_FOR_SUMMARY failures.)
            _storm_recap = _storm_end(identity) if not _by_next else None
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

        except asyncio.CancelledError:
            # v1.8.2.7 (second review N10): the user stopped the call; a waiting
            # notice from an earlier sleep must not stay on screen.
            _kame_wait_notice_finish(st, "stopped")
            raise
        except Exception as e:
            # Fix A (v1.0.1): A0 control-flow exceptions must propagate, never be
            # retried as a failed API call (restores native nudge handling). With
            # delegation these are raised by A0's own streaming callbacks, exactly
            # as they are without KAME installed.
            if _KAME_PASSTHROUGH_EXC and isinstance(e, _KAME_PASSTHROUGH_EXC):
                _kame_wait_notice_finish(st, "stopped")
                raise
            if isinstance(e, _KameSwitchModel):
                _kame_wait_notice_finish(st, "stopped")
                raise
            if isinstance(e, _KameSlowModel):
                # v1.8.2.8 (D11): inside a fallback model this raises on to the
                # outer call, which tries the entry after it.
                _switched = await _kame_switch_model(self, ctx, e.reason, None)
                if _switched is not _KAME_NO_SWITCH:
                    _kame_wait_notice_finish(st, "resumed")
                    return _switched
                if ctx.get("circle"):
                    _kame_walk_arm(ctx)   # a slow model is not the busy case: the 1.8.2.8 delay
                e = TimeoutError(f"KAME: {e.reason}")   # nothing could take it: a timeout
                _kame_tag(e, key, time.perf_counter() - _call_started_at)
            # v1.8.2.7 (Hermes stitching): a provider that will not continue
            # from the model's own turn says so in a 400. That is the shape of
            # the continuation, not the key and not the request: learn it once
            # and continue with a trailing user turn instead.
            if (ctx.get("stitcher") is not None and _KS is not None and identity not in _KAME_NO_PREFILL
                    and _KS.refuses_prefill(_evidence_text(e))):
                _KAME_NO_PREFILL.add(identity)
                _kame_event("recovery", identity, getattr(e, "_kame_leg_key", key),
                            reason="provider refuses a prefill: continuing with a user turn")
                await asyncio.sleep(0)
                continue
            # v1.8.2.7: with backups, the leg that failed last is the one judged.
            key = getattr(e, "_kame_leg_key", key)
            _leg_elapsed = getattr(e, "_kame_leg_elapsed", None)
            # Only a genuinely TERMINAL error surfaces — everything else (incl. a
            # transient mid-stream drop) is cooled + rotated, never re-raised
            # (the v1.0.4 eternal-carousel promise).
            if _is_terminal_error(e):
                _kame_wait_notice_finish(st, "stopped")
                _sc_t = _evidence_status(e)
                _kame_event("surfaced", identity, key, reason="the provider rejected the request itself; another key would get the same answer",
                            code=_sc_t, detail=str(e))
                _kame_timing(identity, key, attempt_no, "terminal", "", _sc_t, _attempt_wall,
                             ctx, _call_started_at, st, None, "", _call_id)
                if _KJ is not None:
                    _KJ.record_refusal(identity=identity, key=key, exc=e, status=_sc_t,
                                       kind="terminal")
                try:
                    _kame_with_retry_hint(e, _classify_error(e)[1])
                except Exception:
                    pass
                raise e
            # v1.6.0.4: "partial output" has to mean output. Until now this line
            # was printed for any turn where the model had merely thought, which
            # on a reasoning model is every turn, and it told the reader their
            # answer had been cut when nothing had been shown at all.
            if ctx["progress"]["any"]:
                _KAME_COUNTS["stream_drops"] += 1
                # v1.8.2.7: what the user saw is continued on the next key.
                ctx["stitch_seen"] = ctx["progress"].get("full_text") or ctx.get("stitch_seen", "")
            _prev_stitch = ctx.get("stitcher")
            if _prev_stitch is not None:
                # Hermes: "continues while the answer makes progress; no-progress
                # repetition still stops on evidence". A continuation that
                # streamed and added nothing, twice: start the answer again.
                if ctx["progress"].get("first_sign_at") and not _prev_stitch.forwarded:
                    ctx["resume_noprogress"] = int(ctx.get("resume_noprogress") or 0) + 1
                    if ctx["resume_noprogress"] >= 2:
                        ctx.pop("stitch_seen", None)
                elif _prev_stitch.forwarded:
                    ctx["resume_noprogress"] = 0
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
            # v1.8.1.0: one function decides, rests and names the rung — the
            # same one the tests and the answer-key gate drive.
            _pre = getattr(e, "_kame_judged", None)
            if _pre and _pre[0] != key:
                _pre = None   # the same exception object on another key: judge this one (third review P2)
            if _pre:
                # Judged inside the race (a leg's own verdict): not twice.
                _k0, applied, kind, sc, _rung, _sized_by = _pre
            else:
                applied, kind, sc, _rung, _sized_by = _kame_decide_failure(
                    identity, key, e,
                    elapsed=_leg_elapsed if _leg_elapsed is not None else time.perf_counter() - _attempt_t0
                )
                _tally_failure(identity, kind, sc, _sized_by)
            # Second review N1: keys refused inside a race count for the "every
            # key refused" rules exactly as keys refused one by one.
            _absorb_race(skip=key)
            _last_exc = e
            _flight = ctx.get("flight")
            if isinstance(_flight, dict):
                _flight["refusals"] = int(_flight.get("refusals") or 0) + 1
                _flight["last"] = _KAME_EVENT_REASON.get(kind, kind)
            _timeouts = _timeouts + 1 if kind == "timeout" else 0
            try:
                # A spent daily / account quota, not a per-minute counter: a
                # 65 s per-minute rest is pressure, and asking more keys at
                # once would deepen it (review 2026-10-17, H2).
                _long_rests = _long_rests + 1 if (float(applied or 0) >= _KAME_BURST_LONG_REST_S
                                                  and kind != "per_minute") else 0
            except (TypeError, ValueError):
                _long_rests = 0
            if kind == "denied":
                _denied[key] = sc
            else:
                _denied.pop(key, None)
            # v1.8.1.2 (owner decision, same rule as Hermes): a billing refusal
            # naming a missing entitlement -- `usage_not_included`, or Google's
            # FAILED_PRECONDITION "location not supported without billing" --
            # is fixed by no wait. Once every key of this call has said so, the
            # eternal carousel hands the error to Agent Zero instead of waiting
            # forever. Credit/spend exhaustion is untouched: time fixes it.
            if kind == "insufficient_quota" and _no_clock_fixes(e):
                _no_clock_fix.add(key)
            else:
                _no_clock_fix.discard(key)
            if not _pre:
                _kame_event(_kame_event_kind(kind), identity, key,
                            reason=_KAME_EVENT_REASON.get(kind, kind), code=sc,
                            seconds=applied, detail=str(e), sized_by=_rung or _sized_by)
                _kame_timing(identity, key, attempt_no, "refused", kind, sc, _attempt_wall,
                             ctx, _call_started_at, st, applied, _rung or _sized_by, _call_id)
                if _KJ is not None:
                    _KJ.record_refusal(identity=identity, key=key, exc=e, status=sc, kind=kind,
                                       rest=applied, sized_by=_rung or _sized_by)
            _last_failed_key = key
            if kind in ("auth", "revoked"):
                # v1.0.6: a refused credential is an actionable problem — always
                # shown, even at 'silent' (matches the documented "silent still
                # shows hard errors" promise).
                PrintStyle.warning(
                    f"[KAME] {call_type}|{model_short} {_key_display_auth(key)} "
                    f"{_friendly_error_msg(kind, applied, sc, e)}"
                    f"{_retirement_suffix(identity, key)}"
                )
                _maybe_log_full_error(call_type, model_short, key, e, kind, applied, sc)
            else:
                _log_failure(call_type, model_short, key, e,
                             kind, applied, sc, identity, all_keys, label=_rung)

            # v1.0.6: rotate to the next key IMMEDIATELY. asyncio.sleep(0) yields to
            # the event loop (so we never spin the CPU or starve other tasks) without
            # any wall-clock delay: the failed key is already marked sick, so the next
            # iteration picks a DIFFERENT key, and once all keys are sick
            # _get_best_key returns EXHAUSTED_RETRY and the ETA-driven sleep takes
            # over. Net effect: near-instant failover.
            _last_kind = kind
            _is_busy_failure = _kame_busy_failure(identity, key, kind, sc)
            if _is_busy_failure:
                _busy_keys.add(key)
                _hop_f = _KAME_FALLBACK_HOP.get()
                if _KAME_MODEL_SWITCH != "never" and _kame_main(ctx):
                    if (_circle_probe or ctx.get("probe_primary") or (_hop_f and _hop_f.get("probe"))):
                        # 1.8.2.9: a probe answered "busy" is proof; the window doubles, once.
                        if not ctx.get("probe_noted"):
                            ctx["probe_noted"] = True
                            _busy_noted[0] = True
                            _kame_note_busy(identity)
                    elif (len(_busy_keys) == 1 and not _busy_noted[0] and _hop_f is None
                          and time.time() >= _KAME_BUSY_UNTIL.get(identity, 0.0)
                          and _kame_is_busy(identity, _busy_keys)):
                        # the first knock after the window said "busy" again: the window reopens
                        # (otherwise every reply for the next half minute knocks once more)
                        _busy_noted[0] = True
                        _kame_note_busy(identity)
            if len(_busy_keys) >= 2 and not _busy_noted[0]:
                _busy_noted[0] = True
                _kame_note_busy(identity)
            if _KAME_CAROUSEL_DISABLED:
                # v1.8.1.0: the key is sized and rested above; what happens
                # next is Agent Zero's own retry, exactly as without KAME.
                _kame_wait_notice_finish(st, "stopped")
                raise _kame_with_retry_hint(e, kind)
            # v1.8.2.7: the four states where this model cannot answer now.
            # The fallback list answers when it can; without one, Agent Zero
            # would end the task on a raised error, so only what no wait can fix
            # is handed back - everything else keeps rotating as in 1.8.1.8.
            _all = set(all_keys)
            _no_fix = bool(_no_clock_fix) and _all <= _no_clock_fix
            _all_denied = bool(_denied) and _all <= set(_denied) and len(set(_denied.values())) == 1
            _door = _kame_closed_door(e)
            _why = ("the provider has no server for this model right now" if _door
                    else "every key: plan or country does not allow this" if _no_fix
                    else "every key refused this model" if _all_denied
                    else "no answer in time, twice in a row" if (_timeouts >= 2 and _kame_main(ctx))
                    # v1.8.2.8: a busy provider moves at once ("auto"); otherwise
                    # once the user has waited past the switch time.
                    else "your provider is busy" if (_KAME_MODEL_SWITCH != "never"
                                                    and _kame_is_busy(identity, _busy_keys))
                    else "no answer in time" if (0 < _kame_switch_threshold(identity, ctx)
                                                 < time.perf_counter() - _call_started_at)
                    else "")
            if _why:
                _switched = await _kame_switch_model(self, ctx, _why, e)
                if _switched is not _KAME_NO_SWITCH:
                    _kame_wait_notice_finish(st, "resumed")
                    return _switched
                if ctx.get("circle"):
                    if _why == "your provider is busy":
                        # 1.8.2.9 (E1): every model is busy - the wait is the probe clock's
                        _switched = await _kame_circle_step(self, ctx, identity, all_keys, e, kind)
                        if _switched is not None:
                            _kame_wait_notice_finish(st, "resumed")
                            return _switched
                    else:
                        _kame_walk_arm(ctx)
            if _no_fix or _all_denied:
                _kame_wait_notice_finish(st, "stopped")
                _kame_event("surfaced", identity, key,
                            reason=f"{_why}; handed to Agent Zero",
                            code=sc, detail=str(e))
                raise _kame_with_retry_hint(e, "denied" if _all_denied else kind)
            if _door:
                # One closed door for every key: the whole pool waits for it
                # (the provider's own number when it gave one), instead of each
                # key knocking once a second.
                _door_wait = max(_KAME_CLOSED_DOOR_REST_S, float(_stated_server_delay(e) or 0))
                _kame_rest_pool_for_door(identity, all_keys, _door_wait)
                _kame_event("wait", identity, reason=f"{_why}: every key waits", seconds=_door_wait)
            if _aux is not None and _aux.budget.klass == "important":
                # 1.8.2.9 review H1/N3/N6. An important job is bounded by time, not by a count: the
                # provider's "busy" and a refusal while another key is ready cost it nothing to go on.
                if _is_busy_failure:
                    _aux.busy_last = True
                    if len(_busy_keys) >= min(2, len(set(all_keys))):
                        if not _aux_walked:
                            # with a list of the user's own, the job does not sit out the storm: the list
                            # is asked while the best model is proven busy (one probe clock at most), and
                            # again at the stop (Q3)
                            _aux_walked = True
                            _early = await _kame_switch_model(
                                self, ctx, "background job busy", None,
                                aux={"deadline": time.monotonic() + max(_KAB.FLOOR_S, min(_aux.remaining(), _KAME_PROBE_EVERY_S)),
                                     "requests": max(1, _aux.budget.max_requests - _aux.requests)})
                            if _early is not _KAME_NO_SWITCH:
                                return _early
                        # N3: the job's first probe clock keeps the 1.8.2.8 cadence (a short blip costs
                        # seconds); after that it asks on the 20 s clock
                        if _aux.elapsed() >= _KAME_PROBE_EVERY_S:
                            if _KAME_PROBE_EVERY_S >= _aux.remaining():
                                return await _kame_aux_stop(self, ctx, _aux, identity)
                            _busy_keys.clear()
                            await asyncio.sleep(_KAME_PROBE_EVERY_S)
                            continue
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
    ctx["a0_response_callback"] = response_callback

    if response_callback is None:
        wrapped_response = None
    else:
        async def wrapped_response(delta, full):
            stitcher = ctx.get("stitcher")
            if stitcher is not None:
                # v1.8.2.7: a continuation. The stitcher drops what repeats the
                # answer already on screen; Agent Zero sees one growing answer.
                if not progress.get("first_sign_at"):
                    progress["first_sign_at"] = time.time()
                shown = stitcher.feed(delta or "")
                if not shown:
                    return None
                delta, full = shown, (ctx.get("stitch_seen") or "") + stitcher.forwarded
            progress["any"] = True
            progress["full_text"] = full
            if not progress.get("first_text_at"):
                progress["first_text_at"] = time.time()
            if not progress.get("first_sign_at"):
                progress["first_sign_at"] = progress["first_text_at"]
            return await response_callback(delta, full)

    if reasoning_callback is None:
        wrapped_reasoning = None
    else:
        async def wrapped_reasoning(delta, full):
            progress["reasoning"] = True
            if not progress.get("first_sign_at"):
                progress["first_sign_at"] = time.time()
            return await reasoning_callback(delta, full)

    if tokens_callback is None:
        wrapped_tokens = None
    else:
        async def wrapped_tokens(text, tokens):
            progress["any"] = True
            if not progress.get("first_text_at"):
                progress["first_text_at"] = time.time()
            if not progress.get("first_sign_at"):
                progress["first_sign_at"] = progress["first_text_at"]
            return await tokens_callback(text, tokens)

    return wrapped_response, wrapped_reasoning, wrapped_tokens


def _kame_make_entry_wrapper(entry_name, original, binding=None):
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
        # An outer extension may retain this callable after uninstall. Leave
        # that extension intact while making our captured layer a passthrough.
        if binding is not None and not binding["active"]:
            return await original(
                self, system_message=system_message, user_message=user_message,
                messages=messages, response_callback=response_callback,
                reasoning_callback=reasoning_callback, tokens_callback=tokens_callback,
                rate_limiter_callback=rate_limiter_callback,
                explicit_caching=explicit_caching, **kwargs)
        provider = (self.a0_model_conf.provider if getattr(self, "a0_model_conf", None) else "unknown").lower()
        model = (getattr(self, "model_name", "") or "unknown").lower()
        identity = f"{provider}:{model}"

        all_keys = _get_all_api_keys(self)
        identity = _kame_identity_with_region(identity, self, kwargs, model, all_keys)

        # v1.8.1.0: `rotation_disabled` hands every call back exactly as if
        # KAME were not installed — the same door as a single-key config.
        if not all_keys or _KAME_ROTATION_DISABLED:
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
            # v1.8.2.7: which entry point this is, so a fallback model is
            # called through the same one (unified_call / unified_turn).
            "entry_name": entry_name,
        }
        (ctx["response_callback"],
         ctx["reasoning_callback"],
         ctx["tokens_callback"]) = _kame_wrap_callbacks(
            ctx, response_callback, reasoning_callback, tokens_callback
        )

        ctx["main_chat"] = _kame_is_main_chat(stream)
        _job = "chat" if ctx["main_chat"] else (
            "compress" if "Compress" in (_KAME_CALL_CONTEXT.get() or "") else "utility")
        _job_token = _KAME_JOB.set(_job)
        _task_token = _KAME_TASK.set("")
        _flight_id = secrets.token_hex(6)
        ctx["flight"] = {"job": _job, "identity": identity, "started": time.time(),
                         "refusals": 0, "last": "", "fallback": bool(_KAME_FALLBACK_HOP.get())}
        _kame_note_call(ctx, self, identity)
        with _KAME_LOCK:
            _KAME_IN_FLIGHT[_flight_id] = ctx["flight"]
        try:
            return await _kame_carousel(self, ctx)
        finally:
            with _KAME_LOCK:
                _KAME_IN_FLIGHT.pop(_flight_id, None)
            _KAME_JOB.reset(_job_token)
            _KAME_TASK.reset(_task_token)

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
            if _KAME_PASSTHROUGH_EXC and isinstance(e, _KAME_PASSTHROUGH_EXC):
                raise
            PrintStyle.error(f"[KAME] Compression failed: {_scrub_for_log(e)}")
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
        except Exception as e:
            if _KAME_PASSTHROUGH_EXC and isinstance(e, _KAME_PASSTHROUGH_EXC):
                raise
            PrintStyle.error(f"[KAME] Bulk compression failed.")
            self.summary = "[Bulk summary unavailable]"
        return self.summary
    finally:
        _KAME_CALL_CONTEXT.reset(_ctx_token)


# --- SHIELD: RATE LIMITER DEADLOCK FIX ---

def _patch_rate_limiters():
    """Apply the lock shield once, retaining enough state for clean removal."""
    try:
        from helpers.rate_limiter import RateLimiter
        import models
        import weakref
        previous = vars(RateLimiter).get("_kame_rate_limiter_state")
        if previous and previous["active"]:
            return True
        state = {"active": True, "locks": weakref.WeakKeyDictionary()}
        RateLimiter._kame_rate_limiter_state = state

        def convert(instance):
            old = getattr(instance, "_lock", None)
            if not isinstance(old, asyncio.Lock):
                return
            lock = threading.Lock()
            try:
                state["locks"][instance] = (old, lock)
            except TypeError:
                return  # A future non-weakrefable host instance stays native.
            instance._lock = lock

        def owns_lock(instance):
            try:
                pair = state["locks"].get(instance)
                return pair is not None and instance._lock is pair[1]
            except TypeError:
                return False

        def init_factory(original, binding):
            def initialize(instance, *args, **kwargs):
                original(instance, *args, **kwargs)
                if state["active"] and binding["active"]:
                    convert(instance)
            return initialize

        def cleanup_factory(original, binding):
            async def cleanup(instance):
                if not state["active"] or not binding["active"] or not owns_lock(instance):
                    return await original(instance)
                with instance._lock:
                    cutoff = time.time() - instance.timeframe
                    for key in instance.values:
                        instance.values[key] = [(t, v) for t, v in instance.values[key] if t > cutoff]
            return cleanup

        def total_factory(original, binding):
            async def get_total(instance, key):
                if not state["active"] or not binding["active"] or not owns_lock(instance):
                    return await original(instance, key)
                with instance._lock:
                    return sum(value for _, value in instance.values.get(key, []))
            return get_total

        for name, factory in (("__init__", init_factory), ("cleanup", cleanup_factory),
                              ("get_total", total_factory)):
            _kame_bind_owned(RateLimiter, name, factory)
        if hasattr(models, "rate_limiters"):
            for rl in models.rate_limiters.values():
                if isinstance(rl, RateLimiter):
                    convert(rl)
        return True
    except Exception:
        _unpatch_rate_limiters()
        return False


def _unpatch_rate_limiters():
    """Restore owned methods and only the instance locks we actually replaced."""
    try:
        from helpers.rate_limiter import RateLimiter
        state = vars(RateLimiter).get("_kame_rate_limiter_state")
        if state:
            state["active"] = False
            for instance, (original, owned) in list(state["locks"].items()):
                if getattr(instance, "_lock", None) is owned:
                    instance._lock = original
            state["locks"].clear()
            delattr(RateLimiter, "_kame_rate_limiter_state")
        _kame_unbind_owned(RateLimiter)
    except Exception:
        pass


# --- PATCH APPLICATION ---

# v1.0.9: the entry points KAME looked for by name before shape-detection existed.
# Only used as the layer-2 fallback (see _kame_bind_entry_points).
_KAME_LEGACY_ENTRY_NAMES = ("unified_turn", "unified_call")


def _kame_bind_owned(cls, name, factory):
    """Own one binding, not the whole host class or another extension's layer."""
    registry = vars(cls).get("_kame_owned_bindings")
    if registry is None:
        registry = {}
        setattr(cls, "_kame_owned_bindings", registry)
    previous = registry.get(name)
    original = getattr(cls, name)
    before = original
    stash = f"_kame_original_{name}"
    if previous and original is previous["wrapper"]:
        state = dict(previous)
        original = state["original"]
    else:
        state = {"had_local": name in vars(cls), "local_value": vars(cls).get(name),
                 "had_stash": stash in vars(cls), "stash_value": vars(cls).get(stash)}
        inherited = getattr(original, "_kame_binding", None)
        if inherited and inherited.get("wrapper") is original:
            # A subclass needs its own lease, not a second rotation through
            # the inherited wrapper. The parent's lease remains active.
            original = inherited["original"]
    state.update(active=True, original=original)
    wrapper = factory(original, state)
    state["wrapper"] = wrapper
    wrapper._kame_binding = state
    try:
        setattr(cls, name, wrapper)
        setattr(cls, stash, original)
    except Exception:
        state["active"] = False
        if getattr(cls, name, None) is wrapper:
            setattr(cls, name, before)
        raise
    if previous:
        previous["active"] = False
    registry[name] = state
    return wrapper


def _kame_unbind_owned(cls):
    registry = vars(cls).get("_kame_owned_bindings", {})
    for name, state in list(registry.items()):
        state["active"] = False
        if getattr(cls, name, None) is state["wrapper"]:
            if state["had_local"]:
                setattr(cls, name, state["local_value"])
            else:
                delattr(cls, name)
        stash = f"_kame_original_{name}"
        if vars(cls).get(stash) is state["original"]:
            if state["had_stash"]:
                setattr(cls, stash, state["stash_value"])
            else:
                delattr(cls, stash)
        registry.pop(name, None)
    if vars(cls).get("_kame_owned_bindings") is registry and not registry:
        delattr(cls, "_kame_owned_bindings")


def _kame_accessory_factory(implementation):
    def factory(original, binding):
        async def wrapped(self, *args, **kwargs):
            fn = implementation if binding["active"] else original
            return await fn(self, *args, **kwargs)
        wrapped._kame_implementation = implementation
        return wrapped
    return factory


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
            _kame_bind_owned(cls, name, lambda original, binding, n=name:
                             _kame_make_entry_wrapper(n, original, binding))
            bound.append(name)
        except Exception:
            continue

    _KAME_BOUND_ENTRY_POINTS = bound
    _kame_check_host_seams(cls, bound, layer)
    return layer if bound else 3


#: v1.8.2.7 (Hermes 1.8.2.3 `check_host_seams`): what KAME found when it bound,
#: named on the panel. The parameters it calls Agent Zero's model method with;
#: one gone means a change upstream the upgrade checker would also flag.
_KAME_SEAM_PARAMS = ("messages", "response_callback", "reasoning_callback", "tokens_callback",
                     "rate_limiter_callback", "explicit_caching")
_KAME_HOST_SEAMS = {}


def _kame_check_host_seams(cls, bound, layer) -> None:
    import inspect as _inspect
    _KAME_HOST_SEAMS.clear()
    _KAME_HOST_SEAMS["layer"] = layer
    _KAME_HOST_SEAMS["entry_points"] = {}
    for name in bound:
        try:
            original = getattr(cls, f"_kame_original_{name}", None) or getattr(cls, name)
            params = _inspect.signature(_inspect.unwrap(original)).parameters
            names = set(params)
            missing = [p for p in _KAME_SEAM_PARAMS if p not in names]
            takes_kwargs = any(p.kind is _inspect.Parameter.VAR_KEYWORD for p in params.values())
            if not takes_kwargs:
                missing.append("**kwargs (api_key)")
            _KAME_HOST_SEAMS["entry_points"][name] = {"ok": not missing, "missing": missing}
        except Exception as exc:
            _KAME_HOST_SEAMS["entry_points"][name] = {"ok": False, "missing": [f"unreadable: {type(exc).__name__}"]}
    try:
        import models as _models  # the fallback list's factory
        _KAME_HOST_SEAMS["fallback_factory"] = bool(getattr(_models, "get_chat_model", None)
                                                    and getattr(_models, "ModelConfig", None))
    except Exception:
        _KAME_HOST_SEAMS["fallback_factory"] = False


def host_seams() -> dict:
    out = dict(_KAME_HOST_SEAMS)
    out["entry_points"] = {k: dict(v) for k, v in (_KAME_HOST_SEAMS.get("entry_points") or {}).items()}
    return out


def _kame_unbind_entry_points(cls) -> None:
    """Restore every entry point KAME wrapped (used by both uninstall and rollback)."""
    global _KAME_BOUND_ENTRY_POINTS
    _kame_unbind_owned(cls)
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
        _kame_bind_owned(Topic, "summarize_messages", _kame_accessory_factory(_kame_summarize_messages))
        _kame_bind_owned(Bulk, "summarize", _kame_accessory_factory(_kame_bulk_summarize))
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
        _kame_unbind_owned(Topic)
        _kame_unbind_owned(Bulk)
    except Exception:
        pass

    _unpatch_rate_limiters()

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
    PrintStyle(font_color="#96E").print("  KAME API Rotation, made by KAME — https://github.com/Kame696")
    PrintStyle(font_color="#96E").print("=" * 55)

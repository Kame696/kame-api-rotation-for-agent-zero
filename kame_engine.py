"""KAME API Rotation & Stability Engine v1.0.0.

Full-Spectrum Protections (unchanged from v0.5.7.3):
1. Identity-Aware Health (Tracks health by Model ID to isolate Chat/Utility)
2. Eternal Rotation (Infinite Loop — NEVER turns off)
3. RPM-Aware Predictive Selection (Picks key with most remaining capacity)
4. Anti-Dogpile Guard (Marks key used at pick time for concurrent safety)
5. Anti-Thundering-Herd (Pending counter prevents concurrent key collision)
6. Trust the Connection (No artificial timeouts — if accepted, let it finish)
7. Hybrid Learning Jitter (Smart Penalty Box up to 3600s + 2.0s Pulse wake-up)
8. KAME-Aware Compression Guard (UI Status Reporting + context-aware)
9. Rate Limiter Deadlock Fix (threading.Lock replaces asyncio.Lock)
10. Token Callback Support (framework token counters work correctly)
11. Friendly Error Reporting (clean messages, honest status)

v0.5.8.0 BEHAVIORAL FIX — ETA-driven sleep on exhausted pool:
- When ALL keys are sick AND the soonest sick_until > 3s, sleep until
  that key recovers (capped at 30s) instead of pulsing every 2s.
  Before this fix, KAME would burn ~45 real 429-rejected requests in 26
  seconds against a pool of fully-sick keys, and EACH wasted request
  re-armed the provider's cooldown, prolonging recovery.
- After EXHAUSTED_RETRY sleep, the loop now `continue`s — KAME NEVER
  calls acompletion() with a key whose sick_until is still in the
  future. Prior versions fell through and burned the request.
- Always-visible "Sleeping Xs (wake at HH:MM:SS)" notification so
  operators see KAME is intentionally waiting (not stuck).
- Long-delay warning (>60s) on parsed retry-after — Google's per-minute
  RPM cooldown is always <60s; longer values usually mean a daily
  quota and warrant operator awareness.
- Jitter preserved (random.uniform(0.1, 1.5)) for anti-bot detection
  and multi-client desync.

When at least 1 key is healthy: behavior is IDENTICAL to v0.5.7.4
(selection, request, no sleeps). The fix only changes the
all-keys-sick path.

v0.5.7.4 UX refinements (kept):
- Verbose trace mode (opt-in via `verbose_trace` plugin setting).
  When enabled, every call line shows: short key id, microsecond-level
  selection time, pool snapshot, and cascade summary after rotations.
- Explicit "Local wait" framing in the pulse log so users understand
  the pause is in-process (no API call) and see when the next key
  recovers.
- Compression-aware light filter: in `_get_best_key`, when the call
  context is "Compress", just-recovered keys (< 5s since last sickness)
  are de-prioritized IFF at least one fully-rested key remains. This
  reduces cascade probability on heavy compressions (90k+ tokens)
  without ever blocking the carousel.

Compatible with Agent Zero v1.14+
"""

import asyncio, contextvars, hashlib, threading, time, logging, re
from typing import Any, Awaitable, Callable, List, Optional, Tuple
import openai
import litellm
from litellm import acompletion
from langchain_core.messages import SystemMessage, HumanMessage
from helpers.print_style import PrintStyle

# --- GLOBAL REGISTRY ---
_KAME_KEY_HEALTH = {}  # { "provider:model": { "keys": {key: {sick_until, last_used, request_log, last_sick_at}} } }
_KAME_LOCK = threading.Lock()
_KAME_PATCHED = False
_KAME_CALL_CONTEXT = contextvars.ContextVar('kame_ctx', default='')

# --- v0.5.7.4: verbose trace mode (opt-in) ---
# Set by the activation extension from plugin settings. Default OFF preserves
# v0.5.7.3 log surface exactly. When ON, the engine adds short key id, pool
# snapshot, selection latency, and cascade summary to its existing lines —
# pure additive instrumentation, no algorithm change.
_KAME_VERBOSE_TRACE = False

# Window (seconds) within which a recently-recovered key is de-prioritized
# from COMPRESSION calls only. Chat/Utility paths are unaffected.
_KAME_COMPRESS_FRESH_WINDOW_S = 5.0


def set_verbose_trace(enabled: bool) -> None:
    """Toggle the v0.5.7.4 verbose log mode. Called by the activation extension."""
    global _KAME_VERBOSE_TRACE
    _KAME_VERBOSE_TRACE = bool(enabled)


def _key_short_id(key: str) -> str:
    """Return a 6-char content-hash identifier for a key, stable across runs.

    Hash so we never echo a key prefix/suffix that could leak the secret.
    """
    if not key:
        return "------"
    h = hashlib.sha256(key.encode("utf-8", errors="replace")).hexdigest()
    return "k" + h[:5]


def _pool_snapshot(identity: str, all_keys: list[str]) -> str:
    """Read-only one-liner about the pool state — for verbose logs only.

    Example: "pool 14/15 healthy, 1 cooling 38s"
    """
    now = time.time()
    with _KAME_LOCK:
        state = _KAME_KEY_HEALTH.get(identity, {}).get("keys", {})
        total = len(all_keys)
        healthy = 0
        soonest_recovery: float | None = None
        for k in all_keys:
            ks = state.get(k) or {}
            sick_until = float(ks.get("sick_until") or 0)
            if sick_until < now:
                healthy += 1
            else:
                # Track the soonest recovery so we can report a meaningful time.
                eta = sick_until - now
                if soonest_recovery is None or eta < soonest_recovery:
                    soonest_recovery = eta
    cooling = total - healthy
    if cooling == 0:
        return f"pool {healthy}/{total} healthy"
    eta_s = f"{int(soonest_recovery)}s" if soonest_recovery is not None else "?"
    return f"pool {healthy}/{total} healthy, {cooling} cooling (next in {eta_s})"


def _next_recovery_seconds(identity: str, all_keys: list[str]) -> float | None:
    """Smallest `sick_until - now` across all keys. None if all healthy."""
    now = time.time()
    with _KAME_LOCK:
        state = _KAME_KEY_HEALTH.get(identity, {}).get("keys", {})
        best: float | None = None
        for k in all_keys:
            ks = state.get(k) or {}
            sick_until = float(ks.get("sick_until") or 0)
            if sick_until > now:
                eta = sick_until - now
                if best is None or eta < best:
                    best = eta
    return best

_RATE_LIMIT_INDICATORS = (
    "429", "too many requests", "rate limit", "rate_limit",
    "quota exceeded", "quota left", "no quota",
    "resource exhausted", "resource_exhausted",
    "tokens per min", "requests per min", "quota_exceeded",
)


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
                # v0.5.7.4: timestamp of the most recent transition into
                # sickness. Used ONLY by the compression-aware filter in
                # _get_best_key. Default 0 means "never been sick" — never
                # filtered out.
                "last_sick_at": 0,
            }
        else:
            # Defensive: backfill for keys that were created on earlier versions.
            state["keys"][k].setdefault("last_sick_at", 0)
    return state


def _mark_key_health(identity, key, success=True, delay=20):
    """Update health state for a key after a completed (or failed) attempt.

    Design note (kept from v0.5.6, restored in v0.5.7.3):
      On success we DO append `now` to request_log here, on top of the
      append already done at selection time in `_get_best_key`. This is
      intentional: successful keys carry slightly heavier "weight" in the
      60s sliding window, which biases `_get_best_key` toward more even
      dispersion across the pool (an anti-overuse brake on champion keys).
      Removing this in v0.5.7/.7.1/.7.2 made the carousel concentrate on
      the best keys, which could trigger avoidable 429s on tight quotas.
      Restored to match the production-tested v0.5.6 behavior.
    """
    global _KAME_KEY_HEALTH
    now = time.time()
    with _KAME_LOCK:
        if identity not in _KAME_KEY_HEALTH: return
        state = _KAME_KEY_HEALTH[identity]
        if key not in state["keys"]: return
        if success:
            state["keys"][key]["last_used"] = now
            state["keys"][key]["sick_until"] = 0
            # Record successful completion in RPM counter (intentional dispersion brake).
            state["keys"][key]["request_log"].append(now)
        else:
            state["keys"][key]["sick_until"] = now + delay
            # v0.5.7.4: track when this transition happened so the compression-aware
            # filter in _get_best_key can de-prioritize just-recovered keys.
            state["keys"][key]["last_sick_at"] = now


def _extract_retry_delay(exc):
    """Extract retry-after from API error. Falls back to 20s default.

    Accepted range: 0 < val < 3600 seconds (raised from 300s in v0.5.7).
    The 3600s ceiling lets KAME respect legitimate longer waits (e.g.,
    a daily quota near reset) while still rejecting absurd or
    parsing-error values like 99999999.
    """
    # 1. Try litellm's retry_after attribute
    retry_after = getattr(exc, 'retry_after', None)
    if retry_after is not None:
        try:
            val = float(retry_after)
            if 0 < val < 3600:
                return val
        except (ValueError, TypeError):
            pass
    # 2. Try HTTP response headers
    headers = getattr(exc, 'headers', None) or getattr(exc, 'response_headers', None)
    if headers:
        ra = None
        if isinstance(headers, dict):
            ra = headers.get('retry-after') or headers.get('Retry-After')
        elif hasattr(headers, 'get'):
            ra = headers.get('retry-after') or headers.get('Retry-After')
        if ra:
            try:
                val = float(ra)
                if 0 < val < 3600:
                    return val
            except (ValueError, TypeError):
                pass
    # 3. Try parsing from error message text
    #    Matches: "retry in 42s", "retry after 42", "retryDelay: 42",
    #    "Retry-After: 42", "Please retry in 42.03309031s"
    err_msg = str(exc)
    match = re.search(r'retry[_\s-]*(?:after|delay|in)[:\s"]*(\d+(?:\.\d+)?)', err_msg, re.IGNORECASE)
    if match:
        try:
            val = float(match.group(1))
            if 0 < val < 3600:
                return val
        except ValueError:
            pass
    return 20  # Safe default


def _classify_error_delay(exc):
    """Smart quarantine duration based on error type.

    Rate-Limit Intelligence: for 429 errors, parses the provider's retryDelay
    (e.g. Google's "retry in 42s") and uses THAT as the quarantine time.
    This prevents spin-loops (blind 3s retry against a 42s limit) and avoids
    over-quarantine (waiting 20s when the provider says 2s is enough).

    - Timeout: 3s (key isn't broken, just slow/busy)
    - Rate limit: parsed retryDelay or 20s default (exact provider guidance)
    - Server error (503/500): 5s (API temporarily busy, recovers fast)
    - Other: 20s (unknown error, give it time)
    """
    # Timeouts: the key isn't broken, just didn't respond in time
    if isinstance(exc, (asyncio.TimeoutError, asyncio.CancelledError)):
        return 3
    err_msg = str(exc).lower()
    if "timeout" in err_msg or "timed out" in err_msg:
        return 3
    # Rate limits: use provider's retryDelay if available
    if any(ind in err_msg for ind in _RATE_LIMIT_INDICATORS):
        delay = _extract_retry_delay(exc)
        # v0.5.8.0 — flag unusually long delays so the operator can investigate.
        # Google's per-minute RPM cooldown is always < 60s; values above that
        # typically mean a daily quota (RPD/TPD) or a different resource class.
        # We respect the value (still capped at 3600s by _extract_retry_delay),
        # but log a warning so the user sees the anomaly.
        if delay > 60:
            PrintStyle.warning(
                f"[KAME] ⚠ Long retry delay parsed: {delay:.0f}s (>60s). "
                f"Likely a daily quota or non-RPM limit. Respecting the provider's value."
            )
        return delay
    # Server errors (503): API temporarily busy, doesn't cost RPM, recovers fast
    status_code = getattr(exc, 'status_code', None)
    if status_code == 503 or "service unavailable" in err_msg or "serviceunavailable" in err_msg:
        return 5
    if status_code == 500 or "internal server error" in err_msg:
        return 5
    # Everything else: 20s default
    return 20


def _friendly_error_msg(exc, delay):
    """Convert raw exception to clean, user-friendly log message."""
    err_msg = str(exc).lower()
    if isinstance(exc, (asyncio.TimeoutError, asyncio.CancelledError)) or "timeout" in err_msg:
        return "\u23f3 Slow response, trying next key..."
    if any(ind in err_msg for ind in _RATE_LIMIT_INDICATORS):
        return f"\u23f3 Rate limited (learned: wait {delay}s), trying next key..."
    status_code = getattr(exc, 'status_code', None)
    if status_code in (500, 503) or "service unavailable" in err_msg or "serviceunavailable" in err_msg:
        return "\u23f3 API temporarily busy, trying next key..."
    return f"\u26a0\ufe0f {type(exc).__name__}, cooling {delay}s..."


def _get_best_key(identity, all_keys):
    """RPM-aware predictive selection: picks the key with most remaining capacity.

    v0.5.7.4 additive refinement: when the current call context is a
    compression call (``📦 Compress``), keys that just transitioned out of
    sickness within the last ``_KAME_COMPRESS_FRESH_WINDOW_S`` seconds are
    de-prioritized — IFF at least one fully-rested healthy key remains.
    This protects heavy compressions (90k+ tokens) from marginal keys that
    only recovered seconds ago. Chat/Utility paths are unaffected (default
    context is empty string, so the filter is skipped).
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

        # 2b. v0.5.7.4 — Compression-aware filter (additive, never empties the pool).
        if is_compress_ctx and len(healthy) > 1:
            fresh = [
                k for k in healthy
                if (pool[k].get("last_sick_at") or 0) == 0
                   or (now - pool[k]["last_sick_at"]) > _KAME_COMPRESS_FRESH_WINDOW_S
            ]
            # Only apply the filter when at least one fully-rested key survives.
            # Otherwise fall through to the unmodified `healthy` list — never
            # block the carousel.
            if fresh:
                healthy = fresh

        # 3. RPM-aware selection: fewest recent requests = most remaining capacity
        #    Tie-break: least recently used (LRU) for even spreading
        best_key = min(healthy, key=lambda k: (
            len(pool[k]["request_log"]),  # primary: fewest requests = most capacity
            pool[k]["last_used"],          # secondary: LRU for even spreading
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
        # 401 = invalid/expired key — not terminal, rotate to next
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
        # No multi-key config — fall through to original framework method
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

    # Logging labels — Chat streams, Utility doesn't
    call_type = "Chat" if stream else "Util"
    model_short = model.split("/")[-1][:25]

    # Strip A0-only retry params before passing to LiteLLM
    call_kwargs: dict[str, Any] = {**self.kwargs, **kwargs}
    call_kwargs.pop("a0_retry_attempts", None)
    call_kwargs.pop("a0_retry_delay_seconds", None)

    # "Calling..." log — shows KAME is alive during gaps
    _ctx = _KAME_CALL_CONTEXT.get()
    _ctx_label = f" {_ctx}" if _ctx else ""
    PrintStyle(font_color="#85C1E9").print(
        f"[KAME] {call_type}|{model_short}{_ctx_label} \u27a1 Calling..."
    )

    # v0.5.7.4 cascade summary tracking
    _call_started_at = time.perf_counter()
    _cooldown_overhead_s = 0.0
    _pulse_count = 0

    attempt_no = 0
    while True:  # ETERNAL CAROUSEL — all call types use same robust rotation
        attempt_no += 1

        # v0.5.7.4 selection latency for verbose trace mode
        _select_t0 = time.perf_counter()
        key, status = _get_best_key(identity, all_keys)
        _select_ms = (time.perf_counter() - _select_t0) * 1000.0

        if status == "EXHAUSTED_RETRY":
            # v0.5.8.0 \u2014 ETA-driven sleep.
            # All keys are sick. Sleep until the SOONEST key recovers (or 30s,
            # whichever is smaller) instead of pulsing every 2s blindly. This
            # is the change that eliminates the "burn ~45 wasted requests in 26s"
            # behavior observed in v0.5.7.4 logs. Jitter preserved for anti-bot
            # detection and multi-client desync. After sleep we `continue` so
            # we re-select a key \u2014 we NEVER call acompletion() with a sick key.
            import random
            _soonest_eta = _next_recovery_seconds(identity, all_keys)
            if _soonest_eta is not None and _soonest_eta > 3.0:
                # We know exactly when the next key recovers. Sleep until then
                # with a small clock-skew buffer. Capped at 30s so very long
                # daily-quota waits still wake up periodically to re-check.
                wait = min(_soonest_eta + 0.5, 30.0) + random.uniform(0.1, 1.5)
            else:
                # No ETA known OR very near recovery \u2014 keep the v0.5.7.x pulse
                # behavior as a safety net.
                wait = 2.0 + random.uniform(0.1, 1.5)
            _pulse_count += 1
            _pulse_started = time.perf_counter()
            # Always-visible sleep notification \u2014 independent of verbose_trace \u2014
            # so users see KAME is awake and waiting (not "stuck"). This is the
            # ONE log line we always emit on a sleep cycle.
            _eta_label = f"{int(_soonest_eta)}s" if _soonest_eta is not None else "unknown"
            _wake_at = time.strftime("%H:%M:%S", time.localtime(time.time() + wait))
            PrintStyle.warning(
                f"[KAME] {call_type}|{model_short} \U0001f4a4 All keys cooling. "
                f"Sleeping {wait:.1f}s (no API calls) - next key recovers in ~{_eta_label} "
                f"(wake at {_wake_at})"
            )
            await asyncio.sleep(wait)
            _cooldown_overhead_s += (time.perf_counter() - _pulse_started)
            continue   # v0.5.8.0 fix: re-select after sleep; never call API with a sick key.
        elif _KAME_VERBOSE_TRACE:
            # Additive trace line: which key was picked + selection time.
            _key_id = _key_short_id(key)
            PrintStyle(font_color="#85C1E9").print(
                f"[KAME] {call_type}|{model_short}{_ctx_label} \u27a1 "
                f"{_key_id} picked in {_select_ms:.2f}ms"
            )

        result = ChatGenerationResult()
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
                    # Mid-stream failure: smart quarantine
                    delay = _classify_error_delay(stream_err)
                    _mark_key_health(identity, key, False, delay)
                    PrintStyle.warning(f"[KAME] {call_type}|{model_short} {key[:8]}... {_friendly_error_msg(stream_err, delay)}")
                    continue
            else:
                parsed = _parse_chunk(_completion)
                result.add_chunk(parsed)

            _mark_key_health(identity, key, True)
            _ctx = _KAME_CALL_CONTEXT.get()
            _ctx_label = f" {_ctx}" if _ctx else ""
            _attempts_s = f"{attempt_no} attempt" + ("s" if attempt_no > 1 else "")
            if _KAME_VERBOSE_TRACE:
                # v0.5.7.4 verbose success line \u2014 show key id (hashed), pool snapshot,
                # total wall time, and cascade summary if applicable.
                _total_s = time.perf_counter() - _call_started_at
                _key_id = _key_short_id(key)
                _snap = _pool_snapshot(identity, all_keys)
                _cascade = ""
                if attempt_no > 1 or _pulse_count > 0:
                    _cascade = (
                        f" | {attempt_no - 1} rotation"
                        f"{'s' if attempt_no - 1 != 1 else ''}"
                    )
                    if _pulse_count > 0:
                        _cascade += f", {_pulse_count} pulse{'s' if _pulse_count != 1 else ''}"
                    if _cooldown_overhead_s > 0.1:
                        _cascade += f", {_cooldown_overhead_s:.1f}s local wait"
                PrintStyle(font_color="#85C1E9").print(
                    f"[KAME] {call_type}|{model_short}{_ctx_label} \u2705 {_key_id} "
                    f"in {_total_s:.1f}s | {_snap}{_cascade}"
                )
            else:
                PrintStyle(font_color="#85C1E9").print(
                    f"[KAME] {call_type}|{model_short}{_ctx_label} \u2705 {key[:8]}... ({_attempts_s})"
                )
            return result.response, result.reasoning

        except Exception as e:
            if _is_terminal_error(e):
                raise e

            # Auth errors: quarantine key for a very long time (likely permanently bad)
            if _is_auth_error(e):
                _mark_key_health(identity, key, False, 3600)  # 1 hour
                PrintStyle.warning(f"[KAME] {call_type}|{model_short} \U0001f512 Invalid key {key[:8]}..., quarantined 1h")
            else:
                delay = _classify_error_delay(e)
                _mark_key_health(identity, key, False, delay)
                PrintStyle.warning(f"[KAME] {call_type}|{model_short} {key[:8]}... {_friendly_error_msg(e, delay)}")

            await asyncio.sleep(0.05)
            continue


# --- SHIELDS: COMPRESSION TIMEOUT GUARD ---

async def _kame_summarize_messages(self, messages):
    """KAME-patched Topic.summarize_messages — Trust the Connection.

    No artificial timeout (v0.5.6+): the eternal carousel rotates keys on
    real errors only. Massive compressions (90k+ tokens) run for however
    long the provider needs. On total failure, a best-effort fallback
    summary is generated from the first few message tails so the chat
    keeps moving.
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
    """KAME-patched Bulk.summarize — Trust the Connection.

    Same philosophy as _kame_summarize_messages: no artificial timeout.
    The eternal carousel rotates keys on real errors; on total failure,
    a placeholder summary is returned so downstream logic keeps moving.
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
        # History.compress and History.merge_bulks_by are LEFT NATIVE —
        # A0 v1.14's _90_organize_history_wait has stall detection + max pass guard.
        if not hasattr(Topic, "_kame_original_summarize_messages"):
            Topic._kame_original_summarize_messages = Topic.summarize_messages
        if not hasattr(Bulk, "_kame_original_summarize"):
            Bulk._kame_original_summarize = Bulk.summarize

        Topic.summarize_messages = _kame_summarize_messages
        Bulk.summarize = _kame_bulk_summarize

        # Shield 6: Rate Limiter Deadlock Fix
        _patch_rate_limiters()

        _KAME_PATCHED = True
        _print_shield_status()
        return True
    except Exception as e:
        PrintStyle.error(f"[KAME v1.0.0] Patch Failed: {e}")
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
        _KAME_PATCHED = False
        return True
    except Exception:
        return False


def _print_shield_status():
    PrintStyle(font_color="#96E").print("=" * 55)
    PrintStyle(font_color="#96E").print("  \U0001f422\u26a1 KAME v1.0.0 \u2014 ACTIVE")
    shields = [
        "Identity-Aware Health",
        "Eternal Carousel Rotation",
        "RPM-Aware Predictive Selection",
        "Anti-Dogpile Guard",
        "Anti-Thundering-Herd (Pending Counter)",
        "Trust the Connection (No Artificial Timeouts)",
        "KAME-Aware Compression Guard",
        "Hybrid Learning (Parsed retry-delay + ETA-driven sleep)",
        "Long-Delay Warning (>60s flagged for operator)",
        "Rate Limiter Lock Fix",
        "Token Callback Support",
        "Friendly Error Reporting",
    ]
    for s in shields:
        PrintStyle.success(f"  \u2713 {s}")
    PrintStyle(font_color="#96E").print("  API Rotation \u2014 Want to donate? BTC: 36BGYhMEVFgY8PLGMVux93pjGt92KVM6dJ")
    PrintStyle(font_color="#96E").print("=" * 55)

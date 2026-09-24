"""KAME's evidence reader — what a refusal says, read from its fields first.

1.8.1.0 port of the Hermes port's error reader (``core/catalog.py``,
``core/classify.py``, the sizing half of ``core/quota.py`` and
``core/provider_rules.py``), so both hosts judge the same payload the same way.
It was graded there against an independent answer key of 68 error shapes from
12 providers and gateways; ``tools/a0_expected_gate.py`` in the workspace runs
this engine against the same key and the same real refusals.

The rule it lives by, unchanged from the Hermes side: **a field outranks a
sentence, and the provider outranks the library.** Google's per-minute
throttle and OpenAI's empty balance send the identical sentence; their fields
never collide. Nothing here branches on who the provider is — only on the
shape of what came back — so a provider that does not exist yet is read by the
same rules on its first refusal.

Framework-free on purpose: no Agent Zero import, no network, no clock beyond
the one passed in. ``kame_engine`` asks :func:`judge` and maps the answer onto
its own kinds; this module never touches key health.
"""
from __future__ import annotations

import ast
import json
import math
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

# ── families ──────────────────────────────────────────────────────────────
THROTTLE = "throttle"
BILLING = "billing"
DENIAL = "denial"
AUTH_DEAD = "auth_dead"
AUTH_REFRESH = "auth_refresh"
SERVER = "server"
TIMEOUT = "timeout"
TERMINAL = "terminal"
NOT_A_FAILURE = "not_a_failure"
UPSTREAM = "upstream"

# ── windows and scopes ────────────────────────────────────────────────────
PER_MINUTE = "per_minute"
TOKENS_PER_MINUTE = "tokens_per_minute"
PER_HOUR = "per_hour"
PER_DAY = "per_day"
PER_WEEK = "per_week"
PER_MONTH = "per_month"
ACCOUNT = "account"
UNKNOWN = "unknown"
PER_MODEL = "per_model"

MAX_RELATIVE_DELAY_SECONDS = 24 * 60 * 60
MAX_ABSOLUTE_HORIZON_SECONDS = 24 * 60 * 60
MIN_BENCH_SECONDS = 1.0

#: A named window that stated no number rests one window's worth. A minute
#: rolls in a minute; five more seconds absorb clock skew (Hermes quota.py).
WINDOW_DEFAULTS = {
    PER_MINUTE: 65.0,
    TOKENS_PER_MINUTE: 65.0,
    PER_HOUR: 600.0,
    PER_DAY: 3600.0,
    PER_WEEK: 3600.0,
    PER_MONTH: 3600.0,
    ACCOUNT: 3600.0,
}
LONG_WINDOWS = frozenset({PER_DAY, PER_WEEK, PER_MONTH, ACCOUNT})


class Reading:
    """One catalogue row: what a field value means, and how sure that is."""

    __slots__ = ("family", "window", "scope", "why", "certain", "billing_context_only")

    def __init__(self, family, *, window=UNKNOWN, scope=UNKNOWN, why="", certain=True,
                 billing_context_only=False):
        self.family = family
        self.window = window
        self.scope = scope
        self.why = why
        self.certain = certain
        self.billing_context_only = billing_context_only


class Judgment:
    """What the evidence says about one failure.

    ``family`` is one of the constants above. For a throttle, ``seconds`` is the
    rest the evidence sizes (``None`` = the provider named a throttle and nothing
    to size it by), ``source`` says where the number came from (``"window"`` =
    one window's default, KAME's number; anything else = the provider's), and
    ``long_stated`` marks a long window whose reset the provider stated itself.
    """

    __slots__ = ("family", "window", "scope", "seconds", "source", "why", "long_stated")

    def __init__(self, family, *, window=UNKNOWN, scope=UNKNOWN, seconds=None, source="", why="",
                 long_stated=False):
        self.family = family
        self.window = window
        self.scope = scope
        self.seconds = seconds
        self.source = source
        self.why = why
        self.long_stated = long_stated

    @property
    def provider_sized(self) -> bool:
        return self.seconds is not None and self.source not in ("", "window")

    def __repr__(self):  # pragma: no cover - debugging aid
        return (f"Judgment({self.family!r}, window={self.window!r}, scope={self.scope!r}, "
                f"seconds={self.seconds!r}, source={self.source!r})")


def _n(text) -> str:
    return re.sub(r"[\s_.\-]+", "", str(text or "")).lower()


# ── the catalogue: field values, never prose ──────────────────────────────
_TABLE: Dict[str, Reading] = {}


def _put(reading: Reading, *names: str) -> None:
    for name in names:
        _TABLE[_n(name)] = reading


_put(Reading(BILLING, window=ACCOUNT, scope=ACCOUNT, why="the account is out of credit"),
     "insufficient_quota", "billing_hard_limit_reached", "billing_not_active",
     "billing_error", "payment_required", "Arrearage", "AllocationQuota.FreeTierOnly",
     "insufficient_user_quota")
_put(Reading(THROTTLE, why="a per-credential counter is spent"),
     "rate_limit_exceeded", "rate_limit_error", "rate_limited", "ratelimit", "too_many_requests",
     "Too Many Requests", "RESOURCE_EXHAUSTED", "RATE_LIMIT_EXCEEDED", "QUOTA_EXCEEDED",
     "gemini_rate_limited", "Throttling", "concurrency_limit_exceeded",
     "too_many_concurrent_requests", "slow_down")
_put(Reading(SERVER, why="the provider is busy, not the credential spent"),
     "overloaded_error", "overloaded", "server_error", "api_error", "service_unavailable",
     "engine_overloaded", "UNAVAILABLE", "INTERNAL", "server", "provider_error")
_put(Reading(TIMEOUT, why="no response in time"),
     "timeout_error", "timeout", "request_timeout", "DEADLINE_EXCEEDED")
_put(Reading(AUTH_DEAD, why="the provider rejected the credential itself"),
     "invalid_api_key", "authentication_error", "authentication", "API_KEY_INVALID",
     "invalid_authentication", "UNAUTHENTICATED", "gemini_unauthorized",
     "ACCESS_TOKEN_TYPE_UNSUPPORTED")
_put(Reading(DENIAL, scope=PER_MODEL, why="this key is refused for this model"),
     "permission_error", "permission_denied", "PERMISSION_DENIED", "SERVICE_DISABLED",
     "CONSUMER_SUSPENDED", "API_KEY_SERVICE_BLOCKED", "access_denied", "model_not_authorized")
_put(Reading(TERMINAL, why="no key can answer this request"),
     "model_not_found", "not_found_error", "NOT_FOUND", "request_too_large",
     "context_length_exceeded", "string_too_long", "invalid_prompt")
# The wastebaskets: a family a provider files a bare 400 under, not a fact.
_put(Reading(TERMINAL, certain=False, why="the provider filed this under its malformed-request family"),
     "invalid_request_error", "INVALID_ARGUMENT")
_put(Reading(NOT_A_FAILURE, why="the provider returned this as a completion"),
     "max_tokens_exceeded", "token_limit_exceeded")
# A missing plan entitlement has no time-based reset (Hermes catalog.py, the
# same row). v1.8.1.4: this port only knew it from the sentence "to use Codex
# with your ChatGPT plan", so the same refusal worded "with your plan" -- or
# any other product that files the code -- rested 30s as a throttle, forever.
_put(Reading(BILLING, window=ACCOUNT, scope=ACCOUNT, why="plan does not include this service"),
     "usage_not_included")
# Google's odd one out: FAILED_PRECONDITION on a 400 is "enable billing".
_put(Reading(BILLING, window=ACCOUNT, scope=ACCOUNT,
             why="the free tier is unavailable here; billing must be enabled"),
     "FAILED_PRECONDITION")

_STATUS_READINGS: Dict[int, Reading] = {
    402: Reading(BILLING, window=ACCOUNT, scope=ACCOUNT, why="payment required"),
    498: Reading(SERVER, why="flex tier at capacity"),
    529: Reading(SERVER, why="the provider is overloaded"),
}

_PREFIX_RULES = (("throttling", Reading(THROTTLE, why="a per-credential counter is spent")),)

_EXCEPTION_CLASSES: Dict[str, Reading] = {}
for _name in ("RateLimitError",):
    _EXCEPTION_CLASSES[_n(_name)] = Reading(THROTTLE, why="the SDK raised its rate-limit class")
for _name in ("PaymentRequired", "PaymentRequiredError"):
    _EXCEPTION_CLASSES[_n(_name)] = Reading(BILLING, window=ACCOUNT, scope=ACCOUNT,
                                            why="the SDK raised its payment-required class")
# AuthenticationError / PermissionDeniedError are the classes of EVERY 401/403
# and are deliberately unmapped: a class is not a statement about this key.


def look_up(*values) -> Optional[Reading]:
    """A certain row wins wherever it sat; an uncertain one only if nothing else matched."""
    candidates = [str(v) for v in values if isinstance(v, (str, int)) and str(v).strip()]
    fallback = None
    for value in candidates:
        hit = _TABLE.get(_n(value))
        if hit is None:
            continue
        if hit.certain:
            return hit
        if fallback is None:
            fallback = hit
    for value in candidates:
        normalised = _n(value)
        for prefix, reading in _PREFIX_RULES:
            if normalised.startswith(prefix):
                return reading
    return fallback


def known_names() -> Tuple[str, ...]:
    return tuple(sorted(_TABLE))


# ── endpoint-scoped exceptions (Hermes provider_rules.py) ─────────────────
_SCOPED_RULES = (
    {"surface": "alibaba_chat", "path": ("error", "code"), "value": "insufficient_quota",
     "messages": ("You exceeded your current quota, please check your plan and billing details.",
                  "Allocated quota exceeded, please increase your quota limit."),
     "reading": Reading(THROTTLE, billing_context_only=True,
                        why="documented token-rate message on this endpoint")},
    *({"surface": "zai_chat", "path": ("error", "code"), "value": code,
       "reading": Reading(THROTTLE, billing_context_only=True,
                          why="subscription limit that recovers at its reset")}
      for code in ("1308", "1310", "1316", "1317", "1318", "1319", "1320", "1321")),
    *({"surface": "zai_chat", "path": ("error", "code"), "value": code,
       "reading": Reading(BILLING, why="balance or subscription renewal required")}
      for code in ("1113", "1309", "1314")),
    {"surface": "google_vertex", "path": ("error", "status"), "value": "UNAUTHENTICATED",
     "reading": Reading(AUTH_REFRESH, why="Vertex OAuth can need a refresh, not a new key")},
    {"surface": "google_interactions", "path": ("error", "code"), "value": "quota_exceeded",
     "reading": Reading(THROTTLE, window=PER_DAY, why="Google Interactions names a daily allowance")},
    {"surface": "anthropic_messages", "path": ("error", "details", "error_code"),
     "value": "enforced_spend_limit_reached",
     "reading": Reading(THROTTLE, window=PER_MONTH, scope=ACCOUNT,
                        why="the organization's monthly spend cap")},
)


def _attr(obj, name):
    try:
        return getattr(obj, name, None)
    except Exception:
        return None


def api_surface(exc) -> str:
    """The API route a failed request went to — from attached request metadata only."""
    response = _attr(exc, "response")
    for request in (_attr(response, "request"), _attr(exc, "request")):
        value = _attr(request, "url")
        if value is None:
            continue
        try:
            url = urlsplit(str(value))
            if url.scheme != "https" or url.username or url.password or url.port not in (None, 443):
                continue
            host = (url.hostname or "").lower()
            path = url.path
            if host == "generativelanguage.googleapis.com":
                pieces = path.strip("/").split("/")
                if len(pieces) >= 2 and pieces[0] in ("v1", "v1beta"):
                    if pieces[1] == "interactions":
                        return "google_interactions"
                    if pieces[1] == "models":
                        return "google_native"
            if host == "aiplatform.googleapis.com" or host.endswith("-aiplatform.googleapis.com"):
                return "google_vertex"
            if host == "api.anthropic.com" and path.rstrip("/") == "/v1/messages":
                return "anthropic_messages"
            if host == "dashscope-intl.aliyuncs.com" and path.rstrip("/") == "/compatible-mode/v1/chat/completions":
                return "alibaba_chat"
            if host == "api.groq.com" and path.rstrip("/") == "/openai/v1/chat/completions":
                return "groq_chat"
            if host == "api.z.ai" and path.rstrip("/") in (
                    "/api/paas/v4/chat/completions", "/api/coding/paas/v4/chat/completions"):
                return "zai_chat"
        except Exception:
            continue
    return ""


def _surface_reading(exc, body) -> Optional[Reading]:
    surface = api_surface(exc)
    if not surface:
        return None
    for rule in _SCOPED_RULES:
        if surface != rule["surface"]:
            continue
        value = body
        for field in rule["path"]:
            value = value.get(field) if isinstance(value, dict) else None
        if value != rule["value"]:
            continue
        if "messages" in rule:
            inner = body.get("error") if isinstance(body, dict) else None
            message = inner.get("message") if isinstance(inner, dict) else None
            if not isinstance(message, str) or message.strip() not in rule["messages"]:
                continue
        if rule["reading"].family == AUTH_REFRESH:
            inner = body.get("error", {}) if isinstance(body, dict) else {}
            details = inner.get("details") if isinstance(inner, dict) else None
            if isinstance(details, list) and any(
                    isinstance(item, dict) and str(item.get("@type", "")).endswith("/google.rpc.ErrorInfo")
                    and item.get("reason") == "API_KEY_INVALID" for item in details):
                return None
        return rule["reading"]
    return None


# ── the payload, parsed ───────────────────────────────────────────────────
_MAX_TEXT = 20000


def _parse_text(text: str) -> Any:
    """The first JSON (or Python-repr) object in a text, bounded."""
    if not isinstance(text, str) or "{" not in text:
        return None
    text = text[:_MAX_TEXT]
    try:
        parsed = json.loads(text)
        if isinstance(parsed, (dict, list)) and parsed:
            return parsed
    except Exception:
        pass
    decoder = json.JSONDecoder()
    tries = 0
    for match in re.finditer(r"\{", text):
        tries += 1
        if tries > 12:
            break
        start = match.start()
        try:
            parsed, _end = decoder.raw_decode(text[start:])
            if isinstance(parsed, dict) and parsed:
                return parsed
        except Exception:
            pass
        # litellm renders an OpenAI-compatible body as a Python dict repr.
        chunk = text[start:]
        depth = 0
        end = None
        for index, char in enumerate(chunk[:_MAX_TEXT]):
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    end = index + 1
                    break
        if end:
            try:
                parsed = ast.literal_eval(chunk[:end])
                if isinstance(parsed, dict) and parsed:
                    return parsed
            except Exception:
                pass
    return None


def body_of(exc) -> Any:
    """The provider's error payload as a structure, from wherever it survived.

    In order: ``exc.body`` (the OpenAI/Anthropic SDKs parse it there), the raw
    response text, then the JSON (or Python-repr dict) litellm embeds in the
    exception message. Never raises.
    """
    try:
        body = _attr(exc, "body")
        if isinstance(body, (dict, list)) and body:
            return body
        if isinstance(body, (str, bytes)) and body:
            parsed = _parse_text(body.decode("utf-8", "replace") if isinstance(body, bytes) else body)
            if parsed is not None:
                return parsed
        response = _attr(exc, "response")
        if response is not None:
            for name in ("text", "content"):
                raw = _attr(response, name)
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8", "replace")
                if isinstance(raw, str) and raw:
                    parsed = _parse_text(raw)
                    if parsed is not None:
                        return parsed
                    break
        details = _attr(exc, "details")
        if isinstance(details, (dict, list)) and details:
            return details
        return _parse_text(str(exc))
    except Exception:
        return None


def structured_values(body: Any, exc: Any = None) -> List[str]:
    """Every machine-readable type/code string, most specific first."""
    reason: List[str] = []
    code: List[str] = []
    title: List[str] = []
    family: List[str] = []

    def take(bucket, value):
        if isinstance(value, str) and value.strip():
            bucket.append(value)

    if exc is not None:
        take(code, _attr(exc, "code"))
        take(family, _attr(exc, "type"))
        details = _attr(exc, "details")
        if isinstance(details, dict):
            take(reason, details.get("reason"))
            take(family, details.get("status"))
    if isinstance(body, dict):
        take(code, body.get("code"))
        take(family, body.get("type"))
        take(title, body.get("title"))
        inner = body.get("error")
        if isinstance(inner, dict):
            take(code, inner.get("code"))
            take(family, inner.get("type"))
            take(family, inner.get("status"))
            metadata = inner.get("metadata")
            if isinstance(metadata, dict):
                take(code, metadata.get("provider_code"))
                take(family, metadata.get("error_type"))
            details = inner.get("details")
            for member in details if isinstance(details, (list, tuple)) else ():
                if isinstance(member, dict) and str(member.get("@type") or "").endswith("/google.rpc.ErrorInfo"):
                    take(reason, member.get("reason"))
    return reason + code + title + family


_MODERATION_TYPES = frozenset({"contentpolicyviolation", "contentfilter", "refusal", "moderation"})


def is_moderation(body: Any) -> bool:
    """A content-policy block, even when an aggregator relays it.

    OpenRouter wraps a moderation refusal in the same envelope it uses for a
    relayed upstream failure (``metadata.provider_name``), but the facts inside
    — ``metadata.reasons``, ``flagged_input``, ``error_type:
    content_policy_violation`` — say the REQUEST was refused. Every key gets
    the same answer, so it is terminal: rotating would walk the pool for ever.
    """
    if not isinstance(body, dict):
        return False
    error = body.get("error")
    if not isinstance(error, dict):
        return False
    metadata = error.get("metadata")
    if isinstance(metadata, dict):
        if "flagged_input" in metadata or "reasons" in metadata:
            return True
        if _n(metadata.get("error_type")) in _MODERATION_TYPES:
            return True
    return _n(error.get("code")) in _MODERATION_TYPES or _n(error.get("type")) in _MODERATION_TYPES


def looks_like_upstream_wrapper(body: Any) -> bool:
    """An aggregator relaying somebody else's failure — by shape, never by name."""
    if not isinstance(body, dict):
        return False
    error = body.get("error")
    if not isinstance(error, dict):
        return False
    if re.match(r"^provider returned (?:an? )?error", str(error.get("message") or "").strip(), re.I):
        return True
    if error.get("type") == "upstream_error" and error.get("source") == "upstream":
        return True
    metadata = error.get("metadata")
    return isinstance(metadata, dict) and ("raw" in metadata or "provider_name" in metadata)


# ── prose, for what no field said ─────────────────────────────────────────
PERMANENT_AUTH_PATTERNS = (
    re.compile(r"api[\s_-]*key[\s_-]*not[\s_-]*valid", re.I),
    re.compile(r"invalid[\s_-]*(?:api[\s_-]*)?key", re.I),
    re.compile(r"(?:api[\s_-]*)?key[\s_-]*(?:has[\s_-]*been[\s_-]*)?(?:expired|revoked|deleted|disabled)", re.I),
    re.compile(r"invalid[\s_-]*authentication", re.I),
    re.compile(r"incorrect[\s_-]*api[\s_-]*key", re.I),
    re.compile(r"(?:api[\s_-]*)?key\b[^.\n]{0,24}?\b(?:is|was)\s+(?:no[\s_-]*longer[\s_-]*valid|not[\s_-]*valid|invalid)", re.I),
    re.compile(r"\baccount[\s_-]+suspended\b", re.I),
)
DENIAL_PATTERNS = (
    re.compile(r"permission[\s_-]*denied", re.I),
    re.compile(r"denied[\s_-]*access", re.I),
    re.compile(r"consumer[\s_-]*suspended", re.I),
    re.compile(r"service[\s_-]*disabled", re.I),
    re.compile(r"api[\s_-]*key[\s_-]*service[\s_-]*blocked", re.I),
    re.compile(r"has[\s_-]*not[\s_-]*been[\s_-]*used[\s_-]*in[\s_-]*project", re.I),
    re.compile(r"is[\s_-]*disabled[\s_-]*for[\s_-]*this[\s_-]*project", re.I),
    re.compile(r"model[\s_-]*not[\s_-]*(?:authorized|available)", re.I),
    re.compile(r"does[\s_-]*not[\s_-]*(?:yet[\s_-]*)?include[\s_-]*access[\s_-]*to", re.I),
    re.compile(r"allowed[\s_-]*only[\s_-]*from[\s_-]*approved[\s_-]*ip[\s_-]*ranges", re.I),
)
BILLING_PATTERNS = (
    re.compile(r"billing\b[\w\s_-]{0,24}?\b(?:enabled|required|disabled|suspended)", re.I),
    re.compile(r"free[\s_-]*tier.*not[\s_-]*available", re.I),
    re.compile(r"consumer[\s_-]*(?:backend|quota).*disabled", re.I),
    re.compile(r"credit[\s_-]*balance[\s_-]*is[\s_-]*too[\s_-]*low", re.I),
    re.compile(r"insufficient[\s_-]*(?:quota|credits?|funds?|balance)", re.I),
    re.compile(r"(?:out[\s_-]*of|no)[\s_-]*credits?", re.I),
    re.compile(r"payment[\s_-]*required", re.I),
    re.compile(r"free[\s_-]*tier\b[^.\n]{0,40}?\bexhausted", re.I),
    re.compile(r"included[\s_-]*credits", re.I),
    re.compile(r"account[\s_-]*is[\s_-]*in[\s_-]*good[\s_-]*standing", re.I),
    re.compile(r"(?:package|plan|subscription)\b[^.\n]{0,40}?\bhas[\s_-]*expired", re.I),
    re.compile(r"reached[\s_-]*your[\s_-]*specified[\s_-]*api[\s_-]*usage[\s_-]*limits?", re.I),
    # one-api gateways, as a 403 in the owner's own log (04/09/2026): "User's
    # credit limit is insufficient". The Hermes port reads this through its
    # carousel's `credit limit` marker; here it is one pattern in one place.
    re.compile(r"credit[\s_-]*limit[\s_-]*is[\s_-]*insufficient", re.I),
    re.compile(r"balance[\s_-]*is[\s_-]*insufficient", re.I),
    # Codex, `usage_not_included`: the ChatGPT plan does not include Codex at
    # all, and no wait changes a plan. The answer key calls it billing. The
    # field itself is in the catalogue above (v1.8.1.4, as in Hermes); this
    # sentence stays for a refusal that arrives without the field.
    re.compile(r"to[\s_-]*use[\s_-]*codex[\s_-]*with[\s_-]*your[\s_-]*chatgpt[\s_-]*plan", re.I),
)
AMBIGUOUS_BILLING_PATTERNS = (re.compile(r"exceeded your current quota.*billing", re.I | re.S),)
RESOURCE_EXHAUSTED_BOILERPLATE = re.compile(r"resource[\s_-]*(?:has|have)[\s_-]*been[\s_-]*exhausted", re.I)
QUOTA_PATTERNS = (
    re.compile(r"rate[\s_-]*limit", re.I),
    re.compile(r"too[\s_-]*many[\s_-]*requests", re.I),
    re.compile(r"quota[\s_-]*exceeded", re.I),
    re.compile(r"resource[\s_-]*exhausted", re.I),
    RESOURCE_EXHAUSTED_BOILERPLATE,
    re.compile(r"exceeded[\s_-]*your[\s_-]*current[\s_-]*quota", re.I),
    re.compile(r"throttl", re.I),
    re.compile(r"usage[\s_-]*limit[\s_-]*reached", re.I),
    re.compile(r"limit[\s_-]*exhausted", re.I),
    re.compile(r"concurren\w*[\s_-]*limit", re.I),
    re.compile(r"tokens?[\s_-]*per[\s_-]*(?:min|minute|hour|day|week|month)", re.I),
    re.compile(r"quota[\s_-]*left", re.I),
)
BUSY_PATTERNS = (
    re.compile(r"overloaded", re.I),
    re.compile(r"(?:over|at|exceeded)[\s_-]*capacity", re.I),
    re.compile(r"(?:server|service|model)[\s_-]*(?:is[\s_-]*)?(?:busy|unavailable)", re.I),
)
_TYPE_THROTTLE = re.compile(r"rate[_\s-]*limit|too[_\s-]*many[_\s-]*requests|throttl|"
                            r"resource[_\s-]*exhausted|quota[_\s-]*exceeded", re.I)
_TYPE_BUSY = re.compile(r"overload|capacity|unavailable|server[_\s-]*error", re.I)
_RETRY_INFO = re.compile(r"retry[\s_-]*(?:info|delay|after)", re.I)


# Advice a HOST welds onto a provider's sentence is not evidence: the host is
# not a party to the failure. Hermes' Gemini adapter appends a free-tier
# footer ("...the free tier is exhausted... a billing-enabled project...")
# that trips two billing patterns and turned a 7-second throttle into an hour
# at account scope over there. Agent Zero appends nothing like it today, but a
# relayed or copied message can carry it, so it comes off before any pattern
# reads a word — the same two anchors the Hermes port strips.
_HOST_APPENDED_PROSE = (
    re.compile(r"\n\nYour Google API key is on the free tier\b.*", re.S | re.I),
    re.compile(r"\n\nGoogle Gemini rejected this API key.s type\b.*", re.S | re.I),
)


def strip_host_prose(text: str) -> str:
    if not text:
        return text
    for pattern in _HOST_APPENDED_PROSE:
        text = pattern.sub("", text)
    return text


def _matches(patterns, *texts) -> bool:
    haystack = " ".join(t for t in texts if t)
    return bool(haystack) and any(p.search(haystack) for p in patterns)


# ── durations and moments ─────────────────────────────────────────────────
_DURATION_PART = re.compile(
    r"(\d+(?:\.\d+)?|\.\d+)\s*"
    r"(milliseconds?|millis|msecs?|ms|hours?|hrs?|h|minutes?|mins?|m|seconds?|secs?|s)(?![a-z])",
    re.IGNORECASE,
)
_UNIT_SECONDS = {
    "ms": 0.001, "msec": 0.001, "msecs": 0.001, "milli": 0.001, "millis": 0.001,
    "millisecond": 0.001, "milliseconds": 0.001,
    "h": 3600.0, "hr": 3600.0, "hrs": 3600.0, "hour": 3600.0, "hours": 3600.0,
    "m": 60.0, "min": 60.0, "mins": 60.0, "minute": 60.0, "minutes": 60.0,
    "s": 1.0, "sec": 1.0, "secs": 1.0, "second": 1.0, "seconds": 1.0,
}


def parse_duration(text) -> Optional[float]:
    """``6m 11.52s``, ``1500ms``, ``2h 30m``, ``45s``, a bare number."""
    if text is None or isinstance(text, bool):
        return None
    raw = str(text).strip()
    if not raw:
        return None
    if re.fullmatch(r"(?:\d+(?:\.\d+)?|\.\d+)", raw):
        return float(raw)
    total, found, position = 0.0, False, 0
    for match in _DURATION_PART.finditer(raw):
        if raw[position:match.start()].strip():
            return None
        value, unit = match.groups()
        position = match.end()
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        total += number * _UNIT_SECONDS.get((unit or "s").lower(), 1.0)
        found = True
    return total if found and not raw[position:].strip() else None


def _epoch_from_number(number: float) -> Optional[float]:
    if number <= 0:
        return None
    if number >= 1e12:
        return number / 1000.0
    if number >= 1e9:
        return number
    return None


def parse_absolute(value) -> Optional[float]:
    """An absolute reset: RFC 3339, HTTP-date, or epoch seconds/milliseconds."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        try:
            return _epoch_from_number(float(value))
        except (OverflowError, ValueError):
            return None
    raw = str(value).strip().strip("\"'")
    if not raw:
        return None
    if re.fullmatch(r"\d{9,}(?:\.\d+)?", raw):
        try:
            return _epoch_from_number(float(raw))
        except ValueError:
            return None
    iso = raw[:-1] + "+00:00" if raw.endswith(("Z", "z")) else raw
    iso = re.sub(r"\s+(?:UTC|GMT)$", "+00:00", iso, flags=re.I)
    try:
        moment = datetime.fromisoformat(iso)
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        return moment.timestamp()
    except ValueError:
        pass
    try:
        moment = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if moment is None:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.timestamp()


def _bounded(seconds) -> Optional[float]:
    try:
        value = float(seconds)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(value) or not (0 < value <= MAX_RELATIVE_DELAY_SECONDS):
        return None
    return value


def _from_absolute(epoch, now) -> Optional[float]:
    if epoch is None:
        return None
    delta = epoch - now
    return delta if 0 < delta <= MAX_ABSOLUTE_HORIZON_SECONDS else None


def _millis(value) -> Optional[float]:
    try:
        return _bounded(float(value) / 1000.0)
    except (TypeError, ValueError, OverflowError):
        return None


_RESET_HEADER = re.compile(r"(?:ratelimit|rate-limit|quota|usage).*reset|reset.*(?:ratelimit|rate-limit|quota)", re.I)
_RETRY_KEY = re.compile(r"retry.?(?:delay|after|in)|reset.?(?:at|after|in|time)|" + _RESET_HEADER.pattern, re.I)


def headers_of(exc) -> Any:
    for holder in (exc, _attr(exc, "response")):
        for name in ("headers", "response_headers", "litellm_response_headers"):
            value = _attr(holder, name)
            if value:
                return value
    return None


def _header_items(headers) -> List[Tuple[str, Any]]:
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


def _from_exception(exc) -> Tuple[Optional[float], str]:
    retry_after = _attr(exc, "retry_after")
    if retry_after is not None:
        seconds = _bounded(retry_after)
        if seconds is None:
            seconds = _bounded(parse_duration(retry_after))
        if seconds is not None:
            return seconds, "exception.retry_after"
    retry_delay = _attr(exc, "retry_delay")
    if retry_delay is not None:
        if hasattr(retry_delay, "seconds") or hasattr(retry_delay, "nanos"):
            try:
                value = float(getattr(retry_delay, "seconds", 0) or 0) + float(getattr(retry_delay, "nanos", 0) or 0) / 1e9
            except (TypeError, ValueError):
                value = None
            seconds = _bounded(value)
            if seconds is not None:
                return seconds, "exception.retry_delay"
        seconds = _bounded(parse_duration(retry_delay))
        if seconds is not None:
            return seconds, "exception.retry_delay"
    return None, ""


def _from_headers(headers, now) -> Tuple[Optional[float], str]:
    items = _header_items(headers)
    if not items:
        return None, ""
    lowered = {name.strip().lower(): value for name, value in items}
    milliseconds = _millis(lowered.get("retry-after-ms"))
    if milliseconds is not None:
        return milliseconds, "header.retry-after-ms"
    raw = lowered.get("retry-after")
    if raw is not None:
        seconds = _bounded(raw)
        if seconds is None:
            seconds = _from_absolute(parse_absolute(raw), now)
        if seconds is None:
            seconds = _bounded(parse_duration(raw))
        if seconds is not None:
            return seconds, "header.retry-after"
    best, best_name = None, ""
    for name, value in lowered.items():
        if name == "retry-after" or not _RESET_HEADER.search(name):
            continue
        seconds = _from_absolute(parse_absolute(value), now)
        if seconds is None:
            seconds = _bounded(parse_duration(value))
        if seconds is not None and (best is None or seconds > best):
            best, best_name = seconds, name
    return (best, f"header.{best_name}") if best is not None else (None, "")


def _body_fields(node, depth=0):
    if depth > 8:
        return
    if isinstance(node, dict):
        for key, value in node.items():
            yield str(key), value, node
            yield from _body_fields(value, depth + 1)
    elif isinstance(node, (list, tuple)):
        for value in node:
            yield from _body_fields(value, depth + 1)


def _duration_message(value) -> Optional[float]:
    if not isinstance(value, dict) or ("seconds" not in value and "nanos" not in value):
        return None
    try:
        return float(value.get("seconds", 0) or 0) + float(value.get("nanos", 0) or 0) / 1e9
    except (TypeError, ValueError):
        return None


def _body_reset(value, now, *, absolute) -> Optional[float]:
    if value is None or isinstance(value, (bool, dict, list, tuple)):
        return None
    try:
        if absolute:
            return _from_absolute(parse_absolute(value), now)
        return _bounded(value)
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _from_body(body, now) -> Tuple[Optional[float], str]:
    if not isinstance(body, (dict, list, tuple)):
        return None, ""
    try:
        pairs = list(_body_fields(body))
    except Exception:
        return None, ""
    for key, value, siblings in pairs:
        norm = key.lower().replace("_", "-")
        if norm in ("retry-after", "retry-after-ms"):
            for sibling, raw in siblings.items():
                if str(sibling).lower().replace("_", "-") == "retry-after-ms":
                    milliseconds = _millis(raw)
                    if milliseconds is not None:
                        return milliseconds, f"body.{sibling}"
            if norm == "retry-after-ms":
                continue
        if key in ("resets_at", "resets_in_seconds"):
            candidates = []
            for name in ("resets_at", "resets_in_seconds"):
                raw = siblings.get(name)
                if isinstance(raw, bool) or raw is None:
                    continue
                seconds = _body_reset(raw, now, absolute=(name == "resets_at"))
                if seconds is not None:
                    candidates.append((seconds, f"body.{name}"))
            if candidates:
                return max(candidates, key=lambda item: item[0])
            continue
        if re.fullmatch(r"reset[ _-]?at", key, re.I):
            seconds = _body_reset(value, now, absolute=True)
            if seconds is not None:
                return seconds, f"body.{key}"
            continue
        if not _RETRY_KEY.search(key):
            continue
        if _RESET_HEADER.search(key):
            seconds = _body_reset(value, now, absolute=True)
            if seconds is not None:
                return seconds, f"body.{key}"
        if isinstance(value, dict):
            seconds = _bounded(_duration_message(value))
            if seconds is not None:
                return seconds, f"body.{key}"
            continue
        if isinstance(value, (list, tuple)):
            continue
        seconds = _bounded(parse_duration(value))
        if seconds is None:
            seconds = _from_absolute(parse_absolute(value), now)
        if seconds is not None:
            return seconds, f"body.{key}"
    return None, ""


_UNIT_TOKEN = (r"(?:milliseconds?|millis|msecs?|ms|hrs?|hours?|h|mins?|minutes?|m"
               r"|secs?|seconds?|s)(?![a-z])")
_DURATION_EXPR = rf"(?:\d+(?:\.\d+)?|\.\d+)\s*{_UNIT_TOKEN}(?:\s*(?:\d+(?:\.\d+)?|\.\d+)\s*{_UNIT_TOKEN})*"
_BARE_SECONDS = r"(?:\d+(?:\.\d+)?|\.\d+)(?![\w.:/\-])"
_TEXT_RETRY = re.compile(
    rf"(?:retry[_\s-]*(?:after|delay|in)|try\s+again\s+in|available\s+in|resets?\s+in|wait\s+(?:for\s+)?)"
    rf"[:\s\"']*({_DURATION_EXPR}|{_BARE_SECONDS})",
    re.IGNORECASE,
)
_ABS_MOMENT = (r"\d{4}-\d{2}-\d{2}(?:(?:[T ]|\s+at\s+)\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?)?"
               r"(?:\s*(?:Z|[+-]\d{2}:?\d{2}|UTC|GMT))?|\d{10,13}")
_TEXT_RESET_MOMENT = re.compile(
    rf"(?:resets?|renews?|refreshes|available|back|try\s+again|come\s+back|regain\s+access)"
    rf"\s+(?:at|on)\s*[:\"']*\s*({_ABS_MOMENT})",
    re.IGNORECASE,
)


def _from_text(message) -> Tuple[Optional[float], str]:
    if not message:
        return None, ""
    match = _TEXT_RETRY.search(str(message))
    if not match:
        return None, ""
    seconds = _bounded(parse_duration(match.group(1)))
    return (seconds, "text") if seconds is not None else (None, "")


def _moment_from_text(message, now) -> Tuple[Optional[float], str]:
    if not message:
        return None, ""
    match = _TEXT_RESET_MOMENT.search(str(message))
    if not match:
        return None, ""
    seconds = _from_absolute(parse_absolute(re.sub(r"\s+at\s+", " ", match.group(1), flags=re.I)), now)
    return (seconds, "text.reset_at") if seconds is not None else (None, "")


def delay_candidates(*, message="", body=None, headers=None, exc=None, now=0.0) -> List[Tuple[float, str]]:
    """Every stated wait, strongest first: exception, headers, body, a dated moment, a sentence."""
    readings = (
        _from_exception(exc),
        _from_headers(headers, now),
        _from_body(body, now),
        _moment_from_text(message, now),
        _from_text(message),
    )
    return [(seconds, source) for seconds, source in readings if seconds is not None]


# ── window and scope ──────────────────────────────────────────────────────
_ACCOUNT_MARKERS = ("insufficientquota", "insufficient_quota", "billinghardlimit",
                    "creditbalanceistoolow", "outofcredits", "nocreditsremaining")
_PER_MONTH_MARKERS = ("permonth", "monthly", "requestspermonth", "/month")
_PER_WEEK_MARKERS = ("perweek", "weekly", "requestsperweek", "/week", "rpw")
_PER_DAY_MARKERS = ("perday", "daily", "requestsperday", "tokensperday", "/day", "rpd",
                    "quotaexceededperday", "dailylimit")
_PER_HOUR_MARKERS = ("perhour", "hourly", "requestsperhour", "/hour", "rph")
_PER_MINUTE_MARKERS = ("perminute", "requestsperminute", "tokensperminute", "/minute",
                       "rpm", "tpm", "permin", "requestspermin", "tokenspermin")
_PER_MODEL_MARKERS = ("permodel", "perbasemodel", "modelperday", "permodelperminute")
_SHARED_MARKERS = ("peruser", "peraccount", "perorganization", "perworkspace", "perapikey",
                   "perproject", "freemodels", "accountwide", "organizationwide",
                   "usagelimitreached")
_MODEL_DIMENSION_KEYS = frozenset({"model", "modelid", "basemodel"})
TOKEN_UNIT_MARKERS = ("inputtoken", "outputtoken", "tokencount")
_WINDOW_MARKERS = (
    (ACCOUNT, _ACCOUNT_MARKERS),
    (PER_WEEK, _PER_WEEK_MARKERS),
    (PER_MONTH, _PER_MONTH_MARKERS),
    (PER_DAY, _PER_DAY_MARKERS),
    (PER_HOUR, _PER_HOUR_MARKERS),
    (PER_MINUTE, _PER_MINUTE_MARKERS),
)


def _hay(*parts) -> str:
    return re.sub(r"[\s_\-]+", "", " ".join(str(p) for p in parts if p)).lower()


def _mentions(hay, markers) -> bool:
    return any(m.replace("_", "").replace("-", "").replace("/", "") in hay for m in markers)


def _body_parts(body):
    out, names_model = [], False
    if isinstance(body, (dict, list, tuple)):
        try:
            for key, value, _siblings in _body_fields(body):
                out.append(str(key))
                if isinstance(value, str):
                    out.append(value)
                    if value.strip() and _hay(key) in _MODEL_DIMENSION_KEYS:
                        names_model = True
        except Exception:
            pass
    return out, names_model


def detect_window(message="", body=None) -> str:
    parts, _ = _body_parts(body)
    hay = _hay(message, *parts)
    if not hay:
        return UNKNOWN
    for window, markers in _WINDOW_MARKERS:
        if _mentions(hay, markers):
            if window == PER_MINUTE and _mentions(hay, TOKEN_UNIT_MARKERS):
                return TOKENS_PER_MINUTE
            return window
    return UNKNOWN


def detect_scope(message="", body=None, *, billing_context_only=False) -> str:
    parts, names_model = _body_parts(body)
    hay = _hay(message, *parts)
    if not hay:
        return UNKNOWN
    if names_model or _mentions(hay, _PER_MODEL_MARKERS):
        return PER_MODEL
    if _mentions(hay, _SHARED_MARKERS) or (not billing_context_only and _mentions(hay, _ACCOUNT_MARKERS)):
        return ACCOUNT
    return UNKNOWN


def _names_a_wait(*, message, body, body_text, headers, exc, now) -> bool:
    window = detect_window(message, body)
    if window not in (UNKNOWN, ACCOUNT):
        return True
    if delay_candidates(message=message, body=body, headers=headers, exc=exc, now=now):
        return True
    return bool(_RETRY_INFO.search(body_text))


def _size(*, message, body, headers, exc, now, window_hint="", scope_hint="",
          billing_context_only=False) -> Optional[Judgment]:
    """Hermes ``compute_reset_at``: the window and the stated wait, reconciled."""
    window = detect_window(message, body)
    if billing_context_only and window == ACCOUNT:
        window = UNKNOWN
    if window == UNKNOWN and window_hint in WINDOW_DEFAULTS:
        window = window_hint
    scope = detect_scope(message, body, billing_context_only=billing_context_only)
    if scope == UNKNOWN and scope_hint in (ACCOUNT, PER_MODEL):
        scope = scope_hint
    candidates = delay_candidates(message=message, body=body, headers=headers, exc=exc, now=now)
    delay, source = candidates[0] if candidates else (None, "")
    long_stated = False
    if window in LONG_WINDOWS:
        absolute = max((c for c in candidates if c[1] in ("body.reset_at", "body.resets_at", "text.reset_at")),
                       key=lambda c: c[0], default=None)
        if absolute is not None and not (delay is not None and delay >= WINDOW_DEFAULTS[PER_DAY]
                                         and delay > absolute[0]):
            seconds, source = absolute
            long_stated = True
        elif delay is not None and delay >= WINDOW_DEFAULTS[PER_DAY]:
            seconds = min(delay, MAX_ABSOLUTE_HORIZON_SECONDS)
            long_stated = True
        else:
            stated = max((r for r in candidates[1:] if r[0] >= WINDOW_DEFAULTS[PER_DAY]),
                         key=lambda r: r[0], default=None)
            if stated is not None:
                seconds, source = min(stated[0], MAX_ABSOLUTE_HORIZON_SECONDS), stated[1]
                long_stated = True
            else:
                seconds, source = WINDOW_DEFAULTS[window], "window"
    elif delay is not None:
        seconds = delay
    elif window in WINDOW_DEFAULTS:
        seconds, source = WINDOW_DEFAULTS[window], "window"
    else:
        return Judgment(THROTTLE, window=window, scope=scope, seconds=None, source="")
    if window == ACCOUNT:
        scope = ACCOUNT
    seconds = max(MIN_BENCH_SECONDS, min(float(seconds), MAX_ABSOLUTE_HORIZON_SECONDS))
    return Judgment(THROTTLE, window=window, scope=scope, seconds=seconds, source=source,
                    long_stated=long_stated)


def judge(exc, *, status=None, message=None, headers=None, now=0.0) -> Optional[Judgment]:
    """The evidence's verdict, in the Hermes reader's order — or ``None``.

    ``None`` means the payload carries nothing this reader would stake a
    verdict on (a bare 401 or 403, a plain 5xx, a timeout, a malformed request),
    and the engine's own rules decide, exactly as they did before 1.8.1.0.
    Families returned: upstream, auth_dead, billing, denial, throttle.
    """
    try:
        return _judge(exc, status=status, message=message, headers=headers, now=now)
    except Exception:
        return None


def _judge(exc, *, status, message, headers, now):
    body = body_of(exc)
    message = strip_host_prose(str(message if message is not None else exc)[:_MAX_TEXT])
    body_text = ""
    if body is not None:
        try:
            body_text = json.dumps(body, ensure_ascii=False)[:_MAX_TEXT]
        except Exception:
            body_text = str(body)[:_MAX_TEXT]
        body_text = strip_host_prose(body_text)
    if headers is None:
        headers = headers_of(exc)
    # Headers an aggregator folds into the body (OpenRouter metadata.headers)
    # are read the same way as real ones, by the body walker.
    status = status if isinstance(status, int) else None
    if status == 200:
        return None

    # A refused request is not a verdict about any key: see `catalog_terminal`.
    if is_moderation(body):
        return None

    # 0. Somebody else's failure, relayed: never evidence about our key.
    if looks_like_upstream_wrapper(body):
        return Judgment(UPSTREAM, why="an aggregator relaying another provider's failure")

    # 0.5 What the provider said in a field, before any sentence.
    status_reading = _STATUS_READINGS.get(status) if status is not None else None
    if status_reading is not None and status_reading.family == BILLING:
        if delay_candidates(message=message, body=body, headers=headers, exc=exc, now=now):
            status_reading = None
    class_reading = _EXCEPTION_CLASSES.get(_n(type(exc).__name__))
    catalog_reading = (_surface_reading(exc, body)
                       or look_up(*structured_values(body, exc))
                       or status_reading)
    if catalog_reading is None and class_reading is not None:
        catalog_reading = class_reading
    if catalog_reading is None and _matches((RESOURCE_EXHAUSTED_BOILERPLATE,), message, body_text):
        catalog_reading = Reading(THROTTLE, why="Google's generic RESOURCE_EXHAUSTED wording")

    wastebasket = False
    catalog_throttle = False
    if catalog_reading is not None:
        family = catalog_reading.family
        if family == AUTH_REFRESH:
            key_specific = tuple(p for p in PERMANENT_AUTH_PATTERNS if "authentication" not in p.pattern)
            if _matches(key_specific, message, body_text):
                return Judgment(AUTH_DEAD, why="explicit invalid-key evidence")
            return None
        if family in (SERVER, TIMEOUT, TERMINAL, NOT_A_FAILURE):
            if family == TERMINAL and not catalog_reading.certain:
                wastebasket = True
            else:
                return None
        elif family == AUTH_DEAD:
            return Judgment(AUTH_DEAD, why=catalog_reading.why)
        elif family == BILLING:
            return Judgment(BILLING, window=catalog_reading.window or ACCOUNT,
                            scope=catalog_reading.scope or ACCOUNT, why=catalog_reading.why)
        elif family == DENIAL:
            return Judgment(DENIAL, scope=PER_MODEL, why=catalog_reading.why)
        else:
            catalog_throttle = True

    # 1. A key the provider says is not a key.
    if not wastebasket and _matches(PERMANENT_AUTH_PATTERNS, message, body_text):
        return Judgment(AUTH_DEAD, why="the provider rejected the credential itself")

    # 2. Out of money, or on a plan that forbids this call.
    ambiguous = _matches(AMBIGUOUS_BILLING_PATTERNS, message, body_text)
    billing_context_only = bool(catalog_reading and catalog_reading.billing_context_only)
    if (_matches(BILLING_PATTERNS, message, body_text) and not billing_context_only) or (
            ambiguous and not catalog_throttle and not _names_a_wait(
                message=message, body=body, body_text=body_text, headers=headers, exc=exc, now=now)):
        return Judgment(BILLING, window=ACCOUNT, scope=ACCOUNT, why="account-level limit, not a throttle")

    # 3. Deliberate refusal of this key/model pairing, stated in words.
    if not wastebasket and _matches(DENIAL_PATTERNS, message, body_text):
        return Judgment(DENIAL, scope=PER_MODEL, why="access denied for this key/model")

    if wastebasket:
        return None

    # 4. A bare 401/403 names nothing: the engine's own credential rules decide.
    if status in (400, 401, 403):
        return None

    # 5. Busy, not spent — unless a structured type contradicts the prose.
    tokens = " ".join(structured_values(body, exc) + [type(exc).__name__])
    busy_prose = _matches(BUSY_PATTERNS, message, body_text)
    contradicted = busy_prose and bool(tokens) and (
        _TYPE_THROTTLE.search(tokens) is not None and _TYPE_BUSY.search(tokens) is None)
    if status in (500, 502, 503, 504, 529) or (busy_prose and not contradicted):
        return None

    if not (status == 429 or catalog_throttle or _matches(QUOTA_PATTERNS, message, body_text)):
        return None

    # 6. A throttle: how long.
    sized = _size(message=message, body=body, headers=headers, exc=exc, now=now,
                  window_hint=catalog_reading.window if catalog_reading is not None else "",
                  scope_hint=catalog_reading.scope if catalog_reading is not None else "",
                  billing_context_only=billing_context_only)
    if sized.seconds is None:
        if contradicted or catalog_throttle:
            window = sized.window
            if window == UNKNOWN and catalog_reading is not None:
                window = catalog_reading.window
            return Judgment(THROTTLE, window=window, scope=sized.scope, seconds=None, source="",
                            why="a throttle with nothing to size it by")
        return None
    spent_allowance = sized.window in (PER_WEEK, PER_MONTH) and sized.source == "window"
    if sized.window == ACCOUNT or spent_allowance:
        return Judgment(BILLING, window=sized.window, scope=ACCOUNT, seconds=sized.seconds,
                        source=sized.source, why="an allowance that is spent, not a rate")
    return sized


def catalog_terminal(exc) -> bool:
    """True when a field the provider filled in names the REQUEST as the problem.

    Only a *certain* row: ``context_length_exceeded``, ``model_not_found``,
    ``invalid_prompt`` and friends. The wastebasket families
    (``invalid_request_error``, ``INVALID_ARGUMENT``) are left to the status
    code, because a specific field beside them is routinely the real fact.
    """
    try:
        body = body_of(exc)
        if is_moderation(body):
            return True
        if looks_like_upstream_wrapper(body):
            return False
        reading = look_up(*structured_values(body, exc))
        return reading is not None and reading.family == TERMINAL and reading.certain
    except Exception:
        return False


def window_label(exc) -> str:
    """The counter the payload names, in KAME's words — for the events and /kame."""
    try:
        return detect_window(str(exc), body_of(exc))
    except Exception:
        return UNKNOWN

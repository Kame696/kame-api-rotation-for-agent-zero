"""v1.8.1.0 — the evidence reader both ports judge with.

`kame_evidence.py` is the Hermes port's error reader, ported: its catalogue of
provider field values, its prose tables, its reset-reading cascade and its
order (a field outranks a sentence, a provider outranks the library). This file
holds Agent Zero to the verdicts it must reach, payload by payload, through the
one function the carousel calls: `_kame_decide_failure` (after the loop's own
terminal check).

Every payload below is written the way the provider sends it. The rows come
from the independent answer key the Hermes port is graded on (68 error shapes,
12 providers and gateways); where the Hermes port itself still disagrees with
that key and this port does not, the row says so.

Run:  python tests/test_v1_8_1_0_evidence.py
"""
import json
import os
import sys
import time
import types


def _stub(name):
    m = types.ModuleType(name)
    sys.modules[name] = m
    return m


for _n in ("openai", "langchain_core", "helpers"):
    _stub(_n)
_lit = _stub("litellm")
_lit.suppress_debug_info = False
_lit.acompletion = lambda *a, **k: None
_lc = _stub("langchain_core.messages")


class _Msg:
    def __init__(self, content=""):
        self.content = content


_lc.SystemMessage = _lc.HumanMessage = _Msg
_ps = _stub("helpers.print_style")


class _PrintStyle:
    def __init__(self, *a, **k): pass
    def print(self, *a, **k): pass
    warning = error = success = staticmethod(lambda *a, **k: None)


_ps.PrintStyle = _PrintStyle
_errs = _stub("helpers.errors")
for _e in ("InterventionException", "RepairableException", "HandledException"):
    setattr(_errs, _e, type(_e, (Exception,), {}))

HERE = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, HERE)
import kame_engine as K  # noqa: E402
import kame_evidence as KE  # noqa: E402

K.set_log_level("silent")
_failures = []


def check(name, cond):
    print(("PASS  " if cond else "FAIL  ") + name)
    if not cond:
        _failures.append(name)


_classes = {}


def err(status, message, body=None, cls="APIStatusError", headers=None):
    """An exception shaped like what the OpenAI/litellm stack hands Agent Zero."""
    kind = _classes.setdefault(cls, type(cls, (Exception,), {}))
    exc = kind(message)
    exc.status_code = status
    if body is not None:
        exc.body = body
    exc.response = types.SimpleNamespace(
        text=json.dumps(body) if body is not None else "", status_code=status, headers=headers or {})
    return exc


def fresh(identity, keys=("A", "B")):
    with K._KAME_LOCK:
        for name in ("_KAME_KEY_HEALTH", "_KAME_STATED_RL", "_KAME_NO_ANSWER_SINCE", "_KAME_STORM", "_KAME_TALLY"):
            getattr(K, name).clear()
    K._get_identity_state(identity, list(keys))


def decide(identity, exc, key="A"):
    """What the carousel does: terminal first, then the one decision function."""
    fresh(identity)
    if K._is_terminal_error(exc):
        return ("terminal", None)
    applied, kind, _sc, _label, sized_by = K._kame_decide_failure(identity, key, exc, elapsed=None)
    return (kind, round(applied, 1), sized_by)


def _g(code, status, message, details=None):
    e = {"code": code, "message": message, "status": status}
    if details:
        e["details"] = details
    return {"error": e}


def _oa(message, type_=None, code=None, **extra):
    e = {"message": message}
    if type_ is not None:
        e["type"] = type_
    if code is not None:
        e["code"] = code
    e.update(extra)
    return {"error": e}


GEM = "gemini:gemini-3.8-flash"
OR = "openrouter:deepseek/deepseek-v4"
OAI = "openai:gpt-6"
CODEX = "openai-codex:gpt-6-astra"
GW = "custom:glm-5"
ANT = "anthropic:claude-5"

print("--- 1. a field outranks a sentence ---")
r = decide(GEM, err(400, "API key not valid. Please pass a valid API key.", _g(400, "INVALID_ARGUMENT",
        "API key not valid. Please pass a valid API key.",
        [{"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": "API_KEY_INVALID"}]), "BadRequestError"))
check("Gemini 400 INVALID_ARGUMENT + API_KEY_INVALID is a dead key, not a malformed request (%s)" % (r,),
      r[0] == "revoked")
r = decide(GEM, err(401, "Request had invalid authentication credentials. Expected OAuth 2 access token.",
        _g(401, "UNAUTHENTICATED", "Request had invalid authentication credentials. Expected OAuth 2 access token.",
           [{"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": "ACCESS_TOKEN_TYPE_UNSUPPORTED"}]),
        "AuthenticationError"))
check("a legacy Standard key (ACCESS_TOKEN_TYPE_UNSUPPORTED) leaves rotation (%s)" % (r,), r[0] == "revoked")
r = decide(OAI, err(429, "You exceeded your current quota, please check your plan and billing details.",
        _oa("You exceeded your current quota, please check your plan and billing details.",
            type_="insufficient_quota", code="insufficient_quota"), "RateLimitError"))
check("OpenAI's out-of-credit sentence WITH its field is billing, an hour (%s)" % (r,),
      r[0] == "insufficient_quota" and r[1] == 3600.0)
r = decide(GEM, err(429, "Gemini HTTP 429 (RESOURCE_EXHAUSTED): You exceeded your current quota, please check your plan and billing details.",
        _g(429, "RESOURCE_EXHAUSTED", "You exceeded your current quota, please check your plan and billing details.",
           [{"@type": "type.googleapis.com/google.rpc.QuotaFailure",
             "violations": [{"quotaId": "GenerateRequestsPerMinutePerProjectPerModel-FreeTier"}]},
            {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "21s"}]), "RateLimitError"))
check("Google's per-minute throttle with the SAME sentence rests the 21s it named (%s)" % (r,),
      r[0] == "per_minute" and r[1] == 21.0 and r[2] == "provider")

print("\n--- 2. an empty balance, however it is spelled ---")
cases = [
    ("OpenRouter 402 payment_required", OR, err(402, "Insufficient credits. Add more credits and retry the request.",
        {"error": {"code": 402, "message": "Insufficient credits. Add more credits and retry the request.",
                   "metadata": {"error_type": "payment_required"}}})),
    ("Anthropic 402 billing_error", ANT, err(402, "Your credit balance is too low to access the Anthropic API.",
        {"type": "error", "error": {"type": "billing_error", "message": "Your credit balance is too low to access the Anthropic API."}})),
    ("one-api 403 insufficient_user_quota", GW, err(403, "Your account balance is insufficient. Please recharge your account.",
        _oa("Your account balance is insufficient. Please recharge your account.", code="insufficient_user_quota"),
        "PermissionDeniedError")),
    ("the owner's own 403 'credit limit is insufficient'", GW, err(403,
        "User's credit limit is insufficient, remaining credit limit: $0.012345 (request id: 123)",
        {"error": {"message": "User's credit limit is insufficient, remaining credit limit: $0.012345 (request id: 123)",
                   "type": "new_api_error", "code": "insufficient_user_quota"}}, "PermissionDeniedError")),
    ("Gemini 429 'Billing account not enabled.'", GEM, err(429, "Gemini HTTP 429: Billing account not enabled.",
        cls="RateLimitError")),
    ("Codex usage_not_included (a plan, not a wait)", CODEX, err(429, "To use Codex with your ChatGPT plan, upgrade to Plus.",
        _oa("To use Codex with your ChatGPT plan, upgrade to Plus.", type_="usage_not_included"), "RateLimitError")),
]
for name, identity, exc in cases:
    r = decide(identity, exc)
    check("%s -> out of credit, an hour (%s)" % (name, r), r[0] == "insufficient_quota" and r[1] == 3600.0)

r = decide(ANT, err(400, "You have reached your specified API usage limits. You will regain access on 2026-10-01 at 00:00 UTC.",
        {"type": "error", "error": {"type": "invalid_request_error",
                                    "message": "You have reached your specified API usage limits. You will regain access on 2026-10-01 at 00:00 UTC."}},
        "BadRequestError"))
check("Anthropic's spend limit on a 400 is billing, not a malformed request that ends the run (%s)" % (r,),
      r[0] == "insufficient_quota")
r = decide(GEM, err(400, "User location is not supported for the API use without a billing account linked.",
        _g(400, "FAILED_PRECONDITION", "User location is not supported for the API use without a billing account linked."),
        "BadRequestError"))
check("Gemini FAILED_PRECONDITION ('enable billing') rotates as billing instead of ending the run (%s)" % (r,),
      r[0] == "insufficient_quota")
r = decide(OR, err(402, "Usage limit reached, try again in 5 minutes",
        {"error": {"code": 402, "message": "Usage limit reached, try again in 5 minutes"}}))
check("a 402 that NAMES a wait is a wait, not an empty balance (%s)" % (r,),
      r[0] == "per_minute" and r[1] == 300.0 and r[2] == "provider")

print("\n--- 3. dead keys and refused models ---")
r = decide(GW, err(403, "Account suspended.", _oa("Account suspended."), "PermissionDeniedError"))
check("'Account suspended.' is a dead credential (%s)" % (r,), r[0] == "revoked")
r = decide(GW, err(401, "Error code: 401 - {'error': {'code': '', 'message': 'Invalid token (request id: 1)', 'type': 'api_error'}}",
        {"error": {"code": "", "message": "Invalid token (request id: 1)", "type": "api_error"}}, "AuthenticationError"))
check("a gateway 401 'Invalid token' is a credential problem, rested 20s and offered last (%s)" % (r,),
      r[0] == "auth" and r[1] == 20.0)
for sentence in ("Forbidden - insufficient permissions.",
                 "Forbidden - key(sk-xx) not authorized to access the requested model.",
                 "Forbidden - channel has been disabled.",
                 "Forbidden - key(sk-xx) allowed only from approved IP ranges."):
    r = decide(GW, err(403, sentence, _oa(sentence), "PermissionDeniedError"))
    check("403 %r refuses this pairing, never retires the key (%s)" % (sentence[:40], r), r[0] == "denied")

print("\n--- 4. the request is the problem: terminal, no key rested ---")
for name, identity, exc in (
    ("410 Gone (a retired model)", GW, err(410,
        "Error code: 410 - {'type': 'about:blank', 'title': 'Gone', 'status': 410, 'detail': \"The model 'z-ai/glm-4.6' has reached its end of life\"}",
        {"type": "about:blank", "title": "Gone", "status": 410, "detail": "The model 'z-ai/glm-4.6' has reached its end of life"})),
    ("Codex invalid_prompt", CODEX, err(400, "invalid_prompt: Request blocked.", _oa("Request blocked.", code="invalid_prompt"),
                                        "BadRequestError")),
    ("a content-policy block an aggregator relays", OR, err(403, "Request blocked: violence",
        {"error": {"code": 403, "message": "Request blocked: violence",
                   "metadata": {"reasons": ["violence"], "flagged_input": "...", "provider_name": "OpenAI",
                                "error_type": "content_policy_violation"}}}, "PermissionDeniedError")),
    ("context_length_exceeded with no status", OAI, err(None, "context too long",
        _oa("This model's maximum context length is 128000 tokens.", code="context_length_exceeded"))),
):
    check("%s is terminal" % name, decide(identity, exc)[0] == "terminal")

print("\n--- 5. somebody else's failure, relayed ---")
r = decide(OR, err(429, "Provider returned error",
        {"error": {"code": 429, "message": "Provider returned error",
                   "metadata": {"raw": "upstream rate-limited", "provider_name": "Chutes",
                                "error_type": "rate_limit_exceeded", "provider_code": 429}}}, "RateLimitError"))
check("an aggregator relaying an upstream 429 rests OUR key 1s, never a quota bench (%s)" % (r,),
      r[0] == "server" and r[1] == 1.0)

print("\n--- 6. every place a provider states a wait ---")
now = time.time()
r = decide(CODEX, err(429, "The usage limit has been reached",
        _oa("The usage limit has been reached", type_="usage_limit_reached", plan_type="plus",
            resets_in_seconds=900), "RateLimitError"))
check("Codex usage_limit_reached obeys resets_in_seconds (%s)" % (r,), r[0] == "per_minute" and r[1] == 900.0
      and r[2] == "provider")
r = decide(CODEX, err(429, "The usage limit has been reached",
        _oa("The usage limit has been reached", type_="usage_limit_reached", resets_at=int(now + 1800)), "RateLimitError"))
check("...and resets_at, an absolute epoch (%s)" % (r,), r[0] == "per_minute" and 1795 <= r[1] <= 1800)
r = decide(OAI, err(429, "Rate limit reached.", _oa("Rate limit reached.", type_="rate_limit_error"), "RateLimitError",
                    headers={"retry-after-ms": "1500"}))
check("retry-after-ms is milliseconds (%s)" % (r,), r[0] == "per_minute" and r[1] == 1.5)
r = decide(OAI, err(429, "Rate limit reached.", _oa("Rate limit reached.", type_="rate_limit_error"), "RateLimitError",
                    headers={"x-ratelimit-reset-requests": "6m0s"}))
check("a rate-limit reset header is read as a duration (%s)" % (r,), r[0] == "per_minute" and r[1] == 360.0)
reset_ms = str(int((now + 9 * 3600) * 1000))
r = decide(OR + ":free", err(429, "Rate limit exceeded: free-models-per-day. Add 10 credits to unlock 1000 free model requests per day",
        {"error": {"code": 429, "message": "Rate limit exceeded: free-models-per-day. Add 10 credits to unlock 1000 free model requests per day",
                   "metadata": {"headers": {"X-RateLimit-Limit": "50", "X-RateLimit-Remaining": "0",
                                            "X-RateLimit-Reset": reset_ms}}}}, "RateLimitError"))
check("OpenRouter's stated daily reset (in the body) is SERVED, up to the one-hour ceiling (%s)" % (r,),
      r[0] == "daily" and r[1] == 3600.0 and r[2] == "provider")

print("\n--- 7. a window named, no number ---")
r = decide(GEM, err(429, "Resource has been exhausted (e.g. check quota).",
        _g(429, "RESOURCE_EXHAUSTED", "Resource has been exhausted (e.g. check quota).",
           [{"@type": "type.googleapis.com/google.rpc.QuotaFailure",
             "violations": [{"quotaId": "GenerateRequestsPerMinutePerProjectPerModel-FreeTier"}]}]), "RateLimitError"))
check("a PerMinute quotaId with no number rests one window, 65s (%s)" % (r,), r[0] == "per_minute" and r[1] == 65.0)
check("a TPM quotaId is labelled tokens_per_minute",
      KE.window_label(err(429, "q", _g(429, "RESOURCE_EXHAUSTED", "q", [{"@type": "type.googleapis.com/google.rpc.QuotaFailure",
          "violations": [{"quotaId": "GenerateContentInputTokensPerModelPerMinute-FreeTier"}]}]))) == "tokens_per_minute")
r = decide(GEM, err(429, "Resource has been exhausted (e.g. check quota).",
        _g(429, "RESOURCE_EXHAUSTED", "Resource has been exhausted (e.g. check quota)."), "RateLimitError"))
check("Gemini's bare RESOURCE_EXHAUSTED still climbs the ladder from 1s (%s)" % (r,), r[0] == "per_minute" and r[1] == 1.0)
r = decide("nvidia:kimi", err(429, "API Error: 429 status code (no body)", cls="RateLimitError"))
check("a throttle with nothing at all rests the flat 30s (%s)" % (r,), r[0] == "per_minute" and r[1] == 30.0)
r = decide("groq:llama", err(498, "Flex Tier Capacity Exceeded"))
check("Groq's 498 is a busy server, 1s (%s)" % (r,), r[0] == "server" and r[1] == 1.0)

print("\n--- 8. an account-wide throttle holds the key on every model ---")
fresh("openai-codex:gpt-6-astra")
K._get_identity_state("openai-codex:gpt-5.6-luna", ["A", "B"])
exc = err(429, "The usage limit has been reached",
          _oa("The usage limit has been reached", type_="usage_limit_reached", resets_in_seconds=900), "RateLimitError")
K._kame_decide_failure("openai-codex:gpt-6-astra", "A", exc)
other = K._KAME_KEY_HEALTH["openai-codex:gpt-5.6-luna"]["keys"]["A"]
check("the same key is held on the provider's other model", other["sick_until"] > time.time() + 800)
check("...the other keys are untouched", K._KAME_KEY_HEALTH["openai-codex:gpt-5.6-luna"]["keys"]["B"]["sick_until"] == 0)
K._mark_key_health("openai-codex:gpt-5.6-luna", "A", success=True)
check("one answer from that key on any model clears the account hold everywhere",
      K._KAME_KEY_HEALTH["openai-codex:gpt-6-astra"]["keys"]["A"]["sick_until"] == 0)

print("\n--- 9. the host's own prose is not evidence ---")
footer = ("\n\nYour Google API key is on the free tier (a few hundred requests/day for Gemini Flash models), so the "
          "free tier is exhausted in a handful of messages. Regenerate the key in a billing-enabled project.")
r = decide(GEM, err(429, "Gemini HTTP 429 (RESOURCE_EXHAUSTED): You exceeded your current quota, please check your plan and billing details." + footer,
        _g(429, "RESOURCE_EXHAUSTED", "You exceeded your current quota, please check your plan and billing details.",
           [{"@type": "type.googleapis.com/google.rpc.QuotaFailure",
             "violations": [{"quotaId": "GenerateRequestsPerMinutePerProjectPerModel-FreeTier"}]},
            {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "7s"}]), "RateLimitError"))
check("a host footer saying 'free tier is exhausted... billing-enabled' does not buy an hour (%s)" % (r,),
      r[0] == "per_minute" and r[1] == 7.0)

print("\n--- 11. measured refutation: a stated deadline proven short twice ---")
_real_time = time.time
clock = {"t": 1_800_000_000.0}
time.time = lambda: clock["t"]
try:
    stated = err(429, "Rate limit reached. Please try again in 20s.", _oa("Rate limit reached. Please try again in 20s.",
                 type_="rate_limit_error"), "RateLimitError")
    fresh(OAI)
    rests = []
    for _ in range(6):
        # Held until the deadline, handed back, refused the same way at once.
        applied = K._kame_decide_failure(OAI, "A", stated)[0]
        rests.append(round(applied, 1))
        clock["t"] += applied + 1.0
    check("20s stated, refused right after each deadline: 20, 20, then 40, 80, 160, capped x8 (%s)" % (rests,),
          rests == [20.0, 20.0, 40.0, 80.0, 160.0, 160.0])
    fresh(OAI)
    clock["t"] += 10_000
    K._kame_decide_failure(OAI, "A", stated)
    clock["t"] += 21.0
    K._kame_decide_failure(OAI, "A", stated)
    clock["t"] += 21.0
    K._mark_key_health(OAI, "A", success=True)
    clock["t"] += 1.0
    check("one answer forgets the streak", K._kame_decide_failure(OAI, "A", stated)[0] == 20.0)
    fresh(OAI)
    K._kame_decide_failure(OAI, "A", stated)
    clock["t"] += 20.0 + 600.0
    K._kame_decide_failure(OAI, "A", stated)
    clock["t"] += 21.0
    check("a refusal landing long after the deadline is not proof it was short",
          K._kame_decide_failure(OAI, "A", stated)[0] == 20.0)
    bare = err(429, "Resource has been exhausted (e.g. check quota).",
               _g(429, "RESOURCE_EXHAUSTED", "Resource has been exhausted (e.g. check quota)."), "RateLimitError")
    fresh(GEM)
    ladder = []
    for _ in range(5):
        applied = K._kame_decide_failure(GEM, "A", bare)[0]
        ladder.append(round(applied, 1))
        clock["t"] += applied + 0.5
    check("the bare RESOURCE_EXHAUSTED ladder is NOT compounded: 1, 2, 4, 8, 16 (%s)" % (ladder,),
          ladder == [1.0, 2.0, 4.0, 8.0, 16.0])
finally:
    time.time = _real_time

print("\n--- 10. what the reader declines stays with the engine's own rules ---")
check("a bare 401 is left to the credential rules (auth, not revoked)",
      decide(GEM, err(401, "Unauthorized", cls="AuthenticationError"))[0] == "auth")
check("a bare 403 is left to the denial rule", decide(GEM, err(403, "Forbidden", cls="PermissionDeniedError"))[0] == "denied")
check("a plain 503 stays a 1s server rest", decide(GEM, err(503, "Service Unavailable"))[:2] == ("server", 1.0))
check("the reader is in the build fingerprint's required set", "kame_evidence.py" in __import__("integrity").REQUIRED)

print()
if _failures:
    print("%d FAILED:" % len(_failures))
    for f in _failures:
        print("  -", f)
    sys.exit(1)
print("ALL v1.8.1.0 EVIDENCE TESTS PASSED")

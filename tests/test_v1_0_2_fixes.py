"""Regression tests for the KAME v1.0.2 fixes — runs WITHOUT Agent Zero.

kame_engine imports a few external / A0 modules at load time (openai, litellm,
langchain_core.messages, helpers.print_style, helpers.errors). We stub those in
sys.modules first, then import the engine and exercise the PURE classification /
health-tracking functions in isolation.

Run:  python tests/test_v1_0_2_fixes.py
Exit code 0 = all pass, 1 = at least one failure.
"""
import sys, types, os, asyncio


# --------------------------------------------------------------------------
# Stub the modules kame_engine imports at module load, so it imports cleanly.
# --------------------------------------------------------------------------
def _stub(name):
    m = types.ModuleType(name)
    sys.modules[name] = m
    return m


_stub("openai")
_litellm = _stub("litellm")
_litellm.suppress_debug_info = False
def _acompletion(*a, **k):  # never called by these unit tests
    raise RuntimeError("acompletion stub should not be called")
_litellm.acompletion = _acompletion

_stub("langchain_core")
_lc_msg = _stub("langchain_core.messages")
class _Msg:
    def __init__(self, content=""):
        self.content = content
_lc_msg.SystemMessage = _Msg
_lc_msg.HumanMessage = _Msg

_stub("helpers")
_ps = _stub("helpers.print_style")
class _PrintStyle:
    def __init__(self, *a, **k): pass
    def print(self, *a, **k): pass
    @staticmethod
    def warning(*a, **k): pass
    @staticmethod
    def error(*a, **k): pass
    @staticmethod
    def success(*a, **k): pass
_ps.PrintStyle = _PrintStyle

_errs = _stub("helpers.errors")
class InterventionException(Exception): pass
class RepairableException(Exception): pass
class HandledException(Exception): pass
_errs.InterventionException = InterventionException
_errs.RepairableException = RepairableException
_errs.HandledException = HandledException


# --------------------------------------------------------------------------
# Import the engine under test (parent dir = the plugin root, has kame_engine).
# --------------------------------------------------------------------------
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import kame_engine as K  # noqa: E402


class FakeErr(Exception):
    """Mimics a provider error: a message string + optional .status_code."""
    def __init__(self, msg, status_code=None):
        super().__init__(msg)
        self.status_code = status_code


_failures = []
def check(name, cond):
    print(("PASS" if cond else "FAIL") + " - " + name)
    if not cond:
        _failures.append(name)


# ==========================================================================
# P1 — a 5xx is ALWAYS 'server', never 'daily' (even with quota/daily text).
#      This is the exact 02-06-2026 failure mode.
# ==========================================================================
GEMINI_503_BODY = (
    "litellm.InternalServerError: VertexAIException - 503 Service Unavailable. "
    "RESOURCE_EXHAUSTED. quota_metric generate_content_free_tier_requests; "
    "quotaId GenerateRequestsPerDayPerProjectPerModel PerDay daily limit"
)
for code in (500, 502, 503, 504, 529):
    d, kind, sc = K._classify_error(FakeErr(GEMINI_503_BODY, status_code=code))
    check(f"{code} + quota/daily text -> 'server' (5s), not 'daily'",
          kind == "server" and d == 5 and sc == code)

# 503 with no status_code but unambiguous server text -> still server.
d, kind, sc = K._classify_error(FakeErr("Service Unavailable - please try again later"))
check("text 'service unavailable' (no status) -> 'server'", kind == "server")

# Bad gateway / gateway timeout text -> server.
check("text 'bad gateway' -> 'server'",
      K._classify_error(FakeErr("502 Bad Gateway"))[1] == "server")
# "Gateway Timeout" contains "timeout", so the transient-timeout fast-path (3s)
# claims it first — also fine: transient + short + crucially NEVER 'daily'.
_, _k504, _ = K._classify_error(FakeErr("504 Gateway Timeout"))
check("'gateway timeout' text -> transient short, never daily", _k504 in ("timeout", "server"))
# A 504 status with no 'timeout' word -> 'server' (the 5xx rule).
check("504 status (no timeout word) -> 'server'",
      K._classify_error(FakeErr("504 upstream unavailable", status_code=504))[1] == "server")


# ==========================================================================
# Classification still correct for REAL rate limits (no regression).
# ==========================================================================
# Real 429 daily quota -> 'daily', floored at the daily cooldown (ignores the
# misleading short retryDelay).
d, kind, sc = K._classify_error(FakeErr(
    "429 RESOURCE_EXHAUSTED quotaId GenerateRequestsPerDayPerProjectPerModel "
    "PerDay ... retryDelay: 1s", status_code=429))
check("429 PerDay -> 'daily', floored at daily cooldown (1h)",
      kind == "daily" and d == K._KAME_DAILY_COOLDOWN_S)

# Real 429 per-minute -> 'per_minute', trusts the honest parsed retry delay.
d, kind, sc = K._classify_error(FakeErr(
    "429 Too Many Requests - please retry in 12s", status_code=429))
check("429 per-minute -> 'per_minute', trusts honest 12s", kind == "per_minute" and d == 12)

# OpenAI insufficient_quota (account/credit) -> 'insufficient_quota'.
d, kind, sc = K._classify_error(FakeErr("429 insufficient_quota: you exceeded your current quota", status_code=429))
check("insufficient_quota -> 'insufficient_quota'", kind == "insufficient_quota")

# Timeout -> 'timeout'.
check("timeout text -> 'timeout'", K._classify_error(FakeErr("Request timed out"))[1] == "timeout")


# ==========================================================================
# P4 — per-minute escalation: trust first honest delay (no 20s floor), then
#      escalate only up to the per-minute ceiling (NOT the 1h daily one).
# ==========================================================================
IDENT = "test:model"
K._get_identity_state(IDENT, ["KEYA"])
a1 = K._mark_key_health(IDENT, "KEYA", False, 8, "per_minute")
check("per-minute 1st strike trusts honest 8s (no 20s floor)", a1 == 8)
a2 = K._mark_key_health(IDENT, "KEYA", False, 8, "per_minute")
check("per-minute 2nd strike escalates to 20s", a2 == 20)
aN = a2
for _ in range(25):
    aN = K._mark_key_health(IDENT, "KEYA", False, 8, "per_minute")
check("per-minute escalation capped at per-minute ceiling (300s, NOT 3600)",
      aN == K._KAME_RL_BACKOFF_CAP_S == 300.0)
K._mark_key_health(IDENT, "KEYA", True)  # success resets the counter
aR = K._mark_key_health(IDENT, "KEYA", False, 8, "per_minute")
check("per-minute counter resets on success (back to honest 8s)", aR == 8)

# Daily strike is the 1h daily cooldown PLUS v1.0.6's re-probe spread (up to
# _KAME_DAILY_REPROBE_SPREAD_S of random jitter so keys cooled in the same burst
# don't all expire — and get re-probed — at the same instant an hour later).
K._get_identity_state(IDENT, ["KEYB"])
aD = K._mark_key_health(IDENT, "KEYB", False, K._KAME_DAILY_COOLDOWN_S, "daily")
check("daily strike stays within [1h, 1h + spread]",
      K._KAME_DAILY_COOLDOWN_S <= aD <= K._KAME_DAILY_COOLDOWN_S + K._KAME_DAILY_REPROBE_SPREAD_S)

# Server escalation: first ~5s, capped at 90s, reset on success.
K._get_identity_state(IDENT, ["KEYC"])
s1 = K._mark_key_health(IDENT, "KEYC", False, 5, "server")
check("server 1st strike ~5s", s1 == 5)
sN = s1
for _ in range(20):
    sN = K._mark_key_health(IDENT, "KEYC", False, 5, "server")
check("server escalation capped at 90s", sN == K._KAME_SERVER_BACKOFF_CAP_S == 90.0)


# ==========================================================================
# P5 — empty-stream guard mechanism: a 3s 'other' rest, no escalation.
# ==========================================================================
K._get_identity_state(IDENT, ["KEYE"])
e1 = K._mark_key_health(IDENT, "KEYE", False, 3, "other")
e2 = K._mark_key_health(IDENT, "KEYE", False, 3, "other")
check("empty-stream 'other' rest is a flat 3s (no escalation)", e1 == 3 and e2 == 3)


# ==========================================================================
# P2 — intervention wiring: set_current_agent + _kame_honor_intervention.
# ==========================================================================
check("set_current_agent exists", callable(getattr(K, "set_current_agent", None)))
check("_kame_honor_intervention exists", callable(getattr(K, "_kame_honor_intervention", None)))

# No agent stashed -> harmless no-op (must not raise).
K.set_current_agent(None)
asyncio.run(K._kame_honor_intervention())
check("honor_intervention no-ops without an agent", True)

# Agent whose handle_intervention raises InterventionException -> it propagates
# (this is what breaks the cooling sleep so a nudge is honored).
class _NudgingAgent:
    async def handle_intervention(self):
        raise InterventionException("user nudged")
K.set_current_agent(_NudgingAgent())
_propagated = False
try:
    asyncio.run(K._kame_honor_intervention())
except InterventionException:
    _propagated = True
check("honor_intervention propagates InterventionException (nudge honored)", _propagated)

# Agent whose handle_intervention raises a NON-control-flow error -> swallowed
# (a check failure must never break rotation).
class _BrokenAgent:
    async def handle_intervention(self):
        raise ValueError("unrelated boom")
K.set_current_agent(_BrokenAgent())
_swallowed = True
try:
    asyncio.run(K._kame_honor_intervention())
except Exception:
    _swallowed = False
check("honor_intervention swallows unrelated errors (rotation never breaks)", _swallowed)


# ==========================================================================
print("-" * 60)
if _failures:
    print(f"{len(_failures)} FAILED: {_failures}")
    sys.exit(1)
print("ALL TESTS PASSED")
sys.exit(0)

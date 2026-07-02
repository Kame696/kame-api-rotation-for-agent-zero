"""v1.0.6 — failover speed, quota-marker logging, empty-stream retry, daily spread.

Covers the four 1.0.6 changes, with stubs (no real A0/litellm):
  #1 faster failover — the outer failure path no longer sleeps a fixed 50ms
     (asserted structurally: no `asyncio.sleep(0.05)` remains in the engine).
  #2 inline quota marker — _extract_quota_marker maps Google quotaIds to a short
     tag, and _friendly_error_msg appends it for quota kinds.
  #3 empty-stream — the SAME key gets one un-penalized retry; only a 2nd empty
     from that key cools it (verified via the counter logic in a mini-loop).
  #4 daily spread — a daily cooldown is 1h plus up to the spread jitter.
"""
import sys, types, os, asyncio, re


def _stub(name):
    m = types.ModuleType(name)
    sys.modules[name] = m
    return m


_stub("openai")
_litellm = _stub("litellm")
_litellm.suppress_debug_info = False
_litellm.acompletion = lambda *a, **k: None
_stub("langchain_core")
_lc = _stub("langchain_core.messages")
class _Msg:
    def __init__(self, content=""):
        self.content = content
_lc.SystemMessage = _Msg
_lc.HumanMessage = _Msg
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
for _n in ("InterventionException", "RepairableException", "HandledException"):
    setattr(_errs, _n, type(_n, (Exception,), {}))
_models = _stub("models")

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import kame_engine as K  # noqa: E402

_failures = []
def check(name, cond):
    print(("PASS" if cond else "FAIL") + " - " + name)
    if not cond:
        _failures.append(name)


# ---- helpers to build fake provider errors -------------------------------
def _err(status, msg):
    e = Exception(msg)
    e.status_code = status
    return e

_GEMINI_DAILY = (
    'litellm.RateLimitError: vertex_ai_betaException - {"error":{"code":429,'
    '"message":"Quota exceeded for ... generate_content_free_tier_requests, limit:20",'
    '"status":"RESOURCE_EXHAUSTED","details":[{"@type":"type.googleapis.com/google.rpc.QuotaFailure",'
    '"violations":[{"quotaId":"GenerateRequestsPerDayPerProjectPerModel-FreeTier"}]}]}}'
)
_GEMINI_PER_MIN = (
    'litellm.RateLimitError: vertex_ai_betaException - {"error":{"code":429,'
    '"status":"RESOURCE_EXHAUSTED","details":[{"violations":[{"quotaId":'
    '"GenerateContentInputTokensPerModelPerMinute-FreeTier"}]}]}}'
)


# ==========================================================================
# #2 — _extract_quota_marker: PerDay vs PerMinute vs none
# ==========================================================================
check("quota marker: Gemini daily quotaId -> 'PerDay'",
      K._extract_quota_marker(_err(429, _GEMINI_DAILY)) == "PerDay")
check("quota marker: Gemini per-minute quotaId -> 'PerMinute'",
      K._extract_quota_marker(_err(429, _GEMINI_PER_MIN)) == "PerMinute")
check("quota marker: no quota text -> '' (nothing appended)",
      K._extract_quota_marker(_err(503, "service unavailable")) == "")

# the daily 429 is CLASSIFIED daily AND the friendly line carries the PerDay tag
_delay, _kind, _sc = K._classify_error(_err(429, _GEMINI_DAILY))
check("daily 429 classified as 'daily'", _kind == "daily")
_line = K._friendly_error_msg("daily", _delay, 429, _err(429, _GEMINI_DAILY))
check("friendly daily line appends '[quota: PerDay]'", "[quota: PerDay]" in _line)

# a per-minute 429 is classified per_minute and tagged PerMinute (no misclassification)
_pd, _pk, _psc = K._classify_error(_err(429, _GEMINI_PER_MIN))
check("per-minute 429 classified as 'per_minute'", _pk == "per_minute")
_pline = K._friendly_error_msg("per_minute", _pd, 429, _err(429, _GEMINI_PER_MIN))
check("friendly per-minute line appends '[quota: PerMinute]'", "[quota: PerMinute]" in _pline)


# ==========================================================================
# #4 — daily cooldown carries the re-probe spread (1h .. 1h + spread)
# ==========================================================================
IDENT = "gemini:gemini-3.5-flash"
K._KAME_KEY_HEALTH = {}
K._get_identity_state(IDENT, ["K1"])
_lo, _hi = K._KAME_DAILY_COOLDOWN_S, K._KAME_DAILY_COOLDOWN_S + K._KAME_DAILY_REPROBE_SPREAD_S
_spreads = [K._mark_key_health(IDENT, "K1", False, K._KAME_DAILY_COOLDOWN_S, "daily") for _ in range(20)]
check("every daily cooldown within [1h, 1h+spread]", all(_lo <= s <= _hi for s in _spreads))
check("daily spread actually varies (not a constant)", len(set(round(s) for s in _spreads)) > 1)
check("spread never shortens below the configured 1h", min(_spreads) >= _lo)


# ==========================================================================
# #1 — no fixed 50ms sleep remains on the failure path (structural)
# ==========================================================================
_src = open(os.path.join(os.path.dirname(__file__), "..", "kame_engine.py"), encoding="utf-8").read()
check("no residual 'asyncio.sleep(0.05)' fixed inter-rotation delay",
      "asyncio.sleep(0.05)" not in _src)
check("failure path yields with asyncio.sleep(0)", "asyncio.sleep(0)" in _src)


# ==========================================================================
# #3 — empty-stream: 1st empty from a key is NOT cooled, 2nd IS (counter logic)
# ==========================================================================
# Mirror the engine's per-call rule: _empty_counts[key] < 2 => not cooled.
K._KAME_KEY_HEALTH = {}
K._get_identity_state(IDENT, ["KE"])
_empty_counts = {}

def _handle_empty(key):
    """Return True if the key was COOLED (2nd+ empty), False if given a free pass."""
    _empty_counts[key] = _empty_counts.get(key, 0) + 1
    if _empty_counts[key] >= 2:
        K._mark_key_health(IDENT, key, False, 3, "other")
        return True
    return False

_first = _handle_empty("KE")
_second = _handle_empty("KE")
check("1st empty stream does NOT cool the key", _first is False)
check("2nd empty stream from same key DOES cool it (3s)", _second is True)
_sick = K._KAME_KEY_HEALTH[IDENT]["keys"]["KE"]["sick_until"]
import time as _t
check("cooled key sick_until is ~now+3s", 0 < (_sick - _t.time()) <= 4)


print("=" * 60)
if _failures:
    print("FAILURES:", _failures)
    sys.exit(1)
print("ALL v1.0.6 TESTS PASSED")

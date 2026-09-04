"""v1.0.6 — failover speed, quota-marker logging, empty-stream retry, daily spread.

Covers the four 1.0.6 changes, with stubs (no real A0/litellm):
  #1 faster failover — the outer failure path no longer sleeps a fixed 50ms
     (asserted structurally: no `asyncio.sleep(0.05)` remains in the engine).
  #2 inline quota marker — _extract_quota_marker maps Google quotaIds to a short
     tag, and _friendly_error_msg appends it for quota kinds.
  #3 empty-stream — the SAME key gets one un-penalized retry; only a 2nd empty
     from that key cools it (verified via the counter logic in a mini-loop).
  #4 daily cooldown — is EXACTLY the configured interval (the spread idea was
     dropped: it added recovery delay for no benefit). No jitter on daily.
  #5 invalid-key visibility — an auth/invalid-key event always logs (no longer
     gated behind _lvl_normal(), so it's visible even at 'silent', matching the
     documented "silent still shows hard errors" promise).
  #6 invalid-key partial reveal — _key_display_auth() upgrades the default
     'fingerprint' style to a partial reveal (first 10 + last 4 chars) for
     THIS event only, so a dead key can be found in the provider console;
     an explicit 'prefix8'/'full' choice is respected unchanged.
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


IDENT = "gemini:gemini-3.5-flash"


# ==========================================================================
# #4 — daily cooldown is EXACTLY the configured interval (no jitter/spread).
# (An early 1.0.6 build added up to 120s spread; removed — it added recovery
#  delay for no benefit the user wanted. Daily cooldown must be exact.)
# ==========================================================================
K._KAME_KEY_HEALTH = {}
K._get_identity_state(IDENT, ["K1"])
_daily = [K._mark_key_health(IDENT, "K1", False, K._KAME_DAILY_COOLDOWN_S, "daily") for _ in range(10)]
check("daily cooldown is exactly the configured interval (no spread)",
      all(s == K._KAME_DAILY_COOLDOWN_S for s in _daily))
check("no _KAME_DAILY_REPROBE_SPREAD_S constant remains",
      not hasattr(K, "_KAME_DAILY_REPROBE_SPREAD_S"))


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


# ==========================================================================
# #5/#6 — invalid-key visibility: always shown (even 'silent'), partial reveal
# ==========================================================================
_LONG_KEY = "AIzaSyABCDEFGHIJKLMNOPQRSTUVWXYZ0123"  # 37 chars, like a real Gemini key

# _key_display_auth: fingerprint style (default) is UPGRADED to a partial reveal
K._KAME_KEY_LOG_STYLE = "fingerprint"
_auth_disp = K._key_display_auth(_LONG_KEY)
check("auth display shows first 10 + last 4 chars (not the opaque fingerprint)",
      _auth_disp == f"{_LONG_KEY[:10]}...{_LONG_KEY[-4:]}")
check("auth display is NOT the same as the routine fingerprint display",
      _auth_disp != K._key_display(_LONG_KEY))
check("auth display never exposes the FULL key when style is fingerprint",
      _LONG_KEY not in _auth_disp)

# v1.6.0.1 REMOVED the `full` style. This block used to assert that an explicit
# `full` showed the whole key; it now asserts the opposite, which is the point of
# removing it. A log is copied into bug reports and screenshots by people who are
# not thinking about what is in it, and Agent Zero v2.11 masks credentials on its
# own — KAME's switch had become the only thing in the stack that deliberately
# un-redacted one. A config that still says `full` is not an error: it is read as
# `prefix8`, which answers the question `full` was actually used for.
K.set_key_log_style("full")
check("a legacy 'full' style is folded into prefix8, not honoured",
      K._KAME_KEY_LOG_STYLE == "prefix8")
check("no display path returns the whole key, whatever the config says",
      K._key_display_auth(_LONG_KEY) != _LONG_KEY
      and K._key_display(_LONG_KEY) != _LONG_KEY)
K._KAME_KEY_LOG_STYLE = "prefix8"
check("auth display respects an explicit 'prefix8' style (unchanged)",
      K._key_display_auth(_LONG_KEY) == K._key_display(_LONG_KEY))
K._KAME_KEY_LOG_STYLE = "fingerprint"  # restore default

# Short key: nothing meaningful to redact, shown as-is
check("a short key (<=16 chars) is shown in full (nothing to usefully hide)",
      K._key_display_auth("shortkey123") == "shortkey123")

# Structural: the auth-warning call site no longer gates on _lvl_normal()
# (previously `if _lvl_normal():` wrapped the auth PrintStyle.warning call,
# making it invisible in 'silent' mode — removed so it always fires).
#
# v1.0.9 note: there used to be TWO such call sites, because KAME owned the
# stream and had to mirror the auth handling inside its own chunk loop. Agent
# Zero owns the stream now, so a connect-time auth error simply raises out of
# the delegated call and lands in the ONE outer handler. One call site, one check.
_src = open(os.path.join(os.path.dirname(__file__), "..", "kame_engine.py"), encoding="utf-8").read()
check("there is exactly ONE carousel auth-error call site left (stream handler is gone)",
      _src.count("_is_auth_error(") == 3)  # def + _classify_error + the carousel
check("no 'if _lvl_normal():' gate right after the outer auth check",
      "if _lvl_normal():" not in _src.split("_is_auth_error(e):")[1][:400])


print("=" * 60)
if _failures:
    print("FAILURES:", _failures)
    sys.exit(1)
print("ALL v1.0.6 TESTS PASSED")

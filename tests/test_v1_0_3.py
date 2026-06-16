"""Regression tests for the KAME v1.0.3 additions — runs WITHOUT Agent Zero.

Covers the three v1.0.3 changes (none of which touch the selection path):
  1. full raw-error log toggle  (set_log_full_errors / _raw_error_detail)
  2. precise durations          (_fmt_duration)
  3. fast pool recovery         (_thaw_server_cooled_keys)

Run:  python tests/test_v1_0_3.py
Exit code 0 = all pass, 1 = at least one failure.
"""
import sys, types, os


# --- stub the modules kame_engine imports at load (same as the v1.0.2 suite) --
def _stub(name):
    m = types.ModuleType(name)
    sys.modules[name] = m
    return m


_stub("openai")
_litellm = _stub("litellm")
_litellm.suppress_debug_info = False
def _acompletion(*a, **k):
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

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import kame_engine as K  # noqa: E402


class FakeErr(Exception):
    def __init__(self, msg, status_code=None):
        super().__init__(msg)
        self.status_code = status_code


_failures = []
def check(name, cond):
    print(("PASS" if cond else "FAIL") + " - " + name)
    if not cond:
        _failures.append(name)


# ==========================================================================
# 1. Full raw-error log toggle.
# ==========================================================================
K.set_log_full_errors(False)
check("full-errors default off", K._KAME_LOG_FULL_ERRORS is False)
K.set_log_full_errors(True)
check("set True (bool)", K._KAME_LOG_FULL_ERRORS is True)
K.set_log_full_errors("false")
check("set 'false' (str) -> off", K._KAME_LOG_FULL_ERRORS is False)
K.set_log_full_errors("on")
check("set 'on' (str) -> on", K._KAME_LOG_FULL_ERRORS is True)
K.set_log_full_errors("garbage")
check("set 'garbage' (str) -> off", K._KAME_LOG_FULL_ERRORS is False)

# _raw_error_detail shows type + status + classification + FULL body.
_e = FakeErr("503 Service Unavailable RESOURCE_EXHAUSTED quota daily limit", status_code=503)
_detail = K._raw_error_detail(_e, kind="server", applied=90, status_code=503)
check("raw detail has exception type", "type=FakeErr" in _detail)
check("raw detail has status", "status=503" in _detail)
check("raw detail shows classification", "classified=server" in _detail)
check("raw detail shows applied cooldown", "cooled=" in _detail)
check("raw detail includes FULL body (untruncated)", "RESOURCE_EXHAUSTED quota daily limit" in _detail)

# _maybe_log_full_error must never raise (PrintStyle is stubbed) and must be a
# no-op when the toggle is off.
K.set_log_full_errors(True)
K._maybe_log_full_error("Chat", "m", "key", _e, "server", 90, 503)
K.set_log_full_errors(False)
K._maybe_log_full_error("Chat", "m", "key", _e, "server", 90, 503)
check("_maybe_log_full_error never raises", True)


# ==========================================================================
# 2. Precise durations (the "2m" -> "1m30s" fix).
# ==========================================================================
check("90s -> 1m30s (was the misleading '2m')", K._fmt_duration(90) == "1m30s")
check("80s -> 1m20s (was '1m')", K._fmt_duration(80) == "1m20s")
check("40s -> 40s", K._fmt_duration(40) == "40s")
check("60s -> 1m", K._fmt_duration(60) == "1m")
check("5s -> 5s", K._fmt_duration(5) == "5s")
check("3600s -> 1h", K._fmt_duration(3600) == "1h")
check("5400s -> 1.5h", K._fmt_duration(5400) == "1.5h")
check("86400s -> 24h", K._fmt_duration(86400) == "24h")
check("bad input -> '?'", K._fmt_duration("nope") == "?")


# ==========================================================================
# 3. Fast pool recovery — _thaw_server_cooled_keys.
#    Only 5xx-cooled keys are brought forward; daily/auth are untouched; a key
#    already recovering soon is never EXTENDED; the succeeding key is excluded.
# ==========================================================================
K._KAME_KEY_HEALTH.clear()
ident = "prov:model"
keys = ["a", "b", "c", "d"]
st = K._get_identity_state(ident, keys)
now = K.time.time()
# a = the key that just succeeded (excluded)
# b = server-cooled, far out (SHOULD thaw)
st["keys"]["b"]["sick_until"] = now + 80
st["keys"]["b"]["consecutive_server"] = 3
# c = daily-cooled (per-key quota) — must NOT be thawed
st["keys"]["c"]["sick_until"] = now + 3000
st["keys"]["c"]["consecutive_rl"] = 2
# d = server-cooled but already recovering in 1s — must NOT be extended
st["keys"]["d"]["sick_until"] = now + 1
st["keys"]["d"]["consecutive_server"] = 1

thawed = K._thaw_server_cooled_keys(ident, "a")
check("thawed exactly 1 (only the far-out server key)", thawed == 1)
check("server-cooled 'b' brought forward to < 12s", st["keys"]["b"]["sick_until"] < now + 12)
check("daily-cooled 'c' left untouched", st["keys"]["c"]["sick_until"] > now + 2000)
check("near-recovery 'd' NOT extended", st["keys"]["d"]["sick_until"] <= now + 2)
check("excluded 'a' never touched", float(st["keys"]["a"]["sick_until"]) == 0)
# unknown identity is a safe no-op
check("unknown identity -> 0 thawed, no raise", K._thaw_server_cooled_keys("nope:nope", "x") == 0)


# ==========================================================================
# 4. Invalid / expired KEY packed into a 400 (Gemini) -> AUTH, not terminal.
#    Gemini returns 400 API_KEY_INVALID / "API key expired" for a bad key; a
#    status-only (401) check misses it and _is_terminal_error would ABORT the
#    whole run instead of quarantining the key and rotating to the next.
# ==========================================================================
_gem_invalid = FakeErr(
    "400 INVALID_ARGUMENT: API key not valid. Please pass a valid API key.",
    status_code=400)
_gem_expired = FakeErr(
    "400 INVALID_ARGUMENT: API key expired. Please renew the API key.",
    status_code=400)
_gem_reason = FakeErr("API_KEY_INVALID", status_code=400)
_real_401 = FakeErr("401 Unauthorized", status_code=401)
_malformed_400 = FakeErr(
    "400 INVALID_ARGUMENT: request payload size exceeds the limit", status_code=400)

check("Gemini 400 'API key not valid' -> auth",
      K._is_auth_error(_gem_invalid) is True)
check("Gemini 400 'API key not valid' -> NOT terminal (rotates, no abort)",
      K._is_terminal_error(_gem_invalid) is False)
check("Gemini 400 'API key expired' -> auth + not terminal",
      K._is_auth_error(_gem_expired) is True and K._is_terminal_error(_gem_expired) is False)
check("Gemini 400 reason API_KEY_INVALID -> auth + not terminal",
      K._is_auth_error(_gem_reason) is True and K._is_terminal_error(_gem_reason) is False)
check("real 401 still auth", K._is_auth_error(_real_401) is True)
check("genuine malformed 400 still TERMINAL (rotating wouldn't help)",
      K._is_auth_error(_malformed_400) is False and K._is_terminal_error(_malformed_400) is True)


# ==========================================================================
# 5. 503-storm log collapse — setter, _storm_tick decisions, _storm_end recap,
#    gap-restart, and auth never collapsed. Pure logging; no rotation change.
# ==========================================================================
K.set_collapse_storm_logs(True)
check("collapse setter True (bool)", K._KAME_COLLAPSE_STORM_LOGS is True)
K.set_collapse_storm_logs("off")
check("collapse setter 'off' (str) -> False", K._KAME_COLLAPSE_STORM_LOGS is False)
K.set_collapse_storm_logs("on")
check("collapse setter 'on' (str) -> True", K._KAME_COLLAPSE_STORM_LOGS is True)

K._KAME_STORM.clear()
sid = "prov:model"
check("storm: first failure -> 'first'", K._storm_tick(sid, "server") == "first")
check("storm: repeat within interval -> 'suppress'", K._storm_tick(sid, "server") == "suppress")
check("storm: count incremented to 2", K._KAME_STORM[sid]["count"] == 2)
# force the emit interval to have elapsed -> one aggregate line
K._KAME_STORM[sid]["last_emit_at"] = K.time.time() - (K._KAME_STORM_LOG_INTERVAL_S + 1)
check("storm: after interval -> 'summary'", K._storm_tick(sid, "server") == "summary")
# a long quiet gap restarts the storm as a fresh 'first'
K._KAME_STORM[sid]["last_err_at"] = K.time.time() - (K._KAME_STORM_GAP_S + 1)
check("storm: after long gap -> 'first' (restart)", K._storm_tick(sid, "server") == "first")
check("storm: restarted count == 1", K._KAME_STORM[sid]["count"] == 1)

# _storm_end: recap only for a storm >= MIN_FOR_SUMMARY, and it always pops state
K._KAME_STORM.clear()
_n = K.time.time()
K._KAME_STORM[sid] = {"count": 5, "first_at": _n - 30, "last_err_at": _n,
                      "last_emit_at": _n, "kinds": {"server": 5}}
_recap = K._storm_end(sid)
check("storm_end: returns (count, span) for big storm", _recap is not None and _recap[0] == 5)
check("storm_end: popped the state", sid not in K._KAME_STORM)
K._KAME_STORM[sid] = {"count": 1, "first_at": _n, "last_err_at": _n,
                      "last_emit_at": _n, "kinds": {"server": 1}}
check("storm_end: no recap for tiny storm (< 3)", K._storm_end(sid) is None)
check("storm_end: popped tiny storm too", sid not in K._KAME_STORM)
check("storm_end: unknown identity -> None, no raise", K._storm_end("nope:nope") is None)

# auth is NEVER collapsed; server IS. _log_failure must not raise either way.
K._KAME_LOG_LEVEL = "normal"
K.set_collapse_storm_logs(True)
K.set_log_full_errors(False)
K._KAME_STORM.clear()
K._log_failure("Chat", "m", "key", FakeErr("401 Unauthorized", 401), "auth", 3600, 401, "auth:id", [])
check("auth failure never enters storm collapse", "auth:id" not in K._KAME_STORM)
K._log_failure("Chat", "m", "key", FakeErr("503", 503), "server", 5, 503, "srv:id", ["a"])
check("server failure enters storm collapse", "srv:id" in K._KAME_STORM)
check("_log_failure never raises", True)


# ==========================================================================
print("=" * 60)
if _failures:
    print(f"{len(_failures)} FAILURE(S): " + ", ".join(_failures))
    sys.exit(1)
print("ALL v1.0.3 TESTS PASSED")
sys.exit(0)

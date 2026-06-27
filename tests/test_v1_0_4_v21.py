"""v1.0.4 (A0 V2.1) tests — the unified_turn rotation wrapper.

A0 V2.1 split the model entry point: the agent monologue calls `unified_turn`
(returns an LLMResult), not `unified_call`. KAME 1.0.3 patched only unified_call,
so on V2.1 rotation never engaged. These tests verify the new `_kame_unified_turn`
wrapper: it rotates on a connect-time rate-limit, injects the rotated api_key +
explicit_caching=False + a0_retry_attempts=0 into the ORIGINAL method, delegates
untouched when there is no multi-key pool, and honors the got-any-chunk contract
(re-raise after content streamed, never re-stream a duplicate on another key).

Same stub strategy as the v1.0.2/1.0.3/1.0.4 suites (no real A0 / litellm needed).
"""
import sys, types, os, asyncio


def _stub(name):
    m = types.ModuleType(name)
    sys.modules[name] = m
    return m


_stub("openai")
_litellm = _stub("litellm")
_litellm.suppress_debug_info = False
_litellm.acompletion = lambda *a, **k: None
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

# models stub: _kame_unified_turn only needs turn_off_logging at call time.
_models = _stub("models")
_models.turn_off_logging = lambda: None

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import kame_engine as K  # noqa: E402

_failures = []
def check(name, cond):
    print(("PASS" if cond else "FAIL") + " - " + name)
    if not cond:
        _failures.append(name)


def _rl_429():
    e = Exception("rate limit")
    e.status_code = 429
    return e


class _Conf:
    provider = "gemini"


class FakeWrapper:
    model_name = "gemini/gemini-3.5-flash"
    a0_model_conf = _Conf()
    def __init__(self):
        self.calls = []


# Reset any health from a prior import so selection starts clean.
K._KAME_KEY_HEALTH = {}


# --- Test A: rotation + kwarg injection -------------------------------------
K._get_all_api_keys = lambda self: ["AAA", "BBB", "CCC"]

_state_a = {"n": 0}
fw_a = FakeWrapper()

async def _orig_turn_a(**kw):
    fw_a.calls.append(kw)
    _state_a["n"] += 1
    if _state_a["n"] == 1:          # the first key tried 429s once
        raise _rl_429()
    return ("LLM_RESULT", kw.get("api_key"))

fw_a._kame_original_unified_turn = _orig_turn_a

res_a = asyncio.run(K._kame_unified_turn(fw_a, messages=[_Msg("hi")]))
check("rotates past a 429 and returns the original's LLMResult",
      isinstance(res_a, tuple) and res_a[0] == "LLM_RESULT")
check("made >= 2 attempts (rotated to a fresh key after the 429)",
      len(fw_a.calls) >= 2)
check("every attempt injected explicit_caching=False (free-tier cache-safe)",
      all(c.get("explicit_caching") is False for c in fw_a.calls))
check("every attempt also forced a0_explicit_prompt_caching=False (override any flag)",
      all(c.get("a0_explicit_prompt_caching") is False for c in fw_a.calls))
check("every attempt pinned a0_api_mode=chat_completions (off vertex_ai_beta Responses route)",
      all(c.get("a0_api_mode") == "chat_completions" for c in fw_a.calls))
check("every attempt disabled the inner retry (a0_retry_attempts=0)",
      all(c.get("a0_retry_attempts") == 0 for c in fw_a.calls))
check("every attempt forced a rotated api_key from the pool",
      all(c.get("api_key") in ("AAA", "BBB", "CCC") for c in fw_a.calls))
check("the second (successful) attempt used a DIFFERENT key than the failed first",
      fw_a.calls[0].get("api_key") != fw_a.calls[1].get("api_key"))


# --- Test B: no multi-key pool -> delegate to the original untouched ---------
K._get_all_api_keys = lambda self: []
_b = {"orig": False, "explicit": None}
fw_b = FakeWrapper()

async def _orig_turn_b(**kw):
    _b["orig"] = True
    _b["explicit"] = kw.get("explicit_caching")
    return "DIRECT"

fw_b._kame_original_unified_turn = _orig_turn_b
res_b = asyncio.run(K._kame_unified_turn(fw_b, messages=[], explicit_caching=True))
check("no pool -> delegates straight to the original method", res_b == "DIRECT" and _b["orig"])
check("delegation passes the caller's explicit_caching through unchanged",
      _b["explicit"] is True)


# --- Test C: got-any-chunk contract (content streamed -> re-raise, no re-stream)
K._KAME_KEY_HEALTH = {}
K._get_all_api_keys = lambda self: ["AAA", "BBB"]
_c = {"calls": 0, "deltas": []}
fw_c = FakeWrapper()

async def _orig_turn_c(**kw):
    _c["calls"] += 1
    cb = kw.get("response_callback")
    if cb is not None:
        await cb("hello", "hello")     # stream real content, THEN fail
    raise _rl_429()

fw_c._kame_original_unified_turn = _orig_turn_c

async def _user_cb(delta, full):
    _c["deltas"].append(delta)
    return None

_raised = None
try:
    asyncio.run(K._kame_unified_turn(fw_c, messages=[], response_callback=_user_cb))
except Exception as e:
    _raised = e
check("a failure AFTER content streamed re-raises (clean A0 restart)", _raised is not None)
check("did NOT rotate/re-stream after delivery (original called exactly once)", _c["calls"] == 1)
check("user callback saw the chunk exactly once (no duplicate stream)", _c["deltas"] == ["hello"])


print("=" * 60)
if _failures:
    print("FAILURES:", _failures)
    sys.exit(1)
print("ALL v1.0.4 (A0 V2.1) TESTS PASSED")

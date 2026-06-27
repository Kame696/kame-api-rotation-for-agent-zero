"""v1.0.4 (A0 V2.1) — direct-call carousel for unified_turn.

KAME does NOT delegate to A0's native unified_turn (whose Responses-mode transport
runs an internal retry/fallback loop that hangs a failing call ~40s). Instead it runs
the SAME proven direct-acompletion carousel as unified_call (the 1.0.3 mechanism —
fast rotation), then wraps the (response, reasoning) in the LLMResult V2.1 expects.

These tests verify, with stubs (no real A0/litellm):
  A. unified_turn returns an LLMResult built from the carousel's output.
  B. the carousel rotates on a connect-time 503 via DIRECT acompletion (fast).
  C. a mid-stream transient drop is ridden out (no error surfaced), full answer.
  D. a genuinely terminal error still surfaces (no infinite spin).
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

# models stub: turn_off_logging, approximate_tokens, ChatGenerationResult, LLMResult
_models = _stub("models")
_models.turn_off_logging = lambda: None
_models.approximate_tokens = lambda s: len(s or "")
class _CGR:
    def __init__(self):
        self.response = ""; self.reasoning = ""
    def add_chunk(self, parsed):
        self.response += parsed.get("response_delta", "")
        self.reasoning += parsed.get("reasoning_delta", "")
        return parsed
    def output(self):
        return {"response_delta": self.response, "reasoning_delta": self.reasoning}
_models.ChatGenerationResult = _CGR
class _LLMResult:
    def __init__(self, response="", reasoning=""):
        self.response = response; self.reasoning = reasoning
    @classmethod
    def from_chat(cls, *, response="", reasoning="", input_items=None,
                  provider_model_key="", capability=None):
        return cls(response=response, reasoning=reasoning)
_models.LLMResult = _LLMResult

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import kame_engine as K  # noqa: E402

# parser stub + V2 mode (so _kame_chunk_aiter parses our raw chunks)
K._KAME_CHUNK_MODE = "v2"
K._KAME_PARSE_CHUNK = lambda raw: {"reasoning_delta": "", "response_delta": str(raw)}

_seen = []
async def _rcb(delta, full):
    _seen.append(delta)
    return None


_failures = []
def check(name, cond):
    print(("PASS" if cond else "FAIL") + " - " + name)
    if not cond:
        _failures.append(name)


def _err(status):
    e = Exception("rate limit" if status == 503 else "bad")
    e.status_code = status
    return e


class _Conf:
    provider = "gemini"
class FW:
    model_name = "gemini/gemini-3.5-flash"
    a0_model_conf = _Conf()
    kwargs = {}
    def _convert_messages(self, msgs, explicit_caching=False):
        return [{"role": "user", "content": "hi"}]


class _Stream:
    def __init__(self, items): self._i = list(items)
    def __aiter__(self): return self
    async def __anext__(self):
        if not self._i:
            raise StopAsyncIteration
        v = self._i.pop(0)
        if isinstance(v, Exception):
            raise v
        return v


# --- Test A: unified_turn wraps the carousel's output in an LLMResult ---------
async def _fake_call(self, **kw):
    return ("HELLO-WORLD", "my-reasoning")
_orig_call = K._kame_unified_call
K._kame_unified_call = _fake_call
res_a = asyncio.run(K._kame_unified_turn(FW(), messages=[_Msg("hi")], response_callback=_rcb))
K._kame_unified_call = _orig_call
check("unified_turn returns an LLMResult (has .response/.reasoning)",
      hasattr(res_a, "response") and hasattr(res_a, "reasoning"))
check("unified_turn LLMResult carries the carousel's response/reasoning",
      res_a.response == "HELLO-WORLD" and res_a.reasoning == "my-reasoning")


# --- Test B: carousel rotates on a connect-time 503 via DIRECT acompletion ----
K._KAME_KEY_HEALTH = {}
K._get_all_api_keys = lambda self: ["AAA", "BBB", "CCC"]
_b = {"calls": []}
async def _acomp_b(model=None, messages=None, **kw):
    _b["calls"].append(kw.get("api_key"))
    if len(_b["calls"]) == 1:
        raise _err(503)              # first key 503s at CONNECT → rotate
    return _Stream(["Hello"])        # next key succeeds
K.acompletion = _acomp_b
res_b = asyncio.run(K._kame_unified_turn(
    FW(), messages=[_Msg("hi")], response_callback=_rcb))
check("connect 503 rotates to another key (>=2 direct acompletion calls)", len(_b["calls"]) >= 2)
check("carousel calls acompletion DIRECTLY (no transport) and returns the answer",
      res_b.response == "Hello")
check("the retry used a different key than the failed first",
      _b["calls"][0] != _b["calls"][1])


# --- Test C: a MID-STREAM transient drop is ridden out (never surfaced) -------
K._KAME_KEY_HEALTH = {}
K._get_all_api_keys = lambda self: ["AAA", "BBB"]
_c = {"calls": 0}
async def _acomp_c(model=None, messages=None, **kw):
    _c["calls"] += 1
    if _c["calls"] == 1:
        return _Stream(["{\"thoughts\":", _err(503)])   # stream a bit, THEN drop
    return _Stream(["done"])
K.acompletion = _acomp_c
_raised_c = None
try:
    res_c = asyncio.run(K._kame_unified_turn(
        FW(), messages=[_Msg("hi")], response_callback=_rcb))
except Exception as e:
    _raised_c = e
check("mid-stream transient drop is NOT surfaced (no exception escapes)", _raised_c is None)
check("KAME rotated + retried after the mid-stream drop (>=2 calls)", _c["calls"] >= 2)
check("returns the COMPLETE answer from the key that finally worked",
      _raised_c is None and res_c.response == "done")


# --- Test D: a genuinely terminal error still surfaces ------------------------
K._KAME_KEY_HEALTH = {}
K._get_all_api_keys = lambda self: ["AAA", "BBB"]
_d = {"calls": 0}
async def _acomp_d(model=None, messages=None, **kw):
    _d["calls"] += 1
    e = Exception("invalid request: content_policy violation")
    e.status_code = 400
    raise e
K.acompletion = _acomp_d
_raised_d = None
try:
    asyncio.run(K._kame_unified_turn(FW(), messages=[_Msg("hi")],
                                     response_callback=_rcb))
except Exception as e:
    _raised_d = e
check("a terminal (4xx/content-policy) error still surfaces", _raised_d is not None)
check("terminal error is not retried in a loop (called once)", _d["calls"] == 1)


print("=" * 60)
if _failures:
    print("FAILURES:", _failures)
    sys.exit(1)
print("ALL v1.0.4 (A0 V2.1) TESTS PASSED")

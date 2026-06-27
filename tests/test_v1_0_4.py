"""v1.0.4 — A0 V2 / V2.1 chunk handling.

KAME calls litellm ``acompletion`` DIRECTLY on every A0 version (the 1.0.3
mechanism); only the raw-chunk PARSER differs:
  * A0 v1.x   → models._parse_chunk
  * A0 V2/V2.1 → ChatCompletionsTransport.parse (a static method)
KAME does NOT route through A0's LiteLLMTransport (whose Responses-mode retry
loop hangs a failing call ~40s). It also strips A0-internal a0_*/responses_*
kwargs before the plain chat call. Tested with stubs (no real A0/litellm).
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
_models = _stub("models")

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import kame_engine as K  # noqa: E402

_failures = []
def check(name, cond):
    print(("PASS" if cond else "FAIL") + " - " + name)
    if not cond:
        _failures.append(name)


def _reset():
    K._KAME_CHUNK_MODE = None
    K._KAME_PARSE_CHUNK = None


# --- detection: v1.x uses models._parse_chunk -------------------------------
_models._parse_chunk = lambda c: {"reasoning_delta": "", "response_delta": c}
_reset()
check("v1 detected when models._parse_chunk present",
      K._kame_detect_chunk_mode() == "v1" and K._KAME_PARSE_CHUNK is _models._parse_chunk)

# --- detection: V2/V2.1 uses ChatCompletionsTransport.parse -------------------
del _models._parse_chunk
_lt = _stub("helpers.litellm_transport")
class _CCT:
    @staticmethod
    def parse(chunk):
        return {"reasoning_delta": "", "response_delta": str(chunk)}
_lt.ChatCompletionsTransport = _CCT
_reset()
check("v2 detected -> parser is ChatCompletionsTransport.parse",
      K._kame_detect_chunk_mode() == "v2" and K._KAME_PARSE_CHUNK is _CCT.parse)
check("detection is cached", K._kame_detect_chunk_mode() == "v2")


# --- _kame_chunk_aiter: DIRECT acompletion + parse + strip a0_*/responses_* --
_captured = {}
class _FakeStream:
    def __init__(self, items): self._i = list(items)
    def __aiter__(self): return self
    async def __anext__(self):
        if not self._i:
            raise StopAsyncIteration
        return self._i.pop(0)

async def _fake_acompletion(model=None, messages=None, **kw):
    _captured.clear(); _captured.update(kw); _captured["_model"] = model
    return _FakeStream(["X", "Y"]) if kw.get("stream") else "Z"

K.acompletion = _fake_acompletion
K._KAME_CHUNK_MODE = "v2"
K._KAME_PARSE_CHUNK = lambda raw: {"reasoning_delta": "", "response_delta": raw}

class _Self:
    model_name = "gemini/gemini-3.5-flash"

async def _collect(stream):
    out = []
    call_kwargs = {"temperature": 0, "a0_responses_function_tools": [1],
                   "responses_state": "x", "a0_retry_attempts": 2, "previous_response_id": "r1"}
    async for p in K._kame_chunk_aiter(_Self(), [], call_kwargs, "KEY7", stream):
        out.append(p)
    return out

_s = asyncio.run(_collect(True))
check("chunk_aiter yields parsed chunks in order (direct acompletion)",
      [r["response_delta"] for r in _s] == ["X", "Y"])
check("chunk_aiter calls acompletion DIRECTLY with the rotated api_key + model",
      _captured.get("api_key") == "KEY7" and _captured.get("_model") == "gemini/gemini-3.5-flash")
check("chunk_aiter STRIPS a0_* / responses_* / previous_response_id before the chat call",
      not any(k in _captured for k in
              ("a0_responses_function_tools", "responses_state", "a0_retry_attempts", "previous_response_id")))
check("chunk_aiter keeps normal kwargs (temperature)", _captured.get("temperature") == 0)
check("chunk_aiter set stream=True", _captured.get("stream") is True)

_ns = asyncio.run(_collect(False))
check("chunk_aiter non-stream parses the single response", len(_ns) == 1 and _ns[0]["response_delta"] == "Z")


print("=" * 60)
if _failures:
    print("FAILURES:", _failures)
    sys.exit(1)
print("ALL v1.0.4 TESTS PASSED")

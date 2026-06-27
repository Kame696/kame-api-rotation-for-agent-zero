"""v1.0.4 tests — Agent Zero V2 compatibility (version-aware chunk source).

Verifies the new capability detection (_kame_detect_chunk_mode) and the
version-aware chunk generator (_kame_chunk_aiter) for BOTH A0 majors, using
stubs (no real A0 / litellm needed). Same stub strategy as the v1.0.2/1.0.3 suites.
"""
import sys, types, os, asyncio


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

_failures = []
def check(name, cond):
    print(("PASS" if cond else "FAIL") + " - " + name)
    if not cond:
        _failures.append(name)


def _reset_detect():
    K._KAME_CHUNK_MODE = None
    K._KAME_PARSE_CHUNK = None
    K._KAME_V2_TRANSPORT = None


class FakeSelf:
    model_name = "gemini/gemini-2.5-flash"


_captured_kwargs = {}
class FakeTransport:
    """Mimics A0 V2's LiteLLMTransport(model, messages, kwargs)."""
    def __init__(self, model, messages, kwargs):
        self.model = model
        _captured_kwargs.clear()
        _captured_kwargs.update(kwargs)
    async def astream(self):
        for d in ({"reasoning_delta": "", "response_delta": "He"},
                  {"reasoning_delta": "", "response_delta": "llo"}):
            yield d
    async def acomplete(self):
        return {"reasoning_delta": "", "response_delta": "Done"}


# --- detection ---------------------------------------------------------------

# models is only imported INSIDE engine functions, so we create the stub here.
_models = _stub("models")

# A0 v1.x: models._parse_chunk present -> mode 'v1'
_models._parse_chunk = lambda c: {"reasoning_delta": "", "response_delta": c}
_reset_detect()
m1 = K._kame_detect_chunk_mode()
check("detect v1 when models._parse_chunk present", m1 == "v1" and K._KAME_PARSE_CHUNK is not None)

# A0 V2: no models._parse_chunk, but helpers.litellm_transport.LiteLLMTransport present -> 'v2'
del _models._parse_chunk
_lt = _stub("helpers.litellm_transport")
_lt.LiteLLMTransport = FakeTransport
_reset_detect()
m2 = K._kame_detect_chunk_mode()
check("detect v2 when _parse_chunk absent + transport present", m2 == "v2" and K._KAME_V2_TRANSPORT is FakeTransport)

# detection is cached (second call returns the same without re-importing)
check("detect mode is cached", K._kame_detect_chunk_mode() == "v2")


# --- V2 chunk generator ------------------------------------------------------

K._KAME_CHUNK_MODE = "v2"
K._KAME_V2_TRANSPORT = FakeTransport
K._KAME_PARSE_CHUNK = None

async def _collect(stream):
    out = []
    async for p in K._kame_chunk_aiter(FakeSelf(), [], {"temperature": 0}, "key123", stream):
        out.append(p)
    return out

v2_stream = asyncio.run(_collect(True))
check("v2 stream yields transport chunks in order",
      [r["response_delta"] for r in v2_stream] == ["He", "llo"])
check("v2 injects api_key into transport kwargs (alongside existing kwargs)",
      _captured_kwargs.get("api_key") == "key123" and _captured_kwargs.get("temperature") == 0)

v2_nostream = asyncio.run(_collect(False))
check("v2 non-stream yields exactly one acomplete chunk",
      len(v2_nostream) == 1 and v2_nostream[0]["response_delta"] == "Done")


# --- V1 chunk generator ------------------------------------------------------

class FakeStream:
    def __init__(self, items): self._items = list(items)
    def __aiter__(self): return self
    async def __anext__(self):
        if not self._items:
            raise StopAsyncIteration
        return self._items.pop(0)

async def _fake_acompletion(model=None, messages=None, **kw):
    return FakeStream(["A", "B"]) if kw.get("stream") else "C"

K._KAME_CHUNK_MODE = "v1"
K._KAME_PARSE_CHUNK = lambda raw: {"reasoning_delta": "", "response_delta": raw}
K._KAME_V2_TRANSPORT = None
K.acompletion = _fake_acompletion  # the engine's module-level acompletion

v1_stream = asyncio.run(_collect(True))
check("v1 stream uses acompletion + _parse_chunk",
      [r["response_delta"] for r in v1_stream] == ["A", "B"])

v1_nostream = asyncio.run(_collect(False))
check("v1 non-stream parses the single completion",
      len(v1_nostream) == 1 and v1_nostream[0]["response_delta"] == "C")


print("=" * 60)
if _failures:
    print("FAILURES:", _failures)
    sys.exit(1)
print("ALL v1.0.4 TESTS PASSED")

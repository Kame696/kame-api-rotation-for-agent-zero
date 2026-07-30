"""v1.0.8 — honor A0's early-stop contract on the streamed response callback.

Since Agent Zero V2, `Agent.monologue`'s stream callback RETURNS the accumulated
text as soon as a complete, valid tool request has been streamed; native
`unified_call` / `unified_turn` then break the stream and use that text
(`stop_response`). KAME owns the stream, so it has to honor the same contract.
Before v1.0.8 it awaited the callback and threw the return value away, so the
model kept generating past a finished tool call on every turn.

These tests verify, with stubs (no real A0/litellm):
  A. a non-None callback return stops the stream immediately (remaining chunks
     are never pulled from the provider).
  B. the returned text becomes the result (trailing junk after the tool JSON is
     dropped, exactly like native A0).
  C. a callback returning None (A0 v1.x, and V2 mid-response) changes nothing —
     the whole stream is consumed.
  D. an early stop with BLANK text is not mistaken for an empty stream: the key
     is not penalized and KAME does not rotate.
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
_models.turn_off_logging = lambda: None
_models.approximate_tokens = lambda s: len(s or "")


class _CGR:
    def __init__(self):
        self.response = ""
        self.reasoning = ""

    def add_chunk(self, parsed):
        self.response += parsed.get("response_delta", "")
        self.reasoning += parsed.get("reasoning_delta", "")
        return parsed

    def output(self):
        return {"response_delta": self.response, "reasoning_delta": self.reasoning}


_models.ChatGenerationResult = _CGR


class _LLMResult:
    def __init__(self, response="", reasoning=""):
        self.response = response
        self.reasoning = reasoning

    @classmethod
    def from_chat(cls, *, response="", reasoning="", input_items=None,
                  output_items=None, provider_model_key="", capability=None):
        return cls(response=response, reasoning=reasoning)


_models.LLMResult = _LLMResult

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import kame_engine as K  # noqa: E402

K._KAME_CHUNK_MODE = "v2"
K._KAME_PARSE_CHUNK = lambda raw: {"reasoning_delta": "", "response_delta": str(raw)}

_failures = []


def check(name, cond):
    print(("PASS  " if cond else "FAIL  ") + name)
    if not cond:
        _failures.append(name)


class _Conf:
    provider = "gemini"


class FW:
    model_name = "gemini/gemini-3.5-flash"
    a0_model_conf = _Conf()
    kwargs = {}

    def _convert_messages(self, msgs, explicit_caching=False):
        return [{"role": "user", "content": "hi"}]


class _Stream:
    """Async iterator that records how many chunks were actually pulled."""

    def __init__(self, items, counter):
        self._i = list(items)
        self._counter = counter

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._i:
            raise StopAsyncIteration
        self._counter["pulled"] += 1
        return self._i.pop(0)


def _run(chunks, callback, **extra):
    """Run one clean carousel call over `chunks`, returning (result, counter)."""
    K._KAME_KEY_HEALTH = {}
    K._get_all_api_keys = lambda self: ["AAA", "BBB"]
    counter = {"pulled": 0, "calls": 0}

    async def _acomp(model=None, messages=None, **kw):
        counter["calls"] += 1
        return _Stream(chunks, counter)

    K.acompletion = _acomp
    res = asyncio.run(K._kame_unified_call(
        FW(), messages=[_Msg("hi")], response_callback=callback, **extra))
    return res, counter


TOOL_JSON = '{"tool_name": "response", "tool_args": {"text": "done"}}'
CHUNKS = [TOOL_JSON, " TRAILING", " JUNK"]


# --- A + B: a non-None return stops the stream and becomes the result --------
async def _cb_stop(delta, full):
    # mimics A0 V2 Agent.monologue: return the full text once the tool JSON closes
    return full if full.endswith("}}") else None


res_ab, cnt_ab = _run(CHUNKS, _cb_stop)
check("early stop halts the stream (trailing chunks never pulled)", cnt_ab["pulled"] == 1)
check("early stop does not trigger a key rotation (single provider call)", cnt_ab["calls"] == 1)
check("returned text becomes the result (trailing junk dropped)", res_ab[0] == TOOL_JSON)


# --- C: a callback that always returns None consumes the whole stream --------
async def _cb_none(delta, full):
    return None


res_c, cnt_c = _run(CHUNKS, _cb_none)
check("callback returning None consumes the whole stream (A0 v1.x behavior)",
      cnt_c["pulled"] == len(CHUNKS))
check("full accumulated text returned when no early stop",
      res_c[0] == "".join(CHUNKS))


# --- D: a BLANK early stop is not mistaken for an empty stream ---------------
async def _cb_blank(delta, full):
    return ""


res_d, cnt_d = _run(CHUNKS, _cb_blank)
check("blank early stop is honored, not treated as an empty stream", cnt_d["calls"] == 1)
check("blank early stop returns the blank text", res_d[0] == "")
check("blank early stop leaves the key un-penalized",
      all(not v.get("keys", {}) or all(k.get("sick_until", 0) == 0 for k in v["keys"].values())
          for v in K._KAME_KEY_HEALTH.values()))


# --- E: streaming with NO response_callback (e.g. a tokens-only utility call).
# KAME streams whenever any callback is set; the early-stop branch must not fire
# or crash when response_callback itself is None.
async def _tok(delta, n):
    return None


res_e, cnt_e = _run(CHUNKS, None, tokens_callback=_tok)
check("streaming without a response_callback consumes the whole stream",
      cnt_e["pulled"] == len(CHUNKS))
check("streaming without a response_callback returns the full text",
      res_e[0] == "".join(CHUNKS))


print("=" * 60)
if _failures:
    print("FAILURES:", _failures)
    sys.exit(1)
print("ALL v1.0.8 TESTS PASSED")

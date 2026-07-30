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

Also covers the second v1.0.8 fix — 403 PERMISSION_DENIED quarantine (group F).
A denied key/project is permanent, not a 20s blip; before the fix it fell into
the generic 20s `other` bucket and was re-probed three times a minute forever.
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


# --- F: 403 PERMISSION_DENIED is quarantined, not cooled for 20s -------------
class _Err(Exception):
    def __init__(self, msg, status_code=None):
        super().__init__(msg)
        self.status_code = status_code


_GEMINI_403 = _Err(
    'litellm.BadRequestError: Vertex_ai_betaException BadRequestError - '
    '{"error": {"code": 403, "message": "Your project has been denied access. '
    'Please contact support.", "status": "PERMISSION_DENIED"}}',
    status_code=403,
)

_delay_403, _kind_403, _sc_403 = K._classify_error(_GEMINI_403)
check("403 PERMISSION_DENIED classifies as 'denied'", _kind_403 == "denied")
check("403 PERMISSION_DENIED reports its real status code", _sc_403 == 403)
check("403 PERMISSION_DENIED is quarantined for the daily cooldown, not 20s",
      _delay_403 == K._KAME_DAILY_COOLDOWN_S and _delay_403 > 20)
check("403 PERMISSION_DENIED is NOT terminal (KAME rotates, never aborts the run)",
      K._is_terminal_error(_GEMINI_403) is False)
check("403 PERMISSION_DENIED is not misread as an invalid key",
      K._is_auth_error(_GEMINI_403) is False)

# the text marker alone must work too: litellm sometimes drops status_code
_text_only = _Err('GeminiException - {"status": "PERMISSION_DENIED"}')
check("PERMISSION_DENIED text alone is enough (status_code may be lost)",
      K._classify_error(_text_only)[1] == "denied")

# and a quota 429 must NEVER be swallowed by the new branch
_429 = _Err("RateLimitError: 429 RESOURCE_EXHAUSTED quota exceeded", status_code=429)
check("a 429 is still classified as a rate limit, not 'denied'",
      K._classify_error(_429)[1] in ("per_minute", "daily", "insufficient_quota"))
_503 = _Err("ServiceUnavailableError: 503 UNAVAILABLE high demand", status_code=503)
check("a 503 is still classified as 'server', not 'denied'",
      K._classify_error(_503)[1] == "server")

# the friendly line must name the real cause, not a generic exception name
_line = K._friendly_error_msg("denied", _delay_403, 403, _GEMINI_403)
check("the denied log line is explicit and actionable",
      "denied" in _line and "quarantined" in _line and "403" in _line)

# a denied key must actually stop being re-selected while quarantined
K._KAME_KEY_HEALTH = {}
_ident = "gemini:gemini-3.5-flash"
K._get_identity_state(_ident, ["DEAD", "GOOD"])
K._mark_key_health(_ident, "DEAD", False, _delay_403, "denied")
_picks = {K._get_best_key(_ident, ["DEAD", "GOOD"])[0] for _ in range(5)}
check("a quarantined denied key is never picked again while cooling",
      _picks == {"GOOD"})


# --- G: the cosmetic banner can never fail the patch -------------------------
# On a non-UTF-8 console (native Windows, cp1252) the emoji banner raises
# UnicodeEncodeError. Before v1.0.8 that escaped into apply_kame_patch's outer
# handler, which printed "Patch Failed" and returned False even though every
# patch had already been applied. Source check: the _print_shield_status() call
# must sit inside its own try/except.
import inspect  # noqa: E402
import re  # noqa: E402

_src = inspect.getsource(K.apply_kame_patch)
_guarded = re.search(
    r"try:\s*\n\s*_print_shield_status\(\)\s*\n\s*except Exception:", _src)
check("the shield banner is printed inside its own try/except", bool(_guarded))
check("_KAME_PATCHED is set BEFORE the banner is printed",
      _src.index("_KAME_PATCHED = True") < _src.index("_print_shield_status()"))


print("=" * 60)
if _failures:
    print("FAILURES:", _failures)
    sys.exit(1)
print("ALL v1.0.8 TESTS PASSED")

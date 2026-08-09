"""v1.0.4 (A0 V2.1) — the turn path, RETARGETED for v1.0.9's delegated execution.

What this suite originally guarded
----------------------------------
A0 V2.1 moved the agent monologue from ``unified_call`` to ``unified_turn``. 1.0.4
patched that too, ran the same direct-``acompletion`` carousel, and then REBUILT the
``LLMResult`` that V2.1 expects via ``LLMResult.from_chat(...)``.

Why it looks different now
--------------------------
v1.0.9 stopped rebuilding anything. A0's own ``unified_turn`` runs and its result —
whatever class that is, with whatever fields a future A0 adds — is handed straight
back. ``LLMResult.from_chat`` is no longer part of KAME's compatibility surface.

The four behavioral guarantees the original suite existed for are unchanged, and
this file still tests all four:

  A. the turn path returns the result type A0's caller expects
  B. a connect-time 503 rotates to another key, fast
  C. a mid-stream transient drop is ridden out — never surfaced to the user
  D. a genuinely terminal error still surfaces instead of spinning forever

Runs with stubs. No real Agent Zero, no litellm, no network.
"""
import sys, types, os, asyncio, time


def _stub(name):
    m = types.ModuleType(name)
    sys.modules[name] = m
    return m


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

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import kame_engine as K  # noqa: E402

K.set_log_level("silent")

_failures = []


def check(name, cond):
    print(("PASS  " if cond else "FAIL  ") + name)
    if not cond:
        _failures.append(name)


class _Conf:
    provider = "gemini"


class _Err(Exception):
    def __init__(self, msg, status_code=None):
        super().__init__(msg)
        self.status_code = status_code


class LLMResultLike:
    """A0 V2.1's LLMResult: what the monologue expects back from unified_turn."""
    def __init__(self, response="", reasoning=""):
        self.response = response
        self.reasoning = reasoning
        self.function_calls = []
        self.mode = "chat_completions"


class TurnModel:
    """A0 V2.1 model whose unified_turn streams via the callbacks, then returns
    an LLMResult. `script` decides what each attempt does."""
    model_name = "gemini/gemini-3.5-flash"

    def __init__(self, script):
        self.a0_model_conf = _Conf()
        self.kwargs = {}
        self.calls = []
        self.script = script

    async def unified_turn(self, system_message="", user_message="", messages=None,
                           response_callback=None, reasoning_callback=None,
                           tokens_callback=None, rate_limiter_callback=None,
                           explicit_caching=False, **kwargs):
        kwargs.pop("a0_retry_attempts", None)
        kwargs.pop("a0_retry_delay_seconds", None)
        self.calls.append(kwargs.get("api_key"))
        return await self.script[min(len(self.calls) - 1, len(self.script) - 1)](
            self, response_callback)


def _answer(text, stream_chunks=()):
    async def _step(model, cb):
        for c in stream_chunks:
            if cb:
                await cb(c, c)
        return LLMResultLike(text)
    return _step


def _fail_before_streaming(exc):
    async def _step(model, cb):
        raise exc
    return _step


def _fail_mid_stream(exc, before=("partial ",)):
    async def _step(model, cb):
        for c in before:
            if cb:
                await cb(c, c)
        raise exc
    return _step


async def _cb(delta, full):
    return None


def _run(script, keys=("AAA", "BBB", "CCC")):
    K._KAME_KEY_HEALTH = {}
    K._get_all_api_keys = lambda self: list(keys)
    model = TurnModel(script)
    wrapper = K._kame_make_entry_wrapper("unified_turn", TurnModel.unified_turn)
    return model, wrapper


# --- A. the turn path returns A0's own result type ---------------------------
_m, _w = _run([_answer("the answer")])
_res = asyncio.run(_w(_m, messages=[_Msg("hi")], response_callback=_cb))
check("A the turn path returns A0's LLMResult (has .response/.reasoning)",
      hasattr(_res, "response") and hasattr(_res, "reasoning"))
check("A the result is A0's OWN object, not a KAME rebuild",
      isinstance(_res, LLMResultLike) and _res.mode == "chat_completions")
check("A the answer text survives the carousel", _res.response == "the answer")


# --- B. a connect-time 503 rotates to another key, fast ----------------------
_m, _w = _run([_fail_before_streaming(_Err("503 UNAVAILABLE high demand", 503)),
               _answer("recovered")])
_t0 = time.perf_counter()
_res = asyncio.run(_w(_m, messages=[_Msg("hi")], response_callback=_cb))
check("B a connect 503 rotates to another key", len(_m.calls) >= 2)
check("B the retry used a different key than the one that failed",
      _m.calls[0] != _m.calls[1])
check("B the answer comes from the key that finally worked", _res.response == "recovered")
check("B rotation is fast (no inherited retry/backoff delay)",
      (time.perf_counter() - _t0) < 1.0)


# --- C. a mid-stream transient drop is ridden out ----------------------------
_m, _w = _run([_fail_mid_stream(_Err("APIConnectionError: stream closed", None)),
               _answer("complete answer")])
_raised = None
try:
    _res = asyncio.run(_w(_m, messages=[_Msg("hi")], response_callback=_cb))
except Exception as e:      # noqa: BLE001 - the point of the test
    _raised = e
check("C a mid-stream transient drop is NOT surfaced to the user", _raised is None)
check("C KAME rotated and retried after the drop", len(_m.calls) >= 2)
check("C the COMPLETE answer from the working key is returned",
      _raised is None and _res.response == "complete answer")


# --- D. a genuinely terminal error still surfaces ----------------------------
_terminal = _Err("BadRequestError: context window exceeded 2000000 > 1000000", 400)
_m, _w = _run([_fail_before_streaming(_terminal)])
_raised = None
try:
    asyncio.run(_w(_m, messages=[_Msg("hi")], response_callback=_cb))
except Exception as e:      # noqa: BLE001 - the point of the test
    _raised = e
check("D a terminal error still surfaces instead of spinning forever",
      _raised is _terminal)
check("D a terminal error is not retried in a loop", len(_m.calls) == 1)

# and the KAME contract that outranks all of them: control-flow passes through
_intervention = _errs.InterventionException("user typed mid-generation")
_m, _w = _run([_fail_mid_stream(_intervention)])
_raised = None
try:
    asyncio.run(_w(_m, messages=[_Msg("hi")], response_callback=_cb))
except Exception as e:      # noqa: BLE001 - the point of the test
    _raised = e
check("D InterventionException still reaches A0 (native nudge handling)",
      _raised is _intervention and len(_m.calls) == 1)


print("=" * 60)
if _failures:
    print("FAILURES:", _failures)
    sys.exit(1)
print("ALL v1.0.4-V2.1 TESTS PASSED")

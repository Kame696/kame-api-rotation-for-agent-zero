"""v1.0.4 — A0 V2 compatibility, RETARGETED for v1.0.9's delegated execution.

What v1.0.4 originally guarded
------------------------------
1.0.4 made KAME work on both Agent Zero majors by calling litellm's
``acompletion`` directly and picking a raw-chunk PARSER per A0 version
(``models._parse_chunk`` on v1.x, ``ChatCompletionsTransport.parse`` on V2). This
suite asserted that detection, the direct-call chunk iterator, and the stripping
of A0-internal ``a0_*`` / ``responses_*`` kwargs.

Why it looks different now
--------------------------
v1.0.9 removed the whole mechanism: Agent Zero makes the call, so there is no
parser to detect, no chunk iterator to drive and no A0-internal kwarg for KAME to
strip (they never leave A0 in the first place). Asserting the old mechanism would
be asserting code that no longer exists.

What survives is the REQUIREMENT the mechanism existed to satisfy, and that is
what this file tests now:

  A. ONE engine still serves both A0 majors — the same wrapper drives a v1.x-shaped
     model and a V2-shaped model with no version detection anywhere.
  B. KAME still never goes near A0's internals: no litellm import, no parser, no
     chunk loop; A0's raw result comes back untouched.
  C. The reason 1.0.4 bypassed A0's transport (its retry loop hung a failing call
     for tens of seconds) is still handled — A0's retry loop is switched off per
     call, so a bad key fails FAST and rotation stays instant.

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


class A0v1Shaped:
    """Agent Zero v1.x: only `unified_call`, returns a (response, reasoning) tuple."""
    model_name = "gemini/gemini-2.0-flash"

    def __init__(self):
        self.a0_model_conf = _Conf()
        self.kwargs = {}
        self.calls = []

    async def unified_call(self, system_message="", user_message="", messages=None,
                           response_callback=None, reasoning_callback=None,
                           tokens_callback=None, rate_limiter_callback=None,
                           explicit_caching=False, **kwargs):
        max_retries = int(kwargs.pop("a0_retry_attempts", 2))
        retry_delay = float(kwargs.pop("a0_retry_delay_seconds", 1.5))
        self.calls.append({"key": kwargs.get("api_key"), "retries": max_retries,
                           "delay": retry_delay})
        if len(self.calls) == 1:
            raise _Err("litellm.RateLimitError: 429 RESOURCE_EXHAUSTED", 429)
        return ("v1 answer", "")


class _TurnResult:
    """Stands in for A0 V2's LLMResult — a rich object, not a tuple."""
    def __init__(self, response, reasoning=""):
        self.response = response
        self.reasoning = reasoning
        self.function_calls = []
        self.capability = {"responses": True}


class A0V2Shaped:
    """Agent Zero V2: `unified_turn` too, returning an LLMResult-like object."""
    model_name = "gemini/gemini-3.5-flash"

    def __init__(self):
        self.a0_model_conf = _Conf()
        self.kwargs = {}
        self.calls = []

    async def unified_turn(self, system_message="", user_message="", messages=None,
                           response_callback=None, reasoning_callback=None,
                           tokens_callback=None, rate_limiter_callback=None,
                           explicit_caching=False, **kwargs):
        max_retries = int(kwargs.pop("a0_retry_attempts", 2))
        retry_delay = float(kwargs.pop("a0_retry_delay_seconds", 1.5))
        self.calls.append({"key": kwargs.get("api_key"), "retries": max_retries,
                           "delay": retry_delay})
        if len(self.calls) == 1:
            raise _Err("litellm.ServiceUnavailableError: 503 UNAVAILABLE", 503)
        return _TurnResult("v2 answer")


# --- A. ONE engine, BOTH A0 majors, zero version detection -------------------
K._get_all_api_keys = lambda self: ["AAA", "BBB"]

K._KAME_KEY_HEALTH = {}
_m1 = A0v1Shaped()
_w1 = K._kame_make_entry_wrapper("unified_call", A0v1Shaped.unified_call)
_r1 = asyncio.run(_w1(_m1, messages=[_Msg("hi")]))
check("A1 a v1.x-shaped A0 rotates and answers through the same wrapper",
      _r1 == ("v1 answer", "") and len(_m1.calls) == 2)
check("A1 the retry used a DIFFERENT key", _m1.calls[0]["key"] != _m1.calls[1]["key"])

K._KAME_KEY_HEALTH = {}
_m2 = A0V2Shaped()
_w2 = K._kame_make_entry_wrapper("unified_turn", A0V2Shaped.unified_turn)
_r2 = asyncio.run(_w2(_m2, messages=[_Msg("hi")]))
check("A2 a V2-shaped A0 rotates and answers through the SAME wrapper code",
      isinstance(_r2, _TurnResult) and len(_m2.calls) == 2)
check("A2 A0's rich result object is returned untouched, not flattened to a tuple",
      _r2.response == "v2 answer" and _r2.capability == {"responses": True})
check("A3 there is no A0-version detection left in the engine",
      not hasattr(K, "_kame_detect_chunk_mode") and not hasattr(K, "_KAME_CHUNK_MODE"))


# --- B. KAME never touches A0's internals ------------------------------------
_src = open(os.path.join(os.path.dirname(__file__), "..", "kame_engine.py"),
            encoding="utf-8").read()
check("B1 the engine does not import litellm",
      "\nimport litellm" not in _src and "from litellm import" not in _src)
check("B2 no chunk parser is bound at any point",
      not hasattr(K, "_KAME_PARSE_CHUNK") and not hasattr(K, "_kame_chunk_aiter"))
check("B3 no A0-internal kwarg stripping is needed any more",
      not hasattr(K, "_kame_clean_call_kwargs"))
check("B4 the engine imports cleanly with NO Agent Zero model module present",
      "models" not in sys.modules)


# --- C. the original reason for bypassing A0's transport is handled ----------
check("C1 A0's own retry loop is switched off on every attempt",
      all(c["retries"] == 0 for c in _m1.calls + _m2.calls))
check("C2 A0's own retry DELAY is switched off on every attempt",
      all(c["delay"] == 0.0 for c in _m1.calls + _m2.calls))

# C3 — end-to-end: a full rotation over a dead pool must not spend real time in
# A0's retry loop. With the knobs zeroed this is instant; with A0's defaults it
# would be 2 attempts x 1.5s PER KEY.
K._KAME_KEY_HEALTH = {}
_m3 = A0v1Shaped()
_w3 = K._kame_make_entry_wrapper("unified_call", A0v1Shaped.unified_call)
_t0 = time.perf_counter()
asyncio.run(_w3(_m3, messages=[_Msg("hi")]))
check("C3 rotation after a 429 is effectively instant (no inherited retry delay)",
      (time.perf_counter() - _t0) < 1.0)


print("=" * 60)
if _failures:
    print("FAILURES:", _failures)
    sys.exit(1)
print("ALL v1.0.4 TESTS PASSED")

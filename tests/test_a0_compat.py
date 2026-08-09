"""Live compatibility harness — KAME against a REAL Agent Zero checkout.

Unlike the other suites (which stub A0 so they run anywhere), this one imports
the genuine `models`, `helpers.history`, `helpers.extension`, `agent` and
`tools.response` modules from an Agent Zero source tree and applies + reverts
KAME's monkey-patches against those real classes. It is the check to run when a
new Agent Zero version ships.

Since KAME v1.0.9 it also runs a REAL rotation end-to-end: a genuine
`LiteLLMChatWrapper`, A0's genuine transport and result assembly, with only the
outermost litellm network call replaced. The first key gets a 429, the second
answers — if that passes, delegation works on this Agent Zero build.

Usage:
    python tests/test_a0_compat.py /path/to/agent-zero
    A0_PATH=/path/to/agent-zero python tests/test_a0_compat.py

Requires A0's own runtime deps (litellm, langchain-core, ...) importable. Only
`sentence_transformers` is stubbed — it is heavy and nothing KAME touches uses
it. Exits 0 and prints SKIPPED when no A0 path is given.

Verified green against Agent Zero v2.8.
"""
import sys, types, os, importlib.util, inspect, asyncio

_A0 = (sys.argv[1] if len(sys.argv) > 1 else os.environ.get("A0_PATH", "")).strip()
if not _A0 or not os.path.isdir(_A0):
    print("SKIPPED - no Agent Zero checkout given.")
    print("  usage: python tests/test_a0_compat.py /path/to/agent-zero")
    sys.exit(0)

_KAME = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, _A0)
os.chdir(_A0)

_failures = []


def check(label, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + label + ((" - " + detail) if detail and not cond else ""))
    if not cond:
        _failures.append(label)


class _Any:
    def __init__(self, *a, **k): pass
    def __getattr__(self, item): return _Any()
    def __call__(self, *a, **k): return _Any()


_st = types.ModuleType("sentence_transformers")
_st.SentenceTransformer = _Any
sys.modules["sentence_transformers"] = _st
# A0 v1.x pulls openai-whisper in through helpers/settings.py. Heavy, optional,
# and nothing KAME touches uses it.
if "whisper" not in sys.modules:
    _wh = types.ModuleType("whisper")
    _wh.load_model = _Any
    sys.modules["whisper"] = _wh

import models  # noqa: E402
import helpers.history as history  # noqa: E402
from helpers.rate_limiter import RateLimiter  # noqa: E402
from helpers.errors import RepairableException  # noqa: E402
from tools.response import ResponseTool  # noqa: E402
import agent as a0_agent  # noqa: E402

spec = importlib.util.spec_from_file_location("kame_engine", os.path.join(_KAME, "kame_engine.py"))
kame = importlib.util.module_from_spec(spec)
sys.modules["kame_engine"] = kame
spec.loader.exec_module(kame)
check("kame_engine imports against this Agent Zero", True)
kame.set_log_level("silent")

# --- v1.0.9: the entry points must be discoverable BY SHAPE ------------------
_found = kame._kame_find_entry_points(models.LiteLLMChatWrapper)
check("A0's model entry points are found by shape (layer 1)", bool(_found), str(_found))
check("shape detection finds the call path", "unified_call" in _found, str(_found))
if hasattr(models.LiteLLMChatWrapper, "unified_turn"):
    check("shape detection finds the turn path", "unified_turn" in _found, str(_found))

# --- v1.0.9: A0's own retry knobs must be readable from its source -----------
_knobs = kame._kame_retry_knobs(models.LiteLLMChatWrapper.unified_call)
check("A0's retry knobs are discovered in its source", bool(_knobs), str(_knobs))
check("every discovered retry knob is disabled (0 / 0.0)",
      all(v == 0 for v in _knobs.values()), str(_knobs))

# --- patch application + clean revert ---------------------------------------
orig_call = models.LiteLLMChatWrapper.unified_call
orig_turn = getattr(models.LiteLLMChatWrapper, "unified_turn", None)
orig_topic = history.Topic.summarize_messages
orig_bulk = history.Bulk.summarize

check("apply_kame_patch() returned True", kame.apply_kame_patch() is True)
check("KAME engaged on layer 1 (bound by shape)", kame._KAME_LAYER == 1,
      f"layer={kame._KAME_LAYER}")
check("unified_call is wrapped", models.LiteLLMChatWrapper.unified_call is not orig_call)
check("original unified_call stored", models.LiteLLMChatWrapper._kame_original_unified_call is orig_call)
check("unified_call is in the bound list", "unified_call" in kame._KAME_BOUND_ENTRY_POINTS)
if orig_turn is not None:
    check("unified_turn is wrapped", models.LiteLLMChatWrapper.unified_turn is not orig_turn)
    check("original unified_turn stored",
          models.LiteLLMChatWrapper._kame_original_unified_turn is orig_turn)
    check("unified_turn is in the bound list", "unified_turn" in kame._KAME_BOUND_ENTRY_POINTS)
check("Topic.summarize_messages patched", history.Topic.summarize_messages is kame._kame_summarize_messages)
check("Bulk.summarize patched", history.Bulk.summarize is kame._kame_bulk_summarize)

# =============================================================================
# LIVE ROTATION — real LiteLLMChatWrapper, real transport, fake network only
# =============================================================================
# Where the network call actually lives depends on the A0 major: V2 routes
# through helpers/litellm_transport.py, v1.x calls litellm straight from
# models.py. Find whichever this checkout has and replace ONLY that.
_net_module = None
try:
    import helpers.litellm_transport as _t  # noqa: E402
    if hasattr(_t, "acompletion"):
        _net_module = _t
except Exception:
    pass
if _net_module is None and hasattr(models, "acompletion"):
    _net_module = models
check("the litellm entry point is patchable for a live test", _net_module is not None)

_real_acompletion = getattr(_net_module, "acompletion", None)
_seen_keys = []


class _RateLimit429(Exception):
    status_code = 429

    def __init__(self):
        super().__init__("litellm.RateLimitError: 429 RESOURCE_EXHAUSTED quota exceeded")


class _FakeStream:
    def __init__(self, chunks):
        self._chunks = list(chunks)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._chunks:
            raise StopAsyncIteration
        return self._chunks.pop(0)

    async def aclose(self):
        return None


async def _fake_acompletion(**kw):
    """Stands in for the network. First key 429s, any other key answers."""
    _seen_keys.append(kw.get("api_key"))
    if kw.get("api_key") == "KEY-DEAD":
        raise _RateLimit429()
    return _FakeStream([
        {"choices": [{"delta": {"content": "Hello"}}]},
        {"choices": [{"delta": {"content": " world"}, "finish_reason": "stop"}]},
    ])


setattr(_net_module, "acompletion", _fake_acompletion)
kame._get_all_api_keys = lambda self: ["KEY-DEAD", "KEY-GOOD"]
kame._KAME_KEY_HEALTH = {}

_wrapper = models.LiteLLMChatWrapper(model="gemini-3.5-flash", provider="gemini")
_streamed = []


async def _resp_cb(delta, full):
    _streamed.append(delta)
    return None


try:
    _answer, _reasoning = asyncio.run(models.LiteLLMChatWrapper.unified_call(
        _wrapper,
        system_message="you are a test",
        user_message="say hello",
        response_callback=_resp_cb,
        a0_api_mode="chat_completions",
    ))
    check("LIVE: a real call rotates off the 429 key and answers",
          _answer == "Hello world", repr(_answer))
    check("LIVE: the dead key was tried first, then a different key",
          _seen_keys[:2] == ["KEY-DEAD", "KEY-GOOD"], str(_seen_keys))
    check("LIVE: KAME's key really reached litellm",
          all(k in ("KEY-DEAD", "KEY-GOOD") for k in _seen_keys), str(_seen_keys))
    check("LIVE: A0 streamed to the caller's callback through KAME's shim",
          "".join(_streamed) == "Hello world", str(_streamed))
    check("LIVE: exactly one retry was needed (no wasted attempts)",
          len(_seen_keys) == 2, str(_seen_keys))
except Exception as e:  # noqa: BLE001 - the point of the test
    check("LIVE: a real call rotates off the 429 key and answers", False, repr(e))

# LIVE 2 — the same real call with NO keys configured must be a pure passthrough.
_seen_keys.clear()
kame._get_all_api_keys = lambda self: []
try:
    _answer2, _ = asyncio.run(models.LiteLLMChatWrapper.unified_call(
        _wrapper, user_message="say hello", response_callback=_resp_cb,
        a0_api_mode="chat_completions"))
    check("LIVE: with no keys the call still works (native passthrough)",
          _answer2 == "Hello world", repr(_answer2))
    check("LIVE: with no keys KAME injects nothing", _seen_keys == [None], str(_seen_keys))
except Exception as e:  # noqa: BLE001
    check("LIVE: with no keys the call still works (native passthrough)", False, repr(e))

setattr(_net_module, "acompletion", _real_acompletion)

# --- clean revert ------------------------------------------------------------
kame.remove_kame_patch()
check("unified_call restored", models.LiteLLMChatWrapper.unified_call is orig_call)
if orig_turn is not None:
    check("unified_turn restored", models.LiteLLMChatWrapper.unified_turn is orig_turn)
check("Topic.summarize_messages restored", history.Topic.summarize_messages is orig_topic)
check("Bulk.summarize restored", history.Bulk.summarize is orig_bulk)

# --- the result contract _kame_result_is_empty relies on ---------------------
# KAME reads `.response` / `.reasoning` off whatever A0 returns, WITHOUT importing
# A0's result class. Verify this A0's turn result still exposes them.
try:
    from helpers.llm_result import LLMResult
    _r = LLMResult.from_chat(response="hi", reasoning="because", provider_model_key="gemini/x")
    check("A0's turn result still exposes .response / .reasoning",
          _r.response == "hi" and _r.reasoning == "because")
    check("KAME reads a non-empty result correctly", kame._kame_result_is_empty(_r) is False)
    _blank = LLMResult.from_chat(response="", reasoning="", provider_model_key="gemini/x")
    check("KAME reads an empty result correctly", kame._kame_result_is_empty(_blank) is True)
except ImportError:
    check("A0 has no helpers.llm_result (older build) - tuple path only", True)
check("KAME reads A0's tuple result correctly",
      kame._kame_result_is_empty(("hi", "")) is False
      and kame._kame_result_is_empty(("", "")) is True)

# --- the extension folders KAME ships must match what @extensible derives ----
shipped = set()
_root = os.path.join(_KAME, "extensions", "python")
for dirpath, _dirs, filenames in os.walk(_root):
    if any(f.endswith(".py") for f in filenames):
        shipped.add(os.path.relpath(dirpath, _root).replace("\\", "/"))

for name in ("monologue", "validate_tool_request"):
    inner = inspect.unwrap(getattr(a0_agent.Agent, name))
    point = "/".join(["_functions", *[p for p in inner.__module__.split(".") if p],
                      *[p for p in inner.__qualname__.split(".") if p and p != "<locals>"], "start"])
    check(f"KAME ships an extension at {point}", point in shipped, f"shipped={sorted(shipped)}")

# --- v1.0.9: activation must not hang on ONE derived folder path -------------
# The @extensible-derived path above is silent when it stops matching. KAME also
# ships at A0's NAMED extension points, whose names are hardcoded strings in
# agent.py. Verify both that KAME ships them and that this A0 actually calls them.
_agent_src = inspect.getsource(a0_agent)
for _point in ("agent_init", "monologue_start"):
    check(f"KAME ships an activation extension at {_point}", _point in shipped,
          f"shipped={sorted(shipped)}")
    check(f"this Agent Zero actually calls the '{_point}' extension point",
          f'"{_point}"' in _agent_src or f"'{_point}'" in _agent_src)

_doors = [d for d in shipped if os.path.isfile(
    os.path.join(_root, d, "_10_kame_api_rotation.py"))]
check("KAME has at least 3 independent activation doors", len(_doors) >= 3, str(_doors))
for _d in _doors:
    _body = open(os.path.join(_root, _d, "_10_kame_api_rotation.py"), encoding="utf-8").read()
    check(f"the {_d} door delegates to the shared activate()",
          "kame_activation import activate" in _body)
check("the shared activation module ships with the plugin",
      os.path.isfile(os.path.join(_KAME, "kame_activation.py")))

# --- rate limiter patch ------------------------------------------------------
import threading  # noqa: E402
check("_patch_rate_limiters() succeeded", kame._patch_rate_limiters() is True)
rl = RateLimiter(seconds=60, requests=1)
check("RateLimiter uses threading.Lock after patch", isinstance(rl._lock, type(threading.Lock())))
check("get_total works under the threading lock", asyncio.run(rl.get_total("requests")) == 0)

# --- response heal vs this A0's ResponseTool ---------------------------------
heal_dir = os.path.join(_KAME, "extensions", "python", "_functions", "agent", "Agent",
                        "validate_tool_request", "start")
heal_spec = importlib.util.spec_from_file_location(
    "kame_heal", os.path.join(heal_dir, "_10_kame_heal_tool_args.py"))
heal = importlib.util.module_from_spec(heal_spec)
heal_spec.loader.exec_module(heal)


def run_response_tool(args):
    tool = ResponseTool(agent=None, name="response", method=None, args=args, message="", loop_data=None)
    return asyncio.run(tool.execute())


req = {"tool_name": "response", "tool_args": {"content": "the real answer"}}
heal.heal_response_args(req)
try:
    check("wrong-key answer is salvaged and accepted by this A0's ResponseTool",
          run_response_tool(req["tool_args"]).message == "the real answer")
except Exception as e:
    check("wrong-key answer is salvaged and accepted by this A0's ResponseTool", False, repr(e))

req2 = {"tool_name": "response", "tool_args": {}}
heal.heal_response_args(req2)
try:
    run_response_tool(req2["tool_args"])
    check("empty response args never raise KeyError", True)
except RepairableException:
    # A0 v2.6+ — the framework asks the model to repair instead of crashing.
    check("empty response args never raise KeyError", True)
except KeyError as e:
    check("empty response args never raise KeyError", False, repr(e))
except Exception as e:
    check("empty response args never raise KeyError", False, repr(e))

print("=" * 60)
if _failures:
    print("FAILURES:", _failures)
    sys.exit(1)
print("AGENT ZERO COMPATIBILITY: ALL GREEN")

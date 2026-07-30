"""Live compatibility harness — KAME against a REAL Agent Zero checkout.

Unlike the other suites (which stub A0 so they run anywhere), this one imports
the genuine `models`, `helpers.history`, `helpers.extension`, `agent` and
`tools.response` modules from an Agent Zero source tree and applies + reverts
KAME's monkey-patches against those real classes. It is the check to run when a
new Agent Zero version ships.

Usage:
    python tests/test_a0_compat.py /path/to/agent-zero
    A0_PATH=/path/to/agent-zero python tests/test_a0_compat.py

Requires A0's own runtime deps (litellm, langchain-core, ...) importable. Only
`sentence_transformers` is stubbed — it is heavy and nothing KAME touches uses
it. Exits 0 and prints SKIPPED when no A0 path is given.

Verified green against Agent Zero v2.7 (2026-07-30).
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

import models  # noqa: E402
import helpers.history as history  # noqa: E402
from helpers.litellm_transport import ChatCompletionsTransport  # noqa: E402
from helpers.llm_result import LLMResult  # noqa: E402
from helpers.rate_limiter import RateLimiter  # noqa: E402
from helpers.errors import RepairableException  # noqa: E402
from tools.response import ResponseTool  # noqa: E402
import agent as a0_agent  # noqa: E402

spec = importlib.util.spec_from_file_location("kame_engine", os.path.join(_KAME, "kame_engine.py"))
kame = importlib.util.module_from_spec(spec)
sys.modules["kame_engine"] = kame
spec.loader.exec_module(kame)
check("kame_engine imports against this Agent Zero", True)

# --- chunk parser detection -------------------------------------------------
mode = kame._kame_detect_chunk_mode()
check("a chunk parser was detected", mode in ("v1", "v2"), repr(mode))
if mode == "v2":
    check("parser bound to ChatCompletionsTransport.parse",
          kame._KAME_PARSE_CHUNK is ChatCompletionsTransport.parse)

parsed = kame._KAME_PARSE_CHUNK({"choices": [{"delta": {"content": "hello"}}]})
check("parser returns both delta keys",
      "response_delta" in parsed and "reasoning_delta" in parsed, str(parsed))
res = models.ChatGenerationResult()
res.add_chunk(parsed)
check("ChatGenerationResult.add_chunk accepts the parsed chunk", res.response == "hello", res.response)

# --- patch application + clean revert ---------------------------------------
orig_call = models.LiteLLMChatWrapper.unified_call
orig_turn = getattr(models.LiteLLMChatWrapper, "unified_turn", None)
orig_topic = history.Topic.summarize_messages
orig_bulk = history.Bulk.summarize

check("apply_kame_patch() returned True", kame.apply_kame_patch() is True)
check("unified_call patched", models.LiteLLMChatWrapper.unified_call is kame._kame_unified_call)
check("original unified_call stored", models.LiteLLMChatWrapper._kame_original_unified_call is orig_call)
if orig_turn is not None:
    check("unified_turn patched", models.LiteLLMChatWrapper.unified_turn is kame._kame_unified_turn)
    check("original unified_turn stored",
          models.LiteLLMChatWrapper._kame_original_unified_turn is orig_turn)
check("Topic.summarize_messages patched", history.Topic.summarize_messages is kame._kame_summarize_messages)
check("Bulk.summarize patched", history.Bulk.summarize is kame._kame_bulk_summarize)

kame.remove_kame_patch()
check("unified_call restored", models.LiteLLMChatWrapper.unified_call is orig_call)
if orig_turn is not None:
    check("unified_turn restored", models.LiteLLMChatWrapper.unified_turn is orig_turn)
check("Topic.summarize_messages restored", history.Topic.summarize_messages is orig_topic)
check("Bulk.summarize restored", history.Bulk.summarize is orig_bulk)

# --- LLMResult contract used by _kame_unified_turn --------------------------
r = LLMResult.from_chat(response="hi", reasoning="because", provider_model_key="gemini/x")
check("LLMResult.from_chat accepts KAME's kwargs", r.response == "hi" and r.reasoning == "because")
check("LLMResult.mode is chat_completions", r.mode == "chat_completions", r.mode)
check("LLMResult.capability is a dict", isinstance(r.capability, dict))

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

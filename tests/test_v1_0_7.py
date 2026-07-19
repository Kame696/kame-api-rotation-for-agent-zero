"""v1.0.7 — Response Shield: response-tool arg healing.

Upstream Agent Zero tools/response.py does
    self.args["text"] if "text" in self.args else self.args["message"]
which raises KeyError: 'message' when a model (seen with Codex-style models)
emits the response tool with empty/null/wrong-key args. Verified still
unfixed on agent0ai/agent-zero main as of 2026-07-19.

Covers (with stubs, no real A0):
  #1 empty args           -> {"text": ""} injected
  #2 null tool_args       -> healed to dict, then {"text": ""}
  #3 string tool_args     -> parsed / defaulted, then healed
  #4 non-dict JSON ("[]") -> forced to {}, then healed
  #5 wrong-key salvage    -> content/answer/response/answer_text -> text
  #6 null values          -> {"text": null} -> {"text": ""}
  #7 normal args          -> untouched (text or message present)
  #8 non-response tools   -> untouched (no text injection)
"""
import sys, types, os, asyncio


def _stub(name):
    m = types.ModuleType(name)
    sys.modules[name] = m
    return m


_helpers = _stub("helpers")
_ext = _stub("helpers.extension")


class Extension:
    def __init__(self, *a, **k):
        pass


_ext.Extension = Extension

_EXT_DIR = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "extensions", "python", "_functions",
    "agent", "Agent", "validate_tool_request", "start"))
sys.path.insert(0, _EXT_DIR)
import importlib
heal_mod = importlib.import_module("_10_kame_heal_tool_args")  # noqa: E402

_failures = []


def check(label, cond):
    print(("PASS  " if cond else "FAIL  ") + label)
    if not cond:
        _failures.append(label)


def run_heal(tool_request):
    ext = heal_mod.KameHealToolArgs()
    asyncio.run(ext.execute({"args": (None, tool_request), "kwargs": {}}))
    return tool_request


# 1 empty args on response tool -> text injected
tr = run_heal({"tool_name": "response", "tool_args": {}})
check("empty response args healed to {'text': ''}", tr["tool_args"] == {"text": ""})

# 2 null tool_args
tr = run_heal({"tool_name": "response", "tool_args": None})
check("null tool_args healed then text injected", tr["tool_args"] == {"text": ""})

# missing tool_args key entirely
tr = run_heal({"tool_name": "response"})
check("missing tool_args healed then text injected", tr["tool_args"] == {"text": ""})

# 3 string tool_args
tr = run_heal({"tool_name": "response", "tool_args": "{}"})
check("string '{}' parsed then text injected", tr["tool_args"] == {"text": ""})
tr = run_heal({"tool_name": "response", "tool_args": "not json"})
check("garbage string defaulted then text injected", tr["tool_args"] == {"text": ""})
tr = run_heal({"tool_name": "response", "tool_args": '{"text": "hi"}'})
check("string with valid text parsed and kept", tr["tool_args"] == {"text": "hi"})

# 4 non-dict JSON
tr = run_heal({"tool_name": "response", "tool_args": "[]"})
check("json list forced to dict then text injected", tr["tool_args"] == {"text": ""})

# 5 wrong-key salvage
for wrong in ("content", "answer", "response", "answer_text"):
    tr = run_heal({"tool_name": "response", "tool_args": {wrong: "hello"}})
    check(f"wrong key '{wrong}' salvaged into text", tr["tool_args"].get("text") == "hello")
tr = run_heal({"tool_name": "response", "tool_args": {"content": 123}})
check("non-string wrong-key value NOT salvaged, empty text injected",
      tr["tool_args"].get("text") == "")

# 6 null values on the real keys
tr = run_heal({"tool_name": "response", "tool_args": {"text": None}})
check("{'text': null} coerced to empty string", tr["tool_args"] == {"text": ""})
tr = run_heal({"tool_name": "response", "tool_args": {"message": None}})
check("{'message': null} coerced to empty string", tr["tool_args"] == {"message": ""})

# 7 normal args untouched
tr = run_heal({"tool_name": "response", "tool_args": {"text": "done"}})
check("normal text arg untouched", tr["tool_args"] == {"text": "done"})
tr = run_heal({"tool_name": "response", "tool_args": {"message": "done"}})
check("normal message arg untouched (no text injected)",
      tr["tool_args"] == {"message": "done"})

# 8 non-response tools: dict-heal still applies, but no text injection
tr = run_heal({"tool_name": "code_execution_tool", "tool_args": {}})
check("non-response tool: no text injected", tr["tool_args"] == {})
tr = run_heal({"tool_name": "code_execution_tool", "tool_args": None})
check("non-response tool: null still healed to dict", tr["tool_args"] == {})

# extension never crashes on garbage input
ext = heal_mod.KameHealToolArgs()
asyncio.run(ext.execute({"args": ()}))
asyncio.run(ext.execute({}))
asyncio.run(ext.execute({"args": (None, "notadict")}))
check("extension survives garbage inputs", True)

print("=" * 60)
if _failures:
    print("FAILURES:", _failures)
    sys.exit(1)
print("ALL v1.0.7 TESTS PASSED")

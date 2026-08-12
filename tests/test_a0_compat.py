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
import sys, types, os, re, importlib.util, inspect, asyncio

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

# =============================================================================
# LIVE 3 — OAuth / subscription providers must be left ALONE (v1.0.9)
# =============================================================================
# A0 v2.x ships plugins/_oauth: providers that authenticate by SUBSCRIPTION
# (OpenAI Codex, GitHub Copilot, ...) instead of by API key. A0 marks them by
# handing get_api_key() a sentinel string. KAME injects api_key=<chosen> and
# CALLER KWARGS WIN, so if KAME ever picked a key for one of these it would
# clobber the subscription auth and the user would just see auth errors.
#
# The check is DIFFERENTIAL on purpose: run the identical call with KAME
# uninstalled, then installed, and compare what reaches litellm. That way the
# test cannot pass (or fail) because of how this harness builds its wrapper —
# only a real behavioral difference shows up. Providers are auto-discovered, so
# a provider added upstream later is covered without touching this file.
# Discovery is deliberately layout-insensitive: A0 has already moved this code
# once (v1.14 keeps a Codex-only PROVIDERS set inside the get_api_key extension;
# v1.20+ has a plugins/_oauth/helpers/providers/ package with a registry). Import
# the modern path first, then fall back to reading the sentinel and the provider
# ids straight out of whatever get_api_key extension this checkout ships.
_oauth_ids, _oauth_sentinel = None, None
try:
    from plugins._oauth.helpers.providers.base import DUMMY_API_KEY as _oauth_sentinel
    from plugins._oauth.helpers.providers.registry import oauth_provider_ids
    _oauth_ids = sorted(oauth_provider_ids())
except Exception:
    try:
        import glob as _glob
        _hits = _glob.glob(os.path.join(
            _A0, "plugins", "_oauth", "extensions", "python", "_functions",
            "models", "get_api_key", "end", "*.py"))
        _ids: set = set()
        for _f in _hits:
            _src = open(_f, encoding="utf-8").read()
            _m = re.search(r"DUMMY_API_KEY\s*=\s*[\"']([^\"']+)[\"']", _src)
            if _m:
                _oauth_sentinel = _m.group(1)
            _ids.update(re.findall(r"[\"']([a-z0-9_]+_oauth)[\"']", _src))
        if _ids and _oauth_sentinel:
            _oauth_ids = sorted(_ids)
    except Exception:
        pass  # No OAuth plugin at all - nothing to protect, nothing to check.

if _oauth_ids:
    print(f"      (OAuth providers found: {', '.join(_oauth_ids)})")

    def _oauth_probe(provider_id):
        """What api_key reaches litellm for this provider, right now?"""
        _seen_keys.clear()
        conf = models.ModelConfig(type=models.ModelType.CHAT, provider=provider_id,
                                  name="oauth-model", api_key=_oauth_sentinel)
        w = models.LiteLLMChatWrapper(model="oauth-model", provider=provider_id,
                                      model_config=conf)
        asyncio.run(models.LiteLLMChatWrapper.unified_call(
            w, user_message="say hello", response_callback=_resp_cb,
            a0_api_mode="chat_completions"))
        return list(_seen_keys)

    # A subscription user has no API_KEY_<PROVIDER> in the .env, so KAME must
    # find nothing to rotate. Force that condition rather than trusting the
    # machine this test happens to run on.
    _real_all_keys = kame._get_all_api_keys
    kame._get_all_api_keys = lambda self: []

    for _pid in _oauth_ids:
        try:
            kame.remove_kame_patch()
            _native = _oauth_probe(_pid)
            kame.apply_kame_patch()
            _with_kame = _oauth_probe(_pid)
            check(f"LIVE: KAME changes nothing for OAuth provider '{_pid}'",
                  _native == _with_kame, f"native={_native} kame={_with_kame}")
            check(f"LIVE: KAME injects no key for OAuth provider '{_pid}'",
                  all(k in (None, _oauth_sentinel) for k in _with_kame), str(_with_kame))
        except Exception as e:  # noqa: BLE001
            check(f"LIVE: KAME changes nothing for OAuth provider '{_pid}'", False, repr(e))

    # Control: with a real pool KAME must STILL inject, or the checks above
    # would pass simply because KAME had stopped working altogether.
    kame._get_all_api_keys = lambda self: ["KEY-GOOD"]
    try:
        _ctrl = _oauth_probe(_oauth_ids[0])
        check("LIVE: control - KAME does still inject when keys DO exist",
              _ctrl == ["KEY-GOOD"], str(_ctrl))
    except Exception as e:  # noqa: BLE001
        check("LIVE: control - KAME does still inject when keys DO exist", False, repr(e))

    kame._get_all_api_keys = _real_all_keys
    # LIVE 3 toggled the patch; make sure it is on for the revert checks below.
    if models.LiteLLMChatWrapper.unified_call is orig_call:
        kame.apply_kame_patch()

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

# --- LIVE 4: the unusable-response floor, against this A0's real guard --------
# The shield ships as an extension FILE, not a patch, so it cannot be verified
# by fingerprinting a symbol. It is driven for real instead: A0's own
# StopUnusableResponseLoop stages the abort, KAME's _95 decides what to do with
# it. The first check is a CONTROL proving A0 alone really does abort on this
# build — without it the rest could pass vacuously.


def _load_ext(path, name):
    s = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(s)
    s.loader.exec_module(m)
    return m


class _FakeCtx:
    def __init__(self, log):
        self.log = log
        self.id = "test-ctx"
        self.streaming_agent = None


class _FakeLoopData:
    def __init__(self):
        self.iteration = 3
        self.params_persistent = {}


class _FakeAgent:
    """Enough of an Agent for the two shields and for A0's own _90 guard."""

    def __init__(self, log, number=0):
        self.context = _FakeCtx(log)
        self.loop_data = _FakeLoopData()
        self.number = number

    def read_prompt(self, name, **kw):
        # Read A0's REAL prompt files so the shields are matched against the
        # text this Agent Zero actually ships, not a stand-in.
        p = os.path.join(_A0, "prompts", name)
        if os.path.isfile(p):
            txt = open(p, encoding="utf-8").read()
            for k, v in kw.items():
                txt = txt.replace("{{" + k + "}}", str(v))
            return txt
        return f"PROMPT:{name}"


import helpers.log as a0_log  # noqa: E402

_floor_ext = _load_ext(os.path.join(
    _KAME, "extensions", "python", "_functions", "agent", "Agent",
    "hist_add_warning", "end", "_95_kame_unusable_floor.py"), "kame_floor")

check("the unusable-response floor extension loads against this A0",
      hasattr(_floor_ext, "KameUnusableFloor"))

# The floor must be a sync execute: hist_add_warning is a SYNC @extensible, and
# call_extensions_sync raises ValueError on an awaitable result.
check("the floor extension's execute is synchronous (sync extension point)",
      not inspect.iscoroutinefunction(_floor_ext.KameUnusableFloor.execute))

# -- 4a: floor vs A0's real StopUnusableResponseLoop ---------------------------
_guard_path = os.path.join(
    _A0, "extensions", "python", "_functions", "agent", "Agent",
    "hist_add_warning", "end", "_90_stop_unusable_response_loop.py")

if not os.path.isfile(_guard_path):
    # A0 < v2.4 has no such guard. The shield must then be a strict no-op.
    _log = a0_log.Log()
    _ag = _FakeAgent(_log)
    _d = {"args": (), "kwargs": {}, "result": "ok", "exception": None}
    _floor_ext.KameUnusableFloor(agent=_ag).execute(data=_d)
    check("floor shield is inert on an A0 with no unusable-response guard",
          _d["exception"] is None and _d["result"] == "ok")
else:
    _guard = _load_ext(_guard_path, "a0_stop_unusable")

    def _stage_abort(a0_limit, iterations):
        """Drive A0's REAL guard until it aborts. Returns (agent, data)."""
        _log = a0_log.Log()
        ag = _FakeAgent(_log)
        _guard.get_settings = lambda: {
            "max_consecutive_unusable_responses": a0_limit}
        msg = ag.read_prompt("fw.msg_misformat.md")
        data = None
        for i in range(iterations):
            ag.loop_data.iteration = i
            data = {"args": (), "kwargs": {"message": msg},
                    "result": "hist-msg", "exception": None}
            _guard.StopUnusableResponseLoop(agent=ag).execute(data=data)
        return ag, data

    def _with_floor(floor, agent, data):
        _floor_ext._read_floor = lambda _a: floor
        _floor_ext.KameUnusableFloor(agent=agent).execute(data=data)
        return data["exception"]

    # Control: A0 alone really does abort at its own limit of 2.
    _ag2, _d2 = _stage_abort(a0_limit=2, iterations=2)
    check("A0's own guard aborts at its configured limit (control)",
          isinstance(_d2["exception"], BaseException),
          f"exception={_d2['exception']!r}")

    # Floor above A0's limit -> KAME lets the turn continue.
    _ag3, _d3 = _stage_abort(a0_limit=2, iterations=2)
    check("KAME clears the abort while under its floor (2 < 5)",
          _with_floor(5, _ag3, _d3) is None)
    check("clearing the abort preserves A0's result (no None deref upstream)",
          _d3["result"] == "hist-msg")
    check("the abort line is rewritten to say the turn continued",
          any("[KAME]" in (i.content or "") for i in _ag3.context.log.logs),
          str([i.content for i in _ag3.context.log.logs]))

    # At the floor -> KAME agrees with the stop. This is the anti-bypass proof.
    _ag4, _d4 = _stage_abort(a0_limit=2, iterations=5)
    check("KAME does NOT clear the abort once its own floor is reached (5 >= 5)",
          isinstance(_with_floor(5, _ag4, _d4), BaseException))

    # floor 0 -> never interfere, A0 behaves exactly as shipped.
    _ag5, _d5 = _stage_abort(a0_limit=2, iterations=2)
    check("floor 0 leaves A0's abort untouched",
          isinstance(_with_floor(0, _ag5, _d5), BaseException))

    # A0's limit already generous -> nothing to lift, and nothing broken.
    _ag6, _d6 = _stage_abort(a0_limit=8, iterations=2)
    check("no abort staged when A0's own limit is not reached",
          _d6["exception"] is None)
    check("floor is a no-op when A0 has not aborted",
          _with_floor(5, _ag6, _d6) is None)

    # A real exception from hist_add_warning must NEVER be swallowed.
    _ag7, _d7 = _stage_abort(a0_limit=2, iterations=2)
    _real = RuntimeError("genuine failure")
    _d7["exception"] = _real
    check("a non-HandledException error is never cleared",
          _with_floor(5, _ag7, _d7) is _real)

    # If the wrapped call produced no result, clearing would return None to a
    # caller that immediately reads `.id` on it. Leave those alone.
    _ag8, _d8 = _stage_abort(a0_limit=2, iterations=2)
    try:
        from helpers.extension import _UNSET as _EXT_UNSET
        _d8["result"] = _EXT_UNSET
        check("abort is kept when the wrapped call produced no result",
              isinstance(_with_floor(5, _ag8, _d8), BaseException))
    except Exception as _e:
        check("abort is kept when the wrapped call produced no result", False, repr(_e))

# --- LIVE 5: post-response memory notifications are A0's, and A0 parks them ---
# Users report memory notifications landing UNDER the answer and the chat still
# looking busy. That is upstream by design — memorization is hooked at
# monologue_end, which agent.py runs in a `finally` AFTER the response tool has
# returned — and A0 already neutralises both visible side effects. KAME must add
# NOTHING here; these checks exist so a future A0 that regresses either
# behaviour is caught by the upgrade runbook instead of by a user.


def _simulate_memorize(log):
    """What _50_memorize_fragments / _51_memorize_solutions do: create the util
    item up front, then update it from a background thread."""
    a = log.log(type="util", heading="Memorizing new information...")
    b = log.log(type="util", heading="Memorizing succesful solutions...")
    return a, b


_log_mem = a0_log.Log()
_mem_items = _simulate_memorize(_log_mem)
_log_mem.set_initial_progress()      # A0's _90_waiting_for_input_msg
for _it in _mem_items:               # the background thread, later
    _it.update(heading="Memorization completed: 3 memories processed")

check("A0 keeps the status line parked through post-response memory updates",
      _log_mem.progress_active is False,
      f"progress_active={_log_mem.progress_active} progress={_log_mem.progress!r}")

# The WebUI side of the same guarantee: trailing utilities must not reopen the
# collapsible "Processing..." group under a finished answer. The rule lives in
# `classifyMessageRenderUnits`, which only exists from A0 v2.8 — older builds
# have no such classifier, so there is nothing to assert and the check is
# reported as not applicable rather than failed.
_mw = os.path.join(_A0, "webui", "js", "message-window.js")
_mw_src = open(_mw, encoding="utf-8").read() if os.path.isfile(_mw) else ""
if "classifyMessageRenderUnits" not in _mw_src:
    print("N/A   A0's WebUI process-group classifier (added in v2.8) - not on this build")
else:
    check("A0's WebUI still refuses to reopen a process group after a response",
          "post-response utilities cannot reopen the group" in _mw_src,
          "message-window.js no longer documents the post-response util rule")

print("=" * 60)
if _failures:
    print("FAILURES:", _failures)
    sys.exit(1)
print("AGENT ZERO COMPATIBILITY: ALL GREEN")

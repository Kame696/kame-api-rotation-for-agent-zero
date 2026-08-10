"""v1.0.9 — DELEGATED EXECUTION + shape-based binding.

The 1.0.9 thesis: KAME should choose the API key and NOTHING else. Agent Zero
makes the call, owns the stream, parses it and builds the result. That removes
most of the surface that used to break on every A0 release.

These tests run with stubs (no real Agent Zero, no litellm, no network) and cover:

  A. Delegation contract — the original A0 method is what actually runs, it
     receives the chosen key, A0's own retry loop is switched off, the caller's
     message list is never mutated, and A0's result object is returned verbatim.
  B. Shape-based binding — the entry points are found by SIGNATURE, so an
     upstream RENAME does not disable rotation; legacy-name fallback (layer 2)
     and the safe no-op (layer 3) both behave.
  C. Retry-knob extraction — the kwargs that disable A0's retry loop are read
     out of A0's own source at runtime, so a rename there is picked up too.
  D. Empty-answer handling — bounded rotation on a truly empty stream, and NO
     rotation on a blank early stop (the v1.0.8 contract, preserved).
  E. Callback transparency — A0 sees the caller's callbacks unchanged, including
     the early-stop RETURN VALUE, and sees None where the caller passed None.
  F. Version single-sourcing — no hardcoded version string left in the engine.
"""
import sys, types, os, asyncio, inspect, re


def _stub(name):
    m = types.ModuleType(name)
    sys.modules[name] = m
    return m


_stub("langchain_core")
_lc = _stub("langchain_core.messages")


class _Msg:
    def __init__(self, content=""):
        self.content = content

    def __repr__(self):
        return f"_Msg({self.content!r})"


class _Sys(_Msg):
    pass


_lc.SystemMessage = _Sys
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


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ((" - " + detail) if detail and not cond else ""))
    if not cond:
        _failures.append(name)


# --- the minimum Agent Zero look-alike KAME needs ---------------------------

class _Conf:
    provider = "gemini"


class FakeA0Model:
    """Stands in for A0's LiteLLMChatWrapper. `unified_call` has A0's real
    signature; the body is replaced per-test by `script`."""

    model_name = "gemini/gemini-3.5-flash"

    def __init__(self):
        self.a0_model_conf = _Conf()
        self.kwargs = {}
        self.seen = []          # one dict per delegated attempt
        self.script = []        # list of callables(attempt_no) -> result | raises

    async def unified_call(self, system_message="", user_message="", messages=None,
                           response_callback=None, reasoning_callback=None,
                           tokens_callback=None, rate_limiter_callback=None,
                           explicit_caching=False, **kwargs):
        # A0 mutates the list it is given — reproduce that faithfully, it is the
        # exact hazard _kame_attempt_delegated has to defend against.
        if messages is None:
            messages = []
        if system_message:
            messages.insert(0, _Sys(content=system_message))
        if user_message:
            messages.append(_Msg(content=user_message))
        max_retries = int(kwargs.pop("a0_retry_attempts", 2))
        retry_delay = float(kwargs.pop("a0_retry_delay_seconds", 1.5))
        self.seen.append({
            "api_key": kwargs.get("api_key"),
            "messages": list(messages),
            "system_message": system_message,
            "user_message": user_message,
            "explicit_caching": explicit_caching,
            "kwargs": dict(kwargs),
            "max_retries": max_retries,
            "retry_delay": retry_delay,
            "response_callback": response_callback,
            "reasoning_callback": reasoning_callback,
            "tokens_callback": tokens_callback,
        })
        step = self.script[min(len(self.seen) - 1, len(self.script) - 1)]
        return await step(len(self.seen))

    async def unified_turn(self, system_message="", user_message="", messages=None,
                           response_callback=None, reasoning_callback=None,
                           tokens_callback=None, rate_limiter_callback=None,
                           explicit_caching=False, **kwargs):
        return await self.unified_call(
            system_message=system_message, user_message=user_message, messages=messages,
            response_callback=response_callback, reasoning_callback=reasoning_callback,
            tokens_callback=tokens_callback, rate_limiter_callback=rate_limiter_callback,
            explicit_caching=explicit_caching, **kwargs)

    def _helper(self):          # not a coroutine — must never be bound
        return None

    async def _private_stream(self, messages=None, response_callback=None,
                              reasoning_callback=None, tokens_callback=None):
        return None             # underscored — must never be bound


class _Err(Exception):
    def __init__(self, msg, status_code=None):
        super().__init__(msg)
        self.status_code = status_code


def _ok(value):
    async def _step(_n):
        return value
    return _step


def _boom(exc):
    async def _step(_n):
        raise exc
    return _step


def _fresh(keys=("AAA", "BBB", "CCC")):
    """A model + a KAME wrapper bound to its real unified_call, clean health."""
    K._KAME_KEY_HEALTH = {}
    K._get_all_api_keys = lambda self: list(keys)
    model = FakeA0Model()
    wrapper = K._kame_make_entry_wrapper("unified_call", FakeA0Model.unified_call)
    return model, wrapper


# =============================================================================
# A. DELEGATION CONTRACT
# =============================================================================
RESULT = ("the answer", "the reasoning")

# A1 — A0's own method runs, with the key KAME chose, and A0's retry loop off.
model, wrap = _fresh()
model.script = [_ok(RESULT)]
out = asyncio.run(wrap(model, messages=[_Msg("hi")]))
check("A1 the original A0 method is what actually executes", len(model.seen) == 1)
check("A1 A0's result object is returned VERBATIM (identity, not a rebuild)",
      out is RESULT)
check("A1 the chosen key is injected as api_key",
      model.seen[0]["api_key"] in ("AAA", "BBB", "CCC"))
check("A1 A0's own retry loop is switched off (0 attempts)",
      model.seen[0]["max_retries"] == 0)
check("A1 A0's own retry delay is switched off (0s)",
      model.seen[0]["retry_delay"] == 0.0)
check("A1 explicit_caching is forced False (free-tier keys 429 on cache-create)",
      model.seen[0]["explicit_caching"] is False)

# A2 — the caller's message list must survive rotation untouched.
model, wrap = _fresh()
model.script = [_boom(_Err("429 RESOURCE_EXHAUSTED quota", 429)),
                _boom(_Err("429 RESOURCE_EXHAUSTED quota", 429)),
                _ok(RESULT)]
caller_msgs = [_Msg("only message")]
out = asyncio.run(wrap(model, system_message="SYS", user_message="USR",
                       messages=caller_msgs))
check("A2 three attempts happened (rotation on 429)", len(model.seen) == 3)
check("A2 the CALLER's list is never mutated by A0", len(caller_msgs) == 1)
check("A2 every attempt got exactly 3 messages (sys + msg + user, no duplication)",
      [len(s["messages"]) for s in model.seen] == [3, 3, 3],
      str([len(s["messages"]) for s in model.seen]))
check("A2 the system prompt is never duplicated across retries",
      all(sum(1 for m in s["messages"] if isinstance(m, _Sys)) == 1 for s in model.seen))
check("A2 system_message/user_message are passed EMPTY (already merged by KAME)",
      all(s["system_message"] == "" and s["user_message"] == "" for s in model.seen))

# A3 — rotation actually moves to a different key.
check("A3 a 429 rotates to a DIFFERENT key",
      model.seen[0]["api_key"] != model.seen[1]["api_key"])

# A4 — terminal errors abort, control-flow exceptions pass through.
model, wrap = _fresh()
model.script = [_boom(_errs.InterventionException("user typed"))]
try:
    asyncio.run(wrap(model, messages=[_Msg("hi")]))
    check("A4 InterventionException propagates (nudge works natively)", False)
except _errs.InterventionException:
    check("A4 InterventionException propagates (nudge works natively)", True)
check("A4 a passthrough exception is NOT retried", len(model.seen) == 1)

model, wrap = _fresh()
_term = _Err("context window exceeded: 1000000 tokens > limit", 400)
model.script = [_boom(_term)]
try:
    asyncio.run(wrap(model, messages=[_Msg("hi")]))
    check("A4 a terminal error surfaces instead of rotating forever",
          not K._is_terminal_error(_term))
except Exception as e:
    check("A4 a terminal error surfaces instead of rotating forever", e is _term)

# A5 — no keys configured: straight passthrough, carousel never engages.
K._get_all_api_keys = lambda self: []
model = FakeA0Model()
model.script = [_ok(RESULT)]
wrap = K._kame_make_entry_wrapper("unified_call", FakeA0Model.unified_call)
passthrough_msgs = [_Msg("hi")]
out = asyncio.run(wrap(model, system_message="SYS", messages=passthrough_msgs))
check("A5 with no keys the call is handed straight to A0", out is RESULT)
check("A5 with no keys KAME does not inject a key", model.seen[0]["api_key"] is None)
check("A5 with no keys KAME does not touch A0's retry loop",
      model.seen[0]["max_retries"] == 2)
check("A5 with no keys A0 receives system_message as given",
      model.seen[0]["system_message"] == "SYS")

# A6 — the free-tier cache flag can never reach the provider.
model, wrap = _fresh()
model.script = [_ok(RESULT)]
asyncio.run(wrap(model, messages=[_Msg("hi")], a0_explicit_prompt_caching=True))
check("A6 a0_explicit_prompt_caching is stripped before the call",
      "a0_explicit_prompt_caching" not in model.seen[0]["kwargs"])

# A7 — unrelated caller kwargs are forwarded untouched.
model, wrap = _fresh()
model.script = [_ok(RESULT)]
asyncio.run(wrap(model, messages=[_Msg("hi")], temperature=0.25, tools=["x"]))
check("A7 caller kwargs reach A0 unchanged",
      model.seen[0]["kwargs"].get("temperature") == 0.25
      and model.seen[0]["kwargs"].get("tools") == ["x"])


# =============================================================================
# B. SHAPE-BASED BINDING (the compatibility win)
# =============================================================================
found = K._kame_find_entry_points(FakeA0Model)
check("B1 both A0 entry points are found by SHAPE",
      set(found) == {"unified_call", "unified_turn"}, str(found))
check("B1 non-coroutine helpers are ignored", "_helper" not in found)
check("B1 underscored internals are ignored", "_private_stream" not in found)


class RenamedA0(FakeA0Model):
    """A0 of the future: same shape, completely different names."""
    async def run_model_turn(self, system_message="", user_message="", messages=None,
                             response_callback=None, reasoning_callback=None,
                             tokens_callback=None, rate_limiter_callback=None,
                             explicit_caching=False, **kwargs):
        return await FakeA0Model.unified_call(
            self, system_message=system_message, user_message=user_message,
            messages=messages, response_callback=response_callback,
            reasoning_callback=reasoning_callback, tokens_callback=tokens_callback,
            rate_limiter_callback=rate_limiter_callback,
            explicit_caching=explicit_caching, **kwargs)


check("B2 a RENAMED entry point is still found by shape",
      "run_model_turn" in K._kame_find_entry_points(RenamedA0))

K._KAME_BOUND_ENTRY_POINTS = []
_orig_renamed = RenamedA0.run_model_turn
layer = K._kame_bind_entry_points(RenamedA0)
check("B2 binding a renamed-only build reports layer 1", layer == 1)
check("B2 the renamed entry point is actually wrapped",
      RenamedA0.run_model_turn is not _orig_renamed)
check("B2 the original is stashed for a clean uninstall",
      RenamedA0._kame_original_run_model_turn is _orig_renamed)
K._get_all_api_keys = lambda self: ["AAA", "BBB"]
K._KAME_KEY_HEALTH = {}
_rm = RenamedA0()
_rm.script = [_ok(RESULT)]
check("B2 rotation really runs through the renamed entry point",
      asyncio.run(_rm.run_model_turn(messages=[_Msg("hi")])) is RESULT
      and _rm.seen[0]["api_key"] in ("AAA", "BBB"))
K._kame_unbind_entry_points(RenamedA0)
check("B2 uninstall restores the renamed entry point exactly",
      RenamedA0.run_model_turn is _orig_renamed)


class _FutureBase:
    """A0 of the further future: the entry point was renamed AND moved into a base
    class. `vars(cls)` alone would miss it and drop KAME to layer 3 - the exact
    failure shape-detection exists to prevent."""
    async def do_llm_turn(self, system_message="", user_message="", messages=None,
                          response_callback=None, reasoning_callback=None,
                          tokens_callback=None, rate_limiter_callback=None,
                          explicit_caching=False, **kwargs):
        return await FakeA0Model.unified_call(
            self, system_message=system_message, user_message=user_message,
            messages=messages, response_callback=response_callback,
            reasoning_callback=reasoning_callback, tokens_callback=tokens_callback,
            rate_limiter_callback=rate_limiter_callback,
            explicit_caching=explicit_caching, **kwargs)


class InheritedA0(_FutureBase):
    """Defines nothing itself - everything is inherited."""
    def __init__(self):
        self.seen = []
        self.script = []
        self.model_name = "future/model"


check("B2b an INHERITED renamed entry point is still found by shape",
      "do_llm_turn" in K._kame_find_entry_points(InheritedA0))

K._KAME_BOUND_ENTRY_POINTS = []
_orig_inherited = InheritedA0.do_llm_turn
layer = K._kame_bind_entry_points(InheritedA0)
check("B2b binding an inherit-only build still reports layer 1", layer == 1)
check("B2b the wrapper is installed on the SUBCLASS, shadowing the base",
      "do_llm_turn" in vars(InheritedA0)
      and _FutureBase.do_llm_turn is _orig_inherited)
K._get_all_api_keys = lambda self: ["AAA", "BBB"]
K._KAME_KEY_HEALTH = {}
_im = InheritedA0()
_im.script = [_ok(RESULT)]
check("B2b rotation really runs through the inherited entry point",
      asyncio.run(_im.do_llm_turn(messages=[_Msg("hi")])) is RESULT
      and _im.seen[0]["api_key"] in ("AAA", "BBB"))
K._kame_unbind_entry_points(InheritedA0)
check("B2b uninstall leaves the base class untouched",
      _FutureBase.do_llm_turn is _orig_inherited
      and InheritedA0.do_llm_turn is _orig_inherited)


class LegacyOnlyA0:
    """A0 whose signature changed so much that shape detection misses it, but
    whose method names are still the historical ones."""
    async def unified_call(self, prompt, **kwargs):
        return ("legacy", "")

    async def unified_turn(self, prompt, **kwargs):
        return ("legacy", "")


check("B3 shape detection correctly finds nothing here",
      K._kame_find_entry_points(LegacyOnlyA0) == [])
K._KAME_BOUND_ENTRY_POINTS = []
check("B3 legacy-name fallback engages as layer 2",
      K._kame_bind_entry_points(LegacyOnlyA0) == 2)
check("B3 layer 2 binds both historical names",
      set(K._KAME_BOUND_ENTRY_POINTS) == {"unified_call", "unified_turn"})
K._kame_unbind_entry_points(LegacyOnlyA0)


class UnknownA0:
    """Nothing KAME recognizes at all."""
    async def generate(self, prompt):
        return ""


K._KAME_BOUND_ENTRY_POINTS = []
_before = UnknownA0.generate
check("B4 an unrecognizable A0 reports layer 3", K._kame_bind_entry_points(UnknownA0) == 3)
check("B4 layer 3 wraps NOTHING (A0 left exactly as found)",
      UnknownA0.generate is _before and not hasattr(UnknownA0, "_kame_original_generate"))
check("B4 layer 3 records no bound entry points", K._KAME_BOUND_ENTRY_POINTS == [])

# B5 — re-binding must always wrap the ORIGINAL, never a wrapper (hot reload).
class ReloadA0(FakeA0Model):
    pass


_orig_call = ReloadA0.unified_call
K._KAME_BOUND_ENTRY_POINTS = []
K._kame_bind_entry_points(ReloadA0)
_first = ReloadA0.unified_call
K._kame_bind_entry_points(ReloadA0)
check("B5 re-binding does not stack wrappers",
      ReloadA0._kame_original_unified_call is _orig_call)
K._kame_unbind_entry_points(ReloadA0)
check("B5 uninstall after a re-bind still restores the true original",
      ReloadA0.unified_call is _orig_call)


# =============================================================================
# C. RETRY-KNOB EXTRACTION
# =============================================================================
K._KAME_RETRY_KNOBS_CACHE = {}
knobs = K._kame_retry_knobs(FakeA0Model.unified_call)
check("C1 a0_retry_attempts is discovered in A0's source",
      knobs.get("a0_retry_attempts") == 0, str(knobs))
check("C1 a0_retry_delay_seconds is discovered and zeroed as a float",
      knobs.get("a0_retry_delay_seconds") == 0.0, str(knobs))


class FutureKnobsA0:
    async def unified_call(self, messages=None, response_callback=None,
                           reasoning_callback=None, tokens_callback=None, **kwargs):
        tries = int(kwargs.pop("a0_retry_max_tries", 3))
        pause = float(kwargs.pop("a0_retry_backoff_seconds", 2.0))
        return (str(tries), str(pause))


K._KAME_RETRY_KNOBS_CACHE = {}
future = K._kame_retry_knobs(FutureKnobsA0.unified_call)
check("C2 a RENAMED retry knob is still discovered",
      future.get("a0_retry_max_tries") == 0, str(future))
check("C2 a renamed DELAY knob is zeroed as a float",
      future.get("a0_retry_backoff_seconds") == 0.0, str(future))

K._KAME_RETRY_KNOBS_CACHE = {}
check("C3 unreadable source degrades to no knobs instead of raising",
      K._kame_retry_knobs(len) == {})


# =============================================================================
# D. EMPTY-ANSWER HANDLING
# =============================================================================
model, wrap = _fresh()
model.script = [_ok(("", "")), _ok(("", "")), _ok(RESULT)]
out = asyncio.run(wrap(model, messages=[_Msg("hi")]))
check("D1 a truly empty answer rotates to another key", len(model.seen) == 3)
check("D1 the good answer after the empties is what is returned", out is RESULT)

# D2 — bounded: once the budget is spent, the empty answer is accepted.
model, wrap = _fresh()
model.script = [_ok(("", ""))]
out = asyncio.run(wrap(model, messages=[_Msg("hi")]))
check("D2 empty-answer rotation is bounded by the budget (no infinite loop)",
      len(model.seen) == K._KAME_EMPTY_RETRY_BUDGET + 1, str(len(model.seen)))
check("D2 after the budget the empty answer is returned like native A0",
      out == ("", ""))

# D3 — a BLANK EARLY STOP is a valid answer from a healthy key (v1.0.8 contract).
model, wrap = _fresh()


async def _stopping_cb(delta, full):
    return ""          # A0's early-stop signal, with blank text


async def _blank_early_stop(_n):
    cb = model.seen[-1]["response_callback"]
    await cb("some streamed text", "some streamed text")   # content DID stream
    return ("", "")


model.script = [_blank_early_stop]
out = asyncio.run(wrap(model, messages=[_Msg("hi")], response_callback=_stopping_cb))
check("D3 a blank EARLY STOP does not rotate (content streamed)", len(model.seen) == 1)
check("D3 a blank early stop returns blank, exactly like native A0", out == ("", ""))
check("D3 a blank early stop leaves every key healthy",
      all(k.get("sick_until", 0) == 0
          for v in K._KAME_KEY_HEALTH.values() for k in v.get("keys", {}).values()))


# =============================================================================
# E. CALLBACK TRANSPARENCY
# =============================================================================
model, wrap = _fresh()
_seen_returns = []


async def _early_stop_cb(delta, full):
    return "STOPPED-TEXT" if full.endswith("}}") else None


async def _drive_callbacks(_n):
    s = model.seen[-1]
    _seen_returns.append(await s["response_callback"]("a", "partial"))
    _seen_returns.append(await s["response_callback"]("b", '{"x":{"y":1}}'))
    await s["reasoning_callback"]("r", "r")
    await s["tokens_callback"]("t", 1)
    return RESULT


_reason_seen, _tokens_seen = [], []


async def _reason_cb(delta, full):
    _reason_seen.append(delta)


async def _tokens_cb(text, n):
    _tokens_seen.append((text, n))


model.script = [_drive_callbacks]
out = asyncio.run(wrap(model, messages=[_Msg("hi")], response_callback=_early_stop_cb,
                       reasoning_callback=_reason_cb, tokens_callback=_tokens_cb))
check("E1 the response_callback's None is passed back to A0 untouched",
      _seen_returns[0] is None)
check("E1 the early-stop RETURN VALUE reaches A0 untouched (v1.0.8 contract kept)",
      _seen_returns[1] == "STOPPED-TEXT")
check("E2 the reasoning_callback reaches the caller", _reason_seen == ["r"])
check("E2 the tokens_callback reaches the caller", _tokens_seen == [("t", 1)])

model, wrap = _fresh()
model.script = [_ok(RESULT)]
asyncio.run(wrap(model, messages=[_Msg("hi")]))
check("E3 a None callback stays None (A0 must still see a NON-streaming call)",
      model.seen[0]["response_callback"] is None
      and model.seen[0]["reasoning_callback"] is None
      and model.seen[0]["tokens_callback"] is None)


# =============================================================================
# F. VERSION SINGLE-SOURCING + LAYER-3 SAFETY
# =============================================================================
check("F1 KAME_VERSION is 1.0.9", K.KAME_VERSION == "1.0.9")
_engine_src = open(os.path.join(os.path.dirname(__file__), "..", "kame_engine.py"),
                   encoding="utf-8").read()
_banner_src = inspect.getsource(K._print_shield_status)
check("F1 the banner prints KAME_VERSION, not a literal",
      "KAME_VERSION" in _banner_src and not re.search(r"KAME v1\.0\.\d", _banner_src))
_apply_src = inspect.getsource(K.apply_kame_patch)
check("F1 the patch-failure line prints KAME_VERSION, not a literal",
      "KAME_VERSION" in _apply_src and not re.search(r"KAME v1\.0\.\d", _apply_src))
check("F2 KAME no longer imports litellm at module level",
      not re.search(r"^\s*(import litellm|from litellm import)", _engine_src, re.M))
check("F3 the removed 1.0.8 stream machinery is really gone",
      not any(hasattr(K, n) for n in
              ("_KAME_PARSE_CHUNK", "_KAME_CHUNK_MODE", "_kame_detect_chunk_mode",
               "_kame_chunk_aiter", "_kame_clean_call_kwargs",
               "_kame_unified_call", "_kame_unified_turn", "acompletion")),
      str([n for n in ("_KAME_PARSE_CHUNK", "_KAME_CHUNK_MODE", "_kame_detect_chunk_mode",
                       "_kame_chunk_aiter", "_kame_clean_call_kwargs",
                       "_kame_unified_call", "_kame_unified_turn", "acompletion")
           if hasattr(K, n)]))
check("F4 layer 3 warns the user in the console and links the issue tracker",
      "github.com/Kame696/kame-api-engine/issues" in _apply_src)
check("F5 accessory shields cannot take down the rotation core",
      _apply_src.index("_patch_rate_limiters()") > _apply_src.index("_kame_bind_entry_points"))


print("=" * 60)
if _failures:
    print("FAILURES:", _failures)
    sys.exit(1)
print("ALL v1.0.9 TESTS PASSED")

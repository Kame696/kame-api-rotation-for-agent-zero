"""v1.6.0.4 — a thinking token is not an answer.

Found in the Hermes port, where it cost two turns out of a twelve-minute run
on 2026-09-04: the flag meaning "the user has seen part of the answer" was
being set by things that show no answer at all, and there it stopped the
rotation outright. Agent Zero has the same confusion with a smaller blast
radius — the carousel rotates every non-terminal failure regardless — but the
flag still gates two things here, and both are about the answer:

* whether an empty result may be retried on another key
  (``_empty_budget > 0 and not ctx["progress"]["any"]``), and
* whether the log may say "mid-stream drop **after partial output**".

``reasoning_callback`` was setting it. A model that thinks and then returns
nothing is *exactly* the empty answer this loop exists to rotate around —
Gemini spending its whole budget on thoughts is the textbook case — so the
retry was switched off on precisely the models that need it, and the warning
told the reader their answer had been cut when nothing had been shown.

Group A is which callback records what. Group B is the consequence: an empty
answer after reasoning is still an empty answer. Group C is the release.
"""
import sys, types, os, asyncio


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

_warnings = []


class _PrintStyle:
    def __init__(self, *a, **k): pass
    def print(self, *a, **k): pass
    @staticmethod
    def warning(*a, **k): _warnings.append(" ".join(str(x) for x in a))
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


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _ctx():
    return {"progress": {"any": False, "reasoning": False}}


async def _noop(*a, **k):
    return None


async def _stop(*a, **k):
    # A0's early-stop signal: a truthy return from ``response_callback`` means
    # "a complete tool request has streamed, stop now". It must survive the shim.
    return "stop"


# --- A. which callback records what -----------------------------------------

ctx = _ctx()
resp, reas, toks = K._kame_wrap_callbacks(ctx, _noop, _noop, _noop)

run(reas("thinking...", "thinking..."))
check("reasoning does not claim the answer was shown", ctx["progress"]["any"] is False)
check("reasoning is recorded on its own", ctx["progress"]["reasoning"] is True)

ctx2 = _ctx()
resp2, reas2, toks2 = K._kame_wrap_callbacks(ctx2, _noop, _noop, _noop)
run(resp2("Hello", "Hello"))
check("the response callback still claims the answer", ctx2["progress"]["any"] is True)

ctx3 = _ctx()
resp3, reas3, toks3 = K._kame_wrap_callbacks(ctx3, _noop, _noop, _noop)
run(toks3("Hello", 1))
check("the tokens callback still claims the answer", ctx3["progress"]["any"] is True)

# The shims stay transparent — A0 reads the return value of the response
# callback to know when to stop early, and a shim that ate it would hang the
# stream open past the point A0 wanted it closed.
ctx4 = _ctx()
resp4, _, _ = K._kame_wrap_callbacks(ctx4, _stop, _noop, _noop)
check("the early-stop signal is passed straight back", run(resp4("x", "x")) == "stop")

# A callback A0 did not supply stays unsupplied. Handing A0 a wrapper around
# None would turn "this host does not stream reasoning" into a crash.
ctx5 = _ctx()
none_resp, none_reas, none_toks = K._kame_wrap_callbacks(ctx5, None, None, None)
check("absent callbacks stay absent",
      (none_resp, none_reas, none_toks) == (None, None, None))

# Reasoning after real text does not erase the fact that text was shown.
ctx6 = _ctx()
resp6, reas6, _ = K._kame_wrap_callbacks(ctx6, _noop, _noop, _noop)
run(resp6("Hello", "Hello"))
run(reas6("more thinking", "more thinking"))
check("reasoning never clears a delivery already recorded",
      ctx6["progress"]["any"] is True and ctx6["progress"]["reasoning"] is True)


# --- B. the consequence -----------------------------------------------------

# The gate, stated directly. This is the line in the carousel:
#   if (_empty_budget > 0 and not ctx["progress"]["any"] and _result_is_empty(result))
# Before 1.6.0.4, one reasoning delta made the middle clause false for the rest
# of the attempt, so an empty answer from a thinking model was returned to A0
# as though a healthy key had deliberately answered with nothing.
ctx7 = _ctx()
_, reas7, _ = K._kame_wrap_callbacks(ctx7, _noop, _noop, _noop)
run(reas7("thinking hard", "thinking hard"))
check("an empty answer after reasoning is still eligible for another key",
      (not ctx7["progress"]["any"]) is True)

ctx8 = _ctx()
resp8, _, _ = K._kame_wrap_callbacks(ctx8, _noop, _noop, _noop)
run(resp8("half an answer", "half an answer"))
check("an empty answer after real text is NOT retried",
      (not ctx8["progress"]["any"]) is False)

# The warning is gated on the same flag, and "partial output" has to mean
# output. Nothing here calls the carousel — the check is that the flag the
# warning reads is the delivery flag, not the liveness one.
src = K._kame_wrap_callbacks.__doc__ or ""
check("the reason is written where the rule lives", "reasoning" in src.lower())

# The per-attempt reset clears both, or the second attempt of a turn would
# inherit the first attempt's thinking.
import inspect  # noqa: E402
carousel_src = inspect.getsource(K._kame_carousel)
check("the per-attempt reset clears the answer flag",
      'ctx["progress"]["any"] = False' in carousel_src)
check("the per-attempt reset clears the reasoning flag",
      'ctx["progress"]["reasoning"] = False' in carousel_src)

# A mutation guard: the rule is one line and a tidy-up could put it back.
wrap_src = inspect.getsource(K._kame_wrap_callbacks)
reasoning_body = wrap_src.split("async def wrapped_reasoning", 1)[-1].split("if tokens_callback", 1)[0]
check("the reasoning shim does not set the answer flag",
      'progress["any"] = True' not in reasoning_body)
check("the reasoning shim sets the reasoning flag",
      'progress["reasoning"] = True' in reasoning_body)


# --- C. the release ---------------------------------------------------------

check("the engine says 1.6.0.4", K.KAME_VERSION == "1.6.0.4")

here = os.path.dirname(__file__)
manifest = open(os.path.join(here, "..", "plugin.yaml"), encoding="utf-8").read()
check("plugin.yaml agrees with the engine, to the digit",
      "version: 1.6.0.4" in manifest)

changelog = open(os.path.join(here, "..", "CHANGELOG.md"), encoding="utf-8").read()
check("the changelog has a 1.6.0.4 entry", "## v1.6.0.4" in changelog)
check("the changelog marks exactly one release current",
      changelog.count("— current") == 1)


print("=" * 60)
if _failures:
    print("FAILURES:", _failures)
    sys.exit(1)
print("ALL v1.6.0.4 TESTS PASSED")

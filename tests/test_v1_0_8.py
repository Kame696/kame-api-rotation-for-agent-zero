"""v1.0.8 — early-stop contract + 403 quarantine, updated for v1.0.9.

Since Agent Zero V2, `Agent.monologue`'s stream callback RETURNS the accumulated
text as soon as a complete, valid tool request has been streamed; native
`unified_call` / `unified_turn` then break the stream and use that text
(`stop_response`). Before v1.0.8 KAME owned the stream, awaited the callback and
threw the return value away, so the model kept generating past a finished tool
call on every turn.

v1.0.9 hands the stream back to Agent Zero, so honoring the contract is once
again A0's own code doing it. KAME's remaining duty is to be TRANSPARENT: the
caller's callback must reach A0 unchanged, its return value must reach A0
unchanged, and a blank early stop must not be mistaken for a dead key. That is
what groups A-E assert now (the behavior under test is identical; only who
implements it changed).

Group F (403 PERMISSION_DENIED quarantine) and group G (the cosmetic banner can
never fail the patch) are unchanged from v1.0.8 — both are still KAME's own.
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


class A0Model:
    """Agent Zero's model, honoring the early-stop contract in its OWN code —
    exactly as native A0 does since V2."""
    model_name = "gemini/gemini-3.5-flash"

    def __init__(self, chunks):
        self.a0_model_conf = _Conf()
        self.kwargs = {}
        self.chunks = list(chunks)
        self.pulled = 0
        self.calls = 0

    async def unified_call(self, system_message="", user_message="", messages=None,
                           response_callback=None, reasoning_callback=None,
                           tokens_callback=None, rate_limiter_callback=None,
                           explicit_caching=False, **kwargs):
        kwargs.pop("a0_retry_attempts", None)
        kwargs.pop("a0_retry_delay_seconds", None)
        self.calls += 1
        result, stop_response = "", None
        for c in self.chunks:
            self.pulled += 1
            result += c
            if response_callback:
                stop_response = await response_callback(c, result)
            if tokens_callback:
                await tokens_callback(c, len(c))
            if stop_response is not None:
                result = stop_response          # A0's own early-stop handling
                break
        return (result, "")


TOOL_JSON = '{"tool_name": "response", "tool_args": {"text": "done"}}'
CHUNKS = [TOOL_JSON, " TRAILING", " JUNK"]


def _run(chunks, callback, **extra):
    """Run one clean carousel call over `chunks`, returning (result, model)."""
    K._KAME_KEY_HEALTH = {}
    K._get_all_api_keys = lambda self: ["AAA", "BBB"]
    model = A0Model(chunks)
    wrapper = K._kame_make_entry_wrapper("unified_call", A0Model.unified_call)
    res = asyncio.run(wrapper(model, messages=[_Msg("hi")],
                              response_callback=callback, **extra))
    return res, model


# --- A + B: the early-stop return value reaches A0 and stops the stream ------
async def _cb_stop(delta, full):
    # mimics A0 V2 Agent.monologue: return the full text once the tool JSON closes
    return full if full.endswith("}}") else None


res_ab, m_ab = _run(CHUNKS, _cb_stop)
check("early stop halts the stream (trailing chunks never pulled)", m_ab.pulled == 1)
check("early stop does not trigger a key rotation (single provider call)", m_ab.calls == 1)
check("returned text becomes the result (trailing junk dropped)", res_ab[0] == TOOL_JSON)


# --- C: a callback that always returns None consumes the whole stream --------
async def _cb_none(delta, full):
    return None


res_c, m_c = _run(CHUNKS, _cb_none)
check("callback returning None consumes the whole stream (A0 v1.x behavior)",
      m_c.pulled == len(CHUNKS))
check("full accumulated text returned when no early stop",
      res_c[0] == "".join(CHUNKS))


# --- D: a BLANK early stop is not mistaken for an empty stream ---------------
async def _cb_blank(delta, full):
    return ""


res_d, m_d = _run(CHUNKS, _cb_blank)
check("blank early stop is honored, not treated as an empty stream", m_d.calls == 1)
check("blank early stop returns the blank text", res_d[0] == "")
check("blank early stop leaves the key un-penalized",
      all(not v.get("keys", {}) or all(k.get("sick_until", 0) == 0 for k in v["keys"].values())
          for v in K._KAME_KEY_HEALTH.values()))


# --- E: streaming with NO response_callback (e.g. a tokens-only utility call).
# A0 must still see tokens_callback set (so it streams) and response_callback
# None (so its early-stop branch is skipped) — KAME's shims must not invent one.
async def _tok(delta, n):
    return None


res_e, m_e = _run(CHUNKS, None, tokens_callback=_tok)
check("streaming without a response_callback consumes the whole stream",
      m_e.pulled == len(CHUNKS))
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
# v1.6.0.1 moved this number, and the reason is worth keeping beside the test.
# The opening rest is now the REFUSAL bench, not the daily hour. Waiting is not
# what repairs a refused pairing, so the two ways of being wrong are not the
# same size: too long costs a healthy key for an hour, too short costs one
# request refused in milliseconds. The hour is not lost — `denied` sits on the
# doubling ladder in `_mark_key_health`, so a permission that really is
# permanent climbs to the daily ceiling by itself, while an API somebody
# switches on comes back in the seconds it actually took.
check("403 PERMISSION_DENIED opens at the refusal bench, not the daily hour",
      _delay_403 == K._KAME_REFUSAL_REST_S)

# ...and the ladder still gets there. Drive the same key through repeated
# denials and confirm the applied rest climbs and saturates at the daily cap.
K._KAME_KEY_HEALTH = {}
_ladder_ident = "gemini:ladder-probe"
K._get_identity_state(_ladder_ident, ["L1", "L2"])
_climb = [K._mark_key_health(_ladder_ident, "L1", False, _delay_403, "denied")
          for _ in range(12)]
check("a repeated denial climbs the ladder",
      _climb[0] == K._KAME_REFUSAL_REST_S and _climb[1] > _climb[0])
check("...and saturates at the daily ceiling, never past it",
      _climb[-1] == K._KAME_DAILY_COOLDOWN_S and max(_climb) <= K._KAME_DAILY_COOLDOWN_S)
check("a denial never retires the key, no matter how many arrive",
      not K._KAME_KEY_HEALTH[_ladder_ident]["keys"]["L1"].get("retired_at"))
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
      "403" in _line and "this model" in _line.lower())
# v1.6.0.1: and it must NOT say the thing that is false. Calling a model-scoped
# refusal an invalid credential is wrong twice over — the key is fine, and it is
# fine on every other model in the account.
check("the denied log line never calls the credential invalid",
      "invalid" not in _line.lower() and "not a valid key" not in _line.lower())
check("the denied log line says the key still works elsewhere",
      "everywhere else" in _line.lower())

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

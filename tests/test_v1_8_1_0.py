"""v1.8.1.0 — the Hermes port's 1.8.x rules, brought across.

The two ports share a version line on purpose: a release on one side raises the
other. Every number below is the one the Hermes port measured on the owner's
own traffic and ships in its 1.8.1.0; this file is the parity gate that says
the Agent Zero engine answers the same shapes with the same rests.

1. **A ceiling on every hold** (Hermes 1.8.0.0). Five real Codex refusals were
   measured holding a key 3h+ on the provider's word alone. No credential sits
   out longer than ``max_hold_seconds`` (default one hour), whatever set it.
2. **A throttle that names no wait rests 30s** (Hermes 1.8.0.2). Retried within
   30s, a refused key answered 0 of 73 times; 30s avoids 388 of 413 avoidable
   refused calls over the whole recorded corpus.
3. **Gemini's bare 429 RESOURCE_EXHAUSTED climbs a short ladder** (Hermes
   1.8.1.0): 1s, 2, 4, 8, 16, 32, 64 and holds at 64 — only for that shape (429,
   status RESOURCE_EXHAUSTED, no retryDelay, no Retry-After, no quotaId), reset
   by an answer, never applied to a number the provider stated.
4. **A timeout rotates without benching** (Hermes 1.8.0.0): the provider went
   quiet, the key is fine.
5. **Out of credit is the account's, not the model's** (Hermes 1.8.0.0): the key
   is benched on every model of that provider it has been seen on.

Run:  python tests/test_v1_8_1_0.py
"""
import asyncio
import os
import sys
import time
import types


def _stub(name):
    m = types.ModuleType(name)
    sys.modules[name] = m
    return m


for _n in ("openai", "langchain_core", "helpers"):
    _stub(_n)
_lit = _stub("litellm")
_lit.suppress_debug_info = False
_lit.acompletion = lambda *a, **k: None
_lc = _stub("langchain_core.messages")


class _Msg:
    def __init__(self, content=""):
        self.content = content


_lc.SystemMessage = _lc.HumanMessage = _Msg
_ps = _stub("helpers.print_style")


class _PrintStyle:
    def __init__(self, *a, **k): pass
    def print(self, *a, **k): pass
    warning = error = success = staticmethod(lambda *a, **k: None)


_ps.PrintStyle = _PrintStyle
_errs = _stub("helpers.errors")
for _e in ("InterventionException", "RepairableException", "HandledException"):
    setattr(_errs, _e, type(_e, (Exception,), {}))

HERE = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, HERE)
import kame_engine as K  # noqa: E402


class RateLimitError(Exception):
    """litellm's class name, which is what A0 actually raises."""

    def __init__(self, msg, status_code=None, headers=None):
        super().__init__(msg)
        self.message = msg
        if status_code is not None:
            self.status_code = status_code
        self.response = types.SimpleNamespace(text=msg, status_code=status_code, headers=headers or {})


def gemini(body_inner: str, status_code=429):
    """The shape litellm hands A0 for a Gemini refusal: the provider's JSON, verbatim, in the message."""
    return RateLimitError(
        "litellm.RateLimitError: litellm.RateLimitError: GeminiException - "
        "{\n  \"error\": {\n    \"code\": %d,\n%s\n  }\n}\n" % (status_code, body_inner),
        status_code=status_code,
    )


BARE = gemini('    "message": "Resource has been exhausted (e.g. check quota).",\n'
              '    "status": "RESOURCE_EXHAUSTED"')
STATED = gemini('    "message": "You exceeded your current quota. Please retry in 42.03309031s.",\n'
                '    "status": "RESOURCE_EXHAUSTED",\n'
                '    "details": [{"@type": "type.googleapis.com/google.rpc.QuotaFailure", "violations": '
                '[{"quotaId": "GenerateContentInputTokensPerModelPerMinute-FreeTier"}]},\n'
                '      {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "42s"}]')
QUOTA_ID_ONLY = gemini('    "message": "Quota exceeded.",\n    "status": "RESOURCE_EXHAUSTED",\n'
                       '    "details": [{"@type": "type.googleapis.com/google.rpc.QuotaFailure", "violations": '
                       '[{"quotaId": "GenerateRequestsPerMinutePerProjectPerModel-FreeTier"}]}]')
PLAIN_429 = RateLimitError("Error code: 429 - {'status': 429, 'title': 'Too Many Requests'}", status_code=429)
ZAI = RateLimitError("Error code: 429 - Usage limit reached for your plan", status_code=429)
OVERLOADED = gemini('    "message": "The model is overloaded.",\n    "status": "UNAVAILABLE"', status_code=503)

_failures = []


def check(name, cond):
    print(("PASS  " if cond else "FAIL  ") + name)
    if not cond:
        _failures.append(name)


def fresh(identity, keys=("A", "B")):
    K._KAME_KEY_HEALTH.pop(identity, None)
    K._KAME_NO_ANSWER_SINCE.pop(identity, None)
    provider = identity.split(":", 1)[0]
    for slot in [s for s in list(K._KAME_ACCOUNT_HOLDS)
                 if s[0] == provider and s[1] in keys]:
        K._KAME_ACCOUNT_HOLDS.pop(slot, None)
    for _k in [k for k in list(K._KAME_STATED_RL) if k == identity or (isinstance(k, tuple) and k[0] == identity)]:
        K._KAME_STATED_RL.pop(_k, None)
    K._get_identity_state(identity, list(keys))


def rests(identity, key, exc, times):
    return [K._kame_rest_for_failure(identity, key, exc)[0] for _ in range(times)]


DEFAULTS = (K._KAME_MAX_HOLD_S, K._KAME_UNSIZED_THROTTLE_REST_S, K._KAME_UNSIZED_BACKOFF,
            K._KAME_UNSIZED_BACKOFF_MAX_S, K._KAME_DAILY_COOLDOWN_S)


def restore():
    (K._KAME_MAX_HOLD_S, K._KAME_UNSIZED_THROTTLE_REST_S, K._KAME_UNSIZED_BACKOFF,
     K._KAME_UNSIZED_BACKOFF_MAX_S, K._KAME_DAILY_COOLDOWN_S) = DEFAULTS


# ==========================================================================
print("\n--- 0. the defaults are the Hermes 1.8.1.0 numbers ---")
check("ceiling defaults to one hour", K._KAME_MAX_HOLD_S == 3600.0)
check("an unsized throttle defaults to 30s", K._KAME_UNSIZED_THROTTLE_REST_S == 30.0)
check("the RESOURCE_EXHAUSTED ladder ships on", K._KAME_UNSIZED_BACKOFF is True)
check("and stops growing at 64s", K._KAME_UNSIZED_BACKOFF_MAX_S == 64.0)

# ==========================================================================
print("\n--- 1. the ladder, on its one shape ---")
check("the bare body is recognised", K._is_bare_resource_exhausted(BARE) is True)
check("a stated retryDelay takes it out of the shape", K._is_bare_resource_exhausted(STATED) is False)
check("a quotaId alone takes it out of the shape", K._is_bare_resource_exhausted(QUOTA_ID_ONLY) is False)
check("a 429 with no RESOURCE_EXHAUSTED is not the shape", K._is_bare_resource_exhausted(PLAIN_429) is False)
check("Z.AI's usage limit is not the shape", K._is_bare_resource_exhausted(ZAI) is False)
check("a 5xx is never the shape", K._is_bare_resource_exhausted(OVERLOADED) is False)
headed = RateLimitError(BARE.message, status_code=429, headers={"retry-after": "7"})
check("a Retry-After header takes it out of the shape", K._is_bare_resource_exhausted(headed) is False)

ID = "gemini:gemini-3.8-flash"
fresh(ID)
got = rests(ID, "A", BARE, 9)
check("1, 2, 4, 8, 16, 32, 64 and then holds at 64: %s" % got,
      got == [1.0, 2.0, 4.0, 8.0, 16.0, 32.0, 64.0, 64.0, 64.0])
applied, kind, sc, label, _sized = K._kame_rest_for_failure(ID, "A", BARE)
check("the rung is named for the log (%s)" % label, label == "backoff.10")
K._mark_key_health(ID, "A", True)
check("an answer resets it to 1s", rests(ID, "A", BARE, 2) == [1.0, 2.0])
check("each key climbs its own ladder", rests(ID, "B", BARE, 1) == [1.0])

fresh(ID)
rests(ID, "A", BARE, 4)
stated = K._kame_rest_for_failure(ID, "A", STATED)
check("a stated number is obeyed, not multiplied (%.1f)" % stated[0], abs(stated[0] - 42.0) < 1.0)
check("...and what it said sizes the next bare refusal on that key (Hermes order)",
      abs(rests(ID, "A", BARE, 1)[0] - 42.0) < 1.0)
K._mark_key_health(ID, "A", True)
check("...until the key answers; then the ladder starts again at 1s", rests(ID, "A", BARE, 1) == [1.0])

fresh(ID)
check("every other throttle keeps the flat 30s: plain 429", rests(ID, "A", PLAIN_429, 3) == [30.0, 30.0, 30.0])
fresh(ID)
check("every other throttle keeps the flat 30s: Z.AI", rests(ID, "A", ZAI, 2) == [30.0, 30.0])
fresh(ID)
over = K._kame_rest_for_failure(ID, "A", OVERLOADED)
check("a 503 stays a 1s server rest (%s)" % (over[:2],), over[0] == 1.0 and over[1] == "server")

K.set_unsized_backoff(False)
fresh(ID)
check("switched off, the bare shape rests the flat 30s", rests(ID, "A", BARE, 3) == [30.0, 30.0, 30.0])
K.set_unsized_backoff(True)
check("switched back on, it starts again from 1s", rests(ID, "A", BARE, 1) == [1.0])

K.set_unsized_backoff_max(3600)
fresh(ID)
long = rests(ID, "A", BARE, 14)
check("uncapped, it keeps doubling until the ceiling: %s" % long[-3:], long[-3:] == [2048.0, 3600.0, 3600.0])
restore()

# ==========================================================================
print("\n--- 2. the unsized throttle ---")
check("a plain 429 is classified at 30s", K._classify_error(PLAIN_429)[:2] == (30.0, "per_minute"))
K.set_unsized_throttle_rest(0)
fresh(ID)
check("the dial can set it to zero (spin)", rests(ID, "A", PLAIN_429, 1) == [0.0])
K.set_unsized_throttle_rest(9999)
check("and it is clamped at 300", K._KAME_UNSIZED_THROTTLE_REST_S == 300.0)
restore()

# ==========================================================================
print("\n--- 3. the ceiling ---")
fresh(ID)
nine_h = RateLimitError("429 rate limited. Please retry in 32400s.", status_code=429)
check("a stated nine hours stops at the one-hour ceiling",
      K._kame_rest_for_failure(ID, "A", nine_h)[0] == 3600.0)
K.set_max_hold(600)
fresh(ID)
check("the ceiling is the owner's number, not a constant",
      K._kame_rest_for_failure(ID, "A", nine_h)[0] == 600.0)
fresh("openai:gpt-6")
paid = K._mark_key_health("openai:gpt-6", "A", False, K._KAME_DAILY_COOLDOWN_S, "insufficient_quota")
check("it bounds the out-of-credit hour too (%s)" % paid, paid == 600.0)
K.set_max_hold(5)
check("clamped to at least 60s", K._KAME_MAX_HOLD_S == 60.0)
K.set_max_hold(10 ** 9)
check("and at most a day", K._KAME_MAX_HOLD_S == 86400.0)
restore()
fresh(ID)
before = time.time()
K._kame_rest_for_failure(ID, "A", nine_h)
until = K._KAME_KEY_HEALTH[ID]["keys"]["A"]["sick_until"]
check("what is stored obeys it, not only what is reported", until <= before + 3600.0 + 1.0)

# ==========================================================================
print("\n--- 4. a timeout rotates without benching ---")
check("an asyncio timeout is a zero-second rest",
      K._classify_error(asyncio.TimeoutError())[:2] == (0, "timeout"))
check("so is a read timeout in the text",
      K._classify_error(RuntimeError("Request timed out."))[:2] == (0, "timeout"))
fresh(ID)
K._kame_rest_for_failure(ID, "A", asyncio.TimeoutError())
check("the key is selectable again at once",
      K._KAME_KEY_HEALTH[ID]["keys"]["A"]["sick_until"] <= time.time())

# ==========================================================================
print("\n--- 4b. a timeout that took no time is not a timeout ---")
# Found by the first REAL session (Agent Zero v2.12 code, real litellm, real
# Gemini keys): an APIConnectionError whose text said "Timeout should be used
# inside a task" failed in half a second, read as a timeout, rested 0s, and the
# pool spun through fourteen keys about twice a second. Only an attempt that
# actually waited earns the zero rest; a fast failure keeps its three seconds.
fast = type("APIConnectionError", (Exception,), {})("litellm.APIConnectionError: Timeout should be used inside a task")
fresh(ID)
fast_rest = K._kame_rest_for_failure(ID, "A", fast, elapsed=0.4)
check("a 'timeout' that failed in 0.4s rests 3s, not 0 (%s)" % (fast_rest[:2],),
      fast_rest[0] == 3.0 and fast_rest[1] == "timeout")
fresh(ID)
slow_rest = K._kame_rest_for_failure(ID, "A", asyncio.TimeoutError(), elapsed=30.0)
check("a timeout that really waited 30s rests 0s", slow_rest[0] == 0.0)
_engine_src = open(os.path.join(HERE, "kame_engine.py"), encoding="utf-8").read()
check("the production loop hands the attempt's duration in",
      "_kame_decide_failure(\n                identity, key, e, elapsed=time.perf_counter() - _attempt_t0" in _engine_src
      and "return _kame_rest_for_failure(identity, key, exc, elapsed=elapsed)" in _engine_src)

print("\n--- 5. out of credit is the account's ---")
for ident in ("openai:gpt-6", "openai:gpt-6-mini", "gemini:gemini-3.8-flash"):
    fresh(ident)
K._mark_key_health("openai:gpt-6", "A", False, K._KAME_DAILY_COOLDOWN_S, "insufficient_quota")
now = time.time()
other_model = K._KAME_KEY_HEALTH["openai:gpt-6-mini"]["keys"]["A"]["sick_until"]
other_key = K._KAME_KEY_HEALTH["openai:gpt-6-mini"]["keys"]["B"]["sick_until"]
other_provider = K._KAME_KEY_HEALTH["gemini:gemini-3.8-flash"]["keys"]["A"]["sick_until"]
check("the same key is benched on the provider's other model", other_model > now + 3000)
check("the other key is untouched", other_key <= now)
check("another provider is untouched", other_provider <= now)

# ==========================================================================
print("\n--- 6. the settings reach the engine and the screen ---")
cfg = open(os.path.join(HERE, "default_config.yaml"), encoding="utf-8").read()
for name in ("max_hold_seconds", "unsized_throttle_rest_seconds", "unsized_throttle_backoff",
             "unsized_backoff_max_seconds"):
    check("default_config.yaml declares %s" % name, name + ":" in cfg)
act = open(os.path.join(HERE, "kame_activation.py"), encoding="utf-8").read()
for setter in ("set_max_hold", "set_unsized_throttle_rest", "set_unsized_backoff", "set_unsized_backoff_max"):
    check("activation calls %s" % setter, setter in act)
ui = open(os.path.join(HERE, "webui", "config.html"), encoding="utf-8").read()
for name in ("max_hold_seconds", "unsized_throttle_backoff", "unsized_backoff_max_seconds",
             "unsized_throttle_rest_seconds"):
    check("the settings page shows %s" % name, name in ui)
for env, attr, raw, want in (("KAME_MAX_HOLD", "_KAME_MAX_HOLD_S", "900", 900.0),
                             ("KAME_UNSIZED_BACKOFF", "_KAME_UNSIZED_BACKOFF", "0", False),
                             ("KAME_UNSIZED_BACKOFF_MAX", "_KAME_UNSIZED_BACKOFF_MAX_S", "16", 16.0),
                             ("KAME_UNSIZED_REST", "_KAME_UNSIZED_THROTTLE_REST_S", "45", 45.0)):
    os.environ[env] = raw
    K._kame_apply_env_overrides()
    check("%s=%s reaches the engine" % (env, raw), getattr(K, attr) == want)
    del os.environ[env]
    restore()

# ==========================================================================
print("\n--- 6b. the /kame doctor table says what the classifier does ---")
import importlib.util as _ilu  # noqa: E402
_spec = _ilu.spec_from_file_location("kame_cmd_under_test", os.path.join(HERE, "commands", "kame_command.py"))
_cmd = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(_cmd)
_table = {k: r for k, r, *_ in _cmd.EXPECTED_RESTS}
check("the table's timeout is the classifier's (%s)" % _table["timeout"],
      _table["timeout"] == K._classify_error(asyncio.TimeoutError())[0])
check("the table's per_minute is the classifier's (%s)" % _table["per_minute"],
      _table["per_minute"] == K._classify_error(PLAIN_429)[0])
check("the table's server is the classifier's", _table["server"] == K._classify_error(OVERLOADED)[0])

print("\n--- 8. what the adversarial review broke ---")
# 1. A number the provider stated earlier, for THIS key on this model, beats the
#    ladder - the ladder only fills the gap when nothing was ever stated. Per
#    key, cleared by an answer, exactly as Hermes 1.8.1.0 keeps it.
fresh(ID)
told = RateLimitError("429 Too Many Requests. Please retry in 42.3s.", status_code=429)
K._kame_rest_for_failure(ID, "A", told)
learned_a = K._kame_rest_for_failure(ID, "A", BARE)
check("a bare refusal on a key that was told 42.3s rests 42.3 (%s)" % (learned_a[:1],),
      abs(learned_a[0] - 42.3) < 0.01 and learned_a[3] == "")
check("...while a key that was never told climbs its own ladder",
      K._kame_rest_for_failure(ID, "B", BARE)[0] == 1.0)
K._mark_key_health(ID, "A", True)
check("an answer forgets what that key was told", K._kame_rest_for_failure(ID, "A", BARE)[0] == 1.0)

# 2. A 503 between two bare refusals does not reset the ladder. A busy Gemini
#    model mixes the two; resetting on every 503 kept the pool at 1s forever.
fresh(ID)
mixed = []
for _ in range(4):
    mixed.append(K._kame_rest_for_failure(ID, "A", BARE)[0])
    K._kame_rest_for_failure(ID, "A", OVERLOADED)
check("bare, 503, bare, 503 ... still climbs: %s" % mixed, mixed == [1.0, 2.0, 4.0, 8.0])

# 3. Thawing an outage never cuts a quota's hold.
IDT = "openai:gpt-6-thaw"
fresh(IDT, keys=("A", "B"))
K._mark_key_health(IDT, "B", False, 1.0, "server")
K._mark_key_health(IDT, "B", False, K._KAME_DAILY_COOLDOWN_S, "insufficient_quota")
K._thaw_server_cooled_keys(IDT, "A")
check("an out-of-credit hold survives an outage thaw",
      K._KAME_KEY_HEALTH[IDT]["keys"]["B"]["sick_until"] > time.time() + 3000)
fresh(IDT, keys=("A", "B"))
K._mark_key_health(IDT, "B", False, 90.0, "server")
check("...and a real outage hold is still thawed", K._thaw_server_cooled_keys(IDT, "A") == 1)

# 4. A sub-second stated number is a spin, not a cooldown: floored at 1s, and
#    it cannot teach a sub-second rest to the next refusal either.
fresh(ID)
tiny = RateLimitError("429 rate limited. Please try again in 140ms.", status_code=429)
check("a stated 140ms rests 1s", K._kame_rest_for_failure(ID, "A", tiny)[0] == 1.0)
check("and what it taught is floored too", K._kame_rest_for_failure(ID, "A", PLAIN_429)[0] >= 1.0)

# 6a. RESOURCE_EXHAUSTED in relayed prose is not the structured status.
relay = RateLimitError("429 Too Many Requests - upstream said resource_exhausted, slow down", status_code=429)
check("prose that mentions resource_exhausted is not the ladder's shape",
      K._is_bare_resource_exhausted(relay) is False)
check("the host's own rendering still is",
      K._is_bare_resource_exhausted(RateLimitError("Gemini HTTP 429 (RESOURCE_EXHAUSTED): Resource has been exhausted", status_code=429)) is True)

# 6b. Retry-After on the real response object (httpx.Headers, not a dict) and
#     on litellm's own attribute is a stated number.
try:
    import httpx
    hx = RateLimitError(BARE.message, status_code=429)
    hx.response = httpx.Response(429, headers={"Retry-After": "42"}, text=BARE.message)
    check("Retry-After on httpx headers takes it out of the shape", K._is_bare_resource_exhausted(hx) is False)
    fresh(ID)
    check("...and is obeyed as stated", K._kame_rest_for_failure(ID, "A", hx)[0] == 42.0)
except ImportError:
    print("SKIP  httpx not installed")
lit = RateLimitError(BARE.message, status_code=429)
lit.litellm_response_headers = {"retry-after": "17"}
check("Retry-After on litellm_response_headers is read", K._is_bare_resource_exhausted(lit) is False)

# 7. Setters never raise and never read a bool as a number.
K.set_max_hold(10 ** 400)
check("an absurd number does not raise", K._KAME_MAX_HOLD_S in (3600.0, 86400.0))
restore()
K.set_max_hold(True)
check("a bool is not a ceiling", K._KAME_MAX_HOLD_S == 3600.0)
restore()

# 8. The retry hint stops at its own sentence.
glued = RateLimitError("Quota exceeded; try again in 5.2s.", status_code=429)
glued.code = 429
check("'try again in 5.2s.' next to a 429 code is 5.2s, not 434.2 (%s)" % (K._extract_retry_delay(glued),),
      abs(K._extract_retry_delay(glued) - 5.2) < 0.01)

print("\n--- 7. one version everywhere ---")
manifest = open(os.path.join(HERE, "plugin.yaml"), encoding="utf-8").read()
check("engine says 1.8.1.2", K.KAME_VERSION == "1.8.1.2")
check("manifest says 1.8.1.2", 'version: 1.8.1.2' in manifest or 'version: "1.8.1.2"' in manifest)

print()
if _failures:
    print("%d FAILED:" % len(_failures))
    for f in _failures:
        print("  -", f)
    sys.exit(1)
print("ALL v1.8.1.0 TESTS PASSED")

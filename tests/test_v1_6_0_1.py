"""v1.6.0.1 — a refusal is not a clock, and a refused model is not a refused key.

Every group here defends a decision that costs a real credential when it is got
wrong, so each one says what it is defending against rather than only what it
checks.

The two tests worth knowing about before reading the rest:

* **Group E** fails if the demotion in `_get_best_key` is removed. Shortening the
  refusal bench from an hour to twenty seconds is *worse* than the hour without
  it: a key that answered 401 comes back with an empty request window and the
  oldest `last_used` in the pool, which is exactly the profile the least-loaded /
  least-recently-used rule reaches for.
* **Group D** fails if retirement stops outranking readiness. A retired key whose
  own rest has lapsed, next to a healthy key that is resting twenty seconds off a
  throttle, is the only thing "ready" — and the call goes to the credential we
  have already been told is dead.
"""
import sys, types, os, time


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


class _Err(Exception):
    def __init__(self, msg, status_code=None):
        super().__init__(msg)
        if status_code is not None:
            self.status_code = status_code


def _fresh(identity, keys):
    """A clean pool for one identity, with nothing remembered."""
    K._KAME_KEY_HEALTH = {}
    K._get_identity_state(identity, keys)
    return K._KAME_KEY_HEALTH[identity]["keys"]


# =============================================================================
# A. THE DICTIONARY
# =============================================================================
print("\n--- A: the dictionary ---")

# A1. `unauthorized` is gone, and gone for the right reason.
#
# It is the HTTP reason phrase for 401, so it arrives on EVERY bare 401 — a
# proxy, a gateway, an OAuth token one second from refreshing. Reading it as
# "this key is not a key" is what cost the Hermes port twenty-one healthy keys,
# quarantined an hour each, every one of them working on the next call.
check("`unauthorized` is no longer an invalid-key marker",
      "unauthorized" not in K._INVALID_KEY_INDICATORS)

_bare_401_text = _Err("Error code: 401 - Unauthorized")
check("a bare 'Unauthorized' with no status code is NOT read as a dead key",
      K._is_revoked_key(_bare_401_text) is False)

# ...but a real 401 is still an auth failure. Removing the word must not make a
# 401 invisible; it only changes what a BARE one costs.
check("a real 401 is still an auth failure",
      K._is_auth_error(_Err("nope", status_code=401)) is True)
check("...and a bare 401 is `auth`, never `revoked`",
      K._is_revoked_key(_Err("nope", status_code=401)) is False)

# A2. The provider using the words IS enough.
for _phrase in (
    "API key not valid. Please pass a valid API key.",
    "Incorrect API key provided: sk-abc",
    "API key expired. Please renew the API key.",
    "Invalid Authentication",
):
    check(f"revoked: {_phrase[:38]!r}", K._is_revoked_key(_Err(_phrase)) is True)

# A3. Two providers say it with the words the other way round, and no substring
# in the tuple can reach them. Anthropic: "API key is invalid." DeepSeek:
# "Your api key: ****0000 is invalid".
check("revoked: Anthropic's 'API key is invalid.'",
      K._is_revoked_key(_Err("Your API key is invalid.")) is True)
check("revoked: DeepSeek's redacted 'api key: ****0000 is invalid'",
      K._is_revoked_key(_Err("Authentication Fails, Your api key: ****0000 is invalid")) is True)

# ...and the bounded clause must not reach across a sentence boundary. A message
# that merely mentions a key somewhere and the word invalid somewhere else is
# not evidence about the key.
check("the bounded clause does not span two sentences",
      K._is_revoked_key(_Err(
          "Your api key was accepted. The request body is invalid.")) is False)

# A4. The quota families whose 429 says none of the old words. Each of these
# used to fall into the generic `other` bucket at a flat 20s with no escalation
# and no daily/per-minute distinction — a whole provider's throttling read as an
# unrecognised error.
for _text, _why in (
    ("Throttling.RateQuota: request was denied", "Alibaba's whole 429 family"),
    ("Usage limit reached for your plan", "Z.AI 1308"),
    ("Weekly Limit Exhausted", "Z.AI 1310"),
    ("concurrent limit reached, please retry", "Kimi / MiniMax concurrency"),
    ("You have exceeded your limit of 200000 tokens per day", "counter named by unit + window"),
):
    _k = K._classify_error(_Err(_text))[1]
    check(f"quota: {_why}", _k in ("per_minute", "daily", "insufficient_quota"))

# ...and the one above that names a DAY must land on the daily branch, not the
# per-minute one. The window markers already read it; nothing upstream had ever
# called it a quota failure, so they were never asked.
check("'tokens per day' reaches the daily branch, not per-minute",
      K._classify_error(_Err("exceeded your limit of 200000 tokens per day"))[1] == "daily")

# A5. The denial family: a model outside the tier the key pays for.
for _text, _why in (
    ("Your current subscription plan does not yet include access to glm-4.6", "Z.AI 1311"),
    ("model not authorized for this API key", "tier refusal"),
    ("model not available on your plan", "tier refusal, other wording"),
):
    check(f"denied: {_why}", K._classify_error(_Err(_text))[1] == "denied")

# A6. The exclusions, each with the reason it is excluded, because an omission
# nobody wrote down gets "fixed" by the next reader.
#
# "model not found" is about the MODEL NAME, not the credential. The Hermes port
# had it in the denial family and its own corpus caught the cost: with several
# keys, that answer walks the whole pool over a misspelt model name and benches
# every one of them.
_404 = _Err("NotFoundError: model `gemini-99-ultra` not found", status_code=404)
check("'model not found' is NOT a denial",
      K._classify_error(_404)[1] != "denied")
check("'model not found' is NOT read as a credential problem",
      K._is_auth_error(_404) is False)
check("'model not found' stays terminal — try another model, not another key",
      K._is_terminal_error(_404) is True)
check("the exclusions are written down, not merely absent",
      "model not found" in K._DENIAL_EXCLUDED_ON_PURPOSE
      and "AuthenticationError" in K._DENIAL_EXCLUDED_ON_PURPOSE)

# A7. A class name may say the connection did not happen. It may NOT say what
# the provider decided.
_conn = type("APIConnectionError", (Exception,), {})("connection error")
_delay, _kind, _ = K._classify_error(_conn)
check("a transport failure rests 3s, not the 20s of the unrecognised",
      _delay == 3 and _kind == "timeout")
check("class names that state a provider verdict are NOT mapped",
      "RateLimitError" not in K._TRANSPORT_EXCEPTION_CLASSES
      and "AuthenticationError" not in K._TRANSPORT_EXCEPTION_CLASSES)

# A8. The order that keeps a real quota out of the refusal families.
check("a 429 is still a quota, never a refusal",
      K._classify_error(_Err("429 RESOURCE_EXHAUSTED quota exceeded", status_code=429))[1]
      in ("per_minute", "daily", "insufficient_quota"))
check("a 5xx is still `server`, even when its body mentions quota",
      K._classify_error(_Err("503 UNAVAILABLE resource exhausted", status_code=503))[1] == "server")


# =============================================================================
# B. THE THREE REFUSALS, AND WHAT EACH COSTS
# =============================================================================
print("\n--- B: three refusals, three prices ---")

check("a refusal opens at twenty seconds, not an hour",
      K._KAME_REFUSAL_REST_S == 20.0)
check("`denied` opens at the refusal bench too",
      K._classify_error(_Err("PERMISSION_DENIED", status_code=403))[0] == K._KAME_REFUSAL_REST_S)

# The kind-to-rest table, stated here as a second time rather than derived from
# the code — derived it would agree by construction and prove nothing.
_TABLE = (
    ("timeout", 3),
    ("server", 5),
    ("denied", K._KAME_REFUSAL_REST_S),
    ("auth", K._KAME_REFUSAL_REST_S),
    ("revoked", K._KAME_REFUSAL_REST_S),
    ("daily", K._KAME_DAILY_COOLDOWN_S),
    ("insufficient_quota", K._KAME_DAILY_COOLDOWN_S),
)
_pool = _fresh("t:table", ["A", "B"])
for _kind, _expected in _TABLE:
    _applied = K._mark_key_health("t:table", "A", False, _expected, _kind)
    check(f"first {_kind} rests {_expected}s", _applied == _expected)
    _pool["A"]["sick_until"] = 0
    _pool["A"]["consecutive_rl"] = 0
    _pool["A"]["consecutive_server"] = 0
    _pool["A"]["consecutive_refusals"] = 0
    _pool["A"]["consecutive_denials"] = 0
    _pool["A"]["retired_at"] = 0

check("nothing rests an hour on a FIRST refusal except a spent quota",
      all(v == K._KAME_REFUSAL_REST_S
          for k, v in _TABLE if k in ("auth", "revoked", "denied")))


# =============================================================================
# C. RETIREMENT
# =============================================================================
print("\n--- C: leaving rotation ---")

# C1. A bare 401 does not retire on the first one, or the second.
_pool = _fresh("t:retire", ["K1", "K2", "K3"])
for _i in range(1, K._KAME_REFUSALS_BEFORE_RETIRING):
    K._mark_key_health("t:retire", "K1", False, K._KAME_REFUSAL_REST_S, "auth")
    check(f"after {_i} bare 401(s), the key is still in rotation",
          not _pool["K1"]["retired_at"])
K._mark_key_health("t:retire", "K1", False, K._KAME_REFUSAL_REST_S, "auth")
check(f"after {K._KAME_REFUSALS_BEFORE_RETIRING} in a row, it leaves rotation",
      bool(_pool["K1"]["retired_at"]))

# C2. "In a row" has to mean in a row. A 429 between two 401s is evidence that
# the credential reached the provider and was metered, which is exactly what a
# dead key cannot do.
_pool = _fresh("t:streak", ["K1", "K2"])
K._mark_key_health("t:streak", "K1", False, 20, "auth")
K._mark_key_health("t:streak", "K1", False, 20, "auth")
K._mark_key_health("t:streak", "K1", False, 20, "per_minute")   # breaks the streak
K._mark_key_health("t:streak", "K1", False, 20, "auth")
check("a 429 between two 401s resets the streak",
      not _pool["K1"]["retired_at"] and _pool["K1"]["consecutive_refusals"] == 1)

# C3. The provider using the words does not wait for three.
_pool = _fresh("t:revoked", ["K1", "K2"])
K._mark_key_health("t:revoked", "K1", False, K._KAME_REFUSAL_REST_S, "revoked")
check("`revoked` leaves rotation on the FIRST one",
      bool(_pool["K1"]["retired_at"]))

# C4. Retiring is not deleting. The row stays, and one answer brings it back.
check("a retired key keeps its row", "K1" in _pool)
K._mark_key_health("t:revoked", "K1", True)
check("one successful call brings a retired key straight back",
      not _pool["K1"]["retired_at"] and _pool["K1"]["consecutive_refusals"] == 0)

# C5. A MODEL refusal never costs the key. This is the expensive one to get
# wrong: a key refused for one model may be the healthiest credential in the
# account on every other, and `denied` is scoped per provider:model anyway.
_pool = _fresh("t:denied", ["K1", "K2"])
for _ in range(K._KAME_REFUSALS_BEFORE_RETIRING * 3):
    K._mark_key_health("t:denied", "K1", False, K._KAME_REFUSAL_REST_S, "denied")
check("a model refusal never retires the key, however many arrive",
      not _pool["K1"]["retired_at"])
check("...and it never feeds the retirement counter",
      _pool["K1"]["consecutive_refusals"] == 0)
check("`denied` is deliberately absent from the retiring kinds",
      "denied" not in K._KAME_RETIRING_KINDS)
check("...while `auth` and `revoked` are both in it",
      K._KAME_RETIRING_KINDS == frozenset({"auth", "revoked"}))

# C6. THE ESCAPE HATCH. Retiring can never take a pool to zero: with every key
# refused, every key is offered again and the provider's own error comes back,
# exactly as it would with no plugin installed.
_pool = _fresh("t:hatch", ["K1", "K2"])
for _k in ("K1", "K2"):
    K._mark_key_health("t:hatch", _k, False, K._KAME_REFUSAL_REST_S, "revoked")
    _pool[_k]["sick_until"] = 0          # rests lapsed
check("both keys are retired", all(_pool[k]["retired_at"] for k in ("K1", "K2")))
_picked, _status = K._get_best_key("t:hatch", ["K1", "K2"])
check("with every key retired, one is still offered",
      _picked in ("K1", "K2") and _status == "SUCCESS")


# =============================================================================
# D. RETIREMENT OUTRANKS BEING READY  (fails if the rule is removed)
# =============================================================================
print("\n--- D: retirement outranks readiness ---")

# The case that actually happens: the working key is resting twenty seconds off
# a throttle, the retired key's own rest has lapsed, so the retired key is the
# only thing "ready". Without this rule the call goes to the credential the
# provider already called dead — spending a request to be told again what we
# know, where waiting twenty seconds would have produced an answer.
_pool = _fresh("t:outrank", ["DEAD", "GOOD"])
K._mark_key_health("t:outrank", "DEAD", False, K._KAME_REFUSAL_REST_S, "revoked")
_pool["DEAD"]["sick_until"] = 0                       # its rest has lapsed
_pool["GOOD"]["sick_until"] = time.time() + 20        # healthy, but resting
_picked, _status = K._get_best_key("t:outrank", ["DEAD", "GOOD"])
check("a retired key is skipped even when it is the only one ready",
      _picked == "GOOD")
check("...and the caller is told the pool is exhausted, not handed the dead key",
      _status == "EXHAUSTED_RETRY")


# =============================================================================
# E. THE DEMOTION  (fails if the sort term is removed)
# =============================================================================
print("\n--- E: a refused key is offered last ---")

# Both keys ready. The refused one has an EMPTY request window and the OLDEST
# `last_used` — precisely the profile the least-loaded / least-recently-used
# rule reaches for. Without the demotion term, the one key known not to work is
# the first one tried, every twenty seconds.
_now = time.time()
_pool = _fresh("t:demote", ["REFUSED", "BUSY"])
_pool["REFUSED"]["request_log"] = []
_pool["REFUSED"]["last_used"] = _now - 600            # oldest
_pool["REFUSED"]["consecutive_refusals"] = 1          # the last thing it did was refuse
_pool["BUSY"]["request_log"] = [_now - 5, _now - 3]   # busier
_pool["BUSY"]["last_used"] = _now - 1                 # most recent
_picked, _ = K._get_best_key("t:demote", ["REFUSED", "BUSY"])
check("a just-refused key is offered LAST, even though it looks least loaded",
      _picked == "BUSY")

# ...but it is demoted, never removed. A pool of nothing but refused keys still
# works — that is the difference between a demotion and a ban.
_pool = _fresh("t:demote2", ["R1", "R2"])
for _k in ("R1", "R2"):
    _pool[_k]["consecutive_refusals"] = 1
_picked, _status = K._get_best_key("t:demote2", ["R1", "R2"])
check("a pool of nothing but refused keys still hands one out",
      _picked in ("R1", "R2") and _status == "SUCCESS")

# ...and the demotion lapses. Both counters reset on any success and on any
# failure of another kind, so "was refused" means "the last thing it did was
# refuse", not "was refused once, an hour ago".
_pool = _fresh("t:demote3", ["OLD", "BUSY"])
_pool["OLD"]["last_used"] = _now - 600
_pool["OLD"]["consecutive_refusals"] = 1
K._mark_key_health("t:demote3", "OLD", True)          # it answered
_pool["BUSY"]["request_log"] = [_now - 5, _now - 3]
_picked, _ = K._get_best_key("t:demote3", ["OLD", "BUSY"])
check("a key that answered is not demoted any more", _picked == "OLD")


# =============================================================================
# F. THE LADDER
# =============================================================================
print("\n--- F: the ladder, and why twenty is not a guess ---")

# Twenty is the base the ladder already applies to this kind. Measured against
# the five minutes tried first, the invented number did no work: both reach the
# hourly re-probe at the same point, and all the larger base bought was
# flattening the first four strikes at five minutes — precisely the window in
# which a re-check is most likely to find a transient refusal already cleared.
_pool = _fresh("t:ladder", ["K1", "K2"])
_climb = [K._mark_key_health("t:ladder", "K1", False, K._KAME_REFUSAL_REST_S, "auth")
          for _ in range(12)]
check("the first refusal is the bench itself", _climb[0] == K._KAME_REFUSAL_REST_S)
check("each repeat doubles", _climb[1] == _climb[0] * 2 and _climb[2] == _climb[1] * 2)
check("and it saturates at the daily ceiling, never past it",
      _climb[-1] == K._KAME_DAILY_COOLDOWN_S
      and max(_climb) <= K._KAME_DAILY_COOLDOWN_S)

# The two ladders are kept apart so a model-scoped 403 can never feed retirement.
_pool = _fresh("t:ladders", ["K1"])
K._mark_key_health("t:ladders", "K1", False, K._KAME_REFUSAL_REST_S, "denied")
K._mark_key_health("t:ladders", "K1", False, K._KAME_REFUSAL_REST_S, "denied")
check("denials climb their own counter",
      _pool["K1"]["consecutive_denials"] == 2 and _pool["K1"]["consecutive_refusals"] == 0)

# A cooldown is never SHORTENED. A 20s refusal must not wipe an hour-long daily
# protection already on the same key.
_pool = _fresh("t:noshorten", ["K1"])
K._mark_key_health("t:noshorten", "K1", False, K._KAME_DAILY_COOLDOWN_S, "daily")
_long = _pool["K1"]["sick_until"]
K._mark_key_health("t:noshorten", "K1", False, K._KAME_REFUSAL_REST_S, "auth")
check("a short refusal never shortens a long daily bench",
      _pool["K1"]["sick_until"] >= _long)


# =============================================================================
# G. THE POOL TELLS THE TRUTH ABOUT WHAT IT HOLDS
# =============================================================================
print("\n--- G: a key the config no longer declares ---")

# Until v1.6.0.1 nothing anywhere removed from the health pool: a key edited out
# of the .env kept its bench, kept its ladder, and went on being counted in
# every "N of M keys resting" the user was shown.
_pool = _fresh("t:prune", ["K1", "K2"])
K._mark_key_health("t:prune", "K2", False, K._KAME_DAILY_COOLDOWN_S, "daily")
_pool["K2"]["last_offered"] = time.time() - (K._KAME_POOL_GRACE_S + 10)
K._get_identity_state("t:prune", ["K1"])
check("a key nothing has offered past the grace is dropped",
      "K2" not in K._KAME_KEY_HEALTH["t:prune"]["keys"])
check("...and the surviving key is untouched",
      "K1" in K._KAME_KEY_HEALTH["t:prune"]["keys"])

# The grace is load-bearing: two callers can hold different lists for one
# identity, and dropping on first absence erases a cooldown the other earned.
_pool = _fresh("t:grace", ["K1", "K2"])
K._get_identity_state("t:grace", ["K1"])          # a caller with a shorter list
check("a key absent for less than the grace is KEPT",
      "K2" in K._KAME_KEY_HEALTH["t:grace"]["keys"])

# An empty candidate list mirrors NOTHING. A loader that failed once is not
# evidence that every key was deleted.
_pool = _fresh("t:empty", ["K1", "K2"])
for _k in ("K1", "K2"):
    _pool[_k]["last_offered"] = time.time() - (K._KAME_POOL_GRACE_S + 10)
K._get_identity_state("t:empty", [])
check("an empty key list never empties the pool",
      set(K._KAME_KEY_HEALTH["t:empty"]["keys"]) == {"K1", "K2"})


# =============================================================================
# H. WHAT THE USER IS TOLD
# =============================================================================
print("\n--- H: three refusals, three sentences ---")

_revoked_line = K._friendly_error_msg("revoked", 20, 401, _Err("API key not valid"))
_auth_line = K._friendly_error_msg("auth", 20, 401, _Err("Unauthorized"))
_denied_line = K._friendly_error_msg("denied", 20, 403, _Err("PERMISSION_DENIED"))

check("the three refusals do not share a sentence",
      len({_revoked_line, _auth_line, _denied_line}) == 3)
check("`revoked` says the key left rotation and that nothing was deleted",
      "out of rotation" in _revoked_line and "nothing was deleted" in _revoked_line.lower())
check("`auth` says it is still being tried, not that it is dead",
      "no explanation" in _auth_line and "invalid" not in _auth_line.lower())
check("`denied` names the model and clears the key everywhere else",
      "this model" in _denied_line.lower() and "everywhere else" in _denied_line.lower())

# The suffix asks the reader for opposite things depending on the state, so the
# two sentences must not be interchangeable.
_pool = _fresh("t:suffix", ["K1", "K2"])
K._mark_key_health("t:suffix", "K1", False, 20, "auth")
_pending = K._retirement_suffix("t:suffix", "K1")
check("a key still being tried says one refusal is not proof",
      "not proof" in _pending)
K._mark_key_health("t:suffix", "K1", False, 20, "revoked")
_gone = K._retirement_suffix("t:suffix", "K1")
check("a retired key says what to do about it",
      "left rotation" in _gone and "paste a replacement" in _gone)
check("the two sentences are not the same sentence", _pending != _gone)

# And when the escape hatch is open, saying "left rotation" would be a lie.
_pool = _fresh("t:hatchsay", ["K1", "K2"])
for _k in ("K1", "K2"):
    K._mark_key_health("t:hatchsay", _k, False, 20, "revoked")
check("with every key refused, the line says they are all being offered again",
      "offered again" in K._retirement_suffix("t:hatchsay", "K1"))

# No line, on any path, may carry the key itself.
_SECRET = "AIzaSyTOTALLY-SECRET-KEY-VALUE-0123456789"
_pool = _fresh("t:leak", [_SECRET, "OTHER"])
K._mark_key_health("t:leak", _SECRET, False, 20, "revoked")
_lines = [
    K._retirement_suffix("t:leak", _SECRET),
    K._friendly_error_msg("revoked", 20, 401, _Err("API key not valid")),
    K._session_summary_line(),
    K._key_display_auth(_SECRET),
]
check("nothing v1.6.0.1 prints contains the key",
      all(_SECRET not in (line or "") for line in _lines))


# =============================================================================
# I. THE SESSION LINE COUNTS THE THREE APART
# =============================================================================
print("\n--- I: counted apart ---")

check("the session counters name all three refusals",
      all(k in K._KAME_STATS for k in ("auth", "revoked", "denied", "retired")))
_summary = K._session_summary_line()
check("the summary distinguishes a 401 from a revoked key from a model refusal",
      "401" in _summary and "revoked" in _summary and "model" in _summary)


# =============================================================================
# J. THE ROTATION ON SCREEN
# =============================================================================
print("\n--- J: pool_report, the payload behind the chip ---")

_pool = _fresh("gemini:flash", ["A", "B", "C"])
K._mark_key_health("gemini:flash", "B", False, 30, "per_minute")   # resting
K._mark_key_health("gemini:flash", "C", False, 20, "revoked")      # retired
_rep = K.pool_report()

check("the report names the running version", _rep["version"] == K.KAME_VERSION)
check("it reports one pool", len(_rep["pools"]) == 1)
_p = _rep["pools"][0]
check("...with the counts a reader needs",
      _p["total"] == 3 and _p["ready"] == 1 and _p["resting"] == 1 and _p["retired"] == 1)
check("...and the totals agree with the pool",
      _rep["totals"]["keys"] == 3 and _rep["totals"]["retired"] == 1)
check("a resting key reports when it comes back", _p["eta"] is not None and _p["eta"] > 0)
check("every key row carries a state",
      {r["state"] for r in _p["keys"]} == {"ready", "resting", "retired"})

# A pool with nothing resting reports NO eta, which is not the same as an eta of
# zero — a panel that renders "back in 0s" for a healthy pool is lying.
_pool = _fresh("gemini:calm", ["A"])
check("a healthy pool reports no eta, not a zero",
      K.pool_report()["pools"][0]["eta"] is None)

# THE GUARANTEE. Nothing in this payload is a key, on any path, under any
# setting — the report calls `_key_short_id` directly rather than the
# configurable `_key_display`, because a rendering switch meant for a
# developer's console must not be able to put a secret on a web page.
import json as _json  # noqa: E402
_SECRET2 = "sk-proj-REALLY-SECRET-abcdefghijklmnopqrstuvwxyz0123456789"
_pool = _fresh("openai:gpt", [_SECRET2, "OTHER"])
K._mark_key_health("openai:gpt", _SECRET2, False, 20, "revoked")
for _style in ("fingerprint", "prefix8", "full"):
    K.set_key_log_style(_style)
    _blob = _json.dumps(K.pool_report())
    check(f"pool_report leaks nothing with key_log_style={_style!r}",
          _SECRET2 not in _blob and _SECRET2[:12] not in _blob)
K.set_key_log_style("fingerprint")

# Layer 3 — KAME bound nothing and Agent Zero is running natively — is a safe
# end state and the single most useful thing to see when somebody reports "the
# plugin does nothing". It is reported, not hidden behind a healthy-looking zero.
check("the report says whether KAME is actually attached",
      "active" in _rep and "layer" in _rep)
check("...and layer 3 reads as not attached",
      K.pool_report()["active"] is False or K._KAME_LAYER in (1, 2))


# =============================================================================
# K. THE KEY IS NEVER WRITTEN WHOLE
# =============================================================================
print("\n--- K: `full` is gone ---")

_SECRET3 = "AIzaSyANOTHER-WHOLE-SECRET-KEY-0123456789abcd"
K.set_key_log_style("full")
check("a config that still says `full` does not crash",
      K._KAME_KEY_LOG_STYLE in ("fingerprint", "prefix8"))
check("...it is read as prefix8, which answers the question `full` was used for",
      K._KAME_KEY_LOG_STYLE == "prefix8")
check("no log rendering returns the whole key",
      _SECRET3 not in K._key_display(_SECRET3)
      and _SECRET3 not in K._key_display_auth(_SECRET3))
K.set_key_log_style("fingerprint")
check("the default still leaks nothing at all",
      K._key_display(_SECRET3) == K._key_short_id(_SECRET3))
# The invalid-key line is the one place a partial reveal is the point: you
# cannot look up a hash in a provider console.
_auth_render = K._key_display_auth(_SECRET3)
check("a refused key is still recognisable in a provider console",
      _SECRET3[:10] in _auth_render and _SECRET3[-4:] in _auth_render)
check("...but never whole", _SECRET3 not in _auth_render)


# =============================================================================
# L. THE FILES THE SCREEN IS MADE OF
# =============================================================================
print("\n--- L: the v2.11 plugin slots are actually wired ---")

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _read(*parts):
    path = os.path.join(_ROOT, *parts)
    if not os.path.isfile(path):
        return None
    return open(path, encoding="utf-8").read()


_store = _read("webui", "kame-rotation-store.js")
_chip = _read("extensions", "webui", "model-context-strip-end", "kame-rotation.html")
_snap = _read("extensions", "webui", "apply_snapshot_before", "refresh-kame-rotation.js")
_api = _read("api", "kame_status.py")
_banner = _read("extensions", "python", "banners", "_40_kame_retired_keys.py")

for _name, _src in (("the store", _store), ("the chip", _chip),
                    ("the snapshot hook", _snap), ("the API handler", _api),
                    ("the banner", _banner)):
    check(f"{_name} exists", _src is not None)

# The route and the import path have to agree with the plugin's manifest name,
# and the manifest name is the one thing in this plugin that must never change —
# Agent Zero keys plugin settings off it, so renaming it silently orphans every
# existing install's configuration.
_ROUTE = "/plugins/api_rotation_by_kame/kame_status"
check("the store calls the route this plugin's name produces",
      _store is not None and _ROUTE in _store)
check("the chip imports the store from the plugin's own webui path",
      _chip is not None
      and "/plugins/api_rotation_by_kame/webui/kame-rotation-store.js" in _chip)
check("the snapshot hook imports the same store",
      _snap is not None
      and "/plugins/api_rotation_by_kame/webui/kame-rotation-store.js" in _snap)
_yaml = _read("plugin.yaml")
check("...and the manifest still declares that name",
      _yaml is not None and "name: api_rotation_by_kame" in _yaml)

# The live surfaces must not be able to hammer the backend. Snapshots arrive
# many times a second while a response streams, and the pool does not change per
# token.
check("the snapshot hook rate-limits itself",
      _snap is not None and "MIN_INTERVAL_MS" in _snap)

# The banner is for the one fact that outlives a chat. A banner that appears for
# ordinary throttling is one people learn to dismiss without reading.
check("the banner fires only for retired credentials",
      _banner is not None and "retired <= 0" in _banner)
check("...and says that nothing was deleted",
      _banner is not None and "Nothing was deleted" in _banner)


# =============================================================================
# M. THE DOCTOR'S TABLE AGREES WITH THE CODE
# =============================================================================
print("\n--- M: /kame doctor ---")

import importlib.util  # noqa: E402

_cmd_path = os.path.join(_ROOT, "commands", "kame_command.py")
_cmd = None
if os.path.isfile(_cmd_path):
    _spec = importlib.util.spec_from_file_location("kame_command", _cmd_path)
    _cmd = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_cmd)

check("the /kame command module loads", _cmd is not None)
check("the command manifest exists",
      os.path.isfile(os.path.join(_ROOT, "commands", "kame.command.yaml")))

# THE POINT OF THIS GROUP. The doctor's rest table is written by hand, because
# derived from the code it would agree with the code by construction and prove
# nothing. Written down it is a second statement of intent — and this is what
# holds the two together, so a constant changed without meaning to is a failing
# test rather than a surprise in production.
if _cmd is not None:
    for _kind, _rest, _meaning, _helps in _cmd.EXPECTED_RESTS:
        _pool = _fresh(f"t:doc:{_kind}", ["K1", "K2"])
        _applied = K._mark_key_health(f"t:doc:{_kind}", "K1", False, _rest, _kind)
        check(f"doctor table: a first `{_kind}` really rests {_rest:g}s",
              _applied == _rest)
        check(f"doctor table: `{_kind}` says what it means", bool(_meaning))

    _table_kinds = {k for k, *_ in _cmd.EXPECTED_RESTS}
    check("the doctor names every kind the engine counts",
          {"auth", "revoked", "denied", "daily", "insufficient_quota",
           "per_minute", "server", "timeout", "other"} <= _table_kinds)

    # The report has to render from a COLD process too — the state nothing else
    # exercises, because every test sets something up before it looks.
    K._KAME_KEY_HEALTH = {}
    _cold = _cmd._render(K.pool_report(), doctor=True)
    check("the doctor renders with no pool at all", isinstance(_cold, str) and len(_cold) > 200)
    check("...and says an empty list is expected, not a fault",
          "learns one on the first model call" in _cold)
    check("...and names the build, not only the version",
          "build `" in _cold)

    # And it must never print a key, on any path.
    _SECRET4 = "AIzaSyDOCTOR-SECRET-KEY-abcdefghijklmnop"
    _fresh("t:doc:leak", [_SECRET4, "OTHER"])
    K._mark_key_health("t:doc:leak", _SECRET4, False, 20, "revoked")
    _rendered = _cmd._render(K.pool_report(), doctor=True)
    check("the doctor leaks no key", _SECRET4 not in _rendered)
    check("...but does name the retired one by fingerprint",
          K._key_short_id(_SECRET4) in _rendered)
    check("...and tells the reader nothing was deleted",
          "Nothing was deleted" in _rendered)


# =============================================================================
# N. THE EVIDENCE THAT WAS ALREADY ON THE EXCEPTION
# =============================================================================
print("\n--- N: reading the exception, not only its prose ---")


class _Resp:
    def __init__(self, text="", headers=None, status_code=None):
        self.text = text
        self.headers = headers or {}
        if status_code is not None:
            self.status_code = status_code


class _Rich(Exception):
    """A provider error whose sentence says nothing and whose fields say it all.

    This is the shape the whole group exists for: the adapter that raised it has
    already consumed the body, so `str(exc)` is a human sentence and every
    machine-readable field is one attribute away, unread.
    """
    def __init__(self, msg, **fields):
        super().__init__(msg)
        for name, value in fields.items():
            setattr(self, name, value)


# N1. A marker that exists ONLY in a parsed `details` dict.
_details_only = _Rich(
    "The model is overloaded. Please try again later.",
    details={"status": "RESOURCE_EXHAUSTED",
             "reason": "RATE_LIMIT_EXCEEDED",
             "metadata": {"quota_limit": "GenerateRequestsPerDayPerProjectPerModel"}},
)
check("a quota named only in `details` is still read as a quota",
      K._classify_error(_details_only)[1] in ("daily", "per_minute", "insufficient_quota"))
check("...and the PerDay in it reaches the daily branch",
      K._classify_error(_details_only)[1] == "daily")
check("...and the quota marker is recovered for the log",
      K._extract_quota_marker(_details_only) == "PerDay")

# N2. A status that lives on the wrapped cause, not on the wrapper. litellm and
# the OpenAI SDK both re-raise, and the outer object does not always carry the
# status the inner one had.
_inner = _Rich("permission denied", status_code=403)
_outer = _Rich("APIError: upstream call failed")
_outer.__cause__ = _inner
check("a status on the cause is found", K._evidence_status(_outer) == 403)
check("...and it changes the verdict", K._classify_error(_outer)[1] == "denied")

# ...but the walk is bounded. A cyclic chain must be a return, never a hang.
_a = _Rich("a")
_b = _Rich("b")
_a.__cause__ = _b
_b.__cause__ = _a
check("a cyclic cause chain terminates", K._evidence_status(_a) is None)

# N3. A body the SDK kept, with the id in it.
_body = _Rich("Too Many Requests", response=_Resp(
    text='{"error":{"details":[{"quotaId":"GenerateRequestsPerMinutePerProject"}]}}'))
check("a quota id in the response body is read",
      K._extract_quota_marker(_body) == "PerMinute")

# N4. Bounded and unraisable. A provider returning a megabyte of HTML must not
# turn every substring check in the engine into a scan of it, and an object
# whose attribute access throws must not take the classifier down.
_huge = _Rich("x", details="y" * 50000)
check("the evidence text is length-bounded",
      len(K._evidence_text(_huge)) <= K._EVIDENCE_MAX_CHARS)


class _Hostile(Exception):
    @property
    def details(self):
        raise RuntimeError("no")

    @property
    def response(self):
        raise RuntimeError("no")


check("evidence gathering never raises",
      isinstance(K._evidence_text(_Hostile("boom")), str))
check("...and the classifier still answers", K._classify_error(_Hostile("boom"))[1] == "other")

# N5. Reading more must not make the classifier trigger-happy. A perfectly
# ordinary success-shaped object carries no verdict.
check("an exception with nothing in it is still just `other`",
      K._classify_error(_Err("something went wrong"))[1] == "other")


# =============================================================================
# O. READ, OR GUESSED
# =============================================================================
print("\n--- O: where each cooldown came from ---")

# Counting failures answers "is anything going wrong". This answers the question
# that matters after an upstream change: is KAME still READING these, or is it
# guessing? A provider that renames a field raises nothing — it quietly moves
# every refusal into the generic bucket.
K._KAME_TALLY = {}
_pool = _fresh("t:tally", ["A", "B"])
K._tally_failure("t:tally", "per_minute", 429, "provider")
K._tally_failure("t:tally", "daily", 429, "kame")
K._tally_failure("t:tally", "other", None, "default")
_t = K.pool_report()["tally"]["t:tally"]
check("the tally counts all three sources",
      _t["provider"] == 1 and _t["kame"] == 1 and _t["default"] == 1 and _t["total"] == 3)
check("...and keeps the kinds apart", _t["kinds"] == {"per_minute": 1, "daily": 1, "other": 1})
check("...and the statuses", _t["statuses"] == {"429": 2, "none": 1})

# A provider that states its own delay is `provider`; the same failure with no
# number anywhere is KAME's own rule.
check("a stated Retry-After is credited to the provider",
      K._delay_source(_Rich("429 rate limit", retry_after=37), "per_minute") == "provider")
check("...and a bare rate limit is credited to KAME",
      K._delay_source(_Err("429 rate limit"), "per_minute") == "kame")
check("...and an unrecognised failure is credited to nobody",
      K._delay_source(_Err("weird"), "other") == "default")

# A refusal is always KAME's own number: no provider tells you when a rejected
# credential will start working.
check("a refusal is never credited to the provider",
      K._delay_source(_Err("401"), "auth") == "kame"
      and K._delay_source(_Err("403"), "denied") == "kame")

# And the tally is counts only.
K._KAME_TALLY = {}
K._tally_failure("openai:" + _SECRET2[:6], "other", None, "default")
check("the tally holds no key",
      _SECRET2 not in _json.dumps(K.pool_report()["tally"]))


print("=" * 60)
if _failures:
    print("FAILURES:", _failures)
    sys.exit(1)
print("ALL v1.6.0.1 TESTS PASSED")

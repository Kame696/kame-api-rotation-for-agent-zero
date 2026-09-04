"""v1.6.0.3 — the provider names the window, and the provider names the wait.

Two rules, one sentence: **KAME never invents a number when the provider has
stated one, and never invents a window when the provider has named one.**

Both come from measurement, not from reasoning about what providers ought to
do. The Hermes port of this plugin ran for 46 minutes against Gemini on
2026-09-03 and produced 340 refusals; every one that carried a number carried
a freshly computed one between 1.5s and 59.8s, and KAME held keys for five
minutes on ten of them. The two ports had the same defect in different
arithmetic — Hermes multiplied the stated number, Agent Zero raised a floor
over it — and this release removes both. See PARITY.md.

Group A is the quota id, which is the only field separating a per-minute
Gemini 429 from a per-day one. Group B is the wait. Group C is what may be
invented when the provider has genuinely said nothing.
"""
import sys, types, os, json


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


# --- the shapes Google actually sends ---------------------------------------

PER_MINUTE = "GenerateRequestsPerMinutePerProjectPerModel-FreeTier"
PER_DAY = "GenerateRequestsPerDayPerProjectPerModel-FreeTier"

#: What Hermes welds onto a free-tier refusal. Agent Zero has its own hosts
#: with their own advice; the point is that *someone else's prose* rides along
#: on the provider's sentence, and it contains the word "day".
HOST_FOOTER = (
    "\n\nYour Google API key is on the free tier (a few hundred requests/day "
    "for Gemini Flash models)."
)


def _payload(quota_id, retry_delay, quota_value):
    return json.dumps({"error": {
        "code": 429,
        "status": "RESOURCE_EXHAUSTED",
        "message": "You exceeded your current quota, please check your plan and billing details.",
        "details": [
            {"@type": "type.googleapis.com/google.rpc.QuotaFailure",
             "violations": [{
                 "quotaMetric": ("generativelanguage.googleapis.com/"
                                 "generate_content_free_tier_requests"),
                 "quotaId": quota_id,
                 "quotaValue": quota_value}]},
            {"@type": "type.googleapis.com/google.rpc.RetryInfo",
             "retryDelay": retry_delay},
        ]}})


class _Response:
    def __init__(self, text):
        self.text = text
        self.status_code = 429


class _Err(Exception):
    def __init__(self, msg, status_code=429, response=None):
        super().__init__(msg)
        self.status_code = status_code
        if response is not None:
            self.response = response


def _gemini(quota_id, retry_delay="41.3s", quota_value="20", footer=True):
    message = "429 RESOURCE_EXHAUSTED: You exceeded your current quota."
    if footer:
        message += HOST_FOOTER
    return _Err(message, 429, _Response(_payload(quota_id, retry_delay, quota_value)))


# =============================================================================
# A. THE QUOTA ID DECIDES THE WINDOW
# =============================================================================
print("\n--- A: the quota id decides the window ---")

# A1. Both free-tier quotas report the identical metric name. Only this field
# tells them apart, so only this field may answer the question.
check("a PerMinute quota id reads as a per-minute window",
      K._quota_window_from_id(_gemini(PER_MINUTE)) == "per_minute")
check("a PerDay quota id reads as a per-day window",
      K._quota_window_from_id(_gemini(PER_DAY, "1s", "250")) == "per_day")

# A2. And it says nothing when the provider filed nothing. Silence here is
# what hands the question back to the substring heuristic, which is right for
# every provider that files no id at all.
check("no quota id means no opinion",
      K._quota_window_from_id(_Err("429 Too Many Requests")) == "")

# A3. The heuristic it replaces, on the same payload. `_is_daily_or_account_limit`
# searches the WHOLE message for "daily", "per day", "/day", "rpd" — and the
# host footer contains "requests/day". This is the false positive: a
# per-minute throttle read as a spent day, and a healthy key sitting out an
# hour for it.
check("the old heuristic is fooled by the host's own footer",
      K._is_daily_or_account_limit(_gemini(PER_MINUTE)) is True)

# A4. So the classifier must not reach it when the provider has named the
# window. This is the whole fix in one assertion.
delay, kind, _sc = K._classify_error(_gemini(PER_MINUTE))
check("a PerMinute id is classified per_minute despite the footer",
      kind == "per_minute")
check("and it rests for the 41.3s Google asked for, not an hour",
      abs(delay - 41.3) < 0.01)

# A5. The other direction, and the reason Agent Zero learned to distrust
# retryDelay in the first place: Google's own forum thread shows 250 daily
# requests spent arriving with `retryDelay: "1s"`.
delay, kind, _sc = K._classify_error(_gemini(PER_DAY, "1s", "250"))
check("a PerDay id is classified daily", kind == "daily")
check("and the misleading 1s is discarded for the hourly re-probe",
      delay == K._KAME_DAILY_COOLDOWN_S)

# A6. A payload with no id at all still reaches the heuristic, so nothing that
# worked before this release stops working.
delay, kind, _sc = K._classify_error(
    _Err("429: Quota exceeded for quota metric 'Requests per day' limit", 429))
check("a provider that names no id is still read from its words",
      kind == "daily")


# =============================================================================
# B. THE STATED WAIT IS THE WAIT
# =============================================================================
print("\n--- B: the stated wait is the wait ---")

IDENT = "google:gemini-3.8-flash"
K._KAME_KEY_HEALTH = {}
K._KAME_STATED_RL = {}
K._get_identity_state(IDENT, ["KEY1"])

# B1. Every strike, not just the first. The floor this branch used to raise
# reached 20s on the second refusal and 300s on the twenty-seventh, over a
# number the provider had stated each time.
rests = [K._mark_key_health(IDENT, "KEY1", False, 41.3, "per_minute",
                            sized_by="provider") for _ in range(10)]
check("a stated 41.3s is obeyed on all ten strikes", rests == [41.3] * 10)

# B2. The ten real values from the Hermes run, smallest and largest included.
K._KAME_KEY_HEALTH = {}
K._KAME_STATED_RL = {}
K._get_identity_state(IDENT, ["KEY2"])
GOOGLE_ASKED = [53.8, 41.1, 37.2, 31.5, 44.0, 22.3, 1.5, 59.8, 12.0, 48.2]
# Marked on separate keys: a cooldown is never shortened, so re-marking one
# key with a smaller number would (correctly) keep the larger deadline and
# measure the wrong thing.
got = []
for i, asked in enumerate(GOOGLE_ASKED):
    key = "K%d" % i
    K._get_identity_state(IDENT, [key])
    got.append(K._mark_key_health(IDENT, key, False, asked, "per_minute",
                                  sized_by="provider"))
check("no rest exceeds the longest number Google ever asked for",
      max(got) <= max(GOOGLE_ASKED))
check("and each one is exactly what was asked", got == GOOGLE_ASKED)


# =============================================================================
# C. WHAT MAY BE INVENTED WHEN NOTHING WAS SAID
# =============================================================================
print("\n--- C: what may be invented ---")

# C1. Nothing, if the provider has spoken before about this model. 232 of the
# Hermes run's 400 refusals were the terse "Resource has been exhausted (e.g.
# check quota)." with no number; the other 168 had already answered them.
K._KAME_KEY_HEALTH = {}
K._KAME_STATED_RL = {}
K._get_identity_state(IDENT, ["KEY3", "KEY4"])
K._mark_key_health(IDENT, "KEY3", False, 53.8, "per_minute", sized_by="provider")
terse = K._mark_key_health(IDENT, "KEY4", False, 20, "per_minute", sized_by="kame")
check("a terse refusal uses what this provider said about this model", terse == 53.8)

# C2. Learned per provider:model, because that is what the window belongs to.
OTHER = "google:gemini-3.5-flash"
K._get_identity_state(OTHER, ["KEY5"])
elsewhere = K._mark_key_health(OTHER, "KEY5", False, 20, "per_minute", sized_by="kame")
check("another model does not inherit it", elsewhere == 20)

# C3. A daily-length number must never teach the short window. On Gemini a
# daily cap classifies as a rate limit too; letting its hour in would mean one
# exhausted day teaching every terse throttle afterwards to rest for an hour,
# which is the original defect wearing a different hat.
DAILY_TEACH = "google:teach"
K._get_identity_state(DAILY_TEACH, ["KEY6", "KEY7"])
K._mark_key_health(DAILY_TEACH, "KEY6", False, 3600, "per_minute", sized_by="provider")
after = K._mark_key_health(DAILY_TEACH, "KEY7", False, 20, "per_minute", sized_by="kame")
check("one exhausted day does not teach the short window", after == 20)

# C4. And when the provider has never said anything at all, the flat re-probe
# stands, with no climb behind it. A rate limit is a rolling window (seconds)
# or a daily cap (hours); nothing lives between them for a ladder to cross.
FLAT = "provider:silent"
K._get_identity_state(FLAT, ["KEY8"])
flat = [K._mark_key_health(FLAT, "KEY8", False, 20, "per_minute", sized_by="kame")
        for _ in range(12)]
check("an unsized throttle rests flat and never climbs", flat == [20] * 12)

# C5. The sibling ladders are untouched. They always had the right shape —
# a floor for the unsized case, never a multiplier over a stated number.
SRV = "provider:server"
K._get_identity_state(SRV, ["KEY9"])
climb = [K._mark_key_health(SRV, "KEY9", False, 0, "server") for _ in range(3)]
check("the 5xx ladder still escalates", climb == [5.0, 10.0, 20.0])


print("=" * 60)
if _failures:
    print("FAILURES:", _failures)
    sys.exit(1)
print("ALL v1.6.0.3 TESTS PASSED")

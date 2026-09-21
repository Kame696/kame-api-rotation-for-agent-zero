"""v1.7.0.5 — the three things the Hermes port measured, brought across.

This release exists because the two ports were run over the same real refusals
and the verdicts were diffed. Most of them already agreed; three did not, and
all three had been measured on the owner's own keys on the Hermes side rather
than reasoned about.

**1. A daily label is evidence, not proof.** Probing fourteen real keys every
two minutes across four models: a key Google had refused with
`GenerateRequestsPerDayPerProjectPerModel-FreeTier` answered again after 6 to
36 minutes, twenty-one times out of twenty-one, and not one interval reached
the hour it was being given. Nothing in the payload separates those from a
real exhaustion — same quotaId, same quotaValue, same prose, same hint. So the
decision moves onto the pool: while any key on that provider:model still
answers, the label costs a re-probe; the hour needs twenty minutes of silence.
The longest the whole pool ever went quiet *while the model still had
capacity* was fifteen minutes.

**2. A millisecond is not a minute.** Google writes `Please retry in
683.050353ms` whenever the wait is under a second. The unit alternation listed
`m` and had no `ms` branch, so the match took the `m`, left the `s`, and 683
milliseconds became 683 minutes. On the Hermes side that cost five keys
between four and twelve hours each in a single ninety-minute session.

**3. A 5xx never escalates.** A 503 is not metered, so a longer rest buys
nothing and only holds back a key that was never at fault. The ladder here
climbed on a *healthy* pool, because the strike counter is per key and only
that key's own success clears it: 7 of 16 measured escalations happened with
another key answering inside the previous two minutes.

Run:  python tests/test_v1_7_0_5.py
"""
import sys, types, os, time


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

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import kame_engine as K  # noqa: E402


class Err(Exception):
    def __init__(self, msg, status_code=None):
        super().__init__(msg)
        self.message = msg
        if status_code is not None:
            self.status_code = status_code
        self.response = types.SimpleNamespace(
            text=msg, status_code=status_code, headers={})


_failures = []


def check(name, cond):
    print(("PASS  " if cond else "FAIL  ") + name)
    if not cond:
        _failures.append(name)


def fresh(identity, keys=("A", "B")):
    K._KAME_KEY_HEALTH.pop(identity, None)
    K._KAME_NO_ANSWER_SINCE.pop(identity, None)
    K._get_identity_state(identity, list(keys))


# ==========================================================================
# 1. The daily label
# ==========================================================================
print("\n--- 1. a daily label is evidence, not proof ---")

ID = "t:daily"
fresh(ID)
first = K._mark_key_health(ID, "A", False, K._KAME_DAILY_COOLDOWN_S, "daily")
check("the first daily label costs a re-probe", first == K._KAME_DAILY_REPROBE_S)
check("...and the re-probe is well short of the hour",
      first < K._KAME_DAILY_COOLDOWN_S)

# Repeating it changes nothing while the pool is alive. This is the half that
# matters most: the old branch read `max(applied, 20 * 2**n)` and the label
# arrived pre-floored at an hour, so every repeat was an hour too.
again = [K._mark_key_health(ID, "A", False, K._KAME_DAILY_COOLDOWN_S, "daily")
         for _ in range(8)]
check("and repeating it does not climb", set(again) == {K._KAME_DAILY_REPROBE_S})

fresh(ID)
K._mark_key_health(ID, "A", False, K._KAME_DAILY_COOLDOWN_S, "daily")
K._KAME_NO_ANSWER_SINCE[ID] = time.time() - K._KAME_POOL_SILENCE_BEFORE_THE_DAY_S - 1
quiet = K._mark_key_health(ID, "A", False, K._KAME_DAILY_COOLDOWN_S, "daily")
check("a pool that has been silent long enough does buy the hour",
      quiet == K._KAME_DAILY_COOLDOWN_S)

# One answer on ANY key of the identity reopens the doubt. The counter is
# about the pool, not the key, which is the whole reason it lives at module
# level instead of beside a credential.
fresh(ID)
K._mark_key_health(ID, "A", False, K._KAME_DAILY_COOLDOWN_S, "daily")
K._KAME_NO_ANSWER_SINCE[ID] = time.time() - K._KAME_POOL_SILENCE_BEFORE_THE_DAY_S - 1
K._mark_key_health(ID, "B", True)
back = K._mark_key_health(ID, "A", False, K._KAME_DAILY_COOLDOWN_S, "daily")
check("a success on a DIFFERENT key reopens the doubt", back == K._KAME_DAILY_REPROBE_S)

# Money is not a window.
fresh("t:money")
paid = K._mark_key_health("t:money", "A", False, K._KAME_DAILY_COOLDOWN_S,
                          "insufficient_quota")
check("insufficient_quota keeps the hour on sight, pool or no pool",
      paid == K._KAME_DAILY_COOLDOWN_S)

# ==========================================================================
# 2. A millisecond is not a minute
# ==========================================================================
print("\n--- 2. a millisecond is not a minute ---")

# The exact sentences, from `refusals.jsonl`, and what they used to cost.
for _text, _seconds, _used_to_cost in (
    ("683.050353ms", 0.683050353, 40983.0),
    ("252.247733ms", 0.252247733, 15135.0),
    ("900ms", 0.9, 54000.0),
):
    got = K._parse_duration_to_seconds(_text)
    check(f"{_text} reads as {_seconds:g}s", abs(got - _seconds) < 1e-6)
    check(f"...and not as {_used_to_cost / 3600:.1f} hours", got < 1.0)

check("6m 11.52s still reads as a compound duration",
      abs(K._parse_duration_to_seconds("6m 11.52s") - 371.52) < 1e-6)
check("2h 30m too", abs(K._parse_duration_to_seconds("2h 30m") - 9000.0) < 1e-6)
check("45.6s is still seconds", abs(K._parse_duration_to_seconds("45.6s") - 45.6) < 1e-6)

# The property, stated without naming a unit, so the next slip is caught even
# if nobody remembers to add its spelling above.
_sub_second = K._extract_retry_delay(Err("Please retry in 683.050353ms.", 429))
check("a sub-second hint can never produce a rest above an hour",
      _sub_second is None or _sub_second < 3600.0)

# ==========================================================================
# 3. A 5xx never escalates
# ==========================================================================
print("\n--- 3. a 5xx never escalates ---")

fresh("t:5xx")
rests = [K._mark_key_health("t:5xx", "A", False, 0, "server") for _ in range(40)]
check("forty 503s in a row all cost the same",
      set(rests) == {K._KAME_SERVER_BASE_S})
check("and that is the base, not a ladder",
      rests[0] == K._KAME_SERVER_BASE_S == 1.0)
# What those forty used to cost, spelled out so the claim cannot rot.
_old = [min(5.0 * (2 ** n), 90.0) for n in range(40)]
check("the old ladder reached its 90s cap", _old[-1] == 90.0)
check("the new one never leaves the base", max(rests) < _old[-1])

# A number the provider states still wins — a 5xx is not an exception to the
# rule the whole engine follows.
fresh("t:5xx-stated")
stated = K._mark_key_health("t:5xx-stated", "A", False, 45.0, "server")
check("a stated 45s on a 5xx is obeyed", stated == 45.0)

# The counter is still kept, because `_thaw_server_cooled_keys` reads it to
# tell a key benched by an outage from a key benched by its own quota.
check("the strike counter still counts",
      K._KAME_KEY_HEALTH["t:5xx"]["keys"]["A"]["consecutive_server"] == 40)

# ==========================================================================
# 4. The release itself
# ==========================================================================
print("\n--- 4. the release ---")

# A floor, not an equality: later releases keep what 1.7.0.5 proved.
check("the engine is 1.7.0.5 or later",
      tuple(int(p) for p in K.KAME_VERSION.split(".")) >= (1, 7, 0, 5))

_here = os.path.dirname(__file__)
_manifest = open(os.path.join(_here, "..", "plugin.yaml"), encoding="utf-8").read()
check("plugin.yaml agrees with the engine", "version: %s" % K.KAME_VERSION in _manifest)

_changelog = open(os.path.join(_here, "..", "CHANGELOG.md"), encoding="utf-8").read()
check("the changelog has a 1.7.0.5 entry", "## v1.7.0.5" in _changelog)
check("exactly one release is marked current", _changelog.count("— current") == 1)
# The jump is the story: nothing between 1.2.0 and this one was ever published,
# and the changelog has to say so rather than leave a hole a reader fills in.
check("and it explains the jump from v1.2.0", "Withdrawn" in _changelog)

print("=" * 60)
if _failures:
    print("FAILURES:", _failures)
    sys.exit(1)
print("ALL v1.7.0.5 TESTS PASSED")

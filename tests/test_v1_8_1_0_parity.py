"""v1.8.1.0 — full parity with the Hermes port: switches, events, files, key health.

Owner's order, 2026-09-21: the Agent Zero 1.8.1.0 must carry every feature,
option and tool the Hermes 1.8.1.0 has. The judgment half is covered by
``test_v1_8_1_0_evidence.py``; this file is the other half:

  A. the switches — rotation off, spread off, carousel off — and their
     environment names, which are the Hermes names;
  B. the events timeline — refused, took over, answered, waited, handed back,
     setting changed;
  C. refusals.jsonl and calls.jsonl — written, redacted, bounded, switchable;
  D. key health on disk — a hold survives a restart, never past the ceiling,
     a corrupt file is ignored, an answer releases it;
  E. reset_pool — every key from zero, the file included (the Hermes port's
     own bug, 2026-09-21: memory cleared, file put every bench back);
  F. the report the chip and the commands read.

Real engine, stubbed host, fake provider calls. No network.
"""
import sys, types, os, asyncio, json, tempfile, time


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

DATA = tempfile.mkdtemp(prefix="kame-a0-parity-")
os.environ["KAME_DATA_DIR"] = DATA

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import kame_engine as K  # noqa: E402
import kame_journal as J  # noqa: E402

K.set_log_level("silent")
K.set_wait_notice(False)

_failures = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        _failures.append(name)


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


KEY_A = "AIzaSyPARITYTESTKEY-aaaa"
KEY_B = "AIzaSyPARITYTESTKEY-bbbb"
KEY_C = "AIzaSyPARITYTESTKEY-cccc"
KEYS = [KEY_A, KEY_B, KEY_C]
ID = "gemini:gemini-3.7-flash"


def _throttle(delay="7s"):
    e = Exception(
        'litellm.RateLimitError: {"error":{"code":429,"status":"RESOURCE_EXHAUSTED",'
        '"message":"Quota exceeded for ' + KEY_A + '","details":[{"@type":'
        '"type.googleapis.com/google.rpc.RetryInfo","retryDelay":"' + delay + '"}]}}'
    )
    e.status_code = 429
    return e


def _fresh():
    K._KAME_KEY_HEALTH.clear()
    K._KAME_STATED_RL.clear()
    K._KAME_NO_ANSWER_SINCE.clear()
    J._reset_for_tests()
    for name in os.listdir(DATA):
        os.unlink(os.path.join(DATA, name))
    K._get_identity_state(ID, KEYS)


def _ctx(script):
    """A carousel ctx whose attempts follow `script`: a list of 'ok' or exceptions."""
    calls = []

    async def attempt(self, key, ctx):
        calls.append(key)
        step = script.pop(0) if script else "ok"
        if step == "ok":
            await ctx["response_callback"]("hi", "hi")
            return ("hi", "")
        raise step

    ctx = {"identity": ID, "all_keys": list(KEYS), "call_type": "Chat",
           "model_short": "gemini-3.7-flash", "attempt": attempt,
           "progress": {"any": False, "reasoning": False}}

    async def _cb(delta, full):
        return None

    ctx["response_callback"], ctx["reasoning_callback"], ctx["tokens_callback"] = \
        K._kame_wrap_callbacks(ctx, _cb, None, None)
    return ctx, calls


def _events(kind=None):
    rows = J.EVENTS.recent()
    return [r for r in rows if kind is None or r["kind"] == kind]


# ==========================================================================
print("\n--- A. switches ---")
check("every switch defaults to what KAME did before it existed",
      (K._KAME_ROTATION_DISABLED, K._KAME_SPREAD_DISABLED, K._KAME_CAROUSEL_DISABLED) == (False, False, False)
      and J.RECORDER_ON and J.TIMINGS_ON and J.SHARE_HEALTH_ON)

# spread off: list order among the ready keys.
_fresh()
K._KAME_KEY_HEALTH[ID]["keys"][KEY_A]["request_log"] = [time.time()] * 5
picked_spread = K._get_best_key(ID, KEYS)[0]
K.set_spread_disabled(True)
_fresh()
K._KAME_KEY_HEALTH[ID]["keys"][KEY_A]["request_log"] = [time.time()] * 5
picked_order = K._get_best_key(ID, KEYS)[0]
check("spread on: the least-loaded key, not the busy first one", picked_spread != KEY_A)
check("spread off: the first ready key in the order written", picked_order == KEY_A)
K._KAME_KEY_HEALTH[ID]["keys"][KEY_A]["sick_until"] = time.time() + 60
check("spread off still skips a resting key", K._get_best_key(ID, KEYS)[0] == KEY_B)
K.set_spread_disabled(False)

# carousel off: one attempt, the key is still rested, the error goes to A0.
_fresh()
K.set_carousel_disabled(True)
ctx, calls = _ctx([_throttle()])
raised = None
try:
    run(K._kame_carousel(None, ctx))
except Exception as exc:
    raised = exc
check("carousel off: the refusal reaches Agent Zero after one attempt",
      raised is not None and len(calls) == 1)
check("...and the refused key was still sized and rested",
      K._KAME_KEY_HEALTH[ID]["keys"][calls[0]]["sick_until"] > time.time() + 5)
K.set_carousel_disabled(False)

# carousel on: the same refusal is rotated around.
_fresh()
ctx, calls = _ctx([_throttle()])
result = run(K._kame_carousel(None, ctx))
check("carousel on: rotated to another key and answered",
      result == ("hi", "") and len(calls) == 2 and calls[0] != calls[1])

# rotation off: the wrapper hands the call to A0 untouched.
original_calls = []


async def _original(self, **kw):
    original_calls.append(kw)
    return "native"


class _Model:
    a0_model_conf = types.SimpleNamespace(provider="gemini")
    model_name = "gemini-3.7-flash"


wrapper = K._kame_make_entry_wrapper("unified_call", _original)
K._get_all_api_keys = (lambda real: (lambda self: list(KEYS)))(K._get_all_api_keys)
K.set_rotation_disabled(True)
out = run(wrapper(_Model(), user_message="x"))
check("rotation off: Agent Zero makes the call itself", out == "native" and len(original_calls) == 1)
K.set_rotation_disabled(False)

# the environment speaks Hermes' names and wins.
for name in ("KAME_ROTATION_DISABLED", "KAME_SPREAD_DISABLED", "KAME_CAROUSEL_DISABLED",
             "KAME_RECORDER_DISABLED", "KAME_CALL_TIMINGS_DISABLED"):
    os.environ[name] = "1"
os.environ["KAME_SHARE_POOL_HEALTH"] = "0"
os.environ["KAME_STORM_COLLAPSE_DISABLED"] = "1"
os.environ["KAME_LIVE_STATUS_DISABLED"] = "1"
os.environ["KAME_DAILY_COOLDOWN"] = "1800"
K._kame_apply_env_overrides()
cur = K.current_settings()
check("the Hermes environment names all apply",
      cur["rotation_disabled"] and cur["spread_disabled"] and cur["carousel_disabled"]
      and cur["refusal_recorder_disabled"] and cur["call_timings_disabled"]
      and not cur["share_pool_health"] and not cur["kame_collapse_storm_logs"]
      and not cur["kame_wait_notice"] and cur["daily_quota_cooldown_seconds"] == 1800.0, cur)
for name in ("KAME_ROTATION_DISABLED", "KAME_SPREAD_DISABLED", "KAME_CAROUSEL_DISABLED",
             "KAME_RECORDER_DISABLED", "KAME_CALL_TIMINGS_DISABLED", "KAME_SHARE_POOL_HEALTH",
             "KAME_STORM_COLLAPSE_DISABLED", "KAME_LIVE_STATUS_DISABLED", "KAME_DAILY_COOLDOWN"):
    os.environ[name] = "0" if name != "KAME_DAILY_COOLDOWN" else "3600"
os.environ["KAME_SHARE_POOL_HEALTH"] = "1"
os.environ["KAME_STORM_COLLAPSE_DISABLED"] = "0"
os.environ["KAME_LIVE_STATUS_DISABLED"] = "1"   # keep the notice off in this test
K._kame_apply_env_overrides()
for name in list(os.environ):
    if name.startswith("KAME_") and name != "KAME_DATA_DIR":
        del os.environ[name]
check("and switch back", not K._KAME_ROTATION_DISABLED and J.RECORDER_ON and J.SHARE_HEALTH_ON)

# ==========================================================================
print("\n--- B. events ---")
_fresh()
ctx, calls = _ctx([_throttle()])
run(K._kame_carousel(None, ctx))
kinds = [r["kind"] for r in reversed(_events())]
check("a refusal, the key that took over, and its answer are on the timeline",
      kinds == ["rotation", "switch", "recovery"], kinds)
row = _events("rotation")[0]
check("the refusal row says why, how long and where the number came from",
      row["reason"] == "throttled" and row["code"] == 429 and row["seconds"] == 7.0
      and row["sized_by"] != "", row)
check("the row carries a fingerprint, never the key",
      row["key"] == K._key_short_id(calls[0]) and KEY_A not in json.dumps(_events()))
check("the provider's text is kept, with the key scrubbed",
      "Quota exceeded" in row["detail"] and "PARITYTESTKEY" not in row["detail"])

_fresh()
for k in KEYS:
    K._mark_key_health(ID, k, False, 3.0, "server")
ctx, calls = _ctx([])
run(K._kame_carousel(None, ctx))
check("a pool that had to wait says so once", len(_events("wait")) == 1)

_fresh()
bad = Exception("unsupported parameter: tools")
bad.status_code = 400
ctx, calls = _ctx([bad])
try:
    run(K._kame_carousel(None, ctx))
except Exception:
    pass
check("an error that is not the key's is recorded as handed back", len(_events("surfaced")) == 1)

K._KAME_LAST_SETTINGS = None
K.note_setting_changes()
K.set_max_hold(1200)
changed = K.note_setting_changes()
K.set_max_hold(3600)
K.note_setting_changes()
check("a setting change goes on the timeline",
      changed == ["max_hold_seconds"] and any("max_hold_seconds" in r["reason"] for r in _events("setting")))

# ==========================================================================
print("\n--- C. the two files ---")
_fresh()
ctx, calls = _ctx([_throttle("9s")])
run(K._kame_carousel(None, ctx))
ref = [json.loads(l) for l in open(os.path.join(DATA, J.REFUSALS_FILE), encoding="utf-8")]
cal = [json.loads(l) for l in open(os.path.join(DATA, J.CALLS_FILE), encoding="utf-8")]
check("one refusal row, in the Hermes row shape",
      len(ref) == 1 and {"at", "provider", "model", "status", "type", "message", "body"} <= set(ref[0])
      and ref[0]["provider"] == "gemini" and ref[0]["status"] == 429 and ref[0]["kind"] == "per_minute")
check("one timing row per attempt, both tied to one call",
      [c["outcome"] for c in cal] == ["refused", "ok"] and cal[0]["call"] == cal[1]["call"]
      and cal[0]["rest_s"] == 9.0 and cal[1]["ms_to_first_text"] is not None)
raw_files = open(os.path.join(DATA, J.REFUSALS_FILE), encoding="utf-8").read() + \
    open(os.path.join(DATA, J.CALLS_FILE), encoding="utf-8").read()
check("no key in either file", all(k not in raw_files for k in KEYS) and "PARITYTESTKEY" not in raw_files)

_fresh()
K.set_recorder_disabled(True)
K.set_call_timings_disabled(True)
ctx, calls = _ctx([_throttle()])
run(K._kame_carousel(None, ctx))
check("both switches stop the writing",
      not os.path.exists(os.path.join(DATA, J.REFUSALS_FILE))
      and not os.path.exists(os.path.join(DATA, J.CALLS_FILE)))
K.set_recorder_disabled(False)
K.set_call_timings_disabled(False)

h = J.safe_headers({"Retry-After": "7", "Authorization": "Bearer abcdefghijkl",
                    "x-ratelimit-reset-requests": "12s", "x-api-key": "zzz",
                    "x-quota-account": "someone@example.com", "Content-Type": "json"})
check("headers: allowlisted, credentials and addresses dropped",
      h == {"retry-after": "7", "x-ratelimit-reset-requests": "12s"}, h)

# ==========================================================================
print("\n--- D. key health on disk ---")
_fresh()
K._mark_key_health(ID, KEY_A, False, 600.0, "per_minute")
health = json.load(open(os.path.join(DATA, J.HEALTH_FILE), encoding="utf-8"))
check("a hold is written, keyed by a hash",
      ID in health["holds"] and KEY_A not in json.dumps(health) and len(health["holds"][ID]) == 1)
# a restart: memory gone, the file stays.
K._KAME_KEY_HEALTH.clear()
J._HEALTH.clear()
J._HEALTH_LOADED = False
K._get_identity_state(ID, KEYS)
until = K._KAME_KEY_HEALTH[ID]["keys"][KEY_A]["sick_until"]
check("the hold survives a restart", until > time.time() + 500)
check("the other keys come back clean",
      K._KAME_KEY_HEALTH[ID]["keys"][KEY_B]["sick_until"] == 0)
# bounded by the ceiling in force when it is read back.
K._KAME_KEY_HEALTH.clear()
J._HEALTH.clear()
J._HEALTH_LOADED = False
K.set_max_hold(60)
K._get_identity_state(ID, KEYS)
check("read back never longer than the ceiling now",
      K._KAME_KEY_HEALTH[ID]["keys"][KEY_A]["sick_until"] <= time.time() + 61)
K.set_max_hold(3600)
# an answer releases it on disk.
K._mark_key_health(ID, KEY_A, True)
health = json.load(open(os.path.join(DATA, J.HEALTH_FILE), encoding="utf-8"))
check("an answer releases the hold on disk", not health["holds"].get(ID))
# a corrupt file is nothing known.
open(os.path.join(DATA, J.HEALTH_FILE), "w").write("{not json")
K._KAME_KEY_HEALTH.clear()
J._HEALTH.clear()
J._HEALTH_LOADED = False
K._get_identity_state(ID, KEYS)
check("a corrupt file is ignored: memory only, every key ready",
      all(K._KAME_KEY_HEALTH[ID]["keys"][k]["sick_until"] == 0 for k in KEYS))
# switched off: nothing written, nothing read.
_fresh()
K.set_share_pool_health(False)
K._mark_key_health(ID, KEY_A, False, 600.0, "per_minute")
check("share_pool_health off writes nothing", not os.path.exists(os.path.join(DATA, J.HEALTH_FILE)))
K.set_share_pool_health(True)

# ==========================================================================
print("\n--- E. reset_pool ---")
_fresh()
for k in KEYS:
    K._mark_key_health(ID, k, False, 600.0, "per_minute")
K._KAME_KEY_HEALTH[ID]["keys"][KEY_B]["retired_at"] = time.time()
count = K.reset_pool()
check("every key is ready again", count == 3 and
      all(K._KAME_KEY_HEALTH[ID]["keys"][k]["sick_until"] == 0 for k in KEYS)
      and not K._KAME_KEY_HEALTH[ID]["keys"][KEY_B]["retired_at"])
K._KAME_KEY_HEALTH.clear()
J._HEALTH.clear()
J._HEALTH_LOADED = False
K._get_identity_state(ID, KEYS)
check("...and the file does not put the benches back after a restart",
      all(K._KAME_KEY_HEALTH[ID]["keys"][k]["sick_until"] == 0 for k in KEYS))

# ==========================================================================
print("\n--- F. the report ---")
rep = K.pool_report()
check("the report carries the events, the settings and where the files are",
      isinstance(rep["events"], list) and rep["settings"]["share_pool_health"] is True
      and rep["data_dir"] == DATA)
check("no key anywhere in the report", all(k not in json.dumps(rep, default=str) for k in KEYS))

# The first zip of the republished 1.8.1.0 left out kame_evidence.py — a file
# integrity.py REQUIRES — and the three optional modules. The release zip must
# carry every file integrity.py names.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
import package as _package  # noqa: E402
check("the release zip carries every file integrity.py names",
      _package.not_packaged(_package.collect()) == [], _package.not_packaged(_package.collect()))

print("=" * 60)
if _failures:
    print("FAILURES:", _failures)
    sys.exit(1)
print("ALL v1.8.1.0 PARITY TESTS PASSED")

"""v1.2.0 — THE WAIT IS SAID WHERE THE USER IS LOOKING.

The 1.2.0 thesis for Agent Zero: rotation was never the problem, silence was.
When every key in a pool is cooling, KAME announces it on the console — which
is where an operator reading a Docker log lives, and not where the person
waiting on the answer lives. To them a pool waiting out a daily quota and a
hung agent look identical, and the move that looks available is restarting
Agent Zero, which throws away both the wait and the context. ADR 0002 named
this when it removed the rotation ceiling and left it open.

So the facts already in the console are now ALSO put in the chat, as one log
item that keeps updating. These tests run with stubs (no real Agent Zero, no
litellm, no network) and cover:

  A. Threshold — a short wait says nothing; only a wait past the threshold
     opens the notice, and the switch turns it off completely.
  B. One item — the notice is created ONCE and updated in place thereafter,
     throttled to the refresh interval, so a 40-minute quota is one line and
     not two hundred.
  C. Content — counts, the pool name and an ETA; never a key, in any field.
  D. Closing — 'resumed' after a key answers, 'stopped' on every other way out,
     and a no-op when no notice was ever shown.
  E. Survivability — no agent, no context, no log, or a log that raises: each
     one disables the notice for the call and NEVER breaks rotation.
  F. Wiring — the carousel closes the notice on all four exits; the activation
     extension reads the setting; the setting exists with the documented
     default; the settings screen exposes it with a matching x-init default.
  G. Release — the version moved everywhere it is written.

Run:  python tests/test_v1_2_0.py
Exit code 0 = all pass, 1 = at least one failure.
"""
import sys, types, os, asyncio, inspect, re, time


def _stub(name):
    m = types.ModuleType(name)
    sys.modules[name] = m
    return m


_stub("openai")
_litellm = _stub("litellm")
_litellm.suppress_debug_info = False
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

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, ROOT)
import kame_engine as K  # noqa: E402

K.set_log_level("silent")

_failures = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ((" - " + detail) if detail and not cond else ""))
    if not cond:
        _failures.append(name)


# --- the minimum chat Agent Zero gives KAME --------------------------------

class FakeItem:
    """A0's LogItem: created once, then edited in place."""

    def __init__(self):
        self.updates = []


    def update(self, **kw):
        self.updates.append(kw)


class FakeLog:
    def __init__(self, boom=False):
        self.boom = boom
        self.created = []       # one kwargs dict per log() call
        self.items = []

    def log(self, **kw):
        if self.boom:
            raise RuntimeError("the log is having a bad day")
        self.created.append(kw)
        item = FakeItem()
        self.items.append(item)
        return item


class FakeContext:
    def __init__(self, log):
        self.log = log


class FakeAgent:
    def __init__(self, log=None, context=True):
        self.context = FakeContext(log if log is not None else FakeLog()) if context else None


IDENT = "gemini:gemini-3.5-flash"
KEYS = ["KEY-A", "KEY-B", "KEY-C"]
SECRET = "AIzaSyTOTALLY-SECRET-KEY-VALUE"


def cold_pool(keys=KEYS, rest_s=1800.0, identity=IDENT):
    """Put every key in `keys` to sleep, and return a state that has been
    waiting long enough for the notice to be due."""
    K._get_identity_state(identity, keys)
    for k in keys:
        K._mark_key_health(identity, k, False, rest_s, "daily")
    st = K._KameSleepState()
    st.cold_since = time.time() - (K._KAME_WAIT_NOTICE_AFTER_S + 30.0)
    return st


def tick(st, keys=KEYS, identity=IDENT):
    K._kame_wait_notice_tick(st, identity, keys, "chat", "gemini-3.5-flash")


def all_text(log):
    """Every string the notice put on screen, from both create and update."""
    out = []
    for payload in list(log.created) + [u for it in log.items for u in it.updates]:
        for v in payload.values():
            if isinstance(v, dict):
                out.extend(f"{a} {b}" for a, b in v.items())
            else:
                out.append(str(v))
    return "\n".join(out)


# ==========================================================================
# A. Threshold — silence is correct for a short wait.
# ==========================================================================
check("A0 the notice functions exist",
      callable(getattr(K, "_kame_wait_notice_tick", None))
      and callable(getattr(K, "_kame_wait_notice_finish", None)))
check("A1 the threshold is a real wait, not a blink",
      K._KAME_WAIT_NOTICE_AFTER_S >= 60.0,
      str(K._KAME_WAIT_NOTICE_AFTER_S))
check("A2 refreshing is a countdown, not a flood",
      5.0 <= K._KAME_WAIT_NOTICE_REFRESH_S <= 60.0,
      str(K._KAME_WAIT_NOTICE_REFRESH_S))

_log = FakeLog()
K.set_current_agent(FakeAgent(_log))
_short = cold_pool()
_short.cold_since = time.time() - 1.0          # one second into the wait
tick(_short)
check("A3 a one-second wait says nothing", _log.created == [])

_st = cold_pool()
tick(_st)
check("A4 a wait past the threshold opens the notice", len(_log.created) == 1)

K.set_wait_notice(False)
_off_log = FakeLog()
K.set_current_agent(FakeAgent(_off_log))
tick(cold_pool())
check("A5 the setting turns it off completely", _off_log.created == [])
K.set_wait_notice(True)
check("A6 set_wait_notice accepts A0's string truthiness", (
    K.set_wait_notice("false") or K._KAME_WAIT_NOTICE is False)
    and (K.set_wait_notice("on") or K._KAME_WAIT_NOTICE is True))


# ==========================================================================
# B. One item, updated in place — the whole point of using LogItem.update.
# ==========================================================================
_log = FakeLog()
K.set_current_agent(FakeAgent(_log))
_st = cold_pool()
tick(_st)
_st.notice_refreshed_at = 0.0                   # force the next tick through
tick(_st)
_st.notice_refreshed_at = 0.0
tick(_st)
check("B1 a long wait is ONE log item, not one per tick", len(_log.created) == 1,
      f"created={len(_log.created)}")
check("B2 the later ticks edited that item in place",
      len(_log.items) == 1 and len(_log.items[0].updates) == 2,
      str([len(i.updates) for i in _log.items]))

_st.notice_refreshed_at = time.time()           # just refreshed
_before = len(_log.items[0].updates)
tick(_st)
check("B3 ticks inside the refresh interval are dropped",
      len(_log.items[0].updates) == _before)


# ==========================================================================
# C. What it says — counts and a pool, never a key.
# ==========================================================================
_log = FakeLog()
K.set_current_agent(FakeAgent(_log))
_secret_keys = KEYS + [SECRET]
_st = cold_pool(_secret_keys)
tick(_st, _secret_keys)
_txt = all_text(_log)
check("C1 no key material reaches the chat, in any field", SECRET not in _txt)
check("C2 it says how many keys are resting", re.search(r"\b4 of 4\b", _txt) is not None, _txt)
check("C3 it names the pool", IDENT in _txt)
check("C4 it gives an earliest-recovery estimate",
      "recovery" in _txt.lower() and re.search(r"\d+\s*(m|h|s)", _txt) is not None)
check("C5 it says no API calls are being made", "No API calls" in _txt)
check("C6 it says stop still works", "stop" in _txt.lower())
check("C7 it says it resumes by itself", "resumes by itself" in _txt)
check("C8 the heading carries the elapsed time so a frozen UI is visible",
      "KAME" in _log.created[0].get("heading", "")
      and re.search(r"\d", _log.created[0].get("heading", "")) is not None,
      _log.created[0].get("heading", ""))
check("C9 it is a util item (A0's neutral UI type)",
      _log.created[0].get("type") == "util", str(_log.created[0].get("type")))


# ==========================================================================
# D. Closing the notice.
# ==========================================================================
K._kame_wait_notice_finish(_st, "resumed")
_last = _log.items[0].updates[-1]
check("D1 'resumed' says a key came back",
      "resumed" in str(_last).lower() and "waited" in str(_last).lower())
check("D2 closing clears the item so it cannot be written twice",
      _st.notice_item is None)
_n = len(_log.items[0].updates)
K._kame_wait_notice_finish(_st, "resumed")
check("D3 closing twice is a no-op", len(_log.items[0].updates) == _n)

_log2 = FakeLog()
K.set_current_agent(FakeAgent(_log2))
_st2 = cold_pool()
tick(_st2)
K._kame_wait_notice_finish(_st2, "stopped")
check("D4 'stopped' does not claim the wait succeeded",
      "stopped" in str(_log2.items[0].updates[-1]).lower()
      and "resumed" not in str(_log2.items[0].updates[-1]).lower())

_never = K._KameSleepState()
K._kame_wait_notice_finish(_never, "resumed")
check("D5 closing a notice that was never shown is a no-op", _never.notice_item is None)


# ==========================================================================
# E. Survivability — a missing or hostile chat can never break rotation.
# ==========================================================================
def survives(agent, label):
    K.set_current_agent(agent)
    st = cold_pool()
    try:
        tick(st)
        ok = True
    except Exception as e:                      # noqa: BLE001 - that IS the test
        ok = False
        print("   raised:", type(e).__name__, e)
    check(label, ok and st.notice_broken is True)


survives(None, "E1 no agent at all: no raise, notice disabled for the call")
survives(FakeAgent(context=False), "E2 no context (CLI / task runner): safe")
survives(FakeContext(None), "E3 a context with no log: safe")
survives(FakeAgent(FakeLog(boom=True)), "E4 a log that raises: safe")

K.set_current_agent(FakeAgent(FakeLog(boom=True)))
_broken = cold_pool()
tick(_broken)
_before_broken = _broken.notice_broken
_broken.notice_refreshed_at = 0.0
tick(_broken)
check("E5 a broken notice is not retried every second",
      _before_broken is True and _broken.notice_broken is True)


class _BadItem(FakeItem):
    def update(self, **kw):
        raise RuntimeError("update exploded")


_bad_log = FakeLog()
_bad_log.log = lambda **kw: _BadItem()          # type: ignore[assignment]
K.set_current_agent(FakeAgent(_bad_log))
_st3 = cold_pool()
tick(_st3)
try:
    K._kame_wait_notice_finish(_st3, "resumed")
    _finish_safe = True
except Exception:
    _finish_safe = False
check("E6 closing a notice whose update() explodes never raises", _finish_safe)


# ==========================================================================
# F. Wiring — the carousel, the extension, the setting, the screen.
# ==========================================================================
_engine_src = open(os.path.join(ROOT, "kame_engine.py"), encoding="utf-8").read()
_carousel_src = inspect.getsource(K._kame_carousel)
_sleep_src = inspect.getsource(K._kame_sleep_on_exhaustion)

check("F1 the notice is refreshed from inside the sleep slices (a live countdown)",
      "_kame_wait_notice_tick(" in _sleep_src)
check("F2 every exit from the carousel closes the notice",
      _carousel_src.count("_kame_wait_notice_finish(") == 4,
      str(_carousel_src.count("_kame_wait_notice_finish(")))
check("F3 a success closes it as 'resumed'",
      '_kame_wait_notice_finish(st, "resumed")' in _carousel_src)
check("F4 stop / nudge during the sleep closes it as 'stopped'",
      _carousel_src.count('_kame_wait_notice_finish(st, "stopped")') == 3)
check("F5 the notice never touches the model's history",
      not re.search(r"_kame_wait_notice[\s\S]{0,3000}?(hist_add|history\.|append_message)",
                    _engine_src))

_act_src = open(os.path.join(ROOT, "kame_activation.py"), encoding="utf-8").read()
check("F6 the activation extension applies the setting",
      "set_wait_notice" in _act_src and 'cfg.get("kame_wait_notice", True)' in _act_src)
check("F7 the setter is fetched by name, so an older engine cannot break activation",
      re.search(r'getattr\(\s*_kame_engine\s*,\s*"set_wait_notice"', _act_src) is not None)

_yaml_src = open(os.path.join(ROOT, "default_config.yaml"), encoding="utf-8").read()
check("F8 the setting ships with a documented default",
      re.search(r"^kame_wait_notice:\s*true\s*$", _yaml_src, re.M) is not None)

_html = open(os.path.join(ROOT, "webui", "config.html"), encoding="utf-8").read()
for _key, _default in (("kame_wait_notice", "true"),
                       ("kame_collapse_storm_logs", "true"),
                       ("kame_log_level", "'normal'"),
                       ("key_log_style", "'fingerprint'"),
                       ("daily_quota_cooldown_seconds", "3600"),
                       ("kame_unusable_response_limit", "5")):
    check(f"F9 the settings screen exposes {_key}", f"config.{_key}" in _html)
    check(f"F10 {_key} carries an x-init default matching the yaml",
          re.search(rf"config\.{_key}\s*=\s*{re.escape(_default)}", _html) is not None)

check("F11 every setting in default_config.yaml is reachable from the screen "
      "(except the debug-only raw-error dump, which the log level covers)",
      all(f"config.{k}" in _html
          for k in re.findall(r"^([a-z_]+):", _yaml_src, re.M)
          if k != "kame_log_full_errors"),
      str([k for k in re.findall(r"^([a-z_]+):", _yaml_src, re.M)
           if k != "kame_log_full_errors" and f"config.{k}" not in _html]))
check("F12 the screen uses A0's own settings markup",
      'class="field-title"' in _html and 'class="section-title"' in _html
      and 'x-if="config"' in _html)
check("F13 both shelves are labelled and explained",
      "What KAME tells you" in _html and "Tuning" in _html
      and "works with none of these touched" in _html)


# ==========================================================================
# G. The release.
# ==========================================================================
_plugin_yaml = open(os.path.join(ROOT, "plugin.yaml"), encoding="utf-8").read()
check("G1 the engine version moved", K.KAME_VERSION == "1.2.0", K.KAME_VERSION)
check("G2 plugin.yaml agrees",
      re.search(r"^version:\s*1\.2\.0\s*$", _plugin_yaml, re.M) is not None)
check("G3 the settings key was NOT renamed (renaming it orphans every install)",
      re.search(r"^name:\s*api_rotation_by_kame\s*$", _plugin_yaml, re.M) is not None)
check("G4 the changelog has a 1.2.0 entry",
      "1.2.0" in open(os.path.join(ROOT, "CHANGELOG.md"), encoding="utf-8").read())

print("=" * 60)
if _failures:
    print("FAILURES:", _failures)
    sys.exit(1)
print("ALL v1.2.0 TESTS PASSED")

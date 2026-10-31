"""v1.2.0 — THE WAIT IS SAID WHERE THE USER IS LOOKING.

The 1.2.0 thesis for Agent Zero: rotation was never the problem, silence was.
When every key in a pool is cooling, KAME announces it on the console — which
is where an operator reading a Docker log lives, and not where the person
waiting on the answer lives. To them a pool waiting out a daily quota and a
hung agent look identical, and the move that looks available is restarting
Agent Zero, which throws away both the wait and the context. ADR 0002 named
this when it removed the rotation ceiling and left it open.

So the facts already in the console are now ALSO put where the person is looking.
1.2.0 did it as one log item in the chat after ninety seconds; 1.8.2.9 (owner's order) moved
the words onto the chat's own status line ("A0: Calling LLM...") and removed the chat row
and the toast. These tests run with stubs (no real Agent Zero, no litellm, no network) and
cover:

  A. The wait is on the status line, never in a chat row; the switch turns it off.
  B. One line - the same words are not written twice.
  C. Content - counts over keys and the time left; never a key.
  D. Giving the line back - 'resumed' / 'stopped', and a no-op when nothing was written.
  E. Survivability - no agent, no context, no log, or a log that raises: each one
     leaves rotation untouched.
  F. Wiring - the carousel closes the wait on every exit; the activation extension reads
     the setting; the setting exists with the documented default; the settings screen
     exposes it with a matching x-init default.
  G. Release - the version moved everywhere it is written.

(The full status-line contract is tests/test_v1_8_2_9_status_line.py.)

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
    st.cold_since = time.time() - 120.0
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
# A. The wait is said on the status line - and nowhere else (1.8.2.9, owner's order).
#    1.2.0 put one log row in the chat after ninety seconds and 1.6.0.1 raised a
#    toast after fifteen; both are gone. The words live in the heading of the
#    chat's own "Calling LLM..." item (tests/test_v1_8_2_9_status_line.py covers
#    them in full); here the contract of the old entry points is kept honest.
# ==========================================================================
class LineItem:
    """Agent Zero's "Calling LLM..." log item: only its heading is edited."""

    def __init__(self):
        self.heading = "A0: Calling LLM..."
        self.headings = []

    def update(self, heading=None, **kw):
        self.heading = heading
        self.headings.append(heading)


class LineAgent:
    def __init__(self, log=None, context=True, item=True):
        self.agent_name = "A0"
        self.number = 0
        self.item = LineItem()
        self.context = FakeContext(log if log is not None else FakeLog()) if context else None
        self.loop_data = types.SimpleNamespace(
            params_temporary={"log_item_generating": self.item} if item else {})


check("A0 the notice functions exist",
      callable(getattr(K, "_kame_wait_notice_tick", None))
      and callable(getattr(K, "_kame_wait_notice_finish", None)))
check("A1 the toast and the ninety-second chat row are gone",
      not hasattr(K, "_kame_toast") and not hasattr(K, "_kame_wait_notice_toast")
      and not hasattr(K, "_KAME_WAIT_NOTICE_AFTER_S") and not hasattr(K, "_KAME_NOTICE_TOAST_AFTER_S")
      and not hasattr(K._KameSleepState(), "notice_item") and not hasattr(K._KameSleepState(), "toast_shown"))
check("A2 the status line is a countdown, not a flood",
      K._KAME_STATUS_LINE_EVERY_S == 1.0, str(K._KAME_STATUS_LINE_EVERY_S))   # 1.8.3.1 (D2): once a second

_log = FakeLog()
_agent = LineAgent(_log)
K.set_current_agent(_agent)
_short = cold_pool()
_short.cold_since = time.time() - 1.0          # one second into the wait
tick(_short)
check("A3 a wait never opens a row in the chat, however long (Q2)", _log.created == [])
_st = cold_pool()
tick(_st)
check("A4 even a wait of minutes opens no row", _log.created == [])
check("A4b ... it is on the status line, in the live line's words (1.8.3.1)",
      "Every key of " in _agent.item.heading and "is resting" in _agent.item.heading,
      _agent.item.heading)

K.set_wait_notice(False)
_off = LineAgent()
K.set_current_agent(_off)
tick(cold_pool())
check("A5 the setting turns it off completely", _off.item.headings == [])
K.set_wait_notice(True)
check("A6 set_wait_notice accepts A0's string truthiness", (
    K.set_wait_notice("false") or K._KAME_WAIT_NOTICE is False)
    and (K.set_wait_notice("on") or K._KAME_WAIT_NOTICE is True))


# ==========================================================================
# B. One line, updated in place - and not twice for the same words.
# ==========================================================================
_agent = LineAgent()
K.set_current_agent(_agent)
_st = cold_pool()
tick(_st)
tick(_st)
tick(_st)
check("B1 the same words are written once", len(_agent.item.headings) == 1,
      str(_agent.item.headings))


# ==========================================================================
# C. What it says - counts and time left, never a key.
# ==========================================================================
_agent = LineAgent()
K.set_current_agent(_agent)
_secret_keys = KEYS + [SECRET]
_st = cold_pool(_secret_keys)
tick(_st, _secret_keys)
_txt = _agent.item.heading
check("C1 no key material reaches the chat", SECRET not in _txt)
check("C2 it names the model whose keys all rest (1.8.3.1: the count lives in the asking line)",
      re.search(r"Every key of \S+ is resting", _txt) is not None, _txt)
check("C4 it gives the time left, in the page's format", re.search(r"next key in \d+m \d\ds", _txt) is not None, _txt)
check("C5 it is the agent's name, then the live line", _txt.startswith("A0: ⏳ Every key of "), _txt)
check("C6 the age of the wait is not on the line", "waited" not in _txt.lower() and "since" not in _txt.lower())


# ==========================================================================
# D. Giving the line back.
# ==========================================================================
K._kame_wait_notice_finish(_st, "resumed")
check("D1 'resumed' gives Agent Zero's own heading back", _agent.item.heading == "A0: Calling LLM...",
      _agent.item.heading)
check("D2 closing clears the flag so it cannot be written twice", _st.line_on is False)
_n = len(_agent.item.headings)
K._kame_wait_notice_finish(_st, "resumed")
check("D3 closing twice is a no-op", len(_agent.item.headings) == _n)
_agent2 = LineAgent()
K.set_current_agent(_agent2)
_st2 = cold_pool()
tick(_st2)
K._kame_wait_notice_finish(_st2, "stopped")
check("D4 'stopped' gives it back too", _agent2.item.heading == "A0: Calling LLM...")
_never = K._KameSleepState()
K._kame_wait_notice_finish(_never, "resumed")
check("D5 closing a wait that never wrote the line is a no-op", _never.line_on is False)


# ==========================================================================
# E. Survivability - a missing or hostile chat can never break rotation.
# ==========================================================================
def survives(agent, label):
    K.set_current_agent(agent)
    st = cold_pool()
    try:
        tick(st)
        K._kame_wait_notice_finish(st, "stopped")
        ok = True
    except Exception as e:                      # noqa: BLE001 - that IS the test
        ok = False
        print("   raised:", type(e).__name__, e)
    check(label, ok)


survives(None, "E1 no agent at all: no raise")
survives(LineAgent(context=False, item=False), "E2 no context (CLI / task runner): safe")
survives(LineAgent(FakeLog(), item=False), "E3 a context whose log has no progress line: safe")
survives(LineAgent(FakeLog(boom=True)), "E4 a log that raises: safe")

_bad = LineAgent()
_bad.item.update = lambda **kw: (_ for _ in ()).throw(RuntimeError("update exploded"))
survives(_bad, "E5 an item whose update() explodes: safe")


# ==========================================================================
# F. Wiring — the carousel, the extension, the setting, the screen.
# ==========================================================================
_engine_src = open(os.path.join(ROOT, "kame_engine.py"), encoding="utf-8").read()
_carousel_src = inspect.getsource(K._kame_carousel)
_sleep_src = inspect.getsource(K._kame_sleep_on_exhaustion)

check("F1 the notice is refreshed from inside the sleep slices (a live countdown)",
      "_kame_wait_notice_tick(" in _sleep_src)
# v1.8.1.0 adds a fifth exit: `carousel_disabled` hands the error to Agent
# Zero after one attempt, and closes the notice on the way out like the rest.
# 1.8.2.9 review L1: this used to count the source occurrences of the call (a change detector that
# stayed green while the circle step could leave a heading standing). The property - a stop or a
# new message in the middle of ANY wait gives Agent Zero's words back - is now tested by behaviour in
# tests/test_v1_8_2_9_review_fixes.py (exhaustion sleep, circle wait, race line).
check("F2 the notice is closed on the exits of the carousel (behaviour: test_v1_8_2_9_review_fixes.py)",
      _carousel_src.count("_kame_wait_notice_finish(") >= 1
      and os.path.exists(os.path.join(ROOT, "tests", "test_v1_8_2_9_review_fixes.py")))
check("F3 a success closes it as 'resumed'",
      '_kame_wait_notice_finish(st, "resumed")' in _carousel_src)
check("F4 stop / nudge during the sleep closes it as 'stopped'",
      _carousel_src.count('_kame_wait_notice_finish(st, "stopped")') == 9)
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
# 1.8.3.0: these two are stored under Hermes' names now (the opposite switches); the old keys are only read
for _key, _default in (("live_status_disabled", "false"),
                       ("storm_collapse_disabled", "false"),
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
          if k not in ("kame_log_full_errors", "kame_wait_notice", "kame_collapse_storm_logs")),
      str([k for k in re.findall(r"^([a-z_]+):", _yaml_src, re.M)
           if k not in ("kame_log_full_errors", "kame_wait_notice", "kame_collapse_storm_logs")
           and f"config.{k}" not in _html]))
check("F12 the screen uses A0's own settings markup",
      'class="field-title"' in _html and 'class="section-title"' in _html
      and 'x-if="config"' in _html)
# 1.8.2.7 rethought the screen: six on show, "Show advanced settings", and the
# off-switches folded under Troubleshooting (the Hermes 1.8.2.7 layout).
check("F13 the shelves are labelled and explained",
      "Show advanced settings" in _html and "Troubleshooting" in _html
      and "works with none of these touched" in _html)


# ==========================================================================
# G. The release.
# ==========================================================================
_plugin_yaml = open(os.path.join(ROOT, "plugin.yaml"), encoding="utf-8").read()
# Loosened in 1.6.0.1 for the reason test_v1_0_9.py already wrote down: a test
# that pins the CURRENT version inside an old release's suite has to be edited
# on every release, and a test edited on every release stops being read. The
# invariant worth defending here was never the digits — it is that the engine
# and the manifest agree, because a plugin whose banner and manifest disagree is
# how you end up debugging a version that is not installed.
check("G1 the engine version is at or past this release's floor",
      tuple(int(p) for p in K.KAME_VERSION.split(".")) >= (1, 2, 0), K.KAME_VERSION)
_yaml_version = re.search(r"^version:\s*(\S+)\s*$", _plugin_yaml, re.M)
check("G2 plugin.yaml agrees with the engine, to the digit",
      _yaml_version is not None and _yaml_version.group(1) == K.KAME_VERSION,
      f"yaml={_yaml_version.group(1) if _yaml_version else None} engine={K.KAME_VERSION}")
check("G3 the settings key was NOT renamed (renaming it orphans every install)",
      re.search(r"^name:\s*api_rotation_by_kame\s*$", _plugin_yaml, re.M) is not None)
check("G4 the changelog has a 1.2.0 entry",
      "1.2.0" in open(os.path.join(ROOT, "CHANGELOG.md"), encoding="utf-8").read())

print("=" * 60)
if _failures:
    print("FAILURES:", _failures)
    sys.exit(1)
print("ALL v1.2.0 TESTS PASSED")

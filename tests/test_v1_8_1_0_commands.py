"""v1.8.1.0 — the Hermes commands, on Agent Zero.

`/kame get|set|reset|events|clear-pool|help`, `/kame-quota [reset]` and
`/kame-keys [status|add|import|reset]`, driven through their real `run()`
with Agent Zero's config and `.env` helpers stubbed onto a temp directory.

What is held here:
  * every setting in the one table is readable, settable, resettable, and the
    settings page and default_config know it too;
  * `set` saves through Agent Zero's plugin config and is in force at once;
  * `/kame-keys add` merges into the provider's comma-separated line, skips
    duplicates, backs the file up first (last 5), and never prints a key;
  * `/kame-keys import` reads a file, BOM or not;
  * `/kame-quota reset` clears counts and leaves live rests alone, while
    `/kame clear-pool` starts every key from zero.
"""
import sys, types, os, tempfile, json, runpy, time, re


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
_helpers = _stub("helpers")
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

TMP = tempfile.mkdtemp(prefix="kame-a0-commands-")
os.environ["KAME_DATA_DIR"] = os.path.join(TMP, "plugin-data")
ENV = os.path.join(TMP, ".env")
CONFIG = {}

# helpers.plugins — Agent Zero's plugin config, on a dict.
_plugins = _stub("helpers.plugins")
_plugins.get_plugin_config = lambda name, agent=None, **k: dict(CONFIG) or None
_plugins.save_plugin_config = lambda name, project, profile, settings, **k: (CONFIG.clear(), CONFIG.update(settings))

# helpers.dotenv — Agent Zero's .env writer, on a temp file (same algorithm).
_dotenv = _stub("helpers.dotenv")
_dotenv.get_dotenv_file_path = lambda: ENV


def _save_dotenv_value(key, value, reload_env=True):
    lines = open(ENV, encoding="utf-8").read().splitlines(True) if os.path.exists(ENV) else []
    found = False
    for i, line in enumerate(lines):
        if re.match(rf"^\s*{key}\s*=", line):
            lines[i] = f"{key}={value}\n"
            found = True
    if not found:
        lines.append(f"\n{key}={value}\n")
    open(ENV, "w", encoding="utf-8").write("".join(lines))


_dotenv.save_dotenv_value = _save_dotenv_value
_dotenv.get_dotenv_value = lambda key, default=None: os.environ.get(key, default)
_helpers.plugins = _plugins
_helpers.dotenv = _dotenv

HERE = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, HERE)
import kame_engine as K  # noqa: E402
import kame_journal as J  # noqa: E402
import kame_settings as S  # noqa: E402
import kame_keys as KK  # noqa: E402
import kame_activation as KA  # noqa: E402

# The commands import `usr.plugins.api_rotation_by_kame.<module>`; point that
# name at the modules already loaded, so there is one engine in this process.
for pkg in ("usr", "usr.plugins", "usr.plugins.api_rotation_by_kame"):
    sys.modules.setdefault(pkg, types.ModuleType(pkg))
for name, mod in (("kame_engine", K), ("kame_journal", J), ("kame_settings", S),
                  ("kame_keys", KK), ("kame_activation", KA)):
    sys.modules[f"usr.plugins.api_rotation_by_kame.{name}"] = mod
    setattr(sys.modules["usr.plugins.api_rotation_by_kame"], name, mod)
K.apply_kame_patch = lambda: False  # activation re-applies settings only

K.set_log_level("silent")

_failures = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        _failures.append(name)


def command(script, args=""):
    module = runpy.run_path(os.path.join(HERE, "commands", script))
    result = module["run"]({"invocation": {"raw_arguments": args}, "context": {"agent": None}})
    effect = (result.get("effects") or [{}])[0]
    return effect.get("content") or effect.get("message") or "", effect.get("type"), effect.get("level")


# ==========================================================================
print("\n--- the one settings table ---")
cfg_src = open(os.path.join(HERE, "default_config.yaml"), encoding="utf-8").read()
page = open(os.path.join(HERE, "webui", "config.html"), encoding="utf-8").read()
missing_default = [n for n in S.SETTINGS if not re.search(rf"^{n}:", cfg_src, re.M)]
missing_page = [n for n in S.SETTINGS if n not in page]
check("every setting has a default in default_config.yaml", not missing_default, missing_default)
check("every setting is on the settings page", not missing_page, missing_page)
current = K.current_settings()
check("the engine reports every setting it applies",
      all(n in current for n in S.SETTINGS if n != "kame_unusable_response_limit"))
check("parse: a flag, a number in range, a choice",
      S.parse("spread_disabled", "on")[:2] == (True, True)
      and S.parse("max_hold_seconds", "1200")[:2] == (True, 1200)
      and S.parse("key_log_style", "prefix8")[:2] == (True, "prefix8"))
check("parse refuses junk, out of range, unknown",
      not S.parse("max_hold_seconds", "5")[0] and not S.parse("spread_disabled", "maybe")[0]
      and not S.parse("no_such", "1")[0])

# ==========================================================================
print("\n--- /kame ---")
text, kind, _ = command("kame_command.py", "help")
check("/kame help lists every command",
      all(c in text for c in ("/kame events", "/kame get", "/kame set", "/kame clear-pool",
                              "/kame-quota", "/kame-keys")))
text, kind, _ = command("kame_command.py", "get")
check("/kame get lists every setting", all(f"`{n}`" in text for n in S.SETTINGS))
KA.activate(None)  # the agent start that happens before anyone types a command
text, kind, level = command("kame_command.py", "set max_hold_seconds 1200")
check("/kame set saves and applies at once",
      CONFIG.get("max_hold_seconds") == 1200 and K._KAME_MAX_HOLD_S == 1200.0, (CONFIG, K._KAME_MAX_HOLD_S))
check("...and the change is on the timeline",
      any("max_hold_seconds" in r["reason"] for r in J.EVENTS.recent() if r["kind"] == "setting"))
text, kind, level = command("kame_command.py", "set max_hold_seconds 5")
check("/kame set refuses out of range", level == "error" and CONFIG.get("max_hold_seconds") == 1200)
os.environ["KAME_MAX_HOLD"] = "900"
text, _, _ = command("kame_command.py", "set max_hold_seconds 1800")
check("/kame set says when the environment still wins", "KAME_MAX_HOLD" in text)
del os.environ["KAME_MAX_HOLD"]
command("kame_command.py", "reset max_hold_seconds")
check("/kame reset <name> restores the default", CONFIG.get("max_hold_seconds") == 3600
      and K._KAME_MAX_HOLD_S == 3600.0)
command("kame_command.py", "set spread_disabled true")
command("kame_command.py", "reset all")
check("/kame reset all", CONFIG.get("spread_disabled") is False and not K._KAME_SPREAD_DISABLED)

ID = "google:gemini-3.7-flash"
KEYS = ["AIzaSyCMDTESTKEY-aaaaaa", "AIzaSyCMDTESTKEY-bbbbbb"]
K._get_identity_state(ID, KEYS)
e = Exception("429 Too Many Requests")
e.status_code = 429
K._kame_rest_for_failure(ID, KEYS[0], e)
K._kame_event("rotation", ID, KEYS[0], reason="throttled", code=429, seconds=30)
text, _, _ = command("kame_command.py", "events 5")
check("/kame events shows the decisions, fingerprints only",
      "throttled" in text and K._key_short_id(KEYS[0]) in text and KEYS[0] not in text)

# ==========================================================================
print("\n--- /kame-quota ---")
text, _, _ = command("kame_quota_command.py")
check("/kame-quota shows the resting key and why",
      K._key_short_id(KEYS[0]) in text and "resting" in text and KEYS[0] not in text)
text, _, _ = command("kame_quota_command.py", "reset")
check("/kame-quota reset clears the counts and leaves the rest",
      K._KAME_STATS["ok"] == 0 and not K._KAME_TALLY
      and K._KAME_KEY_HEALTH[ID]["keys"][KEYS[0]]["sick_until"] > time.time())
text, _, _ = command("kame_command.py", "clear-pool")
check("/kame clear-pool starts every key from zero",
      K._KAME_KEY_HEALTH[ID]["keys"][KEYS[0]]["sick_until"] == 0 and "from zero" in text)

# ==========================================================================
print("\n--- /kame-keys ---")
open(ENV, "w", encoding="utf-8").write("# my keys\nAPI_KEY_OPENAI=sk-existing-000000000000000000\nAPI_KEY_GOOGLE=" + KEYS[0] + "\n")
before = open(ENV, encoding="utf-8").read()
text, _, _ = command("kame_keys_command.py", "add " + KEYS[0] + "," + KEYS[1])
after = KK.read_env(__import__("pathlib").Path(ENV))
check("/kame-keys add merges into the provider's line by prefix",
      after["API_KEY_GOOGLE"] == KEYS[0] + "," + KEYS[1] and "Added 1" in text and "1 already there" in text,
      text)
check("...every other line untouched",
      after["API_KEY_OPENAI"] == "sk-existing-000000000000000000" and "# my keys" in open(ENV).read())
backups = [n for n in os.listdir(TMP) if n.startswith(".env.kame-")]
check("...a backup of the previous file first", len(backups) == 1
      and open(os.path.join(TMP, backups[0]), encoding="utf-8").read() == before)
check("...and no key in the message", all(k not in text for k in KEYS) and KK.mask(KEYS[1]) in text)
text, _, _ = command("kame_keys_command.py", "add sk-ambiguous-11111111111111111111")
check("/kame-keys add asks for a provider when the prefix does not say", "Which provider" in text)
text, _, _ = command("kame_keys_command.py", "add deepseek sk-ds-2222222222222222222222")
check("/kame-keys add <provider> writes API_KEY_<PROVIDER>",
      KK.read_env(__import__("pathlib").Path(ENV)).get("API_KEY_DEEPSEEK") == "sk-ds-2222222222222222222222")
keyfile = os.path.join(TMP, "keys.txt")
open(keyfile, "wb").write(b"\xef\xbb\xbfsk-ds-3333333333333333333333\nsk-ds-4444444444444444444444\n")
text, _, _ = command("kame_keys_command.py", f"import deepseek {keyfile}")
check("/kame-keys import reads a file with a BOM",
      KK.read_env(__import__("pathlib").Path(ENV))["API_KEY_DEEPSEEK"].count(",") == 2, text)
for _ in range(6):
    time.sleep(1.05)
    command("kame_keys_command.py", "add deepseek sk-ds-%d555555555555555555555" % _)
check("...only the last 5 backups are kept",
      len([n for n in os.listdir(TMP) if n.startswith(".env.kame-")]) == 5)
text, _, _ = command("kame_keys_command.py")
check("/kame-keys status lists providers, counts and masked keys",
      "google" in text and "deepseek" in text and all(k not in text for k in KEYS))
check("mask never returns the key", KK.mask(KEYS[0]) != KEYS[0] and len(KK.mask("short")) == 1)
check("split: commas, spaces, newlines, semicolons, pipes",
      KK.split_keys("a,b c\nd;e|f,a") == ["a", "b", "c", "d", "e", "f"])

print("=" * 60)
if _failures:
    print("FAILURES:", _failures)
    sys.exit(1)
print("ALL v1.8.1.0 COMMAND TESTS PASSED")

"""Every KAME setting, once — v1.8.1.0.

One table read by `/kame get|set|reset`, by the settings page's tests and by
the docs, so a setting added in one place and forgotten in another shows up as
a failing test rather than as a switch nobody can find. The Hermes port keeps
the same table in its `settings.py` (META, ALL_FLAGS, ALL_NUMBERS); the names
and the environment variables here are the same wherever the two hosts share a
setting.

Framework-free: no Agent Zero import. Parsing and validation live here; saving
lives in the command, which is the only side that can reach Agent Zero.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

# name: (type, default, low, high, choices, env, title, sentence)
#   type is "flag", "number", "choice" or "text" (1.8.2.7).
#
# 1.8.2.7: the first six are the ones the settings page shows to everybody
# (`VISIBLE`); the rest sit under "Show advanced settings", and the
# troubleshooting switches (`TROUBLESHOOTING`) in a folded card of their own.
SETTINGS: Dict[str, tuple] = {
    "fallback_models": (
        "text", "", None, None, None, "KAME_FALLBACK_MODELS",
        "Fallback models",
        "When your chat model cannot answer, KAME answers with the first of these "
        "that can, one provider/model per line. Empty: never switch."),
    "model_switch": (
        "choice", "auto", None, None, ("auto", "timed", "never"), "KAME_MODEL_SWITCH",
        "Fallback",
        "With fallback models set: one big pool where your best model always comes "
        "first. Every reply starts on your chat model and uses all of its keys; it "
        "goes to the next model of the list only when the provider says it is busy "
        "(two keys overloaded or timing out: at once), every key is resting, or "
        "nothing comes back in time. Automatic: about three times the model's usual "
        "first word, 6 to 20 s, learned. Set time: the seconds below. Never: always "
        "wait for your keys. A model KAME knows is busy is passed down without a "
        "request. Nothing switches after part of an answer is on screen."),
    "failover_after_seconds": (
        "number", 60, 0, 3600, None, "KAME_FAILOVER_AFTER",
        "Fallback after (seconds)",
        "Only with Fallback set to 'Set time': when a reply has waited this long "
        "with nothing on screen (keys resting, failing or slow), the next model of "
        "your list answers it. A busy provider moves at once, whatever this number."),
    "max_total_wait_seconds": (
        "number", 0, 0, 604800, None, "KAME_MAX_TOTAL_WAIT",
        "Give up after (seconds)",
        "0 (default): never give up; KAME waits for a key or uses your fallback "
        "list. A number makes it give up with an error after that long, which "
        "ends Agent Zero's task."),
    "hedge_mode": (
        "choice", "adaptive", None, None, ("off", "adaptive", "max"), "KAME_HEDGE_MODE",
        "Speed",
        "Saver: one key at a time. Balanced: a reply still silent after twice its "
        "usual wait races a backup key. Faster: the same a notch sooner, with "
        "three times the budget; it goes back to Balanced for 10 minutes when one "
        "of the model's keys hits a rate limit. The buttons show what each costs."),
    "hedge_max_legs": (
        "number", 3, 1, 3, None, "",
        "Most keys raced for one reply",
        "One call plus at most this many minus one backups."),
    "first_token_wait": (
        "choice", "auto", None, None, ("auto", "timed", "off"), "KAME_FIRST_TOKEN_WAIT",
        "Wait for the first token",
        "How long a key may stay silent before KAME asks the next one. Automatic "
        "learns each model's usual first-word time and drops a key only far past it "
        "(20 s at least, 60 s for thinking models); never for a model on this "
        "computer. Backup keys still race a slow reply much sooner."),
    "stream_silence_timeout_seconds": (
        "number", 0, 0, 3600, None, "KAME_STREAM_SILENCE_TIMEOUT",
        "First-token wait (seconds)",
        "Only with 'Set time': seconds a key may stay silent before the next one is tried "
        "(a quarter of it from the third try on; never for a local model)."),
    "reply_notice": (
        "choice", "always", None, None, ("always", "switches", "never"), "KAME_REPLY_NOTICE",
        "Show which model answered",
        "When a reply ends, one line in the chat log: its time and the model that "
        "answered each step - and which model a fallback answered for. Never part of "
        "what the model reads."),
    "stream_resume_limit": (
        "number", -1, -1, 10, None, "KAME_STREAM_RESUME_LIMIT",
        "Resume attempts per answer",
        "An answer cut mid-stream is continued on the next key from where it "
        "stopped, shown once. -1 continues while it makes progress; 0 starts the "
        "answer again instead."),
    "hedge_budget_pct": (
        "number", 10, 0, 50, None, "KAME_HEDGE_BUDGET_PCT",
        "Backup budget (% of calls)",
        "Roughly what share of calls may carry a backup request; it refills as "
        "normal calls succeed."),
    "hedge_burst": (
        "number", 3, 1, 10, None, "KAME_HEDGE_BURST",
        "Backup burst allowance",
        "How many backups can fire back to back after an idle period."),
    "hedge_quantile": (
        "number", 90, 50, 99, None, "KAME_HEDGE_QUANTILE",
        "Send backup when slower than usual",
        "For calls that do not stream: the percentile of learned times a call "
        "must exceed before a backup is sent."),
    "hedge_cold_s": (
        "number", 6, 0.5, 120, None, "KAME_HEDGE_COLD_S",
        "First backup while still learning",
        "Before KAME has learned a model's usual time, a call slower than this "
        "still gets a backup."),
    "hedge_cold_reasoning_s": (
        "number", 30, 2, 300, None, "KAME_HEDGE_COLD_REASONING_S",
        "First backup while learning (thinking)",
        "The same, for models that think before they answer."),
    "hedge_max_prompt_tokens": (
        "number", 32000, 1000, 1000000, None, "KAME_HEDGE_MAX_PROMPT_TOKENS",
        "Skip backups for very long prompts",
        "A backup of a huge prompt spends the token quota twice; above this "
        "size no backup and no keys asked together."),
    "pacing_rpm_overrides": (
        "text", "", None, None, None, "KAME_PACING_RPM_OVERRIDES",
        "Per-model requests-per-minute limits",
        "Tell the pacer what a provider really allows, e.g. "
        "'google:gemini/gemini-2.5-flash=10, groq:*=30'. KAME also learns it from "
        "real 429s; a learned number is never raised above this."),
    "key_region_overrides": (
        "text", "", None, None, None, "KAME_KEY_REGION_OVERRIDES",
        "Region per key",
        "For regional providers (Vertex, Bedrock) behind an endpoint that hides "
        "the region: 'k3f9a1=us-central1', the key as the panel shows it."),
    "probe_burst_max_legs": (
        "number", 16, 0, 32, None, "KAME_PROBE_BURST",
        "Keys asked together when quota runs dry",
        "After two long refusals in a row, ask 4, then 8, then up to this many "
        "keys at once instead of one by one. 0 = one by one."),
    "rotation_disabled": (
        "flag", False, None, None, None, "KAME_ROTATION_DISABLED",
        "Turn KAME off",
        "Every call goes to Agent Zero exactly as if KAME were not installed. "
        "The plugin stays installed and does nothing."),
    "spread_disabled": (
        "flag", False, None, None, None, "KAME_SPREAD_DISABLED",
        "Give back key selection",
        "Cooldowns stay, but the first ready key in the order you wrote them is "
        "used instead of the least-loaded one."),
    "carousel_disabled": (
        "flag", False, None, None, None, "KAME_CAROUSEL_DISABLED",
        "Stop rotating per call",
        "A refused key is still rested for as long as the evidence says, but the "
        "error goes back to Agent Zero instead of KAME trying the next key."),
    "daily_quota_cooldown_seconds": (
        "number", 3600, 1, 86400, None, "KAME_DAILY_COOLDOWN",
        "Daily quota cooldown",
        "How long a key rests once a daily or account quota is really spent."),
    "max_hold_seconds": (
        "number", 3600, 60, 86400, None, "KAME_MAX_HOLD",
        "Longest a key can be held",
        "No key sits out longer than this, whatever the provider claimed."),
    "unsized_throttle_rest_seconds": (
        "number", 30, 0, 300, None, "KAME_UNSIZED_REST",
        "Rest after a throttle with no number",
        "Measured: a refused key retried within 30s answered 0 of 73 times."),
    "unsized_throttle_backoff": (
        "flag", True, None, None, None, "KAME_UNSIZED_BACKOFF",
        "Short ladder for a bare RESOURCE_EXHAUSTED",
        "Gemini's bare 429 rests the key 1, 2, 4 ... 64s, back to 1s when it "
        "answers. A number the provider states is always obeyed."),
    "unsized_backoff_max_seconds": (
        "number", 64, 1, 3600, None, "KAME_UNSIZED_BACKOFF_MAX",
        "Where the ladder stops",
        "The longest rung of the ladder above."),
    "share_pool_health": (
        "flag", True, None, None, None, "KAME_SHARE_POOL_HEALTH",
        "Keep key health across restarts",
        "Holds are kept on disk (a hash of each key, never the key), so "
        "restarting Agent Zero does not spend calls on keys already known to be "
        "out. Never longer than the ceiling above."),
    "hedging_disabled": (
        "flag", False, None, None, None, "KAME_HEDGING_DISABLED",
        "Stop backups and keys asked together",
        "The same as speed boost 'off': one key at a time."),
    "stream_stitch_disabled": (
        "flag", False, None, None, None, "KAME_STREAM_STITCH_DISABLED",
        "Stop continuing cut answers",
        "An answer cut mid-stream starts again on the next key instead of being "
        "continued from where it stopped."),
    "refusal_recorder_disabled": (
        "flag", False, None, None, None, "KAME_RECORDER_DISABLED",
        "Stop recording refusals",
        "KAME writes each refusal to refusals.jsonl in usr/plugin-data, keys "
        "removed, and keeps the last ~8 MB (the file rotates). It decides nothing "
        "from it."),
    "call_timings_disabled": (
        "flag", False, None, None, None, "KAME_CALL_TIMINGS_DISABLED",
        "Stop timing calls",
        "One line per attempt to calls.jsonl: durations, outcome, a key "
        "fingerprint. No key, no prompt, no answer. Keeps the last ~4 MB (the "
        "file rotates)."),
    "kame_log_level": (
        "choice", "normal", None, None, ("silent", "normal", "verbose", "verbose+errors"), "",
        "Log verbosity",
        "How much KAME writes to the Agent Zero console."),
    "key_log_style": (
        "choice", "fingerprint", None, None, ("fingerprint", "prefix8"), "",
        "How keys appear in logs",
        "A fingerprint never leaks; prefix8 shows the first 8 characters."),
    "kame_log_full_errors": (
        "flag", False, None, None, None, "",
        "Show the full raw error",
        "Every failure also logs the provider's untruncated message."),
    "kame_collapse_storm_logs": (
        "flag", True, None, None, None, "KAME_STORM_COLLAPSE_DISABLED (inverted)",
        "Collapse outage logs",
        "During an outage, repeats become one line every ~20s."),
    "kame_wait_notice": (
        "flag", True, None, None, None, "KAME_LIVE_STATUS_DISABLED (inverted)",
        "Say on the status line what KAME waits for",
        "The chat's own status line ('A0: Calling LLM...') says which model or key KAME waits "
        "for and when the next one is back. Off: the line stays as Agent Zero writes it."),
    "kame_unusable_response_limit": (
        "number", 5, 0, 100, None, "",
        "Floor for Agent Zero's unusable-response limit",
        "Agent Zero stops a turn after this many unparseable answers in a row; "
        "KAME raises its limit to at least this. 0 leaves it alone."),
}

#: 1.8.2.9 (U4): the shelves of the settings page, ``(id, title, note, keys)``, in the order they
#: are shown. Hermes' five, with its titles and notes word for word, and one more after "Rest
#: times" for what exists only in Agent Zero. Hermes' ``never_fall_back_to_another_model`` has no
#: twin here: in Agent Zero the list is KAME's, and "Never" already means it.
GROUPS = (
    ("extra", "If your model cannot answer",
     "When your provider is busy, every key of your model is resting, or a reply takes too long.",
     ("model_switch", "failover_after_seconds", "reply_notice", "max_total_wait_seconds")),
    ("speed", "Speed",
     "How KAME keeps answers fast. Already tuned; the first two are the ones worth knowing.",
     ("hedge_mode", "first_token_wait", "stream_silence_timeout_seconds", "hedge_max_legs",
      "probe_burst_max_legs", "hedge_budget_pct", "hedge_burst", "hedge_quantile", "hedge_cold_s",
      "hedge_cold_reasoning_s", "hedge_max_prompt_tokens")),
    ("keyring", "Your API keys",
     "One key per account (for Gemini, one per Google project): every key brings its own quota, "
     "and KAME rotates between them.",
     ("share_pool_health", "pacing_rpm_overrides", "key_region_overrides")),
    ("tuning", "Rest times (advanced)",
     "How long a refused key sits out. The provider's own numbers always win; these are the "
     "fallbacks and ceilings.",
     ("daily_quota_cooldown_seconds", "max_hold_seconds", "unsized_throttle_backoff",
      "unsized_throttle_rest_seconds", "unsized_backoff_max_seconds", "stream_resume_limit")),
    ("agentzero", "Agent Zero log and chat",
     "How KAME talks in Agent Zero's chat line and log. These exist only in the Agent Zero version.",
     ("kame_wait_notice", "kame_log_level", "key_log_style", "kame_log_full_errors",
      "kame_collapse_storm_logs", "kame_unusable_response_limit")),
    ("off", "Troubleshooting: turn parts of KAME off",
     "Each switch turns one part of KAME off and gives that job back to Agent Zero. For finding "
     "out whether KAME is behind a problem. Leave them alone unless something is wrong.",
     ("rotation_disabled", "spread_disabled", "carousel_disabled", "hedging_disabled",
      "stream_stitch_disabled", "refusal_recorder_disabled", "call_timings_disabled")),
)

_GROUP_OF = {key: group for group, _title, _note, keys in GROUPS for key in keys}
_GROUP_OF["fallback_models"] = "fallback"        # edited in its own tab, not on this shelf list
_UNGROUPED = "tuning"                            # as in Hermes: a wrong shelf is a small confusion


def group_of(name: str) -> str:
    """Which shelf one setting belongs on."""
    return _GROUP_OF.get(name, _UNGROUPED)


def groups() -> tuple:
    """The shelves for a panel that has to title and explain each one."""
    return tuple({"id": g, "title": t, "note": n, "keys": list(k), "folded": g == "off",
                  "shows_fallback": g == "extra", "shows_keys": g == "keyring"}
                 for g, t, n, k in GROUPS)


#: 1.8.2.9: on the settings page for everybody - the first two of each shelf, the way Hermes
#: splits basic from advanced. The rest is under "Show advanced settings"; a changed one is
#: never hidden. The fallback list has its own tab.
VISIBLE = tuple(key for _g, _t, _n, keys in GROUPS if _g != "off" for key in keys[:2])
#: 1.8.2.8: what each choice is called on screen, in the order it is shown.
CHOICE_LABELS = {
    "hedge_mode": (("off", "Saver"), ("adaptive", "Balanced"), ("max", "Faster")),
    "model_switch": (("auto", "Automatic"), ("timed", "Set time"), ("never", "Never")),
    "first_token_wait": (("auto", "Automatic"), ("timed", "Set time"), ("off", "Off")),
    "reply_notice": (("always", "Every reply"), ("switches", "Only another model"), ("never", "Never")),
}
#: 1.8.2.8: a number shown only under one answer of a choice.
DEPENDS_ON = {
    "failover_after_seconds": ("model_switch", "timed"),
    "stream_silence_timeout_seconds": ("first_token_wait", "timed"),
}
#: Switches that turn a part of KAME off: a folded card of their own.
TROUBLESHOOTING = ("rotation_disabled", "spread_disabled", "carousel_disabled",
                   "hedging_disabled", "stream_stitch_disabled",
                   "refusal_recorder_disabled", "call_timings_disabled")

#: 1.8.2.8: the number each choice replaced, its default, and what 0 meant.
LEGACY_NUMBER = {
    "model_switch": ("failover_after_seconds", 60.0, "never"),
    "first_token_wait": ("stream_silence_timeout_seconds", 0.0, "off"),
}


#: 1.8.2.9: the speed words are Hermes' (settings.py `choice_labels_now`, `choice_help_now`,
#: `CHOICE_FOOTNOTE`), text for text; none of the sentences names the host.
HEDGE_MODE = "hedge_mode"
_HEDGE_BUDGET_PCT = "hedge_budget_pct"


def _hedging():
    """The speed module (its live numbers, its formula), or None when it is missing."""
    for qualified in ("usr.plugins.api_rotation_by_kame.kame_hedging", "kame_hedging"):
        try:
            import importlib

            return importlib.import_module(qualified)
        except Exception:
            continue
    return None


def live_budget_pct() -> float:
    """The backup budget in force now (the engine sets it in ``kame_hedging``), else the default."""
    module = _hedging()
    try:
        return float(module.SETTINGS[_HEDGE_BUDGET_PCT])
    except Exception:
        return float(default(_HEDGE_BUDGET_PCT))


def speed_multipliers(pct: Optional[float] = None) -> Tuple[float, float]:
    """``(Balanced, Faster)``: the most requests per reply each mode may use over a day.

    Balanced ``1 + pct/100``, Faster ``1 + min(100, 3 x pct)/100``; Saver is always 1x. ``pct`` is
    the backup budget (``hedge_budget_pct``); omitted, the one in force now.
    """
    pct = live_budget_pct() if pct is None else pct
    module = _hedging()
    if module is not None:
        return module.speed_multipliers(pct)
    pct = float(pct)
    return 1.0 + pct / 100.0, 1.0 + min(100.0, 3.0 * pct) / 100.0


def _times(value: float) -> str:
    """``1.1`` -> ``1.1x``; ``2.0`` -> ``2x``."""
    text = f"{value:.1f}"
    return (text[:-2] if text.endswith(".0") else text) + "x"


def choice_labels_now(name: str, pct: Optional[float] = None) -> Dict[str, str]:
    """The words each choice is shown with, by stored value. Speed carries what it costs."""
    labels = dict(CHOICE_LABELS.get(name, ()))
    if name == HEDGE_MODE:
        balanced, faster = speed_multipliers(pct)
        labels = {
            "off": "Saver · 1x",
            "adaptive": f"Balanced · up to {_times(balanced)}",
            "max": f"Faster · up to {_times(faster)}",
        }
    return labels


def choice_help_now(name: str, pct: Optional[float] = None) -> Dict[str, str]:
    """One plain sentence per choice, shown under the buttons for the chosen one."""
    if name != HEDGE_MODE:
        return {}
    balanced, faster = speed_multipliers(pct)
    b_pct = round((balanced - 1.0) * 100)
    f_pct = round((faster - 1.0) * 100)
    return {
        "off": "One request per reply, never more: the cheapest, and a slow reply simply waits.",
        "adaptive": ("A second key races only a reply that is slower than twice its usual time, "
                     f"so over a day it uses at most about {b_pct}% more requests."),
        "max": ("Races a little sooner and more often, at most about "
                f"{f_pct}% more requests, and goes back to Balanced by itself for 10 minutes "
                "whenever one of that model's keys hits a rate limit."),
    }


#: 1.8.2.9. One line under the speed buttons (not for Saver): what the percentages leave out.
CHOICE_FOOTNOTE: Dict[str, str] = {
    HEDGE_MODE: ("Balanced and Faster may also race your next model when a reply is already about 3x "
                 "slower than usual, and ask several keys at once when a model runs out of quota - "
                 "the counter below shows what was really spent."),
}


def choice_from_config(cfg: dict, name: str) -> str:
    """The 1.8.2.8 choice a saved configuration means.

    Saved by 1.8.2.8: the choice itself. Saved before it existed: a number left
    at its default is ``auto``, 0 is ``never`` / ``off``, any other number is
    ``timed`` - so a person who chose a time keeps it.
    """
    cfg = cfg or {}
    value = str(cfg.get(name) or "").strip().lower()
    if value in (SETTINGS[name][4] or ()):
        return value
    number, number_default, zero = LEGACY_NUMBER[name]
    try:
        n = float(cfg.get(number, number_default))
    except (TypeError, ValueError):
        n = number_default
    if n == number_default:
        return "auto"
    return zero if n <= 0 else "timed"


_TRUE = ("1", "true", "on", "yes")
_FALSE = ("0", "false", "off", "no")


def parse(name: str, raw: Any) -> Tuple[bool, Any, str]:
    """``(ok, value, message)`` for one setting and one typed value."""
    row = SETTINGS.get(name)
    if row is None:
        return False, None, f"`{name}` is not a KAME setting. `/kame get` lists them."
    kind, _default, low, high, choices = row[0], row[1], row[2], row[3], row[4]
    text = str(raw).strip().lower()
    if kind == "flag":
        if text in _TRUE:
            return True, True, ""
        if text in _FALSE:
            return True, False, ""
        return False, None, f"`{name}` is on or off: true / false."
    if kind == "text":
        value = str(raw if raw is not None else "").strip()
        if len(value) > 4000:
            return False, None, f"`{name}` is at most 4000 characters."
        return True, value, ""
    if kind == "choice":
        if text in choices:
            return True, text, ""
        return False, None, f"`{name}` is one of: {', '.join(choices)}."
    try:
        value = float(text)
    except ValueError:
        return False, None, f"`{name}` is a number."
    if value != value or value < low or value > high:
        return False, None, f"`{name}` goes from {low:g} to {high:g}."
    return True, int(value) if value.is_integer() else value, ""


def default(name: str) -> Any:
    row = SETTINGS.get(name)
    return None if row is None else row[1]


def env_name(name: str) -> str:
    row = SETTINGS.get(name)
    return "" if row is None else row[5]


def env_override(name: str, environ: Optional[dict] = None) -> str:
    """The environment variable currently overriding this setting, if any."""
    import os

    environ = os.environ if environ is None else environ
    var = env_name(name).split(" ")[0]
    if var and str(environ.get(var, "")).strip():
        return var
    return ""

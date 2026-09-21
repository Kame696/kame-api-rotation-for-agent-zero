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
#   type is "flag", "number" or "choice".
SETTINGS: Dict[str, tuple] = {
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
    "refusal_recorder_disabled": (
        "flag", False, None, None, None, "KAME_RECORDER_DISABLED",
        "Stop recording refusals",
        "KAME writes each refusal to refusals.jsonl in usr/plugin-data, keys "
        "removed, 8 MB at most. It decides nothing from it."),
    "call_timings_disabled": (
        "flag", False, None, None, None, "KAME_CALL_TIMINGS_DISABLED",
        "Stop timing calls",
        "One line per attempt to calls.jsonl: durations, outcome, a key "
        "fingerprint. No key, no prompt, no answer. 4 MB at most."),
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
        "Say so in the chat when every key rests",
        "A live notification at ~15s and a chat line at ~90s."),
    "kame_unusable_response_limit": (
        "number", 5, 0, 100, None, "",
        "Floor for Agent Zero's unusable-response limit",
        "Agent Zero stops a turn after this many unparseable answers in a row; "
        "KAME raises its limit to at least this. 0 leaves it alone."),
}

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

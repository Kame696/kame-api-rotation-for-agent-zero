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
# 1.8.3.0 (A6): title, sentence, order, ranges, defaults and environment names are the Hermes ones word for word
# (`tests/fixtures/hermes_1829_ui_strings.json`, made from the Hermes `describe_all()`); only the host's name and the
# clauses that name a Hermes-only thing are different, and `tests/test_v1_8_3_0_settings_words.py` lists each one.
SETTINGS: Dict[str, tuple] = {
    "fallback_models": (
        "text", "", None, None, None, "KAME_FALLBACK_MODELS",
        "Fallback models",
        (
            "When your chat model cannot answer, KAME answers with the first of these that can, one "
            "provider/model per line. Empty: never switch.")),
    "model_switch": (
        "choice", "auto", None, None, ("auto", "timed", "never"), "KAME_MODEL_SWITCH",
        "Fallback",
        (
            "Uses the fallback models in the Fallback models tab, as one big pool where your best model "
            "always comes first. Every reply starts on your best model and uses all of its keys. The "
            "reply goes to the next model of your list only when that model cannot answer: the provider "
            "says it is busy (two keys answer 'overloaded' or time out: at once, whatever the setting), "
            "every key is resting, or nothing has come back in time. Automatic: in time is about three "
            "times the model's usual first word, 6 to 20 s, learned on this computer. Set time: the "
            "seconds below. Never: always wait for this model's keys. The next reply starts on your best "
            "model again; one KAME knows is still busy or resting is passed down without spending a "
            "request. Nothing switches after part of an answer is on screen.")),
    "failover_after_seconds": (
        "number", 60, 0, 3600, None, "KAME_FAILOVER_AFTER",
        "Fallback after (seconds)",
        (
            "Only with Fallback set to 'Set time': when a reply has waited this long with nothing on "
            "screen (keys resting, failing or slow), it goes to the next model of your list. A busy "
            "provider goes at once, whatever this number.")),
    "reply_notice": (
        "choice", "always", None, None, ("always", "switches", "never"), "KAME_REPLY_NOTICE",
        "Show which model answered",
        (
            "After a reply, a short notice says the model that wrote it and how long it took, and when a "
            "model from your fallback list answered. Never part of the conversation the model reads.")),
    "max_total_wait_seconds": (
        "number", 0, 0, 604800, None, "KAME_MAX_TOTAL_WAIT",
        "Give up after (seconds)",
        (
            "Default: a chat never gives up, it waits for a key or uses your fallback list. A number here"
            " makes it give up with an error after that long, which ends Agent Zero's task; 0 never gives"
            " up.")),
    "never_fall_back_to_another_model": (
        "flag", False, None, None, None, "KAME_NO_MODEL_FALLBACK",
        "Ignore my fallback list",
        (
            "Off (default): the fallback models in the Fallback models tab take over when your keys are "
            "out; on: always wait for your keys.")),
    "hedge_mode": (
        "choice", "adaptive", None, None, ("off", "adaptive", "max"), "KAME_HEDGE_MODE",
        "Speed",
        (
            "How soon KAME races a second key when a reply is late. The line under the buttons says what "
            "the chosen mode costs in extra requests.")),
    "first_token_wait": (
        "choice", "auto", None, None, ("auto", "timed", "off"), "KAME_FIRST_TOKEN_WAIT",
        "Wait for the first token",
        (
            "How long a key may stay silent before KAME drops it and asks the next one. Automatic learns "
            "each model's usual first-word time and drops a key only when it is far past it (20 s at "
            "least, 60 s for thinking models); never for a model on this computer. Backup keys still race"
            " a slow reply much sooner. Set time: the seconds below. Off: 120 s.")),
    "stream_silence_timeout_seconds": (
        "number", 0, 0, 3600, None, "KAME_STREAM_SILENCE_TIMEOUT",
        "First-token wait (seconds)",
        (
            "Only with 'Wait for the first token' set to 'Set time': how many seconds a key may stay "
            "silent before KAME asks the next one; 60 suits most providers. 0 (default) leaves the wait at 120 s.")),
    "hedge_max_legs": (
        "number", 3, 1, 3, None, "KAME_HEDGE_MAX_LEGS",
        "Most parallel requests per call",
        (
            "1 turns backup requests off. 2 allows one backup per call. 3 lets a chat reply that is still"
            " silent after the first backup try a third key (calls that do not stream use one backup at "
            "most).")),
    "probe_burst_max_legs": (
        "number", 16, 0, 32, None, "KAME_PROBE_BURST",
        "Keys asked at once when quota runs out",
        (
            "When two keys in a row are refused because their quota is spent, asking the rest one at a "
            "time costs almost a second each. KAME then asks 4 keys at once, then 8, then 16, and the "
            "first that answers wins; in a chat the others are cut (a call that does not stream asks at "
            "most 4 at once, and those finish on their own). Keys the provider itself said are still "
            "spent are asked all at once, and only they. A key that still had quota and lost the race may"
            " have spent one request. 0 or 1 asks one key at a time. Off when backup requests are off; "
            "never for background jobs.")),
    "hedge_budget_pct": (
        "number", 10, 0, 50, None, "KAME_HEDGE_BUDGET_PCT",
        "Backup budget (% of calls)",
        (
            "Roughly what share of calls may carry a backup request. The budget refills as normal calls "
            "succeed, so a slow afternoon hedges a few times and then pauses until it is earned back.")),
    "hedge_burst": (
        "number", 3, 1, 10, None, "KAME_HEDGE_BURST",
        "Backup burst allowance",
        (
            "How many backups can fire back-to-back after an idle period, before the budget above has "
            "been earned back.")),
    "hedge_quantile": (
        "number", 90, 50, 99, None, "KAME_HEDGE_QUANTILE",
        "Send backup when slower than usual",
        (
            "For calls that do not stream: the percentile of learned response times a call must exceed "
            "before a backup is sent. 90 means 'slower than 9 calls in 10'. The chat uses its own "
            "trigger: twice the model's usual time to the first word.")),
    "hedge_cold_s": (
        "number", 6, 0.5, 120, None, "KAME_HEDGE_COLD_S",
        "First backup while still learning",
        (
            "Before KAME has learned how fast this model usually answers, a call slower than this still "
            "gets a backup. Keep it well above the slowest honest first token so a merely normal call is "
            "never doubled.")),
    "hedge_cold_reasoning_s": (
        "number", 30, 2, 300, None, "KAME_HEDGE_COLD_REASONING_S",
        "First backup while learning (thinking)",
        (
            "The same threshold, separate for models that think before they answer — a long thinking "
            "phase is normal there and must not trigger a backup.")),
    "hedge_max_prompt_tokens": (
        "number", 32000, 1000, 1000000, None, "KAME_HEDGE_MAX_PROMPT_TOKENS",
        "Skip backups for very long prompts",
        (
            "A backup of a huge prompt spends your token quota twice and never hits a warm cache, so "
            "above this size backups are skipped.")),
    "share_pool_health": (
        "flag", True, None, None, None, "KAME_SHARE_POOL_HEALTH",
        "Keep key health across restarts",
        (
            "On: holds are kept on disk (a hash of each key, never the key), so restarting Agent Zero "
            "does not spend calls on keys already known to be out.")),
    "pacing_rpm_overrides": (
        "text", "", None, None, None, "KAME_PACING_RPM_OVERRIDES",
        "Per-model requests-per-minute limits",
        (
            "Tell the pacer what a provider really allows, e.g. 'google:gemini/gemini-2.5-flash=10, "
            "groq:*=30'. It is the most KAME will assume; a real per-minute refusal can still lower it, "
            "and nothing learned can raise it above this. Leave empty to let it learn from real answers "
            "and refusals.")),
    "key_region_overrides": (
        "text", "", None, None, None, "KAME_KEY_REGION_OVERRIDES",
        "Region per key",
        (
            "For regional providers (Vertex, Bedrock), name a key's region, e.g. 'key:a1b2c3=us-central1'"
            " — the key as the Events tab shows it (the six characters alone also work). It sets the "
            "region of the whole model on that endpoint, so refusals there do not rest the same model "
            "elsewhere. Auto-detected from the endpoint URL when left empty.")),
    "daily_quota_cooldown_seconds": (
        "number", 3600, 1, 86400, None, "KAME_DAILY_COOLDOWN",
        "Daily quota cooldown",
        (
            "How long a key rests after a daily or account-level refusal — a quota that is genuinely "
            "spent. The provider's own retry hint is ignored for these on purpose: it routinely says a "
            "minute when the truth is midnight. It does not govern a key the provider *refused* (a 401, a"
            " revoked key): that rests twenty seconds and is then offered last, because waiting is not "
            "what repairs one — and a key the provider names dead leaves rotation instead of resting.")),
    "max_hold_seconds": (
        "number", 3600, 60, 86400, None, "KAME_MAX_HOLD",
        "Ceiling on any hold",
        (
            "No credential sits out longer than this, whatever set the rest — a provider's own stated "
            "wait, the daily cooldown above, or this plugin's own escalation. Past it the key returns to "
            "selection and is probed; a refusal that repeats is held again, so a genuinely long outage "
            "costs about one refused request per interval instead of a healthy key sitting out for "
            "however long a provider claimed.")),
    "unsized_throttle_backoff": (
        "flag", True, None, None, None, "KAME_UNSIZED_BACKOFF",
        "Short ladder: bare RESOURCE_EXHAUSTED",
        (
            "Applies to one error only: an HTTP 429 whose structured status is RESOURCE_EXHAUSTED and "
            "that carries no retry number and no quotaId (Gemini's bare refusal). On, that key rests 1s, "
            "then 2, 4, 8, 16, 32... on each consecutive repeat on the same key and model, up to the cap "
            "below, instead of the flat rest. Every other throttle keeps the flat rest. It resets to 1s "
            "the moment that key answers, or when a refusal carries a number the provider stated; a "
            "stated number is never multiplied. In real use it made fewer calls per minute than the flat "
            "rest (24 against 29) without answering less. Off restores the flat rest exactly.")),
    "unsized_throttle_rest_seconds": (
        "number", 30, 0, 300, None, "KAME_UNSIZED_REST",
        "Rest after an unsized throttle",
        (
            "How long a credential rests after a per-minute or rate-limit refusal that never once named a"
            " number - no retry hint, no window. Measured on real traffic: a refused key retried within "
            "30s answered 0 of 73 times, and over the whole recorded corpus 30s avoids 388 of 413 "
            "avoidable refused calls while delaying 6 answers - the best return of any rest tried. Set to"
            " 0 to offer the credential again immediately instead. Clamped to 0-300; past 300 this is no "
            "longer an unsized throttle's business.")),
    "unsized_backoff_max_seconds": (
        "number", 64, 1, 3600, None, "KAME_UNSIZED_BACKOFF_MAX",
        "Where the ladder stops",
        (
            "The longest rest the bare RESOURCE_EXHAUSTED ladder above will reach. The default, 64, runs "
            "1, 2, 4, 8, 16, 32, 64 seconds and then holds at 64: the fastest measured recovery for this "
            "error is 23-34s, so going far past 64 only keeps a key out longer than it needed. Set it to "
            "3600 for the uncapped doubling (1, 2, 4 ... up to an hour). The ceiling on any hold still "
            "bounds it. Clamped to 1-3600.")),
    "stream_resume_limit": (
        "number", -1, -1, 10, None, "KAME_STREAM_RESUME_LIMIT",
        "Resume attempts per turn",
        (
            "Minus one (the default) continues while the answer makes progress; no-progress repetition "
            "still stops on evidence. Zero switches stitching off entirely. Explicit ceilings from one to"
            " ten retain their operator-selected limit.")),
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
    "kame_unusable_response_limit": (
        "number", 5, 0, 100, None, "",
        "Floor for Agent Zero's unusable-response limit",
        (
            "Agent Zero stops a turn after this many unparseable answers in a row; KAME raises its limit "
            "to at least this. 0 leaves it alone.")),
    "rotation_disabled": (
        "flag", False, None, None, None, "KAME_ROTATION_DISABLED",
        "Turn KAME off",
        (
            "Every call keeps the key Agent Zero resolved and failures follow Agent Zero's own retry "
            "rules. The plugin stays installed and does nothing.")),
    "spread_disabled": (
        "flag", False, None, None, None, "KAME_SPREAD_DISABLED",
        "Give back key selection",
        (
            "Cooldown sizing stays, but each call carries the first healthy key in the pool's order (the "
            "order you wrote them) instead of the least-loaded one.")),
    "carousel_disabled": (
        "flag", False, None, None, None, "KAME_CAROUSEL_DISABLED",
        "Stop rotating per call",
        (
            "Agent Zero's own key, retry ceiling and rotation rules come back. Cooldown sizing stays. "
            "This is the switch that stops a failed call trying the next key.")),
    "storm_collapse_disabled": (
        "flag", False, None, None, None, "KAME_STORM_COLLAPSE_DISABLED",
        "Log every failure during an outage",
        (
            "Repeated identical failures are written in full instead of being collapsed into a periodic "
            "count. Louder logs, same rotation.")),
    "live_status_disabled": (
        "flag", False, None, None, None, "KAME_LIVE_STATUS_DISABLED",
        "Hide the status line",
        (
            "Pool health and the recovery countdown stop appearing on the chat's status line. Rotation is"
            " unchanged.")),
    "stream_stitch_disabled": (
        "flag", False, None, None, None, "KAME_STREAM_STITCH_DISABLED",
        "Stop continuing cut answers",
        (
            "An answer cut off mid-stream is handed back to Agent Zero, which starts it again, instead of"
            " KAME continuing it on another key.")),
    "refusal_recorder_disabled": (
        "flag", False, None, None, None, "KAME_RECORDER_DISABLED",
        "Stop recording refusals",
        (
            "KAME writes each refusal a provider sends to refusals.jsonl beside its own state — status, "
            "message and body, with API keys removed first — and decides nothing from it. It is there "
            "because the field that tells a per-minute quota from a per-day one could not be read before "
            "1.7.0.0, so it appears in no older log and no rule about it can be checked against real "
            "traffic. Writes only, never interrupts a call, and keeps the last ~8 MB (the file rotates). "
            "Turn this on to stop the writing.")),
    "call_timings_disabled": (
        "flag", False, None, None, None, "KAME_CALL_TIMINGS_DISABLED",
        "Stop timing calls",
        (
            "KAME writes one line per attempt to calls.jsonl beside its own state: how long until "
            "anything came back, how long until the answer started, how long the attempt took in total, "
            "and how much of the turn had already been spent waiting for a key. It exists so “is it slow,"
            " and whose fault is it” can be answered with numbers rather than from memory — a turn that "
            "took two minutes is a quota problem or a slow model, and those look identical from outside. "
            "No key, no prompt and no answer text is written; only durations, a model name and the same "
            "key fingerprint the panel already shows. Writes only, never interrupts a call, and keeps the"
            " last ~4 MB (the file rotates). Turn this on to stop the writing.")),
    "hedging_disabled": (
        "flag", False, None, None, None, "KAME_HEDGING_DISABLED",
        "Stop adaptive hedging",
        (
            "When hedging is active and a reply is slower than usual, KAME asks a second key (and, if "
            "that is slow too, a third) and uses whichever answers first. In the chat the race is decided"
            " by the first word: the moment one key starts answering, the others are cut. Each backup is "
            "one extra request on a key of another project, held to a budget (about 10% of calls) and "
            "stopped by itself when rate limits appear. Turn this on to stop hedging everywhere.")),
}

#: 1.8.3.0 (A6): how much one setting changes what a person notices (Hermes `IMPACT`): high = every conversation,
#: medium = one situation, low = fine-tuning, escape = takes a job away from KAME, for diagnosis only.
IMPACT: Dict[str, str] = {
    "fallback_models": "high",
    "model_switch": "high",
    "failover_after_seconds": "medium",
    "reply_notice": "medium",
    "max_total_wait_seconds": "high",
    "never_fall_back_to_another_model": "low",
    "hedge_mode": "high",
    "first_token_wait": "medium",
    "stream_silence_timeout_seconds": "low",
    "hedge_max_legs": "medium",
    "probe_burst_max_legs": "low",
    "hedge_budget_pct": "medium",
    "hedge_burst": "low",
    "hedge_quantile": "low",
    "hedge_cold_s": "low",
    "hedge_cold_reasoning_s": "low",
    "hedge_max_prompt_tokens": "low",
    "share_pool_health": "medium",
    "pacing_rpm_overrides": "low",
    "key_region_overrides": "low",
    "daily_quota_cooldown_seconds": "medium",
    "max_hold_seconds": "medium",
    "unsized_throttle_backoff": "medium",
    "unsized_throttle_rest_seconds": "low",
    "unsized_backoff_max_seconds": "low",
    "stream_resume_limit": "low",
    "kame_log_level": "low",
    "key_log_style": "low",
    "kame_log_full_errors": "low",
    "kame_unusable_response_limit": "low",
    "rotation_disabled": "escape",
    "spread_disabled": "escape",
    "carousel_disabled": "escape",
    "storm_collapse_disabled": "escape",
    "live_status_disabled": "escape",
    "stream_stitch_disabled": "escape",
    "refusal_recorder_disabled": "escape",
    "call_timings_disabled": "escape",
    "hedging_disabled": "escape",
}

#: 1.8.3.0 (A6): one sentence per setting saying WHEN to change it (Hermes `_WHEN`). The help says what a setting does.
WHEN: Dict[str, str] = {
    "fallback_models": (
        "Add the models you would accept when yours is busy or out of quota. Put your best second "
        "choice first."),
    "model_switch": "Leave on Automatic. Never if you would rather always wait for the model you picked.",
    "failover_after_seconds": "Only with 'Set time'. Lower to fall back sooner, raise to stay on your best model longer.",
    "reply_notice": "Only another model, if a notice after every reply is too much.",
    "max_total_wait_seconds": "Change only if you would rather get an error than wait for your keys to come back.",
    "never_fall_back_to_another_model": "Only if you keep a fallback list but want KAME to wait for your keys anyway.",
    "hedge_mode": (
        "Leave on Balanced. Saver if every extra request costs you money; Faster if speed matters "
        "more than a few extra requests."),
    "first_token_wait": (
        "Leave on Automatic. Set time only if a provider hangs in silence and Automatic is too "
        "patient for you."),
    "stream_silence_timeout_seconds": "Only with 'Set time'. 60 is a safe number for most providers.",
    "hedge_max_legs": "Lower to 2 to cap a slow call at one backup.",
    "probe_burst_max_legs": "Rarely. Lower it if every request costs you money; 0 asks keys one by one.",
    "hedge_budget_pct": "Lower it to spend fewer extra requests on backups; raise it for faster worst cases.",
    "hedge_burst": "Rarely. How many backups may go out back to back.",
    "hedge_quantile": "Rarely. Lower = backups sooner.",
    "hedge_cold_s": "Rarely. Used only until KAME has learned a model's normal speed.",
    "hedge_cold_reasoning_s": "Rarely. Same as above, for thinking models.",
    "hedge_max_prompt_tokens": "Rarely. Very long prompts are not duplicated, to save quota.",
    "share_pool_health": "Keep on, so a restart does not spend calls on keys already known to be out.",
    "pacing_rpm_overrides": "Only if you know a model's per-minute limit and KAME keeps hitting it.",
    "key_region_overrides": "Only if one key must be treated as a different region (Vertex, Bedrock).",
    "daily_quota_cooldown_seconds": "Only if your provider's daily quota resets at a known time KAME cannot read.",
    "max_hold_seconds": "The longest any key is ever benched. Lower it to retry spent keys sooner.",
    "unsized_throttle_backoff": "Keep on. It retries a bare 'rate limit' after 1, 2, 4… seconds instead of a fixed wait.",
    "unsized_throttle_rest_seconds": "Rarely. The wait for a rate limit that states no time, when the ladder is off.",
    "unsized_backoff_max_seconds": "Rarely. Where the 1, 2, 4… ladder stops.",
    "stream_resume_limit": "Rarely. Caps how often one cut answer is continued.",
    "kame_log_level": "Only when you are looking for a problem in Agent Zero's console.",
    "key_log_style": "Rarely. Leave it on fingerprint unless you must match a key against your own records.",
    "kame_log_full_errors": "Only when you are looking for a problem.",
    "kame_unusable_response_limit": "Rarely. Raise it if a model that sometimes answers badly keeps ending your tasks.",
    "rotation_disabled": "Only to check whether KAME is behind a problem. Everything goes back to plain Agent Zero.",
    "spread_disabled": "Only to check whether this part of KAME is behind a problem.",
    "carousel_disabled": "Only to check whether this part of KAME is behind a problem.",
    "storm_collapse_disabled": "Only to check whether this part of KAME is behind a problem.",
    "live_status_disabled": "Only to check whether this part of KAME is behind a problem.",
    "stream_stitch_disabled": "Only to check whether this part of KAME is behind a problem.",
    "refusal_recorder_disabled": "Only to check whether this part of KAME is behind a problem.",
    "call_timings_disabled": "Only to check whether this part of KAME is behind a problem.",
    "hedging_disabled": "Only to check whether this part of KAME is behind a problem.",
}

#: 1.8.3.0 (A6): the long text of a setting, shown as the tooltip over its sentence (Hermes `_DETAIL`);
#: a setting without one shows its sentence.
DETAIL: Dict[str, str] = {
    "failover_after_seconds": (
        "Works only when you have set fallback models in the Fallback models tab; with no list "
        "nothing ever switches. When every key of the model you picked is resting and the soonest one"
        " is more than this many seconds away, KAME stops waiting and the next model in your list "
        "answers. Agent Zero's own model setting is not touched: the next reply starts on the model "
        "you picked again. Short rests (a minute of rate limit) are waited out on the model you "
        "picked. 0 means never switch. KAME itself never chooses a model."),
    "max_total_wait_seconds": (
        "Unset, a chat waits for as long as every key is resting: each key's own rest is already "
        "capped by the ceiling above, and the call retries the moment one returns, so a spent quota "
        "ends with an answer on the same model. Any value you set wins: 0 means never stop waiting; a"
        " number bounds the wait and hands Agent Zero the provider's original refusal, which ends its"
        " task."),
    "never_fall_back_to_another_model": (
        "KAME itself never chooses a model. Off (the default): if you have set fallback models in the"
        " Fallback models tab, they are used automatically when every key of your model is resting "
        "for long, and the next reply starts on your model again. With no list nothing ever switches."
        " On: your list is ignored and KAME waits for your keys however long that takes."),
    "hedge_mode": (
        "When a reply is slower than usual, send a backup request on another key and use whichever "
        "answers first. Balanced learns each model's usual wait and only races the slow calls; Faster"
        " does the same a notch sooner and more often, within a larger budget, and steps back to "
        "Balanced for 10 minutes when a key hits a rate limit."),
    "stream_silence_timeout_seconds": (
        "For a provider that accepts a request and then sends nothing. KAME waits this long for the "
        "first character — and for every character after it — before dropping that key and asking the"
        " next one. Zero, the default, leaves the wait at 120 s. Leave it at zero unless you have"
        " a provider that accepts a request and then hangs: a reasoning model can spend well over "
        "twenty seconds before its first token, and a number that short would abandon it mid-thought "
        "and rotate through the whole pool doing the same. Anything above zero is raised to at least "
        "5s for the same reason. If you do have such a provider, 60 is the number to try: it is well "
        "clear of the slowest honest first token anyone has measured here — so a hung key costs a "
        "minute, and a slow one is still allowed to think."),
    "share_pool_health": (
        "Holds are written to one file beside the plugin's other state, as a hash of each key and "
        "never the key, so a bench or a success is still known after Agent Zero restarts, and by "
        "every Agent Zero process that uses the same keys. Off, each process learns a key's rest and "
        "its recovery on its own and starts from zero after a restart. Turning this on never makes a "
        "hold longer than the ceiling above already allows."),
}

#: 1.8.3.0 (A6): the settings a first-time reader can skip (Hermes `_ADVANCED`): hidden behind "Show advanced settings"
#: until changed. The switches of the Troubleshooting shelf are folded on a card of their own instead.
ADVANCED = frozenset({
    "max_total_wait_seconds",
    "never_fall_back_to_another_model",
    "hedge_max_legs",
    "probe_burst_max_legs",
    "hedge_budget_pct",
    "hedge_burst",
    "hedge_quantile",
    "hedge_cold_s",
    "hedge_cold_reasoning_s",
    "hedge_max_prompt_tokens",
    "share_pool_health",
    "pacing_rpm_overrides",
    "key_region_overrides",
    "daily_quota_cooldown_seconds",
    "max_hold_seconds",
    "unsized_throttle_backoff",
    "unsized_throttle_rest_seconds",
    "unsized_backoff_max_seconds",
    "stream_resume_limit",
    "key_log_style",
    "kame_log_full_errors",
    "kame_unusable_response_limit",
})

#: Units, whole-number counts, floors ('0 or at least') and consequential switches (Hermes `UNITS`,
#: `_NUMBER_INTEGRAL`, `_NUMBER_FLOOR_ABOVE_ZERO`, `CONSEQUENTIAL`).
UNITS: Dict[str, str] = {
    "failover_after_seconds": "seconds",
    "max_total_wait_seconds": "seconds",
    "stream_silence_timeout_seconds": "seconds",
    "hedge_max_legs": "legs",
    "probe_burst_max_legs": "keys",
    "hedge_budget_pct": "% of calls",
    "hedge_burst": "backups",
    "hedge_quantile": "percentile",
    "hedge_cold_s": "seconds",
    "hedge_cold_reasoning_s": "seconds",
    "hedge_max_prompt_tokens": "tokens",
    "daily_quota_cooldown_seconds": "seconds",
    "max_hold_seconds": "seconds",
    "unsized_throttle_rest_seconds": "seconds",
    "unsized_backoff_max_seconds": "seconds",
    "stream_resume_limit": "times",
}
INTEGRAL = frozenset({"hedge_max_legs", "probe_burst_max_legs", "hedge_burst", "hedge_max_prompt_tokens", "stream_resume_limit"})
OFF_OR_AT_LEAST: Dict[str, float] = {"stream_silence_timeout_seconds": 5.0}
CONSEQUENTIAL = frozenset({"rotation_disabled", "carousel_disabled"})


#: 1.8.2.9 (U4): the shelves of the settings page, ``(id, title, note, keys)``, in the order they
#: are shown. Hermes' five, with its titles and notes word for word, and one more after "Rest
#: times" for what exists only in Agent Zero. 1.8.3.0 (A6): the keys on each shelf are Hermes' keys
#: in Hermes' order (its ``disabled`` is ``rotation_disabled`` here); ``resolver_disabled`` and
#: ``quota_id_disabled`` have no function behind them in Agent Zero (decisions/0015).
GROUPS = (
    ("extra", "If your model cannot answer",
     "When your provider is busy, every key of your model is resting, or a reply takes too long.",
     ("model_switch", "failover_after_seconds", "reply_notice", "max_total_wait_seconds",
      "never_fall_back_to_another_model")),
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
     ("kame_log_level", "key_log_style", "kame_log_full_errors", "kame_unusable_response_limit")),
    ("off", "Troubleshooting: turn parts of KAME off",
     "Each switch turns one part of KAME off and gives that job back to Agent Zero. For finding "
     "out whether KAME is behind a problem. Leave them alone unless something is wrong.",
     ("rotation_disabled", "spread_disabled", "carousel_disabled", "storm_collapse_disabled",
      "live_status_disabled", "stream_stitch_disabled", "refusal_recorder_disabled",
      "call_timings_disabled", "hedging_disabled")),
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


#: 1.8.3.0 (A6): on the settings page for everybody - every setting that is not in ``ADVANCED`` (Hermes'
#: ``_ADVANCED``). The rest is under "Show advanced settings"; a changed one is never hidden. The
#: fallback list has its own tab; the switches of the last shelf are folded on a card of their own.
VISIBLE = tuple(key for _g, _t, _n, keys in GROUPS if _g != "off" for key in keys if key not in ADVANCED)
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
TROUBLESHOOTING = GROUPS[-1][3]

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


#: 1.8.3.0 (A6): Agent Zero's old names for two switches that Hermes names the other way round. The
#: old key is still read from a saved configuration and still accepted by `/kame set`; it means the
#: opposite of the new one. ``old name -> new name``.
LEGACY = {
    "kame_collapse_storm_logs": "storm_collapse_disabled",
    "kame_wait_notice": "live_status_disabled",
}


def canonical(name: str, raw: Any = None) -> Tuple[str, Any]:
    """``(current name, value)`` for a name a person may still type: a legacy one is turned round."""
    new = LEGACY.get(str(name).strip())
    if new is None:
        return name, raw
    text = str(raw).strip().lower()
    if text in _TRUE:
        return new, False
    if text in _FALSE:
        return new, True
    return new, raw


def parse(name: str, raw: Any) -> Tuple[bool, Any, str]:
    """``(ok, value, message)`` for one setting and one typed value.

    The words of a refusal are Hermes' (`settings.parse`), so the panel and `/kame set` say the same
    thing on both hosts.
    """
    name, raw = canonical(name, raw)
    row = SETTINGS.get(name)
    if row is None:
        return False, None, f"`{name}` is not a KAME setting. `/kame get` lists them."
    kind, _default, low, high, choices = row[0], row[1], row[2], row[3], row[4]
    text = str(raw).strip() if raw is not None else ""
    lowered = text.lower()
    if kind == "flag":
        if lowered in _TRUE:
            return True, True, ""
        if lowered in _FALSE:
            return True, False, ""
        return False, None, f"{name} takes true or false; {text!r} is neither"
    if kind == "text":
        value = text
        if len(value) > 4000:
            return False, None, f"{name} is at most 4000 characters."
        return True, value, ""
    if kind == "choice":
        if lowered in choices:
            return True, lowered, ""
        return False, None, f"{name} accepts {', '.join(sorted(choices))}; {text!r} is not one of them"
    try:
        number = float(text)
    except ValueError:
        number = float("nan")
    if number != number:
        return False, None, f"{name} takes a number of {UNITS.get(name, 'seconds')}; {text!r} is not one"
    if number < low or number > high:
        return False, None, f"{name} accepts {low:g} to {high:g}; {number:g} is outside that"
    floor = OFF_OR_AT_LEAST.get(name)
    if floor is not None and 0.0 < number < floor:
        return False, None, (f"{name} is either 0 (off) or at least {floor:g} — anything shorter "
                             "fires while a healthy provider is still connecting")
    if name in INTEGRAL and number != int(number):
        return False, None, f"{name} counts whole things; {number:g} is not a whole number"
    return True, int(number) if number.is_integer() else number, ""


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

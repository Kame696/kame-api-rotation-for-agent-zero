# Generate the 1.8.2.7 settings screen from one table, so every control carries
# the x-init default the yaml has.
import sys

rows = [
    ("fallback_models", "text", "''", "Fallback models",
     "When your chat model cannot answer and nothing has been shown yet, KAME answers with the first of these "
     "that can. One <code>provider/model</code> per line (Agent Zero's provider ids), optional "
     "<code>@ api_base</code>. Empty: nothing ever switches. Background jobs never switch model.", ""),
    ("model_switch", "choice", "'auto'", "Move to your next model",
     "<b>Automatic</b>: when your provider is busy (two keys in a row overloaded or timing out) the next model "
     "answers at once; otherwise once you have waited about three times your model's usual first-word time "
     "(6 to 20 s, learned). <b>After my time</b>: the seconds below. <b>Never</b>: always wait for your keys.",
     [("auto", "Automatic"), ("timed", "After my time"), ("never", "Never")],
     "", "(config.failover_after_seconds === undefined || config.failover_after_seconds === null "
         "|| Number(config.failover_after_seconds) === 60) ? 'auto' : "
         "(Number(config.failover_after_seconds) <= 0 ? 'never' : 'timed')"),
    ("failover_after_seconds", "number", "60", "My time to move to the next model (seconds)",
     "Once you have waited this long with nothing on screen, the next model of your list answers.",
     'min="0" max="3600"', "config.model_switch === 'timed'"),
    ("max_total_wait_seconds", "number", "0", "Stop waiting and show an error after (seconds)",
     "When every key rests, KAME waits for the first one back. 0 = as long as it takes. A number hands "
     "Agent Zero the error after that long, which ends the task.", 'min="0" max="604800"'),
    ("hedge_mode", "choice", "'adaptive'", "Speed",
     "<b>Balanced</b>: a reply still silent after twice its usual wait races a backup key; the first key "
     "that starts working wins (about 10% extra requests). <b>Fastest</b>: every reply races. <b>Saver</b>: "
     "one key at a time.", [("off", "Saver"), ("adaptive", "Balanced"), ("max", "Fastest")]),
    ("first_token_wait", "choice", "'auto'", "Wait for the first token",
     "How long a key may stay silent before KAME asks the next one. <b>Automatic</b> learns each model's "
     "usual first-word time and drops a key only far past it (20 s at least, 60 s for thinking models; never "
     "for a model on this computer). <b>My time</b>: the seconds below. <b>Off</b>: no limit.",
     [("auto", "Automatic"), ("timed", "My time"), ("off", "Off")],
     "", "(config.stream_silence_timeout_seconds === undefined || config.stream_silence_timeout_seconds === null "
         "|| Number(config.stream_silence_timeout_seconds) === 0) ? 'auto' : 'timed'"),
    ("stream_silence_timeout_seconds", "number", "0", "My first-token wait (seconds)",
     "Seconds a key may stay silent before the next one is tried (a quarter of it from the third try on).",
     'min="0" max="3600"', "config.first_token_wait === 'timed'"),
    ("reply_notice", "choice", "'always'", "Show which model answered",
     "When a reply ends, one line in the chat log: its time and the model that answered each step. Never "
     "part of what the model reads.",
     [("always", "Every reply"), ("switches", "Only another model"), ("never", "Never")]),
    ("kame_wait_notice", "flag", "true", "Say so in the chat when every key rests",
     "A live notification at ~15 s and one chat line at ~90 s, never in the model's history.", ""),
    ("kame_log_level", "choice", "'normal'", "Log level",
     "How much KAME writes to the Docker log. The rotation is the same at every level.",
     ["silent", "normal", "verbose", "verbose+errors"]),
]
adv = [
    ("probe_burst_max_legs", "number", "16", "Keys asked together when quota runs dry",
     "After two long refusals in a row, ask 4, then 8, then up to this many keys at once instead of one by "
     "one. 0 = one by one.", 'min="0" max="32"'),
    ("hedge_max_legs", "number", "3", "Most keys raced for one reply",
     "One call plus at most this many minus one backups.", 'min="1" max="3"'),
    ("stream_resume_limit", "number", "-1", "Resume attempts per answer",
     "An answer cut mid-stream is continued on the next key from where it stopped, shown once. -1 continues "
     "while it makes progress; 0 starts the answer again.", 'min="-1" max="10"'),
    ("hedge_budget_pct", "number", "10", "Backup budget (% of calls)",
     "Roughly what share of calls may carry a backup request.", 'min="0" max="50"'),
    ("hedge_burst", "number", "3", "Backup burst allowance",
     "How many backups can fire back to back after an idle period.", 'min="1" max="10"'),
    ("hedge_quantile", "number", "90", "Send backup when slower than usual",
     "For calls that do not stream: the percentile of learned times a call must exceed.", 'min="50" max="99"'),
    ("hedge_cold_s", "number", "6", "First backup while still learning (seconds)",
     "Before a model's usual time is learned, a call slower than this still gets a backup.", 'min="0.5" max="120"'),
    ("hedge_cold_reasoning_s", "number", "30", "First backup while learning, thinking models (seconds)",
     "The same, for models that think before they answer.", 'min="2" max="300"'),
    ("hedge_max_prompt_tokens", "number", "32000", "Skip backups for very long prompts (tokens)",
     "Above this size no backup and no keys asked together.", 'min="1000" max="1000000"'),
    ("pacing_rpm_overrides", "line", "''", "Per-model requests-per-minute limits",
     "e.g. <code>google:gemini/gemini-2.5-flash=10, groq:*=30</code>. KAME also learns them from real 429s.", ""),
    ("key_region_overrides", "line", "''", "Region per key",
     "For Vertex / Bedrock behind an endpoint that hides it: <code>k3f9a1=us-central1</code>.", ""),
    ("daily_quota_cooldown_seconds", "number", "3600", "Daily quota cooldown (seconds)",
     "How long a key rests once a daily or account quota is really spent.", 'min="1" max="86400"'),
    ("max_hold_seconds", "number", "3600", "Longest a key can be held (seconds)",
     "No key sits out longer than this, whatever the provider claimed.", 'min="60" max="86400"'),
    ("unsized_throttle_rest_seconds", "number", "30", "Rest after a throttle with no number (seconds)",
     "Measured: a refused key retried within 30 s answered 0 of 73 times.", 'min="0" max="300"'),
    ("unsized_throttle_backoff", "flag", "true", "Short ladder for a bare RESOURCE_EXHAUSTED",
     "Gemini's bare 429 rests the key 1, 2, 4 ... 64 s, back to 1 s when it answers.", ""),
    ("unsized_backoff_max_seconds", "number", "64", "Where the ladder stops (seconds)",
     "The longest rung of the ladder above.", 'min="1" max="3600"'),
    ("share_pool_health", "flag", "true", "Keep key health across restarts",
     "Holds are kept on disk (a hash of each key, never the key).", ""),
    ("key_log_style", "choice", "'fingerprint'", "How keys appear in logs",
     "A fingerprint never leaks; prefix8 shows the first 8 characters.", ["fingerprint", "prefix8"]),
    ("kame_log_full_errors", "flag", "false", "Show the full raw error",
     "Every failure also logs the provider's untruncated message.", ""),
    ("kame_collapse_storm_logs", "flag", "true", "Collapse outage logs",
     "During an outage, repeats become one line every ~20 s.", ""),
    ("kame_unusable_response_limit", "number", "5", "Floor for Agent Zero's unusable-response limit",
     "Agent Zero stops a turn after this many unparseable answers in a row; KAME raises its limit to at "
     "least this. 0 leaves it alone.", 'min="0" max="100"'),
]
trouble = [
    ("rotation_disabled", "flag", "false", "Turn KAME off",
     "Every call goes to Agent Zero as if KAME were not installed (<code>KAME_ROTATION_DISABLED</code>).", ""),
    ("spread_disabled", "flag", "false", "Give back key selection",
     "Cooldowns stay, but the first ready key in the order you wrote them is used "
     "(<code>KAME_SPREAD_DISABLED</code>).", ""),
    ("carousel_disabled", "flag", "false", "Stop rotating per call",
     "A refused key is still rested, but the error goes back to Agent Zero (<code>KAME_CAROUSEL_DISABLED</code>).", ""),
    ("hedging_disabled", "flag", "false", "Stop backups and keys asked together",
     "The same as speed boost 'off': one key at a time (<code>KAME_HEDGING_DISABLED</code>).", ""),
    ("stream_stitch_disabled", "flag", "false", "Stop continuing cut answers",
     "A cut answer starts again on the next key instead of continuing (<code>KAME_STREAM_STITCH_DISABLED</code>).", ""),
    ("refusal_recorder_disabled", "flag", "false", "Stop recording refusals",
     "No more refusals.jsonl (keys removed, 8 MB at most, decides nothing).", ""),
    ("call_timings_disabled", "flag", "false", "Stop timing calls",
     "No more calls.jsonl (durations only; the speed boost also learns from them).", ""),
]


def field(r):
    name, kind, d, title, sentence, extra = r[:6]
    show = r[6] if len(r) > 6 else ""
    # 1.8.2.8 (review M3): a choice whose default depends on a number saved
    # before the choice existed - the same rule as kame_settings.choice_from_config.
    if len(r) > 7 and r[7]:
        d = r[7]
    init = f'x-init="if (config.{name} === undefined || config.{name} === null) config.{name} = {d}"'
    if kind == "flag":
        ctl = (f'<label class="toggle"><input type="checkbox" x-model="config.{name}" {init} />'
               f'<span class="toggler"></span></label>')
    elif kind == "number":
        ctl = f'<input type="number" step="1" {extra} x-model.number="config.{name}" {init} />'
    elif kind == "line":
        ctl = f'<input type="text" style="width:100%;font-family:monospace" x-model="config.{name}" {init} />'
    elif kind == "choice":
        opts = "".join(f'<option value="{c[0]}">{c[1]}</option>' if isinstance(c, tuple)
                       else f'<option value="{c}">{c}</option>' for c in extra)
        ctl = f'<select x-model="config.{name}" {init}>{opts}</select>'
    else:
        ctl = ('<textarea rows="4" style="width:100%;font-family:monospace" '
               'placeholder="openrouter/meta-llama/llama-4-maverick&#10;groq/llama-3.3-70b-versatile" '
               f'x-model="config.{name}" {init}></textarea>')
    shown = f' x-show="{show}"' if show else ""
    return (f'      <div class="field"{shown}>\n'
            f'        <div class="field-label">\n'
            f'          <div class="field-title">{title}</div>\n'
            f'          <div class="field-description">{sentence}</div>\n'
            f'        </div>\n'
            f'        <div class="field-control">{ctl}</div>\n'
            f'      </div>\n')


head = """<!--
  Settings screen for the api_rotation_by_kame plugin (Agent Zero's plugin settings modal).

  1.8.2.8: the choices on show, a number only under 'my time'; 1.8.2.7 rethought it: settings on show, the rest behind "Show advanced
  settings", the switches that turn a part of KAME off folded away. The same
  settings can be edited in the KAME panel (central menu -> KAME).

  Every control carries an x-init default matching default_config.yaml:
  `get_plugin_config` returns the SAVED config.json when one exists, not
  merged with the yaml, so a setting added after the last Save arrives as
  `undefined` and would otherwise be saved back as a lie (v1.2.0). This file
  is generated from one table; keep the defaults equal to the yaml.
-->
<html>
<head>
  <title>KAME - API Rotation Settings</title>
</head>
<body>
<div x-data="{ advanced: false }">
  <template x-if="config">
    <div>
      <div class="section-title">KAME - API key rotation</div>
      <div class="section-description">
        KAME works with none of these touched. Put several keys of one provider in
        that provider's API key field, separated by commas (or
        <code>API_KEY_GOOGLE=key1,key2</code> in <code>usr/.env</code>, or
        <code>/kame-keys add google key1,key2</code> in the chat). Keys from
        different accounts each bring their own quota. The live view is in the
        central menu: <b>KAME</b>.
      </div>

"""
mid = """      <button class="btn" type="button" @click="advanced = !advanced"
              x-text="advanced ? 'Hide advanced settings' : 'Show advanced settings'"></button>

      <div x-show="advanced">
        <div class="section-title">Advanced</div>
        <div class="section-description">Numbers KAME already sizes from the evidence. Change one only for a measured reason.</div>

"""
tail1 = """        <details>
          <summary class="section-title">Troubleshooting - turns parts of KAME off</summary>

"""
end = """        </details>
      </div>
    </div>
  </template>
</div>
</body>
</html>
"""
html = head + "".join(map(field, rows)) + mid + "".join(map(field, adv)) + tail1 + "".join(map(field, trouble)) + end
open(sys.argv[1], "w", encoding="utf-8", newline="").write(html)
print(len(html))

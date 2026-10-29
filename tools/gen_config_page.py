# Generate the 1.8.2.7 settings screen from one table, so every control carries
# the x-init default the yaml has.
import html as html_module
import os
import sys

# 1.8.2.9: the speed words come from the plugin's own table (the same functions the panel
# serves), at the default backup budget, so the page and the panel cannot say different things.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import kame_settings as table  # noqa: E402

_BUDGET = table.default("hedge_budget_pct")
SPEED_LABELS = table.choice_labels_now("hedge_mode", _BUDGET)
SPEED_HELP = table.choice_help_now("hedge_mode", _BUDGET)
SPEED_FOOTNOTE = table.CHOICE_FOOTNOTE["hedge_mode"]
#: Sentences shown under a choice's control, for the chosen value only: name -> (by value, footnote).
CHOICE_NOTES = {"hedge_mode": (SPEED_HELP, SPEED_FOOTNOTE)}

#: 1.8.3.0 (A6): the stored value of a choice saved before the choice existed (kame_settings.choice_from_config).
LEGACY_INIT = {
    "model_switch": "(config.failover_after_seconds === undefined || config.failover_after_seconds === null "
                    "|| Number(config.failover_after_seconds) === 60) ? 'auto' : "
                    "(Number(config.failover_after_seconds) <= 0 ? 'never' : 'timed')",
    "first_token_wait": "(config.stream_silence_timeout_seconds === undefined || config.stream_silence_timeout_seconds === null "
                        "|| Number(config.stream_silence_timeout_seconds) === 0) ? 'auto' : 'timed'",
}
POWER = {"high": "●●● High", "medium": "●●○ Medium", "low": "●○○ Low",
         "escape": "Off switch"}
FALLBACK_SENTENCE = (
    "When your chat model cannot answer and nothing has been shown yet, KAME answers with the first of these "
    "that can. One <code>provider/model</code> per line (Agent Zero's provider ids), optional "
    "<code>@ api_base</code>. Empty: nothing ever switches. Background jobs never switch model.")


def _row(name):
    """One settings row from the plugin's own table, so this page cannot say what the panel does not."""
    kind, default, low, high, choices, _env, title, sentence = table.SETTINGS[name]
    esc = html_module.escape
    if name == "fallback_models":
        sentence_html = FALLBACK_SENTENCE
    else:
        sentence_html = esc(sentence, quote=False)
    when = table.WHEN.get(name, "")
    if when and name != "fallback_models":
        sentence_html += f"<br><i>When to change it: {esc(when, quote=False)}</i>"
    power = POWER.get(table.IMPACT.get(name, ""), "")
    title_html = esc(title, quote=False) + (f' <span style="opacity:.55;font-size:.8em">{power}</span>' if power else "")
    shown = ""
    if name in table.DEPENDS_ON:
        shown = f"config.{table.DEPENDS_ON[name][0]} === '{table.DEPENDS_ON[name][1]}'"
    if kind == "flag":
        return (name, "flag", "true" if default else "false", title_html, sentence_html, "", shown)
    if kind == "number":
        step = "1" if name in table.INTEGRAL else "any"
        return (name, "number", repr(default), title_html, sentence_html,
                f'step="{step}" min="{low:g}" max="{high:g}"', shown)
    if kind == "choice":
        labels = table.choice_labels_now(name, _BUDGET)
        opts = [(c, labels.get(c, c)) for c in (table.CHOICE_LABELS.get(name) and [v for v, _l in table.CHOICE_LABELS[name]] or choices)]
        return (name, "choice", repr(default), title_html, sentence_html, opts, shown, LEGACY_INIT.get(name, ""))
    return (name, "text" if name == "fallback_models" else "line", "''", title_html, sentence_html, "", shown)


rows = []
adv = []
trouble = []
for _gid, _title, _note, _keys in table.GROUPS:
    for _name in (["fallback_models"] if _gid == "extra" else []) + list(_keys):
        (trouble if _gid == "off" else rows).append(_row(_name))


def field(r, also=""):
    name, kind, d, title, sentence, extra = r[:6]
    show = r[6] if len(r) > 6 else ""
    # 1.8.2.9: a setting outside the first two of its group waits behind "Show advanced settings"
    show = " && ".join(part for part in ((f"({show})" if show else ""), also) if part)
    # 1.8.2.8 (review M3): a choice whose default depends on a number saved
    # before the choice existed - the same rule as kame_settings.choice_from_config.
    if len(r) > 7 and r[7]:
        d = r[7]
    init = f'x-init="if (config.{name} === undefined || config.{name} === null) config.{name} = {d}"'
    if kind == "flag":
        ctl = (f'<label class="toggle"><input type="checkbox" x-model="config.{name}" {init} />'
               f'<span class="toggler"></span></label>')
    elif kind == "number":
        ctl = f'<input type="number" {extra} x-model.number="config.{name}" {init} />'
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
    notes = ""
    if name in CHOICE_NOTES:
        by_value, footnote = CHOICE_NOTES[name]
        for value, text in by_value.items():
            notes += (f'\n          <div class="field-description" '
                      f'x-show="config.{name} === \'{value}\'">{text}</div>')
        notes += (f'\n          <div class="field-description" '
                  f'x-show="config.{name} !== \'off\'">{footnote}</div>')
    return (f'      <div class="field"{shown}>\n'
            f'        <div class="field-label">\n'
            f'          <div class="field-title">{title}</div>\n'
            f'          <div class="field-description">{sentence}</div>\n'
            f'        </div>\n'
            f'        <div class="field-control">{ctl}{notes}</div>\n'
            f'      </div>\n')


head = """<!--
  Settings screen for the api_rotation_by_kame plugin (Agent Zero's plugin settings modal).

  1.8.2.8: the choices on show, a number only under 'Set time'; 1.8.2.7 rethought it: settings on show, the rest behind "Show advanced
  settings", the switches that turn a part of KAME off folded away. The same
  settings can be edited in the API Rotation page (central menu -> API Rotation).
  1.8.2.9: the settings are grouped the way the Hermes version groups them (kame_settings.GROUPS).

  Every control carries an x-init default matching default_config.yaml:
  `get_plugin_config` returns the SAVED config.json when one exists, not
  merged with the yaml, so a setting added after the last Save arrives as
  `undefined` and would otherwise be saved back as a lie (v1.2.0). This file
  is generated from one table; keep the defaults equal to the yaml.
-->
<html>
<head>
  <title>API Rotation Settings</title>
</head>
<body>
<div x-data="{ advanced: false }">
  <template x-if="config">
    <div>
      <div class="section-title">API Rotation</div>
      <div class="section-description">
        KAME works with none of these touched. Put several keys of one provider in
        that provider's API key field, separated by commas (or
        <code>API_KEY_GOOGLE=key1,key2</code> in <code>usr/.env</code>, or
        <code>/kame-keys add google key1,key2</code> in the chat). Keys from
        different accounts each bring their own quota. The live view is in the
        central menu: <b>API Rotation</b>.
      </div>

"""
def grouped() -> str:
    """The settings in the shelves of kame_settings.GROUPS, with Hermes' titles and notes."""
    by_name = {r[0]: r for r in rows + adv + trouble}
    out = ""
    for gid, title, note, keys in table.GROUPS:
        names = (["fallback_models"] if gid == "extra" else []) + list(keys)
        missing = [n for n in names if n not in by_name]
        assert not missing, f"settings in GROUPS without a row: {missing}"
        body = "".join(
            field(by_name[n], "" if (n == "fallback_models" or n in table.VISIBLE or gid == "off") else "advanced")
            for n in names)
        if gid == "off":
            out += ('      <details>\n'
                    f'        <summary class="section-title">{title}</summary>\n'
                    f'        <div class="section-description">{note}</div>\n'
                    f'{body}      </details>\n')
        else:
            out += (f'      <div class="section-title">{title}</div>\n'
                    f'      <div class="section-description">{note}</div>\n'
                    f'{body}')
    return out


button = ('      <button class="btn" type="button" @click="advanced = !advanced"\n'
          "              x-text=\"advanced ? 'Hide advanced settings' : 'Show advanced settings'\"></button>\n")
html = head + grouped() + button + "    </div>\n  </template>\n</div>\n</body>\n</html>\n"
open(sys.argv[1], "w", encoding="utf-8", newline="").write(html)
print(len(html))

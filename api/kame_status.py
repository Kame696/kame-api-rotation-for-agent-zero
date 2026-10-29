"""`POST /plugins/api_rotation_by_kame/kame_status` - what the rotation is doing.

v1.6.0.1. Agent Zero v2.11 added WebUI plugin slots beside the model/context
strip; this is the endpoint the chip in that slot reads. 1.8.2.7: the KAME
panel (central menu -> KAME) reads it too, with ``{"panel": true}`` adding the
settings table (titles, sentences, defaults, which ones are on show).

**It returns counts and fingerprints. It never returns a key.** That is enforced
one layer down, in `kame_engine.pool_report`, which uses `_key_short_id`
directly rather than the configurable `_key_display` - a rendering setting meant
for a developer's console must not be able to put a secret on a web page.

Read-only by construction: there is no branch in this file that writes anything.
A screen that can change the pool it is describing is a screen that can break a
run by being looked at. (Settings are changed by `kame_settings_set`.)
"""

from helpers.api import ApiHandler, Input, Output, Request


def _labels(table, name):
    try:
        return dict(table.choice_labels_now(name))
    except Exception:
        return dict(getattr(table, "CHOICE_LABELS", {}).get(name, ()))


def _choice_help(table, name):
    try:
        return dict(table.choice_help_now(name))
    except Exception:
        return {}


def _settings_meta():
    try:
        from usr.plugins.api_rotation_by_kame import kame_settings as table
    except Exception:
        return []
    advanced = set(getattr(table, "ADVANCED", ()))
    group_of = getattr(table, "group_of", lambda _name: "tuning")
    rows = []
    for name, row in table.SETTINGS.items():
        kind, default, low, high, choices, env, title, sentence = row
        rows.append({
            "name": name, "type": kind, "default": default, "low": low, "high": high,
            "choices": list(choices) if choices else None, "env": env, "title": title,
            "sentence": sentence,
            # 1.8.2.9: the shelf (Hermes' groups) and whether it is on show without "Show advanced".
            "group": group_of(name),
            # 1.8.3.0 (A6): everything Hermes' row shows: its power tag, "When to change it", the long
            # tooltip, the unit and range, and whether turning it on asks first.
            "advanced": name in advanced,
            "impact": getattr(table, "IMPACT", {}).get(name, "low"),
            "when": getattr(table, "WHEN", {}).get(name, ""),
            "detail": getattr(table, "DETAIL", {}).get(name, ""),
            "units": getattr(table, "UNITS", {}).get(name, ""),
            "integral": name in getattr(table, "INTEGRAL", ()),
            "off_or_at_least": getattr(table, "OFF_OR_AT_LEAST", {}).get(name),
            "consequential": name in getattr(table, "CONSEQUENTIAL", ()),
            "env_override": table.env_override(name),
            # 1.8.2.8: the words each choice is shown with, in order, and the
            # choice a number belongs to.
            # 1.8.2.9: the speed words carry what each choice costs, from the live budget,
            # with one plain sentence per choice and a footnote (Hermes' texts).
            "labels": _labels(table, name),
            "choice_help": _choice_help(table, name),
            "choice_footnote": getattr(table, "CHOICE_FOOTNOTE", {}).get(name, ""),
            "choice_order": [v for v, _l in getattr(table, "CHOICE_LABELS", {}).get(name, ())] or None,
            "depends_on": list(getattr(table, "DEPENDS_ON", {}).get(name, ())) or None,
        })
    return rows


class KameStatus(ApiHandler):
    async def process(self, input: Input, request: Request) -> Output:
        # Imported inside `process`, not at module scope, for the same reason
        # every other KAME entry point does it: a half-copied plugin directory
        # must degrade to one dead panel, never to an Agent Zero that will not
        # start. An ImportError here is answered, not raised.
        try:
            from usr.plugins.api_rotation_by_kame.kame_engine import pool_report
        except Exception as exc:  # pragma: no cover - import shape is host-side
            return {
                "available": False,
                "reason": f"{type(exc).__name__}: {exc}",
                "pools": [],
                "totals": {"keys": 0, "ready": 0, "resting": 0, "retired": 0},
            }

        # 1.8.2.9: the chat the page or the chip is open on, so "what is it doing now" is that chat's
        context_id = (input.get("context_id") or input.get("context")) if isinstance(input, dict) else None
        try:
            report = pool_report(context_id=context_id or None)
        except TypeError:        # an older engine beside this handler
            report = pool_report()
        report["available"] = True
        if isinstance(input, dict) and input.get("panel"):
            report["settings_meta"] = _settings_meta()
            try:
                from usr.plugins.api_rotation_by_kame import kame_settings as table
                report["setting_groups"] = list(table.groups())
            except Exception:
                report["setting_groups"] = []
        return report

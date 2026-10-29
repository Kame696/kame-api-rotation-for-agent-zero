"""`POST /plugins/api_rotation_by_kame/kame_settings_set` - change one KAME setting.

1.8.2.7. The KAME panel (Agent Zero's central menu -> KAME) edits the settings
everybody needs - the fallback models, when to switch, when to stop waiting,
the speed boost - without opening the plugin settings screen. One setting per
call, validated by `kame_settings.parse` (the same table `/kame set` uses),
saved to the one global plugin config, applied at once through `activate`.

It never receives, stores or returns an API key: no setting is a key.
"""

from helpers.api import ApiHandler, Input, Output, Request

PLUGIN = "api_rotation_by_kame"


class KameSettingsSet(ApiHandler):
    async def process(self, input: Input, request: Request) -> Output:
        try:
            from usr.plugins.api_rotation_by_kame import kame_settings as table
        except Exception as exc:  # pragma: no cover - import shape is host-side
            return {"ok": False, "message": f"{type(exc).__name__}: {exc}"}
        name = str((input or {}).get("name") or "").strip()
        reset = bool((input or {}).get("reset"))
        # 1.8.3.0: an old name (`kame_wait_notice`) is saved under Hermes' name for the opposite switch.
        name, raw = table.canonical(name, (input or {}).get("value"))
        if name not in table.SETTINGS:
            return {"ok": False, "message": f"`{name}` is not a KAME setting."}
        if reset:
            value = table.default(name)
        else:
            ok, value, message = table.parse(name, raw)
            if not ok:
                return {"ok": False, "message": message}
        try:
            from helpers.plugins import get_plugin_config, save_plugin_config

            config = dict(get_plugin_config(PLUGIN, agent=None) or {})
            config[name] = value
            save_plugin_config(PLUGIN, "", "", config)
        except Exception as exc:
            return {"ok": False, "message": f"could not save: {type(exc).__name__}: {exc}"}
        try:
            from usr.plugins.api_rotation_by_kame.kame_activation import activate

            activate(None)
        except Exception:
            pass
        forced = table.env_override(name)
        return {"ok": True, "name": name, "value": value,
                "message": (f"saved; `{forced}` in the environment still wins" if forced else "saved"),
                "env_override": forced}

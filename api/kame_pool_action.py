"""`POST /plugins/api_rotation_by_kame/kame_pool_action` - the buttons of the API Rotation page.

1.8.2.9. One small door for what the page does besides saving a setting:

* ``clear_pool`` with an ``identity``: "Wake this model" - that model's keys start again.
* ``clear_pool`` without one: the toolbar's "Clear pool" - every key starts again.
* ``forget_busy`` + ``identity``: "Try this model now" - it is not known busy any more.
* ``clear_events``: "Clear events" - the event list is emptied, nothing else changes.
* ``reset_settings``: "Reset to defaults" - every KAME setting goes back to its default.

It never receives, stores or returns an API key, and no action deletes one.
"""

from helpers.api import ApiHandler, Input, Output, Request

PLUGIN = "api_rotation_by_kame"


class KamePoolAction(ApiHandler):
    async def process(self, input: Input, request: Request) -> Output:
        try:
            from usr.plugins.api_rotation_by_kame import kame_engine as engine
        except Exception as exc:  # pragma: no cover - import shape is host-side
            return {"ok": False, "message": f"{type(exc).__name__}: {exc}"}
        action = str((input or {}).get("action") or "").strip()
        identity = str((input or {}).get("identity") or "").strip()
        try:
            if action == "clear_pool":
                if identity:
                    count = engine.wake_model(identity)
                    return {"ok": True, "count": count,
                            "message": f"{count} key{'' if count == 1 else 's'} of this model start again."}
                count = engine.reset_pool()
                return {"ok": True, "count": count,
                        "message": f"{count} key{'' if count == 1 else 's'} start again from zero."}
            if action == "forget_busy":
                if not identity:
                    return {"ok": False, "message": "Which model?"}
                was_busy = engine.forget_busy(identity)
                return {"ok": True, "message": ("This model will be asked again now." if was_busy
                                                else "This model was not marked busy.")}
            if action == "clear_events":
                journal = getattr(engine, "_KJ", None)
                if journal is not None:
                    journal.EVENTS.clear()
                return {"ok": True, "message": "Events cleared."}
            if action == "reset_settings":
                from usr.plugins.api_rotation_by_kame import kame_settings as table
                from helpers.plugins import get_plugin_config, save_plugin_config

                config = dict(get_plugin_config(PLUGIN, agent=None) or {})
                for name in table.SETTINGS:
                    if name != "fallback_models":        # the list is edited in its own tab
                        config[name] = table.default(name)
                save_plugin_config(PLUGIN, "", "", config)
                try:
                    from usr.plugins.api_rotation_by_kame.kame_activation import activate

                    activate(None)
                except Exception:
                    pass
                return {"ok": True, "message": "Every setting is back at its default. Your fallback list is kept."}
        except Exception as exc:
            return {"ok": False, "message": f"{type(exc).__name__}: {exc}"}
        return {"ok": False, "message": f"`{action}` is not something this page can do."}

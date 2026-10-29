"""`POST /plugins/api_rotation_by_kame/kame_fallback_set` - save the fallback list from its rows.

1.8.2.9. The "Fallback models" tab edits the list as numbered rows (provider, model, an X to
remove, "+ Add fallback model"). The rows are merged here, on the server, with the text that is
saved: a row that matches a saved line keeps that line as written (a hand-written
`@ api_base` stays), and a saved line nobody can read as an entry (a comment, a note) is kept
after the list. Only rows with a provider AND a model are saved; at most 12.

Saved through the same path as `kame_settings_set` and applied at once. It receives and returns
provider names and model names only, never a key.
"""

from helpers.api import ApiHandler, Input, Output, Request

PLUGIN = "api_rotation_by_kame"


class KameFallbackSet(ApiHandler):
    async def process(self, input: Input, request: Request) -> Output:
        try:
            from usr.plugins.api_rotation_by_kame import kame_engine as engine
            from usr.plugins.api_rotation_by_kame import kame_settings as table
        except Exception as exc:  # pragma: no cover - import shape is host-side
            return {"ok": False, "message": f"{type(exc).__name__}: {exc}"}
        rows = (input or {}).get("rows")
        if not isinstance(rows, list):
            return {"ok": False, "message": "rows must be a list."}
        try:
            from helpers.plugins import get_plugin_config, save_plugin_config

            config = dict(get_plugin_config(PLUGIN, agent=None) or {})
            merged = engine.merge_fallback_rows(rows, config.get("fallback_models", ""))
            ok, value, message = table.parse("fallback_models", merged["text"])
            if not ok:
                return {"ok": False, "message": message}
            config["fallback_models"] = value
            save_plugin_config(PLUGIN, "", "", config)
        except Exception as exc:
            return {"ok": False, "message": f"could not save: {type(exc).__name__}: {exc}"}
        try:
            from usr.plugins.api_rotation_by_kame.kame_activation import activate

            activate(None)
        except Exception:
            pass
        words = [f"{merged['saved']} model{'' if merged['saved'] == 1 else 's'} saved"]
        if merged["incomplete"]:
            words.append(f"{merged['incomplete']} row{'' if merged['incomplete'] == 1 else 's'} left out - "
                         "choose a provider and a model")
        if merged["left_out"]:
            words.append(f"{merged['left_out']} left out - the list holds at most 12")
        return {"ok": True, "text": merged["text"], "entries": merged["entries"], "saved": merged["saved"],
                "incomplete": merged["incomplete"], "left_out": merged["left_out"],
                "message": "; ".join(words)}

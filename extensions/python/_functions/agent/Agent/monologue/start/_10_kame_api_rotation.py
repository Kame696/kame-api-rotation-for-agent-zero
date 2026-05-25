"""KAME activation extension — applies monkey-patches at agent boot.

v0.5.7.4: also reads the ``verbose_trace`` plugin setting and threads it
into the engine. Toggling via the UI takes effect on the next monologue
start (cheap setter, no patch re-apply needed).
"""

from helpers.extension import Extension


class KameActivation(Extension):
    def execute(self, **kwargs):
        # Safe, sync entry point that works on ALL framework versions.
        try:
            from usr.plugins.api_rotation_by_kame.kame_engine import (
                apply_kame_patch,
                set_verbose_trace,
            )

            # v0.5.7.4: pick up verbose_trace from plugin settings (best-effort).
            try:
                from helpers.plugins import get_plugin_config
                cfg = get_plugin_config("api_rotation_by_kame", agent=self.agent) or {}
                set_verbose_trace(bool(cfg.get("verbose_trace", False)))
            except Exception:
                # Older A0 versions may lack get_plugin_config; fall back to default OFF.
                pass

            apply_kame_patch()
        except Exception as e:
            print(f"KAME Activation Error: {e}")

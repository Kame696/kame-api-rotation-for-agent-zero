"""KAME activation at Agent Zero's ``monologue_start`` extension point (v1.0.9).

``monologue_start`` fires at the beginning of every agent monologue. It is the
belt to ``agent_init``'s braces: it re-applies the plugin settings each turn (so
changing them in the UI takes effect immediately) and would still activate KAME
on a build where agent_init somehow did not fire.

The real work lives in ``kame_activation.activate()`` — this file is only a door.
Activation is idempotent, so it does not matter which door fires first, or how
many fire.
"""

from helpers.extension import Extension


class KameActivation(Extension):
    def execute(self, **kwargs):
        # Safe, sync entry point that works on ALL framework versions.
        try:
            from usr.plugins.api_rotation_by_kame.kame_activation import activate
            activate(getattr(self, "agent", None))
        except Exception as e:
            try:
                from helpers.print_style import PrintStyle
                PrintStyle.error(f"[KAME] Activation Error: {e}")
            except Exception:
                print(f"[KAME] Activation Error: {e}")

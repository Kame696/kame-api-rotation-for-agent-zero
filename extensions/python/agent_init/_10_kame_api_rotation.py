"""KAME activation at Agent Zero's ``agent_init`` extension point (v1.0.9).

``agent_init`` is the EARLIEST point Agent Zero offers: it fires once when an
agent is created, before any model call can happen. It is invoked by the
hardcoded string ``call_extensions_sync("agent_init", self)`` in agent.py,
unchanged since at least Agent Zero v1.14 — which is exactly why KAME leans on
it rather than only on the @extensible-derived folder path.

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

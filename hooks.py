"""Framework runtime hooks for the api_rotation_by_kame (KAME) plugin.

Added in v0.5.7. Lets the framework revert all monkey-patches when the
plugin is uninstalled, instead of leaving them dangling until the next
A0 restart.
"""

from __future__ import annotations


async def install() -> None:
    """Called by A0 right after the plugin directory is placed under usr/plugins/.

    KAME does not install any external dependencies and creates no
    persistent state outside its own folder, so this is a no-op.
    """
    return None


async def pre_update() -> None:
    """Called by A0 right before new plugin code is pulled in for an update.

    KAME's monkey-patches store the original A0 methods as attributes on
    the patched classes (e.g. ``LiteLLMChatWrapper._kame_original_unified_call``).
    On update, the patched module file is replaced first and the next
    activation hook re-applies patches against the fresh originals, so
    there is nothing to do here. No-op.
    """
    return None


async def uninstall() -> None:
    """Called by A0 right before the plugin directory is deleted.

    Reverts every monkey-patch applied by the engine so the running
    Agent Zero process is left in a clean state — no dangling references
    to KAME methods on framework classes.
    """
    try:
        from usr.plugins.api_rotation_by_kame.kame_engine import remove_kame_patch
        remove_kame_patch()
    except Exception:
        # Best-effort: never block uninstallation on cleanup errors.
        pass
    return None

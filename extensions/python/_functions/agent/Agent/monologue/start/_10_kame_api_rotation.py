"""KAME activation extension — applies monkey-patches at agent boot.

v0.5.7.4: reads the ``verbose_trace`` plugin setting and threads it into the
engine.
v1.0.1: threads ``daily_quota_cooldown_seconds`` and ``key_log_style``, and
replaces the old ``verbose_trace`` checkbox with a tri-state ``kame_log_level``
(silent / normal / verbose). A legacy ``verbose_trace: true`` still maps to
"verbose" so existing configs keep working. All settings are cheap setters
applied on every monologue start, so changing them in the UI takes effect on
the next turn (no patch re-apply needed).
v1.0.2: also stashes the live agent (``set_current_agent``) each monologue
start, so the engine's all-keys-cooling sleep can honor a user message /
"nudge" instead of sleeping through it.
"""

from helpers.extension import Extension


class KameActivation(Extension):
    def execute(self, **kwargs):
        # Safe, sync entry point that works on ALL framework versions.
        try:
            from usr.plugins.api_rotation_by_kame.kame_engine import (
                apply_kame_patch,
                set_log_level,
                set_verbose_trace,
                set_daily_cooldown,
                set_key_log_style,
                set_log_full_errors,
                set_collapse_storm_logs,
                set_current_agent,
            )

            # Pick up plugin settings (best-effort; defaults preserve behavior).
            try:
                from helpers.plugins import get_plugin_config
                cfg = get_plugin_config("api_rotation_by_kame", agent=self.agent) or {}

                # v1.0.3: optional raw full-error logging (debug; off by default).
                # Set FIRST so the v1.0.4 'verbose+errors' log level can force it on.
                set_log_full_errors(cfg.get("kame_log_full_errors", False))
                # Log verbosity: silent | normal | verbose | verbose+errors (v1.0.4),
                # with a fallback to the legacy verbose_trace boolean (pre-v1.0.1).
                level = cfg.get("kame_log_level")
                if level:
                    set_log_level(level)
                elif cfg.get("verbose_trace"):
                    set_verbose_trace(True)  # legacy true -> "verbose"
                else:
                    set_log_level("normal")

                set_daily_cooldown(cfg.get("daily_quota_cooldown_seconds", 3600))
                set_key_log_style(cfg.get("key_log_style", "fingerprint"))
                # v1.0.3: collapse repetitive 503-storm logs (on by default).
                set_collapse_storm_logs(cfg.get("kame_collapse_storm_logs", True))
            except Exception:
                # Older A0 versions may lack get_plugin_config; fall back to defaults.
                pass

            # v1.0.2: stash the live agent so the engine's all-keys-cooling
            # sleep can honor a queued user message / "nudge" instead of
            # sleeping through it. Task-local (contextvar) — safe under
            # concurrent agents. Best-effort; never blocks activation.
            try:
                set_current_agent(self.agent)
            except Exception:
                pass

            apply_kame_patch()
        except Exception as e:
            # Last-resort error reporting; stay consistent with the [KAME] tag.
            try:
                from helpers.print_style import PrintStyle
                PrintStyle.error(f"[KAME] Activation Error: {e}")
            except Exception:
                print(f"[KAME] Activation Error: {e}")

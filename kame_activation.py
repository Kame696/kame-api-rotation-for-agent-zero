"""KAME activation — the one place that turns the engine on (v1.0.9+).

Why this module exists
----------------------
Up to 1.0.8 the activation logic lived inside a single extension file at
``extensions/python/_functions/agent/Agent/monologue/start/``. That folder path is
DERIVED by Agent Zero's ``@extensible`` decorator from ``agent.py``'s module +
qualname, so the day upstream renames or moves ``Agent.monologue``, the folder
stops matching, the extension silently never fires, and KAME never installs —
no error, no banner, just no rotation. It was the last remaining single point of
failure in the plugin.

v1.0.9 keeps that extension and ADDS two more at Agent Zero's *named* extension
points, ``agent_init`` and ``monologue_start``. Those names are hardcoded strings
in ``agent.py`` (``call_extensions_sync("agent_init", self)``) and have been
byte-identical since at least Agent Zero v1.14. Three independent doors into the
same room: if any one of them still opens, KAME activates.

Activation is idempotent by construction — ``apply_kame_patch()`` returns False
immediately when KAME is already installed — so firing from three points costs
nothing but a dictionary lookup on the second and third.

All settings are cheap setters re-applied on every activation, so changing them
in the UI takes effect on the next turn (no patch re-apply needed).

History kept from the original extension:
  v0.5.7.4 reads ``verbose_trace`` and threads it into the engine.
  v1.0.1 threads ``daily_quota_cooldown_seconds`` and ``key_log_style``, and
  replaces the ``verbose_trace`` checkbox with a tri-state ``kame_log_level``
  (silent / normal / verbose). A legacy ``verbose_trace: true`` still maps to
  "verbose" so existing configs keep working.
  v1.0.2 stashes the live agent (``set_current_agent``) so the engine's
  all-keys-cooling sleep can honor a user message / "nudge".
  v1.2.0 threads ``kame_wait_notice`` — whether a long all-keys-cooling wait is
  announced in the chat as well as on the console.
"""


def activate(agent=None):
    """Apply KAME's settings and patches. Safe to call any number of times.

    Never raises: an activation failure must never take down the agent boot it
    is riding on. Returns True if this call is the one that installed KAME.
    """
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
        # v1.2.0. Fetched by name rather than imported with the rest on purpose:
        # a half-copied plugin directory (new activation file, older engine)
        # would fail the whole import tuple and leave KAME uninstalled — a
        # missing log line is not worth losing rotation over.
        try:
            from usr.plugins.api_rotation_by_kame import kame_engine as _kame_engine
            set_wait_notice = getattr(_kame_engine, "set_wait_notice", None)
        except Exception:
            set_wait_notice = None

        # Pick up plugin settings (best-effort; defaults preserve behavior).
        try:
            from helpers.plugins import get_plugin_config
            cfg = get_plugin_config("api_rotation_by_kame", agent=agent) or {}

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
            # v1.2.0: tell the user in the chat when the whole pool is cooling
            # (on by default — the console said it, the person did not see it).
            if set_wait_notice is not None:
                set_wait_notice(cfg.get("kame_wait_notice", True))
        except Exception:
            # Older A0 versions may lack get_plugin_config; fall back to defaults.
            pass

        # v1.0.2: stash the live agent so the engine's all-keys-cooling sleep can
        # honor a queued user message / "nudge" instead of sleeping through it.
        # Task-local (contextvar) — safe under concurrent agents. Best-effort;
        # never blocks activation.
        try:
            set_current_agent(agent)
        except Exception:
            pass

        return bool(apply_kame_patch())
    except Exception as e:
        # Last-resort error reporting; stay consistent with the [KAME] tag.
        try:
            from helpers.print_style import PrintStyle
            PrintStyle.error(f"[KAME] Activation Error: {e}")
        except Exception:
            print(f"[KAME] Activation Error: {e}")
        return False

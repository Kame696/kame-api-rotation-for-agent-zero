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


def _truthy(value) -> bool:
    return str(value).strip().lower() in ("1", "true", "on", "yes")


def _collapse_from(cfg) -> bool:
    """Collapse outage logs? Hermes' switch `storm_collapse_disabled` wins; else the 1.8.2.9 key."""
    if cfg.get("storm_collapse_disabled") is not None:
        return not _truthy(cfg["storm_collapse_disabled"])
    return cfg.get("kame_collapse_storm_logs", True)


def _notice_from(cfg) -> bool:
    """Say what KAME waits for on the status line? Hermes' `live_status_disabled` wins; else the old key."""
    if cfg.get("live_status_disabled") is not None:
        return not _truthy(cfg["live_status_disabled"])
    return cfg.get("kame_wait_notice", True)


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
            # 1.8.1.2: global only. One engine per process, one set of dials;
            # a profile/project file must not flip them per agent.
            cfg = get_plugin_config("api_rotation_by_kame", agent=None) or {}

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
            # 1.8.3.0: Hermes' name for it is `storm_collapse_disabled` (the opposite switch); a
            # configuration saved before that still carries `kame_collapse_storm_logs`.
            set_collapse_storm_logs(_collapse_from(cfg))
            # v1.2.0: tell the user in the chat when the whole pool is cooling
            # (on by default — the console said it, the person did not see it).
            if set_wait_notice is not None:
                set_wait_notice(_notice_from(cfg))
            # v1.8.1.0: the Hermes 1.8.x rules. Looked up by name for the same
            # half-copied-directory reason as set_wait_notice above.
            for _setting, _setter, _default in (
                ("max_hold_seconds", "set_max_hold", 3600),
                ("unsized_throttle_rest_seconds", "set_unsized_throttle_rest", 30),
                ("unsized_throttle_backoff", "set_unsized_backoff", True),
                ("unsized_backoff_max_seconds", "set_unsized_backoff_max", 64),
                # v1.8.1.0: the rest of the Hermes switches, same names.
                ("rotation_disabled", "set_rotation_disabled", False),
                ("spread_disabled", "set_spread_disabled", False),
                ("carousel_disabled", "set_carousel_disabled", False),
                ("refusal_recorder_disabled", "set_recorder_disabled", False),
                ("call_timings_disabled", "set_call_timings_disabled", False),
                ("share_pool_health", "set_share_pool_health", True),
                # v1.8.3.0: "Ignore my fallback list", Hermes' never_fall_back_to_another_model.
                ("never_fall_back_to_another_model", "set_no_model_fallback", False),
                # v1.8.2.7: the fallback list, speed and the wait bound.
                ("fallback_models", "set_fallback_models", ""),
                ("failover_after_seconds", "set_failover_after", 60),
                ("max_total_wait_seconds", "set_max_total_wait", 0),
                ("hedge_mode", "set_hedge_mode", "adaptive"),
                ("hedge_max_legs", "set_hedge_max_legs", 3),
                ("probe_burst_max_legs", "set_probe_burst", 16),
                # v1.8.2.7: the rest of the Hermes settings that apply here.
                ("stream_silence_timeout_seconds", "set_stream_silence_timeout", 0),
                ("stream_resume_limit", "set_stream_resume_limit", -1),
                ("stream_stitch_disabled", "set_stream_stitch_disabled", False),
                ("hedging_disabled", "set_hedging_disabled", False),
                ("pacing_rpm_overrides", "set_pacing_rpm_overrides", ""),
                ("key_region_overrides", "set_key_region_overrides", ""),
            ):
                _fn = getattr(_kame_engine, _setter, None)
                if _fn is not None:
                    _fn(cfg.get(_setting, _default))
            # v1.8.2.8: the three choices. A config saved before they existed
            # keeps what its number meant: a time changed from the default is
            # "timed", 0 is never / off; untouched is "auto".
            try:
                from usr.plugins.api_rotation_by_kame.kame_settings import choice_from_config
            except Exception:
                choice_from_config = None
            for _choice, _setter in (("model_switch", "set_model_switch"),
                                     ("first_token_wait", "set_first_token_wait")):
                _fn = getattr(_kame_engine, _setter, None)
                if _fn is not None:
                    _fn(choice_from_config(cfg, _choice) if choice_from_config else
                        (cfg.get(_choice) or "auto"))
            _reply = getattr(_kame_engine, "set_reply_notice", None)
            if _reply is not None:
                _reply(cfg.get("reply_notice", "always"))
            _hedge_number = getattr(_kame_engine, "set_hedge_number", None)
            for _setting, _default in (("hedge_budget_pct", 10), ("hedge_burst", 3),
                                       ("hedge_quantile", 90), ("hedge_cold_s", 6),
                                       ("hedge_cold_reasoning_s", 30),
                                       ("hedge_max_prompt_tokens", 32000)):
                if _hedge_number is not None:
                    _hedge_number(_setting, cfg.get(_setting, _default))
        except Exception:
            # Older A0 versions may lack get_plugin_config; fall back to defaults.
            pass

        # v1.8.1.0: the environment wins over the settings page, as on Hermes -
        # one .env line means the same thing on both hosts.
        try:
            _env = getattr(_kame_engine, "_kame_apply_env_overrides", None)
            if _env is not None:
                _env()
        except Exception:
            pass

        # v1.8.1.0: a setting that changed since the last activation goes on
        # the events timeline, beside the decisions it may have changed.
        try:
            _note = getattr(_kame_engine, "note_setting_changes", None)
            if _note is not None:
                _note()
        except Exception:
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

"""KAME Shield — Unusable-Response Floor (v1.0.9).

Agent Zero v2.4 added ``_90_stop_unusable_response_loop.py`` at the
``Agent.hist_add_warning/end`` extension point. It counts CONSECUTIVE
``fw.msg_misformat.md`` / ``fw.msg_repeat.md`` warnings — turns where the
model's own output could not be parsed into a tool request — and aborts the
monologue once the count reaches ``max_consecutive_unusable_responses``.

A0's own stop text states the rationale: "to prevent further API charges".
Under KAME that rationale is much weaker — keys come from a rotated pool, so a
reformat round-trip is normally free. A0 itself decided the original number was
too tight: the default shipped as **2** in v2.4–v2.7 and was raised to **5** in
v2.8 ("Raise unusable response limit to five"). But a persisted settings.json
wins over a new default, so anyone who ran an earlier A0 keeps 2 forever — and
a single JSON-escaping slip from the model then ends the turn.

This extension applies a FLOOR, never a ceiling::

    effective limit = max(A0's setting, kame_unusable_response_limit)

It runs at the same extension point under a later prefix (``_95`` > ``_90``),
so it sees the abort A0 just staged in ``data["exception"]`` and clears it
while the count is still under KAME's floor. The warning line A0 already
emitted is rewritten in place, so the chat shows the retry that actually
happened instead of a stop that did not.

It is a floor, not a bypass. Once KAME's own limit is reached the abort is left
untouched, so a model stuck in a formatting loop still cannot burn the pool
forever. Set ``kame_unusable_response_limit: 0`` to never interfere and leave
A0's behavior exactly as shipped.

Nothing here touches rotation, key selection, or the engine. If A0 renames or
removes the hook folder this file simply never loads and A0's own behavior
stands — same failure mode as having no plugin at all.
"""

from helpers.extension import Extension

# A0's own state key, set by _90_stop_unusable_response_loop.
STATE_KEY = "_unusable_response_failures"

# Used when the plugin config cannot be read (older A0, config not written yet).
DEFAULT_FLOOR = 5

# How far back to look for the warning line _90 just emitted.
_LOG_SCAN_DEPTH = 3


def _read_floor(agent) -> int:
    """KAME's configured floor. 0/negative means 'never interfere'."""
    try:
        from helpers.plugins import get_plugin_config

        cfg = get_plugin_config("api_rotation_by_kame", agent=agent) or {}
        return int(cfg.get("kame_unusable_response_limit", DEFAULT_FLOOR))
    except Exception:
        return DEFAULT_FLOOR


def _rewrite_stop_warning(agent, count: int, floor: int) -> None:
    """Replace the abort line _90 logged with an honest retry note.

    Best-effort and bounded: only the last few items are inspected, and only an
    item that still carries A0's stop text is touched.
    """
    try:
        logs = agent.context.log.logs
    except Exception:
        return
    for item in reversed(logs[-_LOG_SCAN_DEPTH:]):
        try:
            if item.type != "warning":
                continue
            if "unusable" not in (item.content or "").lower():
                continue
            item.update(
                content=(
                    f"[KAME] Unusable model response {count}/{floor} — "
                    "letting Agent Zero retry instead of stopping the turn. "
                    "Raise or disable this in KAME settings "
                    "(kame_unusable_response_limit)."
                ),
                update_progress="none",
            )
            return
        except Exception:
            return


class KameUnusableFloor(Extension):
    # MUST stay synchronous: hist_add_warning is a sync @extensible, so it runs
    # through call_extensions_sync, which raises on an awaitable result.
    def execute(self, data: dict | None = None, **kwargs):
        try:
            if not isinstance(data, dict):
                return

            exc = data.get("exception")
            if exc is None:
                return  # nothing staged; A0 is letting the loop continue already

            # Only ever clear the specific abort _90 stages. A genuine error
            # raised by hist_add_warning itself must keep propagating.
            from helpers.errors import HandledException

            if not isinstance(exc, HandledException):
                return

            agent = self.agent
            if agent is None:
                return

            # If the wrapped call never produced a result, clearing the
            # exception would make the decorator return None and the caller
            # would blow up on `wmsg.id`. Leave those alone.
            try:
                from helpers.extension import _UNSET

                if data.get("result", _UNSET) is _UNSET:
                    return
            except Exception:
                pass

            loop_data = getattr(agent, "loop_data", None)
            state = getattr(loop_data, "params_persistent", None)
            if not isinstance(state, dict):
                return
            entry = state.get(STATE_KEY)
            if not isinstance(entry, dict):
                return  # some other HandledException — not the unusable guard
            count = entry.get("count")
            if not isinstance(count, int):
                return

            floor = _read_floor(agent)
            if floor <= 0 or count >= floor:
                return  # KAME agrees: stop here

            data["exception"] = None
            _rewrite_stop_warning(agent, count, floor)
        except Exception:
            pass  # never crash the agent loop

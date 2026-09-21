"""`/kame-quota` and `/kame-quota reset` — v1.8.1.0, port of the Hermes command.

`/kame-quota` is the quota picture per key and per model: which keys are
resting, for how long, why (daily, per minute, out of credit, an outage...),
and how close a refused key is to leaving rotation. `/kame-quota reset`
forgets the counts — as on Hermes, the live rests are left alone; the pool
itself is cleared by `/kame clear-pool`.

Fingerprints and counts only. Never a key.
"""

from __future__ import annotations

from typing import Any


def run(payload: dict[str, Any]) -> dict[str, Any]:
    invocation = payload.get("invocation") or {}
    argument = str(invocation.get("raw_arguments") or "").strip().lower()
    try:
        from usr.plugins.api_rotation_by_kame import kame_engine as engine
    except Exception as exc:
        return _toast(f"KAME's engine did not import ({type(exc).__name__}).", "error")

    if argument in ("reset", "clear", "forget"):
        engine.reset_counts()
        return _show("KAME — quota counts cleared",
                     "Session counters and the where-each-number-came-from tally "
                     "start again. **Live rests were left alone** — `/kame clear-pool` "
                     "is the one that starts every key from zero.")
    return _show("KAME — quota", _render(engine.quota_report()))


def _render(report: dict) -> str:
    pools = report.get("pools") or []
    if not pools:
        return ("_No pool seen yet. KAME learns one on the first model call of "
                "a chat._")
    lines = []
    for pool in pools:
        lines.append(f"### `{pool['identity']}` — {pool['ready']}/{pool['total']} ready")
        lines.append("")
        lines.append("| key | state | back in | why | number from | refusals in a row |")
        lines.append("|---|---|---|---|---|---|")
        for key in pool["keys"]:
            lines.append(
                f"| {key['id']} | {key['state']} | {_dur(key.get('seconds_left'))} | "
                f"{key.get('why') or '—'} | {key.get('label') or '—'} | "
                f"{key.get('strikes', 0)}/{key.get('limit', 3)} |"
            )
        lines.append("")
    stats = report.get("stats") or {}
    lines.append(
        f"**This session:** {int(stats.get('ok', 0))} answered · "
        f"{int(stats.get('per_minute', 0))} per-minute · {int(stats.get('daily', 0))} daily · "
        f"{int(stats.get('insufficient_quota', 0))} out of credit · "
        f"{int(stats.get('server', 0))} busy · {int(stats.get('timeout', 0))} timeouts."
    )
    return "\n".join(lines)


def _dur(seconds) -> str:
    try:
        value = float(seconds)
    except (TypeError, ValueError):
        return "—"
    if value <= 0:
        return "—"
    if value < 60:
        return f"{value:.0f}s"
    if value < 3600:
        return f"{value / 60:.0f}m"
    return f"{value / 3600:.1f}h"


def _show(title: str, content: str) -> dict[str, Any]:
    return {"text": "", "effects": [{"type": "show_markdown", "title": title, "content": content}]}


def _toast(message: str, level: str = "success") -> dict[str, Any]:
    return {"text": "", "effects": [{"type": "toast", "message": message, "level": level}]}

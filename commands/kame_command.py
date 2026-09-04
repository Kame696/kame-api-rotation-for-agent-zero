"""`/kame` and `/kame doctor` — v1.6.0.1.

The tool that reads a real run and says whether it looks right used to be a
script in this repository. That is the one place a diagnostic is guaranteed not
to be when it is wanted: a fresh install, another machine, a reinstall, an
assistant that has never seen this code.

Agent Zero v2.11 discovers plugin-contributed slash commands, so it is a command
now, and it answers from inside the process it is describing.

Two forms, deliberately:

    /kame          what is happening right now — build, pools, what is ready
    /kame doctor   the same, plus the whole kind-to-rest table beside how often
                   each kind has actually happened, and a list of the things a
                   person has to do something about

Counts and fingerprints only. Never a key, on any path.
"""

from __future__ import annotations

from typing import Any


# ---------------------------------------------------------------------------
# The kind-to-rest table, written by hand ON PURPOSE.
#
# Derived from the code it would agree with the code by construction and prove
# nothing. Written down, it is a second statement of intent — and
# `tests/test_v1_6_0_1.py` holds the two together, so the day somebody changes a
# constant without meaning to, the disagreement is a failing test rather than a
# surprise in production.
#
# (kind, first rest in seconds, what it means, does waiting help)
# ---------------------------------------------------------------------------
EXPECTED_RESTS = (
    ("timeout", 3.0, "the connection did not happen", "yes — briefly"),
    ("server", 5.0, "the provider is busy; the key is fine", "yes"),
    ("per_minute", 20.0, "a throttle on this credential", "yes — the provider says how long"),
    ("auth", 20.0, "a bare 401, no explanation", "no — but it may not be the key"),
    ("revoked", 20.0, "the provider says this is not a key", "no — it leaves rotation"),
    ("denied", 20.0, "this key may not use THIS model", "no — but only for this model"),
    ("daily", 3600.0, "a daily cap is spent", "yes — this is the one waiting fixes"),
    ("insufficient_quota", 3600.0, "the account allowance is gone", "yes, eventually"),
    ("other", 20.0, "unrecognised", "unknown"),
)


def run(payload: dict[str, Any]) -> dict[str, Any]:
    invocation = payload.get("invocation") or {}
    argument = str(invocation.get("raw_arguments") or "").strip().lower()

    try:
        from usr.plugins.api_rotation_by_kame.kame_engine import pool_report
    except Exception as exc:
        return _toast(
            f"KAME is installed but its engine did not import ({type(exc).__name__}). "
            "That usually means a half-copied plugin directory.",
            level="error",
        )

    try:
        report = pool_report()
    except Exception as exc:
        return _toast(f"KAME could not read its own state: {exc}", level="error")

    doctor = argument.startswith("doctor")
    content = _render(report, doctor=doctor)
    title = "KAME — doctor" if doctor else "KAME — key rotation"
    return {"text": "", "effects": [
        {"type": "show_markdown", "title": title, "content": content}
    ]}


def _render(report: dict, doctor: bool) -> str:
    lines: list[str] = []
    build = report.get("build") or {}
    version = report.get("version", "?")

    # --- which build is actually running ------------------------------------
    # First, because it is the answer to the question people ask second and
    # should have asked first. A version survives a copy that dropped half the
    # package; a fingerprint computed from the files cannot.
    lines.append(f"**KAME {version}** · build `{build.get('fingerprint', '?')}`")
    if build.get("complete") is False:
        lines.append(
            f"> ⚠️ **This copy is incomplete.** Missing: "
            f"`{'`, `'.join(build.get('missing') or [])}`. "
            "Rotation cannot be trusted until the directory is copied whole."
        )
    elif build.get("degraded"):
        lines.append(
            f"> {len(build['degraded'])} optional file(s) absent — one screen or "
            "shield is missing, rotation is unaffected."
        )

    layer = report.get("layer")
    if report.get("active"):
        bound = ", ".join(report.get("bound") or []) or "—"
        lines.append(f"Attached at layer {layer}, wrapping `{bound}`.")
    else:
        # Layer 3 is a SAFE end state — Agent Zero runs exactly as it would with
        # no plugin — and it is also the single most common reason for "the
        # plugin does nothing". Said plainly rather than left to be inferred.
        lines.append(
            "**Not attached.** Agent Zero is making its own calls and KAME is "
            "not choosing keys. This is safe, not broken — but nothing below "
            "will change until it attaches on the next agent start."
        )

    # --- the pools ----------------------------------------------------------
    totals = report.get("totals") or {}
    lines.append("")
    lines.append("## Pools")
    pools = report.get("pools") or []
    if not pools:
        lines.append(
            "_No pool seen yet. KAME learns one on the first model call of a "
            "chat, so an empty list here right after a restart is expected._"
        )
    else:
        lines.append("| pool | ready | resting | left rotation | next back |")
        lines.append("|---|---|---|---|---|")
        for pool in pools:
            eta = pool.get("eta")
            lines.append(
                f"| `{pool.get('identity','')}` | {pool.get('ready',0)}/"
                f"{pool.get('total',0)} | {pool.get('resting',0)} | "
                f"{pool.get('retired',0)} | {_dur(eta)} |"
            )
        lines.append("")
        lines.append(
            f"**{totals.get('ready',0)} of {totals.get('keys',0)} keys can "
            f"answer right now.**"
        )

    # --- what a person has to do something about ----------------------------
    # Deliberately its own section rather than a colour on a row above. A list
    # that is sometimes empty is readable; a table you have to scan for red is
    # not.
    todo: list[str] = []
    for pool in pools:
        gone = [r.get("id", "") for r in (pool.get("keys") or [])
                if r.get("state") == "retired"]
        if gone:
            todo.append(
                f"- `{pool.get('identity','')}` — {', '.join(gone)} left "
                "rotation. **Nothing was deleted**: paste a working key over "
                "the matching one in your `.env` and it returns by itself on "
                "the next successful call."
            )
        being_tried = [f"{r['id']} ({r['strikes']}/{r['limit']})"
                       for r in (pool.get("keys") or [])
                       if r.get("strikes") and r.get("state") != "retired"]
        if being_tried:
            verb = "is" if len(being_tried) == 1 else "are"
            todo.append(
                f"- `{pool.get('identity','')}` — {', '.join(being_tried)} "
                f"refused recently and {verb} **still being tried**. One refusal "
                "is not proof; no action needed yet."
            )
    lines.append("")
    lines.append("## Needs a human")
    lines.extend(todo or ["_Nothing. Every credential is either working or "
                          "waiting out a clock._"])

    if not doctor:
        lines.append("")
        lines.append("_`/kame doctor` adds the full cooldown table and the "
                     "session counters._")
        return "\n".join(lines)

    # --- doctor: the table, beside what actually happened --------------------
    stats = report.get("stats") or {}
    lines.append("")
    lines.append("## What each refusal costs, and how often it happened")
    lines.append("")
    lines.append("| kind | first rest | what it means | does waiting help | seen this session |")
    lines.append("|---|---|---|---|---|")
    for kind, rest, meaning, helps in EXPECTED_RESTS:
        lines.append(
            f"| `{kind}` | {_dur(rest)} | {meaning} | {helps} | "
            f"{int(stats.get(kind, 0))} |"
        )
    lines.append("")
    lines.append(
        "Every kind above except `timeout` climbs a doubling ladder toward one "
        "hour if the same key keeps producing it, and any success resets it. So "
        "a permission that really is permanent reaches an hour by itself, while "
        "one somebody fixes comes back in the seconds it actually took."
    )
    lines.append("")
    lines.append(
        f"**{int(stats.get('ok', 0))} calls answered · "
        f"{int(stats.get('retired', 0))} credential(s) left rotation this "
        "session.**"
    )
    # --- doctor: read, or guessed -------------------------------------------
    # Counting failures answers "is anything going wrong". This answers the
    # question that matters after an upstream change: is KAME still READING
    # these, or is it guessing? A provider that renames a field does not raise
    # anything — it quietly moves every refusal into the generic bucket, and the
    # plugin goes on rotating with a number it invented.
    tally = report.get("tally") or {}
    if tally:
        lines.append("")
        lines.append("## Where each cooldown came from")
        lines.append("")
        lines.append("| pool | failures | the provider said | KAME's own rule | nothing recognised |")
        lines.append("|---|---|---|---|---|")
        worst = 0
        for identity, row in sorted(tally.items()):
            total = int(row.get("total", 0)) or 1
            unread = int(row.get("default", 0))
            worst = max(worst, unread * 100 // total)
            lines.append(
                f"| `{identity}` | {row.get('total', 0)} | "
                f"{row.get('provider', 0)} | {row.get('kame', 0)} | {unread} |"
            )
        lines.append("")
        if worst >= 25:
            lines.append(
                f"> ⚠️ **{worst}% of refusals in the worst pool were not "
                "recognised at all.** One or two is normal. A share this high "
                "usually means a provider changed the shape of its error and "
                "KAME is now guessing every wait — rotation still works, but "
                "the timing is no longer the provider's."
            )
        else:
            lines.append(
                "_A small `nothing recognised` count is normal. A rising share "
                "is the shape of an install that has gone quiet after a "
                "provider changed its error format._"
            )

    lines.append("")
    lines.append(
        "_The cooldown table above is written by hand rather than read from the "
        "code. That is deliberate: derived from the code it would agree with the "
        "code by construction and prove nothing. A test holds the two together._"
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
    return f"{value / 3600:.1f}h".replace(".0h", "h")


def _toast(message: str, *, level: str = "success") -> dict[str, Any]:
    return {"text": "", "effects": [
        {"type": "toast", "message": message, "level": level}
    ]}

"""What a background job of Agent Zero may spend: requests, seconds, waiting.

1.8.2.9. Agent Zero's utility model does the small jobs around a conversation: it names the
chat, recalls and stores memories, compresses history, answers questions about a document.
Until 1.8.2.8 every such call inherited the chat's eternal wait and its backup races, so one
chat title could ask a busy model dozens of times and spend the per-model daily quota the chat
needs. Nobody waits on a title.

A cosmetic job (the chat's name) gets 30 s, 3 requests and never waits for a resting key; every
other job gets 300 s and may wait (Hermes' 6 requests are kept in ``LIMITS``; Agent Zero's engine
bounds an important job by time and by spacing its passes over the keys, not by that count, review
N6); none races or asks keys together. A job that
stops raises a timeout that holds none of the words a host reads as "payment" or "rate limit",
and its caller carries on without the result. The engine budgets only the jobs whose callers it has
read and knows catch that stop (review H1/H2).

Pure functions; the engine applies them to calls that are not the conversation and never to the chat.
No Agent Zero import, no framework: this file also runs by itself in the tests.
"""
from __future__ import annotations

import math
import time
from typing import Any, NamedTuple, Optional

#: A closed list. Any other NAMED job is "important": a wrong guess toward cosmetic would starve a job
#: that matters. (A job the call stack does not name is not budgeted by Agent Zero's engine at all.)
COSMETIC = frozenset({"title_generation", "profile_describer", "background_review"})

FLOOR_S = 5.0
#: After a stop, the same model and class answer "stopped" for this long without a request: the
#: host's own retry of the job (or the next title attempt) costs nothing.
MEMO_S = 30.0

#: Stems of the words a host may match in an error text to decide "payment", "rate limit" or
#: "auth". A name that contains one is shown as "?".
_HOST_WORDS = ("credit", "quota", "billing", "payment", "fund", "balance", "limit", "free", "tier",
               "retry", "again", "reset", "exhaust", "unauthor", "apikey", "api_key", "subscription",
               "budget", "upgrade", "daily", "weekly", "tokens", "usage", "401", "402", "403", "404",
               "429", "afford", "deactivat", "plan", "spending", "overload", "capacity")


class Limit(NamedTuple):
    cap_s: float
    max_requests: int
    may_wait: bool
    default_host_s: float


#: Agent Zero passes no timeout of its own to a utility call, so the default is the cap.
LIMITS = {
    "cosmetic": Limit(cap_s=30.0, max_requests=3, may_wait=False, default_host_s=30.0),
    "important": Limit(cap_s=300.0, max_requests=6, may_wait=True, default_host_s=300.0),
}


class Budget(NamedTuple):
    klass: str
    total_s: float
    max_requests: int
    may_wait: bool


def _seconds(value: Any) -> float:
    """A positive finite number of seconds, or 0.0 when ``value`` is not one (``bool`` is not)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    value = float(value)
    return value if math.isfinite(value) and value > 0 else 0.0


def budget_for(task: str, task_known: bool, host_timeout: Any, user_cap: Any) -> Budget:
    """The budget of one background call.

    ``task_known`` is true only when the call stack named the job; a guess never decides the class.
    ``host_timeout`` is a timeout the host put in the request (Agent Zero puts none);
    ``user_cap`` is the user's own total bound on waiting (0 = none).
    """
    name = str(task or "").strip().lower()
    klass = "cosmetic" if task_known and name in COSMETIC else "important"
    limit = LIMITS[klass]
    host = _seconds(host_timeout) or limit.default_host_s
    total = min(host, limit.cap_s)
    cap = _seconds(user_cap)
    if cap:
        total = min(total, cap)
    return Budget(klass, max(FLOOR_S, total), limit.max_requests, limit.may_wait)


def stop_message(task: str, model: str, tries: int, seconds: float) -> str:
    """The text of the error a stopped background job raises.

    It must hold none of the host's payment, rate-limit or auth words, or the host could take the
    stop for a spent quota. Only identifier characters of the task and model are kept for the
    same reason."""
    def clean(text: Any) -> str:
        kept = "".join(c for c in str(text or "") if c.isalnum() or c in "._-/:")[:60]
        if not kept or any(word in kept.lower() for word in _HOST_WORDS):
            return "?"
        return kept

    return (f"kame: background job {clean(task)} on {clean(model)} timed out after {int(tries)} "
            f"tries in {max(0, int(seconds))}s - its keys are kept for your chat")


#: The suggestion appears only when background jobs are a real share of the best model's requests.
SUGGEST_MIN_SHARE = 0.15
SUGGEST_MIN_SIDE = 20


def _split(identity: Any):
    text = str(identity or "")
    provider, _, name = text.partition(":")
    return provider, name


def suggest(models: Any, primary: Any, chain: Any, written: Any, since: float) -> "Optional[str]":
    """A read-only sentence: which background job to move off the best model, and to which model.

    ``models`` is the engine's per-identity tally (``{identity: {"main", "side", "tasks"}}``);
    ``primary`` is the best model's identity and ``chain`` the owner's own fallback list (identities
    ``provider:name``); ``written(identity)`` gives a model's name as it is shown. The model named is
    ALWAYS a member of that list - one on the best model's provider when there is one (a cosmetic
    job goes to the last such entry, an important one to the first), otherwise the list's own
    last or first entry - and the sentence says KAME changes nothing. ``None`` when there is
    nothing worth saying (under 15% of requests or under 20 background requests).
    """
    try:
        if not primary or not chain:
            return None
        main = side = 0
        tasks: dict = {}
        for identity, row in dict(models or {}).items():
            if identity == primary or str(identity).startswith(str(primary) + ":"):
                main += int(row.get("main", 0))
                side += int(row.get("side", 0))
                for name, count in dict(row.get("tasks") or {}).items():
                    tasks[name] = tasks.get(name, 0) + int(count)
        total = main + side
        if side < SUGGEST_MIN_SIDE or total <= 0 or side / total < SUGGEST_MIN_SHARE or not tasks:
            return None
        task = max(sorted(tasks), key=lambda name: tasks[name])
        provider = _split(primary)[0]
        same = [entry for entry in chain if _split(entry)[0] == provider]
        pool = same or list(chain)
        cosmetic = task.strip().lower() in COSMETIC
        target = pool[-1] if cosmetic else pool[0]
        number = ([primary] + list(chain)).index(target) + 1
        target_provider, target_name = _split(target)
        best = str(written(primary) or _split(primary)[1])
        clock = time.strftime("%H:%M", time.localtime(since))
        return (f"Background jobs used {round(100.0 * side / total)}% of {best}'s requests since {clock} "
                f"({task}: {tasks[task]}). To keep your best model for your chats you could move {task} "
                f"to {target_provider} / {target_name} (number {number} of your list): in Agent Zero's "
                f"model settings, set the Utility model to {target_provider} / {target_name}. "
                "KAME never changes this for you.")
    except Exception:
        return None

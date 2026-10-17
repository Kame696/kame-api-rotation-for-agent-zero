# Agent Zero port of the Hermes 1.8.2.7 `core/pacing.py`, verbatim except where it
# reads its two settings (`SETTINGS`, set by kame_engine).
"""Rate-limit-aware pacing: sliding-window admission.

v1 log-normal pacing added artificial delays. v2 replaced it with pure GCRA,
which is the wrong fit for the providers this pool actually talks to: with a
burst allowance it is unsafe against the *sliding* 60-second windows most
providers enforce (after a burst of L at t=0, GCRA admits again at 60/L
while the provider's window still holds all L -- a guaranteed 429), and with
no burst allowance it adds delay to bursts the provider would have allowed.

This keeps what pacing is *for* -- avoid the 429 before it happens -- with a
sliding-log admission check instead:

* per ``(identity, scope)`` the pacer keeps the timestamps of the requests
  it dispatched (the same evidence "fewest-requests-in-window" sorts by);
* a key is *ready* when the last ``limit`` requests are older than the
  window, so under the limit there is **zero added delay**, and at the
  limit the wait is exactly the minimum the window demands;
* the limit comes from the per-model override setting, then what the pacer
  learned from real 429s, then nothing -- a key whose limit is unknown is
  never paced, because a made-up number is how pacing becomes a delay
  nobody asked for.

``scope`` is the key by default, or a quota group when keys share one
provider project (they share the window too -- pacing them separately is
how a shared limit gets overshot).

References
----------
* Brandur "Rate Limiting, Cells, and GCRA" (what v2 used, and why not)
* Google/Gemini per-minute quota semantics (sliding window)
"""

from __future__ import annotations

import fnmatch
import hashlib
import threading
import time
from collections import deque
from typing import Dict, Optional, Tuple

__all__ = ["Pacer", "PACER", "rpm_from_overrides", "key_labels"]

#: Agent Zero port: the two settings the Hermes pacer reads from its settings
#: module. `key_quota_groups` stays empty here on purpose: one key = one account
#: (the owner's rule, Hermes decisions/0027 s4, hidden there too).
SETTINGS: Dict[str, str] = {"pacing_rpm_overrides": "", "key_quota_groups": ""}


def key_labels(key: str) -> Tuple[str, ...]:
    """Every spelling a person may use for one key in a settings entry.

    The panel shows a key as its fingerprint, ``key:`` plus six hex digits
    (``core.carousel.fingerprint`` — the same digest, repeated here because
    ``carousel`` imports this module). 1.8.2.3 looked a quota-group entry up
    by the raw key, which no person ever types, so no group ever matched.
    Accepted now: the fingerprint as shown, the six digits without the
    ``key:`` prefix, and the raw key (programmatic callers).
    """
    text = str(key or "")
    if not text:
        return ()
    digest = hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:6]
    return (text, "key:" + digest, digest)

#: The sliding window providers meter per minute.
_WINDOW_S = 60.0

#: Provider clock / window-edge slack. A provider that refuses at 60.0s
#: sharp would otherwise be fought at exactly the boundary.
_SKEW_S = 0.5


def _eff_limit(limit: int) -> int:
    """A request of margin below the stated limit: the refusal that costs a
    key its rest is more expensive than the one request the margin forgoes.
    Below 5 the margin disappears -- a limit of 2 paced to 1 halves
    throughput."""
    return limit - 1 if limit >= 5 else limit


#: Log cap per scope. No free tier has an RPM above this.
_MAX_LOG = 1024


def rpm_from_overrides(raw: str, provider: str, model: str) -> Optional[int]:
    """The RPM named for one ``provider:model`` in the override setting.

    Entries are ``pattern=rpm``, comma separated, where ``pattern``
    matches ``provider:model`` with shell wildcards (``gemini:gemini-*``,
    ``groq:*``). First match wins, in the order written -- a person puts the
    specific entry first and the broad one after. Malformed entries are
    skipped; one bad line does not disable the rest.
    """
    text = str(raw or "").strip()
    if not text:
        return None
    subject = f"{str(provider or '?').lower()}:{str(model or '?').lower()}"
    for entry in text.split(","):
        entry = entry.strip()
        if not entry or "=" not in entry:
            continue
        pattern, _, value = entry.partition("=")
        try:
            rpm = int(value.strip())
        except ValueError:
            continue
        if rpm <= 0:
            continue
        if fnmatch.fnmatch(subject, pattern.strip().lower()):
            return min(rpm, _MAX_LOG)
    return None


class Pacer:
    """Sliding-window admission per ``(identity, scope)``."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # (identity, scope) -> deque of monotonic dispatch times
        self._log: Dict[Tuple[str, str], "deque[float]"] = {}
        # (identity, scope) -> learned/known RPM
        self._rpm: Dict[Tuple[str, str], int] = {}
        # (identity, scope) -> monotonic time before which nothing may be
        # dispatched, set from a provider's "remaining=0, reset=Xs" answer.
        self._not_before: Dict[Tuple[str, str], float] = {}
        # quota-group memberships set by a caller (key -> group), consulted
        # per stamp so a change applies immediately.
        self._groups: Dict[str, str] = {}
        # 1.8.2.4: memberships read from the ``key_quota_groups`` setting,
        # keyed by the label as typed (lower case). Rebuilt on every sync, so
        # deleting an entry in the panel takes it out — 1.8.2.3 only ever
        # added to the map.
        self._setting_groups: Dict[str, str] = {}
        # 1.8.2.3: per-identity resolution of the settings overrides,
        # re-read on a short clock so a panel edit lands without a restart
        # and the hot path pays one dict read when nothing changed.
        self._synced_at: Dict[str, float] = {}
        self._sync_rpm: Dict[str, Optional[int]] = {}

    # -- configuration ----------------------------------------------------

    def set_group(self, key: str, group: str) -> None:
        """Keys in one quota group share one window."""
        with self._lock:
            if group:
                self._groups[str(key)] = str(group)
            else:
                self._groups.pop(str(key), None)

    def _scope(self, key: str) -> str:
        """The window this key's requests are counted in. Caller holds the lock."""
        group = self._groups.get(key)
        if group:
            return group
        if self._setting_groups:
            for label in key_labels(key):
                group = self._setting_groups.get(label.lower())
                if group:
                    return group
        return key

    def same_scope(self, a: str, b: str) -> bool:
        """Whether two keys draw on one quota (same key, or one named group)."""
        with self._lock:
            return self._scope(a) == self._scope(b)

    def note_limit(self, identity: str, scope: str, rpm: int) -> None:
        """Record the RPM that governs this scope (learned or header-stated)."""
        with self._lock:
            if rpm and rpm > 0:
                self._rpm[(identity, scope)] = min(int(rpm), _MAX_LOG)

    def note_not_before(self, identity: str, scope: str, seconds: float) -> None:
        """A provider answer of "none left, resets in X seconds"."""
        if seconds is None or seconds < 0:
            return
        now = time.monotonic()
        with self._lock:
            key = (identity, scope)
            self._not_before[key] = max(self._not_before.get(key, 0.0), now + float(seconds))

    def on_minute_429(self, identity: str, scope: str) -> None:
        """Learn the limit from a refused request.

        The window that just refused held at least what this scope
        dispatched in the last minute, so the true limit is at most one
        less than that -- and a learned number is never raised, so a
        shared-project refusal cannot inflate what a key alone may do.
        """
        now = time.monotonic()
        key = (identity, scope)
        with self._lock:
            log = self._log.get(key)
            if not log:
                return
            recent = sum(1 for t in log if now - t <= _WINDOW_S + _SKEW_S)
            inferred = max(1, recent - 1)
            known = self._rpm.get(key)
            if known is None or inferred < known:
                self._rpm[key] = inferred
            # And the window itself is spent: wait for the oldest to age out.
            # 1.8.2.4: measured against the limit just learned — 1.8.2.3
            # compared with the number from *before* this refusal.
            if len(log) >= _eff_limit(self._rpm[key]):
                oldest = log[0]
                self._not_before[key] = max(
                    self._not_before.get(key, 0.0), oldest + _WINDOW_S + _SKEW_S
                )

    # -- the admission check ----------------------------------------------

    def ready_at(self, identity: str, key: str, *, rpm: Optional[int] = None,
                 now: Optional[float] = None) -> float:
        """Monotonic time from which this key may be dispatched.

        Never later than the window demands. An unknown limit means
        ``max(not_before, now)`` -- no pacing at all, because an invented
        number is a delay nobody asked for.
        """
        now = time.monotonic() if now is None else now
        with self._lock:
            scope = self._scope(key)
            pair = (identity, scope)
            limit = rpm if rpm is not None else self._rpm.get(pair)
            if limit is None:
                limit = getattr(self, "_identity_override_rpm", {}).get(identity)
            floor_t = self._not_before.get(pair, 0.0)
            if limit is None:
                return max(floor_t, now)
            eff = _eff_limit(int(limit))
            log = self._log.get(pair)
            if log is not None:
                while log and log[0] <= now - _WINDOW_S - _SKEW_S:
                    log.popleft()
                if len(log) < eff:
                    return max(floor_t, now)
                # The eff-th most recent dispatch ages out of the window at
                # its timestamp + window: that is the exact earliest next.
                return max(floor_t, log[-eff] + _WINDOW_S + _SKEW_S)
            return max(floor_t, now)

    def is_ready(self, identity: str, key: str, *, now: Optional[float] = None) -> bool:
        """Whether this key can be dispatched right now."""
        return self.ready_at(identity, key, now=now) <= (time.monotonic() if now is None else now)

    def time_until_ready(self, identity: str, key: str, *, now: Optional[float] = None) -> float:
        """Seconds until this key is ready. Zero if already ready."""
        now = time.monotonic() if now is None else now
        return max(0.0, self.ready_at(identity, key, now=now) - now)

    def soonest(self, identity: str, keys, *, now: Optional[float] = None) -> Tuple[Optional[str], float]:
        """The key ready soonest, and how long until it is.

        Returns ``(key, wait_seconds)``; ``(None, 0.0)`` when ``keys`` is
        empty. A wait of 0 means a key is ready now.
        """
        if not keys:
            return None, 0.0
        now = time.monotonic() if now is None else now
        best_key, best_wait = None, float("inf")
        for k in keys:
            wait = self.time_until_ready(identity, k, now=now)
            if wait < best_wait:
                best_key, best_wait = k, wait
        return best_key, (0.0 if best_wait == float("inf") else best_wait)

    # -- the dispatch stamp ------------------------------------------------

    def stamp(self, identity: str, key: str, *, now: Optional[float] = None) -> None:
        """Record one dispatch on this key's scope -- called by the transport
        on the success paths, which is what makes the log real traffic."""
        now = time.monotonic() if now is None else now
        with self._lock:
            scope = self._scope(key)
            pair = (identity, scope)
            log = self._log.get(pair)
            if log is None:
                log = self._log[pair] = deque(maxlen=_MAX_LOG)
            log.append(now)
            # 1.8.2.4: a learned limit can be wrong in the other direction
            # too. A refusal caused by *other* keys of the same project
            # teaches this key a number far below its own; 1.8.2.3 never
            # raised it again until a restart. Requests this scope sent and
            # the provider answered inside one window are proof the limit is
            # at least that many — the provider's own evidence, never a
            # guess, and never above an RPM the owner set by hand.
            learned = self._rpm.get(pair)
            if learned is not None:
                answered = sum(1 for t in log if now - t <= _WINDOW_S)
                cap = getattr(self, "_identity_override_rpm", {}).get(identity)
                if answered > learned and (cap is None or answered <= cap):
                    self._rpm[pair] = answered

    # -- settings sync (called from carousel.select, throttled) -----------

    _SYNC_EVERY_S = 15.0

    def sync_identity_settings(self, identity: str) -> None:
        """Resolve the RPM override and quota-group settings for one identity.

        Called from the selection path on a short clock: a panel edit to
        ``pacing_rpm_overrides`` or ``key_quota_groups`` applies within
        seconds, and when nothing changed the cost is one dictionary read.

        An override RPM is taught with ``note_limit``, which only ever
        *lowers* a learned number — a real 429 outranks a wish. Quota
        groups are keyed by the short key labels the panel shows, exactly
        as the setting documents.
        """
        now = time.monotonic()
        if now - self._synced_at.get(identity, 0.0) < self._SYNC_EVERY_S:
            return
        # Agent Zero port: the engine sets SETTINGS from the plugin settings.
        try:
            provider, _, model = str(identity).partition(":")
            rpm = rpm_from_overrides(SETTINGS.get("pacing_rpm_overrides", ""), provider, model)
            raw_groups = SETTINGS.get("key_quota_groups", "")
        except Exception:
            self._synced_at[identity] = now
            return
        with self._lock:
            self._synced_at[identity] = now
            if rpm:
                # An override is knowledge about the provider, not about one
                # key: teach every scope of this identity.
                for pair in list(self._rpm.keys()):
                    if pair[0] == identity and (rpm < self._rpm[pair]):
                        self._rpm[pair] = rpm
                self._sync_rpm[identity] = rpm
            fresh: Dict[str, str] = {}
            for entry in str(raw_groups or "").split(","):
                entry = entry.strip()
                if not entry or "=" not in entry:
                    continue
                label, _, group = entry.partition("=")
                label, group = label.strip().lower(), group.strip()
                if label and group:
                    fresh[label] = group
            self._setting_groups = fresh
        # The learned RPM for scopes seen later must start from the override
        # too: keep it as the identity's known limit until a 429 says less.
        if rpm:
            self._identity_override_rpm = getattr(self, "_identity_override_rpm", {})
            self._identity_override_rpm[identity] = rpm

    def clear(self) -> None:
        with self._lock:
            self._log.clear()
            self._rpm.clear()
            self._not_before.clear()
            self._groups.clear()
            self._setting_groups.clear()

    def snapshot(self) -> Dict[str, object]:
        """For the panel: per-scope limit, window occupancy, next-free.

        Computes inline under the one lock — calling :meth:`ready_at` here
        would re-enter the same lock and deadlock (the bug the snapshot
        test caught on its first run).
        """
        now = time.monotonic()
        out = {}
        with self._lock:
            for pair in list(self._log.keys()):
                identity, scope = pair
                limit = self._rpm.get(pair)
                if limit is None:
                    limit = getattr(self, "_identity_override_rpm", {}).get(identity)
                log = self._log.get(pair) or ()
                recent = sum(1 for t in log if now - t <= _WINDOW_S)
                floor_t = self._not_before.get(pair, 0.0)
                ready = max(floor_t, now)
                if limit is not None and len(log) >= _eff_limit(int(limit)):
                    eff = _eff_limit(int(limit))
                    ready = max(ready, log[-eff] + _WINDOW_S + _SKEW_S)
                out[f"{identity}|{scope}"] = {
                    "rpm": limit,
                    "in_window": recent,
                    "next_free_s": round(max(0.0, ready - now), 1),
                }
        return out


#: Module-level singleton. Kept under the historical name -- the transport
#: and carousel import ``PACER`` and use ``is_ready`` (selection) and
#: ``stamp`` (success paths).
PACER = Pacer()

# Agent Zero port of the Hermes 1.8.2.7 `core/stitch.py`, verbatim.
"""Joining a cut answer to its continuation without printing anything twice.

When a provider closes the stream in the middle of a sentence, the user has
already seen text. Every version of this plugin before 1.1.1 stopped there and
handed the failure back to the host, which appends a synthetic
``[System: The previous response was cut off…]`` row and asks the model to
carry on — visible, ugly, and (because that row moves the server's message
ordinal without moving the client's) the cause of rewind and edit refusing
later in the same session.

The alternative is to continue the answer here, on another key, and show the
user one unbroken response. The only hard part is the seam:

* a model asked to continue from a prefill usually **repeats a few words** of
  it before adding anything new — that overlap must not reach the screen;
* a model may instead **start the whole answer again** from the first word,
  which is the same problem an order of magnitude bigger;
* and it may do neither, picking up mid-word, in which case nothing at all
  should be trimmed.

This module decides which of the three happened and how much of the incoming
text to drop. It is pure: no Hermes, no threads, no clock. That is what lets
the seam be tested with a synthetic stream rather than with a flaky provider.

**What it compares.** Whitespace and case are ignored, because a model that
resumes rarely reproduces the exact spacing of the text it is continuing —
``"the cat"`` and ``"The  cat"`` are the same words and a comparison that says
otherwise leaves the repetition on screen. Positions are mapped back to the
original string so the text that is forwarded keeps its own spacing intact.

**What it refuses to do.** It never invents text, never reorders it, and never
drops anything it has not matched character for character against what the
user already saw. When the continuation stops matching, forwarding resumes
immediately at that point: a seam that reads slightly oddly is a bad sentence,
while a dropped paragraph is a lost answer.
"""

from __future__ import annotations

from typing import List, Optional, Tuple

#: How much of the continuation is held back before deciding what to trim.
#: Counted in comparable (whitespace-free) characters. Large enough that a
#: model repeating a phrase before continuing is caught whole; small enough
#: that the first visible text arrives in well under a second on any provider
#: that is streaming at all.
PROBE_CHARS = 400

#: Shorter than this and a "match" is a coincidence. Two English sentences
#: share short suffixes all the time — ``"tion"``, ``" the "`` — and trimming
#: on one of those would eat real text at the seam.
MIN_OVERLAP = 8

#: How much of the continuation has to match the *beginning* of what was
#: already shown before this is read as "the model started the answer again".
#: Above the coincidence floor by a wide margin: this decision can suppress
#: thousands of characters, so it has to be earned.
RESTART_HEAD = 24


def _comparable(text: str) -> Tuple[str, List[int]]:
    """``("thecatsat", [0, 1, 2, …])`` — the text to match on, and where it came from.

    The second half is what makes the first half safe. Matching happens on a
    string with the whitespace removed and the case folded, and every
    conclusion it reaches has to be expressed as an index into the *original*
    text, or the trim would corrupt the spacing of the words it keeps.
    """
    out: List[str] = []
    positions: List[int] = []
    for index, character in enumerate(text or ""):
        if character.isspace():
            continue
        out.append(character.lower())
        positions.append(index)
    return "".join(out), positions


def _tail_overlap(seen: str, fresh: str) -> int:
    """How many comparable characters of ``fresh`` repeat the end of ``seen``.

    The longest answer wins, which is the conservative direction: a longer
    match means more repetition removed, and a match that is long is a match
    that cannot have happened by accident. Zero when nothing above
    :data:`MIN_OVERLAP` lines up — the ordinary case for a model that resumes
    mid-word.

    A full restart is caught here too whenever the probe is long enough to
    contain the whole of ``seen``: ``fresh`` beginning with all of ``seen``
    makes ``seen`` its own suffix, and the loop finds it at ``k == len(seen)``.
    """
    limit = min(len(seen), len(fresh))
    for size in range(limit, MIN_OVERLAP - 1, -1):
        if seen.endswith(fresh[:size]):
            return size
    return 0


def _looks_like_a_restart(seen: str, fresh: str) -> bool:
    """Whether ``fresh`` is the answer beginning again rather than continuing.

    Only asked when the tail comparison found nothing, and only answerable
    when there is enough of ``fresh`` to ask with — a model that happens to
    open its continuation with the same four words the answer opened with is
    not restarting, it is agreeing with itself.
    """
    if len(fresh) < RESTART_HEAD or len(seen) < RESTART_HEAD:
        return False
    return seen.startswith(fresh[:RESTART_HEAD])


class Stitcher:
    """Trims one continuation as it streams, so the seam never reaches the user.

    Fed the raw deltas of the resumed call; returns the part of each that is
    safe to show. The first :data:`PROBE_CHARS` are held back — that is the
    whole cost of the feature, and it buys the only look at the continuation
    that can tell repetition from new text.

    Three outcomes, decided once:

    * **continuation** — the probe repeats the end of what was seen. Everything
      up to the end of that repetition is dropped, the rest is forwarded, and
      every later delta passes through untouched.
    * **restart** — the probe repeats the *beginning*. The stitcher then drops
      exactly as many characters as were already shown, checking each one
      against what it is supposed to be repeating, and starts forwarding the
      moment the two disagree or the budget runs out.
    * **new text** — neither. Nothing is dropped.

    ``flush`` exists because a continuation can be shorter than the probe: a
    model finishing the last four words of a sentence never reaches
    :data:`PROBE_CHARS`, and without a flush its answer would be held back for
    ever.
    """

    __slots__ = (
        "_seen",
        "_seen_c",
        "_probe",
        "_buffer",
        "_resolved",
        "_skip",
        "_skip_at",
        "_boundary",
        "forwarded",
        "mode",
    )

    def __init__(self, seen: str, *, probe_chars: int = PROBE_CHARS) -> None:
        #: Everything the user has been shown so far this turn, and the text
        #: the continuation was prefilled with — the two are the same string,
        #: which is exactly why the seam can be computed at all.
        self._seen = seen or ""
        # Folded once. A restart skips through this comparison one delta at a
        # time, and re-folding a long answer on every delta would make the
        # cost of the seam grow with the square of the answer's length.
        self._seen_c, _ = _comparable(self._seen)
        self._probe = max(0, int(probe_chars))
        self._buffer = ""
        self._resolved = False
        #: Comparable characters still to be dropped in restart mode.
        self._skip = 0
        #: How far into ``seen`` the restart check has verified.
        self._skip_at = 0
        # Only consumed after a proven overlap/restart. Formatting not already
        # displayed stays intact, including a different newline/indent sequence.
        self._boundary = self._seen[len(self._seen.rstrip()):]
        #: Everything this stitcher has let through, in order.
        self.forwarded = ""
        #: ``""`` until resolved, then ``continuation`` / ``restart`` / ``new``.
        self.mode = ""

    # -- feeding ---------------------------------------------------------

    def feed(self, chunk: str) -> str:
        """Take one delta; return the part of it that should be displayed."""
        if not chunk:
            return ""
        if not self._seen:
            # Nothing to overlap with. A stitcher with no history is a
            # pass-through, which is what makes it safe to install
            # unconditionally.
            self._resolved = True
            self.mode = self.mode or "new"
            return self._emit(chunk)
        if self._resolved:
            return self._emit(self._after_resolution(chunk))
        self._buffer += chunk
        comparable, _ = _comparable(self._buffer)
        if len(comparable) < self._probe:
            return ""
        return self._emit(self._resolve())

    def flush(self) -> str:
        """Decide on whatever is still buffered. Returns the last text to show.

        Called when the continuation ends — normally, or because it failed.
        After this the stitcher has forwarded everything it will ever forward.
        """
        if self._resolved:
            return ""
        return self._emit(self._resolve())

    # -- the decision ----------------------------------------------------

    def _resolve(self) -> str:
        buffered, self._buffer = self._buffer, ""
        self._resolved = True
        seen_c = self._seen_c
        fresh_c, fresh_at = _comparable(buffered)

        # Equality of the WHOLE displayed answer is stronger evidence than a
        # short suffix overlap. Without this, an unchanged tiny stub looks
        # productive forever under progress-based continuation.
        if fresh_c and fresh_c == seen_c:
            self.mode = "continuation"
            return self._trim_boundary(buffered[fresh_at[-1] + 1 :])

        overlap = _tail_overlap(seen_c, fresh_c)
        if overlap:
            self.mode = "continuation"
            # ``+1`` because the map holds the index of the last repeated
            # character, and the text kept starts after it.
            return self._trim_boundary(buffered[fresh_at[overlap - 1] + 1 :])

        if _looks_like_a_restart(seen_c, fresh_c):
            self.mode = "restart"
            self._skip = len(seen_c)
            self._skip_at = 0
            return self._after_resolution(buffered)

        self.mode = "new"
        self._boundary = ""
        return buffered

    def _trim_boundary(self, text: str) -> str:
        """Consume only identical trailing whitespace that was already shown.

        A boundary can arrive in several deltas. Keep the unconsumed part until
        the next delta, but stop matching at the first genuinely new character.
        """
        if not self._boundary or not text:
            return text
        matched = 0
        while matched < min(len(self._boundary), len(text)) and self._boundary[matched] == text[matched]:
            matched += 1
        self._boundary = self._boundary[matched:] if matched == len(text) else ""
        return text[matched:]

    def _after_resolution(self, chunk: str) -> str:
        """Everything after the decision: pass through, or keep skipping."""
        if self._skip <= 0:
            return self._trim_boundary(chunk)
        seen_c = self._seen_c
        fresh_c, fresh_at = _comparable(chunk)
        for index, character in enumerate(fresh_c):
            if self._skip <= 0:
                return chunk[fresh_at[index] :]
            expected = seen_c[self._skip_at] if self._skip_at < len(seen_c) else ""
            if character != expected:
                # The repetition stopped being one. Whatever this is, it is
                # not text the user has already read, so it goes on screen
                # from here — a seam that reads oddly costs a sentence, and
                # skipping on costs the answer.
                self._skip = 0
                return chunk[fresh_at[index] :]
            self._skip -= 1
            self._skip_at += 1
            if self._skip == 0:
                # Preserve new separators after the last repeated character;
                # the previous next-character slice swallowed this gap.
                return self._trim_boundary(chunk[fresh_at[index] + 1 :])
        return ""

    def _emit(self, text: str) -> str:
        if text:
            self.forwarded += text
        return text


def stitch_text(seen: str, fresh: str) -> str:
    """The part of a whole continuation that is not a repeat of ``seen``.

    The same decision :class:`Stitcher` makes on a live stream, made in one
    go. Implemented *through* the stitcher rather than beside it so the two
    can never disagree about the same seam — the one drift that would show up
    as text on screen differing from text in the history.
    """
    stitcher = Stitcher(seen)
    return stitcher.feed(fresh or "") + stitcher.flush()


def prefill_message(text: str, role: str = "assistant") -> dict:
    """The trailing message that asks a provider to continue rather than restart.

    A trailing assistant turn is the one continuation mechanism every
    OpenAI-compatible endpoint and the Anthropic Messages API both understand,
    and it needs no instruction text of its own — an instruction would end up
    quoted back by some models and stapled to the middle of the user's answer.

    Trailing whitespace is stripped because several providers reject a prefill
    that ends in one, and because the model supplies its own spacing.
    """
    return {"role": role, "content": (text or "").rstrip()}


#: What the user turn says when a prefill is not allowed. Kept short and
#: imperative: every word of it is text the model has been handed, and a long
#: instruction is a long thing for a model to acknowledge before continuing.
CONTINUE_INSTRUCTION = (
    "Continue your previous message from exactly where it stopped. Do not "
    "repeat any part of it, do not start it again, and do not write any "
    "preamble — write only the text that comes next."
)

#: How a provider says it will not take a trailing assistant turn. Gemini's
#: native API is the one that matters: it answers a prefilled request with
#: ``HTTP 400 (INVALID_ARGUMENT): Requests ending with a model turn are not
#: supported``, which is a refusal of the *shape* of the request — so no key,
#: and no amount of rotation, can make it succeed.
_PREFILL_REFUSALS = (
    "ending with a model turn",
    "must end with a user",
    "last message must be",
    "last content must be",
    # Anthropic, for models that take no prefill at all and for a prefill under
    # extended thinking (the last assistant turn must then open with a thinking
    # block, which a continuation cannot honestly supply).
    "does not support assistant message prefill",
    "must start with a thinking block",
)


def refuses_prefill(message: object) -> bool:
    """Whether this failure says the provider will not continue from a prefill.

    Matched on the provider's own words rather than on a status code: a 400 is
    also what an unknown parameter and a malformed tool schema look like, and
    those must keep being raised rather than retried in a different shape.
    """
    lowered = str(message or "").lower()
    return any(mark in lowered for mark in _PREFILL_REFUSALS)


def continuation(text: str, *, trailing_user: bool = False) -> List[dict]:
    """The messages to append so the model carries on from ``text``.

    Two shapes, because providers disagree about what a request may end with:

    * a **trailing assistant turn** — the prefill. Every OpenAI-compatible
      endpoint and the Anthropic Messages API continue from it, and it needs no
      instruction of its own, so nothing can leak into the answer.
    * the same assistant turn **followed by a short user instruction**, for a
      provider that refuses to be handed a request ending in its own voice.
      The answer so far is still in the conversation, which is what the seam is
      computed against; only the last turn changes hands.
    """
    partial = prefill_message(text)
    if not trailing_user:
        return [partial]
    return [partial, {"role": "user", "content": CONTINUE_INSTRUCTION}]


def resumable(api_kwargs: object) -> Optional[List[dict]]:
    """The message list to continue from, or ``None`` when there is not one.

    A request whose shape this module does not recognise is a request it
    declines to rewrite. There is no partial credit here: a malformed
    continuation would be sent to a provider as the user's next turn.
    """
    if not isinstance(api_kwargs, dict):
        return None
    messages = api_kwargs.get("messages")
    if not isinstance(messages, list) or not messages:
        return None
    if not all(isinstance(message, dict) for message in messages):
        return None
    return messages

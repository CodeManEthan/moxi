"""memory's recall sender: the name a recall's answer comes back to its session under,
on the routes that answer by comms.

**The word.** memory declares the word `subconscious` to comms by an entry point in
comms' group `moxi.comms.scoped_senders`, whose target is `SUBCONSCIOUS`, a line of
plain text. comms forms each session's recall sender from the session's comms name and
the word (`fleet-lead-subconscious` for `fleet-lead`, `builder-subconscious@acme` for
`builder@acme`); memory never builds the name itself. The sender reaches its parent
alone, nobody can mail it, and no roster lists it.

**Whether one can send (`way_back`).** A route that answers by comms runs in a session
only while comms is installed with scoped senders (comms' interface 3), the session has
a comms name, and memory's scoped sender for that name can send now. Otherwise the
reason (`REASONS`) says which failed, and the route is off for that session: other
sessions' recall senders are unaffected.

**One send (`send`).** The answer goes under the session's recall sender to the
session's comms name, read when it sends, with `interrupt` and no expiry, cut at
`MAX_CHARS` on a line boundary. comms either takes it or says why not; memory holds an
answer comms did not take and sends it again (`moxi_memory.recall_stream`).

**Who sent a delivery (`from_recalls_only`, `recall_senders`).** A sender name is a
recall of this session when comms reads it back, now, as an active scoped sender of
memory's word whose parent is this session's comms name. comms reads that from its
store without its daemon, so a listener can ask at each prompt. A name of the form that
comms does not read back as active (a session or a program held it first, or the word
is not declared) is another agent's.

Standard library at import; comms is reached through `moxi_memory.links` at use.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import NamedTuple

#: The word memory declares to comms, and the module that declares it.
WORD = "subconscious"
MODULE = "memory"
#: What the word is, as comms' entry point group reads it.
SUBCONSCIOUS = "memory's recalls, sent back to the session whose prompt started them"

#: Why a session's routes that answer by comms are off.
NO_COMMS = "comms is not installed"
NO_SCOPED = "comms without scoped senders"
NO_NAME = "no comms name for this session"
CONFLICT = "recall sender in conflict"
UNDECLARED = "recall sender not declared to comms"
UNREADABLE = "comms' store can't be read"
FAILING = "comms can't be asked"
REASONS = (NO_COMMS, NO_SCOPED, NO_NAME, CONFLICT, UNDECLARED, UNREADABLE, FAILING)
#: comms' `ScopedState.reason`, read as memory's.
_STATE_REASONS = {"conflict": CONFLICT, "undeclared": UNDECLARED,
                  "unreadable": UNREADABLE}

#: The most characters one sent answer holds.
MAX_CHARS = 4000


class WayBack(NamedTuple):
    """Whether a session's answers can go back by comms now: its comms name
    (`parent`), its recall sender's name (`name`, when comms knows it), and why not
    (`why`, one of `REASONS`, None when they can), with comms' own words (`detail`)."""

    parent: str | None
    name: str | None
    why: str | None
    detail: str | None = None

    @property
    def ok(self) -> bool:
        return self.why is None


class Sent(NamedTuple):
    """One send's outcome: `taken` when comms posted it, else the refusal (`reason`)."""

    taken: bool
    reason: str | None = None
    message_id: str | None = None


def link():
    """memory's link to comms with scoped senders (comms' interface 3)."""
    from . import links

    return links.comms_scoped()


def _no_link(found) -> WayBack:
    from . import links

    if found.state == links.FAILING:
        return WayBack(None, None, FAILING, found.reason)
    if links.NO_INTERFACE in (found.reason or ""):
        return WayBack(None, None, NO_SCOPED, found.reason)
    return WayBack(None, None, NO_COMMS, found.reason)


def parent_of(session_id: str, *, api=None, timeout_s: float = 5.0) -> str | None:
    """The session's comms name now, from comms' `owner`, or None when it has none or
    comms can't say."""
    if api is None:
        found = link()
        if not found.ready:
            return None
        api = found.api
    try:
        return api.owner(session_id, timeout_s=timeout_s).identity or None
    except Exception:  # noqa: BLE001 - a name comms can't read is none
        return None


def sender_state(parent: str, *, api=None) -> WayBack:
    """Whether memory's scoped sender for comms name `parent` can send now."""
    if api is None:
        found = link()
        if not found.ready:
            return _no_link(found)._replace(parent=parent)
        api = found.api
    try:
        state = api.scoped_state(MODULE, parent, word=WORD)
    except ValueError as exc:
        return WayBack(parent, None, NO_NAME, str(exc))
    except Exception as exc:  # noqa: BLE001 - a comms that can't be asked can't send
        return WayBack(parent, None, FAILING, f"{type(exc).__name__}: {exc}")
    if state.can_send:
        return WayBack(parent, state.qualified, None)
    why = _STATE_REASONS.get(str(state.reason), FAILING)
    return WayBack(parent, state.qualified, why, state.why)


def way_back(session_id: str, *, parent: str | None = None) -> WayBack:
    """Whether session `session_id`'s answers can go back by comms now. `parent` is its
    comms name when the caller already read it."""
    found = link()
    if not found.ready:
        return _no_link(found)
    if parent is None:
        parent = parent_of(session_id, api=found.api)
    if not parent:
        return WayBack(None, None, NO_NAME)
    return sender_state(parent, api=found.api)


def message_text(answer: str) -> str:
    """`answer` as sent: whole when it fits `MAX_CHARS`, else cut at the last line
    boundary inside it."""
    text = str(answer).strip()
    if len(text) <= MAX_CHARS:
        return text
    cut = text.rfind("\n", 0, MAX_CHARS + 1)
    return text[:cut if cut > 0 else MAX_CHARS].rstrip()


def send(session_id: str, answer: str, *, parent: str | None = None,
         timeout_s: float = 30.0) -> Sent:
    """Send `answer` under the session's recall sender to the session's comms name (read
    now, unless `parent` gives it), with `interrupt` and no expiry. Never raises."""
    found = link()
    if not found.ready:
        return Sent(taken=False, reason=_no_link(found).why)
    if parent is None:
        parent = parent_of(session_id, api=found.api)
    if not parent:
        return Sent(taken=False, reason=NO_NAME)
    try:
        result = found.api.send_scoped(MODULE, parent, parent, message_text(answer),
                                       word=WORD, interrupt=True, expires="never",
                                       timeout_s=timeout_s)
    except Exception as exc:  # noqa: BLE001 - a send that raised is one comms did not take
        return Sent(taken=False, reason=f"{type(exc).__name__}: {exc}")
    if getattr(result, "message_id", None):
        return Sent(taken=True, message_id=str(result.message_id))
    refusal = getattr(result, "refusal", None)
    if refusal is not None:
        return Sent(taken=False, reason=f"{refusal.code}: {refusal.text}")
    return Sent(taken=False, reason=str(getattr(result, "outcome", "not sent")))


def is_recall_sender(name: str, parent: str, *, api=None) -> bool:
    """Whether sender `name` is, now, the active scoped sender of memory's word whose
    parent is comms name `parent`."""
    if api is None:
        found = link()
        if not found.ready:
            return False
        api = found.api
    try:
        sender = api.scoped_sender(name)
    except Exception:  # noqa: BLE001 - a name comms can't read back is no recall's
        return False
    return (sender is not None and sender.module == MODULE and sender.word == WORD
            and sender.parent == parent)


def recall_senders(senders: Iterable[str] | None, parent: str | None, *,
                   api=None) -> list[str]:
    """The names of `senders` that are recall senders of the session named `parent`."""
    names = [str(s) for s in (senders or ()) if s]
    if not names or not parent:
        return []
    if api is None:
        found = link()
        if not found.ready:
            return []
        api = found.api
    return [n for n in names if is_recall_sender(n, parent, api=api)]


def session_parent(session_id: str, env=None, *, timeout_s: float = 2.0) -> str | None:
    """The session's comms name, read from inside the session: proven by comms' claim
    socket that `env` names, which needs no daemon; else, when that gives none, from
    comms' `owner`, bounded by `timeout_s`. None when neither gives one."""
    found = link()
    if not found.ready:
        return None
    try:
        name = found.api.caller_identity(env)
    except Exception:  # noqa: BLE001 - a claim that can't be read proves no name
        name = None
    return name or parent_of(session_id, api=found.api, timeout_s=timeout_s)


def from_recalls_only(senders: Iterable[str] | None, parent: str | None, *,
                      api=None) -> bool:
    """Whether a delivery from `senders` comes only from recalls of the session named
    `parent`: one sender at least, each a recall sender of that session."""
    names = [str(s) for s in (senders or ()) if s]
    return bool(names) and len(recall_senders(names, parent, api=api)) == len(names)


__all__ = ["WORD", "MODULE", "SUBCONSCIOUS", "NO_COMMS", "NO_SCOPED", "NO_NAME", "CONFLICT",
           "UNDECLARED", "UNREADABLE", "FAILING", "REASONS", "MAX_CHARS", "WayBack", "Sent",
           "link", "parent_of", "sender_state", "way_back", "message_text", "send",
           "is_recall_sender", "recall_senders", "session_parent", "from_recalls_only"]

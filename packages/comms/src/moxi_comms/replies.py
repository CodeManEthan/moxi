"""Asks and their answers: the one rule for whether a `[reply-once]` ask still waits.

The daemon's `awaiting` (the names a bound name waits on) and the page's mail-state read
(what waits for the operator's reply, and the open asks between agents) decide by these
functions, so the page and the daemon never disagree on whether an ask was answered.

The rule reads only line zero's facts and attribution: who wrote a message, its tags,
its posture and what it is threaded to. Nothing here reads a body or asks the daemon.

- **An ask** is a verified `[reply-once]` message within `ASK_TTL` (`is_ask`).
- **An answer** to it is a later message (by id) by the answerer with a tag naming the
  asker by the one rule (`scoping.tag_matches`), threaded to the ask (`re:` its id) of
  any posture, or not threaded to it with any posture but `[no reply]` (`answered`).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from . import constants
from .machine_line import POSTURE_NONE, POSTURE_ONCE
from .scoping import tag_matches


@dataclass
class SentEntry:
    """One `#names` message as the reply rule reads it; the daemon's sent index holds
    these.

    `tags` are the recipient tags exactly as written on line zero: the rule keys on
    nothing `resolve` computes, because a bare tag qualifies differently live and at boot.
    `verified` is the message's attribution being `verified`.
    """

    id: str
    author: str
    tags: tuple
    posture: str
    reply_to: str | None
    at: float
    verified: bool


def is_ask(entry: SentEntry, now: float) -> bool:
    """Is `entry` an ask that can still wait on an answer at `now`: `[reply-once]`,
    verified, and no older than `ASK_TTL`?"""
    return (entry.posture == POSTURE_ONCE and entry.verified
            and now - entry.at <= constants.ASK_TTL)


def answered(ask_id: str, asker: str, replies: Iterable[SentEntry]) -> bool:
    """Has the ask `ask_id`, by `asker` (qualified), been answered by one of `replies`,
    the answerer's own messages? The caller picks the answerer: an entry's `author` is
    not read here. Each entry is judged on its own, so their order does not matter."""
    for e in replies:
        if _as_id(e.id) <= _as_id(ask_id):
            continue
        if not any(tag_matches(t, asker) for t in e.tags):
            continue
        if e.reply_to == ask_id or e.posture != POSTURE_NONE:
            return True
    return False


def _as_id(message_id: str) -> int:
    """A message id as a number, for ordering. Anything unreadable sorts first."""
    try:
        return int(message_id)
    except (TypeError, ValueError):
        return -1

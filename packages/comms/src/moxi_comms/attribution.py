"""Attribution honesty.

What the record alone can prove:

  * an identity row stamped by the daemon   -> that qualified identity, **verified**
                                               at its current directory tier, or
                                               `send-only` when a module held the name
                                               reserved as the message was posted
  * the operator's user id                -> the operator, **verified**
  * a webhook row an older release left   -> its display, **labeled**
  * anything else                         -> `unknown`, never guessed
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from . import directory
from .record import AUTHOR_IDENTITY, AUTHOR_OPERATOR, AUTHOR_OPERATOR_OLD
from .scoping import GLOBAL, is_auto_name, qualify, split_tag

TIER_EPHEMERAL, TIER_CHOSEN, TIER_STANDING = "ephemeral", "chosen", "standing"
TIER_OPERATOR, TIER_UNKNOWN = "operator", "unknown"
#: The tier of a message posted under a reserved name: a module sent it, and mail to
#: the name reaches nobody.
TIER_SEND_ONLY = "send-only"

VERIFIED, LABELED = "verified", "labeled"

#: The RENDERED word for anything short of `verified`. The stored value and the payload/API
#: field stay `labeled` -- the wire is unchanged -- but nothing an agent or the operator
#: READS says `labeled` any more: `labeled` names how the fact was obtained, `unverified`
#: names what the reader must do about it, and only the second is the reader's business.
UNVERIFIED = "unverified"


def rendered(attribution: str) -> str:
    """The word a READER sees: `verified`, or `unverified` for everything else.

    One function so the two rendering surfaces (the adapters' frame line, the CLI's
    `history` / `reply`) cannot drift, and so the wire value keeps exactly one
    translation point instead of one per f-string.
    """
    return VERIFIED if attribution == VERIFIED else UNVERIFIED


@dataclass
class Author:
    name: str
    scope: str
    qualified: str
    tier: str
    attribution: str          # verified | labeled


def unknown() -> Author:
    return Author("unknown", GLOBAL, "unknown", TIER_UNKNOWN, LABELED)


def from_display(display: str) -> Author:
    name, scope = split_tag(display or "")
    scope = scope or GLOBAL
    if not name:
        return unknown()
    tier = TIER_EPHEMERAL if is_auto_name(name) else TIER_CHOSEN
    return Author(name, scope, qualify(name, scope), tier, LABELED)


def from_record(db, msg) -> Author:
    """Everything the record + directory can prove about who authored `msg`."""
    if msg.author_kind == AUTHOR_IDENTITY:
        name, scope = split_tag(msg.author_id)
        scope = scope or GLOBAL
        if _reserved_at(db, qualify(name, scope), msg.timestamp):
            tier = TIER_SEND_ONLY
        elif directory.standing_row(db, name, scope) is not None:
            tier = TIER_STANDING
        elif is_auto_name(name):
            tier = TIER_EPHEMERAL
        else:
            tier = TIER_CHOSEN
        return Author(name, scope, qualify(name, scope), tier, VERIFIED)
    if msg.author_kind in (AUTHOR_OPERATOR, AUTHOR_OPERATOR_OLD):
        row = directory.operator(db, msg.author_id)
        if row:
            return Author(row["name"], GLOBAL, row["name"], TIER_OPERATOR, VERIFIED)
        return unknown()
    if msg.author_kind == "unknown":
        return unknown()
    if msg.author_kind == "webhook":
        # comms 0.2 converted every webhook row it could prove before its record moved
        # into comms.db; what it left proves only its display.
        return from_display(msg.author_display)
    return unknown()


def _reserved_at(db, qualified: str, at) -> bool:
    """Was `qualified` reserved for a module at `at` (the record's timestamp spelling)?
    Its `reserved` binding interval says: the interval is how attribution reads a
    reserved name's messages, so one sent before the name was freed keeps its mark after.
    The two spellings are one, so the comparison is a string's. A store that can't be
    read holds no interval."""
    if not at:
        return False
    try:
        row = db.execute("SELECT 1 FROM bindings WHERE qualified = ? AND kind = 'reserved' "
                         "AND bound_at <= ? AND (released_at IS NULL OR released_at >= ?) "
                         "LIMIT 1", (qualified, at, at)).fetchone()
    except sqlite3.Error:
        return False
    return row is not None

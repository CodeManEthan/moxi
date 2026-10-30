"""Attribution honesty.

What the record alone can prove:

  * an identity row stamped by the daemon   -> that qualified identity, **verified**
                                               at its current directory tier
  * the operator's user id                -> the operator, **verified**
  * an unconverted webhook row            -> the frozen migration reader gives the same
                                             answer the older release gave
  * a webhook row left after conversion   -> its display, **labeled**
  * anything else                         -> `unknown`, never guessed
"""

from __future__ import annotations

from dataclasses import dataclass

from . import directory, migration
from .scoping import GLOBAL, is_auto_name, qualify, split_tag

TIER_EPHEMERAL, TIER_CHOSEN, TIER_STANDING = "ephemeral", "chosen", "standing"
TIER_OPERATOR, TIER_UNKNOWN = "operator", "unknown"

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


def from_record(db, msg, *, record=None, journal_path=None) -> Author:
    """Everything the record + directory can prove about who authored `msg`."""
    if msg.author_kind == "identity":
        name, scope = split_tag(msg.author_id)
        scope = scope or GLOBAL
        if directory.standing_row(db, name, scope) is not None:
            tier = TIER_STANDING
        elif is_auto_name(name):
            tier = TIER_EPHEMERAL
        else:
            tier = TIER_CHOSEN
        return Author(name, scope, qualify(name, scope), tier, VERIFIED)
    if msg.author_kind == "user":
        row = directory.operator(db, msg.author_id)
        if row:
            return Author(row["name"], GLOBAL, row["name"], TIER_OPERATOR, VERIFIED)
        return unknown()
    if msg.author_kind == "unknown":
        return unknown()
    if msg.author_kind == "webhook":
        marker = (record.meta_get(migration.META_AUTHOR_STAMPS)
                  if record is not None else migration.META_AUTHOR_STAMPS)
        if marker is None:
            return migration.legacy_author(db, msg, journal_path=journal_path)
        return from_display(msg.author_display)
    return unknown()

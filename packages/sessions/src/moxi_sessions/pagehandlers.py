"""The handlers of sessions' page (`moxi_sessions.page` declares the routes).

Each is a plain function, which loopback's base runs off the event loop on its threads,
so every file and database read here runs off the loop. Both are reads: they call
`find`, `window` and `subagents` and write nothing.

**Finding the session.** `find(harness, session)` asks the harness's root as this server's
environment and sessions' config give it; the page server runs in the browser shell's
environment, not the user's terminal's. A Codex thread can have several candidate files:
the first one sessions can read is taken, and a compressed one only when there is no
other, so its answer says why it cannot be read. A Claude Code subagent (`agent`) is the
entry of its session's subagent listing with that id.

**Refusals.** A session no root holds, a root that cannot be read, a subagent the listing
does not hold, an `agent` for a harness whose subagents are sessions of their own, a
`before` that does not parse for its harness, and a source a read cannot open are each a
422 whose text is the reason; that text can name a root or a file. A well-formed `before`
is not checked against the session (a file's size or line starts, OpenCode's ids). The
route's own checks (an unknown key, a harness that is not one of the three, an id over its
length) are loopback's 400.

**The view rows** follow `moxi_sessions.page.RECORD`. `moxi_sessions.view` makes them, and
`moxi sessions read` prints from the same rows: each record's fields go out under their own
names, typed as the row declares them. A field the harness did not say is left out and
named in the row's `unknown`, and so is a value of a type the row does not carry, so no
answer fails its declared shape on what a harness wrote.
"""

from __future__ import annotations

import re
from typing import Any

from moxi.config import ConfigError

from . import find as finding
from .errors import Unreadable
from .find import LISTED, Found
from .page import TEXT_MAX, TURNS_DEFAULT, lb  # noqa: F401  (TEXT_MAX: the rows' cut)
from .read import window as read_window
from .shape import CLAUDE, OPENCODE, SHAPE
from .source import Source
from .view import INT as _INT, STRING as _STRING, header_row, put as _put, view_row

#: A byte offset, as a file's `before` is written.
_OFFSET = re.compile(r"\A[0-9]{1,18}\Z")


# -- finding the source ---------------------------------------------------------------------

def _found(harness: str, session: str) -> Found:
    try:
        return finding.find(harness, session)
    except ConfigError as exc:
        raise lb.Refused(f"sessions' config cannot be read: {exc}") from None


def _source(harness: str, session: str) -> Source:
    """The session's source, or a refusal saying why there is none."""
    found = _found(harness, session)
    hits = found.hits
    if not hits:
        said = "; ".join(f"{probe.root}: {probe.outcome}" for probe in found.probes)
        raise lb.Refused(f"no {harness} session {session} was found ({said})")
    hit = next((h for h in hits if not h.compressed), hits[0])
    return hit.source


def _subagent(source: Source, agent: str) -> Any:
    """The Claude Code subagent `agent` of the session `source` reads."""
    listing = finding.subagents(source)
    for entry in listing.entries:
        if entry.agent_id == agent:
            return entry
    if listing.state == LISTED or listing.reason is None:
        raise lb.Refused(f"session {source.session_id} has no subagent {agent}")
    raise lb.Refused(f"the subagents of session {source.session_id} cannot be listed: "
                     f"{listing.reason}")


def _before(harness: str, before: str | None) -> Any:
    if before is None:
        return None
    if harness == OPENCODE:
        if not before:
            raise lb.Refused("before is an OpenCode message id, and it is empty")
        return before
    if not _OFFSET.match(before):
        raise lb.Refused(f"before is a byte offset in a {harness} file, a whole number "
                         "0 or more")
    return int(before)


# -- the reads ------------------------------------------------------------------------------

def window(call: Any) -> dict[str, Any]:
    """`GET /api/window?harness=&session=[&agent=][&turns=][&before=]`: the session's last
    `turns` turns (5 unless asked), or the turns before `before`."""
    query = call.query
    harness, session, agent = query["harness"], query["session"], query.get("agent")
    turns = query.get("turns", TURNS_DEFAULT)
    if turns < 1:
        raise lb.Refused("turns is a number of turns, 1 or more")
    if agent is not None and harness != CLAUDE:
        raise lb.Refused(f"a {harness} subagent is a session of its own: open it by its "
                         "own id, with no agent")
    before = _before(harness, query.get("before"))
    source = _source(harness, session)
    target = _subagent(source, agent) if agent is not None else source
    try:
        read = read_window(target, turns=turns, before=before)
        header = read.header
    except Unreadable as exc:
        raise lb.Refused(exc.reason) from None
    except ValueError as exc:
        raise lb.Refused(str(exc)) from None
    rows = [view_row(record) for record in read.records]
    times = [row["time"] for row in rows if isinstance(row.get("time"), (int, float))]
    path = target.source.path if agent is not None else source.path
    return {
        "shape": SHAPE,
        "harness": harness,
        "session": session,
        "agent": agent,
        "path": str(path),
        "header": header_row(header, rows),
        "last_activity": max(times) if times else None,
        "turns": turns,
        "has_earlier": read.has_earlier,
        "before": None if read.before is None else str(read.before),
        "ended": read.ended,
        "records": rows,
    }


def _route(harness: str, session: str, agent: str | None) -> str:
    return f"{harness}/{session}/{agent}" if agent is not None else f"{harness}/{session}"


def _entry(harness: str, session: str, entry: Any) -> dict[str, Any]:
    if harness == CLAUDE:
        opens, agent = session, entry.agent_id
    else:
        # An OpenCode child session and a Codex subagent thread are sessions of their own.
        opens, agent = entry.source.session_id or entry.agent_id, None
    row: dict[str, Any] = {"agent_id": entry.agent_id, "session": opens, "agent": agent,
                           "route": _route(harness, opens, agent),
                           "path": str(entry.source.path), "size": entry.size}
    unknown: list[str] = []
    for name, typed in (("agent_type", _STRING), ("description", _STRING),
                        ("tool_use_id", _STRING), ("spawn_depth", _INT),
                        ("parent_agent_id", _STRING), ("workflow_id", _STRING)):
        _put(row, unknown, name, getattr(entry, name), typed)
    row["unknown"] = unknown
    return row


def subagents(call: Any) -> dict[str, Any]:
    """`GET /api/subagents?harness=&session=`: the session's subagents, with the
    listing's state and reason."""
    harness, session = call.query["harness"], call.query["session"]
    source = _source(harness, session)
    try:
        listing = finding.subagents(source)
    except ValueError as exc:
        raise lb.Refused(str(exc)) from None
    return {
        "shape": SHAPE,
        "harness": harness,
        "session": session,
        "state": listing.state,
        "reason": listing.reason,
        "entries": [_entry(harness, session, entry) for entry in listing.entries],
        "unreadable_parts": [{"where": str(where), "reason": str(why)}
                             for where, why in listing.unreadable_parts],
    }


__all__ = ["subagents", "view_row", "window"]

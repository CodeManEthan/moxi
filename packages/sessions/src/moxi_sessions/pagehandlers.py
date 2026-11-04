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

**The view rows** follow `moxi_sessions.page.RECORD`: each record's fields go out under
their own names, typed as the row declares them. A field the harness did not say is left
out and named in the row's `unknown`, and so is a value of a type the row does not carry,
so no answer fails its declared shape on what a harness wrote.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from moxi.config import ConfigError

from . import find as finding
from .errors import Unreadable
from .find import LISTED, Found
from .page import TEXT_MAX, TURNS_DEFAULT, lb
from .read import window as read_window
from .shape import CLAUDE, OPENCODE, SHAPE, UNKNOWN, Record
from .source import Source

#: A byte offset, as a file's `before` is written.
_OFFSET = re.compile(r"\A[0-9]{1,18}\Z")

_STRING = "string"
_INT = "int"
_FLOAT = "float"
_BOOL = "bool"

#: Each kind's fields beyond the common ones, with the type the row carries each as.
_FIELDS: Mapping[str, tuple[tuple[str, str], ...]] = {
    "message": (("role", _STRING), ("reasoning", _BOOL), ("model", _STRING),
                ("synthetic", _BOOL)),
    "tool_call": (("call_id", _STRING), ("name", _STRING), ("summary", _STRING),
                  ("server", _STRING)),
    "tool_result": (("call_id", _STRING), ("is_error", _BOOL), ("size_bytes", _INT)),
    "request": (("request_id", _STRING), ("model", _STRING), ("response_model", _STRING),
                ("provider", _STRING), ("service_tier", _STRING), ("input_tokens", _INT),
                ("cache_read_input_tokens", _INT), ("cache_creation_input_tokens", _INT),
                ("output_tokens", _INT), ("reasoning_output_tokens", _INT),
                ("cost_usd", _FLOAT), ("context_window", _INT), ("sidechain", _BOOL),
                ("synthetic", _BOOL), ("final", _BOOL)),
    "limits": (("limit_id", _STRING), ("limit_name", _STRING), ("plan_type", _STRING)),
    "malformed": (("why", _STRING), ("unterminated", _BOOL)),
    "other": (("recognised", _BOOL),),
}

#: A marker's own fields the row carries, by the marker's name: (field, row key, type).
_MARKER_FIELDS: Mapping[str, tuple[tuple[str, str, str], ...]] = {
    "compaction": (("trigger", "trigger", _STRING), ("pre_tokens", "pre_tokens", _INT)),
    "turn_end": (("duration_ms", "duration_ms", _INT),),
    "title": (("title", "title", _STRING), ("source", "title_source", _STRING)),
    "settings": (("model", "model", _STRING), ("provider", "provider", _STRING),
                 ("service_tier", "service_tier", _STRING)),
}

#: The row keys a None cannot fill: those the row declares not nullable.
_NOT_NULL = frozenset({"reasoning", "size_bytes", "request_id", "final", "why",
                       "unterminated", "recognised", "summary"})


def _fits(value: Any, kind: str) -> bool:
    if kind == _STRING:
        return isinstance(value, str)
    if kind == _BOOL:
        return isinstance(value, bool)
    if isinstance(value, bool):
        return False
    if kind == _INT:
        return isinstance(value, int)
    return isinstance(value, (int, float)) and value == value and value not in (
        float("inf"), float("-inf"))


def _put(row: dict[str, Any], unknown: list[str], key: str, value: Any, kind: str) -> None:
    """Set `row[key]`, or name `key` in `unknown` when the harness did not say or wrote a
    value the row cannot carry."""
    if value is None and key not in _NOT_NULL:
        row[key] = None
    elif value is not UNKNOWN and value is not None and _fits(value, kind):
        row[key] = value
    else:
        unknown.append(key)


def _at(at: Mapping[str, Any]) -> dict[str, Any]:
    out = {}
    for key, kind in (("line", _INT), ("offset", _INT), ("table", _STRING),
                      ("id", _STRING)):
        value = at.get(key)
        if value is None and key == "line" and key in at:
            out[key] = None
        elif value is not None and _fits(value, kind):
            out[key] = value
    return out


def _text(text: str) -> tuple[str, int, bool]:
    """The text cut to `TEXT_MAX` UTF-8 bytes on a character's edge, its whole size, and
    whether it was cut."""
    data = text.encode("utf-8", "surrogatepass")
    if len(data) <= TEXT_MAX:
        return text, len(data), False
    return data[:TEXT_MAX].decode("utf-8", "ignore"), len(data), True


def _windows(windows: Any) -> list[dict[str, Any]]:
    out = []
    for item in windows or ():
        row: dict[str, Any] = {"name": str(getattr(item, "name", ""))}
        unknown: list[str] = []
        _put(row, unknown, "used_percent", getattr(item, "used_percent", UNKNOWN), _FLOAT)
        _put(row, unknown, "window_minutes", getattr(item, "window_minutes", UNKNOWN),
             _INT)
        _put(row, unknown, "resets_at", getattr(item, "resets_at", UNKNOWN), _FLOAT)
        row["unknown"] = unknown
        out.append(row)
    return out


def view_row(record: Record) -> dict[str, Any]:
    """One record as the window's view row (`moxi_sessions.page.RECORD`)."""
    kind = record.kind
    row: dict[str, Any] = {"kind": kind, "raw_kind": record.raw_kind or "",
                           "at": _at(record.at)}
    unknown: list[str] = []
    _put(row, unknown, "time", record.time, _FLOAT)
    _put(row, unknown, "turn_id", record.turn_id, _STRING)
    _put(row, unknown, "agent_id", record.agent_id, _STRING)
    for name, typed in _FIELDS.get(kind, ()):
        _put(row, unknown, name, getattr(record, name), typed)
    if kind == "message":
        text = record.text if isinstance(record.text, str) else ""
        row["text"], row["text_bytes"], row["truncated"] = _text(text)
        row["flags"] = sorted(name for name, value in (record.flags or {}).items()
                              if value is True)
    elif kind == "marker":
        row["marker"] = record.marker
        for field, key, typed in _MARKER_FIELDS.get(record.marker, ()):
            _put(row, unknown, key, record.fields.get(field, UNKNOWN), typed)
    elif kind == "limits":
        row["windows"] = _windows(record.windows)
    row["unknown"] = unknown
    return row


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

def _header(found: Any, rows: list[dict[str, Any]]) -> dict[str, Any]:
    header: dict[str, Any] = {}
    unknown: list[str] = []
    for name in ("cwd", "title", "version", "parent", "entrypoint"):
        _put(header, unknown, name, getattr(found, name, UNKNOWN), _STRING)
    if "title" in unknown or header.get("title") is None:
        titles = [r["title"] for r in rows if r.get("marker") == "title"
                  and isinstance(r.get("title"), str)]
        if titles:
            header["title"] = titles[-1]
            if "title" in unknown:
                unknown.remove("title")
    header["unknown"] = unknown
    return header


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
        "header": _header(header, rows),
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

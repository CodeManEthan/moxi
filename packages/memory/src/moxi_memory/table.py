"""The session table: one row per main session, the only thing the start lists read.

A row holds the harness, the session id, the identity (comms' qualified name, or none),
the project label, the launch kind (or none), whether that kind is settled, the time of
the newest record memory read (before any read, the time a listener first wrote the
row), and the time of the row's last write. Subagent sessions get no row.

Who writes what:

- A start listener writes its own session's row: `write_start` (the harness and id, and
  the label when the row has none), then `write_identity` with the identity it read.
  When the identity arrives after the listener has answered, `write_late_identity`
  writes it under stricter rules.
- An ingest child writes each session it reads with `write_read`: the label and the
  launch kind only when the row has none, the newest record's time, and whether the kind
  is settled (once settled, it stays settled).
- A relabel writes a re-derived label with `relabel`, and nothing else.
- A rebuild removes the rows of sessions it found no transcript for with `remove_unseen`,
  except a row written after the rebuild started.

The two start lists are `agent_sessions` and `project_sessions`. Both read only this
table, through an index that holds only listable rows: a row whose kind is settled and
is neither `scratch` nor `fork`. Each returns at most `MAX_ROWS` rows, newest first, and never this
session's own row.

Every function takes an open state store connection (`moxi_memory.state.connect`) and
runs one statement or one transaction. A caller that must not wait wraps the call in
`moxi_memory.state.bounded`. Times are seconds since the epoch.
"""

from __future__ import annotations

import time
from collections.abc import Iterable
from typing import NamedTuple

TYPE_CHECKING = False
if TYPE_CHECKING:
    import sqlite3

#: The most rows one list query returns.
MAX_ROWS = 10
#: The launch kinds that keep a session out of the lists and out of default search: a
#: scratch launch, and a fork launch, which copies another session's whole transcript.
SCRATCH = "scratch"
FORK = "fork"


class Row(NamedTuple):
    """One session's row, as stored."""

    harness: str
    session_id: str
    identity: str | None
    label: str | None
    kind: str | None
    settled: bool
    newest: float
    written: float


class Listed(NamedTuple):
    """One line of a start list: the session, the time of its newest record memory read,
    and the identity that ran it (None for none)."""

    harness: str
    session_id: str
    newest: float
    identity: str | None


_COLUMNS = "harness, session_id, identity, label, kind, settled, newest, written"


def _now(now: float | None) -> float:
    return time.time() if now is None else now


def _text(value: str | None) -> str | None:
    """`value`, or None when it is None or blank."""
    return value if isinstance(value, str) and value.strip() else None


def row(conn: sqlite3.Connection, harness: str, session_id: str) -> Row | None:
    """The row of one session, or None."""
    found = conn.execute(f"SELECT {_COLUMNS} FROM sessions WHERE harness = ? "
                         "AND session_id = ?", (harness, session_id)).fetchone()
    return None if found is None else _row(found)


def _row(values) -> Row:
    h, s, identity, label, kind, settled, newest, written = values
    return Row(h, s, identity, label, kind, bool(settled), float(newest), float(written))


# -- the listener's writes ------------------------------------------------------------

def write_start(conn: sqlite3.Connection, harness: str, session_id: str,
                label: str | None, *, now: float | None = None) -> None:
    """A start listener's row write for its own session.

    A new row gets the harness, the id and `label`, with `now` as its newest time; the
    kind is not settled. An existing row keeps its label, its kind, whether that is
    settled and its newest time, and only takes `label` when it has none.
    """
    now = _now(now)
    conn.execute(
        "INSERT INTO sessions (harness, session_id, label, settled, newest, written) "
        "VALUES (?, ?, ?, 0, ?, ?) "
        "ON CONFLICT (harness, session_id) DO UPDATE SET "
        "label = COALESCE(sessions.label, excluded.label), written = excluded.written",
        (harness, session_id, _text(label), now, now))


def write_identity(conn: sqlite3.Connection, harness: str, session_id: str,
                   identity: str | None, *, now: float | None = None) -> bool:
    """A start listener's identity write, made after `write_start` for the same start.

    The identity read at this start replaces the stored one. An empty or missing
    identity writes nothing, and nor does a session with no row. Returns whether a row
    changed.
    """
    identity = _text(identity)
    if identity is None:
        return False
    cur = conn.execute("UPDATE sessions SET identity = ?, written = ? "
                       "WHERE harness = ? AND session_id = ?",
                       (identity, _now(now), harness, session_id))
    return cur.rowcount > 0


def write_late_identity(conn: sqlite3.Connection, harness: str, session_id: str,
                        identity: str | None, *, now: float | None = None) -> bool:
    """The identity that arrived after the listener answered its start.

    It is written only to the row of this session id, only when that row exists and
    still holds no identity, and never as an empty or missing value. So an answer late
    from an earlier start never replaces what a later start wrote, and a row a rebuild
    removed meanwhile is not made again. Returns whether a row changed.
    """
    identity = _text(identity)
    if identity is None:
        return False
    cur = conn.execute("UPDATE sessions SET identity = ?, written = ? "
                       "WHERE harness = ? AND session_id = ? AND identity IS NULL",
                       (identity, _now(now), harness, session_id))
    return cur.rowcount > 0


# -- the ingest child's writes --------------------------------------------------------

def write_read(conn: sqlite3.Connection, harness: str, session_id: str, *,
               newest: float, label: str | None = None, kind: str | None = None,
               settled: bool = False, now: float | None = None) -> None:
    """An ingest child's write for a main session it read.

    The row is made when there is none. `label` and `kind` are written only where the row
    holds none, so a stored label or kind is kept. The newest time becomes `newest` when
    that is later than the stored one. `settled=True` settles the kind; a row once
    settled stays settled, so `settled=False` never unsettles it.
    """
    now = _now(now)
    conn.execute(
        "INSERT INTO sessions (harness, session_id, label, kind, settled, newest, written) "
        "VALUES (?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT (harness, session_id) DO UPDATE SET "
        "label = COALESCE(sessions.label, excluded.label), "
        "kind = COALESCE(sessions.kind, excluded.kind), "
        "settled = MAX(sessions.settled, excluded.settled), "
        "newest = MAX(sessions.newest, excluded.newest), "
        "written = excluded.written",
        (harness, session_id, _text(label), _text(kind), 1 if settled else 0,
         float(newest), now))


def relabel(conn: sqlite3.Connection, harness: str, session_id: str, label: str, *,
            now: float | None = None) -> bool:
    """Replace one session's label, changing nothing else in its row. A blank label
    writes nothing. Returns whether a row changed."""
    label = _text(label)
    if label is None:
        return False
    cur = conn.execute("UPDATE sessions SET label = ?, written = ? "
                       "WHERE harness = ? AND session_id = ?",
                       (label, _now(now), harness, session_id))
    return cur.rowcount > 0


def remove_unseen(conn: sqlite3.Connection, seen: Iterable[tuple[str, str]],
                  started: float) -> int:
    """A rebuild's last step on the table: remove every row whose (harness, session id)
    is not in `seen`, the sessions the rebuild found a transcript for, except a row
    written at or after `started`, the rebuild's start time. Returns how many rows went.
    """
    from moxi import store

    with store.transaction(conn):
        conn.execute("CREATE TEMP TABLE IF NOT EXISTS seen_sessions "
                     "(harness TEXT NOT NULL, session_id TEXT NOT NULL, "
                     "PRIMARY KEY (harness, session_id)) WITHOUT ROWID")
        conn.execute("DELETE FROM temp.seen_sessions")
        conn.executemany("INSERT OR IGNORE INTO temp.seen_sessions VALUES (?, ?)", seen)
        cur = conn.execute(
            "DELETE FROM sessions WHERE written < ? AND NOT EXISTS ("
            "SELECT 1 FROM temp.seen_sessions AS s "
            "WHERE s.harness = sessions.harness AND s.session_id = sessions.session_id)",
            (started,))
        removed = cur.rowcount
        conn.execute("DELETE FROM temp.seen_sessions")
    return removed


# -- the start lists ------------------------------------------------------------------

_LISTABLE = "settled = 1 AND kind IS NOT 'scratch' AND kind IS NOT 'fork'"


def _leave_out(me: tuple[str, str], others: Iterable[tuple[str, str]]):
    keys = [tuple(me), *(tuple(k) for k in others)]
    values = ", ".join("(?, ?)" for _ in keys)
    return values, [part for key in keys for part in key]


def agent_sessions(conn: sqlite3.Connection, identity: str, me: tuple[str, str], *,
                   limit: int = MAX_ROWS) -> list[Listed]:
    """The first start list's rows: the sessions whose identity is `identity`, from any
    project, newest first, at most `limit` (never more than `MAX_ROWS`).

    `me` is this session's (harness, session id), which is left out, as is every session
    whose kind is unsettled, `scratch` or `fork`. An empty identity gives no rows.
    """
    identity = _text(identity)
    if identity is None:
        return []
    values, params = _leave_out(me, ())
    rows = conn.execute(
        "SELECT harness, session_id, newest, identity FROM sessions "
        "INDEXED BY sessions_by_identity "
        f"WHERE identity = ? AND {_LISTABLE} "
        f"AND (harness, session_id) NOT IN (VALUES {values}) "
        "ORDER BY newest DESC LIMIT ?",
        (identity, *params, _cap(limit))).fetchall()
    return [Listed(h, s, float(n), i) for h, s, n, i in rows]


def project_sessions(conn: sqlite3.Connection, label: str, me: tuple[str, str], *,
                     leave_out: Iterable[tuple[str, str]] = (),
                     limit: int = MAX_ROWS) -> list[Listed]:
    """The second start list's rows: the sessions whose label is `label`, newest first,
    at most `limit` (never more than `MAX_ROWS`), each with its identity (None for a
    session that had none, which the list names by its harness).

    `me` is this session's (harness, session id), and `leave_out` the (harness, session
    id) keys the first list shows; all of them are left out, as is every session whose
    kind is unsettled, `scratch` or `fork`. An empty label gives no rows.
    """
    label = _text(label)
    if label is None:
        return []
    values, params = _leave_out(me, leave_out)
    rows = conn.execute(
        "SELECT harness, session_id, newest, identity FROM sessions "
        "INDEXED BY sessions_by_label "
        f"WHERE label = ? AND {_LISTABLE} "
        f"AND (harness, session_id) NOT IN (VALUES {values}) "
        "ORDER BY newest DESC LIMIT ?",
        (label, *params, _cap(limit))).fetchall()
    return [Listed(h, s, float(n), i) for h, s, n, i in rows]


def _cap(limit: int) -> int:
    return max(0, min(int(limit), MAX_ROWS))


__all__ = ["MAX_ROWS", "SCRATCH", "FORK", "Row", "Listed", "row", "write_start", "write_identity",
           "write_late_identity", "write_read", "relabel", "remove_unseen",
           "agent_sessions", "project_sessions"]

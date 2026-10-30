"""The directory: standing identities, rooms and the operator, in comms.db.

The tables are comms.db's `identities`, `rooms`, `operators` and `meta`
(`store/schema.py`). Provisioning writes them; the daemon and the verbs read them. A
writer opens the store with `init` (`store.open_write`), a reader with
`connect(read_only=True)` (`store.open_read`). Every connection has foreign keys on.

The daemon consults this per claim -- never a boot-time cache.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .store import open_read, open_write

# meta keys
META_WRITER = "writer"

#: A standing row's `kind`. `agent` is every row before send-only identities existed and
#: every row since that is not one. A `send-only` row is a name that sends and is never a
#: recipient: nothing is delivered to it, deferred for it or held for it, bound or not.
KIND_AGENT = "agent"
KIND_SEND_ONLY = "send-only"
ROW_KINDS = (KIND_AGENT, KIND_SEND_ONLY)


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path: str | Path | None = None, *, read_only: bool = False,
            env=None) -> sqlite3.Connection:
    """Open the directory's store, comms.db (`path`, else its default). Rows come back
    as `sqlite3.Row`. A writer's connection commits each statement by itself; group
    statements with `moxi.store.transaction`."""
    opener = open_read if read_only else open_write
    conn = opener(path, env=env)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init(path: str | Path | None = None, *, env=None) -> sqlite3.Connection:
    """The writer's connection: the store opened through its gate, its schema made."""
    return connect(path, env=env)


# --------------------------------------------------------------------------- reads

def room_by_name(conn, name: str):
    return conn.execute("SELECT * FROM rooms WHERE name = ?", (name,)).fetchone()


def standing_row(conn, name: str, scope: str):
    return conn.execute(
        "SELECT * FROM identities WHERE name = ? AND scope = ? AND status = 'active'",
        (name, scope),
    ).fetchone()


def kind_of(row) -> str:
    """A standing row's kind; `agent` for no row."""
    if row is None:
        return KIND_AGENT
    return row["kind"] if "kind" in row.keys() else KIND_AGENT


def is_send_only(conn, name: str, scope: str) -> bool:
    """Is there an active standing row at exactly (name, scope) whose kind is send-only?
    Read per call, like every directory fact the daemon uses."""
    return kind_of(standing_row(conn, name, scope)) == KIND_SEND_ONLY


def retired(conn, name: str, scope: str) -> bool:
    """Is (name, scope) retired: an `identities` row at exactly (name, scope) whose status is
    `retired`? A retired identity is one comms no longer binds, routes to or queues for;
    its binding history rows, its messages in the record, its cursor and its card stay. Read
    per call, like every directory fact the daemon uses."""
    return conn.execute(
        "SELECT 1 FROM identities WHERE name = ? AND scope = ? AND status = 'retired'",
        (name, scope),
    ).fetchone() is not None


#: The refusals for a retired identity, word for word: the register's, and resolution's
#: (send, reply, --check, explain, cache). `{q}` is the retired identity as `qualify`
#: spells it, `{name}` the bare name of the global identity to use instead.
RETIRED_BIND = ("{q} is retired: comms keeps a project suffix only to tell apart two agents "
                "of one name in different projects, and {q} had no such twin. Bind {name} "
                "instead, with no --scope.")
RETIRED_SEND = ("{q} is retired: comms keeps a project suffix only to tell apart two agents "
                "of one name in different projects, and {q} had no such twin. Send to "
                "{name} instead. Nothing was sent.")


def standing_rows(conn):
    return conn.execute("SELECT * FROM identities WHERE status = 'active'").fetchall()


def operator(conn, user_id: str):
    return conn.execute("SELECT * FROM operators WHERE user_id = ?", (user_id,)).fetchone()


def meta_get(conn, key: str, default=None):
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def meta_set(conn, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta(key, value) VALUES (?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )

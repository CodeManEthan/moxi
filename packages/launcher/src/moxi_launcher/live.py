"""The launches that are alive now, read from the launch log.

**A live launch** is one not `failed` whose process runs now: `moxi.proc.alive` on the pid
and start time its row recorded, or, with no pid, on the endpoint that claimed it. That
costs one store read and one look at a process per row, and asks turns nothing.

This module imports `moxi.proc` and the launcher's `store` and nothing else of the
launcher, so a caller on an install without the UI shell can read what is alive.
"""

from __future__ import annotations

import sqlite3
from dataclasses import replace

from moxi import proc

from . import store


def alive(row: store.LaunchRow) -> bool:
    """Whether the process of the launch `row` runs now: `moxi.proc.alive` on the pid and
    start time it recorded (with no start time, whether any process has that pid), or,
    for a row that recorded no pid, on the endpoint that claimed it. An endpoint that
    can't be read is not alive."""
    if row.pid:
        if row.pid_started is None:
            return proc.identity(row.pid) is not None
        return proc.alive(proc.ProcId(row.pid, row.pid_started))
    parsed = store.parse_endpoint(row.endpoint) if row.endpoint else None
    return parsed is not None and not store.endpoint_gone(row.endpoint or "")


def live(conn: sqlite3.Connection) -> list[store.LaunchRow]:
    """The live launches, newest first: each not `failed` whose process runs now, with
    `newest_session_id` the id it was last known by. One store read and one look at a
    process per row; turns is not asked."""
    found = []
    for row in store.running(conn):
        if alive(row):
            known = store.session_ids(conn, row.request_id)
            found.append(replace(row, newest_session_id=known[-1][0] if known
                                 else row.session_id))
    return found


__all__ = ["alive", "live"]

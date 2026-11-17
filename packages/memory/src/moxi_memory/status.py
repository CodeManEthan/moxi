"""What `moxi memory status` shows: the index, its reads, a rebuild, and the session table.

`status()` never raises for a missing or unreadable index: it says so in `index`, with
the reason, and still reports what it can read without the index (a running rebuild, the
session table). Like a search, it only reads, and never takes the writer lock.

The backlog is the sources that changed since the last walk of their harness, found by
listing each harness root through sessions (one stat per file, no file read) and
comparing each source's reuse keys with those the index stored. A source the next walk
will look at and leave out (a subagent while `index.include_subagents` is off, an imported
thread) may be counted until that walk has run.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field

from . import index
from .search import STATE, Filling, filling

#: `Status.index` for an index that was read, one that isn't there, and one that can't
#: be read.
OK = "ok"
MISSING = "missing"
UNREADABLE = "unreadable"
#: The name a session with no launch kind is counted under.
NO_KIND = "none"


@dataclass
class Status:
    """memory's state as one read sees it.

    - `index`: `ok`, `missing` or `unreadable`, with `reason` for the last two;
      `generation` the generation read.
    - `rows`: rows per tier and harness: `{"1": {harness: n}, "2": {...},
      "subagents": {...}}`.
    - `sources_read`: sources the index has read; `sources_not_read`: compressed Codex
      rollouts whose thread has no rows, which sessions can't read.
    - `last_ingest`: the time a source was last read; `last_walk`: each harness's last
      catch-up walk.
    - `backlog`: sources changed since the last walk, by harness (0 for a harness with
      no root on this machine, None for one whose root couldn't be listed; None as a
      whole when no config could be read).
    - `first_read`: whether the first full read has finished; `filling`: how far it is
      while it hasn't.
    - `rebuilding`: the start time of a running rebuild (0.0 before it wrote its time),
      and `rebuild_pid` the process id it wrote, for information only.
    - `waiting`: the request waiting for the writer lock (`{"kind", "asked_at"}`, kind
      `walk`, `all`, `rebuild` or `relabel`), or None.
    - `tokenizer`: the generation's tokenizer.
    - `sessions`: the session table's rows (`total`), by launch kind (`none` for no
      kind), and by whether they have an identity (`with_identity`,
      `without_identity`). `sessions_unreadable`: why memory's state store, which holds
      the table, can't be read, or None; the index's own state is apart from it.
    - `dropped`: how many sessions the last rebuild dropped; `rebuilt`: when it started.

    Times are seconds since the epoch.
    """

    index: str = OK
    reason: str | None = None
    generation: int | None = None
    rows: dict[str, dict[str, int]] = field(default_factory=dict)
    sources_read: int = 0
    sources_not_read: int = 0
    last_ingest: float | None = None
    last_walk: dict[str, float] = field(default_factory=dict)
    backlog: dict[str, int | None] | None = None
    first_read: bool = False
    filling: Filling | None = None
    rebuilding: float | None = None
    rebuild_pid: int | None = None
    waiting: dict[str, object] | None = None
    tokenizer: str | None = None
    sessions: dict[str, object] = field(default_factory=dict)
    sessions_unreadable: str | None = None
    dropped: int | None = None
    rebuilt: float | None = None

    def to_json(self) -> dict[str, object]:
        """The status as plain JSON values. `rebuilding` is written `rebuild_started_at`,
        a name that says it holds a time, and `filling`'s count of sources read is
        `sources_read`, as at the top level."""
        out = dict(self.__dict__)
        out["rebuild_started_at"] = out.pop("rebuilding")
        out["filling"] = (None if self.filling is None else
                          {"sources_read": self.filling.read, "listed": self.filling.listed})
        return out


def _number(value: str | None, kind=float):
    try:
        return None if value is None else kind(value)
    except ValueError:
        return None


def _rows(conn) -> dict[str, dict[str, int]]:
    tiers: dict[str, dict[str, int]] = {"1": {}, "2": {}, "subagents": {}}
    for harness, sub, n in conn.execute(
            "SELECT harness, agent_id IS NOT NULL, count(*) FROM rows "
            "GROUP BY harness, agent_id IS NOT NULL"):
        tiers["subagents" if sub else "1"][harness] = n
    for harness, n in conn.execute("SELECT harness, count(*) FROM tails GROUP BY harness"):
        tiers["2"][harness] = n
    return tiers


def _sessions_alone(out: Status, env) -> None:
    """The session table's counts with no index to attach it to, into `out`."""
    import sqlite3

    from moxi import store

    from . import state

    where = state.path(env)
    try:
        conn = store.connect(where, create=False, timeout_s=0.25)
    except store.StoreError:
        return
    except sqlite3.Error as exc:
        out.sessions_unreadable = f"{where}: {exc}"
        return
    try:
        conn.execute("PRAGMA query_only = 1")
        out.sessions = _sessions_of(conn, "main")
    except sqlite3.Error as exc:
        out.sessions_unreadable = f"{where}: {exc}"
    finally:
        conn.close()


def _sessions_of(conn, schema: str) -> dict[str, object]:
    """The session table's counts, read from `schema` on `conn`; empty when the table
    isn't there yet."""
    if conn.execute(f"SELECT 1 FROM {schema}.sqlite_master WHERE type = 'table' "
                    "AND name = 'sessions'").fetchone() is None:
        return {}
    kinds = {kind if kind is not None else NO_KIND: n for kind, n in conn.execute(
        f"SELECT kind, count(*) FROM {schema}.sessions GROUP BY kind")}
    with_id, total = conn.execute(
        f"SELECT count(identity), count(*) FROM {schema}.sessions").fetchone()
    return {"total": total, "by_kind": dict(sorted(kinds.items())),
            "with_identity": with_id, "without_identity": total - with_id}


def backlog(conn, env: Mapping[str, str] | None = None) -> dict[str, int | None] | None:
    """Sources changed since the last walk, per harness in config `harnesses`: listed
    through sessions with `modified_since` the last walk, less those whose reuse keys
    the index already holds. 0 for a harness with no root on this machine; None for one
    whose root is there but couldn't be listed; None as a whole when memory's config
    can't be read."""
    from moxi.config import ConfigError

    from . import ingest, settings

    try:
        config = settings.read(env)
    except ConfigError:
        return None
    api = ingest._api()
    subagents = bool(config["index.include_subagents"])
    out: dict[str, int | None] = {}
    for harness in config["harnesses"]:
        last = _number(index.meta_get(conn, f"last_walk.{harness}"))
        try:
            listing = api.sources(harness, modified_since=last, heads=False)
        except Exception:  # noqa: BLE001 - a root that can't be listed is counted unknown
            listing = None
        if listing is None or not listing.ok:
            # no root at all: nothing to read, as a walk counts it; else not known
            absent = listing is not None and not os.path.lexists(listing.root)
            out[harness] = 0 if absent else None
            continue
        stored = dict(conn.execute("SELECT source, reuse FROM sources WHERE harness = ?",
                                   (harness,)))
        behind = 0
        for entry in listing:
            if entry.kind == "subagent" and not subagents:
                continue
            key = ingest.source_key(entry.source)
            if entry.compressed:
                behind += key not in stored
                continue
            reuse = ingest._reuse(entry)
            if reuse is None or stored.get(key) != reuse:
                behind += 1
        out[harness] = behind
    return out


def status(env: Mapping[str, str] | None = None, *, with_backlog: bool = True) -> Status:
    """memory's state now. A missing or unreadable index is said in `index` and
    `reason`, not raised. `with_backlog=False` skips listing the harness roots."""
    import sqlite3

    from . import ingest

    out = Status()
    running = index.rebuild_running(env)
    if running is not None:
        out.rebuilding = running.started if running.started is not None else 0.0
        out.rebuild_pid = running.pid
    from . import state

    request, _ = state.peek(env)
    if request is not None:
        out.waiting = {"kind": request.kind, "asked_at": request.asked}
    try:
        reader = index.open_read(env)
    except index.IndexMissing as exc:
        out.index, out.reason = MISSING, str(exc)
        _sessions_alone(out, env)
        return out
    except index.IndexUnreadable as exc:
        out.index, out.reason = UNREADABLE, str(exc)
        _sessions_alone(out, env)
        return out
    with reader:
        conn = reader.conn
        out.generation = reader.generation
        try:
            meta = dict(conn.execute("SELECT key, value FROM meta"))
            out.rows = _rows(conn)
            out.sources_read = conn.execute(
                "SELECT count(*) FROM sources WHERE reuse IS NOT ?",
                (json.dumps(ingest.COMPRESSED),)).fetchone()[0]
            out.sources_not_read = ingest.not_read(conn)
            out.last_ingest = conn.execute("SELECT max(read) FROM sources").fetchone()[0]
            out.last_walk = {k.removeprefix("last_walk."): float(v) for k, v in meta.items()
                             if k.startswith("last_walk.") and _number(v) is not None}
            out.first_read = meta.get("first_read") == "1"
            out.filling = filling(conn)
            out.tokenizer = meta.get("tokenizer")
            out.dropped = _number(meta.get("dropped"), int)
            out.rebuilt = _number(meta.get("rebuilt"))
            try:
                if reader.attach_state(STATE):
                    out.sessions = _sessions_of(conn, STATE)
            except sqlite3.DatabaseError as exc:
                from . import state

                out.sessions_unreadable = f"{state.path(env)}: {exc}"
            if with_backlog:
                out.backlog = backlog(conn, env)
        except sqlite3.DatabaseError as exc:
            out.index, out.reason = UNREADABLE, f"{reader.path}: {exc}"
    return out


__all__ = ["OK", "MISSING", "UNREADABLE", "NO_KIND", "Status", "backlog", "status"]

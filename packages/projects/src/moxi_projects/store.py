"""projects' store: its schema, the one write sequence, and the row writes.

The store is one SQLite file, `projects.db`, in projects' data directory. Reads are the
read API's (`moxi_projects.api`); they never write. Every write goes through `writing()`,
one sequence in this order:

1. `moxi.store.connect(path)`, which creates the file private to its owner and in WAL
   mode on the first write;
2. `moxi.store.migrate(conn, STEPS)`, each step with its version bump in its own
   transaction. A migration runs before and outside the write's transaction, never inside
   it;
3. one `moxi.store.transaction(conn)` holding the write, which reads the schema version
   again under its lock and stops, writing nothing, when another release migrated the
   store past this code in between;
4. close.

Step 2 adds `remove_status`, where the page keeps what a confirmed remove came to, keyed
by the request's idempotency key, so a repeat of the request gets the same answer for a
day, also after the page's server has restarted. A store at version 1 is still read; its
status read answers that no such request was kept.

A store newer than this code raises `TooNew` (exit 3); a write still locked out when the
busy timeout ends raises `Busy` (exit 75); a store that can't be opened or read raises
`Unreadable` (exit 1).

The row writes below run inside that transaction, given its connection. Each enforces the
rules of `moxi_projects.rules` and the store's own: keys unique ignoring case, roots unique
after `os.path.normcase`, the lead never also in the associated set. Each returns the
project as it now stands. Which kinds exist is not checked here: the acts ask the
contributions (`moxi_projects.contributions`) before they write.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import TypeVar

import moxi.store

from . import api
from .api import DEFAULT_KIND, NotFound, Project, TooNew, Unreadable
from .errors import Refused, Usage
from .rules import check_agent, check_key, check_kind_name, check_root, same_root

T = TypeVar("T")

#: The schema, one step per version: step n takes the store from version n-1 to n.
STEPS: tuple[str, ...] = (
    """
    CREATE TABLE projects (
        key        TEXT NOT NULL PRIMARY KEY COLLATE NOCASE,
        root       TEXT NOT NULL UNIQUE,
        root_real  TEXT NOT NULL,
        kind       TEXT NOT NULL DEFAULT 'project',
        lead       TEXT,
        notes_root TEXT,
        created    TEXT NOT NULL
    );
    CREATE TABLE project_agents (
        project_key TEXT NOT NULL REFERENCES projects(key),
        agent       TEXT NOT NULL,
        created     TEXT NOT NULL,
        PRIMARY KEY (project_key, agent)
    );
    """,
    """
    CREATE TABLE remove_status (
        key     TEXT NOT NULL PRIMARY KEY,
        project TEXT NOT NULL,
        outcome TEXT NOT NULL,
        created TEXT NOT NULL
    );
    """,
)

#: The schema version whose step made `remove_status`.
STATUS_VERSION = 2

#: How long a kept remove status answers a repeat, in seconds: a day.
STATUS_KEPT = 24 * 60 * 60

#: The fields `update` may change. The key is not one: other modules store it, so a
#: rename would have to rewrite their rows. `created` is a birth time.
FIELDS = ("root", "kind", "lead", "notes_root")

#: How long a write waits for another writer's lock, in seconds, before `Busy`.
TIMEOUT = 5.0


def path() -> str:
    """The store's file."""
    return api._store_path()


def now() -> str:
    """The time now, UTC, ISO 8601 with `Z`."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _status_cutoff() -> str:
    """The birth time before which a kept remove status no longer counts, a day ago, in
    `now()`'s spelling, which sorts as text in time order."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - STATUS_KEPT))


def _version(conn: sqlite3.Connection) -> int:
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


@contextmanager
def writing(where: str | None = None) -> Iterator[sqlite3.Connection]:
    """The write sequence around a block: connect, migrate, one transaction, close.

    The block gets the connection inside the transaction; what it raises rolls the
    transaction back and is raised again (a SQLite error mapped to `Busy` or
    `Unreadable`)."""
    where = where or path()
    try:
        conn = moxi.store.connect(where, timeout=TIMEOUT)
    except (moxi.store.StoreError, sqlite3.Error, OSError) as exc:
        raise api._unreadable(where, exc) from exc
    try:
        try:
            moxi.store.migrate(conn, STEPS)
        except moxi.store.StoreTooNew as exc:
            raise TooNew(where, exc.version, exc.known) from None
        with moxi.store.transaction(conn):
            version = _version(conn)
            if version != len(STEPS):
                # Another release migrated it between this code's migration and its lock.
                raise TooNew(where, version, len(STEPS))
            yield conn
    except Unreadable:
        raise
    except (sqlite3.Error, OSError) as exc:
        raise api._unreadable(where, exc) from exc
    finally:
        conn.close()


def write(fn: Callable[[sqlite3.Connection], T], where: str | None = None) -> T:
    """Run `fn(conn)` as one write (see `writing`) and return what it returned."""
    with writing(where) as conn:
        return fn(conn)


def setup() -> int:
    """The announcement's `SETUP`, which `moxi install projects` runs again on every
    upgrade: migrate an existing store to this code's schema. It never makes a store: a
    missing one is made by the first write. Returns the exit code, and prints why on a
    failure."""
    import sys

    where = path()
    try:
        if os.path.getsize(where) == 0:
            return 0  # created a moment ago by a write that will migrate it
    except FileNotFoundError:
        return 0
    except OSError as exc:
        return _say(api._unreadable(where, exc), sys.stderr)
    try:
        conn = moxi.store.connect(where, create=False, timeout=TIMEOUT)
    except moxi.store.StoreError as exc:
        if not os.path.exists(where):
            return 0
        return _say(api._unreadable(where, exc), sys.stderr)
    except (sqlite3.Error, OSError) as exc:
        return _say(api._unreadable(where, exc), sys.stderr)
    try:
        moxi.store.migrate(conn, STEPS)
    except moxi.store.StoreTooNew as exc:
        return _say(TooNew(where, exc.version, exc.known), sys.stderr)
    except (sqlite3.Error, OSError) as exc:
        return _say(api._unreadable(where, exc), sys.stderr)
    finally:
        conn.close()
    return 0


def _say(exc: api.ProjectsError, stream) -> int:
    print(f"moxi-projects: {exc.reason}", file=stream)
    if exc.fix:
        print(f"  fix: {exc.fix}", file=stream)
    return exc.exit_code


# --- reading inside a write -------------------------------------------------------------

_COLUMNS = "key, root, kind, lead, notes_root, created"


def project(conn: sqlite3.Connection, key: str) -> Project:
    """The project `key` names (ignoring case), read on `conn`; `NotFound` when none."""
    row = conn.execute(f"SELECT {_COLUMNS} FROM projects WHERE key = ?", (key,)).fetchone()
    if row is None:
        raise NotFound(key)
    stored, root, kind, lead, notes_root, created = row
    associated = tuple(agent for (agent,) in conn.execute(
        "SELECT agent FROM project_agents WHERE project_key = ? ORDER BY agent", (stored,)))
    return Project(stored, root, kind, lead, associated, notes_root, created)


def _root_owner(conn: sqlite3.Connection, root: str, besides: str | None = None) -> str | None:
    for key, stored in conn.execute("SELECT key, root FROM projects"):
        if key != besides and same_root(stored, root):
            return key
    return None


def refuse_root(root: str, owner: str) -> Refused:
    """The refusal of a root project `owner` already has."""
    return Refused(f"the root {root} is already project {owner!r}'s",
                   f"`moxi projects show {owner}` shows it; one directory is one project")


def refuse_key(key: str, stored: str) -> Refused:
    """The refusal of a key taken by the project stored as `stored`."""
    same = "" if stored == key else f" (as {stored!r}; keys ignore case)"
    return Refused(f"the key {key!r} is taken{same}",
                   f"`moxi projects show {stored}` shows it; pick another key")


def refuse_held_by_agents(current: Project) -> Refused:
    """The refusal to remove a project that still has associated agents."""
    names = " ".join(current.associated)
    return Refused(f"project {current.key!r} still has associated agents: {names}",
                   f"remove each with `moxi projects agents remove {current.key} NAME`")


def _refuse_lead_in_set(key: str, name: str) -> Refused:
    return Refused(f"{name!r} is an associated agent of {key!r}, and a lead is not also in "
                   "the associated set",
                   f"run `moxi projects agents remove {key} {name}` first")


# --- the row writes ---------------------------------------------------------------------

def insert(conn: sqlite3.Connection, *, key: str, root: str, kind: str = DEFAULT_KIND,
           lead: str | None = None, notes_root: str | None = None) -> Project:
    """Add a project row and return it."""
    key = check_key(key)
    root = check_root(root)
    kind = check_kind_name(kind)
    lead = None if lead is None else check_agent(lead, "lead")
    notes_root = None if notes_root is None else check_root(notes_root, "notes_root")
    taken = conn.execute("SELECT key FROM projects WHERE key = ?", (key,)).fetchone()
    if taken is not None:
        raise refuse_key(key, taken[0])
    owner = _root_owner(conn, root)
    if owner is not None:
        raise refuse_root(root, owner)
    try:
        conn.execute("INSERT INTO projects (key, root, root_real, kind, lead, notes_root, "
                     "created) VALUES (?, ?, ?, ?, ?, ?, ?)",
                     (key, root, os.path.realpath(root), kind, lead, notes_root, now()))
    except sqlite3.IntegrityError as exc:
        raise Refused(f"the key {key!r} or the root {root} is taken ({exc})",
                      "`moxi projects list` shows the projects") from exc
    return project(conn, key)


_UNSET = object()


def update(conn: sqlite3.Connection, key: str, *, root=_UNSET, kind=_UNSET, lead=_UNSET,
           notes_root=_UNSET) -> Project:
    """Change the named fields of project `key` and return it as it now stands.

    `lead=None` and `notes_root=None` clear them; `root` and `kind` can't be cleared. The
    root's resolved spelling is written again on every update."""
    changes = {name: value for name, value in
               (("root", root), ("kind", kind), ("lead", lead), ("notes_root", notes_root))
               if value is not _UNSET}
    if not changes:
        raise Usage("nothing to change: name at least one field",
                    "pass --root, --kind, --lead, --no-lead, --notes-root or "
                    "--no-notes-root")
    current = project(conn, key)
    key = current.key
    if "root" in changes:
        changes["root"] = check_root(changes["root"])
        owner = _root_owner(conn, changes["root"], besides=key)
        if owner is not None:
            raise refuse_root(changes["root"], owner)
    if "kind" in changes:
        changes["kind"] = check_kind_name(changes["kind"])
    if changes.get("lead") is not None:
        changes["lead"] = check_agent(changes["lead"], "lead")
        if changes["lead"] in current.associated:
            raise _refuse_lead_in_set(key, changes["lead"])
    if changes.get("notes_root") is not None:
        changes["notes_root"] = check_root(changes["notes_root"], "notes_root")
    changes["root_real"] = os.path.realpath(changes.get("root", current.root))
    names = sorted(changes)
    try:
        conn.execute(f"UPDATE projects SET {', '.join(f'{n} = ?' for n in names)} "
                     "WHERE key = ?", (*(changes[n] for n in names), key))
    except sqlite3.IntegrityError as exc:
        raise Refused(f"the root of {key!r} is taken ({exc})",
                      "`moxi projects list` shows the projects") from exc
    return project(conn, key)


def delete(conn: sqlite3.Connection, key: str) -> Project:
    """Remove project `key` and return the row as it was. Refused while it has associated
    agents."""
    current = project(conn, key)
    if current.associated:
        raise refuse_held_by_agents(current)
    conn.execute("DELETE FROM projects WHERE key = ?", (current.key,))
    return current


def keep_remove_status(conn: sqlite3.Connection, job: str, project: str,
                       outcome: dict) -> None:
    """Keep what the remove request `job` (its idempotency key) came to for project
    `project` (the key as asked): `outcome`, a dict of plain values. Rows older than a day
    go first, in the same write. A row `job` already has stands: a repeat gets the first
    answer."""
    conn.execute("DELETE FROM remove_status WHERE created < ?", (_status_cutoff(),))
    conn.execute("INSERT OR IGNORE INTO remove_status (key, project, outcome, created) "
                 "VALUES (?, ?, ?, ?)", (job, project, json.dumps(outcome), now()))


def remove_status(job: str, where: str | None = None) -> dict | None:
    """What the remove request `job` came to, as kept by `keep_remove_status`: its outcome
    with `job`, `project` and `created` beside it; None when no such request is kept (none
    was, it is older than a day, the store is missing or at a version before the table).

    A read: it never writes. Raises `Unreadable` (or its `TooNew` and `Busy`), also for a
    kept outcome that isn't a mapping with `state` and `key`, as a hand-edited or damaged
    row may be."""
    where = where or path()
    opened = api._open(where)
    if opened is None:
        return None
    conn, version = opened
    try:
        if version < STATUS_VERSION:
            return None
        row = conn.execute("SELECT project, outcome, created FROM remove_status "
                           "WHERE key = ? AND created >= ?",
                           (job, _status_cutoff())).fetchone()
        if row is None:
            return None
        project, outcome, created = row
        kept = json.loads(outcome)
        if not isinstance(kept, dict):
            raise ValueError(f"the outcome kept for a remove is {type(kept).__name__}")
        missing = [name for name in ("state", "key") if name not in kept]
        if missing:
            raise ValueError(f"the outcome kept for a remove has no {' or '.join(missing)}")
    except (sqlite3.Error, OSError, ValueError) as exc:
        raise api._unreadable(where, exc) from exc
    finally:
        conn.close()
    return {**kept, "job": job, "project": project, "created": created}


def add_agent(conn: sqlite3.Connection, key: str, name: str) -> Project:
    """Add `name` to project `key`'s associated set and return the project."""
    name = check_agent(name)
    current = project(conn, key)
    if name == current.lead:
        raise Refused(f"{name!r} is the lead of {current.key!r}, and a lead is not also in "
                      "the associated set", "nothing to do: the lead is already its agent")
    if name in current.associated:
        raise Refused(f"{name!r} is already associated with {current.key!r}",
                      f"`moxi projects show {current.key}` shows its agents")
    conn.execute("INSERT INTO project_agents (project_key, agent, created) VALUES (?, ?, ?)",
                 (current.key, name, now()))
    return project(conn, current.key)


def remove_agent(conn: sqlite3.Connection, key: str, name: str) -> Project:
    """Take `name` out of project `key`'s associated set and return the project. Refused
    when the pair isn't there."""
    current = project(conn, key)
    if name not in current.associated:
        fix = (f"it is the lead: clear it with `moxi projects set {current.key} --no-lead`"
               if name == current.lead else
               f"`moxi projects show {current.key}` shows its agents")
        raise Refused(f"{name!r} is not associated with {current.key!r}", fix)
    conn.execute("DELETE FROM project_agents WHERE project_key = ? AND agent = ?",
                 (current.key, name))
    return project(conn, current.key)


__all__ = ["STEPS", "STATUS_VERSION", "STATUS_KEPT", "FIELDS", "TIMEOUT", "path", "now",
           "writing", "write", "project", "refuse_root", "refuse_key",
           "refuse_held_by_agents", "insert", "update", "delete", "keep_remove_status",
           "remove_status", "add_agent", "remove_agent", "setup"]

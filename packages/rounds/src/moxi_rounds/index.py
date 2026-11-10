"""The index: a cache of what the logs say, so a listing of many rounds need not fold
every log.

`index.db` is in rounds' data directory. It holds nothing the logs do not: deleting it
loses no record, and `rebuild` makes the same rows from the logs again. Three tables:

    logs    one row per log this machine has read or written: the round's name (an empty
            slug for a project's own log), the file, its length and SHA-256 as last seen
            here and when that was, whether it could be read, and since when it has been
            known to have changed outside this machine
    rounds  one row per round: its folder, its status and its flow's name
    items   what each log holds open, one row per item, by kind

**Who writes it.** Every write to a log updates that log's rows before the lock is let go
(`on_append`, one of the write path's after-append callbacks). `refresh` and `rebuild`
bring rows up to the logs as they stand; they are for the commands that show rounds. No
other call writes: `read` and `rounds` open the index without creating or changing it,
and for a log whose row is missing or stale they fold the log themselves and answer from
that, leaving the row as it was.

**Stale.** A row is fresh when the log's bytes are the bytes the row was made from (same
length, same SHA-256) by this version of the code (`DERIVED`). Anything else is stale.

**Changed outside this machine.** A log may be written on another machine and arrive
here, so more lines after the bytes already seen are simply read. But when those bytes
themselves differ, or the file is shorter than it was, something rewrote history this
machine had read. The log is still read as it stands, and its summary carries
`changed_since`, the time this machine last saw the old bytes, until the next event is
appended here.

**A later kind of record** joins the index in one of two ways. Something a log holds
open (a question, a waiting item) is a row of `items`: add its kind to `ITEMS` with the
function that lists it from a `State`, and add one to `DERIVED` so rows made before it
count as stale. Something that needs a column or a table of its own adds a step to
`STEPS`, fills it in `_put` and reads it in `_stored`, and adds one to `DERIVED` too.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from moxi import store

from . import folders, log, paths, questions, state
from .errors import NoLog, Unreadable

#: The schema, one step per version of it (`moxi.store.migrate`). A step is never
#: edited once released; a change is a new step.
STEPS = ("""
    CREATE TABLE logs (
        project TEXT NOT NULL,
        slug TEXT NOT NULL,
        path TEXT NOT NULL,
        length INTEGER NOT NULL,
        sha256 TEXT NOT NULL,
        readable INTEGER NOT NULL,
        derived INTEGER NOT NULL,
        seen_at TEXT NOT NULL,
        changed_since TEXT,
        PRIMARY KEY (project, slug)
    );
    CREATE TABLE rounds (
        project TEXT NOT NULL,
        slug TEXT NOT NULL,
        folder TEXT NOT NULL,
        status TEXT,
        flow TEXT,
        PRIMARY KEY (project, slug)
    );
    CREATE TABLE items (
        project TEXT NOT NULL,
        slug TEXT NOT NULL,
        kind TEXT NOT NULL,
        id TEXT NOT NULL,
        position INTEGER NOT NULL,
        body TEXT NOT NULL,
        PRIMARY KEY (project, slug, kind, id)
    );
""",)

#: Counts the changes to what a row is made from. A row made under another count is
#: stale, so it is not trusted after an upgrade that derives more from a log.
DERIVED = 2

#: What a log holds open, by kind: each function is given the log's `State` and lists
#: `(id, body)` pairs, the body a JSON-ready mapping. A question is here while it is
#: recorded as open or parked.
ITEMS: dict[str, Callable[[state.State], Iterable[tuple[str, Mapping]]]] = {
    "question": questions.waiting}

#: The slug the tables give a project's own log.
_PROJECT = ""


@dataclass(frozen=True)
class Item:
    """One thing a log holds open: its kind (a key of `ITEMS`), its id and its body."""

    kind: str
    id: str
    body: Mapping


@dataclass(frozen=True)
class Summary:
    """What the index answers about one log: a round's, or with no `slug` a project's
    own. `folder` is the round's folder, or for a project its rounds folder. A round
    whose log can't be read has its error in `unreadable` and nothing else from the log.
    `changed_since` is set while the log is known to have changed outside this machine."""

    project: str
    slug: str | None
    folder: Path
    status: str | None = None
    flow: str | None = None
    items: tuple[Item, ...] = ()
    changed_since: str | None = None
    unreadable: Unreadable | None = None

    @property
    def name(self) -> str:
        """The round's name, or the project's key."""
        return self.project if self.slug is None else f"{self.project}/{self.slug}"

    @property
    def notice(self) -> str | None:
        """The line to show while the log is known to have changed outside this
        machine, or None."""
        if self.changed_since is None:
            return None
        return f"log changed outside this machine since {self.changed_since}"


@dataclass(frozen=True)
class Disagreement:
    """One way the index and the logs differ: `name` is the round or the project, and
    `problem` finishes the sentence that starts with it."""

    name: str
    problem: str


# -- what the write path and the showing commands call ------------------------------------

def on_append(written) -> None:
    """Bring the index up to the logs a write just touched. Called by the write path
    with its `Written`, the lock still held."""
    where = written.where
    wrote_round = len(written.round_events) > len(written.view.events)
    wrote_project = len(written.project_events) > len(written.view.project_events)
    with _writing(None) as conn:
        if where.slug is not None:
            _refresh(conn, where.project, where.slug, where.folder, wrote=wrote_round)
        # A round's verb that only read the project's log holds no lock on it, so that
        # log's row is left for whoever writes it.
        if where.slug is None or wrote_project:
            _refresh(conn, where.project, _PROJECT, where.folder, wrote=wrote_project)


def refresh(project: str, folder: Path, slug: str | None = None, *, env=None) -> Summary:
    """Bring the index up to one log as it stands, and answer from it: the round
    `<project>/<slug>` in the rounds folder `folder`, or the project's own log. For a
    command that shows a round. Raises `NoLog` for a round with no log."""
    with _writing(env) as conn:
        found = _refresh(conn, project, _PROJECT if slug is None else slug, folder,
                         wrote=False)
    if found is None:
        if slug is not None:
            raise NoLog(log.round_log(Path(folder) / slug))
        return Summary(project, None, Path(folder))
    return found


def rebuild(homes: Iterable[folders.Home], *, env=None) -> None:
    """Make the index what the logs of the projects `homes` say: a row for every log
    found there, and no row for anything else."""
    with _writing(env) as conn:
        kept = set()
        for home in homes:
            for slug in (_PROJECT, *folders.slugs(home.folder)):
                if _refresh(conn, home.project, slug, home.folder, wrote=False):
                    kept.add((home.project, slug))
        for project, slug in conn.execute("SELECT project, slug FROM logs").fetchall():
            if (project, slug) not in kept:
                _drop(conn, project, slug)


# -- reads, which write nothing -------------------------------------------------------------

def read(project: str, folder: Path, slug: str | None = None, *, env=None) -> Summary:
    """The round `<project>/<slug>` in the rounds folder `folder`, or with no slug the
    project's own log, from the index when its row is fresh and from the log itself when
    not. Writes nothing. Raises `NoLog` for a round with no log; a project with none
    holds nothing."""
    with _reading(env) as conn:
        return _read(conn, project, _PROJECT if slug is None else slug, Path(folder))


def rounds(project: str, folder: Path, *, env=None) -> list[Summary]:
    """Every round in the rounds folder `folder` of the project `project`, by slug, each
    as `read` gives it. Writes nothing."""
    found = []
    with _reading(env) as conn:
        for slug in folders.slugs(folder):
            try:
                found.append(_read(conn, project, slug, Path(folder)))
            except NoLog:  # removed since the folder was listed
                continue
    return found


def disagreements(homes: Iterable[folders.Home], *, env=None) -> list[Disagreement]:
    """Every way the index differs from the logs of the projects `homes`: a log with no
    row, a row whose log has changed since or says something else than the log does, and
    a row with no log. Empty when they agree, and when there is no index yet: the next
    write makes one. Writes nothing."""
    found = []
    with _reading(env) as conn:
        if conn is None:
            return found
        seen = set()
        for home in homes:
            for slug in (_PROJECT, *folders.slugs(home.folder)):
                name = home.project if slug == _PROJECT else f"{home.project}/{slug}"
                path = _log_path(Path(home.folder), slug)
                try:
                    data = path.read_bytes()
                except FileNotFoundError:
                    continue
                seen.add((home.project, slug))
                stored = _stored(conn, home.project, slug)
                if stored is None:
                    found.append(Disagreement(name, "has a log and no row in the index"))
                elif not _fresh(stored, path, data):
                    found.append(Disagreement(
                        name, "has a log that changed since the index last read it"))
                elif stored.row != _derive(home.project, slug, path, data)[0]:
                    found.append(Disagreement(
                        name, "has rows in the index that its log does not give"))
        for project, slug in conn.execute("SELECT project, slug FROM logs ORDER BY 1, 2"):
            if (project, slug) not in seen:
                name = project if slug == _PROJECT else f"{project}/{slug}"
                found.append(Disagreement(name, "has a row in the index and no log"))
    return found


# -- a log, as the index holds it ---------------------------------------------------------

@dataclass(frozen=True)
class _Row:
    """Everything the index keeps that the log's bytes alone decide."""

    project: str
    slug: str
    path: str
    length: int
    sha256: str
    readable: bool
    status: str | None = None
    flow: str | None = None
    items: tuple[Item, ...] = ()


@dataclass(frozen=True)
class _Stored:
    """A row as found in the index, with what depends on when it was made."""

    row: _Row
    derived: int
    seen_at: str
    changed_since: str | None


def _log_path(folder: Path, slug: str) -> Path:
    return log.project_log(folder) if slug == _PROJECT else log.round_log(folder / slug)


def _derive(project: str, slug: str, path: Path,
            data: bytes) -> tuple[_Row, Unreadable | None]:
    """The row the bytes `data` of the log `path` give, and the error when they can't be
    read as a log."""
    digest = hashlib.sha256(data).hexdigest()
    try:
        folded = state.fold(log.parse(path, data))
    except Unreadable as error:
        return _Row(project, slug, str(path), len(data), digest, readable=False), error
    items = tuple(Item(kind, item_id, _plain(body))
                  for kind, listed in ITEMS.items() for item_id, body in listed(folded))
    return _Row(project, slug, str(path), len(data), digest, readable=True,
                status=folded.status, flow=folded.flow, items=items), None


def _plain(body: Mapping) -> dict:
    """`body` as it reads back from the index, so a row derived and a row stored are
    equal when they hold the same."""
    return json.loads(json.dumps(body))


def _fresh(stored: _Stored | None, path: Path, data: bytes) -> bool:
    """Whether the row `stored` was made from exactly the bytes `data` of `path`, by
    this version's rules."""
    return (stored is not None and stored.derived == DERIVED
            and stored.row.path == str(path) and stored.row.length == len(data)
            and stored.row.sha256 == hashlib.sha256(data).hexdigest())


def _changed_since(stored: _Stored | None, path: Path, data: bytes) -> str | None:
    """Since when the log `path`, now holding `data`, is known to have changed outside
    this machine, or None: the time already kept, else the time the row's bytes were
    seen when they are no longer how the file starts."""
    if stored is None or stored.row.path != str(path):
        return None
    if stored.changed_since is not None:
        return stored.changed_since
    known = stored.row.length
    if len(data) < known or hashlib.sha256(data[:known]).hexdigest() != stored.row.sha256:
        return stored.seen_at
    return None


def _summary(row: _Row, error: Unreadable | None, folder: Path,
             changed_since: str | None) -> Summary:
    project_own = row.slug == _PROJECT
    return Summary(project=row.project, slug=None if project_own else row.slug,
                   folder=folder if project_own else folder / row.slug,
                   status=row.status, flow=row.flow, items=row.items,
                   changed_since=changed_since, unreadable=error)


def _read(conn, project: str, slug: str, folder: Path) -> Summary:
    path = _log_path(folder, slug)
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        if slug != _PROJECT:
            raise NoLog(path) from None
        return Summary(project, None, folder)
    stored = None if conn is None else _stored(conn, project, slug)
    changed_since = _changed_since(stored, path, data)
    if _fresh(stored, path, data) and stored.row.readable:
        return _summary(stored.row, None, folder, changed_since)
    # Stale, or a log that could not be read (its error is not kept): the log itself
    # answers, and the row stays as it is.
    row, error = _derive(project, slug, path, data)
    return _summary(row, error, folder, changed_since)


# -- the store ------------------------------------------------------------------------------

@contextmanager
def _writing(env) -> Iterator[sqlite3.Connection]:
    """The index open for a change, made and brought to this schema when need be, inside
    one transaction."""
    conn = store.connect(paths.index_file(env))
    try:
        store.migrate(conn, STEPS)
        with store.transaction(conn):
            yield conn
    finally:
        conn.close()


@contextmanager
def _reading(env) -> Iterator[sqlite3.Connection | None]:
    """The index open for a read, or None when there is none a read can use: no file,
    one not set up yet, one of another schema, or one that can't be opened. A read then
    answers from the logs. Opening creates and changes nothing."""
    conn = _open(paths.index_file(env))
    try:
        yield conn
    finally:
        if conn is not None:
            conn.close()


def _open(path: Path) -> sqlite3.Connection | None:
    try:
        if os.path.getsize(path) == 0:
            # Created a moment ago by a write that has not set it up; opening it for a
            # read must not do that for it.
            return None
        conn = store.connect(path, create=False)
    except (OSError, store.StoreError, sqlite3.Error):
        return None
    try:
        version = int(conn.execute("PRAGMA user_version").fetchone()[0])
    except sqlite3.Error:
        version = None
    if version != len(STEPS):
        conn.close()
        return None
    return conn


def _stored(conn, project: str, slug: str) -> _Stored | None:
    """The row of one log as the index holds it, or None: no row, or an index that
    can't be read, which is the same to a caller that then reads the log."""
    try:
        found = conn.execute(
            "SELECT logs.path, logs.length, logs.sha256, logs.readable, logs.derived, "
            "logs.seen_at, logs.changed_since, rounds.status, rounds.flow "
            "FROM logs LEFT JOIN rounds USING (project, slug) "
            "WHERE logs.project = ? AND logs.slug = ?", (project, slug)).fetchone()
        if found is None:
            return None
        items = tuple(Item(kind, item_id, json.loads(body)) for kind, item_id, body in
                      conn.execute("SELECT kind, id, body FROM items WHERE project = ? "
                                   "AND slug = ? ORDER BY position", (project, slug)))
    except (sqlite3.Error, ValueError):
        return None
    path, length, digest, readable, derived, seen_at, changed_since, status, flow = found
    return _Stored(_Row(project, slug, path, length, digest, bool(readable), status, flow,
                        items), derived, seen_at, changed_since)


def _refresh(conn, project: str, slug: str, folder: Path, *, wrote: bool) -> Summary | None:
    """Make one log's rows what the log says now, inside the caller's transaction, and
    answer from them; None, with its rows removed, when the log is not there. `wrote`
    says an event was just appended here, which ends a changed-outside notice."""
    folder = Path(folder)
    path = _log_path(folder, slug)
    try:
        # Read inside the transaction: of two refreshes of one log, the one that writes
        # its row last is then the one that read the file last.
        data = path.read_bytes()
    except FileNotFoundError:
        _drop(conn, project, slug)
        return None
    changed_since = None if wrote else _changed_since(_stored(conn, project, slug), path,
                                                      data)
    row, error = _derive(project, slug, path, data)
    _drop(conn, project, slug)
    _put(conn, row, folder, seen_at=log.stamp(), changed_since=changed_since)
    return _summary(row, error, folder, changed_since)


def _put(conn, row: _Row, folder: Path, *, seen_at: str, changed_since: str | None) -> None:
    conn.execute("INSERT INTO logs (project, slug, path, length, sha256, readable, "
                 "derived, seen_at, changed_since) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                 (row.project, row.slug, row.path, row.length, row.sha256,
                  int(row.readable), DERIVED, seen_at, changed_since))
    if row.slug != _PROJECT:
        conn.execute("INSERT INTO rounds (project, slug, folder, status, flow) "
                     "VALUES (?, ?, ?, ?, ?)",
                     (row.project, row.slug, str(folder / row.slug), row.status, row.flow))
    conn.executemany(
        "INSERT INTO items (project, slug, kind, id, position, body) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [(row.project, row.slug, item.kind, item.id, position,
          json.dumps(item.body, sort_keys=True))
         for position, item in enumerate(row.items)])


def _drop(conn, project: str, slug: str) -> None:
    for table in ("logs", "rounds", "items"):
        conn.execute(f"DELETE FROM {table} WHERE project = ? AND slug = ?", (project, slug))


__all__ = ["STEPS", "DERIVED", "ITEMS", "Item", "Summary", "Disagreement", "on_append",
           "refresh", "rebuild", "read", "rounds", "disagreements"]

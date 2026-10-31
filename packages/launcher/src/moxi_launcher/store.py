"""The launch log: one SQLite file holding a row per launch, its session ids, its events and
the handoffs given or shown to it.

The store lives at `STATE/launcher.db`. It is opened with `moxi.store.connect`, built and
moved forward with `moxi.store.migrate`, and every write runs in one
`moxi.store.transaction`. Schema version 1 has four tables:

- `launches`, a row per launch, keyed by its request id;
- `session_ids`, every session id a launch is known by (the one minted for it, and those a
  sender or the ready recorder learned), so `resolve` finds a launch from any of them;
- `events`, things that happened to a launch or a session, each a `kind` and a JSON detail;
- `picks`, the handoffs a launch was given or shown at startup.

Later versions add tables through `migrate`; the columns here are read unchanged.

**A later step never moves a row's state back.** The states go forward in one order,
`spawning`, `started`, `ready`, `sending`, `first-sent`, and `failed` ends any of the first
four. Every state write is one conditional update naming the states it may leave, so a
write that comes late matches no row and changes nothing, whatever order the writes land
in. Facts that are not the state (the pid, the pane, session ids, events, the claim) are
written whatever the state.

**A value is never cut to fit.** A brief, `values_json` or `argv_json` past `CAP` bytes is
refused with `TooLarge`, naming the field.

The caller owns a connection's lifetime: open one with `connect`, use it, close it.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, NamedTuple

from moxi import exit as exits
from moxi import store
from moxi.paths import Layout

MODULE = "launcher"
FILE = "launcher.db"

#: The largest brief, `values_json` or `argv_json` the store keeps, in bytes.
CAP = 64 * 1024

HARNESSES = ("claude", "codex")
TARGETS = ("terminal", "headless", "slot")
PRESET_KINDS = ("seat", "project")
#: The event a claim that moved to a new endpoint records.
RECLAIMED = "reclaimed"
#: Every state, in the order a launch moves through them; `failed` may end any but the last
#: of the others.
STATES = ("spawning", "started", "ready", "sending", "first-sent", "failed")
#: The states each state may be written from. Nothing leaves `first-sent` or `failed`.
LEAVES = {
    "started": ("spawning",),
    "ready": ("spawning", "started"),
    "sending": ("spawning", "started", "ready"),
    "first-sent": ("sending",),
    "failed": ("spawning", "started", "ready", "sending"),
}
#: The states a row may still be in when its launch is under way.
UNDER_WAY = ("spawning", "sending")
#: Where a session id came from.
SOURCES = ("minted", "sender", "startup", "resume", "clear", "compact")
#: How a handoff reached a launch: handed to it at insert, or shown at startup.
HOWS = ("given", "shown")
#: The facts a write may set whatever the state.
FACTS = ("pid", "pid_started", "pane_id", "slot")

SCHEMA_1 = """
CREATE TABLE launches (
  request_id TEXT PRIMARY KEY,
  created_at TEXT NOT NULL,
  actor TEXT NOT NULL,
  harness TEXT NOT NULL,
  preset_kind TEXT CHECK (preset_kind IN ('seat','project') OR preset_kind IS NULL),
  preset_name TEXT,
  target TEXT NOT NULL CHECK (target IN ('terminal','headless','slot')),
  slot TEXT,
  pane_id TEXT,
  cwd TEXT NOT NULL,
  project_key TEXT,
  identity TEXT,
  scope TEXT,
  session_id TEXT,
  values_json TEXT NOT NULL,
  argv_json TEXT NOT NULL,
  env_json TEXT NOT NULL,
  posture TEXT NOT NULL,
  brief TEXT,
  handoff_path TEXT,
  relaunch_of TEXT,
  resume_of TEXT,
  pid INTEGER,
  pid_started REAL,
  endpoint TEXT,
  state TEXT NOT NULL,
  reason TEXT,
  updated_at TEXT NOT NULL
);
CREATE TABLE session_ids (
  session_id TEXT PRIMARY KEY,
  request_id TEXT NOT NULL REFERENCES launches,
  source TEXT NOT NULL,
  seen_at TEXT NOT NULL
);
CREATE TABLE events (
  id INTEGER PRIMARY KEY,
  request_id TEXT,
  session_id TEXT,
  at TEXT NOT NULL,
  kind TEXT NOT NULL,
  detail_json TEXT NOT NULL
);
CREATE TABLE picks (
  path TEXT NOT NULL,
  key TEXT NOT NULL,
  session_id TEXT,
  request_id TEXT,
  how TEXT NOT NULL CHECK (how IN ('given','shown')),
  at TEXT NOT NULL
);
CREATE INDEX launches_session_id ON launches(session_id);
CREATE INDEX launches_created_at ON launches(created_at);
CREATE INDEX events_request_at ON events(request_id, at);
CREATE INDEX events_session_at ON events(session_id, at);
CREATE INDEX picks_key_at ON picks(key, at);
"""

#: The schema's steps, one per version; `PRAGMA user_version` counts those run.
STEPS: tuple[str, ...] = (SCHEMA_1,)


class TooLarge(store.StoreError):
    """A value is past the store's cap. It is refused, never cut."""

    exit_code = exits.REFUSED

    def __init__(self, field: str, size: int, largest: tuple[str, int] | None = None):
        self.field = field
        self.size = size
        self.largest = largest
        part = (f", the largest part {largest[0]} ({largest[1]} bytes)"
                if largest is not None else "")
        super().__init__(f"{field} is {size} bytes, past the launch log's cap of {CAP}"
                         f"{part}; it is refused, not cut")


class Busy(store.StoreError):
    """The store's write lock was held past the busy timeout. Trying again may work."""

    exit_code = exits.TEMPFAIL


class SessionTaken(store.StoreError):
    """The session id given for a launch already belongs to another launch."""

    exit_code = exits.REFUSED

    def __init__(self, session_id: str, request_id: str):
        self.session_id = session_id
        self.request_id = request_id
        super().__init__(f"session id {session_id} already belongs to launch {request_id}")


@dataclass(frozen=True)
class LaunchRow:
    """One `launches` row, its columns by name.

    `newest_session_id` is set only by `resolve`: the session id this launch was last
    known by, which after a clear or a compaction is not the one it started with.
    """

    request_id: str
    created_at: str
    actor: str
    harness: str
    preset_kind: str | None
    preset_name: str | None
    target: str
    slot: str | None
    pane_id: str | None
    cwd: str
    project_key: str | None
    identity: str | None
    scope: str | None
    session_id: str | None
    values_json: str
    argv_json: str
    env_json: str
    posture: str
    brief: str | None
    handoff_path: str | None
    relaunch_of: str | None
    resume_of: str | None
    pid: int | None
    pid_started: float | None
    endpoint: str | None
    state: str
    reason: str | None
    updated_at: str
    newest_session_id: str | None = None

    @property
    def values(self) -> dict[str, Any]:
        return json.loads(self.values_json)

    @property
    def argv(self) -> list[str]:
        return json.loads(self.argv_json)

    @property
    def env(self) -> dict[str, str]:
        return json.loads(self.env_json)

    def stale(self, after_s: float, now: datetime | None = None) -> bool:
        """Whether the row is still `spawning` or `sending` more than `after_s` seconds
        after it was made. The row is never rewritten; it only reads as stale."""
        if self.state not in UNDER_WAY:
            return False
        now = now or datetime.now(timezone.utc)
        return (now - parse_time(self.created_at)).total_seconds() > after_s

    def as_json(self, *, stale_after: float | None = None,
                now: datetime | None = None) -> dict[str, Any]:
        """The row as a JSON object: every column, the three JSON columns expanded, and
        `stale` when `stale_after` is given."""
        out = {f.name: getattr(self, f.name) for f in fields(self)
               if f.name != "newest_session_id"}
        out["values_json"] = self.values
        out["argv_json"] = self.argv
        out["env_json"] = self.env
        if stale_after is not None:
            out["stale"] = self.stale(stale_after, now)
        return out


COLUMNS = tuple(f.name for f in fields(LaunchRow) if f.name != "newest_session_id")
_SELECT = "SELECT " + ", ".join(COLUMNS) + " FROM launches"


class Inserted(NamedTuple):
    """What `insert` did: the row now stored, and whether this call made it."""

    row: LaunchRow
    new: bool


@dataclass(frozen=True)
class Event:
    id: int
    request_id: str | None
    session_id: str | None
    at: str
    kind: str
    detail: dict[str, Any]


# -- opening ---------------------------------------------------------------------------


def path(env: Mapping[str, str] | None = None) -> Path:
    """Where the launch log lives: `launcher.db` in the launcher's state directory."""
    return Layout(MODULE).state_dir(env) / FILE


def connect(db: str | Path | None = None, *, env: Mapping[str, str] | None = None,
            create: bool = True, timeout: float = 5.0) -> sqlite3.Connection:
    """Open the launch log and bring its schema up to date.

    `db` defaults to `path(env)`. With `create=False` a missing store raises
    `moxi.store.StoreError`. A store written by a newer launcher raises
    `moxi.store.StoreTooNew`.
    """
    conn = store.connect(path(env) if db is None else db, create=create, timeout=timeout)
    try:
        store.migrate(conn, STEPS)
    except BaseException:
        conn.close()
        raise
    return conn


# -- writing ---------------------------------------------------------------------------


def now() -> str:
    """The time now, UTC, as the store writes it."""
    return stamp(datetime.now(timezone.utc))


def stamp(moment: datetime) -> str:
    """`moment` as the store writes times: UTC ISO 8601 to the microsecond, with `Z`, so
    times sort as text."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def parse_time(text: str) -> datetime:
    """A time as the store or a caller writes it; one with no zone is read as UTC."""
    moment = datetime.fromisoformat(text)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment


@contextmanager
def _writing(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """One write transaction; a write lock held past the busy timeout raises `Busy`."""
    try:
        with store.transaction(conn):
            yield conn
    except sqlite3.OperationalError as error:
        if _busy(error):
            raise Busy(f"the launch log is busy: {error}") from error
        raise


def _busy(error: sqlite3.OperationalError) -> bool:
    code = getattr(error, "sqlite_errorcode", None)
    if code is not None:
        return code & 0xFF in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED)
    text = str(error).lower()
    return "locked" in text or "busy" in text


def _encode(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _check_size(field: str, text: str | None) -> None:
    if text is not None and (size := len(text.encode("utf-8"))) > CAP:
        raise TooLarge(field, size)


def insert(conn: sqlite3.Connection, *, request_id: str, actor: str, harness: str,
           target: str, cwd: str, values: Mapping[str, Any], argv: Sequence[str],
           env: Mapping[str, str], posture: str, session_id: str | None = None,
           preset_kind: str | None = None, preset_name: str | None = None,
           slot: str | None = None, project_key: str | None = None,
           identity: str | None = None, scope: str | None = None,
           brief: str | None = None, handoff_path: str | None = None,
           handoff_key: str | None = None, relaunch_of: str | None = None,
           resume_of: str | None = None, at: str | None = None) -> Inserted:
    """Write a launch's row in state `spawning`, before anything is started.

    One transaction, with `synchronous=FULL` so the row is on disk before the spawn,
    writes the row, the `minted` session id of a Claude launch, and, when a handoff is
    given and `handoff_key` names the key it is filed under, its `given` pick.

    A request id already in the store writes nothing: the stored row comes back with
    `new` false, and the caller starts nothing, since that row's spawn may have run.

    `env` holds the stamped literals only, never a secret. Raises `TooLarge` for a value
    past `CAP`, `SessionTaken` when the session id is another launch's, `Busy` when the
    write lock is held past the timeout, and `ValueError` for a harness, target or preset
    kind the store doesn't know, or a Claude launch with no session id.
    """
    if harness not in HARNESSES:
        raise ValueError(f"unknown harness {harness!r}")
    if target not in TARGETS:
        raise ValueError(f"unknown target {target!r}")
    if preset_kind is not None and preset_kind not in PRESET_KINDS:
        raise ValueError(f"unknown preset kind {preset_kind!r}")
    if harness == "claude" and not session_id:
        raise ValueError("a Claude launch needs the session id minted for it")
    values_json = _encode(dict(values))
    argv_json = _encode(list(argv))
    env_json = _encode(dict(env))
    _check_size("brief", brief)
    _check_size("values_json", values_json)
    _check_size("argv_json", argv_json)
    at = at or now()
    row = {
        "request_id": request_id, "created_at": at, "actor": actor, "harness": harness,
        "preset_kind": preset_kind, "preset_name": preset_name, "target": target,
        "slot": slot, "pane_id": None, "cwd": cwd, "project_key": project_key,
        "identity": identity, "scope": scope, "session_id": session_id,
        "values_json": values_json, "argv_json": argv_json, "env_json": env_json,
        "posture": posture, "brief": brief, "handoff_path": handoff_path,
        "relaunch_of": relaunch_of, "resume_of": resume_of, "pid": None,
        "pid_started": None, "endpoint": None, "state": "spawning", "reason": None,
        "updated_at": at,
    }
    conn.execute("PRAGMA synchronous = FULL")
    try:
        with _writing(conn):
            if (stored := get(conn, request_id)) is not None:
                return Inserted(stored, False)
            if harness == "claude":
                holder = conn.execute(
                    "SELECT request_id FROM session_ids WHERE session_id = ?",
                    (session_id,)).fetchone()
                if holder is not None:
                    raise SessionTaken(session_id, holder[0])
            conn.execute(f"INSERT INTO launches ({', '.join(COLUMNS)}) "
                         f"VALUES ({', '.join('?' * len(COLUMNS))})",
                         [row[name] for name in COLUMNS])
            if harness == "claude":
                conn.execute("INSERT INTO session_ids (session_id, request_id, source, "
                             "seen_at) VALUES (?, ?, 'minted', ?)",
                             (session_id, request_id, at))
            if handoff_path and handoff_key:
                conn.execute("INSERT INTO picks (path, key, session_id, request_id, how, at) "
                             "VALUES (?, ?, ?, ?, 'given', ?)",
                             (handoff_path, handoff_key, session_id, request_id, at))
    finally:
        conn.execute("PRAGMA synchronous = NORMAL")
    return Inserted(LaunchRow(**row), True)


def set_state(conn: sqlite3.Connection, request_id: str, state: str, *,
              reason: str | None = None, leaving: Iterable[str] | None = None,
              at: str | None = None, **facts: Any) -> bool:
    """Move a launch's row to `state`, and say whether it moved.

    The row moves only from a state `state` may be written from (`LEAVES`), narrowed to
    `leaving` when it is given; a row in any other state is left as it is, so a write that
    comes after a later one changes nothing. `reason`, a short code, is written with the
    state. `facts` (any of `FACTS`) are written in the same transaction whatever the
    state.
    """
    if state not in LEAVES:
        raise ValueError(f"no row is moved to {state!r}")
    allowed = LEAVES[state]
    if leaving is not None:
        leaving = tuple(leaving)
        if bad := [s for s in leaving if s not in allowed]:
            raise ValueError(f"{state} is never written from {', '.join(bad)}")
        allowed = leaving
    at = at or now()
    with _writing(conn):
        _write_facts(conn, request_id, facts, at)
        cursor = conn.execute(
            f"UPDATE launches SET state = ?, reason = ?, updated_at = ? "
            f"WHERE request_id = ? AND state IN ({', '.join('?' * len(allowed))})",
            (state, reason, at, request_id, *allowed))
        return cursor.rowcount == 1


def set_facts(conn: sqlite3.Connection, request_id: str, *, at: str | None = None,
              **facts: Any) -> bool:
    """Write facts (any of `FACTS`) to a launch's row whatever its state, and say whether
    the row exists."""
    with _writing(conn):
        return _write_facts(conn, request_id, facts, at or now())


def _write_facts(conn: sqlite3.Connection, request_id: str, facts: Mapping[str, Any],
                 at: str) -> bool:
    if bad := [name for name in facts if name not in FACTS]:
        raise ValueError(f"not a fact of a launch: {', '.join(bad)}")
    if not facts:
        return False
    names = list(facts)
    cursor = conn.execute(
        f"UPDATE launches SET {', '.join(f'{n} = ?' for n in names)}, updated_at = ? "
        "WHERE request_id = ?", (*(facts[n] for n in names), at, request_id))
    return cursor.rowcount == 1


def learn_session(conn: sqlite3.Connection, request_id: str, session_id: str,
                  source: str, *, at: str | None = None) -> bool:
    """Record a session id a launch is known by, whatever its state, and say whether it is
    new.

    The id joins `session_ids` unless it is there already, and becomes the row's
    `session_id` when the row has none. The state is not touched.
    """
    if source not in SOURCES:
        raise ValueError(f"unknown session id source {source!r}")
    at = at or now()
    with _writing(conn):
        if get(conn, request_id) is None:
            return False
        cursor = conn.execute(
            "INSERT OR IGNORE INTO session_ids (session_id, request_id, source, seen_at) "
            "VALUES (?, ?, ?, ?)", (session_id, request_id, source, at))
        added = cursor.rowcount == 1
        conn.execute("UPDATE launches SET session_id = ?, updated_at = ? "
                     "WHERE request_id = ? AND session_id IS NULL",
                     (session_id, at, request_id))
        return added


def claim(conn: sqlite3.Connection, request_id: str, *, harness: str,
          session_id: str | None, endpoint: str, at: str | None = None) -> LaunchRow | None:
    """Claim a launch for the turns endpoint `endpoint`, and return the row when the launch
    is that endpoint's, else None.

    A launch belongs to the one session process the launcher started for it. The first
    endpoint whose event matches the row claims it: the same harness, and for Claude the
    session id minted for the launch; for Codex a launch composed for turns' host whose id
    is not known yet, or is the id the sender learned. One conditional update writes the
    claim, so of two endpoints only the first wins. Any other endpoint carrying the
    launch's stamp (a session started from inside the launched one) gets None.

    A held launch is claimed again only when the endpoint holding it is gone and the new
    endpoint's event names a session id the launch is known by: the row's own, or any
    recorded for it in `session_ids` (only the endpoint holding a launch records its ids,
    so an id there is the launch's own session after a clear or a compaction). So a
    session whose endpoint was replaced (Claude starts a new one when its plugins reload)
    keeps its launch, before or after a `/clear`. Gone
    means the held endpoint's process has ended (`endpoint_gone`), never that it did not
    answer once. The claim moves in one conditional update on the held endpoint, so of
    two new endpoints only one takes it, and a `reclaimed` event records the move.
    """
    if harness not in HARNESSES:
        return None
    moment = at or now()
    with _writing(conn):
        conn.execute(
            "UPDATE launches SET endpoint = ?, updated_at = ? "
            "WHERE request_id = ? AND harness = ? AND endpoint IS NULL "
            "AND (harness <> 'codex' OR json_extract(values_json, '$.mode') = 'host') "
            "AND (session_id = ? OR (session_id IS NULL AND harness = 'codex'))",
            (endpoint, moment, request_id, harness, session_id))
        row = get(conn, request_id)
        held = row.endpoint if row is not None else None
        if (held is not None and held != endpoint and session_id is not None
                and row.harness == harness and _known_by(conn, row, session_id)
                and endpoint_gone(held)):
            moved = conn.execute(
                "UPDATE launches SET endpoint = ?, updated_at = ? "
                "WHERE request_id = ? AND endpoint = ?",
                (endpoint, moment, request_id, held))
            if moved.rowcount == 1:
                conn.execute(
                    "INSERT INTO events (request_id, session_id, at, kind, detail_json) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (request_id, session_id, moment, RECLAIMED,
                     _encode({"endpoint": endpoint, "was": held})))
                row = get(conn, request_id)
    return row if row is not None and row.endpoint == endpoint else None


def _known_by(conn: sqlite3.Connection, row: LaunchRow, session_id: str) -> bool:
    """Whether the launch `row` is known by `session_id`: its own, or one recorded for it."""
    if row.session_id == session_id:
        return True
    return conn.execute("SELECT 1 FROM session_ids WHERE session_id = ? AND request_id = ?",
                        (session_id, row.request_id)).fetchone() is not None


def endpoint_gone(endpoint: str) -> bool:
    """Whether the endpoint process a claim names has ended. A claim names it
    `<pid>@<start time>` (`listeners.endpoint_id`), and the process is gone when no
    process with that pid and start time runs; one named by its pid alone, its start time
    unread, is gone when no process has that pid. A name that is neither is never gone."""
    from moxi import proc

    pid_text, sep, started_text = endpoint.partition("@")
    try:
        pid = int(pid_text)
        started = float(started_text) if sep else None
    except ValueError:
        return False
    if started is None:
        return proc.identity(pid) is None
    return not proc.alive(proc.ProcId(pid, started))


def add_event(conn: sqlite3.Connection, kind: str, detail: Mapping[str, Any] | None = None,
              *, request_id: str | None = None, session_id: str | None = None,
              at: str | None = None) -> int:
    """Add an `events` row and return its id."""
    with _writing(conn):
        cursor = conn.execute(
            "INSERT INTO events (request_id, session_id, at, kind, detail_json) "
            "VALUES (?, ?, ?, ?, ?)",
            (request_id, session_id, at or now(), kind, _encode(dict(detail or {}))))
        return int(cursor.lastrowid)


def add_pick(conn: sqlite3.Connection, handoff_path: str, key: str, how: str, *,
             session_id: str | None = None, request_id: str | None = None,
             at: str | None = None) -> None:
    """Record that the handoff at `handoff_path`, filed under `key`, was given to a launch
    or shown at a session's start."""
    if how not in HOWS:
        raise ValueError(f"unknown pick {how!r}")
    with _writing(conn):
        conn.execute("INSERT INTO picks (path, key, session_id, request_id, how, at) "
                     "VALUES (?, ?, ?, ?, ?, ?)",
                     (handoff_path, key, session_id, request_id, how, at or now()))


# -- reading ---------------------------------------------------------------------------


def _row(cursor: sqlite3.Cursor, values: Sequence[Any] | None,
         **extra: Any) -> LaunchRow | None:
    if values is None:
        return None
    names = [column[0] for column in cursor.description]
    return LaunchRow(**dict(zip(names, values)), **extra)


def get(conn: sqlite3.Connection, request_id: str) -> LaunchRow | None:
    """The row of one launch, or None."""
    cursor = conn.execute(f"{_SELECT} WHERE request_id = ?", (request_id,))
    return _row(cursor, cursor.fetchone())


def resolve(conn: sqlite3.Connection, session_id: str) -> LaunchRow | None:
    """The launch a session id belongs to, with `newest_session_id` the id it was last
    known by; None when no launch holds the id.

    A Claude launch resolves by the id minted for it. A Codex launch resolves only once a
    session id was learned for it, which needs turns' Codex host; a Codex launch made
    without turns, or in `exec` mode, never does.
    """
    found = conn.execute("SELECT request_id FROM session_ids WHERE session_id = ?",
                         (session_id,)).fetchone()
    if found is None:
        return None
    newest = conn.execute("SELECT session_id FROM session_ids WHERE request_id = ? "
                          "ORDER BY seen_at DESC, rowid DESC LIMIT 1", found).fetchone()
    cursor = conn.execute(f"{_SELECT} WHERE request_id = ?", found)
    return _row(cursor, cursor.fetchone(), newest_session_id=newest[0])


def session_ids(conn: sqlite3.Connection, request_id: str) -> list[tuple[str, str]]:
    """Every session id a launch is known by, with its source, oldest first."""
    return [(sid, source) for sid, source in conn.execute(
        "SELECT session_id, source FROM session_ids WHERE request_id = ? "
        "ORDER BY seen_at, rowid", (request_id,))]


def log(conn: sqlite3.Connection, *, limit: int | None = None,
        before: str | datetime | None = None) -> list[LaunchRow]:
    """Launches newest first, at most `limit`, only those made before `before` when it is
    given (a time; one with no zone is UTC). Page on by passing the last row's
    `created_at`."""
    sql, params = _SELECT, []
    if before is not None:
        moment = before if isinstance(before, datetime) else parse_time(before)
        sql += " WHERE created_at < ?"
        params.append(stamp(moment))
    sql += " ORDER BY created_at DESC, rowid DESC"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(int(limit))
    cursor = conn.execute(sql, params)
    return [_row(cursor, values) for values in cursor.fetchall()]


def events(conn: sqlite3.Connection, request_id: str) -> list[Event]:
    """The events of one launch, oldest first."""
    return [Event(id, rid, sid, at, kind, json.loads(detail)) for id, rid, sid, at, kind,
            detail in conn.execute(
                "SELECT id, request_id, session_id, at, kind, detail_json FROM events "
                "WHERE request_id = ? ORDER BY at, id", (request_id,))]


def picked(conn: sqlite3.Connection, key: str) -> list[str]:
    """The paths of the handoffs filed under `key` that count as picked, newest first.

    A `shown` pick always counts. A `given` pick counts unless its launch ended `failed`
    (a launch refused after its row was written ends there too): a handoff handed to a
    launch that never ran is still waiting for its seat.
    """
    return [p for (p,) in conn.execute(
        "SELECT p.path FROM picks p LEFT JOIN launches l ON l.request_id = p.request_id "
        "WHERE p.key = ? AND NOT (p.how = 'given' AND l.state IS 'failed') "
        "ORDER BY p.at DESC, p.rowid DESC", (key,))]


# -- the log verb ----------------------------------------------------------------------


def stale_after_s(current: Mapping[str, Any] | None = None) -> float:
    """How long a row may stay `spawning` or `sending` before it reads as stale:
    `ready.wait_s` plus `first.wait_s`."""
    if current is None:
        from . import settings

        current = settings.current()
    return float(current["ready.wait_s"]) + float(current["first.wait_s"])


def log_lines(*, limit: int | None = None, before: str | datetime | None = None,
              db: str | Path | None = None, env: Mapping[str, str] | None = None,
              stale_after: float | None = None,
              when: datetime | None = None) -> list[str]:
    """What `moxi launcher log --json` prints: one JSON line per launch, newest first,
    `values_json`, `argv_json` and `env_json` expanded and `stale` set. A store not made
    yet has no launches; nothing is created to say so."""
    try:
        conn = connect(db, env=env, create=False)
    except store.StoreError as error:
        if isinstance(error, store.StoreTooNew):
            raise
        return []
    try:
        rows = log(conn, limit=limit, before=before)
    finally:
        conn.close()
    after = stale_after_s() if stale_after is None else stale_after
    moment = when or datetime.now(timezone.utc)
    return [_encode(row.as_json(stale_after=after, now=moment)) for row in rows]


__all__ = ["CAP", "COLUMNS", "FACTS", "HARNESSES", "HOWS", "LEAVES", "RECLAIMED", "SOURCES",
           "STATES", "STEPS", "TARGETS", "Busy", "Event", "Inserted", "LaunchRow",
           "SessionTaken", "TooLarge", "add_event", "add_pick", "claim", "connect",
           "endpoint_gone", "events", "get", "insert", "learn_session", "log", "log_lines", "now", "parse_time", "path", "picked",
           "resolve", "session_ids", "set_facts", "set_state", "stale_after_s", "stamp"]

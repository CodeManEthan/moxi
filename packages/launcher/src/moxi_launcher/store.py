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

Schema version 2 adds the acts on a launched session and changes two things of version 1:

- `runs`, one row per act (a relaunch, an exit, a clear, a compact, a keep-warm ask), and
  `run_events`, what happened during one;
- `pending_compacts`, a compact a crossing asked for while another run was open;
- `warm_holds`, the named holds that keep a launch warm;
- `exits`, one row per exit-session run, whatever its verdict;
- `session_ids` is keyed by the pair `(session_id, request_id)`, so a resumed session's id
  can belong to the launch that resumed it beside the one that started it; its rows are
  copied as they were, in the same order;
- `launches` gains `base_env_json`, null on every row made before version 2.

Each later version adds through `migrate`; no row of an earlier one is lost.

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
import secrets
import sqlite3
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, fields
from datetime import datetime, timedelta, timezone
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
#: A launch's kind, as `LaunchRow.kind` decides it: the first that holds, in this order.
KINDS = ("scratch", "seat", "project", "one-off")
#: The event a claim that moved to a new endpoint records.
RECLAIMED = "reclaimed"
#: The event a launch made from the page records, under its request id, when it is refused
#: before its row is written: no launch row exists to hold the refusal. Its detail is the
#: refusal's `code`, `row`, `problem` and `hint`.
LAUNCH_REFUSED = "launch-refused"
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

#: What a run does.
RUN_KINDS = ("relaunch", "exit", "clear", "compact", "keep-warm")
#: A run's states, in the one order it moves through them.
RUN_STATES = ("armed", "waiting", "acting", "ended")
#: The states each run state may be written from; a run is inserted `armed` or `acting`.
#: A state may be written again from itself, which a job does right before it acts, to
#: learn that its run is still open. Nothing leaves `ended`.
RUN_LEAVES = {
    "waiting": ("armed", "waiting"),
    "acting": ("armed", "waiting", "acting"),
    "ended": ("armed", "waiting", "acting"),
}
#: The fields a run's state write may set beside the state.
RUN_FIELDS = ("job", "slot", "pane_id", "turn_number", "new_request_id", "outcome", "word",
              "detail", "repeats")
#: How long after its insert a run is never read as stale, in seconds: the time its
#: spawner has to start the job and record it.
RUN_GRACE_S = 60.0
#: The outcome and word of a run ended because its job is gone.
JOB_GONE = ("error", "job-gone")
#: The longest detail text a run keeps, in bytes; a longer one loses its front.
DETAIL_CAP = 4 * 1024
#: The most rows of one table `prune` deletes in one call.
PRUNE_LIMIT = 1000

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

SCHEMA_2 = """
ALTER TABLE launches ADD COLUMN base_env_json TEXT;
CREATE TABLE session_ids_new (
  session_id TEXT NOT NULL,
  request_id TEXT NOT NULL REFERENCES launches,
  source TEXT NOT NULL,
  seen_at TEXT NOT NULL,
  PRIMARY KEY (session_id, request_id)
);
INSERT INTO session_ids_new (rowid, session_id, request_id, source, seen_at)
  SELECT rowid, session_id, request_id, source, seen_at FROM session_ids ORDER BY rowid;
DROP TABLE session_ids;
ALTER TABLE session_ids_new RENAME TO session_ids;
CREATE INDEX session_ids_session_id ON session_ids(session_id);
CREATE TABLE runs (
  run_id TEXT PRIMARY KEY,
  kind TEXT NOT NULL CHECK (kind IN ('relaunch','exit','clear','compact','keep-warm')),
  mode TEXT NOT NULL,
  request_id TEXT NOT NULL REFERENCES launches,
  session_id TEXT NOT NULL,
  actor TEXT NOT NULL,
  handoff_path TEXT,
  slot TEXT,
  pane_id TEXT,
  turn_number INTEGER,
  new_request_id TEXT,
  repair_of TEXT,
  crossing_key TEXT UNIQUE,
  job TEXT,
  state TEXT NOT NULL CHECK (state IN ('armed','waiting','acting','ended')),
  outcome TEXT,
  word TEXT,
  detail TEXT,
  repeats INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX runs_open ON runs(request_id) WHERE state <> 'ended';
CREATE TABLE run_events (
  id INTEGER PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES runs,
  at TEXT NOT NULL,
  kind TEXT NOT NULL,
  detail_json TEXT NOT NULL
);
CREATE TABLE pending_compacts (
  request_id TEXT PRIMARY KEY REFERENCES launches,
  crossing_key TEXT NOT NULL,
  at TEXT NOT NULL
);
CREATE TABLE warm_holds (
  request_id TEXT NOT NULL REFERENCES launches,
  name TEXT NOT NULL,
  opened_at TEXT NOT NULL,
  released_at TEXT
);
CREATE UNIQUE INDEX warm_open ON warm_holds(request_id, name) WHERE released_at IS NULL;
CREATE TABLE exits (
  id INTEGER PRIMARY KEY,
  at TEXT NOT NULL,
  run_id TEXT REFERENCES runs,
  request_id TEXT NOT NULL,
  session_id TEXT NOT NULL,
  verdict TEXT NOT NULL,
  word TEXT,
  detail TEXT,
  slot TEXT,
  pane_id TEXT
);
CREATE INDEX runs_session_at ON runs(session_id, created_at);
CREATE INDEX runs_request_at ON runs(request_id, created_at);
CREATE INDEX run_events_run_at ON run_events(run_id, at);
CREATE INDEX exits_at ON exits(at);
CREATE INDEX launches_relaunch_of ON launches(relaunch_of);
CREATE INDEX launches_resume_of ON launches(resume_of);
"""

#: The schema's steps, one per version; `PRAGMA user_version` counts those run.
STEPS: tuple[str, ...] = (SCHEMA_1, SCHEMA_2)


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


class RunOpen(store.StoreError):
    """The launch already has an open run; a second act loses at insert. `run` is the
    open one."""

    exit_code = exits.REFUSED

    def __init__(self, run: Run):
        self.run = run
        super().__init__(f"launch {run.request_id} has an open {run.kind} run, {run.run_id} "
                         f"({run.state})")


class SessionTaken(store.StoreError):
    """The session id given for a launch already belongs to another launch."""

    exit_code = exits.REFUSED

    def __init__(self, session_id: str, request_id: str):
        self.session_id = session_id
        self.request_id = request_id
        super().__init__(f"session id {session_id} already belongs to launch {request_id}")


class SessionLive(store.StoreError):
    """A resume met another launch of the same session that is still starting or whose
    process runs: two resumes of one session never both launch. `holder` is that launch's
    row, and `how` is `starting` or `live`."""

    exit_code = exits.REFUSED

    def __init__(self, session_id: str, holder: LaunchRow, how: str):
        self.session_id = session_id
        self.holder = holder
        self.how = how
        said = "is still starting it" if how == "starting" else "runs it"
        super().__init__(f"session {session_id}: launch {holder.request_id} {said}")


@dataclass(frozen=True)
class LaunchRow:
    """One `launches` row, its columns by name.

    `newest_session_id` is set only by `resolve`: the session id this launch was last
    known by, which after a clear or a compaction is not the one it started with.
    `base_env_json` is null on a row made before schema version 2.
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
    base_env_json: str | None = None
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

    @property
    def kind(self) -> str:
        """The launch's kind, the first of `KINDS` that holds: `scratch` when it works in
        a scratch folder, whatever its preset; `seat` for a seat preset; `project` when it
        names a project, by a project preset or a project key; else `one-off`. Scratch
        comes first so that a seat launched in a scratch folder, as a test tool does, reads
        as scratch."""
        if self.values.get("scratch"):
            return "scratch"
        if self.preset_kind == "seat":
            return "seat"
        if self.preset_kind == "project" or self.project_key:
            return "project"
        return "one-off"

    @property
    def base_env(self) -> dict[str, str | None] | None:
        """The base environment recorded for the launch, or None when none was. A
        credential that was set at launch is there with None in place of its value."""
        return None if self.base_env_json is None else json.loads(self.base_env_json)

    def stale(self, after_s: float, now: datetime | None = None) -> bool:
        """Whether the row is still `spawning` or `sending` more than `after_s` seconds
        after it was made. The row is never rewritten; it only reads as stale."""
        if self.state not in UNDER_WAY:
            return False
        now = now or datetime.now(timezone.utc)
        return (now - parse_time(self.created_at)).total_seconds() > after_s

    def as_json(self, *, stale_after: float | None = None,
                now: datetime | None = None) -> dict[str, Any]:
        """The row as a JSON object: every column, the JSON columns expanded, and `stale`
        when `stale_after` is given."""
        out = {f.name: getattr(self, f.name) for f in fields(self)
               if f.name != "newest_session_id"}
        out["values_json"] = self.values
        out["argv_json"] = self.argv
        out["env_json"] = self.env
        out["base_env_json"] = self.base_env
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
           resume_of: str | None = None, base_env: Mapping[str, str | None] | None = None,
           starting_s: float | None = None, at: str | None = None) -> Inserted:
    """Write a launch's row in state `spawning`, before anything is started.

    One transaction, with `synchronous=FULL` so the row is on disk before the spawn,
    writes the row, the `minted` session id of a Claude launch, and, when a handoff is
    given and `handoff_key` names the key it is filed under, its `given` pick.

    A request id already in the store writes nothing: the stored row comes back with
    `new` false, and the caller starts nothing, since that row's spawn may have run.

    A resume (`resume_of` set) keeps the session id it resumes: that id may already
    belong to the launch it resumes, and joins `session_ids` again for this launch with
    source `resume`, for a Codex thread as for Claude. Any other launch holding it is
    `SessionTaken`. The same transaction refuses the resume, `SessionLive`, while any
    launch holding the id is still starting (`spawning` with no process recorded, made
    less than `starting_s` ago: `stale_after_s()` when None) or its recorded process or
    claimed endpoint runs, so two resumes of one session at once launch it once.

    `env` holds the stamped literals only, never a secret; `base_env` the child's values
    for the recorded names (`compose.common.base_env`), recorded so a replay can start
    from them, with None for each credential that was set and never its value. Raises `TooLarge` for a value
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
    if resume_of is not None and session_id and starting_s is None:
        starting_s = stale_after_s()
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
        "base_env_json": None if base_env is None else _encode(dict(base_env)),
    }
    conn.execute("PRAGMA synchronous = FULL")
    try:
        with _writing(conn):
            if (stored := get(conn, request_id)) is not None:
                return Inserted(stored, False)
            if session_id:
                holders = [r[0] for r in conn.execute(
                    "SELECT request_id FROM session_ids WHERE session_id = ?",
                    (session_id,))]
                if holders and not (resume_of is not None and resume_of in holders):
                    raise SessionTaken(session_id, holders[0])
                if resume_of is not None:
                    for holder in holders:
                        held = get(conn, holder)
                        how = _running(held, parse_time(at), starting_s or 0.0)
                        if how is not None:
                            raise SessionLive(session_id, held, how)
            conn.execute(f"INSERT INTO launches ({', '.join(COLUMNS)}) "
                         f"VALUES ({', '.join('?' * len(COLUMNS))})",
                         [row[name] for name in COLUMNS])
            if session_id and (harness == "claude" or resume_of is not None):
                conn.execute("INSERT INTO session_ids (session_id, request_id, source, "
                             "seen_at) VALUES (?, ?, ?, ?)",
                             (session_id, request_id,
                              "resume" if resume_of is not None else "minted", at))
            if handoff_path and handoff_key:
                conn.execute("INSERT INTO picks (path, key, session_id, request_id, how, at) "
                             "VALUES (?, ?, ?, ?, 'given', ?)",
                             (handoff_path, handoff_key, session_id, request_id, at))
    finally:
        conn.execute("PRAGMA synchronous = NORMAL")
    return Inserted(LaunchRow(**row), True)


def _running(row: LaunchRow, at: datetime, starting_s: float) -> str | None:
    """`starting` when the launch `row` is still `spawning` with no process recorded,
    made less than `starting_s` before `at`; `live` when its recorded process, or the
    endpoint that claimed it, runs; else None."""
    from moxi import proc

    if row.pid:
        if row.pid_started is None:
            alive = proc.identity(row.pid) is not None
        else:
            alive = proc.alive(proc.ProcId(row.pid, row.pid_started))
        if alive:
            return "live"
    elif row.state == "spawning" and (
            at - parse_time(row.created_at)).total_seconds() < starting_s:
        return "starting"
    if row.endpoint and not endpoint_gone(row.endpoint):
        return "live"
    return None


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
    `session_id` when the row has none. An id another launch is known by stays that
    launch's: it joins nothing and the row's column is left as it is, so `resolve` and
    the claim read the same owner. The table is keyed by the pair, which would hold the id
    for both; this write keeps it with the launch that has it, except for a resume: a
    launch made to resume (`resume_of` set) learns a `resume` id whoever holds it, and
    `resolve` then finds it, the newest. A pair already there is left as it is. The state
    is not touched.
    """
    if source not in SOURCES:
        raise ValueError(f"unknown session id source {source!r}")
    at = at or now()
    with _writing(conn):
        row = get(conn, request_id)
        if row is None:
            return False
        shared = source == "resume" and row.resume_of is not None
        cursor = conn.execute(
            "INSERT OR IGNORE INTO session_ids (session_id, request_id, source, seen_at) "
            "SELECT ?, ?, ?, ? WHERE ? OR NOT EXISTS (SELECT 1 FROM session_ids "
            "WHERE session_id = ? AND request_id <> ?)",
            (session_id, request_id, source, at, shared, session_id, request_id))
        added = cursor.rowcount == 1
        conn.execute("UPDATE launches SET session_id = ?, updated_at = ? "
                     "WHERE request_id = ? AND session_id IS NULL AND EXISTS ("
                     "SELECT 1 FROM session_ids WHERE session_id = ? AND request_id = ?)",
                     (session_id, at, request_id, session_id, request_id))
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


def parse_endpoint(endpoint: str) -> tuple[int, float | None] | None:
    """The pid and start time a claim names, from its text `<pid>@<start time>`
    (`listeners.endpoint_id`); the start time is None for a claim named by its pid alone,
    its start time unread. None for text that is neither."""
    pid_text, sep, started_text = endpoint.partition("@")
    try:
        return int(pid_text), (float(started_text) if sep else None)
    except ValueError:
        return None


def endpoint_gone(endpoint: str) -> bool:
    """Whether the endpoint process a claim names has ended: no process with that pid and
    start time runs (start times within `moxi.proc.alive`'s tolerance), or, for a claim
    named by its pid alone, no process has that pid. A name `parse_endpoint` can't read is
    never gone."""
    from moxi import proc

    parsed = parse_endpoint(endpoint)
    if parsed is None:
        return False
    pid, started = parsed
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

    An id more than one launch holds (a resumed session's, held by the launch that started
    it and each that resumed it) belongs to the newest of them, skipping one that failed
    before any endpoint claimed it; when every holder did, the newest of those.
    """
    found = conn.execute(
        "SELECT l.request_id FROM session_ids s JOIN launches l USING (request_id) "
        "WHERE s.session_id = ? ORDER BY (l.state = 'failed' AND l.endpoint IS NULL), "
        "l.created_at DESC, l.rowid DESC LIMIT 1", (session_id,)).fetchone()
    if found is None:
        return None
    newest = conn.execute("SELECT session_id FROM session_ids WHERE request_id = ? "
                          "ORDER BY seen_at DESC, rowid DESC LIMIT 1", found).fetchone()
    cursor = conn.execute(f"{_SELECT} WHERE request_id = ?", found)
    return _row(cursor, cursor.fetchone(), newest_session_id=newest[0])


def kinds(session_ids: Iterable[str], *, timeout: float = 1.0,
          env: Mapping[str, str] | None = None) -> dict[str, str | None]:
    """Each session id's launch kind (`LaunchRow.kind`) by the launch `resolve` finds for
    it, or None when no launch holds the id.

    No launch holds the id of a session the launcher didn't start: one started by hand or
    by a script, and one started from inside a launched session, whose ids never join
    (only the endpoint that claimed a launch records its ids). A Codex launch is held by
    its id only once that id was learned, so a Codex launch without turns, or in `exec`
    mode, answers None. With no launch log every id answers None.

    Opens the launch log for this call alone and writes nothing to it, except to bring
    an older schema up to date. Raises `Busy` when the log is held past `timeout`
    seconds, `moxi.store.StoreTooNew` when a newer launcher wrote it, and any other error
    as it comes: the caller asks again later.
    """
    ids = list(dict.fromkeys(session_ids))
    if not ids or not path(env).exists():
        return dict.fromkeys(ids)
    try:
        conn = connect(env=env, create=False, timeout=timeout)
        try:
            return {sid: None if (row := resolve(conn, sid)) is None else row.kind
                    for sid in ids}
        finally:
            conn.close()
    except sqlite3.OperationalError as error:
        if _busy(error):
            raise Busy(f"the launch log is busy: {error}") from error
        raise


def seen_at(conn: sqlite3.Connection, request_id: str, session_id: str) -> str | None:
    """When the launch first became known by `session_id`, or None when it never was."""
    found = conn.execute("SELECT seen_at FROM session_ids WHERE request_id = ? AND "
                         "session_id = ?", (request_id, session_id)).fetchone()
    return None if found is None else found[0]


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


def running(conn: sqlite3.Connection) -> list[LaunchRow]:
    """Launches that may still run, newest first: every row not `failed` that records a
    process or an endpoint to look at. Whether that process runs is the caller's look."""
    cursor = conn.execute(f"{_SELECT} WHERE state != 'failed' AND (pid IS NOT NULL OR "
                          "endpoint IS NOT NULL) ORDER BY created_at DESC, rowid DESC")
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


# -- runs ------------------------------------------------------------------------------
#
# **One open run per launch.** A unique index over the runs not `ended` makes a second
# insert for the same launch fail, and `insert_run` reports the open run; nothing is locked
# across a wait.
#
# **A run's state goes forward only**, `armed`, `waiting`, `acting`, `ended`, by one
# conditional update naming the states it may leave (`RUN_LEAVES`). A job writes its state
# right before each act on the session; a write that matches nothing means the run was
# ended under it, and the job exits without acting.
#
# **A run is stale exactly when its job is gone.** The job is recorded on the run (a slots
# job by name, a spawned job by its process); the caller says how to test it. No clock
# decides it, except that a run is never stale within `RUN_GRACE_S` of its insert, and one
# with no job recorded after that is stale (its spawner died before recording it).


@dataclass(frozen=True)
class Run:
    """One `runs` row, its columns by name."""

    run_id: str
    kind: str
    mode: str
    request_id: str
    session_id: str
    actor: str
    handoff_path: str | None
    slot: str | None
    pane_id: str | None
    turn_number: int | None
    new_request_id: str | None
    repair_of: str | None
    crossing_key: str | None
    job: str | None
    state: str
    outcome: str | None
    word: str | None
    detail: str | None
    repeats: int
    created_at: str
    updated_at: str

    @property
    def open(self) -> bool:
        return self.state != "ended"

    def as_json(self) -> dict[str, Any]:
        return {f.name: getattr(self, f.name) for f in fields(self)}


@dataclass(frozen=True)
class RunEvent:
    id: int
    run_id: str
    at: str
    kind: str
    detail: dict[str, Any]


RUN_COLUMNS = tuple(f.name for f in fields(Run))
_SELECT_RUN = "SELECT " + ", ".join(RUN_COLUMNS) + " FROM runs"


def _run(values: Sequence[Any] | None) -> Run | None:
    return None if values is None else Run(*values)


def cut(text: str | None) -> str | None:
    """`text` cut from the front to `DETAIL_CAP` bytes, so its end, where an error's last
    words are, is kept; shorter text is kept whole."""
    if text is None:
        return None
    data = text.encode("utf-8")
    if len(data) <= DETAIL_CAP:
        return text
    return "..." + data[-(DETAIL_CAP - 3):].decode("utf-8", "ignore")


def new_run_id() -> str:
    """A run id: 16 hex digits from `secrets`."""
    return secrets.token_hex(8)


def get_run(conn: sqlite3.Connection, run_id: str) -> Run | None:
    """One run, or None."""
    return _run(conn.execute(f"{_SELECT_RUN} WHERE run_id = ?", (run_id,)).fetchone())


def open_run(conn: sqlite3.Connection, request_id: str) -> Run | None:
    """The launch's open run, or None."""
    return _run(conn.execute(f"{_SELECT_RUN} WHERE request_id = ? AND state <> 'ended'",
                             (request_id,)).fetchone())


def runs(conn: sqlite3.Connection, *, request_id: str | None = None,
         session_id: str | None = None, limit: int | None = None,
         before: str | datetime | None = None) -> list[Run]:
    """Runs newest first, of one launch or one session id when either is given, only
    those made before `before` when it is given."""
    sql, where, params = _SELECT_RUN, [], []
    if request_id is not None:
        where.append("request_id = ?")
        params.append(request_id)
    elif session_id is not None:
        where.append("session_id = ?")
        params.append(session_id)
    if before is not None:
        moment = before if isinstance(before, datetime) else parse_time(before)
        where.append("created_at < ?")
        params.append(stamp(moment))
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY created_at DESC, rowid DESC"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(int(limit))
    return [Run(*values) for values in conn.execute(sql, params)]


def made_by(conn: sqlite3.Connection, request_id: str) -> Run | None:
    """The run that made the launch `request_id` (a relaunch, or a repair of one), or
    None."""
    return _run(conn.execute(f"{_SELECT_RUN} WHERE new_request_id = ? "
                             "ORDER BY created_at DESC, rowid DESC LIMIT 1",
                             (request_id,)).fetchone())


def run_stale(run: Run, gone: Callable[[str], bool], now: datetime | None = None) -> bool:
    """Whether an open run is stale: past `RUN_GRACE_S` from its insert, with no job
    recorded or with `gone(job)` true. `gone` is given the run's `job` text (`slots:<name>`
    or `proc:<pid>@<start>`); an ended run is never stale."""
    if not run.open:
        return False
    now = now or datetime.now(timezone.utc)
    if (now - parse_time(run.created_at)).total_seconds() <= RUN_GRACE_S:
        return False
    return run.job is None or bool(gone(run.job))


def insert_run(conn: sqlite3.Connection, *, kind: str, mode: str, request_id: str,
               session_id: str, actor: str, state: str = "armed", job: str | None = None,
               handoff_path: str | None = None, slot: str | None = None,
               pane_id: str | None = None, turn_number: int | None = None,
               repair_of: str | None = None, crossing_key: str | None = None,
               gone: Callable[[str], bool] | None = None, at: str | None = None,
               run_id: str | None = None, pend: bool = False) -> Run:
    """Insert a run for a launch and return it, or raise `RunOpen` naming the launch's
    open run. With `pend`, for a compact a crossing makes, an open run met here leaves
    the compact pending on the launch (`pend_compact`, under `crossing_key`) in the same
    transaction before `RunOpen` is raised, so the open run's end always finds it.

    With `gone`, an open run met here is tested first (`run_stale`): a stale one is ended
    `error`, word `job-gone`, and this run is inserted in the same transaction. The test
    runs before the write lock is taken, and the end is conditional on the run being as
    it was read, so a job that wrote its run in between is alive and keeps it. `state`
    is where the run starts (`armed`, or `acting` for an act that starts at once).

    `run_id` is the run's id when the caller minted it (`new_run_id`) to name its job
    before the insert, so the run is written with its job; else one is minted here.
    """
    if kind not in RUN_KINDS:
        raise ValueError(f"unknown run kind {kind!r}")
    if state not in RUN_STATES or state == "ended":
        raise ValueError(f"a run does not start {state!r}")
    at = at or now()
    seen = open_run(conn, request_id)
    stale = seen is not None and gone is not None and run_stale(seen, gone, parse_time(at))
    met = None
    with _writing(conn):
        current = open_run(conn, request_id)
        if current is not None:
            if not (stale and current == seen and _end_stale(conn, current, at)):
                if not pend or crossing_key is None:
                    raise RunOpen(current)
                _pend(conn, request_id, crossing_key, at)
                met = current
        if met is None:
            row = _insert_run(conn, kind=kind, mode=mode, request_id=request_id,
                              session_id=session_id, actor=actor, state=state, job=job,
                              handoff_path=handoff_path, slot=slot, pane_id=pane_id,
                              turn_number=turn_number, repair_of=repair_of,
                              crossing_key=crossing_key, at=at, run_id=run_id)
    if met is not None:
        raise RunOpen(met)
    return row


def _insert_run(conn: sqlite3.Connection, *, kind: str, mode: str, request_id: str,
                session_id: str, actor: str, state: str, job: str | None,
                handoff_path: str | None, slot: str | None, pane_id: str | None,
                turn_number: int | None, repair_of: str | None,
                crossing_key: str | None, at: str, run_id: str | None) -> Run:
    if run_id is None:
        run_id = new_run_id()
        while conn.execute("SELECT 1 FROM runs WHERE run_id = ?",
                           (run_id,)).fetchone():
            run_id = new_run_id()
    row = Run(run_id=run_id, kind=kind, mode=mode, request_id=request_id,
              session_id=session_id, actor=actor, handoff_path=handoff_path, slot=slot,
              pane_id=pane_id, turn_number=turn_number, new_request_id=None,
              repair_of=repair_of, crossing_key=crossing_key, job=job, state=state,
              outcome=None, word=None, detail=None, repeats=0, created_at=at,
              updated_at=at)
    conn.execute(f"INSERT INTO runs ({', '.join(RUN_COLUMNS)}) "
                 f"VALUES ({', '.join('?' * len(RUN_COLUMNS))})",
                 [getattr(row, name) for name in RUN_COLUMNS])
    return row


def _end_stale(conn: sqlite3.Connection, run: Run, at: str) -> bool:
    outcome, word = JOB_GONE
    ended = conn.execute(
        "UPDATE runs SET state = 'ended', outcome = ?, word = ?, updated_at = ? "
        "WHERE run_id = ? AND state = ? AND updated_at = ?",
        (outcome, word, at, run.run_id, run.state, run.updated_at)).rowcount == 1
    if ended:
        conn.execute("INSERT INTO run_events (run_id, at, kind, detail_json) "
                     "VALUES (?, ?, 'ended', ?)",
                     (run.run_id, at, _encode({"outcome": outcome, "word": word,
                                               "job": run.job})))
    return ended


def advance_run(conn: sqlite3.Connection, run_id: str, state: str, *,
                leaving: Iterable[str] | None = None, at: str | None = None,
                **values: Any) -> bool:
    """Move a run to `state`, with `values` (any of `RUN_FIELDS`), and say whether it
    moved.

    The run moves only from a state `state` may be written from (`RUN_LEAVES`), narrowed
    to `leaving` when given; otherwise nothing is written, so a write that comes after a
    later one, or one for a run ended under its job, changes nothing. `detail` is cut to
    `DETAIL_CAP` from the front.
    """
    if state not in RUN_LEAVES:
        raise ValueError(f"no run is moved to {state!r}")
    allowed = RUN_LEAVES[state]
    if leaving is not None:
        leaving = tuple(leaving)
        if bad := [s for s in leaving if s not in allowed]:
            raise ValueError(f"a run's {state} is never written from {', '.join(bad)}")
        allowed = leaving
    if bad := [name for name in values if name not in RUN_FIELDS]:
        raise ValueError(f"not a field of a run: {', '.join(bad)}")
    if "detail" in values:
        values["detail"] = cut(values["detail"])
    names = list(values)
    sets = "".join(f", {name} = ?" for name in names)
    with _writing(conn):
        cursor = conn.execute(
            f"UPDATE runs SET state = ?, updated_at = ?{sets} "
            f"WHERE run_id = ? AND state IN ({', '.join('?' * len(allowed))})",
            (state, at or now(), *(values[n] for n in names), run_id, *allowed))
        return cursor.rowcount == 1


def end_run(conn: sqlite3.Connection, run_id: str, outcome: str, *, word: str | None = None,
            detail: str | None = None, at: str | None = None, **values: Any) -> bool:
    """End an open run with its `outcome`, and say whether this call ended it; a run
    already ended is left as it was.

    The end takes a compact a crossing left pending on the launch, in the same
    transaction (`_take_pending`), and once the end is written starts that compact's job.
    """
    ended, _kept, picked = _end(conn, run_id, outcome, word=word, detail=detail, at=at,
                                values=values)
    if picked is not None:
        _start_picked(conn, picked)
    return ended


def end_blocked(conn: sqlite3.Connection, run_id: str, word: str,
                detail: str | None = None, *, at: str | None = None) -> str | None:
    """End an open run `blocked` with its `word`, and return the id of the run that now
    carries the verdict, or None when the run was not open.

    **A repeated `blocked` updates one run**: when the launch's run before this one has
    the same kind, mode and word and ended `blocked`, that run's `repeats` and
    `updated_at` move on and its detail is the new one, and this run is deleted with its
    events, so a block that lasts is one run whose `created_at` is when it began.
    """
    ended, kept, picked = _end(conn, run_id, "blocked", word=word, detail=detail, at=at,
                               values={}, merge=True)
    if picked is not None:
        _start_picked(conn, picked)
    return kept if ended else None


def drop_run(conn: sqlite3.Connection, run_id: str, *, at: str | None = None) -> bool:
    """Delete an open run and its events, for an act that ended with nothing done and
    leaves no row; say whether it was dropped. It ends the run as far as a pending
    compact goes: one left on the launch is taken, as an end takes it."""
    at = at or now()
    with _writing(conn):
        run = get_run(conn, run_id)
        if run is None or not run.open:
            return False
        conn.execute("DELETE FROM run_events WHERE run_id = ?", (run_id,))
        if conn.execute("DELETE FROM runs WHERE run_id = ? AND state <> 'ended'",
                        (run_id,)).rowcount != 1:
            return False
        picked = _take_pending(conn, run, None, None, at)
    if picked is not None:
        _start_picked(conn, picked)
    return True


def set_run_job(conn: sqlite3.Connection, run_id: str, job: str) -> bool:
    """Record an open run's job (`proc:<pid>@<start>`), for a spawner that records it
    after its spawn; say whether the run was open."""
    with _writing(conn):
        return conn.execute("UPDATE runs SET job = ?, updated_at = ? "
                            "WHERE run_id = ? AND state <> 'ended'",
                            (job, now(), run_id)).rowcount == 1


def _end(conn: sqlite3.Connection, run_id: str, outcome: str, *, word: str | None,
         detail: str | None, at: str | None, values: Mapping[str, Any],
         merge: bool = False) -> tuple[bool, str, Run | None]:
    """The end's one transaction: (whether this call ended the run, the run that carries
    the verdict, the compact run picked up from the launch's pending compact)."""
    if bad := [name for name in values if name not in RUN_FIELDS]:
        raise ValueError(f"not a field of a run: {', '.join(bad)}")
    at = at or now()
    sets = {"outcome": outcome, "word": word, "detail": cut(detail), **values}
    names = list(sets)
    leaving = RUN_LEAVES["ended"]
    with _writing(conn):
        run = get_run(conn, run_id)
        if run is None:
            return False, run_id, None
        cursor = conn.execute(
            f"UPDATE runs SET state = 'ended', updated_at = ?"
            f"{''.join(f', {name} = ?' for name in names)} "
            f"WHERE run_id = ? AND state IN ({', '.join('?' * len(leaving))})",
            (at, *(sets[n] for n in names), run_id, *leaving))
        if cursor.rowcount != 1:
            return False, run_id, None
        kept = _merge_blocked(conn, run, word, cut(detail), at) if merge else run_id
        picked = _take_pending(conn, run, outcome, values.get("new_request_id"), at,
                               word=word)
    return True, kept, picked


def _merge_blocked(conn: sqlite3.Connection, run: Run, word: str | None,
                   detail: str | None, at: str) -> str:
    before = _run(conn.execute(
        f"{_SELECT_RUN} WHERE request_id = ? AND run_id <> ? "
        "ORDER BY created_at DESC, rowid DESC LIMIT 1",
        (run.request_id, run.run_id)).fetchone())
    if (before is None or before.open or before.outcome != "blocked"
            or (before.kind, before.mode, before.word) != (run.kind, run.mode, word)):
        return run.run_id
    conn.execute("UPDATE runs SET repeats = repeats + 1, updated_at = ?, detail = ? "
                 "WHERE run_id = ?", (at, detail, before.run_id))
    conn.execute("DELETE FROM run_events WHERE run_id = ?", (run.run_id,))
    conn.execute("DELETE FROM runs WHERE run_id = ?", (run.run_id,))
    return before.run_id


# -- pending compacts, launch chains and holds -------------------------------------------
#
# **A compact crossing is never dropped silently.** A crossing that finds a run open on
# its launch leaves a pending compact (one per launch, the newest crossing wins), and
# whatever ends a run on the launch takes it in the same transaction as the end. When the
# run left the session's context fresh (it relaunched, cleared, compacted or exited the
# session), or left no session to compact (the session is gone, or the run's exit ran, so
# it is down or at its exit dialog), the pending compact is dropped with an event;
# otherwise its compact run is inserted there and its job started once the end is
# written.

#: The outcomes after which a launch's session needs no compact a crossing left pending.
FRESH_CONTEXT = ("relaunched", "first-turn-not-sent", "cleared", "compacted", "exited")
#: The actor of the runs the launcher starts on its own reads.
LAUNCHER = "launcher"


def crossing_key(session_id: str, mark: Any, at: Any) -> str:
    """A crossing's key, `<session id>|<mark>|<at>`: one crossing writes once."""
    return f"{session_id}|{mark}|{at}"


def crossing_known(conn: sqlite3.Connection, key: str) -> bool:
    """Whether a crossing already wrote: a run or a pending compact carries its key."""
    return conn.execute(
        "SELECT 1 FROM runs WHERE crossing_key = ? "
        "UNION ALL SELECT 1 FROM pending_compacts WHERE crossing_key = ? LIMIT 1",
        (key, key)).fetchone() is not None


def pend_compact(conn: sqlite3.Connection, request_id: str, key: str, *,
                 at: str | None = None) -> None:
    """Leave a compact pending on a launch for the crossing `key`; the newest wins."""
    with _writing(conn):
        _pend(conn, request_id, key, at or now())


def _pend(conn: sqlite3.Connection, request_id: str, key: str, at: str) -> None:
    conn.execute(
        "INSERT INTO pending_compacts (request_id, crossing_key, at) VALUES (?, ?, ?) "
        "ON CONFLICT (request_id) DO UPDATE SET crossing_key = excluded.crossing_key, "
        "at = excluded.at", (request_id, key, at))


def pending_compact(conn: sqlite3.Connection, request_id: str) -> tuple[str, str] | None:
    """The compact pending on a launch, `(crossing key, at)`, or None."""
    found = conn.execute("SELECT crossing_key, at FROM pending_compacts WHERE request_id = ?",
                         (request_id,)).fetchone()
    return None if found is None else (found[0], found[1])


def _session_left(conn: sqlite3.Connection, run: Run, outcome: str | None,
                  word: str | None) -> bool:
    """Whether `run` left no session to compact: it ended on the session being gone, it
    was a repair (made only once the session is down), or its exit command ran, a refusal
    other than turns' `gone` aside, so the session is down or at its exit dialog."""
    if outcome == "gone" or word == "gone" or run.mode == "repair":
        return True
    for (detail,) in conn.execute("SELECT detail_json FROM run_events "
                                  "WHERE run_id = ? AND kind = 'exit-result'",
                                  (run.run_id,)):
        result = json.loads(detail) if detail else {}
        if result.get("kind") != "refused" or result.get("reason") == "gone":
            return True
    return False


def _take_pending(conn: sqlite3.Connection, run: Run, outcome: str | None,
                  new_request_id: str | None, at: str, *,
                  word: str | None = None) -> Run | None:
    found = pending_compact(conn, run.request_id)
    if found is None:
        return None
    key, pended = found
    conn.execute("DELETE FROM pending_compacts WHERE request_id = ?", (run.request_id,))
    session_id = key.split("|", 1)[0] or run.session_id
    if (new_request_id or outcome in FRESH_CONTEXT
            or _session_left(conn, run, outcome, word)):
        conn.execute("INSERT INTO events (request_id, session_id, at, kind, detail_json) "
                     "VALUES (?, ?, ?, 'pending-compact-dropped', ?)",
                     (run.request_id, session_id, at,
                      _encode({"crossing_key": key, "pended_at": pended,
                               "run_id": run.run_id, "outcome": outcome})))
        return None
    if conn.execute("SELECT 1 FROM runs WHERE crossing_key = ?", (key,)).fetchone():
        return None
    run_id = new_run_id()
    while conn.execute("SELECT 1 FROM runs WHERE run_id = ?", (run_id,)).fetchone():
        run_id = new_run_id()
    picked = Run(run_id=run_id, kind="compact", mode="limit", request_id=run.request_id,
                 session_id=session_id, actor=LAUNCHER, handoff_path=None, slot=run.slot,
                 pane_id=run.pane_id, turn_number=None, new_request_id=None, repair_of=None,
                 crossing_key=key, job=None, state="armed", outcome=None, word=None,
                 detail=None, repeats=0, created_at=at, updated_at=at)
    conn.execute(f"INSERT INTO runs ({', '.join(RUN_COLUMNS)}) "
                 f"VALUES ({', '.join('?' * len(RUN_COLUMNS))})",
                 [getattr(picked, name) for name in RUN_COLUMNS])
    conn.execute("INSERT INTO run_events (run_id, at, kind, detail_json) "
                 "VALUES (?, ?, 'picked', ?)",
                 (run_id, at, _encode({"crossing_key": key, "pended_at": pended,
                                       "after_run": run.run_id})))
    return picked


def _start_picked(conn: sqlite3.Connection, run: Run) -> None:
    from . import recycle

    recycle.start_compact(conn, run)


def chain(conn: sqlite3.Connection, request_id: str) -> list[str]:
    """A launch's chain: the launch itself and each launch it replaced, followed back
    through `relaunch_of` and `resume_of`."""
    return [rid for (rid,) in conn.execute(
        "WITH RECURSIVE chain(id) AS (SELECT ? UNION "
        "SELECT CASE n.k WHEN 0 THEN l.relaunch_of ELSE l.resume_of END "
        "FROM launches l JOIN chain c ON l.request_id = c.id "
        "CROSS JOIN (SELECT 0 AS k UNION ALL SELECT 1) n "
        "WHERE (CASE n.k WHEN 0 THEN l.relaunch_of ELSE l.resume_of END) IS NOT NULL) "
        "SELECT id FROM chain", (request_id,))]


def open_holds(conn: sqlite3.Connection, request_id: str) -> list[str]:
    """The names of the open holds of a launch's whole chain, sorted."""
    return sorted({hold.name for hold in holds(conn, request_id)})


@dataclass(frozen=True)
class Hold:
    """One `warm_holds` row: the launch that opened it, its name, when it was opened, and
    when it was released (None while it is open)."""

    request_id: str
    name: str
    opened_at: str
    released_at: str | None

    def as_json(self) -> dict[str, Any]:
        return {f.name: getattr(self, f.name) for f in fields(self)}


def holds(conn: sqlite3.Connection, request_id: str) -> list[Hold]:
    """The open holds of a launch's whole chain, by name, then oldest first."""
    ids = chain(conn, request_id)
    return [Hold(*values) for values in conn.execute(
        f"SELECT request_id, name, opened_at, released_at FROM warm_holds "
        f"WHERE released_at IS NULL AND request_id IN ({', '.join('?' * len(ids))}) "
        f"ORDER BY name, opened_at, rowid", ids)]


def open_hold(conn: sqlite3.Connection, request_id: str, name: str, *,
              at: str | None = None) -> tuple[Hold, bool]:
    """Open the hold `name` on a launch: `(the open hold, whether this call opened it)`.
    A name already open anywhere in the launch's chain is left as it is, and that hold
    is returned."""
    with _writing(conn):
        found = next((hold for hold in holds(conn, request_id) if hold.name == name), None)
        if found is not None:
            return found, False
        opened = Hold(request_id, name, at or now(), None)
        conn.execute("INSERT INTO warm_holds (request_id, name, opened_at) VALUES (?, ?, ?)",
                     (request_id, name, opened.opened_at))
    return opened, True


def release_hold(conn: sqlite3.Connection, request_id: str, name: str, *,
                 at: str | None = None) -> list[Hold]:
    """Release the hold `name` wherever it is open in a launch's chain, and return the
    holds released (none when it was not open)."""
    at = at or now()
    with _writing(conn):
        found = [hold for hold in holds(conn, request_id) if hold.name == name]
        for hold in found:
            conn.execute("UPDATE warm_holds SET released_at = ? WHERE request_id = ? "
                         "AND name = ? AND released_at IS NULL",
                         (at, hold.request_id, hold.name))
    return [Hold(h.request_id, h.name, h.opened_at, at) for h in found]


def holds_log(conn: sqlite3.Connection, *, limit: int | None = None,
              before: str | datetime | None = None) -> list[Hold]:
    """Every hold, open or released, newest opened first, at most `limit`, only those
    opened before `before` when it is given."""
    sql, params = "SELECT request_id, name, opened_at, released_at FROM warm_holds", []
    if before is not None:
        moment = before if isinstance(before, datetime) else parse_time(before)
        sql += " WHERE opened_at < ?"
        params.append(stamp(moment))
    sql += " ORDER BY opened_at DESC, rowid DESC"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(int(limit))
    return [Hold(*values) for values in conn.execute(sql, params)]


def add_run_event(conn: sqlite3.Connection, run_id: str, kind: str,
                  detail: Mapping[str, Any] | None = None, *, at: str | None = None) -> int:
    """Add a `run_events` row and return its id."""
    with _writing(conn):
        cursor = conn.execute(
            "INSERT INTO run_events (run_id, at, kind, detail_json) VALUES (?, ?, ?, ?)",
            (run_id, at or now(), kind, _encode(dict(detail or {}))))
        return int(cursor.lastrowid)


def run_events(conn: sqlite3.Connection, run_id: str) -> list[RunEvent]:
    """The events of one run, oldest first."""
    return [RunEvent(id, rid, at, kind, json.loads(detail)) for id, rid, at, kind, detail
            in conn.execute("SELECT id, run_id, at, kind, detail_json FROM run_events "
                            "WHERE run_id = ? ORDER BY at, id", (run_id,))]


# -- the exit log ------------------------------------------------------------------------

#: The verdicts an exit-session writes: the session exited; it was held (a word says by
#: what); it is at Claude's exit dialog for a person to answer; it did not exit; or the
#: exit was refused before it was tried.
VERDICTS = ("exited", "held", "at-dialog", "not-exited", "refused")


@dataclass(frozen=True)
class ExitRow:
    """One `exits` row. `run_id` is None for an exit refused before its run was written."""

    id: int
    at: str
    run_id: str | None
    request_id: str
    session_id: str
    verdict: str
    word: str | None
    detail: str | None
    slot: str | None
    pane_id: str | None

    def as_json(self) -> dict[str, Any]:
        return {f.name: getattr(self, f.name) for f in fields(self)}


EXIT_COLUMNS = tuple(f.name for f in fields(ExitRow))


def add_exit(conn: sqlite3.Connection, *, request_id: str, session_id: str, verdict: str,
             run_id: str | None = None, word: str | None = None, detail: str | None = None,
             slot: str | None = None, pane_id: str | None = None,
             at: str | None = None) -> ExitRow:
    """Write one `exits` row and return it. `detail` is cut to `DETAIL_CAP` from the
    front."""
    if verdict not in VERDICTS:
        raise ValueError(f"not an exit's verdict: {verdict!r}")
    at, detail = at or now(), cut(detail)
    with _writing(conn):
        cursor = conn.execute(
            "INSERT INTO exits (at, run_id, request_id, session_id, verdict, word, detail, "
            "slot, pane_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (at, run_id, request_id, session_id, verdict, word, detail, slot, pane_id))
        return ExitRow(int(cursor.lastrowid), at, run_id, request_id, session_id, verdict,
                       word, detail, slot, pane_id)


def exits_log(conn: sqlite3.Connection, *, limit: int | None = None,
              before: str | datetime | None = None) -> list[ExitRow]:
    """Exit rows newest first, at most `limit`, only those written before `before` when
    it is given."""
    sql, params = f"SELECT {', '.join(EXIT_COLUMNS)} FROM exits", []
    if before is not None:
        moment = before if isinstance(before, datetime) else parse_time(before)
        sql += " WHERE at < ?"
        params.append(stamp(moment))
    sql += " ORDER BY at DESC, id DESC"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(int(limit))
    return [ExitRow(*values) for values in conn.execute(sql, params)]


def prune(conn: sqlite3.Connection, keep_days: float, *, at: str | None = None,
          limit: int = PRUNE_LIMIT) -> dict[str, int]:
    """Delete what is older than `keep_days` and say how many rows of each table went:
    ended runs, with their events and `exits` rows, `exits` rows of an exit refused
    before it had a run, and released holds. At most `limit` rows of each table go in
    one call; a run whose events or exits are not all gone yet is kept for the next.
    Open runs and open holds are never deleted by age."""
    moment = parse_time(at) if at else datetime.now(timezone.utc)
    cutoff = stamp(moment - timedelta(days=keep_days))
    with _writing(conn):
        old = [run_id for (run_id,) in conn.execute(
            "SELECT run_id FROM runs WHERE state = 'ended' AND updated_at < ? "
            "ORDER BY updated_at LIMIT ?", (cutoff, limit))]
        marks = ", ".join("?" * len(old))
        gone = {"run_events": 0, "exits": 0, "runs": 0, "warm_holds": 0}
        if old:
            gone["run_events"] = conn.execute(
                f"DELETE FROM run_events WHERE id IN (SELECT id FROM run_events "
                f"WHERE run_id IN ({marks}) LIMIT ?)", (*old, limit)).rowcount
            gone["exits"] = conn.execute(
                f"DELETE FROM exits WHERE id IN (SELECT id FROM exits "
                f"WHERE run_id IN ({marks}) LIMIT ?)", (*old, limit)).rowcount
            gone["runs"] = conn.execute(
                f"DELETE FROM runs WHERE run_id IN ({marks}) "
                "AND NOT EXISTS (SELECT 1 FROM run_events e WHERE e.run_id = runs.run_id) "
                "AND NOT EXISTS (SELECT 1 FROM exits x WHERE x.run_id = runs.run_id)",
                old).rowcount
        gone["exits"] += conn.execute(
            "DELETE FROM exits WHERE id IN (SELECT id FROM exits WHERE run_id IS NULL "
            "AND at < ? LIMIT ?)", (cutoff, max(limit - gone["exits"], 0))).rowcount
        gone["warm_holds"] = conn.execute(
            "DELETE FROM warm_holds WHERE rowid IN (SELECT rowid FROM warm_holds "
            "WHERE released_at IS NOT NULL AND released_at < ? LIMIT ?)",
            (cutoff, limit)).rowcount
    return gone


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


#: The tables `log --table` reads besides the launches.
TABLES = ("runs", "exits", "holds")


def table(conn: sqlite3.Connection, name: str, *, limit: int | None = None,
          before: str | datetime | None = None) -> list[Run | ExitRow | Hold]:
    """The rows of one of `TABLES`, newest first, at most `limit`, only those made before
    `before` when it is given."""
    if name == "runs":
        return runs(conn, limit=limit, before=before)
    if name == "exits":
        return exits_log(conn, limit=limit, before=before)
    if name == "holds":
        return holds_log(conn, limit=limit, before=before)
    raise ValueError(f"not a table log reads: {name!r}")


def table_lines(name: str, *, limit: int | None = None, before: str | datetime | None = None,
                db: str | Path | None = None,
                env: Mapping[str, str] | None = None) -> list[str]:
    """What `moxi launcher log --table NAME --json` prints: one JSON line per row, newest
    first; a run carries its events, oldest first. A store not made yet has no rows;
    nothing is created to say so."""
    try:
        conn = connect(db, env=env, create=False)
    except store.StoreError as error:
        if isinstance(error, store.StoreTooNew):
            raise
        return []
    try:
        lines = []
        for row in table(conn, name, limit=limit, before=before):
            data = row.as_json()
            if isinstance(row, Run):
                data["events"] = [{"at": e.at, "kind": e.kind, "detail": e.detail}
                                  for e in run_events(conn, row.run_id)]
            lines.append(_encode(data))
        return lines
    finally:
        conn.close()


__all__ = ["CAP", "COLUMNS", "DETAIL_CAP", "EXIT_COLUMNS", "FACTS", "FRESH_CONTEXT", "HARNESSES",
           "HOWS", "JOB_GONE", "KINDS", "LAUNCHER", "LAUNCH_REFUSED", "LEAVES", "PRUNE_LIMIT",
           "RECLAIMED",
           "RUN_COLUMNS",
           "RUN_FIELDS", "RUN_GRACE_S", "RUN_KINDS", "RUN_LEAVES", "RUN_STATES", "SOURCES",
           "STATES", "STEPS", "TABLES", "TARGETS", "VERDICTS", "Busy", "Event", "ExitRow",
           "Inserted", "Hold", "LaunchRow", "Run", "RunEvent", "RunOpen", "SessionLive",
           "SessionTaken", "TooLarge", "add_event", "add_exit", "add_pick", "add_run_event",
           "advance_run", "chain",
           "claim", "connect", "crossing_key", "crossing_known", "cut", "drop_run", "end_blocked",
           "end_run", "endpoint_gone", "events", "exits_log", "get", "get_run", "holds", "insert",
           "holds_log", "insert_run", "kinds", "learn_session", "log", "log_lines", "made_by",
           "new_run_id",
           "now", "open_hold", "open_holds", "open_run", "parse_endpoint", "parse_time", "path",
           "pend_compact", "pending_compact", "picked", "prune", "release_hold", "resolve",
           "running", "run_events", "run_stale", "runs", "seen_at", "session_ids", "set_facts",
           "set_run_job", "set_state", "stale_after_s", "stamp", "table", "table_lines"]

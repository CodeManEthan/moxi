"""The pass: read what changed in every harness's records into the token index.

`pass_(...)` runs one pass under `STATE/usage/index.lock`:

1. **The lock.** One pass writes at a time. A caller waits up to `wait` seconds
   (`index.wait_s` by default) for another pass to end, then `LockTimeout` (exit 75)
   names the holder. `wait=0` tries once.
2. **The listings.** Each lane lists its sources through sessions' `sources(...)`:
   `claude` (live transcripts, subagent and workflow agent files, rank 0), `codex`
   (rollouts, rank 1), `opencode` (the database, one row per session, rank 2) and one
   `archive:<id>` lane per `claude.archive_roots` row (ranks 3 on, in config order).
   **A pass never deletes rows on a listing that came back empty or unreadable.** A lane
   whose listing failed, or listed nothing while the index holds rows for it, keeps them
   all, each `held` with the reason. Within a listing, a row is held the same way when
   its path lies under a directory the listing could not read, when its source could not
   be read, or when it left the listing while an unreadable source of the same session
   appeared (a rollout compressed after a week idle comes back under a new path). A held
   row's requests stay counted, and its `held` clears the next time its source is listed
   and readable, whether it is read again or reused unchanged.
3. **Reuse.** A file whose reuse keys match its row is not read: live files on `(dev, ino,
   size, mtime_ns)`, archive files on `(size, mtime_ns)`, since `dev` and `ino` don't
   survive a remount or a copy. A reused row still takes its lane's rank now and, for a
   Codex rollout, its import marks now, so a mark that flips later moves the claim for
   its keys. The OpenCode database has no reuse key: the pass always reads it from its
   stored cursor, which costs little when nothing changed.
4. **The read.** Otherwise `records_since(source, cursor)` until the batch is complete, in
   one `moxi.store.transaction` per source: on `reset` the source's requests go first,
   each request is upserted on `(file_id, key)` so one that comes again replaces its
   earlier copy, the header facts, title and first and last times are updated, the claim
   step runs for the keys the transaction touched, and the new cursor is stored. A crash
   leaves the old cursor, the old rows and their claims together. **A record that comes
   twice leaves the index as one copy would.** Limits records go to the readings store,
   which ignores a repeat.
5. **Gone sources.** A source gone from a listing that held nothing loses its row and its
   requests, and its keys are claimed again in the same transaction. A lane whose archive
   id left the config is dropped.
6. **Live precedence.** An archive file whose session has a live main transcript is not
   read, and its row goes. The live set is taken after the live lane ran, so the pass
   that sees a live transcript gone reads its archive copy.
7. **Project keys.** With the projects module installed, a row with no `project` gets
   one: first from another row of the same session and directory that recorded one (the
   session's start), else, only while its `cwd` exists, from `for_path(cwd)`, one lookup
   per distinct `cwd` per pass. A key once stored is never derived again. A `cwd` that no
   longer exists is never looked up and never guessed at; the row stays without a key and
   reports group it by its `cwd`.

**Marks follow requests.** The pass keeps each source's compaction markers (`marks`)
beside its requests: every path that writes, replaces, deletes or holds a source's
requests does the same to its marks, in the same transaction. A row's `marks_read` is 1
exactly when the index holds every marker of the reads that gave its requests; a source
whose row is at 0 is read from no cursor, and that read sets it to 1. A file's marker sits
at its line, as a request does. An OpenCode marker sits at the `seq` of the last request
of its own message, else at the message's time in milliseconds: a message's records come
whole in every read, so a read from the cursor places a marker where a whole read does.

**Rebuild.** `rebuild=True` reads every listed source again from no cursor, keeps held
rows as they are and every row's `project`, and ends with one claim over every key. A
rebuild, or a pass with more than `POOL_ABOVE` sources to read from no cursor, reads them
in `index.workers` processes; the workers return rows and this process alone writes. The
pass whose schema step moves the index from version 1 runs as a rebuild, since every row
then still lacks its markers and windows.

**Cancellation.** `cancel()` is asked between sources; a cancelled pass keeps what it
wrote and deletes nothing it did not reach.

projects is reached only here, inside the pass, through `moxi.modules.available` and
`load`; nothing a hook imports reaches this module.
"""

from __future__ import annotations

import dataclasses
import json
import os
import time
from collections.abc import Callable, Iterable, Mapping
from contextlib import ExitStack, nullcontext
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple

from . import store

if TYPE_CHECKING:
    import sqlite3

CLAUDE = "claude"
CODEX = "codex"
OPENCODE = "opencode"
ARCHIVE_PREFIX = "archive:"

#: The rank of each live lane; archive lanes take `ARCHIVE_RANK` on, in config order.
RANKS = {CLAUDE: 0, CODEX: 1, OPENCODE: 2}
ARCHIVE_RANK = 3

#: The record kinds a read asks sessions for.
KINDS = frozenset({"request", "limits", "marker"})

#: A pass with more sources than this to read from no cursor reads them in worker
#: processes, as a rebuild does.
POOL_ABOVE = 64
#: The most workers `index.workers = 0` picks.
MAX_WORKERS = 8

#: How often a waiting pass tries the lock again, in seconds.
POLL = 0.05

#: `files.excluded` for a Codex rollout an import mark says was imported.
IMPORTED = "imported"

#: `meta` name for what the last pass learnt about each lane, as JSON (`store.META_LANES`).
#: Each lane's entry: `harness`, `rank`, `root`, `state` (`online`, `offline`, `held`
#: or `absent`), `reason`, `listed`, `held`, `compressed`, `unreadable` (up to
#: `UNREADABLE_SHOWN` `[path, why]` pairs), `checked_at`; Codex adds `imports_path`,
#: `imports_reason` (None, "imports file not found" or "imports file unreadable: ..."),
#: `imported` and `originator_only` (rollouts whose only import mark is the desktop app's
#: `originator`, counted).
UNREADABLE_SHOWN = 20

#: A lane's state: listed; not listed, its rows kept; listed empty, its rows kept; or
#: not listed and holding nothing (a harness this machine doesn't have).
ONLINE = "online"
OFFLINE = "offline"
HELD = "held"
ABSENT = "absent"


# --- lanes ------------------------------------------------------------------------------

@dataclass(frozen=True)
class Lane:
    """One lane: its id, the harness that wrote it, its rank in the claim's order, and the
    root its listing reads (a projects root, a sessions root or the database)."""

    id: str
    harness: str
    rank: int
    root: str

    @property
    def archive(self) -> bool:
        return self.id.startswith(ARCHIVE_PREFIX)


def configured_lanes(*, env: Mapping[str, str] | None = None,
                     settings: Any = None) -> list[Lane]:
    """Every lane, in the order a pass runs them: claude, codex, opencode, then the
    archive roots in config order. Raises `moxi.config.ConfigError` on a config usage
    refuses."""
    from moxi_sessions import api as sessions

    from . import settings as usage_settings

    current = settings if settings is not None else usage_settings.current(env)
    out = [Lane(name, name, rank, str(sessions.root(name, env)))
           for name, rank in RANKS.items()]
    for n, root in enumerate(usage_settings.archive_roots(current)):
        out.append(Lane(root.lane, CLAUDE, ARCHIVE_RANK + n,
                        str(Path(root.path) / "projects")))
    return out


# --- the result -------------------------------------------------------------------------

@dataclass
class PassResult:
    """What one pass did. `lanes` is what it learnt about each lane it ran (the shape
    stored in `meta` under `lanes`)."""

    rebuild: bool = False
    listed: int = 0
    read: int = 0
    reused: int = 0
    excluded: int = 0
    deleted: int = 0
    held: int = 0
    dropped_lanes: list[str] = field(default_factory=list)
    unreadable: list[str] = field(default_factory=list)
    projects_recorded: int = 0
    readings_added: int = 0
    readings_pruned: int = 0
    readings_error: str | None = None
    cancelled: bool = False
    migrated: tuple[int, int] | None = None
    migration_dropped: tuple[str, ...] = ()
    workers: int = 0
    lanes: dict[str, dict[str, Any]] = field(default_factory=dict)
    started_at: float = 0.0
    finished_at: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        out = dataclasses.asdict(self)
        out["unreadable"] = self.unreadable[:UNREADABLE_SHOWN]
        out["unreadable_count"] = len(self.unreadable)
        out["migration_dropped"] = list(self.migration_dropped)
        if self.migrated is not None:
            out["migrated"] = list(self.migrated)
        return out


# --- reading one source -----------------------------------------------------------------

class Task(NamedTuple):
    """One file source to read: the listing entry (without its header, so it travels to
    a worker cheaply), the cursor to start from (None reads from the start), and where its
    rows go."""

    lane: str
    path: str
    entry: Any
    cursor: str | None


@dataclass
class Got:
    """What reading one source gave: the request rows by key (a request that came twice
    is here once, as its last copy), the compaction marks by `(seq, kind)`, the limits
    records, the last title, the header facts, the new cursor, whether sessions said to
    drop what the index holds, and the reason it could not be read."""

    rows: dict[int, store.RequestRow] = field(default_factory=dict)
    marks: dict[tuple[int, str], store.MarkRow] = field(default_factory=dict)
    limits: list[Any] = field(default_factory=list)
    title: str | None = None
    facts: dict[str, Any] = field(default_factory=dict)
    cursor: str | None = None
    reset: bool = False
    error: str | None = None


def read_source(task: Task) -> Got:
    """Read one file source from `task.cursor` until sessions says the batch is complete.
    Never writes; runs in a worker process as well as in the pass's own. An `Unreadable`
    source comes back with `error` set."""
    from moxi_sessions import api as sessions

    got = Got()
    cursor = task.cursor
    try:
        while True:
            batch = sessions.records_since(task.entry, cursor, kinds=KINDS)
            if batch.reset:
                got.reset = True
                got.rows.clear()
                got.marks.clear()
                got.limits.clear()
                got.title = None
            _take(got, batch.records, lane=task.lane, path=task.path)
            header = batch.header
            if header is not None:
                got.facts = header_facts(header)
            cursor = batch.cursor
            if batch.complete:
                break
    except sessions.Unreadable as exc:
        return Got(error=str(exc) or type(exc).__name__)
    got.cursor = cursor
    return got


def _take(got: Got, records: Iterable[Any], *, lane: str, path: str) -> None:
    for record in records:
        kind = record.kind
        if kind == "request":
            row = store.request_row(record, lane=lane, path=path)
            got.rows.pop(row.key, None)          # the later copy keeps the later place
            got.rows[row.key] = row
        elif kind == "limits":
            if record.harness == CODEX:
                got.limits.append(record)
        elif kind == "marker" and record.marker == "title":
            title = record.fields.get("title")
            if isinstance(title, str) and title:
                got.title = title
        elif kind == "marker" and record.marker == store.COMPACTION:
            line = store.marker_line(record)
            if line is not None:
                mark = store.mark_row(record, seq=line)
                got.marks[(mark.seq, mark.kind)] = mark


def header_facts(header: Any) -> dict[str, Any]:
    """The `files` columns a sessions `Header` fills: `cwd`, `origin` (Claude's
    `entrypoint`, Codex's `codex_source`, as written), `originator` (Codex line 1's, as
    written) and `parent_session`. A fact the source did not write is left out, so it
    never overwrites one an earlier read stored."""
    facts: dict[str, Any] = {}
    cwd = _text(header.cwd)
    if cwd is not None:
        facts["cwd"] = cwd
    origin = header.entrypoint if header.harness == CLAUDE else header.codex_source
    origin = _written(origin)
    if origin is not None:
        facts["origin"] = origin
    if header.harness == CODEX and header.meta is not None:
        originator = _written(header.meta.get("originator"))
        if originator is not None:
            facts["originator"] = originator
    parent = _text(header.parent)
    if parent is not None:
        facts["parent_session"] = parent
    return facts


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _written(value: Any) -> str | None:
    """A header value as written: text as it is, anything else known as JSON."""
    from moxi_sessions.api import UNKNOWN

    if isinstance(value, str):
        return value or None
    if value is None or value is UNKNOWN:
        return None
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError):
        return None


def _imported(header: Any) -> bool:
    """A rollout leaves the totals only on a mark that means imported: its first turn id
    is an imported one, or the imports file lists its thread. The desktop app's
    `originator` alone does not count."""
    marks = getattr(header, "import_marks", None)
    if marks is None:
        return False
    return marks.turn_id is True or marks.listed is True


def _originator_only(header: Any) -> bool:
    marks = getattr(header, "import_marks", None)
    return (marks is not None and marks.originator is True
            and marks.turn_id is not True and marks.listed is not True)


def _kind(entry: Any) -> str:
    if entry.kind == "subagent":
        return "workflow" if entry.workflow_id else "subagent"
    if entry.kind == "session":
        return "subagent" if entry.source.agent_id is not None else "main"
    return "main"


# --- the pass ---------------------------------------------------------------------------

#: The default for `projects=`: look the module up when the pass needs it.
LOOKUP = object()


class _Pass:
    """One pass's state: the connection, the clock's `now`, what it touched."""

    def __init__(self, conn: sqlite3.Connection, *, now: float, rebuild: bool,
                 readings: Any, env: Mapping[str, str] | None,
                 cancel: Callable[[], bool] | None, result: PassResult):
        self.conn = conn
        self.now = now
        self.rebuild = rebuild
        self.readings = readings
        self.env = env
        self.cancel = cancel
        self.result = result
        self.tasks: list[tuple[Lane, Any, dict | None, str | None]] = []
        #: The sessions with a live main transcript, worked out after the live lane ran
        #: and before the first archive lane.
        self.live_mains: set[str] | None = None

    def cancelled(self) -> bool:
        if self.result.cancelled:
            return True
        if self.cancel is not None and self.cancel():
            self.result.cancelled = True
        return self.result.cancelled


def pass_(*, lanes: Iterable[str] | None = None, rebuild: bool = False,
          force: bool = False, wait: float | None = None,
          env: Mapping[str, str] | None = None, now: float | None = None,
          projects: Any = LOOKUP, readings: Any = None, workers: int | None = None,
          cancel: Callable[[], bool] | None = None,
          clock: Callable[[], float] = time.monotonic,
          sleep: Callable[[float], None] = time.sleep) -> PassResult:
    """Run one pass over the index (see the module docstring) and return what it did.

    * `lanes`: the lane ids to run (`claude`, `codex`, `opencode`, `archive:<id>`); None
      runs every lane and also drops lanes whose archive id left the config.
    * `rebuild`: read every listed source again from no cursor. `force`: let a schema
      step drop the rows of offline archive lanes (`store.MigrationRefused` otherwise).
    * `wait`: seconds to wait for another pass's lock, `index.wait_s` when None;
      past it `moxi.files.LockTimeout` (exit 75). `clock` and `sleep` time the wait.
    * `projects`: the projects API (anything with `for_path`), None for none; by default
      the installed module when there is one.
    * `readings`: where limits records go (anything with `add_codex(records, *, now,
      env)` and `prune(*, now, env)`); by default `moxi_usage.readings`.
    * `workers`: processes for a rebuild's reads; `index.workers` when None, 0 for one
      per core up to `MAX_WORKERS`.
    * `now`: the pass's time in epoch seconds, `time.time()` when None.

    Raises `store.MigrationRefused` or `moxi.store.StoreTooNew` (exit 3) when the schema
    can't move, and `moxi.config.ConfigError` on a config usage refuses.
    """
    from moxi_sessions import api as sessions

    from . import settings as usage_settings

    current = usage_settings.current(env)
    every = configured_lanes(env=env, settings=current)
    wanted = None if lanes is None else set(lanes)
    if wanted is not None:
        unknown = wanted - {lane.id for lane in every}
        if unknown:
            raise ValueError(f"no such lane: {', '.join(sorted(unknown))}")
    chosen = [lane for lane in every if wanted is None or lane.id in wanted]
    if wait is None:
        wait = float(current["index.wait_s"])
    if workers is None:
        workers = int(current["index.workers"])
    if readings is None:
        import importlib

        readings = importlib.import_module(f"{__package__}.readings")

    with ExitStack() as stack:
        _take_lock(stack, store.lock_path(env), wait, clock, sleep)
        started = time.time() if now is None else now
        result = PassResult(rebuild=rebuild, started_at=started)
        listings = {lane.id: _list(sessions, lane) for lane in chosen}
        offline = [lane.id for lane in every if lane.archive and (
            not listings[lane.id].ok if lane.id in listings else not _listable(lane.root))]
        conn = store.connect(env=env)
        stack.callback(conn.close)
        migration = store.migrate(conn, offline=offline, force=force)
        if migration.after != migration.before:
            result.migrated = (migration.before, migration.after)
        if migration.before == 1 and migration.after >= 2:
            # every row lacks its markers and windows: read them all, the rebuild's way
            rebuild = result.rebuild = True
        result.migration_dropped = migration.dropped
        run = _Pass(conn, now=started, rebuild=rebuild, readings=readings, env=env,
                    cancel=cancel, result=result)
        if wanted is None:
            _drop_unconfigured(run, {lane.id for lane in every})
        for lane in chosen:
            if run.cancelled():
                break
            if lane.archive and run.live_mains is None:
                run.live_mains = _live_mains(run, listings.get(CLAUDE))
            if lane.harness == OPENCODE:
                _opencode_lane(run, lane, listings[lane.id], sessions)
            else:
                _file_lane(run, lane, listings[lane.id])
        if not run.cancelled():
            _read_all(run, workers)
        if projects is LOOKUP:
            projects = _projects_api()
        if projects is not None and not run.cancelled():
            result.projects_recorded = record_projects(conn, projects)
        try:
            result.readings_pruned = int(readings.prune(now=started, env=env))
        except Exception as exc:                         # the index doesn't wait on it
            result.readings_error = result.readings_error or f"prune: {exc}"
        result.finished_at = time.time() if now is None else now
        _store_meta(run, chosen)
        return result


def _take_lock(stack: ExitStack, path: Path, wait: float,
               clock: Callable[[], float], sleep: Callable[[float], None]) -> None:
    """Hold the pass's lock for the rest of `stack`, waiting up to `wait` seconds by
    `clock`. Raises `moxi.files.LockTimeout` (exit 75) when the wait runs out."""
    import moxi.files

    deadline = clock() + max(wait, 0.0)
    while True:
        held = moxi.files.lock(path, timeout_s=0)
        try:
            held.__enter__()
        except moxi.files.LockTimeout:
            if clock() >= deadline:
                raise
            sleep(POLL)
            continue
        stack.push(held.__exit__)
        return


def _list(sessions: Any, lane: Lane) -> Any:
    """The lane's listing. A Codex listing reads each rollout's head, for its import
    marks; a Claude listing takes one stat per file."""
    return sessions.sources(lane.harness, root=lane.root, heads=lane.harness == CODEX)


def _listable(root: str) -> bool:
    try:
        with os.scandir(root):
            return True
    except OSError:
        return False


def _projects_api() -> Any:
    """projects' API when the module is installed and offers interface 1, else None."""
    import moxi.modules

    if not moxi.modules.available("projects"):
        return None
    found = moxi.modules.load("projects", interface=1)
    return found.api if found.ok else None


def _drop_unconfigured(run: _Pass, configured: set[str]) -> None:
    """Step 5's last case: the rows of an archive lane whose id left the config go."""
    import moxi.store

    gone, lanes_gone = [], set()
    for file_id, lane in run.conn.execute("SELECT id, lane FROM files WHERE lane LIKE ?",
                                          (ARCHIVE_PREFIX + "%",)):
        if lane not in configured:
            gone.append(file_id)
            lanes_gone.add(lane)
    if not gone:
        return

    touched: set[int] = set()
    with moxi.store.transaction(run.conn):
        run.result.deleted += store.delete_files(run.conn, gone, touched=touched)
        store.claim(run.conn, touched)
    run.result.dropped_lanes.extend(sorted(lanes_gone))


def _live_mains(run: _Pass, listing: Any) -> set[str]:
    """The sessions with a live main transcript: listed now, or kept in the index. It is
    asked after the live lane ran, so a live transcript this pass saw gone has lost its
    row and its archive copy is read in the same pass; a row the lane held (its listing
    failed or came back empty, or the file sits where it could not be read) still
    counts."""
    mains = {row[0] for row in run.conn.execute(
        "SELECT DISTINCT session_id FROM files WHERE lane = ? AND kind = 'main' "
        "AND session_id IS NOT NULL", (CLAUDE,))}
    if listing is not None and listing.ok:
        mains.update(e.source.session_id for e in listing if e.kind == "main")
    return mains


def _lane_state(lane: Lane, listing: Any, now: float) -> dict[str, Any]:
    state: dict[str, Any] = {
        "harness": lane.harness, "rank": lane.rank, "root": lane.root,
        "state": ONLINE if listing.ok else OFFLINE,
        "reason": None if listing.ok else listing.reason, "listed": len(listing),
        "held": 0, "compressed": 0, "checked_at": now,
        "unreadable": [list(pair) for pair in listing.unreadable[:UNREADABLE_SHOWN]],
    }
    if lane.harness == CODEX:
        imports = listing.imports
        state["imports_path"] = getattr(imports, "path", None)
        state["imports_reason"] = getattr(imports, "reason", None)
        state["imported"] = 0
        state["originator_only"] = 0
    return state


def _hold_all(run: _Pass, lane: Lane, rows: Iterable[dict], reason: str,
              state: dict[str, Any]) -> None:
    import moxi.store

    rows = [row for row in rows if row["held"] != reason]
    state["held"] += _count_rows(run.conn, lane.id)
    if not state["held"] and state["state"] == OFFLINE and not lane.archive:
        state["state"] = ABSENT
    run.result.held += state["held"]
    if not rows:
        return
    touched: set[int] = set()
    with moxi.store.transaction(run.conn):
        for row in rows:
            store.update_file(run.conn, row["id"], touched=touched, held=reason)


def _count_rows(conn: sqlite3.Connection, lane: str) -> int:
    return int(conn.execute("SELECT count(*) FROM files WHERE lane = ?",
                            (lane,)).fetchone()[0])


def _under(path: str, directories: list[tuple[str, str]]) -> str | None:
    for directory, why in directories:
        if path == directory or path.startswith(directory.rstrip(os.sep) + os.sep):
            return f"under an unreadable directory: {directory}: {why}"
    return None


def _file_lane(run: _Pass, lane: Lane, listing: Any) -> None:
    """Steps 2, 3, 5 and 6 for a lane of files: hold, reuse, mark, delete; queue reads."""
    import moxi.store

    conn, result = run.conn, run.result
    state = result.lanes[lane.id] = _lane_state(lane, listing, run.now)
    rows = {row["path"]: row for row in store.files(conn, lane=lane.id)}
    if not listing.ok:
        _hold_all(run, lane, rows.values(), f"{lane.id} could not be listed: "
                  f"{listing.reason}", state)
        return
    if not listing.entries and rows:
        state["state"] = HELD
        state["reason"] = (f"{lane.id} listed no sources at {lane.root} while the index "
                           "holds rows for it")
        _hold_all(run, lane, rows.values(), state["reason"], state)
        return
    result.listed += len(listing)
    root = listing.root
    unreadable = [(os.path.join(root, *rel.split("/")), why)
                  for rel, why in listing.unreadable]
    result.unreadable.extend(f"{lane.id}: {rel}: {why}" for rel, why in listing.unreadable)
    replaced: dict[str, str] = {}
    listed: dict[str, Any] = {}
    for entry in listing:
        path = str(entry.source.path)
        if entry.compressed:
            state["compressed"] += 1
            if entry.source.session_id is not None:
                replaced[entry.source.session_id] = path
            continue
        listed[path] = entry

    touched: set[int] = set()
    holds: dict[int, str] = {}
    deletes: list[int] = []
    updates: list[tuple[int, dict[str, Any]]] = []
    for path, entry in listed.items():
        row = rows.get(path)
        if lane.archive and entry.source.session_id in run.live_mains:
            if row is not None:
                deletes.append(row["id"])        # the live transcript is read instead
            continue
        if lane.harness == CODEX and entry.header is None:
            if row is not None:
                why = _under(path, unreadable) or "its head could not be read"
                holds[row["id"]] = f"unreadable: {why}"
            continue
        excluded = None
        if lane.harness == CODEX:
            excluded = IMPORTED if _imported(entry.header) else None
            if excluded:
                state["imported"] += 1
                result.excluded += 1
            elif _originator_only(entry.header):
                state["originator_only"] += 1
        if row is not None and _reusable(lane, row, entry, excluded) and not run.rebuild:
            change = {name: value for name, value in (
                ("rank", lane.rank), ("excluded", excluded), ("held", None),
                ("dev", entry.dev), ("ino", entry.ino)) if row[name] != value}
            if change:
                updates.append((row["id"], change))
            result.reused += 1
            continue
        if excluded is not None:
            updates.append((-1, {"entry": entry, "row": row}))
            continue
        cursor = None
        if (row is not None and not run.rebuild and row["excluded"] is None
                and row["marks_read"]):
            cursor = row["cursor"]
        run.tasks.append((lane, entry, row, cursor))

    for path, row in rows.items():
        if path in listed:
            continue
        why = _under(path, unreadable)
        if why is None and row["session_id"] in replaced:
            why = (f"replaced by {replaced[row['session_id']]}, which sessions cannot read; "
                   "kept as it was")
        if why is not None:
            holds[row["id"]] = why
        else:
            deletes.append(row["id"])

    state["held"] = len(holds)
    result.held += len(holds)
    if not (holds or deletes or updates):
        return
    with moxi.store.transaction(conn):
        for file_id, why in holds.items():
            store.update_file(conn, file_id, touched=touched, held=why)
        for file_id, change in updates:
            if file_id == -1:
                _write_excluded(run, lane, change["entry"], change["row"], touched)
            else:
                store.update_file(conn, file_id, touched=touched, **change)
        if deletes:
            result.deleted += store.delete_files(conn, deletes, touched=touched)
        store.claim(conn, touched)


def _reusable(lane: Lane, row: dict, entry: Any, excluded: str | None) -> bool:
    """The row stands for the file as it is now: its reuse keys match, it was read (or
    skipped as imported), its import mark has not flipped and it holds its markers."""
    if row["excluded"] != excluded:
        return False
    if excluded is None and (row["cursor"] is None or not row["marks_read"]):
        return False
    names = ("size", "mtime_ns") if lane.archive else ("dev", "ino", "size", "mtime_ns")
    return all(row[name] == getattr(entry, name) for name in names)


def _entry_fields(lane: Lane, entry: Any) -> dict[str, Any]:
    source = entry.source
    return {"harness": lane.harness, "rank": lane.rank, "kind": _kind(entry),
            "session_id": source.session_id, "agent_id": source.agent_id,
            "size": entry.size, "mtime_ns": entry.mtime_ns, "dev": entry.dev,
            "ino": entry.ino}


def _write_excluded(run: _Pass, lane: Lane, entry: Any, row: dict | None,
                    touched: set[int]) -> None:
    """A rollout an import mark says was imported: its row, with no requests and no
    marks, which holds every marker it could."""
    fields = _entry_fields(lane, entry)
    fields.update(header_facts(entry.header))
    fields.update(excluded=IMPORTED, held=None, cursor=None, read_at=run.now,
                  first_ts=None, last_ts=None, marks_read=1)
    file_id = store.upsert_file(run.conn, lane=lane.id, path=str(entry.source.path),
                                touched=touched, **fields)
    store.delete_requests(run.conn, [file_id], touched=touched)
    store.delete_marks(run.conn, [file_id])


# --- reads ------------------------------------------------------------------------------

def _read_all(run: _Pass, workers: int) -> None:
    """Step 4 for every queued file source: from no cursor in worker processes when a
    rebuild or many fresh sources call for it, the rest here, one transaction each."""
    tasks = run.tasks
    fresh = [t for t in tasks if t[3] is None]
    pooled: list = []
    if run.rebuild or len(fresh) > POOL_ABOVE:
        pooled = tasks if run.rebuild else fresh
    count = _worker_count(workers)
    if pooled and count > 1:
        run.result.workers = count
        _read_pooled(run, pooled, count)
        rest = [t for t in tasks if t[3] is not None] if not run.rebuild else []
    else:
        rest = tasks
        if run.rebuild:
            _read_in_one(run, tasks)
            rest = []
    for lane, entry, row, cursor in rest:
        if run.cancelled():
            return
        got = read_source(_task(lane, entry, cursor))
        _apply_one(run, lane, entry, row, cursor, got)


def _worker_count(workers: int) -> int:
    if workers and workers > 0:
        return workers
    return max(1, min(os.cpu_count() or 1, MAX_WORKERS))


def _task(lane: Lane, entry: Any, cursor: str | None) -> Task:
    return Task(lane.id, str(entry.source.path), dataclasses.replace(entry, header=None),
                cursor)


def _apply_one(run: _Pass, lane: Lane, entry: Any, row: dict | None, cursor: str | None,
               got: Got) -> None:
    import moxi.store

    touched: set[int] = set()
    with moxi.store.transaction(run.conn):
        _write_read(run, lane, entry, row, cursor, got, touched)
        store.claim(run.conn, touched)


def _read_in_one(run: _Pass, tasks: list) -> None:
    """A rebuild read here: every source in one transaction, ending with the claim over
    every key."""
    import moxi.store

    touched: set[int] = set()
    with moxi.store.transaction(run.conn):
        with store.bulk_load(run.conn):
            for lane, entry, row, cursor in tasks:
                if run.cancelled():
                    break
                got = read_source(_task(lane, entry, cursor))
                _write_read(run, lane, entry, row, cursor, got, touched)
        store.claim_all(run.conn)


def _read_pooled(run: _Pass, tasks: list, count: int) -> None:
    """Read `tasks` in `count` worker processes; this process writes each result as it
    comes, all in one transaction that ends with the claim (over every key for a
    rebuild)."""
    import multiprocessing
    from concurrent.futures import ProcessPoolExecutor, as_completed

    import moxi.store

    touched: set[int] = set()
    # spawn: a worker starts clean, whatever threads this process runs
    context = multiprocessing.get_context("spawn")
    with ProcessPoolExecutor(max_workers=count, mp_context=context) as pool, \
            moxi.store.transaction(run.conn):
        futures = {pool.submit(read_source, _task(lane, entry, cursor)):
                   (lane, entry, row, cursor) for lane, entry, row, cursor in tasks}
        with store.bulk_load(run.conn) if run.rebuild else nullcontext():
            for future in as_completed(futures):
                lane, entry, row, cursor = futures[future]
                _write_read(run, lane, entry, row, cursor, future.result(), touched)
                if run.cancelled():
                    for pending in futures:
                        pending.cancel()
                    break
        if run.rebuild:
            store.claim_all(run.conn)
        else:
            store.claim(run.conn, touched)


def _write_read(run: _Pass, lane: Lane, entry: Any, row: dict | None, cursor: str | None,
                got: Got, touched: set[int]) -> None:
    """One source's read into the index, inside the caller's transaction."""
    conn, result = run.conn, run.result
    path = str(entry.source.path)
    if got.error is not None:
        result.unreadable.append(f"{lane.id}: {path}: {got.error}")
        if row is not None:                    # keep its rows and its cursor
            store.update_file(conn, row["id"], touched=touched,
                              held=f"unreadable: {got.error}")
            result.held += 1
            result.lanes[lane.id]["held"] += 1
        return
    result.read += 1
    fields = _entry_fields(lane, entry)
    if entry.header is not None:
        fields.update(header_facts(entry.header))
    fields.update(got.facts)
    fields.update(cursor=got.cursor, held=None, excluded=None, read_at=run.now)
    whole = got.reset or cursor is None
    if got.title is not None or whole:
        fields["title"] = got.title
    if whole:
        # a read from the start holds every marker; one from a cursor keeps the 1 it has,
        # since a row at 0 is never read from its cursor
        fields["marks_read"] = 1
    file_id = store.upsert_file(conn, lane=lane.id, path=path, touched=touched, **fields)
    if row is not None and whole:
        store.delete_requests(conn, [file_id], touched=touched)
        store.delete_marks(conn, [file_id])
    store.upsert_requests(conn, file_id, got.rows.values(), touched=touched)
    store.upsert_marks(conn, file_id, got.marks.values())
    _settle_times(conn, [file_id], touched)
    _send_limits(run, got.limits)


def _settle_times(conn: sqlite3.Connection, file_ids: Iterable[int],
                  touched: set[int]) -> None:
    """Each file's `first_ts` and `last_ts`, from the requests it holds now."""
    for file_id in file_ids:
        first, last = conn.execute("SELECT min(ts), max(ts) FROM requests WHERE file_id = ?",
                                   (file_id,)).fetchone()
        store.update_file(conn, file_id, touched=touched, first_ts=first, last_ts=last)


def _send_limits(run: _Pass, records: list) -> None:
    """Limits records to the readings store, which ignores a repeat. A failure there is
    noted in the result and never stops the index."""
    if not records:
        return
    try:
        run.result.readings_added += int(run.readings.add_codex(records, now=run.now,
                                                                env=run.env))
    except Exception as exc:
        run.result.readings_error = f"add_codex: {exc}"


# --- OpenCode ---------------------------------------------------------------------------

def _opencode_lane(run: _Pass, lane: Lane, listing: Any, sessions: Any) -> None:
    """The OpenCode database: one source with one cursor (kept in `meta`), its requests
    and compaction marks filed under one row per session. A batch that says messages are
    gone is followed by one read from no cursor, which replaces every request and mark of
    the lane. So does a lane with a row whose `marks_read` is 0; that read ends with every
    row of the lane at 1."""
    import moxi.store

    conn, result = run.conn, run.result
    state = result.lanes[lane.id] = _lane_state(lane, listing, run.now)
    rows = {row["part"]: row for row in store.files(conn, lane=lane.id)}
    if not listing.ok:
        _hold_all(run, lane, rows.values(), f"{lane.id} could not be listed: "
                  f"{listing.reason}", state)
        return
    if not listing.entries and rows:
        state["state"] = HELD
        state["reason"] = (f"{lane.id} listed no sessions at {lane.root} while the index "
                           "holds rows for it")
        _hold_all(run, lane, rows.values(), state["reason"], state)
        return
    result.listed += len(listing)
    if run.cancelled():
        return
    db = listing.root
    meta_key = store.cursor_meta_key(lane.id, db)
    unsettled = any(not row["marks_read"] for row in rows.values())
    stored = None if run.rebuild or unsettled else store.get_meta(conn, meta_key)
    source = sessions.Source(OPENCODE, db, None)
    try:
        batch = sessions.records_since(source, stored, kinds=KINDS)
        whole = run.rebuild or unsettled or batch.reset
        if batch.gone:
            batch = sessions.records_since(source, None, kinds=KINDS)
            whole = True
    except sessions.Unreadable as exc:
        why = f"unreadable: {exc}"
        result.unreadable.append(f"{lane.id}: {db}: {exc}")
        _hold_all(run, lane, rows.values(), why, state)
        return

    by_session, marks = _opencode_records(batch.records, lane=lane.id, path=db)
    found = set(by_session) | set(marks)
    entries = {entry.source.session_id: entry for entry in listing}
    touched: set[int] = set()
    changed = (batch.cursor != stored or found or whole
               or any(_session_change(lane, entries[part], rows.get(part))
                      for part in entries)
               or any(part not in entries and part not in found for part in rows))
    if not changed:
        return
    with moxi.store.transaction(conn):
        ids: dict[str, int] = {}
        for part, entry in entries.items():
            change = _session_change(lane, entry, rows.get(part))
            # a row this lane inserts holds its markers: on a cursor read, the lane is
            # settled and the cursor brings a new session's markers with its requests
            if rows.get(part) is None or change:
                ids[part] = store.upsert_file(conn, lane=lane.id, path=db, part=part,
                                              touched=touched, read_at=run.now,
                                              marks_read=1, **_session_fields(lane, entry))
            else:
                ids[part] = rows[part]["id"]
        for part in sorted(found):
            if part not in ids:
                ids[part] = (rows[part]["id"] if part in rows else store.upsert_file(
                    conn, lane=lane.id, path=db, part=part, touched=touched,
                    harness=OPENCODE, rank=lane.rank, kind="main", session_id=part,
                    read_at=run.now, marks_read=1))
        if whole:
            held = [row["id"] for row in rows.values()]
            store.delete_requests(conn, held, touched=touched)
            store.delete_marks(conn, held)
        for part, requests in by_session.items():
            store.upsert_requests(conn, ids[part], requests.values(), touched=touched)
        for part, kept in marks.items():
            store.upsert_marks(conn, ids[part], kept.values())
        gone = [row["id"] for part, row in rows.items()
                if part not in entries and part not in found]
        if gone:
            result.deleted += store.delete_files(conn, gone, touched=touched)
        settle = set(ids[part] for part in by_session)
        if whole:
            settle.update(ids.values())
        _settle_times(conn, sorted(settle), touched)
        if by_session:
            result.read += len(by_session)
        if whole:
            store.settle_marks(conn, lane=lane.id)
        store.set_meta(conn, meta_key, batch.cursor)
        store.claim(conn, touched)


def _opencode_records(records: Iterable[Any], *, lane: str, path: str,
                      ) -> tuple[dict[str, dict[int, store.RequestRow]],
                                 dict[str, dict[tuple[int, str], store.MarkRow]]]:
    """The batch's requests by session and key, and its compaction marks by session and
    `(seq, kind)`.

    A compaction marker comes last among its message's records, after that message's
    requests, and a message's records always come whole. So a mark takes the `seq` of the
    request just before it when that request is of its own message (its `message_time`
    is the marker's time), which sorts the mark after its own compaction request; else
    the message's time in milliseconds. Either way the batch's other messages play no
    part, so a read from the cursor gives a mark the `seq` a whole read gives it."""
    by_session: dict[str, dict[int, store.RequestRow]] = {}
    marks: dict[str, dict[tuple[int, str], store.MarkRow]] = {}
    last: dict[str, tuple[Any, int]] = {}
    for record in records:
        session_id = record.session_id
        if not isinstance(session_id, str):
            continue
        if record.kind == "request":
            row = store.request_row(record, lane=lane, path=path)
            by_session.setdefault(session_id, {})[row.key] = row
            extra = record.extra if isinstance(record.extra, Mapping) else {}
            last[session_id] = (extra.get("message_time"), row.seq)
        elif record.kind == "marker":
            if record.marker == store.COMPACTION:
                mark = store.mark_row(record, seq=0)
                before = last.pop(session_id, None)
                if (before is not None and mark.ts is not None
                        and isinstance(before[0], (int, float)) and before[0] == mark.ts):
                    mark = mark._replace(seq=before[1])
                elif mark.ts is not None:
                    mark = mark._replace(seq=int(round(mark.ts * 1000)))
                marks.setdefault(session_id, {})[(mark.seq, mark.kind)] = mark
    return by_session, marks


def _session_fields(lane: Lane, entry: Any) -> dict[str, Any]:
    header = entry.header
    source = entry.source
    fields: dict[str, Any] = {
        "harness": OPENCODE, "rank": lane.rank, "kind": _kind(entry),
        "session_id": source.session_id, "agent_id": source.agent_id, "held": None,
        "parent_session": None, "cwd": None, "title": None,
    }
    if header is not None:
        fields["cwd"] = _text(header.cwd)
        fields["title"] = _text(header.title)
        fields["parent_session"] = _text(header.parent)
    return fields


def _session_change(lane: Lane, entry: Any, row: dict | None) -> bool:
    if row is None:
        return True
    return any(row[name] != value for name, value in _session_fields(lane, entry).items())


# --- project keys -----------------------------------------------------------------------

def record_projects(conn: sqlite3.Connection, projects: Any,
                    *, exists: Callable[[str], bool] = os.path.isdir) -> int:
    """Step 7: give each row with no `project` the key of its session's start, else, while
    its `cwd` exists, the key `projects.for_path(cwd)` names. One lookup per distinct
    `cwd`; `None`, an unreadable or busy store leave the row for a later pass; a `cwd`
    that no longer exists is never looked up. Returns how many rows took a key."""
    import moxi.store

    rows = conn.execute(
        "SELECT id, harness, session_id, cwd FROM files "
        "WHERE project IS NULL AND cwd IS NOT NULL AND excluded IS NULL").fetchall()
    if not rows:
        return 0
    started: dict[tuple[str, str, str], str] = {}
    for harness, session_id, cwd, project in conn.execute(
            "SELECT harness, session_id, cwd, project FROM files "
            "WHERE project IS NOT NULL AND session_id IS NOT NULL AND cwd IS NOT NULL "
            "ORDER BY first_ts IS NULL, first_ts, id"):
        started.setdefault((harness, session_id, cwd), project)
    failures = _lookup_errors(projects)
    found: dict[str, str | None] = {}
    keys: dict[int, str] = {}
    for file_id, harness, session_id, cwd in rows:
        key = started.get((harness, session_id, cwd)) if session_id is not None else None
        if key is None:
            if cwd not in found:
                found[cwd] = None
                if exists(cwd):
                    try:
                        project = projects.for_path(cwd)
                    except failures:
                        project = None
                    found[cwd] = getattr(project, "key", None) if project is not None else None
            key = found[cwd]
        if key is not None:
            keys[file_id] = key
    if not keys:
        return 0
    touched: set[int] = set()
    with moxi.store.transaction(conn):
        for file_id, key in keys.items():
            store.update_file(conn, file_id, touched=touched, project=key)
    return len(keys)


def _lookup_errors(projects: Any) -> tuple[type[BaseException], ...]:
    """What a lookup may raise that leaves the row for a later pass: the API's
    `Unreadable` (its `Busy` is one) and `OSError`."""
    named = getattr(projects, "Unreadable", None)
    errors: list[type[BaseException]] = [OSError]
    if isinstance(named, type) and issubclass(named, BaseException):
        errors.append(named)
    return tuple(errors)


# --- meta -------------------------------------------------------------------------------

def _store_meta(run: _Pass, chosen: list[Lane]) -> None:
    import moxi.store

    raw = store.get_meta(run.conn, store.META_LANES)
    try:
        known = json.loads(raw) if raw else {}
    except ValueError:
        known = {}
    if not isinstance(known, dict):
        known = {}
    known.update(run.result.lanes)
    for lane in run.result.dropped_lanes:
        known.pop(lane, None)
    with moxi.store.transaction(run.conn):
        store.set_meta(run.conn, store.META_LANES, json.dumps(known, sort_keys=True))
        if not run.result.cancelled:
            store.set_meta(run.conn, store.META_LAST_PASS, str(run.result.finished_at))


__all__ = ["Lane", "PassResult", "Task", "Got", "configured_lanes", "pass_", "read_source",
           "header_facts", "record_projects", "KINDS", "RANKS", "ARCHIVE_RANK",
           "POOL_ABOVE", "IMPORTED", "LOOKUP"]

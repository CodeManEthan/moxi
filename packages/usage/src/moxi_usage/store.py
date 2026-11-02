"""The token index's store, `STATE/usage/index.db`: its schema, its writes and the claim.

**The store.** One SQLite file opened with `moxi.store.connect` (write-ahead logging,
`synchronous=NORMAL`, a busy timeout, created 0600). Its schema moves forward by
`moxi.store.migrate` steps (`STEPS`); each step also writes its number into `meta` as
`index_version`. One connection per process: a command opens it and closes it.

**Tables.** `files` holds one row per source the pass read (a transcript, a rollout, or one
OpenCode session under the database's path), with its lane, rank, header facts, reuse
keys and sessions' cursor. `requests` holds one row per request per file, keyed on
`(file_id, key)`, with its token counts. An unknown count is NULL and its column is named
in `unknown`; 0 means the harness wrote 0. `meta` holds `index_version`, the last pass and
the lanes.

**The claim.** One request can sit in several files: an archive copy of a transcript, a
resumed session that repeats its parent's requests. Each key is counted once, by the copy
the `claimed` view picks: among the copies in files that are not excluded, the one whose
file has the earliest `first_ts`, then the largest `size`, then the lowest `rank`, then
the first `path` (OpenCode's session id and the row id after that, so a tie still has one
answer). Reports read `requests.won` instead of the view, because the view partitions
every request in the index on every read. The rule that keeps the two the same: a key's
claim is worked out again in the same transaction as any change to a copy of it, or to the
order or exclusion of a file that holds one. So every write here takes a `touched` set and
adds the keys it changed; the caller runs `claim(conn, touched)` before its transaction
commits. `check(conn)` compares `won` with the view over the whole index.

Every write must run inside the caller's `moxi.store.transaction(conn)`; a write outside
one raises `StoreError`.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple

from moxi.store import StoreError

from .keys import request_key

if TYPE_CHECKING:
    import sqlite3

    from moxi_sessions.api import Request

MODULE = "usage"
INDEX_FILE = "index.db"
LOCK_FILE = "index.lock"

# --- meta names -------------------------------------------------------------------------

#: The schema version, the same number as the store's `user_version`.
META_INDEX_VERSION = "index_version"
#: When the last pass ended, in epoch seconds.
META_LAST_PASS = "last_pass"
#: The lanes the last pass knew, as JSON.
META_LANES = "lanes"


def cursor_meta_key(lane: str, path: str) -> str:
    """The `meta` name of a source's one cursor when no single `files` row holds it (the
    OpenCode database, whose requests sit under one row per session)."""
    return f"cursor:{lane}:{path}"


# --- the schema -------------------------------------------------------------------------

#: The claim's order, the same in the view and in `claim`.
CLAIM_ORDER = "f.first_ts, f.size DESC, f.rank, f.path, f.part, f.id"

SCHEMA_1 = """
CREATE TABLE files (
  id INTEGER PRIMARY KEY,
  lane TEXT NOT NULL,
  harness TEXT NOT NULL,
  path TEXT NOT NULL,
  part TEXT NOT NULL DEFAULT '',
  rank INTEGER NOT NULL,
  kind TEXT NOT NULL,
  session_id TEXT, agent_id TEXT, parent_session TEXT,
  cwd TEXT, title TEXT,
  origin TEXT,
  originator TEXT,
  project TEXT,
  size INTEGER, mtime_ns INTEGER, dev INTEGER, ino INTEGER,
  cursor TEXT,
  first_ts REAL, last_ts REAL,
  excluded TEXT,
  held TEXT,
  read_at REAL NOT NULL,
  UNIQUE (lane, path, part));

CREATE TABLE requests (
  file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
  key INTEGER NOT NULL,
  seq INTEGER NOT NULL,
  ts REAL, model TEXT, response_model TEXT, provider TEXT,
  input INTEGER, cache_read INTEGER, cache_write INTEGER, cache_write_1h INTEGER,
  output INTEGER, reasoning INTEGER,
  service_tier TEXT, speed TEXT, harness_cost REAL,
  sidechain INTEGER NOT NULL, synthetic INTEGER NOT NULL,
  turn_id TEXT, agent_id TEXT,
  unknown TEXT,
  won INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (file_id, key)) WITHOUT ROWID;
CREATE INDEX requests_ts ON requests(ts);
CREATE INDEX requests_key ON requests(key);
CREATE INDEX requests_won_ts ON requests(won, ts);
CREATE INDEX files_session ON files(session_id);

CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);

CREATE VIEW claimed AS
SELECT * FROM (
  SELECT r.*, f.harness, f.lane, f.kind, f.session_id, f.cwd, f.origin, f.project,
         ROW_NUMBER() OVER (PARTITION BY r.key
                            ORDER BY f.first_ts, f.size DESC, f.rank, f.path, f.part, f.id)
           AS claim_n
  FROM requests r JOIN files f ON f.id = r.file_id
  WHERE f.excluded IS NULL)
WHERE claim_n = 1;
"""


@dataclass(frozen=True)
class Step:
    """One schema step. `drops` is true for a step that removes indexed rows, which
    refuses while an archive lane holding rows is offline (its rows could not be read
    again)."""

    sql: str
    drops: bool = False


#: The schema steps, in order; the store's version is how many have run.
STEPS: tuple[Step, ...] = (Step(SCHEMA_1),)
INDEX_VERSION = len(STEPS)


class MigrationRefused(StoreError):
    """A schema step would drop rows that only an offline archive lane holds.

    `lanes` names them. A command that stops on this exits `exit_code` (refused);
    `force=True` runs the step anyway and drops them.
    """

    exit_code = 3

    def __init__(self, lanes: Sequence[str], version: int, target: int):
        self.lanes = tuple(lanes)
        super().__init__(
            f"moving the index from version {version} to {target} drops rows that only the "
            f"offline archive lane(s) {', '.join(self.lanes)} hold; bring them online, or "
            "rebuild with --force to drop them")


@dataclass(frozen=True)
class Migration:
    """What `migrate` did: the version before and after, and the offline lanes a forced
    step dropped."""

    before: int
    after: int
    dropped: tuple[str, ...] = ()


# --- opening ----------------------------------------------------------------------------

def index_path(env: Mapping[str, str] | None = None) -> Path:
    """`STATE/usage/index.db`."""
    from moxi.paths import Layout

    return Layout(MODULE).state_dir(env) / INDEX_FILE


def lock_path(env: Mapping[str, str] | None = None) -> Path:
    """`STATE/usage/index.lock`, the lock one pass holds."""
    from moxi.paths import Layout

    return Layout(MODULE).state_dir(env) / LOCK_FILE


def connect(*, env: Mapping[str, str] | None = None, path: str | Path | None = None,
            create: bool = True) -> sqlite3.Connection:
    """Open the index (at `path`, else `index_path(env)`) without moving its schema.

    Rows come back as `sqlite3.Row`, read by name or by place.
    """
    import sqlite3

    import moxi.store

    conn = moxi.store.connect(index_path(env) if path is None else path, create=create)
    conn.row_factory = sqlite3.Row
    return conn


def migrate(conn: sqlite3.Connection, *, offline: Iterable[str] = (), force: bool = False,
            steps: Sequence[Step] = STEPS) -> Migration:
    """Bring the index's schema up to `len(steps)`.

    `offline` names the archive lanes whose roots can't be read now. When a pending step
    drops rows and any of those lanes holds rows, it raises `MigrationRefused`, unless
    `force`, which runs it and names those lanes in `Migration.dropped`. A store newer than
    `steps` raises `moxi.store.StoreTooNew`.
    """
    import moxi.store

    before = _version(conn)
    pending = steps[before:]
    dropped: tuple[str, ...] = ()
    if any(step.drops for step in pending):
        dropped = tuple(lanes_with_rows(conn, offline))
        if dropped and not force:
            raise MigrationRefused(dropped, before, len(steps))
    texts = [_with_version(step.sql, number) for number, step in enumerate(steps, 1)]
    after = moxi.store.migrate(conn, texts)
    return Migration(before, after, dropped if after != before else ())


def open_index(*, env: Mapping[str, str] | None = None, path: str | Path | None = None,
               offline: Iterable[str] = (), force: bool = False) -> sqlite3.Connection:
    """`connect` and `migrate`; the connection is closed again when the migration raises."""
    conn = connect(env=env, path=path)
    try:
        migrate(conn, offline=offline, force=force)
    except BaseException:
        conn.close()
        raise
    return conn


def _version(conn: sqlite3.Connection) -> int:
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def _with_version(sql: str, number: int) -> str:
    return (f"{sql.rstrip().rstrip(';')};\n"
            f"INSERT INTO meta (key, value) VALUES ('{META_INDEX_VERSION}', '{number}') "
            "ON CONFLICT (key) DO UPDATE SET value = excluded.value;")


def lanes_with_rows(conn: sqlite3.Connection, lanes: Iterable[str]) -> list[str]:
    """The lanes of `lanes` the index holds `files` rows for, sorted. Empty before the
    schema exists."""
    wanted = sorted(set(lanes))
    if not wanted or not _has_table(conn, "files"):
        return []
    marks = ",".join("?" * len(wanted))
    return [row[0] for row in conn.execute(
        f"SELECT DISTINCT lane FROM files WHERE lane IN ({marks}) ORDER BY lane", wanted)]


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                        (name,)).fetchone() is not None


# --- meta -------------------------------------------------------------------------------

def get_meta(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return default if row is None else row[0]


def set_meta(conn: sqlite3.Connection, key: str, value: str | None) -> None:
    """Set one `meta` value; None removes it."""
    if value is None:
        conn.execute("DELETE FROM meta WHERE key = ?", (key,))
    else:
        conn.execute("INSERT INTO meta (key, value) VALUES (?, ?) "
                     "ON CONFLICT (key) DO UPDATE SET value = excluded.value", (key, value))


def all_meta(conn: sqlite3.Connection) -> dict[str, str]:
    return {row[0]: row[1] for row in conn.execute("SELECT key, value FROM meta")}


# --- files ------------------------------------------------------------------------------

#: Every `files` column but `id`.
FILE_COLUMNS = ("lane", "harness", "path", "part", "rank", "kind", "session_id", "agent_id",
                "parent_session", "cwd", "title", "origin", "originator", "project", "size",
                "mtime_ns", "dev", "ino", "cursor", "first_ts", "last_ts", "excluded",
                "held", "read_at")
#: The columns that place a file in the claim's order or take it out of the claim.
ORDER_COLUMNS = frozenset({"first_ts", "size", "rank", "path", "part", "excluded"})


def get_file(conn: sqlite3.Connection, *, lane: str, path: str,
             part: str = "") -> dict[str, Any] | None:
    """The `files` row for one source, as a dict with `id`, or None."""
    row = conn.execute("SELECT * FROM files WHERE lane = ? AND path = ? AND part = ?",
                       (lane, path, part)).fetchone()
    return None if row is None else dict(row)


def files(conn: sqlite3.Connection, *, lane: str | None = None,
          path: str | None = None) -> list[dict[str, Any]]:
    """`files` rows as dicts, every lane or one, every path or one (an OpenCode database's
    session rows share its path), ordered by id."""
    where, args = [], []
    if lane is not None:
        where.append("lane = ?")
        args.append(lane)
    if path is not None:
        where.append("path = ?")
        args.append(path)
    sql = "SELECT * FROM files" + (" WHERE " + " AND ".join(where) if where else "")
    return [dict(row) for row in conn.execute(sql + " ORDER BY id", args)]


def upsert_file(conn: sqlite3.Connection, *, lane: str, path: str, part: str = "",
                touched: set[int], **fields: Any) -> int:
    """Insert or update the row for `(lane, path, part)` and return its id.

    A new row needs `harness`, `rank`, `kind` and `read_at`; the rest default to NULL.
    On an existing row only the given columns change, and when one of them moves the file
    in the claim's order or in or out of the claim (`first_ts`, `size`, `rank`,
    `excluded`), every key the file holds goes into `touched`.
    """
    _require_transaction(conn)
    _check_columns(fields)
    row = conn.execute("SELECT id FROM files WHERE lane = ? AND path = ? AND part = ?",
                       (lane, path, part)).fetchone()
    if row is not None:
        update_file(conn, row[0], touched=touched, **fields)
        return int(row[0])
    values = {"lane": lane, "path": path, "part": part, **fields}
    names = list(values)
    cursor = conn.execute(
        f"INSERT INTO files ({', '.join(names)}) VALUES ({', '.join('?' * len(names))})",
        [values[name] for name in names])
    return int(cursor.lastrowid)


def update_file(conn: sqlite3.Connection, file_id: int, *, touched: set[int],
                **fields: Any) -> None:
    """Change the given columns of one `files` row.

    When `first_ts`, `size`, `rank`, `path`, `part` or `excluded` changes, the file's keys
    go into `touched`, so the claim is worked out again for a reused file whose import mark
    or rank changed as for one that was read. A missing row raises `KeyError`.
    """
    _require_transaction(conn)
    _check_columns(fields)
    if not fields:
        return
    names = list(fields)
    old = conn.execute(f"SELECT {', '.join(names)} FROM files WHERE id = ?",
                       (file_id,)).fetchone()
    if old is None:
        raise KeyError(f"no files row {file_id}")
    changed = [name for index, name in enumerate(names) if old[index] != fields[name]]
    if not changed:
        return
    if ORDER_COLUMNS.intersection(changed):
        touched.update(file_keys(conn, file_id))
    conn.execute(f"UPDATE files SET {', '.join(f'{name} = ?' for name in changed)} "
                 "WHERE id = ?", [*(fields[name] for name in changed), file_id])


def delete_files(conn: sqlite3.Connection, file_ids: Iterable[int], *,
                 touched: set[int]) -> int:
    """Delete `files` rows and their requests; their keys go into `touched` first.
    Returns how many rows went."""
    _require_transaction(conn)
    count = 0
    for file_id in set(file_ids):
        touched.update(file_keys(conn, file_id))
        count += conn.execute("DELETE FROM files WHERE id = ?", (file_id,)).rowcount
    return count


def file_keys(conn: sqlite3.Connection, file_id: int) -> list[int]:
    """Every key one file holds."""
    return [row[0] for row in conn.execute("SELECT key FROM requests WHERE file_id = ?",
                                           (file_id,))]


def _check_columns(fields: Mapping[str, Any]) -> None:
    unknown = sorted(set(fields) - set(FILE_COLUMNS))
    if unknown:
        raise ValueError(f"not a files column: {', '.join(unknown)}")


# --- requests ---------------------------------------------------------------------------

class RequestRow(NamedTuple):
    """One `requests` row, less its file and its claim."""

    key: int
    seq: int
    ts: float | None
    model: str | None
    response_model: str | None
    provider: str | None
    input: int | None
    cache_read: int | None
    cache_write: int | None
    cache_write_1h: int | None
    output: int | None
    reasoning: int | None
    service_tier: str | None
    speed: str | None
    harness_cost: float | None
    sidechain: int
    synthetic: int
    turn_id: str | None
    agent_id: str | None
    unknown: str | None


#: The token columns and the sessions `Request` field each is read from.
TOKEN_COLUMNS: dict[str, str] = {
    "input": "input_tokens",
    "cache_read": "cache_read_input_tokens",
    "cache_write": "cache_creation_input_tokens",
    "cache_write_1h": "cache_creation_1h_input_tokens",
    "output": "output_tokens",
    "reasoning": "reasoning_output_tokens",
}


def unknown_columns(value: str | None) -> list[str]:
    """The token columns a row's `unknown` names (it is comma-separated, in
    `TOKEN_COLUMNS` order)."""
    return value.split(",") if value else []


def request_row(request: Request, *, lane: str, path: str) -> RequestRow:
    """The row one sessions `Request` read from `path` in `lane` becomes.

    The key is `keys.request_key` (a file-local id is qualified by `lane` and `path`).
    `seq` orders the source's requests as sessions' `at` does: a file's request takes its
    line number; an OpenCode request (no line) takes its `start_time` in milliseconds,
    else its `time`, else 0. A token count sessions reports unknown, or as null, is NULL
    and named in `unknown`; 0 stays 0. Every other unknown fact is NULL, and an unknown
    `sidechain` or `synthetic` is 0.
    """
    unknown = [column for column, name in TOKEN_COLUMNS.items()
               if not _is_count(getattr(request, name))]
    return RequestRow(
        key=request_key(request.harness, request.request_id, lane=lane, path=path),
        seq=_seq(request),
        ts=_number(request.time),
        model=_text(request.model),
        response_model=_text(request.response_model),
        provider=_text(request.provider),
        **{column: _count(getattr(request, name)) for column, name in TOKEN_COLUMNS.items()},
        service_tier=_text(request.service_tier),
        speed=_text(request.speed),
        harness_cost=_number(request.cost_usd),
        sidechain=1 if request.sidechain is True else 0,
        synthetic=1 if request.synthetic is True else 0,
        turn_id=_text(request.turn_id),
        agent_id=_text(request.agent_id),
        unknown=",".join(unknown) or None,
    )


def _seq(request: Request) -> int:
    line = request.at.get("line") if request.at else None
    if isinstance(line, int) and not isinstance(line, bool):
        return line
    for value in (request.start_time, request.time):
        number = _number(value)
        if number is not None:
            return int(round(number * 1000))
    return 0


def _is_count(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _count(value: Any) -> int | None:
    return value if _is_count(value) else None


def _number(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def _text(value: Any) -> str | None:
    if value is None or not isinstance(value, (str, int, float)) or isinstance(value, bool):
        return None
    return str(value)


_REQUEST_COLUMNS = ("file_id", *RequestRow._fields)
_UPSERT = (f"INSERT INTO requests ({', '.join(_REQUEST_COLUMNS)}) "
           f"VALUES ({', '.join('?' * len(_REQUEST_COLUMNS))}) "
           "ON CONFLICT (file_id, key) DO UPDATE SET "
           + ", ".join(f"{name} = excluded.{name}" for name in RequestRow._fields
                       if name != "key"))


def upsert_requests(conn: sqlite3.Connection, file_id: int, rows: Iterable[RequestRow], *,
                    touched: set[int]) -> int:
    """Write each row under `file_id`, replacing the copy already there with its key, so a
    request that comes again leaves one row. Every key goes into `touched`. Returns how
    many rows were written."""
    _require_transaction(conn)
    count = 0

    def params():
        nonlocal count
        for row in rows:
            touched.add(row.key)
            count += 1
            yield (file_id, *row)

    conn.executemany(_UPSERT, params())
    return count


def delete_requests(conn: sqlite3.Connection, file_ids: Iterable[int], *,
                    touched: set[int]) -> int:
    """Delete every request of the given files (a `reset` from sessions); their keys go
    into `touched` first. The `files` rows stay. Returns how many requests went."""
    _require_transaction(conn)
    count = 0
    for file_id in set(file_ids):
        touched.update(file_keys(conn, file_id))
        count += conn.execute("DELETE FROM requests WHERE file_id = ?", (file_id,)).rowcount
    return count


# --- the claim --------------------------------------------------------------------------

def claim(conn: sqlite3.Connection, keys: Iterable[int]) -> int:
    """Work out the claim again for `keys`: every copy of each key, wherever its file and
    time fall, takes `won = 0`, and the copy the claim's order puts first takes `won = 1`.
    Run it, inside the transaction that made the changes, with the `touched` set every
    write here filled. Returns how many rows' `won` changed."""
    _require_transaction(conn)
    keys = set(keys)
    if not keys:
        return 0
    conn.execute("CREATE TEMP TABLE IF NOT EXISTS claim_keys (key INTEGER PRIMARY KEY)")
    conn.execute("DELETE FROM temp.claim_keys")
    conn.executemany("INSERT INTO temp.claim_keys (key) VALUES (?)", ((k,) for k in keys))
    try:
        return _settle(conn, scoped=True)
    finally:
        conn.execute("DELETE FROM temp.claim_keys")


def claim_all(conn: sqlite3.Connection) -> int:
    """Work out every key's claim, as a rebuild ends. Returns how many rows changed."""
    _require_transaction(conn)
    return _settle(conn, scoped=False)


def _settle(conn: sqlite3.Connection, *, scoped: bool) -> int:
    within = " AND r.key IN (SELECT key FROM temp.claim_keys)" if scoped else ""
    own = " AND key IN (SELECT key FROM temp.claim_keys)" if scoped else ""
    conn.execute("CREATE TEMP TABLE IF NOT EXISTS claim_won "
                 "(key INTEGER PRIMARY KEY, file_id INTEGER NOT NULL)")
    conn.execute("DELETE FROM temp.claim_won")
    try:
        conn.execute(
            "INSERT INTO temp.claim_won (key, file_id) "
            "SELECT key, file_id FROM ("
            "  SELECT r.key, r.file_id, ROW_NUMBER() OVER ("
            f"   PARTITION BY r.key ORDER BY {CLAIM_ORDER}) AS claim_n"
            "  FROM requests r JOIN files f ON f.id = r.file_id"
            f"  WHERE f.excluded IS NULL{within}) "
            "WHERE claim_n = 1")
        # `+won` keeps SQLite on the key index: the won index would walk every claim
        lost = conn.execute(
            "UPDATE requests SET won = 0 WHERE +won = 1" + own + " AND NOT EXISTS ("
            "SELECT 1 FROM temp.claim_won w "
            "WHERE w.key = requests.key AND w.file_id = requests.file_id)").rowcount
        gained = conn.execute(
            "UPDATE requests SET won = 1 WHERE +won = 0" + own + " AND (file_id, key) IN ("
            "SELECT file_id, key FROM temp.claim_won)").rowcount
    finally:
        conn.execute("DELETE FROM temp.claim_won")
    return lost + gained


def duplicates(conn: sqlite3.Connection, *, lanes: Iterable[str] | None = None,
               limit: int | None = None) -> list[dict[str, Any]]:
    """The files whose every request another file claims, each named as a duplicate of
    the file that claims its first request (lowest `seq`, then key), in lane and path
    order. A file left out as imported is not one. `lanes` keeps the duplicates in those
    lanes; `limit` keeps the first so many. Each is a dict of `lane`, `path`, `part`,
    `session_id`, `requests` (its count) and `of_lane`, `of_path`, `of_part`."""
    where, args = "", []
    if lanes is not None:
        lanes = list(lanes)
        if not lanes:
            return []
        where = f" AND f.lane IN ({','.join('?' * len(lanes))})"
        args.extend(lanes)
    # `+won` keeps SQLite on each file's primary key and on the key index. With no stats,
    # it takes the won index for `won = 1`, which nearly every row matches, so each file
    # would walk every claimed row: minutes on a large index, where this way reads each
    # file's own rows once.
    sql = (
        "SELECT f.lane, f.path, f.part, f.session_id,"
        " (SELECT count(*) FROM requests c WHERE c.file_id = f.id) AS requests,"
        " w.lane AS of_lane, w.path AS of_path, w.part AS of_part"
        " FROM files f"
        " JOIN requests rw ON rw.key = (SELECT r.key FROM requests r WHERE r.file_id = f.id"
        "                               ORDER BY r.seq, r.key LIMIT 1) AND +rw.won = 1"
        " JOIN files w ON w.id = rw.file_id"
        " WHERE f.excluded IS NULL AND w.id != f.id"
        " AND NOT EXISTS (SELECT 1 FROM requests x WHERE x.file_id = f.id AND +x.won = 1)"
        + where + " ORDER BY f.lane, f.path, f.part")
    if limit is not None:
        sql += " LIMIT ?"
        args.append(limit)
    return [dict(zip(("lane", "path", "part", "session_id", "requests", "of_lane",
                      "of_path", "of_part"), row))
            for row in conn.execute(sql, args)]


@contextmanager
def bulk_load(conn: sqlite3.Connection) -> Iterator[None]:
    """Drop the `requests` table's secondary indexes for a load inside the caller's
    transaction, and make them again, from the same SQL, when it ends without an error.
    A rebuild writes every row, and loading them with the indexes off and building each
    index once is faster than keeping them up to date row by row. On an error the
    caller's rollback brings the indexes back with everything else."""
    _require_transaction(conn)
    found = conn.execute("SELECT name, sql FROM sqlite_master WHERE type = 'index' "
                         "AND tbl_name = 'requests' AND sql IS NOT NULL").fetchall()
    for name, _sql in found:
        conn.execute(f'DROP INDEX "{name}"')
    yield
    for _name, sql in found:
        conn.execute(sql)


def check(conn: sqlite3.Connection) -> list[int]:
    """The keys whose stored claim differs from the `claimed` view, over the whole index,
    sorted; empty when they agree."""
    return [row[0] for row in conn.execute(
        "SELECT key FROM (SELECT file_id, key FROM requests WHERE won = 1 "
        "                 EXCEPT SELECT file_id, key FROM claimed) "
        "UNION "
        "SELECT key FROM (SELECT file_id, key FROM claimed "
        "                 EXCEPT SELECT file_id, key FROM requests WHERE won = 1) "
        "ORDER BY key")]


def _require_transaction(conn: sqlite3.Connection) -> None:
    if not conn.in_transaction:
        raise StoreError("the index is written only inside moxi.store.transaction(conn)")


__all__ = [
    "INDEX_FILE", "LOCK_FILE", "META_INDEX_VERSION", "META_LAST_PASS", "META_LANES",
    "CLAIM_ORDER", "STEPS", "INDEX_VERSION", "Step", "Migration", "MigrationRefused",
    "FILE_COLUMNS", "ORDER_COLUMNS", "TOKEN_COLUMNS", "RequestRow",
    "index_path", "lock_path", "connect", "migrate", "open_index", "lanes_with_rows",
    "cursor_meta_key", "get_meta", "set_meta", "all_meta",
    "get_file", "files", "upsert_file", "update_file", "delete_files", "file_keys",
    "request_row", "unknown_columns", "upsert_requests", "delete_requests",
    "claim", "claim_all", "check",
]

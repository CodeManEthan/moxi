"""The move of comms 0.2's files into comms.db, once.

comms 0.2 kept its data in three SQLite files and a folder of cursor files:

    STATE/comms/record.db       messages, rooms, the queue, principals, meta
    DATA/comms/directory.db     identities, rooms' kinds, operators, meta
    DATA/comms/bindings.db      the binding history
    STATE/comms/cursors/local/  one JSON cursor per name

`store.open_write` calls `move` when those files are there and comms.db isn't, and
`finish` when comms.db carries the move's marker and some of the files are still under
their 0.2 names. Both run under the opener's lock. `read` is the move's first half alone
and writes nothing: `moxi comms migrate --dry-run` and the doctor use it.

The move refuses rather than guesses. It writes nothing, and the opener refuses, when a
comms 0.2 daemon still answers on its socket; a 0.2 database is empty, fails to open or
fails `PRAGMA quick_check`; a table or column it reads is missing or the database's
`user_version` isn't 0.2's; the record has messages but lacks 0.2's own conversion
markers, which means a store older than 0.2; or a cursor file doesn't parse or names an
identity comms can't read. The refusal names the file and the reason.

What it writes, in one transaction into `comms.db.new`: every row carried, ids and
`next_id` kept, the operator's messages with their kind renamed from `user` to
`operator`; queue rows as `waiting`; open binding intervals closed as fenced;
send-only identities as unowned reserved names, never as standing rows; and the marker,
`migrated_from = "0.2"`, with the time, the fenced names and each moved file's fingerprint (size and
modification time). Mail that 0.2 would have replayed from the record becomes queue
rows: for a name with a cursor, the messages above it that name it on line zero; for a
name without one, 0.2's bounded set (`[replay]`). After this nothing is replayed from the
record. The new file is committed, checkpointed and closed, then renamed to comms.db, so
a run that dies midway leaves no comms.db and the next open starts again.

What it leaves: each 0.2 file renamed in place to `<name>.0.2-moved` (the cursor folder
to `cursors.0.2-moved`). Nothing is deleted. A run stopped between the commit and the
renames finishes them at the next open, when the files still match the fingerprint.

Older shapes it meets: tables 0.2 no longer reads (an old directory's `webhooks` and its
claim tables) are left in the renamed file and not carried; rooms from before comms had
one room are carried with no kind, their messages with them, and nothing routes to
them; a directory whose `agents` has no `kind` column reads every row as `agent`, as
0.2 itself read it.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from moxi import store as _store

from .. import machine_line, paths as _paths
from ..record import AUTHOR_OPERATOR, AUTHOR_OPERATOR_OLD
from ..scoping import GLOBAL, NAME_RE, identity_of, is_auto_name, qualify, split_tag
from .open import StoreRefused, state_of, MARKED
from .schema import (META_AUTHOR_STAMPS, META_MIGRATED_FROM, META_NEXT_ID,
                     META_QUEUE_MIGRATED, ROOM_KINDS, STEPS)

log = logging.getLogger("comms.store")

#: The version the move reads, as the marker spells it.
FROM = "0.2"
#: What a moved file's name gets.
MOVED_SUFFIX = ".0.2-moved"
#: The file the move builds before it renames it to comms.db.
NEW_SUFFIX = ".new"

#: `meta` keys the move writes beside the marker.
META_MIGRATED_AT = "migrated_at"
META_MIGRATED_FILES = "migrated_files"
META_MIGRATED_FENCED = "migrated_fenced"

#: The 0.2 files, in the order the move renames them.
ROLES = ("record", "directory", "bindings", "cursors")
#: The backend whose cursor folder the move reads; any other folder is test data.
CURSOR_BACKEND = "local"
#: The operator's name, whom `[held]` never holds.
OPERATOR = "operator"
#: The names room, where all name mail rides.
NAMES = "#names"

#: Each 0.2 database's tables and the columns the move reads. A column in `OPTIONAL`
#: may be missing; the move then reads it as 0.2 read it.
REQUIRED = {
    "record": {
        "channels": ("channel_id", "name", "created_at"),
        "messages": ("id", "channel_id", "author_id", "author_kind", "author_display",
                     "content", "timestamp"),
        "meta": ("key", "value"),
        "queue": ("message_id", "name"),
        "principals": ("principal_id", "name"),
        "denied": ("principal_id", "channel_id"),
    },
    "directory": {
        "agents": ("name", "scope", "status", "created_at", "kind"),
        "rooms": ("channel_id", "name", "kind", "created_at"),
        "meta": ("key", "value"),
        "operators": ("user_id", "name", "created_at"),
    },
    "bindings": {
        "bindings": ("id", "qualified", "name", "scope", "tier", "adapter", "bound_at",
                     "released_at", "fenced"),
    },
}
OPTIONAL = {("directory", "agents", "kind"): "agent"}
#: The `user_version` every comms 0.2 database has: 0.2 never set one.
KNOWN_VERSIONS = (0,)

#: The line the report gives each fenced binding, and the doctor while its mail waits.
FENCED_LINE = ("{name}: its session was launched before the upgrade; relaunch it to get "
               "its mail")
#: What the reserved row of a moved send-only name says.
SEND_ONLY_NOTE = "a send-only name of comms 0.2, not yet declared by a module"


class DaemonLive(StoreRefused):
    """A comms 0.2 daemon still answers on its socket, so its files are still in use.
    On an upgrade this is the usual refusal at install time: the unit step that stops it
    hasn't run yet, and the files move at the new daemon's start."""


def files(env=None) -> dict[str, Path]:
    """comms 0.2's files that are still under their 0.2 names, by role, the cursor
    folder included."""
    found = {"record": _paths.record_db(env), "directory": _paths.directory_db(env),
             "bindings": _paths.bindings_db(env), "cursors": _paths.cursors_dir(env)}
    return {role: p for role, p in found.items()
            if (p.is_dir() if role == "cursors" else p.exists())}


def moved_name(p: Path) -> Path:
    return p.with_name(p.name + MOVED_SUFFIX)


def leftovers(env=None) -> list[Path]:
    """The renamed 0.2 files still on disk, for the doctor."""
    return [moved_name(p) for p in (_paths.record_db(env), _paths.directory_db(env),
                                    _paths.bindings_db(env), _paths.cursors_dir(env))
            if moved_name(p).exists()]


# -- what the move found ----------------------------------------------------------------

@dataclass
class Plan:
    """What the move read, and what it writes or wrote."""

    files: dict = field(default_factory=dict)          # role -> Path
    fingerprint: dict = field(default_factory=dict)    # role -> its fingerprint
    meta: dict = field(default_factory=dict)
    rooms: list = field(default_factory=list)          # (room_id, name, kind, created_at)
    unkinded: list = field(default_factory=list)       # room names carried with no kind
    messages: list = field(default_factory=list)
    queue: list = field(default_factory=list)          # (name, message_id, queued_at, held)
    made: int = 0                                      # queue rows made for row-less mail
    cursors: list = field(default_factory=list)        # (name, message_id, updated_at)
    skipped: list = field(default_factory=list)        # lines naming what wasn't read
    bindings: list = field(default_factory=list)
    fenced: list = field(default_factory=list)         # qualified names
    identities: list = field(default_factory=list)
    operators: list = field(default_factory=list)
    reserved: list = field(default_factory=list)       # qualified send-only names
    principals: list = field(default_factory=list)
    denied: list = field(default_factory=list)
    renamed: list = field(default_factory=list)        # (from, to)
    # What the reading passes to the rows it makes.
    directory_rows: dict = field(default_factory=dict, repr=False)  # (name, scope) -> row
    directory_rooms: dict = field(default_factory=dict, repr=False)  # room id -> row
    queued: list = field(default_factory=list, repr=False)  # 0.2's (name, message id)
    cursor_of: dict = field(default_factory=dict, repr=False)  # name -> message id

    def counts(self) -> str:
        return (f"{len(self.rooms)} room(s), {len(self.messages)} message(s), "
                f"{len(self.queue)} queue row(s) ({self.made} made for mail handed and "
                f"not handled), {len(self.cursors)} cursor(s), {len(self.bindings)} "
                f"binding interval(s) ({len(self.fenced)} fenced), {len(self.identities)} "
                f"identit{'y' if len(self.identities) == 1 else 'ies'}, "
                f"{len(self.operators)} operator(s), {len(self.reserved)} send-only "
                f"name(s) kept as unowned reserved names")

    def lines(self, target: Path, *, dry_run: bool = False) -> list[str]:
        """The move's report, one line each."""
        out = [f"{'would move' if dry_run else 'moved'} comms 0.2's files into {target}: "
               f"{self.counts()}"]
        if self.unkinded:
            out.append("rooms from before comms had one room, carried with no kind: "
                       + ", ".join(self.unkinded))
        out.extend(self.skipped)
        out.extend(FENCED_LINE.format(name=n) for n in self.fenced)
        if dry_run:
            out.extend(f"would rename {p} to {moved_name(p)}"
                       for p in self.files.values())
        else:
            out.extend(f"renamed {a} to {b}" for a, b in self.renamed)
        return out


def _stamp(t: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def _record_time(value):
    """A 0.2 time in the record's spelling. The directory and the cursor files wrote
    `isoformat`; a value that won't parse is carried as it is."""
    if not isinstance(value, str) or value.endswith("Z"):
        return value
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return value
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return _stamp(parsed.timestamp())


def _epoch(value: str) -> float:
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc).timestamp()
    except (TypeError, ValueError):
        return 0.0


def _fingerprint(role: str, p: Path) -> dict:
    if role == "cursors":
        entries = {}
        for f in sorted(p.rglob("*")):
            if f.is_file():
                st = f.stat()
                entries[str(f.relative_to(p))] = [st.st_size, st.st_mtime_ns]
        return {"path": str(p), "files": entries}
    st = p.stat()
    return {"path": str(p), "size": st.st_size, "mtime_ns": st.st_mtime_ns}


def _same(role: str, p: Path, kept: dict) -> bool:
    now = _fingerprint(role, p)
    if role == "cursors":
        return now["files"] == kept.get("files")
    return now["size"] == kept.get("size") and now["mtime_ns"] == kept.get("mtime_ns")


# -- the checks before anything is read --------------------------------------------------

def daemon_answers(env=None) -> Path | None:
    """The 0.2 daemon's socket path when something answers there, else None. A connect
    and nothing more: nothing is sent, and the path is never bound, removed or renamed."""
    path = _paths.old_daemon_socket(env)
    family = getattr(socket, "AF_UNIX", None)
    if family is None or not path.exists():
        return None
    s = socket.socket(family, socket.SOCK_STREAM)
    s.settimeout(1.0)
    try:
        s.connect(str(path))
    except OSError:
        return None
    finally:
        s.close()
    return path


def _refuse_live_daemon(env) -> None:
    live = daemon_answers(env)
    if live is not None:
        raise DaemonLive(live, (
            "a comms 0.2 daemon still answers here, so its files are still in use; "
            "nothing was moved. Stop it (the upgrade's unit step does) and the files "
            "move at the next daemon start"))


def _open_0_2(role: str, p: Path) -> sqlite3.Connection:
    """Open one 0.2 database as it is, and check it. Raises `StoreRefused`."""
    if p.stat().st_size == 0:
        raise StoreRefused(p, "is empty, not a comms 0.2 database; nothing was moved")
    try:
        conn = _store.connect(p, create=False, private=False)
    except (sqlite3.Error, _store.StoreError) as e:
        raise StoreRefused(p, f"can't be opened ({e}); nothing was moved") from None
    try:
        try:
            check = [r[0] for r in conn.execute("PRAGMA quick_check").fetchall()]
            version = conn.execute("PRAGMA user_version").fetchone()[0]
        except sqlite3.Error as e:
            raise StoreRefused(p, f"can't be read ({e}); nothing was moved") from None
        if check != ["ok"]:
            raise StoreRefused(p, "fails SQLite's quick_check ("
                               + "; ".join(str(c) for c in check[:3])
                               + "); nothing was moved")
        if version not in KNOWN_VERSIONS:
            raise StoreRefused(p, f"is at user_version {version}, which comms 0.2 never "
                                  "wrote; nothing was moved")
        for table, columns in REQUIRED[role].items():
            have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
            if not have:
                raise StoreRefused(p, f"has no {table} table, which comms 0.2 always "
                                      "has; nothing was moved")
            for column in columns:
                if column not in have and (role, table, column) not in OPTIONAL:
                    raise StoreRefused(p, f"has no {table}.{column} column, which comms "
                                          "0.2 always has; nothing was moved")
    except BaseException:
        conn.close()
        raise
    return conn


def _author_kind(row: dict) -> dict:
    """A 0.2 message row with the operator's kind renamed: 0.2 wrote `user`, comms.db
    writes `operator`. Every other kind is carried as 0.2 wrote it."""
    if row["author_kind"] == AUTHOR_OPERATOR_OLD:
        row = dict(row, author_kind=AUTHOR_OPERATOR)
    return row


def _select(conn, role: str, table: str, order: str = "") -> list[dict]:
    have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    parts = []
    for column in REQUIRED[role][table]:
        if column in have:
            parts.append(column)
        else:
            default = OPTIONAL[(role, table, column)]
            parts.append(f"'{default}' AS {column}")
    rows = conn.execute(f"SELECT {', '.join(parts)} FROM {table} {order}").fetchall()
    names = REQUIRED[role][table]
    return [dict(zip(names, row)) for row in rows]


# -- reading ------------------------------------------------------------------------------

def read(env=None, *, now: float | None = None, prepare: bool = False) -> Plan:
    """Read comms 0.2's files and work out everything the move writes. Raises
    `StoreRefused` (`DaemonLive` for a live 0.2 daemon) on anything the move refuses.

    With `prepare`, each database's write-ahead log is first checkpointed into its main
    file, so the fingerprint taken next is the one the file keeps after the move. Without
    it nothing is written, and the fingerprint is left empty.
    """
    now = time.time() if now is None else now
    found = files(env)
    _refuse_live_daemon(env)
    plan = Plan(files=found)
    conns = {}
    try:
        for role in ("record", "directory", "bindings"):
            if role in found:
                conns[role] = _open_0_2(role, found[role])
        if prepare:
            for role, conn in conns.items():
                try:
                    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
                except sqlite3.Error as e:
                    raise StoreRefused(found[role], f"can't be checkpointed ({e}); "
                                                    "nothing was moved") from None
            plan.fingerprint = {role: _fingerprint(role, p) for role, p in found.items()}
        for conn in conns.values():
            conn.execute("BEGIN")
        _read_directory(plan, conns.get("directory"), found.get("directory"))
        _read_record(plan, conns.get("record"), found.get("record"))
        _read_bindings(plan, conns.get("bindings"), now)
        _read_cursors(plan, found.get("cursors"))
        _make_rows(plan, now, env)
    finally:
        for conn in conns.values():
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            conn.close()
    return plan


def _read_directory(plan: Plan, conn, path) -> None:
    if conn is None:
        return
    for row in _select(conn, "directory", "agents", "ORDER BY rowid"):
        plan.directory_rows[(row["name"], row["scope"])] = row
        if row["kind"] == "send-only":
            # A send-only name is a reserved name now, never a standing row.
            plan.reserved.append(qualify(row["name"], row["scope"]))
            continue
        plan.identities.append((row["name"], row["scope"], row["status"], row["kind"],
                                _record_time(row["created_at"])))
    plan.operators = [(r["user_id"], r["name"], _record_time(r["created_at"]))
                      for r in _select(conn, "directory", "operators", "ORDER BY rowid")]
    plan.directory_rooms = {r["channel_id"]: r for r in _select(conn, "directory", "rooms",
                                                       "ORDER BY rowid")}
    for r in _select(conn, "directory", "meta"):
        plan.meta[r["key"]] = r["value"]


def _read_record(plan: Plan, conn, path) -> None:
    directory_rooms = plan.directory_rooms
    record_meta = {}
    channels = []
    if conn is not None:
        channels = _select(conn, "record", "channels", "ORDER BY rowid")
        plan.messages = [tuple(_author_kind(r).values()) for r in
                         _select(conn, "record", "messages", "ORDER BY id")]
        for r in _select(conn, "record", "meta"):
            record_meta[r["key"]] = r["value"]
        plan.principals = [(r["principal_id"], r["name"])
                           for r in _select(conn, "record", "principals")]
        plan.denied = [(r["principal_id"], r["channel_id"])
                       for r in _select(conn, "record", "denied")]
        plan.queued = [(r["name"], int(r["message_id"]))
                        for r in _select(conn, "record", "queue",
                                         "ORDER BY name, message_id")]
        if plan.messages and (META_AUTHOR_STAMPS not in record_meta
                              or META_QUEUE_MIGRATED not in record_meta):
            missing = [k for k in (META_AUTHOR_STAMPS, META_QUEUE_MIGRATED)
                       if k not in record_meta]
            raise StoreRefused(path, (
                f"has messages but no {' or '.join(missing)} marker, so it is a record "
                "from before comms 0.2 that 0.2 never converted; nothing was moved. "
                "Start comms 0.2 once on it, or move its data by hand"))
        next_id = record_meta.get(META_NEXT_ID)
        if next_id is None or not str(next_id).isdigit():
            raise StoreRefused(path, f"has no usable {META_NEXT_ID} ({next_id!r}); "
                                     "nothing was moved")
    else:
        plan.queued = []
    for key, value in record_meta.items():
        if key in plan.meta and plan.meta[key] != value:
            raise StoreRefused(path, (
                f"and the directory both keep meta {key!r}, with different values; "
                "nothing was moved"))
        plan.meta[key] = value
    # Rooms: the record's channels, each with the directory's kind where it names one,
    # then the directory's rooms the record never made.
    by_name = {}
    for ch in channels:
        named = directory_rooms.get(ch["channel_id"])
        kind = named["kind"] if named is not None else None
        if kind is not None and kind not in ROOM_KINDS:
            plan.unkinded.append(ch["name"])
            kind = None
        plan.rooms.append((ch["channel_id"], ch["name"], kind, ch["created_at"]))
        by_name[ch["name"]] = ch["channel_id"]
    for room_id, r in directory_rooms.items():
        if any(room[0] == room_id for room in plan.rooms):
            continue
        if r["name"] in by_name:
            raise StoreRefused(path or plan.files.get("directory"), (
                f"names room {r['name']} {by_name[r['name']]}, and the directory names "
                f"it {room_id}; nothing was moved"))
        kind = r["kind"] if r["kind"] in ROOM_KINDS else None
        if kind is None:
            plan.unkinded.append(r["name"])
        plan.rooms.append((room_id, r["name"], kind, _record_time(r["created_at"])))
        by_name[r["name"]] = room_id


def _read_bindings(plan: Plan, conn, now: float) -> None:
    if conn is None:
        return
    fenced = []
    for r in _select(conn, "bindings", "bindings", "ORDER BY id"):
        released, was_fenced = r["released_at"], int(r["fenced"] or 0)
        if released is None:
            released, was_fenced = _stamp(now), 1
            fenced.append(r["qualified"])
        plan.bindings.append((r["id"], r["qualified"], r["name"], r["scope"], r["tier"],
                              r["adapter"], r["bound_at"], released, was_fenced))
    plan.fenced = sorted(set(fenced))


def _read_cursors(plan: Plan, folder) -> None:
    if folder is None:
        return
    seen = {}
    for p in sorted(folder.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(folder)
        if rel.name.startswith("."):
            plan.skipped.append(f"skipped {p}: a cursor writer's temporary file")
            continue
        if len(rel.parts) != 2 or rel.parts[0] != CURSOR_BACKEND or p.suffix != ".json":
            plan.skipped.append(f"skipped {p}: not a cursor of the local record (another "
                                "backend's, or test data)")
            continue
        tag = p.stem
        name, scope = split_tag(tag)
        if not NAME_RE.match(name or "") or (scope is not None
                                              and not NAME_RE.match(scope)):
            raise StoreRefused(p, f"names {tag!r}, which isn't a comms identity; nothing "
                                  "was moved")
        try:
            record = json.loads(p.read_text())
            cursor = record["cursor"]
            if isinstance(cursor, bool) or not str(cursor).isdigit():
                raise ValueError(f"cursor {cursor!r} is not a message id")
        except (OSError, UnicodeError, ValueError, KeyError, TypeError) as e:
            raise StoreRefused(p, f"doesn't read as a cursor ({e}); nothing was moved") \
                from None
        q = identity_of(tag)
        if is_auto_name(name):
            plan.skipped.append(f"skipped {p}: {q} is an agent-N name, which ends with "
                                "its session")
            continue
        if q in seen:
            raise StoreRefused(p, f"is a second cursor for {q} (beside {seen[q]}); "
                                  "nothing was moved")
        seen[q] = p
        updated = _record_time(record.get("updated_at")) if isinstance(record, dict) \
            else None
        plan.cursors.append((q, int(cursor), updated or _stamp(p.stat().st_mtime)))
        plan.cursor_of[q] = int(cursor)


def _recipient(plan: Plan, tag: str) -> str | None:
    """The name a line-zero tag routes to, or None for one nothing is queued for: an
    agent-N name, a retired identity, a send-only name."""
    name, scope = split_tag(tag)
    if is_auto_name(name):
        return None
    scope = scope or GLOBAL
    row = plan.directory_rows.get((name, scope))
    if row is not None and (row["status"] == "retired" or row["kind"] == "send-only"):
        return None
    return qualify(name, scope)


def _make_rows(plan: Plan, now: float, env) -> None:
    """The queue: 0.2's rows, then a row for each message 0.2 would have replayed."""
    from .. import config

    names_room = next((r[0] for r in plan.rooms if r[1] == NAMES), None)
    by_id = {int(m[0]): m for m in plan.messages}
    lines = {}

    def line(mid):
        if mid not in lines:
            m = by_id.get(mid)
            lines[mid] = machine_line.parse(m[5]) if m is not None else None
        return lines[mid]

    def held(mid, q):
        ml = line(mid)
        return int(bool(ml is not None and ml.held and q != OPERATOR))

    def queued_at(mid):
        m = by_id.get(mid)
        return m[6] if m is not None else _stamp(now)

    rows = set()
    for q, mid in plan.queued:
        if is_auto_name(split_tag(q)[0]):
            plan.skipped.append(f"skipped the queue row of {q} for message {mid}: an "
                                "agent-N name's mail ends with its session")
            continue
        rows.add((q, mid))
        plan.queue.append((q, mid, queued_at(mid), held(mid, q)))
    if names_room is None:
        return
    mine: dict[str, list[int]] = {}
    for m in plan.messages:
        mid = int(m[0])
        if m[1] != names_room:
            continue
        ml = line(mid)
        if ml is None:
            continue
        for tag in dict.fromkeys(ml.recipients):
            q = _recipient(plan, tag)
            if q is not None and mid not in mine.setdefault(q, []):
                mine[q].append(mid)
    replay = config.load(env=env).replay
    for q in sorted(mine):
        ids = mine[q]
        cursor = plan.cursor_of.get(q)
        if cursor is not None:
            ids = [i for i in ids if i > cursor]
        else:
            ids = [i for i in ids[-replay.messages:]
                   if now - _epoch(by_id[i][6]) <= replay.seconds]
        for mid in ids:
            if (q, mid) in rows:
                continue
            rows.add((q, mid))
            plan.queue.append((q, mid, queued_at(mid), held(mid, q)))
            plan.made += 1


# -- writing ------------------------------------------------------------------------------

def _build(plan: Plan, new: Path, now: float) -> None:
    """Write `plan` into the fresh store `new`, in one transaction, then checkpoint and
    close it."""
    conn = _store.connect(new)
    try:
        _store.migrate(conn, STEPS)
        with _store.transaction(conn):
            meta = dict(plan.meta)
            meta[META_MIGRATED_FROM] = FROM
            meta[META_MIGRATED_AT] = _stamp(now)
            meta[META_MIGRATED_FILES] = json.dumps(plan.fingerprint, sort_keys=True)
            meta[META_MIGRATED_FENCED] = json.dumps(plan.fenced)
            conn.executemany("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
                             sorted(meta.items()))
            conn.executemany("INSERT INTO rooms(room_id, name, kind, created_at) "
                             "VALUES (?, ?, ?, ?)", plan.rooms)
            conn.executemany("INSERT INTO messages(id, room_id, author_id, author_kind, "
                             "author_display, content, timestamp) "
                             "VALUES (?, ?, ?, ?, ?, ?, ?)", plan.messages)
            conn.executemany("INSERT INTO queue(name, message_id, queued_at, held, state) "
                             "VALUES (?, ?, ?, ?, 'waiting')", plan.queue)
            conn.executemany("INSERT INTO cursors(name, message_id, updated_at) "
                             "VALUES (?, ?, ?)", plan.cursors)
            conn.executemany("INSERT INTO bindings(id, qualified, name, scope, tier, "
                             "adapter, bound_at, released_at, fenced, kind) "
                             "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'session')", plan.bindings)
            conn.executemany("INSERT INTO identities(name, scope, status, kind, created_at) "
                             "VALUES (?, ?, ?, ?, ?)", plan.identities)
            conn.executemany("INSERT INTO operators(user_id, name, created_at) "
                             "VALUES (?, ?, ?)", plan.operators)
            conn.executemany("INSERT INTO reserved(name, state, note) "
                             "VALUES (?, 'unowned', ?)",
                             [(q, SEND_ONLY_NOTE) for q in plan.reserved])
            conn.executemany("INSERT INTO principals(principal_id, name) VALUES (?, ?)",
                             plan.principals)
            conn.executemany("INSERT INTO denied(principal_id, channel_id) VALUES (?, ?)",
                             plan.denied)
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
    finally:
        conn.close()


def _discard(new: Path) -> None:
    for p in (new, Path(f"{new}-wal"), Path(f"{new}-shm"), Path(f"{new}-journal")):
        p.unlink(missing_ok=True)


def move(target, env=None, *, now: float | None = None) -> Plan:
    """Move comms 0.2's files into a new store at `target`. Run under the opener's lock,
    with no store at `target`. Returns the plan, its renames filled in. Raises
    `StoreRefused` with nothing written."""
    target = Path(target)
    now = time.time() if now is None else now
    plan = read(env, now=now, prepare=True)
    new = target.with_name(target.name + NEW_SUFFIX)
    _discard(new)                      # a run that died before its rename
    try:
        _build(plan, new, now)
    except sqlite3.IntegrityError as e:
        _discard(new)
        raise StoreRefused(next(iter(plan.files.values())), (
            f"holds a row comms.db can't take ({e}); nothing was moved")) from None
    except BaseException:
        _discard(new)
        raise
    os.replace(new, target)
    _fsync_dir(target.parent)
    plan.renamed = _rename(plan.files, plan.fingerprint, target)
    return plan


def finish(target, env=None) -> list[tuple[Path, Path]]:
    """Finish a move that stopped after its commit: rename the 0.2 files still under
    their 0.2 names, when each still matches the fingerprint the store keeps. Raises
    `StoreRefused` with nothing renamed when any doesn't."""
    target = Path(target)
    _refuse_live_daemon(env)
    conn = _store.connect(target, create=False)
    try:
        row = conn.execute("SELECT value FROM meta WHERE key = ?",
                           (META_MIGRATED_FILES,)).fetchone()
    finally:
        conn.close()
    try:
        kept = json.loads(row[0]) if row else {}
    except ValueError:
        kept = {}
    return _rename(files(env), kept, target)


def _rename(found: dict, kept: dict, target: Path) -> list[tuple[Path, Path]]:
    """Rename each of `found` to its moved name, after checking every one against
    `kept`. Nothing is renamed when any check fails."""
    changed = []
    for role, p in found.items():
        kept_print = kept.get(role)
        if kept_print is None:
            if role == "cursors":
                continue        # an earlier build's cursor writers made it; left alone
            changed.append(f"{p} (not there when the move ran)")
        elif not _same(role, p, kept_print) or _wal_has_frames(p):
            changed.append(str(p))
        elif moved_name(p).exists():
            changed.append(f"{p} ({moved_name(p)} is there already)")
    if changed:
        raise StoreRefused(target, (
            "was moved from comms 0.2, but comms' 0.2 files changed after they were "
            "moved: " + ", ".join(changed) + "; nothing was renamed. Compare them with "
            "the store, then rename or remove them by hand"))
    done = []
    for role in ROLES:
        p = found.get(role)
        if p is None or (role == "cursors" and role not in kept):
            continue
        dest = moved_name(p)
        os.replace(p, dest)
        for side in ("-wal", "-shm"):
            if Path(f"{p}{side}").exists():
                os.replace(Path(f"{p}{side}"), Path(f"{dest}{side}"))
        _fsync_dir(p.parent)
        done.append((p, dest))
    return done


def _wal_has_frames(p: Path) -> bool:
    wal = Path(f"{p}-wal")
    try:
        return wal.stat().st_size > 0
    except OSError:
        return False


def _fsync_dir(directory: Path) -> None:
    if os.name == "nt":
        return
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


# -- what a reader says --------------------------------------------------------------------

def dry_run(target=None, env=None, *, now: float | None = None) -> list[str]:
    """What `moxi comms migrate` would do now, without writing anything. Raises
    `StoreRefused` with the refusal the opener would give."""
    from . import open as _open

    target = Path(target) if target is not None else _open.path(env)
    found = _open.zero_two_files(env, target)
    if not found:
        return [f"nothing to move: no comms 0.2 files here; the store is {target}"]
    try:
        state = state_of(target)
    except sqlite3.Error as e:
        raise StoreRefused(target, f"can't be read ({e}); nothing was written") from None
    if state == MARKED:
        _refuse_live_daemon(env)
        conn = _store.connect(target, create=False)
        try:
            row = conn.execute("SELECT value FROM meta WHERE key = ?",
                               (META_MIGRATED_FILES,)).fetchone()
        finally:
            conn.close()
        kept = json.loads(row[0]) if row else {}
        present = files(env)
        changed = [str(p) for role, p in present.items()
                   if (role in kept and (not _same(role, p, kept[role])
                                         or _wal_has_frames(p)))
                   or (role not in kept and role != "cursors")]
        if changed:
            raise StoreRefused(target, (
                "was moved from comms 0.2, but comms' 0.2 files changed after they were "
                "moved: " + ", ".join(changed) + "; nothing would be renamed"))
        return [f"{target} was moved from comms 0.2; the move would finish by renaming:"] \
            + [f"would rename {p} to {moved_name(p)}" for role, p in present.items()
               if role in kept]
    if state != _open.ABSENT:
        raise StoreRefused(target, (
            "it exists beside comms 0.2's files "
            f"({', '.join(str(p) for p in found)}) and wasn't made by moving them; "
            "nothing was written"))
    return read(env, now=now).lines(target, dry_run=True)


def doctor_rows(env=None) -> list:
    """The doctor's rows about the move: a move that waits or would refuse, the renamed
    files left on disk, and each name fenced by the move while its mail waits."""
    from moxi.doctor import Row

    from . import open as _open
    from ..release import MODULE

    rows = []
    if _open.zero_two_files(env):
        try:
            dry_run(env=env)
        except StoreRefused as e:
            rows.append(Row(MODULE, "store", "fail", str(e),
                            "moxi comms migrate --dry-run"))
        except (sqlite3.Error, OSError, ValueError) as e:
            rows.append(Row(MODULE, "store", "fail",
                            f"comms' 0.2 files can't be read: {e}",
                            "moxi comms migrate --dry-run"))
        else:
            rows.append(Row(MODULE, "store", "warn",
                            "comms' 0.2 files aren't moved yet; they move at the "
                            "daemon's next start", "moxi comms migrate"))
    left = leftovers(env)
    if left:
        rows.append(Row(MODULE, "store", "warn",
                        "comms 0.2's moved files are still here: "
                        + ", ".join(str(p) for p in left),
                        "remove them once you no longer need them"))
    rows.extend(Row(MODULE, "store", "warn", line) for line in waiting_fenced(env))
    return rows


def waiting_fenced(env=None) -> list[str]:
    """`FENCED_LINE` for each name the move fenced that has waiting mail and no live
    binding. Empty when the store can't be read."""
    from . import open as _open

    try:
        conn = _open.open_read(env=env)
    except (StoreRefused, sqlite3.Error, _store.StoreError):
        return []
    try:
        row = conn.execute("SELECT value FROM meta WHERE key = ?",
                           (META_MIGRATED_FENCED,)).fetchone()
        names = json.loads(row[0]) if row else []
        out = []
        for name in names:
            waiting = conn.execute("SELECT 1 FROM queue WHERE name = ? AND state = "
                                   "'waiting' LIMIT 1", (name,)).fetchone()
            live = conn.execute("SELECT 1 FROM bindings WHERE qualified = ? AND "
                                "released_at IS NULL LIMIT 1", (name,)).fetchone()
            if waiting and not live:
                out.append(FENCED_LINE.format(name=name))
        return out
    except (sqlite3.Error, ValueError):
        return []
    finally:
        conn.close()


__all__ = ["FROM", "MOVED_SUFFIX", "NEW_SUFFIX", "META_MIGRATED_AT", "META_MIGRATED_FILES",
           "META_MIGRATED_FENCED", "FENCED_LINE", "DaemonLive", "Plan", "files",
           "leftovers", "moved_name", "daemon_answers", "read", "move", "finish",
           "dry_run", "doctor_rows", "waiting_fenced"]

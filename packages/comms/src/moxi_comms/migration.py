"""One-time conversion of records and removal of retired posting credentials.

The record conversion keeps the old attribution rule here as a frozen reader.  A
history command can therefore interpret an old row before the daemon has run, and the
daemon can apply exactly the same answer when it converts that row.  The conversion's
marker and all of its record edits commit in one transaction.

Credential-file removal is deliberately separate and idempotent.  It runs after every
conversion attempt, including starts that find the marker already committed, so a
process that stopped between the record commit and the file removals finishes the work
on its next start.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from . import config, tokens
from .record import Message
from .paths import bindings_db, state_dir, tokens_dir
from .scoping import GLOBAL, is_auto_name, qualify, split_tag

log = logging.getLogger("comms.migration")

_UNREADABLE = object()

META_AUTHOR_STAMPS = "author_stamps"
_RETIRED_FILE = re.compile(r"slot-[0-9]+\Z")
_RETIRED_KEY = re.compile(r"comms/slot-[0-9]+/[^/]+\Z")


class MigrationError(RuntimeError):
    """A conversion that cannot safely decide the old record's authors."""


@dataclass(frozen=True)
class Result:
    stamped: int = 0
    removed_keys: int = 0
    left_keys: int = 0
    removed_files: int = 0
    marker_written: bool = False


def _author(name: str, scope: str, tier: str, verified: bool):
    # Imported lazily so attribution can import this module without a cycle.
    from .attribution import Author, LABELED, VERIFIED

    return Author(name, scope, qualify(name, scope), tier,
                  VERIFIED if verified else LABELED)


def _unknown():
    from .attribution import unknown

    return unknown()


def _from_display(display: str):
    """The old display-derived fallback, copied here so it cannot drift."""
    from .attribution import TIER_CHOSEN, TIER_EPHEMERAL

    name, scope = split_tag(display or "")
    scope = scope or GLOBAL
    if not name:
        return _unknown()
    tier = TIER_EPHEMERAL if is_auto_name(name) else TIER_CHOSEN
    return _author(name, scope, tier, False)


def _has_table(conn, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
    ).fetchone() is not None


def _webhook_row(conn, webhook_id: str):
    if not _has_table(conn, "webhooks"):
        return None
    return conn.execute("SELECT * FROM webhooks WHERE webhook_id = ?",
                        (str(webhook_id),)).fetchone()


def _holder_at(conn, slot_id, timestamp: str | None):
    """The old exact-interval match, copied from the retired binding history reader."""
    if conn is None or slot_id is None or not timestamp:
        return None
    try:
        rows = conn.execute(
            "SELECT * FROM bindings WHERE slot_id = ? AND bound_at <= ? "
            "AND (released_at IS NULL OR released_at >= ?)",
            (int(slot_id), timestamp, timestamp)).fetchall()
    except (sqlite3.Error, TypeError, ValueError):
        return None
    return rows[0] if len(rows) == 1 else None


def _legacy_author(directory_conn, msg, history_conn=None):
    """Interpret one old webhook row by the rule the older reader used."""
    from .attribution import TIER_STANDING

    row = _webhook_row(directory_conn, msg.author_id)
    if row is None:
        return _unknown()
    keys = set(row.keys())
    name = row["agent_name"] if "agent_name" in keys else None
    if name is not None:
        scope = row["agent_scope"] or GLOBAL
        return _author(name, scope, TIER_STANDING, True)
    claimed = _from_display(msg.author_display)
    if claimed.tier == "unknown":
        return claimed
    held = _holder_at(history_conn,
                      row["slot_id"] if "slot_id" in keys else None,
                      getattr(msg, "timestamp", None))
    if held is not None and held["qualified"] == claimed.qualified:
        return _author(held["name"], held["scope"], held["tier"], True)
    return claimed


def _open_history(path: str | Path | None, *, strict: bool):
    target = Path(path) if path is not None else bindings_db()
    if not target.exists():
        return None
    try:
        conn = sqlite3.connect(f"file:{target}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        # Force SQLite to read the file now.  Merely constructing a connection can
        # succeed for a file that is not a database.
        conn.execute("SELECT name FROM sqlite_master LIMIT 1").fetchone()
        return conn
    except (sqlite3.Error, OSError) as exc:
        if strict:
            raise MigrationError(
                "moxi-comms-daemon: the binding history exists but cannot be opened; "
                "the old record was left unchanged. Repair or restore bindings.db, then "
                "start the daemon again.") from exc
        return None


def legacy_author(directory_conn, msg, *, history_path=None):
    """Read one old row before the conversion marker exists.

    A history read is best-effort, as the old reader was: an unavailable binding history means
    no corroborating evidence and therefore an unverified display, not a failed read.
    """
    history = _open_history(history_path, strict=False)
    try:
        return _legacy_author(directory_conn, msg, history)
    finally:
        if history is not None:
            history.close()


def stamp_record(record, directory_path, *, history_path=None) -> tuple[int, bool]:
    """Convert old webhook authors and commit the marker in one record transaction."""
    if record.meta_get(META_AUTHOR_STAMPS) is not None:
        return 0, False

    directory_conn = sqlite3.connect(f"file:{Path(directory_path)}?mode=ro", uri=True)
    directory_conn.row_factory = sqlite3.Row
    history_conn = None
    try:
        history_conn = _open_history(history_path, strict=True)
        changed = 0
        with record._write() as conn:
            marker = conn.execute("SELECT value FROM meta WHERE key = ?",
                                  (META_AUTHOR_STAMPS,)).fetchone()
            if marker is not None:
                return 0, False
            rows = conn.execute(
                "SELECT id, room_id, author_id, author_kind, author_display, content, "
                "timestamp FROM messages WHERE author_kind = 'webhook' ORDER BY id"
            ).fetchall()
            for row in rows:
                msg = Message(id=str(row[0]), channel_id=row[1], author_id=row[2],
                              author_kind=row[3], author_display=row[4], content=row[5],
                              timestamp=row[6])
                author = _legacy_author(directory_conn, msg, history_conn)
                if author.tier == "unknown":
                    conn.execute("UPDATE messages SET author_kind = 'unknown' WHERE id = ?",
                                 (row[0],))
                    changed += 1
                elif author.attribution == "verified":
                    conn.execute(
                        "UPDATE messages SET author_kind = 'identity', author_id = ?, "
                        "author_display = ? WHERE id = ?",
                        (author.qualified, author.qualified, row[0]))
                    changed += 1
            conn.execute("INSERT INTO meta(key, value) VALUES (?, ?)",
                         (META_AUTHOR_STAMPS, "1"))
        return changed, True
    finally:
        directory_conn.close()
        if history_conn is not None:
            history_conn.close()


def _entries(path: Path) -> dict | None:
    try:
        return tokens._entries(path.read_text())
    except (OSError, UnicodeError):
        return None


def _json_value(path: Path):
    try:
        return json.loads(path.read_text())
    except (OSError, UnicodeError, ValueError):
        return _UNREADABLE


def _standing_key(path: Path, key: str) -> bool:
    parts = key.split("/")
    return (len(parts) == 4 and parts[0] == tokens.KEY_PREFIX
            and parts[1] == path.parent.name and parts[2] == path.name
            and bool(parts[3]))


def _posting_count(path: Path, entries: dict | None) -> int:
    if entries is None:
        return 0
    return sum(1 for key in entries
               if _RETIRED_KEY.fullmatch(key) or _standing_key(path, key))


def _own_store(store) -> bool:
    env = store.env
    if env.get(tokens.STORE_ENV_VAR):
        return False
    return not config.load(env=env).secrets.store and store.root == tokens_dir()


def prune_store(store, *, keep_path=None, own_store: bool | None = None) -> Result:
    """Remove only the retired files from comms' own credential store."""
    own = _own_store(store) if own_store is None else own_store
    root = store.root
    keep = Path(keep_path) if keep_path is not None else state_dir() / "slot-keeps.json"
    if not own:
        left = 0
        if root.is_dir():
            for path in (p for p in root.rglob("*") if p.is_file()):
                entries = _entries(path)
                if entries is None:
                    log.info("credential cleanup kept %s: it is not a readable flat "
                             "credential map", path)
                left += _posting_count(path, entries)
        return Result(left_keys=left)

    removed_keys = removed_files = 0
    if keep.exists():
        if _json_value(keep) is _UNREADABLE:
            log.info("startup cleanup kept %s: it is not readable JSON", keep)
        else:
            keep.unlink()
            removed_files += 1
    if not root.is_dir():
        return Result(removed_files=removed_files)

    for path in sorted((p for p in root.iterdir() if p.is_file()), key=lambda p: p.name):
        if not _RETIRED_FILE.fullmatch(path.name):
            continue
        entries = _entries(path)
        if entries is None:
            log.info("credential cleanup kept %s: it is not a readable flat "
                     "credential map", path)
            continue
        removed_keys += _posting_count(path, entries)
        path.unlink()
        removed_files += 1

    for scope in sorted((p for p in root.iterdir() if p.is_dir()), key=lambda p: p.name):
        for path in sorted((p for p in scope.iterdir() if p.is_file()), key=lambda p: p.name):
            entries = _entries(path)
            if entries is None:
                log.info("credential cleanup kept %s: it is not a readable flat "
                         "credential map", path)
                continue
            if any(not _standing_key(path, key) for key in entries):
                if entries:
                    log.info("credential cleanup kept %s: it contains a key that is not "
                             "one of that retired holder's posting keys", path)
                continue
            removed_keys += len(entries)
            path.unlink()
            removed_files += 1
        try:
            scope.rmdir()
        except OSError:
            pass
    return Result(removed_keys=removed_keys, removed_files=removed_files)


def startup(store, *, keep_path=None, own_store: bool | None = None):
    """Build the daemon's conversion step for the local backend."""
    def run(record, directory_path, history_path=None) -> Result:
        stamped, marker_written = stamp_record(
            record, directory_path, history_path=history_path)
        pruned = prune_store(store, keep_path=keep_path, own_store=own_store)
        result = Result(stamped=stamped, removed_keys=pruned.removed_keys,
                        left_keys=pruned.left_keys, removed_files=pruned.removed_files,
                        marker_written=marker_written)
        if marker_written or pruned.removed_files:
            if pruned.left_keys:
                log.info("author conversion: stamped %d old message(s); left %d retired "
                         "posting key(s) in the externally configured store",
                         stamped, pruned.left_keys)
            else:
                log.info("author conversion: stamped %d old message(s); removed %d retired "
                         "posting key(s) from comms' store", stamped, pruned.removed_keys)
        return result

    return run


__all__ = ["META_AUTHOR_STAMPS", "MigrationError", "Result", "legacy_author",
           "stamp_record", "prune_store", "startup"]

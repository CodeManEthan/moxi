"""An older comms' record and identities files, read in place and never written.

A box that ran an older comms before comms.db keeps that comms' files where it left
them: its record (`record.db`: `channels`, `messages` and `meta`) and its identities
(`directory.db`: `agents`). config.toml's `[archive] record` and `[archive] identities`
name them. comms opens each with SQLite's `immutable` flag, read-only, so no lock, journal
or shared-memory file is made beside it and nothing in it changes.

What the archive answers:

* `history`, the verb and a session's tool, lists the archive's `#names` messages before
  comms.db's, as one record (`timeline`);
* a message id comms.db skipped (below) is the archive's, so `reply` and every other
  read of one message by id find it there;
* every identity the archive marks `retired` stays retired: comms refuses it at bind and
  as a recipient, as it refuses one comms.db retired (`directory.retired`).

**The ids.** The older comms and comms.db count message ids from the same base, so the
same id can name a message in each. At its start the daemon raises comms.db's counter to
the archive's last id once (`skip_ids`) and records the ids it skipped in comms.db's
`meta` (`archive_skipped`, `"<from> <to>"`: every id above `from` up to `to`). comms.db
never writes an id in that range, so an id there is the archive's, and an id outside it
is comms.db's alone. An archive id comms.db had already written before the skip is
reached through `history`, not by id.

The archive is read once per process from the config file, as the daemon reads `[archive]`
at its start (`current`). A file that is missing or can't be read is logged once and
answers nothing.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable
from urllib.parse import quote

from .record import Message

log = logging.getLogger("comms.archive")

#: comms.db's `meta` key for the ids its counter skipped: `"<from> <to>"`.
META_SKIPPED = "archive_skipped"
#: The older record's room whose messages `history` lists.
NAMES_ROOM = "#names"


class ArchiveError(Exception):
    """An archive file that is missing or can't be read."""


def connect(path: Path) -> sqlite3.Connection:
    """`path` opened read-only and immutable: SQLite takes no lock and writes no file
    beside it. Raises `ArchiveError` when it is not there or is not a database."""
    if not path.is_file():
        raise ArchiveError(f"{path} is not a file")
    try:
        conn = sqlite3.connect(f"file:{quote(str(path))}?mode=ro&immutable=1", uri=True,
                               check_same_thread=False)
        conn.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchall()
    except sqlite3.Error as exc:
        raise ArchiveError(f"{path} can't be read: {exc}") from None
    return conn


_COLUMNS = "id, channel_id, author_id, author_kind, author_display, content, timestamp"


def _message(row) -> Message:
    return Message(id=str(row[0]), channel_id=row[1], author_id=row[2],
                   author_kind=row[3], author_display=row[4], content=row[5],
                   timestamp=row[6])


@dataclass
class Archive:
    """The older files config.toml names; either may be None."""

    record: Path | None = None
    identities: Path | None = None
    _conns: dict = field(default_factory=dict, repr=False)
    _retired: frozenset | None = field(default=None, repr=False)

    def _conn(self, role: str) -> sqlite3.Connection:
        if role not in self._conns:
            path = getattr(self, role)
            if path is None:
                raise ArchiveError(f"no archive {role} file is named")
            self._conns[role] = connect(path)
        return self._conns[role]

    def messages(self, limit: int | None = None) -> list[Message]:
        """The record's `#names` messages, ascending by id: the newest `limit`, or all."""
        sql = (f"SELECT {_COLUMNS} FROM messages WHERE channel_id = "
               "(SELECT channel_id FROM channels WHERE name = ?)")
        try:
            if limit is None:
                rows = self._conn("record").execute(sql + " ORDER BY id",
                                                    (NAMES_ROOM,)).fetchall()
            else:
                rows = self._conn("record").execute(sql + " ORDER BY id DESC LIMIT ?",
                                                    (NAMES_ROOM, int(limit))).fetchall()
                rows.reverse()
        except sqlite3.Error as exc:
            raise ArchiveError(f"{self.record} can't be read: {exc}") from None
        return [_message(r) for r in rows]

    def get_message(self, message_id) -> Message | None:
        try:
            row = self._conn("record").execute(
                f"SELECT {_COLUMNS} FROM messages WHERE id = ?", (int(message_id),)
            ).fetchone()
        except sqlite3.Error as exc:
            raise ArchiveError(f"{self.record} can't be read: {exc}") from None
        return _message(row) if row else None

    def last_id(self) -> int | None:
        """The last id the older record's counter gave out (its `meta` `next_id`)."""
        try:
            row = self._conn("record").execute(
                "SELECT value FROM meta WHERE key = 'next_id'").fetchone()
        except sqlite3.Error as exc:
            raise ArchiveError(f"{self.record} can't be read: {exc}") from None
        return int(row[0]) if row and str(row[0]).isdigit() else None

    def retired(self) -> frozenset:
        """Every `(name, scope)` the identities file marks retired."""
        if self._retired is None:
            try:
                rows = self._conn("identities").execute(
                    "SELECT name, scope FROM agents WHERE status = 'retired'").fetchall()
            except sqlite3.Error as exc:
                raise ArchiveError(f"{self.identities} can't be read: {exc}") from None
            self._retired = frozenset((r[0], r[1]) for r in rows)
        return self._retired

    def close(self) -> None:
        for conn in self._conns.values():
            conn.close()
        self._conns.clear()


def from_config(cfg) -> Archive | None:
    """The archive `cfg.archive` names, or None when it names neither file."""
    record, identities = cfg.archive.record, cfg.archive.identities
    if not record and not identities:
        return None
    return Archive(Path(record).expanduser() if record else None,
                   Path(identities).expanduser() if identities else None)


_current: dict = {}
_warned: set = set()


def current() -> Archive | None:
    """This process's archive, from the config file as it stands at the first ask after
    the file last changed; None when it names none, or when the file does not load."""
    from . import config

    path = config.config_path()
    try:
        stamp = path.stat()
        key = (str(path), stamp.st_size, stamp.st_mtime_ns, stamp.st_ino)
    except OSError:
        key = (str(path), None)
    if key not in _current:
        for old in _current.values():
            if old is not None:
                old.close()
        _current.clear()
        try:
            _current[key] = from_config(config.load())
        except config.ConfigError:
            _current[key] = None
    return _current[key]


def _warn(exc: ArchiveError) -> None:
    text = str(exc)
    if text not in _warned:
        _warned.add(text)
        log.warning("the archive is not read: %s", text)


def retired(name: str, scope: str) -> bool:
    """Is `(name, scope)` retired in the archive's identities file? False when there is
    none or it can't be read."""
    found = current()
    if found is None or found.identities is None:
        return False
    try:
        return (name, scope) in found.retired()
    except ArchiveError as exc:
        _warn(exc)
        return False


def messages(limit: int | None = None) -> list[Message]:
    """The archive's `#names` messages (`Archive.messages`); none when there is no record
    or it can't be read."""
    found = current()
    if found is None or found.record is None:
        return []
    try:
        return found.messages(limit)
    except ArchiveError as exc:
        _warn(exc)
        return []


def timeline(record, room_id, limit: int) -> tuple[list[Message], Callable[[Message], bool]]:
    """The newest `limit` `#names` messages of the one record `history` reads: the
    archive's first, then comms.db's (`record.history` of `room_id`), ascending. Also a
    test for whether one of them is the archive's, by the object, since an archive id can
    be one comms.db also used."""
    msgs = record.history(room_id, limit=limit) if room_id is not None else []
    older = messages(limit) if len(msgs) < limit else []
    from_archive = {id(m) for m in older}
    return (older + msgs)[-limit:], lambda m: id(m) in from_archive


def skipped(conn) -> tuple[int, int] | None:
    """The ids comms.db's counter skipped for the archive, `(from, to)`, from its `meta`
    over `conn`; None when it skipped none."""
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (META_SKIPPED,)).fetchone()
    if not row:
        return None
    low, _, high = str(row[0]).partition(" ")
    return (int(low), int(high)) if low.isdigit() and high.isdigit() else None


def get_message(conn, message_id) -> Message | None:
    """The archive's message `message_id`, when the id is one comms.db skipped (`skipped`
    over `conn`, comms.db's connection); None otherwise."""
    found = current()
    if found is None or found.record is None:
        return None
    window = skipped(conn)
    try:
        key = int(message_id)
    except (TypeError, ValueError):
        return None
    if window is None or not window[0] < key <= window[1]:
        return None
    try:
        return found.get_message(key)
    except ArchiveError as exc:
        _warn(exc)
        return None


def skip_ids(record) -> tuple[int, int] | None:
    """Raise comms.db's message counter past the archive's last id, once, and record the
    ids skipped; the daemon calls it at its start. Returns the range skipped, or None when
    there is no archive record, it can't be read, or the counter is already past it."""
    found = current()
    if found is None or found.record is None or not hasattr(record, "skip_ids_to"):
        return None
    try:
        last = found.last_id()
    except ArchiveError as exc:
        _warn(exc)
        return None
    if last is None:
        return None
    window = record.skip_ids_to(last, meta_key=META_SKIPPED)
    if window is not None:
        log.info("message ids %d..%d are the archive's; comms.db's counter moved past them",
                 window[0] + 1, window[1])
    return window


__all__ = ["Archive", "ArchiveError", "META_SKIPPED", "NAMES_ROOM", "connect", "current",
           "from_config", "get_message", "messages", "retired", "skip_ids", "skipped", "timeline"]

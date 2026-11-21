"""`moxi-memory-ingest`: the ingest child, the one process that writes the index.

    moxi-memory-ingest --turn HARNESS SESSION   read one session since its cursor
    moxi-memory-ingest --gone HARNESS SESSION   read one session to its end, then walk
    moxi-memory-ingest --walk                   a catch-up walk
    moxi-memory-ingest --walk --asked           a catch-up walk someone asked for
    moxi-memory-ingest --all                    read every source from its start
    moxi-memory-ingest --rebuild                build a new generation from every source
    moxi-memory-ingest --relabel                the rebuild, deriving labels again
    moxi-memory-ingest --waiting                run the request waiting, if one still is

One child writes at a time, under the writer lock, and a child that finds it held exits
at once. Every read goes through `moxi_sessions.api`, and every row through
`moxi_memory.rows`.

**Nothing asked for is lost to a held lock.** A turn's, a `gone`'s or a timer's walk
that finds the lock held leaves nothing behind: a later walk reads every source that grew.
Work someone asked for (`--walk --asked`, `--all`, `--rebuild`, `--relabel`) is a request
(`moxi_memory.state`):

1. The child records its request in the state store, then tries the writer lock once.
   A request already waiting takes it in, keeping the higher kind and the earlier time.
2. The child that takes the lock to run a request reads and clears the waiting one in
   one transaction and runs what it read; when nothing waits (another child ran it), it
   does nothing. A request written after that take keeps waiting.
3. Every child that held the writer lock reads the store after it frees the lock, also
   when it stopped on an error, and when a request waits starts a `--waiting` child to
   run it. A rebuild starts that in place of its closing walk.
4. A `--waiting` child records nothing: when it finds the lock held it exits, and the
   request stays for the holder to start on its way out.

A run killed after its take is not run again, since its request was cleared at the take;
a killed rebuild is recorded for the doctor by the next child's tidy.

**Cursors.** Each source's cursor lives in the index's `sources` table beside the rows it
produced, written in the same transaction. A cursor sessions can't trust restarts the
read, and the key's replace rule makes that harmless: a row whose key is stored is
replaced only when its text differs.

**Which sources.** The main sessions of each harness in config `harnesses`; subagents only
with `index.include_subagents` on, their rows under their main session's id. A Codex thread whose
import marks say it was imported (it is listed in Codex's imports file, or its turns
carry the import prefix) is skipped: the Claude Code transcript is the original. A
compressed Codex rollout, which sessions doesn't read, is recorded in `sources` and counted
by `not_read`.

**The walk** lists every folder of each harness, its live root then its archive folders,
through sessions' `corpus`, each from that folder's last walk, and reads each source whose
reuse keys changed. A folder answered the run only when the run listed it, nothing in it
failed at any depth (a listing's `unreadable`, a read that raised, a `find` probe that
couldn't read it), and it still answered when tested again just before the run's decision
(`archive_answers(..., fresh=True)` for an archive folder, the top level listed again for
a live root). A folder whose top level lists nothing didn't answer while memory read
sources from under it, or once a listing showed it holding an entry, in any run, answered
or not. Each folder's answer goes into the index's `meta` (`moxi_memory.roots`).
A climb from a subagent to its main session asks only the folders that answered the run's
start, so it waits on no archive folder. A turn's or `gone`'s read of one session asks
the live root only. The first full read finishes once every live root has been listed
whole (or isn't there), whatever the archive folders do.

**The rebuild** makes a new generation, reads every source of every folder into it, keeps
each session's label from the session table, tests the folders again and decides, takes
out what it wrote for each session it couldn't read to its end, copies from the
generation it replaces each session it keeps (every session of a harness with a folder
that didn't answer that it didn't read whole, and every session found only as compressed
rollouts), switches the pointer, removes from the table each session it found no source
for (except a row written after it started, and every row of a harness with a folder that
didn't answer, or one config `harnesses` leaves out), records the counts dropped and
kept, frees its locks and starts one child: the waiting request's when one waits, else one
walk.

**The version rebuild.** Each generation records which secrets `redact` stored its rows
(`index.stored_redact_version`). A rebuild that replaces one below the installed secrets'
`REDACT_VERSION` redacts every row again: it reads every source from its start as any
rebuild does, and then, per source, keeps everything of each source it didn't read to its
end (a gone file, a folder that didn't answer, a compressed rollout, a harness config
`harnesses` leaves out, a subagent's with `index.include_subagents` off, an imported
thread, a read that failed): its rows and tails, its `sources` row and its secret-path
calls, each kept text trimmed at its stored cut (`rows.EDGE_CHARS`), redacted again and
cut to today's sizes. It removes no session from the session table. A row of a source it
read to its end that the source no longer gives leaves, as in any rebuild, unless its
session also has a source it didn't read to its end. Nothing an ingest does starts it:
only a typed `moxi memory reindex` does, and until then an ingest stores its new rows
under the new `redact` and leaves the stored ones as they are. It records the version it
replaced (`REPLACED_KEY`), and the new generation records today's, so the next rebuild
is an ordinary one, which drops what a rebuild drops.

**Writers.** sessions is read at interface 7 (`SESSIONS_INTERFACE`), whose `writer`
names each row's writer as the row is written. A row read again whose writer differs
is replaced. A rebuild's copy keeps the writer a row was stored with, and a row copied
from a generation from before the column has none, as every row stored before it has
until a rebuild reads its source again.

**The session table.** For each main session it reads, a child writes the label when the
row has none, the newest record's time, and the launch kind. It asks the launcher for the
kind of a session whose row has none or isn't settled. The kind is settled once the index
holds a model answer of the session and the launcher answered, with a kind or with none;
with no launcher interface, at that read with no kind. A launcher that can't be asked
leaves it unsettled.

A failure that stops a child (config memory can't use, a redaction set that can't run) is
left as the doctor record `ingest`, which the next child that finishes clears.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from . import roots, index, labels, links, rows, settings, state, table

#: The most bytes one `records_since` call reads; a longer source is read in batches,
#: each written with its cursor in one transaction.
MAX_BYTES = 8 * 1024 * 1024
#: How long a child waits for the launcher's answer about one session.
KIND_WAIT = 1.0
#: The doctor record a failed child leaves.
RECORD = "ingest"
#: A `sources` row's reuse value for a compressed rollout.
COMPRESSED = "compressed"
#: The `meta` key a version rebuild writes: the `redact_version` of the generation it
#: replaced.
REPLACED_KEY = "replaced_redact_version"
#: How far up a chain of subagents a child looks for the main session.
MAX_DEPTH = 16
#: How a probe of sessions' `find` that couldn't read its root begins.
UNREADABLE_PROBE = "unreadable"


@dataclass
class Report:
    """What one child did."""

    mode: str
    busy: bool = False
    idle: bool = False
    sources: int = 0
    rows: int = 0
    tails: int = 0
    sessions: int = 0
    skipped: int = 0
    unreadable: int = 0
    compressed: int = 0
    dropped: int | None = None
    kept: int | None = None

    def line(self) -> str:
        if self.busy:
            return "another ingest is running; nothing done"
        if self.idle:
            return "nothing waiting: another child ran the request"
        text = (f"{self.mode}: {self.sources} sources read, {self.rows} rows and "
                f"{self.tails} tails written, {self.sessions} sessions")
        if self.skipped:
            text += f", {self.skipped} imported threads skipped"
        if self.unreadable:
            text += f", {self.unreadable} unreadable"
        if self.compressed:
            text += f", {self.compressed} compressed rollouts not read"
        if self.dropped is not None:
            text += f", {self.dropped} sessions dropped"
        if self.kept is not None:
            text += f", {self.kept} kept from the last index"
        return text


@dataclass
class Seen:
    """One folder in one run: whether the run listed it (a live root that doesn't exist
    and from which memory read nothing counts, as it holds nothing), whether whole, and
    what went wrong in it: why it was offline, why it lists nothing, and each path that
    failed with why. It answered only with none of those."""

    path: str
    is_archive: bool = False
    listed: bool = False
    whole: bool = False
    absent: bool = False
    offline: str | None = None
    empty: str | None = None
    errors: dict[str, str] = field(default_factory=dict)
    #: A listing in this run showed its top level holding an entry.
    held_entries: bool = False

    @property
    def answered(self) -> bool:
        return (self.listed and self.offline is None and self.empty is None
                and not self.errors)


#: `Ingest`'s `prior` when the run reads its own generation's records.
_OWN = object()


@dataclass(frozen=True)
class Role:
    """Whose rows a source's records become: the main session's id, and the subagent's
    own id (None for the main session itself)."""

    main: str
    agent: str | None = None


#: The sessions interface memory reads at: the records and the writer of each.
SESSIONS_INTERFACE = 7


def _api():
    """sessions' API at `SESSIONS_INTERFACE`. Raises `RuntimeError` when the sessions
    installed doesn't offer it."""
    from moxi import modules

    found = modules.load("sessions", interface=SESSIONS_INTERFACE)
    if found.api is None:
        raise RuntimeError(f"memory reads sessions at interface {SESSIONS_INTERFACE}: "
                           f"{found.reason or found.state}")
    return found.api


def source_key(src) -> str:
    """A source's name in `sources`: its file, or for OpenCode the database and the
    session."""
    if src.harness == "opencode":
        return f"{src.path}#{src.session_id}"
    return str(src.path)


def _reuse(entry) -> str | None:
    size = getattr(entry, "size", None)
    if size is None or getattr(entry, "mtime_ns", None) is None:
        return None
    return json.dumps([entry.size, entry.mtime_ns, entry.dev, entry.ino])


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def imported(header) -> bool:
    """Whether a Codex thread's import marks say Codex imported it from another harness:
    the imports file lists it, or its turn ids carry the import prefix."""
    marks = getattr(header, "import_marks", None)
    if marks is None:
        return False
    return marks.listed is True or marks.turn_id is True


def _spawned(header) -> bool:
    spawn = getattr(header, "spawn", None)
    return spawn is not None and spawn is not _api().UNKNOWN


def not_read(conn) -> int:
    """How many compressed Codex rollouts the index knows of whose thread has no rows:
    sessions memory could not read."""
    return conn.execute(
        "SELECT count(*) FROM sources AS s WHERE s.reuse = ? AND NOT EXISTS ("
        "SELECT 1 FROM rows AS r WHERE r.harness = s.harness "
        "AND r.session_id = s.session_id)", (json.dumps(COMPRESSED),)).fetchone()[0]


class Ingest:
    """One child's reads into one open generation (`conn`), with the session table
    (`sc`)."""

    def __init__(self, conn, sc, config, *, mode: str = "walk", fresh: bool = False,
                 relabel: bool = False, launcher: links.Link | None = None,
                 rebuilding: bool = False, prior: Any = _OWN,
                 started: float | None = None):
        """`rebuilding` for a rebuild's run, whose `prior` is the generation it replaces
        (None when there is none); a walk's `prior` is `conn` itself. A run reads folder
        records and what memory read from under a folder in `prior`."""
        self.conn = conn
        self.prior = conn if prior is _OWN else prior
        self.rebuilding = rebuilding
        self.started = time.time() if started is None else started
        self.run: dict[str, dict[str, Seen]] = {}
        self.records: dict[str, list[roots.Root]] = {}
        self.began: dict[str, float] = {}
        self.failed: dict[str, str] = {}
        self.find_roots: dict[str, list[str]] = {}
        self.begun: set[tuple[str, str]] = set()
        self.broken: set[tuple[str, str]] = set()
        self.read_whole: dict[tuple[str, str], str] = {}
        self.compressed: set[tuple[str, str]] = set()
        self._reading: Seen | None = None
        self.sc = sc
        self.harnesses = tuple(config["harnesses"])
        self.subagents = bool(config["index.include_subagents"])
        self.policy = rows.Policy(tuple(config["redact.sets"]),
                                  int(config["index.tail_chars"]))
        self.fresh = fresh
        self.relabel = relabel
        self._launcher = launcher
        self.labels: dict[tuple[str, str], str | None] = {}
        self.mains: dict[tuple[str, str], str] = {}
        self.seen: set[tuple[str, str]] = set()
        self.report = Report(mode)

    # -- the launcher ------------------------------------------------------------------

    def launcher(self) -> links.Link:
        if self._launcher is None:
            self._launcher = links.launcher()
        return self._launcher

    def ask_kind(self, session_id: str, *, answered: bool) -> tuple[str | None, bool]:
        """The launch kind to store and whether the kind is settled."""
        link = self.launcher()
        if link.state == links.NONE:
            return None, answered
        if not link.ready:
            return None, False
        try:
            kind = link.api.launch_kind(session_id, timeout_s=KIND_WAIT)
        except Exception:  # noqa: BLE001 - a launcher that can't be asked settles nothing
            return None, False
        return (kind if isinstance(kind, str) and kind else None), answered

    # -- labels and the table ----------------------------------------------------------

    def label_for(self, harness: str, main: str, cwd: Any) -> str | None:
        key = (harness, main)
        if key in self.labels:
            return self.labels[key]
        cwd = _text(cwd)
        label = None
        if self.relabel and labels.cwd_exists(cwd):
            label = labels.project_label(cwd)
            if label:
                table.relabel(self.sc, harness, main, label)
        if not label:
            row = table.row(self.sc, harness, main)
            label = row.label if row is not None and row.label else (
                labels.project_label(cwd) if cwd else None)
        self.labels[key] = label
        return label

    def answered(self, harness: str, main: str) -> bool:
        """Whether the index holds a model answer of the main session.

        A row's key leaves out the session id, so a line a Claude `--resume` copied from
        its parent lands under whichever session was read first. A full read takes
        sources in path order, so a resume can be read before its parent; the parent's
        copied answers are then the resume's, and the parent has no answer of its own
        here, is never settled and is in no start list. The resume holds the content and
        is listed, and a session settles only on answers it holds."""
        return self.conn.execute(
            "SELECT 1 FROM rows WHERE harness = ? AND session_id = ? AND agent_id IS NULL "
            "AND kind IN ('assistant', ?) LIMIT 1", (harness, main, rows.TOOL)
        ).fetchone() is not None

    def write_table(self, harness: str, main: str, newest: float | None, cwd: Any) -> None:
        row = table.row(self.sc, harness, main)
        if row is None and newest is None:
            return
        label = self.label_for(harness, main, cwd)
        kind, settled = None, False
        if row is None or row.kind is None or not row.settled:
            kind, settled = self.ask_kind(main, answered=self.answered(harness, main))
        table.write_read(self.sc, harness, main,
                         newest=newest if newest is not None else row.newest,
                         label=label, kind=kind, settled=settled)
        self.report.sessions += 1

    # -- errors, and finding a session ------------------------------------------------

    def _error(self, path: str, why: str, session: tuple[str, str] | None = None) -> None:
        """An error in the folder being read: the folder didn't answer this run, and the
        session it belongs to doesn't count as read."""
        self.report.unreadable += 1
        if self._reading is not None:
            self._reading.errors.setdefault(path, why)
        if session is not None:
            self.broken.add(session)

    def find_in(self, harness: str, session_id: str, asked: list[str] | None = None):
        """The hits of `find` for a session: in `asked` when given, else in the folders
        this run listed for the harness that answered its start, else the live root. A
        folder that can't be read in it is an error in that folder."""
        api = _api()
        asked = asked if asked is not None else self.find_roots.get(harness)
        found = (api.find(harness, session_id, root=asked) if asked
                 else api.find(harness, session_id))
        for probe in found.probes:
            if probe.outcome.startswith(UNREADABLE_PROBE):
                seen = self.run.get(harness, {}).get(probe.root)
                if seen is not None:
                    seen.errors.setdefault(probe.root, probe.outcome)
        return found.hits

    # -- classifying a source ----------------------------------------------------------

    def header_of(self, src):
        api = _api()
        try:
            with api.records(src, kinds=("marker",)) as read:
                return read.header
        except (api.Unreadable, ValueError, OSError):
            return None

    def main_of(self, harness: str, parent: Any) -> str | None:
        """The main session above `parent`, climbing past subagents that spawned
        subagents."""
        start = _text(parent)
        if start is None:
            return None
        if (harness, start) in self.mains:
            return self.mains[(harness, start)]
        current = start
        for _ in range(MAX_DEPTH):
            hit = next((h for h in self.find_in(harness, current) if not h.compressed),
                       None)
            if hit is None:
                break
            if harness == "opencode" and hit.source.agent_id is None:
                break
            header = self.header_of(hit.source)
            if header is None or (harness == "codex" and not _spawned(header)):
                break
            above = _text(header.parent)
            if above is None or above == current:
                break
            current = above
        self.mains[(harness, start)] = current
        return current

    def classify(self, src, header) -> Role | None:
        """Whose rows a source gives, or None for a source skipped (an imported
        thread)."""
        own = _text(src.session_id) or _text(getattr(header, "session_id", None))
        if own is None:
            return None
        if src.harness == "codex":
            if imported(header):
                self.report.skipped += 1
                return None
            if _spawned(header):
                return Role(self.main_of("codex", header.parent) or own, own)
            return Role(own)
        if src.harness == "opencode" and src.agent_id is not None:
            return Role(self.main_of("opencode", header.parent) or own, src.agent_id)
        return Role(own, src.agent_id)

    # -- reading one source ------------------------------------------------------------

    def note_compressed(self, src) -> None:
        self.report.compressed += 1
        self.conn.execute(
            "INSERT INTO sources (harness, source, session_id, cursor, reuse, read) "
            "VALUES (?, ?, ?, NULL, ?, ?) ON CONFLICT (harness, source) DO UPDATE SET "
            "reuse = excluded.reuse, read = excluded.read",
            (src.harness, source_key(src), src.session_id, json.dumps(COMPRESSED),
             time.time()))

    def read_source(self, item, header, role: Role, *, reuse: str | None = None) -> None:
        """Read one source since its cursor (from its start when `fresh`) and write its
        rows, tails and cursor, batch by batch."""
        from moxi import store

        api = _api()
        src = item if isinstance(item, api.Source) else item.source
        skey = source_key(src)
        stored = self.conn.execute("SELECT cursor FROM sources WHERE harness = ? AND "
                                   "source = ?", (src.harness, skey)).fetchone()
        cursor = None if self.fresh or stored is None else stored[0]
        secret = {r[0] for r in self.conn.execute(
            "SELECT call_id FROM secret_calls WHERE harness = ? AND source = ?",
            (src.harness, skey))}
        cwd = getattr(header, "cwd", None)
        label = self.label_for(src.harness, role.main, cwd)
        newest = None
        read_any = False
        self.begun.add((src.harness, role.main))
        while True:
            try:
                batch = api.records_since(item, cursor, kinds=rows.KINDS,
                                          max_bytes=MAX_BYTES)
            except OSError as exc:   # sessions' Unreadable is one, a file gone another
                why = getattr(exc, "reason", None) or exc.strerror or str(exc)
                self._error(str(src.path), why, (src.harness, role.main))
                break
            read_any = True
            got = rows.convert(batch.records, source_key=skey, main_id=role.main,
                               agent_id=role.agent, label=label, policy=self.policy,
                               writer=api.writer, secret_calls=secret)
            secret |= got.secret_calls
            with store.transaction(self.conn):
                self._write(src, skey, role, got, batch.cursor, reuse)
            if got.newest is not None:
                newest = got.newest if newest is None else max(newest, got.newest)
            cursor = batch.cursor
            if batch.complete:
                self.read_whole[(src.harness, skey)] = role.main
                break
        if read_any:
            self.report.sources += 1
            if role.agent is None:
                self.write_table(src.harness, role.main, newest, cwd)

    def _write(self, src, skey: str, role: Role, got: rows.Converted, cursor: str,
               reuse: str | None) -> None:
        c = self.conn
        if got.rows:
            cur = c.executemany(
                "INSERT INTO rows (key, harness, session_id, agent_id, label, time, "
                "turn_id, kind, text, writer) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (key) DO UPDATE SET harness = excluded.harness, "
                "session_id = excluded.session_id, agent_id = excluded.agent_id, "
                "label = excluded.label, time = excluded.time, turn_id = excluded.turn_id, "
                "kind = excluded.kind, text = excluded.text, writer = excluded.writer "
                "WHERE rows.text IS NOT excluded.text OR rows.kind IS NOT excluded.kind "
                "OR rows.writer IS NOT excluded.writer",
                [(r.key, r.harness, r.session_id, r.agent_id, r.label, r.time, r.turn_id,
                  r.kind, r.text, r.writer) for r in got.rows])
            self.report.rows += max(cur.rowcount, 0)
        if got.tails:
            cur = c.executemany(
                "INSERT INTO tails (key, harness, session_id, label, time, turn_id, "
                "failed, text) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (key) DO UPDATE SET harness = excluded.harness, "
                "session_id = excluded.session_id, label = excluded.label, "
                "time = excluded.time, turn_id = excluded.turn_id, "
                "failed = excluded.failed, text = excluded.text "
                "WHERE tails.text IS NOT excluded.text OR tails.failed IS NOT excluded.failed",
                [(t.key, t.harness, t.session_id, t.label, t.time, t.turn_id,
                  1 if t.failed else 0, t.text) for t in got.tails])
            self.report.tails += max(cur.rowcount, 0)
        if got.secret_calls:
            c.executemany("INSERT OR IGNORE INTO secret_calls (harness, source, call_id) "
                          "VALUES (?, ?, ?)",
                          [(src.harness, skey, call) for call in sorted(got.secret_calls)])
        c.execute("INSERT INTO sources (harness, source, session_id, cursor, reuse, read) "
                  "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (harness, source) DO UPDATE SET "
                  "session_id = excluded.session_id, cursor = excluded.cursor, "
                  "reuse = COALESCE(excluded.reuse, sources.reuse), read = excluded.read",
                  (src.harness, skey, role.main, cursor, reuse, time.time()))

    # -- one session, and the walk -----------------------------------------------------

    def read_subagents(self, item, role: Role, depth: int = 0) -> None:
        api = _api()
        if depth >= MAX_DEPTH:
            return
        listing = api.subagents(item)
        if listing.state != "listed":
            return
        for sub in listing:
            header = self.header_of(sub.source)
            if header is None:
                self._error(str(sub.source.path), "its first lines can't be read",
                            (sub.source.harness, role.main))
                continue
            agent = _text(sub.agent_id) or _text(sub.source.agent_id) or \
                _text(sub.source.session_id)
            sub_role = Role(role.main, agent)
            self.read_source(sub, header, sub_role)
            if sub.source.harness != "claude":
                self.read_subagents(sub, sub_role, depth + 1)

    def read_session(self, harness: str, session_id: str) -> None:
        """Read one session's sources since their cursors, and its subagents' when
        `index.include_subagents` is on."""
        if harness not in self.harnesses:
            return
        for hit in _api().find(harness, session_id).hits:
            if hit.compressed:
                self.note_compressed(hit.source)
                continue
            header = self.header_of(hit.source)
            if header is None:
                self.report.unreadable += 1
                continue
            role = self.classify(hit.source, header)
            if role is None or (role.agent is not None and not self.subagents):
                continue
            self.read_source(hit.source, header, role)
            if self.subagents and role.agent is None:
                self.read_subagents(hit.source, role)

    def walk(self, *, full: bool = False) -> None:
        """List every folder of each harness through sessions' `corpus` and read each
        source that changed since that folder's last walk (with `full`, every source),
        then test the folders again and record each one's answer (`moxi_memory.roots`).
        """
        for harness in self.harnesses:
            self._walk_harness(harness, full=full or self.fresh)
        self._reading = None
        self._retest()
        self._record()

    def _since(self, harness: str, records: list[roots.Root]) -> dict[str, float]:
        """Each folder's last walk, for `corpus`: a folder never listed whole, or holding
        a failed path no session can be named for, is left out and listed whole."""
        since = {}
        for rec in records:
            if (rec.listed_whole and rec.last_walk_at is not None
                    and all(_session_of(harness, rec.path, p) for p in rec.retry)):
                since[rec.path] = rec.last_walk_at
        if not records and self.prior is not None:
            # An index written before folder records: its live root's last walk.
            last = index.meta_get(self.prior, f"last_walk.{harness}")
            if last is not None and index.meta_get(self.prior, "first_read") == "1":
                since[str(_api().root(harness))] = float(last)
        return since

    def _walk_harness(self, harness: str, *, full: bool) -> None:
        api = _api()
        records = roots.read(self.prior, harness) if self.prior is not None else []
        self.records[harness] = records
        held_at = {r.path: r.held_entries_at for r in records}
        retry = {r.path: r.retry for r in records}
        began = time.time()
        self.began[harness] = began
        try:
            since = None if full else self._since(harness, records)
            listings = api.corpus(harness, since=since, heads=True)
        except Exception as exc:  # noqa: BLE001 - an error in every folder of the harness
            self.failed[harness] = f"sessions can't list this harness's folders: {exc}"
            return
        run = self.run.setdefault(harness, {})
        if listings:
            self.find_roots[harness] = [listings[0].root, *(
                each.root for each in listings[1:] if each.ok)]
        found: list[tuple[Any, Any, Role, Seen]] = []
        compressed = 0
        whole_count = 0
        read_paths: set[str] = set()
        for listing in listings:
            seen = Seen(listing.root, is_archive=listing.is_archive,
                        whole=since is None or listing.root not in since)
            run[listing.root] = seen
            self._reading = seen
            if not listing.ok:
                absent = listing.absent or (not listing.is_archive
                                            and not os.path.lexists(listing.root))
                if absent and not listing.is_archive:
                    seen.absent = True
                    seen.empty = _lists_nothing(roots.held(self.prior, harness, listing.root),
                                                held_at.get(listing.root))
                    if seen.empty is None:
                        seen.listed = True  # no live root: it holds nothing
                else:
                    seen.offline = listing.reason or "it can't be listed"
                continue
            seen.listed = True
            if listing.empty:
                seen.empty = _lists_nothing(roots.held(self.prior, harness, listing.root),
                                            held_at.get(listing.root))
            else:
                seen.held_entries = True
            for rel, why in listing.unreadable:
                seen.errors.setdefault(os.path.join(listing.root, rel), why)
            for entry in listing:
                src = entry.source
                read_paths.add(str(src.path))
                if entry.compressed:
                    compressed += 1
                    self.note_compressed(src)
                    if src.session_id:
                        self.seen.add((harness, src.session_id))
                        self.compressed.add((harness, src.session_id))
                    continue
                header = entry.header or self.header_of(src)
                if header is None:
                    main = src.session_id if src.agent_id is None else None
                    self._error(str(src.path), "its first lines can't be read",
                                (harness, main) if main else None)
                    if main:
                        self.seen.add((harness, main))
                    continue
                role = self.classify(src, header)
                if role is None:
                    continue
                if role.agent is None:
                    self.seen.add((harness, role.main))
                if role.agent is None or self.subagents:
                    found.append((entry, header, role, seen))
            if seen.whole:
                whole_count += len(listing)
            for path in retry.get(listing.root, ()):
                if path not in read_paths:
                    found += self._retry(harness, listing.root, path, seen)
        if run and next(iter(run.values())).whole:
            # A whole listing: how many sources this run will record, so a search
            # during the first full read can say how far it is.
            index.meta_set(self.conn, f"listed.{harness}",
                           sum(1 for _, _, role, _ in found
                               if role.agent is None or self.subagents) + compressed)
        found.sort(key=lambda item: item[2].agent is not None)   # mains, then subagents
        for item, header, role, seen in found:
            self._reading = seen
            reuse = _reuse(item)
            if not self.fresh and reuse is not None:
                stored = self.conn.execute(
                    "SELECT reuse FROM sources WHERE harness = ? AND source = ?",
                    (harness, source_key(item.source))).fetchone()
                if stored is not None and stored[0] == reuse:
                    continue
            self.read_source(item, header, role, reuse=reuse)
            if (isinstance(item, _api().Source) and self.subagents and role.agent is None
                    and harness == "claude"):
                self.read_subagents(item, role)
        self._reading = None

    def _retry(self, harness: str, root: str, path: str, seen: Seen) -> list:
        """The sources of the session a path that failed at an earlier walk belongs to,
        found in its folder again; none when the session is gone from it."""
        session_id = _session_of(harness, root, path)
        if session_id is None:
            return []
        out = []
        for hit in self.find_in(harness, session_id, [root]):
            if hit.compressed:
                self.note_compressed(hit.source)
                continue
            header = self.header_of(hit.source)
            if header is None:
                self._error(str(hit.source.path), "its first lines can't be read",
                            (harness, session_id))
                continue
            role = self.classify(hit.source, header)
            if role is not None and (role.agent is None or self.subagents):
                if role.agent is None:
                    self.seen.add((harness, role.main))
                out.append((hit.source, header, role, seen))
        return out

    def _retest(self) -> None:
        """The test just before the run's decision: every archive folder that has answered
        so far is tested again by sessions, taking no kept answer (all at once, one wait),
        and each live root's top level is listed again. A folder that fails then, or that
        lists nothing though memory read from it or a listing once showed it holding an
        entry (this run's included), didn't answer the run."""
        api = _api()
        archive_dirs = [s.path for run in self.run.values() for s in run.values()
                        if s.is_archive and s.answered]
        answers: dict[str, Any] = {}
        why = None
        if archive_dirs:
            try:
                answers = api.archive_answers(archive_dirs, fresh=True)
            except Exception as exc:  # noqa: BLE001 - a test that can't run isn't an answer
                why = f"its test before the decision couldn't run: {exc}"
        for harness, run in self.run.items():
            for s in run.values():
                if not s.answered:
                    continue
                if s.is_archive:
                    answer = answers.get(s.path)
                    if answer is None or not answer.ok:
                        s.offline = why or ("didn't answer the test before the decision: "
                                            f"{getattr(answer, 'reason', None)}")
                        continue
                    top = "empty" if answer.empty else "entries"
                else:
                    top, reason = _top_now(s.path)
                    if reason is not None:
                        s.offline = f"couldn't be listed at the test before the decision: {reason}"
                        continue
                if top in ("absent", "empty"):
                    prev = next((r.held_entries_at for r in self.records.get(harness, [])
                                 if r.path == s.path), None)
                    s.empty = _lists_nothing(
                        roots.held(self.prior, harness, s.path),
                        self.began[harness] if s.held_entries else prev)
                else:
                    s.held_entries = True

    def harness_answered(self, harness: str) -> bool:
        """Whether every folder of `harness` answered this run."""
        return harness not in self.failed and all(
            s.answered for s in self.run.get(harness, {}).values())

    def _record(self) -> None:
        """Write each folder's answer to this run, its last walk and what failed, into
        `meta`, and mark the first full read finished once every live root has been
        listed whole."""
        for harness in self.harnesses:
            old = {r.path: r for r in self.records.get(harness, [])}
            recs: list[roots.Root] = []
            if harness in self.failed:
                paths = list(old)
                if not paths:
                    try:
                        paths = [str(_api().root(harness))]
                    except Exception:  # noqa: BLE001 - no root to name
                        paths = []
                for path in paths:
                    prev = old.get(path)
                    rec = self._carried(path, prev)
                    rec.why, rec.errors = roots.FAILED, 1
                    rec.first_error = (path, self.failed[harness])
                    recs.append(rec)
                roots.write(self.conn, harness, recs)
                continue
            for path, s in self.run.get(harness, {}).items():
                prev = old.pop(path, None)
                rec = self._carried(path, prev, is_archive=s.is_archive)
                if s.held_entries:          # answered or not; the last walk is apart
                    rec.held_entries_at = self.began[harness]
                failed = bool(s.errors) and s.offline is None and s.empty is None
                if (s.answered or failed) and not s.absent:
                    # a live root that isn't there keeps no last walk, so it is listed
                    # whole once it is
                    rec.last_walk_at = self.began[harness]
                    if s.whole:
                        rec.listed_whole = True
                if s.answered:
                    rec.answered, rec.since_at = True, None
                    if self.rebuilding:
                        rec.missed_rebuild_at = None
                elif s.offline is not None:
                    rec.why, rec.reason = roots.OFFLINE, s.offline
                elif s.empty is not None:
                    rec.why, rec.reason = roots.EMPTY, s.empty
                else:
                    rec.why, rec.errors = roots.FAILED, len(s.errors)
                    rec.first_error = next(iter(s.errors.items()))
                    rec.retry = tuple(s.errors)
                recs.append(rec)
            if not self.rebuilding:
                for prev in old.values():   # left until a rebuild leaves it out
                    prev.in_config = False
                    recs.append(prev)
            roots.write(self.conn, harness, recs)
            live = recs[0] if recs and not recs[0].is_archive else None
            if live is not None and live.last_walk_at is not None:
                index.meta_set(self.conn, f"last_walk.{harness}", live.last_walk_at)
        lives = []
        for harness in self.harnesses:
            recs = roots.read(self.conn, harness)
            first = next(iter(self.run.get(harness, {}).values()), None)
            lives.append(harness not in self.failed and bool(recs)
                         and not recs[0].is_archive
                         and (recs[0].listed_whole
                              or (first is not None and first.absent and first.answered)))
        if all(lives):
            index.meta_set(self.conn, "first_read", 1)

    def _carried(self, path: str, prev: roots.Root | None, *,
                 is_archive: bool | None = None) -> roots.Root:
        """A new record of `path` for this run, not answered, carrying from `prev` what
        a run that didn't read the folder keeps: its last walk, whether it was listed
        whole (a rebuild starts a live root's afresh), since when it hasn't answered, the
        last rebuild it missed, and when a listing last showed it holding an entry."""
        archive = prev.is_archive if is_archive is None and prev else bool(is_archive)
        rec = roots.Root(path, is_archive=archive, run_at=self.started)
        if prev is not None:
            rec.last_walk_at = prev.last_walk_at
            rec.listed_whole = prev.listed_whole and (archive or not self.rebuilding)
            rec.missed_rebuild_at = prev.missed_rebuild_at
            rec.held_entries_at = prev.held_entries_at
            if not prev.answered and prev.since_at:
                rec.since_at = prev.since_at
        if rec.since_at is None:
            rec.since_at = self.started
        if self.rebuilding:
            rec.missed_rebuild_at = self.started
        return rec

    # -- the rebuild's keep rule -------------------------------------------------------

    def take_out_broken(self) -> None:
        """Take out of this generation what the run wrote for each session it began and
        couldn't read to its end."""
        from moxi import store

        with store.transaction(self.conn):
            for harness, session_id in sorted(self.broken):
                srcs = [r[0] for r in self.conn.execute(
                    "SELECT source FROM sources WHERE harness = ? AND session_id = ?",
                    (harness, session_id))]
                self.conn.executemany(
                    "DELETE FROM secret_calls WHERE harness = ? AND source = ?",
                    [(harness, s) for s in srcs])
                for table_name in ("rows", "tails", "sources"):
                    self.conn.execute(f"DELETE FROM {table_name} WHERE harness = ? "
                                      "AND session_id = ?", (harness, session_id))

    def keeping(self) -> set[str]:
        """The harnesses with a folder that didn't answer this run, whose sessions a
        rebuild keeps."""
        return {h for h in self.harnesses if not self.harness_answered(h)}

    def copy_kept(self) -> int:
        """Copy from the generation this rebuild replaces each session it keeps: every
        session of a harness with a folder that didn't answer that it didn't read whole,
        and every session it found only as compressed rollouts, which sessions lists and
        doesn't read. Returns how many sessions it copied.

        A copy takes the session's rows, tails, sources with their cursors and secret-path
        calls, under the rules a stored row allows: no row of a harness config
        `harnesses` leaves out, no subagent row with `index.include_subagents` off, each
        text redacted with today's `redact.sets` and cut to today's sizes, and each row
        given the session table's label. Each row keeps the writer it was stored with."""
        from moxi import store

        old = self.prior
        if old is None:
            return 0
        keep = self.keeping()
        done = self.begun - self.broken
        copied = 0
        for harness in self.harnesses:
            sessions = sorted({r[0] for r in old.execute(
                "SELECT session_id FROM rows WHERE harness = ? UNION "
                "SELECT session_id FROM sources WHERE harness = ? "
                "AND session_id IS NOT NULL", (harness, harness))})
            for session_id in sessions:
                key = (harness, session_id)
                if key in done or not (harness in keep or key in self.compressed):
                    continue
                with store.transaction(self.conn):
                    if self._copy_one(old, harness, session_id):
                        copied += 1
        return copied

    def _copy_one(self, old, harness: str, session_id: str) -> bool:
        row = table.row(self.sc, harness, session_id)
        label = row.label if row is not None and row.label else None
        sets = self.policy.sets
        rows_out = []
        for key, agent, label_was, when, turn, kind, text, wrote in old.execute(
                "SELECT key, agent_id, label, time, turn_id, kind, text, "
                f"{_writer_column(old)} FROM rows "
                "WHERE harness = ? AND session_id = ?", (harness, session_id)):
            if agent is not None and not self.subagents:
                continue
            limit = rows.TOOL_CHARS if kind == rows.TOOL else rows.MESSAGE_CHARS
            rows_out.append((key, harness, session_id, agent, label or label_was, when,
                             turn, kind, rows.recut_head(text, limit, sets), wrote))
        self.conn.executemany(
            "INSERT OR IGNORE INTO rows (key, harness, session_id, agent_id, label, time, "
            "turn_id, kind, text, writer) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows_out)
        tails_out = [(key, harness, session_id, label or label_was, when, turn, failed,
                      rows.recut_tail(text, self.policy.tail_chars, sets))
                     for key, label_was, when, turn, failed, text in old.execute(
                         "SELECT key, label, time, turn_id, failed, text FROM tails "
                         "WHERE harness = ? AND session_id = ?", (harness, session_id))]
        self.conn.executemany(
            "INSERT OR IGNORE INTO tails (key, harness, session_id, label, time, turn_id, "
            "failed, text) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", tails_out)
        srcs = old.execute("SELECT harness, source, session_id, cursor, reuse, read "
                           "FROM sources WHERE harness = ? AND session_id = ?",
                           (harness, session_id)).fetchall()
        self.conn.executemany(
            "INSERT OR IGNORE INTO sources (harness, source, session_id, cursor, reuse, "
            "read) VALUES (?, ?, ?, ?, ?, ?)", srcs)
        for _, source, *_ in srcs:
            self.conn.executemany(
                "INSERT OR IGNORE INTO secret_calls (harness, source, call_id) "
                "VALUES (?, ?, ?)", old.execute(
                    "SELECT harness, source, call_id FROM secret_calls "
                    "WHERE harness = ? AND source = ?", (harness, source)).fetchall())
        return bool(rows_out or tails_out or srcs)

    # -- the version rebuild's keep rule -----------------------------------------------

    def read_to_end(self) -> set[tuple[str, str]]:
        """The `(harness, source key)` of each source this run read to its end: its read
        stopped at a batch sessions marked complete, and its session isn't one the run
        began and couldn't read to its end."""
        return {key for key, main in self.read_whole.items()
                if (key[0], main) not in self.broken}

    def copy_unread(self) -> int:
        """Copy from the generation a version rebuild replaces everything of a source
        this run didn't read to its end, decided per source. Returns how many sessions it
        copied anything of.

        A row or tail is counted against every source that may have given it: each source
        the old `sources` lists under its harness and session, the source its key names
        (`<source>@line:N` or `@offset:N`), and for an OpenCode subagent's row its own
        session's source. It is copied when one of those is unread, or when none is
        known. Every unread `sources` row is copied with its cursor and reuse, and the
        secret-path calls of each. Nothing the config leaves out is skipped: every harness
        the old generation holds, and subagents' rows whether or not
        `index.include_subagents` is on. A copy never replaces what this run wrote. Each
        copied text loses its stored cut edge (`rows.EDGE_CHARS`), is redacted again with
        today's `redact.sets` and cut to today's sizes, and each row takes the session
        table's label and keeps the writer it was stored with."""
        from moxi import store

        old = self.prior
        if old is None:
            return 0
        whole = self.read_to_end()
        listed: dict[tuple[str, str], set[str]] = {}
        for harness, source, session_id in old.execute(
                "SELECT harness, source, session_id FROM sources"):
            if session_id is not None:
                listed.setdefault((harness, session_id), set()).add(source)
        sessions = sorted(set(old.execute(
            "SELECT harness, session_id FROM rows UNION "
            "SELECT harness, session_id FROM tails UNION "
            "SELECT harness, session_id FROM sources WHERE session_id IS NOT NULL")))
        copied = 0
        for harness, session_id in sessions:
            own = listed.get((harness, session_id), set())
            with store.transaction(self.conn):
                if self._copy_unread_one(old, harness, session_id, own, whole):
                    copied += 1
        for harness, source in old.execute(
                "SELECT harness, source FROM sources WHERE session_id IS NULL").fetchall():
            if (harness, source) not in whole:
                with store.transaction(self.conn):
                    self._copy_source(old, harness, source)
        return copied

    def _copy_unread_one(self, old, harness: str, session_id: str, own: set[str],
                         whole: set[tuple[str, str]]) -> bool:
        def kept(key: str, agent: str | None) -> bool:
            counted = set(own)
            named = _named_source(key)
            if named is not None:
                counted.add(named)
            if harness == "opencode" and agent is not None:
                counted.update(f"{s.rpartition('#')[0]}#{agent}" for s in own if "#" in s)
            return not counted or any((harness, s) not in whole for s in counted)

        row = table.row(self.sc, harness, session_id)
        label = row.label if row is not None and row.label else None
        sets = self.policy.sets
        rows_out = []
        for key, agent, label_was, when, turn, kind, text, wrote in old.execute(
                "SELECT key, agent_id, label, time, turn_id, kind, text, "
                f"{_writer_column(old)} FROM rows "
                "WHERE harness = ? AND session_id = ?", (harness, session_id)):
            if not kept(key, agent):
                continue
            limit = rows.TOOL_CHARS if kind == rows.TOOL else rows.MESSAGE_CHARS
            rows_out.append((key, harness, session_id, agent, label or label_was, when,
                             turn, kind, rows.recut_head(text, limit, sets,
                                                         edge_chars=rows.EDGE_CHARS),
                             wrote))
        self.conn.executemany(
            "INSERT OR IGNORE INTO rows (key, harness, session_id, agent_id, label, time, "
            "turn_id, kind, text, writer) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows_out)
        tails_out = [(key, harness, session_id, label or label_was, when, turn, failed,
                      rows.recut_tail(text, self.policy.tail_chars, sets,
                                      edge_chars=rows.EDGE_CHARS))
                     for key, label_was, when, turn, failed, text in old.execute(
                         "SELECT key, label, time, turn_id, failed, text FROM tails "
                         "WHERE harness = ? AND session_id = ?", (harness, session_id))
                     if kept(key, None)]
        self.conn.executemany(
            "INSERT OR IGNORE INTO tails (key, harness, session_id, label, time, turn_id, "
            "failed, text) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", tails_out)
        unread = [s for s in sorted(own) if (harness, s) not in whole]
        for source in unread:
            self._copy_source(old, harness, source)
        return bool(rows_out or tails_out or unread)

    def _copy_source(self, old, harness: str, source: str) -> None:
        """Copy one `sources` row, with its cursor and reuse, and its secret-path calls."""
        self.conn.executemany(
            "INSERT OR IGNORE INTO sources (harness, source, session_id, cursor, reuse, "
            "read) VALUES (?, ?, ?, ?, ?, ?)",
            old.execute("SELECT harness, source, session_id, cursor, reuse, read "
                        "FROM sources WHERE harness = ? AND source = ?",
                        (harness, source)).fetchall())
        self.conn.executemany(
            "INSERT OR IGNORE INTO secret_calls (harness, source, call_id) "
            "VALUES (?, ?, ?)", old.execute(
                "SELECT harness, source, call_id FROM secret_calls "
                "WHERE harness = ? AND source = ?", (harness, source)).fetchall())


def _writer_column(conn) -> str:
    """What a copy selects for a row's writer from the generation it replaces: the
    column, or NULL from a generation at a schema before it."""
    return "writer" if index.has_writer(conn) else "NULL"


def _named_source(key: str) -> str | None:
    """The source a row's key names (`<harness>|<source>@line:N|<place>`, or
    `@offset:N`), or None for a key that names the line by the harness's own id."""
    _, _, rest = key.partition("|")
    line, _, _ = rest.rpartition("|")
    source, sep, place = line.rpartition("@")
    if sep and source and (place.startswith("line:") or place.startswith("offset:")):
        return source
    return None


def _lists_nothing(n: int, held_at: float | None) -> str | None:
    """Why a folder that lists nothing didn't answer, naming each fact that holds: `n`
    sources memory read from under it, and when a listing last showed it holding an entry.
    None when neither holds: a folder never seen holding anything answers."""
    from .clock import show

    said = []
    if n:
        said.append(f"memory read {n} source{'' if n == 1 else 's'} from it")
    if held_at is not None:
        said.append(f"it held entries at {show(held_at)}")
    return f"lists nothing, though {' and '.join(said)}" if said else None


def _top_now(path: str) -> tuple[str, str | None]:
    """A live root's top level now: `absent`, `empty`, `entries` or `file` (an OpenCode
    database), or why it can't be listed."""
    import stat as stat_mod

    try:
        st = os.stat(path)
    except FileNotFoundError:
        return "absent", None
    except OSError as exc:
        return "", exc.strerror or str(exc)
    if not stat_mod.S_ISDIR(st.st_mode):
        return "file", None
    try:
        with os.scandir(path) as it:
            return ("empty" if next(it, None) is None else "entries"), None
    except OSError as exc:
        return "", exc.strerror or str(exc)


_THREAD_ID = re.compile(r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
                        r"[0-9a-fA-F]{12})\.jsonl(?:\.zst)?$")


def _session_of(harness: str, root: str, path: str) -> str | None:
    """The session a failed path under `root` belongs to, or None when the path names
    no one session (a directory above the sessions, or an OpenCode database)."""
    rel = os.path.relpath(path, root)
    if rel.startswith(".."):
        return None
    parts = rel.split(os.sep)
    if harness == "claude" and len(parts) >= 2:
        name = parts[1]
        name = name[:-len(".jsonl")] if name.endswith(".jsonl") else name
        return name if name and "." not in name else None
    if harness == "codex":
        match = _THREAD_ID.search(parts[-1])
        return match.group(1).lower() if match else None
    return None


# -- the modes -------------------------------------------------------------------------

#: The modes a trigger starts: never recorded, and lost harmlessly to a held lock.
TRIGGERS = ("turn", "gone", "walk")
#: The mode of a child started to run the waiting request.
WAITING = "waiting"


def _rebuild(w, *, env, relabel: bool, config, launcher) -> Report:
    """Build a new generation from every source of every folder that answers, under the
    held writer lock `w`; keep what can't be known gone; switch to it; and drop from the
    session table each session it found no source for.

    A rebuild that replaces a generation whose `redact_version` is below the installed
    secrets' (a version rebuild) keeps more and drops nothing from the table: in place of
    `Ingest.copy_kept` it copies, per source, everything of each source it didn't read to
    its end (`Ingest.copy_unread`), and it removes no session. It runs only when someone
    types `moxi memory reindex`, as any rebuild does; an ingest into such a generation
    leaves its stored rows as they are.

    Its steps, in order: it reads every folder `corpus` lists; it tests the folders again
    and decides which answered (`Ingest.walk`); it takes out of the new generation what it
    wrote for each session it couldn't read to its end; it copies the sessions it keeps
    from the generation it replaces, which it holds open read only under the reader's
    shared lock until then (`Ingest.copy_kept`); it switches. A session of a harness with
    a folder that didn't answer is unknown, not gone: the rebuild keeps it, and its row in
    the session table, as it keeps every row of a harness config `harnesses` leaves
    out."""
    with w.rebuilding() as started:
        try:
            old = index.open_read(env)
        except (index.IndexMissing, index.IndexUnreadable):
            old = None
        versioned = (old is not None and index.stored_redact_version(old.conn)
                     < rows.redact_version())
        replaced = index.stored_redact_version(old.conn) if versioned else None
        try:
            n, conn = w.new_generation()
            sc = state.connect(env)
            try:
                try:
                    job = Ingest(conn, sc, config, mode="relabel" if relabel else "rebuild",
                                 fresh=True, relabel=relabel, launcher=launcher,
                                 rebuilding=True, prior=old.conn if old else None,
                                 started=started)
                    job.walk(full=True)
                    job.take_out_broken()
                    kept = job.copy_unread() if versioned else job.copy_kept()
                finally:
                    conn.close()
                if old is not None:
                    old.close()
                    old = None
                w.switch(n)
                if versioned:
                    dropped = 0
                else:
                    seen = set(job.seen)
                    keep = job.keeping()
                    seen.update((h, s) for h, s in sc.execute(
                        "SELECT harness, session_id FROM sessions")
                        if h in keep or h not in job.harnesses)
                    dropped = table.remove_unseen(sc, seen, started)
                state.clear_unfinished(sc)
            finally:
                sc.close()
        finally:
            if old is not None:
                old.close()
        conn = w.connect()
        try:
            index.meta_set(conn, "dropped", dropped)
            index.meta_set(conn, "kept", kept)
            index.meta_set(conn, "rebuilt", started)
            if replaced is not None:
                index.meta_set(conn, REPLACED_KEY, replaced)
        finally:
            conn.close()
        job.report.dropped = dropped
        job.report.kept = kept
    return job.report


def _do(w, mode: str, harness: str | None, session_id: str | None, *, env, config,
        launcher) -> Report:
    """One mode's work under the held writer lock `w`."""
    if mode in ("rebuild", "relabel"):
        return _rebuild(w, env=env, relabel=mode == "relabel", config=config,
                        launcher=launcher)
    conn = w.connect()
    sc = state.connect(env)
    try:
        job = Ingest(conn, sc, config, mode=mode, fresh=mode == "all", launcher=launcher)
        if mode in ("turn", "gone"):
            job.read_session(harness, session_id)
        if mode in ("gone", "walk"):
            job.walk()
        elif mode == "all":
            job.walk(full=True)
    finally:
        conn.close()
        sc.close()
    return job.report


def _take(env) -> state.Request | None:
    sc = state.connect(env)
    try:
        return state.take_request(sc)
    finally:
        sc.close()


def _after(env, then, *, walk: bool) -> None:
    """After the writer lock is freed: start a child for the waiting request, or, when
    none waits and `walk`, one walk."""
    try:
        sc = state.connect(env)
        try:
            waiting = state.read_request(sc)
        finally:
            sc.close()
    except Exception:  # noqa: BLE001 - a store that can't be read starts nothing it holds
        waiting = None
    if waiting is not None:
        then([f"--{WAITING}"])
    elif walk:
        then(["--walk"])


def run(mode: str, harness: str | None = None, session_id: str | None = None, *,
        asked: bool = False, env: Mapping[str, str] | None = None, config=None,
        launcher: links.Link | None = None, then=None) -> Report:
    """Run one child's work: `turn`, `gone` or `walk` for a trigger; `walk` with `asked`,
    `all`, `rebuild` or `relabel` for a request; `waiting` to run the request waiting.

    A held writer lock gives a busy report at once; a request stays recorded for the
    holder to start. After it frees the lock, the child starts the next child with
    `then(args)` (by default `moxi_memory.start.spawn`): the waiting request's, or after
    a rebuild that finished with none waiting, one walk.
    """
    if then is None:
        from .start import spawn as then
    if mode not in (*TRIGGERS, *state.KINDS, WAITING):
        raise ValueError(f"{mode!r} is not a mode of the ingest child")
    config = settings.read(env) if config is None else config
    kind = mode if mode in state.KINDS and (asked or mode not in TRIGGERS) else None
    if kind is not None:
        sc = state.connect(env)
        try:
            state.ask(sc, kind)
        finally:
            sc.close()
    held = False
    try:
        with index.writer(env) as w:
            held = True
            if kind is None and mode != WAITING:
                report = _do(w, mode, harness, session_id, env=env, config=config,
                             launcher=launcher)
            else:
                taken = _take(env)
                report = (Report(mode, idle=True) if taken is None else
                          _do(w, taken.kind, None, None, env=env, config=config,
                              launcher=launcher))
    except index.WriterBusy:
        if held:
            raise
        return Report(mode, busy=True)
    except BaseException:
        if held:
            try:
                _after(env, then, walk=False)
            except Exception:  # noqa: BLE001 - the error that stopped the child is raised
                pass
        raise
    _after(env, then, walk=report.mode in ("rebuild", "relabel") and not report.idle)
    return report


def rebuild(*, env: Mapping[str, str] | None = None, relabel: bool = False,
            config=None, launcher: links.Link | None = None, then=None) -> Report:
    """Ask for a rebuild (a relabel with `relabel`) and run it when this child takes the
    writer lock. Raises `index.WriterBusy` when another child holds it; the request then
    waits for that child to start it."""
    report = run("relabel" if relabel else "rebuild", env=env, config=config,
                 launcher=launcher, then=then)
    if report.busy:
        raise index.WriterBusy("another ingest holds memory's writer lock")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="moxi-memory-ingest",
                                     description="Read session transcripts into memory's "
                                                 "index. Started detached by memory.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--turn", nargs=2, metavar=("HARNESS", "SESSION"))
    group.add_argument("--gone", nargs=2, metavar=("HARNESS", "SESSION"))
    group.add_argument("--walk", action="store_true")
    group.add_argument("--all", action="store_true")
    group.add_argument("--rebuild", action="store_true")
    group.add_argument("--relabel", action="store_true")
    group.add_argument(f"--{WAITING}", action="store_true")
    parser.add_argument("--asked", action="store_true",
                        help="with --walk: someone asked for it, so a held lock "
                             "doesn't lose it")
    args = parser.parse_args(argv)
    if args.asked and not args.walk:
        parser.error("--asked goes with --walk only")
    harness = session_id = None
    if args.turn or args.gone:
        mode = "turn" if args.turn else "gone"
        harness, session_id = args.turn or args.gone
    else:
        mode = next(m for m in ("walk", "all", "rebuild", "relabel", WAITING)
                    if getattr(args, m))
    from moxi import doctor

    try:
        report = run(mode, harness, session_id, asked=args.asked)
    except Exception as exc:  # noqa: BLE001 - a failed child leaves a record and exits
        doctor.record(settings.MODULE, f"an ingest ({mode}) stopped: {exc}", key=RECORD,
                      fix="moxi memory ingest")
        print(f"moxi-memory-ingest: {exc}", file=sys.stderr)
        return 1
    if not report.busy:
        doctor.clear(settings.MODULE, RECORD)
    print(report.line())
    return 0


__all__ = ["MAX_BYTES", "RECORD", "REPLACED_KEY", "TRIGGERS", "WAITING", "Report", "Role", "Ingest",
           "source_key", "imported", "not_read", "rebuild", "run", "main"]

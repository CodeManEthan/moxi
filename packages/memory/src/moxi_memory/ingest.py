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
with `index.subagents` on, their rows under their main session's id. A Codex thread whose
import marks say it was imported (it is listed in Codex's imports file, or its turns
carry the import prefix) is skipped: the Claude Code transcript is the original. A
compressed Codex rollout, which sessions doesn't read, is recorded in `sources` and counted
by `not_read`.

**The walk** lists each harness root with `modified_since` the last walk of that harness
and reads each source whose reuse keys changed. **The rebuild** makes a new generation,
reads every live source into it, keeps each session's label from the session table,
switches the pointer, removes from the table each session it found no source for (except
a row written after it started, and every row of a harness it didn't list: one whose root
couldn't be listed, or one config `harnesses` leaves out), records that count as the
generation's `dropped`, frees its locks and starts one child: the waiting request's when
one waits, else one walk. Its
first full read is marked finished under the walk's rule, so a harness whose root is
there but couldn't be listed leaves the new generation still filling.

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
import sys
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from . import index, labels, links, rows, settings, state, table

#: The most bytes one `records_since` call reads; a longer source is read in batches,
#: each written with its cursor in one transaction.
MAX_BYTES = 8 * 1024 * 1024
#: How long a child waits for the launcher's answer about one session.
KIND_WAIT = 1.0
#: The doctor record a failed child leaves.
RECORD = "ingest"
#: A `sources` row's reuse value for a compressed rollout.
COMPRESSED = "compressed"
#: How far up a chain of subagents a child looks for the main session.
MAX_DEPTH = 16


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
        return text


@dataclass(frozen=True)
class Role:
    """Whose rows a source's records become: the main session's id, and the subagent's
    own id (None for the main session itself)."""

    main: str
    agent: str | None = None


def _api():
    from moxi_sessions import api

    return api


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
                 relabel: bool = False, launcher: links.Link | None = None):
        self.conn = conn
        self.sc = sc
        self.harnesses = tuple(config["harnesses"])
        self.subagents = bool(config["index.subagents"])
        self.policy = rows.Policy(tuple(config["redact.sets"]),
                                  int(config["index.tail_chars"]))
        self.fresh = fresh
        self.relabel = relabel
        self._launcher = launcher
        self.labels: dict[tuple[str, str], str | None] = {}
        self.mains: dict[tuple[str, str], str] = {}
        self.seen: set[tuple[str, str]] = set()
        self.unlisted: set[str] = set()
        self.report = Report(mode)

    # -- the launcher ------------------------------------------------------------------

    def launcher(self) -> links.Link:
        if self._launcher is None:
            self._launcher = links.launcher()
        return self._launcher

    def ask_kind(self, session_id: str, answered: bool) -> tuple[str | None, bool]:
        """The launch kind to store and whether the kind is settled."""
        link = self.launcher()
        if link.state == links.NONE:
            return None, answered
        if not link.ready:
            return None, False
        try:
            kind = link.api.launch_kind(session_id, timeout=KIND_WAIT)
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
            kind, settled = self.ask_kind(main, self.answered(harness, main))
        table.write_read(self.sc, harness, main,
                         newest=newest if newest is not None else row.newest,
                         label=label, kind=kind, settled=settled)
        self.report.sessions += 1

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
        api = _api()
        current = start
        for _ in range(MAX_DEPTH):
            hit = next((h for h in api.find(harness, current).hits if not h.compressed),
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
        while True:
            try:
                batch = api.records_since(item, cursor, kinds=rows.KINDS,
                                          max_bytes=MAX_BYTES)
            except api.Unreadable:
                self.report.unreadable += 1
                break
            read_any = True
            got = rows.convert(batch.records, source_key=skey, main_id=role.main,
                               agent_id=role.agent, label=label, policy=self.policy,
                               secret_calls=secret)
            secret |= got.secret_calls
            with store.transaction(self.conn):
                self._write(src, skey, role, got, batch.cursor, reuse)
            if got.newest is not None:
                newest = got.newest if newest is None else max(newest, got.newest)
            cursor = batch.cursor
            if batch.complete:
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
                "turn_id, kind, text) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (key) DO UPDATE SET harness = excluded.harness, "
                "session_id = excluded.session_id, agent_id = excluded.agent_id, "
                "label = excluded.label, time = excluded.time, turn_id = excluded.turn_id, "
                "kind = excluded.kind, text = excluded.text "
                "WHERE rows.text IS NOT excluded.text OR rows.kind IS NOT excluded.kind",
                [(r.key, r.harness, r.session_id, r.agent_id, r.label, r.time, r.turn_id,
                  r.kind, r.text) for r in got.rows])
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
                self.report.unreadable += 1
                continue
            agent = _text(sub.agent_id) or _text(sub.source.agent_id) or \
                _text(sub.source.session_id)
            sub_role = Role(role.main, agent)
            self.read_source(sub, header, sub_role)
            if sub.source.harness != "claude":
                self.read_subagents(sub, sub_role, depth + 1)

    def read_session(self, harness: str, session_id: str) -> None:
        """Read one session's sources since their cursors, and its subagents' when
        `index.subagents` is on."""
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
        """List each harness root and read each source that changed since the last walk
        (with `full`, every source the roots hold)."""
        api = _api()
        absent: set[str] = set()
        for harness in self.harnesses:
            last = index.meta_get(self.conn, f"last_walk.{harness}")
            since = None if full or self.fresh or last is None else float(last)
            began = time.time()
            try:
                listing = api.sources(harness, modified_since=since, heads=True)
            except Exception:  # noqa: BLE001 - a root that can't be listed is skipped
                listing = None
            if listing is None or not listing.ok:
                self.unlisted.add(harness)
                if listing is not None and not os.path.lexists(listing.root):
                    absent.add(harness)  # no root: this harness has nothing to read
                continue
            mains, subs = [], []
            for entry in listing:
                src = entry.source
                if entry.compressed:
                    self.note_compressed(src)
                    if src.session_id:
                        self.seen.add((harness, src.session_id))
                    continue
                header = entry.header or self.header_of(src)
                if header is None:
                    self.report.unreadable += 1
                    if src.agent_id is None and src.session_id:
                        self.seen.add((harness, src.session_id))
                    continue
                role = self.classify(src, header)
                if role is None:
                    continue
                if role.agent is None:
                    self.seen.add((harness, role.main))
                    mains.append((entry, header, role))
                elif self.subagents:
                    subs.append((entry, header, role))
            if since is None:
                # A whole listing: how many sources this walk will record, so a search
                # during the first full read can say how far it is.
                noted = sum(1 for entry in listing if entry.compressed)
                index.meta_set(self.conn, f"listed.{harness}",
                               len(mains) + len(subs) + noted)
            for entry, header, role in mains + subs:
                reuse = _reuse(entry)
                if not self.fresh and reuse is not None:
                    stored = self.conn.execute(
                        "SELECT reuse FROM sources WHERE harness = ? AND source = ?",
                        (harness, source_key(entry.source))).fetchone()
                    if stored is not None and stored[0] == reuse:
                        continue
                self.read_source(entry, header, role, reuse=reuse)
            index.meta_set(self.conn, f"last_walk.{harness}", began)
        if all(h in absent or index.meta_get(self.conn, f"last_walk.{h}") is not None
               for h in self.harnesses):
            index.meta_set(self.conn, "first_read", 1)


# -- the modes -------------------------------------------------------------------------

#: The modes a trigger starts: never recorded, and lost harmlessly to a held lock.
TRIGGERS = ("turn", "gone", "walk")
#: The mode of a child started to run the waiting request.
WAITING = "waiting"


def _rebuild(w, *, env, relabel: bool, config, launcher) -> Report:
    """Build a new generation from every live source under the held writer lock `w`,
    switch to it, and drop the sessions with no source from the session table."""
    with w.rebuilding() as started:
        n, conn = w.new_generation()
        sc = state.connect(env)
        try:
            try:
                job = Ingest(conn, sc, config, mode="relabel" if relabel else "rebuild",
                             fresh=True, relabel=relabel, launcher=launcher)
                job.walk(full=True)
            finally:
                conn.close()
            w.switch(n)
            # A harness the rebuild didn't list keeps all its rows: one it tried and
            # couldn't list, and one config `harnesses` leaves out. Its identities and
            # launch kinds can't be read back from a transcript.
            seen = set(job.seen)
            seen.update((h, s) for h, s in sc.execute(
                "SELECT harness, session_id FROM sessions")
                if h in job.unlisted or h not in job.harnesses)
            dropped = table.remove_unseen(sc, seen, started)
            state.clear_unfinished(sc)
        finally:
            sc.close()
        conn = w.connect()
        try:
            index.meta_set(conn, "dropped", dropped)
            index.meta_set(conn, "rebuilt", started)
        finally:
            conn.close()
        job.report.dropped = dropped
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


__all__ = ["MAX_BYTES", "RECORD", "TRIGGERS", "WAITING", "Report", "Role", "Ingest",
           "source_key", "imported", "not_read", "rebuild", "run", "main"]

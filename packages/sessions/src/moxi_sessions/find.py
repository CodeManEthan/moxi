"""Finding records: one session's (`find`), every source under a root (`sources`), and a
session's subagents (`subagents`), for Claude Code, Codex and OpenCode alike.

**`find(harness, session_id, *, root=None)`** asks each root in turn for the session and
stops at the first that holds it. Every root asked is a `Probe` with its outcome: `hit`
(with what it found), `miss` (the root was read and holds none), `unreadable: <why>` (the
root could not be read: an absent or unmounted root is this, never a miss) or `refused:
<why>` (the id is not one the search looks up, so nothing was looked at). Only `miss` says
the record is absent. A Claude root holds one transcript per session; a Codex root can hold
several rollout files for one thread, and `find` returns each of them, a compressed one
(`.jsonl.zst`) marked `compressed`, for the caller to choose from; OpenCode's database is
asked for the session's row.

**`sources(harness, *, root=None, modified_since=None, heads=True)`** lists every source
under a root, for a caller that walks a whole corpus. Each file source carries its reuse
keys (`size`, `mtime_ns`, `dev`, `ino`) from the one stat the listing takes of it, and with
`heads` its `Header` read from the file's head up to that size (a Codex rollout's import
marks among its facts). A root that is missing or cannot be listed is `ok=False` with a
reason, never an empty listing; a directory or file under it that cannot be read is named in
`unreadable`, and the walk goes on.

**`subagents(source)`** lists the subagents of one session, in one of four states:
`listed`, `none` (the place was read and holds none), `unreadable` (the place could not be
read, with the reason; never read as none) and `unsupported` (sessions cannot find this
harness's subagents, with the reason; no harness sessions reads answers it today). A
listing says nothing about whether a subagent is still running.

A listing entry from `sources` or `subagents` carries the file's size when it was listed;
a read of the entry stops there, so the listing and the records read under it agree.

Each root is the one the caller hands in, else the harness's configured or default root
(`source.root`). No search reaches a root its caller did not give or configure.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import claude, codex
from .errors import Unreadable
from .shape import CLAUDE, CODEX, HARNESSES, OPENCODE, UNKNOWN
from .source import (WORKFLOW_DIR, Header, Source, claude_header, codex_head,
                     codex_header, opencode_header, root as default_root, sessions_root_of,
                     sidecar_labels, unpack)

HIT = claude.HIT
MISS = claude.MISS
UNREADABLE = claude.UNREADABLE
REFUSED = claude.REFUSED

#: A Claude Code main transcript's name: `<session uuid>.jsonl`.
SESSION_FILE = re.compile(
    r"^([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.jsonl$", re.I)
#: A Claude Code session's directory beside its transcript: `<session uuid>`.
SESSION_DIR = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
#: A Claude Code subagent's file: `agent-<agent id>.jsonl`.
AGENT_FILE = re.compile(r"^agent-([A-Za-z0-9_-]+)\.jsonl$")

#: A subagent listing's states.
LISTED = "listed"
NONE = "none"
UNSUPPORTED = "unsupported"


def _roots(harness: str, root: Any) -> list[Path]:
    """The roots to ask, in order: `root` as given (one path or several), else the
    harness's own."""
    if root is None:
        return [default_root(harness)]
    if isinstance(root, (str, os.PathLike)):
        return [Path(root)]
    return [Path(item) for item in root]


def _harness(harness: str) -> str:
    if harness not in HARNESSES:
        raise ValueError(f"{harness!r} is not a harness sessions reads")
    return harness


# --- find ---------------------------------------------------------------------------------

@dataclass(frozen=True)
class Hit:
    """One source a probe found: its `Source`, and whether it is a compressed Codex
    rollout, which sessions does not read."""

    source: Source
    compressed: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {"path": self.source.path, "session_id": self.source.session_id,
                "agent_id": self.source.agent_id, "compressed": self.compressed}


@dataclass(frozen=True)
class Probe:
    """One root, asked once for one session, and what asking said. `outcome` is `hit`,
    `miss`, `unreadable: <why>` or `refused: <why>`; `hits` holds what a hit found."""

    root: str
    outcome: str
    hits: tuple[Hit, ...] = ()

    @property
    def hit(self) -> bool:
        return self.outcome == HIT

    def as_dict(self) -> dict[str, Any]:
        return {"root": self.root, "outcome": self.outcome,
                "hits": [hit.as_dict() for hit in self.hits]}


@dataclass(frozen=True)
class Found:
    """What `find` found: every root it asked, in order, and the hits of the one that held
    the session. `found` is false when no root did; only a probe whose outcome is `miss`
    says a root holds no record of the session."""

    harness: str
    session_id: str
    probes: tuple[Probe, ...]

    @property
    def hits(self) -> tuple[Hit, ...]:
        for probe in self.probes:
            if probe.hit:
                return probe.hits
        return ()

    @property
    def found(self) -> bool:
        return bool(self.hits)

    def as_dict(self) -> dict[str, Any]:
        return {"harness": self.harness, "session_id": self.session_id,
                "found": self.found, "hits": [hit.as_dict() for hit in self.hits],
                "probes": [probe.as_dict() for probe in self.probes]}


def _refused(session_id: Any) -> str | None:
    if not isinstance(session_id, str) or not claude.SAFE_ID_RE.match(session_id):
        return (f"{REFUSED}: {session_id!r} is not a session id the search looks up")
    return None


def _probe_claude(root: Path, session_id: str) -> Probe:
    probe = claude.probe_root(root, session_id)
    if not probe.hit:
        return Probe(probe.root, probe.outcome)
    return Probe(probe.root, HIT, (Hit(Source(CLAUDE, probe.path, session_id)),))


def _probe_codex(root: Path, thread_id: str) -> Probe:
    try:
        # Codex writes thread ids in lower case, in names and lines alike.
        paths = codex.find_rollout(root, thread_id.lower())
    except OSError as exc:
        return Probe(str(root), f"{UNREADABLE}: {exc.strerror or exc}")
    if not paths:
        return Probe(str(root), MISS)
    hits = []
    for path in paths:
        match = codex.NAME_RE.match(path.name)
        hits.append(Hit(Source(CODEX, str(path), match.group("thread")),
                        compressed=match.group("zst") is not None))
    return Probe(str(root), HIT, tuple(hits))


def _probe_opencode(database: Path, session_id: str) -> Probe:
    from .opencode import MissingSession, Snapshot

    try:
        with Snapshot(database) as snap:
            row = snap.session(session_id)
    except MissingSession:
        return Probe(str(database), MISS)
    except Unreadable as exc:
        return Probe(str(database), f"{UNREADABLE}: {exc.reason}")
    agent_id = row.id if row.parent_id is not None else None
    return Probe(str(database), HIT,
                 (Hit(Source(OPENCODE, str(database), row.id, agent_id)),))


_PROBES = {CLAUDE: _probe_claude, CODEX: _probe_codex, OPENCODE: _probe_opencode}


def find(harness: str, session_id: str, *, root: Any = None) -> Found:
    """Where `harness` keeps `session_id`'s records.

    `root` is one root or several, asked in order until one holds the session: Claude
    Code projects roots, Codex sessions roots, or OpenCode databases. None asks the
    harness's configured or default root (`source.root`), and raises
    `moxi.config.ConfigError` when the config file cannot be read. Never raises for what a
    root holds or whether it can be read: each is a probe's outcome.
    """
    _harness(harness)
    roots = _roots(harness, root)
    why = _refused(session_id)
    if why is not None:
        return Found(harness, str(session_id), tuple(Probe(str(r), why) for r in roots))
    probes = []
    for each in roots:
        probe = _PROBES[harness](each, session_id)
        probes.append(probe)
        if probe.hit:
            break
    return Found(harness, session_id, tuple(probes))


# --- sources --------------------------------------------------------------------------------

@dataclass(frozen=True)
class Listed:
    """One source a listing found.

    * `source`: the `Source` to read. A read of this entry stops at `size`.
    * `kind`: `main` or `subagent` for Claude Code (a workflow agent is a `subagent` with
      a `workflow_id`), `rollout` for Codex, `session` for OpenCode.
    * `rel_path`: the file's path under the root, with `/` between parts; None for an
      OpenCode session.
    * `size`, `mtime_ns`, `dev`, `ino`: the file's reuse keys, from the one stat the
      listing took; None for an OpenCode session, whose rows a cursor follows instead.
    * `compressed`: a compressed Codex rollout (`.jsonl.zst`), which sessions does not
      read.
    * `workflow_id`: a Claude workflow agent's `wf_<id>` directory, else None.
    * `header`: the source's `Header` when the listing read heads and the source could be
      read, else None.
    """

    source: Source
    kind: str
    rel_path: str | None = None
    size: int | None = None
    mtime_ns: int | None = None
    dev: int | None = None
    ino: int | None = None
    compressed: bool = False
    workflow_id: str | None = None
    header: Header | None = None


@dataclass(frozen=True)
class Listing:
    """What one listing of a root found. `ok` false is not an empty listing: the root
    could not be listed at all, and `reason` says why. `unreadable` holds `(path,
    reason)` for each directory or file under the root that could not be read; the
    listing went on past it. `imports` is the Codex imports file as read once for the
    listing (`codex.Imports`), else None. Iterating a listing gives its entries."""

    harness: str
    root: str
    ok: bool
    reason: str | None = None
    entries: tuple[Listed, ...] = ()
    unreadable: tuple[tuple[str, str], ...] = ()
    imports: Any = None

    def __iter__(self) -> Iterator[Listed]:
        return iter(self.entries)

    def __len__(self) -> int:
        return len(self.entries)


def _root_unlistable(root: Path, what: str) -> str | None:
    """Why `root` cannot be listed, or None."""
    try:
        if not root.is_dir():
            how = "does not exist" if not root.exists() else "is not a directory"
            return f"{what} {how} (unmounted?)"
        with os.scandir(root):
            return None
    except OSError as exc:
        return f"{what} unreadable: {exc.strerror or exc}"


class _Walk:
    """One listing's walk: the directories it could not list, and the stat of each file."""

    def __init__(self, root: Path, modified_since: float | None):
        self.root = root
        self.since_ns = (None if modified_since is None
                         else int(modified_since * 1_000_000_000))
        self.unreadable: list[tuple[str, str]] = []

    def rel(self, path: str | Path) -> str:
        return Path(path).relative_to(self.root).as_posix()

    def entries(self, directory: Path) -> list[os.DirEntry]:
        """`directory`'s entries by name, or none when it cannot be listed (noted)."""
        try:
            with os.scandir(directory) as it:
                return sorted(it, key=lambda e: e.name)
        except FileNotFoundError:
            return []
        except OSError as exc:
            self.unreadable.append((self.rel(directory), exc.strerror or str(exc)))
            return []

    def stat(self, entry: os.DirEntry) -> os.stat_result | None:
        """The file's one stat, or None when it is gone, unreadable (noted) or older
        than `modified_since`."""
        try:
            st = entry.stat()
        except FileNotFoundError:
            return None
        except OSError as exc:
            self.unreadable.append((self.rel(entry.path), exc.strerror or str(exc)))
            return None
        if self.since_ns is not None and st.st_mtime_ns < self.since_ns:
            return None
        return st

    def header(self, source: Source, size: int, read) -> Header | None:
        """`read(source, handle, size)` over the file, or None when it cannot be opened
        (noted)."""
        try:
            with open(source.path, "rb") as handle:
                return read(source, handle, size)
        except FileNotFoundError:
            return None
        except OSError as exc:
            self.unreadable.append((self.rel(source.path), exc.strerror or str(exc)))
            return None


def _is_dir(entry: os.DirEntry) -> bool:
    try:
        return entry.is_dir()
    except OSError:
        return False


def _is_file(entry: os.DirEntry) -> bool:
    try:
        return entry.is_file()
    except OSError:
        return False


def _listed(source: Source, kind: str, walk: _Walk, st: os.stat_result, *,
            header: Header | None = None, **more: Any) -> Listed:
    return Listed(source=source, kind=kind, rel_path=walk.rel(source.path),
                  size=st.st_size, mtime_ns=st.st_mtime_ns, dev=st.st_dev, ino=st.st_ino,
                  header=header, **more)


def _claude_agents(walk: _Walk, directory: Path, session_id: str,
                   workflow_id: str | None, heads: bool) -> Iterator[Listed]:
    for entry in walk.entries(directory):
        matched = AGENT_FILE.match(entry.name)
        if not matched or not _is_file(entry):
            continue
        st = walk.stat(entry)
        if st is None:
            continue
        source = Source(CLAUDE, entry.path, session_id, matched.group(1))
        header = walk.header(source, st.st_size, claude_header) if heads else None
        yield _listed(source, "subagent", walk, st, header=header,
                      workflow_id=workflow_id)


def _claude_sources(root: Path, walk: _Walk, heads: bool) -> list[Listed]:
    out: list[Listed] = []
    for project in walk.entries(root):
        if not _is_dir(project):
            continue
        for entry in walk.entries(Path(project.path)):
            matched = SESSION_FILE.match(entry.name)
            if matched:
                if not _is_file(entry):
                    continue
                st = walk.stat(entry)
                if st is None:
                    continue
                source = Source(CLAUDE, entry.path, matched.group(1))
                header = walk.header(source, st.st_size, claude_header) if heads else None
                out.append(_listed(source, "main", walk, st, header=header))
                continue
            if not (SESSION_DIR.match(entry.name) and _is_dir(entry)):
                continue
            subagents = Path(entry.path) / "subagents"
            out.extend(_claude_agents(walk, subagents, entry.name, None, heads))
            for run in walk.entries(subagents / "workflows"):
                if WORKFLOW_DIR.match(run.name) and _is_dir(run):
                    out.extend(_claude_agents(walk, Path(run.path), entry.name, run.name,
                                              heads))
    return out


def _codex_sources(root: Path, walk: _Walk, heads: bool,
                   imports: codex.Imports) -> list[Listed]:
    out: list[Listed] = []

    def read(source: Source, handle, size: int) -> Header:
        return codex_header(source, codex_head(handle, size), imports)

    for year in walk.entries(root):
        if not _is_dir(year):
            continue
        for month in walk.entries(Path(year.path)):
            if not _is_dir(month):
                continue
            for day in walk.entries(Path(month.path)):
                if not _is_dir(day):
                    continue
                for entry in walk.entries(Path(day.path)):
                    matched = codex.NAME_RE.match(entry.name)
                    if not matched or not _is_file(entry):
                        continue
                    st = walk.stat(entry)
                    if st is None:
                        continue
                    compressed = matched.group("zst") is not None
                    source = Source(CODEX, entry.path, matched.group("thread"))
                    header = (walk.header(source, st.st_size, read)
                              if heads and not compressed else None)
                    out.append(_listed(source, "rollout", walk, st, header=header,
                                       compressed=compressed))
    return out


def _opencode_sources(database: Path, modified_since: float | None) -> Listing:
    from .opencode import Snapshot

    try:
        with Snapshot(database) as snap:
            rows = snap.sessions()
    except Unreadable as exc:
        return Listing(OPENCODE, str(database), False, exc.reason)
    entries = []
    for row in rows:
        if (modified_since is not None
                and (row.time_updated is None or row.time_updated < modified_since)):
            continue
        source = Source(OPENCODE, str(database), row.id,
                        row.id if row.parent_id is not None else None)
        entries.append(Listed(source=source, kind="session",
                              header=opencode_header(source, row)))
    return Listing(OPENCODE, str(database), True, entries=tuple(entries))


def sources(harness: str, *, root: str | Path | None = None,
            modified_since: float | None = None, heads: bool = True) -> Listing:
    """Every source of `harness` under one root.

    * Claude Code: each main transcript (`<project>/<session>.jsonl`), each subagent file
      (`<project>/<session>/subagents/agent-<id>.jsonl`) and each workflow agent's file
      (`.../subagents/workflows/wf_<id>/agent-<id>.jsonl`), by path.
    * Codex: each rollout (`<root>/YYYY/MM/DD/rollout-...jsonl`, compressed ones
      included and marked), by path, with the imports file read once for the listing.
    * OpenCode: one source per session row in the database, child sessions included,
      oldest change first.

    `root` is the projects root, sessions root or database; None takes the harness's
    configured or default one (raising `moxi.config.ConfigError` when the config file
    cannot be read). `modified_since` (epoch seconds) keeps only sources modified at or
    after it: a file by its mtime, an OpenCode session by its `time_updated`.

    `heads` reads each file's `Header` from its head, up to the size listed: one open per
    file. Without it a file costs one stat and the listing reads no file. An OpenCode
    session's header comes from its row either way.
    """
    _harness(harness)
    path = Path(root) if root is not None else default_root(harness)
    if harness == OPENCODE:
        return _opencode_sources(path, modified_since)
    what = "projects root" if harness == CLAUDE else "sessions root"
    why = _root_unlistable(path, what)
    if why is not None:
        return Listing(harness, str(path), False, why)
    walk = _Walk(path, modified_since)
    imports = None
    if harness == CLAUDE:
        entries = _claude_sources(path, walk, heads)
    else:
        imports = codex.read_imports(codex.imports_path(path))
        entries = _codex_sources(path, walk, heads, imports)
    entries.sort(key=lambda e: e.rel_path)
    return Listing(harness, str(path), True, entries=tuple(entries),
                   unreadable=tuple(walk.unreadable), imports=imports)


# --- subagents ------------------------------------------------------------------------------

@dataclass(frozen=True)
class Subagent:
    """One subagent a listing found.

    `source` is what to read, and a read of this entry stops at `size` (None for an
    OpenCode child session). The labels: `agent_type`, `description`, `tool_use_id` (the
    parent's call that spawned it), `spawn_depth`, `parent_agent_id` (the subagent that
    spawned it, None under the main session) and `workflow_id` (a Claude workflow's
    `wf_<id>`). A Claude or OpenCode label is None where the harness did not say; a Codex
    label is `UNKNOWN` where Codex wrote nothing for it, and None where it wrote a null.
    """

    agent_id: str
    source: Source
    size: int | None = None
    agent_type: Any = None
    description: Any = None
    tool_use_id: Any = None
    spawn_depth: Any = None
    parent_agent_id: Any = None
    workflow_id: Any = None

    def as_dict(self) -> dict[str, Any]:
        """The entry as JSON-ready data: an unknown label left out and named in
        `"unknown"`."""
        out: dict[str, Any] = {"agent_id": self.agent_id, "path": self.source.path,
                               "session_id": self.source.session_id, "size": self.size}
        unknown = []
        for name in ("agent_type", "description", "tool_use_id", "spawn_depth",
                     "parent_agent_id", "workflow_id"):
            value = getattr(self, name)
            if value is UNKNOWN:
                unknown.append(name)
            else:
                out[name] = value
        if unknown:
            out["unknown"] = unknown
        return out


@dataclass(frozen=True)
class SubagentListing:
    """A session's subagents, taken once.

    `state` is `listed` (with `entries`), `none` (the place was read and holds none),
    `unreadable` (the place could not be read; `reason` says why, and it is never read as
    none) or `unsupported` (sessions cannot find this harness's subagents; `reason` says
    why). `unreadable_parts` holds `(path, reason)` for a part of the place that could not
    be read while the rest was: a Claude directory that could not be listed, or a Codex
    child thread the parent names whose rollout could not be found or read (its thread id
    in place of a path). `header` is the parent's OpenCode header, read in the same
    snapshot as the listing, else None. Iterating gives the entries.
    """

    source: Source
    state: str
    reason: str | None = None
    entries: tuple[Subagent, ...] = ()
    unreadable_parts: tuple[tuple[str, str], ...] = ()
    header: Header | None = field(default=None, compare=False)

    def __iter__(self) -> Iterator[Subagent]:
        return iter(self.entries)

    def __len__(self) -> int:
        return len(self.entries)

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"harness": self.source.harness,
                               "session_id": self.source.session_id,
                               "agent_id": self.source.agent_id, "state": self.state,
                               "entries": [entry.as_dict() for entry in self.entries]}
        if self.reason is not None:
            out["reason"] = self.reason
        if self.unreadable_parts:
            out["unreadable_parts"] = [list(part) for part in self.unreadable_parts]
        return out


def _claude_subagents(source: Source) -> SubagentListing:
    parent = Path(source.path)
    if source.agent_id is not None:
        # A subagent's own file sits in its session's `subagents/` (or a workflow's
        # directory under it): its children are listed beside it, by parent.
        directory = parent.parent
        if WORKFLOW_DIR.match(directory.name):
            directory = directory.parent.parent
        session_dir = directory.parent
    else:
        session_dir = parent.with_suffix("")
        directory = session_dir / "subagents"
    try:
        os.stat(parent)
    except OSError as exc:
        return SubagentListing(source, UNREADABLE,
                               f"the transcript cannot be read: {exc.strerror or exc}")
    try:
        top = sorted(os.scandir(directory), key=lambda e: e.name)
    except (FileNotFoundError, NotADirectoryError):
        return SubagentListing(source, NONE)
    except OSError as exc:
        return SubagentListing(source, UNREADABLE,
                               f"{directory} cannot be listed: {exc.strerror or exc}")
    session_id = source.session_id or session_dir.name
    walk = _Walk(session_dir, None)
    found: list[Listed] = []
    for entry in top:
        matched = AGENT_FILE.match(entry.name)
        if matched and _is_file(entry):
            st = walk.stat(entry)
            if st is not None:
                found.append(_listed(Source(CLAUDE, entry.path, session_id,
                                            matched.group(1)), "subagent", walk, st))
    for run in walk.entries(directory / "workflows"):
        if WORKFLOW_DIR.match(run.name) and _is_dir(run):
            found.extend(_claude_agents(walk, Path(run.path), session_id, run.name, False))
    entries = []
    for item in found:
        labels = sidecar_labels(item.source.path)
        if source.agent_id is not None and labels["parent_agent_id"] != source.agent_id:
            continue
        entries.append(Subagent(agent_id=item.source.agent_id, source=item.source,
                                size=item.size, **labels))
    entries.sort(key=lambda e: e.source.path)
    parts = tuple(walk.unreadable)
    if entries:
        return SubagentListing(source, LISTED, entries=tuple(entries),
                               unreadable_parts=parts)
    if parts:
        return SubagentListing(source, UNREADABLE,
                               "; ".join(f"{path} cannot be listed: {why}"
                                         for path, why in parts),
                               unreadable_parts=parts)
    return SubagentListing(source, NONE)


def _opencode_subagents(source: Source) -> SubagentListing:
    from .opencode import Snapshot

    if source.session_id is None:
        raise ValueError("an OpenCode source names its session to list its subagents")
    try:
        with Snapshot(source.path) as snap:
            row = snap.session(source.session_id)
            children = snap.children(source.session_id)
    except Unreadable as exc:
        return SubagentListing(source, UNREADABLE, exc.reason)
    header = opencode_header(source, row)
    if not children:
        return SubagentListing(source, NONE, header=header)
    entries = tuple(
        Subagent(agent_id=child.agent_id,
                 source=Source(OPENCODE, source.path, child.session_id, child.agent_id),
                 agent_type=child.agent_type, description=child.description,
                 tool_use_id=child.tool_use_id, spawn_depth=child.spawn_depth,
                 parent_agent_id=child.parent_agent_id)
        for child in children)
    return SubagentListing(source, LISTED, entries=entries, header=header)


def _codex_child(child: codex.Spawned, root: Path | None,
                 parent_agent_id: Any) -> tuple[Subagent | None, str | None]:
    """One named child's entry, or why it has none."""
    if root is None:
        return None, "the parent rollout sits under no sessions root"
    probe = find(CODEX, child.thread_id, root=root).probes[0]
    if not probe.hit:
        return None, f"its rollout is not under {root}: {probe.outcome}"
    hit = next((h for h in probe.hits if not h.compressed), probe.hits[0])
    try:
        size = os.stat(hit.source.path).st_size
    except OSError as exc:
        return None, f"its rollout cannot be read: {exc.strerror or exc}"
    link: Any = UNKNOWN
    if not hit.compressed:
        try:
            with open(hit.source.path, "rb") as handle:
                meta = codex_head(handle, size).meta
        except OSError:
            meta = None
        if meta is not None:
            link = codex.spawn_link(meta) or UNKNOWN
    agent_path = child.agent_path
    if agent_path is UNKNOWN and link is not UNKNOWN:
        agent_path = link.agent_path
    depth = UNKNOWN
    if link is not UNKNOWN and isinstance(link.depth, int) and not isinstance(
            link.depth, bool):
        depth = link.depth
    source = Source(CODEX, hit.source.path, hit.source.session_id, hit.source.session_id)
    return Subagent(agent_id=hit.source.session_id, source=source, size=size,
                    agent_type=link.agent_role if link is not UNKNOWN else UNKNOWN,
                    description=agent_path, tool_use_id=child.call_id,
                    spawn_depth=depth, parent_agent_id=parent_agent_id), None


def _codex_subagents(source: Source, bound: int | None) -> SubagentListing:
    if source.path.endswith(".zst"):
        return SubagentListing(source, UNREADABLE,
                               "the rollout is compressed, and sessions reads no zstd")
    try:
        spawns = codex.read_spawns(source.path, thread_id=source.session_id, end=bound)
    except OSError as exc:
        return SubagentListing(source, UNREADABLE,
                               f"the rollout cannot be read: {exc.strerror or exc}")
    if not spawns.children:
        return SubagentListing(source, NONE)
    # A child of a spawned thread names that thread as the agent that spawned it; a child
    # of a main thread names none. With no line 1, which of the two is not known.
    parent_agent_id: Any = UNKNOWN
    if spawns.meta is not None:
        thread = source.session_id or spawns.meta.get("id")
        parent_agent_id = thread if codex.spawn_link(spawns.meta) is not None else None
    root = sessions_root_of(source.path)
    entries = []
    parts = []
    for child in spawns.children:
        entry, why = _codex_child(child, root, parent_agent_id)
        if entry is not None:
            entries.append(entry)
        else:
            parts.append((child.thread_id, why))
    if entries:
        return SubagentListing(source, LISTED, entries=tuple(entries),
                               unreadable_parts=tuple(parts))
    return SubagentListing(source, UNREADABLE,
                           "; ".join(f"child thread {thread}: {why}"
                                     for thread, why in parts),
                           unreadable_parts=tuple(parts))


def subagents(source: Any) -> SubagentListing:
    """The subagents of the session `source` names (a `Source` or a listing entry).

    * Claude Code: the agent files under `<project>/<session>/subagents/` and its
      workflows' directories, each labelled from its `agent-<id>.meta.json` sidecar. A
      sidecar that cannot be read costs its labels, not the entry. No `subagents`
      directory beside a readable transcript is `none`; so is a transcript written before
      subagents had files of their own, whose subagents' lines are in the transcript,
      marked sidechain. For a subagent's own file, the listing holds the subagents it
      spawned (by their sidecars' parent).
    * Codex: the child threads the rollout's spawn items name (`codex.read_spawns`),
      each found by its thread id under the rollout's own sessions root, with no walk,
      and labelled from the spawn items and the child's line 1. A child named but not
      found is named in `unreadable_parts`. For a spawned thread's own rollout, the
      listing holds the threads it spawned.
    * OpenCode: the session's child sessions, read in one snapshot with its row.

    Never raises for what the place holds or whether it can be read. Raises ValueError for
    something that is not a source, or an OpenCode source that names no session.
    """
    source, bound = unpack(source)
    if source.harness == CODEX:
        return _codex_subagents(source, bound)
    if source.harness == OPENCODE:
        return _opencode_subagents(source)
    return _claude_subagents(source)


__all__ = ["find", "sources", "subagents", "Found", "Probe", "Hit", "Listing", "Listed",
           "SubagentListing", "Subagent", "HIT", "MISS", "UNREADABLE", "REFUSED", "LISTED",
           "NONE", "UNSUPPORTED"]

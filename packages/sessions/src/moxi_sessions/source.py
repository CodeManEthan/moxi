"""What a read names and what it returns beside its records: `Source`, `Header`, the roots.

A `Source` names one record store unit: a Claude Code transcript or subagent file, a Codex
rollout, or OpenCode's database (with the session read in it). A `Header` holds the
session facts a read found beside its records, taken from the same snapshot as them.

**The roots.** Each harness has one root a read starts from when its caller hands none in:
Claude Code's projects root, Codex's sessions root and OpenCode's database. Each is the
config key that names it when one does, else the harness's own default over the
environment the caller hands in (this process's when it hands in none). No reader reaches a
root its caller did not give or configure.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, BinaryIO, NamedTuple

from . import claude, codex
from .shape import CLAUDE, CODEX, HARNESSES, OPENCODE, UNKNOWN

#: How far a header looks into a file's head for its facts: the first lines, up to this
#: many lines and this many bytes. A fact not written there is unknown.
HEAD_LINES = 64
HEAD_BYTES = 262144

#: A workflow agent's directory under a session's `subagents/workflows/`.
WORKFLOW_DIR = re.compile(r"^wf_[A-Za-z0-9_-]+$")


class Source(NamedTuple):
    """One record store unit: `(harness, path, session_id, agent_id)`.

    `path` is the file, or OpenCode's database. `session_id` is the session the records
    belong to (a Claude subagent's file carries its parent session's id; None reads an
    OpenCode database whole in `records_since`). `agent_id` is the subagent's id for a
    subagent's file or an OpenCode child session, else None.

    A read of a `Source` on its own has no listed-size bound: it reads up to the size the
    file has when the read opens it. Only a read of a listing entry (from `sources()` or
    `subagents()`) stops at the size the listing saw, so the listing and the records read
    under it agree.
    """

    harness: str
    path: str
    session_id: str | None
    agent_id: str | None = None


def unpack(item: Any) -> tuple[Source, int | None]:
    """A read's source and its size bound: a listing entry carries both, a `Source` has
    no bound. Raises ValueError for anything else, or a harness sessions does not read."""
    bound = None
    if not isinstance(item, Source) and isinstance(getattr(item, "source", None), Source):
        bound = item.size
        item = item.source
    if not isinstance(item, Source):
        raise ValueError(f"{item!r} is not a Source or a listing entry")
    if item.harness not in HARNESSES:
        raise ValueError(f"{item.harness!r} is not a harness sessions reads")
    return item, bound


# --- the header ---------------------------------------------------------------------------

@dataclass(frozen=True, kw_only=True)
class Header:
    """The session facts a read found beside its records.

    * `harness`, `session_id`, `agent_id`: whose records these are.
    * `cwd`, `title`, `version`: the session's directory, its title and the harness's
      version as written (Claude's `version`, Codex line 1's `cli_version`, OpenCode's
      session `version`). `title` is OpenCode's alone. Codex writes no title. Claude Code
      writes a name the session was given as `custom-title` lines, which can stand among a
      file's first lines and anywhere after them: they come as `title` markers among the
      records, the header does not read them, and `claude_head_name` reads the newest one
      in a file's head.
    * `started`: when the session started, in epoch seconds: the time of the first of a
      Claude file's first lines that has one, Codex line 1's `timestamp`, OpenCode's
      `time_created`.
    * `parent`: the session this one came from: a Claude subagent's parent session, an
      OpenCode child's parent, a forked Codex thread's `forked_from_id` (a spawned
      thread's parent when line 1 has no `forked_from_id`); else None.
    * Claude: `entrypoint` (`cli`, `sdk-cli`, ...), as the first line that has one writes
      it; `labels`, a subagent's sidecar labels (`agent_type`, `description`,
      `tool_use_id`, `spawn_depth`, `parent_agent_id`, `workflow_id`), None for a main
      transcript. A sidecar that cannot be read costs its labels (each None), not the read.
    * Codex: `codex_source` (line 1's `source`), `meta` (line 1's `session_meta` facts),
      `import_marks` (`codex.ImportMarks`) and `spawn`, the spawn link line 1 names for a
      thread another thread spawned (`codex.SpawnLink`: parent thread, depth, agent path,
      nickname and role), None for a thread no thread spawned, `UNKNOWN` with no line 1.
    * OpenCode: `session_row` (`opencode.SessionRow`).

    A fact the source did not write is `UNKNOWN`; an object a harness never has is None.
    """

    harness: str
    session_id: Any = UNKNOWN
    agent_id: str | None = None
    cwd: Any = UNKNOWN
    title: Any = UNKNOWN
    version: Any = UNKNOWN
    started: Any = UNKNOWN
    parent: Any = None
    entrypoint: Any = UNKNOWN
    labels: Mapping[str, Any] | None = None
    codex_source: Any = UNKNOWN
    meta: Mapping[str, Any] | None = None
    import_marks: Any = None
    spawn: Any = None
    session_row: Any = None
    extra: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        """The header as JSON-ready data: an unknown fact left out and named in
        `"unknown"`."""
        from .shape import _plain

        out: dict[str, Any] = {}
        unknown = []
        for name in ("harness", "session_id", "agent_id", "cwd", "title", "version",
                     "started", "parent", "entrypoint", "labels", "codex_source", "meta"):
            value = getattr(self, name)
            if value is UNKNOWN:
                unknown.append(name)
            else:
                out[name] = _plain(value)
        if self.import_marks is not None:
            out["import_marks"] = self.import_marks.as_dict()
        if self.spawn is UNKNOWN:
            unknown.append("spawn")
        elif self.spawn is not None:
            out["spawn"] = self.spawn.as_dict()
        if self.session_row is not None:
            row = self.session_row
            out["session_row"] = {name: _plain(getattr(row, name))
                                  for name in row.__dataclass_fields__
                                  if getattr(row, name) is not UNKNOWN}
        if unknown:
            out["unknown"] = unknown
        return out


def _head_lines(handle: BinaryIO, end: int, lines: int = HEAD_LINES):
    """A file's first lines as written, up to `end` and the head's bounds."""
    handle.seek(0)
    offset = 0
    stop = min(end, HEAD_BYTES)
    for _ in range(lines):
        if offset >= stop:
            return
        data = handle.readline(min(end - offset, HEAD_BYTES))
        if not data:
            return
        offset += len(data)
        if not data.endswith(b"\n") and offset < end:
            return                       # a line longer than the head's bound
        yield data


def _head_entries(handle: BinaryIO, end: int, lines: int = HEAD_LINES):
    """The decoded entries of a file's first lines, up to `end` and the head's bounds."""
    for data in _head_lines(handle, end, lines):
        entry = claude.parse_line(data)
        if entry is not None:
            yield entry


def sidecar_labels(path: str | Path) -> dict[str, Any]:
    """A Claude subagent file's sidecar labels (`agent-<id>.meta.json` beside it): each
    None where the sidecar is missing, unreadable, or does not say. Never raises."""
    path = Path(path)
    labels: dict[str, Any] = {"agent_type": None, "description": None, "tool_use_id": None,
                              "spawn_depth": None, "parent_agent_id": None,
                              "workflow_id": (path.parent.name
                                              if WORKFLOW_DIR.match(path.parent.name)
                                              else None)}
    try:
        meta = json.loads(path.with_suffix(".meta.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return labels
    if not isinstance(meta, dict):
        return labels
    for key, name in (("agentType", "agent_type"), ("description", "description"),
                      ("toolUseId", "tool_use_id"), ("parentAgentId", "parent_agent_id")):
        if isinstance(meta.get(key), str):
            labels[name] = meta[key]
    depth = meta.get("spawnDepth")
    if isinstance(depth, int) and not isinstance(depth, bool):
        labels["spawn_depth"] = depth
    return labels


def claude_header(source: Source, handle: BinaryIO, end: int) -> Header:
    """A Claude transcript's header from its head: the first `cwd`, `version`,
    `entrypoint` and `sessionId` the first lines write, the first time one of them is
    stamped with, and a subagent's sidecar."""
    found: dict[str, Any] = {}
    for entry in _head_entries(handle, end):
        for key in ("cwd", "version", "entrypoint", "sessionId"):
            if key not in found and isinstance(entry.get(key), str):
                found[key] = entry[key]
        if "started" not in found:
            stamp = claude.parse_timestamp(entry.get("timestamp"))
            if stamp is not None:
                found["started"] = stamp
        if len(found) == 5:
            break
    session_id = source.session_id if source.session_id is not None else found.get(
        "sessionId", UNKNOWN)
    subagent = source.agent_id is not None
    return Header(harness=CLAUDE, session_id=session_id, agent_id=source.agent_id,
                  cwd=found.get("cwd", UNKNOWN), version=found.get("version", UNKNOWN),
                  started=found.get("started", UNKNOWN),
                  parent=session_id if subagent else None,
                  entrypoint=found.get("entrypoint", UNKNOWN),
                  labels=sidecar_labels(source.path) if subagent else None)


def claude_named_head(handle: BinaryIO, end: int) -> tuple[str | None, bool]:
    """One read of the first lines a header reads, for a Claude transcript's name:
    `(name, complete)`.

    `name` is the name those lines give the session: their newest `custom-title` line,
    or None when they hold none. A title Claude Code made up itself (`ai-title`) is not a
    name. A name written only past the head is not found here; it comes as a `title`
    marker among the records.

    `complete` is true when the head's bounds, not the file's end at `end`, stopped the
    read. Claude Code writes a transcript by adding lines at its end, and no transcript
    sessions has read had a line changed where it stood; on that, a complete head is
    taken to read the same as the file grows. One the file's end stopped can still gain
    lines, a name among them. A caller that keeps a head checks that the file is the
    same one and no smaller before it relies on this."""
    name = None
    count = size = 0
    for data in _head_lines(handle, end):
        count += 1
        size += len(data)
        if b'"custom-title"' not in data:
            continue
        entry = claude.parse_line(data)
        if (entry is not None and entry.get("type") == "custom-title"
                and isinstance(entry.get("customTitle"), str) and entry["customTitle"]):
            name = entry["customTitle"]
    return name, count >= HEAD_LINES or size >= HEAD_BYTES or size < end


def claude_head_name(handle: BinaryIO, end: int) -> str | None:
    """The name a Claude transcript's head gives its session, or None
    (`claude_named_head`)."""
    return claude_named_head(handle, end)[0]


#: The `session_meta` facts a Codex header carries in `meta`.
META_FACTS = ("timestamp", "history_mode", "originator", "cli_version", "source",
              "model_provider", "context_window", "forked_from_id")


def codex_head(handle: BinaryIO, end: int) -> codex.Head:
    """A rollout's head on an open handle: line 1's `session_meta` payload and the first
    turn id the first lines name (None when none of them does)."""
    meta = None
    for number, entry in enumerate(_head_entries(handle, end), start=1):
        payload = entry.get("payload")
        if not isinstance(payload, dict):
            continue
        if number == 1 and entry.get("type") == "session_meta":
            meta = payload
        turn = payload.get("turn_id")
        if isinstance(turn, str) and turn:
            return codex.Head(meta, turn)
    return codex.Head(meta, None)


def sessions_root_of(path: str | Path) -> Path | None:
    """The sessions root a rollout sits under (`<root>/YYYY/MM/DD/<file>`), or None."""
    parents = Path(path).parents
    return parents[3] if len(parents) > 3 else None


def codex_header(source: Source, head: codex.Head,
                 imports: codex.Imports | None = None) -> Header:
    """A Codex rollout's header from its head and the imports file (read beside the
    rollout's sessions root when not handed in)."""
    if imports is None:
        root = sessions_root_of(source.path)
        imports = (codex.read_imports(codex.imports_path(root)) if root is not None
                   else codex.Imports("", None, codex.IMPORTS_NOT_FOUND))
    meta = head.meta
    thread = source.session_id
    if thread is None and meta is not None:
        thread = meta.get("id") if isinstance(meta.get("id"), str) else None
    facts = None
    if meta is not None:
        facts = {name: meta[name] for name in META_FACTS if name in meta}
        facts["unlisted_keys"] = tuple(k for k in meta if k not in codex.META_KEYS)
    forked = meta.get("forked_from_id") if meta is not None else None
    spawn = codex.spawn_link(meta) if meta is not None else UNKNOWN
    started = claude.parse_timestamp(meta.get("timestamp")) if meta is not None else None
    if not isinstance(forked, str) and spawn is not None and spawn is not UNKNOWN:
        forked = spawn.parent_thread_id
    return Header(
        harness=CODEX, session_id=thread if thread is not None else UNKNOWN,
        agent_id=source.agent_id,
        cwd=meta.get("cwd", UNKNOWN) if meta is not None else UNKNOWN,
        version=meta.get("cli_version", UNKNOWN) if meta is not None else UNKNOWN,
        started=UNKNOWN if started is None else started,
        parent=forked if isinstance(forked, str) else None,
        codex_source=meta.get("source", UNKNOWN) if meta is not None else UNKNOWN,
        meta=facts, import_marks=codex.import_marks(head, imports, thread_id=thread),
        spawn=spawn)


def opencode_header(source: Source, row: Any) -> Header:
    """An OpenCode session's header from its row; with no row (a read of the database
    whole), a header naming the harness alone."""
    if row is None:
        return Header(harness=OPENCODE, session_id=source.session_id,
                      agent_id=source.agent_id)
    return Header(harness=OPENCODE, session_id=row.id,
                  agent_id=row.id if row.parent_id is not None else None,
                  cwd=row.directory, title=row.title, version=row.version,
                  started=UNKNOWN if row.time_created is None else row.time_created,
                  parent=row.parent_id, session_row=row)


def file_header(source: Source, handle: BinaryIO, end: int) -> Header:
    """The header of a Claude or Codex file, read from its head on `handle` up to
    `end`."""
    if source.harness == CLAUDE:
        return claude_header(source, handle, end)
    return codex_header(source, codex_head(handle, end))


# --- the roots ------------------------------------------------------------------------------

#: The config key that names each harness's root.
ROOT_KEYS = {CLAUDE: "claude.projects", CODEX: "codex.sessions",
             OPENCODE: "opencode.database"}


def _home(environ: Mapping[str, str]) -> str:
    return environ.get("HOME") or str(Path.home())


def _expand(value: str, home: str) -> Path:
    if value == "~" or value.startswith("~/"):
        value = home + value[1:]
    return Path(value)


def codex_root(environ: Mapping[str, str] | None = None) -> Path:
    """Codex's sessions root: `${CODEX_HOME:-~/.codex}/sessions` over `environ`. With
    None, config `codex.sessions` when it is set, else the rule over this process's
    environment. Raises `moxi.config.ConfigError` when the config file cannot be read."""
    if environ is None:
        configured = _configured(CODEX)
        if configured is not None:
            return configured
        environ = os.environ
    home = _home(environ)
    base = environ.get("CODEX_HOME")
    return (_expand(base, home) if base else Path(home) / ".codex") / "sessions"


def opencode_root(environ: Mapping[str, str] | None = None) -> Path:
    """OpenCode's database: `${XDG_DATA_HOME:-~/.local/share}/opencode/opencode.db` over
    `environ`. With None, config `opencode.database` when it is set, else the rule over
    this process's environment. A caller that knows the database its OpenCode server uses
    (`opencode db path` under the server's environment) hands that in instead."""
    if environ is None:
        configured = _configured(OPENCODE)
        if configured is not None:
            return configured
        environ = os.environ
    home = _home(environ)
    data = environ.get("XDG_DATA_HOME")
    base = _expand(data, home) if data else Path(home) / ".local" / "share"
    return base / "opencode" / "opencode.db"


def root(harness: str, env: Mapping[str, str] | None = None) -> Path:
    """The harness's root (see `claude.projects_root`, `codex_root`, `opencode_root`)."""
    if harness == CLAUDE:
        return claude.projects_root(env)
    if harness == CODEX:
        return codex_root(env)
    if harness == OPENCODE:
        return opencode_root(env)
    raise ValueError(f"{harness!r} is not a harness sessions reads")


def _configured(harness: str) -> Path | None:
    from .settings import configured

    return configured(ROOT_KEYS[harness])


@dataclass(frozen=True)
class RootOrigin:
    """A harness's root, where it came from (`config`, `environment` or `default`), the
    detail in words, and whether a config key names it."""

    harness: str
    path: Path
    source: str
    detail: str

    @property
    def configured(self) -> bool:
        return self.source == "config"

    def as_dict(self) -> dict[str, str]:
        return {"harness": self.harness, "root": str(self.path), "source": self.source,
                "detail": self.detail}


def root_origin(harness: str) -> RootOrigin:
    """The root a read with no root of its own uses, and where it came from. Raises
    `moxi.config.ConfigError` when the config file cannot be read."""
    from .settings import current

    settings = current()
    key = ROOT_KEYS[harness]
    path = root(harness)
    if settings[key] is not None:
        return RootOrigin(harness, path, "config", f"{key} in {settings.file}")
    if harness == CLAUDE:
        if os.environ.get("CLAUDE_CONFIG_DIR"):
            return RootOrigin(harness, path, "environment", "CLAUDE_CONFIG_DIR/projects")
        return RootOrigin(harness, path, "default",
                          "~/.claude/projects (CLAUDE_CONFIG_DIR is unset)")
    if harness == CODEX:
        if os.environ.get("CODEX_HOME"):
            return RootOrigin(harness, path, "environment", "CODEX_HOME/sessions")
        return RootOrigin(harness, path, "default",
                          "~/.codex/sessions (CODEX_HOME is unset)")
    if os.environ.get("XDG_DATA_HOME"):
        return RootOrigin(harness, path, "environment",
                          "XDG_DATA_HOME/opencode/opencode.db")
    return RootOrigin(harness, path, "default",
                      "~/.local/share/opencode/opencode.db (XDG_DATA_HOME is unset)")


__all__ = ["HEAD_LINES", "HEAD_BYTES", "Source", "Header", "unpack", "sidecar_labels",
           "file_header", "claude_header", "claude_head_name", "claude_named_head",
           "codex_header", "codex_head", "opencode_header",
           "codex_root", "opencode_root", "root", "root_origin", "RootOrigin", "ROOT_KEYS"]

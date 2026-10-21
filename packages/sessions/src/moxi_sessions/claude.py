"""Claude Code's transcript: how a line decodes, where a session's file lives, and the root.

This module owns three facts for every Moxi module, and no other module owns any of them:

* **How a transcript line decodes** (`parse_line`, `iter_entries`, `stream_entries`,
  `MALFORMED`). Every reader walks the same decoder; Codex's rollout reader in `codex`
  decodes through it too. A line that will not decode is yielded as a `MALFORMED` entry
  carrying its raw text, never dropped, so a reader can see that a file is not complete.
* **Where a session's transcript lives** (`probe_root`, `find_for_session`,
  `search_roots`). Claude Code keys a transcript's directory on the session's launch
  directory, which nothing can derive afterwards, so finding one is a search over a root's
  project directories, never a derivation. Each root is asked once and what asking said is
  recorded: a hit, a miss, a root that could not be read, or an id the search refuses.
* **The projects root** (`projects_root`): `${CLAUDE_CONFIG_DIR:-~/.claude}/projects` in
  the environment it is handed, which is the session's own when the session reports it.

Nothing here decides anything about a session. Which lines count, and what a miss means,
are the caller's.

The session id. Claude Code writes `sessionId` on every line; older transcripts carry
`session_id`. A reader that wants the id off a line reads `sessionId` first and falls back
to `session_id`, since a key that was ever written stays readable.
"""

from __future__ import annotations

import json
import os
import re
import threading
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

#: The `type` of an entry for a line that would not decode. It carries `lineno` and `raw`.
MALFORMED = "<malformed>"

#: What a session id may hold for the search: no path separator, no glob character, no
#: leading dot. Claude Code's ids are UUIDs; an id this refuses is never searched for.
SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_-][A-Za-z0-9_.-]*$")


# --- the decoder ----------------------------------------------------------------------

def parse_line(line: str | bytes) -> dict | None:
    """One raw line, text or bytes, to its entry dict; None when the line is blank, will
    not decode, or is not a JSON object. Bytes are decoded as UTF-8, so a reader handed
    raw lines (`read_back`) decodes them here rather than with a parser of its own."""
    line = line.strip()
    if not line:
        return None
    try:
        obj = json.loads(line)
    except ValueError:                   # JSONDecodeError and UnicodeDecodeError both
        return None
    return obj if isinstance(obj, dict) else None


def iter_entries(text: str) -> Iterator[dict]:
    """Every entry of a whole file's text, in file order.

    Each entry gains `lineno`, its 1-based line number, unless it already has one. A line
    that will not decode is yielded as `{"type": MALFORMED, "lineno": n, "raw": line}`.
    Blank lines are skipped.
    """
    for lineno, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        obj = parse_line(line)
        if obj is None:
            yield {"type": MALFORMED, "lineno": lineno, "raw": line}
        else:
            obj.setdefault("lineno", lineno)
            yield obj


def stream_entries(handle: Iterable[str], *, start_line: int = 0) -> Iterator[dict]:
    """Every entry from an open handle (any iterable of text lines), from `start_line` on.

    `start_line` is the count of lines already consumed: the 0-based index of the first
    line to read, so an incremental reader resumes where it stopped. `lineno` stays the
    absolute 1-based line number in the file. The handle is the caller's: this opens and
    closes nothing.

    Same bytes and `start_line=0` give the same entries as `iter_entries`, MALFORMED ones
    included. One difference at the margin: a file handle splits only on `\\n`, while
    `str.splitlines` also splits on `\\r`, `\\x0b`, `\\x0c` and their kind, which no
    transcript writes.
    """
    for index, raw in enumerate(handle):
        if index < start_line:
            continue
        line = raw.rstrip("\n").rstrip("\r")
        if not line.strip():
            continue
        obj = parse_line(line)
        if obj is None:
            yield {"type": MALFORMED, "lineno": index + 1, "raw": line}
        else:
            obj.setdefault("lineno", index + 1)
            yield obj


def parse_timestamp(value: object) -> float | None:
    """An ISO 8601 timestamp to epoch seconds; a stamp with no zone is UTC. None for
    anything that is not one, a non-string included, so a reader that promises not to
    raise does not."""
    if not value or not isinstance(value, str):
        return None
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.timestamp()


# --- finding a session's transcript ---------------------------------------------------

HIT = "hit"
MISS = "miss"
UNREADABLE = "unreadable"
REFUSED = "refused"


@dataclass(frozen=True)
class RootProbe:
    """One root, asked once for one session id, and what asking said.

    `outcome` is `hit` (with `path`), `miss` (the root was read and holds no file for the
    id), `unreadable: <why>` (the root could not be read: an absent or unmounted directory
    is this, never a miss) or `refused: <why>` (the id is not one the search looks up, so
    nothing was looked at). Only `miss` says the file is absent.
    """

    root: str
    outcome: str
    path: str | None = None

    @property
    def hit(self) -> bool:
        return self.outcome == HIT

    def path_or_none(self) -> Path | None:
        return Path(self.path) if self.path else None

    def as_dict(self) -> dict:
        return {"root": self.root, "outcome": self.outcome, "path": self.path}


#: (root, session id) -> the transcript path found there. Searching a root's project
#: directories is the costly part of a read, so each process keeps what it has found; a
#: kept path that is no longer a file is searched for again.
_found: dict[tuple[str, str], Path] = {}
_found_lock = threading.Lock()


def _unreadable(root: Path) -> str | None:
    """Why `root` cannot be listed, or None when it can."""
    try:
        if not root.is_dir():
            return "not a directory (absent or unmounted?)"
        if not os.access(root, os.R_OK | os.X_OK):
            return "permission denied"
        with os.scandir(root):
            return None
    except OSError as exc:
        return str(exc)


def probe_root(root: str | Path, session_id: str) -> RootProbe:
    """Ask one root for one session's transcript, `<root>/<project>/<session_id>.jsonl`.

    Never raises: every OSError is a recorded outcome. When the id is in more than one
    project directory, the newest by modification time is the hit. A hit is kept for the
    process (see `_found`) and returned again while it is still a file.
    """
    root = Path(root)
    label = str(root)
    if not isinstance(session_id, str) or not SAFE_ID_RE.match(session_id):
        return RootProbe(label, f"{REFUSED}: {session_id!r} is not a session id the "
                                "search looks up")
    why = _unreadable(root)
    if why is not None:
        return RootProbe(label, f"{UNREADABLE}: {why}")
    key = (label, session_id)
    try:
        with _found_lock:
            kept = _found.get(key)
        if kept is not None and kept.is_file():
            return RootProbe(label, HIT, str(kept))
        matches = [p for p in root.glob(f"*/{session_id}.jsonl") if p.is_file()]
        if not matches:
            with _found_lock:
                _found.pop(key, None)
            return RootProbe(label, MISS)
        path = max(matches, key=lambda p: p.stat().st_mtime)
    except OSError as exc:
        return RootProbe(label, f"{UNREADABLE}: {exc}")
    with _found_lock:
        _found[key] = path
    return RootProbe(label, HIT, str(path))


def find_for_session(projects_root: str | Path, session_id: str) -> Path | None:
    """The session's transcript under one projects root, or None.

    None covers a miss, a root that could not be read and a refused id alike. A caller
    that decides anything from a transcript's absence asks `probe_root`, whose outcome
    tells the three apart.
    """
    return probe_root(projects_root, session_id).path_or_none()


def search_roots(*, live: str | Path, archives: Iterable[str | Path] | None = None,
                 extra: Iterable[str | Path] | None = None) -> list[Path]:
    """The ordered roots to probe: `live` first, then `archives`, then `extra`.

    `live` is required: the caller knows its live root (`projects_root` gives the usual
    one) and a default here would reach past a caller's redirect. Live comes first because
    a session in both places is a stale copy in the archive; `extra` (a command's
    `--root`) appends, so the default order holds whatever is added. `~` is expanded on
    `archives` and `extra`; a root named twice is probed once.
    """
    roots: list[Path] = [Path(live)]
    for group in (archives or (), extra or ()):
        roots.extend(Path(item).expanduser() for item in group)
    seen: set[str] = set()
    ordered: list[Path] = []
    for root in roots:
        if str(root) not in seen:
            seen.add(str(root))
            ordered.append(root)
    return ordered


# --- the projects root ----------------------------------------------------------------

def projects_root(environ: Mapping[str, str] | None = None) -> Path:
    """Where Claude Code writes transcripts, by its own rule over an environment.

    With `environ`: `${CLAUDE_CONFIG_DIR:-~/.claude}/projects` as that environment says,
    `~` and the home directory taken from its `HOME`. A process that reports its session's
    root passes its own environment, which is the session's.

    With None: the root for a session that stated none, which is the config key
    `claude.projects` when it is set, else the same rule over this process's environment.
    Raises `moxi.config.ConfigError` when the config file cannot be read.
    """
    if environ is None:
        from .settings import configured_projects

        configured = configured_projects()
        if configured is not None:
            return configured
        environ = os.environ
    home = environ.get("HOME") or str(Path.home())
    config = environ.get("CLAUDE_CONFIG_DIR")
    if config:
        if config == "~" or config.startswith("~/"):
            config = home + config[1:]
        base = Path(config)
    else:
        base = Path(home) / ".claude"
    return base / "projects"

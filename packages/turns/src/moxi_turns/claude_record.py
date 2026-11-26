"""The name a person typed for a Claude Code session, read from Claude Code's own record
of the process.

Claude Code keeps one record per running interactive process, `sessions/<pid>.json` in its
own folder (`moxi.paths.claude_home`: `$CLAUDE_CONFIG_DIR`, else `.claude` in the home
folder, on Windows, macOS and Linux alike). It rewrites the record at a `/clear`, a resume,
a `/rename` and each change of status, and removes it when the process exits. Among its
fields are `sessionId`, `name` and `nameSource`, which says where the name came from:

| `nameSource` | the name |
|---|---|
| absent | given at start (`--name`) |
| `user` | typed with `/rename` |
| `derived` | made from the folder, with no name given |
| `auto` | written by the model |
| `collision` | a name changed to tell it from another session's |
| `peer`, `hook` | set by another session, or by a hook |

Only the first two are a name the person typed, so only they are read. A record whose
`sessionId` is not the session's current id is not read either: it may be one a process
that has ended left behind, under a pid used again.

The record is the harness's own file, read as files are read; turns writes nothing there.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from pathlib import Path

from moxi import paths

log = logging.getLogger("moxi.turns.claude")

#: The `nameSource` values of a name the person typed; None is a record with none.
TYPED_SOURCES = (None, "user")
#: The largest record read, in bytes; Claude Code reads no more of one.
RECORD_MAX_BYTES = 262_144


def record_path(pid: int, env: Mapping[str, str] | None = None) -> Path:
    """Where Claude Code keeps the record of process `pid`, placed by `env` (`os.environ`
    without one). Raises `LookupError` when nothing places the home folder."""
    return paths.claude_home(env) / "sessions" / f"{pid}.json"


def typed_name(pid: int | None, session_id: str | None,
               env: Mapping[str, str] | None = None) -> str | None:
    """The name typed for the Claude Code session `session_id` running as `pid`, or None:
    no pid or session id, no record, a record that can't be read or is of another session
    id, no name, or a name the person did not type."""
    if pid is None or pid <= 0 or not session_id:
        return None
    try:
        path = record_path(pid, env)
        with path.open("rb") as record_fh:
            raw = record_fh.read(RECORD_MAX_BYTES + 1)
    except (OSError, LookupError):
        return None
    if len(raw) > RECORD_MAX_BYTES:
        return None
    try:
        record = json.loads(raw)
    except ValueError:
        log.info("Claude Code's record of pid %s is not JSON", pid)
        return None
    if not isinstance(record, dict) or record.get("sessionId") != session_id:
        return None
    source = record.get("nameSource")
    name = record.get("name")
    if source not in TYPED_SOURCES or not isinstance(name, str):
        return None
    return name.strip() or None


__all__ = ["RECORD_MAX_BYTES", "TYPED_SOURCES", "record_path", "typed_name"]

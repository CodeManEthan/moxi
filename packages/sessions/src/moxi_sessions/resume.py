"""The command each harness takes to pick a session up again.

A line is in the table only where the harness's own help text names the option or the
subcommand that takes a session id. A harness with no line answers None: nothing is made
up for it.

* Claude Code: `claude --resume <id>`. Claude Code looks a session up under the folder it
  is run in, so the command is run in the session's folder.
* Codex: `codex resume <id>`. Codex finds a thread by its id from any folder.
* OpenCode: `opencode --session <id>`, run in the session's folder.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from typing import Any

from .shape import CLAUDE, CODEX, OPENCODE


@dataclass(frozen=True)
class Line:
    """One harness's resume command: the words before the session id, and whether the
    command is run in the session's folder."""

    words: tuple[str, ...]
    in_folder: bool


#: The resume command of each harness that has one.
LINES: dict[str, Line] = {
    CLAUDE: Line(("claude", "--resume"), True),
    CODEX: Line(("codex", "resume"), False),
    OPENCODE: Line(("opencode", "--session"), True),
}


def resume(harness: str, session: Any, folder: str | None = None) -> dict[str, Any] | None:
    """`{"command", "folder"}` for picking `session` up again in `harness`, or None for a
    harness with no line in the table and for a session with no id.

    `command` is one shell line; the id is quoted where it holds a character a shell
    would read. `folder` is where to run it: the session's folder as given for a harness
    whose command is run there (None when the folder is not known), and None for a
    harness whose command runs anywhere."""
    line = LINES.get(harness)
    if line is None or not isinstance(session, str) or not session:
        return None
    return {"command": " ".join((*line.words, shlex.quote(session))),
            "folder": folder if line.in_folder else None}


__all__ = ["Line", "LINES", "resume"]

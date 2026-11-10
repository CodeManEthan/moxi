"""The command each harness takes to pick a session up again.

A line is in the table only where the harness's own help text names the option or the
subcommand that takes a session id. A harness with no line answers None: nothing is made
up for it. Each line says whether its command must be run in the session's folder. Where
it must and the folder is not known, the answer is None too: a command is not handed out
without the place it works in.

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
    """`{"command", "folder"}` for picking `session` up again in `harness`. None for a
    harness with no line in the table, for a session with no id, and for a harness whose
    command must be run in the session's folder when `folder` is not known.

    `command` is one shell line; the id is quoted where it holds a character a shell
    would read. `folder` is where to run it: the session's folder for a harness whose
    command is run there, and None for a harness whose command runs anywhere, so a None
    `folder` beside a command means the command needs no folder."""
    line = LINES.get(harness)
    if line is None or not isinstance(session, str) or not session:
        return None
    if line.in_folder and not folder:
        return None
    return {"command": " ".join((*line.words, shlex.quote(session))),
            "folder": folder if line.in_folder else None}


__all__ = ["Line", "LINES", "resume"]

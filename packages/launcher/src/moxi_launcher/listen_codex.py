"""The Codex seat listener: a Codex session's identity block and seat instructions, in
start context.

A Claude session carries both in the system prompt file its launch appends. A Codex session
under turns' host gets them from this `start` row instead, at every source (startup,
resume, clear, compaction), as Claude's appended prompt holds across all of them. It
answers only for a Codex launch this endpoint holds (`listeners.claim`), and None for
Claude and for an endpoint that did not claim.

The instructions are the launch's `instructions` value, or the text of its
`instructions_file`, read at each start. turns cuts start context at the launcher's share
and marks the cut.
"""

from __future__ import annotations

import logging
from pathlib import Path

from . import handoffs, listeners

log = logging.getLogger("moxi.launcher.listeners")

HEADER = "--- LAUNCH IDENTITY (stamped at launch) ---"
_WIDTH = len("start posture: ")


def identity_block(*, session_id: str | None, cwd: str, posture: str,
                   project_key: str | None = None, identity: str | None = None,
                   scope: str | None = None, handoff_dir: str | Path | None = None,
                   agent_role: str | None = None) -> str:
    """The identity block a launched session starts with. A line appears only when it
    carries a value."""
    lines = [("session id:", session_id), ("project cwd:", cwd),
             ("project key:", project_key),
             ("identity:", f"{identity}@{scope}"
              if identity and scope and "@" not in identity else identity),
             ("handoff dir:", handoff_dir), ("start posture:", posture),
             ("agent role:", agent_role)]
    return "\n".join([HEADER, *(f"{label:<{_WIDTH}}{value}" for label, value in lines
                                if value)])


def instructions(values) -> str | None:
    """The seat's instructions: inline, or read from its file now."""
    text = values.get("instructions")
    if text:
        return str(text)
    name = values.get("instructions_file")
    if not name:
        return None
    path = Path(str(name)).expanduser()
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        log.warning("the seat's instructions file %s can't be read: %s", path, exc)
        return None


def answer(event) -> str | None:
    """The identity block and instructions of a held Codex launch, or None."""
    if event.event != "start" or event.harness != "codex":
        return None
    conn = listeners.open_store()
    if conn is None:
        return None
    try:
        row = listeners.claim(event, conn)
    finally:
        conn.close()
    if row is None:
        return None
    values = row.values
    key = handoffs.key_of_row(row)
    block = identity_block(
        session_id=getattr(event, "session_id", None) or row.session_id, cwd=row.cwd,
        posture=row.posture, project_key=row.project_key, identity=row.identity,
        scope=row.scope, handoff_dir=handoffs.folder_of_row(row) if key else None)
    text = instructions(values)
    return f"{block}\n\n{text.strip()}" if text and text.strip() else block


__all__ = ["HEADER", "answer", "identity_block", "instructions"]

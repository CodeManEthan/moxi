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

turns imports this module in the endpoint process at its first `start` ask, inside that
ask's share of the listeners' time, and asks it at every start of every session, Claude's
included. So it imports only the claim's module at the top; the composer's text helpers,
which load the composer and the presets with them, and the handoff folders are imported
once a Codex launch is held.
"""

from __future__ import annotations

import logging
from pathlib import Path

from . import listeners

log = logging.getLogger("moxi.launcher.listeners")


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
    from . import handoffs
    from .compose.common import identity_block, seat_text

    values = row.values
    key = handoffs.key_of_row(row)
    block = identity_block(
        session_id=getattr(event, "session_id", None) or row.session_id, cwd=row.cwd,
        posture=row.posture, project_key=row.project_key, identity=row.identity,
        scope=row.scope, handoff_dir=handoffs.folder_of_row(row) if key else None)
    return seat_text(block, instructions(values))


__all__ = ["answer", "instructions"]

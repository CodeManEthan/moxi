"""turns' start line: a session's own id, at every start, resume, clear and compaction.

A launcher can stamp a session's id at launch, but a clear starts a new session with a new
id, so that stamp goes stale. turns reaches every session, launched or started by hand, and
says the id it has now. The row is in turns' `LISTENERS` and speaks only while `turns` is on
`start.order`; the shipped default list leaves it off.

Imports nothing beyond the standard library.
"""

from __future__ import annotations


def line(session_id: str, source: str | None) -> str:
    """The start line for `session_id` at a start of `source`."""
    return (f"This session's id is {session_id} (as of this {source or 'start'}). It "
            "replaces any session id given at launch, which a clear makes stale.")


def answer(event) -> str | None:
    """The `start` row: the line, or None for a start with no session id."""
    session_id = getattr(event, "session_id", None)
    if not session_id:
        return None
    return line(session_id, getattr(event, "source", None))


__all__ = ["line", "answer"]

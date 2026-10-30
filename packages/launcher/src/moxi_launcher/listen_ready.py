"""The ready recorder: the session resolver's feed, for Claude and Codex alike.

turns calls it at every `ready` (a session's start, resume, clear or compaction) and at
`gone`, in the session's endpoint process, and ignores its answer. For a launch this
endpoint holds (`listeners.claim`):

- at `ready`, the event's session id joins the launch's ids, with the event's source, and
  becomes the row's id when it has none; the row moves to `ready` when it is still
  `spawning` or `started`. So a clear, a resume or a compaction in the launched session
  adds its new id, and a nested session's ids never join;
- at `gone`, an event `gone` is added, with turns' reason.
"""

from __future__ import annotations

from . import listeners, store


def observe(event) -> None:
    """Record a `ready` or `gone` of the launched session. Answers nothing."""
    if event.event not in ("ready", "gone"):
        return None
    conn = listeners.open_store()
    if conn is None:
        return None
    try:
        row = listeners.claim(event, conn)
        if row is None:
            return None
        session_id = getattr(event, "session_id", None)
        if event.event == "ready":
            if session_id:
                source = getattr(event, "source", None)
                store.learn_session(conn, row.request_id, session_id,
                                    source if source in store.SOURCES else "startup")
            store.set_state(conn, row.request_id, "ready")
        else:
            store.add_event(conn, "gone", {"reason": getattr(event, "reason", None)},
                            request_id=row.request_id, session_id=session_id)
    finally:
        conn.close()
    return None


__all__ = ["observe"]

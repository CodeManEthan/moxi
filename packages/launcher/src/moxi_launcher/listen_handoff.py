"""The handoff pointer: at a seat's start, the line naming the handoff waiting for it.

A `start` row that answers only when a session starts (source `startup`), not at a resume,
clear or compaction. It answers nothing on a relaunch (`MOXI_LAUNCHER_RELAUNCH`, whose first
turn carries the handoff) and nothing in a headless launch.

- **A launch this endpoint holds** (`listeners.claim`): the key comes from the row. The
  pointer names the newest handoff of the key still waiting, and records it as picked
  (`shown`), so the seat's next start doesn't show it again. The handoff given to the launch
  itself was recorded when its row was written, so the pointer never offers the handoff the
  first turn carries.
- **A session the launcher did not start** (no stamp): the key is the comms name in its
  environment. It is shown the waiting handoff and never marks it, so a session started by
  hand can't hide a seat's handoff from the seat's next start.
- **A session carrying the stamp that did not claim the launch** (one started from inside
  the launched session) gets nothing.
"""

from __future__ import annotations

import os

from . import handoffs, listeners, store


def answer(event) -> str | None:
    """The pointer for a starting session, or None."""
    if event.event != "start" or getattr(event, "source", None) != "startup":
        return None
    env = listeners.event_env(event)
    if env.get(listeners.RELAUNCH):
        return None
    conn = listeners.open_store()
    try:
        if env.get(listeners.REQUEST):
            return _launched(event, conn)
        return _by_hand(event, env, conn)
    finally:
        if conn is not None:
            conn.close()


def _launched(event, conn) -> str | None:
    row = listeners.claim(event, conn)
    if row is None or row.target == "headless":
        return None
    key = handoffs.key_of_row(row)
    if key is None:
        return None
    found = handoffs.waiting(conn, handoffs.folder_of_row(row), key)
    if found is None:
        return None
    store.add_pick(conn, str(found.path), key, "shown",
                   session_id=getattr(event, "session_id", None),
                   request_id=row.request_id)
    return handoffs.pointer(found)


def _by_hand(event, env, conn) -> str | None:
    key = handoffs.key_of_env(env)
    if key is None:
        return None
    found = handoffs.waiting(conn, handoffs.folder(_cwd(event, env)), key)
    return handoffs.pointer(found) if found is not None else None


def _cwd(event, env) -> str:
    """The session's folder: the event's, else the session's `PWD`, else the endpoint's."""
    return getattr(event, "cwd", None) or env.get("PWD") or os.getcwd()


__all__ = ["answer"]

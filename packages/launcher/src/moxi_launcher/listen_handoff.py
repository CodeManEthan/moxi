"""The handoff lines: at a seat's start, the handoff waiting for it, then the ones waiting
for other seats in the same folder.

A `start` row that answers when a session starts or resumes (source `startup` or
`resume`), not at a clear or compaction. It answers nothing on a relaunch
(`MOXI_LAUNCHER_RELAUNCH`, whose first turn carries the handoff), nothing in a headless
launch, and nothing in a fork (`moxi launcher fork`), a short reader that marks no
handoff picked.

- **A launch this endpoint holds** (`listeners.claim`): the key comes from the row. The
  pointer names the newest handoff of the key still waiting, and records it as picked
  (`shown`), at a resume as at a startup, so the seat's next start doesn't show it again.
  The handoff given to the launch itself was recorded when its row was written, so the
  pointer never offers the handoff the first turn carries.
- **A session the launcher did not start** (no stamp): the key is the comms name in its
  environment. It is shown the waiting handoff and never marks it, so a session started by
  hand can't hide a seat's handoff from the seat's next start.
- **A session carrying the stamp that did not claim the launch** (one started from inside
  the launched session) gets nothing.

After the pointer come other seats' waiting handoffs in the same folder, one line each,
newest first, at most `handoffs.OTHERS_SHOWN`, then how many more (`handoffs.others`). They
mark nothing picked: each waits for its own seat. A session with no key gets no pointer and
these lines for every waiting handoff in its folder. A session with no handoff folder (notes
installed and no notes directory for it) gets nothing.
"""

from __future__ import annotations

import os

from . import handoffs, listeners, store

#: The start sources the lines are shown at.
SOURCES = ("startup", "resume")


def answer(event) -> str | None:
    """The handoff lines for a starting or resuming session, or None."""
    if event.event != "start" or getattr(event, "source", None) not in SOURCES:
        return None
    env = listeners.event_env(event)
    if env.get(listeners.RELAUNCH_ENV):
        return None
    conn = listeners.open_store()
    try:
        if env.get(listeners.REQUEST_ENV):
            lines = _launched(event, conn)
        else:
            lines = _by_hand(event, env, conn)
    finally:
        if conn is not None:
            conn.close()
    return "\n".join(lines) or None


def _launched(event, conn) -> list[str]:
    row = listeners.claim(event, conn)
    if row is None or row.target == "headless" or row.fork_of:
        return []
    key = handoffs.key_of_row(row)
    try:
        where = handoffs.handoff_dir_of_row(row, create=key is not None)
    except handoffs.NoNotesDir:
        return []
    lines = []
    found = handoffs.waiting(conn, where, key) if key is not None else None
    if found is not None:
        store.add_pick(conn, str(found.path), key, "shown",
                       session_id=getattr(event, "session_id", None),
                       request_id=row.request_id)
        lines.append(handoffs.pointer(found))
    return lines + handoffs.other_lines(handoffs.others(conn, where, key))


def _by_hand(event, env, conn) -> list[str]:
    key = handoffs.key_of_env(env)
    try:
        where = handoffs.handoff_dir(_cwd(event, env), create=key is not None)
    except handoffs.NoNotesDir:
        return []
    lines = []
    found = handoffs.waiting(conn, where, key) if key is not None else None
    if found is not None:
        lines.append(handoffs.pointer(found))
    return lines + handoffs.other_lines(handoffs.others(conn, where, key))


def _cwd(event, env) -> str:
    """The session's folder: the event's, else the session's `PWD`, else the endpoint's."""
    return getattr(event, "cwd", None) or env.get("PWD") or os.getcwd()


__all__ = ["SOURCES", "answer"]

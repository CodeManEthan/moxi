"""Clearing a socket a dead server left behind, decided by its lock and never by a probe.

A server holds the lock file beside its socket, `<address>.lock`, for as long as it
serves, and the lock dies with its process. So a free lock means no server: the socket is
left over and may go. A connect probe can't say that (a full backlog or a slow start looks
dead), so it never decides here.

Kept apart from the server so that a caller that only sweeps doesn't load asyncio.
"""

from __future__ import annotations

import os
import sys

from .. import files

_WINDOWS = sys.platform == "win32"

LIVE, REMOVED, ABSENT = "live", "removed", "absent"


def lock_path(address: str) -> str:
    """The lock file a server at Unix socket `address` holds while it serves."""
    return address + ".lock"


def sweep(address: str) -> str:
    """Remove the socket at `address` if no server holds it: "removed"; "live" when a
    server holds its lock; "absent" when there is no socket.

    The lock is tried once, without waiting, and held only for the one `unlink`, so a
    server that starts at that moment waits it out and serves.
    """
    if _WINDOWS:
        raise NotImplementedError("moxi.ipc.sweep is not built for Windows yet")
    if not os.path.lexists(address):
        return ABSENT
    try:
        with files.lock(lock_path(address), timeout=0):
            try:
                os.unlink(address)
            except FileNotFoundError:
                return ABSENT
            return REMOVED
    except files.LockTimeout:
        return LIVE
    except RuntimeError:
        # This process holds the lock itself: it serves at `address`.
        return LIVE

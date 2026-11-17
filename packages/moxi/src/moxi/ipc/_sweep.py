"""Clearing a socket a dead server left behind, decided by its lock and never by a probe.

A server holds its address's lock file for as long as it serves, and the lock dies with
its process. On Linux and macOS the lock is `<address>.lock`, beside the socket; on
Windows it is `<name>.lock` in the module's runtime directory, since a pipe has no
directory of its own. So a free lock means no server: a socket is left over and may go.
A connect probe can't say that (a full backlog or a slow start looks dead), so it never
decides here. On Windows there is nothing to clear, since a pipe goes with its server,
and a sweep only reports.

Kept apart from the server so that a caller that only sweeps doesn't load asyncio.
"""

from __future__ import annotations

import os
import sys

from .. import files

_WINDOWS = sys.platform == "win32"

LIVE, REMOVED, ABSENT = "live", "removed", "absent"


def lock_path(address: str) -> str:
    """The lock file a server at `address` holds while it serves: `<address>.lock` for a
    Unix socket, and for a Windows pipe `address(module, name)` the file `<name>.lock`
    in the module's runtime directory. Raises `ValueError` for a pipe address
    `moxi.ipc.address` didn't make."""
    if not _WINDOWS:
        return address + ".lock"
    from .. import paths
    from . import _pipe_parts

    module, name = _pipe_parts(address)
    return str(paths._layout(module).runtime_dir() / f"{name}.lock")


def sweep(address: str) -> str:
    """Remove the socket at `address` if no server holds it: "removed"; "live" when a
    server holds its lock; "absent" when there is no socket.

    The lock is tried once, without waiting, and held only for the one `unlink`, so a
    server that starts at that moment waits it out and serves. On Windows a sweep only
    reports, "live" or "absent", by the same lock: a pipe goes with its server, so there
    is never anything to remove.
    """
    lock = lock_path(address)
    if _WINDOWS:
        if not os.path.exists(lock):
            return ABSENT
        try:
            with files.lock(lock, timeout_s=0):
                return ABSENT
        except files.LockTimeout:
            return LIVE
        except RuntimeError:
            return LIVE  # this process serves at `address`
    if not os.path.lexists(address):
        return ABSENT
    try:
        with files.lock(lock, timeout_s=0):
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

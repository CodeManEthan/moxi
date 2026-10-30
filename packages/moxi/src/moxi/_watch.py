"""The watchdog behind `moxi.proc.spawn(..., lifeline="watch")` on POSIX.

Run as `python -m moxi._watch <pgid>`, with its stdin the lifeline's read end. When stdin
reaches EOF (the parent closed the lifeline, or died), it sends the process group `pgid`
SIGTERM, waits up to two seconds for the group to go, sends SIGKILL if it hasn't, and
exits. It also exits, sending nothing, once the group is gone, which it checks each second.
"""

from __future__ import annotations

import os
import sys
import time

#: Seconds between SIGTERM and SIGKILL.
GRACE = 2.0
#: How often the group is checked while the lifeline is open, in seconds.
CHECK = 1.0


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if sys.platform == "win32":
        print("moxi._watch: Windows holds a watched child in a Job Object instead",
              file=sys.stderr)
        return 2
    if len(args) != 1 or not args[0].isdigit() or int(args[0]) <= 1:
        print("usage: python -m moxi._watch <pgid>", file=sys.stderr)
        return 2
    pgid = int(args[0])
    while _group_alive(pgid):
        if _lifeline_closed(CHECK):
            stop(pgid)
            return 0
    return 0


def _lifeline_closed(wait: float) -> bool:
    """Wait up to `wait` seconds on stdin; True once it reaches EOF (or fails)."""
    import select

    try:
        ready, _, _ = select.select([0], [], [], wait)
        if not ready:
            return False
        return not os.read(0, 4096)
    except InterruptedError:
        return False
    except OSError:
        return True


def stop(pgid: int, grace: float = GRACE) -> None:
    """SIGTERM to the group, then SIGKILL if any of it is left after `grace` seconds."""
    import signal

    if not _send(pgid, signal.SIGTERM):
        return
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline:
        time.sleep(0.05)
        if not _group_alive(pgid):
            return
    _send(pgid, signal.SIGKILL)


def _send(pgid: int, signum: int) -> bool:
    try:
        os.killpg(pgid, signum)
    except ProcessLookupError:
        return False
    except PermissionError:
        return False  # the group id now belongs to another user's processes
    return True


def _group_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return False
    return True


if __name__ == "__main__":
    sys.exit(main())

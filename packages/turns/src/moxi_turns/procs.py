"""Processes: a process's ancestry, for `find(pid=…)`, and the parent-death signal a
host's children carry.

A command run inside a session finds its own endpoint by walking up from its own pid to
the harness process the endpoint names. `ProcView` reads each parent through
`moxi.proc.parent`, on every platform the core reads one; a test hands in its own.
"""

from __future__ import annotations

import ctypes
import os
import signal
import sys
from collections.abc import Callable, Iterator

#: How long a child a host stops gets between SIGTERM and SIGKILL.
TERM_GRACE = 5.0
PR_SET_PDEATHSIG = 1                  # linux/prctl.h


def die_with_parent(parent_pid: int) -> Callable[[], None] | None:
    """A `preexec_fn` that asks the kernel to SIGTERM the child when the thread that
    spawned it exits, and closes the race where that already happened before the ask.
    Linux only; elsewhere it is None, and a child outlives a host that dies without
    stopping it."""
    if not sys.platform.startswith("linux"):
        return None

    prctl = ctypes.CDLL(None, use_errno=True).prctl    # dlopen here, never in the child

    def _pre() -> None:
        prctl(PR_SET_PDEATHSIG, int(signal.SIGTERM), 0, 0, 0)
        if os.getppid() != parent_pid:
            os._exit(0)

    return _pre


class ProcView:
    """Reads a process's parent through `moxi.proc.parent`."""

    def parent(self, pid: int) -> int | None:
        """The parent pid of `pid`: 0 when no parent is visible, None when there is no
        such process to read."""
        from moxi import proc

        return proc.parent(pid)

    def ancestry(self, pid: int, limit: int = 4096) -> Iterator[int]:
        """`pid` itself, then each ancestor up to (not including) pid 0."""
        seen: set[int] = set()
        while pid and pid > 0 and pid not in seen and len(seen) < limit:
            seen.add(pid)
            yield pid
            parent = self.parent(pid)
            if parent is None:
                return
            pid = parent


DEFAULT = ProcView()

__all__ = ["DEFAULT", "PR_SET_PDEATHSIG", "ProcView", "TERM_GRACE", "die_with_parent"]

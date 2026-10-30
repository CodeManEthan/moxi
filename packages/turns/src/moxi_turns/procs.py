"""Processes: a process's ancestry, read from `/proc`, for `find(pid=…)`, the
parent-death signal a host's children carry, and a child's stdin.

A command run inside a session finds its own endpoint by walking up from its own pid to
the harness process the endpoint names. `ProcView` is the one reader of `/proc`; a test
hands in its own. Where `/proc` does not exist the walk is the pid alone.
"""

from __future__ import annotations

import ctypes
import os
import shutil
import signal
import sys
from collections.abc import Callable, Iterator, Mapping, Sequence

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


# Opens the file named first as fd 0, then becomes the program named next, with the
# arguments after it (its own name first). The exec keeps the pid and the start time,
# so whatever watches the pid watches the program.
_STDIN_EXEC = (
    "import os, sys\n"
    "fd = os.open(sys.argv[1], os.O_RDWR | getattr(os, 'O_NOCTTY', 0))\n"
    "if fd != 0:\n"
    "    os.dup2(fd, 0)\n"
    "    os.close(fd)\n"
    "os.execv(sys.argv[2], sys.argv[3:])\n"
)


def with_stdin(path, argv: Sequence[str], env: Mapping[str, str] | None = None) -> list[str]:
    """`argv` run with `path` as its stdin, for a spawn that cannot set a child's stdin
    (`moxi.proc.spawn` leaves it the parent's own). A short interpreter opens `path`
    as fd 0 and then execs the program, which keeps its pid. The program is looked up
    on `env`'s PATH (this process's when None) first, so a missing one raises
    `FileNotFoundError` here, as `Popen` would."""
    argv = [os.fspath(part) for part in argv]
    search = (os.environ if env is None else env).get("PATH", os.defpath)
    program = shutil.which(argv[0], path=search)
    if program is None:
        raise FileNotFoundError(f"{argv[0]} is not on PATH")
    return [sys.executable, "-I", "-S", "-c", _STDIN_EXEC, os.fspath(path),
            os.path.abspath(program), *argv]


class ProcView:
    """Reads a process's parent from `/proc/<pid>/stat`."""

    def __init__(self, root: str = "/proc"):
        self.root = root

    def parent(self, pid: int) -> int | None:
        """The parent pid of `pid`, or None when there is no such process to read."""
        try:
            with open(f"{self.root}/{int(pid)}/stat", "rb") as f:
                raw = f.read().decode("utf-8", "replace")
        except (OSError, ValueError):
            return None
        # The command name sits in parentheses and may itself hold spaces or parens;
        # everything after the last `)` is space-separated, starting at field 3.
        tail = raw[raw.rfind(")") + 1:].split()
        try:
            return int(tail[1])
        except (IndexError, ValueError):
            return None

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

__all__ = ["DEFAULT", "PR_SET_PDEATHSIG", "ProcView", "TERM_GRACE", "die_with_parent",
           "with_stdin"]

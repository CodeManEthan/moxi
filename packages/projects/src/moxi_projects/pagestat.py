"""What is at a path, asked for a page read that never waits on the filesystem for long.

A root can sit on a mount that hangs, and a `stat` of it then never returns. A page read
asks `look(paths)`, which hands each path's `stat` to a pool of at most `WORKERS` threads
of this page server's own and waits for them together at most `BOUND_S` from its start.
A path whose `stat` has not answered by then reads None. A path whose earlier `stat` is
still running is not asked again: it reads None until that `stat` ends, and the next
`look` takes its answer. So a mount that hangs holds at most `WORKERS` threads, and a read
never waits past the bound.

The threads are daemon threads, so one stuck in a `stat` never holds up the server's
exit.
"""

from __future__ import annotations

import os
import queue
import stat as stat_mode
import threading
import time
from collections.abc import Callable, Iterable

#: The most threads a pool runs at once.
WORKERS = 4
#: How long a `look` waits for its paths, in seconds, from its start.
BOUND_S = 0.25

#: What `look` answers for a path: a directory, something else (a file, a device), or
#: nothing there (or a parent that is not a directory). None means it can't say: the
#: `stat` failed another way, such as a permission error, or did not answer in time.
DIR = "dir"
OTHER = "other"
MISSING = "missing"


def _what(stat: Callable[[str], os.stat_result], path: str) -> str | None:
    try:
        found = stat(path)
    except (FileNotFoundError, NotADirectoryError):
        return MISSING
    except (OSError, ValueError):
        return None
    return DIR if stat_mode.S_ISDIR(found.st_mode) else OTHER


class _Job:
    __slots__ = ("path", "ended", "answer")

    def __init__(self, path: str):
        self.path = path
        self.ended = threading.Event()
        self.answer: str | None = None


class Pool:
    """The threads that `stat` paths for `look`, and the paths whose `stat` is still out.
    `stat` is the call that asks the filesystem (`os.stat` unless a test gives another)."""

    def __init__(self, *, workers: int = WORKERS,
                 stat: Callable[[str], os.stat_result] = os.stat):
        self._workers = workers
        self._stat = stat
        self._lock = threading.Lock()
        self._jobs: queue.SimpleQueue[_Job] = queue.SimpleQueue()
        self._out: dict[str, _Job] = {}
        self._threads = 0

    def _work(self) -> None:
        while True:
            job = self._jobs.get()
            job.answer = _what(self._stat, job.path)
            job.ended.set()

    def look(self, paths: Iterable[str], *, timeout_s: float = BOUND_S
             ) -> dict[str, str | None]:
        """`{path: DIR | OTHER | MISSING | None}` for each of `paths`, waiting at most
        `timeout_s` for them together (the module doc)."""
        found: dict[str, str | None] = {}
        asked: list[_Job] = []
        with self._lock:
            for path in dict.fromkeys(paths):
                job = self._out.get(path)
                if job is not None:
                    if job.ended.is_set():
                        found[path] = job.answer
                        del self._out[path]
                    else:
                        found[path] = None
                    continue
                job = _Job(path)
                self._out[path] = job
                asked.append(job)
            start = time.monotonic()
            for job in asked:
                self._jobs.put(job)
            while self._threads < min(self._workers, len(self._out)):
                threading.Thread(target=self._work, name="moxi-projects-stat",
                                 daemon=True).start()
                self._threads += 1
        deadline = start + timeout_s
        for job in asked:
            if job.ended.wait(max(0.0, deadline - time.monotonic())):
                found[job.path] = job.answer
                with self._lock:
                    if self._out.get(job.path) is job:
                        del self._out[job.path]
            else:
                found[job.path] = None
        return found


_POOL = Pool()


def look(paths: Iterable[str]) -> dict[str, str | None]:
    """What is at each of `paths`, by this server's one pool, within `BOUND_S`."""
    return _POOL.look(paths)


__all__ = ["BOUND_S", "DIR", "MISSING", "OTHER", "WORKERS", "Pool", "look"]

"""tunnel's two locks, and the one order it takes them in.

`STATE/tunnel/tunnel.lock` guards the record and the provider commands. An act whose plan
touches a Cloudflare entry, and every `repair`, also takes the core lock first, in the form
the core gives a module that plans and applies units outside an install:
`moxi.files.lock(moxi.paths.core_lock(), nested=True)`. The core calls such an act makes
(`moxi.config.set` on the daemon switch, `moxi.units.restart`) then find the core lock held
by this process and yield. Under `moxi uninstall`, the core lock is the uninstall's, and the
nested take yields on it the same way.

The rules this module keeps:

- one order: the core lock before `tunnel.lock`. Taking the core lock while this thread
  holds `tunnel.lock` raises `RuntimeError`, so the order can't be broken by accident;
- each wait is `WAIT` seconds unless the caller passes another, and a lock still held then
  raises `moxi.files.LockTimeout` naming the holder's pid (its `exit_code` is 75);
- no lock is held while a user is asked: `none_held()` raises when this thread holds
  either, and every act calls it before it asks;
- threads of one process take turns for `tunnel.lock` rather than being refused, since
  `moxi.files.lock` is not re-entrant across a process's threads.

`held(core=...)` takes what an act's plan needs; `holds_core()` and `holds_tunnel()` say
what this thread holds through this module, which is what `record.commit` checks.
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from moxi import files, paths

MODULE = "tunnel"
#: The lock file in tunnel's state directory.
LOCK_FILE = "tunnel.lock"
#: How long the CLI, the Python API and the uninstall hooks wait for each lock, in seconds.
WAIT = 60.0

_local = threading.local()
# Threads of this process queue here for tunnel.lock before taking the file's lock.
_gate = threading.Lock()


def lock_path() -> Path:
    """`tunnel.lock` in tunnel's state directory."""
    return paths.Layout(MODULE).state_dir() / LOCK_FILE


def holds_core() -> bool:
    """True while this thread is inside `core()`, the core lock held or yielded to."""
    return getattr(_local, "core", False)


def holds_tunnel() -> bool:
    """True while this thread is inside `tunnel()`."""
    return getattr(_local, "tunnel", False)


@contextmanager
def core(timeout: float | None = WAIT) -> Iterator[None]:
    """Hold the core lock, nested, for the block. Inside an outer `core()` it does nothing
    more. Raises `RuntimeError` when this thread holds `tunnel.lock`."""
    if holds_tunnel():
        raise RuntimeError("tunnel takes the core lock before tunnel.lock, never while "
                           "it holds tunnel.lock")
    if holds_core():
        yield
        return
    with files.lock(paths.core_lock(), nested=True, timeout_s=timeout):
        _local.core = True
        try:
            yield
        finally:
            _local.core = False


_core = core


@contextmanager
def tunnel(timeout: float | None = WAIT) -> Iterator[None]:
    """Hold `tunnel.lock` for the block, after this process's other threads are done with
    it. Raises `RuntimeError` when this thread already holds it."""
    if holds_tunnel():
        raise RuntimeError(f"{lock_path()} is already held by this thread; tunnel's lock "
                           "is not re-entrant")
    path = lock_path()
    start = time.monotonic()
    if not _gate.acquire(timeout=-1 if timeout is None else max(timeout, 0)):
        raise files.LockTimeout(path, os.getpid())
    try:
        left = None if timeout is None else max(0.0, timeout - (time.monotonic() - start))
        with files.lock(path, timeout_s=left):
            _local.tunnel = True
            try:
                yield
            finally:
                _local.tunnel = False
    finally:
        _gate.release()


@contextmanager
def held(*, core: bool = False, timeout: float | None = WAIT) -> Iterator[None]:
    """The locks an act's plan needs, in the one order: the core lock first when `core`,
    then `tunnel.lock`. Each waits `timeout` seconds."""
    if core:
        with _core(timeout):
            with tunnel(timeout):
                yield
    else:
        with tunnel(timeout):
            yield


def none_held() -> None:
    """Raise `RuntimeError` when this thread holds either lock: called before a user is
    asked, since no lock is held while one is."""
    if holds_core() or holds_tunnel():
        raise RuntimeError("tunnel asks the user only with no lock held")


__all__ = ["LOCK_FILE", "WAIT", "lock_path", "holds_core", "holds_tunnel", "core",
           "tunnel", "held", "none_held"]

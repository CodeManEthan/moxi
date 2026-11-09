"""Writing a module's files safely: whole-file writes, locks, and append-only records.

Every helper here is standard library only, but for `edit_toml`'s vendored tomlkit, and
imports nothing heavy at module level, so a hook can import `moxi.files` cheaply.

- `atomic_write` replaces a file in one step: a reader sees the old text or the new,
  never half of either.
- `lock` holds an exclusive lock on a lock file for the length of a `with` block. The
  lock dies with its process, so a crashed holder frees it at once. With `nested=True`
  it serves the core lock, which code under an install finds already held.
- `adopt_core_claim` lets an install's settle or teardown child keep the claim on the
  core lock that its parent handed it.
- `update` reads a file, changes it and writes it back under that file's lock.
- `edit_toml` does the same for a TOML file, keeping its comments and order (it imports
  the core's vendored tomlkit inside the call).
- `append_record` and `read_records` keep a log of JSON lines that rolls to one older
  generation at a size cap (or never, with no cap); a durable append is on disk when it
  returns. Its options refuse records that hold given field names, and collapse a busy
  class of records into one summary row a minute or an hour (`Collapse`).
- `private_dir` makes a directory only its owner can enter.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from collections.abc import Callable, Collection, Iterator
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path

_WINDOWS = sys.platform == "win32"
_BINARY = getattr(os, "O_BINARY", 0)

#: How often a waiting `lock` tries again, in seconds.
POLL = 0.05
#: On Windows, `msvcrt.locking` locks this one byte, past the pid text at offset 0, so a
#: waiting process can still read the holder's pid.
LOCK_OFFSET = 2 ** 30
#: How long `atomic_write` retries a replace that meets a sharing violation (Windows).
REPLACE_RETRY = 1.0
#: The block size `read_records` reads backwards in.
BLOCK = 64 * 1024


class LockTimeout(Exception):
    """Another process holds the lock past the caller's timeout.

    `path` is the lock file and `holder` the pid written in it, or None when it holds
    none. A command that stops on this exits `exit_code` (try again later).
    """

    exit_code = 75

    def __init__(self, path: Path, holder: int | None):
        self.path = path
        self.holder = holder
        who = f"pid {holder}" if holder else "another process"
        super().__init__(f"{path} is locked by {who}")


# -- whole-file writes -------------------------------------------------------------------

def atomic_write(path: str | os.PathLike, data: bytes | str, *, mode: int = 0o600) -> None:
    """Write `data` to `path` in one step, with permission bits `mode` (POSIX).

    The data goes to a temp file `.<name>.<8 hex>.tmp` in the same directory, which is
    flushed to disk and then renamed over `path`; the directory is flushed after the
    rename (POSIX). A `str` is written as UTF-8. If any step fails the temp file is
    removed and `path` is left as it was. A missing parent directory is created (0700).
    """
    path = Path(path)
    if isinstance(data, str):
        data = data.encode("utf-8")
    _parent(path)
    tmp = path.with_name(f".{path.name}.{os.urandom(4).hex()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _BINARY, 0o600)
    try:
        try:
            view = memoryview(data)
            while view:
                view = view[os.write(fd, view):]
            if not _WINDOWS:
                os.fchmod(fd, mode)
            os.fsync(fd)
        finally:
            os.close(fd)
        _replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    if not _WINDOWS:
        _fsync_dir(path.parent)


def _replace(src: Path, dst: Path) -> None:
    if not _WINDOWS:
        os.replace(src, dst)
        return
    # Another process holding the target open without delete sharing (a reader, a virus
    # scanner) makes a replace fail for a moment; it is tried again for up to a second.
    deadline = time.monotonic() + REPLACE_RETRY
    while True:
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(POLL)


def _fsync_dir(directory: Path) -> None:
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _parent(path: Path) -> None:
    _make_dirs(path.parent)


# -- locks -------------------------------------------------------------------------------

# The lock files this process holds through `lock`: absolute path -> the holding thread.
_held: dict[str, int] = {}
# The descriptor of each lock file in `_held`, for the install's hand-off to its child.
_fds: dict[str, int] = {}
# The paths in `_held` that a `nested=True` call took itself: a standalone caller's hold
# of the core lock, which covers only its own thread. A core lock held without
# `nested` is the install's hold, which covers every thread of the process.
_nested_takes: set[str] = set()
_held_guard = threading.Lock()
# One in-process gate per lock file that `update`, `edit_toml` and `append_record` take,
# so their callers' threads queue for the lock file instead of being refused.
_gates: dict[str, threading.Lock] = {}

#: The variable through which an install's parent names, to a settle or teardown child,
#: the descriptor (on Windows the handle) of the core lock it holds. Only
#: `adopt_core_claim` reads it; `lock` never reads the environment.
LOCK_FD_ENV = "MOXI_CORE_LOCK_FD"
# The install's claim this process adopted: a descriptor open on the core lock file, or
# None. Kept here, never in the environment, for the process's whole life.
_claim: int | None = None
_adopted = False
_claim_guard = threading.Lock()


def lock(path: str | os.PathLike, *, timeout_s: float | None = None,
         nested: bool = False) -> AbstractContextManager[None]:
    """Hold an exclusive lock on the lock file `path` for the length of the block.

    `timeout=None` waits as long as it takes; `timeout=0` tries once; a number waits that
    many seconds. When the wait runs out, `LockTimeout` names the holder's pid.

    The lock file is created (0600) and never removed. Once the lock is taken, the
    holder's pid is written at the start of the file, so a waiting process can name it.
    The lock belongs to the process: it is freed when the block ends, and also when the
    process dies, however it dies. It is not re-entrant: taking a path this process
    already holds, from any of its threads, raises `RuntimeError` instead of waiting for
    itself. Threads of one process that share a lock file queue for it with a lock of
    their own; `update`, `edit_toml` and `append_record` already do.

    `nested=True` is for the core lock alone (`moxi.paths.core_lock()`); any other path
    raises `ValueError` before anything is taken. Code that runs under an install never
    waits for that install and never raises on its lock, and every other caller takes the
    lock. So a nested call yields at once, taking and releasing nothing, when

    - this process holds the core lock without `nested` (the install's own hold, in the
      install's process): from any of its threads;
    - this thread took the core lock with an outer `nested=True` call (a standalone
      caller holding it around several core calls that take it themselves);
    - this process adopted its install's claim (`adopt_core_claim`) and the claim's
      descriptor is still open on the lock file, checked again on every call.

    Otherwise it takes the lock as a call without `nested` does, with the same timeout,
    first waiting its turn behind this process's other threads that took it with
    `nested=True`, so two threads of a standalone caller never share the lock's span. A
    claim whose descriptor no longer checks is dropped with one warning line, and the
    lock is then taken in the same way.
    """
    path = Path(path)
    if not nested:
        return _lock(path, timeout_s)
    from .paths import core_lock

    if os.path.abspath(path) != os.path.abspath(core_lock()):
        raise ValueError(f"lock(nested=True) is for the core lock alone, not {path}")
    return _nested_lock(path, timeout_s)


@contextmanager
def _lock(path: Path, timeout: float | None, *, nested_take: bool = False) -> Iterator[None]:
    key = os.path.abspath(path)
    me = threading.get_ident()
    with _held_guard:
        holder = _held.get(key)
        if holder is not None:
            who = "this thread" if holder == me else "another thread of this process"
            raise RuntimeError(f"{path} is already locked by {who}; "
                               "moxi.files.lock is not re-entrant")
        _held[key] = me
        if nested_take:
            _nested_takes.add(key)
    try:
        fd = _acquire(path, timeout)
    except BaseException:
        with _held_guard:
            _held.pop(key, None)
            _nested_takes.discard(key)
        raise
    with _held_guard:
        _fds[key] = fd
    try:
        yield
    finally:
        with _held_guard:
            _held.pop(key, None)
            _fds.pop(key, None)
            _nested_takes.discard(key)
        _release(fd)


@contextmanager
def _nested_lock(path: Path, timeout: float | None) -> Iterator[None]:
    key = os.path.abspath(path)
    me = threading.get_ident()
    with _held_guard:
        # Only a hold already taken counts: a thread still waiting to take it covers none.
        holder = _held.get(key) if key in _fds else None
        covered = holder is not None and (holder == me or key not in _nested_takes)
    if covered or _claim_checks(path):
        yield
        return
    with _gated_lock(path, timeout=timeout, nested_take=True):
        yield


@contextmanager
def _gated_lock(path: Path, *, timeout: float | None = None,
                nested_take: bool = False) -> Iterator[None]:
    """`lock`, after this process's other threads that go through here are done with
    `path`. A thread that already holds `path` gets `RuntimeError` at once, as from
    `lock`, rather than waiting on the gate for itself."""
    key = os.path.abspath(path)
    with _held_guard:
        if _held.get(key) == threading.get_ident():
            raise RuntimeError(f"{path} is already locked by this thread; "
                               "moxi.files.lock is not re-entrant")
        gate = _gates.setdefault(key, threading.Lock())
    if timeout is None:
        gate.acquire()
    else:
        deadline = time.monotonic() + timeout
        if not gate.acquire(timeout=max(timeout, 0)):
            raise LockTimeout(path, os.getpid())
        timeout = max(0.0, deadline - time.monotonic())
    try:
        with _lock(path, timeout, nested_take=nested_take):
            yield
    finally:
        gate.release()


def _descriptor(path: str | os.PathLike) -> int:
    """The descriptor through which this process holds the lock file `path` with `lock`.

    For the install's hand-off of the core lock to the child it starts; the descriptor
    stays this process's, and is closed when its `lock` block ends. Raises
    `RuntimeError` when this process does not hold `path`.
    """
    key = os.path.abspath(path)
    with _held_guard:
        fd = _fds.get(key)
    if fd is None:
        raise RuntimeError(f"{path} is not locked by this process")
    return fd


# -- the install's claim -----------------------------------------------------------------

def adopt_core_claim(path: str | os.PathLike) -> bool:
    """Take over the claim on the core lock `path` that an install's parent handed this
    process, and say whether this process now holds one.

    The settle and teardown child calls it once, first. It reads `MOXI_CORE_LOCK_FD`,
    deletes it from `os.environ` whatever it held, and checks that the descriptor it names
    (on Windows a handle) is open on `path`: `(st_dev, st_ino)` of the descriptor equal
    those of `path`. On a pass the descriptor is kept in this module, made
    non-inheritable, for the process's whole life, and every `lock(path, nested=True)`
    then yields; nothing this process spawns inherits the claim, neither the variable nor
    the descriptor. A missing variable, or one that names a closed descriptor or another
    file, keeps nothing and returns False. A later call reads nothing and returns whether
    a claim is still held.
    """
    global _adopted, _claim
    with _claim_guard:
        if _adopted:
            return _claim is not None
        _adopted = True
        value = os.environ.pop(LOCK_FD_ENV, None)
        if value is None:
            return False
        fd = _claim_descriptor(value)
        if fd is None or not _same_file(fd, path):
            return False
        os.set_inheritable(fd, False)
        _claim = fd
        return True


def _claim_descriptor(value: str) -> int | None:
    """The descriptor the variable's text names, or None when it names none."""
    value = value.strip()
    # ASCII digits only: `isdigit` alone passes "²", which `int` then refuses.
    if not (value.isascii() and value.isdigit()):
        return None
    number = int(value)
    if not _WINDOWS:
        return number
    import msvcrt

    try:
        return msvcrt.open_osfhandle(number, os.O_RDWR)
    except (OSError, OverflowError, ValueError):
        return None


def _same_file(fd: int, path: str | os.PathLike) -> bool:
    """Whether `fd` is open on the file at `path` (on Windows the volume serial and the
    file index, which `os.stat` reports as `st_dev` and `st_ino`)."""
    try:
        held = os.fstat(fd)
        there = os.stat(path)
    except (OSError, OverflowError, ValueError):
        return False
    return (held.st_dev, held.st_ino) == (there.st_dev, there.st_ino)


def _claim_checks(path: Path) -> bool:
    """Whether this process holds the install's claim and it still checks against `path`.
    A claim that no longer checks is dropped, with one warning line."""
    global _claim
    with _claim_guard:
        if _claim is None:
            return False
        if _same_file(_claim, path):
            return True
        _claim = None
    try:
        sys.stderr.write(f"moxi: the install's lock claim is no longer open on {path}; "
                         "taking the lock as a standalone caller\n")
        sys.stderr.flush()
    except (OSError, ValueError, AttributeError):
        pass  # no terminal to warn on; the lock is taken all the same
    return False


def _acquire(path: Path, timeout: float | None) -> int:
    _parent(path)
    fd = os.open(path, os.O_RDWR | os.O_CREAT | _BINARY, 0o600)
    deadline = None if timeout is None else time.monotonic() + timeout
    try:
        while not _try_lock(fd):
            if deadline is not None and time.monotonic() >= deadline:
                raise LockTimeout(path, _holder(path))
            time.sleep(POLL)
        _write_pid(fd)
    except BaseException:
        os.close(fd)
        raise
    return fd


def _try_lock(fd: int) -> bool:
    if _WINDOWS:
        import msvcrt

        os.lseek(fd, LOCK_OFFSET, os.SEEK_SET)
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        return True
    import fcntl

    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return False
    return True


def _write_pid(fd: int) -> None:
    text = f"{os.getpid()}\n".encode("ascii")
    os.lseek(fd, 0, os.SEEK_SET)
    os.write(fd, text)
    try:
        os.ftruncate(fd, len(text))
    except OSError:
        pass  # a reader takes only the first line, so a longer old pid does no harm


def _release(fd: int) -> None:
    try:
        try:
            os.ftruncate(fd, 0)
        except OSError:
            pass
        if _WINDOWS:
            import msvcrt

            os.lseek(fd, LOCK_OFFSET, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def _holder(path: Path) -> int | None:
    """The pid written in lock file `path`, or None."""
    try:
        with open(path, "rb") as fh:
            first = fh.readline(32).strip()
    except OSError:
        return None
    return int(first) if first.isdigit() else None


def _lock_path(path: Path) -> Path:
    return path.with_name(path.name + ".lock")


# -- read, change, write -----------------------------------------------------------------

def update(path: str | os.PathLike, fn: Callable[[bytes | None], bytes], *,
           mode: int = 0o600) -> bytes:
    """Change file `path` under its lock, `<path>.lock`, and return what was written.

    `fn` gets the file's bytes (None when it is missing) and returns the new bytes,
    which are written with `atomic_write`. Threads of this process that update the same
    file take turns.
    """
    path = Path(path)
    with _gated_lock(_lock_path(path)):
        try:
            old = path.read_bytes()
        except FileNotFoundError:
            old = None
        new = fn(old)
        atomic_write(path, new, mode=mode)
    return new


@contextmanager
def edit_toml(path: str | os.PathLike, *, timeout_s: float | None = 10.0,
              lock: str | os.PathLike | None = None) -> Iterator:
    """Edit the TOML file `path` in a `with` block, keeping its comments and order.

    Under the file's lock, `<path>.lock` or the lock file `lock` names (waiting `timeout_s`
    as `lock` does), the file is read and parsed with the core's vendored tomlkit, and
    the block gets the document (an empty one when the file is missing). When the block
    ends cleanly the document's text is written with `atomic_write`, keeping an existing
    file's permission bits; a new file is 0600. When the block raises, nothing is
    written and the exception goes on. A file that is not valid TOML raises `ValueError`
    (tomlkit's `ParseError`) before the block runs. When `path` is a symbolic link, the
    file it points to is written and the link is kept.

    This is the one TOML editor: `moxi config set` edits a module's config file through
    it, and a module that edits a TOML file of its own uses it rather than a TOML library
    of its own.

    `lock` is for a file whose lock must not sit beside it (a file in a folder the user
    keeps in git, say). Every caller that edits one file must pass the same `lock`, since
    two lock files don't guard each other.
    """
    from ._vendor import tomlkit

    path = Path(path)
    held = _lock_path(path) if lock is None else Path(lock)
    with _gated_lock(held, timeout=timeout_s):
        target = Path(os.path.realpath(path))
        try:
            text = target.read_text(encoding="utf-8")
        except FileNotFoundError:
            text = None
        if text is None:
            document, mode = tomlkit.document(), 0o600
        else:
            document = tomlkit.parse(text)
            mode = os.stat(target).st_mode & 0o7777
        yield document
        atomic_write(target, tomlkit.dumps(document), mode=mode)


# -- records -----------------------------------------------------------------------------

def append_record(path: str | os.PathLike, record: dict, *,
                  max_bytes: int | None = 1_000_000, durable: bool = False,
                  refuse: Collection[str] | None = None,
                  collapse: tuple[Collapse, str] | None = None) -> None:
    """Append `record` to the log `path` as one JSON line.

    The line is written with one `write` on a descriptor opened for appending (the file
    is created 0600), so lines from several writers never interleave. When the file and
    the line together would pass `max_bytes`, the file first moves to `<path>.1` (one
    older generation is kept; on Windows a move that meets a sharing violation is tried
    again for up to a second, as `atomic_write`'s replace is) and the line starts a new
    file. A line longer than `max_bytes` raises `ValueError`. With `max_bytes=None` the
    file never rolls and a line of any length is written.

    With `durable=True` the file is flushed to disk after the write, and so is its
    directory (POSIX) when this write created the file, so the line survives a power loss
    once the call returns.

    `refuse` is a set of field names no record may hold. A record that holds one as a key,
    at any depth of its nested objects and arrays, raises `ValueError` naming it, and
    nothing is written or counted.

    `collapse` is `(window, name)`: `window` a `Collapse` the caller keeps for the log and
    `name` the record's class. The record is then written or counted by the window's rules
    (see `Collapse`). Without it the record is always written.
    """
    import json

    path = Path(path)
    line = (json.dumps(record, separators=(",", ":"), ensure_ascii=False) + "\n").encode()
    if refuse:
        found = sorted(_refused(record, frozenset(refuse)))
        if found:
            raise ValueError(f"a record may not hold the field{'s' if len(found) > 1 else ''} "
                             f"{', '.join(found)}")
    _check_length(line, max_bytes)
    if collapse is None:
        _write_line(path, line, max_bytes, durable)
    else:
        window, name = collapse
        window._take(path, name, line, max_bytes, durable)


def _refused(value: object, names: frozenset[str]) -> set[str]:
    """The keys of `names` that `value` holds at any depth. Called after `value` encoded,
    so it holds no cycle."""
    found: set[str] = set()
    stack = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            found.update(key for key in item if key in names)
            stack.extend(item.values())
        elif isinstance(item, (list, tuple)):
            stack.extend(item)
    return found


def _check_length(line: bytes, max_bytes: int | None) -> None:
    if max_bytes is not None and len(line) > max_bytes:
        raise ValueError(f"a record of {len(line)} bytes is longer than the log's cap "
                         f"of {max_bytes} bytes")


def _write_line(path: Path, line: bytes, max_bytes: int | None, durable: bool) -> None:
    if max_bytes is None or _size(path) + len(line) <= max_bytes:
        _append(path, line, durable=durable)
        return
    with _gated_lock(_lock_path(path)):
        # Another writer may have rolled the file while this one waited for the lock.
        if _size(path) + len(line) > max_bytes:
            try:
                _replace(path, _older(path))
            except FileNotFoundError:
                pass
        _append(path, line, durable=durable)


class Collapse:
    """The collapse of one log's busy classes, for `append_record(..., collapse=(window,
    name))`. Keep one for a log, in the one process that writes it.

    Per path and class it holds the current window, the rows written in it and the rows
    held back, under its own lock. In a window of one minute the first `after` rows of a
    class are written and the rest are counted. Once the path's file holds `hourly_past`
    bytes or more, the window is an hour and every row of it is counted. When a later row
    of that class meets a new window (the next minute or hour, or a change between the
    two), the old window's count is written first, as one row::

        {"at": ..., "kind": "summary", "class": name, "count": n,
         "per": "minute" | "hour", "since": <the window's start>}

    with the `max_bytes` and `durable` of the class's last row. A window that counted
    nothing writes no summary. A row is never held back without being counted.

    `flush` writes the summaries of windows that have ended, and `close` writes every
    pending one; a writer calls `flush` now and then and `close` at its stop. The counts
    live in this process's memory, so a crash loses the counts of the windows open at
    the time, and two processes writing one file collapse apart.

    `clock` gives the time in seconds since the epoch.
    """

    def __init__(  # noqa: MX1 -- after is a count of rows, not a duration
            self, after: int = 10, hourly_past: int | None = None, *,
            clock: Callable[[], float] = time.time) -> None:
        if after < 0:
            raise ValueError("after must be zero or more")
        if hourly_past is not None and hourly_past < 0:
            raise ValueError("hourly_past must be zero or more")
        self.after = after
        self.hourly_past = hourly_past
        self._clock = clock
        self._lock = threading.Lock()
        #: (path, class) -> [window start, per, written, held, max_bytes, durable]
        self._windows: dict[tuple[Path, str], list] = {}

    def flush(self, path: str | os.PathLike | None = None) -> None:
        """Write the summaries of the windows of `path` (of every path, with none) that
        have ended."""
        now = self._clock()
        with self._lock:
            for key in self._keys(path):
                window = self._windows[key]
                if _window_start(now, window[1]) != window[0]:
                    self._summary(key, self._windows.pop(key))

    def close(self, path: str | os.PathLike | None = None) -> None:
        """Write every pending summary of `path` (of every path, with none)."""
        with self._lock:
            for key in self._keys(path):
                self._summary(key, self._windows.pop(key))

    def _keys(self, path: str | os.PathLike | None) -> list[tuple[Path, str]]:
        wanted = None if path is None else _key_path(path)
        return [key for key in self._windows if wanted is None or key[0] == wanted]

    def _take(self, path: Path, name: str, line: bytes, max_bytes: int | None,
              durable: bool) -> None:
        now = self._clock()
        key = (_key_path(path), name)
        with self._lock:
            hourly = self.hourly_past is not None and _size(path) >= self.hourly_past
            per = "hour" if hourly else "minute"
            start = _window_start(now, per)
            window = self._windows.get(key)
            if window is not None and (window[0] != start or window[1] != per):
                self._summary(key, self._windows.pop(key))
                window = None
            if window is None:
                window = self._windows[key] = [start, per, 0, 0, max_bytes, durable]
            window[4], window[5] = max_bytes, durable
            if per == "minute" and window[2] < self.after:
                _write_line(path, line, max_bytes, durable)
                window[2] += 1
            else:
                window[3] += 1

    def _summary(self, key: tuple[Path, str], window: list) -> None:
        import json

        start, per, _written, held, max_bytes, durable = window
        if not held:
            return
        row = {"at": _stamp(self._clock()), "kind": "summary", "class": key[1],
               "count": held, "per": per, "since": _stamp(start * _SPAN[per])}
        line = (json.dumps(row, separators=(",", ":"), ensure_ascii=False) + "\n").encode()
        _write_line(key[0], line, max_bytes, durable)


_SPAN = {"minute": 60, "hour": 3600}


def _window_start(now: float, per: str) -> int:
    return int(now // _SPAN[per])


def _key_path(path: str | os.PathLike) -> Path:
    return Path(os.path.abspath(path))


def _stamp(when: float) -> str:
    """`when` as UTC to the millisecond: `2026-11-04T10:44:00.123Z`."""
    millis = int(when % 1 * 1000)
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(when)) + f".{millis:03d}Z"


def _size(path: Path) -> int:
    try:
        return os.stat(path).st_size
    except FileNotFoundError:
        return 0


def _append(path: Path, line: bytes, *, durable: bool = False) -> None:
    _parent(path)
    flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | _BINARY
    made = False
    if durable:
        # A first try with O_EXCL tells whether this write makes the file, so its
        # directory entry is flushed too.
        try:
            fd = os.open(path, flags | os.O_EXCL, 0o600)
            made = True
        except FileExistsError:
            fd = os.open(path, flags, 0o600)
    else:
        fd = os.open(path, flags, 0o600)
    try:
        os.write(fd, line)
        if durable:
            os.fsync(fd)
    finally:
        os.close(fd)
    if made and not _WINDOWS:
        _fsync_dir(path.parent)


def _older(path: Path) -> Path:
    return path.with_name(path.name + ".1")


def read_records(path: str | os.PathLike, *, tail: int | None = None) -> list[dict]:
    """The records of the log `path`, oldest first.

    With `tail=n`, only the last `n` records, reading the older generation `<path>.1`
    too when the current file holds fewer. Without it, every record of both. A last
    line with no newline (a write still in progress, or one torn by a crash) and a line
    that is not a JSON object are skipped. The file is read backwards, so a short tail
    of a long log reads only its end.
    """
    if tail is not None and tail < 0:
        raise ValueError("tail must be zero or more")
    if tail == 0:
        return []
    path = Path(path)
    newest_first: list[dict] = []
    for generation in (path, _older(path)):
        want = None if tail is None else tail - len(newest_first)
        newest_first.extend(_records_backwards(generation, want))
        if tail is not None and len(newest_first) >= tail:
            break
    newest_first.reverse()
    return newest_first


def _records_backwards(path: Path, want: int | None) -> list[dict]:
    """Up to `want` records of one file, newest first."""
    import json

    out: list[dict] = []
    for raw in _lines_backwards(path):
        try:
            record = json.loads(raw)
        except ValueError:
            continue
        if isinstance(record, dict):
            out.append(record)
            if want is not None and len(out) >= want:
                break
    return out


def _lines_backwards(path: Path) -> Iterator[bytes]:
    """The complete lines of `path`, last first, without their newlines."""
    try:
        fh = open(path, "rb")
    except FileNotFoundError:
        return
    with fh:
        end = fh.seek(0, os.SEEK_END)
        pending = b""       # the end of a line whose start lies in an earlier block
        newline_seen = False
        while end > 0:
            start = max(0, end - BLOCK)
            fh.seek(start)
            block = fh.read(end - start) + pending
            end = start
            parts = block.split(b"\n")
            pending = parts[0]
            complete = parts[1:]
            if not newline_seen:
                if not complete:
                    continue
                complete.pop()  # what follows the file's last newline: torn, or empty
                newline_seen = True
            for line in reversed(complete):
                if line:
                    yield line
        if pending and newline_seen:
            yield pending


# -- directories -------------------------------------------------------------------------

def private_dir(path: str | os.PathLike) -> Path:
    """Make directory `path` (and its parents) so that only its owner can use it.

    Each missing parent is created private too, and a parent that exists is left as it
    is. POSIX: created 0700; a symbolic link at `path` raises `PermissionError` and is never
    followed; an existing directory owned by another user raises `PermissionError` naming
    the owner, and an existing one of ours loses any group or other permission bits.
    Windows: created with an access list that grants only the current user, and checked
    by reading it back.

    It changes an existing directory, so it is for directories the core owns or creates;
    a directory the user may have chosen (a configured store's, say) is never given to it
    unless the core made it.
    """
    path = Path(path)
    if _WINDOWS:
        from . import _win

        _win.private_dir(path)
        return path
    import errno
    import stat

    _make_dirs(path.parent)
    try:
        os.mkdir(path, 0o700)
    except FileExistsError:
        if not (os.path.isdir(path) or os.path.islink(path)):
            raise
        # A link whose target does not exist is refused below, as any link at `path` is.
    if os.path.islink(path):
        raise PermissionError(f"{path} is a symbolic link; a private directory must not be")
    # The checks and the chmod go through a descriptor opened without following a link,
    # so a link swapped in after the check above is refused too.
    flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_DIRECTORY", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise PermissionError(
                f"{path} is a symbolic link; a private directory must not be") from None
        if exc.errno == errno.ENOTDIR:
            raise NotADirectoryError(f"{path} is not a directory") from None
        raise
    try:
        st = os.fstat(fd)
        if not stat.S_ISDIR(st.st_mode):
            raise NotADirectoryError(f"{path} is not a directory")
        if st.st_uid != os.getuid():
            raise PermissionError(
                f"{path} is owned by {_owner(st.st_uid)}, not by this user")
        if st.st_mode & 0o077:
            os.fchmod(fd, stat.S_IMODE(st.st_mode) & 0o700)
    finally:
        os.close(fd)
    return path


def _make_dirs(directory: str | os.PathLike, *, private: bool = True) -> None:
    """Make each missing directory down to `directory`, outermost first.

    With `private` each one is made 0700 (on Windows with the access list that grants only
    the current user); without it, with the default mode. A directory that already exists
    (a link to one counts) is left exactly as it is, its mode, owner and access list
    unchanged, and so is one another process makes a moment before this call would: only
    a directory this call creates is made private. `Path.mkdir(mode=..., parents=True)`
    would not do: it gives the mode to the last directory alone and makes every missing
    parent with the default mode.
    """
    directory = Path(directory)
    missing = []
    while not os.path.isdir(directory):
        missing.append(directory)
        if directory.parent == directory:
            break
        directory = directory.parent
    for each in reversed(missing):
        if private and _WINDOWS:
            from . import _win

            if not _win.make_private_dir(each) and not os.path.isdir(each):
                raise FileExistsError(f"{each} exists and is not a directory")
            continue
        try:
            os.mkdir(each, 0o700 if private else 0o777)
        except FileExistsError:
            if not os.path.isdir(each):
                raise


def _owner(uid: int) -> str:
    try:
        import pwd

        return f"{pwd.getpwuid(uid).pw_name} (uid {uid})"
    except (ImportError, KeyError):
        return f"uid {uid}"


__all__ = ["LockTimeout", "atomic_write", "lock", "adopt_core_claim", "update", "edit_toml",
           "append_record", "Collapse", "read_records", "private_dir"]

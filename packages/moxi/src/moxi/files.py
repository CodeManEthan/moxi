"""Writing a module's files safely: whole-file writes, locks, and append-only records.

Every helper here is standard library only, but for `edit_toml`'s vendored tomlkit, and
imports nothing heavy at module level, so a hook can import `moxi.files` cheaply.

- `atomic_write` replaces a file in one step: a reader sees the old text or the new,
  never half of either.
- `lock` holds an exclusive lock on a lock file for the length of a `with` block. The
  lock dies with its process, so a crashed holder frees it at once.
- `update` reads a file, changes it and writes it back under that file's lock.
- `edit_toml` does the same for a TOML file, keeping its comments and order (it imports
  the core's vendored tomlkit inside the call).
- `append_record` and `read_records` keep a log of JSON lines that rolls to one older
  generation at a size cap.
- `private_dir` makes a directory only its owner can enter.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
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
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)


# -- locks -------------------------------------------------------------------------------

# The lock files this process holds through `lock`: absolute path -> the holding thread.
_held: dict[str, int] = {}
_held_guard = threading.Lock()
# One in-process gate per lock file that `update`, `edit_toml` and `append_record` take,
# so their callers' threads queue for the lock file instead of being refused.
_gates: dict[str, threading.Lock] = {}


@contextmanager
def lock(path: str | os.PathLike, *, timeout: float | None = None) -> Iterator[None]:
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
    """
    path = Path(path)
    key = os.path.abspath(path)
    me = threading.get_ident()
    with _held_guard:
        holder = _held.get(key)
        if holder is not None:
            who = "this thread" if holder == me else "another thread of this process"
            raise RuntimeError(f"{path} is already locked by {who}; "
                               "moxi.files.lock is not re-entrant")
        _held[key] = me
    try:
        fd = _acquire(path, timeout)
    except BaseException:
        with _held_guard:
            _held.pop(key, None)
        raise
    try:
        yield
    finally:
        with _held_guard:
            _held.pop(key, None)
        _release(fd)


@contextmanager
def _gated_lock(path: Path, *, timeout: float | None = None) -> Iterator[None]:
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
        with lock(path, timeout=timeout):
            yield
    finally:
        gate.release()


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
def edit_toml(path: str | os.PathLike, *, timeout: float | None = 10.0) -> Iterator:
    """Edit the TOML file `path` in a `with` block, keeping its comments and order.

    Under the file's lock, `<path>.lock` (waiting `timeout` as `lock` does), the file is
    read and parsed with the core's vendored tomlkit, and the block gets the document
    (an empty one when the file is missing). When the block ends cleanly the document's
    text is written with `atomic_write`, keeping an existing file's permission bits; a new
    file is 0600. When the block raises, nothing is written and the exception goes on. A
    file that is not valid TOML raises `ValueError` (tomlkit's `ParseError`) before the
    block runs. When `path` is a symbolic link, the file it points to is written and the
    link is kept.

    This is the one TOML editor: `moxi config set` edits a module's config file through
    it, and a module that edits a TOML file of its own uses it rather than a TOML library
    of its own.
    """
    from ._vendor import tomlkit

    path = Path(path)
    with _gated_lock(_lock_path(path), timeout=timeout):
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
                  max_bytes: int = 1_000_000) -> None:
    """Append `record` to the log `path` as one JSON line.

    The line is written with one `write` on a descriptor opened for appending (the file
    is created 0600), so lines from several writers never interleave. When the file and
    the line together would pass `max_bytes`, the file first moves to `<path>.1` (one
    older generation is kept; on Windows a move that meets a sharing violation is tried
    again for up to a second, as `atomic_write`'s replace is) and the line starts a new
    file. A line longer than `max_bytes` raises `ValueError`.
    """
    import json

    path = Path(path)
    line = (json.dumps(record, separators=(",", ":"), ensure_ascii=False) + "\n").encode()
    if len(line) > max_bytes:
        raise ValueError(f"a record of {len(line)} bytes is longer than the log's cap "
                         f"of {max_bytes} bytes")
    if _size(path) + len(line) <= max_bytes:
        _append(path, line)
        return
    with _gated_lock(_lock_path(path)):
        # Another writer may have rolled the file while this one waited for the lock.
        if _size(path) + len(line) > max_bytes:
            try:
                _replace(path, _older(path))
            except FileNotFoundError:
                pass
        _append(path, line)


def _size(path: Path) -> int:
    try:
        return os.stat(path).st_size
    except FileNotFoundError:
        return 0


def _append(path: Path, line: bytes) -> None:
    _parent(path)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | _BINARY, 0o600)
    try:
        os.write(fd, line)
    finally:
        os.close(fd)


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

    POSIX: created 0700; a symbolic link at `path` raises `PermissionError` and is never
    followed; an existing directory owned by another user raises `PermissionError` naming
    the owner, and an existing one of ours loses any group or other permission bits.
    Windows: created with an access list that grants only the current user, and checked
    by reading it back.
    """
    path = Path(path)
    if _WINDOWS:
        from . import _win

        _win.private_dir(path)
        return path
    import errno
    import stat

    path.mkdir(mode=0o700, parents=True, exist_ok=True)
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


def _owner(uid: int) -> str:
    try:
        import pwd

        return f"{pwd.getpwuid(uid).pw_name} (uid {uid})"
    except (ImportError, KeyError):
        return f"uid {uid}"


__all__ = ["LockTimeout", "atomic_write", "lock", "update", "edit_toml", "append_record",
           "read_records", "private_dir"]

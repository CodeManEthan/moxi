"""A launch's private file and its exec verdict, from the side of the call that starts it.

The launch (argv, env, cwd) is written 0600 to a randomly named file in the runtime
`launch/` directory (0700), under the module lock; the pane runs the trampoline
(`moxi-slots-exec`, see `trampoline`) on it. Once the window is made the module lock is
released and `wait` reads the verdict, every 20 ms for at most 5 s:

- it reads the file only after it has seen the file's `flock` free: it takes the lock
  without blocking and lets it go at once, so a trampoline waiting for it only waits
  that instant. The verdict it acts on is the one read while it held the lock, so a
  trampoline whose `execve` failed has always written `failed: ...` by then;
- `exec` there: the descriptor only a successful `execve` closes is closed, so the
  command started (`started`). A pane already dead then is the command's own end, kept
  as `dead_status`, not a failure;
- `failed: ...` there: `failed`, with that line and the pane's exit status;
- the file emptied (`taken`) with the lock free, or the launch still in it and the pane
  dead: the trampoline died with no verdict (`failed`, line `trampoline died`);
- at 5 s, holding the lock, with the launch still in the file: the trampoline never
  ran. The file is removed while the lock is held, so a trampoline that opens it later
  finds it unlinked and runs nothing (`failed`, line `not taken`);
- at 5 s with the lock held by the trampoline: taken, with no verdict yet (a `cwd` on a
  hung mount, say): `unconfirmed`.

The rule: `started` is reported only on evidence that `execve` succeeded, and every bound
has a named outcome. The launch file is removed on every path.
"""

from __future__ import annotations

import json
import os
import secrets
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from . import places, tmux
from .errors import TmuxError, UsageError
from .model import FAILED, STARTED, UNCONFIRMED
from .trampoline import EXEC, FAILED as FAILED_LINE, TAKEN

#: Seconds `wait` gives the trampoline to reach a verdict.
BOUND = 5.0
#: Seconds between reads of the verdict.
POLL = 0.02
#: Seconds between reads of the pane while the launch is not yet taken.
PANE_POLL = 0.1
#: Seconds a failed launch's verdict waits for tmux to mark its pane dead.
DEAD_WAIT = 1.0
#: Seconds after which a launch file nothing holds is stale (a start gives the
#: trampoline 5 s).
STALE = 60.0
#: The trampoline's console script.
SCRIPT = "moxi-slots-exec"


@dataclass(frozen=True)
class Verdict:
    exec: str
    line: str
    dead_status: int | None = None


@dataclass(frozen=True)
class PaneState:
    """One read of a pane: whether it exists, whether it is dead, its exit status, and
    its session, window and process."""

    exists: bool
    dead: bool = False
    dead_status: int | None = None
    session: str = ""
    window_id: str = ""
    pid: int | None = None


def read_pane(pane_id: str) -> PaneState:
    """One read of `pane_id`. A pane tmux no longer has, or no server, reads as not
    existing (`display-message` prints empty fields for an unknown pane)."""
    result = tmux.attempt("display-message", "-p", "-t", pane_id,
                          "#{pane_id}\t#{pane_dead}\t#{pane_dead_status}\t#{session_name}"
                          "\t#{window_id}\t#{pane_pid}")
    if not result.ok:
        if tmux.no_server(result.err):
            return PaneState(False)
        raise TmuxError(f"tmux display-message failed (exit {result.code}): "
                             f"{result.err.strip() or 'no message'}")
    parts = result.out.rstrip("\n").split("\t")
    if len(parts) != 6 or parts[0] != pane_id:
        return PaneState(False)
    status, pid = parts[2].strip(), parts[5].strip()
    return PaneState(True, parts[1] == "1", int(status) if status.lstrip("-").isdigit()
                     else None, parts[3], parts[4], int(pid) if pid.isdigit() else None)


def check(argv: Sequence[str], env: Mapping[str, str],
          cwd: str | os.PathLike) -> tuple[list[str], dict[str, str], str]:
    """The launch's argv, env and cwd, checked: argv a non-empty list of strings, env a
    mapping of names to strings, cwd an absolute path. Raises `UsageError`."""
    if isinstance(argv, (str, bytes)) or not isinstance(argv, Sequence) or not argv \
            or not all(isinstance(a, str) and "\0" not in a for a in argv) or not argv[0]:
        raise UsageError(f"argv is a non-empty list of strings, not {argv!r}")
    if not isinstance(env, Mapping):
        raise UsageError(f"env is a mapping of names to strings, not {type(env).__name__}")
    for key, value in env.items():
        if not isinstance(key, str) or not key or "=" in key or "\0" in key \
                or not isinstance(value, str) or "\0" in value:
            raise UsageError(f"env holds names (no '=') and string values; {key!r} is not "
                             "one")
    try:
        cwd = os.fspath(cwd)
    except TypeError:
        raise UsageError(f"cwd is a path, not {cwd!r}") from None
    if not isinstance(cwd, str) or not os.path.isabs(cwd) or "\0" in cwd:
        raise UsageError(f"cwd is an absolute path, not {cwd!r}")
    return list(argv), dict(env), cwd


def trampoline() -> str:
    """The trampoline's path in this environment. Raises `TmuxError` when it is not
    installed."""
    from moxi import paths

    try:
        return str(paths.script(SCRIPT))
    except FileNotFoundError as exc:
        raise TmuxError(f"{exc}; reinstall slots (`moxi install slots`)") from None


def directory() -> Path:
    """The runtime `launch/` directory, made 0700 when absent."""
    from moxi import files

    return files.private_dir(places.launch_dir())


def write(argv: list[str], env: dict[str, str], cwd: str) -> Path:
    """Write a launch to a new, randomly named 0600 file, whole; return its path. Call
    it under the module lock."""
    from moxi import files

    path = directory() / f"{secrets.token_hex(16)}.launch"
    files.atomic_write(path, json.dumps({"argv": argv, "env": env, "cwd": cwd}),
                       mode=0o600)
    return path


def remove(path: Path) -> None:
    try:
        os.remove(path)
    except FileNotFoundError:
        pass


def _try_lock(fd: int) -> bool:
    import fcntl

    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return False
    return True


def _unlock(fd: int) -> None:
    import fcntl

    fcntl.flock(fd, fcntl.LOCK_UN)


def _text(fd: int) -> str:
    data = os.pread(fd, 65536, 0)
    if data.startswith(b"{"):
        return "{"
    return data.decode("utf-8", "replace").strip()


def sweep(older_than: float = STALE) -> list[Path]:
    """Remove launch files older than `older_than` seconds that nothing holds a lock on
    (each holds a launch's environment); return what was removed."""
    removed = []
    try:
        entries = list(os.scandir(places.launch_dir()))
    except OSError:
        return removed
    now = time.time()
    for entry in entries:
        try:
            if not entry.is_file(follow_symlinks=False) \
                    or now - entry.stat(follow_symlinks=False).st_mtime < older_than:
                continue
            fd = os.open(entry.path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
        except OSError:
            continue
        try:
            if _try_lock(fd):
                try:
                    os.remove(entry.path)
                    removed.append(Path(entry.path))
                finally:
                    _unlock(fd)
        except OSError:
            pass
        finally:
            os.close(fd)
    return removed


def _died(pane: Callable[[str], PaneState], pane_id: str, line: str,
          clock: Callable[[], float], sleep: Callable[[float], None]) -> Verdict:
    """A failed verdict with the pane's exit status, waiting a moment for tmux to mark
    the pane dead (it lags the process's end)."""
    deadline = clock() + DEAD_WAIT
    while True:
        seen = pane(pane_id)
        if not seen.exists or seen.dead or clock() >= deadline:
            return Verdict(FAILED, line, seen.dead_status if seen.dead else None)
        sleep(POLL)


def wait(path: Path, pane_id: str, *, bound: float = BOUND,
         pane: Callable[[str], PaneState] = read_pane,
         clock: Callable[[], float] = time.monotonic,
         sleep: Callable[[float], None] = time.sleep) -> Verdict:
    """The launch's exec verdict (see the module's docstring). Removes `path` on every
    path out."""
    deadline = clock() + bound
    try:
        fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    except OSError as exc:
        return Verdict(FAILED, f"the launch file is gone ({exc.strerror})")
    try:
        next_pane = clock()
        while True:
            last = clock() >= deadline
            if _try_lock(fd):
                try:
                    text = _text(fd)
                    if text == EXEC:
                        seen = pane(pane_id)
                        return Verdict(STARTED, EXEC, seen.dead_status if seen.dead
                                       else None)
                    if text.startswith(FAILED_LINE):
                        return _died(pane, pane_id, text, clock, sleep)
                    if text != "{":
                        # Emptied with no verdict, and the lock free: the trampoline is
                        # gone.
                        return _died(pane, pane_id, "trampoline died", clock, sleep)
                    if last:
                        remove(path)
                        return Verdict(FAILED, "not taken")
                    if clock() >= next_pane:
                        next_pane = clock() + PANE_POLL
                        seen = pane(pane_id)
                        if not seen.exists or seen.dead:
                            remove(path)
                            return Verdict(FAILED, "trampoline died",
                                           seen.dead_status if seen.dead else None)
                finally:
                    _unlock(fd)
            elif last:
                return Verdict(UNCONFIRMED, TAKEN)
            sleep(POLL)
    finally:
        os.close(fd)
        remove(path)


__all__ = ["BOUND", "POLL", "STALE", "SCRIPT", "Verdict", "PaneState", "read_pane",
           "check", "trampoline", "directory", "write", "remove", "sweep", "wait"]

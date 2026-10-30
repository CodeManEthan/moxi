"""Starting and watching other processes the same way on Linux, macOS and Windows.

- `run` runs a command to its end with a deadline. Its stdin is closed or fed (secrets
  travel on stdin, never in argv or the environment), and on timeout the whole tree it
  started is killed, not only the first process.
- `spawn` starts a long-lived child that cannot outlive its parent: it holds a lifeline,
  a pipe the parent keeps open, and the child ends when the parent goes. With
  `detached=True` it starts a child that is meant to outlive it, logging to a file.
- `lifeline` is what a Moxi child started with `lifeline="fd"` (or a plain stdin
  lifeline) calls to stop when its parent goes.
- `identity` and `alive` name a process by its pid and start time, so a pid the system
  has given to another process since never matches.

psutil is imported only inside `identity`, so importing this module is cheap.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

_WINDOWS = sys.platform == "win32"

#: The variable that names the lifeline's read end in a child started with
#: `lifeline="fd"`: a file descriptor on POSIX, a handle on Windows.
LIFELINE_ENV = "MOXI_LIFELINE_FD"
#: How long a timed-out `run` waits between SIGTERM and SIGKILL (POSIX), and the
#: watchdog between the two when a lifeline closes.
KILL_GRACE = 2.0
#: How long `lifeline`'s default stop waits after interrupting the main thread before it
#: ends the process.
EXIT_GRACE = 5.0
# How far apart, in seconds, two reads of one process's start time may be for `alive`
# to take them as one process. A pid is not given to another process within a second.
_START_TOLERANCE = 1.0
#: The lifeline kinds `spawn` takes.
LIFELINES = ("stdin", "fd", "watch")

Argv = Sequence[str | os.PathLike]


# -- run ---------------------------------------------------------------------------------

def run(argv: Argv, *, timeout: float, input: bytes | str | None = None,
        env: Mapping[str, str] | None = None, cwd: str | os.PathLike | None = None,
        capture: bool = True) -> subprocess.CompletedProcess:
    """Run `argv` to its end, or kill everything it started once `timeout` seconds pass.

    stdin is closed (`/dev/null`), or a pipe fed `input` and then closed. With `capture`
    stdout and stderr are collected; without it they are this process's own. Output is
    `str` when `input` is a `str` and `bytes` otherwise. `env` is the child's whole
    environment, used as given (None: this process's). The exit status is not checked.

    POSIX: the child starts in a new session, so it and everything it starts form one
    process group; on timeout the group gets SIGTERM and, two seconds later, SIGKILL
    for any member still running, whether or not the first child has ended by then.
    Windows: the child starts in a new process group and is put in a Job Object as soon
    as it starts; on timeout the job is terminated, which ends the whole tree.

    On timeout `subprocess.TimeoutExpired` is raised, carrying what was captured.
    """
    text = isinstance(input, str)
    pipe = subprocess.PIPE if capture else None
    options: dict = {}
    if _WINDOWS:
        options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        options["start_new_session"] = True
    proc = subprocess.Popen(
        _argv(argv), stdin=subprocess.DEVNULL if input is None else subprocess.PIPE,
        stdout=pipe, stderr=pipe, env=_env(env), cwd=cwd, text=text,
        encoding="utf-8" if text else None, **options)
    job = None
    try:
        if _WINDOWS:
            from . import _win

            job = _win.Job()
            job.assign(proc.pid)
        try:
            out, err = proc.communicate(input, timeout=timeout)
        except subprocess.TimeoutExpired:
            out, err = _end_tree(proc, job)
            raise subprocess.TimeoutExpired(proc.args, timeout, output=out,
                                            stderr=err) from None
    except BaseException:
        if proc.returncode is None:
            _kill_now(proc, job)
        raise
    finally:
        if job is not None:
            job.close()
    return subprocess.CompletedProcess(proc.args, proc.returncode, out, err)


def _end_tree(proc: subprocess.Popen, job) -> tuple:
    """Kill a timed-out child's whole tree and collect what it wrote.

    POSIX: SIGTERM to the group, then up to `KILL_GRACE` seconds for the group to empty,
    then SIGKILL to whatever of it is left. The first child may end and close its pipes
    well before then while another member, one that ignores SIGTERM and holds no pipe,
    keeps running; the group, not the first child, decides when the grace is over.
    """
    if job is not None:
        job.terminate()
        return _drain(proc)
    import time

    _signal_group(proc.pid, _sig("SIGTERM"))
    deadline = time.monotonic() + KILL_GRACE
    try:
        out, err = proc.communicate(timeout=KILL_GRACE)
    except subprocess.TimeoutExpired:
        _signal_group(proc.pid, _sig("SIGKILL"))
        return _drain(proc)
    # The first child is reaped and its pipes are closed. The kernel gives no new process
    # a pid that still names a group, so the group id stays ours while any member lives.
    while _group_alive(proc.pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    if _group_alive(proc.pid):
        _signal_group(proc.pid, _sig("SIGKILL"))
    return out, err


def _kill_now(proc: subprocess.Popen, job) -> None:
    if not _WINDOWS:
        _signal_group(proc.pid, _sig("SIGKILL"))
    else:
        if job is not None:
            job.terminate()
        proc.kill()  # the job may not hold it yet, if putting it there was what failed
    _drain(proc)


def _drain(proc: subprocess.Popen) -> tuple:
    """Reap a killed child and take what output is left, without waiting on a descendant
    that escaped the kill and still holds a pipe open."""
    try:
        return proc.communicate(timeout=KILL_GRACE)
    except subprocess.TimeoutExpired:
        proc.wait()
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass
        return None, None


def _group_alive(pgid: int) -> bool:
    """Whether process group `pgid` has a member (one that ended but is not yet reaped
    counts)."""
    try:
        os.killpg(pgid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    return True


def _signal_group(pgid: int, signum: int) -> None:
    try:
        os.killpg(pgid, signum)
    except (ProcessLookupError, PermissionError):
        pass  # the group is gone (or, after a reuse, not ours)


def _sig(name: str) -> int:
    import signal

    return getattr(signal, name)


# -- spawn -------------------------------------------------------------------------------

def spawn(argv: Argv, *, lifeline: str | None = None,
          env: Mapping[str, str] | None = None, cwd: str | os.PathLike | None = None,
          stdout=None, stderr=None, detached: bool = False,
          output: str | os.PathLike | None = None) -> subprocess.Popen | int:
    """Start `argv` without waiting for it.

    Without `detached`, the child holds a lifeline and ends when this process goes, by
    any death. `lifeline` (default `"stdin"`) is how:

    - `"stdin"`: the child's stdin is a pipe this process keeps open; a child that stops
      at EOF on stdin stops when the pipe closes.
    - `"fd"`: an extra pipe whose read end the child inherits, named in
      `MOXI_LIFELINE_FD`, so its stdin stays free. The child calls `moxi.proc.lifeline()`.
    - `"watch"`: for a program that knows nothing of lifelines. POSIX: the child starts in
      a new session, and a small watchdog process holds the lifeline; when it closes, the
      watchdog sends the child's process group SIGTERM and, two seconds later, SIGKILL.
      Windows: the child is put in a Job Object that kills it when this process closes it
      or dies.

    The returned `Popen` carries the lifeline as `lifeline`, an object with `close()`:
    closing it tells the child to stop, as this process's death would. It is closed when
    the `Popen` is garbage collected, so keep the `Popen`. `stdout` and `stderr` are
    passed to `Popen` (the parent may take stdout as a pipe); stdin is the child's own
    unless the lifeline is `"stdin"`.

    With `detached=True` the child is meant to outlive this process: it starts in a new
    session (POSIX) or as a detached process in a new process group outside any job
    (Windows), its stdin is `/dev/null`, and its stdout and stderr are appended to
    `output` (created 0600; `/dev/null` when None). The child's pid is returned at once.
    A lifeline, `stdout` or `stderr` with `detached=True` raises `ValueError`.

    `env` is the child's whole environment, used as given (None: this process's); the
    `"fd"` lifeline adds its one variable to it.
    """
    if detached:
        if lifeline is not None:
            raise ValueError("a detached child has no lifeline")
        if stdout is not None or stderr is not None:
            raise ValueError("a detached child writes to output=, not stdout= or stderr=")
        return _spawn_detached(argv, env=env, cwd=cwd, output=output)
    if output is not None:
        raise ValueError("output= is for a detached child; pass stdout= and stderr=")
    kind = "stdin" if lifeline is None else lifeline
    if kind == "stdin":
        proc = subprocess.Popen(_argv(argv), stdin=subprocess.PIPE, stdout=stdout,
                                stderr=stderr, env=_env(env), cwd=cwd)
        proc.lifeline = proc.stdin
        return proc
    if kind == "fd":
        return _spawn_fd(argv, env=env, cwd=cwd, stdout=stdout, stderr=stderr)
    if kind == "watch":
        return _spawn_watched(argv, env=env, cwd=cwd, stdout=stdout, stderr=stderr)
    raise ValueError(f"lifeline must be one of {', '.join(LIFELINES)}, not {kind!r}")


def _spawn_fd(argv: Argv, *, env, cwd, stdout, stderr) -> subprocess.Popen:
    read_end, write_end = os.pipe()
    try:
        child_env = dict(os.environ if env is None else env)
        options: dict = {}
        if _WINDOWS:
            import msvcrt

            handle = msvcrt.get_osfhandle(read_end)
            os.set_handle_inheritable(handle, True)
            child_env[LIFELINE_ENV] = str(handle)
            info = subprocess.STARTUPINFO()
            info.lpAttributeList = {"handle_list": [handle]}
            options["startupinfo"] = info
        else:
            child_env[LIFELINE_ENV] = str(read_end)
            options["pass_fds"] = (read_end,)
        proc = subprocess.Popen(_argv(argv), stdout=stdout, stderr=stderr, env=child_env,
                                cwd=cwd, **options)
    except BaseException:
        os.close(write_end)
        raise
    finally:
        os.close(read_end)
    proc.lifeline = open(write_end, "wb", buffering=0)
    return proc


def _spawn_watched(argv: Argv, *, env, cwd, stdout, stderr) -> subprocess.Popen:
    if _WINDOWS:
        from . import _win

        proc = subprocess.Popen(_argv(argv), stdout=stdout, stderr=stderr, env=_env(env),
                                cwd=cwd)
        job = _win.Job(kill_on_close=True)
        try:
            job.assign(proc.pid)
        except BaseException:
            job.close()
            proc.kill()
            proc.wait()
            raise
        proc.lifeline = job
        return proc
    proc = subprocess.Popen(_argv(argv), stdout=stdout, stderr=stderr, env=_env(env),
                            cwd=cwd, start_new_session=True)
    try:
        # The watchdog sits in a session of its own too, so a signal meant for this
        # process's terminal group (Ctrl-C) never reaches it: only its stdin ends it.
        # -P: the watchdog's directory is not put on its path, so no file there can
        # stand in for moxi.
        from .paths import python

        watchdog = subprocess.Popen(
            [str(python()), "-P", "-m", "moxi._watch", str(proc.pid)],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            cwd="/", start_new_session=True)
    except BaseException:
        _signal_group(proc.pid, _sig("SIGKILL"))
        proc.wait()
        raise
    proc.lifeline = watchdog.stdin
    proc.watchdog = watchdog
    return proc


def _spawn_detached(argv: Argv, *, env, cwd, output) -> int:
    if output is None:
        out = os.open(os.devnull, os.O_WRONLY)
    else:
        from pathlib import Path

        path = Path(output)
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        out = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND
                      | getattr(os, "O_BINARY", 0), 0o600)
    try:
        common = dict(stdin=subprocess.DEVNULL, stdout=out, stderr=out, env=_env(env),
                      cwd=cwd)
        if not _WINDOWS:
            proc = subprocess.Popen(_argv(argv), start_new_session=True, **common)
        else:
            flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
            try:
                proc = subprocess.Popen(
                    _argv(argv), creationflags=flags | subprocess.CREATE_BREAKAWAY_FROM_JOB,
                    **common)
            except PermissionError:
                # This process is in a job that forbids breaking away; the child then
                # stays in that job, as any child of this process would.
                proc = subprocess.Popen(_argv(argv), creationflags=flags, **common)
    finally:
        os.close(out)
    with _detached_guard:
        _detached[:] = [child for child in _detached if child.poll() is None]
        _detached.append(proc)
    return proc.pid


# Detached children this process started and has not yet seen end. Each `spawn(...,
# detached=True)` polls them, so one that has ended is reaped (no zombie is left) without
# this process ever waiting for one.
_detached: list[subprocess.Popen] = []
_detached_guard = threading.Lock()


def _argv(argv: Argv) -> list[str]:
    if isinstance(argv, (str, bytes)):
        raise TypeError("argv is a sequence of arguments, not one string")
    return [os.fspath(part) for part in argv]


def _env(env: Mapping[str, str] | None) -> dict[str, str] | None:
    return None if env is None else dict(env)


# -- in a child --------------------------------------------------------------------------

def lifeline(on_close: Callable[[], None] | None = None) -> None:
    """Stop this process when its parent goes: call this once, early, in a Moxi child.

    The lifeline is the pipe named by `MOXI_LIFELINE_FD` (a child started with
    `spawn(..., lifeline="fd")`), else stdin. A daemon thread reads it until EOF, which
    comes when the parent closes it or dies. Then it calls `on_close`; by default it
    interrupts the main thread (SIGINT on POSIX, so a `KeyboardInterrupt` there unless
    SIGINT is ignored) and, if the process is still running five seconds later, ends it
    with exit status 1. The variable is removed from
    this process's environment, so a process it starts in turn does not take the pipe for
    its own.
    """
    value = os.environ.pop(LIFELINE_ENV, "")
    if value:
        number = int(value)
        if _WINDOWS:
            import msvcrt

            fd = msvcrt.open_osfhandle(number, os.O_RDONLY)
        else:
            fd = number
    else:
        fd = 0
    thread = threading.Thread(target=_hold, args=(fd, on_close), name="moxi-lifeline",
                              daemon=True)
    thread.start()


def _hold(fd: int, on_close: Callable[[], None] | None) -> None:
    while True:
        try:
            if not os.read(fd, 4096):
                break
        except OSError:
            break
    if on_close is not None:
        on_close()
        return
    import time

    _interrupt_main()
    time.sleep(EXIT_GRACE)
    os._exit(1)


def _interrupt_main() -> None:
    """Raise `KeyboardInterrupt` in the main thread, even one blocked in a system call.

    POSIX: a real SIGINT sent to the main thread, which breaks a blocking call such as a
    sleep or a read (`_thread.interrupt_main` only sets a flag the thread sees once the
    call returns). Windows: `_thread.interrupt_main`, which also wakes a sleep there.
    """
    if _WINDOWS:
        import _thread

        _thread.interrupt_main()
        return
    import signal

    signal.pthread_kill(threading.main_thread().ident, signal.SIGINT)


# -- identity ----------------------------------------------------------------------------

@dataclass(frozen=True)
class ProcId:
    """A process named by its pid and its start time, in seconds since the epoch."""

    pid: int
    started: float


def identity(pid: int) -> ProcId | None:
    """The process `pid` as a `ProcId`, or None when there is no such process (or it
    can't be read)."""
    if pid <= 0:
        return None
    import psutil

    try:
        return ProcId(pid, psutil.Process(pid).create_time())
    except psutil.Error:
        return None


def alive(proc: ProcId) -> bool:
    """Whether the process `proc` names is still running: its pid exists, started at the
    same time (so a pid given to another process since never matches) and has not ended
    (a child that ended and was not yet reaped by its parent is not alive).

    "The same time" is within a second: a start time is the boot time plus the
    ticks since, and the boot time read moves when the wall clock is adjusted (NTP), so
    two reads of one process's start time can differ.
    """
    if proc.pid <= 0:
        return False
    import psutil

    try:
        found = psutil.Process(proc.pid)
        return (abs(found.create_time() - proc.started) < _START_TOLERANCE
                and found.status() != psutil.STATUS_ZOMBIE)
    except psutil.Error:
        return False


__all__ = ["LIFELINE_ENV", "ProcId", "run", "spawn", "lifeline", "identity", "alive"]

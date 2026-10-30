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
- `environ` reads another process's environment as it was started, where the system
  lets one: Linux and macOS.
- `status` says how a detached child this process started ended.
- `parent` reads another process's parent pid, with the standard library only.

psutil is imported only inside the functions that use it, so importing this module is
cheap.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from collections import deque
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
#: How long `spawn(..., lifeline="watch")` waits for its watchdog to say it is watching.
WATCH_START = 10.0
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
    - `"watch"`: for a program that knows nothing of lifelines and may keep the terminal.
      The child has the terminal exactly as a plain child of this process has it, and
      the terminal's foreground group is never moved. POSIX: the child stays in this
      process's group and session, so a stop, Ctrl-C, a resize or a background read
      reaches it as it reaches this process. A small watchdog in a session of its own
      holds the lifeline and keeps a set of the child's descendants, each named by pid
      and start time; when the lifeline closes it sends SIGTERM to the child and that
      set and, two seconds later, SIGKILL to what is left (see `moxi._watch`). `spawn`
      returns once the watchdog says it is watching; one that fails to start has the
      child and its descendants killed and `OSError` raised. Windows: the child is put
      in a Job Object that kills it when this process closes it or dies.

    The returned `Popen` carries the lifeline as `lifeline`, an object with `close()`:
    closing it tells the child to stop, as this process's death would. It is closed when
    the `Popen` is garbage collected, so keep the `Popen`. `stdout` and `stderr` are
    passed to `Popen` (the parent may take stdout as a pipe); stdin is the child's own
    unless the lifeline is `"stdin"`.

    With `detached=True` the child is meant to outlive this process: it starts in a new
    session (POSIX) or as a detached process in a new process group outside any job
    (Windows), its stdin is `/dev/null`, and its stdout and stderr are appended to
    `output` (created 0600; `/dev/null` when None). The child's pid is returned at once;
    its `Popen` is kept in this process, so `status(pid)` can read how it ended.
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
    # The child stays in this process's group and session, as a plain child does, so
    # the terminal treats the two alike: a stop, Ctrl-C, a resize or a background read
    # reaches both. Nothing here moves the terminal's foreground group.
    proc = subprocess.Popen(_argv(argv), stdout=stdout, stderr=stderr, env=_env(env),
                            cwd=cwd)
    try:
        watchdog = _start_watchdog(proc)
    except BaseException:
        _kill_tree(proc)
        raise
    proc.lifeline = watchdog.stdin
    proc.watchdog = watchdog
    # The watchdog is reaped as soon as it exits, so a long-running parent keeps no
    # ended watchdog for each child it watched. `wait` and `poll` still answer.
    threading.Thread(target=watchdog.wait, name=f"moxi-watchdog-{watchdog.pid}",
                     daemon=True).start()
    return proc


def _start_watchdog(proc: subprocess.Popen) -> subprocess.Popen:
    """Start the watchdog for `proc` and wait until it says it is watching.

    The watchdog sits in a session of its own, so no signal from this process's terminal
    (Ctrl-C, a stop, a hangup) reaches it: only the end of its stdin ends it. `-P`: the
    watchdog's directory is not put on its path, so no file there can stand in for moxi.
    Raises `OSError` when it can't be started or exits without saying it is ready.
    """
    import selectors
    import time

    from . import _watch
    from .paths import python

    ident = identity(proc.pid)
    if ident is None:
        if proc.poll() is None:
            raise OSError(f"can't read the start time of process {proc.pid}")
        started = 0.0  # it has ended already: a start time no process has
    else:
        started = ident.started
    # stderr has a pipe of its own: an interpreter may write to it before the ready
    # line (a warning, an import-time profile), and that must not hide the line.
    watchdog = subprocess.Popen(
        [str(python()), "-P", "-m", "moxi._watch", str(proc.pid), repr(started)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        cwd="/", start_new_session=True)
    said = b""
    err = b""
    late = False
    # A selector, not select.select: a descriptor numbered past 1024, in a process that
    # holds many, is out of select's range.
    waiting = None
    try:
        deadline = time.monotonic() + WATCH_START
        out = watchdog.stdout.fileno()
        waiting = selectors.DefaultSelector()
        waiting.register(out, selectors.EVENT_READ)
        waiting.register(watchdog.stderr.fileno(), selectors.EVENT_READ)
        # Read until the ready line, or to the end of what a failing watchdog writes
        # (a traceback's last line says why).
        while (waiting.get_map() and not said.startswith(_watch.READY)
               and len(said) < 65536):
            left = deadline - time.monotonic()
            if left <= 0:
                late = True
                break
            for key, _ in waiting.select(left):
                part = os.read(key.fd, 4096)
                if not part:
                    waiting.unregister(key.fd)
                elif key.fd == out:
                    said += part
                else:
                    err = (err + part)[-65536:]
        if said.startswith(_watch.READY):
            return watchdog
    except Exception as exc:
        _end_watchdog(watchdog)
        if isinstance(exc, OSError):
            raise
        raise OSError(f"the watchdog for process {proc.pid} did not start: {exc}") from exc
    except BaseException:
        _end_watchdog(watchdog)
        raise
    finally:
        if waiting is not None:
            waiting.close()
        watchdog.stdout.close()
        watchdog.stderr.close()
    _end_watchdog(watchdog)
    lines = (err.decode(errors="replace").strip().splitlines()
             or said.decode(errors="replace").strip().splitlines())
    if lines:
        why = lines[-1]
    elif late:
        why = f"it said nothing within {WATCH_START:g} seconds"
    else:
        why = f"it exited with status {watchdog.returncode}"
    raise OSError(f"the watchdog for process {proc.pid} did not start: {why}")


def _end_watchdog(watchdog: subprocess.Popen) -> None:
    if watchdog.poll() is None:
        watchdog.kill()
    watchdog.wait()
    watchdog.stdin.close()


def _kill_tree(proc: subprocess.Popen) -> None:
    """SIGKILL a child and its descendants, the child first so it starts no more, and
    reap the child."""
    import psutil

    try:
        below = psutil.Process(proc.pid).children(recursive=True)
    except psutil.Error:
        below = []
    if proc.poll() is None:
        proc.kill()
    for process in below:
        try:
            process.kill()  # psutil checks the pid still names the process it found
        except psutil.Error:
            pass
    proc.wait()


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
    # The new child is started before the old ones are polled: a pid is free for reuse
    # only once its child is reaped, and the reap below keeps that child's code, so a new
    # child can only get a pid whose earlier code is already kept.
    with _detached_guard:
        still = []
        for child in _detached:
            code = child.poll()
            if code is None:
                still.append(child)
            else:
                _keep_code(child.pid, code)
        still.append(proc)
        _detached[:] = still
    return proc.pid


def _keep_code(pid: int, code: int) -> None:
    """Keep an ended detached child's code until `status` reads it. Past the bound the
    oldest unread code is dropped. The caller holds `_detached_guard`."""
    _codes.append((pid, code))
    while len(_codes) > CODES_KEPT:
        _codes.popleft()


def status(pid: int) -> int | None:
    """How a child this process started with `spawn(..., detached=True)` ended.

    None while it runs; its exit code once it has ended, read by `Popen.poll()` on the
    `Popen` `spawn` kept, which also reaps it. On POSIX a negative code -N means signal N
    ended it; on Windows the code is the process's own exit code. The first call that
    returns a code forgets the child: a later call for that pid raises `LookupError`
    (unless a later detached child has the pid by then, below), and so does a call for
    any pid this process did not start detached. For another process's liveness use
    `identity` and `alive`.

    An ended child's code is kept until `status` returns it, and no ended child stays a
    zombie past this process's next detached `spawn`, which reaps every ended one and
    keeps its code. For one pid, children are answered in the order they were started: a
    reaped child's pid is free, so a later detached child can get it while the earlier
    code is unread; the earlier code comes first, then the later child's state. A caller
    that reads its children oldest first, or each before it starts the next, always
    reads its own child's end.

    The kept codes are one table for the whole process, shared by every caller of
    `spawn(..., detached=True)` in it, and the order holds only among the children one
    caller reads. A caller that shares the process with another user of detached
    children must read every child it starts, or a later child of the other caller that
    gets the same pid is answered with its unread code first.

    At most `CODES_KEPT` (1024) unread codes are held, oldest dropped first. A dropped
    code is lost: its `status` answers as if it never existed, or by the pid's later
    child when one is kept. A caller that reads each child it starts never reaches the
    bound.

    The code is `Popen`'s own. In a process that reaps its children itself
    (`os.waitpid(-1, ...)`, or SIGCHLD ignored), a child it took reads 0. With SIGCHLD
    ignored a pid can come back while its earlier `Popen` is still kept; the pid's oldest
    kept `Popen` is polled first.
    """
    if isinstance(pid, int) and not isinstance(pid, bool):
        with _detached_guard:
            for at, (kept, code) in enumerate(_codes):
                if kept == pid:
                    del _codes[at]
                    return code
            for at, child in enumerate(_detached):
                if child.pid == pid:
                    code = child.poll()
                    if code is not None:
                        del _detached[at]
                    return code
    raise LookupError(f"process {pid!r} is not a detached child of this process whose "
                      f"status is still unread")


#: The most unread exit codes of ended detached children `status` keeps.
CODES_KEPT = 1024

# Detached children this process started and has not yet seen end, oldest first. Each
# `spawn(..., detached=True)` polls them, so one that has ended is reaped (no zombie is
# left) without this process ever waiting for one, and its code moves to `_codes`.
_detached: list[subprocess.Popen] = []
# The codes of ended detached children no `status` call has returned yet, as (pid, code),
# in the order they were seen to end, which for one pid is the order they were started.
_codes: deque[tuple[int, int]] = deque()
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


# -- environment -------------------------------------------------------------------------

def environ(pid: int) -> dict[str, str] | None:
    """The environment process `pid` was started with, or None when it can't be read.

    Linux reads `/proc/<pid>/environ`. macOS asks the kernel for the process's arguments
    (`sysctl` with `KERN_PROCARGS2`), which answers only for a process of the same user.
    Windows, and any other system, returns None. A process that is gone, or one the
    system won't show, returns None everywhere.

    What is read is the environment the process started with: a variable it set or
    removed itself since is not seen. An entry without `=` is skipped, and bytes that are
    not UTF-8 are replaced. Standard library only.
    """
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return None
    if sys.platform.startswith("linux"):
        return _linux_environ(pid)
    if sys.platform == "darwin":
        return _mac_environ(pid)
    return None


def _linux_environ(pid: int, root: str = "/proc") -> dict[str, str] | None:
    try:
        with open(os.path.join(root, str(pid), "environ"), "rb") as f:
            raw = f.read()
    except OSError:
        return None
    # A zombie reads as an empty environment; it has ended, so it is gone.
    if not raw and _linux_ended(pid, root):
        return None
    return _pairs(raw.split(b"\0"))


def _linux_ended(pid: int, root: str) -> bool:
    """Whether `pid` has ended (a zombie, or gone) by its `stat` file's state."""
    try:
        with open(os.path.join(root, str(pid), "stat"), "rb") as f:
            stat = f.read()
    except OSError:
        return True
    # The state follows the command name, which is in parentheses and may hold any byte.
    state = stat[stat.rfind(b")") + 1:].split()[:1]
    return state in ([b"Z"], [b"X"], [b"x"])


def _mac_environ(pid: int) -> dict[str, str] | None:
    import ctypes
    import ctypes.util

    try:
        libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
        sysctl = libc.sysctl
    except (OSError, AttributeError):
        return None
    sysctl.argtypes = (ctypes.POINTER(ctypes.c_int), ctypes.c_uint, ctypes.c_void_p,
                       ctypes.POINTER(ctypes.c_size_t), ctypes.c_void_p, ctypes.c_size_t)
    sysctl.restype = ctypes.c_int
    # The largest argument area a process may have, which bounds what PROCARGS2 returns.
    mib = (ctypes.c_int * 2)(_CTL_KERN, _KERN_ARGMAX)
    argmax = ctypes.c_int(0)
    size = ctypes.c_size_t(ctypes.sizeof(argmax))
    if sysctl(mib, 2, ctypes.byref(argmax), ctypes.byref(size), None, 0) != 0:
        return None
    if argmax.value <= 0:
        return None
    buffer = ctypes.create_string_buffer(argmax.value)
    size = ctypes.c_size_t(argmax.value)
    mib = (ctypes.c_int * 3)(_CTL_KERN, _KERN_PROCARGS2, pid)
    if sysctl(mib, 3, buffer, ctypes.byref(size), None, 0) != 0:
        return None  # gone, another user's, or refused
    return _procargs2(buffer.raw[:size.value])


# macOS's sysctl names: <sys/sysctl.h>.
_CTL_KERN = 1
_KERN_ARGMAX = 8
_KERN_PROCARGS2 = 49
#: The size of a pointer in a 64-bit process, the boundary the path is padded to.
_POINTER = 8


def _procargs2(raw: bytes) -> dict[str, str] | None:
    """The environment in a `KERN_PROCARGS2` answer.

    Its layout: `argc` as a C int; the executable's path, NUL-terminated, then NUL
    padding to a pointer-size boundary counted from the path's start; the `argc`
    arguments, each NUL-terminated; the environment's entries, each NUL-terminated, ended
    by an empty string; then strings the system keeps for itself, which are not the
    environment.
    """
    width = 4
    if len(raw) < width:
        return None
    argc = int.from_bytes(raw[:width], sys.byteorder, signed=True)
    if argc < 0:
        return None
    at = raw.find(b"\0", width)
    if at < 0:
        return None
    # The path is padded with NULs to a pointer-size boundary from its start; only that
    # padding is skipped, so an empty argv[0] after it is still read as an argument. Fewer
    # NULs than that rule asks for means another layout: then every NUL is padding.
    nuls = 0
    while at + nuls < len(raw) and raw[at + nuls] == 0:
        nuls += 1
    padded = width + _round_up(at + 1 - width, _POINTER)
    at = padded if at + nuls >= padded else at + nuls
    for _ in range(argc):
        end = raw.find(b"\0", at)
        if end < 0:
            return None  # cut short
        at = end + 1
    entries = []
    while at < len(raw):
        end = raw.find(b"\0", at)
        if end < 0:
            end = len(raw)
        if end == at:
            break  # the empty string that ends the environment
        entries.append(raw[at:end])
        at = end + 1
    return _pairs(entries)


def _round_up(n: int, to: int) -> int:
    return -(-n // to) * to


# -- parent ------------------------------------------------------------------------------

def parent(pid: int) -> int | None:
    """The pid of process `pid`'s parent, or None when it can't be read.

    A pid that is gone, can't be read, or is not a positive `int` gives None. A process
    with no parent the reader can see gives the 0 the system gives: Linux's pid 1 and
    `kthreadd`, a process whose parent is outside the reader's pid namespace, and macOS's
    `launchd`. So 0 means no parent is visible, and None means no answer.

    Linux reads `/proc/<pid>/stat`. macOS asks the kernel (`sysctl` with
    `KERN_PROC_PID`), which answers for any user's process. Windows walks a snapshot of
    the process list (Toolhelp). Standard library only, so no psutil import.

    On Linux and macOS a child whose parent ended reads its new parent (init, or a
    subreaper). Windows never reparents: the pid named is the parent's at the child's
    start, and that parent may have ended since and its pid been given to another
    process. A caller that must know the parent pairs this with `identity` and compares
    start times: a process started after the child is not its parent.
    """
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return None
    if sys.platform.startswith("linux"):
        return _linux_parent(pid)
    if sys.platform == "darwin":
        return _mac_parent(pid)
    if _WINDOWS:
        return _windows_parent(pid)
    return None


def _linux_parent(pid: int, root: str = "/proc") -> int | None:
    try:
        with open(os.path.join(root, str(pid), "stat"), "rb") as f:
            stat = f.read()
    except OSError:
        return None
    # The command name is in parentheses and may hold spaces and parentheses, so the
    # fields are counted from the last ")": the state, then the parent's pid.
    fields = stat[stat.rfind(b")") + 1:].split()
    try:
        return int(fields[1])
    except (IndexError, ValueError):
        return None


# macOS's `struct kinfo_proc` of a 64-bit process (x86_64 and arm64): its size, and the
# offsets of `kp_proc.p_pid` and `kp_eproc.e_ppid`, both 32-bit ints.
_KERN_PROC = 14
_KERN_PROC_PID = 1
_KINFO_PROC_SIZE = 648
_KINFO_PID_AT = 40
_KINFO_PPID_AT = 560


def _mac_parent(pid: int) -> int | None:
    import ctypes
    import ctypes.util

    try:
        libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
        sysctl = libc.sysctl
    except (OSError, AttributeError):
        return None
    sysctl.argtypes = (ctypes.POINTER(ctypes.c_int), ctypes.c_uint, ctypes.c_void_p,
                       ctypes.POINTER(ctypes.c_size_t), ctypes.c_void_p, ctypes.c_size_t)
    sysctl.restype = ctypes.c_int
    mib = (ctypes.c_int * 4)(_CTL_KERN, _KERN_PROC, _KERN_PROC_PID, pid)
    buffer = ctypes.create_string_buffer(_KINFO_PROC_SIZE)
    size = ctypes.c_size_t(_KINFO_PROC_SIZE)
    if sysctl(mib, 4, buffer, ctypes.byref(size), None, 0) != 0:
        return None
    return _kinfo_parent(buffer.raw[:size.value], pid)


def _kinfo_parent(raw: bytes, pid: int) -> int | None:
    """The parent's pid in a `KERN_PROC_PID` answer for `pid`. An empty answer is a gone
    pid. Any other size, or a `p_pid` that is not `pid`, is a layout other than the one
    named above, and gives None rather than a wrong number."""
    if len(raw) != _KINFO_PROC_SIZE:
        return None

    def field(at: int) -> int:
        return int.from_bytes(raw[at:at + 4], sys.byteorder, signed=True)

    if field(_KINFO_PID_AT) != pid:
        return None
    return field(_KINFO_PPID_AT)


def _windows_parent(pid: int) -> int | None:
    import ctypes
    from ctypes import wintypes as w

    class Entry(ctypes.Structure):  # PROCESSENTRY32W
        _fields_ = [("dwSize", w.DWORD), ("cntUsage", w.DWORD),
                    ("th32ProcessID", w.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
                    ("th32ModuleID", w.DWORD), ("cntThreads", w.DWORD),
                    ("th32ParentProcessID", w.DWORD), ("pcPriClassBase", w.LONG),
                    ("dwFlags", w.DWORD), ("szExeFile", w.WCHAR * 260)]

    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    except OSError:
        return None
    snapshot_of = kernel32.CreateToolhelp32Snapshot
    snapshot_of.argtypes = (w.DWORD, w.DWORD)
    snapshot_of.restype = w.HANDLE
    first, following = kernel32.Process32FirstW, kernel32.Process32NextW
    for call in (first, following):
        call.argtypes = (w.HANDLE, ctypes.POINTER(Entry))
        call.restype = w.BOOL
    kernel32.CloseHandle.argtypes = (w.HANDLE,)
    kernel32.CloseHandle.restype = w.BOOL
    snapshot = snapshot_of(_TH32CS_SNAPPROCESS, 0)
    if not snapshot or snapshot == ctypes.c_void_p(-1).value:
        return None
    try:
        entry = Entry()
        entry.dwSize = ctypes.sizeof(Entry)
        found = first(snapshot, ctypes.byref(entry))
        while found:
            if entry.th32ProcessID == pid:
                return int(entry.th32ParentProcessID)
            found = following(snapshot, ctypes.byref(entry))
        return None
    finally:
        kernel32.CloseHandle(snapshot)


_TH32CS_SNAPPROCESS = 0x00000002


def _pairs(entries) -> dict[str, str]:
    env: dict[str, str] = {}
    for item in entries:
        key, sep, value = item.partition(b"=")
        if sep and key:
            env[key.decode("utf-8", "replace")] = value.decode("utf-8", "replace")
    return env


__all__ = ["LIFELINE_ENV", "CODES_KEPT", "ProcId", "run", "spawn", "lifeline", "identity",
           "alive", "environ", "status", "parent"]

"""`moxi-slots-exec <launch file>`: the command every launch's pane runs first.

A launch's environment never passes through tmux: tmux would hand it to the pane's
start command and to every later pane of the server. `start` writes the launch (argv,
env, cwd) to a private file and makes the pane run this trampoline on it, which:

1. opens the file close-on-exec and waits for an exclusive `flock` on it (blocking: a
   `start` probing the lock holds it only for an instant);
2. checks the file is still linked and still holds the launch, else exits 127 without
   running anything (the `start` gave up on it and removed it);
3. reads the launch and empties the file at once (`taken`), so the environment's secrets
   leave the disk;
4. builds the child's environment (`child_env`), changes to `cwd` and resolves `argv[0]`
   on that environment's `PATH`; a failure there writes `failed: <reason>` and exits 127;
5. writes `exec` and calls `os.execve`; if that raises, writes `failed: execve: <error>`
   and exits 127. A successful `execve` closes the descriptor, which frees the lock: the
   lock free with `exec` in the file is the evidence that the command started.

The rule for the child's environment: the launch's `env` plus the names tmux sets for
this pane (`TERM_NAMES`), and nothing from the tmux server, the caller of slots, or the
slot's previous occupant. A name tmux sets is always tmux's: a launch-env entry of the
same name is replaced by the pane's value, or dropped when tmux set none, so a launch made
from inside another slot can't carry that slot's `TMUX_PANE`.

Standard library only: it runs in every pane before the command, so it starts fast and
imports nothing of Moxi.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import sys
from collections.abc import Mapping

#: The names tmux sets for a pane, which the child gets from the pane and never from the
#: launch.
TERM_NAMES = ("TERM", "COLORTERM", "TERM_PROGRAM", "TERM_PROGRAM_VERSION", "TMUX",
              "TMUX_PANE")
#: What the file holds once the launch is read: the launch is gone from it.
TAKEN = "taken"
#: What the file holds just before `execve`.
EXEC = "exec"
#: The start of what the file holds when the command could not be run.
FAILED = "failed: "
#: The exit status of a launch that did not run (as a shell's "command not found").
NOT_RUN = 127
PROG = "moxi-slots-exec"


def child_env(launch_env: Mapping[str, str], pane_env: Mapping[str, str]) -> dict[str, str]:
    """The child's whole environment: `launch_env`, with every name of `TERM_NAMES`
    taken from `pane_env` (tmux's values for this pane) and never from the launch."""
    env = {k: v for k, v in launch_env.items() if k not in TERM_NAMES}
    env.update({k: pane_env[k] for k in TERM_NAMES if k in pane_env})
    return env


def resolve(program: str, env: Mapping[str, str]) -> str | None:
    """The file `program` names: itself when it holds a `/` (relative to the working
    directory), else the first match on `env`'s `PATH`; None when it is not an
    executable file."""
    if "/" in program:
        return program if os.path.isfile(program) and os.access(program, os.X_OK) \
            else None
    return shutil.which(program, path=env.get("PATH", os.defpath))


def parse(data: bytes) -> tuple[list[str], dict[str, str], str]:
    """The (argv, env, cwd) a launch file holds. Raises `ValueError` on anything else."""
    launch = json.loads(data.decode("utf-8"))
    argv, env, cwd = launch["argv"], launch["env"], launch["cwd"]
    if not (isinstance(argv, list) and argv and all(isinstance(a, str) for a in argv)):
        raise ValueError("argv is not a list of strings")
    if not (isinstance(env, dict)
            and all(isinstance(k, str) and isinstance(v, str) for k, v in env.items())):
        raise ValueError("env is not an object of strings")
    if not isinstance(cwd, str):
        raise ValueError("cwd is not a string")
    return argv, env, cwd


def _put(fd: int, text: str) -> None:
    """Replace the file's whole content with `text` (and a newline)."""
    os.ftruncate(fd, 0)
    os.pwrite(fd, (text + "\n").encode("utf-8"), 0)


def _read_all(fd: int) -> bytes:
    chunks = []
    offset = 0
    while chunk := os.pread(fd, 65536, offset):
        chunks.append(chunk)
        offset += len(chunk)
    return b"".join(chunks)


def _say(words: str) -> None:
    """Tell the person looking at the pane: the dead pane keeps this line on screen."""
    try:
        print(f"{PROG}: {words}", file=sys.stderr, flush=True)
    except OSError:
        pass


def _fail(fd: int, reason: str) -> int:
    _put(fd, FAILED + reason)
    _say(f"the launch did not run: {reason}")
    return NOT_RUN


def main(argv: list[str] | None = None) -> int:
    import fcntl

    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        _say("usage: moxi-slots-exec LAUNCH-FILE (slots runs it; it is not run by hand)")
        return 2
    path = args[0]
    try:
        fd = os.open(path, os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW)
    except OSError as exc:
        _say(f"the launch file is gone ({exc.strerror}); the start gave up on it")
        return NOT_RUN
    fcntl.flock(fd, fcntl.LOCK_EX)
    if os.fstat(fd).st_nlink == 0:
        _say("the start gave up on this launch before it was taken; nothing ran")
        return NOT_RUN
    data = _read_all(fd)
    if not data.startswith(b"{"):
        _say("the launch file holds no launch (already taken); nothing ran")
        return NOT_RUN
    _put(fd, TAKEN)
    os.fsync(fd)
    try:
        args_, launch_env, cwd = parse(data)
    except (ValueError, KeyError, TypeError, UnicodeDecodeError) as exc:
        return _fail(fd, f"the launch file is not a launch ({exc})")
    env = child_env(launch_env, os.environ)
    try:
        os.chdir(cwd)
    except OSError as exc:
        return _fail(fd, f"cwd: {cwd}: {exc.strerror}")
    program = resolve(args_[0], env)
    if program is None:
        where = "is not an executable file" if "/" in args_[0] else \
            "is not found on the launch's PATH"
        return _fail(fd, f"argv[0]: {args_[0]} {where}")
    # Python ignores these two; an ignored signal stays ignored across execve.
    for name in ("SIGPIPE", "SIGXFSZ"):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), signal.SIG_DFL)
    _put(fd, EXEC)
    try:
        os.execve(program, args_, env)
    except OSError as exc:
        return _fail(fd, f"execve: {exc}")
    return NOT_RUN  # not reached


if __name__ == "__main__":
    sys.exit(main())

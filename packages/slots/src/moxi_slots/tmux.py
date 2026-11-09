"""The one way slots runs tmux: a direct wrapper over `moxi.proc.run`.

Every call runs the recorded tmux (`settings.tmux_path()`), names this module's socket
with `-S`, has a timeout, and runs with `TMUX` and `TMUX_PANE` removed from its
environment, so a call made from inside any tmux reaches this server and only it. No
call uses tmux's default socket or `-L`.

    call(*args, timeout=)      run `tmux -S <socket> args...`; raises on failure
    attempt(*args, timeout=)   the same, returning the result whatever the exit code
    ping(timeout=)             the server's pid, or None when nothing answers
    version(path)              `tmux -V` read and judged against the floor

A failure is `TryAgain` when no server answers on the socket (none running, a stale
socket file, a call that timed out) and `TmuxError` otherwise. A list of arguments may
chain commands with a lone `;`, which reaches tmux as its own argument.

Tests reach every call through `_run`, which a fixture replaces to fence the socket.
"""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from moxi import proc

from . import places
from .errors import TmuxError, TryAgain

#: The oldest tmux slots runs on: 3.2 is the first with `-D`.
FLOOR = (3, 2)
FLOOR_TEXT = "3.2"
#: Seconds an ordinary call gets.
TIMEOUT = 5.0
#: Seconds a ping gets (the health check's bound).
PING_TIMEOUT = 2.0
#: What tmux prints when nothing serves the socket.
_NO_SERVER = ("no server running", "error connecting to", "server exited unexpectedly",
              "lost server", "server not found")
#: The variables a client call drops, so it reaches the socket it names and no other.
DROPPED = ("TMUX", "TMUX_PANE")

_VERSION = re.compile(r"tmux\s+(\S+)")
_NUMBER = re.compile(r"(\d+)\.(\d+)")


@dataclass(frozen=True)
class Result:
    """A finished tmux call: its arguments (after the socket), exit code and output."""

    args: tuple[str, ...]
    code: int
    out: str
    err: str

    @property
    def ok(self) -> bool:
        return self.code == 0


def client_env(environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """This process's environment without `TMUX` and `TMUX_PANE`."""
    source = os.environ if environ is None else environ
    return {k: v for k, v in source.items() if k not in DROPPED}


def argv(path: str, socket: str, args: Sequence[str]) -> list[str]:
    """The argv of one call: the recorded tmux, `-S` and the socket, then `args`."""
    return [path, "-S", socket, *args]


def _run(argv: list[str], timeout: float,
         env: Mapping[str, str]) -> subprocess.CompletedProcess:
    """The one place a tmux process starts."""
    return proc.run(argv, timeout_s=timeout, env=env)


def _text(data: bytes | str | None) -> str:
    if data is None:
        return ""
    return data if isinstance(data, str) else data.decode("utf-8", "replace")


def no_server(err: str) -> bool:
    """Whether tmux's error output says nothing serves the socket."""
    low = err.lower()
    return any(marker in low for marker in _NO_SERVER)


def attempt(*args: str, timeout: float = TIMEOUT, path: str | None = None,
            socket: str | None = None) -> Result:
    """Run one call and return its result whatever its exit code.

    Raises `settings.NotSetUp` (a `TmuxError`) when tmux's path is not recorded,
    `TmuxError` when tmux cannot be started, and `TryAgain` when the call times out.
    """
    from .settings import tmux_path

    path = tmux_path() if path is None else path
    socket = places.socket() if socket is None else socket
    try:
        done = _run(argv(path, socket, args), timeout, client_env())
    except subprocess.TimeoutExpired:
        raise TryAgain(f"tmux {_name(args)} got no answer from the server on {socket} "
                       f"within {timeout:g} s") from None
    except OSError as exc:
        raise TmuxError(f"tmux could not be run ({path}: {exc}); run "
                        "`moxi install slots` to look it up again") from None
    return Result(tuple(args), done.returncode, _text(done.stdout), _text(done.stderr))


def call(*args: str, timeout: float = TIMEOUT, path: str | None = None,
         socket: str | None = None) -> Result:
    """Run one call; raise `TryAgain` when no server answers and `TmuxError` on any
    other failure."""
    result = attempt(*args, timeout=timeout, path=path, socket=socket)
    if result.ok:
        return result
    err = result.err.strip()
    if no_server(err):
        raise TryAgain(f"no slots server answers on "
                       f"{places.socket() if socket is None else socket}: {err}")
    raise TmuxError(f"tmux {_name(args)} failed (exit {result.code}): "
                    f"{err or 'no message'}")


def ping(timeout: float = PING_TIMEOUT, *, path: str | None = None,
         socket: str | None = None) -> int | None:
    """The pid of the server answering on the socket, or None when none answers in
    `timeout` seconds. Raises `TmuxError` only when tmux cannot be run at all."""
    try:
        result = attempt("display-message", "-p", "#{pid}", timeout=timeout, path=path,
                         socket=socket)
    except TryAgain:
        return None
    text = result.out.strip()
    return int(text) if result.ok and text.isdigit() else None


@dataclass(frozen=True)
class Version:
    """What `tmux -V` said: `text` as printed (or why it could not be read), and
    whether that version is at or above the floor."""

    text: str
    ok: bool


def judge(text: str) -> Version:
    """`tmux -V` output judged against the floor: `next-*` and `master` pass; a number
    passes at 3.2 or above; anything else fails."""
    text = text.strip()
    found = _VERSION.search(text)
    if not found:
        return Version(text or "no output", False)
    word = found.group(1)
    if word == "master" or word.startswith("next-"):
        return Version(text, True)
    number = _NUMBER.match(word)
    if not number:
        return Version(text, False)
    return Version(text, (int(number.group(1)), int(number.group(2))) >= FLOOR)


def version(path: str, *, timeout: float = TIMEOUT) -> Version:
    """Run `<path> -V` and judge it. Never raises: a program that can't be run is a
    failing `Version` saying why."""
    try:
        done = _run([path, "-V"], timeout, client_env())
    except subprocess.TimeoutExpired:
        return Version(f"{path} -V gave no answer within {timeout:g} s", False)
    except OSError as exc:
        return Version(f"{path} could not be run: {exc}", False)
    if done.returncode != 0:
        return Version(f"{path} -V exited {done.returncode}: "
                       f"{_text(done.stderr).strip() or 'no message'}", False)
    return judge(_text(done.stdout))


def _name(args: Sequence[str]) -> str:
    return args[0] if args else "(no command)"


__all__ = ["FLOOR", "FLOOR_TEXT", "TIMEOUT", "PING_TIMEOUT", "DROPPED", "Result",
           "Version", "client_env", "argv", "no_server", "attempt", "call", "ping",
           "judge", "version"]

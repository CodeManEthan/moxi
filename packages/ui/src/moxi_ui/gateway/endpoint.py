"""The gateway's endpoint: its kind, its socket path, the runtime directory and the hold file.

The endpoint is what the tunnel's provider forwards a phone to. It is one of two kinds,
resolved once when the shell starts and kept for the life of the process:

- `unix`, the socket `<runtime_dir>/gateway.sock`, on a system whose provider check of
  `unix:` targets passed for every provider (`UNIX_PASSED`, none today);
- `tcp`, `127.0.0.1:<gateway.port>`, everywhere else, Windows always.

A socket is bound only where `check_socket_path` passes: every directory from `/` down to
the socket is owned by root or this user and writable by no one else, so no other user can
ever create a file there. The TCP port is kept only while the shell holds it, so the shell
writes `gateway.hold` (its pid and the port) after the bind and removes it when it lets go,
and nothing points the tunnel at the port until that file names a running shell.
"""

from __future__ import annotations

import json
import os
import stat
import sys
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path

from moxi import files
from moxi.paths import Layout

__all__ = [
    "HOLD_FILE",
    "SOCKET_FILE",
    "SYSTEMS",
    "UNIX_PASSED",
    "PathRefused",
    "check_socket_path",
    "hold_port",
    "remove_hold",
    "resolve_kind",
    "runtime_problem",
    "socket_limit",
    "socket_path",
    "system",
    "write_hold",
]

MODULE = "ui"
SOCKET_FILE = "gateway.sock"
HOLD_FILE = "gateway.hold"

#: The systems the endpoint's kind is resolved for. WSL counts apart from Linux because its
#: provider may run outside the VM.
SYSTEMS = ("linux", "wsl", "darwin", "win32")
#: The systems where a provider check of a `unix:` target passed for every provider there,
#: so the endpoint is a socket. None has: on Linux tailscaled refuses a `unix:` Serve
#: entry from a user who can't run it as root, and the other systems haven't been run.
#: Every system missing here takes the TCP endpoint.
UNIX_PASSED: frozenset[str] = frozenset()


def system(platform: str | None = None, *, wsl: bool | None = None) -> str:
    """This system's name among `SYSTEMS`; any other platform reads as `linux`."""
    platform = sys.platform if platform is None else platform
    if platform == "win32" or platform == "darwin":
        return platform
    if wsl is None:
        from ..session import is_wsl

        wsl = is_wsl()
    return "wsl" if wsl else "linux"


def resolve_kind(name: str, passed: Iterable[str] = UNIX_PASSED) -> str:
    """`unix` where `name`'s `unix:` check passed (never on Windows), else `tcp`."""
    return "unix" if name != "win32" and name in frozenset(passed) else "tcp"


# --- the runtime directory ------------------------------------------------------------


def runtime_problem(environ: Mapping[str, str] | None = None, *,
                    stored: Mapping[str, str] | None = None,
                    platform: str | None = None) -> str | None:
    """The locator text when `environ` gives the ui a runtime directory other than the one
    a unit-started shell gets, else None.

    The unit's shell starts with the environment less moxi's locator variables, on
    Windows with the user's stored environment (`stored`, read here unless given), and
    with the home its unit was written from (`moxi.units.environment`). The
    check runs whether or not a unit is installed, so a shell started by hand and a
    command run from another terminal never read two directories.
    """
    from .. import cli

    environ = os.environ if environ is None else environ
    platform = sys.platform if platform is None else platform
    if stored is None and platform == "win32":
        try:
            stored = cli._stored_environment()
        except OSError:
            stored = {}
    named = cli.locator_problem(environ, cli._unit_environ(environ, stored))
    if not named:
        return None
    variables = ", ".join(named)
    return (f"{variables} moves the shell's runtime directory, so the shell a unit starts "
            f"would read the gateway's files elsewhere; unset {variables}")


# --- the socket path -------------------------------------------------------------------


class PathRefused(PermissionError):
    """The gateway's socket path fails the check; `directory` is the first that failed
    (None for a path that is only too long), and the text holds the fix."""

    def __init__(self, text: str, directory: str | None = None):
        super().__init__(text)
        self.directory = directory


def socket_path(layout: Layout | None = None, env: Mapping[str, str] | None = None) -> str:
    """`<runtime_dir>/gateway.sock`, computed directly and never moved to a shorter path."""
    return os.path.join((layout or Layout(MODULE)).runtime_dir(env), SOCKET_FILE)


def socket_limit(platform: str | None = None) -> int:
    """The most bytes a socket path may hold here: `sun_path` less its closing NUL."""
    platform = sys.platform if platform is None else platform
    return 103 if platform == "darwin" else 107


def _owner_name(uid: int) -> str:
    try:
        import pwd

        return pwd.getpwuid(uid).pw_name
    except (ImportError, KeyError):
        return f"uid {uid}"


def _refuse(directory: str, why: str, uid: int, owner: int) -> PathRefused:
    if owner == uid:
        fix = f"chmod g-w,o-w {directory}"
    else:
        fix = f"ask {_owner_name(owner)}, who owns it, to make it writable by its owner only"
    return PathRefused(f"the gateway's socket can't live under {directory}: {why}; {fix}",
                       directory)


def _walk(path: str, uid: int, lstat: Callable[[str], os.stat_result]) -> None:
    """Check each directory from `/` down to `path`, lexically, with `lstat`."""
    parts = Path(path).parts
    current = parts[0]
    for part in (*parts[1:], None):
        try:
            st = lstat(current)
        except OSError as err:
            raise PathRefused(f"the gateway's socket path can't be checked at {current}: "
                              f"{err.strerror}", current) from None
        if st.st_uid not in (0, uid):
            raise _refuse(current, f"it is owned by {_owner_name(st.st_uid)}", uid, st.st_uid)
        if not stat.S_ISLNK(st.st_mode) and st.st_mode & 0o022:
            kind = "group" if st.st_mode & 0o020 else "other users"
            raise _refuse(current, f"{kind} can write to it "
                          f"(mode {stat.S_IMODE(st.st_mode):o})", uid, st.st_uid)
        if part is None:
            return
        current = os.path.join(current, part)


def check_socket_path(path: str, *, uid: int | None = None, platform: str | None = None,
                      lstat: Callable[[str], os.stat_result] = os.lstat,
                      realpath: Callable[[str], str] = os.path.realpath) -> None:
    """Raise `PathRefused` unless no other user can ever create a file at `path`.

    Every directory from `/` to the socket's own is owned by root or this user and is
    neither group- nor other-writable; a group-writable directory is refused even when its
    group is this user's private one, since its other members can't be listed. A symbolic
    link on the way must be owned by root or this user too, and the directories it leads
    to are checked the same way. A path longer than the system's socket limit is refused.
    """
    uid = os.getuid() if uid is None else uid
    size = len(os.fsencode(path))
    limit = socket_limit(platform)
    if size > limit:
        raise PathRefused(f"the gateway's socket path {path} is {size} bytes, past this "
                          f"system's limit of {limit}; a shorter home or XDG_RUNTIME_DIR "
                          "fixes it")
    directory = os.path.dirname(path) or "."
    _walk(os.path.abspath(directory), uid, lstat)
    resolved = realpath(directory)
    if resolved != os.path.abspath(directory):
        _walk(resolved, uid, lstat)


# --- the hold file --------------------------------------------------------------------


def write_hold(runtime_dir: str | os.PathLike, port: int, *, pid: int | None = None) -> None:
    """Write `gateway.hold`, naming this process and the TCP port it holds (0600)."""
    body = json.dumps({"pid": os.getpid() if pid is None else pid, "port": port})
    files.atomic_write(Path(runtime_dir) / HOLD_FILE, body + "\n")


def remove_hold(runtime_dir: str | os.PathLike, *, pid: int | None = None) -> None:
    """Remove `gateway.hold` when it names this process (or `pid`)."""
    path = Path(runtime_dir) / HOLD_FILE
    found = _read_hold(path)
    if found is not None and found[0] == (os.getpid() if pid is None else pid):
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def _read_hold(path: Path) -> tuple[int, int] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(value, dict):
        return None
    pid, port = value.get("pid"), value.get("port")
    if (not isinstance(pid, int) or isinstance(pid, bool) or not isinstance(port, int)
            or isinstance(port, bool)):
        return None
    return pid, port


def hold_port(runtime_dir: str | os.PathLike, *,
              running: Callable[[int], bool] | None = None) -> int | None:
    """The port `gateway.hold` names when its pid is a running process, else None."""
    found = _read_hold(Path(runtime_dir) / HOLD_FILE)
    if found is None:
        return None
    if running is None:
        import psutil

        running = psutil.pid_exists
    return found[1] if running(found[0]) else None

"""The shell's browser listener: TCP `127.0.0.1:<port>` and `[::1]:<port>`.

A shell a socket started takes the sockets the system holds for it: systemd's, passed
as `LISTEN_FDS` (one socket unit per address, each named in `LISTEN_FDNAMES`), or
launchd's, taken by key (`Listeners`, `Listeners-<n>`) with `launch_activate_socket`.
Their Host names come from loopback's `browser_names`. A shell started any other way
binds both addresses itself, through loopback's `bind_browser`. Either way `localhost`
is admitted only while the shell holds `[::1]` on its port or the system has no IPv6
loopback. It never scans for a free port: bookmarks, SSH forwards and the session all
rest on one stable URL.
"""

from __future__ import annotations

import errno
import os
import socket
import sys
from collections.abc import Callable, MutableMapping
from dataclasses import dataclass

from moxi_loopback.api import NotLoopback, bind_browser, browser_names

__all__ = ["LISTENERS", "Listeners", "NotLoopback", "PortInUse", "bind", "take"]

# A bind refused because the port is taken: EADDRINUSE, and on Windows, where the
# listener is exclusive, WSAEADDRINUSE or WSAEACCES.
_IN_USE = frozenset(code for code in (
    errno.EADDRINUSE, getattr(errno, "WSAEADDRINUSE", None), getattr(errno, "WSAEACCES", None),
) if code is not None)

#: systemd's first passed descriptor (`SD_LISTEN_FDS_START`).
_FIRST_FD = 3
_SYSTEMD_VARIABLES = ("LISTEN_PID", "LISTEN_FDS", "LISTEN_FDNAMES")
#: The base of the launchd `Sockets` keys: `Listeners` for the first address, then
#: `Listeners-<n>` for the n-th after it.
LISTENERS = "Listeners"
#: The most launchd keys looked for, `Listeners` included.
_MOST_KEYS = 8

#: `activate(key)` gives launchd's answer for one `Sockets` key: 0 and its descriptors,
#: or an errno and none.
Activate = Callable[[str], tuple[int, list[int]]]


class PortInUse(OSError):
    """Another program holds the shell's port on `127.0.0.1`."""

    def __init__(self, port: int):
        self.port = port
        super().__init__(f"port {port} is in use by another program; "
                         "choose another with `moxi config set ui port N`")


@dataclass(frozen=True)
class Listeners:
    """The shell's listening sockets, their port, the Host names they admit, and whether
    they were inherited from a socket (else the shell bound them itself).

    `fd_names` are the names systemd gave the inherited sockets (`LISTEN_FDNAMES`, each
    socket unit's name by default), in the order passed; empty for launchd's sockets and
    for the shell's own.
    """

    sockets: tuple[socket.socket, ...]
    port: int
    names: frozenset[str]
    inherited: bool
    fd_names: tuple[str, ...] = ()

    def close(self) -> None:
        for sock in self.sockets:
            sock.close()


def bind(port: int) -> Listeners:
    """Bind the shell's own listener on `port`. Raises `PortInUse` when `127.0.0.1:<port>`
    is taken, or the bind's own `OSError`."""
    try:
        bound = bind_browser(port)
    except OSError as err:
        if err.errno in _IN_USE or getattr(err, "winerror", None) in (10013, 10048):
            raise PortInUse(port) from err
        raise
    return Listeners(bound.sockets, bound.port, bound.names, inherited=False)


def take(port: int | Callable[[], int], *, environ: MutableMapping[str, str] | None = None,
         activate: Activate | None = None, platform: str | None = None) -> Listeners:
    """The shell's listeners: the sockets a socket unit passed, else its own on `port`.

    `port` may be a callable, called only when the shell binds its own, so a shell on
    passed sockets never reads the port setting: a setting it can't read doesn't stop it.

    systemd's are taken when `LISTEN_PID` is this process and `LISTEN_FDS` names at
    least one; the three `LISTEN_*` variables are then removed from `environ`, so no
    process the shell starts takes them for its own. On macOS launchd's are taken by
    key; a process launchd did not start gets none. Inherited sockets that are not the
    shell's loopback listeners raise `NotLoopback`. Without any, it is `bind(port)`.
    """
    environ = os.environ if environ is None else environ
    platform = sys.platform if platform is None else platform
    passed = _from_systemd(environ)
    fd_names: tuple[str, ...] = ()
    if passed is not None:
        sockets, fd_names = passed
    elif platform == "darwin":
        sockets = _from_launchd(activate or _launch_activate_socket)
    else:
        sockets = None
    if not sockets:
        return bind(port() if callable(port) else port)
    try:
        names = browser_names(sockets)
    except NotLoopback:
        for sock in sockets:
            sock.close()
        raise
    found = sockets[0].getsockname()[1]
    return Listeners(tuple(sockets), found, names, inherited=True, fd_names=fd_names)


def _from_systemd(environ: MutableMapping[str, str]
                  ) -> tuple[list[socket.socket], tuple[str, ...]] | None:
    """The sockets systemd passed this process and their names, or None."""
    pid, count = environ.get("LISTEN_PID"), environ.get("LISTEN_FDS")
    if pid is None or count is None:
        return None
    named = environ.get("LISTEN_FDNAMES")
    for name in _SYSTEMD_VARIABLES:
        environ.pop(name, None)
    if pid != str(os.getpid()) or not count.isdigit() or int(count) < 1:
        return None
    total = int(count)
    names = tuple(named.split(":")) if named else ()
    # systemd gives one name per descriptor; any other count names none of them.
    sockets = _wrap(range(_FIRST_FD, _FIRST_FD + total))
    return sockets, names if len(names) == total else ()


def _from_launchd(activate: Activate) -> list[socket.socket] | None:
    """The sockets of this job's `Sockets` keys, or None when launchd did not start
    this process or its job has none of the keys."""
    fds: list[int] = []
    for place in range(_MOST_KEYS):
        key = LISTENERS if place == 0 else f"{LISTENERS}-{place}"
        code, found = activate(key)
        if code == errno.ESRCH:
            # Not a launchd job's process: nothing was passed.
            return _wrap(fds) if fds else None
        if code == errno.ENOENT:
            continue
        if code != 0:
            for fd in fds:
                os.close(fd)
            raise NotLoopback(f"launchd's socket {key} cannot be taken: "
                              f"{os.strerror(code)}")
        fds += found
    return _wrap(fds) if fds else None


def _wrap(fds) -> list[socket.socket]:
    """A socket object for each passed descriptor, never inherited by a child."""
    sockets: list[socket.socket] = []
    try:
        for fd in fds:
            sock = socket.socket(fileno=fd)
            sockets.append(sock)
            sock.set_inheritable(False)
    except OSError as err:
        for sock in sockets:
            sock.close()
        raise NotLoopback(f"a passed socket cannot be read: {err}") from None
    return sockets


def _launch_activate_socket(key: str) -> tuple[int, list[int]]:
    """launchd's `launch_activate_socket(key, &fds, &count)`, through ctypes."""
    import ctypes

    libc = ctypes.CDLL(None)
    call = libc.launch_activate_socket
    call.argtypes = [ctypes.c_char_p, ctypes.POINTER(ctypes.POINTER(ctypes.c_int)),
                     ctypes.POINTER(ctypes.c_size_t)]
    call.restype = ctypes.c_int
    found = ctypes.POINTER(ctypes.c_int)()
    count = ctypes.c_size_t(0)
    code = call(key.encode(), ctypes.byref(found), ctypes.byref(count))
    if code != 0:
        return code, []
    try:
        return 0, [found[i] for i in range(count.value)]
    finally:
        libc.free.argtypes = [ctypes.c_void_p]
        libc.free(ctypes.cast(found, ctypes.c_void_p))

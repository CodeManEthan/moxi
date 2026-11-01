"""The listening socket of comms' tools: the one the supervisor holds for the daemon,
else the daemon's own on `127.0.0.1:<port>`.

A daemon a socket unit started takes the socket the system holds for it: systemd's,
passed as `LISTEN_FDS` (comms declares one address, so one socket unit), or launchd's,
taken by its `Sockets` key `Listeners` with `launch_activate_socket`. The supervisor
then holds the port while the daemon restarts, so a client that calls in that gap waits
in the backlog and is never refused, and no other local user can take the port meanwhile.
An inherited socket on another port than `[mcp] port` is served all the same, and the
endpoint answers its real URL.

A daemon with no inherited socket (run by hand, an install with no units, Windows) binds
the port itself, with `SO_REUSEADDR` on POSIX, so the TIME_WAIT sockets an earlier
daemon's keep-alives left don't refuse the bind (on Linux it still refuses a port another
socket listens on), and `SO_EXCLUSIVEADDRUSE` on Windows, so no other process binds the
same address beside it.

A failed bind alone is no evidence that another process holds the port. So `take` then
connects to `127.0.0.1:<port>`: a listener that answers is another process's (this daemon
holds its socket's lock, and its predecessor closed its listener before it let the lock
go), and the answer is `port-taken`. With no listener it binds again every half second
for at most five seconds, then answers `bind-failed`. The caller decides what each answer
means for the tokens.

The same way of taking inherited sockets lives in the UI shell's `listen` module; comms
keeps its own small copy, since it must not import the UI.
"""

from __future__ import annotations

import errno
import os
import socket
import sys
import time
from collections.abc import Callable, MutableMapping, Sequence
from dataclasses import dataclass

__all__ = ["ADDRESS", "BACKLOG", "LISTENERS", "PROBE_WAIT", "RETRY", "RETRY_FOR",
           "Taken", "Unusable", "answers", "bind", "holder_uid", "inherited", "take"]

#: The one address the endpoint serves on.
ADDRESS = "127.0.0.1"
#: The listening socket's backlog, when the daemon binds its own.
BACKLOG = 128
#: Seconds between binds when the port can't be bound and nothing listens on it.
RETRY = 0.5
#: Seconds the binds are tried for, in all, before the answer is `bind-failed`.
RETRY_FOR = 5.0
#: Seconds the connect that looks for another listener may take.
PROBE_WAIT = 0.5
#: launchd's `Sockets` key for the first (and only) address comms declares.
LISTENERS = "Listeners"

#: systemd's first passed descriptor (`SD_LISTEN_FDS_START`).
_FIRST_FD = 3
_SYSTEMD_VARIABLES = ("LISTEN_PID", "LISTEN_FDS", "LISTEN_FDNAMES")
#: The kernel's tables of TCP sockets, read for the uid that holds a port.
_TCP_TABLES = ("/proc/net/tcp", "/proc/net/tcp6")
#: A listening socket's state in those tables.
_LISTEN = "0A"
#: Local addresses there that take a connection to 127.0.0.1: itself, and the wildcard
#: (IPv4, IPv6, and IPv6's mapped 127.0.0.1).
_TAKES_LOOPBACK = frozenset({"0100007F", "00000000", "0" * 32,
                             "0000000000000000FFFF00000100007F"})

#: `activate(key)` gives launchd's answer for one `Sockets` key: 0 and its descriptors,
#: or an errno and none.
Activate = Callable[[str], tuple[int, list[int]]]


class Unusable(Exception):
    """The supervisor passed a socket the endpoint can't serve on: not a TCP listener on
    `127.0.0.1`, more than one, or one launchd couldn't hand over."""


@dataclass(frozen=True)
class Taken:
    """What `take` found: a listening, non-blocking socket, or why there is none.

    `why` is None with a socket, else `port-taken` (another process listens on the
    port), `bind-failed` (the bind failed and nothing listens) or `failed` (an inherited
    socket the endpoint can't use). `inherited` says whether the supervisor holds the
    port. `detail` is a plain line for the log and the doctor; `holder_uid` the uid of
    the port's other listener, where the kernel's table names one."""

    sock: socket.socket | None
    why: str | None
    detail: str
    inherited: bool = False
    holder_uid: int | None = None


def take(port: int, *, environ: MutableMapping[str, str] | None = None,
         activate: Activate | None = None, platform: str | None = None,
         sleep: Callable[[float], None] = time.sleep,
         clock: Callable[[], float] = time.monotonic,
         retry: float = RETRY, retry_for: float = RETRY_FOR) -> Taken:
    """The endpoint's socket: the inherited one, else its own bound on `port` (0: any
    free port), else why not. Never raises for a port it can't have."""
    try:
        sock = inherited(environ=environ, activate=activate, platform=platform)
    except Unusable as e:
        return Taken(None, "failed", str(e))
    if sock is not None:
        found = sock.getsockname()[1]
        return Taken(sock, None, f"the supervisor holds {ADDRESS}:{found}", inherited=True)
    deadline = clock() + retry_for
    while True:
        try:
            sock = bind(port)
        except OSError as e:
            error = e
        else:
            found = sock.getsockname()[1]
            return Taken(sock, None, f"the daemon bound {ADDRESS}:{found} itself")
        if answers(port):
            uid = holder_uid(port)
            whose = f"uid {uid}" if uid is not None else "a process whose uid isn't known"
            return Taken(None, "port-taken",
                         f"another process ({whose}) listens on {ADDRESS}:{port}",
                         holder_uid=uid)
        left = deadline - clock()
        if left <= 0:
            return Taken(None, "bind-failed",
                         f"{ADDRESS}:{port} can't be bound and nothing listens on it: "
                         f"{error.strerror or error}")
        sleep(min(retry, left))


def bind(port: int) -> socket.socket:
    """A listening TCP socket on `127.0.0.1:port` (0: any free port), non-blocking.
    Raises OSError when the port can't be bound."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if os.name == "nt":                                    # pragma: no cover - Windows
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        else:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((ADDRESS, port))
        sock.listen(BACKLOG)
        sock.setblocking(False)
    except BaseException:
        sock.close()
        raise
    return sock


def answers(port: int, timeout: float = PROBE_WAIT) -> bool:
    """Whether a listener answers a connect to `127.0.0.1:port`. It sends nothing."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(timeout)
        try:
            return probe.connect_ex((ADDRESS, port)) == 0
        except OSError:
            return False


def holder_uid(port: int, tables: Sequence[str] = _TCP_TABLES) -> int | None:
    """The uid of a socket listening on `port` that takes connections to 127.0.0.1, from
    the kernel's TCP tables; None where they don't say (no such file, off Linux)."""
    want = f"{port:04X}"
    for table in tables:
        try:
            with open(table, encoding="ascii", errors="replace") as f:
                lines = f.read().splitlines()[1:]
        except OSError:
            continue
        for line in lines:
            fields = line.split()
            if len(fields) < 8 or fields[3] != _LISTEN:
                continue
            address, _, local_port = fields[1].partition(":")
            if local_port == want and address in _TAKES_LOOPBACK and fields[7].isdigit():
                return int(fields[7])
    return None


def inherited(*, environ: MutableMapping[str, str] | None = None,
              activate: Activate | None = None,
              platform: str | None = None) -> socket.socket | None:
    """The socket the supervisor passed this process, non-blocking, or None.

    systemd's is taken when `LISTEN_PID` is this process and `LISTEN_FDS` names one; the
    three `LISTEN_*` variables are removed from `environ` either way, so no process the
    daemon starts takes them for its own. On macOS launchd's is taken by its key; a
    process launchd did not start gets none. Raises `Unusable` for a passed socket the
    endpoint can't serve on, with every passed descriptor closed."""
    environ = os.environ if environ is None else environ
    platform = sys.platform if platform is None else platform
    fds = _from_systemd(environ)
    if fds is None and platform == "darwin":
        fds = _from_launchd(activate or _launch_activate_socket)
    if not fds:
        return None
    sockets = _wrap(fds)
    if len(sockets) != 1:
        for sock in sockets:
            sock.close()
        raise Unusable(f"the supervisor passed {len(sockets)} sockets; comms' tools "
                       "listen on one")
    (sock,) = sockets
    problem = _not_loopback_listener(sock)
    if problem is not None:
        sock.close()
        raise Unusable(f"the supervisor's socket {problem}")
    sock.setblocking(False)
    return sock


def _from_systemd(environ: MutableMapping[str, str]) -> list[int] | None:
    """The descriptors systemd passed this process, or None."""
    pid, count = environ.get("LISTEN_PID"), environ.get("LISTEN_FDS")
    if pid is None or count is None:
        return None
    for name in _SYSTEMD_VARIABLES:
        environ.pop(name, None)
    if pid != str(os.getpid()) or not count.isdigit() or int(count) < 1:
        return None
    return list(range(_FIRST_FD, _FIRST_FD + int(count)))


def _from_launchd(activate: Activate) -> list[int] | None:
    """The descriptors of this job's `Listeners` key, or None when launchd did not
    start this process or its job has no such key."""
    code, found = activate(LISTENERS)
    if code in (errno.ESRCH, errno.ENOENT):
        return None
    if code != 0:
        raise Unusable(f"launchd's socket {LISTENERS} can't be taken: {os.strerror(code)}")
    return list(found) or None


def _wrap(fds: list[int]) -> list[socket.socket]:
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
        for fd in fds[len(sockets):]:
            try:
                os.close(fd)
            except OSError:
                pass
        raise Unusable(f"a passed socket can't be read: {err}") from None
    return sockets


def _not_loopback_listener(sock: socket.socket) -> str | None:
    """Why `sock` isn't a TCP socket listening on 127.0.0.1, or None when it is."""
    if sock.family != socket.AF_INET or sock.type != socket.SOCK_STREAM:
        return "isn't an IPv4 TCP socket"
    try:
        host = sock.getsockname()[0]
        listening = (sock.getsockopt(socket.SOL_SOCKET, socket.SO_ACCEPTCONN)
                     if hasattr(socket, "SO_ACCEPTCONN") else 1)
    except OSError as e:
        return f"can't be read: {e}"
    if host != ADDRESS:
        return f"is on {host}, not {ADDRESS}"
    if not listening:
        return "isn't listening"
    return None


def _launch_activate_socket(key: str) -> tuple[int, list[int]]:  # pragma: no cover - macOS
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

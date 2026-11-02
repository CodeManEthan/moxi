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

A failed bind alone is no evidence that another process holds the port, and neither is a
listener that may be comms' own supervisor: tokens sent to comms' socket unit reach comms'
own daemon. So after a failed bind, and before any connect, `take` reads comms' socket
unit through the core's units status (`socket_unit_state`, bounded by `UNIT_READ_WAIT`; a
read that raises or takes longer reads `unknown`). When the unit reads `active`, this
daemon runs outside it, a hand run beside a stopped service: the answer is `supervised`,
and nothing connects (a connect would start the service, which then refuses on the lock).

That read is the socket row's state alone: the core's unit state names no port, so a port
the unit holds is not compared with `[mcp] port`. When `[mcp] port` changed since the unit
was written, the unit still reads `active` and the answer is `supervised`, though another
process may hold the new port. That errs the safe way (nothing is revoked) with a wrong
message, and the detail says so. An ordinary `moxi config set comms mcp.port N` gets there:
the core reads the port into the socket unit when it writes the units, and after a `set` it
writes again only the units of a daemon whose switch the key is, so the unit keeps the old
port until `moxi install comms`.

Otherwise `take` connects to `127.0.0.1:<port>`. A listener that answers is not this
daemon's (it holds its socket's lock, and its predecessor closed its listener before it
let the lock go). It is `port-taken` only when the unit read showed comms' unit not
holding the port (`inactive`, `failed`, `missing`, `off`) or the kernel's table names
another user's uid as its holder. A listener with neither shown may be comms' own unit, so
the answer is `bind-failed` with a detail that says so. That covers `nobackend` as well as
`unknown`: a process with no reach to the user manager (cron, `env -i`, a system service)
reads `nobackend` though the manager may hold comms' socket. Finding out costs one connect:
when comms' socket unit does hold the port, that connect starts the unit's service, which
then refuses on this daemon's lock. Nothing is revoked, but the user manager logs a failed
start and the answer reads `bind-failed`. Reading the kernel's listener table before
connecting would avoid the start on Linux; it isn't done. With no listener it binds
again every half second for at most five seconds, then answers `bind-failed`. The caller
decides what each answer means for the tokens.

The same way of taking inherited sockets lives in the UI shell's `listen` module; comms
keeps its own small copy, since it must not import the UI.
"""

from __future__ import annotations

import errno
import os
import socket
import sys
import threading
import time
from collections.abc import Callable, MutableMapping, Sequence
from dataclasses import dataclass

__all__ = ["ADDRESS", "BACKLOG", "LISTENERS", "NOT_HOLDING", "PROBE_WAIT", "RETRY",
           "RETRY_FOR", "Taken", "UNIT_READ_WAIT", "Unusable", "answers", "bind",
           "holder_uid", "inherited", "socket_unit_state", "take"]

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
#: Seconds the read of comms' own socket unit may take after a failed bind.
UNIT_READ_WAIT = 2.0
#: The module and daemon whose socket unit holds the port when comms is installed.
_MODULE, _DAEMON = "comms", "daemon"
#: The socket row's states that show comms' unit does not hold the port: the supervisor
#: read it not running (`inactive`, `failed`), no unit is written for it (`missing`), or
#: none is made for it here (`off`: Windows skips the socket, or the daemon is switched
#: off and its units removed). `nobackend` is not among them: the core gives it when this
#: process can't reach a user manager (on Linux, whenever `XDG_RUNTIME_DIR` is unset), so
#: no read was made, and the unit may hold the port all the same. It counts as `unknown`.
NOT_HOLDING = frozenset({"inactive", "failed", "missing", "off"})

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

    `why` is None with a socket, else `supervised` (comms' socket unit reads active and
    this daemon runs outside it), `port-taken` (a listener shown not to be comms' unit
    answers on the port), `bind-failed` (the bind failed and nothing listens, or a
    listener answers that may be comms' unit) or `failed` (an inherited socket the
    endpoint can't use). `inherited` says whether the supervisor holds the port.
    `detail` is a plain line for the log and the doctor; `holder_uid` the uid of the
    port's other listener, where the kernel's table names one; `unit` the state comms'
    socket unit read after a failed bind, None when no read was asked for."""

    sock: socket.socket | None
    why: str | None
    detail: str
    inherited: bool = False
    holder_uid: int | None = None
    unit: str | None = None


def take(port: int, *, environ: MutableMapping[str, str] | None = None,
         activate: Activate | None = None, platform: str | None = None,
         sleep: Callable[[float], None] = time.sleep,
         clock: Callable[[], float] = time.monotonic,
         retry: float = RETRY, retry_for: float = RETRY_FOR,
         unit_state: Callable[[], str] | None = None) -> Taken:
    """The endpoint's socket: the inherited one, else its own bound on `port` (0: any
    free port), else why not. Never raises for a port it can't have.

    `unit_state()` gives the state of comms' socket row (`socket_unit_state` when None),
    read once, after the first failed bind and before any connect."""
    try:
        sock = inherited(environ=environ, activate=activate, platform=platform)
    except Unusable as e:
        return Taken(None, "failed", str(e))
    if sock is not None:
        found = sock.getsockname()[1]
        return Taken(sock, None, f"the supervisor holds {ADDRESS}:{found}", inherited=True)
    deadline = clock() + retry_for
    unit = None
    while True:
        try:
            sock = bind(port)
        except OSError as e:
            error = e
        else:
            found = sock.getsockname()[1]
            return Taken(sock, None, f"the daemon bound {ADDRESS}:{found} itself")
        if unit is None:
            unit = (unit_state or socket_unit_state)()
            if unit == "active":
                return Taken(None, "supervised", _supervised(port), unit=unit)
        if answers(port):
            uid = holder_uid(port)
            whose = f"uid {uid}" if uid is not None else "a process whose uid isn't known"
            if unit in NOT_HOLDING or _another_user(uid):
                return Taken(None, "port-taken",
                             f"another process ({whose}) listens on {ADDRESS}:{port}",
                             holder_uid=uid, unit=unit)
            return Taken(None, "bind-failed",
                         f"a listener ({whose}) answers on {ADDRESS}:{port}; whether it "
                         "is comms' own socket unit couldn't be read",
                         holder_uid=uid, unit=unit)
        left = deadline - clock()
        if left <= 0:
            return Taken(None, "bind-failed",
                         f"{ADDRESS}:{port} can't be bound and nothing listens on it: "
                         f"{error.strerror or error}", unit=unit)
        sleep(min(retry, left))


def socket_unit_state(wait: float = UNIT_READ_WAIT, *, status=None) -> str:
    """The state of comms' daemon's socket row, as the core's `moxi.units.status` reads
    it (`active`, `inactive`, `failed`, `missing`, `off`, `nobackend`), or `unknown` when
    the read raises, takes longer than `wait` seconds, or gives no socket row. Only reads;
    `status` stands in for `moxi.units.status` in tests.

    The row says whether the unit is up, never which port it holds."""
    found: list[str] = []

    def read() -> None:
        try:
            read_status = status
            if read_status is None:
                from moxi import units

                read_status = units.status
            for row in read_status(_MODULE):
                if row.name == _DAEMON and row.kind == "socket":
                    found.append(row.state)
                    return
        except Exception:                                        # noqa: BLE001
            return

    reader = threading.Thread(target=read, name="comms-unit-read", daemon=True)
    reader.start()
    reader.join(wait)
    if reader.is_alive() or not found:
        return "unknown"
    return found[0]


def _supervised(port: int) -> str:
    return (f"comms' socket unit reads active, so it is taken to hold {ADDRESS}:{port}, "
            "and this daemon runs outside it (the unit's port isn't read, and moxi "
            "config set doesn't move the unit: if [mcp] port changed since the unit was "
            "written, run moxi install comms)")


def _another_user(uid: int | None) -> bool:
    """Whether `uid` is known and isn't this process's user."""
    own = getattr(os, "getuid", None)
    return uid is not None and own is not None and uid != own()


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

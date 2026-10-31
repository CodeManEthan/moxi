"""Listeners and connections: how a page server binds, and how a client reaches one.

Every listener binds loopback only. A module server listens on a Unix socket in its
private runtime directory on Linux and macOS (`bind_unix`), and on `127.0.0.1` with a
port the kernel picks on Windows and on the `--tcp` dev path (`bind_tcp`). The shell's
browser listener is `bind_browser`, and `browser_names` gives the Host names a shell that
inherited its sockets may admit.

TCP sockets take the same flags everywhere: on Windows `SO_EXCLUSIVEADDRUSE` and never
`SO_REUSEADDR`, which there lets another socket take the port; on Linux and macOS
`SO_REUSEADDR`, so a restart binds its own fixed port while the last run's connections
sit in TIME_WAIT, and never `SO_REUSEPORT`, which would let a second socket hold it.
They are made with `IPPROTO_TCP`, which asyncio must see before it turns `TCP_NODELAY`
on for the connections a listener accepts.

Every upstream connection goes through `open_connection` or `call`, and both prove the
server first: they send `GET /api/prove` with a fresh nonce and no token, and go on only
when the answer is the HMAC that the runtime file's token and server id give. Anything
else raises `NotOurServer` with nothing more sent, so no token reaches a process that
took a freed port.
"""

from __future__ import annotations

import errno
import hmac
import json
import os
import secrets
import socket
import stat
import sys
import time
from collections.abc import Iterable
from typing import Any, NamedTuple

from .check import BASE_NAMES, proof
from .routes import PROVE_PATH
from .runtime import RuntimeInfo

# Only the names `moxi_loopback.api` re-exports are public; the rest are the package's own.
__all__ = [
    "BrowserSockets",
    "NotLoopback",
    "NotOurServer",
    "bind_browser",
    "browser_names",
    "call",
    "open_connection",
]

_WINDOWS = sys.platform == "win32"

#: The longest a server has to answer the proof, inside any caller's own deadline.
PROVE_LIMIT = 1.0
#: The most bytes of an answer `call` reads.
ANSWER_MAX = 16 * 1024 * 1024

_V4 = "127.0.0.1"
_V6 = "::1"
_V4_ONLY = frozenset({_V4})
# The two errors that mean the system has no IPv6 loopback, so no one can hold [::1].
_NO_IPV6 = frozenset(code for code in (
    errno.EADDRNOTAVAIL, errno.EAFNOSUPPORT,
    getattr(errno, "WSAEADDRNOTAVAIL", None), getattr(errno, "WSAEAFNOSUPPORT", None),
) if code is not None)
# Not every Python exposes these two names on every system; the values are the systems'.
_SO_EXCLUSIVEADDRUSE = getattr(socket, "SO_EXCLUSIVEADDRUSE", ~socket.SO_REUSEADDR)
_SO_ACCEPTCONN = getattr(socket, "SO_ACCEPTCONN", 30 if sys.platform == "linux" else 2)


class NotLoopback(ValueError):
    """A socket handed to `browser_names` is not a loopback listener of the shell's."""


class NotOurServer(ConnectionError):
    """The server did not prove it holds the runtime file's token; nothing more was sent."""


class NotPrivate(PermissionError):
    """A socket's directory is not this user's alone; the server refuses to bind there."""


class BrowserSockets(NamedTuple):
    """The shell's browser listener: its listening sockets, its port and the Host names
    it may admit."""

    sockets: tuple[socket.socket, ...]
    port: int
    names: frozenset[str]


# --- binding -------------------------------------------------------------------------


def check_private_dir(path: str | os.PathLike) -> None:
    """Raise `NotPrivate` unless `path` is a directory, not a link, owned by this user,
    with no group or other permission bits (POSIX).

    A long runtime path puts a socket in a hashed directory outside the runtime
    directory, so the check runs on whichever directory the socket is in.
    """
    try:
        st = os.lstat(path)
    except OSError as err:
        raise NotPrivate(f"{path} cannot be checked: {err.strerror}") from None
    if stat.S_ISLNK(st.st_mode):
        raise NotPrivate(f"{path} is a symbolic link")
    if not stat.S_ISDIR(st.st_mode):
        raise NotPrivate(f"{path} is not a directory")
    if st.st_uid != os.getuid():
        raise NotPrivate(f"{path} is owned by uid {st.st_uid}, not by this user")
    if st.st_mode & 0o077:
        raise NotPrivate(f"{path} is open to group or others "
                         f"(mode {stat.S_IMODE(st.st_mode):o})")


def bind_unix(path: str | os.PathLike) -> socket.socket:
    """A listening Unix socket at `path`, in a directory `check_private_dir` passes.

    A socket a crashed server left at `path` is unlinked first, so the caller must hold
    the runtime directory's lock. Anything at `path` that is not a socket raises
    `FileExistsError`. Raises `NotPrivate` when the directory fails the check; the server
    then exits rather than loosen the check or fall back to TCP.
    """
    if _WINDOWS:
        raise NotImplementedError("Windows page servers listen on TCP")
    path = os.fspath(path)
    check_private_dir(os.path.dirname(path) or ".")
    _remove_stale(path)
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.bind(path)
        sock.listen()
    except BaseException:
        sock.close()
        raise
    return sock


def bind_tcp(port: int = 0) -> socket.socket:
    """A listening TCP socket on `127.0.0.1:<port>`; port 0 lets the kernel pick, and the
    bound port is read back with `getsockname()`."""
    sock = _tcp_socket(socket.AF_INET)
    try:
        sock.bind((_V4, port))
        sock.listen()
    except BaseException:
        sock.close()
        raise
    return sock


def bind_browser(port: int = 0) -> BrowserSockets:
    """The shell's browser listener: `127.0.0.1:<port>`, and `[::1]` on the same port.

    `names` admits `localhost` only when every address a browser may resolve it to is
    held here or can be held by no one: when the `[::1]` bind succeeds, or fails because
    the system has no IPv6 loopback (`EADDRNOTAVAIL`, `EAFNOSUPPORT`). Any other failure
    (the port taken on `::1`, `EACCES`, `EPERM`, anything else) gives `127.0.0.1` alone.
    If the `127.0.0.1` bind fails, its `OSError` is raised and nothing is left bound.
    """
    v4 = bind_tcp(port)
    bound = v4.getsockname()[1]
    try:
        v6 = _bind_v6(bound)
        try:
            v6.listen()
        except BaseException:
            v6.close()
            raise
    except OSError as err:
        names = BASE_NAMES if err.errno in _NO_IPV6 else _V4_ONLY
        return BrowserSockets((v4,), bound, names)
    except BaseException:
        v4.close()
        raise
    return BrowserSockets((v4, v6), bound, BASE_NAMES)


def browser_names(sockets: Iterable[socket.socket]) -> frozenset[str]:
    """The Host names a shell serving the inherited `sockets` may admit.

    Every socket must be a listening TCP socket on `127.0.0.1` or `::1`, all on one port,
    exactly one on `127.0.0.1` and at most one on `::1`; anything else raises
    `NotLoopback`. `localhost` is admitted when a `::1` socket is among them, or when a
    test bind of `[::1]:0` shows the system has no IPv6 loopback.
    """
    if isinstance(sockets, socket.socket):
        raise NotLoopback("browser_names takes a collection of sockets")
    v4 = v6 = 0
    ports = set()
    for sock in sockets:
        if not isinstance(sock, socket.socket) or sock.type != socket.SOCK_STREAM:
            raise NotLoopback("every inherited socket must be a TCP socket")
        try:
            listening = sock.getsockopt(socket.SOL_SOCKET, _SO_ACCEPTCONN)
            address = sock.getsockname()
        except OSError as err:
            raise NotLoopback(f"an inherited socket cannot be read: {err}") from None
        if not listening:
            raise NotLoopback("an inherited socket is not listening")
        if sock.family == socket.AF_INET and address[0] == _V4:
            v4 += 1
        elif sock.family == socket.AF_INET6 and address[0] == _V6:
            v6 += 1
        else:
            raise NotLoopback(f"an inherited socket is bound to {address[0]}, "
                              "not 127.0.0.1 or ::1")
        ports.add(address[1])
    if len(ports) > 1:
        raise NotLoopback("the inherited sockets are on more than one port")
    if v4 != 1:
        raise NotLoopback("there must be exactly one inherited socket on 127.0.0.1")
    if v6 > 1:
        raise NotLoopback("there may be at most one inherited socket on ::1")
    if v6 or _no_ipv6_loopback():
        return BASE_NAMES
    return _V4_ONLY


def _tcp_socket(family: int) -> socket.socket:
    sock = socket.socket(family, socket.SOCK_STREAM, socket.IPPROTO_TCP)
    try:
        if _WINDOWS:
            sock.setsockopt(socket.SOL_SOCKET, _SO_EXCLUSIVEADDRUSE, 1)
        else:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if family == socket.AF_INET6:
            sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
    except BaseException:
        sock.close()
        raise
    return sock


def _bind_v6(port: int) -> socket.socket:
    """A socket bound to `[::1]:<port>`, not listening. Tests replace it to make the bind
    fail with a chosen error."""
    sock = _tcp_socket(socket.AF_INET6)
    try:
        sock.bind((_V6, port))
    except BaseException:
        sock.close()
        raise
    return sock


def _no_ipv6_loopback() -> bool:
    try:
        _bind_v6(0).close()
    except OSError as err:
        return err.errno in _NO_IPV6
    return False


def _remove_stale(path: str) -> None:
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return
    if not stat.S_ISSOCK(st.st_mode):
        raise FileExistsError(f"{path} exists and is not a socket")
    os.unlink(path)


# --- connecting ----------------------------------------------------------------------


def _host(info: RuntimeInfo) -> str:
    # h11 refuses an HTTP/1.1 request without Host. A module server on TCP admits exactly
    # 127.0.0.1:<its port>; a Unix socket runs no Host gate.
    if info.transport == "tcp":
        return f"{_V4}:{info.port}"
    if info.transport == "unix":
        return "localhost"
    raise ValueError(f"unknown transport {info.transport!r}")


def _prove_request(conn: Any, host: str, nonce: str) -> bytes:
    import h11

    return (conn.send(h11.Request(method="GET", target=PROVE_PATH,
                                  headers=[("Host", host), ("X-Moxi-Nonce", nonce)]))
            + conn.send(h11.EndOfMessage()))


def _check_proof(info: RuntimeInfo, nonce: str, status: int,
                 headers: Iterable[tuple[bytes, bytes]]) -> None:
    if status != 204:
        raise NotOurServer(f"{PROVE_PATH} answered {status}")
    values = [value for name, value in headers if name.lower() == b"x-moxi-proof"]
    if len(values) != 1:
        raise NotOurServer(f"{PROVE_PATH} answered without one proof")
    expected = proof(info.token, info.server_id, nonce).encode("ascii")
    if not hmac.compare_digest(values[0], expected):
        raise NotOurServer("the server's proof does not match its runtime file")


async def open_connection(info: RuntimeInfo, *, timeout: float = 5.0) -> tuple[Any, Any]:
    """An asyncio `(reader, writer)` pair to the server `info` names, proved.

    It connects, sends `GET /api/prove` with a fresh nonce and no token, and returns once
    the server's proof matches `info`; the caller then sends its own requests, the token
    included. A failed proof (no answer within a second, another status, a missing or
    wrong proof, a closed connection) closes the connection and raises `NotOurServer`.
    `timeout` bounds the connect and the proof together; past it the connection is
    closed and `TimeoutError` raised. A failed connect raises its `OSError`.

    A proved server serves the next request on the connection if it arrives within half a
    second, even once its stop is decided. A caller that relies on that sends its first
    request within 0.25 s of the return, or opens a new connection.
    """
    import asyncio

    import h11

    deadline = time.monotonic() + timeout
    host = _host(info)
    async with asyncio.timeout(timeout):
        if info.transport == "unix":
            reader, writer = await asyncio.open_unix_connection(info.address)
        else:
            reader, writer = await asyncio.open_connection(info.address, info.port)
    try:
        conn = h11.Connection(h11.CLIENT)
        nonce = secrets.token_urlsafe(32)
        limit = min(PROVE_LIMIT, deadline - time.monotonic())
        try:
            async with asyncio.timeout(max(limit, 0)):
                writer.write(_prove_request(conn, host, nonce))
                await writer.drain()
                # A proof answer has no body, so its head is the whole answer.
                head = await reader.readuntil(b"\r\n\r\n")
        except TimeoutError:
            if time.monotonic() >= deadline:
                raise
            raise NotOurServer(f"no answer to {PROVE_PATH} within {PROVE_LIMIT} s") from None
        except (OSError, asyncio.IncompleteReadError, asyncio.LimitOverrunError) as err:
            raise NotOurServer(f"{PROVE_PATH} failed: {err!r}") from None
        try:
            conn.receive_data(head)
            event = conn.next_event()
            if not isinstance(event, h11.Response):
                raise NotOurServer(f"{PROVE_PATH} gave no answer")
            _check_proof(info, nonce, event.status_code, event.headers)
            end = conn.next_event()
        except h11.ProtocolError as err:
            raise NotOurServer(f"{PROVE_PATH} answered badly: {err}") from None
        if not isinstance(end, h11.EndOfMessage) or conn.their_state is not h11.DONE:
            raise NotOurServer(f"the server closes the connection after {PROVE_PATH}")
        return reader, writer
    except BaseException:
        writer.close()
        raise


def call(info: RuntimeInfo, method: str, path: str, body: Any = None, *,
         timeout: float = 5.0) -> tuple[int, Any]:
    """One JSON request to the server `info` names: `(status, data)`.

    It connects, proves the server as `open_connection` does, and only then sends the
    request with the runtime file's token in `X-Moxi-Token`. `body`, when given, is sent
    as JSON; `data` is the answer's JSON, or None for an empty answer, and an answer that
    is not JSON raises `ValueError`. `timeout` is one deadline for the connect, the proof
    and the answer together, with the proof's own one-second limit inside it; past it the
    connection is closed and `TimeoutError` raised. A failed proof raises `NotOurServer`
    with nothing more sent, and a failed connect its `OSError`.
    """
    import h11

    deadline = time.monotonic() + timeout
    host = _host(info)
    payload = None
    if body is not None:
        payload = json.dumps(body, separators=(",", ":"), allow_nan=False).encode("utf-8")
    sock = _connect(info, deadline)
    try:
        conn = h11.Connection(h11.CLIENT)
        nonce = secrets.token_urlsafe(32)
        proof_deadline = min(deadline, time.monotonic() + PROVE_LIMIT)
        try:
            _send(sock, _prove_request(conn, host, nonce), proof_deadline)
            status, headers, _ = _receive(sock, conn, proof_deadline)
        except TimeoutError:
            if time.monotonic() >= deadline:
                raise
            raise NotOurServer(f"no answer to {PROVE_PATH} within {PROVE_LIMIT} s") from None
        except (OSError, h11.ProtocolError) as err:
            raise NotOurServer(f"{PROVE_PATH} failed: {err!r}") from None
        _check_proof(info, nonce, status, headers)
        if conn.our_state is not h11.DONE or conn.their_state is not h11.DONE:
            raise NotOurServer(f"the server closes the connection after {PROVE_PATH}")
        conn.start_next_cycle()
        fields = [("Host", host), ("X-Moxi-Token", info.token), ("Connection", "close")]
        if payload is not None:
            fields.append(("Content-Type", "application/json"))
        if payload is not None or method.upper() == "POST":
            fields.append(("Content-Length", str(len(payload or b""))))
        message = conn.send(h11.Request(method=method, target=path, headers=fields))
        if payload:
            message += conn.send(h11.Data(data=payload))
        message += conn.send(h11.EndOfMessage())
        _send(sock, message, deadline)
        try:
            status, _, data = _receive(sock, conn, deadline)
        except h11.ProtocolError as err:
            raise ConnectionError(f"the server answered badly: {err}") from None
    finally:
        sock.close()
    if not data:
        return status, None
    try:
        return status, json.loads(data)
    except (ValueError, RecursionError):
        raise ValueError(f"the answer to {method} {path} is not JSON") from None


def _remaining(deadline: float) -> float:
    left = deadline - time.monotonic()
    if left <= 0:
        raise TimeoutError("the deadline passed")
    return left


def _connect(info: RuntimeInfo, deadline: float) -> socket.socket:
    if info.transport == "unix":
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.settimeout(_remaining(deadline))
            sock.connect(info.address)
        except BaseException:
            sock.close()
            raise
        return sock
    return socket.create_connection((info.address, info.port), timeout=_remaining(deadline))


def _send(sock: socket.socket, data: bytes, deadline: float) -> None:
    view = memoryview(data)
    while view:
        sock.settimeout(_remaining(deadline))
        view = view[sock.send(view):]


def _receive(sock: socket.socket, conn: Any, deadline: float
             ) -> tuple[int, list[tuple[bytes, bytes]], bytes]:
    """Read one answer: its status, its headers and its body, under `deadline`."""
    import h11

    response = None
    chunks: list[bytes] = []
    size = 0
    while True:
        event = conn.next_event()
        if event is h11.NEED_DATA:
            sock.settimeout(_remaining(deadline))
            conn.receive_data(sock.recv(65536))
        elif isinstance(event, h11.Response):
            response = event
        elif isinstance(event, h11.Data):
            size += len(event.data)
            if size > ANSWER_MAX:
                raise ConnectionError(f"the answer is over {ANSWER_MAX} bytes")
            chunks.append(bytes(event.data))
        elif isinstance(event, h11.EndOfMessage):
            assert response is not None
            return response.status_code, list(response.headers), b"".join(chunks)
        elif isinstance(event, h11.ConnectionClosed):
            raise ConnectionError("the server closed the connection before it answered")
        # An informational answer (1xx) is skipped.

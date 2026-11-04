"""The asyncio server of `moxi.ipc`: a Unix socket on Linux and macOS, a named pipe on
Windows.

`serve` makes the socket's directory private when it creates it or the directory is the
core's own (any other directory is checked and never changed), takes the lock beside the
socket and keeps it for the server's life, removes a socket left over from a dead server
and binds. Only a live server makes a second one refuse; a sweep's short hold is waited
out.

On Windows `serve` makes the module's runtime directory private (an access list that
grants only this user), takes the lock `<name>.lock` there, and serves the pipe on the
proactor loop's pipe server. The pipe's first instance is created with
`FILE_FLAG_FIRST_PIPE_INSTANCE`, so a pipe of that name that another process already made
refuses the server too. The lock is let go only once every instance of the pipe has
closed, so a free lock always means a free name.

Each connection gets one reader, which hands the handler one message at a time in order,
and one writer, which drains a bounded queue of frames with a deadline on each write. A
full queue or a missed deadline drops that peer, so a peer that stops reading never holds
up the loop or another peer. A peer running as another user is dropped before it is read.
"""

from __future__ import annotations

import asyncio
import collections
import inspect
import logging
import os
import socket
import stat
import struct
import sys
from collections.abc import Awaitable, Callable
from contextlib import ExitStack

from .. import files
from .. import names as _names
from .. import paths as _paths
from .. import proc as _proc
from ..modules import _Frozen
from . import _users, _wire
from ._sweep import lock_path
from ._wire import AlreadyServing, Hello, Message, Reply

_log = logging.getLogger("moxi.ipc")
_PLATFORM = sys.platform

#: How long `serve` waits for a lock another process holds, in seconds. A sweep holds it
#: for one unlink; only a live server holds it this long.
LOCK_WAIT = 1.0

# macOS: the socket level and options for a peer's credentials and pid (sys/un.h).
_SOL_LOCAL = getattr(socket, "SOL_LOCAL", 0)
_LOCAL_PEERCRED = getattr(socket, "LOCAL_PEERCRED", 0x001)
_LOCAL_PEERPID = getattr(socket, "LOCAL_PEERPID", 0x002)
# struct xucred: u_int cr_version; uid_t cr_uid; short cr_ngroups; gid_t cr_groups[16].
_XUCRED_SIZE = 76


class Peer(_Frozen):
    """The process at the other end of a connection: its pid, its user id and its
    `moxi.proc.ProcId` (pid and start time), each None when it can't be read. Windows
    has no user ids, so `uid` is None there; the peer's user is checked all the same."""

    __slots__ = _fields = ("pid", "uid", "proc")


def _credentials(sock: socket.socket) -> tuple[int | None, int | None]:
    """The peer's pid and uid, as the kernel reports them for a Unix socket."""
    if _PLATFORM.startswith("linux"):
        raw = sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("iII"))
        pid, uid, _gid = struct.unpack("iII", raw)
        return (pid or None), uid
    if _PLATFORM == "darwin":
        raw = sock.getsockopt(_SOL_LOCAL, _LOCAL_PEERCRED, _XUCRED_SIZE)
        _version, uid = struct.unpack_from("=II", raw)
        pid = sock.getsockopt(_SOL_LOCAL, _LOCAL_PEERPID)
        return (pid or None), uid
    return None, None


def _peer(sock: socket.socket | None) -> Peer:
    try:
        pid, uid = _credentials(sock) if sock is not None else (None, None)
    except OSError:
        pid, uid = None, None
    return Peer(pid, uid, _proc.identity(pid) if pid else None)


def _pipe_peer(transport: asyncio.BaseTransport) -> tuple[Peer, str | None]:
    """The process at the client end of a pipe connection, and the name of its user
    (None when either can't be read)."""
    pid = None
    pipe = transport.get_extra_info("pipe")
    if pipe is not None:
        from .. import _win

        try:
            pid = _win.pipe_client_pid(pipe.fileno())
        except (OSError, ValueError):
            pid = None
    peer = Peer(pid, None, _proc.identity(pid) if pid else None)
    return peer, _users.user_of(pid) if pid else None


Handler = Callable[["Connection", str, object], Awaitable[object]]


class Connection:
    """One client's connection to a server.

    `peer` is the process at the other end and `agreed` what its hello settled (None
    before the hello, or when the server takes none). `notify` pushes a notification
    from the loop; `notify_threadsafe` from any other thread. `close` ends the
    connection once the frames already queued are written.
    """

    def __init__(self, server: Server, reader: asyncio.StreamReader,
                 writer: asyncio.StreamWriter, peer: Peer):
        self.peer = peer
        self._server = server
        self._reader = reader
        self._writer = writer
        self._loop = server._loop
        self._inbound = _wire.Inbound(server._hello)
        self._frames: collections.deque[bytes] = collections.deque()
        self._wake = asyncio.Event()
        self._ending = False
        self._dropped = False
        self._done = asyncio.Event()
        self._task: asyncio.Task | None = None

    @property
    def agreed(self) -> _wire.Agreed | None:
        return self._inbound.agreed

    @property
    def closed(self) -> bool:
        """True once the connection is closing or closed, or its peer was dropped."""
        return self._ending

    def __repr__(self) -> str:
        return f"<moxi.ipc.Connection peer pid {self.peer.pid}>"

    # -- pushing notifications ----------------------------------------------------------

    def notify(self, method: str, params: object = None) -> bool:
        """Queue notification `method` for the peer, without waiting. False when the
        connection is closed or the peer was dropped; a full queue drops the peer and
        gives False too. Call it on the loop's thread only."""
        return self._put(_wire.encode(_wire.notification(method, params)))

    def notify_threadsafe(self, method: str, params: object = None) -> None:
        """`notify` from a thread other than the loop's, such as an executor thread.
        The frame is handed to the loop and this returns at once; once the connection
        is closed it does nothing."""
        frame = _wire.encode(_wire.notification(method, params))
        if self._ending:
            return
        try:
            self._loop.call_soon_threadsafe(self._put, frame)
        except RuntimeError:
            pass  # the loop is closed, and the connection with it

    def close(self) -> None:
        """End the connection: stop reading, write what is queued, then close."""
        self._end()

    # -- the queue and the writer -------------------------------------------------------

    def _put(self, frame: bytes) -> bool:
        if self._ending:
            return False
        if len(self._frames) >= self._server._queue:
            self._drop(f"its queue of {self._server._queue} frames is full")
            return False
        self._frames.append(frame)
        self._wake.set()
        return True

    def _end(self) -> None:
        self._ending = True
        self._wake.set()

    def _drop(self, why: str) -> None:
        if self._dropped:
            return
        self._dropped = True
        _log.warning("dropped the peer of pid %s: %s", self.peer.pid, why)
        self._frames.clear()
        self._end()
        self._writer.transport.abort()

    async def _write(self) -> None:
        deadline = self._server._write_deadline
        try:
            while True:
                while not self._frames:
                    if self._ending:
                        return
                    self._wake.clear()
                    await self._wake.wait()
                if self._dropped or self._writer.is_closing():
                    return
                self._writer.write(self._frames.popleft())
                try:
                    await asyncio.wait_for(self._writer.drain(), deadline)
                except TimeoutError:
                    self._drop(f"a write took longer than {deadline} s")
                    return
        except (ConnectionError, OSError) as exc:
            self._dropped = True
            self._end()
            _log.debug("the peer of pid %s went away: %s", self.peer.pid, exc)
        finally:
            # Closing the transport also ends the reader's wait for a line.
            if self._dropped:
                self._writer.transport.abort()
            else:
                self._writer.close()

    # -- the reader ---------------------------------------------------------------------

    async def _read(self) -> None:
        server = self._server
        while not self._ending:
            try:
                line = await self._reader.readuntil(b"\n")
            except asyncio.LimitOverrunError:
                self._put(_wire.too_long(server._max_line))
                return
            except (asyncio.IncompleteReadError, ConnectionError, OSError):
                return
            if self._ending:
                return
            step = self._inbound.receive(line[:-1])
            if step is None:
                continue
            if isinstance(step, Reply):
                if server._legacy is not None and _old_client(step, line[:-1]):
                    await self._legacy(line[:-1], step)
                    return
                if step.frame is not None:
                    self._put(step.frame)
                if step.close:
                    return
                continue
            await self._handle(step)

    async def _handle(self, message: Message) -> None:
        try:
            value = self._server._handler(self, message.method, message.params)
            if inspect.isawaitable(value):
                value = await value
        except Exception as exc:
            if not isinstance(exc, _wire.RpcError):
                _log.error("the handler failed on %s", message.method, exc_info=True)
            frame = self._inbound.fail(message, exc)
        else:
            frame = self._inbound.answer(message, value)
        if frame is not None:
            self._put(frame)

    async def _legacy(self, line: bytes, step: Reply) -> None:
        try:
            out = self._server._legacy(line)
            if inspect.isawaitable(out):
                out = await out
        except Exception:
            _log.error("the legacy hook failed", exc_info=True)
            out = None
        if out is None:
            out = step.frame
        elif not isinstance(out, (bytes, bytearray, memoryview)):
            _log.error("the legacy hook returned %s, not bytes or None",
                       type(out).__name__)
            out = step.frame
        if out:
            self._put(bytes(out))

    # -- one connection's life ----------------------------------------------------------

    async def _run(self) -> None:
        writer_task = asyncio.create_task(self._write(), name="moxi.ipc writer")
        try:
            await self._read()
        finally:
            self._end()
            try:
                await writer_task
            finally:
                self._done.set()


def _old_client(step: Reply, line: bytes) -> bool:
    """Whether a refused line is an old client's first message: not JSON, not an
    object, or an object whose "jsonrpc" is not "2.0"."""
    if not step.first:
        return False
    if step.kind in ("json", "value", "batch"):
        return True
    if step.kind == "request":
        return _wire._loads(line).get("jsonrpc") != "2.0"
    return False


class Server:
    """A running `moxi.ipc` server. `close()` stops it: no new connections, each open
    one ended, the socket removed, then the lock let go. `async with server:` closes it
    at the end of the block; `await server.serve_forever()` serves until cancelled.
    """

    def __init__(self, address: str, handler: Handler, *, hello: Hello | None,
                 max_line: int, write_deadline: float, queue: int,
                 legacy: Callable[[bytes], object] | None,
                 on_close: Callable[[Connection], object] | None):
        self.address = address
        self._handler = handler
        self._hello = hello
        self._max_line = max_line
        self._write_deadline = write_deadline
        self._queue = queue
        self._legacy = legacy
        self._on_close = on_close
        self._loop = asyncio.get_running_loop()
        self._hold = ExitStack()
        self._listener: asyncio.Server | None = None
        self._inode: tuple[int, int] | None = None
        # Windows: the pipe servers, the pipe instances open now, and whether the lock
        # was let go.
        self._pipes: list | None = None
        self._pipes_open = 0
        self._released = asyncio.Event()
        self._connections: set[Connection] = set()
        self._tasks: set[asyncio.Task] = set()
        self._closed = asyncio.Event()

    @property
    def connections(self) -> frozenset[Connection]:
        """The connections open now."""
        return frozenset(self._connections)

    def is_serving(self) -> bool:
        return not self._closed.is_set()

    def __repr__(self) -> str:
        state = "serving" if self.is_serving() else "closed"
        return f"<moxi.ipc.Server {self.address} {state}>"

    async def _start(self) -> None:
        if _PLATFORM == "win32":
            await self._start_pipe()
            return
        await self._take_lock()
        try:
            sock = self._bind()
            try:
                self._listener = await asyncio.start_unix_server(
                    self._accept, sock=sock, limit=self._max_line)
            except BaseException:
                sock.close()
                raise
        except BaseException:
            self._unlink()
            self._hold.close()
            raise

    async def _start_pipe(self) -> None:
        """Hold the lock, then serve the pipe."""
        await self._take_lock()
        loop = self._loop

        def protocol() -> _PipeProtocol:
            reader = asyncio.StreamReader(limit=self._max_line, loop=loop)
            return _PipeProtocol(self, reader)

        try:
            self._pipes = await loop.start_serving_pipe(protocol, self.address)
        except PermissionError as exc:
            # The first instance is made with FILE_FLAG_FIRST_PIPE_INSTANCE, which is
            # refused when a pipe of this name exists: another process made it without
            # the lock.
            self._release()
            raise AlreadyServing(self.address, None) from exc
        except BaseException:
            self._release()
            raise

    def _pipe_closed(self) -> None:
        """One pipe instance's handle has closed. Once the server is closed and none is
        left, the name is free and the lock goes."""
        self._pipes_open -= 1
        if self._closed.is_set() and self._pipes_open <= 0:
            self._release()

    def _release(self) -> None:
        self._hold.close()
        self._released.set()

    async def _take_lock(self) -> None:
        """Hold the lock beside the socket. Another process's hold is waited out for
        `LOCK_WAIT` seconds, polling, so the loop is never blocked."""
        path = lock_path(self.address)
        deadline = self._loop.time() + LOCK_WAIT
        while True:
            try:
                self._hold.enter_context(files.lock(path, timeout=0))
                return
            except files.LockTimeout as exc:
                if self._loop.time() >= deadline:
                    raise AlreadyServing(self.address, exc.holder) from None
            except RuntimeError:
                raise AlreadyServing(self.address, os.getpid()) from None
            await asyncio.sleep(files.POLL)

    def _bind(self) -> socket.socket:
        """Remove a socket left at the address (the lock says its server is gone), bind
        a new one and make it 0600."""
        try:
            st = os.lstat(self.address)
        except FileNotFoundError:
            pass
        else:
            if not stat.S_ISSOCK(st.st_mode):
                raise FileExistsError(f"{self.address} exists and is not a socket")
            os.unlink(self.address)
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.bind(self.address)
            os.chmod(self.address, 0o600)
            st = os.lstat(self.address)
            self._inode = (st.st_dev, st.st_ino)
        except BaseException:
            sock.close()
            raise
        return sock

    def _unlink(self) -> None:
        """Remove the socket this server bound, and no other file."""
        if self._inode is None:
            return
        try:
            st = os.lstat(self.address)
            if (st.st_dev, st.st_ino) == self._inode:
                os.unlink(self.address)
        except FileNotFoundError:
            pass
        self._inode = None

    async def _accept(self, reader: asyncio.StreamReader,
                      writer: asyncio.StreamWriter) -> None:
        peer = _peer(writer.get_extra_info("socket"))
        if peer.uid is None or peer.uid != os.getuid():
            _log.warning("dropped a peer of uid %s (pid %s): not this user",
                         peer.uid, peer.pid)
            writer.transport.abort()
            return
        await self._serve_connection(reader, writer, peer)

    async def _accept_pipe(self, reader: asyncio.StreamReader,
                           writer: asyncio.StreamWriter) -> None:
        peer, user = _pipe_peer(writer.transport)
        if not _users.is_own(user):
            _log.warning("dropped a peer of user %s (pid %s): not this user",
                         user, peer.pid)
            writer.transport.abort()
            return
        await self._serve_connection(reader, writer, peer)

    async def _serve_connection(self, reader: asyncio.StreamReader,
                                writer: asyncio.StreamWriter, peer: Peer) -> None:
        if self._closed.is_set():
            writer.transport.abort()
            return
        conn = Connection(self, reader, writer, peer)
        self._connections.add(conn)
        task = conn._task = asyncio.current_task()
        if task is not None:
            self._tasks.add(task)
        try:
            await conn._run()
        finally:
            self._connections.discard(conn)
            self._tasks.discard(task)
            if self._on_close is not None:
                try:
                    done = self._on_close(conn)
                    if inspect.isawaitable(done):
                        await done
                except Exception:
                    _log.error("on_close failed", exc_info=True)

    def close(self) -> None:
        """Stop serving: no new connections, every open one ended (a handler still
        running is cancelled, and frames already queued are written), the socket
        removed, and then the lock let go. Closing twice does nothing."""
        if self._closed.is_set():
            return
        self._closed.set()
        if self._listener is not None:
            self._listener.close()
        for pipe in self._pipes or ():
            pipe.close()
        try:
            me = asyncio.current_task()
        except RuntimeError:
            me = None
        for conn in list(self._connections):
            conn.close()
            # A handler still running is cancelled, unless it is the one closing.
            if conn._task is not None and conn._task is not me:
                conn._task.cancel()
        self._unlink()
        if self._pipes is None or self._pipes_open <= 0:
            self._release()
        # Otherwise the last pipe instance to close lets the lock go (`_pipe_closed`).

    async def wait_closed(self) -> None:
        """Wait for `close()`, then for every connection to finish (and on Windows for
        the lock to go with the last pipe instance)."""
        await self._closed.wait()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        if self._pipes is not None:
            await self._released.wait()

    async def serve_forever(self) -> None:
        """Serve until the task is cancelled or `close()` is called, then close."""
        try:
            await self._closed.wait()
        finally:
            self.close()
        await self.wait_closed()

    async def __aenter__(self) -> Server:
        return self

    async def __aexit__(self, *exc: object) -> None:
        self.close()
        await self.wait_closed()


class _PipeProtocol(asyncio.StreamReaderProtocol):
    """`StreamReaderProtocol` for one pipe instance, as `start_server` makes for a
    socket, that counts the instance open from the moment it is accepted until its handle
    has closed."""

    def __init__(self, server: Server, reader: asyncio.StreamReader):
        super().__init__(reader, server._accept_pipe, loop=server._loop)
        self._moxi_server = server
        server._pipes_open += 1

    def connection_lost(self, exc: Exception | None) -> None:
        try:
            super().connection_lost(exc)
        finally:
            # The transport closes the handle just after this returns, in the same
            # callback; the count drops on the next one.
            self._moxi_server._loop.call_soon(self._moxi_server._pipe_closed)


def _socket_dir(directory: str) -> None:
    """Make ready the directory the socket goes in, `directory`.

    A directory that doesn't exist yet is made private (`moxi.files.private_dir`), and
    so is one of the core's own: a module's runtime directory or the short hashed
    directory `moxi.paths.socket_path` uses. Any other directory that exists is never
    changed: it must be this user's, not a symbolic link, and not writable by group or
    others, or `serve` refuses it with an `OSError` naming it.
    """
    if not os.path.lexists(directory) or _core_dir(directory):
        files.private_dir(directory)
        return
    st = os.lstat(directory)
    if stat.S_ISLNK(st.st_mode):
        raise PermissionError(f"{directory} is a symbolic link; a socket's directory "
                              f"must not be")
    if not stat.S_ISDIR(st.st_mode):
        raise NotADirectoryError(f"{directory} is not a directory")
    if st.st_uid != os.getuid():
        raise PermissionError(f"{directory} belongs to uid {st.st_uid}, not this user; a "
                              f"socket's directory must be this user's own")
    if st.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise PermissionError(f"{directory} is writable by group or others (mode "
                              f"{stat.S_IMODE(st.st_mode):o}); serve leaves it as it is, so "
                              f"make it 0700 or serve elsewhere")


def _core_dir(directory: str) -> bool:
    """Whether `directory` is one the core makes private: a module's runtime directory,
    a child of its runtime root as `moxi.paths` computes it, or the short hashed
    directory. Decided by comparing resolved paths, never by a name alone."""
    real = os.path.realpath(directory)
    short = os.path.join(_paths._SHORT_ROOT, f"moxi-{os.getuid()}")
    if real == os.path.realpath(short):
        return True
    module = os.path.basename(directory)
    if not _names.is_module_name(module):
        return False
    return real == os.path.realpath(_paths._layout(module).runtime_dir())


async def serve(address: str, handler: Handler, *, hello: Hello | None = None,
                max_line: int = _wire.MAX_LINE, write_deadline: float = 5.0,
                queue: int = 256, legacy: Callable[[bytes], object] | None = None,
                on_close: Callable[[Connection], object] | None = None) -> Server:
    """Serve JSON-RPC 2.0 at `address` (`moxi.ipc.address(module, name)`), calling
    `await handler(conn, method, params)` for each request and notification.

    The handler's result answers a request; raising `RpcError` answers with that error,
    and any other exception answers -32603 and is logged. A notification's result is
    dropped. One connection's messages reach the handler one at a time, in order.

    `hello`: the connection's first request must be `rpc.hello` (anything else is
    answered -32002 and the connection closes). The server answers with the agreed
    version and `hello.capabilities`, whatever the client offered; `conn.agreed` holds
    the version and the capabilities the client offered.
    `max_line`: the longest line read, in bytes without the newline; a longer one is
    answered -32600 and closes the connection. `write_deadline`: seconds one write may
    take before the peer is dropped. `queue`: frames a connection may have waiting to
    be written before the peer is dropped. `legacy`: called as `legacy(line)` (sync or
    async) with a connection's first message, newline stripped, when it is not
    JSON-RPC 2.0 (not JSON, not an object, or an object whose "jsonrpc" isn't "2.0");
    the bytes it returns are written and the connection closes, and None sends the
    usual refusal for that line before closing. Only the first message is judged.
    `on_close`: called as `on_close(conn)` (sync or async) once each connection has
    closed.

    The socket's directory is made private when it doesn't exist yet, or when it is a
    module's runtime directory or the short hashed directory `moxi.paths.socket_path`
    uses. Any other directory that exists is left as it is: one that isn't this user's,
    is a symbolic link, or is writable by group or others raises `OSError` naming it.
    On Windows `address` is a pipe address `moxi.ipc.address` made (any other string
    raises `ValueError`), and the module's runtime directory, where the lock is, is made
    private.

    Raises `AlreadyServing` when another server still holds the address's lock after a
    one-second wait, and on Windows also when a pipe of that name already exists.
    """
    if not callable(handler):
        raise TypeError("handler must be callable")
    if isinstance(max_line, bool) or not isinstance(max_line, int) or max_line < 1:
        raise ValueError(f"max_line is a whole number of bytes above 0, not {max_line!r}")
    if isinstance(queue, bool) or not isinstance(queue, int) or queue < 1:
        raise ValueError(f"queue is a whole number above 0, not {queue!r}")
    if not write_deadline > 0:
        raise ValueError(f"write_deadline is a number of seconds above 0, not "
                         f"{write_deadline!r}")
    if hello is not None and not isinstance(hello, Hello):
        raise TypeError(f"hello is a moxi.ipc.Hello, not {hello!r}")
    address = os.fspath(address)
    if _PLATFORM == "win32":
        from . import _pipe_parts

        module, _name = _pipe_parts(address)
        _paths._layout(module).runtime_dir(create=True)
    else:
        _socket_dir(os.path.dirname(os.path.abspath(address)))
    server = Server(address, handler, hello=hello, max_line=max_line,
                    write_deadline=write_deadline, queue=queue, legacy=legacy,
                    on_close=on_close)
    await server._start()
    return server

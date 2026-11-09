"""The blocking client of `moxi.ipc`, safe to share between threads.

One lock serialises the frames written. One reader thread reads every line the server
sends, hands each answer to the call waiting on its id and queues each notification, so
the server's answers and its pushed notifications share the one connection in any order.
A call waits on its own event; one that runs out of time raises `Timeout`, and its late
answer is dropped. A call's time limit holds while its request waits to be written too.

The connection is a Unix socket on Linux and macOS, and on Windows a named pipe opened
overlapped (`_pipe.Pipe`), which offers the client the same few socket methods.

On Windows every user shares the pipe namespace, and an address `moxi.ipc.address` makes
is easy to work out, so while a module's server is down another user can create its pipe
first. The client checks who serves a pipe at such an address before it writes anything:
the server must run as this user, the check the server makes of each client. On POSIX the
socket sits in a directory only this user can write, so nothing like it is needed there.
"""

from __future__ import annotations

import errno
import itertools
import math
import queue
import select
import socket
import sys
import threading
import time
from collections.abc import Iterable, Iterator

from . import _pipe, _wire
from ._wire import Agreed, Hello, IpcError, NotServing, Timeout

#: How much the reader thread asks of the socket at a time.
_CHUNK = 64 * 1024
#: The errors that mean nothing listens at the address: the daemon is cold.
_COLD = (errno.ENOENT, errno.ECONNREFUSED)
#: The errors that mean the server is busy (its listen backlog is full), and how often,
#: in seconds, `connect` tries a busy server again.
_BUSY = (errno.EAGAIN, errno.EWOULDBLOCK)
BUSY_RETRY = 0.05
_END = object()
_WINDOWS = sys.platform == "win32"


def connect(address: str, *, timeout_s: float = 5.0, max_line: int = _wire.MAX_LINE) -> Client:
    """A connection to the server at `address`. Raises `NotServing` when nothing listens
    there and `Timeout` when no connection is made in `timeout_s` seconds. A busy server
    (its listen backlog full, or on Windows every instance of its pipe taken) is tried
    again every 50 ms until then. `timeout_s` is also the client's wait for `hello()`.
    On Windows, at an address `moxi.ipc.address` made, the pipe is closed unwritten and
    `IpcError` raised when the process serving it runs as another user or its user can't
    be read; a pipe of any other name is not checked, as no socket path is. A pipe
    whose access list doesn't admit this user (another user's server) raises `IpcError`.
    `max_line` caps the answer lines the client reads, in bytes without the newline:
    match a server given a larger `serve(max_line=...)`."""
    if isinstance(max_line, bool) or not isinstance(max_line, int) or max_line < 1:
        raise ValueError(f"max_line is a whole number of bytes above 0, not {max_line!r}")
    deadline = None if timeout_s is None else time.monotonic() + timeout_s
    while True:
        if _WINDOWS:
            conn = _open_pipe(address)
        else:
            conn = _open_socket(address, deadline, timeout_s)
        if conn is not None:
            return Client(conn, address, timeout_s=timeout_s, max_line=max_line)
        left = BUSY_RETRY if deadline is None else deadline - time.monotonic()
        if left <= 0:
            raise Timeout(f"no connection to {address} in {timeout_s} s: the server "
                          f"is busy") from None
        time.sleep(min(BUSY_RETRY, left))


def _open_socket(address: str, deadline: float | None,
                 timeout: float | None) -> socket.socket | None:
    """A socket connected to `address`, or None when the server is busy."""
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(None if deadline is None else max(deadline - time.monotonic(), 0.0))
    try:
        sock.connect(address)
    except TimeoutError:
        sock.close()
        raise Timeout(f"no connection to {address} in {timeout} s") from None
    except OSError as exc:
        sock.close()
        if exc.errno in _COLD:
            raise NotServing(address, exc.strerror) from None
        if exc.errno not in _BUSY:
            raise
        return None
    sock.settimeout(None)
    return sock


def _open_pipe(address: str) -> _pipe.Pipe | None:
    """Pipe `address` opened, or None when every instance of it is taken. An address
    outside the pipe namespace is never opened: nothing serves there."""
    if not _pipe.is_pipe(address):
        raise NotServing(address, "not a named pipe")
    try:
        pipe = _pipe.open_pipe(address)
    except OSError as exc:
        code = getattr(exc, "winerror", None)
        if code in _pipe.COLD:
            raise NotServing(address, exc.strerror) from None
        if code == _pipe.DENIED:
            raise IpcError(f"refused the pipe at {address}: its access list doesn't "
                           f"admit this user (access denied), so it is another user's "
                           f"pipe") from None
        if code != _pipe.BUSY:
            raise
        return None
    try:
        _check_server(pipe, address)
    except BaseException:
        pipe.close()
        raise
    return pipe


def _check_server(pipe: _pipe.Pipe, address: str) -> None:
    """Raise `IpcError` unless the process serving `pipe` runs as this user, when
    `address` is one `moxi.ipc.address` made. Any other pipe name is left unchecked."""
    from . import _pipe_parts, _users

    try:
        _pipe_parts(address)
    except ValueError:
        return
    try:
        pid = pipe.server_pid()
    except OSError as exc:
        why = exc.strerror or exc
        raise IpcError(f"refused the pipe at {address}: the process serving it can't be "
                       f"read ({why}), so it may not be this user's server") from None
    user = _users.user_of(pid)
    if _users.is_own(user):
        return
    if user is None:
        raise IpcError(f"refused the pipe at {address}: the user of the process serving "
                       f"it (pid {pid}) can't be read, so it may not be this user's server")
    raise IpcError(f"refused the pipe at {address}: the process serving it (pid {pid}) "
                   f"runs as {user}, not as this user, so another user may hold the name")


def _fresh(exc: BaseException) -> BaseException:
    """A new exception like `exc`: its type, arguments and attributes, with no
    traceback. A connection's one failure is raised this way by every call that meets
    it, so threads never share one instance and its traceback never grows."""
    new = type(exc).__new__(type(exc), *exc.args)
    new.__dict__.update(exc.__dict__)
    return new


def _writable(sock: socket.socket, seconds: float) -> bool:
    """Whether `sock` can take more bytes within `seconds`."""
    poller = select.poll()
    poller.register(sock.fileno(), select.POLLOUT)
    return bool(poller.poll(max(math.ceil(seconds * 1000), 1)))


def _send_socket_by(sock: socket.socket, frame: bytes, deadline: float) -> int:
    """Write `frame` to `sock` without blocking past `deadline`, waiting for the socket
    to take more between writes, and return the bytes written."""
    view = memoryview(frame)
    sent = 0
    while sent < len(frame):
        try:
            sent += sock.send(view[sent:], socket.MSG_DONTWAIT)
            continue
        except BlockingIOError:
            pass
        left = deadline - time.monotonic()
        if left <= 0 or not _writable(sock, left):
            break
    return sent


class _Call:
    __slots__ = ("done", "result", "error")

    def __init__(self) -> None:
        self.done = threading.Event()
        self.result: object = None
        self.error: BaseException | None = None


class Client:
    """A blocking, thread-safe connection to a `moxi.ipc` server, made by `connect`.

    `call` sends a request and waits for its answer; `notify` sends a notification;
    `notifications()` yields what the server pushes and is read by one thread. `hello`
    agrees a version, and `agreed` then holds it. `closed` says whether the connection
    can still carry calls. Use it as a context manager, or `close()` it.
    """

    def __init__(self, sock: socket.socket | _pipe.Pipe, address: str, *,
                 timeout_s: float = 5.0,
                 max_line: int = _wire.MAX_LINE):
        self.address = address
        self.agreed: Agreed | None = None
        self._sock = sock
        self._timeout = timeout_s
        self._lines = _wire.Lines(max_line)
        self._write_lock = threading.Lock()
        self._lock = threading.Lock()
        self._ids = itertools.count(1)
        self._calls: dict[int, _Call] = {}
        self._failure: BaseException | None = None
        self._notes: queue.SimpleQueue = queue.SimpleQueue()
        self._reader = threading.Thread(target=self._read, name="moxi.ipc reader",
                                        daemon=True)
        self._reader.start()

    def __repr__(self) -> str:
        return f"<moxi.ipc.Client {self.address}>"

    def __enter__(self) -> Client:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- what callers use ---------------------------------------------------------------

    def hello(self, versions: tuple[int, int], capabilities: Iterable[str] = ()) -> Agreed:
        """Offer `versions` (lo, hi) and `capabilities`, and return what the server
        agreed: the version, and the server's own capabilities as it answered them.
        Raises `RpcError` -32001 when no version is common, and `NotRpc` when
        the server doesn't speak JSON-RPC."""
        ours = Hello(versions, capabilities)
        answer = self.call(_wire.HELLO, _wire.hello_params(ours), timeout_s=self._timeout)
        self.agreed = _wire.agreed_from(answer, ours)
        return self.agreed

    def call(self, method: str, params: object = None, *,
             timeout_s: float | None = None) -> object:
        """Send request `method` and return its result. Raises `RpcError` when the
        server answers with an error, `Timeout` past `timeout_s` seconds (None waits with
        no limit), `NotRpc` when the server sends a line that isn't JSON-RPC, and
        `IpcError` when the connection ends first. `timeout_s` covers the whole call:
        the wait to write the request as well as the wait for its answer. A request
        the timeout cuts off partway ends the connection. On Windows so does any
        request whose write the timeout cancels, even one the server read none of."""
        deadline = None if timeout_s is None else time.monotonic() + timeout_s
        call = _Call()
        with self._lock:
            if self._failure is not None:
                raise _fresh(self._failure)
            id = next(self._ids)
            self._calls[id] = call
        try:
            if not self._send(_wire.request(id, method, params), deadline):
                raise Timeout(f"no answer to {method} in {timeout_s} s: its request could "
                              f"not be written in that time")
            left = None if deadline is None else max(deadline - time.monotonic(), 0.0)
            if not call.done.wait(left):
                raise Timeout(f"no answer to {method} in {timeout_s} s")
        finally:
            with self._lock:
                self._calls.pop(id, None)
        if call.error is not None:
            raise call.error
        return call.result

    def notify(self, method: str, params: object = None) -> None:
        """Send notification `method`; nothing comes back."""
        with self._lock:
            if self._failure is not None:
                raise _fresh(self._failure)
        self._send(_wire.notification(method, params))

    def notifications(self) -> Iterator[tuple[str, object]]:
        """The server's notifications as `(method, params)`, as they arrive, waiting
        for each. It ends when the connection does. Read it from one thread."""
        while True:
            item = self._notes.get()
            if item is _END:
                self._notes.put(_END)  # so a later reader ends too
                return
            yield item

    @property
    def closed(self) -> bool:
        """True once this client can carry no more calls, and from then on always True.

        The client keeps one failure, the first, and once it is set every `call` and
        `notify` raises it at once; `closed` is True exactly when it is set. It is set by
        `close()`; by the reader thread's end (the server closed the connection, a read
        failed, an answer line was over `max_line`, or a line was not JSON-RPC); by a
        write that failed; and by a request its call's timeout cut off partway, which
        ends the connection. A call that timed out before writing anything, or whose
        answer was only late, leaves it False. On Windows a pipe write the timeout
        cancels counts as cut off, so it ends the connection and sets this even when the
        server read none of it; only a call that timed out waiting for its turn to write
        leaves it False there.

        Safe to read from any thread with no lock: it reads one reference, set once and
        never cleared. False doesn't promise the next call succeeds: a server that has
        just gone is seen only once the reader thread reads the end of the connection.
        """
        return self._failure is not None

    def close(self) -> None:
        """Close the connection. A call still waiting raises `IpcError`."""
        self._fail(IpcError(f"the connection to {self.address} is closed"))
        try:
            self._sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        if threading.current_thread() is not self._reader:
            self._reader.join(5.0)
        self._sock.close()

    # -- the wire -----------------------------------------------------------------------

    def _send(self, message: dict, deadline: float | None = None) -> bool:
        """Write one frame, holding the write lock. With a `deadline` (a
        `time.monotonic()` time), the wait for the lock and for the socket to take the
        frame both end then, and False says the time ran out: nothing was written, or
        the frame was cut partway and the connection is ended, since the server can
        no longer tell where the next frame starts. On Windows any write the deadline
        cancels ends the connection, even when the server read none of it: the system
        reports no bytes written for a cancelled write, whatever the server read."""
        frame = _wire.encode(message)
        if deadline is None:
            self._write_lock.acquire()
        elif not self._write_lock.acquire(timeout=max(deadline - time.monotonic(), 0.0)):
            return False
        try:
            if deadline is None:
                self._sock.sendall(frame)
                return True
            return self._send_by(frame, deadline)
        except OSError as exc:
            failure = self._fail(IpcError(f"the connection to {self.address} "
                                          f"broke: {exc}"))
            raise _fresh(failure) from None
        finally:
            self._write_lock.release()

    def _send_by(self, frame: bytes, deadline: float) -> bool:
        """Write `frame` without blocking past `deadline`. The caller holds the write
        lock. False says the time ran out; a frame cut off partway ends the
        connection."""
        if isinstance(self._sock, socket.socket):
            sent = _send_socket_by(self._sock, frame, deadline)
            cut = sent > 0
        else:
            # A pipe write its deadline cancels reports no bytes written even when the
            # server has read part of it, so it counts as cut off.
            sent = self._sock.send_by(frame, deadline)
            cut = True
        if sent == len(frame):
            return True
        if cut:
            self._fail(IpcError(f"the connection to {self.address} is ended: a request "
                                f"was cut off when its call ran out of time"))
            try:
                self._sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        return False

    def _read(self) -> None:
        failure: BaseException = IpcError(
            f"the server at {self.address} closed the connection")
        try:
            while True:
                try:
                    data = self._sock.recv(_CHUNK)
                except OSError:
                    break
                if not data:
                    break
                try:
                    lines = self._lines.feed(data)
                except _wire.LineTooLong as exc:
                    for line in exc.lines:
                        self._take(line)
                    failure = IpcError(f"the server at {self.address} sent {exc}")
                    break
                for line in lines:
                    self._take(line)
        except _wire.NotRpc as exc:
            failure = exc
        finally:
            self._fail(failure)
            self._notes.put(_END)
            try:
                self._sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def _take(self, line: bytes) -> None:
        """Sort one line: an answer to its call, a notification to the queue."""
        got = _wire.read_answer(line)
        if got is None:
            return
        if isinstance(got, _wire.Note):
            self._notes.put((got.method, got.params))
            return
        if got.id is None:
            if got.error is None:
                return  # a result for no call: nothing waits on it
            # The server couldn't read a line it was sent, so it can't say whose it
            # was: every waiting call gets the error.
            with self._lock:
                calls = list(self._calls.values())
                self._calls.clear()
            for call in calls:
                call.error = _fresh(got.error)
                call.done.set()
            return
        with self._lock:
            call = self._calls.pop(got.id, None)
        if call is None:
            return  # a late answer to a call that gave up
        call.result, call.error = got.result, got.error
        call.done.set()

    def _fail(self, failure: BaseException) -> BaseException:
        """End every waiting call with `failure`, and every later one; the first
        failure stands."""
        with self._lock:
            if self._failure is None:
                self._failure = failure
            failure = self._failure
            calls = list(self._calls.values())
            self._calls.clear()
        for call in calls:
            if not call.done.is_set():
                call.error = _fresh(failure)
                call.done.set()
        return failure

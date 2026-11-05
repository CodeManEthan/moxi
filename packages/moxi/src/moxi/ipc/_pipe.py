"""Named pipes on Windows: the client's end, opened overlapped, and the server's instances,
made with an access list of their own.

The client's reader thread reads the handle while other threads write it. On a handle
opened for synchronous I/O a read in progress holds every write until it returns, so the
pipe is opened with `FILE_FLAG_OVERLAPPED` through `_winapi`, as
`multiprocessing.connection` opens its pipes, and each read and write waits on its own
event: until its deadline when it has one, and never past `close()`.

`Pipe` offers what the client uses of a socket: `recv`, `sendall`, `shutdown` and
`close`, and `send_by`, a write bounded by a deadline, with `server_pid`, the pid of the
process serving it.

`server_instance` makes one server instance of a pipe for `moxi.ipc.serve`. Windows'
default list for a pipe lets every local user open it for reading and lets clients on
other machines reach it, where a Unix socket in a private directory admits only its own
user and root. So each instance gets `access_list()`: full access for this process's user
and for SYSTEM (root's place on Windows), protected so nothing is inherited, and no one
else; and it refuses clients from other machines. There is no path that makes an
instance with the default list instead.

Nothing here runs on another system; `ctypes`, `_winapi` and `_overlapped` are imported
on first use.
"""

from __future__ import annotations

import errno
import math
import threading
import time

#: What `CreateFile` fails with when no pipe has the name: nothing serves there.
COLD = (2, 3)  # ERROR_FILE_NOT_FOUND, ERROR_PATH_NOT_FOUND
#: What it fails with when every instance of the pipe is taken: the server is busy.
BUSY = 231  # ERROR_PIPE_BUSY
#: What it fails with when the pipe's access list doesn't admit this user.
DENIED = 5  # ERROR_ACCESS_DENIED
#: What a read fails with once the server's end has closed.
_ENDED = (109, 232, 233)  # ERROR_BROKEN_PIPE, ERROR_NO_DATA, ERROR_PIPE_NOT_CONNECTED
_ERROR_IO_PENDING = 997
_ERROR_OPERATION_ABORTED = 995
_WAIT_OBJECT_0 = 0
_INFINITE = 0xFFFFFFFF
#: `CreateFile` flags that let the server learn who the client is but never act as it.
_SECURITY_SQOS_PRESENT = 0x00100000
_SECURITY_IDENTIFICATION = 0x00010000
#: The two spellings of the pipe namespace, lower-cased.
_PREFIXES = ("\\\\.\\pipe\\", "\\\\?\\pipe\\")


#: What `CreateNamedPipeW` fails with when a first instance is asked for and a pipe of
#: that name exists: another process made it.
TAKEN = DENIED
# CreateNamedPipeW's open and pipe modes, and the rest of its arguments.
_PIPE_ACCESS_DUPLEX = 0x00000003
_FILE_FLAG_OVERLAPPED = 0x40000000
_FILE_FLAG_FIRST_PIPE_INSTANCE = 0x00080000
_PIPE_TYPE_BYTE = _PIPE_READMODE_BYTE = _PIPE_WAIT = 0
_PIPE_REJECT_REMOTE_CLIENTS = 0x00000008
_PIPE_UNLIMITED_INSTANCES = 255
_BUFFER = 8192  # each way, as asyncio's own pipe server asks
_NMPWAIT_WAIT_FOREVER = 0xFFFFFFFF
#: SYSTEM's SID, the one account besides this user that a server's pipe admits.
SYSTEM = "S-1-5-18"

_create_named_pipe = None


def access_list() -> str:
    """The access list every server instance gets, in SDDL: protected (`P`, nothing
    inherited), full access (`FA`) for the user SID of this process's token and for
    SYSTEM, and no other entry. Raises `OSError` naming the call when the token can't be
    read."""
    from .. import _win

    return f"D:P(A;;FA;;;{_win.user_sid()})(A;;FA;;;{SYSTEM})"


def server_instance(address: str, sddl: str, *, first: bool) -> int:
    """A new server instance of pipe `address`, not yet connected, as a handle: duplex and
    overlapped, byte-type, refusing clients on other machines, with the access list
    `sddl` (`access_list()`). `first` asks for the pipe's first instance
    (`FILE_FLAG_FIRST_PIPE_INSTANCE`), which fails with `TAKEN` when a pipe of that name
    exists. Raises `OSError` naming the call that failed."""
    from .. import _win

    create = _bind_create()
    api = _win._bind()
    ctypes = api.ctypes
    descriptor = _win._descriptor(sddl)
    try:
        attributes = api.SECURITY_ATTRIBUTES(ctypes.sizeof(api.SECURITY_ATTRIBUTES),
                                             descriptor, False)
        handle = create(
            address,
            _PIPE_ACCESS_DUPLEX | _FILE_FLAG_OVERLAPPED
            | (_FILE_FLAG_FIRST_PIPE_INSTANCE if first else 0),
            _PIPE_TYPE_BYTE | _PIPE_READMODE_BYTE | _PIPE_WAIT | _PIPE_REJECT_REMOTE_CLIENTS,
            _PIPE_UNLIMITED_INSTANCES, _BUFFER, _BUFFER, _NMPWAIT_WAIT_FOREVER,
            ctypes.byref(attributes))
        if handle is None or handle == ctypes.c_void_p(-1).value:
            raise _win._error(f"CreateNamedPipeW for {address}")
        return handle
    finally:
        api.LocalFree(descriptor)


def _bind_create():
    global _create_named_pipe
    if _create_named_pipe is None:
        import ctypes
        from ctypes import wintypes as w

        from .. import _win

        api = _win._bind()
        fn = ctypes.WinDLL("kernel32", use_last_error=True).CreateNamedPipeW
        fn.restype = w.HANDLE
        fn.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, w.DWORD, w.DWORD, w.DWORD, w.DWORD,
                       ctypes.POINTER(api.SECURITY_ATTRIBUTES)]
        _create_named_pipe = fn
    return _create_named_pipe


def is_pipe(address: str) -> bool:
    """Whether `address` names a pipe, so opening it can never open a file."""
    return address[:len(_PREFIXES[0])].lower() in _PREFIXES


def open_pipe(address: str) -> Pipe:
    """The client's end of pipe `address`. Raises `OSError` as `CreateFile` fails: its
    `winerror` is in `COLD` when no pipe has the name and is `BUSY` when every instance is
    taken."""
    import _winapi

    handle = _winapi.CreateFile(
        address, _winapi.GENERIC_READ | _winapi.GENERIC_WRITE, 0, _winapi.NULL,
        _winapi.OPEN_EXISTING,
        _winapi.FILE_FLAG_OVERLAPPED | _SECURITY_SQOS_PRESENT | _SECURITY_IDENTIFICATION,
        _winapi.NULL)
    try:
        return Pipe(handle)
    except BaseException:
        _winapi.CloseHandle(handle)
        raise


def _ms(seconds: float) -> int:
    """`seconds` as a wait in whole milliseconds, rounded up, below `INFINITE`."""
    return min(max(math.ceil(seconds * 1000), 0), _INFINITE - 1)


class Pipe:
    """An overlapped pipe handle, read by one thread and written by others at once.

    `close()` (and `shutdown()`, the same) may come from any thread: it wakes every read
    and write in progress, which then cancel, and the handle closes once the last of them
    has finished, so no thread ever uses a handle after it closed. The read mode stays
    byte mode: a line may span the server's writes, and one write may hold many lines.
    """

    def __init__(self, handle: int):
        import _overlapped

        self._handle = handle
        self._stop = _overlapped.CreateEvent(None, True, False, None)
        self._cond = threading.Condition()
        self._busy = 0
        self._closing = False

    def __repr__(self) -> str:
        return f"<moxi.ipc pipe {self._handle}{' closed' if self._closing else ''}>"

    # -- what the client uses ---------------------------------------------------------------

    def server_pid(self) -> int:
        """The pid of the process at the server end, the one that made the pipe instance
        this end opened. Raises `OSError` when it can't be read."""
        from .. import _win

        return _win.pipe_server_pid(self._handle)

    def recv(self, size: int) -> bytes:
        """The next bytes the server sent, waiting for them; b"" once the server's end has
        closed or this pipe is closed."""
        import _winapi

        if not self._start():
            return b""
        try:
            while True:
                try:
                    ov, err = _winapi.ReadFile(self._handle, size, overlapped=True)
                    _, done = self._finish(ov, err, None)
                except OSError as exc:
                    if getattr(exc, "winerror", None) in _ENDED:
                        return b""
                    raise
                data = ov.getbuffer()
                if data or not done:
                    return bytes(data)
                # A write of no bytes on the server's end: read on.
        finally:
            self._end()

    def send_by(self, data: bytes, deadline: float | None) -> int:
        """Write `data`, waiting no later than `deadline` (a `time.monotonic()` time; None
        waits as long as it takes), and return the bytes written: fewer than all means the
        time ran out. The count is what the system reports, and a write cancelled while
        the server was reading it reports none, so a short write may still have been
        read in part. Raises `BrokenPipeError` when the pipe is closed first, and
        `OSError` when the write fails. A write cut off any other way (an interrupt)
        closes the pipe, since the server can no longer tell where the next frame
        starts."""
        import _winapi

        if not self._start():
            raise BrokenPipeError(errno.EPIPE, "the pipe is closed")
        view = memoryview(data)
        sent = 0
        try:
            while sent < len(view):
                ov, err = _winapi.WriteFile(self._handle, view[sent:], overlapped=True)
                n, done = self._finish(ov, err, deadline)
                sent += n
                if not done:
                    if self._closing:
                        raise BrokenPipeError(errno.EPIPE, "the pipe is closed")
                    break
            return sent
        except OSError:
            raise
        except BaseException:
            self.close()
            raise
        finally:
            self._end()

    def sendall(self, data: bytes) -> None:
        """Write all of `data`, waiting as long as it takes."""
        self.send_by(data, None)

    def shutdown(self, how: object = None) -> None:
        """The same as `close()`: a pipe has no half-close."""
        self.close()

    def close(self) -> None:
        """Wake every read and write in progress and close the handle once they end."""
        import _overlapped

        with self._cond:
            if self._closing:
                return
            self._closing = True
            _overlapped.SetEvent(self._stop)
            if not self._busy:
                self._free()

    # -- the handle -------------------------------------------------------------------------

    def _start(self) -> bool:
        """Count one more read or write in progress; False when the pipe is closed."""
        with self._cond:
            if self._closing:
                return False
            self._busy += 1
            return True

    def _end(self) -> None:
        with self._cond:
            self._busy -= 1
            if self._closing and not self._busy:
                self._free()

    def _free(self) -> None:
        import _winapi

        _winapi.CloseHandle(self._handle)
        _winapi.CloseHandle(self._stop)

    def _finish(self, ov, err: int, deadline: float | None) -> tuple[int, bool]:
        """Wait for overlapped operation `ov`, started with result `err`, and return the
        bytes it moved and whether it ran to its end. At `deadline`, or once the pipe is
        closed, it is cancelled, and the bytes moved before that are returned with False.
        It always completes before this returns, whatever is raised meanwhile, since the
        system writes into its buffer until it does."""
        import _winapi

        if err == _ERROR_IO_PENDING:
            try:
                wait = _INFINITE if deadline is None else _ms(deadline - time.monotonic())
                got = _winapi.WaitForMultipleObjects([ov.event, self._stop], False, wait)
            except BaseException:
                ov.cancel()
                try:
                    ov.GetOverlappedResult(True)
                except OSError:
                    pass
                raise
            if got != _WAIT_OBJECT_0:
                ov.cancel()
        n, err = ov.GetOverlappedResult(True)
        return n, err != _ERROR_OPERATION_ABORTED

"""The names under turns' wire, and the readers of what lies in turns' runtime directory.

The wire itself is `moxi.ipc`'s (JSON-RPC 2.0, one message per line); turns' methods,
values and errors are its contract, written in `contracts/socket-protocol.md`. This module
holds what a finder and an endpoint share around it:

- the endpoint's name, its caller address and its directory under version 2's own root,
  `RUNTIME/turns/v2/<harness>-<nonce>/`, which no version 1 library ever looks into;
- the reader of a directory's `hello.json`, and the sweep of a version 2 directory, which
  removes it only on `moxi.ipc.sweep`'s word, never because a connect failed;
- the reader of a version 1 directory: version 1's own hello line and its answer, so a
  finder can tell a live version 1 endpoint from a dead one without changing anything of
  version 1's;
- the line framing (`LineConn`) that version 1's socket and the hosts' internal legs still
  speak. A caller never uses it on a version 2 endpoint.
"""

from __future__ import annotations

import contextlib
import errno
import json
import os
import socket
import sys
import threading
from pathlib import Path
from typing import Any

#: The protocol version this package speaks, and the range it offers in `rpc.hello`.
VERSION = 2
VERSIONS = (2, 2)

#: The name of version 2's root under turns' runtime directory. A version 1 library
#: reads `<runtime>/v2/hello.json`, which never exists, so it never looks inside.
V2_ROOT = "v2"

#: The endpoint directory's files. The caller's socket is not among them: it lives at
#: the endpoint's address, beside the directory's root.
HELLO = "hello.json"
#: A Claude endpoint's internal socket, its runner's leg; no caller uses it.
MOD_SOCK = "mod.sock"
#: A Codex endpoint's internal sockets: its app-server's, and its hooks'.
CODEX_APP_SOCK = "codex-app.sock"
CODEX_HOOKS_SOCK = "codex-hooks.sock"
#: The internal sockets an endpoint may move to a short path (`endpoint_socket`).
INTERNAL_SOCKS = (MOD_SOCK, CODEX_APP_SOCK, CODEX_HOOKS_SOCK)
#: The temporary name `hello.json` is written under before it replaces the old one.
HELLO_TMP = ".hello.json.tmp"
#: The OpenCode host's `instructions` file.
INSTRUCTIONS = "instructions.md"
#: Every file an endpoint directory may hold that the endpoint removes at its end.
ENDPOINT_FILES = (HELLO, MOD_SOCK, HELLO_TMP, INSTRUCTIONS)

#: turns' methods. `rpc.hello` comes first on every connection and `moxi.ipc` answers it.
METHODS = ("session", "facts", "agents", "deliver", "steer", "wait", "command", "watch",
           "stops.handle", "stops.answer")
#: The one notification a stop handler's connection carries.
STOP_ASKED = "stop.asked"

#: A version 1 endpoint's socket, inside its directory.
SOCK = "sock"
#: Version 1's hello line, the one thing a version 2 finder sends a version 1 endpoint.
V1_HELLO = {"v": 1, "op": "hello", "id": 1}

#: The longest line `LineConn` reads; a longer one ends the connection.
MAX_LINE = 4 * 1024 * 1024
#: A blocking connect onto a full backlog waits forever; every connect is bounded.
CONNECT_TIMEOUT = 2.0

#: `probe`'s verdicts. Only `STALE` says nothing listens.
LIVE, STALE, UNKNOWN = "live", "stale", "unknown"
_STALE_ERRNOS = (errno.ECONNREFUSED, errno.ENOENT)

#: `sweep`'s verdicts, as `moxi.ipc.sweep` gives them.
SWEPT_LIVE, SWEPT_REMOVED, SWEPT_ABSENT = "live", "removed", "absent"


# -- names and places ---------------------------------------------------------------------

def endpoint_name(harness: str, nonce: str) -> str:
    """An endpoint's name: `<harness>-<nonce>`, keyed by its launch."""
    return f"{harness}-{nonce}"


def address(name: str) -> str:
    """The caller address of endpoint `name`: `moxi.ipc.address("turns", name)`."""
    from moxi import ipc

    return ipc.address("turns", name)


def v2_root(runtime_dir: Path | str) -> Path:
    """Version 2's root under turns' runtime directory `runtime_dir`."""
    return Path(runtime_dir) / V2_ROOT


def read_hello(directory: Path | str) -> dict[str, Any] | None:
    """The `hello.json` in `directory` as a dict, or None when it is missing or does not
    read as a JSON object (an endpoint starting, or one that is not a turns endpoint)."""
    try:
        hello = json.loads((Path(directory) / HELLO).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return hello if isinstance(hello, dict) else None


def v2_directories(runtime_dir: Path | str) -> list[Path]:
    """Every directory under version 2's root, sorted by name. A directory with no
    readable `hello.json` is an endpoint starting: the caller leaves it alone."""
    try:
        entries = list(os.scandir(v2_root(runtime_dir)))
    except OSError:
        return []
    return sorted(Path(e.path) for e in entries if _is_dir(e))


def v1_directories(runtime_dir: Path | str) -> list[Path]:
    """Every version 1 endpoint directory: a directory directly under turns' runtime
    directory, other than version 2's root, that holds a readable `hello.json`, whatever
    the file says. Sorted by name."""
    try:
        entries = list(os.scandir(runtime_dir))
    except OSError:
        return []
    return sorted(Path(e.path) for e in entries
                  if e.name != V2_ROOT and _is_dir(e) and read_hello(e.path) is not None)


def _is_dir(entry: os.DirEntry) -> bool:
    try:
        return entry.is_dir(follow_symlinks=False)
    except OSError:
        return False


def endpoint_socket(directory: Path | str, filename: str) -> str:
    """Where an endpoint's internal socket `filename` lives: in its directory, or, when
    that path is too long for a socket address (a long `MOXI_HOME` or runtime directory),
    `moxi.paths.socket_path`'s path for the name `<endpoint name>-<stem>`, which falls
    back to a short hashed path as the endpoint's own address does."""
    from moxi import paths

    directory = Path(directory)
    path = str(directory / filename)
    if len(os.fsencode(path)) < paths.SOCKET_MAX or sys.platform == "win32":
        return path
    return paths.socket_path("turns", f"{directory.name}-{Path(filename).stem}")


# -- sweeping a version 2 directory -------------------------------------------------------

def sweep(directory: Path | str, address: str) -> str:
    """Clear a version 2 endpoint's directory once its server is known to be gone.

    Call it when a connect to `address` failed. It asks `moxi.ipc.sweep(address)`, which
    decides by the server's lock and never by a probe. On `removed` or `absent` the
    directory's files go, then the directory, then the address's lock file; on `live`
    nothing is touched. The verdict is returned. Where `moxi.ipc.sweep` is not built (Windows,
    where a pipe goes with its server) this reports `live` and touches nothing.
    """
    from moxi import ipc

    try:
        verdict = ipc.sweep(address)
    except NotImplementedError:
        return SWEPT_LIVE
    if verdict == SWEPT_LIVE:
        return verdict
    remove_directory(directory)
    remove_lock(address)
    return verdict


def remove_directory(directory: Path | str) -> None:
    """Remove the files directly in an endpoint's directory, then the directory. A
    subdirectory, or a file that will not go, leaves the directory in place."""
    directory = Path(directory)
    try:
        entries = list(os.scandir(directory))
    except OSError:
        return
    for entry in entries:
        if not _is_dir(entry):
            with contextlib.suppress(OSError):
                os.unlink(entry.path)
    with contextlib.suppress(OSError):
        directory.rmdir()


def remove_lock(address: str) -> None:
    """Remove the lock file `moxi.ipc` keeps beside a dead server's socket. An
    endpoint's name is minted per launch, so no server will take it again; the lock is
    taken first, so a holder still alive keeps its file."""
    if sys.platform == "win32":
        return
    from moxi import files

    path = address + ".lock"
    if not os.path.lexists(path):
        return
    try:
        with files.lock(path, timeout=0):
            os.unlink(path)
    except (files.LockTimeout, RuntimeError, OSError):
        pass


# -- reading a version 1 directory --------------------------------------------------------

def v1_hello(directory: Path | str, timeout: float = 1.0) -> dict[str, Any] | None:
    """Ask the version 1 endpoint in `directory` for its hello, with version 1's own line
    on its `sock`. Its hello (a dict naming its session) when a version 1 answer comes
    back; None when nothing answers, or the answer is not version 1's. Nothing under the
    directory is written, renamed or removed."""
    try:
        conn = connect(Path(directory) / SOCK, timeout)
    except OSError:
        return None
    try:
        conn.send(V1_HELLO)
        answer = conn.recv(timeout)
    except (OSError, TimeoutError, ProtocolError):
        return None
    finally:
        conn.close()
    if (not isinstance(answer, dict) or answer.get("v") != 1 or answer.get("ok") is not True
            or not isinstance(answer.get("hello"), dict)):
        return None
    return answer["hello"]


# -- line framing: version 1's socket and the hosts' internal legs ------------------------

class ProtocolError(Exception):
    """A frame that is not one JSON object on one line, or a line that is too long."""


def probe(path: Path | str, timeout: float = 0.5) -> tuple[str, OSError | None]:
    """Is anything listening at the Unix socket `path`? `(LIVE | STALE | UNKNOWN, error)`.

    Only ECONNREFUSED and ENOENT are `STALE`. Anything else (a full backlog, a foreign
    owner, a path too long, a timeout) is `UNKNOWN` with its error. A host's internal leg
    uses it on its own socket; a version 2 endpoint's address is never judged by it.
    """
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect(str(path))
    except OSError as exc:
        if exc.errno in _STALE_ERRNOS:
            return STALE, None
        return UNKNOWN, exc
    else:
        return LIVE, None
    finally:
        sock.close()


def connect(path: Path | str, timeout: float = CONNECT_TIMEOUT) -> "LineConn":
    """A line connection to the Unix socket at `path`. Raises `OSError`."""
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect(str(path))
    except OSError:
        sock.close()
        raise
    sock.settimeout(None)
    return LineConn(sock)


class LineConn:
    """Newline-delimited JSON both ways, with a write lock."""

    def __init__(self, sock: socket.socket):
        self.sock = sock
        self._wlock = threading.Lock()
        self._buf = b""
        self._closed = False

    def send(self, obj: dict[str, Any]) -> None:
        data = (json.dumps(obj, separators=(",", ":")) + "\n").encode()
        with self._wlock:
            if self._closed:
                raise ProtocolError("connection closed")
            self.sock.sendall(data)

    def recv(self, timeout: float | None = None) -> dict[str, Any] | None:
        """The next frame, or None at end of stream. Raises `TimeoutError` past `timeout`
        and `ProtocolError` on a frame that does not parse."""
        try:
            self.sock.settimeout(timeout)
        except OSError:
            return None                      # closed under us: the end of the stream
        try:
            while b"\n" not in self._buf:
                try:
                    chunk = self.sock.recv(65536)
                except (TimeoutError, socket.timeout):
                    raise TimeoutError("no frame in time") from None
                except (OSError, ValueError):
                    return None
                if not chunk:
                    return None
                self._buf += chunk
                if len(self._buf) > MAX_LINE and b"\n" not in self._buf:
                    raise ProtocolError(f"a line longer than {MAX_LINE} bytes")
            line, _, self._buf = self._buf.partition(b"\n")
        finally:
            try:
                self.sock.settimeout(None)
            except OSError:
                pass
        if not line.strip():
            return self.recv(timeout)
        try:
            frame = json.loads(line)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ProtocolError(f"a frame that is not JSON: {exc}") from None
        if not isinstance(frame, dict):
            raise ProtocolError("a frame that is not a JSON object")
        return frame

    def close(self) -> None:
        with self._wlock:
            self._closed = True
        # shutdown() first: a thread blocked in recv() keeps the file open, so close()
        # alone neither wakes it nor sends the peer its end of stream.
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass

    def __enter__(self) -> "LineConn":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


__all__ = ["VERSION", "VERSIONS", "V2_ROOT", "HELLO", "MOD_SOCK", "CODEX_APP_SOCK",
           "CODEX_HOOKS_SOCK", "INTERNAL_SOCKS", "endpoint_socket", "HELLO_TMP",
           "INSTRUCTIONS", "ENDPOINT_FILES", "METHODS", "STOP_ASKED", "SOCK", "V1_HELLO",
           "MAX_LINE", "CONNECT_TIMEOUT", "LIVE", "STALE", "UNKNOWN", "SWEPT_LIVE",
           "SWEPT_REMOVED", "SWEPT_ABSENT", "endpoint_name", "address", "v2_root",
           "read_hello", "v2_directories", "v1_directories", "sweep", "remove_directory",
           "remove_lock",
           "v1_hello", "ProtocolError", "probe", "connect", "LineConn"]

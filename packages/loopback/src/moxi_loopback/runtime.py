"""The runtime file: how a page server tells the shell where it listens and what its token is.

Each page server writes one `page.json` in its module's runtime directory, format 1:

    {"format": 1, "module": "usage", "server_id": "<32 hex>", "pid": 41237,
     "transport": "unix", "address": "<socket path>", "port": null,
     "token": "<43 chars>", "version": "0.1.0", "started_at": "2026-10-30T12:00:00Z"}

Every runtime file is written by `write_runtime`, which mints a fresh token and server id
at each start, and removed by `remove_runtime`, which unlinks it only while it is still
the caller's. The caller holds `<runtime_dir>/page.lock` around both; neither takes it.

`read_runtime` is the hardened read and never raises. It refuses the file unless the
runtime directory and the file belong to this user alone, the file is a regular file of
at most 4 KiB, and its fields have format 1's shapes. It never creates a directory. A
reader on Windows opens the file with full sharing, so a read never makes a write fail.

Liveness is the health route answered with the same `server_id`, never the pid: probing a
pid with `os.kill(pid, 0)` ends the process on Windows.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import stat
import sys
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any

from moxi import files
from moxi.paths import Layout

from .check import TOKEN_PATTERN

# Only the names `moxi_loopback.api` re-exports are public; the rest are the package's own.
__all__ = [
    "RuntimeInfo",
    "read_runtime",
    "remove_runtime",
    "write_runtime",
]

_WINDOWS = sys.platform == "win32"

#: The runtime file's format.
FORMAT = 1
#: The runtime file's name in a module's runtime directory.
RUNTIME_FILE = "page.json"
#: The most bytes a runtime file may hold; a larger one is refused unread.
SIZE_MAX = 4096

_TRANSPORTS = ("unix", "tcp")
_TCP_ADDRESS = "127.0.0.1"
_SERVER_ID = re.compile(r"\A[0-9a-f]{32}\Z")
_KNOWN = frozenset({"format", "module", "server_id", "pid", "transport", "address", "port",
                    "token", "version", "started_at"})
# A sharing violation on Windows is retried this often, for this long.
_RETRY_EVERY = 0.02
_RETRY_FOR = 1.0


@dataclass(frozen=True)
class RuntimeInfo:
    """One page server's runtime file, read or just written.

    `transport` is `unix` or `tcp`. On `tcp`, `address` is `127.0.0.1` and `port` the
    bound port; on `unix`, `address` is the socket path and `port` is None. `extra` holds
    every key format 1 does not name, as it was read. The token is left out of the repr,
    so a logged value never carries it.
    """

    module: str
    server_id: str
    pid: int
    transport: str
    address: str
    port: int | None
    token: str = field(repr=False)
    version: str
    started_at: str
    extra: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))


def runtime_path(module: str) -> Path:
    """Where module `module`'s runtime file lives. Creates nothing."""
    return Layout(module).runtime_dir() / RUNTIME_FILE


def write_runtime(module: str, *, transport: str, address: str, port: int | None,
                  version: str) -> RuntimeInfo:
    """Mint a token and a server id, and write `module`'s runtime file at mode 0600.

    The token is `secrets.token_urlsafe(32)` and the server id `secrets.token_hex(16)`,
    fresh on every call; `pid` and `started_at` are this process's. The write is atomic
    (`moxi.files.atomic_write`, which on Windows retries a replace another process's open
    delays). The runtime directory must already exist: only the server that serves from
    it creates it, with `Layout(module).runtime_dir(create=True)`. Raises `ValueError`
    for an address or port that does not fit the transport.
    """
    _check_listener(transport, address, port)
    if not isinstance(version, str) or not version:
        raise ValueError("version is the page module's distribution version")
    directory = Layout(module).runtime_dir()
    if not directory.is_dir():
        raise FileNotFoundError(f"{directory} does not exist; the server creates it with "
                                "runtime_dir(create=True) before it writes its runtime file")
    info = RuntimeInfo(
        module=module,
        server_id=secrets.token_hex(16),
        pid=os.getpid(),
        transport=transport,
        address=address,
        port=port,
        token=secrets.token_urlsafe(32),
        version=version,
        started_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    data = json.dumps(_data(info), separators=(",", ":")).encode("utf-8") + b"\n"
    if len(data) > SIZE_MAX:
        raise ValueError(f"the runtime file would be over {SIZE_MAX} bytes")
    files.atomic_write(directory / RUNTIME_FILE, data, mode=0o600)
    return info


def remove_runtime(module: str, server_id: str) -> bool:
    """Unlink `module`'s runtime file if it is still the one `server_id` wrote.

    Reads the file back through `read_runtime` first, so a file that another server has
    written since, or one that cannot be read, is left alone. Returns whether it unlinked
    the file. On Windows an unlink another process's open delays is retried every 20 ms
    for up to a second, then raises.
    """
    info = read_runtime(module)
    if info is None or info.server_id != server_id:
        return False
    try:
        _unlink(runtime_path(module))
    except FileNotFoundError:
        return False
    return True


def read_runtime(module: str) -> RuntimeInfo | None:
    """`module`'s runtime file, or None when there is none or it is refused. Never raises.

    Refused: a runtime directory that is a link, not this user's, or open to group or
    others (on Windows, one whose access list grants anyone else); a file that is a link,
    not a regular file, not this user's, open to group or others, or over 4 KiB; and any
    file `parse_runtime` refuses, or one written for another module. A FIFO does not block
    the read. The directory is never created.
    """
    try:
        directory = Layout(module).runtime_dir()
        data = _read_windows(directory) if _WINDOWS else _read_posix(directory)
        if data is None:
            return None
        info = parse_runtime(data)
        if info is None or info.module != module:
            return None
        return info
    except Exception:  # noqa: BLE001 - a reader of a runtime file never raises
        return None


def parse_runtime(data: bytes | str) -> RuntimeInfo | None:
    """A runtime file's contents as a `RuntimeInfo`, or None. Never raises.

    None for anything but one JSON object holding format 1's keys with their shapes:
    `format` 1; `module` a string; `server_id` 32 lower-case hex digits; `pid` a positive
    integer; `transport` `unix` or `tcp`; on `tcp`, `address` `127.0.0.1` and `port` 1 to
    65535; on `unix`, `address` a non-empty string and `port` null; `token` 43 URL-safe
    base64 characters; `version` and `started_at` strings. Unknown keys are kept in
    `extra`.
    """
    try:
        if isinstance(data, bytes):
            data = data.decode("utf-8")
        raw = json.loads(data)
    except (ValueError, TypeError, RecursionError):
        return None
    if not isinstance(raw, dict) or not _KNOWN <= raw.keys():
        return None
    if not _is_int(raw["format"]) or raw["format"] != FORMAT:
        return None
    module, server_id, token = raw["module"], raw["server_id"], raw["token"]
    if not isinstance(module, str) or not module:
        return None
    if not isinstance(server_id, str) or not _SERVER_ID.match(server_id):
        return None
    if not isinstance(token, str) or not TOKEN_PATTERN.match(token):
        return None
    if not _is_int(raw["pid"]) or raw["pid"] <= 0:
        return None
    try:
        _check_listener(raw["transport"], raw["address"], raw["port"])
    except ValueError:
        return None
    if not isinstance(raw["version"], str) or not isinstance(raw["started_at"], str):
        return None
    extra = {key: value for key, value in raw.items() if key not in _KNOWN}
    return RuntimeInfo(
        module=module, server_id=server_id, pid=raw["pid"], transport=raw["transport"],
        address=raw["address"], port=raw["port"], token=token, version=raw["version"],
        started_at=raw["started_at"], extra=MappingProxyType(extra))


# --- helpers ------------------------------------------------------------------------


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _check_listener(transport: object, address: object, port: object) -> None:
    if transport not in _TRANSPORTS:
        raise ValueError("transport is unix or tcp")
    if transport == "tcp":
        # Every listener binds loopback only, so a TCP runtime file names nothing else.
        if address != _TCP_ADDRESS:
            raise ValueError("a tcp listener's address is 127.0.0.1")
        if not _is_int(port) or not 1 <= port <= 65535:
            raise ValueError("a tcp listener's port is 1 to 65535")
    else:
        if not isinstance(address, str) or not address or "\0" in address:
            raise ValueError("a unix listener's address is its socket path")
        if port is not None:
            raise ValueError("a unix listener has no port")


def _data(info: RuntimeInfo) -> dict[str, Any]:
    return {"format": FORMAT, "module": info.module, "server_id": info.server_id,
            "pid": info.pid, "transport": info.transport, "address": info.address,
            "port": info.port, "token": info.token, "version": info.version,
            "started_at": info.started_at}


def _unlink(path: Path) -> None:
    if not _WINDOWS:
        os.unlink(path)
        return
    # A reader that opened the file without delete sharing (a virus scanner, an indexer)
    # makes the unlink fail for a moment; it is tried again for up to a second.
    deadline = time.monotonic() + _RETRY_FOR
    while True:
        try:
            os.unlink(path)
            return
        except PermissionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(_RETRY_EVERY)


def _private(st: os.stat_result) -> bool:
    return st.st_uid == os.getuid() and not st.st_mode & 0o077


def _read_posix(directory: Path) -> bytes | None:
    # The directory is opened without following a link and the file is opened relative to
    # that descriptor, so nothing swapped in between the checks and the read is followed.
    try:
        dir_fd = os.open(directory, os.O_RDONLY | os.O_NOFOLLOW | os.O_DIRECTORY)
    except OSError:
        return None
    try:
        st = os.fstat(dir_fd)
        if not stat.S_ISDIR(st.st_mode) or not _private(st):
            return None
        try:
            fd = os.open(RUNTIME_FILE, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                         dir_fd=dir_fd)
        except OSError:
            return None
        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode) or not _private(st) or st.st_size > SIZE_MAX:
                return None
            return _read_all(lambda n: os.read(fd, n))
        finally:
            os.close(fd)
    finally:
        os.close(dir_fd)


def _read_all(read) -> bytes | None:
    chunks, size = [], 0
    while True:
        chunk = read(SIZE_MAX + 1 - size)
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)
        size += len(chunk)
        if size > SIZE_MAX:
            return None


# --- Windows -------------------------------------------------------------------------

_GENERIC_READ = 0x80000000
_FILE_SHARE_ALL = 0x1 | 0x2 | 0x4  # read, write and delete
_OPEN_EXISTING = 3
_FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
_FILE_ATTRIBUTE_DIRECTORY = 0x10
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400
_FILE_TYPE_DISK = 1


def _read_windows(directory: Path) -> bytes | None:
    if not directory.is_dir():
        return None
    # The access-list check reads the directory's list back. It is called only on a
    # directory that exists, so a reader never creates one.
    try:
        files.private_dir(directory)
    except OSError:
        return None
    handle = _win_open(directory / RUNTIME_FILE)
    if handle is None:
        return None
    try:
        return _win_read(handle)
    finally:
        _win_close(handle)


def _kernel32():
    import ctypes
    from ctypes import wintypes

    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                              ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
                              wintypes.HANDLE]
    k.CreateFileW.restype = wintypes.HANDLE
    k.GetFileType.argtypes = [wintypes.HANDLE]
    k.GetFileType.restype = wintypes.DWORD
    k.GetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
    k.GetFileInformationByHandle.restype = wintypes.BOOL
    k.ReadFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                           ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
    k.ReadFile.restype = wintypes.BOOL
    k.CloseHandle.argtypes = [wintypes.HANDLE]
    k.CloseHandle.restype = wintypes.BOOL
    return ctypes, wintypes, k


def _win_open(path: Path):
    """A read handle on `path` that shares read, write and delete, without following a
    reparse point; None when it cannot be opened."""
    ctypes, wintypes, k = _kernel32()
    handle = k.CreateFileW(str(path), _GENERIC_READ, _FILE_SHARE_ALL, None, _OPEN_EXISTING,
                           _FILE_FLAG_OPEN_REPARSE_POINT, None)
    if handle is None or handle == wintypes.HANDLE(-1).value:
        return None
    return handle


def _win_read(handle) -> bytes | None:
    ctypes, wintypes, k = _kernel32()

    class FileTime(ctypes.Structure):
        _fields_ = [("low", wintypes.DWORD), ("high", wintypes.DWORD)]

    class Info(ctypes.Structure):
        _fields_ = [("attributes", wintypes.DWORD), ("created", FileTime),
                    ("accessed", FileTime), ("written", FileTime),
                    ("volume", wintypes.DWORD), ("size_high", wintypes.DWORD),
                    ("size_low", wintypes.DWORD), ("links", wintypes.DWORD),
                    ("index_high", wintypes.DWORD), ("index_low", wintypes.DWORD)]

    if k.GetFileType(handle) != _FILE_TYPE_DISK:
        return None
    info = Info()
    if not k.GetFileInformationByHandle(handle, ctypes.byref(info)):
        return None
    if info.attributes & (_FILE_ATTRIBUTE_DIRECTORY | _FILE_ATTRIBUTE_REPARSE_POINT):
        return None
    if (info.size_high << 32 | info.size_low) > SIZE_MAX:
        return None

    def read(n: int) -> bytes:
        buffer = ctypes.create_string_buffer(n)
        got = wintypes.DWORD()
        if not k.ReadFile(handle, buffer, n, ctypes.byref(got), None):
            raise OSError(ctypes.get_last_error(), "ReadFile failed")
        return buffer.raw[:got.value]

    return _read_all(read)


def _win_close(handle) -> None:
    _, _, k = _kernel32()
    k.CloseHandle(handle)

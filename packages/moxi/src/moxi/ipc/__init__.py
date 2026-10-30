"""Talking to a module's daemon: JSON-RPC 2.0, one message per line, over a local socket.

An endpoint's address is `address(module, name)`: a Unix socket path on Linux and macOS,
a named pipe on Windows, a plain string either way. `serve(address, handler)` is the
asyncio server, run under `moxi.aio.run`; `connect(address)` is the blocking client, safe
to share between threads.

The wire: one JSON-RPC 2.0 message per line, UTF-8, ending in a newline; blank lines are
skipped and batches are refused. A line over the cap (4 MiB by default) is answered
-32600 and the connection closes; a line that isn't JSON is answered -32700 and the
connection stays open. JSON-RPC's standard codes are used as it defines them; a
handler's own exception is answered -32603 and logged, never sent. `moxi.ipc` keeps
-32001 (hello refused) and -32002 (hello required); a module takes its own error codes
from outside the reserved -32768..-32000, and owns its method and capability names.

The hello: the method `rpc.hello`. The client offers
`{"versions": [lo, hi], "capabilities": [...]}`; the server answers
`{"version": v, "capabilities": [...]}`, `v` the highest version in both ranges and
`capabilities` the server's own set whatever the client offered, or refuses with -32001
and data `{"client": [lo, hi], "server": [lo, hi]}`. The two sets are separate names, each end's
own, and nothing intersects them: each side's `Agreed.capabilities` is the other end's
set, so the client's holds the server's answer and the server's `conn.agreed` holds what
the client offered. A server given `hello=` answers any other first request -32002 and
closes the connection. `hello(offer, ours)` is that rule.

Importing this module costs little: the server and the client, and what they import, load
on first use.
"""

from __future__ import annotations

import sys

from .. import names
from ._wire import (Agreed, AlreadyServing, Hello, IpcError, NotRpc, NotServing, RpcError,
                    Timeout, hello)

_WINDOWS = sys.platform == "win32"
_PIPE_PREFIX = "\\\\.\\pipe\\moxi-"

#: The names that load with the server or the client, and the private module each is in.
_LAZY = {
    "serve": "_server",
    "Server": "_server",
    "Connection": "_server",
    "sweep": "_sweep",
    "Peer": "_server",
    "connect": "_client",
    "Client": "_client",
}


def address(module: str, name: str) -> str:
    """The address of module `module`'s endpoint `name`.

    Linux and macOS: `moxi.paths.socket_path(module, name)`, a socket in the module's
    runtime directory (or a short hashed path when that is too long). Windows:
    `\\\\.\\pipe\\moxi-<module>-<name>-<u>`, `u` the first 8 hex digits of the SHA-256
    of the user's SID, since every user on the machine shares the pipe namespace.
    """
    if not _WINDOWS:
        from .. import paths

        return paths.socket_path(module, name)
    if not names.is_module_name(module):
        raise ValueError(f"{module!r} is not a Moxi module name")
    if not name or name in (".", "..") or any(c in name for c in "/\\\0"):
        raise ValueError(f"{name!r} is not an endpoint name")
    return f"{_PIPE_PREFIX}{module}-{name}-{_user_hash()}"


def _user_hash() -> str:
    import hashlib

    from .. import _win

    return hashlib.sha256(_win.user_sid().encode()).hexdigest()[:8]


def _pipe_parts(address: str) -> tuple[str, str]:
    """The module and endpoint name of a pipe address `address(module, name)` made.
    Raises `ValueError` for any other string."""
    rest = address[len(_PIPE_PREFIX):] if address.startswith(_PIPE_PREFIX) else ""
    module, _, tail = rest.partition("-")
    name, _, user = tail.rpartition("-")
    if (not names.is_module_name(module) or not name or len(user) != 8
            or any(c not in "0123456789abcdef" for c in user)):
        raise ValueError(f"{address!r} is not a moxi.ipc pipe address")
    return module, name


def __getattr__(attr: str) -> object:
    where = _LAZY.get(attr)
    if where is None:
        raise AttributeError(f"module {__name__!r} has no attribute {attr!r}")
    import importlib

    return getattr(importlib.import_module(f"{__name__}.{where}"), attr)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_LAZY))


__all__ = ["address", "serve", "sweep", "connect", "Server", "Connection", "Client",
           "Hello", "Agreed", "hello", "Peer", "IpcError", "RpcError", "NotRpc",
           "NotServing", "AlreadyServing", "Timeout"]

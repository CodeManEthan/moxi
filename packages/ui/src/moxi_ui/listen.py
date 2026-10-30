"""The shell's browser listener: TCP `127.0.0.1:<port>` and `[::1]:<port>`.

A shell started without a socket unit binds both itself, through loopback's
`bind_browser`, which admits `localhost` only while the shell holds `[::1]` on its port
or the system has no IPv6 loopback. It never scans for a free port: bookmarks, SSH
forwards and the session all rest on one stable URL.
"""

from __future__ import annotations

import errno
import socket
from dataclasses import dataclass

from moxi_loopback.api import bind_browser

# A bind refused because the port is taken: EADDRINUSE, and on Windows, where the
# listener is exclusive, WSAEADDRINUSE or WSAEACCES.
_IN_USE = frozenset(code for code in (
    errno.EADDRINUSE, getattr(errno, "WSAEADDRINUSE", None), getattr(errno, "WSAEACCES", None),
) if code is not None)


class PortInUse(OSError):
    """Another program holds the shell's port on `127.0.0.1`."""

    def __init__(self, port: int):
        self.port = port
        super().__init__(f"port {port} is in use by another program; "
                         "choose another with `moxi config set ui port N`")


@dataclass(frozen=True)
class Listeners:
    """The shell's listening sockets, their port, the Host names they admit, and whether
    they were inherited from a socket unit (else the shell bound them itself)."""

    sockets: tuple[socket.socket, ...]
    port: int
    names: frozenset[str]
    inherited: bool

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

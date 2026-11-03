"""The provider check: on Linux, WSL included, only the provider's own process reaches the
gateway.

The check reads the peer of the connection a request arrived on, before the Host check.
On a socket endpoint that is the uid loopback's `serve(..., peer=True)` read when the
connection was made (`scope["moxi.peer"]`); on a TCP endpoint it is the uid on the client
socket's row in `/proc/net/tcp` or `tcp6`, matched on the scope's client and server
addresses. A request whose peer isn't the provider's uid, or any request while that uid is
unknown, is refused with a 403. macOS and Windows run no check: neither gives the peer of a
TCP connection the way Linux does, and on macOS which uid each provider forwards as hasn't
been read.

The provider's uid: for `tailscale`, the peer of a connection to tailscaled's local socket,
the one the `tailscale` command uses, in a directory only root writes; nothing else counts,
not a process that is merely named `tailscaled`. For `cloudflare`, this user's, since
`cloudflared` runs as this user's daemon.
"""

from __future__ import annotations

import os
import socket
import struct
import sys
from collections.abc import Callable
from typing import Any

from ..session import socket_owner

__all__ = ["PEER", "TAILSCALED_SOCKET", "checks", "peer_of", "provider_uid", "uid_on_socket"]

#: The scope key loopback's `serve(..., peer=True)` sets.
PEER = "moxi.peer"
#: tailscaled's local socket, where the `tailscale` command reaches it by default.
TAILSCALED_SOCKET = "/var/run/tailscale/tailscaled.sock"


def checks(system: str) -> bool:
    """Whether the provider check runs on `system` (see `endpoint.system`)."""
    return system in ("linux", "wsl")


def uid_on_socket(path: str, *, timeout: float = 1.0) -> int | None:
    """The uid of the process that accepted a connection to the Unix socket `path`
    (`SO_PEERCRED`), or None when it can't be reached or read."""
    peercred = getattr(socket, "SO_PEERCRED", None)
    if peercred is None:
        return None
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            sock.connect(path)
            raw = sock.getsockopt(socket.SOL_SOCKET, peercred, struct.calcsize("3i"))
    except OSError:
        return None
    _pid, uid, _gid = struct.unpack("3i", raw)
    return uid


def provider_uid(provider: str, *, on_socket: Callable[[str], int | None] = uid_on_socket,
                 getuid: Callable[[], int] | None = None) -> int | None:
    """The uid `provider`'s process runs as, or None when it can't be told."""
    if provider == "tailscale":
        return on_socket(TAILSCALED_SOCKET)
    if provider == "cloudflare":
        return (getuid or os.getuid)()
    return None


def peer_of(scope: dict[str, Any], kind: str, *, proc: str = "/proc") -> int | None:
    """The uid at the other end of the request's connection: the scope's peer on a socket
    endpoint, the `/proc/net/tcp` row of the scope's addresses on a TCP one."""
    if kind == "unix":
        peer = scope.get(PEER)
        uid = peer.get("uid") if isinstance(peer, dict) else None
        return uid if isinstance(uid, int) and not isinstance(uid, bool) else None
    if sys.platform != "linux" and proc == "/proc":
        return None
    return socket_owner(scope.get("client"), scope.get("server"), root=proc)

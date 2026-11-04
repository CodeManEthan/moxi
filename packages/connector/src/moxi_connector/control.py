"""The daemon's control socket: `moxi.ipc.address("connector", "control")`.

Newline JSON-RPC 2.0 through `moxi.ipc.serve`, with the core's `rpc.hello` required first
(versions `checks.VERSIONS`). The socket's directory is private, a peer of another user is
dropped before it is read, and each method gets the connection's `moxi.ipc.Peer` (pid,
uid and start time).

The methods:

- `status`: what `moxi connector status`, the doctor and `HEALTH` show of the running
  process: its pid and version, the origin's URL and the port row's warning, the loopback
  port, each provider entry with its state and tools, the clashing tool names, the served
  tools, the providers' own status rows, and the modules whose mint gate is loaded;
- `probe_expect`: register a fresh nonce for `moxi connector check`, answered as
  `{"nonce": ...}`;
- `probe_seen` `{"nonce": ...}`: `{"seen": bool}`, whether a request carried it;
- `mint_code` `{"label": ...}`, which the daemon passes in (`codes.mint_method`): a
  one-time sign-in code, minted only when a started provider declares a mint gate and no
  gate's `peer` check refuses this connection's peer.

A method is `async fn(peer, params) -> result`, kept by name in `Control.methods`; one
raising `moxi.ipc.RpcError` answers with that error. More methods join through the
`methods` argument (the daemon passes its own) or `Control.register`. Grants are not on
this socket: their file is their source.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Awaitable, Callable, Mapping
from typing import TYPE_CHECKING, Any

from . import checks

if TYPE_CHECKING:
    from .daemon import Daemon

log = logging.getLogger("moxi.connector.control")

#: `async fn(peer, params) -> result`.
Method = Callable[[Any, object], Awaitable[object]]

#: The JSON-RPC error a method name nobody serves answers.
METHOD_NOT_FOUND = -32601
#: The JSON-RPC error of params a method can't take.
INVALID_PARAMS = -32602


class Control:
    """The control socket of one `Daemon`."""

    def __init__(self, daemon: Daemon, methods: Mapping[str, Method] | None = None, *,
                 address: str | None = None):
        self.daemon = daemon
        self.address = address
        self.methods: dict[str, Method] = {"status": self.status,
                                           "probe_expect": self.probe_expect,
                                           "probe_seen": self.probe_seen}
        for name, fn in (methods or {}).items():
            self.register(name, fn)
        self.server = None

    def register(self, name: str, fn: Method) -> None:
        """Serve `fn` as method `name`. A name already served is refused."""
        if name in self.methods or name.startswith("rpc."):
            raise ValueError(f"the control socket already serves {name!r}")
        self.methods[name] = fn

    async def start(self) -> None:
        from moxi import ipc

        if self.address is None:
            self.address = ipc.address(checks.MODULE, checks.CONTROL)
        self.server = await ipc.serve(self.address, self._handle,
                                      hello=ipc.Hello(checks.VERSIONS))
        log.info("the control socket is %s", self.address)

    async def stop(self) -> None:
        server, self.server = self.server, None
        if server is not None:
            server.close()
            await server.wait_closed()

    async def _handle(self, conn, method: str, params: object) -> object:
        from moxi import ipc

        fn = self.methods.get(method)
        if fn is None:
            raise ipc.RpcError(METHOD_NOT_FOUND, f"the connector has no method {method!r}")
        return await fn(conn.peer, params)

    # -- the methods ---------------------------------------------------------------------

    async def status(self, peer, params: object) -> dict[str, Any]:
        d = self.daemon
        front, host = d.front, d.host
        found = front.origin if front is not None else None
        registry = host.registry
        return {
            "pid": os.getpid(),
            "version": host.version or None,
            "url": found.url if found else None,
            "port_warning": found.port_warning() if found else None,
            "public_port": found.port if found else None,
            "port": d.endpoint.port if d.endpoint is not None else None,
            "providers": [{"module": e.module, "state": e.state, "why": e.why,
                           "tools": [t.name for t in e.provider.tools]
                           if e.provider is not None else []}
                          for e in registry.entries],
            "clashes": registry.clashes(),
            "tools": [{"name": name, "provider": module}
                      for name, (module, _) in registry.tools().items()],
            "rows": [list(row) for row in registry.status_rows()],
            "gates": [module for module, _ in registry.gates()],
        }

    async def probe_expect(self, peer, params: object) -> dict[str, str]:
        return {"nonce": self.daemon.host.probes.expect()}

    async def probe_seen(self, peer, params: object) -> dict[str, bool]:
        from moxi import ipc

        nonce = params.get("nonce") if isinstance(params, dict) else None
        if not isinstance(nonce, str):
            raise ipc.RpcError(INVALID_PARAMS, "probe_seen takes {\"nonce\": <string>}")
        return {"seen": self.daemon.host.probes.seen(nonce)}


__all__ = ["Control", "INVALID_PARAMS", "METHOD_NOT_FOUND", "Method"]

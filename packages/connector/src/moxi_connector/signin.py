"""The sign-in's seam: what the door asks of the sign-in for each app it builds.

The door calls `SignIn.mount(origin)` once per app it builds for an origin, as the last
step of that app's start (`moxi_connector.app.App.start`), so an app that fails to start
never mounts and the serving app's sign-in is left as it was. A mount that raises fails
the start, and must leave the sign-in as it was too. The `Mount` it gets back holds:

- `routes`: each path the sign-in serves (the metadata documents, `/register`,
  `/authorize`, `/token`, `/revoke`), mapped to the ASGI app that answers it, every method
  included. A path not here and not `/mcp` is an empty 404;
- `guard(app)`: wraps the MCP endpoint's app for `POST /mcp`. The wrapper either answers
  the request itself (401 with `origin.challenge()` as `WWW-Authenticate`) or sets
  `scope[GRANT]` to the call's `Grant` and calls `app`.

State the sign-in makes inside `mount` lives and dies with that app, so at an origin
change pending sign-ins and unredeemed codes, checked against the old origin, go with the
old app. State that outlives an origin (minted terminal codes, the grants) belongs to the
`SignIn` object itself.

`stopping(switch_on)` runs once at the process's stop, after the door stops listening,
with the daemon switch read at that moment.

`Closed` is the sign-in until one is built: no routes, and every `POST /mcp` answered 401.
The door is never open without a sign-in.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from .origin import Origin

ASGIApp = Callable[[dict, Callable[[], Awaitable[dict]], Callable[[dict], Awaitable[None]]],
                   Awaitable[None]]

#: The scope key a guard sets to the call's `Grant` before it calls the MCP app.
GRANT = "moxi.connector.grant"


@dataclass(frozen=True)
class Grant:
    """The sign-in a call came through: its 8-hex id, its label and its client's id."""

    id: str
    label: str | None = None
    client_id: str | None = None


@dataclass(frozen=True)
class Mount:
    """What a sign-in serves under one origin."""

    routes: Mapping[str, ASGIApp] = field(default_factory=dict)
    guard: Callable[[ASGIApp], ASGIApp] | None = None


class SignIn(Protocol):
    def mount(self, origin: Origin) -> Mount: ...

    async def stopping(self, switch_on: bool) -> None: ...


async def unauthorized(origin: Origin, send) -> None:
    """Answer 401 with the challenge that points Claude at the metadata document."""
    await send({"type": "http.response.start", "status": 401,
                "headers": [(b"www-authenticate", origin.challenge().encode()),
                            (b"content-length", b"0"),
                            (b"cache-control", b"no-store")]})
    await send({"type": "http.response.body", "body": b""})


class Closed:
    """The sign-in that admits nobody: no routes, every `POST /mcp` answered 401."""

    def mount(self, origin: Origin) -> Mount:
        def guard(app: ASGIApp) -> ASGIApp:
            async def refuse(scope: dict, receive: Any, send: Any) -> None:
                await unauthorized(origin, send)
            return refuse
        return Mount(routes={}, guard=guard)

    async def stopping(self, switch_on: bool) -> None:
        return None


__all__ = ["ASGIApp", "Closed", "GRANT", "Grant", "Mount", "SignIn", "unauthorized"]

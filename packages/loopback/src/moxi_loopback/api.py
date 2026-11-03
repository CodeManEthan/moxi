"""loopback's interface 1, the module page modules, the UI shell and tests reach
(`moxi_loopback.api`).

A page is declared once, as data, in a module that imports only this one:

    # moxi_usage/page.py
    from moxi_loopback.api import Field, Out, Page, Route

    PAGE = Page(
        routes=(
            Route("/api/summary", "read", "moxi_usage.pagehandlers:summary",
                  query={"days": Field("int", required=False, max=366)},
                  returns={"total": Out("int"), "log": Out("string", mark="path")}),
            Route("/api/refresh", "act", "moxi_usage.pagehandlers:refresh",
                  body={"force": Field("bool", required=False)},
                  returns={"ok": Out("bool")}),
        ),
        assets="web",
    )

A page module whose distribution doesn't require loopback may instead reach this same
module object as `moxi.modules.load("loopback", interface=1).api`, provided it imports
nothing else outside the standard library and the core.

Handlers are named by `"pkg.mod:fn"` target strings, so the shell, the gateway and tests
read a page as data without importing handler code.

* Declaring a page: `Page`, `Route`, `Field`, `Out`; `PageError` for a refused
  declaration.
* The route table: `Table`, `Entry`, `build_table(page)`, `export(page)`.
* The request check: `check(view, profile)` over a `RequestView`, with a `Profile` from
  `upstream(...)` (a module server), `browser(...)` (the shell's listener) or
  `gateway(...)` (the listener a tunnel forwards a phone to); a refusal is a `Refusal`. `token_matches(view, token)` is gate 4's compare, and `proof(token,
  server_id, nonce)` the answer a server gives `GET /api/prove`.
* Listeners and connections: `bind_browser(port)` and `BrowserSockets`,
  `browser_names(sockets)` and `NotLoopback`; `bind_unix(path)` in a directory
  `check_private_dir(path)` passes, which raises `NotPrivate`, and `bind_tcp(port)` on
  `127.0.0.1`; `open_connection(info)` and `call(info, ...)`, which prove the server first
  and raise `NotOurServer` when it fails.
* The runtime file: `RuntimeInfo`, `write_runtime`, `remove_runtime`, `read_runtime`.
* The base: `app(page, ...)`, `guard(inner, profile)`, `prove(inner, ...)` and
  `serve(app, sockets, ...)`, which with `signals=False` leaves the process's signal
  handling alone and with `peer=True` gives each request its connection's peer uid as
  the scope key `"moxi.peer"`. Handlers get a `Call` and may return `Accepted(...)` or
  raise `Conflict(...)` or `Refused(...)`; `busy(name)` holds the idle exit; `Topics` and
  `Snapshot` feed the page's one stream route. A confirmed act (`Route(...,
  confirmed=True, status=...)`) needs an `Idempotency-Key`, runs at most once per key,
  gets the key as `call.key` and returns `Accepted(...)`.
* The fixed header set: `response_headers(...)` for a response of the caller's own, with
  `policy="gateway"` on the phone's listener, and `complete(headers)` to add whatever is
  missing from it.
* Tests: `gate_matrix(page)`.

Importing this module imports neither uvicorn nor Starlette: they load only inside `app`
and `serve` when a request is served.
"""

from __future__ import annotations

import asyncio
import contextvars
import dataclasses
import json
import secrets
from typing import Any

from .check import (
    Profile,
    Refusal,
    RequestView,
    browser,
    check,
    gateway,
    proof,
    token_matches,
    upstream,
)
from .headers import complete, response_headers
from .routes import (
    Entry,
    Field,
    Out,
    Page,
    PageError,
    Route,
    Table,
    build_table,
    export,
)
from .runtime import RuntimeInfo, read_runtime, remove_runtime, write_runtime
from .serve import serve
from .server import (
    Accepted,
    Call,
    Conflict,
    Refused,
    Snapshot,
    Topics,
    app,
    busy,
    guard,
    prove,
)
from .transport import (
    BrowserSockets,
    NotLoopback,
    NotOurServer,
    NotPrivate,
    bind_browser,
    bind_tcp,
    bind_unix,
    browser_names,
    call,
    check_private_dir,
    open_connection,
)

__all__ = [
    # Declaring a page.
    "Field",
    "Out",
    "Page",
    "PageError",
    "Route",
    # The route table.
    "Entry",
    "Table",
    "build_table",
    "export",
    # The request check.
    "Profile",
    "Refusal",
    "RequestView",
    "browser",
    "check",
    "gateway",
    "proof",
    "token_matches",
    "upstream",
    # Listeners and connections.
    "BrowserSockets",
    "NotLoopback",
    "NotOurServer",
    "NotPrivate",
    "bind_browser",
    "bind_tcp",
    "bind_unix",
    "browser_names",
    "call",
    "check_private_dir",
    "open_connection",
    # The runtime file.
    "RuntimeInfo",
    "read_runtime",
    "remove_runtime",
    "write_runtime",
    # The base.
    "Accepted",
    "Call",
    "Conflict",
    "Refused",
    "Snapshot",
    "Topics",
    "app",
    "busy",
    "guard",
    "prove",
    "serve",
    # The fixed header set.
    "complete",
    "response_headers",
    # Tests.
    "gate_matrix",
]


# --- gate_matrix ---------------------------------------------------------------------

_UNKNOWN_KEYS = tuple("xyzwvuts")
_reached: contextvars.ContextVar[list[str] | None] = contextvars.ContextVar(
    "moxi_loopback_matrix", default=None)


async def _matrix_handler(call: Call) -> dict[str, Any]:
    # Every route of the page under test points here, so a case that gets past the
    # refusal it should meet is seen, and no handler of the page is imported or run.
    reached = _reached.get()
    if reached is not None:
        reached.append("reached")
    raise RuntimeError("gate_matrix: a request the matrix sends reached a handler")


def _unknown_key(declared: Any) -> str:
    for key in _UNKNOWN_KEYS:
        if key not in declared:
            return key
    raise ValueError("gate_matrix found no undeclared key to send")


def _matrix_scope(method: str, path: str, *, port: int, token: str, host: bytes,
                  headers: list[tuple[bytes, bytes]], query: bytes) -> dict[str, Any]:
    pairs = [(b"host", host)]
    if token:
        pairs.append((b"x-moxi-token", token.encode("ascii")))
    pairs.extend(headers)
    raw = path.encode("ascii")
    return {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "server": ("127.0.0.1", port),
        "client": ("127.0.0.1", 50000),
        "scheme": "http",
        "method": method,
        "root_path": "",
        "path": path,
        "raw_path": raw,
        "query_string": query,
        "headers": pairs,
        "state": {},
    }


async def _send_one(asgi: Any, scope: dict[str, Any], body: bytes) -> tuple[int, bytes, bool]:
    sent = {"status": 0, "body": b""}
    first = [True]

    async def receive() -> dict[str, Any]:
        if first[0]:
            first[0] = False
            return {"type": "http.request", "body": body, "more_body": False}
        return {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        if message["type"] == "http.response.start":
            sent["status"] = message["status"]
        elif message["type"] == "http.response.body":
            sent["body"] += message.get("body", b"")

    reached: list[str] = []
    token = _reached.set(reached)
    try:
        await asyncio.wait_for(asgi(scope, receive, send), timeout=5.0)
    except TimeoutError:
        sent["status"] = 0
    finally:
        _reached.reset(token)
    return sent["status"], sent["body"], bool(reached)


async def _run_matrix(page: Page) -> list[tuple[str, str, int, int]]:
    target = f"{__name__}:_matrix_handler"
    routes = tuple(dataclasses.replace(route, handler=target) for route in page.routes)
    page = dataclasses.replace(page, routes=routes, health=None)
    token = secrets.token_urlsafe(32)
    # Nothing is bound: the port only has to agree between the scope, Host and the
    # profile, so each call picks its own rather than a library constant.
    port = 1024 + secrets.randbelow(65536 - 1024)
    inner = app(page, module="matrix", server_id=secrets.token_hex(16), strict=True)
    profile = upstream(inner.table, token=token, port=port)
    asgi = guard(inner, lambda: profile)
    host = b"127.0.0.1:%d" % port
    json_type = [(b"content-type", b"application/json")]

    rows: list[tuple[str, str, int, int]] = []
    failures: list[str] = []

    async def case(route: Route, name: str, expected: int, method: str, *,
                   token_: str = token, host_: bytes = host,
                   headers: list[tuple[bytes, bytes]] | None = None,
                   query: bytes = b"", body: bytes = b"", error: str | None = None) -> None:
        scope = _matrix_scope(method, route.path, port=port, token=token_, host=host_,
                              headers=list(headers or ()), query=query)
        status, answer, reached = await _send_one(asgi, scope, body)
        rows.append((route.path, name, expected, status))
        if reached:
            failures.append(f"{route.path}, {name}: reached the handler (status {status})")
        elif status != expected:
            failures.append(f"{route.path}, {name}: expected {expected}, got {status or 'no answer'}")
        elif error is not None:
            try:
                said = json.loads(answer).get("error")
            except (ValueError, AttributeError):
                said = None
            if said != error:
                failures.append(f"{route.path}, {name}: expected the 400 to say {error!r}, "
                                f"got {said!r}")

    for route in page.routes:
        act = route.kind == "act"
        method = route.method
        # A request that would pass every gate, but for the one thing each case changes.
        base_headers = json_type if act else []
        base_body = b"{}" if act else b""
        wrong = "GET" if act else "POST"
        await case(route, "wrong method", 404, wrong, headers=json_type,
                   body=b"" if act else b"{}")
        if act:
            key = _unknown_key(route.body)
            body = json.dumps({key: 0}, separators=(",", ":")).encode("ascii")
            if len(body) > route.body_cap:
                await case(route, "unknown key, over the body cap", 413, method,
                           headers=base_headers, body=body)
            else:
                await case(route, "unknown key", 400, method, headers=base_headers, body=body,
                           error=f"unknown key: {key}")
        else:
            key = _unknown_key(route.query)
            await case(route, "unknown key", 400, method, query=f"{key}=0".encode("ascii"),
                       error=f"unknown key: {key}")
        await case(route, "missing token", 403, method, token_="", headers=base_headers,
                   body=base_body)
        if act:
            await case(route, "act without JSON", 403, method,
                       headers=[(b"content-type", b"text/plain")], body=base_body)
        await case(route, "refused gate: Host", 403, method,
                   host_=b"localhost:%d" % port, headers=base_headers, body=base_body)
        await case(route, "refused gate: OPTIONS", 403, "OPTIONS", headers=base_headers,
                   body=base_body)
        await case(route, "refused gate: Origin", 403, method,
                   headers=[*base_headers, (b"origin", b"http://" + host)], body=base_body)

    if failures:
        raise AssertionError("gate_matrix: " + "; ".join(failures))
    return rows


def gate_matrix(page: Page) -> list[tuple[str, str, int, int]]:
    """Send each of `page`'s routes the requests the base must refuse, and check each
    status; raise `AssertionError` naming every case that got another.

    For each route: the wrong method (404), an unknown query or body key (400, naming the
    key), no token (403), an act without `Content-Type: application/json` (403), and a
    request refused at the Host, OPTIONS and Origin gates (403 each). The page is served in
    process by `app` behind `guard`, with an upstream profile on TCP and strict projection,
    and every handler target is replaced by one that fails the case it is reached from, so
    no handler of the page is imported or run. Returns `(path, case, expected, got)` for
    every case sent.

    Call it from a test that is not itself running an event loop.
    """
    if not isinstance(page, Page):
        raise TypeError("gate_matrix takes a Page")
    return asyncio.run(_run_matrix(page))

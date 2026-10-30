"""The shell's request path: the worker layer, `check` (through loopback's `guard`), and
the router behind them.

    worker_layer(guard(prove(Router(...)), profile))

The worker layer is outermost. Every request carrying `Service-Worker: script` leaves it
holding the one literal worker path, whatever its raw path was, `%` included, so `check`
finds the worker entry for it and the router answers it with the fixed worker script; no
route, proxy or 404 can answer it first. Nothing else about a request changes. The layer
also records the request's connection for the session's owner check.

The router, in order, for a request `check` passed:

1. the worker path: the worker answer, and the session its cookie names ends;
2. `/signin`: the sign-in page; the sign-in and signout APIs (`/api/prove` is answered by
   loopback's `prove` layer in front of the router);
3. with a valid cookie, `/` redirects to `/m/ui/` and `/m/<module>` to `/m/<module>/`;
4. a navigation anywhere else: the shell document with a valid cookie, else the
   signed-out page; a navigation never reaches a route, the push or the proxy;
5. `/m/ui/api/push`: the push channel;
6. `/m/ui/<rest>`: the shell's own page, served in process, given only the browser
   headers the proxy would pass upstream;
7. `/m/<module>/<rest>`: the proxy.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable, Iterable, Mapping
from typing import Any

from moxi.names import is_module_name
from moxi_loopback.api import (
    Entry,
    Profile,
    RequestView,
    Table,
    browser,
    response_headers,
)

from . import session as _session
from .session import (
    HEALTH_PATH,
    PROVE_PATH,
    PUSH_PATH,
    SIGNIN_API,
    SIGNIN_PATH,
    SIGNOUT_API,
    WORKER_PATH,
    Sessions,
    Static,
)

ASGIApp = Callable[[dict[str, Any], Callable[..., Awaitable[Any]], Callable[..., Awaitable[Any]]],
                   Awaitable[None]]

#: The browser headers passed on to a page served behind the shell, in process or by
#: proxy. `Cookie`, `Origin`, `Referer`, `Sec-Fetch-*`, the key and any token are not.
FORWARDED_HEADERS = frozenset({
    b"accept", b"accept-language", b"content-type", b"content-length", b"if-none-match",
    b"last-event-id",
})

#: The largest sign-in request body read.
_SIGNIN_BODY_CAP = 1024

_WORKER_RAW = WORKER_PATH.encode("ascii")
_UI_PREFIX = b"/m/ui/"
_log = logging.getLogger("moxi.ui.router")


def forwarded(headers: Iterable[tuple[bytes, bytes]]) -> list[tuple[bytes, bytes]]:
    """The headers of `headers` in `FORWARDED_HEADERS`, in order."""
    return [(name, value) for name, value in headers if name.lower() in FORWARDED_HEADERS]


# --- the worker layer ------------------------------------------------------------------


def worker_layer(inner: ASGIApp) -> ASGIApp:
    """The shell's outermost ASGI layer.

    A request with `Service-Worker: script` goes on with its raw path, path and query
    replaced by the worker path; every other request goes on untouched. Each request's
    connection is recorded for the owner check.
    """

    async def layer(scope: dict[str, Any], receive: Callable[..., Any],
                    send: Callable[..., Any]) -> None:
        if scope.get("type") == "http":
            _session.connection.set((scope.get("client"), scope.get("server")))
            if _session.is_worker_request(scope.get("headers") or ()):
                scope = {**scope, "raw_path": _WORKER_RAW, "path": WORKER_PATH,
                         "query_string": b""}
        await inner(scope, receive, send)

    return layer


# --- the table and the profile ---------------------------------------------------------


def shell_table(modules: Iterable[str] = (), mounts: Mapping[str, Mapping[str, Any]] | None = None
                ) -> Table:
    """The shell's route table: its literal entries, `/m/<module>` for `ui` and each of
    `modules`, and each export in `mounts` (module to `export()`) under `/m/<module>`.

    Raises `ValueError` for a name that is not a module name, or an export the table
    refuses.
    """
    table = Table()
    table.add("GET", "/")
    table.add("GET", SIGNIN_PATH)
    table.add("POST", SIGNIN_API, "act")
    table.add("POST", SIGNOUT_API, "act")
    table.add("GET", PROVE_PATH)
    table.add("GET", HEALTH_PATH)
    table.add("GET", PUSH_PATH)
    table.add("GET", WORKER_PATH)
    names = {"ui", *modules}
    for name in sorted(names):
        if not is_module_name(name):
            raise ValueError(f"{name!r} is not a module name")
        table.add("GET", f"/m/{name}")
    for name, export in sorted((mounts or {}).items()):
        if not is_module_name(name):
            raise ValueError(f"{name!r} is not a module name")
        table.mount(f"/m/{name}", export)
    return table


#: The entry the fallback gives a navigation the table does not hold, such as a panel's
#: own route. The router answers it with a static page and nothing else.
NAVIGATION = Entry("GET", "/m/ui/", "builtin")


def fallback(view: RequestView) -> Entry | None:
    """A GET builtin entry for a navigation; None for anything else, which is the 404."""
    if _session.is_navigation(view.method, view.headers):
        return NAVIGATION
    return None


def shell_profile(table: Table, sessions: Sessions, names: Iterable[str]) -> Profile:
    """The shell's `browser` profile: `table` (frozen by this), the session callable, the
    Host names its listeners admit, and the navigation fallback."""
    return browser(table, session=sessions, names=names, fallback=fallback)


# --- the router -------------------------------------------------------------------------


async def _send(send: Callable[..., Any], answer: Static) -> None:
    status, headers, body = answer
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})


def _plain(status: int, *, extra: Iterable[tuple[str, str]] = ()) -> Static:
    pairs = list(extra)
    if status >= 200 and status not in (204, 304):
        pairs.append(("Content-Length", "0"))
    return status, response_headers(extra=pairs), b""


def _json(status: int, value: Any) -> Static:
    body = json.dumps(value, separators=(",", ":")).encode("ascii")
    return status, response_headers(extra=[("Content-Type", "application/json"),
                                           ("Content-Length", str(len(body)))]), body


_MISSING_DOCUMENT = (
    b'<!doctype html>\n<html lang="en"><head><meta charset="utf-8"><title>Moxi</title>'
    b"</head><body><p>The shell's page is not built into this install.</p></body></html>\n"
)


def _document() -> Static:
    body = _MISSING_DOCUMENT
    return 200, response_headers(extra=[("Content-Type", "text/html; charset=utf-8"),
                                        ("Content-Length", str(len(body)))]), body


async def _read_body(receive: Callable[..., Any], cap: int) -> bytes | None:
    """The request body, or None when it runs past `cap` bytes."""
    body = b""
    while True:
        message = await receive()
        if message.get("type") != "http.request":
            return None
        body += message.get("body", b"")
        if len(body) > cap:
            return None
        if not message.get("more_body"):
            return body


class Router:
    """The shell's router, behind `guard`.

    `page` is the shell's own page as loopback's in-process `app` (step 6); `push` the push
    channel (step 5); `proxy` the module proxy (step 7); each None answers 404. `document()`
    gives the shell document's answer (step 4). `on_signout()` runs after a signout.
    """

    def __init__(self, sessions: Sessions, *, page: ASGIApp | None = None,
                 push: ASGIApp | None = None, proxy: ASGIApp | None = None,
                 document: Callable[[], Static] = _document,
                 on_signout: Callable[[], None] | None = None):
        self.sessions = sessions
        self.page = page
        self.push = push
        self.proxy = proxy
        self.document = document
        self.on_signout = on_signout

    async def __call__(self, scope: dict[str, Any], receive: Callable[..., Any],
                       send: Callable[..., Any]) -> None:
        if scope.get("type") != "http":
            return
        method = scope.get("method", "")
        raw = scope.get("raw_path") or b""
        headers = list(scope.get("headers") or ())

        # 1. The worker answer. Only the worker layer puts a request here, and the session
        # callable refuses one without the header.
        if raw == _WORKER_RAW:
            ended = self.sessions.end_named(headers)
            if ended:
                _log.info("a worker's update check ended %d session(s)", ended)
            await _send(send, _session.worker_answer())
            return

        # 2. Sign-in and signout.
        if raw == SIGNIN_PATH.encode() and method == "GET":
            await _send(send, _session.signin_page())
            return
        if raw == SIGNIN_API.encode() and method == "POST":
            await _send(send, await self._sign_in(receive))
            return
        if raw == SIGNOUT_API.encode() and method == "POST":
            self.sessions.store.clear()
            if self.on_signout is not None:
                self.on_signout()
            await _send(send, _json(200, {"ok": True}))
            return

        navigation = _session.is_navigation(method, headers)

        # 3. The two redirects, for a browser with a valid cookie.
        target = _redirect(raw) if method == "GET" else None
        if target is not None:
            if self.sessions.signed_in(headers) is not None:
                status = 302 if raw == b"/" else 308
                await _send(send, _plain(status, extra=[("Location", target)]))
                return
            if not navigation:
                await _send(send, _plain(403))
                return

        # 4. Navigations: static pages only.
        if navigation:
            if self.sessions.signed_in(headers) is not None:
                await _send(send, self.document())
            else:
                await _send(send, _session.signed_out_page())
            return

        # 5. The push channel.
        if raw == PUSH_PATH.encode():
            await self._pass(self.push, scope, receive, send)
            return

        # 6. The shell's own page, with only the forwarded headers.
        if raw.startswith(_UI_PREFIX):
            await self._pass(self.page, {**scope, "headers": forwarded(headers)}, receive, send)
            return

        # 7. A module's page, by proxy.
        if raw.startswith(b"/m/"):
            await self._pass(self.proxy, scope, receive, send)
            return

        await _send(send, _plain(404))

    async def _pass(self, inner: ASGIApp | None, scope: dict[str, Any],
                    receive: Callable[..., Any], send: Callable[..., Any]) -> None:
        if inner is None:
            await _send(send, _plain(404))
        else:
            await inner(scope, receive, send)

    async def _sign_in(self, receive: Callable[..., Any]) -> Static:
        body = await _read_body(receive, _SIGNIN_BODY_CAP)
        if body is None:
            return _plain(413)
        try:
            data = json.loads(body)
        except ValueError:
            return _json(400, {"error": "the body is not JSON"})
        if not isinstance(data, dict) or set(data) != {"ticket"}:
            return _json(400, {"error": "the body holds one key, ticket"})
        made = self.sessions.sign_in(data["ticket"])
        if made is None:
            return _plain(403)
        return _session.signin_answer(*made)


def _redirect(raw: bytes) -> str | None:
    """Where `/` or `/m/<module>` redirects, or None for any other path."""
    if raw == b"/":
        return "/m/ui/"
    parts = raw.split(b"/")
    if len(parts) == 3 and parts[1] == b"m":
        try:
            name = parts[2].decode("ascii")
        except UnicodeDecodeError:
            return None
        if is_module_name(name):
            return f"/m/{name}/"
    return None

"""The gateway's request path behind the provider check:

    watch(strip(guard(mark(Access.layer(GatewayRouter)), profile)))

- `watch` writes the audit row of every request: a `request` row for each one the router
  answered, and a refusal row, by its code, for each one the check refused before it.
- `strip` takes the headers a proxy in front adds (`Tailscale-*`, `X-Forwarded-*`,
  `Forwarded`) out of every request, so nothing past it can read them.
- `GatewayRouter`, for a request the check passed, in order:

  1. the worker path: the gateway's worker, `/sw.js`'s bytes, whatever URL the update check
     asked for;
  2. a navigation, the browser's or one the gateway's worker sent on: the gateway's shell
     document with a valid session, else the 303 to `/signin`;
  3. the PWA's files (`/manifest.webmanifest`, `/sw.js`, `/offline` and its script, the
     icons);
  4. the act paths (`/gw/act/...`), present in the table only while the acts mount;
  5. the push channel, in its gateway form;
  6. `/m/ui/<file>`: the shell's own files; `/m/ui/<route>`: the shell's own page, in
     process, redacted;
  7. `/m/<module>/<file>`: the module's file as sent; `/m/<module>/<route>`: the module's
     read by proxy, redacted.

Every answer the router builds carries the gateway's policy. A module's answer keeps the
headers its server set; a refusal the check made before the router keeps loopback's.
"""

from __future__ import annotations

import json
import logging
import secrets
import time
from collections.abc import Callable, Iterable, Mapping
from typing import Any

from moxi_ui.loopback.api import RequestView, Table, check, response_headers

from .. import config
from .. import document as _document
from ..proxy import Client, Proxy, UpstreamError, downstream
from ..router import Router, forwarded
from ..session import PUSH_PATH, WORKER_PATH, Static, admitted
from . import access as _access
from .acts import PATHS as ACT_PATHS
from .acts import Acts
from .audit import Audit
from .mount import Mount
from .shapes import BODY_LIMIT, redact, refusals, route_of
from .shapes import table as gateway_table

__all__ = ["ERROR_SHAPE", "GATE_CODES", "ICONS", "PWA_FILES", "STRIPPED", "GatewayRouter",
           "document", "mark", "strip", "watch"]

_log = logging.getLogger("moxi.ui.gateway")

#: The PWA's files at the root of the gateway's origin: path to the file in the ui's built
#: web directory and its content type.
PWA_FILES = {
    "/manifest.webmanifest": ("manifest.json", "application/manifest+json"),
    "/sw.js": ("sw.js", "text/javascript; charset=utf-8"),
    "/offline": ("offline.html", "text/html; charset=utf-8"),
    "/offline.js": ("offline.js", "text/javascript; charset=utf-8"),
    "/icon-192.png": ("icon-192.png", "image/png"),
    "/icon-512.png": ("icon-512.png", "image/png"),
}
#: The icons, which the browser fetches for the manifest before any sign-in.
ICONS = frozenset({"/icon-192.png", "/icon-512.png"})
#: The proxy's own error answers, `{"error", "module", "exit", "log"}`, `log` a path.
ERROR_SHAPE = {"error": {"type": "string", "mark": None, "nullable": False, "items": None},
               "module": {"type": "string", "mark": None, "nullable": True, "items": None},
               "exit": {"type": "int", "mark": None, "nullable": True, "items": None},
               "log": {"type": "string", "mark": "path", "nullable": True, "items": None}}
#: The audit code of each gate's refusal: gate 5 is `route` for a 404 and `content-type`
#: for a 403.
GATE_CODES = {1: "host", 2: "options", 3: "origin", 4: "credential"}
#: Header names a proxy in front adds, by prefix or whole name, taken out of every request.
STRIPPED = (b"tailscale-", b"x-forwarded-")
_STRIPPED_NAMES = frozenset({b"forwarded"})
_CONTENT_TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                  ".mjs": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
                  ".json": "application/json", ".map": "application/json",
                  ".svg": "image/svg+xml", ".png": "image/png", ".ico": "image/x-icon",
                  ".woff2": "font/woff2", ".txt": "text/plain; charset=utf-8"}
_NOTE = "moxi.gateway"


def _headers(extra: Iterable[tuple[str, str]], cache: str | None = None
             ) -> list[tuple[bytes, bytes]]:
    if cache is None:
        return response_headers(extra=list(extra), policy="gateway")
    return response_headers(cache=cache, extra=list(extra), policy="gateway")


def _empty(status: int) -> Static:
    return status, _headers([("Content-Length", "0")]), b""


def _json(status: int, value: Any) -> Static:
    body = json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return status, _headers([("Content-Type", "application/json"),
                             ("Content-Length", str(len(body)))]), body


async def _send(send: Callable[..., Any], answer: Static) -> None:
    status, headers, body = answer
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})


def _note(scope: Mapping[str, Any]) -> dict[str, Any]:
    note = scope.get(_NOTE)
    return note if isinstance(note, dict) else {}


# --- the layers ---------------------------------------------------------------------------


def strip(headers: Iterable[tuple[bytes, bytes]]) -> list[tuple[bytes, bytes]]:
    """`headers` without those a proxy in front adds."""
    return [(name, value) for name, value in headers
            if not name.lower().startswith(STRIPPED) and name.lower() not in _STRIPPED_NAMES]


def mark(inner: Callable[..., Any]) -> Callable[..., Any]:
    """`inner`, noting on the request that the check passed it on to the router."""

    async def marked(scope: dict[str, Any], receive: Callable[..., Any],
                     send: Callable[..., Any]) -> None:
        _note(scope)["routed"] = True
        await inner(scope, receive, send)

    return marked


def _view(scope: Mapping[str, Any]) -> RequestView:
    server = scope.get("server")
    transport, port = "none", None
    if isinstance(server, (tuple, list)) and len(server) == 2:
        if server[1] is None:
            transport = "unix"
        elif isinstance(server[1], int) and not isinstance(server[1], bool):
            transport, port = "tcp", server[1]
    return RequestView(method=scope.get("method", ""), raw_path=scope.get("raw_path") or b"",
                       headers=list(scope.get("headers") or ()), transport=transport,
                       port=port)


def watch(inner: Callable[..., Any], profile: Callable[[], Any], audit: Audit, *,
          device_of: Callable[[str | None], str | None],
          clock: Callable[[], float] = time.monotonic) -> Callable[..., Any]:
    """The gateway's outer layer inside the provider check: strips the proxy headers, then
    writes each request's audit row once it is answered."""

    async def watched(scope: dict[str, Any], receive: Callable[..., Any],
                      send: Callable[..., Any]) -> None:
        if scope.get("type") != "http":
            await inner(scope, receive, send)
            return
        note: dict[str, Any] = {}
        scope = {**scope, "headers": strip(scope.get("headers") or ()), _NOTE: note}
        standing = profile()
        began = clock()
        answer = {"status": 0}

        async def sending(message: dict[str, Any]) -> None:
            if message.get("type") == "http.response.start":
                answer["status"] = message.get("status", 0)
            await send(message)

        try:
            await inner(scope, receive, sending)
        finally:
            try:
                _row(scope, note, answer["status"], round((clock() - began) * 1000),
                     standing, audit, device_of)
            except Exception:  # noqa: BLE001 - a row that can't be written never fails a request
                _log.exception("the gateway's audit row can't be written")

    return watched


def _row(scope: Mapping[str, Any], note: Mapping[str, Any], status: int, ms: int,
         standing: Any, audit: Audit, device_of: Callable[[str | None], str | None]) -> None:
    raw = (scope.get("raw_path") or b"").decode("latin-1")
    id_hash = admitted.get()
    signed_in = id_hash is not None
    fields: dict[str, Any] = {"id": secrets.token_hex(8), "method": scope.get("method"),
                              "path": raw, "module": _module_of(raw), "status": status,
                              "ms": ms}
    if signed_in:
        fields["session"] = _access.session_hash(id_hash)
        fields["device"] = device_of(id_hash)
    if note.get("routed"):
        audit.request(fields, signed_in=signed_in)
        code = note.get("refusal")
        if code is not None:
            audit.refusal(code, {"path": raw, "status": status}, signed_in=signed_in)
        return
    if standing is None or status not in (403, 404):
        return
    refusal = check(_view(scope), standing)
    if refusal is None:
        return
    code = GATE_CODES.get(refusal.gate) or ("route" if refusal.status == 404 else "content-type")
    signed_in = admitted.get() is not None
    audit.refusal(code, {"path": raw, "status": status, "gate": refusal.gate},
                  signed_in=signed_in)


def _module_of(raw: str) -> str | None:
    parts = raw.split("/", 3)
    return parts[2] if len(parts) >= 3 and parts[1] == "m" and parts[2] else None


# --- the document -----------------------------------------------------------------------------


def document(mount: Mount, mode: str | None = None, name: str | None = None) -> Static:
    """The gateway's shell document: the desktop's, its root carrying `data-gateway`,
    `data-gateway-acts` and, while acts aren't mounted, `data-gateway-reason`, as they stand
    now, and its head linking the manifest."""
    mode = config.theme_mode() if mode is None else mode
    name = config.theme_palette() if name is None else name
    data = _document.body(mode, name)
    root = _document.root(mode, name)
    attributes = b' data-gateway="on" data-gateway-acts="%s"' % (b"on" if mount.mounted else b"off")
    if not mount.mounted and mount.reason:
        attributes += b' data-gateway-reason="%s"' % mount.reason.encode("ascii")
    data = data.replace(root, root[:-1] + attributes + b">", 1)
    data = data.replace(b"</head>", b'<link rel="manifest" href="/manifest.webmanifest">\n</head>', 1)
    return 200, _headers([("Content-Type", "text/html; charset=utf-8"),
                          ("Content-Length", str(len(data)))]), data


# --- the router ---------------------------------------------------------------------------


class _Proxy(Proxy):
    """The shell's proxy for a module's files on the gateway: a module's answer passes as
    its server sent it, and the proxy's own answers carry the gateway's policy, an error's
    `log` sent as a handle."""

    def __init__(self, starters: Any, *, table: Callable[[], Table], key: Callable[[], bytes]):
        super().__init__(starters, table=table)
        self.key = key

    def refused(self, status: int) -> Static:
        return _empty(status)

    def failed(self, err: UpstreamError) -> Static:
        return _json(err.status, redact(err.body, ERROR_SHAPE, self.key()))


class GatewayRouter:
    """The gateway's router, behind the check.

    `phone` is the gateway's sign-in state (`Access`); `mounts()` the exports the gateway
    mounts (module to export, the shell's own as `ui`); `table()` the gateway's current
    table; `mount()` the acts' mount now; `page` the shell's own page, in process; `push`
    the shell's `Push`; `starters` the shell's page-server starters; `acts` the act store;
    `key()` the gateway's key; `files(name)` a file of the ui's built web directory.
    """

    def __init__(self, phone: Any, *, mounts: Callable[[], Mapping[str, Mapping[str, Any]]],
                 table: Callable[[], Table], mount: Callable[[], Mount],
                 page: Callable[..., Any] | None, push: Any, starters: Any, acts: Acts,
                 key: Callable[[], bytes],
                 files: Callable[[str], bytes | None] = _access.web_file):
        self.phone, self.mounts, self.table, self.mount = phone, mounts, table, mount
        self.page, self.push, self.acts, self.key, self.files = page, push, acts, key, files
        self.client = Client(starters)
        self.proxy = _Proxy(starters, table=table, key=key)
        self._holder = Router(phone)
        self._push = push.gateway_app(key) if hasattr(push, "gateway_app") else None

    # --- plugging in --------------------------------------------------------------------

    def entries(self, mount: Mount) -> list[tuple[str, str, str]]:
        """The gateway's own entries for `mount`: the push and worker paths, the PWA's
        files, and the act paths while the acts mount."""
        own = [("GET", PUSH_PATH, "builtin"), ("GET", WORKER_PATH, "builtin"),
               *(("GET", path, "asset") for path in PWA_FILES)]
        return [*own, *ACT_PATHS] if mount.mounted else own

    def build(self, mount: Mount) -> Table:
        """The gateway's table for `mount`, from the exports mounted now."""
        return gateway_table(self.mounts(), entries=self.entries(mount), problem=_refused)

    def refusals(self) -> dict[str, str]:
        """The modules mounted now that the gateway leaves out, module to why."""
        return refusals(self.mounts())

    # --- serving --------------------------------------------------------------------------

    async def __call__(self, scope: dict[str, Any], receive: Callable[..., Any],
                       send: Callable[..., Any]) -> None:
        if scope.get("type") != "http":
            return
        method = scope.get("method", "")
        raw = (scope.get("raw_path") or b"").decode("latin-1")
        headers = list(scope.get("headers") or ())

        if raw == WORKER_PATH:
            await _send(send, self._file(PWA_FILES["/sw.js"], extra=[("Service-Worker-Allowed", "/")]))
            return
        if _access.is_navigation(method, headers):
            if self.phone.signed_in(headers) is not None:
                await _send(send, document(self.mount()))
            else:
                await _send(send, _access.signin_redirect())
            return
        if method == "GET" and raw in PWA_FILES:
            await _send(send, self._file(PWA_FILES[raw]))
            return
        if raw.startswith("/gw/act/"):
            answer, code = await self.acts(scope, receive)
            if code is not None:
                _note(scope)["refusal"] = code
            await _send(send, answer)
            return
        if raw == PUSH_PATH:
            if self._push is None:
                await _send(send, _empty(404))
                return
            await self._holder._held(self._push, scope, receive, send, finish=True)
            return
        entry = self.table().find(method, raw)
        module = _module_of(raw)
        if entry is None or module is None:
            await _send(send, _empty(404))
            return
        if module == "ui" and entry.kind == "asset":
            name = raw[len("/m/ui/"):]
            suffix = name[name.rfind("."):] if "." in name else ""
            await _send(send, self._file((name, _CONTENT_TYPES.get(suffix, "")), cache="no-cache"))
            return
        if entry.kind == "asset":
            await self._holder._held(self.proxy, scope, receive, send, finish=False)
            return
        route = route_of(self.mounts(), module, raw[len(f"/m/{module}"):])
        returns = route.get("returns") if route is not None else None
        if module == "ui":
            answer = await self._own(scope, receive, returns)
        else:
            answer = await self._read(module, scope, returns)
        if answer[0] >= 500 and answer[0] != 503:
            _note(scope)["refusal"] = "upstream"
        await _send(send, answer)

    def _file(self, spec: tuple[str, str], *, extra: Iterable[tuple[str, str]] = (),
              cache: str | None = None) -> Static:
        name, kind = spec
        body = self.files(name) if kind else None
        if body is None:
            return _empty(404)
        return 200, _headers([("Content-Type", kind), ("Content-Length", str(len(body))),
                              *extra], cache), body

    def _redacted(self, status: int, headers: list[tuple[bytes, bytes]], body: bytes,
                  returns: Mapping[str, Any] | None) -> Static:
        if not 200 <= status < 300 or returns is None:
            return status, headers, body
        if len(body) > BODY_LIMIT:
            return _empty(502)
        try:
            value = json.loads(body)
        except ValueError:
            return _empty(502)
        data = json.dumps(redact(value, returns, self.key()), separators=(",", ":"),
                          ensure_ascii=False).encode("utf-8")
        kept = [(name, value) for name, value in headers if name.lower() != b"content-length"]
        return status, [*kept, (b"content-length", str(len(data)).encode("ascii"))], data

    async def _own(self, scope: dict[str, Any], receive: Callable[..., Any],
                   returns: Mapping[str, Any] | None) -> Static:
        if self.page is None:
            return _empty(404)
        got: dict[str, Any] = {"status": 500, "headers": [], "body": b""}

        async def collect(message: dict[str, Any]) -> None:
            if message.get("type") == "http.response.start":
                got["status"] = message.get("status", 500)
                got["headers"] = list(message.get("headers") or ())
            elif message.get("type") == "http.response.body":
                got["body"] += message.get("body", b"")

        headers = forwarded(scope.get("headers") or ())
        await self.page({**scope, "headers": headers}, receive, collect)
        return self._redacted(got["status"], got["headers"], got["body"], returns)

    async def _read(self, module: str, scope: dict[str, Any],
                    returns: Mapping[str, Any] | None) -> Static:
        raw = (scope.get("raw_path") or b"").decode("latin-1")
        target = raw[len(f"/m/{module}"):]
        query = scope.get("query_string") or b""
        if not query.isascii():
            return _empty(400)
        if query:
            target += "?" + query.decode("ascii")
        try:
            response = await self.client.request(module, "GET", target,
                                                 headers=scope.get("headers") or ())
        except UpstreamError as err:
            body = redact(err.body, ERROR_SHAPE, self.key())
            return _json(err.status, body)
        async with response:
            body = b""
            try:
                async for chunk in response.chunks():
                    body += chunk
                    if len(body) > BODY_LIMIT and 200 <= response.status < 300:
                        return _empty(502)
            except Exception:  # noqa: BLE001 - an answer that broke off sends nothing
                _log.warning("%s's answer broke off on the gateway", module, exc_info=True)
                return _empty(502)
            return self._redacted(response.status, downstream(response.headers), body, returns)


def _refused(module: str, text: str) -> None:
    _log.warning("%s gets no gateway mount: %s", module, text)


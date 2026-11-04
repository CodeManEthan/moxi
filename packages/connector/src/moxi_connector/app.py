"""The door's ASGI app, built whole from one origin, and the front that swaps it.

`build_app(origin, host)` is the app for one origin O (`moxi_connector.origin`); with no
origin it is `NoOrigin`, which answers 503 to every request. The app built from O:

1. **The Host gate**, before anything else: a `Host` header that isn't O's host, with or
   without its port, in any case, is answered 421. No forwarded header (`X-Forwarded-*`,
   `Forwarded`, `Tailscale-*`) is read for this or any other decision.
2. **The routes**, each path served by one handler:

   | path | methods | served by |
   |---|---|---|
   | `/mcp` | POST | the sign-in's guard, then the MCP SDK's streamable HTTP app |
   | `/mcp` | any other | 405, `Allow: POST` |
   | each path of the sign-in's `Mount.routes` | its own | the sign-in |
   | anything else | any | an empty 404 |

The MCP app is the SDK's low-level server in **stateless** mode with **JSON** replies: no
MCP session lives between requests, so a restart or a swap loses nothing a client holds,
and no event stream is ever held open. `subscriptions/listen` is not served. Both ways in
work: a client that sends `server/discover` first and one that sends `initialize` first
both reach the tools. `tools/list` answers every tool of every started provider
(`moxi_connector.providers.Registry.tools`); `prompts/list` and `resources/list` answer
empty lists. A request body is at most `MAX_BODY` bytes. The SDK's DNS-rebinding check is
set from O too: O's host as the one allowed host and O as the one allowed `Origin`.

`Front` is the app uvicorn serves for the process's whole life. For each HTTP request it:

- makes the request's `Call`: a request id, and the `after_answer` callbacks a tool
  registers (`moxi_connector.provider.Ctx`);
- passes the request to the current app, and counts it as that app's while it runs, so a
  swap lets requests already started finish on the old app;
- watches the ASGI `send` seam: once the final `http.response.body` (`more_body` false)
  is sent without an error, the callbacks run with `written=True`; when `send` raises, or
  the request ends with no final body, they run with `written=False`;
- writes one row of the request to the call log (`moxi_connector.calllog`): time, id,
  method, route class, JSON-RPC method, tool and provider, status, milliseconds, grant
  and client, the first 64 characters of the user agent, the `X-Forwarded-For` value as an
  untrusted hint, and the fields the tool added through `ctx.log`.

The tool learns its call through a context variable set just before the SDK's app runs.
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import json
import logging
import secrets
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

import jsonschema
import mcp.types as types
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import (StreamableHTTPASGIApp,
                                                StreamableHTTPSessionManager)
from mcp.server.transport_security import TransportSecuritySettings

from . import calllog
from .origin import ASM_PATH, MCP_PATH, PRM_PATH, Origin
from .provider import TOOL_FAILED, Ctx, Result, Tool
from .providers import Registry
from .signin import GRANT, Closed, Grant, SignIn

log = logging.getLogger("moxi.connector.app")

#: The largest request body the MCP endpoint takes (a comms message is at most 16,000
#: characters).
MAX_BODY = 256 * 1024
#: Seconds an old app keeps serving the requests it started after a swap, before it stops.
RETIRE_WAIT = 120.0
#: The text every request gets while there is no origin.
NO_ORIGIN = b"The connector has no public address yet. Run moxi connector status.\n"
#: The scope key that holds the request's `Call`.
CALL_KEY = "moxi.connector.call"
#: Route classes by path, for the call log.
ROUTE_CLASSES = {MCP_PATH: "mcp", PRM_PATH: "prm", ASM_PATH: "asm", "/register": "register",
                 "/authorize": "authorize", "/token": "token", "/revoke": "revoke"}
#: The longest user agent and JSON-RPC method a row keeps.
SHORT = 64

#: The call a tool runs in, set just before the SDK's app runs.
CALL: contextvars.ContextVar[Call] = contextvars.ContextVar("moxi_connector_call")


@dataclass
class Host:
    """What every app the process builds shares: the providers' tools, the sign-in, the
    call log, and the allowed redirect URIs (the page's `form-action`)."""

    registry: Registry
    signin: SignIn = field(default_factory=Closed)
    calllog: calllog.CallLog = field(default_factory=calllog.MemoryLog)
    redirect_uris: tuple[str, ...] = ()
    name: str = "connector"
    version: str = ""


@dataclass(eq=False)
class Call:
    """One HTTP request as the door sees it."""

    request_id: str
    method: str
    route: str
    started: float
    user_agent: str | None = None
    forwarded_for: str | None = None
    status: int | None = None
    grant: Grant | None = None
    rpc: str | None = None
    tool: str | None = None
    provider: str | None = None
    fields: dict[str, Any] = field(default_factory=dict)
    after: list[Callable[[bool], Any]] = field(default_factory=list)
    answered: bool = False
    body: bytearray = field(default_factory=bytearray, repr=False)

    def ctx(self) -> Ctx:
        grant = self.grant
        return Ctx(grant_id=grant.id if grant else None,
                   grant_label=grant.label if grant else None,
                   request_id=self.request_id, _after=self.after, _fields=self.fields)

    def row(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "at": calllog.now(), "kind": "request", "request": self.request_id,
            "method": self.method, "route": self.route, "rpc": self.rpc,
            "tool": self.tool, "provider": self.provider, "status": self.status,
            "ms": round((time.monotonic() - self.started) * 1000, 1),
            "grant": self.grant.id if self.grant else None,
            "client": self.grant.client_id if self.grant else None,
            "user_agent": self.user_agent,
        }
        if self.forwarded_for is not None:
            out["forwarded_for_untrusted"] = self.forwarded_for
        for key, value in self.fields.items():
            out.setdefault(key, value)
        return out


def header(scope: Mapping[str, Any], name: bytes) -> str | None:
    """The one value of header `name` (lower case), or None when absent or repeated."""
    found = [v for k, v in scope.get("headers", ()) if k.lower() == name]
    if len(found) != 1:
        return None
    return found[0].decode("latin-1")


def clean(text: str | None, limit: int = SHORT) -> str | None:
    """`text` with control characters removed, cut to `limit` characters."""
    if text is None:
        return None
    return "".join(ch for ch in text if ch.isprintable())[:limit]


async def answer(send, status: int, body: bytes = b"", *,
                 headers: tuple[tuple[bytes, bytes], ...] = ()) -> None:
    """A whole plain answer."""
    head = [(b"content-length", str(len(body)).encode()), *headers]
    if body:
        head.insert(0, (b"content-type", b"text/plain; charset=utf-8"))
    await send({"type": "http.response.start", "status": status, "headers": head})
    await send({"type": "http.response.body", "body": body})


# -- the MCP server over the providers' tools ----------------------------------------------

def _annotations(given: Mapping[str, Any] | None) -> types.ToolAnnotations | None:
    if not given:
        return None
    return types.ToolAnnotations.model_validate(dict(given))


def _listed(tool: Tool) -> types.Tool:
    return types.Tool(name=tool.name, title=tool.title, description=tool.description,
                      input_schema=dict(tool.input_schema),
                      output_schema=(dict(tool.output_schema)
                                     if tool.output_schema is not None else None),
                      annotations=_annotations(tool.annotations))


def to_mcp(result: Result) -> types.CallToolResult:
    """A provider's `Result` as the SDK's answer."""
    structured = dict(result.structured) if result.structured is not None else None
    if result.is_error and result.refusal and (structured is None
                                               or "refusal" not in structured):
        structured = {**(structured or {}), "refusal": result.refusal}
    return types.CallToolResult(content=[types.TextContent(type="text", text=result.text)],
                                structured_content=structured, is_error=result.is_error)


def _error(text: str, refusal: str | None = None) -> types.CallToolResult:
    return to_mcp(Result(text, None, is_error=True, refusal=refusal))


class Tools:
    """The MCP handlers over one table of tools (`Registry.tools()`)."""

    def __init__(self, host: Host):
        self.host = host
        self.table = host.registry.tools()

    async def list_tools(self, ctx, params) -> types.ListToolsResult:
        return types.ListToolsResult(tools=[_listed(tool) for _, tool in self.table.values()])

    def input_schema(self, name: str):
        entry = self.table.get(name)
        return dict(entry[1].input_schema) if entry is not None else None

    async def list_prompts(self, ctx, params) -> types.ListPromptsResult:
        return types.ListPromptsResult(prompts=[])

    async def list_resources(self, ctx, params) -> types.ListResourcesResult:
        return types.ListResourcesResult(resources=[])

    async def call_tool(self, ctx, params) -> types.CallToolResult:
        name = params.name
        call = CALL.get(None)
        if call is None:
            # The context variable reaches every call in stateless JSON mode; a call
            # without one still runs, under a fresh id, and says so in the log.
            log.warning("a tool call arrived without its request's context")
            call = Call(new_id(), "POST", "mcp", time.monotonic())
        entry = self.table.get(name)
        call.tool = name
        if entry is None:
            return _error(f"There is no tool named {clean(name)}.", "unknown-tool")
        module, tool = entry
        call.provider = module
        args = dict(params.arguments or {})
        try:
            jsonschema.validate(args, dict(tool.input_schema))
        except jsonschema.ValidationError as e:
            return _error(f"The arguments don't fit the tool: {clean(e.message, 200)}",
                          "invalid-arguments")
        except jsonschema.SchemaError:
            log.error("the tool %s of %s declares a schema that doesn't read", name, module)
            return await self._failed(call, module, name, "its input schema doesn't read")
        try:
            result = await tool.call(args, call.ctx())
            if not isinstance(result, Result):
                raise TypeError(f"the tool answered {type(result).__name__}, not a Result")
        except Exception as e:  # noqa: BLE001 - a provider's fault stays its own
            log.exception("the tool %s of %s failed (request %s)", name, module,
                          call.request_id)
            return await self._failed(call, module, name, type(e).__name__)
        return to_mcp(result)

    async def _failed(self, call: Call, module: str, name: str, why: str):
        await write(self.host.calllog, {
            "at": calllog.now(), "kind": "tool-error", "request": call.request_id,
            "tool": name, "provider": module, "error": why,
            "grant": call.grant.id if call.grant else None}, durable=True, collapse=None)
        return _error(TOOL_FAILED, "tool-error")


def new_id() -> str:
    """A request id: 12 hex digits."""
    return secrets.token_hex(6)


async def write(log_: calllog.CallLog, row: Mapping[str, Any], *, durable: bool,
                collapse: str | None) -> None:
    """Hand one row to the call log off the loop, since a durable write waits on the disk.
    A writer that fails loses the row, logged; it never fails the request."""
    try:
        await asyncio.get_running_loop().run_in_executor(
            None, lambda: log_.write(row, durable=durable, collapse=collapse))
    except Exception:  # noqa: BLE001
        log.exception("a call log row couldn't be written")


# -- the apps ------------------------------------------------------------------------------

class _Served:
    """What every app shares: the count of requests running on it, and its retirement."""

    origin: Origin | None = None

    def __init__(self):
        self.running = 0
        self._idle: asyncio.Event | None = None

    def enter(self) -> None:
        self.running += 1

    def leave(self) -> None:
        self.running -= 1
        if self.running == 0 and self._idle is not None:
            self._idle.set()

    async def drain(self, wait: float) -> bool:
        """Wait at most `wait` seconds for the requests running here to finish. True when
        they all did."""
        if self.running == 0:
            return True
        self._idle = asyncio.Event()
        try:
            await asyncio.wait_for(self._idle.wait(), timeout=wait)
            return True
        except TimeoutError:
            return False

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None


class NoOrigin(_Served):
    """The app while there is no origin: 503 to every request, with the reason."""

    async def __call__(self, scope, receive, send) -> None:
        await answer(send, 503, NO_ORIGIN, headers=((b"cache-control", b"no-store"),))


class App(_Served):
    """The app built from one origin. `start()` runs its MCP session manager, `stop()`
    ends it; the session manager runs in a task of its own, so any task may stop it."""

    def __init__(self, origin: Origin, host: Host):
        super().__init__()
        self.origin = origin
        self.host = host
        self.mount = host.signin.mount(origin)
        self.tools = Tools(host)
        self.server = Server(
            host.name, version=host.version,
            on_list_tools=self.tools.list_tools, on_call_tool=self.tools.call_tool,
            on_list_prompts=self.tools.list_prompts,
            on_list_resources=self.tools.list_resources,
            get_tool_input_schema=self.tools.input_schema)
        self.security = TransportSecuritySettings(
            enable_dns_rebinding_protection=True, allowed_hosts=list(origin.allowed_hosts),
            allowed_origins=[origin.url])
        self.manager = StreamableHTTPSessionManager(
            app=self.server, json_response=True, stateless=True,
            security_settings=self.security, max_request_body_size=MAX_BODY)
        sdk = StreamableHTTPASGIApp(self.manager)
        guard = self.mount.guard or Closed().mount(origin).guard
        self.mcp = guard(_in_call(sdk))
        self._task: asyncio.Task | None = None
        self._stop: asyncio.Event | None = None

    async def start(self) -> None:
        if self._task is not None:
            return
        ready = asyncio.Event()
        self._stop = asyncio.Event()
        stop = self._stop

        async def run() -> None:
            async with self.manager.run():
                ready.set()
                await stop.wait()

        self._task = asyncio.get_running_loop().create_task(run(), name="connector-mcp")
        waiter = asyncio.ensure_future(ready.wait())
        done, _ = await asyncio.wait({waiter, self._task}, return_when=asyncio.FIRST_COMPLETED)
        if self._task in done:
            waiter.cancel()
            self._task.result()
            raise RuntimeError("the MCP session manager ended at its start")

    async def stop(self) -> None:
        if self._task is None or self._stop is None:
            return
        self._stop.set()
        with contextlib.suppress(Exception, asyncio.CancelledError):
            await self._task

    async def __call__(self, scope, receive, send) -> None:
        origin = self.origin
        assert origin is not None
        if not origin.admits_host(header(scope, b"host")):
            await answer(send, 421, b"This server answers only for its own address.\n")
            return
        scope = _canonical_host(scope, origin)
        path = scope.get("path", "")
        if path == MCP_PATH:
            if scope.get("method") != "POST":
                await answer(send, 405, headers=((b"allow", b"POST"),))
                return
            await self.mcp(scope, receive, send)
            return
        handler = self.mount.routes.get(path)
        if handler is None:
            await answer(send, 404)
            return
        await handler(scope, receive, send)


def _canonical_host(scope: dict, origin: Origin) -> dict:
    """The scope with its one `Host` header as O writes it, lower case: the gate has
    admitted it in any case, and the SDK's DNS-rebinding check compares it exactly."""
    value = header(scope, b"host")
    if value is None:
        return scope
    canonical = value.strip().lower().encode("latin-1")
    headers = [(k, canonical if k.lower() == b"host" else v) for k, v in scope["headers"]]
    return {**scope, "headers": headers}


def _in_call(app):
    """Wrap the SDK's app so its tools see the request's `Call`, with the grant the
    guard set."""

    async def inner(scope, receive, send) -> None:
        call: Call | None = scope.get(CALL_KEY)
        if call is None:
            await app(scope, receive, send)
            return
        grant = scope.get(GRANT)
        if isinstance(grant, Grant):
            call.grant = grant
        token = CALL.set(call)
        try:
            await app(scope, receive, send)
        finally:
            CALL.reset(token)

    return inner


def build_app(origin: Origin | None, host: Host) -> _Served:
    """The whole app for `origin`: `App`, or `NoOrigin` when there is none."""
    return NoOrigin() if origin is None else App(origin, host)


class Front:
    """The app uvicorn serves: each request runs on the current app, with its `Call`."""

    def __init__(self, app: _Served, host: Host):
        self.app = app
        self.host = host
        self._retiring: set[asyncio.Task] = set()

    @property
    def origin(self) -> Origin | None:
        return self.app.origin

    async def swap(self, new: _Served) -> None:
        """Start `new` and make it the current app. The old one keeps the requests it
        started for at most `RETIRE_WAIT` seconds, then stops."""
        await new.start()
        old, self.app = self.app, new
        task = asyncio.get_running_loop().create_task(self._retire(old))
        self._retiring.add(task)
        task.add_done_callback(self._retiring.discard)

    async def _retire(self, old: _Served, wait: float = RETIRE_WAIT) -> None:
        if not await old.drain(wait):
            log.warning("requests on the old origin's app were still running after %g s",
                        wait)
        await old.stop()

    async def close(self) -> None:
        """Stop the current app and every retiring one."""
        for task in list(self._retiring):
            task.cancel()
        for task in list(self._retiring):
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        await self.app.stop()

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] == "lifespan":
            await _lifespan(receive, send)
            return
        if scope["type"] != "http":
            return
        path = scope.get("path", "")
        call = Call(new_id(), scope.get("method", ""), ROUTE_CLASSES.get(path, "other"),
                    time.monotonic(),
                    user_agent=clean(header(scope, b"user-agent")),
                    forwarded_for=clean(header(scope, b"x-forwarded-for")))
        scope = {**scope, CALL_KEY: call}
        keep_body = call.route == "mcp"

        async def receive_() -> dict:
            message = await receive()
            if keep_body and message.get("type") == "http.request":
                room = MAX_BODY - len(call.body)
                if room > 0:
                    call.body += message.get("body", b"")[:room]
            return message

        async def send_(message: dict) -> None:
            if message["type"] == "http.response.start":
                call.status = int(message["status"])
            final = (message["type"] == "http.response.body"
                     and not message.get("more_body", False))
            try:
                await send(message)
            except BaseException:
                await _settle(call, False)
                raise
            if final:
                await _settle(call, True)

        app = self.app
        app.enter()
        try:
            await app(scope, receive_, send_)
        except Exception:
            log.exception("request %s failed", call.request_id)
            if call.status is None:
                with contextlib.suppress(Exception):
                    await answer(send_, 500)
        finally:
            app.leave()
            await _settle(call, False)
            if keep_body:
                call.rpc = _rpc_method(call.body)
            call.body = bytearray()
            await self._log(call)

    async def _log(self, call: Call) -> None:
        status = call.status or 0
        collapse = (str(status) if call.grant is None
                    and status in calllog.COLLAPSED_STATUSES else None)
        durable = call.tool is not None and call.grant is not None
        await write(self.host.calllog, call.row(), durable=durable, collapse=collapse)


async def _settle(call: Call, written: bool) -> None:
    """Run the request's `after_answer` callbacks once, with `written`."""
    if call.answered:
        return
    call.answered = True
    for fn in list(call.after):
        try:
            out = fn(written)
            if asyncio.iscoroutine(out):
                await out
        except Exception:  # noqa: BLE001 - a callback's fault is its provider's
            log.exception("an after_answer callback of request %s failed", call.request_id)


def _rpc_method(body: bytes) -> str | None:
    """The JSON-RPC method of a request body, cut and cleaned; None when it has none."""
    if not body:
        return None
    try:
        message = json.loads(body)
    except ValueError:
        return None
    if isinstance(message, dict) and isinstance(message.get("method"), str):
        return clean(message["method"])
    return None


async def _lifespan(receive, send) -> None:
    while True:
        message = await receive()
        if message["type"] == "lifespan.startup":
            await send({"type": "lifespan.startup.complete"})
        elif message["type"] == "lifespan.shutdown":
            await send({"type": "lifespan.shutdown.complete"})
            return


__all__ = ["App", "CALL", "CALL_KEY", "Call", "Front", "Host", "MAX_BODY", "NO_ORIGIN",
           "NoOrigin", "RETIRE_WAIT", "ROUTE_CLASSES", "Tools", "build_app", "header",
           "new_id", "to_mcp"]

"""The page-server base: the request check as an ASGI layer, and dispatch behind it.

`guard(inner, profile)` is the outermost layer on every listener. It runs `check` on each
request and answers a refusal itself, so nothing behind it sees a request `check` refused.
`app(page, ...)` is a page's whole server without a listener: dispatch on the raw path,
the query and body allowlist, projection, assets, the fixed header set, the caps, the
stream route with its topics, and the `/api/health` built-in. It answers 403 to a request
`guard` did not pass. `prove(inner, ...)` answers `GET /api/prove` for a server that holds
a runtime file's token.

`Topics` holds the last value of each named topic and hands each publish to the open
streams; `Snapshot` rebuilds one topic's value single-flight and publishes it only when it
changed. `busy(name)` holds the idle exit while work runs. `Activity` holds the counts
the idle exit reads.

Starlette and uvicorn are imported only inside the functions that serve a request, so a
page module that imports this file to name its handlers' helpers loads neither. Every
window and deadline here is on `time.monotonic()`.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import importlib
import inspect
import json
import logging
import os
import re
import threading
import time
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass
from typing import Any

from moxi_loopback.check import Profile, RequestView, check, proof, prove_nonce
from moxi_loopback.headers import NO_CACHE, NO_STORE, response_headers
from moxi_loopback.routes import (
    CONTENT_TYPES,
    HEALTH_PATH,
    PROVE_PATH,
    BadRequest,
    Page,
    PageError,
    ProjectionError,
    Route,
    build_table,
    parse_body,
    parse_query,
    project,
)

# Only the names `moxi_loopback.api` re-exports are public; the rest are the package's own.
__all__ = [
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
]

#: The scope key `guard` sets on a request `check` passed. `app` refuses any request
#: without it.
CHECKED = "moxi_loopback.checked"
#: The scope key a listener's protocol sets when it hands a request to the app and counts
#: it with `Activity.hand_off`; `guard` settles it.
HANDED = "moxi_loopback.handed"

#: The most bytes of request headers the base serves, counted as on the wire.
HEADER_CAP = 16 * 1024
#: Requests in the app at once, streams aside; past it a request gets 503.
INFLIGHT_CAP = 32
#: Streams open at once, counted apart so streams never take the slots acts need.
STREAM_CAP = 8
#: Seconds a health check may take before the route answers it timed out.
HEALTH_LIMIT = 2.0
#: Seconds between two comment lines on an open stream.
HEARTBEAT = 20.0

_TOPIC = re.compile(r"\A[a-z0-9]+(-[a-z0-9]+)*\Z")
_PROVE_RAW = PROVE_PATH.encode("ascii")
_JSON = "application/json"


# --- what a handler gets and gives ---------------------------------------------------


class Conflict(Exception):
    """Raised by a handler for a 409. The body is `{"conflict": text}`."""

    def __init__(self, text: str):
        if not isinstance(text, str):
            raise TypeError("a Conflict carries a string")
        self.text = text
        super().__init__(text)


class Refused(Exception):
    """Raised by a handler for a 422. The text is sent as is, as `{"refused": text}`."""

    def __init__(self, text: str):
        if not isinstance(text, str):
            raise TypeError("a Refused carries a string")
        self.text = text
        super().__init__(text)


@dataclass(frozen=True)
class Accepted:
    """Returned by a handler for a 202: the work goes on, and `status_path` reports it.
    The body is `{"status": status_path}`."""

    status_path: str

    def __post_init__(self) -> None:
        if not isinstance(self.status_path, str):
            raise TypeError("an Accepted carries its status path as a string")


class Stream:
    """The stream route's answer, made with `call.stream(topics)`."""

    __slots__ = ("topics",)

    def __init__(self, topics: Topics):
        if not isinstance(topics, Topics):
            raise TypeError("a stream is made from a Topics")
        self.topics = topics


class Call:
    """What a handler gets: the checked `query` and `body`, the `module` name, `busy`
    and, on the stream route, `stream`."""

    __slots__ = ("body", "module", "query")

    def __init__(self, module: str, query: Mapping[str, Any], body: Mapping[str, Any]):
        self.module = module
        self.query = query
        self.body = body

    @staticmethod
    def busy(name: str) -> contextlib.AbstractContextManager[None]:
        """Hold the idle exit while the `with` block runs (see `busy`)."""
        return busy(name)

    @staticmethod
    def stream(topics: Topics) -> Stream:
        """The answer of the stream route: an event stream over `topics`."""
        return Stream(topics)


# --- busy holds and the counts --------------------------------------------------------


class _Holds:
    """The busy holds of this process, by name, from any thread."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._names: dict[str, int] = {}
        self._quiet_since = time.monotonic()

    @contextlib.contextmanager
    def hold(self, name: str) -> Iterator[None]:
        if not isinstance(name, str) or not name:
            raise ValueError("a busy hold has a name")
        with self._lock:
            self._names[name] = self._names.get(name, 0) + 1
        try:
            yield
        finally:
            with self._lock:
                left = self._names[name] - 1
                if left:
                    self._names[name] = left
                else:
                    del self._names[name]
                if not self._names:
                    self._quiet_since = time.monotonic()

    def names(self) -> list[str]:
        with self._lock:
            return sorted(self._names)

    def quiet_since(self) -> float | None:
        """When the last hold ended, or None while one is held."""
        with self._lock:
            return None if self._names else self._quiet_since


_HOLDS = _Holds()


def busy(name: str) -> contextlib.AbstractContextManager[None]:
    """`with busy("launch"):` holds the idle exit for the length of the block.

    It may be used from any thread. It holds only the idle decision: once a stop is
    decided, work under a hold is cut like any other.
    """
    return _HOLDS.hold(name)


class Activity:
    """The counts the idle exit reads: open requests, open streams and busy holds.

    A request is counted from the step that decides it passed `check` and is not a
    health probe, until its response ends. Health probes, proofs and refused requests
    are never counted, so nothing sent without the token lengthens an idle run and a
    probe never breaks one.

    A listener whose protocol hands requests to the app calls `hand_off(scope)` in the
    same step, and `guard` settles the hand-off in the request's first step, where the
    request is counted or answered. Until then `quiet()` is None, so an idle decision
    never falls between a request's hand-off and its count. All counting happens on the
    event loop; busy holds may change from any thread.
    """

    def __init__(self) -> None:
        self.requests = 0
        self.streams = 0
        self.probes = 0
        self.pending = 0
        self._quiet_since = time.monotonic()

    def restart(self) -> None:
        """Start the quiet run again from now. The runner calls it when it begins to
        serve, so the time spent before that, waiting for the lock, is not idle time."""
        self._quiet_since = time.monotonic()

    def hand_off(self, scope: dict[str, Any]) -> None:
        """Count a request the protocol is handing to the app, until `guard` settles it.

        Settle it with `settle(scope)` as well from the request task's done callback,
        so a task that never ran its first step does not leave the count raised.
        """
        if scope.get(HANDED) is None:
            scope[HANDED] = self
            self.pending += 1

    def _settled(self) -> None:
        self.pending -= 1

    def quiet(self, now: float | None = None) -> float | None:
        """Seconds the three counts have all been zero, or None while any is not, or
        while a handed-off request is not yet settled."""
        if self.pending or self.requests or self.streams:
            return None
        held = _HOLDS.quiet_since()
        if held is None:
            return None
        now = time.monotonic() if now is None else now
        return max(0.0, now - max(self._quiet_since, held))

    def open(self) -> dict[str, Any]:
        """What the health route reports as open."""
        return {"requests": self.requests, "streams": self.streams, "busy": _HOLDS.names()}

    def _enter(self, kind: str) -> None:
        setattr(self, kind, getattr(self, kind) + 1)

    def _leave(self, kind: str) -> None:
        setattr(self, kind, getattr(self, kind) - 1)
        # A health probe is not counted for the idle exit, so its end starts no idle run.
        if kind != "probes" and not (self.requests or self.streams):
            self._quiet_since = time.monotonic()


def settle(scope: dict[str, Any]) -> None:
    """End the hand-off count `Activity.hand_off` made for this request. Idempotent."""
    activity = scope.pop(HANDED, None)
    if activity is not None:
        activity._settled()


# --- topics and the snapshot ---------------------------------------------------------


class _Subscriber:
    """One open stream's queue: the latest event per topic, by name."""

    __slots__ = ("loop", "names", "pending", "ready")

    def __init__(self, loop: asyncio.AbstractEventLoop, names: tuple[str, ...]):
        self.loop = loop
        self.names = frozenset(names)
        self.pending: dict[str, None] = {}
        self.ready = asyncio.Event()

    def push(self, name: str) -> None:
        if name in self.names:
            self.pending[name] = None
            self.ready.set()

    def take(self) -> list[str]:
        names = list(self.pending)
        self.pending.clear()
        self.ready.clear()
        return names


class Topics:
    """The last value of each named topic, and the open streams that carry them.

    `publish(name, data)` may be called from any thread or from the loop. It stores the
    value and hands the event to each open stream through its loop. A topic that has
    never been published has no value; a stream sends each topic's current value first,
    so a stream that reopens, or a second one, loses nothing. A value handed to
    `publish` is sent as it is when the stream sends it, so it must not be changed after.
    """

    def __init__(self, *names: str):
        if not names:
            raise ValueError("Topics needs at least one topic name")
        for name in names:
            if not isinstance(name, str) or not _TOPIC.match(name):
                raise ValueError(f"{name!r} is not a topic name")
        if len(set(names)) != len(names):
            raise ValueError("Topics names a topic twice")
        self.names = tuple(names)
        self._lock = threading.Lock()
        self._values: dict[str, Any] = {}
        self._subscribers: set[_Subscriber] = set()

    def __repr__(self) -> str:
        return f"Topics{self.names!r}"

    def publish(self, name: str, data: Any) -> None:
        """Store `data` as the topic's value and send it to every stream that asked for
        it. Raises `KeyError` for a name this `Topics` was not given."""
        if name not in self.names:
            raise KeyError(name)
        with self._lock:
            self._values[name] = data
            subscribers = list(self._subscribers)
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        for sub in subscribers:
            if sub.loop is running:
                sub.push(name)
                continue
            try:
                sub.loop.call_soon_threadsafe(sub.push, name)
            except RuntimeError:
                # That stream's loop has closed; the stream is gone with it.
                self._close(sub)

    def _open(self, sub: _Subscriber, names: Iterable[str]) -> list[tuple[str, Any]]:
        # Subscribe and read the current values under one lock, so a publish is either
        # in the first values or queued after them, never lost and never sent twice.
        with self._lock:
            self._subscribers.add(sub)
            return [(name, self._values[name]) for name in names if name in self._values]

    def _latest(self, names: Iterable[str]) -> list[tuple[str, Any]]:
        with self._lock:
            return [(name, self._values[name]) for name in names if name in self._values]

    def _close(self, sub: _Subscriber) -> None:
        with self._lock:
            self._subscribers.discard(sub)


class Snapshot:
    """A single-flight gatherer for one topic.

    `await snapshot.changed()` asks for one rebuild: `gather()` runs (a plain function
    through `moxi.aio.offload`, a coroutine function on the loop), and its result is
    published to `topics` under `name` only when its hash differs from the last one
    published. A fresh `Snapshot` has no last hash, so its first gather always
    publishes. Callers that ask while a rebuild waits to start share it; callers that ask
    while one is gathering share the one rebuild that follows it, so no change is missed.
    `changed()` returns when the rebuild it shares has finished, and raises what that
    gather raised.
    """

    def __init__(self, gather: Callable[[], Any], topics: Topics, name: str):
        if not callable(gather):
            raise TypeError("a Snapshot's gather is a callable")
        if not isinstance(topics, Topics):
            raise TypeError("a Snapshot publishes to a Topics")
        if name not in topics.names:
            raise KeyError(name)
        self._gather = gather
        self.topics = topics
        self.name = name
        self._last: str | None = None
        self._next: asyncio.Future[None] | None = None
        self._task: asyncio.Task[None] | None = None

    async def changed(self) -> None:
        loop = asyncio.get_running_loop()
        if self._next is None:
            self._next = loop.create_future()
            self._next.add_done_callback(_retrieve)
        waiter = self._next
        if self._task is None:
            self._task = loop.create_task(self._run())
        await asyncio.shield(waiter)

    async def _run(self) -> None:
        try:
            while self._next is not None:
                waiter, self._next = self._next, None
                try:
                    result = await self._call()
                    digest = hashlib.sha256(json.dumps(
                        result, sort_keys=True, separators=(",", ":"), allow_nan=False,
                    ).encode("utf-8")).hexdigest()
                except asyncio.CancelledError:
                    waiter.cancel()
                    raise
                except Exception as err:
                    waiter.set_exception(err)
                    continue
                if digest != self._last:
                    self.topics.publish(self.name, result)
                    self._last = digest
                waiter.set_result(None)
        finally:
            self._task = None
            if self._next is not None:
                self._next.cancel()
                self._next = None

    async def _call(self) -> Any:
        if inspect.iscoroutinefunction(self._gather):
            return await self._gather()
        from moxi import aio

        return await aio.offload(self._gather)


def _retrieve(future: asyncio.Future[Any]) -> None:
    # A rebuild nobody awaits any more must not log an unretrieved exception.
    if not future.cancelled():
        future.exception()


def read_topics(route: Route, query_string: bytes | str) -> tuple[str, ...]:
    """The topics `?topics=a,b` asks for on the stream route, or every declared topic
    when the key is absent.

    The value is split on `,`, and each part must be a declared topic name. A `%` in the
    value, an empty part, a name the route does not declare, a name given twice, or a
    repeated `topics` key raises `BadRequest` naming the key `topics` and never the value.
    """
    if isinstance(query_string, bytes):
        try:
            query_string = query_string.decode("ascii")
        except UnicodeDecodeError:
            raise BadRequest(None, "the query string is not ASCII") from None
    from urllib.parse import unquote_plus

    declared = tuple(route.topics or ())
    values = []
    for part in query_string.split("&") if query_string else ():
        key, _, value = part.partition("=")
        if unquote_plus(key) == "topics":
            values.append(value)
    if not values:
        return declared
    if len(values) > 1:
        raise BadRequest("topics", "repeated key")
    value = values[0]
    if "%" in value:
        raise BadRequest("topics", "a percent sign in the value")
    names = value.split(",")
    for name in names:
        if not name:
            raise BadRequest("topics", "an empty topic name")
        if name not in declared:
            raise BadRequest("topics", "not a declared topic")
    if len(set(names)) != len(names):
        raise BadRequest("topics", "a topic named twice")
    return tuple(names)


# --- responses -----------------------------------------------------------------------


def _response(status: int, body: bytes = b"", *, content_type: str | None = None,
              cache: str = NO_STORE, extra: Iterable[tuple[str, str]] = ()) -> Any:
    """A Starlette response carrying the fixed header set and `extra`."""
    from starlette.responses import Response

    response = Response(body, status_code=status)
    pairs = list(extra)
    if content_type is not None:
        pairs.append(("Content-Type", content_type))
    if status >= 200 and status not in (204, 304):
        pairs.append(("Content-Length", str(len(body))))
    response.raw_headers = response_headers(cache=cache, extra=pairs)
    return response


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, separators=(",", ":"), allow_nan=False).encode("ascii")


def _json(status: int, value: Any, **kwargs: Any) -> Any:
    return _response(status, _json_bytes(value), content_type=_JSON, **kwargs)


def _bad_request(err: BadRequest) -> Any:
    return _json(400, {"error": str(err)})


def _view(scope: Mapping[str, Any]) -> RequestView:
    server = scope.get("server")
    transport, port = "none", None
    if isinstance(server, (tuple, list)) and len(server) == 2:
        if server[1] is None:
            transport = "unix"
        elif isinstance(server[1], int) and not isinstance(server[1], bool):
            transport, port = "tcp", server[1]
    return RequestView(
        method=scope.get("method", ""),
        raw_path=scope.get("raw_path") or b"",
        headers=list(scope.get("headers") or ()),
        transport=transport,
        port=port,
    )


# --- guard and prove -----------------------------------------------------------------

_guard_log = logging.getLogger("moxi.loopback.guard")


def guard(inner: Callable[..., Any], profile: Callable[[], Profile]) -> Callable[..., Any]:
    """The outermost ASGI layer: `check` on every request.

    `profile()` is read once per request, so a listener that replaces its profile checks
    each request against the one current when it arrived. A refusal is answered here
    with its status, an empty body and the fixed header set. A request `check` passed
    goes to `inner` with the scope key `CHECKED` set. The request's hand-off count, if
    any, is settled first, in the request's first step.
    """

    async def guarded(scope: dict[str, Any], receive: Callable[..., Any],
                      send: Callable[..., Any]) -> None:
        settle(scope)
        if scope.get("type") != "http":
            return
        try:
            refusal = check(_view(scope), profile())
        except Exception:
            _guard_log.exception("the request check failed")
            await _response(500)(scope, receive, send)
            return
        if refusal is not None:
            _guard_log.debug("%s refused at gate %d: %d", scope.get("method"), refusal.gate,
                             refusal.status)
            await _response(refusal.status)(scope, receive, send)
            return
        await inner({**scope, CHECKED: True}, receive, send)

    return guarded


def prove(inner: Callable[..., Any], *, token: str, server_id: str,
          stopping: Callable[[], bool] = lambda: False) -> Callable[..., Any]:
    """An ASGI layer that answers `GET /api/prove` and passes everything else to `inner`.

    It goes inside `guard`, whose table holds the `/api/prove` entry. The answer is 204
    with `X-Moxi-Proof: proof(token, server_id, nonce)`. A missing, repeated or malformed
    `X-Moxi-Nonce` is a 400. Once `stopping()` is true the answer is 503 with
    `Connection: close`, so a server that has decided to stop proves nothing. A request
    without the `CHECKED` key is a 403. The token is never logged.
    """
    proof(token, server_id, "A" * 43)  # Refuses a token or server_id of the wrong shape.

    async def proving(scope: dict[str, Any], receive: Callable[..., Any],
                      send: Callable[..., Any]) -> None:
        if (scope.get("type") != "http" or scope.get("method") != "GET"
                or scope.get("raw_path") != _PROVE_RAW):
            await inner(scope, receive, send)
            return
        if not scope.get(CHECKED):
            response = _response(403)
        elif stopping():
            response = _response(503, extra=[("Connection", "close")])
        else:
            nonce = prove_nonce(_view(scope))
            if nonce is None:
                response = _json(400, {"error": "X-Moxi-Nonce is missing, repeated or malformed"})
            else:
                response = _response(204, extra=[("X-Moxi-Proof", proof(token, server_id, nonce))])
        await response(scope, receive, send)

    return proving


# --- the app -------------------------------------------------------------------------


def _resolve(target: str, what: str) -> Callable[..., Any]:
    module_name, _, name = target.partition(":")
    try:
        module = importlib.import_module(module_name)
        found = getattr(module, name)
    except Exception as err:
        raise PageError(f"the {what} {target} cannot be loaded: {type(err).__name__}: {err}") from None
    if not callable(found):
        raise PageError(f"the {what} {target} is not callable")
    return found


def _etag(body: bytes) -> str:
    return '"' + hashlib.sha256(body).hexdigest()[:16] + '"'


def _etag_matches(values: list[bytes], etag: str) -> bool:
    for value in values:
        for part in value.split(b","):
            tag = part.strip()
            if tag == b"*":
                return True
            if tag.startswith(b"W/"):
                tag = tag[2:]
            if tag == etag.encode("ascii"):
                return True
    return False


def _header_bytes(headers: Iterable[tuple[bytes, bytes]]) -> int:
    # Name, ": ", value and CRLF, as each header line travels.
    return sum(len(name) + len(value) + 4 for name, value in headers)


def _header_values(scope: Mapping[str, Any], name: bytes) -> list[bytes]:
    return [value for key, value in scope.get("headers") or () if key.lower() == name]


class PageApp:
    """A page's ASGI app; build one with `app`.

    `activity` holds the counts the idle exit reads. `table` is the page's own table, as
    `build_table` makes it.
    """

    def __init__(self, page: Page, *, module: str, server_id: str, prefix: str,
                 version: str | None, strict: bool):
        if not isinstance(page, Page):
            raise TypeError("app serves a Page")
        if not isinstance(module, str) or not module:
            raise ValueError("app needs the module name")
        if not isinstance(server_id, str) or not server_id:
            raise ValueError("app needs the server_id")
        if not isinstance(prefix, str) or prefix.endswith("/") or (prefix and not prefix.startswith("/")):
            raise ValueError("a prefix is empty or starts with / and does not end with one")
        if not prefix.isascii():
            raise ValueError("a prefix is ASCII")
        self.page = page
        self.module = module
        self.server_id = server_id
        self.prefix = prefix
        self.version = version
        self.strict = bool(strict)
        self.activity = Activity()
        self.log = logging.getLogger(f"moxi.{module}.page")
        self._prefix = prefix.encode("ascii") + b"/"
        self._started = time.monotonic()
        self._dropped_seen: set[tuple[str, str]] = set()

        self.table, names = build_table(page)
        self.table.freeze()
        self._routes = {route.path: route for route in page.routes}
        self._handlers = {route.path: _resolve(route.handler, "handler") for route in page.routes}
        self._health = _resolve(page.health, "health check") if page.health else None
        self._assets: dict[str, tuple[bytes, str, str]] = {}
        if names:
            import importlib.resources

            root = importlib.resources.files(page.package).joinpath(page.assets)
            for name in names:
                body = root.joinpath(name).read_bytes()
                suffix = name[name.rfind("."):]
                self._assets["/" + name] = (body, _etag(body), CONTENT_TYPES[suffix])
            if "/index.html" in self._assets:
                self._assets["/"] = self._assets["/index.html"]

    async def __call__(self, scope: dict[str, Any], receive: Callable[..., Any],
                       send: Callable[..., Any]) -> None:
        if scope.get("type") != "http":
            return
        started = time.monotonic()
        method = scope.get("method", "")
        status = 0
        where = "404"

        async def sending(message: dict[str, Any]) -> None:
            nonlocal status
            if message.get("type") == "http.response.start":
                status = message["status"]
            await send(message)

        # Everything up to the count runs in this first step, with no await before it, so
        # a request is counted, or answered uncounted, in the step that settled its
        # hand-off.
        if not scope.get(CHECKED):
            # Not counted: nothing reaches dispatch unless `check` passed it.
            await _response(403)(scope, receive, sending)
            self.log.debug("%s unchecked 403", method)
            return
        activity = self.activity
        entry = None
        raw = scope.get("raw_path") or b""
        if raw.startswith(self._prefix):
            entry = self.table.find(method, raw[len(self._prefix) - 1:])
            if entry is not None and entry.kind == "builtin" and entry.raw_path != HEALTH_PATH:
                entry = None  # `/api/prove` is answered by the caller, never here.
        if entry is not None and entry.raw_path == HEALTH_PATH:
            kind, full = "probes", activity.requests + activity.probes >= INFLIGHT_CAP
        elif entry is not None and entry.stream:
            kind, full = "streams", activity.streams >= STREAM_CAP
        else:
            kind, full = "requests", activity.requests + activity.probes >= INFLIGHT_CAP
        activity._enter(kind)
        response: Any = None
        if full:
            response = _response(503, extra=[("Retry-After", "1")])
        elif _header_bytes(scope.get("headers") or ()) > HEADER_CAP:
            response = _json(400, {"error": "the request headers are over 16 KiB"})
        elif entry is None:
            response = _response(404)
        else:
            where = entry.raw_path
        try:
            if response is None:
                response = await self._dispatch(entry, scope, receive)
            if response is not None:
                await response(scope, receive, sending)
        except asyncio.CancelledError:
            raise
        except Exception:
            self.log.exception("%s %s failed", method, where)
            if not status:
                await _response(500)(scope, receive, sending)
        finally:
            activity._leave(kind)
            self.log.debug("%s %s %d %.1f ms", method, where, status,
                           (time.monotonic() - started) * 1000)

    async def _dispatch(self, entry: Any, scope: dict[str, Any], receive: Callable[..., Any]) -> Any:
        if entry.kind == "asset":
            return self._asset(entry.raw_path, scope)
        if entry.raw_path == HEALTH_PATH:
            return await self._health_answer()
        route = self._routes[entry.raw_path]
        try:
            query = parse_query(route, scope.get("query_string") or b"")
            names = read_topics(route, scope.get("query_string") or b"") if route.stream else ()
            body: Mapping[str, Any] = {}
            if route.kind == "act":
                raw, refusal = await self._read_body(route, scope, receive)
                if refusal is not None:
                    return refusal
                if raw is None:
                    return None  # The client went away mid-body.
                body = parse_body(route, raw)
        except BadRequest as err:
            return _bad_request(err)
        call = Call(self.module, query, body)
        try:
            result = await self._run(self._handlers[route.path], call)
        except Conflict as err:
            return _json(409, {"conflict": err.text})
        except Refused as err:
            return _json(422, {"refused": err.text})
        if route.stream:
            return self._stream(route, result, names)
        if isinstance(result, Accepted):
            return _json(202, {"status": result.status_path})
        if isinstance(result, Stream):
            raise TypeError(f"{route.path} is not the stream route but returned a stream")
        try:
            value = project(result, route.shape, strict=self.strict,
                            dropped=lambda key: self._dropped(route.path, key))
        except ProjectionError as err:
            self.log.error("%s returned a value outside its shape: %s", route.path, err)
            return _response(500)
        return _json(200, value)

    async def _run(self, handler: Callable[..., Any], *args: Any) -> Any:
        if inspect.iscoroutinefunction(handler):
            return await handler(*args)
        from moxi import aio

        return await aio.offload(handler, *args)

    async def _read_body(self, route: Route, scope: dict[str, Any],
                         receive: Callable[..., Any]) -> tuple[bytes | None, Any]:
        lengths = _header_values(scope, b"content-length")
        if len(lengths) == 1 and lengths[0].isdigit() and int(lengths[0]) > route.body_cap:
            return None, _response(413)
        from starlette.requests import ClientDisconnect, Request

        chunks, total = [], 0
        try:
            async for chunk in Request(scope, receive).stream():
                total += len(chunk)
                if total > route.body_cap:
                    return None, _response(413)
                chunks.append(chunk)
        except ClientDisconnect:
            return None, None
        return b"".join(chunks), None

    def _dropped(self, route_path: str, key: str) -> None:
        if (route_path, key) not in self._dropped_seen:
            self._dropped_seen.add((route_path, key))
            self.log.warning("%s returned the undeclared key %s; it was dropped", route_path, key)

    def _asset(self, path: str, scope: Mapping[str, Any]) -> Any:
        body, etag, content_type = self._assets[path]
        if _etag_matches(_header_values(scope, b"if-none-match"), etag):
            return _response(304, cache=NO_CACHE, extra=[("ETag", etag)])
        return _response(200, body, content_type=content_type, cache=NO_CACHE,
                         extra=[("ETag", etag)])

    async def _health_answer(self) -> Any:
        result = None
        if self._health is not None:
            result = await self._health_check()
        return _json(200, {
            "ok": True if result is None else result[0],
            "module": self.module,
            "server_id": self.server_id,
            "version": self.version,
            "pid": os.getpid(),
            "uptime_s": round(time.monotonic() - self._started, 3),
            "open": self.activity.open(),
            "check": result,
        })

    async def _health_check(self) -> list[Any]:
        try:
            async with asyncio.timeout(HEALTH_LIMIT):
                answer = await self._run(self._health)
        except TimeoutError:
            return [False, "timed out"]
        except Exception:
            self.log.exception("the health check raised")
            return [False, "raised"]
        if (not isinstance(answer, (tuple, list)) or len(answer) != 2
                or not isinstance(answer[0], bool) or not isinstance(answer[1], str)):
            self.log.error("the health check did not return (ok, detail)")
            return [False, "not (ok, detail)"]
        return [answer[0], answer[1]]

    def _stream(self, route: Route, result: Any, names: tuple[str, ...]) -> Any:
        if not isinstance(result, Stream):
            raise TypeError(f"the stream route {route.path} did not return call.stream(topics)")
        topics = result.topics
        if set(topics.names) != set(route.topics):
            raise TypeError(f"the stream route {route.path} streams {topics!r}, "
                            f"not its declared topics")
        from starlette.responses import StreamingResponse

        response = StreamingResponse(self._events(route, topics, names))
        response.raw_headers = response_headers(
            cache=NO_STORE, extra=[("Content-Type", "text/event-stream")])
        return response

    async def _events(self, route: Route, topics: Topics, names: tuple[str, ...]) -> Any:
        sub = _Subscriber(asyncio.get_running_loop(), names)
        first = topics._open(sub, names)
        try:
            for name, value in first:
                yield self._event(route, name, value)
            while True:
                try:
                    async with asyncio.timeout(HEARTBEAT):
                        await sub.ready.wait()
                except TimeoutError:
                    yield b": heartbeat\n\n"
                    continue
                for name, value in topics._latest(sub.take()):
                    yield self._event(route, name, value)
        except ProjectionError as err:
            # A topic carries state, so a skipped event would leave the page on a stale
            # value with no sign; the stream ends instead, and the error is logged.
            self.log.error("%s topic event outside its shape, stream ended: %s", route.path, err)
        finally:
            topics._close(sub)

    def _event(self, route: Route, name: str, value: Any) -> bytes:
        cut = project(value, route.topics[name], strict=self.strict,
                      dropped=lambda key: self._dropped(f"{route.path} topic {name}", key))
        return b"event: " + name.encode("ascii") + b"\ndata: " + _json_bytes(cut) + b"\n\n"


def app(page: Page, *, module: str, server_id: str, prefix: str = "",
        version: str | None = None, strict: bool = False) -> PageApp:
    """The page's ASGI app: dispatch, the allowlist, projection, assets, the fixed
    header set, the caps, the stream and its topics, and `/api/health`.

    It has no check of its own and refuses (403) any request whose scope lacks the
    `CHECKED` key, so serve it behind `guard`. It strips `prefix` from the raw path before
    it matches, and a raw path that does not start with `prefix + "/"` is a 404. It does
    not answer `/api/prove`. Handler and health targets are resolved here, once; a
    target that cannot be loaded raises `PageError`, as `build_table` does for a refused
    asset table. `version` is reported by the health route. `strict` (the testing
    helper's) makes a value with an undeclared key a 500 rather than a dropped key.
    """
    return PageApp(page, module=module, server_id=server_id, prefix=prefix,
                   version=version, strict=strict)

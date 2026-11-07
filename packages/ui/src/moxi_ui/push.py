"""The push channel: `GET /m/ui/api/push?topics=<module>.<topic>,...`, one event stream
per browser, fed by one upstream stream per module.

Every topic carries state, not events: the first event of a topic on a stream is its
current value, and each later one replaces it. So a stream that reopens, or joins late,
loses nothing.

- **Browser streams.** A browser holds one push stream however many tabs it has open
  (its client elects one tab to hold it), so the shell takes at most 16 at once, one per
  browser profile; the 17th is a 503. Each stream asks for full topic names,
  `<module>.<topic>`. It gets `event: <module>.<topic>` with the topic's value as `data`,
  `event: moxi.state` with `{"module", "state", "detail"}` for each module it asks about,
  and a comment line every 20 s.
- **Fan-in.** Per module, the shell holds one upstream stream to the module's stream
  route, asking for the union of the topics its browser streams ask for, and starts the
  module's server for it if needed. It reopens that upstream 50 ms after the union last
  changed, so a burst of joins makes one reopen. It keeps each topic's last value and
  gives it at once to a stream that joins, and forwards each later value to the streams
  that asked for it. A value equal to the last one is not sent again.
- **Topics a module doesn't export** are dropped before the shell asks upstream, since the
  module would refuse the whole request, and the stream that asked gets one `moxi.state`
  saying which. The exported topics come from the export run, through `set_exports`.
- **State.** A module's `moxi.state` is `starting` while its upstream first opens or a new
  version starts after a drain, `up` while it streams, and `down` with a detail when it
  failed or dropped: `busy` when the module refused the stream for its own cap. A down
  upstream is tried again after 1, 2, 5, 10 and then every 30 s; the steps start over
  only once an upstream has stayed up for 30 s.
- **The shell's own topics** are `ui.servers` (the page servers' rows, published from the
  starter set while a stream asks for it) and `ui.modules` (published by the shell's
  snapshot through `publish`).
- **The gateway's form** (`gateway_app`): a stream from the phone gets only topics of
  stream routes their module declares `gateway=True`, and the shell's own, so upstream is
  asked for no other topic on its behalf; and each event it gets is redacted at fan-out
  against the topic's declared shape. A desktop stream and a phone stream share one
  upstream; only the phone's events are redacted. Its answers carry the gateway's policy.

A browser stream ends when the browser leaves or the shell stops.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections.abc import Callable, Iterable, Mapping
from typing import Any

from moxi.names import is_module_name
from moxi_ui.loopback.api import response_headers

from .gateway.shapes import out_data, redact_value, shape_data
from .proxy import Client, UpstreamBroken, UpstreamError

__all__ = [
    "BACKOFF",
    "HEARTBEAT",
    "MAX_STREAMS",
    "MAX_TOPICS",
    "OWN_TOPICS",
    "REOPEN_AFTER",
    "SERVERS_EVERY",
    "STATE_EVENT",
    "STAYED_UP",
    "BadTopics",
    "Push",
    "gateway_topics",
    "own_shapes",
    "read_topics",
    "stream_routes",
]

#: Browser push streams open at once: one per browser profile.
MAX_STREAMS = 16
#: Seconds after the union of a module's topics last changed that its upstream reopens.
REOPEN_AFTER = 0.05
#: Seconds before each try to open a down upstream again; the last repeats.
BACKOFF = (1.0, 2.0, 5.0, 10.0, 30.0)
#: Seconds an upstream must stay up before its next drop starts the backoff over, so a
#: module that crashes soon after each start is not started again every second.
STAYED_UP = 30.0
#: Seconds between two comment lines on an open browser stream.
HEARTBEAT = 20.0
#: The most topics one stream may ask for.
MAX_TOPICS = 256
#: The largest upstream event held while it arrives; a bigger one ends that upstream.
EVENT_CAP = 1024 * 1024
#: Seconds between two looks at the starter set while a stream asks for `ui.servers`.
SERVERS_EVERY = 1.0
#: The `Retry-After` of the 503 a stream past the cap gets.
BUSY_RETRY = 5
#: The event that carries a module's upstream state.
STATE_EVENT = "moxi.state"
#: The shell's own topics, under the module name `ui`.
OWN_MODULE = "ui"
OWN_TOPICS = frozenset({"servers", "modules"})
#: Fields of a `/servers` row left out of `ui.servers`, since they change every second.
_VOLATILE = frozenset({"uptime_s", "failed_for_s", "sample", "open"})

_TOPIC = re.compile(r"\A[a-z0-9]+(-[a-z0-9]+)*\Z")
_ACCEPT = [(b"accept", b"text/event-stream")]
_log = logging.getLogger("moxi.ui.push")


class BadTopics(ValueError):
    """A push request's query refused: the 400. The message names the key and never
    repeats the value."""

    def __init__(self, reason: str, key: str = "topics"):
        super().__init__(f"{reason}: {key}")


def read_topics(query_string: bytes | str) -> tuple[tuple[str, str], ...]:
    """The `(module, topic)` pairs `?topics=<module>.<topic>,...` asks for, in order.

    The query holds the one key `topics`. Its value is split on `,`; each part is a
    module name, a dot and a topic name, with no `%`, named once. Raises `BadTopics`.
    """
    if isinstance(query_string, bytes):
        try:
            query_string = query_string.decode("ascii")
        except UnicodeDecodeError:
            raise BadTopics("the query string is not ASCII") from None
    values = []
    for part in query_string.split("&") if query_string else ():
        key, _, value = part.partition("=")
        if key != "topics":
            name = key if re.fullmatch(r"[a-z0-9_]+", key) else "(a key that is not a plain name)"
            raise BadTopics("unknown key", name)
        values.append(value)
    if not values:
        raise BadTopics("missing key")
    if len(values) > 1:
        raise BadTopics("repeated key")
    value = values[0]
    if "%" in value:
        raise BadTopics("a percent sign in the value")
    pairs = []
    for name in value.split(","):
        module, dot, topic = name.partition(".")
        if not name:
            raise BadTopics("an empty topic name")
        if not dot or not is_module_name(module) or not _TOPIC.match(topic):
            raise BadTopics("not a topic name")
        pairs.append((module, topic))
    if len(set(pairs)) != len(pairs):
        raise BadTopics("a topic named twice")
    if len(pairs) > MAX_TOPICS:
        raise BadTopics(f"over {MAX_TOPICS} topics")
    return tuple(pairs)


def stream_routes(mounts: Mapping[str, Mapping[str, Any]]) -> dict[str, tuple[str, frozenset[str]]]:
    """Each module's stream route from its export, as `(path, topic names)`. A module
    whose export has no stream route with topics is left out, and so is the shell's own
    page: its topics are its own, never a stream of a server."""
    routes: dict[str, tuple[str, frozenset[str]]] = {}
    for module, export in mounts.items():
        if module == OWN_MODULE:
            continue
        for route in export.get("routes") or ():
            if route.get("stream") and route.get("kind") == "read" and route.get("topics"):
                routes[module] = (str(route["path"]), frozenset(route["topics"]))
                break
    return routes


def gateway_topics(mounts: Mapping[str, Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """Each module's topics the gateway may ask for, topic to its shape (export data):
    those of a stream route the module declares `gateway=True`."""
    topics: dict[str, dict[str, Any]] = {}
    for module, (path, _names) in stream_routes(mounts).items():
        for route in mounts[module].get("routes") or ():
            if route.get("path") == path and route.get("gateway") is True:
                declared = route.get("topics")
                if isinstance(declared, Mapping):
                    topics[module] = dict(declared)
    return topics


def own_shapes() -> dict[str, dict[str, Any]]:
    """The shapes of the shell's own topics (export data): `ui.servers`, its rows without
    the volatile fields, and `ui.modules`, the modules view."""
    from moxi_ui.loopback.api import Out

    from .page import MODULES_SHAPE, SERVER_ROW

    assert isinstance(SERVER_ROW.items, Mapping)
    row = Out("table", items={key: value for key, value in SERVER_ROW.items.items()
                              if key not in _VOLATILE})
    return {"servers": out_data(Out("table", items={"servers": Out("list", items=row)})),
            "modules": out_data(Out("table", items=dict(MODULES_SHAPE)))}


def _frame(name: str, data: bytes) -> bytes:
    lines = b"".join(b"data: " + line + b"\n" for line in data.split(b"\n"))
    return b"event: " + name.encode("ascii") + b"\n" + lines + b"\n"


def _json(value: Any) -> bytes:
    return json.dumps(value, separators=(",", ":"), allow_nan=False).encode("ascii")


def _state_frame(module: str, state: str, detail: str | None) -> bytes:
    return _frame(STATE_EVENT, _json({"module": module, "state": state, "detail": detail}))


def _not_exported(topics: Iterable[str]) -> str:
    return "not exported: " + ", ".join(sorted(topics))


class _Parser:
    """Server-sent events out of an upstream's bytes: `(name, data)` per event."""

    def __init__(self) -> None:
        self.buffer = b""

    def feed(self, chunk: bytes) -> list[tuple[str | None, bytes]]:
        self.buffer = (self.buffer + chunk).replace(b"\r\n", b"\n")
        events = []
        while True:
            block, sep, rest = self.buffer.partition(b"\n\n")
            if not sep:
                break
            self.buffer = rest
            name: str | None = None
            data: list[bytes] = []
            for line in block.split(b"\n"):
                if not line or line.startswith(b":"):
                    continue
                field, _, value = line.partition(b":")
                if value.startswith(b" "):
                    value = value[1:]
                if field == b"event":
                    name = value.decode("ascii", "replace")
                elif field == b"data":
                    data.append(value)
            if data:
                events.append((name, b"\n".join(data)))
        if len(self.buffer) > EVENT_CAP:
            raise ValueError(f"an event over {EVENT_CAP} bytes")
        return events


class _Stream:
    """One browser stream: what it asked for, and its queue of frames, the latest per
    topic, so a slow browser holds one pending frame per topic and never a backlog."""

    def __init__(self, asked: tuple[tuple[str, str], ...],
                 key: Callable[[], bytes] | None = None):
        #: The gateway's key for a stream from the phone, whose events are redacted.
        self.key = key
        self.by_module: dict[str, frozenset[str]] = {}
        for module, topic in asked:
            self.by_module[module] = self.by_module.get(module, frozenset()) | {topic}
        self.told: dict[str, frozenset[str]] = {}
        self.pending: dict[tuple[str, str], bytes] = {}
        self.ready = asyncio.Event()
        self.closed = False

    def asks(self, module: str, topic: str) -> bool:
        return topic in self.by_module.get(module, ())

    def put(self, key: tuple[str, str], frame: bytes) -> None:
        self.pending.pop(key, None)
        self.pending[key] = frame
        self.ready.set()

    def take(self) -> bytes:
        frames = b"".join(self.pending.values())
        self.pending.clear()
        self.ready.clear()
        return frames

    def close(self) -> None:
        self.closed = True
        self.ready.set()


class _Upstream:
    """One module's one upstream stream, for the union of its browser streams' topics."""

    def __init__(self, push: Push, module: str):
        self.push = push
        self.module = module
        self.wanted: frozenset[str] = frozenset()
        self.asking: frozenset[str] = frozenset()
        self.values: dict[str, bytes] = {}
        self.state: tuple[str, str | None] | None = None
        self.task: asyncio.Task[None] | None = None
        self.timer: asyncio.TimerHandle | None = None
        self.reroute = False

    # -- the union ----------------------------------------------------------------------

    def changed(self) -> None:
        """The union may have changed: apply it 50 ms after the last change."""
        if self.timer is not None:
            self.timer.cancel()
        self.timer = asyncio.get_running_loop().call_later(REOPEN_AFTER, self._apply)

    def _apply(self) -> None:
        self.timer = None
        union = self.push._union(self.module)
        reroute, self.reroute = self.reroute, False
        if union == self.wanted and not reroute:
            return
        for topic in self.wanted - union:
            self.values.pop(topic, None)
        self.wanted = union
        if not union:
            self.stop()
            self.state = None
            self.values.clear()
            return
        if self.task is None or self.task.done():
            self.task = asyncio.ensure_future(self._run(quiet=False))
        elif reroute or (self.asking and self.asking != union):
            # Open with another set of topics: close it and open again, quietly, since the
            # module stays up and each value it sends again is the one already held.
            old = self.task
            old.cancel()
            self.task = asyncio.ensure_future(self._run(quiet=True, after=old))

    def stop(self) -> None:
        if self.timer is not None:
            self.timer.cancel()
            self.timer = None
        if self.task is not None:
            self.task.cancel()

    # -- state and values -----------------------------------------------------------------

    def set_state(self, state: str, detail: str | None = None) -> None:
        if self.state == (state, detail):
            return
        self.state = (state, detail)
        self.push._fan_state(self.module, state, detail)

    def _event(self, name: str | None, data: bytes) -> None:
        if name is None or name not in self.wanted:
            return
        if self.values.get(name) == data:
            return
        self.values[name] = data
        self.push._fan(self.module, name, data)

    # -- the connection -------------------------------------------------------------------

    async def _run(self, *, quiet: bool, after: asyncio.Task[None] | None = None) -> None:
        if after is not None:
            # Never two upstreams for one module: the old one is closed first.
            await asyncio.wait({after})
        failures = 0
        try:
            while self.wanted:
                topics = self.wanted
                if not quiet and (self.state is None or self.state[0] != "down"):
                    self.set_state("starting")
                quiet = False
                route = self.push._routes.get(self.module)
                if route is None:
                    return
                target = f"{route[0]}?topics={','.join(sorted(topics))}"
                try:
                    response = await self.push.client.request(
                        self.module, "GET", target, headers=_ACCEPT, stream=True)
                except UpstreamError as err:
                    if err.status == 503 and err.body.get("error") == "stopping":
                        self.set_state("down", "the shell is stopping")
                        return
                    self.set_state("down", _detail(err))
                    await self._wait(failures)
                    failures += 1
                    continue
                async with response:
                    if response.status != 200:
                        self.set_state("down", "busy" if response.status == 503
                                       else f"answered {response.status}")
                    elif self.wanted != topics:
                        # The union changed while this one opened.
                        quiet = self.state is not None and self.state[0] == "up"
                        continue
                    else:
                        self.asking = topics
                        self.set_state("up")
                        up_at = time.monotonic()
                        ended = await self._read(response)
                        self.asking = frozenset()
                        if time.monotonic() - up_at >= STAYED_UP:
                            failures = 0
                        if ended == "cut":
                            # A drain for a new version: the next request waits for it,
                            # then starts the new server.
                            self.set_state("starting", "new version")
                            quiet = True
                            continue
                        self.set_state("down", ended)
                await self._wait(failures)
                failures += 1
        finally:
            self.asking = frozenset()

    async def _read(self, response: Any) -> str:
        """Forward the upstream's events until it ends. Returns how: `cut` for a drain,
        else the detail of the down state that follows."""
        parser = _Parser()
        try:
            async for chunk in response.chunks():
                for name, data in parser.feed(chunk):
                    self._event(name, data)
        except UpstreamBroken:
            return "stream lost"
        except ValueError as err:
            _log.warning("%s's stream: %s; it is opened again", self.module, err)
            return "event too large"
        return "cut" if response.cut else "stopped"

    async def _wait(self, failures: int) -> None:
        await asyncio.sleep(BACKOFF[min(failures, len(BACKOFF) - 1)])


def _detail(err: UpstreamError) -> str:
    body = err.body
    error = body.get("error")
    if error == "start":
        code = body.get("exit")
        return "start failed" if code is None else f"start failed (exit {code})"
    if error == "unreachable":
        return "unreachable"
    if error == "upstream":
        return "no answer"
    return str(error or err.status)


class Push:
    """The push channel as the router's ASGI app for `/m/ui/api/push`.

    `starters` is the shell's `Starters`, which the upstreams start servers through.
    `mounts` is each module's export, module to `export()`, as the export run gives them;
    `set_exports` replaces it after a snapshot refresh. `publish` sets one of the shell's
    own topics. All of it runs on the shell's loop, and `aclose()` ends every stream at
    the shell's stop.
    """

    def __init__(self, starters: Any, *, mounts: Mapping[str, Mapping[str, Any]] | None = None,
                 client: Client | None = None):
        self.starters = starters
        self.client = client or Client(starters)
        self.closing = False
        self._routes = stream_routes(mounts or {})
        self._streams: set[_Stream] = set()
        self._upstreams: dict[str, _Upstream] = {}
        self._own: dict[str, bytes] = {}
        self._servers_task: asyncio.Task[None] | None = None
        self._gateway = gateway_topics(mounts or {})
        self._own_shapes: dict[str, dict[str, Any]] | None = None

    @property
    def streams(self) -> int:
        """Browser streams open now."""
        return len(self._streams)

    def upstream(self, module: str) -> _Upstream | None:
        """`module`'s upstream, while some stream asks for one of its topics."""
        return self._upstreams.get(module)

    # -- seams for the shell ---------------------------------------------------------------

    def set_exports(self, mounts: Mapping[str, Mapping[str, Any]]) -> None:
        """Take a new snapshot's exports: from now on a module's topics are those its
        stream route lists here. An upstream whose route moved is opened again."""
        old, self._routes = self._routes, stream_routes(mounts)
        self._gateway = gateway_topics(mounts)
        for module, upstream in list(self._upstreams.items()):
            if old.get(module, ("",))[0] != self._routes.get(module, ("",))[0]:
                upstream.reroute = True
            upstream.changed()
        for stream in self._streams:
            for module in stream.by_module:
                self._tell_dropped(stream, module)
                if (module != OWN_MODULE and module not in self._upstreams
                        and self._exported(stream, module)):
                    self._upstream_for(module).changed()

    def publish(self, topic: str, value: Any) -> None:
        """Set the shell's own topic `ui.<topic>` (`servers` or `modules`) to `value`,
        a JSON-able value, and send it to every stream that asked for it."""
        if topic not in OWN_TOPICS:
            raise KeyError(topic)
        data = _json(value)
        if self._own.get(topic) == data:
            return
        self._own[topic] = data
        self._fan(OWN_MODULE, topic, data)

    def servers_value(self) -> dict[str, Any]:
        """`ui.servers`' value: the starter set's rows, without the fields that change
        every second (the `/servers` route has those)."""
        rows = [{key: value for key, value in row.items() if key not in _VOLATILE}
                for row in self.starters.rows()]
        return {"servers": rows}

    async def aclose(self) -> None:
        """End every browser stream and close every upstream. Safe to call twice."""
        self.closing = True
        for stream in list(self._streams):
            stream.close()
        tasks = []
        for upstream in self._upstreams.values():
            upstream.stop()
            if upstream.task is not None:
                tasks.append(upstream.task)
        if self._servers_task is not None:
            self._servers_task.cancel()
            tasks.append(self._servers_task)
        await asyncio.gather(*tasks, return_exceptions=True)

    # -- the browser's request --------------------------------------------------------------

    async def __call__(self, scope: dict[str, Any], receive: Callable[..., Any],
                       send: Callable[..., Any], *, key: Callable[[], bytes] | None = None
                       ) -> None:
        if scope.get("type") != "http":
            return
        policy = "page" if key is None else "gateway"
        try:
            asked = read_topics(scope.get("query_string") or b"")
        except BadTopics as err:
            await _answer(send, 400, {"error": str(err)}, policy=policy)
            return
        if self.closing:
            await _answer(send, 503, {"error": "stopping"}, policy=policy)
            return
        if len(self._streams) >= MAX_STREAMS:
            await _answer(send, 503, {"error": "busy"}, retry=BUSY_RETRY, policy=policy)
            return
        stream = _Stream(asked, key)
        self._join(stream)
        try:
            await send({"type": "http.response.start", "status": 200,
                        "headers": response_headers(extra=[("Content-Type", "text/event-stream")],
                                                    policy=policy)})
            writing = asyncio.ensure_future(self._write(stream, send))
            watching = asyncio.ensure_future(_disconnected(receive))
            try:
                await asyncio.wait({writing, watching}, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for task in (writing, watching):
                    task.cancel()
                for result in await asyncio.gather(writing, watching, return_exceptions=True):
                    if isinstance(result, Exception):
                        _log.error("a push stream failed", exc_info=result)
        finally:
            self._leave(stream)

    def gateway_app(self, key: Callable[[], bytes]) -> Callable[..., Any]:
        """The push channel as the gateway's router serves it: each stream's topics kept
        to what the gateway reaches, each event redacted under `key()`."""

        async def app(scope: dict[str, Any], receive: Callable[..., Any],
                      send: Callable[..., Any]) -> None:
            await self(scope, receive, send, key=key)

        return app

    async def _write(self, stream: _Stream, send: Callable[..., Any]) -> None:
        while not stream.closed:
            try:
                async with asyncio.timeout(HEARTBEAT):
                    await stream.ready.wait()
            except TimeoutError:
                await send({"type": "http.response.body", "body": b": heartbeat\n\n",
                            "more_body": True})
                continue
            frames = stream.take()
            if frames:
                await send({"type": "http.response.body", "body": frames, "more_body": True})
        await send({"type": "http.response.body", "body": b""})

    # -- joining and leaving -----------------------------------------------------------------

    def _exported(self, stream: _Stream, module: str) -> frozenset[str]:
        route = self._routes.get(module)
        if route is None:
            return frozenset()
        if stream.key is not None:
            return stream.by_module.get(module, frozenset()) & frozenset(
                self._gateway.get(module, ()))
        return stream.by_module.get(module, frozenset()) & route[1]

    def _shape(self, module: str, topic: str) -> dict[str, Any] | None:
        if module == OWN_MODULE:
            if self._own_shapes is None:
                self._own_shapes = own_shapes()
            return self._own_shapes.get(topic)
        return self._gateway.get(module, {}).get(topic)

    def _framed(self, stream: _Stream, module: str, topic: str, data: bytes,
                cache: dict[bool, bytes | None]) -> bytes | None:
        """The frame `stream` gets for `data`: as sent upstream on the desktop, redacted on
        the gateway (None when the event can't be read against its shape)."""
        phone = stream.key is not None
        if phone not in cache:
            cache[phone] = (self._redacted(stream.key, module, topic, data) if phone
                            else _frame(f"{module}.{topic}", data))
        return cache[phone]

    def _redacted(self, key: Callable[[], bytes] | None, module: str, topic: str,
                  data: bytes) -> bytes | None:
        assert key is not None
        try:
            value = json.loads(data)
            return _frame(f"{module}.{topic}",
                          _json(redact_value(value, self._shape(module, topic), key())))
        except (ValueError, OSError):
            _log.warning("%s.%s's event can't be redacted; the phone doesn't get it",
                         module, topic, exc_info=True)
            return None

    def _union(self, module: str) -> frozenset[str]:
        union: frozenset[str] = frozenset()
        for stream in self._streams:
            union |= self._exported(stream, module)
        return union

    def _upstream_for(self, module: str) -> _Upstream:
        upstream = self._upstreams.get(module)
        if upstream is None:
            upstream = self._upstreams[module] = _Upstream(self, module)
        return upstream

    def _join(self, stream: _Stream) -> None:
        self._streams.add(stream)
        for module, topics in stream.by_module.items():
            if module == OWN_MODULE:
                if "servers" in topics:
                    self.publish("servers", self.servers_value())
                    self._watch_servers()
                for topic in sorted(topics & OWN_TOPICS):
                    if topic in self._own:
                        self._put(stream, OWN_MODULE, topic, self._own[topic])
                self._tell_dropped(stream, module)
                continue
            exported = self._exported(stream, module)
            upstream = self._upstreams.get(module)
            if upstream is not None:
                for topic in sorted(exported):
                    if topic in upstream.values:
                        self._put(stream, module, topic, upstream.values[topic])
                if upstream.state is not None:
                    stream.put((STATE_EVENT, module), _state_frame(module, *upstream.state))
            self._tell_dropped(stream, module)
            if exported:
                self._upstream_for(module).changed()

    def _leave(self, stream: _Stream) -> None:
        self._streams.discard(stream)
        for module in stream.by_module:
            upstream = self._upstreams.get(module)
            if upstream is not None:
                upstream.changed()

    def _tell_dropped(self, stream: _Stream, module: str) -> None:
        """Say once which of the topics `stream` asked of `module` it won't get."""
        asked = stream.by_module.get(module, frozenset())
        known = OWN_TOPICS if module == OWN_MODULE else self._exported(stream, module)
        dropped = asked - known
        if dropped == stream.told.get(module, frozenset()):
            return
        stream.told[module] = dropped
        if not dropped:
            return
        if module == OWN_MODULE:
            state: tuple[str, str | None] = ("up", None)
        elif not known:
            state = ("down", None)
        else:
            upstream = self._upstreams.get(module)
            state = upstream.state if upstream and upstream.state else ("starting", None)
        stream.put((STATE_EVENT, module), _state_frame(module, state[0], _not_exported(dropped)))

    # -- fan-out ------------------------------------------------------------------------------

    def _put(self, stream: _Stream, module: str, topic: str, data: bytes) -> None:
        frame = self._framed(stream, module, topic, data, {})
        if frame is not None:
            stream.put((module, topic), frame)

    def _fan(self, module: str, topic: str, data: bytes) -> None:
        cache: dict[bool, bytes | None] = {}
        for stream in self._streams:
            if stream.asks(module, topic) and (
                    module == OWN_MODULE or topic in self._exported(stream, module)):
                frame = self._framed(stream, module, topic, data, cache)
                if frame is not None:
                    stream.put((module, topic), frame)

    def _fan_state(self, module: str, state: str, detail: str | None) -> None:
        frame = _state_frame(module, state, detail)
        for stream in self._streams:
            if self._exported(stream, module):
                stream.put((STATE_EVENT, module), frame)

    # -- ui.servers ---------------------------------------------------------------------------

    def _watch_servers(self) -> None:
        if self._servers_task is None or self._servers_task.done():
            self._servers_task = asyncio.ensure_future(self._servers())

    async def _servers(self) -> None:
        while any(stream.asks(OWN_MODULE, "servers") for stream in self._streams):
            self.publish("servers", self.servers_value())
            await asyncio.sleep(SERVERS_EVERY)


async def _disconnected(receive: Callable[..., Any]) -> None:
    while True:
        message = await receive()
        if message.get("type") == "http.disconnect":
            return


async def _answer(send: Callable[..., Any], status: int, value: Any, *,
                  retry: int | None = None, policy: str = "page") -> None:
    body = _json(value)
    extra = [("Content-Type", "application/json"), ("Content-Length", str(len(body)))]
    if retry is not None:
        extra.append(("Retry-After", str(retry)))
    await send({"type": "http.response.start", "status": status,
                "headers": response_headers(extra=extra, policy=policy)})
    await send({"type": "http.response.body", "body": body})

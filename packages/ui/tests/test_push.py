"""The push channel: first against a stand-in client that plays a module's stream route,
then through the shell's whole request path to real page servers, spawned over test-local
page modules in a site directory of the session's own.

Each test runs on one fresh loop and closes every stream and child it opened.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import socket
import textwrap
import threading
import time
from pathlib import Path

import pytest
from moxi.paths import Layout

from moxi_ui import listen
from moxi_ui import push as push_mod
from moxi_ui.proxy import UpstreamBroken, UpstreamError
from moxi_ui.push import BadTopics, Push, read_topics, stream_routes
from moxi_ui.router import shell_profile, shell_table
from moxi_ui.serve import Shell
from moxi_ui.starter import Starters, export_tables
from shell_kit import FETCH, Harness, scope


def _export(*topics: str, path: str = "/api/events") -> dict:
    return {"format": 1, "assets": [], "routes": [
        {"path": "/api/read", "kind": "read", "stream": False, "topics": None},
        {"path": path, "kind": "read", "stream": True,
         "topics": {name: {"type": "table"} for name in topics}},
    ]}


MOUNTS = {"fx": _export("one", "two", "three"), "plain": {"format": 1, "routes": [
    {"path": "/api/read", "kind": "read", "stream": False, "topics": None}], "assets": []}}


# --- a stand-in browser ------------------------------------------------------------------


class Reader:
    """One push request to an ASGI app, read as a browser would: each event and comment
    in order, until the answer ends."""

    def __init__(self, asgi, sc):
        self.asgi, self.scope = asgi, sc
        self.status = 0
        self.headers: list = []
        self.body = b""
        self.complete = False
        self.items: asyncio.Queue = asyncio.Queue()
        self._buffer = b""
        self._left = asyncio.Event()
        self._sent_request = False

    async def _receive(self):
        if not self._sent_request:
            self._sent_request = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await self._left.wait()
        return {"type": "http.disconnect"}

    async def _send(self, message):
        if message["type"] == "http.response.start":
            self.status, self.headers = message["status"], list(message["headers"])
            return
        chunk = message.get("body", b"")
        self.body += chunk
        self._buffer += chunk
        while b"\n\n" in self._buffer:
            block, self._buffer = self._buffer.split(b"\n\n", 1)
            if block.startswith(b":"):
                self.items.put_nowait(("comment", block[1:].strip().decode()))
                continue
            lines = dict(line.split(b": ", 1) for line in block.split(b"\n"))
            self.items.put_nowait((lines[b"event"].decode(), json.loads(lines[b"data"])))
        if not message.get("more_body"):
            self.complete = True
            self.items.put_nowait(None)

    def start(self) -> Reader:
        self.task = asyncio.ensure_future(self.asgi(self.scope, self._receive, self._send))
        return self

    def leave(self) -> None:
        self._left.set()

    async def next(self, timeout: float = 5.0):
        return await asyncio.wait_for(self.items.get(), timeout)

    async def until(self, name: str, test=lambda data: True, timeout: float = 10.0):
        """The next event named `name` that passes `test`; earlier ones are kept in
        `self.skipped`."""
        self.skipped = getattr(self, "skipped", [])
        deadline = time.monotonic() + timeout
        while True:
            item = await self.next(max(deadline - time.monotonic(), 0.001))
            assert item is not None, f"the stream ended before {name}"
            if item[0] == name and test(item[1]):
                return item[1]
            self.skipped.append(item)

    def drained(self) -> list:
        out = []
        while not self.items.empty():
            out.append(self.items.get_nowait())
        return out

    def header(self, name: str):
        found = [v.decode() for k, v in self.headers if k.lower() == name.encode()]
        return found[0] if len(found) == 1 else (found or None)


def ask(topics: str) -> dict:
    return scope("GET", "/m/ui/api/push", query=f"topics={topics}".encode())


def run(body, timeout: float = 30.0):
    asyncio.run(asyncio.wait_for(body(), timeout))


async def settle(seconds: float = 0.12) -> None:
    await asyncio.sleep(seconds)


# --- a stand-in client: one module's stream route ----------------------------------------


class FakeResponse:
    def __init__(self, module: str, topics: list[str], status: int = 200):
        self.module, self.topics, self.status = module, topics, status
        self.cut = False
        self.closed = False
        self.queue: asyncio.Queue = asyncio.Queue()

    def event(self, name: str, value) -> None:
        self.queue.put_nowait(b"event: %s\ndata: %s\n\n" % (name.encode(), json.dumps(value).encode()))

    def end(self, how: str = "end") -> None:
        self.queue.put_nowait(how)

    async def chunks(self):
        while True:
            item = await self.queue.get()
            if item == "end":
                return
            if item == "cut":
                self.cut = True
                return
            if item == "broken":
                raise UpstreamBroken("broke off")
            yield item

    async def aclose(self) -> None:
        self.closed = True

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self.aclose()


class FakeClient:
    """Plays every module's stream route: current values first, then each `publish`.
    `fail` is an `UpstreamError` to raise, or a status to answer, for the next requests."""

    def __init__(self):
        self.values: dict[str, dict[str, object]] = {}
        self.requests: list[tuple[str, str]] = []
        self.open: list[FakeResponse] = []
        self.fail: list = []
        self.delay = 0.0

    async def request(self, module, method, target, *, headers=(), stream=False, **kw):
        assert method == "GET" and stream and (b"accept", b"text/event-stream") in headers
        self.requests.append((module, target))
        await asyncio.sleep(self.delay)
        if self.fail:
            failure = self.fail.pop(0)
            if isinstance(failure, Exception):
                raise failure
            return FakeResponse(module, [], status=failure)
        topics = target.split("?topics=", 1)[1].split(",")
        response = FakeResponse(module, topics)
        for name in topics:
            if name in self.values.get(module, {}):
                response.event(name, self.values[module][name])
        self.open.append(response)
        return response

    def publish(self, module: str, name: str, value) -> None:
        self.values.setdefault(module, {})[name] = value
        for response in self.live():
            if response.module == module and name in response.topics:
                response.event(name, value)

    def live(self) -> list[FakeResponse]:
        return [response for response in self.open if not response.closed]


class FakeStarters:
    def __init__(self, rows=()):
        self.row_list = list(rows)

    def rows(self):
        return [dict(row) for row in self.row_list]


def fake_push(**kwargs):
    client = FakeClient()
    starters = FakeStarters(kwargs.pop("rows", ()))
    return Push(starters, mounts=kwargs.pop("mounts", MOUNTS), client=client, **kwargs), client


# --- the query ---------------------------------------------------------------------------


def test_read_topics_takes_full_names_and_refuses_the_rest_naming_only_the_key():
    assert read_topics(b"topics=fx.one,ui.servers,fx.two-b") == (
        ("fx", "one"), ("ui", "servers"), ("fx", "two-b"))
    for query, said in [
        (b"", "missing key: topics"),
        (b"topics=fx.one&topics=fx.two", "repeated key: topics"),
        (b"topics=fx.%6Fne", "a percent sign in the value: topics"),
        (b"topics=fx.one,,fx.two", "an empty topic name: topics"),
        (b"topics=fxone", "not a topic name: topics"),
        (b"topics=Fx.one", "not a topic name: topics"),
        (b"topics=fx.One", "not a topic name: topics"),
        (b"topics=fx.one,fx.one", "a topic named twice: topics"),
        (b"topics=fx.one&secret=1", "unknown key: secret"),
        (b"topics=fx.one&x%20y=1", "unknown key: (a key that is not a plain name)"),
        ("topics=fx.é".encode(), "the query string is not ASCII: topics"),
        (("topics=" + ",".join(f"fx.t{n}" for n in range(257))).encode(), "over 256 topics: topics"),
    ]:
        with pytest.raises(BadTopics) as refused:
            read_topics(query)
        assert str(refused.value) == said


def test_a_bad_query_is_a_400_and_opens_nothing():
    async def body():
        push, client = fake_push()
        reader = Reader(push, ask("fx.one,fx.one")).start()
        await reader.task
        assert reader.status == 400 and reader.complete
        assert json.loads(reader.body) == {"error": "a topic named twice: topics"}
        assert reader.header("x-content-type-options") == "nosniff"
        await settle()
        assert client.requests == [] and push.streams == 0

    run(body)


def test_stream_routes_come_from_the_exports_stream_route_only():
    assert stream_routes(MOUNTS) == {"fx": ("/api/events", frozenset({"one", "two", "three"}))}
    assert stream_routes({"ui": _export("x")}) == {}


# --- fan-in, against the stand-in client ---------------------------------------------------


def test_a_topic_the_export_does_not_list_is_dropped_before_the_upstream_is_asked():
    async def body():
        push, client = fake_push()
        client.publish("fx", "one", {"n": 1})
        reader = Reader(push, ask("fx.one,fx.nope,plain.x,gone.y")).start()
        first = await reader.next()
        assert reader.status == 200 and reader.header("content-type") == "text/event-stream"
        assert reader.header("cache-control") == "no-store"
        said = {first[1]["module"]: first[1]}
        for _ in range(2):
            name, data = await reader.next()
            assert name == "moxi.state"
            said[data["module"]] = data
        assert said == {
            "fx": {"module": "fx", "state": "starting", "detail": "not exported: nope"},
            "plain": {"module": "plain", "state": "down", "detail": "not exported: x"},
            "gone": {"module": "gone", "state": "down", "detail": "not exported: y"},
        }
        assert await reader.until("fx.one") == {"n": 1}
        assert client.requests == [("fx", "/api/events?topics=one")]
        assert set(push._upstreams) == {"fx"}
        reader.leave()
        await reader.task

    run(body)


def test_two_streams_make_one_upstream_with_the_union_and_a_late_joiner_gets_values_at_once():
    async def body():
        push, client = fake_push()
        client.publish("fx", "one", {"n": 1})
        client.publish("fx", "two", {"n": 2})
        first = Reader(push, ask("fx.one")).start()
        second = Reader(push, ask("fx.two")).start()
        assert await first.until("fx.one") == {"n": 1}
        assert await second.until("fx.two") == {"n": 2}
        assert client.requests == [("fx", "/api/events?topics=one,two")]
        assert len(client.live()) == 1

        late = Reader(push, ask("fx.one,fx.two")).start()
        got = [await late.next(timeout=0.04) for _ in range(3)]
        assert ("fx.one", {"n": 1}) in got and ("fx.two", {"n": 2}) in got
        assert ("moxi.state", {"module": "fx", "state": "up", "detail": None}) in got
        await settle()
        assert len(client.requests) == 1, "the union did not change, so nothing reopened"

        client.publish("fx", "one", {"n": 10})
        assert await first.until("fx.one") == {"n": 10}
        assert await late.until("fx.one") == {"n": 10}
        await settle()
        assert [item for item in second.drained() if item[0] == "fx.one"] == []
        for reader in (first, second, late):
            reader.leave()
            await reader.task
        await settle()
        assert client.live() == [], "with no stream left, the upstream closes"
        assert push.upstream("fx").state is None and push.upstream("fx").values == {}

    run(body)


def test_the_upstream_reopens_once_50_ms_after_the_union_last_changed():
    async def body():
        push, client = fake_push()
        readers = []
        for topics in ("fx.one", "fx.two", "fx.three"):
            readers.append(Reader(push, ask(topics)).start())
            await asyncio.sleep(0.02)
        assert client.requests == []
        await settle()
        assert client.requests == [("fx", "/api/events?topics=one,three,two")]

        readers[2].leave()
        await readers[2].task
        await asyncio.sleep(0.02)
        assert len(client.requests) == 1
        await settle()
        assert client.requests[-1] == ("fx", "/api/events?topics=one,two")
        assert len(client.live()) == 1 and client.open[0].closed
        states = [item for item in readers[0].drained() if item[0] == "moxi.state"]
        # A slow reader may see only the latest state; a reopen for a new union adds none.
        assert [data["state"] for _, data in states] in (["up"], ["starting", "up"])
        for reader in readers[:2]:
            reader.leave()
            await reader.task

    run(body)


def test_a_value_equal_to_the_last_is_not_sent_again():
    async def body():
        push, client = fake_push()
        client.publish("fx", "one", {"n": 1})
        reader = Reader(push, ask("fx.one")).start()
        assert await reader.until("fx.one") == {"n": 1}
        client.publish("fx", "one", {"n": 1})
        client.publish("fx", "one", {"n": 2})
        assert await reader.until("fx.one") == {"n": 2}
        assert [item for item in reader.skipped if item[0] == "fx.one"] == []
        reader.leave()
        await reader.task

    run(body)


def test_a_dropped_upstream_is_down_then_up_and_retried_after_1_2_5_10_then_30_s(monkeypatch):
    waits = []

    async def wait(self, failures):
        waits.append(push_mod.BACKOFF[min(failures, len(push_mod.BACKOFF) - 1)])
        await asyncio.sleep(0.02)  # Long enough for the stream to send each state.

    monkeypatch.setattr(push_mod._Upstream, "_wait", wait)

    async def body():
        push, client = fake_push()
        client.publish("fx", "one", {"n": 1})
        reader = Reader(push, ask("fx.one")).start()
        state = lambda want: lambda data: data["state"] == want
        await reader.until("moxi.state", state("up"))
        failed = UpstreamError(502, {"error": "start", "module": "fx", "exit": 3, "log": "x"})
        client.fail = [failed, failed, 503, UpstreamError(502, {"error": "unreachable"}),
                       failed, failed]
        client.live()[0].end("broken")
        assert await reader.until("moxi.state", state("down")) == {
            "module": "fx", "state": "down", "detail": "stream lost"}
        assert await reader.until("moxi.state", state("up")) == {
            "module": "fx", "state": "up", "detail": None}
        details = [data["detail"] for name, data in reader.skipped if name == "moxi.state"]
        assert details[-4:] == ["start failed (exit 3)", "busy", "unreachable",
                                "start failed (exit 3)"]
        assert waits == [1.0, 2.0, 5.0, 10.0, 30.0, 30.0, 30.0]
        assert "starting" not in [data["state"] for name, data in reader.skipped
                                  if name == "moxi.state"][2:], "retries stay down until up"

        waits.clear()
        client.live()[0].end("end")
        assert (await reader.until("moxi.state", state("down")))["detail"] == "stopped"
        await reader.until("moxi.state", state("up"))
        assert waits == [1.0], "a stream that came up resets the backoff"

        client.delay = 0.05  # As a drain holds the next start.
        client.live()[0].end("cut")
        assert await reader.until("moxi.state", state("starting")) == {
            "module": "fx", "state": "starting", "detail": "new version"}
        await reader.until("moxi.state", state("up"))
        assert waits == [1.0], "a drain's cut reconnects at once"
        reader.leave()
        await reader.task

    run(body)


def test_the_shell_stopping_ends_the_upstream():
    async def body():
        push, client = fake_push()
        client.fail = [UpstreamError(503, {"error": "stopping", "module": "fx"})]
        reader = Reader(push, ask("fx.one")).start()
        assert (await reader.until("moxi.state", lambda d: d["state"] == "down"))["detail"] == (
            "the shell is stopping")
        await settle()
        assert push.upstream("fx").task.done() and len(client.requests) == 1
        reader.leave()
        await reader.task

    run(body)


def test_past_16_streams_the_next_is_a_503_and_a_freed_slot_takes_one_again():
    async def body():
        push, _ = fake_push()
        readers = [Reader(push, ask("ui.modules")).start() for _ in range(16)]
        await settle(0.02)
        assert push.streams == 16
        extra = Reader(push, ask("ui.modules")).start()
        await extra.task
        assert extra.status == 503 and json.loads(extra.body) == {"error": "busy"}
        assert extra.header("retry-after") == "5"
        readers[0].leave()
        await readers[0].task
        again = Reader(push, ask("ui.modules")).start()
        await settle(0.02)
        assert again.status == 200 and push.streams == 16
        await push.aclose()
        for reader in [*readers[1:], again]:
            await reader.task
            assert reader.complete

    run(body)


def test_a_comment_heartbeat_goes_out_on_an_idle_stream(monkeypatch):
    monkeypatch.setattr(push_mod, "HEARTBEAT", 0.05)

    async def body():
        push, _ = fake_push()
        reader = Reader(push, ask("ui.modules")).start()
        assert await reader.next() == ("comment", "heartbeat")
        assert await reader.next() == ("comment", "heartbeat")
        await push.aclose()
        await reader.task

    run(body)


def test_the_shells_own_topics():
    rows = [{"module": "fx", "state": "running", "uptime_s": 1.5, "failed_for_s": None,
             "starts": 1}]

    async def body():
        push, client = fake_push(rows=rows)
        reader = Reader(push, ask("ui.servers,ui.modules,ui.nope")).start()
        got = [await reader.next(), await reader.next()]
        assert ("ui.servers", {"servers": [{"module": "fx", "state": "running", "starts": 1}]}
                ) in got
        assert ("moxi.state", {"module": "ui", "state": "up", "detail": "not exported: nope"}
                ) in got
        push.publish("modules", {"modules": ["fx"]})
        assert await reader.next() == ("ui.modules", {"modules": ["fx"]})
        push.starters.row_list[0]["state"] = "idle"
        push.starters.row_list[0]["uptime_s"] = None
        servers = await reader.until("ui.servers")
        assert servers["servers"][0]["state"] == "idle"
        late = Reader(push, ask("ui.modules")).start()
        assert await late.next() == ("ui.modules", {"modules": ["fx"]})
        with pytest.raises(KeyError):
            push.publish("nope", {})
        assert client.requests == [], "the shell's own topics open no upstream"
        await push.aclose()
        await reader.task
        await late.task
        assert reader.complete and late.complete
        assert push._servers_task.done()

    run(body)


def test_new_exports_drop_and_add_topics_and_a_moved_route_reopens():
    async def body():
        push, client = fake_push()
        reader = Reader(push, ask("fx.one,fx.four")).start()
        await reader.until("moxi.state", lambda d: d["state"] == "up")
        assert client.requests == [("fx", "/api/events?topics=one")]
        push.set_exports({"fx": _export("one", "four")})
        await settle()
        assert client.requests[-1] == ("fx", "/api/events?topics=four,one")
        push.set_exports({"fx": _export("one", "four", path="/api/stream")})
        await settle()
        assert client.requests[-1] == ("fx", "/api/stream?topics=four,one")
        assert len(client.live()) == 1
        push.set_exports({})
        got = await reader.until("moxi.state", lambda d: d["state"] == "down")
        assert got["detail"] == "not exported: four, one"
        await settle()
        assert client.live() == []
        reader.leave()
        await reader.task

    run(body)


def test_aclose_ends_every_stream_and_upstream_and_refuses_new_ones():
    async def body():
        push, client = fake_push()
        reader = Reader(push, ask("fx.one")).start()
        await reader.until("moxi.state", lambda d: d["state"] == "up")
        await push.aclose()
        await reader.task
        assert reader.complete and client.live() == []
        after = Reader(push, ask("fx.one")).start()
        await after.task
        assert after.status == 503

    run(body)


# --- through the shell to real page servers -------------------------------------------------


PAGE = '''
from moxi_loopback.routes import Field, Out, Page, Route

H = "moxi_NAME.handlers"
PAGE = Page(
    routes=(
        Route("/api/bump", "act", H + ":bump", body={"n": Field("int")},
              returns={"ok": Out("bool")}),
        Route("/api/events", "read", H + ":events", stream=True,
              topics={"alpha": Out("table", items={"n": Out("int")}),
                      "beta": Out("table", items={"n": Out("int")})}),
    ),
)
'''

HANDLERS = '''
from moxi_loopback.server import Topics

TOPICS = Topics("alpha", "beta")
TOPICS.publish("alpha", {"n": 0})
TOPICS.publish("beta", {"n": 100})


def bump(call):
    TOPICS.publish("alpha", {"n": call.body["n"]})
    return {"ok": True}


def events(call):
    return call.stream(TOPICS)
'''

ANNOUNCEMENT = {
    "INTERFACES": "frozenset({1})", "API": "None", "CLI": "None",
    "SUMMARY": "'A module made for a test.'", "OPTIONAL": "{}", "GATED": "{}",
    "INTERNAL_ENV": "{}", "SETTINGS": "None", "SETUP": "None", "SEGMENT": "None",
}
MODULES = ("pspage", "psbad")


def _module(root: Path, name: str, *, handlers: bool = True) -> None:
    package = root / f"moxi_{name}"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "page.py").write_text(textwrap.dedent(PAGE).replace("NAME", name))
    if handlers:
        (package / "handlers.py").write_text(HANDLERS)
    fields = {**ANNOUNCEMENT, "PAGE": repr(f"moxi_{name}.page:PAGE")}
    (package / "module.py").write_text("".join(f"{k} = {v}\n" for k, v in fields.items()))
    info = root / f"moxi_{name}-0.1.0.dist-info"
    info.mkdir()
    (info / "METADATA").write_text(f"Metadata-Version: 2.1\nName: moxi-{name}\nVersion: 0.1.0\n")
    (info / "entry_points.txt").write_text(f"[moxi.modules]\n{name} = moxi_{name}.module\n")


@pytest.fixture(scope="session")
def site(tmp_path_factory):
    root = tmp_path_factory.mktemp("push-site")
    _module(root, "pspage")
    _module(root, "psbad", handlers=False)  # its handlers don't import: exit 3
    return root


@pytest.fixture
def shell(site, tmp_path, monkeypatch):
    monkeypatch.setenv("PYTHONPATH", str(site))
    layout = Layout("ui")
    runtime_dir = layout.runtime_dir(create=True)
    mounts = export_tables(list(MODULES), cwd=runtime_dir).mounts
    assert set(mounts) == set(MODULES)
    return runtime_dir, layout.state_dir(), mounts


class Rig:
    """The shell's request path with its real profile, a real push over real starters,
    and one signed-in browser."""

    def __init__(self, shell, tmp_path):
        runtime_dir, state_dir, mounts = shell
        self.starters = Starters(runtime_dir=runtime_dir, state_dir=state_dir,
                                 log=state_dir / "logs" / "shell.log")
        self.push = Push(self.starters, mounts=mounts)
        self.harness = Harness(tmp_path / "state")
        self.harness.profile = shell_profile(shell_table(MODULES, mounts),
                                             self.harness.sessions, ("127.0.0.1", "localhost"))
        self.harness.router.push = self.push
        self.cookie, self.key = self.harness.sign_in()

    def reader(self, topics: str, *, key: bool = True) -> Reader:
        headers = [self.cookie, *([self.key] if key else []), *FETCH]
        return Reader(self.harness.asgi, scope("GET", "/m/ui/api/push", headers=headers,
                                               query=f"topics={topics}".encode()))

    def server(self, module: str = "pspage"):
        return self.starters.get(module).server


def drive(rig: Rig, body, timeout: float = 40.0) -> None:
    async def main():
        try:
            await body(rig)
        finally:
            await rig.push.aclose()
            await rig.starters.aclose()

    asyncio.run(asyncio.wait_for(main(), timeout))


def up(data) -> bool:
    return data["state"] == "up"


def test_the_push_needs_the_key(shell, tmp_path):
    rig = Rig(shell, tmp_path)

    async def body(rig):
        reader = rig.reader("ui.modules", key=False).start()
        await reader.task
        assert reader.status == 403 and rig.push.streams == 0

    drive(rig, body)


def test_two_topics_and_ui_servers_on_one_stream_current_value_first_down_then_up_on_a_kill(
        shell, tmp_path):
    rig = Rig(shell, tmp_path)

    async def body(rig):
        reader = rig.reader("pspage.alpha,pspage.beta,pspage.gamma,ui.servers").start()
        assert await reader.until("moxi.state") == {
            "module": "pspage", "state": "starting", "detail": "not exported: gamma"}
        assert await reader.until("moxi.state", up) == {
            "module": "pspage", "state": "up", "detail": None}
        assert await reader.until("pspage.alpha") == {"n": 0}
        assert await reader.until("pspage.beta") == {"n": 100}
        names = [name for name, _ in reader.skipped]
        assert names[0] == "ui.servers"
        server = rig.server()
        assert server.streams == 1, "one upstream, without the dropped topic (else a 400)"
        assert await reader.until("ui.servers", lambda d: any(
            row["module"] == "pspage" and row["state"] == "running" for row in d["servers"]))

        # A fixture act changes a topic, and the stream shows it.
        answer = await rig.push.client.request("pspage", "POST", "/api/bump", act=True,
                                               headers=[(b"content-type", b"application/json")],
                                               body=b'{"n":7}')
        async with answer:
            assert answer.status == 200 and json.loads(await answer.read()) == {"ok": True}
        assert await reader.until("pspage.alpha") == {"n": 7}

        os.kill(server.child.pid, signal.SIGKILL)
        assert await reader.until("moxi.state") == {
            "module": "pspage", "state": "down", "detail": "stream lost"}
        assert await reader.until("moxi.state", up) == {
            "module": "pspage", "state": "up", "detail": None}
        assert rig.server() is not server and rig.server().streams == 1
        # The new server's values are its own, and each replaces the last.
        assert await reader.until("pspage.alpha") == {"n": 0}
        reader.leave()
        await reader.task

    drive(rig, body)


def test_two_browser_streams_make_one_upstream_and_a_later_one_gets_the_last_values(
        shell, tmp_path):
    rig = Rig(shell, tmp_path)

    async def body(rig):
        first = rig.reader("pspage.alpha").start()
        second = rig.reader("pspage.beta").start()
        assert await first.until("pspage.alpha") == {"n": 0}
        assert await second.until("pspage.beta") == {"n": 100}
        assert rig.server().streams == 1
        assert rig.push.upstream("pspage").asking == {"alpha", "beta"}
        late = rig.reader("pspage.alpha,pspage.beta").start()
        got = {name: data for name, data in [await late.next(1.0) for _ in range(3)]}
        assert got == {"pspage.alpha": {"n": 0}, "pspage.beta": {"n": 100},
                       "moxi.state": {"module": "pspage", "state": "up", "detail": None}}
        await settle()
        assert rig.server().streams == 1
        for reader in (first, second, late):
            reader.leave()
            await reader.task
        await settle()
        assert rig.server().streams == 0, "the last stream gone closes the upstream"

    drive(rig, body)


def test_a_drain_restarts_the_upstream_on_the_new_server(shell, tmp_path):
    rig = Rig(shell, tmp_path)

    async def body(rig):
        reader = rig.reader("pspage.alpha").start()
        await reader.until("moxi.state", up)
        old = rig.server()
        drains = rig.starters.set_versions({"pspage": "0.2.0"})
        assert drains
        assert await reader.until("moxi.state") == {
            "module": "pspage", "state": "starting", "detail": "new version"}
        await reader.until("moxi.state", up)
        assert rig.server() is not old and old.gone
        reader.leave()
        await reader.task
        await asyncio.gather(*drains)

    drive(rig, body)


def test_a_module_that_fails_to_start_is_down_with_its_exit(shell, tmp_path):
    rig = Rig(shell, tmp_path)

    async def body(rig):
        reader = rig.reader("psbad.alpha").start()
        assert await reader.until("moxi.state", lambda d: d["state"] == "down") == {
            "module": "psbad", "state": "down", "detail": "start failed (exit 3)"}
        reader.leave()
        await reader.task

    drive(rig, body)


def test_the_shell_serves_the_push_on_its_listener_and_its_stop_ends_the_stream(shell):
    _, _, mounts = shell
    running = Shell(listen.bind(0), epoch=None, modules=MODULES, mounts=mounts)
    assert isinstance(running.push, Push) and running.push.starters is running.starters
    running.start()
    assert running.router.push is running.push
    thread = threading.Thread(target=lambda: asyncio.run(running.run()), daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 5
        while running._should_exit is None and time.monotonic() < deadline:
            time.sleep(0.01)
        session_id, key = running.sessions.store.mint()
        port = running.listeners.port
        head = (f"GET /m/ui/api/push?topics=pspage.alpha HTTP/1.1\r\n"
                f"Host: 127.0.0.1:{port}\r\nCookie: moxi-ui={session_id}\r\n"
                f"X-Moxi-Key: {key}\r\nSec-Fetch-Site: same-origin\r\n"
                "Sec-Fetch-Mode: cors\r\nSec-Fetch-Dest: empty\r\n\r\n")
        with socket.create_connection(("127.0.0.1", port), timeout=15) as sock:
            sock.sendall(head.encode())
            data = b""
            while b'data: {"n":0}' not in data:
                chunk = sock.recv(65536)
                assert chunk, data
                data += chunk
            assert data.startswith(b"HTTP/1.1 200")
            assert b"event: pspage.alpha\n" in data
            stopped = time.monotonic()
            running.stop()
            while not data.endswith(b"0\r\n\r\n"):
                chunk = sock.recv(65536)
                if not chunk:
                    break
                data += chunk
            assert data.endswith(b"0\r\n\r\n"), "the stream ended cleanly at the stop"
            assert time.monotonic() - stopped < 1.5
    finally:
        running.stop()
        thread.join(15)
        running.close()
    assert running.starters.children() == 0

"""turns' page server in process, through loopback's strict app, against fake endpoints; and
turns without the UI.

Loopback is reached as the page reaches it, through `moxi.modules.load`: turns'
distribution doesn't require it, so these tests don't import it either. The app runs behind
`guard` with an upstream profile, as the runner serves it, with strict projection, so a
handler that sends a key or a type its route doesn't declare fails here.

The endpoints are the fake harness's (`make_endpoint`) and, where one must misbehave, a
`moxi.ipc` server that speaks turns' hello and answers as the test says. Every one lives in
the test's own runtime directory; no live session is read.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import json
import os
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest

from fake_harness import FakeHarness
import moxi_turns as turns
from moxi import ipc, modules
from moxi_turns import client, page, pagehandlers, pagereader, protocol
from moxi_turns.results import Agent

pytestmark = pytest.mark.skipif(sys.platform == "win32",
                                reason="moxi.ipc's named pipe server comes later")

ROOT = Path(__file__).resolve().parents[1]
TOKEN = "T" * 43
SERVER_ID = "0123456789abcdef0123456789abcdef"
PORT = 4321

#: How long a wait gives something that must happen: a stream event, a read landing, a
#: session dropped. The test proves that it happens, not how fast, so the bound is long; it
#: costs time only when a test is failing. A wait for something that must not happen keeps
#: its own short bound.
LIVE_S = 30.0

SESSION_KEYS = ["key", "harness", "pid", "session_id", "cwd", "started", "capabilities",
                "endpoint_version", "mod_version", "trouble", "facts", "agents",
                "read_error", "ended_at", "has_box", "unread_since"]


#: The reader's own reach, before `fresh_reader` puts a stand-in in its place.
OWN_REACH = pagereader._claude_reach


def loopback():
    found = modules.load("ui", interface=3)
    assert found.ok, found.reason
    return found.api.loopback


@pytest.fixture(autouse=True)
def fresh_reader(monkeypatch, turns_env):
    """Each test starts its own reader in its own runtime directory, and stops it. Its
    reach is a stand-in: only the reach's own tests read it through `claude_setup`."""
    monkeypatch.setattr(pagereader, "_claude_reach", lambda: "start")
    monkeypatch.setattr(pagehandlers, "_reader_now", None)
    monkeypatch.setattr(pagehandlers, "_feed_now", None)
    yield
    reader = pagehandlers._reader_now
    if reader is not None:
        reader.stop()


# --- a misbehaving endpoint -----------------------------------------------------------------

class Odd:
    """A `moxi.ipc` server that speaks turns' hello and answers `session` with `info`, found
    by `sessions()` through a `hello.json` of its own. `answers[method]` is called with the
    params and returns the answer; `held` holds every method in `hold` until it is set. With
    `hold_hello` the server's loop stalls, so not even its hello answers."""

    def __init__(self, name: str, info: dict, *, answers=None, hold=(), hold_hello=False,
                 capabilities=("deliver",)):
        self.name = name
        self.address = ipc.address("turns", name)
        self.info = info
        self.answers = dict(answers or {})
        self.hold = frozenset(hold)
        self.held = threading.Event()
        self.capabilities = tuple(capabilities)
        self.dir = protocol.v2_root(client.runtime_dir()) / name
        self._ready: concurrent.futures.Future = concurrent.futures.Future()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self._ready.result(LIVE_S)
        self.dir.mkdir(parents=True, exist_ok=True)
        hello = {"address": self.address, "protocol": 2, "started": info.get("started", 0)}
        (self.dir / protocol.HELLO).write_text(json.dumps(hello))
        if hold_hello:
            self.loop.call_soon_threadsafe(self.held.wait, 30)

    def _run(self):
        from moxi import aio

        async def main():
            async def handler(conn, method, params):
                if method == "session":
                    return self.info
                if method in self.hold:
                    await asyncio.to_thread(self.held.wait, 30)
                fn = self.answers.get(method)
                if fn is None:
                    raise ipc.RpcError(-32601, f"no {method}")
                return fn(params)

            self.server = await ipc.serve(self.address, handler,
                                          hello=ipc.Hello((2, 2), self.capabilities))
            self.loop = asyncio.get_running_loop()
            self._ready.set_result(None)
            await self.server.serve_forever()

        aio.run(main)

    def stop(self):
        self.held.set()
        if self._thread.is_alive():
            with contextlib.suppress(RuntimeError):
                self.loop.call_soon_threadsafe(self.server.close)
            self._thread.join(5)


@pytest.fixture
def odd():
    made = []

    def make(name, info, **kw):
        server = Odd(name, info, **kw)
        made.append(server)
        return server

    yield make
    for server in made:
        server.stop()


def info(**over) -> dict:
    out = {"harness": "claude", "pid": os.getpid(), "endpoint_pid": os.getpid(),
           "session_id": "s-odd", "earlier": [], "cwd": "/somewhere", "nonce": "odd",
           "started": time.time(), "endpoint_version": "0.2.0", "mod_version": None}
    out.update(over)
    return out


def facts_wire(**over) -> dict:
    out = {"kind": "facts", "session_id": "s-odd", "turn_number": 0, "turn_open": False,
           "turn": None, "agents": 0, "draft": False, "at": time.time(), "stale": False,
           "ready": None}
    out.update(over)
    return out


# --- driving the app ---------------------------------------------------------------------

def served():
    lb = loopback()
    inner = lb.app(page.PAGE, module="turns", server_id=SERVER_ID, strict=True)
    profile = lb.upstream(lb.build_table(page.PAGE)[0], token=TOKEN, port=PORT)
    return lb.guard(inner, lambda: profile)


def scope(path: str, query: bytes = b"") -> dict:
    return {
        "type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1", "server": ("127.0.0.1", PORT),
        "client": ("127.0.0.1", 50000), "scheme": "http", "method": "GET",
        "root_path": "", "path": path, "raw_path": path.encode("ascii"),
        "query_string": query,
        "headers": [(b"host", b"127.0.0.1:%d" % PORT), (b"x-moxi-token", TOKEN.encode())],
        "state": {},
    }


async def get(app, path: str = "/api/sessions") -> tuple[int, dict | None]:
    sent = {"status": 0, "body": b""}
    asked = [False]

    async def receive():
        if not asked[0]:
            asked[0] = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await asyncio.sleep(3600)

    async def send(message):
        if message["type"] == "http.response.start":
            sent["status"] = message["status"]
        elif message["type"] == "http.response.body":
            sent["body"] += message.get("body", b"")

    await app(scope(path), receive, send)
    return sent["status"], json.loads(sent["body"]) if sent["body"] else None


class Stream:
    """One open stream on the app: its events as `(name, data)`, heartbeats left out."""

    def __init__(self, app, query: bytes = b"topics=sessions"):
        self.status = 0
        self._gone = asyncio.Event()
        self._chunks: asyncio.Queue[bytes] = asyncio.Queue()
        self._buffer = b""
        first = [True]

        async def receive():
            if first[0]:
                first[0] = False
                return {"type": "http.request", "body": b"", "more_body": False}
            await self._gone.wait()
            return {"type": "http.disconnect"}

        async def send(message):
            if message["type"] == "http.response.start":
                self.status = message["status"]
            elif message["type"] == "http.response.body":
                await self._chunks.put(message.get("body", b""))

        self.task = asyncio.get_running_loop().create_task(
            app(scope("/api/events", query), receive, send))

    async def next(self, timeout: float) -> tuple[str, dict]:
        async with asyncio.timeout(timeout):
            while True:
                while b"\n\n" not in self._buffer:
                    self._buffer += await self._chunks.get()
                block, _, self._buffer = self._buffer.partition(b"\n\n")
                if block.startswith(b":"):
                    continue
                fields = dict(line.split(": ", 1) for line in block.decode().split("\n"))
                return fields["event"], json.loads(fields["data"])

    async def until(self, check, timeout: float) -> dict:
        """The first value that passes `check`, within `timeout` seconds."""
        async with asyncio.timeout(timeout):
            while True:
                name, data = await self.next(timeout)
                assert name == "sessions"
                if check(data):
                    return data

    async def close(self) -> None:
        self._gone.set()
        async with asyncio.timeout(LIVE_S):
            await self.task


def run(coro):
    return asyncio.run(coro)


def keys(data: dict) -> list[str]:
    return [row["key"] for row in data["sessions"]]


def row_of(data: dict, key: str) -> dict | None:
    return next((row for row in data["sessions"] if row["key"] == key), None)


def watched(*endpoints, timeout: float = LIVE_S) -> None:
    """Wait until each fake endpoint has a watcher: the reader's watch of it."""
    deadline = time.monotonic() + timeout
    while not all(ep._watchers for ep in endpoints):
        assert time.monotonic() < deadline, "the reader's watch never started"
        time.sleep(0.02)


class Dirty(threading.Event):
    """A held session's dirty flag that counts. `sets` is how many times it was set; `read`
    is how many of those sets a finished read pass covers. The reads thread clears the flag
    just before a pass, so the pass covers every set made before the clear, and waits on it
    just after the pass, once the pass's reads have reported."""

    def __init__(self):
        super().__init__()
        self._count_lock = threading.Lock()
        self._sets = 0
        self._taken = 0
        self._read = 0

    def set(self):
        with self._count_lock:
            self._sets += 1
            super().set()

    def clear(self):
        with self._count_lock:
            self._taken = self._sets
            super().clear()

    def wait(self, timeout=None):
        with self._count_lock:
            self._read = self._taken
        return super().wait(timeout)

    def counts(self) -> tuple[int, int]:
        """`(sets, read)`, taken together."""
        with self._count_lock:
            return self._sets, self._read


def counted_reads(monkeypatch) -> list:
    """Give each session the reader holds from now on a counting dirty flag; returns the
    list each new held session joins."""
    made = []

    class Counted(pagereader._Held):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            # The threads start later, in `start()`; this is the first set, as the base's.
            self.dirty = Dirty()
            self.dirty.set()
            made.append(self)

    monkeypatch.setattr(pagereader, "_Held", Counted)
    return made


def read_through(made: list, count: int, timeout: float = LIVE_S) -> None:
    """Wait until `count` sessions are held, each has opened its watch, and every read each
    started has reported. A held session's flag is set once when it is made and once when
    its watch opens, after the watch is in place, so two sets covered by a finished pass
    means the read the watch's opening asked for has reported, whether it was a pass of its
    own or the first pass began after it."""
    deadline = time.monotonic() + timeout
    while True:
        seen = [held.dirty.counts() for held in made]
        if len(seen) == count and all(sets >= 2 and read == sets for sets, read in seen):
            return
        assert time.monotonic() < deadline, f"the reads never settled: (sets, read) {seen}"
        time.sleep(0.02)


def turn(data: dict, key: str) -> tuple | None:
    row = row_of(data, key)
    if row is None or row["facts"] is None:
        return None
    return row["facts"]["turn_number"], row["facts"]["turn_open"]


# --- the table ----------------------------------------------------------------------------

def test_the_gate_matrix_passes():
    assert loopback().gate_matrix(page.PAGE)


def test_the_page_has_one_read_and_one_stream_and_no_act():
    exported = loopback().export(page.PAGE)
    routes = {route["path"]: route for route in exported["routes"]}
    assert list(routes) == ["/api/sessions", "/api/events"]
    assert all(r["kind"] == "read" and not r["gateway"] for r in routes.values())
    assert not routes["/api/sessions"]["stream"]
    assert routes["/api/events"]["stream"]
    assert list(routes["/api/events"]["topics"]) == ["sessions"]
    assert list(routes["/api/sessions"]["returns"]) == ["now", "listed", "detail", "home",
                                                        "reach", "sessions"]
    assert routes["/api/events"]["topics"]["sessions"]["items"] == {
        key: value for key, value in routes["/api/sessions"]["returns"].items()
        if key != "now"}
    assert exported["budget"] == page.BUDGET
    assert page.PAGE.idle_s == 300 and page.PAGE.health is None


def test_each_session_carries_the_declared_fields_and_cwd_is_a_path():
    assert list(page.SESSION.items) == SESSION_KEYS
    assert page.SESSION.items["cwd"].mark == "path"
    marked = {key for key, out in page.SESSION.items.items() if out.mark is not None}
    assert marked == {"cwd", "trouble"}
    assert page.LIST["home"].mark == "path"
    assert list(page.FACTS.items) == ["session_id", "turn_number", "turn_open",
                                      "turn_started_at", "agents", "draft", "stale",
                                      "ready", "at"]
    assert list(page.READY.items) == ["source", "session_id", "at"]
    assert list(page.AGENTS.items) == ["entries", "complete"]
    assert list(page.AGENT.items) == ["id", "kind", "state", "final", "description",
                                      "parent_id"]
    assert page.AGENT.items["description"].mark == "redact"
    # No address, nonce or endpoint pid: the key names the endpoint, and nothing else
    # that leads to its socket is sent.
    assert not set(page.SESSION.items) & {"address", "dir", "nonce", "endpoint_pid", "earlier"}


# --- the read ----------------------------------------------------------------------------

def test_the_first_read_lists_the_session_with_its_facts_and_agents(make_endpoint):
    fake = FakeHarness()
    fake.entries = [Agent("a1", "subagent", "running", final=False, description="look around", parent_id=None),
                    Agent("a2", "subagent", "done", final=True, description=None, parent_id="a1")]
    ep, _ = make_endpoint(fake, session_id="s-1")
    ep.ready("startup", "s-1")

    async def go():
        return await get(served())

    status, data = run(go())
    assert status == 200
    assert data["listed"] is True and data["detail"] is None
    assert isinstance(data["now"], float)
    [row] = data["sessions"]
    assert list(row) == SESSION_KEYS
    assert row["key"] == ep.dir.name == f"fake-{ep.nonce}"
    assert row["harness"] == "fake" and row["pid"] == os.getpid()
    assert row["session_id"] == "s-1" and row["cwd"] == fake.cwd
    assert row["started"] == pytest.approx(ep.started_at)
    assert row["capabilities"] == sorted(row["capabilities"])
    assert {"deliver", "agents", "steer"} <= set(row["capabilities"])
    assert row["endpoint_version"] == ep.version and row["mod_version"] is None
    assert row["trouble"] is None and row["read_error"] is None
    assert row["ended_at"] is None and row["has_box"] is False
    assert row["unread_since"] is None
    facts = row["facts"]
    assert facts["session_id"] == "s-1"
    assert facts["turn_number"] == 0 and facts["turn_open"] is False
    assert facts["turn_started_at"] is None
    assert facts["agents"] == 1 and facts["draft"] is False and facts["stale"] is False
    assert facts["ready"]["source"] == "startup" and facts["ready"]["session_id"] == "s-1"
    assert isinstance(facts["ready"]["at"], float)
    assert isinstance(facts["at"], float)
    assert row["agents"] == {"complete": True, "entries": [
        {"id": "a1", "kind": "subagent", "state": "running", "final": False,
         "description": "look around", "parent_id": None},
        {"id": "a2", "kind": "subagent", "state": "done", "final": True,
         "description": None, "parent_id": "a1"}]}


def test_no_session_reads_as_listed_and_empty():
    status, data = run(get(served()))
    assert status == 200
    assert {k: v for k, v in data.items() if k not in ("now", "reach")} == {
        "listed": True, "detail": None, "home": os.path.expanduser("~"), "sessions": []}


def test_sessions_are_newest_first(make_endpoint):
    first, _ = make_endpoint(session_id="s-1")
    time.sleep(0.01)
    second, _ = make_endpoint(session_id="s-2")
    _, data = run(get(served()))
    assert keys(data) == [second.dir.name, first.dir.name]


def test_a_session_without_the_agents_capability_has_agents_null_and_no_error(make_endpoint):
    ep, _ = make_endpoint(FakeHarness(can=("deliver",)))
    _, data = run(get(served()))
    [row] = data["sessions"]
    assert row["agents"] is None and row["read_error"] is None
    assert row["facts"] is not None and "agents" not in row["capabilities"]


# --- the stream --------------------------------------------------------------------------

def test_a_new_endpoint_appears_within_one_rescan(make_endpoint):
    a, _ = make_endpoint(session_id="s-1")

    async def go():
        stream = Stream(served())
        try:
            name, data = await stream.next(LIVE_S)
            assert name == "sessions" and keys(data) == [a.dir.name]
            assert stream.status == 200
            b, _ = make_endpoint(session_id="s-2")
            began = time.monotonic()
            data = await stream.until(lambda d: b.dir.name in keys(d), LIVE_S)
            assert time.monotonic() - began <= pagereader.RESCAN_S + 1.5
            assert set(keys(data)) == {a.dir.name, b.dir.name}
        finally:
            await stream.close()

    run(go())


def test_session_gone_drops_it_and_a_later_scan_does_not_bring_it_back(make_endpoint,
                                                                         monkeypatch):
    a, _ = make_endpoint(session_id="s-1")
    b, _ = make_endpoint(session_id="s-2")
    made = counted_reads(monkeypatch)

    async def go():
        stream = Stream(served())
        try:
            # Every read of both sessions has reported before b stops: the first reads and
            # the facts read each watch's opening asks for. Each facts read carries a new
            # `at`, so one reporting after the stop would publish again.
            await stream.until(lambda d: len(d["sessions"]) == 2 and all(
                row["facts"] is not None and row["agents"] is not None
                for row in d["sessions"]), LIVE_S)
            await asyncio.to_thread(read_through, made, 2)
            b.stop("the session ended")
            data = await stream.until(lambda d: b.dir.name not in keys(d), LIVE_S)
            assert keys(data) == [a.dir.name]
            # Two scans later it is still gone, and nothing else was published.
            with pytest.raises(TimeoutError):
                await stream.next(2 * pagereader.RESCAN_S + 0.5)
        finally:
            await stream.close()

    run(go())


def test_a_turn_start_and_end_update_turn_open_and_turn_number(make_endpoint):
    ep, fake = make_endpoint(session_id="s-1")
    key = ep.dir.name

    async def go():
        stream = Stream(served())
        try:
            await stream.until(lambda d: turn(d, key) == (0, False), LIVE_S)
            fake.start_turn()
            began = time.monotonic()
            await stream.until(lambda d: turn(d, key) == (1, True), LIVE_S)
            fake.end_turn()
            await stream.until(lambda d: turn(d, key) == (1, False), LIVE_S)
            fake.start_turn()
            await stream.until(lambda d: turn(d, key) == (2, True), LIVE_S)
            assert time.monotonic() - began < 4
        finally:
            await stream.close()

    run(go())


def test_a_second_stream_gets_the_current_value_first(make_endpoint):
    ep, fake = make_endpoint(session_id="s-1")
    key = ep.dir.name
    fake.start_turn()

    async def go():
        app = served()
        first = Stream(app)
        try:
            await first.until(lambda d: turn(d, key) == (1, True), LIVE_S)
            second = Stream(app)
            try:
                _, data = await second.next(LIVE_S)
                assert turn(data, key) == (1, True)
            finally:
                await second.close()
        finally:
            await first.close()

    run(go())


def test_trouble_passes_through_and_follows_the_endpoint(make_endpoint):
    fake = FakeHarness()
    fake.trouble = "the host is loading the thread"
    ep, _ = make_endpoint(fake, session_id="s-1")
    key = ep.dir.name

    async def go():
        stream = Stream(served())
        try:
            data = await stream.until(lambda d: row_of(d, key) is not None, LIVE_S)
            assert row_of(data, key)["trouble"] == "the host is loading the thread"
            fake.trouble = None
            data = await stream.until(lambda d: row_of(d, key)["trouble"] is None, LIVE_S)
            assert row_of(data, key)["read_error"] is None
        finally:
            await stream.close()

    run(go())


# --- the turn's times ------------------------------------------------------------------------

def facts_of(data: dict, key: str) -> dict:
    row = row_of(data, key)
    return {} if row is None or row["facts"] is None else row["facts"]


def test_turn_started_at_is_the_open_turns_start_and_null_once_it_ends(make_endpoint):
    ep, fake = make_endpoint(session_id="s-1")
    key = ep.dir.name

    async def go():
        stream = Stream(served())
        try:
            await stream.until(lambda d: turn(d, key) == (0, False), LIVE_S)
            before = time.time()
            fake.start_turn()
            data = await stream.until(lambda d: turn(d, key) == (1, True), LIVE_S)
            started = facts_of(data, key)["turn_started_at"]
            assert before <= started <= time.time()
            fake.end_turn()
            data = await stream.until(lambda d: turn(d, key) == (1, False), LIVE_S)
            assert facts_of(data, key)["turn_started_at"] is None
            # A turn held for its tie is open with no turn named yet: no start is sent.
            ep.turn_started("t-held", None, held=True)
            # A held start raises no event; the read is asked for as the timer would.
            pagehandlers._reader_now._held[key].dirty.set()
            data = await stream.until(lambda d: turn(d, key) == (2, True), LIVE_S)
            assert facts_of(data, key)["turn_started_at"] is None
            ep.turn_released("t-held")
            data = await stream.until(
                lambda d: facts_of(d, key).get("turn_started_at") is not None, LIVE_S)
            assert turn(data, key) == (2, True)
        finally:
            await stream.close()

    run(go())


def test_ended_at_is_sent_while_the_reading_is_at_the_turn_that_ended(make_endpoint):
    ep, fake = make_endpoint(session_id="s-1")
    key = ep.dir.name

    async def go():
        stream = Stream(served())
        try:
            data = await stream.until(lambda d: turn(d, key) == (0, False), LIVE_S)
            assert row_of(data, key)["ended_at"] is None
            await asyncio.to_thread(watched, ep)
            fake.start_turn()
            await stream.until(lambda d: turn(d, key) == (1, True), LIVE_S)
            before = time.time()
            fake.end_turn()
            data = await stream.until(lambda d: row_of(d, key)["ended_at"] is not None,
                                      LIVE_S)
            assert before <= row_of(data, key)["ended_at"] <= time.time()
            assert turn(data, key) == (1, False)
            # A later turn open: the end no longer holds.
            fake.start_turn()
            data = await stream.until(lambda d: turn(d, key) == (2, True), LIVE_S)
            assert row_of(data, key)["ended_at"] is None
            fake.end_turn()
            await stream.until(lambda d: turn(d, key) == (2, False)
                               and row_of(d, key)["ended_at"] is not None, LIVE_S)
            # A new session id starts its count again: the end of the old id's turn is
            # not this id's.
            ep.ready("clear", "s-2")
            data = await stream.until(lambda d: facts_of(d, key).get("session_id") == "s-2",
                                      LIVE_S)
            assert row_of(data, key)["ended_at"] is None
        finally:
            await stream.close()

    run(go())


def test_a_session_first_seen_idle_has_no_ended_at(make_endpoint):
    ep, fake = make_endpoint(session_id="s-1")
    fake.start_turn()
    fake.end_turn()
    _, data = run(get(served()))
    assert turn(data, ep.dir.name) == (1, False)
    assert row_of(data, ep.dir.name)["ended_at"] is None


def test_a_turn_that_ended_while_no_watch_was_held_has_no_ended_at(make_endpoint,
                                                                    monkeypatch):
    def no_watch(self):
        raise turns.TurnsError("the watch cannot start")

    monkeypatch.setattr(client.Session, "watch", no_watch)
    ep, fake = make_endpoint(session_id="s-1")
    key = ep.dir.name
    run(get(served()))
    held = pagehandlers._reader_now._held[key]
    fake.start_turn()
    fake.end_turn()
    held.dirty.set()

    async def go():
        stream = Stream(served())
        try:
            data = await stream.until(lambda d: turn(d, key) == (1, False), LIVE_S)
            assert row_of(data, key)["ended_at"] is None
        finally:
            await stream.close()

    run(go())


def test_ended_at_waits_for_a_reading_that_answers(make_endpoint, monkeypatch):
    ep, fake = make_endpoint(session_id="s-1")
    key = ep.dir.name
    failing = threading.Event()
    real = client.Session.facts

    def facts(self):
        if failing.is_set():
            raise turns.TurnsError("the endpoint did not answer facts")
        return real(self)

    monkeypatch.setattr(client.Session, "facts", facts)
    made = counted_reads(monkeypatch)

    async def go():
        stream = Stream(served())
        try:
            await stream.until(lambda d: turn(d, key) == (0, False), LIVE_S)
            # Every read asked for so far has reported, so the one the turn's start asks
            # for is the last before the reads fail.
            await asyncio.to_thread(read_through, made, 1)
            fake.start_turn()
            await stream.until(lambda d: turn(d, key) == (1, True), LIVE_S)
            failing.set()
            fake.end_turn()
            # The read the end asked for fails: the row keeps its last reading, a turn
            # open, so no end is sent.
            data = await stream.until(lambda d: row_of(d, key)["read_error"] is not None,
                                      LIVE_S)
            assert turn(data, key) == (1, True) and row_of(data, key)["ended_at"] is None
            assert pagehandlers._reader_now._held[key].ended is not None
            failing.clear()
            pagehandlers._reader_now._held[key].dirty.set()
            data = await stream.until(lambda d: turn(d, key) == (1, False), LIVE_S)
            assert row_of(data, key)["ended_at"] is not None
        finally:
            await stream.close()

    run(go())


def test_ended_at_needs_the_same_session_id_and_turn():
    facts = {"session_id": "s-1", "turn_number": 3, "turn_open": False}
    assert pagereader.ended_at((5.0, "s-1", 3), facts) == 5.0
    assert pagereader.ended_at(None, facts) is None
    assert pagereader.ended_at((5.0, "s-1", 3), None) is None
    assert pagereader.ended_at((5.0, "s-1", 3), {**facts, "turn_open": True}) is None
    assert pagereader.ended_at((5.0, "s-1", 2), facts) is None
    assert pagereader.ended_at((5.0, "s-2", 3), facts) is None
    # A null id never matches another null.
    assert pagereader.ended_at((5.0, None, 3), {**facts, "session_id": None}) is None


# --- the prompt box ----------------------------------------------------------------------

@pytest.mark.parametrize(("harness", "has_box"), [
    ("claude", True), ("codex", False), ("opencode", False), ("", False)])
def test_has_box_is_true_for_claude_alone(odd, harness, has_box):
    odd("odd-box", info(harness=harness),
        answers={"watch": lambda p: {"facts": facts_wire()},
                 "facts": lambda p: facts_wire()})
    _, data = run(get(served()))
    [row] = data["sessions"]
    assert row["has_box"] is has_box


def test_the_codex_and_opencode_adapters_never_say_draft():
    """`has_box` leaves these out because their `live()` is always `draft` false."""
    from moxi_turns.codex import CodexHarness
    from moxi_turns.opencode import OpenCodeHarness
    from moxi_turns.results import Agents

    codex = object.__new__(CodexHarness)
    busy = Agent("a1", "subagent", "running", final=False)
    for read in (([busy], True, []), ([busy], False, []), ([busy], True, None), ([], True, [])):
        codex._read_agents = lambda read=read: read
        assert codex.live().draft is False

    opencode = object.__new__(OpenCodeHarness)
    for agents in (Agents((busy,), complete=True, at=0.0), Agents((), complete=False, at=0.0)):
        opencode.agents = lambda agents=agents: agents
        assert opencode.live().draft is False


@pytest.mark.parametrize(("setup", "draft", "agents"), [
    ({"draft": True}, True, 0),                             # a draft, the list read
    ({"box_fails": True}, True, 0),                         # the box read throws
    ({"agents_fail": True}, False, None),                   # the box empty, no list
    ({"agents_fail": True, "draft": True}, True, None),     # a draft, no list
    ({"live_fails": True}, True, None),                     # the live read failed
])
def test_the_draft_cases_on_the_wire(make_endpoint, setup, draft, agents):
    fake = FakeHarness()
    for name, value in setup.items():
        setattr(fake, name, value)
    ep, _ = make_endpoint(fake, session_id="s-1")
    _, data = run(get(served()))
    facts = facts_of(data, ep.dir.name)
    assert (facts["draft"], facts["agents"]) == (draft, agents)


# --- the reach --------------------------------------------------------------------------------

class Reaches:
    """A reach stand-in that answers `answer` and counts its reads; with `hold` set it waits
    for `go` first."""

    def __init__(self, answer="setup", *, hold=False):
        self.answer = answer
        self.calls = 0
        self.go = threading.Event()
        if not hold:
            self.go.set()

    def __call__(self):
        self.go.wait(LIVE_S)
        self.calls += 1
        return self.answer


def until(check, timeout=LIVE_S):
    deadline = time.monotonic() + timeout
    while not check():
        assert time.monotonic() < deadline, "it never came"
        time.sleep(0.02)


@pytest.fixture
def reader():
    made = []

    def make(reach, **kw):
        one = pagereader.Reader(rescan_s=0.1, reach=reach, **kw)
        made.append(one)
        one.start()
        return one

    yield make
    for one in made:
        one.stop()


def test_the_reach_is_null_until_its_first_read_answers(monkeypatch):
    from moxi_turns.codex_host import codex_host_available

    reach = Reaches("setup", hold=True)
    monkeypatch.setattr(pagereader, "_claude_reach", reach)

    async def go():
        app = served()
        _, data = await get(app)
        assert data["reach"] is None
        stream = Stream(app)
        try:
            reach.go.set()
            data = await stream.until(lambda d: d["reach"] is not None, LIVE_S)
            assert data["reach"] == {"claude": "setup", "codex": codex_host_available()}
        finally:
            await stream.close()

    run(go())


def test_the_reach_is_read_again_when_endpoint_json_or_claude_changes(reader, monkeypatch):
    from moxi_turns import claude_setup

    reach = Reaches("start")
    one = reader(reach, reach_soon_s=600, reach_later_s=600)
    until(lambda: reach.calls == 1)
    target = claude_setup.plugin_dir(claude_setup.RUNNER, claude_setup.placed_root()) \
        / claude_setup.ENDPOINT_FILE
    target.parent.mkdir(parents=True)
    target.write_text("{}")
    until(lambda: reach.calls == 2)
    target.write_text('{"command": "/a/longer/one"}')
    until(lambda: reach.calls == 3)
    monkeypatch.setenv("PATH", os.pathsep.join(
        part for part in os.environ["PATH"].split(os.pathsep)
        if not (Path(part) / "claude").exists()))
    until(lambda: reach.calls == 4)
    time.sleep(0.5)
    assert reach.calls == 4 and one.value()["reach"]["claude"] == "start"


def test_the_reach_is_read_again_soon_while_setup_and_late_while_start(reader):
    setup = Reaches("setup")
    reader(setup, reach_soon_s=0.2, reach_later_s=600)
    until(lambda: setup.calls >= 3)
    start = Reaches("start")
    reader(start, reach_soon_s=0.2, reach_later_s=600)
    until(lambda: start.calls == 1)
    time.sleep(1.0)
    assert start.calls == 1


def test_a_changed_reach_is_published_once_and_an_unchanged_one_not():
    reach = Reaches("setup")
    notified = []
    one = pagereader.Reader(rescan_s=0.1, reach=reach, reach_soon_s=0.1)
    one.listen(lambda: notified.append(one.value()["reach"]))
    try:
        one.start()
        until(lambda: reach.calls >= 3)
        assert [r for r in notified if r is not None] == [
            {"claude": "setup", "codex": notified[-1]["codex"]}]
        reach.answer = "start"
        until(lambda: any(r and r["claude"] == "start" for r in notified))
        calls = reach.calls
        time.sleep(0.5)
        assert reach.calls == calls     # start waits for the long pace
        assert [r["claude"] for r in notified if r is not None] == ["setup", "start"]
    finally:
        one.stop()


def test_the_readers_own_reach_asks_the_stand_in_claude(monkeypatch, stub_claude):
    """Without a stand-in reach the reader reads `claude_setup.reach`, which runs the
    `claude` PATH finds: the conftest's stand-in, never the real one."""
    monkeypatch.setattr(pagereader, "_claude_reach", OWN_REACH)
    one = pagereader.Reader(rescan_s=0.1)
    try:
        one.start()
        until(lambda: one.value()["reach"] is not None)
    finally:
        one.stop()
    assert "plugin list --json" in (stub_claude / "log").read_text().splitlines()


# --- a slow or misbehaving endpoint ---------------------------------------------------------

def test_an_endpoint_that_hangs_stalls_neither_the_topic_nor_the_read(make_endpoint, odd,
                                                                      monkeypatch):
    monkeypatch.setattr(client, "LIVE_READ_S", 0.5)
    monkeypatch.setattr(pagehandlers, "FIRST_S", 1.0)
    hung = odd("claude-hung", info(), hold=("watch", "facts", "agents"),
               capabilities=("deliver", "agents"))
    ep, fake = make_endpoint(session_id="s-1")
    key = ep.dir.name

    async def go():
        app = served()
        began = time.monotonic()
        status, data = await get(app)
        assert status == 200 and time.monotonic() - began < 2.0
        assert set(keys(data)) == {key, "claude-hung"}
        assert turn(data, key) == (0, False)
        stream = Stream(app)
        try:
            data = await stream.until(
                lambda d: (row_of(d, "claude-hung") or {}).get("read_error") is not None,
                LIVE_S)
            hung_row = row_of(data, "claude-hung")
            assert hung_row["facts"] is None and hung_row["agents"] is None
            assert "watch" in hung_row["read_error"]
            fake.start_turn()
            # The short bound is the claim that the topic is not stalled: a topic held up
            # by the hung reads would come free when their client timeouts end.
            await stream.until(lambda d: turn(d, key) == (1, True), 1.5)
            began = time.monotonic()
            status, data = await get(app)
            assert status == 200 and time.monotonic() - began < 0.5
            assert turn(data, key) == (1, True) and "claude-hung" in keys(data)
        finally:
            await stream.close()

    run(go())
    assert hung.held.is_set() is False      # it hung the whole time


def test_an_endpoint_whose_hello_hangs_stalls_neither_the_topic_nor_the_read(make_endpoint,
                                                                             odd):
    ep, fake = make_endpoint(session_id="s-1")
    key = ep.dir.name

    async def go():
        app = served()
        stream = Stream(app)
        try:
            await stream.until(lambda d: turn(d, key) == (0, False), LIVE_S)
            odd("claude-mute", info(), hold_hello=True)
            await asyncio.sleep(pagereader.RESCAN_S + 0.5)     # a scan is now held on it
            fake.start_turn()
            # The short bound is the claim that the topic is not stalled: a topic held up
            # by the held scan would come free when its connect timeout ends.
            await stream.until(lambda d: turn(d, key) == (1, True), 1.5)
            began = time.monotonic()
            status, data = await get(app)
            assert status == 200 and time.monotonic() - began < 0.5
            assert keys(data) == [key]
        finally:
            await stream.close()

    run(go())


def test_values_of_the_wrong_type_never_reach_the_page(odd):
    odd("claude-odd", info(pid="1234", started="noon", cwd=7, trouble={"oops": 1},
                           endpoint_version=2, session_id=None),
        answers={"watch": lambda p: {"facts": facts_wire()},
                 "facts": lambda p: facts_wire(turn_number="three")},
        capabilities=("deliver",))
    status, data = run(get(served()))
    assert status == 200
    [row] = data["sessions"]
    assert row["pid"] is None and row["started"] is None and row["cwd"] is None
    assert row["endpoint_version"] is None and row["session_id"] is None
    assert row["trouble"] == "the endpoint reports trouble turns cannot read"
    assert row["facts"] is None
    assert row["read_error"] == "its turn could not be read, so what shows may be old"
    assert isinstance(row["unread_since"], float)


def test_a_failed_read_keeps_the_last_agents_and_says_so(make_endpoint, monkeypatch):
    ep, fake = make_endpoint(session_id="s-1")
    key = ep.dir.name
    failing = threading.Event()
    real = client.Session.agents

    def agents(self):
        if failing.is_set():
            raise turns.TurnsError("the endpoint did not answer agents")
        return real(self)

    monkeypatch.setattr(client.Session, "agents", agents)

    async def go():
        stream = Stream(served())
        try:
            # Its first agents read has landed: that is the agents a failed read keeps.
            await stream.until(lambda d: turn(d, key) == (0, False)
                               and row_of(d, key)["agents"] is not None, LIVE_S)
            await asyncio.to_thread(watched, ep)
            failing.set()
            fake.start_turn()
            # A read pass can be under way on either side of each step here: one the watch
            # queued before the turn opened can fail on agents while facts still say turn 0,
            # and one the opening queued can read agents again after `failing` clears while
            # the turn is still open. So each wait is for the error and the turn together.
            data = await stream.until(lambda d: row_of(d, key)["read_error"] is not None
                                      and turn(d, key) == (1, True), LIVE_S)
            assert row_of(data, key)["read_error"] == (
                "its agents could not be read, so what shows may be old")
            assert row_of(data, key)["agents"] == {"entries": [], "complete": True}
            failing.clear()
            fake.end_turn()
            await stream.until(lambda d: row_of(d, key)["read_error"] is None
                               and turn(d, key) == (1, False), LIVE_S)
        finally:
            await stream.close()

    run(go())


def test_a_failed_scan_is_listed_with_a_fixed_detail(monkeypatch):
    def broken():
        raise PermissionError(13, "Permission denied: /a/private/path")

    monkeypatch.setattr(client, "sessions", broken)
    _, data = run(get(served()))
    assert data["listed"] is True and data["sessions"] == []
    assert data["detail"] == ("turns' page server could not list the sessions "
                              "(PermissionError); its log has the details")


# --- the reader's life ----------------------------------------------------------------------

def test_the_reader_starts_on_the_first_ask_and_stop_closes_every_watch(make_endpoint):
    ep, _ = make_endpoint(session_id="s-1")
    assert pagehandlers._reader_now is None
    run(get(served()))
    reader = pagehandlers._reader_now
    assert reader is not None
    watched(ep)
    reader.stop()
    deadline = time.monotonic() + LIVE_S
    while ep._watchers and time.monotonic() < deadline:
        time.sleep(0.02)
    assert not ep._watchers
    assert reader.value()["sessions"] == []


def test_the_read_error_sentences():
    assert pagereader.read_error(set()) is None
    assert pagereader.read_error({"watch"}) == (
        "Moxi can't watch it, so what shows is read every 30 s and may be up to 30 s old")
    assert pagereader.read_error({"watch"}, reread_s=2.5) == (
        "Moxi can't watch it, so what shows is read every 2.5 s and may be up to 2.5 s old")
    assert pagereader.read_error({"watch", "facts"}) == (
        "Moxi can't watch it or read its turn, and tries again")
    assert pagereader.read_error({"agents", "watch"}) == (
        "Moxi can't watch it or read its agents, and tries again")
    assert pagereader.read_error({"facts", "agents", "watch"}) == (
        "Moxi can't watch it or read its turn and agents, and tries again")
    assert pagereader.read_error({"facts"}) == (
        "its turn could not be read, so what shows may be old")
    assert pagereader.read_error({"agents"}) == (
        "its agents could not be read, so what shows may be old")
    assert pagereader.read_error({"agents", "facts"}) == (
        "its turn and agents could not be read, so what shows may be old")


# --- the timer -----------------------------------------------------------------------------

#: The paces the timer tests run at: short, so a test waits seconds, not half a minute.
FAST_S = 0.3
SLOW_S = 1.5


@pytest.fixture
def short_paces(monkeypatch):
    monkeypatch.setattr(pagereader, "UNREAD_S", FAST_S)
    monkeypatch.setattr(pagereader, "REREAD_S", SLOW_S)


def test_a_live_part_that_did_not_read_is_read_again_at_the_fast_pace(make_endpoint,
                                                                      short_paces):
    fake = FakeHarness()
    fake.live_fails = True
    ep, _ = make_endpoint(fake, session_id="s-1")
    key = ep.dir.name

    async def go():
        stream = Stream(served())
        try:
            data = await stream.until(lambda d: facts_of(d, key) != {}, LIVE_S)
            row = row_of(data, key)
            assert row["facts"]["agents"] is None and isinstance(row["unread_since"], float)
            fake.live_fails = False
            began = time.monotonic()
            data = await stream.until(lambda d: row_of(d, key)["unread_since"] is None,
                                      LIVE_S)
            # No event asked for it: the fast pace did, well before the slow one.
            assert time.monotonic() - began < SLOW_S
            assert facts_of(data, key)["agents"] == 0
        finally:
            await stream.close()

    run(go())


def test_an_agent_that_ends_with_no_event_leaves_within_the_slow_pace(make_endpoint,
                                                                      short_paces):
    fake = FakeHarness()
    fake.live_agents = 1
    ep, _ = make_endpoint(fake, session_id="s-1")
    key = ep.dir.name

    async def go():
        stream = Stream(served())
        try:
            await stream.until(lambda d: facts_of(d, key).get("agents") == 1, LIVE_S)
            fake.live_agents = 0
            data = await stream.until(lambda d: facts_of(d, key).get("agents") == 0
                                      and row_of(d, key)["agents"]["entries"] == [], LIVE_S)
            assert row_of(data, key)["unread_since"] is None
        finally:
            await stream.close()

    run(go())


def test_a_failed_read_is_tried_again_and_its_run_is_published_once(make_endpoint,
                                                                    monkeypatch,
                                                                    short_paces):
    ep, _ = make_endpoint(session_id="s-1")
    key = ep.dir.name
    failing = threading.Event()
    tries = []
    real = client.Session.facts

    def facts(self):
        if failing.is_set():
            tries.append(time.monotonic())
            raise turns.TurnsError("the endpoint did not answer facts")
        return real(self)

    monkeypatch.setattr(client.Session, "facts", facts)

    async def go():
        stream = Stream(served())
        try:
            await stream.until(lambda d: turn(d, key) == (0, False), LIVE_S)
            failing.set()
            data = await stream.until(lambda d: row_of(d, key)["unread_since"] is not None,
                                      LIVE_S)
            since = row_of(data, key)["unread_since"]
            assert row_of(data, key)["read_error"] == (
                "its turn could not be read, so what shows may be old")
            # Retries at the fast pace fail again and publish nothing.
            with pytest.raises(TimeoutError):
                await stream.next(6 * FAST_S)
            assert len(tries) >= 3
            _, now = await get(served())
            assert row_of(now, key)["unread_since"] == since
            failing.clear()
            data = await stream.until(lambda d: row_of(d, key)["unread_since"] is None,
                                      LIVE_S)
            assert row_of(data, key)["read_error"] is None
        finally:
            await stream.close()

    run(go())


def test_a_reading_new_only_in_its_at_is_answered_but_not_published(make_endpoint,
                                                                   short_paces):
    ep, _ = make_endpoint(session_id="s-1")
    key = ep.dir.name

    async def go():
        app = served()
        stream = Stream(app)
        try:
            data = await stream.until(lambda d: turn(d, key) == (0, False), LIVE_S)
            # Let the reads the watch's opening asks for settle, and take every value
            # published before then: the first reach among them.
            await asyncio.sleep(SLOW_S)
            with contextlib.suppress(TimeoutError):
                while True:
                    await stream.next(0.5)
            _, first = await get(app)
            try:
                published = await stream.next(3 * SLOW_S)
            except TimeoutError:
                published = None
            assert published is None, (first, published)
            _, later = await get(app)
            assert facts_of(later, key)["at"] > facts_of(first, key)["at"]
            assert {k: v for k, v in facts_of(later, key).items() if k != "at"} == {
                k: v for k, v in facts_of(first, key).items() if k != "at"}
        finally:
            await stream.close()

    run(go())


def test_with_the_watch_down_the_timer_still_reads_and_nothing_says_unread(make_endpoint,
                                                                          monkeypatch,
                                                                          short_paces):
    def no_watch(self):
        raise turns.TurnsError("the watch cannot start")

    monkeypatch.setattr(client.Session, "watch", no_watch)
    ep, fake = make_endpoint(session_id="s-1")
    key = ep.dir.name

    async def go():
        stream = Stream(served())
        try:
            data = await stream.until(lambda d: turn(d, key) == (0, False)
                                      and row_of(d, key)["read_error"] is not None, LIVE_S)
            row = row_of(data, key)
            assert row["read_error"] == (
                f"Moxi can't watch it, so what shows is read every {SLOW_S:g} s and may be "
                f"up to {SLOW_S:g} s old")
            assert row["unread_since"] is None
            fake.start_turn()
            began = time.monotonic()
            await stream.until(lambda d: turn(d, key) == (1, True), LIVE_S)
            assert time.monotonic() - began < 2 * SLOW_S
        finally:
            await stream.close()

    run(go())


class _Reads:
    """A session stand-in whose facts readings are stale for the first `stale` reads."""

    def __init__(self, stale: int):
        self.stale = stale
        self.calls = 0

    def facts(self):
        from types import SimpleNamespace

        self.calls += 1
        return SimpleNamespace(stale=self.calls <= self.stale, n=self.calls)


@pytest.mark.parametrize(("stale", "calls", "kept_stale"), [
    (0, 1, False), (1, 2, False), (3, 4, False), (10, 4, True)])
def test_a_stale_reading_is_read_again_up_to_three_times(stale, calls, kept_stale):
    held = object.__new__(pagereader._Held)
    held.session = _Reads(stale)
    facts = held.read_facts()
    assert pagereader.STALE_REREADS == 3
    assert held.session.calls == calls and facts.n == calls
    assert facts.stale is kept_stale


# --- without the UI -------------------------------------------------------------------------

WITHOUT_UI = textwrap.dedent("""
    import json
    import sys

    # As if the UI shell weren't installed.
    sys.modules["moxi_ui"] = None

    import moxi_turns
    import moxi_turns.client
    import moxi_turns.doctor
    import moxi_turns.module
    import moxi_turns.pagereader
    from moxi_dev.contract import check

    moxi_turns.sessions
    loaded = sorted(name for name in sys.modules
                    if name in ("moxi_turns.page", "moxi_turns.pagehandlers")
                    or name.startswith("moxi_ui."))
    try:
        import moxi_turns.page
    except ImportError as err:
        refused = str(err)
    else:
        refused = None
    findings = [str(finding) for finding in check(sys.argv[1])]
    print(json.dumps({"loaded": loaded, "refused": refused, "findings": findings}))
""")


def test_turns_works_without_the_ui(tmp_path):
    done = subprocess.run([sys.executable, "-P", "-c", WITHOUT_UI, str(ROOT)], cwd=tmp_path,
                          capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    out = json.loads(done.stdout.strip().splitlines()[-1])
    assert out["loaded"] == []
    assert out["refused"].startswith("turns' page needs the ui module")
    assert out["findings"] == []


def test_the_announcement_names_the_page_and_the_ui_extra():
    from moxi_turns import module

    assert module.PAGE == "moxi_turns.page:PAGE"
    assert set(module.OPTIONAL) == {"codex", "ui"}
    found = modules.load("turns", interface=2)
    assert found.ok and found.page == module.PAGE


def test_nothing_in_turns_imports_moxi_ui():
    src = ROOT / "src" / "moxi_turns"
    for path in src.rglob("*.py"):
        text = path.read_text()
        assert "import moxi_ui" not in text and "from moxi_ui" not in text, path


def test_the_page_states_its_group_and_order_among_the_pages():
    from moxi_turns import module

    page_decl = module.COMPONENTS["page"]
    assert (page_decl["group"], page_decl["order"]) == ("daily", 25)

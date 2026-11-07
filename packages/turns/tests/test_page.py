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

SESSION_KEYS = ["key", "harness", "pid", "session_id", "cwd", "started", "capabilities",
                "endpoint_version", "mod_version", "trouble", "facts", "agents",
                "read_error"]


def loopback():
    found = modules.load("loopback", interface=1)
    assert found.ok, found.reason
    return found.api


@pytest.fixture(autouse=True)
def fresh_reader(monkeypatch, turns_env):
    """Each test starts its own reader in its own runtime directory, and stops it."""
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
        self._ready.result(5)
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
        async with asyncio.timeout(3):
            await self.task


def run(coro):
    return asyncio.run(coro)


def keys(data: dict) -> list[str]:
    return [row["key"] for row in data["sessions"]]


def row_of(data: dict, key: str) -> dict | None:
    return next((row for row in data["sessions"] if row["key"] == key), None)


def watched(*endpoints, timeout: float = 5.0) -> None:
    """Wait until each fake endpoint has a watcher: the reader's watch of it."""
    deadline = time.monotonic() + timeout
    while not all(ep._watchers for ep in endpoints):
        assert time.monotonic() < deadline, "the reader's watch never started"
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
    assert list(routes["/api/sessions"]["returns"]) == ["now", "listed", "detail", "sessions"]
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
    assert list(page.FACTS.items) == ["turn_number", "turn_open", "agents", "draft", "stale",
                                      "ready", "at"]
    assert list(page.READY.items) == ["source", "at"]
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
    fake.entries = [Agent("a1", "subagent", "running", False, "look around", None),
                    Agent("a2", "subagent", "done", True, None, "a1")]
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
    facts = row["facts"]
    assert facts["turn_number"] == 0 and facts["turn_open"] is False
    assert facts["agents"] == 1 and facts["draft"] is False and facts["stale"] is False
    assert facts["ready"]["source"] == "startup" and isinstance(facts["ready"]["at"], float)
    assert isinstance(facts["at"], float)
    assert row["agents"] == {"complete": True, "entries": [
        {"id": "a1", "kind": "subagent", "state": "running", "final": False,
         "description": "look around", "parent_id": None},
        {"id": "a2", "kind": "subagent", "state": "done", "final": True,
         "description": None, "parent_id": "a1"}]}


def test_no_session_reads_as_listed_and_empty():
    status, data = run(get(served()))
    assert status == 200
    assert {k: v for k, v in data.items() if k != "now"} == {
        "listed": True, "detail": None, "sessions": []}


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
            name, data = await stream.next(5)
            assert name == "sessions" and keys(data) == [a.dir.name]
            assert stream.status == 200
            b, _ = make_endpoint(session_id="s-2")
            began = time.monotonic()
            data = await stream.until(lambda d: b.dir.name in keys(d),
                                      pagereader.RESCAN_S + 1.5)
            assert time.monotonic() - began <= pagereader.RESCAN_S + 1.5
            assert set(keys(data)) == {a.dir.name, b.dir.name}
        finally:
            await stream.close()

    run(go())


def test_session_gone_drops_it_and_a_later_scan_does_not_bring_it_back(make_endpoint):
    a, _ = make_endpoint(session_id="s-1")
    b, _ = make_endpoint(session_id="s-2")

    async def go():
        stream = Stream(served())
        try:
            await stream.until(lambda d: len(d["sessions"]) == 2, 5)
            await asyncio.to_thread(watched, a, b)
            b.stop("the session ended")
            data = await stream.until(lambda d: b.dir.name not in keys(d), 1.5)
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
            await stream.until(lambda d: turn(d, key) == (0, False), 5)
            fake.start_turn()
            began = time.monotonic()
            await stream.until(lambda d: turn(d, key) == (1, True), 1.5)
            fake.end_turn()
            await stream.until(lambda d: turn(d, key) == (1, False), 1.5)
            fake.start_turn()
            await stream.until(lambda d: turn(d, key) == (2, True), 1.5)
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
            await first.until(lambda d: turn(d, key) == (1, True), 5)
            second = Stream(app)
            try:
                _, data = await second.next(1)
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
            data = await stream.until(lambda d: row_of(d, key) is not None, 5)
            assert row_of(data, key)["trouble"] == "the host is loading the thread"
            fake.trouble = None
            data = await stream.until(lambda d: row_of(d, key)["trouble"] is None,
                                      pagereader.RESCAN_S + 1.5)
            assert row_of(data, key)["read_error"] is None
        finally:
            await stream.close()

    run(go())


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
                lambda d: (row_of(d, "claude-hung") or {}).get("read_error") is not None, 3)
            hung_row = row_of(data, "claude-hung")
            assert hung_row["facts"] is None and hung_row["agents"] is None
            assert "watch" in hung_row["read_error"]
            fake.start_turn()
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
            await stream.until(lambda d: turn(d, key) == (0, False), 5)
            odd("claude-mute", info(), hold_hello=True)
            await asyncio.sleep(pagereader.RESCAN_S + 0.5)     # a scan is now held on it
            fake.start_turn()
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
    assert row["read_error"] == ("turns could not read this session's facts; what is shown "
                                 "may be old")


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
            await stream.until(lambda d: turn(d, key) == (0, False), 5)
            await asyncio.to_thread(watched, ep)
            failing.set()
            fake.start_turn()
            # A read pass can be under way on either side of each step here: one the watch
            # queued before the turn opened can fail on agents while facts still say turn 0,
            # and one the opening queued can read agents again after `failing` clears while
            # the turn is still open. So each wait is for the error and the turn together.
            data = await stream.until(lambda d: row_of(d, key)["read_error"] is not None
                                      and turn(d, key) == (1, True), 5)
            assert row_of(data, key)["read_error"] == (
                "turns could not read this session's agents; what is shown may be old")
            assert row_of(data, key)["agents"] == {"entries": [], "complete": True}
            failing.clear()
            fake.end_turn()
            await stream.until(lambda d: row_of(d, key)["read_error"] is None
                               and turn(d, key) == (1, False), 5)
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
    deadline = time.monotonic() + 3
    while ep._watchers and time.monotonic() < deadline:
        time.sleep(0.02)
    assert not ep._watchers
    assert reader.value()["sessions"] == []


def test_the_read_error_sentences():
    assert pagereader.read_error(set()) is None
    assert pagereader.read_error({"watch"}) == ("turns could not watch this session; it "
                                                "tries again")
    assert pagereader.read_error({"facts", "agents", "watch"}) == (
        "turns could not watch this session or read its facts and agents; it tries again")


# --- without the UI -------------------------------------------------------------------------

WITHOUT_UI = textwrap.dedent("""
    import json
    import sys

    # As if neither loopback nor the UI shell were installed.
    sys.modules["moxi_loopback"] = None
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
                    or name.startswith(("moxi_loopback.", "moxi_ui.")))
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
    assert out["refused"].startswith("turns' page needs the loopback module")
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

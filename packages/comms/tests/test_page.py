"""comms' page server in process, through loopback's strict app, and comms without the UI.

Loopback is reached as the page reaches it, through `moxi.modules.load`: comms'
distribution doesn't require it, so its tests don't import it either. The app runs behind
`guard` with an upstream profile, as the runner serves it, with strict projection, so a
handler that sends a key its route doesn't declare fails here. The roster comes from a
rig daemon, or from `api.roster_rows` stubbed.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest
from conftest import wait_until

from moxi import modules
from moxi_comms import api, page, pagehandlers

from test_reply_enforcement import PLANNER, REVIEWER, P, R

pytestmark = pytest.mark.skipif(not sys.platform.startswith("linux"),
                                reason="comms runs on Linux only")

ROOT = Path(__file__).resolve().parents[1]
TOKEN = "T" * 43
SERVER_ID = "0123456789abcdef0123456789abcdef"
PORT = 4321
STANDING = ["herald", "main@proj-a", "main@proj-b"]


def loopback():
    found = modules.load("ui", interface=3)
    assert found.ok, found.reason
    return found.api.loopback


@pytest.fixture(autouse=True)
def fresh_handlers(monkeypatch):
    """Each test logs each failure class anew and starts its own feed."""
    monkeypatch.setattr(pagehandlers, "_logged", set())
    monkeypatch.setattr(pagehandlers, "_current", None)


def row(r: api.RosterRow) -> dict:
    return json.loads(json.dumps({key: getattr(r, key) for key in pagehandlers.KEYS}))


def up(rows) -> dict:
    return {"state": "up", "detail": None, "rows": [row(r) for r in rows]}


def bound(rig, *names):
    assert wait_until(lambda: all(n in rig.daemon.bindings
                                  and rig.daemon.bindings[n].session_id for n in names))


# --- driving the app ----------------------------------------------------------------------

def served():
    lb = loopback()
    inner = lb.app(page.PAGE, module="comms", server_id=SERVER_ID, strict=True)
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


async def get(app, path: str) -> tuple[int, dict | None]:
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

    def __init__(self, app, query: bytes = b"topics=roster"):
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

    async def none_within(self, seconds: float) -> bool:
        try:
            await self.next(seconds)
        except TimeoutError:
            return True
        return False

    async def close(self) -> None:
        self._gone.set()
        async with asyncio.timeout(3):
            await self.task


def run(coro):
    return asyncio.run(coro)


# --- the table ------------------------------------------------------------------------------

def test_the_gate_matrix_passes():
    assert loopback().gate_matrix(page.PAGE)


def test_the_page_declares_two_reads_none_for_the_gateway():
    exported = loopback().export(page.PAGE)
    routes = {route["path"]: route for route in exported["routes"]}
    assert list(routes) == ["/api/roster", "/api/events"]
    assert all(r["kind"] == "read" and not r["gateway"] for r in routes.values())
    assert routes["/api/events"]["stream"] and list(routes["/api/events"]["topics"]) == ["roster"]
    assert exported["assets"] == ["build-stamp.txt", "page.js", "roster.js"]
    assert exported["budget"] == {"rss": "120M", "fds": 64}
    assert page.PAGE.idle_s == 300 and page.PAGE.health is None


def test_a_row_is_the_reads_fields_and_no_shape_carries_a_path_or_a_token():
    assert list(page.ROW.items) == list(api.RosterRow.__dataclass_fields__)
    keys = set(page.ROW.items) | set(page.ROSTER)
    assert not keys & {"cache_source", "cache_kind", "path", "socket", "token", "pid"}


# --- /api/roster ---------------------------------------------------------------------------

def test_the_roster_over_a_rig_daemon_in_exactly_the_declared_shape(rig):
    reviewer = rig.shim(**REVIEWER)
    bound(rig, R)
    before = time.time()
    status, body = run(get(served(), "/api/roster"))
    assert status == 200
    assert set(body) == {"now", "state", "detail", "rows"}
    assert before <= body["now"] <= time.time()
    assert (body["state"], body["detail"]) == ("up", None)
    assert body["rows"] == up(api.roster_rows())["rows"]
    assert [(r["qualified"], r["live"]) for r in body["rows"]] == [
        (R, True)] + [(q, False) for q in STANDING]
    live, standing = body["rows"][0], body["rows"][1]
    for sent in body["rows"]:
        assert list(sent) == list(page.ROW.items)
    assert live["session_id"] == reviewer.session_id
    assert live["adapter"] == "claude-shim" and isinstance(live["bound_at"], float)
    # A standing row reaches the browser with its binding's fields null.
    assert (standing["tier"], standing["adapter"], standing["session_id"],
            standing["bound_at"], standing["directors"]) == (None, None, None, None, [])


def test_the_daemon_stopped_is_offline_with_no_detail(rig):
    rig.daemon.stop()
    status, body = run(get(served(), "/api/roster"))
    assert status == 200
    assert {**body, "now": 0} == {"now": 0, "state": "offline", "detail": None, "rows": []}


@pytest.mark.parametrize(("error", "detail"), [
    (api.DaemonError("OSError: /x/y"), pagehandlers.DAEMON_ERROR.format("DaemonError")),
    (RuntimeError("at /x/y"), pagehandlers.UNEXPECTED.format("RuntimeError")),
])
def test_a_failed_read_is_a_200_with_state_error_and_a_fixed_detail(monkeypatch, caplog,
                                                                     error, detail):
    def fail(**kw):
        raise error

    monkeypatch.setattr(api, "roster_rows", fail)
    with caplog.at_level(logging.ERROR, logger="moxi.comms.page"):
        status, body = run(get(served(), "/api/roster"))
        again = run(get(served(), "/api/roster"))[1]
    assert {**again, "now": 0} == {**body, "now": 0}
    assert status == 200
    assert (body["state"], body["detail"], body["rows"]) == ("error", detail, [])
    assert "/x/y" not in json.dumps(body)
    logged = [r for r in caplog.records if r.exc_info and r.exc_info[0] is type(error)]
    assert len(logged) == 1 and "/x/y" in caplog.text


LEAD_ROW = api.RosterRow(qualified="lead", name="lead", scope="global", live=True,
                         kind=None, module=None, tier="chosen", adapter="claude-shim")


@pytest.mark.parametrize("bad", [{"directors": (1,)}, {"directors": ({},)},
                                 {"awaiting": (7,)}, {"tier": 3},
                                 {"bound_at": float("inf")}, {"bound_at": 10 ** 400},
                                 {"live": "yes"}, {"name": None}],
                         ids=["int-director", "object-director", "int-awaiting", "int-tier",
                              "infinite", "overflowing", "string-live", "no-name"])
def test_a_value_its_declared_type_cannot_carry_is_state_error(monkeypatch, bad):
    """A row built from a hand-edited claim file, with one value of the wrong type, ends
    the read in `state` `error`, never a 500."""
    rows = (dataclasses.replace(LEAD_ROW, **bad), dataclasses.replace(LEAD_ROW, name="ok"))
    monkeypatch.setattr(api, "roster_rows", lambda **kw: rows)
    status, body = run(get(served(), "/api/roster"))
    assert status == 200
    assert (body["state"], body["detail"], body["rows"]) == (
        "error", pagehandlers.UNEXPECTED.format("ValueError"), [])


def test_the_gather_asks_with_the_pages_deadline(monkeypatch):
    asked = []
    monkeypatch.setattr(api, "roster_rows", lambda **kw: asked.append(kw) or ())
    assert pagehandlers.gather() == {"state": "up", "detail": None, "rows": []}
    assert asked == [{"timeout_s": 2.0}] and pagehandlers.DEADLINE == 2.0


# --- /api/events and the poller ---------------------------------------------------------------

def test_the_first_event_is_current_and_a_new_binding_comes_within_12_s(rig):
    """At the real period: the poller gathers every 10 s."""
    assert pagehandlers.PERIOD == 10.0
    rig.shim(**REVIEWER)
    bound(rig, R)

    async def go():
        stream = Stream(served())
        try:
            name, data = await stream.next(3)
            assert stream.status == 200
            assert (name, data) == ("roster", up(api.roster_rows()))
            rig.shim(**PLANNER)
            name, data = await stream.next(12)
            assert [(r["qualified"], r["live"]) for r in data["rows"]][:2] == [
                (P, True), (R, True)]
        finally:
            await stream.close()

    run(go())


def test_two_gathers_with_no_change_publish_once(monkeypatch):
    monkeypatch.setattr(pagehandlers, "PERIOD", 0.1)
    rows = (api.RosterRow(qualified="lead", name="lead", scope="global", live=True,
                          kind=None, module=None, tier="chosen", adapter="claude-shim"),)
    calls = []
    monkeypatch.setattr(api, "roster_rows", lambda **kw: calls.append(1) or rows)

    async def go():
        stream = Stream(served())
        try:
            assert await stream.next(3) == ("roster", up(rows))
            assert await stream.none_within(0.6)
        finally:
            await stream.close()

    run(go())
    assert len(calls) >= 3


def test_the_topic_goes_offline_with_the_daemon_and_up_when_it_answers(rig, monkeypatch):
    monkeypatch.setattr(pagehandlers, "PERIOD", 0.1)
    rig.shim(**REVIEWER)
    bound(rig, R)
    offline = [True]
    real = api.roster_rows

    def flaky(**kw):
        if offline[0]:
            raise api.DaemonOffline("comms offline: /x/daemon.sock")
        return real(**kw)

    monkeypatch.setattr(api, "roster_rows", flaky)

    async def go():
        stream = Stream(served())
        try:
            assert await stream.next(3) == ("roster", {"state": "offline", "detail": None,
                                                       "rows": []})
            offline[0] = False
            _, data = await stream.next(3)
            assert data["state"] == "up" and data["rows"][0]["qualified"] == R
        finally:
            await stream.close()

    run(go())


def test_the_topic_publishes_the_error_and_then_up_once_the_read_recovers(monkeypatch):
    monkeypatch.setattr(pagehandlers, "PERIOD", 0.1)
    raising = [api.DaemonError("OSError: /x/y"), RuntimeError("boom")]

    def flaky(**kw):
        if raising:
            raise raising[0]
        return ()

    monkeypatch.setattr(api, "roster_rows", flaky)

    async def go():
        app = served()
        stream = Stream(app)
        try:
            _, data = await stream.next(3)
            assert data == {"state": "error", "rows": [],
                            "detail": pagehandlers.DAEMON_ERROR.format("DaemonError")}
            raising.pop(0)
            _, data = await stream.next(3)
            assert data["detail"] == pagehandlers.UNEXPECTED.format("RuntimeError")
            status, body = await get(app, "/api/roster")
            assert status == 200 and body["state"] == "error"
            raising.pop(0)
            _, data = await stream.next(3)
            assert data == {"state": "up", "detail": None, "rows": []}
        finally:
            await stream.close()

    run(go())


def test_the_poller_outlives_a_gather_that_raises(monkeypatch, caplog):
    monkeypatch.setattr(pagehandlers, "PERIOD", 0.1)
    calls = []

    def gather():
        calls.append(1)
        if len(calls) <= 2:
            raise RuntimeError("the gather broke")
        return {"state": "up", "detail": None, "rows": []}

    monkeypatch.setattr(pagehandlers, "gather", gather)

    async def go():
        stream = Stream(served())
        try:
            assert await stream.next(3) == ("roster", {"state": "up", "detail": None,
                                                       "rows": []})
        finally:
            await stream.close()

    with caplog.at_level(logging.ERROR, logger="moxi.comms.page"):
        run(go())
    assert len([r for r in caplog.records if r.exc_info]) == 1


def test_the_poller_starts_with_the_first_stream_and_keeps_running_after_it(monkeypatch):
    monkeypatch.setattr(pagehandlers, "PERIOD", 0.1)
    monkeypatch.setattr(api, "roster_rows", lambda **kw: ())

    async def go():
        assert pagehandlers._current is None
        stream = Stream(served())
        await stream.next(3)
        feed = pagehandlers._current
        await stream.close()
        await asyncio.sleep(0.3)
        assert feed is pagehandlers._current and not feed.task.done()
        feed.task.cancel()

    run(go())


# --- without the UI ---------------------------------------------------------------------------

WITHOUT_UI = textwrap.dedent("""
    import json
    import sys

    # As if the UI shell weren't installed.
    sys.modules["moxi_ui"] = None

    import moxi_comms.adapters.claude_shim
    import moxi_comms.adapters.opencode
    import moxi_comms.adapters.session
    import moxi_comms.api
    import moxi_comms.cli
    import moxi_comms.daemon
    import moxi_comms.listeners
    import moxi_comms.module
    import moxi_comms.provision
    import moxi_comms.reserved
    from moxi_dev.contract import check

    code = moxi_comms.cli.main(["help"], prog="moxi comms")
    loaded = sorted(name for name in sys.modules
                    if name in ("moxi_comms.page", "moxi_comms.pagehandlers")
                    or name.startswith("moxi_ui."))
    try:
        import moxi_comms.page
    except ImportError as err:
        refused = str(err)
    else:
        refused = None
    findings = [str(finding) for finding in check(sys.argv[1])]
    print(json.dumps({"code": code, "loaded": loaded, "refused": refused,
                      "findings": findings}))
""")


def test_comms_works_without_the_ui(tmp_path):
    done = subprocess.run([sys.executable, "-P", "-c", WITHOUT_UI, str(ROOT)], cwd=tmp_path,
                          capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    out = json.loads(done.stdout.strip().splitlines()[-1])
    assert out["code"] == 0
    assert out["loaded"] == []
    assert out["refused"].startswith("comms' page needs the ui module")
    assert out["findings"] == []


def test_the_announcement_names_the_page_and_its_components():
    from moxi_comms import module

    assert module.PAGE == "moxi_comms.page:PAGE"
    assert module.OPTIONAL["ui"] == ("The comms panel and its roster of agents in the "
                                     "browser UI shell.")
    assert set(module.COMPONENTS) == {"page", "roster"}
    assert module.COMPONENTS["roster"] == {
        "script": "roster.js", "title": "Agents", "contract": 1, "place": "block",
        "home": True, "doc": "Names held by live sessions, and standing names"}
    assert module.COMPONENTS["page"]["script"] == "page.js"
    modules.reset()
    found = modules.load("comms", interface=2)
    assert found.ok and found.page == module.PAGE
    assert set(found.components) == {"page", "roster"}

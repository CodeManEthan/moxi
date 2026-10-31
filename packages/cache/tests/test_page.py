"""cache's page server in process, through loopback's strict app, and cache without the UI.

Loopback is reached as the page reaches it, through `moxi.modules.load`: cache's
distribution doesn't require it, so its tests don't import it either. The app runs behind
`guard` with an upstream profile, as the runner serves it, with strict projection, so a
handler that sends a key its route doesn't declare fails here.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest
from band_cases import CASES

from moxi import modules
from moxi_cache import api, page, pagehandlers, records

pytestmark = pytest.mark.skipif(not sys.platform.startswith("linux"),
                                reason="cache runs on Linux only")

ROOT = Path(__file__).resolve().parents[1]
TOKEN = "T" * 43
SERVER_ID = "0123456789abcdef0123456789abcdef"
PORT = 4321
A = "aaaaaaaa-1111-4111-8111-111111111111"
B = "bbbbbbbb-2222-4222-8222-222222222222"
UNEXPECTED = "cache's page server hit an unexpected error ({}); its log has the details"


def loopback():
    found = modules.load("loopback", interface=1)
    assert found.ok, found.reason
    return found.api


@pytest.fixture(autouse=True)
def fresh_handlers(monkeypatch):
    """Each test logs each failure class anew and starts its own feed."""
    monkeypatch.setattr(pagehandlers, "_logged", set())
    monkeypatch.setattr(pagehandlers, "_current", None)


def record(session=A, *, last_request=None, **extra) -> dict:
    now = time.time()
    out = {"v": 1, "session": session, "harness": "claude", "pid": 4242, "kind": "warm",
           "last_request": now - 60 if last_request is None else last_request,
           "ttl": 3600, "read_at": now - 5, "seen_at": now - 1, "code": None,
           "warn_before": 900, "stale_after": 30}
    out.update(extra)
    return out


def put(rec: dict, text: str | None = None) -> None:
    directory = records.record_dir()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{rec['session']}.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(rec) if text is None else text)
    os.replace(tmp, path)


def row(rec: dict) -> dict:
    return {key: rec.get(key) for key in pagehandlers.KEYS}


# --- driving the app ----------------------------------------------------------------------

def served():
    lb = loopback()
    inner = lb.app(page.PAGE, module="cache", server_id=SERVER_ID, strict=True)
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

    def __init__(self, app, query: bytes = b"topics=clocks"):
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
    assert list(routes) == ["/api/clocks", "/api/events"]
    assert all(r["kind"] == "read" and not r["gateway"] for r in routes.values())
    assert routes["/api/events"]["stream"] and list(routes["/api/events"]["topics"]) == ["clocks"]
    assert exported["budget"] == {"rss": "120M", "fds": 64}
    assert page.PAGE.idle_s == 300 and page.PAGE.health is None


def test_the_export_lists_the_built_assets():
    exported = loopback().export(page.PAGE)
    assert [route["path"] for route in exported["routes"]] == ["/api/clocks", "/api/events"]
    assert exported["assets"] == ["build-stamp.txt", "chip.js", "page.js"]


def test_no_declared_shape_carries_a_path_or_a_process():
    keys = set(page.ROW.items) | set(page.CLOCKS)
    assert not keys & {"pid", "read_at", "v", "path"}


# --- /api/clocks ---------------------------------------------------------------------------

def test_clocks_sends_every_record_in_exactly_the_declared_shape():
    written = [case["record"] for case in CASES]
    for rec in written:
        put(rec)
    before = time.time()
    status, body = run(get(served(), "/api/clocks"))
    assert status == 200
    assert set(body) == {"now", "state", "detail", "clocks"}
    assert before <= body["now"] <= time.time()
    assert (body["state"], body["detail"]) == ("up", None)
    expected = [row(rec) for rec in sorted(written, key=lambda r: r["session"])]
    assert body["clocks"] == expected
    for sent in body["clocks"]:
        assert list(sent) == list(page.ROW.items)
    # A record an older watcher wrote reaches the browser with its thresholds null.
    assert any(sent["warn_before"] is None for sent in body["clocks"])
    assert any(sent["last_request"] is None and sent["ttl"] is None for sent in body["clocks"])


def test_clocks_with_no_records_is_up_and_empty():
    status, body = run(get(served(), "/api/clocks"))
    assert status == 200 and body["state"] == "up" and body["clocks"] == []


@pytest.mark.parametrize("error", [PermissionError(13, "Permission denied: '/x/y'"),
                                   RuntimeError("at /x/y")])
def test_a_failed_listing_is_a_200_with_state_error_and_a_fixed_detail(monkeypatch, caplog,
                                                                        error):
    def fail():
        raise error

    monkeypatch.setattr(api, "clock_records", fail)
    with caplog.at_level(logging.ERROR, logger="moxi.cache.page"):
        status, body = run(get(served(), "/api/clocks"))
        again = run(get(served(), "/api/clocks"))[1]
    assert {**again, "now": 0} == {**body, "now": 0}
    assert status == 200
    assert body["state"] == "error" and body["clocks"] == []
    assert body["detail"] == UNEXPECTED.format(type(error).__name__)
    assert "/x/y" not in json.dumps(body)
    logged = [r for r in caplog.records if r.exc_info and r.exc_info[0] is type(error)]
    assert len(logged) == 1


@pytest.mark.parametrize("bad", [{"ttl": float("inf")}, {"warn_before": "900"},
                                 {"warn_before": True}, {"stale_after": {}},
                                 {"last_request": 10 ** 400}],
                         ids=["infinite", "string", "bool", "object", "overflowing"])
def test_a_value_its_declared_type_cannot_carry_is_state_error(bad):
    """A hand-edited record, or one another writer of the format made, with one value of
    the wrong type ends the read in `state` `error`, never a 500."""
    put(record(), json.dumps(record(**bad)))
    put(record(B))
    status, body = run(get(served(), "/api/clocks"))
    assert status == 200 and body["state"] == "error" and body["clocks"] == []
    assert body["detail"] == UNEXPECTED.format("ValueError")


# --- /api/events and the poller ---------------------------------------------------------------

def test_the_first_event_is_the_current_value_and_a_rewrite_comes_within_3_s():
    rec = record()
    put(rec)

    async def go():
        stream = Stream(served())
        try:
            assert await stream.next(3) == ("clocks", {"state": "up", "detail": None,
                                                       "clocks": [row(rec)]})
            assert stream.status == 200
            moved = dict(rec, last_request=rec["last_request"] + 30)
            put(moved)
            assert await stream.next(3) == ("clocks", {"state": "up", "detail": None,
                                                       "clocks": [row(moved)]})
        finally:
            await stream.close()

    run(go())


def test_one_event_per_change_and_none_for_a_rewrite_that_changes_nothing(monkeypatch):
    monkeypatch.setattr(pagehandlers, "PERIOD", 0.1)
    rec = record()
    put(rec)

    async def go():
        stream = Stream(served())
        try:
            await stream.next(3)
            put(record(B, last_request=rec["last_request"]))
            _, data = await stream.next(3)
            assert [sent["session"] for sent in data["clocks"]] == [A, B]
            assert await stream.none_within(0.6)
            put(rec)
            assert await stream.none_within(0.6)
        finally:
            await stream.close()

    run(go())


def test_a_second_stream_gets_the_current_value_first(monkeypatch):
    monkeypatch.setattr(pagehandlers, "PERIOD", 0.1)
    rec = record()
    put(rec)

    async def go():
        app = served()
        first = Stream(app)
        try:
            await first.next(3)
            second = Stream(app)
            try:
                assert (await second.next(1))[1]["clocks"] == [row(rec)]
            finally:
                await second.close()
        finally:
            await first.close()

    run(go())


def test_the_topic_publishes_the_error_and_then_up_once_the_listing_recovers(monkeypatch):
    monkeypatch.setattr(pagehandlers, "PERIOD", 0.1)
    rec = record()
    put(rec)
    raising = [PermissionError(13, "Permission denied"), RuntimeError("boom")]
    real = api.clock_records

    def flaky():
        if raising:
            raise raising[0]
        return real()

    monkeypatch.setattr(api, "clock_records", flaky)

    async def go():
        app = served()
        stream = Stream(app)
        try:
            _, data = await stream.next(3)
            assert data == {"state": "error", "detail": UNEXPECTED.format("PermissionError"),
                            "clocks": []}
            raising.pop(0)
            _, data = await stream.next(3)
            assert data["detail"] == UNEXPECTED.format("RuntimeError")
            status, body = await get(app, "/api/clocks")
            assert status == 200 and body["state"] == "error"
            raising.pop(0)
            _, data = await stream.next(3)
            assert data == {"state": "up", "detail": None, "clocks": [row(rec)]}
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
        return {"state": "up", "detail": None, "clocks": []}

    monkeypatch.setattr(pagehandlers, "gather", gather)

    async def go():
        stream = Stream(served())
        try:
            assert await stream.next(3) == ("clocks", {"state": "up", "detail": None,
                                                       "clocks": []})
        finally:
            await stream.close()

    with caplog.at_level(logging.ERROR, logger="moxi.cache.page"):
        run(go())
    assert len([r for r in caplog.records if r.exc_info]) == 1


def test_the_poller_starts_with_the_first_stream_and_keeps_running_after_it(monkeypatch):
    monkeypatch.setattr(pagehandlers, "PERIOD", 0.1)

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

    # As if neither loopback nor the UI shell were installed.
    sys.modules["moxi_loopback"] = None
    sys.modules["moxi_ui"] = None

    import moxi_cache.api
    import moxi_cache.cli
    import moxi_cache.doctor
    import moxi_cache.module
    import moxi_cache.segment
    import moxi_cache.watch
    from moxi.contract import check

    code = moxi_cache.cli.main(["read", "aaaaaaaa-1111-4111-8111-111111111111"],
                               prog="moxi cache")
    loaded = sorted(name for name in sys.modules
                    if name in ("moxi_cache.page", "moxi_cache.pagehandlers")
                    or name.startswith(("moxi_loopback.", "moxi_ui.")))
    try:
        import moxi_cache.page
    except ImportError as err:
        refused = str(err)
    else:
        refused = None
    findings = [str(finding) for finding in check(sys.argv[1])]
    print(json.dumps({"code": code, "loaded": loaded, "refused": refused,
                      "findings": findings}))
""")


def test_cache_works_without_the_ui(tmp_path):
    done = subprocess.run([sys.executable, "-P", "-c", WITHOUT_UI, str(ROOT)], cwd=tmp_path,
                          capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    out = json.loads(done.stdout.strip().splitlines()[-1])
    assert out["code"] == 0
    assert out["loaded"] == []
    assert out["refused"].startswith("cache's page needs the loopback module")
    assert "loopback" in out["refused"]
    assert out["findings"] == []


def test_the_announcement_names_the_page_and_its_components():
    from moxi_cache import module

    assert module.PAGE == "moxi_cache.page:PAGE"
    assert module.OPTIONAL == {"ui": "The cache panel and its chip in the browser UI shell."}
    assert set(module.COMPONENTS) == {"page", "chip"}
    assert module.COMPONENTS["chip"]["attributes"] == {
        "session": {"type": "string", "required": True, "doc": "the harness's session id"}}
    found = modules.load("cache", interface=1)
    assert found.ok and found.page == module.PAGE
    assert set(found.components) == {"page", "chip"}

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
#: The page server's local date in these tests, but where a test moves it.
TODAY = "2027-01-15"
#: The page server's own reading of the date, kept before any test replaces it.
LOCAL_DATE = pagehandlers._today


def loopback():
    found = modules.load("ui", interface=3)
    assert found.ok, found.reason
    return found.api.loopback


@pytest.fixture(autouse=True)
def fresh_handlers(monkeypatch):
    """Each test logs each failure class anew and starts its own feed, on a date that
    stays put until the test moves it."""
    monkeypatch.setattr(pagehandlers, "_logged", set())
    monkeypatch.setattr(pagehandlers, "_current", None)
    monkeypatch.setattr(pagehandlers, "_today", lambda: TODAY)


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


def put_reading(tick_at: float | None = None, listed_at: float | None = 0.0,
                stale_after: float = 30, text: str | None = None) -> dict:
    """Write the watcher's reading, as the watcher does: whole, by a rename. `listed_at`
    0.0 means at `tick_at`. Returns what the page should send for it."""
    tick_at = time.time() - 1 if tick_at is None else tick_at
    listed_at = tick_at if listed_at == 0.0 else listed_at
    doc = {"v": 1, "pid": 4242, "tick_at": tick_at, "listed_at": listed_at,
           "stale_after": stale_after}
    path = records.reading_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc) if text is None else text)
    os.replace(tmp, path)
    return {"tick_at": tick_at, "listed_at": listed_at, "stale_after": stale_after}


def value(rows: list[dict], reading: dict | None = None, today: str = TODAY) -> dict:
    """The topic's value when the page server is up."""
    return {"state": "up", "detail": None, "today": today, "reading": reading,
            "clocks": rows}


def conforms(sent, shape) -> bool:
    """Whether `sent` is of the declared `shape`, key for key, as strict projection holds
    it: a table has exactly its declared keys."""
    if sent is None:
        return shape.nullable
    if shape.type == "table":
        return (isinstance(sent, dict) and set(sent) == set(shape.items)
                and all(conforms(sent[k], shape.items[k]) for k in shape.items))
    if shape.type == "list":
        return isinstance(sent, list) and all(conforms(v, shape.items) for v in sent)
    if shape.type == "float":
        return isinstance(sent, (int, float)) and not isinstance(sent, bool)
    return isinstance(sent, {"string": str, "int": int, "bool": bool}[shape.type])


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


async def get(app, path: str, query: bytes = b"") -> tuple[int, dict | None]:
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

    await app(scope(path, query), receive, send)
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


ROUTES = ["/api/clocks", "/api/events", "/api/names", "/api/session"]


def test_the_page_declares_four_reads_none_for_the_gateway():
    exported = loopback().export(page.PAGE)
    routes = {route["path"]: route for route in exported["routes"]}
    assert list(routes) == ROUTES
    assert all(r["kind"] == "read" and not r["gateway"] for r in routes.values())
    assert routes["/api/events"]["stream"] and list(routes["/api/events"]["topics"]) == ["clocks"]
    assert exported["budget"] == {"rss": "120M", "fds": 64}
    assert page.PAGE.idle_s == 300 and page.PAGE.health is None


def test_the_export_lists_the_built_assets():
    exported = loopback().export(page.PAGE)
    assert [route["path"] for route in exported["routes"]] == ROUTES
    assert exported["assets"] == ["build-stamp.txt", "chip.js", "page.js"]


def test_no_declared_shape_carries_a_path_or_a_process():
    keys = set(page.ROW.items) | set(page.CLOCKS) | set(page.READING.items)
    assert not keys & {"pid", "read_at", "v", "path"}


# --- /api/clocks ---------------------------------------------------------------------------

def test_clocks_sends_every_record_in_exactly_the_declared_shape():
    written = [case["record"] for case in CASES]
    for rec in written:
        put(rec)
    before = time.time()
    status, body = run(get(served(), "/api/clocks"))
    assert status == 200
    assert set(body) == {"now", "state", "detail", "today", "reading", "clocks"}
    assert before <= body["now"] <= time.time()
    assert (body["state"], body["detail"], body["today"]) == ("up", None, TODAY)
    assert body["reading"] is None
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
    assert body["reading"] is None


def test_clocks_carries_the_watchers_reading_cut_to_its_declared_keys():
    sent = put_reading(listed_at=time.time() - 40)
    put(record())
    status, body = run(get(served(), "/api/clocks"))
    assert status == 200 and body["state"] == "up"
    assert body["reading"] == sent
    assert list(body["reading"]) == list(page.READING.items)


def test_clocks_carries_a_reading_that_never_listed():
    sent = put_reading(listed_at=None)
    status, body = run(get(served(), "/api/clocks"))
    assert status == 200 and body["reading"] == sent and sent["listed_at"] is None


@pytest.mark.parametrize("text", ['{"v": 1, "pid": 4242, "tick_at"', "[]",
                                  '{"v": 1, "pid": 1, "tick_at": "x", "listed_at": null, '
                                  '"stale_after": 30}'],
                         ids=["cut", "not-a-table", "malformed"])
def test_a_reading_that_cannot_be_read_is_null_and_the_read_is_up(text):
    put_reading(text=text)
    rec = record()
    put(rec)
    status, body = run(get(served(), "/api/clocks"))
    assert status == 200 and body["state"] == "up" and body["reading"] is None
    assert body["clocks"] == [row(rec)]


def test_clocks_carries_the_page_servers_local_date(monkeypatch):
    status, body = run(get(served(), "/api/clocks"))
    assert body["today"] == TODAY
    monkeypatch.setattr(pagehandlers, "_today", lambda: "2027-01-16")
    assert run(get(served(), "/api/clocks"))[1]["today"] == "2027-01-16"


def test_today_is_the_local_date():
    import datetime as dt

    before = dt.date.today().isoformat()
    assert LOCAL_DATE() in {before, dt.date.today().isoformat()}


@pytest.mark.parametrize("error", [PermissionError(13, "Permission denied: '/x/y'"),
                                   RuntimeError("at /x/y")])
def test_a_failed_listing_is_a_200_with_state_error_and_a_fixed_detail(monkeypatch, caplog,
                                                                        error):
    def fail():
        raise error

    monkeypatch.setattr(api, "clock_records", fail)
    put_reading()
    with caplog.at_level(logging.ERROR, logger="moxi.cache.page"):
        status, body = run(get(served(), "/api/clocks"))
        again = run(get(served(), "/api/clocks"))[1]
    assert {**again, "now": 0} == {**body, "now": 0}
    assert status == 200
    assert body["state"] == "error" and body["clocks"] == []
    assert body["reading"] is None and body["today"] == TODAY
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
            assert await stream.next(3) == ("clocks", value([row(rec)]))
            assert stream.status == 200
            moved = dict(rec, last_request=rec["last_request"] + 30)
            put(moved)
            assert await stream.next(3) == ("clocks", value([row(moved)]))
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


def test_the_topic_carries_the_reading_and_today():
    sent = put_reading()

    async def go():
        stream = Stream(served())
        try:
            assert await stream.next(3) == ("clocks", value([], sent))
        finally:
            await stream.close()

    run(go())


def test_one_event_per_watcher_tick_with_no_record(monkeypatch):
    monkeypatch.setattr(pagehandlers, "PERIOD", 0.1)
    first = put_reading(tick_at=1_800_000_000.0)

    async def go():
        stream = Stream(served())
        try:
            assert await stream.next(3) == ("clocks", value([], first))
            for tick in (10, 20):
                moved = put_reading(tick_at=1_800_000_000.0 + tick)
                assert await stream.next(3) == ("clocks", value([], moved))
                assert await stream.none_within(0.4)
            put_reading(tick_at=1_800_000_020.0)       # rewritten, nothing changed
            assert await stream.none_within(0.4)
            stopped = put_reading(tick_at=1_800_000_030.0, listed_at=1_800_000_020.0)
            assert await stream.next(3) == ("clocks", value([], stopped))
        finally:
            await stream.close()

    run(go())


def test_an_event_when_the_date_changes(monkeypatch):
    monkeypatch.setattr(pagehandlers, "PERIOD", 0.1)
    rec = record()
    put(rec)
    dates = ["2027-01-15"]
    monkeypatch.setattr(pagehandlers, "_today", lambda: dates[0])

    async def go():
        stream = Stream(served())
        try:
            assert await stream.next(3) == ("clocks", value([row(rec)]))
            assert await stream.none_within(0.4)
            dates[0] = "2027-01-16"
            assert await stream.next(3) == ("clocks", value([row(rec)], today="2027-01-16"))
            assert await stream.none_within(0.4)
        finally:
            await stream.close()

    run(go())


def test_a_reading_that_goes_is_published_as_null(monkeypatch):
    monkeypatch.setattr(pagehandlers, "PERIOD", 0.1)
    sent = put_reading()

    async def go():
        stream = Stream(served())
        try:
            assert (await stream.next(3))[1]["reading"] == sent
            records.reading_path().unlink()
            assert await stream.next(3) == ("clocks", value([]))
        finally:
            await stream.close()

    run(go())


def test_every_answer_passes_its_declared_shape(monkeypatch):
    """The read and the topic, up with and without a reading, a reading that never
    listed, records with null values, and the error, each hold exactly to the shapes the
    routes declare."""
    read_shape = lb_table({"now": None, **page.CLOCKS})
    topic_shape = lb_table(page.CLOCKS)
    for case in CASES:
        put(case["record"])
    answers = []
    answers.append(run(get(served(), "/api/clocks")))
    put_reading(listed_at=None)
    answers.append(run(get(served(), "/api/clocks")))
    put_reading()
    answers.append(run(get(served(), "/api/clocks")))

    def fail():
        raise RuntimeError("boom")

    with monkeypatch.context() as patched:
        patched.setattr(api, "clock_records", fail)
        answers.append(run(get(served(), "/api/clocks")))
    for status, body in answers:
        assert status == 200
        assert conforms(body, read_shape), body
        topic = {k: v for k, v in body.items() if k != "now"}
        assert conforms(topic, topic_shape)
    assert [body["state"] for _, body in answers] == ["up", "up", "up", "error"]
    assert pagehandlers.gather()["reading"] is not None
    assert conforms(pagehandlers.gather(), topic_shape)


def lb_table(items: dict):
    lb = loopback()
    return lb.Out("table", items={k: (lb.Out("float") if v is None else v)
                                  for k, v in items.items()})


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
                            "today": TODAY, "reading": None, "clocks": []}
            raising.pop(0)
            _, data = await stream.next(3)
            assert data["detail"] == UNEXPECTED.format("RuntimeError")
            status, body = await get(app, "/api/clocks")
            assert status == 200 and body["state"] == "error"
            raising.pop(0)
            _, data = await stream.next(3)
            assert data == value([row(rec)])
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
        return value([])

    monkeypatch.setattr(pagehandlers, "gather", gather)

    async def go():
        stream = Stream(served())
        try:
            assert await stream.next(3) == ("clocks", value([]))
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

    # As if the UI shell weren't installed.
    sys.modules["moxi_ui"] = None

    import moxi_cache.api
    import moxi_cache.cli
    import moxi_cache.doctor
    import moxi_cache.module
    import moxi_cache.segment
    import moxi_cache.watch
    from moxi_dev.contract import check

    code = moxi_cache.cli.main(["read", "aaaaaaaa-1111-4111-8111-111111111111"],
                               prog="moxi cache")
    loaded = sorted(name for name in sys.modules
                    if name in ("moxi_cache.page", "moxi_cache.pagehandlers")
                    or name.startswith("moxi_ui."))
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
    assert out["refused"].startswith("cache's page needs the ui module")
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


def test_the_page_states_its_group_and_order_among_the_pages():
    from moxi_cache import module

    page_decl = module.COMPONENTS["page"]
    assert (page_decl["group"], page_decl["order"]) == ("daily", 50)


# --- /api/names and /api/session ------------------------------------------------------------

C = "cccccccc-3333-4333-8333-333333333333"
#: What the page server sends for a session sessions does not name.
UNNAMED = {"name": None, "folder": None, "started": None}


class StandIn:
    """sessions as `pagehandlers` calls it, answering from what a test gives it and keeping
    every call's keys."""

    def __init__(self, facts=None, roots=(), opened=None, fails=None):
        self.facts, self.roots, self.opened, self.fails = facts or {}, list(roots), opened, fails
        self.calls: list[list[tuple[str, str]]] = []
        self.asked: list[dict] = []

    def session_facts(self, keys):
        keys = list(keys)
        self.calls.append(keys)
        if self.fails is not None:
            raise self.fails
        return {"sessions": [{"harness": h, "session": s, **self.facts[(h, s)]}
                             for h, s in keys if (h, s) in self.facts],
                "roots": self.roots}

    def session_opened(self, *, harness, session):
        self.asked.append({"harness": harness, "session": session})
        if self.fails is not None:
            raise self.fails
        return self.opened


def stand_in(monkeypatch, **kw) -> StandIn:
    found = StandIn(**kw)
    monkeypatch.setattr(pagehandlers, "sessions", found)
    return found


def test_names_and_the_model_come_from_sessions_own_reads():
    """Real transcripts read through sessions: a typed name, a session named by its head
    alone, and a record sessions does not hold; then the opened row's model."""
    from moxi_sessions.api import testing

    projects = Path(os.environ["CLAUDE_CONFIG_DIR"]) / "projects"
    started = 1793354400.0
    typed = testing.ClaudeTranscript(projects, session_id=A, cwd="/work/kestrel-web",
                                     project="-work-kestrel-web", start=started)
    typed.user("Do the thing.", origin="human")
    typed.title("orders-skeleton")
    typed.assistant("Done.", model="claude-model-a")
    typed.write()
    head = testing.ClaudeTranscript(projects, session_id=B, cwd="/work/orbit-cli",
                                    project="-work-orbit-cli", start=started + 60)
    head.user("Look at this.", origin="human")
    head.assistant("Done.")
    head.write()
    for session in (A, B, C):
        put(record(session))
    status, body = run(get(served(), "/api/names"))
    assert status == 200
    assert (body["state"], body["today"], body["home"]) == ("up", TODAY, str(Path.home()))
    assert body["sessions"] == [
        {"session": A, "harness": "claude", "name": "orders-skeleton",
         "folder": "/work/kestrel-web", "started": started, "known": True},
        {"session": B, "harness": "claude", "name": None, "folder": "/work/orbit-cli",
         "started": started + 60, "known": True},
        {"session": C, "harness": "claude", **UNNAMED, "known": False},
    ]
    assert conforms(body, lb_table(page.NAMES))
    assert run(get(served(), "/api/session", b"session=" + A.encode())) == (
        200, {"model": "claude-model-a"})
    # a record sessions does not hold, and a session with no record
    assert run(get(served(), "/api/session", b"session=" + C.encode())) == (200, {"model": None})
    assert run(get(served(), "/api/session", b"session=nothing-here")) == (200, {"model": None})


def test_a_root_not_listed_whole_leaves_its_sessions_unknown(monkeypatch):
    found = stand_in(monkeypatch,
                     facts={("claude", A): {"name": "n", "folder": "/f", "started": 5}},
                     roots=[{"harness": "claude", "root": "/x/projects", "reason": None}])
    put(record(A))
    put(record(B))
    body = run(get(served(), "/api/names"))[1]
    assert [item["known"] for item in body["sessions"]] == [True, None]
    assert body["sessions"][1] == {"session": B, "harness": "claude", **UNNAMED, "known": None}
    assert found.calls == [[("claude", A), ("claude", B)]]
    assert "/x/projects" not in json.dumps(body)


def test_a_harness_sessions_does_not_name_is_not_asked(monkeypatch):
    found = stand_in(monkeypatch)
    put(record(A, harness="other-harness"))
    put(record(B))
    body = run(get(served(), "/api/names"))[1]
    assert [(item["harness"], item["known"]) for item in body["sessions"]] == [
        ("other-harness", None), ("claude", False)]
    assert found.calls == [[("claude", B)]]
    assert run(get(served(), "/api/session", b"session=" + A.encode()))[1] == {"model": None}
    assert found.asked == []


def test_a_names_read_that_raises_is_up_to_the_page_as_error(monkeypatch, caplog):
    stand_in(monkeypatch, fails=PermissionError(13, "Permission denied: '/x/y'"))
    put(record(A))
    with caplog.at_level(logging.ERROR, logger="moxi.cache.page"):
        status, body = run(get(served(), "/api/names"))
        again = run(get(served(), "/api/names"))[1]
    assert status == 200 and again == body
    assert body == {"state": "error", "today": TODAY, "home": str(Path.home()), "sessions": []}
    assert "/x/y" not in json.dumps(body)
    assert len([r for r in caplog.records if r.exc_info and r.exc_info[0] is PermissionError]) == 1
    assert conforms(body, lb_table(page.NAMES))


def test_a_failed_listing_of_the_records_names_nothing(monkeypatch):
    def fail():
        raise RuntimeError("at /x/y")

    found = stand_in(monkeypatch)
    monkeypatch.setattr(api, "clock_records", fail)
    assert run(get(served(), "/api/names"))[1]["state"] == "error"
    assert run(get(served(), "/api/session", b"session=" + A.encode()))[1] == {"model": None}
    assert found.calls == [] and found.asked == []


def test_names_asks_at_most_200_keys_a_call_and_keeps_the_records_order(monkeypatch):
    sessions = [f"s{n:04d}" for n in range(401)]
    facts = {("claude", s): {"name": f"name {s}", "folder": None, "started": None}
             for s in sessions[::2]}
    found = stand_in(monkeypatch, facts=facts)
    for session in sessions:
        put(record(session))
    body = run(get(served(), "/api/names"))[1]
    assert [len(keys) for keys in found.calls] == [200, 200, 1]
    assert [key for keys in found.calls for key in keys] == [("claude", s) for s in sessions]
    assert [item["session"] for item in body["sessions"]] == sessions
    assert [item["known"] for item in body["sessions"][:4]] == [True, False, True, False]
    assert body["sessions"][2]["name"] == "name s0002"
    assert conforms(body, lb_table(page.NAMES))


def test_names_carries_the_date_and_a_value_sessions_gives_that_is_not_its_type(monkeypatch):
    stand_in(monkeypatch,
             facts={("claude", A): {"name": 7, "folder": ["/f"], "started": float("inf")}})
    put(record(A))
    monkeypatch.setattr(pagehandlers, "_today", lambda: "2027-01-16")
    body = run(get(served(), "/api/names"))[1]
    assert body["today"] == "2027-01-16"
    assert body["sessions"] == [{"session": A, "harness": "claude", **UNNAMED, "known": True}]


def test_with_no_record_names_is_up_and_empty_and_asks_nothing(monkeypatch):
    found = stand_in(monkeypatch)
    assert run(get(served(), "/api/names"))[1] == {"state": "up", "today": TODAY,
                                                   "home": str(Path.home()), "sessions": []}
    assert found.calls == []


def test_the_model_read_sends_the_model_alone(monkeypatch, caplog):
    found = stand_in(monkeypatch, opened={"model": "claude-model-b", "reply": {"text": "secret"},
                                          "subagents": [], "folder": "/x/y"})
    put(record(A))
    status, body = run(get(served(), "/api/session", b"session=" + A.encode()))
    assert (status, body) == (200, {"model": "claude-model-b"})
    assert found.asked == [{"harness": "claude", "session": A}]
    for opened in (None, {"model": None}, {"model": ""}, {"model": 3}):
        found.opened = opened
        assert run(get(served(), "/api/session", b"session=" + A.encode()))[1] == {"model": None}
    found.fails = OSError("at /x/y")
    with caplog.at_level(logging.ERROR, logger="moxi.cache.page"):
        assert run(get(served(), "/api/session", b"session=" + A.encode())) == (
            200, {"model": None})
        assert run(get(served(), "/api/session", b"session=" + A.encode()))[1] == {"model": None}
    assert len([r for r in caplog.records if r.exc_info and r.exc_info[0] is OSError]) == 1
    assert conforms(body, lb_table(page.SESSION))


def test_the_model_read_needs_its_session():
    assert run(get(served(), "/api/session"))[0] == 400
    assert run(get(served(), "/api/session", b"session=" + b"x" * (page.ID_MAX + 1)))[0] == 400


def marks(shape, path: str = "") -> dict[str, str]:
    """Every mark in a declared shape, by where it sits: `sessions[].folder`."""
    found = {path: shape.mark} if shape.mark else {}
    if shape.type == "table":
        for key, item in shape.items.items():
            found.update(marks(item, f"{path}.{key}" if path else key))
    elif shape.type == "list":
        found.update(marks(shape.items, f"{path}[]"))
    return found


def test_the_names_mark_the_name_redact_and_each_folder_path():
    assert marks(lb_table(page.NAMES)) == {
        "home": "path", "sessions[].name": "redact", "sessions[].folder": "path"}
    assert marks(lb_table(page.SESSION)) == {}
    assert marks(lb_table({"now": None, **page.CLOCKS})) == {}
    exported = {route["path"]: route for route in loopback().export(page.PAGE)["routes"]}
    assert exported["/api/names"]["returns"]["home"]["mark"] == "path"

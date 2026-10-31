"""The launcher's page server's state and stream, in process through loopback's strict app:
the live launches, `/api/state`, `/api/sessions`, and `/api/events` with its two topics
and the poller that feeds them."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import sys
import time
import uuid

import pytest
from moxi import proc
from test_page import PORT, TOKEN, get, served

from moxi_launcher import first, page, pagehandlers, slots, store

SID = "5f6e7d8c-9b0a-4c1d-8e2f-3a4b5c6d7e8f"
OLD = "2026-01-01T00:00:00.000000Z"
UNEXPECTED = pagehandlers.UNEXPECTED


@pytest.fixture(autouse=True)
def fresh_handlers(monkeypatch):
    """Each test logs each failure class anew and starts its own feed; no other module
    reads as installed, and turns is not asked."""
    monkeypatch.setattr(pagehandlers, "_logged", set())
    monkeypatch.setattr(pagehandlers, "_current", None)
    monkeypatch.setattr(slots, "links", lambda name: False)
    monkeypatch.setattr(slots, "api", lambda: None)
    monkeypatch.setattr(first, "turns_api", lambda quiet=False: None)


@pytest.fixture
def conn():
    connection = store.connect()
    yield connection
    connection.close()


def me() -> proc.ProcId:
    return proc.identity(os.getpid())


def launched(conn, request_id: str, *, at: str | None = None, session_id: str | None = None,
             state: str = "first-sent", pid: int | None = None,
             started: float | None = None, target: str = "slot", **more) -> str:
    """A launch's row in `state` whose process is `pid` (started at `started`)."""
    sid = session_id or str(uuid.uuid4())
    store.insert(conn, request_id=request_id, actor="ann", harness="claude", target=target,
                 cwd="/work", values={"model": "opus"}, argv=["claude"], env={},
                 posture="operator", session_id=sid, slot="3" if target == "slot" else None,
                 at=at or store.now(), **more)
    facts = {k: v for k, v in (("pid", pid), ("pid_started", started)) if v is not None}
    if state == "failed":
        store.set_state(conn, request_id, "failed", reason="spawn-failed", **facts)
    elif state != "spawning":
        store.set_state(conn, request_id, "started", **facts)
        for step in ("sending", "first-sent")[:("started", "sending", "first-sent").index(
                state)]:
            store.set_state(conn, request_id, step)
    elif facts:
        store.set_facts(conn, request_id, **facts)
    return sid


def living(conn, request_id: str, **kw) -> str:
    """A launch whose process is this test's own."""
    ident = me()
    return launched(conn, request_id, pid=ident.pid, started=ident.started, **kw)


def ran(conn, request_id: str, outcome: str, *, kind: str = "relaunch", mode: str = "director",
        word: str | None = None, detail: str | None = None, at: str | None = None):
    ident = me()
    run = store.insert_run(conn, kind=kind, mode=mode, request_id=request_id,
                           session_id=store.get(conn, request_id).session_id,
                           actor="launcher", state="acting",
                           job=f"proc:{ident.pid}@{ident.started:.6f}", at=at)
    store.end_run(conn, run.run_id, outcome, word=word, detail=detail)
    return store.get_run(conn, run.run_id)


# --- the live launches ----------------------------------------------------------------------

def test_a_live_launch_is_one_not_failed_whose_process_runs(conn):
    ident = me()
    living(conn, "alive", at="2026-01-05T00:00:00.000000Z")
    launched(conn, "no-start", pid=ident.pid, at="2026-01-04T00:00:00.000000Z")
    launched(conn, "reused", pid=ident.pid, started=ident.started - 1000,
             at="2026-01-03T00:00:00.000000Z")
    living(conn, "failed", state="failed", at="2026-01-02T00:00:00.000000Z")
    launched(conn, "spawning", state="spawning", at="2026-01-01T00:00:00.000000Z")
    launched(conn, "claimed", state="started", at="2025-12-31T00:00:00.000000Z")
    launched(conn, "unread", state="started", at="2025-12-30T00:00:00.000000Z")
    conn.execute("UPDATE launches SET endpoint = ? WHERE request_id = 'claimed'",
                 (f"{ident.pid}@{ident.started}",))
    conn.execute("UPDATE launches SET endpoint = 'not an endpoint' WHERE request_id = 'unread'")
    assert [row.request_id for row in pagehandlers.live(conn)] == [
        "alive", "no-start", "claimed"]


def test_a_live_launch_is_known_by_its_newest_session_id(conn):
    living(conn, "r1", session_id=SID)
    store.learn_session(conn, "r1", "0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d", "clear")
    [row] = pagehandlers.live(conn)
    assert row.newest_session_id == "0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d"


# --- /api/state -----------------------------------------------------------------------------

def state_of(session: str | None = None) -> dict:
    status, said, _ = get("/api/state", "" if session is None else f"session={session}")
    assert status == 200, said
    assert said["state"] == "up" and said["detail"] is None, said
    return said


@pytest.mark.parametrize("outcome,word,red", [
    ("unconfirmed", "failed", True), ("at-dialog", "failed", True),
    ("first-turn-not-sent", "failed", True), ("relaunch-failed", "failed", True),
    ("blocked", "blocked", True), ("not-started", None, False),
    ("relaunched", None, False),
])
def test_a_sessions_state_is_red_as_the_launcher_reads_it(conn, outcome, word, red):
    living(conn, "r1", session_id=SID)
    run = ran(conn, "r1", outcome, word="w", detail="d")
    said = state_of(SID)
    assert said["refused"] is None
    [got] = said["sessions"]
    assert set(got) == set(page.SESSION_STATE.items)
    assert (got["session_id"], got["request_id"]) == (SID, "r1")
    assert (got["red"], got["word"]) == (red, word)
    assert got["run_id"] == (run.run_id if red else None)
    assert got["relaunch"]["run"]["outcome"] == outcome


def test_a_later_run_that_ended_well_clears_red(conn):
    living(conn, "r1", session_id=SID)
    ran(conn, "r1", "failed", word="gone", at=OLD)
    ran(conn, "r1", "nudged", kind="keep-warm", mode="ask")
    assert state_of(SID)["sessions"][0]["red"] is False


def test_an_ended_sessions_state_is_answered_too(conn):
    launched(conn, "r1", session_id=SID, pid=1, started=1.0)
    ran(conn, "r1", "unconfirmed")
    [got] = state_of(SID)["sessions"]
    assert got["red"] is True and got["word"] == "failed"


def test_a_session_no_launch_holds_is_answered_not_launched(conn):
    living(conn, "r1", session_id=SID)
    said = state_of("0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d")
    assert said["sessions"] == []
    assert said["refused"]["code"] == "not-launched"
    assert said["refused"]["row"] is None and said["refused"]["hint"]


def test_with_no_launch_log_a_session_is_not_launched_and_no_log_is_made():
    said = state_of(SID)
    assert said["refused"]["code"] == "not-launched"
    assert state_of() == {"state": "up", "detail": None, "sessions": [], "refused": None}
    assert not store.path().exists()


def test_with_no_session_the_state_of_every_live_launch_newest_first(conn):
    living(conn, "old", at="2026-01-01T00:00:00.000000Z")
    living(conn, "new", at="2026-01-02T00:00:00.000000Z")
    launched(conn, "ended", pid=1, started=1.0, at="2026-01-03T00:00:00.000000Z")
    ran(conn, "old", "blocked", kind="clear", mode="expiry", word="draft")
    said = state_of()
    assert [(s["request_id"], s["red"]) for s in said["sessions"]] == [("new", False),
                                                                      ("old", True)]
    assert said["refused"] is None


def test_a_failed_state_read_says_only_the_class(conn, monkeypatch):
    living(conn, "r1", session_id=SID)

    def broken(*args, **kwargs):
        raise RuntimeError("/home/someone/private/launcher.db is locked")

    monkeypatch.setattr(pagehandlers.state, "of_launch", broken)
    status, said, raw = get("/api/state", f"session={SID}")
    assert status == 200 and said["state"] == "error"
    assert said["detail"] == UNEXPECTED.format("RuntimeError")
    assert said["sessions"] == [] and said["refused"] is None
    assert b"someone" not in raw


def broken_for(monkeypatch, request_id: str) -> None:
    """`state.of_launch` raising, with a path in its words, for one launch only."""
    real = pagehandlers.state.of_launch

    def of_launch(conn, row, **kw):
        if row.request_id == request_id:
            raise RuntimeError("/home/someone/private/cache.db is locked")
        return real(conn, row, **kw)

    monkeypatch.setattr(pagehandlers.state, "of_launch", of_launch)


def test_one_launchs_failed_state_read_marks_only_its_entry(conn, monkeypatch):
    living(conn, "old", session_id=SID, at="2026-01-01T00:00:00.000000Z")
    living(conn, "new", at="2026-01-02T00:00:00.000000Z")
    ran(conn, "new", "blocked", kind="clear", mode="expiry", word="draft")
    broken_for(monkeypatch, "old")
    status, said, raw = get("/api/state")
    assert status == 200 and said["state"] == "up" and said["detail"] is None
    new, old = said["sessions"]
    assert (new["request_id"], new["red"], new["unreadable"]) == ("new", True, None)
    assert new["relaunch"] is not None and new["keep_warm"] is not None
    assert (old["request_id"], old["session_id"]) == ("old", SID)
    assert old["unreadable"] == pagehandlers.UNREADABLE.format("RuntimeError")
    assert old["red"] is False
    assert (old["relaunch"], old["recycle"], old["keep_warm"]) == (None, None, None)
    assert b"someone" not in raw


# --- /api/sessions --------------------------------------------------------------------------

def test_sessions_list_each_launch_with_its_liveness_newest_first(conn):
    living(conn, "r1", session_id=SID, at="2026-01-01T00:00:00.000000Z")
    launched(conn, "r2", pid=1, started=1.0, at="2026-01-02T00:00:00.000000Z")
    launched(conn, "r3", target="headless", pid=1, started=1.0,
             at="2026-01-03T00:00:00.000000Z")
    status, said, _ = get("/api/sessions")
    assert status == 200 and said["state"] == "up"
    rows = said["sessions"]
    assert [r["request_id"] for r in rows] == ["r3", "r2", "r1"]
    assert all(set(r) == set(page.PAST.items) for r in rows)
    assert [(r["live"], r["resumable"]) for r in rows] == [(False, False), (False, True),
                                                           (True, False)]
    assert rows[0]["why"].endswith("ran headless, so there is nothing to resume it into")
    assert rows[2]["why"] == f"session {SID} is live"
    _, said, _ = get("/api/sessions", "limit=1&before=2026-01-03T00:00:00.000000Z")
    assert [r["request_id"] for r in said["sessions"]] == ["r2"]


def test_with_no_launch_log_sessions_read_empty():
    assert get("/api/sessions")[1] == {"state": "up", "detail": None, "sessions": []}
    assert not store.path().exists()


def test_a_query_the_sessions_list_cant_take_is_refused(conn):
    status, said, _ = get("/api/sessions", "limit=0")
    assert status == 422 and said["refused"] == "limit is 1 or more"


# --- /api/events ----------------------------------------------------------------------------

def scope(path: str, query: bytes) -> dict:
    return {
        "type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1", "server": ("127.0.0.1", PORT),
        "client": ("127.0.0.1", 50000), "scheme": "http", "method": "GET",
        "root_path": "", "path": path, "raw_path": path.encode("ascii"),
        "query_string": query,
        "headers": [(b"host", b"127.0.0.1:%d" % PORT), (b"x-moxi-token", TOKEN.encode())],
        "state": {},
    }


class Stream:
    """One open stream on the app: its events as `(name, data)`, heartbeats left out."""

    def __init__(self, app, query: bytes = b"topics=sessions,launches"):
        self.status = 0
        self._gone = asyncio.Event()
        self._chunks: asyncio.Queue[bytes] = asyncio.Queue()
        self._buffer = b""
        first_ = [True]

        async def receive():
            if first_[0]:
                first_[0] = False
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

    async def until(self, name: str, check, timeout: float) -> dict:
        async with asyncio.timeout(timeout):
            while True:
                got, data = await self.next(timeout)
                if got == name and check(data):
                    return data

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


def requests(data: dict, key: str) -> list[str]:
    return [row["request_id"] for row in data[key]]


def quick(monkeypatch, *, regather: float = 60.0) -> None:
    """A poller that reads the data version every 0.05 s and regathers on its own only
    after `regather` seconds, so a change that comes sooner came through the version."""
    monkeypatch.setattr(pagehandlers, "TICK", 0.05)
    monkeypatch.setattr(pagehandlers, "REGATHER", regather)


def test_each_topic_sends_its_value_first_then_each_change_a_store_write_makes(conn,
                                                                                monkeypatch):
    quick(monkeypatch)
    living(conn, "r1", session_id=SID, at="2026-01-01T00:00:00.000000Z")

    async def go():
        stream = Stream(served())
        try:
            seen = dict([await stream.next(3), await stream.next(3)])
            assert stream.status == 200
            assert set(seen) == {"sessions", "launches"}
            assert seen["sessions"]["state"] == "up" and seen["sessions"]["detail"] is None
            assert requests(seen["sessions"], "sessions") == ["r1"]
            assert seen["sessions"]["sessions"][0]["red"] is False
            assert set(seen["launches"]) == {"state", "detail", "launches"}
            assert requests(seen["launches"], "launches") == ["r1"]
            # A new launch, written by another connection, moves the data version.
            launched(conn, "r2", pid=1, started=1.0)
            data = await stream.until("launches", lambda d: len(d["launches"]) == 2, 3)
            assert requests(data, "launches") == ["r2", "r1"]
            # A run on the live launch ending red changes its state.
            ran(conn, "r1", "first-turn-not-sent")
            data = await stream.until("sessions", lambda d: d["sessions"][0]["red"], 3)
            assert data["sessions"][0]["word"] == "failed"
        finally:
            await stream.close()

    asyncio.run(go())


def test_a_write_that_changes_neither_topic_sends_nothing(conn, monkeypatch):
    quick(monkeypatch)
    living(conn, "r1", session_id=SID)

    async def go():
        stream = Stream(served())
        try:
            await stream.next(3)
            await stream.next(3)
            store.add_event(conn, "note", {"said": "nothing the page shows"}, request_id="r9")
            assert await stream.none_within(0.5)
        finally:
            await stream.close()

    asyncio.run(go())


def test_a_launch_log_made_after_the_stream_opened_is_seen(monkeypatch):
    quick(monkeypatch)

    async def go():
        stream = Stream(served())
        try:
            seen = dict([await stream.next(3), await stream.next(3)])
            assert seen["sessions"]["sessions"] == [] and seen["launches"]["launches"] == []
            made = store.connect()
            try:
                living(made, "r1")
            finally:
                made.close()
            data = await stream.until("launches", lambda d: d["launches"], 3)
            assert requests(data, "launches") == ["r1"]
        finally:
            await stream.close()

    asyncio.run(go())


def test_a_session_whose_process_ends_leaves_the_topic_with_no_store_write(conn,
                                                                         monkeypatch):
    """Nothing writes to the store when a session's process ends: the period's regather
    sees it go."""
    quick(monkeypatch, regather=0.3)
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        ident = None
        deadline = time.monotonic() + 5
        while ident is None and time.monotonic() < deadline:
            ident = proc.identity(child.pid)
        launched(conn, "r1", pid=child.pid, started=ident.started)

        async def go():
            stream = Stream(served(), b"topics=sessions")
            try:
                _, data = await stream.next(3)
                assert requests(data, "sessions") == ["r1"]
                child.kill()
                child.wait(5)
                data = await stream.until("sessions", lambda d: not d["sessions"], 3)
                assert data == {"state": "up", "detail": None, "sessions": []}
            finally:
                await stream.close()

        asyncio.run(go())
    finally:
        child.kill()
        child.wait(5)


def test_a_topic_publishes_its_error_and_then_up_once_its_read_recovers(conn, monkeypatch):
    quick(monkeypatch, regather=0.1)
    living(conn, "r1", session_id=SID)
    raising = [PermissionError(13, "Permission denied: '/home/someone'")]
    real = pagehandlers.live

    def flaky(c):
        if raising:
            raise raising[0]
        return real(c)

    monkeypatch.setattr(pagehandlers, "live", flaky)

    async def go():
        stream = Stream(served(), b"topics=sessions")
        try:
            _, data = await stream.next(3)
            assert data == {"state": "error", "detail": UNEXPECTED.format("PermissionError"),
                            "sessions": []}
            raising.clear()
            data = await stream.until("sessions", lambda d: d["state"] == "up", 3)
            assert requests(data, "sessions") == ["r1"]
        finally:
            await stream.close()

    asyncio.run(go())


def test_one_launchs_failed_state_read_leaves_the_rest_of_the_topic(conn, monkeypatch):
    quick(monkeypatch)
    living(conn, "old", session_id=SID, at="2026-01-01T00:00:00.000000Z")
    living(conn, "new", at="2026-01-02T00:00:00.000000Z")
    broken_for(monkeypatch, "old")

    async def go():
        stream = Stream(served(), b"topics=sessions")
        try:
            _, data = await stream.next(3)
            assert data["state"] == "up" and data["detail"] is None
            assert requests(data, "sessions") == ["new", "old"]
            assert [s["unreadable"] for s in data["sessions"]] == [
                None, pagehandlers.UNREADABLE.format("RuntimeError")]
        finally:
            await stream.close()

    asyncio.run(go())


def test_the_poller_outlives_a_gather_and_a_version_read_that_raise(conn, monkeypatch,
                                                                    caplog):
    quick(monkeypatch, regather=0.1)
    gathers, reads = [], []

    def gather():
        gathers.append(1)
        if len(gathers) <= 2:
            raise RuntimeError("the gather broke")
        return {"state": "up", "detail": None, "sessions": []}

    real_read = pagehandlers._Version.read

    def read(self):
        reads.append(1)
        if len(reads) <= 2:
            raise OSError("the version read broke")
        return real_read(self)

    monkeypatch.setattr(pagehandlers, "gather_sessions", gather)
    monkeypatch.setattr(pagehandlers._Version, "read", read)

    async def go():
        stream = Stream(served(), b"topics=sessions")
        try:
            assert await stream.next(3) == ("sessions", {"state": "up", "detail": None,
                                                         "sessions": []})
        finally:
            await stream.close()

    with caplog.at_level(logging.ERROR, logger="moxi.launcher.page"):
        asyncio.run(go())
    logged = [r.exc_info[0] for r in caplog.records if r.exc_info]
    assert sorted(t.__name__ for t in logged) == ["OSError", "RuntimeError"]


def test_the_poller_starts_with_the_first_stream_and_keeps_running_after_it(monkeypatch):
    quick(monkeypatch)

    async def go():
        assert pagehandlers._current is None
        stream = Stream(served())
        await stream.next(3)
        feed = pagehandlers._current
        await stream.close()
        await asyncio.sleep(0.3)
        assert feed is pagehandlers._current and not feed.task.done()
        feed.task.cancel()
        feed.version.close()

    asyncio.run(go())


def test_a_second_stream_gets_each_current_value_first(conn, monkeypatch):
    quick(monkeypatch)
    living(conn, "r1")

    async def go():
        app = served()
        first_stream = Stream(app)
        try:
            await first_stream.next(3)
            await first_stream.next(3)
            second = Stream(app)
            try:
                seen = dict([await second.next(1), await second.next(1)])
                assert requests(seen["sessions"], "sessions") == ["r1"]
                assert requests(seen["launches"], "launches") == ["r1"]
            finally:
                await second.close()
        finally:
            await first_stream.close()

    asyncio.run(go())

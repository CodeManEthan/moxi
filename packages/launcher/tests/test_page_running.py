"""The launches running now on the launcher's page server, in process through loopback's
strict app: `gather_running`, `GET /api/running` and the stream's topic `running`."""

from __future__ import annotations

import asyncio
import os

import pytest
from moxi import proc
from moxi_dev.testing import PROC_WAIT_S
from test_page import _send, get, loopback, served
from test_page_state import Stream, quick

from moxi_launcher import api, first, page, pagehandlers, slots, store

SID = "5f6e7d8c-9b0a-4c1d-8e2f-3a4b5c6d7e8f"
CWD = "/srv/work/proj-a"
#: The keys of every entry, in the order they are declared.
ENTRY = ["request_id", "harness", "session_id", "name", "identity", "where", "started_at",
         "model", "effort", "cache", "waits", "covered", "coverable", "activity"]


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
    conn = store.connect()
    yield conn
    conn.close()


def launched(conn, request_id: str, *, live: bool = True, target: str = "slot",
             cwd: str = CWD, at: str | None = None, values: dict | None = None,
             **more) -> None:
    """A Claude launch's row with a session id of its own, its process this test's own
    when `live`; its values `{"model": "opus"}` unless `values` are given."""
    me = proc.identity(os.getpid())
    store.insert(conn, request_id=request_id, by="alice", harness="claude", target=target,
                 cwd=cwd, values={"model": "opus"} if values is None else values,
                 argv=["claude"], env={},
                 posture="operator",
                 session_id=more.pop("session_id", f"session-{request_id}"),
                 slot="3" if target == "slot" else None, at=at or store.now(), **more)
    store.set_state(conn, request_id, "started", pid=me.pid,
                    pid_started=me.started if live else 1.0)


# --- the gather ------------------------------------------------------------------------------

def test_with_no_launch_log_nothing_runs():
    assert pagehandlers.gather_running() == {"state": "up", "detail": None, "count": 0,
                                             "sessions": []}


def test_a_live_launch_is_sent_with_its_dir_and_nothing_joined(conn):
    launched(conn, "r1", identity="planner", project_key="proj-a", session_id=SID,
             at="2026-03-01T10:00:00.000000Z")
    value = pagehandlers.gather_running()
    assert value["state"] == "up" and value["count"] == 1
    [entry] = value["sessions"]
    assert list(entry) == ENTRY
    assert entry["request_id"] == "r1" and entry["harness"] == "claude"
    assert entry["session_id"] == SID and entry["identity"] == "planner"
    assert entry["name"] == api.running()[0].name
    assert entry["where"] == {"target": "slot", "slot": "3", "project_key": "proj-a",
                              "folder": "proj-a"}
    assert entry["started_at"] == "2026-03-01T10:00:00.000000Z"
    assert (entry["model"], entry["effort"]) == ("opus", None)
    assert (entry["cache"], entry["waits"], entry["covered"], entry["activity"]) == (
        None, None, False, None)


def test_the_model_and_effort_are_the_launchs_own_and_null_where_it_set_none(conn):
    launched(conn, "both", values={"model": "sonnet", "effort": "high"},
             at="2026-03-01T10:00:00.000000Z")
    launched(conn, "unset", values={}, at="2026-03-01T09:00:00.000000Z")
    launched(conn, "empty", values={"model": "", "effort": ""},
             at="2026-03-01T08:00:00.000000Z")
    by_id = {entry["request_id"]: entry for entry in pagehandlers.gather_running()["sessions"]}
    assert (by_id["both"]["model"], by_id["both"]["effort"]) == ("sonnet", "high")
    assert (by_id["unset"]["model"], by_id["unset"]["effort"]) == (None, None)
    assert (by_id["empty"]["model"], by_id["empty"]["effort"]) == (None, None)


def test_kept_facts_never_cover_the_model_or_effort(conn):
    launched(conn, "r1", values={"model": "sonnet", "effort": "low"})
    [entry] = pagehandlers.gather_running(
        {"r1": {"model": "joined", "effort": "joined"}})["sessions"]
    assert (entry["model"], entry["effort"]) == ("sonnet", "low")


def test_the_dir_is_the_dirs_last_segment_and_no_path_is_sent(conn):
    launched(conn, "r1", target="headless", cwd="/srv/work/proj-b/sub/")
    [entry] = pagehandlers.gather_running()["sessions"]
    assert entry["where"] == {"target": "headless", "slot": None, "project_key": None,
                              "folder": "sub"}
    assert "cwd" not in entry
    assert "/srv" not in repr(entry)


def test_only_live_launches_are_sent_and_counted_newest_first(conn):
    launched(conn, "old", at="2026-03-01T09:00:00.000000Z")
    launched(conn, "ended", live=False, at="2026-03-01T10:00:00.000000Z")
    launched(conn, "new", at="2026-03-01T11:00:00.000000Z")
    value = pagehandlers.gather_running()
    assert value["count"] == 2
    assert [entry["request_id"] for entry in value["sessions"]] == ["new", "old"]


def test_kept_facts_are_laid_over_their_launch_by_request_id(conn):
    launched(conn, "r1", at="2026-03-01T09:00:00.000000Z")
    launched(conn, "r2", at="2026-03-01T10:00:00.000000Z")
    facts = {"cache": {"state": "warm", "until": "2026-03-01T10:30:00Z", "estimated": False},
             "waits": {"kind": "subagents", "count": 2}, "covered": True,
             "activity": {"at": "2026-03-01T10:05:00Z", "by": "you", "text": "Go on."}}
    value = pagehandlers.gather_running({"r1": {**facts, "where": "not a joined fact"},
                                         "gone": facts})
    by_id = {entry["request_id"]: entry for entry in value["sessions"]}
    assert {key: by_id["r1"][key] for key in facts} == facts
    assert by_id["r1"]["where"]["folder"] == "proj-a"
    assert by_id["r2"]["covered"] is False and by_id["r2"]["cache"] is None
    assert value["count"] == 2


def test_a_read_that_fails_is_an_error_with_nothing_counted(monkeypatch):
    def broken(**_):
        raise PermissionError(13, "Permission denied: '/home/someone'")

    monkeypatch.setattr(api, "running", broken)
    assert pagehandlers.gather_running() == {
        "state": "error", "detail": pagehandlers.UNEXPECTED.format("PermissionError"),
        "count": 0, "sessions": []}


# --- the route and its shape -----------------------------------------------------------------

def test_the_route_answers_the_running_launches_with_no_path(conn):
    launched(conn, "r1", identity="planner")
    launched(conn, "ended", live=False)
    status, data, raw = get("/api/running")
    assert status == 200
    assert data["state"] == "up" and data["count"] == 1
    assert [entry["request_id"] for entry in data["sessions"]] == ["r1"]
    assert list(data["sessions"][0]) == ENTRY
    assert CWD.encode() not in raw and b"/srv" not in raw


def test_the_route_is_the_gateways_and_marks_the_activity_text():
    exported = loopback().export(page.PAGE)
    routes = {route["path"]: route for route in exported["routes"]}
    assert [path for path, route in routes.items() if route["gateway"]] == ["/api/running"]
    assert routes["/api/running"]["kind"] == "read"
    entry = page.RUNNING["sessions"].items.items
    assert entry["activity"].items["text"].mark == "redact"
    assert "cwd" not in entry and set(entry["where"].items) == {
        "target", "slot", "project_key", "folder"}
    assert all(out.mark is None for out in entry["where"].items.values())


def test_the_model_and_effort_go_to_the_gateway_as_plain_nullable_names():
    entry = page.RUNNING["sessions"].items.items
    for key in ("model", "effort"):
        assert entry[key].type == "string" and entry[key].nullable and entry[key].mark is None
    [route] = [r for r in loopback().export(page.PAGE)["routes"]
               if r["path"] == "/api/running"]
    exported = route["returns"]["sessions"]["items"]["items"]
    for key in ("model", "effort"):
        assert exported[key] == {"type": "string", "mark": None, "nullable": True,
                                 "items": None}


def test_coverable_is_a_nullable_cap_the_gateway_sends_the_phone_as_it_is():
    entry = page.RUNNING["sessions"].items.items
    coverable = entry["coverable"]
    assert coverable.type == "table" and coverable.nullable
    assert set(coverable.items) == {"cap"} and coverable.items["cap"].type == "int"
    # The gateway cuts only what the exported shape marks: neither the table nor its cap
    # is marked, so the phone gets `coverable` as the desk does.
    [route] = [r for r in loopback().export(page.PAGE)["routes"]
               if r["path"] == "/api/running"]
    exported = route["returns"]["sessions"]["items"]["items"]
    assert route["gateway"] is True
    assert exported["coverable"]["mark"] is None and exported["coverable"]["nullable"]
    assert exported["coverable"]["items"] == {
        "cap": {"type": "int", "mark": None, "nullable": False, "items": None}}


def test_a_call_of_the_route_starts_the_feed(conn, monkeypatch):
    quick(monkeypatch)
    launched(conn, "r1")
    real = pagehandlers.gather_running
    calls = []

    def counted(joined=None):
        calls.append(joined)
        return real(joined)

    monkeypatch.setattr(pagehandlers, "gather_running", counted)

    async def go():
        status, _ = await _send(served(), "GET", "/api/running", b"", None)
        assert status == 200 and len(calls) >= 1
        feed = pagehandlers._current
        assert feed is not None and feed.loop is asyncio.get_running_loop()
        assert not feed.task.done() and not feed.join_task.done()
        # The poller it started gathers the topic too, with no stream opened, and lays
        # the joiner's kept facts over it.
        async with asyncio.timeout(3):
            while len(calls) < 2:
                await asyncio.sleep(0.05)
        assert all(isinstance(joined, dict) for joined in calls)
        feed.task.cancel()
        feed.join_task.cancel()

    asyncio.run(go())


def test_the_stream_sends_the_running_topic_and_each_change(conn, monkeypatch):
    quick(monkeypatch)
    launched(conn, "r1", at="2026-03-01T09:00:00.000000Z")

    async def go():
        stream = Stream(served(), b"topics=running")
        try:
            name, data = await stream.next(PROC_WAIT_S)
            assert name == "running"
            assert data["count"] == 1 and data["sessions"][0]["request_id"] == "r1"
            launched(conn, "r2", at="2026-03-01T10:00:00.000000Z")
            data = await stream.until("running", lambda d: d["count"] == 2, PROC_WAIT_S)
            assert [e["request_id"] for e in data["sessions"]] == ["r2", "r1"]
        finally:
            await stream.close()

    asyncio.run(go())

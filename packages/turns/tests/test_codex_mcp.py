"""The Codex host's MCP settle against a scripted app-server and an injected clock: the
reload at a `ready` and on `mcp.reload`, the waits on Codex's startup notifications, the
one deadline, joining and queueing, and the doctor records."""

from __future__ import annotations

import threading
import time

import pytest

from moxi_turns import codex_protocol as proto
from moxi_turns.codex import CodexHarness
from moxi_turns.codex_mcp import LIST, RELOAD, McpSettle, rendered
from moxi_turns.client import Session
from moxi_turns.endpoint import Endpoint
from moxi_turns.listeners import Registry
from test_codex_host import FakeAppServer, FakeClient


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class App:
    """The app-server's MCP side: each server's `runtimeStatus`, the account's own
    `codex_apps` beside them, and hooks a test sets on a list read or the reload."""

    def __init__(self, **status):
        self.status = dict(status)
        self.calls: list[str] = []
        self.settle: McpSettle | None = None
        self.on_list = None
        self.on_reload = None
        self.lists = 0

    def call(self, method, params, timeout):
        self.calls.append(method)
        if method == LIST:
            assert params == {"threadId": "t"}
            self.lists += 1
            if self.on_list is not None:
                self.on_list(self, timeout)
            data = [{"name": "codex_apps", "runtimeStatus": "failed", "tools": []}]
            data += [{"name": name, "runtimeStatus": status,
                      "tools": [{"name": "probe_echo"}]}
                     for name, status in self.status.items() if status != "absent"]
            return {"data": data, "nextCursor": None}
        if method == RELOAD:
            assert params is None
            if self.on_reload is not None:
                self.on_reload(self)
            return {}
        raise AssertionError(method)

    def note(self, name, status, *, after=0.0, then=None):
        """Codex's startup notification for `name`, `after` seconds from now, with the
        server's status set to `then` first."""
        def send():
            if then is not None:
                self.status[name] = then
            self.settle.notified({"threadId": "t", "name": name, "status": status,
                                  "error": None, "failureReason": None})
        if after:
            threading.Timer(after, send).start()
        else:
            send()


def _settle(app, names, *, clock=None, thread_id=None, tick=0.01):
    records, cleared = {}, []

    def record(text, key, fix):
        assert fix == "relaunch the session"
        records[key] = text

    def clear(key):
        records.pop(key, None)
        cleared.append(key)

    settle = McpSettle(names, app.call, thread_id or (lambda: "t"), record=record,
                       clear=clear, clock=clock or time.monotonic, tick=tick)
    app.settle = settle
    return settle, records, cleared


def _advance_when(clock, condition, by=11.0):
    """Move the clock past the deadline once `condition()` holds, on a thread."""
    def run():
        deadline = time.monotonic() + 5
        while not condition() and time.monotonic() < deadline:
            time.sleep(0.01)
        clock.now += by
    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread


def _wait_for(condition, timeout=5.0):
    deadline = time.monotonic() + timeout
    while not condition() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert condition()


def test_the_rendered_servers_are_the_thread_configs():
    assert rendered({"config": {"mcp_servers": {"comms": {}, "b": {}}}}) == ("comms", "b")
    assert rendered({"config": {}}) == rendered({}) == ()


def test_a_server_failed_at_the_ready_gets_one_reload_and_a_late_ready_ends_the_wait():
    app = App(comms="failed", other="connected")
    settle, records, _ = _settle(app, ["comms", "other"])
    # Thread start's `failed`, still recorded, must not end the wait after the reload.
    app.note("comms", "failed")

    def reload(app):
        app.status["comms"] = "starting"      # answered {} before the server settles
        app.note("comms", "ready", after=0.1, then="connected")
    app.on_reload = reload
    answer = settle.reload()
    assert answer == {"reloaded": True, "servers": {"comms": "connected",
                                                    "other": "connected"}}
    # (a), then one read after the late ready: the wait did not end early on the cached
    # failed (which would have read `starting` and read again).
    assert app.calls == [LIST, RELOAD, LIST]
    assert settle.reloads == 1 and records == {}


def test_a_connected_server_and_codex_apps_get_no_reload():
    app = App(comms="connected")
    settle, records, _ = _settle(app, ["comms"])
    # codex_apps reads failed in the list and is not counted; tools are never read.
    assert settle.reload() == {"reloaded": False, "servers": {"comms": "connected"}}
    assert app.calls == [LIST] and records == {}


def test_starting_not_started_and_null_are_waited_out_on_notifications_alone():
    app = App(a="starting", b="notStarted", c=None, d="absent")
    settle, records, _ = _settle(app, ["a", "b", "c", "d"])
    for name in "abcd":
        app.note(name, "ready", after=0.15, then="connected")
    answer = settle.reload()
    assert answer["servers"] == dict.fromkeys("abcd", "connected")
    # One read, a wait with no list polling, one read after it.
    assert app.calls == [LIST, LIST]


def test_a_final_status_is_never_waited_on():
    app = App(a="cancelled", b="authenticationRequired", c="disabled")
    settle, records, _ = _settle(app, ["a", "b", "c"])
    started = time.monotonic()
    answer = settle.reload()
    assert time.monotonic() - started < 1
    assert answer == {"reloaded": False, "servers": {"a": "cancelled",
                                                     "b": "authenticationRequired",
                                                     "c": "disabled"}}
    assert app.calls == [LIST] and records == {}


def test_a_read_still_starting_after_the_wait_is_waited_on_again():
    # Codex's second startup round: the re-read shows `starting` again.
    app = App(comms="starting")
    settle, _, _ = _settle(app, ["comms"])

    def on_list(app, timeout):
        if app.lists == 2:
            app.note("comms", "ready", after=0.1, then="connected")
    app.on_list = on_list
    app.note("comms", "ready", after=0.1)       # status still `starting` at the re-read
    assert settle.reload()["servers"] == {"comms": "connected"}
    assert app.calls == [LIST, LIST, LIST]


def test_a_failure_after_waiting_out_starting_is_reloaded():
    app = App(comms="starting")
    settle, _, _ = _settle(app, ["comms"])
    app.note("comms", "failed", after=0.05, then="failed")
    app.on_reload = lambda app: app.note("comms", "ready", after=0.05, then="connected")
    assert settle.reload() == {"reloaded": True, "servers": {"comms": "connected"}}
    assert app.calls == [LIST, LIST, RELOAD, LIST]


def test_a_server_that_never_settles_is_answered_at_the_deadline_and_recorded():
    clock = Clock()
    app = App(comms="starting")
    settle, records, _ = _settle(app, ["comms"], clock=clock)
    _advance_when(clock, lambda: app.lists == 1)
    answer = settle.reload()
    assert answer == {"reloaded": False, "servers": {"comms": "starting"}}
    assert app.calls == [LIST]          # nothing is sent once the deadline passed
    assert records == {"mcp:t:comms": "t: MCP server comms stays starting"}


def test_the_deadline_after_a_reload_answers_reloaded_and_records_failed():
    clock = Clock()
    app = App(comms="failed")
    settle, records, _ = _settle(app, ["comms"], clock=clock)
    _advance_when(clock, lambda: settle.reloads == 1)
    assert settle.reload() == {"reloaded": True, "servers": {"comms": "failed"}}
    assert app.calls == [LIST, RELOAD]
    assert records == {"mcp:t:comms": "t: MCP server comms stays failed"}


def test_the_deadline_covers_a_list_read_in_flight():
    clock = Clock()
    app = App(comms="connected")
    settle, records, _ = _settle(app, ["comms"], clock=clock)

    def slow(app, timeout):
        # The request races the deadline: its own bound is the time left.
        assert timeout == pytest.approx(10.0)
        clock.now += 11.0
        raise proto.NoReply("Codex app-server timed out on mcpServerStatus/list")
    app.on_list = slow
    answer = settle.reload()
    # Never read: unsettled, and recorded as such.
    assert answer == {"reloaded": False, "servers": {"comms": None}}
    assert records == {"mcp:t:comms": "t: MCP server comms stays unsettled"}


def test_two_calls_before_the_first_read_make_one_settle():
    app = App(comms="connected")
    gate = threading.Event()
    settle, _, _ = _settle(app, ["comms"], thread_id=lambda: gate.wait(5) and "t")
    answers = []
    callers = [threading.Thread(target=lambda: answers.append(settle.reload()))
               for _ in range(2)]
    for caller in callers:
        caller.start()
    time.sleep(0.1)
    gate.set()
    for caller in callers:
        caller.join(5)
    assert answers == [{"reloaded": False, "servers": {"comms": "connected"}}] * 2
    assert settle.settles == 1 and app.calls == [LIST]


def test_a_call_after_the_first_read_waits_for_one_more_settle():
    app = App(comms="failed")
    settle, _, _ = _settle(app, ["comms"])
    in_read, go_on = threading.Event(), threading.Event()

    def first_read(app, timeout):
        if app.lists == 1:
            in_read.set()
            go_on.wait(5)
    app.on_list = first_read
    # The ready's settle reads `failed` and reloads before the caller registered.
    app.on_reload = lambda app: app.note("comms", "failed", after=0.02)
    settle.ready()
    assert in_read.wait(5)
    answers = []
    late = [threading.Thread(target=lambda: answers.append(settle.reload()))
            for _ in range(2)]
    for caller in late:
        caller.start()
    settle.ready()                       # a ready joins the running settle
    time.sleep(0.05)
    go_on.set()
    for caller in late:
        caller.join(5)
    # The two late calls share one queued settle, which reloads again for them.
    assert settle.settles == 2 and settle.reloads == 2
    assert answers == [{"reloaded": True, "servers": {"comms": "failed"}}] * 2


def test_a_compactions_two_readys_give_at_most_one_reload_each():
    app = App(comms="failed")
    settle, records, _ = _settle(app, ["comms"])
    app.on_reload = lambda app: app.note("comms", "failed", after=0.02)
    settle.ready()                       # PostCompact
    settle.ready()                       # SessionStart compact while it runs: joins
    _wait_for(lambda: settle._running is None)
    assert (settle.settles, settle.reloads) == (1, 1)
    settle.ready()                       # SessionStart compact after it ended
    _wait_for(lambda: settle.settles == 2 and settle._running is None)
    assert settle.reloads == 2
    assert app.calls.count(RELOAD) == 2
    assert records == {"mcp:t:comms": "t: MCP server comms stays failed"}


def test_a_record_clears_when_the_server_reads_connected_or_reports_ready_or_on_exit():
    app = App(comms="failed", other="starting")
    clock = Clock()
    settle, records, cleared = _settle(app, ["comms", "other"], clock=clock)
    # The deadline passes while `other` is waited on, before any reload.
    _advance_when(clock, lambda: app.lists == 1)
    settle.reload()
    assert records == {"mcp:t:comms": "t: MCP server comms stays failed",
                       "mcp:t:other": "t: MCP server other stays starting"}
    # A later settle reads comms connected: its record clears.
    app.status.update(comms="connected", other="starting")
    _advance_when(clock, lambda: app.lists == 2)
    settle.reload()
    assert set(records) == {"mcp:t:other"}
    # A late ready for the unsettled one clears it too.
    app.note("other", "ready")
    assert records == {}
    _advance_when(clock, lambda: app.lists == 3)
    settle.reload()
    assert set(records) == {"mcp:t:other"}
    settle.close()
    assert records == {}


def test_the_first_settle_clears_an_earlier_hosts_record_for_a_connected_server():
    app = App(comms="connected")
    settle, records, cleared = _settle(app, ["comms"])
    settle.reload()
    settle.reload()
    assert cleared == ["mcp:t:comms"]


def test_no_rendered_server_sends_nothing():
    app = App()
    settle, _, _ = _settle(app, [])
    settle.ready()
    assert settle.reload() == {"reloaded": False, "servers": {}}
    assert app.calls == []


def test_a_closing_host_ends_a_settle_without_a_record():
    app = App(comms="starting")
    settle, records, _ = _settle(app, ["comms"])
    threading.Timer(0.1, settle.close).start()
    assert settle.reload() == {"reloaded": False, "servers": {"comms": "starting"}}
    assert records == {}


# -- through the host ---------------------------------------------------------------------

def _host(turns_env, app):
    harness = None

    def clients(**kwargs):
        return FakeClient(harness, **kwargs)

    harness = CodexHarness(
        turns_env, window=False, ping=["/env/bin/moxi-turns-ping"],
        client_factory=clients, app_server_factory=FakeAppServer,
        options={"config": {"mcp_servers": {"comms": {"url": "http://127.0.0.1:1/mcp"}}}})
    records = {}
    harness.mcp = McpSettle(["comms"], harness._app_call, lambda: harness.session_id,
                            record=lambda text, key, fix: records.__setitem__(key, text),
                            clear=lambda key: records.pop(key, None))
    thread_ids = []

    def fake_mcp(method, params, timeout):
        if method == LIST:
            thread_ids.append(params["threadId"])
            params = {**params, "threadId": "t"}
        return app.call(method, params, timeout)
    harness.fake_mcp = fake_mcp
    app.settle = harness.mcp
    endpoint = Endpoint(harness, session_id=None, registry=Registry(),
                        env=harness.env).open()
    harness.start()
    return endpoint, harness, records, thread_ids


def test_the_host_settles_at_its_load_and_offers_mcp_reload(turns_env):
    app = App(comms="connected")
    endpoint, harness, records, thread_ids = _host(turns_env, app)
    session = Session(endpoint.address, endpoint.session(), directory=endpoint.dir)
    try:
        assert {"mcp-token", "usage"} <= session.capabilities
        _wait_for(lambda: harness.mcp.settles == 1 and harness.mcp._running is None)
        assert thread_ids == ["thread-1"]
        # The notification for the session's thread reaches the settle; a subagent's
        # thread's does not.
        app.status["comms"] = "failed"
        app.on_reload = lambda app: (app.status.update(comms="starting"),
                                     app.note("comms", "ready", after=0.1,
                                              then="connected"))
        answer = session.mcp_reload()
        assert answer == {"reloaded": True, "servers": {"comms": "connected"}}
        harness._on_notification("mcpServer/startupStatus/updated",
                                 {"threadId": "child", "name": "comms",
                                  "status": "ready"})
        assert harness.mcp._notes == {}
        harness._on_notification("mcpServer/startupStatus/updated",
                                 {"threadId": "thread-1", "name": "comms",
                                  "status": "ready"})
        assert harness.mcp._notes == {"comms": "ready"}
    finally:
        session.close()
        endpoint.stop("the test ended")


def test_a_hook_ready_settles_and_a_loads_session_start_does_not(turns_env):
    app = App(comms="connected")
    endpoint, harness, records, thread_ids = _host(turns_env, app)
    try:
        _wait_for(lambda: harness.mcp.settles == 1 and harness.mcp._running is None)
        server = harness.hook_server
        server._dispatch({"event": "SessionStart", "session_id": "thread-1",
                          "source": "startup"}, None)
        time.sleep(0.1)
        assert harness.mcp.settles == 1
        for event, source in (("PostCompact", "compact"), ("SessionStart", "compact")):
            server._dispatch({"event": event, "session_id": "thread-1",
                              "source": source}, None)
            _wait_for(lambda: harness.mcp._running is None)
        assert harness.mcp.settles in (2, 3)
        assert app.calls.count(RELOAD) == 0
    finally:
        endpoint.stop("the test ended")

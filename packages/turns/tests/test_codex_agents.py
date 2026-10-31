"""Subagent liveness on the Codex host: collab tool calls tracked from the item stream,
rebuilt from the thread's history at a resumed load, and background terminals listed as
`shell`, against synthetic app-server answers."""

from __future__ import annotations

import sys
import threading
import time

import pytest

from moxi_turns import codex_protocol as proto
from moxi_turns.client import Session
from moxi_turns.results import Agent, Refused
from test_codex_commands import CompactClient
from test_codex_host import _host

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="no pipe server yet")


def collab(receivers, states=None, *, sender="thread-1", tool="spawnAgent",
           status="completed", item_id="call-1"):
    """A `collabAgentToolCall` item as Codex sends it."""
    return {"type": "collabAgentToolCall", "id": item_id, "tool": tool,
            "status": status, "senderThreadId": sender,
            "receiverThreadIds": list(receivers),
            "agentsStates": {k: {"status": v, "message": None}
                             for k, v in (states or {}).items()}}


def item(app, event, payload, thread="thread-1"):
    app.on_notification(f"item/{event}", {"threadId": thread, "turnId": "turn-x",
                                          "item": payload}, None)


def session(endpoint):
    return Session(endpoint.address, endpoint.session(), directory=endpoint.dir)


def wait_for(pred, timeout=3.0):
    deadline = time.monotonic() + timeout
    while not pred() and time.monotonic() < deadline:
        time.sleep(0.02)
    return pred()


@pytest.fixture
def host(turns_env):
    made = []

    def make():
        endpoint, harness, app = _host(turns_env)
        made.append(endpoint)
        return session(endpoint), endpoint, harness, app

    yield make
    for endpoint in made:
        endpoint.stop("the test ended")


def test_the_codex_host_offers_agents(host):
    s, endpoint, harness, app = host()
    assert "agents" in s.capabilities
    a = s.agents()
    assert a.complete and a.entries == ()
    assert s.facts().agents == 0


@pytest.mark.parametrize("status, state, final", [
    ("pendingInit", "pending", False), ("running", "running", False),
    ("completed", "done", True), ("errored", "failed", True),
    ("interrupted", "stopped", True), ("shutdown", "stopped", True),
    ("notFound", "unknown", False), ("hibernating", "unknown", None)])
def test_each_collab_status_maps_to_a_state(host, status, state, final):
    s, endpoint, harness, app = host()
    item(app, "completed", collab(["sub-1"], {"sub-1": status}))
    a = s.agents()
    assert a.complete
    assert a.entries == (Agent("sub-1", "subagent", state, final),)
    assert s.facts().agents == (1 if state in ("pending", "running") else 0)


def test_a_spawn_reads_pending_until_its_state_comes_then_its_latest_notice(host):
    s, endpoint, harness, app = host()
    item(app, "started", collab(["sub-1"], status="inProgress"))   # no state yet
    assert s.agents().entries == (Agent("sub-1", "subagent", "pending", None),)
    assert s.facts().agents == 1                       # spawning counts as busy
    item(app, "completed", collab(["sub-1"], {"sub-1": "running"}))
    assert s.agents().entries[0].state == "running"
    # A later call on it (a wait) carries its newer notice.
    item(app, "completed", collab(["sub-1"], {"sub-1": "completed"}, tool="wait",
                                  item_id="call-2"))
    assert s.agents().entries == (Agent("sub-1", "subagent", "done", True),)
    assert s.facts().agents == 0


def test_a_subagents_own_spawn_names_its_parent_and_foreign_threads_are_ignored(host):
    s, endpoint, harness, app = host()
    item(app, "completed", collab(["sub-1"], {"sub-1": "running"}))
    item(app, "completed", collab(["sub-2"], {"sub-2": "running"}, sender="sub-1"),
         thread="sub-1")
    item(app, "completed", collab(["x-9"], {"x-9": "running"}, sender="other"),
         thread="other")
    a = s.agents()
    assert [(e.id, e.parent_id) for e in a.entries] == [("sub-1", None), ("sub-2", "sub-1")]


def test_terminals_are_shell_entries_and_count_as_busy(host):
    s, endpoint, harness, app = host()
    harness.ws.call = _terminals(harness.ws, ["p1", "p2"])
    a = s.agents()
    assert a.complete
    assert a.entries == (Agent("p1", "shell", "running", False),
                         Agent("p2", "shell", "running", False))
    assert s.facts().agents == 2


def _terminals(ws, ids):
    original = ws.call

    def call(method, params=None, timeout=None):
        if method == "thread/backgroundTerminals/list":
            if isinstance(ids, BaseException):
                raise ids
            return {"data": [{"processId": p} for p in ids]}
        return original(method, params, timeout)
    return call


def test_a_failed_terminal_list_is_unknown_never_zero(host):
    s, endpoint, harness, app = host()
    item(app, "completed", collab(["sub-1"], {"sub-1": "completed"}))
    harness.ws.call = _terminals(harness.ws, proto.CodexError(-32603, "internal error"))
    a = s.agents()
    assert a.complete is False and a.busy() is None
    assert [e.id for e in a.entries] == ["sub-1"]       # what is known is still listed
    f = s.facts()
    assert f.agents is None
    d = s.deliver("x", only_if_idle=True, refuse_agents=True)
    assert d.wait_started(3) == Refused("busy-agents", d.id)


def test_a_final_notice_inside_an_open_turn_reads_final_before_the_turn_ends(host):
    s, endpoint, harness, app = host()
    d = s.deliver("go")
    assert d.wait_started(3).turn_id == "turn-1"
    item(app, "completed", collab(["sub-1"], {"sub-1": "running"}))
    assert s.agents().entries[0].final is False and s.facts().agents == 1
    # The subagent's final notice arrives while the main-loop turn runs on.
    item(app, "completed", collab(["sub-1"], {"sub-1": "completed"}, tool="wait",
                                  item_id="call-2"))
    f, a = s.facts(), s.agents()
    assert f.turn_open and f.turn.turn_id == "turn-1"
    assert a.entries == (Agent("sub-1", "subagent", "done", True),)
    assert f.agents == 0
    app.complete()


def test_a_spawned_childs_own_turn_neither_opens_nor_closes_the_sessions_turn(host):
    # The live sequence: the session's turn spawns a subagent, whose own thread raises
    # turn/started, status changes and turn/completed on the same connection.
    s, endpoint, harness, app = host()
    events = []
    threading.Thread(target=lambda: events.extend(s.watch()), daemon=True).start()
    time.sleep(0.2)
    d = s.deliver("spawn one and wait")
    assert d.wait_started(3).turn_id == "turn-1"
    item(app, "completed", collab(["sub-1"], {"sub-1": "pendingInit"}))
    child = "sub-1"
    app.on_notification("thread/status/changed",
                        {"threadId": child, "status": {"type": "active"}}, None)
    app.on_notification("turn/started", {"threadId": child, "turn": {"id": "child-t"}},
                        None)
    f = s.facts()
    assert f.turn_open and f.turn.turn_id == "turn-1"
    app.on_notification("thread/status/changed",
                        {"threadId": child, "status": {"type": "idle"}}, None)
    app.on_notification("turn/completed", {"threadId": child, "turn": {
        "id": "child-t", "status": "completed"}}, None)
    f = s.facts()
    assert f.turn_open and f.turn.turn_id == "turn-1"
    assert harness._grace is None           # the child's idle set no backstop
    item(app, "completed", collab(["sub-1"], {"sub-1": "completed"}, tool="wait",
                                  item_id="call-2"))
    app.complete()
    ended = d.wait_ended(3)
    assert (ended.turn_id, ended.outcome) == ("turn-1", "answer")
    assert wait_for(lambda: any(type(e).__name__ == "TurnEnded" for e in events))
    turns = [(type(e).__name__, e.turn_number) for e in events
             if type(e).__name__ in ("TurnStarted", "TurnEnded")]
    assert len(turns) == 2 and turns[0][0] == "TurnStarted" and turns[1][0] == "TurnEnded"
    assert turns[0][1] == turns[1][1]


def test_turn_notifications_naming_the_sessions_thread_still_count(host):
    s, endpoint, harness, app = host()
    app.on_notification("turn/started", {"threadId": "thread-1",
                                         "turn": {"id": "own-t"}}, None)
    assert wait_for(lambda: s.facts().turn_open)
    app.on_notification("turn/completed", {"threadId": "thread-1", "turn": {
        "id": "own-t", "status": "completed"}}, None)
    assert wait_for(lambda: not s.facts().turn_open)


def activity(thread_id="sub-v2", kind="started", item_id="act-1"):
    """A `subAgentActivity` item, a spawn as Codex's multi_agent_v2 reports it."""
    return {"type": "subAgentActivity", "id": item_id, "agentThreadId": thread_id,
            "agentPath": "/root/worker", "kind": kind}


def test_a_live_subagent_activity_item_leaves_the_list_not_complete_for_good(host):
    s, endpoint, harness, app = host()
    item(app, "completed", collab(["sub-1"], {"sub-1": "running"}))
    item(app, "started", activity())
    a = s.agents()
    assert a.complete is False and a.busy() is None
    assert a.entries == (Agent("sub-1", "subagent", "running", False),)
    assert s.facts().agents is None
    # A final notice for every readable entry does not make it whole again.
    item(app, "completed", activity(kind="completed"))
    item(app, "completed", collab(["sub-1"], {"sub-1": "completed"}, tool="wait",
                                  item_id="call-2"))
    a = s.agents()
    assert a.complete is False
    assert a.entries == (Agent("sub-1", "subagent", "done", True),)
    assert s.facts().agents is None
    d = s.deliver("x", only_if_idle=True, refuse_agents=True)
    assert d.wait_started(3) == Refused("busy-agents", d.id)


def test_a_subagent_activity_item_of_a_foreign_thread_is_ignored(host):
    s, endpoint, harness, app = host()
    item(app, "started", activity(), thread="other")
    assert s.agents().complete and s.facts().agents == 0


def test_a_rebuild_that_finds_a_subagent_activity_item_stays_not_complete(host):
    s, endpoint, harness, app = host()
    harness.fake_history = [
        collab(["sub-1"], {"sub-1": "completed"}),
        activity(),
    ]
    _reconnect(harness, app)
    a = s.agents()
    assert a.complete is False
    assert a.entries == (Agent("sub-1", "subagent", "done", True),)
    assert s.facts().agents is None
    # A later load whose history no longer shows it does not clear it.
    harness.fake_history = [collab(["sub-1"], {"sub-1": "completed"})]
    _reconnect(harness, harness.ws)
    assert s.agents().complete is False and s.facts().agents is None


def test_a_history_of_collab_items_only_stays_complete(host):
    s, endpoint, harness, app = host()
    harness.fake_history = [collab(["sub-1"], {"sub-1": "completed"})]
    _reconnect(harness, app)
    assert s.agents().complete is True and s.facts().agents == 0


def _reconnect(harness, first):
    harness.app_server.alive = False
    first.connected = False
    assert wait_for(lambda: harness.ws is not None and harness.ws is not first
                    and not harness.loading())


def test_a_resumed_load_rebuilds_the_record_from_the_threads_history(host):
    s, endpoint, harness, app = host()
    item(app, "completed", collab(["gone-1"], {"gone-1": "running"}))
    # While the host was away Codex spawned and finished subagents it never saw.
    harness.fake_history = [
        collab(["sub-1"], {"sub-1": "running"}),
        collab(["sub-2"], {"sub-2": "pendingInit"}, item_id="call-2"),
        collab(["sub-1"], {"sub-1": "completed"}, tool="wait", item_id="call-3"),
        {"type": "agentMessage", "id": "m", "text": "hi"},
    ]
    _reconnect(harness, app)
    assert ("thread/read", {"threadId": "thread-1", "includeTurns": True}) \
        in harness.ws.calls
    a = s.agents()
    assert a.complete
    assert a.entries == (Agent("sub-1", "subagent", "done", True),
                         Agent("sub-2", "subagent", "pending", False))


def test_a_history_that_cannot_be_read_leaves_the_list_not_complete(host):
    s, endpoint, harness, app = host()
    item(app, "completed", collab(["sub-1"], {"sub-1": "running"}))
    harness.fake_read_error = proto.CodexError(-32603, "rollout unreadable")
    _reconnect(harness, app)
    a = s.agents()
    assert a.complete is False and [e.id for e in a.entries] == ["sub-1"]
    assert s.facts().agents is None
    # The next resumed load that reads it is whole again.
    harness.fake_read_error = None
    harness.fake_history = [collab(["sub-1"], {"sub-1": "completed"})]
    _reconnect(harness, harness.ws)
    assert s.agents().complete is True and s.facts().agents == 0


@pytest.fixture
def compacting(turns_env, monkeypatch):
    """A Codex host whose client compacts and lists terminals (`CompactClient`)."""
    from moxi_turns.codex import CodexHarness
    from moxi_turns.endpoint import Endpoint
    from test_codex_host import FakeAppServer

    monkeypatch.setenv("MOXI_TURNS_COMMANDS", "compact")
    made = []

    def make():
        harness = None
        clients = []

        def factory(**kwargs):
            client = CompactClient(harness, **kwargs)
            clients.append(client)
            return client

        harness = CodexHarness(turns_env, window=False, ping=["/env/bin/moxi-turns-ping"],
                               client_factory=factory, app_server_factory=FakeAppServer)
        endpoint = Endpoint(harness, session_id=None).open()
        made.append(endpoint)
        harness.start()
        return session(endpoint), harness, clients[0]

    yield make
    for endpoint in made:
        endpoint.stop("the test ended")


def test_a_failed_terminal_list_refuses_a_plain_compact_but_not_one_that_stops_them(
        compacting):
    s, harness, app = compacting()
    error = proto.CodexError(-32603, "internal error")
    app.list_errors = [error]
    assert s.command("compact") == Refused("busy-agents")
    # With stop_shells the terminals leave the check, so the known subagents decide it.
    app.list_errors = [error] * 3
    result = s.command("compact", stop_shells=True, timeout=15)
    assert result.shells is not None and result.shells.complete is False


def test_a_live_subagent_holds_a_compact_that_stops_shells(compacting):
    s, harness, app = compacting()
    item(app, "completed", collab(["sub-1"], {"sub-1": "running"}))
    assert s.command("compact", stop_shells=True) == Refused("busy-agents")


def test_a_subagent_activity_item_refuses_compact_with_no_subagent_running(compacting):
    s, harness, app = compacting()
    item(app, "started", activity())
    item(app, "completed", activity(kind="completed"))
    assert s.facts().agents is None
    assert s.command("compact") == Refused("busy-agents")
    assert s.command("compact", stop_shells=True) == Refused("busy-agents")
    d = s.deliver("x", only_if_idle=True, refuse_agents=True)
    assert d.wait_started(3) == Refused("busy-agents", d.id)

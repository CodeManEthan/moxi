"""The listeners turns calls, against session events shaped like turns' `SessionEvent`:
the claim, the ready recorder, the Codex seat listener and the handoff lines, a session
started from inside a launched one among them."""

from __future__ import annotations

import ast
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

import pytest
from moxi import modules, proc
from moxi.paths import Layout

from moxi_launcher import (handoffs, listen_codex, listen_handoff, listen_ready, listeners,
                           store)
from moxi_launcher.compose import common

CLAUDE_ID = "5f0c3a1e-7f43-4c8e-9e0a-1d2b3c4d5e6f"
CLEARED_ID = "9a8b7c6d-1111-4222-8333-444455556666"
NESTED_ID = "00000000-aaaa-4bbb-8ccc-dddddddddddd"
THREAD_ID = "thr-0001"
PARENT = "1000@1.000000"
NESTED = "2000@2.000000"
#: This test process, named as an endpoint: one whose process is alive.
LIVE = f"{os.getpid()}@{proc.identity(os.getpid()).started:.6f}"
#: The launch's own `claude` in these tests: this test process, by pid and start time.
SEAT = proc.identity(os.getpid())
#: A replacing endpoint of the launched session.
NEW = "3000@3.000000"


@dataclass
class Event:
    """A session event as turns hands one to a listener."""

    event: str
    harness: str
    session_id: str | None
    env: dict[str, str] = field(default_factory=dict)
    source: str | None = None
    reason: str | None = None


@pytest.fixture(autouse=True)
def without_notes(monkeypatch):
    """notes not installed, whatever this environment holds: these tests mean the launcher
    alone, with its handoffs in the launcher's own folder."""
    real = modules.available
    monkeypatch.setattr(modules, "available", lambda name: name != "notes" and real(name))


@pytest.fixture(autouse=True)
def projects_never_read(monkeypatch):
    """Every listener test fails if the code asks for projects: a listener takes the
    project key from the launch row and has no time for a projects read. The rule binds
    the launcher's own code. With notes installed, the handoff listener asks notes for its
    folder, and notes' answer may read projects: that read counts in notes' own start
    cost, so the tests with notes present use `stand_in_notes`, whose answer reads
    nothing."""
    asked = []
    real_available, real_load = modules.available, modules.load

    def available(name):
        if name == "projects":
            asked.append(("available", name))
        return real_available(name)

    def load(name, *, interface=None):
        if name == "projects":
            asked.append(("load", name))
        return real_load(name, interface=interface)

    monkeypatch.setattr(modules, "available", available)
    monkeypatch.setattr(modules, "load", load)
    yield
    assert asked == [], "a listener asked for projects"


@pytest.fixture
def endpoint(monkeypatch):
    """Which endpoint process the next event comes from, and that endpoint's parent
    process: the launched session's endpoint by default, its parent unknown. `use(name,
    parent)` names another; a replacing endpoint of the launched session passes
    `parent=SEAT`."""
    current = {"id": PARENT, "parent": None}
    monkeypatch.setattr(listeners, "endpoint_id", lambda: current["id"])
    monkeypatch.setattr(listeners, "endpoint_parent", lambda: current["parent"])

    def use(name, parent=None):
        current["id"], current["parent"] = name, parent
    return use


@pytest.fixture
def dead() -> str:
    """An endpoint name whose process has ended: a child's pid and start time, read while
    it ran."""
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        ident = proc.identity(child.pid)
    finally:
        child.kill()
        child.wait()
    assert ident is not None and not proc.alive(ident)
    return f"{ident.pid}@{ident.started:.6f}"


@pytest.fixture(autouse=True)
def overlap_drained():
    """Each test starts with no wait for a held endpoint under way and no id remembered
    from a `load`, and ends once the wait it started, if any, has handled or dropped its
    queue."""
    listen_ready._drainer = None
    listen_ready._load_added = None
    yield
    if listen_ready._drainer is not None:
        listen_ready._drainer.join(timeout=10)
        assert not listen_ready._drainer.is_alive()
    assert listen_ready._queue == []


def drained():
    """Wait for the overlap wait under way to finish."""
    assert listen_ready._drainer is not None
    listen_ready._drainer.join(timeout=10)


@pytest.fixture
def sibling():
    """A live endpoint of the launched session that a replacing one is a sibling of: a
    child of this test process, which plays the launch's `claude`. `.end()` ends it."""
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    ident = proc.identity(child.pid)

    def end():
        child.kill()
        child.wait()
    yield SimpleNamespace(name=f"{ident.pid}@{ident.started:.6f}", end=end)
    end()


@pytest.fixture
def conn():
    conn = store.connect()
    yield conn
    conn.close()


def stamp(request_id, **more):
    return {"MOXI_LAUNCHER_REQUEST": request_id, **more}


def launch_claude(conn, request_id="r1", **overrides):
    args = {"request_id": request_id, "by": "someone", "harness": "claude",
            "target": "slot", "cwd": "/work", "values": {"mode": "interactive"},
            "argv": ["claude"], "env": stamp(request_id), "posture": "operator",
            "session_id": CLAUDE_ID, "preset_kind": "seat", "preset_name": "lead"}
    args.update(overrides)
    return store.insert(conn, **args).row


def launch_codex(conn, request_id="c1", mode="host", values=None, **overrides):
    args = {"request_id": request_id, "by": "someone", "harness": "codex",
            "target": "slot", "cwd": "/work", "values": {"mode": mode, **(values or {})},
            "argv": ["moxi-turns-codex-host"], "env": stamp(request_id), "posture": "go",
            "preset_kind": "seat", "preset_name": "builder"}
    args.update(overrides)
    return store.insert(conn, **args).row


def seated(conn, request_id="r1"):
    """Record this test process as the launch's own process, its `claude`."""
    store.set_facts(conn, request_id, pid=SEAT.pid, pid_started=SEAT.started)


def load(harness, session_id, request_id, **env):
    return Event("load", harness, session_id, stamp(request_id, **env))


def ready(harness, session_id, request_id, source="startup", **env):
    return Event("ready", harness, session_id, stamp(request_id, **env), source=source)


def start(harness, session_id, request_id, source="startup", **env):
    return Event("start", harness, session_id, stamp(request_id, **env), source=source)


# -- the registration ----------------------------------------------------------------------


def test_the_registration_names_three_rows_in_answer_order():
    assert listeners.LISTENERS == (
        (("ready", "gone", "load"), "moxi_launcher.listen_ready:observe"),
        (("start",), "moxi_launcher.listen_codex:answer"),
        (("start",), "moxi_launcher.listen_handoff:answer"),
    )
    for _, target in listeners.LISTENERS:
        module_name, _, attr = target.partition(":")
        module = __import__(module_name, fromlist=[attr])
        assert callable(getattr(module, attr))


def test_the_registration_module_imports_only_the_standard_library():
    source = Path(listeners.__file__).read_text(encoding="utf-8")
    top = [node for node in ast.parse(source).body
           if isinstance(node, (ast.Import, ast.ImportFrom))]
    names = {alias.name for node in top if isinstance(node, ast.Import)
             for alias in node.names}
    names |= {node.module for node in top if isinstance(node, ast.ImportFrom)}
    assert names <= {"__future__", "os", "threading", "collections.abc"}
    assert not any(isinstance(node, ast.ImportFrom) and node.level for node in top)


def test_the_launcher_registers_in_the_turns_listener_group():
    entries = [e for e in modules.extensions("moxi.turns.listeners") if e.module == "launcher"]
    assert [(e.name, e.target) for e in entries] == [("launcher", "moxi_launcher.listeners")]


def test_the_endpoint_is_named_once_by_its_pid_and_start_time():
    first = listeners.endpoint_id()
    assert first.startswith(f"{__import__('os').getpid()}@")
    assert listeners.endpoint_id() == first


def test_the_endpoint_parent_is_read_once_by_its_pid_and_start_time(monkeypatch):
    first = listeners.endpoint_parent()
    assert first == proc.identity(os.getppid())
    monkeypatch.setattr(proc, "identity", lambda pid: None)
    assert listeners.endpoint_parent() == first


# -- the claim and the ready recorder ------------------------------------------------------


def test_a_claude_ready_claims_its_launch_and_records_ready(conn, endpoint):
    launch_claude(conn)
    listen_ready.observe(ready("claude", CLAUDE_ID, "r1"))
    row = store.get(conn, "r1")
    assert (row.endpoint, row.state) == (PARENT, "ready")
    assert store.session_ids(conn, "r1") == [(CLAUDE_ID, "minted")]


def test_a_clear_and_a_compaction_add_their_ids_to_the_launch(conn, endpoint):
    launch_claude(conn)
    listen_ready.observe(ready("claude", CLAUDE_ID, "r1"))
    listen_ready.observe(ready("claude", CLEARED_ID, "r1", source="clear"))
    listen_ready.observe(ready("claude", "c0mpact-id", "r1", source="compact"))
    assert store.session_ids(conn, "r1") == [
        (CLAUDE_ID, "minted"), (CLEARED_ID, "clear"), ("c0mpact-id", "compact")]
    found = store.resolve(conn, CLEARED_ID)
    assert found.request_id == "r1" and found.newest_session_id == "c0mpact-id"
    assert store.get(conn, "r1").session_id == CLAUDE_ID


def test_a_nested_claude_session_gets_no_claim_no_ids_and_no_state(conn, endpoint):
    launch_claude(conn)
    listen_ready.observe(ready("claude", CLAUDE_ID, "r1"))
    store.set_state(conn, "r1", "sending")
    endpoint(NESTED)
    listen_ready.observe(ready("claude", NESTED_ID, "r1"))
    listen_ready.observe(Event("gone", "claude", NESTED_ID, stamp("r1"), reason="exited"))
    row = store.get(conn, "r1")
    assert (row.endpoint, row.state) == (PARENT, "sending")
    assert store.session_ids(conn, "r1") == [(CLAUDE_ID, "minted")]
    assert store.resolve(conn, NESTED_ID) is None
    assert store.events(conn, "r1") == []


def test_a_nested_session_cannot_claim_even_before_the_parent(conn, endpoint):
    launch_claude(conn)
    endpoint(NESTED)
    listen_ready.observe(ready("claude", NESTED_ID, "r1"))
    assert store.get(conn, "r1").endpoint is None
    endpoint(PARENT)
    listen_ready.observe(ready("claude", CLAUDE_ID, "r1"))
    assert store.get(conn, "r1").endpoint == PARENT


def test_a_second_endpoint_with_the_minted_id_does_not_take_the_launch_while_the_first_lives(
        conn, endpoint):
    launch_claude(conn)
    endpoint(LIVE)
    listen_ready.observe(ready("claude", CLAUDE_ID, "r1"))
    endpoint(NESTED)
    listen_ready.observe(ready("claude", CLAUDE_ID, "r1", source="resume"))
    listen_ready.observe(ready("claude", CLEARED_ID, "r1", source="clear"))
    assert store.get(conn, "r1").endpoint == LIVE
    assert store.session_ids(conn, "r1") == [(CLAUDE_ID, "minted")]
    assert store.events(conn, "r1") == []


def test_a_replaced_endpoint_then_a_clear_moves_the_claim_by_parent(conn, endpoint, dead):
    # Claude Code replaced the endpoint (its plugins reloaded), then the session was
    # cleared: the clear's id is one the launch never knew.
    launch_claude(conn)
    seated(conn)
    endpoint(dead)
    listen_ready.observe(ready("claude", CLAUDE_ID, "r1"))
    endpoint(NEW, parent=SEAT)
    listen_ready.observe(ready("claude", CLEARED_ID, "r1", source="clear"))
    assert store.get(conn, "r1").endpoint == NEW
    [moved] = store.events(conn, "r1")
    assert (moved.kind, moved.session_id) == (store.RECLAIMED, CLEARED_ID)
    assert moved.detail == {"endpoint": NEW, "was": dead, "by": "parent"}
    assert store.session_ids(conn, "r1") == [(CLAUDE_ID, "minted"), (CLEARED_ID, "clear")]
    found = store.resolve(conn, CLEARED_ID)
    assert found.request_id == "r1" and found.newest_session_id == CLEARED_ID


def test_a_replacing_endpoint_load_moves_the_claim_and_writes_no_state(conn, endpoint, dead):
    launch_claude(conn)
    seated(conn)
    assert store.claim(conn, "r1", harness="claude", session_id=CLAUDE_ID,
                       endpoint=dead) is not None
    endpoint(NEW, parent=SEAT)
    listen_ready.observe(load("claude", CLAUDE_ID, "r1"))
    row = store.get(conn, "r1")
    assert (row.endpoint, row.state) == (NEW, "spawning")
    assert [(e.kind, e.detail) for e in store.events(conn, "r1")] == [
        (store.RECLAIMED, {"endpoint": NEW, "was": dead, "by": "parent"})]
    assert store.session_ids(conn, "r1") == [(CLAUDE_ID, "minted")]


@pytest.mark.parametrize("session_id", [CLAUDE_ID, CLEARED_ID, NESTED_ID])
def test_a_nested_endpoint_never_takes_a_held_launch_whatever_id_it_names(
        conn, endpoint, dead, session_id):
    # A session started from inside the launched one: its endpoint's parent is the nested
    # `claude`, another process, even after the launch's own endpoint has ended.
    launch_claude(conn)
    seated(conn)
    endpoint(dead)
    listen_ready.observe(ready("claude", CLAUDE_ID, "r1"))
    listen_ready.observe(ready("claude", CLEARED_ID, "r1", source="clear"))
    endpoint(NESTED, parent=proc.identity(os.getppid()))
    listen_ready.observe(load("claude", session_id, "r1"))
    listen_ready.observe(ready("claude", session_id, "r1", source="resume"))
    listen_ready.observe(ready("claude", NESTED_ID, "r1", source="clear"))
    assert store.get(conn, "r1").endpoint == dead
    assert store.events(conn, "r1") == []
    assert store.session_ids(conn, "r1") == [(CLAUDE_ID, "minted"), (CLEARED_ID, "clear")]
    assert store.resolve(conn, NESTED_ID) is None


def test_an_endpoint_whose_parent_is_unknown_never_takes_a_held_launch(conn, endpoint, dead):
    launch_claude(conn)
    seated(conn)
    endpoint(dead)
    listen_ready.observe(ready("claude", CLAUDE_ID, "r1"))
    endpoint(NEW)
    listen_ready.observe(load("claude", CLAUDE_ID, "r1"))
    assert store.get(conn, "r1").endpoint == dead


def test_a_reused_pid_never_moves_the_claim(conn, endpoint, dead):
    # The parent has the launch's pid but started at another time: another process.
    launch_claude(conn)
    seated(conn)
    endpoint(dead)
    listen_ready.observe(ready("claude", CLAUDE_ID, "r1"))
    endpoint(NEW, parent=proc.ProcId(SEAT.pid, SEAT.started + 30))
    listen_ready.observe(load("claude", CLAUDE_ID, "r1"))
    listen_ready.observe(ready("claude", CLEARED_ID, "r1", source="clear"))
    assert store.get(conn, "r1").endpoint == dead
    assert store.events(conn, "r1") == []


def test_a_row_with_no_pid_started_never_moves(conn, endpoint, dead):
    launch_claude(conn)
    store.set_facts(conn, "r1", pid=SEAT.pid)
    endpoint(dead)
    listen_ready.observe(ready("claude", CLAUDE_ID, "r1"))
    endpoint(NEW, parent=SEAT)
    listen_ready.observe(load("claude", CLAUDE_ID, "r1"))
    listen_ready.observe(ready("claude", CLEARED_ID, "r1", source="clear"))
    assert store.get(conn, "r1").endpoint == dead
    assert store.events(conn, "r1") == []


def test_the_launch_parent_is_compared_by_pid_and_start_time(conn):
    row = launch_claude(conn)
    assert not store.is_launch_parent(row, SEAT)                 # no pid recorded
    seated(conn)
    row = store.get(conn, "r1")
    assert store.is_launch_parent(row, SEAT)
    assert store.is_launch_parent(row, proc.ProcId(SEAT.pid, SEAT.started + 0.5))
    assert not store.is_launch_parent(row, proc.ProcId(SEAT.pid, SEAT.started + 30))
    assert not store.is_launch_parent(row, proc.identity(os.getppid()))
    assert not store.is_launch_parent(row, None)


def test_an_id_recorded_for_another_launch_does_not_reclaim(conn, endpoint, dead):
    launch_claude(conn, "r0", session_id="other-minted-id")
    store.learn_session(conn, "r0", CLEARED_ID, "clear")
    launch_claude(conn)
    endpoint(dead)
    listen_ready.observe(ready("claude", CLAUDE_ID, "r1"))
    endpoint(NESTED)
    listen_ready.observe(ready("claude", CLEARED_ID, "r1", source="startup"))
    assert store.get(conn, "r1").endpoint == dead
    assert store.events(conn, "r1") == []


def test_a_codex_launch_is_reclaimed_only_by_its_thread_id(conn, endpoint, dead):
    launch_codex(conn)
    store.set_facts(conn, "c1", pid=SEAT.pid, pid_started=SEAT.started)
    endpoint(dead)
    listen_ready.observe(ready("codex", THREAD_ID, "c1"))
    # Neither a parent nor an id it doesn't know moves a Codex claim.
    endpoint(NESTED, parent=SEAT)
    listen_ready.observe(ready("codex", "thr-other", "c1"))
    assert store.get(conn, "c1").endpoint == dead
    endpoint(NESTED)
    listen_ready.observe(ready("codex", THREAD_ID, "c1", source="resume"))
    assert store.get(conn, "c1").endpoint == NESTED
    [moved] = store.events(conn, "c1")
    assert moved.detail == {"endpoint": NESTED, "was": dead}


def test_a_codex_launch_is_reclaimed_by_an_id_learned_after_a_clear(conn, endpoint, dead):
    launch_codex(conn)
    endpoint(dead)
    listen_ready.observe(ready("codex", THREAD_ID, "c1"))
    listen_ready.observe(ready("codex", "thr-cleared", "c1", source="clear"))
    endpoint(NESTED)
    listen_ready.observe(ready("codex", "thr-cleared", "c1", source="resume"))
    assert store.get(conn, "c1").endpoint == NESTED


LOADED_ID = "77777777-2222-4333-8444-555566667777"
COMPACTED_ID = "c0c0c0c0-3333-4444-8555-666677778888"


def held_by(conn, name, request_id="r1", session_id=CLAUDE_ID):
    launch_claude(conn, request_id, session_id=session_id)
    seated(conn, request_id)
    assert store.claim(conn, request_id, harness="claude", session_id=session_id,
                       endpoint=name) is not None


def test_a_load_while_the_old_endpoint_ends_moves_the_claim_within_the_wait(
        conn, endpoint, sibling):
    held_by(conn, sibling.name)
    endpoint(NEW, parent=SEAT)
    began = time.monotonic()
    listen_ready.observe(load("claude", LOADED_ID, "r1"))
    assert time.monotonic() - began < 1                  # returns to turns at once
    assert store.get(conn, "r1").endpoint == sibling.name
    threading.Timer(0.3, sibling.end).start()
    drained()
    assert time.monotonic() - began < listen_ready.OVERLAP_WAIT_S
    row = store.get(conn, "r1")
    assert (row.endpoint, row.state) == (NEW, "spawning")
    [moved] = store.events(conn, "r1")
    assert moved.detail == {"endpoint": NEW, "was": sibling.name, "by": "parent"}
    # An id first learned at load (the session was cleared in the gap) says so.
    assert store.session_ids(conn, "r1") == [(CLAUDE_ID, "minted"), (LOADED_ID, "load")]


def test_an_old_endpoint_alive_past_the_wait_keeps_the_claim_until_the_next_ready(
        conn, endpoint, sibling, monkeypatch):
    monkeypatch.setattr(listen_ready, "OVERLAP_WAIT_S", 0.5)
    held_by(conn, sibling.name)
    endpoint(NEW, parent=SEAT)
    listen_ready.observe(load("claude", LOADED_ID, "r1"))
    drained()
    assert store.get(conn, "r1").endpoint == sibling.name
    assert store.session_ids(conn, "r1") == [(CLAUDE_ID, "minted")]
    assert listen_ready._queue == []
    sibling.end()
    listen_ready.observe(ready("claude", CLEARED_ID, "r1", source="clear"))
    row = store.get(conn, "r1")
    assert (row.endpoint, row.state) == (NEW, "ready")
    assert store.session_ids(conn, "r1") == [(CLAUDE_ID, "minted"), (CLEARED_ID, "clear")]


def test_a_clear_inside_the_overlap_is_learned_in_arrival_order(
        conn, endpoint, sibling, monkeypatch):
    # A long step, so the old endpoint ends and a third event arrives before the wait
    # looks again.
    monkeypatch.setattr(listen_ready, "OVERLAP_STEP_S", 1.0)
    held_by(conn, sibling.name)
    endpoint(NEW, parent=SEAT)
    listen_ready.observe(load("claude", CLAUDE_ID, "r1"))
    listen_ready.observe(ready("claude", CLEARED_ID, "r1", source="clear"))
    assert store.session_ids(conn, "r1") == [(CLAUDE_ID, "minted")]
    sibling.end()
    assert store.endpoint_gone(sibling.name)
    listen_ready.observe(ready("claude", COMPACTED_ID, "r1", source="compact"))
    assert len(listen_ready._queue) == 3
    drained()
    row = store.get(conn, "r1")
    assert (row.endpoint, row.state) == (NEW, "ready")
    assert store.session_ids(conn, "r1") == [
        (CLAUDE_ID, "minted"), (CLEARED_ID, "clear"), (COMPACTED_ID, "compact")]
    found = store.resolve(conn, CLEARED_ID)
    assert found.request_id == "r1" and found.newest_session_id == COMPACTED_ID
    assert [e.kind for e in store.events(conn, "r1")] == [store.RECLAIMED]


def test_a_queued_clear_alone_is_the_newest_id(conn, endpoint, sibling):
    held_by(conn, sibling.name)
    endpoint(NEW, parent=SEAT)
    listen_ready.observe(load("claude", CLAUDE_ID, "r1"))
    listen_ready.observe(ready("claude", CLEARED_ID, "r1", source="clear"))
    sibling.end()
    drained()
    found = store.resolve(conn, CLEARED_ID)
    assert found.request_id == "r1" and found.newest_session_id == CLEARED_ID
    assert store.get(conn, "r1").state == "ready"


@pytest.mark.parametrize("overlapping", [False, True])
def test_a_load_and_a_ready_of_one_endpoint_arriving_at_once(
        conn, endpoint, dead, overlapping):
    # turns calls each listener on its own thread, so the two may run at the same time.
    for n in range(8):
        request_id, minted, cleared = f"r{n}", f"minted-{n}", f"cleared-{n}"
        if overlapping:
            child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
            ident = proc.identity(child.pid)
            held_by(conn, f"{ident.pid}@{ident.started:.6f}", request_id, minted)
        else:
            held_by(conn, dead, request_id, minted)
        endpoint(NEW, parent=SEAT)
        barrier = threading.Barrier(2)

        def arrive(event, barrier=barrier):
            barrier.wait()
            listen_ready.observe(event)

        threads = [threading.Thread(target=arrive, args=(e,)) for e in (
            load("claude", minted, request_id),
            ready("claude", cleared, request_id, source="clear"))]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        if overlapping:
            child.kill()
            child.wait()
            drained()
            listen_ready._drainer = None
        row = store.get(conn, request_id)
        assert (row.endpoint, row.state) == (NEW, "ready")
        assert [e.kind for e in store.events(conn, request_id)] == [store.RECLAIMED]
        assert store.session_ids(conn, request_id) == [(minted, "minted"), (cleared, "clear")]


@pytest.mark.parametrize("load_first", [True, False])
def test_a_ready_after_its_own_endpoints_load_of_the_same_id_names_its_source(
        conn, endpoint, dead, load_first):
    # The two arrive in either order; the `ready`'s source says how the id began.
    held_by(conn, dead)
    endpoint(NEW, parent=SEAT)
    events = [load("claude", LOADED_ID, "r1"),
              ready("claude", LOADED_ID, "r1", source="clear")]
    for event in events if load_first else events[::-1]:
        listen_ready.observe(event)
    assert store.session_ids(conn, "r1") == [(CLAUDE_ID, "minted"), (LOADED_ID, "clear")]
    found = store.resolve(conn, CLAUDE_ID)
    assert found.request_id == "r1" and found.newest_session_id == LOADED_ID


def test_a_ready_of_an_id_another_endpoint_loaded_leaves_its_source(conn, endpoint, dead):
    held_by(conn, dead)
    endpoint(NEW, parent=SEAT)
    listen_ready.observe(load("claude", LOADED_ID, "r1"))
    endpoint("3001@3.000000", parent=SEAT)
    listen_ready.observe(ready("claude", LOADED_ID, "r1", source="resume"))
    assert store.get(conn, "r1").endpoint == "3001@3.000000"
    assert store.session_ids(conn, "r1") == [(CLAUDE_ID, "minted"), (LOADED_ID, "load")]


def test_a_live_held_endpoint_that_is_not_a_sibling_waits_for_nothing(conn, endpoint):
    # The held endpoint runs, but its parent is not the launch's claude.
    held_by(conn, LIVE)
    endpoint(NEW, parent=SEAT)
    listen_ready.observe(load("claude", CLAUDE_ID, "r1"))
    listen_ready.observe(ready("claude", CLEARED_ID, "r1", source="clear"))
    assert listen_ready._drainer is None and listen_ready._queue == []
    assert store.get(conn, "r1").endpoint == LIVE
    assert store.session_ids(conn, "r1") == [(CLAUDE_ID, "minted")]


def test_a_nested_endpoint_beside_a_live_sibling_neither_queues_nor_waits(
        conn, endpoint, sibling):
    held_by(conn, sibling.name)
    endpoint(NESTED, parent=proc.identity(os.getppid()))
    listen_ready.observe(load("claude", CLAUDE_ID, "r1"))
    listen_ready.observe(ready("claude", NESTED_ID, "r1", source="clear"))
    assert listen_ready._drainer is None and listen_ready._queue == []
    sibling.end()
    assert store.get(conn, "r1").endpoint == sibling.name
    assert store.session_ids(conn, "r1") == [(CLAUDE_ID, "minted")]


def test_a_start_never_queues(conn, endpoint, sibling):
    held_by(conn, sibling.name)
    endpoint(NEW, parent=SEAT)
    assert listen_handoff.answer(start("claude", CLAUDE_ID, "r1")) is None
    assert listen_ready._drainer is None and listen_ready._queue == []


def test_the_held_endpoint_is_gone_only_when_its_process_has_ended(dead):
    assert store.endpoint_gone(dead)
    assert not store.endpoint_gone(LIVE)
    assert not store.endpoint_gone(str(os.getpid()))        # a pid alone, alive
    assert store.endpoint_gone(dead.partition("@")[0]) == (
        proc.identity(int(dead.partition("@")[0])) is None)
    assert not store.endpoint_gone("not-an-endpoint")


def test_a_codex_host_ready_claims_and_learns_the_thread_id(conn, endpoint):
    launch_codex(conn)
    listen_ready.observe(ready("codex", THREAD_ID, "c1"))
    row = store.get(conn, "c1")
    assert (row.endpoint, row.state, row.session_id) == (PARENT, "ready", THREAD_ID)
    assert store.session_ids(conn, "c1") == [(THREAD_ID, "startup")]
    assert store.resolve(conn, THREAD_ID).request_id == "c1"


def test_a_codex_launch_the_sender_learned_claims_only_by_that_id(conn, endpoint):
    launch_codex(conn)
    store.learn_session(conn, "c1", THREAD_ID, "sender")
    endpoint(NESTED)
    listen_ready.observe(ready("codex", "thr-other", "c1"))
    assert store.get(conn, "c1").endpoint is None
    endpoint(PARENT)
    listen_ready.observe(ready("codex", THREAD_ID, "c1"))
    assert store.get(conn, "c1").endpoint == PARENT


def test_a_nested_codex_host_gets_nothing(conn, endpoint):
    launch_codex(conn)
    listen_ready.observe(ready("codex", THREAD_ID, "c1"))
    endpoint(NESTED)
    listen_ready.observe(ready("codex", "thr-nested", "c1"))
    assert store.session_ids(conn, "c1") == [(THREAD_ID, "startup")]
    assert listen_codex.answer(start("codex", "thr-nested", "c1")) is None


@pytest.mark.parametrize("mode", ["interactive", "exec", None])
def test_a_codex_launch_not_composed_for_the_host_is_never_claimed(conn, endpoint, mode):
    launch_codex(conn, mode=mode)
    listen_ready.observe(ready("codex", THREAD_ID, "c1"))
    assert listen_codex.answer(start("codex", THREAD_ID, "c1")) is None
    row = store.get(conn, "c1")
    assert (row.endpoint, row.state, row.session_id) == (None, "spawning", None)


def test_a_session_of_another_harness_does_not_claim(conn, endpoint):
    launch_codex(conn)
    listen_ready.observe(ready("claude", CLAUDE_ID, "c1"))
    assert store.get(conn, "c1").endpoint is None


def test_gone_adds_an_event_for_the_held_launch(conn, endpoint):
    launch_claude(conn)
    listen_ready.observe(ready("claude", CLAUDE_ID, "r1"))
    listen_ready.observe(Event("gone", "claude", CLAUDE_ID, stamp("r1"), reason="exited"))
    [event] = store.events(conn, "r1")
    assert (event.kind, event.session_id, event.detail) == ("gone", CLAUDE_ID,
                                                            {"reason": "exited"})


def test_ready_never_moves_a_row_back(conn, endpoint):
    launch_claude(conn)
    store.set_state(conn, "r1", "sending")
    store.set_state(conn, "r1", "first-sent")
    listen_ready.observe(ready("claude", CLAUDE_ID, "r1"))
    assert store.get(conn, "r1").state == "first-sent"


def test_without_a_stamp_or_a_store_nothing_is_written(endpoint):
    path = store.path()
    assert listen_ready.observe(ready("claude", CLAUDE_ID, "r1")) is None
    assert listen_ready.observe(Event("ready", "claude", CLAUDE_ID, {})) is None
    assert listen_handoff.answer(start("claude", CLAUDE_ID, "r1")) is None
    assert listen_codex.answer(start("codex", THREAD_ID, "c1")) is None
    assert not path.exists()


def test_an_unknown_request_claims_nothing(conn, endpoint):
    launch_claude(conn)
    listen_ready.observe(ready("claude", CLAUDE_ID, "r-unknown"))
    assert store.get(conn, "r1").endpoint is None


def test_of_two_endpoints_claiming_at_once_exactly_one_wins(conn):
    launch_codex(conn)
    barrier = threading.Barrier(2)
    won = []

    def race(name):
        conn = store.connect()
        try:
            barrier.wait()
            if store.claim(conn, "c1", harness="codex", session_id=None,
                           endpoint=name) is not None:
                won.append(name)
        finally:
            conn.close()

    threads = [threading.Thread(target=race, args=(n,)) for n in (PARENT, NESTED)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(won) == 1 and store.get(conn, "c1").endpoint == won[0]


def test_of_two_endpoints_reclaiming_at_once_exactly_one_wins(conn, dead):
    launch_claude(conn)
    seated(conn)
    assert store.claim(conn, "r1", harness="claude", session_id=CLAUDE_ID,
                       endpoint=dead) is not None
    barrier = threading.Barrier(2)
    won = []

    def race(name):
        conn = store.connect()
        try:
            barrier.wait()
            if store.claim(conn, "r1", harness="claude", session_id=CLAUDE_ID,
                           endpoint=name, parent=SEAT) is not None:
                won.append(name)
        finally:
            conn.close()

    # Both alive, as a reclaiming endpoint is, so the loser can't take it from the winner.
    parent = proc.identity(os.getppid())
    other = f"{parent.pid}@{parent.started:.6f}"
    threads = [threading.Thread(target=race, args=(n,)) for n in (LIVE, other)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(won) == 1 and store.get(conn, "r1").endpoint == won[0]
    assert [e.kind for e in store.events(conn, "r1")] == [store.RECLAIMED]


# -- the Codex seat listener ---------------------------------------------------------------


def test_a_codex_seat_gets_its_identity_block_and_instructions(conn, endpoint):
    launch_codex(conn, values={"instructions": "Build what the brief says."},
                 project_key="moxi", identity="builder", scope="moxi", posture="go")
    text = listen_codex.answer(start("codex", THREAD_ID, "c1"))
    handoff_dir = handoffs.own_handoffs_dir()
    assert text == "\n".join([
        "--- LAUNCH IDENTITY (stamped at launch) ---",
        f"session id:    {THREAD_ID}",
        "project cwd:   /work",
        "project key:   moxi",
        "identity:      builder@moxi",
        f"handoff dir:   {handoff_dir}",
        "start posture: go",
    ]) + "\n\nBuild what the brief says."


@pytest.mark.parametrize("source", ["startup", "resume", "clear", "compact"])
def test_the_instructions_file_is_read_at_every_start(conn, endpoint, tmp_path, source):
    brief = tmp_path / "builder.md"
    brief.write_text("first text\n", encoding="utf-8")
    launch_codex(conn, values={"instructions_file": str(brief)})
    assert listen_codex.answer(start("codex", THREAD_ID, "c1")).endswith("first text")
    brief.write_text("second text\n", encoding="utf-8")
    assert listen_codex.answer(start("codex", THREAD_ID, "c1", source=source)).endswith(
        "second text")


def test_an_unreadable_instructions_file_still_gives_the_block(conn, endpoint, tmp_path):
    launch_codex(conn, values={"instructions_file": str(tmp_path / "missing.md")})
    text = listen_codex.answer(start("codex", THREAD_ID, "c1"))
    assert text.startswith(common.HEADER) and text.endswith("start posture: go")


def test_a_codex_launch_without_a_key_names_no_handoff_dir(conn, endpoint):
    launch_codex(conn, preset_kind=None, preset_name=None)
    assert "handoff dir:" not in listen_codex.answer(start("codex", THREAD_ID, "c1"))


def test_a_claude_session_gets_no_codex_instructions(conn, endpoint):
    launch_claude(conn, values={"mode": "interactive", "instructions": "x"})
    assert listen_codex.answer(start("claude", CLAUDE_ID, "r1")) is None


# -- the handoff pointer -------------------------------------------------------------------


def pointer(path, date):
    return (f"Handoff waiting: {path} ({date}). Read it before other work, then run: "
            f"moxi launcher handoff take {path}")


def handoff(key, date="2026-10-29", count=1, handoff_dir=None):
    path = (handoff_dir or handoffs.own_handoffs_dir()) / handoffs.file_name(key, date, count)
    path.write_text("where the work is\n", encoding="utf-8")
    return path


def test_a_seat_is_pointed_to_its_newest_waiting_handoff_at_every_start_until_taken(
        conn, endpoint):
    launch_claude(conn)
    handoff("lead", "2026-10-28")
    newest = handoff("lead", "2026-10-29", 2)
    assert listen_handoff.answer(start("claude", CLAUDE_ID, "r1")) == pointer(
        newest, "2026-10-29")
    assert store.picked(conn, "lead") == []
    launch_claude(conn, "r2", session_id=CLEARED_ID)
    assert listen_handoff.answer(start("claude", CLEARED_ID, "r2")) == pointer(
        newest, "2026-10-29")
    taken = handoffs.take(env={listeners.REQUEST_ENV: "r2"})
    assert (taken.key, taken.path, taken.request_id) == ("lead", newest, "r2")
    assert store.picked(conn, "lead") == [str(newest)]
    launch_claude(conn, "r3", session_id=NESTED_ID)
    assert listen_handoff.answer(start("claude", NESTED_ID, "r3")) is None


def test_a_shown_pick_an_older_launcher_wrote_still_counts(conn, endpoint):
    launch_claude(conn)
    shown = handoff("lead")
    store.add_pick(conn, str(shown), "lead", "shown", request_id="r0")
    assert listen_handoff.answer(start("claude", CLAUDE_ID, "r1")) is None
    newer = handoff("lead", "2026-10-30")
    assert listen_handoff.answer(start("claude", CLAUDE_ID, "r1")) == pointer(
        newer, "2026-10-30")


def test_with_notes_and_no_notes_dir_a_seat_is_pointed_to_nothing(conn, endpoint, monkeypatch):
    # A handoff is read only from the launch's notes directory; a handoff in the
    # launcher's own folder is not offered while notes is installed.
    launch_claude(conn)
    handoff("lead")
    homeless = SimpleNamespace(state=modules.PRESENT, ok=True, reason="", api=SimpleNamespace(
        handoffs_dir=lambda path, *, create=False, override=None: None))
    real_load = modules.load
    monkeypatch.setattr(modules, "available", lambda name: name == "notes")
    monkeypatch.setattr(modules, "load", lambda name, interface=None:
                        homeless if name == "notes" else real_load(name, interface=interface))
    assert listen_handoff.answer(start("claude", CLAUDE_ID, "r1")) is None
    assert store.picked(conn, "lead") == []


@pytest.mark.parametrize("source", ["clear", "compact"])
def test_the_lines_answer_not_at_a_clear_or_compaction(conn, endpoint, source):
    launch_claude(conn)
    handoff("lead")
    handoff("planner")
    assert listen_handoff.answer(start("claude", CLAUDE_ID, "r1", source=source)) is None
    assert store.picked(conn, "lead") == []


def test_a_resume_of_a_held_launch_is_pointed_and_marks_nothing(conn, endpoint):
    launch_claude(conn)
    waiting = handoff("lead")
    other = handoff("planner", "2026-10-28")
    text = listen_handoff.answer(start("claude", CLAUDE_ID, "r1", source="resume"))
    assert text == (pointer(waiting, "2026-10-29") + "\n"
                    + f"Another seat's handoff: {other} (planner, 2026-10-28)")
    assert store.picked(conn, "lead") == [] and store.picked(conn, "planner") == []
    assert listen_handoff.answer(start("claude", CLAUDE_ID, "r1", source="resume")) == text


def test_other_seats_lines_follow_the_pointer_newest_first_and_mark_nothing(conn, endpoint):
    launch_claude(conn)
    own = handoff("lead", "2026-10-20")
    others = [handoff(f"seat{n}", f"2026-10-2{n}") for n in range(7, 0, -1)]
    lines = listen_handoff.answer(start("claude", CLAUDE_ID, "r1")).split("\n")
    assert lines[0] == pointer(own, "2026-10-20")
    assert lines[1:6] == [f"Another seat's handoff: {path} (seat{n}, 2026-10-2{n})"
                          for path, n in zip(others, range(7, 2, -1))]
    assert lines[6:] == ["\u2026 and 2 more"]
    assert all(store.picked(conn, f"seat{n}") == [] for n in range(1, 8))


def test_a_seat_with_nothing_of_its_own_still_gets_other_seats_lines(conn, endpoint):
    launch_claude(conn)
    other = handoff("planner")
    assert listen_handoff.answer(start("claude", CLAUDE_ID, "r1")) == (
        f"Another seat's handoff: {other} (planner, 2026-10-29)")
    assert store.picked(conn, "lead") == [] and store.picked(conn, "planner") == []


def test_a_relaunch_gets_no_pointer(conn, endpoint):
    launch_claude(conn)
    handoff("lead")
    handoff("planner")
    event = start("claude", CLAUDE_ID, "r1", MOXI_LAUNCHER_RELAUNCH="1")
    assert listen_handoff.answer(event) is None


def test_a_headless_launch_gets_no_pointer(conn, endpoint):
    launch_claude(conn, target="headless")
    handoff("lead")
    handoff("planner")
    assert listen_handoff.answer(start("claude", CLAUDE_ID, "r1")) is None
    assert store.picked(conn, "lead") == []


def test_a_nested_session_gets_no_pointer_and_marks_nothing(conn, endpoint):
    launch_claude(conn)
    listen_ready.observe(ready("claude", CLAUDE_ID, "r1"))
    handoff("lead")
    handoff("planner")
    endpoint(NESTED)
    assert listen_handoff.answer(start("claude", NESTED_ID, "r1")) is None
    assert store.picked(conn, "lead") == []
    endpoint(PARENT)
    assert listen_handoff.answer(start("claude", CLAUDE_ID, "r1")) is not None


def test_the_handoff_given_to_a_launch_is_not_offered_again(conn, endpoint):
    given = handoff("lead")
    launch_claude(conn, handoff_path=str(given), handoff_key="lead")
    assert listen_handoff.answer(start("claude", CLAUDE_ID, "r1")) is None


def test_a_handoff_given_to_a_failed_launch_still_waits(conn, endpoint):
    given = handoff("lead")
    launch_claude(conn, "r0", session_id=NESTED_ID, handoff_path=str(given),
                  handoff_key="lead")
    store.set_state(conn, "r0", "failed", reason="no-ready")
    launch_claude(conn)
    assert str(given) in listen_handoff.answer(start("claude", CLAUDE_ID, "r1"))


def test_a_session_started_by_hand_is_shown_its_handoff_and_never_marks_it(conn,
                                                                           endpoint):
    waiting = handoff("planner")
    event = Event("start", "claude", "hand-1", {"MOXI_COMMS_NAME": "planner"},
                  source="startup")
    for _ in range(2):
        assert listen_handoff.answer(event) == (
            pointer(waiting, "2026-10-29"))
    assert store.picked(conn, "planner") == []


def test_a_session_started_by_hand_gets_other_seats_lines_at_a_resume(conn, endpoint):
    waiting = handoff("planner")
    other = handoff("lead", "2026-10-28")
    event = Event("start", "claude", "hand-1", {"MOXI_COMMS_NAME": "planner"},
                  source="resume")
    assert listen_handoff.answer(event) == (
        pointer(waiting, "2026-10-29") + "\n"
        + f"Another seat's handoff: {other} (lead, 2026-10-28)")
    assert store.picked(conn, "planner") == [] and store.picked(conn, "lead") == []


def test_a_session_by_hand_reads_no_store_it_would_have_to_make(endpoint):
    waiting = handoff("planner")
    event = Event("start", "codex", "thr", {"MOXI_COMMS_NAME": "planner"},
                  source="startup")
    assert str(waiting) in listen_handoff.answer(event)
    assert not store.path().exists()


def test_a_session_without_a_key_gets_every_waiting_handoff_as_another_seats(conn,
                                                                            endpoint):
    launch_claude(conn, preset_kind=None, preset_name=None)
    lead = handoff("lead")
    planner = handoff("planner", "2026-10-28")
    lines = [f"Another seat's handoff: {lead} (lead, 2026-10-29)",
             f"Another seat's handoff: {planner} (planner, 2026-10-28)"]
    assert listen_handoff.answer(start("claude", CLAUDE_ID, "r1")) == "\n".join(lines)
    assert listen_handoff.answer(Event("start", "claude", "x", {"PWD": "/work"},
                                       source="resume")) == "\n".join(lines)
    assert store.picked(conn, "lead") == [] and store.picked(conn, "planner") == []


def test_a_session_without_a_key_in_an_empty_handoff_dir_gets_nothing(conn, endpoint):
    launch_claude(conn, preset_kind=None, preset_name=None)
    assert listen_handoff.answer(start("claude", CLAUDE_ID, "r1")) is None


def test_the_key_of_a_project_launch_is_its_project_key(conn, endpoint):
    launch_claude(conn, preset_kind="project", preset_name="moxi-preset", project_key="moxi")
    waiting = handoff("moxi")
    assert str(waiting) in listen_handoff.answer(start("claude", CLAUDE_ID, "r1"))
    assert store.picked(conn, "moxi") == []


@pytest.fixture
def stand_in_notes(monkeypatch, tmp_path):
    """notes installed, as a stand-in whose `handoffs_dir` reads nothing and answers one
    folder. It wraps the fixtures' own `available` and `load`, so a projects read still
    fails the test."""
    calls = []
    handoff_dir = tmp_path / "notes" / "code" / "alpha" / "handoffs"

    def handoffs_dir(path, *, create=False, override=None):
        calls.append((path, create, override))
        if create:
            handoff_dir.mkdir(parents=True, exist_ok=True)
        return handoff_dir

    found = SimpleNamespace(state=modules.PRESENT, reason="",
                            api=SimpleNamespace(handoffs_dir=handoffs_dir))
    available, load = modules.available, modules.load
    monkeypatch.setattr(modules, "available", lambda name: name == "notes" or available(name))
    monkeypatch.setattr(modules, "load", lambda name, *, interface=None: (
        found if name == "notes" else load(name, interface=interface)))
    return SimpleNamespace(calls=calls, handoff_dir=handoff_dir)


def test_with_notes_a_launched_seat_is_pointed_into_notes_dir(conn, endpoint,
                                                                 stand_in_notes):
    launch_claude(conn, cwd="/work/alpha")
    stand_in_notes.handoff_dir.mkdir(parents=True)
    waiting = handoff("lead", handoff_dir=stand_in_notes.handoff_dir)
    handoff("lead", "2026-10-30")  # the launcher's own folder is not where it looks
    text = listen_handoff.answer(start("claude", CLAUDE_ID, "r1"))
    assert text == pointer(waiting, "2026-10-29")
    assert stand_in_notes.calls == [("/work/alpha", True, None)]
    assert store.picked(conn, "lead") == []


def test_with_notes_a_session_by_hand_is_pointed_into_notes_dir(conn, endpoint,
                                                                   stand_in_notes):
    stand_in_notes.handoff_dir.mkdir(parents=True)
    waiting = handoff("planner", handoff_dir=stand_in_notes.handoff_dir)
    event = Event("start", "claude", "hand-1",
                  {"MOXI_COMMS_NAME": "planner", "PWD": "/work/alpha"}, source="startup")
    assert str(waiting) in listen_handoff.answer(event)
    assert stand_in_notes.calls == [("/work/alpha", True, None)]
    assert store.picked(conn, "planner") == []


def test_a_codex_seat_gets_both_answers(conn, endpoint):
    launch_codex(conn, values={"instructions": "Build."})
    waiting = handoff("builder")
    assert listen_codex.answer(start("codex", THREAD_ID, "c1")).endswith("Build.")
    assert str(waiting) in listen_handoff.answer(start("codex", THREAD_ID, "c1"))


def test_the_own_dir_is_the_handoffs_store():
    assert handoffs.own_handoffs_dir() == Layout("launcher").data_dir() / "handoffs"

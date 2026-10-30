"""The listeners turns calls, against session events shaped like turns' `SessionEvent`:
the claim, the ready recorder, the Codex seat listener and the handoff pointer, a session
started from inside a launched one among them."""

from __future__ import annotations

import ast
import threading
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from moxi import modules
from moxi.paths import Layout

from moxi_launcher import (handoffs, listen_codex, listen_handoff, listen_ready, listeners,
                           store)

CLAUDE_ID = "5f0c3a1e-7f43-4c8e-9e0a-1d2b3c4d5e6f"
CLEARED_ID = "9a8b7c6d-1111-4222-8333-444455556666"
NESTED_ID = "00000000-aaaa-4bbb-8ccc-dddddddddddd"
THREAD_ID = "thr-0001"
PARENT = "1000@1.000000"
NESTED = "2000@2.000000"


@dataclass
class Event:
    """A session event as turns hands one to a listener."""

    event: str
    harness: str
    session_id: str | None
    env: dict[str, str] = field(default_factory=dict)
    source: str | None = None
    reason: str | None = None


@pytest.fixture
def endpoint(monkeypatch):
    """Which endpoint process the next event comes from; the launched session's by
    default."""
    current = {"id": PARENT}
    monkeypatch.setattr(listeners, "endpoint_id", lambda: current["id"])

    def use(name):
        current["id"] = name
    return use


@pytest.fixture
def conn():
    connection = store.connect()
    yield connection
    connection.close()


def stamp(request_id, **more):
    return {"MOXI_LAUNCHER_REQUEST": request_id, **more}


def launch_claude(conn, request_id="r1", **overrides):
    args = {"request_id": request_id, "actor": "someone", "harness": "claude",
            "target": "slot", "cwd": "/work", "values": {"mode": "interactive"},
            "argv": ["claude"], "env": stamp(request_id), "posture": "operator",
            "session_id": CLAUDE_ID, "preset_kind": "seat", "preset_name": "lead"}
    args.update(overrides)
    return store.insert(conn, **args).row


def launch_codex(conn, request_id="c1", mode="host", values=None, **overrides):
    args = {"request_id": request_id, "actor": "someone", "harness": "codex",
            "target": "slot", "cwd": "/work", "values": {"mode": mode, **(values or {})},
            "argv": ["moxi-turns-codex-host"], "env": stamp(request_id), "posture": "go",
            "preset_kind": "seat", "preset_name": "builder"}
    args.update(overrides)
    return store.insert(conn, **args).row


def ready(harness, session_id, request_id, source="startup", **env):
    return Event("ready", harness, session_id, stamp(request_id, **env), source=source)


def start(harness, session_id, request_id, source="startup", **env):
    return Event("start", harness, session_id, stamp(request_id, **env), source=source)


# -- the registration ----------------------------------------------------------------------


def test_the_registration_names_three_rows_in_answer_order():
    assert listeners.LISTENERS == (
        (("ready", "gone"), "moxi_launcher.listen_ready:observe"),
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


def test_a_second_endpoint_with_the_minted_id_does_not_take_the_launch(conn, endpoint):
    launch_claude(conn)
    listen_ready.observe(ready("claude", CLAUDE_ID, "r1"))
    endpoint(NESTED)
    listen_ready.observe(ready("claude", CLAUDE_ID, "r1", source="resume"))
    assert store.get(conn, "r1").endpoint == PARENT
    assert store.session_ids(conn, "r1") == [(CLAUDE_ID, "minted")]


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
        mine = store.connect()
        try:
            barrier.wait()
            if store.claim(mine, "c1", harness="codex", session_id=None,
                           endpoint=name) is not None:
                won.append(name)
        finally:
            mine.close()

    threads = [threading.Thread(target=race, args=(n,)) for n in (PARENT, NESTED)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(won) == 1 and store.get(conn, "c1").endpoint == won[0]


# -- the Codex seat listener ---------------------------------------------------------------


def test_a_codex_seat_gets_its_identity_block_and_instructions(conn, endpoint):
    launch_codex(conn, values={"instructions": "Build what the brief says."},
                 project_key="moxi", identity="builder", scope="moxi", posture="go")
    text = listen_codex.answer(start("codex", THREAD_ID, "c1"))
    folder = handoffs.own_folder()
    assert text == "\n".join([
        "--- LAUNCH IDENTITY (stamped at launch) ---",
        f"session id:    {THREAD_ID}",
        "project cwd:   /work",
        "project key:   moxi",
        "identity:      builder@moxi",
        f"handoff dir:   {folder}",
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
    assert text.startswith(listen_codex.HEADER) and text.endswith("start posture: go")


def test_a_codex_launch_without_a_key_names_no_handoff_dir(conn, endpoint):
    launch_codex(conn, preset_kind=None, preset_name=None)
    assert "handoff dir:" not in listen_codex.answer(start("codex", THREAD_ID, "c1"))


def test_a_claude_session_gets_no_codex_instructions(conn, endpoint):
    launch_claude(conn, values={"mode": "interactive", "instructions": "x"})
    assert listen_codex.answer(start("claude", CLAUDE_ID, "r1")) is None


# -- the handoff pointer -------------------------------------------------------------------


def handoff(key, date="2026-10-29", count=1, folder=None):
    path = (folder or handoffs.own_folder()) / handoffs.file_name(key, date, count)
    path.write_text("where the work is\n", encoding="utf-8")
    return path


def test_a_seat_is_pointed_to_its_newest_waiting_handoff_once(conn, endpoint):
    launch_claude(conn)
    handoff("lead", "2026-10-28")
    newest = handoff("lead", "2026-10-29", 2)
    text = listen_handoff.answer(start("claude", CLAUDE_ID, "r1"))
    assert text == f"Handoff waiting: {newest} (2026-10-29). Read it before other work."
    assert store.picked(conn, "lead") == [str(newest)]
    launch_claude(conn, "r2", session_id=CLEARED_ID)
    assert listen_handoff.answer(start("claude", CLEARED_ID, "r2")) is None


@pytest.mark.parametrize("source", ["resume", "clear", "compact"])
def test_the_pointer_answers_only_at_startup(conn, endpoint, source):
    launch_claude(conn)
    handoff("lead")
    assert listen_handoff.answer(start("claude", CLAUDE_ID, "r1", source=source)) is None
    assert store.picked(conn, "lead") == []


def test_a_relaunch_gets_no_pointer(conn, endpoint):
    launch_claude(conn)
    handoff("lead")
    event = start("claude", CLAUDE_ID, "r1", MOXI_LAUNCHER_RELAUNCH="1")
    assert listen_handoff.answer(event) is None


def test_a_headless_launch_gets_no_pointer(conn, endpoint):
    launch_claude(conn, target="headless")
    handoff("lead")
    assert listen_handoff.answer(start("claude", CLAUDE_ID, "r1")) is None
    assert store.picked(conn, "lead") == []


def test_a_nested_session_gets_no_pointer_and_marks_nothing(conn, endpoint):
    launch_claude(conn)
    listen_ready.observe(ready("claude", CLAUDE_ID, "r1"))
    handoff("lead")
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
            f"Handoff waiting: {waiting} (2026-10-29). Read it before other work.")
    assert store.picked(conn, "planner") == []


def test_a_session_by_hand_reads_no_store_it_would_have_to_make(endpoint):
    waiting = handoff("planner")
    event = Event("start", "codex", "thr", {"MOXI_COMMS_NAME": "planner"},
                  source="startup")
    assert str(waiting) in listen_handoff.answer(event)
    assert not store.path().exists()


def test_a_session_without_a_key_gets_no_pointer(conn, endpoint):
    launch_claude(conn, preset_kind=None, preset_name=None)
    handoff("lead")
    assert listen_handoff.answer(start("claude", CLAUDE_ID, "r1")) is None
    assert listen_handoff.answer(Event("start", "claude", "x", {"PWD": "/work"},
                                       source="startup")) is None


def test_the_key_of_a_project_launch_is_its_project_key(conn, endpoint):
    launch_claude(conn, preset_kind="project", preset_name="moxi-preset", project_key="moxi")
    waiting = handoff("moxi")
    assert str(waiting) in listen_handoff.answer(start("claude", CLAUDE_ID, "r1"))
    assert store.picked(conn, "moxi") == [str(waiting)]


def test_a_codex_seat_gets_both_answers(conn, endpoint):
    launch_codex(conn, values={"instructions": "Build."})
    waiting = handoff("builder")
    assert listen_codex.answer(start("codex", THREAD_ID, "c1")).endswith("Build.")
    assert str(waiting) in listen_handoff.answer(start("codex", THREAD_ID, "c1"))


def test_the_own_folder_is_the_handoffs_store():
    assert handoffs.own_folder() == Layout("launcher").data_dir() / "handoffs"

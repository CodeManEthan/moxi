"""A session's launch kind, read from the launch log by session id: each of the four kinds
and the order they are decided in, each case that answers None, ids learned after the
start, launches made end to end, and a log that can't be read."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest
from moxi import modules
from moxi import store as core_store

import launcher_fake_turns as fake_turns
from launcher_fake_harness import Harness
from moxi_launcher import api, listen_ready, listeners, presets, store
from moxi_launcher.api import LaunchRequest

CLAUDE_ID = "5f0c3a1e-7f43-4c8e-9e0a-1d2b3c4d5e6f"
CLEARED_ID = "9a8b7c6d-1111-4222-8333-444455556666"
NESTED_ID = "00000000-aaaa-4bbb-8ccc-dddddddddddd"
HAND_ID = "11111111-2222-4333-8444-555555555555"
THREAD_ID = "thr-0001"
PARENT = "1000@1.000000"
NESTED = "2000@2.000000"
V1_DUMP = Path(__file__).with_name("launcher_v1.sql")
POSIX = pytest.mark.skipif(sys.platform == "win32",
                           reason="the fake harness is a script run by its #! line")


class Event:
    """A session event as turns hands one to a listener."""

    def __init__(self, event, harness, session_id, request_id, source="startup"):
        self.event, self.harness, self.session_id = event, harness, session_id
        self.env = {listeners.REQUEST: request_id}
        self.source, self.reason = source, None


@pytest.fixture
def conn():
    connection = store.connect()
    yield connection
    connection.close()


@pytest.fixture
def endpoint(monkeypatch):
    """Which endpoint process the next event comes from; the launched session's by
    default."""
    current = {"id": PARENT}
    monkeypatch.setattr(listeners, "endpoint_id", lambda: current["id"])
    return lambda name: current.update(id=name)


def launch(conn, request_id="r1", *, harness="claude", session_id=CLAUDE_ID, values=None,
           preset_kind=None, project_key=None, **more):
    """A launch's row as `api.launch` writes it; `values` adds to its place rows."""
    placed = {"cwd": "/work", "scratch": False, "project": project_key or "",
              "mode": "interactive" if harness == "claude" else "host", **(values or {})}
    return store.insert(conn, request_id=request_id, actor="someone", harness=harness,
                        target="slot", cwd="/work", values=placed, argv=[harness],
                        env={listeners.REQUEST: request_id}, posture="operator",
                        session_id=session_id if harness == "claude" else None,
                        preset_kind=preset_kind,
                        preset_name=None if preset_kind is None else "a-preset",
                        project_key=project_key, **more).row


# -- the four kinds, in their order --------------------------------------------------------


@pytest.mark.parametrize("preset_kind, project_key, scratch, kind", [
    (None, None, True, "scratch"),
    ("seat", None, True, "scratch"),
    ("project", None, True, "scratch"),
    ("seat", None, False, "seat"),
    ("seat", "moxi", False, "seat"),
    ("project", None, False, "project"),
    ("project", "moxi", False, "project"),
    (None, "moxi", False, "project"),
    (None, None, False, "one-off"),
])
def test_each_launch_answers_the_first_kind_that_holds(conn, preset_kind, project_key,
                                                       scratch, kind):
    launch(conn, preset_kind=preset_kind, project_key=project_key,
           values={"scratch": scratch})
    assert api.launch_kind(CLAUDE_ID) == kind
    assert kind in api.KINDS


def test_the_kinds_are_four_and_scratch_comes_first():
    assert api.KINDS == ("scratch", "seat", "project", "one-off")


def test_a_row_made_before_the_place_rows_were_recorded_reads_by_its_columns(conn):
    launch(conn, preset_kind="seat")
    conn.execute("UPDATE launches SET values_json = '{}'")
    assert api.launch_kind(CLAUDE_ID) == "seat"


# -- None ----------------------------------------------------------------------------------


def test_with_no_launch_log_every_session_answers_none_and_none_is_made():
    assert not store.path().exists()
    assert api.launch_kind(HAND_ID) is None
    assert api.launch_kinds([HAND_ID, CLAUDE_ID]) == {HAND_ID: None, CLAUDE_ID: None}
    assert not store.path().exists()


def test_a_session_started_by_hand_answers_none(conn):
    launch(conn, values={"scratch": True})
    assert api.launch_kind(HAND_ID) is None


def test_a_session_started_inside_a_launched_one_answers_none(conn, endpoint):
    """It carries the launch's stamp, but the launch is the parent endpoint's: the nested
    endpoint never claims it, so none of its ids joins the launch."""
    launch(conn, preset_kind="seat", values={"scratch": True})
    listen_ready.observe(Event("ready", "claude", CLAUDE_ID, "r1"))
    endpoint(NESTED)
    listen_ready.observe(Event("ready", "claude", NESTED_ID, "r1"))
    listen_ready.observe(Event("ready", "claude", "nested-cleared", "r1", source="clear"))
    assert api.launch_kinds([CLAUDE_ID, NESTED_ID, "nested-cleared"]) == {
        CLAUDE_ID: "scratch", NESTED_ID: None, "nested-cleared": None}
    assert store.get(conn, "r1").endpoint == PARENT


def test_a_codex_launch_answers_none_until_its_id_is_learned(conn, endpoint):
    launch(conn, "c1", harness="codex", preset_kind="seat")
    assert api.launch_kind(THREAD_ID) is None
    listen_ready.observe(Event("ready", "codex", THREAD_ID, "c1"))
    assert api.launch_kind(THREAD_ID) == "seat"


def test_a_codex_launch_in_exec_mode_never_answers(conn, endpoint):
    launch(conn, "c1", harness="codex", values={"mode": "exec", "scratch": True})
    listen_ready.observe(Event("ready", "codex", THREAD_ID, "c1"))
    assert store.get(conn, "c1").endpoint is None
    assert api.launch_kind(THREAD_ID) is None


# -- ids a launch learns after its start ---------------------------------------------------


def test_the_id_after_a_clear_answers_its_launchs_kind_once_learned(conn, endpoint):
    launch(conn, values={"scratch": True})
    listen_ready.observe(Event("ready", "claude", CLAUDE_ID, "r1"))
    assert api.launch_kind(CLEARED_ID) is None
    listen_ready.observe(Event("ready", "claude", CLEARED_ID, "r1", source="clear"))
    assert api.launch_kinds([CLAUDE_ID, CLEARED_ID]) == {CLAUDE_ID: "scratch",
                                                         CLEARED_ID: "scratch"}


def test_a_resumed_session_answers_the_kind_of_the_launch_that_holds_it_last(conn):
    launch(conn, "r1", values={"scratch": True})
    launch(conn, "r2", values={"scratch": True}, resume_of="r1", starting_s=0.0)
    assert store.resolve(conn, CLAUDE_ID).request_id == "r2"
    assert api.launch_kind(CLAUDE_ID) == "scratch"


def test_many_ids_are_answered_in_one_read_each_once(conn, monkeypatch):
    launch(conn, "r1", preset_kind="seat")
    launch(conn, "r2", session_id=CLEARED_ID, preset_kind="project")
    opened = []
    real = store.connect
    monkeypatch.setattr(store, "connect", lambda *a, **k: opened.append(1) or real(*a, **k))
    got = api.launch_kinds(iter([CLAUDE_ID, HAND_ID, CLEARED_ID, CLAUDE_ID]))
    assert got == {CLAUDE_ID: "seat", HAND_ID: None, CLEARED_ID: "project"}
    assert list(got) == [CLAUDE_ID, HAND_ID, CLEARED_ID] and len(opened) == 1
    assert api.launch_kinds([]) == {} and len(opened) == 1


# -- launches made end to end --------------------------------------------------------------


@pytest.fixture
def harness(tmp_path, monkeypatch) -> Harness:
    fake_turns.hide(monkeypatch)
    return Harness.on_path(tmp_path, monkeypatch)


@POSIX
@pytest.mark.parametrize("preset, typed, kind", [
    ('[seat.tester]\nscratch = true\n', {}, "scratch"),
    ('[seat.tester]\n', {}, "seat"),
    ('[seat.tester]\n', {"scratch": True}, "scratch"),
    ('[project.docs]\ncwd = "{work}"\n', {}, "project"),
    ('', {}, "one-off"),
])
def test_a_launch_made_through_the_api_answers_its_kind(harness, tmp_path, preset, typed,
                                                        kind):
    work = tmp_path / "work"
    work.mkdir()
    file = presets.path()
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(preset.replace("{work}", str(work)))
    named = {"seat": "tester"} if "seat." in preset else (
        {"project_preset": "docs"} if "project." in preset else {})
    api.launch(LaunchRequest(**named, typed={"target": "headless", "brief": "Go.", **typed}, cwd=str(work),
                             request_id="r1", session_id=CLAUDE_ID, actor="ann"))
    harness.one()
    assert api.launch_kind(CLAUDE_ID) == kind


# -- a launch log that can't be read -------------------------------------------------------


def test_a_log_held_past_the_wait_raises_busy_and_writes_nothing():
    """A version 1 log needs its schema step, which waits for the write lock."""
    path = store.path()
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = sqlite3.connect(path, isolation_level=None)
    raw.execute("PRAGMA journal_mode = WAL")
    raw.executescript(V1_DUMP.read_text())
    raw.execute("BEGIN IMMEDIATE")
    try:
        with pytest.raises(api.Busy) as raised:
            api.launch_kind(CLAUDE_ID, timeout=0.05)
        assert raised.value.exit_code == 75
    finally:
        raw.execute("ROLLBACK")
    assert raw.execute("PRAGMA user_version").fetchone()[0] == 1
    raw.close()


def test_a_log_a_newer_launcher_wrote_is_refused_not_read(conn):
    launch(conn, preset_kind="seat")
    conn.execute("PRAGMA user_version = 3")
    with pytest.raises(core_store.StoreTooNew):
        api.launch_kind(CLAUDE_ID)


# -- the interface -------------------------------------------------------------------------


def test_the_launcher_offers_interface_1_with_the_kind_read(conn):
    launch(conn, values={"scratch": True})
    modules.reset()
    try:
        found = modules.load("launcher", interface=1)
        assert found.state == modules.PRESENT, found.reason
        assert found.interfaces == frozenset({1})
        assert found.api.launch_kind(CLAUDE_ID) == "scratch"
        assert found.api.launch_kinds([CLAUDE_ID]) == {CLAUDE_ID: "scratch"}
        assert modules.load("launcher", interface=2).state == modules.BROKEN
    finally:
        modules.reset()


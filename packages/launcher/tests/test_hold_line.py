"""The launcher's `moxi.comms.hold_lines` entry, `notices.hold_line`: the relaunch line
under comms' hold line for a director of the cold session's live launch, and None for
anyone else, a session no launch holds, and a launch whose endpoint is gone."""

from __future__ import annotations

import os

import pytest
from moxi import modules, proc

from moxi_launcher import notices, presets, store

SID = "5f6e7d8c-9b0a-4c1d-8e2f-3a4b5c6d7e8f"
LINE = ("To reach it now, relaunch it fresh from its handoff: moxi launcher relaunch "
        "planner@proj-a.")


class FakeComms:
    """comms as the launcher reads it: names qualified as comms qualifies them, and the
    box's policy directors."""

    GLOBAL = "global"

    def __init__(self, policy=()):
        self.policy = tuple(policy)

    def qualify(self, name, scope):
        return name if scope == self.GLOBAL else f"{name}@{scope}"

    def split_tag(self, tag):
        name, sep, scope = tag.partition("@")
        return name, scope if sep else None

    def policy_directors(self):
        return self.policy


@pytest.fixture
def env(tmp_path):
    return {"MOXI_HOME": str(tmp_path / "moxi-home")}


@pytest.fixture
def comms(monkeypatch):
    found = FakeComms(policy=("fleet-lead",))
    monkeypatch.setattr(presets, "comms_api", lambda: found)
    return found


def this_process() -> str:
    ident = proc.identity(os.getpid())
    return f"{ident.pid}@{ident.started:.6f}"


def launched(env, *, directors=("boss",), endpoint=None, scope="proj-a"):
    """One Claude launch of planner in `scope` holding `SID`, claimed by `endpoint` (this
    process by default)."""
    path = store.path(env)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = store.connect(path)
    try:
        store.insert(conn, request_id="r1", by="ann", harness="claude", target="slot",
                     cwd="/work", values={"model": "opus", "director": list(directors)},
                     argv=["claude"], env={}, posture="operator", session_id=SID,
                     slot="3", identity="planner", scope=scope)
        conn.execute("UPDATE launches SET endpoint = ? WHERE request_id = ?",
                     (endpoint or this_process(), "r1"))
        conn.commit()
    finally:
        conn.close()


def test_a_director_the_launch_named_gets_the_relaunch_line(env, comms):
    launched(env)
    assert notices.hold_line("boss@proj-a", "planner@proj-a", SID, env=env) == LINE


def test_a_policy_director_gets_the_relaunch_line(env, comms):
    launched(env, directors=())
    assert notices.hold_line("fleet-lead", "planner@proj-a", SID, env=env) == LINE


def test_a_sender_that_directs_nothing_gets_none(env, comms):
    launched(env)
    assert notices.hold_line("reviewer@proj-a", "planner@proj-a", SID, env=env) is None
    # A director named bare is read in the launch's own scope, not the global one.
    assert notices.hold_line("boss", "planner@proj-a", SID, env=env) is None


def test_a_session_no_launch_holds_gets_none(env, comms):
    assert notices.hold_line("boss@proj-a", "planner@proj-a", SID, env=env) is None
    launched(env)
    other = "6a7b8c9d-0e1f-4a2b-9c3d-4e5f6a7b8c9d"
    assert notices.hold_line("boss@proj-a", "planner@proj-a", other, env=env) is None
    assert notices.hold_line("boss@proj-a", "planner@proj-a", None, env=env) is None


def test_a_launch_whose_endpoint_is_gone_gets_none(env, comms, monkeypatch):
    launched(env)
    monkeypatch.setattr(store, "endpoint_gone", lambda endpoint: True)
    assert notices.hold_line("boss@proj-a", "planner@proj-a", SID, env=env) is None


def test_without_comms_at_the_launchers_interface_the_answer_is_none(env, monkeypatch):
    launched(env)
    monkeypatch.setattr(presets, "comms_api", lambda: None)
    assert notices.hold_line("boss@proj-a", "planner@proj-a", SID, env=env) is None


def test_the_entry_is_registered_in_comms_group():
    (entry,) = [e for e in modules.extensions("moxi.comms.hold_lines")
                if e.module == "launcher"]
    assert entry.load() is notices.hold_line

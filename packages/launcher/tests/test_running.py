"""The launches running now, `api.running` (interface 2): live and ended launches, the cap,
the id each is last known by, its name, and a launch log that is missing or can't be read.
It reads the launch log and the process table and nothing else."""

from __future__ import annotations

import os
import sqlite3
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from moxi import modules, proc
from moxi import store as core_store

import launcher_page_kit as kit
from moxi_launcher import api, names, store

#: `launcher_page_kit.seed` takes a shell for its environment; the test's own is the fence's.
HERE = SimpleNamespace(env=None)
V1_DUMP = Path(__file__).with_name("launcher_v1.sql")


def launched(request_id: str, *, identity: str | None = None, name: str | None = None,
             project_key: str | None = None, live: bool = True) -> None:
    """A headless Claude launch's row with a comms name, a name or a project key; its
    process this test's own when `live`."""
    me = proc.identity(os.getpid())
    values = {"harness": "claude", "target": "headless", "cwd": "/work/proj-a",
              **({"name": name} if name else {})}
    with kit.launch_log(HERE) as conn:
        store.insert(conn, request_id=request_id, by="alice", harness="claude",
                     target="headless", cwd="/work/proj-a", values=values, argv=["claude"],
                     env={}, posture="operator", session_id=f"session-{request_id}",
                     identity=identity, project_key=project_key, scope="proj-a")
        store.set_state(conn, request_id, "started", pid=me.pid,
                        pid_started=me.started if live else 1.0)


def test_with_no_launch_log_nothing_is_running():
    assert not store.path().exists()
    assert api.running() == ()
    assert api.running(live_only=False) == ()
    assert not store.path().exists()


def test_a_live_launch_is_given_with_its_row_and_an_ended_one_is_left_out():
    sid = kit.seed(HERE, "r-live", live=True)
    kit.seed(HERE, "r-ended", live=False)
    found = api.running()
    assert [r.request_id for r in found] == ["r-live"]
    with kit.launch_log(HERE) as conn:
        row = store.get(conn, "r-live")
    assert found[0] == api.Running(
        request_id="r-live", harness="claude", session_id=sid, target="slot", slot="3",
        project_key=None, cwd="/work/page", name=names.shown(row), identity=None,
        created_at=row.created_at, live=True, model="opus", effort=None)


def test_the_model_and_effort_are_the_rows_values_and_none_where_unset():
    kit.seed(HERE, "r-set", live=True, values={"model": "sonnet", "effort": "high"})
    kit.seed(HERE, "r-unset", live=True, values={"model": "", "effort": None})
    launched("r-bare")
    by_id = {r.request_id: r for r in api.running()}
    assert (by_id["r-set"].model, by_id["r-set"].effort) == ("sonnet", "high")
    assert (by_id["r-unset"].model, by_id["r-unset"].effort) == (None, None)
    assert (by_id["r-bare"].model, by_id["r-bare"].effort) == (None, None)


def test_without_live_only_an_ended_launch_is_kept_with_live_false_newest_first():
    kit.seed(HERE, "r-old", live=True)
    kit.seed(HERE, "r-ended", live=False)
    kit.seed(HERE, "r-new", live=True)
    found = api.running(live_only=False)
    assert [(r.request_id, r.live) for r in found] == [
        ("r-new", True), ("r-ended", False), ("r-old", True)]


def test_a_failed_launch_and_one_with_no_process_are_never_given():
    kit.seed(HERE, "r-live", live=True)
    kit.seed(HERE, "r-failed", live=True)
    with kit.launch_log(HERE) as conn:
        store.set_state(conn, "r-failed", "failed", reason="test")
        store.insert(conn, request_id="r-spawning", by="alice", harness="claude",
                     target="headless", cwd="/work", values={}, argv=["claude"], env={},
                     posture="operator", session_id="session-spawning")
    assert [r.request_id for r in api.running(live_only=False)] == ["r-live"]


def test_at_most_limit_launches_are_given_the_newest():
    for n in range(5):
        kit.seed(HERE, f"r{n}", live=True)
    assert [r.request_id for r in api.running(limit=2)] == ["r4", "r3"]
    assert api.running(limit=0) == ()
    assert len(api.running()) == 5


def test_the_limit_counts_only_the_launches_given():
    kit.seed(HERE, "r-live", live=True)
    kit.seed(HERE, "r-ended-1", live=False)
    kit.seed(HERE, "r-ended-2", live=False)
    assert [r.request_id for r in api.running(limit=1)] == ["r-live"]
    assert [r.request_id for r in api.running(limit=1, live_only=False)] == ["r-ended-2"]


def test_the_session_id_is_the_newest_the_launch_learned_else_its_own():
    started = kit.seed(HERE, "r-cleared", live=True)
    kept = kit.seed(HERE, "r-kept", live=True)
    first = kit.learned(HERE, "r-cleared", "clear")
    newest = kit.learned(HERE, "r-cleared", "compact")
    found = {r.request_id: r.session_id for r in api.running()}
    assert found == {"r-cleared": newest, "r-kept": kept}
    assert started != newest and first != newest


def test_a_launch_that_never_learned_an_id_has_none():
    kit.seed(HERE, "r-codex", live=True)
    with kit.launch_log(HERE) as conn:
        conn.execute("UPDATE launches SET session_id = NULL WHERE request_id = 'r-codex'")
        conn.execute("DELETE FROM session_ids WHERE request_id = 'r-codex'")
    assert api.running()[0].session_id is None


def test_the_name_is_the_rows_name_else_its_comms_name_else_the_dir_and_time():
    launched("r-named", identity="planner", name="the parser", project_key="proj-a")
    launched("r-identity", identity="reviewer")
    launched("r-plain")
    found = {r.request_id: r for r in api.running()}
    assert found["r-named"].name == "the parser"
    assert found["r-named"].identity == "planner"
    assert found["r-named"].project_key == "proj-a"
    assert found["r-identity"].name == "reviewer"
    assert found["r-plain"].identity is None
    with kit.launch_log(HERE) as conn:
        at = store.parse_time(store.get(conn, "r-plain").created_at).astimezone()
    assert found["r-plain"].name == f"proj-a {at:%H:%M}"


def test_it_asks_no_module_starts_no_process_and_writes_nothing(monkeypatch):
    kit.seed(HERE, "r-live", live=True)
    kit.seed(HERE, "r-ended", live=False)
    with kit.launch_log(HERE) as conn:
        version = conn.execute("PRAGMA data_version").fetchone()[0]

        def refused(*args, **kwargs):
            raise AssertionError("running asked something besides the log and the processes")

        monkeypatch.setattr(modules, "load", refused)
        monkeypatch.setattr(subprocess, "Popen", refused)
        assert len(api.running(live_only=False)) == 2
        # Another connection's commit would move this connection's data version.
        assert conn.execute("PRAGMA data_version").fetchone()[0] == version


def test_a_log_held_past_the_wait_raises_busy_and_writes_nothing():
    """A version 1 log needs its schema step, which waits for the write lock."""
    path = store.path()
    path.parent.mkdir(parents=True, exist_ok=True)
    raw_conn = sqlite3.connect(path, isolation_level=None)
    raw_conn.execute("PRAGMA journal_mode = WAL")
    raw_conn.executescript(V1_DUMP.read_text())
    raw_conn.execute("BEGIN IMMEDIATE")
    try:
        with pytest.raises(api.Busy) as raised:
            api.running(timeout_s=0.05)
        assert raised.value.exit_code == 75
    finally:
        raw_conn.execute("ROLLBACK")
    assert raw_conn.execute("PRAGMA user_version").fetchone()[0] == 1
    raw_conn.close()


def test_a_log_a_newer_launcher_wrote_is_refused_not_read():
    kit.seed(HERE, "r-live", live=True)
    with kit.launch_log(HERE) as conn:
        conn.execute(f"PRAGMA user_version = {len(store.STEPS) + 1}")
    with pytest.raises(core_store.StoreTooNew):
        api.running()


def test_interface_2_offers_the_read():
    kit.seed(HERE, "r-live", live=True)
    modules.reset()
    try:
        found = modules.load("launcher", interface=2)
        assert found.state == modules.PRESENT, found.reason
        assert [r.request_id for r in found.api.running()] == ["r-live"]
    finally:
        modules.reset()

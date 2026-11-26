"""Interface 4: a launch request's stamps, a headless resume (`continue_headless`), a
launch's run log, and ending a headless launch's process tree (`end_headless`)."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from moxi import modules, proc
from moxi_dev.testing import wait_for

import launcher_fake_turns as fake_turns
from launcher_fake_harness import Harness
from moxi_launcher import api, catalog, headless, module, store, targets
from moxi_launcher.api import LaunchRequest

SID = "5f0c3a1e-7f43-4c8e-9e0a-1d2b3c4d5e6f"
FORK_ENV = "MOXI_MEMORY_SUBCONSCIOUS_FORK"


@pytest.fixture(autouse=True)
def no_turns(monkeypatch):
    fake_turns.hide(monkeypatch)


@pytest.fixture
def harness(tmp_path, monkeypatch) -> Harness:
    return Harness.on_path(tmp_path, monkeypatch)


@pytest.fixture
def work(tmp_path) -> Path:
    path = tmp_path / "work"
    path.mkdir()
    return path


@pytest.fixture
def memory_lists(monkeypatch):
    """A present memory module whose `SESSION_ENV` lists `FORK_ENV`."""
    real_installed, real_load = modules.installed, modules.load
    fake = SimpleNamespace(ok=True, state=modules.PRESENT,
                           session_env={FORK_ENV: "a subconscious fork"})
    monkeypatch.setattr(modules, "installed", lambda: [
        *(e for e in real_installed() if e.name != "memory"),
        SimpleNamespace(name="memory")])
    monkeypatch.setattr(modules, "load", lambda name, **kw: (
        fake if name == "memory" else real_load(name, **kw)))


@pytest.fixture
def spawned(monkeypatch):
    """The headless target's spawn, faked: each start is recorded and given the pid of a
    process that has already ended, so the session is not live after it."""
    seen = []

    def spawn(argv, **kw):
        done = subprocess.Popen([sys.executable, "-c", "pass"])
        done.wait()
        seen.append({"argv": list(argv), **kw})
        return done.pid

    monkeypatch.setattr(targets, "_spawn", spawn)
    return seen


def request(work: Path, *, rid: str = "r1", stamps=None, **typed) -> LaunchRequest:
    typed = {"target": "headless", "brief": "first brief", **typed}
    return LaunchRequest(typed=typed, cwd=str(work), request_id=rid, session_id=SID,
                         actor="memory", stamps=stamps or {})


def row(rid: str) -> store.LaunchRow | None:
    conn = store.connect(create=False)
    try:
        return store.get(conn, rid)
    finally:
        conn.close()


def test_the_launcher_offers_interface_4():
    assert 4 in module.INTERFACES
    for name in ("continue_headless", "run_log", "end_headless"):
        assert callable(getattr(api, name))


# -- stamps ---------------------------------------------------------------------------------

def test_a_launchs_stamps_land_after_the_scrub_and_are_kept(work, harness, spawned,
                                                            memory_lists, monkeypatch):
    monkeypatch.setenv(FORK_ENV, "the asker's")
    req = request(work, stamps={FORK_ENV: "recall-1"})
    assert api.preview(req).env[FORK_ENV] == "recall-1"
    api.launch(req)
    assert spawned[-1]["env"][FORK_ENV] == "recall-1"
    got = row("r1")
    assert got.env[FORK_ENV] == "recall-1"
    assert got.values[headless.STAMPS] == {FORK_ENV: "recall-1"}


def test_a_launch_without_stamps_records_none(work, harness, spawned):
    api.launch(request(work))
    assert headless.STAMPS not in row("r1").values


@pytest.mark.parametrize("name, said", [
    ("MOXI_LAUNCHER_REQUEST", "the launch stamps itself"),
    ("MOXI_TURNS_COMMANDS", "the launch stamps itself"),
    ("OTHER_NAME", "no module's variable"),
    ("MOXI_MEMORY_SOMETHING_ELSE", "not in memory's SESSION_ENV"),
])
def test_a_stamp_outside_its_owners_prefix_is_refused_with_nothing_written(
        work, harness, spawned, memory_lists, name, said):
    req = request(work, stamps={name: "1"})
    with pytest.raises(catalog.BadValue, match=said):
        api.launch(req)
    with pytest.raises(catalog.BadValue, match=said):
        api.preview(req)
    [refused] = api.check(req)
    assert refused.row == "stamps"
    assert not spawned and (not store.path().exists() or row("r1") is None)


# -- the headless resume -----------------------------------------------------------------------

def test_a_headless_resume_keeps_the_values_and_stamps(work, harness, spawned,
                                                       memory_lists):
    api.launch(request(work, stamps={FORK_ENV: "recall-1"}, model="sonnet",
                       tools=["Read"]))
    result = api.continue_headless(SID, "go on", request_id="r2")
    assert result.state == "started"
    first, again = spawned
    assert again["env"][FORK_ENV] == "recall-1"
    words = again["argv"]
    assert words[words.index("--resume") + 1] == SID
    assert "--session-id" not in words
    assert words[words.index("--model") + 1] == "sonnet"
    assert words[words.index("--tools") + 1] == "Read"
    assert any("go on" in w for w in words) and not any("first brief" in w for w in words)
    assert again["output"] == api.run_log("r2")
    got = row("r2")
    assert got.resume_of == "r1" and got.session_id == SID and got.target == "headless"
    assert got.values[headless.STAMPS] == {FORK_ENV: "recall-1"}
    # A resume of the resume keeps them too.
    api.continue_headless(SID, "and on", request_id="r3")
    assert spawned[-1]["env"][FORK_ENV] == "recall-1"


def test_a_headless_resume_is_launched_by_whom_its_caller_names(work, harness, spawned,
                                                                  monkeypatch):
    from moxi_launcher import first

    api.launch(request(work))
    assert row("r1").actor == "memory"
    api.continue_headless(SID, "go on", request_id="r2", by="memory")
    assert row("r2").actor == "memory"
    # Left out, the resume is the caller's, as before.
    monkeypatch.setattr(first, "actor", lambda env=None: "the-caller")
    api.continue_headless(SID, "and on", request_id="r3")
    assert row("r3").actor == "the-caller"


def test_a_headless_resume_retried_with_its_request_id_starts_nothing(work, harness,
                                                                     spawned):
    api.launch(request(work))
    api.continue_headless(SID, "go on", request_id="r2")
    again = api.continue_headless(SID, "go on", request_id="r2")
    assert again.duplicate and len(spawned) == 2


def test_a_running_session_is_not_resumed(work, harness, monkeypatch):
    import os

    monkeypatch.setattr(targets, "_spawn", lambda argv, **kw: os.getpid())
    api.launch(request(work))
    with pytest.raises(catalog.Refused) as refused:
        api.continue_headless(SID, "go on", request_id="r2")
    assert refused.value.code == "live" and row("r2") is None


def test_only_a_headless_launch_is_continued(work, harness, monkeypatch):
    monkeypatch.setattr(targets, "_spawn", lambda argv, **kw: 99)
    api.launch(request(work))
    conn = store.connect()
    try:
        conn.execute("UPDATE launches SET target = 'terminal' WHERE request_id = 'r1'")
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(catalog.Refused) as refused:
        api.continue_headless(SID, "go on")
    assert refused.value.code == "not-headless"


def test_an_unknown_session_is_not_launched(work, harness):
    with pytest.raises(catalog.Refused) as refused:
        api.continue_headless(SID, "go on")
    assert refused.value.code == "not-launched"


def test_the_run_log_is_the_targets():
    assert api.run_log("r9") == targets.run_log("r9")


# -- ending a headless launch -------------------------------------------------------------------

TREE = """
import json, os, subprocess, sys, time
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
with open(sys.argv[1], "w") as fh:
    json.dump({"pid": os.getpid(), "grandchild": child.pid}, fh)
time.sleep(120)
"""


def _tree(monkeypatch, tmp_path):
    """The headless target's spawn, starting a process that starts a grandchild."""
    script = tmp_path / "tree.py"
    script.write_text(TREE)
    pids = tmp_path / "pids.json"

    def spawn(argv, **kw):
        return proc.spawn([sys.executable, str(script), str(pids)], detached=True,
                          output=kw["output"])

    monkeypatch.setattr(targets, "_spawn", spawn)
    return pids


def _gone(pid: int) -> bool:
    found = proc.identity(pid)
    return found is None or not proc.alive(found)


def test_end_headless_ends_the_launch_and_its_grandchild(work, harness, tmp_path,
                                                         monkeypatch):
    pids = _tree(monkeypatch, tmp_path)
    api.launch(request(work))
    wait_for(lambda: pids.exists() and pids.read_text(), timeout=30)
    seen = json.loads(pids.read_text())
    got = api.end_headless("r1")
    assert got.outcome == headless.ENDED
    wait_for(lambda: _gone(seen["pid"]) and _gone(seen["grandchild"]), timeout=10)
    assert api.end_headless("r1").outcome == headless.GONE


def test_end_headless_leaves_a_pid_whose_start_time_no_longer_matches(
        work, harness, tmp_path, monkeypatch):
    pids = _tree(monkeypatch, tmp_path)
    api.launch(request(work))
    wait_for(lambda: pids.exists() and pids.read_text(), timeout=30)
    seen = json.loads(pids.read_text())
    conn = store.connect()
    try:
        conn.execute("UPDATE launches SET pid_started = pid_started - 1000 "
                     "WHERE request_id = 'r1'")
        conn.commit()
    finally:
        conn.close()
    try:
        assert api.end_headless("r1").outcome == headless.GONE
        time.sleep(0.2)
        assert not _gone(seen["pid"])
    finally:
        headless.end_tree(proc.identity(seen["pid"]))


def test_end_headless_of_an_unknown_launch_is_gone(work):
    assert api.end_headless("nothing").outcome == headless.GONE

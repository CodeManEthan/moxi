"""The turns listeners: the start line, its cost, and the snapshot trigger."""

from __future__ import annotations

import importlib.metadata
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import moxi.paths
import moxi.proc
import pytest
from moxi_dev.testing import wait_for
from moxi_turns import SessionEvent

from moxi_notes import listener, listeners
from moxi_notes.resolve import PROJECTS_UNREADABLE

from notes_kit import configure, git_env, home, repo

SOURCES = ("startup", "resume", "clear", "compact")


@dataclass(frozen=True)
class Event:
    """A stand-in for turns' event once it carries the session's working directory."""

    event: str
    harness: str = "claude"
    session_id: str | None = "s-1"
    env: dict = field(default_factory=dict)
    source: str | None = None
    cwd: str | None = None


@pytest.fixture(autouse=True)
def _git(tmp_path, monkeypatch):
    git_env(tmp_path, monkeypatch)


@pytest.fixture
def alpha() -> Path:
    return repo(home() / "code" / "alpha")


def expected(alpha) -> str:
    return f"Notes directory: {home() / 'notes' / 'code' / 'alpha'}"


# --- the table ---------------------------------------------------------------------------

def test_notes_registers_its_two_rows_with_turns():
    found = [e for e in importlib.metadata.entry_points(group="moxi.turns.listeners")
             if e.name == "notes"]
    assert [e.value for e in found] == ["moxi_notes.listeners"]
    assert listeners.LISTENERS == ((("start",), "moxi_notes.listener:start"),
                                   (("turn", "gone"), "moxi_notes.listener:fire"))


def test_the_table_imports_nothing():
    code = ("import sys; before = set(sys.modules); import moxi_notes.listeners; "
            "print(sorted(set(sys.modules) - before))")
    done = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True,
                          text=True, timeout=30, check=True)
    assert json.loads(done.stdout.replace("'", '"')) == ["moxi_notes",
                                                         "moxi_notes.listeners"]


# --- the start line ----------------------------------------------------------------------

def test_every_start_source_gives_the_same_line(alpha):
    lines = {listener.start(SessionEvent("start", "claude", "s-1", env={"PWD": str(alpha)},
                                         source=source)) for source in SOURCES}
    assert lines == {expected(alpha)}


def test_the_sessions_override_wins(alpha):
    there = home() / "elsewhere" / "alpha"
    event = Event("start", env={"PWD": str(alpha), "MOXI_NOTES_DIR": str(there)},
                  source="startup")
    assert listener.start(event) == f"Notes directory: {there}"


def test_the_processs_own_override_is_not_the_sessions(alpha, monkeypatch):
    monkeypatch.setenv("MOXI_NOTES_DIR", str(home() / "elsewhere"))
    event = Event("start", env={"PWD": str(alpha)}, source="startup")
    assert listener.start(event) == expected(alpha)


def test_no_notes_directory_gives_no_line():
    event = Event("start", env={"PWD": str(home())}, source="startup")
    assert listener.start(event) is None


def test_the_folder_is_the_events_cwd_then_pwd_then_the_process(alpha, monkeypatch):
    beta = repo(home() / "code" / "beta")
    beta_line = f"Notes directory: {home() / 'notes' / 'code' / 'beta'}"
    assert listener.start(Event("start", env={"PWD": str(beta)}, cwd=str(alpha))) \
        == expected(alpha)
    for cwd in (None, "relative/alpha", str(home() / "gone")):
        assert listener.start(Event("start", env={"PWD": str(beta)}, cwd=cwd)) == beta_line
    monkeypatch.chdir(alpha)
    for pwd in (None, "relative", str(home() / "gone")):
        env = {} if pwd is None else {"PWD": pwd}
        assert listener.start(Event("start", env=env)) == expected(alpha)
        assert listener.start(SessionEvent("start", "claude", "s-1", env=env)) \
            == expected(alpha)


def test_the_projects_warning_rides_on_the_line(alpha, monkeypatch):
    from moxi_notes import resolve

    monkeypatch.setattr(resolve, "_project", lambda path: (None, PROJECTS_UNREADABLE))
    line = listener.start(Event("start", env={"PWD": str(alpha)}))
    assert line == f"{expected(alpha)} ({PROJECTS_UNREADABLE})"


START_COST = r"""
import json, sys, time
t0 = time.perf_counter()
from moxi_notes.listener import start
class E:
    event = "start"; source = "startup"; env = {"PWD": sys.argv[1]}
line = start(E())
print(json.dumps([time.perf_counter() - t0, line]))
"""


@pytest.mark.timed
def test_the_start_line_costs_under_its_budget_at_the_95th_percentile(alpha):
    budget = {"win32": 0.150}.get(sys.platform, 0.050)
    costs = []
    for _ in range(20):
        done = subprocess.run([sys.executable, "-c", START_COST, str(alpha)],
                              capture_output=True, text=True, timeout=30, check=True)
        cost, line = json.loads(done.stdout)
        assert line == expected(alpha)
        costs.append(cost)
    costs.sort()
    assert costs[int(len(costs) * 0.95) - 1] < budget, costs


# --- the trigger -------------------------------------------------------------------------

@pytest.fixture
def notes_repo():
    return repo(home() / "notes", commit=True)


@pytest.fixture
def spawned(monkeypatch):
    calls = []

    def spawn(argv, **kw):
        calls.append((list(argv), kw))
        return 4242

    monkeypatch.setattr(moxi.proc, "spawn", spawn)
    return calls


def turn():
    return SessionEvent("turn", "claude", "s-1", turn_id="t-1", outcome="completed")


def gone():
    return SessionEvent("gone", "claude", "s-1", reason="exit")


def fired() -> Path:
    return moxi.paths.Layout("notes").runtime_dir() / "fired"


def test_fire_starts_the_settling_pass_detached_and_returns(notes_repo, spawned):
    assert listener.fire(gone()) is None
    [(argv, kw)] = spawned
    assert argv == [str(moxi.paths.script("moxi-notes-snapshot")), "--settle"]
    assert kw == {"detached": True, "output": os.devnull, "cwd": str(notes_repo)}


def test_a_turn_within_a_minute_of_the_last_fired_one_starts_nothing(notes_repo, spawned):
    listener.fire(turn())
    listener.fire(turn())
    assert len(spawned) == 1
    old = time.time() - 61
    os.utime(fired(), (old, old))
    listener.fire(turn())
    assert len(spawned) == 2


def test_gone_is_never_debounced(notes_repo, spawned):
    listener.fire(turn())
    listener.fire(gone())
    listener.fire(gone())
    assert len(spawned) == 3


def test_nothing_fires_with_auto_off_or_without_a_git_repo(spawned):
    root = home() / "notes"
    root.mkdir()
    listener.fire(gone())
    assert spawned == []
    repo(root)
    configure(snapshot__auto=False)
    listener.fire(gone())
    listener.fire(turn())
    assert spawned == []
    assert not fired().exists()


def test_a_fired_pass_runs_in_its_own_process(notes_repo, monkeypatch):
    pids = []
    real = moxi.proc.spawn

    def spawn(argv, **kw):
        pids.append(real(argv, **kw))
        return pids[-1]

    monkeypatch.setattr(moxi.proc, "spawn", spawn)
    (notes_repo / "a.md").write_text("alpha\n", encoding="utf-8")
    listener.fire(gone())
    [pid] = pids
    codes = []
    wait_for(lambda: codes.append(moxi.proc.status(pid)) or codes[-1] is not None,
             timeout=60)
    assert codes[-1] == 0
    state = json.loads((notes_repo / ".git" / "moxi-repo" / "state.json").read_text())
    assert "notes/snapshot" in state["specs"]

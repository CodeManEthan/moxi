"""The stop: usage's guard for turns that holds a session past its stop mark to wrap-up,
its latch, its words and its declaration. Calls are turns' own `ToolCall` where turns'
answers matter and plain objects shaped like it elsewhere; the session's working
directory and handoff folder are real directories in the test's tmp."""

import importlib
import json
import logging
import os
from types import SimpleNamespace

import pytest

from moxi import modules
from moxi_usage import stop

from conftest import write_config

SESSION = "0190b000-0000-7000-8000-0000000004d1"
AGENT = "a0190b00000004d1"
NOW = 1793400000.0

turns = modules.load("turns").api


@pytest.fixture(autouse=True)
def fresh():
    stop.reset()
    yield
    stop.reset()


@pytest.fixture()
def where(tmp_path):
    """The session's working directory and its handoff folder, real paths."""
    root = os.path.realpath(tmp_path)
    work = os.path.join(root, "work", "project")
    handoffs = os.path.join(root, "handoffs")
    os.makedirs(work)
    os.makedirs(handoffs)
    return SimpleNamespace(work=work, handoffs=handoffs, home=os.path.join(root, "home"))


def stamps(where, mark="2.5x"):
    return {"MOXI_USAGE_STOP_AT": mark, "MOXI_USAGE_STOP_HANDOFFS": where.handoffs,
            "HOME": where.home}


def call(where, tool="Grep", tool_input=None, *, agent_id=None, env=None,
         turns_call=False):
    fields = dict(harness="claude", session_id=SESSION, agent_id=agent_id, tool=tool,
                  tool_use_id="toolu_1", input=tool_input or {}, cwd=where.work,
                  record_path=None, env=stamps(where) if env is None else env)
    if turns_call:
        return turns.ToolCall(**fields)
    return SimpleNamespace(**fields, prompts=())


def bash(where, command, **kwargs):
    return call(where, "Bash", {"command": command}, **kwargs)


def latch():
    assert stop.latch("claude", SESSION, mark="2.5x", tokens=400_000, threshold=399_000,
                      model="claude-opus-5-5", at=NOW)


# --- the declaration --------------------------------------------------------------------

def loader():
    """turns' guard loader, which turns' own endpoint runs."""
    return importlib.import_module("moxi_turns.guards")


def test_the_guard_is_declared_as_turns_reads_it():
    from moxi_usage.guards import GUARDS

    g = next(g for g in GUARDS if g.name == "stop")
    assert isinstance(g, turns.Guard)
    assert (g.who, g.judge, g.switch, g.sessions, g.on_error) == (
        "main", "moxi_usage.stop:judge", None, "moxi_usage.stop:on_for", "allow")
    assert g.calls == {"claude": ("*",)}
    assert g.problems() == []


def test_it_runs_only_in_a_session_whose_launch_stamped_a_stop_mark(where):
    assert stop.on_for({"MOXI_USAGE_STOP_AT": "2.5x"}) is True
    assert stop.on_for({"MOXI_USAGE_STOP_AT": ""}) is False
    assert stop.on_for({}) is False
    on = loader().load(on=[], env=stamps(where)).get("usage.stop")
    assert on is not None and on.by == ("sessions",)
    got = loader().load(on=[], env={})
    assert got.get("usage.stop") is None
    assert "usage.stop" in [f.id for f in got.off]
    assert not [p for p in got.problems if p.id.startswith("usage")]


# --- before the latch -------------------------------------------------------------------

@pytest.mark.parametrize("tool,tool_input", [
    ("Grep", {"pattern": "x"}), ("Bash", {"command": "pytest -q"}),
    ("Write", {"file_path": "/elsewhere/a.py", "content": "x"}), ("Agent", {}),
])
def test_with_no_latch_every_call_runs(where, tool, tool_input):
    assert stop.decide(call(where, tool, tool_input)) is None
    assert stop.judge(call(where, tool, tool_input, turns_call=True)) is None


# --- latched ----------------------------------------------------------------------------

@pytest.mark.parametrize("tool,tool_input", [
    ("Write", {"file_path": "{handoffs}/handoff.md", "content": "done, left, where"}),
    ("Edit", {"file_path": "{handoffs}/handoff.md", "old_string": "a", "new_string": "b"}),
    ("Read", {"file_path": "{handoffs}/handoff.md", "limit": 50}),
    ("Bash", {"command": "git status --short"}),
    ("Bash", {"command": "git add -A"}),
    ("Bash", {"command": "git commit -m 'wrap up'"}),
    ("Bash", {"command": "moxi repo rules"}),
    ("Bash", {"command": "moxi launcher handoff dir"}),
    ("Bash", {"command": "moxi launcher relaunch --self --handoff {handoffs}/handoff.md"}),
    ("Bash", {"command": "moxi comms send fleet-lead 'done; costs $5' --posture none"}),
    ("Bash", {"command": "moxi comms reply 4611686018427396090 \"on it\""}),
    ("Bash", {"command": "moxi comms whoami"}),
    ("mcp__comms__send", {"to": "fleet-lead", "text": "done"}),
    ("mcp__comms__history", {}),
    ("Skill", {"skill": "handoff"}),
    ("ToolSearch", {"query": "select:mcp__comms__send", "max_results": 1}),
    ("ToolSearch", {"query": "notebook jupyter", "max_results": 5}),
])
def test_latched_only_wrap_up_runs(where, tool, tool_input):
    latch()
    filled = {k: v.format(handoffs=where.handoffs) if isinstance(v, str) else v
              for k, v in tool_input.items()}
    assert stop.decide(call(where, tool, filled)) is None


@pytest.mark.parametrize("tool,tool_input,why", [
    ("Grep", {"pattern": "x"}, "Grep is not a wrap-up operation"),
    ("Agent", {"prompt": "go on"}, "Agent is not a wrap-up operation"),
    ("Bash", {"command": "pytest -q"}, "pytest is not a wrap-up command"),
    ("Bash", {"command": "git push"}, "git push is not a wrap-up git form"),
    ("Write", {"file_path": "{work}/a.md", "content": "x"},
     "the hand-off file is outside every hand-off root"),
    ("Write", {"file_path": "{handoffs}/a.py", "content": "x"},
     "a hand-off file must end in .md"),
    ("Read", {"file_path": "{handoffs}/handoff.md"},
     "a hand-off Read needs a limit from 1 to 200"),
    ("Bash", {"command": "moxi launcher relaunch --self --handoff {work}/h.md"},
     "moxi is not a wrap-up command"),
    ("Bash", {"command": "moxi launcher relaunch --self"}, "moxi is not a wrap-up command"),
    ("Bash", {"command": "moxi comms send fleet-lead \"$(cat notes.md)\""},
     "'$' outside single quotes"),
    ("Bash", {"command": "cd {work} && moxi comms send fleet-lead done"},
     "moxi is not a wrap-up command"),
    ("Bash", {"command": "moxi comms exit-session x"}, "moxi is not a wrap-up command"),
    ("mcp__notes__search", {}, "mcp__notes__search is not a wrap-up operation"),
])
def test_latched_everything_else_is_refused_with_the_stop_text(where, tool, tool_input, why):
    latch()
    filled = {k: v.format(handoffs=where.handoffs, work=where.work) if isinstance(v, str)
              else v for k, v in tool_input.items()}
    reason = stop.decide(call(where, tool, filled))
    assert reason == (f"This session is past its stop mark (2.5x). Only wrap-up works now: "
                      f"commit your work with git, write your handoff in {where.handoffs}, "
                      "send what you owe on comms, then run moxi launcher relaunch --self "
                      f"--handoff FILE. ({why})")
    denied = stop.judge(call(where, tool, filled, turns_call=True))
    assert isinstance(denied, turns.Deny) and denied.reason == reason


def test_a_tool_toolsearch_loads_is_still_judged_when_called(where):
    latch()
    assert stop.decide(call(where, "ToolSearch", {"query": "select:NotebookEdit,WebFetch",
                                                  "max_results": 2})) is None
    for tool in ("NotebookEdit", "WebFetch"):
        reason = stop.decide(call(where, tool, {}))
        assert reason.endswith(f"({tool} is not a wrap-up operation)")
        assert isinstance(stop.judge(call(where, tool, {}, turns_call=True)), turns.Deny)


def test_a_subagents_calls_are_not_the_stops(where):
    latch()
    assert stop.decide(call(where, "Grep", {"pattern": "x"}, agent_id=AGENT)) is None


def test_a_launch_that_stamped_no_handoffs_may_write_no_notes(where):
    latch()
    env = {"MOXI_USAGE_STOP_AT": "2.5x", "HOME": where.home}
    reason = stop.decide(call(where, "Write", {"file_path": f"{where.handoffs}/h.md",
                                               "content": "x"}, env=env))
    assert reason.startswith("This session is past its stop mark (2.5x)")
    assert f"write your handoff in {stop.NO_HANDOFF_DIR}," in reason
    assert reason.endswith("(the hand-off file is outside every hand-off root)")


def test_the_words_are_the_users_setting(where):
    write_config('[stop]\ndeny_text = "Stopped at {mark}; notes go in {folder}."\n')
    latch()
    assert stop.decide(bash(where, "ls")) == (
        f"Stopped at 2.5x; notes go in {where.handoffs}. (ls is not a wrap-up command)")
    write_config('[stop]\ndeny_text = "Stopped {at {mark}"\n')
    assert stop.decide(bash(where, "ls")) == "Stopped {at {mark} (ls is not a wrap-up command)"


def test_an_unreadable_config_keeps_the_shipped_words(where, caplog):
    write_config("[stop\n")
    latch()
    with caplog.at_level(logging.WARNING, logger="moxi.usage"):
        assert stop.decide(bash(where, "ls")).startswith(
            "This session is past its stop mark (2.5x).")
        stop.decide(bash(where, "ls"))
    assert [r.message for r in caplog.records] == [
        "usage's config could not be read; the stop uses its shipped words"]


# --- the latch --------------------------------------------------------------------------

def test_the_latch_is_written_once_and_held_after_it_is_found(where):
    path = stop.latch_file("claude", SESSION)
    assert stop.latched("claude", SESSION) is None
    latch()
    doc = json.loads(path.read_text())
    assert (doc["schema"], doc["mark"], doc["tokens"], doc["fired_at"]) == (
        1, "2.5x", 400_000, NOW)
    assert not stop.latch("claude", SESSION, mark="3x", tokens=1, threshold=1, model=None,
                          at=NOW + 1)
    assert json.loads(path.read_text())["mark"] == "2.5x"
    stop.reset()                                  # a new endpoint: found by one stat
    assert stop.latched("claude", SESSION)["mark"] == "2.5x"
    path.unlink()
    assert stop.latched("claude", SESSION)["mark"] == "2.5x"   # held in memory
    assert stop.decide(bash(where, "ls")) is not None


def test_a_latch_that_cannot_be_read_is_no_latch_and_logged_once(where, caplog):
    path = stop.latch_file("claude", SESSION)
    path.parent.mkdir(parents=True)
    path.write_text("not json")
    with caplog.at_level(logging.WARNING, logger="moxi.usage"):
        assert stop.decide(bash(where, "ls")) is None
        assert stop.decide(bash(where, "pytest")) is None
    assert len(caplog.records) == 1
    assert "can't be read; it counts as no latch" in caplog.records[0].message
    assert stop.latch("claude", SESSION, mark="2.5x", tokens=1, threshold=1, model=None,
                      at=NOW)                     # a broken file is written over
    assert stop.decide(bash(where, "ls")) is not None


def test_an_id_that_cannot_name_a_file_has_no_latch(where):
    assert stop.latch_file("claude", "../x") is None
    assert stop.latch_file("cursor", SESSION) is None
    assert not stop.latch("claude", "../x", mark="2.5x", tokens=1, threshold=1, model=None,
                          at=NOW)
    assert stop.latched("claude", "../x") is None


def test_a_broken_judge_lets_the_call_run(where, monkeypatch, caplog):
    def broken(call, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(stop, "decide", broken)
    latch()
    with caplog.at_level(logging.WARNING, logger="moxi.usage"):
        assert stop.judge(bash(where, "ls", turns_call=True)) is None
    assert caplog.records[0].message == "the stop could not judge a call; it was allowed"


def test_the_sweep_removes_latches_past_their_age():
    old, new = stop.latch_file("claude", "old-1"), stop.latch_file("claude", "new-1")
    old.parent.mkdir(parents=True)
    for path, age in ((old, 8), (old.with_name(old.name + ".lock"), 8), (new, 1)):
        path.write_text("{}")
        os.utime(path, (NOW - age * 86400, NOW - age * 86400))
    assert stop.sweep(now=NOW, max_age_days=7) == (1, [])
    assert sorted(p.name for p in old.parent.iterdir()) == [new.name]

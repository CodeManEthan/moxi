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
    """The session's working directory and its handoff folder, real paths, and a home
    whose Claude folder holds an inline handoff skill."""
    root = os.path.realpath(tmp_path)
    work = os.path.join(root, "work", "project")
    handoffs = os.path.join(root, "handoffs")
    os.makedirs(work)
    os.makedirs(handoffs)
    home = os.path.join(root, "home")
    skill(os.path.join(home, ".claude"), "handoff")
    return SimpleNamespace(work=work, handoffs=handoffs, home=home, root=root)


def skill(base, name, *, context=None, command=False):
    """Write the skill `name` under `base` (a `.claude` folder or a plugin's), as
    `skills/<name>/SKILL.md` or, with `command`, the older `commands/<name>.md`; with
    `context`, its frontmatter sets it. Returns the file's path."""
    path = os.path.join(base, "commands", f"{name}.md") if command else \
        os.path.join(base, "skills", name, "SKILL.md")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    front = f"name: {name}\ndescription: a test skill\n"
    if context is not None:
        front += f"context: {context}\n"
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(f"---\n{front}---\n\n# {name}\n\ncontext: fork is body text here.\n")
    return path


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


def skill_call(where, name, **kwargs):
    return call(where, "Skill", {"skill": name}, **kwargs)


#: The stop text the test's launch reads, before the reason in brackets.
def stopped(where):
    return (f"This session is past its stop mark (2.5x). Only wrap-up works now: commit "
            f"your work with git, write your handoff in {where.handoffs}, send what you "
            "owe on comms, then run moxi launcher relaunch --self --handoff FILE.")


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


# --- skills past the stop --------------------------------------------------------------

def test_a_skill_that_runs_inline_loads(where):
    latch()
    assert stop.decide(skill_call(where, "handoff")) is None
    assert stop.decide(skill_call(where, "/handoff")) is None
    assert stop.judge(skill_call(where, "handoff", turns_call=True)) is None


@pytest.mark.parametrize("context", ["fork", "'fork'", '"Fork"', "fork  # in a subagent"])
def test_a_skill_that_runs_forked_is_refused_with_the_stop_text(where, context):
    skill(os.path.join(where.home, ".claude"), "survey", context=context)
    latch()
    reason = stop.decide(skill_call(where, "survey"))
    assert reason == f"{stopped(where)} (skill survey runs forked in a subagent)"
    denied = stop.judge(skill_call(where, "survey", turns_call=True))
    assert isinstance(denied, turns.Deny) and denied.reason == reason


def test_a_context_other_than_fork_and_body_text_load_inline(where):
    skill(os.path.join(where.home, ".claude"), "plain", context="inline")
    latch()
    # The fixture's body holds "context: fork" below the frontmatter: not read.
    assert stop.decide(skill_call(where, "plain")) is None


def test_a_skill_with_no_file_found_is_refused(where):
    latch()
    reason = stop.decide(skill_call(where, "simplify"))
    assert reason == (f"{stopped(where)} (no file of skill simplify is found, so whether "
                      "it runs in a subagent can't be told)")


@pytest.mark.parametrize("tool_input", [{}, {"skill": ""}, {"skill": "../handoff"},
                                        {"skill": "a/b"}, {"skill": 3}, {"skill": "a::b"}])
def test_a_skill_call_naming_no_skill_is_refused(where, tool_input):
    latch()
    reason = stop.decide(call(where, "Skill", tool_input))
    assert reason.endswith("(Skill names no skill a file can be found for)")


def test_project_skills_are_found_up_to_the_repositorys_root(where):
    repo = os.path.dirname(where.work)
    os.makedirs(os.path.join(repo, ".git"))
    skill(os.path.join(where.work, ".claude"), "here")
    skill(os.path.join(repo, ".claude"), "above", context="fork")
    skill(os.path.join(where.root, ".claude"), "outside")
    latch()
    assert stop.decide(skill_call(where, "here")) is None
    assert stop.decide(skill_call(where, "above")).endswith("runs forked in a subagent)")
    assert "no file of skill outside is found" in stop.decide(skill_call(where, "outside"))


def test_a_fork_anywhere_its_name_is_found_refuses(where):
    skill(os.path.join(where.work, ".claude"), "handoff", context="fork")
    latch()
    assert stop.decide(skill_call(where, "handoff")).endswith(
        "(skill handoff runs forked in a subagent)")


def test_an_older_command_file_is_read_too(where):
    skill(os.path.join(where.home, ".claude"), "deploy", context="fork", command=True)
    skill(os.path.join(where.work, ".claude"), "notes", command=True)
    latch()
    assert stop.decide(skill_call(where, "deploy")).endswith("runs forked in a subagent)")
    assert stop.decide(skill_call(where, "notes")) is None


def test_plugin_skills_are_found_through_claudes_plugin_list(where):
    claude_dir = os.path.join(where.home, ".claude")
    plugin = os.path.join(where.root, "plugins", "kit", "1.0.0")
    skill(plugin, "tidy")
    skill(plugin, "audit", context="fork")
    os.makedirs(os.path.join(claude_dir, "plugins"), exist_ok=True)
    with open(os.path.join(claude_dir, "plugins", "installed_plugins.json"), "w") as fh:
        json.dump({"version": 2, "plugins": {"kit@market": [
            {"scope": "user", "installPath": plugin}]}}, fh)
    latch()
    assert stop.decide(skill_call(where, "kit:tidy")) is None
    assert stop.decide(skill_call(where, "kit:audit")).endswith(
        "(skill kit:audit runs forked in a subagent)")
    assert "no file of skill other:tidy" in stop.decide(skill_call(where, "other:tidy"))


def test_synced_account_skills_are_found(where):
    bucket = os.path.join(where.home, ".claude", "skills", "synced", "bucket-1")
    os.makedirs(os.path.join(bucket, "docs"))
    with open(os.path.join(bucket, "docs", "SKILL.md"), "w", encoding="utf-8") as fh:
        fh.write("---\nname: docs\n---\n")
    latch()
    assert stop.decide(skill_call(where, "account-skills:docs")) is None
    assert stop.decide(skill_call(where, "docs")) is None


def test_claudes_config_dir_is_the_sessions(where):
    config_dir = os.path.join(where.root, "claude-config")
    skill(config_dir, "wrap")
    latch()
    env = {**stamps(where), "CLAUDE_CONFIG_DIR": config_dir}
    assert stop.decide(skill_call(where, "wrap", env=env)) is None
    assert stop.decide(skill_call(where, "wrap")).endswith("can't be told)")


def test_an_unreadable_skill_file_is_refused(where):
    path = skill(os.path.join(where.home, ".claude"), "broken")
    with open(path, "wb") as fh:
        fh.write(b"---\ncontext: \xff\xfe\n---\n")
    latch()
    assert stop.decide(skill_call(where, "broken")).endswith(f"{path} can't be read)")


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

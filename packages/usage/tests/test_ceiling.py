"""The worker ceiling: usage's guard for turns, its judge, its markers, its switch and
install question, and the `marks` doctor row's warning. Calls are turns' own `ToolCall`
where turns' answers matter, and plain objects shaped like it elsewhere; transcripts are
sessions' builders; turns' guard loader reads the installed entry points."""

import importlib
import json
import logging
import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from moxi import modules
from moxi.config import ConfigError
from moxi.paths import Layout
from moxi.units import switch_problem
from moxi_sessions import api as sessions
from moxi_usage import ceiling, cli, doctor, settings

from conftest import write_config

ClaudeTranscript = sessions.testing.ClaudeTranscript
CodexRollout = sessions.testing.CodexRollout
token_usage = sessions.testing.token_usage
usage = sessions.testing.usage

SESSION = "0190b000-0000-7000-8000-0000000004c1"
AGENT = "a0190b00000004c1"
THREAD = "0190c000-0000-7000-8000-0000000004c2"
MODEL = "claude-opus-5-5"
WARN, HARD = 100_000, 150_000
THRESHOLDS = f"[ceiling]\nwarn_tokens = {WARN}\nhard_tokens = {HARD}\n"
NOW = 1793400000.0

turns = modules.load("turns").api


@pytest.fixture(autouse=True)
def fresh():
    ceiling.reset()
    yield
    ceiling.reset()


def loader():
    """turns' guard loader, which turns' own endpoint runs."""
    return importlib.import_module("moxi_turns.guards")


def write_turns_config(text: str) -> None:
    path = Layout("turns").config_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class Agent:
    """A Claude session with one subagent whose context the test sets call by call."""

    def __init__(self, agent_id=AGENT):
        self.t = ClaudeTranscript(Path(sessions.root("claude")), session_id=SESSION)
        self.t.user("go")
        self.t.assistant("ok", usage=usage(input=10_000, cache_read=0, cache_write=0),
                         model=MODEL)
        self.agent_id = agent_id
        self.child = self.t.subagent(agent_id)

    def context(self, tokens):
        self.child.user("look")
        self.child.assistant("found", usage=usage(input=tokens - 10, cache_read=10,
                                                  cache_write=0), model=MODEL)
        self.t.write()

    def call(self, tool="Grep", tool_input=None, *, cwd="/work/project", agent_id=None,
             turns_call=False):
        fields = dict(harness="claude", session_id=SESSION,
                      agent_id=self.agent_id if agent_id is None else agent_id, tool=tool,
                      tool_use_id="toolu_1", input=tool_input or {}, cwd=cwd,
                      record_path=str(self.t.path), env={})
        if turns_call:
            return turns.ToolCall(**fields)
        return SimpleNamespace(**fields, prompts=())


def started(base=20_000):
    """An agent whose first request is `base` tokens."""
    a = Agent()
    a.context(base)
    return a


# --- the declaration --------------------------------------------------------------------

def test_the_guard_is_declared_as_turns_reads_it():
    from moxi_usage.guards import GUARDS

    (g,) = GUARDS
    assert isinstance(g, turns.Guard)
    assert (g.name, g.who, g.judge, g.switch, g.on_error) == (
        "ceiling", "agents", "moxi_usage.ceiling:judge", "ceiling.on", "allow")
    assert g.calls == {"claude": ("*",), "codex": ("*",)}
    assert g.problems() == []


def test_the_switch_passes_the_cores_check_and_is_the_install_question():
    assert switch_problem(settings.schema(), "ceiling.on") is None
    found = modules.load("usage")
    assert found.ok, found.reason
    text = found.questions["ceiling.on"]
    assert text.startswith("the worker ceiling (") and text.endswith(")")
    assert "\n" not in text


# --- the four states of the rule --------------------------------------------------------

def test_off_with_thresholds_set_the_guard_does_not_run():
    write_config(THRESHOLDS)
    got = loader().load(on=[])
    assert got.get("usage.ceiling") is None
    assert "usage.ceiling" in [f.id for f in got.off]
    assert not [p for p in got.problems if p.id.startswith("usage")]
    assert doctor.marks_row(now=NOW).state == "ok"


def test_on_by_the_switch_with_both_thresholds_the_ceiling_acts():
    write_config(THRESHOLDS + "on = true\n")
    on = loader().load(on=[]).get("usage.ceiling")
    assert on is not None and on.by == ("switch",)
    a = started()
    a.context(20_000 + WARN)
    assert isinstance(ceiling.judge(a.call()), turns.Allow)
    a.context(20_000 + HARD)
    assert isinstance(ceiling.judge(a.call()), turns.Deny)
    assert doctor.marks_row(now=NOW).state == "ok"


@pytest.mark.parametrize("config", [
    "[ceiling]\non = true\nwarn_tokens = 100000\n",
    "[ceiling]\non = true\nhard_tokens = 150000\n",
    "[ceiling]\non = true\n",
])
def test_on_with_a_threshold_at_zero_every_call_is_allowed_and_marks_warns(config):
    write_config(config)
    a = started()
    a.context(5_000_000)
    for tool in ("Grep", "Bash", "Agent"):
        assert ceiling.judge(a.call(tool, {"command": "rm -rf x"})) is None
    assert not ceiling.state_dir().exists()
    row = doctor.marks_row(now=NOW)
    assert row.state == "warn"
    assert "the worker ceiling is on (ceiling.on)" in row.detail
    assert "allows every call" in row.detail


def test_on_by_turns_list_alone_the_ceiling_acts_and_marks_warns_without_thresholds():
    write_turns_config('[guards]\non = ["usage.ceiling"]\n')
    on = loader().load().get("usage.ceiling")
    assert on is not None and on.by == ("list",)
    row = doctor.marks_row(now=NOW)
    assert row.state == "warn" and "on (turns' guards.on)" in row.detail
    write_config(THRESHOLDS)                 # ceiling.on stays false
    assert doctor.marks_row(now=NOW).state == "ok"
    a = started()
    a.context(20_000 + HARD)
    assert isinstance(ceiling.judge(a.call()), turns.Deny)


def test_marks_reads_only_ceiling_on_when_turns_is_absent(monkeypatch):
    write_turns_config('[guards]\non = ["usage.ceiling"]\n')
    monkeypatch.setattr(modules, "available", lambda name: name != "turns")
    assert doctor.marks_row(now=NOW).state == "ok"
    write_config("[ceiling]\non = true\n")
    assert doctor.marks_row(now=NOW).state == "warn"


# --- the bands --------------------------------------------------------------------------

def test_below_the_warning_band_the_call_runs_with_nothing_added():
    write_config(THRESHOLDS)
    a = started()
    a.context(20_000 + WARN - 1)
    assert ceiling.judge(a.call()) is None
    assert not ceiling.state_dir().exists()


def test_the_warning_is_one_added_line_once():
    write_config(THRESHOLDS)
    a = started()
    a.context(20_000 + WARN + 5)
    got = ceiling.judge(a.call(turns_call=True))
    assert isinstance(got, turns.Allow)
    assert "100,005 tokens" in got.line and "(now 120,005)" in got.line
    assert "150,000 tokens" in got.line
    a.context(20_000 + WARN + 900)
    assert ceiling.judge(a.call()) is None
    ceiling.reset()                          # the endpoint starts again: the marker holds
    assert ceiling.judge(a.call()) is None


def test_past_the_hard_mark_the_call_is_refused_with_the_deny_text():
    write_config(THRESHOLDS + 'deny_text = "Stop: {growth} grown, past {hard}."\n')
    a = started()
    a.context(20_000 + HARD + 1)
    got = ceiling.judge(a.call("Grep"))
    assert isinstance(got, turns.Deny)
    assert got.reason.startswith("Stop: 150,001 grown, past 150,000.")
    assert "Grep is not a wrap-up operation" in got.reason


def test_the_hard_latch_survives_a_compaction_and_a_restart():
    write_config(THRESHOLDS)
    a = started()
    a.context(20_000 + HARD)
    assert isinstance(ceiling.judge(a.call()), turns.Deny)
    a.child.compaction()
    a.context(30_000)                        # the context shrank: growth is small again
    assert isinstance(ceiling.judge(a.call()), turns.Deny)
    ceiling.reset()
    assert isinstance(ceiling.judge(a.call()), turns.Deny)
    marker = ceiling.marker_dir(SESSION, AGENT) / f"hard-{HARD}"
    assert json.loads(marker.read_text())["growth"] == HARD


def test_a_new_hard_threshold_is_its_own_latch():
    write_config(THRESHOLDS)
    a = started()
    a.context(20_000 + HARD)
    assert isinstance(ceiling.judge(a.call()), turns.Deny)
    write_config("[ceiling]\nwarn_tokens = 100000\nhard_tokens = 900000\n")
    a.context(20_000 + HARD + 10)
    assert isinstance(ceiling.judge(a.call()), turns.Allow)      # warned, not latched


def test_return_tools_run_before_any_measurement_even_when_latched(monkeypatch):
    write_config(THRESHOLDS + 'return_tools = ["StructuredOutput", "Finish"]\n')
    a = started()
    a.context(20_000 + HARD)
    assert isinstance(ceiling.judge(a.call()), turns.Deny)
    monkeypatch.setattr(ceiling, "_measure", lambda *a: pytest.fail("measured"))
    monkeypatch.setattr(ceiling, "read_hard", lambda *a: pytest.fail("read the latch"))
    assert ceiling.judge(a.call("StructuredOutput")) is None
    assert ceiling.judge(a.call("Finish")) is None


def test_the_main_loops_calls_are_never_judged():
    write_config(THRESHOLDS)
    a = started()
    a.context(20_000 + HARD)
    call = a.call()
    call.agent_id = None
    assert ceiling.judge(call) is None


def test_past_the_ceiling_the_allowlist_lets_the_hand_off_through(tmp_path):
    notes, work = tmp_path / "notes", tmp_path / "work"
    repo = work / "build"
    for d in (notes, repo):
        d.mkdir(parents=True)
    write_config(THRESHOLDS + f'handoff_roots = ["{notes}"]\nwork_roots = ["{work}"]\n'
                 'allow_commands = [["make", "handoff"]]\n')
    a = started()
    a.context(20_000 + HARD)
    cwd = str(tmp_path)
    assert ceiling.judge(a.call("Write", {"file_path": str(notes / "r.md")}, cwd=cwd)) is None
    assert ceiling.judge(a.call("Bash", {"command": f"git -C {repo} status --short"},
                                cwd=cwd)) is None
    assert ceiling.judge(a.call("Bash", {"command": f"cd {repo} && make handoff"},
                                cwd=cwd)) is None
    got = ceiling.judge(a.call("Bash", {"command": f"git -C {repo} push"}, cwd=cwd))
    assert isinstance(got, turns.Deny) and "git push is not a wrap-up git form" in got.reason
    got = ceiling.judge(a.call("Write", {"file_path": str(tmp_path / "r.md")}, cwd=cwd))
    assert isinstance(got, turns.Deny)


def test_the_baseline_is_the_agents_first_request_read_once(monkeypatch):
    write_config(THRESHOLDS)
    a = started(50_000)
    a.context(60_000)
    reads = []
    real = ceiling._first
    monkeypatch.setattr(ceiling, "_first", lambda s, src: reads.append(1) or real(s, src))
    assert ceiling.judge(a.call()) is None
    a.context(50_000 + WARN)
    assert isinstance(ceiling.judge(a.call()), turns.Allow)
    assert reads == [1]


def test_a_codex_agents_own_rollout_is_its_source():
    write_config(THRESHOLDS)
    r = CodexRollout(Path(sessions.root("codex")), thread_id=THREAD)
    r.turn()
    r.user("go")
    r.request(token_usage(input=30_000, cached=0), response_id="resp_1")
    r.request(token_usage(input=30_000 + HARD, cached=0), response_id="resp_2")
    r.turn_end()
    r.write()
    call = SimpleNamespace(harness="codex", session_id="0190c000-parent", agent_id=THREAD,
                           tool="exec_command", input={}, cwd="/work/project",
                           record_path=str(r.path), env={})
    assert isinstance(ceiling.judge(call), turns.Deny)


def test_a_latched_codex_agent_can_still_write_its_hand_off_and_commit(tmp_path):
    """Codex's hooks name its shell tool `Bash` with the shell text in `command`, and its
    file tool `apply_patch` with the patch text in `command`."""
    notes, work = tmp_path / "notes", tmp_path / "work"
    repo = work / "build"
    for d in (notes, repo):
        d.mkdir(parents=True)
    write_config(THRESHOLDS + f'handoff_roots = ["{notes}"]\nwork_roots = ["{work}"]\n')
    r = CodexRollout(Path(sessions.root("codex")), thread_id=THREAD)
    r.turn()
    r.user("look into it")
    r.request(token_usage(input=30_000, cached=0), response_id="resp_1")
    r.request(token_usage(input=30_000 + HARD, cached=0), response_id="resp_2")
    r.write()
    parent = "0190c000-0000-7000-8000-0000000004c3"

    def call(tool, tool_input):
        return turns.ToolCall("codex", parent, THREAD, tool, "exec-1", tool_input,
                              str(tmp_path), str(r.path), {}, ())

    def patch(*lines):
        return "\n".join(("*** Begin Patch", *lines, "*** End Patch")) + "\n"

    report = notes / "hand-off.md"
    got = ceiling.judge(call("Bash", {"command": "ls"}))
    assert isinstance(got, turns.Deny) and "ls is not a wrap-up command" in got.reason
    assert ceiling.latched()[0].agent_id == THREAD
    assert ceiling.judge(call("apply_patch", {"command": patch(
        f"*** Add File: {report}", "+# Hand-off", "+", "+done: the parser")})) is None
    assert ceiling.judge(call("apply_patch", {"command": patch(
        "*** Update File: notes/hand-off.md", "@@", "-done: the parser",
        "+done: the parser and its tests")})) is None
    assert ceiling.judge(call("Bash", {"command": f"cd {repo} && git add -A"})) is None
    assert ceiling.judge(call("Bash", {"command": f"git -C {repo} commit -m 'hand off'"})) is None
    for tool, tool_input, why in [
        ("apply_patch", {"command": patch(f"*** Add File: {repo}/x.py", "+x")}, "must end in .md"),
        ("apply_patch", {"command": patch(f"*** Delete File: {report}")}, "deletes a file"),
        ("apply_patch", {"command": patch(f"*** Update File: {report}",
                                          f"*** Move to: {notes}/moved.md", "@@", "+x")},
         "moves a file"),
        ("apply_patch", {"input": patch(f"*** Add File: {report}", "+x")}, "no patch text"),
        ("Bash", {"command": f"git -C {repo} push"}, "git push is not a wrap-up git form"),
    ]:
        got = ceiling.judge(call(tool, tool_input))
        assert isinstance(got, turns.Deny) and why in got.reason, (tool_input, got)


# --- markers ----------------------------------------------------------------------------

def test_markers_live_in_a_private_directory_per_agent():
    write_config(THRESHOLDS)
    a = started()
    a.context(20_000 + WARN)
    ceiling.judge(a.call())
    where = ceiling.marker_dir(SESSION, AGENT)
    assert where == Layout("usage").state_dir() / "ceiling" / SESSION / AGENT
    assert (where / f"warn-{WARN}").is_file()
    assert os.stat(where).st_mode & 0o777 == 0o700
    assert os.stat(where.parent).st_mode & 0o777 == 0o700


def test_making_an_agents_directory_sweeps_old_ones():
    write_config(THRESHOLDS)
    old = ceiling.state_dir() / "old-session" / "old-agent"
    old.mkdir(parents=True)
    (old / "warn-1").write_text("{}")
    stale = NOW - 8 * 86400
    for p in (old / "warn-1", old):
        os.utime(p, (stale, stale))
    kept = ceiling.state_dir() / "new-session" / "new-agent"
    kept.mkdir(parents=True)
    a = started()
    a.context(20_000 + WARN)
    assert isinstance(ceiling.decide(a.call(), now=NOW), turns.Allow)
    assert not old.exists() and not old.parent.exists()
    assert kept.exists()


def test_an_id_that_cannot_name_a_directory_still_warns_once_and_latches_in_memory():
    write_config(THRESHOLDS)
    a = started()
    a.context(20_000 + WARN)
    call = a.call()
    for bad in ("../x", ".hidden", "a/b"):
        assert ceiling.marker_dir(SESSION, bad) is None
    assert ceiling.marker_dir("s/../x", AGENT) is None
    ceiling._agent("claude", SESSION, AGENT)            # held as for any agent
    monkey = ceiling.marker_dir
    try:
        ceiling.marker_dir = lambda *a, **k: None
        assert isinstance(ceiling.judge(call), turns.Allow)
        assert ceiling.judge(call) is None
        a.context(20_000 + HARD)
        assert isinstance(ceiling.judge(call), turns.Deny)
        a.context(25_000)
        assert isinstance(ceiling.judge(call), turns.Deny)
    finally:
        ceiling.marker_dir = monkey
    assert not ceiling.state_dir().exists()


def test_the_marks_verb_lists_the_agents_past_the_ceiling(capsys):
    write_config(THRESHOLDS)
    a = started()
    a.context(20_000 + HARD)
    ceiling.decide(a.call(), now=NOW)
    (found,) = ceiling.latched()
    assert (found.session_id, found.agent_id, found.growth, found.threshold, found.at) == (
        SESSION, AGENT, HARD, HARD, NOW)
    assert cli.main(["marks"]) == 0
    assert f"{SESSION} agent {AGENT}: past the worker ceiling" in capsys.readouterr().out
    assert cli.main(["marks", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["ceiling"][0]["growth"] == HARD


def test_the_doctor_sweep_uses_the_ceilings_own():
    write_config(THRESHOLDS)
    old = ceiling.state_dir() / "s1" / "a1"
    old.mkdir(parents=True)
    stale = NOW - 8 * 86400
    os.utime(old, (stale, stale))
    row = doctor.marks_row(now=NOW)
    assert row.state == "ok" and "the ceiling markers of 1 agent" in row.detail
    assert not old.parent.exists()


# --- config and failing open ------------------------------------------------------------

@pytest.mark.parametrize("text, key", [
    ('[ceiling]\nhandoff_roots = ["notes"]\n', "ceiling.handoff_roots"),
    ('[ceiling]\nwork_roots = ["/ok", "rel/dir"]\n', "ceiling.work_roots"),
    ('[ceiling]\nallow_commands = ["make handoff"]\n', "ceiling.allow_commands"),
    ('[ceiling]\nallow_commands = [[]]\n', "ceiling.allow_commands"),
    ('[ceiling]\nallow_commands = [["make", ""]]\n', "ceiling.allow_commands"),
])
def test_the_config_check_refuses_a_relative_root_or_a_bad_command(text, key):
    with pytest.raises(ConfigError) as caught:
        settings.check(text)
    assert key in str(caught.value)


def test_the_config_check_takes_roots_from_home_and_word_lists():
    settings.check('[ceiling]\nhandoff_roots = ["~/notes"]\nwork_roots = ["/tmp/w"]\n'
                   'allow_commands = [["make", "handoff"]]\n')
    write_config('[ceiling]\nhandoff_roots = ["~/notes"]\n')
    assert ceiling.rules().handoff_roots == (os.path.expanduser("~/notes"),)


def test_a_config_that_cannot_be_read_allows_every_call(caplog):
    write_config(THRESHOLDS + "on = \"maybe\"\n")
    a = started()
    a.context(20_000 + HARD)
    with caplog.at_level(logging.WARNING, logger="moxi.usage"):
        assert ceiling.judge(a.call()) is None
    assert "the worker ceiling allows every call" in caplog.text


def test_every_error_fails_open(monkeypatch, caplog):
    write_config(THRESHOLDS)
    a = started()
    a.context(20_000 + HARD)
    monkeypatch.setattr(ceiling, "_measure", lambda *a: 1 / 0)
    with caplog.at_level(logging.WARNING, logger="moxi.usage"):
        assert ceiling.judge(a.call()) is None
    assert "it was allowed" in caplog.text


def test_an_agent_with_no_request_yet_is_allowed():
    write_config(THRESHOLDS)
    a = Agent()
    a.t.write()
    assert ceiling.judge(a.call()) is None
    assert ceiling.judge(a.call(agent_id="a-not-listed")) is None


def test_the_texts_fill_their_counts_and_survive_stray_braces():
    assert ceiling.fill("{growth}/{current}/{hard}", growth=1200, current=None,
                        hard=300000) == "1,200/unknown/300,000"
    assert ceiling.fill("a {b} {", growth=1, current=2, hard=3) == "a {b} {"


def test_the_default_texts_name_no_tool_or_path():
    schema = settings.schema()
    for key in ("ceiling.warning_text", "ceiling.deny_text"):
        text = settings.current()[key]
        assert "/" not in text and "~" not in text and "git" not in text
    assert settings.current()["ceiling.handoff_roots"] == []
    assert settings.current()["ceiling.work_roots"] == []
    assert settings.current()["ceiling.allow_commands"] == []
    assert schema is not None


def test_importing_the_judge_costs_next_to_nothing():
    import subprocess
    import sys

    code = ("import sys, __future__; before = set(sys.modules); import moxi_usage.ceiling; "
            "print(' '.join(sorted(set(sys.modules) - before)))")
    out = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True,
                         text=True, check=True).stdout.split()
    assert sorted(out) == ["moxi_usage", "moxi_usage.ceiling"]


@pytest.mark.timed
def test_the_judge_below_the_warning_band_takes_under_two_milliseconds():
    write_config(THRESHOLDS)
    a = started()
    a.context(30_000)
    call = a.call()
    assert ceiling.judge(call) is None
    t0 = time.perf_counter()
    for _ in range(50):
        assert ceiling.judge(call) is None
    per_call = (time.perf_counter() - t0) / 50 * 1000
    assert per_call < 2.0, f"{per_call:.3f} ms per call"

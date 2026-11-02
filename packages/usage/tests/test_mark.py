"""The warn-at mark: turns' after-tool listener, its floor, crossings, the fired marks on
disk, `api.mark` and `moxi usage marks`. Events are plain objects shaped as turns' own
`SessionEvent`; transcripts are sessions' builders; the measure and the model rows are
written into the test's tmp, so each mark's threshold is known exactly."""

import json
import logging
import os
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from moxi_sessions import api as sessions
from moxi_usage import api, cli, mark, marks, thresholds

from conftest import write_config

ClaudeTranscript = sessions.testing.ClaudeTranscript
usage = sessions.testing.usage

SESSION = "0190b000-0000-7000-8000-0000000004b1"
AGENT = "a0190b00000004b1"
MODEL = "claude-opus-5-5"
OTHER = "claude-fable-5-1"
#: The two models' rows; the subagent row of MODEL is its main row's numbers.
PARAMS = {
    "schema": 1, "measured_at": "2026-10-26T00:00:00+00:00", "w": 2.0,
    "models": {
        MODEL: {"main": {"n": 50, "S": 47058, "P": 23171, "g": 1476},
                "subagent": {"n": 50, "S": 47058, "P": 23171, "g": 1476,
                             "fallback": "main"}},
        OTHER: {"main": {"n": 147, "S": 51053, "P": 23116, "g": 2539}},
    },
}
#: The thresholds those rows give with r 0.05 and 0.025.
ONE_X, ONE_HALF_X = 100_808, 187_776
OTHER_ONE_X = 158_135
CONFIG = f"""
[[model]]
id = "{MODEL}"
input = 10.0
output = 50.0
cache_read = 0.5

[[model]]
id = "{OTHER}"
input = 10.0
output = 50.0
cache_read = 0.25
"""
NOW = 1793400000.0
#: The real reader of the crossing group, which the fixture stands in for.
HANDLERS = mark.handlers


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    mark.reset()
    write_config(CONFIG)
    path = thresholds.params_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(PARAMS), encoding="utf-8")
    monkeypatch.setattr(mark, "handlers", lambda: [])
    yield
    mark.reset()


class Session:
    """A Claude session whose context the test sets call by call."""

    def __init__(self, session_id=SESSION):
        self.t = ClaudeTranscript(Path(sessions.root("claude")), session_id=session_id)
        self.agents = {}

    def context(self, tokens, *, model=MODEL):
        self.t.user("go")
        self.t.assistant("ok", usage=usage(input=tokens - 1000, cache_read=1000,
                                           cache_write=0), model=model)
        self.t.write()

    def agent_context(self, agent_id, tokens):
        child = self.agents.get(agent_id) or self.t.subagent(agent_id)
        self.agents[agent_id] = child
        child.user("look")
        child.assistant("found", usage=usage(input=tokens - 10, cache_read=10,
                                             cache_write=0), model=MODEL)
        self.t.write()

    def event(self, *, agent_id=None, env=None, session_id=None):
        return SimpleNamespace(
            event="after-tool", harness="claude", session_id=session_id or self.t.session_id,
            env={"MOXI_USAGE_WARN_AT": "1x"} if env is None else env, agent_id=agent_id,
            tool="Bash", tool_use_id="toolu_1", is_error=False, point="after",
            record_path=str(self.t.path), cwd="/work/project")


def notice(tokens, mark_name="1x", threshold=ONE_X, model=MODEL):
    return mark.notice_line(tokens, mark_name, threshold, model)


# --- selection --------------------------------------------------------------------------

def test_the_notice_is_the_launchs_stamp_else_the_config_default():
    assert mark.selected({"MOXI_USAGE_WARN_AT": "1.5x"}) == ("1.5x", None)
    assert mark.selected({}) == (None, None)
    write_config(CONFIG + '\n[marks]\ndefault = "2x"\n')
    assert mark.selected({}) == ("2x", None)
    assert mark.selected({"MOXI_USAGE_WARN_AT": "off"}) == (None, None)
    assert mark.selected({"MOXI_USAGE_WARN_AT": "3x"}) == (None, None)


def test_the_crossing_is_its_own_stamp_with_no_config_default():
    write_config(CONFIG + '\n[marks]\ndefault = "2x"\n')
    assert mark.selected({"MOXI_USAGE_CROSSING_AT": "1x", "MOXI_USAGE_WARN_AT": "off"}) == (
        None, "1x")
    assert mark.selected({"MOXI_USAGE_CROSSING_AT": "soon"}) == ("2x", None)


def test_with_both_off_the_listener_reads_nothing_and_holds_no_state():
    s = Session()
    s.context(400_000)
    assert mark.on_tool(s.event(env={"MOXI_USAGE_WARN_AT": "off"})) is None
    assert mark.on_tool(s.event(env={})) is None
    assert mark._sessions == {}


def test_off_imports_nothing_beyond_the_mark_module():
    """`__future__` is every module's first line, and costs nothing."""
    code = ("import sys; from types import SimpleNamespace as N; import __future__; "
            "before = set(sys.modules); import moxi_usage.mark as m; "
            "e = N(event='after-tool', harness='claude', session_id='s', agent_id=None, "
            "env={'MOXI_USAGE_WARN_AT': 'off'}, record_path='/nowhere'); "
            "assert m.on_tool(e) is None and m.floor(e) == m.NEVER; "
            "print(' '.join(sorted(set(sys.modules) - before)))")
    out = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True,
                         text=True, check=True).stdout.split()
    assert out == ["moxi_usage", "moxi_usage.mark"]


# --- firing -----------------------------------------------------------------------------

def test_a_first_observation_past_the_mark_fires_once():
    s = Session()
    s.context(120_000)
    assert mark.on_tool(s.event()) == notice(120_000)
    assert notice(120_000) == (f"context 120,000 passed the 1x restart point (100,808) "
                               f"for {MODEL}")
    s.context(130_000)
    assert mark.on_tool(s.event()) is None


def test_below_the_mark_says_nothing_until_it_is_passed():
    s = Session()
    s.context(50_000)
    assert mark.on_tool(s.event()) is None
    s.context(ONE_X - 1)
    assert mark.on_tool(s.event()) is None
    s.context(ONE_X)
    assert mark.on_tool(s.event()) == notice(ONE_X)


def test_marks_crossing_a_compaction_and_growth_past_it_again_give_a_second_of_each(
        monkeypatch):
    got = []
    monkeypatch.setattr(mark, "handlers", lambda: [
        SimpleNamespace(name="launcher", load=lambda: lambda c: got.append(c) or "recycle")])
    s = Session()
    env = {"MOXI_USAGE_WARN_AT": "1x", "MOXI_USAGE_CROSSING_AT": "1x"}
    s.context(110_000)
    assert mark.on_tool(s.event(env=env)) == f"{notice(110_000)}\nrecycle"
    s.t.compaction(pre_tokens=110_000)
    s.context(30_000)                          # below the mark: both are armed again
    assert mark.on_tool(s.event(env=env)) is None
    s.context(60_000)
    assert mark.on_tool(s.event(env=env)) is None
    s.context(105_000)
    assert mark.on_tool(s.event(env=env)) == f"{notice(105_000)}\nrecycle"
    assert len(got) == 2
    assert [(c.mark, c.tokens, c.threshold, c.model, c.session_id) for c in got] == [
        ("1x", 110_000, ONE_X, MODEL, SESSION), ("1x", 105_000, ONE_X, MODEL, SESSION)]
    assert isinstance(got[0].at, float)


def test_a_launch_with_only_the_crossing_stamp_gets_the_handlers_line_and_no_notice(
        monkeypatch):
    got = []
    monkeypatch.setattr(mark, "handlers", lambda: [
        SimpleNamespace(name="launcher", load=lambda: lambda c: got.append(c) or "relaunch")])
    s = Session()
    s.context(200_000)
    env = {"MOXI_USAGE_CROSSING_AT": "1.5x"}
    assert mark.on_tool(s.event(env=env)) == "relaunch"
    assert got == [mark.Crossing("claude", SESSION, "1.5x", 200_000, ONE_HALF_X, MODEL,
                                 got[0].at)]


def test_the_notice_and_the_crossing_each_fire_at_their_own_mark(monkeypatch):
    monkeypatch.setattr(mark, "handlers", lambda: [
        SimpleNamespace(name="h", load=lambda: lambda c: f"crossed {c.mark}")])
    s = Session()
    env = {"MOXI_USAGE_WARN_AT": "1x", "MOXI_USAGE_CROSSING_AT": "1.5x"}
    s.context(150_000)
    assert mark.on_tool(s.event(env=env)) == notice(150_000)
    s.context(190_000)
    assert mark.on_tool(s.event(env=env)) == "crossed 1.5x"


def test_a_model_with_no_row_fires_nothing():
    s = Session()
    s.context(900_000, model="claude-unmeasured-1")
    assert mark.on_tool(s.event()) is None


# --- agents -----------------------------------------------------------------------------

def test_an_agent_past_its_mark_while_the_parent_is_below_the_floor_is_warned_and_its_line_drains():
    s = Session()
    s.context(20_000)
    assert mark.on_tool(s.event()) is None
    assert mark.floor(s.event()) == ONE_X
    s.agent_context(AGENT, 101_000)
    agent_event = s.event(agent_id=AGENT)
    own = notice(101_000)
    assert mark.on_tool(agent_event) == own
    assert mark.floor(agent_event) == 0         # the parent's next call must come
    assert mark.floor(s.event()) == 0           # a line waits
    assert mark.on_tool(s.event()) == f"subagent agent-a0190b00... {own}"
    assert mark.on_tool(s.event()) is None      # drained once
    assert mark.floor(s.event()) == ONE_X


def test_an_agent_has_no_crossing(monkeypatch):
    got = []
    monkeypatch.setattr(mark, "handlers", lambda: [
        SimpleNamespace(name="h", load=lambda: lambda c: got.append(c))])
    s = Session()
    s.context(10_000)
    s.agent_context(AGENT, 300_000)
    assert mark.on_tool(s.event(agent_id=AGENT, env={"MOXI_USAGE_CROSSING_AT": "1x"})) is None
    assert got == []


def test_each_agent_is_measured_against_its_own_context():
    s = Session()
    s.context(300_000)
    assert mark.on_tool(s.event()) == notice(300_000)
    s.agent_context(AGENT, 5_000)
    assert mark.on_tool(s.event(agent_id=AGENT)) is None


# --- Codex and OpenCode -----------------------------------------------------------------
#
# turns' Codex host hands the main session's events the main rollout and a subagent's
# events the subagent's own rollout as `record_path`, with the child's thread id as
# `agent_id`; its OpenCode host hands no `record_path`, and a child session's id as
# `agent_id`. The model rows are the Claude ones above, named by every harness alike.

BOTH = {"MOXI_USAGE_WARN_AT": "1x", "MOXI_USAGE_CROSSING_AT": "1x"}
THREAD = "0190c000-0000-7000-8000-0000000004d1"
CHILD = "0190c000-0000-7000-8000-0000000004d2"


def crossing_handler(monkeypatch):
    got = []
    monkeypatch.setattr(mark, "handlers", lambda: [
        SimpleNamespace(name="launcher", load=lambda: lambda c: got.append(c) or "recycle")])
    return got


def event(harness, session_id, *, agent_id=None, record_path=None, env=BOTH):
    return SimpleNamespace(
        event="after-tool", harness=harness, session_id=session_id, env=env,
        agent_id=agent_id, tool="Bash", tool_use_id="exec-1", is_error=False,
        point="after", record_path=record_path, cwd="/work/project")


class Codex:
    """A Codex thread, and a subagent it spawned, whose contexts the test sets."""

    def __init__(self):
        self.r = sessions.testing.CodexRollout(Path(sessions.root("codex")), thread_id=THREAD)
        self.r.turn(MODEL)
        self.child = None

    def context(self, tokens):
        self.r.user("go")
        self.r.request(sessions.testing.token_usage(input=tokens, cached=tokens // 2))
        self.r.write()

    def agent_context(self, tokens):
        if self.child is None:
            self.child = self.r.spawn(CHILD, "explore")
            self.child.turn(MODEL)
        self.child.user("look")
        self.child.request(sessions.testing.token_usage(input=tokens, cached=0))
        self.r.write()
        self.child.write()

    def event(self, **kw):
        return event("codex", THREAD, record_path=str(self.r.path), **kw)

    def agent_event(self, **kw):
        return event("codex", THREAD, agent_id=CHILD, record_path=str(self.child.path), **kw)


class OpenCode:
    """An OpenCode session, and a child session a task made, whose contexts the test
    sets."""

    def __init__(self):
        root = Path(sessions.root("opencode"))
        self.db = sessions.testing.OpenCodeDb(root.parent, name=root.name)
        self.s = self.db.session(model=("provider", MODEL))
        self.kid = None

    @staticmethod
    def _step(session, tokens):
        session.user("go", model=("provider", MODEL))
        reply = session.assistant(model=("provider", MODEL))
        reply.step(input=tokens - 1000, cache_read=1000, output=10)
        reply.complete()

    def context(self, tokens):
        self._step(self.s, tokens)
        self.db.write()

    def agent_context(self, tokens):
        if self.kid is None:
            self.kid = self.s.child(title="look")
        self._step(self.kid, tokens)
        self.db.write()

    def event(self, **kw):
        return event("opencode", self.s.id, **kw)

    def agent_event(self, **kw):
        return event("opencode", self.s.id, agent_id=self.kid.id, **kw)


@pytest.mark.parametrize("make", [Codex, OpenCode], ids=["codex", "opencode"])
def test_a_main_session_gets_the_notice_and_the_crossing_once_per_rise(monkeypatch, make):
    got = crossing_handler(monkeypatch)
    h = make()
    h.context(50_000)
    assert mark.on_tool(h.event()) is None
    assert mark.floor(h.event()) == ONE_X
    h.context(110_000)
    assert mark.on_tool(h.event()) == f"{notice(110_000)}\nrecycle"
    assert mark.floor(h.event()) == 0
    h.context(115_000)
    assert mark.on_tool(h.event()) is None
    h.context(30_000)                            # compacted: both are armed again
    assert mark.on_tool(h.event()) is None
    h.context(105_000)
    assert mark.on_tool(h.event()) == f"{notice(105_000)}\nrecycle"
    harness, session_id = h.event().harness, h.event().session_id
    assert [(c.harness, c.session_id, c.tokens, c.threshold, c.model) for c in got] == [
        (harness, session_id, 110_000, ONE_X, MODEL),
        (harness, session_id, 105_000, ONE_X, MODEL)]
    assert mark.state_file(harness, session_id).is_file()


@pytest.mark.parametrize("make", [Codex, OpenCode], ids=["codex", "opencode"])
def test_a_subagent_is_warned_on_its_own_context_and_its_line_drains(monkeypatch, make):
    got = crossing_handler(monkeypatch)
    h = make()
    h.context(20_000)
    assert mark.on_tool(h.event()) is None
    h.agent_context(101_000)
    own = notice(101_000)
    assert mark.on_tool(h.agent_event()) == own
    assert got == []                             # an agent has no crossing
    assert mark.floor(h.agent_event()) == 0
    assert mark.floor(h.event()) == 0            # a line waits
    label = mark.agent_label(h.agent_event().agent_id)
    assert mark.on_tool(h.event()) == f"subagent {label} {own}"
    assert mark.on_tool(h.event()) is None
    h.agent_context(102_000)
    assert mark.on_tool(h.agent_event()) is None  # fired once per rise


def test_a_codex_subagents_source_is_its_own_rollout():
    h = Codex()
    h.context(20_000)
    h.agent_context(150_000)
    mark.on_tool(h.agent_event())
    reader = mark._sessions[("codex", THREAD)].readers[CHILD]
    assert reader.source == sessions.Source("codex", str(h.child.path), CHILD, CHILD)


def test_a_codex_event_with_no_record_path_finds_its_rollouts_by_id():
    h = Codex()
    h.context(20_000)
    h.agent_context(120_000)
    assert mark.on_tool(event("codex", THREAD, agent_id=CHILD)) == notice(120_000)
    h.context(130_000)
    assert mark.on_tool(event("codex", THREAD, env={"MOXI_USAGE_WARN_AT": "1x"})) == (
        f"{notice(130_000)}\nsubagent {mark.agent_label(CHILD)} {notice(120_000)}")


def test_an_open_opencode_step_neither_fires_nor_re_arms():
    """A step still running has no tokens yet; sessions passes over it, so a tool call
    inside it reads the last finished step."""
    one = {"MOXI_USAGE_WARN_AT": "1x"}
    h = OpenCode()
    h.context(120_000)
    assert mark.on_tool(h.event(env=one)) == notice(120_000)
    h.s.user("go", model=("provider", MODEL))
    reply = h.s.assistant(model=("provider", MODEL))
    reply.step_start()
    reply.tool("bash", {"command": "ls"})
    h.db.write()
    assert mark.on_tool(h.event(env=one)) is None
    reply.step_finish(input=125_000)
    h.db.write()
    assert mark.on_tool(h.event(env=one)) is None


# --- the floor --------------------------------------------------------------------------

def test_the_floor_before_any_reading_is_the_smallest_over_every_row():
    s = Session()
    assert mark.floor(s.event()) == ONE_X
    assert mark.floor(s.event(env={"MOXI_USAGE_WARN_AT": "2x",
                                   "MOXI_USAGE_CROSSING_AT": "1.5x"})) == ONE_HALF_X


def test_the_floor_follows_the_models_the_session_used():
    s = Session()
    s.context(10_000, model=OTHER)
    mark.on_tool(s.event())
    assert mark.floor(s.event()) == OTHER_ONE_X
    s.context(20_000)                            # a model switch
    mark.on_tool(s.event())
    assert mark.floor(s.event()) == ONE_X


def test_the_floor_is_zero_while_a_main_purpose_is_fired_and_never_with_both_off():
    s = Session()
    s.context(150_000)
    mark.on_tool(s.event())
    assert mark.floor(s.event()) == 0
    assert mark.floor(s.event(env={"MOXI_USAGE_WARN_AT": "off"})) == mark.NEVER


def test_the_floor_is_zero_with_no_measure_and_follows_a_new_one():
    thresholds.params_path().unlink()
    s = Session()
    assert mark.floor(s.event()) == 0
    thresholds.params_path().write_text(json.dumps(PARAMS), encoding="utf-8")
    assert mark.floor(s.event()) == ONE_X


def test_the_floor_and_the_listener_take_turns_own_event():
    import dataclasses

    from moxi import modules

    SessionEvent = modules.load("turns").api.SessionEvent
    s = Session()
    ready = SessionEvent(event="ready", harness="claude", session_id=SESSION,
                         env={"MOXI_USAGE_WARN_AT": "1x"})
    assert mark.floor(dataclasses.replace(ready, env=dict(ready.env))) == ONE_X
    s.context(120_000)
    tool = SessionEvent(event="after-tool", harness="claude", session_id=SESSION,
                        env={"MOXI_USAGE_WARN_AT": "1x"}, tool="Bash", point="after",
                        record_path=str(s.t.path))
    assert mark.on_tool(tool) == notice(120_000)


# --- crossing handlers ------------------------------------------------------------------

def crossing():
    return mark.Crossing("claude", SESSION, "1x", 120_000, ONE_X, MODEL, NOW)


def test_a_handler_that_raises_costs_only_its_own_line(monkeypatch, caplog):
    def broken(c):
        raise RuntimeError("no")

    monkeypatch.setattr(mark, "handlers", lambda: [
        SimpleNamespace(name="a", load=lambda: broken),
        SimpleNamespace(name="b", load=lambda: lambda c: "line b"),
        SimpleNamespace(name="c", load=lambda: lambda c: 7)])
    with caplog.at_level(logging.WARNING, logger="moxi.usage"):
        assert mark.run_handlers(crossing(), wait=5.0) == ["line b"]
    assert "crossing handler a raised RuntimeError" in caplog.text
    assert "crossing handler c answered a int" in caplog.text


def test_a_handler_past_the_wait_costs_only_its_line(monkeypatch, caplog):
    release = threading.Event()
    monkeypatch.setattr(mark, "handlers", lambda: [
        SimpleNamespace(name="slow", load=lambda: lambda c: release.wait(5) and "late")])
    try:
        with caplog.at_level(logging.WARNING, logger="moxi.usage"):
            assert mark.run_handlers(crossing(), wait=0.0) == []
    finally:
        release.set()
    assert "crossing handler slow did not answer within 0 ms" in caplog.text


@pytest.mark.slow
def test_a_handler_that_overruns_marks_handler_wait_ms_costs_only_its_line(monkeypatch):
    release = threading.Event()
    write_config(CONFIG + "\n[marks]\nhandler_wait_ms = 50\n")
    monkeypatch.setattr(mark, "handlers", lambda: [
        SimpleNamespace(name="slow", load=lambda: lambda c: release.wait(5) and "late"),
        SimpleNamespace(name="quick", load=lambda: lambda c: "quick")])
    s = Session()
    s.context(110_000)
    try:
        got = mark.on_tool(s.event(env={"MOXI_USAGE_CROSSING_AT": "1x"}))
    finally:
        release.set()
    assert got == "quick"


def test_the_crossing_group_is_read_through_the_cores_extensions(monkeypatch):
    import moxi.modules

    monkeypatch.setattr(mark, "handlers", HANDLERS)
    asked = []
    monkeypatch.setattr(moxi.modules, "extensions",
                        lambda group: asked.append(group) or [])
    assert mark.run_handlers(crossing(), wait=0.0) == []
    assert asked == ["moxi.usage.crossings"]


# --- the fired marks on disk ------------------------------------------------------------

def test_a_restarted_endpoint_does_not_fire_twice_and_gone_deletes_the_file():
    s = Session()
    s.context(120_000)
    assert mark.on_tool(s.event()) == notice(120_000)
    path = mark.state_file("claude", SESSION)
    doc = json.loads(path.read_text())
    assert doc["readers"]["main"]["notice"]["mark"] == "1x"
    assert doc["marks"] == {"notice": "1x", "crossing": "off"}
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    mark.reset()                                  # a new endpoint process
    s.context(125_000)
    assert mark.on_tool(s.event()) is None
    mark.gone(SimpleNamespace(event="gone", harness="claude", session_id=SESSION, env={}))
    assert not path.exists()
    assert mark._sessions == {}


def test_a_re_arm_is_written_too():
    s = Session()
    s.context(120_000)
    mark.on_tool(s.event())
    s.context(10_000)
    mark.on_tool(s.event())
    doc = json.loads(mark.state_file("claude", SESSION).read_text())
    assert doc["readers"] == {}


def test_a_fired_mark_of_another_mark_does_not_hold_this_one():
    s = Session()
    s.context(120_000)
    mark.on_tool(s.event())
    mark.reset()
    s.context(200_000)
    assert mark.on_tool(s.event(env={"MOXI_USAGE_WARN_AT": "1.5x"})) == notice(
        200_000, "1.5x", ONE_HALF_X)


# --- fail open --------------------------------------------------------------------------

def test_every_error_is_logged_once_and_answers_none(monkeypatch, caplog):
    def broken(source, **kw):
        raise OSError("disk gone")

    monkeypatch.setattr(sessions, "last_request", broken)
    s = Session()
    s.context(120_000)
    with caplog.at_level(logging.WARNING, logger="moxi.usage"):
        assert mark.on_tool(s.event()) is None
        assert mark.on_tool(s.event()) is None
    assert caplog.text.count("could not be read for the warn-at mark") == 1


def test_a_broken_event_answers_none():
    assert mark.on_tool(object()) is None
    assert mark.floor(SimpleNamespace(env={"MOXI_USAGE_WARN_AT": "1x"})) == 0


# --- api.mark and the verb --------------------------------------------------------------

def test_api_mark_reads_the_fired_purposes_and_the_marks_recorded():
    s = Session()
    s.context(120_000)
    env = {"MOXI_USAGE_WARN_AT": "1x", "MOXI_USAGE_CROSSING_AT": "1.5x"}
    mark.on_tool(s.event(env=env))
    s.agent_context(AGENT, 150_000)
    mark.on_tool(s.event(agent_id=AGENT, env=env))
    found = api.mark(SESSION, live=False)
    assert isinstance(found, api.MarkState)
    assert (found.harness, found.tokens, found.model, found.marks_from) == (
        "claude", 120_000, MODEL, "record")
    note, cross = found.purpose("notice"), found.purpose("crossing")
    assert (note.mark, note.threshold, note.state) == ("1x", ONE_X, "fired")
    assert note.fired_at is not None
    assert (cross.mark, cross.threshold, cross.state, cross.fired_at) == (
        "1.5x", ONE_HALF_X, "armed", None)
    assert set(found.agents) == {AGENT} and set(found.agents[AGENT]) == {"notice"}
    doc = found.as_dict()
    assert doc["purposes"][0]["state"] == "fired" and "unknown" not in doc


def test_api_mark_with_nothing_on_disk_reads_the_config_default():
    s = Session()
    s.context(50_000)
    found = api.mark(SESSION, live=False)
    assert found.marks_from == "config"
    assert [p.state for p in found.purposes] == ["off", "off"]


def test_api_mark_reads_a_live_sessions_own_stamps_through_turns(monkeypatch):
    s = Session()
    s.context(50_000)
    monkeypatch.setattr(marks, "_live_marks", lambda sid: {
        "MOXI_USAGE_WARN_AT": "2x", "MOXI_USAGE_CROSSING_AT": None})
    found = api.mark(SESSION)
    assert found.marks_from == "session"
    assert found.purpose("notice").mark == "2x" and found.purpose("notice").state == "armed"


def test_the_marks_verb_prints_one_session_and_every_fired_file(capsys):
    s = Session()
    s.context(120_000)
    mark.on_tool(s.event())
    assert cli.main(["marks", SESSION]) == 0
    out = capsys.readouterr().out
    assert "context 120,000" in out and "notice: 1x at 100,808, fired" in out
    assert cli.main(["marks"]) == 0
    assert f"claude {SESSION}: notice 1x fired" in capsys.readouterr().out
    assert cli.main(["marks", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["fired"][0]["session_id"] == SESSION
    assert cli.main(["marks", "no-such-session"]) == 1


# --- the sweep --------------------------------------------------------------------------

def test_the_sweep_deletes_old_fired_files_and_old_ceiling_markers():
    from moxi.paths import Layout

    s = Session()
    s.context(120_000)
    mark.on_tool(s.event())
    old = mark.state_dir() / "claude-old.json"
    old.write_text('{"schema": 1}')
    os.utime(old, (NOW - 8 * 86400, NOW - 8 * 86400))
    ceiling = Layout("usage").state_dir() / "ceiling"
    stale, live = ceiling / "s1" / "a1", ceiling / "s2" / "a2"
    for where, age in ((stale, 9), (live, 1)):
        where.mkdir(parents=True)
        (where / "warn").write_text("")
        for p in (where / "warn", where):
            os.utime(p, (NOW - age * 86400, NOW - age * 86400))
    os.utime(mark.state_file("claude", SESSION), (NOW, NOW))
    swept = marks.sweep(now=NOW, marks_days=7, ceiling_days=7)
    assert (swept.marks, swept.ceiling, swept.kept, swept.problems) == (1, 1, 1, ())
    assert not old.exists() and not (ceiling / "s1").exists() and live.exists()

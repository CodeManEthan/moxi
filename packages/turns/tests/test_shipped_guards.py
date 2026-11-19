"""turns' three shipped guards: their declarations, the removal judge over the shell hook's
cases and its filter, no-nested-agents as data on both harnesses, question mode's judge and
reminder, and each through the Claude endpoint's guard runner with the real loader."""

from __future__ import annotations

import os
import random
from types import SimpleNamespace

import pytest

import moxi_turns.guards as guards
import removal_cases as rc
from claude_runner import FakeRunner, pipe_pair
from conftest import write_config
from moxi_turns import claude_leg as leg
from moxi_turns import config, listeners, protocol
from moxi_turns.claude_endpoint import ClaudeHarness
from moxi_turns.endpoint import Endpoint
from moxi_turns.guards import (GUARDS, Deny, Found, Guard, Prompt, ToolCall, compile_match,
                               judge_of, regex_problem)
from moxi_turns.guards import question as qm
from moxi_turns.guards import removal
from moxi_turns.guards.run import GuardRunner, break_deny, decide, entry_for
from moxi_turns.results import SessionEvent

BY_NAME = {g.name: g for g in GUARDS}
REMOVAL = BY_NAME["removal"]
FILTER = compile_match(REMOVAL)["command"]
LETTERS = {text: letter for letter, text in removal.REASONS.items()}


def found(g: Guard) -> Found:
    return Found(f"turns.{g.name}", g, "module", module="turns")


# -- the declarations --------------------------------------------------------------------

def test_turns_declares_its_three_guards_each_sound_and_off_by_a_switch():
    assert [g.name for g in GUARDS] == ["removal", "no-nested-agents", "question-mode"]
    schema = config.schema()
    from moxi.units import switch_problem

    for g in GUARDS:
        assert g.problems() == [], g.name
        assert g.switch in guards_switches()
        assert switch_problem(schema, g.switch) is None
        assert schema.keys[g.switch].default is False
        assert g.doc and "\n" not in g.doc


def guards_switches() -> set[str]:
    from moxi_turns import module

    return set(module.QUESTIONS)


def test_the_declarations_match_the_design():
    assert REMOVAL.calls == {"claude": ("Bash",)} and REMOVAL.who == "any"
    assert REMOVAL.match == {"command": guards.REMOVAL_MATCH} and REMOVAL.match_flags == "i"
    assert regex_problem(guards.REMOVAL_MATCH) is None
    assert (REMOVAL.judge, REMOVAL.on_error, REMOVAL.switch) == (
        "moxi_turns.guards.removal:judge", "allow", "guards.removal")
    nested = BY_NAME["no-nested-agents"]
    assert nested.calls == {"claude": ("Agent", "Task"), "codex": ("spawn_agent",)}
    assert (nested.who, nested.judge, nested.switch) == ("agents", None,
                                                         "guards.no_nested_agents")
    assert nested.deny == guards.NO_NESTED_AGENTS_DENY
    q = BY_NAME["question-mode"]
    assert (q.calls, q.who, q.wants, q.on_error, q.switch) == (
        {"claude": ("AskUserQuestion",)}, "any", ("prompts",), "deny", "guards.question_mode")
    assert q.judge == "moxi_turns.guards.question:judge"
    assert guards.LISTENERS == ((("prompt",), "moxi_turns.guards.question:on_prompt"),
                                (("start",), "moxi_turns.startline:answer"))


def test_each_judge_target_imports():
    for g in GUARDS:
        if g.judge:
            assert callable(judge_of(found(g)))


def test_the_entry_points_name_this_module_for_guards_and_listeners():
    from importlib.metadata import entry_points

    assert [e.value for e in entry_points(group=guards.GROUP) if e.name == "turns"] == \
        ["moxi_turns.guards"]
    assert [e.value for e in entry_points(group=listeners.GROUP) if e.name == "turns"] == \
        ["moxi_turns.guards"]


def test_the_real_loader_finds_them_off_and_the_switch_or_list_turns_each_on(turns_env):
    env = dict(os.environ)
    loaded = guards.load(env=env)
    assert {f.id for f in loaded.off} >= {"turns.removal", "turns.no-nested-agents",
                                          "turns.question-mode"}
    assert not loaded.on and not [p for p in loaded.problems if p.id.startswith("turns")]
    write_config(turns_env, '[guards]\nrun = ["turns.no-nested-agents"]\nremoval = true\n')
    loaded = guards.load(env=env)
    assert {(o.id, o.by) for o in loaded.on} == {("turns.removal", ("switch",)),
                                                 ("turns.no-nested-agents", ("list",))}


def test_the_listener_rows_load_from_the_group():
    reg = listeners.load(entries=[SimpleNamespace(name="turns", load=lambda: guards)],
                         order=())
    assert [(ls.module, ls.events, ls.target) for ls in reg.listeners] == [
        ("turns", frozenset({"prompt"}), "moxi_turns.guards.question:on_prompt"),
        ("turns", frozenset({"start"}), "moxi_turns.startline:answer")]
    assert not reg.broken


# -- the session id start line ---------------------------------------------------------------

@pytest.mark.parametrize("source", ["startup", "resume", "clear", "compact"])
def test_the_start_line_gives_the_sessions_id_at_every_source(source):
    reg = listeners.load(entries=[SimpleNamespace(name="turns", load=lambda: guards)],
                         order=("turns",))
    got = listeners.ask(reg, SessionEvent("ready", "claude", "s-9", source=source), 2.0)
    assert got == (f"This session's id is s-9 (as of this {source}). It replaces any "
                   "session id given at launch, which a clear makes stale.")


def test_the_start_line_says_nothing_without_an_id_or_off_the_list():
    on = listeners.load(entries=[SimpleNamespace(name="turns", load=lambda: guards)],
                        order=("turns",))
    assert listeners.ask(on, SessionEvent("ready", "claude", None, source="clear"), 2.0) \
        is None
    off = listeners.load(entries=[SimpleNamespace(name="turns", load=lambda: guards)],
                         order=None, limit=0)
    assert "turns" not in off.start_order()
    assert listeners.ask(off, SessionEvent("ready", "claude", "s-9", source="clear"),
                         2.0) is None


# -- the removal judge -------------------------------------------------------------------

def call_for(command, *, agent=None) -> ToolCall:
    return ToolCall("claude", "s-1", agent, "Bash", "tu-1", {"command": command}, rc.CWD,
                    None, {"HOME": rc.HOME})


def verdict(answer) -> str:
    if answer is None:
        return "pass"
    assert isinstance(answer, Deny)
    return LETTERS[answer.reason]


def test_the_case_labels_are_unique():
    labels = [c[0] for c in rc.CASES]
    assert len(labels) == len(set(labels)) and len(labels) > 250


@pytest.mark.parametrize("label,command,expect", rc.CASES, ids=[c[0] for c in rc.CASES])
def test_a_removal_case(label, command, expect):
    assert verdict(removal.judge(call_for(command))) == expect
    if expect != "pass":
        assert FILTER.search(command), "the filter must pass every call the judge refuses"


def test_a_subagents_call_is_judged_the_same():
    assert verdict(removal.judge(call_for("cd t && rm -rf *", agent="a-1"))) == "T"


@pytest.mark.parametrize("given", [{}, {"command": ["cd t && rm -rf *"]}, {"command": None},
                                   {"cmd": "cd t && rm -rf *"}])
def test_input_the_judge_cant_read_passes(given):
    assert removal.judge(ToolCall("claude", "s", None, "Bash", input=given)) is None


@pytest.mark.parametrize("text", [r"r\m", "r''m", "R''M", "rm''dir", "r\"m\"d'i'r"])
def test_the_filter_passes_a_removal_word_split_by_quotes_and_backslashes(text):
    command = f"cd t && {text} -rf x/*"
    assert FILTER.search(command)
    # Rule T reads the text with every quote and backslash deleted, and is case-sensitive.
    assert verdict(removal.judge(call_for(command))) == ("pass" if "R" in text else "T")


def test_the_filter_passes_over_commands_the_rule_could_not_refuse():
    for text in ["ls -la", "git status", "echo arm farm", "rmx sub/*", "cd t && ls *",
                 "bash -c 'echo $X'", "cd t && r{}m -rf *", "cd t && r$'m' -rf *"]:
        assert not FILTER.search(text)
        assert removal.judge(call_for(text)) is None


def test_the_filter_passes_every_command_the_judge_refuses_over_random_text():
    rng = random.Random(8)
    # Rule T needs all three of a cd, an rm and a *, so those come up more often.
    alphabet = list("rmdiRMDI'\"\\{}$ -c*/.;~ab") + ["bash ", "sh ", " -c ", "rmdir", "ı",
                                                       "ſ", "K"] + ["cd ", "rm", " *"] * 4
    refused = 0
    for _ in range(4000):
        text = "".join(rng.choice(alphabet) for _ in range(rng.randint(1, 14)))
        if removal.judge(call_for(text)) is not None:
            refused += 1
            assert FILTER.search(text), text
    assert refused > 200


@pytest.mark.parametrize("text,want", [
    ("cat > s <<'E'\nrm *\nE\necho", "cat > s <<'E'\nE\necho"),
    ("cat > s <<-'E'\n\trm *\n\tE", "cat > s <<-'E'\n\tE"),
    ("cat > a <<'A'; tee b <<\\B\nx\nA\ny\nB", "cat > a <<'A'; tee b <<\\B\nA\nB"),
    ("cat <<'E' | sh\nrm *\nE", "cat <<'E' | sh\nrm *\nE"),
    ("cat > s <<'E' || true\nrm *\nE", "cat > s <<'E' || true\nE"),
    ("cat > s <<E\nrm *\nE", "cat > s <<E\nrm *\nE"),
    ("cat > s <<'E'\nrm *\n", "cat > s <<'E'\nrm *\n")])
def test_the_heredoc_cut(text, want):
    assert removal.cut_heredocs(text) == want


def test_the_reason_gives_two_fixes_and_names_no_ones_paths():
    answer = removal.judge(call_for("cd t && rm -rf *"))
    assert answer.reason == removal.REASON_T and removal.REASONS == {"T": removal.REASON_T}
    assert answer.reason.startswith("Refused before it ran (turns removal guard")
    assert "(1) Drop the cd and use full paths" in answer.reason
    assert "(2) If the text is file content" in answer.reason
    assert "/home/" not in answer.reason and "operator" not in answer.reason
    assert "hook" not in answer.reason


def test_the_checked_claude_code_version_is_a_constant():
    assert removal.CHECKED_CLAUDE == "2.1.325"


# -- no-nested-agents ----------------------------------------------------------------------

@pytest.mark.parametrize("harness,tool,agent,want", [
    ("claude", "Agent", "a-1", "deny"), ("claude", "Task", "a-1", "deny"),
    ("claude", "Agent", None, "next"), ("claude", "Agent", "", "next"),
    ("claude", "Bash", "a-1", "next"),
    ("codex", "spawn_agent", "a-1", "deny"), ("codex", "spawn_agent", None, "next"),
    ("codex", "Agent", "a-1", "next")])
def test_no_nested_agents_is_data_only_on_both_harnesses(harness, tool, agent, want):
    on = guards.On(found(BY_NAME["no-nested-agents"]), ("switch",))
    entry = entry_for(on, harness, 3000)
    assert entry is not None and entry.judge is False
    d = decide([entry], tool, agent, {})
    assert d.verdict == want
    if want == "deny":
        assert d.deny == guards.NO_NESTED_AGENTS_DENY and not d.ask


def test_no_nested_agents_covers_nothing_on_opencode():
    on = guards.On(found(BY_NAME["no-nested-agents"]), ("switch",))
    assert entry_for(on, "opencode", 3000) is None


# -- question mode ---------------------------------------------------------------------------

@pytest.fixture
def argv(monkeypatch):
    """The Claude Code process's arguments, through a fake reader; `reads` counts asks."""
    state = SimpleNamespace(args=["claude", "--model", "x"], reads=[], fail=None)

    def cmdline(pid):
        state.reads.append(pid)
        if state.fail is not None:
            raise state.fail
        return list(state.args)

    monkeypatch.setattr(qm, "cmdline", cmdline)
    monkeypatch.setattr(qm, "harness_pid", lambda: 4242)
    return state


def ask(*prompts, agent=None, env=None) -> ToolCall:
    return ToolCall("claude", "s-1", agent, "AskUserQuestion", "tu-1", {"questions": []},
                    "/w", None, dict(os.environ) if env is None else env, tuple(prompts))


def test_a_subagent_is_refused(turns_env, argv):
    got = qm.judge(ask(Prompt("composer", "any questions?"), agent="a-1"))
    assert got == Deny(qm.R_SUBAGENT) and not argv.reads


def test_a_typed_prompt_mentioning_a_question_allows(turns_env, argv):
    assert qm.judge(ask(Prompt("composer", "One QUESTION for you"))) is None
    assert argv.reads == [4242]


def test_a_prompt_from_the_claude_apps_counts_as_typed_by_default(turns_env, argv):
    assert qm.judge(ask(Prompt("bridge", "any questions?"))) is None
    assert qm.judge(ask(Prompt("plugin", "any questions?"))) == \
        Deny(qm.R_NOT_HERE.format(origin="plugin"))


def test_a_config_of_composer_alone_refuses_a_prompt_from_the_claude_apps(turns_env, argv):
    write_config(turns_env, '[question_mode]\norigins = ["composer"]\n')
    assert qm.judge(ask(Prompt("bridge", "any questions?"))) == \
        Deny(qm.R_NOT_HERE.format(origin="bridge"))
    assert qm.judge(ask(Prompt("composer", "any questions?"))) is None


@pytest.mark.parametrize("prompts,want", [
    ((), qm.R_NOT_ASKED),
    ((Prompt("composer", "carry on"),), qm.R_NOT_ASKED),
    ((Prompt("plugin", "go through the open questions"),),
     qm.R_NOT_HERE.format(origin="plugin")),
    ((Prompt(None, "a question"),), qm.R_NOT_HERE.format(origin="none")),
    ((Prompt("composer", "carry on"), Prompt("plugin", "a question")),
     qm.R_NOT_HERE.format(origin="plugin")),
])
def test_without_a_typed_question_it_refuses_naming_why(turns_env, argv, prompts, want):
    assert qm.judge(ask(*prompts)) == Deny(want)
    assert not argv.reads, "no argument read unless a grant is in sight"


def test_the_launch_prompt_never_counts(turns_env, argv):
    argv.args = ["claude", "--model", "x", "  any questions about the plan?\n"]
    assert qm.judge(ask(Prompt("composer", "any questions about the plan?"))) == \
        Deny(qm.R_LAUNCH)
    # A second typed prompt that is not the launch prompt allows.
    assert qm.judge(ask(Prompt("composer", "any questions about the plan?"),
                        Prompt("composer", "one more question"))) is None
    # Only a whole argument counts.
    argv.args = ["claude", "any questions about the plan? and more"]
    assert qm.judge(ask(Prompt("composer", "any questions about the plan?"))) is None


def test_the_origins_come_from_turns_config(turns_env, argv):
    write_config(turns_env, '[question_mode]\norigins = ["composer", "plugin"]\n')
    assert qm.judge(ask(Prompt("plugin", "a question"))) is None
    write_config(turns_env, '[question_mode]\norigins = []\n')
    assert qm.judge(ask(Prompt("composer", "a question"))) == \
        Deny(qm.R_NOT_HERE.format(origin="composer"))


def test_a_failed_argument_read_raises_and_the_runner_refuses(turns_env, argv):
    argv.fail = ProcessLookupError("gone")
    with pytest.raises(ProcessLookupError):
        qm.judge(ask(Prompt("composer", "a question")))
    g = BY_NAME["question-mode"]
    runner = GuardRunner("claude", settings=lambda: ([], 3000),
                         load=lambda on: guards.Loaded(on=(guards.On(found(g), ("switch",)),)),
                         record=lambda *a: None, clear=lambda *a: None)
    try:
        runner.reload()
        got = runner.ask(ask(Prompt("composer", "a question")), ["turns.question-mode"])
        assert got.deny == break_deny("turns.question-mode",
                                      "it raised ProcessLookupError: gone")
    finally:
        runner.close()


def test_the_real_argument_reader_reads_a_process():
    args = qm.cmdline(os.getpid())
    assert args and isinstance(args[0], str)


# -- the reminder ----------------------------------------------------------------------------

@pytest.mark.parametrize("text,want", [
    ("let's go through open questions", True),
    ("lets go through the open questions, I'm ready", True),
    ("I'm ready to answer open questions", True),
    ("let's answer the open questions", True),
    ("Okay, let's start going through open questions.", True),
    ("Go through open-questions", True),
    ("I'm not ready to go through open questions", True),
    ("go through open questions in pros", True),
    ("let's go through open questions about the prosecution case", True),
    ("a question about X", False),
    ("quick question about the cache", False),
    ("there's an open question about the cache, what do you think?", False),
    ("what open questions are left?", False),
    ("go through the diff", False),
    ("let's go through open questions in prose", False),
    ("Let's go through open questions in PROSE", False),
    ("<channel source=\"x\">let's go through open questions</channel>", False),
    ("  <task-notification>go through open questions</task-notification>", False),
    ("say hi", False), ("", False), (None, False), (["go through open questions"], False)])
def test_nudges(text, want):
    assert qm.nudges(text, ("prose",)) is want


def test_nudges_reads_the_prose_words_it_is_given():
    text = "go through open questions in pros"
    assert qm.nudges(text, ("prose",)) is True
    assert qm.nudges(text, ("prose", "pros")) is False
    assert qm.nudges("go through open questions in prose", ()) is True


def prompt_event(text, origin, env=None) -> SessionEvent:
    return SessionEvent("prompt", "claude", "s-1", env=dict(os.environ) if env is None else env,
                        cwd="/w", text=text, origin=origin)


def test_the_reminder_comes_only_with_question_mode_on_and_a_counted_origin(turns_env):
    text = "let's go through the open questions"
    assert qm.on_prompt(prompt_event(text, "composer")) is None            # off
    write_config(turns_env, "[guards]\nquestion_mode = true\n")
    assert qm.on_prompt(prompt_event(text, "composer")) == qm.REMINDER
    assert qm.on_prompt(prompt_event(text, "bridge")) == qm.REMINDER
    assert qm.on_prompt(prompt_event(text, "plugin")) is None
    assert qm.on_prompt(prompt_event(text, None)) is None
    assert qm.on_prompt(prompt_event("go through the open questions in prose",
                                     "composer")) is None
    write_config(turns_env, '[guards]\nrun = ["turns.question-mode"]\n')
    assert qm.on_prompt(prompt_event(text, "composer")) == qm.REMINDER
    write_config(turns_env, '[guards]\nrun = ["turns.question-mode"]\n'
                            '[question_mode]\norigins = ["plugin"]\n')
    assert qm.on_prompt(prompt_event(text, "plugin")) == qm.REMINDER
    assert qm.on_prompt(prompt_event(text, "composer")) is None
    write_config(turns_env, '[guards]\nquestion_mode = true\n'
                            '[question_mode]\norigins = ["composer"]\n')
    assert qm.on_prompt(prompt_event(text, "composer")) == qm.REMINDER
    assert qm.on_prompt(prompt_event(text, "bridge")) is None


def test_the_reminder_reads_the_prose_words_setting(turns_env):
    text = "let's go through the open questions in pros"
    write_config(turns_env, "[guards]\nquestion_mode = true\n")
    assert qm.on_prompt(prompt_event(text, "composer")) == qm.REMINDER
    write_config(turns_env, '[guards]\nquestion_mode = true\n'
                            '[question_mode]\nprose_words = ["Prose", "PROS"]\n')
    assert qm.on_prompt(prompt_event(text, "composer")) is None
    assert qm.on_prompt(prompt_event("go through the open questions in prose",
                                     "composer")) is None
    assert qm.on_prompt(prompt_event("go through the open questions",
                                     "composer")) == qm.REMINDER


def test_the_reminder_names_no_one():
    assert "operator" not in qm.REMINDER and "AskUserQuestion" in qm.REMINDER
    assert "up to four per call" in qm.REMINDER


# -- through the Claude endpoint -------------------------------------------------------------

@pytest.fixture
def endpoint(turns_env):
    """A Claude endpoint whose guard runner reads the real loader and turns' config, and
    whose listeners are turns' own row."""
    made = []

    def make(config_text: str):
        write_config(turns_env, config_text)
        env = dict(os.environ, HOME="/home/user")
        runner_ = GuardRunner("claude", env=env)
        rd, w = pipe_pair()
        harness = ClaudeHarness(w, pid=os.getpid(), cwd="/home/user/proj", guards=runner_)
        reg = listeners.load(entries=[SimpleNamespace(name="turns", load=lambda: guards)],
                             order=(), env=env)
        ep = Endpoint(harness, registry=reg, env=env)
        ep.prepare()
        harness.serve(str(ep.dir / protocol.MOD_SOCK))
        fake = FakeRunner(rd)
        harness.first_line()
        fake.wait_first()
        fake.session("s-1")
        ep.open()
        made.append((ep, w))
        return fake

    yield make
    for ep, w in made:
        ep.stop("the test ended")
        w.close()


def test_end_to_end_the_switched_on_guards_reach_the_table_and_judge(endpoint, argv):
    fake = endpoint("[guards]\nremoval = true\nquestion_mode = true\n")
    table = {e["id"]: e for e in fake.first["guards"]}
    assert set(table) == {"turns.removal", "turns.question-mode"}
    assert table["turns.removal"]["match"] == {"command": guards.REMOVAL_MATCH}
    assert table["turns.removal"]["match_flags"] == "i"
    assert table["turns.question-mode"]["wants"] == ["prompts"]

    refused = fake.post(leg.PATH_GUARD, {
        "call": {"tool": "Bash", "tool_use_id": "tu-1", "input": {"command": "cd t && rm -rf *"}},
        "guards": ["turns.removal"]})
    assert refused["deny"] == removal.REASON_T
    ran = fake.post(leg.PATH_GUARD, {
        "call": {"tool": "Bash", "input": {"command": "rm -rf ~/tmp/x/*"}},
        "guards": ["turns.removal"]})
    assert "deny" not in ran and ran["broken"] == []

    asked = fake.post(leg.PATH_GUARD, {
        "call": {"tool": "AskUserQuestion", "input": {},
                 "prompts": [{"origin": "composer", "text": "a question for you"}]},
        "guards": ["turns.question-mode"]})
    assert "deny" not in asked and asked["broken"] == []
    not_asked = fake.post(leg.PATH_GUARD, {
        "call": {"tool": "AskUserQuestion", "input": {}, "prompts": []},
        "guards": ["turns.question-mode"]})
    assert not_asked["deny"] == qm.R_NOT_ASKED

    reminded = fake.post(leg.PATH_CONTEXT, {"event": "prompt", "session_id": "s-1",
                                            "text": "go through the open questions",
                                            "origin": "composer"})
    assert reminded["context"] == qm.REMINDER


def test_end_to_end_a_subagents_agent_call_is_refused_without_a_trip(endpoint):
    fake = endpoint('[guards]\nrun = ["turns.no-nested-agents"]\n')
    (entry,) = fake.first["guards"]
    assert entry["id"] == "turns.no-nested-agents" and entry["judge"] is False
    assert entry["deny"] == guards.NO_NESTED_AGENTS_DENY and entry["who"] == "agents"

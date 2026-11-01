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
    assert guards.LISTENERS == ((("prompt",), "moxi_turns.guards.question:on_prompt"),)


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
    write_config(turns_env, '[guards]\non = ["turns.no-nested-agents"]\nremoval = true\n')
    loaded = guards.load(env=env)
    assert {(o.id, o.by) for o in loaded.on} == {("turns.removal", ("switch",)),
                                                 ("turns.no-nested-agents", ("list",))}


def test_the_listener_row_loads_from_the_group():
    reg = listeners.load(entries=[SimpleNamespace(name="turns", load=lambda: guards)],
                         order=())
    assert [(ls.module, ls.events, ls.target) for ls in reg.listeners] == [
        ("turns", frozenset({"prompt"}), "moxi_turns.guards.question:on_prompt")]
    assert not reg.broken


# -- the removal judge -------------------------------------------------------------------

def call_for(command, cwd=rc.CWD, home=rc.HOME, *, agent=None) -> ToolCall:
    env = {} if home is rc.UNSET else {"HOME": home}
    return ToolCall("claude", "s-1", agent, "Bash", "tu-1", {"command": command}, cwd, None,
                    env)


def verdict(answer) -> str:
    if answer is None:
        return "pass"
    assert isinstance(answer, Deny)
    parts = answer.reason.split(removal.JOIN)
    return "".join(LETTERS[p] for p in parts)


def test_the_case_labels_are_unique():
    labels = [c[0] for c in rc.CASES]
    assert len(labels) == len(set(labels)) and len(labels) > 500


@pytest.mark.parametrize("label,command,expect,cwd,home", rc.CASES, ids=[c[0] for c in rc.CASES])
def test_a_removal_case(label, command, expect, cwd, home):
    assert verdict(removal.judge(call_for(command, cwd, home))) == expect
    if expect != "pass":
        assert FILTER.search(command), "the filter must pass every call the judge refuses"


ROWS = [c for c in rc.CASES if c[0].startswith("row ") and c[4] is rc.HOME]


@pytest.mark.parametrize("label,command,expect,cwd,home", ROWS, ids=[c[0] for c in ROWS])
def test_a_removal_row_with_home_unset(label, command, expect, cwd, home):
    want = rc.HOME_UNSET.get(label, expect)
    assert verdict(removal.judge(call_for(command, cwd, rc.UNSET))) == want


def test_a_subagents_call_is_judged_the_same():
    assert verdict(removal.judge(call_for("cd t && rm -rf *", agent="a-1"))) == "TW"


@pytest.mark.parametrize("given", [{}, {"command": ["cd t && rm -rf *"]}, {"command": None},
                                   {"cmd": "cd t && rm -rf *"}])
def test_input_the_judge_cant_read_passes(given):
    assert removal.judge(ToolCall("claude", "s", None, "Bash", input=given)) is None


@pytest.mark.parametrize("text", [r"r\m -rf /x", "r''m -rf /x", "r{}m -rf /x", "r$'m' -rf /x",
                                  "R''M -rf /x", "rm''dir x", "r\"m\"d'i'r x"])
def test_the_filter_passes_a_removal_word_split_by_quotes_braces_and_dollars(text):
    assert FILTER.search(text)
    # In a shell -c script, rule C reads each of them as a removal word.
    wrapped = f"bash -c '{text}'"
    assert "C" in verdict(removal.judge(call_for(wrapped)))
    assert FILTER.search(wrapped)


def test_the_filter_passes_over_commands_no_rule_could_refuse():
    for text in ["ls -la", "git status", "echo arm farm", "rmx sub/*", "cd t && ls *",
                 "bash -c 'echo $X'"]:
        assert not FILTER.search(text)
        assert removal.judge(call_for(text)) is None


def test_the_filter_passes_every_command_the_judge_refuses_over_random_text():
    rng = random.Random(8)
    alphabet = list("rmdiRMDI'\"\\{}$ -c*/.;~ab") + ["bash ", "sh ", " -c ", "cd ", "rm",
                                                       "rmdir", "ı", "ſ", "K"]
    refused = 0
    for _ in range(4000):
        text = "".join(rng.choice(alphabet) for _ in range(rng.randint(1, 14)))
        for cwd, home in ((rc.CWD, rc.HOME), (None, rc.UNSET), ("/", rc.HOME)):
            if removal.judge(call_for(text, cwd, home)) is not None:
                refused += 1
                assert FILTER.search(text), text
    assert refused > 500


def test_a_removal_word_in_another_script_is_not_one():
    # The letters are ASCII only, so the filter and the judge agree on a dotless i.
    assert removal.judge(call_for("bash -c 'rmdır zz'")) is None
    assert not FILTER.search("bash -c 'rmdır zz'")
    # A letter that folds into ASCII still counts as a letter beside the word, so rule C
    # finds no removal word there; rule W, whose letters are ASCII, refuses the non-ASCII
    # word.
    assert verdict(removal.judge(call_for("bash -c 'rm\u212a zz'"))) == "W"
    assert verdict(removal.judge(call_for("bash -c 'rm zz'"))) == "C"


@pytest.mark.parametrize("text,want", [
    ("rm zz", True), ('r""m zz', True), ("r{m} zz", True), ("RM zz", True), ("rm2 zz", True),
    ("arm zz", False), ("rmx zz", False), ("rm_x zz", False), ("echo 2rm", False),
    ("{a,b} $'x' $X", False)])
def test_rule_cs_removal_arm(text, want):
    assert removal.c_removal(text) is want


@pytest.mark.parametrize("text,want", [
    ('bash "-c"', True), ('ba"s"h -c', True), ("bash --norc -x", False), ("python3 -c", False)])
def test_rule_cs_gate(text, want):
    assert removal.shell_c(text) is want


def test_the_four_reasons_join_in_order_and_name_no_ones_paths():
    command = "cd t && rm -rf * ; bash -c 'echo hi' ; " + rc.subs("$(", ")", 65)
    answer = removal.judge(call_for(command))
    assert verdict(answer) == "TWSC"
    parts = answer.reason.split("\n\n")
    assert parts == [removal.REASON_T, removal.REASON_W, removal.REASON_S, removal.REASON_C]
    assert all(p.startswith("Refused before it ran (turns removal guard") for p in parts)
    assert "(1) Drop the cd and use full paths" in parts[0]
    assert "(2) If the text is file content" in parts[0]
    assert parts[1].startswith("Refused before it ran (turns removal guard): you are removing "
                               "your working directory or a directory above it")
    assert parts[2].endswith("run it with `bash /full/path/script.sh`.")
    assert "brace" not in parts[3] and "$ variable" not in parts[3]
    for p in parts:
        assert "/home/" not in p and "operator" not in p and "hook" not in p


def test_the_checked_claude_code_version_is_a_constant():
    assert removal.CHECKED_CLAUDE == "2.1.316"


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
    assert qm.nudges(text) is want


def prompt_event(text, origin, env=None) -> SessionEvent:
    return SessionEvent("prompt", "claude", "s-1", env=dict(os.environ) if env is None else env,
                        cwd="/w", text=text, origin=origin)


def test_the_reminder_comes_only_with_question_mode_on_and_a_counted_origin(turns_env):
    text = "let's go through the open questions"
    assert qm.on_prompt(prompt_event(text, "composer")) is None            # off
    write_config(turns_env, "[guards]\nquestion_mode = true\n")
    assert qm.on_prompt(prompt_event(text, "composer")) == qm.REMINDER
    assert qm.on_prompt(prompt_event(text, "plugin")) is None
    assert qm.on_prompt(prompt_event(text, None)) is None
    assert qm.on_prompt(prompt_event("go through the open questions in prose",
                                     "composer")) is None
    write_config(turns_env, '[guards]\non = ["turns.question-mode"]\n')
    assert qm.on_prompt(prompt_event(text, "composer")) == qm.REMINDER
    write_config(turns_env, '[guards]\non = ["turns.question-mode"]\n'
                            '[question_mode]\norigins = ["plugin"]\n')
    assert qm.on_prompt(prompt_event(text, "plugin")) == qm.REMINDER
    assert qm.on_prompt(prompt_event(text, "composer")) is None


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
    assert refused["deny"] == removal.REASON_T + removal.JOIN + removal.REASON_W
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


@pytest.mark.parametrize(("cwd", "refused"), [
    ("/home", True),             # a cd moved the shell: `user` is the home
    ("/home/user", False),       # `user` is a directory under the home
    ("/home/", True),            # absolute, and judged as its normal form
    (None, False),               # no cwd: judged against the launch directory
    ("home", False),             # a relative cwd counts as none
], ids=["moved-to-home-parent", "moved-to-home", "trailing-slash", "none", "relative"])
def test_end_to_end_a_relative_removal_is_judged_against_the_asks_cwd(endpoint, cwd,
                                                                       refused):
    fake = endpoint("[guards]\nremoval = true\n")
    call = {"tool": "Bash", "input": {"command": "rm -rf user"}}
    if cwd is not None:
        call["cwd"] = cwd
    answer = fake.post(leg.PATH_GUARD, {"call": call, "guards": ["turns.removal"]})
    if refused:
        assert answer["deny"] == removal.REASON_W
    else:
        assert "deny" not in answer and answer["broken"] == []
    # Against the launch directory alone, the same word passes.
    assert removal.rules("rm -rf user", "/home/user/proj", "/home/user") == ""


def test_end_to_end_a_subagents_agent_call_is_refused_without_a_trip(endpoint):
    fake = endpoint('[guards]\non = ["turns.no-nested-agents"]\n')
    (entry,) = fake.first["guards"]
    assert entry["id"] == "turns.no-nested-agents" and entry["judge"] is False
    assert entry["deny"] == guards.NO_NESTED_AGENTS_DENY and entry["who"] == "agents"

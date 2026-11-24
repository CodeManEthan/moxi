"""The recall-fork guards: the plain-read test, a fork's calls within its time and tool
calls, the count over its rounds, the refusal of background and self-timed shell calls,
and the refusal of every permission ask of a fork."""

import pytest
from moxi_turns import Deny, ToolCall

from moxi_memory import guards, recall_guard, recall_start, records

SESSION = "0190b000-0000-7000-8000-000000000005"
LIST = recall_start.default_settings()["fork_commands"]


@pytest.mark.parametrize("command", [
    "grep x file", "grep x file | head -5", "grep -E 'a|b' file", "grep x file|head",
    "moxi memory search \"ingest lock\" --since 7d", "moxi sessions read abc",
    "cat 'a file' | wc -l"])
def test_a_plain_read_is_allowed(command):
    assert recall_guard.plain_read(command, LIST)


@pytest.mark.parametrize("command", [
    "grep x f; rm f", "grep x f\nrm f", "grep x f\rrm f", "grep x f & rm f",
    "grep x f && rm f", "grep x f || rm f", "grep x f |& head", "| grep x f",
    "grep x f |", "grep x f | | head", "grep x | sh", "grep x | xargs rm",
    "grep x | sed p", "grep x f 2>&1", "grep x f 2>/dev/null", "grep x f > out",
    "grep x f >> out", "grep x < f", "cat << EOF", "cat <<< x", "cat <(ls)",
    "tee >(ls)", "cat $(ls)", "cat `ls`", "cat ${HOME}", "cat $HOME", "cat $'x'",
    "( grep x f )", "{ grep x f; }", "grep x \\\nf", "VAR=x grep x f", "! grep x f",
    "grep x # ; rm", "grep a#x | sh", "grep x # | sh", "grep \"$(ls)\" f", "grep x\x00 f", "grep x\tf", "grep x\x1bf",
    "grep x f", "grep x f", "env grep x", "xargs grep", "bash -c 'grep x'",
    "find . -exec rm {} +", "sudo cat f", "grep 'unclosed f", "", "   "])
def test_everything_else_is_refused(command):
    assert not recall_guard.plain_read(command, LIST)


def test_a_listed_command_matches_word_for_word():
    assert not recall_guard.plain_read("moxi memory ingest", LIST)
    assert not recall_guard.plain_read("grepx f", LIST)
    assert recall_guard.plain_read("rg x", ["rg"])


def call(tool, agent="agent-1", **given):
    return ToolCall(harness="claude", session_id=SESSION, agent_id=agent, tool=tool,
                    input=given)


def a_fork(**fields):
    record = recall_start.begin(SESSION, "claude", start="prompt", text="ingest lock",
                                senders=(), settings=recall_start.default_settings(),
                                cwd=None, notes_dir=None, route_name="subagent",
                                spawn=lambda s, r: None)
    from moxi_memory import recalls

    with recalls.session_lock(SESSION):
        return records.update(SESSION, record["recall"],
                              **{"state": "running", "fork": "agent-1", **fields})


def test_a_fork_reads_and_each_call_is_counted():
    record = a_fork()
    assert recall_guard.judge_call(call("Read", file_path="/x")) is None
    assert recall_guard.judge_call(call("Bash", command="grep x f | head")) is None
    assert records.read(SESSION, record["recall"])["tools"] == 2


@pytest.mark.parametrize("tool, given", [
    ("Write", {"file_path": "/x", "content": ""}), ("Edit", {}), ("Agent", {}),
    ("SendMessage", {}), ("mcp__comms__send", {}), ("Bash", {"command": "rm -rf x"}),
    ("Bash", {"command": "tail -f log", "run_in_background": True}),
    ("Bash", {"command": "grep x f", "timeout": 600000})])
def test_a_fork_is_refused_everything_but_reads(tool, given):
    record = a_fork()
    assert recall_guard.judge_call(call(tool, **given)) == Deny(recall_guard.READ_ONLY)
    assert records.read(SESSION, record["recall"])["tools"] == 0


@pytest.mark.parametrize("fields", [
    {"state": "hurried"}, {"state": "lost"}, {"tools": 8}, {"at": 0.0}])
def test_a_fork_past_its_limits_is_told_to_answer(fields):
    a_fork(**fields)
    assert recall_guard.judge_call(call("Read")) == Deny(recall_guard.TIME_UP)


def test_another_subagent_and_the_main_loop_are_left_alone():
    a_fork()
    assert recall_guard.judge_call(call("Write", agent="agent-2")) is None
    assert recall_guard.judge_call(call("Write", agent=None)) is None
    assert recall_guard.judge_ask(call("Write", agent="agent-2")) is None


def test_a_call_while_a_record_is_starting_is_judged_as_its_fork():
    record = recall_start.begin(SESSION, "claude", start="prompt", text="x", senders=(),
                                settings=recall_start.default_settings(), cwd=None,
                                notes_dir=None, route_name="subagent",
                                spawn=lambda s, r: None)
    found = recall_guard.fork_record(SESSION, "agent-9", wait_s=0.1)
    assert found is not None and found["recall"] == record["recall"]


def test_every_ask_of_a_fork_is_refused():
    a_fork(state="ended")
    assert recall_guard.judge_ask(call("Bash", command="moxi sessions read x")) == Deny(
        recall_guard.ASK_REFUSED)


def test_the_guards_are_well_formed_and_run_only_where_it_is_on():
    assert [g.name for g in guards.GUARDS] == ["subconscious-fork", "subconscious-fork-ask"]
    for guard in guards.GUARDS:
        assert guard.problems() == []
    assert [g.on_error for g in guards.GUARDS] == ["allow", "deny"]
    assert guards.GUARDS[1].step == "ask"
    assert recall_guard.guarded({recall_start.SUBCONSCIOUS_ENV: "automatic"})
    assert recall_guard.guarded({recall_start.SUBCONSCIOUS_ENV: "on-demand"})
    assert not recall_guard.guarded({})

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
    "cat 'a file' | wc -l", "grep '|' file", "grep -F \"|\" f", "grep 'a'|head",
    "grep x \"f\"|head", "grep '' f", "grep a'|'b f | wc -l"])
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
    "find . -exec rm {} +", "sudo cat f", "grep 'unclosed f", "", "   ",
    "grep '|' f | sh", "grep x '|' | sh", "grep x f||head", "grep x \"f|\" | | head"])
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


@pytest.mark.parametrize("tool", ["PowerShell", "Monitor"])
def test_bash_is_the_one_shell_tool_a_fork_may_read_with(tool):
    assert recall_guard.SHELL == "Bash"
    record = a_fork()
    assert recall_guard.judge_call(call(tool, command="grep x f")) == Deny(
        recall_guard.READ_ONLY)
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


@pytest.mark.parametrize("tool, given, decision", [
    ("Read", {"file_path": "/x"}, None),
    ("Write", {"file_path": "/x", "content": ""}, Deny(recall_guard.READ_ONLY))])
def test_a_lock_held_past_the_budget_still_gets_a_decision_in_time(tool, given, decision):
    import threading
    import time

    from moxi_memory import recalls

    record = a_fork()
    held, done = threading.Event(), threading.Event()

    def hold():
        # a child acking a round over turns' socket holds the lock this long
        with recalls.session_lock(SESSION):
            held.set()
            done.wait(10)

    holder = threading.Thread(target=hold)
    holder.start()
    try:
        held.wait(5)
        began = time.monotonic()
        assert recall_guard.judge_call(call(tool, **given)) == decision
        took = time.monotonic() - began
    finally:
        done.set()
        holder.join()
    assert took < recall_guard.BUDGET_S + 0.3
    # judged without the lock, an allowed call is not counted
    assert records.read(SESSION, record["recall"])["tools"] == 0


def test_the_budget_fits_inside_the_guards_deadline():
    for guard in guards.GUARDS:
        if guard.judge is not None:
            assert recall_guard.BUDGET_S < guard.deadline_ms / 1000
    assert recall_guard.STARTING_WAIT_S < recall_guard.BUDGET_S


def a_starting_record(child, **fields):
    """A `starting` record of no fork yet, with no line (`child` None), a line naming
    no child (`nameless`), or a line naming a child that has ended (`dead`) or this
    process (`live`), and `fields` set on it."""
    import os

    from moxi_memory import recall_take, recalls

    record = recall_start.begin(SESSION, "claude", start="prompt", text="x", senders=(),
                                settings=recall_start.default_settings(), cwd=None,
                                notes_dir=None, route_name="subagent",
                                spawn=lambda s, r: None)
    if child is not None:
        identity = {"dead": {"pid": os.getpid(), "started": 1.0},
                    "nameless": None}.get(child) if child != "live" else recall_take.me()
        with recalls.session_lock(SESSION):
            recalls.put_line(SESSION, {"recall": record["recall"], "harness": "claude",
                                       "state": "open", "child": identity})
    if fields:
        with recalls.session_lock(SESSION):
            record = records.update(SESSION, record["recall"], **fields)
    return record


def test_a_starting_record_whose_child_is_gone_is_no_fork():
    a_starting_record("dead")
    assert recall_guard.fork_record(SESSION, "agent-9", wait_s=0.1) is None
    assert recall_guard.judge_call(call("Write", agent="agent-9")) is None
    assert recall_guard.judge_ask(call("Write", agent="agent-9")) is None


@pytest.mark.parametrize("child", ["live", None])
def test_a_starting_record_whose_child_lives_or_is_not_yet_named_still_counts(child):
    record = a_starting_record(child)
    found = recall_guard.fork_record(SESSION, "agent-9", wait_s=0.1)
    assert found is not None and found["recall"] == record["recall"]


@pytest.mark.parametrize("child", ["live", None])
def test_a_hurried_record_awaiting_its_fork_still_judges_its_calls(child):
    record = a_starting_record(child, state="hurried", rounds=1)
    found = recall_guard.fork_record(SESSION, "agent-9", wait_s=0.1)
    assert found is not None and found["recall"] == record["recall"]
    assert recall_guard.judge_call(call("Read", agent="agent-9")) == Deny(
        recall_guard.TIME_UP)
    assert recall_guard.judge_ask(call("Write", agent="agent-9")) == Deny(
        recall_guard.ASK_REFUSED)


def test_a_hurried_record_whose_child_is_gone_is_no_fork():
    a_starting_record("dead", state="hurried", rounds=1)
    assert recall_guard.fork_record(SESSION, "agent-9", wait_s=0.1) is None
    assert recall_guard.judge_call(call("Write", agent="agent-9")) is None


@pytest.mark.parametrize("state", ["starting", "hurried"])
def test_a_record_with_no_line_awaits_its_fork_only_for_the_grace(state):
    import time

    from moxi_memory import recalls

    grace = records.STARTING_GRACE_S
    record = a_starting_record(None, state=state, rounds=1, at=time.time() - grace + 5)
    found = recall_guard.fork_record(SESSION, "agent-9", wait_s=0.1)
    assert found is not None and found["recall"] == record["recall"]
    with recalls.session_lock(SESSION):
        records.update(SESSION, record["recall"], at=time.time() - grace - 1,
                       at_boot=None)
    assert recall_guard.fork_record(SESSION, "agent-9", wait_s=0.1) is None
    assert recall_guard.judge_call(call("Write", agent="agent-9")) is None
    assert recall_guard.judge_ask(call("Write", agent="agent-9")) is None


@pytest.mark.parametrize("state", ["starting", "hurried"])
def test_a_record_whose_line_names_no_child_awaits_its_fork_only_for_the_grace(state):
    import time

    from moxi_memory import recalls

    grace = records.STARTING_GRACE_S
    record = a_starting_record("nameless", state=state, rounds=1,
                               at=time.time() - grace + 5)
    assert recalls.find_line(SESSION, record["recall"])["child"] is None
    found = recall_guard.fork_record(SESSION, "agent-9", wait_s=0.1)
    assert found is not None and found["recall"] == record["recall"]
    with recalls.session_lock(SESSION):
        records.update(SESSION, record["recall"], at=time.time() - grace - 1,
                       at_boot=None)
    assert recall_guard.fork_record(SESSION, "agent-9", wait_s=0.1) is None
    assert recall_guard.judge_call(call("Write", agent="agent-9")) is None
    assert recall_guard.judge_ask(call("Write", agent="agent-9")) is None


@pytest.mark.parametrize("child", [None, {"pid": "x", "started": 1.0}, {"pid": 1}])
def test_a_line_that_names_no_child_gives_none(child):
    assert recall_guard.line_child(None) is None
    assert recall_guard.line_child({"recall": "r", "child": child}) is None


@pytest.mark.parametrize("state", ["starting", "hurried"])
def test_a_record_whose_line_names_a_live_child_awaits_past_the_grace(state):
    import time

    record = a_starting_record("live", state=state, rounds=1,
                               at=time.time() - records.STARTING_GRACE_S - 60)
    found = recall_guard.fork_record(SESSION, "agent-9", wait_s=0.1)
    assert found is not None and found["recall"] == record["recall"]


def test_an_unnamed_call_is_judged_as_the_fork_of_the_record_whose_round_was_asked():
    asked = a_starting_record("live", state="hurried", rounds=1)
    newer = a_starting_record(None)
    assert newer["at"] >= asked["at"]
    found = recall_guard.fork_record(SESSION, "agent-9", wait_s=0.1)
    assert found["recall"] == asked["recall"]


def test_every_ask_of_a_fork_is_refused():
    a_fork(state="ended")
    assert recall_guard.judge_ask(call("Bash", command="moxi sessions read x")) == Deny(
        recall_guard.ASK_REFUSED)


def test_the_guards_are_well_formed_and_run_only_where_it_is_on():
    assert [g.name for g in guards.GUARDS] == ["subconscious-fork", "subconscious-fork-ask",
                                               "subconscious-slot", "subconscious-slot-ask"]
    for guard in guards.GUARDS:
        assert guard.problems() == []
    assert [g.on_error for g in guards.GUARDS[:3]] == ["allow", "deny", "deny"]
    assert [g.step for g in guards.GUARDS] == ["call", "ask", "call", "ask"]
    slot, slot_ask = guards.GUARDS[2:]
    assert slot.sessions == slot_ask.sessions == "moxi_memory.recall_guard:slot_fork"
    assert slot.who == slot_ask.who == "any"
    assert slot_ask.judge is None and slot_ask.deny == recall_guard.ASK_REFUSED
    assert set(slot.calls) == {"claude", "codex"}
    fork = {recall_start.SUBCONSCIOUS_FORK_ENV: "r-1"}
    assert recall_guard.slot_fork(fork) and not recall_guard.slot_fork({})
    assert not recall_guard.guarded(fork)
    assert recall_guard.guarded({recall_start.SUBCONSCIOUS_ENV: "automatic"})
    assert recall_guard.guarded({recall_start.SUBCONSCIOUS_ENV: "on-demand"})
    assert not recall_guard.guarded({})


# -- a slot fork ---------------------------------------------------------------------------


def slot_call(tool, recall_id, **given):
    return ToolCall(harness="claude", session_id="0190b000-0000-7000-8000-0000000000f0",
                    agent_id=None, tool=tool, input=given,
                    env={recall_start.SUBCONSCIOUS_FORK_ENV: recall_id})


def test_a_slot_forks_calls_are_judged_against_the_record_its_stamp_names():
    record = a_fork(route="slot", fork="0190b000-0000-7000-8000-0000000000f0")
    rid = record["recall"]
    assert recall_guard.judge_slot_call(slot_call("Read", rid, file_path="/x")) is None
    assert recall_guard.judge_slot_call(slot_call("Bash", rid, command="grep x f")) is None
    assert records.read(SESSION, rid)["tools"] == 2
    assert recall_guard.judge_slot_call(slot_call("Write", rid, file_path="/x")) == Deny(
        recall_guard.READ_ONLY)
    assert recall_guard.judge_slot_call(slot_call("Bash", rid, command="rm f")) == Deny(
        recall_guard.READ_ONLY)
    records.update(SESSION, rid, tools=999)
    assert recall_guard.judge_slot_call(slot_call("Read", rid, file_path="/x")) == Deny(
        recall_guard.TIME_UP)


def codex_call(recall_id, tool_name, tool_input):
    """A Codex slot fork's call as turns' Codex host hands it to the guard: the tool's
    name and input as Codex's `PreToolUse` hook carries them (its shell is `Bash`, the
    command text under `command`; `apply_patch` the patch under the same key). turns'
    own tests pin those names."""
    return ToolCall(harness="codex", session_id="0190b000-0000-7000-8000-0000000000f0",
                    agent_id=None, tool=tool_name, input=dict(tool_input),
                    env={recall_start.SUBCONSCIOUS_FORK_ENV: recall_id})


def test_a_codex_slot_forks_calls_are_judged_by_the_same_rule_as_turns_delivers_them():
    record = a_fork(route="slot", fork="0190b000-0000-7000-8000-0000000000f0")
    rid = record["recall"]
    judge = recall_guard.judge_slot_call
    assert judge(codex_call(rid, "Bash", {"command": "grep -n lock notes.md"})) is None
    assert records.read(SESSION, rid)["tools"] == 1
    refused = [("Bash", {"command": "rm notes.md"}),
               ("Bash", {"command": "grep lock notes.md > out"}),
               # Codex's hook hands the command as text; anything else is refused.
               ("Bash", {"command": ["grep", "lock", "notes.md"]}),
               ("apply_patch", {"command": "*** Begin Patch\n*** Delete File: a\n"
                                           "*** End Patch"}),
               ("spawn_agent", {"message": "dig"}),
               ("write_stdin", {"session_id": 1, "chars": "q"}),
               ("mcp__comms__send", {"to": "x", "text": "y"})]
    for tool, given in refused:
        assert judge(codex_call(rid, tool, given)) == Deny(recall_guard.READ_ONLY), tool
    assert records.read(SESSION, rid)["tools"] == 1


def test_a_slot_fork_whose_record_is_gone_is_refused_every_call():
    assert recall_guard.judge_slot_call(slot_call("Read", "r-none", file_path="/x")) == \
        Deny(recall_guard.TIME_UP)
    assert recall_guard.judge_slot_call(slot_call("Read", "", file_path="/x")) == Deny(
        recall_guard.TIME_UP)


def test_a_record_is_found_by_its_recall_id_alone():
    record = a_fork()
    assert records.find(record["recall"])["session"] == SESSION
    assert records.find("r-none") is None and records.find("../x") is None

"""The stale-subagent judgment (`claude_stale.judge`) over transcripts written in a test
folder: Core's hour off the last timestamped line, Claude Code's `waiting`, an `end_turn`
tail past the bookkeeping records, owned files, the workflow folder, and the one
deadline over the whole list."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from moxi_turns import claude_stale
from moxi_turns.claude_stale import JUDGE_BUDGET_S, STALE_AFTER_S, judge
from moxi_turns.results import Agent, Agents

NOW = 1_800_000_000.0
MINUTE = 60.0


def stamp(age_s: float) -> str:
    return datetime.fromtimestamp(NOW - age_s, timezone.utc).isoformat().replace("+00:00", "Z")


def assistant(age_s: float, stop_reason: str = "end_turn") -> dict:
    return {"type": "assistant", "timestamp": stamp(age_s),
            "message": {"role": "assistant", "stop_reason": stop_reason,
                        "content": [{"type": "text", "text": "started."}]}}


def started_shell(age_s: float, output: Path) -> dict:
    """A Bash tool result that names its background task's output file."""
    return {"type": "user", "timestamp": stamp(age_s),
            "message": {"role": "user", "content": [{
                "type": "tool_result", "tool_use_id": "t1",
                "content": f"Command running in background with ID: b1. Output is being "
                           f"written to: {output}. You will be notified when it completes"}]}}


class Session:
    """A session record and its subagents folder, under `root`."""

    def __init__(self, root: Path):
        self.record = root / "s-1.jsonl"
        self.record.write_text("")
        self.sub_dir = root / "s-1" / "subagents"
        self.sub_dir.mkdir(parents=True)

    def write(self, agent_id: str, records: list, *, age_s: float | None = None,
              where: Path | None = None) -> Path:
        """The transcript of `agent_id`, its mtime `age_s` old (the last record's age when
        None)."""
        path = (where or self.sub_dir) / f"agent-{agent_id}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(r) + "\n" for r in records))
        if age_s is None:
            stamped = [r for r in records if "timestamp" in r]
            last = datetime.fromisoformat(stamped[-1]["timestamp"].replace("Z", "+00:00"))
            age_s = NOW - last.timestamp()
        touch(path, age_s)
        return path

    def judge(self, *entries: Agent, clock=None) -> dict[str, Agent]:
        listed = Agents(tuple(entries), complete=True, at=NOW)
        kw = {} if clock is None else {"clock": clock}
        out = judge(listed, str(self.record), now=NOW, **kw)
        assert out.complete and out.at == NOW
        return {e.id: e for e in out.entries}


def touch(path: Path, age_s: float) -> None:
    os.utime(path, (NOW - age_s, NOW - age_s))


def waiting(agent_id: str, **rest) -> Agent:
    return Agent(agent_id, "subagent", "waiting", final=False, **rest)


def test_a_waiting_entry_silent_59_minutes_is_live(tmp_path):
    s = Session(tmp_path)
    s.write("a1", [assistant(59 * MINUTE)])
    got = s.judge(waiting("a1"))["a1"]
    assert got.state == "waiting" and got.last_write_at == NOW - 59 * MINUTE


def test_a_waiting_entry_silent_61_minutes_with_nothing_owned_written_is_stale(tmp_path):
    s = Session(tmp_path)
    s.write("a1", [assistant(61 * MINUTE)])
    got = s.judge(waiting("a1"))["a1"]
    assert (got.state, got.final, got.busy) == ("idle", False, False)
    assert got.last_write_at == NOW - 61 * MINUTE
    assert Agents((got,), complete=True, at=NOW).busy() == 0


def test_an_output_file_written_10_minutes_ago_keeps_it_live(tmp_path):
    s = Session(tmp_path)
    output = tmp_path / "tasks" / "b1.output"
    output.parent.mkdir()
    output.write_text("tick\n")
    touch(output, 10 * MINUTE)
    s.write("a1", [started_shell(62 * MINUTE, output), assistant(61 * MINUTE)])
    got = s.judge(waiting("a1"))["a1"]
    assert got.state == "waiting" and got.last_write_at == NOW - 10 * MINUTE
    touch(output, 70 * MINUTE)                  # silent as long: stale
    assert s.judge(waiting("a1"))["a1"].state == "idle"
    output.unlink()                             # gone: nothing is written to it
    assert s.judge(waiting("a1"))["a1"].state == "idle"


def test_a_task_notice_names_its_output_file_too(tmp_path):
    s = Session(tmp_path)
    output = tmp_path / "tasks" / "b2.output"
    output.parent.mkdir()
    output.write_text("")
    touch(output, 5 * MINUTE)
    notice = {"type": "user", "timestamp": stamp(63 * MINUTE),
              "message": {"role": "user", "content":
                          f"<task-notification><output-file>{output}</output-file>"
                          "</task-notification>"}}
    s.write("a1", [notice, assistant(61 * MINUTE)])
    assert s.judge(waiting("a1"))["a1"].state == "waiting"


def test_a_running_or_pending_entry_is_live_whatever_its_transcript_says(tmp_path):
    s = Session(tmp_path)
    s.write("a1", [assistant(6 * 60 * MINUTE)])
    s.write("a2", [assistant(6 * 60 * MINUTE)])
    got = s.judge(Agent("a1", "subagent", "running", final=False),
                  Agent("a2", "subagent", "pending", final=False))
    assert [(e.state, e.last_write_at) for e in got.values()] == [
        ("running", None), ("pending", None)]


def test_an_end_turn_followed_by_bookkeeping_records_reads_the_end_turn(tmp_path):
    s = Session(tmp_path)
    s.write("a1", [assistant(65 * MINUTE),
                   {"type": "api-request", "timestamp": stamp(62 * MINUTE)},
                   {"type": "api-request-blob"},
                   {"type": "attachment", "timestamp": stamp(61 * MINUTE),
                    "attachment": {"type": "date"}},
                   {"type": "data-flow-records"}])
    got = s.judge(waiting("a1"))["a1"]
    assert got.state == "idle" and got.last_write_at == NOW - 61 * MINUTE


def test_a_turn_not_ended_keeps_it_live(tmp_path):
    s = Session(tmp_path)
    s.write("a1", [assistant(61 * MINUTE, stop_reason="tool_use")])
    s.write("a2", [assistant(63 * MINUTE),
                   {"type": "user", "timestamp": stamp(62 * MINUTE),
                    "message": {"role": "user", "content": "go on"}}])
    got = s.judge(waiting("a1"), waiting("a2"))
    assert got["a1"].state == "waiting" and got["a2"].state == "waiting"


def test_a_last_timestamped_line_inside_the_hour_keeps_it_live_whatever_the_mtime(tmp_path):
    s = Session(tmp_path)
    s.write("a1", [assistant(30 * MINUTE)], age_s=2 * 60 * MINUTE)
    got = s.judge(waiting("a1"))["a1"]
    assert got.state == "waiting" and got.last_write_at == NOW - 30 * MINUTE


def test_a_recent_mtime_is_live_and_nothing_more_is_read(tmp_path):
    s = Session(tmp_path)
    path = s.sub_dir / "agent-a1.jsonl"
    path.write_text("not json\n")
    touch(path, 5 * MINUTE)
    got = s.judge(waiting("a1"))["a1"]
    assert got.state == "waiting" and got.last_write_at == NOW - 5 * MINUTE


def test_an_unreadable_or_unknown_transcript_leaves_claudes_status(tmp_path):
    s = Session(tmp_path)
    path = s.sub_dir / "agent-a1.jsonl"
    path.write_text("not json\n")
    touch(path, 2 * 60 * MINUTE)
    s.write("a2", [{"type": "attachment"}], age_s=2 * 60 * MINUTE)   # no timestamp
    s.write("a3", [{"type": "assistant", "timestamp": "yesterday"}], age_s=2 * 60 * MINUTE)
    s.write("a4", [{"type": "assistant", "timestamp": stamp(70 * MINUTE),
                    "message": "end_turn"}])
    got = s.judge(waiting("a1"), waiting("a2"), waiting("a3"), waiting("a4"))
    assert [(e.state, e.last_write_at) for e in got.values()] == [("waiting", None)] * 4


def test_a_workflow_agents_transcript_is_found_and_an_id_in_neither_place_keeps_its_status(
        tmp_path):
    s = Session(tmp_path)
    s.write("w1", [assistant(61 * MINUTE)], where=s.sub_dir / "workflows" / "wf_run1")
    got = s.judge(waiting("w1"), waiting("nowhere"))
    assert got["w1"].state == "idle"
    assert got["nowhere"].state == "waiting" and got["nowhere"].last_write_at is None


def test_an_agent_it_started_that_wrote_inside_the_hour_keeps_it_live(tmp_path):
    s = Session(tmp_path)
    s.write("a1", [assistant(61 * MINUTE)])
    s.write("c1", [assistant(70 * MINUTE)])
    s.write("g1", [assistant(10 * MINUTE)])     # a grandchild, through c1
    got = s.judge(waiting("a1"), Agent("c1", "subagent", "done", final=True, parent_id="a1"),
                  Agent("g1", "subagent", "running", final=False, parent_id="c1"))
    assert got["a1"].state == "waiting" and got["a1"].last_write_at == NOW - 10 * MINUTE
    touch(s.sub_dir / "agent-g1.jsonl", 90 * MINUTE)
    assert s.judge(waiting("a1"),
                   Agent("c1", "subagent", "done", final=True, parent_id="a1"),
                   Agent("g1", "subagent", "done", final=True, parent_id="c1"),
                   )["a1"].state == "idle"


def test_an_owned_agent_with_no_transcript_leaves_claudes_status(tmp_path):
    s = Session(tmp_path)
    s.write("a1", [assistant(61 * MINUTE)])
    got = s.judge(waiting("a1"), Agent("c1", "subagent", "running", final=False,
                                       parent_id="a1"))
    assert got["a1"].state == "waiting" and got["a1"].last_write_at is None


def test_teammates_and_forks_are_never_judged(tmp_path):
    s = Session(tmp_path)
    s.write("t1", [assistant(61 * MINUTE)])
    s.write("f1", [assistant(61 * MINUTE)])
    got = s.judge(Agent("t1", "teammate", "waiting", final=False), waiting("f1", is_fork=True))
    assert [(e.state, e.last_write_at) for e in got.values()] == [("waiting", None)] * 2


def test_entries_not_judged_by_the_deadline_keep_their_status(tmp_path):
    s = Session(tmp_path)
    for agent_id in ("a1", "a2", "a3"):
        s.write(agent_id, [assistant(61 * MINUTE)])
    # The deadline is read at the start, before each stat, before each silent entry and
    # between the steps of one: the first entry is judged whole, then time is up.
    ticks = iter([0.0] * 7)

    def clock() -> float:
        return next(ticks, JUDGE_BUDGET_S + 1.0)

    got = s.judge(waiting("a1"), waiting("a2"), waiting("a3"), clock=clock)
    assert [got[i].state for i in ("a1", "a2", "a3")] == ["idle", "waiting", "waiting"]
    assert got["a2"].last_write_at is None and got["a3"].last_write_at is None


def test_with_no_record_path_nothing_is_judged(tmp_path):
    listed = Agents((waiting("a1"),), complete=True, at=NOW)
    assert judge(listed, None, now=NOW) is listed


def test_the_subagents_dir_sits_beside_the_record():
    record = Path("projects") / "p" / "s-1.jsonl"
    assert claude_stale.subagents_dir(str(record)) == Path("projects") / "p" / "s-1" / "subagents"
    assert STALE_AFTER_S == 3600.0 and JUDGE_BUDGET_S == 0.3

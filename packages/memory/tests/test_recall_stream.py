"""A recall's stream: rounds with their refs and the kept mark, each settled line first,
then acked, then unmarked; the stream's ends; a first round lost and a later one."""

import os
import types

import pytest
from moxi_turns import Ended, Lost, Started

from moxi_memory import recall_child, recall_start, recall_stream, recalls, records

SESSION = "0190b000-0000-7000-8000-000000000001"


class FakeRoute:
    name = "subagent"

    def __init__(self, ends, *, fork_id="agent-1", on_ack=None):
        self.ends = list(ends)
        self.fork_id = fork_id
        self.asked = []
        self.acked = []
        self.on_ack = on_ack

    def first(self, text, *, ref, round_timeout_s):
        self.asked.append(("fork", text, ref, round_timeout_s))
        return types.SimpleNamespace(id=f"d-{ref}")

    def next(self, prior, text, *, ref, round_timeout_s):
        self.asked.append(("continue", text, ref, round_timeout_s, prior.id))
        return types.SimpleNamespace(id=f"d-{ref}")

    def started(self, handle):
        return self.fork_id

    def ended(self, handle, timeout_s):
        end = self.ends.pop(0)
        if isinstance(end, BaseException):
            raise end
        return end

    def ack(self, ref):
        line = recalls.find_line(SESSION, self.recall_id)
        self.acked.append((ref, line["state"], line["kept"]))
        if self.on_ack:
            self.on_ack(ref)


def ended(answer):
    return Ended(turn_id=None, outcome="completed", answer=answer)


def a_recall(**settings):
    record = recall_start.begin(SESSION, "claude", start="prompt", text="ingest lock",
                                senders=(), settings=dict(recall_start.default_settings(),
                                                          **settings),
                                cwd=None, notes_dir=None, route_name="subagent",
                                spawn=lambda s, r: None)
    recall = recall_child.Recall(records.read(SESSION, record["recall"]))
    recall_child.open_line(recall)
    return recall


def run(recall, route, **kwargs):
    route.recall_id = recall.recall_id
    return recall_stream.run(recall, route, "the brief", **kwargs)


def test_a_stream_runs_rounds_until_end_of_recall():
    recall = a_recall()
    route = FakeRoute([ended("Recalled ...:\n- a"), ended("- b\nEnd of recall.")])
    assert run(recall, route) == "ended"
    rid = recall.recall_id
    assert [a[0] for a in route.asked] == ["fork", "continue"]
    assert route.asked[0][1:4] == ("the brief", f"{rid}:1", 90.0)
    assert route.asked[1][2:] == (f"{rid}:2", 90.0, f"d-{rid}:1")
    assert "Keep digging on the same prompt" in route.asked[1][1]
    assert "8 tool calls" in route.asked[1][1]
    # every round's line state is written before its ack, with the mark still on
    assert route.acked == [(f"{rid}:1", "back", True), (f"{rid}:2", "back", True)]
    line = recalls.find_line(SESSION, rid)
    assert line["state"] == "back" and line["kept"] is False
    assert [r["text"] for r in line["rounds"]] == ["Recalled ...:\n- a",
                                                   "- b\nEnd of recall."]
    assert line["fork"] == "agent-1"
    record = records.read(SESSION, rid)
    assert record["state"] == "ended" and record["answered"] is True
    assert record["fork"] == "agent-1" and record["rounds"] == 2


@pytest.mark.parametrize("answer", ["NOTHING", "", None])
def test_nothing_ends_the_stream_after_one_round(answer):
    recall = a_recall()
    route = FakeRoute([ended(answer)])
    assert run(recall, route) == "ended"
    assert len(route.asked) == 1


def test_max_rounds_ends_the_stream():
    recall = a_recall(max_rounds=2)
    route = FakeRoute([ended("- a"), ended("- b"), ended("- c")])
    run(recall, route)
    assert len(route.asked) == 2


def test_the_limit_ends_the_stream_past_limit():
    recall = a_recall(limit_s=5)
    at = recall.record["at"]
    route = FakeRoute([ended("- a"), ended("- b")])
    run(recall, route, clock=lambda: at + 6)
    assert len(route.asked) == 1
    assert recalls.find_line(SESSION, recall.recall_id)["past_limit"] is True


def test_tool_calls_used_end_the_stream():
    recall = a_recall(max_tools=2)

    def spend(ref):
        # the ack runs under the session's lock, which the guard in another process
        # takes; this stand-in writes the count beside it
        records.update(SESSION, recall.recall_id, tools=2)

    run(recall, FakeRoute([ended("- a"), ended("- b")], on_ack=spend))
    assert records.read(SESSION, recall.recall_id)["rounds"] == 1


def test_a_hurried_recall_gets_no_further_round_and_stays_hurried_until_its_end():
    recall = a_recall()

    def hurry(ref):
        records.hurry_running(SESSION)

    route = FakeRoute([ended("- a"), ended("- b")], on_ack=hurry)
    run(recall, route)
    assert len(route.asked) == 1
    line = recalls.find_line(SESSION, recall.recall_id)
    assert line["hurried"] is True and line["state"] == "back"
    assert records.read(SESSION, recall.recall_id)["state"] == "ended"


def test_a_first_round_lost_loses_the_recall_and_is_acked():
    recall = a_recall()
    route = FakeRoute([Lost("denied: too many agents")], fork_id=None)
    assert run(recall, route) == "lost"
    rid = recall.recall_id
    line = recalls.find_line(SESSION, rid)
    assert line["state"] == "lost" and line["reason"] == "denied" and line["kept"] is False
    assert route.acked == [(f"{rid}:1", "lost", True)]
    record = records.read(SESSION, rid)
    assert record["state"] == "lost" and record["reason"] == "denied"


def test_a_later_round_lost_ends_the_stream_back():
    recall = a_recall()
    route = FakeRoute([ended("- a"), Lost("gone: the round's bound passed")])
    assert run(recall, route) == "ended"
    line = recalls.find_line(SESSION, recall.recall_id)
    assert line["state"] == "back" and line["kept"] is False
    assert line["rounds"][-1]["lost"] == "gone"
    assert records.read(SESSION, recall.recall_id)["state"] == "ended"


def test_a_round_turns_gave_no_end_for_keeps_its_mark():
    recall = a_recall()
    route = FakeRoute([TimeoutError()])
    assert run(recall, route) == "unsettled"
    line = recalls.find_line(SESSION, recall.recall_id)
    assert line["state"] == "open" and line["kept"] is True
    assert line["ref"] == f"{recall.recall_id}:1"
    assert route.acked == []


def test_an_ack_turns_refused_leaves_the_mark():
    recall = a_recall()

    def refuse(ref):
        raise RuntimeError("the endpoint did not answer")

    run(recall, FakeRoute([ended("NOTHING")], on_ack=refuse))
    line = recalls.find_line(SESSION, recall.recall_id)
    assert line["state"] == "back" and line["kept"] is True


class KeptEnds(FakeRoute):
    """A route whose turns keeps every round's end until its ref is acked, and refuses
    the acks of the refs in `refuse`."""

    def __init__(self, ends, *, refuse=()):
        super().__init__(ends)
        self.held = set()
        self.refuse = set(refuse)

    def first(self, text, *, ref, round_timeout_s):
        self.held.add(ref)
        return super().first(text, ref=ref, round_timeout_s=round_timeout_s)

    def next(self, prior, text, *, ref, round_timeout_s):
        self.held.add(ref)
        return super().next(prior, text, ref=ref, round_timeout_s=round_timeout_s)

    def ack(self, ref):
        if ref in self.refuse:
            self.refuse.discard(ref)
            raise RuntimeError("the endpoint got the ack and did not answer")
        self.held.discard(ref)


def test_an_ack_not_taken_ends_the_stream_and_its_kept_end_stays_named_for_a_taker():
    from moxi_memory import recall_take

    recall = a_recall()
    rid = recall.recall_id
    route = KeptEnds([ended("- a"), ended("- b"), ended("- c\nEnd of recall.")],
                     refuse={f"{rid}:1"})
    route.recall_id = rid
    assert recall_stream.run(recall, route, "the brief") == "ended"
    # no later round was asked over the kept one
    assert [a[2] for a in route.asked] == [f"{rid}:1"]
    line = recalls.find_line(SESSION, rid)
    assert line["state"] == "back" and line["kept"] is True and line["ref"] == f"{rid}:1"
    assert records.read(SESSION, rid)["state"] == "ended"
    assert route.held == {f"{rid}:1"}
    # once the child is gone a taker acks the line's round, and turns releases its end
    with recalls.session_lock(SESSION):
        # this pid with a start time it never had: a process that has ended
        line["child"] = {"pid": os.getpid(), "started": 1.0}
        recalls.put_line(SESSION, line)
    assert recall_take.take(SESSION, rid, identity=recall_take.me(), route=route) == "acked"
    assert route.held == set()
    assert recalls.find_line(SESSION, rid)["kept"] is False


def test_a_round_is_never_opened_over_a_kept_end():
    recall = a_recall()
    rid = recall.recall_id
    assert recall_stream.open_round(recall, 1, f"{rid}:1") is None
    assert recall_stream.open_round(recall, 2, f"{rid}:2") == "kept"
    line = recalls.find_line(SESSION, rid)
    assert line["ref"] == f"{rid}:1" and line["kept"] is True


def test_a_recall_hurried_in_the_gap_before_its_next_round_gets_none(monkeypatch):
    recall = a_recall()

    def hurry_after_the_check(record, settings, round_number, *, now):
        # a newer recall's begin lands between the stream's check and its next ask
        with recalls.session_lock(SESSION):
            records.hurry_running(SESSION)
        return None

    route = FakeRoute([ended("- a"), ended("- b")])
    monkeypatch.setattr(recall_stream, "why_stop", hurry_after_the_check)
    assert run(recall, route) == "ended"
    assert len(route.asked) == 1
    line = recalls.find_line(SESSION, recall.recall_id)
    assert line["hurried"] is True and line["state"] == "back" and line["kept"] is False
    assert records.read(SESSION, recall.recall_id)["state"] == "ended"


def test_a_recall_hurried_while_starting_never_starts_its_fork():
    recall = a_recall()
    with recalls.session_lock(SESSION):
        records.hurry_running(SESSION)
    route = FakeRoute([ended("- a")])
    assert run(recall, route) == "lost"
    assert route.asked == []
    line = recalls.find_line(SESSION, recall.recall_id)
    assert line["state"] == "lost" and line["reason"] == "hurried"
    assert line["kept"] is False and line["ref"] is None
    record = records.read(SESSION, recall.recall_id)
    assert record["state"] == "lost" and record["reason"] == "hurried"


def test_a_start_not_read_in_its_wait_is_read_again_once_the_round_ended():
    recall = a_recall()

    class LateStart(FakeRoute):
        def __init__(self, ends):
            super().__init__(ends)
            self.starts = [None, "agent-7"]

        def started(self, handle):
            return self.starts.pop(0) if self.starts else "agent-7"

    route = LateStart([ended("NOTHING")])
    assert run(recall, route) == "ended"
    assert recalls.find_line(SESSION, recall.recall_id)["fork"] == "agent-7"
    assert records.read(SESSION, recall.recall_id)["fork"] == "agent-7"


def test_the_subagent_route_with_no_endpoint_loses_the_first_round():
    from moxi_memory import recall_route

    recall = a_recall()
    route = recall_route.Subagent(SESSION)
    handle = route.first("the brief", ref="r:1", round_timeout_s=90.0)
    assert route.started(handle) is None
    end = route.ended(handle, 1.0)
    assert isinstance(end, Lost) and end.reason.startswith("gone")
    assert not isinstance(end, Started)
    route.ack("r:1")
    assert recall.recall_id

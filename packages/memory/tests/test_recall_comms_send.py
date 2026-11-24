"""The send by comms on a route that answers by comms: the line `sending` before the ack,
the send held until comms takes it, `NOTHING` sending nothing, a round its bound ended
and its late text, and a taker sending what a line holds."""

import os
import types

import pytest
from moxi_turns import Ended

from moxi_memory import (recall_child, recall_cli, recall_send, recall_start, recall_stream,
                         recall_take, recalls, records)

SESSION = "0190b000-0000-7000-8000-000000000021"
ROUTE = "comms-route"
DEAD = {"pid": os.getpid(), "started": 1.0}


@pytest.fixture(autouse=True)
def _comms_route(monkeypatch):
    monkeypatch.setattr(recall_start, "BY_COMMS", (ROUTE,))


class Route:
    name = ROUTE

    def __init__(self, ends):
        self.ends = list(ends)
        self.asked = []
        self.acked = []
        self.waits = []

    def first(self, text, *, ref, round_timeout_s):
        self.asked.append(ref)
        return types.SimpleNamespace(id=f"d-{ref}")

    def next(self, prior, text, *, ref, round_timeout_s):
        self.asked.append(ref)
        return types.SimpleNamespace(id=f"d-{ref}")

    def started(self, handle):
        return "fork-1"

    def ended(self, handle, timeout_s):
        self.waits.append(timeout_s)
        end = self.ends.pop(0)
        if isinstance(end, BaseException):
            raise end
        return end

    def ack(self, ref):
        rid = ref.rsplit(":", 1)[0]
        line = recalls.find_line(SESSION, rid)
        self.acked.append((ref, line["state"], line["kept"]))


class Sender:
    """A stand-in for `recall_send.send`: refuses `refusals` times, then takes."""

    def __init__(self, refusals=0, *, on_send=None):
        self.refusals = refusals
        self.sent = []
        self.on_send = on_send

    def __call__(self, session_id, text):
        line = recalls.find_line(SESSION, self.recall_id)
        self.sent.append((text, line["state"], line["kept"]))
        if self.on_send:
            self.on_send()
        if len(self.sent) <= self.refusals:
            return recall_send.Sent(taken=False, reason="unavailable: daemon down")
        return recall_send.Sent(taken=True, message_id="m-1")


def ended(answer):
    return Ended(turn_id=None, outcome="completed", answer=answer)


def a_recall(settings=None, **fields):
    record = recall_start.begin(SESSION, "claude", start="prompt", text="ingest lock",
                                senders=(), settings=dict(recall_start.default_settings(),
                                                          **(settings or {})),
                                cwd=None, notes_dir=None, route_name=ROUTE,
                                spawn=lambda s, r: None)
    recall = recall_child.Recall(records.read(SESSION, record["recall"]))
    recall_child.open_line(recall)
    if fields:
        recall.put(**fields)
    return recall


def run(recall, route, sender, sleeps=None, **kwargs):
    sender.recall_id = recall.recall_id
    sleeps = [] if sleeps is None else sleeps
    return recall_stream.run(recall, route, "the brief", send=sender, sleep=sleeps.append,
                             **kwargs)


def test_an_answer_goes_on_the_line_sending_then_is_acked_then_sent():
    recall = a_recall({"max_rounds": 1})
    route, sender = Route([ended("- a\nEnd of recall.")]), Sender()
    assert run(recall, route, sender) == "ended"
    rid = recall.recall_id
    assert route.acked == [(f"{rid}:1", "sending", True)]
    assert sender.sent == [("- a\nEnd of recall.", "sending", False)]
    line = recalls.find_line(SESSION, rid)
    assert line["state"] == "back" and line["message_id"] == "m-1"
    assert line["sent_at"] is not None
    assert records.read(SESSION, rid)["state"] == "ended"


def test_the_round_is_waited_for_its_bound_only_on_a_comms_route():
    recall = a_recall({"max_rounds": 1})
    route = Route([ended("- a\nEnd of recall.")])
    run(recall, route, Sender())
    assert route.waits == [recall_stream.round_bound_s(recall.settings)]


def test_a_refused_send_is_tried_again_and_its_reason_kept():
    recall = a_recall({"max_rounds": 1})
    seen = []
    sender = Sender(2, on_send=lambda: seen.append(
        recalls.find_line(SESSION, recall.recall_id).get("send_reason")))
    sleeps = []
    run(recall, Route([ended("- a\nEnd of recall.")]), sender, sleeps)
    assert len(sender.sent) == 3
    assert seen == [None, "unavailable: daemon down", "unavailable: daemon down"]
    assert sleeps == [10, 10]
    line = recalls.find_line(SESSION, recall.recall_id)
    assert line["state"] == "back" and line["send_tries"] == 2


def test_tries_go_every_ten_seconds_for_a_minute_then_every_five_minutes():
    recall = a_recall()
    now = [1000.0]
    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)
        now[0] += seconds

    sender = Sender(9)
    sender.recall_id = recall.recall_id
    with recalls.session_lock(SESSION):
        recall.write(state=recalls.SENDING, rounds=[{"round": 1, "text": "- a"}])
    assert recall_stream.deliver(recall, send=sender, sleep=sleep,
                                 clock=lambda: now[0]) == "back"
    assert sleeps == [10] * 6 + [300] * 3


def test_nothing_sends_nothing_and_ends_the_record_back():
    recall = a_recall()
    sender = Sender()
    assert run(recall, Route([ended("NOTHING")]), sender) == "ended"
    assert sender.sent == []
    line = recalls.find_line(SESSION, recall.recall_id)
    assert line["state"] == "back" and line["kept"] is False
    assert records.read(SESSION, recall.recall_id)["answered"] is True


def test_a_discard_while_sending_stops_the_tries():
    recall = a_recall()

    def discard():
        with recalls.session_lock(SESSION):
            line = recalls.find_line(SESSION, recall.recall_id)
            line["state"] = recalls.DISCARDED
            recalls.put_line(SESSION, line)

    sender = Sender(5, on_send=discard)
    assert run(recall, Route([ended("- a")]), sender) == "discarded"
    assert len(sender.sent) == 1
    assert recalls.find_line(SESSION, recall.recall_id)["state"] == "discarded"


def test_a_try_taken_while_a_discard_was_made_is_noted_not_undone():
    recall = a_recall()

    def discard():
        with recalls.session_lock(SESSION):
            line = recalls.find_line(SESSION, recall.recall_id)
            line["state"] = recalls.DISCARDED
            recalls.put_line(SESSION, line)

    run(recall, Route([ended("- a")]), Sender(0, on_send=discard))
    line = recalls.find_line(SESSION, recall.recall_id)
    assert line["state"] == "discarded" and line["message_id"] == "m-1"


def test_a_first_round_past_its_bound_then_a_late_text_is_sent_record_left_lost():
    recall = a_recall()
    route = Route([TimeoutError(), ended("- late point")])
    sender = Sender()
    assert run(recall, route, sender) == "lost"
    rid = recall.recall_id
    assert route.waits[1] == recall_stream.LATE_WAIT_S
    assert route.acked == [(f"{rid}:1", "sending", True)]
    line = recalls.find_line(SESSION, rid)
    assert line["state"] == "back" and line["past_bound"] is True
    assert line["rounds"][-1]["late"] is True and line["kept"] is False
    assert sender.sent == [("- late point", "sending", False)]
    record = records.read(SESSION, rid)
    assert record["state"] == "lost" and record["reason"] == "gone"


def test_a_later_round_past_its_bound_with_no_late_text_keeps_the_bounds_state():
    recall = a_recall()
    route = Route([ended("- a"), TimeoutError(), ended("NOTHING")])
    sender = Sender()
    assert run(recall, route, sender) == "ended"
    rid = recall.recall_id
    assert route.acked[-1] == (f"{rid}:2", "back", True)
    line = recalls.find_line(SESSION, rid)
    assert line["state"] == "back" and line["kept"] is False
    assert line["rounds"][-1]["lost"] == "gone"
    assert len(sender.sent) == 1
    assert records.read(SESSION, rid)["state"] == "ended"


def test_the_late_waits_last_limit_leaves_the_line_kept_for_a_taker():
    recall = a_recall()
    route = Route([TimeoutError(), TimeoutError()])
    assert run(recall, route, Sender()) == "unsettled"
    line = recalls.find_line(SESSION, recall.recall_id)
    assert line["state"] == "lost" and line["kept"] is True and line["past_bound"] is True
    assert route.acked == []
    assert records.read(SESSION, recall.recall_id)["state"] == "lost"


def test_a_taker_sends_a_sending_line_whose_child_died():
    recall = a_recall(child=DEAD, state=recalls.SENDING, kept=True,
                      rounds=[{"round": 1, "text": "- a"}])
    recall.put(ref=f"{recall.recall_id}:1")
    sender = Sender(1)
    sender.recall_id = recall.recall_id
    route = Route([])
    assert recall_take.take(SESSION, recall.recall_id, identity=DEAD, route=route,
                            send=sender, sleep=lambda s: None) == "settled"
    assert route.acked == [(f"{recall.recall_id}:1", "sending", True)]
    assert [s[0] for s in sender.sent] == ["- a", "- a"]
    assert recalls.find_line(SESSION, recall.recall_id)["state"] == "back"
    assert records.read(SESSION, recall.recall_id)["state"] == "ended"


def test_a_taker_never_only_acks_a_round_its_bound_ended():
    recall = a_recall(child=DEAD, state=recalls.LOST, reason="gone", past_bound=True,
                      ref=None, kept=False)
    rid = recall.recall_id
    recall.put(ref=f"{rid}:1", kept=True, record={"state": records.LOST})
    route = Route([])

    def no_end(ref):
        raise TimeoutError

    assert recall_take.take(SESSION, rid, identity=DEAD, route=route,
                            read_end=no_end) == "left"
    assert route.acked == []
    assert recalls.find_line(SESSION, rid)["kept"] is True


def test_a_taker_sends_a_late_text_it_finds_and_leaves_the_record_as_the_bound_set_it():
    recall = a_recall(child=DEAD, state=recalls.LOST, reason="gone", past_bound=True)
    rid = recall.recall_id
    recall.put(ref=f"{rid}:1", kept=True, record={"state": records.LOST})
    route, sender = Route([]), Sender()
    sender.recall_id = rid
    assert recall_take.take(SESSION, rid, identity=DEAD, route=route,
                            read_end=lambda ref: ended("- late"), send=sender,
                            sleep=lambda s: None) == "settled"
    assert route.acked == [(f"{rid}:1", "sending", True)]
    assert sender.sent == [("- late", "sending", False)]
    assert recalls.find_line(SESSION, rid)["state"] == "back"
    assert records.read(SESSION, rid)["state"] == "lost"


def test_list_shows_a_sending_lines_wait_and_last_refusal():
    line = {"recall": "r1", "state": "sending", "at": 100.0, "sending_at": 100.0,
            "send_tries": 3, "send_reason": "unavailable: daemon down", "rounds": []}
    text = recall_cli.format_line(line, now=190.0)
    assert "sending for 90s, 3 tries refused, the last: unavailable: daemon down" in text

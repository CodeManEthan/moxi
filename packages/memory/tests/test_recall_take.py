"""Taking over a recall's line whose owner is gone: which lines are taken and by whom,
the claim under the session's lock, and what a taker does with a settled-but-kept line,
an open line whose round turns has settled, one it has not, and one turns never had."""

import os
import types

from moxi_turns import Ended, Lost

from moxi_memory import (listener, listeners, recall_child, recall_start, recall_take,
                         recalls, records)

SESSION = "0190b000-0000-7000-8000-000000000002"
OTHER = "0190b000-0000-7000-8000-000000000003"
#: An identity no live process has: this pid with a start time it never had.
DEAD = {"pid": os.getpid(), "started": 1.0}
LIVE = {"pid": os.getpid(), "started": None}


def live():
    return recall_take.me()


class AckRoute:
    name = "subagent"

    def __init__(self, session_id=SESSION):
        self.session_id = session_id
        self.acked = []

    def ack(self, ref):
        recall_id = ref.rsplit(":", 1)[0]
        line = recalls.find_line(self.session_id, recall_id)
        self.acked.append((ref, line["state"], line["kept"]))


def a_line(session_id=SESSION, *, child=DEAD, **fields):
    """A recall's record and line, the line written with `fields` and child `child`."""
    record = recall_start.begin(session_id, "claude", start="prompt", text="ingest lock",
                                senders=(), settings=recall_start.default_settings(),
                                cwd=None, notes_dir=None, route_name="subagent",
                                spawn=lambda s, r: None)
    recall = recall_child.Recall(records.read(session_id, record["recall"]))
    with recalls.session_lock(session_id):
        recall.write(child=child, **fields)
    return recall.recall_id


def ended(answer):
    return Ended(turn_id=None, outcome="completed", answer=answer)


def ends(value):
    calls = []

    def read(ref):
        calls.append(ref)
        if isinstance(value, BaseException):
            raise value
        return value

    read.calls = calls
    return read


def test_a_line_whose_child_lives_is_not_taken():
    rid = a_line(child=live(), ref="x:1", kept=True)
    read = ends(ended("- a"))
    assert recall_take.take(SESSION, rid, identity=LIVE, read_end=read,
                            route=AckRoute()) == "none"
    assert read.calls == []
    assert recalls.find_line(SESSION, rid).get("taker") is None


def test_an_open_line_whose_round_turns_settled_is_finished_by_the_taker():
    rid = a_line(ref=None, kept=False)
    with recalls.session_lock(SESSION):
        line = recalls.find_line(SESSION, rid)
        line.update(ref=f"{rid}:1", kept=True)
        recalls.put_line(SESSION, line)
    route = AckRoute()
    read = ends(ended("Recalled ...:\n- a"))
    me = live()
    assert recall_take.take(SESSION, rid, identity=me, read_end=read, route=route) == "settled"
    assert read.calls == [f"{rid}:1"]
    # the line's state is written before the ack, with the mark still on
    assert route.acked == [(f"{rid}:1", "back", True)]
    line = recalls.find_line(SESSION, rid)
    assert line["state"] == "back" and line["kept"] is False
    assert line["rounds"][0]["text"] == "Recalled ...:\n- a"
    assert line["taker"] == me and line["child"] == DEAD
    record = records.read(SESSION, rid)
    assert record["state"] == "ended" and record["answered"] is True


def test_a_round_turns_has_not_settled_is_left_kept_for_the_next_taker():
    rid = a_line(ref="r", kept=True)
    with recalls.session_lock(SESSION):
        line = recalls.find_line(SESSION, rid)
        line["ref"] = f"{rid}:1"
        recalls.put_line(SESSION, line)
    route = AckRoute()
    me = live()
    assert recall_take.take(SESSION, rid, identity=me, read_end=ends(TimeoutError()),
                            route=route) == "left"
    line = recalls.find_line(SESSION, rid)
    assert line["state"] == "open" and line["kept"] is True and line["taker"] == me
    assert route.acked == []
    # a taker that lives holds it; once it has exited the next one takes it
    assert recall_take.take(SESSION, rid, identity=DEAD, read_end=ends(ended("- a")),
                            route=route) == "none"
    with recalls.session_lock(SESSION):
        line["taker"] = DEAD
        recalls.put_line(SESSION, line)
    assert recall_take.take(SESSION, rid, identity=me, read_end=ends(ended("- a")),
                            route=route) == "settled"


def test_a_first_round_turns_never_had_loses_the_recall_gone():
    rid = a_line(kept=True)
    with recalls.session_lock(SESSION):
        line = recalls.find_line(SESSION, rid)
        line["ref"] = f"{rid}:1"
        recalls.put_line(SESSION, line)
    route = AckRoute()
    end = Lost(f"{recall_take.UNKNOWN_DELIVERY}: no live endpoint, and no kept end carries this ref")
    assert recall_take.take(SESSION, rid, identity=live(), read_end=ends(end),
                            route=route) == "settled"
    line = recalls.find_line(SESSION, rid)
    assert line["state"] == "lost" and line["reason"] == "gone" and line["kept"] is False
    assert route.acked == [(f"{rid}:1", "lost", True)]
    record = records.read(SESSION, rid)
    assert record["state"] == "lost" and record["reason"] == "gone"


def test_a_later_round_turns_never_had_ends_the_stream_back():
    rid = a_line(kept=True, state="open",
                 rounds=[{"round": 1, "text": "- a", "at": 1.0}])
    with recalls.session_lock(SESSION):
        line = recalls.find_line(SESSION, rid)
        line["ref"] = f"{rid}:2"
        recalls.put_line(SESSION, line)
        records.update(SESSION, rid, state="back", answered=True)
    end = Lost(f"{recall_take.UNKNOWN_DELIVERY}: no delivery at this endpoint carries this ref")
    assert recall_take.take(SESSION, rid, identity=live(), read_end=ends(end),
                            route=AckRoute()) == "settled"
    line = recalls.find_line(SESSION, rid)
    assert line["state"] == "back" and line["kept"] is False
    assert line["rounds"][-1]["round"] == 2 and line["rounds"][-1]["lost"] == "gone"
    assert records.read(SESSION, rid)["state"] == "ended"


def test_a_settled_line_still_kept_is_only_acked():
    rid = a_line(state="back", kept=True,
                 rounds=[{"round": 1, "text": "- a", "at": 1.0}])
    with recalls.session_lock(SESSION):
        line = recalls.find_line(SESSION, rid)
        line["ref"] = f"{rid}:1"
        recalls.put_line(SESSION, line)
    route = AckRoute()
    read = ends(ended("- a"))
    assert recall_take.take(SESSION, rid, identity=live(), read_end=read,
                            route=route) == "acked"
    assert read.calls == []
    assert route.acked == [(f"{rid}:1", "back", True)]
    line = recalls.find_line(SESSION, rid)
    assert line["state"] == "back" and line["kept"] is False
    assert line["rounds"] == [{"round": 1, "text": "- a", "at": 1.0}]


def test_an_ack_turns_refuses_leaves_the_mark():
    rid = a_line(state="lost", kept=True, ref="r")

    class Refusing(AckRoute):
        def ack(self, ref):
            raise RuntimeError("the endpoint did not answer")

    with recalls.session_lock(SESSION):
        line = recalls.find_line(SESSION, rid)
        line["ref"] = f"{rid}:1"
        recalls.put_line(SESSION, line)
    assert recall_take.take(SESSION, rid, identity=live(), route=Refusing()) == "left"
    assert recalls.find_line(SESSION, rid)["kept"] is True


def test_an_open_line_its_child_left_before_any_round_is_lost_gone():
    rid = a_line()
    assert recall_take.take(SESSION, rid, identity=live(), read_end=ends(ended("- a")),
                            route=AckRoute()) == "settled"
    line = recalls.find_line(SESSION, rid)
    assert line["state"] == "lost" and line["reason"] == "gone"
    assert records.read(SESSION, rid)["state"] == "lost"


def test_a_line_with_no_owner_named_is_taken():
    rid = a_line(child=None)
    assert recall_take.owner_gone(recalls.find_line(SESSION, rid))
    assert recall_take.take(SESSION, rid, identity=live(), route=AckRoute()) == "settled"


def test_lines_that_are_done_are_never_taken():
    rid = a_line(state="back", kept=False)
    assert not recall_take.takeable(recalls.find_line(SESSION, rid))
    recalls.note_off(SESSION, recall_start.NO_FORK)
    assert recall_take.take_all(sessions=[SESSION], identity=live()) == {}


def test_take_all_takes_every_session_s_lines_but_the_one_it_skips():
    own = a_line(SESSION)
    other = a_line(OTHER)
    third = a_line(OTHER)
    done = recall_take.take_all(identity=live(), skip=third, read_end=ends(ended("- a")),
                                route=AckRoute())
    assert done == {own: "settled", other: "settled"}
    assert recalls.find_line(OTHER, third)["state"] == "open"


def test_a_settled_round_of_a_discarded_line_keeps_its_state():
    from moxi_memory import recall_stream

    rid = a_line(child=live(), kept=True, ref="r")
    with recalls.session_lock(SESSION):
        line = recalls.find_line(SESSION, rid)
        line.update(ref=f"{rid}:1", state="discarded")
        recalls.put_line(SESSION, line)
    recall = recall_child.Recall(records.read(SESSION, rid), line=line)
    route = AckRoute()
    assert recall_stream.settle(recall, 1, ended("- a"), route) is False
    line = recalls.find_line(SESSION, rid)
    assert line["state"] == "discarded" and line["kept"] is False and line["rounds"] == []
    assert route.acked == [(f"{rid}:1", "discarded", True)]


def test_the_gone_listener_starts_a_taker_only_where_a_line_waits(monkeypatch):
    started = []
    monkeypatch.setattr(recall_take, "start_taker", started.append)
    assert (("gone",), "moxi_memory.listener:subconscious_gone") in listeners.LISTENERS

    def gone(env):
        return types.SimpleNamespace(event="gone", session_id=SESSION, env=env,
                                     harness="claude")

    on = {recall_start.SUBCONSCIOUS_ENV: "automatic"}
    assert listener.subconscious_gone(gone(on)) is None
    assert started == []  # no line yet
    a_line(child=live())
    listener.subconscious_gone(gone({}))
    assert started == []  # the subconscious is off in this session
    listener.subconscious_gone(gone(on))
    assert started == [SESSION]


def test_the_gone_listener_never_raises(monkeypatch):
    def boom(session_id):
        raise OSError("no script")

    monkeypatch.setattr(recall_take, "start_taker", boom)
    a_line(child=live())
    event = types.SimpleNamespace(event="gone", session_id=SESSION, harness="claude",
                                  env={recall_start.SUBCONSCIOUS_ENV: "on-demand"})
    assert listener.subconscious_gone(event) is None


def test_the_child_s_take_mode(monkeypatch):
    taken = []
    monkeypatch.setattr(recall_take, "take_all", lambda **kw: taken.append(kw) or {})
    assert recall_child.main(["--take", "--session", SESSION]) == 0
    assert taken == [{"sessions": [SESSION], "skip": None}]
    assert recall_child.main(["--session", SESSION]) == 2
    assert recall_child.main(["--take", "--session", SESSION, "--recall", "abc"]) == 2
    assert recall_child.main(["--take", "--session", "../x"]) == 2

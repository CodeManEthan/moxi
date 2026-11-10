"""What a question record says: what it holds up, widening and narrowing, the stages a
log names, and the state a parked question reads as now."""

from __future__ import annotations

import pytest

from moxi_rounds import index, log, opening, questions, state
from moxi_rounds.errors import BadArgument

AT = "2026-01-02T03:04:05Z"


def event(n, name, **fields):
    return {"v": 1, "n": n, "at": AT, "actor": "planner", "type": name, **fields}


def asked(n, **more):
    fields = {"id": "Q1", "text": "Is it so?", "from": "comms:12", "blocks": "all",
              "state": "open", "asked_by": "planner", "asked_at": AT, **more}
    return event(n, "question.asked", **fields)


def parked(n, **more):
    return event(n, "question.changed", id="Q1", state="parked", ruling="C1",
                 record="comms:13", **more)


# -- what it holds up --------------------------------------------------------------------

@pytest.mark.parametrize("text, found", [
    ("all", "all"), ("none", "none"), ("B2", ("B2",)), ("B2,B3", ("B2", "B3")),
    ("B10, B2", ("B10", "B2"))])
def test_what_a_question_holds_up_is_every_stage_none_or_a_list(text, found):
    assert questions.parse_blocks(text) == found
    assert questions.parse_blocks(questions.said(found)) == found


@pytest.mark.parametrize("text", ["", "B", "b2", "B0", "B02", "2", "B2,,B3", "B2;B3",
                                  "B2,B2", "All", "NONE", "all,B2", "B2,none"])
def test_a_list_that_cant_be_read_is_refused_as_an_argument(text):
    with pytest.raises(BadArgument, match="--blocks"):
        questions.parse_blocks(text)


def test_a_list_is_stored_as_a_list_and_a_word_as_the_word():
    assert questions.stored(("B2", "B3")) == ["B2", "B3"]
    assert questions.stored("all") == "all" and questions.stored("none") == "none"


@pytest.mark.parametrize("new, old, holds", [
    ("all", "all", True), ("all", ("B1",), True), ("all", "none", True),
    (("B1", "B2"), ("B1",), True), (("B2", "B1"), ("B1", "B2"), True),
    (("B1",), "none", True), ("none", "none", True),
    (("B1",), "all", False), (("B1", "B2", "B3"), "all", False),
    (("B1",), ("B1", "B2"), False), (("B3",), ("B1",), False),
    ("none", "all", False), ("none", ("B1",), False)])
def test_a_value_holds_every_stage_another_does_or_it_does_not(new, old, holds):
    assert questions.holds_every(new, old) is holds


# -- reading a state ---------------------------------------------------------------------

def test_a_question_is_found_by_its_id():
    found = state.fold([asked(1), asked(2, id="Q2", text="And this?")])

    assert questions.find(found, "Q2").text == "And this?"
    assert questions.find(found, "Q3") is None


def test_the_next_id_is_one_past_the_highest_of_its_letter():
    found = state.fold([asked(1, id="Q4"), asked(2, id="P9"), asked(3, id="Q2")])

    assert questions.next_id("Q", found) == "Q5"
    assert questions.next_id("P", found) == "P10"
    assert questions.next_id("Q", state.State()) == "Q1"


def test_a_rounds_stages_are_the_ones_its_log_declared():
    found = state.fold([event(1, "stage.declared", id="B2", repo="proj-a"),
                        event(2, "stage.declared", id="B1", repo="proj-b"),
                        event(3, "finish.done", stage="B1", tip="0" * 40)])

    assert questions.recorded_stages(found) == ("B2", "B1")
    assert questions.finished_stages(found) == {"B1"}
    assert questions.unrecorded(("B1", "B7", "B2", "B9"), found) == ("B7", "B9")
    assert questions.unrecorded("all", found) == ()
    assert questions.unrecorded("none", found) == ()
    assert questions.recorded_stages(state.State()) == ()


# -- the state as it reads now -----------------------------------------------------------

def test_a_question_reads_as_recorded_when_it_waits_for_nothing():
    for change in ([], [parked(2)],
                   [event(2, "question.changed", id="Q1", state="ruled")],
                   [event(2, "question.changed", id="Q1", state="withdrawn")]):
        found = state.fold([asked(1), *change])
        [question] = found.questions

        assert questions.current(question, found) == question.state
        assert not questions.reached(question, found)


def test_a_question_parked_until_a_stage_reads_open_once_the_stage_is_finished():
    waiting = [asked(1), event(2, "stage.declared", id="B1"),
               event(3, "stage.declared", id="B2"), parked(4, until="B2")]
    other = state.fold([*waiting, event(5, "finish.done", stage="B1", tip="0" * 40)])
    done = state.fold([*waiting, event(5, "finish.done", stage="B2", tip="0" * 40)])

    assert questions.current(other.questions[0], other) == "parked"
    assert questions.current(done.questions[0], done) == "open"
    # Nothing was written for it: the record still says parked, and until what.
    assert (done.questions[0].state, done.questions[0].until) == ("parked", "B2")


def test_a_question_parked_until_a_round_reads_open_once_that_round_is_closed():
    found = state.fold([asked(1), parked(2, until="round:proj-a/b-round")])
    [question] = found.questions
    seen = []

    def closed(name):
        seen.append(name)
        return name == "proj-a/b-round"

    assert questions.current(question, found, closed=closed) == "open"
    assert questions.current(question, found, closed=lambda name: False) == "parked"
    assert seen == ["proj-a/b-round"]


def test_a_round_is_closed_once_its_log_holds_its_closing(tmp_path, add_project):
    add_project("proj-a", notes_root=tmp_path / "notes")
    opening.open_round("proj-a/b-round", description="a placeholder round",
                       actor="planner", flow="fix")
    found = state.fold([asked(1), parked(2, until="round:proj-a/b-round")])
    [question] = found.questions

    assert not questions.is_closed("proj-a/b-round")
    assert questions.current(question, found) == "parked"

    log.append(log.round_log(tmp_path / "notes" / "rounds" / "b-round"), "round.closed",
               {"numbers": {}}, actor="planner")

    assert questions.is_closed("proj-a/b-round")
    assert questions.current(question, found) == "open"


@pytest.mark.parametrize("name", ["proj-a/never-opened", "proj-b/b-round", "not a name"])
def test_a_round_that_cant_be_found_is_not_closed(tmp_path, add_project, name):
    add_project("proj-a", notes_root=tmp_path / "notes")

    assert not questions.is_closed(name)


# -- what the index keeps ----------------------------------------------------------------

def test_the_index_keeps_the_questions_recorded_as_open_or_parked():
    found = state.fold([
        asked(1), asked(2, id="Q2", blocks=["B1", "B2"]), asked(3, id="Q3"),
        asked(4, id="Q4"),
        event(5, "question.changed", id="Q2", state="parked", until="B2", ruling="C1"),
        event(6, "question.changed", id="Q3", state="ruled", ruling="C2"),
        event(7, "question.changed", id="Q4", state="withdrawn", ruling="C3")])

    assert index.ITEMS["question"] is questions.waiting
    assert list(questions.waiting(found)) == [
        ("Q1", {"text": "Is it so?", "from": "comms:12", "blocks": "all",
                "state": "open", "by": "planner", "at": AT}),
        ("Q2", {"text": "Is it so?", "from": "comms:12", "blocks": ["B1", "B2"],
                "state": "parked", "by": "planner", "at": AT, "until": "B2"})]

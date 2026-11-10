"""The hold rule: which questions hold a stage and which hold the round's close, over
rounds built with the verbs and with the records later verbs will write."""

from __future__ import annotations

import copy

import pytest

from moxi_rounds import asking, holds, log, marking, opening, options, ruling, state

ROUND = "proj-a/a-round"
ASKED = "Do agent-read times follow the settings?"
OTHER = "proj-a/b-round"


@pytest.fixture
def path(tmp_path, add_project):
    """The log of the round `a-round` of `proj-a`, opened with the stages B1, B2 and
    B3 declared."""
    add_project("proj-a", notes_root=tmp_path / "notes")
    opening.open_round(ROUND, description="a placeholder round", actor="planner",
                       flow="fix")
    found = log.round_log(tmp_path / "notes" / "rounds" / "a-round")
    for stage in ("B1", "B2", "B3"):
        declare(found, stage)
    return found


def declare(path, stage):
    log.append(path, "stage.declared",
               {"id": stage, "repo": "proj-a", "branch": f"branch-{stage.lower()}"},
               actor="planner")


def ask(path, blocks=None, **more):
    asking.ask(ROUND, ASKED, origin="comms:11", blocks=blocks, actor="scout", **more)


def rule(path, item, **more):
    ruling.rule(ROUND, item=item, record="comms:12", actor="planner", **more)


def widen(path, question_id, blocks):
    marking.widen(ROUND, question_id, blocks, actor="coder")


def reopen(path, question_id):
    marking.reopen(ROUND, question_id, actor="coder")


def finish(path, stage):
    log.append(path, "finish.done", {"stage": stage, "tip": "0" * 40}, actor="coder")


def remove(path, stage):
    """A stage leaves the plan as a change to the stage records it."""
    log.append(path, "stage.changed", {"id": stage, "removed": True}, actor="planner")


def unstage(path, stage):
    """A stage leaves the plan by the operator's ruling."""
    log.append(path, "ruling.recorded",
               {"id": "C90", "record": "comms:13", "kind": "process",
                "rules": f"unstage:{stage}"}, actor="planner")


def skip(path, stage, *covers, step="docket", ruling_id="C80"):
    """A skip of a step for one stage, listing the questions it lets the stage past."""
    log.append(path, "ruling.recorded",
               {"id": ruling_id, "record": "comms:13", "kind": "process",
                "rules": f"skip:{step}:{stage}", "covers": list(covers)},
               actor="planner")


def offer(path, question_id, *given, retire=()):
    options.fill(ROUND, question_id, actor="helper", options=given, retire=retire)


def held(path, stages=("B1", "B2", "B3"), **more):
    """What holds each stage, and what holds the close, as question ids."""
    found = state.fold(log.read(path))
    on_stages = {stage: [one.id for one in holds.on_stage(found, stage, **more)]
                 for stage in stages}
    return on_stages, [one.id for one in holds.on_close(found)]


EVERY = {"B1": ["Q1"], "B2": ["Q1"], "B3": ["Q1"]}
NO_STAGE = {"B1": [], "B2": [], "B3": []}

#: Each row: what is done to the round, what then holds each stage, and what holds the
#: close.
ROWS = {
    "a round with no question": ([], NO_STAGE, []),
    "one that does not say holds every stage": ([(ask,)], EVERY, ["Q1"]),
    "one that says every stage holds every stage": ([(ask, "all")], EVERY, ["Q1"]),
    "one that lists stages holds those only": (
        [(ask, "B1,B3")], {"B1": ["Q1"], "B2": [], "B3": ["Q1"]}, ["Q1"]),
    "one that holds up none holds no stage and holds the close": (
        [(ask, "none")], NO_STAGE, ["Q1"]),
    "a parked one holds no stage and holds the close": (
        [(ask,), (rule, "park:Q1")], NO_STAGE, ["Q1"]),
    "one parked until a stage holds nothing while it waits": (
        [(ask, "B1,B2"), (rule, "park:Q1", {"until": "B3"})], NO_STAGE, ["Q1"]),
    "one parked until a stage holds its stages again once that stage is finished": (
        [(ask, "B1,B2"), (rule, "park:Q1", {"until": "B3"}), (finish, "B3")],
        {"B1": ["Q1"], "B2": ["Q1"], "B3": []}, ["Q1"]),
    "one parked until a stage that left the plan stays parked": (
        [(ask, "B1,B2"), (rule, "park:Q1", {"until": "B3"}), (remove, "B3")],
        NO_STAGE, ["Q1"]),
    "a reopened one holds what it held": (
        [(ask, "B2"), (rule, "park:Q1"), (reopen, "Q1")],
        {"B1": [], "B2": ["Q1"], "B3": []}, ["Q1"]),
    "a ruled one holds nothing": (
        [(ask,), (rule, "Q1", {"text": "They do."})], NO_STAGE, []),
    "a withdrawn one holds nothing": ([(ask,), (rule, "withdraw:Q1")], NO_STAGE, []),
    "a parked one that was then ruled holds nothing": (
        [(ask,), (rule, "park:Q1", {"until": "B3"}), (rule, "Q1", {"text": "They do."}),
         (finish, "B3")], NO_STAGE, []),
    "a widening adds holds": (
        [(ask, "B1"), (widen, "Q1", "B1,B2")],
        {"B1": ["Q1"], "B2": ["Q1"], "B3": []}, ["Q1"]),
    "a widening to every stage holds every stage": (
        [(ask, "B1"), (widen, "Q1", "all")], EVERY, ["Q1"]),
    "a widening of a parked one adds holds once it reads open": (
        [(ask, "B1"), (rule, "park:Q1", {"until": "B3"}), (widen, "Q1", "B1,B2"),
         (finish, "B3")], {"B1": ["Q1"], "B2": ["Q1"], "B3": []}, ["Q1"]),
    "a narrowing ends holds": (
        [(ask,), (rule, "narrow:Q1=B2")], {"B1": [], "B2": ["Q1"], "B3": []}, ["Q1"]),
    "a narrowing to none leaves only the close held": (
        [(ask, "B1,B2"), (rule, "narrow:Q1=none")], NO_STAGE, ["Q1"]),
    "a finished stage is stopped by nothing": (
        [(ask,), (ask, "B2"), (finish, "B2")],
        {"B1": ["Q1"], "B2": [], "B3": ["Q1"]}, ["Q1", "Q2"]),
    "a stage that left the plan is stopped by nothing": (
        [(ask,), (ask, "B2"), (remove, "B2")],
        {"B1": ["Q1"], "B2": [], "B3": ["Q1"]}, ["Q1", "Q2"]),
    "a stage a ruling took out of the plan is stopped by nothing": (
        [(ask,), (ask, "B2"), (unstage, "B2")],
        {"B1": ["Q1"], "B2": [], "B3": ["Q1"]}, ["Q1", "Q2"]),
    "a question asked of a finished stage does not hold it": (
        [(finish, "B2"), (ask,)], {"B1": ["Q1"], "B2": [], "B3": ["Q1"]}, ["Q1"]),
    "a skip lets its stage past the questions it lists, and no other stage": (
        [(ask,), (ask, "B2,B3"), (skip, "B2", "Q1", "Q2")],
        {"B1": ["Q1"], "B2": [], "B3": ["Q1", "Q2"]}, ["Q1", "Q2"]),
    "a question asked after a skip holds the stage": (
        [(ask,), (skip, "B2", "Q1"), (ask,), (ask, "B2")],
        {"B1": ["Q1", "Q2"], "B2": ["Q2", "Q3"], "B3": ["Q1", "Q2"]},
        ["Q1", "Q2", "Q3"]),
    "a question widened onto a stage after its skip holds the stage": (
        [(ask,), (ask, "B1"), (skip, "B2", "Q1"), (widen, "Q2", "B1,B2")],
        {"B1": ["Q1", "Q2"], "B2": ["Q2"], "B3": ["Q1"]}, ["Q1", "Q2"]),
    "a skip that lists no question lets its stage past none": (
        [(ask,), (skip, "B2")], EVERY, ["Q1"]),
    "a skip of another step lets no stage past a question": (
        [(ask,), (skip, "B2", "Q1", {"step": "check"})], EVERY, ["Q1"]),
    "two skips of one stage each let it past what they list": (
        [(ask,), (skip, "B2", "Q1"), (ask,), (skip, "B2", "Q2", {"ruling_id": "C81"}),
         (ask,)],
        {"B1": ["Q1", "Q2", "Q3"], "B2": ["Q3"], "B3": ["Q1", "Q2", "Q3"]},
        ["Q1", "Q2", "Q3"]),
    "questions hold in the order they were asked": (
        [(ask, "B2"), (ask, "none"), (ask,), (ask, "B1,B2"), (rule, "park:Q3")],
        {"B1": ["Q4"], "B2": ["Q1", "Q4"], "B3": []}, ["Q1", "Q2", "Q3", "Q4"]),
}


def build(path, steps):
    for act, *given in steps:
        more = given.pop() if given and isinstance(given[-1], dict) else {}
        act(path, *given, **more)


@pytest.mark.parametrize("row", ROWS)
def test_what_holds_each_stage_and_the_close(path, row):
    steps, on_stages, on_close = ROWS[row]

    build(path, steps)

    assert held(path) == (on_stages, on_close)


def test_one_that_holds_every_stage_holds_a_stage_declared_after_it_was_asked(path):
    ask(path)
    ask(path, "B1")

    declare(path, "B4")

    on_stages, on_close = held(path, stages=("B1", "B4"))
    assert on_stages == {"B1": ["Q1", "Q2"], "B4": ["Q1"]}
    assert on_close == ["Q1", "Q2"]


def test_one_parked_until_a_round_holds_its_stages_again_once_that_round_is_closed(
        path):
    opening.open_round(OTHER, description="a placeholder round", actor="planner",
                       flow="fix")
    ask(path, "B1")
    rule(path, "park:Q1", until=f"round:{OTHER}")
    seen = []

    def closed(name):
        seen.append(name)
        return True

    assert held(path) == (NO_STAGE, ["Q1"])
    assert held(path, closed=closed) == ({"B1": ["Q1"], "B2": [], "B3": []}, ["Q1"])
    assert set(seen) == {OTHER}

    log.append(path.parent.parent / "b-round" / "round.jsonl", "round.closed",
               {"numbers": {}}, actor="planner")

    assert held(path) == ({"B1": ["Q1"], "B2": [], "B3": []}, ["Q1"])


@pytest.mark.parametrize("steps", [
    [(ask,)], [(ask, "B1,B2")], [(ask, "none")],
    [(ask,), (rule, "park:Q1", {"until": "B3"})],
    [(ask,), (rule, "park:Q1", {"until": "B3"}), (finish, "B3")],
    [(ask,), (skip, "B2", "Q1")]])
def test_options_on_a_question_change_nothing_it_holds(path, steps):
    build(path, steps)
    before = held(path)

    offer(path, "Q1", "a=prose yes, parsed rows no", "b=everything", "c=nothing")
    assert held(path) == before
    offer(path, "Q1", "d=only the times", retire=("c",))
    assert held(path) == before
    offer(path, "Q1", retire=("a",))

    assert held(path) == before
    assert before[1] == ["Q1"]


def test_a_question_asked_with_options_holds_as_one_asked_bare(path):
    ask(path, "B2", options=("a=prose yes, parsed rows no", "b=everything"))
    ask(path, "B2")

    assert held(path) == ({"B1": [], "B2": ["Q1", "Q2"], "B3": []}, ["Q1", "Q2"])


def test_an_answer_that_names_an_option_ends_the_hold_as_any_answer(path):
    ask(path, options=("a=prose yes, parsed rows no", "b=everything"))

    rule(path, "Q1", chose="b")

    assert held(path) == (NO_STAGE, [])


def test_a_projects_question_holds_no_stage(path):
    asking.ask("proj-a", ASKED, origin="comms:11", actor="scout")
    found = state.fold(log.read(log.project_log(path.parent.parent)))

    assert [one.id for one in found.questions] == ["P1"]
    assert [holds.on_stage(found, stage) for stage in ("B1", "B2")] == [(), ()]


def test_what_holds_is_given_as_the_questions_themselves(path):
    ask(path, "B2")
    found = state.fold(log.read(path))

    assert holds.on_stage(found, "B2") == (found.questions[0],)
    assert holds.on_close(found) == (found.questions[0],)
    assert holds.on_stage(found, "B1") == ()


def test_reading_what_holds_changes_nothing_and_reads_the_same_twice(path):
    build(path, [(ask,), (ask, "B2"), (rule, "park:Q2", {"until": "B1"}),
                 (skip, "B3", "Q1"), (finish, "B1")])
    found = state.fold(log.read(path))
    before = copy.deepcopy(found)
    bytes_before = path.read_bytes()

    first = [holds.on_stage(found, stage) for stage in ("B1", "B2", "B3")]
    second = [holds.on_stage(found, stage) for stage in ("B1", "B2", "B3")]

    assert first == second == [(), tuple(found.questions), ()]
    assert holds.on_close(found) == tuple(found.questions)
    assert found == before and path.read_bytes() == bytes_before


def test_the_questions_a_skip_lets_a_stage_past_are_the_ones_it_lists(path):
    build(path, [(ask,), (ask,), (skip, "B2", "Q1"),
                 (skip, "B3", "Q2", {"ruling_id": "C81"})])
    found = state.fold(log.read(path))

    assert holds.skipped(found, "B2") == {"Q1"}
    assert holds.skipped(found, "B3") == {"Q2"}
    assert holds.skipped(found, "B1") == frozenset()
    assert [one.covers for one in found.rulings] == [("Q1",), ("Q2",)]


@pytest.mark.parametrize("names", ["revokes", "supersedes"])
def test_a_skip_that_was_taken_back_or_replaced_lets_its_stage_past_nothing(
        path, names):
    build(path, [(ask,), (skip, "B2", "Q1")])
    assert held(path)[0] == {"B1": ["Q1"], "B2": [], "B3": ["Q1"]}

    log.append(path, "ruling.recorded",
               {"id": "C81", "record": "comms:14", "kind": "process", names: "C80"},
               actor="planner")

    found = state.fold(log.read(path))
    assert holds.skipped(found, "B2") == frozenset()
    assert held(path)[0] == EVERY

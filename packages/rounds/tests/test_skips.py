"""Skips: the operator's ruling that a step is not done, what may be skipped and what
may not, and which questions a skip of the questions step lets a stage past."""

from __future__ import annotations

import pytest

from moxi_rounds import (asking, holds, log, marking, opening, ruling, showing, skips,
                         state, stepping, write)
from moxi_rounds.cli import main
from moxi_rounds.errors import BadArgument, Refused

ROUND = "proj-a/a-round"
ASKED = "Do agent-read times follow the settings?"
STAGES = ("B1", "B2", "B3")

#: A flow with every step that may be skipped, one of the user's own among them.
MINE = """\
[flows.mine]
steps = [
  { step = "design", by = "planner" },
  { step = "verify-design", by = "reviewer" },
  { step = "build", by = "coder" },
  { step = "security-review", does = "report", before = "finish", by = "critic" },
  { step = "verify-build", by = "reviewer" },
  { step = "finish", by = "coder" },
  { step = "retro", by = "helper" },
]
"""

#: Each step that may be skipped, as the item that skips it.
SKIPPABLE = ["skip:verify-design", "skip:ratify", "skip:retro", "skip:docket:B2",
             "skip:check:B2", "skip:verify-build:B2", "skip:security-review:B2"]


@pytest.fixture
def path(tmp_path, add_project, write_config, capsys):
    """The log of `a-round` of `proj-a`, opened on a flow that has every step that may
    be skipped, with the stages B1, B2 and B3 declared."""
    write_config(MINE)
    add_project("proj-a", notes_root=tmp_path / "notes")
    return open_round(tmp_path, "mine", capsys)


def open_round(tmp_path, flow, capsys):
    opening.open_round(ROUND, description="a placeholder round", actor="planner",
                       flow=flow)
    found = log.round_log(tmp_path / "notes" / "rounds" / "a-round")
    for stage in STAGES:
        log.append(found, "stage.declared",
                   {"id": stage, "repo": "proj-a", "branch": f"branch-{stage.lower()}"},
                   actor="planner")
    capsys.readouterr()
    return found


@pytest.fixture
def light(tmp_path, add_project, capsys):
    """The same round on the lightest flow, which has no design and no retro."""
    add_project("proj-a", notes_root=tmp_path / "notes")
    return open_round(tmp_path, "fix", capsys)


def ask(blocks=None):
    return asking.ask(ROUND, ASKED, origin="comms:11", blocks=blocks, actor="scout").id


def rule(item, record="comms:12", **more):
    return ruling.rule(ROUND, item=item, record=record, actor="planner", **more)


def folded(path):
    return state.fold(log.read(path))


def last(path):
    event = log.read(path)[-1]
    return {key: event[key] for key in event if key not in ("v", "n", "at")}


def held(path):
    """What holds each stage, as question ids."""
    found = folded(path)
    return {stage: [one.id for one in holds.on_stage(found, stage)] for stage in STAGES}


def parts(item):
    step, _colon, stage = item[len("skip:"):].partition(":")
    return step, stage or None


# -- what may be skipped -----------------------------------------------------------------

@pytest.mark.parametrize("item", SKIPPABLE)
def test_each_step_that_may_be_skipped_is_skipped_by_one_ruling(path, item):
    step, stage = parts(item)
    count = len(log.read(path))
    assert not skips.is_skipped(folded(path), step, stage)

    ruled = rule(item)

    assert len(log.read(path)) == count + 1
    event = last(path)
    assert {key: event[key] for key in event if key != "covers"} == {
        "actor": "planner", "type": "ruling.recorded", "id": "C1", "record": "comms:12",
        "kind": "process", "rules": item}
    assert ruled.id == "C1"
    found = folded(path)
    assert skips.is_skipped(found, step, stage)
    assert [(one.id, one.step, one.stage) for one in skips.live(found)] == [
        ("C1", step, stage)]


@pytest.mark.parametrize("item", ["skip:check:B2", "skip:verify-build:B2",
                                  "skip:security-review:B2", "skip:docket:B2"])
def test_a_step_skipped_for_one_stage_is_not_skipped_for_another(path, item):
    step, stage = parts(item)

    rule(item)

    found = folded(path)
    assert not skips.is_skipped(found, step, "B1")
    assert not skips.is_skipped(found, step)
    assert not skips.is_skipped(found, "retro")


@pytest.mark.parametrize("item", ["skip:design", "skip:commission:B2", "skip:build:B2",
                                  "skip:finish:B2", "skip:commission", "skip:finish"])
def test_a_step_that_is_never_skipped_is_refused(path, item):
    before = path.read_bytes()

    with pytest.raises(Refused, match=f"{parts(item)[0]} is never skipped"):
        rule(item)

    assert path.read_bytes() == before
    assert main(["rule", ROUND, item, "--record", "comms:12"]) == 3


@pytest.mark.parametrize("item, step", [
    ("skip:retro", "retro"), ("skip:verify-design", "verify-design"),
    ("skip:security-review:B2", "security-review"), ("skip:no-such-step:B2",
                                                      "no-such-step"),
    ("skip:no-such-step", "no-such-step")])
def test_a_step_the_rounds_flow_does_not_have_is_refused(light, item, step):
    before = light.read_bytes()

    with pytest.raises(Refused) as refused:
        rule(item)

    assert (f"the flow fix of {ROUND} has no {step} step (it lists build, "
            "verify-build, finish)") in str(refused.value)
    assert light.read_bytes() == before


@pytest.mark.parametrize("item", ["skip:docket:B2", "skip:check:B2", "skip:ratify",
                                  "skip:verify-build:B2"])
def test_the_lightest_flow_takes_a_skip_of_the_steps_it_has_and_of_the_questions_step(
        light, item):
    rule(item)

    assert skips.is_skipped(folded(light), *parts(item))


def test_a_skip_without_his_pointer_is_refused(path):
    before = path.read_bytes()

    with pytest.raises(Refused, match="needs a pointer to where he said it"):
        rule("skip:retro", record=None)
    with pytest.raises(Refused, match="is no pointer|not a pointer|pointer"):
        rule("skip:retro", record="he said so")

    assert path.read_bytes() == before


def test_a_skip_for_a_stage_the_round_does_not_have_is_refused(path):
    before = path.read_bytes()

    with pytest.raises(Refused, match=f"B9 is not a stage of {ROUND}: its stages are "
                                      "B1, B2, B3"):
        rule("skip:check:B9")

    assert path.read_bytes() == before


def test_a_step_of_the_users_own_is_skipped_for_one_stage(path):
    before = path.read_bytes()

    with pytest.raises(Refused) as refused:
        rule("skip:security-review")

    assert "security-review is done for each build" in str(refused.value)
    assert (f"moxi rounds rule {ROUND} skip:security-review:B<n> --record PTR"
            in str(refused.value))
    assert path.read_bytes() == before


@pytest.mark.parametrize("target, item, more", [
    (ROUND, "skip:", {}), (ROUND, "skip:check", {}), (ROUND, "skip:verify-build", {}),
    (ROUND, "skip:docket", {}), (ROUND, "skip:retro:B1", {}),
    (ROUND, "skip:verify-design:B1", {}), (ROUND, "skip:ratify:B1", {}),
    (ROUND, "skip:check:b2", {}), (ROUND, "skip:check:B2:B3", {}),
    ("proj-a", "skip:retro", {}), (ROUND, "skip:retro", {"stages": "B1"}),
    (ROUND, "skip:retro", {"standing": True}), (ROUND, "skip:retro", {"text": " "}),
    (ROUND, "skip:retro", {"chose": "a"}), (ROUND, "skip:retro", {"until": "B1"})])
def test_a_skip_that_cant_be_read_is_a_usage_error(path, target, item, more):
    before = path.read_bytes()

    with pytest.raises(BadArgument):
        ruling.rule(target, item=item, record="comms:12", actor="planner", **more)

    assert path.read_bytes() == before


def test_a_skip_may_carry_a_sentence_and_a_reason(path):
    rule("skip:retro", text="No retro for this one.", reason="it is a one-line fix")

    assert last(path)["text"] == "No retro for this one."
    assert last(path)["reason"] == "it is a one-line fix"


# -- a second skip, and a skip taken back ------------------------------------------------

def test_a_second_skip_of_a_step_names_the_first_and_replaces_it(path):
    rule("skip:check:B2")
    before = path.read_bytes()

    with pytest.raises(Refused) as refused:
        rule("skip:check:B2", record="comms:13")

    assert "check for B2 is skipped already, by C1" in str(refused.value)
    assert (f"moxi rounds rule {ROUND} skip:check:B2 --record PTR --supersedes C1"
            in str(refused.value))
    assert path.read_bytes() == before
    # The same step for another stage is another thing, and names nothing.
    rule("skip:check:B3", record="comms:13")
    rule("skip:check:B2", record="comms:14", supersedes="C1")
    found = folded(path)
    assert [(one.id, one.replaced_by) for one in found.rulings] == [
        ("C1", "C3"), ("C2", None), ("C3", None)]
    assert [(one.id, one.stage) for one in skips.live(found)] == [("C2", "B3"),
                                                                  ("C3", "B2")]


def test_a_skip_replaces_only_a_skip_of_the_same_step_and_stage(path):
    rule("skip:check:B2")
    before = path.read_bytes()

    with pytest.raises(Refused, match="a ruling replaces an earlier ruling of the "
                                      "same thing"):
        rule("skip:check:B3", record="comms:13", supersedes="C1")

    assert path.read_bytes() == before


@pytest.mark.parametrize("item", SKIPPABLE)
def test_a_skip_taken_back_leaves_its_step_due_again(path, item):
    step, stage = parts(item)
    rule(item)

    rule("revoke:C1", record="comms:13")

    event = last(path)
    assert (event["rules"], event["revokes"], event["kind"]) == (
        "revoke:C1", "C1", "process")
    found = folded(path)
    assert not skips.is_skipped(found, step, stage)
    assert skips.live(found) == ()
    # Taken back, the step is skipped again as if for the first time.
    rule(item, record="comms:14")
    assert skips.is_skipped(folded(path), step, stage)


def test_a_skip_stands_across_a_change_of_flow(path):
    rule("skip:retro")
    rule("skip:check:B2")

    rule("flow:full")

    found = folded(path)
    assert stepping.current(found).name == "full"
    assert skips.is_skipped(found, "retro") and skips.is_skipped(found, "check", "B2")


# -- a skip of the questions step --------------------------------------------------------

def test_a_skip_of_the_questions_step_lists_the_questions_that_hold_its_stage(path):
    ask()
    ask("B2,B3")
    ask("B1")
    ask("none")

    rule("skip:docket:B2")

    assert last(path)["covers"] == ["Q1", "Q2"]
    assert held(path) == {"B1": ["Q1", "Q3"], "B2": [], "B3": ["Q1", "Q2"]}
    # Every one of them still holds the round's close.
    assert [one.id for one in holds.on_close(folded(path))] == ["Q1", "Q2", "Q3", "Q4"]


def test_a_question_asked_after_the_skip_holds_the_stage(path):
    ask()
    rule("skip:docket:B2")

    ask()
    ask("B2")

    assert held(path) == {"B1": ["Q1", "Q2"], "B2": ["Q2", "Q3"], "B3": ["Q1", "Q2"]}


def test_a_question_widened_onto_the_stage_after_the_skip_holds_it(path):
    ask()
    ask("B1")
    rule("skip:docket:B2")

    marking.widen(ROUND, "Q2", "B1,B2", actor="coder")

    assert held(path)["B2"] == ["Q2"]


def test_a_question_parked_when_the_stage_was_skipped_holds_it_once_reopened(path):
    ask()
    rule("park:Q1")
    rule("skip:docket:B2", record="comms:13")
    assert last(path)["covers"] == []

    marking.reopen(ROUND, "Q1", actor="coder")

    assert held(path)["B2"] == ["Q1"]


def test_a_listed_question_narrowed_off_the_stage_and_widened_back_holds_it_again(path):
    ask("B1,B2")
    rule("skip:docket:B2")
    rule("narrow:Q1=B1", record="comms:13")
    assert held(path) == {"B1": ["Q1"], "B2": [], "B3": []}

    marking.widen(ROUND, "Q1", "B1,B2", actor="coder")

    assert held(path) == {"B1": ["Q1"], "B2": ["Q1"], "B3": []}
    found = folded(path)
    assert (found.rulings[0].covers, found.rulings[0].let_go) == (("Q1",), ("Q1",))
    assert [(one.covers, one.let_go) for one in skips.live(found)] == [((), ("Q1",))]
    # His skip still stands, so the stage is let past the question only by a new one
    # that replaces it.
    rule("skip:docket:B2", record="comms:14", supersedes="C1")
    assert held(path)["B2"] == []


def test_a_listed_question_narrowed_to_none_and_widened_back_holds_the_stage_again(path):
    ask()
    rule("skip:docket:B2")
    rule("narrow:Q1=none", record="comms:13")

    marking.widen(ROUND, "Q1", "all", actor="coder")

    assert held(path) == {"B1": ["Q1"], "B2": ["Q1"], "B3": ["Q1"]}


def test_a_listed_question_whose_narrowing_off_the_stage_is_taken_back_holds_it_again(
        path):
    ask("B1,B2")
    rule("skip:docket:B2")
    rule("narrow:Q1=B1", record="comms:13")

    rule("revoke:C2", record="comms:14")

    assert held(path) == {"B1": ["Q1"], "B2": ["Q1"], "B3": []}


def test_a_listed_question_narrowed_to_a_list_that_keeps_the_stage_stays_let_past(path):
    ask()
    rule("skip:docket:B2")

    rule("narrow:Q1=B2,B3", record="comms:13")
    marking.widen(ROUND, "Q1", "all", actor="coder")

    assert held(path) == {"B1": ["Q1"], "B2": [], "B3": ["Q1"]}
    assert folded(path).rulings[0].let_go == ()


def test_a_listed_question_answered_and_opened_again_stays_let_past(path):
    ask()
    rule("skip:docket:B2")
    rule("Q1", record="comms:13", text="They do.")

    rule("revoke:C2", record="comms:14")

    assert held(path) == {"B1": ["Q1"], "B2": [], "B3": ["Q1"]}


def test_a_skip_of_the_questions_step_taken_back_lets_its_stage_past_nothing(path):
    ask()
    rule("skip:docket:B2")
    assert held(path)["B2"] == []

    rule("revoke:C1", record="comms:13")

    assert held(path) == {"B1": ["Q1"], "B2": ["Q1"], "B3": ["Q1"]}
    assert not skips.is_skipped(folded(path), "docket", "B2")


def test_a_skip_that_replaces_one_lists_what_the_first_let_past_and_what_holds_now(path):
    ask()
    rule("skip:docket:B2")
    ask("B2")
    assert held(path)["B2"] == ["Q2"]

    rule("skip:docket:B2", record="comms:13", supersedes="C1")

    assert last(path)["covers"] == ["Q1", "Q2"]
    assert held(path) == {"B1": ["Q1"], "B2": [], "B3": ["Q1"]}


def test_a_skip_of_the_questions_step_for_a_stage_nothing_holds_lists_none(path):
    ask("B1")

    rule("skip:docket:B2")

    assert last(path)["covers"] == []
    ask()
    assert held(path)["B2"] == ["Q2"]


def test_a_skip_of_another_step_lets_no_stage_past_a_question(path):
    ask()

    rule("skip:check:B2")
    rule("skip:verify-design", record="comms:13")

    assert "covers" not in last(path)
    assert held(path) == {"B1": ["Q1"], "B2": ["Q1"], "B3": ["Q1"]}


# -- where a skip shows ------------------------------------------------------------------

def test_the_skip_is_printed_with_what_it_lets_its_stage_past(path, capsys):
    ask()
    capsys.readouterr()

    assert main(["rule", ROUND, "skip:docket:B2", "--record", "comms:12"]) == 0

    assert capsys.readouterr().out.splitlines()[:2] == [
        "Recorded as C1: skip:docket:B2",
        "it lets B2 past Q1; a question asked later, or made to hold up B2 later, "
        "holds it"]


def test_the_skips_that_stand_are_shown_and_are_in_the_note(path, capsys):
    ask()
    rule("skip:docket:B2")
    rule("skip:check:B3", record="comms:13")
    rule("skip:retro", record="comms:14")
    rule("revoke:C3", record="comms:15")
    capsys.readouterr()

    assert main(["show", ROUND]) == 0
    shown = capsys.readouterr().out
    assert "skipped: docket for B2, by C1, past Q1 (record comms:12)\n" in shown
    assert "skipped: check for B3, by C2 (record comms:13)\n" in shown
    assert "skipped: retro" not in shown
    found = showing.data(showing.show(ROUND))["skips"]
    assert [{key: one[key] for key in one if key != "at"} for one in found] == [
        {"id": "C1", "step": "docket", "stage": "B2", "record": "comms:12",
         "covers": ["Q1"], "let_go": []},
        {"id": "C2", "step": "check", "stage": "B3", "record": "comms:13"}]
    note = (path.parent / "round-note.md").read_text(encoding="utf-8")
    assert "- skipped: docket for B2, by C1, past Q1 (record comms:12)\n" in note
    assert "- skipped: check for B3, by C2 (record comms:13)\n" in note
    assert "skipped: retro" not in note


def test_a_skip_that_lets_a_question_past_no_longer_is_shown_as_that(path, capsys):
    ask("B1,B2")
    rule("skip:docket:B2")
    rule("narrow:Q1=B1", record="comms:13")
    capsys.readouterr()

    assert main(["show", ROUND]) == 0

    assert ("skipped: docket for B2, by C1, past no question and no longer past Q1 "
            "(record comms:12)\n") in capsys.readouterr().out


# -- a skip stopped right after its record -----------------------------------------------

@pytest.mark.parametrize("item", SKIPPABLE)
def test_a_skip_stopped_after_its_append_stands_and_is_refused_when_run_again(
        path, monkeypatch, item):
    step, stage = parts(item)
    ask(blocks="B2")
    events_before = len(log.read(path))

    def stop(written):
        raise KeyboardInterrupt

    with monkeypatch.context() as patched:
        patched.setattr(write, "ON_APPEND", (stop,))
        with pytest.raises(KeyboardInterrupt):
            rule(item)

    with pytest.raises(Refused, match="C1"):
        rule(item)
    after = log.read(path)
    assert len(after) == events_before + 1
    assert [(event["id"], event["rules"]) for event in after
            if event["type"] == "ruling.recorded"] == [("C1", item)]
    found = folded(path)
    assert skips.is_skipped(found, step, stage)
    assert [(skip.id, skip.step, skip.stage) for skip in skips.live(found)] == [
        ("C1", step, stage)]
    assert held(path)["B2"] == ([] if step == "docket" else ["Q1"])

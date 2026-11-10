"""Corrections: a ruling replaced by a later one that names it, and a ruling taken
back, over each kind of ruling; what every reader then takes, what the questions hold
up afterwards, and what is refused."""

from __future__ import annotations

import pytest

from moxi_rounds import (asking, corrections, holds, index, log, marking, moving,
                         opening, questions, ruling, state, write)
from moxi_rounds.cli import main
from moxi_rounds.errors import BadArgument, Refused

ROUND = "proj-a/a-round"
ASKED = "Do agent-read times follow the settings?"
ANSWER = "Prose follows the settings; parsed rows do not."
OTHER = "Everything follows the settings."
SAID = "Usage is not calculated in the status line; it may be shown there."
STAGES = ("B1", "B2", "B3")


@pytest.fixture
def rounds(tmp_path, add_project, capsys):
    """The rounds folder of `proj-a`, with `a-round` opened in it and the stages B1,
    B2 and B3 declared."""
    add_project("proj-a", notes_root=tmp_path / "notes")
    opening.open_round(ROUND, description="a placeholder round", actor="planner",
                       flow="fix")
    found = tmp_path / "notes" / "rounds"
    for stage in STAGES:
        log.append(round_log(found), "stage.declared",
                   {"id": stage, "repo": "proj-a", "branch": f"branch-{stage.lower()}"},
                   actor="planner")
    capsys.readouterr()
    return found


def printed(ruling_id, said, holder="proj-a/a-round", *, question=None):
    """What `rule` prints for one ruling: the sentence as recorded, the question it
    answers, and how whoever recorded it says the operator was shown it."""
    answers = "" if question is None else f"in answer to {question[0]}: {question[1]}\n"
    return (f"Recorded as {ruling_id}: {said}\n{answers}"
            'show him this under "Recorded as" in your next message, then:\n'
            f"  moxi rounds mark {holder} {ruling_id} --shown --record "
            "<pointer to that message>\n")


def round_log(rounds):
    return log.round_log(rounds / "a-round")


def ask(target=ROUND, blocks=None, **more):
    return asking.ask(target, ASKED, origin="comms:11", blocks=blocks, actor="scout",
                      **more).id


def rule(item=None, target=ROUND, record="comms:12", **more):
    return ruling.rule(target, item=item, record=record, actor="planner", **more)


def revoke(ruling_id, target=ROUND, **more):
    return rule(f"revoke:{ruling_id}", target=target, record="comms:13", **more)


def held(rounds, target=ROUND):
    path = round_log(rounds) if "/" in target else log.project_log(rounds)
    return state.fold(log.read(path))


def question(rounds, question_id="Q1", target=ROUND):
    found = questions.find(held(rounds, target), question_id)
    assert found is not None
    return found


def marks(rounds, target=ROUND):
    """Each ruling's id with what replaced it and what took it back."""
    return [(one.id, one.replaced_by, one.revoked_by)
            for one in held(rounds, target).rulings]


def holding(rounds):
    """The ids of the questions that hold each stage, and the close."""
    found = held(rounds)
    return ({stage: [one.id for one in holds.on_stage(found, stage)]
             for stage in STAGES}, [one.id for one in holds.on_close(found)])


def last(rounds, target=ROUND):
    """The last event of a log, without what the log itself sets."""
    path = round_log(rounds) if "/" in target else log.project_log(rounds)
    event = log.read(path)[-1]
    return {key: event[key] for key in event if key not in ("v", "n", "at")}


def logs(rounds):
    return {str(path.relative_to(rounds)): path.read_bytes()
            for path in sorted(rounds.rglob("*.jsonl"))}


EVERY = {"B1": ["Q1"], "B2": ["Q1"], "B3": ["Q1"]}
NO_STAGE = {"B1": [], "B2": [], "B3": []}


# -- an answer ---------------------------------------------------------------------------

def test_a_second_answer_that_names_the_first_replaces_it(rounds):
    ask(options=("a=Prose only.", "b=Everything."))
    rule("Q1", text=ANSWER, chose="a")

    ruled = rule("Q1", text=OTHER, chose="b", supersedes="C1")

    assert last(rounds) == {
        "actor": "planner", "type": "ruling.recorded", "id": "C2", "text": OTHER,
        "chose": "b", "question": "Q1", "state": "ruled", "stages": "all",
        "record": "comms:12", "kind": "substance", "rules": "Q1", "supersedes": "C1"}
    assert ruled.id == "C2" and ruled.ended is None
    assert marks(rounds) == [("C1", "C2", None), ("C2", None, None)]
    found = question(rounds)
    assert (found.state, found.ruling, found.chose) == ("ruled", "C2", "b")
    assert [one.id for one in corrections.live(held(rounds).rulings)] == ["C2"]


def test_a_replacing_answer_that_names_no_key_leaves_none_chosen(rounds):
    ask(options=("a=Prose only.", "b=Everything."))
    rule("Q1", chose="a")

    rule("Q1", text=OTHER, supersedes="C1")

    assert question(rounds).chose is None


def test_an_answer_taken_back_leaves_the_question_open_and_holding_its_stages(
        rounds, capsys):
    ask(options=("a=Prose only.", "b=Everything."))
    rule("Q1", chose="a")
    assert holding(rounds) == (NO_STAGE, [])

    assert main(["rule", ROUND, "revoke:C1", "--record", "comms:13", "--as",
                 "planner"]) == 0

    assert last(rounds) == {
        "actor": "planner", "type": "ruling.recorded", "id": "C2", "question": "Q1",
        "state": "open", "revokes": "C1", "record": "comms:13", "kind": "process",
        "rules": "revoke:C1"}
    assert capsys.readouterr().out == printed("C2", "revoke:C1")
    assert marks(rounds) == [("C1", None, "C2"), ("C2", None, None)]
    found = question(rounds)
    assert (found.state, found.chose, found.ruling) == ("open", None, "C2")
    assert holding(rounds) == (EVERY, ["Q1"])
    [waiting] = index.read("proj-a", rounds, "a-round").of("question")
    assert (waiting.id, waiting.body["state"]) == ("Q1", "open")


def test_a_question_whose_answer_was_taken_back_is_answered_again_as_if_new(rounds):
    ask()
    rule("Q1", text=ANSWER)
    revoke("C1")

    ruled = rule("Q1", text=OTHER)

    assert ruled.id == "C3" and "supersedes" not in ruled.event
    assert (question(rounds).state, question(rounds).ruling) == ("ruled", "C3")


def test_an_answer_to_a_parked_question_taken_back_leaves_it_open(rounds):
    ask()
    rule("park:Q1", until="B3")
    rule("Q1", text=ANSWER)

    revoke("C2")

    assert (question(rounds).state, question(rounds).until) == ("open", None)
    assert holding(rounds) == (EVERY, ["Q1"])


def test_the_answer_that_replaced_another_is_taken_back_and_the_first_stays_replaced(
        rounds):
    ask()
    rule("Q1", text=ANSWER)
    rule("Q1", text=OTHER, supersedes="C1")

    revoke("C2")

    assert marks(rounds) == [("C1", "C2", None), ("C2", None, "C3"), ("C3", None, None)]
    assert question(rounds).state == "open"


# -- a ruling that stands on its own -----------------------------------------------------

def test_a_plain_ruling_is_replaced_by_another_that_names_it(rounds):
    rule(text=SAID)
    rule(text="Another thing.")

    rule(text="Usage is shown in the status line.", stages="B2", supersedes="C1")

    assert last(rounds)["supersedes"] == "C1" and last(rounds)["stages"] == ["B2"]
    assert marks(rounds) == [("C1", "C3", None), ("C2", None, None), ("C3", None, None)]
    assert [one.id for one in corrections.live(held(rounds).rulings)] == ["C2", "C3"]


def test_a_plain_ruling_is_taken_back(rounds):
    rule(text=SAID)

    revoke("C1", text="He took it back.")

    assert last(rounds) == {
        "actor": "planner", "type": "ruling.recorded", "id": "C2",
        "text": "He took it back.", "revokes": "C1", "record": "comms:13",
        "kind": "process", "rules": "revoke:C1"}
    assert marks(rounds) == [("C1", None, "C2"), ("C2", None, None)]
    assert [one.id for one in corrections.live(held(rounds).rulings)] == ["C2"]


def test_a_register_entry_is_replaced_and_then_reads_as_replaced(rounds):
    rule(text=SAID, target="proj-a")

    rule(text="Shown, never calculated.", standing=True, supersedes="S1")
    rule(text="Shown there.", target="proj-a", scope="proj-a", supersedes="S2")

    assert marks(rounds, "proj-a") == [("S1", "S2", None), ("S2", "S3", None),
                                       ("S3", None, None)]
    assert held(rounds).rulings == []


def test_a_register_entry_is_taken_back_on_the_project(rounds):
    rule(text=SAID, target="proj-a")

    assert main(["rule", "proj-a", "revoke:S1", "--record", "comms:13"]) == 0

    assert marks(rounds, "proj-a") == [("S1", None, "S2"), ("S2", None, None)]


# -- a parking ---------------------------------------------------------------------------

def test_a_second_parking_names_the_first_and_replaces_it(rounds, capsys):
    ask()
    rule("park:Q1", until="B1")
    before = logs(rounds)

    assert main(["rule", ROUND, "park:Q1", "--until", "B2", "--record",
                 "comms:12"]) == 3

    err = capsys.readouterr().err
    assert "Q1 is already parked, by C1" in err and "--supersedes C1" in err
    assert logs(rounds) == before

    assert main(["rule", ROUND, "park:Q1", "--until", "B2", "--record", "comms:12",
                 "--supersedes", "C1"]) == 0

    assert marks(rounds) == [("C1", "C2", None), ("C2", None, None)]
    assert (question(rounds).state, question(rounds).until) == ("parked", "B2")
    assert holding(rounds) == (NO_STAGE, ["Q1"])


def test_a_parking_taken_back_leaves_the_question_holding_its_stages_again(rounds):
    ask(blocks="B1,B2")
    rule("park:Q1", until="B3")
    assert holding(rounds) == (NO_STAGE, ["Q1"])

    revoke("C1")

    assert last(rounds) == {
        "actor": "planner", "type": "ruling.recorded", "id": "C2", "question": "Q1",
        "revokes": "C1", "record": "comms:13", "kind": "process", "rules": "revoke:C1"}
    found = question(rounds)
    assert (found.state, found.until, found.blocks) == ("open", None, ("B1", "B2"))
    assert holding(rounds) == ({"B1": ["Q1"], "B2": ["Q1"], "B3": []}, ["Q1"])


def test_the_parking_that_replaced_another_is_taken_back_and_the_question_is_open(
        rounds):
    ask()
    rule("park:Q1", until="B1")
    rule("park:Q1", until="B2", supersedes="C1")

    revoke("C2")

    assert (question(rounds).state, question(rounds).until) == ("open", None)
    assert holding(rounds) == (EVERY, ["Q1"])


def test_a_parking_that_was_ended_by_a_reopening_is_taken_back_and_changes_nothing(
        rounds):
    ask()
    rule("park:Q1")
    marking.reopen(ROUND, "Q1", actor="coder")

    revoke("C1")

    assert marks(rounds)[0] == ("C1", None, "C2")
    assert question(rounds).state == "open"


def test_a_reopened_question_is_parked_again_with_no_ruling_named(rounds):
    ask()
    rule("park:Q1")
    marking.reopen(ROUND, "Q1", actor="coder")

    assert rule("park:Q1").id == "C2"

    assert question(rounds).state == "parked"


def test_a_narrowing_of_a_parked_question_outlasts_the_parking_taken_back(rounds):
    ask()
    rule("park:Q1")
    rule("narrow:Q1=B2")

    revoke("C1")

    assert (question(rounds).state, question(rounds).blocks) == ("open", ("B2",))
    assert holding(rounds) == ({"B1": [], "B2": ["Q1"], "B3": []}, ["Q1"])


# -- a narrowing -------------------------------------------------------------------------

def test_a_second_narrowing_names_the_first_and_replaces_it(rounds, capsys):
    ask()
    rule("narrow:Q1=B1")
    before = logs(rounds)

    assert main(["rule", ROUND, "narrow:Q1=none", "--record", "comms:12"]) == 3

    err = capsys.readouterr().err
    assert "Q1 was narrowed already, by C1" in err
    assert ("run: moxi rounds rule proj-a/a-round narrow:Q1=none --record PTR "
            "--supersedes C1") in err
    assert logs(rounds) == before

    # The new list is held to what the question held up before the first narrowing.
    rule("narrow:Q1=B2,B3", supersedes="C1")

    assert last(rounds)["blocks"] == ["B2", "B3"] and last(rounds)["supersedes"] == "C1"
    assert marks(rounds) == [("C1", "C2", None), ("C2", None, None)]
    assert question(rounds).blocks == ("B2", "B3")
    assert holding(rounds) == ({"B1": [], "B2": ["Q1"], "B3": ["Q1"]}, ["Q1"])


def test_a_replacing_narrowing_still_holds_up_no_stage_the_question_never_did(rounds):
    ask(blocks="B1,B2")
    rule("narrow:Q1=B1")
    before = logs(rounds)

    with pytest.raises(Refused, match="Q1 holds up B1,B2, and B1,B3 would hold up"):
        rule("narrow:Q1=B1,B3", supersedes="C1")

    assert logs(rounds) == before


def test_a_narrowing_taken_back_leaves_the_question_holding_the_wider_set(rounds):
    ask()
    rule("narrow:Q1=B2")
    assert holding(rounds) == ({"B1": [], "B2": ["Q1"], "B3": []}, ["Q1"])

    revoke("C1")

    assert question(rounds).blocks == "all"
    assert holding(rounds) == (EVERY, ["Q1"])


def test_the_narrowing_that_replaced_another_is_taken_back_and_neither_is_left(rounds):
    ask(blocks="B1,B2")
    rule("narrow:Q1=B1")
    rule("narrow:Q1=none", supersedes="C1")
    assert holding(rounds) == (NO_STAGE, ["Q1"])

    revoke("C2")

    assert question(rounds).blocks == ("B1", "B2")


@pytest.mark.parametrize("asked, widened, left", [
    (None, "B1,B2", "all"), ("B1,B2", "B1,B3", ("B1", "B2", "B3")),
    ("B1,B2", "all", "all")])
def test_a_widening_made_while_a_narrowing_stood_is_kept_when_it_is_taken_back(
        rounds, asked, widened, left):
    ask(blocks=asked)
    rule("narrow:Q1=B1")
    marking.widen(ROUND, "Q1", widened, actor="coder")

    revoke("C1")

    assert question(rounds).blocks == left


def test_a_widening_made_after_a_narrowing_was_taken_back_is_read_as_it_was_given(
        rounds):
    ask(blocks="B1,B2")
    rule("narrow:Q1=B1")
    revoke("C1")

    marking.widen(ROUND, "Q1", "B1,B2,B3", actor="coder")
    rule("park:Q1")
    revoke("C3")

    assert question(rounds).blocks == ("B1", "B2", "B3")


def test_a_narrowing_of_a_question_since_answered_is_taken_back_under_the_answer(
        rounds):
    ask()
    rule("narrow:Q1=B1")
    rule("Q1", text=ANSWER)

    revoke("C1")

    found = question(rounds)
    assert (found.state, found.blocks) == ("ruled", "all")
    assert holding(rounds) == (NO_STAGE, [])
    with pytest.raises(Refused, match="Q1 is already ruled, by C2"):
        rule("Q1", text=OTHER)


# -- a withdrawal ------------------------------------------------------------------------

def test_a_second_withdrawal_names_the_first_and_replaces_it(rounds, capsys):
    ask()
    rule("withdraw:Q1")
    before = logs(rounds)

    assert main(["rule", ROUND, "withdraw:Q1", "--record", "comms:12"]) == 3

    err = capsys.readouterr().err
    assert "Q1 is already withdrawn, by C1; a second withdrawal replaces" in err
    assert ("run: moxi rounds rule proj-a/a-round withdraw:Q1 --record PTR "
            "--supersedes C1") in err
    assert logs(rounds) == before

    rule("withdraw:Q1", text="It was never his question.", supersedes="C1")

    assert marks(rounds) == [("C1", "C2", None), ("C2", None, None)]
    assert (question(rounds).state, question(rounds).ruling) == ("withdrawn", "C2")


def test_a_withdrawal_taken_back_leaves_the_question_open_as_it_was(rounds):
    ask(blocks="B2")
    rule("withdraw:Q1")
    assert holding(rounds) == (NO_STAGE, [])

    revoke("C1")

    assert question(rounds).state == "open"
    assert holding(rounds) == ({"B1": [], "B2": ["Q1"], "B3": []}, ["Q1"])


def test_a_withdrawal_taken_back_leaves_a_parked_question_parked_as_it_was(rounds):
    ask()
    rule("park:Q1", until="B3")
    rule("withdraw:Q1")
    rule("withdraw:Q1", supersedes="C2")

    revoke("C3")

    assert (question(rounds).state, question(rounds).until) == ("parked", "B3")
    assert holding(rounds) == (NO_STAGE, ["Q1"])
    # The parking stands again, so it is the ruling a second parking names.
    with pytest.raises(Refused, match="Q1 is already parked, by C1"):
        rule("park:Q1")
    revoke("C1")
    assert holding(rounds) == (EVERY, ["Q1"])


# -- a project's question ----------------------------------------------------------------

def test_the_rulings_of_a_projects_question_are_replaced_and_taken_back(rounds):
    ask("proj-a", options=("a=Prose only.", "b=Everything."))
    rule("P1", target="proj-a", chose="a")

    rule("P1", target="proj-a", chose="b", supersedes="S1")

    assert question(rounds, "P1", "proj-a").chose == "b"
    revoke("S2", target="proj-a")
    assert last(rounds, "proj-a") == {
        "actor": "planner", "type": "ruling.recorded", "id": "S3", "question": "P1",
        "state": "open", "revokes": "S2", "record": "comms:13", "kind": "process",
        "rules": "revoke:S2"}
    assert marks(rounds, "proj-a") == [("S1", "S2", None), ("S2", None, "S3"),
                                       ("S3", None, None)]
    found = question(rounds, "P1", "proj-a")
    assert (found.state, found.chose) == ("open", None)


@pytest.mark.parametrize("item, was", [("park:P1", "parked"),
                                       ("withdraw:P1", "withdrawn")])
def test_a_parking_and_a_withdrawal_of_a_projects_question_are_taken_back(
        rounds, item, was):
    ask("proj-a")
    rule(item, target="proj-a")
    assert question(rounds, "P1", "proj-a").state == was

    revoke("S1", target="proj-a")

    assert question(rounds, "P1", "proj-a").state == "open"


# -- what is refused ---------------------------------------------------------------------

def test_a_ruling_that_names_one_the_log_does_not_hold_is_refused(rounds):
    ask()
    rule("Q1", text=ANSWER)
    before = logs(rounds)

    with pytest.raises(Refused, match="proj-a/a-round has no ruling C9 to replace"):
        rule(text=SAID, supersedes="C9")
    with pytest.raises(Refused, match="proj-a/a-round has no ruling C9 to replace"):
        rule("Q1", text=OTHER, supersedes="C9")
    with pytest.raises(Refused, match="proj-a has no ruling S4 to replace"):
        rule(text=SAID, target="proj-a", supersedes="S4")
    with pytest.raises(Refused, match="proj-a/a-round has no ruling C9"):
        revoke("C9")
    assert logs(rounds) == before


@pytest.mark.parametrize("given, names, said", [
    ({"item": "Q2", "text": OTHER}, "C1", "C1 rules Q1, and this ruling rules Q2"),
    ({"text": SAID}, "C1", "C1 rules Q1, and this ruling stands on its own"),
    ({"item": "Q2", "text": OTHER}, "C2", "C2 stands on its own, and this ruling rules"),
    ({"item": "park:Q2"}, "C3", "C3 rules park:Q3, and this ruling rules park:Q2"),
    ({"item": "withdraw:Q3"}, "C3", "C3 rules park:Q3, and this ruling rules withdraw"),
    ({"item": "narrow:Q2=B1"}, "C4", "C4 rules narrow:Q3=B1,B2, and this ruling")])
def test_a_ruling_replaces_only_an_earlier_ruling_of_the_same_thing(
        rounds, given, names, said):
    for _ in range(3):
        ask()
    rule("Q1", text=ANSWER)
    rule(text=SAID)
    rule("park:Q3")
    rule("narrow:Q3=B1,B2")
    before = logs(rounds)

    with pytest.raises(Refused, match=said):
        rule(supersedes=names, **given)

    assert logs(rounds) == before


def test_a_ruling_that_was_replaced_or_taken_back_is_corrected_no_further(rounds):
    for _ in range(2):
        ask()
    rule("Q1", text=ANSWER)
    rule("Q1", text=OTHER, supersedes="C1")
    rule("Q2", text=ANSWER)
    revoke("C3")
    before = logs(rounds)

    with pytest.raises(Refused) as replaced:
        revoke("C1")
    assert "C1 was replaced by C2, which is the ruling that stands" in str(
        replaced.value)
    assert "run: moxi rounds rule proj-a/a-round revoke:C2 --record PTR" in str(
        replaced.value)
    with pytest.raises(Refused, match="C1 was replaced already, by C2"):
        rule("Q1", text=ANSWER, supersedes="C1")
    with pytest.raises(Refused, match="C3 was taken back already, by C4"):
        revoke("C3")
    with pytest.raises(Refused, match="C3 was taken back by C4, so nothing stands"):
        rule("Q2", text=OTHER, supersedes="C3")
    assert logs(rounds) == before


def test_a_taking_back_is_not_itself_taken_back_or_replaced(rounds):
    ask()
    rule("Q1", text=ANSWER)
    revoke("C1")
    before = logs(rounds)

    with pytest.raises(Refused) as refused:
        revoke("C2")

    assert "C2 took C1 back, and a taking back is not itself taken back" in str(
        refused.value)
    assert "run: moxi rounds rule proj-a/a-round Q1 --text TEXT --record PTR" in str(
        refused.value)
    with pytest.raises(Refused, match="a ruling of revoke:C1 is not ruled a second"):
        rule(text=SAID, supersedes="C2")
    assert logs(rounds) == before


def test_a_move_of_a_question_is_not_taken_back_and_the_refusal_names_the_way_back(
        rounds):
    ask()
    moving.move("proj-a", f"{ROUND}:Q1", actor="planner", record="comms:14")
    moving.adopt(ROUND, "P1", actor="planner", blocks="B2", record="comms:15")
    before = logs(rounds)
    assert [one.rules for one in held(rounds).rulings] == ["move:Q1"]
    assert [one.rules for one in held(rounds, "proj-a").rulings] == ["move:P1"]

    with pytest.raises(Refused) as out_of_the_round:
        revoke("C1")
    with pytest.raises(Refused) as into_the_round:
        revoke("S1", target="proj-a")

    assert "C1 moved Q1 to the project's list, where it is P1" in str(
        out_of_the_round.value)
    assert "run: moxi rounds ask proj-a/a-round --adopt P1" in str(
        out_of_the_round.value)
    assert "S1 brought P1 into proj-a/a-round as Q2" in str(into_the_round.value)
    assert "run: moxi rounds ask proj-a --move proj-a/a-round:Q2 --record PTR" in str(
        into_the_round.value)
    assert logs(rounds) == before


def test_a_ruling_of_a_question_that_was_moved_since_is_not_taken_back_there(rounds):
    ask()
    rule("park:Q1")
    moving.move("proj-a", f"{ROUND}:Q1", actor="planner", record="comms:14")
    before = logs(rounds)

    with pytest.raises(Refused, match="Q1 was moved out of proj-a/a-round since and "
                                      "is now P1"):
        revoke("C1")

    assert logs(rounds) == before


def test_a_ruling_of_a_kind_nothing_here_knows_is_neither_taken_back_nor_replaced(
        rounds):
    log.append(round_log(rounds), "ruling.recorded",
               {"id": "C1", "record": "comms:12", "kind": "process",
                "rules": "skip:check:B2"}, actor="planner")
    before = logs(rounds)

    with pytest.raises(Refused, match="C1 rules skip:check:B2, and nothing here says "
                                      "how such a ruling is taken back"):
        revoke("C1")
    with pytest.raises(Refused, match="a ruling of skip:check:B2 is not ruled a "
                                      "second time"):
        rule(text=SAID, supersedes="C1")
    assert logs(rounds) == before


def test_a_correction_needs_his_pointer(rounds, capsys):
    rule(text=SAID)
    before = logs(rounds)

    assert main(["rule", ROUND, "revoke:C1"]) == 3
    assert ("run: moxi rounds rule proj-a/a-round revoke:C1 --record PTR"
            in capsys.readouterr().err)
    assert main(["rule", ROUND, "--text", SAID, "--supersedes", "C1"]) == 3
    assert ("run: moxi rounds rule proj-a/a-round --text TEXT --record PTR"
            in capsys.readouterr().err)
    assert logs(rounds) == before


@pytest.mark.parametrize("target, argv", [
    (ROUND, ["--text", SAID, "--supersedes", "Q1"]),
    (ROUND, ["--text", SAID, "--supersedes", "C01"]),
    (ROUND, ["revoke:C1", "--stages", "B1"]), (ROUND, ["revoke:C1", "--standing"]),
    (ROUND, ["revoke:C1", "--supersedes", "C1"]), (ROUND, ["revoke:C1", "--text", " "]),
    ("proj-a", ["revoke:S1", "--scope", "proj-a"]),
    (ROUND, ["revoke:C1", "--chose", "a"]), (ROUND, ["revoke:C1", "--until", "B1"]),
    (ROUND, ["revoke:Q1"]), (ROUND, ["revoke:"]), (ROUND, ["revoke:C0"])])
def test_a_correction_that_cant_be_read_is_a_usage_error(rounds, capsys, target, argv):
    rule(text=SAID)
    rule(text=SAID, target="proj-a")
    before = logs(rounds)

    assert main(["rule", target, *argv, "--record", "comms:13"]) == 2

    assert logs(rounds) == before


@pytest.mark.parametrize("target, argv, fix", [
    (ROUND, ["--text", SAID, "--supersedes", "S1"],
     "moxi rounds rule proj-a --text TEXT --supersedes S1 --record PTR"),
    (ROUND, ["--text", SAID, "--standing", "--supersedes", "C1"],
     "moxi rounds rule proj-a/a-round --text TEXT --supersedes C1 --record PTR"),
    ("proj-a", ["--text", SAID, "--supersedes", "C1"],
     "moxi rounds rule proj-a/<slug> --text TEXT --supersedes C1 --record PTR"),
    (ROUND, ["revoke:S1"], "moxi rounds rule proj-a revoke:S1 --record PTR"),
    ("proj-a", ["revoke:C1"], "moxi rounds rule proj-a/<slug> revoke:C1 --record PTR")])
def test_a_correction_naming_a_ruling_of_the_other_log_is_refused(rounds, capsys,
                                                                  target, argv, fix):
    rule(text=SAID)
    rule(text=SAID, target="proj-a")
    before = logs(rounds)

    assert main(["rule", target, *argv, "--record", "comms:13"]) == 3

    assert f"run: {fix}" in capsys.readouterr().err
    assert logs(rounds) == before


def test_a_ruling_named_by_something_that_is_no_id_is_a_bad_argument():
    with pytest.raises(BadArgument, match="is not a ruling's id"):
        ruling.rule(ROUND, text=SAID, record="comms:12", actor="planner",
                    supersedes="first")


# -- one event, and a round that has ended -----------------------------------------------

@pytest.mark.parametrize("given, again", [
    ({"item": "revoke:C1"}, "C1 was taken back already, by C2"),
    ({"item": "Q1", "text": OTHER, "supersedes": "C1"},
     "C1 was replaced already, by C2")])
def test_a_correction_stopped_after_its_append_is_whole_and_refused_when_run_again(
        rounds, monkeypatch, given, again):
    ask()
    rule("Q1", text=ANSWER)
    events_before = len(log.read(round_log(rounds)))

    def stop(written):
        raise KeyboardInterrupt

    with monkeypatch.context() as patched:
        patched.setattr(write, "ON_APPEND", (stop,))
        with pytest.raises(KeyboardInterrupt):
            rule(**given)

    with pytest.raises(Refused, match=again):
        rule(**given)
    after = log.read(round_log(rounds))
    assert len(after) == events_before + 1
    assert after[-1]["type"] == "ruling.recorded"
    assert question(rounds).ruling == "C2"


@pytest.mark.parametrize("how", ["closed", "dropped"])
def test_a_correction_on_a_round_that_has_ended_is_recorded_and_reopens_nothing(
        rounds, capsys, how):
    ask()
    rule("Q1", text=ANSWER)
    rule(text=SAID)
    log.append(round_log(rounds), f"round.{how}", {}, actor="planner")

    assert main(["rule", ROUND, "revoke:C1", "--record", "comms:13"]) == 0
    out, err = capsys.readouterr()
    assert out == printed("C3", "revoke:C1")
    assert (f"note: proj-a/a-round was {how} and stays as it ended; C3 is recorded, "
            "and what follows from it is new work, in a new round") in err
    assert main(["rule", ROUND, "--text", "Shown there.", "--record", "comms:13",
                 "--supersedes", "C2"]) == 0
    assert "new work, in a new round" in capsys.readouterr().err

    found = held(rounds)
    assert [event["type"] for event in found.history[-3:]] == [
        f"round.{how}", "ruling.recorded", "ruling.recorded"]
    assert found.rulings[0].revoked_by == "C3" and found.rulings[1].replaced_by == "C4"
    assert questions.find(found, "Q1").state == "open"


@pytest.mark.parametrize("how", ["closed", "dropped"])
def test_a_ruling_that_corrects_nothing_is_refused_on_a_round_that_has_ended(
        rounds, capsys, how):
    log.append(round_log(rounds), f"round.{how}", {}, actor="planner")
    before = round_log(rounds).read_bytes()

    assert main(["rule", ROUND, "--text", SAID, "--record", "comms:13"]) == 3

    err = capsys.readouterr().err
    assert f"proj-a/a-round was {how} and stays as it ended" in err
    assert "run: moxi rounds open proj-a/SLUG --description TEXT" in err
    assert round_log(rounds).read_bytes() == before


# -- the table of kinds ------------------------------------------------------------------

@pytest.mark.parametrize("rules, name, item", [
    (None, "a ruling that stands on its own", ""), ("Q1", "an answer", "Q1"),
    ("P12", "an answer", "P12"), ("park:Q1", "a parking", "park:Q1"),
    ("narrow:Q1=B1,B2", "a narrowing", "narrow:Q1"),
    ("narrow:Q1=none", "a narrowing", "narrow:Q1"),
    ("withdraw:P3", "a withdrawal", "withdraw:P3"), ("move:Q1", "a move", "move:Q1"),
    ("revoke:C1", "a taking back", "revoke:C1")])
def test_each_ruling_has_its_kind_and_the_thing_it_rules(rules, name, item):
    assert corrections.kind_of(rules).name == name
    assert corrections.item_of(rules) == item


@pytest.mark.parametrize("rules", ["skip:docket:B2", "B2.M1=accept", "flow:full", ""])
def test_a_ruling_of_no_kind_in_the_table_has_none(rules):
    assert corrections.kind_of(rules) is None and corrections.item_of(rules) is None


def test_a_kind_added_to_the_table_is_taken_back_and_replaced(rounds, monkeypatch):
    skip = corrections.Kind("a skip", lambda rules: rules if (rules or "").startswith(
        "skip:") else None, undo=lambda ruling: {})
    monkeypatch.setattr(corrections, "KINDS", (*corrections.KINDS, skip))
    log.append(round_log(rounds), "ruling.recorded",
               {"id": "C1", "record": "comms:12", "kind": "process",
                "rules": "skip:check:B2"}, actor="planner")

    revoke("C1")

    assert last(rounds)["revokes"] == "C1" and "question" not in last(rounds)
    assert marks(rounds)[0] == ("C1", None, "C2")

"""Moving a question between a round and its project's list: `ask --adopt` and
`ask --move`, what the question keeps, whose act each is, and what a call that was
stopped half way leaves for the next."""

from __future__ import annotations

import pytest
from moxi import cli

from moxi_rounds import (asking, holds, index, log, marking, moving, opening, options,
                         questions, ruling, state, write)
from moxi_rounds.cli import main
from moxi_rounds.errors import BadArgument, BadName, Locked, NoRound, Refused

A = "proj-a/a-round"
B = "proj-a/b-round"
SAID = "Do agent-read times follow the settings?"
ASKED_AT = "2026-01-02T03:04:05Z"
MOVED_AT = "2026-02-03T04:05:06Z"


@pytest.fixture
def rounds(tmp_path, add_project, capsys):
    """The rounds folder of the project `proj-a`, with the rounds `a-round` and
    `b-round` opened in it, each with the stages B1, B2 and B3."""
    add_project("proj-a", notes_root=tmp_path / "notes")
    folder = tmp_path / "notes" / "rounds"
    for slug in ("a-round", "b-round"):
        opening.open_round(f"proj-a/{slug}", description="a placeholder round",
                           actor="planner", flow="fix")
        for stage in ("B1", "B2", "B3"):
            log.append(log.round_log(folder / slug), "stage.declared",
                       {"id": stage, "repo": "proj-a",
                        "branch": f"{slug}-{stage.lower()}"}, actor="planner")
    capsys.readouterr()
    return folder


def round_log(rounds, slug="a-round"):
    return log.round_log(rounds / slug)


def project_log(rounds):
    return log.project_log(rounds)


def logs(rounds):
    """Every log file under the rounds folder, with its bytes."""
    return {str(path.relative_to(rounds)): path.read_bytes()
            for path in sorted(rounds.rglob("*.jsonl"))}


def ask(target=A, blocks=None, **more):
    """Ask a question of `target`, by `scout` at `ASKED_AT`, and return its id."""
    more.setdefault("origin", None if "record" in more else "comms:11")
    return asking.ask(target, SAID, blocks=blocks, actor="scout", at=ASKED_AT,
                      **more).id


def park(target, question_id, until=None):
    ruling.rule(target, item=f"park:{question_id}", until=until, record="comms:12",
                actor="planner")


def finish(rounds, stage, slug="a-round"):
    log.append(round_log(rounds, slug), "finish.done", {"stage": stage, "tip": "0" * 40},
               actor="coder")


def held(path):
    return state.fold(log.read(path))


def question(path, question_id):
    found = questions.find(held(path), question_id)
    assert found is not None
    return found


def added(path, before):
    """The events a log gained since it held `before` of them, without what the log
    itself sets."""
    return [{key: event[key] for key in event if key not in ("v", "n")}
            for event in log.read(path)[before:]]


def count(path):
    return len(log.read(path)) if log.exists(path) else 0


def recorded_as(ruling_id, rules, holder):
    """What is printed for a move that is the operator's ruling: the ruling as
    recorded, and how whoever recorded it says he was shown it."""
    return (f"Recorded as {ruling_id}: {rules}\n"
            'show him this under "Recorded as" in your next message, then:\n'
            f"  moxi rounds mark {holder} {ruling_id} --shown --record "
            "<pointer to that message>\n")


def from_a_round(rounds, blocks=None):
    """A question of `a-round` put on the project's list by the operator; returns its
    id there."""
    ask(A, blocks)
    return moving.move("proj-a", f"{A}:Q1", record="comms:20", actor="planner").id


# -- adopting a question of the project's list -------------------------------------------

def test_an_adopted_question_arrives_holding_up_every_stage(rounds, capsys):
    ask("proj-a")
    before = count(round_log(rounds)), count(project_log(rounds))

    assert main(["ask", A, "--adopt", "P1", "--as", "curator"]) == 0

    [arrived] = added(round_log(rounds), before[0])
    assert arrived == {
        "actor": "curator", "type": "question.asked", "at": arrived["at"], "id": "Q1",
        "text": SAID, "from": "comms:11", "blocks": "all", "state": "open",
        "asked_by": "scout", "asked_at": ASKED_AT, "moved_from": "P1"}
    [left] = added(project_log(rounds), before[1])
    assert left == {"actor": "curator", "type": "question.changed", "at": arrived["at"],
                    "id": "P1", "state": "moved", "moved_to": f"{A}:Q1"}
    out, err = capsys.readouterr()
    assert out == f"Q1: {SAID}\nblocks: all   from: comms:11\nmoved from: P1\n"
    assert err == ""


@pytest.mark.parametrize("blocks, held_up", [("B2", ("B2",)), ("B1,B3", ("B1", "B3")),
                                             ("all", "all")])
def test_a_question_asked_on_the_projects_list_is_adopted_with_any_list(
        rounds, blocks, held_up):
    ask("proj-a")

    moved = moving.adopt(A, "P1", blocks=blocks, actor="curator")

    assert (moved.id, moved.name, moved.source, moved.ruling) == ("Q1", A, "P1", None)
    assert moved.question.blocks == held_up
    assert [one.id for one in holds.on_stage(held(round_log(rounds)), "B2")] == (
        ["Q1"] if "B2" in held_up or held_up == "all" else [])


def test_an_adopted_question_keeps_where_it_came_from_who_asked_it_and_when(rounds):
    ask("proj-a", origin="https://example.org/thread/4")

    moving.adopt(A, "P1", actor="curator", at=MOVED_AT)

    assert question(round_log(rounds), "Q1") == state.Question(
        id="Q1", text=SAID, origin="https://example.org/thread/4", blocks="all",
        state="open", by="scout", at=ASKED_AT, moved_from="P1")
    left = question(project_log(rounds), "P1")
    assert (left.state, left.moved_to, left.by, left.at) == (
        "moved", f"{A}:Q1", "scout", ASKED_AT)
    assert [(change.state, change.moved_to, change.actor, change.at, change.ruling)
            for change in left.changes] == [("moved", f"{A}:Q1", "curator", MOVED_AT,
                                             None)]


def test_a_question_that_is_the_operators_own_stays_his_own_when_adopted(rounds):
    ask("proj-a", record="comms:15")

    moving.adopt(A, "P1", actor="curator")

    found = question(round_log(rounds), "Q1")
    assert (found.origin, found.record) == ("comms:15", "comms:15")


def test_an_adopted_question_takes_the_next_id_of_the_round(rounds):
    ask(A)
    ask("proj-a")
    ask("proj-a")

    assert moving.adopt(A, "P2", actor="curator").id == "Q2"
    assert moving.adopt(B, "P1", actor="curator").id == "Q1"


def test_a_parked_question_is_adopted_parked_and_waits_for_the_round_it_waited_for(
        rounds):
    ask("proj-a")
    park("proj-a", "P1", until=f"round:{B}")

    moved = moving.adopt(A, "P1", actor="curator")

    assert (moved.question.state, moved.question.until) == ("parked", f"round:{B}")
    assert holds.on_stage(held(round_log(rounds)), "B1") == ()
    assert [one.id for one in holds.on_close(held(round_log(rounds)))] == ["Q1"]


def test_a_question_parked_until_a_round_that_has_closed_is_adopted_open(rounds):
    ask("proj-a")
    park("proj-a", "P1", until=f"round:{B}")
    log.append(round_log(rounds, "b-round"), "round.closed", {"numbers": {}},
               actor="planner")

    moved = moving.adopt(A, "P1", actor="curator")

    assert (moved.question.state, moved.question.until) == ("open", None)


def test_an_adopted_question_keeps_its_options_and_the_keys_retired_from_it(rounds):
    ask("proj-a", options=["a=prose only", "b=everything", "c=nothing"])
    options.fill("proj-a", "P1", actor="helper", retire=["c"])

    moved = moving.adopt(A, "P1", actor="curator")

    assert moved.question.options == (("a", "prose only"), ("b", "everything"))
    assert moved.question.retired == ("c",)
    with pytest.raises(Refused, match="the key c was retired from Q1"):
        options.fill(A, "Q1", actor="helper", options=["c=nothing at all"])


def test_a_stage_the_adopting_round_does_not_have_is_refused(rounds):
    ask("proj-a")
    before = logs(rounds)

    with pytest.raises(Refused, match="B7 is not a stage of proj-a/a-round"):
        moving.adopt(A, "P1", blocks="B1,B7", actor="curator")

    assert logs(rounds) == before


def test_a_question_the_list_does_not_have_is_not_adopted(rounds):
    before = logs(rounds)

    with pytest.raises(Refused, match="proj-a has no question P4"):
        moving.adopt(A, "P4", actor="curator")

    assert logs(rounds) == before


@pytest.mark.parametrize("item, said, state_", [
    ("P1", "Prose only.", "ruled"), ("withdraw:P1", None, "withdrawn")])
def test_a_ruled_or_withdrawn_question_is_not_adopted(rounds, item, said, state_):
    ask("proj-a")
    ruling.rule("proj-a", item=item, text=said, record="comms:12", actor="planner")
    before = logs(rounds)

    with pytest.raises(Refused, match=f"P1 is {state_}, and only a question that is "
                                      "open or parked is moved"):
        moving.adopt(A, "P1", actor="curator")

    assert logs(rounds) == before


def test_a_question_is_adopted_once(rounds):
    ask("proj-a")
    moving.adopt(A, "P1", actor="curator")
    before = logs(rounds)

    with pytest.raises(Refused, match=f"P1 was moved already and is now {A}:Q1"):
        moving.adopt(B, "P1", actor="curator")

    assert logs(rounds) == before


@pytest.mark.parametrize("argv, said", [
    (["ask", A, "--adopt", "P1", "--blocks", "none"], "none names no stage"),
    (["ask", A, "--adopt", "P1", "--record", "comms:20"], "no list is given"),
    (["ask", A, "--adopt", "Q1"], "not the id of a project's question"),
    (["ask", "proj-a", "--adopt", "P1"], "is not a round"),
    (["ask", A, SAID, "--adopt", "P1"], "the sentence is not for it"),
    (["ask", A, "--adopt", "P1", "--from", "comms:11"], "--from is not for it"),
    (["ask", A, "--adopt", "P1", "--option", "a=one"], "--option is not for it"),
    (["ask", A, "--adopt", "P1", "--move", f"{A}:Q1"], "give one of the two"),
    (["ask", "proj-a", "--move", f"{A}:Q1", "--blocks", "B1"],
     "--blocks is not for it"),
    (["ask", "proj-a", "--move", "Q1"], "does not name a round's question"),
    (["ask", "proj-a", "--move", f"{A}:P1"], "does not name a round's question"),
    (["ask", A, "--move", f"{A}:Q1"], "is not a project"),
    (["ask", A], "nothing is asked")])
def test_a_call_that_cant_be_read_is_a_usage_error(rounds, capsys, argv, said):
    ask("proj-a")
    ask(A)
    before = logs(rounds)

    assert main(argv) == 2

    assert said in capsys.readouterr().err
    assert logs(rounds) == before


# -- adopting a question that came from a round ------------------------------------------

def test_a_question_that_came_from_a_round_is_adopted_holding_up_every_stage(rounds):
    from_a_round(rounds, blocks="B2")

    moved = moving.adopt(B, "P1", actor="curator")

    assert moved.question.blocks == "all"
    assert moved.ruling is None
    assert [one.id for one in holds.on_stage(held(round_log(rounds, "b-round")),
                                             "B3")] == ["Q1"]


def test_a_list_for_a_question_that_came_from_a_round_is_refused_without_his_pointer(
        rounds, capsys):
    from_a_round(rounds)
    before = logs(rounds)

    assert main(["ask", B, "--adopt", "P1", "--blocks", "B2"]) == 3

    err = capsys.readouterr().err
    assert f"P1 came from {A}:Q1, so it arrives holding up every stage" in err
    assert f"run: moxi rounds ask {B} --adopt P1 --blocks B2 --record PTR" in err
    assert logs(rounds) == before


def test_every_stage_named_outright_needs_no_pointer(rounds):
    from_a_round(rounds)

    assert moving.adopt(B, "P1", blocks="all", actor="curator").question.blocks == "all"


def test_a_list_with_his_pointer_is_taken_and_the_adoption_is_his_ruling(rounds):
    from_a_round(rounds)
    before = count(round_log(rounds, "b-round")), count(project_log(rounds))

    moved = moving.adopt(B, "P1", blocks="B2", record="comms:21", actor="curator")

    assert (moved.id, moved.ruling, moved.question.blocks) == ("Q1", "S1", ("B2",))
    [arrived] = added(round_log(rounds, "b-round"), before[0])
    assert (arrived["type"], arrived["blocks"], arrived["moved_from"]) == (
        "question.asked", ["B2"], "P1")
    [left] = added(project_log(rounds), before[1])
    assert left == {
        "actor": "curator", "type": "ruling.recorded", "at": left["at"], "id": "S1",
        "record": "comms:21", "kind": "process", "rules": "move:P1", "question": "P1",
        "state": "moved", "moved_to": f"{B}:Q1"}
    gone = question(project_log(rounds), "P1")
    assert (gone.state, gone.ruling, gone.moved_to) == ("moved", "S1", f"{B}:Q1")


def test_an_adoption_that_is_his_ruling_is_printed_with_the_mark_that_says_he_saw_it(
        rounds, capsys):
    from_a_round(rounds)

    assert main(["ask", B, "--adopt", "P1", "--blocks", "B2", "--record",
                 "comms:21"]) == 0

    out, err = capsys.readouterr()
    assert out == (f"Q1: {SAID}\nblocks: B2   from: comms:11\nmoved from: P1\n"
                   + recorded_as("S1", "move:P1", "proj-a"))
    assert err == ""
    # The id printed is the one a mark takes.
    assert main(["mark", "proj-a", "S1", "--shown", "--record", "comms:30"]) == 0


def test_a_move_that_is_not_his_ruling_prints_no_ruling(rounds, capsys):
    ask(A, "B1")
    park(A, "Q1")
    finish(rounds, "B1")
    capsys.readouterr()

    assert main(["ask", "proj-a", "--move", f"{A}:Q1"]) == 0

    assert "Recorded as" not in capsys.readouterr().out


def test_a_pointer_of_no_known_shape_is_refused_at_an_adoption(rounds):
    from_a_round(rounds)
    before = logs(rounds)

    with pytest.raises(Refused, match="not-a-pointer"):
        moving.adopt(B, "P1", blocks="B2", record="not-a-pointer", actor="curator")

    assert logs(rounds) == before


# -- the operator's move -----------------------------------------------------------------

def test_the_operators_move_puts_a_rounds_question_on_the_projects_list(rounds, capsys):
    ask(A, "B1,B2")
    before = count(round_log(rounds)), count(project_log(rounds))

    assert main(["ask", "proj-a", "--move", f"{A}:Q1", "--record", "comms:20",
                 "--as", "planner"]) == 0

    [arrived] = added(project_log(rounds), before[1])
    assert arrived == {
        "actor": "planner", "type": "question.asked", "at": arrived["at"], "id": "P1",
        "text": SAID, "from": "comms:11", "blocks": "none", "state": "open",
        "asked_by": "scout", "asked_at": ASKED_AT, "moved_from": f"{A}:Q1",
        "held": ["B1", "B2"]}
    [left] = added(round_log(rounds), before[0])
    assert left == {
        "actor": "planner", "type": "ruling.recorded", "at": arrived["at"], "id": "C1",
        "record": "comms:20", "kind": "process", "rules": "move:Q1", "question": "Q1",
        "state": "moved", "moved_to": "P1"}
    out, err = capsys.readouterr()
    assert out == (f"P1: {SAID}\nblocks: none   from: comms:11\n"
                   f"moved from: {A}:Q1, where it held up B1,B2\n"
                   + recorded_as("C1", "move:Q1", A))
    assert err == ""


def test_the_real_command_moves_and_adopts(rounds, capsys):
    ask(A)

    assert cli.main(["rounds", "ask", "proj-a", "--move", f"{A}:Q1", "--record",
                     "comms:20"]) == 0
    assert cli.main(["rounds", "ask", B, "--adopt", "P1"]) == 0

    assert capsys.readouterr().out.splitlines()[-1] == "moved from: P1"


@pytest.mark.parametrize("blocks, held_up", [(None, "all"), ("B2,B3", ("B2", "B3")),
                                             ("none", "none")])
def test_a_moved_question_holds_up_nothing_and_keeps_what_it_held_up(
        rounds, blocks, held_up):
    ask(A, blocks)
    assert holds.on_close(held(round_log(rounds)))

    moved = moving.move("proj-a", f"{A}:Q1", record="comms:20", actor="planner")

    assert (moved.id, moved.name, moved.source, moved.ruling) == (
        "P1", "proj-a", f"{A}:Q1", "C1")
    assert (moved.question.blocks, moved.question.held, moved.question.moved_from) == (
        "none", held_up, f"{A}:Q1")
    after = held(round_log(rounds))
    assert [holds.on_stage(after, stage) for stage in ("B1", "B2", "B3")] == [()] * 3
    assert holds.on_close(after) == ()
    gone = question(round_log(rounds), "Q1")
    assert (gone.state, gone.moved_to, gone.ruling, gone.blocks) == (
        "moved", "P1", "C1", held_up)
    assert [(change.state, change.ruling, change.record) for change in gone.changes] == [
        ("moved", "C1", "comms:20")]


def test_a_moved_question_keeps_where_it_came_from_who_asked_it_and_when(rounds):
    ask(A, origin="file:design.md#read-times")

    moving.move("proj-a", f"{A}:Q1", record="comms:20", actor="planner", at=MOVED_AT)

    assert question(project_log(rounds), "P1") == state.Question(
        id="P1", text=SAID, origin="file:design.md#read-times", blocks="none",
        state="open", by="scout", at=ASKED_AT, moved_from=f"{A}:Q1", held="all")


def test_the_asker_and_the_time_outlast_a_move_and_an_adoption(rounds):
    ask(A, record="comms:15")

    moving.move("proj-a", f"{A}:Q1", record="comms:20", actor="planner", at=MOVED_AT)
    moved = moving.adopt(B, "P1", actor="curator", at=MOVED_AT)

    assert (moved.question.by, moved.question.at, moved.question.origin,
            moved.question.record) == ("scout", ASKED_AT, "comms:15", "comms:15")


def test_a_parked_question_is_moved_parked(rounds):
    ask(A)
    park(A, "Q1", until=f"round:{B}")

    moved = moving.move("proj-a", f"{A}:Q1", record="comms:20", actor="planner")

    assert (moved.question.state, moved.question.until) == ("parked", f"round:{B}")


def test_a_wait_for_a_stage_of_the_round_is_not_carried_out_of_it(rounds):
    ask(A)
    park(A, "Q1", until="B2")

    moved = moving.move("proj-a", f"{A}:Q1", record="comms:20", actor="planner")

    assert (moved.question.state, moved.question.until) == ("parked", None)
    assert question(round_log(rounds), "Q1").changes[0].until == "B2"


def test_a_question_whose_wait_is_over_is_moved_open(rounds):
    ask(A)
    park(A, "Q1", until="B2")
    finish(rounds, "B2")

    moved = moving.move("proj-a", f"{A}:Q1", record="comms:20", actor="planner")

    assert (moved.question.state, moved.question.until) == ("open", None)


def test_a_move_takes_the_next_id_of_the_projects_list(rounds):
    ask("proj-a")
    ask(A)
    ask(B)

    assert moving.move("proj-a", f"{A}:Q1", record="comms:20", actor="planner").id == "P2"
    assert moving.move("proj-a", f"{B}:Q1", record="comms:20", actor="planner").id == "P3"


def test_a_move_names_the_project_by_its_stored_key(rounds):
    ask(A)

    moved = moving.move("PROJ-A", "PROJ-A/a-round:Q1", record="comms:20",
                        actor="planner")

    assert (moved.name, moved.source) == ("proj-a", f"{A}:Q1")


@pytest.mark.parametrize("item, said, state_", [
    ("Q1", "Prose only.", "ruled"), ("withdraw:Q1", None, "withdrawn")])
@pytest.mark.parametrize("record", [None, "comms:20"])
def test_a_ruled_or_withdrawn_question_is_not_moved(rounds, item, said, state_, record):
    ask(A)
    ruling.rule(A, item=item, text=said, record="comms:12", actor="planner")
    before = logs(rounds)

    with pytest.raises(Refused, match=f"Q1 is {state_}, and only a question that is "
                                      "open or parked is moved"):
        moving.move("proj-a", f"{A}:Q1", record=record, actor="planner")

    assert logs(rounds) == before


def test_a_question_is_moved_once(rounds):
    ask(A)
    moving.move("proj-a", f"{A}:Q1", record="comms:20", actor="planner")
    before = logs(rounds)

    with pytest.raises(Refused, match="Q1 was moved already and is now P1"):
        moving.move("proj-a", f"{A}:Q1", record="comms:20", actor="planner")

    assert logs(rounds) == before


def test_a_question_the_round_does_not_have_is_not_moved(rounds):
    before = logs(rounds)

    with pytest.raises(Refused, match="proj-a/a-round has no question Q4"):
        moving.move("proj-a", f"{A}:Q4", record="comms:20", actor="planner")

    assert logs(rounds) == before


def test_a_round_that_was_never_opened_has_no_question_to_move_or_to_adopt(rounds):
    ask("proj-a")
    before = logs(rounds)

    with pytest.raises(NoRound):
        moving.move("proj-a", "proj-a/c-round:Q1", record="comms:20", actor="planner")
    with pytest.raises(NoRound):
        moving.adopt("proj-a/c-round", "P1", actor="curator")
    with pytest.raises(BadName):
        moving.move("proj-a", "proj-a/C_Round:Q1", record="comms:20", actor="planner")

    assert logs(rounds) == before


def test_a_question_moves_to_the_list_of_its_own_project_only(rounds, add_project,
                                                              tmp_path):
    add_project("proj-b", notes_root=tmp_path / "other")
    ask(A)
    before = logs(rounds)

    with pytest.raises(BadArgument, match="is not a round of proj-b"):
        moving.move("proj-b", f"{A}:Q1", record="comms:20", actor="planner")

    assert logs(rounds) == before


@pytest.mark.parametrize("record", ["not-a-pointer", "comms:", "http://example.org/x"])
def test_a_pointer_of_no_known_shape_is_refused_at_a_move(rounds, record):
    ask(A)
    before = logs(rounds)

    with pytest.raises(Refused):
        moving.move("proj-a", f"{A}:Q1", record=record, actor="planner")

    assert logs(rounds) == before


# -- a move without his pointer ----------------------------------------------------------

def test_a_parked_question_whose_stages_are_all_finished_is_moved_by_anyone(rounds):
    ask(A, "B1,B2")
    park(A, "Q1")
    finish(rounds, "B1")
    finish(rounds, "B2")
    before = count(round_log(rounds))

    moved = moving.move("proj-a", f"{A}:Q1", actor="planner")

    assert (moved.id, moved.ruling, moved.question.state) == ("P1", None, "parked")
    [left] = added(round_log(rounds), before)
    assert left == {"actor": "planner", "type": "question.changed", "at": left["at"],
                    "id": "Q1", "state": "moved", "moved_to": "P1"}
    assert held(round_log(rounds)).rulings[-1].rules == "park:Q1"


def test_a_stage_that_left_the_plan_counts_as_one_that_is_done(rounds):
    ask(A, "B1,B2,B3")
    park(A, "Q1")
    finish(rounds, "B1")
    log.append(round_log(rounds), "stage.changed", {"id": "B2", "removed": True},
               actor="planner")
    log.append(round_log(rounds), "ruling.recorded",
               {"id": "C9", "record": "comms:13", "kind": "process",
                "rules": "unstage:B3"}, actor="planner")

    assert moving.move("proj-a", f"{A}:Q1", actor="planner").id == "P1"


def lead_moves(rounds, capsys):
    """Run the move of Q1 of `a-round` with no pointer; returns the exit code and what
    was said."""
    code = main(["ask", "proj-a", "--move", f"{A}:Q1"])
    return code, capsys.readouterr().err


def test_an_open_question_is_not_moved_without_his_pointer(rounds, capsys):
    ask(A, "B1")
    finish(rounds, "B1")
    before = logs(rounds)

    code, err = lead_moves(rounds, capsys)

    assert code == 3
    assert "and Q1 is open. PTR below" in err
    assert f"run: moxi rounds ask proj-a --move {A}:Q1 --record PTR" in err
    assert logs(rounds) == before


def test_a_parked_question_that_holds_up_every_stage_is_not_moved_without_his_pointer(
        rounds, capsys):
    ask(A)
    park(A, "Q1")
    for stage in ("B1", "B2", "B3"):
        finish(rounds, stage)
    before = logs(rounds)

    code, err = lead_moves(rounds, capsys)

    assert code == 3
    assert "Q1 holds up every stage, any the round gains later included" in err
    assert "Q1 is open" not in err
    assert logs(rounds) == before


def test_a_parked_question_that_holds_up_no_stage_is_not_moved_without_his_pointer(
        rounds, capsys):
    ask(A, "none")
    park(A, "Q1")
    before = logs(rounds)

    code, err = lead_moves(rounds, capsys)

    assert code == 3
    assert "Q1 holds up no stage" in err
    assert logs(rounds) == before


def test_a_question_is_not_moved_without_his_pointer_while_a_stage_it_lists_is_unfinished(
        rounds, capsys):
    ask(A, "B1,B2,B3")
    park(A, "Q1")
    finish(rounds, "B1")
    before = logs(rounds)

    code, err = lead_moves(rounds, capsys)

    assert code == 3
    assert "B2, B3 is neither finished nor out of the plan" in err
    assert logs(rounds) == before

    finish(rounds, "B2")
    code, err = lead_moves(rounds, capsys)
    assert code == 3 and "B3 is neither finished nor out of the plan" in err

    finish(rounds, "B3")
    assert lead_moves(rounds, capsys) == (0, "")
    assert question(project_log(rounds), "P1").held == ("B1", "B2", "B3")


def test_a_refused_move_says_every_condition_it_falls_short_of(rounds, capsys):
    ask(A, "B1,B2")
    finish(rounds, "B1")

    code, err = lead_moves(rounds, capsys)

    assert code == 3
    assert "Q1 is open; B2 is neither finished nor out of the plan" in err


def test_a_question_parked_until_a_stage_that_is_finished_reads_open_and_is_his_to_move(
        rounds, capsys):
    ask(A, "B1,B2")
    park(A, "Q1", until="B1")
    finish(rounds, "B1")
    finish(rounds, "B2")
    before = logs(rounds)

    code, err = lead_moves(rounds, capsys)

    assert code == 3
    assert "Q1 reads as open, since what it was parked until has come" in err
    assert logs(rounds) == before


# -- what a moved question is in the log it left -----------------------------------------

def test_a_moved_question_takes_no_ruling_no_mark_and_no_options_where_it_was(rounds):
    ask(A, "B1")
    moving.move("proj-a", f"{A}:Q1", record="comms:20", actor="planner")
    before = logs(rounds)

    for item, said in (("Q1", "Prose only."), ("park:Q1", None), ("narrow:Q1=none", None),
                       ("withdraw:Q1", None)):
        with pytest.raises(Refused, match="Q1 was moved out of proj-a/a-round and is "
                                          "now P1"):
            ruling.rule(A, item=item, text=said, record="comms:12", actor="planner")
    with pytest.raises(Refused, match="Q1 is moved and holds nothing up"):
        marking.widen(A, "Q1", "all", actor="coder")
    with pytest.raises(Refused, match="Q1 is moved, and only a parked question"):
        marking.reopen(A, "Q1", actor="coder")
    with pytest.raises(Refused, match="Q1 is moved; options are added"):
        options.fill(A, "Q1", actor="helper", options=["a=one", "b=two"])

    assert logs(rounds) == before


def test_a_moved_question_is_ruled_where_it_is_now(rounds):
    ask(A)
    moving.move("proj-a", f"{A}:Q1", record="comms:20", actor="planner")

    ruled = ruling.rule("proj-a", item="P1", text="Prose only.", record="comms:22",
                        actor="planner")

    assert ruled.id == "S1"
    assert question(project_log(rounds), "P1").state == "ruled"


def test_a_moved_question_waits_on_him_in_the_log_it_is_in_now(rounds):
    def waiting(slug=None):
        return [one.id for one in index.read("proj-a", rounds, slug).of("question")]

    ask(A)
    assert waiting("a-round") == ["Q1"]

    moving.move("proj-a", f"{A}:Q1", record="comms:20", actor="planner")

    assert waiting("a-round") == []
    assert waiting() == ["P1"]

    moving.adopt(B, "P1", actor="curator")

    assert waiting() == []
    assert waiting("b-round") == ["Q1"]


# -- a move stopped between the two logs -------------------------------------------------

def stopped_after_one_append(monkeypatch, call):
    """Run `call`, and stop it as soon as its first event is in a log: before the
    second is appended."""
    append = log.append
    done = []

    def once(*args, **more):
        if done:
            raise KeyboardInterrupt
        done.append(append(*args, **more))
        return done[0]

    with monkeypatch.context() as patched:
        patched.setattr(log, "append", once)
        with pytest.raises(KeyboardInterrupt):
            call()
    return done[0]


@pytest.mark.parametrize("record, left_as", [("comms:20", "ruling.recorded"),
                                             (None, "question.changed")])
def test_a_move_stopped_between_the_two_logs_is_finished_by_running_it_again(
        rounds, monkeypatch, record, left_as):
    ask(A, "B1")
    park(A, "Q1")
    finish(rounds, "B1")
    before = count(round_log(rounds)), count(project_log(rounds))

    def call():
        return moving.move("proj-a", f"{A}:Q1", record=record, actor="planner")

    first = stopped_after_one_append(monkeypatch, call)

    # The question is on the list already, and is still the round's.
    assert (first["type"], first["id"], first["moved_from"]) == (
        "question.asked", "P1", f"{A}:Q1")
    assert count(round_log(rounds)) == before[0]
    assert question(round_log(rounds), "Q1").state == "parked"
    assert [one.id for one in holds.on_close(held(round_log(rounds)))] == ["Q1"]

    moved = call()

    assert (moved.id, moved.finished) == ("P1", True)
    assert [event["type"] for event in moved.events] == [left_as]
    assert count(project_log(rounds)) == before[1] + 1
    assert [one.id for one in held(project_log(rounds)).questions] == ["P1"]
    [left] = added(round_log(rounds), before[0])
    assert (left["type"], left["state"], left["moved_to"]) == (left_as, "moved", "P1")
    with pytest.raises(Refused, match="Q1 was moved already and is now P1"):
        call()


@pytest.mark.parametrize("blocks, record, left_as", [
    (None, None, "question.changed"), ("B2", "comms:21", "ruling.recorded")])
def test_an_adoption_stopped_between_the_two_logs_is_finished_by_running_it_again(
        rounds, monkeypatch, blocks, record, left_as):
    from_a_round(rounds)
    before = count(round_log(rounds, "b-round")), count(project_log(rounds))

    def call():
        return moving.adopt(B, "P1", blocks=blocks, record=record, actor="curator")

    first = stopped_after_one_append(monkeypatch, call)

    assert (first["type"], first["id"], first["moved_from"]) == (
        "question.asked", "Q1", "P1")
    assert count(project_log(rounds)) == before[1]
    assert question(project_log(rounds), "P1").state == "open"

    moved = call()

    assert (moved.id, moved.finished) == ("Q1", True)
    assert [event["type"] for event in moved.events] == [left_as]
    assert count(round_log(rounds, "b-round")) == before[0] + 1
    assert [one.id for one in held(round_log(rounds, "b-round")).questions] == ["Q1"]
    [left] = added(project_log(rounds), before[1])
    assert (left["type"], left["state"], left["moved_to"]) == (
        left_as, "moved", f"{B}:Q1")


def test_an_adoption_that_was_stopped_is_not_finished_with_another_list(
        rounds, monkeypatch):
    ask("proj-a")
    stopped_after_one_append(
        monkeypatch, lambda: moving.adopt(A, "P1", blocks="B2", actor="curator"))
    before = logs(rounds)

    with pytest.raises(Refused) as refused:
        moving.adopt(A, "P1", actor="curator")

    assert "P1 is already in proj-a/a-round as Q1, holding up B2" in str(refused.value)
    assert f"run: moxi rounds ask {A} --adopt P1 --blocks B2" in str(refused.value)
    assert logs(rounds) == before
    assert moving.adopt(A, "P1", blocks="B2", actor="curator").finished


def test_a_narrowed_adoption_that_was_stopped_is_not_finished_without_his_pointer(
        rounds, monkeypatch):
    from_a_round(rounds)
    stopped_after_one_append(
        monkeypatch,
        lambda: moving.adopt(B, "P1", blocks="B2", record="comms:21", actor="curator"))
    before = logs(rounds)

    with pytest.raises(Refused, match="which only the operator does"):
        moving.adopt(B, "P1", blocks="B2", actor="curator")

    assert logs(rounds) == before


def test_a_move_that_was_not_stopped_is_two_events_one_in_each_log(rounds):
    ask(A)

    moved = moving.move("proj-a", f"{A}:Q1", record="comms:20", actor="planner")

    assert not moved.finished
    assert [event["type"] for event in moved.events] == ["question.asked",
                                                         "ruling.recorded"]


# -- the locks ---------------------------------------------------------------------------

@pytest.mark.parametrize("which", ["round", "project"])
def test_a_move_and_an_adoption_wait_for_both_locks(rounds, holder, which):
    ask(A)
    ask("proj-a")
    before = logs(rounds)
    path = (write.round_lock("proj-a", "a-round") if which == "round" else
            write.project_lock("proj-a"))
    busy = holder(path)

    for call in (lambda: moving.move("proj-a", f"{A}:Q1", record="comms:20",
                                     actor="planner", wait_s=0.2),
                 lambda: moving.adopt(A, "P1", actor="curator", wait_s=0.2)):
        with pytest.raises(Locked) as stopped:
            call()
        assert (stopped.value.file, stopped.value.holder) == (path, busy.pid)

    assert logs(rounds) == before
    if which == "project":
        # The round's lock was let go when the project's could not be had.
        assert not write.round_lock("proj-a", "a-round").read_bytes()


# -- every question of a round, for a verb that ends the round ---------------------------

def test_the_open_and_parked_questions_of_a_round_are_made_ready_for_the_list(rounds):
    ask("proj-a")
    ask(A, "B1")
    ask(A)
    ask(A, "none")
    ask(A)
    park(A, "Q2", until=f"round:{B}")
    ruling.rule(A, item="withdraw:Q3", record="comms:12", actor="planner")
    moving.move("proj-a", f"{A}:Q4", record="comms:20", actor="planner")

    found = moving.arrivals(held(round_log(rounds)), held(project_log(rounds)), A)

    assert [(one.question, one.became) for one in found] == [("Q1", "P3"), ("Q2", "P4")]
    assert [dict(one.entry.fields) for one in found] == [
        {"id": "P3", "text": SAID, "from": "comms:11", "blocks": "none",
         "state": "open", "asked_by": "scout", "asked_at": ASKED_AT,
         "moved_from": f"{A}:Q1", "held": ["B1"]},
        {"id": "P4", "text": SAID, "from": "comms:11", "blocks": "none",
         "state": "parked", "asked_by": "scout", "asked_at": ASKED_AT,
         "moved_from": f"{A}:Q2", "until": f"round:{B}", "held": "all"}]
    assert {(one.entry.type, one.entry.log) for one in found} == {
        ("question.asked", write.PROJECT)}


def test_a_question_the_list_already_holds_is_not_made_ready_again(rounds, monkeypatch):
    ask(A)
    ask(A)
    stopped_after_one_append(
        monkeypatch,
        lambda: moving.move("proj-a", f"{A}:Q1", record="comms:20", actor="planner"))

    found = moving.arrivals(held(round_log(rounds)), held(project_log(rounds)), A)

    assert [(one.question, one.became, one.entry is None) for one in found] == [
        ("Q1", "P1", True), ("Q2", "P2", False)]

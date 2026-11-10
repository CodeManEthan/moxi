"""`moxi rounds mark` on a question: widening what it holds up and reopening a parked
one, both anyone's, and what each refuses."""

from __future__ import annotations

import pytest
from moxi import cli

from moxi_rounds import asking, index, log, marking, opening, ruling, state, write
from moxi_rounds.cli import main
from moxi_rounds.errors import NoRound, Refused

AT = "2026-01-02T03:04:05Z"
SAID = "Do agent-read times follow the settings?"


@pytest.fixture
def rounds(tmp_path, add_project, capsys):
    """The rounds folder of the project `proj-a`, with the round `a-round` opened in
    it, its stages B1, B2 and B3 declared."""
    add_project("proj-a", notes_root=tmp_path / "notes")
    opening.open_round("proj-a/a-round", description="a placeholder round",
                       actor="planner", flow="fix")
    folder = tmp_path / "notes" / "rounds"
    for stage in ("B1", "B2", "B3"):
        log.append(log.round_log(folder / "a-round"), "stage.declared",
                   {"id": stage, "repo": "proj-a", "branch": f"branch-{stage.lower()}"},
                   actor="planner")
    capsys.readouterr()
    return folder


def ask(blocks=None):
    """Ask a question of `a-round` and return its id."""
    return asking.ask("proj-a/a-round", SAID, origin="comms:12", blocks=blocks,
                      actor="scout").id


def rule(item, **more):
    return ruling.rule("proj-a/a-round", item=item, record="comms:13", actor="planner",
                       **more)


def round_log(rounds):
    return log.round_log(rounds / "a-round")


def question(rounds, question_id="Q1"):
    for found in state.fold(log.read(round_log(rounds))).questions:
        if found.id == question_id:
            return found
    raise AssertionError(f"no {question_id}")


def mark(*argv):
    return main(["mark", "proj-a/a-round", *argv])


# -- widening ----------------------------------------------------------------------------

@pytest.mark.parametrize("old, new, recorded", [
    ("B1", "B1,B2", ["B1", "B2"]), ("B1", "all", "all"), ("B2,B1", "B1,B2,B3",
                                                          ["B1", "B2", "B3"]),
    ("none", "B3", ["B3"]), ("none", "all", "all"), ("all", "all", "all"),
    ("B1", "B1", ["B1"])])
def test_a_question_is_made_to_hold_up_more_by_anyone_with_no_pointer(
        rounds, capsys, old, new, recorded):
    ask(old)
    capsys.readouterr()

    assert mark("Q1", "--blocks", new, "--as", "coder") == 0

    event = log.read(round_log(rounds))[-1]
    assert {key: event[key] for key in event if key not in ("v", "n", "at")} == {
        "actor": "coder", "type": "question.changed", "id": "Q1", "blocks": recorded}
    assert question(rounds).blocks == (recorded if recorded == "all"
                                       else tuple(recorded))
    assert question(rounds).state == "open"
    assert capsys.readouterr().out == f"Q1: open   blocks: {new}\n"


@pytest.mark.parametrize("old, new", [
    ("all", "B1"), ("all", "B1,B2,B3"), ("B1,B2", "B1"), ("B1,B2", "B2,B3"),
    ("B1", "B2")])
def test_a_change_that_lets_a_stage_go_is_refused_and_names_the_ruling_that_narrows(
        rounds, capsys, old, new):
    ask(old)
    before = round_log(rounds).read_bytes()

    assert mark("Q1", "--blocks", new) == 3

    err = capsys.readouterr().err
    assert f"Q1 holds up {old}" in err and "only the operator narrows it" in err
    assert f"run: moxi rounds rule proj-a/a-round narrow:Q1={new} --record PTR" in err
    assert round_log(rounds).read_bytes() == before


@pytest.mark.parametrize("old", ["all", "B1"])
def test_mark_takes_no_none_for_what_a_question_holds_up(rounds, capsys, old):
    ask(old)

    with pytest.raises(Refused, match="narrow:Q1=none --record PTR"):
        marking.widen("proj-a/a-round", "Q1", "none", actor="coder")


def test_a_widening_then_a_narrowing_by_mark_is_refused(rounds, capsys):
    ask("B1")

    assert mark("Q1", "--blocks", "B1,B2") == 0
    assert mark("Q1", "--blocks", "B1") == 3
    assert question(rounds).blocks == ("B1", "B2")


def test_a_stage_the_round_does_not_have_is_refused(rounds, capsys):
    ask("B1")
    before = round_log(rounds).read_bytes()

    assert mark("Q1", "--blocks", "B1,B7") == 3

    assert ("B7 is not a stage of proj-a/a-round: its stages are B1, B2, B3"
            in capsys.readouterr().err)
    assert round_log(rounds).read_bytes() == before


def test_a_parked_question_is_widened_and_stays_parked(rounds, capsys):
    ask("B1")
    rule("park:Q1", until="B2")

    assert mark("Q1", "--blocks", "all") == 0

    found = question(rounds)
    assert (found.state, found.until, found.blocks) == ("parked", "B2", "all")


@pytest.mark.parametrize("item, state_word", [("Q1", "ruled"), ("withdraw:Q1",
                                                               "withdrawn")])
def test_a_ruled_or_withdrawn_question_is_not_widened(rounds, capsys, item, state_word):
    ask("B1")
    rule(item, text="It is so.")
    before = round_log(rounds).read_bytes()

    assert mark("Q1", "--blocks", "all") == 3

    assert f"Q1 is {state_word} and holds nothing up" in capsys.readouterr().err
    assert round_log(rounds).read_bytes() == before


# -- reopening ---------------------------------------------------------------------------

def test_a_parked_question_is_reopened_by_anyone_with_no_pointer(rounds, capsys):
    ask("B1,B2")
    rule("park:Q1", until="B3")
    capsys.readouterr()

    assert mark("Q1", "--reopen", "--as", "coder") == 0

    event = log.read(round_log(rounds))[-1]
    assert {key: event[key] for key in event if key not in ("v", "n", "at")} == {
        "actor": "coder", "type": "question.changed", "id": "Q1", "state": "open"}
    found = question(rounds)
    assert (found.state, found.until, found.blocks) == ("open", None, ("B1", "B2"))
    assert capsys.readouterr().out == "Q1: open   blocks: B1,B2\n"


def test_the_real_command_reopens_a_question(rounds, capsys):
    ask()
    rule("park:Q1")

    assert cli.main(["rounds", "mark", "proj-a/a-round", "Q1", "--reopen"]) == 0
    assert question(rounds).state == "open"


@pytest.mark.parametrize("item, state_word", [(None, "open"), ("Q1", "ruled"),
                                              ("withdraw:Q1", "withdrawn")])
def test_only_a_parked_question_is_reopened(rounds, capsys, item, state_word):
    ask()
    if item is not None:
        rule(item, text="It is so.")
    before = round_log(rounds).read_bytes()

    assert mark("Q1", "--reopen") == 3

    assert (f"Q1 is {state_word}, and only a parked question is reopened"
            in capsys.readouterr().err)
    assert round_log(rounds).read_bytes() == before


def test_a_question_whose_wait_is_over_reads_open_and_is_not_reopened(rounds, capsys):
    ask()
    rule("park:Q1", until="B2")
    log.append(round_log(rounds), "finish.done", {"stage": "B2", "tip": "0" * 40},
               actor="coder")
    before = round_log(rounds).read_bytes()

    assert mark("Q1", "--reopen") == 3

    assert "Q1 reads as open already: it was parked until B2" in capsys.readouterr().err
    assert round_log(rounds).read_bytes() == before


def test_a_reopened_question_is_in_the_index_as_open(rounds):
    ask()
    rule("park:Q1", until="B2")
    [parked] = index.read("proj-a", rounds, "a-round").items
    marking.reopen("proj-a/a-round", "Q1", actor="coder")
    [found] = index.read("proj-a", rounds, "a-round").items

    assert (parked.body["state"], parked.body["until"]) == ("parked", "B2")
    assert found.body["state"] == "open" and "until" not in found.body


# -- what can't be marked ----------------------------------------------------------------

@pytest.mark.parametrize("form", [["--blocks", "all"], ["--reopen"]])
def test_a_question_the_round_does_not_have_is_refused(rounds, capsys, form):
    ask()
    before = round_log(rounds).read_bytes()

    assert mark("Q2", *form) == 3

    err = capsys.readouterr().err
    assert "proj-a/a-round has no question Q2" in err
    assert "run: moxi rounds show proj-a/a-round" in err
    assert round_log(rounds).read_bytes() == before


@pytest.mark.parametrize("argv", [
    ["proj-a/a-round", "Q1"], ["proj-a/a-round", "Q1", "--blocks", "all", "--reopen"],
    ["proj-a/a-round", "--reopen"], ["proj-a", "P1", "--reopen"],
    ["proj-a/a-round", "P1", "--reopen"], ["proj-a/a-round", "q1", "--reopen"],
    ["proj-a/a-round", "Q1", "--blocks", "B2,,B3"],
    ["proj-a/a-round", "Q1", "--blocks", ""],
    ["proj-a/a-round", "Q1", "--reopen", "--record", "comms:12"],
    ["proj-a/A-Round", "Q1", "--reopen"]])
def test_a_mark_that_cant_be_read_is_a_usage_error(rounds, capsys, argv):
    ask()
    before = round_log(rounds).read_bytes()

    assert main(["mark", *argv]) == 2

    assert capsys.readouterr().out == ""
    assert round_log(rounds).read_bytes() == before


def test_a_round_that_was_never_opened_is_refused(rounds, capsys):
    assert main(["mark", "proj-a/c-round", "Q1", "--reopen"]) == 3

    assert "moxi rounds open proj-a/c-round" in capsys.readouterr().err
    with pytest.raises(NoRound):
        marking.widen("proj-a/c-round", "Q1", "all", actor="coder")


def test_help_says_what_mark_does(capsys):
    assert main(["mark", "--help"]) == 0

    out = " ".join(capsys.readouterr().out.split())
    for word in ("ROUND", "ID", "--blocks LIST|all", "--reopen", "--as NAME",
                 "narrow:Q<n>=LIST"):
        assert word in out


def test_a_mark_takes_only_the_rounds_lock(rounds, holder, capsys):
    ask()
    holder(write.project_lock("proj-a"))

    assert mark("Q1", "--blocks", "all") == 0

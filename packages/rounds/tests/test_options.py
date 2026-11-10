"""The options of a question: given when it is asked or added later, a key that keeps
its line, retiring, how many a question may have, and that they change nothing a
question holds up."""

from __future__ import annotations

import json

import pytest
from moxi import cli

from moxi_rounds import asking, index, log, opening, options, questions, ruling, state
from moxi_rounds.cli import main
from moxi_rounds.errors import BadArgument, NoRound, Refused

SAID = "Do agent-read times follow the settings?"
A = "a=prose yes, parsed rows no"
B = "b=everything"
C = "c=nothing"
D = "d=only what a person reads"
PAIRS = {"a": "prose yes, parsed rows no", "b": "everything", "c": "nothing",
         "d": "only what a person reads"}
ROUND = "proj-a/a-round"


@pytest.fixture
def rounds(tmp_path, add_project, capsys):
    """The rounds folder of the project `proj-a`, with the round `a-round` opened in it
    and the stages B1 and B2 declared."""
    add_project("proj-a", notes_root=tmp_path / "notes")
    opening.open_round(ROUND, description="a placeholder round", actor="planner",
                       flow="fix")
    folder = tmp_path / "notes" / "rounds"
    for stage in ("B1", "B2"):
        log.append(log.round_log(folder / "a-round"), "stage.declared",
                   {"id": stage, "repo": "proj-a", "branch": f"branch-{stage.lower()}"},
                   actor="planner")
    capsys.readouterr()
    return folder


def ask(*given, target=ROUND, blocks=None):
    """Ask a question of `target` with the options `given`, and return its id."""
    return asking.ask(target, SAID, origin="comms:11", blocks=blocks, actor="scout",
                      options=given).id


def fill(*argv, target=ROUND, question_id="Q1"):
    """Run `moxi rounds ask` on a question on record."""
    return main(["ask", target, question_id, *argv])


def path_of(rounds, target=ROUND):
    return (log.round_log(rounds / "a-round") if "/" in target else
            log.project_log(rounds))


def question(rounds, target=ROUND, question_id=None):
    wanted = question_id or ("Q1" if "/" in target else "P1")
    found = questions.find(state.fold(log.read(path_of(rounds, target))), wanted)
    assert found is not None
    return found


def last(path):
    event = log.read(path)[-1]
    return {key: event[key] for key in event if key not in ("v", "n", "at")}


def logs(rounds):
    """Every log file under the rounds folder, with its bytes."""
    return {str(path.relative_to(rounds)): path.read_bytes()
            for path in sorted(rounds.rglob("*.jsonl"))}


def held(rounds, target=ROUND):
    """What a gate reads of the question, as bytes: its state and what it holds up."""
    found = question(rounds, target)
    return json.dumps([found.state, found.blocks, found.until, found.ruling]).encode()


# -- options given when a question is asked ----------------------------------------------

def test_a_question_is_asked_with_its_options(rounds, capsys):
    assert main(["ask", ROUND, SAID, "--from", "comms:12", "--option", A, "--option", B,
                 "--option", C]) == 0

    event = last(path_of(rounds))
    assert event["type"] == "question.asked"
    assert event["options"] == {key: PAIRS[key] for key in "abc"}
    assert list(event["options"]) == ["a", "b", "c"]
    assert question(rounds).options == tuple((key, PAIRS[key]) for key in "abc")
    assert capsys.readouterr().out == (
        f"Q1: {SAID}\nblocks: all   from: comms:12\na: {PAIRS['a']}\nb: {PAIRS['b']}\n"
        f"c: {PAIRS['c']}\n")


def test_a_question_asked_bare_has_no_options_in_its_record(rounds):
    ask()

    assert "options" not in last(path_of(rounds))
    found = question(rounds)
    assert (found.options, found.retired, found.chose) == ((), (), None)


def test_a_projects_question_is_asked_with_its_options(rounds):
    assert main(["ask", "proj-a", SAID, "--from", "comms:12", "--option", A,
                 "--option", B]) == 0

    assert question(rounds, "proj-a").options == (("a", PAIRS["a"]), ("b", PAIRS["b"]))


@pytest.mark.parametrize("given", [[A], [A, B, C, D, "e=something else"]])
def test_a_question_asked_with_one_option_or_five_is_refused(rounds, capsys, given):
    before = logs(rounds)
    flags = [word for one in given for word in ("--option", one)]

    assert main(["ask", ROUND, SAID, "--from", "comms:12", *flags]) == 3

    err = capsys.readouterr().err
    assert f"has 2 to 4, retired ones not counted, and this would leave it {len(given)}" \
        in err
    assert "run: moxi rounds ask proj-a/a-round TEXT --from PTR --option K=TEXT" in err
    assert logs(rounds) == before


@pytest.mark.parametrize("given, problem", [
    (["a"], "it has no ="), (["=text"], "'' is not a key"), (["A=text"], "'A' is not"),
    (["a b=text"], "'a b' is not a key"), (["a_b=text"], "'a_b' is not a key"),
    (["abcdefghijklmnopq=text"], "is not a key"), (["a="], "its line says nothing"),
    (["a=  "], "its line says nothing"), (["a=one\ntwo"], "has a line break"),
    (["a=one\rtwo"], "has a line break"), ([A, "a=everything"], "given twice"),
    ([A, A], "given twice")])
def test_an_option_that_cant_be_read_is_a_usage_error(rounds, capsys, given, problem):
    before = logs(rounds)
    flags = [word for one in [*given, B] for word in ("--option", one)]

    assert main(["ask", ROUND, SAID, "--from", "comms:12", *flags]) == 2
    assert problem in capsys.readouterr().err
    ask(A, B)
    assert fill(*flags) == 2
    assert problem in capsys.readouterr().err

    assert len(logs(rounds)) == len(before) and "options" in last(path_of(rounds))
    assert question(rounds).options == (("a", PAIRS["a"]), ("b", PAIRS["b"]))


def test_a_key_is_letters_digits_and_dashes_and_its_line_is_kept_as_given(rounds):
    ask("2nd-choice= spaced = and equal ", "x=y")

    assert question(rounds).options == (("2nd-choice", " spaced = and equal "),
                                        ("x", "y"))


def test_retire_is_for_a_question_on_record(rounds, capsys):
    before = logs(rounds)

    assert main(["ask", ROUND, SAID, "--from", "comms:12", "--retire", "a"]) == 2

    assert "--retire takes an option out of a question on record" in \
        capsys.readouterr().err
    assert logs(rounds) == before


# -- options added to a question on record -----------------------------------------------

def test_options_are_added_to_a_question_on_record_by_anyone_with_no_pointer(
        rounds, capsys):
    ask()

    assert fill("--option", A, "--option", B, "--as", "helper") == 0

    assert last(path_of(rounds)) == {
        "actor": "helper", "type": "question.changed", "id": "Q1",
        "options": {"a": PAIRS["a"], "b": PAIRS["b"]}}
    assert question(rounds).options == (("a", PAIRS["a"]), ("b", PAIRS["b"]))
    assert capsys.readouterr().out == f"Q1: {SAID}\na: {PAIRS['a']}\nb: {PAIRS['b']}\n"


def test_the_real_command_adds_options(rounds, capsys):
    ask()

    assert cli.main(["rounds", "ask", ROUND, "Q1", "--option", A, "--option", B]) == 0

    assert capsys.readouterr().out.startswith(f"Q1: {SAID}\na: ")


def test_an_option_is_added_to_the_ones_a_question_has(rounds):
    ask(A, B)

    assert fill("--option", C) == 0
    assert fill("--option", D) == 0

    assert [key for key, _text in question(rounds).options] == ["a", "b", "c", "d"]
    assert last(path_of(rounds))["options"] == {"d": PAIRS["d"]}


def test_options_are_added_to_a_projects_question(rounds, capsys):
    ask(target="proj-a")

    assert fill("--option", A, "--option", B, target="proj-a", question_id="P1") == 0

    event = last(path_of(rounds, "proj-a"))
    assert (event["type"], event["id"]) == ("question.changed", "P1")
    assert question(rounds, "proj-a").options == (("a", PAIRS["a"]), ("b", PAIRS["b"]))


def test_options_are_added_to_a_parked_question(rounds):
    ask()
    ruling.rule(ROUND, item="park:Q1", record="comms:12", actor="planner")

    assert fill("--option", A, "--option", B) == 0

    found = question(rounds)
    assert (found.state, len(found.options)) == ("parked", 2)


@pytest.mark.parametrize("item, was", [("Q1", "ruled"), ("withdraw:Q1", "withdrawn")])
def test_a_ruled_or_withdrawn_question_takes_no_options(rounds, capsys, item, was):
    ask(A, B)
    ruling.rule(ROUND, item=item, text="Prose only.", record="comms:12",
                actor="planner")
    before = logs(rounds)

    assert fill("--option", C) == 3
    err = capsys.readouterr().err
    assert f"Q1 is {was}; options are added and retired only while a question is " \
           "open or parked" in err
    assert "run: moxi rounds show proj-a/a-round" in err
    assert fill("--retire", "a") == 3
    assert logs(rounds) == before


def test_a_question_left_with_one_option_or_five_is_refused_whole(rounds, capsys):
    ask()
    before = logs(rounds)

    assert fill("--option", A) == 3
    assert "this would leave it 1" in capsys.readouterr().err
    assert logs(rounds) == before
    ask(A, B, C, D, target=ROUND)
    before = logs(rounds)
    assert fill("--option", "e=something else", question_id="Q2") == 3
    err = capsys.readouterr().err
    assert "this would leave it 5" in err
    assert "run: moxi rounds ask proj-a/a-round Q2 --option K=TEXT... [--retire K]..." \
        in err
    assert logs(rounds) == before
    assert len(question(rounds, question_id="Q2").options) == 4


def test_a_question_the_log_does_not_hold_is_refused(rounds, capsys):
    ask()
    before = logs(rounds)

    assert fill("--option", A, "--option", B, question_id="Q7") == 3
    err = capsys.readouterr().err
    assert "proj-a/a-round has no question Q7" in err
    assert "run: moxi rounds show proj-a/a-round" in err
    assert fill("--option", A, "--option", B, target="proj-a", question_id="P1") == 3
    assert "run: moxi rounds list proj-a --questions" in capsys.readouterr().err
    assert logs(rounds) == before


def test_a_round_that_was_never_opened_is_refused(rounds):
    with pytest.raises(NoRound):
        options.fill("proj-a/c-round", "Q1", options=[A, B], actor="helper")


def test_an_id_with_nothing_to_change_or_with_what_asking_takes_is_a_usage_error(
        rounds, capsys):
    ask()
    before = logs(rounds)

    assert fill() == 2
    assert "nothing says what to change" in capsys.readouterr().err
    for flag, value in (("--from", "comms:12"), ("--record", "comms:12"),
                        ("--blocks", "B1")):
        assert fill("--option", A, "--option", B, flag, value) == 2
        assert f"{flag} is given when a question is asked" in capsys.readouterr().err
    with pytest.raises(BadArgument):
        options.fill(ROUND, "B1", options=[A, B], actor="helper")
    assert logs(rounds) == before


# -- a key keeps its text ----------------------------------------------------------------

def test_a_key_the_question_has_is_refused_with_another_text(rounds, capsys):
    ask(A, B)
    before = logs(rounds)

    assert fill("--option", "a=prose and parsed rows") == 3

    err = capsys.readouterr().err
    assert f"Q1 offers {PAIRS['a']!r} under a, and a key keeps its text" in err
    assert "run: moxi rounds ask proj-a/a-round Q1 --retire a --option K=TEXT" in err
    assert logs(rounds) == before


def test_an_option_given_again_with_its_own_line_writes_nothing(rounds, capsys):
    ask(A, B)
    before = logs(rounds)

    assert fill("--option", A) == 0
    assert logs(rounds) == before
    assert fill("--option", A, "--option", C) == 0

    assert last(path_of(rounds))["options"] == {"c": PAIRS["c"]}
    assert [key for key, _text in question(rounds).options] == ["a", "b", "c"]


# -- retiring ----------------------------------------------------------------------------

def test_an_option_is_retired_and_is_offered_no_longer(rounds, capsys):
    ask(A, B, C)

    assert fill("--retire", "c", "--as", "helper") == 0

    assert last(path_of(rounds)) == {"actor": "helper", "type": "question.changed",
                                     "id": "Q1", "retired": ["c"]}
    found = question(rounds)
    assert found.options == (("a", PAIRS["a"]), ("b", PAIRS["b"]))
    assert found.retired == ("c",)
    assert capsys.readouterr().out == (
        f"Q1: {SAID}\na: {PAIRS['a']}\nb: {PAIRS['b']}\nretired: c\n")


def test_a_retire_and_an_add_in_one_call_correct_a_line(rounds):
    ask(A, B)

    assert fill("--retire", "b", "--option", "c=everything a person reads") == 0

    assert last(path_of(rounds)) == {
        "actor": last(path_of(rounds))["actor"], "type": "question.changed", "id": "Q1",
        "options": {"c": "everything a person reads"}, "retired": ["b"]}
    found = question(rounds)
    assert found.options == (("a", PAIRS["a"]), ("c", "everything a person reads"))
    assert found.retired == ("b",)
    assert found.changes[-1].options == (("c", "everything a person reads"),)
    assert found.changes[-1].retired == ("b",)


@pytest.mark.parametrize("again", [C, "c=something else"])
def test_a_retired_key_is_refused_again_with_any_text(rounds, capsys, again):
    ask(A, B, C)
    assert fill("--retire", "c") == 0
    before = logs(rounds)
    capsys.readouterr()

    assert fill("--option", again) == 3

    err = capsys.readouterr().err
    assert "the key c was retired from Q1 and is never used again, with any line" in err
    assert "run: moxi rounds ask proj-a/a-round Q1 --option K=TEXT" in err
    assert logs(rounds) == before


def test_a_key_is_not_retired_and_added_again_in_one_call(rounds, capsys):
    ask(A, B, C)
    before = logs(rounds)

    assert fill("--retire", "c", "--option", "c=something else") == 3
    assert fill("--retire", "c", "--option", C) == 0

    assert question(rounds).retired == ("c",)
    assert len(logs(rounds)) == len(before)
    assert [key for key, _text in question(rounds).options] == ["a", "b"]


def test_a_retire_of_a_key_the_question_never_had_or_already_retired_is_refused(
        rounds, capsys):
    ask(A, B, C)
    assert fill("--retire", "c") == 0
    before = logs(rounds)
    capsys.readouterr()

    assert fill("--retire", "d") == 3
    err = capsys.readouterr().err
    assert "Q1 never had an option d; its options are a, b" in err
    assert fill("--retire", "c", "--option", D) == 3
    assert "the key c of Q1 is retired already" in capsys.readouterr().err
    assert fill("--retire", "d", "--option", D) == 3
    assert fill("--retire", "a", "--retire", "a", "--option", D) == 2
    assert fill("--retire", "A") == 2

    assert logs(rounds) == before


def test_a_retire_that_would_leave_one_option_or_none_is_refused(rounds, capsys):
    ask(A, B)
    before = logs(rounds)

    assert fill("--retire", "b") == 3
    assert "this would leave it 1" in capsys.readouterr().err
    assert fill("--retire", "a", "--retire", "b") == 3
    assert "this would leave it 0" in capsys.readouterr().err

    assert logs(rounds) == before


def test_retired_keys_do_not_count_among_a_questions_options(rounds):
    ask(A, B, C, D)

    assert fill("--retire", "a", "--option", "e=something else") == 0
    assert fill("--retire", "b", "--option", "f=another line") == 0

    found = question(rounds)
    assert [key for key, _text in found.options] == ["c", "d", "e", "f"]
    assert found.retired == ("a", "b")


def test_a_refusal_names_everything_that_stopped_it(rounds, capsys):
    ask(A, B, C)
    assert fill("--retire", "c") == 0
    capsys.readouterr()

    assert fill("--retire", "d", "--option", C, "--option", "a=another line") == 3

    err = capsys.readouterr().err
    for said in ("the key c was retired", "a key keeps its text",
                 "Q1 never had an option d"):
        assert said in err


# -- options change nothing a question holds up ------------------------------------------

@pytest.mark.parametrize("target, blocks", [(ROUND, None), (ROUND, "B1"),
                                            (ROUND, "none"), ("proj-a", None)])
def test_no_options_call_changes_a_questions_state_or_what_it_holds_up(
        rounds, target, blocks):
    ask(target=target, blocks=blocks)
    question_id = "Q1" if "/" in target else "P1"
    before = held(rounds, target)
    slug = "a-round" if "/" in target else None
    row = index.read("proj-a", rounds, slug).items

    for argv in (["--option", A, "--option", B], ["--option", C],
                 ["--retire", "a", "--option", D], ["--retire", "b", "--option", "e=x"],
                 ["--option", C]):
        assert fill(*argv, target=target, question_id=question_id) == 0
        assert held(rounds, target) == before
        assert index.read("proj-a", rounds, slug).items == row

    found = question(rounds, target)
    assert found.state == "open" and questions.current(found, state.State()) == "open"
    assert len(found.changes) == 4


def test_no_options_call_changes_a_parked_question_or_what_it_waits_for(rounds):
    ask(blocks="B1,B2")
    ruling.rule(ROUND, item="park:Q1", until="B2", record="comms:12", actor="planner")
    before = held(rounds)

    assert fill("--option", A, "--option", B, "--option", C) == 0
    assert held(rounds) == before
    assert fill("--retire", "c") == 0
    assert held(rounds) == before

    found = question(rounds)
    assert (found.state, found.blocks, found.until, found.ruling) == (
        "parked", ("B1", "B2"), "B2", "C1")


def test_a_refused_options_call_writes_nothing(rounds):
    ask(A, B)
    before = logs(rounds)

    with pytest.raises(Refused):
        options.fill(ROUND, "Q1", options=["a=another line", C], retire=["b"],
                     actor="helper")

    assert logs(rounds) == before


def test_help_says_how_options_are_given(capsys):
    assert main(["ask", "--help"]) == 0

    out = " ".join(capsys.readouterr().out.split())
    for word in ("TEXT|ID", "--option K=TEXT", "--retire K", "two to four",
                 "never used again"):
        assert word in out

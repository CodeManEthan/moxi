"""`moxi rounds ask`: a question on a round or on a project, the six things it is
recorded with, and what is refused."""

from __future__ import annotations

import pytest
from moxi import cli

from moxi_rounds import asking, index, log, opening, state, write
from moxi_rounds.cli import main
from moxi_rounds.errors import BadArgument, BadName, NoRound, Refused

AT = "2026-01-02T03:04:05Z"
SAID = "Do agent-read times follow the settings?"


@pytest.fixture
def rounds(tmp_path, add_project, capsys):
    """The rounds folder of the project `proj-a`, with the rounds `a-round` and
    `b-round` opened in it; `a-round` has the stages B1 and B2."""
    add_project("proj-a", notes_root=tmp_path / "notes")
    for slug in ("a-round", "b-round"):
        opening.open_round(f"proj-a/{slug}", description="a placeholder round",
                           actor="planner", flow="fix")
    folder = tmp_path / "notes" / "rounds"
    for stage in ("B1", "B2"):
        log.append(log.round_log(folder / "a-round"), "stage.declared",
                   {"id": stage, "repo": "proj-a", "branch": f"branch-{stage.lower()}"},
                   actor="planner")
    capsys.readouterr()
    return folder


def run(target, *argv, text=SAID, origin="comms:12"):
    """Run `moxi rounds ask` on `target`, with a sentence and an origin unless the
    origin is None."""
    pointer = [] if origin is None else ["--from", origin]
    return main(["ask", target, text, *pointer, *argv])


def round_log(rounds, slug="a-round"):
    return log.round_log(rounds / slug)


def asked(path):
    return [event for event in log.read(path) if event["type"] == "question.asked"]


def logs(rounds):
    """Every log file under the rounds folder, with its bytes."""
    return {str(path.relative_to(rounds)): path.read_bytes()
            for path in sorted(rounds.rglob("*.jsonl"))}


# -- a round's question ------------------------------------------------------------------

def test_a_question_is_recorded_in_the_rounds_log_with_its_six_things(rounds, capsys):
    assert run("proj-a/a-round", "--as", "scout") == 0

    [event] = asked(round_log(rounds))
    assert {key: event[key] for key in event if key not in ("v", "n")} == {
        "actor": "scout", "type": "question.asked", "at": event["at"], "id": "Q1",
        "text": SAID, "from": "comms:12", "blocks": "all", "state": "open",
        "asked_by": "scout", "asked_at": event["at"]}
    assert not log.exists(log.project_log(rounds))
    out, err = capsys.readouterr()
    assert out == f"Q1: {SAID}\nblocks: all   from: comms:12\n" and err == ""


def test_a_rounds_state_holds_the_question(rounds):
    asking.ask("proj-a/a-round", SAID, origin="file:design.md#read-times", blocks="B2",
               actor="scout", at=AT)

    assert state.fold(log.read(round_log(rounds))).questions == [state.Question(
        id="Q1", text=SAID, origin="file:design.md#read-times", blocks=("B2",), state="open",
        by="scout", at=AT)]


def test_the_real_command_asks_a_question(rounds, capsys):
    assert cli.main(["rounds", "ask", "proj-a/a-round", SAID, "--from", "comms:12"]) == 0

    assert capsys.readouterr().out.startswith(f"Q1: {SAID}\n")


def test_the_asker_is_the_one_named_with_as_else_the_os_user(rounds, monkeypatch):
    monkeypatch.setattr("getpass.getuser", lambda: "alice")
    assert run("proj-a/a-round", "--as", "scout") == 0
    assert run("proj-a/a-round") == 0

    assert [event["asked_by"] for event in asked(round_log(rounds))] == ["scout",
                                                                         "alice"]


def test_a_question_is_asked_under_the_projects_stored_key(rounds):
    assert run("PROJ-A/a-round") == 0

    assert len(asked(round_log(rounds))) == 1
    assert not write.round_lock("PROJ-A", "a-round").exists()


def test_help_says_what_ask_does(capsys):
    assert main(["ask", "--help"]) == 0

    out = " ".join(capsys.readouterr().out.split())
    for word in ("ROUND|PROJECT", "TEXT", "--from PTR", "--record PTR",
                 "--blocks LIST|none", "--as NAME", "file:design.md#<anchor>",
                 "--adopt P<n>", "--move ROUND:Q<n>"):
        assert word in out


# -- the origin --------------------------------------------------------------------------

@pytest.mark.parametrize("pointer", ["comms:4611686018427393782",
                                     "https://example.com/thread/7#note",
                                     "file:design.md#read-times", "file:reports/pass-1.md#l2"])
def test_an_origin_of_each_shape_is_recorded_as_given(rounds, pointer):
    assert run("proj-a/a-round", origin=pointer) == 0

    [event] = asked(round_log(rounds))
    assert event["from"] == pointer and "record" not in event


def test_the_operators_own_question_has_his_pointer_as_its_origin(rounds, capsys):
    assert run("proj-a/a-round", "--record", "comms:77", origin=None) == 0

    [event] = asked(round_log(rounds))
    assert (event["from"], event["record"]) == ("comms:77", "comms:77")
    [found] = state.fold(log.read(round_log(rounds))).questions
    assert (found.origin, found.record) == ("comms:77", "comms:77")


@pytest.mark.parametrize("target", ["proj-a/a-round", "proj-a"])
def test_a_question_with_no_origin_is_refused_and_the_logs_are_untouched(
        rounds, capsys, target):
    before = logs(rounds)

    assert run(target, origin=None) == 3

    out, err = capsys.readouterr()
    assert out == "" and "says where it came from" in err
    assert f"run: moxi rounds ask {target} TEXT --from PTR" in err
    assert logs(rounds) == before


def test_no_origin_is_refused_wherever_the_command_is_run(rounds, tmp_path, monkeypatch):
    worktree = tmp_path / "a-build's-worktree"
    worktree.mkdir()
    monkeypatch.chdir(worktree)

    with pytest.raises(Refused, match="says where it came from"):
        asking.ask("proj-a/a-round", SAID, actor="scout")


@pytest.mark.parametrize("pointer", ["he asked in chat", "comms:twelve", "comms:",
                                     "http://example.com/x", "file:design.md",
                                     "file:../other/design.md#q", ""])
@pytest.mark.parametrize("flag", ["--from", "--record"])
def test_an_origin_of_no_known_shape_is_refused_and_the_logs_are_untouched(
        rounds, capsys, pointer, flag):
    before = logs(rounds)

    assert main(["ask", "proj-a/a-round", SAID, flag, pointer]) == 3

    out, err = capsys.readouterr()
    assert out == "" and "is not a pointer" in err and "run: moxi rounds ask" in err
    assert logs(rounds) == before


def test_an_origin_and_the_operators_pointer_together_are_a_usage_error(rounds, capsys):
    before = logs(rounds)

    assert run("proj-a/a-round", "--record", "comms:77") == 2

    assert "--from and --record" in capsys.readouterr().err
    assert logs(rounds) == before


def test_ask_takes_no_build(rounds, capsys):
    assert main(["ask", "proj-a/a-round", SAID, "--build", "B1"]) == 2

    assert "unrecognized arguments" in capsys.readouterr().err


# -- the sentence ------------------------------------------------------------------------

@pytest.mark.parametrize("text", ["Is it so?\nAnd this?", "Is it so?\r\nAnd this?",
                                  "Is it so?\n", "\rIs it so?"])
def test_a_sentence_with_a_line_break_is_refused(rounds, capsys, text):
    before = logs(rounds)

    assert run("proj-a/a-round", text=text) == 2

    assert "line break" in capsys.readouterr().err
    assert logs(rounds) == before


@pytest.mark.parametrize("text", ["", "   ", "\t"])
def test_a_question_with_no_sentence_is_a_usage_error(rounds, capsys, text):
    assert run("proj-a/a-round", text=text) == 2

    assert "needs its sentence" in capsys.readouterr().err


def test_the_sentence_is_kept_as_given(rounds, capsys):
    said = "  Does é follow →, or \"neither\"?  "

    assert run("proj-a/a-round", text=said) == 0

    assert asked(round_log(rounds))[0]["text"] == said
    assert capsys.readouterr().out.startswith(f"Q1: {said}\n")


# -- what it holds up --------------------------------------------------------------------

@pytest.mark.parametrize("flags, recorded, printed", [
    ([], "all", "all"), (["--blocks", "all"], "all", "all"),
    (["--blocks", "none"], "none", "none"), (["--blocks", "B2"], ["B2"], "B2"),
    (["--blocks", "B2,B1"], ["B2", "B1"], "B2,B1")])
def test_a_question_holds_up_every_stage_the_stages_it_names_or_none(
        rounds, capsys, flags, recorded, printed):
    assert run("proj-a/a-round", *flags) == 0

    assert asked(round_log(rounds))[0]["blocks"] == recorded
    assert f"blocks: {printed}   from:" in capsys.readouterr().out


def test_a_stage_the_round_does_not_have_is_refused(rounds, capsys):
    before = logs(rounds)

    assert run("proj-a/a-round", "--blocks", "B1,B3,B7") == 3

    err = capsys.readouterr().err
    assert "B3, B7 is not a stage of proj-a/a-round: its stages are B1, B2" in err
    assert logs(rounds) == before


def test_a_round_with_no_stage_takes_no_list(rounds, capsys):
    assert run("proj-a/b-round", "--blocks", "B1") == 3

    assert "it has no stage yet" in capsys.readouterr().err
    assert run("proj-a/b-round", "--blocks", "none") == 0


@pytest.mark.parametrize("blocks", ["", "B", "b2", "B0", "B2,,B3", "B2,B2", "None"])
def test_a_stage_list_that_cant_be_read_is_a_usage_error(rounds, capsys, blocks):
    before = logs(rounds)

    assert run("proj-a/a-round", "--blocks", blocks) == 2

    assert "--blocks" in capsys.readouterr().err
    assert logs(rounds) == before


def test_a_refusal_names_everything_that_stopped_it(rounds, capsys):
    assert run("proj-a/a-round", "--blocks", "B9", origin="nowhere") == 3

    err = capsys.readouterr().err
    assert "is not a pointer" in err and "B9 is not a stage" in err


# -- a project's question ----------------------------------------------------------------

def test_a_question_on_a_project_goes_on_the_projects_list(rounds, capsys):
    before = round_log(rounds).read_bytes()

    assert run("proj-a", "--as", "scout") == 0

    [event] = asked(log.project_log(rounds))
    assert (event["id"], event["blocks"], event["state"], event["asked_by"]) == (
        "P1", "none", "open", "scout")
    assert round_log(rounds).read_bytes() == before
    assert capsys.readouterr().out == f"P1: {SAID}\nblocks: none   from: comms:12\n"
    [found] = state.fold(log.read(log.project_log(rounds))).questions
    assert (found.id, found.blocks) == ("P1", "none")


def test_a_projects_question_holds_up_no_stage(rounds, capsys):
    assert run("proj-a", "--blocks", "none") == 0
    for blocks in ("all", "B1"):
        assert run("proj-a", "--blocks", blocks) == 2
        assert "holds up no stage" in capsys.readouterr().err

    assert len(asked(log.project_log(rounds))) == 1


def test_ids_count_for_each_round_and_for_the_project_on_their_own(rounds):
    for target in ("proj-a/a-round", "proj-a/a-round", "proj-a/b-round", "proj-a",
                   "proj-a"):
        assert run(target) == 0

    assert [event["id"] for event in asked(round_log(rounds))] == ["Q1", "Q2"]
    assert [event["id"] for event in asked(round_log(rounds, "b-round"))] == ["Q1"]
    assert [event["id"] for event in asked(log.project_log(rounds))] == ["P1", "P2"]


def test_an_id_is_one_past_the_highest_the_log_holds(rounds):
    log.append(round_log(rounds), "question.asked",
               {"id": "Q90", "text": SAID, "from": "comms:12", "blocks": "all",
                "state": "open", "asked_by": "planner", "asked_at": AT},
               actor="planner")

    assert run("proj-a/a-round") == 0

    assert asked(round_log(rounds))[-1]["id"] == "Q91"


# -- the index, and what can't be asked of ------------------------------------------------

def test_an_open_question_is_in_the_index(rounds):
    asking.ask("proj-a/a-round", SAID, origin="comms:12", actor="scout", at=AT)
    asking.ask("proj-a", SAID, origin="comms:13", actor="scout", at=AT)

    [found] = index.read("proj-a", rounds, "a-round").items
    assert (found.kind, found.id, found.body) == (
        "question", "Q1", {"text": SAID, "from": "comms:12", "blocks": "all",
                           "state": "open", "by": "scout", "at": AT})
    [listed] = index.read("proj-a", rounds).items
    assert (listed.kind, listed.id, listed.body["blocks"]) == ("question", "P1", "none")


def test_a_round_that_was_never_opened_is_refused(rounds, capsys):
    before = logs(rounds)

    assert run("proj-a/c-round") == 3

    assert "moxi rounds open proj-a/c-round" in capsys.readouterr().err
    with pytest.raises(NoRound):
        asking.ask("proj-a/c-round", SAID, origin="comms:12", actor="scout")
    assert logs(rounds) == before and not (rounds / "c-round").exists()


def test_a_project_the_projects_list_does_not_hold_is_refused(rounds, capsys):
    assert run("proj-b") == 3
    assert run("proj-b/a-round") == 3

    assert "there is no project 'proj-b'" in capsys.readouterr().err


@pytest.mark.parametrize("target", ["", " ", "proj-a/", "proj-a/A-Round", "proj-a/a/b"])
def test_a_target_that_names_nothing_is_a_usage_error(rounds, capsys, target):
    assert run(target) == 2
    with pytest.raises((BadArgument, BadName)):
        asking.ask(target, SAID, origin="comms:12", actor="scout")


def test_a_rounds_question_takes_only_the_rounds_lock(rounds, holder, capsys):
    holder(write.project_lock("proj-a"))

    assert run("proj-a/a-round") == 0


def test_a_projects_question_takes_only_the_projects_lock(rounds, holder, capsys):
    holder(write.round_lock("proj-a", "a-round"))

    assert run("proj-a") == 0

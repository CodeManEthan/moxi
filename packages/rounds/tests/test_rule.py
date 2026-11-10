"""`moxi rounds rule`: a plain ruling on a round, a standing one in the project's
register, the rulings of a question (its answer, a parking, a narrowing, a withdrawal),
what each is recorded with, and what is refused."""

from __future__ import annotations

import pytest
from moxi import cli

from moxi_rounds import asking, index, log, opening, questions, ruling, state, write
from moxi_rounds.cli import main
from moxi_rounds.errors import BadArgument, Locked, NoRound, Refused

AT = "2026-01-02T03:04:05Z"
SAID = "Usage is not calculated in the status line; it may be shown there."


@pytest.fixture
def rounds(tmp_path, add_project, capsys):
    """The rounds folder of the project `proj-a`, with the rounds `a-round` and
    `b-round` opened in it."""
    add_project("proj-a", notes_root=tmp_path / "notes")
    for slug in ("a-round", "b-round"):
        opening.open_round(f"proj-a/{slug}", description="a placeholder round",
                           actor="planner", flow="fix")
    capsys.readouterr()
    return tmp_path / "notes" / "rounds"


def run(target, *argv, text=SAID, record="comms:12"):
    """Run `moxi rounds rule` on `target`, with a sentence and a pointer unless one is
    None."""
    said = [] if text is None else ["--text", text]
    pointer = [] if record is None else ["--record", record]
    return main(["rule", target, *said, *pointer, *argv])


def printed(ruling_id, said, holder="proj-a/a-round", *, question=None):
    """What `rule` prints for one ruling: the sentence as recorded, the question it
    answers, and how whoever recorded it says the operator was shown it."""
    answers = "" if question is None else f"in answer to {question[0]}: {question[1]}\n"
    return (f"Recorded as {ruling_id}: {said}\n{answers}"
            'show him this under "Recorded as" in your next message, then:\n'
            f"  moxi rounds mark {holder} {ruling_id} --shown --record "
            "<pointer to that message>\n")


def round_log(rounds, slug="a-round"):
    return log.round_log(rounds / slug)


def rulings(path):
    return [event for event in log.read(path) if event["type"] == "ruling.recorded"]


def logs(rounds):
    """Every log file under the rounds folder, with its bytes."""
    return {str(path.relative_to(rounds)): path.read_bytes()
            for path in sorted(rounds.rglob("*.jsonl"))}


# -- a round's ruling --------------------------------------------------------------------

def test_a_plain_ruling_is_recorded_in_the_rounds_log(rounds, capsys):
    assert run("proj-a/a-round", "--as", "planner") == 0

    [event] = rulings(round_log(rounds))
    assert {key: event[key] for key in event if key not in ("v", "n", "at")} == {
        "actor": "planner", "type": "ruling.recorded", "id": "C1", "text": SAID,
        "record": "comms:12", "kind": "substance", "stages": "all"}
    assert not log.exists(log.project_log(rounds))
    out, err = capsys.readouterr()
    assert out == printed("C1", SAID) and err == ""


def test_a_rounds_state_holds_its_rulings(rounds):
    ruling.rule("proj-a/a-round", text=SAID, record="comms:12", actor="planner",
                reason="it is not ours to work out", stages="B2,B3", at=AT)

    assert state.fold(log.read(round_log(rounds))).rulings == [state.Ruling(
        id="C1", text=SAID, record="comms:12", reason="it is not ours to work out",
        kind=ruling.SUBSTANCE, rules=None, stages=("B2", "B3"), scope=(),
        actor="planner", at=AT)]


def test_the_real_command_records_a_ruling(rounds, capsys):
    assert cli.main(["rounds", "rule", "proj-a/a-round", "--text", SAID, "--record",
                     "comms:12"]) == 0

    assert capsys.readouterr().out == printed("C1", SAID)


def test_the_sentence_is_printed_as_recorded_byte_for_byte(rounds, capsys):
    said = "  Two lines,\n\tthe second indented; \u00e9 and \u2192 kept.  "

    assert run("proj-a/a-round", text=said) == 0

    [event] = rulings(round_log(rounds))
    assert event["text"] == said
    assert capsys.readouterr().out == printed("C1", said)


def test_a_reason_is_kept_and_left_out_when_none_is_given(rounds):
    assert run("proj-a/a-round", "--reason", "he reads it there") == 0
    assert run("proj-a/a-round") == 0

    first, second = rulings(round_log(rounds))
    assert first["reason"] == "he reads it there" and "reason" not in second


def test_a_ruling_on_a_round_is_recorded_under_the_projects_stored_key(rounds, capsys):
    assert run("PROJ-A/a-round") == 0

    assert len(rulings(round_log(rounds))) == 1
    assert not write.round_lock("PROJ-A", "a-round").exists()


def test_the_one_named_with_as_records_it_else_the_os_user(rounds, monkeypatch):
    monkeypatch.setattr("getpass.getuser", lambda: "alice")
    assert run("proj-a/a-round", "--as", "planner") == 0
    assert run("proj-a/a-round") == 0

    assert [event["actor"] for event in rulings(round_log(rounds))] == ["planner",
                                                                        "alice"]


def test_help_says_what_rule_does(capsys):
    assert main(["rule", "--help"]) == 0

    out = " ".join(capsys.readouterr().out.split())
    for word in ("ROUND|PROJECT", "[ITEM]", "--record PTR", "--text TEXT",
                 "--chose K", "--reason TEXT", "--standing", "--scope SPEC",
                 "--stages LIST",
                 "--until WHAT", "--as NAME", "comms:<digits>", "park:Q<n>",
                 "narrow:Q<n>=LIST", "withdraw:Q<n>", "--supersedes C<n>|S<n>",
                 "revoke:C<n>", "revoke:S<n>"):
        assert word in out


# -- a standing ruling -------------------------------------------------------------------

def test_a_standing_ruling_from_a_round_goes_in_the_projects_register(rounds, capsys):
    before = round_log(rounds).read_bytes()

    assert run("proj-a/a-round", "--standing", "--as", "planner") == 0

    [event] = rulings(log.project_log(rounds))
    assert {key: event[key] for key in event if key not in ("v", "n", "at")} == {
        "actor": "planner", "type": "ruling.recorded", "id": "S1", "text": SAID,
        "record": "comms:12", "kind": "substance"}
    assert round_log(rounds).read_bytes() == before
    assert capsys.readouterr().out == printed("S1", SAID, "proj-a")


def test_a_ruling_on_a_project_is_standing_with_no_round(rounds, capsys):
    assert run("proj-a") == 0
    assert run("PROJ-A", "--standing", text="Named again, with the flag.") == 0

    assert [event["id"] for event in rulings(log.project_log(rounds))] == ["S1", "S2"]
    assert capsys.readouterr().out == (
        printed("S1", SAID, "proj-a")
        + printed("S2", "Named again, with the flag.", "proj-a"))
    assert rulings(round_log(rounds)) == []
    assert not write.round_lock("proj-a", "a-round").read_bytes()


def test_a_projects_state_holds_the_register(rounds):
    ruling.rule("proj-a", text=SAID, record="https://example.com/pages/7",
                actor="planner", scope="proj-a:packages/ui/**,proj-b", at=AT)

    assert state.fold(log.read(log.project_log(rounds))).rulings == [state.Ruling(
        id="S1", text=SAID, record="https://example.com/pages/7", reason=None,
        kind=ruling.SUBSTANCE, rules=None, stages=None,
        scope=({"repo": "proj-a", "path": "packages/ui/**"}, {"repo": "proj-b"}),
        actor="planner", at=AT)]


def test_a_project_with_no_rounds_folder_yet_gets_its_register(
        tmp_path, add_project, capsys):
    add_project("proj-b", notes_root=tmp_path / "other")
    capsys.readouterr()

    assert run("proj-b") == 0

    assert [event["id"] for event in rulings(
        log.project_log(tmp_path / "other" / "rounds"))] == ["S1"]


def test_a_recorded_ruling_brings_the_index_up_to_its_log(rounds):
    assert run("proj-a/a-round") == 0
    assert run("proj-a/a-round", "--standing") == 0

    homes = [index.folders.Home("proj-a", rounds)]
    assert index.disagreements(homes) == []


# -- ids ---------------------------------------------------------------------------------

def test_ids_count_for_each_round_and_for_the_project_on_their_own(rounds, capsys):
    for target, flags in [("proj-a/a-round", []), ("proj-a/a-round", []),
                          ("proj-a/b-round", []), ("proj-a/a-round", ["--standing"]),
                          ("proj-a", []), ("proj-a/b-round", ["--standing"]),
                          ("proj-a/a-round", []), ("proj-a/b-round", [])]:
        assert run(target, *flags) == 0

    assert [event["id"] for event in rulings(round_log(rounds))] == ["C1", "C2", "C3"]
    assert [event["id"] for event in rulings(round_log(rounds, "b-round"))] == ["C1",
                                                                                "C2"]
    assert [event["id"] for event in rulings(log.project_log(rounds))] == ["S1", "S2",
                                                                           "S3"]
    assert [line.split(":")[0].split()[-1]
            for line in capsys.readouterr().out.splitlines()
            if line.startswith("Recorded as ")] == [
        "C1", "C2", "C1", "S1", "S2", "S3", "C3", "C2"]


def test_an_id_is_one_past_the_highest_the_log_holds(rounds):
    log.append(round_log(rounds), "ruling.recorded",
               {"id": "C7", "text": "from another machine", "record": "comms:3",
                "kind": "process", "rules": "skip:retro"}, actor="planner")

    assert run("proj-a/a-round") == 0

    assert [event["id"] for event in rulings(round_log(rounds))] == ["C7", "C8"]


# -- the pointer -------------------------------------------------------------------------

@pytest.mark.parametrize("pointer", [
    "comms:4611686018427393782", "0a1b2c3d@2026-01-02T03:04Z",
    "https://example.com/pages/7#answers", "file:design.md#rulings"])
def test_a_pointer_of_each_shape_is_recorded_as_given(rounds, pointer):
    assert run("proj-a/a-round", record=pointer) == 0
    assert run("proj-a", record=pointer) == 0

    assert rulings(round_log(rounds))[0]["record"] == pointer
    assert rulings(log.project_log(rounds))[0]["record"] == pointer


@pytest.mark.parametrize("pointer", ["he said so in chat", "comms:twelve",
                                     "0a1b2c3d@yesterday", "http://example.com/pages/7",
                                     "file:design.md", "file:../b-round/design.md#x"])
@pytest.mark.parametrize("target", [["proj-a/a-round"], ["proj-a/a-round", "--standing"],
                                    ["proj-a"]])
def test_a_pointer_of_no_known_shape_is_refused_and_the_logs_are_untouched(
        rounds, capsys, pointer, target):
    before = logs(rounds)

    assert run(*target, record=pointer) == 3

    out, err = capsys.readouterr()
    assert out == "" and "refused, with nothing written" in err
    assert repr(pointer) in err and "comms:<digits>" in err
    standing = " --standing" if "--standing" in target else ""
    assert f"run: moxi rounds rule {target[0]} --text TEXT --record PTR{standing}" in err
    assert logs(rounds) == before


@pytest.mark.parametrize("target", [["proj-a/a-round"], ["proj-a/a-round", "--standing"],
                                    ["proj-a"]])
def test_a_ruling_with_no_pointer_is_refused_and_the_logs_are_untouched(
        rounds, capsys, target):
    before = logs(rounds)

    assert run(*target, record=None) == 3

    out, err = capsys.readouterr()
    assert out == "" and "needs a pointer to where he said it" in err
    assert "--record PTR" in err
    assert logs(rounds) == before
    with pytest.raises(Refused):
        ruling.rule(target[0], text=SAID, actor="planner")


def test_rule_takes_no_by_and_one_pointer(rounds, capsys):
    before = logs(rounds)

    assert run("proj-a/a-round", "--by", "operator") == 2
    assert "unrecognized arguments" in capsys.readouterr().err
    # A second --record replaces the first: one call records one pointer.
    assert run("proj-a/a-round", "--record", "comms:13") == 0

    assert logs(rounds) != before
    assert [event["record"] for event in rulings(round_log(rounds))] == ["comms:13"]


# -- what it applies to ------------------------------------------------------------------

def test_a_ruling_applies_to_every_stage_or_to_the_stages_it_names(rounds):
    assert run("proj-a/a-round") == 0
    assert run("proj-a/a-round", "--stages", "B2,B3") == 0
    assert run("proj-a/a-round", "--stages", "B12") == 0

    assert [event["stages"] for event in rulings(round_log(rounds))] == [
        "all", ["B2", "B3"], ["B12"]]


def test_a_named_stage_need_not_be_declared_yet(rounds):
    log.append(round_log(rounds), "stage.declared",
               {"id": "B1", "repo": "proj-a", "branch": "a-branch"}, actor="planner")

    assert run("proj-a/a-round", "--stages", "B1,B4") == 0

    assert rulings(round_log(rounds))[0]["stages"] == ["B1", "B4"]


@pytest.mark.parametrize("stages", ["", "B", "b2", "B0", "B02", "2", "B2,,B3", "B2;B3",
                                    "B2,B2", "B2 B3", "all"])
def test_a_stage_list_that_cant_be_read_is_a_usage_error(rounds, capsys, stages):
    before = logs(rounds)

    assert run("proj-a/a-round", "--stages", stages) == 2

    assert "--stages B2,B3" in capsys.readouterr().err
    assert logs(rounds) == before
    with pytest.raises(BadArgument):
        ruling.parse_stages(stages)


def test_a_scope_names_repos_and_paths(rounds):
    assert run("proj-a/a-round", "--standing", "--scope", "proj-a:packages/ui/**") == 0
    assert run("proj-a", "--scope", "proj-a,proj-b:docs/*.md,proj-b:src/**/*.py") == 0
    assert run("proj-a") == 0

    first, second, third = rulings(log.project_log(rounds))
    assert first["scope"] == [{"repo": "proj-a", "path": "packages/ui/**"}]
    assert second["scope"] == [{"repo": "proj-a"},
                               {"repo": "proj-b", "path": "docs/*.md"},
                               {"repo": "proj-b", "path": "src/**/*.py"}]
    assert "scope" not in third


@pytest.mark.parametrize("scope", ["", ":packages/**", "proj-a:", "proj a:src/**",
                                   "proj-a,,proj-b", "proj-a:/abs/**", "proj-a:../x/**",
                                   "proj-a:src//x", "proj-a/b:src/**", "proj-a,proj-a",
                                   ".proj:src/**"])
def test_a_scope_that_cant_be_read_is_a_usage_error(rounds, capsys, scope):
    before = logs(rounds)

    assert run("proj-a", "--scope", scope) == 2

    assert "--scope KEY:GLOB,KEY" in capsys.readouterr().err
    assert logs(rounds) == before
    with pytest.raises(BadArgument):
        ruling.parse_scope(scope)


def test_a_scope_is_for_a_standing_ruling_and_stages_for_a_rounds_own(rounds, capsys):
    before = logs(rounds)

    assert run("proj-a/a-round", "--scope", "proj-a") == 2
    assert "--standing" in capsys.readouterr().err
    assert run("proj-a/a-round", "--standing", "--stages", "B2") == 2
    assert "--stages" in capsys.readouterr().err
    assert run("proj-a", "--stages", "B2") == 2
    assert "--stages" in capsys.readouterr().err
    assert logs(rounds) == before


# -- the sentence ------------------------------------------------------------------------

def test_a_sentence_over_400_characters_is_recorded_with_a_note(rounds, capsys):
    at_the_mark, past_it = "x" * 400, "y" * 401

    assert run("proj-a/a-round", text=at_the_mark) == 0
    out, err = capsys.readouterr()
    assert out == printed("C1", at_the_mark) and err == ""

    assert run("proj-a/a-round", text=past_it) == 0
    out, err = capsys.readouterr()
    assert out == printed("C2", past_it)
    assert "note: C2 is 401 characters long" in err and "meant to be short" in err
    assert [event["text"] for event in rulings(round_log(rounds))] == [at_the_mark,
                                                                       past_it]


def test_no_length_is_refused(rounds):
    assert run("proj-a", text="z" * 50_000) == 0

    assert len(rulings(log.project_log(rounds))[0]["text"]) == 50_000


@pytest.mark.parametrize("flags", [[], ["--text", ""], ["--text", "  \n"],
                                   ["--text", SAID, "--reason", " "]])
def test_a_ruling_with_no_sentence_or_an_empty_reason_is_a_usage_error(
        rounds, capsys, flags):
    before = logs(rounds)

    assert run("proj-a/a-round", *flags, text=None) == 2

    assert ("--text TEXT" in capsys.readouterr().err) == ("--reason" not in flags)
    assert logs(rounds) == before


# -- what else is refused ----------------------------------------------------------------

def test_a_round_that_was_never_opened_is_refused(rounds, capsys):
    before = logs(rounds)

    assert run("proj-a/c-round") == 3
    assert run("proj-a/c-round", "--standing") == 3

    err = capsys.readouterr().err
    assert "there is no round proj-a/c-round" in err
    assert "moxi rounds open proj-a/c-round --description TEXT" in err
    assert logs(rounds) == before and not (rounds / "c-round").exists()
    with pytest.raises(NoRound):
        ruling.rule("proj-a/c-round", text=SAID, record="comms:12", actor="planner")


def test_a_project_the_projects_list_does_not_hold_is_refused(rounds, capsys):
    assert run("proj-b") == 3
    assert "there is no project 'proj-b'" in capsys.readouterr().err
    assert run("proj-b/a-round") == 3
    assert "there is no project 'proj-b'" in capsys.readouterr().err


@pytest.mark.parametrize("target", ["", " ", "proj-a/", "proj-a/A-Round", "proj-a/a/b"])
def test_a_target_that_names_nothing_is_a_usage_error(rounds, capsys, target):
    assert run(target) == 2

    assert capsys.readouterr().out == ""


def test_an_item_no_form_knows_is_a_usage_error(rounds, capsys):
    before = logs(rounds)

    for item in ("skip:retro", "park:", "park:B1", "narrow:Q0=none", "Q01", "q1"):
        assert main(["rule", "proj-a/a-round", item, "--record", "comms:12"]) == 2
        assert f"{item!r} is nothing a ruling can be on" in capsys.readouterr().err
    assert logs(rounds) == before


def test_a_form_that_rules_an_item_sets_its_kind_and_what_it_rules(rounds, monkeypatch):
    def parks(item, asked):
        if item != "park:Q1":
            return None
        return ruling.Form(kind=ruling.PROCESS, rules=item)

    monkeypatch.setattr(ruling, "_ITEM_FORMS", (parks,))

    ruled = ruling.rule("proj-a/a-round", item="park:Q1", record="comms:12",
                        actor="planner")

    assert (ruled.id, ruled.text) == ("C1", None)
    [found] = state.fold(log.read(round_log(rounds))).rulings
    assert (found.kind, found.rules, found.text) == (ruling.PROCESS, "park:Q1", None)


# -- the rulings of a question -----------------------------------------------------------

ASKED = "Do agent-read times follow the settings?"
ANSWER = "Prose follows the settings; parsed rows do not."


@pytest.fixture
def staged(rounds, capsys):
    """`rounds`, with the stages B1, B2 and B3 declared in `a-round`."""
    for stage in ("B1", "B2", "B3"):
        log.append(round_log(rounds), "stage.declared",
                   {"id": stage, "repo": "proj-a", "branch": f"branch-{stage.lower()}"},
                   actor="planner")
    return rounds


def ask(target="proj-a/a-round", blocks=None):
    """Ask a question of `target` and return its id."""
    return asking.ask(target, ASKED, origin="comms:11", blocks=blocks, actor="scout").id


def question(path, question_id="Q1"):
    found = questions.find(state.fold(log.read(path)), question_id)
    assert found is not None
    return found


def last(path):
    """The last event of a log, without what the log itself sets."""
    event = log.read(path)[-1]
    return {key: event[key] for key in event if key not in ("v", "n", "at")}


@pytest.mark.parametrize("blocks, stages", [(None, "all"), ("B1,B2", ["B1", "B2"]),
                                            ("none", "none")])
def test_an_answer_is_a_ruling_of_substance_on_the_stages_the_question_held_up(
        staged, capsys, blocks, stages):
    ask(blocks=blocks)

    assert run("proj-a/a-round", "Q1", "--as", "planner", text=ANSWER) == 0

    assert last(round_log(staged)) == {
        "actor": "planner", "type": "ruling.recorded", "id": "C1", "text": ANSWER,
        "question": "Q1", "state": "ruled", "stages": stages, "record": "comms:12",
        "kind": "substance", "rules": "Q1"}
    found = question(round_log(staged))
    assert (found.state, found.ruling) == ("ruled", "C1")
    assert found.blocks == ("all" if blocks is None else questions.parse_blocks(blocks))
    assert capsys.readouterr().out == printed("C1", ANSWER, question=("Q1", ASKED))


def test_an_answer_needs_its_sentence_and_his_pointer(staged, capsys):
    ask()
    before = logs(staged)

    assert run("proj-a/a-round", "Q1", text=None) == 2
    assert "an answer needs its sentence" in capsys.readouterr().err
    assert run("proj-a/a-round", "Q1", text=ANSWER, record=None) == 3
    assert ("run: moxi rounds rule proj-a/a-round Q1 --text TEXT --record PTR"
            in capsys.readouterr().err)
    assert run("proj-a/a-round", "Q1", text=ANSWER, record="he said so") == 3
    assert logs(staged) == before


def test_an_answer_counts_among_the_rounds_rulings(staged, capsys):
    ask()
    assert run("proj-a/a-round") == 0
    assert run("proj-a/a-round", "Q1", text=ANSWER) == 0
    assert run("proj-a/a-round") == 0

    assert [(found.id, found.kind, found.rules) for found in
            state.fold(log.read(round_log(staged))).rulings] == [
        ("C1", "substance", None), ("C2", "substance", "Q1"), ("C3", "substance", None)]


def test_a_second_answer_is_refused_and_names_the_first(staged, capsys):
    ask()
    assert run("proj-a/a-round", "Q1", text=ANSWER) == 0
    before = logs(staged)
    capsys.readouterr()

    assert run("proj-a/a-round", "Q1", text="Everything follows them.") == 3

    out, err = capsys.readouterr()
    assert out == "" and "Q1 is already ruled, by C1" in err
    assert ("run: moxi rounds rule proj-a/a-round Q1 --text TEXT --record PTR "
            "--supersedes C1") in err
    assert logs(staged) == before


@pytest.mark.parametrize("first, was, then", [
    ("Q1", "ruled", "park:Q1"), ("Q1", "ruled", "narrow:Q1=none"),
    ("Q1", "ruled", "withdraw:Q1"), ("withdraw:Q1", "withdrawn", "park:Q1"),
    ("withdraw:Q1", "withdrawn", "narrow:Q1=none"), ("withdraw:Q1", "withdrawn", "Q1")])
def test_a_ruled_or_withdrawn_question_takes_no_other_ruling(
        staged, capsys, first, was, then):
    ask()
    assert run("proj-a/a-round", first, text=ANSWER) == 0
    before = logs(staged)
    capsys.readouterr()

    assert run("proj-a/a-round", then, text=ANSWER) == 3

    err = capsys.readouterr().err
    assert f"Q1 is already {was}, by C1" in err
    assert "run: moxi rounds rule proj-a/a-round revoke:C1 --record PTR" in err
    assert logs(staged) == before


def test_a_parking_is_an_act_on_the_process_and_needs_no_sentence(staged, capsys):
    ask(blocks="B1")

    assert run("proj-a/a-round", "park:Q1", "--as", "planner", text=None) == 0

    assert last(round_log(staged)) == {
        "actor": "planner", "type": "ruling.recorded", "id": "C1", "question": "Q1",
        "state": "parked", "record": "comms:12", "kind": "process", "rules": "park:Q1"}
    found = question(round_log(staged))
    assert (found.state, found.until, found.blocks) == ("parked", None, ("B1",))
    assert capsys.readouterr().out == printed("C1", "park:Q1")


def test_a_sentence_given_with_an_act_is_kept(staged, capsys):
    ask()

    assert run("proj-a/a-round", "withdraw:Q1", text="It no longer matters.") == 0

    assert log.read(round_log(staged))[-1]["text"] == "It no longer matters."
    assert capsys.readouterr().out == printed("C1", "It no longer matters.")
    assert run("proj-a/a-round", "park:Q1", text="  ") == 2


@pytest.mark.parametrize("until", ["B2", "round:proj-a/b-round",
                                   "round:proj-b/not-opened-yet"])
def test_a_parking_may_say_what_the_question_waits_for(staged, until):
    ask()

    assert run("proj-a/a-round", "park:Q1", "--until", until, text=None) == 0

    assert log.read(round_log(staged))[-1]["until"] == until
    assert question(round_log(staged)).until == until


def test_a_question_parked_until_a_stage_reads_open_once_the_stage_is_finished(staged):
    ask()
    assert run("proj-a/a-round", "park:Q1", "--until", "B2", text=None) == 0
    waiting = state.fold(log.read(round_log(staged)))
    log.append(round_log(staged), "finish.done", {"stage": "B2", "tip": "0" * 40},
               actor="coder")
    done = state.fold(log.read(round_log(staged)))

    assert questions.current(waiting.questions[0], waiting) == "parked"
    assert questions.current(done.questions[0], done) == "open"


def test_a_question_parked_until_a_round_reads_open_once_that_round_is_closed(staged):
    ask()
    assert run("proj-a/a-round", "park:Q1", "--until", "round:proj-a/b-round",
               text=None) == 0
    found = state.fold(log.read(round_log(staged)))

    assert questions.current(found.questions[0], found) == "parked"
    log.append(round_log(staged, "b-round"), "round.closed", {"numbers": {}},
               actor="planner")
    assert questions.current(found.questions[0], found) == "open"


def test_a_wait_for_a_stage_the_round_does_not_have_is_refused(staged, capsys):
    ask()
    before = logs(staged)

    assert run("proj-a/a-round", "park:Q1", "--until", "B7", text=None) == 3

    assert ("B7 is not a stage of proj-a/a-round: its stages are B1, B2, B3"
            in capsys.readouterr().err)
    assert logs(staged) == before


@pytest.mark.parametrize("argv", [
    ["park:Q1", "--until", "soon"], ["park:Q1", "--until", "round:b-round"],
    ["park:Q1", "--until", "round:proj-a/B-Round"], ["park:Q1", "--until", ""],
    ["Q1", "--until", "B2"], ["withdraw:Q1", "--until", "B2"], ["--until", "B2"],
    ["narrow:Q1=none", "--until", "B2"], ["Q1", "--stages", "B1"],
    ["park:Q1", "--stages", "B1"], ["Q1", "--standing"], ["withdraw:Q1", "--standing"],
    ["narrow:Q1"], ["narrow:Q1="], ["narrow:Q1=B2,,B3"], ["narrow:Q1=b2"]])
def test_a_ruling_of_a_question_that_cant_be_read_is_a_usage_error(
        staged, capsys, argv):
    ask()
    before = logs(staged)

    assert run("proj-a/a-round", *argv, text=ANSWER) == 2

    assert capsys.readouterr().out == ""
    assert logs(staged) == before


@pytest.mark.parametrize("old, new, recorded", [
    (None, "B1,B2", ["B1", "B2"]), (None, "none", "none"), ("B1,B2,B3", "B3", ["B3"]),
    ("B1,B2", "none", "none"), ("B1,B2", "B2,B1", ["B2", "B1"]), (None, "all", "all")])
def test_a_narrowing_makes_a_question_hold_up_fewer_stages_or_none(
        staged, capsys, old, new, recorded):
    ask(blocks=old)

    assert run("proj-a/a-round", f"narrow:Q1={new}", "--as", "planner", text=None) == 0

    assert last(round_log(staged)) == {
        "actor": "planner", "type": "ruling.recorded", "id": "C1", "question": "Q1",
        "blocks": recorded, "record": "comms:12", "kind": "process",
        "rules": f"narrow:Q1={new}"}
    found = question(round_log(staged))
    assert found.state == "open"
    assert found.blocks == (recorded if isinstance(recorded, str) else tuple(recorded))


@pytest.mark.parametrize("old, new", [("B1", "B1,B2"), ("B1,B2", "B2,B3"), ("B1", "all"),
                                      ("none", "B1"), ("none", "all")])
def test_a_narrowing_that_would_hold_up_another_stage_is_refused(
        staged, capsys, old, new):
    ask(blocks=old)
    before = logs(staged)

    assert run("proj-a/a-round", f"narrow:Q1={new}", text=None) == 3

    err = capsys.readouterr().err
    assert f"Q1 holds up {old}" in err
    assert f"run: moxi rounds mark proj-a/a-round Q1 --blocks {new}" in err
    assert logs(staged) == before


def test_a_narrowing_to_a_stage_the_round_does_not_have_is_refused(staged, capsys):
    ask()
    before = logs(staged)

    assert run("proj-a/a-round", "narrow:Q1=B2,B9", text=None) == 3

    assert "B9 is not a stage of proj-a/a-round" in capsys.readouterr().err
    assert logs(staged) == before


def test_a_parked_question_is_narrowed_and_stays_parked(staged):
    ask()
    assert run("proj-a/a-round", "park:Q1", "--until", "B3", text=None) == 0

    assert run("proj-a/a-round", "narrow:Q1=B1", text=None) == 0

    found = question(round_log(staged))
    assert (found.state, found.until, found.blocks, found.ruling) == (
        "parked", "B3", ("B1",), "C2")


def test_a_parked_question_is_parked_in_place_of_its_parking_answered_or_withdrawn(
        staged):
    for _ in range(3):
        ask()
        ask()
    for number in range(1, 4):
        assert run("proj-a/a-round", f"park:Q{number}", "--until", "B1", text=None) == 0

    assert run("proj-a/a-round", "park:Q1", text=None) == 3
    assert run("proj-a/a-round", "park:Q1", "--supersedes", "C1", text=None) == 0
    assert run("proj-a/a-round", "Q2", text=ANSWER) == 0
    assert run("proj-a/a-round", "withdraw:Q3", text=None) == 0

    found = [question(round_log(staged), f"Q{number}") for number in range(1, 5)]
    assert [(one.state, one.until) for one in found] == [
        ("parked", None), ("ruled", None), ("withdrawn", None), ("open", None)]


def test_a_withdrawal_is_an_act_on_the_process(staged, capsys):
    ask()

    assert run("proj-a/a-round", "withdraw:Q1", "--as", "planner", text=None) == 0

    assert last(round_log(staged)) == {
        "actor": "planner", "type": "ruling.recorded", "id": "C1", "question": "Q1",
        "state": "withdrawn", "record": "comms:12", "kind": "process",
        "rules": "withdraw:Q1"}
    assert question(round_log(staged)).state == "withdrawn"


@pytest.mark.parametrize("item", ["park:Q1", "narrow:Q1=none", "withdraw:Q1"])
@pytest.mark.parametrize("record", [None, "he said so"])
def test_an_act_on_a_question_is_the_operators_and_needs_his_pointer(
        staged, capsys, item, record):
    ask()
    before = logs(staged)

    assert run("proj-a/a-round", item, text=None, record=record) == 3

    assert "PTR below is one token" in capsys.readouterr().err
    assert logs(staged) == before


@pytest.mark.parametrize("item", ["Q2", "park:Q2", "narrow:Q2=none", "withdraw:Q2",
                                  "P1", "withdraw:P1"])
def test_a_question_the_round_does_not_have_is_refused(staged, capsys, item):
    ask()
    ask("proj-a")
    before = logs(staged)

    assert run("proj-a/a-round", item, text=ANSWER) == 3

    err = capsys.readouterr().err
    assert f"proj-a/a-round has no question {item.split(':')[-1].split('=')[0]}" in err
    assert "run: moxi rounds show proj-a/a-round" in err
    assert logs(staged) == before


def test_a_refusal_of_a_question_names_everything_that_stopped_it(staged, capsys):
    ask(blocks="B1")

    assert run("proj-a/a-round", "narrow:Q1=B2,B9", text=None, record=None) == 3

    err = capsys.readouterr().err
    for said in ("needs a pointer", "B9 is not a stage", "Q1 holds up B1"):
        assert said in err


def test_a_ruled_question_leaves_the_index_and_a_parked_one_stays(staged):
    ask()
    ask()
    ruling.rule("proj-a/a-round", item="Q1", text=ANSWER, record="comms:12",
                actor="planner")
    ruling.rule("proj-a/a-round", item="park:Q2", until="B2", record="comms:12",
                actor="planner")

    [found] = index.read("proj-a", staged, "a-round").of("question")
    assert (found.id, found.body["state"], found.body["until"]) == ("Q2", "parked", "B2")


# -- the rulings of a project's question -------------------------------------------------

def test_an_answer_to_a_projects_question_goes_in_the_projects_log(staged, capsys):
    ask("proj-a")
    before = round_log(staged).read_bytes()
    capsys.readouterr()

    assert run("proj-a", "P1", "--as", "planner", text=ANSWER) == 0

    assert last(log.project_log(staged)) == {
        "actor": "planner", "type": "ruling.recorded", "id": "S1", "text": ANSWER,
        "question": "P1", "state": "ruled", "record": "comms:12", "kind": "substance",
        "rules": "P1"}
    assert round_log(staged).read_bytes() == before
    assert question(log.project_log(staged), "P1").state == "ruled"
    assert capsys.readouterr().out == printed("S1", ANSWER, "proj-a",
                                              question=("P1", ASKED))


def test_a_projects_question_is_parked_until_a_round_and_withdrawn(staged, capsys):
    ask("proj-a")
    ask("proj-a")

    assert run("proj-a", "park:P1", "--until", "round:proj-a/b-round", text=None) == 0
    assert run("proj-a", "withdraw:P2", text=None) == 0

    found = state.fold(log.read(log.project_log(staged)))
    assert [(one.id, one.state, one.until, one.ruling) for one in found.questions] == [
        ("P1", "parked", "round:proj-a/b-round", "S1"), ("P2", "withdrawn", None, "S2")]
    assert [one.kind for one in found.rulings] == ["process", "process"]
    assert questions.current(found.questions[0], found) == "parked"
    log.append(round_log(staged, "b-round"), "round.closed", {"numbers": {}},
               actor="planner")
    assert questions.current(found.questions[0], found) == "open"


@pytest.mark.parametrize("argv", [["park:P1", "--until", "B1"], ["narrow:P1=none"],
                                  ["park:P1", "--scope", "proj-a"],
                                  ["P1", "--stages", "B1"]])
def test_a_projects_question_has_no_stage_to_wait_for_or_to_narrow(
        staged, capsys, argv):
    ask("proj-a")
    before = logs(staged)

    assert run("proj-a", *argv, text=ANSWER) == 2

    assert logs(staged) == before


@pytest.mark.parametrize("item", ["Q1", "P2", "park:Q1", "withdraw:P2"])
def test_a_question_the_project_does_not_have_is_refused(staged, capsys, item):
    ask()
    ask("proj-a")
    before = logs(staged)

    assert run("proj-a", item, text=ANSWER) == 3

    err = capsys.readouterr().err
    assert f"proj-a has no question {item.split(':')[-1]}" in err
    assert "run: moxi rounds list proj-a --questions" in err
    assert logs(staged) == before


def test_a_ruling_of_a_projects_question_takes_only_the_projects_lock(
        staged, holder, capsys):
    ask("proj-a")
    holder(write.round_lock("proj-a", "a-round"))

    assert run("proj-a", "P1", text=ANSWER) == 0


# -- the locks ---------------------------------------------------------------------------

def test_a_standing_ruling_from_a_round_is_written_under_both_locks(rounds, holder):
    before = logs(rounds)
    held = holder(write.project_lock("proj-a"))

    with pytest.raises(Locked) as stopped:
        ruling.rule("proj-a/a-round", text=SAID, record="comms:12", actor="planner",
                    standing=True, wait_s=0.2)

    assert stopped.value.file == write.project_lock("proj-a")
    assert stopped.value.holder == held.pid
    assert logs(rounds) == before
    # The round's lock was let go when the project's could not be had.
    assert not write.round_lock("proj-a", "a-round").read_bytes()


def test_a_standing_ruling_from_a_round_waits_for_the_rounds_lock_too(rounds, holder):
    before = logs(rounds)
    held = holder(write.round_lock("proj-a", "a-round"))

    with pytest.raises(Locked) as stopped:
        ruling.rule("proj-a/a-round", text=SAID, record="comms:12", actor="planner",
                    standing=True, wait_s=0.2)

    assert stopped.value.file == write.round_lock("proj-a", "a-round")
    assert stopped.value.holder == held.pid
    assert logs(rounds) == before


def test_a_rounds_own_ruling_takes_only_the_rounds_lock(rounds, holder, capsys):
    holder(write.project_lock("proj-a"))

    assert run("proj-a/a-round") == 0

    assert [event["id"] for event in rulings(round_log(rounds))] == ["C1"]


def test_a_ruling_on_a_project_takes_only_the_projects_lock(rounds, holder, capsys):
    holder(write.round_lock("proj-a", "a-round"))

    assert run("proj-a") == 0

    assert [event["id"] for event in rulings(log.project_log(rounds))] == ["S1"]


# -- after the record --------------------------------------------------------------------

def test_work_that_fails_after_the_record_leaves_the_ruling_recorded(
        rounds, monkeypatch, capsys):
    def fail(written):
        raise RuntimeError("the disk is full")

    monkeypatch.setattr(write, "ON_APPEND", (fail,))

    assert run("proj-a/a-round") == 0

    out, err = capsys.readouterr()
    assert out == printed("C1", SAID)
    assert "warning" in err and "the disk is full" in err
    assert len(rulings(round_log(rounds))) == 1


# -- a ruling stopped right after its record ---------------------------------------------

def stopped_after_its_append(monkeypatch, target, **ruled):
    """Rule, and stop the verb as soon as its event is in the log: before anything
    that follows an append has run."""
    def stop(written):
        raise KeyboardInterrupt

    with monkeypatch.context() as patched:
        patched.setattr(write, "ON_APPEND", (stop,))
        with pytest.raises(KeyboardInterrupt):
            ruling.rule(target, record="comms:12", actor="planner", **ruled)


@pytest.mark.parametrize("target, item, said, became, by", [
    ("proj-a/a-round", "Q1", ANSWER, "ruled", "C1"),
    ("proj-a/a-round", "withdraw:Q1", None, "withdrawn", "C1"),
    ("proj-a", "P1", ANSWER, "ruled", "S1"),
    ("proj-a", "withdraw:P1", None, "withdrawn", "S1")])
def test_a_ruling_stopped_after_its_append_is_whole_and_is_refused_when_run_again(
        staged, monkeypatch, target, item, said, became, by):
    question_id = ask(target)
    path = round_log(staged) if "/" in target else log.project_log(staged)
    events_before = len(log.read(path))

    stopped_after_its_append(monkeypatch, target, item=item, text=said)

    with pytest.raises(Refused, match=f"{question_id} is already {became}, by {by}"):
        ruling.rule(target, item=item, text=said, record="comms:12", actor="planner")
    after = log.read(path)
    assert len(after) == events_before + 1
    assert [(event["id"], event["question"]) for event in rulings(path)] == [
        (by, question_id)]
    assert not [event for event in after if event["type"] == "question.changed"]
    found = question(path, question_id)
    assert (found.state, found.ruling) == (became, by)
    assert [(change.state, change.ruling, change.record) for change in found.changes] == [
        (became, by, "comms:12")]


@pytest.mark.parametrize("item, until, state_, blocks, waits", [
    ("park:Q1", "B2", "parked", ("B1", "B2"), "B2"),
    ("narrow:Q1=B2", None, "open", ("B2",), None)])
def test_a_parking_or_a_narrowing_stopped_after_its_append_has_changed_the_question(
        staged, monkeypatch, item, until, state_, blocks, waits):
    ask(blocks="B1,B2")
    events_before = len(log.read(round_log(staged)))

    stopped_after_its_append(monkeypatch, "proj-a/a-round", item=item, until=until)

    assert len(log.read(round_log(staged))) == events_before + 1
    found = question(round_log(staged))
    assert (found.state, found.blocks, found.until, found.ruling) == (
        state_, blocks, waits, "C1")


def test_no_ruling_of_a_question_writes_a_second_event(staged):
    for _ in range(4):
        ask()
    events_before = len(log.read(round_log(staged)))

    for item, said in (("Q1", ANSWER), ("park:Q2", None), ("narrow:Q3=none", None),
                       ("withdraw:Q4", None)):
        ruling.rule("proj-a/a-round", item=item, text=said, record="comms:12",
                    actor="planner")

    added = log.read(round_log(staged))[events_before:]
    assert [event["type"] for event in added] == ["ruling.recorded"] * 4
    assert [(one.id, one.ruling) for one in
            state.fold(log.read(round_log(staged))).questions] == [
        ("Q1", "C1"), ("Q2", "C2"), ("Q3", "C3"), ("Q4", "C4")]


# -- the option an answer chose ----------------------------------------------------------

OPTIONS = ("a=prose yes, parsed rows no", "b=everything", "c=nothing")


def offer(target="proj-a/a-round", given=OPTIONS):
    """Ask a question of `target` that offers the options `given`."""
    return asking.ask(target, ASKED, origin="comms:11", actor="scout", options=given).id


def test_an_answer_names_the_key_he_chose(staged, capsys):
    offer()

    assert run("proj-a/a-round", "Q1", "--chose", "b", "--as", "planner",
               text=ANSWER) == 0

    assert last(round_log(staged)) == {
        "actor": "planner", "type": "ruling.recorded", "id": "C1", "text": ANSWER,
        "chose": "b", "question": "Q1", "state": "ruled", "stages": "all",
        "record": "comms:12", "kind": "substance", "rules": "Q1"}
    found = question(round_log(staged))
    assert (found.state, found.ruling, found.chose) == ("ruled", "C1", "b")
    [recorded] = state.fold(log.read(round_log(staged))).rulings
    assert (recorded.chose, recorded.text) == ("b", ANSWER)
    assert capsys.readouterr().out == printed("C1", ANSWER, question=("Q1", ASKED))


def test_an_answer_that_only_names_a_key_records_the_options_line(staged, capsys):
    offer()

    assert run("proj-a/a-round", "Q1", "--chose", "a", text=None) == 0

    [recorded] = rulings(round_log(staged))
    assert (recorded["text"], recorded["chose"]) == ("prose yes, parsed rows no", "a")
    assert question(round_log(staged)).chose == "a"
    assert capsys.readouterr().out == printed("C1", "prose yes, parsed rows no",
                                              question=("Q1", ASKED))


def test_an_answer_with_no_key_leaves_none_chosen(staged):
    offer()

    assert run("proj-a/a-round", "Q1", text=ANSWER) == 0

    assert "chose" not in rulings(round_log(staged))[0]
    assert question(round_log(staged)).chose is None


def test_an_answer_to_a_projects_question_names_the_key_he_chose(staged):
    offer("proj-a")

    assert run("proj-a", "P1", "--chose", "c", text=None) == 0

    [recorded] = rulings(log.project_log(staged))
    assert (recorded["id"], recorded["text"], recorded["chose"]) == ("S1", "nothing", "c")
    assert "stages" not in recorded
    assert question(log.project_log(staged), "P1").chose == "c"


@pytest.mark.parametrize("key, said", [
    ("d", "'d' is not one of its options; Q1: its options are a, b"),
    ("c", "'c' was retired and is offered no longer; Q1: its options are a, b"),
    ("", "'' is not one of its options")])
@pytest.mark.parametrize("text", [ANSWER, None])
def test_a_key_the_question_does_not_offer_now_is_refused(staged, capsys, key, said,
                                                          text):
    offer()
    assert main(["ask", "proj-a/a-round", "Q1", "--retire", "c"]) == 0
    before = logs(staged)
    capsys.readouterr()

    assert run("proj-a/a-round", "Q1", "--chose", key, text=text) == 3

    out, err = capsys.readouterr()
    assert out == "" and said in err
    assert "run: moxi rounds rule proj-a/a-round Q1 --chose K --record PTR" in err
    assert logs(staged) == before


def test_a_key_chosen_of_a_question_with_no_options_is_refused(staged, capsys):
    ask()
    before = logs(staged)

    assert run("proj-a/a-round", "Q1", "--chose", "a", text=None) == 3

    err = capsys.readouterr().err
    assert "Q1 has no options, so there is no 'a' to choose" in err
    assert "run: moxi rounds rule proj-a/a-round Q1 --text TEXT --record PTR" in err
    assert logs(staged) == before


def test_a_key_added_after_another_was_retired_is_chosen(staged):
    offer()
    assert main(["ask", "proj-a/a-round", "Q1", "--retire", "c", "--option",
                 "d=nothing a person reads"]) == 0

    assert run("proj-a/a-round", "Q1", "--chose", "d", text=None) == 0

    assert rulings(round_log(staged))[0]["text"] == "nothing a person reads"


@pytest.mark.parametrize("argv", [[], ["park:Q1"], ["narrow:Q1=none"], ["withdraw:Q1"]])
def test_only_an_answer_names_a_key(staged, capsys, argv):
    offer()
    before = logs(staged)

    assert run("proj-a/a-round", *argv, "--chose", "a") == 2

    assert "--chose names the option he chose in answer to a question" in \
        capsys.readouterr().err
    assert logs(staged) == before


def test_an_answer_with_a_key_and_an_empty_sentence_is_a_usage_error(staged, capsys):
    offer()

    assert run("proj-a/a-round", "Q1", "--chose", "a", text="  ") == 2

    assert "--text says nothing" in capsys.readouterr().err


def test_an_answer_with_a_key_still_needs_his_pointer(staged, capsys):
    offer()
    before = logs(staged)

    assert run("proj-a/a-round", "Q1", "--chose", "a", text=None, record=None) == 3

    assert ("run: moxi rounds rule proj-a/a-round Q1 --chose a --record PTR"
            in capsys.readouterr().err)
    assert logs(staged) == before


def test_a_second_answer_with_a_key_is_refused_as_any_second_answer(staged, capsys):
    offer()
    assert run("proj-a/a-round", "Q1", "--chose", "a", text=None) == 0
    before = logs(staged)

    assert run("proj-a/a-round", "Q1", "--chose", "b", text=None) == 3

    assert "Q1 is already ruled, by C1" in capsys.readouterr().err
    assert logs(staged) == before
    assert question(round_log(staged)).chose == "a"

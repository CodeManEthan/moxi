"""Prepared answers: the `answer` block, `ask ID --prepared FILE`, what it refuses,
which answer is current, and that no gate reads one."""

from __future__ import annotations

import pytest

from moxi_rounds import (answer_block, asking, log, moving, opening, options, preparing,
                         questions, ruling, state, steps)
from moxi_rounds.cli import main
from moxi_rounds.errors import BadBlock, Refused

ROUND = "kestrel/billing-export"
SAID = "Should the export read archived invoices too?"
LINES = {"a": "yes, every invoice", "b": "no, live invoices only",
         "c": "only the last year's"}


@pytest.fixture
def rounds(tmp_path, add_project, capsys):
    """The rounds folder of the project `kestrel`, with the round `billing-export`
    opened in it and the stages B1 and B2 declared."""
    add_project("kestrel", notes_root=tmp_path / "notes")
    opening.open_round(ROUND, description="the billing export", by="planner",
                       flow="fix")
    rounds_dir = tmp_path / "notes" / "rounds"
    for stage in ("B1", "B2"):
        log.append(log.round_log(rounds_dir / "billing-export"), "stage.declared",
                   {"id": stage, "repo": "kestrel", "branch": f"branch-{stage.lower()}"},
                   by="planner")
    capsys.readouterr()
    return rounds_dir


def ask(*keys, target=ROUND, blocks=None) -> str:
    """Ask the question of `target` with the options under `keys`; return its id."""
    return asking.ask(target, SAID, origin="comms:11", blocks=blocks, by="scout",
                      options=[f"{key}={LINES[key]}" for key in keys]).id


def option(key, *, text=None, costs="a slower export", forecloses="nothing") -> str:
    lines = ["[[option]]", f'key = "{key}"']
    if text is not None:
        lines.append(f'text = "{text}"')
    lines += [f'costs = "{costs}"', f'forecloses = "{forecloses}"']
    return "\n".join(lines)


def answer(tmp_path, *keys, question="Q1", texts=False, name="answer.md",
           **keys_given) -> str:
    """Write an answer file for `question` with one `[[option]]` under each of `keys`,
    each with its line when `texts`; `keys_given` replaces a top key, and a value of
    None leaves it out. Return the file's path."""
    top = {"kind": '"answer"', "question": f'"{question}"', "by": '"builder-1"',
           "model": '"a-model"', "recommend": f'"{keys[0] if keys else "a"}"',
           "sure": '"medium"', "why": '"the invoices before last year are never read"',
           "flips": '"an auditor who asks for an old invoice"',
           "read": '["file:design.md#export", "comms:4611686018427394085"]'}
    top.update(keys_given)
    lines = [f"{key} = {value}" for key, value in top.items() if value is not None]
    body = "\n".join(lines) + "\n\n" + "\n\n".join(
        option(key, text=LINES[key] if texts else None) for key in keys)
    path = tmp_path / name
    path.write_text(f"# An answer\n\nProse around the block.\n\n```toml rounds\n"
                    f"{body}\n```\n", encoding="utf-8")
    return str(path)


def path_of(rounds, target=ROUND):
    return (log.round_log(rounds / "billing-export") if "/" in target else
            log.project_log(rounds))


def question(rounds, target=ROUND, question_id=None):
    wanted = question_id or ("Q1" if "/" in target else "P1")
    found = questions.find(state.fold(log.read(path_of(rounds, target))), wanted)
    assert found is not None
    return found


def logs(rounds):
    """Every log file under the rounds folder, with its bytes."""
    return {str(path.relative_to(rounds)): path.read_bytes()
            for path in sorted(rounds.rglob("*.jsonl"))}


def prepare(file, *, target=ROUND, question_id="Q1"):
    return main(["ask", target, question_id, "--prepared", file, "--as", "helper"])


# -- the block ---------------------------------------------------------------------------

def test_the_empty_block_is_kind_answer_with_one_option():
    text = answer_block.empty()

    assert text.startswith("```toml rounds\nkind = \"answer\"")
    for key in ("question", "by", "model", "recommend", "sure", "why", "flips", "read",
                "[[option]]", "key", "text", "costs", "forecloses"):
        assert f"\n{key}" in text
    assert "low | medium | high" in text


def test_a_filled_block_is_read(tmp_path):
    found = answer_block.read(answer(tmp_path, "a", "b"))

    assert found.question == "Q1" and found.question_line == 7
    prepared = found.prepared
    assert (prepared.recommend, prepared.by, prepared.model, prepared.sure) == (
        "a", "builder-1", "a-model", "medium")
    assert prepared.read == ("file:design.md#export", "comms:4611686018427394085")
    assert [(one.key, one.text, one.costs) for one in prepared.options] == [
        ("a", None, "a slower export"), ("b", None, "a slower export")]


# -- recording one -----------------------------------------------------------------------

def test_an_answer_is_recorded_on_the_question_by_anyone_with_no_pointer(
        rounds, tmp_path, capsys):
    ask("a", "b")

    assert prepare(answer(tmp_path, "b", "a")) == 0

    event = log.read(path_of(rounds))[-1]
    assert {key: event[key] for key in event if key not in ("v", "n", "at")} == {
        "type": "question.changed", "actor": "helper", "id": "Q1",
        "prepared": {
            "by": "builder-1", "model": "a-model", "recommend": "b", "sure": "medium",
            "why": "the invoices before last year are never read",
            "flips": "an auditor who asks for an old invoice",
            "read": ["file:design.md#export", "comms:4611686018427394085"],
            "option": [{"key": "b", "costs": "a slower export", "forecloses": "nothing"},
                       {"key": "a", "costs": "a slower export",
                        "forecloses": "nothing"}]}}
    out, err = capsys.readouterr()
    assert out.splitlines() == [
        f"Q1: {SAID}",
        "prepared by builder-1 (a-model, sure: medium): recommends b. the invoices "
        "before last year are never read. Flips if: an auditor who asks for an old "
        "invoice.",
        "  b: costs a slower export; forecloses nothing",
        "  a: costs a slower export; forecloses nothing"]
    assert err == ""
    found = question(rounds)
    [prepared] = found.prepared
    assert (prepared.recommend, prepared.actor, prepared.at) == ("b", "helper",
                                                                  event["at"])
    now = questions.prepared_now(found)
    assert now.prepared == prepared and now.current


def test_a_projects_question_and_a_parked_one_take_an_answer(rounds, tmp_path):
    ask("a", "b", target="kestrel")
    ask("a", "b")
    ruling.rule(ROUND, item="park:Q1", record="comms:12", by="planner")

    assert preparing.prepare("kestrel", "P1", answer(tmp_path, "a", "b", question="P1"),
                             by="helper").question.prepared
    assert prepare(answer(tmp_path, "a", "b")) == 0
    assert question(rounds).state == "parked"
    assert question(rounds, "kestrel").prepared[0].recommend == "a"


def test_a_question_with_no_options_takes_its_options_from_the_answer(rounds, tmp_path):
    ask()

    assert prepare(answer(tmp_path, "c", "a", texts=True)) == 0

    event = log.read(path_of(rounds))[-1]
    assert event["options"] == {"c": LINES["c"], "a": LINES["a"]}
    found = question(rounds)
    assert found.options == (("c", LINES["c"]), ("a", LINES["a"]))
    assert questions.prepared_now(found).current


def test_an_option_given_with_the_line_the_question_offers_is_accepted(rounds, tmp_path):
    ask("a", "b")

    assert prepare(answer(tmp_path, "a", "b", texts=True)) == 0
    assert "options" not in log.read(path_of(rounds))[-1]


# -- which one is current ----------------------------------------------------------------

@pytest.mark.parametrize("argv", [["--option", f"c={LINES['c']}"], ["--retire", "b"],
                                  ["--retire", "a"]])
def test_an_option_added_or_retired_makes_the_answer_stale(rounds, tmp_path, capsys,
                                                           argv):
    ask("a", "b", "c") if argv[0] == "--retire" else ask("a", "b")
    keys = ("a", "b", "c") if argv[0] == "--retire" else ("a", "b")
    assert prepare(answer(tmp_path, *keys)) == 0

    assert main(["ask", ROUND, "Q1", *argv]) == 0

    now = questions.prepared_now(question(rounds))
    assert now is not None and not now.current
    data = questions.prepared_data(question(rounds))
    assert data["current"] is False
    assert questions.prepared_lines(data)[1:] == ["prepared for other options"]
    offered = [key for key, _text in question(rounds).options]
    capsys.readouterr()
    assert prepare(answer(tmp_path, *offered, name="again.md")) == 0
    found = question(rounds)
    assert len(found.prepared) == 2 and questions.prepared_now(found).current


def test_the_newest_answer_is_the_one_shown(rounds, tmp_path):
    ask("a", "b")
    prepare(answer(tmp_path, "a", "b"))
    prepare(answer(tmp_path, "b", "a", name="second.md", sure='"high"'))

    found = question(rounds)
    assert [one.recommend for one in found.prepared] == ["a", "b"]
    assert questions.prepared_now(found).prepared.sure == "high"
    assert questions.prepared_now(question(rounds, question_id="Q1")) is not None


def test_a_question_with_no_answer_has_none_shown(rounds):
    ask("a", "b")

    found = question(rounds)
    assert questions.prepared_now(found) is None
    assert questions.prepared_data(found) is None
    assert questions.prepared_lines(None) == []


def test_a_moved_question_keeps_its_answers(rounds, tmp_path):
    ask("a", "b", blocks="none")
    ruling.rule(ROUND, item="park:Q1", record="comms:12", by="planner")
    prepare(answer(tmp_path, "a", "b"))
    was = question(rounds).prepared

    moving.move("kestrel", f"{ROUND}:Q1", by="planner", record="comms:13")

    assert question(rounds, "kestrel").prepared == was


# -- no gate reads it --------------------------------------------------------------------

@pytest.mark.parametrize("blocks", [None, "B1", "none"])
def test_no_gate_reads_an_answer(rounds, tmp_path, blocks):
    ask("a", "b", blocks=blocks)
    folded = state.fold(log.read(path_of(rounds)))
    before = (question(rounds).state, question(rounds).blocks,
              steps.of(folded, name=ROUND))

    assert prepare(answer(tmp_path, "a", "b")) == 0
    assert main(["ask", ROUND, "Q1", "--option", f"c={LINES['c']}"]) == 0
    assert prepare(answer(tmp_path, "a", "b", "c", name="again.md")) == 0

    folded = state.fold(log.read(path_of(rounds)))
    assert (question(rounds).state, question(rounds).blocks,
            steps.of(folded, name=ROUND)) == before


# -- refusals ----------------------------------------------------------------------------

@pytest.mark.parametrize("item, was", [("Q1", "ruled"), ("withdraw:Q1", "withdrawn")])
def test_a_ruled_or_withdrawn_question_takes_no_answer(rounds, tmp_path, capsys, item,
                                                       was):
    ask("a", "b")
    ruling.rule(ROUND, item=item, text="Live invoices only.", record="comms:12",
                by="planner")
    before = logs(rounds)

    assert prepare(answer(tmp_path, "a", "b")) == 3

    err = capsys.readouterr().err
    assert f"Q1 is {was}; an answer is prepared only for a question that is open or " \
           "parked" in err
    assert "run: moxi rounds show kestrel/billing-export" in err
    assert logs(rounds) == before


def test_a_moved_question_takes_no_answer(rounds, tmp_path, capsys):
    ask("a", "b")
    moving.move("kestrel", f"{ROUND}:Q1", by="planner", record="comms:13")
    before = logs(rounds)

    assert prepare(answer(tmp_path, "a", "b")) == 3
    assert "Q1 is moved" in capsys.readouterr().err
    assert logs(rounds) == before


def test_a_round_that_has_ended_takes_no_answer(rounds, tmp_path, capsys):
    ask("a", "b")
    assert main(["drop", ROUND, "--by", "lead", "--record", "comms:30"]) == 0
    capsys.readouterr()
    before = logs(rounds)

    assert prepare(answer(tmp_path, "a", "b")) == 3
    assert "a prepared answer" in capsys.readouterr().err
    assert logs(rounds) == before


def test_a_question_the_log_does_not_hold_is_refused(rounds, tmp_path, capsys):
    ask("a", "b")
    before = logs(rounds)

    assert prepare(answer(tmp_path, "a", "b", question="Q4"), question_id="Q4") == 3
    assert "kestrel/billing-export has no question Q4" in capsys.readouterr().err
    assert logs(rounds) == before


def refused(rounds, capsys, file, *, question_id="Q1"):
    """Run the command on `file`, check it is refused as a bad block with the log as
    it was, and return what it printed."""
    before = logs(rounds)
    assert prepare(file, question_id=question_id) == 3
    assert logs(rounds) == before
    err = capsys.readouterr().err
    assert "can't be read as a prepared answer's data block" in err
    return err


def test_a_file_with_no_block_is_refused(rounds, tmp_path, capsys):
    ask("a", "b")
    path = tmp_path / "answer.md"
    path.write_text("No block here.\n", encoding="utf-8")

    assert "it holds no data block" in refused(rounds, capsys, str(path))


@pytest.mark.parametrize("key, value, said", [
    ("kind", '"build"', '`kind` is "build", which is none of answer'),
    ("model", None, "the block has no `model`"),
    ("sure", '"fairly"', '`sure` is "fairly", which is none of low | medium | high'),
    ("sure", "3", "`sure` is 3, which is none of low | medium | high"),
    ("why", '""', "`why` says nothing"),
    ("flips", '"one\\ntwo"', "`flips` has a line break, and it is one line"),
    ("read", "[]", "`read` is [], not a list of at least one pointer"),
    ("read", '["design.md"]', '`read` holds "design.md", which is not a pointer'),
    ("recommend", '"d"', '`recommend` is "d", which is none of the options the answer '
                         'lists (a, b)'),
    ("colour", '"red"', "`colour` is not a key of the block"),
])
def test_a_block_that_does_not_fit_is_refused_with_its_line(rounds, tmp_path, capsys,
                                                            key, value, said):
    ask("a", "b")

    err = refused(rounds, capsys, answer(tmp_path, "a", "b", **{key: value}))

    assert said in err
    assert "line " in err


@pytest.mark.parametrize("key, value, said", [
    ("costs", "", "`costs` says nothing"),
    ("forecloses", "two\\nlines", "`forecloses` has a line break"),
])
def test_an_options_line_that_is_empty_or_two_lines_is_refused(rounds, tmp_path, capsys,
                                                                key, value, said):
    ask("a", "b")
    file = answer(tmp_path, "a", "b")
    text = open(file, encoding="utf-8").read().replace(
        f'{key} = "{"a slower export" if key == "costs" else "nothing"}"',
        f'{key} = "{value}"', 1)
    open(file, "w", encoding="utf-8").write(text)

    assert said in refused(rounds, capsys, file)


def test_an_answer_for_another_question_is_refused(rounds, tmp_path, capsys):
    ask("a", "b")
    ask("a", "b")

    err = refused(rounds, capsys, answer(tmp_path, "a", "b", question="Q2"))

    assert "line 7: the answer is for Q2, and it is recorded on Q1" in err
    assert "fix: name the question the file answers: Q1, or record it on Q2" in err


def test_an_option_the_question_offers_and_the_answer_leaves_out_is_refused(
        rounds, tmp_path, capsys):
    ask("a", "b", "c")

    err = refused(rounds, capsys, answer(tmp_path, "a", "b"))

    assert "Q1 offers c, and the answer has no [[option]] for it" in err
    assert "fix: one [[option]] for each option the question offers" in err


def test_an_option_the_question_does_not_offer_is_refused(rounds, tmp_path, capsys):
    ask("a", "b")

    err = refused(rounds, capsys, answer(tmp_path, "a", "b", "c"))

    assert "Q1 offers no option c; its options are a, b" in err


def test_a_retired_key_is_refused(rounds, tmp_path, capsys):
    ask("a", "b", "c")
    options.fill(ROUND, "Q1", retire=["c"], by="helper")

    err = refused(rounds, capsys, answer(tmp_path, "a", "b", "c"))

    assert "the key c was retired from Q1 and is never used again" in err


def test_another_line_under_a_key_the_question_offers_is_refused(rounds, tmp_path,
                                                                 capsys):
    ask("a", "b")
    file = answer(tmp_path, "a", "b", texts=True)
    text = open(file, encoding="utf-8").read().replace(LINES["a"], "every invoice ever")
    open(file, "w", encoding="utf-8").write(text)

    err = refused(rounds, capsys, file)

    assert f"Q1 offers {LINES['a']!r} under a, and a key keeps its line" in err


def test_a_question_with_no_options_needs_a_line_for_each(rounds, tmp_path, capsys):
    ask()

    err = refused(rounds, capsys, answer(tmp_path, "a", "b"))

    assert "Q1 has no options yet, so the answer gives them, and the option a has no " \
           "text" in err
    assert "fix: give each option its line in text; 2 to 4 options" in err


def test_a_question_with_no_options_needs_two_to_four(rounds, tmp_path, capsys):
    ask()

    err = refused(rounds, capsys, answer(tmp_path, "a", texts=True))

    assert "it gives 1: a question that has options has 2 to 4" in err


def test_an_option_key_used_twice_is_refused(rounds, tmp_path, capsys):
    ask("a", "b")

    err = refused(rounds, capsys, answer(tmp_path, "a", "b", "a"))

    assert "the key a is used twice" in err


def test_prepared_is_for_a_question_on_record(rounds, tmp_path, capsys):
    assert main(["ask", ROUND, SAID, "--from", "comms:1", "--prepared",
                 answer(tmp_path, "a", "b")]) == 2
    assert "--prepared records an answer for a question on record" in \
        capsys.readouterr().err
    ask("a", "b")
    assert main(["ask", ROUND, "Q1", "--prepared", answer(tmp_path, "a", "b"),
                 "--option", "c=x"]) == 2
    assert "--option is not given with --prepared" in capsys.readouterr().err


def test_a_refused_answer_raises_and_writes_nothing(rounds, tmp_path):
    ask("a", "b")
    ruling.rule(ROUND, item="Q1", text="Live only.", record="comms:12", by="planner")
    before = logs(rounds)

    with pytest.raises(Refused):
        preparing.prepare(ROUND, "Q1", answer(tmp_path, "a", "b"), by="helper")
    with pytest.raises(BadBlock):
        preparing.prepare(ROUND, "Q1", answer(tmp_path, "a", question="Q2"),
                          by="helper")
    assert logs(rounds) == before


def test_help_says_how_an_answer_is_prepared(capsys):
    assert main(["ask", "--help"]) == 0

    out = " ".join(capsys.readouterr().out.split())
    assert "--prepared FILE" in out
    assert "next --schema answer" in out

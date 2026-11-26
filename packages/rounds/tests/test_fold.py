"""`fold`: freezing a design as a numbered version, and what the design's block adds to
the round's record, takes away from it, and may not say."""

from __future__ import annotations

import hashlib

import pytest

from moxi_rounds import (asking, dropping, freezing, holds, index, log, marking, opening,
                         options, questions, ruling, showing, stages, state, versions,
                         write)
from moxi_rounds.cli import main
from moxi_rounds.errors import BadBlock, NoRound, Refused

ROUND = "proj-a/a-round"
ASKED = "Do agent-read times follow the settings?"
OTHER = "Does a parsed row follow them too?"
CHILD = "proj-a/a-child"
AT = "2026-01-02T03:04:05Z"


@pytest.fixture(name="round_dir")
def round_dir_fixture(tmp_path, add_project, capsys):
    """The folder of `a-round` of `proj-a`, opened on a flow that has a design step."""
    add_project("proj-a", notes_root=tmp_path / "notes")
    opening.open_round(ROUND, description="a placeholder round", by="planner",
                       flow="full")
    capsys.readouterr()
    return tmp_path / "notes" / "rounds" / "a-round"


def question(id_, text=ASKED, **more) -> str:
    """One question of a block; `blocks` and `options` as TOML text."""
    lines = ["[[question]]", f'id = "{id_}"', f'text = "{text}"']
    lines += [f"{key} = {value}" for key, value in more.items()]
    return "\n".join(lines)


def stage(id_, *, after="[]", round_=None, branch=None, slices=()) -> str:
    """One stage of a block: a build in `proj-a`, or the round `round_`."""
    lines = ["[[stage]]", f'id = "{id_}"']
    if round_ is not None:
        lines.append(f'round = "{round_}"')
    else:
        lines += ['repo = "proj-a"', f'branch = "{branch or "branch-" + id_.lower()}"']
    lines.append(f"after = {after}")
    for slice_id, what, minutes in slices:
        lines += ["  [[stage.slice]]", f'  id = "{slice_id}"', f'  what = "{what}"',
                  f"  minutes = {minutes}"]
    return "\n".join(lines)


def design(round_dir, *entries, prose="What the round builds.") -> bytes:
    """Write `design.md` with a block of `entries`, and return its bytes."""
    text = (f"# A design\n\n{prose}\n\n```toml rounds\n" + "\n\n".join(entries)
            + "\n```\n\nMore prose.\n")
    (round_dir / "design.md").write_text(text, encoding="utf-8")
    return text.encode("utf-8")


def fold(**more):
    return freezing.freeze(ROUND, by="scout", **more)


def events(round_dir):
    return log.read(log.round_log(round_dir))


def folded(round_dir):
    return state.fold(events(round_dir))


def last(round_dir):
    event = events(round_dir)[-1]
    return {key: event[key] for key in event if key not in ("v", "n", "at")}


def line_with(round_dir, needle: str) -> int:
    lines = (round_dir / "design.md").read_text(encoding="utf-8").splitlines()
    return next(n for n, line in enumerate(lines, 1) if needle in line)


def untouched(round_dir, before: bytes) -> bool:
    """Whether a refusal left the log as it was and froze nothing."""
    return (log.round_log(round_dir).read_bytes() == before
            and not (round_dir / "versions").exists())


# -- freezing a version ------------------------------------------------------------------

def test_a_fold_copies_the_design_to_its_first_version_and_records_its_digest(round_dir):
    data = design(round_dir, question("Q1"), stage("B1"))

    frozen = fold(at=AT)

    digest = hashlib.sha256(data).hexdigest()
    assert frozen.file == round_dir / "versions" / "design-v1.md"
    assert frozen.file.read_bytes() == data
    assert (frozen.name, frozen.version, frozen.digest) == (ROUND, 1, digest)
    assert last(round_dir) == {
        "actor": "scout", "type": "design.frozen", "version": 1, "digest": digest,
        "records_only": False, "trace": {"asked": False},
        "declared": {
            "question": [{"id": "Q1", "text": ASKED, "blocks": "all"}],
            "stage": [{"id": "B1", "after": [], "repo": "proj-a",
                       "branch": "branch-b1", "slices": []}],
            "trace": [], "commitment": [], "checklist": []},
        "added": {"stages": ["B1"], "questions": ["Q1"]}, "pending": []}
    found = folded(round_dir)
    assert versions.current(found).n == 1
    assert versions.digest(found) == versions.digest(found, 1) == digest
    assert versions.current(found).trace == {"asked": False}
    assert versions.current(found).records_only is False


def test_each_fold_freezes_the_next_version_and_keeps_the_ones_before(round_dir):
    first = design(round_dir, question("Q1"))
    fold()
    second = design(round_dir, question("Q1"), prose="What the round builds, said better.")

    frozen = fold()

    assert frozen.version == 2
    assert versions.file(round_dir, 1).read_bytes() == first
    assert versions.file(round_dir, 2).read_bytes() == second
    found = folded(round_dir)
    assert [one.n for one in found.versions] == [1, 2]
    assert versions.find(found, 1).digest == hashlib.sha256(first).hexdigest()
    assert versions.digest(found) == hashlib.sha256(second).hexdigest()
    assert versions.find(found, 3) is None and versions.digest(found, 3) is None
    assert [one["type"] for one in events(round_dir)] == [
        "round.opened", "design.frozen", "design.frozen"]


def test_a_design_frozen_again_unchanged_is_a_version_of_its_own(round_dir):
    data = design(round_dir, question("Q1"))
    fold()

    assert fold().version == 2
    assert versions.file(round_dir, 2).read_bytes() == data
    assert [one.id for one in folded(round_dir).questions] == ["Q1"]


def test_what_a_version_declared_is_read_from_the_log(round_dir):
    design(round_dir, question("Q1", blocks='["B1"]'), stage("B1"),
           '[[commitment]]\nid = "R1"\ntext = "A placeholder commitment."\nwhere = "1"',
           '[[trace]]\nid = "R1"\nas = "carried"\nwhere = "1"')
    fold()
    design(round_dir, stage("B1"))
    fold()

    found = folded(round_dir)

    assert [one["id"] for one in versions.declarations(found, 1)["question"]] == ["Q1"]
    assert versions.declarations(found, 1)["commitment"] == [
        {"id": "R1", "text": "A placeholder commitment.", "where": "1"}]
    assert versions.declarations(found, 1)["trace"] == [
        {"id": "R1", "as": "carried", "where": "1", "record": "", "stages": []}]
    assert versions.declarations(found)["question"] == []
    assert versions.declarations(found, 7) == {}


# -- what a fold adds --------------------------------------------------------------------

def test_a_question_a_design_declares_is_open_asked_by_the_fold_from_its_version(round_dir):
    design(round_dir, question("Q1", blocks='["B1"]'), question("Q2", OTHER), stage("B1"))

    fold(at=AT)

    assert folded(round_dir).questions == [
        state.Question(id="Q1", text=ASKED, origin="file:versions/design-v1.md#Q1",
                       blocks=("B1",), state="open", by="scout", at=AT),
        state.Question(id="Q2", text=OTHER, origin="file:versions/design-v1.md#Q2",
                       blocks="all", state="open", by="scout", at=AT)]


def test_a_question_keeps_the_version_that_first_carried_it(round_dir):
    design(round_dir, question("Q1"))
    fold(at=AT)
    design(round_dir, question("Q1"), question("Q2", OTHER))

    freezing.freeze(ROUND, by="critic", at="2026-01-03T00:00:00Z")

    first, second = folded(round_dir).questions
    assert (first.origin, first.by, first.at) == (
        "file:versions/design-v1.md#Q1", "scout", AT)
    assert (second.origin, second.by, second.at) == (
        "file:versions/design-v2.md#Q2", "critic", "2026-01-03T00:00:00Z")
    assert last(round_dir)["added"] == {"questions": ["Q2"]}


def test_a_question_declared_by_a_fold_holds_its_stages(round_dir):
    design(round_dir, question("Q1", blocks='["B2"]'), question("Q2", OTHER),
           question("Q3", "Is it so?", blocks='"none"'), stage("B1"), stage("B2"))

    fold()

    found = folded(round_dir)
    assert [one.id for one in holds.on_stage(found, "B1")] == ["Q2"]
    assert [one.id for one in holds.on_stage(found, "B2")] == ["Q1", "Q2"]
    assert [one.id for one in holds.on_close(found)] == ["Q1", "Q2", "Q3"]


def test_a_question_declared_by_a_fold_is_shown_with_who_asked_it_and_when(
        round_dir, capsys):
    design(round_dir, question("Q1"))
    fold(at=AT)
    capsys.readouterr()

    assert main(["show", ROUND]) == 0

    out = capsys.readouterr().out
    [line] = [one for one in out.splitlines() if "Q1:" in one]
    following = out[out.index(line):]
    assert "scout" in following and AT in following
    [shown] = showing.data(showing.show(ROUND))["questions"]
    assert (shown["id"], shown["by"], shown["at"]) == ("Q1", "scout", AT)


def test_a_stage_a_design_declares_joins_the_plan_with_its_slices(round_dir):
    design(round_dir,
           stage("B1", slices=[("1a", "the log and its fold", 40), ("1b", "the verbs", 30)]),
           stage("B2", after='["B1"]', round_="proj-a/a-child"))

    fold(at=AT)

    assert folded(round_dir).stages == [
        state.Stage(id="B1", repo="proj-a", branch="branch-b1", round=None, after=(),
                    slices=({"id": "1a", "what": "the log and its fold", "minutes": 40},
                            {"id": "1b", "what": "the verbs", "minutes": 30}),
                    why=None, version=1, actor="scout", at=AT),
        state.Stage(id="B2", repo=None, branch=None, round="proj-a/a-child",
                    after=("B1",), slices=(), why=None, version=1, actor="scout", at=AT)]
    assert questions.recorded_stages(folded(round_dir)) == ("B1", "B2")


def test_a_stage_another_now_waits_for_is_added_at_once(round_dir):
    design(round_dir, stage("B1"), stage("B2"), stage("B3"))
    fold()
    design(round_dir, stage("B1"), stage("B2"), stage("B3", after='["B1", "B2"]'))

    frozen = fold()

    assert frozen.plan.added == {"after": {"B3": ["B1", "B2"]}}
    assert frozen.plan.pending == ()
    assert folded(round_dir).stages[2].after == ("B1", "B2")


def test_a_wider_hold_is_added_at_once(round_dir):
    design(round_dir, question("Q1", blocks='["B1"]'), question("Q2", OTHER, blocks='"none"'),
           question("Q3", "Is it so?", blocks='["B1"]'), stage("B1"), stage("B2"))
    fold()
    design(round_dir, question("Q1", blocks='["B1", "B2"]'),
           question("Q2", OTHER, blocks='["B2"]'), question("Q3", "Is it so?"),
           stage("B1"), stage("B2"))

    frozen = fold()

    assert frozen.plan.added == {"blocks": {"Q1": ["B1", "B2"], "Q2": ["B2"],
                                            "Q3": "all"}}
    assert frozen.plan.pending == ()
    assert [one.blocks for one in folded(round_dir).questions] == [
        ("B1", "B2"), ("B2",), "all"]


def test_a_block_that_says_what_the_record_says_adds_nothing(round_dir):
    design(round_dir, question("Q1", blocks='["B1", "B2"]'), stage("B1"),
           stage("B2", after='["B1"]'))
    fold()
    before = folded(round_dir)
    design(round_dir, question("Q1", blocks='["B2", "B1"]'), stage("B1"),
           stage("B2", after='["B1"]'), prose="Said again.")

    frozen = fold()

    assert (frozen.plan.added, frozen.plan.pending) == ({}, ())
    after = folded(round_dir)
    assert (after.questions, after.stages) == (before.questions, before.stages)


def test_a_question_asked_on_its_own_and_then_declared_stays_as_it_was_asked(round_dir):
    asking.ask(ROUND, ASKED, origin="comms:11", by="planner")
    before = folded(round_dir).questions

    design(round_dir, question("Q1"))
    frozen = fold()

    assert frozen.plan.added == {}
    assert folded(round_dir).questions == before


def test_a_sentence_that_differs_from_the_record_changes_nothing_and_is_named(
        round_dir, capsys):
    design(round_dir, question("Q1"))
    fold()
    design(round_dir, question("Q1", OTHER))
    capsys.readouterr()

    assert main(["fold", ROUND, "--as", "scout"]) == 0

    assert "kept: Q1 keeps the sentence on record" in capsys.readouterr().out
    assert folded(round_dir).questions[0].text == ASKED
    assert last(round_dir)["added"] == {}


# -- what a ruling took off, and a fold leaves off ---------------------------------------

def ruled(item: str, **more) -> str:
    """Record the operator's ruling `item` and return its id."""
    return ruling.rule(ROUND, item=item, record="comms:1234567", by="planner",
                       **more).id


def held(round_dir, question_id: str = "Q1"):
    return questions.find(folded(round_dir), question_id).blocks


def waits(round_dir, stage_id: str):
    return stages.find(folded(round_dir), stage_id).after


def test_a_fold_does_not_put_back_what_a_ruling_took_off_an_entry_nobody_edited(round_dir):
    entries = (question("Q1"), stage("B1"), stage("B2", after='["B1"]'))
    design(round_dir, *entries)
    fold()
    narrowed = ruled("narrow:Q1=B2")
    unwaited = ruled("unafter:B2=B1")
    design(round_dir, *entries, prose="One more word of prose.")

    frozen = fold()

    assert (frozen.plan.added, frozen.plan.pending) == ({}, ())
    assert frozen.plan.kept_off == (
        freezing.KeptOff("after", "B2", "B1", unwaited),
        freezing.KeptOff("blocks", "Q1", "B1", narrowed))
    assert held(round_dir) == ("B2",) and waits(round_dir, "B2") == ()
    assert "added" not in last(round_dir) or last(round_dir)["added"] == {}


def test_two_folds_after_a_ruling_leave_off_what_one_does(round_dir):
    entries = (question("Q1"), stage("B1"), stage("B2", after='["B1"]'))
    design(round_dir, *entries)
    fold()
    ruled("narrow:Q1=B2")
    ruled("unafter:B2=B1")

    first = fold()
    design(round_dir, *entries, prose="Other prose.")
    second = fold()

    assert first.plan.kept_off == second.plan.kept_off != ()
    assert (second.plan.added, second.plan.pending) == ({}, ())
    assert held(round_dir) == ("B2",) and waits(round_dir, "B2") == ()


def test_a_change_to_the_rest_of_an_entry_puts_nothing_back(round_dir):
    design(round_dir, question("Q1", blocks='["B1", "B2", "B3"]'), stage("B1"),
           stage("B2"), stage("B3"), stage("B4", after='["B1", "B2"]'))
    fold()
    narrowed = ruled("narrow:Q1=B2,B3")
    unwaited = ruled("unafter:B4=B1")
    design(round_dir,
           question("Q1", OTHER, blocks='["B1", "B2"]',
                    options='{ a = "one", b = "two" }'),
           stage("B1"), stage("B2"), stage("B3"), stage("B4", after='["B1", "B3"]'))

    frozen = fold()

    assert frozen.plan.kept_off == (
        freezing.KeptOff("after", "B4", "B1", unwaited),
        freezing.KeptOff("blocks", "Q1", "B1", narrowed))
    assert frozen.plan.added == {
        "after": {"B4": ["B3"]},
        "options": {"Q1": {"options": {"a": "one", "b": "two"}}}}
    assert frozen.plan.pending == (
        state.Pending("after", "B4", gone=("B2",)),
        state.Pending("blocks", "Q1", gone=("B3",), blocks=("B1", "B2")))
    assert frozen.plan.reworded == ("Q1",)
    assert held(round_dir) == ("B2", "B3") and waits(round_dir, "B4") == ("B2", "B3")


def test_a_stage_a_list_gained_is_added_though_a_ruling_narrowed_the_rest(round_dir):
    design(round_dir, question("Q1", blocks='["B1", "B2"]'), stage("B1"), stage("B2"),
           stage("B3"), stage("B4", after='["B1"]'))
    fold()
    narrowed = ruled("narrow:Q1=B2")
    unwaited = ruled("unafter:B4=B1")
    design(round_dir, question("Q1", blocks='["B1", "B2", "B3"]'), stage("B1"),
           stage("B2"), stage("B3"), stage("B4", after='["B1", "B2"]'))

    frozen = fold()

    assert frozen.plan.added == {"after": {"B4": ["B2"]},
                                 "blocks": {"Q1": ["B2", "B3"]}}
    assert frozen.plan.kept_off == (
        freezing.KeptOff("after", "B4", "B1", unwaited),
        freezing.KeptOff("blocks", "Q1", "B1", narrowed))
    assert frozen.plan.pending == ()
    assert held(round_dir) == ("B2", "B3") and waits(round_dir, "B4") == ("B2",)


def test_a_hold_on_every_stage_counts_as_the_stages_its_block_declares(round_dir):
    design(round_dir, question("Q1"), stage("B1"), stage("B2"))
    fold()
    narrowed = ruled("narrow:Q1=B2")
    design(round_dir, question("Q1"), stage("B1"), stage("B2"), stage("B3"))

    frozen = fold()

    assert frozen.plan.added == {"stages": ["B3"], "blocks": {"Q1": ["B2", "B3"]}}
    assert frozen.plan.kept_off == (freezing.KeptOff("blocks", "Q1", "B1", narrowed),)
    assert held(round_dir) == ("B2", "B3")
    assert [one.id for one in holds.on_stage(folded(round_dir), "B3")] == ["Q1"]
    assert holds.on_stage(folded(round_dir), "B1") == ()


def test_a_list_made_a_hold_on_every_stage_gains_the_stages_it_did_not_name(round_dir):
    design(round_dir, question("Q1", blocks='["B1", "B2"]'), stage("B1"), stage("B2"),
           stage("B3"))
    fold()
    narrowed = ruled("narrow:Q1=B2")
    design(round_dir, question("Q1"), stage("B1"), stage("B2"), stage("B3"))

    frozen = fold()

    assert frozen.plan.added == {"blocks": {"Q1": ["B2", "B3"]}}
    assert frozen.plan.kept_off == (freezing.KeptOff("blocks", "Q1", "B1", narrowed),)
    assert held(round_dir) == ("B2", "B3")


def test_a_hold_on_no_stage_names_none_before_and_gains_none_after(round_dir):
    design(round_dir, question("Q1", blocks='"none"'), question("Q2", OTHER),
           stage("B1"), stage("B2"))
    fold()
    narrowed = ruled("narrow:Q2=none")
    design(round_dir, question("Q1", blocks='["B1"]'), question("Q2", OTHER),
           stage("B1"), stage("B2"))

    frozen = fold()

    assert frozen.plan.added == {"blocks": {"Q1": ["B1"]}}
    assert frozen.plan.kept_off == (
        freezing.KeptOff("blocks", "Q2", "B1", narrowed),
        freezing.KeptOff("blocks", "Q2", "B2", narrowed))
    assert held(round_dir, "Q2") == "none"
    design(round_dir, question("Q1", blocks='["B1"]'),
           question("Q2", OTHER, blocks='"none"'), stage("B1"), stage("B2"))
    frozen = fold()
    assert (frozen.plan.added, frozen.plan.pending, frozen.plan.kept_off) == (
        {}, (), ())


def test_a_narrowing_that_was_taken_back_keeps_nothing_off(round_dir):
    entries = (question("Q1"), stage("B1"), stage("B2"))
    design(round_dir, *entries)
    fold()
    narrowed = ruled("narrow:Q1=B2")
    assert fold().plan.kept_off != ()
    ruled(f"revoke:{narrowed}")
    assert held(round_dir) == "all"

    frozen = fold()

    assert (frozen.plan.added, frozen.plan.pending, frozen.plan.kept_off) == (
        {}, (), ())
    assert held(round_dir) == "all"


def test_a_narrowing_that_replaced_another_is_the_one_named(round_dir):
    design(round_dir, question("Q1"), stage("B1"), stage("B2"), stage("B3"))
    fold()
    first = ruled("narrow:Q1=B1,B2")
    second = ruled("narrow:Q1=B2", supersedes=first)

    frozen = fold()

    assert frozen.plan.kept_off == (
        freezing.KeptOff("blocks", "Q1", "B1", second),
        freezing.KeptOff("blocks", "Q1", "B3", second))
    assert frozen.plan.added == {}


def test_a_narrowing_that_names_every_stage_is_left_a_list_until_a_stage_is_gained(
        round_dir):
    design(round_dir, question("Q1"), stage("B1"), stage("B2"))
    fold()
    ruled("narrow:Q1=B1,B2")

    frozen = fold()

    assert (frozen.plan.added, frozen.plan.kept_off) == ({}, ())
    assert held(round_dir) == ("B1", "B2")
    design(round_dir, question("Q1"), stage("B1"), stage("B2"), stage("B3"))
    assert fold().plan.added == {"stages": ["B3"], "blocks": {"Q1": "all"}}
    assert held(round_dir) == "all"


def test_the_lead_holds_again_what_a_ruling_took_off_and_a_fold_then_adds_nothing(
        round_dir):
    entries = (question("Q1"), stage("B1"), stage("B2", after='["B1"]'))
    design(round_dir, *entries)
    fold()
    ruled("narrow:Q1=B2")
    ruled("unafter:B2=B1")
    fold()

    marking.widen(ROUND, "Q1", "B1,B2", by="planner")
    stages.wait(ROUND, "B2", "B1", by="planner")

    assert set(held(round_dir)) == {"B1", "B2"} and waits(round_dir, "B2") == ("B1",)
    frozen = fold()
    assert frozen.plan.kept_off == ()
    assert set(held(round_dir)) == {"B1", "B2"} and waits(round_dir, "B2") == ("B1",)


def test_an_edit_that_lists_again_a_stage_it_had_left_out_adds_it(round_dir):
    design(round_dir, question("Q1"), stage("B1"), stage("B2"), stage("B3", after='["B1"]'))
    fold()
    ruled("narrow:Q1=B2,B3")
    ruled("unafter:B3=B1")
    design(round_dir, question("Q1", blocks='["B2", "B3"]'), stage("B1"), stage("B2"),
           stage("B3"))
    assert fold().plan.kept_off == ()
    design(round_dir, question("Q1", blocks='["B1", "B2", "B3"]'), stage("B1"),
           stage("B2"), stage("B3", after='["B1"]'))

    frozen = fold()

    assert frozen.plan.added == {"after": {"B3": ["B1"]},
                                 "blocks": {"Q1": ["B1", "B2", "B3"]}}
    assert frozen.plan.kept_off == ()
    assert set(held(round_dir)) == {"B1", "B2", "B3"} and waits(round_dir, "B3") == ("B1",)


def test_a_first_version_adds_the_whole_of_each_list(round_dir):
    asking.ask(ROUND, ASKED, origin="comms:11", by="planner", blocks="none")
    design(round_dir, question("Q1", blocks='["B1"]'), stage("B1"),
           stage("B2", after='["B1"]'))

    frozen = fold()

    assert frozen.plan.added == {"stages": ["B1", "B2"], "blocks": {"Q1": ["B1"]}}
    assert frozen.plan.kept_off == ()
    assert held(round_dir) == ("B1",) and waits(round_dir, "B2") == ("B1",)


def test_an_entry_the_version_before_did_not_have_adds_its_whole_list(round_dir):
    design(round_dir, question("Q1"), stage("B1"), stage("B2"))
    fold()
    ruled("narrow:Q1=B2")
    design(round_dir, stage("B1"), stage("B2"))
    fold()
    design(round_dir, question("Q1"), stage("B1"), stage("B2"))

    frozen = fold()

    assert frozen.plan.added == {"blocks": {"Q1": "all"}}
    assert frozen.plan.kept_off == ()
    assert held(round_dir) == "all"


def test_the_command_prints_a_line_for_each_stage_kept_off_with_its_ruling(
        round_dir, capsys):
    entries = (question("Q1"), stage("B1"), stage("B2", after='["B1"]'))
    design(round_dir, *entries)
    fold()
    narrowed = ruled("narrow:Q1=none")
    unwaited = ruled("unafter:B2=B1")
    capsys.readouterr()

    assert main(["fold", ROUND, "--as", "scout"]) == 0

    assert capsys.readouterr().out.splitlines()[3:] == [
        f"kept off: B2 still does not wait for B1: {unwaited} took it off",
        f"kept off: Q1 still does not hold up B1: {narrowed} took it off",
        f"kept off: Q1 still does not hold up B2: {narrowed} took it off",
        "what is kept off is held again by mark, with --blocks for a question and "
        "--after for a stage"]


# -- what a fold does not take away ------------------------------------------------------

def test_what_a_block_takes_away_is_pending_and_changes_nothing(round_dir):
    design(round_dir, question("Q1", blocks='["B1", "B2"]'), question("Q2", OTHER),
           question("Q3", "Is it so?"), question("Q4", "Is it not?", blocks='["B1"]'),
           stage("B1"), stage("B2", after='["B1"]'), stage("B3", after='["B1", "B2"]'))
    fold()
    before = folded(round_dir)
    design(round_dir, question("Q1", blocks='["B2"]'), question("Q3", "Is it so?",
                                                             blocks='["B1"]'),
           question("Q4", "Is it not?", blocks='"none"'),
           stage("B1"), stage("B3", after='["B1"]'))

    frozen = fold()

    took = (state.Pending("after", "B3", gone=("B2",)), state.Pending("stage", "B2"),
            state.Pending("blocks", "Q1", gone=("B1",), blocks=("B2",)),
            state.Pending("blocks", "Q3", gone=("B2", "B3"), blocks=("B1",)),
            state.Pending("blocks", "Q4", gone=("B1",), blocks="none"),
            state.Pending("question", "Q2"))
    assert frozen.plan.pending == took
    assert frozen.plan.added == {}
    after = folded(round_dir)
    assert (after.questions, after.stages) == (before.questions, before.stages)
    assert versions.pending_removals(after, 2) == took
    assert versions.pending_removals(after, 1) == ()
    assert versions.pending_removals(after, 3) == ()
    assert last(round_dir)["pending"] == [
        {"what": "after", "id": "B3", "gone": ["B2"]}, {"what": "stage", "id": "B2"},
        {"what": "blocks", "id": "Q1", "gone": ["B1"], "blocks": ["B2"]},
        {"what": "blocks", "id": "Q3", "gone": ["B2", "B3"], "blocks": ["B1"]},
        {"what": "blocks", "id": "Q4", "gone": ["B1"], "blocks": "none"},
        {"what": "question", "id": "Q2"}]
    assert [one.id for one in holds.on_stage(after, "B1")] == ["Q1", "Q2", "Q3", "Q4"]


def test_what_a_version_took_away_is_pending_in_each_version_that_leaves_it_out(round_dir):
    design(round_dir, question("Q1"), question("Q2", OTHER), stage("B1"), stage("B2"))
    fold()
    design(round_dir, question("Q1"), stage("B1"))
    fold()
    design(round_dir, question("Q1"), stage("B1"), prose="Said again.")
    fold()
    design(round_dir, question("Q1"), question("Q2", OTHER), stage("B1"), stage("B2"))
    fold()

    found = folded(round_dir)

    gone = (state.Pending("stage", "B2"), state.Pending("question", "Q2"))
    assert [versions.pending_removals(found, n) for n in (1, 2, 3, 4)] == [
        (), gone, gone, ()]
    assert [one.id for one in found.questions] == ["Q1", "Q2"]


def test_a_hold_moved_from_one_stage_to_another_gains_the_new_and_keeps_the_old(round_dir):
    design(round_dir, question("Q1", blocks='["B1"]'), stage("B1"), stage("B2"))
    fold()
    design(round_dir, question("Q1", blocks='["B2"]'), stage("B1"), stage("B2"))

    frozen = fold()

    assert frozen.plan.added == {"blocks": {"Q1": ["B1", "B2"]}}
    assert frozen.plan.pending == (
        state.Pending("blocks", "Q1", gone=("B1",), blocks=("B2",)),)
    assert folded(round_dir).questions[0].blocks == ("B1", "B2")


def test_a_question_no_version_carried_is_not_taken_away_by_a_block_without_it(round_dir):
    asking.ask(ROUND, ASKED, origin="comms:11", by="planner")
    design(round_dir, question("Q2", OTHER))

    frozen = fold()

    assert frozen.plan.pending == ()
    assert [one.id for one in folded(round_dir).questions] == ["Q1", "Q2"]


def test_a_question_since_ruled_and_left_out_is_not_pending(round_dir):
    design(round_dir, question("Q1"), question("Q2", OTHER))
    fold()
    ruling.rule(ROUND, item="Q1", text="They do.", record="comms:12", by="planner")
    ruling.rule(ROUND, item="park:Q2", record="comms:13", by="planner")
    design(round_dir, prose="No question is left.")

    frozen = fold()

    assert frozen.plan.pending == (state.Pending("question", "Q2"),)


def test_a_stage_declared_on_its_own_and_left_out_of_the_block_is_pending(round_dir):
    log.append(log.round_log(round_dir), "stage.declared",
               {"id": "B1", "repo": "proj-a", "branch": "branch-b1"}, by="planner")
    log.append(log.round_log(round_dir), "stage.declared",
               {"id": "B2", "repo": "proj-a", "branch": "branch-b2"}, by="planner")
    log.append(log.round_log(round_dir), "finish.done", {"stage": "B2", "tip": "0" * 40},
               by="coder")
    design(round_dir, stage("B3"))

    frozen = fold()

    assert frozen.plan.added == {"stages": ["B3"]}
    assert frozen.plan.pending == (state.Pending("stage", "B1"),)
    assert questions.recorded_stages(folded(round_dir)) == ("B1", "B2", "B3")


# -- options -----------------------------------------------------------------------------

ABC = '{ a = "prose yes, parsed rows no", b = "everything", c = "nothing" }'


def test_a_question_is_declared_with_its_options(round_dir):
    design(round_dir, question("Q1", options=ABC))

    fold()

    assert folded(round_dir).questions[0].options == (
        ("a", "prose yes, parsed rows no"), ("b", "everything"), ("c", "nothing"))


def test_a_block_with_options_adds_new_keys_and_retires_the_ones_it_leaves_out(round_dir):
    design(round_dir, question("Q1", options=ABC))
    fold()
    design(round_dir, question(
        "Q1", options='{ a = "prose yes, parsed rows no", b = "everything", '
                      'd = "only what is asked" }'))

    frozen = fold()

    assert frozen.plan.added == {"options": {"Q1": {
        "options": {"d": "only what is asked"}, "retired": ["c"]}}}
    found = folded(round_dir).questions[0]
    assert found.options == (("a", "prose yes, parsed rows no"), ("b", "everything"),
                             ("d", "only what is asked"))
    assert found.retired == ("c",)


def test_options_from_a_block_change_nothing_a_question_holds_up(round_dir):
    design(round_dir, question("Q1", blocks='["B1"]', options=ABC), stage("B1"))
    fold()
    before = folded(round_dir)
    design(round_dir, question("Q1", blocks='["B1"]',
                            options='{ a = "prose yes, parsed rows no", b = "everything" }'),
           stage("B1"))

    frozen = fold()

    after = folded(round_dir)
    assert frozen.plan.pending == ()
    assert (after.questions[0].state, after.questions[0].blocks) == (
        before.questions[0].state, before.questions[0].blocks)
    assert holds.on_stage(after, "B1")[0].id == "Q1"


def test_a_block_that_gives_a_question_no_options_changes_none(round_dir):
    design(round_dir, question("Q1", options=ABC))
    fold()
    before = folded(round_dir).questions

    design(round_dir, question("Q1"))
    frozen = fold()

    assert frozen.plan.added == {}
    assert folded(round_dir).questions == before


def test_a_retired_key_a_block_lists_is_left_out_by_name_and_the_rest_is_read(
        round_dir, capsys):
    design(round_dir, question("Q1", options=ABC))
    fold()
    options.fill(ROUND, "Q1", by="planner", retire=["c"])
    design(round_dir, question("Q1", options=ABC.replace("}", ', d = "only what is asked" }')),
           question("Q2", OTHER))
    capsys.readouterr()

    assert main(["fold", ROUND, "--as", "scout"]) == 0

    out = capsys.readouterr().out
    assert "not used: the key c of Q1 was retired and is never used again" in out
    assert last(round_dir)["added"] == {
        "questions": ["Q2"], "options": {"Q1": {"options": {"d": "only what is asked"}}}}
    found = folded(round_dir).questions[0]
    assert found.options == (("a", "prose yes, parsed rows no"), ("b", "everything"),
                             ("d", "only what is asked"))
    assert found.retired == ("c",)


def test_a_key_given_another_line_than_it_has_is_refused_at_its_line(round_dir):
    design(round_dir, question("Q1", options=ABC))
    fold()
    design(round_dir, question("Q1", options=ABC.replace("everything", "all of it")))
    before = log.round_log(round_dir).read_bytes()

    with pytest.raises(BadBlock) as refused:
        fold()

    [problem] = refused.value.problems
    assert problem.line == line_with(round_dir, "options =")
    assert "Q1 offers 'everything' under b, and a key keeps its text" in problem.what
    assert log.round_log(round_dir).read_bytes() == before
    assert not versions.file(round_dir, 2).exists()


def test_options_that_would_leave_a_question_one_live_key_are_refused(round_dir):
    design(round_dir, question("Q1", options=ABC))
    fold()
    options.fill(ROUND, "Q1", by="planner", retire=["c"])
    design(round_dir, question("Q1", options='{ a = "prose yes, parsed rows no", '
                                          'c = "nothing" }'))
    before = log.round_log(round_dir).read_bytes()

    with pytest.raises(BadBlock) as refused:
        fold()

    [problem] = refused.value.problems
    assert "2 to 4" in problem.what and "leave it 1" in problem.what
    assert log.round_log(round_dir).read_bytes() == before


# -- refusals ----------------------------------------------------------------------------

def test_a_round_with_no_design_is_refused(round_dir):
    before = log.round_log(round_dir).read_bytes()

    with pytest.raises(Refused) as refused:
        fold()

    [unmet] = refused.value.unmet
    assert "has no design" in unmet.reason and "design.md" in unmet.reason
    assert unmet.fix == f"moxi rounds fold {ROUND}"
    assert untouched(round_dir, before)


def test_a_round_whose_flow_has_no_design_step_is_refused(tmp_path, add_project, capsys):
    add_project("proj-a", notes_root=tmp_path / "notes")
    opening.open_round(ROUND, description="a placeholder round", by="planner",
                       flow="fix")
    round_dir = tmp_path / "notes" / "rounds" / "a-round"
    design(round_dir, question("Q1"))
    before = log.round_log(round_dir).read_bytes()

    with pytest.raises(Refused) as refused:
        fold()

    [unmet] = refused.value.unmet
    assert "the flow fix" in unmet.reason and "has no design step" in unmet.reason
    assert unmet.fix == f"moxi rounds mark {ROUND} --flow NAME"
    assert untouched(round_dir, before)


def test_a_refusal_names_everything_that_stopped_it(tmp_path, add_project, capsys):
    add_project("proj-a", notes_root=tmp_path / "notes")
    opening.open_round(ROUND, description="a placeholder round", by="planner",
                       flow="fix")

    with pytest.raises(Refused) as refused:
        fold()

    assert len(refused.value.unmet) == 2


def test_a_round_moved_up_to_a_flow_with_a_design_step_is_folded(
        tmp_path, add_project, capsys):
    add_project("proj-a", notes_root=tmp_path / "notes")
    opening.open_round(ROUND, description="a placeholder round", by="planner",
                       flow="fix")
    round_dir = tmp_path / "notes" / "rounds" / "a-round"
    design(round_dir, question("Q1"))
    main(["mark", ROUND, "--flow", "small", "--as", "planner"])

    assert fold().version == 1


@pytest.mark.parametrize("entries, needle, says", [
    (('[[question]]\nid = "Q1"\ntext = "Is it so?"\nblocks = []',), "blocks = []", None),
    (('[[question]]\nid = "Q1"',), "[[question]]", None),
    (('[[stage]]\nid = "B1"\nrepo = "proj-a"\nbranch = "b"\nlands = "soon"',),
     "lands", None),
    (('id = ',), "id = ", None)])
def test_a_block_that_cant_be_read_is_refused_with_its_file_and_line(
        round_dir, entries, needle, says):
    design(round_dir, *entries)
    before = log.round_log(round_dir).read_bytes()

    with pytest.raises(BadBlock) as refused:
        fold()

    assert refused.value.file == round_dir / "design.md"
    assert line_with(round_dir, needle) in [one.line for one in refused.value.problems]
    assert untouched(round_dir, before)


def test_a_design_with_no_block_is_refused(round_dir):
    (round_dir / "design.md").write_text("# A design\n\nProse only.\n", encoding="utf-8")
    before = log.round_log(round_dir).read_bytes()

    with pytest.raises(BadBlock) as refused:
        fold()

    assert refused.value.file == round_dir / "design.md"
    assert untouched(round_dir, before)


@pytest.mark.parametrize("item, became", [("Q1", "ruled"), ("withdraw:Q1", "withdrawn")])
def test_the_id_of_a_question_since_ruled_or_withdrawn_is_refused_at_its_line(
        round_dir, item, became):
    design(round_dir, question("Q1"), question("Q2", OTHER))
    fold()
    ruling.rule(ROUND, item=item, record="comms:12", by="planner",
                text="They do." if item == "Q1" else None)
    design(round_dir, question("Q2", OTHER), question("Q1", "Is it so?"))
    before = log.round_log(round_dir).read_bytes()

    with pytest.raises(BadBlock) as refused:
        fold()

    [problem] = refused.value.problems
    assert problem.line == line_with(round_dir, 'id = "Q1"')
    assert f"the round already used Q1: it is {became}, by C1" in problem.what
    assert log.round_log(round_dir).read_bytes() == before
    assert not versions.file(round_dir, 2).exists()


def test_the_id_of_a_question_moved_to_the_projects_list_is_refused(round_dir):
    design(round_dir, question("Q1"))
    fold()
    main(["ask", "proj-a", "--move", f"{ROUND}:Q1", "--record", "comms:12",
          "--as", "planner"])
    design(round_dir, question("Q1"), prose="Asked again.")
    before = log.round_log(round_dir).read_bytes()

    with pytest.raises(BadBlock, match="the round already used Q1: it is moved"):
        fold()

    assert log.round_log(round_dir).read_bytes() == before


def prepared(*keys, recommend="a", texts=None, sure="medium") -> str:
    """A `[question.prepared]` with an option under each of `keys`, each with its line
    from `texts` where given."""
    lines = ["", "  [question.prepared]", f'  recommend = "{recommend}"', '  by = "helper"',
             '  model = "a-model"', f'  sure = "{sure}"', '  why = "It reads so."',
             '  flips = "A reading the other way."', '  read = ["comms:1234567"]']
    for key in keys:
        lines += ["", "  [[question.prepared.option]]", f'  key = "{key}"']
        if texts:
            lines.append(f'  text = "{texts[key]}"')
        lines += [f'  costs = "Costs of {key}."', '  forecloses = "nothing"']
    return "\n".join(lines)


TWO = '{ a = "one", b = "two" }'


def test_a_prepared_answer_in_a_block_is_recorded_on_its_question(round_dir, capsys):
    design(round_dir, question("Q1", options=TWO) + prepared("a", "b"))

    assert main(["fold", ROUND, "--as", "scout"]) == 0

    body = {"by": "helper", "model": "a-model", "recommend": "a", "sure": "medium",
            "why": "It reads so.", "flips": "A reading the other way.",
            "read": ["comms:1234567"],
            "option": [{"key": "a", "costs": "Costs of a.", "forecloses": "nothing"},
                       {"key": "b", "costs": "Costs of b.", "forecloses": "nothing"}]}
    assert last(round_dir)["added"] == {"questions": ["Q1"], "prepared": {"Q1": body}}
    found = folded(round_dir).questions[0]
    [one] = found.prepared
    assert one.body() == body and one.actor == "scout"
    assert questions.prepared_now(found).current
    assert "prepared: Q1 by helper, recommends a" in capsys.readouterr().out


def test_a_question_on_record_takes_the_answer_a_later_block_prepares(round_dir):
    design(round_dir, question("Q1", options=TWO))
    fold()
    design(round_dir, question("Q1", options=TWO) + prepared("b", "a", recommend="b"),
           prose="Prepared.")

    frozen = fold()

    assert set(frozen.plan.added) == {"prepared"}
    assert questions.prepared_now(folded(round_dir).questions[0]).prepared.recommend == "b"


def test_a_second_fold_with_the_same_answer_adds_nothing(round_dir):
    design(round_dir, question("Q1", options=TWO) + prepared("a", "b"))
    fold()
    design(round_dir, question("Q1", options=TWO) + prepared("a", "b"),
           prose="Reworded.")

    assert fold().plan.added == {}
    assert len(folded(round_dir).questions[0].prepared) == 1


def test_the_answer_on_record_less_the_lines_the_question_offers_adds_nothing(
        round_dir, tmp_path):
    design(round_dir, question("Q1"))
    fold()
    file = tmp_path / "answer.md"
    file.write_text("""```toml rounds
kind = "answer"
question = "Q1"
recommend = "a"
by = "helper"
model = "a-model"
sure = "medium"
why = "It reads so."
flips = "A reading the other way."
read = ["comms:1234567"]

[[option]]
key = "a"
text = "one"
costs = "Costs of a."
forecloses = "nothing"

[[option]]
key = "b"
text = "two"
costs = "Costs of b."
forecloses = "nothing"
```
""", encoding="utf-8")
    assert main(["ask", ROUND, "Q1", "--prepared", str(file), "--as", "helper"]) == 0
    design(round_dir, question("Q1") + prepared("a", "b"), prose="Prepared again.")

    assert fold().plan.added == {}
    found = folded(round_dir).questions[0]
    assert len(found.prepared) == 1 and questions.prepared_now(found).current


def test_a_changed_answer_or_a_stale_one_is_recorded_again(round_dir):
    design(round_dir, question("Q1", options=TWO) + prepared("a", "b"))
    fold()
    design(round_dir, question("Q1", options=TWO) + prepared("a", "b", sure="high"),
           prose="Surer.")

    assert set(fold().plan.added) == {"prepared"}
    three = '{ a = "one", b = "two", c = "three" }'
    design(round_dir, question("Q1", options=three) + prepared("a", "b", "c",
                                                               sure="high"),
           prose="A third option.")
    assert set(fold().plan.added) == {"options", "prepared"}
    found = folded(round_dir).questions[0]
    assert len(found.prepared) == 3 and questions.prepared_now(found).current


def test_a_question_with_no_options_takes_them_from_its_answer(round_dir):
    design(round_dir, question("Q1") + prepared("a", "b", texts={"a": "one", "b": "two"}))

    frozen = fold()

    assert frozen.plan.added["options"] == {"Q1": {"options": {"a": "one", "b": "two"}}}
    found = folded(round_dir).questions[0]
    assert found.options == (("a", "one"), ("b", "two"))
    assert questions.prepared_now(found).current


def test_a_records_only_fold_that_only_adds_an_answer_is_accepted(round_dir):
    design(round_dir, question("Q1", options=TWO))
    fold()
    design(round_dir, question("Q1", options=TWO) + prepared("a", "b"),
           prose="Prepared.")

    frozen = fold(records_only=True, record="comms:77")

    assert set(frozen.plan.added) == {"prepared"}
    found = folded(round_dir)
    assert versions.current(found).records_only is True
    assert found.questions[0].prepared[0].recommend == "a"


@pytest.mark.parametrize("entry, needle, says", [
    (question("Q1", options='{ a = "one", b = "two", c = "three" }') + prepared("a", "b"),
     "[question.prepared]",
     "Q1 offers c, and the answer has no [[question.prepared.option]] for it"),
    (question("Q1", options=TWO) + prepared("a", "b", "c"), 'key = "c"',
     "Q1 offers no option c; its options are a, b"),
    (question("Q1") + prepared("a", "b"), 'key = "a"',
     "Q1 has no options yet, so the answer gives them, and the option a has no text"),
])
def test_an_answer_that_does_not_fit_its_question_is_refused_with_its_line(
        round_dir, entry, needle, says):
    design(round_dir, entry)
    before = log.round_log(round_dir).read_bytes()

    with pytest.raises(BadBlock) as refused:
        fold()

    found = [one for one in refused.value.problems if says in one.what]
    assert found and found[0].line == line_with(round_dir, needle)
    assert untouched(round_dir, before)


def test_an_answer_for_a_retired_key_is_refused(round_dir):
    three = '{ a = "one", b = "two", c = "three" }'
    design(round_dir, question("Q1", options=three))
    fold()
    main(["ask", ROUND, "Q1", "--retire", "c", "--as", "planner"])
    design(round_dir, question("Q1", options=TWO) + prepared("a", "b", "c"),
           prose="Again.")

    with pytest.raises(BadBlock, match="the key c was retired from Q1"):
        fold()


@pytest.mark.parametrize("entries, needle, says", [
    ((question("Q1", blocks='["B1", "B7"]'), stage("B1")), "blocks =",
     "the question Q1 names B7, which is no stage of the round"),
    ((stage("B1", after='["B7"]'),), "after =",
     "the stage B1 names B7, which is no stage of the round")])
def test_a_stage_named_that_the_round_and_the_block_do_not_have_is_refused(
        round_dir, entries, needle, says):
    design(round_dir, *entries)
    before = log.round_log(round_dir).read_bytes()

    with pytest.raises(BadBlock) as refused:
        fold()

    [problem] = refused.value.problems
    assert says in problem.what
    assert problem.line == line_with(round_dir, needle)
    assert untouched(round_dir, before)


def test_a_question_may_hold_up_a_stage_the_record_has_and_the_block_leaves_out(round_dir):
    design(round_dir, stage("B1"))
    fold()
    design(round_dir, question("Q1", blocks='["B1"]'))

    frozen = fold()

    assert frozen.plan.added == {"questions": ["Q1"]}
    assert frozen.plan.pending == (state.Pending("stage", "B1"),)


def test_every_problem_with_the_record_is_named_in_one_refusal(round_dir):
    design(round_dir, question("Q1"))
    fold()
    ruling.rule(ROUND, item="Q1", text="They do.", record="comms:12", by="planner")
    design(round_dir, question("Q1"), question("Q2", OTHER, blocks='["B7"]'))

    with pytest.raises(BadBlock) as refused:
        fold()

    assert len(refused.value.problems) == 2


def test_a_round_that_has_ended_is_folded_no_longer(round_dir):
    design(round_dir, question("Q1"))
    dropping.drop(ROUND, by="lead", record="comms:12", run_by="planner")
    before = log.round_log(round_dir).read_bytes()

    with pytest.raises(Refused, match="was dropped"):
        fold()

    assert untouched(round_dir, before)


def test_a_round_that_was_never_opened_is_refused(round_dir):
    with pytest.raises(NoRound):
        freezing.freeze("proj-a/no-such-round", by="scout")


def test_a_check_added_to_the_folds_own_refuses_the_block(round_dir, monkeypatch):
    from moxi_rounds.errors import Problem

    def never(found, block):
        return [Problem(block.stages[0].line_of("repo"), "a placeholder problem",
                        "a placeholder fix")]

    monkeypatch.setattr(freezing, "CHECKS", [never])
    design(round_dir, stage("B1"))
    before = log.round_log(round_dir).read_bytes()

    with pytest.raises(BadBlock, match="a placeholder problem"):
        fold()

    assert untouched(round_dir, before)


# -- a fold that was stopped -------------------------------------------------------------

def test_a_fold_stopped_between_its_copy_and_its_append_is_run_again_to_one_version(
        round_dir, monkeypatch):
    data = design(round_dir, question("Q1"), stage("B1"))
    before = log.round_log(round_dir).read_bytes()

    def stop(*args, **more):
        raise KeyboardInterrupt

    with monkeypatch.context() as patched:
        patched.setattr(log, "append", stop)
        with pytest.raises(KeyboardInterrupt):
            fold()
    left = versions.file(round_dir, 1)
    assert left.read_bytes() == data
    assert log.round_log(round_dir).read_bytes() == before
    made = left.stat()

    frozen = fold()

    assert frozen.version == 1
    assert [one["type"] for one in events(round_dir)] == ["round.opened", "design.frozen"]
    assert sorted(one.name for one in (round_dir / "versions").iterdir()) == ["design-v1.md"]
    kept = left.stat()
    assert (kept.st_ino, kept.st_mtime_ns) == (made.st_ino, made.st_mtime_ns)
    assert [one.id for one in folded(round_dir).questions] == ["Q1"]
    assert [one.id for one in folded(round_dir).stages] == ["B1"]


def test_a_stopped_folds_file_is_written_again_when_the_design_changed_since(
        round_dir, monkeypatch):
    design(round_dir, question("Q1"))

    def stop(*args, **more):
        raise KeyboardInterrupt

    with monkeypatch.context() as patched:
        patched.setattr(log, "append", stop)
        with pytest.raises(KeyboardInterrupt):
            fold()
    changed = design(round_dir, question("Q1"), question("Q2", OTHER))

    frozen = fold()

    assert frozen.version == 1
    assert versions.file(round_dir, 1).read_bytes() == changed
    assert versions.digest(folded(round_dir)) == hashlib.sha256(changed).hexdigest()
    assert sorted(one.name for one in (round_dir / "versions").iterdir()) == ["design-v1.md"]


def test_a_fold_stopped_right_after_its_append_has_added_everything(round_dir, monkeypatch):
    design(round_dir, question("Q1", blocks='["B1"]'), stage("B1"))

    def stop(written):
        raise KeyboardInterrupt

    with monkeypatch.context() as patched:
        patched.setattr(write, "ON_APPEND", (stop,))
        with pytest.raises(KeyboardInterrupt):
            fold()

    assert [one["type"] for one in events(round_dir)] == ["round.opened", "design.frozen"]
    found = folded(round_dir)
    assert [one.id for one in found.questions] == ["Q1"]
    assert [one.id for one in found.stages] == ["B1"]
    assert [one.id for one in holds.on_stage(found, "B1")] == ["Q1"]
    assert versions.file(round_dir, 1).is_file()


# -- the design since its last version ---------------------------------------------------

def test_a_design_as_it_was_frozen_last_is_unchanged(round_dir):
    design(round_dir, question("Q1"))
    fold()

    assert versions.unchanged(folded(round_dir), round_dir, ROUND) is None


def test_a_design_edited_since_its_last_version_is_named_with_the_fold_to_run(round_dir):
    design(round_dir, question("Q1"))
    fold()
    fold()
    design(round_dir, question("Q1"), prose="Edited since.")

    unmet = versions.unchanged(folded(round_dir), round_dir, ROUND)

    assert unmet.reason == "design.md changed since v2 was frozen"
    assert unmet.fix == f"moxi rounds fold {ROUND}"


def test_a_design_never_frozen_or_since_removed_is_not_unchanged(round_dir):
    never = versions.unchanged(folded(round_dir), round_dir, ROUND)
    design(round_dir, question("Q1"))
    fold()
    (round_dir / "design.md").unlink()

    gone = versions.unchanged(folded(round_dir), round_dir, ROUND)

    assert never.reason == f"the design of {ROUND} was never frozen"
    assert gone.reason == "design.md changed since v1 was frozen"


# -- the stages a fold declares, as the other verbs read them ----------------------------

def test_a_question_is_asked_of_a_stage_a_fold_declared(round_dir):
    design(round_dir, stage("B1"), stage("B2"))
    fold()

    asked = asking.ask(ROUND, ASKED, origin="comms:11", blocks="B2", by="planner")
    with pytest.raises(Refused, match="B7"):
        asking.ask(ROUND, OTHER, origin="comms:11", blocks="B7", by="planner")

    assert asked.id == "Q1"
    assert [one.id for one in holds.on_stage(folded(round_dir), "B2")] == ["Q1"]
    assert holds.on_stage(folded(round_dir), "B1") == ()


def test_a_hold_is_widened_onto_a_stage_a_fold_declared(round_dir):
    design(round_dir, question("Q1", blocks='["B1"]'), stage("B1"), stage("B2"))
    fold()

    marking.widen(ROUND, "Q1", "B1,B2", by="planner")

    assert folded(round_dir).questions[0].blocks == ("B1", "B2")


def test_a_skip_names_a_stage_a_fold_declared(round_dir):
    design(round_dir, question("Q1"), stage("B1"))
    fold()

    ruling.rule(ROUND, item="skip:docket:B1", record="comms:12", by="planner")

    assert holds.on_stage(folded(round_dir), "B1") == ()


# -- the command, and where a version is shown -------------------------------------------

def test_the_command_says_what_was_frozen_added_and_left_pending(round_dir, capsys):
    design(round_dir, question("Q1", blocks='["B1"]'), question("Q2", OTHER), stage("B1"),
           stage("B2", after='["B1"]'))
    assert main(["fold", ROUND, "--as", "scout"]) == 0
    first = capsys.readouterr().out
    data = design(round_dir, question("Q1", blocks='["B1", "B2"]'), stage("B1"),
                  stage("B2"), stage("B3", after='["B1"]'))

    assert main(["fold", ROUND, "--as", "scout"]) == 0

    assert first.splitlines()[0] == f"froze the design of {ROUND} as v1"
    assert "added: questions Q1, Q2" in first and "added: stages B1, B2" in first
    assert capsys.readouterr().out.splitlines() == [
        f"froze the design of {ROUND} as v2",
        f"file: {round_dir / 'versions' / 'design-v2.md'}",
        f"sha256: {hashlib.sha256(data).hexdigest()}",
        "added: stages B3",
        "added: Q1 now holds up B1, B2",
        "pending: B2 waits for B1 no longer",
        "pending: the question Q2 is gone from the block",
        "what is pending changes nothing until a verdict finds this version "
        "publishable or the operator rules it"]


def test_the_real_command_folds(round_dir, capsys):
    from moxi import cli

    design(round_dir, question("Q1"))

    assert cli.main(["rounds", "fold", ROUND, "--as", "scout"]) == 0
    assert last(round_dir)["actor"] == "scout"


def test_the_command_refuses_with_the_file_the_line_and_the_fix(round_dir, capsys):
    design(round_dir, '[[question]]\nid = "Q1"\ntext = "Is it so?"\nblocks = []')

    assert main(["fold", ROUND]) == 3

    err = capsys.readouterr().err
    assert str(round_dir / "design.md") in err
    assert f"line {line_with(round_dir, 'blocks = []')}:" in err
    assert "fix:" in err


def test_help_says_what_fold_does(capsys):
    assert main(["fold", "--help"]) == 0
    assert "Freeze the round's design as its next version" in capsys.readouterr().out


def test_stages_the_version_and_what_it_took_away_are_shown_and_in_the_note(
        round_dir, capsys):
    design(round_dir, question("Q1"), stage("B1", slices=[("1a", "the log", 40)]),
           stage("B2", after='["B1"]', round_="proj-a/a-child"))
    fold()
    data = design(round_dir, stage("B1", slices=[("1a", "the log", 40)]),
                  stage("B2", round_="proj-a/a-child"))
    fold(at=AT)
    capsys.readouterr()

    assert main(["show", ROUND]) == 0

    digest = hashlib.sha256(data).hexdigest()
    lines = [f"design: v2, frozen {AT} by scout, sha256 {digest}",
             "pending in v2: B2 waits for B1 no longer",
             "pending in v2: the question Q1 is gone from the block",
             "stage B1: proj-a on branch-b1; 1 slice",
             f"stage B2: the round {CHILD}, after B1"]
    out = capsys.readouterr().out.splitlines()
    assert [line for line in out if line.startswith(("design:", "pending", "stage "))
            ] == lines
    assert "  3  " + AT + "  scout  design.frozen  v2" in out
    note = (round_dir / "round-note.md").read_text(encoding="utf-8")
    for line in lines:
        assert f"- {line}" in note
    shown = showing.data(showing.show(ROUND))
    assert [(one["id"], one["version"], one["after"], one["finished"], one["left"])
            for one in shown["stages"]] == [("B1", 1, [], False, False),
                                            ("B2", 1, ["B1"], False, False)]
    assert [(one["version"], one["digest"] == digest, one["records_only"],
             [took["said"] for took in one["pending"]]) for one in shown["versions"]] == [
        (1, False, False, []),
        (2, True, False, ["B2 waits for B1 no longer",
                          "the question Q1 is gone from the block"])]


def test_stages_the_version_and_what_it_took_away_are_in_the_index(round_dir):
    design(round_dir, question("Q1"), stage("B1"), stage("B2", after='["B1"]'))
    fold()
    data = design(round_dir, question("Q1"), stage("B1"))
    fold(at=AT)

    found = index.read("proj-a", round_dir.parent, "a-round")

    assert [(item.id, item.body["repo"], item.body["after"])
            for item in found.of(index.STAGE)] == [("B1", "proj-a", []),
                                                   ("B2", "proj-a", ["B1"])]
    assert found.of(index.VERSION) == (index.Item(index.VERSION, "v2", {
        "version": 2, "digest": hashlib.sha256(data).hexdigest(), "at": AT,
        "by": "scout", "records_only": False, "standing": "none",
        "trace": {"asked": False}}),)
    assert [(item.id, item.body["said"]) for item in found.of(index.PENDING)] == [
        ("v2:stage:B2", "the stage B2 is gone from the block")]
    assert [item.id for item in found.of(index.QUESTION)] == ["Q1"]

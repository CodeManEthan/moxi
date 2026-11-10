"""`fold`: freezing a design as a numbered version, and what the design's block adds to
the round's record, takes away from it, and may not say."""

from __future__ import annotations

import hashlib

import pytest

from moxi_rounds import (asking, dropping, freezing, holds, index, log, marking, opening,
                         options, questions, ruling, showing, state, versions, write)
from moxi_rounds.cli import main
from moxi_rounds.errors import BadBlock, NoRound, Refused

ROUND = "proj-a/a-round"
ASKED = "Do agent-read times follow the settings?"
OTHER = "Does a parsed row follow them too?"
CHILD = "proj-a/a-child"
AT = "2026-01-02T03:04:05Z"


@pytest.fixture
def folder(tmp_path, add_project, capsys):
    """The folder of `a-round` of `proj-a`, opened on a flow that has a design step."""
    add_project("proj-a", notes_root=tmp_path / "notes")
    opening.open_round(ROUND, description="a placeholder round", actor="planner",
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


def design(folder, *entries, prose="What the round builds.") -> bytes:
    """Write `design.md` with a block of `entries`, and return its bytes."""
    text = (f"# A design\n\n{prose}\n\n```toml rounds\n" + "\n\n".join(entries)
            + "\n```\n\nMore prose.\n")
    (folder / "design.md").write_text(text, encoding="utf-8")
    return text.encode("utf-8")


def fold(**more):
    return freezing.freeze(ROUND, actor="scout", **more)


def events(folder):
    return log.read(log.round_log(folder))


def folded(folder):
    return state.fold(events(folder))


def last(folder):
    event = events(folder)[-1]
    return {key: event[key] for key in event if key not in ("v", "n", "at")}


def line_with(folder, needle: str) -> int:
    lines = (folder / "design.md").read_text(encoding="utf-8").splitlines()
    return next(n for n, line in enumerate(lines, 1) if needle in line)


def untouched(folder, before: bytes) -> bool:
    """Whether a refusal left the log as it was and froze nothing."""
    return (log.round_log(folder).read_bytes() == before
            and not (folder / "versions").exists())


# -- freezing a version ------------------------------------------------------------------

def test_a_fold_copies_the_design_to_its_first_version_and_records_its_digest(folder):
    data = design(folder, question("Q1"), stage("B1"))

    frozen = fold(at=AT)

    digest = hashlib.sha256(data).hexdigest()
    assert frozen.file == folder / "versions" / "design-v1.md"
    assert frozen.file.read_bytes() == data
    assert (frozen.name, frozen.version, frozen.digest) == (ROUND, 1, digest)
    assert last(folder) == {
        "actor": "scout", "type": "design.frozen", "version": 1, "digest": digest,
        "records_only": False,
        "declared": {
            "question": [{"id": "Q1", "text": ASKED, "blocks": "all"}],
            "stage": [{"id": "B1", "after": [], "repo": "proj-a",
                       "branch": "branch-b1", "slices": []}],
            "trace": [], "commitment": []},
        "added": {"stages": ["B1"], "questions": ["Q1"]}, "pending": []}
    found = folded(folder)
    assert versions.current(found).n == 1
    assert versions.digest(found) == versions.digest(found, 1) == digest
    assert versions.current(found).trace is None
    assert versions.current(found).records_only is False


def test_each_fold_freezes_the_next_version_and_keeps_the_ones_before(folder):
    first = design(folder, question("Q1"))
    fold()
    second = design(folder, question("Q1"), prose="What the round builds, said better.")

    frozen = fold()

    assert frozen.version == 2
    assert versions.file(folder, 1).read_bytes() == first
    assert versions.file(folder, 2).read_bytes() == second
    found = folded(folder)
    assert [one.n for one in found.versions] == [1, 2]
    assert versions.find(found, 1).digest == hashlib.sha256(first).hexdigest()
    assert versions.digest(found) == hashlib.sha256(second).hexdigest()
    assert versions.find(found, 3) is None and versions.digest(found, 3) is None
    assert [one["type"] for one in events(folder)] == [
        "round.opened", "design.frozen", "design.frozen"]


def test_a_design_frozen_again_unchanged_is_a_version_of_its_own(folder):
    data = design(folder, question("Q1"))
    fold()

    assert fold().version == 2
    assert versions.file(folder, 2).read_bytes() == data
    assert [one.id for one in folded(folder).questions] == ["Q1"]


def test_what_a_version_declared_is_read_from_the_log(folder):
    design(folder, question("Q1", blocks='["B1"]'), stage("B1"),
           '[[commitment]]\nid = "R1"\ntext = "A placeholder commitment."\nwhere = "1"',
           '[[trace]]\nid = "R1"\nas = "carried"\nwhere = "1"')
    fold()
    design(folder, stage("B1"))
    fold()

    found = folded(folder)

    assert [one["id"] for one in versions.declarations(found, 1)["question"]] == ["Q1"]
    assert versions.declarations(found, 1)["commitment"] == [
        {"id": "R1", "text": "A placeholder commitment.", "where": "1"}]
    assert versions.declarations(found, 1)["trace"] == [
        {"id": "R1", "as": "carried", "where": "1", "record": "", "stages": []}]
    assert versions.declarations(found)["question"] == []
    assert versions.declarations(found, 7) == {}


# -- what a fold adds --------------------------------------------------------------------

def test_a_question_a_design_declares_is_open_asked_by_the_fold_from_its_version(folder):
    design(folder, question("Q1", blocks='["B1"]'), question("Q2", OTHER), stage("B1"))

    fold(at=AT)

    assert folded(folder).questions == [
        state.Question(id="Q1", text=ASKED, origin="file:versions/design-v1.md#Q1",
                       blocks=("B1",), state="open", by="scout", at=AT),
        state.Question(id="Q2", text=OTHER, origin="file:versions/design-v1.md#Q2",
                       blocks="all", state="open", by="scout", at=AT)]


def test_a_question_keeps_the_version_that_first_carried_it(folder):
    design(folder, question("Q1"))
    fold(at=AT)
    design(folder, question("Q1"), question("Q2", OTHER))

    freezing.freeze(ROUND, actor="critic", at="2026-01-03T00:00:00Z")

    first, second = folded(folder).questions
    assert (first.origin, first.by, first.at) == (
        "file:versions/design-v1.md#Q1", "scout", AT)
    assert (second.origin, second.by, second.at) == (
        "file:versions/design-v2.md#Q2", "critic", "2026-01-03T00:00:00Z")
    assert last(folder)["added"] == {"questions": ["Q2"]}


def test_a_question_declared_by_a_fold_holds_its_stages(folder):
    design(folder, question("Q1", blocks='["B2"]'), question("Q2", OTHER),
           question("Q3", "Is it so?", blocks='"none"'), stage("B1"), stage("B2"))

    fold()

    found = folded(folder)
    assert [one.id for one in holds.on_stage(found, "B1")] == ["Q2"]
    assert [one.id for one in holds.on_stage(found, "B2")] == ["Q1", "Q2"]
    assert [one.id for one in holds.on_close(found)] == ["Q1", "Q2", "Q3"]


def test_a_question_declared_by_a_fold_is_shown_with_who_asked_it_and_when(
        folder, capsys):
    design(folder, question("Q1"))
    fold(at=AT)
    capsys.readouterr()

    assert main(["show", ROUND]) == 0

    out = capsys.readouterr().out
    [line] = [one for one in out.splitlines() if "Q1:" in one]
    following = out[out.index(line):]
    assert "scout" in following and AT in following
    [shown] = showing.data(showing.show(ROUND))["questions"]
    assert (shown["id"], shown["by"], shown["at"]) == ("Q1", "scout", AT)


def test_a_stage_a_design_declares_joins_the_plan_with_its_slices(folder):
    design(folder,
           stage("B1", slices=[("1a", "the log and its fold", 40), ("1b", "the verbs", 30)]),
           stage("B2", after='["B1"]', round_="proj-a/a-child"))

    fold(at=AT)

    assert folded(folder).stages == [
        state.Stage(id="B1", repo="proj-a", branch="branch-b1", round=None, after=(),
                    slices=({"id": "1a", "what": "the log and its fold", "minutes": 40},
                            {"id": "1b", "what": "the verbs", "minutes": 30}),
                    why=None, version=1, actor="scout", at=AT),
        state.Stage(id="B2", repo=None, branch=None, round="proj-a/a-child",
                    after=("B1",), slices=(), why=None, version=1, actor="scout", at=AT)]
    assert questions.recorded_stages(folded(folder)) == ("B1", "B2")


def test_a_stage_another_now_waits_for_is_added_at_once(folder):
    design(folder, stage("B1"), stage("B2"), stage("B3"))
    fold()
    design(folder, stage("B1"), stage("B2"), stage("B3", after='["B1", "B2"]'))

    frozen = fold()

    assert frozen.plan.added == {"after": {"B3": ["B1", "B2"]}}
    assert frozen.plan.pending == ()
    assert folded(folder).stages[2].after == ("B1", "B2")


def test_a_wider_hold_is_added_at_once(folder):
    design(folder, question("Q1", blocks='["B1"]'), question("Q2", OTHER, blocks='"none"'),
           question("Q3", "Is it so?", blocks='["B1"]'), stage("B1"), stage("B2"))
    fold()
    design(folder, question("Q1", blocks='["B1", "B2"]'),
           question("Q2", OTHER, blocks='["B2"]'), question("Q3", "Is it so?"),
           stage("B1"), stage("B2"))

    frozen = fold()

    assert frozen.plan.added == {"blocks": {"Q1": ["B1", "B2"], "Q2": ["B2"],
                                            "Q3": "all"}}
    assert frozen.plan.pending == ()
    assert [one.blocks for one in folded(folder).questions] == [
        ("B1", "B2"), ("B2",), "all"]


def test_a_block_that_says_what_the_record_says_adds_nothing(folder):
    design(folder, question("Q1", blocks='["B1", "B2"]'), stage("B1"),
           stage("B2", after='["B1"]'))
    fold()
    before = folded(folder)
    design(folder, question("Q1", blocks='["B2", "B1"]'), stage("B1"),
           stage("B2", after='["B1"]'), prose="Said again.")

    frozen = fold()

    assert (frozen.plan.added, frozen.plan.pending) == ({}, ())
    after = folded(folder)
    assert (after.questions, after.stages) == (before.questions, before.stages)


def test_a_question_asked_on_its_own_and_then_declared_stays_as_it_was_asked(folder):
    asking.ask(ROUND, ASKED, origin="comms:11", actor="planner")
    before = folded(folder).questions

    design(folder, question("Q1"))
    frozen = fold()

    assert frozen.plan.added == {}
    assert folded(folder).questions == before


def test_a_sentence_that_differs_from_the_record_changes_nothing_and_is_named(
        folder, capsys):
    design(folder, question("Q1"))
    fold()
    design(folder, question("Q1", OTHER))
    capsys.readouterr()

    assert main(["fold", ROUND, "--as", "scout"]) == 0

    assert "kept: Q1 keeps the sentence on record" in capsys.readouterr().out
    assert folded(folder).questions[0].text == ASKED
    assert last(folder)["added"] == {}


# -- what a fold does not take away ------------------------------------------------------

def test_what_a_block_takes_away_is_pending_and_changes_nothing(folder):
    design(folder, question("Q1", blocks='["B1", "B2"]'), question("Q2", OTHER),
           question("Q3", "Is it so?"), question("Q4", "Is it not?", blocks='["B1"]'),
           stage("B1"), stage("B2", after='["B1"]'), stage("B3", after='["B1", "B2"]'))
    fold()
    before = folded(folder)
    design(folder, question("Q1", blocks='["B2"]'), question("Q3", "Is it so?",
                                                             blocks='["B1"]'),
           question("Q4", "Is it not?", blocks='"none"'),
           stage("B1"), stage("B3", after='["B1"]'))

    frozen = fold()

    took = (state.Pending("after", "B3", gone=("B2",)), state.Pending("stage", "B2"),
            state.Pending("blocks", "Q1", blocks=("B2",)),
            state.Pending("blocks", "Q3", blocks=("B1",)),
            state.Pending("blocks", "Q4", blocks="none"),
            state.Pending("question", "Q2"))
    assert frozen.plan.pending == took
    assert frozen.plan.added == {}
    after = folded(folder)
    assert (after.questions, after.stages) == (before.questions, before.stages)
    assert versions.pending_removals(after, 2) == took
    assert versions.pending_removals(after, 1) == ()
    assert versions.pending_removals(after, 3) == ()
    assert last(folder)["pending"] == [
        {"what": "after", "id": "B3", "gone": ["B2"]}, {"what": "stage", "id": "B2"},
        {"what": "blocks", "id": "Q1", "blocks": ["B2"]},
        {"what": "blocks", "id": "Q3", "blocks": ["B1"]},
        {"what": "blocks", "id": "Q4", "blocks": "none"},
        {"what": "question", "id": "Q2"}]
    assert [one.id for one in holds.on_stage(after, "B1")] == ["Q1", "Q2", "Q3", "Q4"]


def test_what_a_version_took_away_is_pending_in_each_version_that_leaves_it_out(folder):
    design(folder, question("Q1"), question("Q2", OTHER), stage("B1"), stage("B2"))
    fold()
    design(folder, question("Q1"), stage("B1"))
    fold()
    design(folder, question("Q1"), stage("B1"), prose="Said again.")
    fold()
    design(folder, question("Q1"), question("Q2", OTHER), stage("B1"), stage("B2"))
    fold()

    found = folded(folder)

    gone = (state.Pending("stage", "B2"), state.Pending("question", "Q2"))
    assert [versions.pending_removals(found, n) for n in (1, 2, 3, 4)] == [
        (), gone, gone, ()]
    assert [one.id for one in found.questions] == ["Q1", "Q2"]


def test_a_hold_moved_from_one_stage_to_another_gains_the_new_and_keeps_the_old(folder):
    design(folder, question("Q1", blocks='["B1"]'), stage("B1"), stage("B2"))
    fold()
    design(folder, question("Q1", blocks='["B2"]'), stage("B1"), stage("B2"))

    frozen = fold()

    assert frozen.plan.added == {"blocks": {"Q1": ["B1", "B2"]}}
    assert frozen.plan.pending == (state.Pending("blocks", "Q1", blocks=("B2",)),)
    assert folded(folder).questions[0].blocks == ("B1", "B2")


def test_a_question_no_version_carried_is_not_taken_away_by_a_block_without_it(folder):
    asking.ask(ROUND, ASKED, origin="comms:11", actor="planner")
    design(folder, question("Q2", OTHER))

    frozen = fold()

    assert frozen.plan.pending == ()
    assert [one.id for one in folded(folder).questions] == ["Q1", "Q2"]


def test_a_question_since_ruled_and_left_out_is_not_pending(folder):
    design(folder, question("Q1"), question("Q2", OTHER))
    fold()
    ruling.rule(ROUND, item="Q1", text="They do.", record="comms:12", actor="planner")
    ruling.rule(ROUND, item="park:Q2", record="comms:13", actor="planner")
    design(folder, prose="No question is left.")

    frozen = fold()

    assert frozen.plan.pending == (state.Pending("question", "Q2"),)


def test_a_stage_declared_on_its_own_and_left_out_of_the_block_is_pending(folder):
    log.append(log.round_log(folder), "stage.declared",
               {"id": "B1", "repo": "proj-a", "branch": "branch-b1"}, actor="planner")
    log.append(log.round_log(folder), "stage.declared",
               {"id": "B2", "repo": "proj-a", "branch": "branch-b2"}, actor="planner")
    log.append(log.round_log(folder), "finish.done", {"stage": "B2", "tip": "0" * 40},
               actor="coder")
    design(folder, stage("B3"))

    frozen = fold()

    assert frozen.plan.added == {"stages": ["B3"]}
    assert frozen.plan.pending == (state.Pending("stage", "B1"),)
    assert questions.recorded_stages(folded(folder)) == ("B1", "B2", "B3")


# -- options -----------------------------------------------------------------------------

ABC = '{ a = "prose yes, parsed rows no", b = "everything", c = "nothing" }'


def test_a_question_is_declared_with_its_options(folder):
    design(folder, question("Q1", options=ABC))

    fold()

    assert folded(folder).questions[0].options == (
        ("a", "prose yes, parsed rows no"), ("b", "everything"), ("c", "nothing"))


def test_a_block_with_options_adds_new_keys_and_retires_the_ones_it_leaves_out(folder):
    design(folder, question("Q1", options=ABC))
    fold()
    design(folder, question(
        "Q1", options='{ a = "prose yes, parsed rows no", b = "everything", '
                      'd = "only what is asked" }'))

    frozen = fold()

    assert frozen.plan.added == {"options": {"Q1": {
        "options": {"d": "only what is asked"}, "retired": ["c"]}}}
    found = folded(folder).questions[0]
    assert found.options == (("a", "prose yes, parsed rows no"), ("b", "everything"),
                             ("d", "only what is asked"))
    assert found.retired == ("c",)


def test_options_from_a_block_change_nothing_a_question_holds_up(folder):
    design(folder, question("Q1", blocks='["B1"]', options=ABC), stage("B1"))
    fold()
    before = folded(folder)
    design(folder, question("Q1", blocks='["B1"]',
                            options='{ a = "prose yes, parsed rows no", b = "everything" }'),
           stage("B1"))

    frozen = fold()

    after = folded(folder)
    assert frozen.plan.pending == ()
    assert (after.questions[0].state, after.questions[0].blocks) == (
        before.questions[0].state, before.questions[0].blocks)
    assert holds.on_stage(after, "B1")[0].id == "Q1"


def test_a_block_that_gives_a_question_no_options_changes_none(folder):
    design(folder, question("Q1", options=ABC))
    fold()
    before = folded(folder).questions

    design(folder, question("Q1"))
    frozen = fold()

    assert frozen.plan.added == {}
    assert folded(folder).questions == before


def test_a_retired_key_a_block_lists_is_left_out_by_name_and_the_rest_is_read(
        folder, capsys):
    design(folder, question("Q1", options=ABC))
    fold()
    options.fill(ROUND, "Q1", actor="planner", retire=["c"])
    design(folder, question("Q1", options=ABC.replace("}", ', d = "only what is asked" }')),
           question("Q2", OTHER))
    capsys.readouterr()

    assert main(["fold", ROUND, "--as", "scout"]) == 0

    out = capsys.readouterr().out
    assert "not used: the key c of Q1 was retired and is never used again" in out
    assert last(folder)["added"] == {
        "questions": ["Q2"], "options": {"Q1": {"options": {"d": "only what is asked"}}}}
    found = folded(folder).questions[0]
    assert found.options == (("a", "prose yes, parsed rows no"), ("b", "everything"),
                             ("d", "only what is asked"))
    assert found.retired == ("c",)


def test_a_key_given_another_line_than_it_has_is_refused_at_its_line(folder):
    design(folder, question("Q1", options=ABC))
    fold()
    design(folder, question("Q1", options=ABC.replace("everything", "all of it")))
    before = log.round_log(folder).read_bytes()

    with pytest.raises(BadBlock) as refused:
        fold()

    [problem] = refused.value.problems
    assert problem.line == line_with(folder, "options =")
    assert "Q1 offers 'everything' under b, and a key keeps its text" in problem.what
    assert log.round_log(folder).read_bytes() == before
    assert not versions.file(folder, 2).exists()


def test_options_that_would_leave_a_question_one_live_key_are_refused(folder):
    design(folder, question("Q1", options=ABC))
    fold()
    options.fill(ROUND, "Q1", actor="planner", retire=["c"])
    design(folder, question("Q1", options='{ a = "prose yes, parsed rows no", '
                                          'c = "nothing" }'))
    before = log.round_log(folder).read_bytes()

    with pytest.raises(BadBlock) as refused:
        fold()

    [problem] = refused.value.problems
    assert "2 to 4" in problem.what and "leave it 1" in problem.what
    assert log.round_log(folder).read_bytes() == before


# -- refusals ----------------------------------------------------------------------------

def test_a_round_with_no_design_is_refused(folder):
    before = log.round_log(folder).read_bytes()

    with pytest.raises(Refused) as refused:
        fold()

    [unmet] = refused.value.unmet
    assert "has no design" in unmet.reason and "design.md" in unmet.reason
    assert unmet.fix == f"moxi rounds fold {ROUND}"
    assert untouched(folder, before)


def test_a_round_whose_flow_has_no_design_step_is_refused(tmp_path, add_project, capsys):
    add_project("proj-a", notes_root=tmp_path / "notes")
    opening.open_round(ROUND, description="a placeholder round", actor="planner",
                       flow="fix")
    folder = tmp_path / "notes" / "rounds" / "a-round"
    design(folder, question("Q1"))
    before = log.round_log(folder).read_bytes()

    with pytest.raises(Refused) as refused:
        fold()

    [unmet] = refused.value.unmet
    assert "the flow fix" in unmet.reason and "has no design step" in unmet.reason
    assert unmet.fix == f"moxi rounds mark {ROUND} --flow NAME"
    assert untouched(folder, before)


def test_a_refusal_names_everything_that_stopped_it(tmp_path, add_project, capsys):
    add_project("proj-a", notes_root=tmp_path / "notes")
    opening.open_round(ROUND, description="a placeholder round", actor="planner",
                       flow="fix")

    with pytest.raises(Refused) as refused:
        fold()

    assert len(refused.value.unmet) == 2


def test_a_round_moved_up_to_a_flow_with_a_design_step_is_folded(
        tmp_path, add_project, capsys):
    add_project("proj-a", notes_root=tmp_path / "notes")
    opening.open_round(ROUND, description="a placeholder round", actor="planner",
                       flow="fix")
    folder = tmp_path / "notes" / "rounds" / "a-round"
    design(folder, question("Q1"))
    main(["mark", ROUND, "--flow", "small", "--as", "planner"])

    assert fold().version == 1


@pytest.mark.parametrize("entries, needle, says", [
    (('[[question]]\nid = "Q1"\ntext = "Is it so?"\nblocks = []',), "blocks = []", None),
    (('[[question]]\nid = "Q1"',), "[[question]]", None),
    (('[[stage]]\nid = "B1"\nrepo = "proj-a"\nbranch = "b"\nlands = "soon"',),
     "lands", None),
    (('id = ',), "id = ", None)])
def test_a_block_that_cant_be_read_is_refused_with_its_file_and_line(
        folder, entries, needle, says):
    design(folder, *entries)
    before = log.round_log(folder).read_bytes()

    with pytest.raises(BadBlock) as refused:
        fold()

    assert refused.value.file == folder / "design.md"
    assert line_with(folder, needle) in [one.line for one in refused.value.problems]
    assert untouched(folder, before)


def test_a_design_with_no_block_is_refused(folder):
    (folder / "design.md").write_text("# A design\n\nProse only.\n", encoding="utf-8")
    before = log.round_log(folder).read_bytes()

    with pytest.raises(BadBlock) as refused:
        fold()

    assert refused.value.file == folder / "design.md"
    assert untouched(folder, before)


@pytest.mark.parametrize("item, became", [("Q1", "ruled"), ("withdraw:Q1", "withdrawn")])
def test_the_id_of_a_question_since_ruled_or_withdrawn_is_refused_at_its_line(
        folder, item, became):
    design(folder, question("Q1"), question("Q2", OTHER))
    fold()
    ruling.rule(ROUND, item=item, record="comms:12", actor="planner",
                text="They do." if item == "Q1" else None)
    design(folder, question("Q2", OTHER), question("Q1", "Is it so?"))
    before = log.round_log(folder).read_bytes()

    with pytest.raises(BadBlock) as refused:
        fold()

    [problem] = refused.value.problems
    assert problem.line == line_with(folder, 'id = "Q1"')
    assert f"the round already used Q1: it is {became}, by C1" in problem.what
    assert log.round_log(folder).read_bytes() == before
    assert not versions.file(folder, 2).exists()


def test_the_id_of_a_question_moved_to_the_projects_list_is_refused(folder):
    design(folder, question("Q1"))
    fold()
    main(["ask", "proj-a", "--move", f"{ROUND}:Q1", "--record", "comms:12",
          "--as", "planner"])
    design(folder, question("Q1"), prose="Asked again.")
    before = log.round_log(folder).read_bytes()

    with pytest.raises(BadBlock, match="the round already used Q1: it is moved"):
        fold()

    assert log.round_log(folder).read_bytes() == before


def test_a_prepared_answer_in_a_block_is_refused_as_not_recorded_yet(folder):
    design(folder, question("Q1", options='{ a = "one", b = "two" }') + """

  [question.prepared]
  recommend = "a"
  by = "helper"
  model = "a-model"
  sure = "medium"
  why = "It reads so."
  flips = "A reading the other way."
  read = ["comms:1234567"]

  [[question.prepared.option]]
  key = "a"
  costs = "Little."
  forecloses = "nothing"

  [[question.prepared.option]]
  key = "b"
  costs = "More."
  forecloses = "nothing"
""")
    before = log.round_log(folder).read_bytes()

    with pytest.raises(BadBlock) as refused:
        fold()

    [problem] = refused.value.problems
    assert "an answer prepared in a design's block is not recorded yet" in problem.what
    assert problem.line == line_with(folder, "[question.prepared]")
    assert untouched(folder, before)


@pytest.mark.parametrize("entries, needle, says", [
    ((question("Q1", blocks='["B1", "B7"]'), stage("B1")), "blocks =",
     "the question Q1 names B7, which is no stage of the round"),
    ((stage("B1", after='["B7"]'),), "after =",
     "the stage B1 names B7, which is no stage of the round")])
def test_a_stage_named_that_the_round_and_the_block_do_not_have_is_refused(
        folder, entries, needle, says):
    design(folder, *entries)
    before = log.round_log(folder).read_bytes()

    with pytest.raises(BadBlock) as refused:
        fold()

    [problem] = refused.value.problems
    assert says in problem.what
    assert problem.line == line_with(folder, needle)
    assert untouched(folder, before)


def test_a_question_may_hold_up_a_stage_the_record_has_and_the_block_leaves_out(folder):
    design(folder, stage("B1"))
    fold()
    design(folder, question("Q1", blocks='["B1"]'))

    frozen = fold()

    assert frozen.plan.added == {"questions": ["Q1"]}
    assert frozen.plan.pending == (state.Pending("stage", "B1"),)


def test_every_problem_with_the_record_is_named_in_one_refusal(folder):
    design(folder, question("Q1"))
    fold()
    ruling.rule(ROUND, item="Q1", text="They do.", record="comms:12", actor="planner")
    design(folder, question("Q1"), question("Q2", OTHER, blocks='["B7"]'))

    with pytest.raises(BadBlock) as refused:
        fold()

    assert len(refused.value.problems) == 2


def test_a_round_that_has_ended_is_folded_no_longer(folder):
    design(folder, question("Q1"))
    dropping.drop(ROUND, by="lead", record="comms:12", actor="planner")
    before = log.round_log(folder).read_bytes()

    with pytest.raises(Refused, match="was dropped"):
        fold()

    assert untouched(folder, before)


def test_a_round_that_was_never_opened_is_refused(folder):
    with pytest.raises(NoRound):
        freezing.freeze("proj-a/no-such-round", actor="scout")


def test_a_check_added_to_the_folds_own_refuses_the_block(folder, monkeypatch):
    from moxi_rounds.errors import Problem

    def never(found, block):
        return [Problem(block.stages[0].line_of("repo"), "a placeholder problem",
                        "a placeholder fix")]

    monkeypatch.setattr(freezing, "CHECKS", [never])
    design(folder, stage("B1"))
    before = log.round_log(folder).read_bytes()

    with pytest.raises(BadBlock, match="a placeholder problem"):
        fold()

    assert untouched(folder, before)


# -- a fold that was stopped -------------------------------------------------------------

def test_a_fold_stopped_between_its_copy_and_its_append_is_run_again_to_one_version(
        folder, monkeypatch):
    data = design(folder, question("Q1"), stage("B1"))
    before = log.round_log(folder).read_bytes()

    def stop(*args, **more):
        raise KeyboardInterrupt

    with monkeypatch.context() as patched:
        patched.setattr(log, "append", stop)
        with pytest.raises(KeyboardInterrupt):
            fold()
    left = versions.file(folder, 1)
    assert left.read_bytes() == data
    assert log.round_log(folder).read_bytes() == before
    made = left.stat()

    frozen = fold()

    assert frozen.version == 1
    assert [one["type"] for one in events(folder)] == ["round.opened", "design.frozen"]
    assert sorted(one.name for one in (folder / "versions").iterdir()) == ["design-v1.md"]
    kept = left.stat()
    assert (kept.st_ino, kept.st_mtime_ns) == (made.st_ino, made.st_mtime_ns)
    assert [one.id for one in folded(folder).questions] == ["Q1"]
    assert [one.id for one in folded(folder).stages] == ["B1"]


def test_a_stopped_folds_file_is_written_again_when_the_design_changed_since(
        folder, monkeypatch):
    design(folder, question("Q1"))

    def stop(*args, **more):
        raise KeyboardInterrupt

    with monkeypatch.context() as patched:
        patched.setattr(log, "append", stop)
        with pytest.raises(KeyboardInterrupt):
            fold()
    changed = design(folder, question("Q1"), question("Q2", OTHER))

    frozen = fold()

    assert frozen.version == 1
    assert versions.file(folder, 1).read_bytes() == changed
    assert versions.digest(folded(folder)) == hashlib.sha256(changed).hexdigest()
    assert sorted(one.name for one in (folder / "versions").iterdir()) == ["design-v1.md"]


def test_a_fold_stopped_right_after_its_append_has_added_everything(folder, monkeypatch):
    design(folder, question("Q1", blocks='["B1"]'), stage("B1"))

    def stop(written):
        raise KeyboardInterrupt

    with monkeypatch.context() as patched:
        patched.setattr(write, "ON_APPEND", (stop,))
        with pytest.raises(KeyboardInterrupt):
            fold()

    assert [one["type"] for one in events(folder)] == ["round.opened", "design.frozen"]
    found = folded(folder)
    assert [one.id for one in found.questions] == ["Q1"]
    assert [one.id for one in found.stages] == ["B1"]
    assert [one.id for one in holds.on_stage(found, "B1")] == ["Q1"]
    assert versions.file(folder, 1).is_file()


# -- the design since its last version ---------------------------------------------------

def test_a_design_as_it_was_frozen_last_is_unchanged(folder):
    design(folder, question("Q1"))
    fold()

    assert versions.unchanged(folded(folder), folder, ROUND) is None


def test_a_design_edited_since_its_last_version_is_named_with_the_fold_to_run(folder):
    design(folder, question("Q1"))
    fold()
    fold()
    design(folder, question("Q1"), prose="Edited since.")

    unmet = versions.unchanged(folded(folder), folder, ROUND)

    assert unmet.reason == "design.md changed since v2 was frozen"
    assert unmet.fix == f"moxi rounds fold {ROUND}"


def test_a_design_never_frozen_or_since_removed_is_not_unchanged(folder):
    never = versions.unchanged(folded(folder), folder, ROUND)
    design(folder, question("Q1"))
    fold()
    (folder / "design.md").unlink()

    gone = versions.unchanged(folded(folder), folder, ROUND)

    assert never.reason == f"the design of {ROUND} was never frozen"
    assert gone.reason == "design.md changed since v1 was frozen"


# -- the stages a fold declares, as the other verbs read them ----------------------------

def test_a_question_is_asked_of_a_stage_a_fold_declared(folder):
    design(folder, stage("B1"), stage("B2"))
    fold()

    asked = asking.ask(ROUND, ASKED, origin="comms:11", blocks="B2", actor="planner")
    with pytest.raises(Refused, match="B7"):
        asking.ask(ROUND, OTHER, origin="comms:11", blocks="B7", actor="planner")

    assert asked.id == "Q1"
    assert [one.id for one in holds.on_stage(folded(folder), "B2")] == ["Q1"]
    assert holds.on_stage(folded(folder), "B1") == ()


def test_a_hold_is_widened_onto_a_stage_a_fold_declared(folder):
    design(folder, question("Q1", blocks='["B1"]'), stage("B1"), stage("B2"))
    fold()

    marking.widen(ROUND, "Q1", "B1,B2", actor="planner")

    assert folded(folder).questions[0].blocks == ("B1", "B2")


def test_a_skip_names_a_stage_a_fold_declared(folder):
    design(folder, question("Q1"), stage("B1"))
    fold()

    ruling.rule(ROUND, item="skip:docket:B1", record="comms:12", actor="planner")

    assert holds.on_stage(folded(folder), "B1") == ()


# -- the command, and where a version is shown -------------------------------------------

def test_the_command_says_what_was_frozen_added_and_left_pending(folder, capsys):
    design(folder, question("Q1", blocks='["B1"]'), question("Q2", OTHER), stage("B1"),
           stage("B2", after='["B1"]'))
    assert main(["fold", ROUND, "--as", "scout"]) == 0
    first = capsys.readouterr().out
    data = design(folder, question("Q1", blocks='["B1", "B2"]'), stage("B1"),
                  stage("B2"), stage("B3", after='["B1"]'))

    assert main(["fold", ROUND, "--as", "scout"]) == 0

    assert first.splitlines()[0] == f"froze the design of {ROUND} as v1"
    assert "added: questions Q1, Q2" in first and "added: stages B1, B2" in first
    assert capsys.readouterr().out.splitlines() == [
        f"froze the design of {ROUND} as v2",
        f"file: {folder / 'versions' / 'design-v2.md'}",
        f"sha256: {hashlib.sha256(data).hexdigest()}",
        "added: stages B3",
        "added: Q1 now holds up B1, B2",
        "pending: B2 waits for B1 no longer",
        "pending: the question Q2 is gone from the block",
        "what is pending changes nothing until a verdict finds this version "
        "publishable or the operator rules it"]


def test_the_real_command_folds(folder, capsys):
    from moxi import cli

    design(folder, question("Q1"))

    assert cli.main(["rounds", "fold", ROUND, "--as", "scout"]) == 0
    assert last(folder)["actor"] == "scout"


def test_the_command_refuses_with_the_file_the_line_and_the_fix(folder, capsys):
    design(folder, '[[question]]\nid = "Q1"\ntext = "Is it so?"\nblocks = []')

    assert main(["fold", ROUND]) == 3

    err = capsys.readouterr().err
    assert str(folder / "design.md") in err
    assert f"line {line_with(folder, 'blocks = []')}:" in err
    assert "fix:" in err


def test_fold_takes_no_flag_for_a_change_of_records_only(folder, capsys):
    design(folder, question("Q1"))

    assert main(["fold", ROUND, "--records-only", "--record", "comms:12"]) == 2
    assert [one["type"] for one in events(folder)] == ["round.opened"]


def test_help_says_what_fold_does(capsys):
    assert main(["fold", "--help"]) == 0
    assert "Freeze the round's design as its next version" in capsys.readouterr().out


def test_stages_the_version_and_what_it_took_away_are_shown_and_in_the_note(
        folder, capsys):
    design(folder, question("Q1"), stage("B1", slices=[("1a", "the log", 40)]),
           stage("B2", after='["B1"]', round_="proj-a/a-child"))
    fold()
    data = design(folder, stage("B1", slices=[("1a", "the log", 40)]),
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
    note = (folder / "round-note.md").read_text(encoding="utf-8")
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


def test_stages_the_version_and_what_it_took_away_are_in_the_index(folder):
    design(folder, question("Q1"), stage("B1"), stage("B2", after='["B1"]'))
    fold()
    data = design(folder, question("Q1"), stage("B1"))
    fold(at=AT)

    found = index.read("proj-a", folder.parent, "a-round")

    assert [(item.id, item.body["repo"], item.body["after"])
            for item in found.of(index.STAGE)] == [("B1", "proj-a", []),
                                                   ("B2", "proj-a", ["B1"])]
    assert found.of(index.VERSION) == (index.Item(index.VERSION, "v2", {
        "version": 2, "digest": hashlib.sha256(data).hexdigest(), "at": AT,
        "by": "scout", "records_only": False}),)
    assert [(item.id, item.body["said"]) for item in found.of(index.PENDING)] == [
        ("v2:stage:B2", "the stage B2 is gone from the block")]
    assert [item.id for item in found.of(index.QUESTION)] == ["Q1"]

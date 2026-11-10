"""Stages before any build: what a design may not say of one, how one leaves the plan,
and what one waits for."""

from __future__ import annotations

import pytest

from moxi_rounds import (asking, corrections, dropping, freezing, holds, index, log,
                         opening, questions, ruling, showing, stages, state, write)
from moxi_rounds.cli import main
from moxi_rounds.errors import BadArgument, BadBlock, Refused

ROUND = "proj-a/a-round"
CHILD = "proj-a/a-child"
FLOWS = ("full", "small", "fix")
DESIGNED = ("full", "small")


@pytest.fixture
def opened(tmp_path, add_project, capsys):
    """`opened(flow)`: open `a-round` of `proj-a` on that flow and return its folder."""
    add_project("proj-a", notes_root=tmp_path / "notes")

    def open_on(flow: str):
        opening.open_round(ROUND, description="a placeholder round", actor="planner",
                           flow=flow)
        capsys.readouterr()
        return tmp_path / "notes" / "rounds" / "a-round"

    return open_on


def declare(folder, stage_id, *, repo=None, after=()):
    """Record a stage as a build in a repo of its own, through the log."""
    log.append(log.round_log(folder), "stage.declared",
               {"id": stage_id, "repo": repo or f"repo-{stage_id.lower()}",
                "branch": f"branch-{stage_id.lower()}", "after": list(after)},
               actor="planner")


def planned(folder, *ids):
    for stage_id in ids:
        declare(folder, stage_id)


def stage(id_, *, repo=None, branch=None, round_=None, after="[]") -> str:
    """One stage of a block: a build in a repo of its own, or the round `round_`."""
    lines = ["[[stage]]", f'id = "{id_}"']
    if round_ is not None:
        lines.append(f'round = "{round_}"')
    else:
        lines += [f'repo = "{repo or "repo-" + id_.lower()}"',
                  f'branch = "{branch or "branch-" + id_.lower()}"']
    lines.append(f"after = {after}")
    return "\n".join(lines)


def design(folder, *entries) -> None:
    text = ("# A design\n\nWhat the round builds.\n\n```toml rounds\n"
            + "\n\n".join(entries) + "\n```\n")
    (folder / "design.md").write_text(text, encoding="utf-8")


def fold():
    return freezing.freeze(ROUND, actor="scout")


def rule(item, **more):
    return ruling.rule(ROUND, item=item, record="comms:1234567", actor="planner", **more)


def events(folder):
    return log.read(log.round_log(folder))


def folded(folder):
    return state.fold(events(folder))


def last(folder):
    event = events(folder)[-1]
    return {key: event[key] for key in event if key not in ("v", "n", "at")}


def raw(folder) -> bytes:
    return log.round_log(folder).read_bytes()


def line_with(folder, needle: str) -> int:
    lines = (folder / "design.md").read_text(encoding="utf-8").splitlines()
    return next(n for n, line in enumerate(lines, 1) if needle in line)


def build(folder, stage_id, *more):
    """Record that a build of the stage was commissioned, and each event of `more`
    after it, as the verbs that make them will."""
    path = log.round_log(folder)
    log.append(path, "build.commissioned",
               {"stage": stage_id, "repo": f"repo-{stage_id.lower()}",
                "branch": f"branch-{stage_id.lower()}"}, actor="coder")
    for name in more:
        fields = ({"stage": stage_id, "tip": "0" * 40} if name == "finish.done" else
                  {"stage": stage_id, "by": "lead"})
        log.append(path, name, fields, actor="coder")


def applied(folder, *taken) -> None:
    """Append a verdict that found the design publishable and took `taken` out of
    the record, as recording such a verdict does."""
    log.append(log.round_log(folder), "verdict.recorded",
               {"kind": "design", "verdict": "publishable", "design": 1,
                "applied": list(taken)}, actor="critic")


def append(folder, entry) -> None:
    """Append an event a later verb writes, as that verb will."""
    log.append(log.round_log(folder), entry.type, dict(entry.fields), actor="critic")


# -- a stage's repo, branch and child round never change ---------------------------------

@pytest.mark.parametrize("flow", DESIGNED)
@pytest.mark.parametrize(("again", "where", "said"), [
    (stage("B1", repo="repo-other"), "repo = ",
     "B1 is recorded as repo-b1 on branch-b1, and the block declares it as "
     "repo-other on branch-b1: a stage's repo never changes"),
    (stage("B1", branch="branch-other"), "branch = ",
     "the block declares it as repo-b1 on branch-other: a stage's branch never "
     "changes"),
    (stage("B1", round_=CHILD), "[[stage]]",
     f"the block declares it as the round {CHILD}")])
def test_a_design_that_gives_a_recorded_stage_another_place_is_refused(
        opened, flow, again, where, said):
    folder = opened(flow)
    design(folder, stage("B1"))
    fold()
    design(folder, again)
    before = raw(folder)

    with pytest.raises(BadBlock) as refused:
        fold()

    [problem] = refused.value.problems
    assert said in problem.what and "different work is a new id" in problem.what
    assert problem.line == line_with(folder, where)
    assert raw(folder) == before
    assert not (folder / "versions" / "design-v2.md").exists()


@pytest.mark.parametrize("flow", DESIGNED)
def test_a_stage_that_stands_for_a_round_keeps_that_round(opened, flow):
    folder = opened(flow)
    design(folder, stage("B1", round_=CHILD))
    fold()
    before = raw(folder)

    design(folder, stage("B1", round_="proj-a/another"))
    with pytest.raises(BadBlock, match="a stage's round never changes"):
        fold()
    design(folder, stage("B1"))
    with pytest.raises(BadBlock, match=f"B1 is recorded as the round {CHILD}, and "
                                       "the block declares it as repo-b1 on"):
        fold()

    assert raw(folder) == before


@pytest.mark.parametrize("flow", DESIGNED)
def test_a_design_that_declares_a_recorded_stage_as_it_is_freezes(opened, flow):
    folder = opened(flow)
    design(folder, stage("B1"))
    fold()

    assert fold().version == 2
    assert [one.id for one in folded(folder).stages] == ["B1"]


def test_a_stage_declared_as_it_was_commissioned_keeps_its_place_across_a_step_up(
        opened):
    folder = opened("fix")
    declare(folder, "B1")
    assert main(["mark", ROUND, "--flow", "small", "--as", "planner"]) == 0
    design(folder, stage("B1", branch="branch-other"))
    before = raw(folder)

    with pytest.raises(BadBlock, match="a stage's branch never changes"):
        fold()
    assert raw(folder) == before

    design(folder, stage("B1"))
    assert fold().plan.added == {}
    [kept] = folded(folder).stages
    assert (kept.id, kept.repo, kept.branch, kept.version) == (
        "B1", "repo-b1", "branch-b1", None)


@pytest.mark.parametrize("flow", FLOWS)
def test_a_change_of_flow_keeps_every_stage_and_what_it_waits_for(opened, flow):
    folder = opened(flow)
    planned(folder, "B1", "B2")
    assert main(["mark", ROUND, "B2", "--after", "B1"]) == 0
    before = folded(folder).stages

    for move in ("full", "fix", "small"):
        if move != folded(folder).flow:
            rule(f"flow:{move}")

    assert folded(folder).stages == before
    assert stages.state_of(folded(folder), "B1") == stages.PLANNED


@pytest.mark.parametrize("flow", DESIGNED)
def test_a_design_that_leaves_a_recorded_stage_out_takes_nothing_away(opened, flow):
    folder = opened(flow)
    planned(folder, "B1", "B2")
    design(folder, stage("B2"))

    frozen = fold()

    assert [(one.what, one.id) for one in frozen.plan.pending] == [("stage", "B1")]
    assert [one.id for one in stages.in_plan(folded(folder))] == ["B1", "B2"]


# -- one stage to a repo where the design is not verified --------------------------------

def test_a_design_with_two_stages_in_one_repo_is_refused_where_it_is_not_verified(
        opened):
    folder = opened("small")
    design(folder, stage("B1", repo="proj-a"), stage("B2", repo="proj-a"))
    before = raw(folder)

    with pytest.raises(BadBlock) as refused:
        fold()

    [problem] = refused.value.problems
    assert ("B2 would be a second stage in proj-a, which has B1: the flow small does "
            "not verify its design") in problem.what
    assert "moxi rounds mark ROUND --flow NAME" in problem.fix
    assert "verify-design" in problem.fix
    assert problem.line == line_with(folder, 'branch = "branch-b2"') - 1
    assert raw(folder) == before and not (folder / "versions").exists()


def test_a_design_that_adds_a_second_stage_to_a_recorded_ones_repo_is_refused(opened):
    folder = opened("small")
    design(folder, stage("B1", repo="proj-a"))
    fold()
    design(folder, stage("B1", repo="proj-a"), stage("B2", repo="proj-a"))
    before = raw(folder)

    with pytest.raises(BadBlock, match="B2 would be a second stage in proj-a, which "
                                       "has B1"):
        fold()

    assert raw(folder) == before


def test_a_design_with_two_stages_in_one_repo_freezes_where_it_is_verified(opened):
    folder = opened("full")
    design(folder, stage("B1", repo="proj-a"), stage("B2", repo="proj-a"))

    assert fold().plan.added == {"stages": ["B1", "B2"]}


def test_the_step_up_a_refusal_names_lets_the_design_freeze(opened):
    folder = opened("small")
    design(folder, stage("B1", repo="proj-a"), stage("B2", repo="proj-a"))
    with pytest.raises(BadBlock):
        fold()

    assert main(["mark", ROUND, "--flow", "full", "--as", "planner"]) == 0

    assert fold().plan.added == {"stages": ["B1", "B2"]}


def test_a_stage_to_each_repo_and_a_round_in_place_of_one_freeze_where_not_verified(
        opened):
    folder = opened("small")
    design(folder, stage("B1", repo="proj-a"), stage("B2", repo="proj-b"),
           stage("B3", round_=CHILD), stage("B4", round_="proj-a/another"))

    assert fold().plan.added == {"stages": ["B1", "B2", "B3", "B4"]}


def test_a_repo_whose_stage_left_the_plan_takes_another(opened):
    folder = opened("small")
    design(folder, stage("B1", repo="proj-a"))
    fold()
    rule("unstage:B1")
    design(folder, stage("B2", repo="proj-a"))

    assert fold().plan.added == {"stages": ["B2"]}


def test_stages_that_shared_a_repo_before_a_step_down_are_declared_again(opened):
    folder = opened("full")
    design(folder, stage("B1", repo="proj-a"), stage("B2", repo="proj-a"))
    fold()
    rule("flow:small")

    assert fold().version == 2

    design(folder, stage("B1", repo="proj-a"), stage("B2", repo="proj-a"),
           stage("B3", repo="proj-a"))
    with pytest.raises(BadBlock, match="B3 would be a second stage in proj-a"):
        fold()


# -- leaving the plan --------------------------------------------------------------------

@pytest.mark.parametrize("flow", FLOWS)
def test_his_ruling_takes_a_stage_with_no_build_out_of_the_plan(opened, flow):
    folder = opened(flow)
    planned(folder, "B1", "B2")
    count = len(events(folder))

    ruled = rule("unstage:B1")

    assert len(events(folder)) == count + 1
    assert last(folder) == {"actor": "planner", "type": "ruling.recorded",
                            "id": ruled.id, "record": "comms:1234567",
                            "kind": "process", "rules": "unstage:B1"}
    found = folded(folder)
    assert stages.state_of(found, "B1") == stages.LEFT == "left"
    assert stages.state_of(found, "B2") == stages.PLANNED == "planned"
    assert stages.state_of(found, "B9") is None
    assert [one.id for one in stages.in_plan(found)] == ["B2"]
    assert questions.left_stages(found) == {"B1"}
    assert [one.id for one in found.stages] == ["B1", "B2"]


def test_a_stage_that_left_holds_nothing_and_is_shown_as_left(opened, capsys):
    folder = opened("full")
    planned(folder, "B1", "B2")
    asking.ask(ROUND, "Is it so?", origin="comms:1234568", blocks="B1,B2",
               actor="scout")
    assert [one.id for one in holds.on_stage(folded(folder), "B1")] == ["Q1"]

    assert main(["rule", ROUND, "unstage:B1", "--record", "comms:1234567"]) == 0

    out = capsys.readouterr().out
    assert "Recorded as C1: unstage:B1" in out
    assert "B1 left the plan of proj-a/a-round and does not come back" in out
    assert holds.on_stage(folded(folder), "B1") == ()
    assert [one.id for one in holds.on_stage(folded(folder), "B2")] == ["Q1"]
    assert main(["show", ROUND]) == 0
    assert "stage B1: repo-b1 on branch-b1; left the plan" in capsys.readouterr().out
    found = index.read("proj-a", folder.parent, "a-round")
    assert [item.id for item in found.of(index.STAGE)] == ["B2"]


@pytest.mark.parametrize("flow", FLOWS)
@pytest.mark.parametrize(("built", "said"), [
    (("build.commissioned",), "B1 has an open build, and a stage with one leaves only "
                              "after that build is dropped"),
    (("build.commissioned", "finish.done"),
     "B1 is finished: its work is met, and a finished stage has nothing to leave")])
def test_a_stage_with_an_open_or_finished_build_does_not_leave(opened, flow, built,
                                                               said):
    folder = opened(flow)
    planned(folder, "B1")
    build(folder, "B1", *built[1:])
    before = raw(folder)

    with pytest.raises(Refused, match=said):
        rule("unstage:B1")

    assert raw(folder) == before
    assert stages.state_of(folded(folder), "B1") == stages.PLANNED


@pytest.mark.parametrize("flow", FLOWS)
def test_a_stage_whose_build_was_dropped_leaves(opened, flow):
    folder = opened(flow)
    planned(folder, "B1")
    build(folder, "B1", "build.dropped")
    assert stages.build_of(folded(folder), "B1") is None

    rule("unstage:B1")

    assert stages.state_of(folded(folder), "B1") == stages.LEFT


def test_what_a_stages_build_is_follows_the_log(opened):
    folder = opened("fix")
    planned(folder, "B1", "B2")
    assert stages.build_of(folded(folder), "B1") is None
    build(folder, "B1")
    assert stages.build_of(folded(folder), "B1") == stages.BUILDING == "open"
    build(folder, "B1", "build.dropped")
    assert stages.build_of(folded(folder), "B1") is None
    build(folder, "B1", "finish.done")
    assert stages.build_of(folded(folder), "B1") == stages.FINISHED == "finished"
    assert stages.build_of(folded(folder), "B2") is None


@pytest.mark.parametrize(("item", "said"), [
    ("unstage:B9", "B9 is not a stage of proj-a/a-round: its stages are B1, B2"),
    ("unstage:B2", "B2 left the plan already, and a stage leaves once")])
def test_a_stage_the_plan_does_not_hold_is_not_taken_out(opened, item, said):
    folder = opened("small")
    planned(folder, "B1", "B2")
    rule("unstage:B2")
    before = raw(folder)

    with pytest.raises(Refused, match=said):
        rule(item)

    assert raw(folder) == before


def test_a_ruling_that_takes_a_stage_out_needs_his_pointer(opened, capsys):
    folder = opened("fix")
    planned(folder, "B1")
    before = raw(folder)

    assert main(["rule", ROUND, "unstage:B1"]) == 3

    assert ("run: moxi rounds rule proj-a/a-round unstage:B1 --record PTR"
            in capsys.readouterr().err)
    assert raw(folder) == before


@pytest.mark.parametrize("item", ["unstage:", "unstage:Q1", "unstage:B1,B2",
                                  "unafter:B1", "unafter:B1=", "unafter:=B1",
                                  "unafter:B1=B2,B3", "unafter:B1=all"])
def test_an_item_that_names_no_stage_is_not_a_ruling(opened, item):
    folder = opened("fix")
    planned(folder, "B1", "B2", "B3")
    before = raw(folder)

    with pytest.raises(BadArgument):
        rule(item)

    assert raw(folder) == before


@pytest.mark.parametrize("item", ["unstage:B1", "unafter:B2=B1"])
def test_a_ruling_of_a_stage_is_of_a_round_and_replaces_none(opened, item):
    folder = opened("fix")
    planned(folder, "B1")
    declare(folder, "B2", after=["B1"])
    before = raw(folder)

    with pytest.raises(BadArgument, match="is of a round, and proj-a is a project"):
        ruling.rule("proj-a", item=item, record="comms:1234567", actor="planner")
    with pytest.raises(BadArgument, match="leave --supersedes out"):
        rule(item, supersedes="C1")
    with pytest.raises(BadArgument, match="leave --stages out"):
        rule(item, stages="B1")

    assert raw(folder) == before


def stopped_after_its_append(monkeypatch, act):
    def stop(written):
        raise KeyboardInterrupt

    with monkeypatch.context() as patched:
        patched.setattr(write, "ON_APPEND", (stop,))
        with pytest.raises(KeyboardInterrupt):
            act()


def test_a_ruling_that_takes_a_stage_out_stopped_after_its_append_is_whole(
        opened, monkeypatch):
    folder = opened("small")
    planned(folder, "B1")
    count = len(events(folder))

    stopped_after_its_append(monkeypatch, lambda: rule("unstage:B1"))

    with pytest.raises(Refused, match="B1 left the plan already"):
        rule("unstage:B1")
    assert len(events(folder)) == count + 1
    assert stages.state_of(folded(folder), "B1") == stages.LEFT


@pytest.mark.parametrize("flow", FLOWS)
def test_a_stage_taken_out_is_not_put_back_by_taking_the_ruling_back(opened, flow):
    folder = opened(flow)
    planned(folder, "B1")
    rule("unstage:B1")
    before = raw(folder)

    with pytest.raises(Refused) as refused:
        rule("revoke:C1")

    [unmet] = refused.value.unmet
    assert ("C1 took B1 out of the plan of proj-a/a-round, and a stage that left does "
            "not come back") in unmet.reason
    assert "declared again under a new id" in unmet.reason
    assert unmet.fix == ("moxi rounds show proj-a/a-round" if flow == "fix" else
                         "moxi rounds fold proj-a/a-round")
    assert raw(folder) == before


@pytest.mark.parametrize("flow", DESIGNED)
@pytest.mark.parametrize("how", ["ruling", "verdict"])
def test_a_design_that_declares_a_stage_that_left_is_refused(opened, flow, how):
    folder = opened(flow)
    design(folder, stage("B1"))
    fold()
    if how == "ruling":
        rule("unstage:B1")
    else:
        applied(folder, {"what": "stage", "id": "B1"})
    design(folder, stage("B1"), stage("B2"))
    before = raw(folder)

    with pytest.raises(BadBlock) as refused:
        fold()

    [problem] = refused.value.problems
    assert ("B1 left the plan of the round, and a stage that left does not come "
            "back") in problem.what
    assert "an id the round has not used" in problem.fix
    assert problem.line == line_with(folder, 'id = "B1"')
    assert raw(folder) == before
    assert not (folder / "versions" / "design-v2.md").exists()


def test_a_stage_that_left_is_refused_with_whatever_place_the_design_gives_it(opened):
    folder = opened("full")
    design(folder, stage("B1"))
    fold()
    rule("unstage:B1")
    design(folder, stage("B1", repo="repo-other"))

    with pytest.raises(BadBlock) as refused:
        fold()

    assert len(refused.value.problems) == 1


@pytest.mark.parametrize("flow", DESIGNED)
def test_the_work_of_a_stage_that_left_is_planned_again_under_a_new_id(opened, flow):
    folder = opened(flow)
    design(folder, stage("B1"))
    fold()
    rule("unstage:B1")
    design(folder, stage("B2", repo="repo-b1", branch="branch-b1"))

    frozen = fold()

    assert frozen.plan.added == {"stages": ["B2"]}
    assert frozen.plan.pending == ()
    assert [one.id for one in stages.in_plan(folded(folder))] == ["B2"]


def test_a_removal_a_verdict_applies_takes_the_stage_out(opened):
    folder = opened("full")
    planned(folder, "B1", "B2")
    assert stages.cannot_leave(folded(folder), "B1") is None

    applied(folder, {"what": "stage", "id": "B1"})

    found = folded(folder)
    assert stages.state_of(found, "B1") == stages.LEFT
    assert stages.cannot_leave(found, "B1") == ("B1 left the plan already, and a "
                                                "stage leaves once")
    with pytest.raises(Refused, match="B1 left the plan already"):
        rule("unstage:B1")


def test_why_a_stage_stays_says_what_its_build_is(opened):
    folder = opened("full")
    planned(folder, "B1", "B2")
    build(folder, "B1")
    build(folder, "B2", "finish.done")
    found = folded(folder)

    assert "B1 has an open build" in stages.cannot_leave(found, "B1")
    assert "B2 is finished" in stages.cannot_leave(found, "B2")
    assert stages.cannot_leave(found, "B9", name=ROUND) == (
        "B9 is not a stage of proj-a/a-round: its stages are B1, B2")


def test_a_narrowing_a_verdict_applies_takes_the_waits_off(opened):
    folder = opened("full")
    planned(folder, "B1", "B2")
    declare(folder, "B3", after=["B1", "B2"])

    applied(folder, {"what": "after", "id": "B3", "gone": ["B1"]})

    assert stages.find(folded(folder), "B3").after == ("B2",)


# -- what a stage waits for --------------------------------------------------------------

@pytest.mark.parametrize("flow", FLOWS)
def test_anyone_makes_a_stage_wait_for_more(opened, flow, capsys):
    folder = opened(flow)
    planned(folder, "B1", "B2")
    declare(folder, "B3", after=["B1"])
    count = len(events(folder))

    assert main(["mark", ROUND, "B3", "--after", "B1,B2", "--as", "scout"]) == 0

    assert capsys.readouterr().out == "B3   after: B1, B2   added: B2\n"
    assert len(events(folder)) == count + 1
    assert last(folder) == {"actor": "scout", "type": "stage.changed", "id": "B3",
                            "after": ["B2"]}
    assert stages.find(folded(folder), "B3").after == ("B1", "B2")


def test_a_wait_added_is_shown_and_is_in_the_index(opened, capsys):
    folder = opened("full")
    planned(folder, "B1", "B2")

    marked = stages.wait(ROUND, "B2", "B1", actor="scout")

    assert (marked.name, marked.stage.after, marked.added) == (ROUND, ("B1",), ("B1",))
    assert main(["show", ROUND]) == 0
    out = capsys.readouterr().out
    assert "stage B2: repo-b2 on branch-b2, after B1" in out
    assert "stage.changed  B2, also after B1" in out
    found = index.read("proj-a", folder.parent, "a-round")
    assert [(item.id, item.body["after"]) for item in found.of(index.STAGE)] == [
        ("B1", []), ("B2", ["B1"])]


@pytest.mark.parametrize(("stage_id", "after", "said"), [
    ("B9", "B1", "B9 is not a stage of proj-a/a-round: its stages are B1, B2, B3"),
    ("B1", "B8,B9", "B8, B9 is not a stage of proj-a/a-round"),
    ("B1", "B1", "B1 can't wait for itself"),
    ("B1", "B2", "B2 waits for B1 already, directly or through other stages"),
    ("B1", "B3", "B3 waits for B1 already, directly or through other stages"),
    ("B3", "B2", "B3 waits for B2 already, so there is nothing to add")])
def test_a_wait_that_names_no_stage_or_closes_a_circle_or_adds_nothing_is_refused(
        opened, stage_id, after, said):
    folder = opened("small")
    planned(folder, "B1")
    declare(folder, "B2", after=["B1"])
    declare(folder, "B3", after=["B2"])
    before = raw(folder)

    with pytest.raises(Refused, match=said):
        stages.wait(ROUND, stage_id, after, actor="scout")

    assert raw(folder) == before


def test_a_circle_is_refused_naming_the_ruling_that_takes_a_wait_off(opened, capsys):
    folder = opened("fix")
    planned(folder, "B1")
    declare(folder, "B2", after=["B1"])
    before = raw(folder)

    assert main(["mark", ROUND, "B1", "--after", "B2"]) == 3

    assert ("run: moxi rounds rule proj-a/a-round unafter:B<n>=B<m> --record PTR"
            in capsys.readouterr().err)
    assert raw(folder) == before


@pytest.mark.parametrize("argv", [
    ("B1", "--after", ""), ("B1", "--after", "all"), ("B1", "--after", "B2,B2"),
    ("B1", "--after", "B2,Q1"), ("Q1", "--after", "B2"), ("--after", "B2"),
    ("B1", "--after", "B2", "--record", "comms:1234567"),
    ("B1", "--after", "B2", "--reopen")])
def test_a_wait_that_cannot_be_read_is_a_wrong_use(opened, argv, capsys):
    folder = opened("fix")
    planned(folder, "B1", "B2")
    before = raw(folder)

    assert main(["mark", ROUND, *argv]) == 2

    assert raw(folder) == before


def test_a_round_that_ended_takes_no_wait_and_loses_no_stage(opened):
    folder = opened("fix")
    planned(folder, "B1", "B2")
    dropping.drop(ROUND, by="lead", record="comms:1234569", actor="planner")
    before = raw(folder)

    with pytest.raises(Refused, match="dropped"):
        stages.wait(ROUND, "B2", "B1", actor="scout")
    with pytest.raises(Refused, match="dropped"):
        rule("unstage:B1")

    assert raw(folder) == before


def test_a_wait_stopped_after_its_append_is_whole(opened, monkeypatch):
    folder = opened("fix")
    planned(folder, "B1", "B2")
    count = len(events(folder))

    stopped_after_its_append(monkeypatch,
                             lambda: stages.wait(ROUND, "B2", "B1", actor="scout"))

    with pytest.raises(Refused, match="nothing to add"):
        stages.wait(ROUND, "B2", "B1", actor="scout")
    assert len(events(folder)) == count + 1


@pytest.mark.parametrize("flow", FLOWS)
def test_his_ruling_takes_one_wait_off_a_stage(opened, flow, capsys):
    folder = opened(flow)
    planned(folder, "B1", "B2")
    declare(folder, "B3", after=["B1", "B2"])
    count = len(events(folder))

    assert main(["rule", ROUND, "unafter:B3=B1", "--record", "comms:1234567",
                 "--as", "planner"]) == 0

    out = capsys.readouterr().out
    assert "Recorded as C1: unafter:B3=B1" in out
    assert ("B3 waits for B1 no longer; anyone adds the wait again: moxi rounds mark "
            "proj-a/a-round B3 --after B1") in out
    assert len(events(folder)) == count + 1
    assert last(folder) == {"actor": "planner", "type": "ruling.recorded", "id": "C1",
                            "record": "comms:1234567", "kind": "process",
                            "rules": "unafter:B3=B1"}
    assert stages.find(folded(folder), "B3").after == ("B2",)


@pytest.mark.parametrize(("item", "said"), [
    ("unafter:B9=B1", "B9 is not a stage of proj-a/a-round: its stages are B1, B2"),
    ("unafter:B2=B9", "B2 does not wait for B9: it waits for B1"),
    ("unafter:B1=B2", "B1 does not wait for B2: it waits for no stage")])
def test_a_wait_a_stage_does_not_have_is_not_taken_off(opened, item, said):
    folder = opened("small")
    planned(folder, "B1")
    declare(folder, "B2", after=["B1"])
    before = raw(folder)

    with pytest.raises(Refused, match=said):
        rule(item)

    assert raw(folder) == before


def test_a_wait_taken_off_is_added_again_by_a_mark_and_never_by_taking_back(opened):
    folder = opened("full")
    planned(folder, "B1")
    declare(folder, "B2", after=["B1"])
    rule("unafter:B2=B1")
    before = raw(folder)

    with pytest.raises(Refused) as refused:
        rule("revoke:C1")

    [unmet] = refused.value.unmet
    assert "C1 took B1 off what B2 waits for" in unmet.reason
    assert unmet.fix == "moxi rounds mark proj-a/a-round B2 --after B1"
    assert raw(folder) == before

    stages.wait(ROUND, "B2", "B1", actor="scout")
    assert stages.find(folded(folder), "B2").after == ("B1",)
    rule("unafter:B2=B1")
    assert stages.find(folded(folder), "B2").after == ()


def test_a_wait_taken_off_stopped_after_its_append_is_whole(opened, monkeypatch):
    folder = opened("fix")
    planned(folder, "B1")
    declare(folder, "B2", after=["B1"])
    count = len(events(folder))

    stopped_after_its_append(monkeypatch, lambda: rule("unafter:B2=B1"))

    with pytest.raises(Refused, match="B2 does not wait for B1"):
        rule("unafter:B2=B1")
    assert len(events(folder)) == count + 1


def test_a_wait_taken_off_lets_a_stage_wait_the_other_way(opened):
    folder = opened("fix")
    planned(folder, "B1")
    declare(folder, "B2", after=["B1"])

    rule("unafter:B2=B1")
    stages.wait(ROUND, "B1", "B2", actor="scout")

    assert stages.find(folded(folder), "B1").after == ("B2",)


@pytest.mark.parametrize("flow", FLOWS)
def test_a_stage_waits_for_the_ones_before_it_that_are_not_finished(opened, flow):
    folder = opened(flow)
    planned(folder, "B1", "B2")
    declare(folder, "B3", after=["B1", "B2"])
    assert stages.waits_for(folded(folder), "B3") == ("B1", "B2")
    assert stages.waits_for(folded(folder), "B1") == ()
    assert stages.waits_for(folded(folder), "B9") == ()

    build(folder, "B1")
    assert stages.waits_for(folded(folder), "B3") == ("B1", "B2")
    build(folder, "B1", "finish.done")
    assert stages.waits_for(folded(folder), "B3") == ("B2",)
    build(folder, "B2", "finish.done")
    assert stages.waits_for(folded(folder), "B3") == ()


def test_reading_a_stage_changes_nothing(opened):
    folder = opened("full")
    planned(folder, "B1")
    declare(folder, "B2", after=["B1"])
    found = folded(folder)
    before = raw(folder)

    twice = [(stages.waits_for(found, "B2"), stages.state_of(found, "B1"),
              stages.build_of(found, "B1"), stages.cannot_leave(found, "B1"))
             for _ in range(2)]

    assert twice[0] == twice[1] == (("B1",), "planned", None, None)
    assert raw(folder) == before and folded(folder) == found


# -- the two rulings among the kinds -----------------------------------------------------

@pytest.mark.parametrize(("rules", "name"), [
    ("unstage:B1", "a stage taken out of the plan"),
    ("unafter:B2=B1", "a wait taken off a stage")])
def test_neither_ruling_of_a_stage_is_taken_back_or_ruled_again(rules, name):
    kind = corrections.kind_of(rules)

    assert (kind.name, kind.undo, kind.again) == (name, None, False)
    assert corrections.item_of(rules) == rules


def test_the_history_says_what_changed_of_a_stage():
    assert showing.history_of({"type": "stage.changed", "id": "B1",
                               "removed": True}) == "B1, left the plan"
    assert showing.history_of({"type": "stage.changed", "id": "B2",
                               "gone": ["B1"]}) == "B2, no longer after B1"

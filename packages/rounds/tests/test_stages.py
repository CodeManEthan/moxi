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
        opening.open_round(ROUND, description="a placeholder round", by="planner",
                           flow=flow)
        capsys.readouterr()
        return tmp_path / "notes" / "rounds" / "a-round"

    return open_on


def declare(round_dir, stage_id, *, repo=None, after=()):
    """Record a stage as a build in a repo of its own, through the log."""
    log.append(log.round_log(round_dir), "stage.declared",
               {"id": stage_id, "repo": repo or f"repo-{stage_id.lower()}",
                "branch": f"branch-{stage_id.lower()}", "after": list(after)},
               by="planner")


def planned(round_dir, *ids):
    for stage_id in ids:
        declare(round_dir, stage_id)


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


def design(round_dir, *entries) -> None:
    text = ("# A design\n\nWhat the round builds.\n\n```toml rounds\n"
            + "\n\n".join(entries) + "\n```\n")
    (round_dir / "design.md").write_text(text, encoding="utf-8")


def fold():
    return freezing.freeze(ROUND, by="scout")


def rule(item, **more):
    return ruling.rule(ROUND, item=item, record="comms:1234567", by="planner", **more)


def events(round_dir):
    return log.read(log.round_log(round_dir))


def folded(round_dir):
    return state.fold(events(round_dir))


def last(round_dir):
    event = events(round_dir)[-1]
    return {key: event[key] for key in event if key not in ("v", "n", "at")}


def raw(round_dir) -> bytes:
    return log.round_log(round_dir).read_bytes()


def line_with(round_dir, needle: str) -> int:
    lines = (round_dir / "design.md").read_text(encoding="utf-8").splitlines()
    return next(n for n, line in enumerate(lines, 1) if needle in line)


def build(round_dir, stage_id, *more):
    """Record that a build of the stage was commissioned, and each event of `more`
    after it, as the verbs that make them will."""
    path = log.round_log(round_dir)
    log.append(path, "build.commissioned",
               {"stage": stage_id, "repo": f"repo-{stage_id.lower()}",
                "branch": f"branch-{stage_id.lower()}"}, by="coder")
    for name in more:
        fields = ({"stage": stage_id, "tip": "0" * 40} if name == "finish.done" else
                  {"stage": stage_id, "by": "lead"})
        log.append(path, name, fields, by="coder")


def applied(round_dir, *taken) -> None:
    """Append a verdict that found the design publishable and took `taken` out of
    the record, as recording such a verdict does."""
    log.append(log.round_log(round_dir), "verdict.recorded",
               {"kind": "design", "verdict": "publishable", "design": 1,
                "applied": list(taken)}, by="critic")


def append(round_dir, entry) -> None:
    """Append an event a later verb writes, as that verb will."""
    log.append(log.round_log(round_dir), entry.type, dict(entry.fields), by="critic")


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
    round_dir = opened(flow)
    design(round_dir, stage("B1"))
    fold()
    design(round_dir, again)
    before = raw(round_dir)

    with pytest.raises(BadBlock) as refused:
        fold()

    [problem] = refused.value.problems
    assert said in problem.what and "different work is a new id" in problem.what
    assert problem.line == line_with(round_dir, where)
    assert raw(round_dir) == before
    assert not (round_dir / "versions" / "design-v2.md").exists()


@pytest.mark.parametrize("flow", DESIGNED)
def test_a_stage_that_stands_for_a_round_keeps_that_round(opened, flow):
    round_dir = opened(flow)
    design(round_dir, stage("B1", round_=CHILD))
    fold()
    before = raw(round_dir)

    design(round_dir, stage("B1", round_="proj-a/another"))
    with pytest.raises(BadBlock, match="a stage's round never changes"):
        fold()
    design(round_dir, stage("B1"))
    with pytest.raises(BadBlock, match=f"B1 is recorded as the round {CHILD}, and "
                                       "the block declares it as repo-b1 on"):
        fold()

    assert raw(round_dir) == before


@pytest.mark.parametrize("flow", DESIGNED)
def test_a_design_that_declares_a_recorded_stage_as_it_is_freezes(opened, flow):
    round_dir = opened(flow)
    design(round_dir, stage("B1"))
    fold()

    assert fold().version == 2
    assert [one.id for one in folded(round_dir).stages] == ["B1"]


def test_a_stage_declared_as_it_was_commissioned_keeps_its_place_across_a_step_up(
        opened):
    round_dir = opened("fix")
    declare(round_dir, "B1")
    assert main(["mark", ROUND, "--flow", "small", "--as", "planner"]) == 0
    design(round_dir, stage("B1", branch="branch-other"))
    before = raw(round_dir)

    with pytest.raises(BadBlock, match="a stage's branch never changes"):
        fold()
    assert raw(round_dir) == before

    design(round_dir, stage("B1"))
    assert fold().plan.added == {}
    [kept] = folded(round_dir).stages
    assert (kept.id, kept.repo, kept.branch, kept.version) == (
        "B1", "repo-b1", "branch-b1", None)


def test_a_step_up_while_a_build_is_open_keeps_the_build_and_applies_the_new_flow(
        git_repo, tmp_path, capsys):
    from moxi import cli
    from moxi_rounds import builds, checking, commissioning, next_step
    from moxi_rounds import settings as own
    from moxi_rounds import steps

    assert cli.main(["projects", "add", "proj-a", "--root", str(git_repo.root)]) == 0
    assert cli.main(["projects", "set", "proj-a", "--notes-root",
                     str(tmp_path / "notes")]) == 0
    opening.open_round(ROUND, description="a placeholder round", by="planner",
                       flow="fix")
    round_dir = tmp_path / "notes" / "rounds" / "a-round"
    log.append(log.round_log(round_dir), "stage.declared",
               {"id": "B1", "repo": "proj-a", "branch": "work", "after": []},
               by="planner")
    made = commissioning.commission(ROUND, "B1", builder="coder", by="planner")
    type(git_repo)(made.worktree).commit("work.txt", "the work")

    assert main(["mark", ROUND, "--flow", "small", "--as", "planner"]) == 0
    capsys.readouterr()

    state_ = folded(round_dir)
    seen = checking.seen_of(state_, ROUND, settings=own.load())
    by_step = {(one.step, one.stage): one
               for one in steps.of(state_, name=ROUND, seen=seen)}
    assert state_.flow == "small"
    # The build stays the one commissioned: its commission is not asked for again.
    [build] = builds.builds(state_)
    assert (build.stage, build.branch, build.ended) == ("B1", "work", None)
    assert by_step[("commission", "B1")].state == steps.DONE
    assert by_step[("build", "B1")].state == steps.DONE
    assert by_step[("verify-build", "B1")].state == steps.READY
    assert not by_step[("finish", "B1")].settled
    assert ("check", "B1") not in by_step
    # The new flow's rows apply: its design step and its checker's tier.
    assert by_step[("design", None)].state == steps.READY
    groups = next_step.read(ROUND)["groups"]
    assert [(one["step"], one["stage"], one["tier"]) for one in groups] == [
        ("design", None, "second"), ("verify-build", "B1", "second")]


@pytest.mark.parametrize("flow", FLOWS)
def test_a_change_of_flow_keeps_every_stage_and_what_it_waits_for(opened, flow):
    round_dir = opened(flow)
    planned(round_dir, "B1", "B2")
    assert main(["mark", ROUND, "B2", "--after", "B1"]) == 0
    before = folded(round_dir).stages

    for move in ("full", "fix", "small"):
        if move != folded(round_dir).flow:
            rule(f"flow:{move}")

    assert folded(round_dir).stages == before
    assert stages.state_of(folded(round_dir), "B1") == stages.PLANNED


@pytest.mark.parametrize("flow", DESIGNED)
def test_a_design_that_leaves_a_recorded_stage_out_takes_nothing_away(opened, flow):
    round_dir = opened(flow)
    planned(round_dir, "B1", "B2")
    design(round_dir, stage("B2"))

    frozen = fold()

    assert [(one.what, one.id) for one in frozen.plan.pending] == [("stage", "B1")]
    assert [one.id for one in stages.in_plan(folded(round_dir))] == ["B1", "B2"]


# -- one stage to a repo where the design is not verified --------------------------------

def test_a_design_with_two_stages_in_one_repo_is_refused_where_it_is_not_verified(
        opened):
    round_dir = opened("small")
    design(round_dir, stage("B1", repo="proj-a"), stage("B2", repo="proj-a"))
    before = raw(round_dir)

    with pytest.raises(BadBlock) as refused:
        fold()

    [problem] = refused.value.problems
    assert ("B2 would be a second stage in proj-a, which has B1: the flow small does "
            "not verify its design") in problem.what
    assert "moxi rounds mark ROUND --flow NAME" in problem.fix
    assert "verify-design" in problem.fix
    assert problem.line == line_with(round_dir, 'branch = "branch-b2"') - 1
    assert raw(round_dir) == before and not (round_dir / "versions").exists()


def test_a_design_that_adds_a_second_stage_to_a_recorded_ones_repo_is_refused(opened):
    round_dir = opened("small")
    design(round_dir, stage("B1", repo="proj-a"))
    fold()
    design(round_dir, stage("B1", repo="proj-a"), stage("B2", repo="proj-a"))
    before = raw(round_dir)

    with pytest.raises(BadBlock, match="B2 would be a second stage in proj-a, which "
                                       "has B1"):
        fold()

    assert raw(round_dir) == before


def test_a_design_with_two_stages_in_one_repo_freezes_where_it_is_verified(opened):
    round_dir = opened("full")
    design(round_dir, stage("B1", repo="proj-a"), stage("B2", repo="proj-a"))

    assert fold().plan.added == {"stages": ["B1", "B2"]}


def test_the_step_up_a_refusal_names_lets_the_design_freeze(opened):
    round_dir = opened("small")
    design(round_dir, stage("B1", repo="proj-a"), stage("B2", repo="proj-a"))
    with pytest.raises(BadBlock):
        fold()

    assert main(["mark", ROUND, "--flow", "full", "--as", "planner"]) == 0

    assert fold().plan.added == {"stages": ["B1", "B2"]}


def test_a_stage_to_each_repo_and_a_round_in_place_of_one_freeze_where_not_verified(
        opened):
    round_dir = opened("small")
    design(round_dir, stage("B1", repo="proj-a"), stage("B2", repo="proj-b"),
           stage("B3", round_=CHILD), stage("B4", round_="proj-a/another"))

    assert fold().plan.added == {"stages": ["B1", "B2", "B3", "B4"]}


def test_a_repo_whose_stage_left_the_plan_takes_another(opened):
    round_dir = opened("small")
    design(round_dir, stage("B1", repo="proj-a"))
    fold()
    rule("unstage:B1")
    design(round_dir, stage("B2", repo="proj-a"))

    assert fold().plan.added == {"stages": ["B2"]}


def test_stages_that_shared_a_repo_before_a_step_down_are_declared_again(opened):
    round_dir = opened("full")
    design(round_dir, stage("B1", repo="proj-a"), stage("B2", repo="proj-a"))
    fold()
    rule("flow:small")

    assert fold().version == 2

    design(round_dir, stage("B1", repo="proj-a"), stage("B2", repo="proj-a"),
           stage("B3", repo="proj-a"))
    with pytest.raises(BadBlock, match="B3 would be a second stage in proj-a"):
        fold()


# -- leaving the plan --------------------------------------------------------------------

@pytest.mark.parametrize("flow", FLOWS)
def test_his_ruling_takes_a_stage_with_no_build_out_of_the_plan(opened, flow):
    round_dir = opened(flow)
    planned(round_dir, "B1", "B2")
    count = len(events(round_dir))

    ruled = rule("unstage:B1")

    assert len(events(round_dir)) == count + 1
    assert last(round_dir) == {"actor": "planner", "type": "ruling.recorded",
                            "id": ruled.id, "record": "comms:1234567",
                            "kind": "process", "rules": "unstage:B1"}
    found = folded(round_dir)
    assert stages.state_of(found, "B1") == stages.LEFT == "left"
    assert stages.state_of(found, "B2") == stages.PLANNED == "planned"
    assert stages.state_of(found, "B9") is None
    assert [one.id for one in stages.in_plan(found)] == ["B2"]
    assert questions.left_stages(found) == {"B1"}
    assert [one.id for one in found.stages] == ["B1", "B2"]


def test_a_stage_that_left_holds_nothing_and_is_shown_as_left(opened, capsys):
    round_dir = opened("full")
    planned(round_dir, "B1", "B2")
    asking.ask(ROUND, "Is it so?", origin="comms:1234568", blocks="B1,B2",
               by="scout")
    assert [one.id for one in holds.on_stage(folded(round_dir), "B1")] == ["Q1"]

    assert main(["rule", ROUND, "unstage:B1", "--record", "comms:1234567"]) == 0

    out = capsys.readouterr().out
    assert "Recorded as C1: unstage:B1" in out
    assert "B1 left the plan of proj-a/a-round and does not come back" in out
    assert holds.on_stage(folded(round_dir), "B1") == ()
    assert [one.id for one in holds.on_stage(folded(round_dir), "B2")] == ["Q1"]
    assert main(["show", ROUND]) == 0
    assert "stage B1: repo-b1 on branch-b1; left the plan" in capsys.readouterr().out
    found = index.read("proj-a", round_dir.parent, "a-round")
    assert [item.id for item in found.of(index.STAGE)] == ["B2"]


@pytest.mark.parametrize("flow", FLOWS)
@pytest.mark.parametrize(("built", "said"), [
    (("build.commissioned",), "B1 has an open build, and a stage with one leaves only "
                              "after that build is dropped"),
    (("build.commissioned", "finish.done"),
     "B1 is finished: its work is met, and a finished stage has nothing to leave")])
def test_a_stage_with_an_open_or_finished_build_does_not_leave(opened, flow, built,
                                                               said):
    round_dir = opened(flow)
    planned(round_dir, "B1")
    build(round_dir, "B1", *built[1:])
    before = raw(round_dir)

    with pytest.raises(Refused, match=said):
        rule("unstage:B1")

    assert raw(round_dir) == before
    assert stages.state_of(folded(round_dir), "B1") == stages.PLANNED


@pytest.mark.parametrize("flow", FLOWS)
def test_a_stage_whose_build_was_dropped_leaves(opened, flow):
    round_dir = opened(flow)
    planned(round_dir, "B1")
    build(round_dir, "B1", "build.dropped")
    assert stages.build_of(folded(round_dir), "B1") is None

    rule("unstage:B1")

    assert stages.state_of(folded(round_dir), "B1") == stages.LEFT


def test_what_a_stages_build_is_follows_the_log(opened):
    round_dir = opened("fix")
    planned(round_dir, "B1", "B2")
    assert stages.build_of(folded(round_dir), "B1") is None
    build(round_dir, "B1")
    assert stages.build_of(folded(round_dir), "B1") == stages.BUILDING == "open"
    build(round_dir, "B1", "build.dropped")
    assert stages.build_of(folded(round_dir), "B1") is None
    build(round_dir, "B1", "finish.done")
    assert stages.build_of(folded(round_dir), "B1") == stages.FINISHED == "finished"
    assert stages.build_of(folded(round_dir), "B2") is None


@pytest.mark.parametrize(("item", "said"), [
    ("unstage:B9", "B9 is not a stage of proj-a/a-round: its stages are B1, B2"),
    ("unstage:B2", "B2 left the plan already, and a stage leaves once")])
def test_a_stage_the_plan_does_not_hold_is_not_taken_out(opened, item, said):
    round_dir = opened("small")
    planned(round_dir, "B1", "B2")
    rule("unstage:B2")
    before = raw(round_dir)

    with pytest.raises(Refused, match=said):
        rule(item)

    assert raw(round_dir) == before


def test_a_ruling_that_takes_a_stage_out_needs_his_pointer(opened, capsys):
    round_dir = opened("fix")
    planned(round_dir, "B1")
    before = raw(round_dir)

    assert main(["rule", ROUND, "unstage:B1"]) == 3

    assert ("run: moxi rounds rule proj-a/a-round unstage:B1 --record PTR"
            in capsys.readouterr().err)
    assert raw(round_dir) == before


@pytest.mark.parametrize("item", ["unstage:", "unstage:Q1", "unstage:B1,B2",
                                  "unafter:B1", "unafter:B1=", "unafter:=B1",
                                  "unafter:B1=B2,B3", "unafter:B1=all"])
def test_an_item_that_names_no_stage_is_not_a_ruling(opened, item):
    round_dir = opened("fix")
    planned(round_dir, "B1", "B2", "B3")
    before = raw(round_dir)

    with pytest.raises(BadArgument):
        rule(item)

    assert raw(round_dir) == before


@pytest.mark.parametrize("item", ["unstage:B1", "unafter:B2=B1"])
def test_a_ruling_of_a_stage_is_of_a_round_and_replaces_none(opened, item):
    round_dir = opened("fix")
    planned(round_dir, "B1")
    declare(round_dir, "B2", after=["B1"])
    before = raw(round_dir)

    with pytest.raises(BadArgument, match="is of a round, and proj-a is a project"):
        ruling.rule("proj-a", item=item, record="comms:1234567", by="planner")
    with pytest.raises(BadArgument, match="leave --supersedes out"):
        rule(item, supersedes="C1")
    with pytest.raises(BadArgument, match="leave --stages out"):
        rule(item, stages="B1")

    assert raw(round_dir) == before


def stopped_after_its_append(monkeypatch, act):
    def stop(written):
        raise KeyboardInterrupt

    with monkeypatch.context() as patched:
        patched.setattr(write, "ON_APPEND", (stop,))
        with pytest.raises(KeyboardInterrupt):
            act()


def test_a_ruling_that_takes_a_stage_out_stopped_after_its_append_is_whole(
        opened, monkeypatch):
    round_dir = opened("small")
    planned(round_dir, "B1")
    count = len(events(round_dir))

    stopped_after_its_append(monkeypatch, lambda: rule("unstage:B1"))

    with pytest.raises(Refused, match="B1 left the plan already"):
        rule("unstage:B1")
    assert len(events(round_dir)) == count + 1
    assert stages.state_of(folded(round_dir), "B1") == stages.LEFT


@pytest.mark.parametrize("flow", FLOWS)
def test_a_stage_taken_out_is_not_put_back_by_taking_the_ruling_back(opened, flow):
    round_dir = opened(flow)
    planned(round_dir, "B1")
    rule("unstage:B1")
    before = raw(round_dir)

    with pytest.raises(Refused) as refused:
        rule("revoke:C1")

    [unmet] = refused.value.unmet
    assert ("C1 took B1 out of the plan of proj-a/a-round, and a stage that left does "
            "not come back") in unmet.reason
    assert "declared again under a new id" in unmet.reason
    assert unmet.fix == ("moxi rounds show proj-a/a-round" if flow == "fix" else
                         "moxi rounds fold proj-a/a-round")
    assert raw(round_dir) == before


@pytest.mark.parametrize("flow", DESIGNED)
@pytest.mark.parametrize("how", ["ruling", "verdict"])
def test_a_design_that_declares_a_stage_that_left_is_refused(opened, flow, how):
    round_dir = opened(flow)
    design(round_dir, stage("B1"))
    fold()
    if how == "ruling":
        rule("unstage:B1")
    else:
        applied(round_dir, {"what": "stage", "id": "B1"})
    design(round_dir, stage("B1"), stage("B2"))
    before = raw(round_dir)

    with pytest.raises(BadBlock) as refused:
        fold()

    [problem] = refused.value.problems
    assert ("B1 left the plan of the round, and a stage that left does not come "
            "back") in problem.what
    assert "an id the round has not used" in problem.fix
    assert problem.line == line_with(round_dir, 'id = "B1"')
    assert raw(round_dir) == before
    assert not (round_dir / "versions" / "design-v2.md").exists()


def test_a_stage_that_left_is_refused_with_whatever_place_the_design_gives_it(opened):
    round_dir = opened("full")
    design(round_dir, stage("B1"))
    fold()
    rule("unstage:B1")
    design(round_dir, stage("B1", repo="repo-other"))

    with pytest.raises(BadBlock) as refused:
        fold()

    assert len(refused.value.problems) == 1


@pytest.mark.parametrize("flow", DESIGNED)
def test_the_work_of_a_stage_that_left_is_planned_again_under_a_new_id(opened, flow):
    round_dir = opened(flow)
    design(round_dir, stage("B1"))
    fold()
    rule("unstage:B1")
    design(round_dir, stage("B2", repo="repo-b1", branch="branch-b1"))

    frozen = fold()

    assert frozen.plan.added == {"stages": ["B2"]}
    assert frozen.plan.pending == ()
    assert [one.id for one in stages.in_plan(folded(round_dir))] == ["B2"]


def test_a_removal_a_verdict_applies_takes_the_stage_out(opened):
    round_dir = opened("full")
    planned(round_dir, "B1", "B2")
    assert stages.cannot_leave(folded(round_dir), "B1") is None

    applied(round_dir, {"what": "stage", "id": "B1"})

    found = folded(round_dir)
    assert stages.state_of(found, "B1") == stages.LEFT
    assert stages.cannot_leave(found, "B1") == ("B1 left the plan already, and a "
                                                "stage leaves once")
    with pytest.raises(Refused, match="B1 left the plan already"):
        rule("unstage:B1")


def test_why_a_stage_stays_says_what_its_build_is(opened):
    round_dir = opened("full")
    planned(round_dir, "B1", "B2")
    build(round_dir, "B1")
    build(round_dir, "B2", "finish.done")
    found = folded(round_dir)

    assert "B1 has an open build" in stages.cannot_leave(found, "B1")
    assert "B2 is finished" in stages.cannot_leave(found, "B2")
    assert stages.cannot_leave(found, "B9", name=ROUND) == (
        "B9 is not a stage of proj-a/a-round: its stages are B1, B2")


def test_a_narrowing_a_verdict_applies_takes_the_waits_off(opened):
    round_dir = opened("full")
    planned(round_dir, "B1", "B2")
    declare(round_dir, "B3", after=["B1", "B2"])

    applied(round_dir, {"what": "after", "id": "B3", "gone": ["B1"]})

    assert stages.find(folded(round_dir), "B3").after == ("B2",)


# -- what a stage waits for --------------------------------------------------------------

@pytest.mark.parametrize("flow", FLOWS)
def test_anyone_makes_a_stage_wait_for_more(opened, flow, capsys):
    round_dir = opened(flow)
    planned(round_dir, "B1", "B2")
    declare(round_dir, "B3", after=["B1"])
    count = len(events(round_dir))

    assert main(["mark", ROUND, "B3", "--after", "B1,B2", "--as", "scout"]) == 0

    assert capsys.readouterr().out == "B3   after: B1, B2   added: B2\n"
    assert len(events(round_dir)) == count + 1
    assert last(round_dir) == {"actor": "scout", "type": "stage.changed", "id": "B3",
                            "after": ["B2"]}
    assert stages.find(folded(round_dir), "B3").after == ("B1", "B2")


def test_a_wait_added_is_shown_and_is_in_the_index(opened, capsys):
    round_dir = opened("full")
    planned(round_dir, "B1", "B2")

    marked = stages.wait(ROUND, "B2", "B1", by="scout")

    assert (marked.name, marked.stage.after, marked.added) == (ROUND, ("B1",), ("B1",))
    assert main(["show", ROUND]) == 0
    out = capsys.readouterr().out
    assert "stage B2: repo-b2 on branch-b2, after B1" in out
    assert "stage.changed  B2, also after B1" in out
    found = index.read("proj-a", round_dir.parent, "a-round")
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
    round_dir = opened("small")
    planned(round_dir, "B1")
    declare(round_dir, "B2", after=["B1"])
    declare(round_dir, "B3", after=["B2"])
    before = raw(round_dir)

    with pytest.raises(Refused, match=said):
        stages.wait(ROUND, stage_id, after, by="scout")

    assert raw(round_dir) == before


def test_a_circle_is_refused_naming_the_ruling_that_takes_a_wait_off(opened, capsys):
    round_dir = opened("fix")
    planned(round_dir, "B1")
    declare(round_dir, "B2", after=["B1"])
    before = raw(round_dir)

    assert main(["mark", ROUND, "B1", "--after", "B2"]) == 3

    assert ("run: moxi rounds rule proj-a/a-round unafter:B<n>=B<m> --record PTR"
            in capsys.readouterr().err)
    assert raw(round_dir) == before


LEFT_FOR_GOOD = ("B2 left the plan, and a stage that left does not come back, so B1 "
                 "waiting for B2 could never start")
CIRCLE = ("B3 waits for B1 already, directly or through other stages, so B1 waiting "
          "for B3 would leave neither able to start; the operator takes a wait off")


@pytest.mark.parametrize("how", ["ruling", "verdict"])
def test_a_wait_for_a_stage_that_left_the_plan_is_refused(opened, how, capsys):
    round_dir = opened("full")
    planned(round_dir, "B1", "B2", "B3")
    if how == "ruling":
        rule("unstage:B2")
    else:
        design(round_dir, stage("B1"), stage("B2"), stage("B3"))
        fold()
        applied(round_dir, {"what": "stage", "id": "B2"})
    before = raw(round_dir)

    with pytest.raises(Refused) as refused:
        stages.wait(ROUND, "B1", "B3,B2", by="scout")

    assert LEFT_FOR_GOOD in str(refused.value)
    assert main(["mark", ROUND, "B1", "--after", "B2"]) == 3
    assert LEFT_FOR_GOOD in capsys.readouterr().err
    assert raw(round_dir) == before
    assert stages.wait(ROUND, "B1", "B3", by="scout").added == ("B3",)


def test_a_wait_for_a_finished_stage_is_still_added(opened):
    round_dir = opened("full")
    planned(round_dir, "B1", "B2")
    log.append(log.round_log(round_dir), "finish.done", {"stage": "B2"}, by="planner")

    assert stages.wait(ROUND, "B1", "B2", by="scout").added == ("B2",)


def test_a_fold_refuses_a_circle_of_waits_in_the_words_of_the_wait_added_by_hand(
        opened):
    round_dir = opened("full")
    design(round_dir, stage("B1"), stage("B2", after='["B1"]'),
           stage("B3", after='["B2"]'))
    fold()
    with pytest.raises(Refused) as by_hand:
        stages.wait(ROUND, "B1", "B3", by="scout")
    design(round_dir, stage("B1", after='["B3"]'), stage("B2", after='["B1"]'),
           stage("B3", after='["B2"]'))
    before = raw(round_dir)

    with pytest.raises(BadBlock) as refused:
        fold()

    [problem] = refused.value.problems
    assert problem.what == CIRCLE and CIRCLE in str(by_hand.value)
    assert "take B3 out of the after of B1" in problem.fix
    assert "unafter:B<n>=B<m> --record PTR" in problem.fix
    assert problem.line == line_with(round_dir, 'after = ["B3"]')
    assert raw(round_dir) == before
    assert not (round_dir / "versions" / "design-v2.md").exists()


LEFT_OUT = ("this block leaves B2 out, which takes it out of the plan once the version "
            "is found publishable, so B1 waiting for B2 could never start")


def test_a_fold_refuses_a_wait_for_a_stage_its_own_block_leaves_out(opened):
    round_dir = opened("full")
    design(round_dir, stage("B1"), stage("B2"), stage("B3"))
    fold()
    design(round_dir, stage("B1", after='["B3", "B2"]'), stage("B3"))
    before = raw(round_dir)

    with pytest.raises(BadBlock) as refused:
        fold()

    [problem] = refused.value.problems
    assert problem.what == LEFT_OUT
    assert problem.fix == ("take B2 out of the after of B1, or declare B2 in the block "
                           "again")
    assert problem.line == line_with(round_dir, 'after = ["B3", "B2"]')
    assert raw(round_dir) == before
    assert not (round_dir / "versions" / "design-v2.md").exists()


@pytest.mark.parametrize("fixed", ["declared again", "wait taken out"])
def test_either_fix_named_for_a_wait_on_a_stage_left_out_lets_the_fold_freeze(
        opened, fixed):
    round_dir = opened("full")
    design(round_dir, stage("B1"), stage("B2"))
    fold()
    if fixed == "declared again":
        design(round_dir, stage("B1", after='["B2"]'), stage("B2"))
    else:
        design(round_dir, stage("B1"))

    fold()

    assert stages.waits(folded(round_dir)).get("B1", ()) == (
        ("B2",) if fixed == "declared again" else ())


def test_a_fold_adds_a_wait_for_a_finished_stage_its_block_leaves_out(opened):
    round_dir = opened("full")
    design(round_dir, stage("B1"), stage("B2"))
    fold()
    log.append(log.round_log(round_dir), "finish.done", {"stage": "B2"}, by="planner")
    design(round_dir, stage("B1", after='["B2"]'))

    fold()

    assert tuple(stages.waits(folded(round_dir))["B1"]) == ("B2",)


def test_a_wait_recorded_before_its_stage_was_left_out_is_not_refused(opened):
    round_dir = opened("full")
    design(round_dir, stage("B1", after='["B2"]'), stage("B2"))
    fold()
    design(round_dir, stage("B1", after='["B2"]'))

    fold()

    assert tuple(stages.waits(folded(round_dir))["B1"]) == ("B2",)


def test_a_first_fold_refuses_a_circle_among_the_stages_it_declares(opened):
    round_dir = opened("full")
    design(round_dir, stage("B1", after='["B3"]'), stage("B2", after='["B1"]'),
           stage("B3", after='["B2"]'), stage("B4", after='["B1", "B2"]'))
    before = raw(round_dir)

    with pytest.raises(BadBlock) as refused:
        fold()

    [problem] = refused.value.problems
    assert problem.what == (
        "B2 waits for B3 already, directly or through other stages, so B3 waiting "
        "for B2 would leave neither able to start; the operator takes a wait off")
    assert problem.line == line_with(round_dir, 'after = ["B2"]')
    assert raw(round_dir) == before


def test_a_fold_counts_a_wait_the_block_left_out_until_it_is_taken_off(opened):
    round_dir = opened("full")
    design(round_dir, stage("B1"), stage("B2", after='["B1"]'))
    fold()
    design(round_dir, stage("B1", after='["B2"]'), stage("B2"))

    with pytest.raises(BadBlock) as refused:
        fold()

    assert "B2 waits for B1 already" in refused.value.problems[0].what
    rule("unafter:B2=B1")
    assert fold().plan.added == {"after": {"B1": ["B2"]}}
    assert stages.find(folded(round_dir), "B1").after == ("B2",)


def test_a_fold_refuses_a_wait_it_adds_for_a_stage_that_left_the_plan(opened):
    round_dir = opened("full")
    design(round_dir, stage("B1"), stage("B2"), stage("B3", after='["B2"]'))
    fold()
    rule("unstage:B2")
    design(round_dir, stage("B1", after='["B2"]'), stage("B3", after='["B2"]'),
           stage("B4", after='["B2"]'))
    before = raw(round_dir)

    with pytest.raises(BadBlock) as refused:
        fold()

    assert [(one.what, one.line) for one in refused.value.problems] == [
        (LEFT_FOR_GOOD, line_with(round_dir, 'id = "B1"') + 3),
        (LEFT_FOR_GOOD.replace("B1", "B4"), line_with(round_dir, 'id = "B4"') + 3)]
    assert refused.value.problems[0].fix == "take B2 out of the after of B1"
    assert raw(round_dir) == before
    # The wait B3 had before the stage left is none this freezing adds.
    design(round_dir, stage("B1"), stage("B3", after='["B2"]'))
    assert fold().plan.added == {}


@pytest.mark.parametrize("argv", [
    ("B1", "--after", ""), ("B1", "--after", "all"), ("B1", "--after", "B2,B2"),
    ("B1", "--after", "B2,Q1"), ("Q1", "--after", "B2"), ("--after", "B2"),
    ("B1", "--after", "B2", "--record", "comms:1234567"),
    ("B1", "--after", "B2", "--reopen")])
def test_a_wait_that_cannot_be_read_is_a_wrong_use(opened, argv, capsys):
    round_dir = opened("fix")
    planned(round_dir, "B1", "B2")
    before = raw(round_dir)

    assert main(["mark", ROUND, *argv]) == 2

    assert raw(round_dir) == before


def test_a_round_that_ended_takes_no_wait_and_loses_no_stage(opened):
    round_dir = opened("fix")
    planned(round_dir, "B1", "B2")
    dropping.drop(ROUND, by="lead", record="comms:1234569", run_by="planner")
    before = raw(round_dir)

    with pytest.raises(Refused, match="dropped"):
        stages.wait(ROUND, "B2", "B1", by="scout")
    with pytest.raises(Refused, match="dropped"):
        rule("unstage:B1")

    assert raw(round_dir) == before


def test_a_wait_stopped_after_its_append_is_whole(opened, monkeypatch):
    round_dir = opened("fix")
    planned(round_dir, "B1", "B2")
    count = len(events(round_dir))

    stopped_after_its_append(monkeypatch,
                             lambda: stages.wait(ROUND, "B2", "B1", by="scout"))

    with pytest.raises(Refused, match="nothing to add"):
        stages.wait(ROUND, "B2", "B1", by="scout")
    assert len(events(round_dir)) == count + 1


@pytest.mark.parametrize("flow", FLOWS)
def test_his_ruling_takes_one_wait_off_a_stage(opened, flow, capsys):
    round_dir = opened(flow)
    planned(round_dir, "B1", "B2")
    declare(round_dir, "B3", after=["B1", "B2"])
    count = len(events(round_dir))

    assert main(["rule", ROUND, "unafter:B3=B1", "--record", "comms:1234567",
                 "--as", "planner"]) == 0

    out = capsys.readouterr().out
    assert "Recorded as C1: unafter:B3=B1" in out
    assert ("B3 waits for B1 no longer; anyone adds the wait again: moxi rounds mark "
            "proj-a/a-round B3 --after B1") in out
    assert len(events(round_dir)) == count + 1
    assert last(round_dir) == {"actor": "planner", "type": "ruling.recorded", "id": "C1",
                            "record": "comms:1234567", "kind": "process",
                            "rules": "unafter:B3=B1"}
    assert stages.find(folded(round_dir), "B3").after == ("B2",)


@pytest.mark.parametrize(("item", "said"), [
    ("unafter:B9=B1", "B9 is not a stage of proj-a/a-round: its stages are B1, B2"),
    ("unafter:B2=B9", "B2 does not wait for B9: it waits for B1"),
    ("unafter:B1=B2", "B1 does not wait for B2: it waits for no stage")])
def test_a_wait_a_stage_does_not_have_is_not_taken_off(opened, item, said):
    round_dir = opened("small")
    planned(round_dir, "B1")
    declare(round_dir, "B2", after=["B1"])
    before = raw(round_dir)

    with pytest.raises(Refused, match=said):
        rule(item)

    assert raw(round_dir) == before


def test_a_wait_taken_off_is_added_again_by_a_mark_and_never_by_taking_back(opened):
    round_dir = opened("full")
    planned(round_dir, "B1")
    declare(round_dir, "B2", after=["B1"])
    rule("unafter:B2=B1")
    before = raw(round_dir)

    with pytest.raises(Refused) as refused:
        rule("revoke:C1")

    [unmet] = refused.value.unmet
    assert "C1 took B1 off what B2 waits for" in unmet.reason
    assert unmet.fix == "moxi rounds mark proj-a/a-round B2 --after B1"
    assert raw(round_dir) == before

    stages.wait(ROUND, "B2", "B1", by="scout")
    assert stages.find(folded(round_dir), "B2").after == ("B1",)
    rule("unafter:B2=B1")
    assert stages.find(folded(round_dir), "B2").after == ()


def test_a_wait_taken_off_stopped_after_its_append_is_whole(opened, monkeypatch):
    round_dir = opened("fix")
    planned(round_dir, "B1")
    declare(round_dir, "B2", after=["B1"])
    count = len(events(round_dir))

    stopped_after_its_append(monkeypatch, lambda: rule("unafter:B2=B1"))

    with pytest.raises(Refused, match="B2 does not wait for B1"):
        rule("unafter:B2=B1")
    assert len(events(round_dir)) == count + 1


def test_a_wait_taken_off_lets_a_stage_wait_the_other_way(opened):
    round_dir = opened("fix")
    planned(round_dir, "B1")
    declare(round_dir, "B2", after=["B1"])

    rule("unafter:B2=B1")
    stages.wait(ROUND, "B1", "B2", by="scout")

    assert stages.find(folded(round_dir), "B1").after == ("B2",)


@pytest.mark.parametrize("flow", FLOWS)
def test_a_stage_waits_for_the_ones_before_it_that_are_not_finished(opened, flow):
    round_dir = opened(flow)
    planned(round_dir, "B1", "B2")
    declare(round_dir, "B3", after=["B1", "B2"])
    assert stages.waits_for(folded(round_dir), "B3") == ("B1", "B2")
    assert stages.waits_for(folded(round_dir), "B1") == ()
    assert stages.waits_for(folded(round_dir), "B9") == ()

    build(round_dir, "B1")
    assert stages.waits_for(folded(round_dir), "B3") == ("B1", "B2")
    build(round_dir, "B1", "finish.done")
    assert stages.waits_for(folded(round_dir), "B3") == ("B2",)
    build(round_dir, "B2", "finish.done")
    assert stages.waits_for(folded(round_dir), "B3") == ()


def test_reading_a_stage_changes_nothing(opened):
    round_dir = opened("full")
    planned(round_dir, "B1")
    declare(round_dir, "B2", after=["B1"])
    found = folded(round_dir)
    before = raw(round_dir)

    twice = [(stages.waits_for(found, "B2"), stages.state_of(found, "B1"),
              stages.build_of(found, "B1"), stages.cannot_leave(found, "B1"))
             for _ in range(2)]

    assert twice[0] == twice[1] == (("B1",), "planned", None, None)
    assert raw(round_dir) == before and folded(round_dir) == found


# -- the two rulings among the kinds -----------------------------------------------------

@pytest.mark.parametrize(("rules", "name"), [
    ("unstage:B1", "a stage taken out of the plan"),
    ("unafter:B2=B1", "a wait taken off a stage")])
def test_neither_ruling_of_a_stage_is_taken_back_or_ruled_again(rules, name):
    kind = corrections.kind_of(rules)

    assert (kind.name, kind.undo, kind.again) == (name, None, False)
    assert corrections.item_of(rules) == rules


def test_the_history_says_what_changed_of_a_stage():
    assert showing.history_of({"type": "stage.changed", "id": "B2",
                               "after": ["B1"]}) == "B2, also after B1"
    # A mark adds waits and nothing else, so nothing else is read from its event.
    assert showing.history_of({"type": "stage.changed", "id": "B1", "removed": True,
                               "gone": ["B2"]}) == "B1"

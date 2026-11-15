"""Dropping one build: who may drop it is read from git now (the lead a build with no
work a drop could lose, the operator any), the drop records the branch's tip and
deletes nothing, the stage stays owed; a round's drop ends its open builds the same
way, and the lead's is refused while one of them is not his to drop."""

from __future__ import annotations

import pytest

from moxi_rounds import (builds, dropping, listing, log, opening, places, showing, stages,
                         state)
from moxi_rounds.cli import main
from moxi_rounds.errors import Refused

ROUND = "proj-a/a-round"
LEADS = "moxi rounds drop proj-a/a-round B1 --by lead --record PTR"


@pytest.fixture
def a_round(git_repo, tmp_path, capsys):
    """`proj-a` rooted at the test's git repo, its round open in the `fix` flow with B1
    declared on the branch `work` and commissioned at `main`'s head through the log.
    Returns the round's log."""
    from moxi import cli

    assert cli.main(["projects", "add", "proj-a", "--root", str(git_repo.root)]) == 0
    assert cli.main(["projects", "set", "proj-a", "--notes-root",
                     str(tmp_path / "notes")]) == 0
    opening.open_round(ROUND, description="a placeholder round", by="planner",
                       flow="fix")
    path = log.round_log(tmp_path / "notes" / "rounds" / "a-round")
    log.append(path, "stage.declared", {"id": "B1", "repo": "proj-a", "branch": "work",
                                        "after": []}, by="planner")
    log.append(path, "build.commissioned", {
        "stage": "B1", "repo": "proj-a", "branch": "work", "base": git_repo.rev("main"),
        "target": "main", "builder": "coder"}, by="planner")
    capsys.readouterr()
    return path


def folded(path):
    return state.fold(log.read(path))


def lead_drops(**more):
    return dropping.drop_build(ROUND, "B1", by="lead", record="comms:1234567",
                               run_by="planner", **more)


def operator_drops():
    return dropping.drop_build(ROUND, "B1", by="operator", record="comms:7654321",
                               run_by="planner")


def refused_to_the_lead(path) -> str:
    before = log.read(path)
    with pytest.raises(Refused) as caught:
        lead_drops()
    assert log.read(path) == before
    return str(caught.value)


# -- who may drop which state ------------------------------------------------------------

def test_a_gone_build_is_the_leads_to_drop_and_records_no_tip(a_round):
    dropped = lead_drops()

    assert dropped.event["type"] == "build.dropped"
    assert {key: dropped.event[key] for key in ("stage", "by", "record", "tips")} == {
        "stage": "B1", "by": "lead", "record": "comms:1234567",
        "tips": [{"stage": "B1", "repo": "proj-a", "branch": "work", "tip": None}]}
    assert "questions" not in dropped.event


def test_an_empty_build_with_no_commit_of_its_own_is_the_leads(a_round, git_repo):
    git_repo.git("branch", "work", "main")

    dropped = lead_drops()

    assert dropped.event["tips"][0]["tip"] == git_repo.rev("main")
    assert git_repo.rev("work") == git_repo.rev("main")


def test_an_empty_build_with_commits_of_its_own_is_the_operators(a_round, git_repo):
    git_repo.switch("work", new_from="main")
    git_repo.commit("feature.txt")
    git_repo.git("revert", "--no-edit", "HEAD")
    git_repo.switch("main")

    said = refused_to_the_lead(a_round)

    assert "B1's build reads empty with commits of its own" in said
    assert "it is the operator's to drop and not the lead's" in said
    assert "moxi rounds drop proj-a/a-round B1 --by operator --record PTR" in said
    assert operator_drops().event["by"] == "operator"


def test_a_building_build_is_the_operators_and_its_branch_is_kept(a_round, git_repo):
    git_repo.switch("work", new_from="main")
    tip = git_repo.commit("feature.txt")
    git_repo.switch("main")

    assert "B1's build reads building" in refused_to_the_lead(a_round)
    dropped = operator_drops()

    assert dropped.event["tips"][0]["tip"] == tip
    assert git_repo.rev("work") == tip


def test_a_merged_build_is_the_operators(a_round, git_repo):
    git_repo.switch("work", new_from="main")
    git_repo.commit("feature.txt")
    git_repo.switch("main")
    git_repo.merge("work")

    assert "B1's build reads merged" in refused_to_the_lead(a_round)


def test_an_unclear_build_is_the_operators(a_round, git_repo):
    git_repo.switch("work", new_from="main")
    git_repo.commit("feature.txt")
    git_repo.switch("main")
    git_repo.git("merge", "-q", "--ff-only", "work")

    assert "B1's build reads unclear" in refused_to_the_lead(a_round)


def test_a_build_that_shares_is_never_the_leads():
    empty = builds.Status(builds.EMPTY, builds.OFF_TARGET, "nothing to merge")
    gone = builds.Status(builds.GONE, builds.NO_HEAD, "no branch")

    assert builds.drop_by(empty) == builds.drop_by(gone) == builds.LEAD
    assert builds.drop_by(empty, [("proj-a/b-round", "B2")]) == builds.OPERATOR
    assert builds.drop_by(builds.Status(builds.EMPTY, builds.OFF_TARGET, "",
                                        has_own_commits=True)) == builds.OPERATOR


# -- what a drop leaves --------------------------------------------------------------------

def test_a_dropped_build_ends_and_its_stage_stays_owed(a_round):
    lead_drops()

    after = folded(a_round)
    assert builds.current(after, "B1").ended == builds.DROPPED
    assert stages.build_of(after, "B1") is None
    assert stages.state_of(after, "B1") != stages.LEFT
    with pytest.raises(Refused) as caught:
        lead_drops()
    assert "B1 has no open build: its last was dropped" in str(caught.value)


def test_a_drop_keeps_dirs_outside_rounds_roots_and_names_the_check(a_round, git_repo,
                                                                      tmp_path):
    worktree, scratch = tmp_path / "wt", tmp_path / "scratch"
    worktree.mkdir()
    scratch.mkdir()
    log.append(a_round, "build.dropped", {"stage": "B1", "by": "lead",
                                          "record": "comms:1234567", "tips": []},
               by="planner")
    log.append(a_round, "build.commissioned", {
        "stage": "B1", "repo": "proj-a", "branch": "work", "base": git_repo.rev("main"),
        "target": "main", "builder": "coder", "worktree": str(worktree),
        "scratch": str(scratch), "host": places.host()}, by="planner")

    dropped = lead_drops()
    assert [(one.place, one.check) for one in dropped.kept] == [(worktree, "A2"),
                                                                 (scratch, "A2")]
    assert dropped.removed == ()
    assert worktree.is_dir() and scratch.is_dir()


@pytest.mark.parametrize(("stage", "said"), [
    ("B7", "B7 is not a stage of proj-a/a-round"),
    ("B1", "a drop carries a pointer to why it is dropped"),
])
def test_a_drop_with_no_build_or_no_pointer_is_refused(a_round, stage, said):
    with pytest.raises(Refused) as caught:
        dropping.drop_build(ROUND, stage, by="lead",
                            record=None if stage == "B1" else "comms:1234567",
                            run_by="planner")
    assert said in str(caught.value)


# -- a round's drop ------------------------------------------------------------------------

def test_a_rounds_drop_ends_its_open_builds_with_their_tips(a_round, git_repo):
    git_repo.git("branch", "work", "main")

    dropped = dropping.drop(ROUND, by="lead", record="comms:1234567", run_by="planner")

    assert dropped.event["tips"] == [{"stage": "B1", "repo": "proj-a", "branch": "work",
                                      "tip": git_repo.rev("main")}]
    assert builds.current(folded(a_round), "B1").ended == builds.DROPPED


def test_the_leads_round_drop_is_refused_while_a_build_is_not_his(a_round, git_repo):
    git_repo.switch("work", new_from="main")
    git_repo.commit("feature.txt")
    git_repo.switch("main")

    with pytest.raises(Refused) as caught:
        dropping.drop(ROUND, by="lead", record="comms:1234567", run_by="planner")

    assert "the open build of B1, which reads building" in str(caught.value)
    assert dropping.drop(ROUND, by="operator", record="comms:7654321",
                         run_by="planner").event["by"] == "operator"


# -- the command, show and list ------------------------------------------------------------

def test_the_command_drops_a_build_and_says_the_stage_stays_owed(a_round, capsys):
    assert main(["drop", ROUND, "B1", "--by", "lead", "--record", "comms:1234567"]) == 0

    said = capsys.readouterr().out
    assert said.startswith(f"dropped {ROUND} B1   by: lead   record: comms:1234567\n")
    assert "branch: work at no branch, kept in git; the stage stays owed" in said


def test_the_command_refuses_withdraw_questions_for_a_build(a_round, capsys):
    assert main(["drop", ROUND, "B1", "--by", "operator", "--record", "comms:1234567",
                 "--withdraw-questions"]) == 2
    assert log.read(a_round)[-1]["type"] == "build.commissioned"


def test_show_and_list_say_who_drops_a_build_looked_at(a_round, capsys):
    assert main(["show", ROUND]) == 0
    text = capsys.readouterr().out
    assert f"    the lead drops it: {LEADS}" in text
    [row] = showing.data(showing.show(ROUND))["builds"]
    assert (row["drop_by"], row["drop"], row["credit"]) == ("lead", LEADS, None)

    listed = listing.waiting_as_text(listing.waiting())
    assert "proj-a/a-round B1: proj-a on work; gone; the lead's to drop" in listed

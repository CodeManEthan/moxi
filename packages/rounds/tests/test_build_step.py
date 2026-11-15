"""The `build` step of a stage: not due before the stage is commissioned, ready until git
reads the build as having work, then done; read the same way by `next`, `show`, `list`
and the index."""

from __future__ import annotations

import json
import shlex
import sys
from pathlib import Path

import pytest

from moxi_rounds import (checking, commissioning, index, listing, log, next_step, opening,
                         showing, steps)
from moxi_rounds import settings as own
from moxi_rounds import state as states
from moxi_rounds.errors import Unmet

ROUND = "proj-a/a-round"


@pytest.fixture
def repo(git_repo, tmp_path):
    """The project `proj-a`, whose root is the test's git repo, keeping its rounds under
    a notes folder of its own."""
    from moxi import cli

    assert cli.main(["projects", "add", "proj-a", "--root", str(git_repo.root)]) == 0
    assert cli.main(["projects", "set", "proj-a", "--notes-root",
                     str(tmp_path / "notes")]) == 0
    return git_repo


@pytest.fixture
def planned(repo, tmp_path, write_config):
    """The round, in the `fix` flow, with B1 planned on the branch `work` in a repo that
    declares a check. Returns the round's log."""
    command = shlex.join([sys.executable, "-c", "print('the check passed')"])
    write_config(f"[projects.proj-a]\ncheck = {{ command = {json.dumps(command)} }}\n")
    opening.open_round(ROUND, description="a placeholder round", by="planner",
                       flow="fix")
    path = log.round_log(tmp_path / "notes" / "rounds" / "a-round")
    log.append(path, "stage.declared",
               {"id": "B1", "repo": "proj-a", "branch": "work", "after": []},
               by="planner")
    return path


def commission():
    return commissioning.commission(ROUND, "B1", builder="coder", by="planner")


def folded(path: Path) -> states.State:
    return states.fold(log.read_round(path))


def seen(path: Path) -> dict:
    return checking.seen_of(folded(path), ROUND, settings=own.load())


def build_step(path: Path, **given) -> steps.Step:
    return steps.one(folded(path), "build", "B1", name=ROUND,
                     seen=given.get("seen", seen(path)))


def at_next() -> tuple:
    group = next_step.read(ROUND)["groups"][0]
    return group["step"], group["stage"], group["state"]


def listed_step() -> dict:
    [found] = listing.rounds("proj-a").data()["rounds"]
    return found["step"]


# -- the row -----------------------------------------------------------------------------

def test_the_build_step_is_not_due_before_the_stage_is_commissioned(planned):
    found = build_step(planned)

    assert (found.state, found.waits) == (steps.NOT_DUE, ())


def test_the_build_step_moves_from_ready_to_done_as_work_is_committed(planned, repo):
    made = commission()
    assert build_step(planned).state == steps.READY

    type(repo)(made.worktree).commit("work.txt", "the work")

    assert build_step(planned).state == steps.DONE


def test_a_finished_build_s_step_is_done_without_a_read_of_git(planned):
    commission()
    log.append(planned, "finish.done", {"stage": "B1", "tip": "0" * 40}, by="coder")

    assert build_step(planned, seen=None).state == steps.DONE


def test_where_git_was_not_read_the_step_waits_on_that_read(planned):
    commission()

    found = build_step(planned, seen=None)

    assert (found.state, found.unread) == (steps.WAITING, True)
    assert found.waits[0].reason == ("whether B1 has work is read from git, which was "
                                     "not read here")


def test_a_build_git_can_t_read_waits_with_what_kept_it_from_being_read(planned):
    commission()
    problem = steps.Seen(problem=Unmet("B1 can't be read from git: gone",
                                                f"moxi rounds show {ROUND}"))

    found = build_step(planned, seen={"B1": problem})

    assert (found.state, found.waits, found.unread) == (steps.WAITING, (problem.problem,),
                                                        False)


@pytest.mark.parametrize("word", ["empty", "gone", "unclear"])
def test_a_build_that_reads_with_no_work_is_ready(planned, word):
    commission()

    found = build_step(planned, seen={"B1": steps.Seen(state=word, tip="0" * 40)})

    assert found.state == steps.READY


# -- what reads it -----------------------------------------------------------------------

def test_next_moves_from_commission_through_build_to_check(planned, repo, capsys):
    assert at_next() == ("commission", "B1", steps.READY)

    made = commission()
    assert at_next() == ("build", "B1", steps.READY)
    group = next_step.read(ROUND)["groups"][0]
    assert group["by"] == "builder"
    assert group["write"] == f"the work, committed on work in {made.worktree}"

    type(repo)(made.worktree).commit("work.txt", "the work")
    assert at_next() == ("check", "B1", steps.READY)

    checking.check(ROUND, "B1", by="coder")
    assert at_next()[0] == "verify-build"


def test_show_gives_the_step_as_git_reads_the_build(planned, repo):
    made = commission()
    assert showing.data(showing.show(ROUND))["step"]["step"] == "build"

    type(repo)(made.worktree).commit("work.txt", "the work")

    head = showing.data(showing.show(ROUND))["step"]
    assert (head["step"], head["state"]) == ("check", steps.READY)


def test_the_index_marks_a_step_git_says_and_list_reads_it_with_git(planned, repo):
    made = commission()
    summary = index.read("proj-a", planned.parent.parent, "a-round")
    [kept] = [item.body for item in summary.of(index.STEP)]
    assert (kept["step"], kept["state"], kept["git"]) == ("build", steps.WAITING, True)

    assert listed_step() == {"step": "build", "stage": "B1", "state": steps.READY}

    type(repo)(made.worktree).commit("work.txt", "the work")
    assert listed_step() == {"step": "check", "stage": "B1", "state": steps.READY}


def test_the_index_keeps_a_step_the_log_alone_says_unmarked(planned):
    summary = index.read("proj-a", planned.parent.parent, "a-round")
    [kept] = [item.body for item in summary.of(index.STEP)]

    assert kept == {"step": "commission", "stage": "B1", "state": steps.READY}

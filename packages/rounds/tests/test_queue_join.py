"""Joining a repo's queue: `finish` in a queue project enqueues at the tip after the
finish gates but the target's, the check met by the scoped local check with its own
merge-base or by the full check (`--full`); `--urgent` and `--leave`; a moved member
leaves; a direct finish is refused; and `next` says where the build stands."""

from __future__ import annotations

import json
import shlex
import sys
from pathlib import Path

import pytest

from moxi_rounds import (checking, commissioning, finishing, joining, log, next_step,
                         opening, queue_file, queue_log, verdicts)
from moxi_rounds import settings as own
from moxi_rounds import state as states
from moxi_rounds.cli import main
from moxi_rounds.errors import BadArgument, Refused

ROUND = "proj-a/a-round"
OTHER = "proj-a/b-round"
FULL = shlex.join([sys.executable, "-c", "print('the full check')"])
SCOPED = shlex.join([sys.executable, "-c", "import sys; print(sys.argv)", "--base",
                     "{base}"])


@pytest.fixture
def repo(git_repo, tmp_path):
    from moxi import cli

    assert cli.main(["projects", "add", "proj-a", "--root", str(git_repo.root)]) == 0
    assert cli.main(["projects", "set", "proj-a", "--notes-root",
                     str(tmp_path / "notes")]) == 0
    git_repo.git("config", "user.name", "coder")
    git_repo.git("config", "user.email", "coder@example.test")
    return git_repo


@pytest.fixture
def configure(write_config):
    def write(extra: str = "", *, land: str = "queue") -> None:
        write_config(f"[projects.proj-a]\nland = {json.dumps(land)}\n"
                     f"check = {{ command = {json.dumps(FULL)}, "
                     f"scoped = {json.dumps(SCOPED)} }}\n{extra}\n")

    write()
    return write


def verify(path: Path, stage: str, head: str, name: str = ROUND) -> None:
    report = path.parent / f"build-report-{stage}.md"
    report.write_text("# report\n\n```toml rounds\n" + "\n".join([
        'kind = "build"', 'verdict = "publishable"', 'pass = "full"', 'by = "critic"',
        'model = "a-model"', f'stage = "{stage}"', f'head = "{head}"']) + "\n```\n",
        encoding="utf-8")
    verdicts.record(name, report, by="critic")


@pytest.fixture
def round_log(repo, configure, tmp_path):
    opening.open_round(ROUND, description="a placeholder round", by="planner",
                       flow="fix")
    return log.round_log(tmp_path / "notes" / "rounds" / "a-round")


@pytest.fixture
def ready(repo, round_log, capsys, tmp_path):
    """`ready(name, branch, file)`: B1 of the round `name` (opened when it is not this
    file's `ROUND`) declared, commissioned, one commit of work in `file`, checked and
    verified at its tip. Returns the worktree's repo."""

    def make(name: str = ROUND, branch: str = "work", file: str = "work.txt"):
        path = round_log
        if name != ROUND:
            opening.open_round(name, description="a placeholder round", by="planner",
                               flow="fix")
            path = log.round_log(tmp_path / "notes" / "rounds" / name.split("/")[1])
        log.append(path, "stage.declared",
                   {"id": "B1", "repo": "proj-a", "branch": branch, "after": []},
                   by="planner")
        made = commissioning.commission(name, "B1", builder="coder", by="planner")
        tree = type(repo)(made.worktree)
        tree.commit(file, f"the work of {name}")
        checking.check(name, "B1", by="coder")
        verify(path, "B1", tree.rev(), name)
        capsys.readouterr()
        return tree

    return make


def events(path: Path, kind: str) -> list[dict]:
    return [one for one in log.read_round(path) if one["type"] == kind]


# -- the check ---------------------------------------------------------------------------

def test_the_check_of_a_queue_project_runs_the_scoped_command_against_the_merge_base(
        repo, ready, round_log):
    main_head = repo.rev("main")
    tree = ready()
    ran = events(round_log, checking.RAN)[-1]
    assert ran["commit"] == tree.rev() and ran["merge_base"] == main_head
    assert ran["argv"][-2:] == ["--base", main_head] and ran["exit_code"] == 0
    assert not events(round_log, "ci.read")


def test_the_scoped_check_stays_good_while_the_target_moves_on(repo, ready, round_log):
    tree = ready()
    repo.commit("main.txt", "the target moved")
    state = states.fold(log.read_round(round_log))
    seen = checking.seen_of(state, ROUND, settings=own.load())["B1"]
    assert seen.check[-1] == events(round_log, checking.RAN)[-1]["merge_base"]
    assert checking.passes(state, "B1", checking.CHECK, tip=tree.rev(), argv=seen.check)


def test_a_full_check_runs_in_a_queue_project(ready, round_log, configure):
    # a ci remote that does not exist: a push would refuse the run
    configure('ci = { workflows = ["ci.yml"], local = "none", remote = "nowhere" }')
    tree = ready()
    ran = checking.check(ROUND, "B1", by="coder", full=True)
    assert ran.exit_code == 0 and list(ran.argv) == shlex.split(FULL)
    recorded = events(round_log, checking.RAN)[-1]
    assert (recorded["step"], recorded["commit"], recorded["argv"]) == (
        checking.CHECK, tree.rev(), shlex.split(FULL))
    assert recorded["merge_base"] is not None
    assert not events(round_log, "ci.read")
    assert own.build_setting(own.load(), "proj-a", "land") == own.LAND_QUEUE


def full_only(tree, round_log) -> None:
    """A new commit on the build, verified, and only the full check run at it."""
    tree.commit("more.txt", "more work")
    verify(round_log, "B1", tree.rev())
    assert checking.check(ROUND, "B1", by="coder", full=True).exit_code == 0


def test_a_full_check_meets_the_check_gate_of_a_queue_project(ready, round_log):
    tree = ready()
    full_only(tree, round_log)
    assert joining.join(ROUND, "B1", by="coder").tip == tree.rev()


def test_a_full_check_in_a_queue_project_meets_gate_3_after_land_is_flipped(
        repo, ready, round_log, configure):
    tree = ready()
    full_only(tree, round_log)
    tip = tree.rev()
    configure(land="finish")
    finishing.finish(ROUND, "B1", by="coder")
    assert repo.git("merge-base", "--is-ancestor", tip, "main") == ""


def test_a_failed_full_check_after_a_passed_scoped_one_leaves_the_step_owed(
        ready, round_log, write_config):
    tree = ready()
    failing = shlex.join([sys.executable, "-c", "raise SystemExit(1)"])
    write_config(f"[projects.proj-a]\nland = \"queue\"\n"
                 f"check = {{ command = {json.dumps(failing)}, "
                 f"scoped = {json.dumps(SCOPED)} }}\n")
    assert checking.check(ROUND, "B1", by="coder", full=True).exit_code == 1
    with pytest.raises(Refused) as caught:
        joining.join(ROUND, "B1", by="coder")
    assert any("the check of B1 has not passed" in one.reason
               for one in caught.value.unmet)
    assert events(round_log, checking.RAN)[-1]["commit"] == tree.rev()


# -- joining -----------------------------------------------------------------------------

def test_finish_joins_the_queue_and_merges_nothing(repo, ready, round_log):
    tree = ready()
    main_head = repo.rev("main")
    joined = joining.join(ROUND, "B1", by="coder")
    assert (joined.position, joined.tip, joined.main) == (1, tree.rev(), main_head)
    assert repo.rev("main") == main_head
    [mirrored] = events(round_log, joining.JOINED)
    assert (mirrored["tip"], mirrored["main"], mirrored["urgent"]) == (
        tree.rev(), main_head, False)
    member = queue_file.read("proj-a").member(ROUND, "B1")
    assert member.state == queue_file.QUEUED and member.tip == tree.rev()
    assert not events(round_log, "finish.action")


def test_the_target_gate_is_off_for_a_queued_build(repo, ready, configure):
    configure('check_on = "merged"')
    ready()
    repo.commit("main.txt", "the target moved on")
    assert joining.join(ROUND, "B1", by="coder").position == 1


def test_a_build_whose_scoped_check_did_not_run_at_the_tip_is_refused(ready, round_log):
    tree = ready()
    tree.commit("more.txt", "more work")
    with pytest.raises(Refused) as caught:
        joining.join(ROUND, "B1", by="coder")
    assert any("check of B1 has not passed" in one.reason for one in caught.value.unmet)
    assert not events(round_log, joining.JOINED)
    assert queue_file.read("proj-a").members == {}


def test_finish_actions_other_than_one_merge_are_refused(ready, configure):
    ready()
    configure('finish = [ { do = "merge" }, { do = "push", remote = "origin" } ]')
    with pytest.raises(Refused) as caught:
        joining.join(ROUND, "B1", by="coder")
    assert "only merges" in caught.value.unmet[0].reason


def test_a_direct_finish_is_refused_in_a_queue_project(repo, ready):
    ready()
    main_head = repo.rev("main")
    with pytest.raises(Refused) as caught:
        finishing.finish(ROUND, "B1", by="coder")
    assert caught.value.unmet[0].reason.startswith("land is queue: finish enqueues")
    assert repo.rev("main") == main_head


def test_joining_again_at_the_same_tip_writes_nothing(ready, round_log):
    ready()
    joining.join(ROUND, "B1", by="coder")
    again = joining.join(ROUND, "B1", by="coder")
    assert again.already and again.position == 1
    assert len(events(round_log, joining.JOINED)) == 1
    assert len(queue_file.read("proj-a").events) == 1


def test_a_member_whose_branch_moved_leaves_and_joins_again_at_the_back(
        ready, round_log):
    tree = ready()
    ready(OTHER, "other", "other.txt")
    joining.join(ROUND, "B1", by="coder")
    joining.join(OTHER, "B1", by="coder")
    old = tree.rev()
    tree.commit("more.txt", "more work")
    checking.check(ROUND, "B1", by="coder")
    verify(round_log, "B1", tree.rev())
    again = joining.join(ROUND, "B1", by="coder")
    assert again.moved == old and again.position == 2
    [left] = events(round_log, joining.LEFT)
    assert (left["tip"], left["reason"]) == (old, queue_file.MOVED)
    assert [one.name for one in queue_file.read("proj-a").waiting()] == [OTHER, ROUND]


# -- urgent and leave --------------------------------------------------------------------

def test_an_urgent_member_goes_ahead_of_every_member_that_is_not(ready, round_log):
    ready()
    ready(OTHER, "other", "other.txt")
    joining.join(ROUND, "B1", by="coder")
    urgent = joining.join(OTHER, "B1", by="fixer", urgent=True)
    assert urgent.position == 1 and urgent.urgent
    queue = queue_file.read("proj-a")
    assert [(one.name, one.urgent) for one in queue.waiting()] == [
        (OTHER, True), (ROUND, False)]
    assert queue.member(OTHER, "B1").by == "fixer"
    assert events(round_log, joining.JOINED)[-1]["urgent"] is False


def test_leave_takes_a_waiting_member_out_at_once(ready, round_log):
    ready()
    joining.join(ROUND, "B1", by="coder")
    left = joining.leave(ROUND, "B1", by="lead")
    assert left.rebuilds is None
    assert queue_file.read("proj-a").member(ROUND, "B1").state == queue_file.LEFT
    [mirrored] = events(round_log, joining.LEFT)
    assert (mirrored["reason"], mirrored["by"]) == (queue_file.BY_HAND, "lead")


def test_leave_of_a_member_in_a_running_batch_names_the_batch_built_again(ready):
    tree = ready()
    joining.join(ROUND, "B1", by="coder")
    queue_file.append("proj-a", {
        "event": queue_file.BATCH, "state": queue_file.BUILT, "id": "abc-1", "n": 1,
        "main": "abc", "branch": "queue-abc-1",
        "members": [{"round": ROUND, "stage": "B1", "tip": tree.rev()}]})
    assert joining.leave(ROUND, "B1", by="lead").rebuilds == "abc-1"


def test_leave_of_a_build_not_in_the_queue_is_refused(ready):
    ready()
    with pytest.raises(Refused) as caught:
        joining.leave(ROUND, "B1", by="lead")
    assert "not in proj-a's queue" in caught.value.unmet[0].reason


def test_urgent_and_leave_are_refused_where_the_repo_lands_by_finish(ready, configure):
    ready()
    configure(land="finish")
    with pytest.raises(BadArgument):
        joining.refuse_flags(urgent=True, leave_=False,
                             repo=joining.queue_repo(ROUND, "B1"), name=ROUND, stage="B1")


# -- what the verbs say ------------------------------------------------------------------

def test_the_finish_verb_prints_the_position(ready, capsys):
    tree = ready()
    assert main(["finish", ROUND, "B1"]) == 0
    out = capsys.readouterr().out
    assert f"queued: position 1, {ROUND} B1 at {tree.rev()[:12]}" in out


def test_the_finish_verb_with_leave_says_the_build_left(ready, capsys):
    ready()
    assert main(["finish", ROUND, "B1"]) == 0
    assert main(["finish", ROUND, "B1", "--leave"]) == 0
    assert "left the queue:" in capsys.readouterr().out


def test_next_names_finish_as_joining_the_queue(ready):
    ready()
    said = next_step.as_text(next_step.read(ROUND))
    assert "it joins proj-a's queue, and the lander merges it" in said


def test_next_says_a_queued_build_waits_on_the_lander_with_its_position(ready):
    tree = ready()
    joining.join(ROUND, "B1", by="coder")
    said = next_step.as_text(next_step.read(ROUND))
    assert f"queued at {tree.rev()[:12]}; proj-a's lander merges it" in said
    assert "queue: position 1 in proj-a's queue" in said


def test_next_says_why_a_member_left(ready):
    ready()
    joining.join(ROUND, "B1", by="coder")
    joining.leave(ROUND, "B1", by="lead")
    said = next_step.as_text(next_step.read(ROUND))
    assert "owes:  left: by hand" in said


def test_a_members_standing_is_read_from_the_round_log(ready, round_log):
    tree = ready()
    joining.join(ROUND, "B1", by="coder", urgent=True)
    found = queue_log.standing(states.fold(log.read_round(round_log)), "B1")
    assert found.queued and found.tip == tree.rev() and found.urgent

"""The gates of `finish`: each unmet alone and named with the command that meets it, a
skip of each skippable step meeting the gate that waits on it, `check_on` and CI's form
turning the target gate on and off, and the `finish` step's row read from them."""

from __future__ import annotations

import dataclasses
import json
import shlex
import sys
from pathlib import Path

import pytest

from moxi_rounds import (checking, ci, commissioning, finish_gates, log, next_step,
                         opening, steps, verdicts)
from moxi_rounds import settings as own
from moxi_rounds import state as states
from moxi_rounds.cli import main
from moxi_rounds.errors import Unmet

ROUND = "proj-a/a-round"
CHECK = shlex.join([sys.executable, "-c", "print('the check passed')"])
OTHER_CHECK = shlex.join([sys.executable, "-c", "print('another check')"])
FLOW = ".github/workflows/ci.yml"
POINTER = "comms:1234567"


@pytest.fixture
def repo(git_repo, tmp_path):
    from moxi import cli

    assert cli.main(["projects", "add", "proj-a", "--root", str(git_repo.root)]) == 0
    assert cli.main(["projects", "set", "proj-a", "--notes-root",
                     str(tmp_path / "notes")]) == 0
    return git_repo


@pytest.fixture
def configure(write_config):
    """`configure(extra="", flows="")`: the config with proj-a's check and `extra` keys
    of its table, then `flows` as more tables."""

    def write(extra: str = "", flows: str = "") -> None:
        write_config(f"[projects.proj-a]\ncheck = {{ command = {json.dumps(CHECK)} }}\n"
                     f"{extra}\n{flows}")

    write()
    return write


@pytest.fixture
def opened(repo, configure, tmp_path, capsys):
    """`opened(flow="fix")`: the round in `flow`, with B1 planned on `work`,
    commissioned, and one commit of work on it. Returns `(log, worktree repo)`."""

    def open_it(flow: str = "fix"):
        opening.open_round(ROUND, description="a placeholder round", by="planner",
                           flow=flow)
        path = log.round_log(tmp_path / "notes" / "rounds" / "a-round")
        log.append(path, "stage.declared",
                   {"id": "B1", "repo": "proj-a", "branch": "work", "after": []},
                   by="planner")
        made = commissioning.commission(ROUND, "B1", builder="coder", by="planner")
        tree = type(repo)(made.worktree)
        tree.commit("work.txt", "the work")
        capsys.readouterr()
        return path, tree

    return open_it


def verify(path: Path, head: str, *, name: str = "build-report.md",
           findings: str = "") -> None:
    """Record a publishable build report of B1 read at `head`."""
    report = path.parent / name
    report.write_text(f"# {name}\n\n```toml rounds\n" + "\n".join([
        'kind = "build"', 'verdict = "publishable"', 'pass = "full"', 'by = "critic"',
        'model = "a-model"', 'stage = "B1"', f'head = "{head}"']) + f"\n{findings}```\n",
        encoding="utf-8")
    verdicts.record(ROUND, report, by="critic")


@pytest.fixture
def ready(opened):
    """The round with B1 checked and verified at its tip: every gate holds."""
    path, tree = opened()
    checking.check(ROUND, "B1", by="coder")
    verify(path, tree.rev())
    return path, tree


def folded(path: Path) -> states.State:
    return states.fold(log.read_round(path))


def seen_now(path: Path) -> steps.Seen:
    return checking.seen_of(folded(path), ROUND, settings=own.load())["B1"]


def gates(path: Path, **given) -> tuple[finish_gates.Gate, ...]:
    seen = given.pop("seen", None) or seen_now(path)
    return finish_gates.unmet(folded(path), "B1", seen=seen, name=ROUND, **given)


def numbers(found) -> list[int]:
    return [one.number for one in found]


def finish_step(path: Path, **given) -> steps.Step:
    seen = given["seen"] if "seen" in given else {"B1": seen_now(path)}
    return steps.one(folded(path), "finish", "B1", name=ROUND, seen=seen)


def rule(item: str) -> None:
    assert main(["rule", ROUND, item, "--record", POINTER, "--as", "planner"]) == 0


# -- every gate holds --------------------------------------------------------------------

def test_a_build_checked_and_verified_at_its_tip_meets_every_gate(ready):
    path, _tree = ready

    assert gates(path) == ()
    found = finish_step(path)
    assert (found.state, found.waits, found.gates) == (steps.READY, (), ())


# -- each gate unmet alone ---------------------------------------------------------------

def _unverified(path, tree, repo, configure):
    tree.commit("more.txt", "a commit after the verdict")
    checking.check(ROUND, "B1", by="coder")


def _a_finding_open(path, tree, repo, configure):
    verify(path, tree.rev(), name="second.md", findings=(
        '[[finding]]\nid = "L1"\nseverity = "minor"\nstate = "open"\n'
        'text = "a name is unclear"\n'))


def _asked(path, tree, repo, configure):
    assert main(["ask", ROUND, "Is the page right?", "--from", POINTER, "--blocks", "B1",
                 "--as", "planner"]) == 0


def _target_moved_onto_the_build(path, tree, repo, configure):
    configure('check_on = "tip"')
    repo.commit("work.txt", "the same file changed on the target")


def _target_moved_apart(path, tree, repo, configure):
    repo.commit("other.txt", "a change the build does not touch")


def _untracked(path, tree, repo, configure):
    (tree.root / "notes.txt").write_text("left behind\n", encoding="utf-8")


def _changed(path, tree, repo, configure):
    (tree.root / "work.txt").write_text("changed, not committed\n", encoding="utf-8")


@pytest.mark.parametrize("act, number, said, fix", [
    (_unverified, finish_gates.VERIFIED, "the verification of B1 does not count at",
     f"moxi rounds verdict {ROUND} <your report>"),
    (_a_finding_open, finish_gates.FINDINGS,
     "finding B1.L1: minor, open: a name is unclear",
     f"moxi rounds mark {ROUND} B1.L1 --fixed"),
    (_asked, finish_gates.QUESTIONS, "holds B1: Is the page right?",
     f"moxi rounds rule {ROUND} Q"),
    (_target_moved_onto_the_build, finish_gates.DRIFT,
     "the target moved under B1 since the newest report of verify-build",
     'a re-check with pass = "drift", reading'),
    (_target_moved_apart, finish_gates.TARGET,
     "does not hold the target main", "git merge --no-ff main"),
    (_untracked, finish_gates.WORKTREE, "the worktree of B1 holds a change not committed",
     "commit or put away"),
    (_changed, finish_gates.WORKTREE, "the worktree of B1 holds a change not committed",
     "commit or put away"),
], ids=["verification", "findings", "questions", "drift", "target", "untracked",
        "tracked-change"])
def test_each_gate_unmet_alone_is_named_with_the_command_that_meets_it(
        ready, repo, configure, act, number, said, fix):
    path, tree = ready

    act(path, tree, repo, configure)

    found = gates(path)
    assert set(numbers(found)) == {number}
    assert any(said in one.unmet.reason and fix in one.unmet.fix for one in found)
    step = finish_step(path)
    assert step.state == steps.WAITING
    assert set(step.gates) == {number}


def test_the_check_gate_is_unmet_alone_by_a_pass_of_a_command_no_longer_declared(
        ready, write_config):
    path, _tree = ready
    write_config(f"[projects.proj-a]\ncheck = {{ command = {json.dumps(OTHER_CHECK)} }}\n")

    found = gates(path)

    assert numbers(found) == [finish_gates.CHECKED]
    assert found[0].unmet.reason.startswith("the check of B1 has not passed at ")
    assert found[0].unmet.fix == (f"moxi rounds check {ROUND} B1; or the operator's skip: "
                                  f"moxi rounds rule {ROUND} skip:check:B1 --record PTR")


@pytest.mark.parametrize("field, value, number, said", [
    ("state", "empty", finish_gates.WORK,
     "B1 has no work to finish: it reads empty, and only a build that reads building "
     "or merged finishes, under any skip"),
    ("state", "unclear", finish_gates.WORK, "it reads unclear"),
    ("state", "gone", finish_gates.WORK, "it reads gone"),
    ("shares", (("proj-a/b-round", "B2"),), finish_gates.SHARING,
     "B1 shares commits with proj-a/b-round B2"),
    ("tree", finish_gates.ELSEWHERE, finish_gates.WORKTREE,
     "the worktree of B1 was made on another machine"),
])
def test_the_gates_no_skip_meets_are_read_from_what_git_says(ready, field, value, number,
                                                             said):
    path, _tree = ready
    seen = dataclasses.replace(seen_now(path), **{field: value})

    found = gates(path, seen=seen)

    assert numbers(found) == [number]
    assert said in found[0].unmet.reason


def test_a_build_with_no_work_is_refused_in_a_flow_with_no_check_and_no_verification(
        repo, write_config, opened, tmp_path):
    write_config('[flows.bare]\nsteps = [ { step = "build", by = "builder" }, '
                 '{ step = "finish", by = "builder" } ]\n')
    opening.open_round(ROUND, description="a placeholder round", by="planner",
                       flow="bare")
    path = log.round_log(tmp_path / "notes" / "rounds" / "a-round")
    log.append(path, "stage.declared",
               {"id": "B1", "repo": "proj-a", "branch": "work", "after": []},
               by="planner")
    commissioning.commission(ROUND, "B1", builder="coder", by="planner")

    assert numbers(gates(path)) == [finish_gates.WORK]
    assert seen_now(path).state == "empty"


def test_the_branch_moved_from_the_tip_the_gates_were_read_at_fails_the_worktree_gate(
        ready):
    path, tree = ready
    older = tree.rev("HEAD~1")

    found = [one for one in gates(path, tip=older) if one.number == finish_gates.WORKTREE]

    assert [one.unmet.reason for one in found] == [
        f"the branch work is at {tree.rev()[:12]}, not at {older[:12]}, the tip the "
        "gates were read at"]


def test_an_ignored_path_in_the_worktree_is_left_to_the_removal(ready, repo):
    path, tree = ready
    exclude = repo.root / ".git" / "info" / "exclude"
    exclude.parent.mkdir(parents=True, exist_ok=True)
    exclude.write_text("*.log\n", encoding="utf-8")
    (tree.root / "run.log").write_text("ignored\n", encoding="utf-8")

    assert gates(path) == ()


def test_an_untracked_file_a_config_hides_from_git_status_fails_the_worktree_gate(
        ready, repo):
    path, tree = ready
    repo.git("config", "status.showUntrackedFiles", "no")
    (tree.root / "notes.txt").write_text("work kept only here\n", encoding="utf-8")
    assert repo.git("-C", str(tree.root), "status", "--porcelain") == ""

    assert finish_gates.WORKTREE in numbers(gates(path))


def test_a_worktree_removed_holds_the_worktree_gate(ready, repo):
    path, tree = ready
    repo.git("worktree", "remove", "--force", str(tree.root))

    assert seen_now(path).tree is None
    assert finish_gates.WORKTREE not in numbers(gates(path))


# -- a skip meets the gate that waits on its step ----------------------------------------

@pytest.mark.parametrize("act, skip, number", [
    (_unverified, "skip:verify-build:B1", finish_gates.VERIFIED),
    (_target_moved_onto_the_build, "skip:verify-build:B1", finish_gates.DRIFT),
])
def test_a_skip_of_verification_meets_its_gates(ready, repo, configure, act, skip, number):
    path, tree = ready
    act(path, tree, repo, configure)
    assert number in numbers(gates(path))

    rule(skip)

    assert gates(path) == ()


def test_a_skip_of_the_check_meets_the_check_gate(opened):
    path, tree = opened()
    verify(path, tree.rev())
    assert numbers(gates(path)) == [finish_gates.CHECKED]

    rule("skip:check:B1")

    assert gates(path) == ()


USER_FLOW = """
[flows.gated]
steps = [
  { step = "build", by = "builder" },
  { step = "verify-build", by = "checker" },
  { step = "staging", does = "command", before = "finish", argv = ["true"] },
  { step = "finish", by = "builder" },
]
"""


def test_a_user_step_holds_finish_until_done_at_the_tip_and_a_skip_clears_it(
        opened, configure):
    configure(flows=USER_FLOW)
    path, tree = opened("gated")
    checking.check(ROUND, "B1", by="coder")
    verify(path, tree.rev())

    found = gates(path)
    assert numbers(found) == [finish_gates.USER_STEPS]
    assert found[0].unmet.reason.startswith("the step staging of B1 is not done at ")
    assert found[0].unmet.fix.startswith(f"moxi rounds check {ROUND} B1 --step staging; ")

    checking.check(ROUND, "B1", by="coder", step="staging")
    assert gates(path) == ()

    tree.commit("more.txt", "a commit the run did not see")
    checking.check(ROUND, "B1", by="coder")
    rule("skip:verify-build:B1")
    assert numbers(gates(path)) == [finish_gates.USER_STEPS]
    rule("skip:staging:B1")
    assert gates(path) == ()


# -- the target gate: check_on and CI's form ---------------------------------------------

def test_with_check_on_tip_and_the_full_check_the_target_may_move(ready, repo, configure):
    path, _tree = ready
    configure('check_on = "tip"')
    repo.commit("other.txt", "a change the build does not touch")

    assert gates(path) == ()


def test_with_check_on_tip_and_the_check_skipped_the_target_may_move(opened, repo,
                                                                     configure):
    configure('check_on = "tip"')
    path, tree = opened()
    verify(path, tree.rev())
    rule("skip:check:B1")
    repo.commit("other.txt", "a change the build does not touch")

    assert gates(path) == ()


def _green(path: Path, tip: str, base: str) -> None:
    log.append(path, ci.READ, {
        "stage": "B1", "commit": tip, "merge_base": base, "status_argv": ["status"],
        "ids_argv": ["ids"], "workflows": [{"path": FLOW, "id": 11, "reading": ci.GREEN,
                                            "url": "https://example.test/run/1"}],
        "reading": ci.GREEN}, by="coder")


def test_a_check_met_only_by_ci_needs_the_target_in_the_tip_whatever_check_on_says(
        opened, repo, configure):
    configure(f'check_on = "tip"\nci = {{ workflows = ["{FLOW}"], local = "none" }}')
    path, tree = opened()
    verify(path, tree.rev())
    _green(path, tree.rev(), repo.rev("main"))
    assert gates(path) == ()

    repo.commit("other.txt", "a change the build does not touch")

    found = gates(path)
    assert numbers(found) == [finish_gates.TARGET]
    assert "the check is met only by CI" in found[0].unmet.reason


def test_a_full_check_meets_the_check_gate_with_ci_red(ready, repo, configure):
    path, tree = ready
    configure(f'check_on = "tip"\nci = {{ workflows = ["{FLOW}"], local = "none" }}')
    log.append(path, ci.READ, {
        "stage": "B1", "commit": tree.rev(), "merge_base": repo.rev("main"),
        "status_argv": ["status"], "ids_argv": ["ids"],
        "workflows": [{"path": FLOW, "id": 11, "reading": ci.RED, "url": None}],
        "reading": ci.RED}, by="coder")
    repo.commit("other.txt", "a change the build does not touch")

    assert gates(path) == ()


def test_a_tip_that_holds_the_moved_target_meets_the_target_gate_again(ready, repo):
    path, tree = ready
    repo.commit("other.txt", "a change the build does not touch")
    assert numbers(gates(path)) == [finish_gates.TARGET]

    tree.merge("main")
    checking.check(ROUND, "B1", by="coder")

    assert gates(path) == ()


def test_a_build_merged_by_hand_reads_the_target_before_its_merge(ready, repo):
    path, _tree = ready
    repo.merge("work")

    seen = seen_now(path)
    assert seen.state == "merged"
    assert seen.target_before == repo.rev("main^1")
    assert gates(path) == ()


def _gone(repo, tree) -> None:
    """Remove the build's worktree and delete its branch, as a user may once they merged
    it by hand."""
    repo.git("worktree", "remove", "--force", str(tree.root))
    repo.git("branch", "-D", "work")


def test_a_build_merged_by_hand_whose_branch_is_gone_meets_the_check_gate_at_its_head(
        ready, repo):
    path, tree = ready
    head = tree.rev()
    repo.merge("work")
    _gone(repo, tree)

    seen = seen_now(path)
    assert (seen.tip, seen.head) == (None, head)
    assert gates(path) == ()
    assert steps.one(folded(path), "check", "B1", name=ROUND,
                     seen={"B1": seen}).state == steps.DONE


def test_a_check_met_only_by_ci_is_read_at_the_head_once_the_branch_is_gone(
        opened, repo, configure):
    configure(f'check_on = "tip"\nci = {{ workflows = ["{FLOW}"], local = "none" }}')
    path, tree = opened()
    verify(path, tree.rev())
    _green(path, tree.rev(), repo.rev("main"))
    repo.commit("other.txt", "a change the build does not touch")
    repo.merge("work")
    _gone(repo, tree)

    found = gates(path)

    assert numbers(found) == [finish_gates.TARGET]
    assert "the check is met only by CI" in found[0].unmet.reason


# -- the row -----------------------------------------------------------------------------

def test_finish_is_not_due_before_commission_and_carries_the_holds_on_its_stage(
        repo, tmp_path):
    opening.open_round(ROUND, description="a placeholder round", by="planner",
                       flow="fix")
    path = log.round_log(tmp_path / "notes" / "rounds" / "a-round")
    log.append(path, "stage.declared",
               {"id": "B1", "repo": "proj-a", "branch": "work", "after": []},
               by="planner")
    assert main(["ask", ROUND, "Is the page right?", "--from", POINTER, "--blocks", "B1",
                 "--as", "planner"]) == 0

    found = finish_step(path, seen=None)

    assert found.state == steps.NOT_DUE
    assert [one.reason for one in found.waits] == ["Q1 holds B1: Is the page right?"]


def test_a_finished_build_s_finish_is_done_without_a_read_of_git(ready):
    path, tree = ready
    log.append(path, "finish.done", {"stage": "B1", "tip": tree.rev()}, by="coder")

    assert finish_step(path, seen=None).state == steps.DONE


def test_where_git_was_not_read_finish_waits_on_that_read(ready):
    path, _tree = ready

    found = finish_step(path, seen=None)

    assert (found.state, found.unread) == (steps.WAITING, True)


def test_a_build_git_can_t_read_waits_with_its_problem(ready):
    path, _tree = ready
    problem = Unmet("B1 can't be read from git: gone", f"moxi rounds show {ROUND}")

    found = finish_step(path, seen={"B1": steps.Seen(problem=problem)})

    assert (found.state, found.waits) == (steps.WAITING, (problem,))


def test_next_reaches_finish_once_its_gates_hold(ready):
    groups = next_step.read(ROUND)["groups"]

    assert (groups[0]["step"], groups[0]["stage"], groups[0]["state"]) == (
        "finish", "B1", steps.READY)


def test_verdict_prints_each_unmet_gate_with_its_number(opened, capsys):
    path, tree = opened()
    report = path.parent / "build-report.md"
    report.write_text("```toml rounds\n" + "\n".join([
        'kind = "build"', 'verdict = "publishable"', 'pass = "full"', 'by = "critic"',
        'model = "a-model"', 'stage = "B1"', f'head = "{tree.rev()}"']) + "\n```\n",
        encoding="utf-8")

    assert main(["verdict", ROUND, str(report), "--as", "critic"]) == 0

    out = capsys.readouterr().out
    assert "finish of B1 waits for:\n  gate 3, check: the check of B1 has not passed" in out

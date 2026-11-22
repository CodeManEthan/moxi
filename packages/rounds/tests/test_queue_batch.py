"""One batch of a queue project: the candidate built from the members' joined tips, the
ejections (a conflict, a commit hook, the publishing scan), the check and the read, the
landing re-read under the finish lock, the fast-forward of the target, each member's
records and finish, and a lander killed midway."""

from __future__ import annotations

import json
import shlex
import sys
from pathlib import Path

import pytest

from moxi_rounds import (batching, checking, ci, commissioning, finishing, joining, log,
                         opening, queue_ci, queue_file, verdicts)
from moxi_rounds import state as states

ROUND = "proj-a/a-round"
OTHER = "proj-a/b-round"
FULL = shlex.join([sys.executable, "-c", "print('the full check')"])
SCOPED = shlex.join([sys.executable, "-c", "import sys; print(sys.argv)", "--base",
                     "{base}"])


class FakeCi:
    """The candidate's CI: what it reads (`word`), what a push says (`refusal`, None for
    a push that goes), and the local run's exit code."""

    def __init__(self):
        self.word = ci.GREEN
        self.refusal: str | None = None
        self.local_code = 0
        self.pushes: list[tuple[str, str]] = []
        self.removed: list[str] = []
        self.reruns: list[tuple[tuple[str, ...], bool]] = []

    def push(self, place, commit, ref):
        self.pushes.append((commit, ref))
        if self.refusal is not None:
            return queue_ci.Said(1, self.refusal)
        return queue_ci.Said(0, "")

    def remove(self, place, ref):
        self.removed.append(ref)
        return queue_ci.Said(0, "")

    def local(self, place, values):
        return queue_ci.Local(("ci-local", values["tip"]), self.local_code, 0.5,
                              str(place / "local.log"))

    def read(self, place, commit):
        return queue_ci.Read(self.word, ("101",))

    def rerun(self, place, runs, *, failed):
        self.reruns.append((tuple(runs), failed))
        return queue_ci.Said(0, "")


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
    def write(ci_table: str = 'ci = { workflows = ["ci.yml"], local = "none" }') -> None:
        write_config(f"[projects.proj-a]\nland = \"queue\"\n"
                     f"check = {{ command = {json.dumps(FULL)}, "
                     f"scoped = {json.dumps(SCOPED)} }}\n{ci_table}\n")

    write()
    return write


def round_log_of(tmp_path: Path, name: str) -> Path:
    return log.round_log(tmp_path / "notes" / "rounds" / name.split("/")[1])


@pytest.fixture
def ready(repo, configure, capsys, tmp_path):
    """`ready(name, branch, file, text)`: B1 of the round `name` opened, declared,
    commissioned, one commit writing `text` to `file`, checked, verified and joined.
    Returns the worktree's repo."""

    def make(name: str = ROUND, branch: str = "work", file: str = "work.txt",
             text: str | None = None, *, joins: bool = True):
        opening.open_round(name, description="a placeholder round", by="planner",
                           flow="fix")
        path = round_log_of(tmp_path, name)
        log.append(path, "stage.declared",
                   {"id": "B1", "repo": "proj-a", "branch": branch, "after": []},
                   by="planner")
        made = commissioning.commission(name, "B1", builder="coder", by="planner")
        tree = type(repo)(made.worktree)
        tree.commit(file, text if text is not None else f"the work of {name}")
        checking.check(name, "B1", by="coder")
        report = path.parent / "build-report-B1.md"
        report.write_text("# report\n\n```toml rounds\n" + "\n".join([
            'kind = "build"', 'verdict = "publishable"', 'pass = "full"',
            'by = "critic"', 'model = "a-model"', 'stage = "B1"',
            f'head = "{tree.rev()}"']) + "\n```\n", encoding="utf-8")
        verdicts.record(name, report, by="critic")
        if joins:
            joining.join(name, "B1", by="coder")
        capsys.readouterr()
        return tree

    return make


@pytest.fixture
def fake():
    return FakeCi()


@pytest.fixture
def lander_pass(fake):
    """`lander_pass(now)`: one pass of the lander, the clock reading `now`; the lines it
    said."""
    said: list[str] = []

    def run(now: float = 0.0) -> list[str]:
        start = len(said)
        batching.step("proj-a", candidate_ci=fake, say=said.append, clock=lambda: now)
        return said[start:]

    return run


def events(tmp_path: Path, name: str, kind: str) -> list[dict]:
    return [one for one in log.read_round(round_log_of(tmp_path, name))
            if one["type"] == kind]


def queue() -> queue_file.Queue:
    return queue_file.read("proj-a")


# -- take --------------------------------------------------------------------------------

def join_event(name: str, *, urgent: bool = False) -> dict:
    return {"event": queue_file.JOIN, "round": name, "stage": "B1", "tip": "a" * 40,
            "urgent": urgent}


def test_urgent_members_are_taken_alone_and_even_while_the_queue_is_paused():
    found = queue_file.fold("proj-a", [join_event("p/one"), join_event("p/two", urgent=True),
                                       {"event": queue_file.PAUSED, "reason": "a pause"}])
    assert [one.name for one in batching.take(found)] == ["p/two"]
    found = queue_file.fold("proj-a", [join_event("p/one"),
                                       {"event": queue_file.PAUSED, "reason": "a pause"}])
    assert batching.take(found) == []
    found = queue_file.fold("proj-a", [join_event("p/one"), join_event("p/two")])
    assert [one.name for one in batching.take(found)] == ["p/one", "p/two"]


# -- a batch that lands ------------------------------------------------------------------

def test_a_lone_member_lands_by_a_fast_forward_and_is_finished_as_found(
        repo, ready, fake, lander_pass, tmp_path):
    tree = ready()
    tip, before = tree.rev(), repo.rev("main")
    lander_pass()
    batch = queue().running()
    assert batch is not None and batch.branch == f"queue-{before[:12]}-1"
    assert fake.pushes == [(batch.candidate, batch.branch)]
    assert events(tmp_path, ROUND, "queue.batch")[-1]["candidate"] == batch.candidate

    lander_pass()
    assert repo.rev("main") == batch.candidate
    assert repo.git("rev-list", "--parents", "-n1", "main").split()[1:] == [before, tip]
    action = events(tmp_path, ROUND, finishing.ACTION)[-1]
    assert (action["state"], action["landing"], action["target_before"]) == (
        finishing.FOUND, batch.candidate, before)
    assert events(tmp_path, ROUND, finishing.DONE)[-1]["tip"] == tip
    read = events(tmp_path, ROUND, ci.READ)[-1]
    assert (read["commit"], read["candidate"], read["batch"], read["reading"]) == (
        tip, batch.candidate, batch.id, ci.GREEN)
    assert events(tmp_path, ROUND, "queue.landed")[-1]["landing"] == batch.candidate
    assert queue().member(ROUND, "B1").state == queue_file.LANDED
    assert queue().running() is None and fake.removed == [batch.branch]
    assert repo.git("branch", "--list", batch.branch) == ""


def test_the_check_gate_accepts_a_candidate_read_green_with_its_local_run(
        repo, ready, fake, lander_pass, tmp_path, configure):
    configure('ci = { workflows = ["ci.yml"], local = { command = "ci-local" } }')
    tip = ready().rev()
    lander_pass()
    lander_pass()
    state = states.fold(log.read_round(round_log_of(tmp_path, ROUND)))
    local = events(tmp_path, ROUND, checking.RAN)[-1]
    assert local["step"] == ci.LOCAL and local["candidate"] == repo.rev("main")
    assert ci.candidate_why_not(state, "B1", tip=tip) is None


def test_two_members_that_change_one_file_and_merge_cleanly_both_land_in_order(
        repo, ready, fake, lander_pass, tmp_path):
    repo.commit("shared.txt", "one\ntwo\nthree\nfour\nfive\nsix\n")
    first = ready(file="shared.txt", text="ONE\ntwo\nthree\nfour\nfive\nsix\n").rev()
    second = ready(OTHER, branch="other", file="shared.txt",
                   text="one\ntwo\nthree\nfour\nfive\nSIX\n").rev()
    lander_pass()
    assert len(queue().running().members) == 2
    lander_pass()
    landed_first = events(tmp_path, ROUND, finishing.ACTION)[-1]
    landed_second = events(tmp_path, OTHER, finishing.ACTION)[-1]
    assert landed_second["target_before"] == landed_first["landing"]
    assert landed_second["landing"] == repo.rev("main")
    assert events(tmp_path, ROUND, finishing.DONE)[-1]["tip"] == first
    assert events(tmp_path, OTHER, finishing.DONE)[-1]["tip"] == second


# -- ejections ---------------------------------------------------------------------------

def test_a_member_that_conflicts_is_ejected_naming_the_member_before_it(
        repo, ready, fake, lander_pass, tmp_path):
    ready(file="same.txt", text="the first\n")
    ready(OTHER, branch="other", file="same.txt", text="the second\n")
    lander_pass()
    ejected = events(tmp_path, OTHER, "queue.ejected")[-1]
    assert ejected["blocker"] == f"{ROUND} B1"
    assert "merge main into the branch" in ejected["reason"]
    assert [one[:2] for one in queue().running().members] == [(ROUND, "B1")]
    lander_pass()
    assert queue().member(ROUND, "B1").state == queue_file.LANDED
    assert queue().member(OTHER, "B1").state == queue_file.EJECTED


def test_a_member_a_commit_hook_refuses_is_ejected_with_the_hooks_output(
        repo, ready, fake, lander_pass, tmp_path):
    hook = repo.root / ".git" / "hooks" / "pre-merge-commit"
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text("#!/bin/sh\nif git diff --cached --name-only | grep -q refused.txt;"
                    " then echo 'refused.txt is not taken' >&2; exit 1; fi\n",
                    encoding="utf-8")
    hook.chmod(0o755)
    ready()
    ready(OTHER, branch="other", file="refused.txt")
    lander_pass()
    ejected = events(tmp_path, OTHER, "queue.ejected")[-1]
    assert "commit hook" in ejected["reason"]
    assert "refused.txt is not taken" in ejected["output"]
    assert [one[:2] for one in queue().running().members] == [(ROUND, "B1")]


def test_a_scan_refusal_naming_a_member_ejects_it_and_the_rest_are_rebuilt(
        repo, ready, fake, lander_pass, tmp_path):
    ready()
    ready(OTHER, branch="other", file="secret.txt")
    fake.refusal = "the scan refused: secret.txt holds a key"
    lander_pass()
    assert queue().member(OTHER, "B1").state == queue_file.EJECTED
    assert queue().batches[-1].why == batching.SCAN_WHY
    fake.refusal = None
    lander_pass()
    assert [one[:2] for one in queue().running().members] == [(ROUND, "B1")]
    assert queue().running().n == 2


def test_a_push_refusal_naming_no_member_is_tried_again_then_pauses_the_queue(
        repo, ready, fake, lander_pass):
    ready()
    fake.refusal = "the scan refused: the remote is not public"
    lander_pass()
    assert queue().paused is None and len(fake.pushes) == 1
    lander_pass()
    assert "not public" in queue().paused and len(fake.pushes) == 2
    assert queue().member(ROUND, "B1").state == queue_file.QUEUED
    lander_pass()
    assert queue().running() is None


# -- the landing re-read -----------------------------------------------------------------

def test_a_member_that_left_is_dropped_at_the_landing_and_the_rest_rebuilt(
        repo, ready, fake, lander_pass):
    ready()
    ready(OTHER, branch="other", file="other.txt")
    lander_pass()
    joining.leave(OTHER, "B1", by="coder")
    lander_pass()
    assert queue().batches[-1].why.startswith("rebuilt without")
    assert repo.rev("main") == queue().batches[-1].main
    lander_pass()
    assert [one[:2] for one in queue().running().members] == [(ROUND, "B1")]
    lander_pass()
    assert queue().member(ROUND, "B1").state == queue_file.LANDED


def test_a_member_whose_branch_moved_is_left_as_moved_at_the_landing(
        repo, ready, fake, lander_pass, tmp_path):
    ready()
    other = ready(OTHER, branch="other", file="other.txt")
    lander_pass()
    other.commit("more.txt", "a commit after joining")
    lander_pass()
    assert queue().member(OTHER, "B1").reason == queue_file.MOVED
    assert events(tmp_path, OTHER, "queue.left")[-1]["reason"] == queue_file.MOVED


def test_the_target_moved_by_hand_before_the_landing_rebuilds_on_it(
        repo, ready, fake, lander_pass):
    ready()
    lander_pass()
    repo.commit("hand.txt", "a hand commit on main")
    hand = repo.rev("main")
    lander_pass()
    assert queue().batches[-1].why == batching.MOVED_WHY
    lander_pass()
    assert queue().running().main == hand
    lander_pass()
    assert queue().member(ROUND, "B1").state == queue_file.LANDED


def test_a_checkout_of_the_target_not_clean_waits_and_the_landing_is_tried_again(
        repo, ready, fake, lander_pass):
    ready()
    lander_pass()
    (repo.root / "tracked.txt").write_text("not committed", encoding="utf-8")
    repo.git("add", "tracked.txt")
    lander_pass()
    lander_pass()
    assert [one for one in queue().events if one["event"] == queue_file.WAITING][-1][
        "reason"].endswith("not clean")
    assert len([one for one in queue().events if one["event"] == queue_file.WAITING]) == 1
    assert queue().running() is not None
    repo.git("rm", "--cached", "--quiet", "tracked.txt")
    (repo.root / "tracked.txt").unlink()
    lander_pass()
    assert queue().member(ROUND, "B1").state == queue_file.LANDED


# -- a lander killed ---------------------------------------------------------------------

def test_a_candidate_on_the_target_already_is_finished_member_by_member(
        repo, ready, fake, lander_pass, tmp_path):
    tree = ready()
    lander_pass()
    batch = queue().running()
    repo.git("merge", "--ff-only", "--quiet", batch.candidate)
    tree.commit("late.txt", "a commit after the landing")
    lander_pass()
    assert events(tmp_path, ROUND, finishing.DONE)[-1]["tip"] == batch.members[0][2]
    assert queue().member(ROUND, "B1").state == queue_file.LANDED
    assert repo.git("branch", "--list", "work").strip()
    assert tree.root.is_dir()


# -- the read ----------------------------------------------------------------------------

def test_a_pending_run_waits_and_a_red_one_ejects_its_member(
        repo, ready, fake, lander_pass, tmp_path):
    ready()
    fake.word = ci.PENDING
    lander_pass()
    lander_pass()
    assert queue().running() is not None
    fake.word = ci.RED
    lander_pass()
    assert events(tmp_path, ROUND, "queue.ejected")[-1]["reason"] == "CI red"


def test_a_failing_local_run_counts_as_red(repo, ready, fake, lander_pass, configure):
    configure('ci = { workflows = ["ci.yml"], local = { command = "ci-local" } }')
    ready()
    fake.local_code = 1
    lander_pass()
    lander_pass()
    assert queue().member(ROUND, "B1").state == queue_file.EJECTED


def test_a_cancelled_run_is_rerun_once_then_the_queue_pauses(repo, ready, fake,
                                                             lander_pass):
    ready()
    fake.word = queue_ci.CANCELLED
    lander_pass()
    lander_pass()
    assert fake.reruns == [(("101",), False)]
    lander_pass()
    assert "cancelled twice" in queue().paused


def test_an_absent_run_is_pushed_again_under_the_next_number_then_pauses(
        repo, ready, fake, lander_pass):
    ready()
    fake.word = ci.ABSENT
    lander_pass(0.0)
    lander_pass(60.0)
    assert queue().running().n == 1
    lander_pass(batching.ABSENT_S + 1)
    assert queue().batches[-1].why == batching.ABSENT_WHY
    lander_pass(batching.ABSENT_S + 2)
    assert queue().running().n == 2 and len(fake.pushes) == 2
    lander_pass(3 * batching.ABSENT_S)
    assert "absent twice" in queue().paused


def test_a_repo_with_no_ci_setting_pauses_its_queue(repo, ready, configure, capsys):
    configure("")
    ready()
    batching.step("proj-a", say=lambda _line: None)
    assert "no ci setting" in queue().paused


def test_a_failed_read_is_tried_again_and_pauses_the_queue_after_too_many(
        repo, ready, fake, lander_pass):
    ready()
    lander_pass()

    def fails(place, commit):
        raise ci.ReadFailed(["gh", "run", "list"], "exited 1: no network")

    fake.read = fails
    for _ in range(batching.READ_FAILS_MAX - 1):
        lander_pass()
    assert queue().paused is None and queue().running() is not None
    lander_pass()
    assert "no network" in queue().paused

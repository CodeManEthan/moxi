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
                         opening, queue_ci, queue_file, queue_log, verdicts)
from moxi_rounds import state as states
from moxi_rounds.cli import main
from moxi_rounds.errors import Refused

ROUND = "proj-a/a-round"
OTHER = "proj-a/b-round"
FULL = shlex.join([sys.executable, "-c", "print('the full check')"])
SCOPED = shlex.join([sys.executable, "-c", "import sys; print(sys.argv)", "--base",
                     "{base}"])


class FakeCi:
    """The candidate's CI: what it reads (`word` for a commit pushed as a candidate,
    `main_word` for any other, `words` by commit, and `landed_word`, when set, for the
    target's head as `main_of()` gives it), what a push says
    (`refusal`, None for a push that goes), the local run's exit code (`local_code`, or
    `local_codes` by commit), and a red run's failures (`failed`, or `failed_at` by
    commit)."""

    def __init__(self):
        self.word = ci.GREEN
        self.main_word = ci.GREEN
        self.landed_word: str | None = None
        self.main_of = None
        self.words: dict[str, str] = {}
        self.refusal: str | None = None
        self.local_code = 0
        self.local_codes: dict[str, int] = {}
        self.failed: list[dict] = []
        self.failed_at: dict[str, list[dict]] = {}
        self.pushes: list[tuple[str, str]] = []
        self.removed: list[str] = []
        self.reruns: list[tuple[tuple[str, ...], bool]] = []
        self.locals: list[str] = []

    def push(self, place, commit, ref):
        self.pushes.append((commit, ref))
        if self.refusal is not None:
            return queue_ci.Said(1, self.refusal)
        return queue_ci.Said(0, "")

    def remove(self, place, ref):
        self.removed.append(ref)
        return queue_ci.Said(0, "")

    def local(self, place, values):
        self.locals.append(values["tip"])
        return queue_ci.Local(("ci-local", values["tip"]),
                              self.local_codes.get(values["tip"], self.local_code), 0.5,
                              str(place / "local.log"))

    def read(self, place, commit):
        if self.landed_word is not None and self.main_of is not None \
                and commit == self.main_of() and commit not in self.words:
            return queue_ci.Read(self.landed_word, ("202",))
        if commit in self.words:
            return queue_ci.Read(self.words[commit], ("101",))
        candidate = any(pushed == commit and "queue-" in ref for pushed, ref in self.pushes)
        return queue_ci.Read(self.word if candidate else self.main_word, ("101",))

    def failures(self, place, commit, read):
        return [dict(one) for one in self.failed_at.get(commit, self.failed)]

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
    commissioned, its branch reset to `base` when given, one commit writing `text` to
    `file` (none when `file` is None), checked, verified and joined. Returns the
    worktree's repo."""

    def make(name: str = ROUND, branch: str = "work", file: str | None = "work.txt",
             text: str | None = None, *, joins: bool = True, base: str | None = None):
        opening.open_round(name, description="a placeholder round", by="planner",
                           flow="fix")
        path = round_log_of(tmp_path, name)
        log.append(path, "stage.declared",
                   {"id": "B1", "repo": "proj-a", "branch": branch, "after": []},
                   by="planner")
        made = commissioning.commission(name, "B1", builder="coder", by="planner")
        tree = type(repo)(made.worktree)
        if base is not None:
            tree.git("reset", "-q", "--hard", base)
        if file is not None:
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
        batching.step("proj-a", candidate_ci=fake, comms=None, say=said.append,
                      clock=lambda: now)
        return said[start:]

    return run


def events(tmp_path: Path, name: str, kind: str) -> list[dict]:
    return [one for one in log.read_round(round_log_of(tmp_path, name))
            if one["type"] == kind]


def queue() -> queue_file.Queue:
    return queue_file.read("proj-a")


def back_from(tmp_path: Path, name: str, batch: str) -> str:
    """The member's standing as its round's log reads it, which must be queued again
    after `batch` ended without it; the line `next` says."""
    standing = queue_log.standing(
        states.fold(log.read_round(round_log_of(tmp_path, name))), "B1")
    assert standing.queued and standing.last == queue_log.REQUEUED
    assert standing.fields["batch"] == batch
    return standing.line()


def rebuilt_after(tmp_path: Path, name: str, batch: str) -> bool:
    """Whether the member's round log says it went back to waiting when `batch` ended
    and was then taken into the batch built in the same pass."""
    found = [(one["type"], one.get("batch")) for one in log.read_round(
        round_log_of(tmp_path, name)) if one["type"].startswith("queue.")]
    return (found[-2] == (queue_log.REQUEUED, batch)
            and found[-1][0] == "queue.batch" and found[-1][1] != batch)


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


def test_while_paused_a_group_with_no_urgent_member_keeps_no_urgent_member_waiting(
        repo, ready, fake, lander_pass):
    ready()
    ready(OTHER, branch="other", file="other.txt")
    lander_pass()
    batch = queue().running()
    queue_file.append("proj-a", {
        "event": queue_file.BATCH, "state": queue_file.ENDED, "id": batch.id,
        "why": batching.SPLIT_WHY, "regroups": [[list(one)] for one in batch.members]})
    queue_file.append("proj-a", {"event": queue_file.PAUSED, "reason": "a pause"})
    urgent = "proj-a/c-round"
    ready(urgent, branch="third", file="third.txt", joins=False)
    joining.join(urgent, "B1", by="coder", urgent=True)
    lander_pass()
    assert [one[:2] for one in queue().running().members] == [(urgent, "B1")]
    assert len(queue().regrouped()) == 2


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
    assert ci.candidate_why_not(state, "B1", tip=tip, has_local=True) is None
    without = states.fold([one for one in log.read_round(round_log_of(tmp_path, ROUND))
                           if one.get("step") != ci.LOCAL])
    assert "no check.local ran" in ci.candidate_why_not(without, "B1", tip=tip,
                                                        has_local=True)
    assert ci.candidate_why_not(without, "B1", tip=tip, has_local=False) is None


class Killed(Exception):
    """The lander killed where the stand-in raises it."""


def test_a_lander_killed_during_its_local_run_runs_it_before_landing(
        repo, ready, fake, lander_pass, tmp_path, configure):
    configure('ci = { workflows = ["ci.yml"], local = { command = "ci-local" } }')
    tip = ready().rev()
    local = fake.local

    def killed(place, values):
        raise Killed

    fake.local = killed
    with pytest.raises(Killed):
        lander_pass()
    fake.local = local
    batch = queue().running()
    assert [one["kind"] for one in batch.runs] == [batching.PUSHED]
    lander_pass()
    assert fake.locals == [batch.candidate]
    assert queue().member(ROUND, "B1").state == queue_file.LANDED
    ran = events(tmp_path, ROUND, checking.RAN)[-1]
    assert (ran["step"], ran["commit"], ran["candidate"]) == (ci.LOCAL, tip,
                                                              batch.candidate)


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


INNER = "proj-a/inner-round"
OUTER = "proj-a/outer-round"


def early_commit(repo) -> str:
    """A commit on a branch of its own off the target: the inner build's work."""
    repo.git("checkout", "--quiet", "-b", "early", "main")
    early = repo.commit("inner.txt", "the inner work")
    repo.git("checkout", "--quiet", "main")
    return early


def test_a_build_the_sharing_gate_holds_is_refused_at_join(repo, ready):
    early = early_commit(repo)
    ready(INNER, branch="inner", file=None, base=early, joins=False)
    ready(OUTER, branch="outer", file="outer.txt", base=early, joins=False)
    with pytest.raises(Refused, match=f"held until {INNER} B1's work is on the target"):
        joining.join(OUTER, "B1", by="coder")
    joining.join(INNER, "B1", by="coder")
    assert queue().member(OUTER, "B1") is None


def test_a_member_held_after_joining_is_dropped_and_the_rest_and_the_inner_land(
        repo, ready, fake, lander_pass):
    early = early_commit(repo)
    ready(OUTER, branch="outer", file="outer.txt", base=early)
    ready(OTHER, branch="other", file="other.txt")
    lander_pass()
    assert [one[:2] for one in queue().running().members] == [(OUTER, "B1"),
                                                              (OTHER, "B1")]
    ready(INNER, branch="inner", file=None, base=early, joins=False)
    lander_pass()
    outer = queue().member(OUTER, "B1")
    assert outer.state == queue_file.LEFT
    assert outer.reason.startswith("gate 10:") and "held until" in outer.reason
    assert repo.rev("main") == queue().batches[-1].main
    lander_pass()
    assert [one[:2] for one in queue().running().members] == [(OTHER, "B1")]
    lander_pass()
    assert queue().member(OTHER, "B1").state == queue_file.LANDED
    joining.join(INNER, "B1", by="coder")
    lander_pass()
    lander_pass()
    assert queue().member(INNER, "B1").state == queue_file.LANDED
    assert repo.git("merge-base", "--is-ancestor", early, "main") == ""


def test_the_target_moved_by_hand_before_the_landing_rebuilds_on_it(
        repo, ready, fake, lander_pass, tmp_path):
    ready()
    lander_pass()
    repo.commit("hand.txt", "a hand commit on main")
    hand = repo.rev("main")
    lander_pass()
    moved = queue().batches[-1]
    assert moved.why == batching.MOVED_WHY
    assert back_from(tmp_path, ROUND, moved.id).endswith(
        f"back from batch {moved.id}: {batching.MOVED_WHY}")
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


def test_a_leave_after_the_fast_forward_is_refused_and_the_member_lands(
        repo, ready, fake, lander_pass, tmp_path):
    ready()
    lander_pass()
    batch = queue().running()
    repo.git("merge", "--ff-only", "--quiet", batch.candidate)
    with pytest.raises(Refused, match="has landed on main"):
        joining.leave(ROUND, "B1", by="coder")
    lander_pass()
    assert queue().member(ROUND, "B1").state == queue_file.LANDED
    assert events(tmp_path, ROUND, finishing.DONE)


def test_a_leave_the_fast_forward_overtakes_is_overruled_by_the_landing(
        repo, ready, fake, lander_pass, tmp_path):
    ready()
    ready(OTHER, branch="other", file="other.txt")
    lander_pass()
    batch = queue().running()
    repo.git("merge", "--ff-only", "--quiet", batch.candidate)
    member = queue().member(OTHER, "B1")
    queue_file.append("proj-a", {"event": queue_file.LEFT, "round": OTHER, "stage": "B1",
                                 "tip": member.tip, "reason": queue_file.BY_HAND,
                                 "by": "coder", "batch": batch.id})
    lander_pass()
    for name in (ROUND, OTHER):
        assert queue().member(name, "B1").state == queue_file.LANDED
        assert events(tmp_path, name, finishing.ACTION)[-1]["state"] == finishing.FOUND
        assert events(tmp_path, name, finishing.DONE)
    landed = events(tmp_path, OTHER, "queue.landed")[-1]
    assert landed["left_late"] is True
    assert "left_late" not in events(tmp_path, ROUND, "queue.landed")[-1]


def test_a_join_at_a_new_tip_the_fast_forward_overtakes_stays_queued_at_it(
        repo, ready, fake, lander_pass, tmp_path):
    tree = ready()
    lander_pass()
    batch = queue().running()
    old = batch.members[0][2]
    repo.git("merge", "--ff-only", "--quiet", batch.candidate)
    new = tree.commit("late.txt", "a commit after the landing")
    for event in ({"event": queue_file.LEFT, "round": ROUND, "stage": "B1", "tip": old,
                   "reason": queue_file.MOVED, "by": "coder"},
                  {"event": queue_file.JOIN, "round": ROUND, "stage": "B1", "tip": new,
                   "main": batch.main, "urgent": False, "by": "coder"}):
        queue_file.append("proj-a", event)
    lander_pass()
    assert events(tmp_path, ROUND, finishing.ACTION)[-1]["tip"] == old
    assert events(tmp_path, ROUND, finishing.DONE)[-1]["tip"] == old
    landed = events(tmp_path, ROUND, "queue.landed")[-1]
    assert (landed["tip"], landed["left_late"]) == (old, True)
    member = queue().member(ROUND, "B1")
    assert (member.state, member.tip) == (queue_file.QUEUED, new)
    assert queue().running() is None
    lander_pass()
    assert queue().member(ROUND, "B1").state == queue_file.LEFT
    assert repo.git("merge-base", "main", new) != new


# -- the read ----------------------------------------------------------------------------

def test_a_pending_run_waits(repo, ready, fake, lander_pass):
    ready()
    fake.word = ci.PENDING
    lander_pass()
    lander_pass()
    assert queue().running() is not None and fake.reruns == []


def test_a_cancelled_run_is_rerun_once_then_the_queue_pauses(repo, ready, fake,
                                                             lander_pass, tmp_path):
    ready()
    fake.word = queue_ci.CANCELLED
    lander_pass()
    lander_pass()
    assert fake.reruns == [(("101",), False)]
    lander_pass()
    assert "cancelled twice" in queue().paused
    member = queue().member(ROUND, "B1")
    assert (member.state, queue().position(ROUND, "B1")) == (queue_file.QUEUED, 1)
    line = back_from(tmp_path, ROUND, queue().batches[-1].id)
    assert line.startswith(f"queued at {member.tip[:12]}") and "CI pending" not in line


def test_an_absent_run_is_pushed_again_under_the_next_number_then_pauses(
        repo, ready, fake, lander_pass, tmp_path):
    ready()
    fake.word = ci.ABSENT
    lander_pass(0.0)
    lander_pass(60.0)
    assert queue().running().n == 1
    lander_pass(batching.ABSENT_S + 1)
    assert queue().batches[-1].why == batching.ABSENT_WHY
    back_from(tmp_path, ROUND, queue().batches[-1].id)
    lander_pass(batching.ABSENT_S + 2)
    assert queue().running().n == 2 and len(fake.pushes) == 2
    lander_pass(3 * batching.ABSENT_S)
    assert "absent twice" in queue().paused


def test_a_repo_with_no_ci_setting_pauses_its_queue(repo, ready, configure, capsys):
    configure("")
    ready()
    batching.step("proj-a", comms=None, say=lambda _line: None)
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


# -- the landing re-read drops a member a gate now holds -----------------------------------

def _gains_a_finding(tree, tmp_path):
    report = round_log_of(tmp_path, OTHER).parent / "second.md"
    report.write_text("# second\n\n```toml rounds\n" + "\n".join([
        'kind = "build"', 'verdict = "publishable"', 'pass = "full"', 'by = "critic"',
        'model = "a-model"', 'stage = "B1"', f'head = "{tree.rev()}"',
        "[[finding]]", 'id = "L1"', 'severity = "minor"', 'state = "open"',
        'text = "a name is unclear"']) + "\n```\n", encoding="utf-8")
    verdicts.record(OTHER, report, by="critic")


def _gains_a_question(tree, tmp_path):
    assert main(["ask", OTHER, "Is the page right?", "--from", "comms:1234567",
                 "--blocks", "B1", "--as", "planner"]) == 0


@pytest.mark.parametrize("gain", [_gains_a_finding, _gains_a_question],
                         ids=["finding", "question"])
def test_a_member_that_gained_a_finding_or_a_question_is_dropped_at_the_landing(
        repo, ready, fake, lander_pass, tmp_path, gain):
    ready()
    other = ready(OTHER, branch="other", file="other.txt")
    lander_pass()
    gain(other, tmp_path)
    lander_pass()
    member = queue().member(OTHER, "B1")
    assert member.state == queue_file.LEFT and member.reason.startswith("gate ")
    assert queue().batches[-1].why == f"rebuilt without {OTHER} B1"
    lander_pass()
    lander_pass()
    assert queue().member(ROUND, "B1").state == queue_file.LANDED


# -- a red candidate ---------------------------------------------------------------------

#: A failed test as the candidate's scoped job and main's full job name it.
IN_SCOPED = {"job": "scoped", "test": "tests/test_a.py::test_one"}
IN_LINUX = {"job": "linux", "test": "tests/test_a.py::test_one"}
LOCAL_FAILED = {"job": batching.LOCAL_JOB, "test": None}


def test_a_red_candidate_is_rerun_once_and_lands_when_the_rerun_is_green(
        repo, ready, fake, lander_pass, tmp_path):
    ready()
    fake.word = ci.RED
    lander_pass()
    lander_pass()
    assert fake.reruns == [(("101",), True)]
    fake.word = ci.GREEN
    lander_pass()
    assert queue().member(ROUND, "B1").state == queue_file.LANDED
    assert events(tmp_path, ROUND, ci.READ)[-1]["excused"] == []


def test_a_failure_mains_run_at_m_has_blames_no_member_and_lands_excused(
        repo, ready, fake, lander_pass, tmp_path):
    main_head = repo.rev("main")
    ready()
    fake.word, fake.failed = ci.RED, [IN_SCOPED]
    fake.words[main_head], fake.failed_at[main_head] = ci.RED, [IN_LINUX]
    lander_pass(0.0)
    lander_pass(0.0)
    lander_pass(0.0)
    # main's own run at M is read and rerun from the first pass, beside the candidate's
    assert fake.reruns == [(("101",), True)] * 2 and queue().running() is not None
    lander_pass(batching.ABSENT_S)
    assert queue().main_at(main_head)["failures"] == [IN_LINUX]
    assert queue().member(ROUND, "B1").state == queue_file.LANDED
    assert events(tmp_path, ROUND, "queue.landed")[-1]["excused"] == [IN_SCOPED]
    read = events(tmp_path, ROUND, ci.READ)[-1]
    assert (read["reading"], read["excused"]) == (ci.RED, [IN_SCOPED])
    assert events(tmp_path, ROUND, finishing.DONE)


def test_a_new_failure_with_one_member_ejects_it_with_the_failures(
        repo, ready, fake, lander_pass, tmp_path):
    ready()
    fake.word, fake.failed = ci.RED, [IN_SCOPED]
    fake.words[repo.rev("main")] = ci.GREEN
    lander_pass(0.0)
    lander_pass(0.0)
    lander_pass(batching.ABSENT_S)
    ejected = events(tmp_path, ROUND, "queue.ejected")[-1]
    assert ejected["reason"] == "CI red: scoped: tests/test_a.py::test_one"
    assert ejected["failures"] == [IN_SCOPED]
    assert queue().batches[-1].why == batching.RED_WHY
    assert repo.rev("main") == queue().batches[-1].main


def test_a_red_candidate_waits_for_mains_run_and_ends_when_main_moves(
        repo, ready, fake, lander_pass):
    ready()
    fake.word, fake.failed = ci.RED, [IN_SCOPED]
    fake.words[repo.rev("main")] = ci.PENDING
    lander_pass(0.0)
    lander_pass(0.0)
    lander_pass(batching.ABSENT_S)
    assert queue().running() is not None and queue().waiting_on is not None
    repo.commit("hand.txt", "a hand commit on main")
    lander_pass(batching.ABSENT_S)
    assert queue().batches[-1].why == batching.MOVED_WHY
    assert queue().member(ROUND, "B1").state == queue_file.QUEUED


def test_a_new_failure_splits_the_batch_and_the_earlier_green_half_lands_first(
        repo, ready, fake, lander_pass, tmp_path):
    ready()
    ready(OTHER, branch="other", file="other.txt")
    fake.word, fake.failed = ci.RED, [IN_SCOPED]
    fake.words[repo.rev("main")] = ci.GREEN
    lander_pass(0.0)
    lander_pass(0.0)
    split = queue().running()
    lander_pass(batching.ABSENT_S)
    assert queue().batch(split.id).why == batching.SPLIT_WHY
    for name in (ROUND, OTHER):
        assert rebuilt_after(tmp_path, name, split.id)
    first, second = queue().running_batches()
    assert [one.members[0][0] for one in (first, second)] == [ROUND, OTHER]
    assert (first.source, second.source) == ((split.id, 0), (split.id, 1))
    assert first.main == second.main == repo.rev("main")

    fake.word = ci.GREEN
    fake.words[first.candidate] = ci.PENDING
    lander_pass(batching.ABSENT_S)
    assert [one.id for one in queue().running_batches()] == [first.id, second.id]
    del fake.words[first.candidate]
    lander_pass(batching.ABSENT_S)
    assert queue().member(ROUND, "B1").state == queue_file.LANDED
    assert queue().batch(second.id).why == batching.MOVED_WHY
    assert rebuilt_after(tmp_path, OTHER, second.id)
    rebuilt = queue().running()
    assert rebuilt.source == (second.id, 0) and rebuilt.main == repo.rev("main")
    lander_pass(batching.ABSENT_S)
    assert queue().member(OTHER, "B1").state == queue_file.LANDED


def test_a_failing_local_run_is_rerun_then_compared_with_local_at_m(
        repo, ready, fake, lander_pass, tmp_path, configure):
    configure('ci = { workflows = ["ci.yml"], local = { command = "ci-local" } }')
    main_head = repo.rev("main")
    ready()
    fake.local_code, fake.local_codes[main_head] = 1, 0
    lander_pass()
    candidate = queue().running().candidate
    lander_pass()
    lander_pass()
    assert fake.locals == [candidate, candidate, main_head]
    assert events(tmp_path, ROUND, "queue.ejected")[-1]["failures"] == [LOCAL_FAILED]


def test_a_local_failure_main_has_too_lands_and_the_gate_accepts_it(
        repo, ready, fake, lander_pass, tmp_path, configure):
    configure('ci = { workflows = ["ci.yml"], local = { command = "ci-local" } }')
    ready()
    fake.local_code = 1
    for _ in range(3):
        lander_pass()
    assert queue().member(ROUND, "B1").state == queue_file.LANDED
    assert events(tmp_path, ROUND, ci.READ)[-1]["excused"] == [LOCAL_FAILED]
    assert events(tmp_path, ROUND, finishing.DONE)

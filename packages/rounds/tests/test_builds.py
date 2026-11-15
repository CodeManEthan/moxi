"""A build's records in the log, and its state read from git while it is off the target:
recorded ends, the head, and work told by trees."""

from __future__ import annotations

import pytest

from moxi_rounds import builds, log, state
from moxi_rounds.builds import BUILDING, DROPPED, EMPTY, FINISHED, GONE, UNCLEAR
from moxi_rounds.gitreads import Git


class Round:
    """A round's log in the test's tmp, written through the log's own append."""

    def __init__(self, folder):
        folder.mkdir(parents=True, exist_ok=True)
        self.path = log.round_log(folder)
        self.add("round.opened", description="a placeholder round", flow="full",
                 steps=["design", "build", "finish"])

    def add(self, type_: str, **fields) -> dict:
        return log.append(self.path, type_, fields, actor="planner")

    def commission(self, stage="B1", *, branch="work", base, target="main") -> None:
        self.add("build.commissioned", stage=stage, repo="proj-a", branch=branch,
                 base=base, target=target, builder="coder")

    def check(self, commit, merge_base, stage="B1") -> None:
        self.add("check.ran", stage=stage, step="check", commit=commit,
                 merge_base=merge_base, argv=["true"], exit_code=0, duration_s=1.0)

    def build(self, stage="B1") -> builds.Build:
        return builds.current(state.fold(log.read(self.path)), stage)


@pytest.fixture
def a_round(tmp_path) -> Round:
    return Round(tmp_path / "rounds" / "a-round")


def read(a_round, git_repo, stage="B1", **more) -> builds.Status:
    return builds.read(a_round.build(stage), Git(git_repo.root), **more)


# -- the records of a build ------------------------------------------------------------------

def test_a_stage_commissioned_again_after_a_drop_is_a_new_build_with_none_of_the_old_records(
        a_round):
    a_round.commission(base="a" * 40)
    a_round.check("b" * 40, "a" * 40)
    a_round.add("build.dropped", stage="B1", by="lead", record="comms:1234567")
    a_round.check("c" * 40, "a" * 40)
    a_round.commission(base="d" * 40)
    a_round.check("e" * 40, "d" * 40)
    first, second = builds.builds(state.fold(log.read(a_round.path)))
    assert first.ended == DROPPED
    assert [one.commit for one in first.readings] == ["b" * 40]
    assert second.ended is None and second.base == "d" * 40
    assert [one.commit for one in second.readings] == ["e" * 40]
    assert a_round.build() == second


def test_only_the_stage_s_own_records_are_its_readings(a_round):
    a_round.commission(base="a" * 40)
    a_round.commission("B2", branch="other", base="a" * 40)
    a_round.check("b" * 40, "a" * 40, stage="B2")
    a_round.add("verdict.recorded", kind="build", stage="B1", head="c" * 40,
                merge_base="a" * 40)
    a_round.add("finish.action", stage="B1", tip="f" * 40, id="merge", kind="merge",
                state="started")
    only = a_round.build()
    assert [(one.type, one.commit) for one in only.readings] == [
        ("verdict.recorded", "c" * 40)]
    assert only.tips == ("f" * 40,)
    assert only.commits() == ("c" * 40, "f" * 40)


def test_a_value_that_is_not_a_full_commit_id_is_no_reading(a_round):
    a_round.commission(base="a" * 40)
    a_round.check("HEAD", "a" * 40)
    a_round.check("b" * 40, "--all")
    only = a_round.build()
    assert [(one.commit, one.merge_base) for one in only.readings] == [("b" * 40, None)]


def test_a_reading_with_no_merge_base_is_not_off_target(a_round):
    a_round.commission(base="a" * 40)
    a_round.check("b" * 40, None)
    a_round.check("a" * 40, "a" * 40)
    a_round.check("c" * 40, "a" * 40)
    assert [one.off_target for one in a_round.build().readings] == [False, False, True]


def test_a_stage_never_commissioned_has_no_build(a_round):
    assert a_round.build("B3") is None


# -- recorded ends ----------------------------------------------------------------------------

@pytest.mark.parametrize(("event", "fields", "word"), [
    ("build.dropped", {"by": "lead", "record": "comms:1234567"}, DROPPED),
    ("finish.done", {"tip": "b" * 40}, FINISHED),
])
def test_a_recorded_end_gives_the_state_without_reading_git(a_round, tmp_path, event,
                                                           fields, word):
    a_round.commission(base="a" * 40)
    a_round.add(event, stage="B1", **fields)
    status = builds.read(a_round.build(), Git(tmp_path / "no-repo-here"))
    assert (status.state, status.by, status.head) == (word, builds.ENDED, None)


# -- off the target ---------------------------------------------------------------------------

def test_a_branch_with_work_the_target_lacks_reads_building(a_round, git_repo):
    base = git_repo.rev("main")
    a_round.commission(base=base)
    git_repo.switch("work", new_from="main")
    tip = git_repo.commit("feature.txt")
    status = read(a_round, git_repo)
    assert (status.state, status.head, status.merge_base) == (BUILDING, tip, base)


def test_a_branch_with_no_commit_of_its_own_is_on_the_target(a_round, git_repo):
    a_round.commission(base=git_repo.rev("main"))
    git_repo.switch("work", new_from="main")
    git_repo.switch("main")
    git_repo.commit("target-only.txt")
    status = read(a_round, git_repo)
    assert (status.state, status.by) == (None, builds.ON_TARGET)


def test_a_commit_and_its_revert_read_empty_with_commits_of_its_own(a_round, git_repo):
    a_round.commission(base=git_repo.rev("main"))
    git_repo.switch("work", new_from="main")
    git_repo.commit("feature.txt")
    git_repo.git("revert", "--no-edit", "HEAD")
    status = read(a_round, git_repo)
    assert (status.state, status.own_commits) == (EMPTY, True)


def test_the_target_merged_into_an_empty_branch_reads_empty_before_and_after_it_moves(
        a_round, git_repo):
    a_round.commission(base=git_repo.rev("main"))
    git_repo.switch("work", new_from="main")
    git_repo.switch("main")
    git_repo.commit("target-only.txt")
    git_repo.switch("work")
    git_repo.merge("main")
    before = read(a_round, git_repo)
    git_repo.switch("main")
    git_repo.commit("later.txt")
    after = read(a_round, git_repo)
    assert (before.state, before.own_commits) == (EMPTY, False)
    assert (after.state, after.own_commits) == (EMPTY, False)


def test_a_branch_and_a_target_with_slashes_in_their_names_are_read(a_round, git_repo):
    git_repo.git("branch", "release/1.0", "main")
    a_round.commission(branch="feature/x", base=git_repo.rev("release/1.0"),
                       target="release/1.0")
    git_repo.switch("feature/x", new_from="release/1.0")
    git_repo.commit("feature.txt")
    assert read(a_round, git_repo).state == BUILDING


def test_a_branch_fast_forwarded_onto_the_target_is_left_to_the_landing_rules(
        a_round, git_repo):
    a_round.commission(base=git_repo.rev("main"))
    git_repo.switch("work", new_from="main")
    tip = git_repo.commit("feature.txt")
    git_repo.switch("main")
    git_repo.git("merge", "-q", "--ff-only", "work")
    status = read(a_round, git_repo)
    assert (status.state, status.by, status.head) == (None, builds.ON_TARGET, tip)


def test_a_missing_target_branch_is_refused_by_name(a_round, git_repo):
    a_round.commission(base=git_repo.rev("main"), target="no-such-branch")
    with pytest.raises(builds.NoTarget, match="no-such-branch"):
        read(a_round, git_repo)


def test_a_git_variable_of_the_caller_does_not_steer_the_read(a_round, git_repo,
                                                             monkeypatch, tmp_path):
    a_round.commission(base=git_repo.rev("main"))
    git_repo.switch("work", new_from="main")
    git_repo.commit("feature.txt")
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "elsewhere"))
    assert read(a_round, git_repo).state == BUILDING


# -- a branch that is gone ----------------------------------------------------------------------

def test_a_deleted_branch_with_no_reading_reads_gone(a_round, git_repo):
    a_round.commission(base=git_repo.rev("main"))
    git_repo.switch("work", new_from="main")
    git_repo.commit("feature.txt")
    git_repo.switch("main")
    git_repo.git("merge", "-q", "--no-ff", "--no-edit", "work")
    git_repo.git("branch", "-D", "work")
    status = read(a_round, git_repo)
    assert (status.state, status.by) == (GONE, builds.NO_HEAD)


def test_a_deleted_unmerged_branch_with_a_reading_reads_gone(a_round, git_repo):
    base = git_repo.rev("main")
    a_round.commission(base=base)
    git_repo.switch("work", new_from="main")
    tip = git_repo.commit("feature.txt")
    a_round.check(tip, base)
    git_repo.switch("main")
    git_repo.git("branch", "-D", "work")
    status = read(a_round, git_repo)
    assert (status.state, status.by, status.head) == (GONE, builds.OFF_TARGET, tip)


def test_a_reading_git_no_longer_has_is_no_head(a_round, git_repo):
    a_round.commission(base=git_repo.rev("main"))
    a_round.check("b" * 40, git_repo.rev("main"))
    assert read(a_round, git_repo).state == GONE


def test_a_hand_merged_deleted_branch_is_read_at_what_was_merged(a_round, git_repo):
    base = git_repo.rev("main")
    a_round.commission(base=base)
    git_repo.switch("work", new_from="main")
    checked = git_repo.commit("feature.txt")
    a_round.check(checked, base)
    merged = git_repo.commit("after-the-check.txt")
    git_repo.switch("main")
    git_repo.commit("target-only.txt")
    git_repo.merge("work")
    git_repo.git("branch", "-D", "work")
    status = read(a_round, git_repo)
    assert (status.state, status.by, status.head) == (None, builds.ON_TARGET, merged)


def test_a_head_brought_in_by_a_landing_stays_the_recorded_one(a_round, git_repo):
    base = git_repo.rev("main")
    a_round.commission(base=base)
    git_repo.switch("work", new_from="main")
    checked = git_repo.commit("feature.txt")
    a_round.check(checked, base)
    git_repo.commit("after-the-check.txt")
    git_repo.switch("main")
    landing = git_repo.merge("work")
    git_repo.git("branch", "-D", "work")
    status = read(a_round, git_repo, landings={landing})
    assert (status.state, status.head) == (None, checked)


def test_an_octopus_merge_whose_two_other_parents_hold_the_head_reads_unclear(
        a_round, git_repo):
    base = git_repo.rev("main")
    a_round.commission(base=base)
    git_repo.switch("work", new_from="main")
    checked = git_repo.commit("feature.txt")
    a_round.check(checked, base)
    git_repo.switch("one", new_from="work")
    git_repo.commit("one.txt")
    git_repo.switch("two", new_from="work")
    git_repo.commit("two.txt")
    git_repo.switch("main")
    git_repo.git("merge", "-q", "--no-ff", "--no-edit", "one", "two")
    git_repo.git("branch", "-D", "work")
    status = read(a_round, git_repo)
    assert (status.state, status.by) == (UNCLEAR, builds.NO_ANSWER)


@pytest.mark.parametrize("newer_first", [False, True])
def test_the_head_of_a_deleted_branch_is_picked_by_ancestry_not_record_order(
        a_round, git_repo, newer_first):
    base = git_repo.rev("main")
    a_round.commission(base=base)
    git_repo.switch("work", new_from="main")
    older = git_repo.commit("feature.txt")
    newer = git_repo.commit("more.txt")
    for commit in ((newer, older) if newer_first else (older, newer)):
        a_round.check(commit, base)
    git_repo.switch("main")
    git_repo.git("branch", "-D", "work")
    status = read(a_round, git_repo)
    assert (status.state, status.head) == (GONE, newer)


@pytest.mark.parametrize("reset_first", [False, True])
def test_a_deleted_branch_with_readings_on_lines_that_parted_reads_unclear(
        a_round, git_repo, reset_first):
    base = git_repo.rev("main")
    a_round.commission(base=base)
    git_repo.switch("work", new_from="main")
    first = git_repo.commit("feature.txt")
    git_repo.git("reset", "-q", "--hard", base)
    second = git_repo.commit("other.txt")
    for commit in ((second, first) if reset_first else (first, second)):
        a_round.check(commit, base)
    git_repo.switch("main")
    git_repo.git("branch", "-D", "work")
    status = read(a_round, git_repo)
    assert (status.state, status.by) == (UNCLEAR, builds.PARTED)


# -- no single answer from git ------------------------------------------------------------------

def test_an_orphan_branch_reads_unclear(a_round, git_repo):
    a_round.commission(base=git_repo.rev("main"))
    git_repo.git("switch", "-q", "--orphan", "work")
    git_repo.commit("alone.txt")
    status = read(a_round, git_repo)
    assert (status.state, status.by) == (UNCLEAR, builds.NO_ANSWER)


def test_a_criss_cross_off_the_target_reads_unclear_until_the_target_is_merged_in(
        a_round, git_repo):
    a_round.commission(base=git_repo.rev("main"))
    git_repo.switch("work", new_from="main")
    git_repo.commit("branch-side.txt")
    git_repo.switch("main")
    git_repo.commit("target-side.txt")
    git_repo.switch("side", new_from="main")
    git_repo.merge("work")
    git_repo.switch("work")
    git_repo.merge("main")
    git_repo.switch("main")
    git_repo.git("merge", "-q", "--ff-only", "side")
    crossed = read(a_round, git_repo)
    git_repo.switch("work")
    git_repo.merge("main")
    after = read(a_round, git_repo)
    assert (crossed.state, crossed.by) == (UNCLEAR, builds.NO_ANSWER)
    assert after.state == EMPTY


# -- work told by trees -------------------------------------------------------------------------

def test_a_reading_shows_work_only_when_its_tree_differs_from_its_merge_base_s(
        a_round, git_repo):
    base = git_repo.rev("main")
    a_round.commission(base=base)
    git_repo.switch("work", new_from="main")
    work = git_repo.commit("feature.txt")
    git_repo.git("revert", "--no-edit", "HEAD")
    reverted = git_repo.rev()
    a_round.check(base, base)
    a_round.check(work, base)
    a_round.check(reverted, base)
    a_round.check("b" * 40, base)
    git = Git(git_repo.root)
    assert [builds.shows_work(git, one) for one in a_round.build().readings] == [
        False, True, False, False]


def test_a_deleted_branch_fast_forwarded_onto_the_target_is_read_at_its_recorded_head(
        a_round, git_repo):
    base = git_repo.rev("main")
    a_round.commission(base=base)
    git_repo.switch("work", new_from="main")
    checked = git_repo.commit("feature.txt")
    a_round.check(checked, base)
    git_repo.switch("main")
    git_repo.git("merge", "-q", "--ff-only", "work")
    git_repo.commit("later.txt")
    git_repo.git("branch", "-D", "work")
    status = read(a_round, git_repo)
    assert (status.state, status.by, status.head) == (None, builds.ON_TARGET, checked)

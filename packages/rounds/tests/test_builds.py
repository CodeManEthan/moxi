"""A build's records in the log, and its state read from git while it is off the target:
recorded ends, the head, and work told by trees."""

from __future__ import annotations

import pytest

from moxi_rounds import builds, log, state
from moxi_rounds.builds import BUILDING, DROPPED, EMPTY, FINISHED, GONE, MERGED, UNCLEAR
from moxi_rounds.gitreads import Git


class Round:
    """A round's log in the test's tmp, written through the log's own append."""

    def __init__(self, round_dir):
        round_dir.mkdir(parents=True, exist_ok=True)
        self.path = log.round_log(round_dir)
        self.add("round.opened", description="a placeholder round", flow="full",
                 steps=["design", "build", "finish"])

    def add(self, type_: str, **fields) -> dict:
        return log.append(self.path, type_, fields, by="planner")

    def commission(self, stage="B1", *, branch="work", base, target="main") -> None:
        self.add("build.commissioned", stage=stage, repo="proj-a", branch=branch,
                 base=base, target=target, builder="coder")

    def check(self, commit, merge_base, stage="B1") -> None:
        self.add("check.ran", stage=stage, step="check", commit=commit,
                 merge_base=merge_base, argv=["true"], exit_code=0, duration_s=1.0)

    def land(self, landing, *, before, tip, stage="B1", state_="ok") -> None:
        """Record a finish's merge for the build of `stage`."""
        self.add("finish.action", stage=stage, tip=tip, id="merge", kind="merge",
                 state=state_, landing=landing, target_before=before)

    def build(self, stage="B1") -> builds.Build:
        return builds.current(state.fold(log.read(self.path)), stage)


@pytest.fixture
def a_round(tmp_path) -> Round:
    return Round(tmp_path / "rounds" / "a-round")


def read(a_round, git_repo, stage="B1", **more) -> builds.Status:
    return builds.read(a_round.build(stage), Git(git_repo.root), **more)


def another(landing: str, *, before: str, tip: str, target="main") -> builds.Landing:
    """A landing on record for a build of another round."""
    return builds.Landing(stage="B9", build=7, n=9, repo="proj-a", target=target,
                          landing=landing, target_before=before, tip=tip)


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


def test_a_merge_made_or_found_by_a_finish_is_a_landing_of_its_build(a_round):
    a_round.commission(base="a" * 40, target="release")
    for state_, landing in (("started", "1"), ("failed", "2"), ("ok", "3"), ("found", "4")):
        a_round.add("finish.action", stage="B1", tip="b" * 40, id="merge", kind="merge",
                    state=state_, landing=landing * 40, target_before="a" * 40)
    a_round.add("finish.action", stage="B1", tip="b" * 40, id="push", kind="push",
                state="ok", landing="5" * 40)
    a_round.add("finish.action", stage="B1", tip="b" * 40, id="merge", kind="merge",
                state="ok", landing="not a commit", target_before="a" * 40)
    a_round.add("finish.action", stage="B1", tip="b" * 40, id="merge", kind="merge",
                state="ok", landing="6" * 40)
    landed = a_round.build().landings
    assert [(one.landing, one.target_before, one.tip, one.repo, one.target, one.build)
            for one in landed] == [("3" * 40, "a" * 40, "b" * 40, "proj-a", "release", 2),
                                   ("4" * 40, "a" * 40, "b" * 40, "proj-a", "release", 2)]
    assert builds.landings(state.fold(log.read(a_round.path))) == landed


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


def test_a_branch_with_no_commit_of_its_own_reads_empty_at_its_base(a_round, git_repo):
    a_round.commission(base=git_repo.rev("main"))
    git_repo.switch("work", new_from="main")
    git_repo.switch("main")
    git_repo.commit("target-only.txt")
    status = read(a_round, git_repo)
    assert (status.state, status.by, status.has_own_commits) == (EMPTY, builds.NOTHING_OWN,
                                                                 False)


def test_a_commit_and_its_revert_read_empty_with_commits_of_its_own(a_round, git_repo):
    a_round.commission(base=git_repo.rev("main"))
    git_repo.switch("work", new_from="main")
    git_repo.commit("feature.txt")
    git_repo.git("revert", "--no-edit", "HEAD")
    status = read(a_round, git_repo)
    assert (status.state, status.has_own_commits) == (EMPTY, True)


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
    assert (before.state, before.has_own_commits) == (EMPTY, False)
    assert (after.state, after.has_own_commits) == (EMPTY, False)


def test_a_branch_and_a_target_with_slashes_in_their_names_are_read(a_round, git_repo):
    git_repo.git("branch", "release/1.0", "main")
    a_round.commission(branch="feature/x", base=git_repo.rev("release/1.0"),
                       target="release/1.0")
    git_repo.switch("feature/x", new_from="release/1.0")
    git_repo.commit("feature.txt")
    assert read(a_round, git_repo).state == BUILDING


def test_the_target_fast_forwarded_onto_a_branch_with_no_reading_reads_unclear(
        a_round, git_repo):
    a_round.commission(base=git_repo.rev("main"))
    git_repo.switch("work", new_from="main")
    tip = git_repo.commit("feature.txt")
    git_repo.switch("main")
    git_repo.git("merge", "-q", "--ff-only", "work")
    status = read(a_round, git_repo)
    assert (status.state, status.by, status.head) == (UNCLEAR, builds.FAST_FORWARD, tip)


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
    assert (status.state, status.by, status.head) == (MERGED, builds.HAND_MERGE, merged)


def test_a_head_brought_in_by_another_build_s_landing_stays_the_recorded_one(a_round, git_repo):
    base = git_repo.rev("main")
    a_round.commission(base=base)
    git_repo.switch("work", new_from="main")
    checked = git_repo.commit("feature.txt")
    a_round.check(checked, base)
    tip = git_repo.commit("after-the-check.txt")
    git_repo.switch("main")
    landing = git_repo.merge("work")
    git_repo.git("branch", "-D", "work")
    status = read(a_round, git_repo, landings=[another(landing, before=base, tip=tip)])
    assert (status.state, status.by, status.head) == (EMPTY, builds.NOTHING_OWN, checked)


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
    assert (status.state, status.by, status.head, status.target_before) == (
        MERGED, builds.FAST_FORWARD, checked, base)


# -- on the target ------------------------------------------------------------------------------

def work_on(git_repo, name="feature.txt", branch="work") -> str:
    """Cut `branch` from main, commit `name` on it, and give its tip."""
    git_repo.switch(branch, new_from="main")
    return git_repo.commit(name)


def test_a_build_its_own_finish_merged_reads_merged_with_the_target_before_it(
        a_round, git_repo):
    base = git_repo.rev("main")
    a_round.commission(base=base)
    tip = work_on(git_repo)
    git_repo.switch("main")
    git_repo.commit("target-only.txt")
    before = git_repo.rev()
    landing = git_repo.merge("work")
    a_round.land(landing, before=before, tip=tip)
    git_repo.commit("later.txt")
    status = read(a_round, git_repo)
    assert (status.state, status.by, status.target_before) == (
        MERGED, builds.OWN_LANDING, before)


@pytest.mark.parametrize("newer_first", [False, True])
def test_of_two_landings_the_one_the_head_landed_under_is_read(a_round, git_repo,
                                                               newer_first):
    a_round.commission(base=git_repo.rev("main"))
    first_tip = work_on(git_repo)
    git_repo.switch("main")
    first_before = git_repo.rev()
    first = (git_repo.merge("work"), first_before, first_tip)
    git_repo.switch("work")
    second_tip = git_repo.commit("more.txt")
    git_repo.switch("main")
    git_repo.commit("target-only.txt")
    second_before = git_repo.rev()
    second = (git_repo.merge("work"), second_before, second_tip)
    for landing, before, tip in ((second, first) if newer_first else (first, second)):
        a_round.land(landing, before=before, tip=tip)
    status = read(a_round, git_repo)
    assert (status.state, status.by, status.target_before) == (
        MERGED, builds.OWN_LANDING, second_before)


def test_a_head_under_none_of_two_landings_reads_unclear(a_round, git_repo):
    a_round.commission(base=git_repo.rev("main"))
    tip = work_on(git_repo)
    git_repo.switch("main")
    before = git_repo.rev()
    landing = git_repo.merge("work")
    a_round.land(landing, before=before, tip=tip)
    a_round.land(landing, before=before, tip=tip, state_="found")
    status = read(a_round, git_repo)
    assert (status.state, status.by) == (UNCLEAR, builds.NO_LANDING)


def test_a_branch_moved_onto_a_finished_build_s_tip_reads_empty(a_round, git_repo):
    base = git_repo.rev("main")
    a_round.commission(base=base)
    other_tip = work_on(git_repo, branch="other")
    git_repo.git("branch", "-f", "work", other_tip)
    git_repo.switch("main")
    landing = git_repo.merge("other")
    status = read(a_round, git_repo,
                  landings=[another(landing, before=base, tip=other_tip)])
    assert (status.state, status.by) == (EMPTY, builds.NOTHING_OWN)


@pytest.mark.parametrize("fast_forward", [False, True])
def test_an_empty_branch_fast_forwarded_onto_other_builds_landings_reads_empty(
        a_round, git_repo, fast_forward):
    base = git_repo.rev("main")
    a_round.commission(base=base)
    git_repo.git("branch", "work", "main")
    other_tip = work_on(git_repo, branch="other")
    git_repo.switch("main")
    if fast_forward:
        git_repo.git("merge", "-q", "--ff-only", "other")
        landing = other_tip
    else:
        landing = git_repo.merge("other")
    git_repo.git("branch", "-f", "work", "main")
    status = read(a_round, git_repo,
                  landings=[another(landing, before=base, tip=other_tip)])
    assert (status.state, status.by) == (EMPTY, builds.NOTHING_OWN)


def test_an_empty_branch_fast_forwarded_onto_a_direct_commit_too_reads_unclear(
        a_round, git_repo):
    base = git_repo.rev("main")
    a_round.commission(base=base)
    git_repo.git("branch", "work", "main")
    other_tip = work_on(git_repo, branch="other")
    git_repo.switch("main")
    landing = git_repo.merge("other")
    git_repo.commit("straight-on-the-target.txt")
    git_repo.git("branch", "-f", "work", "main")
    status = read(a_round, git_repo,
                  landings=[another(landing, before=base, tip=other_tip)])
    assert (status.state, status.by) == (UNCLEAR, builds.FAST_FORWARD)


def test_a_reading_that_landed_under_another_build_s_landing_credits_nothing(
        a_round, git_repo):
    base = git_repo.rev("main")
    a_round.commission(base=base)
    other_tip = work_on(git_repo, branch="other")
    git_repo.git("branch", "-f", "work", other_tip)
    a_round.check(other_tip, base)
    git_repo.switch("main")
    git_repo.git("merge", "-q", "--ff-only", "other")
    git_repo.commit("straight-on-the-target.txt")
    git_repo.git("branch", "-f", "work", "main")
    status = read(a_round, git_repo,
                  landings=[another(other_tip, before=base, tip=other_tip)])
    assert (status.state, status.by) == (UNCLEAR, builds.FAST_FORWARD)


def test_the_target_fast_forwarded_onto_a_checked_branch_reads_merged_at_the_newest_x(
        a_round, git_repo):
    base = git_repo.rev("main")
    a_round.commission(base=base)
    first = work_on(git_repo)
    a_round.check(first, base)
    git_repo.switch("main")
    moved = git_repo.commit("target-only.txt")
    git_repo.switch("work")
    git_repo.merge("main")
    a_round.check(git_repo.rev(), moved)
    git_repo.switch("main")
    git_repo.git("merge", "-q", "--ff-only", "work")
    status = read(a_round, git_repo)
    assert (status.state, status.by, status.target_before) == (
        MERGED, builds.FAST_FORWARD, moved)


def test_a_hand_merge_that_changed_the_target_reads_merged_at_its_first_parent(
        a_round, git_repo):
    a_round.commission(base=git_repo.rev("main"))
    work_on(git_repo)
    git_repo.switch("main")
    before = git_repo.commit("target-only.txt")
    merge = git_repo.merge("work")
    status = read(a_round, git_repo)
    assert (status.state, status.by, status.target_before, status.hand_merge) == (
        MERGED, builds.HAND_MERGE, before, merge)


def test_a_hand_merge_reverted_later_still_reads_merged(a_round, git_repo):
    a_round.commission(base=git_repo.rev("main"))
    work_on(git_repo)
    git_repo.switch("main")
    git_repo.merge("work")
    git_repo.git("revert", "--no-edit", "-m", "1", "HEAD")
    assert read(a_round, git_repo).state == MERGED


def test_a_hand_merge_with_ours_reads_unclear(a_round, git_repo):
    a_round.commission(base=git_repo.rev("main"))
    work_on(git_repo)
    git_repo.switch("main")
    git_repo.merge("work", "-s", "ours")
    status = read(a_round, git_repo)
    assert (status.state, status.by) == (UNCLEAR, builds.HAND_MERGE)


def test_a_hand_merge_of_a_branch_that_changed_nothing_reads_empty(a_round, git_repo):
    a_round.commission(base=git_repo.rev("main"))
    work_on(git_repo)
    git_repo.git("revert", "--no-edit", "HEAD")
    git_repo.switch("main")
    git_repo.commit("target-only.txt")
    git_repo.merge("work")
    status = read(a_round, git_repo)
    assert (status.state, status.by) == (EMPTY, builds.HAND_MERGE)


def test_a_criss_cross_at_a_hand_merge_reads_unclear(a_round, git_repo):
    a_round.commission(base=git_repo.rev("main"))
    work_on(git_repo)
    git_repo.switch("main")
    git_repo.commit("target-side.txt")
    git_repo.switch("side", new_from="main")
    git_repo.merge("work")
    git_repo.switch("work")
    git_repo.merge("main")
    git_repo.switch("main")
    git_repo.git("merge", "-q", "--ff-only", "side")
    git_repo.merge("work")
    status = read(a_round, git_repo)
    assert (status.state, status.by) == (UNCLEAR, builds.NO_ANSWER)


def test_an_octopus_merge_of_a_live_branch_whose_two_other_parents_hold_it_reads_unclear(
        a_round, git_repo):
    a_round.commission(base=git_repo.rev("main"))
    work_on(git_repo)
    for name in ("one", "two"):
        git_repo.switch(name, new_from="work")
        git_repo.commit(f"{name}.txt")
    git_repo.switch("main")
    git_repo.git("merge", "-q", "--no-ff", "--no-edit", "one", "two")
    status = read(a_round, git_repo)
    assert (status.state, status.by) == (UNCLEAR, builds.NO_ANSWER)


def test_a_build_on_a_rewritten_target_reads_unclear(a_round, git_repo):
    git_repo.commit("dropped-later.txt")
    a_round.commission(base=git_repo.rev("main"))
    git_repo.git("reset", "-q", "--hard", "HEAD~1")
    git_repo.commit("rewritten.txt")
    git_repo.git("branch", "work", "main")
    status = read(a_round, git_repo)
    assert (status.state, status.by) == (UNCLEAR, builds.NO_ANSWER)


def test_a_branch_name_used_again_after_a_finish_is_a_new_build(a_round, git_repo):
    base = git_repo.rev("main")
    a_round.commission(base=base)
    tip = work_on(git_repo)
    git_repo.switch("main")
    landing = git_repo.merge("work")
    a_round.land(landing, before=base, tip=tip)
    a_round.add("finish.done", stage="B1", tip=tip)
    git_repo.git("branch", "-D", "work")
    a_round.commission("B2", base=landing)
    git_repo.git("branch", "work", "main")
    every = builds.landings(state.fold(log.read(a_round.path)))
    assert read(a_round, git_repo).state == FINISHED
    status = read(a_round, git_repo, "B2", landings=every)
    assert (status.state, status.by) == (EMPTY, builds.NOTHING_OWN)

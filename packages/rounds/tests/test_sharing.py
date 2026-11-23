"""Sharing between builds: each build that reads `building` or `merged` has a range, and
two builds of one repo and target share when their ranges have a commit in common."""

from __future__ import annotations

import pytest

from moxi_rounds import builds, log, state
from moxi_rounds.builds import BUILDING, EMPTY, FINISHED, MERGED
from moxi_rounds.gitreads import Git


class Round:
    """A round's log in the test's tmp, written through the log's own append."""

    def __init__(self, round_dir, name):
        round_dir.mkdir(parents=True, exist_ok=True)
        self.name = name
        self.path = log.round_log(round_dir)
        self.add("round.opened", description="a placeholder round", flow="full",
                 steps=["design", "build", "finish"])

    def add(self, type_: str, **fields) -> dict:
        return log.append(self.path, type_, fields, by="planner")

    def commission(self, stage, *, branch, base) -> None:
        self.add("build.commissioned", stage=stage, repo="proj-a", branch=branch,
                 base=base, target="main", builder="coder")

    def check(self, stage, commit, merge_base) -> None:
        self.add("check.ran", stage=stage, step="check", commit=commit,
                 merge_base=merge_base, argv=["true"], exit_code=0, duration_s=1.0)

    def build(self, stage) -> builds.Build:
        return builds.current(state.fold(log.read(self.path)), stage)


@pytest.fixture
def two_rounds(tmp_path):
    return (Round(tmp_path / "rounds" / "first", "proj-a/first"),
            Round(tmp_path / "rounds" / "second", "proj-a/second"))


def read_all(git_repo, *pairs, landings=()) -> dict:
    """Each `(round, stage)` read now, under the key `(round name, stage)`."""
    git = Git(git_repo.root)
    every = [*landings]
    for a_round, stage in pairs:
        every += a_round.build(stage).landings
    return {(a_round.name, stage): builds.read(a_round.build(stage), git, landings=every)
            for a_round, stage in pairs}


@pytest.mark.parametrize("moved_first", [False, True])
@pytest.mark.parametrize("given_backwards", [False, True])
def test_a_branch_moved_onto_another_build_s_tip_shares_with_it_from_both_sides(
        two_rounds, git_repo, moved_first, given_backwards):
    first, second = two_rounds
    base = git_repo.rev("main")
    moved, kept = (first, second) if moved_first else (second, first)
    moved.commission("B1", branch="work", base=base)
    kept.commission("B2", branch="other", base=base)
    git_repo.switch("work", new_from="main")
    moved.check("B1", git_repo.commit("own.txt"), base)
    git_repo.switch("other", new_from="main")
    other_tip = git_repo.commit("theirs.txt")
    kept.check("B2", other_tip, base)
    git_repo.git("branch", "-f", "work", other_tip)
    pairs = [(moved, "B1"), (kept, "B2")]
    statuses = read_all(git_repo, *(reversed(pairs) if given_backwards else pairs))
    shared = builds.sharing(Git(git_repo.root), statuses)
    assert [one.state for one in statuses.values()] == [BUILDING, BUILDING]
    assert shared == {(moved.name, "B1"): (((kept.name, "B2"), builds.OVERLAPS),),
                      (kept.name, "B2"): (((moved.name, "B1"), builds.OVERLAPS),)}


def test_once_the_other_build_finished_the_moved_branch_reads_empty_and_shares_nothing(
        two_rounds, git_repo):
    first, second = two_rounds
    base = git_repo.rev("main")
    first.commission("B1", branch="work", base=base)
    second.commission("B2", branch="other", base=base)
    git_repo.switch("other", new_from="main")
    other_tip = git_repo.commit("theirs.txt")
    second.check("B2", other_tip, base)
    git_repo.git("branch", "-f", "work", other_tip)
    git_repo.switch("main")
    landing = git_repo.merge("other")
    second.add("finish.action", stage="B2", tip=other_tip, id="merge", kind="merge",
               state="ok", landing=landing, target_before=base)
    second.add("finish.done", stage="B2", tip=other_tip)
    statuses = read_all(git_repo, (first, "B1"), (second, "B2"))
    assert [one.state for one in statuses.values()] == [EMPTY, FINISHED]
    assert builds.sharing(Git(git_repo.root), statuses) == {
        ("proj-a/first", "B1"): (), ("proj-a/second", "B2"): ()}


def test_builds_with_work_of_their_own_share_nothing(two_rounds, git_repo):
    first, second = two_rounds
    base = git_repo.rev("main")
    first.commission("B1", branch="work", base=base)
    second.commission("B1", branch="other", base=base)
    git_repo.switch("work", new_from="main")
    git_repo.commit("own.txt")
    git_repo.switch("other", new_from="main")
    git_repo.commit("theirs.txt")
    statuses = read_all(git_repo, (first, "B1"), (second, "B1"))
    assert set(builds.sharing(Git(git_repo.root), statuses).values()) == {()}


def test_two_builds_merged_by_one_hand_merge_share_what_both_ranges_hold(
        two_rounds, git_repo):
    first, second = two_rounds
    base = git_repo.rev("main")
    first.commission("B1", branch="work", base=base)
    second.commission("B1", branch="other", base=base)
    git_repo.switch("work", new_from="main")
    git_repo.commit("own.txt")
    git_repo.switch("other", new_from="work")
    git_repo.commit("theirs.txt")
    git_repo.switch("main")
    before = git_repo.commit("target-only.txt")
    git_repo.merge("other")
    statuses = read_all(git_repo, (first, "B1"), (second, "B1"))
    assert [(one.state, one.target_before) for one in statuses.values()] == [
        (MERGED, before), (MERGED, before)]
    shared = builds.sharing(Git(git_repo.root), statuses)
    assert shared[("proj-a/first", "B1")] == ((("proj-a/second", "B1"), builds.INSIDE),)
    assert shared[("proj-a/second", "B1")] == ((("proj-a/first", "B1"), builds.CONTAINS),)


def test_a_branch_stacked_on_work_already_on_the_target_shares_none_of_it(
        two_rounds, git_repo):
    first, second = two_rounds
    base = git_repo.rev("main")
    first.commission("B1", branch="work", base=base)
    second.commission("B1", branch="other", base=base)
    git_repo.switch("work", new_from="main")
    git_repo.commit("own.txt")
    git_repo.switch("main")
    git_repo.commit("target-only.txt")
    git_repo.merge("work")
    git_repo.switch("other", new_from="work")
    git_repo.commit("theirs.txt")
    statuses = read_all(git_repo, (first, "B1"), (second, "B1"))
    assert [one.state for one in statuses.values()] == [MERGED, BUILDING]
    assert set(builds.sharing(Git(git_repo.root), statuses).values()) == {()}


def test_only_a_building_or_merged_build_has_a_range(git_repo):
    git = Git(git_repo.root)
    main = git_repo.rev("main")
    git_repo.switch("work", new_from="main")
    tip = git_repo.commit("own.txt")
    for state_ in (builds.EMPTY, builds.UNCLEAR, builds.GONE):
        status = builds.Status(state_, builds.OFF_TARGET, "", head=tip, target_head=main,
                               target_before=main)
        assert builds.span(git, status) == frozenset()
    for state_ in (builds.DROPPED, builds.FINISHED):
        assert builds.span(git, builds.Status(state_, builds.ENDED, "")) == frozenset()
    status = builds.Status(BUILDING, builds.OFF_TARGET, "", head=tip, target_head=main,
                           target_before=main)
    assert builds.span(git, status) == frozenset({tip})


# -- the relation of two ranges, and the order it sets ------------------------------------

@pytest.mark.parametrize("own, theirs, said", [
    ({"a"}, {"a", "b"}, builds.INSIDE),
    ({"a", "b"}, {"a"}, builds.CONTAINS),
    ({"a", "b"}, {"b", "c"}, builds.OVERLAPS),
    ({"a", "b"}, {"a", "b"}, builds.OVERLAPS),
    ({"a"}, {"b"}, None),
    (set(), {"b"}, None),
])
def test_two_ranges_stand_inside_contains_or_overlap(own, theirs, said):
    assert builds.relation(frozenset(own), frozenset(theirs)) == said


@pytest.mark.parametrize("relation, merged, other_merged, said", [
    (builds.INSIDE, False, False, builds.FIRST),
    (builds.INSIDE, True, True, builds.HELD_FOR_LANDING),
    (builds.INSIDE, False, True, builds.HELD_FOR_LANDING),
    (builds.CONTAINS, False, False, builds.HELD_FOR_WORK),
    (builds.CONTAINS, False, True, builds.HELD_FOR_WORK),
    (builds.CONTAINS, True, False, builds.FIRST),
    (builds.CONTAINS, True, True, builds.FIRST),
    (builds.OVERLAPS, False, False, builds.REFUSED),
    (builds.OVERLAPS, True, True, builds.REFUSED),
])
def test_the_order_of_two_builds_follows_their_relation_and_which_reads_merged(
        relation, merged, other_merged, said):
    share = builds.Share("proj-a/b", "B2", relation, merged=other_merged)
    assert builds.order(share, merged=merged) == said


def test_each_share_is_worded_by_its_relation_and_overlaps_are_named_together():
    shares = [("proj-a/b", "B1", builds.INSIDE), ("proj-a/c", "B1", builds.OVERLAPS),
              ("proj-a/d", "B2", builds.CONTAINS), ("proj-a/e", "B1", builds.OVERLAPS)]
    assert builds.share_words(shares, merged=False) == (
        "inside proj-a/b B1 (finishes first), contains proj-a/d B2 (held until its "
        "work is on the target), shares with proj-a/c B1, proj-a/e B1")
    assert builds.share_words(shares[:1] + shares[2:3], merged=True) == (
        "inside proj-a/b B1 (held until its finish records the landing), contains "
        "proj-a/d B2 (finishes first)")
    assert builds.share_words([], merged=False) == ""


def test_a_branch_stacked_on_another_build_s_branch_is_inside_it(two_rounds, git_repo):
    first, second = two_rounds
    base = git_repo.rev("main")
    first.commission("B1", branch="inner", base=base)
    second.commission("B1", branch="outer", base=base)
    git_repo.switch("inner", new_from="main")
    git_repo.commit("inner.txt")
    git_repo.switch("outer", new_from="inner")
    git_repo.commit("outer.txt")
    statuses = read_all(git_repo, (first, "B1"), (second, "B1"))
    assert [one.state for one in statuses.values()] == [BUILDING, BUILDING]
    assert builds.sharing(Git(git_repo.root), statuses) == {
        (first.name, "B1"): (((second.name, "B1"), builds.INSIDE),),
        (second.name, "B1"): (((first.name, "B1"), builds.CONTAINS),)}

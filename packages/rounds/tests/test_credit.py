"""The operator's credit of a fast-forward: `rule ROUND credit:B<n>` is taken only for a
build unclear because its target was fast-forwarded with no reading, whose head's tree
differs from its base's; the credited build reads merged with its base as the target
before it, and reads unclear again once the credit is taken back."""

from __future__ import annotations

import pytest

from moxi_rounds import builds, log, opening, ruling, showing, state
from moxi_rounds.errors import BadArgument, Refused
from moxi_rounds.gitreads import Git

ROUND = "proj-a/a-round"


@pytest.fixture
def a_round(git_repo, tmp_path, capsys):
    """`proj-a` rooted at the test's git repo, its round open in the `fix` flow with B1
    on the branch `work` commissioned at `main`'s head through the log. Returns the
    round's log."""
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


def fast_forward(git_repo, *, revert=False) -> str:
    """Commit on `work`, fast-forward `main` onto it, and give the tip."""
    git_repo.switch("work", new_from="main")
    git_repo.commit("feature.txt")
    if revert:
        git_repo.git("revert", "--no-edit", "HEAD")
    git_repo.switch("main")
    git_repo.git("merge", "-q", "--ff-only", "work")
    return git_repo.rev("work")


def status_now(path, git_repo) -> builds.Status:
    return builds.read(builds.current(state.fold(log.read(path)), "B1"), Git(git_repo.root))


def credit(**more):
    return ruling.rule(ROUND, item="credit:B1", record="comms:1234567", by="planner",
                       **more)


def refused(path) -> str:
    before = log.read(path)
    with pytest.raises(Refused) as caught:
        credit()
    assert log.read(path) == before
    return str(caught.value)


def test_a_fast_forward_with_no_reading_is_credited_and_reads_merged_at_its_base(
        a_round, git_repo):
    base = git_repo.rev("main")
    tip = fast_forward(git_repo)
    assert status_now(a_round, git_repo).by == builds.FAST_FORWARD

    done = credit()

    commissioned = builds.current(state.fold(log.read(a_round)), "B1").n
    assert {key: done.event[key] for key in ("rules", "kind", "build", "head")} == {
        "rules": "credit:B1", "kind": ruling.PROCESS, "build": commissioned,
        "head": tip}
    after = status_now(a_round, git_repo)
    assert (after.state, after.by, after.target_before) == (
        builds.MERGED, builds.FAST_FORWARD, base)


def test_a_credit_taken_back_leaves_the_build_unclear_again(a_round, git_repo):
    fast_forward(git_repo)
    done = credit()

    ruling.rule(ROUND, item=f"revoke:{done.id}", record="comms:7654321", by="planner")

    assert status_now(a_round, git_repo).state == builds.UNCLEAR


def test_a_credited_build_is_not_credited_again_or_replaced(a_round, git_repo):
    fast_forward(git_repo)
    done = credit()

    assert "B1's build is not credited: it reads merged" in refused(a_round)
    with pytest.raises(BadArgument):
        credit(supersedes=done.id)


def test_a_fast_forward_whose_head_holds_the_base_tree_is_not_credited(a_round,
                                                                        git_repo):
    fast_forward(git_repo, revert=True)
    assert status_now(a_round, git_repo).by == builds.FAST_FORWARD

    assert "so there is no work to credit" in refused(a_round)


def test_a_hand_merge_that_changed_nothing_is_not_credited(a_round, git_repo):
    git_repo.switch("work", new_from="main")
    git_repo.commit("feature.txt")
    git_repo.switch("main")
    git_repo.merge("work", "-s", "ours")

    said = refused(a_round)
    assert "only a fast-forward with no reading is credited" in said


def test_a_build_on_a_rewritten_target_is_not_credited(a_round, git_repo):
    git_repo.git("switch", "-q", "--orphan", "fresh")
    git_repo.commit("rewritten.txt")
    git_repo.git("branch", "-f", "main", "fresh")
    git_repo.git("branch", "work", "main")
    assert status_now(a_round, git_repo).by == builds.NO_ANSWER

    assert "only a fast-forward with no reading is credited" in refused(a_round)


@pytest.mark.parametrize("history", ["building", "gone"])
def test_a_build_in_another_state_is_not_credited(a_round, git_repo, history):
    if history == "building":
        git_repo.switch("work", new_from="main")
        git_repo.commit("feature.txt")
        git_repo.switch("main")

    assert (f"B1's build is not credited: it reads {history}, and only an unclear "
            "build is credited") in refused(a_round)


def test_a_stage_with_no_open_build_is_not_credited(a_round):
    log.append(a_round, "build.dropped", {"stage": "B1", "by": "lead",
                                          "record": "comms:1234567", "tips": []},
               by="planner")

    assert "B1 has no open build to credit" in refused(a_round)


def test_show_names_the_credit_and_the_drop_of_an_unclear_fast_forward(a_round, git_repo,
                                                                       capsys):
    fast_forward(git_repo)

    [row] = showing.data(showing.show(ROUND))["builds"]

    assert row["credit"] == f"moxi rounds rule {ROUND} credit:B1 --record PTR"
    assert row["drop"] == f"moxi rounds drop {ROUND} B1 --by operator --record PTR"
    assert showing.drop_lines(row)[1:] == [
        f"the operator credits it: {row['credit']}",
        f"the operator drops it: {row['drop']}"]

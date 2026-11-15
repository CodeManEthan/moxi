"""Finding a build from its worktree: the repo and the branch checked out there name the
one open build, and `next` with no round prints that build's round; each way no single
build is found is refused with the command that names the round."""

from __future__ import annotations

import pytest

from moxi_rounds import by_branch, commissioning, log, opening
from moxi_rounds.cli import main
from moxi_rounds.errors import BadArgument

ROUND = "proj-a/a-round"
OTHER = "proj-a/b-round"
INSTEAD = "moxi rounds next <project>/<slug>"


@pytest.fixture
def repo(git_repo, tmp_path):
    """The project `proj-a`, whose root is the test's git repo with `main` checked out,
    keeping its rounds under a notes folder of its own. Returns the repo."""
    from moxi import cli

    assert cli.main(["projects", "add", "proj-a", "--root", str(git_repo.root)]) == 0
    assert cli.main(["projects", "set", "proj-a", "--notes-root",
                     str(tmp_path / "notes")]) == 0
    return git_repo


@pytest.fixture
def opened(repo, tmp_path, capsys):
    """`opened(name, stages)`: open the round on the `fix` flow with each `(id,
    branch)` of `stages` declared through the log, and return its log."""

    def open_on(name=ROUND, stages=(("B1", "work"),)):
        opening.open_round(name, description="a placeholder round", by="planner",
                           flow="fix")
        path = log.round_log(tmp_path / "notes" / "rounds" / name.split("/")[1])
        for stage_id, branch in stages:
            log.append(path, "stage.declared",
                       {"id": stage_id, "repo": "proj-a", "branch": branch, "after": []},
                       by="planner")
        capsys.readouterr()
        return path

    return open_on


def commission(name=ROUND, stage="B1"):
    return commissioning.commission(name, stage, builder="coder", by="planner")


def refusal(path) -> str:
    with pytest.raises(BadArgument) as caught:
        by_branch.find(path, verb="next")
    assert caught.value.fix.endswith(INSTEAD)
    return caught.value.reason


# -- found -------------------------------------------------------------------------------

def test_next_with_no_round_in_a_worktree_prints_the_round_of_the_build_there(
        opened, monkeypatch, capsys):
    opened()
    made = commission()
    monkeypatch.chdir(made.worktree)
    capsys.readouterr()

    assert main(["next"]) == 0
    by_name = capsys.readouterr().out
    assert main(["next", ROUND]) == 0

    assert by_name == capsys.readouterr().out
    assert by_name.startswith(f"round {ROUND}   flow fix")


def test_next_json_with_no_round_gives_the_same_data_as_naming_it(
        opened, monkeypatch, capsys):
    opened()
    made = commission()
    monkeypatch.chdir(made.worktree)
    capsys.readouterr()

    assert main(["next", "--json"]) == 0
    by_name = capsys.readouterr().out
    assert main(["next", ROUND, "--json"]) == 0

    assert by_name == capsys.readouterr().out


def test_a_build_is_found_from_a_dir_inside_its_worktree(opened):
    opened()
    made = commission()
    inside = made.worktree / "docs" / "deeper"
    inside.mkdir(parents=True)

    assert by_branch.find(inside, verb="next") == by_branch.Found(
        ROUND, "B1", "proj-a", "work")


def test_the_branch_checked_out_picks_the_build_among_rounds(opened):
    opened(ROUND, (("B1", "work"),))
    opened(OTHER, (("B1", "other-work"),))
    commission(ROUND, "B1")
    made = commission(OTHER, "B1")

    assert by_branch.find(made.worktree, verb="next") == by_branch.Found(
        OTHER, "B1", "proj-a", "other-work")


# -- refused -----------------------------------------------------------------------------

def test_with_no_round_outside_any_git_work_tree_it_is_refused(tmp_path, monkeypatch,
                                                                capsys):
    outside = tmp_path / "plain"
    outside.mkdir()
    monkeypatch.chdir(outside)

    assert main(["next"]) == 2

    err = capsys.readouterr().err
    assert "is in no git work tree" in err
    assert INSTEAD in err


def test_a_worktree_with_a_detached_head_is_refused(opened, repo):
    opened()
    made = commission()
    repo.git("-C", str(made.worktree), "switch", "-q", "--detach")

    assert "detached head" in refusal(made.worktree)


def test_a_repo_in_no_project_is_refused(git_repo, tmp_path):
    loose = tmp_path / "loose"
    git_repo.git("clone", "-q", str(git_repo.root), str(loose))

    assert "is in no project of the projects list" in refusal(loose)


def test_a_branch_with_no_open_build_is_refused_naming_repo_and_branch(opened, repo):
    opened()
    commission()

    reason = refusal(repo.root)

    assert "no open build has repo proj-a and branch main" in reason


def test_a_finished_build_is_not_found(opened, repo):
    path = opened()
    made = commission()
    log.append(path, "finish.done", {"stage": "B1", "tip": repo.rev("work")},
               by="coder")

    assert "no open build has repo proj-a and branch work" in refusal(made.worktree)


def test_two_open_builds_on_one_branch_are_refused_naming_both(opened):
    first = opened()
    made = commission()
    other = opened(OTHER, (("B3", "work"),))
    [event] = [one for one in log.read(first) if one["type"] == "build.commissioned"]
    copied = {key: value for key, value in event.items()
              if key not in ("v", "n", "at", "actor", "type")}
    log.append(other, "build.commissioned", {**copied, "stage": "B3"}, by="planner")

    reason = refusal(made.worktree)

    assert "more than one open build has repo proj-a and branch work" in reason
    assert f"{ROUND} B1" in reason and f"{OTHER} B3" in reason

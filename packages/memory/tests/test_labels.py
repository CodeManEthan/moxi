"""Project labels: the nearest folder holding `.git`, a worktree's main repository, else
the cwd's own name."""

import os
import shutil
import subprocess

import pytest

from moxi_memory.labels import cwd_exists, project_label


def test_the_nearest_dir_holding_git_names_the_project(tmp_path):
    repo = tmp_path / "alpha"
    (repo / ".git").mkdir(parents=True)
    deep = repo / "src" / "pkg"
    deep.mkdir(parents=True)
    assert project_label(repo) == "alpha"
    assert project_label(deep) == "alpha"
    assert project_label(str(deep)) == "alpha"


def test_the_nearest_git_wins_over_one_further_up(tmp_path):
    (tmp_path / "outer" / ".git").mkdir(parents=True)
    inner = tmp_path / "outer" / "vendor" / "inner"
    (inner / ".git").mkdir(parents=True)
    assert project_label(inner / "docs") == "inner"


def test_with_no_git_above_the_label_is_the_cwds_own_name(tmp_path):
    plain = tmp_path / "scratchpad" / "notes"
    plain.mkdir(parents=True)
    assert project_label(plain) == "notes"


def test_a_worktree_takes_its_main_repositorys_name(tmp_path):
    main = tmp_path / "beta"
    gitdir = main / ".git" / "worktrees" / "feature"
    gitdir.mkdir(parents=True)
    (gitdir / "commondir").write_text("../..\n")
    tree = tmp_path / "elsewhere" / "beta-feature"
    tree.mkdir(parents=True)
    (tree / ".git").write_text(f"gitdir: {gitdir}\n")
    assert project_label(tree) == "beta"
    assert project_label(tree / "sub") == "beta"


def test_a_worktree_with_a_relative_gitdir_takes_its_main_repositorys_name(tmp_path):
    main = tmp_path / "gamma"
    gitdir = main / ".git" / "worktrees" / "wt"
    gitdir.mkdir(parents=True)
    (gitdir / "commondir").write_text("../..\n")
    tree = tmp_path / "gamma-wt"
    tree.mkdir()
    (tree / ".git").write_text(f"gitdir: {os.path.relpath(gitdir, tree)}\n")
    assert project_label(tree) == "gamma"


def test_a_worktree_of_a_bare_repository_takes_the_repositorys_name(tmp_path):
    bare = tmp_path / "delta.git"
    gitdir = bare / "worktrees" / "main"
    gitdir.mkdir(parents=True)
    (gitdir / "commondir").write_text("../..\n")
    tree = tmp_path / "checkout"
    tree.mkdir()
    (tree / ".git").write_text(f"gitdir: {gitdir}\n")
    assert project_label(tree) == "delta"


@pytest.mark.parametrize("content", ["", "not a gitdir line\n", "gitdir: /no/such/dir\n"])
def test_a_git_file_that_leads_nowhere_names_its_own_dir(tmp_path, content):
    sub_dir = tmp_path / "submodule"
    sub_dir.mkdir()
    (sub_dir / ".git").write_text(content)
    assert project_label(sub_dir / "x") == "submodule"


def test_a_real_git_worktree_gets_its_repositorys_label(tmp_path):
    git = shutil.which("git")
    if git is None:
        pytest.skip("git is not installed")
    main = tmp_path / "epsilon"
    main.mkdir()
    env = {**os.environ, "GIT_CONFIG_NOSYSTEM": "1"}

    def run(*args, cwd=main):
        subprocess.run([git, "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                        "-c", "init.defaultBranch=main", *args], cwd=cwd, env=env,
                       check=True, capture_output=True)

    run("init", "-q")
    run("commit", "-q", "--allow-empty", "-m", "first")
    tree = tmp_path / "trees" / "epsilon-topic"
    run("worktree", "add", "-q", "-b", "topic", str(tree))
    assert project_label(tree) == "epsilon"
    assert project_label(main) == "epsilon"


def test_a_cwd_that_is_gone_still_gets_a_label_from_what_is_left(tmp_path):
    (tmp_path / "zeta" / ".git").mkdir(parents=True)
    gone = tmp_path / "zeta" / "removed" / "deeper"
    assert not cwd_exists(gone)
    assert project_label(gone) == "zeta"
    assert cwd_exists(tmp_path / "zeta")


@pytest.mark.parametrize("cwd", [None, "", "relative/path", "/"])
def test_no_label_for_an_empty_relative_or_root_cwd(cwd):
    assert project_label(cwd) is None

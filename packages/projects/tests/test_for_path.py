"""Path lookup: which project a directory is in.

The trees are written by hand in tmp, `.git` and `commondir` files included, so no git is
needed; one test in the slow tier makes a real worktree.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from moxi_projects import api, store

POSIX = pytest.mark.skipif(sys.platform == "win32", reason="POSIX symlinks and paths")


def register(*rows) -> None:
    """Add projects `(key, root)` to the test's store."""
    def add(conn):
        for key, root in rows:
            store.insert(conn, key=key, root=str(root))

    store.write(add)


def key_for(path) -> str | None:
    found = api.for_path(str(path))
    return found.key if found else None


def make_main_repo(main: Path) -> Path:
    """A main work tree with a `.git` directory; returns its git dir."""
    git = main / ".git"
    git.mkdir(parents=True)
    (git / "HEAD").write_text("ref: refs/heads/main\n")
    return git


def make_worktree(main: Path, tree: Path, name: str, *, relative: bool = False) -> None:
    """A linked worktree `tree` of `main`, written as git writes one."""
    gitdir = main / ".git" / "worktrees" / name
    gitdir.mkdir(parents=True)
    (gitdir / "commondir").write_text("../..\n")
    (gitdir / "gitdir").write_text(f"{tree / '.git'}\n")
    tree.mkdir(parents=True, exist_ok=True)
    target = os.path.relpath(gitdir, tree) if relative else str(gitdir)
    (tree / ".git").write_text(f"gitdir: {target}\n")


# --- by string ---------------------------------------------------------------------------

def test_a_path_at_or_under_a_root_finds_it(tmp_path):
    root = tmp_path / "proj"
    (root / "src" / "pkg").mkdir(parents=True)
    register(("proj", root))
    assert key_for(root) == "proj"
    assert key_for(root / "src" / "pkg") == "proj"
    assert key_for(tmp_path) is None


def test_a_relative_path_is_taken_from_the_working_directory(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    (root / "src").mkdir(parents=True)
    register(("proj", root))
    monkeypatch.chdir(root)
    assert key_for("src") == "proj"
    assert key_for(".") == "proj"


def test_the_deepest_root_wins(tmp_path):
    outer = tmp_path / "outer"
    inner = outer / "packages" / "inner"
    (inner / "src").mkdir(parents=True)
    register(("outer", outer), ("inner", inner))
    assert key_for(inner / "src") == "inner"
    assert key_for(outer / "packages") == "outer"


def test_a_path_that_does_not_exist_still_compares_by_string(tmp_path):
    root = tmp_path / "not-cloned-yet"
    register(("later", root))
    assert key_for(root / "branch-only" / "dir") == "later"


def test_no_rows_answers_none_without_touching_the_path(store_path, monkeypatch):
    calls = []
    true_stat = os.stat
    monkeypatch.setattr(os, "stat", lambda p, *a, **k: calls.append(os.fspath(p))
                        or true_stat(p, *a, **k))
    assert api.for_path("/some/where") is None
    assert [c for c in calls if c.startswith("/some")] == []


# --- symlinks, case and identity -----------------------------------------------------------

@POSIX
def test_a_root_registered_through_a_symlink_is_found_by_its_real_path(tmp_path):
    real = tmp_path / "real"
    (real / "src").mkdir(parents=True)
    link = tmp_path / "link"
    link.symlink_to(real)
    register(("proj", link))
    assert key_for(real / "src") == "proj"
    assert key_for(link / "src") == "proj"


@POSIX
def test_a_symlinked_parent_of_the_queried_path_is_followed(tmp_path):
    root = tmp_path / "proj"
    (root / "src").mkdir(parents=True)
    alias = tmp_path / "alias"
    alias.symlink_to(tmp_path)
    register(("proj", root))
    assert key_for(alias / "proj" / "src") == "proj"


@POSIX
def test_a_symlinked_root_that_now_points_elsewhere_is_not_matched_by_its_old_target(
        tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    (first / "src").mkdir(parents=True)
    second.mkdir()
    link = tmp_path / "link"
    link.symlink_to(first)
    register(("proj", link))
    link.unlink()
    link.symlink_to(second)
    assert key_for(first / "src") is None
    assert key_for(link) == "proj"


@POSIX
def test_two_roots_naming_one_directory_tie_to_the_lower_key(tmp_path):
    real = tmp_path / "real"
    (real / "src").mkdir(parents=True)
    alias = tmp_path / "alias"
    alias.symlink_to(real)
    register(("zed", real), ("abc", alias))
    assert key_for(real / "src") == "abc"
    assert key_for(alias / "src") == "abc"


def _fold_identity(monkeypatch, tree: Path):
    """Identities as a case-insensitive disk gives them, for paths under `tree`."""
    real = api._identity

    def identity(path):
        if Path(path).as_posix().casefold().startswith(tree.as_posix().casefold()):
            return (1, hash(os.path.normcase(path).casefold()))
        return real(path)

    monkeypatch.setattr(api, "_identity", identity)


def test_a_case_only_spelling_matches_when_identity_confirms_it(tmp_path, monkeypatch):
    root = tmp_path / "Proj"
    (root / "src").mkdir(parents=True)
    register(("proj", root))
    _fold_identity(monkeypatch, tmp_path)
    assert key_for(tmp_path / "proj" / "src") == "proj"


def test_a_case_only_spelling_without_identity_does_not_match(tmp_path):
    root = tmp_path / "Proj"
    (root / "src").mkdir(parents=True)
    register(("proj", root))
    if os.path.exists(tmp_path / "proj"):
        pytest.skip("a case-insensitive disk: the other spelling exists")
    assert key_for(tmp_path / "proj" / "src") is None


@POSIX
def test_a_root_without_an_inode_number_has_no_identity(tmp_path, monkeypatch):
    real = tmp_path / "real"
    (real / "src").mkdir(parents=True)
    link = tmp_path / "link"
    link.symlink_to(real)
    register(("proj", link))
    true_stat = os.stat

    class NoInode:
        def __init__(self, st):
            self.st = st
            self.st_ino = 0

        def __getattr__(self, name):
            return getattr(self.st, name)

    monkeypatch.setattr(os, "stat", lambda p, *a, **k: NoInode(true_stat(p, *a, **k)))
    assert api._identity(str(real)) is None
    # Matched by the resolved spelling, with no identity to say otherwise.
    assert key_for(real / "src") == "proj"


@POSIX
def test_a_lookup_touches_no_root_that_is_not_a_candidate(tmp_path, monkeypatch):
    here = tmp_path / "here"
    (here / "deep" / "er").mkdir(parents=True)
    away = [tmp_path / "away" / f"p{n}" for n in range(5)]
    for path in away:
        path.mkdir(parents=True)
    unreachable = Path("/nonexistent-nas/share/proj")
    link = tmp_path / "linked-away"
    link.symlink_to(away[0])
    register(("here", here), ("unreachable", unreachable), ("linked", link),
             *((f"away{n}", p) for n, p in enumerate(away)))
    touched = []
    true_stat, true_lstat = os.stat, os.lstat

    def watch(fn):
        def wrapped(path, *args, **kwargs):
            touched.append(os.fspath(path))
            return fn(path, *args, **kwargs)
        return wrapped

    monkeypatch.setattr(os, "stat", watch(true_stat))
    monkeypatch.setattr(os, "lstat", watch(true_lstat))
    assert key_for(here / "deep" / "er") == "here"
    for path in touched:
        for root in (*away, unreachable, link):
            assert not (path == str(root) or path.startswith(str(root) + os.sep)), path


# --- git work trees ----------------------------------------------------------------------

def test_a_linked_worktree_outside_the_root_finds_its_main_projects(tmp_path):
    main = tmp_path / "repos" / "main"
    make_main_repo(main)
    tree = tmp_path / "scratch" / "build-x"
    make_worktree(main, tree, "build-x")
    (tree / "src" / "pkg").mkdir(parents=True)
    register(("main", main))
    assert key_for(tree / "src" / "pkg") == "main"
    assert key_for(tree) == "main"


def test_a_relative_gitdir_line_is_read_relative_to_the_file(tmp_path):
    main = tmp_path / "repos" / "main"
    make_main_repo(main)
    tree = tmp_path / "scratch" / "rel"
    make_worktree(main, tree, "rel", relative=True)
    register(("main", main))
    assert key_for(tree) == "main"


def test_a_worktree_maps_to_the_nested_project_at_its_place(tmp_path):
    main = tmp_path / "repo"
    make_main_repo(main)
    (main / "packages" / "inner").mkdir(parents=True)
    tree = tmp_path / "wt"
    make_worktree(main, tree, "wt")
    register(("repo", main), ("inner", main / "packages" / "inner"))
    assert key_for(tree / "packages" / "inner" / "src") == "inner"
    assert key_for(tree / "docs") == "repo"


def test_a_registered_worktree_wins_over_its_main_project(tmp_path):
    main = tmp_path / "repo"
    make_main_repo(main)
    tree = tmp_path / "wt"
    make_worktree(main, tree, "wt")
    register(("repo", main), ("wt", tree))
    assert key_for(tree / "src") == "wt"


def test_a_project_nested_inside_a_repo_wins(tmp_path):
    repo = tmp_path / "repo"
    make_main_repo(repo)
    (repo / "sub" / "x").mkdir(parents=True)
    register(("parent", tmp_path), ("sub", repo / "sub"))
    assert key_for(repo / "sub" / "x") == "sub"


def test_a_worktree_with_no_match_in_its_main_tree_falls_back_to_the_match_above(tmp_path):
    main = tmp_path / "repos" / "main"
    make_main_repo(main)
    tree = tmp_path / "scratch" / "wt"
    make_worktree(main, tree, "wt")
    register(("scratch", tmp_path / "scratch"))
    assert key_for(tree / "src") == "scratch"


def test_a_submodule_is_never_mapped(tmp_path):
    main = tmp_path / "super"
    git = make_main_repo(main)
    modules = git / "modules" / "lib"
    modules.mkdir(parents=True)
    (modules / "HEAD").write_text("ref: refs/heads/main\n")
    sub = tmp_path / "elsewhere" / "lib"
    sub.mkdir(parents=True)
    (sub / ".git").write_text(f"gitdir: {modules}\n")
    register(("super", main))
    assert key_for(sub) is None


def test_a_common_dir_not_named_git_is_not_mapped(tmp_path):
    bare = tmp_path / "bare.git"
    gitdir = bare / "worktrees" / "wt"
    gitdir.mkdir(parents=True)
    (gitdir / "commondir").write_text("../..\n")
    tree = tmp_path / "wt"
    tree.mkdir()
    (tree / ".git").write_text(f"gitdir: {gitdir}\n")
    register(("bare", tmp_path / "bare"))
    assert key_for(tree) is None


def test_a_broken_git_file_is_not_mapped(tmp_path):
    main = tmp_path / "repo"
    make_main_repo(main)
    tree = tmp_path / "wt"
    tree.mkdir()
    (tree / ".git").write_text("not a gitdir line\n")
    register(("repo", main))
    assert key_for(tree) is None


@pytest.mark.skipif(sys.platform != "win32", reason="Windows spellings")
def test_windows_spellings(tmp_path):
    main = tmp_path / "Repo"
    make_main_repo(main)
    tree = tmp_path / "wt"
    make_worktree(main, tree, "wt")
    gitdir = main / ".git" / "worktrees" / "wt"
    (tree / ".git").write_text(f"gitdir: {gitdir.as_posix()}\n")
    register(("repo", main))
    assert key_for(str(tree).upper()) == "repo"
    assert key_for(str(main).lower() + "\\src") == "repo"
    unc = "\\\\server\\share\\proj"
    register(("unc", unc))
    assert key_for(unc + "\\sub") == "unc"


@pytest.mark.slow
@pytest.mark.skipif(shutil.which("git") is None, reason="git is not on PATH")
def test_a_real_git_worktree_finds_its_main_project(tmp_path):
    main = tmp_path / "main"
    main.mkdir()

    def git(*args, cwd=main):
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                        "-c", "init.defaultBranch=main", *args],
                       cwd=cwd, check=True, capture_output=True)

    git("init", "-q")
    git("commit", "-q", "--allow-empty", "-m", "first")
    tree = tmp_path / "scratch" / "wt"
    git("worktree", "add", "-q", "-b", "side", str(tree))
    (tree / "src").mkdir()
    register(("main", main))
    assert key_for(tree / "src") == "main"

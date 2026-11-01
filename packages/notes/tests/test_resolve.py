"""The resolver: each rule, the no-twins rule, the OS path shapes, and no ambient
environment."""

from __future__ import annotations

import ntpath
import os
import posixpath
import sys
from pathlib import Path

import pytest

from moxi_notes import resolve
from moxi_notes.api import notes_dir
from moxi_notes.resolve import PROJECTS_UNREADABLE, below

from notes_kit import add_project, configure, git, git_env, home, no_projects, \
    projects_store, repo


@pytest.fixture(autouse=True)
def _git(tmp_path, monkeypatch):
    git_env(tmp_path, monkeypatch)


def notes() -> Path:
    return home() / "notes"


# --- rule 1: the override ---------------------------------------------------------------

def test_the_override_wins_as_given_with_its_tilde_expanded():
    alpha = repo(home() / "code" / "alpha")
    found = notes_dir(alpha, override="~/elsewhere/alpha-notes")
    assert found.dir == home() / "elsewhere" / "alpha-notes"
    assert found.rule == "override"
    assert not found.dir.exists()


def test_an_empty_override_is_no_override():
    alpha = repo(home() / "code" / "alpha")
    assert notes_dir(alpha, override="").dir == notes() / "code" / "alpha"


def test_the_environment_never_stands_in_for_the_override(monkeypatch):
    alpha = repo(home() / "code" / "alpha")
    monkeypatch.setenv("MOXI_NOTES_DIR", str(home() / "elsewhere"))
    assert notes_dir(alpha).dir == notes() / "code" / "alpha"
    assert notes_dir(alpha, override=None).rule == "computed"


# --- rule 2: projects -------------------------------------------------------------------

def test_a_project_with_a_notes_root_answers_with_it_from_any_folder_inside():
    alpha = repo(home() / "code" / "alpha")
    (alpha / "src").mkdir()
    kept = home() / "notes" / "alpha-notes"
    add_project("alpha", alpha, "--notes-root", str(kept))
    for where in (alpha, alpha / "src"):
        found = notes_dir(where)
        assert (found.dir, found.rule, found.warning) == (kept, "project", None)


def test_a_project_without_a_notes_root_is_the_anchor():
    beta = home() / "code" / "beta"
    (beta / "deep" / "er").mkdir(parents=True)
    add_project("beta", beta)
    # beta's row now holds the computed folder as its notes root; clear it so the
    # anchor rule answers.
    from notes_kit import moxi_cmd

    assert moxi_cmd("projects", "set", "beta", "--no-notes-root")[0] == 0
    found = notes_dir(beta / "deep" / "er")
    assert (found.dir, found.rule) == (notes() / "code" / "beta", "computed")


def test_without_projects_the_path_alone_decides(monkeypatch):
    alpha = repo(home() / "code" / "alpha")
    add_project("alpha", alpha, "--notes-root", str(home() / "notes" / "alpha-notes"))
    no_projects(monkeypatch)
    found = notes_dir(alpha)
    assert (found.dir, found.rule, found.warning) == (notes() / "code" / "alpha",
                                                      "computed", None)


def test_projects_that_cannot_be_read_go_on_with_a_warning():
    store = projects_store()
    store.parent.mkdir(parents=True, exist_ok=True)
    store.write_bytes(b"not a database" * 100)
    alpha = repo(home() / "code" / "alpha")
    found = notes_dir(alpha)
    assert found.dir == notes() / "code" / "alpha"
    assert found.warning == PROJECTS_UNREADABLE


def test_projects_that_will_not_load_go_on_with_a_warning(monkeypatch):
    import moxi.modules

    real = moxi.modules.load

    class Broken:
        state = "broken"
        ok = False

    monkeypatch.setattr(moxi.modules, "load",
                        lambda name, **kw: Broken() if name == "projects"
                        else real(name, **kw))
    found = notes_dir(repo(home() / "code" / "alpha"))
    assert found.warning == PROJECTS_UNREADABLE


# --- rules 3 and 4 ----------------------------------------------------------------------

def test_an_excluded_folder_gets_none_with_its_tilde_expanded():
    configure(exclude=["~/personal"])
    private = repo(home() / "personal" / "diary")
    found = notes_dir(private)
    assert (found.dir, found.rule) == (None, "excluded")
    assert notes_dir(home() / "personal").dir is None


def test_an_excluded_project_root_gets_no_computed_folder():
    configure(exclude=["~/code/alpha/vendor"])
    alpha = repo(home() / "code" / "alpha")
    (alpha / "vendor").mkdir()
    assert notes_dir(alpha / "vendor").rule == "excluded"
    assert notes_dir(alpha).dir == notes() / "code" / "alpha"


def test_a_folder_inside_the_notes_is_its_own_notes_directory():
    repo(notes())
    inside = notes() / "team" / "topics" / "gardening"
    inside.mkdir(parents=True)
    found = notes_dir(inside)
    assert (found.dir, found.rule) == (inside, "inside")
    assert notes_dir(notes()).dir == notes()


def test_the_notes_root_may_be_set_elsewhere(tmp_path):
    configure(root=str(tmp_path / "vault"))
    alpha = repo(home() / "code" / "alpha")
    assert notes_dir(alpha).dir == tmp_path / "vault" / "code" / "alpha"
    assert notes_dir(tmp_path / "vault" / "x").rule == "inside"


# --- rule 5: the computed folder --------------------------------------------------------

def test_a_git_repo_maps_below_home_and_every_subfolder_shares_it():
    alpha = repo(home() / "code" / "alpha")
    (alpha / "src" / "deep").mkdir(parents=True)
    for where in (alpha, alpha / "src", alpha / "src" / "deep"):
        found = notes_dir(where)
        assert (found.dir, found.rule) == (notes() / "code" / "alpha", "computed")


def test_the_prefix_puts_the_computed_folders_under_it():
    configure(prefix="team")
    alpha = repo(home() / "code" / "alpha")
    assert notes_dir(alpha).dir == notes() / "team" / "code" / "alpha"


def test_a_folder_in_no_repo_is_its_own_anchor():
    loose = home() / "scratch" / "idea"
    loose.mkdir(parents=True)
    assert notes_dir(loose).dir == notes() / "scratch" / "idea"
    assert not (notes() / "scratch").exists()


def test_a_linked_worktree_maps_to_its_main_work_tree(tmp_path):
    alpha = repo(home() / "code" / "alpha", commit=True)
    linked = home() / "work" / "alpha-feature"
    git("worktree", "add", "-q", str(linked), "-b", "feature", cwd=alpha)
    (linked / "src").mkdir()
    for where in (linked, linked / "src"):
        assert notes_dir(where).dir == notes() / "code" / "alpha"


def test_a_symlinked_home_gives_one_answer_for_either_spelling(tmp_path, monkeypatch):
    real_home = tmp_path / "var" / "home" / "sample"
    real_home.mkdir(parents=True)
    linked_home = tmp_path / "home-link"
    linked_home.symlink_to(real_home)
    monkeypatch.setenv("HOME", str(linked_home))
    alpha = repo(real_home / "code" / "alpha")
    (alpha / "src").mkdir()
    answers = {notes_dir(where).dir for where in (
        alpha, alpha / "src", linked_home / "code" / "alpha" / "src")}
    assert answers == {linked_home / "notes" / "code" / "alpha"}
    monkeypatch.setenv("HOME", str(real_home))
    assert notes_dir(linked_home / "code" / "alpha").dir == real_home / "notes" / "code" / "alpha"


def test_a_symlinked_notes_root_is_still_inside(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    (home() / "notes").symlink_to(vault)
    (vault / "plans").mkdir()
    assert notes_dir(vault / "plans").rule == "inside"
    assert notes_dir(home() / "notes" / "plans").rule == "inside"


def test_home_itself_gets_no_notes_directory():
    found = notes_dir(home())
    assert (found.dir, found.rule) == (None, "none")


def test_home_as_a_git_repo_still_gets_none_and_its_folders_map_to_home():
    repo(home())
    assert notes_dir(home()).dir is None
    (home() / "code").mkdir()
    assert notes_dir(home() / "code").dir is None


def test_a_path_off_home_gets_none(tmp_path):
    srv = repo(tmp_path / "srv" / "alpha")
    found = notes_dir(srv)
    assert (found.dir, found.rule) == (None, "none")


@pytest.mark.parametrize("folder", ["topics", "inbox"])
def test_an_answer_under_topics_or_inbox_is_refused(folder):
    clash = repo(home() / folder / "gardening")
    found = notes_dir(clash)
    assert (found.dir, found.rule) == (None, "reserved")


def test_the_reserved_folders_follow_the_prefix():
    configure(prefix="team")
    # base is notes/team, so ~/topics/gardening would claim notes/team/topics/gardening
    assert notes_dir(repo(home() / "topics" / "gardening")).rule == "reserved"
    assert notes_dir(repo(home() / "team" / "topics" / "x")).dir == (
        notes() / "team" / "team" / "topics" / "x")


def test_the_git_call_answers_for_the_path_whatever_git_dir_the_caller_set(monkeypatch):
    alpha = repo(home() / "code" / "alpha")
    beta = repo(home() / "code" / "beta")
    (alpha / "src").mkdir()
    monkeypatch.setenv("GIT_DIR", str(beta / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(beta))
    assert notes_dir(alpha / "src").dir == notes() / "code" / "alpha"


def test_a_git_that_fails_leaves_the_path_as_its_own_anchor(monkeypatch):
    alpha = repo(home() / "code" / "alpha")
    (alpha / "src").mkdir()
    monkeypatch.setenv("PATH", str(home() / "no-git-here"))
    assert notes_dir(alpha / "src").dir == notes() / "code" / "alpha" / "src"


def test_the_resolver_never_creates_a_folder():
    alpha = repo(home() / "code" / "alpha")
    notes_dir(alpha)
    assert not notes().exists()


def test_a_relative_path_is_refused():
    with pytest.raises(ValueError, match="absolute path"):
        notes_dir("code/alpha")


# --- the OS path shapes, as pure strings ------------------------------------------------

@pytest.mark.parametrize("flavour, path, root, rel", [
    (posixpath, "/home/sample/code/x", "/home/sample", ("code", "x")),
    (posixpath, "/Users/sample/code/x", "/Users/sample", ("code", "x")),
    (ntpath, r"C:\Users\sample\code\x", r"C:\Users\sample", ("code", "x")),
    (ntpath, r"c:\users\SAMPLE\Code\x", r"C:\Users\sample", ("Code", "x")),
    (ntpath, "C:/Users/sample/code/x", r"C:\Users\sample", ("code", "x")),
    (posixpath, "/home/sample", "/home/sample", ()),
    (ntpath, r"C:\Users\sample", r"C:\Users\sample\\", ()),
])
def test_below_takes_the_parts_under_home_on_each_os(flavour, path, root, rel):
    assert below(path, root, flavour=flavour) == rel


@pytest.mark.parametrize("flavour, path, root", [
    (posixpath, "/home/sample2/code", "/home/sample"),
    (posixpath, "/Users/Sample/code", "/Users/sample"),
    (posixpath, "/srv/alpha", "/home/sample"),
    (ntpath, r"D:\Users\sample\code", r"C:\Users\sample"),
    (ntpath, r"C:\Users\sample2", r"C:\Users\sample"),
])
def test_below_is_none_for_a_path_not_under_the_root(flavour, path, root):
    assert below(path, root, flavour=flavour) is None


@pytest.mark.skipif(sys.platform != "win32", reason="Windows compares paths without case")
def test_on_windows_another_case_gives_the_same_folder():
    alpha = repo(home() / "code" / "alpha")
    upper = Path(str(alpha).upper())
    assert notes_dir(upper).dir is not None
    assert os.path.normcase(notes_dir(upper).dir) == os.path.normcase(notes_dir(alpha).dir)


@pytest.mark.skipif(sys.platform != "darwin",
                    reason="macOS: a path in another case than HOME gets no computed folder")
def test_on_macos_a_path_spelled_in_another_case_than_home_gets_none():
    alpha = home() / "code" / "alpha"
    alpha.mkdir(parents=True)
    swapped = Path(str(alpha).swapcase())
    assert notes_dir(swapped).dir is None


def test_the_rule_names_are_the_ones_listed():
    assert set(resolve.RULES) == {"override", "project", "excluded", "inside", "computed",
                                  "reserved", "none"}

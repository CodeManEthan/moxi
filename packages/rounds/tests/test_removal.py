"""The removal rule: a build's folder goes only when rounds made it here (its place under
rounds' roots, no link below them, its marker naming the build) and removing it loses no
work, read with git's settings that hide work overridden. Each check fails alone, keeps
the folder and is named; a folder holding only what is disposable goes."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import sys
from pathlib import Path

import pytest
from moxi.config import ConfigError

from moxi_rounds import (builds, checking, commissioning, dropping, log, opening, places,
                         removal)
from moxi_rounds import settings as own
from moxi_rounds import state as states

ROUND = "proj-a/a-round"
POINTER = "comms:1234567"


@pytest.fixture
def repo(git_repo, tmp_path):
    from moxi import cli

    assert cli.main(["projects", "add", "proj-a", "--root", str(git_repo.root)]) == 0
    assert cli.main(["projects", "set", "proj-a", "--notes-root",
                     str(tmp_path / "notes")]) == 0
    exclude = git_repo.root / ".git" / "info" / "exclude"
    exclude.parent.mkdir(parents=True, exist_ok=True)
    exclude.write_text(".venv\n__pycache__\n.env\nnode_modules\n", encoding="utf-8")
    return git_repo


@pytest.fixture
def made(repo, tmp_path, write_config):
    """B1 commissioned on `work` with one commit of work. Returns `(log, worktree repo,
    commission event)`."""
    write_config("")
    opening.open_round(ROUND, description="a placeholder round", actor="planner",
                       flow="fix")
    path = log.round_log(tmp_path / "notes" / "rounds" / "a-round")
    log.append(path, "stage.declared",
               {"id": "B1", "repo": "proj-a", "branch": "work", "after": []},
               actor="planner")
    done = commissioning.commission(ROUND, "B1", builder="coder", actor="planner")
    tree = type(repo)(done.worktree)
    tree.commit("work.txt", "the work")
    return path, tree, commission(path)


def commission(path: Path) -> dict:
    state = states.fold(log.read_round(path))
    return checking.commission_of(state, builds.current(state, "B1"))


def remove(event: dict, kind: str = removal.WORKTREE, **given):
    return removal.remove(ROUND, event, kind, settings=own.load(), **given)


def kept_by(found) -> str:
    assert isinstance(found, removal.Kept), found
    return found.check


# -- what goes -----------------------------------------------------------------------------

def test_a_worktree_holding_only_disposable_paths_goes_and_its_branch_stays(made, repo):
    _path, tree, event = made
    (tree.root / ".venv" / "bin").mkdir(parents=True)
    (tree.root / ".venv" / "bin" / "python").write_text("", encoding="utf-8")
    (tree.root / "__pycache__").mkdir()
    (tree.root / "__pycache__" / "work.pyc").write_bytes(b"\0")

    assert remove(event) == Path(event["worktree"])

    assert not tree.root.exists()
    assert repo.git("branch", "--list", "work").strip().endswith("work")


def test_a_scratch_folder_goes_whole_through_the_trash(made):
    _path, _tree, event = made
    scratch = Path(event["scratch"])
    (scratch / "check-1.log").write_text("output\n", encoding="utf-8")

    assert remove(event, removal.SCRATCH) == scratch

    assert not scratch.exists()
    assert not (removal.paths.cache_dir() / removal.TRASH_DIR / event["build"]).exists()


def test_a_symlinked_root_is_followed_and_its_proven_folder_goes(repo, tmp_path,
                                                                 write_config):
    real = tmp_path / "real-worktrees"
    real.mkdir()
    (tmp_path / "linked").symlink_to(real)
    write_config(f"worktree_dir = {json.dumps(str(tmp_path / 'linked'))}\n")
    opening.open_round(ROUND, description="a placeholder round", actor="planner",
                       flow="fix")
    path = log.round_log(tmp_path / "notes" / "rounds" / "a-round")
    log.append(path, "stage.declared",
               {"id": "B1", "repo": "proj-a", "branch": "work", "after": []},
               actor="planner")
    commissioning.commission(ROUND, "B1", builder="coder", actor="planner")

    assert remove(commission(path)) == real / "proj-a" / "work"
    assert not (real / "proj-a" / "work").exists()


# -- A: ownership --------------------------------------------------------------------------

@pytest.mark.parametrize(("change", "check"), [
    ({"host": "box-1"}, "A1"),
    ({"worktree": "/srv/elsewhere/proj-a/work"}, "A2"),
    ({"branch": "a/../work"}, "A2"),
    ({"build": "not-a-build-id"}, "A2"),
    ({"repo": "proj-b"}, "A2"),
])
def test_a_record_that_does_not_name_rounds_own_place_keeps_the_folder(made, change,
                                                                        check):
    _path, tree, event = made
    assert kept_by(remove({**event, **change})) == check
    assert tree.root.is_dir()


def test_a_worktree_dir_the_config_moved_from_is_outside_the_roots(made, write_config,
                                                                   tmp_path):
    _path, tree, event = made
    write_config(f"worktree_dir = {json.dumps(str(tmp_path / 'moved'))}\n")
    found = remove(event)
    assert kept_by(found) == "A2" and "outside rounds' roots" in found.why
    assert tree.root.is_dir()


def test_a_link_at_the_place_keeps_what_it_points_at(made, tmp_path):
    _path, _tree, event = made
    scratch = Path(event["scratch"])
    elsewhere = tmp_path / "elsewhere"
    shutil.move(scratch, elsewhere)
    scratch.symlink_to(elsewhere)

    assert kept_by(remove(event, removal.SCRATCH)) == "A3"
    assert (elsewhere / places.SCRATCH_MARKER).is_file()


def test_a_link_to_a_folder_above_the_place_keeps_it(made, tmp_path):
    _path, _tree, event = made
    scratch = Path(event["scratch"])
    above = scratch.parent
    moved = tmp_path / "moved-branch"
    shutil.move(above, moved)
    above.symlink_to(moved)

    assert kept_by(remove(event, removal.SCRATCH)) == "A3"
    assert (moved / event["build"]).is_dir()


def test_a_folder_with_no_marker_is_unmarked_and_kept(made):
    _path, _tree, event = made
    scratch = Path(event["scratch"])
    (scratch / places.SCRATCH_MARKER).unlink()

    found = remove(event, removal.SCRATCH)
    assert kept_by(found) == "A4" and found.why.startswith("unmarked")
    assert scratch.is_dir()


def test_a_marker_naming_another_build_keeps_the_folder(made):
    _path, tree, event = made
    git_dir = places.worktree_git_dir(tree.root)
    marker = git_dir / places.WORKTREE_MARKER
    other = places.marker("0123456789abcdef", round=ROUND, stage="B1", repo="proj-a",
                          branch="work")
    marker.write_text(other.line(), encoding="utf-8")

    found = remove(event)
    assert kept_by(found) == "A4" and "0123456789abcdef" in found.why
    assert tree.root.is_dir()


def test_a_worktree_on_another_branch_is_kept(made):
    _path, tree, event = made
    tree.git("switch", "-q", "-c", "other")
    assert kept_by(remove(event)) == "A4"
    assert tree.root.is_dir()


def test_a_worktree_whose_git_file_names_another_repos_folder_is_kept(made, repo,
                                                                      tmp_path):
    _path, tree, event = made
    other = type(repo)(tmp_path / "other")
    other.root.mkdir()
    other.git("init", "-q", "-b", "main")
    other.commit("README")
    other.git("worktree", "add", "-q", "-b", "work", str(tmp_path / "other-wt"))
    named = (tmp_path / "other" / ".git" / "worktrees" / "other-wt")
    (named / places.WORKTREE_MARKER).write_text(
        (places.worktree_git_dir(tree.root) / places.WORKTREE_MARKER).read_text(
            encoding="utf-8"), encoding="utf-8")
    (tree.root / ".git").write_text(f"gitdir: {named}\n", encoding="utf-8")

    assert kept_by(remove(event)) == "A4"
    assert (tree.root / "work.txt").is_file()


# -- B: no lost work -----------------------------------------------------------------------

def _untracked_hidden_by_config(tree, repo, monkeypatch, tmp_path):
    repo.git("config", "status.showUntrackedFiles", "no")
    (tree.root / "notes.txt").write_text("only here\n", encoding="utf-8")


def _untracked_with_git_dir_set(tree, repo, monkeypatch, tmp_path):
    other = tmp_path / "decoy"
    other.mkdir()
    type(repo)(other).git("init", "-q", "-b", "main")
    monkeypatch.setenv("GIT_DIR", str(other / ".git"))
    (tree.root / "notes.txt").write_text("only here\n", encoding="utf-8")


def _fsmonitor_program(tree, repo, monkeypatch, tmp_path):
    ran = tmp_path / "fsmonitor-ran"
    program = tmp_path / "fsmonitor.sh"
    program.write_text(f"#!/bin/sh\ntouch {shlex.quote(str(ran))}\n", encoding="utf-8")
    program.chmod(0o755)
    repo.git("config", "core.fsmonitor", str(program))
    (tree.root / "notes.txt").write_text("only here\n", encoding="utf-8")


def _staged(tree, repo, monkeypatch, tmp_path):
    (tree.root / "work.txt").write_text("changed\n", encoding="utf-8")
    tree.git("add", "work.txt")


def _ignored_env(tree, repo, monkeypatch, tmp_path):
    (tree.root / ".env").write_text("TOKEN=only here\n", encoding="utf-8")


def _skip_worktree(tree, repo, monkeypatch, tmp_path):
    tree.git("update-index", "--skip-worktree", "work.txt")
    (tree.root / "work.txt").write_text("changed\n", encoding="utf-8")


def _assume_unchanged(tree, repo, monkeypatch, tmp_path):
    tree.git("update-index", "--assume-unchanged", "work.txt")
    (tree.root / "work.txt").write_text("changed\n", encoding="utf-8")


def _commit_only_on_a_worktree_ref(tree, repo, monkeypatch, tmp_path):
    tree.commit("kept.txt", "a commit kept only by refs/worktree/keep")
    tree.git("update-ref", "refs/worktree/keep", "HEAD")
    tree.git("reset", "-q", "--hard", "HEAD~1")


def _locked(tree, repo, monkeypatch, tmp_path):
    repo.git("worktree", "lock", str(tree.root))


def _submodule(tree, repo, monkeypatch, tmp_path):
    sub = type(repo)(tmp_path / "sub")
    sub.root.mkdir()
    sub.git("init", "-q", "-b", "main")
    sub.commit("lib.txt")
    tree.git("-c", "protocol.file.allow=always", "submodule", "add", "-q",
             str(sub.root), "sub")
    tree.git("commit", "-q", "-m", "add a submodule")


def _changed_submodule_hidden_by_config(tree, repo, monkeypatch, tmp_path):
    _submodule(tree, repo, monkeypatch, tmp_path)
    repo.git("config", "diff.ignoreSubmodules", "all")
    (tree.root / ".gitmodules").write_text(
        (tree.root / ".gitmodules").read_text(encoding="utf-8") + "\tignore = all\n",
        encoding="utf-8")
    tree.git("commit", "-q", "-am", "ignore the submodule")
    inside = type(repo)(tree.root / "sub")
    inside.commit("lib.txt", "changed only here")


@pytest.mark.parametrize(("make", "check"), [
    (_untracked_hidden_by_config, "B3"),
    (_untracked_with_git_dir_set, "B3"),
    (_fsmonitor_program, "B3"),
    (_staged, "B3"),
    (_ignored_env, "B3"),
    (_skip_worktree, "B2"),
    (_assume_unchanged, "B2"),
    (_commit_only_on_a_worktree_ref, "B4"),
    (_locked, "B6"),
    (_submodule, "B5"),
    (_changed_submodule_hidden_by_config, "B3"),
])
def test_work_a_users_config_would_hide_keeps_the_worktree(made, repo, monkeypatch,
                                                           tmp_path, make, check):
    _path, tree, event = made
    make(tree, repo, monkeypatch, tmp_path)

    assert kept_by(remove(event)) == check
    assert tree.root.is_dir()
    assert not (tmp_path / "fsmonitor-ran").exists()


def test_the_paths_that_are_work_are_named(made):
    _path, tree, event = made
    (tree.root / ".venv").mkdir()
    (tree.root / ".env").write_text("TOKEN=only here\n", encoding="utf-8")
    (tree.root / "draft.md").write_text("only here\n", encoding="utf-8")

    found = remove(event)
    assert (found.check, sorted(found.work)) == ("B3", [".env", "draft.md"])


def test_the_branch_the_same_act_deletes_holds_no_commit(made, repo):
    _path, tree, event = made
    assert kept_by(remove(event, deletes="work")) == "B4"
    assert tree.root.is_dir()


@pytest.mark.parametrize("kind", [removal.WORKTREE, removal.SCRATCH])
def test_a_mount_inside_keeps_the_folder(made, monkeypatch, kind):
    _path, tree, event = made
    place = Path(os.path.realpath(event[kind]))
    (place / "node_modules").mkdir()
    monkeypatch.setattr(removal, "_mount_table",
                        lambda: [str(place / "node_modules"), "/"])

    found = remove(event, kind)
    assert kept_by(found) == "B7" and found.work == (str(place / "node_modules"),)
    assert place.is_dir()


def test_a_file_written_between_the_first_read_and_the_removal_keeps_the_worktree(
        made, monkeypatch):
    _path, tree, event = made
    judge = removal.judge

    def judge_then_write(*args, **kwargs):
        found = judge(*args, **kwargs)
        (tree.root / ".env").write_text("TOKEN=written late\n", encoding="utf-8")
        return found

    monkeypatch.setattr(removal, "judge", judge_then_write)

    assert kept_by(remove(event)) == "B3"
    assert (tree.root / ".env").is_file()


# -- the drops -----------------------------------------------------------------------------

def test_a_build_drop_removes_its_folders_and_keeps_its_branch(made, repo):
    _path, tree, event = made
    dropped = dropping.drop_build(ROUND, "B1", by="operator", record=POINTER,
                                  actor="planner")
    assert dropped.removed == (Path(event["worktree"]), Path(event["scratch"]))
    assert dropped.kept == ()
    assert repo.git("branch", "--list", "work").strip().endswith("work")


def test_a_round_drop_keeps_a_worktree_holding_an_ignored_env_and_says_b3(made):
    _path, tree, event = made
    (tree.root / ".env").write_text("TOKEN=only here\n", encoding="utf-8")

    dropped = dropping.drop(ROUND, by="operator", record=POINTER, actor="planner")

    assert dropped.removed == (Path(event["scratch"]),)
    assert [(one.check, one.work) for one in dropped.kept] == [("B3", (".env",))]
    assert (tree.root / ".env").is_file()


# -- the setting ---------------------------------------------------------------------------

@pytest.mark.parametrize("entry", ["*", "**", "?*", "[!.]*", "", "build/out", "[abc]"])
def test_a_disposable_entry_that_matches_every_name_or_a_path_is_refused(entry):
    with pytest.raises(ConfigError):
        own.check_text(f"[defaults]\ndisposable = [{json.dumps(entry)}]\n")
    with pytest.raises(ConfigError):
        own.check_text(f"[projects.proj-a]\ndisposable = [{json.dumps(entry)}]\n")


@pytest.mark.parametrize("entry", [".venv", "*.pyc", "[!.]*.log", "dist?"])
def test_a_disposable_entry_naming_what_may_go_is_taken(entry):
    own.check_text(f"[defaults]\ndisposable = [{json.dumps(entry)}]\n")


def test_a_project_disposable_list_is_read_for_its_builds(made, write_config):
    _path, tree, event = made
    (tree.root / ".env").write_text("TOKEN=disposable here\n", encoding="utf-8")
    write_config('[projects.proj-a]\ndisposable = [".env"]\n')

    assert remove(event) == Path(event["worktree"])


@pytest.mark.skipif(sys.platform == "win32", reason="the mount table is read on Linux")
def test_the_mount_table_reads_escaped_mount_points():
    assert removal._unescape("/srv/a\\040b") == "/srv/a b"

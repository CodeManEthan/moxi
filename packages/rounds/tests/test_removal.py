"""The removal rule: a build's folder goes only when rounds made it here (its place under
rounds' roots, no link below them, its marker naming the build) and removing it loses no
work, read with git's settings that hide work overridden. Each check fails alone, keeps
the folder and is named; a folder holding only what is disposable goes."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from moxi import files
from moxi.config import ConfigError

from moxi_rounds import (builds, checking, commissioning, dropping, log, opening, places,
                         removal, sweeping)
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
    opening.open_round(ROUND, description="a placeholder round", by="planner",
                       flow="fix")
    path = log.round_log(tmp_path / "notes" / "rounds" / "a-round")
    log.append(path, "stage.declared",
               {"id": "B1", "repo": "proj-a", "branch": "work", "after": []},
               by="planner")
    done = commissioning.commission(ROUND, "B1", builder="coder", by="planner")
    tree = type(repo)(done.worktree)
    tree.commit("work.txt", "the work")
    return path, tree, commission(path)


def commission(path: Path) -> dict:
    state = states.fold(log.read_round(path))
    return checking.commission_of(state, builds.current(state, "B1"))


def placed(event: dict, kind: str) -> Path:
    """The place B1's commission `event` records for `kind`, read as rounds reads it: a
    record holds a place under the home folder from `~` (on Windows rounds' own folders
    are under it)."""
    return Path(places.expand_home(event[kind]))


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

    assert remove(event) == placed(event, "worktree")

    assert not tree.root.exists()
    assert repo.git("branch", "--list", "work").strip().endswith("work")


def test_a_scratch_dir_goes_whole_through_the_trash(made):
    _path, _tree, event = made
    scratch = placed(event, "scratch")
    (scratch / "check-1.log").write_text("output\n", encoding="utf-8")

    assert remove(event, removal.SCRATCH) == scratch

    assert not scratch.exists()
    assert not (removal.paths.cache_dir() / removal.TRASH_DIR / event["build"]).exists()


def test_a_symlinked_root_is_followed_and_its_proven_dir_goes(repo, tmp_path,
                                                                 write_config):
    real = tmp_path / "real-worktrees"
    real.mkdir()
    (tmp_path / "linked").symlink_to(real)
    write_config(f"worktree_dir = {json.dumps(str(tmp_path / 'linked'))}\n")
    opening.open_round(ROUND, description="a placeholder round", by="planner",
                       flow="fix")
    path = log.round_log(tmp_path / "notes" / "rounds" / "a-round")
    log.append(path, "stage.declared",
               {"id": "B1", "repo": "proj-a", "branch": "work", "after": []},
               by="planner")
    commissioning.commission(ROUND, "B1", builder="coder", by="planner")

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
def test_a_record_that_does_not_name_rounds_own_place_keeps_the_dir(made, change,
                                                                        check):
    _path, tree, event = made
    assert kept_by(remove({**event, **change})) == check
    assert tree.root.is_dir()


def test_a_record_with_no_host_is_not_read_as_this_machine_s(made):
    _path, tree, event = made
    unrecorded = {key: value for key, value in event.items() if key != "host"}

    found = remove(unrecorded)

    assert kept_by(found) == "A1" and "names no machine" in found.why
    assert tree.root.is_dir()


def test_whether_a_build_was_made_here_reads_a_missing_host_as_the_caller_says():
    assert places.made_here({"host": places.host()},
                                    unrecorded_is_here=False)
    assert not places.made_here({"host": "box-1"}, unrecorded_is_here=True)
    assert places.made_here({}, unrecorded_is_here=True)
    assert not places.made_here({}, unrecorded_is_here=False)


def test_a_worktree_dir_the_config_moved_from_is_outside_the_roots(made, write_config,
                                                                   tmp_path):
    _path, tree, event = made
    write_config(f"worktree_dir = {json.dumps(str(tmp_path / 'moved'))}\n")
    found = remove(event)
    assert kept_by(found) == "A2" and "outside rounds' roots" in found.why
    assert tree.root.is_dir()


def test_a_link_at_the_place_keeps_what_it_points_at(made, tmp_path):
    _path, _tree, event = made
    scratch = placed(event, "scratch")
    elsewhere = tmp_path / "elsewhere"
    shutil.move(scratch, elsewhere)
    scratch.symlink_to(elsewhere)

    assert kept_by(remove(event, removal.SCRATCH)) == "A3"
    assert (elsewhere / places.SCRATCH_MARKER).is_file()


def test_a_link_to_a_dir_above_the_place_keeps_it(made, tmp_path):
    _path, _tree, event = made
    scratch = placed(event, "scratch")
    above = scratch.parent
    moved = tmp_path / "moved-branch"
    shutil.move(above, moved)
    above.symlink_to(moved)

    assert kept_by(remove(event, removal.SCRATCH)) == "A3"
    assert (moved / event["build"]).is_dir()


def test_a_dir_with_no_marker_is_unmarked_and_kept(made):
    _path, _tree, event = made
    scratch = placed(event, "scratch")
    (scratch / places.SCRATCH_MARKER).unlink()

    found = remove(event, removal.SCRATCH)
    assert kept_by(found) == "A4" and found.why.startswith("unmarked")
    assert scratch.is_dir()


def test_a_marker_naming_another_build_keeps_the_dir(made):
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


def test_a_worktree_whose_git_file_names_another_repos_dir_is_kept(made, repo,
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
def test_a_mount_inside_keeps_the_dir(made, monkeypatch, kind):
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

def test_a_build_drop_removes_its_dirs_and_keeps_its_branch(made, repo):
    _path, tree, event = made
    dropped = dropping.drop_build(ROUND, "B1", by="operator", record=POINTER,
                                  run_by="planner")
    assert dropped.removed == (placed(event, "worktree"), placed(event, "scratch"))
    assert dropped.kept == ()
    assert repo.git("branch", "--list", "work").strip().endswith("work")


def test_a_build_drop_writes_its_trash_entry_to_the_sweeps_log_proving_a_and_b(made):
    _path, _tree, event = made
    dropping.drop_build(ROUND, "B1", by="operator", record=POINTER, run_by="planner")

    found = files.read_records(sweeping.log_file())

    assert [(one["act"], one["kind"], one["entry"]) for one in found] == [
        ("renamed", "scratch", event["build"]), ("deleted", "scratch", event["build"])]
    assert found[0]["place"] == event["scratch"] and found[1]["gone"] is True
    for one in found:
        assert (one["round"], one["stage"], one["repo"], one["branch"], one["build"]) == (
            ROUND, "B1", "proj-a", "work", event["build"])
        assert one["proven"] == ["A", "B"]


@pytest.mark.parametrize("whole", [False, True], ids=["build", "round"])
def test_a_drop_leaves_a_scratch_a_process_uses_and_moves_the_rest(made, whole):
    # Core's abandon was not refused for its scratch folder: the folder in use was not
    # moved and stayed for the sweep, and the worktree went as before.
    _path, _tree, event = made
    scratch = placed(event, "scratch")
    proc = subprocess.Popen([sys.executable, "-c", "import sys; sys.stdin.read()"],
                            cwd=scratch, stdin=subprocess.PIPE)
    try:
        if whole:
            dropped = dropping.drop(ROUND, by="operator", record=POINTER,
                                    run_by="planner")
        else:
            dropped = dropping.drop_build(ROUND, "B1", by="operator", record=POINTER,
                                          run_by="planner")
    finally:
        proc.communicate(timeout=30)

    assert dropped.removed == (placed(event, "worktree"),)
    [kept] = dropped.kept
    assert (kept.kind, kept.place, kept.check) == ("scratch", scratch, "D")
    assert len(kept.work) == 1 and kept.work[0].startswith(f"pid {proc.pid} (")
    assert scratch.is_dir() and files.read_records(sweeping.log_file()) == []


def test_a_round_drop_keeps_a_worktree_holding_an_ignored_env_and_says_b3(made):
    _path, tree, event = made
    (tree.root / ".env").write_text("TOKEN=only here\n", encoding="utf-8")

    dropped = dropping.drop(ROUND, by="operator", record=POINTER, run_by="planner")

    assert dropped.removed == (placed(event, "scratch"),)
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

    assert remove(event) == placed(event, "worktree")


@pytest.mark.skipif(sys.platform == "win32", reason="the mount table is read on Linux")
def test_the_mount_table_reads_escaped_mount_points():
    assert removal._unescape("/srv/a\\040b") == "/srv/a b"


# -- another device, and a repo whose git folder is elsewhere ---------------------------------

def _on_another_device(found: os.stat_result) -> os.stat_result:
    fields = list(found)
    fields[2] = found.st_dev + 1
    return os.stat_result(fields)


def test_a_part_below_the_root_on_another_device_keeps_the_dir(made, monkeypatch):
    _path, tree, event = made
    place = Path(os.path.realpath(placed(event, "worktree")))
    real_lstat = os.lstat

    def lstat(path, *args, **kwargs):
        found = real_lstat(path, *args, **kwargs)
        return _on_another_device(found) if Path(path) == place else found

    monkeypatch.setattr(removal.os, "lstat", lstat)
    found = remove(event)
    monkeypatch.undo()

    assert kept_by(found) == "A3" and "on another device than its root" in found.why
    assert tree.root.is_dir()


@pytest.mark.parametrize("kind", [removal.WORKTREE, removal.SCRATCH])
def test_an_entry_on_another_device_inside_keeps_the_dir(made, monkeypatch, kind):
    _path, _tree, event = made
    place = Path(os.path.realpath(event[kind]))
    # Ignored and disposable, so only the walk can keep it.
    (place / "node_modules").mkdir()
    (place / "node_modules" / "data.bin").write_bytes(b"\0")
    real_scandir = removal.os.scandir

    class Entry:
        def __init__(self, real):
            self._real, self.path, self.name = real, real.path, real.name

        def __getattr__(self, name):
            return getattr(self._real, name)

        def stat(self, *, follow_symlinks=True):
            found = self._real.stat(follow_symlinks=follow_symlinks)
            return _on_another_device(found) if self.name == "node_modules" else found

    class Listing:
        def __init__(self, path):
            self._real = real_scandir(path)

        def __enter__(self):
            return [Entry(one) for one in self._real.__enter__()]

        def __exit__(self, *exc):
            return self._real.__exit__(*exc)

    monkeypatch.setattr(removal.os, "scandir", Listing)
    found = remove(event, kind)
    monkeypatch.undo()

    assert kept_by(found) == "B7" and found.why.endswith("node_modules is on another device")
    assert (place / "node_modules" / "data.bin").is_file()


def test_a_worktree_of_a_repo_made_with_a_separate_git_dir_goes(repo, tmp_path,
                                                                  write_config):
    repo.git("init", "-q", f"--separate-git-dir={tmp_path / 'git-folder'}")
    assert (repo.root / ".git").is_file()
    write_config("")
    opening.open_round(ROUND, description="a placeholder round", by="planner",
                       flow="fix")
    path = log.round_log(tmp_path / "notes" / "rounds" / "a-round")
    log.append(path, "stage.declared",
               {"id": "B1", "repo": "proj-a", "branch": "work", "after": []},
               by="planner")
    done = commissioning.commission(ROUND, "B1", builder="coder", by="planner")
    type(repo)(done.worktree).commit("work.txt", "the work")

    assert remove(commission(path)) == Path(done.worktree)

    assert not Path(done.worktree).exists()
    assert "work" in repo.git("branch", "--list", "work")

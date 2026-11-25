"""`finish` with the merge action: the merge made in the target's checkout or in one
added for it, its record under its id with the landing and the target before it,
`finish.done` once the gates read on the log still hold, the finish lock taken and
released, and every refusal writing nothing."""

from __future__ import annotations

import json
import os
import shlex
import stat
import subprocess
import sys
from pathlib import Path

import pytest
from moxi import files

from moxi_rounds import (asking, builds, checking, commissioning, finish_lock, finishing,
                         log, next_step, opening, places, removal, sweeping, verdicts)
from moxi_rounds import settings as own
from moxi_rounds import state as states
from moxi_rounds.cli import main
from moxi_rounds.errors import Refused

ROUND = "proj-a/a-round"
CHECK = shlex.join([sys.executable, "-c", "print('the check passed')"])
POINTER = "comms:1234567"
LOCK = "refs/rounds/finishing/main"
# A process that uses its working folder until its stdin closes, printing its own pid
# first: on Windows a venv's python.exe runs the interpreter as its child, and the child
# is the one using the folder.
USES_ITS_DIR = "import os, sys; print(os.getpid(), flush=True); sys.stdin.read()"

@pytest.fixture
def repo(git_repo, tmp_path):
    from moxi import cli

    assert cli.main(["projects", "add", "proj-a", "--root", str(git_repo.root)]) == 0
    assert cli.main(["projects", "set", "proj-a", "--notes-root",
                     str(tmp_path / "notes")]) == 0
    # The merge commit finish makes is the user's own, so the repo names who they are.
    git_repo.git("config", "user.name", "coder")
    git_repo.git("config", "user.email", "coder@example.test")
    return git_repo


@pytest.fixture
def configure(write_config):
    """`configure(extra="")`: the config with proj-a's check and `extra` keys of its
    table."""

    def write(extra: str = "") -> None:
        write_config(f"[projects.proj-a]\ncheck = {{ command = {json.dumps(CHECK)} }}\n"
                     f"{extra}\n")

    write()
    return write


def verify(path: Path, head: str) -> None:
    report = path.parent / "build-report.md"
    report.write_text("# report\n\n```toml rounds\n" + "\n".join([
        'kind = "build"', 'verdict = "publishable"', 'pass = "full"', 'by = "critic"',
        'model = "a-model"', 'stage = "B1"', f'head = "{head}"']) + "\n```\n",
        encoding="utf-8")
    verdicts.record(ROUND, report, by="critic")


@pytest.fixture
def ready(repo, configure, tmp_path, capsys):
    """The round in `fix` with B1 on `work`, one commit of work, checked and verified at
    its tip: every gate holds. Returns `(log, worktree repo)`."""
    opening.open_round(ROUND, description="a placeholder round", by="planner",
                       flow="fix")
    path = log.round_log(tmp_path / "notes" / "rounds" / "a-round")
    log.append(path, "stage.declared",
               {"id": "B1", "repo": "proj-a", "branch": "work", "after": []},
               by="planner")
    made = commissioning.commission(ROUND, "B1", builder="coder", by="planner")
    tree = type(repo)(made.worktree)
    tree.commit("work.txt", "the work")
    checking.check(ROUND, "B1", by="coder")
    verify(path, tree.rev())
    capsys.readouterr()
    return path, tree


def events(path: Path) -> list[dict]:
    return log.read_round(path)


def of_type(path: Path, kind: str) -> list[dict]:
    return [one for one in events(path) if one["type"] == kind]


def lock_ref(repo) -> str | None:
    found = finish_lock.found(repo.root, LOCK)
    return None if found is None else found.oid


def build_of(path: Path) -> builds.Build:
    return builds.current(states.fold(events(path)), "B1")


def placed(event: dict, key: str) -> Path:
    """The place `event` records under `key`, read as rounds reads it: a record holds a
    place under the home folder from `~` (on Windows rounds' own folders are under it)."""
    return Path(places.expand_home(event[key]))


# -- a finish ------------------------------------------------------------------------------

def test_a_ready_build_is_merged_into_the_target_recorded_and_finished(ready, repo):
    path, tree = ready
    tip, before = tree.rev(), repo.rev("main")

    done = finishing.finish(ROUND, "B1", by="coder")

    landing = repo.rev("main")
    assert repo.git("rev-list", "--parents", "-n", "1", landing).split()[1:] == [before,
                                                                                  tip]
    assert repo.git("log", "-1", "--format=%s", landing) == "Merge branch 'work'"
    (action,) = of_type(path, "finish.action")
    assert {key: action[key] for key in ("stage", "tip", "id", "kind", "state", "landing",
                                         "target_before")} == {
        "stage": "B1", "tip": tip, "id": "merge", "kind": "merge", "state": "ok",
        "landing": landing, "target_before": before}
    (finished,) = of_type(path, "finish.done")
    assert (finished["stage"], finished["tip"]) == ("B1", tip)
    assert finished["n"] == action["n"] + 1
    assert done.tip == tip and done.event == finished
    assert [(one.id, one.state, one.landing) for one in done.actions] == [
        ("merge", "ok", landing)]
    assert build_of(path).ended == builds.FINISHED
    assert lock_ref(repo) is None and done.released
    assert repo.git("status", "--porcelain") == ""


def test_a_branch_that_moved_once_the_lock_was_taken_is_refused(ready, repo,
                                                                  monkeypatch):
    path, tree = ready
    tip = tree.rev()
    take = finish_lock.take

    def take_then_commit(*args, **kwargs):
        taken = take(*args, **kwargs)
        tree.commit("late.txt", "a commit made after the gates were read")
        return taken

    monkeypatch.setattr(finish_lock, "take", take_then_commit)
    with pytest.raises(Refused) as refused:
        finishing.finish(ROUND, "B1", by="coder")
    assert f"moved from {tip[:12]}" in refused.value.unmet[0].reason
    assert of_type(path, "finish.action") == [] and lock_ref(repo) is None


def test_a_target_moved_between_the_gates_and_the_lock_is_refused_with_nothing_written(
        ready, repo, monkeypatch):
    path, _tree = ready
    take = finish_lock.take
    before = path.read_bytes()

    def take_then_move_the_target(*args, **kwargs):
        taken = take(*args, **kwargs)
        # Another finish merged into the target while this one waited for the lock.
        repo.commit("other.txt", "another build's work on the target")
        return taken

    monkeypatch.setattr(finish_lock, "take", take_then_move_the_target)
    with pytest.raises(Refused) as refused:
        finishing.finish(ROUND, "B1", by="coder")

    assert [one.reason for one in refused.value.unmet if "does not hold the target main"
            in one.reason]
    assert refused.value.done == ()
    assert path.read_bytes() == before and lock_ref(repo) is None


def test_a_commit_made_while_the_actions_run_is_not_merged_and_the_finish_is_at_the_tip(
        ready, repo, monkeypatch):
    path, tree = ready
    tip = tree.rev()
    merge = finishing._merge

    def commit_then_merge(*args, **kwargs):
        tree.commit("late.txt", "a commit made after the gates were read")
        return merge(*args, **kwargs)

    monkeypatch.setattr(finishing, "_merge", commit_then_merge)

    done = finishing.finish(ROUND, "B1", by="coder")

    late = tree.rev()
    assert late != tip
    assert repo.git("rev-list", "--parents", "-n", "1", "main").split()[2] == tip
    assert repo.git("ls-tree", "--name-only", "main").split() == ["README", "work.txt"]
    (action,) = of_type(path, "finish.action")
    assert (action["tip"], action["state"]) == (tip, "ok")
    (finished,) = of_type(path, "finish.done")
    assert finished["tip"] == tip and done.tip == tip
    assert build_of(path).ended == builds.FINISHED and lock_ref(repo) is None
    # The branch moved past what was merged, so its folders and the late commit stay.
    assert tree.root.is_dir() and repo.rev("work") == late
    assert [(one.check, one.kind) for one in done.kept] == [("tip", "worktree"),
                                                            ("tip", "scratch")]
    assert done.removed == () and done.branch is None


def test_a_target_no_worktree_has_is_merged_in_a_checkout_added_and_removed(
        ready, repo):
    path, tree = ready
    repo.switch("elsewhere", new_from="main")
    before, tip = repo.rev("main"), tree.rev()

    finishing.finish(ROUND, "B1", by="coder")

    assert repo.rev("main") != before
    assert repo.git("rev-list", "--parents", "-n", "1", "main").split()[1:] == [
        before, tip]
    place = places.worktree_dir(own.load()) / "proj-a" / ".finish-main"
    assert not place.exists()
    assert str(place) not in repo.git("worktree", "list", "--porcelain")
    assert repo.git("symbolic-ref", "--short", "HEAD") == "elsewhere"


def _left_checkout(repo) -> Path:
    """A checkout of main at finish's own place, as a finish that stopped left it, with
    the root on another branch."""
    repo.switch("elsewhere", new_from="main")
    place = places.worktree_dir(own.load()) / "proj-a" / ".finish-main"
    repo.git("worktree", "add", "-q", str(place), "main")
    return place


def test_a_checkout_a_stopped_finish_left_is_merged_in_and_removed(ready, repo):
    _path, tree = ready
    place = _left_checkout(repo)
    before, tip = repo.rev("main"), tree.rev()

    done = finishing.finish(ROUND, "B1", by="coder")

    assert repo.git("rev-list", "--parents", "-n", "1", "main").split()[1:] == [
        before, tip]
    assert done.actions[0].kept is None
    assert not place.exists()
    assert str(place) not in repo.git("worktree", "list", "--porcelain")


def test_a_merge_found_made_in_a_left_checkout_is_followed_by_its_removal(ready, repo):
    path, _tree = ready
    place = _left_checkout(repo)
    type(repo)(place).merge("work")
    landing = repo.rev("main")

    done = finishing.finish(ROUND, "B1", by="coder")

    assert [(one.state, one.landing) for one in done.actions] == [("found", landing)]
    assert not place.exists()
    assert str(place) not in repo.git("worktree", "list", "--porcelain")
    assert builds.current(states.fold(log.read_round(path)), "B1").ended == (
        builds.FINISHED)


def test_a_left_checkout_holding_work_is_refused_and_kept(ready, repo):
    place = _left_checkout(repo)
    (place / "notes.txt").write_text("not committed\n", encoding="utf-8")

    with pytest.raises(Refused) as refused:
        finishing.finish(ROUND, "B1", by="coder")

    assert "uncommitted changes" in refused.value.unmet[0].reason
    assert (place / "notes.txt").read_text() == "not committed\n"


def test_the_target_checked_out_in_a_worktree_is_merged_there(ready, repo, tmp_path):
    _path, tree = ready
    repo.switch("elsewhere", new_from="main")
    repo.git("worktree", "add", "-q", str(tmp_path / "main-tree"), "main")

    finishing.finish(ROUND, "B1", by="coder")

    assert repo.git("-C", str(tmp_path / "main-tree"), "rev-parse", "HEAD") == repo.rev(
        "main")
    assert (tmp_path / "main-tree" / "work.txt").read_text() == "the work\n"


# -- refusals that write nothing ----------------------------------------------------------

def test_an_unmet_gate_refuses_before_the_lock_with_nothing_written(ready, repo):
    path, tree = ready
    tree.commit("more.txt", "a commit after the verdict")
    before, main_before = events(path), repo.rev("main")
    with pytest.raises(Refused) as refused:
        finishing.finish(ROUND, "B1", by="coder")
    assert any("verification" in one.reason for one in refused.value.unmet)
    assert events(path) == before and repo.rev("main") == main_before
    assert lock_ref(repo) is None


def test_a_second_finish_of_a_finished_build_is_refused_and_writes_nothing(ready, repo):
    path, _tree = ready
    finishing.finish(ROUND, "B1", by="coder")
    before, main_before = events(path), repo.rev("main")
    with pytest.raises(Refused) as refused:
        finishing.finish(ROUND, "B1", by="coder")
    # The finish was the round's last step, so its write closed the round.
    assert "is closed" in refused.value.unmet[0].reason
    assert events(path) == before and repo.rev("main") == main_before


def test_a_stage_with_no_build_is_refused(ready):
    with pytest.raises(Refused) as refused:
        finishing.finish(ROUND, "B2", by="coder")
    assert "B2 has no build" in refused.value.unmet[0].reason


def test_a_held_lock_refuses_naming_its_holder_with_nothing_written(ready, repo):
    path, _tree = ready
    held = finish_lock.take(repo.root, LOCK, by="pilot", name="proj-a/b-round",
                            stage="B3")
    before = events(path)
    with pytest.raises(Refused) as refused:
        finishing.finish(ROUND, "B1", by="coder")
    assert held.holder.line in refused.value.unmet[0].reason
    assert events(path) == before and lock_ref(repo) == held.blob


def test_a_repos_lock_ref_is_the_lock_finish_takes(ready, repo, configure):
    path, _tree = ready
    configure('lock_ref = "refs/locks/merging"')
    repo.git("update-ref", "refs/locks/merging", repo.rev("main"))
    with pytest.raises(Refused) as refused:
        finishing.finish(ROUND, "B1", by="coder")
    assert "refs/locks/merging" in refused.value.unmet[0].reason
    assert of_type(path, "finish.action") == []


def test_a_dead_holders_lock_is_cleared_and_recorded_before_the_merge(ready, repo):
    path, _tree = ready
    line = finish_lock.holder("pilot", ROUND, "B1", at="2026-01-02T03:04:05Z",
                              pid=dead_pid()).line
    blob = _blob(repo, line + "\n")
    repo.git("update-ref", LOCK, blob)

    done = finishing.finish(ROUND, "B1", by="coder")

    assert done.cleared == line
    kinds = [one["type"] for one in events(path)][-4:]
    assert kinds == ["lock.cleared", "finish.action", "finish.done", "round.closed"]
    (cleared,) = of_type(path, "lock.cleared")
    assert (cleared["ref"], cleared["holder"]) == (LOCK, line)
    assert lock_ref(repo) is None


def test_a_conflict_is_aborted_and_refused_with_the_way_through(ready, repo, configure):
    path, tree = ready
    configure('check_on = "tip"')
    assert main(["rule", ROUND, "skip:verify-build:B1", "--record", POINTER,
                 "--as", "planner"]) == 0
    repo.commit("work.txt", "the target's own text")
    before, main_before = events(path), repo.rev("main")

    with pytest.raises(Refused) as refused:
        finishing.finish(ROUND, "B1", by="coder")

    (unmet,) = refused.value.unmet
    assert "conflicts; the merge was aborted" in unmet.reason
    assert unmet.fix.startswith("git merge --no-ff main in the build's worktree")
    assert events(path) == before and repo.rev("main") == main_before
    assert repo.git("status", "--porcelain") == "" and lock_ref(repo) is None


def test_a_target_checkout_with_changes_is_refused_and_kept(ready, repo):
    path, _tree = ready
    (repo.root / "README").write_text("changed by hand\n", encoding="utf-8")
    with pytest.raises(Refused) as refused:
        finishing.finish(ROUND, "B1", by="coder")
    assert "holds uncommitted changes" in refused.value.unmet[0].reason
    assert of_type(path, "finish.action") == []
    assert (repo.root / "README").read_text() == "changed by hand\n"


# -- a failed merge, and the gates after it ------------------------------------------------

def hook(repo, name: str, code: int) -> None:
    script = repo.root / ".git" / "hooks" / name
    script.parent.mkdir(exist_ok=True)
    script.write_text(f"#!/bin/sh\necho 'the {name} hook says no'\nexit {code}\n",
                      encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)


def test_a_failed_merge_is_recorded_undoes_nothing_and_runs_again(ready, repo):
    path, tree = ready
    hook(repo, "pre-merge-commit", 1)
    main_before = repo.rev("main")

    with pytest.raises(finishing.ActionFailed) as failed:
        finishing.finish(ROUND, "B1", by="coder")

    assert failed.value.exit_code == 1 and "hook says no" in failed.value.output
    (action,) = of_type(path, "finish.action")
    assert (action["id"], action["state"], action["tip"]) == ("merge", "failed",
                                                             tree.rev())
    assert action["landing"] is None
    assert of_type(path, "finish.done") == []
    assert repo.rev("main") == main_before and repo.git("status", "--porcelain") == ""
    assert lock_ref(repo) is None
    assert build_of(path).ended is None

    hook(repo, "pre-merge-commit", 0)
    done = finishing.finish(ROUND, "B1", by="coder")
    assert [one["state"] for one in of_type(path, "finish.action")] == ["failed", "ok"]
    assert done.event is not None and build_of(path).ended == builds.FINISHED


def test_a_gate_the_log_no_longer_meets_after_the_merge_refuses_and_a_rerun_merges_once(
        ready, repo, monkeypatch):
    path, tree = ready
    merge = finishing._merge

    def merge_then_ask(*args, **kwargs):
        made = merge(*args, **kwargs)
        asking.ask(ROUND, "Is the merge what was wanted?", by="scout",
                   origin="comms:1234568")
        return made

    monkeypatch.setattr(finishing, "_merge", merge_then_ask)
    with pytest.raises(Refused) as refused:
        finishing.finish(ROUND, "B1", by="coder")
    assert any("Q1" in one.reason for one in refused.value.unmet)
    (action,) = of_type(path, "finish.action")
    assert action["state"] == "ok"
    assert of_type(path, "finish.done") == [] and lock_ref(repo) is None
    landing = repo.rev("main")

    monkeypatch.setattr(finishing, "_merge", merge)
    assert main(["rule", ROUND, "withdraw:Q1", "--record", POINTER, "--as",
                 "planner"]) == 0
    done = finishing.finish(ROUND, "B1", by="coder")
    assert [(one.state, one.ran) for one in done.actions] == [("ok", False)]
    assert len(of_type(path, "finish.action")) == 1 and repo.rev("main") == landing
    assert done.event is not None


def test_a_refusal_after_the_merge_names_what_was_done_and_that_it_reads_merged(
        ready, repo, monkeypatch, capsys):
    merge = finishing._merge

    def merge_then_ask(*args, **kwargs):
        made = merge(*args, **kwargs)
        asking.ask(ROUND, "Is the merge what was wanted?", by="scout",
                   origin="comms:1234568")
        return made

    monkeypatch.setattr(finishing, "_merge", merge_then_ask)
    capsys.readouterr()

    assert main(["finish", ROUND, "B1"]) == 3

    landing = repo.rev("main")
    err = capsys.readouterr().err
    assert "nothing written" not in err
    assert "refused after acting; what was done is recorded and stands:" in err
    assert f"done: merge (merge): ok, {landing[:12]} on the target" in err
    assert f"done: B1 reads merged, not finished: its merge {landing[:12]} is on main" in err
    assert "Q1" in err.split("refused because:")[1]


def test_a_refusal_after_a_dead_holder_was_cleared_names_the_clearing(ready, repo,
                                                                      monkeypatch):
    path, tree = ready
    line = finish_lock.holder("pilot", ROUND, "B1", at="2026-01-02T03:04:05Z",
                              pid=dead_pid()).line
    repo.git("update-ref", LOCK, _blob(repo, line + "\n"))
    take = finish_lock.take

    def take_then_commit(*args, **kwargs):
        taken = take(*args, **kwargs)
        tree.commit("late.txt", "a commit made after the gates were read")
        return taken

    monkeypatch.setattr(finish_lock, "take", take_then_commit)
    with pytest.raises(Refused) as refused:
        finishing.finish(ROUND, "B1", by="coder")

    assert refused.value.done == (
        "the finish lock of a holder that had ended was cleared",)
    assert "nothing written" not in str(refused.value)
    assert len(of_type(path, "lock.cleared")) == 1 and of_type(path, "finish.action") == []


def test_the_cleanup_removes_both_dirs_and_deletes_the_merged_branch(ready, repo):
    path, tree = ready
    made = checking.commission_of(states.fold(events(path)), build_of(path))
    worktree, scratch = placed(made, "worktree"), placed(made, "scratch")

    done = finishing.finish(ROUND, "B1", by="coder")

    assert done.event is not None
    assert (done.removed, done.kept, done.branch) == ((worktree, scratch), (), "deleted")
    assert not worktree.exists() and not scratch.exists()
    assert repo.git("branch", "--list", "work") == ""


def test_a_process_in_the_builds_scratch_refuses_the_finish_with_nothing_written(
        ready, repo):
    # Core refused to land a build while a live process used its scratch folder.
    path, _tree = ready
    scratch = placed(checking.commission_of(states.fold(events(path)), build_of(path)),
                 "scratch")
    before = events(path)
    proc = subprocess.Popen([sys.executable, "-c", USES_ITS_DIR], cwd=scratch,
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    own_pid = int(proc.stdout.readline())
    try:
        with pytest.raises(Refused) as caught:
            finishing.finish(ROUND, "B1", by="coder")
    finally:
        proc.communicate(timeout=30)

    [unmet] = caught.value.unmet
    assert unmet.reason.startswith(
        f"B1's scratch folder {scratch} is in use by pid {own_pid} (")
    assert unmet.fix == ("end that process or leave the folder, then: moxi rounds "
                         f"finish {ROUND} B1")
    assert events(path) == before and lock_ref(repo) is None
    assert finishing.finish(ROUND, "B1", by="coder").event is not None


def test_a_scratch_that_came_into_use_after_the_gate_stays_for_the_sweep(
        ready, repo, monkeypatch, capsys):
    # Core's land, once past its gate, left a scratch folder that came into use for the
    # sweep and said so; the worktree and the branch went as before.
    path, _tree = ready
    made = checking.commission_of(states.fold(events(path)), build_of(path))
    worktree, scratch = placed(made, "worktree"), placed(made, "scratch")
    gate, started = sweeping.scratch_gate, []

    def gate_then_use(*args, **kwargs):
        found = gate(*args, **kwargs)
        proc = subprocess.Popen([sys.executable, "-c", USES_ITS_DIR], cwd=scratch,
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        proc.own_pid = int(proc.stdout.readline())
        started.append(proc)
        return found

    monkeypatch.setattr(sweeping, "scratch_gate", gate_then_use)
    try:
        assert main(["finish", ROUND, "B1"]) == 0
    finally:
        for proc in started:
            proc.communicate(timeout=30)

    [proc] = started
    assert not worktree.exists() and scratch.is_dir()
    assert repo.git("branch", "--list", "work") == ""
    [line] = [one for one in capsys.readouterr().out.splitlines()
              if one.startswith(f"kept {scratch}:")]
    assert line.startswith(f"kept {scratch}: D: a running process came to use it after "
                           f"the scratch gate (pid {proc.own_pid} (")


def test_the_cleanup_writes_the_scratch_dirs_trash_entry_to_the_sweeps_log(ready):
    path, _tree = ready
    made = checking.commission_of(states.fold(events(path)), build_of(path))

    finishing.finish(ROUND, "B1", by="coder")

    found = files.read_records(sweeping.log_file())
    assert [(one["act"], one["entry"], one["proven"]) for one in found] == [
        ("renamed", made["build"], ["A", "B"]), ("deleted", made["build"], ["A", "B"])]


class _Stopped(BaseException):
    """A stop between the trash rename and its delete, as a killed process leaves it."""


def test_a_trash_entry_a_stopped_finish_left_is_deleted_by_the_next_armed_sweep(
        ready, configure, monkeypatch):
    path, _tree = ready
    made = checking.commission_of(states.fold(events(path)), build_of(path))

    def stop(_target_dir):
        raise _Stopped

    with monkeypatch.context() as patched:
        patched.setattr(removal, "delete", stop)
        with pytest.raises(_Stopped):
            finishing.finish(ROUND, "B1", by="coder")
    entry = removal.trash_dir() / made["build"]
    assert entry.is_dir() and not placed(made, "scratch").exists()
    assert len(of_type(path, "finish.done")) == 1

    configure("\n[sweep]\narmed = true")
    swept = sweeping.run(remove=True)

    assert [(one.name, one.fate) for one in swept.trash] == [(made["build"], "deleted")]
    assert not os.path.lexists(entry)


def test_with_keep_branch_the_cleanup_removes_the_dirs_and_keeps_the_branch(
        ready, repo, configure):
    configure("keep_branch = true")
    done = finishing.finish(ROUND, "B1", by="coder")
    assert len(done.removed) == 2 and done.branch is None
    assert repo.git("branch", "--list", "work").strip().endswith("work")


def test_the_command_says_what_the_cleanup_removed_and_kept(ready, repo, capsys):
    path, tree = ready
    made = checking.commission_of(states.fold(events(path)), build_of(path))
    exclude = repo.root / ".git" / "info" / "exclude"
    exclude.parent.mkdir(parents=True, exist_ok=True)
    exclude.write_text(".env\n", encoding="utf-8")
    (tree.root / ".env").write_text("TOKEN=kept only here\n", encoding="utf-8")

    assert main(["finish", ROUND, "B1", "--as", "coder"]) == 0
    lines = capsys.readouterr().out.splitlines()

    assert f"removed {placed(made, 'scratch')}" in lines
    (kept,) = [line for line in lines if line.startswith("kept ")]
    assert kept.startswith(f"kept {placed(made, 'worktree')}: B3: ") and "(.env)" in kept
    assert (tree.root / ".env").is_file()
    assert [line for line in lines if line.startswith("branch: ")] == [
        "branch: kept: its worktree was kept, and git deletes no branch a worktree has "
        "checked out"]


# -- the command ---------------------------------------------------------------------------

def test_the_command_finishes_by_name_and_says_what_it_did(ready, repo, capsys):
    _path, tree = ready
    tip = tree.rev()
    assert main(["finish", ROUND, "B1", "--as", "coder"]) == 0
    out = capsys.readouterr().out
    assert f"finish {ROUND} B1 at {tip[:12]}" in out
    assert f"merge: ok: {repo.rev('main')[:12]} on the target" in out
    assert "finished B1" in out


def test_the_command_in_the_builds_worktree_finds_its_build(ready, repo, monkeypatch,
                                                            capsys):
    _path, tree = ready
    monkeypatch.chdir(tree.root)
    assert main(["finish", "--as", "coder"]) == 0
    assert "finished B1" in capsys.readouterr().out


def test_the_command_exits_3_on_a_refusal_and_1_on_a_failed_action(ready, repo, capsys):
    path, tree = ready
    tree.commit("more.txt", "a commit after the verdict")
    assert main(["finish", ROUND, "B1"]) == 3
    verify(path, tree.rev())
    checking.check(ROUND, "B1", by="coder")
    hook(repo, "pre-merge-commit", 1)
    capsys.readouterr()
    assert main(["finish", ROUND, "B1"]) == 1
    assert "hook says no" in capsys.readouterr().out


def test_next_names_the_finish_command_once_the_gates_hold(ready):
    found = next_step.read(ROUND)
    (group,) = [one for one in found["groups"] if one["step"] == "finish"]
    assert group["then"] == [f"moxi rounds finish {ROUND} B1, or moxi rounds finish in "
                             "the build's worktree"]


# -- helpers --------------------------------------------------------------------------------

def dead_pid() -> int:
    done = subprocess.Popen([sys.executable, "-c", "pass"])
    done.wait()
    return done.pid


def _blob(repo, text: str) -> str:
    done = subprocess.run(["git", "-C", str(repo.root), "hash-object", "-w", "--stdin"],
                          input=text, capture_output=True, text=True, env=repo.env(),
                          check=True)
    return done.stdout.strip()


"""`finish`'s push and command actions: a push of the target at the landing after a
merge, or of the branch at the tip without one; a command run in the build's worktree
with its placeholders filled and `started` recorded before it; a failed action and its
rerun, with the actions before it not run again; records kept by id when the list
changes; and a command that started with no result refused."""

from __future__ import annotations

import json
import shlex
import sys
from pathlib import Path

import pytest

from moxi_rounds import builds, checking, commissioning, finishing, log, opening, verdicts
from moxi_rounds import state as states
from moxi_rounds.errors import Refused

ROUND = "proj-a/a-round"
CHECK = shlex.join([sys.executable, "-c", "print('the check passed')"])


@pytest.fixture
def repo(git_repo, tmp_path):
    from moxi import cli

    assert cli.main(["projects", "add", "proj-a", "--root", str(git_repo.root)]) == 0
    assert cli.main(["projects", "set", "proj-a", "--notes-root",
                     str(tmp_path / "notes")]) == 0
    # The merge commit finish makes is the user's own, so the repo names who they are.
    git_repo.git("config", "user.name", "coder")
    git_repo.git("config", "user.email", "coder@example.test")
    remote = tmp_path / "remote.git"
    git_repo.git("init", "-q", "--bare", str(remote))
    git_repo.git("remote", "add", "origin", str(remote))
    return git_repo


@pytest.fixture
def configure(write_config):
    """`configure(*actions)`: proj-a's check, and its finish set to `actions`, each a
    TOML inline table."""

    def write(*actions: str) -> None:
        finish = f"finish = [ {', '.join(actions)} ]\n" if actions else ""
        write_config(f"[projects.proj-a]\ncheck = {{ command = {json.dumps(CHECK)} }}\n"
                     f"{finish}")

    write()
    return write


@pytest.fixture
def ready(repo, configure, tmp_path):
    """B1 built, checked and verified at its tip. Gives `(log, worktree repo)`."""
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
    report = path.parent / "build-report.md"
    report.write_text("# report\n\n```toml rounds\n" + "\n".join([
        'kind = "build"', 'verdict = "publishable"', 'pass = "full"', 'by = "critic"',
        'model = "a-model"', 'stage = "B1"', f'head = "{tree.rev()}"']) + "\n```\n",
        encoding="utf-8")
    verdicts.record(ROUND, report, by="critic")
    return path, tree


def command(action_id: str, code: int, said: Path, *, extra: str = "") -> str:
    """A command action that writes its words and where it ran to `said`, prints a
    line, and exits `code`."""
    script = ("import json, os, sys, pathlib; "
              "pathlib.Path(sys.argv[1]).write_text(json.dumps([sys.argv[2:], os.getcwd()])); "
              f"print('the command spoke'); sys.exit({code})")
    argv = [sys.executable, "-c", script, str(said), "{round}", "{stage}", "{branch}",
            "{tip}", "{into}"]
    return f'{{ do = "command", id = "{action_id}", argv = {json.dumps(argv)}{extra} }}'


def actions_of(path: Path) -> list[tuple[str, str]]:
    return [(one["id"], one["state"]) for one in log.read_round(path)
            if one["type"] == "finish.action"]


def ended(path: Path) -> str | None:
    return builds.current(states.fold(log.read_round(path)), "B1").ended


def remote_rev(repo, branch: str) -> str:
    return repo.git("--git-dir", str(repo.root.parent / "remote.git"), "rev-parse",
                    f"refs/heads/{branch}")


# -- a push ---------------------------------------------------------------------------------

def test_a_push_after_the_merge_pushes_the_target_at_its_landing(ready, repo, configure):
    path, _tree = ready
    configure('{ do = "merge" }', '{ do = "push", remote = "origin" }')

    done = finishing.finish(ROUND, "B1", by="coder")

    assert remote_rev(repo, "main") == repo.rev("main") == done.actions[0].landing
    assert actions_of(path) == [("merge", "ok"), ("push", "ok")]
    assert ended(path) == builds.FINISHED


def test_a_push_with_no_merge_pushes_the_branch_at_the_tip(ready, repo, configure):
    path, tree = ready
    configure('{ do = "push", remote = "origin" }')
    main_before = repo.rev("main")

    finishing.finish(ROUND, "B1", by="coder")

    assert remote_rev(repo, "work") == tree.rev()
    assert repo.rev("main") == main_before
    assert actions_of(path) == [("push", "ok")] and ended(path) == builds.FINISHED


def test_with_no_merge_the_cleanup_removes_only_the_scratch_dir(ready, repo,
                                                                   configure):
    _path, tree = ready
    configure('{ do = "push", remote = "origin" }')

    done = finishing.finish(ROUND, "B1", by="coder")

    (scratch,) = done.removed
    assert not scratch.exists() and tree.root.is_dir()
    assert [(one.check, one.kind) for one in done.kept] == [("no merge", "worktree")]
    assert done.branch is None and repo.rev("work") == tree.rev()


def test_a_push_git_refuses_is_recorded_failed(ready, repo, configure):
    path, _tree = ready
    configure('{ do = "merge" }', '{ do = "push", remote = "nowhere" }')

    with pytest.raises(finishing.ActionFailed) as failed:
        finishing.finish(ROUND, "B1", by="coder")

    assert failed.value.id == "push" and "nowhere" in failed.value.output
    assert actions_of(path) == [("merge", "ok"), ("push", "failed")]
    assert ended(path) is None
    landing = repo.rev("main")
    assert failed.value.done == (
        f"merge (merge): ok, {landing[:12]} on the target",
        f"B1 reads merged, not finished: its merge {landing[:12]} is on main")
    assert "done before it, recorded: merge (merge): ok" in failed.value.reason


def test_show_names_each_finish_action_s_state_until_the_build_is_finished(
        ready, repo, configure, capsys):
    from moxi_rounds import api
    from moxi_rounds.cli import main

    path, _tree = ready
    configure('{ do = "merge" }', '{ do = "push", remote = "nowhere" }')
    with pytest.raises(finishing.ActionFailed):
        finishing.finish(ROUND, "B1", by="coder")
    landing = repo.rev("main")
    capsys.readouterr()

    assert main(["show", ROUND]) == 0

    out = capsys.readouterr().out
    merged, pushed = (one["n"] for one in log.read_round(path)
                      if one["type"] == "finish.action")
    assert f"finish: merge ok, landing {landing[:12]} (line {merged})" in out
    assert f"finish: push failed (line {pushed})" in out
    assert f"finish.action  B1 merge: ok, landing {landing[:12]}" in out
    assert "finish.action  B1 push: failed" in out
    (row,) = api.round_data(ROUND)["builds"]
    assert [(one["id"], one["state"], one["landing"]) for one in row["finish"]] == [
        ("merge", "ok", landing), ("push", "failed", None)]

    configure('{ do = "merge" }')
    finishing.finish(ROUND, "B1", by="coder")
    (row,) = api.round_data(ROUND)["builds"]
    assert row["finish"] == []


def _remote_away(repo):
    """Take the remote away, so a push to it fails; gives a function that brings it
    back."""
    remote = repo.root.parent / "remote.git"
    away = remote.with_name("remote-away.git")
    remote.rename(away)
    return lambda: away.rename(remote)


def _push_on_from_a_clone(repo, tmp_path, name: str) -> str:
    """Clone the repo, commit `name` on its main, push that main to the remote, and give
    the commit: the remote's main moves on past what the repo's main holds, to a commit
    the repo has never seen."""
    other = tmp_path / f"clone-{name}"
    type(repo)(tmp_path).git("clone", "-q", str(repo.root), str(other))
    clone = type(repo)(other)
    pushed = clone.commit(name)
    clone.git("push", "-q", str(repo.root.parent / "remote.git"), "main:main")
    return pushed


def test_a_push_rerun_passes_when_the_remote_target_moved_on_past_the_landing(
        ready, repo, configure, tmp_path):
    path, _tree = ready
    configure('{ do = "merge" }', '{ do = "push", remote = "origin" }')
    repo.git("push", "-q", "origin", "main")
    back = _remote_away(repo)
    with pytest.raises(finishing.ActionFailed):
        finishing.finish(ROUND, "B1", by="coder")
    back()
    landing = repo.rev("main")
    later = _push_on_from_a_clone(repo, tmp_path, "later.txt")

    done = finishing.finish(ROUND, "B1", by="coder")

    push = done.actions[-1]
    assert (push.kind, push.state, push.ran) == ("push", "ok", True)
    assert "nothing was pushed" in push.output and landing[:12] in push.output
    assert remote_rev(repo, "main") == later
    assert actions_of(path) == [("merge", "ok"), ("push", "failed"), ("push", "ok")]
    assert ended(path) == builds.FINISHED


def test_a_push_after_a_hand_merge_passes_when_the_remote_target_moved_on(
        ready, repo, configure, tmp_path):
    path, tree = ready
    configure('{ do = "merge" }', '{ do = "push", remote = "origin" }')
    landing = repo.merge(tree.rev())
    repo.git("push", "-q", "origin", "main")
    later = _push_on_from_a_clone(repo, tmp_path, "later.txt")

    done = finishing.finish(ROUND, "B1", by="coder")

    assert [(one.kind, one.state) for one in done.actions] == [("merge", "found"),
                                                               ("push", "ok")]
    assert done.actions[0].landing == landing
    assert remote_rev(repo, "main") == later
    assert ended(path) == builds.FINISHED


def test_a_push_never_moves_the_remote_target_off_a_line_that_lacks_the_landing(
        ready, repo, configure, tmp_path):
    path, _tree = ready
    configure('{ do = "merge" }', '{ do = "push", remote = "origin" }')
    repo.git("push", "-q", "origin", "main")
    back = _remote_away(repo)
    with pytest.raises(finishing.ActionFailed):
        finishing.finish(ROUND, "B1", by="coder")
    back()
    # Another line moves the remote's main on from the base the landing merged onto.
    other = tmp_path / "clone-parted"
    type(repo)(tmp_path).git("clone", "-q", "--branch", "main",
                             str(repo.root.parent / "remote.git"), str(other))
    parted = type(repo)(other).commit("parted.txt")
    type(repo)(other).git("push", "-q", "origin", "main")

    with pytest.raises(finishing.ActionFailed) as failed:
        finishing.finish(ROUND, "B1", by="coder")

    assert failed.value.id == "push" and "rejected" in failed.value.output
    assert remote_rev(repo, "main") == parted
    assert actions_of(path) == [("merge", "ok"), ("push", "failed"), ("push", "failed")]
    assert ended(path) is None


def _refs(repo) -> dict[str, str]:
    listed = repo.git("for-each-ref", "--format=%(refname) %(objectname)")
    return dict(line.split() for line in listed.splitlines())


def test_reading_the_remote_target_changes_only_its_remote_tracking_branch(
        repo, tmp_path):
    repo.git("push", "-q", "origin", "main")
    landing = repo.rev("main")
    later = _push_on_from_a_clone(repo, tmp_path, "later.txt")
    before = _refs(repo)

    there = finishing._remote_holds(repo.root, "origin", "main", landing)

    after = _refs(repo)
    assert there == later
    assert before["refs/remotes/origin/main"] == landing
    assert {name: rev for name, rev in after.items() if before.get(name) != rev} == {
        "refs/remotes/origin/main": later}
    assert before.keys() == after.keys()
    assert not (repo.root / ".git" / "FETCH_HEAD").exists()


# -- a command ------------------------------------------------------------------------------

def test_a_command_runs_in_the_worktree_with_its_placeholders_after_its_start_is_recorded(
        ready, repo, configure, tmp_path, capfd):
    path, tree = ready
    said = tmp_path / "said.txt"
    configure('{ do = "merge" }', command("pr", 0, said))
    tip, place = tree.rev(), tree.root.resolve()

    finishing.finish(ROUND, "B1", by="coder")

    words, cwd = json.loads(said.read_text())
    assert words == [ROUND, "B1", "work", tip, "main"]
    assert Path(cwd) == place
    assert "the command spoke" in capfd.readouterr().out
    assert actions_of(path) == [("merge", "ok"), ("pr", "started"), ("pr", "ok")]
    assert ended(path) == builds.FINISHED


def test_a_failing_second_action_is_recorded_and_its_rerun_leaves_the_first_alone(
        ready, repo, configure, tmp_path):
    path, _tree = ready
    said = tmp_path / "said.txt"
    configure('{ do = "merge" }', command("deploy", 3, said))

    with pytest.raises(finishing.ActionFailed) as failed:
        finishing.finish(ROUND, "B1", by="coder")
    assert failed.value.id == "deploy" and "exited 3" in failed.value.output
    landing = repo.rev("main")
    assert actions_of(path) == [("merge", "ok"), ("deploy", "started"),
                                ("deploy", "failed")]
    assert ended(path) is None

    # The command fixed in config under the same id, with an action put before both:
    # each record stays with its id.
    configure(command("first", 0, tmp_path / "first.txt"), '{ do = "merge" }',
              command("deploy", 0, said))
    done = finishing.finish(ROUND, "B1", by="coder")

    assert [(one.id, one.state, one.ran) for one in done.actions] == [
        ("first", "ok", True), ("merge", "ok", False), ("deploy", "ok", True)]
    assert repo.rev("main") == landing
    assert ended(path) == builds.FINISHED


class Stopped(BaseException):
    """The finish stopped while a command ran."""


def test_a_command_that_started_with_no_result_is_refused_naming_when(
        ready, repo, configure, tmp_path, monkeypatch):
    path, _tree = ready
    configure('{ do = "merge" }', command("deploy", 0, tmp_path / "said.txt"))

    real = finishing._command

    def stop(*args, **kwargs):
        raise Stopped

    monkeypatch.setattr(finishing, "_command", stop)
    with pytest.raises(Stopped):
        finishing.finish(ROUND, "B1", by="coder")
    monkeypatch.setattr(finishing, "_command", real)
    (started,) = [one for one in log.read_round(path)
                  if one["type"] == "finish.action" and one["state"] == "started"]
    before = log.read_round(path)

    with pytest.raises(Refused) as refused:
        finishing.finish(ROUND, "B1", by="coder")

    (unmet,) = refused.value.unmet
    assert "deploy" in unmet.reason and started["at"] in unmet.reason
    assert log.read_round(path) == before
    assert not (tmp_path / "said.txt").exists()


def test_a_command_whose_worktree_is_gone_is_refused_before_anything(
        ready, repo, configure, tmp_path):
    path, tree = ready
    configure('{ do = "merge" }', command("deploy", 0, tmp_path / "said.txt"))
    repo.git("worktree", "remove", str(tree.root))
    before = log.read_round(path)

    with pytest.raises(Refused) as refused:
        finishing.finish(ROUND, "B1", by="coder")

    assert any("worktree, which is gone" in one.reason for one in refused.value.unmet)
    assert log.read_round(path) == before

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
    opening.open_round(ROUND, description="a placeholder round", actor="planner",
                       flow="fix")
    path = log.round_log(tmp_path / "notes" / "rounds" / "a-round")
    log.append(path, "stage.declared",
               {"id": "B1", "repo": "proj-a", "branch": "work", "after": []},
               actor="planner")
    made = commissioning.commission(ROUND, "B1", builder="coder", actor="planner")
    tree = type(repo)(made.worktree)
    tree.commit("work.txt", "the work")
    checking.check(ROUND, "B1", actor="coder")
    report = path.parent / "build-report.md"
    report.write_text("# report\n\n```toml rounds\n" + "\n".join([
        'kind = "build"', 'verdict = "publishable"', 'pass = "full"', 'by = "critic"',
        'model = "a-model"', 'stage = "B1"', f'head = "{tree.rev()}"']) + "\n```\n",
        encoding="utf-8")
    verdicts.record(ROUND, report, actor="critic")
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

    done = finishing.finish(ROUND, "B1", actor="coder")

    assert remote_rev(repo, "main") == repo.rev("main") == done.actions[0].landing
    assert actions_of(path) == [("merge", "ok"), ("push", "ok")]
    assert ended(path) == builds.FINISHED


def test_a_push_with_no_merge_pushes_the_branch_at_the_tip(ready, repo, configure):
    path, tree = ready
    configure('{ do = "push", remote = "origin" }')
    main_before = repo.rev("main")

    finishing.finish(ROUND, "B1", actor="coder")

    assert remote_rev(repo, "work") == tree.rev()
    assert repo.rev("main") == main_before
    assert actions_of(path) == [("push", "ok")] and ended(path) == builds.FINISHED


def test_a_push_git_refuses_is_recorded_failed(ready, repo, configure):
    path, _tree = ready
    configure('{ do = "merge" }', '{ do = "push", remote = "nowhere" }')

    with pytest.raises(finishing.ActionFailed) as failed:
        finishing.finish(ROUND, "B1", actor="coder")

    assert failed.value.id == "push" and "nowhere" in failed.value.output
    assert actions_of(path) == [("merge", "ok"), ("push", "failed")]
    assert ended(path) is None


# -- a command ------------------------------------------------------------------------------

def test_a_command_runs_in_the_worktree_with_its_placeholders_after_its_start_is_recorded(
        ready, repo, configure, tmp_path, capfd):
    path, tree = ready
    said = tmp_path / "said.txt"
    configure('{ do = "merge" }', command("pr", 0, said))

    finishing.finish(ROUND, "B1", actor="coder")

    words, cwd = json.loads(said.read_text())
    assert words == [ROUND, "B1", "work", tree.rev(), "main"]
    assert Path(cwd).resolve() == tree.root.resolve()
    assert "the command spoke" in capfd.readouterr().out
    assert actions_of(path) == [("merge", "ok"), ("pr", "started"), ("pr", "ok")]
    assert ended(path) == builds.FINISHED


def test_a_failing_second_action_is_recorded_and_its_rerun_leaves_the_first_alone(
        ready, repo, configure, tmp_path):
    path, _tree = ready
    said = tmp_path / "said.txt"
    configure('{ do = "merge" }', command("deploy", 3, said))

    with pytest.raises(finishing.ActionFailed) as failed:
        finishing.finish(ROUND, "B1", actor="coder")
    assert failed.value.id == "deploy" and "exited 3" in failed.value.output
    landing = repo.rev("main")
    assert actions_of(path) == [("merge", "ok"), ("deploy", "started"),
                                ("deploy", "failed")]
    assert ended(path) is None

    # The command fixed in config under the same id, with an action put before both:
    # each record stays with its id.
    configure(command("first", 0, tmp_path / "first.txt"), '{ do = "merge" }',
              command("deploy", 0, said))
    done = finishing.finish(ROUND, "B1", actor="coder")

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
        finishing.finish(ROUND, "B1", actor="coder")
    monkeypatch.setattr(finishing, "_command", real)
    (started,) = [one for one in log.read_round(path)
                  if one["type"] == "finish.action" and one["state"] == "started"]
    before = log.read_round(path)

    with pytest.raises(Refused) as refused:
        finishing.finish(ROUND, "B1", actor="coder")

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
        finishing.finish(ROUND, "B1", actor="coder")

    assert any("worktree, which is gone" in one.reason for one in refused.value.unmet)
    assert log.read_round(path) == before

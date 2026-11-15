"""`finish` run again, and a merge found in git: a merge made by hand or by a finish
that stopped before its record is recorded `found` with the landing and the target
before it, and is not made again; a fast-forward is found with its tip; a hand merge
prints what it put on the target; and a rerun after the merge reads the gates against
the target as it was before it."""

from __future__ import annotations

import json
import shlex
import stat
import sys
from pathlib import Path

import pytest

from moxi_rounds import (asking, builds, checking, commissioning, finishing, log,
                         open_builds, opening, verdicts)
from moxi_rounds import state as states
from moxi_rounds.cli import main
from moxi_rounds.errors import Refused

ROUND = "proj-a/a-round"
OTHER = "proj-a/b-round"
CHECK = shlex.join([sys.executable, "-c", "print('the check passed')"])
POINTER = "comms:1234567"


@pytest.fixture
def repo(git_repo, tmp_path):
    from moxi import cli

    assert cli.main(["projects", "add", "proj-a", "--root", str(git_repo.root)]) == 0
    assert cli.main(["projects", "set", "proj-a", "--notes-root",
                     str(tmp_path / "notes")]) == 0
    # The merge commits made here are the user's own, so the repo names who they are.
    git_repo.git("config", "user.name", "coder")
    git_repo.git("config", "user.email", "coder@example.test")
    return git_repo


@pytest.fixture
def path(repo, write_config, tmp_path):
    """The round's log: the round in `fix` with B1 on `work` declared, and proj-a's
    check."""
    write_config(f"[projects.proj-a]\ncheck = {{ command = {json.dumps(CHECK)} }}\n")
    return opened(ROUND, "work", tmp_path)


def opened(name: str, branch: str, tmp_path) -> Path:
    opening.open_round(name, description="a placeholder round", by="planner",
                       flow="fix")
    found = log.round_log(tmp_path / "notes" / "rounds" / name.split("/")[1])
    log.append(found, "stage.declared",
               {"id": "B1", "repo": "proj-a", "branch": branch, "after": []},
               by="planner")
    return found


def built(repo, file: str, name: str = ROUND):
    """Commission B1 of `name`, commit `file` in its worktree and check it there: every
    gate holds but verification. Gives the worktree's repo."""
    made = commissioning.commission(name, "B1", builder="coder", by="planner")
    tree = type(repo)(made.worktree)
    tree.commit(file, f"the work in {file}")
    checking.check(name, "B1", by="coder")
    return tree


def verify(path: Path, head: str, name: str = ROUND) -> None:
    report = path.parent / f"build-report-{head[:8]}.md"
    report.write_text("# report\n\n```toml rounds\n" + "\n".join([
        'kind = "build"', 'verdict = "publishable"', 'pass = "full"', 'by = "critic"',
        'model = "a-model"', 'stage = "B1"', f'head = "{head}"']) + "\n```\n",
        encoding="utf-8")
    verdicts.record(name, report, by="critic")


@pytest.fixture
def ready(repo, path, capsys):
    """B1 built, checked and verified at its tip. Gives the worktree's repo."""
    tree = built(repo, "work.txt")
    verify(path, tree.rev())
    capsys.readouterr()
    return tree


def of_type(path: Path, kind: str) -> list[dict]:
    return [one for one in log.read_round(path) if one["type"] == kind]


def build_of(path: Path) -> builds.Build:
    return builds.current(states.fold(log.read_round(path)), "B1")


def status_of() -> builds.Status:
    return open_builds.of_round(ROUND, ["proj-a"])["B1"].status


def asked_during(monkeypatch, name: str) -> None:
    """Make `finishing.<name>` ask a question once it has done its part, so the gates
    read on the log after the actions refuse."""
    real = getattr(finishing, name)

    def then_ask(*args, **kwargs):
        made = real(*args, **kwargs)
        asking.ask(ROUND, "Is the merge what was wanted?", by="scout",
                   origin="comms:1234568")
        monkeypatch.setattr(finishing, name, real)
        return made

    monkeypatch.setattr(finishing, name, then_ask)


def withdraw(question: str = "Q1") -> None:
    assert main(["rule", ROUND, f"withdraw:{question}", "--record", POINTER, "--as",
                 "planner"]) == 0


# -- a hand merge ---------------------------------------------------------------------------

def test_a_hand_merge_is_found_recorded_and_finished_with_what_it_put_on_the_target(
        ready, repo, path, capsys):
    tip, before = ready.rev(), repo.rev("main")
    hand = repo.merge("work", "-m", "merged by hand")

    assert main(["finish", ROUND, "B1", "--as", "coder"]) == 0

    out = capsys.readouterr().out
    assert f"found hand merge {hand[:12]}: 1 file changed, 1 insertion(+)" in out
    assert f"merge: found: {hand[:12]} on the target, which was at {before[:12]}" in out
    assert "finished B1" in out
    assert repo.rev("main") == hand
    (action,) = of_type(path, "finish.action")
    assert {key: action[key] for key in ("tip", "id", "kind", "state", "landing",
                                         "target_before")} == {
        "tip": tip, "id": "merge", "kind": "merge", "state": "found", "landing": hand,
        "target_before": before}
    (done,) = of_type(path, "finish.done")
    assert done["tip"] == tip and build_of(path).ended == builds.FINISHED


def test_a_hand_merge_whose_branch_is_gone_is_finished_at_its_checked_head(
        ready, repo, path, capsys):
    head = ready.rev()
    hand = repo.merge("work", "-m", "merged by hand")
    repo.git("worktree", "remove", "--force", str(ready.root))
    repo.git("branch", "-D", "work")

    assert main(["finish", ROUND, "B1", "--as", "coder"]) == 0

    assert "finished B1" in capsys.readouterr().out
    (action,) = of_type(path, "finish.action")
    assert (action["tip"], action["state"], action["landing"]) == (head, "found", hand)
    assert build_of(path).ended == builds.FINISHED


def test_a_hand_merge_before_verification_is_refused_with_what_is_owed(repo, path):
    tree = built(repo, "work.txt")
    repo.merge("work", "-m", "merged by hand")
    before = log.read_round(path)

    with pytest.raises(Refused) as refused:
        finishing.finish(ROUND, "B1", by="coder")

    assert any("verdict" in one.fix and tree.rev()[:12] in one.reason
               for one in refused.value.unmet)
    assert log.read_round(path) == before
    assert status_of().state == builds.MERGED


def test_the_found_line_is_said_as_it_is_recorded_though_the_finish_is_refused_after(
        ready, repo, path, monkeypatch):
    hand = repo.merge("work", "-m", "merged by hand")
    asked_during(monkeypatch, "_found")
    said = []

    with pytest.raises(Refused):
        finishing.finish(ROUND, "B1", by="coder", on_action=said.append)

    assert [(one.state, one.landing, one.hand_merge) for one in said] == [
        ("found", hand, True)]
    assert "1 file changed" in said[0].output
    assert of_type(path, "finish.done") == []


# -- a fast-forward -------------------------------------------------------------------------

def test_a_fast_forward_is_found_with_its_tip_and_reads_merged_before_the_finish(
        ready, repo, path, monkeypatch):
    tip, base = ready.rev(), repo.rev("main")
    repo.git("merge", "-q", "--ff-only", "work")
    asked_during(monkeypatch, "_found")

    with pytest.raises(Refused):
        finishing.finish(ROUND, "B1", by="coder")

    (action,) = of_type(path, "finish.action")
    assert (action["state"], action["landing"], action["tip"],
            action["target_before"]) == ("found", tip, tip, base)
    now = status_of()
    assert (now.state, now.by, now.target_before) == (builds.MERGED,
                                                      builds.OWN_LANDING, base)

    withdraw()
    done = finishing.finish(ROUND, "B1", by="coder")
    assert [(one.state, one.ran) for one in done.actions] == [("found", False)]
    assert len(of_type(path, "finish.action")) == 1 and repo.rev("main") == tip
    assert done.event is not None


# -- a rerun --------------------------------------------------------------------------------

class Stopped(BaseException):
    """The finish stopped between an act and its record."""


def test_a_finish_stopped_between_its_merge_and_its_record_merges_once_when_run_again(
        ready, repo, path, monkeypatch):
    tip, before = ready.rev(), repo.rev("main")
    merge = finishing._merge

    def merge_then_stop(*args, **kwargs):
        merge(*args, **kwargs)
        raise Stopped

    monkeypatch.setattr(finishing, "_merge", merge_then_stop)
    with pytest.raises(Stopped):
        finishing.finish(ROUND, "B1", by="coder")
    assert of_type(path, "finish.action") == []
    landing = repo.rev("main")
    assert landing != before

    monkeypatch.setattr(finishing, "_merge", merge)
    done = finishing.finish(ROUND, "B1", by="coder")

    assert repo.rev("main") == landing
    assert repo.git("rev-list", "--parents", "-n", "1", "main").split()[1:] == [before,
                                                                                tip]
    (action,) = of_type(path, "finish.action")
    assert (action["state"], action["landing"], action["target_before"]) == (
        "found", landing, before)
    assert done.event is not None and len(of_type(path, "finish.done")) == 1


def test_a_failed_merge_then_merged_by_hand_is_found_not_made_again(ready, repo, path):
    script = repo.root / ".git" / "hooks" / "pre-merge-commit"
    script.parent.mkdir(exist_ok=True)
    script.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    with pytest.raises(finishing.ActionFailed):
        finishing.finish(ROUND, "B1", by="coder")
    script.unlink()
    hand = repo.merge("work", "-m", "merged by hand")

    finishing.finish(ROUND, "B1", by="coder")

    assert repo.rev("main") == hand
    assert [(one["state"], one["landing"]) for one in of_type(path, "finish.action")] == [
        ("failed", None), ("found", hand)]
    assert build_of(path).ended == builds.FINISHED


def test_a_rerun_after_the_merge_passes_the_drift_and_target_gates_after_another_finish(
        ready, repo, path, monkeypatch, tmp_path):
    other_log = opened(OTHER, "other", tmp_path)
    other = built(repo, "other.txt", OTHER)
    asked_during(monkeypatch, "_merge")
    with pytest.raises(Refused):
        finishing.finish(ROUND, "B1", by="coder")
    first = repo.rev("main")

    # Another round's build takes in the target this one reached, is checked and
    # verified there, and finishes, moving the target past this build's landing.
    other.merge("main")
    checking.check(OTHER, "B1", by="coder")
    verify(other_log, other.rev(), OTHER)
    finishing.finish(OTHER, "B1", by="coder")
    assert repo.rev("main") != first and build_of(other_log).ended == builds.FINISHED

    withdraw()
    done = finishing.finish(ROUND, "B1", by="coder")

    assert [(one.state, one.ran) for one in done.actions] == [("ok", False)]
    assert done.event is not None and build_of(path).ended == builds.FINISHED


def test_a_build_on_the_target_by_its_own_merge_at_another_tip_is_refused(
        ready, repo, path, monkeypatch):
    asked_during(monkeypatch, "_merge")
    with pytest.raises(Refused):
        finishing.finish(ROUND, "B1", by="coder")
    withdraw()
    # The branch is brought up to the target its own merge made, and checked and
    # verified there: it reads merged by that merge, which has no record at this tip.
    ready.git("merge", "-q", "--ff-only", "main")
    checking.check(ROUND, "B1", by="coder")
    verify(path, ready.rev())
    assert status_of().by == builds.OWN_LANDING
    before = log.read_round(path)

    with pytest.raises(Refused) as refused:
        finishing.finish(ROUND, "B1", by="coder")

    assert "its own finish at another tip" in refused.value.unmet[0].reason
    assert log.read_round(path) == before

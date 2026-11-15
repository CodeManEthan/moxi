"""A finish command that started and has no result: run again when its `repeat` is
set, or once by `finish --again ID`, else refused with the two ways on; `--again`
refused for any other action; the operator's skip of an action, `skip-action:B<n>=ID`,
which `finish` passes as done with no record of its own, its refusals, replacement and
revocation; and the finish row waiting where the verb refuses."""

from __future__ import annotations

import json
import shlex
import sys
from pathlib import Path

import pytest

from moxi_rounds import (builds, checking, commissioning, finishing, log, next_step,
                         opening, verdicts)
from moxi_rounds import state as states
from moxi_rounds.cli import main
from moxi_rounds.errors import Refused

ROUND = "proj-a/a-round"
CHECK = shlex.join([sys.executable, "-c", "print('the check passed')"])
POINTER = "comms:1234567"
SKIP = "skip-action:B1=deploy"


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
    """`configure(*actions)`: proj-a's check, and its finish set to `actions`, each a
    TOML inline table."""

    def write(*actions: str) -> None:
        finish = f"finish = [ {', '.join(actions)} ]\n" if actions else ""
        write_config(f"[projects.proj-a]\ncheck = {{ command = {json.dumps(CHECK)} }}\n"
                     f"{finish}")

    write()
    return write


@pytest.fixture
def ready(repo, configure, tmp_path, capsys):
    """B1 built, checked and verified at its tip. Gives the round's log."""
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
    capsys.readouterr()
    return path


class Stopped(BaseException):
    """The finish stopped while a command ran."""


def command(action_id: str, code: int, said: Path, *, extra: str = "") -> str:
    """A command action that adds a line to `said` each time it runs, and exits
    `code`."""
    script = ("import sys, pathlib; p = pathlib.Path(sys.argv[1]); "
              "p.write_text((p.read_text() if p.exists() else '') + 'ran\\n'); "
              f"sys.exit({code})")
    argv = [sys.executable, "-c", script, str(said)]
    return f'{{ do = "command", id = "{action_id}", argv = {json.dumps(argv)}{extra} }}'


def stop_during_the_command(monkeypatch) -> None:
    """Run a finish that stops while its command runs, leaving `started` with no
    result."""
    real = finishing._command

    def stop(*args, **kwargs):
        raise Stopped

    monkeypatch.setattr(finishing, "_command", stop)
    with pytest.raises(Stopped):
        finishing.finish(ROUND, "B1", by="coder")
    monkeypatch.setattr(finishing, "_command", real)


def actions_of(path: Path) -> list[tuple[str, str]]:
    return [(one["id"], one["state"]) for one in log.read_round(path)
            if one["type"] == "finish.action"]


def ended(path: Path) -> str | None:
    return builds.current(states.fold(log.read_round(path)), "B1").ended


def runs(said: Path) -> int:
    return said.read_text().count("ran") if said.exists() else 0


def skip(*more: str) -> int:
    return main(["rule", ROUND, SKIP, "--record", POINTER, "--as", "planner", *more])


# -- a command with no result --------------------------------------------------------------

def test_a_started_command_is_refused_with_both_ways_on_and_nothing_written(
        ready, configure, tmp_path, monkeypatch):
    said = tmp_path / "said.txt"
    configure('{ do = "merge" }', command("deploy", 0, said))
    stop_during_the_command(monkeypatch)
    before = log.read_round(ready)

    with pytest.raises(Refused) as refused:
        finishing.finish(ROUND, "B1", by="coder")

    (unmet,) = refused.value.unmet
    assert f"moxi rounds finish {ROUND} B1 --again deploy" in unmet.fix
    assert f"moxi rounds rule {ROUND} {SKIP} --record PTR" in unmet.fix
    assert log.read_round(ready) == before
    assert runs(said) == 0


def test_a_started_command_whose_repeat_is_set_is_run_again_and_says_so(
        ready, configure, tmp_path, monkeypatch, capsys):
    said = tmp_path / "said.txt"
    configure('{ do = "merge" }', command("deploy", 0, said, extra=", repeat = true"))
    stop_during_the_command(monkeypatch)
    (first,) = [one for one in log.read_round(ready) if one.get("state") == "started"]

    assert main(["finish", ROUND, "B1", "--as", "coder"]) == 0

    out = capsys.readouterr().out
    assert (f"deploy: started at {first['at']} and has no result; run again, as its "
            "repeat is set") in out
    assert runs(said) == 1
    assert actions_of(ready) == [("merge", "ok"), ("deploy", "started"),
                                 ("deploy", "started"), ("deploy", "ok")]
    assert ended(ready) == builds.FINISHED


def test_again_runs_a_started_command_once_and_records_what_it_sees(
        ready, configure, tmp_path, monkeypatch, capsys):
    said = tmp_path / "said.txt"
    configure('{ do = "merge" }', command("deploy", 0, said))
    stop_during_the_command(monkeypatch)

    assert main(["finish", ROUND, "B1", "--again", "deploy", "--as", "coder"]) == 0

    assert "run again, as --again asked for it" in capsys.readouterr().out
    assert runs(said) == 1
    assert actions_of(ready)[-2:] == [("deploy", "started"), ("deploy", "ok")]
    assert ended(ready) == builds.FINISHED


def test_again_of_a_command_that_fails_records_the_failure(
        ready, configure, tmp_path, monkeypatch):
    said = tmp_path / "said.txt"
    configure('{ do = "merge" }', command("deploy", 3, said))
    stop_during_the_command(monkeypatch)

    with pytest.raises(finishing.ActionFailed):
        finishing.finish(ROUND, "B1", by="coder", again="deploy")

    assert actions_of(ready)[-1] == ("deploy", "failed")
    assert ended(ready) is None


@pytest.mark.parametrize("again, words", [
    ("nothing", "has no finish action nothing; its actions are merge, first, deploy"),
    ("merge", "merge is a merge action"),
    ("first", "is recorded ok"),
    ("deploy", "is recorded failed"),
])
def test_again_is_refused_for_anything_but_a_started_command(
        ready, configure, tmp_path, again, words):
    configure('{ do = "merge" }', command("first", 0, tmp_path / "first.txt"),
              command("deploy", 3, tmp_path / "said.txt"))
    with pytest.raises(finishing.ActionFailed):
        finishing.finish(ROUND, "B1", by="coder")
    before = log.read_round(ready)

    with pytest.raises(Refused) as refused:
        finishing.finish(ROUND, "B1", by="coder", again=again)

    assert any(words in one.reason for one in refused.value.unmet)
    assert log.read_round(ready) == before


def test_again_is_refused_for_a_command_with_no_record(ready, configure, tmp_path):
    configure('{ do = "merge" }', command("deploy", 0, tmp_path / "said.txt"))
    with pytest.raises(Refused) as refused:
        finishing.finish(ROUND, "B1", by="coder", again="deploy")
    assert any("has no record" in one.reason and one.fix == f"moxi rounds finish {ROUND} B1"
               for one in refused.value.unmet)
    assert actions_of(ready) == []


# -- the operator's skip of an action -------------------------------------------------------

def test_a_skipped_action_is_passed_as_done_with_no_record_of_its_own(
        ready, configure, tmp_path, monkeypatch, capsys):
    said = tmp_path / "said.txt"
    configure('{ do = "merge" }', command("deploy", 0, said))
    stop_during_the_command(monkeypatch)
    assert skip() == 0
    capsys.readouterr()

    assert main(["finish", ROUND, "B1", "--as", "coder"]) == 0

    assert "deploy: skipped by the operator, C1; not run" in capsys.readouterr().out
    assert runs(said) == 0
    assert actions_of(ready) == [("merge", "ok"), ("deploy", "started")]
    assert ended(ready) == builds.FINISHED


def test_a_skipped_action_taken_back_is_refused_again(
        ready, configure, tmp_path, monkeypatch):
    configure('{ do = "merge" }', command("deploy", 0, tmp_path / "said.txt"))
    stop_during_the_command(monkeypatch)
    assert skip() == 0
    assert main(["rule", ROUND, "revoke:C1", "--record", POINTER, "--as",
                 "planner"]) == 0

    with pytest.raises(Refused) as refused:
        finishing.finish(ROUND, "B1", by="coder")
    assert "started at" in refused.value.unmet[0].reason


def test_again_is_refused_for_an_action_the_operator_skipped(
        ready, configure, tmp_path, monkeypatch):
    configure('{ do = "merge" }', command("deploy", 0, tmp_path / "said.txt"))
    stop_during_the_command(monkeypatch)
    assert skip() == 0
    with pytest.raises(Refused) as refused:
        finishing.finish(ROUND, "B1", by="coder", again="deploy")
    assert any("the operator skipped deploy for B1, by C1" in one.reason
               for one in refused.value.unmet)


def test_a_skip_of_an_action_is_replaced_only_by_naming_the_first(
        ready, configure, tmp_path, capsys):
    configure('{ do = "merge" }', command("deploy", 0, tmp_path / "said.txt"))
    assert skip() == 0
    capsys.readouterr()
    assert skip() == 3
    assert "is skipped already, by C1" in capsys.readouterr().err
    assert skip("--supersedes", "C1") == 0


@pytest.mark.parametrize("item, words", [
    ("skip-action:B1=nothing", "finish list has no action nothing; its actions are "
                               "merge, deploy"),
    ("skip-action:B9=deploy", "B9 is not a stage of"),
])
def test_a_skip_of_an_action_the_stage_does_not_have_is_refused(
        ready, configure, tmp_path, capsys, item, words):
    configure('{ do = "merge" }', command("deploy", 0, tmp_path / "said.txt"))
    assert main(["rule", ROUND, item, "--record", POINTER, "--as", "planner"]) == 3
    assert words in capsys.readouterr().err


@pytest.mark.parametrize("item", ["skip-action:B1", "skip-action:B1=", "skip-action:x=y"])
def test_a_skip_of_an_action_that_can_t_be_read_is_refused(ready, capsys, item):
    assert main(["rule", ROUND, item, "--record", POINTER, "--as", "planner"]) == 2
    assert "skip-action:B<n>=ID" in capsys.readouterr().err


def test_show_lists_a_skip_of_an_action(ready, configure, tmp_path, capsys):
    configure('{ do = "merge" }', command("deploy", 0, tmp_path / "said.txt"))
    assert skip() == 0
    capsys.readouterr()
    assert main(["show", ROUND]) == 0
    assert (f"skipped: the finish action deploy for B1, by C1 (record {POINTER})"
            in capsys.readouterr().out)


# -- the finish row -------------------------------------------------------------------------

def finish_group(found: dict) -> dict | None:
    return next((one for one in found["groups"] if one["step"] == "finish"), None)


def test_the_finish_row_waits_for_a_started_command_finish_would_refuse(
        ready, configure, tmp_path, monkeypatch):
    configure('{ do = "merge" }', command("deploy", 0, tmp_path / "said.txt"))
    stop_during_the_command(monkeypatch)

    found = next_step.read(ROUND)

    assert "--again deploy" in json.dumps(found)
    assert finish_group(found)["state"] == "waiting"


def test_the_finish_row_is_ready_where_the_command_repeats(
        ready, configure, tmp_path, monkeypatch):
    configure('{ do = "merge" }', command("deploy", 0, tmp_path / "said.txt",
                                          extra=", repeat = true"))
    stop_during_the_command(monkeypatch)
    assert finish_group(next_step.read(ROUND))["state"] == "ready"

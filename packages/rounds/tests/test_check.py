"""`check`: the run of a repo's check at a build's tip, its record, the refusals before
it, what changes during it, and what a gate compares."""

from __future__ import annotations

import json
import os
import shlex
import sys
import textwrap
from pathlib import Path

import pytest

from moxi_rounds import checking, commissioning, log, opening, places, steps
from moxi_rounds import state as states
from moxi_rounds.cli import main
from moxi_rounds.errors import Refused

ROUND = "proj-a/a-round"
PY = sys.executable


@pytest.fixture
def repo(git_repo, tmp_path):
    """The project `proj-a`, whose root is the test's git repo, keeping its rounds under
    a notes folder of its own."""
    from moxi import cli

    assert cli.main(["projects", "add", "proj-a", "--root", str(git_repo.root)]) == 0
    assert cli.main(["projects", "set", "proj-a", "--notes-root",
                     str(tmp_path / "notes")]) == 0
    return git_repo


@pytest.fixture
def declare(write_config, tmp_path):
    """`declare(code, *words, timeout_s=None)`: make `proj-a` declare as its check a
    Python script holding `code`, run with `words` after it. Returns the script."""

    def set_check(code: str, *words: str, timeout_s: int | None = None) -> Path:
        script = tmp_path / "check.py"
        script.write_text(textwrap.dedent(code), encoding="utf-8")
        command = shlex.join([PY, str(script), *words])
        limit = "" if timeout_s is None else f", timeout_s = {timeout_s}"
        write_config(f"[projects.proj-a]\ncheck = {{ command = {json.dumps(command)}"
                     f"{limit} }}\n")
        return script

    return set_check


@pytest.fixture
def build(repo, tmp_path, capsys):
    """The round, in the `fix` flow, with B1 on the branch `work` commissioned and one
    commit made on it. Returns `(round log, worktree repo, the commission)`."""
    opening.open_round(ROUND, description="a placeholder round", actor="planner",
                       flow="fix")
    path = log.round_log(tmp_path / "notes" / "rounds" / "a-round")
    log.append(path, "stage.declared",
               {"id": "B1", "repo": "proj-a", "branch": "work", "after": []},
               actor="planner")
    made = commissioning.commission(ROUND, "B1", builder="coder", actor="planner")
    tree = type(repo)(made.worktree)
    tree.commit("work.txt", "the work")
    capsys.readouterr()
    return path, tree, made


def ran(path: Path) -> list[dict]:
    return [one for one in log.read(path) if one["type"] == "check.ran"]


PASS = "print('the check passed')"


# -- the run and its record --------------------------------------------------------------

def test_a_check_runs_in_the_worktree_and_records_the_run(build, declare, repo):
    path, tree, made = build
    script = declare("import os; print(os.getcwd())")

    done = checking.check(ROUND, "B1", actor="coder")

    [event] = ran(path)
    assert {key: event[key] for key in ("stage", "step", "commit", "merge_base", "argv",
                                        "exit_code")} == {
        "stage": "B1", "step": "check", "commit": tree.rev(),
        "merge_base": repo.rev("main"), "argv": [PY, str(script)], "exit_code": 0}
    assert isinstance(event["duration_s"], float)
    output = Path(event["output"])
    assert output == made.scratch / "check-1.log"
    assert output.read_text().strip() == str(made.worktree)
    assert done.passed and done.output == output


def test_the_placeholders_are_filled_for_the_build_and_other_braces_are_kept(
        build, declare, repo):
    path, tree, _made = build
    declare(PASS, "{tip}", "{branch}", "{stage}", "{round}", "{repo}", "{into}", "{root}",
            "{other}", "x{stage}y")

    checking.check(ROUND, "B1", actor="coder")

    assert ran(path)[0]["argv"][2:] == [tree.rev(), "work", "B1", ROUND, "proj-a", "main",
                                        str(repo.root), "{other}", "xB1y"]


def test_the_run_has_stdin_closed_and_no_git_variable(build, declare, monkeypatch):
    path, _tree, _made = build
    declare("""
        import os, sys
        print(repr(sys.stdin.read()))
        print(sorted(name for name in os.environ if name.startswith("GIT_")))
    """)
    monkeypatch.setenv("GIT_WORK_TREE", "/nowhere")

    checking.check(ROUND, "B1", actor="coder")

    assert Path(ran(path)[0]["output"]).read_text().split("\n")[:2] == ["''", "[]"]


def test_each_run_writes_a_new_output_file(build, declare):
    path, _tree, made = build
    declare(PASS)

    checking.check(ROUND, "B1", actor="coder")
    checking.check(ROUND, "B1", actor="coder")

    assert [Path(one["output"]).name for one in ran(path)] == ["check-1.log",
                                                                "check-2.log"]
    assert sorted(os.listdir(made.scratch)) == sorted([
        "check-1.log", "check-2.log", places.SCRATCH_MARKER])


def test_a_failed_check_is_recorded_and_the_verb_exits_1(build, declare, capsys):
    path, _tree, _made = build
    declare("raise SystemExit(4)")

    assert main(["check", ROUND, "B1"]) == 1

    assert ran(path)[0]["exit_code"] == 4
    assert "failed: exit 4" in capsys.readouterr().out


def test_a_run_past_its_time_limit_is_ended_and_recorded_with_no_exit_code(
        build, declare, capsys):
    path, _tree, _made = build
    declare("import time; time.sleep(60)", timeout_s=600)

    assert main(["check", ROUND, "B1", "--timeout-s", "1"]) == 1

    [event] = ran(path)
    assert event["exit_code"] is None and event["duration_s"] < 30
    assert "ended at its time limit" in capsys.readouterr().out


def test_the_check_s_own_time_limit_applies_without_the_flag(build, declare):
    path, _tree, _made = build
    declare("import time; time.sleep(60)", timeout_s=1)

    checking.check(ROUND, "B1", actor="coder")

    assert ran(path)[0]["exit_code"] is None


def test_with_no_round_the_check_finds_the_build_from_its_worktree(
        build, declare, monkeypatch, capsys):
    path, _tree, made = build
    declare(PASS)
    monkeypatch.chdir(made.worktree)

    assert main(["check"]) == 0

    assert ran(path)[0]["stage"] == "B1"
    assert f"check {ROUND} B1 at" in capsys.readouterr().out


def test_a_round_named_with_no_stage_is_refused(build, declare):
    assert main(["check", ROUND]) == 2


def test_while_a_check_runs_a_second_verb_on_the_round_completes(build, declare):
    path, _tree, _made = build
    declare(f"""
        from moxi_rounds.cli import main
        raise SystemExit(main(["ask", {ROUND!r}, "a placeholder question", "--from",
                               "comms:1234567"]))
    """)

    checking.check(ROUND, "B1", actor="coder")

    types = [one["type"] for one in log.read(path)]
    assert types[-2:] == ["question.asked", "check.ran"]
    assert ran(path)[0]["exit_code"] == 0


def test_a_run_the_caller_interrupts_records_nothing(build, declare, monkeypatch):
    path, _tree, _made = build
    declare(PASS)
    before = path.read_bytes()

    def interrupted(*_args, **_kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(checking.proc, "run", interrupted)
    with pytest.raises(KeyboardInterrupt):
        checking.check(ROUND, "B1", actor="coder")

    assert path.read_bytes() == before


# -- what changes during the run ---------------------------------------------------------

def test_a_commit_made_during_the_run_records_nothing(build, declare):
    path, _tree, _made = build
    declare("""
        import subprocess
        subprocess.run(["git", "-c", "user.name=coder", "-c", "user.email=coder@example.test",
                        "commit", "-q", "--allow-empty", "-m", "during"], check=True)
    """)
    before = path.read_bytes()

    with pytest.raises(Refused) as caught:
        checking.check(ROUND, "B1", actor="coder")

    assert "moved" in str(caught.value) and "nothing is recorded" in str(caught.value)
    assert path.read_bytes() == before


def test_a_file_left_in_the_worktree_by_the_run_records_nothing(build, declare):
    path, _tree, _made = build
    declare("open('left.txt', 'w').write('left')")
    before = path.read_bytes()

    with pytest.raises(Refused) as caught:
        checking.check(ROUND, "B1", actor="coder")

    assert "changed while the check ran" in str(caught.value)
    assert path.read_bytes() == before


# -- what it refuses before the run ------------------------------------------------------

def refused(path: Path) -> str:
    before = path.read_bytes()
    with pytest.raises(Refused) as caught:
        checking.check(ROUND, "B1", actor="coder")
    assert path.read_bytes() == before
    return str(caught.value)


def test_a_repo_with_no_check_declared_is_refused(build):
    path, _tree, _made = build

    assert "proj-a declares no check" in refused(path)


def test_a_worktree_with_changes_not_committed_is_refused(build, declare):
    path, _tree, made = build
    declare(PASS)
    (made.worktree / "loose.txt").write_text("loose\n")

    assert "holds changes not committed" in refused(path)


def test_a_worktree_on_another_branch_is_refused(build, declare):
    path, tree, _made = build
    declare(PASS)
    tree.switch("elsewhere", new_from="work")

    assert "has elsewhere checked out, not work" in refused(path)


def test_a_build_with_no_work_is_refused(repo, declare, tmp_path):
    opening.open_round(ROUND, description="a placeholder round", actor="planner",
                       flow="fix")
    path = log.round_log(tmp_path / "notes" / "rounds" / "a-round")
    log.append(path, "stage.declared",
               {"id": "B1", "repo": "proj-a", "branch": "work", "after": []},
               actor="planner")
    commissioning.commission(ROUND, "B1", builder="coder", actor="planner")
    declare(PASS)

    assert "B1 has no work to check: it reads empty" in refused(path)


def test_a_stage_never_commissioned_is_refused(repo, declare, tmp_path):
    opening.open_round(ROUND, description="a placeholder round", actor="planner",
                       flow="fix")
    path = log.round_log(tmp_path / "notes" / "rounds" / "a-round")
    declare(PASS)

    assert "B1 of proj-a/a-round was never commissioned" in refused(path)


def test_a_worktree_made_on_another_machine_is_refused(build, declare, monkeypatch):
    path, _tree, _made = build
    declare(PASS)
    monkeypatch.setattr(places, "host", lambda: "box-1")

    assert "and the check runs in it there" in refused(path)


# -- what a gate compares ----------------------------------------------------------------

def folded(path: Path) -> states.State:
    return states.fold(log.read(path))


def test_a_pass_counts_at_its_commit_with_the_argv_given_now(build, declare, repo):
    path, tree, _made = build
    declare(PASS, "{tip}")
    checking.check(ROUND, "B1", actor="coder")
    tip = tree.rev()

    assert checking.passes(folded(path), "B1", "check", tip=tip,
                           argv=[PY, str(Path(ran(path)[0]["argv"][1])), tip])


def test_a_pass_at_an_older_commit_does_not_count(build, declare):
    path, tree, _made = build
    declare(PASS)
    checking.check(ROUND, "B1", actor="coder")
    argv = ran(path)[0]["argv"]
    newer = tree.commit("more.txt")

    why = checking.why_not(folded(path), "B1", "check", tip=newer, argv=argv)

    assert why is not None and "not at the tip" in why


def test_a_pass_of_a_command_the_repo_no_longer_declares_does_not_count(build, declare,
                                                                         repo):
    from moxi_rounds import settings as own

    path, tree, _made = build
    declare(PASS)
    checking.check(ROUND, "B1", actor="coder")
    declare(PASS, "--more")
    build_now = checking.builds.current(folded(path), "B1")
    argv = checking.argv_now(own.load(), ROUND, build_now, root=repo.root, tip=tree.rev())

    why = checking.why_not(folded(path), "B1", "check", tip=tree.rev(), argv=argv)

    assert why is not None and "the command is now" in why


def test_a_failed_run_and_a_run_of_another_step_do_not_count(build, declare):
    path, tree, _made = build
    declare("raise SystemExit(1)")
    checking.check(ROUND, "B1", actor="coder")
    argv = ran(path)[0]["argv"]
    log.append(path, "check.ran", {
        "stage": "B1", "step": "staging", "commit": tree.rev(), "merge_base": None,
        "argv": argv, "exit_code": 0, "duration_s": 0.1, "output": "placeholder.log"},
        actor="coder")

    state = folded(path)
    assert "exited 1" in checking.why_not(state, "B1", "check", tip=tree.rev(), argv=argv)
    assert checking.newest(state, "B1", "check")["exit_code"] == 1
    assert checking.passes(state, "B1", "staging", tip=tree.rev(), argv=argv)


# -- the step ----------------------------------------------------------------------------

def step(path: Path, **seen) -> steps.Step | None:
    return steps.one(folded(path), "check", "B1", name=ROUND, seen=seen or None)


def test_the_check_step_is_not_due_before_the_build_is_commissioned(repo, tmp_path):
    opening.open_round(ROUND, description="a placeholder round", actor="planner",
                       flow="fix")
    path = log.round_log(tmp_path / "notes" / "rounds" / "a-round")
    log.append(path, "stage.declared",
               {"id": "B1", "repo": "proj-a", "branch": "work", "after": []},
               actor="planner")

    assert step(path).state == steps.NOT_DUE


def test_the_check_step_waits_on_git_where_git_was_not_read(build):
    path, _tree, _made = build

    found = step(path)

    assert found.state == steps.WAITING
    assert "not read here" in found.waits[0].reason


def test_the_check_step_reads_the_build_as_seen(build, declare):
    from moxi_rounds import settings as own

    path, tree, made = build
    declare(PASS)
    state = folded(path)

    seen = checking.seen_of(state, ROUND, settings=own.load())
    assert seen["B1"].state == "building" and seen["B1"].clean
    assert step(path, **seen).state == steps.READY

    (made.worktree / "loose.txt").write_text("loose\n")
    seen = checking.seen_of(state, ROUND, settings=own.load())
    assert step(path, **seen).state == steps.WAITING
    (made.worktree / "loose.txt").unlink()

    checking.check(ROUND, "B1", actor="coder")
    seen = checking.seen_of(folded(path), ROUND, settings=own.load())
    assert step(path, **seen).state == steps.DONE

    tree.commit("more.txt")
    seen = checking.seen_of(folded(path), ROUND, settings=own.load())
    assert step(path, **seen).state == steps.READY


def test_a_build_whose_repo_declares_no_check_has_no_check_step(build):
    from moxi_rounds import settings as own

    path, _tree, _made = build
    seen = checking.seen_of(folded(path), ROUND, settings=own.load())

    assert seen["B1"].check is None
    assert step(path, **seen) is None
    assert "check" not in [one.step for one in steps.of(folded(path), seen=seen)]


def test_a_build_with_no_work_waits_at_the_check_step(build):
    path, _tree, _made = build
    seen = steps.Seen(state="empty", tip="0" * 40, clean=True, check=("placeholder",))

    found = step(path, B1=seen)

    assert found.state == steps.WAITING
    assert "B1 has no work to check: it reads empty" in found.waits[0].reason


# -- where a run is shown ----------------------------------------------------------------

def test_show_and_the_round_note_give_a_build_s_newest_check_run(build, declare,
                                                                 tmp_path):
    from moxi_rounds import showing

    _path, tree, _made = build
    declare("raise SystemExit(3)")
    checking.check(ROUND, "B1", actor="coder")
    declare(PASS)
    checking.check(ROUND, "B1", actor="coder")

    shown = json.loads(showing.as_json(showing.show(ROUND)))
    [row] = shown["builds"]
    assert row["check"]["commit"] == tree.rev() and row["check"]["exit_code"] == 0
    assert f"check passed at {tree.rev()[:7]}, output" in showing.as_text(
        showing.show(ROUND))
    note = (tmp_path / "notes" / "rounds" / "a-round" / "round-note.md").read_text()
    assert f"check passed at {tree.rev()[:7]}" in note


def test_a_skip_of_the_check_lets_the_stage_past_it_whatever_git_reads(build):
    path, _tree, _made = build
    from moxi_rounds import ruling

    ruling.rule(ROUND, item="skip:check:B1", record="comms:1234567", actor="planner")

    found = step(path, B1=steps.Seen(state="empty", tip="0" * 40, clean=False,
                                     check=("placeholder",)))

    assert found.state == steps.SKIPPED


# -- a command step of the user's own ----------------------------------------------------

STEPPED = """
    [flows.mine]
    steps = [
      {{ step = "build", by = "builder" }},
      {{ step = "staging", does = "command", before = "finish", by = "builder", argv = {argv}{limit} }},
      {{ step = "security-review", does = "report", before = "finish", by = "checker" }},
      {{ step = "finish", by = "builder" }},
    ]
"""


@pytest.fixture
def stepped(repo, write_config, tmp_path, capsys):
    """`stepped(code, *words, timeout_s=None, check=None)`: the round, in a flow whose
    command step `staging` runs a Python script holding `code` with `words` after it,
    with B1 on the branch `work` commissioned and one commit made on it; with `check`,
    the repo declares that command as its check. Returns `(round log, worktree repo,
    the commission, the script)`."""

    def make(code: str, *words: str, timeout_s: int | None = None,
             check: str | None = None):
        script = tmp_path / "staging.py"
        script.write_text(textwrap.dedent(code), encoding="utf-8")
        limit = "" if timeout_s is None else f", timeout_s = {timeout_s}"
        config = STEPPED.format(argv=json.dumps([PY, str(script), *words]), limit=limit)
        if check is not None:
            config += f"[projects.proj-a]\ncheck = {{ command = {json.dumps(check)} }}\n"
        write_config(config)
        opening.open_round(ROUND, description="a placeholder round", actor="planner",
                           flow="mine")
        path = log.round_log(tmp_path / "notes" / "rounds" / "a-round")
        log.append(path, "stage.declared",
                   {"id": "B1", "repo": "proj-a", "branch": "work", "after": []},
                   actor="planner")
        made = commissioning.commission(ROUND, "B1", builder="coder", actor="planner")
        tree = type(repo)(made.worktree)
        tree.commit("work.txt", "the work")
        capsys.readouterr()
        return path, tree, made, script

    return make


def test_a_command_step_runs_its_argv_in_the_worktree_recorded_under_its_name(stepped):
    path, tree, made, script = stepped("import os; print(os.getcwd())", "{tip}",
                                       "{stage}")

    done = checking.check(ROUND, "B1", actor="coder", step="staging")

    [event] = ran(path)
    assert (event["step"], event["commit"], event["argv"], event["exit_code"]) == (
        "staging", tree.rev(), [PY, str(script), tree.rev(), "B1"], 0)
    assert Path(event["output"]) == made.scratch / "check-1.log"
    assert Path(event["output"]).read_text().strip() == str(made.worktree)
    assert (done.step, done.passed) == ("staging", True)


def test_a_command_step_runs_in_a_repo_that_declares_no_check(stepped):
    path, _tree, _made, _script = stepped(PASS)

    checking.check(ROUND, "B1", actor="coder", step="staging")

    assert [one["step"] for one in ran(path)] == ["staging"]


def test_a_command_step_s_own_time_limit_applies(stepped):
    path, _tree, _made, _script = stepped("import time; time.sleep(60)", timeout_s=1)

    checking.check(ROUND, "B1", actor="coder", step="staging")

    assert ran(path)[0]["exit_code"] is None


def test_the_verb_runs_a_command_step_from_the_build_s_worktree(
        stepped, monkeypatch, capsys):
    path, _tree, made, _script = stepped(PASS)
    monkeypatch.chdir(made.worktree)

    assert main(["check", "--step", "staging"]) == 0

    assert ran(path)[0]["step"] == "staging"
    assert f"staging {ROUND} B1 at" in capsys.readouterr().out


@pytest.mark.parametrize("name", ["check.local", "staging.extra"])
def test_a_step_name_with_a_dot_is_refused(stepped, name):
    path, _tree, _made, _script = stepped(PASS)
    before = path.read_bytes()

    assert main(["check", ROUND, "B1", "--step", name]) == 2

    assert path.read_bytes() == before


def test_the_repo_s_check_named_as_a_step_is_refused(stepped):
    path, _tree, _made, _script = stepped(PASS, check=f"{PY} -c pass")
    before = path.read_bytes()

    assert main(["check", ROUND, "B1", "--step", "check"]) == 2

    assert path.read_bytes() == before


@pytest.mark.parametrize("name", ["deploy", "security-review", "build"])
def test_a_step_that_is_no_command_step_of_the_flow_is_refused(stepped, name):
    path, _tree, _made, _script = stepped(PASS)
    before = path.read_bytes()

    with pytest.raises(Refused) as caught:
        checking.check(ROUND, "B1", actor="coder", step=name)

    assert (f"the flow mine of {ROUND} has no step {name} that runs a command (its "
            "command steps: staging)") in str(caught.value)
    assert path.read_bytes() == before


def stepped_step(path: Path, name: str = "staging") -> steps.Step | None:
    from moxi_rounds import settings as own

    state = folded(path)
    return steps.one(state, name, "B1", name=ROUND,
                     seen=checking.seen_of(state, ROUND, settings=own.load()))


def test_the_command_step_is_done_once_a_run_of_it_passed_at_the_tip(stepped):
    path, tree, _made, _script = stepped(PASS)
    assert stepped_step(path).state == steps.READY

    checking.check(ROUND, "B1", actor="coder", step="staging")
    assert stepped_step(path).state == steps.DONE

    tree.commit("more.txt")
    assert stepped_step(path).state == steps.READY


def test_a_failed_run_of_the_command_step_leaves_it_ready(stepped):
    path, _tree, _made, _script = stepped("raise SystemExit(2)")

    assert main(["check", ROUND, "B1", "--step", "staging"]) == 1

    assert stepped_step(path).state == steps.READY


def test_the_command_step_waits_for_a_clean_worktree_as_its_run_refuses_one(stepped):
    path, tree, _made, _script = stepped(PASS)
    (tree.root / "loose.txt").write_text("not committed\n", encoding="utf-8")

    found = stepped_step(path)
    with pytest.raises(Refused) as caught:
        checking.check(ROUND, "B1", actor="coder", step="staging")

    assert found.state == steps.WAITING
    assert "the worktree of B1 is not clean on its branch here" in [
        one.reason for one in found.waits]
    assert "holds changes not committed" in str(caught.value)

    (tree.root / "loose.txt").unlink()
    assert stepped_step(path).state == steps.READY


def test_a_run_of_one_step_is_never_read_for_another(stepped):
    path, _tree, _made, _script = stepped(PASS, check=f"{PY} -c pass")

    checking.check(ROUND, "B1", actor="coder")
    assert (stepped_step(path).state, stepped_step(path, "check").state) == (
        steps.READY, steps.DONE)

    checking.check(ROUND, "B1", actor="coder", step="staging")
    assert stepped_step(path).state == steps.DONE


def test_the_command_step_waits_on_git_where_git_was_not_read(stepped):
    path, _tree, _made, _script = stepped(PASS)

    found = steps.one(folded(path), "staging", "B1", name=ROUND)

    assert (found.state, found.unread) == (steps.WAITING, True)


def test_the_command_step_is_not_due_before_the_stage_is_commissioned(
        repo, write_config, tmp_path):
    write_config(STEPPED.format(argv=json.dumps([PY, "-c", "pass"]), limit=""))
    opening.open_round(ROUND, description="a placeholder round", actor="planner",
                       flow="mine")
    path = log.round_log(tmp_path / "notes" / "rounds" / "a-round")
    log.append(path, "stage.declared",
               {"id": "B1", "repo": "proj-a", "branch": "work", "after": []},
               actor="planner")

    assert steps.one(folded(path), "staging", "B1").state == steps.NOT_DUE


def test_next_prints_the_command_step_with_the_command_that_runs_it(stepped, capsys):
    from moxi_rounds import next_step

    _path, _tree, _made, _script = stepped(PASS)

    [group, *_] = next_step.read(ROUND)["groups"]

    assert (group["step"], group["stage"], group["state"]) == ("staging", "B1",
                                                               steps.READY)
    assert group["then"] == [f"moxi rounds check {ROUND} B1 --step staging, or moxi "
                             "rounds check --step staging in the build's worktree"]
    checking.check(ROUND, "B1", actor="coder", step="staging")
    assert next_step.read(ROUND)["groups"][0]["step"] == "security-review"


def test_show_and_the_round_note_give_a_command_step_s_newest_run(stepped, tmp_path):
    from moxi_rounds import showing

    _path, tree, _made, _script = stepped(PASS)
    checking.check(ROUND, "B1", actor="coder", step="staging")

    [row] = json.loads(showing.as_json(showing.show(ROUND)))["builds"]
    assert row["check"] is None
    assert [(one["step"], one["commit"]) for one in row["step_runs"]] == [
        ("staging", tree.rev())]
    assert (f"staging passed at {tree.rev()[:7]}, output"
            in showing.as_text(showing.show(ROUND)))
    note = (tmp_path / "notes" / "rounds" / "a-round" / "round-note.md").read_text()
    assert f"staging passed at {tree.rev()[:7]}" in note

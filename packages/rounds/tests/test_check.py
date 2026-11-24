"""`check`: the run of a repo's check at a build's tip, its record, the refusals before
it, what changes during it, and what a gate compares."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from moxi_rounds import (check_line, checking, commissioning, log, opening, places,
                         steps)
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
    opening.open_round(ROUND, description="a placeholder round", by="planner",
                       flow="fix")
    path = log.round_log(tmp_path / "notes" / "rounds" / "a-round")
    log.append(path, "stage.declared",
               {"id": "B1", "repo": "proj-a", "branch": "work", "after": []},
               by="planner")
    made = commissioning.commission(ROUND, "B1", builder="coder", by="planner")
    tree = type(repo)(made.worktree)
    tree.commit("work.txt", "the work")
    capsys.readouterr()
    return path, tree, made


def ran(path: Path) -> list[dict]:
    return [one for one in log.read(path) if one["type"] == "check.ran"]


def placed(event: dict, key: str) -> Path:
    """The place `event` records under `key`, read as rounds reads it: a record holds a
    place under the home folder from `~` (on Windows rounds' own folders are under it)."""
    return Path(places.expand_home(event[key]))


PASS = "print('the check passed')"


# -- the run and its record --------------------------------------------------------------

def test_a_check_runs_in_the_worktree_and_records_the_run(build, declare, repo):
    path, tree, made = build
    script = declare("import os; print(os.getcwd())")

    done = checking.check(ROUND, "B1", by="coder")

    [event] = ran(path)
    assert {key: event[key] for key in ("stage", "step", "commit", "merge_base", "argv",
                                        "exit_code")} == {
        "stage": "B1", "step": "check", "commit": tree.rev(),
        "merge_base": repo.rev("main"), "argv": [PY, str(script)], "exit_code": 0}
    assert isinstance(event["duration_s"], float)
    output = placed(event, "output")
    assert output == made.scratch / "check-1.log"
    assert output.read_text().strip() == str(made.worktree)
    assert done.passed and done.output == output


def test_the_placeholders_are_filled_for_the_build_and_other_braces_are_kept(
        build, declare, repo):
    path, tree, _made = build
    declare(PASS, "{tip}", "{branch}", "{stage}", "{round}", "{repo}", "{into}", "{root}",
            "{other}", "x{stage}y")

    checking.check(ROUND, "B1", by="coder")

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

    checking.check(ROUND, "B1", by="coder")

    assert placed(ran(path)[0], "output").read_text().split("\n")[:2] == ["''", "[]"]


def test_each_run_writes_a_new_output_file(build, declare):
    path, _tree, made = build
    declare(PASS)

    checking.check(ROUND, "B1", by="coder")
    checking.check(ROUND, "B1", by="coder")

    assert [placed(one, "output").name for one in ran(path)] == ["check-1.log",
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

    checking.check(ROUND, "B1", by="coder")

    assert ran(path)[0]["exit_code"] is None


POSIX = pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals and /proc")


@POSIX
def test_with_no_limit_set_a_run_stops_at_the_default_on_sigint(
        build, declare, monkeypatch):
    # Core's check ended a run at 3600 s by default, SIGINT first and SIGKILL 30 s on.
    assert (checking.DEFAULT_TIMEOUT_S, checking.GRACE_S) == (3600.0, 30.0)
    monkeypatch.setattr(checking, "DEFAULT_TIMEOUT_S", 1.0)
    path, _tree, made = build
    declare(f"""
        import signal, sys, time
        def stop(signum, frame):
            open({str(made.scratch / "caught")!r}, "w").write(signal.Signals(signum).name)
            sys.exit(3)
        signal.signal(signal.SIGINT, stop)
        time.sleep(60)
        """)

    checking.check(ROUND, "B1", by="coder")

    [event] = ran(path)
    assert event["exit_code"] is None and event["duration_s"] < 20
    assert (made.scratch / "caught").read_text() == "SIGINT"


@POSIX
def test_the_check_lock_is_held_while_the_command_runs_and_not_inherited(
        build, declare):
    # Core's check held the machine's check lock around the run; the command did not
    # inherit its descriptor.
    path, _tree, _made = build
    lock = checking.lock_file()
    declare(f"""
        import fcntl, os, sys
        lock = {str(lock)!r}
        mine = [one for one in os.listdir("/proc/self/fd")
                if os.path.realpath(f"/proc/self/fd/{{one}}") == lock]
        fd = os.open(lock, os.O_RDWR)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            sys.exit(5)
        except BlockingIOError:
            sys.exit(6 if mine else 0)
        """)

    assert checking.check(ROUND, "B1", by="coder").exit_code == 0


def test_with_no_wait_a_held_check_lock_refuses_with_nothing_run(
        build, declare, holder):
    # Core's --no-wait refused at once, naming the holder.
    path, _tree, made = build
    declare(PASS)
    held = holder(checking.lock_file())

    with pytest.raises(Refused) as caught:
        checking.check(ROUND, "B1", by="coder", wait=False)
    held.stdin.close()

    [unmet] = caught.value.unmet
    assert unmet.reason == (f"another check holds or waits for the machine's check lock "
                            f"{checking.lock_file()}: held by pid {held.own_pid}; 0 waiting")
    assert ran(path) == [] and not list(made.scratch.glob("check-*.log"))


def test_a_run_waits_for_the_check_lock_and_says_who_holds_it(build, declare, holder):
    # Core's check waited for the lock by default and said once who held it.
    import threading

    path, _tree, _made = build
    declare(PASS)
    held = holder(checking.lock_file())
    said: list[str] = []
    threading.Timer(2.0, held.stdin.close).start()

    done = checking.check(ROUND, "B1", by="coder", say=said.append)

    assert done.passed and len(ran(path)) == 1
    assert said[0] == (f"waiting for the machine's check lock, held by pid {held.own_pid}; "
                       "0 ahead of you")
    assert said[1].startswith("got the check lock after ")
    assert done.duration_s < 2.0
    assert check_line.tickets(checking.lock_file()) == []
    assert not check_line.holder_file(checking.lock_file()).exists()


# -- the check lock's line ---------------------------------------------------------------

_WAITER = textwrap.dedent("""
    import sys, time
    from moxi_rounds import checking
    order, name = sys.argv[1], sys.argv[2]
    held = checking._Held(name=name, step="check")
    with checking._check_lock(held, checking._Look(unmet=[])):
        with open(order, "a", encoding="utf-8") as fh:
            fh.write(name + "\\n")
        time.sleep(0.2)
    """)


@pytest.fixture
def waiters(fenced, tmp_path):
    """`waiters.start(name)`: a real process that waits in the check lock's line as the
    round `name`, writes `name` to `waiters.order` once it holds the lock, and lets it
    go; it returns once the process's ticket is in the line, or it has taken the lock.
    Every process left is killed when the test ends."""
    from moxi_dev.testing import PROC_WAIT_S, wait_for

    class Waiters:
        order = tmp_path / "order.txt"
        procs: list = []

        def start(self, name: str):
            before = len(check_line.tickets(checking.lock_file()))
            proc = subprocess.Popen([PY, "-c", _WAITER, str(self.order), name])
            self.procs.append(proc)
            wait_for(lambda: len(check_line.tickets(checking.lock_file())) > before
                     or name in self.taken(), PROC_WAIT_S, what=f"the ticket of {name}")
            return proc

        def taken(self) -> list[str]:
            if not self.order.exists():
                return []
            return self.order.read_text(encoding="utf-8").split()

    found = Waiters()
    yield found
    for proc in found.procs:
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=PROC_WAIT_S)


def _ticket(fields: dict, *, touched_s: float | None = None) -> Path:
    path = check_line.take(checking.lock_file(), fields)
    if touched_s is not None:
        os.utime(path, (touched_s, touched_s))
    return path


def test_check_lock_waiters_take_the_lock_in_the_order_they_asked(holder, waiters):
    from moxi_dev.testing import PROC_WAIT_S

    held = holder(checking.lock_file())
    started = [waiters.start(name) for name in ("first", "second", "third")]

    held.stdin.close()
    for proc in started:
        assert proc.wait(timeout=PROC_WAIT_S) == 0

    assert waiters.taken() == ["first", "second", "third"]
    assert check_line.tickets(checking.lock_file()) == []


def test_a_live_ticket_ahead_holds_a_waiter_back_from_a_free_lock(waiters):
    import time

    from moxi_dev.testing import PROC_WAIT_S

    ahead = _ticket({**check_line.me(), "round": "proj-a/ahead"})
    behind = waiters.start("behind")
    time.sleep(6 * check_line.LINE_POLL_S)

    assert behind.poll() is None and waiters.taken() == []
    check_line.remove(ahead)
    assert behind.wait(timeout=PROC_WAIT_S) == 0
    assert waiters.taken() == ["behind"]
    assert check_line.tickets(checking.lock_file()) == []


def test_a_dead_waiters_ticket_is_removed_and_the_next_goes(holder, waiters):
    from moxi_dev.testing import PROC_WAIT_S

    held = holder(checking.lock_file())
    killed = waiters.start("killed")
    after = waiters.start("after")
    killed.kill()
    killed.wait(timeout=PROC_WAIT_S)

    held.stdin.close()

    assert after.wait(timeout=PROC_WAIT_S) == 0
    assert waiters.taken() == ["after"]
    assert check_line.tickets(checking.lock_file()) == []


def test_a_ticket_with_a_reused_pid_is_dead():
    mine = check_line.me()
    path = _ticket({**mine, "created": mine["created"] - 3600, "round": "proj-a/x"})

    assert check_line.remove_dead(checking.lock_file()) == []
    assert not path.exists()


def test_a_stale_ticket_is_passed_over_and_keeps_its_place():
    import time

    lock = checking.lock_file()
    stale = _ticket({**check_line.me(), "round": "proj-a/stale"},
                    touched_s=time.time() - check_line.TICKET_STALE_S - 5)
    own = _ticket({**check_line.me(), "round": "proj-a/own"})

    assert check_line.remove_dead(lock, own=own) == []
    assert stale.exists()

    os.utime(stale)
    assert [one.path for one in check_line.remove_dead(lock, own=own)] == [stale]


def test_no_wait_is_refused_while_a_ticket_waits(build, declare):
    path, _tree, made = build
    declare(PASS)
    _ticket({**check_line.me(), "round": "proj-a/other"})

    with pytest.raises(Refused) as caught:
        checking.check(ROUND, "B1", by="coder", wait=False)

    [unmet] = caught.value.unmet
    assert unmet.reason == (f"another check holds or waits for the machine's check lock "
                            f"{checking.lock_file()}: held by no one; 1 waiting")
    assert ran(path) == [] and not list(made.scratch.glob("check-*.log"))


def test_an_interrupted_waiter_removes_its_ticket(holder):
    holder(checking.lock_file())

    def interrupt(_line):
        raise KeyboardInterrupt

    held = checking._Held(say=interrupt, name="proj-a/x", step="check")
    with pytest.raises(KeyboardInterrupt), checking._check_lock(
            held, checking._Look(unmet=[])):
        pytest.fail("the lock was taken")

    assert check_line.tickets(checking.lock_file()) == []


@pytest.fixture
def refused_removal(monkeypatch):
    """`refused_removal(times=None)`: removing a ticket or the holder record raises
    `PermissionError`, as Windows does while another process has the file open, `times`
    times, or every time with None. Returns the list each refused path is added to."""
    real = Path.unlink

    def start(times: int | None = None) -> list[Path]:
        lock = checking.lock_file()
        refused: list[Path] = []

        def unlink(self, *args, **kwargs):
            if ((self.parent == check_line.line_dir(lock)
                 or self == check_line.holder_file(lock))
                    and (times is None or len(refused) < times)):
                refused.append(self)
                raise PermissionError(13, "the file is open in another process", str(self))
            return real(self, *args, **kwargs)

        monkeypatch.setattr(Path, "unlink", unlink)
        return refused

    return start


def test_a_removal_refused_for_a_moment_is_tried_again(refused_removal):
    path = _ticket({**check_line.me(), "round": "proj-a/x"})
    refused = refused_removal(times=2)

    assert check_line.remove(path)
    assert refused == [path, path] and not path.exists()


def test_a_removal_refused_past_its_retry_never_ends_the_check(
        build, declare, refused_removal, monkeypatch):
    path, _tree, _made = build
    declare(PASS)
    monkeypatch.setattr(check_line, "REMOVE_RETRY_S", 0.1)
    refused = refused_removal()
    lock = checking.lock_file()

    done = checking.check(ROUND, "B1", by="coder")

    assert done.passed and len(ran(path)) == 1
    assert {one.parent for one in refused} == {check_line.line_dir(lock), lock.parent}
    assert check_line.holder_file(lock).exists()
    assert [one.path for one in check_line.tickets(lock)] == [refused[0]]


def test_the_holder_record_names_the_run_and_its_output(build, declare):
    path, _tree, made = build
    declare(f"""
        import json, pathlib
        record = pathlib.Path({str(check_line.holder_file(checking.lock_file()))!r})
        print(record.read_text(encoding="utf-8"))
        """)

    done = checking.check(ROUND, "B1", by="coder")

    record = json.loads(done.output.read_text(encoding="utf-8"))
    assert record["pid"] == os.getpid() and record["round"] == ROUND
    assert (record["stage"], record["step"], record["repo"]) == ("B1", "check", "proj-a")
    assert record["output"] == str(done.output) and record["created"]
    assert not check_line.holder_file(checking.lock_file()).exists()


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

    checking.check(ROUND, "B1", by="coder")

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
        checking.check(ROUND, "B1", by="coder")

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
        checking.check(ROUND, "B1", by="coder")

    assert "moved" in str(caught.value) and "nothing is recorded" in str(caught.value)
    assert path.read_bytes() == before


def test_a_file_left_in_the_worktree_by_the_run_records_nothing(build, declare):
    path, _tree, _made = build
    declare("open('left.txt', 'w').write('left')")
    before = path.read_bytes()

    with pytest.raises(Refused) as caught:
        checking.check(ROUND, "B1", by="coder")

    assert "changed while the check ran" in str(caught.value)
    assert path.read_bytes() == before


# -- what it refuses before the run ------------------------------------------------------

def refused(path: Path) -> str:
    before = path.read_bytes()
    with pytest.raises(Refused) as caught:
        checking.check(ROUND, "B1", by="coder")
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
    opening.open_round(ROUND, description="a placeholder round", by="planner",
                       flow="fix")
    path = log.round_log(tmp_path / "notes" / "rounds" / "a-round")
    log.append(path, "stage.declared",
               {"id": "B1", "repo": "proj-a", "branch": "work", "after": []},
               by="planner")
    commissioning.commission(ROUND, "B1", builder="coder", by="planner")
    declare(PASS)

    assert "B1 has no work to check: it reads empty" in refused(path)


def test_a_stage_never_commissioned_is_refused(repo, declare, tmp_path):
    opening.open_round(ROUND, description="a placeholder round", by="planner",
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
    checking.check(ROUND, "B1", by="coder")
    tip = tree.rev()

    assert checking.passes(folded(path), "B1", "check", tip=tip,
                           argv=[PY, str(Path(ran(path)[0]["argv"][1])), tip])


def test_a_pass_at_an_older_commit_does_not_count(build, declare):
    path, tree, _made = build
    declare(PASS)
    checking.check(ROUND, "B1", by="coder")
    argv = ran(path)[0]["argv"]
    newer = tree.commit("more.txt")

    why = checking.why_not(folded(path), "B1", "check", tip=newer, argv=argv)

    assert why is not None and "not at the tip" in why


def test_a_pass_of_a_command_the_repo_no_longer_declares_does_not_count(build, declare,
                                                                         repo):
    from moxi_rounds import settings as own

    path, tree, _made = build
    declare(PASS)
    checking.check(ROUND, "B1", by="coder")
    declare(PASS, "--more")
    build_now = checking.builds.current(folded(path), "B1")
    argv = checking.argv_now(own.load(), ROUND, build_now, root=repo.root, tip=tree.rev())

    why = checking.why_not(folded(path), "B1", "check", tip=tree.rev(), argv=argv)

    assert why is not None and "the command is now" in why


def test_a_failed_run_and_a_run_of_another_step_do_not_count(build, declare):
    path, tree, _made = build
    declare("raise SystemExit(1)")
    checking.check(ROUND, "B1", by="coder")
    argv = ran(path)[0]["argv"]
    log.append(path, "check.ran", {
        "stage": "B1", "step": "staging", "commit": tree.rev(), "merge_base": None,
        "argv": argv, "exit_code": 0, "duration_s": 0.1, "output": "placeholder.log"},
        by="coder")

    state = folded(path)
    assert "exited 1" in checking.why_not(state, "B1", "check", tip=tree.rev(), argv=argv)
    assert checking.newest(state, "B1", "check")["exit_code"] == 1
    assert checking.passes(state, "B1", "staging", tip=tree.rev(), argv=argv)


# -- the step ----------------------------------------------------------------------------

def step(path: Path, seen=None, **by_stage) -> steps.Step | None:
    """B1's check step, with what was read of the round: `seen` as `seen_of` gives
    it, or a `Seen` by stage."""
    return steps.one(folded(path), "check", "B1", name=ROUND, seen=seen or by_stage or None)


def test_the_check_step_is_not_due_before_the_build_is_commissioned(repo, tmp_path):
    opening.open_round(ROUND, description="a placeholder round", by="planner",
                       flow="fix")
    path = log.round_log(tmp_path / "notes" / "rounds" / "a-round")
    log.append(path, "stage.declared",
               {"id": "B1", "repo": "proj-a", "branch": "work", "after": []},
               by="planner")

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
    assert step(path, seen).state == steps.READY

    (made.worktree / "loose.txt").write_text("loose\n")
    seen = checking.seen_of(state, ROUND, settings=own.load())
    assert step(path, seen).state == steps.WAITING
    (made.worktree / "loose.txt").unlink()

    checking.check(ROUND, "B1", by="coder")
    seen = checking.seen_of(folded(path), ROUND, settings=own.load())
    assert step(path, seen).state == steps.DONE

    tree.commit("more.txt")
    seen = checking.seen_of(folded(path), ROUND, settings=own.load())
    assert step(path, seen).state == steps.READY


def test_a_build_whose_repo_declares_no_check_has_no_check_step(build):
    from moxi_rounds import settings as own

    path, _tree, _made = build
    seen = checking.seen_of(folded(path), ROUND, settings=own.load())

    assert seen["B1"].check is None
    assert step(path, seen) is None
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
    checking.check(ROUND, "B1", by="coder")
    declare(PASS)
    checking.check(ROUND, "B1", by="coder")

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

    ruling.rule(ROUND, item="skip:check:B1", record="comms:1234567", by="planner")

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
        opening.open_round(ROUND, description="a placeholder round", by="planner",
                           flow="mine")
        path = log.round_log(tmp_path / "notes" / "rounds" / "a-round")
        log.append(path, "stage.declared",
                   {"id": "B1", "repo": "proj-a", "branch": "work", "after": []},
                   by="planner")
        made = commissioning.commission(ROUND, "B1", builder="coder", by="planner")
        tree = type(repo)(made.worktree)
        tree.commit("work.txt", "the work")
        capsys.readouterr()
        return path, tree, made, script

    return make


def test_a_command_step_runs_its_argv_in_the_worktree_recorded_under_its_name(stepped):
    path, tree, made, script = stepped("import os; print(os.getcwd())", "{tip}",
                                       "{stage}")

    done = checking.check(ROUND, "B1", by="coder", step="staging")

    [event] = ran(path)
    assert (event["step"], event["commit"], event["argv"], event["exit_code"]) == (
        "staging", tree.rev(), [PY, str(script), tree.rev(), "B1"], 0)
    assert placed(event, "output") == made.scratch / "check-1.log"
    assert placed(event, "output").read_text().strip() == str(made.worktree)
    assert (done.step, done.passed) == ("staging", True)


def test_a_command_step_runs_in_a_repo_that_declares_no_check(stepped):
    path, _tree, _made, _script = stepped(PASS)

    checking.check(ROUND, "B1", by="coder", step="staging")

    assert [one["step"] for one in ran(path)] == ["staging"]


def test_a_command_step_s_own_time_limit_applies(stepped):
    path, _tree, _made, _script = stepped("import time; time.sleep(60)", timeout_s=1)

    checking.check(ROUND, "B1", by="coder", step="staging")

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
    path, _tree, _made, _script = stepped(PASS, check=shlex.join([PY, "-c", "pass"]))
    before = path.read_bytes()

    assert main(["check", ROUND, "B1", "--step", "check"]) == 2

    assert path.read_bytes() == before


@pytest.mark.parametrize("name", ["deploy", "security-review", "build"])
def test_a_step_that_is_no_command_step_of_the_flow_is_refused(stepped, name):
    path, _tree, _made, _script = stepped(PASS)
    before = path.read_bytes()

    with pytest.raises(Refused) as caught:
        checking.check(ROUND, "B1", by="coder", step=name)

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

    checking.check(ROUND, "B1", by="coder", step="staging")
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
        checking.check(ROUND, "B1", by="coder", step="staging")

    assert found.state == steps.WAITING
    assert "the worktree of B1 is not clean on its branch here" in [
        one.reason for one in found.waits]
    assert "holds changes not committed" in str(caught.value)

    (tree.root / "loose.txt").unlink()
    assert stepped_step(path).state == steps.READY


def test_a_run_of_one_step_is_never_read_for_another(stepped):
    path, _tree, _made, _script = stepped(PASS, check=shlex.join([PY, "-c", "pass"]))

    checking.check(ROUND, "B1", by="coder")
    assert (stepped_step(path).state, stepped_step(path, "check").state) == (
        steps.READY, steps.DONE)

    checking.check(ROUND, "B1", by="coder", step="staging")
    assert stepped_step(path).state == steps.DONE


def test_the_command_step_waits_on_git_where_git_was_not_read(stepped):
    path, _tree, _made, _script = stepped(PASS)

    found = steps.one(folded(path), "staging", "B1", name=ROUND)

    assert (found.state, found.unread) == (steps.WAITING, True)


def test_the_command_step_is_not_due_before_the_stage_is_commissioned(
        repo, write_config, tmp_path):
    write_config(STEPPED.format(argv=json.dumps([PY, "-c", "pass"]), limit=""))
    opening.open_round(ROUND, description="a placeholder round", by="planner",
                       flow="mine")
    path = log.round_log(tmp_path / "notes" / "rounds" / "a-round")
    log.append(path, "stage.declared",
               {"id": "B1", "repo": "proj-a", "branch": "work", "after": []},
               by="planner")

    assert steps.one(folded(path), "staging", "B1").state == steps.NOT_DUE


def test_next_prints_the_command_step_with_the_command_that_runs_it(stepped, capsys):
    from moxi_rounds import next_step

    _path, _tree, _made, _script = stepped(PASS)

    [group, *_] = next_step.read(ROUND)["groups"]

    assert (group["step"], group["stage"], group["state"]) == ("staging", "B1",
                                                               steps.READY)
    assert group["then"] == [f"moxi rounds check {ROUND} B1 --step staging, or moxi "
                             "rounds check --step staging in the build's worktree"]
    checking.check(ROUND, "B1", by="coder", step="staging")
    assert next_step.read(ROUND)["groups"][0]["step"] == "security-review"


def test_show_and_the_round_note_give_a_command_step_s_newest_run(stepped, tmp_path):
    from moxi_rounds import showing

    _path, tree, _made, _script = stepped(PASS)
    checking.check(ROUND, "B1", by="coder", step="staging")

    [row] = json.loads(showing.as_json(showing.show(ROUND)))["builds"]
    assert row["check"] is None
    assert [(one["step"], one["commit"]) for one in row["step_runs"]] == [
        ("staging", tree.rev())]
    assert (f"staging passed at {tree.rev()[:7]}, output"
            in showing.as_text(showing.show(ROUND)))
    note = (tmp_path / "notes" / "rounds" / "a-round" / "round-note.md").read_text()
    assert f"staging passed at {tree.rev()[:7]}" in note

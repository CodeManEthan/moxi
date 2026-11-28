"""Each way a check's run ends, through `checking.check` with real processes, and when
its place of the machine's check lock frees: a normal end, a timeout, an interrupt of
the rounds process, and the rounds process killed outright, that last on every system.
Also what `check.ran` says of the place, and `--no-wait` with two places. Every lock is
in the test's own state folder."""

from __future__ import annotations

import json
import os
import shlex
import signal
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from moxi import proc
from moxi_dev.testing import PROC_WAIT_S, wait_for
from test_check import ROUND, build, declare, ran, repo  # noqa: F401 -- fixtures
from test_check_slots import hold

from moxi_rounds import check_line, checking
from moxi_rounds.errors import Refused

POSIX = pytest.mark.skipif(sys.platform == "win32",
                           reason="POSIX signals, sessions and environments")
PY = sys.executable
PASS = "print('the check passed')"
#: Ends a process outright: SIGKILL, or on Windows, where there is none, TerminateProcess.
KILL = getattr(signal, "SIGKILL", signal.SIGTERM)

#: A leftover: it names itself in the file `argv[1]` (pid, start time and the tag its
#: environment carries), then sleeps; on SIGINT or SIGTERM it writes, beside that file,
#: the signal's name and whether the holder record `argv[2]` was still there, and exits.
LEFTOVER = textwrap.dedent("""
    import json, os, signal, sys, time
    from moxi import proc
    path, holder = sys.argv[1], sys.argv[2]
    def stop(signum, frame):
        with open(path + ".signal", "w", encoding="utf-8") as fh:
            json.dump({"signal": signal.Signals(signum).name,
                       "held": os.path.exists(holder)}, fh)
        sys.exit(0)
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    with open(path + ".tmp", "w", encoding="utf-8") as fh:
        json.dump({"pid": os.getpid(), "started": proc.identity(os.getpid()).started,
                   "tag": os.environ.get(proc.RUN_TAG_ENV)}, fh)
    os.replace(path + ".tmp", path)
    time.sleep(120)
    """)

#: A rounds process that runs the check of B1 in the round `argv[1]`.
ROUNDS = textwrap.dedent("""
    import sys
    from moxi_rounds import checking
    checking.check(sys.argv[1], "B1", by="coder")
    """)


def holder_path() -> Path:
    return check_line.holder_file(checking.lock_file())


def command(out: Path, *, setsid: bool, then: str = "", trap: bool = False) -> str:
    """A check's command: it starts a leftover (in a session of its own when `setsid`),
    waits until the leftover has named itself, runs `then`, and ends. With `trap` the
    command itself writes `first.signal` on SIGINT and exits."""
    return textwrap.dedent(f"""
        import os, pathlib, signal, subprocess, sys, time
        out = pathlib.Path({str(out)!r})
        def stop(signum, frame):
            (out / "first.signal").write_text(signal.Signals(signum).name)
            sys.exit(3)
        if {trap!r}:
            signal.signal(signal.SIGINT, stop)
        subprocess.Popen([sys.executable, "-c", {LEFTOVER!r}, str(out / "left"),
                          {str(holder_path())!r}], start_new_session={setsid!r})
        while not (out / "left").exists():
            time.sleep(0.01)
        (out / "first").write_text(str(os.getpid()))
        """) + textwrap.dedent(then)


@pytest.fixture
def out(tmp_path):
    """The folder a check's command and its leftover write what they are to."""
    found = tmp_path / "out"
    found.mkdir()
    return found


@pytest.fixture
def rounds_proc(fenced):
    """`rounds_proc(round)`: a real rounds process running the check of B1 in `round`;
    killed when the test ends if still running. Leftovers a test names are killed too
    (`rounds_proc.leftovers`)."""
    procs: list[subprocess.Popen] = []
    leftovers: list[proc.ProcId] = []

    def start(name: str, script: str = ROUNDS) -> subprocess.Popen:
        one = subprocess.Popen([PY, "-c", script, name])
        procs.append(one)
        return one

    start.leftovers = leftovers
    yield start
    for one in procs:
        if one.poll() is None:
            one.kill()
        one.wait(timeout=PROC_WAIT_S)
    for found in leftovers:
        if proc.alive(found):
            os.kill(found.pid, KILL)


def leftover(out: Path, name: str = "left") -> dict:
    """What the leftover wrote of itself, once it has."""
    path = out / name
    wait_for(path.exists, PROC_WAIT_S, what=f"{name} to name itself")
    return json.loads(path.read_text(encoding="utf-8"))


def ident(found: dict) -> proc.ProcId:
    return proc.ProcId(found["pid"], found["started"])


def signalled(out: Path, name: str = "left") -> dict:
    return json.loads((out / f"{name}.signal").read_text(encoding="utf-8"))


def end(found: proc.ProcId) -> None:
    """Kill the process `found` names, and wait until it no longer runs."""
    os.kill(found.pid, KILL)
    wait_for(lambda: not proc.alive(found), PROC_WAIT_S, what=f"pid {found.pid} to end")


def free_now() -> bool:
    """Whether a run takes the check lock now, without waiting: the place is let go at
    once."""
    try:
        with check_line.slot(checking.lock_file(), {**check_line.me(), "round": "x/y"},
                             wait=False):
            return True
    except check_line.NotFree:
        return False


# -- while rounds lives ------------------------------------------------------------------

@POSIX
@pytest.mark.parametrize("setsid", [False, True], ids=["group member", "tagged setsid"])
def test_a_normal_end_frees_the_place_only_after_its_leftover_is_ended(
        build, declare, out, setsid):
    path, _tree, _made = build
    declare(command(out, setsid=setsid))

    done = checking.check(ROUND, "B1", by="coder")

    assert done.exit_code == 0
    left = leftover(out)
    assert left["tag"] and not proc.alive(ident(left))
    assert signalled(out) == {"signal": "SIGINT", "held": True}
    assert not holder_path().exists() and free_now()
    assert ran(path)[0]["check_lock"] == 1


@POSIX
def test_a_timeout_ends_the_tagged_leftover_before_the_place_frees(build, declare, out):
    path, _tree, _made = build
    declare(command(out, setsid=True, then="time.sleep(60)"), timeout_s=1)

    done = checking.check(ROUND, "B1", by="coder")

    assert done.exit_code is None
    assert not proc.alive(ident(leftover(out)))
    assert signalled(out) == {"signal": "SIGINT", "held": True}
    assert not holder_path().exists() and free_now()


@POSIX
def test_an_interrupt_of_rounds_signals_the_run_first_and_frees_the_place_after(
        build, declare, out, rounds_proc):
    path, _tree, _made = build
    declare(command(out, setsid=True, then="time.sleep(60)", trap=True))
    rounds = rounds_proc(ROUND)
    left = leftover(out)
    rounds_proc.leftovers.append(ident(left))
    wait_for((out / "first").exists, PROC_WAIT_S, what="the command to start")

    rounds.send_signal(signal.SIGINT)

    assert rounds.wait(timeout=PROC_WAIT_S) != 0
    assert (out / "first.signal").read_text() == "SIGINT"
    assert signalled(out) == {"signal": "SIGINT", "held": True}
    assert not proc.alive(ident(left))
    assert not holder_path().exists() and free_now()
    assert ran(path) == []
    assert check_line.tickets(checking.lock_file()) == []


# -- rounds killed outright --------------------------------------------------------------

def _kill_rounds(rounds: subprocess.Popen) -> None:
    rounds.kill()
    rounds.wait(timeout=PROC_WAIT_S)


def _named_run() -> dict:
    """The holder record, once it names its run's first process."""
    return wait_for(lambda: (found := check_line.read_holder(checking.lock_file()))
                    and "run_pid" in found and found, PROC_WAIT_S,
                    what="the holder record to name the run")


@POSIX
def test_a_holder_killed_after_its_command_ended_lets_the_next_run_in_at_once(
        build, declare, out, rounds_proc):
    path, _tree, _made = build
    declare(f"import pathlib, time; pathlib.Path({str(out / 'first')!r}).write_text('x'); "
            "time.sleep(120)")
    rounds = rounds_proc(ROUND)
    record = _named_run()
    first = proc.ProcId(record["run_pid"], record["run_started"])
    rounds_proc.leftovers.append(first)
    wait_for((out / "first").exists, PROC_WAIT_S, what="the command to start")

    _kill_rounds(rounds)
    end(first)
    declare(PASS)

    done = checking.check(ROUND, "B1", by="coder", wait=False)

    assert done.passed
    assert [one["check_lock"] for one in ran(path)] == [1]
    assert not holder_path().exists()


def test_a_rounds_process_terminated_mid_run_frees_the_place_once_its_command_is_gone(
        build, declare, out, rounds_proc):
    """On every system. On Windows the command ends with the rounds process (its job);
    on Linux and macOS it outlives it and is ended here."""
    path, _tree, _made = build
    declare(f"import pathlib, time; pathlib.Path({str(out / 'first')!r}).write_text('x'); "
            "time.sleep(120)")
    rounds = rounds_proc(ROUND)
    record = _named_run()
    first = proc.ProcId(record["run_pid"], record["run_started"])
    rounds_proc.leftovers.append(first)
    wait_for((out / "first").exists, PROC_WAIT_S, what="the command to start")

    _kill_rounds(rounds)
    if sys.platform != "win32":
        end(first)
    wait_for(lambda: not proc.alive(first), PROC_WAIT_S, what="the command to end")
    declare(PASS)

    done = checking.check(ROUND, "B1", by="coder", wait=False)

    assert done.passed
    assert [one["check_lock"] for one in ran(path)] == [1]
    assert not holder_path().exists()


@POSIX
def test_a_holder_killed_while_its_command_and_a_tagged_child_run_keeps_the_place(
        build, declare, out, rounds_proc):
    path, _tree, _made = build
    declare(command(out, setsid=True, then="time.sleep(120)"))
    rounds = rounds_proc(ROUND)
    left = leftover(out)
    rounds_proc.leftovers.append(ident(left))
    record = _named_run()
    first = proc.ProcId(record["run_pid"], record["run_started"])
    rounds_proc.leftovers.append(first)

    _kill_rounds(rounds)
    assert check_line.orphan(checking.lock_file())["run_tag"] == left["tag"]
    with pytest.raises(Refused):
        checking.check(ROUND, "B1", by="coder", wait=False)

    end(first)  # the command has ended, its tagged child runs on
    assert check_line.orphan(checking.lock_file()) is not None
    with pytest.raises(Refused):
        checking.check(ROUND, "B1", by="coder", wait=False)

    end(ident(left))
    declare(PASS)
    assert checking.check(ROUND, "B1", by="coder", wait=False).passed
    assert [one["check_lock"] for one in ran(path)] == [1]


#: A rounds process that dies once its holder record is written and before its command
#: starts: in place of the command (the one run given a tag), it starts a leftover
#: carrying the run's tag, waits for it, then kills itself.
DIES_BEFORE_START = textwrap.dedent("""
    import os, signal, subprocess, sys, time
    from moxi import proc
    from moxi_rounds import checking
    left, holder, leftover = sys.argv[2], sys.argv[3], sys.argv[4]
    real = proc.run

    def dying(argv, **options):
        if "tag" not in options:  # git's reads before the run
            return real(argv, **options)
        env = dict(os.environ)
        env[proc.RUN_TAG_ENV] = options["tag"]
        subprocess.Popen([sys.executable, "-c", leftover, left, holder], env=env,
                         start_new_session=True)
        while not os.path.exists(left):
            time.sleep(0.01)
        os.kill(os.getpid(), signal.SIGKILL)

    proc.run = dying
    checking.check(sys.argv[1], "B1", by="coder")
    """)


@POSIX
def test_a_holder_killed_before_its_command_started_keeps_the_place_while_tagged(
        build, declare, out, rounds_proc):
    path, _tree, _made = build
    declare(PASS)
    rounds = subprocess.Popen([PY, "-c", DIES_BEFORE_START, ROUND, str(out / "left"),
                               str(holder_path()), LEFTOVER])
    left = leftover(out)
    rounds_proc.leftovers.append(ident(left))
    assert rounds.wait(timeout=PROC_WAIT_S) == -signal.SIGKILL

    record = check_line.read_holder(checking.lock_file())
    assert record["run_tag"] == left["tag"] and "run_pid" not in record
    with pytest.raises(Refused):
        checking.check(ROUND, "B1", by="coder", wait=False)

    end(ident(left))
    assert checking.check(ROUND, "B1", by="coder", wait=False).passed
    assert ran(path)[0]["check_lock"] == 1


# -- the record and --no-wait with two places --------------------------------------------

@pytest.fixture
def two_places(write_config, monkeypatch, tmp_path):
    """`two_places(code, available_mib)`: make `proj-a`'s check a script holding `code`
    with `memory_mib = 100`, two places of the check lock, and the free memory read as
    `available_mib`."""

    def configure(code: str, available_mib: int) -> None:
        script = tmp_path / "check.py"
        script.write_text(textwrap.dedent(code), encoding="utf-8")
        line = shlex.join([PY, str(script)])
        write_config(f"[check_lock]\nat_once = 2\n\n[projects.proj-a]\n"
                     f"check = {{ command = {json.dumps(line)}, memory_mib = 100 }}\n")
        monkeypatch.setattr(check_line, "available_mib", lambda: available_mib)

    return configure


def test_a_run_alone_records_its_place_and_no_reading(build, declare):
    path, _tree, _made = build
    declare(PASS)

    checking.check(ROUND, "B1", by="coder")

    [event] = ran(path)
    assert event["check_lock"] == 1
    assert "available_mib" not in event and "need_mib" not in event


def test_a_run_beside_another_records_its_place_the_reading_and_the_need(
        build, two_places, holder):
    path, _tree, _made = build
    two_places(PASS, 5000)
    hold(holder, 1, memory_mib=100)

    assert checking.check(ROUND, "B1", by="coder").passed

    [event] = ran(path)
    assert (event["check_lock"], event["available_mib"], event["need_mib"]) == (2, 5000, 200)


def test_no_wait_with_both_places_held_is_refused_naming_each_holder(
        build, two_places, holder):
    path, _tree, made = build
    two_places(PASS, 5000)
    hold(holder, 1, memory_mib=100, name="proj-a/one")
    hold(holder, 2, memory_mib=100, name="proj-a/two")

    with pytest.raises(Refused) as caught:
        checking.check(ROUND, "B1", by="coder", wait=False)

    [unmet] = caught.value.unmet
    assert unmet.reason == (f"another check holds or waits for the machine's check lock "
                            f"{checking.lock_file()}: held by proj-a/one B1 for 0 min, "
                            "proj-a/two B1 for 0 min; 0 waiting")
    assert ran(path) == [] and not list(made.scratch.glob("check-*.log"))


def test_no_wait_short_of_memory_is_refused_with_the_reading_and_the_need(
        build, two_places, holder):
    path, _tree, _made = build
    two_places(PASS, 150)
    hold(holder, 1, memory_mib=100, name="proj-a/one")

    with pytest.raises(Refused) as caught:
        checking.check(ROUND, "B1", by="coder", wait=False)

    [unmet] = caught.value.unmet
    assert unmet.reason.endswith("held by proj-a/one B1 for 0 min; 0 waiting; "
                                 "0.1G available, 0.2G needed")
    assert ran(path) == []

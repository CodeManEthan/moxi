"""The "watch" lifeline on POSIX: a watched child has the terminal as a plain child does,
and its watchdog ends it and its descendants when the parent goes.

The terminal tests run a small job-control shell on a pseudo-terminal: the shell is the
session leader, starts the parent in a process group of its own and, for a foreground
job, makes that group the terminal's foreground group. The parent starts the child either
plainly or with `lifeline="watch"`, and each test checks that the two behave alike. Every
process writes what it sees as JSON lines to one report file; the tests poll it, and the
processes' states, against deadlines.

Windows holds a watched child in a Job Object instead; its rows are in test_proc and
test_win.
"""

from __future__ import annotations

import fcntl
import json
import os
import signal
import struct
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

if sys.platform == "win32":
    pytest.skip("POSIX terminals, sessions and process groups", allow_module_level=True)

import termios  # noqa: E402

import psutil  # noqa: E402

from moxi import _watch, paths, proc  # noqa: E402

PY = sys.executable
KINDS = ["plain", "watch"]

SHELL = """
    import fcntl, os, signal, subprocess, sys, termios
    fcntl.ioctl(0, termios.TIOCSCTTY, 0)
    mode, argv = sys.argv[1], sys.argv[2:]
    job = subprocess.Popen(argv, process_group=0)
    if mode == "fg":
        signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGTTOU})
        os.tcsetpgrp(0, job.pid)
    job.wait()
"""

PARENT = """
    import json, os, signal, subprocess, sys, time
    from moxi import proc
    report, kind, mode, argv = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4:]

    def say(**fields):
        with open(report, "a") as out:
            out.write(json.dumps({"who": "parent", **fields}) + "\\n")

    signal.signal(signal.SIGINT, lambda *_: say(got="SIGINT"))
    if mode == "fg":
        deadline = time.monotonic() + 10
        while os.tcgetpgrp(0) != os.getpgrp() and time.monotonic() < deadline:
            time.sleep(0.02)
    if kind == "watch":
        child = proc.spawn(argv, lifeline="watch")
        watchdog = child.watchdog.pid
    else:
        child = subprocess.Popen(argv)
        watchdog = None
    say(pid=os.getpid(), pgid=os.getpgrp(), sid=os.getsid(0), child=child.pid,
        watchdog=watchdog)
    while True:
        time.sleep(0.2)
"""

DEAF_SLEEP = ("import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
              "signal.signal(signal.SIGHUP, signal.SIG_IGN); time.sleep(120)")

CHILD = f"""
    import json, os, signal, subprocess, sys, time
    report, flags = sys.argv[1], set(sys.argv[2:])

    def say(**fields):
        with open(report, "a") as out:
            out.write(json.dumps({{"who": "child", **fields}}) + "\\n")

    signal.signal(signal.SIGWINCH, lambda *_: say(got="SIGWINCH"))
    signal.signal(signal.SIGINT, lambda *_: say(got="SIGINT"))
    if "deaf" in flags:
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        signal.signal(signal.SIGHUP, signal.SIG_IGN)
    try:
        tty = os.open("/dev/tty", os.O_RDWR)
    except OSError:
        tty = None
    grandchild = None
    if "grandchild" in flags:
        grandchild = subprocess.Popen([sys.executable, "-c", {DEAF_SLEEP!r}]).pid
    say(pid=os.getpid(), pgid=os.getpgrp(), sid=os.getsid(0), tty=tty is not None,
        grandchild=grandchild)
    if "read" in flags:
        # Read only once the parent has said who it is: the read stops it too.
        while '"parent"' not in open(report).read():
            time.sleep(0.02)
        try:
            os.read(tty, 1)
        except OSError:
            pass
    while True:
        time.sleep(0.2)
"""


def script(tmp_path: Path, name: str, text: str) -> str:
    path = tmp_path / name
    path.write_text(textwrap.dedent(text))
    return str(path)


def wait_for(check, timeout: float = 15.0, step: float = 0.02, between=None):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        if between is not None:
            between()
        time.sleep(step)
    return check()


def state(pid: int | None) -> str:
    """A process's state: psutil's status name, or "gone" (an unreaped zombie too)."""
    if not pid:
        return "gone"
    try:
        status = psutil.Process(pid).status()
    except psutil.Error:
        return "gone"
    return "gone" if status == psutil.STATUS_ZOMBIE else status


def stopped(pid: int) -> bool:
    return state(pid) == psutil.STATUS_STOPPED


def running(pid: int) -> bool:
    return state(pid) not in ("gone", psutil.STATUS_STOPPED)


def gone(*pids: int) -> bool:
    return all(state(pid) == "gone" for pid in pids)


class Report:
    """The JSON lines the parent and child write."""

    def __init__(self, path: Path):
        self.path = path
        path.write_text("")

    def records(self) -> list[dict]:
        text = self.path.read_text()
        return [json.loads(line) for line in text.splitlines() if line.endswith("}")]

    def find(self, who: str, key: str, value=None) -> dict | None:
        for record in self.records():
            if record["who"] == who and key in record and (
                    value is None or record[key] == value):
                return record
        return None


class Job:
    """A shell on a pseudo-terminal running the parent, which runs the child."""

    def __init__(self, tmp_path: Path, kind: str, mode: str, *flags: str):
        self.report = Report(tmp_path / "report.jsonl")
        self.master, slave = os.openpty()
        os.set_blocking(self.master, False)
        self.screen = b""
        argv = [PY, script(tmp_path, "shell.py", SHELL), mode,
                PY, script(tmp_path, "parent.py", PARENT), str(self.report.path), kind,
                mode, PY, script(tmp_path, "child.py", CHILD), str(self.report.path),
                *flags]
        try:
            self.shell = subprocess.Popen(argv, stdin=slave, stdout=slave, stderr=slave,
                                          start_new_session=True)
        finally:
            os.close(slave)
        self.parent: dict = {}
        self.child: dict = {}

    def drain(self) -> None:
        """Read what the terminal shows, so no writer to it ever blocks."""
        while True:
            try:
                part = os.read(self.master, 4096)
            except (BlockingIOError, OSError):
                return
            if not part:
                return
            self.screen += part

    def started(self) -> None:
        self.parent = self.wait_record("parent", "child")
        self.child = self.wait_record("child", "tty")

    def wait_record(self, who: str, key: str, value=None) -> dict:
        found = wait_for(lambda: self.report.find(who, key, value), between=self.drain)
        assert found, (f"no {who} record with {key}; report: {self.report.records()}; "
                       f"terminal: {self.screen!r}")
        return found

    def until(self, check, timeout: float = 15.0) -> bool:
        return wait_for(check, timeout, between=self.drain)

    def foreground(self) -> int:
        return os.tcgetpgrp(self.master)

    def type(self, data: bytes) -> None:
        os.write(self.master, data)

    def pids(self) -> list[int]:
        found = [self.parent.get("pid"), self.child.get("pid"),
                 self.child.get("grandchild"), self.parent.get("watchdog")]
        for record in self.report.records():
            found += [record.get(k) for k in ("pid", "child", "grandchild", "watchdog")]
        return sorted({pid for pid in found if pid})

    def ours(self, pid: int) -> bool:
        """Whether `pid` still names a process of this job, and not one that took a freed
        pid since: the job's session holds them all but the watchdog."""
        try:
            return (os.getsid(pid) == self.shell.pid
                    or "moxi._watch" in psutil.Process(pid).cmdline())
        except (OSError, psutil.Error):
            return False

    def close(self) -> None:
        for pid in filter(self.ours, self.pids()):
            for signum in (signal.SIGCONT, signal.SIGKILL):
                try:
                    os.kill(pid, signum)
                except OSError:
                    pass
        if self.shell.poll() is None:
            self.shell.kill()
        self.shell.wait(timeout=30)
        os.close(self.master)


@pytest.fixture
def job(tmp_path):
    made: list[Job] = []

    def make(kind: str, mode: str = "fg", *flags: str) -> Job:
        started = Job(tmp_path, kind, mode, *flags)
        made.append(started)
        started.started()
        return started

    yield make
    for each in made:
        each.close()


# -- the terminal ------------------------------------------------------------------------

@pytest.mark.parametrize("kind", KINDS)
def test_the_child_opens_the_terminal_and_hears_a_resize(job, kind):
    j = job(kind)
    assert j.child["tty"] is True
    assert (j.child["pgid"], j.child["sid"]) == (j.parent["pgid"], j.parent["sid"])
    assert j.foreground() == j.parent["pgid"]
    fcntl.ioctl(j.master, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 100, 0, 0))
    j.wait_record("child", "got", "SIGWINCH")
    assert j.foreground() == j.parent["pgid"]


@pytest.mark.parametrize("kind", KINDS)
def test_a_stop_from_the_terminal_stops_parent_and_child_together(job, kind):
    j = job(kind)
    group = j.parent["pgid"]
    assert j.foreground() == group
    j.type(b"\x1a")  # ^Z: SIGTSTP to the foreground group
    assert j.until(lambda: stopped(j.parent["pid"]) and stopped(j.child["pid"]))
    assert j.foreground() == group
    if kind == "watch":
        assert running(j.parent["watchdog"])
    os.killpg(group, signal.SIGCONT)
    assert j.until(lambda: running(j.parent["pid"]) and running(j.child["pid"]))
    assert j.foreground() == group


@pytest.mark.parametrize("kind", KINDS)
def test_ctrl_c_reaches_parent_and_child(job, kind):
    j = job(kind)
    j.type(b"\x03")
    j.wait_record("parent", "got", "SIGINT")
    j.wait_record("child", "got", "SIGINT")
    assert j.foreground() == j.parent["pgid"]


def test_the_watchdog_is_in_its_own_session_and_survives_ctrl_c(job):
    j = job("watch", "fg", "deaf")
    watchdog = j.parent["watchdog"]
    assert os.getsid(watchdog) == watchdog != j.parent["sid"]
    j.type(b"\x03")
    j.wait_record("child", "got", "SIGINT")
    time.sleep(0.2)  # a SIGINT that did reach the watchdog would have ended it by now
    assert running(watchdog)
    os.kill(j.parent["pid"], signal.SIGKILL)
    assert j.until(lambda: gone(j.child["pid"], watchdog))


@pytest.mark.parametrize("kind", KINDS)
def test_a_background_read_stops_parent_and_child(job, kind):
    j = job(kind, "bg", "read", "deaf")
    group = j.parent["pgid"]
    shell = j.foreground()
    assert shell != group
    assert j.until(lambda: stopped(j.parent["pid"]) and stopped(j.child["pid"]))
    assert j.foreground() == shell
    if kind == "watch":
        # The parent goes while the group is stopped; its watchdog still ends the child.
        os.kill(j.parent["pid"], signal.SIGKILL)
        assert j.until(lambda: gone(j.child["pid"], j.parent["watchdog"]))


def test_closing_the_lifeline_ends_a_deaf_child_and_grandchild_within_three_seconds(job):
    j = job("watch", "fg", "deaf", "grandchild")
    child, grandchild = j.child["pid"], j.child["grandchild"]
    assert running(child) and running(grandchild)
    os.kill(j.parent["pid"], signal.SIGKILL)
    begun = time.monotonic()
    assert j.until(lambda: gone(child, grandchild), timeout=10)
    assert time.monotonic() - begun < 3.0
    assert j.until(lambda: gone(j.parent["watchdog"]), timeout=10)


def test_a_parent_with_no_terminal_spawns_and_ends_the_child_the_same(tmp_path):
    report = Report(tmp_path / "report.jsonl")
    parent = subprocess.Popen(
        [PY, script(tmp_path, "parent.py", PARENT), str(report.path), "watch", "none",
         PY, script(tmp_path, "child.py", CHILD), str(report.path), "deaf", "grandchild"],
        stdin=subprocess.DEVNULL, start_new_session=True)
    child: dict = {}
    try:
        said = wait_for(lambda: report.find("parent", "child"))
        child = wait_for(lambda: report.find("child", "tty"))
        assert said and child
        assert child["tty"] is False
        assert (child["pgid"], child["sid"]) == (said["pgid"], said["sid"])
        assert os.getsid(said["watchdog"]) == said["watchdog"]
        parent.kill()
        parent.wait(timeout=30)
        assert wait_for(lambda: gone(child["pid"], child["grandchild"], said["watchdog"]))
    finally:
        if parent.poll() is None:
            parent.kill()
            parent.wait()
        for pid in (child.get("pid"), child.get("grandchild")):
            if pid and not gone(pid) and os.getsid(pid) == parent.pid:
                os.kill(pid, signal.SIGKILL)


# -- the watched set ---------------------------------------------------------------------

@pytest.fixture
def sleeper():
    """A process that is no one's watched child."""
    process = subprocess.Popen([PY, "-c", "import time; time.sleep(120)"])
    yield process
    process.kill()
    process.wait()


def test_a_reused_pid_is_never_signalled(sleeper):
    started = proc.identity(sleeper.pid).started
    watch = _watch.Watch(sleeper.pid, started - 100)
    assert watch.child() is None
    watch.watched[sleeper.pid] = started - 100
    assert watch.live() == [] and watch.done()
    watch.stop(grace=0.1)
    assert sleeper.poll() is None


def test_a_watchdog_whose_child_pid_was_reused_leaves_without_a_signal(sleeper):
    started = proc.identity(sleeper.pid).started
    watchdog = subprocess.Popen(
        [PY, "-m", "moxi._watch", str(sleeper.pid), repr(started - 100)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    assert watchdog.stdout.readline() == _watch.READY
    assert watchdog.wait(timeout=30) == 0
    watchdog.stdin.close()
    watchdog.stdout.close()
    assert sleeper.poll() is None


def test_a_zombie_child_counts_as_gone():
    zombie = subprocess.Popen([PY, "-c", "pass"])
    try:
        assert wait_for(lambda: psutil.Process(zombie.pid).status()
                        == psutil.STATUS_ZOMBIE)
        started = psutil.Process(zombie.pid).create_time()
        assert _watch.Watch(zombie.pid, started).done()
        watchdog = subprocess.Popen(
            [PY, "-m", "moxi._watch", str(zombie.pid), repr(started)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        assert watchdog.stdout.readline() == _watch.READY
        assert watchdog.wait(timeout=30) == 0  # with the lifeline still open
        watchdog.stdin.close()
        watchdog.stdout.close()
    finally:
        zombie.wait()


MIDDLE = f"""
import subprocess, sys, time
orphan = subprocess.Popen([sys.executable, "-c", {DEAF_SLEEP!r}])
print(orphan.pid, flush=True)
time.sleep(120)
"""

FAMILY = f"""
    import subprocess, sys, time
    middle = subprocess.Popen([sys.executable, "-c", {MIDDLE!r}], stdout=subprocess.PIPE)
    orphan = middle.stdout.readline().decode().strip()
    print(middle.pid, orphan, flush=True)
    middle.wait()
    time.sleep(120)
"""


def test_a_descendant_moved_away_after_a_scan_is_still_killed(tmp_path):
    child = subprocess.Popen([PY, script(tmp_path, "family.py", FAMILY)],
                             stdout=subprocess.PIPE)
    orphan = 0
    try:
        middle, orphan = map(int, child.stdout.readline().split())
        watch = _watch.Watch(child.pid, proc.identity(child.pid).started)
        watch.scan()
        assert {middle, orphan} <= set(watch.watched)
        os.kill(middle, signal.SIGKILL)
        assert wait_for(lambda: psutil.Process(orphan).ppid() != middle)
        assert orphan not in {p.pid for p in psutil.Process(child.pid).children(True)}
        watch.stop(grace=0.2)
        assert child.wait(timeout=30) == -signal.SIGTERM
        assert wait_for(lambda: gone(orphan))
        assert watch.done()
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()
        child.stdout.close()
        if orphan and not gone(orphan):
            os.kill(orphan, signal.SIGKILL)


def test_the_watchdog_kills_what_starts_while_it_waits_out_the_grace(tmp_path):
    forker = """
        import os, signal, sys, time
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        while True:
            if os.fork() == 0:
                signal.signal(signal.SIGTERM, signal.SIG_IGN)
                print(os.getpid(), flush=True)
                time.sleep(120)
                os._exit(0)
            time.sleep(0.1)
    """
    out = tmp_path / "kids.txt"
    with open(out, "wb") as sink:
        child = proc.spawn([PY, script(tmp_path, "forker.py", forker)], lifeline="watch",
                           stdout=sink)
    kids: list[int] = []
    try:
        assert wait_for(lambda: len(out.read_text().split()) >= 3)
        child.lifeline.close()
        assert child.wait(timeout=30) == -signal.SIGKILL
        assert child.watchdog.wait(timeout=30) == 0
        kids = [int(k) for k in out.read_text().split()]
        assert len(kids) > 10  # it kept forking through the grace
        assert wait_for(lambda: gone(*kids))
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()
        for kid in kids or [int(k) for k in out.read_text().split()]:
            if not gone(kid):
                os.kill(kid, signal.SIGKILL)


# -- a watchdog that fails to start ------------------------------------------------------

def fake_python(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "python"
    path.write_text("#!/bin/sh\n" + textwrap.dedent(body))
    path.chmod(0o755)
    return path


def test_a_watchdog_that_fails_to_start_kills_the_child_and_spawn_raises(
        tmp_path, monkeypatch):
    pids = tmp_path / "pids.txt"
    monkeypatch.setattr(paths, "python", lambda: fake_python(tmp_path, f"""
        while [ ! -s {pids} ]; do sleep 0.02; done
        echo "No module named moxi" >&2
        exit 1
    """))
    tree = f"""
        import subprocess, sys, time
        grandchild = subprocess.Popen([sys.executable, "-c", {DEAF_SLEEP!r}])
        with open({str(pids)!r}, "w") as out:
            out.write(f"{{grandchild.pid}}\\n")
        time.sleep(120)
    """
    before = children()
    with pytest.raises(OSError, match="did not start: No module named moxi"):
        proc.spawn([PY, script(tmp_path, "tree.py", tree)], lifeline="watch")
    grandchild = int(pids.read_text())
    assert wait_for(lambda: gone(grandchild))
    assert children() <= before  # the child and the watchdog were killed and reaped


def test_a_watchdog_that_never_says_ready_is_killed_and_spawn_raises(
        tmp_path, monkeypatch):
    monkeypatch.setattr(proc, "WATCH_START", 0.5)
    monkeypatch.setattr(paths, "python", lambda: fake_python(tmp_path, "exec sleep 60\n"))
    before = children()
    with pytest.raises(OSError, match="said nothing within 0.5 seconds"):
        proc.spawn([PY, "-c", "import time; time.sleep(60)"], lifeline="watch")
    assert children() <= before


def test_a_watchdog_that_writes_to_stderr_before_ready_still_starts(
        tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "python", lambda: fake_python(tmp_path, f"""
        echo "a line from the interpreter's startup" >&2
        exec {PY} "$@"
    """))
    child = proc.spawn([PY, "-c", "import time; time.sleep(60)"], lifeline="watch")
    child.lifeline.close()
    assert child.wait(timeout=30) == -signal.SIGTERM
    assert child.watchdog.wait(timeout=30) == 0


@pytest.mark.parametrize("variable, value", [("PYTHONWARNINGS", "bogus"),
                                             ("PYTHONPROFILEIMPORTTIME", "1")])
def test_a_watchdog_whose_interpreter_warns_at_startup_still_starts(monkeypatch,
                                                                    variable, value):
    monkeypatch.setenv(variable, value)
    child = proc.spawn([PY, "-c", "import time; time.sleep(60)"], lifeline="watch")
    child.lifeline.close()
    assert child.wait(timeout=30) == -signal.SIGTERM
    assert child.watchdog.wait(timeout=30) == 0


def test_a_failing_watchdog_is_named_by_its_last_stderr_line(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "python", lambda: fake_python(tmp_path, """
        echo "something on stdout"
        echo "the reason it stopped" >&2
        exit 1
    """))
    with pytest.raises(OSError, match="did not start: the reason it stopped"):
        proc.spawn([PY, "-c", "import time; time.sleep(60)"], lifeline="watch")


def children() -> set[int]:
    """This process's live children (a zombie, not yet reaped, counts)."""
    return {p.pid for p in psutil.Process().children()}

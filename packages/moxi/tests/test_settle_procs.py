"""The core lock's rule and the settle step's interrupts, with real processes.

A real parent runs `moxi install`'s settle loop under its own hold of the core lock, and
a real settle child (`python -m moxi._settle`) adopts the parent's claim and runs the
steps on the fake backend, whose supervisor keeps its state in a file every process
shares (`settle_procs.py`). The lock file is the real one, in tmp. The interrupt rows run
the parent as a foreground job on a pseudo-terminal, so the child shares the terminal and
its process group, and a Ctrl-C is `\\x03` typed at the terminal.

Every process logs what it does to one file; the tests read it against deadlines, and
kill whatever is left of the job when they end.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import textwrap
import threading
import time
import types
from pathlib import Path

import pytest

if sys.platform == "win32":
    pytest.skip("POSIX processes, descriptors and terminals; the Windows rows run on a "
                "Windows box", allow_module_level=True)

import psutil  # noqa: E402

import moxi.paths  # noqa: E402
from moxi import _settle, config, files, units  # noqa: E402
from moxi.units import _backend, _state  # noqa: E402
from moxi.units._fake import FakeBackend  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent))
import settle_procs  # noqa: E402

PY = sys.executable
HELPER = str(Path(settle_procs.__file__).resolve())
SERVICE = "moxi-demo-relay.service"
AUX = "moxi-demo-aux.service"
#: The longest any one wait in these tests lasts.
WAIT = 30.0

SETTINGS = """
[relay.enabled]
type = "bool"
default = false
doc = "Run the relay."
read = "use"

[snapshot.enabled]
type = "bool"
default = false
doc = "Take snapshots."
read = "use"
"""

SETUP = """
import os, time
from settle_procs import note

def run():
    how = os.environ.get("MOXITEST_SETUP", "ok")
    note("setup", begin=how)
    if how == "sleep":
        time.sleep(30)
    elif how == "nap":
        time.sleep(2)
    elif how == "catch":
        from moxi._settle import Interrupted
        try:
            time.sleep(30)
        except Interrupted:
            note("setup", caught=True)
            return None
    elif how == "switch":
        from moxi import config
        applied = config.set("demo", "relay.enabled", True, by="setup")
        note("setup", applied=[[a.change.name, a.change.action, a.ok] for a in applied])
    note("setup", end=how)
"""

TEARDOWN = """
import os, time
from settle_procs import note

def run():
    note("teardown", begin=True)
    if os.environ.get("MOXITEST_DOWN") == "quick":
        return None
    try:
        time.sleep(30)
    finally:
        note("teardown", ran_finally=True)
"""

#: An install listener whose calls each take the core lock nested, trying once: under an
#: install or uninstall they yield, so a call that waited would fail here instead.
LISTENER = """
import moxi.paths
from moxi import files
from settle_procs import note

def _yields(event, module):
    with files.lock(moxi.paths.core_lock(), nested=True, timeout=0):
        note("listener", **{event: module})

def on_install(module, found):
    _yields("installed", module)

def on_uninstall(module):
    _yields("uninstalled", module)
"""

ONE = "{'relay': {'command': 'moxi-demo-relay', 'doc': 'the relay'}}"
TWO = ("{'relay': {'command': 'moxi-demo-relay', 'doc': 'the relay'}, "
       "'aux': {'command': 'moxi-demo-relay'}}")
SWITCHED = "{'relay': {'command': 'moxi-demo-relay', 'switch': 'relay.enabled'}}"
TIMER = ("{'snapshot': {'command': 'moxi-demo-relay', 'every': '5m', "
         "'doc': 'take a snapshot', 'switch': 'snapshot.enabled'}}")


class Box:
    """A module `demo` on the fake backend, set up in this process, and the environment
    the real processes run it in."""

    def __init__(self, site, tmp: Path):
        self.site = site
        self.tmp = tmp
        self.root = tmp / "units"
        self.scripts = tmp / "bin"
        self.log = tmp / "log.jsonl"
        self.home = Path(os.environ["MOXI_HOME"])
        self.sup = settle_procs.Supervisor(self.root, tmp / "supervisor.json")
        self.backend = FakeBackend(self.root, runner=self.sup)
        layout = tmp / "box.json"
        layout.write_text(json.dumps({"units": str(self.root), "scripts": str(self.scripts),
                                      "supervisor": str(tmp / "supervisor.json")}))
        user = tmp / "user"
        self.env = {**os.environ, "PYTHONPATH": str(site.root),
                    settle_procs.BOX: str(layout), settle_procs.LOG: str(self.log),
                    "HOME": str(user), "USERPROFILE": str(user),
                    "XDG_CONFIG_HOME": str(user / ".config"),
                    "XDG_STATE_HOME": str(user / ".local/state"),
                    "XDG_DATA_HOME": str(user / ".local/share"),
                    "XDG_CACHE_HOME": str(user / ".cache"),
                    "MOXI_DEMO_DEVICE": "oldbox", "MOXITEST_SETUP": "ok"}
        for name in ("MOXI_TEST_REAL_SUPERVISOR", files.CLAIM_VARIABLE):
            self.env.pop(name, None)

    def module(self, *, listener: bool = False, **names):
        self.site.write("moxi_demo/settings.toml", SETTINGS)
        self.site.write("moxi_demo/setup.py", SETUP)
        self.site.write("moxi_demo/down.py", TEARDOWN)
        names.setdefault("SETTINGS", "'settings.toml'")
        names.setdefault("SETUP", "'moxi_demo.setup:run'")
        self.site.module("demo", **names)
        if listener:
            self.site.write("moxi_demo/installs.py", LISTENER)
            self.site.write("moxi_demo-0.1.0.dist-info/entry_points.txt",
                            "[moxi.modules]\ndemo = moxi_demo.module\n"
                            "[moxi.installs]\ndemo = moxi_demo.installs\n")
        moxi.modules.reset()

    def installed(self, *, listener: bool = False, **names):
        """The module with its units written and running, its timers' switches recorded,
        and its `SETUP` recorded as run at its version."""
        self.module(listener=listener, **names)
        for change in units.plan("demo"):
            if change.digest is not None:
                _state.record("demo", change.name, change.digest)
        done = units.apply(units.plan("demo"), say=lambda line: None)
        assert all(a.ok for a in done), done
        _settle.record_setup("demo", "0.1.0")

    def unmarked(self, unit: str = SERVICE):
        """A running unit file of the daemon's name that moxi did not write."""
        (self.root / unit).write_text("[Service]\nExecStart=/bin/true\n")
        self.sup.load()
        self.sup.reload()
        self.sup.state(unit).update(active=True, enabled=True)
        self.sup.save()

    def supervisor(self, **fields):
        """Change the shared supervisor: `sleep`, `fail` (a list of `[verb, unit, text]`),
        `probe`."""
        data = json.loads((self.tmp / "supervisor.json").read_text())
        data.update(fields)
        (self.tmp / "supervisor.json").write_text(json.dumps(data))
        self.sup.load()

    def legacy(self) -> Path:
        old = self.home / "state" / "oldbox" / "demo"
        old.mkdir(parents=True)
        (old / "data.txt").write_text("live")
        return old

    def events(self, who: str | None = None, key: str | None = None) -> list[dict]:
        return [e for e in settle_procs.events(self.log)
                if (who is None or e["who"] == who) and (key is None or key in e)]

    def value(self, who: str, key: str):
        found = self.events(who, key)
        return found[-1][key] if found else None

    def calls(self, *verbs) -> list[tuple[str, ...]]:
        """The supervisor calls the real processes made, as `(verb, unit...)`."""
        return [tuple(e["call"]) for e in self.events("sup", "call")
                if not verbs or e["call"][0] in verbs]

    def moved(self) -> bool:
        return (self.home / "state" / "demo" / "data.txt").exists()


@pytest.fixture
def box(site, tmp_path, monkeypatch):
    scripts = tmp_path / "bin"
    scripts.mkdir()
    (scripts / "moxi-demo-relay").write_text("#!/bin/sh\n")
    (scripts / "moxi-demo-relay").chmod(0o755)
    monkeypatch.setattr(moxi.paths, "_scripts", lambda: scripts)
    monkeypatch.setenv("MOXI_DEMO_DEVICE", "oldbox")
    (tmp_path / "units").mkdir()
    b = Box(site, tmp_path)
    b.sup.save()
    with _backend.use(b.backend):
        yield b


def wait_for(check, timeout: float = WAIT, between=None):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        if between is not None:
            between()
        time.sleep(0.02)
    return check()


def held_elsewhere() -> int | None:
    """The pid holding the core lock, or None when it is free."""
    try:
        with files.lock(moxi.paths.core_lock(), timeout=0):
            return None
    except files.LockTimeout as exc:
        return exc.holder or -1


def parent_run(box, *args, timeout: float = 60) -> subprocess.CompletedProcess:
    """The install's parent, with no terminal and in a process group of its own, until
    it exits; whatever is left of the group is killed."""
    proc = subprocess.Popen([PY, HELPER, "parent", *args], env=box.env,
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, process_group=0)
    try:
        out, err = proc.communicate(timeout=timeout)
    finally:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            pass
        proc.kill()
        proc.wait(WAIT)
    return subprocess.CompletedProcess(proc.args, proc.returncode, out, err)


# -- the lock rule ---------------------------------------------------------------------------

def test_a_settle_child_moves_applies_and_resumes_under_its_parents_lock(box):
    """The child's environment no longer holds the variable, yet each core call it makes
    (the move's stop, `apply`, the `finally`'s `resume`) yields on the claim: a call that
    took the lock as a standalone caller would wait for the parent forever."""
    box.installed(DAEMONS=ONE)
    box.module(DAEMONS=TWO)              # the unit step adds aux
    box.legacy()
    done = parent_run(box, "demo")
    assert done.returncode == 0, done.stdout + done.stderr
    started = box.events("child", "started")[0]
    assert started["variable"] is None and started["claim"] is True
    assert box.value("parent", "outcome") == 0
    assert box.moved()
    calls = box.calls("stop", "start", "enable")
    assert calls == [("stop", SERVICE), ("enable", "--now", AUX), ("start", SERVICE)]


@pytest.mark.parametrize("forged", ["a closed descriptor", "a descriptor on another file"])
def test_a_settle_child_given_a_forged_claim_exits_1_before_any_step(box, forged):
    box.installed(DAEMONS=ONE)
    old = box.legacy()
    other = os.open(box.tmp / "other", os.O_RDWR | os.O_CREAT)
    try:
        number, keep = ("999", ()) if forged == "a closed descriptor" else (str(other),
                                                                            (other,))
        env = {**box.env, files.CLAIM_VARIABLE: number}
        with files.lock(moxi.paths.core_lock()):
            done = subprocess.run([PY, HELPER, "child", "demo"], env=env, pass_fds=keep,
                                  stdin=subprocess.DEVNULL, capture_output=True,
                                  text=True, timeout=60)
    finally:
        os.close(other)
    assert done.returncode == 1, done.stderr
    assert "the install's lock claim did not check; nothing done" in done.stderr
    assert box.events("child", "started") == [] and box.events("setup") == []
    assert box.events("sup") == []
    assert (old / "data.txt").exists()


def test_config_set_of_a_daemon_switch_from_setup_yields_in_the_settle_child(box):
    box.module(DAEMONS=SWITCHED)
    box.env["MOXITEST_SETUP"] = "switch"
    done = parent_run(box, "demo")
    assert done.returncode == 0, done.stdout + done.stderr
    assert box.value("setup", "applied") == [["relay", "add", True]]
    assert box.calls("enable") == [("enable", "--now", SERVICE)]


def test_an_install_listener_s_core_call_yields_in_the_settle_child(box):
    box.module(DAEMONS=ONE, listener=True)
    box.env["MOXITEST_LATER"] = "1"
    done = parent_run(box, "demo")
    assert done.returncode == 0, done.stdout + done.stderr
    assert box.value("listener", "installed") == "demo"
    assert box.events(key="raised") == []


def test_uninstall_runs_teardown_in_a_child_then_listeners_units_and_package(box):
    """The whole uninstall under its own hold of the core lock: the teardown child adopts
    the claim, and the listener's core call and `units.remove` in the parent yield."""
    box.installed(DAEMONS=ONE, TEARDOWN="'moxi_demo.down:run'", listener=True)
    box.env["MOXITEST_DOWN"] = "quick"
    assert (box.root / SERVICE).exists()
    done = parent_run(box, "demo", "--teardown")
    assert done.returncode == 0, done.stdout + done.stderr
    assert box.value("child", "exit") == 0
    assert box.events("child", "started")[0]["claim"] is True
    assert box.value("listener", "uninstalled") == "demo"
    assert not (box.root / SERVICE).exists()
    ran = box.value("parent", "ran")
    assert ran[-1] == "moxi-demo" and "uninstall" in ran
    assert "uninstalled demo (moxi-demo 0.1.0)" in done.stdout
    assert box.events(key="raised") == []


HOLDER = textwrap.dedent("""
    import time
    from moxi import files, paths
    with files.lock(paths.core_lock()):
        print("held", flush=True)
        time.sleep(30)
""")


@pytest.fixture
def install_running(box):
    """`install_running()` starts another process that holds the core lock, as a running
    install does, and returns a function that ends it."""
    procs: list[subprocess.Popen] = []

    def start():
        proc = subprocess.Popen([PY, "-c", HOLDER], stdout=subprocess.PIPE, text=True,
                                env=box.env)
        procs.append(proc)
        assert proc.stdout.readline().strip() == "held"

        def end():
            proc.kill()
            proc.wait(WAIT)
        return end

    yield start
    for proc in procs:
        proc.kill()
        proc.wait(WAIT)


def in_thread(fn):
    done: list = []
    thread = threading.Thread(target=lambda: done.append(fn()), daemon=True)
    thread.start()
    return thread, done


@pytest.mark.parametrize("standalone", ["config set", "apply", "remove"])
def test_a_standalone_core_call_waits_for_a_running_install(box, install_running,
                                                            standalone):
    if standalone == "config set":
        box.module(DAEMONS=SWITCHED)
        fn = lambda: config.set("demo", "relay.enabled", True, by="cli")  # noqa: E731
    elif standalone == "apply":
        box.module(DAEMONS=ONE)
        fn = lambda: units.apply(units.plan("demo"), say=lambda line: None)  # noqa: E731
    else:
        box.installed(DAEMONS=ONE)
        fn = lambda: units.remove("demo", say=lambda line: None)  # noqa: E731
    box.sup.calls.clear()
    end = install_running()
    thread, done = in_thread(fn)
    thread.join(0.5)
    assert thread.is_alive() and box.sup.calls == []
    if standalone == "config set":
        # The setting is written first; only its units wait, and not under the file's
        # own lock.
        assert "relay" in moxi.paths._layout("demo").config_file().read_text()
    end()
    thread.join(WAIT)
    assert not thread.is_alive()
    assert done[0] and all(a.ok for a in done[0])
    verb = "disable" if standalone == "remove" else "enable"
    assert (verb, "--now", SERVICE) in box.sup.calls


def test_remove_where_this_process_holds_the_lock_neither_blocks_nor_raises(box):
    box.installed(DAEMONS=TWO)
    results: list = []
    with files.lock(moxi.paths.core_lock(), timeout=0):
        results.append(units.remove("demo", say=lambda line: None))
        box.installed(DAEMONS=TWO)
        thread, done = in_thread(lambda: units.remove("demo", say=lambda line: None))
        thread.join(WAIT)
        assert not thread.is_alive()
        results += done
    assert len(results) == 2 and all(a.ok for r in results for a in r)
    assert list(box.root.glob("moxi-demo-*")) == []


# -- interrupts, on a terminal ---------------------------------------------------------------

class Job:
    """The install's parent as the foreground job of a shell on a pseudo-terminal."""

    def __init__(self, box: Box, *args: str):
        self.box = box
        self.master, slave = os.openpty()
        os.set_blocking(self.master, False)
        self.screen = b""
        argv = [PY, HELPER, "shell", PY, HELPER, "parent", *args]
        try:
            self.shell = subprocess.Popen(argv, stdin=slave, stdout=slave, stderr=slave,
                                          env=box.env, start_new_session=True)
        finally:
            os.close(slave)

    def drain(self) -> None:
        while self.master is not None:
            try:
                part = os.read(self.master, 4096)
            except (BlockingIOError, OSError):
                return
            if not part:
                return
            self.screen += part

    def wait(self, who: str, key: str, value=None, timeout: float = WAIT) -> dict:
        def found():
            for event in self.box.events(who, key):
                if value is None or event[key] == value:
                    return event
            return None
        event = wait_for(found, timeout, between=self.drain)
        assert event, (f"no {who} event with {key}; log: {self.box.events()}; "
                       f"terminal: {self.screen!r}")
        return event

    def shows(self, text: str) -> None:
        assert wait_for(lambda: text.encode() in self.screen, between=self.drain), (
            f"the terminal never showed {text!r}: {self.screen!r}")

    def type(self, data: bytes) -> None:
        os.write(self.master, data)

    def parent(self) -> int:
        return self.wait("parent", "locked")["pid"]

    def child(self) -> int:
        return self.wait("child", "started")["pid"]

    def ended(self) -> int:
        """The parent's exit code, once the shell saw it exit."""
        code = self.wait("shell", "exit")["exit"]
        self.drain()
        return code

    def hang_up(self) -> None:
        os.close(self.master)
        self.master = None

    def close(self) -> None:
        """Kill every process of the job: those still in the shell's session, and any
        process that logged an event and still runs this helper."""
        pids = {e["pid"] for e in self.box.events()}
        pids |= {p.pid for p in psutil.process_iter() if _session(p) == self.shell.pid}
        for pid in pids - {os.getpid()}:
            try:
                proc = psutil.Process(pid)
                if _session(proc) == self.shell.pid or HELPER in proc.cmdline():
                    proc.kill()
            except psutil.Error:
                pass
        if self.shell.poll() is None:
            self.shell.kill()
        self.shell.wait(WAIT)
        if self.master is not None:
            os.close(self.master)


def _session(proc) -> int | None:
    try:
        return os.getsid(proc.pid)
    except OSError:
        return None


@pytest.fixture
def job(box):
    made: list[Job] = []

    def make(*args: str) -> Job:
        made.append(Job(box, *args))
        return made[-1]

    yield make
    for j in made:
        j.close()


def ended_interrupted(job: Job, box: Box, starts: list[str]) -> None:
    """The end every interrupt row shares: the recorded units started again, in order;
    the child exited 130, the parent 1, and no later module's settle step ran; the parent
    sent the child only the signals it caught itself, each once."""
    assert job.ended() == 1, job.screen
    assert box.value("child", "exit") == _settle.INTERRUPTED, job.screen
    # The parent's forwarded copy of a signal the child already had can come after the
    # child is done; ignored then, it never replaces the exit code with the signal.
    assert box.value("child", "ignored") == [int(s) for s in _settle.signals()]
    assert box.value("parent", "outcome") == _settle.INTERRUPTED
    assert [e["settle"] for e in box.events("parent", "settle")] == ["demo"]
    assert [c[1] for c in box.calls("start")] == starts
    assert box.events("child", "later") == []
    caught = box.value("parent", "signals")
    sent = [e["sent"] for e in box.events("parent", "sent")]
    assert sent == caught, (sent, caught)
    assert box.events(key="raised") == []


@pytest.mark.parametrize("how", ["ctrl-c", "sigterm to the parent", "sigterm to the child",
                                 "sigquit to the group"])
def test_an_interrupt_during_setup_starts_the_old_unmarked_unit_again(box, job, how):
    box.module(DAEMONS=ONE)
    box.unmarked()
    box.legacy()
    if sys.platform.startswith("linux"):
        box.supervisor(probe=["start"])
    box.env["MOXITEST_SETUP"] = "sleep"
    j = job("demo", "later", "--replace")
    j.wait("setup", "begin")
    parent, child = j.parent(), j.child()
    if how == "ctrl-c":
        j.type(b"\x03")
    elif how == "sigterm to the parent":
        os.kill(parent, signal.SIGTERM)
    elif how == "sigterm to the child":
        os.kill(child, signal.SIGTERM)
    else:
        os.killpg(os.getpgid(parent), signal.SIGQUIT)
    ended_interrupted(j, box, [SERVICE])
    # The child shares the parent's process group, the terminal's foreground group.
    assert box.events("child", "started")[0]["pgid"] == box.value("parent", "pgid")
    assert box.calls("stop", "start") == [("stop", SERVICE), ("start", SERVICE)]
    assert box.events("setup", "end") == []
    assert "ExecStart=/bin/true" in (box.root / SERVICE).read_text()   # never displaced
    caught = box.value("parent", "signals")
    assert caught == {"ctrl-c": [signal.SIGINT], "sigterm to the parent": [signal.SIGTERM],
                      "sigterm to the child": [],
                      "sigquit to the group": [signal.SIGQUIT]}[how]
    assert b"interrupted in demo's settle step; no later step ran" in j.screen
    # The supervisor call `resume` makes runs with no signal ignored (read from
    # /proc, so on Linux only).
    if sys.platform.startswith("linux"):
        ignored = box.value("sup", "ignored")
        assert ignored is not None and not {1, 2, 3, 15} & set(ignored), ignored


def test_a_hang_up_during_setup_ends_the_same_and_keeps_the_failure_record(box, job):
    box.installed(DAEMONS=ONE)
    box.legacy()
    box.supervisor(fail=[["start", SERVICE, "Unit failed to start."]])
    box.env["MOXITEST_SETUP"] = "sleep"
    j = job("demo", "later")
    j.wait("setup", "begin")
    parent = j.parent()
    os.killpg(os.getpgid(parent), signal.SIGHUP)
    j.hang_up()                                   # every later write fails with EIO
    ended_interrupted(j, box, [SERVICE])
    records = json.loads((moxi.paths._layout("demo").state_dir() / "doctor.json")
                         .read_text())
    assert f"resume:{SERVICE}" in records, records


def test_a_parent_started_with_hang_up_ignored_leaves_it_ignored(box):
    """Run as `nohup` runs it, the install's parent never catches a hang-up: the wait
    goes on, nothing is sent to the child, and the step ends as if none came."""
    box.module(DAEMONS=ONE)
    box.env["MOXITEST_SETUP"] = "nap"

    def ignore_hang_up():
        signal.signal(signal.SIGHUP, signal.SIG_IGN)

    proc = subprocess.Popen([PY, HELPER, "parent", "demo"], env=box.env,
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, process_group=0,
                            preexec_fn=ignore_hang_up)
    try:
        assert wait_for(lambda: box.events("setup", "begin")), box.events()
        os.kill(proc.pid, signal.SIGHUP)
        out, err = proc.communicate(timeout=WAIT)
    finally:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            pass
        proc.kill()
        proc.wait(WAIT)
    assert proc.returncode == 0, out + err
    assert box.value("parent", "outcome") == 0
    assert box.value("parent", "signals") == []
    assert box.events("parent", "sent") == []
    assert box.events("child", "got") == []
    assert box.value("setup", "end") == "nap"


def test_a_second_ctrl_c_during_resume_still_starts_every_recorded_unit(box, job):
    box.installed(DAEMONS=TWO)
    box.legacy()
    box.supervisor(sleep={"start": 2})
    box.env["MOXITEST_SETUP"] = "sleep"
    j = job("demo", "later")
    j.wait("setup", "begin")
    j.type(b"\x03")
    j.wait("sup", "begin", ["start", SERVICE])
    j.type(b"\x03")
    ended_interrupted(j, box, [SERVICE, AUX])
    assert box.value("parent", "signals") == [signal.SIGINT, signal.SIGINT]
    phases = [e["phase"] for e in box.events("child", "got")]
    assert _settle.RESTART in phases
    assert b"started again 2 of the 2 units the move stopped" in j.screen


def test_ctrl_c_during_the_moves_stop_lets_the_stop_and_rename_finish(box, job):
    box.installed(DAEMONS=TWO)
    old = box.legacy()
    box.supervisor(sleep={f"stop {SERVICE}": 2})
    j = job("demo", "later")
    j.wait("sup", "begin", ["stop", SERVICE])
    j.type(b"\x03")
    ended_interrupted(j, box, [SERVICE, AUX])
    assert box.calls("stop", "start") == [("stop", SERVICE), ("stop", AUX),
                                          ("start", SERVICE), ("start", AUX)]
    assert box.moved() and not old.exists()
    assert box.events("setup") == []


def test_ctrl_c_at_the_timer_switch_prompt_writes_neither_switch_nor_entry(box, job):
    box.installed(DAEMONS=ONE)
    box.module(DAEMONS=ONE, TIMERS=TIMER)
    box.legacy()
    j = job("demo", "later")
    j.shows("Turn it on? [y/N]")
    j.type(b"\x03")
    ended_interrupted(j, box, [SERVICE])
    file = moxi.paths._layout("demo").config_file()
    assert not file.exists() or "snapshot" not in file.read_text()
    assert _state.recorded("demo", "snapshot") is None
    assert box.events("setup", "end") != []


def test_a_setup_that_catches_the_interrupt_stops_when_it_returns(box, job):
    box.installed(DAEMONS=ONE)
    box.legacy()
    before = _settle.setup_path().read_text()
    box.env["MOXITEST_SETUP"] = "catch"
    j = job("demo", "later")
    j.wait("setup", "begin")
    j.type(b"\x03")
    ended_interrupted(j, box, [SERVICE])
    assert box.events("setup", "caught") != []
    assert box.events("setup", "end") == []
    assert _settle.setup_path().read_text() == before       # not recorded as run
    assert b"setup demo: done" not in j.screen


def test_a_parent_killed_outright_leaves_the_child_to_finish_holding_the_lock(box, job):
    box.installed(DAEMONS=ONE)
    box.legacy()
    box.env["MOXITEST_SETUP"] = "nap"
    j = job("demo", "later")
    j.wait("setup", "begin")
    parent, child = j.parent(), j.child()
    os.kill(parent, signal.SIGKILL)
    assert j.ended() == -signal.SIGKILL
    assert box.events("child", "exit") == []
    assert held_elsewhere() == parent                # the child's descriptor keeps it
    assert j.wait("child", "exit")["exit"] == 0
    assert wait_for(lambda: not psutil.pid_exists(child)
                    or psutil.Process(child).status() == psutil.STATUS_ZOMBIE)
    assert box.events("setup", "end") != []
    assert box.calls("stop", "start") == [("stop", SERVICE), ("start", SERVICE)]
    assert box.events(key="sent") == []
    assert wait_for(lambda: held_elsewhere() is None)


def test_ctrl_c_during_teardown_abandons_it_and_its_finally_runs(box, job):
    box.installed(DAEMONS=ONE, TEARDOWN="'moxi_demo.down:run'")
    j = job("demo", "--teardown")
    j.wait("teardown", "begin")
    j.type(b"\x03")
    assert j.ended() == 1, j.screen
    assert box.value("child", "exit") == _settle.INTERRUPTED
    assert box.value("child", "ignored") == [int(s) for s in _settle.signals()]
    assert box.events("teardown", "ran_finally") != []
    assert box.events("sup") == []
    assert [e["sent"] for e in box.events("parent", "sent")] == [signal.SIGINT]
    assert b"teardown demo: interrupted; nothing was removed" in j.screen
    assert b"interrupted in demo's teardown; nothing was removed" in j.screen
    assert box.events("parent", "ran") == []                # no package step
    assert (box.root / SERVICE).exists()                    # nor the units
    assert box.events(key="raised") == []


# -- the placer under the lock -----------------------------------------------------------------

#: An install listener that hands the module's placements to `loadout`, and a `TEARDOWN`
#: that hands them back: the placer's own lock, taken nested with no timeout, would wait
#: for the install forever if it didn't yield on the claim.
PLACING = """
from moxi import place
from settle_procs import note

def on_install(module, found):
    moved = place.adopt(module, "loadout", place.status(module), say=lambda line: None)
    note("listener", adopted=sorted(str(p.path) for p in moved))

def on_uninstall(module):
    note("listener", uninstalled=module)
"""

HANDBACK = """
from moxi import place
from settle_procs import note

def run():
    back = place.adopt("loadout", "demo", place.status("loadout"), say=lambda line: None)
    note("teardown", back=sorted(str(p.path) for p in back))
"""


@pytest.fixture
def placing(box, monkeypatch):
    """`box` with module demo shipping skill `demo-send`, and the home the real processes
    run under also this process's, so the harness folders are one place under tmp."""
    from moxi import place

    user = box.tmp / "user"
    user.mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(user))
    monkeypatch.setenv("USERPROFILE", str(user))  # what `~` reads on Windows
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    box.env.pop("CLAUDE_CONFIG_DIR", None)
    box.site.write("moxi_demo/skills/demo-send/SKILL.md", "# send\n")
    box.site.write("moxi_demo/handback.py", HANDBACK)
    box.place = place
    box.targets = sorted(str(user / folder / "skills" / "demo-send")
                         for folder in (".claude", ".agents"))
    return box


def test_the_settle_child_places_skills_and_a_listener_adopts_them_under_the_claim(placing):
    box = placing
    box.module(DAEMONS=ONE, SKILLS="'skills'", listener=True)
    box.site.write("moxi_demo/installs.py", PLACING)
    box.env["MOXITEST_LATER"] = "1"
    done = parent_run(box, "demo", "--skills", timeout=30)
    assert done.returncode == 0, done.stdout + done.stderr
    assert box.events(key="raised") == []
    assert box.value("listener", "adopted") == box.targets
    src = box.site.root / "moxi_demo" / "skills" / "demo-send"
    assert all(os.readlink(t) == str(src) for t in box.targets)
    assert box.place.status("demo") == []
    assert sorted(str(p.path) for p in box.place.status("loadout")) == box.targets


def test_uninstall_hands_back_in_the_teardown_child_and_removes_in_the_parent(placing):
    """The hand-back runs in the teardown child, where `adopt` finds the claim; the
    parent's `place.remove` yields on its own hold and removes what came back."""
    box = placing
    box.installed(DAEMONS=ONE, SKILLS="'skills'", TEARDOWN="'moxi_demo.handback:run'")
    place = box.place
    src = box.site.root / "moxi_demo" / "skills" / "demo-send"
    items = [place.Item(src, h, "demo-send") for h in ("claude", "codex")]
    place.apply(place.plan("demo", items), say=lambda line: None)
    place.adopt("demo", "loadout", place.status("demo"), say=lambda line: None)
    done = parent_run(box, "demo", "--teardown", timeout=30)
    assert done.returncode == 0, done.stdout + done.stderr
    assert box.events(key="raised") == []
    assert box.value("teardown", "back") == box.targets
    assert box.events("child", "started")[0]["claim"] is True
    assert not any(os.path.lexists(t) for t in box.targets)
    ledger = place._read_ledger()
    assert not ledger.get("demo", {}).get("placed") and not ledger.get("demo", {}).get("handed")
    assert not ledger["loadout"].get("placed")
    assert sorted(ledger["loadout"]["handed"]) == box.targets
    assert "removed skill demo-send for claude" in done.stdout

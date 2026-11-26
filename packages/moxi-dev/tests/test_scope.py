"""The memory scope: figures, skip and version rules, the argv, the line watch, the ends.

Every systemd call and cgroup read is injected: no test here makes a scope or reaches a
user manager. The live tests in test_scope_live.py make real ones.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from moxi_dev import scope
from moxi_dev.scope import (Figures, Host, Outer, Sample, ScopeError, System, Watch,
                            MIB)

HOST = Host(client=258, manager=258, line_figures=True)


# --- a platform with fewer signals -----------------------------------------------------

#: Imports the scope and runs `moxi-dev check --help` and `moxi-dev cap --help` in a
#: Python whose `signal` has no SIGHUP, as on Windows.
NO_SIGHUP = """\
import signal, sys
del signal.SIGHUP
import moxi_dev.scope
assert signal.SIGINT in moxi_dev.scope.Waiter.SIGNALS
from moxi_dev.cli import main
for verb in ("check", "cap"):
    try:
        code = main([verb, "--help"])
    except SystemExit as exc:
        code = exc.code
    assert code in (0, None), (verb, code)
"""


def test_the_scope_and_the_verbs_import_where_there_is_no_sighup(tmp_path):
    done = subprocess.run([sys.executable, "-c", NO_SIGHUP], cwd=tmp_path,
                          capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    assert "usage" in done.stdout


# --- the figures -----------------------------------------------------------------------

@pytest.mark.parametrize("workers, high, top", [(1, 3008, 4544), (8, 6592, 8128),
                                                (16, 10688, 12224)])
def test_the_default_figures_follow_the_worker_count(workers, high, top):
    found = scope.figures({}, workers)
    assert (found.high_mib, found.max_mib) == (high, top)
    assert found.source() == f"the default for {workers} worker{'s' * (workers != 1)}"


@pytest.mark.parametrize("workers, line_mib", [(8, 6262.4), (16, 10153.6)])
def test_the_line_is_95_percent_of_high_unrounded(workers, line_mib):
    found = scope.figures({}, workers)
    assert found.line_bytes == pytest.approx(line_mib * MIB)


def test_each_variable_replaces_its_own_default_alone():
    high = scope.figures({scope.MEMORY_HIGH_ENV: "5000"}, 8)
    assert (high.high_mib, high.max_mib, high.high_set, high.max_set) == (5000, 8128, True,
                                                                         False)
    top = scope.figures({scope.MEMORY_MAX_ENV: "9000"}, 8)
    assert (top.high_mib, top.max_mib) == (6592, 9000)
    assert top.source() == f"set by {scope.MEMORY_MAX_ENV}"


def test_max_none_runs_with_no_scope():
    assert scope.figures({scope.MEMORY_MAX_ENV: "none", scope.MEMORY_HIGH_ENV: "x"}, 8) \
        is None
    assert scope.skip_reason(None, HOST) == f"{scope.MEMORY_MAX_ENV} is none"


@pytest.mark.parametrize("env", [{scope.MEMORY_HIGH_ENV: "63"},
                                 {scope.MEMORY_MAX_ENV: "1.5"},
                                 {scope.MEMORY_MAX_ENV: "-100"},
                                 {scope.MEMORY_HIGH_ENV: "6G"},
                                 {scope.MEMORY_HIGH_ENV: "8128"},
                                 {scope.MEMORY_HIGH_ENV: "900", scope.MEMORY_MAX_ENV: "800"}])
def test_a_bad_value_or_a_high_not_below_the_max_is_refused(env):
    with pytest.raises(ScopeError):
        scope.figures(env, 8)


@pytest.mark.parametrize("env, high_from, max_from", [
    ({scope.MEMORY_MAX_ENV: "6000"}, "the default for 8 workers",
     f"set by {scope.MEMORY_MAX_ENV}"),
    ({scope.MEMORY_HIGH_ENV: "8128"}, f"set by {scope.MEMORY_HIGH_ENV}",
     "the default for 8 workers"),
    ({scope.MEMORY_HIGH_ENV: "900", scope.MEMORY_MAX_ENV: "800"},
     f"set by {scope.MEMORY_HIGH_ENV}", f"set by {scope.MEMORY_MAX_ENV}")])
def test_the_refusal_names_where_each_figure_came_from(env, high_from, max_from):
    with pytest.raises(ScopeError) as caught:
        scope.figures(env, 8)
    high, top = int(env.get(scope.MEMORY_HIGH_ENV, 6592)), int(env.get(scope.MEMORY_MAX_ENV,
                                                                         8128))
    assert str(caught.value).startswith(f"MemoryHigh {high} MiB ({high_from}) is not below "
                                        f"MemoryMax {top} MiB ({max_from});")


def test_a_max_below_the_default_warns_with_the_default_figure():
    found = scope.figures({scope.MEMORY_MAX_ENV: "7000"}, 8)
    assert scope.below_default(found) == (
        f"{scope.MEMORY_MAX_ENV} 7000 is below the 8128 MiB the suite needs at 8 workers; "
        f"expect a kill, or lower {scope.WORKERS_ENV}")
    assert scope.below_default(scope.figures({scope.MEMORY_MAX_ENV: "9000"}, 8)) is None
    assert scope.below_default(scope.figures({}, 8)) is None


# --- the host: skip rules and versions -------------------------------------------------

class Calls:
    """A stand-in for running systemctl and systemd-run: answers by the argv's words."""

    def __init__(self, answers: dict[tuple[str, ...], tuple[int, str]] | None = None):
        self.answers = answers or {}
        self.calls: list[list[str]] = []

    def __call__(self, argv):
        self.calls.append(list(argv))
        for words, answer in self.answers.items():
            if tuple(argv[:len(words)]) == words or all(w in argv for w in words):
                return answer
        return 0, ""


def manager_cgroup(root: Path, *, controllers="cpu io memory pids",
                   stat="anon 1\nshmem 2\nkernel 3\nslab_reclaimable 1\nfile 9\n") -> Path:
    group = root / "user.slice" / "user@1000.service"
    group.mkdir(parents=True)
    if controllers is not None:
        (group / "cgroup.subtree_control").write_text(controllers + "\n")
    (group / "memory.stat").write_text(stat)
    return group


def host_system(tmp_path, *, manager="258.11-1.fc43\n", client="systemd 258 (258.11-1.fc43)\n",
                env=None, which=True, **cgroup) -> System:
    manager_cgroup(tmp_path, **cgroup)
    answers = {("--property=Version",): (0, manager) if manager is not None else (1, ""),
               ("systemd-run", "--version"): (0, client),
               ("--property=ControlGroup",): (0, "/user.slice/user@1000.service\n")}
    return System(env=env or {}, platform="linux", output=Calls(answers),
                  which=(lambda name: f"/usr/bin/{name}") if which else (lambda name: None),
                  cgroup_root=tmp_path)


def test_a_working_host_reads_both_versions(tmp_path):
    assert scope.find_host(host_system(tmp_path)) == Host(client=258, manager=258,
                                                          line_figures=True)


@pytest.mark.parametrize("platform", ["darwin", "win32"])
def test_off_linux_there_is_no_scope(tmp_path, platform):
    system = host_system(tmp_path)
    system.platform = platform
    assert scope.find_host(system) == "not Linux"


@pytest.mark.parametrize("value, skipped", [("true", True), ("1", True), ("yes", True),
                                            ("false", False), ("0", False), ("", False)])
def test_ci_skips_unless_false_or_0(tmp_path, value, skipped):
    found = scope.find_host(host_system(tmp_path, env={"CI": value}))
    assert (found == "CI") is skipped


def test_no_systemd_run_on_path_skips(tmp_path):
    assert scope.find_host(host_system(tmp_path, which=False)) == "no systemd-run"


def test_no_user_manager_skips(tmp_path):
    assert scope.find_host(host_system(tmp_path, manager=None)) == "no systemd user manager"


@pytest.mark.parametrize("manager, client", [("unknown\n", "systemd 258 (x)\n"),
                                             ("258.11\n", "systemd version x\n"),
                                             ("258.11\n", "")])
def test_an_unreadable_version_of_either_skips(tmp_path, manager, client):
    assert scope.find_host(host_system(tmp_path, manager=manager, client=client)) == \
        "systemd version unreadable"


@pytest.mark.parametrize("controllers", ["cpu io pids", None])
def test_no_delegated_memory_controller_skips(tmp_path, controllers):
    assert scope.find_host(host_system(tmp_path, controllers=controllers)) == \
        "the user manager has no memory controller"


@pytest.mark.parametrize("stat", ["anon 1\nshmem 2\nslab_reclaimable 1\n",
                                  "anon 1\nshmem 2\nkernel 3\n"])
def test_no_kernel_or_slab_reclaimable_line_gives_no_memory_high(tmp_path, stat):
    host = scope.find_host(host_system(tmp_path, stat=stat))
    assert isinstance(host, Host) and not host.line_figures


@pytest.mark.parametrize("manager, client", [("252.30-1~deb12u2", "systemd 258 (x)"),
                                             ("253", "systemd 253"),
                                             ("254", "systemd 254"),
                                             ("258", "systemd 252")])
def test_versions_read_from_their_leading_numbers(tmp_path, manager, client):
    host = scope.find_host(host_system(tmp_path, manager=manager + "\n",
                                       client=client + "\n"))
    assert (host.manager, host.client) == (int(manager[:3]), int(client.split()[1]))


# --- the argv --------------------------------------------------------------------------

def argv_for(host: Host, unit="moxi-dev-check-0123456789ab.scope"):
    return scope.scope_argv(unit, host, scope.figures({}, 8), "moxi-dev check in /r",
                            ["/py", "-m", "moxi_dev", "check", "--full"], 7, python="/py")


def test_the_argv_on_258_has_every_property_and_the_flag():
    argv = argv_for(HOST)
    assert argv[:argv.index("--")] == [
        "systemd-run", "--user", "--scope", "--quiet", "--expand-environment=no",
        "--unit=moxi-dev-check-0123456789ab.scope", "--description=moxi-dev check in /r",
        "-p", "MemoryHigh=6592M", "-p", "MemoryMax=8128M", "-p", "MemorySwapMax=0",
        "-p", "OOMPolicy=kill"]
    after = argv[argv.index("--") + 1:]
    assert after[:3] == ["/py", "-I", "-c"] and after[3] == scope.STARTED
    assert after[4:] == ["7", "/py", "-m", "moxi_dev", "check", "--full"]


@pytest.mark.parametrize("client, manager, flag, policy", [
    (258, 252, True, False),      # a 252 manager with a 258 client gets the flag
    (253, 253, False, True),
    (254, 252, True, False),
    (252, 252, False, False),
    (253, 258, False, True)])
def test_each_option_goes_by_its_own_programs_version(client, manager, flag, policy):
    argv = argv_for(Host(client=client, manager=manager, line_figures=True))
    assert ("--expand-environment=no" in argv) is flag
    assert ("OOMPolicy=kill" in argv) is policy
    assert "MemoryMax=8128M" in argv and "MemorySwapMax=0" in argv


def test_no_line_figures_gives_no_memory_high():
    argv = argv_for(Host(client=258, manager=258, line_figures=False))
    assert not any(word.startswith("MemoryHigh=") for word in argv)


def test_unit_names_are_unique():
    names = {scope.new_unit("check") for _ in range(50)}
    assert len(names) == 50
    assert all(name.startswith("moxi-dev-check-") and name.endswith(".scope")
               and len(name) == len("moxi-dev-check-.scope") + 12 for name in names)


# --- U and the line watch --------------------------------------------------------------

def test_u_counts_anon_shmem_and_kernel_less_reclaimable_slab():
    assert scope.unreclaimable({"anon": 10, "shmem": 5, "kernel": 7,
                                "slab_reclaimable": 3, "file": 100}) == 19


def test_u_counts_none_of_a_kernel_that_is_all_reclaimable_slab():
    assert scope.unreclaimable({"anon": 4, "shmem": 0, "kernel": 50,
                                "slab_reclaimable": 50}) == 4


def test_u_is_floored_at_0():
    assert scope.unreclaimable({"anon": 0, "shmem": 0, "kernel": 3,
                                "slab_reclaimable": 9}) == 0


def test_u_is_none_without_its_lines():
    assert scope.unreclaimable({"anon": 4, "shmem": 0, "kernel": 5}) is None


LINE = 100.0


def samples(held, high=None, **kw):
    high = high if high is not None else range(len(held))
    return [Sample(held=h, high_events=e, **kw) for h, e in zip(held, high)]


def decisions(watch, feed):
    return [watch.add(one) for one in feed]


def test_five_samples_at_the_line_with_high_rising_end_it():
    got = decisions(Watch(LINE), samples([100, 101, 150, 100, 120]))
    assert got == [None, None, None, None, "line"]


def test_four_at_the_line_then_one_under_do_not():
    assert decisions(Watch(LINE), samples([100, 101, 150, 100, 99])) == [None] * 5


def test_at_the_line_with_high_flat_does_not():
    assert decisions(Watch(LINE), samples([120] * 8, high=[7] * 8)) == [None] * 8


def test_under_the_line_never_ends_it_whatever_else_does():
    feed = [Sample(held=99, high_events=n * 1000, current=10**12, peak=10**12,
                   stalled_us=n * 10**6) for n in range(30)]
    assert decisions(Watch(LINE), feed) == [None] * 30


def test_reclaimable_slab_does_not_count_toward_the_line(tmp_path):
    (tmp_path / "memory.stat").write_text(
        "anon 5\nshmem 0\nkernel 200\nslab_reclaimable 199\n")
    (tmp_path / "memory.events").write_text("high 3\noom_kill 0\n")
    assert scope.read_sample(tmp_path).held == 6


def test_oom_kill_ends_it_below_253_only():
    feed = [Sample(held=1, oom_kills=0), Sample(held=1, oom_kills=1)]
    assert decisions(Watch(LINE, group_kill=True), feed) == [None, "oom"]
    assert decisions(Watch(LINE, group_kill=False), feed) == [None, None]


def test_no_line_without_kernel_figures():
    assert decisions(Watch(None), samples([10**9] * 6)) == [None] * 6


def test_a_sample_reads_every_figure(tmp_path):
    (tmp_path / "memory.stat").write_text("anon 50\nshmem 10\nkernel 5\nslab_reclaimable 1\n")
    (tmp_path / "memory.events").write_text("low 0\nhigh 12\nmax 0\noom 0\noom_kill 2\n")
    (tmp_path / "memory.current").write_text("90\n")
    (tmp_path / "memory.peak").write_text("95\n")
    (tmp_path / "memory.pressure").write_text(
        "some avg10=0.00 avg60=0.00 avg300=0.00 total=2500000\n"
        "full avg10=0.00 avg60=0.00 avg300=0.00 total=100\n")
    assert scope.read_sample(tmp_path) == Sample(held=64, high_events=12, oom_kills=2,
                                                 current=90, peak=95, stalled_us=2500000)


def test_a_sample_without_peak_or_pressure_reads_none(tmp_path):
    (tmp_path / "memory.stat").write_text("anon 50\nshmem 10\nkernel 5\nslab_reclaimable 1\n")
    (tmp_path / "memory.events").write_text("high 0\n")
    one = scope.read_sample(tmp_path)
    assert one.peak is None and one.stalled_us is None and one.current is None


def test_a_gone_cgroup_raises_oserror(tmp_path):
    with pytest.raises(OSError):
        scope.read_sample(tmp_path / "gone")


# --- the inner: marker, sampler, report, leftovers -------------------------------------

def proc_cgroup(tmp_path, unit):
    path = tmp_path / "cgroup"
    path.write_text(f"0::/user.slice/user@1000.service/app.slice/{unit}\n")
    return path


def test_a_matching_marker_runs_as_an_inner(tmp_path):
    unit = "moxi-dev-check-0123456789ab.scope"
    assert scope.inner_unit({scope.SCOPE_ENV: unit}, proc_cgroup(tmp_path, unit)) == unit


def test_a_mismatched_or_missing_marker_does_not(tmp_path):
    here = proc_cgroup(tmp_path, "tmux-spawn-1.scope")
    assert scope.inner_unit({scope.SCOPE_ENV: "moxi-dev-check-0123456789ab.scope"},
                            here) is None
    assert scope.inner_unit({}, here) is None
    assert scope.inner_unit({scope.SCOPE_ENV: "x.scope"}, tmp_path / "none") is None


def test_own_cgroup_reads_the_v2_line(tmp_path):
    path = tmp_path / "cgroup"
    path.write_text("1:name=systemd:/x\n0::/user.slice/a.scope\n")
    assert scope.own_cgroup(path, Path("/sys/fs/cgroup")) == \
        Path("/sys/fs/cgroup/user.slice/a.scope")


def test_the_sampler_keeps_the_largest_u(tmp_path):
    stat = tmp_path / "memory.stat"
    stat.write_text("anon 500\nshmem 0\nkernel 0\nslab_reclaimable 0\n")
    sampler = scope.Sampler(tmp_path, interval_s=0.01).start()
    deadline = time.monotonic() + 10
    while sampler.held is None and time.monotonic() < deadline:
        time.sleep(0.01)
    stat.write_text("anon 100\nshmem 0\nkernel 0\nslab_reclaimable 0\n")
    assert sampler.stop() == 500
    assert scope.Sampler(None).start().stop() is None


def inner_cgroup(tmp_path, *, high="6912212992", peak="6120000000",
                 pressure="some avg10=0.00 avg60=0.00 avg300=0.00 total=400000\n"):
    group = tmp_path / "scope"
    group.mkdir()
    (group / "memory.high").write_text(high + "\n")
    (group / "memory.max").write_text(str(8128 * MIB) + "\n")
    if peak is not None:
        (group / "memory.peak").write_text(peak + "\n")
    if pressure is not None:
        (group / "memory.pressure").write_text(pressure)
    return group


def test_the_report_names_peak_held_line_high_max_and_stalled(tmp_path):
    report = scope.memory_report(inner_cgroup(tmp_path), held=5000 * MIB)
    assert report.field == ("peak 5.7G, held 4.9G, line 6.1G, high 6.4G, max 7.9G, "
                            "stalled 0.4s")
    assert report.lines == []


def test_the_near_line_line_comes_at_90_percent_of_the_line(tmp_path):
    group = inner_cgroup(tmp_path)
    line = 6592 * MIB * 0.95
    assert scope.memory_report(group, held=int(0.9 * line) - 1).lines == []
    lines = scope.memory_report(group, held=int(0.9 * line) + 1).lines
    assert len(lines) == 1 and lines[0].startswith("held 5.5G it can't give back, within a "
                                                   "tenth of its line 6.1G")


def test_a_stall_of_10s_warns(tmp_path):
    group = inner_cgroup(tmp_path, pressure="some avg10=1 avg60=1 avg300=1 total=41200000\n")
    lines = scope.memory_report(group, held=10).lines
    assert lines == [f"the check waited on memory for 41.2s (MemoryHigh 6.4G, peak 5.7G); "
                     f"fewer workers or a higher {scope.MEMORY_HIGH_ENV} would speed it up"]


def test_missing_parts_are_named(tmp_path):
    group = inner_cgroup(tmp_path, high="max", peak=None, pressure=None)
    assert scope.memory_report(group, held=10).field == (
        "peak unknown, high not set (no kernel memory figures), max 7.9G, stalled unknown")


def test_leftovers_are_killed_but_the_kept_one(tmp_path):
    procs = tmp_path / "cgroup.procs"
    procs.write_text("10\n11\n12\n")
    killed = []

    def kill(pid):
        killed.append(pid)
        procs.write_text("\n".join(str(p) for p in (10, 11, 12)
                                   if p == 10 or p not in killed) + "\n")

    assert scope.end_leftovers(tmp_path, keep=10, kill=kill, pause=lambda s: None) == 2
    assert sorted(killed) == [11, 12]



def states(rig, *said):
    """Answer the outer's ActiveState reads with `said` in turn, the last one for good;
    every other call as the rig would."""
    real, left = rig.calls.__call__, list(said)
    reads = []

    def calls(argv):
        if "--property=ActiveState" in argv:
            reads.append(rig.clock[0])
            return 0, (left.pop(0) if len(left) > 1 else left[0]) + "\n"
        return real(argv)

    rig.outer.system.output = calls
    return reads


@pytest.mark.parametrize("ending", ["code", "signal", "line"])
def test_the_outer_waits_for_its_unit_to_be_gone_however_the_work_ended(tmp_path, ending):
    rig = Rig(tmp_path, child=Child(code=0, alive=100 if ending != "code" else 1))
    if ending == "signal":
        rig.child.code = -signal.SIGTERM
        rig.waiter.steps = [signal.SIGTERM]
    elif ending == "line":
        rig.child.code = -signal.SIGKILL
        rig.waiter.steps = runaway(rig)
    reads = states(rig, "active", "deactivating", "inactive")
    outcome = rig.run()
    assert outcome.is_gone and len(reads) == 3
    assert (outcome.signum is not None) is (ending == "signal")


def test_a_unit_not_gone_within_the_settle_time_is_not_gone(tmp_path):
    rig = Rig(tmp_path, child=Child(code=0, alive=1))
    reads = states(rig, "deactivating")
    outcome = rig.run()
    assert outcome.code == 0 and not outcome.is_gone
    assert reads[-1] - reads[0] == pytest.approx(scope.SETTLE_S, abs=0.11)


def test_a_failed_read_of_the_unit_is_not_gone(tmp_path):
    rig = Rig(tmp_path, child=Child(code=0, alive=1),
              answers={("--property=ActiveState",): (1, "")})
    assert not rig.run().is_gone

def test_leftovers_stop_at_their_deadline(tmp_path):
    (tmp_path / "cgroup.procs").write_text("11\n")
    now = [0.0]

    def pause(seconds):
        now[0] += 1.0

    count = scope.end_leftovers(tmp_path, kill=lambda pid: None, clock=lambda: now[0],
                                pause=pause)
    assert count == 1 and now[0] == pytest.approx(scope.LEFTOVER_S)


def test_the_leftover_line():
    assert scope.ended_line(2, "check") == "ended 2 processes the check left running"
    assert scope.ended_line(1, "command") == "ended 1 process the command left running"


# --- the outer -------------------------------------------------------------------------

class Child:
    """systemd-run as the outer sees it: alive for `alive` polls, then `code`."""

    def __init__(self, code=0, alive=0):
        self.pid = 4242
        self.returncode = None
        self.code, self.alive = code, alive
        self.waited = 0

    def poll(self):
        if self.returncode is None:
            if self.alive <= 0:
                self.returncode = self.code
            self.alive -= 1
        return self.returncode

    def wait(self, timeout=None):
        self.waited += 1
        self.returncode = self.code if self.returncode is None else self.returncode
        return self.returncode


class FakeWaiter:
    """Each wait is one second: runs the next step (which may write the cgroup's files)
    and returns the signal a step names. The first wait is the one for the started
    byte."""

    def __init__(self, steps, clock):
        self.steps, self.clock = list(steps), clock
        self.closed = False

    def wait(self, timeout_s, fds=()):
        self.clock[0] += timeout_s
        signum = None
        if self.steps:
            step = self.steps.pop(0)
            signum = step() if callable(step) else step
        import select
        ready = select.select(list(fds), [], [], 0)[0] if fds else []
        return signum, ready

    def close(self):
        self.closed = True


class Rig:
    """An outer with every machine call injected, and a fake scope cgroup under tmp."""

    def __init__(self, tmp_path, *, host=HOST, kind="check", started=True, child=None,
                 steps=(), answers=None, found=None):
        self.cgroup = tmp_path / "app.slice" / "u.scope"
        self.cgroup.mkdir(parents=True)
        self.calls = Calls({("--property=ControlGroup",): (0, "/app.slice/u.scope\n"),
                            **(answers or {})})
        self.said: list[str] = []
        self.clock = [0.0]
        self.child = child or Child()
        self.started = started
        self.spawned: list = []
        self.waiter = FakeWaiter(steps, self.clock)
        system = System(env={}, platform="linux", output=self.calls,
                        which=lambda name: name, cgroup_root=tmp_path)
        self.outer = Outer(kind=kind, command=["work"], found=found or scope.figures({}, 8),
                           host=host, system=system, description="d", say=self.said.append,
                           spawn=self.spawn, waiter=lambda: self.waiter,
                           clock=lambda: self.clock[0], pause=self.pause, unit="u.scope")

    def pause(self, seconds):
        self.clock[0] += seconds

    def spawn(self, argv, env, fd):
        self.spawned.append((argv, env))
        if self.started:
            os.write(fd, b"x")
        return self.child

    def stat(self, held_mib, high, *, oom_kill=0, current_mib=None, peak_mib=None,
             stalled_us=None):
        def write():
            (self.cgroup / "memory.stat").write_text(
                f"anon {int(held_mib * MIB)}\nshmem 0\nkernel 0\nslab_reclaimable 0\n")
            (self.cgroup / "memory.events").write_text(f"high {high}\noom_kill {oom_kill}\n")
            if current_mib is not None:
                (self.cgroup / "memory.current").write_text(f"{int(current_mib * MIB)}\n")
            if peak_mib is not None:
                (self.cgroup / "memory.peak").write_text(f"{int(peak_mib * MIB)}\n")
            if stalled_us is not None:
                (self.cgroup / "memory.pressure").write_text(f"some total={stalled_us}\n")
        return write

    def run(self):
        return self.outer.run({"KEEP": "1"})

    def systemctl(self, *words):
        return [call for call in self.calls.calls if all(w in call for w in words)]


def test_the_work_gets_the_marker_and_its_exit_code_passes_through(tmp_path):
    rig = Rig(tmp_path, child=Child(code=3, alive=2))
    outcome = rig.run()
    assert outcome.code == 3 and outcome.memory is None and outcome.signum is None
    argv, env = rig.spawned[0]
    assert env == {"KEEP": "1", scope.SCOPE_ENV: "u.scope"}
    assert argv[0] == "systemd-run" and "--unit=u.scope" in argv
    assert rig.said == [] and rig.waiter.closed


def test_no_started_byte_is_exit_2_pointing_at_systemd_run(tmp_path):
    rig = Rig(tmp_path, started=False, child=Child(code=1))
    outcome = rig.run()
    assert outcome.code == 2
    assert rig.said == [f"moxi-dev check: the memory scope could not be made (systemd-run "
                        f"exited 1; its message is above); {scope.MEMORY_MAX_ENV}=none runs "
                        f"the check uncapped"]


def runaway(rig, n=5):
    line_mib = 6592 * 0.95
    return [rig.stat(line_mib + 10, high=k, current_mib=6600, peak_mib=6650)
            for k in range(1, n + 1)]


def test_a_runaway_is_ended_past_its_line_the_line_said_before_the_kill(tmp_path):
    rig = Rig(tmp_path, child=Child(alive=100))
    rig.waiter.steps = runaway(rig)
    said_at_kill = []
    real = rig.calls.__call__

    def calls(argv):
        if "kill" in argv:
            said_at_kill.append(list(rig.said))
            rig.child.code = -signal.SIGKILL
        return real(argv)

    rig.outer.system.output = calls
    outcome = rig.run()
    assert outcome.code == 1
    assert outcome.memory == ("ended past its line (held 6.1G, line 6.1G, high 6.4G, max "
                              "7.9G, peak 6.5G)")
    assert said_at_kill and len(said_at_kill[0]) == 1
    line = said_at_kill[0][0]
    assert line.startswith("moxi-dev check: ended for memory: u.scope held 6.1G it can't "
                           "give back (anonymous, shared and kernel memory) for 5s, past its "
                           "line 6.1G under MemoryHigh 6.4G (now 6.4G, peak 6.5G, MemoryMax "
                           "7.9G; the default for 8 workers).")
    assert ["systemctl", "--user", "kill", "--signal=SIGKILL", "u.scope"] in rig.calls.calls


def test_a_check_under_its_line_is_never_ended(tmp_path):
    rig = Rig(tmp_path, child=Child(code=0, alive=12))
    rig.waiter.steps = [rig.stat(6000, high=k * 1000, current_mib=6592, peak_mib=6592,
                                 stalled_us=k * 10**6) for k in range(12)]
    outcome = rig.run()
    assert outcome.code == 0 and rig.said == [] and not rig.systemctl("kill")


def test_a_read_that_fails_mid_run_stops_the_watch_with_no_error(tmp_path):
    rig = Rig(tmp_path, child=Child(code=0, alive=4))
    rig.waiter.steps = [rig.stat(10, high=0)]     # the second read finds the files gone

    def gone():
        for name in ("memory.stat", "memory.events"):
            (rig.cgroup / name).unlink()

    rig.waiter.steps.append(gone)
    outcome = rig.run()
    assert outcome.code == 0 and rig.said == [] and rig.outer.cgroup_dir is None


def killed_rig(tmp_path, result, memory_peak, *, host=HOST, oom_kill=0, kind="check"):
    show = (f"ActiveState=failed\nResult={result}\nMemoryPeak={memory_peak}\n"
            f"MemoryMax={8128 * MIB}\n")
    rig = Rig(tmp_path, host=host, kind=kind, child=Child(code=-signal.SIGKILL, alive=2),
              answers={("--property=ActiveState,Result,MemoryPeak,MemoryMax",): (0, show)})
    rig.waiter.steps = [rig.stat(5000, high=1, oom_kill=oom_kill, peak_mib=7000,
                                 current_mib=6000)]
    return rig


def test_a_kernel_kill_at_the_cap_is_named_and_reset(tmp_path):
    rig = killed_rig(tmp_path, "oom-kill", 8100 * MIB)
    outcome = rig.run()
    assert outcome.code == 1 and outcome.memory == "killed at its cap (max 7.9G, peak 7.9G)"
    assert rig.said[0].startswith("moxi-dev check: killed for memory: u.scope reached its "
                                  "cap, MemoryMax 7.9G (peak 7.9G; the default for 8 workers)")
    assert ["systemctl", "--user", "reset-failed", "u.scope"] in rig.calls.calls


def test_a_kill_below_95_percent_of_max_is_the_machine_running_short(tmp_path):
    rig = killed_rig(tmp_path, "oom-kill", 5100 * MIB)
    outcome = rig.run()
    assert outcome.memory == "killed while the machine ran short (max 7.9G, peak 5.0G)"
    assert rig.said[0].startswith("moxi-dev check: killed for memory while the machine ran "
                                  "short, below its cap (peak 5.0G, MemoryMax 7.9G)")


def test_the_peak_is_memory_peak_then_memory_peak_read_then_current(tmp_path):
    old = Host(client=258, manager=254, line_figures=True)    # no MemoryPeak before 255
    rig = killed_rig(tmp_path, "oom-kill", 8100 * MIB, host=old)
    assert "peak 6.8G" in rig.run().memory                     # memory.peak read, 7000 MiB
    rig = killed_rig(tmp_path / "b", "oom-kill", "[not set]")
    (rig.cgroup / "memory.peak").unlink(missing_ok=True)
    rig.waiter.steps = [rig.stat(5000, high=1, current_mib=6000)]
    assert "peak 5.9G" in rig.run().memory                     # memory.current, 6000 MiB


def test_an_oom_kill_count_names_a_memory_kill_whatever_the_result(tmp_path):
    rig = killed_rig(tmp_path, "success", 8100 * MIB, oom_kill=1)
    assert rig.run().memory.startswith("killed at its cap")


def test_a_sigkill_not_for_memory_is_said_so(tmp_path):
    rig = killed_rig(tmp_path, "success", 100)
    outcome = rig.run()
    assert outcome.code == 1 and rig.said == [
        "moxi-dev check: the check was ended by SIGKILL, not for memory"]
    assert outcome.memory.startswith("peak ")


def test_below_253_the_outer_finishes_an_oom_kill_itself(tmp_path):
    old = Host(client=252, manager=252, line_figures=True)
    rig = killed_rig(tmp_path, "oom-kill", 8100 * MIB, host=old, oom_kill=1)
    rig.child = Child(code=-signal.SIGKILL, alive=100)
    outcome = rig.run()
    assert ["systemctl", "--user", "kill", "--signal=SIGKILL", "u.scope"] in rig.calls.calls
    assert outcome.code == 1 and outcome.memory.startswith("killed ")


@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGHUP])
def test_sigterm_and_sighup_say_the_state_and_queue_the_stop_at_once(tmp_path, signum):
    rig = Rig(tmp_path, child=Child(code=-signum, alive=100))
    rig.waiter.steps = [None,          # the wait for the started byte
                        rig.stat(5000, high=1, current_mib=5900, peak_mib=6000,
                                 stalled_us=0),
                        rig.stat(5100, high=2, current_mib=5900, peak_mib=6000,
                                 stalled_us=12 * 10**6),
                        signum]
    outcome = rig.run()
    assert outcome.signum == signum
    name = signal.Signals(signum).name
    assert rig.said == [f"moxi-dev check: stopped by {name}; memory then 5.8G, held 5.0G "
                        f"(line 6.1G, peak 5.9G, MemoryHigh 6.4G, MemoryMax 7.9G, stalled "
                        f"12s of the last 1)"]
    stops = rig.systemctl("stop")
    assert stops == [["systemctl", "--user", "stop", "--no-block", "u.scope"]]


def test_sigint_waits_20s_for_the_inner_before_the_stop(tmp_path):
    rig = Rig(tmp_path, child=Child(code=-signal.SIGINT, alive=1000))
    rig.waiter.steps = [signal.SIGINT]
    outcome = rig.run()
    assert outcome.signum == signal.SIGINT
    assert rig.said[0] == "moxi-dev check: stopped by SIGINT; memory then unknown"
    assert rig.systemctl("stop") == [["systemctl", "--user", "stop", "--no-block",
                                      "u.scope"]]
    assert rig.clock[0] >= scope.INT_GRACE_S


def test_sigint_with_an_inner_that_stops_needs_no_stop(tmp_path):
    rig = Rig(tmp_path, child=Child(code=-signal.SIGINT, alive=3))
    rig.waiter.steps = [signal.SIGINT]
    assert rig.run().signum == signal.SIGINT and rig.systemctl("stop") == []


def test_a_unit_still_holding_processes_after_a_check_is_stopped_with_no_line(tmp_path):
    rig = Rig(tmp_path, child=Child(code=0, alive=1))
    rig.waiter.steps = [rig.stat(10, high=0),
                        lambda: (rig.cgroup / "cgroup.procs").write_text("77\n") and None]
    assert rig.run().code == 0 and rig.said == []
    assert rig.systemctl("stop") == [["systemctl", "--user", "stop", "u.scope"]]


def test_the_cap_ends_its_leftovers_and_counts_them(tmp_path, monkeypatch):
    rig = Rig(tmp_path, kind="cap", child=Child(code=4, alive=1))
    rig.waiter.steps = [rig.stat(100, high=0, peak_mib=200)]
    (rig.cgroup / "cgroup.procs").write_text("77\n78\n")
    monkeypatch.setattr(scope, "end_leftovers", lambda group: 2)
    outcome = rig.run()
    assert outcome.code == 4
    assert rig.said == ["moxi-dev cap: ended 2 processes the command left running"]
    assert outcome.memory == "peak 0.2G, held 0.1G, line 6.1G, high 6.4G, max 7.9G, " \
                             "stalled unknown"

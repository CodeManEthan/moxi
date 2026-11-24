"""The kit's temp files: a top-level run chooses, records, cleans up and sweeps, and a
run nested in a test does none of it.

A top-level row starts a real child pytest with `MOXI_TEST_FENCED` taken out of its
environment and no `--basetemp`; the conftest it gets points `moxi_dev.temp` at a fake
machine under the test's tmp before the kit's configure, so it never sees a real root."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

from moxi_dev import temp
from moxi_dev.temp import FENCED_ENV, TEMP_ENV, TEMPROOT_ENV
from moxi_dev.testing import DURATIONS_ENV

pytestmark = pytest.mark.skipif(not sys.platform.startswith("linux"),
                                reason="the fake machine is a Linux one")


class FakeMachine:
    """Roots and /proc files under `base`, and the conftest that points a run at them."""

    def __init__(self, base: Path) -> None:
        self.base = base
        self.disk = base / "disk"
        self.ram = base / "shm"
        self.system = base / "systmp"
        for path in (self.disk, self.ram, self.system):
            path.mkdir(parents=True)
        proc = base / "proc"
        proc.mkdir()
        lines = ["1 0 0:1 / / rw - ext4 ext4 rw",
                 f"2 1 0:2 / {os.path.realpath(self.disk)} rw - ext4 ext4 rw",
                 f"3 1 0:3 / {os.path.realpath(self.ram)} rw - tmpfs tmpfs rw",
                 f"4 1 0:4 / {os.path.realpath(self.system)} rw - ext4 ext4 rw"]
        (proc / "mountinfo").write_text("\n".join(lines) + "\n")
        (proc / "meminfo").write_text("MemAvailable: 33554432 kB\n")
        (proc / "cgroup").write_text("0::/\n")
        (base / "cgroup").mkdir()
        self.places = temp.Places(platform="linux", disk_root=str(self.disk),
                                  ram_root=str(self.ram), system_temp=str(self.system),
                                  mountinfo=str(proc / "mountinfo"),
                                  meminfo=str(proc / "meminfo"), cgroup=str(proc / "cgroup"),
                                  cgroup_root=str(base / "cgroup"))

    def conftest(self) -> str:
        where = {key: value for key, value in vars(self.places).items() if key != "statvfs"}
        return (f"from moxi_dev import temp\n"
                f"temp.use_places(temp.Places(**{where!r}))\n"
                f"pytest_plugins = ['moxi_dev.testing']\n")

    def leftover(self, root: Path, name: str = "mx-old000") -> Path:
        """A directory a killed run left: the kit's record beside it, free, and old."""
        path = root / name
        path.mkdir()
        (path / "data").write_bytes(b"x" * 8192)
        temp.record_of(path).write_text(json.dumps({"kit": "moxi-dev", "pid": 1,
                                                    "made": "2026-01-01T00:00:00Z"}))
        past = time.time() - temp.SWEEP_MIN_AGE_S - 60
        os.utime(path, (past, past))
        return path

    def contents(self, root: Path) -> list[str]:
        return sorted(os.listdir(root))


@pytest.fixture
def machine(tmp_path: Path) -> FakeMachine:
    return FakeMachine(tmp_path / "machine")


@pytest.fixture
def top_level_run(pytester: pytest.Pytester, machine: FakeMachine,
                  monkeypatch: pytest.MonkeyPatch):
    """Run a real child pytest as a top-level run, in the fake machine."""
    pytester.makeconftest(machine.conftest())
    monkeypatch.delenv(FENCED_ENV, raising=False)
    monkeypatch.delenv(TEMPROOT_ENV, raising=False)

    def run(*args: str, setting: str | None = None) -> pytest.RunResult:
        if setting is None:
            monkeypatch.delenv(TEMP_ENV, raising=False)
        else:
            monkeypatch.setenv(TEMP_ENV, setting)
        return pytester.run(sys.executable, "-m", "pytest", "-p", "no:cacheprovider",
                            "-p", "no:randomly", "-rA", *args, timeout=180)

    return run


#: Tests the child runs: one passes and one fails, each writing in its tmp_path; then
#: the third looks at what is left of them while the run still goes, and at the short
#: process dir its own short directory sits in.
CHILD_TESTS = '''
import os, re
from pathlib import Path

def test_a_passes(tmp_path):
    (tmp_path / "f").write_text("x")

def test_b_fails(tmp_path):
    (tmp_path / "f").write_text("x")
    assert False, "on purpose"

def test_c_looks(tmp_path, fence):
    parent = tmp_path.parent
    print("BASETEMP", parent.parent if parent.name.startswith("popen-gw") else parent)
    in_memory = os.environ.get("CHILD_EXPECTS") == "ram"
    passed = list(Path(parent.parent).rglob("test_a_passes0"))
    failed = list(Path(parent.parent).rglob("test_b_fails0"))
    if in_memory:
        assert not passed and failed
    process_dir = Path(fence.short).parent
    assert re.fullmatch(r"mx-[a-z0-9_]{6}", process_dir.name), process_dir
    assert (process_dir.parent / (process_dir.name + ".mx-owner")).is_file()
    print("PROCESS_DIR", process_dir)
'''


def _child_tests(pytester: pytest.Pytester, expects: str) -> None:
    pytester.makepyfile(test_child=CHILD_TESTS.replace(
        'os.environ.get("CHILD_EXPECTS")', repr(expects)))


def _printed(result: pytest.RunResult, key: str) -> list[Path]:
    return [Path(line.split(key, 1)[1].strip()) for line in result.outlines
            if f"{key} " in line]


@pytest.mark.parametrize("workers", [[], ["-n", "2", "--dist", "loadfile"]], ids=["serial", "xdist"])
def test_a_memory_run_removes_its_temp(pytester, machine, top_level_run, workers):
    _child_tests(pytester, "ram")
    result = top_level_run(*workers, setting="ram")
    result.assert_outcomes(passed=2, failed=1)
    assert any(line == f"moxi-dev temp: ram, {machine.ram}; removed when the run ends, "
               "failed tests' files and browser traces included (MOXI_TEST_TEMP=disk "
               "keeps them)" for line in result.outlines), result.outlines[:8]
    basetemps = _printed(result, "BASETEMP")
    assert basetemps and all(path.is_relative_to(machine.ram) for path in basetemps)
    for path in basetemps:
        assert not path.exists() and not temp.record_of(path).exists()
    process_dirs = _printed(result, "PROCESS_DIR")
    assert process_dirs and all(path.parent == machine.ram for path in process_dirs)
    assert machine.contents(machine.ram) == []          # nothing of the run is left


def test_a_disk_run_keeps_pytests_retention(pytester, machine, top_level_run,
                                            monkeypatch, tmp_path):
    _child_tests(pytester, "disk")
    durations = tmp_path / "durations.json"
    monkeypatch.setenv(DURATIONS_ENV, str(durations))
    result = top_level_run()
    result.assert_outcomes(passed=2, failed=1)
    assert f"moxi-dev temp: disk, {machine.disk} (the default; MOXI_TEST_TEMP=ram keeps " \
           "it in memory)" in result.outlines
    (basetemp,) = _printed(result, "BASETEMP")
    assert basetemp.parent.parent == machine.disk
    assert (basetemp / "test_a_passes0" / "f").exists()          # pytest's retention
    assert not temp.record_of(basetemp).exists()                  # no record on disk
    (process_dir,) = _printed(result, "PROCESS_DIR")
    assert process_dir.parent == machine.disk
    assert not process_dir.exists() and not temp.record_of(process_dir).exists()
    assert machine.contents(machine.ram) == []
    chosen = json.loads(durations.read_text())["temp"]
    assert (chosen["mode"], chosen["root"], chosen["is_in_memory"]) == (
        "disk", str(machine.disk), False)


def test_a_top_level_run_sweeps_at_its_start(pytester, machine, top_level_run):
    gone = machine.leftover(machine.ram)
    kept = machine.disk / "mx-norec0"                  # no record: never the sweep's
    kept.mkdir()
    pytester.makepyfile(test_one="def test_one():\n    pass\n")
    result = top_level_run("-n", "2")
    result.assert_outcomes(passed=1)
    assert not gone.exists() and not temp.record_of(gone).exists()
    assert kept.is_dir()
    removed = [line for line in result.outlines if "left by runs" in line]
    assert len(removed) == 1                           # the controller swept; no worker
    assert re.fullmatch(r"moxi-dev temp: removed 1 test temp dir \([0-9]+ KiB\) left by "
                        r"runs that ended without cleaning up", removed[0])


def test_a_bad_setting_stops_the_run(pytester, machine, top_level_run):
    pytester.makepyfile(test_one="def test_one():\n    pass\n")
    result = top_level_run(setting="RAM")
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    assert "MOXI_TEST_TEMP must be disk or ram; it is 'RAM'" in result.stderr.str()


def test_a_basetemp_run_is_left_to_pytest(pytester, machine, top_level_run):
    leftover = machine.leftover(machine.ram)
    pytester.makepyfile(test_one="def test_one():\n    pass\n")
    base = machine.base / "given"
    result = top_level_run(f"--basetemp={base}", setting="ram")
    result.assert_outcomes(passed=1)
    assert f"moxi-dev temp: --basetemp {base}, left to pytest" in result.outlines
    assert leftover.is_dir()                           # no sweep
    assert base.is_dir()                               # nothing of pytest's removed


# --- nested runs -------------------------------------------------------------------------

NESTED_TESTS = '''
from pathlib import Path
from moxi_dev import testing

def test_short_dirs_go_in_the_outer_tests(fence):
    assert Path(fence.short).parent == Path(OUTER)
    with testing.short_dir() as where:
        assert where.parent == Path(fence.short)
'''


@pytest.mark.parametrize("how", ["in_process", "subprocess"])
def test_a_nested_run_chooses_records_and_sweeps_nothing(pytester, machine, monkeypatch,
                                                         fence, how):
    monkeypatch.setattr(temp, "_PLACES", None)         # whatever the conftest sets goes
    leftover = machine.leftover(machine.ram)
    before = {root: machine.contents(root) for root in (machine.disk, machine.ram)}
    pytester.makeconftest(machine.conftest())
    pytester.makepyfile(test_nested=NESTED_TESTS.replace("OUTER", repr(str(fence.short))))
    monkeypatch.setenv(TEMP_ENV, "ram")
    run = pytester.runpytest if how == "in_process" else pytester.runpytest_subprocess
    result = run("-p", "no:cacheprovider")
    result.assert_outcomes(passed=1)
    assert "moxi-dev temp: nested in a test; left to the outer run" in result.outlines
    assert leftover.is_dir() and temp.record_of(leftover).exists()
    assert {root: machine.contents(root) for root in before} == before
    assert [name for name in os.listdir(fence.short)
            if name.endswith(temp.RECORD_SUFFIX)] == []


# --- live: real roots --------------------------------------------------------------------

LIVE_TESTS = '''
import os, sys, time
from pathlib import Path

def test_shows_its_dirs(tmp_path, fence):
    parent = tmp_path.parent
    base = parent.parent if parent.name.startswith("popen-gw") else parent
    print("BASETEMP", base, flush=True)
    print("PROCESS_DIR", Path(fence.short).parent, flush=True)
    if os.environ.get("LIVE_HANG"):
        time.sleep(600)
'''


def _live_child(pytester: pytest.Pytester, setting: str, *,
                hang: bool = False) -> subprocess.Popen:
    """A real top-level run: no MOXI_TEST_FENCED, no --basetemp, the real roots."""
    pytester.makeconftest("pytest_plugins = ['moxi_dev.testing']\n")
    pytester.makepyfile(test_live_child=LIVE_TESTS)
    env = {key: value for key, value in os.environ.items()
           if key not in (FENCED_ENV, TEMPROOT_ENV)}
    env[TEMP_ENV] = setting
    if hang:
        env["LIVE_HANG"] = "1"
    return subprocess.Popen([sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-s",
                             str(pytester.path)], cwd=pytester.path, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)


def _read_dirs(child: subprocess.Popen) -> tuple[Path, Path, list[str]]:
    lines: list[str] = []
    found: dict[str, Path] = {}
    while len(found) < 2:
        line = child.stdout.readline()
        if not line:
            break
        lines.append(line.rstrip("\n"))
        for key in ("BASETEMP", "PROCESS_DIR"):
            if f"{key} " in line:                  # after pytest's progress, under -s
                found[key] = Path(line.split(f"{key} ", 1)[1].strip())
    assert len(found) == 2, "\n".join(lines)
    return found["BASETEMP"], found["PROCESS_DIR"], lines


def _ram_or_skip(lines: list[str]) -> None:
    header = next((line for line in lines if line.startswith("moxi-dev temp: ")), "")
    if not header.startswith("moxi-dev temp: ram, /dev/shm"):
        pytest.skip(f"this machine can't run in RAM now: {header}")


@pytest.mark.live
def test_live_a_ram_run_uses_shm_and_leaves_nothing(pytester):
    child = _live_child(pytester, "ram")
    basetemp, process_dir, lines = _read_dirs(child)
    rest, _ = child.communicate(timeout=120)
    _ram_or_skip(lines)
    assert child.returncode == 0, "\n".join(lines) + rest
    assert basetemp.is_relative_to("/dev/shm") and process_dir.parent == Path("/dev/shm")
    for path in (basetemp, process_dir):
        assert not path.exists() and not temp.record_of(path).exists()


@pytest.mark.live
def test_live_a_killed_run_is_swept_by_the_next(pytester):
    child = _live_child(pytester, "ram", hang=True)
    try:
        basetemp, process_dir, lines = _read_dirs(child)
        _ram_or_skip(lines)
    finally:
        child.kill()
        child.communicate(timeout=60)
    for path in (basetemp, process_dir):
        assert path.is_dir() and temp.record_of(path).is_file()
    time.sleep(temp.SWEEP_MIN_AGE_S + 2)           # the sweep's floor on a dir's age
    after = _live_child(pytester, "ram")
    out, _ = after.communicate(timeout=120)
    assert after.returncode == 0, out
    assert "left by runs that ended without cleaning up" in out
    for path in (basetemp, process_dir):
        assert not path.exists() and not temp.record_of(path).exists()


@pytest.mark.live
def test_live_a_disk_runs_short_dirs_are_in_var_tmp_and_go(pytester):
    child = _live_child(pytester, "disk")
    _, process_dir, lines = _read_dirs(child)
    rest, _ = child.communicate(timeout=120)
    assert child.returncode == 0, "\n".join(lines) + rest
    assert process_dir.parent == Path("/var/tmp")
    assert not process_dir.exists() and not temp.record_of(process_dir).exists()

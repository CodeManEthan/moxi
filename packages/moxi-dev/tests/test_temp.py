"""Where tests keep their temp (moxi_dev.temp): the choice, a check's environment, a
nested run, the room a ram run needs, owner records and the sweep. Every root, /proc
file and statvfs here is a stand-in under the test's tmp."""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from moxi_dev import temp
from moxi_dev.temp import DISK, FENCED_ENV, RAM, TEMP_ENV, TEMPROOT_ENV, Places

POSIX = os.name == "posix"
MIB = 1024 * 1024

if POSIX:
    import fcntl


def _mount_line(number: int, point: Path | str, kind: str) -> str:
    return f"{number} 1 0:{number} / {point} rw - {kind} {kind} rw"


class Machine:
    """A fake system under `base`: its roots, mount table, meminfo and cgroup files."""

    def __init__(self, base: Path, platform: str = "linux") -> None:
        self.base = base
        self.disk = base / "var-tmp"
        self.ram = base / "shm"
        self.system = base / "tmp"
        self.macos = base / "mac-tmp"
        for path in (self.disk, self.ram, self.system, self.macos):
            path.mkdir()
        self.platform = platform
        self.kinds = {self.disk: "ext4", self.ram: "tmpfs", self.system: "tmpfs"}
        self.free_mib = 16 * 1024
        self.available_mib = 16 * 1024
        self.cgroup_line: str | None = "0::/user.slice/run.scope"
        self.cgroups: dict[str, dict[str, str]] = {}
        self.proc = base / "proc"
        self.proc.mkdir()

    def places(self) -> Places:
        lines = [_mount_line(1, "/", "ext4")]
        lines += [_mount_line(n, os.path.realpath(path), kind)
                  for n, (path, kind) in enumerate(self.kinds.items(), start=2)]
        (self.proc / "mountinfo").write_text("\n".join(lines) + "\n")
        (self.proc / "meminfo").write_text(
            f"MemTotal: 33554432 kB\nMemAvailable: {self.available_mib * 1024} kB\n")
        (self.proc / "cgroup").write_text(f"{self.cgroup_line}\n" if self.cgroup_line
                                          else "1:name=systemd:/x\n")
        cgroup_root = self.base / "cgroup"
        for rel, files in self.cgroups.items():
            where = cgroup_root / rel
            where.mkdir(parents=True, exist_ok=True)
            for name, value in files.items():
                (where / name).write_text(value + "\n")
        cgroup_root.mkdir(exist_ok=True)
        free = self.free_mib

        def statvfs(path: str) -> SimpleNamespace:
            return SimpleNamespace(f_bavail=free * 256, f_frsize=4096)

        return Places(platform=self.platform, disk_root=str(self.disk),
                      ram_root=str(self.ram), system_temp=str(self.system),
                      macos_short_root=str(self.macos),
                      mountinfo=str(self.proc / "mountinfo"),
                      meminfo=str(self.proc / "meminfo"), cgroup=str(self.proc / "cgroup"),
                      cgroup_root=str(cgroup_root), statvfs=statvfs)


@pytest.fixture
def machine(tmp_path: Path) -> Machine:
    return Machine(tmp_path)


# --- choose ------------------------------------------------------------------------------

def test_linux_disk_is_the_default_and_the_setting(machine):
    where = machine.places()
    chosen = temp.choose({}, where=where)
    assert (chosen.mode, chosen.root, chosen.short_root) == (DISK, str(machine.disk),
                                                             str(machine.disk))
    assert not chosen.is_in_memory and chosen.source == temp.BY_DEFAULT
    assert chosen.header() == (f"disk, {machine.disk} (the default; MOXI_TEST_TEMP=ram "
                               "keeps it in memory)")
    asked = temp.choose({TEMP_ENV: "disk"}, where=where)
    assert (asked.mode, asked.root, asked.source) == (DISK, str(machine.disk),
                                                      temp.BY_SETTING)
    assert asked.header() == f"disk, {machine.disk} (MOXI_TEST_TEMP=disk)"
    assert temp.choose({TEMP_ENV: ""}, where=where).source == temp.BY_DEFAULT


def test_linux_ram_puts_both_roots_in_shm(machine):
    chosen = temp.choose({TEMP_ENV: "ram"}, workers=8, where=machine.places())
    assert (chosen.mode, chosen.root, chosen.short_root) == (RAM, str(machine.ram),
                                                             str(machine.ram))
    assert chosen.is_in_memory
    assert chosen.header() == (f"ram, {machine.ram}; removed when the run ends, failed "
                               "tests' files and browser traces included "
                               "(MOXI_TEST_TEMP=disk keeps them)")


@pytest.mark.parametrize("platform", ["darwin", "win32"])
def test_off_linux_disk_keeps_todays_roots_and_ram_runs_as_disk(machine, platform):
    machine.platform = platform
    where = machine.places()
    short = str(machine.macos if platform == "darwin" else machine.system)
    for env in ({}, {TEMP_ENV: "disk"}, {TEMP_ENV: "ram"}):
        chosen = temp.choose(env, workers=16, where=where)
        assert (chosen.mode, chosen.root, chosen.short_root) == (DISK, str(machine.system),
                                                                 short)
        assert not chosen.is_in_memory          # off Linux nothing is read
    assert temp.choose({}, where=where).header() == f"disk, {machine.system} (the default)"
    assert temp.choose({TEMP_ENV: "ram"}, where=where).header() == (
        f"disk, {machine.system}: ram was asked, but this system has no RAM filesystem")


@pytest.mark.parametrize("value", ["RAM", "memory", "1", "Disk"])
def test_a_bad_value_stops_the_run(machine, value):
    with pytest.raises(temp.TempError) as caught:
        temp.choose({TEMP_ENV: value}, where=machine.places())
    assert str(caught.value) == f"MOXI_TEST_TEMP must be disk or ram; it is {value!r}"


def test_a_basetemp_wins_over_the_setting(machine):
    where = machine.places()
    chosen = temp.choose({TEMP_ENV: "ram", TEMPROOT_ENV: "/x"}, basetemp="/x/y", where=where)
    assert chosen.source == temp.BY_BASETEMP and chosen.root is None
    assert not chosen.is_in_memory and not chosen.sweeps and not chosen.is_nested
    assert chosen.short_root == str(machine.ram)        # the short root follows the setting
    assert chosen.header() == "--basetemp /x/y, left to pytest"
    assert temp.choose({}, basetemp="/x/y", where=where).short_root == str(machine.disk)


def test_the_setting_overrides_another_temproot_and_says_so(machine, tmp_path):
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    where = machine.places()
    chosen = temp.choose({TEMP_ENV: "ram", TEMPROOT_ENV: str(scratch)}, where=where)
    assert (chosen.root, chosen.over) == (str(machine.ram), str(scratch))
    assert chosen.header().startswith(f"ram, {machine.ram}, over "
                                      f"PYTEST_DEBUG_TEMPROOT={scratch}; removed")
    on_disk = temp.choose({TEMP_ENV: "disk", TEMPROOT_ENV: str(scratch)}, where=where)
    assert on_disk.root == str(machine.disk)
    assert on_disk.header() == (f"disk, {machine.disk} (MOXI_TEST_TEMP=disk), over "
                                f"PYTEST_DEBUG_TEMPROOT={scratch}")


def test_a_temproot_that_is_the_default_root_is_not_named_as_overridden(machine):
    where = machine.places()
    chosen = temp.choose({TEMP_ENV: "ram", TEMPROOT_ENV: str(machine.disk)}, where=where)
    assert (chosen.root, chosen.over) == (str(machine.ram), None)
    assert "over" not in chosen.header().split(";")[0]


def test_a_temproot_is_honoured_when_the_setting_is_unset(machine, tmp_path):
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    chosen = temp.choose({TEMPROOT_ENV: str(scratch)}, where=machine.places())
    assert (chosen.source, chosen.root) == (temp.BY_TEMPROOT, str(scratch))
    assert chosen.short_root == str(machine.disk)       # the short root follows the default
    assert not chosen.is_in_memory
    assert chosen.header() == f"disk, {scratch} (PYTEST_DEBUG_TEMPROOT)"


def test_a_temproot_naming_the_default_root_reads_as_the_default(machine, tmp_path):
    link = tmp_path / "link"
    link.symlink_to(machine.disk)
    where = machine.places()
    for given in (str(machine.disk), str(machine.disk) + "/", str(link)):
        chosen = temp.choose({TEMPROOT_ENV: given}, where=where)
        assert chosen.source == temp.BY_DEFAULT and chosen.root == str(machine.disk)


def test_a_disk_root_on_tmpfs_falls_back_with_its_reason(machine):
    machine.kinds[machine.disk] = "tmpfs"
    machine.kinds[machine.system] = "ext4"
    chosen = temp.choose({TEMP_ENV: "disk"}, where=machine.places())
    assert (chosen.root, chosen.short_root) == (str(machine.system), str(machine.system))
    assert chosen.why == f"{machine.disk} is on tmpfs, not on disk"
    assert chosen.header() == f"disk, {machine.system}: {machine.disk} is on tmpfs, not on disk"


def test_a_missing_disk_root_falls_back_and_says_so(machine):
    machine.disk.rmdir()
    chosen = temp.choose({}, where=machine.places())
    assert chosen.root == str(machine.system) and chosen.is_in_memory
    assert chosen.header() == (
        f"disk, {machine.system}: {machine.disk} is not writable; it is in memory, so it "
        "is removed when the run ends, failed tests' files and browser traces included "
        "(MOXI_TEST_TEMP=disk keeps them)")


@pytest.mark.skipif(not POSIX, reason="a mount table names POSIX paths")
def test_a_root_in_memory_is_in_memory_whatever_chose_it(machine, tmp_path):
    shm_scratch = machine.ram / "scratch"
    shm_scratch.mkdir()
    chosen = temp.choose({TEMPROOT_ENV: str(shm_scratch)}, where=machine.places())
    assert chosen.source == temp.BY_TEMPROOT and chosen.is_in_memory


def test_a_choice_passes_to_a_worker_as_plain_values(machine):
    chosen = temp.choose({TEMP_ENV: "ram"}, where=machine.places())
    data = json.loads(json.dumps(chosen.as_dict()))
    assert temp.Choice.from_dict({**data, "unknown": 1}) == chosen


# --- room for a ram run ------------------------------------------------------------------

def test_the_need_grows_with_the_workers():
    assert [temp.ram_need_mib(n) for n in (1, 8, 16)] == [208, 1216, 2368]
    assert temp.ram_need_mib(0) == temp.ram_need_mib(1)


def _ram(machine: Machine, workers: int = 8) -> temp.Choice:
    return temp.choose({TEMP_ENV: "ram"}, workers=workers, where=machine.places())


def test_too_little_free_space_in_shm_goes_to_disk(machine):
    machine.free_mib = 900
    chosen = _ram(machine)
    assert chosen.mode == DISK and chosen.root == str(machine.disk)
    assert chosen.header() == (f"disk, {machine.disk}: ram was asked, but {machine.ram} "
                               "has 900 MiB free and this run needs about 1216 MiB")


def test_too_little_memory_available_goes_to_disk(machine):
    machine.available_mib = 1216 + 2048 - 1
    assert _ram(machine).why == ("ram was asked, but the machine has 3263 MiB available "
                                 "and this run needs about 1216 MiB, with 2048 MiB left "
                                 "to spare")
    machine.available_mib = 1216 + 2048
    assert _ram(machine).mode == RAM


@pytest.mark.parametrize("where", ["own", "ancestor"])
def test_a_cgroup_limit_short_of_the_need_goes_to_disk(machine, where):
    rel = "user.slice/run.scope" if where == "own" else "user.slice"
    machine.cgroups[rel] = {"memory.high": str(2000 * MIB),
                            "memory.current": str(1100 * MIB)}
    chosen = _ram(machine)
    assert chosen.mode == DISK
    assert chosen.header() == (f"disk, {machine.disk}: ram was asked, but this run's "
                               "memory limit leaves 900 MiB and it needs about 1216 MiB")


def test_memory_max_counts_where_high_is_unset(machine):
    machine.cgroups["user.slice/run.scope"] = {"memory.high": "max",
                                               "memory.max": str(1300 * MIB),
                                               "memory.current": str(100 * MIB)}
    assert _ram(machine).mode == DISK
    machine.cgroups["user.slice/run.scope"]["memory.max"] = str(1316 * MIB)
    assert _ram(machine).mode == RAM                # just enough: 1216 MiB left


def test_just_enough_room_stays_in_ram(machine):
    machine.free_mib = 1216
    machine.available_mib = 1216 + 2048
    assert _ram(machine).mode == RAM


def test_no_limit_or_no_cgroup_v2_skips_the_cgroup_test(machine):
    machine.cgroups["user.slice"] = {"memory.high": "max", "memory.max": "max",
                                     "memory.current": str(10**12)}
    assert temp.cgroup_headroom_mib(machine.places()) is None
    assert _ram(machine).mode == RAM
    machine.cgroup_line = None
    assert temp.cgroup_headroom_mib(machine.places()) is None


def test_a_shm_that_cannot_serve_goes_to_disk(machine):
    machine.kinds[machine.ram] = "ext4"
    assert _ram(machine).why == f"ram was asked, but {machine.ram} is on ext4, not tmpfs"
    machine.ram.rmdir()
    assert _ram(machine).why == f"ram was asked, but {machine.ram} is missing"


@pytest.mark.skipif(not POSIX or os.geteuid() == 0, reason="needs POSIX file modes, not root")
def test_a_shm_that_is_not_writable_goes_to_disk(machine):
    machine.ram.chmod(0o500)
    try:
        assert _ram(machine).why == f"ram was asked, but {machine.ram} is not writable"
    finally:
        machine.ram.chmod(0o700)


# --- a check's environment ---------------------------------------------------------------

def test_a_check_moves_ram_to_disk_on_linux(machine):
    env, why = temp.for_check({TEMP_ENV: "ram", "X": "1"}, where=machine.places())
    assert env == {TEMP_ENV: "disk", "X": "1"}
    assert why == "ram asked; a check keeps its temp on disk"


@pytest.mark.parametrize("platform", ["darwin", "win32"])
def test_a_check_leaves_ram_alone_off_linux(machine, platform):
    machine.platform = platform
    env, why = temp.for_check({TEMP_ENV: "ram"}, where=machine.places())
    assert env == {TEMP_ENV: "ram"}
    assert why == "ram asked; no RAM filesystem on this system"


def test_a_check_moves_a_temproot_in_memory_to_disk(machine):
    given = {TEMPROOT_ENV: str(machine.ram)}
    env, why = temp.for_check(given, where=machine.places())
    assert env == {**given, TEMP_ENV: "disk"}
    assert why == (f"PYTEST_DEBUG_TEMPROOT={machine.ram} is in memory; a check keeps its "
                   "temp on disk")


def test_a_check_passes_a_disk_choice_through(machine, tmp_path):
    where = machine.places()
    for given in ({}, {TEMPROOT_ENV: str(machine.disk)}, {TEMPROOT_ENV: str(tmp_path)},
                  {TEMP_ENV: "disk"}, {TEMP_ENV: "disk", TEMPROOT_ENV: str(machine.ram)}):
        assert temp.for_check(given, where=where) == (given, None)
    with pytest.raises(temp.TempError):
        temp.for_check({TEMP_ENV: "RAM"}, where=where)


# --- top-level or nested -----------------------------------------------------------------

def test_a_run_is_nested_with_a_fence_or_the_mark():
    assert temp.top_level({}, is_fenced=False)
    assert not temp.top_level({}, is_fenced=True)
    assert not temp.top_level({FENCED_ENV: "/tmp/mx-x"}, is_fenced=False)
    assert not temp.top_level({})                   # this test runs fenced
    nested = temp.nested("/short")
    assert nested.is_nested and not nested.sweeps
    assert nested.header() == "nested in a test; left to the outer run"


# --- owner records -----------------------------------------------------------------------

HOLDER = """
import os, sys
from pathlib import Path
from moxi_dev import temp
kind, where = sys.argv[1], sys.argv[2]
if kind == "dir":
    held = temp.make_process_dir(where)
else:
    Path(where).mkdir(exist_ok=True)
    held = temp.hold(where)
print(held.path, flush=True)
if len(sys.argv) > 3 and sys.argv[3] == "fork":
    child = os.fork()
    if child == 0:
        import time
        time.sleep(60)
        os._exit(0)
    print(child, flush=True)
    os._exit(0)
sys.stdin.read()
"""


class Holder:
    """A child process that holds a record, until its stdin closes."""

    def __init__(self, tmp_path: Path, kind: str, where: Path, *extra: str) -> None:
        script = tmp_path / "holder.py"
        script.write_text(HOLDER)
        self.proc = subprocess.Popen([sys.executable, str(script), kind, str(where), *extra],
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     text=True)
        self.path = Path(self.proc.stdout.readline().strip())
        assert self.path.name, "the holder printed no path"

    def end(self) -> None:
        self.proc.stdin.close()
        self.proc.wait(timeout=30)


def _old(path: Path) -> None:
    """Set `path`'s own mtime past the sweep's age; where `os.utime` can't leave a link
    alone (Windows), the path's, which is never a link there."""
    past = time.time() - temp.SWEEP_MIN_AGE_S - 30
    if os.utime in os.supports_follow_symlinks:
        os.utime(path, (past, past), follow_symlinks=False)
    else:
        os.utime(path, (past, past))


def _wait_exited(pid: int) -> None:
    """Wait until the child `pid` has exited, without reaping it: by `os.waitid` where
    the system has it, else (macOS) until `ps` shows it a zombie."""
    if hasattr(os, "waitid"):
        os.waitid(os.P_PID, pid, os.WEXITED | os.WNOWAIT)
        return
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        shown = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True,
                               text=True, check=False).stdout.strip()
        if shown.startswith("Z"):
            return
        time.sleep(0.05)
    raise AssertionError(f"{pid} did not exit within 30 s")


class _Locking:
    """`msvcrt.locking` as `fcntl.flock` stands in for it, the position of each call
    kept."""

    LK_UNLCK = 0
    LK_NBLCK = 2

    def __init__(self) -> None:
        self.positions: list[int] = []

    def locking(self, fd: int, mode: int, nbytes: int) -> None:
        self.positions.append(os.lseek(fd, 0, os.SEEK_CUR))
        if mode == self.LK_UNLCK:
            fcntl.flock(fd, fcntl.LOCK_UN)
        else:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)


class _WindowsFiles:
    """`os` as `temp` meets it on Windows: a file another handle holds open can't be
    unlinked. Everything else is the real `os`."""

    def __init__(self) -> None:
        self.open_fds: dict[int, str] = {}
        self.refusals = 0
        self._guard = threading.Lock()

    def __getattr__(self, name: str):
        return getattr(os, name)

    def open(self, path, flags: int, mode: int = 0o777) -> int:
        fd = os.open(path, flags, mode)
        with self._guard:
            self.open_fds[fd] = os.fspath(path)
        return fd

    def close(self, fd: int) -> None:
        with self._guard:
            self.open_fds.pop(fd, None)
        os.close(fd)

    def unlink(self, path) -> None:
        with self._guard:
            is_open = os.fspath(path) in self.open_fds.values()
            if is_open:
                self.refusals += 1
        if is_open:
            raise PermissionError(13, "The process cannot access the file", os.fspath(path))
        os.unlink(path)


@pytest.fixture
def windows_like(monkeypatch) -> SimpleNamespace:
    """`temp` run as on Windows, on POSIX: its Windows branches, `msvcrt.locking` and
    Windows' refusal to unlink an open file stood in."""
    if not POSIX:
        pytest.skip("on Windows the real limits run in the test's other case")
    files, locks = _WindowsFiles(), _Locking()
    monkeypatch.setattr(temp, "WINDOWS", True)
    monkeypatch.setattr(temp, "msvcrt", locks, raising=False)
    monkeypatch.setattr(temp, "os", files)
    return SimpleNamespace(files=files, locks=locks)


@pytest.fixture(params=["here", "windows-like"])
def system(request) -> SimpleNamespace | None:
    """Each test that takes it runs on this system, then as on Windows (`windows_like`)."""
    return request.getfixturevalue("windows_like") if request.param == "windows-like" \
        else None


def _basetemp_parent(machine: Machine) -> Path:
    parent = machine.ram / f"pytest-of-{temp._user()}"
    parent.mkdir(exist_ok=True)
    return parent


def test_a_live_owners_dir_is_kept_and_goes_once_it_exits(machine, tmp_path):
    where = machine.places()
    holder = Holder(tmp_path, "dir", machine.disk)
    assert temp.PROCESS_DIR_NAME.fullmatch(holder.path.name)
    record = temp.record_of(holder.path)
    assert json.loads(record.read_text())["kit"] == "moxi-dev"
    _old(holder.path)
    assert temp.sweep(where).count == 0
    assert holder.path.is_dir() and record.exists()
    holder.end()
    _old(holder.path)
    swept = temp.sweep(where)
    assert swept.count == 1 and swept.line().startswith("removed 1 test temp dir (")
    assert not holder.path.exists() and not record.exists()


def test_a_basetemp_is_kept_while_a_worker_holds_its_record(machine, tmp_path):
    where = machine.places()
    basetemp = _basetemp_parent(machine) / "pytest-7"
    basetemp.mkdir()
    worker = Holder(tmp_path, "worker", basetemp / "popen-gw0")
    record = temp.record_of(basetemp)               # the controller's record, free
    record.write_text(json.dumps({"kit": "moxi-dev", "pid": 1, "made": "x"}))
    _old(basetemp)
    assert temp.sweep(where).count == 0 and basetemp.is_dir()
    worker.end()
    assert temp.sweep(where).count == 1
    assert not basetemp.exists() and not record.exists()


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="reads /proc/self/fd")
def test_a_record_taken_before_its_lock_starts_again_under_a_new_name(machine, monkeypatch):
    real_lock = temp._lock_within
    taken: list[str] = []

    def sweeper_first(fd, wait_s):
        if not taken:
            record = Path(os.readlink(f"/proc/self/fd/{fd}"))
            taken.append(record.name)
            os.unlink(record)                       # a sweeper removed it in between
        return real_lock(fd, wait_s)

    monkeypatch.setattr(temp, "_lock_within", sweeper_first)
    held = temp.make_process_dir(machine.disk)
    try:
        assert taken and held.record.name != taken[0]
        assert not (machine.disk / taken[0]).exists()
        assert held.path.is_dir() and held.record.exists()
    finally:
        held.release()
    assert not held.path.exists() and not held.record.exists()


def test_a_basetemp_takes_over_a_free_orphan_record(machine):
    basetemp = _basetemp_parent(machine) / "pytest-3"
    basetemp.mkdir()
    record = temp.record_of(basetemp)
    record.write_text(json.dumps({"kit": "moxi-dev", "pid": 123456789,
                                  "made": "2026-01-01T00:00:00Z", "note": "x" * 400}))
    held = temp.hold(basetemp)
    try:
        data = json.loads(record.read_text())      # no old tail left behind
        assert data["pid"] == os.getpid() and "note" not in data
    finally:
        held.release()
    assert not basetemp.exists() and not record.exists()


def test_a_basetemp_whose_record_is_held_stops_the_run(machine, tmp_path, monkeypatch):
    monkeypatch.setattr(temp, "TAKE_OVER_WAIT_S", 0.3)
    basetemp = _basetemp_parent(machine) / "pytest-4"
    holder = Holder(tmp_path, "worker", basetemp)
    try:
        with pytest.raises(temp.TempError, match="is held by a live process"):
            temp.hold(basetemp)
    finally:
        holder.end()


@pytest.mark.parametrize("body", ['{"kit": "moxi-dev", "pid": 1, "made": "x"}',
                                  "not json", ""])
def test_a_free_record_whose_path_is_gone_goes(machine, body):
    record = machine.disk / ("mx-abc123" + temp.RECORD_SUFFIX)
    record.write_text(body)
    temp.sweep(machine.places())
    assert not record.exists()


def test_a_free_record_that_does_not_parse_is_left_with_its_path(machine):
    path = machine.disk / "mx-abc123"
    path.mkdir()
    record = temp.record_of(path)
    record.write_text('{"kit": "other"}')
    _old(path)
    temp.sweep(machine.places())
    assert path.is_dir() and record.exists()


@pytest.mark.skipif(not POSIX, reason="SIGKILL and zombies are POSIX")
@pytest.mark.parametrize("waiter", ["waitid", "ps"])
def test_a_killed_owner_and_a_zombie_hold_nothing(machine, tmp_path, monkeypatch, waiter):
    if waiter == "ps":                              # as on macOS, which has no waitid
        monkeypatch.delattr(os, "waitid", raising=False)
    where = machine.places()
    killed = Holder(tmp_path, "dir", machine.disk)
    killed.proc.send_signal(signal.SIGKILL)
    killed.proc.wait(timeout=30)
    zombie = Holder(tmp_path, "dir", machine.disk)
    zombie.proc.stdin.close()
    _wait_exited(zombie.proc.pid)                   # exited, not reaped
    for path in (killed.path, zombie.path):
        _old(path)
    assert temp.sweep(where).count == 2
    assert not killed.path.exists() and not zombie.path.exists()
    zombie.proc.wait(timeout=30)


@pytest.mark.skipif(not hasattr(os, "fork"), reason="needs os.fork")
def test_a_child_forked_from_the_owner_holds_nothing_once_the_owner_is_gone(machine,
                                                                           tmp_path):
    holder = Holder(tmp_path, "dir", machine.disk, "fork")
    grandchild = int(holder.proc.stdout.readline())
    holder.proc.wait(timeout=30)
    try:
        _old(holder.path)
        assert temp.sweep(machine.places()).count == 1
        assert not holder.path.exists()
    finally:
        os.kill(grandchild, signal.SIGKILL)


def test_an_owner_and_a_sweeper_leave_no_record_behind(machine, system):
    held = temp.make_process_dir(machine.disk)
    held.release()
    assert sorted(os.listdir(machine.disk)) == []
    path = machine.disk / "mx-abc123"
    path.mkdir()
    temp.record_of(path).write_text('{"kit": "moxi-dev"}')
    _old(path)
    temp.sweep(machine.places())
    assert sorted(os.listdir(machine.disk)) == []


@pytest.mark.skipif(not temp.WINDOWS, reason="the order Windows needs")
def test_windows_closes_a_record_before_it_unlinks_it(machine, monkeypatch):
    calls: list[str] = []
    real_close, real_unlink = os.close, os.unlink
    monkeypatch.setattr(os, "close", lambda fd: (calls.append("close"), real_close(fd)))
    monkeypatch.setattr(os, "unlink", lambda p: (calls.append("unlink"), real_unlink(p)))
    held = temp.make_process_dir(machine.disk)
    calls.clear()
    held.release()
    assert calls == ["close", "unlink"]
    assert sorted(os.listdir(machine.disk)) == []


def test_the_sweep_never_removes_what_is_not_provably_the_kits(machine, tmp_path):
    parent = _basetemp_parent(machine)
    kept = [parent / "pytest-9", machine.disk / "mx-abc123", machine.ram / "mx-abc124",
            machine.disk / "mx-abcdefgh"]
    for path in kept:
        path.mkdir()
    eight = machine.disk / "mx-abcdefgh"
    temp.record_of(eight).write_text('{"kit": "moxi-dev"}')
    a_file = machine.disk / "mx-zzzzzz"
    a_file.write_text("")
    temp.record_of(a_file).write_text('{"kit": "moxi-dev"}')
    kept.append(a_file)
    if POSIX:
        target = tmp_path / "target"
        target.mkdir()
        link = machine.disk / "mx-link00"
        link.symlink_to(target)
        temp.record_of(link).write_text('{"kit": "moxi-dev"}')
        kept += [link, target]
    young = machine.disk / "mx-young0"
    young.mkdir()
    temp.record_of(young).write_text('{"kit": "moxi-dev"}')
    kept.append(young)
    outside = tmp_path / "elsewhere" / "mx-abc999"
    outside.mkdir(parents=True)
    temp.record_of(outside).write_text('{"kit": "moxi-dev"}')
    kept.append(outside)
    for path in kept:
        if path is not young:
            _old(path)
    assert temp.sweep(machine.places()).count == 0
    for path in kept:
        assert os.path.lexists(path), path


def test_a_sweep_with_no_places_does_nothing_inside_a_test(machine):
    path = machine.disk / "mx-abc123"
    path.mkdir()
    temp.record_of(path).write_text('{"kit": "moxi-dev"}')
    _old(path)
    temp.use_places(machine.places())
    try:
        assert temp.sweep().count == 0
    finally:
        temp.use_places(None)
    assert path.is_dir()


def test_two_sweeps_at_once_both_end_clean(machine, system):
    where = machine.places()
    parent = _basetemp_parent(machine)
    made = []
    for number in range(20):
        for path in (machine.disk / f"mx-{number:06d}", parent / f"pytest-{number}"):
            path.mkdir()
            (path / "file").write_bytes(b"x" * 4096)
            temp.record_of(path).write_text('{"kit": "moxi-dev"}')
            _old(path)
            made.append(path)
    errors: list[BaseException] = []
    results: list[temp.Swept] = []

    def run() -> None:
        try:
            results.append(temp.sweep(where))
        except BaseException as exc:                # noqa: BLE001 -- reported below
            errors.append(exc)

    threads = [threading.Thread(target=run) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not errors
    assert sum(result.count for result in results) == len(made)
    assert os.listdir(machine.disk) == [] and os.listdir(parent) == []


def test_a_path_another_removed_first_is_not_counted_and_its_record_goes(machine,
                                                                        monkeypatch, system):
    path = machine.disk / "mx-abc123"
    path.mkdir()
    temp.record_of(path).write_text('{"kit": "moxi-dev"}')
    _old(path)
    real_open_free = temp._open_free

    def another_sweeper_first(record):
        fd = real_open_free(record)
        shutil.rmtree(path, ignore_errors=True)    # gone before this one held the lock
        return fd

    monkeypatch.setattr(temp, "_open_free", another_sweeper_first)
    assert temp.sweep(machine.places()).count == 0
    assert os.listdir(machine.disk) == []


def test_the_age_is_set_where_utime_cannot_leave_a_link_alone(machine, monkeypatch):
    real_utime = os.utime

    def windows_utime(path, times=None, *, follow_symlinks=True):
        if not follow_symlinks:
            raise NotImplementedError("utime: follow_symlinks unavailable on this platform")
        real_utime(path, times)

    monkeypatch.setattr(os, "utime", windows_utime)
    monkeypatch.setattr(os, "supports_follow_symlinks", frozenset())
    path = machine.disk / "mx-abc123"
    path.mkdir()
    temp.record_of(path).write_text('{"kit": "moxi-dev"}')
    _old(path)
    assert temp.sweep(machine.places()).count == 1
    assert os.listdir(machine.disk) == []


def test_on_windows_the_lock_sits_past_the_json_so_a_held_record_reads(machine,
                                                                       windows_like):
    basetemp = _basetemp_parent(machine) / "pytest-3"
    basetemp.mkdir()
    orphan = temp.record_of(basetemp)
    orphan.write_text(json.dumps({"kit": "moxi-dev", "pid": 123456789,
                                  "made": "2026-01-01T00:00:00Z", "note": "x" * 400}))
    taken = temp.hold(basetemp)                     # takes the free orphan over
    made = temp.make_process_dir(machine.disk)
    try:
        for path in (basetemp, made.path):
            body = temp.record_of(path).read_bytes()
            data = json.loads(body)
            assert data["pid"] == os.getpid() and "note" not in data
            assert len(body) < temp.WINDOWS_LOCK_POSITION
        positions = windows_like.locks.positions
        assert positions and all(position == temp.WINDOWS_LOCK_POSITION for position in positions)
    finally:
        taken.release()
        made.release()
    assert os.listdir(machine.disk) == [] and not orphan.exists()


def test_on_windows_a_record_open_elsewhere_goes_once_that_handle_closes(machine,
                                                                         windows_like):
    held = temp.make_process_dir(machine.disk)
    peek = windows_like.files.open(held.record, os.O_RDONLY)   # a sweeper testing it
    closer = threading.Timer(0.2, windows_like.files.close, [peek])
    closer.start()
    held.release()
    closer.join()
    assert windows_like.files.refusals > 0
    assert os.listdir(machine.disk) == []


def test_on_windows_a_record_open_past_the_wait_goes_at_the_next_sweep(machine,
                                                                       windows_like,
                                                                       monkeypatch):
    monkeypatch.setattr(temp, "UNLINK_WAIT_S", 0.1)
    held = temp.make_process_dir(machine.disk)
    peek = windows_like.files.open(held.record, os.O_RDONLY)
    held.release()
    assert not held.path.exists() and held.record.exists()
    windows_like.files.close(peek)
    temp.sweep(machine.places())
    assert os.listdir(machine.disk) == []

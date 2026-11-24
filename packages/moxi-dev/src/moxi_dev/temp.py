"""Where Moxi's tests keep their temp files, and the cleanup of what they leave.

The setting is one environment variable, `MOXI_TEST_TEMP`: `disk` or `ram`, unset or
empty meaning `disk` on every machine. Any other value stops the run before a test runs.

Roots, by system:

- Linux: `disk` puts pytest's root and the kit's short root at `/var/tmp`, `ram` puts
  both at `/dev/shm`. Each is the directory itself; pytest makes
  `pytest-of-<user>/pytest-<N>` under it. `/var/tmp` serves when it is a writable
  directory not on tmpfs or ramfs; else the run takes the system temp directory and
  says why. `/dev/shm` serves when it is a writable directory on tmpfs with room for
  the run (`ram_room`); else the run goes to disk and says why.
- macOS and Windows: pytest's own default root; the short root is `/tmp` on macOS and
  the system temp directory on Windows. `ram` runs as `disk` and says that this system
  has no RAM filesystem.

Who chooses (`choose`), in this order, the first that holds deciding: a `--basetemp`
given to the run leaves it to pytest; a set `MOXI_TEST_TEMP` decides, over any
`PYTEST_DEBUG_TEMPROOT`; a `PYTEST_DEBUG_TEMPROOT` naming another root than the
default's is honoured; else the default, `disk`. Only a top-level run chooses
(`top_level`): a run inside a fenced test, in process or as a child carrying the fence's
`MOXI_TEST_FENCED` mark, is nested and leaves everything to the outer run. The run's own
environment carries the mark too, so a run a fixture of wider scope starts as a child is
nested as well.

A check keeps its temp on disk whatever is set (`for_check`).

Cleanup. Each in-memory basetemp, each xdist worker's `popen-gw<K>` inside it and each
short process dir (`mx-` and 6 of `[a-z0-9_]`) has a sibling owner record,
`<path>.mx-owner`, that its maker holds open and locked until it exits (`Held`). The
kernel drops a process's locks however it exits, so a free lock means the owner is gone.
`sweep` removes only those shapes, in the places `Places` names, beside the kit's own
record, owned by this user, once every record holding the path is free and the
directory's own mtime is over `SWEEP_MIN_AGE_S` old; then the record. A free record
whose path is gone goes too. A path with no record, or beside a record that doesn't
parse as the kit's, is never removed.

The places read (the roots, the system temp directory, the `/proc` and cgroup files,
`statvfs`) are one `Places`; `use_places` puts another in their stead for a test.
"""

from __future__ import annotations

import contextlib
import dataclasses
import getpass
import json
import os
import re
import secrets
import shutil
import stat
import sys
import tempfile
import time
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

#: The setting: `disk` or `ram`; unset or empty is `disk`.
TEMP_ENV = "MOXI_TEST_TEMP"
#: The fence's mark in every fenced test's environment, the test's short directory, and
#: in the run's environment, the run's short directory. A run whose environment carries
#: it is nested in a test or in the run.
FENCED_ENV = "MOXI_TEST_FENCED"
#: pytest's own variable for its temp root.
TEMPROOT_ENV = "PYTEST_DEBUG_TEMPROOT"

DISK = "disk"
RAM = "ram"
MODES = (DISK, RAM)

#: What a `ram` run is taken to need in `/dev/shm`: this plus `TEMP_PER_WORKER_MIB` for
#: each worker. Both are from the largest disk temp a full check made, and overstate RAM
#: temp, since a memory run removes a passed test's files as it goes.
TEMP_BASE_MIB = 64
TEMP_PER_WORKER_MIB = 144
#: Memory a `ram` run leaves for the rest of the machine.
RAM_SPARE_MIB = 2048
#: How old a directory's own mtime must be before a sweep removes it.
SWEEP_MIN_AGE_S = 60
#: How long a run waits for the lock of a basetemp record another process left.
TAKE_OVER_WAIT_S = 5.0
#: How long removing a record on Windows tries again while another handle holds it open.
UNLINK_WAIT_S = 1.0

#: The suffix of an owner record, a sibling file of the path it holds.
RECORD_SUFFIX = ".mx-owner"
#: A short process dir's name.
PROCESS_DIR_NAME = re.compile(r"mx-[a-z0-9_]{6}")
#: A basetemp's name, as pytest numbers them.
BASETEMP_NAME = re.compile(r"pytest-[0-9]+")
#: An xdist worker's basetemp inside the controller's.
WORKER_DIR_NAME = re.compile(r"popen-gw[0-9]+")
#: Filesystems that keep files in memory only.
MEMORY_FILESYSTEMS = frozenset({"tmpfs", "ramfs"})

#: The record's `kit` field; a record without it is not this kit's.
KIT = "moxi-dev"

WINDOWS = os.name == "nt"
_NAME_CHARS = "abcdefghijklmnopqrstuvwxyz0123456789_"
_MIB = 1024 * 1024
_POLL_S = 0.05
_RAM_KEEPS = ("removed when the run ends, failed tests' files and browser traces "
              f"included ({TEMP_ENV}={DISK} keeps them)")


class TempError(Exception):
    """A setting the kit can't run with, or a record it can't hold: the run stops."""


# --- the places --------------------------------------------------------------------------

@dataclass(frozen=True)
class Places:
    """Everything outside the process that choosing and sweeping read.

    `platform` is `sys.platform`'s value. `disk_root` and `ram_root` are Linux's roots;
    `system_temp` is the system temp directory (pytest's own default root, and the
    fallback); `macos_short_root` is macOS's short root. `mountinfo`, `meminfo` and
    `cgroup` are the `/proc` files, `cgroup_root` the cgroup v2 mount. `statvfs` is
    `os.statvfs` or a stand-in."""

    platform: str
    disk_root: str = "/var/tmp"
    ram_root: str = "/dev/shm"
    system_temp: str = ""
    macos_short_root: str = "/tmp"
    mountinfo: str = "/proc/self/mountinfo"
    meminfo: str = "/proc/meminfo"
    cgroup: str = "/proc/self/cgroup"
    cgroup_root: str = "/sys/fs/cgroup"
    statvfs: Callable[[str], Any] | None = None

    @property
    def is_linux(self) -> bool:
        return self.platform.startswith("linux")


_PLACES: Places | None = None


def use_places(places: Places | None) -> None:
    """Read `places` from now on in this process, in place of the real ones; `None`
    goes back to the real ones."""
    global _PLACES
    _PLACES = places


def real_places() -> Places:
    """This system's places, the system temp directory read now."""
    return Places(platform=sys.platform, system_temp=tempfile.gettempdir(),
                  statvfs=getattr(os, "statvfs", None))


def places() -> Places:
    """The places in use: those `use_places` set, else the real ones."""
    return _PLACES if _PLACES is not None else real_places()


# --- what a path is on -------------------------------------------------------------------

def filesystem_type(path: str, where: Places) -> str | None:
    """The type of the filesystem `path` is on, from the mount table (Linux only;
    `None` elsewhere or when it can't be read)."""
    if not where.is_linux:
        return None
    try:
        lines = Path(where.mountinfo).read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    real, best, kind = os.path.realpath(path), "", None
    for line in lines:
        fields = line.split()
        if "-" not in fields or len(fields) < 5:
            continue
        point = fields[4].replace("\\040", " ")
        position = fields.index("-") + 1
        if position >= len(fields):
            continue
        if (real == point or real.startswith(point.rstrip("/") + "/")) \
                and len(point) >= len(best):
            best, kind = point, fields[position]
    return kind


def in_memory(path: str, where: Places) -> bool:
    """Whether `path` is on tmpfs or ramfs. Off Linux nothing is read and it is not."""
    return filesystem_type(path, where) in MEMORY_FILESYSTEMS


def _writable_dir(path: str) -> bool:
    return os.path.isdir(path) and os.access(path, os.W_OK)


def _same_path(one: str, other: str) -> bool:
    return os.path.realpath(one) == os.path.realpath(other)


# --- room for a ram run ------------------------------------------------------------------

def ram_need_mib(workers: int) -> int:
    """What a `ram` run of `workers` workers is taken to need, in MiB."""
    return TEMP_BASE_MIB + TEMP_PER_WORKER_MIB * max(1, workers)


def _mem_available_mib(where: Places) -> int | None:
    try:
        text = Path(where.meminfo).read_text(encoding="utf-8")
    except OSError:
        return None
    match = re.search(r"^MemAvailable:\s+([0-9]+)\s*kB", text, re.MULTILINE)
    return int(match.group(1)) // 1024 if match else None


def _read_limit(cgroup_dir: Path, name: str) -> int | None:
    try:
        raw = (cgroup_dir / name).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return int(raw) if raw.isdigit() else None


def cgroup_headroom_mib(where: Places) -> int | None:
    """The lowest headroom along this process's cgroup path, in MiB: at each cgroup from
    its own up to the root, `memory.high` (else `memory.max`) less `memory.current`,
    wherever one of the two is set. `None` with no limit on the path or no cgroup v2."""
    try:
        lines = Path(where.cgroup).read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    own = next((line[3:] for line in lines if line.startswith("0::")), None)
    if own is None:
        return None
    root = Path(where.cgroup_root)
    parts = [part for part in own.strip().split("/") if part]
    lowest: int | None = None
    for depth in range(len(parts), -1, -1):
        cgroup_dir = root.joinpath(*parts[:depth])
        limit = _read_limit(cgroup_dir, "memory.high")
        if limit is None:
            limit = _read_limit(cgroup_dir, "memory.max")
        if limit is None:
            continue
        current = _read_limit(cgroup_dir, "memory.current") or 0
        headroom = max(0, limit - current) // _MIB
        lowest = headroom if lowest is None else min(lowest, headroom)
    return lowest


def ram_room(workers: int, where: Places) -> str | None:
    """Why `where.ram_root` has no room for a `ram` run of `workers` workers, or `None`
    when it has: its free space, the machine's `MemAvailable` less `RAM_SPARE_MIB`, and
    the run's cgroup headroom must each cover `ram_need_mib(workers)`."""
    need = ram_need_mib(workers)
    if where.statvfs is not None:
        try:
            stats = where.statvfs(where.ram_root)
        except OSError:
            stats = None
        if stats is not None:
            free = stats.f_bavail * stats.f_frsize // _MIB
            if free < need:
                return (f"{where.ram_root} has {free} MiB free and this run needs about "
                        f"{need} MiB")
    available = _mem_available_mib(where)
    if available is not None and available < need + RAM_SPARE_MIB:
        return (f"the machine has {available} MiB available and this run needs about "
                f"{need} MiB, with {RAM_SPARE_MIB} MiB left to spare")
    headroom = cgroup_headroom_mib(where)
    if headroom is not None and headroom < need:
        return (f"this run's memory limit leaves {headroom} MiB and it needs about "
                f"{need} MiB")
    return None


# --- the choice --------------------------------------------------------------------------

#: How a run came by its root (`Choice.source`).
BY_BASETEMP = "basetemp"
BY_SETTING = "setting"
BY_TEMPROOT = "temproot"
BY_DEFAULT = "default"
BY_NESTING = "nested"


@dataclass(frozen=True)
class Choice:
    """Where a run keeps its temp, and why.

    `mode` is what the run runs as, `disk` or `ram`. `root` is pytest's root the kit
    puts in `PYTEST_DEBUG_TEMPROOT`, `None` when the run's root is left to pytest (a
    `--basetemp`, a nested run). `short_root` is where the run's short process dirs go
    (for a nested run, the outer test's short directory). `is_in_memory` is true when
    `root` is on tmpfs or ramfs, so the kit removes its temp at the run's end. `source`
    is one of the BY_ constants. `asked` is the setting's value, `None` when unset. `why`
    is why the run is not where it asked to be. `over` is a `PYTEST_DEBUG_TEMPROOT` the
    setting overrode. `basetemp` is the `--basetemp` given. `can_ram` is whether this system has a RAM filesystem
    to offer."""

    mode: str
    root: str | None
    short_root: str
    is_in_memory: bool
    source: str
    asked: str | None = None
    why: str | None = None
    over: str | None = None
    basetemp: str | None = None
    can_ram: bool = False

    @property
    def is_nested(self) -> bool:
        return self.source == BY_NESTING

    @property
    def sweeps(self) -> bool:
        """Whether the run sweeps: a top-level run not given `--basetemp`."""
        return self.source not in (BY_NESTING, BY_BASETEMP)

    def as_dict(self) -> dict[str, Any]:
        """The choice as plain values, for an xdist worker and the durations file."""
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Choice:
        names = {item.name for item in dataclasses.fields(cls)}
        return cls(**{key: value for key, value in data.items() if key in names})

    def header(self) -> str:
        """The run's header line (without its `moxi-dev temp: ` lead)."""
        if self.source == BY_NESTING:
            return "nested in a test; left to the outer run"
        if self.source == BY_BASETEMP:
            return f"--basetemp {self.basetemp}, left to pytest"
        over = f", over {TEMPROOT_ENV}={self.over}" if self.over else ""
        if self.mode == RAM:
            return f"{RAM}, {self.root}{over}; {_RAM_KEEPS}"
        line = f"{DISK}, {self.root}"
        if self.why:
            line += f"{over}: {self.why}"
        elif self.source == BY_SETTING:
            line += f" ({TEMP_ENV}={self.asked}){over}"
        elif self.source == BY_TEMPROOT:
            line += f" ({TEMPROOT_ENV})"
        elif self.can_ram:
            line += f" (the default; {TEMP_ENV}={RAM} keeps it in memory)"
        else:
            line += " (the default)"
        if self.is_in_memory:
            line += f"; it is in memory, so it is {_RAM_KEEPS}"
        return line


def read_setting(env: Mapping[str, str]) -> str | None:
    """The setting in `env`: `disk`, `ram`, or `None` when unset or empty. Any other
    value raises `TempError`."""
    raw = env.get(TEMP_ENV, "").strip()
    if not raw:
        return None
    if raw not in MODES:
        raise TempError(f"{TEMP_ENV} must be {DISK} or {RAM}; it is {raw!r}")
    return raw


def _default_disk(where: Places) -> tuple[str, str, str | None]:
    """The disk root, the short root and why the root isn't the usual one."""
    if not where.is_linux:
        short = (where.macos_short_root if where.platform == "darwin"
                 and _writable_dir(where.macos_short_root) else where.system_temp)
        return where.system_temp, short, None
    root = where.disk_root
    if not _writable_dir(root):
        why = f"{root} is not writable"
    elif in_memory(root, where):
        why = f"{root} is on {filesystem_type(root, where)}, not on disk"
    else:
        return root, root, None
    return where.system_temp, where.system_temp, why


def _is_default_root(value: str, where: Places) -> bool:
    usual = where.disk_root if where.is_linux else where.system_temp
    return _same_path(value, usual)


def choose(env: Mapping[str, str], *, basetemp: str | None = None, workers: int = 1,
           where: Places | None = None) -> Choice:
    """Where a top-level run whose controller started with `env` keeps its temp
    (module doc). `basetemp` is the `--basetemp` given to it; `workers` is its xdist
    worker count, 1 without xdist. A bad setting raises `TempError`."""
    where = where or places()
    asked = read_setting(env)
    root, short, why = _default_disk(where)
    can_ram = where.is_linux
    if basetemp is not None:
        if asked == RAM and can_ram and _ram_serves(where) is None:
            short = where.ram_root
        return Choice(mode=DISK, root=None, short_root=short, is_in_memory=False,
                      source=BY_BASETEMP, asked=asked, basetemp=str(basetemp),
                      can_ram=can_ram)
    given = env.get(TEMPROOT_ENV) or None
    if asked is not None:
        mode = DISK
        if asked == RAM:
            if not can_ram:
                why = "ram was asked, but this system has no RAM filesystem"
            else:
                unfit = _ram_serves(where) or ram_room(workers, where)
                if unfit is None:
                    mode, root, short, why = RAM, where.ram_root, where.ram_root, None
                else:
                    why = f"ram was asked, but {unfit}" + (f"; {why}" if why else "")
        over = (given if given and not _same_path(given, root)
                and not _is_default_root(given, where) else None)
        return Choice(mode=mode, root=root, short_root=short,
                      is_in_memory=in_memory(root, where), source=BY_SETTING,
                      asked=asked, why=why, over=over, can_ram=can_ram)
    if given and not _is_default_root(given, where):
        return Choice(mode=DISK, root=given, short_root=short,
                      is_in_memory=in_memory(given, where), source=BY_TEMPROOT,
                      can_ram=can_ram)
    return Choice(mode=DISK, root=root, short_root=short,
                  is_in_memory=in_memory(root, where), source=BY_DEFAULT, why=why,
                  can_ram=can_ram)


def _ram_serves(where: Places) -> str | None:
    """Why `where.ram_root` can't hold a run's temp, or `None` when it can."""
    root = where.ram_root
    if not os.path.isdir(root):
        return f"{root} is missing"
    kind = filesystem_type(root, where)
    if kind != "tmpfs":
        return f"{root} is not tmpfs" if kind is None else f"{root} is on {kind}, not tmpfs"
    if not os.access(root, os.W_OK):
        return f"{root} is not writable"
    return None


def nested(short_root: str) -> Choice:
    """The choice of a nested run, whose short dirs go in `short_root`, the outer
    test's short directory."""
    return Choice(mode=DISK, root=None, short_root=short_root, is_in_memory=False,
                  source=BY_NESTING)


def outer_short_dir(env: Mapping[str, str]) -> str | None:
    """The outer test's short directory the fence's mark in `env` names, if any."""
    return env.get(FENCED_ENV) or None


def given_root(env: Mapping[str, str]) -> str | None:
    """The `PYTEST_DEBUG_TEMPROOT` set in `env`, if any."""
    return env.get(TEMPROOT_ENV) or None


def put_root(choice: Choice, env: dict[str, str] | Any) -> None:
    """Put `choice`'s root in `env` for pytest, where it has one."""
    if choice.root is not None:
        env[TEMPROOT_ENV] = choice.root


def _fence_active() -> bool:
    testing = sys.modules.get("moxi_dev.testing")
    guard = getattr(testing, "_GUARD", None)
    return bool(getattr(guard, "active", False))


def top_level(env: Mapping[str, str], *, is_fenced: bool | None = None) -> bool:
    """Whether a run started with `env` in this process is top-level: no fence is active
    in this process (`is_fenced` says so in its stead when given) and `env` carries no
    `MOXI_TEST_FENCED` mark."""
    fenced = _fence_active() if is_fenced is None else is_fenced
    return not fenced and not env.get(FENCED_ENV)


# --- a check's environment ---------------------------------------------------------------

def for_check(env: Mapping[str, str], *,
              where: Places | None = None) -> tuple[dict[str, str], str | None]:
    """The environment a check's tests get from `env`, and the reason the check gives
    when it changed or kept back a memory choice: a check keeps its temp on disk.

    `ram` on Linux comes back as `disk`; `ram` elsewhere comes back unchanged, since
    each suite runs it as `disk` and says why; an unset setting with a
    `PYTEST_DEBUG_TEMPROOT` in memory comes back with `MOXI_TEST_TEMP=disk`, which
    overrides it. Anything else comes back unchanged with no reason. A bad setting
    raises `TempError`."""
    where = where or places()
    result = dict(env)
    asked = read_setting(env)
    if asked == RAM:
        if not where.is_linux:
            return result, "ram asked; no RAM filesystem on this system"
        result[TEMP_ENV] = DISK
        return result, "ram asked; a check keeps its temp on disk"
    given = env.get(TEMPROOT_ENV) or None
    if asked is None and given and in_memory(given, where):
        result[TEMP_ENV] = DISK
        return result, f"{TEMPROOT_ENV}={given} is in memory; a check keeps its temp on disk"
    return result, None


# --- owner records -----------------------------------------------------------------------

if WINDOWS:
    import msvcrt
else:
    import fcntl

#: Where the Windows lock sits in a record: one byte far past its JSON. A Windows lock
#: bars every other handle from the bytes it covers, so a lock on the JSON would leave
#: the record unreadable while its owner runs.
WINDOWS_LOCK_POSITION = 1 << 30


def _try_lock(fd: int) -> bool:
    """Take the exclusive lock on the record open at `fd` without waiting."""
    if WINDOWS:
        os.lseek(fd, WINDOWS_LOCK_POSITION, os.SEEK_SET)
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        return True
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return False
    return True


def _lock_within(fd: int, wait_s: float) -> bool:
    deadline = time.monotonic() + wait_s
    while not _try_lock(fd):
        if time.monotonic() >= deadline:
            return False
        time.sleep(_POLL_S)
    return True


def _drop_record(fd: int, record: Path) -> None:
    """Remove a record this process holds locked at `fd`: on POSIX unlink, then close;
    on Windows, which won't delete a file a process holds open, unlock and close, then
    unlink (`_unlink_once_closed`)."""
    if WINDOWS:
        with contextlib.suppress(OSError):
            os.lseek(fd, WINDOWS_LOCK_POSITION, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        with contextlib.suppress(OSError):
            os.close(fd)
        _unlink_once_closed(record)
        return
    with contextlib.suppress(OSError):
        os.unlink(record)
    with contextlib.suppress(OSError):
        os.close(fd)


def _unlink_once_closed(record: Path) -> None:
    """Unlink the record on Windows, trying again for up to `UNLINK_WAIT_S` while another
    handle holds it open (a sweeper testing its lock). A handle still open then is one
    that took the lock this process let go: a sweeper that removes the record itself, or
    an owner that holds it anew. A handle closed past the wait without removing it leaves
    a free record whose path is gone, which the next sweep removes."""
    deadline = time.monotonic() + UNLINK_WAIT_S
    while True:
        try:
            os.unlink(record)
        except PermissionError:
            if time.monotonic() >= deadline:
                return
            time.sleep(_POLL_S)
        except OSError:                           # gone already: another removed it
            return
        else:
            return


def _same_file(fd: int, record: Path) -> bool:
    try:
        on_disk = os.lstat(record)
    except OSError:
        return False
    mine = os.fstat(fd)
    return (mine.st_ino, mine.st_dev) == (on_disk.st_ino, on_disk.st_dev)


def record_of(path: Path) -> Path:
    """The owner record beside `path`."""
    return path.with_name(path.name + RECORD_SUFFIX)


def _body() -> bytes:
    made = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return json.dumps({"kit": KIT, "pid": os.getpid(), "made": made}).encode()


def _write_body(fd: int) -> None:
    """Write this process's JSON as the record's whole content."""
    body = _body()
    os.ftruncate(fd, 0)
    os.lseek(fd, 0, os.SEEK_SET)
    os.write(fd, body)


_OPEN_FLAGS = os.O_RDWR | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOINHERIT", 0) \
    | getattr(os, "O_BINARY", 0)


@dataclass
class Held:
    """A path this process owns, with its record open and locked."""

    path: Path
    record: Path
    fd: int
    pid: int = field(default_factory=os.getpid)

    def release(self, *, remove: bool = True) -> None:
        """Remove the path (when `remove`) and then the record, the record last. A
        process that didn't take it (a forked child) does nothing."""
        if self.pid != os.getpid() or self.fd < 0:
            return
        _HELD.pop(id(self), None)
        if remove:
            shutil.rmtree(self.path, ignore_errors=True)
        _drop_record(self.fd, self.record)
        self.fd = -1


#: Every record this process holds, so a child forked without exec closes its copies.
_HELD: dict[int, Held] = {}


def _close_in_child() -> None:
    for held in list(_HELD.values()):
        with contextlib.suppress(OSError):
            os.close(held.fd)
        held.fd = -1
    _HELD.clear()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_close_in_child)


def _keep(held: Held) -> Held:
    _HELD[id(held)] = held
    return held


def _create_record(record: Path) -> int | None:
    """Create the record anew and lock it; `None` when the name is taken."""
    try:
        fd = os.open(record, _OPEN_FLAGS | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return None
    if not _lock_within(fd, TAKE_OVER_WAIT_S):    # a sweeper holds it for a moment
        os.close(fd)
        return None
    return fd


def make_process_dir(short_root: str | Path) -> Held:
    """Make a short process dir, `mx-` and 6 of `[a-z0-9_]`, under `short_root`, its
    record written and locked before the directory is made."""
    root = Path(short_root)
    while True:
        name = "mx-" + "".join(secrets.choice(_NAME_CHARS) for _ in range(6))
        path = root / name
        record = record_of(path)
        fd = _create_record(record)
        if fd is None:
            continue
        if not _same_file(fd, record):           # a sweeper took it between: a new name
            os.close(fd)
            continue
        _write_body(fd)
        try:
            path.mkdir(mode=0o700)
        except FileExistsError:                   # a path with no record of ours
            _drop_record(fd, record)
            continue
        except OSError:
            _drop_record(fd, record)
            raise
        return _keep(Held(path=path, record=record, fd=fd))


def hold(path: str | Path) -> Held:
    """Write and lock the record of `path`, a basetemp or a worker's `popen-gw<K>`
    that pytest has just made. A record already there under the name, left by a run
    that died, is taken over once its lock is free; one still held after
    `TAKE_OVER_WAIT_S` raises `TempError`."""
    path = Path(path)
    record = record_of(path)
    while True:
        fd = _create_record(record)
        if fd is None:
            try:
                fd = os.open(record, _OPEN_FLAGS)
            except FileNotFoundError:              # a sweeper removed it: create anew
                continue
            if not _lock_within(fd, TAKE_OVER_WAIT_S):
                os.close(fd)
                raise TempError(f"{record} is held by a live process, though {path} is "
                                "this run's; remove the record once that process ends")
        if not _same_file(fd, record):
            os.close(fd)
            continue
        _write_body(fd)
        return _keep(Held(path=path, record=record, fd=fd))


# --- the sweep ---------------------------------------------------------------------------

@dataclass
class Swept:
    """What a sweep removed: how many directories, and their size in bytes."""

    count: int = 0
    size: int = 0

    def line(self) -> str | None:
        """The line a run prints for it (without its lead), `None` when it removed
        nothing."""
        if not self.count:
            return None
        noun = "dir" if self.count == 1 else "dirs"
        return (f"removed {self.count} test temp {noun} ({_size(self.size)}) left by "
                "runs that ended without cleaning up")


def _size(size: int) -> str:
    if size >= 1024 * _MIB:
        return f"{size / (1024 * _MIB):.1f} GiB"
    return f"{size / _MIB:.0f} MiB" if size >= _MIB else f"{size // 1024} KiB"


def _disk_usage(path: Path) -> int:
    total = 0
    for top, _dirs, files in os.walk(path):
        for name in (*files, *_dirs):
            with contextlib.suppress(OSError):
                stats = os.lstat(os.path.join(top, name))
                total += stats.st_blocks * 512 if hasattr(stats, "st_blocks") \
                    else stats.st_size
    return total


def _mine(stats: os.stat_result) -> bool:
    return WINDOWS or stats.st_uid == os.getuid()


def _open_free(record: Path) -> int | None:
    """Open the record, a regular file of this user's, and take its lock without waiting;
    `None` when it isn't one or its owner still holds it."""
    try:
        stats = os.lstat(record)
    except OSError:
        return None
    if not stat.S_ISREG(stats.st_mode) or not _mine(stats):
        return None
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0) \
        | getattr(os, "O_NOINHERIT", 0) | getattr(os, "O_BINARY", 0)
    try:
        fd = os.open(record, flags)
    except OSError:
        return None
    if not _try_lock(fd) or not _same_file(fd, record):
        os.close(fd)
        return None
    return fd


def _is_kits(fd: int) -> bool:
    try:
        os.lseek(fd, 0, os.SEEK_SET)
        raw = b""
        while chunk := os.read(fd, 65536):
            raw += chunk
        data = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return False
    return isinstance(data, dict) and data.get("kit") == KIT


def _old_dir(path: Path, now: float) -> bool:
    """Whether `path` is a directory of this user's, not a symlink, whose own mtime is
    over `SWEEP_MIN_AGE_S` old."""
    try:
        stats = os.lstat(path)
    except OSError:
        return False
    return (stat.S_ISDIR(stats.st_mode) and _mine(stats)
            and now - stats.st_mtime > SWEEP_MIN_AGE_S)


def _sweep_one(path: Path, now: float, swept: Swept, *, has_workers: bool) -> None:
    record = record_of(path)
    if not _old_dir(path, now):
        return
    fd = _open_free(record)
    if fd is None:
        return
    if not os.path.lexists(path):         # another removed it first: the record is free
        _drop_record(fd, record)          # and its path gone, so it goes, uncounted
        return
    workers: list[tuple[int, Path]] = []
    removed = False
    try:
        if not _is_kits(fd):
            return
        if has_workers:
            for inner in sorted(path.glob("popen-gw*" + RECORD_SUFFIX)):
                if not WORKER_DIR_NAME.fullmatch(inner.name[:-len(RECORD_SUFFIX)]):
                    continue
                inner_fd = _open_free(inner)
                if inner_fd is None:
                    return                         # a worker still holds the basetemp
                workers.append((inner_fd, inner))
        size = _disk_usage(path)
        while workers:
            _drop_record(*workers.pop())
        shutil.rmtree(path, ignore_errors=True)
        removed = not os.path.lexists(path)
        if removed:
            swept.count += 1
            swept.size += size
    finally:
        for inner_fd, _inner in workers:
            with contextlib.suppress(OSError):
                os.close(inner_fd)
        if removed:
            _drop_record(fd, record)
        else:
            with contextlib.suppress(OSError):
                os.close(fd)


def _sweep_orphan(record: Path) -> None:
    """Remove the record when the path it held is gone and its lock is free, parsed or
    not."""
    path = record.with_name(record.name[:-len(RECORD_SUFFIX)])
    if os.path.lexists(path):
        return
    fd = _open_free(record)
    if fd is None:
        return
    if os.path.lexists(path):
        os.close(fd)
        return
    _drop_record(fd, record)


def _sweep_dir(parent: Path, shape: re.Pattern[str], now: float, swept: Swept, *,
               has_workers: bool) -> None:
    try:
        names = os.listdir(parent)
    except OSError:
        return
    for name in sorted(names):
        if name.endswith(RECORD_SUFFIX):
            if shape.fullmatch(name[:-len(RECORD_SUFFIX)]):
                _sweep_orphan(parent / name)
        elif shape.fullmatch(name):
            _sweep_one(parent / name, now, swept, has_workers=has_workers)


def short_roots(where: Places) -> list[str]:
    """The short roots a sweep looks in on this system."""
    if where.is_linux:
        found = [where.disk_root, where.ram_root, where.system_temp]
    elif where.platform == "darwin":
        found = [where.macos_short_root]
    else:
        found = [where.system_temp]
    return _distinct(found)


def basetemp_parents(where: Places, own_root: str | None = None) -> list[str]:
    """The `pytest-of-<user>` directories a sweep looks in for memory basetemps: under
    `/dev/shm` (Linux), under the system temp directory when that is in memory, and
    under `own_root`, the sweeping run's own root when it is in memory."""
    roots = [where.ram_root] if where.is_linux else []
    if in_memory(where.system_temp, where):
        roots.append(where.system_temp)
    if own_root and in_memory(own_root, where):
        roots.append(own_root)
    user = _user()
    return _distinct([os.path.join(root, f"pytest-of-{user}") for root in roots])


def _user() -> str:
    try:
        return getpass.getuser()
    except (ImportError, KeyError, OSError):
        return "unknown"


def _distinct(paths: list[str]) -> list[str]:
    seen: list[str] = []
    for path in paths:
        if path and not any(_same_path(path, other) for other in seen):
            seen.append(path)
    return seen


def sweep(where: Places | None = None, *, own_root: str | None = None,
          now: float | None = None) -> Swept:
    """Remove what runs of this kit left whose owners are gone (module doc), in the
    places `where` names. With no `where` it sweeps the places in use, and only in a
    top-level run (`top_level`); a test hands it places of its own. `own_root` is the
    sweeping run's own root; `now` is the time to judge ages by."""
    if where is None:
        if not top_level(os.environ):
            return Swept()
        where = places()
    moment = time.time() if now is None else now
    swept = Swept()
    for root in short_roots(where):
        _sweep_dir(Path(root), PROCESS_DIR_NAME, moment, swept, has_workers=False)
    for parent in basetemp_parents(where, own_root):
        _sweep_dir(Path(parent), BASETEMP_NAME, moment, swept, has_workers=True)
    return swept


def held_records() -> Iterator[Held]:
    """The records this process holds now."""
    return iter(list(_HELD.values()))

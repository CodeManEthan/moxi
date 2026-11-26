"""The memory scope a check runs in: one `systemd-run --user --scope` per run, capped.

`moxi-dev check` (any form but `--only`) and `moxi-dev cap` run their work in a transient
scope of the user manager, so a runaway is throttled at MemoryHigh, then ended, and the
rest of the machine goes on. Two processes take part:

- **The outer** is the process the caller started. It reads the figures and the skip
  rules, starts the scope with `systemd-run`, then watches the scope's cgroup once a
  second until the work ends (`run`). Its own memory is not counted against the cap.
- **The inner** is the work itself, started in the scope with `MOXI_DEV_SCOPE` naming
  the unit. A `moxi-dev check` that finds that variable set counts as an inner only when
  `/proc/self/cgroup` names the same unit (`inner_unit`); a marker that doesn't match is
  dropped and the check acts as an outer.

The figures. By default MemoryHigh is `BASE_MIB + PER_WORKER_MIB × N`, N the check's
worker count, and MemoryMax is MemoryHigh plus `ROOM_MIB`. `MOXI_TEST_MEMORY_HIGH_MIB`
and `MOXI_TEST_MEMORY_MAX_MIB`, whole numbers of MiB of at least 64, each replace its
default; a MemoryHigh not below the MemoryMax, or any other value, stops the run with
exit 2. `MOXI_TEST_MEMORY_MAX_MIB=none` runs with no scope. The figures are counted in
U, the scope's unreclaimable charge: `anon + shmem + kernel - slab_reclaimable` from its
`memory.stat`, never below 0.

No scope is made, and the work runs in place, where (the first that holds gives the
reason) `MOXI_TEST_MEMORY_MAX_MIB` is `none`; this is not Linux; `CI` is set and is not
`false` or `0`; `systemd-run` or `systemctl` is not on PATH; the user manager can't be
reached or its version, or the client's, can't be read; or the user manager's cgroup
does not delegate the memory controller (`skip_reason`). Past those, a scope that can't
be made exits 2 and never falls back to an uncapped run.

The scope's properties follow what the systemd in use understands: MemoryMax and
`MemorySwapMax=0` always; MemoryHigh where the user manager's `memory.stat` has the four
lines U is made of; `--expand-environment=no` where the `systemd-run` client is 254 or
later; `OOMPolicy=kill` where the user manager is 253 or later. Below 253 the outer ends
the whole scope itself when its `oom_kill` count rises.

The end of a runaway. The line is 95 % of MemoryHigh. The outer ends the scope with
`systemctl --user kill --signal=SIGKILL` when U was at or above the line in each of the
last `LINE_HOLD_S` samples and the `memory.events` `high` count rose over them, and it
prints why before the kill. Pressure, the `high` count and `memory.current` alone never
end a run. A run that reaches MemoryMax first is killed by the kernel, and the outer
reads the unit's `Result` and names it. A caller's SIGINT, SIGTERM or SIGHUP prints the
scope's memory state at once, queues the scope's stop (at once for SIGTERM and SIGHUP,
20 s later for SIGINT), and the outer then ends by the same signal.
"""

from __future__ import annotations

import os
import re
import secrets
import select
import shutil
import signal
import subprocess
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Mapping, Protocol, Sequence

#: The default figures, in MiB. Measured as the largest unreclaimable charge (U, below)
#: of an unlimited full check sampled once a second, at 8 and at 16 workers; MemoryHigh at
#: each count is 1.2 × that, the line through the two unrounded points is fitted once, and
#: its slope (`PER_WORKER_MIB`) and intercept (`BASE_MIB`) are each rounded up to 64 MiB
#: from the fit. `ROOM_MIB` is MemoryMax's room above MemoryHigh for page cache.
PER_WORKER_MIB = 512
BASE_MIB = 2496
ROOM_MIB = 1536
#: The smallest figure a variable may set.
MIN_MIB = 64

#: The line, as a share of MemoryHigh: U held at or above it at MemoryHigh is a runaway.
LINE_SHARE = 0.95
#: How long U must stay at or above the line, in one-second samples, before the outer
#: ends the scope.
LINE_HOLD_S = 5
#: A run that held this share of its line or more prints a warning before its summary.
NEAR_SHARE = 0.9
#: A run that waited on memory this long or longer prints a warning before its summary.
STALL_WARN_S = 10.0
#: How long the inner and the cap's outer keep killing leftover processes.
LEFTOVER_S = 5.0
#: How long after a caller's SIGINT the outer waits before it queues the scope's stop.
INT_GRACE_S = 20.0
#: How long the outer waits for its unit to settle: a killed one before it reads its
#: Result, and any one, gone, before it sweeps.
SETTLE_S = 5.0
#: The `ActiveState`s of a unit that still holds, or may still hold, a process.
LIVE_STATES = ("active", "activating", "deactivating", "reloading", "refreshing")
#: The stopped line's stall figure covers this many of the latest seconds.
STALL_WINDOW_S = 60

MEMORY_HIGH_ENV = "MOXI_TEST_MEMORY_HIGH_MIB"
MEMORY_MAX_ENV = "MOXI_TEST_MEMORY_MAX_MIB"
#: The scope's unit, set for the work the scope runs.
SCOPE_ENV = "MOXI_DEV_SCOPE"  # noqa: MX2 -- moxi-dev's own scope marker; no other package reads it
WORKERS_ENV = "MOXI_TEST_WORKERS"

CGROUP_ROOT = Path("/sys/fs/cgroup")
PROC_CGROUP = Path("/proc/self/cgroup")

#: The four `memory.stat` lines U is made of.
U_KEYS = ("anon", "shmem", "kernel", "slab_reclaimable")

#: Run in the scope before the work: writes one byte to the descriptor its first
#: argument names, closes it, then becomes the rest of its arguments (exit 127 when it
#: can't). Run with `-I`, so nothing is imported from the working directory.
STARTED = """\
import os, sys
fd = int(sys.argv[1])
os.write(fd, b"x")
os.close(fd)
try:
    os.execvp(sys.argv[2], sys.argv[2:])
except OSError as exc:
    print(f"{sys.argv[2]}: {exc.strerror}", file=sys.stderr)
    sys.exit(127)
"""

MIB = 1 << 20
GIB = 1 << 30


class ScopeError(Exception):
    """The run can't start: a bad figure; the message says why."""


def gib(n_bytes: float) -> str:
    """A size in GiB with one decimal, as the lines print it: `6.4G`."""
    return f"{n_bytes / GIB:.1f}G"


# --- the figures -----------------------------------------------------------------------

def default_high_mib(workers: int) -> int:
    """The default MemoryHigh for `workers` workers, in MiB."""
    return BASE_MIB + PER_WORKER_MIB * workers


def default_max_mib(workers: int) -> int:
    """The default MemoryMax for `workers` workers, in MiB."""
    return default_high_mib(workers) + ROOM_MIB


@dataclass(frozen=True)
class Figures:
    """The scope's MemoryHigh and MemoryMax, and where each came from."""
    workers: int
    high_mib: int
    max_mib: int
    high_set: bool = False      # MOXI_TEST_MEMORY_HIGH_MIB gave it
    max_set: bool = False       # MOXI_TEST_MEMORY_MAX_MIB gave it

    @property
    def high_bytes(self) -> int:
        return self.high_mib * MIB

    @property
    def max_bytes(self) -> int:
        return self.max_mib * MIB

    @property
    def line_bytes(self) -> float:
        """The line, 95 % of MemoryHigh, unrounded."""
        return self.high_bytes * LINE_SHARE

    def source(self) -> str:
        """Where the figures came from, as the end lines name it."""
        named = [name for name, given in ((MEMORY_HIGH_ENV, self.high_set),
                                          (MEMORY_MAX_ENV, self.max_set)) if given]
        if not named:
            return self.default_source()
        return "set by " + " and ".join(named)

    def default_source(self) -> str:
        """`the default for 8 workers`."""
        return f"the default for {self.workers} worker{'s' * (self.workers != 1)}"


def _mib(env: Mapping[str, str], name: str) -> int | None:
    raw = env.get(name, "").strip()
    if not raw:
        return None
    if not raw.isdigit() or int(raw) < MIN_MIB:
        raise ScopeError(f"{name} must be a whole number of MiB, {MIN_MIB} or more"
                         f"{', or none' if name == MEMORY_MAX_ENV else ''}; it is {raw!r}")
    return int(raw)


def figures(env: Mapping[str, str], workers: int) -> Figures | None:
    """The scope's figures for a run at `workers` workers; None when
    `MOXI_TEST_MEMORY_MAX_MIB` is `none`. ScopeError for a bad value, or a MemoryHigh not
    below the MemoryMax."""
    if env.get(MEMORY_MAX_ENV, "").strip() == "none":
        return None
    high, top = _mib(env, MEMORY_HIGH_ENV), _mib(env, MEMORY_MAX_ENV)
    found = Figures(workers=workers,
                    high_mib=default_high_mib(workers) if high is None else high,
                    max_mib=default_max_mib(workers) if top is None else top,
                    high_set=high is not None, max_set=top is not None)
    if found.high_mib >= found.max_mib:
        high_from = f"set by {MEMORY_HIGH_ENV}" if found.high_set \
            else found.default_source()
        max_from = f"set by {MEMORY_MAX_ENV}" if found.max_set else found.default_source()
        raise ScopeError(f"MemoryHigh {found.high_mib} MiB ({high_from}) is not below "
                         f"MemoryMax {found.max_mib} MiB ({max_from}); set "
                         f"{MEMORY_HIGH_ENV} below {MEMORY_MAX_ENV}")
    return found


def below_default(found: Figures) -> str | None:
    """The warning for a set MemoryMax below the default for its worker count, else
    None."""
    default = default_max_mib(found.workers)
    if not found.max_set or found.max_mib >= default:
        return None
    workers = f"{found.workers} worker{'s' * (found.workers != 1)}"
    return (f"{MEMORY_MAX_ENV} {found.max_mib} is below the {default} MiB the suite needs "
            f"at {workers}; expect a kill, or lower {WORKERS_ENV}")


# --- the host: skip rules and versions -------------------------------------------------

# Runs a command and returns its exit code and stdout; tests replace it.
Output = Callable[[Sequence[str]], tuple[int, str]]


def _output(env: Mapping[str, str]) -> Output:
    def output(argv: Sequence[str]) -> tuple[int, str]:
        try:
            done = subprocess.run(list(argv), env=dict(env), capture_output=True,
                                  text=True, timeout=30)
        except (OSError, subprocess.SubprocessError):
            return 127, ""
        return done.returncode, done.stdout
    return output


@dataclass(frozen=True)
class Host:
    """What the systemd in use understands."""
    client: int                 # the systemd-run client's version
    manager: int                # the user manager's version
    line_figures: bool          # the user manager's memory.stat has the four U lines

    @property
    def expands(self) -> bool:
        """Whether `--expand-environment=no` is understood (client 254 or later)."""
        return self.client >= 254

    @property
    def oom_policy(self) -> bool:
        """Whether a scope takes `OOMPolicy=` (manager 253 or later)."""
        return self.manager >= 253

    @property
    def memory_peak(self) -> bool:
        """Whether the manager reports a unit's `MemoryPeak` (255 or later)."""
        return self.manager >= 255


@dataclass
class System:
    """The calls the scope makes to the machine; tests replace them."""
    env: Mapping[str, str]
    platform: str = sys.platform
    output: Output | None = None
    which: Callable[[str], str | None] | None = None
    cgroup_root: Path = CGROUP_ROOT
    proc_cgroup: Path = PROC_CGROUP

    def __post_init__(self) -> None:
        if self.output is None:
            self.output = _output(self.env)
        if self.which is None:
            path = self.env.get("PATH")
            self.which = lambda name: shutil.which(name, path=path)

    def systemctl(self, *args: str) -> tuple[int, str]:
        return self.output(["systemctl", "--user", *args])

    def cgroup_dir(self, control_group: str) -> Path:
        """The directory of a cgroup systemd names by its path (`/user.slice/...`)."""
        return self.cgroup_root / control_group.strip().lstrip("/")


def _is_ci(env: Mapping[str, str]) -> bool:
    value = env.get("CI", "").strip().lower()
    return bool(value) and value not in ("false", "0")


def read_stat(path: Path) -> dict[str, int]:
    """A `memory.stat` file's lines as numbers; OSError when it can't be read."""
    stat = {}
    for line in path.read_text().splitlines():
        key, _, value = line.partition(" ")
        if value.strip().isdigit():
            stat[key] = int(value)
    return stat


def find_host(system: System) -> Host | str:
    """The host's systemd, or the reason no scope is made there (the skip rules after
    `none`, in order)."""
    if not system.platform.startswith("linux"):
        return "not Linux"
    if _is_ci(system.env):
        return "CI"
    if not (system.which("systemd-run") and system.which("systemctl")):
        return "no systemd-run"
    code, said = system.systemctl("show", "--property=Version", "--value")
    if code != 0:
        return "no systemd user manager"
    manager = re.match(r"\s*(\d+)", said)
    code, said = system.output(["systemd-run", "--version"])
    first = said.splitlines()[0] if code == 0 and said.strip() else ""
    client = re.match(r"systemd (\d+)", first)
    if not (manager and client):
        return "systemd version unreadable"
    code, group = system.systemctl("show", "--property=ControlGroup", "--value")
    manager_dir = system.cgroup_dir(group) if code == 0 and group.strip() else None
    try:
        delegated = (manager_dir is not None and "memory" in
                     (manager_dir / "cgroup.subtree_control").read_text().split())
    except OSError:
        delegated = False
    if not delegated:
        return "the user manager has no memory controller"
    try:
        line_figures = all(key in read_stat(manager_dir / "memory.stat") for key in U_KEYS)
    except OSError:
        line_figures = False
    return Host(client=int(client.group(1)), manager=int(manager.group(1)),
                line_figures=line_figures)


def skip_reason(found: Figures | None, host: Host | str) -> str | None:
    """Why no scope is made, or None when one is."""
    if found is None:
        return f"{MEMORY_MAX_ENV} is none"
    return host if isinstance(host, str) else None


# --- the scope's argv ------------------------------------------------------------------

def new_unit(kind: str) -> str:
    """A unit name unique to this run: `moxi-dev-<kind>-<12 hex>.scope`."""
    return f"moxi-dev-{kind}-{secrets.token_hex(6)}.scope"


def scope_argv(unit: str, host: Host, found: Figures, description: str,
               command: Sequence[str], started_fd: int,
               python: str = sys.executable) -> list[str]:
    """The `systemd-run` command that makes the scope and runs `command` in it, after
    the started byte is written to `started_fd`."""
    argv = ["systemd-run", "--user", "--scope", "--quiet"]
    if host.expands:
        argv.append("--expand-environment=no")
    argv += [f"--unit={unit}", f"--description={description}"]
    if host.line_figures:
        argv += ["-p", f"MemoryHigh={found.high_mib}M"]
    argv += ["-p", f"MemoryMax={found.max_mib}M", "-p", "MemorySwapMax=0"]
    if host.oom_policy:
        argv += ["-p", "OOMPolicy=kill"]
    return [*argv, "--", python, "-I", "-c", STARTED, str(started_fd), *command]


# --- reading a scope's cgroup ----------------------------------------------------------

def unreclaimable(stat: Mapping[str, int]) -> int | None:
    """U: `anon + shmem + kernel - slab_reclaimable`, never below 0; None when a line is
    missing."""
    if not all(key in stat for key in U_KEYS):
        return None
    return max(0, stat["anon"] + stat["shmem"] + stat["kernel"] - stat["slab_reclaimable"])


def _number(path: Path) -> int | None:
    """A cgroup file holding one number; None where it is missing or reads `max`."""
    try:
        text = path.read_text().strip()
    except FileNotFoundError:
        return None
    return int(text) if text.isdigit() else None


def _pressure_us(path: Path) -> int | None:
    """The `some` total of a `memory.pressure` file, in microseconds; None where it is
    missing."""
    try:
        text = path.read_text()
    except FileNotFoundError:
        return None
    found = re.search(r"^some .*\btotal=(\d+)", text, re.MULTILINE)
    return int(found.group(1)) if found else None


@dataclass(frozen=True)
class Sample:
    """One read of a scope's cgroup."""
    held: int | None             # U in bytes; None where memory.stat lacks a U line
    high_events: int = 0         # memory.events `high`
    oom_kills: int = 0           # memory.events `oom_kill`
    current: int | None = None   # memory.current
    peak: int | None = None      # memory.peak; None before kernel 5.19
    stalled_us: int | None = None  # memory.pressure `some` total; None where it is missing


def read_sample(cgroup_dir: Path) -> Sample:
    """Read the scope's cgroup once; OSError when its files are gone (the scope ended)."""
    stat = read_stat(cgroup_dir / "memory.stat")
    events = read_stat(cgroup_dir / "memory.events")
    return Sample(held=unreclaimable(stat), high_events=events.get("high", 0),
                  oom_kills=events.get("oom_kill", 0),
                  current=_number(cgroup_dir / "memory.current"),
                  peak=_number(cgroup_dir / "memory.peak"),
                  stalled_us=_pressure_us(cgroup_dir / "memory.pressure"))


class Watch:
    """The outer's rule over the samples, one a second.

    `add` answers `line` when U was at or above the line in each of the last
    `LINE_HOLD_S` samples and the `high` count rose over them, `oom` when the outer must
    finish a kernel memory kill itself (`group_kill`, below systemd 253) and the
    `oom_kill` count is above 0, and None otherwise."""

    def __init__(self, line_bytes: float | None, *, group_kill: bool = False):
        self.line_bytes = line_bytes
        self.group_kill = group_kill
        self.samples: deque[Sample] = deque(maxlen=STALL_WINDOW_S + 1)

    def add(self, sample: Sample) -> str | None:
        self.samples.append(sample)
        if self.group_kill and sample.oom_kills > 0:
            return "oom"
        if self.line_bytes is None or len(self.samples) < LINE_HOLD_S:
            return None
        last = list(self.samples)[-LINE_HOLD_S:]
        if all(one.held is not None and one.held >= self.line_bytes for one in last) \
                and last[-1].high_events > last[0].high_events:
            return "line"
        return None

    @property
    def latest(self) -> Sample | None:
        return self.samples[-1] if self.samples else None

    def most(self, name: str) -> int | None:
        values = [getattr(one, name) for one in self.samples
                  if getattr(one, name) is not None]
        return max(values) if values else None

    def stalled_lately(self) -> tuple[float, int] | None:
        """The seconds stalled over the latest samples, and how many seconds they span."""
        read = [one.stalled_us for one in self.samples if one.stalled_us is not None]
        if not read:
            return None
        return (read[-1] - read[0]) / 1e6, len(read) - 1


# The held sampler and the inner's own reads ------------------------------------------

def own_cgroup(proc_cgroup: Path = PROC_CGROUP, cgroup_root: Path = CGROUP_ROOT
               ) -> Path | None:
    """This process's cgroup v2 directory, from `/proc/self/cgroup`; None where it can't
    be read."""
    try:
        lines = proc_cgroup.read_text().splitlines()
    except OSError:
        return None
    for line in lines:
        if line.startswith("0::"):
            return cgroup_root / line[3:].strip().lstrip("/")
    return None


def inner_unit(env: Mapping[str, str], proc_cgroup: Path = PROC_CGROUP) -> str | None:
    """The scope this process runs in as an inner: `MOXI_DEV_SCOPE`'s unit when the last
    part of this process's cgroup is that unit, else None."""
    unit = env.get(SCOPE_ENV, "").strip()
    if not unit:
        return None
    here = own_cgroup(proc_cgroup, Path("/"))
    return unit if here is not None and here.name == unit else None


class Sampler:
    """Reads a cgroup's U once a second from a thread of its own and keeps the largest."""

    def __init__(self, cgroup_dir: Path | None, interval_s: float = 1.0):
        self.cgroup_dir = cgroup_dir
        self.interval_s = interval_s
        self.held: int | None = None
        self._done = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="moxi-dev-held",
                                        daemon=True)

    def _read(self) -> None:
        if self.cgroup_dir is None:
            return
        try:
            held = unreclaimable(read_stat(self.cgroup_dir / "memory.stat"))
        except OSError:
            return
        if held is not None and (self.held is None or held > self.held):
            self.held = held

    def _loop(self) -> None:
        while not self._done.is_set():
            self._read()
            self._done.wait(self.interval_s)

    def start(self) -> Sampler:
        self._thread.start()
        return self

    def stop(self) -> int | None:
        """Stop sampling, after one last read; the largest U seen, or None."""
        self._done.set()
        if self._thread.is_alive():
            self._thread.join()
        self._read()
        return self.held


def end_leftovers(cgroup_dir: Path, keep: int | None = None,
                  timeout_s: float = LEFTOVER_S,
                  kill: Callable[[int], None] | None = None,
                  clock: Callable[[], float] = time.monotonic,
                  pause: Callable[[float], None] = time.sleep) -> int:
    """SIGKILL every process in the cgroup but `keep`, again until none is left or
    `timeout_s` passes; how many it ended."""
    kill = kill or (lambda pid: os.kill(pid, signal.SIGKILL))
    ended: set[int] = set()
    deadline = clock() + timeout_s
    while True:
        try:
            pids = {int(line) for line in
                    (cgroup_dir / "cgroup.procs").read_text().split() if line.isdigit()}
        except OSError:
            break
        pids.discard(keep)
        if not pids or clock() >= deadline:
            break
        for pid in pids:
            try:
                kill(pid)
            except ProcessLookupError:
                continue
            ended.add(pid)
        pause(0.05)
    return len(ended)


def ended_line(count: int, what: str) -> str:
    """`ended 2 processes the check left running`."""
    return f"ended {count} process{'es' * (count != 1)} the {what} left running"


@dataclass(frozen=True)
class Report:
    """The memory field of a finished run and the warning lines that go before it."""
    field: str
    lines: list[str]


def memory_report(cgroup_dir: Path | None, held: int | None) -> Report:
    """The inner's memory field from its own cgroup at the end of the run: peak, held,
    line, high, max and stalled, with the near-line and stall warnings."""
    if cgroup_dir is None:
        return Report("unknown (no cgroup)", [])
    peak = _number(cgroup_dir / "memory.peak")
    high = _number(cgroup_dir / "memory.high")
    top = _number(cgroup_dir / "memory.max")
    stalled_us = _pressure_us(cgroup_dir / "memory.pressure")
    stalled = None if stalled_us is None else stalled_us / 1e6
    return describe(peak=peak, held=held, high=high, top=top, stalled_s=stalled)


def describe(*, peak: int | None, held: int | None, high: int | None, top: int | None,
             stalled_s: float | None) -> Report:
    """A finished run's memory field and warnings from its figures, in bytes and seconds;
    `high` None where the scope has no MemoryHigh."""
    parts = [f"peak {gib(peak)}" if peak is not None else "peak unknown"]
    lines = []
    if high is None:
        parts.append("high not set (no kernel memory figures)")
    else:
        line = high * LINE_SHARE
        parts += [f"held {gib(held)}" if held is not None else "held unknown",
                  f"line {gib(line)}", f"high {gib(high)}"]
        if held is not None and held >= NEAR_SHARE * line:
            lines.append(f"held {gib(held)} it can't give back, within a tenth of its line "
                         f"{gib(line)}; the suite has outgrown the cap's default: a higher "
                         f"{MEMORY_HIGH_ENV} or fewer workers keeps it from being ended")
    parts.append(f"max {gib(top)}" if top is not None else "max not set")
    parts.append(f"stalled {stalled_s:.1f}s" if stalled_s is not None else "stalled unknown")
    if stalled_s is not None and stalled_s >= STALL_WARN_S:
        about = f"MemoryHigh {gib(high)}, " if high is not None else ""
        about += f"peak {gib(peak)}" if peak is not None else "peak unknown"
        lines.append(f"the check waited on memory for {stalled_s:.1f}s ({about}); fewer "
                     f"workers or a higher {MEMORY_HIGH_ENV} would speed it up")
    return Report(", ".join(parts), lines)


# --- the outer -------------------------------------------------------------------------

class Child(Protocol):
    pid: int
    returncode: int | None

    def poll(self) -> int | None: ...
    def wait(self, timeout: float | None = None) -> int: ...


# Starts systemd-run with `env`, handing it the started descriptor; tests replace it.
Spawn = Callable[[list[str], Mapping[str, str], int], Child]


def _spawn(argv: list[str], env: Mapping[str, str], started_fd: int) -> Child:
    return subprocess.Popen(argv, env=dict(env), pass_fds=(started_fd,))


class Waiter:
    """Waits up to a time for a caller's signal or a ready descriptor.

    The real one catches SIGINT, SIGTERM and SIGHUP (each the platform has) while it is
    open and wakes through `signal.set_wakeup_fd`, so a signal is seen at once."""

    SIGNALS = tuple(getattr(signal, name) for name in ("SIGINT", "SIGTERM", "SIGHUP")
                    if hasattr(signal, name))

    def __init__(self) -> None:
        self.caught: list[int] = []
        self._wake_r, self._wake_w = os.pipe()
        os.set_blocking(self._wake_w, False)
        os.set_blocking(self._wake_r, False)
        self._old_fd = signal.set_wakeup_fd(self._wake_w)
        self._old = {sig: signal.signal(sig, self._caught) for sig in self.SIGNALS}

    def _caught(self, signum: int, frame: object) -> None:
        self.caught.append(signum)

    def wait(self, timeout_s: float, fds: Sequence[int] = ()) -> tuple[int | None, list[int]]:
        """The first signal caught and not yet taken, if any, and the descriptors of
        `fds` ready to read, after at most `timeout_s`."""
        if not self.caught:
            try:
                ready, _, _ = select.select([self._wake_r, *fds], [], [], timeout_s)
            except InterruptedError:
                ready = []
            if self._wake_r in ready:
                try:
                    os.read(self._wake_r, 512)
                except BlockingIOError:
                    pass
        else:
            ready = select.select(list(fds), [], [], 0)[0] if fds else []
        signum = self.caught.pop(0) if self.caught else None
        return signum, [fd for fd in ready if fd in fds]

    def close(self) -> None:
        for sig, old in self._old.items():
            signal.signal(sig, old)
        signal.set_wakeup_fd(self._old_fd)
        os.close(self._wake_r)
        os.close(self._wake_w)


@dataclass
class Outcome:
    """How a run in a scope ended."""
    code: int                       # the exit code to return
    #: The memory field the outer prints in its own summary; None when the inner
    #: printed its own summary (a check's inner that exited with a code).
    memory: str | None = None
    signum: int | None = None       # the caller's signal: the outer ends by it
    #: Whether the unit was seen gone, every process in it ended, before the outer
    #: returned: only then may the outer sweep what the work's test runs left.
    is_gone: bool = False


def die_by(signum: int) -> None:
    """End this process by `signum`, as a process with no handler for it would."""
    signal.signal(signum, signal.SIG_DFL)
    os.kill(os.getpid(), signum)


@dataclass
class Outer:
    """One run of `command` in a scope, from the outer's side (`run` does it)."""
    kind: str                       # check or cap
    command: Sequence[str]
    found: Figures
    host: Host
    system: System
    description: str
    say: Callable[[str], None] = print
    spawn: Spawn = _spawn
    waiter: Callable[[], Waiter] = Waiter
    clock: Callable[[], float] = time.monotonic
    pause: Callable[[float], None] = time.sleep
    unit: str = ""
    cgroup_dir: Path | None = None
    watch: Watch = field(init=False)

    def __post_init__(self) -> None:
        self.unit = self.unit or new_unit(self.kind)
        self.watch = Watch(self.found.line_bytes if self.host.line_figures else None,
                           group_kill=not self.host.oom_policy)

    @property
    def prefix(self) -> str:
        return f"moxi-dev {self.kind}"

    @property
    def what(self) -> str:
        return "check" if self.kind == "check" else "command"

    def tell(self, text: str) -> None:
        self.say(f"{self.prefix}: {text}")

    def run(self, env: Mapping[str, str]) -> Outcome:
        """Start the scope, watch it until the work ends, say how it ended, and wait a
        moment for the unit to be gone (`Outcome.is_gone`)."""
        outcome = self._run(env)
        outcome.is_gone = self.wait_gone()
        return outcome

    def wait_gone(self) -> bool:
        """Whether the unit is gone, every process in it ended, within `SETTLE_S`: its
        `ActiveState` reads neither active nor on its way in or out."""
        deadline = self.clock() + SETTLE_S
        while True:
            code, said = self.system.systemctl("show", "--property=ActiveState", "--value",
                                               self.unit)
            if code == 0 and said.strip() not in LIVE_STATES:
                return True
            if self.clock() >= deadline:
                return False
            self.pause(0.1)

    def _run(self, env: Mapping[str, str]) -> Outcome:
        started_r, started_w = os.pipe()
        waiter = self.waiter()
        try:
            argv = scope_argv(self.unit, self.host, self.found, self.description,
                              self.command, started_w)
            child = self.spawn(argv, {**env, SCOPE_ENV: self.unit}, started_w)
            os.close(started_w)
            started_w = -1
            return self._follow(child, waiter, started_r)
        finally:
            waiter.close()
            os.close(started_r)
            if started_w >= 0:
                os.close(started_w)

    def _follow(self, child: Child, waiter: Waiter, started_r: int) -> Outcome:
        # The started byte, or EOF when systemd-run ended without running the work.
        while True:
            signum, ready = waiter.wait(1.0, [started_r])
            if signum is not None:
                return self._stopped(child, waiter, signum)
            if ready:
                break
        if not os.read(started_r, 1):
            code = child.wait()
            self.tell(f"the memory scope could not be made (systemd-run exited {code}; "
                      f"its message is above); {MEMORY_MAX_ENV}=none runs the {self.what} "
                      f"uncapped")
            return Outcome(2, "not capped (the scope could not be made)")
        code, group = self.system.systemctl("show", "--property=ControlGroup", "--value",
                                            self.unit)
        if code == 0 and group.strip():
            self.cgroup_dir = self.system.cgroup_dir(group)
        decision = None
        while child.poll() is None:
            signum, _ = waiter.wait(1.0)
            if signum is not None:
                return self._stopped(child, waiter, signum)
            decision = self._sample()
            if decision:
                break
        if decision == "line":
            return self._ended_past_line(child)
        if decision == "oom":
            self._kill_scope()
            child.wait()
        rc = child.returncode
        if decision == "oom" or rc == -signal.SIGKILL:
            killed = self._killed(forced=decision == "oom")
            if killed is not None:
                return killed
        if rc is not None and rc < 0:
            name = signal.Signals(-rc).name
            self.tell(f"the {self.what} was ended by {name}, not for memory")
            return Outcome(1, self.field())
        return self._finished(rc)

    def _sample(self) -> str | None:
        if self.cgroup_dir is None:
            return None
        try:
            sample = read_sample(self.cgroup_dir)
        except OSError:
            # The scope's files are gone: it has ended. Stop watching; no error.
            self.cgroup_dir = None
            return None
        return self.watch.add(sample)

    def _kill_scope(self) -> None:
        self.system.systemctl("kill", "--signal=SIGKILL", self.unit)

    def _stop_scope(self, *, queued: bool) -> None:
        self.system.systemctl("stop", *(("--no-block",) if queued else ()), self.unit)

    def _ended_past_line(self, child: Child) -> Outcome:
        latest = self.watch.latest
        found = self.found
        peak = self.peak()
        self.tell(f"ended for memory: {self.unit} held {gib(latest.held)} it can't give "
                  f"back (anonymous, shared and kernel memory) for {LINE_HOLD_S}s, past its "
                  f"line {gib(found.line_bytes)} under MemoryHigh {gib(found.high_bytes)} "
                  f"(now {self._size(latest.current)}, peak {self._size(peak)}, MemoryMax "
                  f"{gib(found.max_bytes)}; {found.source()}). {self._advice()}")
        self._kill_scope()
        child.wait()
        self.system.systemctl("reset-failed", self.unit)
        return Outcome(1, f"ended past its line (held {gib(latest.held)}, line "
                          f"{gib(found.line_bytes)}, high {gib(found.high_bytes)}, max "
                          f"{gib(found.max_bytes)}, peak {self._size(peak)})")

    def _advice(self) -> str:
        return (f"A {self.what} that grows past its usual size has a test that leaks; "
                f"fewer workers ({WORKERS_ENV}) or a higher {MEMORY_HIGH_ENV} lets a "
                f"bigger {self.what} finish.")

    def _killed(self, *, forced: bool) -> Outcome | None:
        """A SIGKILL death read against the unit: the memory kill's line and outcome, or
        None when it was not for memory."""
        deadline = self.clock() + SETTLE_S
        shown: dict[str, str] = {}
        while True:
            code, said = self.system.systemctl(
                "show", "--property=ActiveState,Result,MemoryPeak,MemoryMax", self.unit)
            shown = dict(line.partition("=")[::2] for line in said.splitlines() if line) \
                if code == 0 else {}
            if shown.get("ActiveState") not in ("active", "deactivating") \
                    or self.clock() >= deadline:
                break
            self.pause(0.1)
        latest = self.watch.latest
        if not (forced or shown.get("Result") == "oom-kill"
                or (latest is not None and latest.oom_kills > 0)):
            return None
        self.system.systemctl("reset-failed", self.unit)
        peak = self.peak(shown.get("MemoryPeak"))
        top = self.found.max_bytes
        if peak is not None and peak < LINE_SHARE * top:
            self.tell(f"killed for memory while the machine ran short, below its cap "
                      f"(peak {gib(peak)}, MemoryMax {gib(top)}): {self.unit}. "
                      f"{self._advice()}")
            return Outcome(1, f"killed while the machine ran short (max {gib(top)}, "
                              f"peak {gib(peak)})")
        self.tell(f"killed for memory: {self.unit} reached its cap, MemoryMax {gib(top)} "
                  f"(peak {self._size(peak)}; {self.found.source()}). {self._advice()}")
        return Outcome(1, f"killed at its cap (max {gib(top)}, peak {self._size(peak)})")

    def peak(self, shown: str | None = None) -> int | None:
        """The peak: the unit's `MemoryPeak` where systemd gives it, else the largest
        `memory.peak` read, else the largest `memory.current` read."""
        if self.host.memory_peak and shown and shown.strip().isdigit() \
                and int(shown) < (1 << 63):
            return int(shown)
        return self.watch.most("peak") or self.watch.most("current")

    @staticmethod
    def _size(n_bytes: int | None) -> str:
        return "unknown" if n_bytes is None else gib(n_bytes)

    def field(self) -> str:
        """The memory field from the outer's own samples (`moxi-dev cap`'s last line)."""
        latest = self.watch.latest
        stalled = None
        if latest is not None and latest.stalled_us is not None:
            stalled = latest.stalled_us / 1e6
        return describe(peak=self.peak(), held=self.watch.most("held"),
                        high=self.found.high_bytes if self.host.line_figures else None,
                        top=self.found.max_bytes, stalled_s=stalled).field

    def _finished(self, rc: int) -> Outcome:
        """The work exited with a code: end what it left in the scope."""
        group = self.cgroup_dir
        if group is not None and self.kind == "cap":
            count = end_leftovers(group)
            if count:
                self.tell(ended_line(count, self.what))
        elif group is not None:
            try:
                left = (group / "cgroup.procs").read_text().split()
            except OSError:
                left = []
            if left:
                self._stop_scope(queued=False)
        return Outcome(rc, self.field() if self.kind == "cap" else None)

    def _stopped(self, child: Child, waiter: Waiter, signum: int) -> Outcome:
        """A caller's signal: say the memory state, stop the scope, end by the signal."""
        name = signal.Signals(signum).name
        self.tell(f"stopped by {name}; {self.state()}")
        if signum == signal.SIGINT:
            deadline = self.clock() + INT_GRACE_S
            while child.poll() is None and self.clock() < deadline:
                waiter.wait(min(1.0, max(0.0, deadline - self.clock())))
            if child.poll() is None:
                self._stop_scope(queued=True)
        else:
            self._stop_scope(queued=True)
        child.wait()
        return Outcome(128 + signum, None, signum)

    def state(self) -> str:
        """`memory then 5.9G, held 5.1G (line ..., stalled 12s of the last 60)`."""
        latest = self.watch.latest
        if latest is None:
            return "memory then unknown"
        found = self.found
        parts = []
        if self.host.line_figures:
            parts.append(f"line {gib(found.line_bytes)}")
        parts.append(f"peak {self._size(self.peak())}")
        if self.host.line_figures:
            parts.append(f"MemoryHigh {gib(found.high_bytes)}")
        parts.append(f"MemoryMax {gib(found.max_bytes)}")
        lately = self.watch.stalled_lately()
        parts.append("stalled unknown" if lately is None
                     else f"stalled {lately[0]:.0f}s of the last {lately[1]}")
        held = self.watch.most("held")
        return (f"memory then {self._size(latest.current)}, held {self._size(held)} "
                f"({', '.join(parts)})")

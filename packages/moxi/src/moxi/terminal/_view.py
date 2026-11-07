"""What the walk reads of a process, one reader per system.

A view answers `read(pid)` with a `Proc`, or raises `Gone` (there is no such process: it
ended, or the system hides it) or `Unread` (the system lets this account read it, and the
read failed anyway). `parent(pid)` re-reads one parent pid, which the walk asks when a
parent was gone, to tell a parent that ended (its child has a new parent now) from one
the system hides (the child's parent is unchanged). `slots_loose()` says whether slots'
tmux server can run outside a unit here.

- **Linux** reads `/proc/<pid>`: `stat` for the parent and start time, `cmdline`,
  `environ` and `cgroup`. A process whose `/proc/<pid>` another uid owns is shielded: it
  runs as another account, or isn't dumpable (an SSH login's `sshd-session`, `sudo`, a
  harness that hardens itself). So is one whose reads the system refuses (`systemd
  --user` holds a capability its own account lacks). Of a shielded process only its
  command line is read, which the system shows for every account's processes.
- **macOS** reads the parent through `sysctl` (`moxi.proc.parent`), the start time and
  the uids through psutil, the command line and the environment through `sysctl`
  (`KERN_PROCARGS2`), and the unit from one `launchctl list`. A process of another uid, or
  a set-id one, is shielded.
- **Windows** reads the parent through Toolhelp (`moxi.proc.parent`), and the start time,
  command line, environment and image through psutil. A process psutil is refused is
  shielded.

A test hands the walk its own view, with the same three methods and `windows`.
"""

from __future__ import annotations

import os
import re
import sys
from collections.abc import Mapping
from dataclasses import dataclass

#: A unit the core writes: systemd `moxi-<module>-<name>.service`, launchd
#: `moxi.<module>.<name>`, each with the home's tag (`h` and 8 hex digits) before the
#: module for a home given by `MOXI_HOME`. The module group is the module's name.
SYSTEMD_UNIT = re.compile(r"moxi-(?:h[0-9a-f]{8}-)?([a-z0-9]+)-([a-z0-9][a-z0-9_-]*)\.service")
LAUNCHD_UNIT = re.compile(r"moxi\.(?:h[0-9a-f]{8}\.)?([a-z0-9]+)\.([a-z0-9][a-z0-9_.-]*)")

#: The one daemon that execs into a program that hands out terminals: slots' server,
#: which becomes tmux.
TMUX_DAEMON = ("slots", "server")


@dataclass(frozen=True)
class Proc:
    """One process as a view read it.

    `parent` is 0 when no parent is visible. `started` compares with another `Proc`'s of
    the same view: a parent that started later than its child is not its parent. `argv`
    is None when the system shows no command line, `env` (the environment it started
    with) is None for a shielded process, `unit` is the supervisor's unit it runs in
    (None outside one, and for a shielded process), `image` its executable's path where
    the walk needs it (Windows), `name` the system's short name for it, for the lines."""

    pid: int
    parent: int
    started: float
    argv: tuple[str, ...] | None
    env: Mapping[str, str] | None
    unit: str | None = None
    image: str | None = None
    shielded: bool = False
    name: str = ""


class Gone(Exception):
    """There is no process `pid`: it ended, or the system hides it from this account."""

    def __init__(self, pid: int):
        super().__init__(f"process {pid} is gone")
        self.pid = pid


class Unread(Exception):
    """Process `pid` exists and this account may read it, but a read failed."""

    def __init__(self, pid: int, error: str, name: str = ""):
        super().__init__(f"process {pid} can't be read: {error}")
        self.pid = pid
        self.error = error
        self.name = name


class _View:
    windows = False

    def read(self, pid: int) -> Proc:
        raise NotImplementedError

    def parent(self, pid: int) -> int | None:
        from moxi import proc

        return proc.parent(pid)

    def slots_loose(self) -> bool:
        """Whether slots is installed and its server doesn't run as a unit here: no
        service manager, or no unit written (an editable install, whose server is started
        by hand). A question that fails answers True, so a tmux server the walk can't
        place stops the record."""
        try:
            from moxi import modules, units

            module, daemon = TMUX_DAEMON
            if not modules.present(module):
                return False
            if units.backend() is None:
                return True
            states = units.status(module)
            server = next((s for s in states if s.kind == "daemon" and s.name == daemon),
                          None)
            return server is None or server.state in ("missing", "unknown")
        except Exception:
            return True


class LinuxView(_View):
    def __init__(self, root: str = "/proc", uid: int | None = None):
        self.root = root
        self.uid = os.geteuid() if uid is None else uid

    def parent(self, pid: int) -> int | None:
        try:
            return self._stat(pid)[0]
        except (Gone, Unread):
            return None

    def read(self, pid: int) -> Proc:
        from moxi import proc

        base = os.path.join(self.root, str(pid))
        try:
            owner = os.stat(base).st_uid
        except FileNotFoundError:
            raise Gone(pid) from None
        except OSError as error:
            raise Unread(pid, _words(error)) from None
        ppid, started, name = self._stat(pid)
        # Another uid owns a process that runs as another account or isn't dumpable. A
        # refused read is the system keeping it from this account too: `systemd --user`
        # holds a capability its account lacks, so its environment is refused.
        shielded = owner != self.uid
        try:
            argv = tuple(proc._linux_cmdline(pid, self.root))
        except ProcessLookupError:
            raise Gone(pid) from None
        except PermissionError:
            argv, shielded = None, True
        except OSError as error:
            if not shielded:
                raise Unread(pid, _words(error), name) from None
            argv = None
        env = None
        if not shielded:
            try:
                env = proc._linux_read_environ(pid, self.root)
            except ProcessLookupError:
                raise Gone(pid) from None
            except PermissionError:
                shielded = True
            except OSError as error:
                raise Unread(pid, _words(error), name) from None
        if shielded:
            return Proc(pid, ppid, started, argv, None, shielded=True, name=name)
        try:
            unit = _cgroup_unit(proc._linux_file(pid, self.root, "cgroup"))
        except ProcessLookupError:
            raise Gone(pid) from None
        except OSError as error:
            raise Unread(pid, _words(error), name) from None
        return Proc(pid, ppid, started, argv, env, unit=unit, name=name)

    def _stat(self, pid: int) -> tuple[int, float, str]:
        """(parent pid, start time in clock ticks since boot, short name). A zombie, or
        a process that ended between reads, is `Gone`."""
        from moxi import proc

        try:
            raw = proc._linux_file(pid, self.root, "stat")
        except ProcessLookupError:
            raise Gone(pid) from None
        except OSError as error:
            raise Unread(pid, _words(error)) from None
        cut = raw.rfind(b")")
        name = raw[raw.find(b"(") + 1:cut].decode("utf-8", "replace")
        fields = raw[cut + 1:].split()
        try:
            state, ppid, started = fields[0], int(fields[1]), float(fields[19])
        except (IndexError, ValueError):
            raise Unread(pid, "its stat file has another shape", name) from None
        if state in (b"Z", b"X", b"x"):
            raise Gone(pid)
        return ppid, started, name


def _cgroup_unit(raw: bytes) -> str | None:
    """The core's unit a `/proc/<pid>/cgroup` names, or None: any line (v1 or v2), any
    component of its path."""
    for line in raw.decode("utf-8", "replace").splitlines():
        path = line.split(":", 2)[-1]
        for part in reversed(path.split("/")):
            if SYSTEMD_UNIT.fullmatch(part):
                return part
    return None


class MacView(_View):
    def __init__(self, uid: int | None = None, units: Mapping[int, str] | None = None):
        self.uid = os.geteuid() if uid is None else uid
        self._units = units
        self._units_error: str | None = None

    def read(self, pid: int) -> Proc:
        import psutil

        from moxi import proc

        try:
            found = psutil.Process(pid)
            started = found.create_time()
            uids, gids = found.uids(), found.gids()
            name = found.name()
        except (psutil.NoSuchProcess, psutil.ZombieProcess):
            raise Gone(pid) from None
        except psutil.Error as error:
            raise Unread(pid, _words(error)) from None
        ppid = proc.parent(pid)
        if ppid is None:
            if not psutil.pid_exists(pid):
                raise Gone(pid)
            raise Unread(pid, "the kernel gave no parent", name)
        shielded = (any(u != self.uid for u in uids) or gids.real != gids.effective
                    or gids.saved != gids.real)
        try:
            argv, entries = proc._procargs2_parts(proc._mac_procargs(pid))
        except ProcessLookupError:
            raise Gone(pid) from None
        except OSError as error:
            if not shielded:
                raise Unread(pid, _words(error), name) from None
            return Proc(pid, ppid, started, None, None, shielded=True, name=name)
        if shielded:
            return Proc(pid, ppid, started, tuple(argv), None, shielded=True, name=name)
        units = self._launchd_units()
        if units is None:
            raise Unread(pid, f"launchctl list failed ({self._units_error})", name)
        return Proc(pid, ppid, started, tuple(argv), proc._pairs(entries),
                    unit=units.get(pid), name=name)

    def _launchd_units(self) -> Mapping[int, str] | None:
        """The pid of each running job of the core's, from one `launchctl list`."""
        if self._units is None and self._units_error is None:
            from moxi import proc

            try:
                done = proc.run(["launchctl", "list"], timeout=10)
            except Exception as error:  # noqa: BLE001 - a timeout, a missing launchctl
                self._units_error = _words(error)
                return None
            if done.returncode != 0:
                self._units_error = f"exit {done.returncode}"
                return None
            self._units = launchd_units(done.stdout.decode("utf-8", "replace"))
        return self._units


def launchd_units(text: str) -> dict[int, str]:
    """`launchctl list`'s `<pid> <status> <label>` lines: pid to label, for each running
    job whose label is one the core writes."""
    found = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[0].isdigit() and LAUNCHD_UNIT.fullmatch(parts[2]):
            found[int(parts[0])] = parts[2]
    return found


class WindowsView(_View):
    windows = True

    def __init__(self):
        self._parents: dict[int, int] | None = None

    def parent(self, pid: int) -> int | None:
        """From one Toolhelp snapshot, taken at the walk's first read: a process started
        since is no ancestor of the run, and a pid given to another process since fails
        the walk's start-time test."""
        from moxi import proc

        if self._parents is None:
            self._parents = proc._windows_parents()
            if self._parents is None:
                return None
        return self._parents.get(pid)

    def read(self, pid: int) -> Proc:
        import psutil

        try:
            found = psutil.Process(pid)
            started = found.create_time()
        except (psutil.NoSuchProcess, psutil.ZombieProcess):
            raise Gone(pid) from None
        except psutil.Error as error:
            raise Unread(pid, _words(error)) from None
        ppid = self.parent(pid)
        if ppid is None:
            if not psutil.pid_exists(pid):
                raise Gone(pid)
            raise Unread(pid, "Toolhelp gave no parent")
        shielded = False
        read: dict[str, object] = {}
        for what in ("name", "cmdline", "environ", "exe"):
            try:
                read[what] = getattr(found, what)()
            except (psutil.NoSuchProcess, psutil.ZombieProcess):
                raise Gone(pid) from None
            except psutil.AccessDenied:
                shielded = True
                read[what] = None
            except (psutil.Error, OSError) as error:
                raise Unread(pid, _words(error), str(read.get("name") or "")) from None
        argv = read["cmdline"]
        return Proc(pid, ppid, started, None if argv is None else tuple(argv),
                    None if shielded else read["environ"], image=read["exe"],
                    shielded=shielded, name=str(read["name"] or ""))


def default_view() -> _View:
    """The reader for this system."""
    if sys.platform.startswith("linux"):
        return LinuxView()
    if sys.platform == "darwin":
        return MacView()
    if sys.platform == "win32":
        return WindowsView()
    raise OSError(f"the walk has no process reader on {sys.platform}")


def _words(error: BaseException) -> str:
    return str(error) or type(error).__name__

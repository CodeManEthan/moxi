"""Which supervisor this system has, and the protocol each backend implements.

`backend()` names the supervisor from the platform and the file system alone, with no
supervisor call. `implementation()` gives the object that speaks to it: a `Backend`.
Three are written for real systems (`moxi.units._systemd`, `_launchd`, `_schtasks`, each
defining `BACKEND`), and `moxi.units._fake` is one for tests. A test installs its own with
`use(backend)`.

The protocol
------------

The core (`plan`, `apply`, `remove`, the acts, `status`, `wrap`) owns everything that is
the same on every supervisor: reading declarations, the checks, marks, the hold on a
changed switched timer, moving a foreign file aside, the core lock, and the shape of the
results. A backend owns what differs: the unit text, where the files go, the order of the
supervisor verbs, and reading the supervisor's state. Every supervisor call a backend
makes goes through the `Fence` it is given (`io.fence` or `fence`), never a program of its
own.

The values passed across:

- `Spec`, one declared unit resolved for writing: the command as an absolute argv, its
  schedule, its sockets (the port, and the addresses to write), and the rest of its
  entry. `render(spec)` turns it into one or more `Rendered` groups of files.
- `UnitRef`, one unit as the supervisor knows it, marked (`module` set) or foreign
  (`module` None): its name, its kind, the backend's ids for it and its files.
- `ApplyIO`, given to `apply`: the fence, file writes and deletes that move a foreign file
  aside first when told to, and `fail` and `say` for the lines and the result of each
  change.

A daemon's `socket`: one listening socket per address, each named by its address's place
in the declared `addresses` (`socket_name`), so one name always holds one address. How a
backend holds them is its `sockets` attribute:

- `"units"` (systemd): one socket unit per address, `<stem>.socket` for the first and
  `<stem>-<k>.socket` for the k-th after it, rendered as one `Rendered` of kind `socket`
  and planned as one `socket` change over the set. The service names them all. Under
  `start = "connection"` it has no install section, so the sockets start it, never a
  login; under `start = "login"` it keeps its install section and a login starts it too.
  Wherever the writer asks whether such a service is static, the answer is whether its
  text has an install section, never whether the daemon declares `socket`.
- `"job"` (launchd): the sockets are keys of the daemon's own file, so there is no
  `socket` change; `status` reads the socket from the daemon's job. Under `start =
  "login"` the file keeps what starts a daemon at load and after a crash.
- `"none"` (Task Scheduler): the socket is skipped, the daemon binds its own, and
  `status` gives the socket `off`.

Marks: a backend writes the mark `moxi unit: <module>/<name>` into every file of a unit
(systemd a first line `# moxi unit: ...`, launchd an XML comment, Task Scheduler the
`Source` line under the `\\Moxi\\` folder), and `mark(text)` reads it back. A unit declared
`enabled = false` says so in its text, so a change of `enabled` alone changes the text;
`enabled(text)` reads it back.
"""

from __future__ import annotations

import os
import shutil
import sys
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Protocol, runtime_checkable

from ._fence import Fence, Ran
from ._types import Acted, Applied, UnitChange, UnitState, Wrapped

# The paths `backend()` reads. Tests point them into their tmp.
_SYSTEMD_INIT = "/run/systemd/system"
_LAUNCHCTL = "/bin/launchctl"


def backend() -> str | None:
    """`"systemd"`, `"launchd"`, `"schtasks"` or None, from the platform and the file
    system, with no supervisor call.

    Linux is `systemd` when `systemctl` is on PATH, systemd is the init
    (`/run/systemd/system` exists) and the user manager's socket
    `$XDG_RUNTIME_DIR/systemd/private` exists; macOS is `launchd` when `/bin/launchctl`
    exists; Windows is `schtasks` when `%SystemRoot%\\System32\\schtasks.exe` exists.
    """
    if _override:
        return _override[-1].name
    if sys.platform.startswith("linux"):
        runtime = os.environ.get("XDG_RUNTIME_DIR")
        if (shutil.which("systemctl") and os.path.isdir(_SYSTEMD_INIT) and runtime
                and os.path.exists(os.path.join(runtime, "systemd", "private"))):
            return "systemd"
        return None
    if sys.platform == "darwin":
        return "launchd" if os.path.exists(_LAUNCHCTL) else None
    if sys.platform == "win32":
        root = os.environ.get("SystemRoot") or os.environ.get("SYSTEMROOT")
        if root and os.path.isfile(os.path.join(root, "System32", "schtasks.exe")):
            return "schtasks"
    return None


def implementation() -> Backend | None:
    """The backend object for `backend()`, or None when there is no supervisor.

    Raises `NotImplementedError` when this moxi has no writer for the supervisor found.
    """
    if _override:
        return _override[-1]
    name = backend()
    if name is None:
        return None
    import importlib

    try:
        module = importlib.import_module(f"moxi.units._{name}")
    except ModuleNotFoundError as exc:
        if exc.name != f"moxi.units._{name}":
            raise
        raise NotImplementedError(f"this moxi has no {name} writer yet") from None
    return module.BACKEND


_override: list[Backend] = []


@contextmanager
def use(impl: Backend) -> Iterator[Backend]:
    """Make `impl` the backend for the length of the block (tests)."""
    _override.append(impl)
    try:
        yield impl
    finally:
        _override.remove(impl)


# -- the values passed across ----------------------------------------------------------------

@dataclass(frozen=True)
class Schedule:
    """A timer's schedule. Either `every` (minutes, 1 to 1440) or `at` (local times as
    `(hour, minute)`, sorted), with `days` the weekdays it runs on (`mon`..`sun`, in week
    order; all seven when the declaration names none)."""

    every: int | None = None
    at: tuple[tuple[int, int], ...] = ()
    days: tuple[str, ...] = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

    @property
    def daily(self) -> bool:
        """True when the timer runs on all seven days."""
        return len(self.days) == 7


@dataclass(frozen=True)
class Socket:
    """A daemon's listening sockets: the addresses as declared, the port read from its
    setting when the unit is written, the IPv6 addresses `plan` left out because the
    system has no IPv6 loopback, and what starts the daemon.

    A backend writes `listens`, never `addresses` whole: each kept address with its place
    in `addresses`, which names its socket (`socket_name`). An address left out keeps its
    place, so the others keep their names.

    `start` is `"connection"` (the default: the sockets start the daemon at the first
    connection, and nothing starts it at login) or `"login"` (a login starts it as it
    starts any daemon, and the supervisor still holds the sockets, so a connection made
    while it restarts waits in the queue). The socket units' text is the same under both.
    """

    setting: str
    addresses: tuple[str, ...]
    port: int
    left_out: tuple[str, ...] = ()
    start: str = "connection"

    @property
    def listens(self) -> tuple[tuple[int, str], ...]:
        """`(place, address)` for each address to write, in declared order."""
        return tuple((place, address) for place, address in enumerate(self.addresses)
                     if address not in self.left_out)


def socket_name(base: str, place: int) -> str:
    """The name of the socket for the address at `place` in a daemon's `addresses`:
    `base` for the first, `<base>-<k>` for the k-th after it (systemd's unit stem
    `moxi-ui-shell` gives `moxi-ui-shell` and `moxi-ui-shell-1`; launchd's key
    `Listeners` gives `Listeners` and `Listeners-1`)."""
    return base if place == 0 else f"{base}-{place}"


def socket_place(base: str, name: str) -> int | None:
    """The place `socket_name(base, place)` gives `name`, or None when it is no socket
    name of `base` (k is a number from 1 with no leading zero)."""
    if name == base:
        return 0
    head, dash, k = name.rpartition("-")
    if head != base or not dash or not k.isdigit() or not k.isascii() or k[0] == "0":
        return None
    return int(k)


def socket_state(unit: UnitRef, backend: str,
                 reads: Sequence[tuple[str, str, bool | None, float | None, str]],
                 log: str | None) -> UnitState:
    """The `socket` state of a daemon whose sockets are units of their own (`sockets ==
    "units"`), from one read of each unit: `reads` is `(id, state, enabled, since,
    invocation)` per unit of `unit.ids`, in unit order, with `state` the unit's own
    (`active`, `inactive`, `failed`, or `missing` for one the supervisor has not loaded),
    `enabled` its unit file state (True, False, or None), `since` its activation's wall
    time and `invocation` its `InvocationID` (empty when it has none).

    `active` when every unit is active, with the epoch `<id>=<invocation>,...`; `failed`
    when one has failed, naming each failed unit and then any other unit not active, so a
    reader can tell which of the rest still listen; else `inactive`, naming the units not
    active. The epoch is read from the state, never from an id being there, since a
    stopped unit can still show its last one; an active unit with no id leaves it None.
    `missing` when `unit` names no socket unit. A backend whose read failed gives
    `unknown` itself, as for any unit.
    """
    if not reads:
        return UnitState(unit.module, unit.name, "socket", backend, "missing",
                         detail="no socket unit is written", log=None)
    failed = [r[0] for r in reads if r[1] == "failed"]
    idle = [r[0] for r in reads if r[1] != "active"]
    if failed:
        state, detail = "failed", "failed: " + ", ".join(failed)
        if rest := [i for i in idle if i not in failed]:
            detail += "; not active: " + ", ".join(rest)
    elif idle:
        state, detail = "inactive", "not active: " + ", ".join(idle)
    else:
        state, detail = "active", "listening"
    enabled = (False if any(r[2] is False for r in reads)
               else True if all(r[2] is True for r in reads) else None)
    times = [r[3] for r in reads if r[3] is not None]
    epoch = None
    if state == "active" and all(r[4] for r in reads):
        epoch = ",".join(f"{r[0]}={r[4]}" for r in reads)
    return UnitState(unit.module, unit.name, "socket", backend, state,
                     since=max(times) if times else None, detail=detail, log=log,
                     enabled=enabled, epoch=epoch)


def enabled_by_socket(units: Sequence[UnitRef], states: Sequence[UnitState],
                      static: Callable[[UnitRef], bool]) -> list[UnitState]:
    """`states` (one per unit of `units`) with each marked daemon whose service is
    `static` (its text has no install section, so it reads `static` and has nothing to
    enable) given the `enabled` of its own `socket` state in the same read. A daemon
    whose service has an install section keeps its own, as does one with no socket state
    there, and one `missing` or `unknown`."""
    sockets = {(u.module, u.name): s for u, s in zip(units, states)
               if u.kind == "socket" and u.marked}
    out = []
    for unit, state in zip(units, states):
        socket = sockets.get((unit.module, unit.name))
        if (socket is not None and unit.kind == "daemon" and unit.marked
                and state.state not in ("missing", "unknown") and static(unit)):
            state = replace(state, enabled=socket.enabled)
        out.append(state)
    return out


@dataclass(frozen=True)
class Spec:
    """One declared unit, resolved for writing.

    `command` is the absolute argv the unit runs: the module's console script found in this
    environment and its `args`, or a run-time entry's `argv`. A timer's backend wraps it in
    the gate, `moxi run [--if <module>.<switch>] --record <module>/<name> -- <command>`,
    with no `--if` when `switch` is None. `doc` is the entry's one line (the unit's name when
    it has none). `entry` is the declaration as read.
    """

    module: str
    name: str
    kind: str
    command: tuple[str, ...]
    doc: str
    entry: Mapping = field(repr=False, compare=False)
    cwd: str | None = None
    enabled: bool = True
    switch: str | None = None
    stop: str = "group"
    upgrade: str = "restart"
    memory_high: str | None = None
    memory_swap_max: str | None = None
    schedule: Schedule | None = None
    socket: Socket | None = None

    @property
    def unit(self) -> str:
        return f"{self.module}/{self.name}"


@dataclass(frozen=True)
class Rendered:
    """One unit's new files: `kind` `daemon`, `timer` or `socket` (a daemon's sockets
    where the backend writes them as units of their own: every socket unit, in unit
    order), and each file's path and text, in the order the backend writes them."""

    kind: str
    files: tuple[tuple[Path, str], ...]


@dataclass(frozen=True)
class UnitRef:
    """One unit as the supervisor knows it.

    `module` is the module whose mark it carries, or None for a foreign unit. `kind` is
    `daemon`, `timer` or `socket`. `ids` are the backend's exact ids for it (systemd unit
    names, a launchd label, a task path), in the order `stop` takes them: a systemd timer
    `(<stem>.timer, <service>)`, where a foreign timer's service is the unit its `Triggers`
    names; a daemon's `socket` its socket units in unit order (on a `"job"` backend, the
    daemon's own label). `paths` are its files (a unit with none, a registered task, has
    `()`).
    `enabled` is what its text says (`enabled = false` written in it), True when it says
    nothing or is foreign.
    """

    module: str | None
    name: str
    kind: str
    ids: tuple[str, ...]
    paths: tuple[Path, ...] = ()
    enabled: bool = True

    @property
    def marked(self) -> bool:
        return self.module is not None


class ReadError(RuntimeError):
    """A supervisor read that failed (`Backend.running`). `ran` is the call."""

    def __init__(self, ran: Ran):
        super().__init__(ran.failure())
        self.ran = ran


class RenderError(Exception):
    """A unit the backend cannot write; `plan` refuses it with `cause` and the text."""

    def __init__(self, cause: str, text: str):
        super().__init__(text)
        self.cause = cause
        self.text = text


class ApplyIO:
    """What `Backend.apply` is given for one `apply` call.

    `fence` runs the supervisor calls. `write(change)` writes the change's files (0644,
    each with `atomic_write`, the directories made as needed), moving an unmarked file in
    the way aside first when the change was planned `refuse` with cause `foreign` and
    `apply` was given `replace=True`. `delete(change)` removes the change's files.
    `fail(change, ran, remedy=None)` records a failed step: it prints the command with the
    supervisor's text, then one remedy line, and makes that change's result `ok=False`.
    `say(change, line)` prints a line and keeps it in the change's result; with `change`
    None it is printed only. `drop(change)` removes the files of the change's `dropped`.
    """

    def __init__(self, fence: Fence, *, say: Callable[[str], object] = print,
                 replace: bool = False, displaced: Callable[[Path], Path] | None = None):
        self.fence = fence
        self._say = say
        self.replace = replace
        self._displaced = displaced
        self.lines: dict[int, list[str]] = {}
        self.failed: set[int] = set()
        self.written: list[Path] = []
        self.moved: list[tuple[Path, Path]] = []

    def write(self, change: UnitChange) -> None:
        from .. import files

        if change.foreign is not None and self.replace and self._displaced is not None:
            if change.foreign.exists():
                aside = self._displaced(change.foreign)
                aside.parent.mkdir(parents=True, exist_ok=True)
                # `shutil.move`, not a rename: the displaced folder may be on another
                # file system than the unit directory.
                shutil.move(change.foreign, aside)
                self.moved.append((change.foreign, aside))
                self.say(change, f"{change.foreign.name} moved aside to {aside}")
        for path, text in zip(change.paths, change.texts):
            files.atomic_write(path, text.encode(self.encoding_of(change)), mode=0o644)
            self.written.append(path)

    def delete(self, change: UnitChange) -> None:
        for path in change.paths:
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass

    def drop(self, change: UnitChange) -> None:
        for path in change.dropped:
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass

    def fail(self, change: UnitChange, ran: Ran, remedy: str | None = None) -> None:
        self.failed.add(id(change))
        self.say(change, ran.failure())
        module = change.module
        self.say(change, remedy or f"the unit file is written; fix the cause and run "
                                   f"`moxi install {module}` again")

    def say(self, change: UnitChange | None, line: str) -> None:
        self._say(line)
        if change is not None:
            self.lines.setdefault(id(change), []).append(line)

    def result(self, change: UnitChange) -> Applied:
        return Applied(change, id(change) not in self.failed,
                       tuple(self.lines.get(id(change), ())))

    # The text encoding of the backend's files; set by `apply` from `Backend.encoding`.
    encoding = "utf-8"

    def encoding_of(self, change: UnitChange) -> str:
        return self.encoding


@runtime_checkable
class Backend(Protocol):
    """What the core asks of a supervisor. Every method's contract is in its docstring.

    A method that runs supervisor calls takes the `Fence` (or `ApplyIO`) to run them
    through. None of them takes the core lock: the core holds it around `apply`, the acts,
    `wrap` and `unwrap`.
    """

    #: `"systemd"`, `"launchd"`, `"schtasks"` (or a test backend's own name).
    name: str
    #: The fence's verb set this backend runs: `"systemd"`, `"launchd"` or `"schtasks"`.
    supervisor: str
    #: The encoding of the unit files: `"utf-8"`, or `"utf-16"` (with its byte-order mark)
    #: for Task Scheduler's XML.
    encoding: str
    #: How a daemon's `socket` is held (the module docstring): `"units"` (socket units of
    #: their own, a `socket` change in a plan), `"job"` (keys of the daemon's own file) or
    #: `"none"` (skipped; the daemon binds its own). `plan` probes for IPv6 loopback only
    #: on `"units"` and `"job"`.
    sockets: str

    # -- writing ---------------------------------------------------------------------------

    def render(self, spec: Spec) -> tuple[Rendered, ...]:
        """The files of `spec`, marked, as one `Rendered` per unit the backend writes for
        it: a daemon's own, then its `socket` where the backend writes one apart (systemd:
        one socket unit per entry of `spec.socket.listens`, named by its place, in unit
        order, all in one group); a timer's (systemd's `.service` and `.timer` in one
        group). Under `start = "connection"` a daemon's sockets are what start it: on
        systemd its service names every socket unit and has no install section, on launchd
        its file has no `RunAtLoad` and no `KeepAlive`. Under `start = "login"` the service
        keeps its install section and the file its `RunAtLoad` and `KeepAlive`, beside the
        sockets. Paths are absolute and
        fixed by the unit's module and name; the text uses absolute paths resolved now,
        copies no variable of this shell, and carries `# moxi: enabled = false` (or the
        backend's form of it) only when `spec.enabled` is False. Raises `RenderError` for a
        unit it cannot write (a script it needs is missing: cause `command`)."""
        ...

    def mark(self, text: str) -> str | None:
        """The `<module>/<name>` a file's text is marked with, or None for no mark."""
        ...

    def enabled(self, text: str) -> bool:
        """False when a marked file's text says the unit is declared `enabled = false`."""
        ...

    def marked(self, module: str) -> tuple[UnitRef, ...]:
        """Every unit on this system that carries `module`'s mark, read from the files (no
        supervisor call): one `UnitRef` per unit `render` would give, a daemon's `socket`
        apart where it is a unit of its own. That `socket` ref is the whole set: every
        file carrying the daemon's mark at a socket name of its stem (`<stem>.socket`, or
        `<stem>-<k>.socket` by `socket_place`), `ids` and `paths` in unit order, so `plan`
        can tell which written units a new render drops. A wrap's drop-in is never one of
        them."""
        ...

    def apply(self, changes: Sequence[UnitChange], *, restart: bool, io: ApplyIO) -> None:
        """Carry out `changes` in the backend's order, writing files through `io.write`
        and `io.delete` and running every verb through `io.fence`.

        `changes` holds only `add`, `change` and `remove`, plus `keep` (which the backend
        acts on only with `restart=True`: a running daemon whose entry says `upgrade =
        "restart"` is restarted). A change's `entry` and `enabled` say how to start it; its
        `enabled_before` is what the written unit said. Each failed step calls
        `io.fail(change, ran)` and the backend goes on with the next unit. A unit whose
        `enabled` is False is written and never enabled or started; a change whose
        `enabled` flipped enables or disables the unit now. A daemon with `upgrade =
        "keep"` whose text changed is rewritten without a restart, with the line "<unit
        id> rewritten; the new code runs from its next start".

        A `socket` change (`sockets == "units"`) is applied before its daemon's change, in
        unit order: a new socket unit `enable --now`, a changed one `enable` then
        `restart`, an unchanged one in a changed set left as it is, and each of `dropped`
        disabled with `--now` beside the removes and deleted with `io.drop` beside the
        removed files. A service with no install section (a daemon with a socket under
        `start = "connection"`) is never enabled or disabled: where another daemon's
        service gets `enable --now` it gets `start`, `restart` alone where another gets
        `enable` then `restart`, and nothing where another gets `enable` alone. One that
        keeps its install section (`start = "login"`) gets the steps any daemon's service
        gets, in the same places. The supervisor refuses to start a socket while its
        service runs, so a set added to a running daemon, or changed in any way (a unit
        added, changed or dropped), is applied between one `stop` and one start of the
        service, whatever `upgrade` says; a new daemon with a new set gets no stop. The
        login link follows the new text: a service whose old text had an install section
        and whose new one has none is disabled (without `--now`), so its old login link
        goes, and a changed daemon whose new text has one is always enabled."""
        ...

    # -- acting and reading ----------------------------------------------------------------

    def own(self, ref: str) -> bool:
        """True when `ref` is in moxi's own namespace on this backend (systemd `moxi-*`,
        launchd `moxi.*`, a task under `\\Moxi\\`), marked or not."""
        ...

    def foreign(self, ref: str, fence: Fence | None) -> UnitRef:
        """The `UnitRef` of the foreign unit `ref` (a systemd unit name, a launchd label, a
        task path), `module` None. A foreign systemd timer's ids are its `.timer` and the
        unit its `Triggers` names, read with one `show`.

        With `fence` None it makes no supervisor call and gives what the name and the files
        tell: a foreign systemd timer's ids are then its `.timer` alone, and `status`
        reads the unit its `Triggers` names in its own calls. `status` passes None, so
        its foreign units cost no call of their own."""
        ...

    def ids(self, module: str, name: str, kind: str) -> tuple[str, ...]:
        """The backend ids the writer gives unit `<module>/<name>` of `kind` (`daemon` or
        `timer`), in the order `stop` takes them: a systemd daemon's socket units
        (`<stem>.socket`, whether or not its file is there, then each `<stem>-<k>.socket`
        whose file is there, in unit order) then its `.service`, a timer's `.timer` then
        its `.service`; a launchd label; a task path. No supervisor call. `stop(...,
        replace=True)` stops the unmarked unit of a declared daemon by these ids."""
        ...

    def files(self, module: str, name: str, kind: str) -> tuple[Path, ...]:
        """The files the writer gives unit `<module>/<name>` of `kind` (`daemon` or
        `timer`): a systemd daemon's `.service` and `.socket` whether or not they are
        there, then each `<stem>-<k>.socket` that is there, in unit order; a timer's
        `.service` and `.timer`; a launchd plist; a task's kept XML. No supervisor call.
        The home move reads them for a file moxi did not write."""
        ...

    def act(self, act: str, unit: UnitRef, fence: Fence) -> Acted:
        """One of `start`, `stop`, `restart`, `enable`, `disable` on one unit, by the verb
        table of the design: after `enable` a daemon runs and a timer's schedule is armed,
        after `disable` both are off. Starting a running unit and stopping a stopped one
        are not failures. On systemd a daemon's socket units go with it, in unit order and
        before its service, for every act but `restart`, which restarts the service alone;
        a service with no install section gets `start` and `stop` for `enable` and
        `disable`, and one with an install section `enable --now` and `disable --now`. On
        launchd a daemon with a socket counts as started once its job is
        loaded, with or without a process."""
        ...

    def running(self, ids: Sequence[str], fence: Fence) -> frozenset[str]:
        """Which of the backend ids `ids` are running now, read in one supervisor call
        (systemd `ActiveState` active, activating or reloading; launchd loaded; Task
        Scheduler a live runtime file). Raises `ReadError` when the read fails, so a
        failed read is never taken for "nothing runs"."""
        ...

    def stop_id(self, unit: str, fence: Fence) -> Ran:
        """Stop the one backend unit `unit` (an id from a `UnitRef`)."""
        ...

    def start_id(self, unit: str, fence: Fence) -> Ran:
        """Start the one backend unit `unit` with the backend's start verb only (systemd
        `start`, launchd `bootstrap` of the plist at the label's path, `schtasks /run`),
        never an enable verb."""
        ...

    def status(self, units: Sequence[UnitRef], fence: Fence) -> list[UnitState]:
        """The state of every unit in `units`, marked and foreign together, in as few
        supervisor calls as the backend allows (one, plus one for the units foreign timers
        trigger): `state`, `pid`, `since`, `enabled`, the supervisor's last result in
        `last`, the `log` hint, and `wrapped` for a foreign timer a module wrapped.

        One `UnitState` per unit, in the order of `units`, with the unit's `module`,
        `name` and `kind`. A foreign timer may come with its `.timer` id alone (`foreign`
        with no fence): its state is the timer's, and its `last`, `pid` and `wrapped` come
        from the unit its `Triggers` names, read in the second call. `last` holds only
        the supervisor's own result (or None); the core adds the gate's run line.

        A `socket` ref is a daemon's sockets, read in the same call. On a `"units"`
        backend its ids are its socket units, and its state follows `socket_state`, with
        `epoch` from each unit's `InvocationID`. On a `"job"` backend the core passes the
        daemon's own ids and paths under kind `socket`: `active` while the job is loaded,
        `inactive` with its file there and not loaded, `missing` without, `enabled` the
        job's, `epoch` None, and never `failed`. `epoch` is None on every other state.
        On a `"units"` backend a daemon whose service text has no install section reads
        `static`, so its own `enabled` is its socket's, from the same read; one whose text
        has an install section reads its own."""
        ...

    def log(self, unit: UnitRef) -> str | None:
        """The hint for reading the unit's output: `journalctl --user -u <id>` on systemd,
        a log file's path on launchd and Windows, None when there is none."""
        ...

    # -- a foreign timer's wrap (systemd only) -----------------------------------------------

    def wrap(self, ref: str, *, module: str, key: str, name: str, fence: Fence) -> Acted:
        """Write `module`'s marked drop-in on the service the foreign timer `ref` triggers
        and reload; `ok=False` with one line on a backend that has no wraps.

        The core has already checked `ref` as a foreign name, `key` as a switch, `name`,
        and that no other wrap of `module` conflicts (`wraps`). The backend refuses, with
        one line naming what it found: a manager older than 243; a timer not loaded or
        not triggering exactly one service; a service without exactly one `ExecStart` or
        with a `Type` other than `oneshot`, `simple` or `exec`; a drop-in on that service
        marked for another module; an unmarked file at the drop-in's path. A drop-in there
        with `module`'s mark is rewritten."""
        ...

    def wraps(self, module: str) -> tuple[tuple[str, str, Wrapped], ...]:
        """Every drop-in carrying `module`'s wrap mark, read from the files (no supervisor
        call), as `(timer ref, service, Wrapped)`: the timer its mark line names, the
        service whose drop-in folder holds it. Empty on a backend with no wraps."""
        ...

    def unwrap(self, ref: str, *, module: str, fence: Fence) -> Acted:
        """Remove `module`'s drop-in for `ref`, found by its mark, and reload."""
        ...

    def unwrap_all(self, module: str, fence: Fence) -> list[Acted]:
        """Remove every drop-in carrying `module`'s mark, with one reload for all; an
        empty list when there are none (or no wraps on this backend)."""
        ...

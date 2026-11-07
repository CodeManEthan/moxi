"""The records `moxi.units` hands back: plans, results, states and declarations."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType

#: What `plan` may decide for one unit.
ACTIONS = ("add", "change", "keep", "remove", "refuse", "skip")
#: The causes a `refuse`, `skip` or `remove` carries.
CAUSES = ("editable", "foreign", "command", "switch", "socket", "declaration", "nobackend",
          "platform")
#: A unit's kinds. A `socket` is a daemon's listening sockets, under its daemon's name. In
#: a plan it is the socket units a backend writes apart from the daemon's own file
#: (systemd's, one per address); in `status` every daemon with a `socket` has one, on every
#: backend.
KINDS = ("daemon", "timer", "socket")
#: What `status` may read for one unit.
STATES = ("active", "inactive", "failed", "missing", "nobackend", "off", "unknown")
#: Task Scheduler's own success codes, which a task's `LastTaskResult` may hold: every
#: `SCHED_S_*` code (0x41300 to 0x41308 "ready", "running", "has not yet run" and the like,
#: 0x4131B, 0x4131C, and 0x41325 "queued") lies in this band. A result that is non-zero and
#: outside it is a run that failed or was refused.
TASK_STATUS_CODES = range(0x41300, 0x41400)

_EMPTY: Mapping = MappingProxyType({})


@dataclass(frozen=True)
class UnitChange:
    """What `plan` decided for one unit of a module, and what `apply` needs to carry it out.

    `kind` is `daemon`, `timer` or `socket`. `action` is one of `ACTIONS`; `cause` says why
    for a `refuse` and a `skip` (and for a `remove` of a daemon switched off, `switch`),
    and is None otherwise. `paths` are the unit's files, in the backend's order (a systemd
    timer's `.service` then its `.timer`). `text` is the new text: one file's text, or, for
    a unit of several files, their texts joined in `paths` order; `texts` holds them one
    per path. Both are empty for a `remove`, a `skip` and a `refuse` with nothing rendered.
    `reason` is one line for a person. `foreign` is the unmarked file in the way of a
    `refuse` with cause `foreign`, which `apply(..., replace=True)` moves aside.

    A `socket` change is a daemon's whole set of socket units, `paths` in unit order (the
    first address's unit, then `-1`, `-2`, ...): `add` when none is written, `keep` when
    the written set names the same files and each is byte-equal, else `change`.
    `dropped` lists the written socket units the new render no longer names (an address
    taken out, or left out for want of IPv6), in unit order; `apply` disables each with
    `--now` and deletes it, as it does a `remove`. It is empty for every other change.

    The fields after `foreign` are for `apply` and the backends: `entry` is the
    declaration the unit was rendered from, `enabled_before` whether the written unit
    said it is enabled (None when no marked file was there), and `digest` the
    declaration digest of a switched timer (None for every other unit).
    """

    module: str
    name: str
    kind: str
    action: str
    cause: str | None
    paths: tuple[Path, ...]
    text: str | None
    reason: str
    foreign: Path | None = None
    dropped: tuple[Path, ...] = ()
    texts: tuple[str, ...] = field(default=(), repr=False)
    entry: Mapping = field(default=_EMPTY, repr=False, compare=False)
    enabled_before: bool | None = field(default=None, repr=False)
    digest: str | None = field(default=None, repr=False)

    @property
    def unit(self) -> str:
        """`<module>/<name>`, the unit's name in lines and records."""
        return f"{self.module}/{self.name}"

    @property
    def enabled(self) -> bool:
        """Whether the declaration lets `apply` enable and start the unit."""
        return self.entry.get("enabled", True) is not False

    @property
    def switch(self) -> str | None:
        return self.entry.get("switch")


@dataclass(frozen=True)
class Applied:
    """What `apply` did with one `UnitChange`. `ok` is False when a step failed, or the
    change is a `refuse` for any cause but `editable`; `lines` are what it printed for
    this unit."""

    change: UnitChange
    ok: bool
    lines: tuple[str, ...] = ()


@dataclass(frozen=True)
class Acted:
    """What one act (`start`, `stop`, `restart`, `enable`, `disable`, `wrap`, `unwrap`)
    did on one unit. `module` is None for a foreign unit."""

    module: str | None
    name: str
    act: str
    ok: bool
    lines: tuple[str, ...] = ()


@dataclass(frozen=True)
class Wrapped:
    """A foreign timer's wrap drop-in, read back: the module that wrote it, its switch key
    and the run record's name."""

    module: str
    key: str
    name: str


@dataclass(frozen=True)
class UnitState:
    """What `status` read for one unit. `module` is None for a foreign unit.

    `state` is one of `STATES`. A timer's state is its schedule on every backend: `active`
    while the backend will run it at its next time, `inactive` when it won't, `failed` only
    when the schedule itself failed (a systemd `.timer` in `failed`), never because of a run.
    `unknown` means the supervisor read that covers the unit failed, on any backend, with
    the failure in `detail`. `since` is a wall-clock time. `log` is the hint for reading
    the unit's output (`journalctl --user -u <unit>`, a log file's path), or None.
    `enabled` is whether it starts at login or on schedule, None when the backend cannot
    say. `wrapped` is set only for a foreign timer that a module wrapped.

    A daemon with a `socket` has a second state, kind `socket`, under its name, right
    after its own, on every backend. On systemd it reads the daemon's socket units:
    `active` when every one is active, `failed` when one has failed (each failed unit
    named in `detail`), else `inactive` (naming the units not active), and `missing` when
    none is written; `since` is the latest of their activation times, `pid` None, and
    `enabled` True when every unit is enabled, False when one is disabled, else None. Under
    `start = "connection"` such a daemon's service has no install section and reads
    `static`, so its own `enabled` is its socket's; under `start = "login"` its service
    keeps its install section, and its own `enabled` is the service's, as any daemon's.
    On launchd the socket state is the daemon's job's: `active` while it
    is loaded (launchd then holds its sockets, with the daemon running or not), else
    `inactive` with its plist there and `missing` without; launchd says nothing of a
    socket it could not bind, so it is never `failed` there. On Task Scheduler it is
    `off`: the socket is skipped on Windows and the daemon binds its own. Wherever the
    daemon is `off` or `nobackend`, so is its socket. A daemon's own state is its
    process's, so one idle under a listening socket reads `inactive`: read its socket
    state beside it before calling it down.

    `epoch` is set on a systemd socket state only, and only while it is `active`:
    `<unit>=<InvocationID>` for each socket unit, in unit order, joined by `,`. systemd
    gives a unit a new `InvocationID` each time it starts, so the epoch changes exactly
    when a socket unit starts again. It is None for every other state and kind, and on
    launchd and Task Scheduler.

    `triggers` is set on a systemd timer's state only, marked or foreign: the unit its
    `.timer`'s `Triggers` names, read in the same `show` as its state, so it costs no
    call. A marked timer's is `<stem>.service`; a foreign one's is the unit its `Unit=`
    names, or the service of its own name when it names none. systemd lets a timer name
    one unit and ignores a second `Unit=`, so this is one name. It is None for a daemon
    and a socket; for a timer that is `missing` (systemd shows an empty `Triggers` for a
    unit it does not hold), `unknown` (nothing was read), `off` or `nobackend` (no read
    was made), or whose `Triggers` is empty; and always on launchd and Task Scheduler,
    where a label or a task is its own job with no second unit. When the `show` of the
    unit a foreign timer names fails after its `.timer` was read, `triggers` is still
    set and `last` is None.

    `last` is how the unit's last run went, None when nothing is known. Its keys at the top
    level are the gate's newest run line (`unit`, `at`, `seconds`, `result`, `exit`), the
    same on every backend, present for a marked unit (and a wrapped foreign timer) once
    the gate has run it. The supervisor's own last result, where it has one, is beside
    them under `"supervisor"`, in each backend's shape:

        systemd       a timer's service: {"result": Result, "status": ExecMainStatus}
                      (strings, as `systemctl show` prints them)
        launchd       a timer: {"status": last exit status} (an int, or None)
        Task Scheduler  a foreign task: {"last_run": LastRunTime, "last_result":
                      LastTaskResult} (Task Scheduler's own status codes, such as
                      0x41303 for "has not yet run", are among its values)

    A foreign Task Scheduler task read as a daemon (`status(..., kinds=...)`) keeps that
    `{"last_run", "last_result"}` entry. Any other daemon has no `"supervisor"` entry, nor
    does a marked Task Scheduler unit. To tell
    whether a timer's last run failed the same way on every backend, read the run line
    (`exit`, `result`), as the doctor does, or `last_runs`.
    """

    module: str | None
    name: str
    kind: str
    backend: str | None
    state: str
    pid: int | None = None
    since: float | None = None
    last: Mapping | None = None
    detail: str = ""
    log: str | None = None
    enabled: bool | None = None
    wrapped: Wrapped | None = None
    epoch: str | None = None
    triggers: str | None = None


@dataclass(frozen=True)
class Declarations:
    """A module's merged declarations: `DAEMONS` and `TIMERS`, with what `UNITS_FROM`
    returned, each a read-only table of name to entry.

    `rejected` maps `(kind, name)` to the reason for every run-time entry that failed its
    checks (`kind` "daemon" or "timer"); `plan` refuses each one. It also holds
    `("health", key)` for a `HEALTH` key that names no daemon of the merged set, which
    `plan` leaves alone and the doctor shows.
    """

    module: str
    daemons: Mapping[str, Mapping]
    timers: Mapping[str, Mapping]
    rejected: Mapping[tuple[str, str], str] = _EMPTY


class DeclarationError(Exception):
    """A module's `UNITS_FROM` target raised, did not finish in time, or returned something
    other than a table of daemons and timers. `text` says which, for a line."""

    def __init__(self, module: str, text: str):
        super().__init__(f"{module}: UNITS_FROM failed: {text}")
        self.module = module
        self.text = text


@dataclass(frozen=True)
class Stopped:
    """One backend unit that `stop(..., record=...)` stopped, so `resume` can start it
    again. `unit` is the backend's exact id; `marked` whether it carried the module's mark.
    `token` is issued by `stop` in this process; `resume` acts only on an entry whose
    token is still outstanding."""

    module: str
    name: str
    unit: str
    backend: str
    marked: bool
    token: object = field(default=None, repr=False, compare=False)

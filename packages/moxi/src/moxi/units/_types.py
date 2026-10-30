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
#: A unit's kinds. A `socket` is a daemon's listening socket where the backend writes it
#: as a unit of its own (systemd's `.socket`); it carries its daemon's name.
KINDS = ("daemon", "timer", "socket")
#: What `status` may read for one unit.
STATES = ("active", "inactive", "failed", "missing", "nobackend", "off", "unknown")

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

    `state` is one of `STATES`. `since` is a wall-clock time. `last` is the newest run
    line, with the supervisor's own result beside it where it has one. `log` is the hint
    for reading the unit's output (`journalctl --user -u <unit>`, a log file's path), or
    None. `enabled` is whether it starts at login or on schedule, None when the backend
    cannot say. `wrapped` is set only for a foreign timer that a module wrapped.
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

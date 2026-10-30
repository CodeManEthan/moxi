"""A stand-in for the core's writer, `moxi.units`, that services' acts and reads call.

Tests put it in place of the `units` that `moxi_services.acts` and `moxi_services.state`
call through, so no test reaches a supervisor. It keeps the units it was told about in
three tables, records every call, and can be told to fail in the ways the acts must
survive. Its records are the core's public ones (`UnitChange`, `Applied`, `Acted`,
`UnitState`, `Wrapped`), and its plan reads services' real `declared()`, so a written
row's unit follows from the store here as it does under the core.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from moxi.units import Acted, Applied, UnitChange, UnitState, Wrapped

from moxi_services import units as services_units


@dataclass
class Unit:
    """One unit the fake supervisor holds."""

    kind: str
    enabled: bool | None = False
    active: bool = False
    last: dict | None = None
    detail: str = ""


@dataclass
class FakeUnits:
    """The fake writer. `supervisor` is what `backend()` answers ("systemd", "launchd",
    "schtasks" or None)."""

    supervisor: str | None = "systemd"
    calls: list = field(default_factory=list)
    #: services' written units, by name.
    written: dict = field(default_factory=dict)
    #: foreign units, by the supervisor's own name.
    foreign: dict = field(default_factory=dict)
    #: other modules' units, by (module, name).
    modules: dict = field(default_factory=dict)
    #: foreign timers wrapped, by name.
    wrapped: dict = field(default_factory=dict)
    #: (act, name) pairs whose act fails.
    fail: set = field(default_factory=set)
    #: (action, cause, reason) every planned unit gets in place of add or change.
    plan_as: tuple | None = None
    #: an exception plan raises.
    plan_raises: Exception | None = None
    #: apply fails to write or to remove a unit, leaving it as it was.
    apply_fails: bool = False
    #: an act that succeeds leaves the unit's state as it was (a drift the re-read sees).
    drift: bool = False
    #: every state reads unknown, as after a failed supervisor read.
    unknown: bool = False
    #: wrap refuses with this line, writing nothing.
    wrap_refuses: str | None = None
    #: wrap fails after writing its drop-in.
    wrap_fails: bool = False
    #: unwrap fails, leaving the drop-in.
    unwrap_fails: bool = False
    #: whether services holds the core lock, asked at each call that writes or acts.
    probe: object = None
    #: what `probe` said, by call, in order.
    locked: list = field(default_factory=list)

    # -- the calls services makes ------------------------------------------------------

    def backend(self):
        return self.supervisor

    def plan(self, module, *, only=None):
        self.calls.append(("plan", module, tuple(only or ())))
        self._held()
        if self.plan_raises is not None:
            raise self.plan_raises
        declared = services_units.declared()
        out = []
        names = set()
        for key, kind in (("daemons", "daemon"), ("timers", "timer")):
            for name, entry in declared.get(key, {}).items():
                if only is not None and name not in only:
                    continue
                names.add(name)
                if self.plan_as is not None:
                    action, cause, reason = self.plan_as
                else:
                    action = "change" if name in self.written else "add"
                    cause, reason = None, f"{action} {name}"
                out.append(UnitChange(module, name, kind, action, cause, (), None, reason,
                                      entry=entry))
        for name, unit in self.written.items():
            if name not in names and (only is None or name in only):
                # An editable install refuses every unit, a marked one no longer
                # declared too.
                action, cause, reason = (self.plan_as if self.plan_as is not None
                                         and self.plan_as[1] == "editable"
                                         else ("remove", None, "no longer declared"))
                out.append(UnitChange(module, name, unit.kind, action, cause, (), None,
                                      reason))
        return out

    def apply(self, changes, *, say=print, replace=False, restart=False, runner=None):
        self.calls.append(("apply", tuple((c.name, c.action) for c in changes)))
        self._held()
        out = []
        for change in changes:
            if change.action in ("refuse", "skip"):
                out.append(Applied(change, change.cause == "editable"
                                   if change.action == "refuse" else True,
                                   (f"{change.unit}: {change.reason}",)))
                continue
            if self.apply_fails:
                out.append(Applied(change, False, (f"{change.unit}: Job failed",)))
                continue
            if change.action == "remove":
                self.written.pop(change.name, None)
            elif change.action in ("add", "change"):
                on = change.enabled
                self.written[change.name] = Unit(change.kind, on,
                                                 on and change.kind == "daemon")
            out.append(Applied(change, True, (f"{change.unit} {change.action}",)))
        return out

    def start(self, module, names, *, foreign=False, runner=None):
        return self._act("start", module, names, foreign)

    def stop(self, module, names, *, foreign=False, runner=None):
        return self._act("stop", module, names, foreign)

    def restart(self, module, names, *, foreign=False, runner=None):
        return self._act("restart", module, names, foreign)

    def enable(self, module, names, *, foreign=False, runner=None):
        return self._act("enable", module, names, foreign)

    def disable(self, module, names, *, foreign=False, runner=None):
        return self._act("disable", module, names, foreign)

    def _table(self, module, foreign):
        if foreign:
            return self.foreign, lambda name: name
        if module == "services":
            return self.written, lambda name: name
        return self.modules, lambda name: (module, name)

    def _act(self, act, module, names, foreign):
        self.calls.append((act, module, tuple(names), foreign))
        self._held()
        table, key = self._table(module, foreign)
        out = []
        for name in names:
            unit = table.get(key(name))
            owner = None if foreign else module
            if unit is None:
                out.append(Acted(owner, name, act, False, (f"{name}: no such unit",)))
                continue
            if (act, name) in self.fail:
                out.append(Acted(owner, name, act, False, (f"{name}: Job for {name} failed",)))
                continue
            if not self.drift:
                if act in ("start", "restart"):
                    unit.active = unit.kind == "daemon"
                elif act == "stop":
                    unit.active = False
                elif act == "enable":
                    unit.enabled, unit.active = True, unit.kind == "daemon"
                elif act == "disable":
                    unit.enabled, unit.active = False, False
            out.append(Acted(owner, name, act, True, (f"{name} {act}ed",)))
        return out

    def status(self, modules=None, *, foreign=(), runner=None):
        modules = [modules] if isinstance(modules, str) else list(modules or ())
        foreign = [foreign] if isinstance(foreign, str) else list(foreign)
        self.calls.append(("status", tuple(modules), tuple(foreign)))
        out = []
        for module in modules:
            if module == "services":
                units = sorted(self.written.items())
            else:
                units = sorted((name, unit) for (m, name), unit in self.modules.items()
                               if m == module)
            for name, unit in units:
                out.append(self._state(module, name, unit))
        for ref in foreign:
            unit = self.foreign.get(ref)
            if unit is None:
                kind = "timer" if ref.endswith(".timer") else "daemon"
                out.append(UnitState(None, ref, kind, self.supervisor, "missing",
                                     detail=f"{ref} is not loaded"))
            else:
                out.append(self._state(None, ref, unit, self.wrapped.get(ref)))
        return out

    def _state(self, module, name, unit, wrapped=None):
        if self.supervisor is None:
            return UnitState(module, name, unit.kind, None, "nobackend",
                             detail="no supervisor on this system")
        if self.unknown:
            return UnitState(module, name, unit.kind, self.supervisor, "unknown",
                             detail="systemctl show failed")
        if unit.kind == "timer":
            st = "active" if unit.enabled else "inactive"
        else:
            st = "active" if unit.active else "inactive"
        return UnitState(module, name, unit.kind, self.supervisor, st, last=unit.last,
                         detail=unit.detail, enabled=unit.enabled, wrapped=wrapped,
                         log=f"journalctl --user -u {name}")

    def last_runs(self, module, timer, n=1):
        self.calls.append(("last_runs", module, timer, n))
        return []

    def wrap(self, ref, *, module, key, name, runner=None):
        self.calls.append(("wrap", ref, module, key, name))
        self._held()
        if self.wrap_refuses is not None:
            return Acted(module, ref, "wrap", False, (self.wrap_refuses,))
        self.wrapped[ref] = Wrapped(module, key, name)
        if self.wrap_fails:
            return Acted(module, ref, "wrap", False, (f"{ref}: daemon-reload failed",))
        return Acted(module, ref, "wrap", True, (f"{ref} wrapped",))

    def unwrap(self, ref, *, module, runner=None):
        self.calls.append(("unwrap", ref, module))
        self._held()
        if self.unwrap_fails:
            return Acted(module, ref, "unwrap", False, (f"{ref}: daemon-reload failed",))
        self.wrapped.pop(ref, None)
        return Acted(module, ref, "unwrap", True, (f"{ref} unwrapped",))

    def wraps(self, module):
        return {ref: w for ref, w in self.wrapped.items() if w.module == module}

    def _held(self):
        if self.probe is not None:
            self.locked.append((self.calls[-1][0], self.probe()))

    # -- for the tests -----------------------------------------------------------------

    def acts(self):
        """The acts called, as (act, module, names, foreign)."""
        return [c for c in self.calls if c[0] in ("start", "stop", "restart", "enable",
                                                   "disable")]


def plist(home: Path, label: str) -> Path:
    """Write a launchd agent's plist for `label` under `home`."""
    path = home / "Library" / "LaunchAgents" / f"{label}.plist"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("<plist/>")
    return path

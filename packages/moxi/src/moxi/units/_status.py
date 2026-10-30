"""`status` and `last_runs`: what the supervisor says of each unit, and the gate's run lines.

`status(modules)` reads every declared unit of each module, and every unit carrying its
mark, in one supervisor call per backend (the backend's `status`, plus one call for the
units foreign timers trigger), and `status(foreign=[...])` reads foreign units in that
same call. It only reads, so it takes no lock.

What it gives a unit that has no unit file to read:

    nobackend   no supervisor on this system; install skipped the unit
    off         not on this platform, or a daemon whose switch is off
    missing     declared, and no unit is written for it

`last` is the newest run line the gate wrote for the unit (`STATE/<module>/runs.jsonl`),
with the supervisor's own last result beside it under `"supervisor"`; with no run line it
is `{"supervisor": ...}`, and None when there is neither. A foreign timer a module wrapped
takes its run line from the wrap's record, `<module>/<name>`.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import replace

from . import _backend
from ._apply import fence as _fence
from ._backend import UnitRef
from ._fence import FenceError, argv
from ._plan import _platform
from ._types import DeclarationError, Declarations, UnitState

#: The gate's run lines, in a module's state directory.
RUNS = "runs.jsonl"


def status(modules: Iterable[str] | str | None = None, *, foreign: Iterable[str] = (),
           runner=None) -> list[UnitState]:
    """The state of every unit of `modules` (every module present when None), then of
    each foreign unit in `foreign`, in that order.

    A module's units are its declared daemons and timers, in declared order, then every
    other unit carrying its mark (one a module no longer declares, a daemon's own
    `.socket` unit). A foreign name that is empty, in moxi's own namespace (read such a
    unit through its module), or one the supervisor's fence refuses is `unknown` with the
    reason, and the other units are read as ever.
    """
    from .. import modules as _modules

    if isinstance(modules, str):
        modules = [modules]
    if isinstance(foreign, str):
        foreign = [foreign]
    foreign = list(foreign)
    names = (sorted({entry.name for entry in _modules.installed()}) if modules is None
             else list(modules))
    impl = _implementation()
    rows: list[UnitState | None] = []
    refs: list[UnitRef] = []
    slots: list[int] = []

    def read(ref: UnitRef) -> None:
        slots.append(len(rows))
        rows.append(None)
        refs.append(ref)

    for module in names:
        for row in _module_rows(module, impl):
            if isinstance(row, UnitRef):
                read(row)
            else:
                rows.append(row)
    for ref in foreign:
        if not isinstance(ref, str):
            raise ValueError(f"a foreign unit's name is a string, not {ref!r}")
        kind = "timer" if ref.endswith(".timer") else "daemon"
        backend = None if isinstance(impl, str) else impl.name
        if not ref:
            rows.append(UnitState(None, ref, kind, backend, "unknown",
                                  detail="a foreign unit's name is not empty; not read"))
            continue
        if isinstance(impl, str):
            rows.append(UnitState(None, ref, kind, None, "nobackend", detail=impl))
            continue
        if impl.own(ref):
            rows.append(UnitState(None, ref, kind, backend, "unknown",
                                  detail=f"{ref} is in moxi's own unit namespace; read it "
                                         "through the module whose mark it carries"))
            continue
        unit = impl.foreign(ref, None)
        refused = _refused(impl, unit)
        if refused is not None:
            rows.append(UnitState(None, ref, kind, backend, "unknown",
                                  detail=f"{ref}: {refused}; not read"))
            continue
        read(unit)
    if refs:
        read_states = impl.status(refs, _fence(impl, runner))
        if len(read_states) != len(refs):
            raise RuntimeError(f"the {impl.name} backend read {len(read_states)} states for "
                               f"{len(refs)} units")
        runs = _Runs()
        for slot, ref, state in zip(slots, refs, read_states):
            rows[slot] = _with_last(state, ref, runs)
    return [row for row in rows if row is not None]


def _refused(impl, unit: UnitRef) -> str | None:
    """Why the fence would refuse to read foreign `unit`, or None. Only systemd puts a
    foreign name on the command line of its read; the other backends read the whole
    domain, or pass the names in the environment."""
    if impl.supervisor != "systemd":
        return None
    try:
        argv("systemd", "show", "--property=Id", *unit.ids)
    except FenceError as exc:
        return str(exc)
    return None


def last_runs(module: str, timer: str, n: int = 1) -> list[dict]:
    """The newest `n` run lines the gate wrote for `<module>/<timer>`, oldest first (so
    the newest is last), read from `STATE/<module>/runs.jsonl` and its older generation.
    An empty list when there are none."""
    from .. import names

    names.check_module_name(module)
    if not isinstance(n, int) or isinstance(n, bool) or n < 0:
        raise ValueError(f"n is a count of run lines, not {n!r}")
    if n == 0:
        return []
    return _Runs().of(module, f"{module}/{timer}")[-n:]


# -- a module's units ------------------------------------------------------------------------

def _implementation():
    try:
        impl = _backend.implementation()
    except NotImplementedError as exc:
        return str(exc)
    return "no supervisor on this system (no systemd user manager)" if impl is None else impl


def _module_rows(module: str, impl) -> list[UnitState | UnitRef]:
    """One entry per unit of `module`: a `UnitState` decided without the supervisor, or
    the `UnitRef` the backend reads."""
    from .. import modules as _modules

    found = _modules.load(module)
    declared: list[tuple[str, str, Mapping | None]] = []
    if found.state == _modules.PRESENT:
        try:
            from ._declare import merge

            decl = merge(found)
        except DeclarationError:
            decl = Declarations(module, found.daemons, found.timers)
        declared = [(kind, name, entry) for kind, table in (("daemon", decl.daemons),
                                                            ("timer", decl.timers))
                    for name, entry in table.items()]
        declared += [(kind, name, None) for kind, name in decl.rejected
                     if kind in ("daemon", "timer")]
    if isinstance(impl, str):
        return [UnitState(module, name, kind, None, "nobackend", detail=impl)
                for kind, name, _ in declared]
    by_name: dict[str, list[UnitRef]] = {}
    for ref in impl.marked(module):
        by_name.setdefault(ref.name, []).append(ref)
    out: list[UnitState | UnitRef] = []
    settings = _Switches(found)
    for kind, name, entry in declared:
        refs = by_name.pop(name, [])
        if refs:
            out += refs
            continue
        unit = f"{module}/{name}"
        here = _platform()
        if entry is not None and "platforms" in entry and here not in entry["platforms"]:
            out.append(UnitState(module, name, kind, impl.name, "off",
                                 detail=f"{unit} is not on this system ({here})"))
        elif entry is not None and kind == "daemon" and settings.off(entry.get("switch")):
            out.append(UnitState(module, name, kind, impl.name, "off",
                                 detail=f"{unit} is switched off; turn it on with: moxi "
                                        f"config set {module} {entry['switch']} true"))
        else:
            out.append(UnitState(module, name, kind, impl.name, "missing",
                                 detail=f"{unit} is not installed; `moxi install {module}` "
                                        "writes it"))
    for refs in by_name.values():
        out += refs
    return out


class _Switches:
    """A module's daemon switches, read once; a switch that cannot be read is not off."""

    def __init__(self, found):
        self.found = found
        self._settings: list = []

    def off(self, switch: str | None) -> bool:
        if switch is None:
            return False
        if not self._settings:
            self._settings.append(self._load())
        settings = self._settings[0]
        if settings is None:
            return False
        try:
            return not bool(settings[switch])
        except KeyError:
            return False

    def _load(self):
        from .. import config

        try:
            schema = config.schema_for(self.found)
            return None if schema is None else config.load(self.found.name, schema)
        except config.ConfigError:
            return None


# -- run lines -------------------------------------------------------------------------------

class _Runs:
    """Each module's run lines, read once per `status` call."""

    def __init__(self):
        self._by_module: dict[str, list[dict]] = {}

    def of(self, module: str, unit: str) -> list[dict]:
        if module not in self._by_module:
            self._by_module[module] = self._read(module)
        return [r for r in self._by_module[module] if r.get("unit") == unit]

    def newest(self, module: str, unit: str) -> dict | None:
        found = self.of(module, unit)
        return found[-1] if found else None

    @staticmethod
    def _read(module: str) -> list[dict]:
        from .. import files, paths

        try:
            return files.read_records(paths._layout(module).state_dir() / RUNS)
        except (OSError, ValueError):
            return []


def _with_last(state: UnitState, ref: UnitRef, runs: _Runs) -> UnitState:
    run = None
    if state.wrapped is not None:
        wrap = state.wrapped
        run = runs.newest(wrap.module, f"{wrap.module}/{wrap.name}")
    elif ref.module is not None and ref.kind != "socket":
        run = runs.newest(ref.module, f"{ref.module}/{ref.name}")
    supervisor = dict(state.last) if state.last else None
    if run is None:
        last = None if supervisor is None else {"supervisor": supervisor}
    else:
        last = dict(run)
        if supervisor is not None:
            last["supervisor"] = supervisor
    return replace(state, module=ref.module, name=ref.name, kind=ref.kind, last=last)

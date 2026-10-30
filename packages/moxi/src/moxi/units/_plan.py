"""`plan`: what writing a module's declared units would change, unit by unit.

`plan` reads the module's merged declarations, resolves each unit for writing (its command
in this environment, its switch, its socket's port), renders it through the backend, and
compares the new text with what is on disk. It writes nothing and makes no supervisor
call, so it takes no lock.
"""

from __future__ import annotations

import sys
from collections.abc import Iterable, Mapping
from pathlib import Path

from . import _backend
from ._backend import RenderError, Schedule, Socket, Spec, UnitRef
from ._declare import digest, merge, switch_problem, switch_value
from ._types import DeclarationError, Declarations, UnitChange

_DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def plan(module: str, *, only: Iterable[str] | None = None,
         env: Mapping[str, str] | None = None) -> list[UnitChange]:
    """What applying module `module`'s declarations would change, one `UnitChange` per
    unit (a daemon's `socket` apart where the backend writes it as a unit of its own).

    `only` names daemons and timers: only those are planned (each with its socket), and
    no unit outside them is planned `remove`. `env` is the environment the module's
    settings (a daemon's switch, a socket's port) are read in. Raises `ValueError` for a
    module that is not present.
    """
    from .. import modules

    found = modules.load(module)
    if found.state != modules.PRESENT:
        raise ValueError(f"{module} is {found.state}"
                         + (f": {found.reason}" if found.reason else ""))
    wanted = None if only is None else frozenset(only)
    try:
        decl, failed = merge(found), None
    except DeclarationError as exc:
        decl, failed = Declarations(module, found.daemons, found.timers), exc
    declared = [(kind, name, entry) for kind, table in (("daemon", decl.daemons),
                                                        ("timer", decl.timers))
                for name, entry in table.items()]
    declared += [(kind, name, None) for (kind, name) in decl.rejected
                 if kind in ("daemon", "timer")]
    if wanted is not None:
        declared = [d for d in declared if d[1] in wanted]

    impl = _backend.implementation()
    if impl is None:
        return [_unit(module, name, kind, "skip", "nobackend",
                      "no supervisor on this system (no systemd user manager)", entry)
                for kind, name, entry in declared]
    marked: dict[str, list[UnitRef]] = {}
    for ref in impl.marked(module):
        marked.setdefault(ref.name, []).append(ref)
    if _editable(found):
        reason = (f"{found.distribution} is an editable install; units are written only "
                  f"from an installed release: `moxi install {module}` in a uv tool "
                  "environment")
        names = {name for _, name, _ in declared}
        out = [_unit(module, name, kind, "refuse", "editable", reason, entry)
               for kind, name, entry in declared]
        out += [_unit(module, ref.name, ref.kind, "refuse", "editable", reason, None,
                      ref.paths) for name, refs in marked.items() if name not in names
                for ref in refs if wanted is None or name in wanted]
        return out

    planner = _Planner(found, impl, env)
    out: list[UnitChange] = []
    for kind, name, entry in declared:
        refs = marked.pop(name, [])
        if entry is None:
            out.append(_unit(module, name, kind, "refuse", "declaration",
                             decl.rejected[(kind, name)], None, _paths(refs)))
            continue
        out += planner.unit(kind, name, entry, refs)
    for name, refs in marked.items():
        if wanted is not None and name not in wanted:
            continue
        for ref in refs:
            if failed is not None:
                why = ("did not finish in 10 s" if failed.text.startswith("did not finish")
                       else f"failed: {failed.text}")
                out.append(_unit(module, name, ref.kind, "skip", "declaration",
                                 f"UNITS_FROM {why}; left as they are", None, ref.paths))
            else:
                out.append(_remove(module, ref, None, "no longer declared"))
    return out


class _Planner:
    """The per-unit half of `plan`, holding what one module's units share."""

    def __init__(self, found, impl, env):
        self.found = found
        self.module = found.name
        self.impl = impl
        self.env = env
        self._schema: list = []

    def schema(self):
        """The module's settings schema, or a `moxi.config.ConfigError`, read once."""
        if not self._schema:
            from .. import config

            try:
                self._schema.append(config.schema_for(self.found))
            except config.ConfigError as exc:
                self._schema.append(exc)
        return self._schema[0]

    def unit(self, kind: str, name: str, entry: Mapping, refs: list[UnitRef]
             ) -> list[UnitChange]:
        module = self.module
        unit = f"{module}/{name}"
        here = _platform()
        if "platforms" in entry and here not in entry["platforms"]:
            return [_unit(module, name, kind, "skip", "platform",
                          f"{unit} is not on this system ({here})", entry, _paths(refs))]
        switch = entry.get("switch")
        if switch is not None:
            problem = self._switch_problem(switch)
            if problem:
                return [_unit(module, name, kind, "refuse", "switch", f"{unit}: {problem}",
                              entry, _paths(refs))]
        if kind == "daemon" and switch is not None:
            from .. import config

            try:
                on = switch_value(module, self.schema(), switch, self.env)
            except config.ConfigError as exc:
                return [_unit(module, name, kind, "refuse", "switch",
                              f"{unit}: its switch {switch} cannot be read: {exc}", entry,
                              _paths(refs))]
            if not on:
                line = (f"{unit} is off; turn it on with: moxi config set {module} "
                        f"{switch} true")
                if refs:
                    return [_remove(module, ref, entry, line, cause="switch")
                            for ref in refs]
                return [_unit(module, name, kind, "skip", "switch", line, entry)]
        socket = None
        if kind == "daemon" and "socket" in entry:
            socket = self._socket(entry["socket"])
            if isinstance(socket, str):
                return [_unit(module, name, kind, "refuse", "socket", f"{unit}: {socket}",
                              entry, _paths(refs))]
        command = _command(entry)
        if isinstance(command, str):
            return [_unit(module, name, kind, "refuse", "command", command, entry,
                          _paths(refs))]
        spec = Spec(module=module, name=name, kind=kind, command=command,
                    doc=entry.get("doc") or name, entry=entry, cwd=entry.get("cwd"),
                    enabled=entry.get("enabled", True) is not False, switch=switch,
                    stop=entry.get("stop", "group"),
                    upgrade=entry.get("upgrade", "restart"),
                    memory_high=entry.get("memory_high"),
                    memory_swap_max=entry.get("memory_swap_max"),
                    schedule=_schedule(entry) if kind == "timer" else None, socket=socket)
        try:
            rendered = self.impl.render(spec)
        except RenderError as exc:
            return [_unit(module, name, kind, "refuse", exc.cause, exc.text, entry,
                          _paths(refs))]
        stamp = digest(entry) if kind == "timer" and switch is not None else None
        out = [self._compare(name, group, entry, stamp) for group in rendered]
        kinds = {group.kind for group in rendered}
        out += [_remove(module, ref, entry, "no longer declared")
                for ref in refs if ref.kind not in kinds]
        return out

    def _switch_problem(self, switch: str) -> str | None:
        schema = self.schema()
        if isinstance(schema, Exception):
            return f"its switch {switch} cannot be checked: {schema}"
        return switch_problem(schema, switch)

    def _socket(self, declared: Mapping) -> Socket | str:
        """The socket with its port read now, or the reason it is refused."""
        from .. import config

        setting = declared["setting"]
        schema = self.schema()
        if isinstance(schema, Exception):
            return f"its socket setting {setting} cannot be checked: {schema}"
        key = None if schema is None else schema.keys.get(setting)
        if key is None or key.retired is not None or key.type != "int":
            return f"its socket setting {setting} must be an int setting of the module"
        try:
            port = config.load(self.module, schema, env=self.env)[setting]
        except config.ConfigError as exc:
            return f"its socket setting {setting} cannot be read: {exc}"
        if not isinstance(port, int) or isinstance(port, bool) or not 0 < port < 65536:
            return f"its socket setting {setting} is {port!r}, not a port from 1 to 65535"
        return Socket(setting, tuple(declared["addresses"]), port)

    def _compare(self, name: str, group, entry: Mapping, stamp: str | None) -> UnitChange:
        module, impl = self.module, self.impl
        unit = f"{module}/{name}"
        paths = tuple(path for path, _ in group.files)
        texts = tuple(text for _, text in group.files)
        missing = same = 0
        foreign: tuple[Path, str | None] | None = None
        enabled_before: bool | None = None
        for path, text in group.files:
            try:
                old = path.read_bytes()
            except FileNotFoundError:
                missing += 1
                continue
            except OSError as exc:
                foreign = foreign or (path, f"cannot be read ({exc})")
                continue
            decoded = old.decode(impl.encoding, errors="replace")
            mark = impl.mark(decoded)
            if mark != unit:
                if foreign is None:
                    foreign = (path, mark)
                continue
            enabled_before = impl.enabled(decoded) and enabled_before is not False
            if old == text.encode(impl.encoding):
                same += 1
        common = dict(paths=paths, text="".join(texts), texts=texts, entry=entry,
                      enabled_before=enabled_before, digest=stamp)
        if foreign is not None:
            path, mark = foreign
            what = (f"is marked for {mark}" if mark and "/" in mark
                    else mark if mark and mark.startswith("cannot")
                    else "was not written by moxi")
            reason = (f"{path.name} exists and {what}; `moxi install {module} --replace` "
                      "moves it to DATA/moxi/displaced/")
            return UnitChange(module, name, group.kind, "refuse", "foreign",
                              reason=reason, foreign=path, **common)
        if missing == len(paths):
            return UnitChange(module, name, group.kind, "add", None,
                              reason="not installed", **common)
        if same == len(paths):
            return UnitChange(module, name, group.kind, "keep", None, reason="up to date",
                              **common)
        return UnitChange(module, name, group.kind, "change", None, reason="stale",
                          **common)


def _unit(module, name, kind, action, cause, reason, entry, paths=()) -> UnitChange:
    return UnitChange(module, name, kind, action, cause, tuple(paths), None, reason,
                      entry=entry if entry is not None else {})


def _remove(module, ref: UnitRef, entry, reason, *, cause=None) -> UnitChange:
    return UnitChange(module, ref.name, ref.kind, "remove", cause, ref.paths, None, reason,
                      entry=entry if entry is not None else {}, enabled_before=ref.enabled)


def _paths(refs: list[UnitRef]) -> tuple[Path, ...]:
    return tuple(path for ref in refs for path in ref.paths)


def _platform() -> str:
    return "linux" if sys.platform.startswith("linux") else sys.platform


def _command(entry: Mapping) -> tuple[str, ...] | str:
    """The absolute argv the unit runs, or the reason it is refused."""
    import os

    if "argv" in entry:
        argv = tuple(entry["argv"])
        if not os.path.isfile(argv[0]):
            return f"{argv[0]} does not exist; the run-time declaration names it as argv[0]"
        return argv
    from .. import paths

    name = entry["command"]
    try:
        script = paths.script(name)
    except FileNotFoundError:
        return f"no {name} in this environment"
    return (str(script), *entry.get("args", ()))


def _schedule(entry: Mapping) -> Schedule:
    if "every" in entry:
        every = entry["every"]
        count = int(every[:-1])
        return Schedule(every=count * 60 if every.endswith("h") else count)
    at = tuple(sorted({(int(t[:2]), int(t[3:])) for t in entry["at"]}))
    days = tuple(d for d in _DAYS if d in entry.get("days", _DAYS))
    return Schedule(at=at, days=days)


def _editable(found) -> bool:
    """True when the module's distribution is installed editable (its `direct_url.json`
    says `dir_info.editable`)."""
    import json
    from importlib.metadata import PackageNotFoundError, distribution

    if not found.distribution:
        return False
    try:
        text = distribution(found.distribution).read_text("direct_url.json")
    except PackageNotFoundError:
        return False
    if not text:
        return False
    try:
        data = json.loads(text)
    except ValueError:
        return False
    return bool(isinstance(data, dict) and isinstance(data.get("dir_info"), dict)
                and data["dir_info"].get("editable"))

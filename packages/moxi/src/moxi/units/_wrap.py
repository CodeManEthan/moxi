"""Wrapping a foreign systemd timer: its runs behind one of a module's switches.

`wrap(ref, module=, key=, name=)` puts the foreign timer `ref`'s runs behind `module`'s
switch `key`, with run lines recorded as `<module>/<name>`, by a marked drop-in on the
service the timer triggers. The drop-in copies nothing of the foreign unit, so a change to
that unit flows through at the next reload. It is the one file moxi writes for a foreign
unit, and only on systemd.

The core checks what is the same everywhere: `ref` as `foreign=True` checks a name, the
backend being systemd, `key` as a switch, `name` as an entry name that no declared daemon
or timer of the module has (their run lines share `STATE/<module>/runs.jsonl`), and the
module's other wraps: a second `wrap` of the same ref with the same key and name replaces
the first, while one with another key or name, one of another ref on a service the module
already wraps, and one whose record name another of its wraps uses are refused. The
backend reads the timer and its service and refuses what it finds there (see
`Backend.wrap`).

`unwrap(ref, module=)` removes the module's drop-in for `ref`, found by its mark, so it
works after the timer is gone. Both run under the core lock. `wraps(module)` reads the
module's drop-ins back without a supervisor call.
"""

from __future__ import annotations

from . import _backend
from ._apply import core_lock, fence as _fence
from ._types import Acted, DeclarationError, Wrapped


def wrap(ref: str, *, module: str, key: str, name: str, runner=None) -> Acted:
    """Wrap the foreign systemd timer `ref` behind `module`'s switch `key`, recording its
    runs as `<module>/<name>`. One `Acted` (act `wrap`); a refusal is `ok=False` with one
    line. On success its last line says how the key turns the runs on, when it is off."""
    with core_lock():
        impl = _implementation()
        problem = _refusal(impl, ref, module, key, name)
        if problem is None:
            fence = _fence(impl, runner)
            from ._act import foreign_unit

            unit = foreign_unit(impl, ref, fence)
            problem = unit if isinstance(unit, str) else _conflict(
                impl, ref, module, key, name, unit.ids[1] if len(unit.ids) == 2 else None)
        if problem is not None:
            return Acted(module, ref, "wrap", False, (problem,))
        acted = impl.wrap(ref, module=module, key=key, name=name, fence=fence)
        lines = list(acted.lines)
        if acted.ok:
            lines.append(_key_line(ref, module, key))
        return Acted(module, ref, "wrap", acted.ok, tuple(lines))


def unwrap(ref: str, *, module: str, runner=None) -> Acted:
    """Remove `module`'s drop-in for the foreign timer `ref`. A ref the module has not
    wrapped is `ok=True` with a line saying so; another module's drop-in and an unmarked
    file are never touched."""
    from .. import names

    names.check_module_name(module)
    with core_lock():
        impl = _implementation()
        if isinstance(impl, str) or impl.supervisor != "systemd":
            return Acted(module, ref, "unwrap", True, (f"{ref} is not wrapped by {module}",))
        acted = impl.unwrap(ref, module=module, fence=_fence(impl, runner))
        return Acted(module, ref, "unwrap", acted.ok, acted.lines)


def wraps(module: str) -> dict[str, Wrapped]:
    """`module`'s wraps, by the foreign timer each one wraps, read from the drop-ins with
    no supervisor call. Empty where there is no systemd."""
    impl = _implementation()
    if isinstance(impl, str) or impl.supervisor != "systemd":
        return {}
    return {ref: wrapped for ref, _, wrapped in impl.wraps(module)}


def _implementation():
    try:
        impl = _backend.implementation()
    except NotImplementedError as exc:
        return str(exc)
    return "there is no systemd user manager here" if impl is None else impl


def _refusal(impl, ref: str, module: str, key: str, name: str) -> str | None:
    """Steps 1 and 2 of a wrap, with no supervisor call: the line refusing it, or None."""
    from .. import config, modules, names
    from ._declare import merge, switch_problem

    if not isinstance(ref, str) or not ref:
        return f"a timer's name is a non-empty string, not {ref!r}"
    stem = ref.removesuffix(".timer")
    if isinstance(impl, str) or impl.supervisor != "systemd":
        return f"wrap is systemd only; {stem} runs as it is"
    if impl.own(ref):
        return (f"{ref} is in moxi's own unit namespace; wrap takes only a timer moxi did "
                "not write")
    if not ref.endswith(".timer"):
        return f"{ref} is not a timer; wrap takes a foreign .timer unit"
    if not names.is_module_name(module):
        return f"{module!r} is not a module name"
    found = modules.load(module)
    if found.state != modules.PRESENT:
        return f"{module} is {found.state}" + (f": {found.reason}" if found.reason else "")
    try:
        schema = config.schema_for(found)
    except config.ConfigError as exc:
        return f"{module}: its settings cannot be read, so {key} cannot be checked: {exc}"
    problem = switch_problem(schema, key)
    if problem is not None:
        return f"{module}: {problem}"
    if not isinstance(name, str) or not modules._UNIT_NAME.fullmatch(name):
        return (f"{name!r} is not a run record name: lower-case letters and digits, "
                "joined by single '-'")
    try:
        decl = merge(found)
    except DeclarationError as exc:
        return (f"{module}: UNITS_FROM failed ({exc.text}), so the record name {name} "
                "cannot be checked against its units; not wrapped")
    taken = {n: "daemon" for n in decl.daemons} | {n: "timer" for n in decl.timers}
    taken |= {n: k for k, n in decl.rejected if k in ("daemon", "timer")}
    if name in taken:
        return (f"{name} is a declared {taken[name]} of {module}, and their run lines "
                f"share STATE/{module}/runs.jsonl; choose another record name")
    return None


def _conflict(impl, ref: str, module: str, key: str, name: str,
              service: str | None) -> str | None:
    """The line refusing a wrap that conflicts with another wrap of `module`, or None."""
    for other, on, wrapped in impl.wraps(module):
        if other == ref:
            if (wrapped.key, wrapped.name) != (key, name):
                return (f"{ref} is wrapped by {module} with key {wrapped.key} and record "
                        f"{wrapped.name}; unwrap it first to change them")
            if service is not None and on != service:
                return (f"{ref} is wrapped by {module} on {on}, and now triggers "
                        f"{service}; unwrap it first")
        elif service is not None and on == service:
            return f"{service} is wrapped by {module} for {other}; unwrap it first"
        elif wrapped.name == name:
            return (f"the record name {name} is taken by {module}'s wrap of {other}; "
                    "choose another")
    return None


def _key_line(ref: str, module: str, key: str) -> str:
    from .. import config, modules

    on = False
    try:
        found = modules.load(module)
        schema = config.schema_for(found)
        on = schema is not None and bool(config.load(module, schema)[key])
    except (config.ConfigError, KeyError):
        pass
    if on:
        return f"{ref} wrapped; {module} {key} is on, so its runs go on"
    return (f"{ref} wrapped; its runs are off until: moxi config set {module} {key} "
            "true")

"""The acts on named units, the stop record and `resume`.

`start`, `stop`, `restart`, `enable` and `disable` act on named daemons and timers of a
module, one `Acted` per name. They take only units that carry the module's mark; a name
with none is an `Acted` with `ok=False`. What each act runs on each supervisor is the
backend's (`Backend.act`): after `enable` a daemon runs and a timer's schedule is armed,
after `disable` both are off; a timer's `start` runs it now through its gate and its
`stop` ends a run; on systemd a daemon's `.socket` goes with it.

With `foreign=True` the names are the backend's own names of units moxi did not write
(`backup.timer`, a launchd label, a task path). The acts only act on them: nothing writes,
plans or removes one. A name in moxi's own namespace (systemd `moxi-*`, launchd `moxi.*`,
a task under `\\Moxi\\`), marked or not, and a unit whose file carries a moxi mark are
refused, so `foreign=True` never reaches a unit the writer may own or displace.

The stop record. `stop(module, names, record=[])` first reads, in one supervisor call,
which of the units are running, and stops only those; before it asks the supervisor to
stop each one it appends a `Stopped` entry (the backend's exact id, a daemon's `.socket`
before its service) to `record`. `resume(record)` starts exactly those entries again, in
the order recorded, with the backend's start verb alone (never an enable verb, so a unit
the user disabled stays disabled), and only entries a `stop` of this process issued and
no `resume` has taken yet. A start the supervisor refuses is an `Acted` with `ok=False`
carrying its text and the command that starts the unit by hand, and `resume` goes on
with the next entry; it never retries. A start that succeeds clears the module's
`resume:<unit>` doctor record, left by an earlier install whose start of that unit
failed.

All of them run under the core lock (`moxi.units.core_lock`), which yields under an
install.
"""

from __future__ import annotations

import threading
from collections.abc import Iterable, Sequence

from . import _backend
from ._apply import core_lock, fence as _fence
from ._backend import ReadError, UnitRef
from ._fence import Fence, FenceError
from ._types import Acted, Stopped

#: The acts on named units.
ACTS = ("start", "stop", "restart", "enable", "disable")

#: The doctor record key `moxi install` leaves for a unit `resume` could not start.
RESUME_KEY = "resume:{unit}"

# The tokens `stop` issued in this process that no `resume` has taken yet, by id. The
# token itself is kept, so its id is never reused while it is outstanding.
_tokens: dict[int, object] = {}
_tokens_lock = threading.Lock()


def start(module: str, names, *, foreign: bool = False, runner=None) -> list[Acted]:
    """Start each named unit: a daemon now, a timer's run now through its gate."""
    return _act("start", module, names, foreign, runner)


def restart(module: str, names, *, foreign: bool = False, runner=None) -> list[Acted]:
    """Restart each named unit: a daemon, or a timer's run (`stop` then `start`)."""
    return _act("restart", module, names, foreign, runner)


def enable(module: str, names, *, foreign: bool = False, runner=None) -> list[Acted]:
    """Enable each named unit, taking effect now: a daemon runs, a timer's schedule is
    armed."""
    return _act("enable", module, names, foreign, runner)


def disable(module: str, names, *, foreign: bool = False, runner=None) -> list[Acted]:
    """Disable each named unit, taking effect now: a daemon is stopped, a timer's schedule
    is off (on Windows a timer's run in progress goes on; `stop` ends it)."""
    return _act("disable", module, names, foreign, runner)


def stop(module: str, names=None, *, replace: bool = False, record: list | None = None,
         foreign: bool = False, runner=None) -> list[Acted]:
    """Stop each named unit: a daemon, or a timer's run in progress.

    `names=None` takes every daemon `declarations(module)` reads, its rejected run-time
    daemons included (an older unit of one may be running), and raises its
    `DeclarationError`. With `record` (a list), `stop` reads which units run in one
    supervisor call, stops only those, and appends one `Stopped` per backend unit before
    stopping it. `replace=True` also takes the unmarked unit of a declared daemon's name
    (one the unit step will move aside); it needs a `record`, so an unmarked unit is
    never stopped without an entry that starts it again, and raises `ValueError`
    without one. `foreign=True` takes no record.
    """
    if replace and record is None:
        raise ValueError("stop(replace=True) needs a record, so an unmarked unit is never "
                         "stopped without an entry that starts it again")
    if foreign and (record is not None or replace):
        raise ValueError("the stop record and replace=True are for a module's own units, "
                         "not foreign=True")
    if names is None:
        if foreign:
            raise ValueError("stop(foreign=True) needs the names of the units")
        from ._declare import declarations

        decl = declarations(module)
        names = list(decl.daemons) + [name for kind, name in decl.rejected
                                      if kind == "daemon" and name not in decl.daemons]
    if record is None:
        return _act("stop", module, names, foreign, runner)
    return _stop_recorded(module, _names(names), replace, record, runner)


def resume(record: Iterable[Stopped], *, runner=None) -> list[Acted]:
    """Start again exactly the units `stop(..., record=...)` stopped, in the order
    recorded, one `Acted` (act `start`) per entry.

    Only an entry whose token a `stop` of this process issued and no `resume` took yet
    is started; it is taken as it is acted on. Any other entry (built by hand, or
    resumed already) is `ok=False`, "not stopped by this process", and is not started.
    A start the supervisor refuses, or that raises, is `ok=False` with its text and, as
    its last line, `start it by hand: <command>` (left out only when the backend raises
    before it can name the command); the next entry is still started. A
    start that succeeds clears the module's `resume:<unit>` doctor record.
    """
    entries = list(record)
    out: list[Acted] = []
    with core_lock():
        impl = None
        problem = None
        try:
            impl = _backend.implementation()
        except NotImplementedError as exc:
            problem = str(exc)
        fence = _fence(impl, runner) if impl is not None else None
        for entry in entries:
            out.append(_resume_one(entry, impl, fence, problem))
    return out


def _resume_one(entry, impl, fence, problem: str | None) -> Acted:
    if not isinstance(entry, Stopped) or not _take(entry.token):
        unit = getattr(entry, "unit", repr(entry))
        return Acted(getattr(entry, "module", None), getattr(entry, "name", str(unit)),
                     "start", False, (f"{unit}: not stopped by this process; not started",))
    if impl is None or impl.name != entry.backend:
        why = problem or ("no supervisor on this system" if impl is None
                          else f"this system's supervisor is {impl.name}")
        return Acted(entry.module, entry.name, "start", False,
                     (f"{entry.unit}: stopped on {entry.backend}, but {why}; not started",))
    try:
        ran = impl.start_id(entry.unit, fence)
    except Exception as exc:  # each entry's error stays in its own Acted
        text = str(exc).strip().splitlines()
        lines = [f"{entry.unit}: {type(exc).__name__}: {text[0] if text else ''}"
                 .rstrip(": ")]
        command = _start_command(impl, entry.unit)
        if command:
            lines.append(f"start it by hand: {command}")
        return Acted(entry.module, entry.name, "start", False, tuple(lines))
    if not ran.ok:
        return Acted(entry.module, entry.name, "start", False,
                     (f"{entry.unit}: {ran.failure()}", f"start it by hand: {ran.command}"))
    _clear_record(entry)
    return Acted(entry.module, entry.name, "start", True, (f"{entry.unit} started",))


def _start_command(impl, unit: str) -> str | None:
    """The command `impl.start_id(unit, ...)` runs, named without running anything: the
    start is asked again of a fence whose runner only notes the command. None when the
    backend raises before naming one."""
    import subprocess

    def note(cmd, *, env=None, timeout=None):
        return subprocess.CompletedProcess(cmd, 0, "", "")

    try:
        return impl.start_id(unit, Fence(impl.supervisor, note)).command
    except Exception:  # noqa: BLE001 - the start's own error is the line that counts
        return None


def _clear_record(entry: Stopped) -> None:
    from .. import doctor

    try:
        doctor.clear(entry.module, RESUME_KEY.format(unit=entry.unit))
    except (OSError, ValueError):
        pass  # a record left behind is cleared by the next clean install; the start stands


# -- the tokens ------------------------------------------------------------------------------

def _issue() -> object:
    token = object()
    with _tokens_lock:
        _tokens[id(token)] = token
    return token


def _take(token: object) -> bool:
    """Take `token` if it is outstanding; True once, False ever after."""
    with _tokens_lock:
        if token is None or _tokens.get(id(token)) is not token:
            return False
        del _tokens[id(token)]
        return True


# -- the acts --------------------------------------------------------------------------------

def _names(names) -> list[str]:
    if isinstance(names, str):
        return [names]
    out = list(names)
    for name in out:
        if not isinstance(name, str) or not name:
            raise ValueError(f"a unit name is a non-empty string, not {name!r}")
    return out


def _act(act: str, module: str, names, foreign: bool, runner) -> list[Acted]:
    names = _names(names)
    owner = None if foreign else module
    with core_lock():
        impl = _implementation()
        if isinstance(impl, str):
            return [Acted(owner, name, act, False, (f"{_label(module, name, foreign)}: "
                                                    f"{impl}; nothing to {act}",))
                    for name in names]
        fence = _fence(impl, runner)
        marked = () if foreign else impl.marked(module)
        out = []
        for name in names:
            unit = (foreign_unit(impl, name, fence) if foreign
                    else _marked_unit(marked, module, name))
            if isinstance(unit, str):
                out.append(Acted(owner, name, act, False, (unit,)))
                continue
            try:
                acted = impl.act(act, unit, fence)
            except FenceError as exc:  # a name the fence refuses is that name's refusal
                out.append(Acted(owner, name, act, False, (f"{name}: {exc}",)))
                continue
            out.append(Acted(owner, name, act, acted.ok, acted.lines))
        return out


def _implementation():
    """The backend, or the reason there is none, as a line."""
    try:
        impl = _backend.implementation()
    except NotImplementedError as exc:
        return str(exc)
    return "no supervisor on this system" if impl is None else impl


def _label(module: str, name: str, foreign: bool) -> str:
    return name if foreign else f"{module}/{name}"


def _marked_unit(marked: Sequence[UnitRef], module: str, name: str) -> UnitRef | str:
    for ref in marked:
        if ref.name == name and ref.kind in ("daemon", "timer"):
            return ref
    return (f"{module}/{name} has no unit written by moxi; `moxi install {module}` writes "
            "the units the module declares")


def foreign_unit(impl, ref: str, fence) -> UnitRef | str:
    """The `UnitRef` of foreign unit `ref`, or the line refusing it: a name in moxi's
    own namespace, a name the fence refuses, or a unit whose file carries a moxi mark."""
    if impl.own(ref):
        return (f"{ref} is in moxi's own unit namespace; foreign=True takes only units "
                "moxi did not write")
    try:
        unit = impl.foreign(ref, fence)
    except FenceError as exc:
        return f"{ref}: {exc}"
    for path in unit.paths:
        try:
            text = path.read_bytes().decode(impl.encoding, errors="replace")
        except OSError:
            continue
        mark = impl.mark(text)
        if mark:
            owner = mark.partition("/")[0]
            return (f"{ref} carries the mark of {mark}; it is {owner}'s own unit, not a "
                    "foreign one")
    return unit


def _stop_recorded(module: str, names: list[str], replace: bool, record: list,
                   runner) -> list[Acted]:
    with core_lock():
        impl = _implementation()
        if isinstance(impl, str):
            # Nothing can run without a supervisor, so nothing is stopped or recorded.
            return [Acted(module, name, "stop", True, (f"{module}/{name}: {impl}",))
                    for name in names]
        fence = _fence(impl, runner)
        marked = impl.marked(module)
        plans: list[tuple[str, list[tuple[str, bool]] | str]] = []
        for name in names:
            refs = [r for r in marked if r.name == name]
            if refs:
                ids: list[tuple[str, bool]] = []
                for ref in sorted(refs, key=lambda r: r.kind != "socket"):
                    # A timer's run is its service's; the .timer itself is left armed.
                    for unit in (ref.ids[1:] if ref.kind == "timer" else ref.ids):
                        ids.append((unit, True))
                plans.append((name, ids))
            elif replace:
                plans.append((name, [(unit, False)
                                     for unit in impl.ids(module, name, "daemon")]))
            else:
                plans.append((name, _marked_unit((), module, name)))
        every = [unit for _, ids in plans if not isinstance(ids, str) for unit, _ in ids]
        try:
            running = impl.running(every, fence) if every else frozenset()
        except ReadError as exc:
            line = f"cannot tell which units of {module} run: {exc}; nothing was stopped"
            return [Acted(module, name, "stop", False, (line,)) for name in names]
        out = []
        for name, ids in plans:
            if isinstance(ids, str):
                out.append(Acted(module, name, "stop", False, (ids,)))
                continue
            ok, lines = True, []
            for unit, is_marked in ids:
                if unit not in running:
                    continue
                record.append(Stopped(module, name, unit, impl.name, is_marked, _issue()))
                ran = impl.stop_id(unit, fence)
                if ran.ok:
                    lines.append(f"{unit} stopped")
                else:
                    ok = False
                    lines.append(f"{unit}: {ran.failure()}")
            if not lines:
                lines.append(f"{module}/{name} is not running")
            out.append(Acted(module, name, "stop", ok, tuple(lines)))
        return out

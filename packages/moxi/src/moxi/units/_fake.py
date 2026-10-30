"""A backend for tests: systemd-shaped files in a directory, and a simulated supervisor.

`FakeBackend(root)` implements the whole `Backend` protocol. It writes units as systemd
does, one `.service` per daemon, one socket unit per address beside it for a daemon with
a socket (`<stem>.socket`, then `<stem>-<k>.socket`), and a `.service` and `.timer` pair
per timer, all under `root`, and it runs every verb through the systemd fence. A daemon
with a socket is applied and acted on as on systemd: its socket units first, in unit
order, and its service, which has no install section, only started and stopped. `FakeSupervisor` is a runner for that fence: it keeps each unit's
state in memory, records every command in `calls`, and fails the commands a test names
in `fail`. Like systemd it refuses to start a socket unit while its service runs, and gives
a unit a new `InvocationID` each time it starts. Together they let a test drive `plan`, `apply`, `remove`, the acts, `status`
and `wrap` with no supervisor and no file outside its tmp:

    supervisor = FakeSupervisor(root)
    with moxi.units._backend.use(FakeBackend(root)):
        moxi.units.apply(moxi.units.plan("demo"), runner=supervisor)

The texts are not the systemd backend's: they carry the mark, the command and the
schedule in a form this module reads back, and nothing else.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

from ._backend import (
    ApplyIO, ReadError, Rendered, Spec, UnitRef, socket_name, socket_place, socket_state)
from ._fence import Fence, Ran
from ._types import Acted, UnitChange, UnitState, Wrapped

MARK = "# moxi unit: "
DISABLED = "# moxi: enabled = false"
WRAP_MARK = "# moxi wrap: "
_SUFFIXES = (".service", ".timer", ".socket")


class FakeSupervisor:
    """A runner for the systemd fence that simulates a user manager over `root`.

    `units` maps a unit id to `{"active", "enabled"}`; `loaded` is what the last
    `daemon-reload` found in `root` (plus `extra`, units a test declares outside it, such
    as a foreign timer's service). `triggers` maps a timer id to the unit it triggers
    (default `<stem>.service`; several separated by spaces, or none). `props` maps a unit
    id to properties `show` prints for it (`Type`, `ExecStart` as the number of its
    command lines, and `Result`, `ExecMainStatus` and `ActiveState`, which stand in for
    the ones its state gives, so a test can show a failed run), and `version` is the
    manager's `Version`. `fail` maps `(verb, unit)`
    (or `(verb, None)` for `daemon-reload` and `show`) to the stderr that command fails
    with. `calls` lists every command as a tuple of its arguments after `systemctl
    --user`, without the `--` the fence puts before the units. `invocations` maps a unit
    id to its `InvocationID`, new at each start; `props` may set `InvocationID` to show
    another.
    """

    def __init__(self, root: Path, *, linger: bool = True):
        self.root = Path(root)
        self.units: dict[str, dict] = {}
        self.loaded: set[str] = set()
        self.extra: set[str] = set()
        self.triggers: dict[str, str] = {}
        self.props: dict[str, dict] = {}
        self.version = "256.7-1"
        self.fail: dict[tuple[str, str | None], str] = {}
        self.calls: list[tuple[str, ...]] = []
        self.linger = linger
        self.invocations: dict[str, str] = {}
        self._starts = 0
        self.reload()

    def reload(self) -> None:
        self.loaded = {p.name for p in self.root.glob("*") if p.suffix in _SUFFIXES}
        self.loaded |= self.extra

    def state(self, unit: str) -> dict:
        return self.units.setdefault(unit, {"active": False, "enabled": False})

    def _start(self, unit: str, *, again: bool = False):
        """Start `unit` (again, for a restart); the stderr when systemd would refuse."""
        state = self.state(unit)
        if unit.endswith(".socket"):
            service = _socket_service(self.root / unit)
            if service and self.units.get(service, {}).get("active") and (
                    again or not state["active"]):
                return f"Socket service {service} already active, refusing."
        if again or not state["active"]:
            self._starts += 1
            self.invocations[unit] = f"{self._starts:032x}"
        state["active"] = True
        return None

    def __call__(self, cmd: list[str], *, env=None, timeout=None):
        import subprocess

        if cmd[0] == "loginctl":
            self.calls.append(tuple(cmd))
            return subprocess.CompletedProcess(cmd, 0, f"Linger={'yes' if self.linger else 'no'}\n", "")
        args = [arg for arg in cmd[2:] if arg != "--"]
        self.calls.append(tuple(args))
        verb, rest = args[0], args[1:]
        if verb == "daemon-reload":
            if (verb, None) in self.fail:
                return subprocess.CompletedProcess(cmd, 1, "", self.fail[(verb, None)])
            self.reload()
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if verb == "show":
            if (verb, None) in self.fail:
                return subprocess.CompletedProcess(cmd, 1, "", self.fail[(verb, None)])
            return subprocess.CompletedProcess(cmd, 0, self._show(rest), "")
        now = bool(rest) and rest[0] == "--now"
        names = rest[1:] if now else rest
        for unit in names:
            if (verb, unit) in self.fail:
                return subprocess.CompletedProcess(cmd, 1, "", self.fail[(verb, unit)])
            if unit not in self.loaded and verb != "stop" and not (
                    verb == "disable" and unit in self.units):
                return subprocess.CompletedProcess(cmd, 5, "",
                                                   f"Unit {unit} not found.")
            state = self.state(unit)
            refused = None
            if verb in ("enable", "disable"):
                state["enabled"] = verb == "enable"
                if now and verb == "enable":
                    refused = self._start(unit)
                elif now:
                    state["active"] = False
            elif verb in ("start", "restart"):
                refused = self._start(unit, again=verb == "restart")
            elif verb == "stop":
                state["active"] = False
            if refused:
                return subprocess.CompletedProcess(cmd, 1, "", refused)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    def _file_state(self, unit: str, state: dict) -> str:
        """`static` for a socket daemon's service, which has no install section (the
        fake's `sockets = ` line, or the systemd backend's `Sockets=`), else `enabled` or
        `disabled`."""
        if unit.endswith(".service"):
            try:
                text = (self.root / unit).read_text(encoding="utf-8")
            except OSError:
                text = ""
            if "\nsockets = " in text or "\nSockets=" in text:
                return "static"
        return "enabled" if state["enabled"] else "disabled"

    def _show(self, rest: list[str]) -> str:
        names = [a for a in rest if not a.startswith("--")]
        if not names:
            return f"Version={self.version}\n"
        blocks = []
        for unit in names:
            state = self.units.get(unit, {"active": False, "enabled": False})
            loaded = unit in self.loaded
            trigger = self.triggers.get(unit, unit.rsplit(".", 1)[0] + ".service"
                                        if unit.endswith(".timer") else "")
            dropins = sorted(str(p) for p in (self.root / f"{unit}.d").glob("*.conf"))
            props = self.props.get(unit, {})
            execs = [f"ExecStart={{ path=/bin/true ; argv[]=/bin/true {i} }}"
                     for i in range(props.get("ExecStart", 1 if loaded else 0))]
            active = props.get("ActiveState", "active" if state["active"] else "inactive")
            blocks.append("\n".join([
                f"Id={unit}",
                f"LoadState={'loaded' if loaded else 'not-found'}",
                f"ActiveState={active}",
                f"SubState={'running' if state['active'] else 'dead'}",
                f"MainPID={4242 if state['active'] and unit.endswith('.service') else 0}",
                "ActiveEnterTimestampMonotonic=0",
                f"Result={props.get('Result', 'success')}",
                f"ExecMainStatus={props.get('ExecMainStatus', 0)}",
                f"UnitFileState={self._file_state(unit, state)}",
                f"Triggers={trigger}",
                f"DropInPaths={' '.join(dropins)}",
                f"Type={props.get('Type', 'simple')}",
                f"InvocationID={props.get('InvocationID', self.invocations.get(unit, ''))}",
                *execs,
            ]))
        return "\n\n".join(blocks) + "\n"


class FakeBackend:
    """The `Backend` protocol over files in `root` and the systemd fence."""

    name = "fake"
    supervisor = "systemd"
    encoding = "utf-8"
    sockets = "units"

    def __init__(self, root: Path, *, gate: str = "/opt/moxi/bin/moxi",
                 runner: FakeSupervisor | None = None):
        self.root = Path(root)
        self.gate = gate
        #: The runner the core's fence uses when a call passes none.
        self.runner = runner if runner is not None else FakeSupervisor(self.root)

    # -- writing -----------------------------------------------------------------------------

    def _stem(self, module: str, name: str) -> str:
        return f"moxi-{module}-{name}"

    def render(self, spec: Spec) -> tuple[Rendered, ...]:
        stem = self._stem(spec.module, spec.name)
        head = [f"{MARK}{spec.unit}"] + ([DISABLED] if not spec.enabled else [])
        head.append(f"description = Moxi {spec.module}: {spec.doc}")
        if spec.kind == "daemon":
            body = head + [f"run = {' '.join(spec.command)}", f"stop = {spec.stop}"]
            if spec.cwd:
                body.append(f"cwd = {spec.cwd}")
            sockets = []
            if spec.socket is not None:
                for place, address in spec.socket.listens:
                    unit = f"{socket_name(stem, place)}.socket"
                    listen = f"[{address}]" if ":" in address else address
                    sockets.append((self.root / unit, _text(head + [
                        f"listen = {listen}:{spec.socket.port}",
                        f"service = {stem}.service"])))
                body.append("sockets = " + " ".join(path.name for path, _ in sockets))
            out = [Rendered("daemon", ((self.root / f"{stem}.service", _text(body)),))]
            if sockets:
                out.append(Rendered("socket", tuple(sockets)))
            return tuple(out)
        gate = [self.gate, "run"] + (["--if", f"{spec.module}.{spec.switch}"]
                                     if spec.switch else [])
        gate += ["--record", spec.unit, "--", *spec.command]
        service = head + [f"run = {' '.join(gate)}"]
        schedule = spec.schedule
        when = (f"every = {schedule.every}m" if schedule.every else
                "at = " + " ".join(f"{h:02d}:{m:02d}" for h, m in schedule.at)
                + ("" if schedule.daily else " on " + ",".join(schedule.days)))
        return (Rendered("timer", ((self.root / f"{stem}.service", _text(service)),
                                   (self.root / f"{stem}.timer", _text(head + [when])))),)

    def mark(self, text: str) -> str | None:
        first = text.split("\n", 1)[0]
        return first[len(MARK):].strip() if first.startswith(MARK) else None

    def enabled(self, text: str) -> bool:
        return DISABLED not in text.split("\n")[:3]

    def marked(self, module: str) -> tuple[UnitRef, ...]:
        found: dict[tuple[str, str], list[Path]] = {}
        texts: dict[Path, str] = {}
        for path in sorted(self.root.glob("moxi-*")):
            if path.suffix not in _SUFFIXES or not path.is_file():
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            mark = self.mark(text)
            if not mark or mark.split("/", 1)[0] != module:
                continue
            texts[path] = text
            name = mark.split("/", 1)[1]
            found.setdefault((name, path.suffix), []).append(path)
        out = []
        for (name, suffix), paths in sorted(found.items()):
            path = paths[0]
            if suffix == ".socket":
                stem = self._stem(module, name)
                placed = sorted((place, p) for p in paths
                                if (place := socket_place(stem, p.stem)) is not None)
                if placed:
                    files = tuple(p for _, p in placed)
                    out.append(UnitRef(module, name, "socket",
                                       tuple(p.name for p in files), files,
                                       all(self.enabled(texts[p]) for p in files)))
            elif suffix == ".timer":
                service = path.with_suffix(".service")
                files = (service, path) if service in texts else (path,)
                out.append(UnitRef(module, name, "timer", (path.name, service.name), files,
                                   all(self.enabled(texts[p]) for p in files)))
            elif (name, ".timer") not in found:
                out.append(UnitRef(module, name, "daemon", (path.name,), (path,),
                                   self.enabled(texts[path])))
        return tuple(out)

    def _enable_id(self, change: UnitChange) -> str:
        stem = self._stem(change.module, change.name)
        return {"daemon": f"{stem}.service", "socket": f"{stem}.socket",
                "timer": f"{stem}.timer"}[change.kind]

    def apply(self, changes: Sequence[UnitChange], *, restart: bool, io: ApplyIO) -> None:
        fence = io.fence
        removes = [c for c in changes if c.action == "remove"]
        writes = [c for c in changes if c.action in ("add", "change")]
        keeps = [c for c in changes if c.action == "keep"]
        sockets = {(c.module, c.name): c for c in changes if c.kind == "socket"
                   and c.action in ("add", "change", "keep")}
        for change in removes:
            for unit in self._ids_of(change):
                ran = fence.run("disable", "--now", unit)
                if not ran.ok:
                    io.fail(change, ran, "the unit is left in place; fix the cause and run "
                                         f"`moxi install {change.module}` again")
        for change in writes:
            for path in change.dropped:
                ran = fence.run("disable", "--now", path.name)
                if not ran.ok:
                    io.fail(change, ran)
        # Which socket units are new or changed, read before they are written.
        news = {path: _differs(path, text) for change in writes if change.kind == "socket"
                for path, text in zip(change.paths, change.texts)}
        running = self._running_services(writes, sockets, fence)
        for change in removes:
            io.delete(change)
        for change in writes:
            io.drop(change)
            io.write(change)
        if removes or writes:
            ran = fence.run("daemon-reload")
            if not ran.ok:
                for change in writes:
                    io.fail(change, ran)
                return
        stopped: set[str] = set()
        for change in writes:
            if change.kind != "socket" or not change.enabled:
                continue
            service = f"{self._stem(change.module, change.name)}.service"
            if service in running and change.action != "keep":
                # systemd won't start a socket while its service runs.
                if self._run(io, change, "stop", service):
                    stopped.add(service)
            for path in change.paths:
                if news[path] == "new":
                    self._run(io, change, "enable", "--now", path.name)
                elif news[path] == "changed" and self._run(io, change, "enable", path.name):
                    self._run(io, change, "restart", path.name)
        for change in writes:
            if change.kind == "socket":
                if not change.enabled and change.action == "change" and change.enabled_before:
                    for path in change.paths:
                        self._run(io, change, "disable", "--now", path.name)
                continue
            unit = self._enable_id(change)
            socketed = change.kind == "daemon" and (change.module, change.name) in sockets
            if not change.enabled:
                if change.action == "change" and change.enabled_before:
                    self._run(io, change, "stop" if socketed else "disable",
                              *(() if socketed else ("--now",)), unit)
                continue
            keep = change.kind == "daemon" and change.entry.get("upgrade") == "keep"
            if socketed:
                # The service has no install section: started and stopped, never enabled.
                down = unit not in running or unit in stopped
                if keep and not down:
                    io.say(change, f"{unit} rewritten; the new code runs from its next "
                                   "start")
                    continue
                if down:
                    self._listen(io, change, sockets[(change.module, change.name)], news)
                self._run(io, change, "start" if down else "restart", unit)
                stopped.discard(unit)
                continue
            if change.action == "add" or change.enabled_before is False:
                self._run(io, change, "enable", "--now", unit)
            elif keep:
                if self._run(io, change, "enable", unit):
                    io.say(change, f"{unit} rewritten; the new code runs from its next "
                                   "start")
            elif self._run(io, change, "enable", unit):
                self._run(io, change, "restart", unit)
        for service in sorted(stopped):
            change = next(c for c in writes if c.kind == "socket"
                          and f"{self._stem(c.module, c.name)}.service" == service)
            self._listen(io, change, change, news)
            self._run(io, change, "start", service)
        if restart:
            daemons = [c for c in keeps if c.kind == "daemon" and c.enabled
                       and c.entry.get("upgrade", "restart") == "restart"]
            try:
                running = self.running([self._enable_id(c) for c in daemons], fence)
            except ReadError as exc:
                for change in daemons:
                    io.fail(change, exc.ran)
                return
            for change in daemons:
                if self._enable_id(change) in running:
                    self._run(io, change, "restart", self._enable_id(change))

    def _ids_of(self, change: UnitChange) -> tuple[str, ...]:
        """The ids a `remove` disables: each socket unit of a set, else its one unit."""
        if change.kind == "socket":
            return tuple(path.name for path in change.paths)
        return (self._enable_id(change),)

    def _listen(self, io: ApplyIO, change: UnitChange, socket: UnitChange, news) -> None:
        """Start each unit of a daemon's socket set this apply did not just start, before
        its service starts: a socket stopped by someone else (the home move) listens
        again, and systemd won't start it once the service runs."""
        if not socket.enabled:
            return
        for path in socket.paths:
            if news.get(path, "same") == "same":
                self._run(io, change, "start", path.name)

    def _running_services(self, writes, sockets, fence) -> frozenset[str]:
        """The services of the daemons with a socket among `writes` that run now: a
        changed set is applied around one stop of its service, and a service that is
        down has its sockets started before it."""
        ids = []
        for change in writes:
            if (change.module, change.name) not in sockets or not change.enabled:
                continue
            service = f"{self._stem(change.module, change.name)}.service"
            if service not in ids:
                ids.append(service)
        if not ids:
            return frozenset()
        try:
            return self.running(ids, fence)
        except ReadError:
            return frozenset()

    def _run(self, io: ApplyIO, change: UnitChange, verb: str, *args: str) -> bool:
        ran = io.fence.run(verb, *args)
        if not ran.ok:
            io.fail(change, ran)
        return ran.ok

    # -- acting and reading ----------------------------------------------------------------

    def own(self, ref: str) -> bool:
        return ref.startswith("moxi-")

    def foreign(self, ref: str, fence: Fence | None) -> UnitRef:
        if ref.endswith(".timer"):
            if fence is None:
                return UnitRef(None, ref, "timer", (ref,))
            ran = fence.run("show", "--property=Triggers", ref)
            trigger = _blocks(ran.stdout)[0].get("Triggers", "") if ran.ok else ""
            return UnitRef(None, ref, "timer", (ref,) + ((trigger,) if trigger else ()))
        return UnitRef(None, ref, "daemon", (ref,))

    def ids(self, module: str, name: str, kind: str) -> tuple[str, ...]:
        stem = self._stem(module, name)
        if kind == "timer":
            return (f"{stem}.timer", f"{stem}.service")
        return (f"{stem}.socket", *self._more_sockets(stem), f"{stem}.service")

    def files(self, module: str, name: str, kind: str) -> tuple[Path, ...]:
        stem = self._stem(module, name)
        if kind == "timer":
            names = (f"{stem}.service", f"{stem}.timer")
        else:
            names = (f"{stem}.service", f"{stem}.socket", *self._more_sockets(stem))
        return tuple(self.root / unit for unit in names)

    def _more_sockets(self, stem: str) -> tuple[str, ...]:
        """The `<stem>-<k>.socket` files there, in unit order."""
        placed = []
        for path in self.root.glob(f"{stem}-*.socket"):
            place = socket_place(stem, path.stem)
            if place:
                placed.append((place, path.name))
        return tuple(name for _, name in sorted(placed))

    def _socket_of(self, unit: UnitRef) -> tuple[str, ...]:
        """A marked daemon's socket units, in unit order."""
        if unit.kind != "daemon" or not unit.marked:
            return ()
        for ref in self.marked(unit.module):
            if ref.kind == "socket" and ref.name == unit.name:
                return ref.ids
        return ()

    def act(self, act: str, unit: UnitRef, fence: Fence) -> Acted:
        lines: list[str] = []
        ok = True

        def run(verb, *args):
            nonlocal ok
            ran = fence.run(verb, *args)
            if not ran.ok:
                ok = False
                lines.append(ran.failure())
            return ran.ok

        both = self._socket_of(unit) + unit.ids[:1]
        if unit.kind == "timer":
            service = unit.ids[1:2]
            if act in ("start", "stop") and service:
                run(act, *service)
            elif act == "restart" and service:
                run("stop", *service) and run("start", *service)
            elif act in ("enable", "disable"):
                run(act, "--now", unit.ids[0])
            else:
                ok, lines = False, [f"{unit.name} triggers no unit"]
        elif act in ("start", "stop", "enable", "disable"):
            sockets = self._socket_of(unit)
            if sockets and act in ("enable", "disable"):
                # Its service has no install section: the sockets carry the enable.
                run(act, "--now", *sockets) and run(
                    "start" if act == "enable" else "stop", *unit.ids[:1])
            elif act in ("enable", "disable"):
                run(act, "--now", *both)
            else:
                run(act, *both)
        elif act == "restart":
            run("restart", *unit.ids[:1])
        return Acted(unit.module, unit.name, act, ok, tuple(lines))

    def running(self, ids: Sequence[str], fence: Fence) -> frozenset[str]:
        if not ids:
            return frozenset()
        ran = fence.run("show", "--property=Id,ActiveState", *ids)
        if not ran.ok:
            raise ReadError(ran)
        return frozenset(b.get("Id", "") for b in _blocks(ran.stdout)
                         if b.get("ActiveState") in ("active", "activating", "reloading"))

    def stop_id(self, unit: str, fence: Fence) -> Ran:
        return fence.run("stop", unit)

    def start_id(self, unit: str, fence: Fence) -> Ran:
        return fence.run("start", unit)

    def status(self, units: Sequence[UnitRef], fence: Fence) -> list[UnitState]:
        ids = sorted({i for u in units for i in u.ids})
        if not ids:
            return []
        props = ("--property=Id,LoadState,ActiveState,MainPID,Result,ExecMainStatus,"
                 "UnitFileState,Triggers,DropInPaths,InvocationID")
        ran = fence.run("show", props, *ids)
        shown = {b.get("Id"): b for b in _blocks(ran.stdout)} if ran.ok else {}
        # A foreign timer given by its .timer alone: the unit its Triggers names, read in
        # one more call for all of them.
        triggered: dict[str, str] = {}
        for unit in units:
            if unit.kind == "timer" and not unit.marked and len(unit.ids) == 1:
                trigger = shown.get(unit.ids[0], {}).get("Triggers", "").split()
                if len(trigger) == 1:
                    triggered[unit.ids[0]] = trigger[0]
        if triggered:
            more = fence.run("show", props, *sorted(set(triggered.values())))
            if more.ok:
                shown.update({b.get("Id"): b for b in _blocks(more.stdout)})
        out = []
        for unit in units:
            if unit.kind == "socket" and ran.ok:
                out.append(socket_state(unit, self.name, [
                    _socket_read(i, shown.get(i, {})) for i in unit.ids], self.log(unit)))
                continue
            head = shown.get(unit.ids[0], {})
            service = shown.get(triggered.get(unit.ids[0], unit.ids[-1]), {})
            if not ran.ok:
                state, detail = "unknown", ran.failure()
            elif head.get("LoadState") == "not-found":
                state, detail = "missing", "not-found"
            else:
                active = head.get("ActiveState", "")
                state = ("active" if active in ("active", "activating", "reloading")
                         else "failed" if active == "failed" else "inactive")
                detail = f"{active}/{head.get('UnitFileState', '')}"
            raw = head.get("UnitFileState")
            enabled = {"enabled": True, "disabled": False}.get(raw) if state not in (
                "missing", "unknown") else None
            pid = int(service.get("MainPID", "0") or 0) or None
            last = ({"result": service.get("Result"),
                     "status": service.get("ExecMainStatus")}
                    if unit.kind == "timer" and service else None)
            named = (head.get("Triggers", "").split()
                     if unit.kind == "timer" and state != "missing" else [])
            out.append(UnitState(unit.module, unit.name, unit.kind, self.name, state,
                                 pid=pid, last=last, detail=detail,
                                 log=None if state == "missing" else self.log(unit),
                                 enabled=enabled, wrapped=self._wrapped(service),
                                 triggers=named[0] if named else None))
        return out

    def log(self, unit: UnitRef) -> str | None:
        if unit.kind == "socket":
            return "journalctl --user " + " ".join(f"-u {i}" for i in unit.ids)
        return f"journalctl --user -u {unit.ids[0]}"

    # -- wraps -------------------------------------------------------------------------------

    def _dropin(self, service: str, module: str) -> Path:
        return self.root / f"{service}.d" / f"50-moxi-{module}.conf"

    def _wrapped(self, shown: Mapping):
        from ._types import Wrapped

        for path in (shown.get("DropInPaths") or "").split():
            try:
                lines = Path(path).read_text(encoding="utf-8").splitlines()
            except OSError:
                continue
            if lines and lines[0].startswith(WRAP_MARK):
                module, _, name = lines[0][len(WRAP_MARK):].partition("/")
                key = lines[1].rpartition("if = ")[2].partition(".")[2]
                return Wrapped(module, key, name)
        return None

    def dropin_text(self, ref: str, *, module: str, key: str, name: str) -> str:
        record = f"{module}/{name}"
        return _text([f"{WRAP_MARK}{record}",
                      f"# moxi: timer = {ref}, if = {module}.{key}",
                      "[Service]",
                      f"ExecCondition={self.gate} run --if {module}.{key} --record {record} "
                      "--condition",
                      f"ExecStopPost={self.gate} run --record {record} --result"])

    def wrap(self, ref: str, *, module: str, key: str, name: str, fence: Fence) -> Acted:
        from .. import files

        def no(line: str) -> Acted:
            return Acted(None, ref, "wrap", False, (line,))

        ran = fence.run("show", "--property=Version")
        if not ran.ok:
            return no(ran.failure())
        version = _blocks(ran.stdout)[0].get("Version", "") if ran.stdout.strip() else ""
        major = int(version.split(".")[0].split("-")[0] or 0) if version[:1].isdigit() else 0
        if major < 243:
            return no(f"systemd {major} has no ExecCondition= (it needs 243); not wrapped")
        ran = fence.run("show", "--property=LoadState,Triggers", ref)
        if not ran.ok:
            return no(ran.failure())
        timer = _blocks(ran.stdout)[0] if ran.stdout.strip() else {}
        if timer.get("LoadState") != "loaded":
            return no(f"{ref} is not loaded ({timer.get('LoadState', 'unknown')}); "
                      "not wrapped")
        triggers = timer.get("Triggers", "").split()
        if len(triggers) != 1 or not triggers[0].endswith(".service"):
            return no(f"{ref} triggers {' '.join(triggers) or 'nothing'}; wrap needs "
                      "exactly one service")
        service = triggers[0]
        ran = fence.run("show", "--property=Type,ExecStart,DropInPaths", service)
        if not ran.ok:
            return no(ran.failure())
        shown = _blocks(ran.stdout)[0] if ran.stdout.strip() else {}
        execs = sum(line.startswith("ExecStart=") for line in ran.stdout.splitlines())
        if execs != 1:
            return no(f"{service} has {execs} ExecStart lines; wrap needs exactly one")
        if shown.get("Type") not in ("oneshot", "simple", "exec"):
            return no(f"{service} is Type={shown.get('Type')}; wrap needs oneshot, simple "
                      "or exec")
        for other in shown.get("DropInPaths", "").split():
            mark = _first_line(Path(other))
            if mark.startswith(WRAP_MARK):
                owner = mark[len(WRAP_MARK):].partition("/")[0]
                if owner != module:
                    return no(f"{service} is wrapped by {owner}")
        path = self._dropin(service, module)
        if path.exists() and not _first_line(path).startswith(f"{WRAP_MARK}{module}/"):
            return no(f"{path} exists and was not written by moxi; not wrapped")
        files.atomic_write(path, self.dropin_text(ref, module=module, key=key,
                                                  name=name).encode("utf-8"), mode=0o644)
        ran = fence.run("daemon-reload")
        return Acted(None, ref, "wrap", ran.ok, () if ran.ok else (ran.failure(),))

    def wraps(self, module: str) -> tuple[tuple[str, str, Wrapped], ...]:
        out = []
        for path in sorted(self.root.glob(f"*.d/50-moxi-{module}.conf")):
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except OSError:
                continue
            if len(lines) < 2 or not lines[0].startswith(f"{WRAP_MARK}{module}/"):
                continue
            name = lines[0][len(f"{WRAP_MARK}{module}/"):].strip()
            ref = lines[1].partition("timer = ")[2].partition(",")[0].strip()
            key = lines[1].partition(f"if = {module}.")[2].strip()
            out.append((ref, path.parent.name.removesuffix(".d"),
                        Wrapped(module, key, name)))
        return tuple(out)

    def unwrap(self, ref: str, *, module: str, fence: Fence) -> Acted:
        for path in self.root.glob(f"*.d/50-moxi-{module}.conf"):
            lines = path.read_text(encoding="utf-8").splitlines()
            if len(lines) > 1 and lines[0].startswith(f"{WRAP_MARK}{module}/") \
                    and f"timer = {ref}," in lines[1]:
                _unlink(path)
                ran = fence.run("daemon-reload")
                return Acted(None, ref, "unwrap", ran.ok, () if ran.ok else (ran.failure(),))
        return Acted(None, ref, "unwrap", True, (f"{ref} is not wrapped by {module}",))

    def unwrap_all(self, module: str, fence: Fence) -> list[Acted]:
        out = []
        for path in sorted(self.root.glob(f"*.d/50-moxi-{module}.conf")):
            lines = path.read_text(encoding="utf-8").splitlines()
            if not lines or not lines[0].startswith(f"{WRAP_MARK}{module}/"):
                continue
            ref = lines[1].partition("timer = ")[2].partition(",")[0]
            _unlink(path)
            out.append(Acted(None, ref, "unwrap", True))
        if out:
            ran = fence.run("daemon-reload")
            if not ran.ok:
                out = [Acted(None, a.name, "unwrap", False, (ran.failure(),)) for a in out]
        return out


def _text(lines: list[str]) -> str:
    return "\n".join(lines) + "\n"


def _differs(path: Path, text: str) -> str:
    """`new`, `changed` or `same`: how `text` stands to the file at `path` now."""
    try:
        old = path.read_bytes()
    except FileNotFoundError:
        return "new"
    return "same" if old == text.encode("utf-8") else "changed"


def _socket_service(path: Path) -> str | None:
    """The service a fake socket unit at `path` names."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in lines:
        if line.startswith("service = "):
            return line[len("service = "):]
        if line.startswith("Service="):  # the systemd backend's text
            return line[len("Service="):]
    return None


def _socket_read(unit: str, block: Mapping) -> tuple[str, str, bool | None, None, str]:
    """One socket unit's read, as `socket_state` takes it."""
    active = block.get("ActiveState", "")
    state = ("missing" if block.get("LoadState") == "not-found"
             else "active" if active in ("active", "activating", "reloading")
             else "failed" if active == "failed" else "inactive")
    enabled = {"enabled": True, "disabled": False}.get(block.get("UnitFileState", ""))
    return (unit, state, enabled, None, block.get("InvocationID", ""))


def _unlink(path: Path) -> None:
    """Delete a drop-in, and its folder when that leaves it empty."""
    path.unlink()
    try:
        path.parent.rmdir()
    except OSError:
        pass


def _first_line(path: Path) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.readline().rstrip("\n")
    except OSError:
        return ""


def _blocks(text: str) -> list[dict[str, str]]:
    """`systemctl show` output: blank-line-separated blocks of `key=value` lines."""
    out = []
    for block in text.strip().split("\n\n"):
        fields = {}
        for line in block.splitlines():
            key, sep, value = line.partition("=")
            if sep:
                fields[key] = value
        if fields:
            out.append(fields)
    return out

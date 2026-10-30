"""A backend for tests: systemd-shaped files in a directory, and a simulated supervisor.

`FakeBackend(root)` implements the whole `Backend` protocol. It writes units as systemd
does, one `.service` per daemon, a `.socket` beside it for a daemon with a socket, and a
`.service` and `.timer` pair per timer, all under `root`, and it runs every verb through
the systemd fence. `FakeSupervisor` is a runner for that fence: it keeps each unit's
state in memory, records every command in `calls`, and fails the commands a test names
in `fail`. Together they let a test drive `plan`, `apply`, `remove`, the acts, `status`
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

from ._backend import ApplyIO, ReadError, Rendered, Spec, UnitRef
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
    id to properties `show` prints for it (`Type`, and `ExecStart` as the number of its
    command lines), and `version` is the manager's `Version`. `fail` maps `(verb, unit)`
    (or `(verb, None)` for `daemon-reload` and `show`) to the stderr that command fails
    with. `calls` lists every command as a tuple of its arguments after `systemctl
    --user`, without the `--` the fence puts before the units.
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
        self.reload()

    def reload(self) -> None:
        self.loaded = {p.name for p in self.root.glob("*") if p.suffix in _SUFFIXES}
        self.loaded |= self.extra

    def state(self, unit: str) -> dict:
        return self.units.setdefault(unit, {"active": False, "enabled": False})

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
            if verb in ("enable", "disable"):
                state["enabled"] = verb == "enable"
                if now:
                    state["active"] = verb == "enable"
            elif verb in ("start", "restart"):
                state["active"] = True
            elif verb == "stop":
                state["active"] = False
        return subprocess.CompletedProcess(cmd, 0, "", "")

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
            blocks.append("\n".join([
                f"Id={unit}",
                f"LoadState={'loaded' if loaded else 'not-found'}",
                f"ActiveState={'active' if state['active'] else 'inactive'}",
                f"SubState={'running' if state['active'] else 'dead'}",
                f"MainPID={4242 if state['active'] and unit.endswith('.service') else 0}",
                "ActiveEnterTimestampMonotonic=0",
                "Result=success",
                "ExecMainStatus=0",
                f"UnitFileState={'enabled' if state['enabled'] else 'disabled'}",
                f"Triggers={trigger}",
                f"DropInPaths={' '.join(dropins)}",
                f"Type={props.get('Type', 'simple')}",
                *execs,
            ]))
        return "\n\n".join(blocks) + "\n"


class FakeBackend:
    """The `Backend` protocol over files in `root` and the systemd fence."""

    name = "fake"
    supervisor = "systemd"
    encoding = "utf-8"

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
            out = [Rendered("daemon", ((self.root / f"{stem}.service", _text(body)),))]
            if spec.socket is not None:
                listen = [f"listen = {a}:{spec.socket.port}" for a in spec.socket.addresses]
                out.append(Rendered("socket", ((self.root / f"{stem}.socket",
                                                _text(head + listen)),)))
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
                out.append(UnitRef(module, name, "socket", (path.name,), (path,),
                                   self.enabled(texts[path])))
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
        for change in removes:
            ran = fence.run("disable", "--now", self._enable_id(change))
            if not ran.ok:
                io.fail(change, ran, "the unit is left in place; fix the cause and run "
                                     f"`moxi install {change.module}` again")
        for change in removes:
            io.delete(change)
        for change in writes:
            io.write(change)
        if removes or writes:
            ran = fence.run("daemon-reload")
            if not ran.ok:
                for change in writes:
                    io.fail(change, ran)
                return
        # A daemon's socket is started before its service.
        for change in sorted(writes, key=lambda c: c.kind != "socket"):
            unit = self._enable_id(change)
            if not change.enabled:
                if change.action == "change" and change.enabled_before:
                    self._run(io, change, "disable", "--now", unit)
                continue
            if change.action == "add" or change.enabled_before is False:
                self._run(io, change, "enable", "--now", unit)
            elif change.kind == "daemon" and change.entry.get("upgrade") == "keep":
                if self._run(io, change, "enable", unit):
                    io.say(change, f"{unit} rewritten; the new code runs from its next "
                                   "start")
            elif self._run(io, change, "enable", unit):
                self._run(io, change, "restart", unit)
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
        return (f"{stem}.socket", f"{stem}.service")

    def files(self, module: str, name: str, kind: str) -> tuple[Path, ...]:
        return tuple(self.root / unit for unit in self.ids(module, name, kind))

    def _socket_of(self, unit: UnitRef) -> tuple[str, ...]:
        if unit.kind != "daemon" or not unit.marked:
            return ()
        socket = unit.ids[0].rsplit(".", 1)[0] + ".socket"
        return (socket,) if (self.root / socket).is_file() else ()

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
            if act in ("enable", "disable"):
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
                 "UnitFileState,Triggers,DropInPaths")
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
            out.append(UnitState(unit.module, unit.name, unit.kind, self.name, state,
                                 pid=pid, last=last, detail=detail,
                                 log=None if state == "missing" else self.log(unit),
                                 enabled=enabled, wrapped=self._wrapped(service)))
        return out

    def log(self, unit: UnitRef) -> str | None:
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

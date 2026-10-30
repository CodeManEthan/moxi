"""The systemd backend: a module's units as systemd user units.

Files go in the user unit directory (`$XDG_CONFIG_HOME`, else `~/.config`, then
`/systemd/user`), 0644, each written in one step. A daemon is `moxi-<module>-<name>.service`;
a daemon with a `socket` adds `moxi-<module>-<name>.socket`; a timer is a `.service` and
`.timer` pair whose service runs the gate, `moxi run`. Every file starts with the mark
`# moxi unit: <module>/<name>`, and a unit declared `enabled = false` carries a second line
`# moxi: enabled = false`. The text uses absolute paths resolved now, no specifier, and
copies no variable of this shell.

Every supervisor call goes through the systemd fence (`systemctl --user`, always), so the
verbs here are the fence's closed set. A wrap of a foreign timer is the one file this
backend writes for a unit it did not write: a marked drop-in on the service the timer
triggers, `<service>.d/50-moxi-<module>.conf`, which puts the timer's runs behind one of
the module's switches and records each run, and copies nothing of the foreign unit.
"""

from __future__ import annotations

import ipaddress
import os
import re
import time
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path

from ._backend import ApplyIO, ReadError, Rendered, RenderError, Spec, UnitRef
from ._fence import Fence, Ran
from ._types import Acted, UnitChange, UnitState, Wrapped

MARK = "# moxi unit: "
DISABLED = "# moxi: enabled = false"
WRAP_MARK = "# moxi wrap: "
_WRAP_LINE = re.compile(r"# moxi: timer = (?P<ref>\S+), if = (?P<switch>\S+)")
_DROPIN = re.compile(r"50-moxi-(?P<module>[^/]+)\.conf")
_SUFFIXES = (".service", ".timer", ".socket")
_DAY_NAMES = {"mon": "Mon", "tue": "Tue", "wed": "Wed", "thu": "Thu", "fri": "Fri",
              "sat": "Sat", "sun": "Sun"}
#: The oldest systemd whose services take `ExecCondition=`, which a wrap needs.
WRAP_VERSION = 243
#: The service types a wrap takes: one main process, whose exit `$EXIT_STATUS` gives.
WRAP_TYPES = ("oneshot", "simple", "exec")
#: The properties `status` reads, in one `show`.
STATUS_PROPERTIES = ("Id,LoadState,ActiveState,SubState,MainPID,"
                     "ActiveEnterTimestampMonotonic,Result,ExecMainStatus,NRestarts,"
                     "ControlGroup,UnitFileState,Triggers,DropInPaths")

LINGER = (
    "The daemon now starts when you log in and stops when your last session ends. To keep "
    "it running while you are logged out (a headless box, work left running overnight), "
    "enable lingering for your user:",
    "    loginctl enable-linger {user}",
    "Lingering starts your user services at boot and keeps them after logout. It is a "
    "login policy for your account on the whole machine, so moxi does not turn it on for "
    "you.")

# A word systemd reads as it is, with no quotes: nothing that splits, quotes, escapes or
# separates commands. `%` and `$` are doubled whether quoted or not.
_PLAIN = re.compile(r"[A-Za-z0-9_@%$+=:,./-]+")


def unit_dir(env: Mapping[str, str] | None = None) -> Path:
    """The user unit directory: `$XDG_CONFIG_HOME/systemd/user`, else
    `~/.config/systemd/user`."""
    environ = os.environ if env is None else env
    base = environ.get("XDG_CONFIG_HOME") or os.path.join(
        environ.get("HOME") or os.path.expanduser("~"), ".config")
    return Path(base) / "systemd" / "user"


def quote(arg: str) -> str:
    """One argument of an `Exec*=` line, by systemd's rules: `%` and `$` doubled, and a
    word that holds anything but plain characters put in double quotes, with `\\` and `"`
    escaped and a control character written as a C escape."""
    text = arg.replace("%", "%%").replace("$", "$$")
    if arg and _PLAIN.fullmatch(arg):
        return text
    out = []
    for ch in text:
        if ch in '\\"':
            out.append("\\" + ch)
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\t":
            out.append("\\t")
        elif ord(ch) < 0x20 or ord(ch) == 0x7F:
            out.append(f"\\x{ord(ch):02x}")
        else:
            out.append(ch)
    return '"' + "".join(out) + '"'


def command_line(argv: Sequence[str]) -> str:
    """An argv as the value of an `Exec*=` line."""
    return " ".join(quote(a) for a in argv)


def _value(text: str) -> str:
    """A free-text value (a description): one line, `%` doubled, no trailing `\\` (which
    would join the next line to it)."""
    flat = "".join(" " if ord(ch) < 0x20 or ord(ch) == 0x7F else ch for ch in text)
    return flat.strip().rstrip("\\").replace("%", "%%")


def _path_value(path: str, what: str) -> str:
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in path) or path.endswith("\\"):
        raise RenderError("declaration", f"its {what} {path!r} cannot be written in a "
                                         "unit file")
    return path.replace("%", "%%")


def _listen(address: str, port: int) -> str:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return f"{address}:{port}"
    return f"[{ip}]:{port}" if ip.version == 6 else f"{ip}:{port}"


def _user() -> str | None:
    """This account's name, for the lingering read."""
    try:
        import pwd

        return pwd.getpwuid(os.getuid()).pw_name
    except (ImportError, KeyError):
        return os.environ.get("USER") or os.environ.get("LOGNAME")


def _blocks(text: str) -> list[dict[str, list[str]]]:
    """`systemctl show` output: blank-line-separated blocks of `key=value` lines. A key may
    repeat (one `ExecStart=` line per command), so each value is a list."""
    out = []
    for block in re.split(r"\n\s*\n", text.strip()):
        fields: dict[str, list[str]] = {}
        for line in block.splitlines():
            key, sep, value = line.partition("=")
            if sep:
                fields.setdefault(key, []).append(value)
        if fields:
            out.append(fields)
    return out


def _one(block: Mapping[str, list[str]], key: str, default: str = "") -> str:
    values = block.get(key)
    return values[0] if values else default


def _by_id(ran: Ran, ids: Sequence[str]) -> dict[str, dict[str, list[str]]]:
    """The blocks of a `show` keyed by unit id: by each block's `Id` when it has one, else
    by position (systemctl prints one block per unit, in the order asked)."""
    blocks = _blocks(ran.stdout) if ran.ok else []
    out = {}
    for i, block in enumerate(blocks):
        unit = _one(block, "Id") or (ids[i] if i < len(ids) else "")
        out[unit] = block
    return out


def _gate_argv(spec: Spec) -> list[str]:
    from .. import paths

    try:
        gate = str(paths.script("moxi"))
    except FileNotFoundError:
        raise RenderError("command", f"{spec.unit}: no moxi in this environment, and a "
                                     "timer runs through its gate, `moxi run`") from None
    argv = [gate, "run"]
    if spec.switch:
        argv += ["--if", f"{spec.module}.{spec.switch}"]
    return argv + ["--record", spec.unit, "--", *spec.command]


def _disabled(text: str) -> bool:
    return DISABLED in text.split("\n")[1:2]


class SystemdBackend:
    """The `Backend` protocol on a systemd user manager."""

    name = "systemd"
    supervisor = "systemd"
    encoding = "utf-8"
    #: The fence's runner when a call passes none: the real supervisor.
    runner = None

    def __init__(self, root: Path | None = None):
        self._root = None if root is None else Path(root)

    @property
    def root(self) -> Path:
        """The unit directory, read from the environment at each call unless fixed."""
        return self._root if self._root is not None else unit_dir()

    def _stem(self, module: str, name: str) -> str:
        return f"moxi-{module}-{name}"

    # -- writing ---------------------------------------------------------------------------

    def render(self, spec: Spec) -> tuple[Rendered, ...]:
        root, stem = self.root, self._stem(spec.module, spec.name)
        head = [f"{MARK}{spec.unit}"] + ([DISABLED] if not spec.enabled else [])
        doc = f"Moxi {spec.module}: {spec.doc}"
        if spec.kind == "daemon":
            service = head + ["[Unit]", f"Description={_value(doc)}", "", "[Service]",
                              "Type=simple", f"ExecStart={command_line(spec.command)}"]
            if spec.cwd:
                service.append(f"WorkingDirectory={_path_value(spec.cwd, 'cwd')}")
            service += ["Restart=on-failure", "RestartSec=2"]
            if spec.stop == "process":
                service.append("KillMode=process")
            if spec.memory_high:
                service.append(f"MemoryHigh={spec.memory_high}")
            if spec.memory_swap_max:
                service.append(f"MemorySwapMax={spec.memory_swap_max}")
            service += ["", "[Install]", "WantedBy=default.target"]
            out = [Rendered("daemon", ((root / f"{stem}.service", _text(service)),))]
            if spec.socket is not None:
                socket = head + ["[Unit]", f"Description={_value(doc + ' (socket)')}", "",
                                 "[Socket]"]
                socket += [f"ListenStream={_listen(a, spec.socket.port)}"
                           for a in spec.socket.addresses]
                socket += ["", "[Install]", "WantedBy=sockets.target"]
                out.append(Rendered("socket", ((root / f"{stem}.socket", _text(socket)),)))
            return tuple(out)
        service = head + ["[Unit]", f"Description={_value(doc)}", "", "[Service]",
                          "Type=oneshot",
                          f"ExecStart={command_line(_gate_argv(spec))}"]
        if spec.cwd:
            service.append(f"WorkingDirectory={_path_value(spec.cwd, 'cwd')}")
        timer = head + ["[Unit]", f"Description={_value(doc + ' (schedule)')}", "",
                        "[Timer]"]
        schedule = spec.schedule
        if schedule is None:
            raise RenderError("declaration", f"{spec.unit}: a timer with no schedule")
        if schedule.every:
            timer += [f"OnActiveSec={schedule.every}min",
                      f"OnUnitActiveSec={schedule.every}min"]
        else:
            days = "" if schedule.daily else ",".join(
                _DAY_NAMES[d] for d in schedule.days) + " "
            timer += [f"OnCalendar={days}*-*-* {h:02d}:{m:02d}:00" for h, m in schedule.at]
            timer.append("Persistent=false")
        timer += ["AccuracySec=1s", "", "[Install]", "WantedBy=timers.target"]
        return (Rendered("timer", ((root / f"{stem}.service", _text(service)),
                                   (root / f"{stem}.timer", _text(timer)))),)

    def mark(self, text: str) -> str | None:
        first = text.split("\n", 1)[0]
        return first[len(MARK):].strip() or None if first.startswith(MARK) else None

    def enabled(self, text: str) -> bool:
        return not _disabled(text)

    def marked(self, module: str) -> tuple[UnitRef, ...]:
        root = self.root
        prefix = f"moxi-{module}-"
        found: dict[tuple[str, str], Path] = {}
        texts: dict[Path, str] = {}
        try:
            entries = sorted(root.iterdir())
        except OSError:
            return ()
        for path in entries:
            if (not path.name.startswith(prefix) or path.suffix not in _SUFFIXES
                    or not path.is_file()):
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            mark = self.mark(text)
            if not mark or "/" not in mark:
                continue
            owner, _, name = mark.partition("/")
            # Only a file at the name `render` gives this mark is the writer's.
            if owner != module or path.stem != self._stem(module, name):
                continue
            texts[path] = text
            found[(name, path.suffix)] = path
        out = []
        for (name, suffix), path in sorted(found.items()):
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

    def _id(self, change: UnitChange) -> str:
        """The unit `enable` and `disable` take for a change: a timer's `.timer`."""
        stem = self._stem(change.module, change.name)
        return {"daemon": f"{stem}.service", "socket": f"{stem}.socket",
                "timer": f"{stem}.timer"}[change.kind]

    def apply(self, changes: Sequence[UnitChange], *, restart: bool, io: ApplyIO) -> None:
        """systemd's order: each remove disabled with `--now` (a `.socket` before its
        service); removed files deleted, foreign files moved aside, new and changed files
        written; one `daemon-reload`; then each written unit enabled and started, a
        `.socket` before its service; with `restart=True`, running unchanged daemons whose
        entry says `upgrade = "restart"` restarted. After the first new daemon, the
        lingering advice when lingering is off."""
        fence = io.fence
        removes = sorted((c for c in changes if c.action == "remove"),
                         key=lambda c: c.kind != "socket")
        writes = [c for c in changes if c.action in ("add", "change")]
        keeps = [c for c in changes if c.action == "keep"]
        gone = []
        for change in removes:
            if self._remove(io, change):
                gone.append(change)
        for change in gone:
            io.delete(change)
        for change in writes:
            io.write(change)
        if gone or writes:
            ran = fence.run("daemon-reload")
            if not ran.ok:
                for change in writes:
                    io.fail(change, ran)
                for change in gone:
                    io.fail(change, ran, "the unit files are deleted; run `systemctl "
                                         "--user daemon-reload` once the cause is fixed")
                return
        services = {(c.module, c.name): c for c in writes if c.kind == "daemon"}
        restarted: set[tuple[str, str]] = set()
        lingered = False
        for change in sorted(writes, key=lambda c: c.kind != "socket"):
            if change.kind == "socket":
                if self._start_socket(io, change, services.get((change.module, change.name))):
                    restarted.add((change.module, change.name))
            elif self._start(io, change, (change.module, change.name) in restarted) \
                    and change.kind == "daemon" \
                    and change.action == "add" and change.enabled and not lingered:
                lingered = True
                self._linger(io, change)
        if restart:
            daemons = [c for c in keeps if c.kind == "daemon" and c.enabled
                       and c.entry.get("upgrade", "restart") == "restart"]
            try:
                running = self.running([self._id(c) for c in daemons], fence)
            except ReadError as exc:
                for change in daemons:
                    io.fail(change, exc.ran, "the unit is written; restart it with "
                                             f"`systemctl --user restart {self._id(change)}`")
                return
            for change in daemons:
                if self._id(change) in running:
                    self._run(io, change, "restart", self._id(change))

    def _remove(self, io: ApplyIO, change: UnitChange) -> bool:
        """Disable and stop a removed unit (a timer's run in progress too); False, with
        the files left in place, when the supervisor refuses."""
        remedy = (f"the unit is left in place; fix the cause and run `moxi install "
                  f"{change.module}` again")
        ran = io.fence.run("disable", "--now", self._id(change))
        if not ran.ok:
            io.fail(change, ran, remedy)
            return False
        if change.kind == "timer":
            ran = io.fence.run("stop", f"{self._stem(change.module, change.name)}.service")
            if not ran.ok and not _not_loaded(ran):
                io.fail(change, ran, remedy)
                return False
        return True

    def _start(self, io: ApplyIO, change: UnitChange, restarted: bool = False) -> bool:
        """Enable and start a written daemon or timer as its change asks. True when every
        step it ran passed. `restarted` says the daemon's changed socket already stopped
        and started it again, which an `upgrade = "keep"` daemon takes as its start."""
        unit = self._id(change)
        if not change.enabled:
            if change.action == "change" and change.enabled_before:
                return self._run(io, change, "disable", "--now", unit)
            return True
        if change.action == "add" or change.enabled_before is False:
            return self._run(io, change, "enable", "--now", unit)
        if _keeps(change):
            if self._run(io, change, "enable", unit):
                io.say(change, f"{unit} rewritten and restarted, since its socket changed"
                       if restarted else
                       f"{unit} rewritten; the new code runs from its next start")
                return True
            return False
        return self._run(io, change, "enable", unit) and self._run(io, change, "restart",
                                                                     unit)

    def _start_socket(self, io: ApplyIO, change: UnitChange,
                      service_change: UnitChange | None) -> bool:
        """Enable and start a written `.socket` before its service. systemd refuses to
        start a socket while its service runs, so the service is stopped first unless it is
        new, and started again after unless its own change starts it. A daemon with
        `upgrade = "keep"` is started here too: a changed socket restarts its daemon
        whatever `upgrade` says, never leaving it stopped until the next connection.
        True when this step started the service again."""
        stem = self._stem(change.module, change.name)
        service, unit = f"{stem}.service", f"{stem}.socket"
        if not change.enabled:
            if change.action == "change" and change.enabled_before:
                self._run(io, change, "disable", "--now", unit)
            return False
        if service_change is None or service_change.action != "add":
            ran = io.fence.run("stop", service)
            if not ran.ok and not _not_loaded(ran):
                io.fail(change, ran)
                return False
        if change.action == "add" or change.enabled_before is False:
            ok = self._run(io, change, "enable", "--now", unit)
        else:
            ok = self._run(io, change, "enable", unit) and self._run(io, change, "restart",
                                                                       unit)
        starts_itself = service_change is not None and (
            service_change.action == "add" or service_change.enabled_before is False
            or not _keeps(service_change))
        if ok and not starts_itself:
            return self._run(io, change, "start", service)
        return False

    def _linger(self, io: ApplyIO, change: UnitChange) -> None:
        """The lingering advice, when `loginctl` says lingering is off for this user. The
        writer never turns it on: it is a login policy for the account on the whole
        machine."""
        user = _user()
        if not user or not re.fullmatch(r"[A-Za-z0-9._-]+", user):
            return
        ran = io.fence.run("linger", user)
        if not ran.ok:
            return
        if any(line.strip() == "Linger=no" for line in ran.stdout.splitlines()):
            for line in LINGER:
                io.say(change, line.format(user=user))

    def _run(self, io: ApplyIO, change: UnitChange, verb: str, *args: str) -> bool:
        ran = io.fence.run(verb, *args)
        if not ran.ok:
            io.fail(change, ran)
        return ran.ok

    # -- acting and reading ----------------------------------------------------------------

    def own(self, ref: str) -> bool:
        return ref.startswith("moxi-")

    def foreign(self, ref: str, fence: Fence | None) -> UnitRef:
        """A foreign unit; a timer's ids are its `.timer` and the unit its `Triggers`
        names, or, with `fence` None (no supervisor call), its `.timer` alone."""
        if ref.endswith(".timer"):
            if fence is None:
                return UnitRef(None, ref, "timer", (ref,))
            ran = fence.run("show", "--property=Id,Triggers", ref)
            block = _by_id(ran, [ref]).get(ref, {})
            triggers = tuple(_one(block, "Triggers").split())
            return UnitRef(None, ref, "timer", (ref, *triggers))
        return UnitRef(None, ref, "daemon", (ref,))

    def ids(self, module: str, name: str, kind: str) -> tuple[str, ...]:
        """The ids the writer gives a unit, file or not: a daemon's `.socket` then its
        `.service`; a timer's `.timer` then its `.service`."""
        stem = self._stem(module, name)
        if kind == "timer":
            return (f"{stem}.timer", f"{stem}.service")
        return (f"{stem}.socket", f"{stem}.service")

    def files(self, module: str, name: str, kind: str) -> tuple[Path, ...]:
        """The unit files of those ids, file or not."""
        return tuple(self.root / unit for unit in self.ids(module, name, kind))

    def _socket_of(self, unit: UnitRef) -> tuple[str, ...]:
        """A marked daemon's `.socket` id, when a file with its mark is there."""
        if unit.kind != "daemon" or not unit.marked:
            return ()
        path = self.root / (unit.ids[0].rsplit(".", 1)[0] + ".socket")
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ()
        return (path.name,) if self.mark(text) == f"{unit.module}/{unit.name}" else ()

    def act(self, act: str, unit: UnitRef, fence: Fence) -> Acted:
        lines: list[str] = []
        ok = True

        def run(verb: str, *args: str) -> bool:
            nonlocal ok
            ran = fence.run(verb, *args)
            if not ran.ok and not (verb == "stop" and _not_loaded(ran)):
                ok = False
                lines.append(ran.failure())
                return False
            return True

        if act not in ("start", "stop", "restart", "enable", "disable"):
            raise ValueError(f"no such act {act!r}")
        if unit.kind == "timer":
            service = unit.ids[1:2]
            if act in ("enable", "disable"):
                run(act, "--now", unit.ids[0])
            elif not service:
                ok, lines = False, [f"{unit.ids[0]} triggers no unit"]
            elif act == "restart":
                if run("stop", *service):
                    run("start", *service)
            else:
                run(act, *service)
        else:
            socket, service = self._socket_of(unit), unit.ids[0]
            if act == "restart":
                run("restart", service)
            else:
                for one in (*socket, service):
                    run(*((act, "--now", one) if act in ("enable", "disable")
                          else (act, one)))
        return Acted(unit.module, unit.name, act, ok, tuple(lines))

    def running(self, ids: Sequence[str], fence: Fence) -> frozenset[str]:
        if not ids:
            return frozenset()
        ran = fence.run("show", "--property=Id,ActiveState", *ids)
        if not ran.ok:
            raise ReadError(ran)
        return frozenset(unit for unit, block in _by_id(ran, ids).items()
                         if _one(block, "ActiveState") in ("active", "activating",
                                                           "reloading"))

    def stop_id(self, unit: str, fence: Fence) -> Ran:
        return fence.run("stop", unit)

    def start_id(self, unit: str, fence: Fence) -> Ran:
        return fence.run("start", unit)

    def status(self, units: Sequence[UnitRef], fence: Fence) -> list[UnitState]:
        """One `show` over every id of `units`, and, when a foreign timer came without the
        unit its `Triggers` names, one more for those units.

        A timer's state is its `.timer`'s, so it reads `active` while armed whatever its
        runs did; its service's `Result` and `ExecMainStatus` go in `last`. When the first
        `show` fails, every unit is `unknown` with the failure in `detail`."""
        ids = list(dict.fromkeys(i for u in units for i in u.ids))
        if not ids:
            return []
        ran = fence.run("show", f"--property={STATUS_PROPERTIES}", *ids)
        shown = _by_id(ran, ids)
        units = list(units)
        later = []
        for i, unit in enumerate(units):
            if unit.kind == "timer" and not unit.marked and len(unit.ids) == 1:
                triggers = tuple(_one(shown.get(unit.ids[0], {}), "Triggers").split())
                if triggers:
                    units[i] = UnitRef(None, unit.name, "timer", (unit.ids[0], *triggers),
                                       unit.paths, unit.enabled)
                    later += [t for t in triggers if t not in shown]
        unread: Ran | None = None
        if ran.ok and later:
            later = list(dict.fromkeys(later))
            more = fence.run("show", f"--property={STATUS_PROPERTIES}", *later)
            shown.update(_by_id(more, later))
            unread = None if more.ok else more
        states = [self._state(unit, shown, ran) for unit in units]
        if unread is not None:
            # The timers read; the units they trigger did not, so no last result is
            # known for them. Their state is their `.timer`'s, read above.
            states = [replace(state, detail=f"{state.detail}; its triggered unit could not "
                                            f"be read: {unread.failure()}")
                      if unit.kind == "timer" and not unit.marked and len(unit.ids) > 1
                      and unit.ids[1] in later and state.state != "unknown" else state
                      for unit, state in zip(units, states)]
        return states

    def _state(self, unit: UnitRef, shown: Mapping, ran: Ran) -> UnitState:
        head = shown.get(unit.ids[0], {})
        service = shown.get(unit.ids[1], {}) if unit.kind == "timer" and len(
            unit.ids) > 1 else head
        if not ran.ok:
            return UnitState(unit.module, unit.name, unit.kind, self.name, "unknown",
                             detail=ran.failure())
        if not head or _one(head, "LoadState") == "not-found":
            return UnitState(unit.module, unit.name, unit.kind, self.name, "missing",
                             detail="not-found")
        active, sub = _one(head, "ActiveState"), _one(head, "SubState")
        state = ("active" if active in ("active", "activating", "reloading")
                 else "failed" if active == "failed" else "inactive")
        raw = _one(head, "UnitFileState")
        enabled = {"enabled": True, "disabled": False}.get(raw)
        detail = f"{active} ({sub})" if sub else active
        if raw:
            detail += f", {raw}"
        restarts = _one(service, "NRestarts")
        if unit.kind == "daemon" and restarts not in ("", "0"):
            detail += f", restarted {restarts} times"
        pid = _int(_one(service, "MainPID")) if unit.kind != "socket" else None
        since = None
        if state == "active":
            mono = _int(_one(head, "ActiveEnterTimestampMonotonic"))
            if mono:
                since = time.time() - (time.monotonic() - mono / 1e6)
        last = None
        if unit.kind == "timer" and service and service is not head:
            last = {"result": _one(service, "Result"),
                    "status": _one(service, "ExecMainStatus")}
        wrapped = None
        if unit.kind == "timer" and not unit.marked and service is not head:
            wrapped = self._wrapped(unit.ids[0], _one(service, "DropInPaths").split())
        return UnitState(unit.module, unit.name, unit.kind, self.name, state, pid=pid,
                         since=since, last=last, detail=detail, log=self.log(unit),
                         enabled=enabled, wrapped=wrapped)

    def log(self, unit: UnitRef) -> str | None:
        """`journalctl --user -u <unit>`: a timer's service, where its runs write."""
        target = unit.ids[1] if unit.kind == "timer" and len(unit.ids) > 1 else unit.ids[0]
        return f"journalctl --user -u {target}"

    # -- a foreign timer's wrap ----------------------------------------------------------------

    def _dropin(self, service: str, module: str) -> Path:
        return self.root / f"{service}.d" / f"50-moxi-{module}.conf"

    def _wrapped(self, ref: str, paths: Sequence[str]) -> Wrapped | None:
        for path in paths:
            if not _DROPIN.fullmatch(os.path.basename(path)):
                continue
            mark = _read_wrap(Path(path))
            if mark and mark[1] == ref:
                module, _, key, name = mark
                return Wrapped(module, key, name)
        return None

    def wrap(self, ref: str, *, module: str, key: str, name: str, fence: Fence) -> Acted:
        """Put the foreign timer `ref`'s runs behind `module`'s switch `key`, recorded as
        `<module>/<name>`: a marked drop-in on the service it triggers, with the gate's
        two wrap forms as `ExecCondition=` and `ExecStopPost=`, then one `daemon-reload`.
        The core has already checked `ref` as a foreign name, `key` as a switch, `name`,
        and that no other wrap of `module` conflicts (`wraps`). This refuses, with one line
        naming what it found: a manager older than 243 (or whose version it cannot read);
        a timer not loaded or not triggering exactly one service; a service not loaded,
        without exactly one `ExecStart`, or with a `Type` other than oneshot, simple or
        exec; a drop-in on that service marked for another module; an unmarked file at
        the drop-in's path; no `moxi` gate script. As a guard it also refuses a name in
        moxi's own namespace and a ref that is not a `.timer`. A drop-in there with
        `module`'s mark is rewritten."""
        from .. import files, paths

        def no(line: str) -> Acted:
            return Acted(None, ref, "wrap", False, (line,))

        if self.own(ref):
            return no(f"{ref} is in moxi's own unit names; only a foreign timer is wrapped")
        if not ref.endswith(".timer"):
            return no(f"{ref} is not a timer; only a timer's runs are wrapped")
        ran = fence.run("show", "--property=Version")
        if not ran.ok:
            return no(ran.failure())
        version = _version(_one(next(iter(_blocks(ran.stdout)), {}), "Version"))
        if version is None:
            return no(f"the systemd version could not be read ({ran.stdout.strip()!r}); "
                      "not wrapped")
        if version < WRAP_VERSION:
            return no(f"systemd {version} has no ExecCondition= (it needs {WRAP_VERSION}); "
                      "not wrapped")
        ran = fence.run("show", "--property=Id,LoadState,Triggers", ref)
        if not ran.ok:
            return no(ran.failure())
        timer = _by_id(ran, [ref]).get(ref, {})
        if _one(timer, "LoadState") != "loaded":
            return no(f"{ref} is not loaded ({_one(timer, 'LoadState') or 'not found'}); "
                      "not wrapped")
        triggers = _one(timer, "Triggers").split()
        if len(triggers) != 1:
            return no(f"{ref} triggers {len(triggers)} units, not one; not wrapped")
        service = triggers[0]
        if not service.endswith(".service"):
            return no(f"{ref} triggers {service}, not a service; not wrapped")
        ran = fence.run("show", "--property=Id,LoadState,Type,ExecStart,DropInPaths",
                        service)
        if not ran.ok:
            return no(ran.failure())
        shown = _by_id(ran, [service]).get(service, {})
        if _one(shown, "LoadState") != "loaded":
            return no(f"{service} is not loaded ({_one(shown, 'LoadState') or 'not found'})"
                      "; not wrapped")
        starts = [v for v in shown.get("ExecStart", []) if v.strip()]
        if len(starts) != 1:
            return no(f"{service} has {len(starts)} ExecStart lines, not one; its run's exit "
                      "would not be one process's; not wrapped")
        kind = _one(shown, "Type")
        if kind not in WRAP_TYPES:
            return no(f"{service} is Type={kind}, not oneshot, simple or exec; not wrapped")
        path = self._dropin(service, module)
        seen = set(_one(shown, "DropInPaths").split())
        seen |= {str(p) for p in (self.root / f"{service}.d").glob("50-moxi-*.conf")}
        for other in sorted(seen):
            match = _DROPIN.fullmatch(os.path.basename(other))
            if not match or Path(other) == path:
                continue
            mark = _read_wrap(Path(other))
            if mark and mark[0] != module:
                return no(f"{service} is wrapped by {mark[0]}")
        if path.exists() or path.is_symlink():
            mark = _read_wrap(path)
            if mark is None:
                return no(f"{path} exists and was not written by moxi; not wrapped")
            if mark[0] != module:
                return no(f"{service} is wrapped by {mark[0]}")
        try:
            gate = str(paths.script("moxi"))
        except FileNotFoundError:
            return no("no moxi in this environment, and a wrap runs its gate, `moxi run`")
        switch = f"{module}.{key}"
        record = f"{module}/{name}"
        text = _text([
            f"{WRAP_MARK}{record}",
            f"# moxi: timer = {ref}, if = {switch}",
            "[Service]",
            "ExecCondition=" + command_line([gate, "run", "--if", switch, "--record",
                                             record, "--condition"]),
            "ExecStopPost=" + command_line([gate, "run", "--record", record, "--result"]),
        ])
        path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
        files.atomic_write(path, text, mode=0o644)
        ran = fence.run("daemon-reload")
        if not ran.ok:
            return no(ran.failure() + f"; {path} is written, and applies at the next "
                                      "daemon-reload")
        return Acted(None, ref, "wrap", True, (
            f"{ref}: its runs are behind {switch} and recorded as {record}",
            f"turn them on with: moxi config set {module} {key} true"))

    def _drop_ins(self, module: str) -> list[tuple[Path, tuple[str, str, str, str]]]:
        out = []
        for path in sorted(self.root.glob(f"*.d/50-moxi-{module}.conf")):
            mark = _read_wrap(path)
            if mark and mark[0] == module:
                out.append((path, mark))
        return out

    def wraps(self, module: str) -> tuple[tuple[str, str, Wrapped], ...]:
        """Every drop-in with `module`'s wrap mark, as `(timer ref, service, Wrapped)`,
        read from the files."""
        return tuple((mark[1], path.parent.name.removesuffix(".d"),
                      Wrapped(module, mark[2], mark[3]))
                     for path, mark in self._drop_ins(module))

    def unwrap(self, ref: str, *, module: str, fence: Fence) -> Acted:
        found = [path for path, mark in self._drop_ins(module) if mark[1] == ref]
        if not found:
            return Acted(None, ref, "unwrap", True, (f"{ref} is not wrapped by {module}",))
        for path in found:
            _delete(path)
        ran = fence.run("daemon-reload")
        return Acted(None, ref, "unwrap", ran.ok, () if ran.ok else (ran.failure(),))

    def unwrap_all(self, module: str, fence: Fence) -> list[Acted]:
        found = self._drop_ins(module)
        if not found:
            return []
        for path, _ in found:
            _delete(path)
        ran = fence.run("daemon-reload")
        lines = () if ran.ok else (ran.failure(),)
        return [Acted(None, mark[1], "unwrap", ran.ok, lines) for _, mark in found]


def _read_wrap(path: Path) -> tuple[str, str, str, str] | None:
    """A drop-in's two mark lines as `(module, timer, key, name)`, or None for a file
    without them."""
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            first, second = handle.readline().rstrip("\n"), handle.readline().rstrip("\n")
    except OSError:
        return None
    if not first.startswith(WRAP_MARK):
        return None
    module, sep, name = first[len(WRAP_MARK):].strip().partition("/")
    match = _WRAP_LINE.fullmatch(second.strip())
    if not sep or not match or not match["switch"].startswith(module + "."):
        return None
    return module, match["ref"], match["switch"][len(module) + 1:], name


def _keeps(change: UnitChange) -> bool:
    """Whether a changed daemon is written without a restart (`upgrade = "keep"`)."""
    return change.kind == "daemon" and change.entry.get("upgrade") == "keep"


def _delete(path: Path) -> None:
    """Delete a drop-in, and its `.d` folder when that leaves it empty."""
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass
    try:
        path.parent.rmdir()
    except OSError:
        pass


def _not_loaded(ran: Ran) -> bool:
    """True for a `stop` of a unit systemd has not loaded: it is not running."""
    return "not loaded" in ran.stderr


def _version(text: str) -> int | None:
    match = re.match(r"\s*(\d+)", text)
    return int(match[1]) if match else None


def _int(text: str) -> int | None:
    try:
        return int(text) or None
    except ValueError:
        return None


def _text(lines: list[str]) -> str:
    return "\n".join(lines) + "\n"


BACKEND = SystemdBackend()

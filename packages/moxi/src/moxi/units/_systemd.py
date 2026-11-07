"""The systemd backend: a module's units as systemd user units.

Files go in the user unit directory (`$XDG_CONFIG_HOME`, else `~/.config`, then
`/systemd/user`), 0644, each written in one step. A daemon is `moxi-<module>-<name>.service`;
a timer is a `.service` and `.timer` pair whose service runs the gate, `moxi run`. A home
given by `MOXI_HOME` puts its tag in every name, `moxi-<tag>-<module>-<name>`, so two homes'
files sit apart in the one unit directory (`moxi.units.home_tag`). Every file starts with
the mark `# moxi unit: <module>/<name>`, and a unit declared `enabled = false` carries a
second line `# moxi: enabled = false`. The text uses absolute paths resolved now, no
specifier, and copies no variable of this shell but one: a tagged home's services carry
`Environment="MOXI_HOME=<path>"`, the home's real path, so the unit runs with the home it
was written from.

A daemon with a `socket` gets one socket unit per address, `<stem>.socket` for the first
address and `<stem>-<k>.socket` for the k-th after it, so one address that can't be bound
fails only its own unit. Its service names them all in `Sockets=`. Under `start =
"connection"` (the default) the service has no install section: the socket units are
what is enabled, they start with `sockets.target`, and they start the service at the
first connection, never a login. Such a service reads `static`, so it is never enabled or
disabled; it is started and stopped. Under `start = "login"` the service keeps its
install section, `WantedBy=default.target`, so a login starts it, and it is enabled and
disabled as any daemon's service is, after its socket units; `Sockets=` gives it a
`Wants=` and an `After=` on each unit it names, so a login that starts it starts its
sockets first. The socket units are the same text under both. Wherever this backend asks
whether a service is static, the answer is whether its text has an install section, never
whether the daemon declares `socket`.

`moxi install` starts a socket daemon's service all the same: a new or changed daemon's
service is started after its sockets, so the first `status` after an install shows it
`active` even before any connection, and a socket set that changes under a running
service stops it and starts it again. An `upgrade = "keep"` daemon whose socket set is
unchanged is left as it is (only enabled, under `start = "login"`, so its login link is
made or kept). So is a change that touches only the service's install section, its
`start` moving between `login` and `connection`, whatever `upgrade` says: the service is
enabled or disabled (without `--now`) and a running one goes on.

Every supervisor call goes through the systemd fence (`systemctl --user`, always), so the
verbs here are the fence's closed set. A wrap of a foreign timer is the one file this
backend writes for a unit it did not write: a marked drop-in on the service the timer
triggers, `<service>.d/50-moxi-<module>.conf` (`50-moxi-<tag>-<module>.conf` for a tagged
home), which puts the timer's runs behind one of the module's switches and records each
run, and copies nothing of the foreign unit. A tagged home's drop-in gives its gate the
home with `--home`, so the foreign service's own command is not given `MOXI_HOME`.
"""

from __future__ import annotations

import ipaddress
import os
import re
import time
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path

from ._backend import (
    ApplyIO, ReadError, Rendered, RenderError, Spec, UnitRef, enabled_by_socket,
    listen_address, socket_name, socket_place, socket_state)
from ._fence import Fence, Ran
from ._home import environment, home_tag
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
                     "ControlGroup,UnitFileState,Triggers,DropInPaths,InvocationID")

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


def environment_line(name: str, value: str) -> str:
    """One `Environment=` line setting `name` to `value`, by that setting's rules: the
    assignment in double quotes, `\\` and `"` escaped, a control character written as a
    C escape, `%` doubled (a specifier), and `$` left alone, since systemd expands no
    variable there. `quote`, which is the `Exec*=` quoting, would double the `$`."""
    out = []
    for ch in f"{name}={value}".replace("%", "%%"):
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
    return 'Environment="' + "".join(out) + '"'


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
    """A `ListenStream=` value: an IPv6 address in brackets, `%` (a scope's) doubled."""
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return f"{address}:{port}".replace("%", "%%")
    text = f"[{ip}]:{port}" if ip.version == 6 else f"{ip}:{port}"
    return text.replace("%", "%%")


def _v6(address: str) -> bool:
    try:
        return ipaddress.ip_address(address).version == 6
    except ValueError:
        return False


def _installs(path: Path) -> bool:
    """True when the unit file at `path` has an install section (a link can name it)."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return _text_installs(text)


def _text_installs(text: str | None) -> bool:
    """True when a unit's text has an install section. A service without one reads
    `static`: it has nothing to enable, so it is only started and stopped."""
    return any(line.strip() == "[Install]" for line in (text or "").splitlines())


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
    sockets = "units"
    #: The fence's runner when a call passes none: the real supervisor.
    runner = None

    def __init__(self, root: Path | None = None):
        self._root = None if root is None else Path(root)

    @property
    def root(self) -> Path:
        """The unit directory, read from the environment at each call unless fixed."""
        return self._root if self._root is not None else unit_dir()

    def _stem(self, module: str, name: str) -> str:
        """`moxi-<module>-<name>`, or `moxi-<tag>-<module>-<name>` for a tagged home."""
        tag = home_tag()
        return f"moxi-{module}-{name}" if tag is None else f"moxi-{tag}-{module}-{name}"

    # -- writing ---------------------------------------------------------------------------

    def render(self, spec: Spec) -> tuple[Rendered, ...]:
        root, stem = self.root, self._stem(spec.module, spec.name)
        head = [f"{MARK}{spec.unit}"] + ([DISABLED] if not spec.enabled else [])
        doc = f"Moxi {spec.module}: {spec.doc}"
        if spec.kind == "daemon":
            sockets = []
            if spec.socket is not None:
                for place, address in spec.socket.listens:
                    unit = f"{socket_name(stem, place)}.socket"
                    socket = head + ["[Unit]",
                                     f"Description={_value(f'{doc} (socket {address})')}",
                                     "", "[Socket]",
                                     f"ListenStream={_listen(address, spec.socket.port)}"]
                    if _v6(address):
                        # Never also the IPv4 port, which another unit of the set holds.
                        socket.append("BindIPv6Only=ipv6-only")
                    socket += [f"Service={stem}.service", "", "[Install]",
                               "WantedBy=sockets.target"]
                    sockets.append((root / unit, _text(socket)))
            service = head + ["[Unit]", f"Description={_value(doc)}", "", "[Service]",
                              "Type=simple", *_environment(spec),
                              f"ExecStart={command_line(spec.command)}"]
            if sockets:
                # By default a service is handed only the socket unit of its own name.
                service.append("Sockets=" + " ".join(path.name for path, _ in sockets))
            if spec.cwd:
                service.append(f"WorkingDirectory={_path_value(spec.cwd, 'cwd')}")
            service += ["Restart=on-failure", "RestartSec=2"]
            if spec.stop == "process":
                service.append("KillMode=process")
            if spec.memory_high:
                service.append(f"MemoryHigh={spec.memory_high}")
            if spec.memory_swap_max:
                service.append(f"MemorySwapMax={spec.memory_swap_max}")
            if not sockets or spec.socket.start == "login":
                # Under "connection" a daemon with sockets has no install section: they
                # start it, a login doesn't.
                service += ["", "[Install]", "WantedBy=default.target"]
            out = [Rendered("daemon", ((root / f"{stem}.service", _text(service)),))]
            if sockets:
                out.append(Rendered("socket", tuple(sockets)))
            return tuple(out)
        service = head + ["[Unit]", f"Description={_value(doc)}", "", "[Service]",
                          "Type=oneshot", *_environment(spec),
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
        prefix = self._stem(module, "")
        found: dict[tuple[str, str], Path] = {}
        sockets: dict[str, list[tuple[int, Path]]] = {}
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
            if owner != module:
                continue
            stem = self._stem(module, name)
            # Only a file at a name `render` gives this mark is the writer's: a daemon's
            # socket units at the socket names of its stem.
            if path.suffix == ".socket":
                place = socket_place(stem, path.stem)
                if place is not None:
                    texts[path] = text
                    sockets.setdefault(name, []).append((place, path))
                continue
            if path.stem != stem:
                continue
            texts[path] = text
            found[(name, path.suffix)] = path
        out = []
        for name, placed in sockets.items():
            found[(name, ".socket")] = min(placed)[1]
        for (name, suffix), path in sorted(found.items()):
            if suffix == ".socket":
                files = tuple(p for _, p in sorted(sockets[name]))
                out.append(UnitRef(module, name, "socket", tuple(p.name for p in files),
                                   files, all(self.enabled(texts[p]) for p in files)))
            elif suffix == ".timer":
                service = path.with_suffix(".service")
                files = (service, path) if service in texts else (path,)
                out.append(UnitRef(module, name, "timer", (path.name, service.name), files,
                                   all(self.enabled(texts[p]) for p in files)))
            elif (name, ".timer") not in found:
                out.append(UnitRef(module, name, "daemon", (path.name,), (path,),
                                   self.enabled(texts[path])))
        return tuple(out)

    def socket_files(self) -> tuple[tuple[Path, str, tuple[tuple[str, int], ...]], ...]:
        try:
            entries = sorted(self.root.iterdir())
        except OSError:
            return ()
        out = []
        for path in entries:
            if path.suffix != ".socket" or not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            mark = self.mark(text)
            if not mark:
                continue
            listens = tuple(found for line in text.splitlines()
                            if line.startswith("ListenStream=")
                            and (found := listen_address(
                                line.partition("=")[2].replace("%%", "%"))) is not None)
            if listens:
                out.append((path, mark, listens))
        return tuple(out)

    def _id(self, change: UnitChange) -> str:
        """The unit `enable` and `disable` take for a change: a timer's `.timer`, a
        daemon's `.service`, a socket set's first unit."""
        stem = self._stem(change.module, change.name)
        return {"daemon": f"{stem}.service", "socket": f"{stem}.socket",
                "timer": f"{stem}.timer"}[change.kind]

    def apply(self, changes: Sequence[UnitChange], *, restart: bool, io: ApplyIO) -> None:
        """systemd's order: each remove disabled with `--now` (socket units before their
        service), and with them each unit a socket set drops; removed and dropped files
        deleted, foreign files moved aside, new and changed files written; one
        `daemon-reload`; then each written unit enabled and started; with `restart=True`,
        running unchanged daemons whose entry says `upgrade = "restart"` restarted. After
        the first new daemon, the lingering advice when lingering is off.

        A daemon with a socket: its socket units are started before its service, and none
        while it runs, which systemd refuses (`SocketStarts` holds the rule): a new or
        changed daemon's service is started after its sockets even when it was idle, and
        an `upgrade = "keep"` daemon whose socket set is unchanged is left as it is. A
        service with no install section (`start = "connection"`) is started, restarted
        and stopped, never enabled or disabled; one with an install section (`start =
        "login"`) is enabled in the step that starts it.

        The login link follows the new service text: a daemon whose old service text had
        an install section and whose new one has none is disabled (without `--now`) with
        the removes, so its old link goes; that is never so from `"login"` to
        `"login"`."""
        fence = io.fence
        removes = sorted((c for c in changes if c.action == "remove"),
                         key=lambda c: c.kind != "socket")
        writes = [c for c in changes if c.action in ("add", "change")]
        keeps = [c for c in changes if c.action == "keep"]
        gone = [change for change in removes if self._remove(io, change)]
        dropped = {id(change): self._drop(io, change) for change in writes
                   if change.dropped}
        for change in writes:
            self._unlink_login(io, change)
        # Read before the files are written: how each socket unit stands to its file, and
        # which socket daemons' services run.
        starts = SocketStarts(self, writes, keeps, restart=restart, socketed=_socketed,
                              installs=_text_installs, login_only=_login_only)
        starts.read(fence)
        for change in gone:
            io.delete(change)
        for change in writes:
            if id(change) in dropped:
                io.drop(replace(change, dropped=dropped[id(change)]))
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
        lingered = False
        for change in writes:
            if starts.owns(change):
                started = starts.start(io, change)
            else:
                started = self._start(io, change)
            if started and change.kind == "daemon" and change.action == "add" \
                    and change.enabled and not lingered:
                lingered = True
                self._linger(io, change)
        starts.restart_kept(io)
        if restart:
            daemons = [c for c in keeps if c.kind == "daemon" and c.enabled
                       and not starts.owns(c)
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
        """Disable and stop a removed unit (a timer's run in progress too; every unit of
        a socket set; a service with no install section only stopped); False, with the
        files left in place, when the supervisor refuses."""
        remedy = (f"the unit is left in place; fix the cause and run `moxi install "
                  f"{change.module}` again")
        if change.kind == "socket":
            units = [path.name for path in change.paths]
        else:
            units = [self._id(change)]
        static = change.kind == "daemon" and bool(change.paths) and not _installs(
            change.paths[0])
        for unit in units:
            ran = (io.fence.run("stop", unit) if static
                   else io.fence.run("disable", "--now", unit))
            if not ran.ok and not (static and _not_loaded(ran)):
                io.fail(change, ran, remedy)
                return False
        if change.kind == "timer":
            ran = io.fence.run("stop", f"{self._stem(change.module, change.name)}.service")
            if not ran.ok and not _not_loaded(ran):
                io.fail(change, ran, remedy)
                return False
        return True

    def _drop(self, io: ApplyIO, change: UnitChange) -> tuple[Path, ...]:
        """Disable and stop each socket unit a set drops; the ones the supervisor let go,
        whose files are then deleted. One it refuses keeps its file, so the next plan
        drops it again."""
        out = []
        for path in change.dropped:
            ran = io.fence.run("disable", "--now", path.name)
            if ran.ok:
                out.append(path)
            else:
                io.fail(change, ran, f"{path.name} is left in place; fix the cause and run "
                                     f"`moxi install {change.module}` again")
        return tuple(out)

    def _unlink_login(self, io: ApplyIO, change: UnitChange) -> None:
        """A changed daemon's service whose old text has an install section and whose new
        text has none (it gains a socket under `start = "connection"`, or switches to
        it): `disable` it without `--now`, so the login link the old text made goes and a
        login no longer starts it. Whether it stops is the start rule's to decide."""
        if (change.kind == "daemon" and change.action == "change" and change.paths
                and _installs(change.paths[0]) and not _text_installs(change.text)):
            self._run(io, change, "disable", self._id(change))

    def _start(self, io: ApplyIO, change: UnitChange) -> bool:
        """Enable and start a written daemon or timer as its change asks. True when every
        step it ran passed."""
        unit = self._id(change)
        if not change.enabled:
            if change.action == "change" and change.enabled_before:
                return self._run(io, change, "disable", "--now", unit)
            return True
        if change.action == "add" or change.enabled_before is False:
            return self._run(io, change, "enable", "--now", unit)
        if _keeps(change):
            if self._run(io, change, "enable", unit):
                io.say(change, f"{unit} rewritten; the new code runs from its next start")
                return True
            return False
        return self._run(io, change, "enable", unit) and self._run(io, change, "restart",
                                                                     unit)

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

    def foreign(self, ref: str, fence: Fence | None, kind: str | None = None) -> UnitRef:
        """A foreign unit; a timer's ids are its `.timer` and the unit its `Triggers`
        names, or, with `fence` None (no supervisor call), its `.timer` alone. Its kind
        is its suffix's, whatever `kind` says."""
        if ref.endswith(".timer"):
            if fence is None:
                return UnitRef(None, ref, "timer", (ref,))
            ran = fence.run("show", "--property=Id,Triggers", ref)
            block = _by_id(ran, [ref]).get(ref, {})
            triggers = tuple(_one(block, "Triggers").split())
            return UnitRef(None, ref, "timer", (ref, *triggers))
        return UnitRef(None, ref, "daemon", (ref,))

    def ids(self, module: str, name: str, kind: str) -> tuple[str, ...]:
        """The ids the writer gives a unit: a daemon's `.socket` (file or not), each
        `<stem>-<k>.socket` whose file is there, in unit order, then its `.service`; a
        timer's `.timer` then its `.service`."""
        stem = self._stem(module, name)
        if kind == "timer":
            return (f"{stem}.timer", f"{stem}.service")
        return (f"{stem}.socket", *self._more_sockets(stem), f"{stem}.service")

    def files(self, module: str, name: str, kind: str) -> tuple[Path, ...]:
        """The unit files the writer gives a unit: a daemon's `.service` and `.socket`,
        file or not, then each `<stem>-<k>.socket` there; a timer's `.service` and
        `.timer`."""
        stem = self._stem(module, name)
        if kind == "timer":
            names = (f"{stem}.service", f"{stem}.timer")
        else:
            names = (f"{stem}.service", f"{stem}.socket", *self._more_sockets(stem))
        return tuple(self.root / unit for unit in names)

    def _more_sockets(self, stem: str) -> tuple[str, ...]:
        """The `<stem>-<k>.socket` files there, in unit order, marked or not."""
        placed = []
        try:
            entries = list(self.root.glob(f"{stem}-*.socket"))
        except OSError:
            return ()
        for path in entries:
            place = socket_place(stem, path.stem)
            if place:
                placed.append((place, path.name))
        return tuple(name for _, name in sorted(placed))

    def _socket_of(self, unit: UnitRef) -> tuple[str, ...]:
        """A marked daemon's socket units, in unit order: each file at a socket name of
        its stem that carries its mark."""
        if unit.kind != "daemon" or not unit.marked:
            return ()
        for ref in self.marked(unit.module):
            if ref.kind == "socket" and ref.name == unit.name:
                return ref.ids
        return ()

    def act(self, act: str, unit: UnitRef, fence: Fence) -> Acted:
        """A daemon with socket units has them acted on first, in unit order, for every
        act but `restart`, which restarts the service alone. A marked service with no
        install section (a socket daemon under `start = "connection"`) gets `start` and
        `stop` for `enable` and `disable`; one with an install section `enable --now` and
        `disable --now`."""
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
            sockets, service = self._socket_of(unit), unit.ids[0]
            static = unit.marked and self._static(unit)
            if act == "restart":
                run("restart", service)
            else:
                for one in sockets:
                    run(*((act, "--now", one) if act in ("enable", "disable")
                          else (act, one)))
                if act in ("enable", "disable") and static:
                    run("start" if act == "enable" else "stop", service)
                elif act in ("enable", "disable"):
                    run(act, "--now", service)
                else:
                    run(act, service)
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
        `show` fails, every unit is `unknown` with the failure in `detail`. A marked
        daemon whose service text has no install section reads `static`, so its `enabled`
        is its socket's from the same read; one with an install section keeps its
        service's own `UnitFileState`."""
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
        return enabled_by_socket(units, states, self._static)

    def _static(self, unit: UnitRef) -> bool:
        """Whether a marked daemon's service has no install section."""
        return not _installs(unit.paths[0] if unit.paths else self.root / unit.ids[0])

    def _state(self, unit: UnitRef, shown: Mapping, ran: Ran) -> UnitState:
        if unit.kind == "socket" and ran.ok:
            return socket_state(unit, self.name,
                                [_socket_read(i, shown.get(i, {})) for i in unit.ids],
                                self.log(unit))
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
        since = _since(head) if state == "active" else None
        last = None
        if unit.kind == "timer" and service and service is not head:
            last = {"result": _one(service, "Result"),
                    "status": _one(service, "ExecMainStatus")}
        wrapped = None
        if unit.kind == "timer" and not unit.marked and service is not head:
            wrapped = self._wrapped(unit.ids[0], _one(service, "DropInPaths").split())
        # The unit the `.timer` names, from its own block: a timer names one.
        named = _one(head, "Triggers").split() if unit.kind == "timer" else []
        return UnitState(unit.module, unit.name, unit.kind, self.name, state, pid=pid,
                         since=since, last=last, detail=detail, log=self.log(unit),
                         enabled=enabled, wrapped=wrapped,
                         triggers=named[0] if named else None)

    def log(self, unit: UnitRef) -> str | None:
        """`journalctl --user -u <unit>`: a timer's service, where its runs write; a
        socket set's every unit, one `-u` each."""
        if unit.kind == "socket":
            return "journalctl --user " + " ".join(f"-u {i}" for i in unit.ids)
        target = unit.ids[1] if unit.kind == "timer" and len(unit.ids) > 1 else unit.ids[0]
        return f"journalctl --user -u {target}"

    # -- a foreign timer's wrap ----------------------------------------------------------------

    def _dropin(self, service: str, module: str) -> Path:
        return self.root / f"{service}.d" / dropin_name(module)

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
            if mark:
                return no(f"{service} is wrapped by {module} of another Moxi home "
                          f"({os.path.basename(other)})")
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
        # The gate is told the home rather than the service given `MOXI_HOME`, which
        # would reach the foreign unit's own command too.
        home = environment(module).get("MOXI_HOME")
        run = [gate, "run"] + (["--home", home] if home else [])
        text = _text([
            f"{WRAP_MARK}{record}",
            f"# moxi: timer = {ref}, if = {switch}",
            "[Service]",
            "ExecCondition=" + command_line([*run, "--if", switch, "--record", record,
                                             "--condition"]),
            "ExecStopPost=" + command_line([*run, "--record", record, "--result"]),
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
        for path in sorted(self.root.glob(f"*.d/{dropin_name(module)}")):
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


def dropin_name(module: str) -> str:
    """The file name of `module`'s wrap drop-in: `50-moxi-<module>.conf`, or
    `50-moxi-<tag>-<module>.conf` for a tagged home."""
    tag = home_tag()
    return f"50-moxi-{module}.conf" if tag is None else f"50-moxi-{tag}-{module}.conf"


def _environment(spec: Spec) -> list[str]:
    """A service's `Environment=` lines: one per variable the unit runs with."""
    return [environment_line(name, value) for name, value in spec.environment]


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


class SocketStarts:
    """The start rule for the daemons with a socket in one `apply`, on systemd and on the
    fake backend that stands in for it.

    systemd refuses to start a socket unit while its service runs, and a `start` of a
    unit that is already active does nothing. So no step here starts a socket unit while
    its service runs, and every start of a service comes after each unit of its set.

    The service of a daemon with a socket is started or restarted only for a new daemon,
    a changed daemon with `upgrade = "restart"`, a changed socket set (whatever `upgrade`
    says), a flip of `enabled` to true, and, with `restart=True`, an unchanged daemon with
    `upgrade = "restart"` that runs. Every other one is left as it is: an `upgrade =
    "keep"` daemon whose set is unchanged keeps its old code if it runs, and stays down
    if it is down until its next connection, or until `resume` starts it after its
    sockets once a home move is done.

    `read` asks, in one `show` before the files are written, whether those services run
    (a new daemon has none yet and is not read). A read that fails counts every service
    it covers running. Then `start`, for each one:

    - found down: the set's own steps (a new unit `enable --now`, a changed one `enable`
      then `restart`), a `start` of each unit of the set it has not just started, so a
      set the home move stopped listens again, then `start` of the service;
    - found running, its set unchanged: `restart` of the service alone, as its units
      listen;
    - found running with its set changed, or counted running: `stop` of the service
      first, then the same as found down. Under a failed read every start and restart is
      therefore `stop`, the units' starts, then `start`; a `stop` of a service that is
      down does nothing.

    A `stop` that fails starts nothing more of that daemon: its new and changed units
    are only enabled, so no socket start meets a service that may still run. Its remedy
    line names the `systemctl --user` steps left undone, since a second `moxi install`
    finds the files up to date. A unit
    start that fails is a failed step, and the service is still started after the other
    units: it serves on the addresses that bound.

    A unit the set drops was disabled and stopped with the removes, so it is never
    started here. A flip of `enabled` to false disables each unit now and stops the
    service.

    A service whose text has an install section (`start = "login"`) is also enabled, so
    its login link follows its text whatever the rule decides about running it: where
    the rule says `start` of the service it gets `enable --now`, where it says `restart`
    alone `enable` then `restart`, and a changed daemon the rule leaves as it is gets
    `enable` alone with the "rewritten" line. A flip of `enabled` to false is `disable
    --now` of it, after its units. That holds for `restart=True`'s unchanged daemon too:
    its `enable` does nothing to an enabled unit, and makes the link again if someone
    took it away.

    A login-only change, a changed daemon whose old and new service texts differ only in
    that one has the install section and the other none (its `start` moved between
    `login` and `connection`, its socket set unchanged), is never stopped or restarted,
    whatever `upgrade` says: it is enabled when the new text has the section and
    disabled without `--now` (the unlink before the rule) when it doesn't, and the
    running service goes on. With `restart=True` it is treated as an unchanged daemon,
    so an upgrade's restart still reaches it. A change with anything else in it follows
    the rule above.

    `installs(text)` says whether a service's text has an install section, in the
    backend's own text; `login_only(change)` whether a change is login-only, read from
    the file on disk, so before the files are written."""

    def __init__(self, backend, writes: Sequence[UnitChange], keeps: Sequence[UnitChange],
                 *, restart: bool, socketed, installs, login_only):
        self._backend = backend
        self._restart = restart
        self._installs = installs
        #: The daemons whose change is login-only, read before the files are written.
        self._login_only = {(c.module, c.name) for c in writes
                            if c.kind == "daemon" and c.action == "change" and login_only(c)}
        #: (module, name) -> [daemon change or None, socket change or None]
        self._daemons: dict[tuple[str, str], list] = {}
        for change in writes:
            if change.kind == "socket":
                self._of(change)[1] = change
            elif socketed(change):
                self._of(change)[0] = change
        if restart:
            for change in keeps:
                key = (change.module, change.name)
                if change.kind == "daemon" and socketed(change) and (
                        key not in self._daemons or self._daemons[key][0] is None):
                    self._of(change)[0] = change
        #: How each written socket unit stands to its file, read before it is written.
        self._news: dict[Path, str] = {}
        for change in writes:
            if change.kind == "socket":
                for path, text in zip(change.paths, change.texts):
                    self._news[path] = ("new" if change.enabled_before is False
                                        else _differs(path, text))
        self._running: frozenset[str] = frozenset()
        self._counted = False
        self._done: set[tuple[str, str]] = set()
        self._ok = True

    def _of(self, change: UnitChange) -> list:
        return self._daemons.setdefault((change.module, change.name), [None, None])

    def owns(self, change: UnitChange) -> bool:
        """Whether `change` is a daemon with a socket, or its set, that `start` takes."""
        return (change.module, change.name) in self._daemons and change.kind in (
            "daemon", "socket")

    def _service(self, key: tuple[str, str]) -> str:
        return f"{self._backend._stem(*key)}.service"

    def _login(self, key) -> bool:
        """Whether the daemon's service text has an install section, so a login starts
        it and the rule enables it: its new text when it is written, else the file's."""
        daemon, _ = self._daemons[key]
        if daemon is not None:
            return self._installs(daemon.text)
        try:
            text = (self._backend.root / self._service(key)).read_text(
                encoding="utf-8", errors="replace")
        except OSError:
            return False
        return self._installs(text)

    def _enabled(self, key) -> bool:
        daemon, sockets = self._daemons[key]
        return (daemon or sockets).enabled

    def _new(self, key) -> bool:
        daemon, _ = self._daemons[key]
        return daemon is not None and daemon.action == "add"

    def _acted(self, key) -> bool:
        """Whether `apply` starts or restarts this daemon's service (given it runs, for
        `restart=True`'s unchanged daemon)."""
        daemon, sockets = self._daemons[key]
        if not self._enabled(key):
            return False
        if sockets is not None or daemon is None:
            return sockets is not None
        if daemon.action == "add":
            return True
        if daemon.action == "change" and key not in self._login_only:
            return not _keeps(daemon) or daemon.enabled_before is False
        return self._restart and daemon.entry.get("upgrade", "restart") == "restart"

    def read(self, fence: Fence) -> None:
        """Read whether the services `apply` would start or restart run, in one `show`; a
        read that fails counts each of them running."""
        ids = [self._service(key) for key in self._daemons
               if self._acted(key) and not self._new(key)]
        if not ids:
            return
        try:
            self._running = self._backend.running(ids, fence)
        except ReadError:
            self._running = frozenset(ids)
            self._counted = True

    def start(self, io: ApplyIO, change: UnitChange) -> bool:
        """Carry out the rule for the daemon `change` belongs to, once per daemon. True
        when every step it ran passed."""
        key = (change.module, change.name)
        if key in self._done:
            return True
        self._done.add(key)
        self._ok = True
        self._start(io, key)
        return self._ok

    def restart_kept(self, io: ApplyIO) -> None:
        """`restart=True`'s unchanged daemons with a socket, once the written ones are
        done."""
        for key in self._daemons:
            if key not in self._done:
                self._done.add(key)
                self._start(io, key)

    def _start(self, io: ApplyIO, key: tuple[str, str]) -> None:
        daemon, sockets = self._daemons[key]
        owner = daemon or sockets
        service = self._service(key)
        login = self._login(key)
        if not self._enabled(key):
            if sockets is not None and sockets.action == "change" and sockets.enabled_before:
                for path in sockets.paths:
                    self._run(io, sockets, "disable", "--now", path.name)
            if daemon is not None and daemon.action == "change" and daemon.enabled_before:
                self._run(io, daemon, *(("disable", "--now") if login else ("stop",)),
                          service)
            return
        login_only = key in self._login_only
        if not self._acted(key):
            if daemon is not None and daemon.action == "change":
                # Its login link is made or kept even though nothing starts it now.
                if login and not self._run(io, daemon, "enable", service):
                    return
                io.say(daemon, _login_line(service, login) if login_only else
                       f"{service} rewritten; the new code runs from its next start")
            return
        found = "down" if self._new(key) else (
            "counted" if self._counted
            else "running" if service in self._running else "down")
        if daemon is not None and (daemon.action == "keep" or login_only) \
                and sockets is None and found == "down":
            # `restart=True` restarts only a service that runs; a login-only change still
            # gets its login link.
            if login_only and login:
                self._run(io, daemon, "enable", service)
            return
        if found == "running" and sockets is None:
            if not login or self._run(io, owner, "enable", service):
                self._run(io, owner, "restart", service)
            return
        if found != "down":
            ran = io.fence.run("stop", service)
            if not ran.ok and not _not_loaded(ran):
                self._ok = False
                io.fail(owner, ran, self._after_failed_stop(key))
                if sockets is not None:
                    for path in sockets.paths:
                        if self._news.get(path) in ("new", "changed"):
                            self._run(io, sockets, "enable", path.name)
                return
        if sockets is not None:
            for path in sockets.paths:
                if self._news.get(path) == "new":
                    self._run(io, sockets, "enable", "--now", path.name)
                elif self._news.get(path) == "changed" and self._run(
                        io, sockets, "enable", path.name):
                    self._run(io, sockets, "restart", path.name)
        for path in self._units(key):
            if self._news.get(path, "same") == "same":
                self._run(io, owner, "start", path.name)
        last = ("enable", "--now") if login else ("start",)
        if self._run(io, owner, *last, service) and daemon is not None \
                and _keeps(daemon) and sockets is not None and found != "down":
            io.say(daemon, f"{service} rewritten and restarted, since its socket changed")

    def _after_failed_stop(self, key) -> str:
        """The remedy for a failed `stop` of the service. The files are written, so a
        second `moxi install` finds them up to date and runs none of the steps; the
        remedy names them, in the order the rule runs them."""
        service = self._service(key)
        paths = self._units(key)
        fresh = [p.name for p in paths if self._news.get(p, "same") != "same"]
        same = [p.name for p in paths if self._news.get(p, "same") == "same"]
        steps = [f"`systemctl --user stop {service}`"]
        if fresh:
            steps.append(f"`systemctl --user restart {' '.join(fresh)}`")
        if same:
            steps.append(f"`systemctl --user start {' '.join(same)}`")
        login = self._login(key)
        steps.append(f"`systemctl --user {'enable --now' if login else 'start'} {service}`")
        line = (f"the unit files are written, and `moxi install {key[0]}` again finds "
                f"them up to date; fix the cause, then run {', '.join(steps[:-1])} and "
                f"{steps[-1]}")
        if login:
            line += (f"; {service} was not enabled, so the login link its text asks for "
                     "is not made until that `enable` runs")
        return line

    def _units(self, key) -> tuple[Path, ...]:
        """The daemon's socket units: its written set's, or, when the set is unchanged
        (and so not in the apply), the marked ones on disk."""
        _, sockets = self._daemons[key]
        if sockets is not None:
            return sockets.paths
        for ref in self._backend.marked(key[0]):
            if ref.kind == "socket" and ref.name == key[1]:
                return ref.paths
        return ()

    def _run(self, io: ApplyIO, change: UnitChange, verb: str, *args: str) -> bool:
        ran = io.fence.run(verb, *args)
        if not ran.ok:
            self._ok = False
            io.fail(change, ran)
        return ran.ok


def _socketed(change: UnitChange) -> bool:
    """Whether a written daemon's new text is a service with sockets (`Sockets=`), which
    the start rule takes, whatever its `start`."""
    return change.kind == "daemon" and any(
        line.startswith("Sockets=") for line in (change.text or "").splitlines())


def _login_only(change: UnitChange) -> bool:
    """Whether a changed daemon's service file on disk and its new text differ only in
    that one of them has the install section and the other none. False when the file
    can't be read."""
    if change.kind != "daemon" or change.action != "change" or not change.paths:
        return False
    try:
        old = change.paths[0].read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    new = change.text or ""
    return (_text_installs(old) != _text_installs(new)
            and _outside_install(old) == _outside_install(new))


def _outside_install(text: str) -> list[str]:
    """A unit text's lines less its `[Install]` section, with trailing blank lines
    dropped."""
    out: list[str] = []
    skip = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            skip = stripped == "[Install]"
        if not skip:
            out.append(line)
    while out and not out[-1].strip():
        out.pop()
    return out


def _login_line(unit: str, login: bool) -> str:
    """The line for a login-only change, which runs no stop or restart."""
    now = "starts it from now on" if login else "no longer starts it"
    return f"{unit} rewritten: a login {now}; a running daemon is left as it is"


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


def _since(block: Mapping[str, list[str]]) -> float | None:
    """The wall time of a unit's `ActiveEnterTimestampMonotonic`, or None."""
    mono = _int(_one(block, "ActiveEnterTimestampMonotonic"))
    return time.time() - (time.monotonic() - mono / 1e6) if mono else None


def _socket_read(unit: str, block: Mapping[str, list[str]]
                 ) -> tuple[str, str, bool | None, float | None, str]:
    """One socket unit's read from its `show` block, as `socket_state` takes it."""
    active = _one(block, "ActiveState")
    if not block or _one(block, "LoadState") == "not-found":
        state = "missing"
    else:
        state = ("active" if active in ("active", "activating", "reloading")
                 else "failed" if active == "failed" else "inactive")
    enabled = {"enabled": True, "disabled": False}.get(_one(block, "UnitFileState"))
    since = _since(block) if state == "active" else None
    return (unit, state, enabled, since, _one(block, "InvocationID"))


def _int(text: str) -> int | None:
    try:
        return int(text) or None
    except ValueError:
        return None


def _differs(path: Path, text: str) -> str:
    """`new`, `changed` or `same`: how `text` stands to the file at `path` now."""
    try:
        old = path.read_bytes()
    except FileNotFoundError:
        return "new"
    return "same" if old == text.encode("utf-8") else "changed"


def _text(lines: list[str]) -> str:
    return "\n".join(lines) + "\n"


BACKEND = SystemdBackend()

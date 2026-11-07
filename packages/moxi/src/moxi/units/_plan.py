"""`plan`: what writing a module's declared units would change, unit by unit.

`plan` reads the module's merged declarations, resolves each unit for writing (its command
in this environment, its switch, its socket's port and what starts it), renders it through
the backend, and compares the new text with what is on disk. It writes nothing, so it
takes no lock. Its only supervisor call is the read of one port check below.

A unit's name says its home (`moxi.units.home_tag`) and nothing else, so a module's units
are refused, cause `locator`, while this process's environment sets any of that module's
own locators (`MOXI_<M>_HOME` and the rest), in any home: they would move the module's
stores under a name that doesn't say so, and in the default home rewrite its units. A
second store is made with `MOXI_HOME`. The names, the variables a unit runs with and this
check read this process's environment; `env` is for the module's settings alone.

A daemon's sockets. On a backend that writes them as units of their own (systemd), they
are one `socket` change over the whole set: `add` when none is written, `keep` when the
written set names the same files and each is byte-equal, else `change`, with the written
units the render no longer names in `dropped`. The service names the set, so a set that
grows or shrinks changes the daemon too.

No IPv6 loopback. Once per `plan` call, before the first daemon with an IPv6 address is
rendered (on a backend that writes sockets at all), `plan` creates a TCP socket, binds it
to `[::1]:0` and closes it. When the create or the bind fails with `EADDRNOTAVAIL` or
`EAFNOSUPPORT`, every IPv6 address in that plan is left out and each change it touches
says so in its reason; any other error, or none, keeps them. An IPv4 address is never
left out, and a daemon left with no address is refused, cause `socket`. Each address
keeps its place, so the others keep their names and a later plan whose bind works adds
the left-out ones back as a `change`.

One port, one home. A daemon with a socket is refused, cause `socket`, only when the plan
can show that what holds one of its addresses is not this home's own daemon, and never for
the port alone, so a home re-installed while its daemon runs is never refused for it. Two
checks run, after `foreign` is decided (a daemon refused `foreign` gets neither):

1. Another marked socket file names the address. Every home's unit files share the
   supervisor's directory, so the plan reads every marked socket file there
   (`Backend.socket_files`), and one that is not this module's own (`marked()`) and names
   the same address and port refuses it, whether its daemon runs or not. A home's own
   names are the ones it gives itself, so for a home given by `MOXI_HOME` the default
   home's untagged file is another's, and for the default home every tagged one is.
2. A listener that isn't this home's daemon (`_Listeners`). Nothing connects when this
   home's own socket unit or job names the address and that unit or job is `active`,
   whatever the set's other units do; else a TCP connect that nothing answers leaves the
   address free; else this home's daemon must be running with a process (or one under
   it) that listens there, or the address is refused. When the supervisor's read is
   `unknown` or the process can't be read, the plan says so in one line on stderr and
   doesn't refuse.

Task Scheduler writes no socket files, so there a stopped daemon of another home is seen by
neither check: the start that comes second fails on the port.
"""

from __future__ import annotations

import errno
import sys
from collections.abc import Iterable, Mapping
from dataclasses import replace as _replaced
from pathlib import Path

from . import _backend
from ._backend import RenderError, Schedule, Socket, Spec, UnitRef
from ._declare import digest, merge, switch_problem, switch_value
from ._home import environment, locators_set
from ._types import DeclarationError, Declarations, UnitChange

_DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

#: The errors of the IPv6 probe that mean the system has no IPv6 loopback.
NO_IPV6 = (errno.EADDRNOTAVAIL, errno.EAFNOSUPPORT)


def plan(module: str, *, only: Iterable[str] | None = None,
         env: Mapping[str, str] | None = None) -> list[UnitChange]:
    """What applying module `module`'s declarations would change, one `UnitChange` per
    unit (a daemon's `socket` apart where the backend writes it as a unit of its own).

    `only` names daemons and timers: only those are planned (each with its socket), and
    no unit outside them is planned `remove`. `env` is the environment the module's
    settings (a daemon's switch, a socket's port, the switch a socket's `start` names) are
    read in. Raises `ValueError` for a module that is not present.
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
    set_here = locators_set(module)
    if set_here:
        which = ", ".join(set_here)
        reason = (f"{which} {'is' if len(set_here) == 1 else 'are'} set, and a unit's name "
                  "says only its Moxi home, so these units would run with stores their "
                  "name doesn't say; a second store is made with MOXI_HOME, which gives "
                  f"its units names of their own; fix: unset {which}")
        return _refuse_all(module, declared, marked, wanted, "locator", reason)
    if _editable(found):
        reason = (f"{found.distribution} is an editable install; units are written only "
                  f"from an installed release: `moxi install {module}` in a uv tool "
                  "environment")
        return _refuse_all(module, declared, marked, wanted, "editable", reason)

    own = frozenset(path for refs in marked.values() for ref in refs for path in ref.paths)
    planner = _Planner(found, impl, env, environment(module), own)
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


def _refuse_all(module, declared, marked, wanted, cause, reason) -> list[UnitChange]:
    """Every declared unit, and every marked one the plan would otherwise remove,
    refused with `cause` and `reason`."""
    names = {name for _, name, _ in declared}
    out = [_unit(module, name, kind, "refuse", cause, reason, entry)
           for kind, name, entry in declared]
    out += [_unit(module, ref.name, ref.kind, "refuse", cause, reason, None, ref.paths)
            for name, refs in marked.items() if name not in names
            for ref in refs if wanted is None or name in wanted]
    return out


class _Planner:
    """The per-unit half of `plan`, holding what one module's units share."""

    def __init__(self, found, impl, env, environ=None, own=frozenset()):
        self.found = found
        #: The files `marked()` gives this module here: this home's own.
        self.own = frozenset(own)
        self.module = found.name
        self.impl = impl
        self.env = env
        self.environment = tuple(sorted((environ or {}).items()))
        self._schema: list = []
        self._ipv6: list[bool] = []
        self._socket_files: list = []

    def ipv6(self) -> bool:
        """Whether this system has an IPv6 loopback, probed once per plan."""
        if not self._ipv6:
            self._ipv6.append(ipv6_loopback())
        return self._ipv6[0]

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
        note = ""
        if kind == "daemon" and "socket" in entry:
            socket = self._socket(entry["socket"])
            if isinstance(socket, str):
                return [_unit(module, name, kind, "refuse", "socket", f"{unit}: {socket}",
                              entry, _paths(refs))]
            if socket.left_out:
                note = (f"{', '.join(socket.left_out)} left out: no IPv6 loopback on this "
                        "system")
                if not socket.listens:
                    return [_unit(module, name, kind, "refuse", "socket",
                                  f"{unit}: its socket has no address left to listen on "
                                  f"({note})", entry, _paths(refs))]
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
                    schedule=_schedule(entry) if kind == "timer" else None, socket=socket,
                    environment=self.environment)
        try:
            rendered = self.impl.render(spec)
        except RenderError as exc:
            return [_unit(module, name, kind, "refuse", exc.cause, exc.text, entry,
                          _paths(refs))]
        stamp = digest(entry) if kind == "timer" and switch is not None else None
        written = {ref.kind: ref for ref in refs}
        out = [self._compare(name, group, entry, stamp, written.get(group.kind))
               for group in rendered]
        if socket is not None and not any(c.cause == "foreign" for c in out):
            # After `foreign`: an unmarked file at this home's name is that file's daemon,
            # and only `--replace` gets past it.
            clash = self._held_elsewhere(name, socket, refs)
            if clash is not None:
                out = [_replaced(c, action="refuse", cause="socket",
                                 reason=f"{unit}: {clash}") for c in out]
        if note:
            out = [_replaced(c, reason=f"{c.reason}; {note}") for c in out]
        kinds = {group.kind for group in rendered}
        out += [_remove(module, ref, entry, "no longer declared")
                for ref in refs if ref.kind not in kinds]
        return out

    # -- one port, one home ---------------------------------------------------------------

    def socket_files(self):
        """`Backend.socket_files()`, read once per plan."""
        if not self._socket_files:
            read = getattr(self.impl, "socket_files", None)
            self._socket_files.append(tuple(read()) if read is not None else ())
        return self._socket_files[0]

    def _held_elsewhere(self, name: str, socket: Socket, refs: list[UnitRef]) -> str | None:
        """Why daemon `name`'s addresses are not free for it, or None. Its address is
        refused only when the plan can show that what holds it is not this home's own
        daemon: another home's marked socket file names it (`_named_elsewhere`), or a
        listener answers on it that this home's daemon doesn't hold (`_Listeners`). The
        port alone never refuses."""
        wanted = [(_address(address), socket.port) for _, address in socket.listens]
        fix = (f"fix: give this home's {self.module}/{name} another port: moxi config set "
               f"{self.module} {socket.setting} <port>")
        named = self._named_elsewhere(wanted)
        if named is not None:
            return f"{named}; {fix}"
        held = _Listeners(self, name, refs).elsewhere(wanted)
        return None if held is None else f"{held}; {fix}"

    def _named_elsewhere(self, wanted) -> str | None:
        """A marked socket file outside this module's own (`marked()`) that names one of
        `wanted`, described, or None. Every home's files share the supervisor's unit
        directory, so this sees another home's daemon whether it runs or not."""
        for path, mark, listens in self.socket_files():
            if path in self.own:
                continue
            for address, port in wanted:
                if (address, port) in listens:
                    whose = ("of this Moxi home" if self._this_home(path, mark)
                             else "of another Moxi home; if that home is gone, remove the "
                                  "file by hand")
                    return (f"{_where(address, port)} is named by {path}, the socket of "
                            f"{mark} {whose}")
        return None

    def _this_home(self, path: Path, mark: str) -> bool:
        """Whether `path` is at a name this home gives the unit `mark` names."""
        module, _, name = mark.partition("/")
        try:
            return path in self.impl.files(module, name, "daemon")
        except Exception:  # noqa: BLE001 - a mark that names no unit names no home's
            return False

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
        start = declared.get("start", "connection")
        if isinstance(start, Mapping):
            start = self._start(start["login"])
            if start not in ("login", "connection"):
                return start
        addresses = tuple(declared["addresses"])
        left_out: tuple[str, ...] = ()
        if self.impl.sockets != "none":
            six = tuple(a for a in addresses if _is_ipv6(a))
            if six and not self.ipv6():
                left_out = six
        return Socket(setting, addresses, port, left_out, start)

    def _start(self, key: str) -> str:
        """A switch-form `start` read now: `login` while the bool setting `key` is true,
        `connection` while it is false; else the reason the socket is refused. The key is
        checked as a daemon's switch is."""
        problem = self._switch_problem(key)
        if problem:
            return f"its socket start {problem.removeprefix('its ')}"
        from .. import config

        try:
            on = switch_value(self.module, self.schema(), key, self.env)
        except config.ConfigError as exc:
            return f"its socket start's switch {key} cannot be read: {exc}"
        return "login" if on else "connection"

    def _compare(self, name: str, group, entry: Mapping, stamp: str | None,
                 written: UnitRef | None = None) -> UnitChange:
        """The change for one rendered group. `written` is the marked unit of the same
        kind already there; for a `socket` group its files the render no longer names
        are the change's `dropped`."""
        module, impl = self.module, self.impl
        unit = f"{module}/{name}"
        paths = tuple(path for path, _ in group.files)
        texts = tuple(text for _, text in group.files)
        dropped: tuple[Path, ...] = ()
        if group.kind == "socket" and written is not None:
            dropped = tuple(path for path in written.paths if path not in paths)
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
                      enabled_before=enabled_before, digest=stamp, dropped=dropped)
        if foreign is not None:
            path, mark = foreign
            what = (f"is marked for {mark}" if mark and "/" in mark
                    else mark if mark and mark.startswith("cannot")
                    else "was not written by moxi")
            reason = (f"{path.name} exists and {what}; `moxi install {module} --replace` "
                      "moves it to DATA/moxi/displaced/")
            return UnitChange(module, name, group.kind, "refuse", "foreign",
                              reason=reason, foreign=path, **common)
        if missing == len(paths) and not dropped:
            return UnitChange(module, name, group.kind, "add", None,
                              reason="not installed", **common)
        if same == len(paths) and not dropped:
            return UnitChange(module, name, group.kind, "keep", None, reason="up to date",
                              **common)
        reason = "stale"
        if dropped:
            reason += f" (drops {', '.join(path.name for path in dropped)})"
        return UnitChange(module, name, group.kind, "change", None, reason=reason,
                          **common)


class _Listeners:
    """What listens on one daemon's addresses, and whether it is this home's own daemon.

    For each address, in order: this home's own socket unit or job names it and that
    unit's or job's own state is `active` (the supervisor holds it for this home: nothing
    connects, so a set whose other units are down still holds the addresses its active
    units name); else a TCP connect that nothing answers (free); else this home's daemon
    `active` with a pid whose process, or one under it, listens on it (`listening`).
    Anything else that answers is another's. When the daemon's state is `unknown` or its
    process can't be read, the plan can't tell: it says so in one line on stderr and
    doesn't refuse, and a real clash shows as the daemon's start failing on the port. The
    supervisor is read once, and only when one of these steps needs it."""

    def __init__(self, planner: _Planner, name: str, refs: list[UnitRef]):
        self.planner = planner
        self.name = name
        self.refs = refs
        self._read: list = []

    def elsewhere(self, wanted) -> str | None:
        unit = f"{self.planner.module}/{self.name}"
        paths = {path for ref in self.refs for path in ref.paths}
        named: dict[tuple[str, int], list[Path]] = {}
        for path, _, listens in self.planner.socket_files():
            if path in paths:
                for listen in listens:
                    named.setdefault(listen, []).append(path)
        for address, port in wanted:
            if (address, port) in named:
                sockets = self.states()[1]
                if any(sockets.get(path) is not None and sockets[path].state == "active"
                       for path in named[(address, port)]):
                    continue
            if not answers(address, port):
                continue
            daemon = self.states()[0]
            if daemon is not None and daemon.state == "unknown":
                _say(f"{unit}: something listens on {_where(address, port)}, and the "
                     f"supervisor read failed ({daemon.detail}), so the plan can't tell "
                     "whether it is this home's daemon; not refused")
                continue
            if daemon is not None and daemon.state == "active" and daemon.pid:
                held = listening(daemon.pid, address, port)
                if held is True:
                    continue
                if held is None:
                    _say(f"{unit}: something listens on {_where(address, port)}, and the "
                         f"process of this home's daemon (pid {daemon.pid}) can't be read, "
                         "so the plan can't tell whether it is that daemon; not refused")
                    continue
                return (f"something that is not this home's {unit} listens on "
                        f"{_where(address, port)}, and the running daemon (pid "
                        f"{daemon.pid}) doesn't hold it")
            return (f"something that is not this home's {unit} listens on "
                    f"{_where(address, port)}, and this home's {unit} isn't running")
        return None

    def states(self):
        """`(daemon, sockets)`, read in one supervisor call: the `UnitState` of this home's
        daemon (None when it has nothing written), and the socket state of each of its
        socket files by path. Each socket unit is read on its own, so an address is held
        by the unit that names it whatever its set's other units do; a job's one state is
        every path's."""
        if self._read:
            return self._read[0]
        planner = self.planner
        impl, module, name = planner.impl, planner.module, self.name
        daemon = next((ref for ref in self.refs if ref.kind == "daemon"), None)
        sockets: list[UnitRef] = []
        if daemon is not None and impl.sockets == "job":
            sockets = [UnitRef(module, name, "socket", daemon.ids, daemon.paths,
                               daemon.enabled)]
        elif impl.sockets == "units":
            socket = next((ref for ref in self.refs if ref.kind == "socket"), None)
            if socket is not None and len(socket.ids) == len(socket.paths):
                sockets = [UnitRef(module, name, "socket", (one,), (path,), socket.enabled)
                           for one, path in zip(socket.ids, socket.paths)]
            elif socket is not None:
                sockets = [socket]
        refs = [ref for ref in (daemon, *sockets) if ref is not None]
        read: list = []
        if refs:
            from ._apply import fence
            from ._types import UnitState

            try:
                read = list(impl.status(refs, fence(impl)))
            except Exception as exc:  # noqa: BLE001 - a failed read is `unknown`
                read = [UnitState(module, name, ref.kind, impl.name, "unknown",
                                  detail=str(exc) or type(exc).__name__) for ref in refs]
        if daemon is not None and read:
            daemon_state, read = read[0], read[1:]
        else:
            daemon_state = None
        by_path = {path: state for ref, state in zip(sockets, read) for path in ref.paths}
        self._read.append((daemon_state, by_path))
        return self._read[0]


def answers(address: str, port: int, timeout: float = 0.5) -> bool:
    """Whether a TCP connect to `address`:`port` is answered within `timeout` seconds.
    The connect is closed at once and takes no port."""
    import socket

    try:
        with socket.create_connection((address, port), timeout=timeout):
            return True
    except (OSError, ValueError):
        return False


def listening(pid: int, address: str, port: int) -> bool | None:
    """Whether process `pid`, or a process under it, has a listening TCP socket on
    `address`:`port` (an unspecified address it bound counts for every address), read
    through psutil's per-process read, which needs no privilege for a process of one's
    own user. None when a process can't be read and none of the others listens there."""
    import psutil

    try:
        top = psutil.Process(pid)
        processes = [top, *top.children(recursive=True)]
    except psutil.Error:
        return None
    unread = False
    for process in processes:
        read = getattr(process, "net_connections", None) or process.connections
        try:
            connections = read(kind="tcp")
        except psutil.NoSuchProcess:
            unread = unread or process is top
            continue
        except psutil.Error:
            unread = True
            continue
        for connection in connections:
            local = connection.laddr
            if (connection.status == psutil.CONN_LISTEN and local and local.port == port
                    and _covers(local.ip, address)):
                return True
    return None if unread else False


def _covers(bound: str, address: str) -> bool:
    """Whether a socket bound to `bound` takes connections to `address`."""
    import ipaddress

    try:
        mine, theirs = ipaddress.ip_address(bound.split("%")[0]), ipaddress.ip_address(
            address.split("%")[0])
    except ValueError:
        return bound == address
    if mine.version == 6 and mine.ipv4_mapped is not None:
        mine = mine.ipv4_mapped
    if mine == theirs:
        return True
    # A wildcard bind: IPv4's takes IPv4 only; IPv6's may take both (dual stack).
    return mine.is_unspecified and (mine.version == 6 or theirs.version == 4)


def _address(address: str) -> str:
    """`address` in `ipaddress`'s spelling, as `listen_address` gives it."""
    import ipaddress

    try:
        return str(ipaddress.ip_address(address))
    except ValueError:
        return address


def _where(address: str, port: int) -> str:
    return f"[{address}]:{port}" if ":" in address else f"{address}:{port}"


def _say(line: str) -> None:
    print(line, file=sys.stderr)


def ipv6_loopback() -> bool:
    """False when this system has no IPv6 loopback: creating a TCP socket or binding it
    to `[::1]:0` fails with `EADDRNOTAVAIL` or `EAFNOSUPPORT` (or this Python has no
    IPv6 at all). Any other error, or none, is True."""
    import socket

    family = getattr(socket, "AF_INET6", None)
    if family is None:
        return False
    try:
        probe = socket.socket(family, socket.SOCK_STREAM)
    except OSError as exc:
        return exc.errno not in NO_IPV6
    try:
        probe.bind(("::1", 0))
    except OSError as exc:
        return exc.errno not in NO_IPV6
    finally:
        probe.close()
    return True


def _is_ipv6(address: str) -> bool:
    import ipaddress

    return ipaddress.ip_address(address).version == 6


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

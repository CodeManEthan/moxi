"""The wrap proof on this box's own systemd user manager.

Not part of the suite: it runs only with `MOXI_TEST_REAL_SUPERVISOR=1` on a system with a
systemd user manager, and is skipped everywhere else. It writes a throwaway timer and
service under test names, `moxitest-wrap-<8 hex>.timer` and `.service`, into the user unit
directory, and deletes them at its end with their drop-in folder and a `daemon-reload`. It
never names another unit, and it lists the unit directory before and after and fails if
anything else changed. The service's command is a script in the test's tmp that appends
its arguments to a file there.

The rows that run the gate's wrap forms (`moxi run --condition` and `--result`) need the
gate; on a tree without it they are skipped with that reason.

The socket rows write throwaway daemons with sockets through the core's own `plan`,
`apply`, acts and `status`, under a fixture module named `t<8 hex>`, so every file is
`moxi-t<8 hex>-<name>.*`, listening on free ports of the loopback. The shell row's command
is a script in the row's tmp that records the sockets it was handed, answers one
connection on each and exits. One row also writes a hand-made socket unit,
`moxitest-bind-<8 hex>.socket`, with its service. At its end each row removes its module's
units with `units.remove`, then stops and deletes anything left under its names, takes any
`.wants` link to them, and reloads, and it fails if the unit directory listing changed.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shlex
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

import moxi.paths
from moxi.units import _systemd
from moxi.units._fence import REAL_SUPERVISOR, Fence
from moxi.units._systemd import SystemdBackend, command_line, quote
from moxi.units._types import Wrapped

from conftest import drop_modules

MODULE, KEY, NAME = "wraptest", "backup.enabled", "backup"


def _user_manager() -> bool:
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    return bool(shutil.which("systemctl") and os.path.isdir("/run/systemd/system")
                and runtime and os.path.exists(os.path.join(runtime, "systemd", "private")))


pytestmark = pytest.mark.skipif(
    os.environ.get(REAL_SUPERVISOR) != "1" or not _user_manager(),
    reason=f"a proof row on the real user manager: set {REAL_SUPERVISOR}=1 on a system "
           "with a systemd user manager")


def _listing(root: Path) -> dict[str, tuple]:
    """Every entry under `root` with what would show a change: a file's size and mtime,
    a link's target. A directory counts by its name only (adding an entry changes its
    mtime)."""
    out = {}
    for path in sorted(root.rglob("*")):
        rel = str(path.relative_to(root))
        if path.is_symlink():
            out[rel] = ("link", os.readlink(path))
        elif path.is_dir():
            out[rel] = ("dir",)
        else:
            st = path.stat()
            out[rel] = ("file", st.st_size, st.st_mtime_ns)
    return out


def _show(fence: Fence, unit: str, *props: str) -> dict[str, str]:
    ran = fence.run("show", "--property=" + ",".join(props), unit)
    assert ran.ok, ran.failure()
    return dict(line.partition("=")[::2] for line in ran.stdout.splitlines() if line)


class Row:
    """The throwaway pair and what the row reads back."""

    def __init__(self, tmp: Path):
        self.id = os.urandom(4).hex()
        self.stem = f"moxitest-wrap-{self.id}"
        self.timer, self.service = f"{self.stem}.timer", f"{self.stem}.service"
        self.dir = _systemd.unit_dir()
        self.tmp = tmp
        self.home = tmp / "home"
        self.out = tmp / "out.txt"
        self.code = tmp / "code"
        self.code.write_text("0")
        self.fence = Fence("systemd")
        self.backend = SystemdBackend()
        self.env = {"MOXI_HOME": str(self.home)}
        self.runs = moxi.paths.Layout(MODULE).state_dir(self.env) / "runs.jsonl"
        self.config = moxi.paths.Layout(MODULE).config_file(self.env)

    def script(self, name: str, tag: str) -> Path:
        path = self.tmp / name
        path.write_text(f'#!/bin/sh\nprintf "{tag} %s\\n" "$@" >> {shlex.quote(str(self.out))}\n'
                        f'exit "$(cat {shlex.quote(str(self.code))})"\n')
        return path

    def write(self, argv: list[str]) -> None:
        (self.dir / self.service).write_text(
            "[Unit]\nDescription=moxi test: a throwaway service for the wrap proof\n\n"
            "[Service]\nType=oneshot\n"
            f"Environment={quote('MOXI_HOME=' + str(self.home))}\n"
            f"ExecStart={command_line(argv)}\n")
        (self.dir / self.timer).write_text(
            "[Unit]\nDescription=moxi test: a throwaway timer for the wrap proof\n\n"
            f"[Timer]\nOnActiveSec=1d\nUnit={self.service}\n")
        assert self.fence.run("daemon-reload").ok

    def start(self):
        return self.fence.run("start", self.service)

    def show(self, *props: str) -> dict[str, str]:
        return _show(self.fence, self.service, *props)

    def lines(self) -> list[dict]:
        if not self.runs.exists():
            return []
        return [json.loads(line) for line in self.runs.read_text().splitlines() if line]

    def output(self) -> list[str]:
        return self.out.read_text().splitlines() if self.out.exists() else []

    def switch(self, on: bool) -> None:
        self.config.parent.mkdir(parents=True, exist_ok=True)
        table, _, key = KEY.partition(".")
        self.config.write_text(f"[{table}]\n{key} = {'true' if on else 'false'}\n")

    def clean(self) -> None:
        for name in (self.timer, self.service):
            try:
                (self.dir / name).unlink()
            except FileNotFoundError:
                pass
        shutil.rmtree(self.dir / f"{self.service}.d", ignore_errors=True)
        # Cleanup only: forget a failed run of the throwaway service.
        subprocess.run(["systemctl", "--user", "reset-failed", self.service, self.timer],
                       capture_output=True, timeout=30)
        self.fence.run("daemon-reload")


def _gate() -> bool:
    return importlib.util.find_spec("moxi.run") is not None


def test_wrap_on_this_boxs_user_manager(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MOXI_HOME", str(tmp_path / "home"))
    row = Row(tmp_path)
    before = _listing(row.dir)
    assert not any(k.startswith("moxitest-") for k in before)
    report: list[str] = []
    try:
        first = row.script("first.sh", "first")
        odd = ["a b", "50%", "$HOME", 'q"x', "back\\slash", ";", "it's"]
        row.write(["/bin/sh", str(first), *odd])

        # The service runs as written, its arguments quoted by the backend's rules.
        assert row.start().ok
        assert row.output() == [f"first {a}" for a in odd]
        report.append(f"quoting: {row.output()}")
        row.out.unlink()

        acted = row.backend.wrap(row.timer, module=MODULE, key=KEY, name=NAME,
                                 fence=row.fence)
        report.append(f"wrap: ok={acted.ok} lines={acted.lines}")
        assert acted.ok, acted.lines
        dropin = row.dir / f"{row.service}.d" / f"50-moxi-{MODULE}.conf"
        text = dropin.read_text()
        assert str(dropin) in row.show("DropInPaths")["DropInPaths"].split()
        unit = row.backend.foreign(row.timer, None)
        [state] = row.backend.status([unit], row.fence)
        assert state.wrapped == Wrapped(MODULE, KEY, NAME)
        report.append(f"status: {state.state} wrapped={state.wrapped} last={state.last}")

        if _gate():
            # Off (no config file): skipped, not failed, the script not run, one `off` line.
            assert row.start().ok
            shown = row.show("Result", "ActiveState")
            report.append(f"off: {shown} lines={row.lines()}")
            # systemd hands `exec-condition` to ExecStopPost= in $SERVICE_RESULT; the
            # unit's own Result property reads `success` after the skip (systemd 258).
            assert shown["ActiveState"] != "failed"
            assert shown["Result"] in ("success", "exec-condition")
            assert row.output() == []
            assert [r["result"] for r in row.lines()] == ["off"]

            # On: the script runs and one `ran` line carries its exit and seconds.
            row.switch(True)
            assert row.start().ok
            assert row.output() == [f"first {a}" for a in odd]
            ran = row.lines()[-1]
            report.append(f"on: {ran}")
            assert (ran["result"], ran["exit"]) == ("ran", 0) and "seconds" in ran
            assert len(row.lines()) == 2

            # Exit 4: `ran`, exit 4, and the unit failed as the foreign unit makes it.
            row.code.write_text("4")
            assert not row.start().ok
            ran = row.lines()[-1]
            shown = row.show("Result", "ActiveState")
            report.append(f"exit 4: {ran} {shown}")
            assert (ran["result"], ran["exit"]) == ("ran", 4)
            assert shown["ActiveState"] == "failed"
            row.code.write_text("0")

            # A changed ExecStart runs through the same, unchanged drop-in.
            second = row.script("second.sh", "second")
            row.write(["/bin/sh", str(second)])
            row.out.unlink()
            assert row.start().ok
            assert row.output() == ["second "]
            assert dropin.read_text() == text
            assert row.lines()[-1]["result"] == "ran" and len(row.lines()) == 4
            report.append(f"changed ExecStart: {row.output()} {row.lines()[-1]}")
        else:
            report.append("gate rows skipped: this tree has no gate (moxi run)")

        acted = row.backend.unwrap(row.timer, module=MODULE, fence=row.fence)
        assert acted.ok and not dropin.exists() and not dropin.parent.exists()
        assert str(dropin) not in row.show("DropInPaths")["DropInPaths"]
        count = len(row.lines())
        assert row.start().ok
        assert len(row.lines()) == count
        report.append(f"unwrap: ok={acted.ok}, a start after it wrote no run line")
    finally:
        row.clean()
        after = _listing(row.dir)
        with capsys.disabled():
            print("\n" + "\n".join(f"  {line}" for line in report))
            print(f"  unit directory unchanged: {after == before}")
    assert after == before
    if not _gate():
        pytest.skip("the gate's wrap forms (moxi run --condition, --result) are not in "
                    "this tree; the drop-in, status and unwrap rows passed")


# -- sockets ----------------------------------------------------------------------------------

SOCKET_SETTINGS = """
[port]
type = "int"
default = {port}
doc = "The throwaway socket's port."
read = "start"
"""

#: An address of the documentation range, which no interface of this box holds.
UNBINDABLE = "192.0.2.1"

#: The shell row's command: it records the sockets it was handed, answers one connection
#: on each, and exits 0. Handed none, it only sleeps.
SHELL = """#!{python}
import json, os, selectors, socket, sys, time
ours = os.environ.get("LISTEN_PID") == str(os.getpid())
count = int(os.environ.get("LISTEN_FDS", "0")) if ours else 0
with open({record!r}, "a") as out:
    out.write(json.dumps({{"pid": os.getpid(), "fds": count,
                          "names": os.environ.get("LISTEN_FDNAMES", "")}}) + "\\n")
if not count:
    time.sleep(300)
    sys.exit(0)
chosen = selectors.DefaultSelector()
for fd in range(3, 3 + count):
    chosen.register(socket.socket(fileno=fd), selectors.EVENT_READ)
left, deadline = count, time.time() + 120
while left and time.time() < deadline:
    for key, _ in chosen.select(timeout=1):
        conn, _ = key.fileobj.accept()
        conn.sendall(b"ok\\n")
        conn.close()
        chosen.unregister(key.fileobj)
        left -= 1
"""


def _free_ports(count: int) -> list[int]:
    """`count` distinct ports of 127.0.0.1 that were free a moment ago."""
    held = [socket.socket() for _ in range(count)]
    try:
        for s in held:
            s.bind(("127.0.0.1", 0))
        return [s.getsockname()[1] for s in held]
    finally:
        for s in held:
            s.close()


def _scrub(directory: Path, stems: list[str], fence: Fence) -> None:
    """Stop and delete every unit file under `stems` and every `.wants` link to one, then
    forget their failures and reload. Cleanup only: names nothing else."""
    def ours(name: str) -> bool:
        return any(name.startswith((stem + ".", stem + "-")) for stem in stems)

    found = [p for p in directory.iterdir() if ours(p.name)]
    for path in sorted(found, key=lambda p: not p.name.endswith(".socket")):
        fence.run("stop", path.name)
    for wants in directory.glob("*.wants"):
        for link in wants.iterdir():
            if ours(link.name) and link.is_symlink():
                link.unlink()
    for path in found:
        if path.is_file() or path.is_symlink():
            path.unlink()
    if found:
        # Cleanup only: forget a failed run of the throwaway units.
        subprocess.run(["systemctl", "--user", "reset-failed", *[p.name for p in found]],
                       capture_output=True, timeout=30)
    fence.run("daemon-reload")


class SocketModule:
    """A throwaway fixture module `t<8 hex>` whose daemons have sockets, and its cleanup."""

    def __init__(self, site, tmp: Path, monkeypatch):
        self.site = site
        self.module = f"t{os.urandom(4).hex()}"
        self.dir = _systemd.unit_dir()
        self.fence = Fence("systemd")
        self.lines: list[str] = []
        self.scripts = tmp / "bin"
        self.scripts.mkdir()
        monkeypatch.setattr(moxi.paths, "_scripts", lambda: self.scripts)
        self.port, self.other = _free_ports(2)
        site.write(f"moxi_{self.module}/settings.toml",
                   SOCKET_SETTINGS.format(port=self.port))

    def stem(self, name: str) -> str:
        return f"moxi-{self.module}-{name}"

    def script(self, name: str, body: str) -> None:
        path = self.scripts / f"moxi-{self.module}-{name}"
        path.write_text(body)
        path.chmod(0o755)

    def declare(self, daemons: dict) -> None:
        # A second declaration is read anew, not from the first one's import.
        drop_modules({f"moxi_{self.module}"})
        self.site.module(self.module, SETTINGS="'settings.toml'", DAEMONS=repr(daemons))
        moxi.modules.reset()

    def plan(self):
        from moxi import units

        return units.plan(self.module)

    def apply(self):
        from moxi import units

        self.lines.clear()
        return units.apply(self.plan(), say=self.lines.append)

    def status(self, name: str):
        from moxi import units

        rows = {r.kind: r for r in units.status([self.module]) if r.name == name}
        return rows["daemon"], rows["socket"]

    def show(self, unit: str, *props: str) -> dict[str, str]:
        return _show(self.fence, unit, *props)

    def wait(self, unit: str, state: str, seconds: float = 15) -> str:
        """Wait for `unit`'s `ActiveState` to read `state`; the last one read."""
        deadline = time.monotonic() + seconds
        while True:
            now = self.show(unit, "ActiveState")["ActiveState"]
            if now == state or time.monotonic() > deadline:
                return now
            time.sleep(0.1)

    def remove(self):
        from moxi import units

        return units.remove(self.module, say=self.lines.append)


def _ask(address: str, port: int) -> bytes:
    family = socket.AF_INET6 if ":" in address else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as conn:
        conn.settimeout(10)
        conn.connect((address, port))
        return conn.recv(16)


def _records(path: Path) -> list[dict]:
    return ([json.loads(line) for line in path.read_text().splitlines()]
            if path.exists() else [])


def _doctor_rows(module: str) -> list[tuple[str, str, str]]:
    """The doctor's rows of `module`, for the report (the rows are the doctor's own)."""
    from moxi.doctor import _run

    return [(r.name, r.state, r.detail) for r in _run.run([module]) if r.module == module]


def test_sockets_on_this_boxs_user_manager(site, tmp_path, monkeypatch, capsys):
    """One socket unit per address on the real user manager: the units, the service
    they start, the state and its epoch, a bad bind failing one unit alone (and, in a
    hand-made unit with two listen lines, failing the whole unit), an address taken out
    again, and a home move's stop and resume around a changed daemon."""
    from moxi import units
    from moxi.units import _backend, _plan

    row = SocketModule(site, tmp_path, monkeypatch)
    record = tmp_path / "handed.jsonl"
    row.script("shell", SHELL.format(python=sys.executable, record=str(record)))
    stem = row.stem("shell")
    service = f"{stem}.service"
    bind = f"moxitest-bind-{os.urandom(4).hex()}"
    before = _listing(row.dir)
    assert not any(k.startswith((f"moxi-{row.module}-", "moxitest-")) for k in before)
    report: list[str] = []

    def declare(*addresses, doc="the throwaway shell"):
        row.declare({"shell": {"command": f"moxi-{row.module}-shell", "doc": doc,
                               "socket": {"setting": "port",
                                          "addresses": list(addresses)}}})

    try:
        with _backend.use(SystemdBackend()):
            v6 = _plan.ipv6_loopback()
            declare("127.0.0.1", "::1")
            plan = {c.kind: c for c in row.plan()}
            held = [f"{stem}.socket"] + ([f"{stem}-1.socket"] if v6 else [])
            addresses = ["127.0.0.1"] + (["::1"] if v6 else [])
            assert [p.name for p in plan["socket"].paths] == held
            if v6:
                report.append("IPv6 loopback: yes; ::1 is written")
            else:
                note = "::1 left out: no IPv6 loopback on this system"
                assert note in plan["socket"].reason and note in plan["daemon"].reason
                report.append(f"IPv6 loopback: no; the plan says {note!r} and writes "
                              f"{held} alone")
            assert f"\nSockets={' '.join(held)}\n" in plan["daemon"].text
            assert "[Install]" not in plan["daemon"].text

            done = row.apply()
            assert all(a.ok for a in done), row.lines
            shown = {u: row.show(u, "ActiveState", "UnitFileState", "Listen") for u in held}
            first = row.show(service, "ActiveState", "UnitFileState")
            report.append(f"after apply: {shown}; service {first}")
            assert all(s["ActiveState"] == "active" for s in shown.values())
            assert all(s["UnitFileState"] == "enabled" for s in shown.values())
            # The service has no install section, so it reads static; install starts it
            # after its sockets, before any connection.
            assert first["UnitFileState"] == "static"

            daemon, sock = row.status("shell")
            ids = {u: row.show(u, "InvocationID")["InvocationID"] for u in held}
            epoch = ",".join(f"{u}={ids[u]}" for u in held)
            report.append(f"status: daemon {daemon.state}, socket {sock.state}, "
                          f"enabled {sock.enabled}, epoch {sock.epoch}")
            assert (sock.state, sock.epoch, sock.enabled) == ("active", epoch, True)
            assert daemon.enabled is True

            # A connection to each address is answered; the script then exits, and the
            # service reads inactive under its listening sockets.
            assert [_ask(a, row.port) for a in addresses] == [b"ok\n"] * len(addresses)
            assert row.wait(service, "inactive") == "inactive"
            handed = _records(record)
            report.append(f"handed to the service: {handed}")
            assert handed[-1]["names"] == ":".join(held)
            assert handed[-1]["fds"] == len(held)
            daemon, sock = row.status("shell")
            report.append(f"idle: daemon {daemon.state}, socket {sock.state}, epoch "
                          f"{sock.epoch}; doctor {_doctor_rows(row.module)}")
            assert (daemon.state, sock.state, sock.epoch) == ("inactive", "active", epoch)

            # The next connection starts it again, by socket activation alone.
            assert [_ask(a, row.port) for a in addresses] == [b"ok\n"] * len(addresses)
            assert row.wait(service, "inactive") == "inactive"
            assert len(_records(record)) == len(handed) + 1
            assert _records(record)[-1]["names"] == ":".join(held)
            assert row.status("shell")[1].epoch == epoch

            # A socket unit started again gets a new id in the epoch.
            assert row.fence.run("restart", held[0]).ok
            again = row.status("shell")[1].epoch
            report.append(f"after restart of {held[0]}: epoch {again}")
            assert again != epoch and again.split(",")[1:] == epoch.split(",")[1:]
            epoch = again

            # The premise of one unit per address: a hand-made unit with two listen
            # lines, one of them an address this box doesn't hold, fails as a whole,
            # and its good address doesn't listen either.
            (row.dir / f"{bind}.service").write_text(
                "[Unit]\nDescription=moxi test: a throwaway service for the bind proof\n\n"
                "[Service]\nExecStart=/bin/true\n")
            (row.dir / f"{bind}.socket").write_text(
                "[Unit]\nDescription=moxi test: two listen lines, one unbindable\n\n"
                f"[Socket]\nListenStream=127.0.0.1:{row.other}\n"
                f"ListenStream={UNBINDABLE}:{row.other}\n")
            assert row.fence.run("daemon-reload").ok
            started = row.fence.run("start", f"{bind}.socket")
            whole = row.show(f"{bind}.socket", "ActiveState", "Result")
            try:
                _ask("127.0.0.1", row.other)
                good = "listening"
            except OSError as exc:
                good = f"not listening ({type(exc).__name__})"
            report.append(f"two listen lines, one on {UNBINDABLE}: start ok={started.ok} "
                          f"{started.stderr.strip()!r}; unit {whole}; 127.0.0.1:{row.other} "
                          f"{good}")
            assert not started.ok and whole["ActiveState"] == "failed"
            assert good.startswith("not listening")

            # One unit per address: the unbindable address fails its own unit alone.
            declare("127.0.0.1", "::1", UNBINDABLE)
            third = f"{stem}-2.socket"
            plan = {c.kind: c for c in row.plan()}
            assert [p.name for p in plan["socket"].paths] == held + [third]
            done = {a.change.kind: a for a in row.apply()}
            states = {u: row.show(u, "ActiveState")["ActiveState"] for u in held + [third]}
            daemon, sock = row.status("shell")
            report.append(f"with {UNBINDABLE}: socket apply ok={done['socket'].ok} "
                          f"{done['socket'].lines}; units {states}; socket {sock.state} "
                          f"{sock.detail!r} epoch {sock.epoch}; service "
                          f"{row.show(service, 'ActiveState')}; doctor "
                          f"{_doctor_rows(row.module)}")
            assert not done["socket"].ok
            assert states == {**{u: "active" for u in held}, third: "failed"}
            assert (sock.state, sock.epoch) == ("failed", None)
            assert third in sock.detail

            # Taken out again: the unit is disabled and deleted, the rest listen.
            declare("127.0.0.1", "::1")
            plan = {c.kind: c for c in row.plan()}
            assert plan["socket"].dropped == (row.dir / third,)
            done = row.apply()
            assert all(a.ok for a in done), row.lines
            assert not (row.dir / third).exists()
            daemon, sock = row.status("shell")
            report.append(f"{UNBINDABLE} taken out: {third} deleted, socket {sock.state}")
            assert sock.state == "active"

            # A home move: every unit stopped with a record, the daemon changed, the
            # unit step applied, then resume. The sockets start before the service, so
            # resume finds them running and is done.
            stops: list = []
            stopped = units.stop(row.module, ["shell"], record=stops)
            assert all(a.ok for a in stopped)
            declare("127.0.0.1", "::1", doc="the throwaway shell, rewritten")
            done = row.apply()
            resumed = units.resume(stops)
            report.append(f"home move: stopped {[e.unit for e in stops]}; apply "
                          f"ok={all(a.ok for a in done)} {row.lines}; resume "
                          f"{[(a.ok, a.lines) for a in resumed]}")
            assert all(a.ok for a in done), row.lines
            assert [e.unit for e in stops][:len(held)] == held
            assert all(a.ok for a in resumed), resumed
            assert row.status("shell")[1].state == "active"

            removed = row.remove()
            report.append(f"remove: {[(a.change.kind, a.ok, a.lines) for a in removed]}")
            assert all(a.ok for a in removed)
    finally:
        _scrub(row.dir, [row.stem("")[:-1], bind], row.fence)
        after = _listing(row.dir)
        with capsys.disabled():
            print("\n" + "\n".join(f"  {line}" for line in report))
            print(f"  unit directory unchanged: {after == before}")
    assert after == before


def test_a_service_that_gains_a_socket_loses_its_login_link_on_this_boxs_user_manager(
        site, tmp_path, monkeypatch, capsys):
    """A daemon first written without a socket is enabled, linked from
    `default.target.wants`; given a socket, its service loses its install section and
    the writer takes the link away, so a login no longer starts it."""
    from moxi.units import _backend

    row = SocketModule(site, tmp_path, monkeypatch)
    row.script("relay", f"#!{sys.executable}\nimport time\ntime.sleep(300)\n")
    service = f"{row.stem('relay')}.service"
    link = row.dir / "default.target.wants" / service
    before = _listing(row.dir)
    report: list[str] = []
    try:
        with _backend.use(SystemdBackend()):
            entry = {"command": f"moxi-{row.module}-relay", "doc": "the throwaway relay"}
            row.declare({"relay": entry})
            assert all(a.ok for a in row.apply()), row.lines
            report.append(f"no socket: link {link.is_symlink()}, "
                          f"{row.show(service, 'UnitFileState', 'ActiveState')}")
            assert link.is_symlink()
            row.declare({"relay": {**entry, "socket": {"setting": "port",
                                                        "addresses": ["127.0.0.1"]}}})
            done = row.apply()
            shown = row.show(service, "UnitFileState", "ActiveState")
            report.append(f"with a socket: link {link.is_symlink()}, {shown}, "
                          f"lines {row.lines}")
            assert all(a.ok for a in done), row.lines
            assert not link.is_symlink() and not link.exists()
            assert shown == {"UnitFileState": "static", "ActiveState": "active"}
            assert all(a.ok for a in row.remove())
    finally:
        _scrub(row.dir, [row.stem("")[:-1]], row.fence)
        after = _listing(row.dir)
        with capsys.disabled():
            print("\n" + "\n".join(f"  {line}" for line in report))
            print(f"  unit directory unchanged: {after == before}")
    assert after == before


@pytest.mark.parametrize("service_too", [False, True],
                         ids=["socket only", "socket and service"])
def test_a_changed_socket_restarts_a_keep_daemon_on_this_boxs_user_manager(
        site, tmp_path, monkeypatch, capsys, service_too):
    """A changed socket restarts its daemon whatever `upgrade` says: a running
    `upgrade = "keep"` daemon whose port changed ends running, as a new process, behind a
    socket on the new port, with or without a change to its own `.service`."""
    from moxi import config
    from moxi.units import _backend

    row = SocketModule(site, tmp_path, monkeypatch)
    row.script("relay", "#!/bin/sh\nexec sleep 300\n")
    stem = row.stem("relay")
    service, sock = f"{stem}.service", f"{stem}.socket"
    before = _listing(row.dir)
    report: list[str] = []

    def declare(doc):
        row.declare({"relay": {"command": f"moxi-{row.module}-relay", "doc": doc,
                               "upgrade": "keep",
                               "socket": {"setting": "port", "addresses": ["127.0.0.1"]}}})

    try:
        with _backend.use(SystemdBackend()):
            declare("a throwaway daemon for the socket proof")
            assert all(a.ok for a in row.apply()), row.lines
            first = row.show(service, "ActiveState", "SubState", "MainPID")
            report.append(f"first apply: {first} {row.show(sock, 'ActiveState', 'Listen')}")
            assert first["ActiveState"] == "active" and first["MainPID"] not in ("", "0")
            assert row.show(sock, "Listen")["Listen"] == f"127.0.0.1:{row.port} (Stream)"

            config.set(row.module, "port", str(row.other), by="test")
            if service_too:
                declare("a throwaway daemon for the socket proof, rewritten")
            assert {(c.kind, c.action) for c in row.plan()} == {
                ("daemon", "change" if service_too else "keep"), ("socket", "change")}
            done = row.apply()
            then = row.show(service, "ActiveState", "SubState", "MainPID")
            listens = row.show(sock, "ActiveState", "Listen")
            report.append(f"port {row.port} -> {row.other}: {then} {listens} "
                          f"lines={row.lines}")
            assert all(a.ok for a in done), row.lines
            assert then["ActiveState"] == "active", (then, row.lines)
            assert then["MainPID"] not in ("", "0", first["MainPID"])
            assert listens == {"ActiveState": "active",
                               "Listen": f"127.0.0.1:{row.other} (Stream)"}
            if service_too:
                assert (f"{service} rewritten and restarted, since its socket changed"
                        in row.lines)
            with socket.create_connection(("127.0.0.1", row.other), timeout=5):
                pass
            assert all(a.ok for a in row.remove())
    finally:
        _scrub(row.dir, [row.stem("")[:-1]], row.fence)
        after = _listing(row.dir)
        with capsys.disabled():
            print("\n" + "\n".join(f"  {line}" for line in report))
            print(f"  unit directory unchanged: {after == before}")
    assert after == before

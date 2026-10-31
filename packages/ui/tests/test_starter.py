"""The starter: real page servers spawned over test-local page modules installed in a
site directory of the session's own, and the export command run over the same site.

Each test drives one `Starters` inside `asyncio.run` and stops every child it started
before it returns.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest
from moxi import files, proc
from moxi.paths import Layout, python
from moxi_loopback.api import RuntimeInfo, read_runtime, write_runtime

from moxi_ui import starter as starter_mod
from moxi_ui.router import shell_table
from moxi_ui.starter import (
    FAILED_FOR,
    POOL_MAX,
    Conn,
    Server,
    ServerGone,
    StartFailed,
    Starters,
    export_argv,
    export_tables,
    read_servers,
)

PAGE = """
from moxi_loopback.routes import Field, Out, Page, Route

H = "moxi_{name}.handlers"
PAGE = Page(
    routes=(
        Route("/api/echo", "read", H + ":echo", query={{"n": Field("int", required=False)}},
              returns={{"n": Out("int")}}),
        Route("/api/events", "read", H + ":events", stream=True,
              topics={{"tick": Out("table", items={{"n": Out("int")}})}}),
    ),
    health=H + ":health",
)
"""

HANDLERS = """
from moxi_loopback.server import Topics

TOPICS = Topics("tick")


def echo(call):
    return {"n": call.query.get("n", 0)}


def events(call):
    TOPICS.publish("tick", {"n": 1})
    return call.stream(TOPICS)


def health():
    return (True, "fine")
"""

ANNOUNCEMENT = {
    "INTERFACES": "frozenset({1})", "API": "None", "CLI": "None",
    "SUMMARY": "'A module made for a test.'", "OPTIONAL": "{}", "GATED": "{}",
    "INTERNAL_ENV": "{}", "SETTINGS": "None", "SETUP": "None", "SEGMENT": "None",
}


def _module(root: Path, name: str, *, page: bool = True, handlers: bool = True,
            extra: str = "", version: str = "0.1.0") -> None:
    package = root / f"moxi_{name}"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    fields = dict(ANNOUNCEMENT)
    if page:
        fields["PAGE"] = repr(f"moxi_{name}.page:PAGE")
        (package / "page.py").write_text(textwrap.dedent(extra) + PAGE.format(name=name))
    if handlers:
        (package / "handlers.py").write_text(HANDLERS)
    (package / "module.py").write_text("".join(f"{k} = {v}\n" for k, v in fields.items()))
    _dist(root, name, version)


def _dist(root: Path, name: str, version: str) -> None:
    for old in root.glob(f"moxi_{name}-*.dist-info"):
        for item in old.iterdir():
            item.unlink()
        old.rmdir()
    info = root / f"moxi_{name}-{version}.dist-info"
    info.mkdir()
    (info / "METADATA").write_text(f"Metadata-Version: 2.1\nName: moxi-{name}\n"
                                   f"Version: {version}\n")
    (info / "entry_points.txt").write_text(f"[moxi.modules]\n{name} = moxi_{name}.module\n")


@pytest.fixture(scope="session")
def site(tmp_path_factory):
    root = tmp_path_factory.mktemp("starter-site")
    for name in ("stpage", "stheld", "stsquat"):
        _module(root, name)
    _module(root, "stbad", handlers=False)  # its handlers don't import: exit 3
    _module(root, "stslow", extra="import time\ntime.sleep(30)\n")
    _module(root, "stnopage", page=False, handlers=False)
    return root


@pytest.fixture
def shell_dirs(site, monkeypatch):
    """The page modules on the import path, and the shell's runtime and state dirs."""
    monkeypatch.setenv("PYTHONPATH", str(site))
    layout = Layout("ui")
    return layout.runtime_dir(create=True), layout.state_dir()


class Clock:
    def __init__(self):
        self.offset = 0.0

    def __call__(self):
        return time.monotonic() + self.offset


def make(shell_dirs, **kwargs) -> Starters:
    runtime_dir, state_dir = shell_dirs
    return Starters(runtime_dir=runtime_dir, state_dir=state_dir,
                    log=state_dir / "logs" / "shell.log", **kwargs)


def drive(starters: Starters, body) -> None:
    """Run `body(starters)` on a fresh loop, then stop every child."""

    async def main():
        try:
            await body(starters)
        finally:
            await starters.aclose()

    asyncio.run(asyncio.wait_for(main(), 40))


async def until(predicate, timeout: float = 5.0, what: str = "the condition"):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() >= deadline:
            raise AssertionError(f"timed out after {timeout:g} s waiting for {what}")
        await asyncio.sleep(0.01)


async def get(conn: Conn, info, path: str) -> tuple[int, bytes]:
    host = "localhost" if info.transport == "unix" else f"127.0.0.1:{info.port}"
    conn.writer.write(f"GET {path} HTTP/1.1\r\nHost: {host}\r\n"
                      f"X-Moxi-Token: {info.token}\r\n\r\n".encode())
    head = await conn.reader.readuntil(b"\r\n\r\n")
    status = int(head.split(b" ", 2)[1])
    length = next(int(line.split(b":")[1]) for line in head.split(b"\r\n")
                  if line.lower().startswith(b"content-length:"))
    return status, await conn.reader.readexactly(length)


# --- no spawn ---------------------------------------------------------------------------


def test_every_child_runs_as_python_P_m_from_the_shells_runtime_dir(shell_dirs, monkeypatch):
    monkeypatch.setenv("MOXI_LOG_FILE", "/elsewhere/shell.log")
    seen = []

    def spawn(argv, **kwargs):
        seen.append((argv, kwargs))
        raise OSError("recorded")

    starters = make(shell_dirs, spawn=spawn, child_args=("--idle-s", "0.5"))

    async def body(starters):
        with pytest.raises(StartFailed) as failed:
            await starters.ensure("stpage")
        assert failed.value.exit is None

    drive(starters, body)
    (argv, kwargs), = seen
    assert argv == [str(python()), "-P", "-m", "moxi_loopback.serve", "stpage",
                    "--idle-s", "0.5"]
    assert kwargs["lifeline"] == "stdin"
    assert kwargs["cwd"] == str(shell_dirs[0])
    assert kwargs["stdout"] == kwargs["stderr"] == subprocess.PIPE
    assert "MOXI_LOG_FILE" not in kwargs["env"]
    assert kwargs["env"]["PYTHONPATH"] == os.environ["PYTHONPATH"]
    assert export_argv(["stpage"])[:4] == [str(python()), "-P", "-m", "moxi_loopback.export"]
    with pytest.raises(ValueError):
        make(shell_dirs).argv("not a module")


class _Writer:
    def __init__(self):
        self.closed = False

    def is_closing(self):
        return self.closed

    def close(self):
        self.closed = True


class _Reader:
    def at_eof(self):
        return False


def _info():
    return RuntimeInfo(
        module="stpage", server_id="0" * 32, pid=1, transport="unix", address="/x.sock",
        port=None, token="t" * 43, version="0.1.0", started_at="2026-10-30T00:00:00Z")


def test_the_pool_keeps_four_idle_connections_and_none_once_the_server_is_gone():
    async def body():
        server = Server("stpage", _info(), None, started=0.0)
        conns = [Conn(_Reader(), _Writer(), time.monotonic()) for _ in range(POOL_MAX + 1)]
        for conn in conns:
            server.give(conn)
        assert conns[-1].writer.closed and not any(c.writer.closed for c in conns[:-1])
        assert server.take() is conns[POOL_MAX - 1]
        conns[0].writer.closed = True
        server.take(), server.take()
        assert server.take() is None  # the closed one is skipped
        server.give(conns[1])
        server._drop()
        assert conns[1].writer.closed and server.take() is None
        with pytest.raises(ServerGone):
            server.use()
        with pytest.raises(ServerGone):
            await server.connect()
        old = Conn(_Reader(), _Writer(), time.monotonic() - 0.3)
        assert not old.fresh() and Conn(_Reader(), _Writer(), time.monotonic()).fresh()

    asyncio.run(body())


def test_the_export_command_mounts_each_page_and_names_each_refusal(shell_dirs):
    found = export_tables(["stpage", "stnopage"], cwd=shell_dirs[0])
    assert list(found.mounts) == ["stpage"]
    assert "declares no PAGE" in found.errors["stnopage"]
    table = shell_table(["stpage"], found.mounts)
    assert table.find("GET", "/m/stpage/api/echo") is not None
    assert table.find("GET", "/m/stpage/api/health") is None


def test_an_export_command_that_fails_gives_every_module_its_reason(shell_dirs):
    def garbled(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 1, b"Traceback", b"")

    def slow(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, 10)

    def refused(argv, **kwargs):
        body = {"a": {"ok": True, "export": {"format": 99}}, "b": {"ok": False, "error": "x"}}
        return subprocess.CompletedProcess(argv, 3, json.dumps(body).encode(), b"")

    assert export_tables(["a", "b"], cwd=".", run=garbled).errors == {
        "a": "the export command exited 1 with no answer",
        "b": "the export command exited 1 with no answer"}
    assert set(export_tables(["a"], cwd=".", run=slow).errors) == {"a"}
    found = export_tables(["a", "b"], cwd=".", run=refused)
    assert found.mounts == {} and found.errors["b"] == "x"
    assert found.errors["a"].startswith("its export is refused")


# --- real page servers ------------------------------------------------------------------


def test_two_first_requests_on_a_fresh_install_make_one_child(shell_dirs):
    assert not Layout("stpage").runtime_dir().exists()
    starters = make(shell_dirs)

    async def body(starters):
        one, two = await asyncio.gather(starters.ensure("stpage"), starters.ensure("stpage"))
        assert one is two
        starter = starters.get("stpage")
        assert starter.state == "running" and starter.record.starts == 1
        assert starters.children() == 1
        assert one.info == read_runtime("stpage") and one.child.server_id == one.info.server_id
        conn = await one.connect()
        assert conn.fresh()
        assert await get(conn, one.info, "/api/echo?n=7") == (200, b'{"n":7}')
        one.give(conn)
        assert one.take() is conn
        conn.close()

    drive(starters, body)
    assert starters.children() == 0


def test_a_refused_page_fails_for_ten_seconds_then_starts_again(shell_dirs):
    clock = Clock()
    starters = make(shell_dirs, clock=clock)

    async def body(starters):
        starter = starters.get("stbad")
        with pytest.raises(StartFailed) as first:
            await starters.ensure("stbad")
        assert first.value.exit == 3
        assert first.value.body() == {"error": "start", "module": "stbad", "exit": 3,
                                      "log": starters.log}
        assert starter.state == "failed"
        clock.offset = FAILED_FOR - 0.1
        with pytest.raises(StartFailed) as again:
            await starters.ensure("stbad")
        assert again.value is first.value and starter.record.starts == 1
        for n in (2, 3):
            clock.offset += FAILED_FOR + 0.1
            assert starter.state == "idle"
            with pytest.raises(StartFailed):
                await starters.ensure("stbad")
            assert starter.record.starts == n
        assert starter.tail(), "the child's stderr is kept"

    drive(starters, body)
    saved = read_servers(starters.path)["stbad"]
    assert saved["failed_starts"] == saved["failed_in_row"] == 3 and saved["last_exit"] == 3
    assert "exited 3" in saved["reason"]


def test_no_ready_line_in_time_kills_the_child(shell_dirs, monkeypatch):
    monkeypatch.setattr(starter_mod, "READY_WITHIN", 1.0)
    starters = make(shell_dirs)

    async def body(starters):
        with pytest.raises(StartFailed) as failed:
            await starters.ensure("stslow")
        assert failed.value.exit is None and "no ready line" in failed.value.reason
        assert starters.children() == 0

    drive(starters, body)


def test_the_childs_idle_exit_drops_the_server_and_the_next_request_starts_one(shell_dirs):
    starters = make(shell_dirs, child_args=("--idle-s", "0.5"))

    async def body(starters):
        starter = starters.get("stpage")
        first = await starters.ensure("stpage")
        await until(lambda: starter.state == "idle", what="the idle exit")
        assert first.gone and starter.record.last_exit == 0 and starter.record.crashes == 0
        with pytest.raises(ServerGone):
            await first.connect()
        second = await starters.ensure("stpage")
        assert second.info.server_id != first.info.server_id
        assert starter.record.starts == 2

    drive(starters, body)


def test_a_crash_is_counted_and_written_and_read_back(shell_dirs):
    starters = make(shell_dirs)

    async def body(starters):
        starter = starters.get("stpage")
        server = await starters.ensure("stpage")
        server.child.popen.kill()  # from outside the starter: a crash
        await until(lambda: starter.state == "idle", what="the crash to be seen")
        assert server.gone and starter.record.crashes == 1
        assert starter.record.last_exit != 0 and starter.record.last_crash_at

    drive(starters, body)
    assert read_servers(starters.path)["stpage"]["crashes"] == 1
    again = make(shell_dirs)
    row, = again.rows()
    assert row["module"] == "stpage" and row["state"] == "idle" and row["crashes"] == 1


def test_on_tcp_a_dead_childs_port_is_never_dialled(shell_dirs):
    starters = make(shell_dirs, child_args=("--tcp",))

    async def body(starters):
        starter = starters.get("stpage")
        first = await starters.ensure("stpage")
        assert first.info.transport == "tcp"
        first.child.popen.kill()
        await until(lambda: starter.state == "idle", what="the exit")
        squatter = socket.create_server(("127.0.0.1", first.info.port))
        squatter.settimeout(0.2)
        try:
            with pytest.raises(ServerGone):
                await first.connect()
            second = await starters.ensure("stpage")
            assert second.info.port != first.info.port
            with pytest.raises(TimeoutError):
                squatter.accept()
        finally:
            squatter.close()

    drive(starters, body)


def test_a_version_change_drains_and_restarts(shell_dirs, tmp_path, monkeypatch):
    own = tmp_path / "own-site"
    _module(own, "stver")
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join([os.environ["PYTHONPATH"], str(own)]))
    starters = make(shell_dirs)
    cut = []

    async def body(starters):
        starter = starters.get("stver")
        old = await starters.ensure("stver")
        assert old.info.version == "0.1.0"
        request = old.use()
        stream = old.use(stream=True, close=lambda: cut.append(True))
        assert starters.set_versions({"stver": "0.1.0"}) == []
        drains = starters.set_versions({"stver": "0.2.0", "other": "9"})
        assert len(drains) == 1 and starter.state == "draining"
        assert cut == [True], "the open stream is closed at once"
        with pytest.raises(ServerGone):
            old.use()
        waiting = asyncio.ensure_future(starters.ensure("stver"))
        stream.end()
        await asyncio.sleep(0.2)
        assert not waiting.done() and old.child.alive, "an open request holds the drain"
        _dist(own, "stver", "0.2.0")
        request.end()
        new = await waiting
        assert new.info.version == "0.2.0" and old.gone
        assert old.child.code == 0 and starter.record.crashes == 0

    drive(starters, body)


def test_stopping_the_shell_stops_every_child(shell_dirs):
    starters = make(shell_dirs)
    seen = {}

    async def body(starters):
        server = await starters.ensure("stpage")
        seen["child"] = server.child
        await starters.aclose()
        assert starters.children() == 0 and server.gone
        with pytest.raises(ServerGone):
            await starters.ensure("stpage")

    drive(starters, body)
    assert seen["child"].code == 0
    assert starters.get("stpage").record.crashes == 0


def test_the_servers_data_samples_each_running_server(shell_dirs):
    starters = make(shell_dirs)

    async def body(starters):
        server = await starters.ensure("stpage")
        data = await starters.servers()
        row, = data["servers"]
        assert row["module"] == "stpage" and row["state"] == "running" and row["child"]
        assert row["server_id"] == server.info.server_id and row["version"] == "0.1.0"
        assert row["pid"] == server.child.pid == row["sample"]["pid"]
        assert row["sample"]["rss"] > 0 and row["sample"]["fds"] > 0
        assert row["open"] == {"requests": 0, "streams": 0}
        assert row["starts"] == 1 and row["log"] == starters.log

    drive(starters, body)


# --- exit 75: another server holds the lock ---------------------------------------------


class Squatter:
    """Holds a module's lock with a runtime file naming its own listener, which answers
    nothing: it reads one request head, then lets go of the lock and hangs up."""

    def __init__(self, module: str, transport: str):
        runtime = Layout(module).runtime_dir(create=True)
        if transport == "unix":
            address, port = str(runtime / "squat.sock"), None
            self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self.sock.bind(address)
        else:
            self.sock = socket.socket()
            self.sock.bind(("127.0.0.1", 0))
            address, port = "127.0.0.1", self.sock.getsockname()[1]
        self.sock.listen()
        self.sock.settimeout(15)
        self.info = write_runtime(module, transport=transport, address=address, port=port,
                                  version="0.1.0")
        self.got = b""
        self._held = threading.Event()
        self._lock = runtime / "page.lock"
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        assert self._held.wait(5)

    def _run(self):
        try:
            with files.lock(self._lock, timeout=1):
                self._held.set()
                conn, _ = self.sock.accept()
                conn.settimeout(5)
                while b"\r\n\r\n" not in self.got:
                    chunk = conn.recv(4096)
                    if not chunk:
                        break
                    self.got += chunk
            conn.close()
        except OSError:
            pass
        finally:
            self.sock.close()


def _hand_run(module: str, cwd: Path) -> tuple[subprocess.Popen, str]:
    child = proc.spawn([str(python()), "-P", "-m", "moxi_loopback.serve", module],
                       lifeline="stdin", stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                       cwd=str(cwd))
    line = []
    reader = threading.Thread(target=lambda: line.append(child.stdout.readline()), daemon=True)
    reader.start()
    reader.join(10)
    assert line and line[0].startswith(b"ready "), "the hand-run server is not ready"
    return child, line[0].split()[1].decode()


def _squatter_saw_only_the_proof(squatter: Squatter) -> None:
    got = squatter.got.lower()
    assert got.startswith(b"get /api/prove ") and got.count(b"\r\n\r\n") == 1
    assert b"x-moxi-token" not in got and squatter.info.token.encode().lower() not in got


def test_exit_75_on_a_unix_socket(shell_dirs):
    """A holder that passes the proof is used; one that fails it gets one more start."""
    if sys.platform == "win32":
        pytest.skip("Unix sockets")
    holder, holder_id = _hand_run("stheld", shell_dirs[0])
    squatter = Squatter("stsquat", "unix")
    starters = make(shell_dirs)

    async def body(starters):
        held, fresh = await asyncio.gather(starters.ensure("stheld"),
                                           starters.ensure("stsquat"))
        assert held.info.server_id == holder_id and held.child is None
        conn = await held.connect()  # the proof's connection, handed over
        assert await get(conn, held.info, "/api/echo?n=1") == (200, b'{"n":1}')
        conn.close()
        assert starters.get("stheld").record.starts == 1
        assert fresh.info.server_id != squatter.info.server_id and fresh.child.alive
        assert starters.get("stsquat").record.starts == 2
        assert starters.get("stsquat").record.failed_starts == 0

    try:
        drive(starters, body)
    finally:
        holder.lifeline.close()
        holder.wait(5)
        squatter.thread.join(5)
    _squatter_saw_only_the_proof(squatter)


def test_exit_75_on_tcp(shell_dirs):
    """Our own server, forgotten after a failed connect, holds the lock and passes the
    proof: it is used again and still watched. A holder that fails the proof gets one more
    start."""
    squatter = Squatter("stsquat", "tcp")
    starters = make(shell_dirs, child_args=("--tcp",))

    async def body(starters):
        first = await starters.ensure("stheld")
        starters.lost(first)
        assert first.gone and starters.get("stheld").state == "idle"
        again, fresh = await asyncio.gather(starters.ensure("stheld"),
                                            starters.ensure("stsquat"))
        assert again is not first and again.info.server_id == first.info.server_id
        assert again.child is first.child and again.child.server is again
        assert starters.get("stheld").record.starts == 2
        assert fresh.info.transport == "tcp" and fresh.info.server_id != squatter.info.server_id
        assert starters.get("stsquat").record.starts == 2

    try:
        drive(starters, body)
    finally:
        squatter.thread.join(5)
    _squatter_saw_only_the_proof(squatter)



def _spawns(*kinds):
    """A `spawn` seam: each start in turn is a real page server (`real`) or a child that
    exits 75 at once (`held`), as a runner does after its lock wait; then real ones."""
    kinds = list(kinds)

    def spawn(argv, **kwargs):
        if kinds and kinds.pop(0) == "held":
            argv = [str(python()), "-c", f"raise SystemExit({starter_mod.LOCK_HELD})"]
        return proc.spawn(argv, **kwargs)

    return spawn


def test_a_holder_of_another_version_is_not_used(shell_dirs):
    """A drained server the shell didn't start can't be stopped: the next start finds it
    holding the lock and does not use it while the snapshot says another version."""
    holder, holder_id = _hand_run("stheld", shell_dirs[0])
    clock = Clock()
    starters = make(shell_dirs, clock=clock, spawn=_spawns(*["held"] * 4))

    async def body(starters):
        held = await starters.ensure("stheld")
        assert held.info.server_id == holder_id and held.child is None
        await asyncio.gather(*starters.set_versions({"stheld": "0.2.0"}))
        assert held.gone
        with pytest.raises(StartFailed) as failed:
            await starters.ensure("stheld")
        assert failed.value.exit == starter_mod.LOCK_HELD
        assert "of version 0.1.0" in failed.value.reason
        assert starters.get("stheld").record.starts == 3 and holder.poll() is None
        starters.set_versions({"stheld": "0.1.0"})
        clock.offset += FAILED_FOR
        again = await starters.ensure("stheld")
        assert again.info.server_id == holder_id

    try:
        drive(starters, body)
    finally:
        holder.lifeline.close()
        holder.wait(5)


def test_a_start_that_comes_up_on_the_version_before_a_new_snapshot_is_started_again(
        shell_dirs, tmp_path, monkeypatch):
    own = tmp_path / "own-site"
    _module(own, "stver")
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join([os.environ["PYTHONPATH"], str(own)]))
    starters = make(shell_dirs)
    snapshots, children = [], []
    runtime = starter_mod.Starter._runtime

    async def snapshot_meanwhile(self, server_id, deadline):
        info = await runtime(self, server_id, deadline)
        children.append(self._children[-1])
        if snapshots:
            version, install = snapshots.pop(0)
            if install:
                _dist(own, "stver", version)
            starters.set_versions({"stver": version})
        return info

    monkeypatch.setattr(starter_mod.Starter, "_runtime", snapshot_meanwhile)

    async def body(starters):
        starter = starters.get("stver")
        # The upgrade lands, and its snapshot is taken, while the old code starts.
        snapshots.append(("0.2.0", True))
        server = await starters.ensure("stver")
        assert server.info.version == "0.2.0" and starter.record.starts == 2
        old, new = children
        assert old.code == 0 and new.alive and starter.record.crashes == 0
        # A snapshot that names a version nothing installs drains once, not in a loop.
        starter.lost(server)
        await new.stop()
        snapshots.append(("0.3.0", False))
        server = await starters.ensure("stver")
        assert server.info.version == "0.2.0" and starter.record.starts == 4

    drive(starters, body)


@pytest.mark.skipif(sys.platform == "win32", reason="SIGSTOP")
def test_a_hung_child_of_its_own_that_holds_the_lock_is_stopped(shell_dirs, monkeypatch):
    import signal

    monkeypatch.setattr(starter_mod, "HELD_PROOF_WAIT", 0.5)
    monkeypatch.setattr(starter_mod, "STOP_WAIT", 0.5)
    starters = make(shell_dirs, spawn=_spawns("real", "held"))

    async def body(starters):
        hung = await starters.ensure("stpage")
        child = hung.child
        os.kill(child.pid, signal.SIGSTOP)
        starters.lost(hung)
        fresh = await starters.ensure("stpage")
        assert fresh.info.server_id != hung.info.server_id and fresh.child.alive
        assert not child.alive and starters.get("stpage").record.starts == 3

    drive(starters, body)


@pytest.mark.skipif(sys.platform == "win32", reason="process groups")
def test_a_kill_ends_the_child_and_what_it_started_and_never_signals_the_group(
        tmp_path, monkeypatch):
    signalled = []
    monkeypatch.setattr(os, "killpg", lambda *args: signalled.append(args))
    script = ("import pathlib, subprocess, sys, time\n"
              "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
              "pathlib.Path('below').write_text(str(child.pid))\n"
              "time.sleep(60)\n")

    async def body():
        popen = proc.spawn([str(python()), "-c", script], lifeline="stdin",
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=str(tmp_path))
        assert os.getpgid(popen.pid) == os.getpgid(0), "the child runs in the shell's group"
        child = starter_mod.Child("stpage", popen, asyncio.get_running_loop(),
                                  lambda child, code: None)
        await until(lambda: (tmp_path / "below").exists(), what="the grandchild")
        below = int((tmp_path / "below").read_text())
        child.kill()
        assert await asyncio.wait_for(child.exited, 5) == -9
        await until(lambda: _gone(below), what="the grandchild's end")

    asyncio.run(asyncio.wait_for(body(), 20))
    assert signalled == []


def _gone(pid: int) -> bool:
    import psutil

    try:
        return psutil.Process(pid).status() == psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return True

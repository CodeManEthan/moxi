"""Fixtures: a throwaway site directory that holds fake distributions for discovery tests."""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

import moxi.modules

ANNOUNCEMENT_DEFAULTS = {
    "INTERFACES": "frozenset({1})",
    "API": "None",
    "CLI": "None",
    "SUMMARY": "'A module made for a test.'",
    "OPTIONAL": "{}",
    "GATED": "{}",
    "INTERNAL_ENV": "{}",
    "SETTINGS": "None",
    "SETUP": "None",
    "SEGMENT": "None",
}


def announcement(drop: tuple[str, ...] = (), **names: str) -> str:
    """The source of an announcement module; each keyword is a name's Python expression."""
    fields = {**ANNOUNCEMENT_DEFAULTS, **names}
    return "".join(f"{k} = {v}\n" for k, v in fields.items() if k not in drop)


class Site:
    """A directory on sys.path where a test installs fake distributions."""

    def __init__(self, root: Path):
        self.root = root
        self.packages: set[str] = set()

    def write(self, relpath: str, text: str) -> Path:
        path = self.root / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text))
        top = relpath.split("/")[0].removesuffix(".py")
        if not top.endswith(".dist-info") and top not in self.packages:
            self.packages.add(top)
            # An earlier test may have imported the real package of this name; the site's
            # own must be the one a later import finds.
            drop_modules({top})
        return path

    def distribution(self, name: str, version: str = "0.1.0", *,
                     entry_points: dict[str, str] | None = None,
                     requires: tuple[str, ...] = (),
                     extras: dict[str, list[str]] | None = None,
                     direct_url: dict | None = None) -> None:
        info = f"{name.replace('-', '_')}-{version}.dist-info"
        self.write(f"{info}/METADATA",
                   metadata(name, version, requires=requires, extras=extras))
        if entry_points:
            lines = "".join(f"{k} = {v}\n" for k, v in entry_points.items())
            self.write(f"{info}/entry_points.txt", f"[moxi.modules]\n{lines}")
        if direct_url is not None:
            import json

            self.write(f"{info}/direct_url.json", json.dumps(direct_url))

    def module(self, name: str, *, dist: str | None = None, version: str = "0.1.0",
               package: str | None = None, drop: tuple[str, ...] = (),
               requires: tuple[str, ...] = (),
               extras: dict[str, list[str]] | None = None,
               direct_url: dict | None = None,
               **names: str) -> str:
        """A whole fake module: its distribution, package and announcement."""
        package = package or f"moxi_{name}"
        self.write(f"{package}/__init__.py", "")
        self.write(f"{package}/module.py", announcement(drop, **names))
        self.distribution(dist or f"moxi-{name}", version,
                          entry_points={name: f"{package}.module"},
                          requires=requires, extras=extras, direct_url=direct_url)
        return package


def metadata(name: str, version: str, *, requires: tuple[str, ...] = (),
             extras: dict[str, list[str]] | None = None) -> str:
    """A METADATA file's text, with Requires-Dist and Provides-Extra lines."""
    lines = ["Metadata-Version: 2.1", f"Name: {name}", f"Version: {version}"]
    lines += [f"Requires-Dist: {req}" for req in requires]
    for extra, reqs in (extras or {}).items():
        lines.append(f"Provides-Extra: {extra}")
        lines += [f'Requires-Dist: {req}; extra == "{extra}"' for req in reqs]
    return "\n".join(lines) + "\n"


def see_only(root: Path, monkeypatch) -> None:
    """Let `importlib.metadata` and `load` find only the distributions under `root`, and
    `moxi` itself.

    A test of discovery or install sees exactly the distributions its site installs. The
    environment may hold real modules and their dependencies (a workspace installs every
    module), so the rest is filtered out of `Distribution.discover`, which entry points,
    `distributions()` and `distribution(name)` all go through.
    """
    from importlib.metadata import Distribution

    discover = Distribution.discover

    def seen(dist) -> bool:
        return dist.name == "moxi" or Path(dist.locate_file("")) == root

    def filtered(cls, **kwargs):
        return (d for d in discover(**kwargs) if seen(d))

    monkeypatch.setattr(Distribution, "discover", classmethod(filtered))

    # `load` reads a distribution's .dist-info directly, without `importlib.metadata`, so
    # it is held to the same view: what it finds outside `root` reads as not found.
    find = moxi.modules._DistInfo.find.__func__

    def find_seen(cls, module):
        dist = find(cls, module)
        return dist if dist is not None and Path(dist.path).parent == root else None

    monkeypatch.setattr(moxi.modules._DistInfo, "find", classmethod(find_seen))


@pytest.fixture(autouse=True)
def _no_real_core_roots(tmp_path_factory, monkeypatch):
    """The core's own roots, uv's tool directory and the user site point into tmp, so
    nothing the core reads or writes for itself (the catalogue's refreshed copy, the
    strays record) is the real one. A test that needs another root sets its own."""
    import moxi.strays

    home = tmp_path_factory.mktemp("core-home")
    monkeypatch.setenv("MOXI_HOME", str(home / "moxi"))
    monkeypatch.setenv("UV_TOOL_DIR", str(home / "uv-tools"))
    monkeypatch.setattr(moxi.strays, "_user_site", lambda: str(home / "user-site"))


@pytest.fixture
def work(tmp_path) -> Path:
    """An empty directory of the test's own. The fence keeps its HOME in `tmp_path`, so a
    test that lists what it wrote there lists this instead."""
    path = tmp_path / "work"
    path.mkdir()
    return path


@pytest.fixture
def site(tmp_path, monkeypatch):
    """A fresh site on `sys.path` for the test's fake distributions.

    While it is active, `importlib.metadata` finds only the site's distributions and `moxi`
    itself: every other real distribution in the environment is hidden, so a test that uses
    this fixture cannot look one up by name, entry point or listing. Each package the site
    takes is dropped from `sys.modules` when it is first written and again at teardown, so
    a real package an earlier test imported can't stand in for the fake, and the fake can't
    outlive the test.
    """
    root = tmp_path / "site"
    root.mkdir()
    see_only(root, monkeypatch)
    monkeypatch.syspath_prepend(str(root))
    moxi.modules.reset()
    s = Site(root)
    yield s
    moxi.modules.reset()
    drop_modules(s.packages)


def drop_modules(packages: set[str]) -> None:
    """Forget every imported module under `packages`, so the next import reads it anew."""
    for mod in list(sys.modules):
        if mod.split(".")[0] in packages:
            del sys.modules[mod]


# -- local wheels and an offline uv, for the uv tests ---------------------------------------

def build_wheel(out: Path, name: str, version: str, files: dict[str, str], *,
                requires: tuple[str, ...] = (), extras: dict[str, list[str]] | None = None,
                scripts: dict[str, str] | None = None,
                entry_points: dict[str, dict[str, str]] | None = None) -> Path:
    """Write a pure-Python wheel by hand: no build backend, no index."""
    import base64
    import hashlib
    import zipfile

    norm = name.replace("-", "_")
    info = f"{norm}-{version}.dist-info"
    files = dict(files)
    files[f"{info}/METADATA"] = metadata(name, version, requires=requires, extras=extras)
    files[f"{info}/WHEEL"] = ("Wheel-Version: 1.0\nGenerator: moxi-tests\n"
                              "Root-Is-Purelib: true\nTag: py3-none-any\n")
    groups = dict(entry_points or {})
    if scripts:
        groups["console_scripts"] = scripts
    if groups:
        files[f"{info}/entry_points.txt"] = "".join(
            f"[{group}]\n" + "".join(f"{k} = {v}\n" for k, v in items.items())
            for group, items in groups.items())
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{norm}-{version}-py3-none-any.whl"
    record = []
    with zipfile.ZipFile(path, "w") as whl:
        for rel, text in files.items():
            data = text.encode()
            whl.writestr(rel, data)
            digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=")
            record.append(f"{rel},sha256={digest.decode()},{len(data)}")
        record.append(f"{info}/RECORD,,")
        whl.writestr(f"{info}/RECORD", "\n".join(record) + "\n")
    return path


def core_wheel(out: Path, version: str = "0.1.0") -> Path:
    """This checkout's `moxi` package as a wheel, with its console script: every file
    under `src/moxi`, its settings schema and vendored copies included."""
    src = Path(__file__).resolve().parents[1] / "src" / "moxi"
    files = {f"moxi/{p.relative_to(src).as_posix()}": p.read_text(encoding="utf-8")
             for p in sorted(src.rglob("*"))
             if p.is_file() and "__pycache__" not in p.parts}
    return build_wheel(out, "moxi", version, files, scripts={"moxi": "moxi.cli:main"})


@pytest.fixture
def offline_uv_env(tmp_path, uv_env):
    """Environment variables for an offline uv that writes nothing outside `tmp_path`.

    Its cache is a new one in tmp, so the tests that ask for it see only the wheels they
    build. The kit's `uv_env` is the other kind: the real cache, for a child that needs
    what is installed. One thing is taken from it, uv's installed Pythons
    (`UV_PYTHON_INSTALL_DIR`): the fence points the homes uv would find them under into
    tmp, so without it a machine whose only Python is one uv installed, off PATH, would
    have none to build with. Downloads stay off, so uv only reads that directory."""
    import os
    import shutil

    if shutil.which("uv") is None:
        pytest.skip("uv is not on PATH")
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("UV_", "VIRTUAL_ENV", "PYTHON"))}
    # No other moxi on PATH (the workspace's own), which the tool's moxi would tell as
    # holding strays.
    env["PATH"] = os.pathsep.join(
        p for p in env.get("PATH", "").split(os.pathsep)
        if p and not os.path.isfile(os.path.join(p, "moxi")))
    env.update(UV_TOOL_DIR=str(tmp_path / "tools"), UV_TOOL_BIN_DIR=str(tmp_path / "bin"),
               UV_CACHE_DIR=str(tmp_path / "uv-cache"), UV_OFFLINE="1",
               UV_PYTHON_INSTALL_DIR=uv_env["UV_PYTHON_INSTALL_DIR"],
               UV_PYTHON_DOWNLOADS="never", UV_NO_CONFIG="1", UV_NO_PROGRESS="1",
               MOXI_HOME=str(tmp_path / "moxi-home"))
    return env


def module_wheel(out: Path, name: str, *, requires: tuple[str, ...] = (),
                 extras: dict[str, list[str]] | None = None,
                 optional: dict[str, str] | None = None,
                 scripts: dict[str, str] | None = None, setup: str | None = None,
                 files: dict[str, str] | None = None, cli: str | None = None,
                 version: str = "0.1.0") -> Path:
    """A fake module's wheel: its package, a real announcement and its entry point."""
    pkg = f"moxi_{name}"
    fields = {"SUMMARY": repr(f"The {name} module."), "OPTIONAL": repr(optional or {}),
              "SETUP": repr(setup), "CLI": repr(cli)}
    body = {f"{pkg}/__init__.py": "", f"{pkg}/module.py": announcement(**fields)}
    body.update(files or {})
    return build_wheel(out, f"moxi-{name}", version, body, requires=requires,
                       extras=extras, scripts=scripts,
                       entry_points={"moxi.modules": {name: f"{pkg}.module"}})


@pytest.fixture
def wheels():
    """The wheel builders, for test files (which import nothing from this one)."""
    import types

    return types.SimpleNamespace(build=build_wheel, core=core_wheel, module=module_wheel)


# -- moxi.ipc on real Unix sockets ----------------------------------------------------------

#: A Unix socket path must stay under about 100 bytes, so IPC tests use short directories.
_SHORT_SOCKETS = 60


@pytest.fixture
def sock_dir(tmp_path_factory):
    """A short private directory for a test's Unix sockets, removed after the test."""
    import shutil
    import tempfile

    base = str(tmp_path_factory.getbasetemp())
    if len(base) > _SHORT_SOCKETS:
        base = "/tmp"
    path = Path(tempfile.mkdtemp(prefix="ipc", dir=base))
    yield path
    shutil.rmtree(path, ignore_errors=True)


class ServerThread:
    """An event loop on a thread of its own, running a `moxi.ipc` server, so a test's
    blocking code (the client, a raw socket) can talk to it from the main thread."""

    def __init__(self, address, handler, **options):
        import asyncio
        import threading

        self.address = str(address)
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()
        from moxi import ipc

        try:
            self.server = self.run(ipc.serve(self.address, handler, **options))
        except BaseException:
            self._stop_loop()
            raise

    def run(self, coro, timeout: float = 10.0):
        """Run coroutine `coro` on the server's loop and return its result."""
        import asyncio

        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout)

    def call(self, fn, *args, timeout: float = 10.0):
        """Call `fn(*args)` on the loop's thread and return its result."""
        async def on_loop():
            return fn(*args)
        return self.run(on_loop(), timeout)

    def close(self) -> None:
        async def stop():
            self.server.close()
            await self.server.wait_closed()
        try:
            if not self.loop.is_closed():
                self.run(stop())
        finally:
            self._stop_loop()

    def _stop_loop(self) -> None:
        if self.loop.is_closed():
            return
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(10)
        self.loop.close()


@pytest.fixture
def serving(sock_dir):
    """`serving(handler, **options)` starts a server at `<sock_dir>/s/a.sock` on a loop
    thread and closes it after the test."""
    started = []

    def start(handler, *, address=None, **options):
        thread = ServerThread(address or sock_dir / "s" / "a.sock", handler, **options)
        started.append(thread)
        return thread

    yield start
    for thread in reversed(started):
        thread.close()


class Raw:
    """A plain blocking Unix socket to a server, reading whole lines with a deadline."""

    def __init__(self, address, timeout: float = 10.0):
        import socket

        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(timeout)
        self.sock.connect(str(address))
        self.timeout = timeout
        self.buffer = b""

    def send(self, data: bytes) -> None:
        self.sock.sendall(data)

    def line(self) -> bytes:
        """The next line, without its newline. AssertionError at the end of the stream."""
        import time

        deadline = time.monotonic() + self.timeout
        while b"\n" not in self.buffer:
            assert time.monotonic() < deadline, "no line in time"
            chunk = self.sock.recv(65536)
            assert chunk, f"the stream ended; unread: {self.buffer[:200]!r}"
            self.buffer += chunk
        line, _, self.buffer = self.buffer.partition(b"\n")
        return line

    def json(self) -> dict:
        import json

        return json.loads(self.line())

    def closed(self) -> bool:
        """Read to the end of the stream: True when the server closed it with nothing
        more sent (anything unread fails the test)."""
        while True:
            try:
                chunk = self.sock.recv(65536)
            except ConnectionResetError:
                break  # closed with our bytes unread: a reset, still the end
            if not chunk:
                break
            self.buffer += chunk
        assert self.buffer == b"", f"unread: {self.buffer[:200]!r}"
        return True

    def close(self) -> None:
        self.sock.close()

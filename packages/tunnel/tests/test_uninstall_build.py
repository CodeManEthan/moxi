"""The real `moxi uninstall tunnel`, end to end, from wheels.

Builds the wheels of the core and of tunnel, installs them into a fresh venv inside a
temp HOME, and adds a fixture module, `alpha`, that declares two targets. Everything then
runs from that install, with an environment built from nothing: HOME and every XDG root
in the temp home, and `XDG_RUNTIME_DIR` unset, so the core finds no unit backend and its
unit removal does nothing. A stand-in `systemctl` on PATH records any call, and the test
asserts there was none: no case reaches a real unit manager. A fake `tailscale` on PATH
keeps the serve config. Only Tailscale entries are recorded, through `moxi tunnel add`:
with no unit backend a Cloudflare act is a failed unit act, so the Cloudflare cases run
in process in `test_uninstall.py`.

It proves the real order. A plain uninstall runs tunnel's teardown in its child, which
removes every address; then the listeners, then the package. With `--skip-teardown` the
addresses stay live and tunnel's own listener names each with its hand command; a
reinstall finds them as they were. With `--purge` after it those lines come before the
core's store questions, and once the record has gone the live entries read as config
Moxi did not apply. tunnel is reinstalled from its wheel between the runs.

It needs `uv` and builds wheels, so it is a `build` test: a plain pytest run leaves it out,
and `pytest -m build tests/test_uninstall_build.py` runs it.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import textwrap
import tomllib
from pathlib import Path

import pytest

from fake_tailscale import HOST, FakeTailscale

TESTS = Path(__file__).resolve().parent
PACKAGE = TESTS.parent
PACKAGES = PACKAGE.parent
UV = shutil.which("uv")

pytestmark = [pytest.mark.build,
              pytest.mark.touches("packages/moxi/pyproject.toml",
                                  "packages/moxi/src/moxi/uninstall.py",
                                  "packages/tunnel/pyproject.toml",
                                  "packages/tunnel/src/moxi_tunnel/uninstall.py",
                                  "packages/tunnel/src/moxi_tunnel/installs.py",
                                  "packages/tunnel/tests/test_uninstall_build.py"),
              pytest.mark.skipif(UV is None, reason="the uninstall build test needs uv"),
              pytest.mark.skipif(not sys.platform.startswith("linux"),
                                 reason="the uninstall build test runs on Linux")]

#: The fixture module: two targets, on its `port` (8461) and `other.port` (9000).
ALPHA = {
    "moxi_alpha/__init__.py": "",
    "moxi_alpha/module.py": "SUMMARY = 'A module made for a test.'\n"
                            "SETTINGS = 'settings.toml'\n",
    "moxi_alpha/settings.toml": """\
        [port]
        type = "int"
        default = 8461
        doc = "The page's port."
        read = "use"

        [other.port]
        type = "int"
        default = 9000
        doc = "The connector's port."
        read = "use"
        """,
    "moxi_alpha/tunnel_decl.py": """\
        GATEWAY = {"reach": "own-devices", "port_setting": "port"}
        CONNECTOR = {"reach": "public", "port_setting": "other.port"}
        """,
    "moxi_alpha-0.1.0.dist-info/METADATA": "Metadata-Version: 2.1\nName: moxi-alpha\n"
                                           "Version: 0.1.0\n",
    "moxi_alpha-0.1.0.dist-info/entry_points.txt": """\
        [moxi.modules]
        alpha = moxi_alpha.module

        [moxi.tunnel]
        alpha.gateway = moxi_alpha.tunnel_decl:GATEWAY
        alpha.connector = moxi_alpha.tunnel_decl:CONNECTOR
        """,
}

#: Records any call to the user's unit manager; the test asserts there was none.
FAKE_SYSTEMCTL = """#!/bin/sh
echo "$*" >> "$HOME/systemctl.calls"
exit 1
"""


def _version(directory: Path) -> str:
    with (directory / "pyproject.toml").open("rb") as f:
        return tomllib.load(f)["project"]["version"]


class Box:
    """The temp home, its venv and the commands run in it."""

    def __init__(self, base: Path, uv_env: dict):
        self.base = base
        self.uv_env = uv_env
        self.home = base / "home"
        self.home.mkdir()
        self.venv = self.home / ".local" / "share" / "moxi-venv"
        self.py = self.venv / "bin" / "python"
        self.fakebin = self.home / "fakebin"
        self.tailscale = FakeTailscale(self.fakebin)
        (self.fakebin / "systemctl").write_text(FAKE_SYSTEMCTL)
        (self.fakebin / "systemctl").chmod(0o755)
        uvbin = base / "uvbin"
        uvbin.mkdir()
        (uvbin / "uv").symlink_to(UV)
        self.env = {
            "HOME": str(self.home),
            "PATH": os.pathsep.join([str(self.venv / "bin"), str(self.fakebin), str(uvbin),
                                     "/usr/bin", "/bin"]),
            "LANG": "C.UTF-8",
            "TMPDIR": str(self.home / "tmp"),
            "XDG_CONFIG_HOME": str(self.home / ".config"),
            "XDG_DATA_HOME": str(self.home / ".local" / "share"),
            "XDG_STATE_HOME": str(self.home / ".local" / "state"),
            "XDG_CACHE_HOME": str(self.home / ".cache"),
            "UV_PYTHON_DOWNLOADS": "never",
            "PYTHONUNBUFFERED": "1",
        }
        (self.home / "tmp").mkdir()
        assert "XDG_RUNTIME_DIR" not in self.env
        self.wheels: dict[str, Path] = {}

    def uv(self, *args: str, timeout: float = 300) -> None:
        done = subprocess.run([UV, *args], capture_output=True, text=True, timeout=timeout,
                              env=self.uv_env)
        assert done.returncode == 0, done.stderr

    def build(self) -> None:
        for name, directory in (("moxi", PACKAGES / "moxi"), ("moxi-tunnel", PACKAGE)):
            out = self.base / "dist" / name
            self.uv("build", "--wheel", "--out-dir", str(out), str(directory))
            [self.wheels[name]] = out.glob("*.whl")
        self.uv("venv", "--python", sys.executable, str(self.venv), timeout=120)
        self.uv("pip", "install", "--python", str(self.py),
                *(str(w) for w in self.wheels.values()))
        site = Path(self.run(str(self.py), "-c", "import sysconfig; "
                             "print(sysconfig.get_path('purelib'))").stdout.strip())
        for relpath, text in ALPHA.items():
            path = site / relpath
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(textwrap.dedent(text))

    def reinstall_tunnel(self) -> None:
        self.uv("pip", "install", "--python", str(self.py), str(self.wheels["moxi-tunnel"]))
        assert self.has_tunnel()

    def has_tunnel(self) -> bool:
        got = self.run(str(self.py), "-c", "import importlib.util as u; "
                       "print(u.find_spec('moxi_tunnel') is not None)")
        return got.stdout.strip() == "True"

    def run(self, *argv: str) -> subprocess.CompletedProcess:
        return subprocess.run(list(argv), capture_output=True, text=True, timeout=120,
                              env=self.env, cwd=self.home, stdin=subprocess.DEVNULL)

    def moxi(self, *args: str) -> subprocess.CompletedProcess:
        """`moxi ...`, its stdout and stderr in one stream, in the order written."""
        return subprocess.run([str(self.venv / "bin" / "moxi"), *args],
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                              timeout=180, env=self.env, cwd=self.home,
                              stdin=subprocess.DEVNULL)

    def record(self) -> dict | None:
        path = self.home / ".local" / "state" / "moxi" / "tunnel" / "applied.json"
        return json.loads(path.read_text()) if path.exists() else None

    def live(self) -> list[str]:
        return sorted(self.tailscale.serve["TCP"])

    def status(self) -> dict:
        got = self.moxi("tunnel", "status", "--json", "--check")
        assert got.returncode == 0, got.stdout
        return {f"{t['module']}/{t['name']}": t for t in json.loads(got.stdout)["targets"]}


@pytest.fixture
def box(tmp_path, uv_env):
    base = tmp_path / "box"
    base.mkdir()
    b = Box(base, uv_env)
    b.build()
    yield b
    assert not (b.home / "systemctl.calls").exists(), \
        (b.home / "systemctl.calls").read_text()


def lines(done: subprocess.CompletedProcess) -> list[str]:
    return done.stdout.splitlines()


def index(output: list[str], text: str) -> int:
    found = [i for i, line in enumerate(output) if text in line]
    assert found, f"{text!r} not in:\n" + "\n".join(output)
    return found[0]


def test_the_real_uninstall_runs_the_teardown_then_the_listeners_then_purge(box):
    assert (box.wheels["moxi-tunnel"].name.split("-")[1]) == _version(PACKAGE)
    gateway = f"https://{HOST}:8461/"

    # 1. A plain uninstall: the teardown, in its child, removes both addresses.
    for target in ("alpha/gateway", "alpha/connector"):
        added = box.moxi("tunnel", "add", target, "--yes")
        assert added.returncode == 0, added.stdout
    assert box.live() == ["443", "8461"]
    done = box.moxi("uninstall", "tunnel")
    assert done.returncode == 0, done.stdout
    out = lines(done)
    assert index(out, "tunnel: removed alpha/connector") < index(out, "uninstalled tunnel")
    assert index(out, "tunnel: removed alpha/gateway") < index(out, "uninstalled tunnel")
    assert "stays live" not in done.stdout
    assert box.live() == []
    assert box.record()["entries"] == {}
    assert not box.has_tunnel()

    # 2. --skip-teardown: the address stays live, and tunnel's own listener names it.
    box.reinstall_tunnel()
    added = box.moxi("tunnel", "add", "alpha/gateway", "--yes")
    assert added.returncode == 0, added.stdout
    token = box.status()["alpha/gateway"]["change"]
    done = box.moxi("uninstall", "tunnel", "--skip-teardown")
    assert done.returncode == 0, done.stdout
    out = lines(done)
    named = index(out, f"tunnel: alpha/gateway stays live at {gateway}; remove it by hand "
                       "with: tailscale serve --https=8461 off")
    assert index(out, "teardown tunnel: skipped (--skip-teardown)") < named
    assert named < index(out, "uninstalled tunnel")
    assert box.live() == ["8461"]
    assert list(box.record()["entries"]) == ["alpha/gateway"]

    # 3. A reinstall finds the address as it was: the doctor's comparison and the token.
    box.reinstall_tunnel()
    row = box.status()["alpha/gateway"]
    assert (row["check"], row["state"], row["url"], row["change"]) == \
        ("ok", "on", gateway, token)

    # 4. --purge after it, with nobody at the keyboard: the listener's lines come before
    #    the core's store questions, and a store that can't be rebuilt is kept.
    done = box.moxi("uninstall", "tunnel", "--skip-teardown", "--purge")
    assert done.returncode == 0, done.stdout
    out = lines(done)
    assert index(out, "tunnel: alpha/gateway stays live") < \
        index(out, "tunnel: kept store applied")
    assert box.record() is not None

    # 5. --purge --yes: the record goes; the live entry stays, now config Moxi did not
    #    apply, so an add on its port needs --replace.
    box.reinstall_tunnel()
    done = box.moxi("uninstall", "tunnel", "--skip-teardown", "--purge", "--yes")
    assert done.returncode == 0, done.stdout
    out = lines(done)
    assert index(out, "tunnel: alpha/gateway stays live") < \
        index(out, "tunnel: removed store applied")
    assert box.record() is None
    assert box.live() == ["8461"]
    box.reinstall_tunnel()
    assert box.status()["alpha/gateway"]["state"] is None
    refused = box.moxi("tunnel", "add", "alpha/gateway", "--port", "8461", "--yes")
    assert refused.returncode == 3, refused.stdout
    assert "--replace" in refused.stdout
    assert box.live() == ["8461"]

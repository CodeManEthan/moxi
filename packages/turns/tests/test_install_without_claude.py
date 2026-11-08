"""Installing turns, comms and notes on a machine without Claude Code, from wheels.

turns' setup has a Claude Code step, and comms and notes require turns, so a machine
without `claude` once saw all three installs report a failed setup. This test holds the
fix end to end, the way a stranger meets it: it builds the wheels of the core, turns,
comms, notes and the workspace packages they require, then runs each case in a fresh
venv inside a temp HOME of its own:

- a plain wheel install of notes (and so of turns), nothing set up: `moxi doctor notes`
  and `moxi doctor turns` show no `fail`, and notes' root row says its setup has not
  run here;
- `moxi install comms` from a venv holding only the core: it exits 0, and turns' setup,
  run because the install brought turns in, prints its one skip line; `moxi doctor
  turns` then shows no `fail`;
- `moxi install notes` the same way: it exits 0, and `moxi doctor notes` shows no `fail`.

Each case runs with an environment built from nothing: HOME and every XDG root in the
temp home, and a PATH of the venv, a stand-in `systemctl` that records any call, `uv`
and the system's own folders. The test checks that PATH finds no `claude`. The runtime
dir holds no `systemd/private`, so the core finds no supervisor and writes no unit, and
no D-Bus address is set. The test asserts the stand-in `systemctl` was never called.
uv inside an install gets uv's real cache and installed Pythons (the kit's `uv_env`),
so the third-party requirements come from the warm cache or the index; every Moxi
distribution comes from the wheel this tree built, given with `--from`.

It needs `uv` and builds wheels, so it is a `build` test: a plain pytest run leaves it
out, and `pytest -m build tests/test_install_without_claude.py` runs it.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent
PACKAGE = TESTS.parent
PACKAGES = PACKAGE.parent
UV = shutil.which("uv")


def _normal(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _project(directory: Path) -> dict:
    with (directory / "pyproject.toml").open("rb") as f:
        return tomllib.load(f)["project"]


def _members() -> dict[str, Path]:
    """Every package under packages/, by normalized name, with its directory."""
    return {_normal(_project(pyproject.parent)["name"]): pyproject.parent
            for pyproject in sorted(PACKAGES.glob("*/pyproject.toml"))}


def _required(members: dict[str, Path], *roots: str) -> dict[str, Path]:
    """`roots` and every workspace package their required dependencies reach. Extras are
    not followed: each case installs the bare module."""
    found, todo = {}, list(roots)
    while todo:
        name = todo.pop()
        if name in found:
            continue
        found[name] = members[name]
        for requirement in _project(members[name]).get("dependencies", []):
            dep = _normal(re.match(r"[A-Za-z0-9._-]+", requirement).group(0))
            if dep in members:
                todo.append(dep)
    return found


MEMBERS = _members()
#: The workspace packages the cases build: the core, turns, comms, notes and what they
#: require.
BUILT = _required(MEMBERS, "moxi-comms", "moxi-notes")

pytestmark = [pytest.mark.build,
              pytest.mark.touches(*(f"packages/{d.name}/pyproject.toml"
                                    for d in BUILT.values()),
                                  "packages/moxi/src/moxi/install.py",
                                  "packages/moxi/src/moxi/_settle.py",
                                  "packages/moxi/src/moxi/modules.py",
                                  "packages/turns/src/moxi_turns/claude_setup.py",
                                  "packages/notes/src/moxi_notes/doctor.py",
                                  "packages/notes/src/moxi_notes/setup.py",
                                  "packages/turns/tests/test_install_without_claude.py"),
              pytest.mark.skipif(UV is None, reason="the install test needs uv"),
              pytest.mark.skipif(not sys.platform.startswith("linux"),
                                 reason="the install test runs on Linux")]

#: Records any call to the user's unit manager; the test asserts there was none.
FAKE_SYSTEMCTL = """#!/bin/sh
echo "$*" >> "$HOME/systemctl.calls"
exit 1
"""

#: The line turns' setup prints when it skips its Claude Code step.
SKIPPED = ("turns: Claude Code not found; skipped its plugin. Run moxi install turns "
           "again after installing it.")


@pytest.fixture
def wheels(tmp_path, uv_env) -> dict[str, Path]:
    """Each built package's wheel, by normalized name."""
    out = {}
    for name, directory in BUILT.items():
        dist = tmp_path / "dist" / name
        done = subprocess.run([UV, "build", "--wheel", "--out-dir", str(dist),
                               str(directory)], capture_output=True, text=True,
                              timeout=300, env=uv_env, check=False)
        assert done.returncode == 0, done.stderr
        [out[name]] = dist.glob("*.whl")
    return out


class Box:
    """A temp home, its venv and the commands run in it."""

    def __init__(self, base: Path, uv_env: dict, wheels: dict[str, Path]):
        self.uv_env = uv_env
        self.wheels = wheels
        self.home = base / "home"
        self.home.mkdir(parents=True)
        self.venv = self.home / ".local" / "share" / "moxi-venv"
        self.py = self.venv / "bin" / "python"
        fakebin = self.home / "fakebin"
        fakebin.mkdir()
        (fakebin / "systemctl").write_text(FAKE_SYSTEMCTL)
        (fakebin / "systemctl").chmod(0o755)
        uvbin = base / "uvbin"
        uvbin.mkdir()
        (uvbin / "uv").symlink_to(UV)
        runtime = self.home / "run"
        runtime.mkdir(mode=0o700)
        self.env = {
            "HOME": str(self.home),
            "PATH": os.pathsep.join([str(self.venv / "bin"), str(fakebin), str(uvbin),
                                     "/usr/bin", "/bin"]),
            "LANG": "C.UTF-8",
            "TMPDIR": str(self.home / "tmp"),
            "XDG_CONFIG_HOME": str(self.home / ".config"),
            "XDG_DATA_HOME": str(self.home / ".local" / "share"),
            "XDG_STATE_HOME": str(self.home / ".local" / "state"),
            "XDG_CACHE_HOME": str(self.home / ".cache"),
            "XDG_RUNTIME_DIR": str(runtime),
            "PYTHONUNBUFFERED": "1",
            # uv run by `moxi install` reads the real cache, as the build did.
            **{key: uv_env[key] for key in ("UV_CACHE_DIR", "UV_PYTHON_INSTALL_DIR",
                                            "UV_PYTHON_DOWNLOADS") if key in uv_env},
        }
        (self.home / "tmp").mkdir()
        assert shutil.which("claude", path=self.env["PATH"]) is None, \
            "the system's own folders hold a claude, so the case can't run without it"
        assert not (runtime / "systemd" / "private").exists()
        done = subprocess.run([UV, "venv", "--python", sys.executable, str(self.venv)],
                              capture_output=True, text=True, timeout=120, env=uv_env,
                              check=False)
        assert done.returncode == 0, done.stderr

    def pip(self, *names: str) -> None:
        """A plain wheel install: these packages' wheels, nothing set up."""
        done = subprocess.run([UV, "pip", "install", "--python", str(self.py),
                               *(str(self.wheels[n]) for n in names)],
                              capture_output=True, text=True, timeout=300, env=self.uv_env,
                              check=False)
        assert done.returncode == 0, done.stderr

    def moxi(self, *args: str) -> subprocess.CompletedProcess:
        """`moxi ...`, its stdout and stderr in one stream, with nobody at the keyboard."""
        return subprocess.run([str(self.venv / "bin" / "moxi"), *args],
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                              timeout=600, env=self.env, cwd=self.home,
                              stdin=subprocess.DEVNULL, check=False)

    def install(self, module: str) -> subprocess.CompletedProcess:
        """`moxi install`, with a `--from` for each Moxi distribution the module needs."""
        froms = []
        for name in _required(MEMBERS, f"moxi-{module}"):
            if name != "moxi":
                froms += ["--from", f"{name} @ {self.wheels[name].as_uri()}"]
        return self.moxi("install", module, *froms)

    def doctor(self, module: str) -> list[dict]:
        done = subprocess.run([str(self.venv / "bin" / "moxi"), "doctor", module, "--json"],
                              capture_output=True, text=True, timeout=180, env=self.env,
                              cwd=self.home, stdin=subprocess.DEVNULL, check=False)
        rows = json.loads(done.stdout)
        assert done.returncode == 0, (done.stderr, failing(rows))
        return rows

    def calls(self) -> str:
        path = self.home / "systemctl.calls"
        return path.read_text() if path.exists() else ""


def failing(rows: list[dict]) -> list[dict]:
    return [r for r in rows if r["state"] == "fail"]


def row(rows: list[dict], module: str, name: str) -> dict:
    [found] = [r for r in rows if r["module"] == module and r["name"] == name]
    return found


@pytest.fixture
def box(tmp_path, uv_env, wheels):
    made = []

    def make(name: str) -> Box:
        made.append(Box(tmp_path / name, uv_env, wheels))
        return made[-1]

    yield make
    for b in made:
        assert not b.calls(), b.calls()


def test_a_plain_wheel_install_of_notes_shows_no_fail_before_setup(box):
    b = box("plain")
    b.pip(*_required(MEMBERS, "moxi-notes"))
    notes = b.doctor("notes")
    assert not failing(notes)
    root = row(notes, "notes", "root")
    assert (root["state"], root["fix"]) == ("warn", "moxi install notes")
    assert root["detail"].startswith("notes' setup has not run here")
    turns = b.doctor("turns")
    assert not failing(turns)
    assert row(turns, "turns", "claude code")["state"] == "off"


def test_installing_comms_without_claude_code_succeeds(box):
    b = box("comms")
    b.pip("moxi")
    done = b.install("comms")
    assert done.returncode == 0, done.stdout
    assert SKIPPED in done.stdout, done.stdout
    assert "setup failed" not in done.stdout, done.stdout
    turns = b.doctor("turns")
    assert not failing(turns)
    states = {r["name"]: r["state"] for r in turns}
    assert (states["claude code"], states["claude placed copy"]) == ("off", "off")


def test_installing_notes_without_claude_code_succeeds(box):
    b = box("notes")
    b.pip("moxi")
    done = b.install("notes")
    assert done.returncode == 0, done.stdout
    assert SKIPPED in done.stdout, done.stdout
    assert "setup failed" not in done.stdout, done.stdout
    notes = b.doctor("notes")
    assert not failing(notes)
    assert row(notes, "notes", "root")["state"] == "ok"     # setup made the root a repo
    assert not failing(b.doctor("turns"))

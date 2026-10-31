"""The live tier: services from a built wheel against this system's own supervisor.

Not part of a plain run: it is skipped unless `MOXI_TEST_REAL_SUPERVISOR=1` is set when
pytest starts, and it is built for Linux with a systemd user manager so far. The rows for
launchd and Task Scheduler are still to come.

The core writes no unit for an editable install, and the workspace's environment is
editable, so the tier builds the core's and services' wheels, installs the core with
`uv tool install` into a tool directory in the test's tmp, and adds services to it with
`moxi install services --from <wheel>`. Every `moxi` it runs has `MOXI_HOME` in the
test's tmp as well, so its store, config and lock are the test's own; only the unit
files are the real user's.

It touches only units named for this run, by an id of 8 hex digits:

- two written rows, `test-<id>-web` (a daemon running `sleep`) and `test-<id>-tick` (a
  timer every 24 h, never run), so units `moxi-services-test-<id>-web.service`,
  `moxi-services-test-<id>-tick.timer` and its service: added, enabled, started,
  stopped, restarted (the daemon), disabled and removed;
- one throwaway foreign daemon, `moxitest-services-<id>.service`, adopted, started,
  stopped and forgotten, its file left as it was;
- one throwaway foreign timer, `moxitest-services-<id>-tick.timer` with its service,
  adopted (so wrapped), its key turned off, run once (recorded off), and removed (its
  schedule disabled, then unwrapped), its files left as they were.

It refuses to start while any `moxi-services-*` unit is in the unit directory: an
install of services under an empty store would plan those units away. At its end it
disables, deletes and forgets every unit named for the run, whatever happened, and fails
if one of them is left or any unit that was there before is gone.

The test's own process is fenced to its tmp, so the throwaway foreign units are written
and deleted by a child process.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

#: The environment pytest started with, read before any test fences its own.
_REAL = dict(os.environ)
REAL_SUPERVISOR = "MOXI_TEST_REAL_SUPERVISOR"


def _user_manager() -> bool:
    runtime = _REAL.get("XDG_RUNTIME_DIR")
    return bool(shutil.which("systemctl") and os.path.isdir("/run/systemd/system")
                and runtime and os.path.exists(os.path.join(runtime, "systemd", "private")))


def _workspace() -> Path | None:
    """The repository root these tests sit in, or None (as in an sdist)."""
    for parent in Path(__file__).resolve().parents:
        project = parent / "pyproject.toml"
        if project.is_file() and "[tool.uv.workspace]" in project.read_text():
            return parent
    return None


pytestmark = [
    pytest.mark.skipif(_REAL.get(REAL_SUPERVISOR) != "1",
                       reason=f"the live tier: set {REAL_SUPERVISOR}=1 to run it; it "
                              "installs throwaway units on this system's supervisor"),
    pytest.mark.skipif(sys.platform != "linux",
                       reason="the live tier is built for Linux so far"),
    pytest.mark.skipif(not _user_manager(), reason="no systemd user manager here"),
    pytest.mark.skipif(shutil.which("uv") is None, reason="no uv to install the wheels"),
    pytest.mark.skipif(_workspace() is None,
                       reason="no workspace to build the wheels from"),
]


def _unit_dir() -> Path:
    base = _REAL.get("XDG_CONFIG_HOME") or os.path.join(_REAL["HOME"], ".config")
    return Path(base) / "systemd" / "user"


#: A child that writes or deletes the throwaway foreign units' files.
_FILES = """
import os, shutil, sys
act, path = sys.argv[1], sys.argv[2]
if act == "write":
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        fh.write(sys.argv[3])
elif os.path.isdir(path) and not os.path.islink(path):
    shutil.rmtree(path)
elif os.path.lexists(path):
    os.unlink(path)
"""


class Box:
    """The tool environment in the test's tmp, and the run's throwaway names."""

    def __init__(self, tmp: Path, uv_env: dict[str, str]):
        self.id = os.urandom(4).hex()
        self.tmp = tmp
        self.home = tmp / "moxi-home"
        self.units = _unit_dir()
        keep = {k: v for k, v in _REAL.items()
                if not k.startswith(("MOXI_", "UV_", "PYTEST_"))}
        self.env = {**keep,
                    "UV_CACHE_DIR": uv_env["UV_CACHE_DIR"],
                    "UV_PYTHON_INSTALL_DIR": uv_env["UV_PYTHON_INSTALL_DIR"],
                    "UV_PYTHON_DOWNLOADS": "never",
                    "UV_TOOL_DIR": str(tmp / "tools"),
                    "UV_TOOL_BIN_DIR": str(tmp / "bin"),
                    "MOXI_HOME": str(self.home),
                    REAL_SUPERVISOR: "1"}
        self.web = f"test-{self.id}-web"
        self.tick = f"test-{self.id}-tick"
        self.adopt = f"test-{self.id}-adopt"
        self.wrap = f"test-{self.id}-wrap"
        self.foreign = f"moxitest-services-{self.id}.service"
        self.timer = f"moxitest-services-{self.id}-tick.timer"
        self.timer_service = f"moxitest-services-{self.id}-tick.service"

    def ours(self, entry: str) -> bool:
        return self.id in entry

    def run(self, argv: list[str], *, timeout: float = 120) -> subprocess.CompletedProcess:
        return subprocess.run(argv, env=self.env, capture_output=True, text=True,
                              timeout=timeout, stdin=subprocess.DEVNULL)

    def moxi(self, *args: str, code: int = 0) -> str:
        done = self.run([str(self.tmp / "bin" / "moxi"), *args], timeout=300)
        said = done.stdout + done.stderr
        assert done.returncode == code, f"moxi {' '.join(args)}: {done.returncode}\n{said}"
        return said

    def show(self, name: str) -> dict:
        return json.loads(self.moxi("services", "show", name, "--json"))

    def systemctl(self, *args: str) -> subprocess.CompletedProcess:
        return self.run(["systemctl", "--user", *args], timeout=60)

    def file(self, act: str, path: Path, text: str = "") -> None:
        done = self.run([sys.executable, "-I", "-c", _FILES, act, str(path), text])
        assert done.returncode == 0, done.stderr

    def listing(self) -> set[str]:
        if not self.units.is_dir():
            return set()
        return {str(p.relative_to(self.units)) for p in self.units.rglob("*")}

    def install(self) -> None:
        wheels = self.tmp / "wheels"
        root = _workspace()
        for package in ("moxi", "moxi-services"):
            done = subprocess.run(["uv", "build", "--wheel", "--package", package,
                                   "--out-dir", str(wheels)],
                                  cwd=root, env=self.env, capture_output=True, text=True,
                                  timeout=300, stdin=subprocess.DEVNULL)
            assert done.returncode == 0, done.stderr
        # The core by its wheel's own URL, so no index's `moxi` can stand in for it.
        core = next(wheels.glob("moxi-*.whl"))
        done = self.run(["uv", "tool", "install", f"moxi @ {core.as_uri()}",
                         "--find-links", str(wheels)], timeout=300)
        assert done.returncode == 0, done.stderr
        wheel = next(wheels.glob("moxi_services-*.whl"))
        self.moxi("install", "services", "--from", f"moxi-services @ {wheel.as_uri()}",
                  "--none")

    def write_foreign(self) -> None:
        """The throwaway foreign daemon and timer. The timer's service carries the
        test's `MOXI_HOME`, so the gate its drop-in runs reads and writes the test's own
        files."""
        assert " " not in str(self.home) and '"' not in str(self.home)
        self.file("write", self.units / self.foreign,
                  "[Unit]\nDescription=moxi services live test: a throwaway daemon\n\n"
                  "[Service]\nType=simple\nExecStart=/bin/sleep 600\n\n"
                  "[Install]\nWantedBy=default.target\n")
        self.file("write", self.units / self.timer_service,
                  "[Unit]\nDescription=moxi services live test: a throwaway timer's "
                  "service\n\n[Service]\nType=oneshot\n"
                  f"Environment=MOXI_HOME={self.home}\nExecStart=/bin/true\n")
        self.file("write", self.units / self.timer,
                  "[Unit]\nDescription=moxi services live test: a throwaway timer\n\n"
                  f"[Timer]\nOnActiveSec=1d\nUnit={self.timer_service}\n\n"
                  "[Install]\nWantedBy=timers.target\n")
        assert self.systemctl("daemon-reload").returncode == 0

    def clean(self) -> None:
        """Take away every unit named for this run, whatever state it was left in."""
        names = sorted({entry.split("/")[-1] for entry in self.listing()
                        if self.ours(entry) and not entry.endswith(".d")}
                       | {self.foreign, self.timer, self.timer_service})
        units = [n for n in names if n.endswith((".service", ".timer"))]
        # One at a time: systemctl gives up on every name when one has no file.
        for name in units:
            self.systemctl("disable", "--now", name)
        for entry in sorted(self.listing(), key=len, reverse=True):
            if self.ours(entry):
                self.file("delete", self.units / entry)
        self.systemctl("daemon-reload")
        for name in units:
            self.systemctl("reset-failed", name)


@pytest.fixture
def box(tmp_path, uv_env):
    b = Box(tmp_path, uv_env)
    before = b.listing()
    taken = sorted(e for e in before if e.split("/")[-1].startswith("moxi-services-"))
    if taken:
        pytest.fail("the live tier would plan these away, so it does not start: "
                    + ", ".join(taken))
    try:
        yield b
    finally:
        b.clean()
        after = b.listing()
        left = sorted(e for e in after if b.ours(e))
        gone = sorted(e for e in before - after if not b.ours(e))
        assert not left, f"units of this run were left: {left}"
        assert not gone, f"units that were there before are gone: {gone}"


def test_services_on_this_systems_user_manager(box):
    box.install()

    # A written daemon through every act.
    box.moxi("services", "add", box.web, "--", "sleep", "600")
    unit = box.units / f"moxi-services-{box.web}.service"
    assert unit.is_file()
    shown = box.show(box.web)
    assert (shown["verdict"]["state"], shown["state"]) == ("off", "inactive")
    box.moxi("services", "enable", box.web)
    shown = box.show(box.web)
    assert (shown["verdict"]["state"], shown["state"], shown["enabled"]) == \
        ("ok", "active", True)
    box.moxi("services", "stop", box.web)
    assert box.show(box.web)["state"] != "active"
    box.moxi("services", "start", box.web)
    assert box.show(box.web)["state"] == "active"
    box.moxi("services", "restart", box.web)
    assert box.show(box.web)["state"] == "active"
    box.moxi("services", "disable", box.web)
    shown = box.show(box.web)
    assert (shown["verdict"]["state"], shown["enabled"]) == ("off", False)
    box.moxi("services", "remove", box.web)
    assert not unit.exists()
    box.moxi("services", "show", box.web, code=3)

    # A written timer, its schedule armed and off again; never run.
    box.moxi("services", "add", box.tick, "--timer", "--every", "24h", "--", "true")
    timer = box.units / f"moxi-services-{box.tick}.timer"
    assert timer.is_file()
    box.moxi("services", "enable", box.tick)
    shown = box.show(box.tick)
    assert (shown["verdict"]["state"], shown["enabled"]) == ("ok", True)
    box.moxi("services", "disable", box.tick)
    assert box.show(box.tick)["enabled"] is False
    box.moxi("services", "remove", box.tick)
    assert not timer.exists()

    box.write_foreign()
    foreign = (box.units / box.foreign).read_text()
    timer_text = (box.units / box.timer).read_text()
    service_text = (box.units / box.timer_service).read_text()

    # A foreign daemon adopted, acted on, and forgotten with its file as it was.
    box.moxi("services", "add", box.adopt, "--unit", box.foreign, "--disable")
    box.moxi("services", "start", box.adopt)
    assert box.show(box.adopt)["state"] == "active"
    box.moxi("services", "stop", box.adopt)
    assert box.show(box.adopt)["state"] != "active"
    box.moxi("services", "remove", box.adopt)
    assert (box.units / box.foreign).read_text() == foreign
    box.moxi("services", "show", box.adopt, code=3)

    # A foreign timer adopted, so wrapped; its key off, a run held off; unwrapped.
    said = box.moxi("services", "add", box.wrap, "--unit", box.timer, "--enable")
    assert "runs are off until" not in said
    assert "turn them on with" not in said
    shown = box.show(box.wrap)
    assert shown["wrapped"] == {"module": "services", "key": f"adopted.{box.wrap}",
                                "name": box.wrap}
    assert (shown["verdict"]["state"], shown["enabled"]) == ("ok", True)
    dropin = box.units / f"{box.timer_service}.d"
    assert dropin.is_dir()
    box.moxi("services", "disable", box.wrap)
    said = box.moxi("services", "start", box.wrap)
    assert "recorded off and nothing ran" in said
    shown = box.show(box.wrap)
    assert shown["runs"] and shown["runs"][-1]["result"] == "off"
    said = box.moxi("services", "remove", box.wrap)
    assert "its schedule is disabled first" in said
    assert not dropin.exists()
    assert (box.units / box.timer).read_text() == timer_text
    assert (box.units / box.timer_service).read_text() == service_text
    assert box.systemctl("is-enabled", box.timer).stdout.strip() == "disabled"
    box.moxi("services", "show", box.wrap, code=3)

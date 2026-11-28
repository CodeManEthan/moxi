"""The memory scope on this machine's own systemd user manager, through `moxi-dev cap`.

`live`: never run by a check, and only with `MOXI_TEST_LIVE=1` on Linux with a systemd
user manager that delegates the memory controller; skipped everywhere else. Each test
makes one real `moxi-dev-cap-<12 hex>.scope` with small figures that its command never
brings to MemoryMax, so no test here ends in a kernel OOM kill: the one end is the line
watch's own SIGKILL. Each checks that no scope of its own is left behind.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from moxi_dev import scope

# Read when the module loads, before the fence empties the environment.
REAL_ENV = {name: os.environ[name] for name in ("XDG_RUNTIME_DIR",
                                                "DBUS_SESSION_BUS_ADDRESS")
            if name in os.environ}


def _host() -> scope.Host | str:
    return scope.find_host(scope.System(env={**REAL_ENV, "PATH": os.environ.get("PATH", "")}))


pytestmark = pytest.mark.live


@pytest.fixture(autouse=True)
def user_manager():
    found = _host()
    if not isinstance(found, scope.Host):
        pytest.skip(f"needs a systemd user manager with the memory controller ({found})")

ALLOC = """\
import os, sys, time
step_mib, top_mib, pause_s = int(sys.argv[1]), int(sys.argv[2]), float(sys.argv[3])
held = []
while sum(map(len, held)) < top_mib << 20:
    held.append(os.urandom(step_mib << 20))
    time.sleep(pause_s)
time.sleep(float(sys.argv[4]))
sys.exit(int(sys.argv[5]))
"""

CACHE = """\
import os, sys
where = sys.argv[1]
block = os.urandom(1 << 20)
with open(os.path.join(where, "big"), "wb") as fh:
    for _ in range(800):
        fh.write(block)
os.remove(os.path.join(where, "big"))
many = os.path.join(where, "many")
os.mkdir(many)
for n in range(150_000):
    with open(os.path.join(many, str(n)), "wb") as fh:
        fh.write(b"x")
for n in range(150_000):
    os.remove(os.path.join(many, str(n)))
"""


def caps() -> set[str]:
    listed = subprocess.run(["systemctl", "--user", "list-units", "--all", "--plain",
                             "--no-legend", "moxi-dev-cap-*"],
                            env={**REAL_ENV, "PATH": os.environ["PATH"]},
                            capture_output=True, text=True, timeout=30).stdout
    return {line.split()[0] for line in listed.splitlines() if line.strip()}


def cap(tmp_path: Path, high_mib: int, max_mib: int, *command: str) -> tuple[int, list[str]]:
    before = caps()
    env = {**REAL_ENV, "PATH": os.environ["PATH"], scope.WORKERS_ENV: "1",
           scope.MEMORY_HIGH_ENV: str(high_mib), scope.MEMORY_MAX_ENV: str(max_mib)}
    done = subprocess.run([sys.executable, "-m", "moxi_dev", "cap", "--", *command],
                          env=env, cwd=tmp_path, capture_output=True, text=True,
                          timeout=600)
    assert caps() <= before, "a scope was left behind"
    return done.returncode, done.stdout.strip().splitlines()


def script(tmp_path: Path, text: str) -> str:
    path = tmp_path / "work.py"
    path.write_text(textwrap.dedent(text))
    return str(path)


def test_a_small_command_passes_its_code_and_its_leftover_is_ended(tmp_path):
    work = script(tmp_path, ALLOC)
    code, out = cap(tmp_path, 150, 200, "sh", "-c",
                    f'setsid sleep 60 & exec "{sys.executable}" "{work}" 10 20 0 1 3')
    assert code == 3, out
    assert out[-2] == "moxi-dev cap: ended 1 process the command left running"
    assert re.fullmatch(r"moxi-dev cap: memory peak \d+\.\dG, held \d+\.\dG, line 0\.1G, "
                        r"high 0\.1G, max 0\.2G, stalled \d+\.\ds", out[-1]), out


def test_page_cache_and_reclaimable_slab_at_the_cap_are_never_ended(tmp_path):
    kind = subprocess.run(["stat", "-f", "-c", "%T", str(tmp_path)], capture_output=True,
                          text=True).stdout.strip()
    if kind == "tmpfs":
        pytest.skip("the test's tmp is tmpfs, which the cap counts in full; point "
                    "PYTEST_DEBUG_TEMPROOT at a disk")
    work = script(tmp_path, CACHE)
    code, out = cap(tmp_path, 80, 400, sys.executable, work, str(tmp_path))
    assert code == 0, out
    assert not any("ended for memory" in line for line in out), out
    assert out[-1].startswith("moxi-dev cap: memory peak "), out


def test_an_anonymous_grower_is_ended_by_the_line_watch(tmp_path):
    work = script(tmp_path, ALLOC)
    code, out = cap(tmp_path, 80, 400, sys.executable, work, "10", "150", "0.3", "60", "0")
    assert code == 1, out
    ended = [line for line in out if line.startswith("moxi-dev cap: ended for memory: ")]
    assert len(ended) == 1 and "past its line 0.1G under MemoryHigh 0.1G" in ended[0], out
    assert out[-1].startswith("moxi-dev cap: memory ended past its line (held "), out
    unit = re.search(r"(moxi-dev-cap-[0-9a-f]{12}\.scope)", ended[0]).group(1)
    shown = subprocess.run(["systemctl", "--user", "show", "--property=Result", unit],
                           env={**REAL_ENV, "PATH": os.environ["PATH"]},
                           capture_output=True, text=True, timeout=30).stdout
    assert "oom-kill" not in shown

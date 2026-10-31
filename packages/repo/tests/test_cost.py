"""What a git hook costs: the runner's import set, its import time and its wall time."""

from __future__ import annotations

import os
import statistics
import subprocess
import sys
import time

import pytest

from moxi_repo import api
from tests.conftest import POSIX_ONLY, git

pytestmark = POSIX_ONLY

def _imports(python, repo) -> set[str]:
    """Every module a real git hook start loads that isn't the standard library's."""
    done = subprocess.run([python, "-I", "-X", "importtime", "-m", "moxi_repo.run",
                           "pre-commit"], cwd=repo, capture_output=True, text=True,
                          env=dict(os.environ), timeout=60)
    assert done.returncode == 0, done.stderr
    names = set()
    for line in done.stderr.splitlines():
        parts = line.split("|")
        if len(parts) == 3 and line.startswith("import time:"):
            name = parts[2].strip()
            if name.split(".")[0] not in sys.stdlib_module_names and name != "imported package":
                names.add(name)
    return names - {"_virtualenv", "site", "sitecustomize", "usercustomize"}


def test_the_runner_imports_only_its_own_two_modules_and_the_standard_library(demo):
    repo = demo.repo()
    python = str(demo.hook_env["python"])
    assert _imports(python, repo) == {"moxi_repo"}
    api.add(repo, ["demo/lint"])
    # A run that calls a piece adds the light context module and the piece's own.
    assert _imports(python, repo) == {"moxi_repo", "moxi_repo.pieces",
                                      "demo_pieces", "demo_pieces.hooks"}


@pytest.mark.timed
def test_the_runner_imports_in_under_10_ms(demo):
    python = str(demo.hook_env["python"])
    # The suite's other workers can load the machine (eight parallel committer passes, a
    # pass over thousands of files), so a median over the bound is measured again, twice.
    for attempt in range(3):
        times = _import_times(python)
        if statistics.median(times) < 10_000:
            break
        time.sleep(1)
    assert statistics.median(times) < 10_000, times


def _import_times(python: str) -> list[int]:
    times = []
    for _ in range(5):
        done = subprocess.run([python, "-I", "-X", "importtime", "-c",
                               "import moxi_repo, moxi_repo.run, moxi_repo.pieces"],
                              capture_output=True, text=True, timeout=60)
        total = 0
        for line in done.stderr.splitlines():
            parts = line.split("|")
            if len(parts) == 3 and parts[2].strip() in ("moxi_repo", "moxi_repo.run",
                                                        "moxi_repo.pieces"):
                total = max(total, int(parts[1]))
        times.append(total)
    return times


@pytest.mark.timed
def test_a_git_hook_costs_under_twice_a_bare_interpreter(demo):
    repo = demo.repo()
    api.add(repo, ["demo/lint"])
    git(repo, "config", "moxi-repo.piece.demo/lint.enabled", "false")
    python = str(demo.hook_env["python"])

    def wall(argv) -> float:
        runs = []
        for _ in range(15):
            start = time.perf_counter()
            subprocess.run(argv, cwd=repo, capture_output=True, timeout=60)
            runs.append(time.perf_counter() - start)
        return statistics.median(runs)

    for attempt in range(2):
        floor = wall([python, "-I", "-c", "pass"])
        runner = wall([python, "-I", "-m", "moxi_repo.run", "pre-commit"])
        if runner < 2 * floor:
            break
    assert runner < 2 * floor, (runner, floor)
    assert sys.platform != "win32"

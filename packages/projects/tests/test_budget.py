"""What a reader pays for projects beyond the core's own part of the hook path.

The core holds its part, `import moxi.paths`, `available()` and `load()`, with its own
import test. On top of it a reader pays for projects' read API: importing it (which
`load` does) and the first reads. One test pins the modules that part may add to
`sys.modules`: projects' own, `sqlite3` and what it pulls in, and the core's
`moxi.store`, through which a read opens the store. The other, in the slow tier, times
`python -I` runs of the hook path with a `get` and a `for_path` against the same hook path
for a module whose announcement is one line. The two kinds of run alternate in one loop
and the test compares the fastest of each, so load that comes and goes during the test
falls on both sides alike and the least-disturbed run of each is what is compared.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time

import pytest

#: What projects' part may add to `sys.modules`.
ALLOWED = frozenset({
    "moxi_projects", "moxi_projects.module", "moxi_projects.api",
    "sqlite3", "sqlite3.dbapi2", "_sqlite3", "datetime", "_datetime", "warnings",
    "_py_warnings",
    "moxi.store",
})
#: What projects adds over the core's hook path, in milliseconds: its import and a read.
#: A hook that reads projects budgets this cost itself.
BUDGET_MS = 4.0
RUNS = 15

READS = ("f = modules.load('projects', interface=1); assert f.ok, f.reason; "
         "f.api.get('alpha'); f.api.for_path({path!r})")


def _env() -> dict[str, str]:
    return dict(os.environ)


def test_the_reads_add_only_projects_sqlite3_and_the_store_helper(golden_copy):
    path = os.path.join(golden_copy, "alpha", "src")
    code = ("import sys; import moxi.paths; from moxi import modules; "
            "assert modules.available('projects'); before = set(sys.modules); "
            + READS.format(path=path) + "; "
            "print(' '.join(sorted(set(sys.modules) - before)))")
    done = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True,
                          text=True, env=_env())
    assert done.returncode == 0, done.stderr
    added = set(done.stdout.split())
    assert "moxi_projects.api" in added
    assert added <= ALLOWED, sorted(added - ALLOWED)


def _run_ms(code: str, env: dict) -> float:
    start = time.perf_counter()
    subprocess.run([sys.executable, "-I", "-c", code], check=True, env=env,
                   stdout=subprocess.DEVNULL)
    return (time.perf_counter() - start) * 1000


def _fastest_ms(reads: str, floor: str, env: dict) -> tuple[float, float]:
    """The fastest run of each, the two kinds alternating so neither gets a quieter spell."""
    read_times, floor_times = [], []
    for _ in range(RUNS):
        floor_times.append(_run_ms(floor, env))
        read_times.append(_run_ms(reads, env))
    return min(read_times), min(floor_times)


@pytest.mark.slow
def test_projects_part_of_the_hook_path_stays_under_its_budget(golden_copy, tmp_path):
    site = tmp_path / "site"
    (site / "moxi_hookcost").mkdir(parents=True)
    (site / "moxi_hookcost" / "__init__.py").write_text("")
    (site / "moxi_hookcost" / "module.py").write_text("SUMMARY = 'one line'\n")
    info = site / "moxi_hookcost-0.1.0.dist-info"
    info.mkdir()
    (info / "METADATA").write_text("Metadata-Version: 2.1\nName: moxi-hookcost\n"
                                   "Version: 0.1.0\n")
    (info / "entry_points.txt").write_text("[moxi.modules]\nhookcost = moxi_hookcost.module\n")
    hook = (f"import sys; sys.path.insert(0, {str(site)!r}); import moxi.paths; "
            "from moxi import modules; assert modules.available({name!r}); ")
    floor = hook.format(name="hookcost") + "assert modules.load('hookcost').ok"
    reads = hook.format(name="projects") + READS.format(
        path=os.path.join(golden_copy, "alpha", "src"))
    env = _env()
    read_ms, floor_ms = _fastest_ms(reads, floor, env)
    ms = read_ms - floor_ms
    load = os.getloadavg()[0] if hasattr(os, "getloadavg") else float("nan")
    print(f"\nprojects' part of the hook path: {ms:.1f} ms at load average {load:.2f}")
    assert ms < BUDGET_MS

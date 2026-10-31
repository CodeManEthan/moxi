"""What importing the core costs: the light-import rule, and the timed hook path.

The deterministic test holds the core modules a hook or a command imports first free of
the dependencies and the heavy standard modules.

A hook may cost at most 15 ms per hook process: what Moxi imports and does in it, over a
bare `python -I` start. The ceiling is per hook, since the hooks of one event run side by
side. Each share of it is held by its own test. The core's two shares are held here:

- the hook path (`import moxi.paths`, `available()` and `load()`) under 9 ms, for an
  absent module and for a module whose announcement is one line;
- `import moxi.config` with one `load()` of a small file under 7 ms.

One more test runs both in one process and holds them under the 15 ms ceiling together.
A module's own imports are held by that module's test. The hook path's own `sys.modules`
check is in test_hook_path.py.

How the timed tests (tier `slow`, marked `timed`) measure: 51 pairs, each a `python -I -c pass` start (the
floor) followed at once by a `python -I` run of the code. Each pair gives the code's run
minus the floor's, and the test takes the lower quartile of those 51 differences.

- Pairs, not two separate batches: the two runs of a pair are a few milliseconds apart, so
  a slow patch on the machine falls on both. Seven floor runs and then seven code runs saw
  different moments, and their medians wandered by about 1.5 ms between test runs, with
  only about 1 ms between the measured cost and the bound.
- Disturbance only ever adds time. A pair whose code run was slowed reads high; a pair
  whose floor run was slowed reads low. Most pairs have neither.
- Not the minimum difference: it is nearly always a pair whose floor run was slowed, so
  it reads well under the real cost and moves by several milliseconds from one test run
  to the next.
- Not the median: the pairs whose code run was slowed pull it up, and those pairs are the
  more common kind, because the code run is the longer one.
- The lower quartile sits among the undisturbed pairs, above the few low ones and below
  the high ones. At an ordinary load it reads about a millisecond under the median of the
  differences.

A machine under sustained CPU load runs every start slower, the floor and the code alike,
so every measure of the difference reads higher too; that is real cost, not noise, and
the bounds stay where they are.
"""

from __future__ import annotations

import os
import statistics
import subprocess
import sys
import textwrap
import time

import pytest

LIGHT = ("moxi.cli", "moxi.paths", "moxi.config", "moxi.modules", "moxi.files",
         "moxi.log", "moxi.exit", "moxi.doctor")
HEAVY = ("psutil", "tomlkit", "moxi._vendor.tomlkit", "packaging", "moxi._vendor.packaging",
         "platformdirs", "asyncio", "sqlite3", "_sqlite3")
#: A hook's ceiling, per hook process, over a bare `python -I` start.
HOOK_CEILING_MS = 15.0
#: The core's share of it for the hook path.
HOOK_PATH_MS = 9.0
#: The core's share of it for `import moxi.config` with one `load()`.
CONFIG_MS = 7.0
#: Floor-then-code pairs per timed test.
PAIRS = 51


@pytest.mark.parametrize("module", LIGHT)
def test_the_light_core_modules_import_no_heavy_module(module):
    code = textwrap.dedent(f"""\
        import sys
        import {module}
        print(" ".join(sorted(m for m in {HEAVY!r} if m in sys.modules)))
        """)
    done = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True,
                          text=True, check=True)
    assert done.stdout.strip() == "", f"{module} imports {done.stdout.strip()}"


def test_importing_the_store_pulls_no_typing_and_no_sqlite():
    """`typing` costs over a millisecond, and `sqlite3` more; the store imports neither
    until a function needs it, and `typing` only for a type checker."""
    code = ("import sys; import moxi.store; "
            "print(' '.join(m for m in ('typing', 'sqlite3', '_sqlite3') if m in sys.modules))")
    done = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True,
                          text=True, check=True)
    assert done.stdout.strip() == "", f"moxi.store imports {done.stdout.strip()}"


def _run_ms(argv: list[str]) -> float:
    start = time.perf_counter()
    subprocess.run(argv, check=True, stdout=subprocess.DEVNULL)
    return (time.perf_counter() - start) * 1000


def _over_floor(code: str) -> float:
    """The lower quartile of `PAIRS` differences, each a run of `code` minus the floor run
    just before it (the module docstring says why)."""
    floor = [sys.executable, "-I", "-c", "pass"]
    subject = [sys.executable, "-I", "-c", code]
    differences = []
    for _ in range(PAIRS):
        before = _run_ms(floor)
        differences.append(_run_ms(subject) - before)
    return statistics.quantiles(differences, n=4, method="inclusive")[0]


@pytest.fixture
def one_line_module(tmp_path):
    """A module `hookcost` whose announcement is a one-line package, in its own directory."""
    root = tmp_path / "site"
    (root / "moxi_hookcost").mkdir(parents=True)
    (root / "moxi_hookcost" / "__init__.py").write_text("")
    (root / "moxi_hookcost" / "module.py").write_text("SUMMARY = 'one line'\n")
    info = root / "moxi_hookcost-0.1.0.dist-info"
    info.mkdir()
    (info / "METADATA").write_text("Metadata-Version: 2.1\nName: moxi-hookcost\n"
                                   "Version: 0.1.0\n")
    (info / "entry_points.txt").write_text("[moxi.modules]\nhookcost = moxi_hookcost.module\n")
    return root


HOOK = ("import sys; sys.path.insert(0, {root!r}); import moxi.paths; "
        "from moxi import modules; assert modules.available({name!r}) is {there}; "
        "assert modules.load({name!r}).state == {state!r}")


def _record(name: str, ms: float) -> None:
    load = os.getloadavg()[0] if hasattr(os, "getloadavg") else float("nan")
    print(f"\n{name}: {ms:.1f} ms over the floor at load average {load:.2f}")


@pytest.mark.slow
@pytest.mark.timed
def test_the_hook_path_for_an_absent_module(tmp_path):
    ms = _over_floor(HOOK.format(root=str(tmp_path), name="nosuchmodule", there=False,
                                 state="absent"))
    _record("hook path, absent module", ms)
    assert ms < HOOK_PATH_MS


@pytest.mark.slow
@pytest.mark.timed
def test_the_hook_path_for_a_one_line_module(one_line_module):
    ms = _over_floor(HOOK.format(root=str(one_line_module), name="hookcost", there=True,
                                 state="present"))
    _record("hook path, one-line module", ms)
    assert ms < HOOK_PATH_MS


SCHEMA = '[mode]\ntype = "string"\ndefault = "a"\ndoc = "A mode."\nread = "use"\n'
CONFIG = ("import moxi.config as c; s = c.Schema.parse({schema!r}); "
          "assert c.load('demo', s, path={path!r})['mode'] == 'b'")


@pytest.fixture
def config_file(tmp_path):
    conf = tmp_path / "config.toml"
    conf.write_text('mode = "b"\n')
    return conf


@pytest.mark.slow
@pytest.mark.timed
def test_config_import_and_one_load(config_file):
    ms = _over_floor(CONFIG.format(schema=SCHEMA, path=str(config_file)))
    _record("import moxi.config + one load()", ms)
    assert ms < CONFIG_MS


@pytest.mark.slow
@pytest.mark.timed
def test_the_hook_path_and_config_together_stay_under_a_hooks_ceiling(one_line_module,
                                                                      config_file):
    code = "; ".join([
        HOOK.format(root=str(one_line_module), name="hookcost", there=True,
                    state="present"),
        CONFIG.format(schema=SCHEMA, path=str(config_file))])
    ms = _over_floor(code)
    _record("hook path, one-line module + import moxi.config + one load()", ms)
    assert ms < HOOK_CEILING_MS

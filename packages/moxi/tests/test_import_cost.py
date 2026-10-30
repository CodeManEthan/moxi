"""What importing the core costs: the light-import rule, and the timed hook path.

The deterministic test holds the core modules a hook or a command imports first free of
the dependencies and the heavy standard modules. The timed tests (tier `slow`) take the
median of seven `python -I` runs minus the median of seven `python -I -c pass`, and hold
each under 10 ms: the hook path (`import moxi.paths`, `available()` and `load()`) for an
absent module and for a module whose announcement is one line, and `import moxi.config`
with one `load()` of a small file. The hook path's own `sys.modules` check is in
test_hook_path.py.
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
BUDGET_MS = 10.0
RUNS = 7


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


def _median_ms(argv: list[str], env: dict | None = None) -> float:
    times = []
    for _ in range(RUNS):
        start = time.perf_counter()
        subprocess.run(argv, check=True, env=env, stdout=subprocess.DEVNULL)
        times.append((time.perf_counter() - start) * 1000)
    return statistics.median(times)


def _over_floor(code: str, env: dict | None = None) -> float:
    floor = _median_ms([sys.executable, "-I", "-c", "pass"], env)
    return _median_ms([sys.executable, "-I", "-c", code], env) - floor


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
def test_the_hook_path_for_an_absent_module(tmp_path):
    ms = _over_floor(HOOK.format(root=str(tmp_path), name="nosuchmodule", there=False,
                                 state="absent"))
    _record("hook path, absent module", ms)
    assert ms < BUDGET_MS


@pytest.mark.slow
def test_the_hook_path_for_a_one_line_module(one_line_module):
    ms = _over_floor(HOOK.format(root=str(one_line_module), name="hookcost", there=True,
                                 state="present"))
    _record("hook path, one-line module", ms)
    assert ms < BUDGET_MS


@pytest.mark.slow
def test_config_import_and_one_load(tmp_path):
    schema = ('[mode]\ntype = "string"\ndefault = "a"\ndoc = "A mode."\nread = "use"\n')
    conf = tmp_path / "config.toml"
    conf.write_text('mode = "b"\n')
    code = (f"import moxi.config as c; s = c.Schema.parse({schema!r}); "
            f"assert c.load('demo', s, path={str(conf)!r})['mode'] == 'b'")
    ms = _over_floor(code)
    _record("import moxi.config + one load()", ms)
    assert ms < BUDGET_MS

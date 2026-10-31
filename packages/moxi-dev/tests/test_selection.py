"""The kit's selection, durations and seam helper, each run in a pytest of its own.

Each test writes a small package (the kit's conftest line and a package's tier `-m`)
with pytester and runs pytest on it, in process unless the run needs workers.
"""

from __future__ import annotations

import json

import pytest

from moxi_dev import testing
from moxi_dev.testing import touches_match

INI = """\
[pytest]
addopts = -m 'not browser and not slow and not build and not live'
"""

TIERED = """\
import pytest

def test_unit():
    pass

@pytest.mark.slow
def test_slow():
    pass

@pytest.mark.build
def test_build():
    pass

@pytest.mark.browser
def test_browser():
    pass

@pytest.mark.live
def test_live():
    pass

@pytest.mark.slow
@pytest.mark.touches("packages/p/src/p/claude/**")
def test_slow_touches():
    pass

@pytest.mark.live
@pytest.mark.touches("packages/p/**")
def test_live_touches():
    pass

@pytest.mark.touches("packages/p/**")
def test_plain_touches():
    pass

@pytest.mark.finding("L7")
def test_holds_l7():
    pass

@pytest.mark.slow
@pytest.mark.finding("L7")
def test_slow_holds_l7():
    pass

@pytest.mark.finding("L8")
def test_holds_l8():
    pass
"""


@pytest.fixture
def package(pytester):
    pytester.makeconftest('pytest_plugins = ["moxi_dev.testing"]\n')
    pytester.makeini(INI)
    pytester.makepyfile(test_tiers=TIERED)
    return pytester


def ran(result) -> set[str]:
    """The names of the tests a run ran (passed or skipped)."""
    names = set()
    for report in result.reprec.getreports("pytest_runtest_logreport"):
        if report.when == "call" or (report.when == "setup" and report.skipped):
            names.add(report.nodeid.rsplit("::", 1)[1])
    return names


def changed(pytester, *paths) -> str:
    where = pytester.path / "changed-paths"
    where.write_text("".join(p + "\0" for p in paths))
    return str(where)


PLAIN = {"test_unit", "test_plain_touches", "test_holds_l7", "test_holds_l8"}


def test_a_plain_run_runs_no_tier(package):
    result = package.runpytest("-p", "no:cacheprovider")
    assert ran(result) == PLAIN


def test_every_tier_but_live_comes_back(package, monkeypatch):
    monkeypatch.setenv(testing.TIERS_ENV, "all")
    result = package.runpytest("-p", "no:cacheprovider")
    assert ran(result) == PLAIN | {"test_slow", "test_build", "test_browser",
                                   "test_slow_touches", "test_slow_holds_l7"}


def test_a_changed_path_brings_back_the_tier_tests_that_touch_it(package, monkeypatch):
    monkeypatch.setenv(testing.CHANGED_ENV,
                       changed(package, "README.md", "packages/p/src/p/claude/hooks.py"))
    result = package.runpytest("-p", "no:cacheprovider")
    assert ran(result) == PLAIN | {"test_slow_touches"}


def test_a_change_elsewhere_brings_nothing_back(package, monkeypatch):
    monkeypatch.setenv(testing.CHANGED_ENV, changed(package, "packages/p/src/p/codex.py"))
    result = package.runpytest("-p", "no:cacheprovider")
    assert ran(result) == PLAIN


@pytest.mark.parametrize("glob", ["src/p/claude/**", "tests/test_x.py", "**/claude/**", ""])
def test_a_touches_glob_not_from_the_repository_root_is_refused(pytester, glob):
    pytester.makeconftest('pytest_plugins = ["moxi_dev.testing"]\n')
    pytester.makeini(INI)
    pytester.makepyfile(test_bad=f"""
import pytest

@pytest.mark.slow
@pytest.mark.touches("packages/p/ok/**", {glob!r})
def test_slow():
    pass
""")
    result = pytester.runpytest("-p", "no:cacheprovider")
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    err = "\n".join(result.errlines)
    assert "a touches glob is a path from the repository's root, under packages/" in err
    assert f"test_bad.py::test_slow: {glob!r}" in err
    assert "packages/p/ok/**" not in err.split("these are not:")[1]


def test_a_lifted_test_still_answers_to_k(package, monkeypatch):
    monkeypatch.setenv(testing.TIERS_ENV, "all")
    result = package.runpytest("-p", "no:cacheprovider", "-k", "not touches")
    assert "test_slow" in ran(result) and "test_slow_touches" not in ran(result)


def test_a_finding_runs_only_its_tests_whatever_their_tier(package, monkeypatch):
    monkeypatch.setenv(testing.FINDING_ENV, "L7")
    result = package.runpytest("-p", "no:cacheprovider")
    assert ran(result) == {"test_holds_l7", "test_slow_holds_l7"}
    monkeypatch.setenv(testing.FINDING_ENV, "L7,L8")
    result = package.runpytest("-p", "no:cacheprovider")
    assert ran(result) == {"test_holds_l7", "test_slow_holds_l7", "test_holds_l8"}


def test_a_finding_no_test_carries_runs_nothing(package, monkeypatch):
    monkeypatch.setenv(testing.FINDING_ENV, "L99")
    result = package.runpytest("-p", "no:cacheprovider")
    assert ran(result) == set()
    assert result.ret == pytest.ExitCode.NO_TESTS_COLLECTED


@pytest.mark.parametrize("var, value, says", [
    (testing.FINDING_ENV, "L7,Z5", "names findings as L<n>"),
    (testing.FINDING_ENV, "50", "names findings as L<n>"),
    (testing.TIERS_ENV, "slow", "is 'all' or unset"),
    (testing.CHANGED_ENV, "/no/such/file", "can't read"),
    (testing.TIMED_ENV, "serial", "is 'hold' or 'only' or unset")])
def test_a_bad_selection_is_a_usage_error(package, monkeypatch, var, value, says):
    monkeypatch.setenv(var, value)
    result = package.runpytest("-p", "no:cacheprovider")
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    assert says in "\n".join(result.errlines)


LIVE_AND_TIERED = """\
import pytest

def test_unit():
    pass

@pytest.mark.live
@pytest.mark.slow
@pytest.mark.touches("packages/p/**")
@pytest.mark.finding("L7")
def test_live_and_slow():
    pass

@pytest.mark.live
@pytest.mark.build
@pytest.mark.touches("packages/p/**")
@pytest.mark.finding("L7")
def test_live_and_build():
    pass
"""


@pytest.mark.parametrize("var, value", [(testing.TIERS_ENV, "all"),
                                        (testing.CHANGED_ENV, "packages/p/src/p/x.py"),
                                        (testing.FINDING_ENV, "L7")])
def test_a_live_test_in_another_tier_is_never_put_back_even_with_live_on(
        pytester, monkeypatch, var, value):
    pytester.makeconftest('pytest_plugins = ["moxi_dev.testing"]\n')
    pytester.makeini(INI)
    pytester.makepyfile(test_live=LIVE_AND_TIERED)
    # as if this pytest had started with MOXI_TEST_LIVE=1, which an in-process run
    # reads from the outer one
    monkeypatch.setitem(testing._SESSION, "live", True)
    live = pytester.runpytest("-p", "no:cacheprovider", "-m", "live")
    live.assert_outcomes(passed=2)
    if var == testing.CHANGED_ENV:
        value = changed(pytester, value)
    monkeypatch.setenv(var, value)
    result = pytester.runpytest("-p", "no:cacheprovider")
    assert ran(result) == (set() if var == testing.FINDING_ENV else {"test_unit"})


def test_selection_holds_under_workers_and_the_controller_writes_the_durations(
        package, monkeypatch):
    out = package.path / "out" / "durations.json"
    monkeypatch.setenv(testing.TIERS_ENV, "all")
    monkeypatch.setenv(testing.DURATIONS_ENV, str(out))
    result = package.runpytest_subprocess("-p", "no:cacheprovider", "-n", "2")
    result.assert_outcomes(passed=9)
    tests = json.loads(out.read_text())["tests"]
    assert {nodeid.rsplit("::", 1)[1] for nodeid in tests} == PLAIN | {
        "test_slow", "test_build", "test_browser", "test_slow_touches",
        "test_slow_holds_l7"}
    lifted = {nodeid.rsplit("::", 1)[1]: entry.get("lifted")
              for nodeid, entry in tests.items()}
    assert lifted["test_build"] == ["build"] and lifted["test_browser"] == ["browser"]
    assert lifted["test_slow"] == ["slow"] and lifted["test_unit"] is None


def test_the_durations_name_the_tiers_a_changed_path_put_back(package, monkeypatch):
    out = package.path / "durations.json"
    monkeypatch.setenv(testing.DURATIONS_ENV, str(out))
    monkeypatch.setenv(testing.CHANGED_ENV, changed(package, "packages/p/src/p/claude/x"))
    package.runpytest("-p", "no:cacheprovider")
    tests = {nodeid.rsplit("::", 1)[1]: entry
             for nodeid, entry in json.loads(out.read_text())["tests"].items()}
    assert {name for name, entry in tests.items() if "lifted" in entry} == {
        "test_slow_touches"}
    assert tests["test_slow_touches"]["lifted"] == ["slow"]


# --- timed tests ---------------------------------------------------------------------------

TIMED = """\
import pytest

def test_unit():
    pass

@pytest.mark.timed
def test_plain_timed():
    pass

@pytest.mark.slow
@pytest.mark.timed
def test_slow_timed():
    pass

@pytest.mark.slow
@pytest.mark.timed
@pytest.mark.touches("packages/p/src/**")
def test_slow_timed_touches():
    pass

@pytest.mark.live
@pytest.mark.timed
@pytest.mark.touches("packages/p/**")
def test_live_timed():
    pass

@pytest.mark.slow
def test_slow():
    pass

@pytest.mark.timed
@pytest.mark.finding("L7")
def test_timed_holds_l7():
    pass
"""


@pytest.fixture
def timed_package(pytester):
    pytester.makeconftest('pytest_plugins = ["moxi_dev.testing"]\n')
    pytester.makeini(INI)
    pytester.makepyfile(test_timed=TIMED)
    return pytester


def held(path) -> set[str]:
    return {nodeid.rsplit("::", 1)[1] for nodeid in json.loads(path.read_text())["held"]}


def test_a_plain_run_runs_a_timed_test_where_it_falls(timed_package):
    result = timed_package.runpytest("-p", "no:cacheprovider")
    assert ran(result) == {"test_unit", "test_plain_timed", "test_timed_holds_l7"}


@pytest.mark.parametrize("var, value, timed", [
    (None, None, {"test_plain_timed", "test_timed_holds_l7"}),
    (testing.TIERS_ENV, "all", {"test_plain_timed", "test_slow_timed",
                                "test_slow_timed_touches", "test_timed_holds_l7"}),
    (testing.CHANGED_ENV, "packages/p/src/p/x.py", {"test_plain_timed",
                                                    "test_slow_timed_touches",
                                                    "test_timed_holds_l7"}),
    (testing.FINDING_ENV, "L7", {"test_timed_holds_l7"})],
    ids=["plain", "every-tier", "changed", "finding"])
def test_hold_leaves_out_what_only_runs(timed_package, monkeypatch, var, value, timed):
    """What a hold run leaves out and names is exactly what an only run with the same
    selection runs, under every tier, a changed path and a finding alike."""
    if var == testing.CHANGED_ENV:
        value = changed(timed_package, value)
    if var is not None:
        monkeypatch.setenv(var, value)
    out = timed_package.path / "held.json"
    monkeypatch.setenv(testing.DURATIONS_ENV, str(out))
    monkeypatch.setenv(testing.TIMED_ENV, "hold")
    everything = ran(timed_package.runpytest("-p", "no:cacheprovider"))
    assert not everything & timed and "test_live_timed" not in everything
    assert held(out) == timed
    assert set(json.loads(out.read_text())["tests"]) == {
        f"test_timed.py::{name}" for name in everything}
    monkeypatch.setenv(testing.TIMED_ENV, "only")
    monkeypatch.setenv(testing.DURATIONS_ENV, str(timed_package.path / "only.json"))
    result = timed_package.runpytest("-p", "no:cacheprovider")
    assert ran(result) == timed
    assert "held" not in json.loads((timed_package.path / "only.json").read_text())


def test_hold_names_the_held_tests_under_workers(timed_package, monkeypatch):
    out = timed_package.path / "out" / "durations.json"
    monkeypatch.setenv(testing.TIERS_ENV, "all")
    monkeypatch.setenv(testing.TIMED_ENV, "hold")
    monkeypatch.setenv(testing.DURATIONS_ENV, str(out))
    result = timed_package.runpytest_subprocess("-p", "no:cacheprovider", "-n", "2")
    result.assert_outcomes(passed=2)
    assert held(out) == {"test_plain_timed", "test_slow_timed", "test_slow_timed_touches",
                         "test_timed_holds_l7"}


def test_hold_with_nothing_timed_names_none(package, monkeypatch):
    out = package.path / "durations.json"
    monkeypatch.setenv(testing.TIMED_ENV, "hold")
    monkeypatch.setenv(testing.DURATIONS_ENV, str(out))
    assert ran(package.runpytest("-p", "no:cacheprovider")) == PLAIN
    assert json.loads(out.read_text())["held"] == []


# --- durations ---------------------------------------------------------------------------

DURATIONS = """\
import time
import pytest

@pytest.fixture
def slow_setup():
    time.sleep(0.05)

def test_sleeps(slow_setup):
    time.sleep(0.1)

def test_fails():
    assert False

@pytest.mark.skip(reason="not today")
def test_skipped():
    pass
"""


def test_durations_hold_each_test_s_phases_and_outcome(pytester, monkeypatch):
    pytester.makeconftest('pytest_plugins = ["moxi_dev.testing"]\n')
    pytester.makepyfile(test_d=DURATIONS)
    out = pytester.path / "durations.json"
    monkeypatch.setenv(testing.DURATIONS_ENV, str(out))
    pytester.runpytest("-p", "no:cacheprovider")
    data = json.loads(out.read_text())
    assert data["format"] == 1
    tests = {nodeid.rsplit("::", 1)[1]: entry for nodeid, entry in data["tests"].items()}
    assert set(tests) == {"test_sleeps", "test_fails", "test_skipped"}
    sleeps = tests["test_sleeps"]
    assert set(sleeps) == {"setup", "call", "teardown", "outcome"}
    assert sleeps["setup"] >= 0.05 and sleeps["call"] >= 0.1 and sleeps["outcome"] == "passed"
    assert tests["test_fails"]["outcome"] == "failed"
    assert tests["test_skipped"]["outcome"] == "skipped"


def test_durations_go_to_the_pytest_cache_by_default(pytester):
    pytester.makeconftest('pytest_plugins = ["moxi_dev.testing"]\n')
    pytester.makepyfile(test_d="def test_one():\n    pass\n")
    pytester.runpytest()
    path = pytester.path / ".pytest_cache" / "d" / "moxi-dev" / "durations.json"
    assert list(json.loads(path.read_text())["tests"]) == ["test_d.py::test_one"]


# --- touches globs ---------------------------------------------------------------------

@pytest.mark.parametrize("glob, path, matches", [
    ("packages/turns/src/moxi_turns/claude/**", "packages/turns/src/moxi_turns/claude/x.py", True),
    ("packages/turns/src/moxi_turns/claude/**", "packages/turns/src/moxi_turns/claude/a/b.json", True),
    ("packages/turns/src/moxi_turns/claude/**", "packages/turns/src/moxi_turns/codex/x.py", False),
    ("packages/turns/src/moxi_turns/claude/**", "packages/turns/src/moxi_turns/claude.py", False),
    ("packages/*/pyproject.toml", "packages/turns/pyproject.toml", True),
    ("packages/*/pyproject.toml", "packages/turns/sub/pyproject.toml", False),
    ("packages/**/hooks.py", "packages/hooks.py", True),
    ("packages/**/hooks.py", "packages/turns/src/hooks.py", True),
    ("packages/turns/src", "packages/turns/src/moxi_turns/x.py", True),
    ("packages/turns/src", "packages/turns/srcx/y.py", False),
    ("packages/turns/x?.py", "packages/turns/x1.py", True),
    ("packages/turns/x?.py", "packages/turns/x/.py", False),
    ("packages/turns/[ab].py", "packages/turns/b.py", True),
    ("packages/turns/[!ab].py", "packages/turns/b.py", False),
    ("packages/turns/a+b.py", "packages/turns/a+b.py", True),
    ("", "packages/turns/x.py", False),
])
def test_touches_globs_read_parts(glob, path, matches):
    assert touches_match(glob, path) is matches


# --- the seam helper ---------------------------------------------------------------------

SEAM_CONFTEST = """\
pytest_plugins = ["moxi_dev.testing"]
from moxi_dev.testing import Seam

BACKEND = Seam("backend", ["mock", "local"], default="mock",
               contract=["test_contract_suite.py"])
backend = BACKEND.fixture


def pytest_generate_tests(metafunc):
    BACKEND.parametrize(metafunc)
"""


def test_a_seam_runs_its_contract_on_each_and_the_rest_on_the_default(pytester):
    pytester.makeconftest(SEAM_CONFTEST)
    pytester.makepyfile(
        test_contract_suite=(
            "import pytest\n"
            "def test_both(backend):\n    assert backend in ('mock', 'local')\n"
            "def test_no_seam():\n    pass\n"
            "@pytest.mark.parametrize('backend', ['local'])\n"
            "def test_its_own(backend):\n    assert backend == 'local'\n"),
        test_behaviour="def test_default(backend):\n    assert backend == 'mock'\n")
    result = pytester.runpytest("-p", "no:cacheprovider", "-v")
    result.assert_outcomes(passed=5)
    result.stdout.fnmatch_lines_random([
        "*test_contract_suite.py::test_both[[]mock[]] PASSED*",
        "*test_contract_suite.py::test_both[[]local[]] PASSED*",
        "*test_contract_suite.py::test_no_seam PASSED*",
        "*test_contract_suite.py::test_its_own[[]local[]] PASSED*",
        "*test_behaviour.py::test_default PASSED*"])


@pytest.mark.parametrize("impls, default, says", [
    (["mock"], "mock", "two implementations or more"),
    (["mock", "mock"], "mock", "the same id"),
    (["mock", "local"], "discord", "is not one of"),
])
def test_a_seam_refuses_a_bad_shape(impls, default, says):
    with pytest.raises(ValueError, match=says):
        testing.Seam("backend", impls, default=default, contract=[])

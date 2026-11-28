"""`moxi-dev check` through its memory scope: the outer and the inner, and `moxi-dev cap`.

The scope itself is injected (`moxi_dev.scope`'s host reading and its `Outer.run`), so
no scope is made; test_scope.py tests the mechanism, test_scope_live.py real scopes.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from moxi_dev import cap, check, scope, temp
from moxi_dev.scope import MIB, Host, Outcome
from test_check import LINE, Runs, commit_file, repo  # noqa: F401 -- the repo fixture

HOST = Host(client=258, manager=258, line_figures=True)


@pytest.fixture
def in_repo(repo, monkeypatch):
    commit_file(repo, "packages/turns/x.py")
    runs = Runs()
    monkeypatch.setattr(check, "_run", runs)
    monkeypatch.setattr(check.lint, "plan", lambda package, work, members: [])
    monkeypatch.chdir(repo)
    return runs


def last(capsys) -> str:
    return capsys.readouterr().out.strip().splitlines()[-1]


def no_outer(*args, **kwargs):
    raise AssertionError("no scope may be made here")


@pytest.mark.parametrize("why", ["CI", "not Linux", "no systemd user manager",
                                 "the user manager has no memory controller"])
def test_with_no_scope_the_check_runs_in_place_and_says_why(in_repo, monkeypatch, capsys,
                                                           why):
    monkeypatch.setattr(scope, "find_host", lambda system: why)
    monkeypatch.setattr(scope, "Outer", no_outer)
    assert check.main([]) == 0
    line = last(capsys)
    assert LINE.match(line) and f"| memory not capped ({why}) | total " in line
    assert any("pytest" in cmd for cmd, _, _ in in_repo.calls)


def test_max_none_runs_in_place_without_reading_the_host(in_repo, monkeypatch, capsys):
    monkeypatch.setenv(scope.MEMORY_MAX_ENV, "none")
    monkeypatch.setattr(scope, "find_host", no_outer)
    assert check.main([]) == 0
    assert f"| memory not capped ({scope.MEMORY_MAX_ENV} is none) |" in last(capsys)


def test_the_fenced_env_reaches_no_user_manager(in_repo, capsys):
    # The fence points XDG_RUNTIME_DIR into the test's tmp: rule 4 or 5 holds, whatever
    # the machine has.
    assert check.main([]) == 0
    assert LINE.match(line := last(capsys)), line
    assert ("| memory not capped (no systemd user manager) |" in line
            or "| memory not capped (no systemd-run) |" in line)


@pytest.mark.parametrize("name, value", [(scope.MEMORY_HIGH_ENV, "12"),
                                         (scope.MEMORY_MAX_ENV, "lots"),
                                         (scope.MEMORY_HIGH_ENV, "999999"),
                                         (scope.WORKERS_ENV, "0")])
def test_a_bad_figure_stops_the_check_with_exit_2_before_any_step(in_repo, monkeypatch,
                                                                  capsys, name, value):
    monkeypatch.setenv(name, value)
    monkeypatch.setattr(scope, "find_host", no_outer)
    assert check.main([]) == 2
    out, err = capsys.readouterr()
    assert out.strip().splitlines()[-1].endswith("exit 2") and in_repo.calls == []
    assert err.startswith(f"moxi-dev check: ") and name in err


class FakeOuter:
    made: list[FakeOuter] = []
    outcome = Outcome(0)

    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)
        FakeOuter.made.append(self)

    def run(self, env):
        self.env = dict(env)
        return FakeOuter.outcome


@pytest.fixture
def outer(monkeypatch):
    FakeOuter.made = []
    FakeOuter.outcome = Outcome(0)
    monkeypatch.setattr(scope, "find_host", lambda system: HOST)
    monkeypatch.setattr(scope, "Outer", FakeOuter)
    # 16 CPUs whatever the machine has, so MOXI_TEST_WORKERS alone sets the worker count
    # the figures follow.
    monkeypatch.setattr(check, "cpu_count", lambda: 16)
    return FakeOuter


def test_the_outer_starts_the_same_check_as_an_inner_and_prints_nothing_after_it(
        in_repo, outer, monkeypatch, capsys):
    monkeypatch.setenv(scope.WORKERS_ENV, "8")
    assert check.main(["--base", "main"]) == 0
    made = outer.made[0]
    assert made.kind == "check" and made.host == HOST
    assert made.command == [sys.executable, "-m", "moxi_dev", "check", "--base", "main"]
    assert (made.found.high_mib, made.found.max_mib) == (8000, 9536)
    assert capsys.readouterr().out == "" and in_repo.calls == []


def test_the_outer_says_a_max_below_the_default_first(in_repo, outer, monkeypatch, capsys):
    monkeypatch.setenv(scope.WORKERS_ENV, "8")
    monkeypatch.setenv(scope.MEMORY_MAX_ENV, "9000")
    outer.outcome = Outcome(1)
    assert check.main([]) == 1
    assert capsys.readouterr().out.splitlines() == [
        f"moxi-dev check: {scope.MEMORY_MAX_ENV} 9000 is below the "
        f"{scope.default_max_mib(8)} MiB the suite needs at 8 workers; expect a kill, or "
        f"lower {scope.WORKERS_ENV}"]


def test_an_end_for_memory_gives_the_outers_summary_last(in_repo, outer, capsys):
    outer.outcome = Outcome(1, "ended past its line (held 7.5G, line 7.4G, high 7.8G, "
                               "max 9.3G, peak 8.0G)")
    assert check.main([]) == 1
    line = last(capsys)
    assert LINE.match(line), line
    assert "| packages: unknown |" in line and line.endswith("exit 1")
    assert ("| memory ended past its line (held 7.5G, line 7.4G, high 7.8G, max 9.3G, "
            "peak 8.0G) | total ") in line


def test_a_callers_signal_ends_the_outer_by_it(in_repo, outer, monkeypatch, capsys):
    died = []
    monkeypatch.setattr(scope, "die_by", died.append)
    outer.outcome = Outcome(143, None, 15)
    assert check.main([]) == 143
    assert died == [15] and capsys.readouterr().out == ""


SWEPT = "removed 2 test temp dirs (3.0 GiB) left by runs that ended without cleaning up"


@pytest.fixture
def sweeps(monkeypatch):
    """The outers' sweeps, counted; each removes two dirs."""
    made: list[int] = []

    def sweep():
        made.append(1)
        return temp.Swept(count=2, size=3 * 1024 ** 3)

    monkeypatch.setattr(check, "_sweep", sweep)
    monkeypatch.setattr(cap, "_sweep", sweep)
    return made


ENDINGS = {"exit": Outcome(1, is_gone=True),
           "memory": Outcome(1, "killed at its cap (max 9.3G, peak 9.3G)", is_gone=True),
           "signal": Outcome(143, None, 15, is_gone=True)}


@pytest.mark.parametrize("ending", list(ENDINGS))
def test_the_outer_sweeps_once_its_unit_is_gone_however_the_check_ended(
        in_repo, outer, sweeps, monkeypatch, capsys, ending):
    monkeypatch.setattr(scope, "die_by", lambda signum: None)
    outer.outcome = ENDINGS[ending]
    check.main([])
    assert sweeps == [1]
    out = capsys.readouterr().out.splitlines()
    if ending == "exit":
        assert out == []            # the inner's summary stays the last line
    elif ending == "memory":
        assert out[-2] == f"moxi-dev check: {SWEPT}" and LINE.match(out[-1])
    else:
        assert out == [f"moxi-dev check: {SWEPT}"]


def test_the_outer_sweeps_nothing_while_its_unit_is_not_gone(in_repo, outer, sweeps):
    outer.outcome = Outcome(0, is_gone=False)
    check.main([])
    assert sweeps == []


def test_a_mismatched_marker_is_dropped_and_the_check_makes_its_own_scope(
        in_repo, outer, monkeypatch):
    monkeypatch.setenv(scope.SCOPE_ENV, "moxi-dev-check-000000000000.scope")
    monkeypatch.setattr(scope, "inner_unit", lambda env: None)
    check.main([])
    assert len(outer.made) == 1 and scope.SCOPE_ENV not in outer.made[0].env


def inner_cgroup(tmp_path: Path) -> Path:
    group = tmp_path / "cg"
    group.mkdir()
    (group / "memory.stat").write_text(
        f"anon {5000 * MIB}\nshmem 0\nkernel {10 * MIB}\nslab_reclaimable {10 * MIB}\n")
    (group / "memory.high").write_text(f"{8000 * MIB}\n")
    (group / "memory.max").write_text(f"{9536 * MIB}\n")
    (group / "memory.peak").write_text(f"{5877 * MIB}\n")
    (group / "memory.pressure").write_text("some avg10=0.00 avg60=0.00 avg300=0.00 "
                                           "total=400000\n")
    return group


@pytest.mark.parametrize("unit, ends", [("moxi-dev-check-0123456789ab.scope", True),
                                        ("moxi-dev-cap-0123456789ab.scope", False)])
def test_the_inner_runs_the_check_ends_its_leftovers_and_names_its_memory(
        in_repo, monkeypatch, capsys, tmp_path, unit, ends):
    group = inner_cgroup(tmp_path)
    monkeypatch.setenv(scope.SCOPE_ENV, unit)
    monkeypatch.setattr(scope, "inner_unit", lambda env: env[scope.SCOPE_ENV])
    monkeypatch.setattr(scope, "own_cgroup", lambda: group)
    monkeypatch.setattr(scope, "Outer", no_outer)
    monkeypatch.setattr(scope, "find_host", no_outer)
    leftovers = []
    monkeypatch.setattr(scope, "end_leftovers",
                        lambda cgroup_dir, keep: leftovers.append((cgroup_dir, keep)) or 2)
    assert check.main([]) == 0
    out = capsys.readouterr().out.strip().splitlines()
    assert LINE.match(out[-1]), out[-1]
    assert ("| memory peak 5.7G, held 4.9G, line 7.4G, high 7.8G, max 9.3G, "
            "stalled 0.4s | total ") in out[-1]
    if ends:
        assert leftovers == [(group, os.getpid())]
        assert out[-2] == "moxi-dev check: ended 2 processes the check left running"
    else:
        assert leftovers == []          # under `moxi-dev cap`, the cap's outer ends them
    # The marker never reaches the suites.
    assert all(scope.SCOPE_ENV not in env for cmd, _, env in in_repo.calls
               if "pytest" in cmd)


def test_the_marker_is_dropped_from_the_suites():
    assert scope.SCOPE_ENV in check.SUITE_DROPPED_ENV


# --- moxi-dev cap ----------------------------------------------------------------------

def test_cap_runs_the_command_in_its_own_scope_and_prints_its_memory_last(outer, capsys):
    outer.outcome = Outcome(3, "peak 0.8G, held 0.6G, line 7.4G, high 7.8G, max 9.3G, "
                               "stalled 0.0s")
    assert cap.main(["--", "sh", "-c", "echo $HOME; exit 3"]) == 3
    made = outer.made[0]
    assert made.kind == "cap" and made.command == ["sh", "-c", "echo $HOME; exit 3"]
    assert capsys.readouterr().out.splitlines()[-1] == (
        "moxi-dev cap: memory peak 0.8G, held 0.6G, line 7.4G, high 7.8G, max 9.3G, "
        "stalled 0.0s")


def test_cap_with_no_scope_runs_in_place_and_says_why(monkeypatch, capsys):
    monkeypatch.setattr(scope, "find_host", lambda system: "CI")
    monkeypatch.setattr(scope, "Outer", no_outer)
    assert cap.main([sys.executable, "-c", "raise SystemExit(4)"]) == 4
    assert capsys.readouterr().out.splitlines()[-1] == "moxi-dev cap: memory not capped (CI)"


def test_cap_refuses_a_bad_figure_and_no_command(monkeypatch, capsys):
    assert cap.main([]) == 2
    monkeypatch.setenv(scope.MEMORY_MAX_ENV, "1")
    assert cap.main(["true"]) == 2
    assert f"moxi-dev cap: {scope.MEMORY_MAX_ENV} must be" in capsys.readouterr().err


def test_cap_sweeps_once_its_unit_is_gone_before_its_last_line(outer, sweeps, capsys):
    outer.outcome = Outcome(0, "peak 0.8G, held 0.6G, line 7.4G, high 7.8G, max 9.3G, "
                               "stalled 0.0s", is_gone=True)
    assert cap.main(["true"]) == 0
    assert sweeps == [1]
    assert capsys.readouterr().out.splitlines()[-2:] == [
        f"moxi-dev cap: {SWEPT}",
        "moxi-dev cap: memory peak 0.8G, held 0.6G, line 7.4G, high 7.8G, max 9.3G, "
        "stalled 0.0s"]


def test_cap_sweeps_nothing_while_its_unit_is_not_gone(outer, sweeps):
    outer.outcome = Outcome(0, "peak 0.8G", is_gone=False)
    cap.main(["true"])
    assert sweeps == []


def test_cap_sweeps_on_a_callers_signal_before_it_ends_by_it(outer, sweeps, monkeypatch,
                                                            capsys):
    died = []
    monkeypatch.setattr(scope, "die_by", died.append)
    outer.outcome = Outcome(143, None, 15, is_gone=True)
    assert cap.main(["true"]) == 143
    assert sweeps == [1] and died == [15]
    assert capsys.readouterr().out.splitlines() == [f"moxi-dev cap: {SWEPT}"]


def test_cap_with_no_scope_sweeps_after_its_command(monkeypatch, sweeps, capsys):
    monkeypatch.setattr(scope, "find_host", lambda system: "not Linux")
    monkeypatch.setattr(scope, "Outer", no_outer)
    assert cap.main([sys.executable, "-c", "raise SystemExit(5)"]) == 5
    assert sweeps == [1]
    assert capsys.readouterr().out.splitlines()[-2:] == [
        f"moxi-dev cap: {SWEPT}", "moxi-dev cap: memory not capped (not Linux)"]


@pytest.mark.parametrize("scoped", [True, False])
def test_cap_gives_its_command_its_temp_on_disk(outer, monkeypatch, capsys, scoped):
    monkeypatch.setattr(temp, "places", lambda: temp.Places(platform="linux"))
    monkeypatch.setenv(temp.TEMP_ENV, temp.RAM)
    seen = []
    if not scoped:
        monkeypatch.setattr(scope, "find_host", lambda system: "CI")
        monkeypatch.setattr(cap.subprocess, "run",
                            lambda command, env: seen.append(env) or Done())
    assert cap.main(["true"]) == 0
    env = outer.made[0].env if scoped else seen[0]
    assert env[temp.TEMP_ENV] == temp.DISK
    assert capsys.readouterr().out.splitlines()[0] == (
        f"moxi-dev cap: gave the command {temp.TEMP_ENV}={temp.DISK}: ram asked; a check "
        f"keeps its temp on disk")


class Done:
    returncode = 0


def test_cap_refuses_a_bad_temp_setting(monkeypatch, capsys):
    monkeypatch.setattr(scope, "Outer", no_outer)
    monkeypatch.setenv(temp.TEMP_ENV, "tape")
    assert cap.main(["true"]) == 2
    assert capsys.readouterr().err.startswith(f"moxi-dev cap: {temp.TEMP_ENV}")

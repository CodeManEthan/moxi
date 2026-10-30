"""`moxi-dev check` picks the packages a change reaches and runs each one's suite.

These tests build tiny workspaces in git repositories under tmp and replace the step
runner, so no environment is synced and no suite runs; test_check_e2e.py runs the
real thing once.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
from pathlib import Path

import pytest

from moxi_dev import check, testing

REPO = Path(__file__).resolve().parents[3]

# The real workspace's shape: a sessions change reaches cache (a dependency) and comms
# (its dev group installs cache), and a core change reaches everything.
LAYOUT = {
    "moxi": ("moxi", [], {}, {"dev": ["pytest"]}),
    "sessions": ("moxi-sessions", ["moxi>=0.1,<0.2"], {}, {"dev": ["pytest"]}),
    "turns": ("moxi-turns", ["moxi>=0.1,<0.2"], {"codex": ["websockets>=17"]}, {}),
    "cache": ("moxi-cache", ["moxi>=0.1,<0.2", "moxi-sessions>=0.1,<0.2"], {}, {}),
    "comms": ("moxi-comms", ["moxi>=0.1,<0.2", "moxi-turns>=0.1,<0.2"],
              {"codex": ["moxi-turns[codex]>=0.1,<0.2"]},
              {"dev": ["pytest", {"include-group": "lint"}, "moxi-cache>=0.1,<0.2"],
               "lint": []}),
    "moxi-dev": ("moxi-dev", ["moxi", "pytest", "pytest-xdist"], {}, {}),
}


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com",
         "-c", "commit.gpgsign=false", *args],
        cwd=root, check=True, capture_output=True, text=True).stdout


def toml_list(items) -> str:
    out = []
    for item in items:
        if isinstance(item, dict):
            out.append("{ " + ", ".join(f'{k} = "{v}"' for k, v in item.items()) + " }")
        else:
            out.append(f'"{item}"')
    return "[" + ", ".join(out) + "]"


def write_workspace(root: Path, layout: dict) -> None:
    members = toml_list(f"packages/{d}" for d in layout)
    (root / "pyproject.toml").write_text(f"[tool.uv.workspace]\nmembers = {members}\n")
    (root / "README.md").write_text("root\n")
    for directory, (dist, deps, extras, groups) in layout.items():
        package = root / "packages" / directory
        (package / "tests").mkdir(parents=True)
        text = f'[project]\nname = "{dist}"\nversion = "0.1.0"\ndependencies = {toml_list(deps)}\n'
        if extras:
            text += "[project.optional-dependencies]\n"
            text += "".join(f"{k} = {toml_list(v)}\n" for k, v in extras.items())
        if groups:
            text += "[dependency-groups]\n"
            text += "".join(f"{k} = {toml_list(v)}\n" for k, v in groups.items())
        (package / "pyproject.toml").write_text(text)
        (package / "tests" / "test_it.py").write_text("def test_it():\n    pass\n")


@pytest.fixture
def repo(tmp_path):
    """A workspace of LAYOUT's shape, committed on main, with `work` checked out."""
    root = tmp_path / "r"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    write_workspace(root, LAYOUT)
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "start")
    git(root, "switch", "-q", "-c", "work")
    return root


def commit_file(root: Path, rel: str, text: str = "x\n") -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    git(root, "add", rel)
    git(root, "commit", "-q", "-m", f"change {rel}")


def picked(root: Path, base: str = "main") -> list[str]:
    packages = check.load_workspace(root)
    return [p.dir for p in check.pick(packages, check.changed_paths(root, base))]


class Runs:
    """The step runner's stand-in: records each command and answers with a code."""

    def __init__(self, codes=None):
        self.calls = []
        self.codes = codes or {}

    def __call__(self, cmd, cwd, env):
        self.calls.append((list(cmd), Path(cwd), dict(env)))
        for key, code in self.codes.items():
            if key(list(cmd), Path(cwd)):
                return code
        return 0

    def suites(self):
        return [(cwd.name, cmd) for cmd, cwd, _ in self.calls if "pytest" in cmd]


# --- picking ---------------------------------------------------------------------------

def test_a_sessions_change_picks_sessions_cache_and_comms(repo):
    commit_file(repo, "packages/sessions/src/moxi_sessions/x.py")
    assert picked(repo) == ["sessions", "cache", "comms"]


def test_a_core_change_picks_every_package(repo):
    commit_file(repo, "packages/moxi/src/moxi/x.py")
    assert picked(repo) == list(LAYOUT)


def test_a_turns_change_reaches_comms_but_not_cache(repo):
    commit_file(repo, "packages/turns/x.py")
    assert picked(repo) == ["turns", "comms"]


@pytest.mark.parametrize("path", ["uv.lock", "README.md", ".github/workflows/ci.yml",
                                  "packages/NOTES", "packages/gone/x.py"])
def test_a_path_outside_a_package_picks_every_package(repo, path):
    commit_file(repo, path)
    assert picked(repo) == list(LAYOUT)


def test_no_change_picks_nothing(repo):
    assert picked(repo) == []


def test_working_tree_changes_count_staged_unstaged_and_untracked(repo):
    (repo / "packages/cache/a.py").write_text("staged\n")
    git(repo, "add", "packages/cache/a.py")
    assert picked(repo) == ["cache", "comms"]
    (repo / "packages/turns/tests/test_it.py").write_text("# edited\n")   # unstaged
    assert picked(repo) == ["turns", "cache", "comms"]
    (repo / "packages/moxi-dev/new.py").write_text("untracked\n")        # untracked
    assert picked(repo) == ["turns", "cache", "comms", "moxi-dev"]


def test_ignored_files_do_not_count(repo):
    (repo / ".gitignore").write_text(".moxi-dev/\n")
    git(repo, "add", ".gitignore")
    git(repo, "commit", "-q", "-m", "ignore")
    git(repo, "switch", "-q", "main")
    git(repo, "merge", "-q", "work")
    (repo / ".moxi-dev" / "env-3.12").mkdir(parents=True)
    (repo / ".moxi-dev" / "env-3.12" / "f").write_text("x\n")
    assert picked(repo) == []


def test_only_the_commits_since_the_fork_count(repo):
    commit_file(repo, "packages/turns/x.py")
    git(repo, "switch", "-q", "main")
    commit_file(repo, "packages/moxi/later.py")     # main moved on after the fork
    git(repo, "switch", "-q", "work")
    assert picked(repo) == ["turns", "comms"]


def test_base_replaces_main(repo):
    commit_file(repo, "packages/turns/x.py")
    git(repo, "branch", "mark")
    commit_file(repo, "packages/sessions/y.py")
    assert picked(repo, base="mark") == ["sessions", "cache", "comms"]
    assert picked(repo) == ["sessions", "turns", "cache", "comms"]


def test_a_move_between_packages_picks_both(repo):
    commit_file(repo, "packages/turns/mod.py")
    git(repo, "switch", "-q", "main")
    git(repo, "merge", "-q", "work")
    git(repo, "switch", "-q", "-c", "move")
    git(repo, "mv", "packages/turns/mod.py", "packages/sessions/mod.py")
    git(repo, "commit", "-q", "-m", "move")
    assert picked(repo) == ["sessions", "turns", "cache", "comms"]


def test_odd_file_names_are_read_whole(repo):
    commit_file(repo, "packages/sessions/a name\twith tab.py")
    commit_file(repo, "packages/sessions/été.py")
    assert check.changed_paths(repo) == ["packages/sessions/a name\twith tab.py",
                                         "packages/sessions/été.py"]


def test_no_merge_base_is_a_clear_error(repo):
    with pytest.raises(check.CheckError, match="--base REV or --full"):
        check.changed_paths(repo, "no-such-branch")


def test_requirement_names_are_normalized():
    assert check.requirement_name("Moxi_Sessions[x]>=0.1; python_version>'3'") == "moxi-sessions"
    assert check.requirement_name("moxi.turns") == "moxi-turns"
    assert check.requirement_name("  ") is None


def test_the_real_workspace_picks_by_the_same_rule():
    """The rule read against this repository's own pyproject files."""
    if not (REPO / "packages" / "sessions" / "pyproject.toml").is_file():
        pytest.skip("not run from the repository (an sdist has no workspace)")
    packages = check.load_workspace(REPO)
    dirs = [p.dir for p in packages]

    def of(path):
        return [p.dir for p in check.pick(packages, [path])]

    assert {"moxi", "sessions", "turns", "cache", "comms", "moxi-dev"} <= set(dirs)
    assert set(of("packages/sessions/src/moxi_sessions/__init__.py")) == {
        "sessions", "cache", "comms"}
    assert of("packages/moxi/src/moxi/__init__.py") == dirs
    assert of("uv.lock") == dirs


# --- the steps -------------------------------------------------------------------------

def run_check(repo, runs, **kw):
    kw.setdefault("environ", {"PATH": "/usr/bin", "VIRTUAL_ENV": str(repo / ".venv")})
    kw.setdefault("plan", lambda package, work, members: [])
    return check.check(repo, run=runs, **kw)


def test_steps_run_in_order_from_the_right_places(repo):
    commit_file(repo, "packages/sessions/x.py")
    runs = Runs()
    result = run_check(repo, runs)
    assert result.code == 0
    lock, sync, *suites = runs.calls
    assert lock[0] == ["uv", "lock", "--check"] and lock[1] == repo
    env_dir = repo / ".moxi-dev" / "env-3.12"
    assert sync[0] == ["uv", "sync", "--locked", "--all-packages", "--all-extras",
                       "--all-groups", "--python", "3.12"]
    assert sync[1] == repo
    assert sync[2]["UV_PROJECT_ENVIRONMENT"] == str(env_dir)
    assert "VIRTUAL_ENV" not in lock[2] and "VIRTUAL_ENV" not in sync[2]
    assert [cwd for _, cwd, _ in suites] == [repo / "packages" / d
                                             for d in ("sessions", "cache", "comms")]
    for cmd, _, env in suites:
        assert cmd[:3] == [str(env_dir / "bin" / "python"), "-m", "pytest"]
        assert env["VIRTUAL_ENV"] == str(env_dir)
        assert env["PATH"].split(":")[0] == str(env_dir / "bin")


def test_no_step_sets_a_pytest_tmp_root_or_basetemp(repo):
    commit_file(repo, "packages/sessions/x.py")
    runs = Runs()
    run_check(repo, runs, environ={"PATH": "/usr/bin", "PYTEST_DEBUG_TEMPROOT": "/var/tmp"})
    for cmd, _, env in runs.calls:
        assert not any(arg.startswith("--basetemp") for arg in cmd)
        assert env.get("PYTEST_DEBUG_TEMPROOT") == "/var/tmp"


def test_every_suite_runs_under_an_explicit_worker_count(repo, monkeypatch):
    monkeypatch.setattr(check, "cpu_count", lambda: 6)
    runs = Runs()
    run_check(repo, runs, full=True)
    suites = runs.suites()
    assert len(suites) == len(LAYOUT)
    for _, cmd in suites:
        assert cmd[3:] == ["-n", "6"]


def test_the_worker_count_is_capped_by_the_variable(repo, monkeypatch):
    monkeypatch.setattr(check, "cpu_count", lambda: 6)
    runs = Runs()
    run_check(repo, runs, full=True, environ={"PATH": "/usr/bin", "MOXI_TEST_WORKERS": "3"})
    for _, cmd in runs.suites():
        assert cmd[3:] == ["-n", "3"]


@pytest.mark.parametrize("cpus, cap, want", [(0, "", "1"), (1, "", "1"), (2, "9", "2"),
                                             (8, "1", "1")])
def test_the_worker_count_is_never_zero(cpus, cap, want):
    environ = {"MOXI_TEST_WORKERS": cap} if cap else {}
    assert check.worker_args(environ, cpus=cpus) == ["-n", want]


def test_the_cpu_count_is_a_whole_number_of_one_or_more():
    assert check.cpu_count() >= 1


def test_the_suites_drop_what_could_make_them_serial(repo):
    runs = Runs()
    environ = {"PATH": "/usr/bin", "PYTEST_XDIST_AUTO_NUM_WORKERS": "0",
               "PYTEST_ADDOPTS": "--pdb", "PYTEST_DEBUG_TEMPROOT": "/var/tmp"}
    run_check(repo, runs, full=True, environ=environ)
    lock, sync, *suites = runs.calls
    assert suites
    for _, _, env in suites:
        assert "PYTEST_XDIST_AUTO_NUM_WORKERS" not in env and "PYTEST_ADDOPTS" not in env
        assert env["PYTEST_DEBUG_TEMPROOT"] == "/var/tmp"


@pytest.mark.parametrize("value", ["0", "-2", "two", "1.5"])
def test_a_bad_worker_cap_stops_the_check(repo, value, capsys):
    runs = Runs()
    result = run_check(repo, runs, full=True, environ={"MOXI_TEST_WORKERS": value})
    assert result.code == 2 and runs.calls == []
    assert "MOXI_TEST_WORKERS" in capsys.readouterr().err


def test_full_picks_every_package_without_reading_changes(repo):
    runs = Runs()
    result = run_check(repo, runs, full=True, base="no-such-branch")
    assert result.code == 0
    assert [name for name, _ in runs.suites()] == list(LAYOUT)


def test_a_failing_package_fails_the_check_and_the_rest_still_run(repo):
    commit_file(repo, "packages/sessions/x.py")
    runs = Runs({lambda cmd, cwd: "pytest" in cmd and cwd.name == "cache": 1})
    result = run_check(repo, runs)
    assert result.code == 1
    assert [name for name, _ in runs.suites()] == ["sessions", "cache", "comms"]
    assert [(n, ok) for n, ok, _ in result.suites] == [
        ("sessions", True), ("cache", False), ("comms", True)]


def test_no_tests_collected_is_not_a_pass(repo):
    runs = Runs({lambda cmd, cwd: "pytest" in cmd: 5})
    assert run_check(repo, runs, full=True).code == 1


def test_a_stale_lock_stops_before_the_sync(repo):
    commit_file(repo, "packages/sessions/x.py")
    runs = Runs({lambda cmd, cwd: cmd[:2] == ["uv", "lock"]: 1})
    result = run_check(repo, runs)
    assert result.code == 1 and len(runs.calls) == 1
    assert "lock fail | tags not run | sync not run | lint not run" in result.line()


def test_a_failed_sync_runs_no_suite(repo):
    runs = Runs({lambda cmd, cwd: cmd[:2] == ["uv", "sync"]: 1})
    result = run_check(repo, runs, full=True)
    assert result.code == 1 and runs.suites() == []


def test_nothing_picked_checks_the_lock_and_passes(repo):
    runs = Runs()
    result = run_check(repo, runs)
    assert result.code == 0
    assert [cmd for cmd, _, _ in runs.calls] == [["uv", "lock", "--check"]]
    assert ("packages: none | lock pass | tags pass | sync skipped | lint not run | "
            "no suites run") in result.line()


# --- the tag check and the lint -------------------------------------------------------

def test_a_tag_fails_the_check_and_the_rest_still_run(repo, capsys):
    tag = "[B5]"
    commit_file(repo, "packages/sessions/x.py", f"# see {tag}\n")
    runs = Runs()
    result = run_check(repo, runs)
    assert result.code == 1 and result.tags == "fail"
    assert [name for name, _ in runs.suites()] == ["sessions", "cache", "comms"]
    assert f"moxi-dev check: packages/sessions/x.py:1: tag {tag!r}" in capsys.readouterr().out


def test_each_lint_command_runs_in_the_check_s_environment(repo):
    commit_file(repo, "packages/sessions/x.py")
    seen = []

    def plan(package, work, members):
        seen.append((package, work, list(members)))
        return [check.lint.Command("one", ["lint-imports", "--config", "c"], ["here"]),
                check.lint.Command("two", ["deptry", "."])]

    runs = Runs()
    result = run_check(repo, runs, plan=plan,
                       environ={"PATH": "/usr/bin", "PYTHONPATH": "/stray"})
    assert result.code == 0 and "| lint pass |" in result.line()
    env_dir = repo / ".moxi-dev" / "env-3.12"
    members = [repo / "packages" / d for d in LAYOUT]
    assert seen == [(repo / "packages" / d, repo / ".moxi-dev" / "lint" / d, members)
                    for d in ("sessions", "cache", "comms")]
    linted = [(cmd, cwd, env) for cmd, cwd, env in runs.calls if "pytest" not in cmd
              and cmd[0] != "uv"]
    assert [(cmd[0], cwd.name) for cmd, cwd, _ in linted] == [
        (str(env_dir / "bin" / tool), d) for d in ("sessions", "cache", "comms")
        for tool in ("lint-imports", "deptry")]
    assert linted[0][2]["PYTHONPATH"] == "here" and "PYTHONPATH" not in linted[1][2]
    assert linted[0][2]["VIRTUAL_ENV"] == str(env_dir)
    last_lint = max(i for i, call in enumerate(runs.calls) if call in linted)
    first_suite = min(i for i, (cmd, _, _) in enumerate(runs.calls) if "pytest" in cmd)
    assert last_lint < first_suite


def test_a_lint_failure_fails_the_check_and_the_rest_still_run(repo):
    commit_file(repo, "packages/sessions/x.py")

    def plan(package, work, members):
        if package.name == "cache":
            raise check.lint.LintError("no announcement")
        return [check.lint.Command("one", ["deptry", "."])]

    runs = Runs({lambda cmd, cwd: cmd[0].endswith("deptry") and cwd.name == "comms": 1})
    result = run_check(repo, runs, plan=plan)
    assert result.code == 1
    assert "| lint fail (cache, comms) |" in result.line()
    assert [name for name, _ in runs.suites()] == ["sessions", "cache", "comms"]


def test_a_package_with_no_lint_commands_is_left_out(repo):
    commit_file(repo, "packages/sessions/x.py")
    result = run_check(repo, Runs())
    assert result.code == 0 and "| lint none |" in result.line()


def test_a_failed_sync_runs_no_lint(repo):
    runs = Runs({lambda cmd, cwd: cmd[:2] == ["uv", "sync"]: 1})
    calls = []
    result = run_check(repo, runs, full=True, plan=lambda *a: calls.append(a) or [])
    assert calls == [] and "| sync fail | lint not run |" in result.line()


def test_the_real_plan_runs_over_the_real_layout(repo):
    """Without a stand-in plan, each package whose required module has no announcement
    fails its lint with a reason, and the rest still lint."""
    commit_file(repo, "packages/sessions/x.py")
    result = check.check(repo, run=Runs(), environ={"PATH": "/usr/bin"})
    assert "| lint fail (cache, comms) |" in result.line()
    assert (repo / ".moxi-dev" / "lint" / "sessions" / "importlinter.toml").is_file()


# --- the summary line -------------------------------------------------------------------

LINE = re.compile(
    r"moxi-dev check: commit (?P<commit>[0-9a-f]{12}(\+dirty)?) \| "
    r"packages: (?P<packages>[^|]+) \| lock (pass|fail|not run) \| "
    r"tags (pass|fail|not run) \| sync (pass|fail|skipped|not run) \| "
    r"lint (?P<lint>pass|none|not run|fail \([^)]+\)) \| (?P<suites>[^|]+) \| "
    r"(?P<durations>durations none|durations \d+\.\d+s, slowest 5% \d+%, delta "
    r"(none|[+-]\d+\.\d+s vs [0-9a-f]{12}( \([^)]+\))?)) \| "
    r"total \d+\.\d+s \| exit (?P<code>\d)$")


def test_the_summary_line_names_commit_packages_results_total_and_exit(repo):
    commit_file(repo, "packages/sessions/x.py")
    runs = Runs({lambda cmd, cwd: "pytest" in cmd and cwd.name == "comms": 1})
    line = run_check(repo, runs).line()
    match = LINE.match(line)
    assert match, line
    assert match["commit"] == git(repo, "rev-parse", "--short=12", "HEAD").strip()
    assert match["packages"] == "sessions, cache, comms"
    assert re.fullmatch(r"sessions pass \d+\.\d+s, cache pass \d+\.\d+s, "
                        r"comms fail \d+\.\d+s", match["suites"])
    assert match["code"] == "1"


def test_a_dirty_tree_is_marked_on_the_commit(repo):
    (repo / "packages/turns/x.py").write_text("x\n")
    assert "+dirty |" in run_check(repo, Runs()).line()


def test_main_prints_the_line_last_and_returns_its_code(repo, monkeypatch, capsys):
    commit_file(repo, "packages/turns/x.py")
    runs = Runs({lambda cmd, cwd: "pytest" in cmd: 1})
    monkeypatch.setattr(check, "_run", runs)
    monkeypatch.chdir(repo / "packages" / "turns")
    assert check.main([]) == 1
    out = capsys.readouterr().out.splitlines()
    assert LINE.match(out[-1]) and out[-1].endswith("exit 1")


@pytest.mark.parametrize("text, why", [(None, "no readable pyproject.toml"),
                                       ("[tool.x]\n", "names no project"),
                                       ("[project\n", "no readable pyproject.toml")])
def test_a_bad_member_stops_the_check_with_a_line(repo, monkeypatch, capsys, text, why):
    pyproject = repo / "packages" / "turns" / "pyproject.toml"
    if text is None:
        pyproject.unlink()
    else:
        pyproject.write_text(text)
    runs = Runs()
    monkeypatch.setattr(check, "_run", runs)
    monkeypatch.chdir(repo)
    assert check.main(["--full"]) == 2
    out, err = capsys.readouterr()
    assert out.splitlines()[-1].endswith("exit 2") and runs.calls == []
    assert "workspace member packages/turns:" in err and why in err, err


def test_uv_missing_stops_the_check_with_a_line(repo, monkeypatch, capsys):
    bin_dir = repo.parent / "bin"            # git, and no uv
    bin_dir.mkdir()
    (bin_dir / "git").symlink_to(shutil.which("git"))
    monkeypatch.setenv("PATH", str(bin_dir))
    result = check.check(repo, full=True)
    assert result.code == 2 and result.lock == "not run"
    assert "could not run uv" in capsys.readouterr().err
    assert result.line().endswith("exit 2")


def test_main_outside_a_checkout_still_prints_a_line(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    assert check.main([]) == 2
    assert capsys.readouterr().out.strip().endswith("exit 2")


# --- what the suites run: the kit's selection ----------------------------------------------

def suite_envs(runs):
    return {cwd.name: env for cmd, cwd, env in runs.calls if "pytest" in cmd}


def test_a_picked_run_hands_the_suites_the_changed_paths(repo):
    commit_file(repo, "packages/sessions/x.py")
    (repo / "packages/cache/new file.py").write_text("x\n")
    runs = Runs()
    environ = {"PATH": "/usr/bin", testing.TIERS_ENV: "all", testing.FINDING_ENV: "L1",
               testing.CHANGED_ENV: "/elsewhere", testing.DURATIONS_ENV: "/elsewhere"}
    run_check(repo, runs, environ=environ)
    envs = suite_envs(runs)
    assert list(envs) == ["sessions", "cache", "comms"]
    for name, env in envs.items():
        assert testing.TIERS_ENV not in env and testing.FINDING_ENV not in env
        listed = Path(env[testing.CHANGED_ENV]).read_text()
        assert listed == "packages/cache/new file.py\0packages/sessions/x.py\0"
        assert env[testing.DURATIONS_ENV] == str(
            repo / ".moxi-dev" / "run" / f"{name}.durations.json")


def test_full_hands_the_suites_every_tier(repo):
    runs = Runs()
    run_check(repo, runs, full=True)
    envs = suite_envs(runs)
    assert list(envs) == list(LAYOUT)
    for env in envs.values():
        assert env[testing.TIERS_ENV] == "all" and testing.CHANGED_ENV not in env


# --- durations -----------------------------------------------------------------------------

class Timed(Runs):
    """A step runner whose suites write the kit's durations file: `seconds` maps a
    package to its tests' call seconds."""

    def __init__(self, seconds, codes=None, write=True):
        super().__init__(codes)
        self.seconds = seconds
        self.write = write

    def __call__(self, cmd, cwd, env):
        code = super().__call__(cmd, cwd, env)
        if "pytest" in cmd and self.write and Path(cwd).name in self.seconds:
            tests = {f"tests/test_it.py::test_{i}": {"setup": 0.0, "call": secs,
                                                    "teardown": 0.0, "outcome": "passed"}
                     for i, secs in enumerate(self.seconds[Path(cwd).name])}
            Path(env[testing.DURATIONS_ENV]).write_text(json.dumps({"tests": tests}))
        return code


@pytest.fixture
def ignoring(repo):
    """The repo, with the check's own directory ignored, as the real one has it."""
    (repo / ".git" / "info" / "exclude").write_text(".moxi-dev/\n")
    return repo


def records(repo):
    return repo / ".git" / "moxi-dev" / "durations"


def head(repo):
    return git(repo, "rev-parse", "HEAD").strip()


def test_the_line_gives_the_tests_seconds_and_the_slowest_share(ignoring):
    commit_file(ignoring, "packages/cache/x.py")
    # 20 tests: one of 10 s, nineteen of 0.5 s. The slowest 5 % is the one.
    runs = Timed({"cache": [10.0] + [0.5] * 9, "comms": [0.5] * 10})
    result = run_check(ignoring, runs)
    assert "| durations 19.5s, slowest 5% 51%, delta none |" in result.line()
    assert LINE.match(result.line()), result.line()


def test_the_slowest_share_counts_one_test_in_twenty_rounded_up(ignoring):
    commit_file(ignoring, "packages/cache/x.py")
    # 41 tests: 5 % is 2.05, so the slowest three count: two of 10 s and one of 4 s.
    runs = Timed({"cache": [10.0, 4.0] + [0.5] * 19, "comms": [10.0] + [0.5] * 19})
    result = run_check(ignoring, runs)
    assert result.durations.tests == 41
    assert "| durations 43.0s, slowest 5% 56%, delta none |" in result.line()
    # 40 tests: exactly two count
    runs = Timed({"cache": [10.0] + [0.5] * 19, "comms": [10.0] + [0.5] * 19})
    assert "| durations 39.0s, slowest 5% 51%, delta none |" in run_check(
        ignoring, runs).line()


def test_a_clean_run_keeps_the_commit_s_record_of_the_suites_that_passed(ignoring):
    commit_file(ignoring, "packages/cache/x.py")
    runs = Timed({"cache": [1.0], "comms": [2.0]},
                 {lambda cmd, cwd: "pytest" in cmd and cwd.name == "comms": 1})
    result = run_check(ignoring, runs)
    path = records(ignoring) / f"{head(ignoring)}.json"
    assert result.kept == path
    data = json.loads(path.read_text())
    assert data["commit"] == head(ignoring)
    assert list(data["packages"]) == ["cache"]
    assert data["packages"]["cache"]["tiers"] == "plain"
    assert data["packages"]["cache"]["tests"]["tests/test_it.py::test_0"]["call"] == 1.0


def test_a_later_run_at_the_commit_replaces_only_its_packages(ignoring):
    commit_file(ignoring, "packages/cache/x.py")
    run_check(ignoring, Timed({"cache": [1.0], "comms": [2.0]}))
    run_check(ignoring, Timed({"moxi": [3.0], "cache": [4.0]}), full=True)
    data = json.loads((records(ignoring) / f"{head(ignoring)}.json").read_text())
    assert {name: entry["tiers"] for name, entry in data["packages"].items()} == {
        "cache": "all", "comms": "plain", "moxi": "all"}


def test_a_dirty_tree_or_a_finding_keeps_no_record(ignoring):
    commit_file(ignoring, "packages/cache/x.py")
    (ignoring / "packages/cache/y.py").write_text("y\n")
    assert run_check(ignoring, Timed({"cache": [1.0]})).kept is None
    git(ignoring, "add", "-A")
    git(ignoring, "commit", "-q", "-m", "y")
    result = run_check(ignoring, Timed({"cache": [1.0]}), finding=["L3"])
    assert result.kept is None and not records(ignoring).exists()


def on_main(repo, rel, seconds, full=True):
    """Commit `rel` on main and keep a record for it there."""
    git(repo, "switch", "-q", "main")
    commit_file(repo, rel)
    result = run_check(repo, Timed(seconds), full=full)
    assert result.kept is not None
    git(repo, "switch", "-q", "work")
    git(repo, "rebase", "-q", "main")
    return git(repo, "rev-parse", "main").strip()


def test_the_delta_is_against_the_newest_ancestor_record_on_main(ignoring):
    first = on_main(ignoring, "packages/moxi/a.py", {"cache": [5.0], "comms": [5.0]})
    second = on_main(ignoring, "packages/moxi/b.py", {"cache": [3.0], "comms": [4.0]})
    # a record on a branch HEAD does not descend from is no ancestor
    git(ignoring, "switch", "-q", "-c", "side", "main")
    commit_file(ignoring, "packages/moxi/side.py")
    run_check(ignoring, Timed({"cache": [100.0], "comms": [100.0]}), full=True)
    git(ignoring, "switch", "-q", "work")
    commit_file(ignoring, "packages/cache/x.py")
    result = run_check(ignoring, Timed({"cache": [4.5], "comms": [4.0]}), full=True)
    assert first != second
    assert result.delta.commit == second and result.delta.whole
    assert result.delta.seconds == pytest.approx(1.5)
    assert f", delta +1.5s vs {second[:12]} |" in result.line()


def test_the_delta_compares_only_packages_run_the_same_way(ignoring):
    first = on_main(ignoring, "packages/moxi/a.py", {"cache": [5.0], "comms": [5.0]})
    commit_file(ignoring, "packages/cache/x.py")
    # a picked run (not --full) has no package to compare with a --full record
    assert run_check(ignoring, Timed({"cache": [1.0], "comms": [1.0]})).delta is None
    result = run_check(ignoring, Timed({"cache": [4.0], "comms": [1.0], "moxi": [1.0]}),
                       full=True)
    assert result.delta.commit == first and not result.delta.whole
    assert result.delta.packages == ("cache", "comms")
    assert f", delta -5.0s vs {first[:12]} (cache, comms) |" in result.line()


def test_head_s_own_record_is_not_its_ancestor(ignoring):
    git(ignoring, "switch", "-q", "main")
    commit_file(ignoring, "packages/moxi/a.py")
    run_check(ignoring, Timed({"moxi": [1.0]}), full=True)
    result = run_check(ignoring, Timed({"moxi": [1.0]}), full=True)
    assert result.kept is not None and result.delta is None


@pytest.mark.skipif(os.geteuid() == 0, reason="root writes into a read-only directory")
def test_a_record_the_disk_refuses_is_said_and_the_line_still_prints(
        ignoring, monkeypatch, capsys):
    commit_file(ignoring, "packages/cache/x.py")
    where = records(ignoring)
    where.mkdir(parents=True)
    where.chmod(0o500)
    monkeypatch.setattr(check, "_run", Timed({"cache": [1.0], "comms": [2.0]}))
    monkeypatch.setattr(check.lint, "plan", lambda package, work, members: [])
    monkeypatch.chdir(ignoring)
    try:
        assert check.main([]) == 0
    finally:
        where.chmod(0o700)
    out, err = capsys.readouterr()
    assert f"durations: keeping no record for {head(ignoring)[:12]}" in err
    assert "Permission denied" in err
    assert LINE.match(out.splitlines()[-1]), out
    assert "| durations 3.0s, slowest 5% 67%, delta none |" in out
    assert list(where.iterdir()) == []


def test_another_writer_s_part_file_is_left_alone(tmp_path):
    from moxi_dev import durations
    tmp_path = tmp_path / "records"
    tmp_path.mkdir()
    run = durations.PackageRun(tiers="plain", tests={"t::a": {"call": 1.0}})
    theirs = tmp_path / "abc.json.part"
    theirs.write_text("another check's half-written record\n")
    kept = durations.keep(tmp_path, "abc", {"cache": run})
    assert theirs.read_text() == "another check's half-written record\n"
    assert list(durations.read_record(kept)) == ["cache"]
    assert sorted(p.name for p in tmp_path.iterdir()) == [".lock", "abc.json",
                                                           "abc.json.part"]


def test_two_checks_keeping_one_commit_at_once_drop_neither_s_packages(tmp_path):
    from moxi_dev import durations
    tmp_path = tmp_path / "records"
    names = [f"p{i}" for i in range(16)]
    start = threading.Barrier(len(names))
    errors = []

    def keep(name):
        start.wait()
        try:
            for round_ in range(5):
                durations.keep(tmp_path, "abc", {name: durations.PackageRun(
                    tiers="plain", tests={f"t::{round_}": {"call": 1.0}})})
        except Exception as exc:  # noqa: BLE001 -- reported below
            errors.append(exc)

    threads = [threading.Thread(target=keep, args=(name,)) for name in names]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert sorted(durations.read_record(tmp_path / "abc.json")) == sorted(names)
    assert not list(tmp_path.glob("*.part"))


# --- --finding ------------------------------------------------------------------------------

def test_a_finding_runs_only_its_tests_and_a_package_without_one_is_none(ignoring):
    commit_file(ignoring, "packages/sessions/x.py")
    runs = Timed({"sessions": [1.0], "cache": [], "comms": []},
                 {lambda cmd, cwd: "pytest" in cmd and cwd.name != "sessions": 5})
    result = run_check(ignoring, runs, finding=["L50", "L51", "L50"])
    assert result.code == 0
    for env in suite_envs(runs).values():
        assert env[testing.FINDING_ENV] == "L50,L51"
    assert re.search(r"sessions pass \S+, cache none \S+, comms none \S+ \|",
                     result.line()), result.line()


def test_a_finding_no_test_carries_fails_the_check(ignoring, capsys):
    commit_file(ignoring, "packages/sessions/x.py")
    runs = Timed({"sessions": [], "cache": [], "comms": []},
                 {lambda cmd, cwd: "pytest" in cmd: 5})
    result = run_check(ignoring, runs, finding=["L50"])
    assert result.code == 1
    assert "no test in sessions, cache, comms carries L50" in capsys.readouterr().out


def test_a_finding_in_a_package_without_the_kit_fails_it(ignoring, capsys):
    commit_file(ignoring, "packages/sessions/x.py")
    result = run_check(ignoring, Timed({}, write=False), finding=["L50"])
    assert result.code == 1
    assert all(ok is False for _, ok, _ in result.suites)
    assert "sessions: its tests don't load moxi_dev.testing" in capsys.readouterr().out


@pytest.mark.parametrize("name", ["50", "l50", "L-50", "L5x", ""])
def test_a_bad_finding_stops_the_check(repo, capsys, name):
    runs = Runs()
    result = run_check(repo, runs, full=True, finding=[name])
    assert result.code == 2 and runs.calls == []
    assert "--finding takes a finding as L<n>" in capsys.readouterr().err


def test_main_reads_the_finding(repo, monkeypatch):
    seen = {}
    monkeypatch.setattr(check, "check", lambda root, **kw: seen.update(kw) or check.Result())
    monkeypatch.chdir(repo)
    check.main(["--finding", "L4", "--finding", "L9"])
    assert seen["finding"] == ["L4", "L9"]

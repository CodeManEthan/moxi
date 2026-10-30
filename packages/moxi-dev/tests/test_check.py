"""`moxi-dev check` picks the packages a change reaches and runs each one's suite.

These tests build tiny workspaces in git repositories under tmp and replace the step
runner, so no environment is synced and no suite runs; test_check_e2e.py runs the
real thing once.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from moxi_dev import check

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
        "sessions", "cache", "comms", "usage"}
    assert of("packages/moxi/src/moxi/__init__.py") == dirs
    assert of("uv.lock") == dirs


# --- the steps -------------------------------------------------------------------------

def run_check(repo, runs, **kw):
    kw.setdefault("environ", {"PATH": "/usr/bin", "VIRTUAL_ENV": str(repo / ".venv")})
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
    assert "lock fail | sync not run" in result.line()


def test_a_failed_sync_runs_no_suite(repo):
    runs = Runs({lambda cmd, cwd: cmd[:2] == ["uv", "sync"]: 1})
    result = run_check(repo, runs, full=True)
    assert result.code == 1 and runs.suites() == []


def test_nothing_picked_checks_the_lock_and_passes(repo):
    runs = Runs()
    result = run_check(repo, runs)
    assert result.code == 0
    assert [cmd for cmd, _, _ in runs.calls] == [["uv", "lock", "--check"]]
    assert "packages: none | lock pass | sync skipped | no suites run" in result.line()


# --- the summary line -------------------------------------------------------------------

LINE = re.compile(
    r"moxi-dev check: commit (?P<commit>[0-9a-f]{12}(\+dirty)?) \| "
    r"packages: (?P<packages>[^|]+) \| lock (pass|fail|not run) \| "
    r"sync (pass|fail|skipped|not run) \| (?P<suites>[^|]+) \| "
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

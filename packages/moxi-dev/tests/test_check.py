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
        "sessions", "cache", "comms"}
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
    assert "lock fail | stamps not run | web not run | sync not run" in result.line()


def test_a_failed_sync_runs_no_suite(repo):
    runs = Runs({lambda cmd, cwd: cmd[:2] == ["uv", "sync"]: 1})
    result = run_check(repo, runs, full=True)
    assert result.code == 1 and runs.suites() == []


def test_nothing_picked_checks_the_lock_and_passes(repo):
    runs = Runs()
    result = run_check(repo, runs)
    assert result.code == 0
    assert [cmd for cmd, _, _ in runs.calls] == [["uv", "lock", "--check"]]
    assert ("packages: none | lock pass | stamps skipped | web skipped | sync skipped | "
            "no suites run"
            in result.line())


# --- browser tests ---------------------------------------------------------------------

# A page-shell package whose testing extra brings Playwright, a module whose dev group
# asks for that extra, and a package with neither.
BROWSER_LAYOUT = {
    "ui": ("moxi-ui", [], {"testing": ["pytest>=8", "playwright>=1.50,<2"]},
           {"dev": ["pytest", "moxi-ui[testing]"]}),
    "mod": ("moxi-mod", [], {}, {"dev": ["pytest", "Moxi_UI[ Testing ]>=0.1"]}),
    "plain": ("moxi-plain", [], {}, {"dev": ["pytest", "moxi-ui>=0.1"]}),
}


@pytest.fixture
def browser_repo(tmp_path):
    root = tmp_path / "b"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    write_workspace(root, BROWSER_LAYOUT)
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "start")
    return root


class Probe:
    """The Chromium probe's stand-in: answers `why` (None: it starts), counting calls."""

    def __init__(self, why=None):
        self.why = why
        self.calls = []

    def __call__(self, python, env):
        self.calls.append((Path(python), dict(env)))
        return self.why


def test_a_package_drives_a_browser_when_it_asks_for_playwright_or_ui_testing(browser_repo):
    browser = {p.dir: p.browser for p in check.load_workspace(browser_repo)}
    assert browser == {"ui": True, "mod": True, "plain": False}
    assert check.requirement_extras("moxi-ui[testing, Other_X]>=1") == {"testing", "other-x"}
    assert check.requirement_extras("moxi-ui>=1") == frozenset()


def test_the_browser_tiers_are_the_plain_runs_less_the_browser():
    from moxi_dev.testing import TIERS

    assert check.WITH_BROWSER == " and ".join(f"not {t}" for t in TIERS if t != "browser")


def test_with_chromium_a_browser_package_runs_its_browser_tests_in_its_suite(browser_repo,
                                                                            monkeypatch):
    monkeypatch.setattr(check, "cpu_count", lambda: 4)
    runs, probe = Runs(), Probe()
    result = run_check(browser_repo, runs, full=True, probe=probe)
    assert result.code == 0
    suites = dict(runs.suites())
    assert suites["ui"][3:] == ["-n", "4", "-m", check.WITH_BROWSER]
    assert suites["mod"][3:] == ["-n", "4", "-m", check.WITH_BROWSER]
    assert suites["plain"][3:] == ["-n", "4"]
    # One probe, with the check's own python and the suites' environment.
    env_dir = browser_repo / ".moxi-dev" / "env-3.12"
    assert [python for python, _ in probe.calls] == [env_dir / "bin" / "python"]
    assert probe.calls[0][1]["VIRTUAL_ENV"] == str(env_dir)
    assert "without its browser tests" not in result.line()


def test_without_chromium_the_browser_tests_are_left_out_with_a_line_saying_why(
        browser_repo, capsys):
    runs, probe = Runs(), Probe("Executable doesn't exist at /x/chrome")
    result = run_check(browser_repo, runs, full=True, probe=probe)
    assert result.code == 0
    for _, cmd in runs.suites():
        assert "-m" not in cmd[3:]
    err = capsys.readouterr().err
    for name in ("ui", "mod"):
        assert (f"moxi-dev check: {name}'s browser tests are left out: Playwright's "
                f"Chromium can't start here (Executable doesn't exist at /x/chrome)") in err
    assert "plain's browser" not in err
    match = LINE.match(result.line())
    assert match, result.line()
    assert re.fullmatch(r"ui pass \d+\.\d+s without its browser tests, "
                        r"mod pass \d+\.\d+s without its browser tests, "
                        r"plain pass \d+\.\d+s", match["suites"])


def test_no_browser_package_picked_means_no_probe(browser_repo):
    git(browser_repo, "switch", "-q", "-c", "work")
    commit_file(browser_repo, "packages/plain/x.py")
    runs, probe = Runs(), Probe("not reached")
    result = run_check(browser_repo, runs, probe=probe)
    assert result.code == 0 and probe.calls == []
    assert [name for name, _ in runs.suites()] == ["plain"]


def test_a_failed_sync_runs_no_probe(browser_repo):
    runs, probe = Runs({lambda cmd, cwd: cmd[:2] == ["uv", "sync"]: 1}), Probe()
    run_check(browser_repo, runs, full=True, probe=probe)
    assert probe.calls == []


def test_the_probe_says_why_chromium_cant_start(tmp_path):
    fake = tmp_path / "python"
    fake.write_text("#!/bin/sh\necho 'Traceback (most recent call last):' >&2\n"
                    "echo \"No module named 'playwright'\" >&2\nexit 1\n")
    fake.chmod(0o755)
    assert check.chromium_probe(fake, {"PATH": "/usr/bin:/bin"}) == "No module named 'playwright'"
    good = tmp_path / "good"
    good.write_text("#!/bin/sh\nexit 0\n")
    good.chmod(0o755)
    assert check.chromium_probe(good, {"PATH": "/usr/bin:/bin"}) is None
    assert "could not run" in check.chromium_probe(tmp_path / "missing", {})


def test_the_real_workspace_runs_browser_tests_for_its_page_packages():
    if not (REPO / "packages" / "ui" / "pyproject.toml").is_file():
        pytest.skip("not run from the repository (an sdist has no workspace)")
    assert [p.dir for p in check.load_workspace(REPO) if p.browser] == ["cache", "ui"]


# --- the built web files ---------------------------------------------------------------

def _with_page(repo, monkeypatch, stamps="pass"):
    """Make the stamps step report `stamps` for the picked packages."""
    def fake(root, chosen, result):
        result.stamps = stamps
        return stamps != "fail"
    monkeypatch.setattr(check, "_stamps", fake)


def _tools(tmp_path, *names):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    for name in names:
        tool = bin_dir / name
        tool.write_text("#!/bin/sh\nexit 0\n")
        tool.chmod(0o755)
    return str(bin_dir)


def test_with_node_and_node_modules_the_built_files_are_compared(repo, monkeypatch, tmp_path):
    _with_page(repo, monkeypatch)
    (repo / "node_modules").mkdir()
    runs = Runs()
    result = run_check(repo, runs, full=True, environ={"PATH": _tools(tmp_path, "node", "npm")})
    assert result.code == 0 and "| web pass |" in result.line()
    lock, web, sync, *_ = runs.calls
    assert web[0] == ["npm", "run", "check"] and web[1] == repo


def test_a_built_file_that_differs_fails_before_the_sync(repo, monkeypatch, tmp_path):
    _with_page(repo, monkeypatch)
    (repo / "node_modules").mkdir()
    runs = Runs({lambda cmd, cwd: cmd[:2] == ["npm", "run"]: 1})
    result = run_check(repo, runs, full=True, environ={"PATH": _tools(tmp_path, "node", "npm")})
    assert result.code == 1 and runs.suites() == []
    assert "| web fail | sync not run |" in result.line()


@pytest.mark.parametrize("tools, modules, why", [
    (("npm",), True, "no node on PATH"),
    (("node", "npm"), False, "no node_modules at the root (run `npm ci`)")])
def test_without_node_the_compare_is_left_out_with_a_line(repo, monkeypatch, tmp_path, capsys,
                                                        tools, modules, why):
    _with_page(repo, monkeypatch)
    if modules:
        (repo / "node_modules").mkdir()
    runs = Runs()
    result = run_check(repo, runs, full=True, environ={"PATH": _tools(tmp_path, *tools)})
    assert result.code == 0 and "| web skipped |" in result.line()
    assert not any(cmd[0] == "npm" for cmd, _, _ in runs.calls)
    assert ("moxi-dev check: the built web files were not compared with their sources: "
            + why) in capsys.readouterr().err


def test_no_page_picked_runs_no_compare(repo, monkeypatch, tmp_path):
    _with_page(repo, monkeypatch, stamps="none")
    (repo / "node_modules").mkdir()
    runs = Runs()
    result = run_check(repo, runs, full=True, environ={"PATH": _tools(tmp_path, "node", "npm")})
    assert "| web none |" in result.line()
    assert not any(cmd[0] == "npm" for cmd, _, _ in runs.calls)


# --- the summary line -------------------------------------------------------------------

LINE = re.compile(
    r"moxi-dev check: commit (?P<commit>[0-9a-f]{12}(\+dirty)?) \| "
    r"packages: (?P<packages>[^|]+) \| lock (pass|fail|not run) \| "
    r"stamps (pass|fail|none|skipped|not run) \| "
    r"web (pass|fail|none|skipped|not run) \| "
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

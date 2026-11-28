"""`moxi-dev check` picks the packages a change reaches and runs each one's suite.

These tests build tiny workspaces in git repositories under tmp and replace the step
runner, so no environment is synced and no suite runs; test_check_e2e.py runs the
real thing once.
"""

from __future__ import annotations

import contextlib
import dataclasses
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
import tomllib
from pathlib import Path

import pytest
from moxi import proc

from moxi_dev import check, temp, testing
from moxi_dev.testing import wait_for
from test_release import METADATA, release_files

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
    """The layout's packages, each ready for the release step; the core's catalogue lists
    no module, as no package here declares one."""
    members = toml_list(f"packages/{d}" for d in layout)
    (root / "pyproject.toml").write_text(
        f"[tool.uv.workspace]\nmembers = {members}\n"
        '[tool.ruff.lint]\nselect = ["N", "A", "FBT"]\nignore = ["N818"]\n')
    (root / "README.md").write_text("root\n")
    for workspace_dir, (dist, deps, extras, groups) in layout.items():
        package = root / "packages" / workspace_dir
        (package / "tests").mkdir(parents=True)
        release_files(package)
        if dist == "moxi":
            (package / "src" / "moxi").mkdir(parents=True)
            (package / "src" / "moxi" / "catalogue.toml").write_text("version = 1\n")
        text = (f'[project]\nname = "{dist}"\nversion = "0.1.0"\n{METADATA}'
                f"dependencies = {toml_list(deps)}\n")
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
    return [p.dir for p in check.pick(packages, check.changed_paths(root, base),
                                      with_dependents=True)]


def program_path(bin_dir: Path, name: str) -> Path:
    """Where `program` writes `name`: as it is, or as `name.cmd` on Windows."""
    return bin_dir / (f"{name}.cmd" if os.name == "nt" else name)


def program(bin_dir: Path, name: str, *, code: int = 0, stderr: tuple[str, ...] = ()
            ) -> Path:
    """A stand-in program that prints `stderr`'s lines there and exits `code`: a shell
    script, or on Windows a batch file, which `shutil.which` finds by PATHEXT and a
    child process starts by its path."""
    path = program_path(bin_dir, name)
    if os.name == "nt":
        path.write_text("".join(["@echo off\n", *(f">&2 echo {line}\n" for line in stderr),
                                 f"exit /b {code}\n"]), newline="\r\n")
    else:
        path.write_text("".join(["#!/bin/sh\n", *(f"echo {shlex.quote(line)} >&2\n"
                                                   for line in stderr), f"exit {code}\n"]))
        path.chmod(0o755)
    return path


class Runs:
    """The step runner's stand-in: records each command and answers with a code."""

    def __init__(self, codes=None):
        self.calls = []
        self.codes = codes or {}
        self.bounds = []                # (cwd's name, timeout_s) of each call

    def __call__(self, cmd, cwd, env, /, *, timeout_s=None):
        self.calls.append((list(cmd), Path(cwd), dict(env)))
        self.bounds.append((Path(cwd).name, timeout_s))
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


@pytest.mark.parametrize("path", ["uv.lock", "tools/x.py", "LICENSE", "packages/NOTES",
                                  "packages/gone/x.py"])
def test_a_path_outside_a_package_picks_every_package(repo, path):
    commit_file(repo, path)
    assert picked(repo) == list(LAYOUT)


def test_the_root_pyproject_picks_every_package(repo):
    commit_file(repo, "pyproject.toml", (repo / "pyproject.toml").read_text() + "# x\n")
    assert picked(repo) == list(LAYOUT)


@pytest.mark.parametrize("path", ["README.md", "CONTRIBUTING.md",
                                  ".github/workflows/lint.yml", "docs/queue.md"])
def test_the_workflows_and_the_root_docs_pick_nothing(repo, path):
    commit_file(repo, path)
    assert picked(repo) == []
    commit_file(repo, "packages/turns/x.py")
    assert picked(repo) == ["turns", "comms"]


def test_a_check_change_picks_every_package(repo):
    commit_file(repo, "packages/moxi-dev/src/moxi_dev/x.py")
    assert picked(repo) == list(LAYOUT)


def test_pick_prints_the_picked_packages_as_one_json_line_and_runs_nothing(
        repo, monkeypatch, capsys):
    commit_file(repo, "packages/sessions/src/moxi_sessions/x.py")
    base = git(repo, "rev-parse", "main").strip()
    git(repo, "branch", "-f", "main", "HEAD")   # --base, not main, gives the changes
    runs = Runs()
    monkeypatch.setattr(check, "_run", runs)
    monkeypatch.chdir(repo / "packages" / "turns")
    assert check.main(["--pick", "--with-dependents", "--base", base]) == 0
    out = capsys.readouterr().out
    assert out.count("\n") == 1
    assert json.loads(out) == {"packages": ["sessions", "cache", "comms"]}
    assert runs.calls == []
    assert check.main(["--pick"]) == 0
    assert json.loads(capsys.readouterr().out) == {"packages": []}


def test_pick_without_the_dependents_prints_the_touched_packages(repo, monkeypatch,
                                                                 capsys):
    commit_file(repo, "packages/sessions/src/moxi_sessions/x.py")
    commit_file(repo, "uv.lock")
    runs = Runs()
    monkeypatch.setattr(check, "_run", runs)
    monkeypatch.chdir(repo)
    assert check.main(["--pick"]) == 0
    assert json.loads(capsys.readouterr().out) == {"packages": ["sessions"]}
    assert check.main(["--pick", "--with-dependents"]) == 0
    assert json.loads(capsys.readouterr().out) == {"packages": list(LAYOUT)}
    assert runs.calls == []


def test_pick_with_no_merge_base_exits_2_printing_nothing(repo, monkeypatch, capsys):
    monkeypatch.chdir(repo)
    assert check.main(["--pick", "--base", "no-such-rev"]) == 2
    out, err = capsys.readouterr()
    assert out == "" and "no merge base of no-such-rev" in err


@pytest.mark.parametrize("args", [["--full"], ["--finding", "L1"], ["--static"],
                                  ["--only", "refs"]])
def test_pick_takes_no_other_mode(repo, monkeypatch, args):
    monkeypatch.chdir(repo)
    with pytest.raises(SystemExit) as stop:
        check.main(["--pick", *args])
    assert stop.value.code == 2


@pytest.mark.parametrize("args", [["--full"], ["--static"], ["--only", "refs"]])
def test_with_dependents_takes_no_mode_that_picks_every_package_or_none(
        repo, monkeypatch, capsys, args):
    runs = Runs()
    monkeypatch.setattr(check, "_run", runs)
    monkeypatch.chdir(repo)
    with pytest.raises(SystemExit) as stop:
        check.main(["--with-dependents", *args])
    assert stop.value.code == 2
    assert "--with-dependents cannot be combined" in capsys.readouterr().err
    assert runs.calls == []


def test_the_check_without_the_dependents_runs_the_touched_packages_alone(ignoring):
    commit_file(ignoring, "packages/sessions/x.py")
    runs = Runs()
    result = run_check(ignoring, runs, with_dependents=False)
    assert result.packages == ["sessions"]
    assert [name for name, _ in runs.suites()] == ["sessions"]
    runs = Runs()
    result = run_check(ignoring, runs, with_dependents=True)
    assert result.packages == ["sessions", "cache", "comms"]
    assert [name for name, _ in runs.suites()] == ["sessions", "cache", "comms"]


def test_main_passes_with_dependents_to_the_check(repo, monkeypatch):
    seen = []
    monkeypatch.setattr(check, "check", lambda root, **kw: seen.append(kw) or check.Result())
    monkeypatch.chdir(repo)
    check.main(["--with-dependents", "--base", "main"])
    check.main([])
    assert [kw["with_dependents"] for kw in seen] == [True, False]


# The real workspace's dev groups: every package lists moxi-dev, which requires moxi and
# publishing; connector's dev group takes ui, whose pages its browser tests drive.
DEV_LAYOUT = {
    "moxi": ("moxi", [], {}, {"dev": ["pytest", "moxi-dev"]}),
    "publishing": ("moxi-publishing", ["moxi>=0.1,<0.2"], {}, {"dev": ["moxi-dev"]}),
    "turns": ("moxi-turns", ["moxi>=0.1,<0.2"], {}, {"dev": ["moxi-dev"]}),
    "comms": ("moxi-comms", ["moxi-turns>=0.1,<0.2"], {}, {"dev": ["moxi-dev"]}),
    "sessions": ("moxi-sessions", ["moxi>=0.1,<0.2"], {}, {"dev": ["moxi-dev"]}),
    "ui": ("moxi-ui", ["moxi>=0.1,<0.2"], {"testing": ["pytest"]}, {"dev": ["moxi-dev"]}),
    "connector": ("moxi-connector", ["moxi>=0.1,<0.2"], {},
                  {"dev": ["moxi-dev", "moxi-ui[testing]"]}),
    "moxi-dev": ("moxi-dev", ["moxi", "moxi-publishing", "pytest"], {}, {}),
}


@pytest.fixture
def dev_repo(tmp_path):
    root = tmp_path / "d"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    write_workspace(root, DEV_LAYOUT)
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "start")
    git(root, "switch", "-q", "-c", "work")
    return root


def test_picking_ignores_the_edges_to_the_check(dev_repo):
    commit_file(dev_repo, "packages/turns/src/moxi_turns/x.py")
    assert picked(dev_repo) == ["turns", "comms"]


def test_a_change_the_check_requires_picks_the_check_not_everything(dev_repo):
    commit_file(dev_repo, "packages/publishing/x.py")
    assert picked(dev_repo) == ["publishing", "moxi-dev"]


def test_another_dev_group_edge_still_picks(dev_repo):
    commit_file(dev_repo, "packages/ui/x.py")
    assert picked(dev_repo) == ["ui", "connector"]


def test_with_every_dev_group_naming_the_check_its_change_picks_every_package(dev_repo):
    commit_file(dev_repo, "packages/moxi-dev/x.py")
    assert picked(dev_repo) == list(DEV_LAYOUT)
    git(dev_repo, "reset", "-q", "--hard", "main")
    commit_file(dev_repo, "packages/moxi/x.py")
    assert picked(dev_repo) == list(DEV_LAYOUT)


@pytest.mark.parametrize("path", [".github/workflows/publishing.yml",
                                  ".github/workflows/release.yml"])
def test_a_workflow_publishings_tests_read_picks_publishing(dev_repo, path):
    commit_file(dev_repo, path)
    assert picked(dev_repo) == ["publishing", "moxi-dev"]


@pytest.mark.parametrize("path", [".github/workflows/ci.yml",
                                  ".github/workflows/queue.yml"])
def test_a_workflow_the_checks_tests_read_picks_the_check_alone(dev_repo, path):
    commit_file(dev_repo, path)
    assert picked(dev_repo) == ["moxi-dev"]


def test_another_github_path_picks_nothing(dev_repo):
    commit_file(dev_repo, ".github/workflows/other.yml")
    commit_file(dev_repo, ".github/dependabot.yml")
    assert picked(dev_repo) == []


def test_the_rename_table_picks_the_check_alone(dev_repo):
    commit_file(dev_repo, "packages/moxi-dev/renames.tsv", "a\tb\n")
    assert picked(dev_repo) == ["moxi-dev"]


def test_no_change_picks_nothing(repo):
    assert picked(repo) == []


def test_working_tree_changes_count_staged_unstaged_and_untracked(repo):
    (repo / "packages/cache/a.py").write_text("staged\n")
    git(repo, "add", "packages/cache/a.py")
    assert picked(repo) == ["cache", "comms"]
    (repo / "packages/turns/tests/test_it.py").write_text("# edited\n")   # unstaged
    assert picked(repo) == ["turns", "cache", "comms"]
    (repo / "packages/sessions/new.py").write_text("untracked\n")        # untracked
    assert picked(repo) == ["sessions", "turns", "cache", "comms"]


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
    # Windows refuses a tab in a file name; there the name keeps its spaces only.
    odd = "a name with spaces.py" if os.name == "nt" else "a name\twith tab.py"
    commit_file(repo, f"packages/sessions/{odd}")
    commit_file(repo, "packages/sessions/été.py")
    assert check.changed_paths(repo) == [f"packages/sessions/{odd}",
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
        return [p.dir for p in check.pick(packages, [path], with_dependents=True)]

    assert {"moxi", "sessions", "turns", "cache", "comms", "moxi-dev"} <= set(dirs)
    # A root path a package's tests read picks that package; the rest of .github none.
    assert "memory" in of("README.md") and "publishing" not in of("README.md")
    assert "publishing" in of(".github/workflows/release.yml")
    assert of(".github/workflows/ci.yml") == of(".github/workflows/queue.yml") == [
        "moxi-dev"]
    assert of(".github/workflows/other.yml") == []
    # cache depends on sessions, comms on cache through an extra, the launcher on comms,
    # and usage and memory on sessions directly.
    assert set(of("packages/sessions/src/moxi_sessions/__init__.py")) == {
        "sessions", "cache", "comms", "launcher", "usage", "memory", "rounds"}
    assert of("packages/moxi/src/moxi/__init__.py") == dirs
    assert of("packages/moxi-dev/src/moxi_dev/check.py") == dirs
    assert of("uv.lock") == dirs
    # Every package lists moxi-dev in its dev group, so only the edges to it would have
    # carried these to every package.
    assert len(of("packages/turns/src/moxi_turns/__init__.py")) < len(dirs)
    assert len(of("packages/publishing/src/moxi_publishing/__init__.py")) < len(dirs)
    assert "connector" in of("packages/ui/src/moxi_ui/__init__.py")


EVERY = "every package"


def closure_of(dirs: list[str]) -> str:
    """A table cell: the dependents' pick of a change inside these packages."""
    return "closure of " + ",".join(dirs)


# Each row: the changed paths, the touched pick, the dependents' pick.
PICK_TABLE = [
    (["uv.lock"], [], EVERY),
    (["pyproject.toml"], ["moxi", "moxi-dev"], EVERY),
    (["LICENSE"], ["moxi-dev"], EVERY),
    ([".gitignore"], ["moxi-dev"], EVERY),
    ([".gitattributes"], ["moxi-dev"], EVERY),
    ([".public-paths"], ["moxi-dev"], EVERY),
    ([".refs-allow"], ["moxi-dev"], EVERY),
    ([".residue-allow"], ["moxi-dev"], EVERY),
    (["SECURITY.md"], [], EVERY),
    (["package.json"], EVERY, EVERY),
    (["tools/web/build.mjs"], EVERY, EVERY),
    (["a-root-file-no-table-lists.txt"], EVERY, EVERY),
    (["packages/moxi/src/moxi/x.py"], ["moxi"], closure_of(["moxi"])),
    (["packages/moxi-dev/src/moxi_dev/x.py"], ["moxi-dev"], EVERY),
    (["packages/moxi-dev/renames.tsv"], ["moxi-dev"], ["moxi-dev"]),
    (["README.md"], ["memory"], closure_of(["memory"])),
    ([".github/workflows/queue.yml"], ["moxi-dev"], ["moxi-dev"]),
    (["packages/memory/src/moxi_memory/x.py", "packages/launcher/src/moxi_launcher/x.py",
      "uv.lock"], ["launcher", "memory"], EVERY),
]


def _real_workspace() -> list[check.Package]:
    if not (REPO / "packages" / "sessions" / "pyproject.toml").is_file():
        pytest.skip("not run from the repository (an sdist has no workspace)")
    return check.load_workspace(REPO)


def _cell(packages, want) -> list[str]:
    dirs = [package.dir for package in packages]
    if want == EVERY:
        return dirs
    if isinstance(want, str):
        members = want.removeprefix("closure of ").split(",")
        paths = [f"packages/{member}/src/x.py" for member in members]
        return [p.dir for p in check.pick(packages, paths, with_dependents=True)]
    return [one for one in dirs if one in want]


@pytest.mark.parametrize(("paths", "touched", "wide"), PICK_TABLE,
                         ids=[" ".join(row[0]) for row in PICK_TABLE])
def test_the_real_workspace_picks_by_both_rules(paths, touched, wide):
    packages = _real_workspace()
    assert [p.dir for p in check.pick(packages, paths, with_dependents=False)] == _cell(
        packages, touched)
    assert [p.dir for p in check.pick(packages, paths, with_dependents=True)] == _cell(
        packages, wide)


def test_the_table_has_a_row_for_every_root_reader():
    rows = {paths[0] for paths, _, _ in PICK_TABLE if len(paths) == 1}
    assert set(check.ROOT_READERS) <= rows


# --- the steps -------------------------------------------------------------------------

def run_check(repo, runs, **kw):
    """The check over `repo` with `runs` as its step runner. The steps' tests pick as CI
    does, with the dependents, so a change in one package runs several suites; the
    touched pick has tests of its own."""
    kw.setdefault("env", {"PATH": "/usr/bin", "VIRTUAL_ENV": str(repo / ".venv")})
    kw.setdefault("plan", lambda package, work, members: [])
    kw.setdefault("with_dependents", True)
    return check.check(repo, run=runs, **kw)


STATIC_STOP = ("moxi-dev check: a static step failed, so the check stops before the "
               "packages' steps\n")
LINT_STOP = "moxi-dev check: a package's import rules failed, so the check runs no tests\n"
NOT_REACHED = ("| stamps not run | web not run | sync not run | lint not run | "
               "no suites run | timed not run |")


def test_code_check_failures_are_recorded_and_stop_the_check(repo, monkeypatch, capsys):
    commit_file(repo, "packages/sessions/x.py")
    monkeypatch.setattr(check.refs, "scan", lambda root: [
        check.refs.Hit("packages/sessions/x.py", 1, "Gone")])
    monkeypatch.setattr(check.naming, "scan", lambda root: [
        check.naming.Hit("packages/sessions/x.py", 1, "MX1", "unit", "bad", "fix")])
    monkeypatch.setattr(check.ruff_check, "scan", lambda root: [
        check.ruff_check.Hit("packages/sessions/x.py", 1, 1, "N802", "bad name")])
    runs = Runs()
    result = run_check(repo, runs)
    assert (result.code == 1 and result.refs == "fail" and result.names == "fail"
            and result.ruff == "fail")
    # Every static step ran, then the check stopped: no stamps, sync, lint or suite.
    assert result.release == "pass" and result.machine == "pass"
    assert [cmd for cmd, _, _ in runs.calls] == [["uv", "lock", "--check"]]
    assert NOT_REACHED in result.line() and LINE.match(result.line())
    assert capsys.readouterr().out.endswith(STATIC_STOP)


def test_under_full_a_static_failure_still_runs_every_suite(repo, monkeypatch, capsys):
    monkeypatch.setattr(check.refs, "scan", lambda root: [
        check.refs.Hit("packages/sessions/x.py", 1, "Gone")])
    runs = Runs()
    result = run_check(repo, runs, full=True)
    assert result.code == 1 and result.refs == "fail" and result.sync == "pass"
    assert [name for name, _ in runs.suites()] == list(LAYOUT)
    assert STATIC_STOP not in capsys.readouterr().out


def test_with_nothing_picked_a_static_failure_reads_as_before(repo, monkeypatch, capsys):
    monkeypatch.setattr(check.refs, "scan", lambda root: [
        check.refs.Hit("README.md", 1, "Gone")])
    runs = Runs()
    result = run_check(repo, runs, with_dependents=False)
    assert result.code == 1 and result.refs == "fail"
    assert ("packages: none | lock pass | tags pass | refs fail" in result.line()
            and "| stamps skipped | web skipped | sync skipped | lint not run | "
                "no suites run |" in result.line())
    assert STATIC_STOP not in capsys.readouterr().out


def test_only_runs_one_scanner_reads_the_whole_tree_and_filters_printing(tmp_path,
                                                                         monkeypatch,
                                                                         capsys):
    root = tmp_path / "root"
    root.mkdir()
    seen = []
    monkeypatch.setattr(check.refs, "scan", lambda given: seen.append(given) or [
        check.refs.Hit("packages/a/x.py", 1, "A"),
        check.refs.Hit("packages/b/y.py", 2, "B")])
    monkeypatch.setattr(check.naming, "scan", lambda root: pytest.fail("names ran"))
    assert check.only(root, "refs", ["packages/a"]) == 1
    out = capsys.readouterr().out
    assert "packages/a/x.py" in out and "packages/b/y.py" not in out
    assert seen == [root]


def test_only_ruff_runs_the_real_binary_and_filters_printing(tmp_path, monkeypatch, capsys):
    root = tmp_path / "ruff"
    root.mkdir()
    git(root, "init", "-q")
    (root / "pyproject.toml").write_text('''
[tool.ruff.lint]
select = ["N", "A", "FBT"]
ignore = ["N818"]
external = ["MX"]
''')
    for package in ("a", "b"):
        path = root / "packages" / package
        path.mkdir(parents=True)
        (path / "one.py").write_text("def choose(flag: bool):\n    return flag\n")
    git(root, "add", "-A")
    monkeypatch.chdir(root)
    assert check.main(["--only", "ruff", "packages/a"]) == 1
    out = capsys.readouterr().out
    assert "packages/a/one.py:1: FBT001: ruff:" in out
    assert "packages/b/one.py" not in out


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
        assert cmd[:3] == [str(check.venv_program(env_dir, "python")), "-m", "pytest"]
        assert env["VIRTUAL_ENV"] == str(env_dir)
        assert env["PATH"].split(os.pathsep)[0] == str(check.venv_bin(env_dir))


def test_no_step_sets_a_pytest_tmp_root_or_basetemp(repo):
    commit_file(repo, "packages/sessions/x.py")
    runs = Runs()
    run_check(repo, runs, env={"PATH": "/usr/bin", "PYTEST_DEBUG_TEMPROOT": "/var/tmp"})
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
    run_check(repo, runs, full=True, env={"PATH": "/usr/bin", "MOXI_TEST_WORKERS": "3"})
    for _, cmd in runs.suites():
        assert cmd[3:] == ["-n", "3"]


@pytest.mark.parametrize("cpus, cap, want", [(0, "", "1"), (1, "", "1"), (2, "9", "2"),
                                             (8, "1", "1")])
def test_the_worker_count_is_never_zero(cpus, cap, want):
    env = {"MOXI_TEST_WORKERS": cap} if cap else {}
    assert check.worker_args(env, cpus=cpus) == ["-n", want]


def test_the_cpu_count_is_a_whole_number_of_one_or_more():
    assert check.cpu_count() >= 1


def test_the_suites_drop_what_could_make_them_serial(repo):
    runs = Runs()
    given_env = {"PATH": "/usr/bin", "PYTEST_XDIST_AUTO_NUM_WORKERS": "0",
               "PYTEST_ADDOPTS": "--pdb", "PYTEST_DEBUG_TEMPROOT": "/var/tmp"}
    run_check(repo, runs, full=True, env=given_env)
    lock, sync, *suites = runs.calls
    assert suites
    for _, _, env in suites:
        assert "PYTEST_XDIST_AUTO_NUM_WORKERS" not in env and "PYTEST_ADDOPTS" not in env
        assert env["PYTEST_DEBUG_TEMPROOT"] == "/var/tmp"


def test_each_suite_and_nothing_else_runs_under_the_default_bound(repo):
    runs = Runs()
    run_check(repo, runs, full=True)
    assert [(name, bound) for name, bound in runs.bounds if bound is not None] == [
        (name, check.SUITE_BOUND_S) for name in LAYOUT]
    assert [cmd for (cmd, _, _), (_, bound) in zip(runs.calls, runs.bounds)
            if bound is None] == [cmd for cmd, _, _ in runs.calls if "pytest" not in cmd]
    assert check.SUITE_BOUND_S <= 900


def test_the_variable_sets_the_bound(repo):
    runs = Runs()
    run_check(repo, runs, full=True, env={"PATH": "/usr/bin",
                                          "MOXI_TEST_SUITE_BOUND_S": "1200"})
    assert {bound for _, bound in runs.bounds if bound is not None} == {1200.0}


@pytest.mark.parametrize("value", ["0", "-5", "ten", "nan", "inf"])
def test_a_bad_bound_stops_the_check(repo, value, capsys):
    runs = Runs()
    result = run_check(repo, runs, full=True, env={"MOXI_TEST_SUITE_BOUND_S": value})
    assert result.code == 2 and runs.calls == []
    assert "MOXI_TEST_SUITE_BOUND_S" in capsys.readouterr().err


def test_each_suite_is_told_where_and_when_to_write_its_stacks_and_an_in_time_run_keeps_none(
        repo):
    stale = repo / check.RUN_DIR / "cache.stacks"
    stale.mkdir(parents=True)
    (stale / "main-1.txt").write_text("old\n")
    runs = Runs()
    before = time.time()
    run_check(repo, runs, full=True, env={"PATH": "/usr/bin",
                                          "MOXI_TEST_SUITE_BOUND_S": "100"})
    for cmd, cwd, env in runs.calls:
        if "pytest" in cmd:
            assert env[testing.STACKS_ENV] == str(repo / check.RUN_DIR / f"{cwd.name}.stacks")
            assert env[testing.PARENT_PID_ENV] == str(os.getpid())
            at = float(env[testing.STACKS_AT_ENV])
            assert before + 90 - 1 <= at <= time.time() + 90 + 1
    assert not stale.exists()


def test_a_short_bound_writes_the_stacks_at_half_of_it(repo):
    runs = Runs()
    before = time.time()
    run_check(repo, runs, full=True, env={"PATH": "/usr/bin",
                                          "MOXI_TEST_SUITE_BOUND_S": "6"})
    ats = [float(env[testing.STACKS_AT_ENV]) for cmd, _, env in runs.calls if "pytest" in cmd]
    assert ats and all(before + 3 - 1 <= at <= time.time() + 3 + 1 for at in ats)


#: A package run that never ends: it starts a child that sleeps, notes the child's pid in
#: the file its argument names, writes one process's stacks and one empty file where the
#: kit would, and sleeps.
HANG = """\
import os, subprocess, sys, time
from pathlib import Path
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
Path(sys.argv[1]).write_text(str(child.pid))
stacks = Path(os.environ["MOXI_TEST_STACKS"])
stacks.mkdir(parents=True, exist_ok=True)
(stacks / "main-1.txt").write_text("Thread 0x1 (most recent call first):\\n")
(stacks / "gw0-2.txt").write_text("")
time.sleep(120)
"""


def _running(pid: int) -> bool:
    found = proc.identity(pid)
    return found is not None and proc.alive(found)


def test_a_package_run_past_its_bound_is_ended_with_its_tree_and_fails_alone(
        repo, tmp_path, capsys):
    pid_file = tmp_path / "child.pid"
    runs = Runs()

    def run(cmd, cwd, env, /, *, timeout_s=None):
        runs(cmd, cwd, env, timeout_s=timeout_s)
        if "pytest" in cmd and Path(cwd).name == "cache":
            return check._run([sys.executable, "-c", HANG, str(pid_file)], cwd, env,
                              timeout_s=timeout_s)
        return 0

    began = time.monotonic()
    result = run_check(repo, run, full=True,
                       env={**os.environ, "MOXI_TEST_SUITE_BOUND_S": "4"})
    took = time.monotonic() - began
    assert result.code == 1
    assert [(n, ok) for n, ok, _ in result.suites] == [
        (name, name != "cache") for name in LAYOUT]
    assert result.cut == ["cache"]
    assert re.search(r"\| moxi pass [\d.]+s, sessions pass [\d.]+s, turns pass [\d.]+s, "
                     r"cache fail [\d.]+s ended at its bound, comms pass", result.line())
    assert 4 <= took < 4 + 30
    stacks = repo / check.RUN_DIR / "cache.stacks"
    out = capsys.readouterr().out
    assert (f"moxi-dev check: cache: its tests ran past their 4 s bound, so the check "
            f"ended the run and every process under it and fails the package; the one "
            f"process still running then wrote its stacks to {stacks}") in out
    assert [path.name for path in stacks.iterdir()] == ["main-1.txt"]
    child = int(pid_file.read_text())
    wait_for(lambda: not _running(child), testing.PROC_WAIT_S,
             what="the run's child to be ended")


#: A check whose cache package's run is a process that starts a sleeping child, notes
#: both pids in the file its second argument names, and sleeps.
SIGNALLED = """\
import os, sys
from pathlib import Path
from moxi_dev import check

TREE = (
    "import subprocess, sys, time, os\\n"
    "from pathlib import Path\\n"
    "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\\n"
    "Path(sys.argv[1]).write_text(f'{os.getpid()} {child.pid}')\\n"
    "time.sleep(120)\\n")


def run(cmd, cwd, env, /, *, timeout_s=None):
    if "pytest" in cmd and Path(cwd).name == "cache":
        return check._run([sys.executable, "-c", TREE, sys.argv[2]], cwd, env,
                          timeout_s=timeout_s)
    return 0


if len(sys.argv) > 3:
    import signal
    signal.signal(getattr(signal, sys.argv[3]), signal.SIG_IGN)
result = check.check(Path(sys.argv[1]), run=run, full=True, env=dict(os.environ),
                     plan=lambda package, work, members: [])
sys.exit(result.code)
"""


@pytest.mark.skipif(sys.platform == "win32",
                    reason="Windows has no SIGTERM to send: os.kill there ends the process "
                           "outright, which the test kit's parent watch covers")
@pytest.mark.parametrize("name", ["SIGTERM", "SIGHUP"])
def test_a_check_ended_by_a_signal_ends_the_package_run_under_way(repo, tmp_path, name):
    import signal

    pids_file = tmp_path / "pids"
    checker = subprocess.Popen([sys.executable, "-c", SIGNALLED, str(repo), str(pids_file)],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        wait_for(lambda: pids_file.exists() and " " in pids_file.read_text(),
                 testing.PROC_WAIT_S, what="the package run to start")
        run_pid, child = (int(one) for one in pids_file.read_text().split())
        signum = getattr(signal, name)
        checker.send_signal(signum)
        assert checker.wait(timeout=testing.PROC_WAIT_S) == 128 + signum
        wait_for(lambda: not _running(run_pid) and not _running(child), testing.PROC_WAIT_S,
                 what="the package run and its child to be ended")
    finally:
        checker.kill()
        checker.wait()
        for pid in (pids_file.read_text().split() if pids_file.exists() else ()):
            with contextlib.suppress(OSError):
                os.kill(int(pid), signal.SIGKILL)


def test_the_check_puts_back_the_signal_handlers_it_found(repo):
    import signal

    before = {one: signal.getsignal(one) for one in check.END_SIGNALS}
    run_check(repo, Runs(), full=True)
    assert {one: signal.getsignal(one) for one in check.END_SIGNALS} == before


@pytest.mark.skipif(sys.platform == "win32", reason="Windows has no SIGHUP")
def test_a_check_started_ignoring_sighup_survives_it_and_its_run_goes_on(repo, tmp_path):
    import signal

    pids_file = tmp_path / "pids"
    checker = subprocess.Popen(
        [sys.executable, "-c", SIGNALLED, str(repo), str(pids_file), "SIGHUP"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        wait_for(lambda: pids_file.exists() and " " in pids_file.read_text(),
                 testing.PROC_WAIT_S, what="the package run to start")
        run_pid, child = (int(one) for one in pids_file.read_text().split())
        checker.send_signal(signal.SIGHUP)
        with pytest.raises(subprocess.TimeoutExpired):
            checker.wait(timeout=1)
        assert _running(run_pid) and _running(child)
        checker.send_signal(signal.SIGTERM)
        assert checker.wait(timeout=testing.PROC_WAIT_S) == 128 + signal.SIGTERM
        wait_for(lambda: not _running(run_pid) and not _running(child), testing.PROC_WAIT_S,
                 what="the package run and its child to be ended")
    finally:
        checker.kill()
        checker.wait()
        for pid in (pids_file.read_text().split() if pids_file.exists() else ()):
            with contextlib.suppress(OSError):
                os.kill(int(pid), signal.SIGKILL)


def test_the_check_leaves_a_handler_python_did_not_install_in_place(repo, monkeypatch):
    import signal

    foreign = check.END_SIGNALS[0]
    getsignal, install = signal.getsignal, signal.signal
    installed = []

    def recording(signum, handler):                       # as Python's own does for one
        installed.append(signum)                          # it did not install: None back
        was = install(signum, handler)
        return None if signum == foreign else was

    monkeypatch.setattr(signal, "getsignal",
                        lambda signum: None if signum == foreign else getsignal(signum))
    monkeypatch.setattr(signal, "signal", recording)
    before = {one: getsignal(one) for one in check.END_SIGNALS}
    run_check(repo, Runs(), full=True)
    assert foreign not in installed
    assert {one: getsignal(one) for one in check.END_SIGNALS} == before


@pytest.mark.parametrize(("written", "said"), [
    (0, "none of its processes wrote its stacks"),
    (1, "the one process still running then wrote its stacks to {where}"),
    (3, "the 3 processes still running then wrote their stacks to {where}"),
])
def test_the_cut_line_says_how_many_processes_wrote_their_stacks(tmp_path, written, said):
    where = tmp_path / "cache.stacks"
    assert check.stacks_said(written, where) == said.format(where=where)


def test_a_run_ended_at_its_bound_with_no_stacks_says_so(repo, capsys):
    def run(cmd, cwd, env, /, *, timeout_s=None):
        if "pytest" in cmd and Path(cwd).name == "turns":
            raise subprocess.TimeoutExpired(cmd, timeout_s)
        return 0

    result = run_check(repo, run, full=True)
    assert result.code == 1 and result.cut == ["turns"]
    assert ("moxi-dev check: turns: its tests ran past their 900 s bound, so the check "
            "ended the run and every process under it and fails the package; none of its "
            "processes wrote its stacks") in capsys.readouterr().out


@pytest.mark.parametrize("value", ["0", "-2", "two", "1.5"])
def test_a_bad_worker_cap_stops_the_check(repo, value, capsys):
    runs = Runs()
    result = run_check(repo, runs, full=True, env={"MOXI_TEST_WORKERS": value})
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
    assert ("lock fail | tags not run | refs not run | names not run | ruff not run | "
            "renames not run | release not run | pronouns not run | machine not run | "
            "stamps not run | web not run | sync not run | lint not run") in result.line()


def test_a_failed_sync_runs_no_suite(repo):
    runs = Runs({lambda cmd, cwd: cmd[:2] == ["uv", "sync"]: 1})
    result = run_check(repo, runs, full=True)
    assert result.code == 1 and runs.suites() == []


def test_nothing_picked_checks_the_lock_and_passes(repo):
    runs = Runs()
    result = run_check(repo, runs)
    assert result.code == 0
    assert [cmd for cmd, _, _ in runs.calls] == [["uv", "lock", "--check"]]
    assert ("packages: none | lock pass | tags pass | refs pass | names pass | ruff pass | "
            "renames none | release pass | pronouns pass | machine pass "
            "| stamps skipped | web skipped | sync skipped | lint not run | no suites run") \
        in result.line()


# --- the tag check and the lint -------------------------------------------------------

def test_a_tag_fails_the_check_and_stops_it_before_the_packages(repo, capsys):
    tag = "[B5]"
    commit_file(repo, "packages/sessions/x.py", f"# see {tag}\n")
    runs = Runs()
    result = run_check(repo, runs)
    assert result.code == 1 and result.tags == "fail" and result.refs == "pass"
    assert runs.suites() == [] and NOT_REACHED in result.line()
    out = capsys.readouterr().out
    assert f"moxi-dev check: packages/sessions/x.py:1: tag {tag!r}" in out
    assert out.endswith(STATIC_STOP)



def test_the_release_step_runs_whatever_the_change_picks(repo, capsys):
    # A CHANGELOG behind its version, committed: no change since `--base HEAD` picks turns.
    commit_file(repo, "packages/turns/CHANGELOG.md", "# Changelog\n\n## 0.0.9\n")
    runs = Runs()
    result = run_check(repo, runs, base="HEAD")
    assert result.code == 1 and result.release == "fail"
    assert ("| packages: none | lock pass | tags pass | refs pass | names pass | ruff pass | "
            "renames none | release fail |") in result.line()
    assert ("moxi-dev check: packages/turns: CHANGELOG.md: its first version heading is "
            "0.0.9, but project.version is 0.1.0;") in capsys.readouterr().out


def test_a_release_problem_fails_the_check_and_stops_it_before_the_packages(repo, capsys):
    commit_file(repo, "packages/sessions/README.md", "See [the guide](docs/guide.md).\n")
    runs = Runs()
    result = run_check(repo, runs)
    assert result.code == 1 and result.release == "fail" and result.tags == "pass"
    assert result.pronouns == "pass" and result.machine == "pass"
    assert runs.suites() == [] and NOT_REACHED in result.line()
    assert "packages/sessions: README.md:1: links to docs/guide.md" in capsys.readouterr().out

def test_each_lint_command_runs_in_the_check_s_env(repo):
    commit_file(repo, "packages/sessions/x.py")
    seen = []

    def plan(package, work, members):
        seen.append((package, work, list(members)))
        return [check.lint.Command("one", ["lint-imports", "--config", "c"], ["here"]),
                check.lint.Command("two", ["deptry", "."])]

    runs = Runs()
    result = run_check(repo, runs, plan=plan,
                       env={"PATH": "/usr/bin", "PYTHONPATH": "/stray"})
    assert result.code == 0 and "| lint pass |" in result.line()
    env_dir = repo / ".moxi-dev" / "env-3.12"
    members = [repo / "packages" / d for d in LAYOUT]
    assert seen == [(repo / "packages" / d, repo / ".moxi-dev" / "lint" / d, members)
                    for d in ("sessions", "cache", "comms")]
    linted = [(cmd, cwd, env) for cmd, cwd, env in runs.calls if "pytest" not in cmd
              and cmd[0] != "uv"]
    assert [(cmd[0], cwd.name) for cmd, cwd, _ in linted] == [
        (str(check.venv_program(env_dir, tool)), d) for d in ("sessions", "cache", "comms")
        for tool in ("lint-imports", "deptry")]
    assert linted[0][2]["PYTHONPATH"] == "here" and "PYTHONPATH" not in linted[1][2]
    assert linted[0][2]["VIRTUAL_ENV"] == str(env_dir)
    last_lint = max(i for i, call in enumerate(runs.calls) if call in linted)
    first_suite = min(i for i, (cmd, _, _) in enumerate(runs.calls) if "pytest" in cmd)
    assert last_lint < first_suite


def _failing_lint(package, work, members):
    if package.name == "cache":
        raise check.lint.LintError("no announcement")
    return [check.lint.Command("one", ["deptry", "."])]


def test_a_lint_failure_fails_the_check_after_every_lint_and_runs_no_suite(repo, capsys):
    commit_file(repo, "packages/sessions/x.py")
    runs = Runs({lambda cmd, cwd: Path(cmd[0]).stem == "deptry" and cwd.name == "comms": 1})
    result = run_check(repo, runs, plan=_failing_lint)
    assert result.code == 1
    assert "| lint fail (cache, comms) | no suites run | timed not run |" in result.line()
    assert runs.suites() == []
    assert capsys.readouterr().out.endswith(LINT_STOP)


def test_under_full_a_lint_failure_still_runs_every_suite(repo, capsys):
    runs = Runs({lambda cmd, cwd: Path(cmd[0]).stem == "deptry" and cwd.name == "comms": 1})
    result = run_check(repo, runs, plan=_failing_lint, full=True)
    assert result.code == 1 and "| lint fail (cache, comms) |" in result.line()
    assert [name for name, _ in runs.suites()] == list(LAYOUT)
    assert LINT_STOP not in capsys.readouterr().out


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
    result = check.check(repo, run=Runs(), env={"PATH": "/usr/bin"}, with_dependents=True)
    assert "| lint fail (cache, comms) |" in result.line()
    assert (repo / ".moxi-dev" / "lint" / "sessions" / "importlinter.toml").is_file()


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
    assert [python for python, _ in probe.calls] == [check.venv_program(env_dir, "python")]
    assert probe.calls[0][1]["VIRTUAL_ENV"] == str(env_dir)
    assert "without its browser tests" not in result.line()


def test_without_chromium_the_browser_tests_are_left_out_with_a_line_saying_why(
        browser_repo, capsys):
    runs, probe = Runs(), Probe("Executable doesn't exist at /x/chrome")
    result = run_check(browser_repo, runs, full=True, probe=probe)
    assert result.code == 0
    for cmd, cwd, env in runs.calls:
        if "pytest" in cmd:
            assert "-m" not in cmd[3:]
            # Not even --full puts them back.
            want = None if cwd.name == "plain" else "browser"
            assert env.get(testing.LEAVE_OUT_ENV) == want, cwd.name
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
    fake = program(tmp_path, "python", code=1, stderr=(
        "Traceback (most recent call last):", "No module named 'playwright'"))
    assert check.chromium_probe(fake, {"PATH": "/usr/bin:/bin"}) == "No module named 'playwright'"
    good = program(tmp_path, "good")
    assert check.chromium_probe(good, {"PATH": "/usr/bin:/bin"}) is None
    assert "could not run" in check.chromium_probe(tmp_path / "missing", {})


def test_the_real_workspace_runs_browser_tests_for_its_page_packages():
    if not (REPO / "packages" / "ui" / "pyproject.toml").is_file():
        pytest.skip("not run from the repository (an sdist has no workspace)")
    found = [p for p in check.load_workspace(REPO) if p.browser]
    # The page packages known here, not all of them: the next one joins without a change.
    assert {"cache", "comms", "ui"} <= {p.dir for p in found}
    for package in found:
        data = tomllib.loads((package.path / "pyproject.toml").read_text())
        assert any(check.requirement_name(r) == "moxi-ui"
                   and "testing" in check.requirement_extras(r)
                   for r in check._requirement_strings(data)), package.dir


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
        program(bin_dir, name)
    return str(bin_dir)


def test_with_node_and_node_modules_the_built_files_are_compared(repo, monkeypatch, tmp_path):
    _with_page(repo, monkeypatch)
    (repo / "node_modules").mkdir()
    runs = Runs()
    result = run_check(repo, runs, full=True, env={"PATH": _tools(tmp_path, "node", "npm")})
    assert result.code == 0 and "| web pass |" in result.line()
    lock, web, sync, *_ = runs.calls
    # The npm `which` found, so Windows's npm.cmd starts too.
    npm = program_path(tmp_path / "bin", "npm")
    assert [os.path.normcase(web[0][0]), *web[0][1:]] == [
        os.path.normcase(npm), "run", "check"] and web[1] == repo


def test_a_built_file_that_differs_fails_before_the_sync(repo, monkeypatch, tmp_path):
    _with_page(repo, monkeypatch)
    (repo / "node_modules").mkdir()
    runs = Runs({lambda cmd, cwd: Path(cmd[0]).stem == "npm" and cmd[1] == "run": 1})
    result = run_check(repo, runs, full=True, env={"PATH": _tools(tmp_path, "node", "npm")})
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
    result = run_check(repo, runs, full=True, env={"PATH": _tools(tmp_path, *tools)})
    assert result.code == 0 and "| web skipped |" in result.line()
    assert not any(Path(cmd[0]).stem == "npm" for cmd, _, _ in runs.calls)
    assert ("moxi-dev check: the built web files were not compared with their sources: "
            + why) in capsys.readouterr().err


def test_no_page_picked_runs_no_compare(repo, monkeypatch, tmp_path):
    _with_page(repo, monkeypatch, stamps="none")
    (repo / "node_modules").mkdir()
    runs = Runs()
    result = run_check(repo, runs, full=True, env={"PATH": _tools(tmp_path, "node", "npm")})
    assert "| web none |" in result.line()
    assert not any(Path(cmd[0]).stem == "npm" for cmd, _, _ in runs.calls)


# --- the environment's programs ---------------------------------------------------------

@pytest.mark.parametrize("windows, programs, python, script", [
    (False, "bin", "python", "lint-imports"),
    (True, "Scripts", "python.exe", "lint-imports.exe")])
def test_the_environments_programs_are_where_the_platform_keeps_them(
        monkeypatch, tmp_path, windows, programs, python, script):
    monkeypatch.setattr(check, "WINDOWS", windows)
    assert check.venv_bin(tmp_path) == tmp_path / programs
    assert check.venv_program(tmp_path, "python") == tmp_path / programs / python
    assert check.venv_program(tmp_path, "lint-imports") == tmp_path / programs / script


def test_on_windows_every_step_runs_the_environments_programs_from_scripts(
        repo, monkeypatch):
    monkeypatch.setattr(check, "WINDOWS", True)
    commit_file(repo, "packages/sessions/x.py")
    lint_plan = lambda package, work, members: [
        check.lint.Command("rules", ["lint-imports", "--config", "x"])]
    runs = Runs()
    result = run_check(repo, runs, plan=lint_plan)
    assert result.code == 0
    scripts = repo / ".moxi-dev" / "env-3.12" / "Scripts"
    ran = [cmd[0] for cmd, _, _ in runs.calls if cmd[0] != "uv"]
    assert ran and set(ran) == {str(scripts / "lint-imports.exe"), str(scripts / "python.exe")}
    for _, _, env in runs.calls[2:]:
        assert env["PATH"].split(os.pathsep)[0] == str(scripts)


# --- the renames step -----------------------------------------------------------------------

TABLE_HEAD = "# package\tpath\tscope\tkind\told\tnew\n"


def with_renames(repo, files, *rows):
    """Commit `files` and the repository's rename table holding `rows`, each (path,
    scope, kind, old, new); a row's table line is its place here plus one."""
    for rel, text in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    table = "".join("\t".join(("p", *row)) + "\n" for row in rows)
    commit_file(repo, str(check.rename.TABLE), TABLE_HEAD + table)


X = "packages/sessions/src/moxi_sessions/x.py"


def test_a_settled_tree_passes_the_renames_step(repo):
    with_renames(repo, {X: "fresh = 1\n"}, (X, "-", "name", "old", "fresh"))
    result = run_check(repo, Runs())
    assert result.code == 0 and result.renames == "pass"
    assert "| ruff pass | renames pass | release pass |" in result.line()
    assert LINE.match(result.line()), result.line()


def test_a_row_to_apply_fails_the_renames_step_and_stops_the_check(repo, capsys):
    with_renames(repo, {X: "old = 1\n"}, (X, "-", "name", "old", "fresh"))
    runs = Runs()
    result = run_check(repo, runs)
    assert result.code == 1 and result.renames == "fail"
    assert (repo / X).read_text() == "old = 1\n", "the step writes nothing"
    assert (f"moxi-dev check: renames: to apply: renames.tsv row 2: {X} name old -> fresh"
            in capsys.readouterr().out)
    assert "| renames fail |" in result.line() and LINE.match(result.line())
    assert runs.suites() == []


def test_a_leftover_fails_the_renames_step(repo, capsys):
    with_renames(repo, {X: "def fresh():\n    pass\nthing.old\n"},
                 (X, "-", "name", "old", "fresh"))
    result = run_check(repo, Runs())
    assert result.code == 1 and result.renames == "fail"
    out = capsys.readouterr().out
    assert f"moxi-dev check: renames: leftover: {X}:3: unresolved attribute" in out
    assert "to apply" not in out


def test_a_refused_row_fails_the_renames_step(repo, capsys):
    text = "def run(timeout=1):\n    pick = lambda timeout: timeout * 2\n    return pick(3)\n"
    with_renames(repo, {X: text}, (X, "run", "name", "timeout", "timeout_s"))
    result = run_check(repo, Runs())
    assert result.code == 1 and result.renames == "fail"
    out = capsys.readouterr().out
    assert (f"moxi-dev check: renames: refused: renames.tsv row 2 ({X} run timeout -> "
            f"timeout_s): {X}:2: a scope inside run has its own 'timeout'") in out
    assert (repo / X).read_text() == text


def test_the_renames_step_does_not_compare_with_main(repo, monkeypatch):
    with_renames(repo, {X: "fresh = 1\n"}, (X, "-", "name", "old", "fresh"))
    seen = []
    real = check.rename.read
    monkeypatch.setattr(check.rename, "read",
                        lambda root, rows, **kw: seen.append(kw) or real(root, rows, **kw))
    assert run_check(repo, Runs()).renames == "pass"
    assert seen == [{}]


def test_a_failing_renames_step_prints_its_first_ten_and_how_many_more(repo, monkeypatch,
                                                                        capsys):
    with_renames(repo, {X: "fresh = 1\n"})
    hits = [check.rename.Unsettled("to apply", X, f"renames.tsv row {n}: ...")
            for n in range(2, 14)]
    monkeypatch.setattr(check.rename, "scan", lambda root: hits)
    assert run_check(repo, Runs()).renames == "fail"
    out = [line for line in capsys.readouterr().out.splitlines() if "renames:" in line]
    assert out == [f"moxi-dev check: renames: {hit}" for hit in hits[:10]] + [
        ("moxi-dev check: renames: and 2 more; `uv run moxi-dev rename --dry-run` lists "
         "every one")]


def test_a_tree_with_no_table_has_no_renames_step(repo, monkeypatch):
    monkeypatch.setattr(check.rename, "scan", lambda root: pytest.fail("scanned"))
    assert run_check(repo, Runs()).renames == "none"


def test_only_renames_reads_the_whole_tree_and_filters_printing(repo, monkeypatch, capsys):
    other = "packages/turns/src/moxi_turns/y.py"
    with_renames(repo, {X: "old = 1\n", other: "gone = 1\n"},
                 (X, "-", "name", "old", "fresh"), (other, "-", "name", "gone", "kept"))
    monkeypatch.chdir(repo)
    assert check.main(["--only", "renames", "packages/sessions"]) == 1
    out = capsys.readouterr().out
    assert f"to apply: renames.tsv row 2: {X} name old -> fresh" in out
    assert other not in out
    assert check.main(["--only", "renames", "packages/cache"]) == 0


# --- --static: the whole-tree static steps alone ------------------------------------------

STATIC_NOT_RUN = ("| stamps not run | web not run | sync not run | lint not run | "
                  "no suites run | timed not run | durations none | temp not run |")


def test_static_runs_the_static_steps_and_nothing_else(repo, monkeypatch):
    commit_file(repo, "packages/sessions/x.py")
    monkeypatch.setattr(check, "changed_paths", lambda *a: pytest.fail("read changes"))
    monkeypatch.setattr(check, "worker_args", lambda *a: pytest.fail("read workers"))
    monkeypatch.setattr(check.stamp, "check_stamps", lambda *a, **kw: pytest.fail("stamps"))
    scanned = []
    for module in (check.tags, check.refs, check.naming, check.ruff_check,
                   check.release, check.pronouns, check.machine_text):
        real = module.scan
        monkeypatch.setattr(module, "scan", lambda root, real=real, name=module.__name__:
                            scanned.append(name) or real(root))
    runs = Runs()
    result = run_check(repo, runs, static=True)
    assert result.code == 0 and runs.calls == [], "no command runs: no lock, sync or suite"
    assert scanned == ["moxi_dev.tags", "moxi_dev.refs", "moxi_dev.naming",
                       "moxi_dev.ruff_check", "moxi_dev.release", "moxi_dev.pronouns",
                       "moxi_dev.machine_text"]
    line = result.line()
    assert LINE.match(line), line
    assert ("| packages: none | lock not run | tags pass | refs pass | names pass | "
            "ruff pass | renames none | release pass | pronouns pass | machine pass "
            + STATIC_NOT_RUN) in line


def test_static_runs_the_renames_step_when_there_is_a_table(repo):
    with_renames(repo, {X: "fresh = 1\n"}, (X, "-", "name", "old", "fresh"))
    result = run_check(repo, Runs(), static=True)
    assert result.code == 0 and "| ruff pass | renames pass | release pass |" in result.line()


def test_a_leftover_fails_static_with_the_full_check_s_lines(repo, capsys):
    with_renames(repo, {X: "def fresh():\n    pass\nthing.old\n"},
                 (X, "-", "name", "old", "fresh"))
    runs = Runs()
    result = run_check(repo, runs, static=True)
    assert result.code == 1 and result.renames == "fail" and runs.calls == []
    static_out = [line for line in capsys.readouterr().out.splitlines() if "renames:" in line]
    assert static_out and static_out[0].startswith(
        f"moxi-dev check: renames: leftover: {X}:3: unresolved attribute")
    run_check(repo, Runs())
    full_out = [line for line in capsys.readouterr().out.splitlines() if "renames:" in line]
    assert static_out == full_out


def test_static_steps_all_run_after_one_fails(repo, monkeypatch, capsys):
    tag = "[B5]"
    commit_file(repo, "packages/sessions/x.py", f"# see {tag}\n")
    monkeypatch.setattr(check.ruff_check, "scan", lambda root: [
        check.ruff_check.Hit("packages/sessions/x.py", 1, 1, "N802", "bad name")])
    result = run_check(repo, Runs(), static=True)
    assert result.code == 1 and result.tags == "fail" and result.ruff == "fail"
    assert (result.refs == result.names == result.release == result.pronouns
            == result.machine == "pass")
    out = capsys.readouterr().out
    assert f"moxi-dev check: packages/sessions/x.py:1: tag {tag!r}" in out
    assert "moxi-dev check: packages/sessions/x.py:1: N802: ruff: bad name" in out


def test_main_static_prints_the_line_last_and_returns_its_code(repo, monkeypatch, capsys):
    tag = "[B5]"
    commit_file(repo, "packages/turns/x.py", f"# see {tag}\n")
    runs = Runs()
    monkeypatch.setattr(check, "_run", runs)
    monkeypatch.chdir(repo / "packages" / "turns")
    assert check.main(["--static"]) == 1
    out = capsys.readouterr().out.splitlines()
    assert LINE.match(out[-1]) and out[-1].endswith("exit 1") and "tags fail" in out[-1]
    assert runs.calls == []


def test_a_bad_member_stops_static_with_exit_2(repo, monkeypatch, capsys):
    (repo / "packages" / "turns" / "pyproject.toml").write_text("[tool.x]\n")
    monkeypatch.chdir(repo)
    assert check.main(["--static"]) == 2
    out, err = capsys.readouterr()
    assert out.splitlines()[-1].endswith("exit 2") and "names no project" in err


@pytest.mark.parametrize("args", [["--full"], ["--base", "main"], ["--finding", "L1"],
                                  ["--only", "refs"], ["packages/a"]])
def test_static_takes_no_other_mode(repo, monkeypatch, args):
    monkeypatch.chdir(repo)
    with pytest.raises(SystemExit) as stop:
        check.main(["--static", *args])
    assert stop.value.code == 2


# --- the summary line -------------------------------------------------------------------

LINE = re.compile(
    r"moxi-dev check: commit (?P<commit>[0-9a-f]{12}(\+dirty)?) \| "
    r"packages: (?P<packages>[^|]+) \| lock (pass|fail|not run) \| "
    r"tags (pass|fail|not run) \| "
    r"refs (pass|fail|not run) \| "
    r"names (pass|fail|not run) \| "
    r"ruff (pass|fail|not run) \| "
    r"renames (pass|fail|none|not run) \| "
    r"release (pass|fail|not run) \| "
    r"pronouns (pass|fail|not run) \| "
    r"machine (pass|fail|not run) \| "
    r"stamps (pass|fail|none|skipped|not run) \| "
    r"web (pass|fail|none|skipped|not run) \| "
    r"sync (pass|fail|skipped|not run) \| "
    r"lint (?P<lint>pass|none|not run|fail \([^)]+\)) \| (?P<suites>[^|]+) \| "
    r"timed (?P<timed>held \d+|none|not run) \| "
    r"(?P<durations>durations none|durations \d+\.\d+s, slowest 5% \d+%, delta "
    r"(none|[+-]\d+\.\d+s vs [0-9a-f]{12}( \([^)]+\))?)) \| "
    r"(?P<temp>temp (disk|PYTEST_DEBUG_TEMPROOT|not run)( \([^|]+\))?) \| "
    r"memory (?P<memory>[^|]+) \| "
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
    real_git = Path(shutil.which("git"))
    try:
        (bin_dir / real_git.name).symlink_to(real_git)
    except OSError:
        # No right to make a symlink (Windows without Developer Mode): git's own
        # directory serves, where uv isn't in it.
        if shutil.which("uv", path=str(real_git.parent)):
            pytest.skip("no right to make a symlink here, and uv sits beside git")
        bin_dir = real_git.parent
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
    given_env = {"PATH": "/usr/bin", testing.TIERS_ENV: "all", testing.FINDING_ENV: "L1",
               testing.CHANGED_ENV: "/elsewhere", testing.DURATIONS_ENV: "/elsewhere"}
    run_check(repo, runs, env=given_env)
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
    package to its tests' call seconds, each a number or (seconds, the tiers of a test
    the kit put back). `held` maps a package to its `timed` tests' seconds, in the same
    shape: a pass under `MOXI_TEST_TIMED=hold` names them held and runs none of them."""

    def __init__(self, seconds, codes=None, *, write=True, held=None):
        super().__init__(codes)
        self.seconds = seconds
        self.write = write
        self.held = held or {}

    @staticmethod
    def tests(name, seconds):
        tests = {}
        for i, secs in enumerate(seconds):
            secs, lifted = secs if isinstance(secs, tuple) else (secs, None)
            tests[f"tests/test_{name}.py::test_{i}"] = {
                "setup": 0.0, "call": secs, "teardown": 0.0, "outcome": "passed",
                **({"lifted": lifted} if lifted else {})}
        return tests

    def __call__(self, cmd, cwd, env, /, *, timeout_s=None):
        code = super().__call__(cmd, cwd, env, timeout_s=timeout_s)
        name = Path(cwd).name
        if "pytest" not in cmd or not self.write:
            return code
        mode = env.get(testing.TIMED_ENV)
        timed = self.tests("timed", self.held.get(name, []))
        if name in self.seconds or name in self.held:
            body = {"tests": self.tests("it", self.seconds.get(name, []))}
            if mode == "hold":
                body["held"] = sorted(timed)
        else:
            return code
        Path(env[testing.DURATIONS_ENV]).write_text(json.dumps(body))
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


def on_main(repo, rel, seconds, *, full=True):
    """Commit `rel` on main and keep a record for it there (a plain run, picked by that
    commit, when not `full`)."""
    git(repo, "switch", "-q", "main")
    commit_file(repo, rel)
    result = run_check(repo, Timed(seconds), full=full,
                       **({} if full else {"base": "HEAD~1"}))
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


def test_a_plain_run_that_put_tier_tests_back_is_kept_and_compared_apart(ignoring):
    first = on_main(ignoring, "packages/moxi/a.py", {"cache": [5.0], "comms": [5.0]},
                    full=False)
    commit_file(ignoring, "packages/cache/x.py")
    result = run_check(ignoring, Timed({"cache": [5.0, (40.0, ["slow"])],
                                        "comms": [6.0, (1.0, ["build", "slow"])]}))
    record = json.loads((records(ignoring) / f"{head(ignoring)}.json").read_text())
    assert {name: entry["tiers"] for name, entry in record["packages"].items()} == {
        "cache": "plain+slow", "comms": "plain+build+slow"}
    # neither is compared with main's plain record, so no slowdown is reported
    assert result.delta is None
    result = run_check(ignoring, Timed({"cache": [5.5],
                                        "comms": [6.0, (1.0, ["build", "slow"])]}))
    assert result.delta.commit == first and result.delta.packages == ("cache",)
    assert result.delta.seconds == pytest.approx(0.5)


def test_head_s_own_record_is_not_its_ancestor(ignoring):
    git(ignoring, "switch", "-q", "main")
    commit_file(ignoring, "packages/moxi/a.py")
    run_check(ignoring, Timed({"moxi": [1.0]}), full=True)
    result = run_check(ignoring, Timed({"moxi": [1.0]}), full=True)
    assert result.kept is not None and result.delta is None


@pytest.mark.skipif(sys.platform == "win32" or os.geteuid() == 0,
                    reason="POSIX directory modes; root writes into a read-only directory")
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
        assert check.main(["--with-dependents"]) == 0
    finally:
        where.chmod(0o700)
    out, err = capsys.readouterr()
    assert f"durations: keeping no record for {head(ignoring)[:12]}" in err
    assert "Permission denied" in err
    assert LINE.match(out.splitlines()[-1]), out
    assert "| durations 3.0s, slowest 5% 67%, delta none |" in out
    assert list(where.iterdir()) == []


def test_the_records_are_in_git_s_own_dir_whatever_its_name(tmp_path):
    from moxi_dev import durations
    root = tmp_path / "dépôt"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    assert durations.records_dir(root) == root / ".git" / "moxi-dev" / "durations"


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
    result = run_check(ignoring, runs, finding=["L50"], with_dependents=False)
    assert result.code == 1
    assert ("no test in sessions carries L50; a test in a package that depends on them "
            "needs --with-dependents\n") in capsys.readouterr().out
    result = run_check(ignoring, runs, finding=["L50"], with_dependents=True)
    assert result.code == 1
    assert "no test in sessions, cache, comms carries L50\n" in capsys.readouterr().out


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


# --- the timed tests held back ----------------------------------------------------------------

def pytest_calls(runs):
    """Each pytest run: (package, its xdist arguments, its MOXI_TEST_TIMED, its env)."""
    return [(cwd.name, cmd[3:], env.get(testing.TIMED_ENV), env)
            for cmd, cwd, env in runs.calls if "pytest" in cmd]


def test_full_holds_every_timed_test_and_runs_none(ignoring, monkeypatch):
    monkeypatch.setattr(check, "cpu_count", lambda: 6)
    runs = Timed({name: [1.0] for name in LAYOUT},
                 held={"turns": [0.5], "moxi": [0.25, 0.25]})
    result = run_check(ignoring, runs, full=True)
    assert result.code == 0
    # one session per package, under workers, holding the timed tests; no other session
    assert [(name, args, mode) for name, args, mode, _ in pytest_calls(runs)] == [
        (name, ["-n", "6"], "hold") for name in LAYOUT]
    assert result.timed == 3
    assert "| timed held 3 |" in result.line()
    assert LINE.match(result.line()), result.line()


def test_a_picked_run_holds_the_timed_tests_and_counts_them(ignoring):
    commit_file(ignoring, "packages/sessions/x.py")
    runs = Timed({"sessions": [1.0], "cache": [1.0], "comms": [1.0]},
                 held={"sessions": [0.5], "comms": [0.5]})
    result = run_check(ignoring, runs)
    assert result.code == 0
    assert [(name, mode) for name, _, mode, _ in pytest_calls(runs)] == [
        ("sessions", "hold"), ("cache", "hold"), ("comms", "hold")]
    assert "| timed held 2 |" in result.line()


def test_held_timed_tests_have_no_times_in_the_durations_or_record(ignoring):
    commit_file(ignoring, "packages/cache/x.py")
    runs = Timed({"cache": [1.0], "comms": [2.0]},
                 held={"cache": [0.5, (4.0, ["slow"])]})
    result = run_check(ignoring, runs)
    assert "| durations 3.0s, " in result.line()
    cache = json.loads(result.kept.read_text())["packages"]["cache"]
    assert sorted(cache["tests"]) == ["tests/test_it.py::test_0"]
    assert cache["tiers"] == "plain"


def test_no_held_test_says_timed_none(ignoring):
    commit_file(ignoring, "packages/cache/x.py")
    runs = Timed({"cache": [1.0], "comms": [2.0]})
    result = run_check(ignoring, runs)
    assert [mode for _, _, mode, _ in pytest_calls(runs)] == ["hold", "hold"]
    assert "| timed none |" in result.line()


def test_a_check_that_stops_before_the_suites_says_timed_not_run(repo):
    runs = Runs({lambda cmd, cwd: cmd[:2] == ["uv", "lock"]: 1})
    assert "| no suites run | timed not run |" in run_check(repo, runs, full=True).line()


def test_a_package_whose_chosen_tests_are_all_timed_is_none(ignoring):
    commit_file(ignoring, "packages/cache/x.py")
    cache = lambda cmd, cwd: "pytest" in cmd and cwd.name == "cache"
    runs = Timed({"comms": [2.0]}, {cache: 5}, held={"cache": [0.5]})
    result = run_check(ignoring, runs)
    assert result.code == 0
    assert re.search(r"\| cache none \S+, comms pass \S+ \| timed held 1 \|",
                     result.line()), result.line()
    assert sorted(json.loads(result.kept.read_text())["packages"]) == ["comms"]


@pytest.mark.parametrize("findings, said", [
    (["L50"], "L50: its tests are timed; moxi-dev timed --finding L50 runs them"),
    (["L50", "L51"], ("L50, L51: their tests are timed; "
                      "moxi-dev timed --finding L50 --finding L51 runs them"))])
def test_a_finding_whose_tests_are_all_timed_points_at_the_timed_mode_and_passes(
        ignoring, capsys, findings, said):
    commit_file(ignoring, "packages/sessions/x.py")
    every = lambda cmd, cwd: "pytest" in cmd
    runs = Timed({"cache": [], "comms": []}, {every: 5}, held={"sessions": [0.5]})
    result = run_check(ignoring, runs, finding=findings)
    assert result.code == 0
    assert {mode for _, _, mode, _ in pytest_calls(runs)} == {"hold"}
    out = capsys.readouterr().out
    assert f"moxi-dev check: {said}\n" in out and "carries" not in out
    assert re.search(r"\| sessions none \S+, cache none \S+, comms none \S+ \| "
                     r"timed held 1 \|", result.line()), result.line()


def test_a_finding_whose_tests_ran_says_nothing_of_its_held_ones(ignoring, capsys):
    commit_file(ignoring, "packages/sessions/x.py")
    others = lambda cmd, cwd: "pytest" in cmd and cwd.name != "sessions"
    runs = Timed({"sessions": [1.0], "cache": [], "comms": []}, {others: 5},
                 held={"sessions": [0.5]})
    result = run_check(ignoring, runs, finding=["L50"])
    assert result.code == 0 and "are timed" not in capsys.readouterr().out


def test_the_suites_drop_a_caller_s_timed_setting(repo):
    runs = Runs()
    run_check(repo, runs, full=True,
              env={"PATH": "/usr/bin", testing.TIMED_ENV: "only"})
    assert {env[testing.TIMED_ENV] for _, _, _, env in pytest_calls(runs)} == {"hold"}


def test_the_suites_drop_a_caller_s_left_out_tiers(repo):
    runs = Runs()
    run_check(repo, runs, full=True,
              env={"PATH": "/usr/bin", testing.LEAVE_OUT_ENV: "slow"})
    assert all(testing.LEAVE_OUT_ENV not in env for _, _, _, env in pytest_calls(runs))


# --- where the suites keep their temp --------------------------------------------------

@pytest.fixture
def fake_temp(tmp_path, monkeypatch):
    """A Linux machine for moxi_dev.temp: a disk root on ext4 and a RAM root on tmpfs."""
    disk, ram = tmp_path / "disk", tmp_path / "shm"
    disk.mkdir()
    ram.mkdir()
    mounts = tmp_path / "mountinfo"
    mounts.write_text(f"1 0 0:1 / / rw - ext4 ext4 rw\n"
                      f"2 1 0:2 / {os.path.realpath(disk)} rw - ext4 ext4 rw\n"
                      f"3 1 0:3 / {os.path.realpath(ram)} rw - tmpfs tmpfs rw\n")
    where = temp.Places(platform="linux", disk_root=str(disk), ram_root=str(ram),
                        system_temp=str(disk), mountinfo=str(mounts))
    monkeypatch.setattr(temp, "_PLACES", where)
    return where


def _suite_envs(runs):
    return [env for cmd, _, env in runs.calls if "pytest" in cmd]


def test_a_check_moves_ram_to_disk_for_its_suites(repo, fake_temp):
    commit_file(repo, "packages/sessions/x.py")
    runs = Runs()
    result = run_check(repo, runs, env={"PATH": "/usr/bin", "MOXI_TEST_TEMP": "ram"})
    envs = _suite_envs(runs)
    assert envs and all(env["MOXI_TEST_TEMP"] == "disk" for env in envs)
    assert "| temp disk (ram asked; a check keeps its temp on disk) |" in result.line()
    assert LINE.match(result.line()), result.line()


def test_a_check_leaves_ram_to_the_suites_off_linux(repo, fake_temp, monkeypatch):
    monkeypatch.setattr(temp, "_PLACES", dataclasses.replace(fake_temp, platform="darwin"))
    commit_file(repo, "packages/sessions/x.py")
    runs = Runs()
    result = run_check(repo, runs, env={"PATH": "/usr/bin", "MOXI_TEST_TEMP": "ram"})
    assert all(env["MOXI_TEST_TEMP"] == "ram" for env in _suite_envs(runs))
    assert "| temp disk (ram asked; no RAM filesystem on this system) |" in result.line()


def test_a_check_moves_a_temproot_in_memory_to_disk(repo, fake_temp):
    commit_file(repo, "packages/sessions/x.py")
    runs = Runs()
    given = {"PATH": "/usr/bin", "PYTEST_DEBUG_TEMPROOT": fake_temp.ram_root}
    result = run_check(repo, runs, env=given)
    for env in _suite_envs(runs):
        assert env["MOXI_TEST_TEMP"] == "disk"
        assert env["PYTEST_DEBUG_TEMPROOT"] == fake_temp.ram_root
    assert (f"| temp disk (PYTEST_DEBUG_TEMPROOT={fake_temp.ram_root} is in memory; a "
            "check keeps its temp on disk) |") in result.line()


def test_a_check_passes_a_disk_choice_through(repo, fake_temp, tmp_path):
    commit_file(repo, "packages/sessions/x.py")
    scratch = fake_temp.disk_root + "/scratch"
    os.mkdir(scratch)
    for given, field in (({}, "temp disk"),
                         ({"PYTEST_DEBUG_TEMPROOT": fake_temp.disk_root}, "temp disk"),
                         ({"PYTEST_DEBUG_TEMPROOT": scratch}, "temp PYTEST_DEBUG_TEMPROOT"),
                         ({"MOXI_TEST_TEMP": "disk"}, "temp disk")):
        runs = Runs()
        result = run_check(repo, runs, env={"PATH": "/usr/bin", **given})
        for env in _suite_envs(runs):
            assert env.get("MOXI_TEST_TEMP") == given.get("MOXI_TEST_TEMP")
            assert env.get("PYTEST_DEBUG_TEMPROOT") == given.get("PYTEST_DEBUG_TEMPROOT")
            assert not any(arg.startswith("--basetemp") for arg in runs.calls[0][0])
        assert f"| {field} |" in result.line()


def test_a_bad_setting_stops_the_check_and_still_sweeps(repo, fake_temp, capsys):
    swept = []
    result = run_check(repo, Runs(), env={"PATH": "/usr/bin", "MOXI_TEST_TEMP": "RAM"},
                       sweep=lambda: swept.append(1) or temp.Swept())
    assert result.code == 2 and result.lock == "not run"
    assert "MOXI_TEST_TEMP must be disk or ram; it is 'RAM'" in capsys.readouterr().err
    assert swept == [1]                                 # the end's sweep, by any path


def test_static_reads_no_temp_setting_and_sweeps_nothing(repo, fake_temp):
    def sweep():
        pytest.fail("a run that starts no test swept")

    result = run_check(repo, Runs(), static=True, sweep=sweep,
                       env={"PATH": "/usr/bin", "MOXI_TEST_TEMP": "RAM"})
    assert result.code == 0, "a bad MOXI_TEST_TEMP doesn't stop a run that makes no temp"
    assert "| temp not run |" in result.line()


def test_the_check_sweeps_at_its_start_after_each_suite_and_at_its_end(repo, capsys):
    commit_file(repo, "packages/sessions/x.py")
    runs = Runs({lambda cmd, cwd: "pytest" in cmd and cwd.name == "cache": -9})

    def sweep():
        runs.calls.append((["sweep"], Path("."), {}))
        first = len([c for c in runs.calls if c[0] == ["sweep"]]) == 1
        return temp.Swept(count=2, size=3 * 1024 ** 3) if first else temp.Swept()

    result = run_check(repo, runs, sweep=sweep)
    order = ["sweep" if cmd == ["sweep"] else cwd.name
             for cmd, cwd, _ in runs.calls if cmd == ["sweep"] or "pytest" in cmd]
    assert order == ["sweep", "sessions", "sweep", "cache", "sweep", "comms", "sweep",
                     "sweep"]
    assert result.code == 1                              # cache's suite died by a signal
    assert ("moxi-dev check: removed 2 test temp dirs (3.0 GiB) left by runs that ended "
            "without cleaning up") in capsys.readouterr().out


def test_a_suite_in_memory_after_all_is_named_in_the_summary(repo):
    commit_file(repo, "packages/sessions/x.py")

    class Writing(Runs):
        def __call__(self, cmd, cwd, env, /, *, timeout_s=None):
            if "pytest" in cmd and cwd.name in ("sessions", "cache"):
                chosen = {"mode": "disk", "root": "/tmp", "is_in_memory": True,
                          "why": "/var/tmp is not writable"}
                Path(env[testing.DURATIONS_ENV]).write_text(
                    json.dumps({"format": 1, "tests": {}, "temp": chosen}))
            return super().__call__(cmd, cwd, env, timeout_s=timeout_s)

    result = run_check(repo, Writing())
    assert ("| temp disk (2 suites in memory at /tmp: /var/tmp is not writable) |"
            in result.line())

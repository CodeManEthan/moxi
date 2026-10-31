"""`moxi-dev check` run for real on a two-package workspace: the lock, the sync, the suites.

One real `uv lock` and `uv sync` (a few seconds from uv's cache), and real pytest
sessions under xdist workers. uv runs only in child processes, with the kit's `uv_env`:
the fenced environment plus uv's real cache and installed Pythons.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from moxi_dev import check

pytestmark = pytest.mark.skipif(shutil.which("uv") is None, reason="needs uv")

TOOLS = '["pytest==9.1.1", "pytest-xdist==3.8.0"]'

# Each test proves it ran in an xdist worker, not in a serial session.
UNDER_WORKERS = """\
def test_runs_in_a_worker(request):
    assert hasattr(request.config, "workerinput")
"""


def git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com",
                    "-c", "commit.gpgsign=false", *args],
                   cwd=root, check=True, capture_output=True, text=True)


def package(root: Path, name: str, deps: str) -> None:
    path = root / "packages" / name
    (path / "tests").mkdir(parents=True)
    (path / "pyproject.toml").write_text(
        f'[project]\nname = "{name}"\nversion = "0.1.0"\nrequires-python = ">=3.12"\n'
        f"dependencies = {deps}\n[dependency-groups]\ndev = {TOOLS}\n"
        '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n')
    (path / "tests" / "test_it.py").write_text(UNDER_WORKERS)


@pytest.fixture
def workspace(tmp_path, monkeypatch, uv_env):
    """`low` and `high` (which depends on `low`), locked and committed on main."""
    for var in ("PYTEST_XDIST_WORKER", "PYTEST_XDIST_WORKER_COUNT",
                "PYTEST_XDIST_TESTRUNUID", "PYTEST_ADDOPTS"):
        monkeypatch.delenv(var, raising=False)
        uv_env.pop(var, None)
    # check.main hands its own environment to the uv and pytest it starts, so uv's
    # settings go there; HOME and the XDG homes stay the fenced ones.
    for var in ("UV_CACHE_DIR", "UV_PYTHON_INSTALL_DIR", "UV_PYTHON_DOWNLOADS"):
        monkeypatch.setenv(var, uv_env[var])
    monkeypatch.setenv("MOXI_TEST_WORKERS", "2")
    root = tmp_path / "w"
    root.mkdir()
    (root / "pyproject.toml").write_text(
        '[tool.uv.workspace]\nmembers = ["packages/low", "packages/high"]\n'
        '[tool.uv.sources]\nlow = { workspace = true }\n')
    (root / ".gitignore").write_text(".moxi-dev/\n.venv/\n__pycache__/\n.pytest_cache/\n")
    package(root, "low", "[]")
    package(root, "high", '["low"]')
    subprocess.run(["uv", "lock", "--quiet"], cwd=root, env=uv_env, check=True,
                   timeout=120)
    git(root, "init", "-q", "-b", "main")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "start")
    git(root, "switch", "-q", "-c", "work")
    monkeypatch.chdir(root)
    return root


def last_line(capsys) -> str:
    return capsys.readouterr().out.strip().splitlines()[-1]


def test_the_check_syncs_its_own_env_and_runs_the_picked_suites(workspace, capsys,
                                                                monkeypatch):
    # Inherited, this would make `-n auto` serial; the suites still run under workers.
    monkeypatch.setenv("PYTEST_XDIST_AUTO_NUM_WORKERS", "0")
    (workspace / "packages/low/tests/test_more.py").write_text(UNDER_WORKERS)
    assert check.main([]) == 0
    line = last_line(capsys)
    assert ("packages: low, high | lock pass | tags pass | stamps none | web none | "
            "sync pass | lint none | low pass") in line, line
    assert ", high pass " in line and line.endswith("exit 0")
    assert (workspace / ".moxi-dev/env-3.12/bin/python").exists()
    assert not (workspace / ".venv").exists()
    assert not (Path(os.environ["HOME"]) / ".cache" / "uv").exists()  # the real cache served

    git(workspace, "add", "-A")
    git(workspace, "commit", "-q", "-m", "more")
    git(workspace, "branch", "mark")
    (workspace / "packages/high/tests/test_it.py").write_text("def test_no():\n    assert 0\n")
    git(workspace, "add", "-A")
    git(workspace, "commit", "-q", "-m", "break high")
    assert check.main(["--base", "mark"]) == 1
    line = last_line(capsys)
    assert ("packages: high | lock pass | tags pass | stamps none | web none | sync pass | "
            "lint none | high fail ") in line, line
    assert line.endswith("exit 1")


def test_a_stale_lock_fails_the_check(workspace, capsys):
    (workspace / "packages/low/pyproject.toml").write_text(
        (workspace / "packages/low/pyproject.toml").read_text()
        .replace("dependencies = []", 'dependencies = ["iniconfig"]'))
    assert check.main(["--full"]) == 1
    assert "lock fail | tags not run | stamps not run | web not run | sync not run" in (
        last_line(capsys))

"""Fixtures: rounds' config file and the projects list in the test's own Moxi home, a
second process to hold a lock, and a git repo of the test's own.

The test kit points `MOXI_ROUNDS_HOME` and `MOXI_PROJECTS_HOME` into each test's tmp, so
the config file and the projects every call reads and writes are the test's own.
"""

from __future__ import annotations

import itertools
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from moxi_rounds import paths


@pytest.fixture
def config_file() -> Path:
    """The test's own config file (not yet created)."""
    return paths.LAYOUT.config_file()


@pytest.fixture
def write_config(config_file):
    """`write_config(text)`: write `text`, dedented, as rounds' config file; returns the
    file's path."""

    def write(text: str) -> Path:
        config_file.parent.mkdir(parents=True, exist_ok=True)
        config_file.write_text(textwrap.dedent(text), encoding="utf-8")
        return config_file

    return write


@pytest.fixture
def add_project():
    """`add_project(key, notes_root=None)`: add the project `key` to the test's projects
    list, its root a new folder under the test's home; with no `notes_root` its record
    names no notes folder. Returns the root."""
    from moxi import cli

    def add(key: str, notes_root: Path | None = None) -> Path:
        root = Path.home() / "roots" / key
        root.mkdir(parents=True)
        assert cli.main(["projects", "add", key, "--root", str(root)]) == 0
        if notes_root is None:
            argv = ["projects", "set", key, "--no-notes-root"]
        else:
            notes_root.mkdir(parents=True, exist_ok=True)
            argv = ["projects", "set", key, "--notes-root", str(notes_root)]
        assert cli.main(argv) == 0
        return root

    return add


@pytest.fixture
def without_notes(monkeypatch):
    """The notes module reads as not installed."""
    from moxi import modules

    installed = modules.available
    monkeypatch.setattr(modules, "available",
                        lambda name: name != "notes" and installed(name))


_HOLDER = textwrap.dedent("""
    import sys
    from moxi import files
    with files.lock(sys.argv[1]):
        print("held", flush=True)
        sys.stdin.read()
""")


@pytest.fixture
def holder():
    """`holder(path)`: a real process that holds the lock on `path` until the test ends.
    Returns the process. A thread can't stand in: the lock refuses a second taker in
    its own process outright."""
    procs = []

    def start(path):
        proc = subprocess.Popen([sys.executable, "-c", _HOLDER, str(path)],
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        procs.append(proc)
        assert proc.stdout.readline().strip() == "held"
        return proc

    yield start
    for proc in procs:
        proc.communicate(timeout=30)


class GitRepo:
    """A git repo for one test, at `root`, with one commit on `main`. `git(...)` runs git
    there and gives what it printed, stripped. The environment is built from nothing but
    PATH, so neither the user's git config nor a `GIT_` variable of the run reaches it,
    and each commit's date is a second after the one before."""

    _clock = itertools.count(1)

    def __init__(self, root: Path):
        self.root = Path(root)

    def env(self) -> dict[str, str]:
        when = f"{1_700_000_000 + next(self._clock)} +0000"
        return {"PATH": os.environ.get("PATH", ""), "HOME": str(self.root.parent),
                "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_AUTHOR_NAME": "coder", "GIT_AUTHOR_EMAIL": "coder@example.test",
                "GIT_COMMITTER_NAME": "coder", "GIT_COMMITTER_EMAIL": "coder@example.test",
                "GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when}

    def git(self, *args: str) -> str:
        done = subprocess.run(["git", "-C", str(self.root), *args], env=self.env(),
                              capture_output=True, text=True, check=False)
        assert done.returncode == 0, f"git {' '.join(args)}: {done.stderr}"
        return done.stdout.strip()

    def rev(self, ref: str = "HEAD") -> str:
        return self.git("rev-parse", f"{ref}^{{commit}}")

    def commit(self, name: str, text: str | None = None) -> str:
        """Write `text` (else `name`) to the file `name`, commit it, and give the
        commit."""
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text((name if text is None else text) + "\n", encoding="utf-8")
        self.git("add", "--", name)
        self.git("commit", "-q", "-m", f"change {name}")
        return self.rev()

    def switch(self, branch: str, *, new_from: str | None = None) -> None:
        if new_from is None:
            self.git("switch", "-q", branch)
        else:
            self.git("switch", "-q", "-c", branch, new_from)

    def merge(self, ref: str, *more: str) -> str:
        """Merge `ref` into the branch checked out with a merge commit, and give it."""
        self.git("merge", "-q", "--no-ff", "--no-edit", *more, ref)
        return self.rev()


@pytest.fixture(scope="session")
def _git_template(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("git-template") / "repo"
    root.mkdir()
    repo = GitRepo(root)
    repo.git("init", "-q", "-b", "main")
    repo.commit("README", "a placeholder repo")
    return root


@pytest.fixture
def git_repo(_git_template, tmp_path) -> GitRepo:
    """A git repo of the test's own, copied from one made once for the run: one commit
    on `main`, which is checked out."""
    root = tmp_path / "repo"
    shutil.copytree(_git_template, root, symlinks=True)
    return GitRepo(root)

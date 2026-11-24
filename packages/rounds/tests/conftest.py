"""Fixtures: rounds' config file and the projects list in the test's own Moxi home, a
second process to hold a lock, and a git repo of the test's own.

The test kit points `MOXI_ROUNDS_HOME` and `MOXI_PROJECTS_HOME` into each test's tmp, so
the config file and the projects every call reads and writes are the test's own. Every
test asks for the kit's fence by name, and the config fixtures refuse a path outside the
test's tmp, so a run the kit does not fence fails instead of writing to the real homes.
Every fixture here a test file elsewhere can borrow (by `-p conftest` or by importing it)
takes `fenced`, which checks at setup that both homes are inside the test's own tmp, so a
borrowed fixture fails before its body runs: without the kit with "fixture ... not found",
and with the kit but not its environment with `fence_in_force`'s text.
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


#: The variables the fence points into the test's tmp that this file's fixtures rely on:
#: rounds' home and the projects list's.
FENCED_HOMES = (paths.HOME_ENV, "MOXI_PROJECTS_HOME")


def fence_in_force(env, roots) -> str | None:
    """None when every variable of `FENCED_HOMES` is set in `env` and is inside one of
    `roots` (the test's `tmp_path` and the fence's short directory), real paths compared
    on both sides; else the text that fails the test, naming what was found."""
    real_roots = [os.path.realpath(root) for root in roots]
    found = []
    for variable in FENCED_HOMES:
        value = env.get(variable)
        if not value:
            found.append(f"{variable} is not set")
            continue
        real = Path(os.path.realpath(value))
        inside = [root for root in real_roots if real.is_relative_to(root)]
        if not inside:
            found.append(f"{variable} is {value}")
    if not found:
        return None
    return ("rounds' tests run only inside moxi-dev's test fence, which "
            "packages/rounds/conftest.py loads, and this test's MOXI_ROUNDS_HOME or "
            f"MOXI_PROJECTS_HOME is not inside its own tmp ({'; '.join(found)}). Put the "
            "test in packages/rounds/tests and run pytest from packages/rounds.")


@pytest.fixture(autouse=True)
def fenced(fence, tmp_path):
    """Every test asks for the fence by name, so a run without the test kit's plugin (a
    copy of these tests outside the package, whose root conftest.py loads it) fails each
    test at setup instead of writing to the real homes. With the kit, the test fails at
    setup unless the fence's homes are in force (`fence_in_force`). Every fixture a test
    can borrow takes this one, so the check runs before any of them. It runs once, at
    setup: a test body that moves a home afterwards is not checked here."""
    refused = fence_in_force(os.environ, (tmp_path, fence.short))
    if refused is not None:
        pytest.fail(refused, pytrace=False)
    return fence


def own_path(path: Path, tmp_path: Path, fence) -> Path:
    """`path`, when it is inside the test's own tmp (`tmp_path` or the fence's short
    directory); else the test fails, naming it."""
    real = Path(os.path.realpath(path))
    for root in (tmp_path, fence.short):
        if real.is_relative_to(os.path.realpath(root)):
            return path
    pytest.fail(f"the config file {path} is outside the test's own tmp "
                f"({tmp_path}, {fence.short}): the test fence is not in force",
                pytrace=False)


@pytest.fixture
def config_file(tmp_path, fenced) -> Path:
    """The test's own config file (not yet created). A path outside the test's tmp fails
    the test."""
    return own_path(paths.LAYOUT.config_file(), tmp_path, fenced)


@pytest.fixture
def write_config(config_file):
    """`write_config(text)`: write `text`, dedented, as rounds' config file; returns the
    file's path. A path outside the test's tmp fails the test before anything is
    written (through `config_file`)."""

    def write(text: str) -> Path:
        config_file.parent.mkdir(parents=True, exist_ok=True)
        config_file.write_text(textwrap.dedent(text), encoding="utf-8")
        return config_file

    return write


@pytest.fixture
def add_project(fenced):
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
def without_notes(fenced, monkeypatch):
    """The notes module reads as not installed."""
    from moxi import modules

    installed = modules.available
    monkeypatch.setattr(modules, "available",
                        lambda name: name != "notes" and installed(name))


_HOLDER = textwrap.dedent("""
    import os
    import sys
    from moxi import files
    with files.lock(sys.argv[1]):
        print("held", os.getpid(), flush=True)
        sys.stdin.read()
""")


@pytest.fixture
def holder(fenced):
    """`holder(path)`: a real process that holds the lock on `path` until the test ends.
    Returns the process, with `own_pid` the pid it reports itself, which is the pid the
    lock records: on Windows a venv's python.exe starts the interpreter as its child, so
    `Popen.pid` is not it. A thread can't stand in: the lock refuses a second taker in
    its own process outright."""
    procs = []

    def start(path):
        proc = subprocess.Popen([sys.executable, "-c", _HOLDER, str(path)],
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        procs.append(proc)
        said, _, pid = proc.stdout.readline().strip().partition(" ")
        assert said == "held"
        proc.own_pid = int(pid)
        return proc

    yield start
    for proc in procs:
        if not proc.stdin.closed:
            proc.communicate(timeout=30)
            continue
        # A test closed stdin to let the lock go; before Python 3.13, communicate
        # flushes stdin first and raises on a closed one.
        proc.wait(timeout=30)
        proc.stdout.close()


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
def git_repo(fenced, _git_template, tmp_path) -> GitRepo:
    """A git repo of the test's own, copied from one made once for the run: one commit
    on `main`, which is checked out."""
    root = tmp_path / "repo"
    shutil.copytree(_git_template, root, symlinks=True)
    return GitRepo(root)

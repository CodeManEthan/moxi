"""Fixtures: rounds' config file and the projects list in the test's own Moxi home, and
a second process to hold a lock.

The test kit points `MOXI_ROUNDS_HOME` and `MOXI_PROJECTS_HOME` into each test's tmp, so
the config file and the projects every call reads and writes are the test's own.
"""

from __future__ import annotations

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

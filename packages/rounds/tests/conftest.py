"""Fixtures: rounds' config file and the projects list in the test's own Moxi home.

The test kit points `MOXI_ROUNDS_HOME` and `MOXI_PROJECTS_HOME` into each test's tmp, so
the config file and the projects every call reads and writes are the test's own.
"""

from __future__ import annotations

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

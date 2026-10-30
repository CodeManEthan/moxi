"""Fixtures: projects' store in the test's own Moxi home, a golden store built once, and a
site of fake distributions that contribute to projects.

The test kit points `MOXI_PROJECTS_HOME` into each test's tmp, so the store every call
reads and writes is the test's own. The golden store is built once per session and
copied into a test that wants rows to read; a test that writes builds what it needs.
"""

from __future__ import annotations

import os
import shutil
import sys
import textwrap
from pathlib import Path

import pytest

from moxi_projects import api, store

#: The golden store's rows: (key, root under the golden root, kind, lead, associated).
GOLDEN_ROWS = (
    ("alpha", "alpha", "project", "lead-a", ("helper", "scout")),
    ("Beta", "beta", "agent", None, ()),
    ("gamma", "gamma", "project", "lead-a", ()),
    ("delta.web", "work/delta", "project", None, ("helper",)),
)


def fill(conn, base: str, rows=GOLDEN_ROWS) -> None:
    """Insert `rows` with roots under `base`."""
    for key, rel, kind, lead, associated in rows:
        store.insert(conn, key=key, root=os.path.join(base, rel), kind=kind, lead=lead)
        for name in associated:
            store.add_agent(conn, key, name)


@pytest.fixture(scope="session")
def golden(tmp_path_factory) -> tuple[Path, str]:
    """The golden store, built once: its file and the base its roots sit under."""
    where = tmp_path_factory.mktemp("golden")
    base = os.path.join(str(where), "roots")
    db = where / "projects.db"
    store.write(lambda conn: fill(conn, base), where=str(db))
    return db, base


@pytest.fixture
def store_path() -> Path:
    """The test's own store file (not yet created)."""
    return Path(api._store_path())


@pytest.fixture
def golden_copy(golden, store_path) -> str:
    """The golden store copied in as the test's store; returns the roots' base."""
    db, base = golden
    store_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(db, store_path)
    return base


# --- fake distributions ------------------------------------------------------------------

class Site:
    """A directory on sys.path where a test installs fake distributions."""

    def __init__(self, root: Path):
        self.root = root
        self.packages: set[str] = set()

    def write(self, relpath: str, text: str) -> Path:
        path = self.root / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text))
        top = relpath.split("/")[0]
        if not top.endswith(".dist-info"):
            self.packages.add(top)
        return path

    def distribution(self, name: str, groups: dict[str, dict[str, str]]) -> None:
        info = f"{name.replace('-', '_')}-0.1.0.dist-info"
        self.write(f"{info}/METADATA",
                   f"Metadata-Version: 2.1\nName: {name}\nVersion: 0.1.0\n")
        self.write(f"{info}/entry_points.txt", "".join(
            f"[{group}]\n" + "".join(f"{k} = {v}\n" for k, v in entries.items()) + "\n"
            for group, entries in groups.items()))

    def contribution(self, module: str, source: str, *, package: str | None = None) -> str:
        """A fake module `module` whose `moxi.projects` entry points at a link module
        with `source` as its text. Returns the link module's dotted name."""
        package = package or f"fake_{module}"
        self.write(f"{package}/__init__.py", "")
        self.write(f"{package}/projects_link.py", source)
        target = f"{package}.projects_link"
        self.distribution(f"fake-{module}", {"moxi.projects": {module: target}})
        return target

    def module(self, name: str) -> None:
        """A fake Moxi module `name`: a distribution declaring it in `moxi.modules`."""
        package = f"moxi_{name}"
        self.write(f"{package}/__init__.py", "")
        self.write(f"{package}/module.py", "SUMMARY = 'A module made for a test.'\n")
        self.distribution(f"moxi-{name}", {"moxi.modules": {name: f"{package}.module"}})


@pytest.fixture
def site(tmp_path, monkeypatch):
    """A fresh site on sys.path for the test's fake distributions. While it is active,
    `importlib.metadata` finds only the site's distributions and `moxi` itself."""
    from importlib.metadata import Distribution

    root = tmp_path / "site"
    root.mkdir()
    discover = Distribution.discover

    def filtered(cls, **kwargs):
        return (d for d in discover(**kwargs)
                if d.name == "moxi" or Path(d.locate_file("")) == root)

    monkeypatch.setattr(Distribution, "discover", classmethod(filtered))
    monkeypatch.syspath_prepend(str(root))
    s = Site(root)
    yield s
    for mod in list(sys.modules):
        if mod.split(".")[0] in s.packages:
            del sys.modules[mod]

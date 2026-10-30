"""Fixtures: services' store in the test's own Moxi home, rows to put in it, and a site of
fake distributions for module rows.

The test kit points each module's home into the test's tmp, so the store every call
reads and writes is the test's own.
"""

from __future__ import annotations

import sqlite3
import sys
import textwrap
from pathlib import Path

import pytest

from moxi_services import store


@pytest.fixture
def store_path() -> Path:
    """The test's own store file (not yet created)."""
    return Path(store.path())


@pytest.fixture
def too_new(store_path):
    """A store one schema version past this code's."""
    store.write(lambda conn: None)
    conn = sqlite3.connect(store_path)
    conn.execute(f"PRAGMA user_version = {store.VERSION + 1}")
    conn.commit()
    conn.close()
    return store_path


# --- fake distributions ------------------------------------------------------------------

class Site:
    """A directory on sys.path where a test installs fake Moxi modules."""

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

    def module(self, name: str, announcement: str) -> None:
        """A fake Moxi module `name` whose announcement is `announcement`."""
        package = f"moxi_{name}"
        self.write(f"{package}/__init__.py", "")
        self.write(f"{package}/module.py", announcement)
        info = f"moxi_{name}-0.1.0.dist-info"
        self.write(f"{info}/METADATA",
                   f"Metadata-Version: 2.1\nName: moxi-{name}\nVersion: 0.1.0\n")
        self.write(f"{info}/entry_points.txt",
                   f"[moxi.modules]\n{name} = {package}.module\n")


@pytest.fixture
def site(tmp_path, monkeypatch):
    """A fresh site on sys.path for fake modules. While it is active, `importlib.metadata`
    finds only the site's distributions, `moxi` itself and `moxi-services`."""
    from importlib.metadata import Distribution

    import moxi.modules

    root = tmp_path / "site"
    root.mkdir()
    discover = Distribution.discover

    def filtered(cls, **kwargs):
        return (d for d in discover(**kwargs)
                if d.name in ("moxi", "moxi-services")
                or Path(d.locate_file("")) == root)

    monkeypatch.setattr(Distribution, "discover", classmethod(filtered))
    monkeypatch.syspath_prepend(str(root))
    moxi.modules.reset()
    s = Site(root)
    yield s
    for mod in list(sys.modules):
        if mod.split(".")[0] in s.packages:
            del sys.modules[mod]
    moxi.modules.reset()


# --- the core's writer, faked -----------------------------------------------------------

@pytest.fixture
def fake(monkeypatch):
    """A fake `moxi.units` in place of the one services' acts and reads call, so no test
    reaches a supervisor. Its `probe` records, at each of its calls, whether the core
    lock is held by services' act."""
    from fake_units import FakeUnits

    from moxi_services import acts, state

    units = FakeUnits()
    depth = [0]
    real = acts._locked

    from contextlib import contextmanager

    @contextmanager
    def watched():
        with real():
            depth[0] += 1
            try:
                yield
            finally:
                depth[0] -= 1

    units.probe = lambda: depth[0] > 0
    monkeypatch.setattr(acts, "_locked", watched)
    monkeypatch.setattr(acts, "units", units)
    monkeypatch.setattr(state, "units", units)
    return units

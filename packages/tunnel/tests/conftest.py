"""Fixtures: a site of fake distributions that declare targets, and the record's locks.

The test kit points every module's home into the test's tmp, so the record, the locks and
each fixture module's config file are the test's own.
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

import moxi.modules

#: A fixture module's schema: two int ports and a string.
SETTINGS = """\
[port]
type = "int"
default = 8461
doc = "The port."
read = "use"

[other.port]
type = "int"
default = 9000
doc = "Another port."
read = "use"

[name]
type = "string"
default = ""
doc = "Not a port."
read = "use"
"""


class Site:
    """A directory on sys.path where a test installs fake Moxi modules that declare
    targets in `moxi.tunnel`."""

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
            drop_modules({top})
        return path

    def module(self, name: str, targets: dict[str, str] | None = None, *,
               values: str = "", dist: str | None = None,
               settings: str | None = SETTINGS) -> str:
        """A fake module `name` with a `settings.toml` and a `tunnel_decl.py` holding
        `values` (Python source); `targets` maps each `moxi.tunnel` entry name to the
        attribute of `tunnel_decl` it points at."""
        package = f"moxi_{name}"
        self.write(f"{package}/__init__.py", "")
        announce = "SUMMARY = 'A module made for a test.'\n"
        if settings is not None:
            self.write(f"{package}/settings.toml", settings)
            announce += "SETTINGS = 'settings.toml'\n"
        self.write(f"{package}/module.py", announce)
        self.write(f"{package}/tunnel_decl.py", values)
        dist = dist or f"moxi-{name}"
        info = f"{dist.replace('-', '_')}-0.1.0.dist-info"
        self.write(f"{info}/METADATA", f"Metadata-Version: 2.1\nName: {dist}\nVersion: 0.1.0\n")
        lines = f"[moxi.modules]\n{name} = {package}.module\n\n[moxi.tunnel]\n"
        lines += "".join(f'{entry} = {package}.tunnel_decl:{attr}\n'
                         for entry, attr in (targets or {}).items())
        self.write(f"{info}/entry_points.txt", lines)
        return package


def drop_modules(tops: set[str]) -> None:
    for mod in list(sys.modules):
        if mod.split(".")[0] in tops:
            del sys.modules[mod]


@pytest.fixture
def site(tmp_path, monkeypatch):
    """A fresh site on sys.path for fake modules. While it is active, `importlib.metadata`
    finds only the site's distributions, `moxi` and `moxi-tunnel`, so the targets read are
    the site's alone."""
    from importlib.metadata import Distribution

    root = tmp_path / "site"
    root.mkdir()
    discover = Distribution.discover

    def filtered(cls, **kwargs):
        return (d for d in discover(**kwargs)
                if d.name in ("moxi", "moxi-tunnel") or Path(d.locate_file("")) == root)

    monkeypatch.setattr(Distribution, "discover", classmethod(filtered))
    monkeypatch.syspath_prepend(str(root))
    moxi.modules.reset()
    s = Site(root)
    yield s
    drop_modules(s.packages)
    moxi.modules.reset()


@pytest.fixture
def config_file():
    """`config_file(module, text)`: write module `module`'s config file."""
    from moxi.paths import Layout

    def write(module: str, text: str) -> Path:
        path = Layout(module).config_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text))
        return path

    return write

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
               settings: str | None = SETTINGS,
               targets_group: dict[str, str] | None = None) -> str:
        """A fake module `name` with a `settings.toml` and a `tunnel_decl.py` holding
        `values` (Python source); `targets` maps each `moxi.tunnel` entry name to the
        attribute of `tunnel_decl` it points at; `targets_group` the same for
        `moxi.tunnel.targets`."""
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
        if targets_group:
            lines += "\n[moxi.tunnel.targets]\n" + "".join(
                f"{entry} = {package}.tunnel_decl:{attr}\n"
                for entry, attr in targets_group.items())
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


#: Two fixture targets: `alpha/gateway` (own-devices, port setting `port`, 8461) and
#: `alpha/connector` (public, port setting `other.port`, 9000).
TARGETS = """
GATEWAY = {"reach": "own-devices", "port_setting": "port", "doc": "the page"}
CONNECTOR = {"reach": "public", "port_setting": "other.port"}
"""


@pytest.fixture(autouse=True)
def units(monkeypatch):
    """A fake `moxi.units` in place of the one tunnel's Cloudflare step calls, and of the
    unit apply `moxi.config.set` runs after a write of the daemon switch: no test reaches
    a supervisor."""
    import moxi.config
    from fake_units import FakeUnits

    from moxi_tunnel import cloudflare

    fake = FakeUnits()
    monkeypatch.setattr(cloudflare, "units", fake)
    monkeypatch.setattr(moxi.config, "apply_units", fake.apply_switch)
    return fake


#: The Cloudflare tunnel id the fixture sets.
TUNNEL_ID = "6ff42ae2-765d-4adf-8112-31c55c1551ef"


@pytest.fixture
def cf(tmp_path, monkeypatch, config_file):
    """Cloudflare set up: a tunnel id, a credentials file the user owns (mode 0000, so
    opening it would fail) at a path holding `#`, `: ` and a backslash, and a fake
    `cloudflared` on PATH. Returns the credentials path."""
    import os
    import stat

    creds = tmp_path / "cred dir" / "a #b: c\\d.json"
    creds.parent.mkdir()
    creds.write_text("{}")
    creds.chmod(0)
    bindir = tmp_path / "cfbin"
    bindir.mkdir()
    binary = bindir / "cloudflared"
    binary.write_text("#!/bin/sh\nexit 0\n")
    binary.chmod(stat.S_IRWXU)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}")
    config_file("tunnel", f"""
        [cloudflare]
        tunnel_id = "{TUNNEL_ID}"
        credentials = '{creds}'
        """)
    return creds


@pytest.fixture
def tailscale(tmp_path, monkeypatch):
    """A fake `tailscale` first on PATH, logged in with HTTPS and Funnel allowed."""
    from fake_tailscale import install

    return install(tmp_path / "bin", monkeypatch)


@pytest.fixture
def alpha(site):
    """The fixture module `alpha`, declaring `alpha/gateway` and `alpha/connector`."""
    site.module("alpha", {"alpha.gateway": "GATEWAY", "alpha.connector": "CONNECTOR"},
                values=TARGETS)
    return site


@pytest.fixture
def yes():
    """A confirm that says yes and keeps every list of lines it was shown."""
    class Yes:
        def __init__(self):
            self.asked: list[list[str]] = []

        def __call__(self, lines):
            from moxi_tunnel import locks

            locks.none_held()
            self.asked.append(list(lines))
            return True

    return Yes()

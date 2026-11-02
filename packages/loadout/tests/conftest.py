"""Fixtures for loadout's tests, on top of the test kit's fence.

The kit (`moxi_dev.testing`) already points `HOME`, `MOXI_HOME`, every `MOXI_<MODULE>_HOME`,
the `XDG_*` homes, `CLAUDE_CONFIG_DIR` and `CODEX_HOME` into the test's tmp and refuses a
write outside it. Here, for every test:

- git reads a scratch global config (`GIT_CONFIG_GLOBAL`) and no system one
  (`GIT_CONFIG_NOSYSTEM=1`);
- fake `claude` and `codex` programs come first on `PATH`; each appends its arguments to
  `calls.log` beside it and exits 0, so no test runs a real harness CLI;
- every harness home is asserted to be under the test's tmp before the test runs.

`site` is a fresh site of fake distributions on `sys.path` (a fake projects or repo, say).
While it is active only the site's distributions and `moxi` itself are seen.
"""

from __future__ import annotations

import os
import sys
import textwrap
from pathlib import Path

import pytest

import moxi.modules

#: The variables that place a harness's home.
HARNESS_HOMES = ("HOME", "CLAUDE_CONFIG_DIR", "CODEX_HOME", "XDG_CONFIG_HOME")
FAKE_CLIS = ("claude", "codex")


@pytest.fixture(autouse=True)
def _loadout_fence(tmp_path, monkeypatch, fence):
    """Git kept off the real config, fake harness CLIs on PATH, harness homes in tmp."""
    gitconfig = tmp_path / "gitconfig"
    gitconfig.write_text("[user]\n\tname = sample-user\n\temail = user@example.invalid\n"
                         "[init]\n\tdefaultBranch = main\n", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(gitconfig))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    bin_dir = tmp_path / "fake-bin"
    bin_dir.mkdir()
    for name in FAKE_CLIS:
        fake_cli(bin_dir, name)
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ.get("PATH", ""))
    for key in HARNESS_HOMES:
        value = os.environ.get(key)
        if value:
            assert under(value, fence.roots), f"{key}={value} is outside the test's tmp"


def under(path: str | os.PathLike, roots) -> bool:
    real = os.path.realpath(path)
    return any(real == str(r) or real.startswith(str(r).rstrip(os.sep) + os.sep)
               for r in roots)


def fake_cli(bin_dir: Path, name: str) -> Path:
    """A program `name` in `bin_dir` that appends its name and arguments to
    `bin_dir/calls.log` and exits 0."""
    log = bin_dir / "calls.log"
    path = bin_dir / name
    path.write_text(f'#!/bin/sh\nprintf "%s" "{name}" >> "{log}"\n'
                    f'for a in "$@"; do printf " %s" "$a" >> "{log}"; done\n'
                    f'printf "\\n" >> "{log}"\n', encoding="utf-8")
    path.chmod(0o755)
    return path


@pytest.fixture
def folder(tmp_path) -> Path:
    """A loadout folder of the test's own, not yet made."""
    return tmp_path / "loadout-folder"


# -- a site of fake distributions ----------------------------------------------------------

ANNOUNCEMENT_DEFAULTS = {
    "INTERFACES": "frozenset({1})",
    "API": "None",
    "CLI": "None",
    "SUMMARY": "'A module made for a test.'",
    "OPTIONAL": "{}",
    "GATED": "{}",
    "INTERNAL_ENV": "{}",
    "SETTINGS": "None",
    "SETUP": "None",
    "SEGMENT": "None",
}


def announcement(drop: tuple[str, ...] = (), **names: str) -> str:
    """The source of an announcement module; each keyword is a name's Python expression."""
    fields = {**ANNOUNCEMENT_DEFAULTS, **names}
    return "".join(f"{k} = {v}\n" for k, v in fields.items() if k not in drop)


def metadata(name: str, version: str) -> str:
    return f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n"


class Site:
    """A directory on sys.path where a test installs fake distributions."""

    def __init__(self, root: Path):
        self.root = root
        self.packages: set[str] = set()

    def write(self, relpath: str, text: str) -> Path:
        path = self.root / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text))
        top = relpath.split("/")[0].removesuffix(".py")
        if not top.endswith(".dist-info") and top not in self.packages:
            self.packages.add(top)
            # An earlier test may have imported the real package of this name; the site's
            # own must be the one a later import finds.
            drop_modules({top})
        return path

    def distribution(self, name: str, version: str = "0.1.0", *,
                     groups: dict[str, dict[str, str]] | None = None) -> None:
        info = f"{name.replace('-', '_')}-{version}.dist-info"
        self.write(f"{info}/METADATA", metadata(name, version))
        if groups:
            self.write(f"{info}/entry_points.txt", "".join(
                f"[{group}]\n" + "".join(f"{k} = {v}\n" for k, v in entries.items()) + "\n"
                for group, entries in groups.items()))

    def module(self, name: str, *, version: str = "0.1.0", files: dict[str, str] | None = None,
               drop: tuple[str, ...] = (), **names: str) -> str:
        """A whole fake module: its distribution, package, announcement and `files`
        (paths inside the package, with their text)."""
        package = f"moxi_{name}"
        self.write(f"{package}/__init__.py", "")
        self.write(f"{package}/module.py", announcement(drop, **names))
        for rel, text in (files or {}).items():
            self.write(f"{package}/{rel}", text)
        self.distribution(f"moxi-{name}", version,
                          groups={"moxi.modules": {name: f"{package}.module"}})
        return package


def see_only(root: Path, monkeypatch) -> None:
    """Let `importlib.metadata` and `moxi.modules.load` find only the distributions under
    `root`, and `moxi` itself."""
    from importlib.metadata import Distribution

    discover = Distribution.discover

    def seen(dist) -> bool:
        return dist.name == "moxi" or Path(dist.locate_file("")) == root

    def filtered(cls, **kwargs):
        return (d for d in discover(**kwargs) if seen(d))

    monkeypatch.setattr(Distribution, "discover", classmethod(filtered))

    # `load` reads a distribution's .dist-info directly, without `importlib.metadata`, so
    # it is held to the same view: what it finds outside `root` reads as not found.
    find = moxi.modules._DistInfo.find.__func__

    def find_seen(cls, module):
        dist = find(cls, module)
        return dist if dist is not None and Path(dist.path).parent == root else None

    monkeypatch.setattr(moxi.modules._DistInfo, "find", classmethod(find_seen))


@pytest.fixture
def site(tmp_path, monkeypatch):
    """A fresh site on `sys.path` for the test's fake distributions; while it is active
    every other real distribution is hidden."""
    root = tmp_path / "site"
    root.mkdir()
    see_only(root, monkeypatch)
    monkeypatch.syspath_prepend(str(root))
    moxi.modules.reset()
    s = Site(root)
    yield s
    moxi.modules.reset()
    drop_modules(s.packages)


def drop_modules(packages: set[str]) -> None:
    """Forget every imported module under `packages`, so the next import reads it anew."""
    for mod in list(sys.modules):
        if mod.split(".")[0] in packages:
            del sys.modules[mod]

"""The `fixture` module installed as a real distribution in a site directory, and the
files in its state directory that steer its page server."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

SOURCE = Path(__file__).resolve().parent / "fixture" / "moxi_fixture"


def install(site: Path, version: str = "0.1.0") -> Path:
    """Copy the fixture's package into `site` and write its `.dist-info` beside it."""
    site.mkdir(parents=True, exist_ok=True)
    target = site / "moxi_fixture"
    if not target.exists():
        shutil.copytree(SOURCE, target, ignore=shutil.ignore_patterns("__pycache__"))
    set_version(site, version)
    return site


def set_version(site: Path, version: str) -> None:
    """Give the installed fixture `version`, and mark `site` changed, so a shell's next
    snapshot sees the new version and drains a server of the old one."""
    for old in site.glob("moxi_fixture-*.dist-info"):
        shutil.rmtree(old)
    info = site / f"moxi_fixture-{version}.dist-info"
    info.mkdir()
    (info / "METADATA").write_text(f"Metadata-Version: 2.1\nName: moxi-fixture\n"
                                   f"Version: {version}\n")
    (info / "entry_points.txt").write_text("[moxi.modules]\nfixture = moxi_fixture.module\n")
    os.utime(site)


def fail_start(shell, on: bool = True) -> None:
    """While on, the fixture's page server exits 3 at its start."""
    flag = shell.state_dir("fixture") / "fail-start"
    if on:
        flag.parent.mkdir(parents=True, exist_ok=True)
        flag.write_text("")
    else:
        flag.unlink(missing_ok=True)


def acts(shell) -> int:
    """How many acts the fixture's page servers have run."""
    try:
        return len((shell.state_dir("fixture") / "acts").read_text().splitlines())
    except FileNotFoundError:
        return 0

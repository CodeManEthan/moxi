"""The suites' durations, kept per commit and compared with the newest one on `main`.

Each suite `moxi-dev check` runs writes the kit's `durations.json` (`moxi_dev.testing`):
every test's seconds by phase. The check reads them back and keeps one record per
commit at `<git common dir>/moxi-dev/durations/<sha>.json`, inside git's own directory,
so it is never tracked and every worktree of the clone shares it. A record holds, for
each package whose suite passed, whether every tier ran (`--full`) and each test's
seconds. A later run at the same commit replaces the packages it ran and keeps the rest.
A run on a tree with changes of its own, or one that ran a finding's tests only, keeps
no record: its times are not the commit's.

The comparison is with the newest record for a commit on `main` that is an ancestor of
HEAD (the commits from the merge base back), HEAD itself left out, over the packages
both ran the same way (every tier, or not).
"""

from __future__ import annotations

import json
import math
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

FORMAT = 1
#: The share of tests, slowest first, whose part of the total the summary gives.
SLOWEST = 0.05
PHASES = ("setup", "call", "teardown")


@dataclass(frozen=True)
class PackageRun:
    """One package's suite in one run: whether every tier ran, and each test's phases."""

    tiers: str                          # "all" (--full) or "plain"
    tests: Mapping[str, Mapping[str, Any]]

    def seconds(self) -> float:
        return sum(test_seconds(entry) for entry in self.tests.values())


@dataclass(frozen=True)
class Summary:
    total: float                # every test's seconds, all phases, every package
    slowest_share: float        # the slowest 5 % of tests' part of the total, 0 to 1
    tests: int


@dataclass(frozen=True)
class Delta:
    seconds: float              # this run minus the record, over `packages`
    commit: str                 # the record's commit
    packages: tuple[str, ...]   # the packages compared
    whole: bool                 # every package this run kept was compared


def test_seconds(entry: Mapping[str, Any]) -> float:
    return sum(float(entry.get(phase) or 0.0) for phase in PHASES)


def load_run(path: Path) -> dict[str, dict[str, Any]] | None:
    """The tests of a `durations.json` the kit wrote, or None when there is none."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    tests = data.get("tests") if isinstance(data, dict) else None
    return tests if isinstance(tests, dict) else None


def summarize(runs: Mapping[str, PackageRun]) -> Summary:
    times = sorted((test_seconds(entry) for run in runs.values()
                    for entry in run.tests.values()), reverse=True)
    total = sum(times)
    if not times or total <= 0:
        return Summary(total=total, slowest_share=0.0, tests=len(times))
    top = max(1, math.ceil(len(times) * SLOWEST))
    return Summary(total=total, slowest_share=sum(times[:top]) / total, tests=len(times))


def _git(root: Path, *args: str) -> str | None:
    proc = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True)
    return proc.stdout if proc.returncode == 0 else None


def records_dir(root: Path) -> Path | None:
    """`<git common dir>/moxi-dev/durations` of the checkout at `root`."""
    common = _git(root, "rev-parse", "--path-format=absolute", "--git-common-dir")
    return Path(common.strip()) / "moxi-dev" / "durations" if common else None


def read_record(path: Path) -> dict[str, PackageRun] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        packages = data["packages"]
        return {name: PackageRun(tiers=str(entry["tiers"]), tests=dict(entry["tests"]))
                for name, entry in packages.items()}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None


def keep(directory: Path, commit: str, runs: Mapping[str, PackageRun]) -> Path:
    """Write the commit's record: these packages' runs over any it already holds."""
    path = directory / f"{commit}.json"
    packages = dict(read_record(path) or {})
    packages.update(runs)
    directory.mkdir(parents=True, exist_ok=True)
    body = {"format": FORMAT, "commit": commit,
            "packages": {name: {"tiers": run.tiers, "tests": dict(run.tests)}
                         for name, run in sorted(packages.items())}}
    part = path.with_name(path.name + ".part")
    part.write_text(json.dumps(body, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(part, path)
    return path


def compare(runs: Mapping[str, PackageRun], record: Mapping[str, PackageRun]
            ) -> tuple[float, tuple[str, ...]]:
    """This run minus the record over the packages both ran the same way, and those
    packages."""
    same = tuple(name for name, run in runs.items()
                 if name in record and record[name].tiers == run.tiers)
    return (sum(runs[name].seconds() - record[name].seconds() for name in same), same)


def against_main(root: Path, directory: Path, runs: Mapping[str, PackageRun],
                 base: str = "main") -> Delta | None:
    """The delta against the newest ancestor record on `base` that has a package this run
    can be compared on; None when there is none (or no merge base)."""
    if not runs or not directory.is_dir():
        return None
    kept = {path.stem for path in directory.glob("*.json")}
    if not kept:
        return None
    head = (_git(root, "rev-parse", "HEAD") or "").strip()
    fork = (_git(root, "merge-base", base, "HEAD") or "").strip()
    if not fork:
        return None
    for sha in (_git(root, "rev-list", fork) or "").split():
        if sha == head or sha not in kept:
            continue
        record = read_record(directory / f"{sha}.json")
        if not record:
            continue
        seconds, same = compare(runs, record)
        if same:
            return Delta(seconds=seconds, commit=sha, packages=same,
                         whole=len(same) == len(runs))
    return None

"""The suites' durations, kept per commit and compared with the newest one on `main`.

Each suite `moxi-dev check` runs writes the kit's `durations.json` (`moxi_dev.testing`):
every test's seconds by phase. The check reads them back and keeps one record per
commit at `<git common dir>/moxi-dev/durations/<sha>.json`, inside git's own directory,
so it is never tracked and every worktree of the clone shares it. A record holds, for
each package whose suite passed, the tiers it ran and each test's seconds. The tiers are
`all` for `--full`, `plain` for a run that put no tier test back, and `plain+<tier>...`
for one whose changed paths put some back (`plain+slow`), from the tests the kit marks
`lifted`. A later run at the same commit replaces the packages it ran and keeps the rest.
A run on a tree with changes of its own, or one that ran a finding's tests only, keeps
no record: its times are not the commit's. A package's `timed` tests run in the check's
serial pass, after its parallel one; their times join the package's in the same record,
and the package counts as passed only when both passes did.

Two checks can keep the same commit's record at once, from two worktrees of one clone.
Each writes its own part file and replaces the record with it, and on a system with
`fcntl` the read, merge and write hold a lock on `.lock` beside the records, so neither
drops the packages the other kept. Where `fcntl` is missing the last writer's packages
win.

The comparison is with the newest record for a commit on `main` that is an ancestor of
HEAD (the commits from the merge base back), HEAD itself left out, over the packages
both ran with the same tiers, so a tier test a change put back is not read as a
slowdown.
"""

from __future__ import annotations

import contextlib
import json
import math
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Mapping

try:
    import fcntl
except ImportError:                     # not on Windows: no lock (module doc)
    fcntl = None

FORMAT = 1
#: The share of tests, slowest first, whose part of the total the summary gives.
SLOWEST = 0.05
PHASES = ("setup", "call", "teardown")


@dataclass(frozen=True)
class PackageRun:
    """One package's suite in one run: whether every tier ran, and each test's phases."""

    tiers: str                          # "all" (--full), "plain" or "plain+<tier>..."
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


def tiers_of(tests: Mapping[str, Mapping[str, Any]], *, full: bool) -> str:
    """The tiers a run ran: `all` under `--full`, else `plain` and the tiers of every test
    the kit put back (module doc)."""
    if full:
        return "all"
    lifted = {tier for entry in tests.values() if isinstance(entry, Mapping)
              for tier in entry.get("lifted") or () if isinstance(tier, str)}
    return "+".join(["plain", *sorted(lifted)])


def load_run(path: Path) -> dict[str, dict[str, Any]] | None:
    """The tests of a `durations.json` the kit wrote, or None when there is none."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    tests = data.get("tests") if isinstance(data, dict) else None
    return tests if isinstance(tests, dict) else None


def load_held(path: Path) -> list[str]:
    """The `timed` tests a run held back (`MOXI_TEST_TIMED=hold`), as its `durations.json`
    names them; none when it names none or there is no file."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    held = data.get("held") if isinstance(data, dict) else None
    return [nodeid for nodeid in held if isinstance(nodeid, str)] \
        if isinstance(held, list) else []


def merged(first: Mapping[str, Mapping[str, Any]] | None,
           second: Mapping[str, Mapping[str, Any]] | None) -> dict[str, Any] | None:
    """One package's tests from its two passes (the parallel one and the serial timed
    one), or None when neither wrote any."""
    if first is None and second is None:
        return None
    return {**(first or {}), **(second or {})}


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


@contextlib.contextmanager
def _locked(directory: Path) -> Iterator[None]:
    """Hold the records' lock, where the system has `fcntl` (module doc)."""
    if fcntl is None:
        yield
        return
    fd = os.open(directory / ".lock", os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)                    # closing the descriptor releases the lock


def keep(directory: Path, commit: str, runs: Mapping[str, PackageRun]) -> Path:
    """Write the commit's record: these packages' runs over any it already holds. An
    `OSError` (a read-only or full disk) reaches the caller, and no part file is left."""
    path = directory / f"{commit}.json"
    directory.mkdir(parents=True, exist_ok=True)
    with _locked(directory):
        packages = dict(read_record(path) or {})
        packages.update(runs)
        body = {"format": FORMAT, "commit": commit,
                "packages": {name: {"tiers": run.tiers, "tests": dict(run.tests)}
                             for name, run in sorted(packages.items())}}
        fd, part = tempfile.mkstemp(dir=directory, prefix=f"{commit}.", suffix=".part")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as out:
                out.write(json.dumps(body, indent=1, sort_keys=True) + "\n")
            os.replace(part, path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(part)
            raise
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

"""The timed record: each machine's results per commit, and `moxi-dev timed take`.

`<record dir>/<machine>/<sha>.json` holds one machine's results for one commit, and
`<record dir>/<machine>/ticks/<time>.json` one file per scheduled tick
(`moxi_dev.tick`). The record dir is `<git common dir>/moxi-dev/timed` in a clone, or
the one `--record-dir` names. Each machine writes only its own directory. A commit file
is written as the durations record writes its own: read, merged and written under the
lock on `.lock` beside it (`moxi_dev.durations`), part file then replace.

A commit file holds `format`, `commit`, `machine`; `runs`, one entry per run (its start
time in UTC, the machine's system, CPU model and CPU count, Python's version, the gauge,
the line, the gauge's first sample, the seconds waited, how many wall-clock tests were
measured, the slack factor, the exit code, `scheduled` or `hand`, and whether it was
complete); `collected`, the node ids of every timed test the newest complete run
collected, with `collected_run`, that run's start time; and `tests`, per node id and
within it per slack factor (`factor_key`), the newest result by the merge rule: package,
outcome, clock, gauge, other work over its window, line, reading, budget, unit, failure
text and the start time of the run it came from.

A complete run is one given no `--package` and no `--finding` whose every session
collected without error; only it writes `collected`.

The merge rule, within one test and one factor (`merge_result`): a measured outcome
(`passed` or `failed`) replaces any earlier outcome; a skip replaces `not measured` and an
earlier skip; `not measured` replaces only an earlier `not measured`. Results at
different factors are kept apart.

The rule a result settles a test by (`counts`): a pass counts when taken at or under the
machine's set factor, a failure when taken at or over it, and a skip at any factor.

`take DIR --machine NAME` merges a host's record, brought to this machine by a synced
folder, into this machine's `<record dir>/NAME/`, per commit, never wiping what is here:
a file this record lacks is added; a file both hold is merged (runs united by start
time, test results merged by the rule in that order, `collected` from the file whose
`collected_run` is later); a file only this record holds is kept. A tick file this
record lacks is added and one it holds is never changed. Only names of the form
`<sha>.json` and `ticks/YYYYMMDDTHHMMSSZ.json` are read, so a part file or a sync's
temporary file is never taken. Every file is parsed whole; one that doesn't parse, whose
`machine` isn't NAME or whose `format` isn't known is named and left, the rest merge,
and the exit is 2. NAME equal to this machine's own name exits 2. A source that doesn't
exist or holds no files exits 0 with `nothing to take`; a source whose parent, the
results folder, is gone exits 2. `take` takes no run lock: it measures nothing, and
inside a scheduled tick its parent holds that lock.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import platform
import re
import sys
import tempfile
from pathlib import Path
from collections.abc import Mapping
from typing import Any

from moxi import paths

from . import durations

FORMAT = 1
#: The tick files' format this moxi-dev reads; `moxi_dev.tick` writes the same number.
TICK_FORMAT = 1
MEASURED = ("passed", "failed")
SKIPPED = "skipped"
NOT_MEASURED = "not measured"
#: A machine's name as a record files it: letters, digits and `._-`.
MACHINE_NAME = re.compile(r"[A-Za-z0-9._-]+")
COMMIT_FILE = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?\.json")
TICK_FILE = re.compile(r"[0-9]{8}T[0-9]{6}Z\.json")
TICKS = "ticks"


class RecordError(Exception):
    """A record file that can't be read or kept; the message says which and why."""


def default_machine() -> str:
    """The short host name: `platform.node()` up to its first dot, filled through
    `moxi.paths.uname`, which makes no WMI query on Windows."""
    paths.uname()
    return platform.node().split(".", 1)[0] or "unknown"


def factor_key(factor: float) -> str:
    """A slack factor as a record's key: `1`, `3.1`."""
    return format(float(factor), "g")


def counts(outcome: str, factor: float, set_factor: float) -> bool:
    """Whether a result taken at `factor` settles its test on a machine set at
    `set_factor` (module doc)."""
    if outcome == "passed":
        return factor <= set_factor
    if outcome == "failed":
        return factor >= set_factor
    return outcome == SKIPPED


def merge_result(older: Mapping[str, Any] | None, newer: Mapping[str, Any]
                 ) -> Mapping[str, Any]:
    """The result kept when `newer` follows `older` at one test and factor (module doc)."""
    if older is None:
        return newer
    new, old = newer.get("outcome"), older.get("outcome")
    if new in MEASURED:
        return newer
    if new == SKIPPED:
        return newer if old in (NOT_MEASURED, SKIPPED) else older
    return newer if old == NOT_MEASURED else older


def empty(commit: str, machine: str) -> dict[str, Any]:
    return {"format": FORMAT, "commit": commit, "machine": machine, "runs": [],
            "tests": {}}


def read_file(path: Path) -> dict[str, Any] | None:
    """A commit file, or None when there is none; RecordError when it doesn't parse or
    its format isn't known."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        raise RecordError(f"{path}: doesn't parse ({exc})") from exc
    if not isinstance(data, dict):
        raise RecordError(f"{path}: doesn't parse (not an object)")
    if data.get("format") != FORMAT:
        raise RecordError(f"{path}: format {data.get('format')!r} is not one this "
                          f"moxi-dev knows ({FORMAT})")
    if not (isinstance(data.get("runs"), list) and isinstance(data.get("tests"), dict)
            and isinstance(data.get("commit"), str) and isinstance(data.get("machine"), str)):
        raise RecordError(f"{path}: doesn't parse (a field is missing)")
    return data


def merge_files(mine: Mapping[str, Any], theirs: Mapping[str, Any]) -> dict[str, Any]:
    """Two files of one commit and machine merged (module doc, `take`)."""
    runs = {run.get("started"): run for run in [*mine["runs"], *theirs["runs"]]}
    order = {started: n for n, started in enumerate(sorted(runs, key=str))}
    merged = {**mine, "runs": [runs[started] for started in sorted(runs, key=str)],
              "tests": {}}
    for nodeid in sorted(set(mine["tests"]) | set(theirs["tests"])):
        by_factor: dict[str, Any] = {}
        for factor in set(mine["tests"].get(nodeid, {})) | set(theirs["tests"].get(nodeid, {})):
            found = [one for one in (mine["tests"].get(nodeid, {}).get(factor),
                                     theirs["tests"].get(nodeid, {}).get(factor)) if one]
            found.sort(key=lambda one: order.get(one.get("run"), -1))
            kept = None
            for one in found:
                kept = merge_result(kept, one)
            by_factor[factor] = kept
        merged["tests"][nodeid] = by_factor
    newer = max((mine, theirs), key=lambda one: str(one.get("collected_run") or ""))
    merged.pop("collected", None)
    merged.pop("collected_run", None)
    if newer.get("collected") is not None:
        merged["collected"] = newer["collected"]
        merged["collected_run"] = newer["collected_run"]
    return merged


def add_run(data: Mapping[str, Any], run: Mapping[str, Any],
            results: Mapping[str, Mapping[str, Any]],
            collected: list[str] | None) -> dict[str, Any]:
    """`data` with one more run: its entry, each test's result at the run's factor merged
    in, and `collected` when the run was complete."""
    key = factor_key(run["slack"])
    tests = {nodeid: dict(by_factor) for nodeid, by_factor in data["tests"].items()}
    for nodeid, result in results.items():
        entry = tests.setdefault(nodeid, {})
        entry[key] = merge_result(entry.get(key), {**result, "run": run["started"]})
    out = {**data, "runs": [*data["runs"], dict(run)], "tests": tests}
    if collected is not None:
        out["collected"] = sorted(collected)
        out["collected_run"] = run["started"]
    return out


def _write(path: Path, data: Mapping[str, Any]) -> None:
    fd, part = tempfile.mkstemp(dir=path.parent, prefix=f"{path.stem}.", suffix=".part")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(data, indent=1, sort_keys=True) + "\n")
        os.replace(part, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(part)
        raise


def keep(record_dir: Path, machine: str, commit: str, run: Mapping[str, Any],
         results: Mapping[str, Mapping[str, Any]], collected: list[str] | None) -> Path:
    """Add one run to the machine's file for `commit`, under the record's lock."""
    machine_dir = record_dir / machine
    machine_dir.mkdir(parents=True, exist_ok=True)
    path = machine_dir / f"{commit}.json"
    with durations.locked(machine_dir):
        data = read_file(path) or empty(commit, machine)
        _write(path, add_run(data, run, results, collected))
    return path


def machines(record_dir: Path) -> list[str]:
    """The machines with a directory in the record, by name."""
    if not record_dir.is_dir():
        return []
    return sorted(entry.name for entry in record_dir.iterdir()
                  if entry.is_dir() and MACHINE_NAME.fullmatch(entry.name))


def commit_files(machine_dir: Path) -> dict[str, Path]:
    """Each commit file of a machine's directory, by commit."""
    if not machine_dir.is_dir():
        return {}
    return {path.name[:-5]: path for path in machine_dir.iterdir()
            if COMMIT_FILE.fullmatch(path.name) and path.is_file()}


def tick_files(machine_dir: Path) -> list[Path]:
    """A machine's tick files, oldest first."""
    ticks = machine_dir / TICKS
    if not ticks.is_dir():
        return []
    return sorted(path for path in ticks.iterdir()
                  if TICK_FILE.fullmatch(path.name) and path.is_file())


def read_tick(path: Path) -> dict[str, Any]:
    """A tick file; RecordError when it doesn't parse or its format isn't known."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RecordError(f"{path}: doesn't parse ({exc})") from exc
    if not isinstance(data, dict):
        raise RecordError(f"{path}: doesn't parse (not an object)")
    if data.get("format") != TICK_FORMAT:
        raise RecordError(f"{path}: format {data.get('format')!r} is not one this "
                          f"moxi-dev knows ({TICK_FORMAT})")
    return data


def take(source: Path, record_dir: Path, machine: str, *, own: str) -> tuple[int, list[str]]:
    """Merge `source`, a host's machine directory, into `record_dir/machine` (module doc);
    the exit code and the lines to print."""
    if machine == own:
        return 2, [(f"{machine} is this machine's own name; take merges another "
                    f"machine's record")]
    if not source.parent.is_dir():
        return 2, [f"the results folder {source.parent} is gone"]
    commits = commit_files(source)
    ticks = tick_files(source)
    if not commits and not ticks:
        return 0, [f"nothing to take from {source}"]
    target = record_dir / machine
    (target / TICKS).mkdir(parents=True, exist_ok=True)
    said, bad, taken = [], [], 0
    for commit, path in sorted(commits.items()):
        try:
            theirs = read_file(path)
            if theirs is None:
                continue
            if theirs["machine"] != machine or theirs["commit"] != commit:
                raise RecordError(f"{path}: names machine {theirs['machine']!r} and "
                                  f"commit {theirs['commit'][:12]}, not {machine!r} and "
                                  f"{commit[:12]}")
            with durations.locked(target):
                mine = read_file(target / path.name)
                _write(target / path.name,
                       theirs if mine is None else merge_files(mine, theirs))
            taken += 1
        except (RecordError, OSError) as exc:
            bad.append(str(exc))
    for path in ticks:
        try:
            data = read_tick(path)
            if data.get("machine") != machine:
                raise RecordError(f"{path}: names machine {data.get('machine')!r}, not "
                                  f"{machine!r}")
            kept = target / TICKS / path.name
            if kept.exists():
                continue
            with durations.locked(target):
                if not kept.exists():
                    _write(kept, data)
            taken += 1
        except (RecordError, OSError) as exc:
            bad.append(str(exc))
    said.append(f"took {taken} file(s) from {source} into {target}")
    said += [f"not taken: {why}" for why in bad]
    return (2 if bad else 0), said


def take_main(argv: list[str], record_dir: Path | None) -> int:
    parser = argparse.ArgumentParser(
        prog="moxi-dev timed take",
        description="Merge a host's timed record, brought here by a synced folder, into "
                    "this clone's record under the host's name, per commit; nothing here "
                    "is removed.")
    parser.add_argument("source", metavar="DIR",
                        help="the host's machine directory, inside its results folder")
    parser.add_argument("--machine", metavar="NAME", required=True,
                        help="the host's name, as its record files it")
    parser.add_argument("--record-dir", metavar="DIR",
                        help="the record to merge into (default: this clone's)")
    args = parser.parse_args(argv)
    if not MACHINE_NAME.fullmatch(args.machine):
        print(f"moxi-dev timed take: --machine takes letters, digits and ._-; "
              f"{args.machine!r} is not one", file=sys.stderr)
        return 2
    target = Path(args.record_dir) if args.record_dir else record_dir
    if target is None:
        print("moxi-dev timed take: not in a git checkout; pass --record-dir",
              file=sys.stderr)
        return 2
    code, said = take(Path(args.source).expanduser(), target, args.machine,
                      own=default_machine())
    for line in said:
        print(f"moxi-dev timed take: {line}", file=sys.stderr if code else sys.stdout)
    return code

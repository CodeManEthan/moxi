"""`moxi-dev timed show [REV] [--days N] [--machine NAME] [--package NAME] [--json]`: read
the timed record.

It walks REV's first-parent line (`main` by default) back N days (7 by default) from
REV's own commit time, and reports each machine with a record on the walk on its own: a
machine's results never settle another's tests. The machine it runs on comes first, then
the others by name; `--machine` picks one.

Per machine: its set factor, the factor of its newest scheduled run on the walk, 1 with
none; a result counts by `moxi_dev.timed_record.counts` against it. The tests reported
are the `collected` list of the machine's newest record on the walk that has one; with
none, the machine has no complete run in the window and its outcomes are listed as
found. For each test, the newest counted outcome on the walk, its reading and budget,
commit and time, and `slack <f>` when taken at a factor other than the set one; for a
failure, the range from the last commit where it passed to the first where it failed;
for a test with no counted outcome, the last gauge reading that kept it out and any
result kept that didn't count. Then the machine's newest tick: its time, exit, first
failure line or not-settled cause, and its tick version beside this moxi-dev's
(`moxi_dev.tick.TICK_VERSION`), marked `run schedule again on <machine>` when older.

A machine's verdict is 1 when a test's newest counted outcome failed, else 3 when a test
of its set has no counted outcome, else 0. A machine with no complete run has no set: a
counted failure still makes it 1, and otherwise it reads `no complete run` and stays out
of the exit. The exit is the worst verdict that counts, 1 over 3 over 0, and 3 when no
machine has a complete run and none failed.

Before the machines, the strict line: how many tests of the set (this machine's, or the
newest other machine's when this one has none) this machine, which measures the strict
budget, has a counted outcome for. With `--machine` naming another machine, the reading
leaves this one out, so there is no strict line (`--json`'s `strict` is null).

`--package NAME` reads one package's tests, as a release reads its own: NAME is the
package's directory under `packages/` or its distribution name, as `moxi-dev timed
--package` takes it, resolved against the workspace of the tree `show` runs in; a name
naming no package there exits 2. Results are picked by each result's `package` field,
the directory name at the run, never by the node id, so no name reads results filed
under a directory that tree no longer has. Every part of the reading counts that
package's tests and no other's: each machine's set is its `collected` list cut to the
ids whose results name the package, a machine with no complete run lists only that
package's outcomes, a failure lifts the exit only when it is that package's, a
machine's newest measured commit is its newest record on the walk holding one of that
package's results (`no result of <name> on the walk` with none), and the strict line
counts that package's tests alone. When the set the strict line reads holds
none of the package's tests, the line in its place is `<name> has no timed test
collected by a complete run on the walk to <commit>; nothing to read`. A machine whose
cut set is empty has nothing unsettled and reads 0; with no complete run on any machine
the exit is 3, as without `--package`.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from collections.abc import Sequence
from typing import Any

from . import check, timed, timed_record

DAY_S = 86400


class ShowError(Exception):
    """The record can't be read; the message says why (exit 2)."""


def walk(root: Path, rev: str, days: float) -> list[str]:
    """REV's first-parent commits back `days` from REV's own time, newest first."""
    proc = subprocess.run(["git", "log", "--first-parent", "--format=%H %ct", rev, "--"],
                          cwd=root, capture_output=True, encoding="utf-8", check=False)
    if proc.returncode != 0:
        raise ShowError(f"git log {rev}: {proc.stderr.strip()}")
    rows = [line.split() for line in proc.stdout.splitlines() if line.strip()]
    if not rows:
        return []
    since = int(rows[0][1]) - days * DAY_S
    return [sha for sha, when in rows if int(when) >= since]


def _counted(data: dict[str, Any], nodeid: str, set_factor: float
             ) -> tuple[float, dict[str, Any]] | None:
    """The newest counted result of a test in one commit file, with its factor."""
    found = [(float(key), result) for key, result in data["tests"].get(nodeid, {}).items()
             if timed_record.counts(result.get("outcome"), float(key), set_factor)]
    return max(found, key=lambda one: str(one[1].get("run")), default=None)


def package_only(data: dict[str, Any], package: str) -> dict[str, Any]:
    """A commit file cut to the results whose `package` is `package` (a directory name),
    its `collected` list to the ids whose results in the file name it."""
    tests = {}
    for nodeid, by_factor in data["tests"].items():
        mine = {key: result for key, result in by_factor.items()
                if result.get("package") == package}
        if mine:
            tests[nodeid] = mine
    cut = {**data, "tests": tests}
    if data.get("collected") is not None:
        cut["collected"] = [nodeid for nodeid in data["collected"] if nodeid in tests]
    return cut


def machine_report(name: str, machine_dir: Path, walk_shas: Sequence[str],
                   package: str | None = None) -> dict[str, Any] | None:
    """One machine's report over the walk (module doc), cut to `package`'s results when
    given; None when it has no record on it."""
    files = timed_record.commit_files(machine_dir)
    records, unreadable = [], []
    for sha in walk_shas:
        if sha in files:
            try:
                data = timed_record.read_file(files[sha])
            except timed_record.RecordError as exc:
                unreadable.append(str(exc))
                continue
            if data is not None:
                records.append((sha, data))
    if not records:
        return None
    if package is not None:
        records = [(sha, package_only(data, package)) for sha, data in records]
    scheduled = [run for _, data in records for run in data["runs"]
                 if run.get("kind") == "scheduled"]
    newest_scheduled = max(scheduled, key=lambda run: str(run.get("started")), default=None)
    set_factor = float(newest_scheduled["slack"]) if newest_scheduled else 1.0
    with_set = next((data for _, data in records if data.get("collected") is not None), None)
    complete = with_set is not None
    tests = list(with_set["collected"]) if complete else sorted(
        {nodeid for _, data in records for nodeid in data["tests"]})
    reported, verdict_failed, unsettled = [], False, False
    for nodeid in tests:
        row: dict[str, Any] = {"test": nodeid}
        newest = None
        for index, (sha, data) in enumerate(records):
            hit = _counted(data, nodeid, set_factor)
            if hit is not None:
                newest = (index, sha, hit)
                break
        if newest is None:
            unsettled = True
            row["outcome"] = None
            kept = [(float(key), result) for _, data in records
                    for key, result in data["tests"].get(nodeid, {}).items()]
            gauged = [result.get("other") for _, result in sorted(
                kept, key=lambda one: str(one[1].get("run")), reverse=True)
                if result.get("outcome") == timed_record.NOT_MEASURED]
            row["last_other"] = gauged[0] if gauged else None
            row["kept"] = [{"outcome": result.get("outcome"), "slack": factor}
                           for factor, result in kept
                           if result.get("outcome") in timed_record.MEASURED]
            reported.append(row)
            continue
        index, sha, (factor, result) = newest
        row.update(outcome=result.get("outcome"), reading=result.get("reading"),
                   budget=result.get("budget"), unit=result.get("unit"), commit=sha,
                   run=result.get("run"), slack=factor)
        if result.get("outcome") == "failed":
            verdict_failed = True
            first_fail, last_pass = sha, None
            for older_sha, older in records[index + 1:]:
                hit = _counted(older, nodeid, set_factor)
                if hit is None:
                    continue
                if hit[1].get("outcome") == "failed":
                    first_fail = older_sha
                elif hit[1].get("outcome") == "passed":
                    last_pass = older_sha
                    break
            row.update(first_failed=first_fail, last_passed=last_pass)
        reported.append(row)
    if verdict_failed:
        verdict: int | None = 1
    elif not complete:
        verdict = None
    else:
        verdict = 3 if unsettled else 0
    ticks = timed_record.tick_files(machine_dir)
    tick = None
    if ticks:
        try:
            tick = timed_record.read_tick(ticks[-1])
        except timed_record.RecordError as exc:
            unreadable.append(str(exc))
    if package is None:
        newest_sha: str | None = records[0][0]
    else:
        newest_sha = next((sha for sha, data in records if data["tests"]), None)
    behind = None if newest_sha is None else list(walk_shas).index(newest_sha)
    return {"machine": name, "set_factor": set_factor, "complete": complete,
            "newest_commit": newest_sha, "behind": behind,
            "tests": reported, "tick": tick, "verdict": verdict,
            "unreadable": unreadable}


def strict_line(own: str, reports: Sequence[dict[str, Any]], tip: str,
                package: str | None = None) -> str:
    """How much of the test set this machine's strict reading covers; with `package`, the
    name given, the no-timed-test line when that set holds none of its tests."""
    mine = next((report for report in reports if report["machine"] == own), None)
    sets = [report for report in reports if report["complete"]]
    if mine is not None and mine["complete"]:
        base = mine
    else:
        base = min(sets, key=lambda report: (report["behind"] is None,
                                             report["behind"] or 0), default=mine)
    total = len(base["tests"]) if base else 0
    if package is not None and not total:
        return (f"{package} has no timed test collected by a complete run on the walk to "
                f"{tip[:12]}; nothing to read")
    covered = 0
    if mine is not None:
        counted = {row["test"] for row in mine["tests"] if row.get("outcome")}
        covered = sum(1 for row in (base["tests"] if base else []) if row["test"] in counted)
    if not covered:
        return (f"no strict reading: {own} measured no timed test on the walk to "
                f"{tip[:12]}; the hosts' readings below are at their own factors")
    return (f"{own}'s strict reading covers {covered} of {total} tests on the walk to "
            f"{tip[:12]}; the other {total - covered} are read only at the hosts' factors")


def exit_code(reports: Sequence[dict[str, Any]]) -> int:
    verdicts = [report["verdict"] for report in reports if report["verdict"] is not None]
    if 1 in verdicts:
        return 1
    if 3 in verdicts or not verdicts:
        return 3
    return 0


def _tick_lines(name: str, tick: dict[str, Any] | None) -> list[str]:
    from . import tick as tick_module
    if tick is None:
        return ["  newest tick: none"]
    text = f"  newest tick {tick.get('started')}: exit {tick.get('exit')}"
    if tick.get("not_settled"):
        text += f"; not measured: tree not settled: {tick['not_settled']}"
    elif tick.get("failure"):
        text += f"; {tick['failure']}"
    version = tick.get("tick_version")
    text += f"; tick {version}, this moxi-dev's {tick_module.TICK_VERSION}"
    if not isinstance(version, int) or version < tick_module.TICK_VERSION:
        text += f"; run schedule again on {name}"
    return [text]


def _reading(row: dict[str, Any]) -> str:
    if row.get("reading") is None:
        return "no reading"
    return f"{row['reading']:g} of {row['budget']:g} {row.get('unit') or ''}".rstrip()


def strict_for(own: str, reports: Sequence[dict[str, Any]], tip: str,
               machine: str | None, package: str | None = None) -> str | None:
    """The strict line when the reading includes this machine (no `--machine`, or
    `--machine` naming it) and holds a report; else None."""
    if not reports or machine not in (None, own):
        return None
    return strict_line(own, reports, tip, package)


def render(reports: Sequence[dict[str, Any]], rev: str, tip: str, days: float,
           own: str, *, machine: str | None = None, package: str | None = None
           ) -> list[str]:
    head = f"moxi-dev timed show: {rev} ({tip[:12]}), back {days:g} days"
    if not reports:
        return [f"{head}: no record on the walk"]
    lines = [head]
    strict = strict_for(own, reports, tip, machine, package)
    if strict is not None:
        lines.append(strict)
    for report in reports:
        name, set_factor = report["machine"], report["set_factor"]
        if report["newest_commit"] is None:
            lines.append(f"{name}: set factor {set_factor:g} | no result of {package} on "
                         f"the walk")
        else:
            lines.append(f"{name}: set factor {set_factor:g} | newest measured commit "
                         f"{report['newest_commit'][:12]}, {report['behind']} commits "
                         f"behind")
        lines += _tick_lines(name, report["tick"])
        if not report["complete"]:
            lines.append("  no complete run in the window; outcomes found:")
        for row in report["tests"]:
            if row["outcome"] is None:
                text = f"  not measured lately: {row['test']}"
                if row["last_other"] is not None:
                    text += f" (last gauge {row['last_other']:g})"
                if row["kept"]:
                    text += " (kept, not counted: " + ", ".join(
                        f"{kept['outcome']} at slack {kept['slack']:g}"
                        for kept in row["kept"]) + ")"
                lines.append(text)
                continue
            text = (f"  {row['outcome']}: {row['test']}, {_reading(row)}, at "
                    f"{row['commit'][:12]} {row['run']}")
            if row["slack"] != set_factor:
                text += f", slack {row['slack']:g}"
            if row["outcome"] == "failed":
                since = row["last_passed"][:12] if row["last_passed"] else "none on the walk"
                text += (f"; failing since {row['first_failed'][:12]}, last passed at "
                         f"{since}")
            lines.append(text)
        for why in report["unreadable"]:
            lines.append(f"  not read: {why}")
        verdict = report["verdict"]
        lines.append(f"  verdict: {'no complete run' if verdict is None else verdict}")
    return lines


def package_dir(root: Path, name: str) -> str:
    """The directory under `packages/` of the workspace package `name` names, in the
    workspace of the tree at `root`."""
    workspace = timed.workspace_root(root)
    if workspace is None:
        raise ShowError(f"--package reads the workspace of the tree it runs in; {root} is "
                        f"under no uv workspace root")
    try:
        (package,) = timed.chosen_packages(check.load_workspace(workspace), [name])
    except (check.CheckError, timed.TimedError) as exc:
        raise ShowError(str(exc)) from exc
    return package.dir


def show(root: Path, record_dir: Path, rev: str = "main", *, days: float = 7,
         machine: str | None = None, own: str | None = None, package: str | None = None
         ) -> tuple[int, list[dict[str, Any]], str]:
    """The reports, each machine on its own and cut to `package` when given (module doc),
    the exit code and REV's sha."""
    own = own or timed_record.default_machine()
    cut = package_dir(root, package) if package is not None else None
    shas = walk(root, rev, days)
    names = [machine] if machine else timed_record.machines(record_dir)
    names = sorted(names, key=lambda name: (name != own, name))
    reports = [report for name in names
               if (report := machine_report(name, record_dir / name, shas, cut)) is not None]
    return exit_code(reports), reports, (shas[0] if shas else rev)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="moxi-dev timed show",
        description="Read the timed record along REV's first-parent line, each machine on "
                    "its own. Exit 0: every test passed; 1: one failed; 3: some test has no "
                    "counted outcome in the window; 2: the record couldn't be read.")
    parser.add_argument("rev", nargs="?", default="main", metavar="REV")
    parser.add_argument("--days", type=float, default=7.0, metavar="N",
                        help="how far back the walk goes from REV's time (default 7)")
    parser.add_argument("--machine", metavar="NAME", help="report this machine only")
    parser.add_argument("--package", metavar="NAME",
                        help="read only this package's tests: its directory under packages/ "
                             "or its distribution name")
    parser.add_argument("--record-dir", metavar="DIR",
                        help="the record to read (default: this clone's)")
    parser.add_argument("--json", action="store_true", help="print the reports as JSON")
    args = parser.parse_args(argv)
    root = Path.cwd()
    record_dir = Path(args.record_dir) if args.record_dir else timed.common_record_dir(root)
    if record_dir is None:
        print("moxi-dev timed show: not in a git checkout", file=sys.stderr)
        return 2
    own = timed_record.default_machine()
    try:
        code, reports, tip = show(root, record_dir, args.rev, days=args.days,
                                  machine=args.machine, own=own, package=args.package)
    except ShowError as exc:
        print(f"moxi-dev timed show: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps({"rev": args.rev, "commit": tip, "exit": code,
                          "package": args.package,
                          "strict": strict_for(own, reports, tip, args.machine, args.package),
                          "machines": reports}, indent=1, sort_keys=True))
    else:
        for line in render(reports, args.rev, tip, args.days, own, machine=args.machine,
                           package=args.package):
            print(line)
        print(f"moxi-dev timed show: exit {code}")
    return code

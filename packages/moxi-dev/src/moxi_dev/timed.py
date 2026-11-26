"""`moxi-dev timed`: run the timed tests on their own, gated on how busy the machine is.

Run from the repository's root as `uv run --locked moxi-dev timed [--wait-s S]
[--idle-line CPUS] [--finding L<n>]... [--package NAME]... [--machine NAME]
[--record-dir DIR] [--no-record] [--scheduled]`. Its other verbs: `show` reads the
record (`moxi_dev.timed_show`), `take` merges a host's record into this one
(`moxi_dev.timed_record`), and `schedule` and `unschedule` write and remove a machine's
timer (`moxi_dev.schedule`).

The tree it measures is a git checkout, or a tree with no git that a scheduled tick
copied from a synced source (`moxi_dev.tick`). Outside git the root is the nearest
directory up whose `pyproject.toml` declares the uv workspace, the commit is the one in
`.moxi-dev/timed/commit.json`, which only a tick writes once the tree verified against its
stamp, and the record needs `--record-dir`; with no `commit.json` or no `--record-dir` the
run exits 2 before measuring. In order:

1. Take the run lock, before anything else touches the tree: `.run-lock` in
   `<git common dir>/moxi-dev/timed/` in a clone, or in the tree's own
   `.moxi-dev/timed/` outside git. A run never waits for it: held, it prints
   "another timed run is measuring in this clone" and exits 2. A tick that changed the
   tree first holds the lock on a descriptor its run inherits and names it in
   `MOXI_TEST_TIMED_LOCK_FD`; the run locks that descriptor, and exits 2 when it isn't
   open on this tree's lock file.
2. With `--scheduled`: when this machine's record for the commit has a `collected` list
   and every test on it is settled at this run's factor (`moxi_dev.timed_record.counts`),
   print `already measured` and exit 0.
3. `uv lock --check`, then sync the verb's own environment, `.moxi-dev/timed-env-3.12`,
   with every package, extra and group. A stale lock or a failed sync exits 2. The check's
   environment and run directory are never touched.
4. For each package (or each `--package`), in the workspace's order, one pytest session
   with no workers, `MOXI_TEST_TIMED=only`, `MOXI_TEST_TIERS=all` (every tier but live)
   and the idle gate's `MOXI_TEST_IDLE=<line>@<deadline>`, the deadline `--wait-s` seconds
   from the first session's start (300 by default), shared by every session.
   `MOXI_TEST_TIMED_SLACK` passes through; the verb reads it once for the record and
   exits 2 on a value the kit would refuse. The temp stays on disk as a check's does
   (`moxi_dev.temp.for_check`), since the record is what checks are held to: a
   `MOXI_TEST_TEMP=ram` reaches the sessions as `disk`, the verb says so on one line,
   and a value the kit would refuse exits 2. Browser packages take the check's browser
   handling. Each session writes its `durations.json` under `.moxi-dev/timed-run/`. A
   session that collects no test is a package with no timed test.
5. Read each session's tests, their `timed` entries and the gate's figures, and keep the
   record (`moxi_dev.timed_record`) unless `--no-record` or the tree has changes of its
   own.
6. Print one summary line, the last line:
   `moxi-dev timed: commit <sha> on <machine> | passed <n>, failed <n>, skipped <n>, not
   measured <n> (...) | total <s> s | exit <code>`.

The exit code: 0 when every selected test passed or skipped; 1 when one failed or a
session broke (a collection error, a crash); 3 when none failed and at least one was not
measured; 2 when it couldn't start (the lock held, a stale `uv.lock`, a failed sync, a
refused option).
"""

from __future__ import annotations

import argparse
import contextlib
import datetime
import json
import math
import os
import platform
import subprocess
import sys
import time
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from collections.abc import Mapping, Sequence
from typing import Any

from moxi import paths

from . import check, temp, timed_record
from .durations import fcntl, msvcrt
from .testing import (DURATIONS_ENV, FINDING_ENV, IDLE_ENV, TIERS_ENV, TIMED_ENV,
                      TIMED_LOCK_FD_ENV, TIMED_SLACK_ENV)

ENV_DIR = Path(".moxi-dev") / f"timed-env-{check.PYTHON}"
RUN_DIR = Path(".moxi-dev") / "timed-run"
#: The run lock and the commit file of a tree, under `.moxi-dev/timed/` outside git.
TREE_TIMED_DIR = Path(".moxi-dev") / "timed"
COMMIT_JSON = "commit.json"
RUN_LOCK = ".run-lock"
DEFAULT_WAIT_S = 300.0
DEFAULT_LINE = 2.0
SUBVERBS = ("show", "take", "schedule", "unschedule")


class TimedError(Exception):
    """The run can't start; the message says why (exit 2)."""


@dataclass(frozen=True)
class Tree:
    root: Path
    commit: str                     # full sha
    dirty: bool
    lock_path: Path
    record_dir: Path | None         # the clone's record dir; None outside git
    has_git: bool


def _git(root: Path, *args: str) -> str | None:
    proc = subprocess.run(["git", *args], cwd=root, capture_output=True, encoding="utf-8",
                          check=False)
    return proc.stdout if proc.returncode == 0 else None


def workspace_root(start: Path) -> Path | None:
    """The nearest directory up from `start` whose `pyproject.toml` declares the uv
    workspace."""
    for where in (start, *start.parents):
        try:
            data = tomllib.loads((where / "pyproject.toml").read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError):
            continue
        if isinstance(data.get("tool", {}).get("uv", {}).get("workspace"), dict):
            return where
    return None


def common_record_dir(root: Path) -> Path | None:
    """`<git common dir>/moxi-dev/timed` of the checkout at `root`, or None outside git."""
    common = _git(root, "rev-parse", "--path-format=absolute", "--git-common-dir")
    return Path(common.strip()) / "moxi-dev" / "timed" if common else None


def find_tree(start: Path) -> Tree:
    """The tree a run measures from `start` (module doc)."""
    top = _git(start, "rev-parse", "--show-toplevel")
    if top is not None:
        root = Path(top.strip())
        record_dir = common_record_dir(root)
        head = (_git(root, "rev-parse", "HEAD") or "").strip()
        if not head or record_dir is None:
            raise TimedError(f"{root}: git names no commit at HEAD")
        dirty = bool((_git(root, "status", "--porcelain", "-z") or "").strip("\0"))
        return Tree(root=root, commit=head, dirty=dirty,
                    lock_path=record_dir / RUN_LOCK, record_dir=record_dir, has_git=True)
    root = workspace_root(start)
    if root is None:
        raise TimedError(f"{start} is in no git checkout and under no uv workspace root")
    where = root / TREE_TIMED_DIR / COMMIT_JSON
    try:
        commit = json.loads(where.read_text(encoding="utf-8"))["commit"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise TimedError(f"{where}: no commit to measure in a tree with no git; a tick "
                         f"writes it once the tree verifies against its stamp ({exc})") from exc
    if not (isinstance(commit, str) and timed_record.COMMIT_FILE.fullmatch(commit + ".json")):
        raise TimedError(f"{where}: {commit!r} is not a commit")
    return Tree(root=root, commit=commit, dirty=False,
                lock_path=root / TREE_TIMED_DIR / RUN_LOCK, record_dir=None, has_git=False)


def lock_run(lock_path: Path, env: Mapping[str, str]) -> int:
    """Hold the run lock without waiting (module doc, step 1); the descriptor, which stays
    open for the run. TimedError when it is held or the handed descriptor is wrong."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    given = env.get(TIMED_LOCK_FD_ENV, "").strip()
    if given:
        try:
            fd = int(given)
            handed, ours = os.fstat(fd), os.stat(lock_path)
        except (ValueError, OSError) as exc:
            raise TimedError(f"{TIMED_LOCK_FD_ENV}={given} is not open on {lock_path} "
                             f"({exc})") from exc
        if (handed.st_dev, handed.st_ino) != (ours.st_dev, ours.st_ino):
            raise TimedError(f"{TIMED_LOCK_FD_ENV}={given} is not open on {lock_path}")
    else:
        fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        if fcntl is not None:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        elif msvcrt is not None:
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
    except OSError as exc:
        if not given:
            os.close(fd)
        raise TimedError("another timed run is measuring in this clone") from exc
    return fd


def slack_of(env: Mapping[str, str]) -> float:
    """The run's slack factor, by the kit's rule; TimedError for a value it refuses."""
    raw = env.get(TIMED_SLACK_ENV, "").strip()
    if not raw:
        return 1.0
    try:
        factor = float(raw)
    except ValueError:
        factor = math.nan
    if not math.isfinite(factor) or factor < 1:
        raise TimedError(f"{TIMED_SLACK_ENV} is a finite number of at least 1, or unset; "
                         f"it is {raw!r}")
    return factor


def already_measured(data: Mapping[str, Any] | None, factor: float) -> bool:
    """Whether every test of the record's `collected` list is settled at `factor`."""
    if not data or data.get("collected") is None:
        return False
    for nodeid in data["collected"]:
        results = data["tests"].get(nodeid, {})
        if not any(timed_record.counts(result.get("outcome"), float(key), factor)
                   for key, result in results.items()):
            return False
    return True


def _cpu_model() -> str:
    with contextlib.suppress(OSError):
        for line in Path("/proc/cpuinfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("model name"):
                return line.split(":", 1)[1].strip()
    paths.uname()  # fills platform without a WMI query on Windows
    return platform.processor() or "unknown"


def _venv_python_version(venv: Path) -> str:
    with contextlib.suppress(OSError):
        for line in (venv / "pyvenv.cfg").read_text(encoding="utf-8").splitlines():
            key, _, value = line.partition("=")
            if key.strip() in ("version_info", "version"):
                return value.strip()
    return check.PYTHON


def _now_utc() -> str:
    return datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


@dataclass
class Result:
    commit: str = "unknown"
    machine: str = "unknown"
    counts: dict[str, int] = field(default_factory=lambda: dict.fromkeys(
        ("passed", "failed", "skipped", "not measured"), 0))
    busy: float | None = None           # the most other work over a voided window
    line_cpus: float = DEFAULT_LINE
    gauge: str | None = None
    waited_s: float = 0.0
    note: str | None = None             # in place of the counts: `already measured`
    seconds: float = 0.0
    code: int = 0

    def line(self) -> str:
        if self.note is not None:
            middle = self.note
        else:
            done = self.counts
            middle = (f"passed {done['passed']}, failed {done['failed']}, "
                      f"skipped {done['skipped']}, not measured {done['not measured']}")
            if done["not measured"]:
                if self.gauge == "none":
                    middle += " (no gauge on this system)"
                else:
                    other = "unknown" if self.busy is None else f"{self.busy:.2f}"
                    middle += (f" (machine busy, other work {other} CPUs over "
                               f"{self.line_cpus:g}, waited {self.waited_s:.0f} s)")
        sha, plus, mark = self.commit.partition("+")
        return (f"moxi-dev timed: commit {sha[:12]}{plus}{mark} on {self.machine} | {middle} | "
                f"total {self.seconds:.1f} s | exit {self.code}")


@dataclass(frozen=True)
class Options:
    machine: str
    wait_s: float = DEFAULT_WAIT_S
    line: float = DEFAULT_LINE
    findings: Sequence[str] = ()
    packages: Sequence[str] = ()
    record_dir: Path | None = None
    keep_record: bool = True
    scheduled: bool = False


def check_options(opts: Options) -> None:
    if not timed_record.MACHINE_NAME.fullmatch(opts.machine):
        raise TimedError(f"--machine takes letters, digits and ._-; {opts.machine!r} is "
                         f"not one")
    if not (math.isfinite(opts.line) and opts.line > 0):
        raise TimedError(f"--idle-line is a number of CPUs above 0; it is {opts.line!r}")
    if not (math.isfinite(opts.wait_s) and opts.wait_s >= 0):
        raise TimedError(f"--wait-s is a number of seconds, 0 or more; it is {opts.wait_s!r}")
    if opts.scheduled and (opts.packages or opts.findings):
        raise TimedError("--scheduled measures every package; it takes no --package or "
                         "--finding")
    try:
        check.check_findings(opts.findings)
    except check.CheckError as exc:
        raise TimedError(str(exc)) from exc


def run_timed(start: Path, opts: Options, *, env: Mapping[str, str] | None = None,
              run: check.Run | None = None, probe: check.Probe | None = None) -> Result:
    """Run the verb's steps from `start` (module doc); the result holds the exit code."""
    env = os.environ if env is None else env
    run = run or check._run
    probe = probe or check.chromium_probe
    began = time.monotonic()
    result = Result(machine=opts.machine, line_cpus=opts.line)
    fd = None
    try:
        check_options(opts)
        # Its durations feed the machine record the checks are held to, and a check
        # keeps its temp on disk: so does every timed run.
        try:
            env, temp_why = temp.for_check(env)
        except temp.TempError as exc:
            raise TimedError(str(exc)) from exc
        if temp_why:
            print(f"moxi-dev timed: temp disk ({temp_why})", flush=True)
        tree = find_tree(start)
        result.commit = tree.commit + ("+dirty" if tree.dirty else "")
        record_dir = opts.record_dir or tree.record_dir
        if record_dir is None:
            raise TimedError("a tree with no git keeps its record where --record-dir says; "
                             "pass it")
        factor = slack_of(env)
        fd = lock_run(tree.lock_path, env)
        _Run(tree, opts, env, run, probe, result, record_dir, factor).steps()
    except (TimedError, check.CheckError) as exc:
        print(f"moxi-dev timed: {exc}", file=sys.stderr, flush=True)
        result.code = 2
    finally:
        if fd is not None and not env.get(TIMED_LOCK_FD_ENV, "").strip():
            os.close(fd)
    result.seconds = time.monotonic() - began
    return result


@dataclass
class _Run:
    tree: Tree
    opts: Options
    env: Mapping[str, str]
    run: check.Run
    probe: check.Probe
    result: Result
    record_dir: Path
    factor: float

    def steps(self) -> None:
        tree, opts, result = self.tree, self.opts, self.result
        machine_file = self.record_dir / opts.machine / f"{tree.commit}.json"
        if opts.scheduled:
            try:
                data = timed_record.read_file(machine_file)
            except timed_record.RecordError as exc:
                raise TimedError(str(exc)) from exc
            if already_measured(data, self.factor):
                result.note = "already measured"
                return
        packages = check.load_workspace(tree.root)
        chosen = self.choose(packages)
        base = check._base_env(self.env)
        if self.run(["uv", "lock", "--check"], tree.root, base) != 0:
            raise TimedError("uv.lock is stale: `uv lock` updates it")
        venv = tree.root / ENV_DIR
        if self.run(["uv", "sync", "--locked", "--all-packages", "--all-extras",
                     "--all-groups", "--python", check.PYTHON], tree.root,
                    {**base, "UV_PROJECT_ENVIRONMENT": str(venv)}) != 0:
            raise TimedError(f"uv sync of {ENV_DIR} failed")
        started = _now_utc()
        sessions = self.sessions(chosen, venv, base)
        complete = (not opts.packages and not opts.findings
                    and all(code in (0, 1, check.NO_TESTS) for code, _, _ in sessions.values()))
        results, gates = {}, []
        broke = False
        for package, (code, tests, gate) in sessions.items():
            if code not in (0, check.NO_TESTS):
                broke = True
            if gate:
                gates.append(gate)
            for nodeid, entry in (tests or {}).items():
                timed = entry.get("timed") or {"outcome": entry.get("outcome", "failed")}
                outcome = timed.get("outcome")
                if outcome in result.counts:
                    result.counts[outcome] += 1
                if outcome == timed_record.NOT_MEASURED and timed.get("other") is not None:
                    result.busy = max(result.busy or 0.0, float(timed["other"]))
                results[nodeid] = {"package": package, **{key: timed.get(key) for key in (
                    "outcome", "clock", "gauge", "other", "line", "reading", "budget",
                    "unit", "failure")}}
        result.gauge = gates[0].get("gauge") if gates else None
        result.waited_s = sum(float(gate.get("waited_s") or 0) for gate in gates)
        result.code = 1 if broke or result.counts["failed"] else (
            3 if result.counts["not measured"] else 0)
        if not opts.keep_record or tree.dirty:
            if tree.dirty:
                print(f"moxi-dev timed: {tree.root} has changes of its own; no record kept",
                      flush=True)
            return
        first = next((gate.get("first_sample") for gate in gates
                      if gate.get("first_sample") is not None), None)
        entry = {"started": started, "system": paths.uname().system, "cpu_model": _cpu_model(),
                 "cpus": os.cpu_count(), "python": _venv_python_version(venv),
                 "gauge": result.gauge, "line": opts.line, "first_sample": first,
                 "waited_s": round(result.waited_s, 3),
                 "measured": result.counts["passed"] + result.counts["failed"],
                 "slack": self.factor, "exit": result.code, "complete": complete,
                 "kind": "scheduled" if opts.scheduled else "hand"}
        collected = sorted(results) if complete else None
        try:
            timed_record.keep(self.record_dir, opts.machine, tree.commit, entry, results,
                              collected)
        except (OSError, timed_record.RecordError) as exc:
            print(f"moxi-dev timed: keeping no record for {tree.commit[:12]} ({exc})",
                  file=sys.stderr, flush=True)

    def choose(self, packages: Sequence[check.Package]) -> list[check.Package]:
        try:
            return check.chosen_packages(packages, self.opts.packages)
        except check.CheckError as exc:
            raise TimedError(str(exc)) from exc

    def sessions(self, chosen: Sequence[check.Package], venv: Path,
                 base: Mapping[str, str]) -> dict[str, tuple[int, dict | None, dict | None]]:
        """Each package's session: its exit code, its tests and its gate's figures;
        packages with no timed test left out."""
        work = self.tree.root / RUN_DIR
        work.mkdir(parents=True, exist_ok=True)
        suite_env = {key: value for key, value in base.items()
                     if key not in (*check.SUITE_DROPPED_ENV, TIMED_LOCK_FD_ENV)}
        suite_env = {**suite_env, "VIRTUAL_ENV": str(venv),
                     "PATH": os.pathsep.join(filter(None, [str(check.venv_bin(venv)),
                                                           base.get("PATH")]))}
        deadline = time.time() + self.opts.wait_s
        suite_env.update({TIMED_ENV: "only", TIERS_ENV: "all",
                          IDLE_ENV: f"{self.opts.line:g}@{deadline:.3f}"})
        if self.opts.findings:
            suite_env[FINDING_ENV] = ",".join(self.opts.findings)
        plans, _ = check.browser_plans(chosen, venv, suite_env, self.probe, "timed")
        python = str(check.venv_program(venv, "python"))
        out = {}
        for package in chosen:
            where = work / f"{package.dir}.durations.json"
            where.unlink(missing_ok=True)
            args, extra = plans.get(package.dir, ((), {}))
            code = self.run([python, "-m", "pytest", "-n", "0", *args], package.path,
                            {**suite_env, **extra, DURATIONS_ENV: str(where)})
            body = _read_json(where)
            tests = body.get("tests") if isinstance(body.get("tests"), dict) else None
            if code == check.NO_TESTS and not tests:
                continue
            gate = body.get("gate") if isinstance(body.get("gate"), dict) else None
            out[package.dir] = (code, tests, gate)
        return out


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="moxi-dev timed",
        description="Run the timed tests on their own, each counted only when little other "
                    "work ran on the machine during it, and keep the results per commit "
                    "and machine. Its other verbs: show, take, schedule, unschedule.")
    parser.add_argument("--wait-s", type=float, default=DEFAULT_WAIT_S, metavar="S",
                        help="how long the whole run may wait for the machine to quieten "
                             "(default 300)")
    parser.add_argument("--idle-line",  # noqa: MX1 -- a count of CPUs, not a duration
                        type=float, default=DEFAULT_LINE, metavar="CPUS",
                        help="other work, in CPUs, a test's window must stay under "
                             "(default 2.0)")
    parser.add_argument("--finding", metavar="L<n>", action="append", default=[],
                        help="run only the timed tests marked with this finding")
    parser.add_argument("--package", metavar="NAME", action="append", default=[],
                        help="run only this package's timed tests; may be given more "
                             "than once")
    parser.add_argument("--machine", metavar="NAME", default=None,
                        help="the machine the record files the run under (default: the "
                             "short host name)")
    parser.add_argument("--record-dir", metavar="DIR",
                        help="keep the record here (default: the clone's git directory)")
    parser.add_argument("--no-record", action="store_true", help="keep no record")
    parser.add_argument("--scheduled", action="store_true",
                        help="a scheduled run: exit 0 at once when this machine's record "
                             "already settles every test of the commit")
    return parser


def main(argv: list[str]) -> int:
    if argv and argv[0] in SUBVERBS:
        verb, rest = argv[0], argv[1:]
        if verb == "show":
            from . import timed_show
            return timed_show.main(rest)
        if verb == "take":
            return timed_record.take_main(rest, common_record_dir(Path.cwd()))
        from . import schedule
        return schedule.schedule_main(rest) if verb == "schedule" \
            else schedule.unschedule_main(rest)
    args = _parser().parse_args(argv)
    opts = Options(machine=args.machine or timed_record.default_machine(),
                   wait_s=args.wait_s, line=args.idle_line, findings=args.finding,
                   packages=args.package,
                   record_dir=Path(args.record_dir).expanduser() if args.record_dir else None,
                   keep_record=not args.no_record, scheduled=args.scheduled)
    result = run_timed(Path.cwd(), opts)
    print(result.line(), flush=True)
    return result.code

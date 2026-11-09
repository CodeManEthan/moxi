"""`moxi-dev check`: the checks a change must pass before it merges.

Run from the repository's root as
`uv run --locked moxi-dev check [--full] [--base REV] [--finding L<n>]`.
It first reads the workspace, the worker count and the changed paths; a bad workspace,
worker count or base stops it there, before any step runs. Then, in order:

1. `uv lock --check`: the committed `uv.lock` matches every package's declarations. A
   stale lock stops the check here.
2. The tag, comment-reference and public-name checks over the repository's files
   (`moxi_dev.tags`, `moxi_dev.refs`, `moxi_dev.naming`): no short reference ids
   pointing into notes a reader of the repository can't see; every source name, file and
   flag cited by a comment exists or has a live `.refs-allow` entry; public config keys,
   flags, parameters and CLI verbs keep their unit and switch-word rules; every exception
   mark names its rule and reason.
   Then the release step over every workspace member, whatever the change picks
   (`moxi_dev.release`): the core's catalogue lists exactly the module packages, every
   requirement one member places on another admits that member's version, and each
   member has the metadata, the CHANGELOG entry and the README links PyPI needs. Each
   problem's line names the member, the field and the fix.
3. Pick the packages. The changed paths are the commits since the branch left `main`
   (`git merge-base main HEAD`, or `--base REV` in place of `main`) plus the working
   tree's staged, unstaged and untracked files. A path under `packages/<p>/` picks `p`;
   any other path (the root files, the lock, the workflow) picks every package. Then
   every package that depends on a picked one, by its declared dependencies, optional
   dependencies or dependency groups, is picked too, and so on until nothing is added:
   a plain path filter would skip a dependent's tests and its contract check. `--full`
   picks every package.
4. Check the build stamps of the picked packages that have a web page
   (`moxi-dev stamp`, on the working tree): built files that lag their sources fail here,
   with no Node.
   Then, when `node` and `npm` are on PATH and the root's `node_modules` exists,
   `npm run check` compares the built files byte for byte with what the pinned
   toolchain makes from their sources, so a built file edited by hand fails too; without
   them a printed line says the compare was left out. A failure here stops the check.
5. Sync the check's own environment, `.moxi-dev/env-3.12` under the root, with every
   package, extra and group at the lowest Python the packages support. The root's own
   `.venv` is never touched. A failed sync stops the check here.
6. For each picked package, the contract's import rules: import-linter and deptry, run
   in that environment with a configuration written from the package's declarations
   (`moxi_dev.lint`, which writes it under `.moxi-dev/lint/`).
7. The parallel pass: run each picked package's tests in a pytest session of its own,
   from the package's directory (each has its own conftest, and test names repeat across
   packages), always spread over workers: `-n N`, where N is the CPUs this process may
   run on, at most `MOXI_TEST_WORKERS` when that is set, and never less than one. This
   pass is never serial, so N is passed explicitly: pytest-xdist's
   `PYTEST_XDIST_AUTO_NUM_WORKERS` (which can turn `-n auto` into a serial run) and the
   caller's `PYTEST_ADDOPTS` are dropped from the suites' environment, and an option that
   would undo the workers (`--pdb`) makes pytest refuse the run.

   What runs is the package's plain `pytest` (its `-m` deselects the `browser`, `slow`,
   `build` and `live` tiers), and besides it, through the test kit's variables
   (`moxi_dev.testing`): a tier test marked `touches` whose glob matches a changed path;
   with `--full`, every tier but `live`; with `--finding L<n>`, only the tests marked
   with that finding (one reported issue, named by an L and a number, which
   `@pytest.mark.finding` gives the tests that cover it), whatever their tier but `live`. Under `--finding` a package where
   no test carries it counts as `none`, not as a failure, but a check where no package
   has one fails. A `live` test never runs here.

   A package whose tests drive a browser (its declarations name Playwright, or moxi-ui
   with its `testing` extra) runs its browser tests as well, beside the rest, when
   Playwright's Chromium starts in the check's environment. When it can't, a printed
   line says why and the package's browser tests are left out, even those `--full` or a
   changed path would put back (`MOXI_TEST_LEAVE_OUT=browser`).

   A test marked `timed` asserts a wall-clock budget, which sixteen workers beside every
   other suite would break (`moxi_dev.testing`, Timed tests). The parallel pass holds
   back every timed test it would have run (`MOXI_TEST_TIMED=hold`), and the kit names
   them in the suite's durations.
8. The serial pass, once every package's parallel pass has finished: for each package
   whose parallel pass held a timed test, in the packages' order, one pytest session
   with no workers (`-n 0`) and the same selection, running only the timed tests
   (`MOXI_TEST_TIMED=only`): under `--full` every timed test but a `live` one, under a
   picked run the plain ones and the tier ones a changed path puts back, under
   `--finding` the ones that carry it. Nothing else of the check runs meanwhile. A
   failure here fails the check as one in the parallel pass does. A package whose
   parallel pass ran nothing because every test it chose was timed counts as `none`
   there.
9. Read each suite's durations (the kit writes them; a package's two passes are one
   package), keep the commit's record and find the newest record on `main` to compare
   with (`moxi_dev.durations`).
10. Print one summary line, the last line of the output: the commit, the packages, the
   lock, tag, reference, name, release, stamp, web, sync and lint results, each package's tests and seconds (and
   `without its browser tests` where Chromium couldn't start), the serial pass's
   packages with their results and seconds (`timed none` when no package held a timed
   test, `timed not run` when the suites never ran), the tests' own seconds with the
   slowest 5 %'s share of them and the delta against main, the total and the exit code.

The tags, references, names, release step, each package's lint and each package's tests
(both passes) all run even when one before them failed, so one run shows every failure.

The exit code is 0 only when every step passed, 1 when one failed, and 2 when the check
could not start or a step could not be run (not in a workspace, a member with no readable
`pyproject.toml` or no project name, no merge base, a bad `MOXI_TEST_WORKERS` or
`--finding`, `uv` not on PATH). The summary line is printed in every case.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import time
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Mapping, Sequence

from . import durations, lint, naming, refs, release, stamp, tags
from .testing import (CHANGED_ENV, DURATIONS_ENV, FINDING_ENV, FINDING_NAME,
                      LEAVE_OUT_ENV, TIERS_ENV, TIMED_ENV)

PYTHON = "3.12"
ENV_DIR = Path(".moxi-dev") / f"env-{PYTHON}"
LINT_DIR = Path(".moxi-dev") / "lint"
RUN_DIR = Path(".moxi-dev") / "run"       # this run's changed paths and suites' durations
WORKERS_VAR = "MOXI_TEST_WORKERS"
#: pytest-xdist's arguments for the serial pass of the timed tests: no workers.
SERIAL = ("-n", "0")

# A step's command, run from `cwd` with `env`; returns the exit code. Tests replace it.
Run = Callable[[Sequence[str], Path, Mapping[str, str]], int]
# The lint's commands for a package: (package dir, work dir, every member's dir).
Plan = Callable[[Path, Path, Sequence[Path]], list[lint.Command]]


#: Whether this is Windows, where a virtual environment keeps its programs in `Scripts`
#: and names them `<name>.exe`; tests replace it.
WINDOWS = os.name == "nt"


def venv_bin(venv: Path) -> Path:
    """The directory of a virtual environment's programs: `Scripts` on Windows, else
    `bin`."""
    return venv / ("Scripts" if WINDOWS else "bin")


def venv_program(venv: Path, name: str) -> Path:
    """A program a virtual environment installs (`python`, a console script), by its
    name: `Scripts\\<name>.exe` on Windows, else `bin/<name>`."""
    return venv_bin(venv) / (f"{name}.exe" if WINDOWS else name)


class CheckError(Exception):
    """The check can't start; the message says why."""


@dataclass(frozen=True)
class Package:
    dir: str                 # its directory under packages/
    dist: str                # its distribution name, normalized
    path: Path
    requires: frozenset[str] = frozenset()   # the workspace packages it depends on
    browser: bool = False    # its tests drive a browser


def normalize(name: str) -> str:
    """A distribution name in its normal form (`Moxi_Sessions` reads `moxi-sessions`)."""
    return re.sub(r"[-_.]+", "-", name).lower()


_NAME = re.compile(r"\s*([A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)")
_EXTRAS = re.compile(r"\s*\[([^\]]*)\]")


def requirement_name(requirement: str) -> str | None:
    """The distribution a requirement string names, normalized; None if it names none."""
    match = _NAME.match(requirement)
    return normalize(match.group(1)) if match else None


def requirement_extras(requirement: str) -> frozenset[str]:
    """The extras a requirement string asks for, normalized."""
    match = _NAME.match(requirement)
    extras = _EXTRAS.match(requirement, match.end()) if match else None
    if not extras:
        return frozenset()
    return frozenset(normalize(name.strip()) for name in extras.group(1).split(",")
                     if name.strip())


def _requirement_strings(data: dict) -> list[str]:
    project = data.get("project", {})
    strings = list(project.get("dependencies", []))
    for extra in project.get("optional-dependencies", {}).values():
        strings += extra
    for group in data.get("dependency-groups", {}).values():
        # An {include-group = ...} entry names a group of the same file, read here anyway.
        strings += [entry for entry in group if isinstance(entry, str)]
    return strings


def _requirements(data: dict) -> set[str]:
    return {name for name in map(requirement_name, _requirement_strings(data)) if name}


def drives_a_browser(data: dict) -> bool:
    """Whether a package's tests drive a browser: its declarations name Playwright, or
    moxi-ui with its `testing` extra, whose fixtures bring a browser."""
    for requirement in _requirement_strings(data):
        name = requirement_name(requirement)
        if name in ("playwright", "pytest-playwright"):
            return True
        if name == "moxi-ui" and "testing" in requirement_extras(requirement):
            return True
    return False


def load_workspace(root: Path) -> list[Package]:
    """The workspace's packages, in the order the root's `members` names them."""
    try:
        data = tomllib.loads((root / "pyproject.toml").read_text())
        members = data["tool"]["uv"]["workspace"]["members"]
    except (OSError, KeyError, tomllib.TOMLDecodeError) as exc:
        raise CheckError(f"{root} is not a uv workspace root ({exc})") from exc
    loaded = []
    for member in members:
        path = root / member
        try:
            package_data = tomllib.loads((path / "pyproject.toml").read_text())
            name = package_data["project"]["name"]
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise CheckError(f"workspace member {member}: no readable pyproject.toml "
                             f"({exc})") from exc
        except (KeyError, TypeError) as exc:
            raise CheckError(f"workspace member {member}: its pyproject.toml names no "
                             f"project") from exc
        loaded.append((path, normalize(name), _requirements(package_data),
                       drives_a_browser(package_data)))
    dists = {dist for _, dist, _, _ in loaded}
    return [Package(dir=path.name, dist=dist, path=path,
                    requires=frozenset((requires & dists) - {dist}), browser=browser)
            for path, dist, requires, browser in loaded]


def pick(packages: Sequence[Package], paths: Sequence[str]) -> list[Package]:
    """The packages the changed paths pick, with every package that depends on them."""
    by_dir = {package.dir: package for package in packages}
    picked: set[str] = set()
    for path in paths:
        parts = path.split("/")
        if len(parts) >= 3 and parts[0] == "packages" and parts[1] in by_dir:
            picked.add(by_dir[parts[1]].dist)
        else:
            return list(packages)
    grew = True
    while grew:
        grew = False
        for package in packages:
            if package.dist not in picked and package.requires & picked:
                picked.add(package.dist)
                grew = True
    return [package for package in packages if package.dist in picked]


def _git(root: Path, *args: str) -> str:
    # git writes paths in UTF-8 on every system; Windows's locale encoding is another.
    proc = subprocess.run(["git", *args], cwd=root, capture_output=True, encoding="utf-8")
    if proc.returncode != 0:
        raise CheckError(f"git {' '.join(args)}: {proc.stderr.strip()}")
    return proc.stdout


def _names(output: str) -> list[str]:
    return [name for name in output.split("\0") if name]


def changed_paths(root: Path, base: str = "main") -> list[str]:
    """Paths changed since HEAD left `base`, and in the working tree, sorted."""
    try:
        fork = _git(root, "merge-base", base, "HEAD").strip()
    except CheckError as exc:
        raise CheckError(f"no merge base of {base} and HEAD; pass --base REV or --full "
                         f"({exc})") from exc
    # --no-renames: a file moved between packages counts in both.
    diff = ("diff", "--name-only", "--no-renames", "-z")
    paths = set(_names(_git(root, *diff, fork, "HEAD")))
    paths |= set(_names(_git(root, *diff, "HEAD")))            # staged and unstaged
    paths |= set(_names(_git(root, "ls-files", "--others", "--exclude-standard", "-z")))
    return sorted(paths)


def cpu_count() -> int:
    """The CPUs this process may run on, as pytest-xdist's `-n auto` counts them; at least 1."""
    try:
        return len(os.sched_getaffinity(0)) or 1
    except (AttributeError, OSError):
        return os.cpu_count() or 1


def worker_args(environ: Mapping[str, str], cpus: int | None = None) -> list[str]:
    """pytest-xdist's arguments: `-n N`, N the CPU count capped by MOXI_TEST_WORKERS.

    N is never 0, which would run the suite serially."""
    count = max(1, cpu_count() if cpus is None else cpus)
    cap = environ.get(WORKERS_VAR, "").strip()
    if cap:
        if not cap.isdigit() or int(cap) < 1:
            raise CheckError(f"{WORKERS_VAR} must be a whole number of workers, 1 or more; "
                             f"it is {cap!r}")
        count = min(count, int(cap))
    return ["-n", str(count)]


def _run(cmd: Sequence[str], cwd: Path, env: Mapping[str, str]) -> int:
    try:
        return subprocess.run(list(cmd), cwd=cwd, env=dict(env)).returncode
    except OSError as exc:
        raise CheckError(f"could not run {cmd[0]} ({exc})") from exc


def _base_env(environ: Mapping[str, str]) -> dict[str, str]:
    # `uv run` points VIRTUAL_ENV at the root's .venv; no step here may use or change it.
    return {key: value for key, value in environ.items()
            if key not in ("VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT")}


#: Dropped from the suites' environment: either can make the run serial, or run less
#: than the package's own configuration says; the kit's selection and durations
#: variables are the check's own to set.
SUITE_DROPPED_ENV = ("PYTEST_XDIST_AUTO_NUM_WORKERS", "PYTEST_ADDOPTS", TIERS_ENV,
                     CHANGED_ENV, FINDING_ENV, DURATIONS_ENV, TIMED_ENV, LEAVE_OUT_ENV)

#: pytest's exit code when it ran no test.
NO_TESTS = 5

#: The marker expression a browser package's suite runs with when Chromium starts: what
#: a plain run deselects (moxi_dev.testing's tiers), less the browser tier.
WITH_BROWSER = "not slow and not build and not live"

#: Run in the check's environment: exits 0 when Playwright's Chromium starts, else
#: prints why on its last line of stderr.
CHROMIUM_PROBE = """\
import sys
try:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as playwright:
        playwright.chromium.launch().close()
except Exception as exc:
    said = str(exc).strip()
    print(said.splitlines()[0] if said else type(exc).__name__, file=sys.stderr)
    sys.exit(1)
"""

# Why Chromium can't start in the check's environment, or None when it starts; given the
# environment's python and the suites' environment. Tests replace it.
Probe = Callable[[Path, Mapping[str, str]], str | None]


def chromium_probe(python: Path, env: Mapping[str, str]) -> str | None:
    """Start and close Playwright's Chromium with `python`: None when it starts, else
    one line saying why it can't."""
    try:
        done = subprocess.run([str(python), "-c", CHROMIUM_PROBE], env=dict(env),
                              capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        return "it did not start within 120 s"
    except OSError as exc:
        return f"could not run {python} ({exc})"
    if done.returncode == 0:
        return None
    lines = [line.strip() for line in done.stderr.splitlines() if line.strip()]
    return lines[-1] if lines else f"the probe exited {done.returncode}"


@dataclass
class Result:
    commit: str = "unknown"
    packages: list[str] | None = None
    lock: str = "not run"
    tags: str = "not run"
    refs: str = "not run"
    names: str = "not run"
    release: str = "not run"
    stamps: str = "not run"
    web: str = "not run"
    sync: str = "not run"
    lint: list[tuple[str, bool]] | None = None
    #: (package, passed, seconds); passed is None under --finding when no test there
    #: carries the finding.
    suites: list[tuple[str, bool | None, float]] = field(default_factory=list)
    #: The packages whose browser tests were left out, Chromium not starting.
    no_browser: list[str] = field(default_factory=list)
    #: The serial pass of the timed tests, (package, passed, seconds) for each package
    #: whose parallel pass held one; None when the suites never ran.
    timed: list[tuple[str, bool, float]] | None = None
    durations: durations.Summary | None = None
    delta: durations.Delta | None = None
    kept: Path | None = None             # the commit's durations record, when kept
    seconds: float = 0.0
    code: int = 0

    def timing(self) -> str:
        if self.durations is None:
            return "durations none"
        summary = self.durations
        text = (f"durations {summary.total:.1f}s, slowest 5% "
                f"{round(summary.slowest_share * 100)}%")
        delta = self.delta
        if delta is None:
            return text + ", delta none"
        text += f", delta {delta.seconds:+.1f}s vs {delta.commit[:12]}"
        return text if delta.whole else text + f" ({', '.join(delta.packages)})"

    def line(self) -> str:
        names = "unknown" if self.packages is None else ", ".join(self.packages) or "none"
        word = {True: "pass", False: "fail", None: "none"}
        suites = ", ".join(f"{name} {word[ok]} {secs:.1f}s"
                           + (" without its browser tests" if name in self.no_browser else "")
                           for name, ok, secs in self.suites) or "no suites run"
        if self.timed is None:
            timed = "not run"
        else:
            timed = ", ".join(f"{name} {word[ok]} {secs:.1f}s"
                              for name, ok, secs in self.timed) or "none"
        if self.lint is None:
            linted = "not run"
        elif not self.lint:
            linted = "none"
        else:
            failed = [name for name, ok in self.lint if not ok]
            linted = f"fail ({', '.join(failed)})" if failed else "pass"
        return (f"moxi-dev check: commit {self.commit} | packages: {names} | "
                f"lock {self.lock} | tags {self.tags} | refs {self.refs} | "
                f"names {self.names} | release {self.release} | "
                f"stamps {self.stamps} | "
                f"web {self.web} | sync {self.sync} | "
                f"lint {linted} | {suites} | timed {timed} | {self.timing()} | "
                f"total {self.seconds:.1f}s | exit {self.code}")


def _commit(root: Path) -> str:
    commit = _git(root, "rev-parse", "--short=12", "HEAD").strip()
    dirty = _git(root, "status", "--porcelain", "-z").strip("\0")
    return commit + ("+dirty" if dirty else "")


def check_findings(findings: Sequence[str]) -> list[str]:
    """The findings `--finding` names, each `L<n>`; CheckError for any other."""
    bad = [name for name in findings if not FINDING_NAME.fullmatch(name)]
    if bad:
        raise CheckError(f"--finding takes a finding as L<n>; {', '.join(map(repr, bad))} "
                         f"is not one")
    return list(dict.fromkeys(findings))


def check(root: Path, *, full: bool = False, base: str | None = None,
          finding: Sequence[str] = (), environ: Mapping[str, str] | None = None,
          run: Run | None = None, plan: Plan | None = None,
          probe: Probe | None = None) -> Result:
    """Run the check's steps on the workspace at `root`; the result holds the exit code."""
    environ = os.environ if environ is None else environ
    run = run or _run
    plan = plan or lint.plan
    probe = probe or chromium_probe
    started = time.monotonic()
    result = Result()
    try:
        findings = check_findings(finding)
        result.commit = _commit(root)
        packages = load_workspace(root)
        workers = worker_args(environ)
        changed = None if full else changed_paths(root, base or "main")
        chosen = list(packages) if changed is None else pick(packages, changed)
        result.packages = [package.dir for package in chosen]
        steps = _Steps(root, packages, workers, environ, run, plan, result,
                       changed=changed, findings=findings, base=base or "main",
                       probe=probe)
        result.code = 0 if steps.all(chosen) else 1
    except CheckError as exc:
        print(f"moxi-dev check: {exc}", file=sys.stderr, flush=True)
        result.code = 2
    result.seconds = time.monotonic() - started
    return result


@dataclass
class _Steps:
    root: Path
    packages: Sequence[Package]
    workers: Sequence[str]
    environ: Mapping[str, str]
    run: Run
    plan: Plan
    result: Result
    changed: Sequence[str] | None = None      # None: --full
    findings: Sequence[str] = ()
    base: str = "main"
    probe: Probe = chromium_probe

    def all(self, chosen: Sequence[Package]) -> bool:
        """The lock, the tags, the stamps, the sync, the lint and the suites, recorded in
        the result; True when all passed."""
        root, run, result = self.root, self.run, self.result
        env = _base_env(self.environ)
        ok = run(["uv", "lock", "--check"], root, env) == 0
        result.lock = "pass" if ok else "fail"
        if not ok:
            return False
        ok = self.tags()
        ok = self.references() and ok
        ok = self.names() and ok
        ok = self.release() and ok
        if not chosen:
            result.stamps = result.web = result.sync = "skipped"
            return ok
        if not (_stamps(root, chosen, result) and _web(root, env, run, result)):
            return False
        venv = root / ENV_DIR
        synced = run(["uv", "sync", "--locked", "--all-packages", "--all-extras",
                      "--all-groups", "--python", PYTHON],
                     root, {**env, "UV_PROJECT_ENVIRONMENT": str(venv)}) == 0
        result.sync = "pass" if synced else "fail"
        if not synced:
            return False
        suite_env = {key: value for key, value in env.items()
                     if key not in SUITE_DROPPED_ENV}
        suite_env = {**suite_env, "VIRTUAL_ENV": str(venv),
                     "PATH": os.pathsep.join(filter(None, [str(venv_bin(venv)),
                                                           env.get("PATH")]))}
        result.lint = []
        for package in chosen:
            ok = self.lint(package, venv, suite_env) and ok
        return self.suites(chosen, venv, suite_env) and ok

    def selection_env(self, work: Path) -> dict[str, str]:
        """The kit's variables for what the suites run besides the plain run."""
        env = {}
        if self.changed is None:
            env[TIERS_ENV] = "all"
        else:
            listed = work / "changed-paths"
            listed.write_text("".join(path + "\0" for path in self.changed),
                              encoding="utf-8")
            env[CHANGED_ENV] = str(listed)
        if self.findings:
            env[FINDING_ENV] = ",".join(self.findings)
        return env

    def suites(self, chosen: Sequence[Package], venv: Path,
               suite_env: Mapping[str, str]) -> bool:
        """Each picked package's tests in the parallel pass, then the timed tests it held
        in the serial pass, then the durations; True when every suite passed (and, under
        --finding, some package had a test that carries it)."""
        run, result = self.run, self.result
        work = self.root / RUN_DIR
        work.mkdir(parents=True, exist_ok=True)
        env = {**suite_env, **self.selection_env(work)}
        python = str(venv_program(venv, "python"))
        ok, ran = True, False
        # Each package's (passed, tests) over both passes; passed None: it ran nothing.
        runs: dict[str, tuple[bool | None, dict | None]] = {}
        holding: list[Package] = []
        result.timed = []
        browsers = self.browsers(chosen, venv, suite_env)
        for package in chosen:
            where = work / f"{package.dir}.durations.json"
            where.unlink(missing_ok=True)
            began = time.monotonic()
            args, extra = browsers.get(package.dir, ((), {}))
            code = run([python, "-m", "pytest", *self.workers, *args], package.path,
                       {**env, **extra, TIMED_ENV: "hold", DURATIONS_ENV: str(where)})
            tests = durations.load_run(where)
            held = durations.load_held(where)
            passed: bool | None = code == 0
            if self.findings and tests is None and code in (0, NO_TESTS):
                print(f"moxi-dev check: {package.dir}: its tests don't load "
                      f"moxi_dev.testing, so --finding can't choose them", flush=True)
                passed = False
            elif code == NO_TESTS and (self.findings or held):
                passed = None
            result.suites.append((package.dir, passed, time.monotonic() - began))
            ok = ok and passed is not False
            ran = ran or passed is True
            runs[package.dir] = (passed, tests)
            if held:
                holding.append(package)
        for package in holding:
            # The serial pass: no workers, one package at a time, nothing else running.
            where = work / f"{package.dir}.timed.durations.json"
            where.unlink(missing_ok=True)
            began = time.monotonic()
            args, extra = browsers.get(package.dir, ((), {}))
            code = run([python, "-m", "pytest", *SERIAL, *args], package.path,
                       {**env, **extra, TIMED_ENV: "only", DURATIONS_ENV: str(where)})
            passed = code == 0
            result.timed.append((package.dir, passed, time.monotonic() - began))
            ok = ok and passed
            ran = ran or passed
            before, tests = runs[package.dir]
            both = False if before is False else passed
            runs[package.dir] = (both, durations.merged(tests, durations.load_run(where)))
        if self.findings and not ran and ok:
            print(f"moxi-dev check: no test in {', '.join(p.dir for p in chosen)} carries "
                  f"{', '.join(self.findings)}", flush=True)
            ok = False
        timed = {name: (passed, durations.PackageRun(
                     tiers=durations.tiers_of(tests, full=self.changed is None),
                     tests=tests))
                 for name, (passed, tests) in runs.items() if tests is not None}
        self.timings({name: one for name, (_, one) in timed.items()},
                     {name: one for name, (passed, one) in timed.items() if passed})
        return ok

    def browsers(self, chosen: Sequence[Package], venv: Path, suite_env: Mapping[str, str]
                 ) -> dict[str, tuple[Sequence[str], Mapping[str, str]]]:
        """For each picked package whose tests drive a browser, the pytest arguments and
        kit variables both its passes take: its browser tests run when Playwright's
        Chromium starts here, and are left out, with a printed line, when it can't."""
        browser = [package for package in chosen if package.browser]
        if not browser:
            return {}
        why = self.probe(venv_program(venv, "python"), suite_env)
        if why is None:
            return {package.dir: (("-m", WITH_BROWSER), {}) for package in browser}
        for package in browser:
            self.result.no_browser.append(package.dir)
            print(f"moxi-dev check: {package.dir}'s browser tests are left out: "
                  f"Playwright's Chromium can't start here ({why}); "
                  f"`uv run playwright install chromium` installs it",
                  file=sys.stderr, flush=True)
        return {package.dir: ((), {LEAVE_OUT_ENV: "browser"}) for package in browser}

    def timings(self, every: Mapping[str, durations.PackageRun],
                  passed: Mapping[str, durations.PackageRun]) -> None:
        """The summary of every suite's durations, the commit's record of those that
        passed, and the delta against main (`moxi_dev.durations`)."""
        result = self.result
        if not every:
            return
        result.durations = durations.summarize(every)
        directory = durations.records_dir(self.root)
        if directory is None:
            return
        clean = not result.commit.endswith("+dirty")
        if passed and clean and not self.findings:
            head = _git(self.root, "rev-parse", "HEAD").strip()
            try:
                result.kept = durations.keep(directory, head, passed)
            except OSError as exc:
                # The record is a by-product; the check's own results and its line come
                # first, so a disk that refuses the write costs the record, not the run.
                print(f"moxi-dev check: durations: keeping no record for {head[:12]} "
                      f"({exc})", file=sys.stderr, flush=True)
        if not self.findings:
            try:
                result.delta = durations.against_main(self.root, directory, passed,
                                                      self.base)
            except OSError as exc:
                print(f"moxi-dev check: durations: no delta ({exc})", file=sys.stderr,
                      flush=True)

    def tags(self) -> bool:
        try:
            hits = tags.scan(self.root)
        except (OSError, subprocess.CalledProcessError) as exc:
            raise CheckError(f"the tag check could not list the files ({exc})") from exc
        for hit in hits:
            print(f"moxi-dev check: {hit}", flush=True)
        self.result.tags = "fail" if hits else "pass"
        return not hits

    def release(self) -> bool:
        problems = release.scan(self.root)
        for problem in problems:
            print(f"moxi-dev check: {problem}", flush=True)
        self.result.release = "fail" if problems else "pass"
        return not problems

    def references(self) -> bool:
        try:
            hits = refs.scan(self.root)
        except (OSError, subprocess.SubprocessError) as exc:
            raise CheckError(f"the comment reference check could not read the tree ({exc})") from exc
        for hit in hits:
            print(f"moxi-dev check: {hit}", flush=True)
        self.result.refs = "fail" if hits else "pass"
        return not hits

    def names(self) -> bool:
        try:
            hits = naming.scan(self.root)
        except (OSError, subprocess.SubprocessError) as exc:
            raise CheckError(f"the naming check could not read the tree ({exc})") from exc
        for hit in hits:
            print(f"moxi-dev check: {hit}", flush=True)
        self.result.names = "fail" if hits else "pass"
        return not hits

    def lint(self, package: Package, venv: Path, suite_env: Mapping[str, str]) -> bool:
        """The package's lint commands, run in the check's environment; True when each
        passed. A package with none is left out of the result."""
        work = self.root / LINT_DIR / package.dir
        try:
            commands = self.plan(package.path, work, [p.path for p in self.packages])
        except lint.LintError as exc:
            print(f"moxi-dev check: lint {package.dir}: {exc}", flush=True)
            self.result.lint.append((package.dir, False))
            return False
        if not commands:
            return True
        ok = True
        for command in commands:
            print(f"moxi-dev check: lint {package.dir}: {command.what}", flush=True)
            env = {key: value for key, value in suite_env.items() if key != "PYTHONPATH"}
            if command.pythonpath:
                env["PYTHONPATH"] = os.pathsep.join(command.pythonpath)
            argv = [str(venv_program(venv, command.argv[0])), *command.argv[1:]]
            ok = self.run(argv, package.path, env) == 0 and ok
        self.result.lint.append((package.dir, ok))
        return ok


def _stamps(root: Path, chosen: Sequence[Package], result: Result) -> bool:
    try:
        checked, problems = stamp.check_stamps(root, only=[package.dir for package in chosen])
    except stamp.StampError as exc:
        raise CheckError(f"the build stamps could not be checked ({exc})") from exc
    for problem in problems:
        print(f"moxi-dev check: {problem}", file=sys.stderr, flush=True)
    result.stamps = "fail" if problems else "pass" if checked else "none"
    return not problems


def _web(root: Path, env: Mapping[str, str], run: Run, result: Result) -> bool:
    """`npm run check` when the stamps step found a page and Node can run it here."""
    if result.stamps != "pass":
        result.web = "none"
        return True
    path = env.get("PATH")
    found = {tool: shutil.which(tool, path=path) for tool in ("node", "npm")}
    missing = [tool for tool, where in found.items() if where is None]
    if missing or not (root / "node_modules").is_dir():
        why = (f"no {' or '.join(missing)} on PATH" if missing
               else "no node_modules at the root (run `npm ci`)")
        print(f"moxi-dev check: the built web files were not compared with their "
              f"sources: {why}", file=sys.stderr, flush=True)
        result.web = "skipped"
        return True
    # The path `which` found: on Windows npm is `npm.cmd`, which a bare `npm` can't start.
    ok = run([found["npm"], "run", "check"], root, env) == 0
    result.web = "pass" if ok else "fail"
    return ok


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="moxi-dev check",
        description="Run the checks a change must pass before it merges: the lock, the "
                    "tag, comment-reference and public-name checks and the release step, "
                    "then the import rules and the tests "
                    "of every package the change touches and of every package that "
                    "depends on one, in an environment of the check's own. Run it from "
                    "the repository's root.")
    parser.add_argument("--full", action="store_true",
                        help="check every package, whatever changed, and run every "
                             "test tier but live")
    parser.add_argument("--base", metavar="REV",
                        help="count the changes since HEAD left REV, and compare the "
                             "durations with REV's records (default: main)")
    parser.add_argument("--finding", metavar="L<n>", action="append", default=[],
                        help="run only the tests marked with this finding; may be given "
                             "more than once")
    parser.add_argument("--only", choices=("refs", "names"),
                        help="run only this whole-tree code check")
    parser.add_argument("paths", nargs="*", metavar="PATH",
                        help="with --only, print findings only under these paths")
    return parser


def _shown(path: str, selected: Sequence[str]) -> bool:
    if not selected:
        return True
    clean = path.removeprefix("./")
    return any(clean == one.removeprefix("./").rstrip("/")
               or clean.startswith(one.removeprefix("./").rstrip("/") + "/")
               for one in selected)


def only(root: Path, step: str, paths: Sequence[str] = ()) -> int:
    """Run one whole-tree code check and print its selected findings."""
    scanner = refs.scan if step == "refs" else naming.scan
    try:
        hits = scanner(root)
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"moxi-dev check: {step} could not read the tree ({exc})", file=sys.stderr)
        return 2
    shown = [hit for hit in hits if _shown(hit.path, paths)]
    for hit in shown:
        print(hit, flush=True)
    return 1 if shown else 0


def main(argv: list[str]) -> int:
    args = _parser().parse_args(argv)
    if args.paths and not args.only:
        _parser().error("PATH may be given only with --only")
    if args.only and (args.full or args.base or args.finding):
        _parser().error("--only cannot be combined with --full, --base or --finding")
    try:
        root = Path(_git(Path.cwd(), "rev-parse", "--show-toplevel").strip())
    except CheckError as exc:
        print(f"moxi-dev check: not in a git checkout ({exc})", file=sys.stderr)
        result = Result(code=2)
        print(result.line(), flush=True)
        return result.code
    if args.only:
        return only(root, args.only, args.paths)
    result = check(root, full=args.full, base=args.base, finding=args.finding)
    print(result.line(), flush=True)
    return result.code

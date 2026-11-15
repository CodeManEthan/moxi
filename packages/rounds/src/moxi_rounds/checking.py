"""`check`: run a build's check at its tip and record the run; and what a gate compares.

**What a gate compares.** A step's gate reads the newest `check.ran` of the build whose
`step` is that step's name, and no other record (`newest`). It passes at tip `T` when that
record's commit is `T`, its exit code is 0, and its argv is the argv the step's command
gives now, its placeholders filled for the build at `T` (`passes`, `why_not`). For the
repo's check that command is the repo's `check.command` as the settings have it when the
gate is read (`argv_now`). A pass at an older commit, a run of another step, and a pass of
a command the repo no longer declares do not count.

**The command line** is split as a shell would split it and run with no shell. Each word
may hold the placeholders `{round}`, `{stage}`, `{repo}`, `{root}`, `{branch}`, `{tip}`
and `{into}`, filled in for the build (`fill`); any other text in braces is left as it is.

**The run** (`check`), through the write path, with the round's lock held only to read
and to append:

1. Under the lock: the build of the stage is open; its repo declares a check; its
   worktree was made on this machine and is there, has the build's branch checked out
   and holds nothing uncommitted; its scratch folder is there; the build has work (it
   reads `building` or `merged`). The tip `T` is read then, with the merge-base of `T`
   and the target's head. Any of these that does not hold refuses the verb, having run
   nothing.
2. With no lock held: the argv runs in the worktree, stdin closed, with the caller's
   environment less every `GIT_` variable, its stdout and stderr written straight to
   `check-<n>.log` in the scratch folder (`n` one above the highest there), and its
   whole process tree ended at the time limit: `timeout_s` when given, else the check's
   own `timeout_s`, else none.
3. Under the lock again: the tip must still be `T` and the worktree still clean, so the
   commit recorded is what ran. If either changed, nothing is recorded and the verb says
   so. Otherwise `check.ran` is appended with the stage, the step, `T`, the merge-base,
   the argv as it ran, the exit code (None for a run ended at its time limit), the
   duration in seconds and the output file.

A run the caller interrupts records nothing: the interrupt ends the run's tree and the
verb before its append.
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from moxi import proc

from . import builds, folders, gitreads, open_builds, paths, places, write
from . import settings as own
from . import state as states
from .errors import BadArgument, Refused, RoundsError, Unmet
from .gitreads import Git, GitError

#: The step name a run of the repo's declared check is recorded under.
CHECK = "check"
#: The record of a run.
RAN = "check.ran"
#: What a command line's words may hold, each filled in for the build.
PLACEHOLDERS = ("round", "stage", "repo", "root", "branch", "tip", "into")
#: The states of a build that has work: what a check runs on.
WITH_WORK = (builds.BUILDING, builds.MERGED)
#: The name of a run's output file in the build's scratch folder, by its number.
OUTPUT = "check-{n}.log"

_OUTPUT_NAME = re.compile(r"check-([0-9]+)\.log")
_PLACEHOLDER = re.compile(r"\{(" + "|".join(PLACEHOLDERS) + r")\}")


# -- what a gate compares ----------------------------------------------------------------

def fill(words: Sequence[str], values: Mapping[str, str]) -> list[str]:
    """`words` with each placeholder of `PLACEHOLDERS` replaced by its value in
    `values`. Text in braces that is no placeholder, or one `values` lacks, is kept."""
    def one(match: re.Match) -> str:
        value = values.get(match.group(1))
        return match.group(0) if value is None else str(value)

    return [_PLACEHOLDER.sub(one, str(word)) for word in words]


def declared(settings, repo: str) -> list[str] | None:
    """The words of the repo's declared check, split as a shell would, with their
    placeholders as written; None when the repo declares no check."""
    found = own.build_setting(settings, repo, "check")
    if not isinstance(found, Mapping) or not isinstance(found.get("command"), str):
        return None
    return shlex.split(found["command"])


def values(name: str, build: builds.Build, *, root: str | os.PathLike,
           tip: str) -> dict[str, str]:
    """What the placeholders read for the build `build` of the round `name` at the tip
    `tip`, its repo's root being `root`."""
    return {"round": name, "stage": build.stage, "repo": build.repo, "root": str(root),
            "branch": build.branch, "tip": tip, "into": build.target}


def argv_now(settings, name: str, build: builds.Build, *, root: str | os.PathLike,
             tip: str) -> list[str] | None:
    """The argv the repo's check gives now for the build at `tip`, placeholders filled;
    None when the repo declares no check."""
    words = declared(settings, build.repo)
    if words is None:
        return None
    return fill(words, values(name, build, root=root, tip=tip))


def runs(state: states.State, stage: str, step: str) -> list[dict]:
    """Every `check.ran` of the stage's last build recorded under `step`, oldest first.
    A run of an earlier build of the stage, one that was dropped, is not its."""
    build = builds.current(state, stage)
    if build is None:
        return []
    return [event for event in state.history
            if event["n"] > build.n and event["type"] == RAN
            and event.get("stage") == stage and event.get("step") == step]


def newest(state: states.State, stage: str, step: str) -> dict | None:
    """The newest `check.ran` of the stage's last build under `step`, or None."""
    found = runs(state, stage, step)
    return found[-1] if found else None


def why_not(state: states.State, stage: str, step: str, *, tip: str,
            argv: Sequence[str]) -> str | None:
    """Why the newest run of `step` for the stage does not pass at `tip` with `argv`,
    in words; None when it passes."""
    run = newest(state, stage, step)
    if run is None:
        return f"{step} has no run on record for {stage}"
    if run.get("commit") != tip:
        return (f"the newest run of {step} for {stage} was at "
                f"{str(run.get('commit'))[:12]}, not at the tip {tip[:12]}")
    if run.get("exit_code") != 0:
        code = run.get("exit_code")
        how = "was ended at its time limit" if code is None else f"exited {code}"
        return f"the newest run of {step} for {stage}, at {tip[:12]}, {how}"
    if list(run.get("argv") or ()) != list(argv):
        return (f"the newest run of {step} for {stage} passed at {tip[:12]}, but ran "
                f"{shlex.join(run.get('argv') or ())}, and the command is now "
                f"{shlex.join(argv)}")
    return None


def passes(state: states.State, stage: str, step: str, *, tip: str,
           argv: Sequence[str]) -> bool:
    """Whether the newest run of `step` for the stage passed at `tip` with `argv`."""
    return why_not(state, stage, step, tip=tip, argv=argv) is None


# -- what is read of a build -------------------------------------------------------------

def commission_of(state: states.State, build: builds.Build) -> dict:
    """The `build.commissioned` event of `build`."""
    return next(event for event in state.history if event["n"] == build.n)


@dataclass(frozen=True)
class Tip:
    """The build's worktree as read now: the branch's `tip`, and whether the worktree
    `clean`ly holds nothing uncommitted with the build's branch checked out."""

    tip: str | None
    clean: bool


def read_tip(worktree: Path, branch: str) -> Tip:
    """The tip of `branch` and whether the worktree at `worktree` is clean on it.
    Raises `GitError` when git can't read it."""
    git = Git(worktree)
    clean = git.checked_out() == branch and not git.has_changes()
    return Tip(git.branch(branch), clean)


def seen_of(state: states.State, name: str, *, settings,
            read: Mapping[str, open_builds.OpenBuild] | None = None) -> dict:
    """What git and the settings say now of each open build of the round `name`, by
    stage, as the step function reads it (`steps.Seen`). `read` is the round's open
    builds by stage as `open_builds` read them, read here when left out. A build that
    can't be read is given with its problem. Nothing is written."""
    from . import steps

    opened = [one for one in builds.builds(state) if one.ended is None
              and builds.current(state, one.stage) == one]
    if not opened or state.status != states.OPEN:
        return {}
    if read is None:
        read = open_builds.of_round(name, [one.repo for one in opened], settings=settings)
    found = {}
    for build in opened:
        now = read.get(build.stage)
        if now is None or now.problem is not None or now.status is None:
            why = (f"the repo {build.repo} could not be read" if now is None
                   or now.problem is None else now.problem.reason)
            found[build.stage] = steps.Seen(problem=Unmet(
                f"{build.stage} can't be read from git: {why}", f"moxi rounds show {name}"))
            continue
        commissioned = commission_of(state, build)
        worktree = Path(str(commissioned.get("worktree") or ""))
        try:
            _key, root = folders.root(build.repo)
            tip = Git(root).branch(build.branch)
            here = (commissioned.get("host") in (None, places.host())
                    and commissioned.get("worktree") and worktree.is_dir())
            clean = bool(here) and read_tip(worktree, build.branch).clean
        except RoundsError as error:
            found[build.stage] = steps.Seen(problem=Unmet(
                f"{build.stage} can't be read from git: {error.reason}",
                f"moxi rounds show {name}"))
            continue
        argv = None if tip is None else argv_now(settings, name, build, root=root, tip=tip)
        found[build.stage] = steps.Seen(
            state=now.status.state, tip=tip, clean=clean,
            check=None if argv is None and declared(settings, build.repo) is None
            else tuple(argv or ()))
    return found


# -- the run -----------------------------------------------------------------------------

@dataclass(frozen=True)
class Ran:
    """A run as `check` recorded it: the round's `name`, the `stage`, the `step` it ran
    as, the `commit` (the tip), its `merge_base` with the target's head, the `argv`,
    the `exit_code` (None when the run was ended at its time limit), the run's
    `duration_s`, the `output` file, the `event` written and the after-append
    callbacks that `failed`."""

    name: str
    stage: str
    step: str
    commit: str
    merge_base: str | None
    argv: tuple[str, ...]
    exit_code: int | None
    duration_s: float
    output: Path
    event: dict
    failed: tuple = ()

    @property
    def passed(self) -> bool:
        return self.exit_code == 0


@dataclass
class _Look:
    unmet: list[Unmet]
    build: builds.Build | None = None
    worktree: Path | None = None
    scratch: Path | None = None
    tip: str | None = None
    merge_base: str | None = None
    argv: list[str] | None = None
    timeout_s: float | None = None


def check(name: str, stage: str, *, actor: str, timeout_s: float | None = None,
          settings=None, wait_s: float = write.WAIT_S, at: str | None = None,
          env=None) -> Ran:
    """Run the repo's check for the build of `stage` in the round `name` at its tip,
    and record the run. `timeout_s` is the run's time limit in place of the check's
    own. `settings` is what `settings.load()` gives, read now when left out.

    Raises `BadArgument` for no stage or a time limit that is not above 0, what
    `folders.home` raises for a round that can't be found, and `Refused` with every
    condition that does not hold before the run, or with what changed during it, in
    which case nothing is recorded."""
    if not isinstance(stage, str) or not stage.strip():
        raise BadArgument("no stage is named, so no build is known to check",
                          f"moxi rounds check {name} B<n>, or run moxi rounds check in "
                          "the build's worktree")
    if timeout_s is not None and (isinstance(timeout_s, bool) or timeout_s <= 0):
        raise BadArgument(f"{timeout_s!r} is no time limit",
                          "give --timeout-s a number of seconds above 0")
    project, slug = folders.split(name)
    settings = own.load() if settings is None else settings
    home = folders.home(project, settings)
    name = f"{home.project}/{slug}"
    again = f"moxi rounds check {name} {stage}"
    looked: dict[int, _Look] = {}

    def look(view) -> _Look:
        if id(view) not in looked:
            looked[id(view)] = _look(view, name=name, stage=stage, settings=settings,
                                     again=again, timeout_s=timeout_s)
        return looked[id(view)]

    def act(view):
        found = look(view)
        output = _output_file(found.scratch)
        started = time.monotonic()
        try:
            done = proc.run(found.argv, timeout_s=found.timeout_s,
                            env=gitreads.environment(), cwd=found.worktree, output=output)
            code: int | None = done.returncode
        except subprocess.TimeoutExpired:
            code = None
        except OSError as error:
            raise RoundsError(f"the check {shlex.join(found.argv)} could not be started "
                              f"in {found.worktree}: {error}",
                              "fix the check's command in the settings, then "
                              f"{again}") from None
        return code, round(time.monotonic() - started, 3), output

    first: list[_Look] = []

    def event(view, result):
        before = first[0]
        moved = _moved(before, name=name, stage=stage, again=again)
        if moved:
            raise Refused(moved)
        code, duration_s, output = result
        return write.Entry(RAN, {
            "stage": stage, "step": CHECK, "commit": before.tip,
            "merge_base": before.merge_base, "argv": list(before.argv),
            "exit_code": code, "duration_s": duration_s, "output": str(output)})

    def gate_and_keep(view):
        found = look(view)
        if not first:
            first.append(found)
        return found.unmet

    written = write.record(
        write.Where(home.project, home.folder, slug), actor=actor,
        gates=[gate_and_keep],
        act=act, event=event, long_act=True, wait_s=wait_s, at=at, env=env)
    one = written.events[-1]
    return Ran(name=name, stage=stage, step=CHECK, commit=one["commit"],
               merge_base=one["merge_base"], argv=tuple(one["argv"]),
               exit_code=one["exit_code"], duration_s=one["duration_s"],
               output=Path(one["output"]), event=one, failed=written.failed)


def _moved(before: _Look, *, name: str, stage: str, again: str) -> list[Unmet]:
    """What changed in the worktree while the run ran, read again now: the tip, or
    anything uncommitted. Empty when neither did."""
    try:
        now = read_tip(before.worktree, before.build.branch)
    except GitError as error:
        return [Unmet(f"the worktree {before.worktree} can't be read after the run, so "
                      f"nothing is recorded: {error.reason}", again)]
    unmet = []
    if now.tip != before.tip:
        unmet.append(Unmet(
            f"the tip of {before.build.branch} moved from {before.tip[:12]} to "
            f"{(now.tip or 'nothing')[:12]} while the check ran, so nothing is recorded: "
            "the commit that ran is not known", again))
    if not now.clean:
        unmet.append(Unmet(
            f"the worktree {before.worktree} changed while the check ran, so nothing is "
            "recorded: what ran is not the commit", f"commit or put away the changes, "
            f"then {again}"))
    return unmet


def _output_file(scratch: Path) -> Path:
    """The run's output file in the scratch folder: `check-<n>.log`, `n` one above the
    highest of the files there. The run's start creates it."""
    taken = [int(match.group(1)) for one in scratch.iterdir()
             if (match := _OUTPUT_NAME.fullmatch(one.name))]
    return scratch / OUTPUT.format(n=max(taken, default=0) + 1)


def _look(view, *, name: str, stage: str, settings, again: str,
          timeout_s: float | None) -> _Look:
    found = _Look(unmet=[])
    state = view.state
    show = f"moxi rounds show {name}"
    build = builds.current(state, stage)
    if build is None or build.ended is not None or state.status != states.OPEN:
        how = ("was never commissioned" if build is None
               else f"has no open build: its last one is {build.ended}"
               if build.ended is not None else "is in a round that has ended")
        found.unmet.append(Unmet(f"{stage} of {name} {how}, so there is nothing to check",
                                 show))
        return found
    found.build = build
    config = paths.LAYOUT.config_file()
    words = declared(settings, build.repo)
    if words is None:
        found.unmet.append(Unmet(
            f"{build.repo} declares no check, so a build in it has no check step",
            f'set check = {{ command = "...", timeout_s = N }} in '
            f"[projects.{build.repo}] of {config}"))
        return found
    commissioned = commission_of(state, build)
    worktree = Path(str(commissioned.get("worktree") or ""))
    scratch = Path(str(commissioned.get("scratch") or ""))
    host = commissioned.get("host")
    if host is not None and host != places.host():
        found.unmet.append(Unmet(
            f"the worktree of {stage} was made on {host}, and the check runs in it there",
            f"run moxi rounds check on {host}"))
        return found
    if not commissioned.get("worktree") or not worktree.is_dir():
        found.unmet.append(Unmet(f"the worktree {worktree} of {stage} is not there",
                                 show))
        return found
    if not commissioned.get("scratch") or not scratch.is_dir():
        found.unmet.append(Unmet(
            f"the scratch folder {scratch} of {stage}, where a run's output goes, is "
            "not there", show))
    found.worktree, found.scratch = worktree, scratch
    try:
        _key, root = folders.root(build.repo)
    except RoundsError as error:
        found.unmet.append(Unmet(error.reason, error.fix or "moxi projects list"))
        return found
    try:
        found.unmet += _read_git(found, Git(root), worktree, build, name=name, stage=stage,
                                 settings=settings)
    except GitError as error:
        found.unmet.append(Unmet(error.reason, error.fix or again))
        return found
    if found.tip is not None:
        found.argv = fill(words, values(name, build, root=root, tip=found.tip))
    check_timeout = own.build_setting(settings, build.repo, "check").get("timeout_s")
    found.timeout_s = timeout_s if timeout_s is not None else check_timeout
    return found


def _read_git(found: _Look, git: Git, worktree: Path, build: builds.Build, *, name: str,
              stage: str, settings) -> list[Unmet]:
    unmet = []
    here = Git(worktree)
    checked = here.checked_out()
    if checked != build.branch:
        unmet.append(Unmet(
            f"the worktree {worktree} has {checked or 'a detached head'} checked out, not "
            f"{build.branch}, the branch of {stage}",
            f"git -C {shlex.quote(str(worktree))} switch {shlex.quote(build.branch)}"))
    if here.has_changes():
        unmet.append(Unmet(
            f"the worktree {worktree} holds changes not committed, and a check runs on a "
            "commit", "commit them, or put them away, then run the check again"))
    tip = git.branch(build.branch)
    if tip is None:
        unmet.append(Unmet(f"the branch {build.branch} of {stage} is not in "
                           f"{build.repo}", f"moxi rounds show {name}"))
        return unmet
    read = open_builds.of_round(name, [build.repo], settings=settings).get(stage)
    if read is None or read.problem is not None or read.status is None:
        why = ("its state can't be read" if read is None or read.problem is None
               else read.problem.reason)
        unmet.append(Unmet(f"whether {stage} has work is not known: {why}",
                           f"moxi rounds show {name}"))
    elif read.status.state not in WITH_WORK:
        unmet.append(Unmet(
            f"{stage} has no work to check: it reads {read.status.state} "
            f"({read.status.why})",
            f"commit the work on {build.branch}, then run the check"))
    target_head = git.branch(build.target)
    bases = () if target_head is None else git.merge_bases(tip, target_head)
    found.tip = tip
    found.merge_base = bases[0] if len(bases) == 1 else None
    return unmet


__all__ = ["CHECK", "RAN", "PLACEHOLDERS", "WITH_WORK", "OUTPUT", "fill", "declared",
           "values", "argv_now", "runs", "newest", "why_not", "passes", "commission_of",
           "Tip", "read_tip", "seen_of", "Ran", "check"]

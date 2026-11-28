"""`check`: run a build's check, or a command step of the user's own, at its tip and
record the run; and what a gate compares.

**What a gate compares.** A step's gate reads the newest `check.ran` of the build whose
`step` is that step's name, and no other record (`newest`). It passes at tip `T` when that
record's commit is `T`, its exit code is 0, and its argv is the argv the step's command
gives now, its placeholders filled for the build at `T` (`passes`, `why_not`), both read
word by word through `places.expand_home`: a run recorded with a path under the home
folder as `~` counts against the command that names the whole path. For the
repo's check that command is the repo's `check.command` as the settings have it when the
gate is read (`argv_now`); for a command step of the user's own (`check --step NAME`),
the `argv` of the step's entry in the round's recorded flow (`step_entry`). A pass at an
older commit, a run of another step, and a pass of a command the repo no longer declares
do not count. A run of one step is never read for another.

**A queue project's check** (`land = "queue"`) is the repo's `check.scoped` command,
`{base}` filled in with the merge-base of `T` and the target's head: the check of the
packages the build touched; the candidate's CI run adds every package that depends on
them. It pushes nothing and reads no CI.
`--full` runs the repo's `check.command` there instead, as in any other project, and
records it; `land` is neither read for it nor changed. The step is met at `T` by the
newest run passing with either argv as the settings give it now (`steps.Seen.check`,
`steps.Seen.full_check`). The scoped gate's argv fills `{base}` with the newest run's
own `merge_base` when that run is at `T`, and with the merge-base read from git
otherwise; the merge-base of `T` and the target does not change as the target moves
forward, so the record stays good while the build waits in the queue.

**The command line** is split as a shell would split it and run with no shell; a step of
the user's own gives its words as a list. Each word may hold the placeholders `{round}`, `{stage}`, `{repo}`, `{root}`, `{branch}`, `{tip}`
and `{into}`, filled in for the build (`fill`); any other text in braces is left as it is.
The repo's check and `ci.local` may also hold `{memory_mib}`, filled in as their words are
read with their own table's `memory_mib` (`settings.fill_memory`), and left as written
where that table declares none.

**The run** (`check`), through the write path, with the round's lock held only to read
and to append:

1. Under the lock: the build of the stage is open; its repo declares a check, or the
   round's flow has the command step named; its worktree was made on this machine and is there, has the build's branch checked out
   and holds nothing uncommitted; its scratch folder is there; the build has work (it
   reads `building` or `merged`); no commit from the tip not on the target's head holds
   a private file of the repo (finish's gate 11, `private_files.held`). The tip `T` is
   read then, with the merge-base of `T`
   and the target's head. Any of these that does not hold refuses the verb, having run
   nothing.
2. With the round's lock released, the machine's check lock is taken (below), and the
   argv runs in the worktree, stdin closed, with the caller's environment less every
   `GIT_` variable, its stdout and stderr written straight to `check-<n>.log` in the
   scratch folder (`n` one above the highest there). At the time limit, `timeout_s`
   when given, else the check's or the step's own `timeout_s`, else
   `DEFAULT_TIMEOUT_S`, the run's whole process group gets SIGINT, the signal test
   runners stop cleanly on, and `GRACE_S` later SIGKILL (on Windows the tree is ended
   at once). The limit counts the run only, never the wait for the lock.
3. Under the lock again: the tip must still be `T` and the worktree still clean, so the
   commit recorded is what ran. If either changed, nothing is recorded and the verb says
   so. Otherwise `check.ran` is appended with the stage, the step, `T`, the merge-base,
   the argv as it ran, the exit code (None for a run ended at its time limit), the
   duration in seconds and the output file, each path under the home folder written
   as `places.contract_home` gives it.

A run the caller interrupts records nothing: the interrupt ends the run's tree and the
verb before its append.

**The check lock.** At most `check_lock.at_once` runs at a time on this machine (one by
default), across every repo and round, so builds of parallel rounds don't run whole test
suites side by side past what memory holds: each run, the local part of a check by CI and
a command step's included, holds a place of the machine's check lock (`check_line.slot`)
from just before its command starts until everything the command started has ended,
however it ends; a check's or a command step's run holds it until its `check.ran` is
appended, or refused, too. The place's lock is the process's, so the system frees it when the verb
dies, and the command does not inherit it. Runs take places in the order they asked for
them: each takes a ticket in the lock's line and tries only once no live ticket is ahead
of it. A run starts beside others only when its command's table (the check, or
`ci.local`) and every holder declare `memory_mib` and the free memory covers the need;
a command step of the user's own declares none, so it runs only alone. The run that holds
a place keeps a holder record naming its round, stage, step, repo, output file, the
place, its `memory_mib` and its command's tag and first process, which the doctor's
check lock row reads. The command runs with that tag (`moxi.proc.run`'s `tag`,
`on_start` and `end_leftovers`): its process group and every process carrying the tag
are ended before the place is let go, so a place whose rounds process died stays taken
while its command still runs. A run that must wait says once why, and again when the
reason changes, then where it got in; with `wait=False` (`--no-wait`) it takes a place
only when no ticket waits and one can be taken now, and is refused at once otherwise,
naming the holders, the number waiting and, when memory is short, the reading and the
need, with nothing run or recorded. The record of a run names its place (`check_lock`)
and, for one started beside others, the reading and the need it met. A wait that a tip
moved during is caught by step 3.

**Lanes.** A project may send its check to lanes, other machines its
`[projects.<key>.lanes.<name>]` tables name (`sending`). Only a run of the repo's check
as this machine would run it may go: the scoped check and `--full` in a queue project,
`--full` in a project with CI, the declared check in any other; a check by CI and a
command step of the user's own run here. Such a run chooses where it runs before it
takes any place here (`_choose`): here when a place is free now, else the first lane, in
the project's order, that takes it now, else here, waiting in line. `urgent` asks no
lane; `lane` sends it to that lane, waiting in that machine's line. A run on a lane runs
the lane's words (`lane_words`), holds no place here, and is recorded with `lane`,
`lane_host` and `lane_check_lock` in place of `check_lock`, its argv filled with this
machine's root. The gate compares it with the words that lane's table gives now, picked
by the newest run's `lane` (`run_lane`, `argv_now`): a run on a lane the project no
longer names counts for nothing.
"""

from __future__ import annotations

import functools
import os
import re
import shlex
import subprocess
import time
from collections.abc import Iterator, Mapping, Sequence
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path

from moxi import proc

from . import (builds, check_line, findings, flows, folders, gitreads, open_builds,
               paths, places, write)
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
#: How long the push before a read of CI may take.
PUSH_TIMEOUT_S = 300
#: The name of a run's output file in the build's scratch folder, by its number.
OUTPUT = "check-{n}.log"
#: The time limit of a run with no `timeout_s` from the call, the check or the step.
DEFAULT_TIMEOUT_S = 3600.0
#: How long a run ended at its time limit has between SIGINT and SIGKILL.
GRACE_S = 30.0
#: The machine's check lock, in rounds' folder of lock files.
LOCK_FILE = "check.lock"

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


def declared(settings, repo: str, lane: str | None = None) -> list[str] | None:
    """The words of the repo's declared check, split as a shell would, with their
    placeholders as written but `{memory_mib}`, filled from the check's own
    `memory_mib` where it declares one; None when the repo declares no check. With
    `lane`, the words that lane runs (`lane_words`), None when the project names no
    such lane."""
    found = own.build_setting(settings, repo, "check")
    if not isinstance(found, Mapping) or not isinstance(found.get("command"), str):
        return None
    if lane is not None:
        return lane_words(settings, repo, lane, "command")
    return own.fill_memory(shlex.split(found["command"]), found)


def lane_words(settings, repo: str, lane: str, key: str) -> list[str] | None:
    """The words the check's `key` (`command` or `scoped`) runs on the lane `lane`: the
    project's lane table's own, else the project's, split as a shell would, with
    `{memory_mib}` filled from the lane table's `memory_mib`, else the check's own, else
    kept as written. None when the project names no such lane or has no such words."""
    check = own.build_setting(settings, repo, "check")
    used = own.project_lanes(settings, repo).get(lane)
    if used is None or not isinstance(check, Mapping):
        return None
    text = used.get(key) if isinstance(used.get(key), str) else check.get(key)
    if not isinstance(text, str):
        return None
    return own.fill_memory(own.fill_memory(shlex.split(text), used), check)


def lane_memory_mib(settings, repo: str, lane: str) -> int | None:
    """What one run of the check on the lane `lane` holds at most: the project's lane
    table's `memory_mib`, else the check's own; None when neither declares one."""
    used = own.project_lanes(settings, repo).get(lane)
    found = own.memory_mib(used)
    return found if found is not None else own.memory_mib(
        own.build_setting(settings, repo, "check"))


def values(name: str, build: builds.Build, *, root: str | os.PathLike,
           tip: str) -> dict[str, str]:
    """What the placeholders read for the build `build` of the round `name` at the tip
    `tip`, its repo's root being `root`."""
    return {"round": name, "stage": build.stage, "repo": build.repo, "root": str(root),
            "branch": build.branch, "tip": tip, "into": build.target}


def queued(settings, repo: str) -> bool:
    """Whether the repo's builds land through its queue (`land = "queue"`)."""
    return own.build_setting(settings, repo, "land") == own.LAND_QUEUE


def scoped(settings, repo: str, lane: str | None = None) -> list[str] | None:
    """The words of a queue project's `check.scoped`, placeholders and `{base}` as
    written but `{memory_mib}`, filled as `declared` fills it; None when the repo is no
    queue project or names no scoped command. With `lane`, the words that lane runs
    (`lane_words`)."""
    found = own.build_setting(settings, repo, "check")
    if not queued(settings, repo) or not isinstance(found, Mapping) or not isinstance(
            found.get("scoped"), str):
        return None
    if lane is not None:
        return lane_words(settings, repo, lane, "scoped")
    return own.fill_memory(shlex.split(found["scoped"]), found)


def fill_base(words: Sequence[str], base: str | None) -> list[str]:
    """`words` with `{base}` replaced by the merge-base `base`; kept where it is None."""
    if base is None:
        return list(words)
    return [str(word).replace(own.BASE_FIELD, base) for word in words]


def argv_now(settings, name: str, build: builds.Build, *, root: str | os.PathLike,
             tip: str, state: states.State | None = None) -> list[str] | None:
    """The argv the repo's check gives now for the build at `tip`, placeholders filled;
    None when the repo declares no check. In a queue project it is the scoped command,
    `{base}` filled in with the newest run's own merge-base when that run is at `tip`
    and `state` is given, else with the merge-base git reads now. With `state`, the
    words are picked by the newest run's `lane` (`run_lane`): that lane's words when the
    project still names it, and an empty argv, which no run passes, when it names a lane
    the project no longer names."""
    lane = run_lane(state, build.stage)
    words = scoped(settings, build.repo, lane) or declared(settings, build.repo, lane)
    if words is None:
        return None if lane is None or declared(settings, build.repo) is None else []
    filled = fill(words, values(name, build, root=root, tip=tip))
    if not queued(settings, build.repo):
        return filled
    run = None if state is None else newest(state, build.stage, CHECK)
    if run is not None and run.get("commit") == tip and run.get("merge_base"):
        return fill_base(filled, run["merge_base"])
    target_head = Git(root).branch(build.target)
    bases = () if target_head is None else Git(root).merge_bases(tip, target_head)
    return fill_base(filled, bases[0] if len(bases) == 1 else None)


def run_lane(state: states.State | None, stage: str) -> str | None:
    """The lane the newest run of the stage's check ran on; None when it ran on this
    machine, or with no run or no `state`."""
    run = None if state is None else newest(state, stage, CHECK)
    lane = None if run is None else run.get("lane")
    return lane if isinstance(lane, str) else None


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
    lane = run.get("lane")
    on = f" on lane {lane}" if lane else ""
    if lane and not argv:
        return (f"the newest run of {step} for {stage} passed at {tip[:12]} on lane "
                f"{lane}, which the project no longer sends checks to")
    if _words(run.get("argv") or ()) != _words(argv):
        return (f"the newest run of {step} for {stage} passed at {tip[:12]}{on}, but ran "
                f"{shlex.join(run.get('argv') or ())}, and the command{on} is now "
                f"{shlex.join(argv)}")
    return None


def _words(argv: Sequence[str]) -> list[str]:
    """`argv` as a recorded argv and a current one are compared: each word read as a
    path a record holds is read."""
    return [places.expand_home(word) for word in argv]


def recorded_argv(argv: Sequence[str]) -> list[str]:
    """`argv` as a record holds it: each word that is a path under the home folder in
    its `~` form."""
    return [places.contract_home(str(word)) for word in argv]


def passes(state: states.State, stage: str, step: str, *, tip: str,
           argv: Sequence[str]) -> bool:
    """Whether the newest run of `step` for the stage passed at `tip` with `argv`."""
    return why_not(state, stage, step, tip=tip, argv=argv) is None


# -- what is read of a build -------------------------------------------------------------

def step_entry(state: states.State, step: str) -> Mapping | None:
    """The entry of the command step `step` of the user's own in the round's recorded
    flow, or None when the flow has no such step."""
    for entry in state.steps:
        if (entry.get("step") == step and flows.is_user_step(entry)
                and entry.get("does") == flows.COMMAND):
            return entry
    return None


def command_steps(state: states.State) -> tuple[str, ...]:
    """The names of the command steps of the user's own in the round's recorded flow,
    in the flow's order."""
    return tuple(str(entry.get("step")) for entry in state.steps
                 if flows.is_user_step(entry) and entry.get("does") == flows.COMMAND)


def report_steps(state: states.State) -> tuple[str, ...]:
    """The names of the report steps of the user's own in the round's recorded flow,
    in the flow's order."""
    return tuple(str(entry.get("step")) for entry in state.steps
                 if flows.is_user_step(entry) and entry.get("does") == flows.REPORT)


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
            read: Mapping[str, open_builds.OpenBuild] | None = None,
            tips: Mapping[str, str] | None = None) -> dict:
    """What git and the settings say now of each open build of the round `name`, by
    stage, as the step function reads it (`steps.Seen`). `read` is the round's open
    builds by stage as `open_builds` read them, read here when left out. `tips` holds,
    by stage, the commit to read a build at as its branch's tip, in every read that
    names that tip; the others are read at their branch's tip as it is now. A build
    that can't be read is given with its problem. Under no stage (`None`) is what the
    round's own steps read: the project's `ratify` setting, given whatever is open
    where the round's flow has a `ratify` step, and what `commission` refuses on while
    `design.md` changed since its current version was frozen. Nothing is written."""
    from . import closing, skips, steps, versions

    found: dict = {}
    ratify = closing.RATIFY_DEFAULT
    if flows.has(state.steps, closing.RATIFY):
        ratify = closing.ratify_of(settings, name)
        found[None] = steps.Seen(ratify=ratify)
    if (flows.has(state.steps, steps.DESIGN) and not skips.is_skipped(state, steps.DESIGN)
            and versions.current(state) is not None):
        project, slug = folders.split(name)
        moved = versions.unchanged(state, folders.home(project, settings).round_dir(slug),
                                   name)
        if moved is not None:
            # What sits under no stage reads as the settings read, so ratify is too.
            found[None] = steps.Seen(ratify=closing.ratify_of(settings, name),
                                     design=moved)
    opened = [one for one in builds.builds(state) if one.ended is None
              and builds.current(state, one.stage) == one]
    if not opened or state.status != states.OPEN:
        return found
    if read is None:
        read = open_builds.of_round(name, [one.repo for one in opened], settings=settings,
                                    tips=tips)
    tips = tips or {}
    for build in opened:
        now = read.get(build.stage)
        if now is None or now.problem is not None or now.status is None:
            why = (f"the repo {build.repo} could not be read" if now is None
                   or now.problem is None else now.problem.reason)
            found[build.stage] = steps.Seen(problem=Unmet(
                f"{build.stage} can't be read from git: {why}", f"moxi rounds show {name}"))
            continue
        commissioned = commission_of(state, build)
        worktree = Path(places.expand_home(commissioned.get("worktree") or ""))
        try:
            _key, root = folders.root(build.repo)
            tip = tips.get(build.stage) or Git(root).branch(build.branch)
            here = (places.made_here(commissioned, unrecorded_is_here=True)
                    and commissioned.get("worktree") and worktree.is_dir())
            clean = bool(here) and read_tip(worktree, build.branch).clean
            head = tip if tip is not None else now.status.head
            tree = _tree(commissioned, worktree, here=bool(here))
            before = now.status.target_before
            holds = (None if before is None or head is None
                     or set(Git(root).present([before, head])) != {before, head}
                     else Git(root).is_ancestor(before, head))
            since, moved = _since_verdict(state, build, now.status, Git(root),
                                          head=head, settings=settings)
            reported = {step: _since_verdict(state, build, now.status, Git(root),
                                             head=head, settings=settings, step=step)
                        for step in report_steps(state)}
            private = _private_held(settings, build, commissioned, Git(root), head)
        except RoundsError as exc:
            found[build.stage] = steps.Seen(problem=Unmet(
                f"{build.stage} can't be read from git: {exc.reason}",
                f"moxi rounds show {name}"))
            continue
        # The check is read at the head, which is the tip while the branch is there:
        # a build whose branch is gone is finished at its head, so a run there counts.
        argv = None if head is None else argv_now(settings, name, build, root=root,
                                                  tip=head, state=state)
        found[build.stage] = steps.Seen(
            state=now.status.state, tip=tip, clean=clean,
            check=None if argv is None and declared(settings, build.repo) is None
            else tuple(argv or ()),
            values=None if head is None else values(name, build, root=root, tip=head),
            full_check=_full_check(settings, name, build, root=root, tip=head,
                                   state=state),
            ci=(None if queued(settings, build.repo)
                else _ci_now(settings, build, name=name, root=root, tip=head)),
            head=head, since=since, drift=moved, reported=reported,
            target_before=before, holds_target=holds, tree=tree,
            check_on=own.build_setting(settings, build.repo, "check_on"),
            land=own.build_setting(settings, build.repo, "land"),
            actions=tuple(own.build_setting(settings, build.repo, "finish")),
            shares=tuple(now.shares), ratify=ratify, private=private)
    return found


def _full_check(settings, name: str, build: builds.Build, *, root,
                tip: str | None, state: states.State | None = None
                ) -> tuple[str, ...] | None:
    """In a queue project, the argv the repo's `check.command` gives now at `tip`, which
    meets the check step as the scoped argv does, picked by the newest run's lane as
    `argv_now` picks it; None elsewhere, or with no tip or no such command."""
    if tip is None or declared(settings, build.repo) is None or not queued(
            settings, build.repo):
        return None
    words = declared(settings, build.repo, run_lane(state, build.stage))
    if words is None:
        return ()
    return tuple(fill(words, values(name, build, root=root, tip=tip)))


def _private_held(settings, build: builds.Build, commissioned: Mapping, git: Git,
                  tip: str | None) -> tuple:
    """Each private file of the build some commit from `tip` not on its target's head
    holds (`private_files.held`); none where the tip or the target can't be read."""
    from . import private_files

    paths = private_files.private_paths(settings, build.repo, commissioned.get("brought"))
    target_head = git.branch(build.target)
    if not paths or tip is None or target_head is None:
        return ()
    return tuple(private_files.held(git, paths, tip, target_head))


def _tree(commissioned: Mapping, worktree: Path, *, here: bool) -> str | None:
    """The build's worktree as `finish` reads it: None when it no longer exists,
    `elsewhere` when it was made on another machine, else whether it is clean by the
    removal rule's read of git (`removal.changes`), which no setting of the user's
    hides work from. Ignored paths are left to the removal; a tracked change or an
    untracked path is a change."""
    from . import finish_gates, removal

    if not places.made_here(commissioned, unrecorded_is_here=True):
        return finish_gates.ELSEWHERE
    if not here:
        return None
    return finish_gates.CHANGED if removal.changes(worktree) else finish_gates.CLEAN


def _since_verdict(state: states.State, build: builds.Build, status: builds.Status,
                   git: Git, *, head: str | None, settings,
                   step: str = findings.VERIFY_BUILD):
    """The commits from the head the build's newest verdict read to `head`, None when
    it has no verdict or git does not hold that head; and the build's drift (`drift`).
    With `step` a report step of the user's own, the same against that step's newest
    verdict: each step is read on its own reports."""
    from . import drift

    verdict = drift.newest_verdict(state, build, step)
    if verdict is None or head is None:
        return None, None
    since = (tuple(git.between(verdict.head, head))
             if verdict.head and git.present([verdict.head]) else None)
    return since, drift.of_build(state, build, status, git, settings=settings, step=step)


def _ci_now(settings, build: builds.Build, *, name: str, root, tip: str | None):
    from . import ci

    found = ci.declared(settings, build.repo)
    if found is None or tip is None:
        return None
    return ci.now_of(found, values(name, build, root=root, tip=tip))


# -- the run -----------------------------------------------------------------------------

@dataclass(frozen=True)
class Ran:
    """A run as `check` recorded it: the round's `name`, the `stage`, the `step` it ran
    as, the `commit` (the tip), its `merge_base` with the target's head, the `argv`,
    the `exit_code` (None when the run was ended at its time limit), the run's
    `duration_s`, the `output` file, the `event` written and the after-append
    callbacks that `failed`; for a run on a lane, the `lane`'s name and the name its
    machine gave (`lane_host`)."""

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
    lane: str | None = None
    lane_host: str | None = None

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
    values: dict | None = None
    ci: object = None
    local_argv: list[str] | None = None
    local_due: bool = False
    memory_mib: int | None = None
    full: bool = False
    given_timeout_s: float | None = None
    own_timeout_s: float | None = None
    into_head: str | None = None
    brought: object = None
    lanes: tuple[str, ...] = ()


@dataclass(frozen=True)
class _OnLane:
    """Where a run on a lane held its place: the `lane`'s name, the far machine's name
    (`host`) and the place of its check lock (`check_lock`); and the `argv` the run's
    record holds, the lane's words filled with this machine's root."""

    lane: str
    host: str
    check_lock: int | None
    argv: tuple[str, ...]

    def fields(self) -> dict:
        return {"lane": self.lane, "lane_host": self.host,
                "lane_check_lock": self.check_lock}


def lock_file(env=None) -> Path:
    """The machine's check lock's first place, STATE/rounds/locks/check.lock; the k-th is
    `check-<k>.lock` beside it (`check_line.slot_path`)."""
    return paths.locks_dir(env) / LOCK_FILE


def lock_setting(settings, key: str) -> int:
    """The machine's `check_lock.<key>` as the settings give it: `at_once` or
    `need_mib`, their defaults where the settings hold no such key."""
    try:
        return int(settings[f"check_lock.{key}"])
    except (KeyError, TypeError):
        return 1 if key == "at_once" else 0


def lock_fields(taken: check_line.Taken) -> dict:
    """What a run's record says of the check lock's place it held: `check_lock`, and for
    a run started beside others the `available_mib` and `need_mib` read when it took
    it."""
    found: dict = {"check_lock": taken.k}
    if taken.beside:
        found.update(available_mib=taken.decision.available_mib,
                     need_mib=taken.decision.need_mib)
    return found


def check(name: str, stage: str, *, by: str, step: str | None = None,
          full: bool = False, timeout_s: float | None = None, settings=None,
          wait_s: float = write.WAIT_S, at: str | None = None,
          env=None, wait: bool = True, say=None, urgent: bool = False,
          lane: str | None = None) -> Ran | CiChecked:
    """Run the repo's check for the build of `stage` in the round `name` at its tip,
    and record the run; with `step`, run that command step of the user's own instead,
    and record the run under its name. In a repo that declares `ci`, with neither
    `step` nor `full`, check by CI instead (`CiChecked`): push the tip where the
    setting names a remote, run the local part unless it passed at the tip, and read
    CI. `timeout_s` is the time limit of the run rounds makes, in place of its own.
    `settings` is what `settings.load()` gives, read now when left out. `wait` says
    whether to wait for the machine's check lock while another run holds it; `say`
    gets the lines that say who holds it and how long the wait was.

    A run of the repo's check that may go to a lane (`_lanes_look`), in a project that names
    lanes, chooses where it runs before it takes any place here: here when a place is
    free now; else the first of the project's lanes, in its order, that takes the run
    now (`sending.send`, not waiting), each lane that is busy, can't be reached or isn't
    ready said to `say` and passed over; else here, waiting in line as with no lane, or
    refused with `wait` false. `urgent` keeps it here and asks no lane; `lane` sends it to
    that lane, waiting in its line unless `wait` is false, and refuses it, with nothing
    recorded, when that lane gives no result. A run on a lane holds no place here.

    Raises `BadArgument` for no stage, a time limit that is not above 0, `step` with
    `full`, `urgent` with `lane`, `step` with `lane`, and a `step` whose name no step of
    the user's own can have; what
    `folders.home` raises for a round that can't be found; and `Refused` with every
    condition that does not hold before the run, with a refused push, with what
    changed during the run, with the check lock held and `wait` false, or with a
    failed read of CI when nothing ran, in which case nothing is recorded."""
    if not isinstance(stage, str) or not stage.strip():
        raise BadArgument("no stage is named, so no build is known to check",
                          f"moxi rounds check {name} B<n>, or run moxi rounds check in "
                          "the build's worktree")
    if timeout_s is not None and (isinstance(timeout_s, bool) or timeout_s <= 0):
        raise BadArgument(f"{timeout_s!r} is no time limit",
                          "give --timeout-s a number of seconds above 0")
    _step_name(name, stage, step)
    if full and step is not None:
        raise BadArgument("--full runs the repo's check, and --step a step of your own",
                          "give one of them")
    if urgent and lane is not None:
        raise BadArgument("--urgent keeps the check on this machine, and --lane sends it "
                          "to another", "give one of them")
    if step is not None and lane is not None:
        raise BadArgument("a command step of your own runs here: its words come from the "
                          "round's flow and may read this machine's state",
                          "leave --lane out")
    project, slug = folders.split(name)
    settings = own.load() if settings is None else settings
    home = folders.home(project, settings)
    name = f"{home.project}/{slug}"
    run_as = CHECK if step is None else step
    again = f"moxi rounds check {name} {stage}" + ("" if step is None else f" --step {step}")
    looked: dict[int, _Look] = {}

    def look(view) -> _Look:
        if id(view) not in looked:
            found = _look(view, name=name, stage=stage, step=step,
                          settings=settings, again=again, timeout_s=timeout_s,
                          by_ci=step is None and not full, full=full)
            if step is None:
                _lanes_look(found, settings, lane=lane, urgent=urgent, again=again)
            looked[id(view)] = found
        return looked[id(view)]

    held = _Held(wait=wait, say=say, again=again, env=env, name=name, step=run_as,
                 at_once=lock_setting(settings, "at_once"),
                 need_mib=lock_setting(settings, "need_mib"))

    def act(view):
        found = look(view)
        if found.ci is not None:
            return _by_ci(found, again=again, held=held)
        where = ("the check's command in the settings" if step is None
                 else f"the argv of the step {step} in the round's flow")
        fix = f"fix {where}, then {again}"
        if found.lanes:
            return _choose(found, fix, held=held, keep=keep, settings=settings,
                           lane=lane)
        return _run(found, found.argv, found.timeout_s, fix,
                    held=held, memory_mib=found.memory_mib, keep=keep)

    first: list[_Look] = []
    ran_argv: list = []

    def event(view, result):
        before = first[0]
        moved = _moved(before, name=name, stage=stage, again=again)
        if moved:
            raise Refused(moved)
        if before.ci is not None:
            return _ci_entries(before, result, stage=stage, name=name)
        code, duration_s, output, taken = result
        on_lane = isinstance(taken, _OnLane)
        argv = taken.argv if on_lane else before.argv
        ran_argv.append(argv)
        return write.Entry(RAN, {
            "stage": stage, "step": run_as, "commit": before.tip,
            "merge_base": before.merge_base, "argv": recorded_argv(argv),
            "exit_code": code, "duration_s": duration_s,
            "output": places.contract_home(output),
            **(taken.fields() if on_lane else lock_fields(taken))})

    def gate_and_keep(view):
        found = look(view)
        if not first:
            first.append(found)
        return found.unmet

    # The place a run takes is let go only once its record is appended or refused.
    with ExitStack() as keep:
        written = write.record(
            write.Where(home.project, home.rounds_dir, slug), by=by,
            gates=[gate_and_keep],
            act=act, event=event, long_act=True, wait_s=wait_s, at=at, env=env)
    if first[0].ci is not None:
        return _ci_checked(first[0], written, name=name, stage=stage)
    one = written.events[-1]
    return Ran(name=name, stage=stage, step=run_as, commit=one["commit"],
               merge_base=one["merge_base"], argv=tuple(ran_argv[-1]),
               exit_code=one["exit_code"], duration_s=one["duration_s"],
               output=Path(places.expand_home(one["output"])), event=one,
               failed=written.failed, lane=one.get("lane"), lane_host=one.get("lane_host"))


@dataclass(frozen=True)
class _Held:
    """How a run takes the machine's check lock: whether it `wait`s, the `say` that
    gets the waiting lines, the command that runs the check `again`, the `env` the lock
    file's place is read from, the round (`name`) and `step` its ticket and holder
    record name, how many runs may hold it `at_once` and the free memory a run beside
    others needs (`need_mib`, 0 for the sum), and the free-memory reader (`available`,
    `check_line.available_mib` when None)."""

    wait: bool = True
    say: object = None
    again: str = ""
    env: object = None
    name: str = ""
    step: str = ""
    at_once: int = 1
    need_mib: int = 0
    available: object = None


@contextmanager
def _check_lock(held: _Held, found: _Look, *, memory_mib: int | None = None,
                now: bool = False) -> Iterator[check_line.Taken]:
    """Hold a place of the machine's check lock for the block, taken in the order the
    runs asked for it (`check_line.slot`), or refused at once where `held.wait` is false
    and no place can be taken now. `memory_mib` is what the run declares it holds at
    most. With `now`, it takes a place only when one can be taken now, and raises
    `check_line.NotFree` otherwise, having taken nothing. The block gets the place
    taken, to name its output and its command in the holder record
    (`check_line.update_holder`, `check_line.name_first`)."""
    path = lock_file(held.env)
    build = found.build
    fields = {**check_line.me(), "round": held.name,
              "stage": None if build is None else build.stage, "step": held.step,
              "repo": None if build is None else build.repo}
    if now:
        with check_line.slot(path, fields, memory_mib=memory_mib, at_once=held.at_once,
                             need_mib=held.need_mib, wait=False, say=held.say,
                             available=held.available or check_line.available_mib) as taken:
            yield taken
        return
    try:
        with check_line.slot(path, fields, memory_mib=memory_mib, at_once=held.at_once,
                             need_mib=held.need_mib, wait=held.wait, say=held.say,
                             available=held.available or check_line.available_mib) as taken:
            yield taken
    except check_line.NotFree as exc:
        raise Refused([Unmet(exc.words, f"run it again once those are done: {held.again}, "
                             "or leave --no-wait out to wait in line")]) from None


def _run(found: _Look, argv: Sequence[str], timeout_s: float | None,
         fix: str, *, held: _Held, memory_mib: int | None = None,
         keep: ExitStack | None = None) -> tuple:
    """Run `argv` in the build's worktree, holding a place of the machine's check lock,
    its output to a new file in the scratch folder: its exit code (None when ended at
    `timeout_s`), duration, output file and the place it held (`check_line.Taken`). The
    place is taken (`_check_lock`) and the run made under it (`run_held`), with no time
    between them. With `keep`, the place is held in that stack, let go when the caller
    closes it, not when the run ends."""
    with ExitStack() as own_stack:
        taken = (own_stack if keep is None else keep).enter_context(
            _check_lock(held, found, memory_mib=memory_mib))
        output = _output_file(found.scratch)
        code, duration_s = run_held(taken, argv, cwd=found.worktree, output=output,
                                    timeout_s=timeout_s, fix=fix)
        return code, duration_s, output, taken


def _choose(found: _Look, fix: str, *, held: _Held, keep: ExitStack, settings,
            lane: str | None) -> tuple:
    """Where the run goes, and its run: on `lane` when one is named; else here when a
    place is free now, else the first of `found.lanes` that takes it now, else here,
    waiting in line (refused at once where `held.wait` is false). What `_run` gives,
    with an `_OnLane` in place of the place held for a run on a lane."""
    from . import sending

    say = held.say or (lambda _line: None)
    if lane is not None:
        try:
            return _on_lane(found, lane, settings=settings, wait=held.wait, say=say,
                            name=held.name)
        except sending.LaneError as exc:
            raise Refused([Unmet(exc.reason, exc.fix or held.again)]) from None
    try:
        taken = keep.enter_context(_check_lock(held, found, memory_mib=found.memory_mib,
                                               now=True))
    except check_line.NotFree as exc:
        why = [exc.words]
    else:
        output = _output_file(found.scratch)
        code, duration_s = run_held(taken, found.argv, cwd=found.worktree, output=output,
                                    timeout_s=found.timeout_s, fix=fix)
        return code, duration_s, output, taken
    for one in found.lanes:
        try:
            return _on_lane(found, one, settings=settings, wait=False, say=say,
                            name=held.name)
        except sending.Lost as exc:
            raise Refused([Unmet(exc.reason, exc.fix or held.again)]) from None
        except sending.LaneError as exc:
            say(f"{exc.reason}; so it is not used for this check")
            why.append(exc.reason)
    if not held.wait:
        raise Refused([Unmet("; ".join(why), f"run it again once those are done: "
                             f"{held.again}, or leave --no-wait out to wait in line")])
    return _run(found, found.argv, found.timeout_s, fix, held=held,
                memory_mib=found.memory_mib, keep=keep)


def _on_lane(found: _Look, lane: str, *, settings, wait: bool, say, name: str) -> tuple:
    """Run the check on `lane` (`sending.send`): the lane's words, filled here but for
    `{root}`, which the far side fills; its time limit (the call's, else the project's
    lane table's, else the check's, else `DEFAULT_TIMEOUT_S`); its `memory_mib`
    (`lane_memory_mib`), its preflight and its machine's shared lock. Gives what `_run`
    gives, with an `_OnLane`. Raises what `sending.send` raises."""
    from . import sending

    build = found.build
    used = own.project_lanes(settings, build.repo)[lane]
    machine = own.lane(settings, lane)
    words = (None if found.full else scoped(settings, build.repo, lane)) or declared(
        settings, build.repo, lane)
    far = fill(words, {key: value for key, value in found.values.items() if key != "root"})
    here = fill(words, found.values)
    if not found.full and queued(settings, build.repo):
        far, here = fill_base(far, found.merge_base), fill_base(here, found.merge_base)
    limit = next((one for one in (found.given_timeout_s, used.get("timeout_s"),
                                  found.own_timeout_s) if one is not None),
                 DEFAULT_TIMEOUT_S)
    request = sending.request_of(
        name=name, stage=build.stage, step=CHECK, repo=build.repo, branch=build.branch,
        tip=found.tip, into=build.target, into_head=found.into_head, argv=far,
        timeout_s=limit, memory_mib=lane_memory_mib(settings, build.repo, lane),
        preflight=used.get("preflight") or (), wait=wait,
        shared_lock=machine["shared_lock"])
    output = _output_file(found.scratch)
    ran = sending.send(lane, machine, request, worktree=found.worktree, output=output,
                       brought=found.brought, say=say)
    return (ran.exit_code, ran.duration_s, output,
            _OnLane(lane, ran.host, ran.check_lock, tuple(here)))


def run_held(taken: check_line.Taken, argv: Sequence[str], *, cwd: Path, output: Path,
             timeout_s: float | None, fix: str = "") -> tuple[int | None, float]:
    """Run `argv` in `cwd` under the place `taken` already held, its output to `output`:
    its exit code (None when ended at `timeout_s`) and duration, which counts the run
    only. The place's holder record names the output file, and the command's first
    process once it starts; the command runs with the place's tag, with the caller's
    environment less every `GIT_` variable, and everything it started is ended before
    this returns, however it ends: SIGINT first, then SIGKILL `GRACE_S` later (read at
    the call). Raises `RoundsError` with `fix` when the command can't be started."""
    check_line.update_holder(taken.path, output=str(output))
    started = time.monotonic()
    try:
        done = proc.run(list(argv), timeout_s=timeout_s, env=gitreads.git_env(),
                        cwd=cwd, output=output, first_signal="SIGINT",
                        grace_s=GRACE_S, tag=taken.tag,
                        on_start=functools.partial(check_line.name_first, taken.path),
                        end_leftovers=True)
        code: int | None = done.returncode
    except subprocess.TimeoutExpired:
        code = None
    except OSError as exc:
        raise RoundsError(f"the command {shlex.join(argv)} could not be started in "
                          f"{cwd}: {exc}", fix) from None
    return code, round(time.monotonic() - started, 3)


# -- by CI -------------------------------------------------------------------------------

@dataclass(frozen=True)
class _ByCi:
    """What the act of a check by CI did: the `pushed` remote, the `local` run as
    `_run` gives it (None when none ran), the `reading`, or the read that `failed`, and
    the branch name of CI's own `ref` the tip went to (None for a push to the build's
    own branch name, or none), and whether the remote's branch was `already` at the
    tip, so nothing was pushed."""

    pushed: str | None
    local: tuple | None
    reading: object = None
    failed: object = None
    ref: str | None = None
    already: bool = False


@dataclass(frozen=True)
class CiChecked:
    """A check by CI as `check` recorded it: the round's `name`, the `stage`, the
    `commit` (the tip), the remote it `pushed` to, the `local` run recorded now (None
    when none ran: `local_passed` then says whether one passed at the tip before, or
    the repo has none), the `ci.read` event (`reading`, None when the read failed), the
    `read_failed` line, the after-append callbacks that `failed`, the branch name of
    CI's own `ref` the tip was pushed to (None where the setting names no `ref`), and
    whether that branch was `already` at the tip, so nothing was pushed."""

    name: str
    stage: str
    commit: str
    pushed: str | None
    local: dict | None
    local_passed: bool
    reading: dict | None
    read_failed: str | None
    failed: tuple = ()
    ref: str | None = None
    already: bool = False

    @property
    def passed(self) -> bool:
        """Whether CI reads green at the tip and the local part passed there."""
        from . import ci

        local_ok = (self.local_passed if self.local is None
                    else self.local.get("exit_code") == 0)
        return (local_ok and self.reading is not None
                and self.reading.get("reading") == ci.GREEN)


def _git_out(found: _Look, argv: list[str]) -> str | None:
    """`argv`'s output, run in the worktree under the push time limit, or None when it
    fails or runs past the limit."""
    try:
        done = proc.run(argv, timeout_s=PUSH_TIMEOUT_S, env=gitreads.git_env(),
                        cwd=found.worktree)
    except (subprocess.TimeoutExpired, OSError):
        return None
    if done.returncode != 0:
        return None
    return (done.stdout or b"").decode(errors="replace")


def _at_tip(found: _Look, remote: str, name: str) -> bool:
    """Whether the remote's branch `name` is already at the tip at every URL `git push`
    sends to: each push URL `git remote get-url --push --all` lists is read with
    `git ls-remote`, under the push time limit. A read that fails, a remote with no
    URL listed, or a URL that lists no branch of exactly that name reads as not at the
    tip, so the push goes ahead and says what is wrong."""
    said = _git_out(found, ["git", "remote", "get-url", "--push", "--all", remote])
    urls = [] if said is None else [url for url in said.splitlines() if url.strip()]
    return bool(urls) and all(_url_at_tip(found, url, name) for url in urls)


def _url_at_tip(found: _Look, url: str, name: str) -> bool:
    said = _git_out(found, ["git", "ls-remote", "--heads", url, f"refs/heads/{name}"])
    for line in (said or "").splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1] == f"refs/heads/{name}":
            return parts[0] == found.tip
    return False


def _push(found: _Look, *, again: str) -> tuple[str | None, bool]:
    """Push the tip to the remote the setting names: where it names a `ref`, forced to
    the branch name the template gives the build's branch; else as a plain push to the
    build's own branch name. Returns that `ref` name (None for the build's own branch
    name), and whether the remote's branch was already at the tip at each of its push
    URLs: then nothing is pushed, since git would run the pre-push hook with no ref to
    push, which a guard may refuse. The name `full_ref` gives is never pushed here.
    The push runs through the repo's hooks. Raises `Refused` with git's message, and
    the hooks', when the push is refused."""
    from . import ci

    remote, branch, template = found.ci.remote, found.build.branch, found.ci.ref
    ref = None if template is None else ci.ref_for(template, branch)
    if _at_tip(found, remote, branch if ref is None else ref):
        return ref, True
    argv = (["git", "push", remote, f"+{found.tip}:refs/heads/{ref}"] if ref is not None
            else ["git", "push", remote, f"{found.tip}:refs/heads/{branch}"])
    try:
        done = proc.run(argv, timeout_s=PUSH_TIMEOUT_S, env=gitreads.git_env(),
                        cwd=found.worktree)
    except subprocess.TimeoutExpired:
        raise Refused([Unmet(f"{shlex.join(argv)} ran past {PUSH_TIMEOUT_S:g} s, so CI "
                             "was not read and nothing is recorded", again)]) from None
    if done.returncode == 0:
        return ref, False
    said = (done.stderr or b"").decode(errors="replace").strip()
    said = f"{shlex.join(argv)} was refused, so nothing is recorded: " + (
        said or f"exit {done.returncode}")
    if ref is None:
        # git's words are shown, not read: the way on names both cases.
        raise Refused([Unmet(
            said, f"fix what git says, then {again}; where the branch was rebased and the "
            "push is not a fast-forward, push it yourself with --force-with-lease first")])
    raise Refused([Unmet(said, (
        f"where a guard's hit is in a commit of {branch}'s own, fix it there (reword or "
        f"rewrite your own commits), then {again}; where it is in a commit you did not "
        "make, or looks false, stop and give this text to your lead. Never push with "
        "--no-verify, set a push word, or push to another remote or ref"))])


def _by_ci(found: _Look, *, again: str, held: _Held) -> _ByCi:
    from . import ci

    pushed = ref = None
    already = False
    if found.ci.remote:
        ref, already = _push(found, again=again)
        pushed = found.ci.remote
    local = None
    if found.local_due:
        local = _run(found, found.local_argv, found.timeout_s,
                     f"fix ci.local in the settings, then {again}", held=held,
                     memory_mib=found.ci.local_memory_mib)
        if local[0] != 0:
            # A failed local run stops the check there: CI is not read.
            return _ByCi(pushed, local, ref=ref, already=already)
    try:
        reading = ci.read(found.ci, values=found.values, cwd=found.worktree)
    except ci.ReadFailed as exc:
        return _ByCi(pushed, local, failed=exc, ref=ref, already=already)
    return _ByCi(pushed, local, reading=reading, ref=ref, already=already)


def _read_failed(name: str, stage: str, error) -> Unmet:
    return Unmet(f"CI could not be read: {shlex.join(error.command)} {error.why}",
                 "fix the command (install gh, or gh auth login), or run the full "
                 f"check, which meets the gate without CI: moxi rounds check {name} "
                 f"{stage} --full")


def _ci_entries(before: _Look, result: _ByCi, *, stage: str, name: str) -> list:
    from . import ci

    entries = []
    # Each record of the check names CI's own ref it pushed, so the build's end knows
    # what to remove whatever the setting says by then.
    pushed = ({} if result.ref is None
              else {"pushed": {"remote": result.pushed, "ref": result.ref}})
    if result.local is not None:
        code, duration_s, output, taken = result.local
        entries.append(write.Entry(RAN, {
            "stage": stage, "step": ci.LOCAL, "commit": before.tip,
            "merge_base": before.merge_base, "argv": recorded_argv(before.local_argv),
            "exit_code": code, "duration_s": duration_s,
            "output": places.contract_home(output), **lock_fields(taken),
            **pushed}))
    if result.reading is not None:
        found = result.reading
        jobs = {} if not found.jobs_argvs else {
            "jobs_argvs": [recorded_argv(one) for one in found.jobs_argvs]}
        entries.append(write.Entry(ci.READ, {
            "stage": stage, "commit": before.tip, "merge_base": before.merge_base,
            "status_argv": recorded_argv(found.status_argv),
            "ids_argv": recorded_argv(found.ids_argv),
            **jobs, "workflows": [dict(one) for one in found.workflows],
            "reading": found.whole, **pushed}))
    if not entries and result.failed is not None:
        raise Refused([_read_failed(name, stage, result.failed)])
    return entries


def _ci_checked(before: _Look, written, *, name: str, stage: str) -> CiChecked:
    from . import ci

    local = next((one for one in written.events if one["type"] == RAN), None)
    reading = next((one for one in written.events if one["type"] == ci.READ), None)
    result = written.result
    failed = (None if result.failed is None
              else _read_failed(name, stage, result.failed).reason)
    return CiChecked(name=name, stage=stage, commit=before.tip, pushed=result.pushed,
                     local=local, local_passed=not before.local_due, reading=reading,
                     read_failed=failed, failed=written.failed, ref=result.ref,
                     already=result.already)


def _step_name(name: str, stage: str, step: str | None) -> None:
    """Refuse a `step` that names no step of the user's own: a name that is not text, a
    name with a dot, and the repo's check, which is run with no step named."""
    plain = f"moxi rounds check {name} {stage}"
    if step is None:
        return
    if not isinstance(step, str) or not step.strip():
        raise BadArgument("--step names no step",
                          "name a command step of the round's flow, or leave --step out")
    if "." in step:
        raise BadArgument(
            f"{step} has a dot, and no step of your own has one: such a name is rounds' "
            "own record of a run", f"name a command step of the round's flow: {plain} "
            "--step NAME")
    if step == CHECK:
        raise BadArgument(f"{CHECK} is the repo's declared check, and is run with no "
                          "--step", plain)


def _moved(before: _Look, *, name: str, stage: str, again: str) -> list[Unmet]:
    """What changed in the worktree while the run ran, read again now: the tip, or
    anything uncommitted. Empty when neither did."""
    try:
        now = read_tip(before.worktree, before.build.branch)
    except GitError as exc:
        return [Unmet(f"the worktree {before.worktree} can't be read after the run, so "
                      f"nothing is recorded: {exc.reason}", again)]
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


def _command(state: states.State, build: builds.Build, step: str | None, *, name: str,
             settings, full: bool = False) -> tuple[list[str] | None, float | None,
                                                    Unmet | None]:
    """The words of the command a run of `step` (the repo's check when None) runs,
    placeholders as written, and its own time limit; or why there is none. With `full`,
    the repo's check is its `check.command` in a queue project too, not its scoped
    one."""
    if step is None:
        words = (None if full else scoped(settings, build.repo)) or declared(
            settings, build.repo)
        if words is None:
            return None, None, Unmet(
                f"{build.repo} declares no check, so a build in it has no check step",
                f'set check = {{ command = "...", timeout_s = N }} in '
                f"[projects.{build.repo}] of {paths.LAYOUT.config_file()}")
        return words, own.build_setting(settings, build.repo, "check").get("timeout_s"), None
    entry = step_entry(state, step)
    if entry is None:
        commands = [str(one.get("step")) for one in state.steps if flows.is_user_step(one)
                    and one.get("does") == flows.COMMAND]
        listed = ", ".join(commands) if commands else "none"
        return None, None, Unmet(
            f"the flow {state.flow} of {name} has no step {step} that runs a command (its "
            f"command steps: {listed})", f"moxi rounds show {name}")
    # The step's words are read from the round's record, so `~` and a `~/`-led one name
    # the home folder and a path under it.
    return ([places.expand_home(word) for word in entry["argv"]], entry.get("timeout_s"),
            None)


def _look(view, *, name: str, stage: str, step: str | None, settings, again: str,
          timeout_s: float | None, by_ci: bool, full: bool = False) -> _Look:
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
    words, own_timeout_s, missing = _command(state, build, step, name=name,
                                             settings=settings, full=full)
    if missing is not None:
        found.unmet.append(missing)
        return found
    commissioned = commission_of(state, build)
    worktree = Path(places.expand_home(commissioned.get("worktree") or ""))
    scratch = Path(places.expand_home(commissioned.get("scratch") or ""))
    host = commissioned.get("host")
    if not places.made_here(commissioned, unrecorded_is_here=True):
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
    except RoundsError as exc:
        found.unmet.append(Unmet(exc.reason, exc.fix or "moxi projects list"))
        return found
    try:
        found.unmet += _read_git(found, Git(root), worktree, build, commissioned,
                                 name=name, stage=stage,
                                 settings=settings)
    except GitError as exc:
        found.unmet.append(Unmet(exc.reason, exc.fix or again))
        return found
    if found.tip is not None:
        found.values = values(name, build, root=root, tip=found.tip)
        found.argv = fill(words, found.values)
    found.timeout_s = _limit(timeout_s, own_timeout_s)
    found.given_timeout_s, found.own_timeout_s, found.full = timeout_s, own_timeout_s, full
    found.brought = commissioned.get("brought")
    if step is None:
        found.memory_mib = own.memory_mib(own.build_setting(settings, build.repo, "check"))
    if step is None and queued(settings, build.repo):
        _queue_look(found, full=full, name=name, stage=stage)
        return found
    if by_ci:
        _by_ci_look(found, state, settings, timeout_s=timeout_s, name=name)
    return found


def _queue_look(found: _Look, *, full: bool, name: str, stage: str) -> None:
    """A queue project's check: `{base}` filled in with the merge-base read at the
    tip; refused where the merge-base is not one commit. With `full` the run is the
    repo's `check.command`, which has no `{base}`: nothing is filled or refused."""
    build = found.build
    if full or found.argv is None:
        return
    if found.merge_base is None:
        found.unmet.append(Unmet(
            f"the merge-base of {stage}'s tip and {build.target} is not one commit, and "
            "the scoped check runs against it",
            f"merge {build.target} into the branch, then moxi rounds check {name} {stage}"))
        return
    found.argv = fill_base(found.argv, found.merge_base)


def _limit(given_s: float | None, own_s: float | None) -> float:
    """A run's time limit: the one the call gives, else the command's own, else
    `DEFAULT_TIMEOUT_S`."""
    for one in (given_s, own_s):
        if one is not None:
            return one
    return DEFAULT_TIMEOUT_S


def _by_ci_look(found: _Look, state: states.State, settings, *,
                timeout_s: float | None, name: str) -> None:
    """Fill in what a check by CI needs, where the build's repo declares `ci`: the
    setting, the local argv, and whether the local part is to run at the tip. A build
    on its repo's target is refused where the setting names a `ref`."""
    from . import ci

    declared_ci = ci.declared(settings, found.build.repo)
    if declared_ci is None:
        return
    found.ci = declared_ci
    build = found.build
    targets = {build.target, own.build_setting(settings, build.repo, "into")}
    if declared_ci.ref is not None and declared_ci.remote and build.branch in targets:
        # The ref the template gives the target is the lead's, pushed by hand before
        # the target goes public, so no build pushes it.
        found.unmet.append(Unmet(
            f"{build.branch} is the target of {build.repo}, and "
            f"{ci.ref_for(declared_ci.ref, build.branch)} is the lead's to push, so a "
            "check by CI pushes nothing for it",
            f"check it by the full check: moxi rounds check {name} {build.stage} --full"))
    found.timeout_s = _limit(timeout_s, declared_ci.local_timeout_s)
    if declared_ci.local is None or found.values is None:
        return
    found.local_argv = fill(declared_ci.local, found.values)
    found.local_due = not passes(state, found.build.stage, ci.LOCAL, tip=found.tip,
                                 argv=found.local_argv)


def _read_git(found: _Look, git: Git, worktree: Path, build: builds.Build,
              commissioned: Mapping, *, name: str, stage: str, settings) -> list[Unmet]:
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
    held = _private_held(settings, build, commissioned, git, tip)
    if held:
        from . import private_files

        reason, fix = private_files.words(held, repo=build.repo, target=build.target,
                                          round_name=name)
        unmet.append(Unmet(reason, fix))
    bases = () if target_head is None else git.merge_bases(tip, target_head)
    found.tip = tip
    found.into_head = target_head
    found.merge_base = bases[0] if len(bases) == 1 else None
    return unmet


def _lanes_look(found: _Look, settings, *, lane: str | None, urgent: bool,
                again: str) -> None:
    """The lanes a run of the repo's check asks (`found.lanes`), in the order the
    project's table names them: the one `lane` names, or with none named every lane the
    project names, unless `urgent`. Only a run that may go asks any: one by CI runs
    here, and with `lane` named is refused, as is a lane the project names no table
    for."""
    build = found.build
    if build is None or found.unmet or found.argv is None:
        return
    if found.ci is not None:
        if lane is not None:
            found.unmet.append(Unmet(
                "a check by CI runs here: its local part reads this machine's CI state; "
                "--full can go to a lane", f"{again} --full --lane {lane}"))
        return
    named = list(own.project_lanes(settings, build.repo))
    if lane is None:
        found.lanes = () if urgent else tuple(
            one for one in named if own.lane(settings, one) is not None)
        return
    if lane not in named or own.lane(settings, lane) is None:
        listed = ", ".join(named) if named else "none"
        found.unmet.append(Unmet(
            f"{build.repo} sends no check to a lane {lane}: its lanes are {listed}",
            f"name one of them, or add [lanes.{lane}] and [projects.{build.repo}.lanes."
            f"{lane}] to {paths.LAYOUT.config_file()}"))
        return
    found.lanes = (lane,)


__all__ = ["CHECK", "RAN", "PLACEHOLDERS", "WITH_WORK", "OUTPUT", "DEFAULT_TIMEOUT_S",
           "GRACE_S", "LOCK_FILE", "lock_file", "lock_setting", "lock_fields", "fill", "declared",
           "values", "queued", "scoped", "lane_words", "lane_memory_mib", "fill_base",
           "argv_now", "run_lane", "runs", "newest", "why_not", "passes", "recorded_argv",
           "step_entry", "report_steps", "commission_of", "run_held",
           "Tip", "read_tip", "seen_of", "Ran", "CiChecked", "PUSH_TIMEOUT_S", "check"]

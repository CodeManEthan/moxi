"""`commission`: the round's act of authorizing one build of a stage the plan holds.

`commission(name, stage, builder=...)` runs the write path. Under the round's lock it
reads the round, its project's log and git, and refuses, having made nothing, while any
of these does not hold:

- the round has not ended, and its flow has a `build` step;
- the stage is one of the plan's, not one that left it, and stands for a build rather
  than a child round;
- the stage's `commission` step waits for nothing (`steps`), and has no build that is
  open or finished; where the flow has a design step, `design.md` holds the bytes its
  last version was frozen from;
- the target can be read: the repo's `into` setting, else the branch checked out at
  the project's root, which must not be a detached head or an open build's branch;
  the target branch is in the repo, and its head is the build's **base**;
- the branch is a name git takes; it is not there yet, or, with `existing`, it is
  there and shares no commit with another open build of the repo and target;
- nothing is at the worktree's place but the worktree of this commission's own stopped
  run (below).

Then it makes, in this order: the **build id** (16 random hex digits); the branch, cut
from the base, and its worktree, in one git call, at `<worktree_dir>/<repo>/<branch>`;
the worktree's marker, in the worktree's own git folder; the scratch folder,
`<cache dir>/scratch/<repo>/<branch>/<build id>`, with a create that fails on a folder
already there, and its marker first in it. Then it appends `build.commissioned` with
both folders, the build id and this machine's host name. A branch is percent-encoded in
both paths (`places.encode`).

**A rerun after a stop.** A run stopped before its append left folders and no event. A
rerun adopts the worktree at the place only when its marker names this round, stage,
repo and branch and a build id no `build.commissioned` of the round records: its own
stopped run. It keeps that id, and makes the scratch folder under it when it is
missing. Any other folder at either place is refused, naming it: one with no marker (a
stop between its create and its marker), and a worktree whose marker names a recorded
build. rounds never adopts or removes a folder it can't tell it made; the user looks at
it, removes it, and runs `commission` again.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from moxi import proc

from . import (builds, ending, folders, gitreads, index, open_builds, paths, places,
               pointers, skips, stages, stepping, steps, versions, write)
from . import settings as own
from .errors import BadArgument, RoundsError, Unmet
from .gitreads import Git, GitError

#: The step a flow has when it builds anything.
BUILD = "build"


@dataclass(frozen=True)
class Commissioned:
    """A build as `commission` made it: the round's `name`, the `stage`, its `repo`
    (the key as the projects list stores it), `branch`, `base` and `target`, its
    `worktree` and `scratch` folders, its `build` id, the `host` that made them, its
    `builder` and the go pointer `record`. `adopted` is true when the worktree was
    left by a stopped run of this commission. `event` is the event written; `failed`
    pairs each after-append callback that raised with what it raised."""

    name: str
    stage: str
    repo: str
    branch: str
    base: str
    target: str
    worktree: Path
    scratch: Path
    build: str
    host: str
    builder: str
    record: str | None
    adopted: bool
    event: dict
    failed: tuple[tuple[Callable, Exception], ...] = ()


@dataclass
class _Look:
    """What one reading of the round, under the lock, found: every unmet condition,
    and for a commission that may go ahead what it is made from."""

    unmet: list[Unmet] = field(default_factory=list)
    root: Path | None = None
    repo: str | None = None
    branch: str | None = None
    target: str | None = None
    base: str | None = None
    worktree: Path | None = None
    adopt: str | None = None
    #: Whether a folder at either place was refused.
    unmet_place: bool = False


def commission(name: str, stage_id: str, *, builder: str, actor: str,
               record: str | None = None, existing: bool = False, settings=None,
               wait_s: float = write.WAIT_S, at: str | None = None,
               env=None) -> Commissioned:
    """Commission the stage `stage_id` of the round `name`, `<project>/<slug>`.

    - `builder`: who builds it; `actor`: who commissions it, the event's actor.
    - `record`: the pointer to the operator's go, when there was one.
    - `existing`: build on a branch that is already there, in place of cutting one.
    - `settings`: rounds' settings as `settings.load()` gives them, read now when left
      out; `wait_s`: how long to wait for the round's lock; `at`: the event's time.

    Raises `BadArgument` for a builder with no name, `BadName`, `UnknownProject`,
    `NoFolder` or `NoRound` for a round that can't be found, `Refused` with every
    condition that does not hold, and `RoundsError` when git or the file system fails
    to make a folder. A refusal makes nothing and writes nothing."""
    if not isinstance(builder, str) or not builder.strip():
        raise BadArgument("the build has no builder", "name who builds it: --builder NAME")
    project, slug = folders.split(name)
    settings = own.load() if settings is None else settings
    home = folders.home(project, settings)
    name = f"{home.project}/{slug}"
    folder = home.round_folder(slug)
    again = f"moxi rounds commission {name} {stage_id} --builder {builder}"
    if existing:
        again += " --existing"
    looked: dict[int, _Look] = {}

    def look(view) -> _Look:
        # One look for one reading of the log: each gate and the act speak of it.
        if id(view) not in looked:
            looked[id(view)] = _look(view, name=name, folder=folder, stage_id=stage_id,
                                     existing=existing, settings=settings, again=again,
                                     env=env)
        return looked[id(view)]

    def ready(view):
        return look(view).unmet

    def has_a_pointer(view):
        if record is None:
            return None
        return pointers.unmet(record, f"{again} --record PTR",
                              names="where the operator gave the go")

    def make(view) -> tuple[str, Path, bool]:
        found = look(view)
        build_id = found.adopt or places.new_build_id()
        names = {"round": name, "stage": stage_id, "repo": found.repo,
                 "branch": found.branch}
        if found.adopt is None:
            _add_worktree(found, existing=existing)
            git_dir = places.worktree_git_dir(found.worktree)
            if git_dir is None:
                raise RoundsError(f"git made no worktree at {found.worktree}",
                                  f"look at {found.worktree}, remove it, then: {again}")
            _mark(git_dir / places.WORKTREE_MARKER, places.marker(build_id, **names),
                  again)
        scratch = places.scratch_place(found.repo, found.branch, build_id, env)
        if not (found.adopt is not None and _is_own_scratch(scratch, build_id, names)):
            _make_scratch(scratch, places.marker(build_id, **names), again)
        return build_id, scratch, found.adopt is not None

    def event(view, result) -> write.Entry:
        found = look(view)
        build_id, scratch, _adopted = result
        fields = {"stage": stage_id, "repo": found.repo, "branch": found.branch,
                  "base": found.base, "target": found.target,
                  "worktree": str(found.worktree), "scratch": str(scratch),
                  "builder": builder, "host": places.host(), "build": build_id}
        if record is not None:
            fields["record"] = record
        return write.Entry("build.commissioned", fields)

    written = write.record(
        write.Where(home.project, home.folder, slug), actor=actor,
        gates=[ending.takes_new_work(name, "a new build"), ready, has_a_pointer],
        act=make, event=event, wait_s=wait_s, at=at)
    one = written.events[0]
    return Commissioned(
        name=name, stage=stage_id, repo=one["repo"], branch=one["branch"],
        base=one["base"], target=one["target"], worktree=Path(one["worktree"]),
        scratch=Path(one["scratch"]), build=one["build"], host=one["host"],
        builder=builder, record=record, adopted=written.result[2], event=one,
        failed=written.failed)


# -- what is read under the lock ---------------------------------------------------------

def _look(view, *, name: str, folder: Path, stage_id: str, existing: bool, settings,
          again: str, env) -> _Look:
    found = _Look()
    state = view.state
    stage = stages.find(state, stage_id)
    show = f"moxi rounds show {name}"
    if stage is None:
        found.unmet.append(Unmet(f"{stage_id} is not a stage of {name}", show))
        return found
    if stages.state_of(state, stage_id) == stages.LEFT:
        found.unmet.append(Unmet(
            f"{stage_id} left the plan of {name}, and a stage that left does not come "
            "back: the same work planned again takes a new id", show))
        return found
    if stage.round is not None:
        found.unmet.append(Unmet(
            f"{stage_id} stands for the round {stage.round}, which is run as a round "
            "of its own and has no build", f"moxi rounds next {stage.round}"))
        return found
    if not stepping.has_step(state, BUILD):
        flow = stepping.current(state).name
        found.unmet.append(Unmet(
            f"the flow {flow} of {name} has no {BUILD} step, so nothing in it is "
            "built; a flow that has one is the lead's to move it to",
            f"moxi rounds mark {name} --flow NAME"))
        return found
    step = steps.one(state, steps.COMMISSION, stage_id, name=name,
                     register=view.project_state)
    if step is not None and step.state == steps.DONE:
        found.unmet.append(Unmet(
            f"{stage_id} has a build that is open or finished, and a stage is built by "
            "one build at a time", show))
        return found
    if step is not None:
        found.unmet += step.waits
    if (stepping.has_step(state, steps.DESIGN)
            and not skips.is_skipped(state, steps.DESIGN)
            and versions.current(state) is not None):
        moved = versions.unchanged(state, folder, name)
        if moved is not None:
            found.unmet.append(moved)
    found.unmet += _git(found, view, name=name, stage=stage, existing=existing,
                        settings=settings, again=again, env=env)
    return found


def _git(found: _Look, view, *, name: str, stage, existing: bool, settings, again: str,
         env) -> list[Unmet]:
    """What git and the folders say of the build: its target and base, its branch, and
    what is at the worktree's place. Fills `found` as it goes."""
    try:
        repo, root = folders.root(stage.repo)
    except RoundsError as error:
        return [Unmet(error.reason, error.fix or "moxi projects list")]
    found.repo, found.root, found.branch = repo, root, stage.branch
    git = Git(root)
    try:
        return _read_git(found, git, view, name=name, stage=stage, existing=existing,
                         settings=settings, again=again, env=env)
    except GitError as error:
        return [Unmet(error.reason, error.fix or again)]


def _read_git(found: _Look, git: Git, view, *, name: str, stage, existing: bool,
              settings, again: str, env) -> list[Unmet]:
    unmet: list[Unmet] = []
    repo, branch = found.repo, found.branch
    config = paths.LAYOUT.config_file(env)
    set_into = f"set into in [projects.{repo}] of {config} to the branch builds land on"
    if not git.is_branch_name(branch):
        return [Unmet(f"{branch!r}, the branch of {stage.id}, is not a name git takes "
                      "for a branch; different work is a new stage",
                      f"moxi rounds show {name}")]
    open_now: open_builds.OpenBuilds | None = None

    def opened() -> open_builds.OpenBuilds:
        nonlocal open_now
        if open_now is None:
            open_now = open_builds.read(repo, settings=settings)
        return open_now

    target = own.build_setting(settings, repo, "into")
    if target is None:
        checked = git.checked_out()
        if checked is None:
            return [Unmet(f"{repo} sets no into, and its root {found.root} has a "
                          "detached head, so nothing says which branch builds land on",
                          set_into)]
        holder = next((one for one in opened().builds
                       if one.build.branch == checked), None)
        if holder is not None:
            return [Unmet(f"{repo} sets no into, and its root has {checked} checked "
                          f"out, the branch of the open build {holder.build.stage} of "
                          f"{holder.round}, which is no target", set_into)]
        target = checked
    base = git.branch(target)
    if base is None:
        return [Unmet(f"the target branch {target} of {repo} is not in the repo",
                      set_into)]
    found.target, found.base = target, base
    found.worktree = places.worktree_place(settings, repo, branch, env)
    unmet += _at_the_place(found, view, name=name, stage=stage, again=again, env=env)
    tip = git.branch(branch)
    if found.adopt is None and not found.unmet_place:
        if tip is not None and not existing:
            unmet.append(Unmet(
                f"the branch {branch} is in {repo} already; a build cuts its own branch "
                "from the target, or takes one that is there with --existing",
                f"{again} --existing"))
        if tip is None and existing:
            unmet.append(Unmet(f"--existing names the branch {branch}, which is not in "
                               f"{repo}", again.replace(" --existing", "")))
    if existing and tip is not None:
        unmet += _shares(found, git, name=name, stage=stage, opened=opened,
                         settings=settings)
    return unmet


def _at_the_place(found: _Look, view, *, name: str, stage, again: str,
                  env) -> list[Unmet]:
    """What is at the worktree's place: nothing; the worktree of this commission's own
    stopped run, which is adopted (`found.adopt`); or a folder that is refused."""
    place = found.worktree
    if not os.path.lexists(place):
        return []
    fix = f"look at {place}, remove it if nothing in it is wanted, then: {again}"
    marker = places.worktree_marker(place)
    recorded = {event.get("build") for event in view.state.history
                if event["type"] == "build.commissioned"}
    if marker is None:
        found.unmet_place = True
        return [Unmet(f"{place} is there and holds no marker of a build: rounds never "
                      "takes or removes a folder it can't tell it made", fix)]
    if (marker.names(round=name, stage=stage.id, repo=found.repo, branch=found.branch)
            and marker.build not in recorded):
        found.adopt = marker.build
        scratch = places.scratch_place(found.repo, found.branch, marker.build, env)
        names = {"round": name, "stage": stage.id, "repo": found.repo,
                 "branch": found.branch}
        if os.path.lexists(scratch) and not _is_own_scratch(scratch, marker.build, names):
            found.unmet_place = True
            return [Unmet(f"{scratch} is there and holds no marker of this build: "
                          "rounds never takes or removes a folder it can't tell it "
                          "made", f"look at {scratch}, remove it if nothing in it is "
                                  f"wanted, then: {again}")]
        return []
    found.unmet_place = True
    if marker.build in recorded:
        return [Unmet(f"{place} holds the worktree of the build {marker.build} of "
                      f"{marker.round} {marker.stage}, which is on record; it was kept "
                      "when that build ended, and the place is used again only once "
                      "it is gone", fix)]
    return [Unmet(f"{place} holds a worktree marked for the build {marker.build} of "
                  f"{marker.round} {marker.stage}, which is not this commission's own "
                  "stopped run", fix)]


def _shares(found: _Look, git: Git, *, name: str, stage, opened,
            settings) -> list[Unmet]:
    """The unmet condition of a branch taken with `--existing` that shares commits with
    another open build of the repo and target, read as a build cut at the base now."""
    candidate = builds.Build(stage=stage.id, repo=found.repo, branch=found.branch,
                             base=found.base, target=found.target, n=0)
    homes, _unresolved = folders.homes(settings)
    landed = [one.landing for one in index.landings(homes, repo=found.repo,
                                                    target=found.target)]
    status = builds.read(candidate, git, landings=landed)
    if status.state not in builds.RANGED:
        return []
    statuses = {(one.round, one.build.stage): one.status for one in opened().builds
                if one.status is not None and one.build.target == found.target}
    key = (name, stage.id)
    statuses[key] = status
    shared = builds.sharing(git, statuses)[key]
    if not shared:
        return []
    said = ", ".join(f"{other} {other_stage}" for other, other_stage in shared)
    return [Unmet(f"the branch {found.branch} shares commits with the open build of "
                  f"{said}; a build's branch holds no other build's work",
                  f"cut the work onto a branch of its own, or end that build first: "
                  f"moxi rounds show {shared[0][0]}")]


# -- the act ------------------------------------------------------------------------------

def _add_worktree(found: _Look, *, existing: bool) -> None:
    """Cut the branch from the base and add its worktree, in one git call; with
    `existing`, add the worktree on the branch that is there."""
    found.worktree.parent.mkdir(parents=True, exist_ok=True)
    tail = [found.branch] if existing else ["-b", found.branch, found.base]
    argv = ["git", "-C", str(found.root), "worktree", "add", "--quiet",
            str(found.worktree), *tail]
    try:
        done = proc.run(argv, timeout_s=gitreads.GIT_TIMEOUT_S,
                        env=gitreads.environment(), stdin_data="")
    except (OSError, subprocess.TimeoutExpired) as error:
        raise GitError(argv, f"could not run: {error}") from None
    if done.returncode != 0:
        said = " ".join((done.stderr or "").split())[:200]
        raise GitError(argv, f"exited {done.returncode}" + (f": {said}" if said else ""))


def _mark(path: Path, marker: places.Marker, again: str) -> None:
    try:
        places.write_marker(path, marker)
    except OSError as error:
        raise RoundsError(f"the marker {path} can't be written: {error}",
                          f"look at the folder, remove it, then: {again}") from None


def _make_scratch(place: Path, marker: places.Marker, again: str) -> None:
    """Make the scratch folder `place` with a create that fails on one already there,
    and write its marker in it before anything else."""
    try:
        place.parent.mkdir(parents=True, exist_ok=True)
        os.mkdir(place)
    except FileExistsError:
        raise RoundsError(f"{place} is there already, and a build's scratch folder is "
                          "made new", f"look at {place}, remove it, then: {again}") from None
    except OSError as error:
        raise RoundsError(f"the scratch folder {place} can't be made: {error}",
                          f"make {place.parent} one that can be written to, then: "
                          f"{again}") from None
    _mark(place / places.SCRATCH_MARKER, marker, again)


def _is_own_scratch(place: Path, build_id: str, names: dict) -> bool:
    """Whether `place` is a scratch folder whose marker names this build."""
    marker = places.read_marker(place / places.SCRATCH_MARKER)
    return (not place.is_symlink() and place.is_dir() and marker is not None
            and marker.build == build_id and marker.names(**names))


__all__ = ["BUILD", "Commissioned", "commission"]

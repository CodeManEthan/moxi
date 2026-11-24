"""`commission`: the round's act of authorizing one build of a stage.

`commission(name, stage, builder=...)` builds a stage the plan holds.
`commission(name, repo=..., branch=..., builder=...)`, in a flow that does not verify
its design, declares the stage as it commissions it: under the lowest `B<n>` the round
has not used, with no `after`, and with the slices given. Either runs the write path.
Under the round's lock it reads the round, its project's log and git, and refuses,
having made nothing, while any of these does not hold:

- the round has not ended, and its flow has a `build` step;
- the stage is one of the plan's, not one that left it, and stands for a build rather
  than a child round; a stage declared here is in a flow that does not verify its
  design, and the repo has no stage of the plan without an open or finished build,
  which is commissioned by its id instead;
- the stage's `commission` step waits for nothing (`steps`), read for a stage declared
  here as if it were declared, and has no build that is open or finished; where the
  flow has a design step, `design.md` holds the bytes its last version was frozen from;
  a question whose `blocks` is every stage holds a stage the moment it is declared, so
  while one is open nothing is declared, and the refusal names the operator's ways
  past it;
- no open build of any round is on the repo's branch already;
- the target can be read: the repo's `into` setting, else the branch checked out at
  the project's root, which must not be a detached head or an open build's branch;
  the target branch is in the repo, and its head is the build's **base**;
- the branch is a name git takes; it is not there yet, or, with `existing`, it is
  there, and, read as a build cut at the base now, it overlaps no other open build of
  the repo and target (a range strictly inside or around another build's is taken, and
  the finish gate orders the two; equal ranges overlap) and it did not come onto the
  target by another build's recorded landing;
- nothing is at the worktree's place but the worktree of this commission's own stopped
  run (below);
- each entry of the repo's `private_files` is a file at the project's root, is not held
  by the tree of the commit the worktree checks out (the base, else the branch's tip),
  and is read as ignored by git at the root (`private_files.unmet`).

Then it makes, in this order: the **build id** (16 random hex digits); the branch, cut
from the base, and its worktree, in one git call, at `<worktree_dir>/<repo>/<branch>`;
the worktree's marker, in the worktree's own git folder; a copy of each private file in
the worktree, once git there reads every one as ignored (`private_files.bring`); the
scratch folder, `<cache dir>/scratch/<repo>/<branch>/<build id>`, with a create that
fails on a folder already there, and its marker first in it. Then it appends
`build.commissioned` with both folders, each under the home folder written `~/<rest>`
(`places.contract_home`), the build id, this machine's host name and each private file
copied (`brought`), after the `stage.declared` of a stage declared here. A branch is
percent-encoded in both paths (`places.encode`).

**A rerun after a stop.** A run stopped before its append left folders and no event. A
rerun adopts the worktree at the place only when its marker names this round, stage,
repo and branch and a build id no `build.commissioned` of the round records: its own
stopped run. It keeps that id, and makes the scratch folder under it when it is missing,
and copies each private file not copied yet, keeping one already there only when it is a
regular file with the source's bytes. Its base is the merge base of the branch's tip and
the target, the commit the stopped run cut the branch from: a build's base never moves,
even when the target did between the runs. Any other folder at either place is refused,
naming it: one with no marker (a stop between its create and its marker), and a worktree
whose marker names a recorded build. rounds never adopts or removes a folder it can't
tell it made; the user looks at it, removes it, and runs `commission` again. A stage
declared here is declared in the same append as its build, so a rerun of the same
command declares the same id and adopts its own stopped run's worktree.

**Again after a drop.** A dropped build counts for nothing: its stage is commissioned
again under the same id, with `existing` on the branch the drop kept or on a fresh
branch once that one is deleted. Its worktree, if kept, is refused until it is gone.
"""

from __future__ import annotations

import os
import shlex
import subprocess
from collections.abc import Sequence
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from moxi import proc

from . import (builds, by_branch, commission_block, design_block, ending, folders,
               freezing, gitreads, index, open_builds, paths, places, pointers,
               private_files, sizing, skips, stages, stepping, steps, versions, write)
from . import settings as own
from . import state as states
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
    left by a stopped run of this commission. `event` is the event written; `block`
    is the commission block, read from the logs as they stood under the lock;
    `oversized` lists each slice this commission declared over the project's
    `slice_minutes`, as `{"stage", "slice", "minutes"}`, and `slice_minutes` is that
    limit as read now; `failed` pairs each after-append callback that raised with what
    it raised; `brought` is each private file copied into the worktree, as the event
    records it."""

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
    block: commission_block.Block | None = None
    oversized: tuple[dict, ...] = ()
    slice_minutes: int | None = None
    failed: tuple[tuple[Callable, Exception], ...] = ()
    brought: tuple[dict, ...] = ()


@dataclass
class _Look:
    """What one reading of the round, under the lock, found: every unmet condition,
    and for a commission that may go ahead what it is made from."""

    unmet: list[Unmet] = field(default_factory=list)
    stage: str | None = None
    root: Path | None = None
    repo: str | None = None
    branch: str | None = None
    target: str | None = None
    base: str | None = None
    worktree: Path | None = None
    adopt: str | None = None
    #: Whether a folder at either place was refused.
    unmet_place: bool = False
    #: The stage this commission declares, in the form that declares one.
    declared: states.Stage | None = None
    #: The repo's private files, copied into the worktree.
    private: tuple[str, ...] = ()


@dataclass(frozen=True)
class Declaring:
    """What `commission` with no stage declares: the `repo` and `branch` of the build,
    and its `slices`, each `{"id", "what", "minutes"}`."""

    repo: str
    branch: str
    slices: tuple[dict, ...] = ()


def commission(name: str, stage_id: str | None = None, *, builder: str, by: str,
               repo: str | None = None, branch: str | None = None,
               slices: Sequence[tuple[str, str, int]] = (), record: str | None = None,
               existing: bool = False, settings=None, wait_s: float = write.WAIT_S,
               at: str | None = None, env=None) -> Commissioned:
    """Commission the stage `stage_id` of the round `name`, `<project>/<slug>`; or, with
    no stage, declare one as it is commissioned.

    - `repo` and `branch`: with no stage, the project the build is in, by key, and its
      branch; `slices`: with no stage, each worker's piece of it as `(id, what,
      minutes)`. All three are refused with a stage.
    - `builder`: who builds it; `by`: who commissions it, the event's actor.
    - `record`: the pointer to the operator's go, when there was one.
    - `existing`: build on a branch that is already there, in place of cutting one.
    - `settings`: rounds' settings as `settings.load()` gives them, read now when left
      out; `wait_s`: how long to wait for the round's lock; `at`: the event's time.

    Raises `BadArgument` for a builder with no name, for a stage given with a repo,
    branch or slices, for no stage without both a repo and a branch, and for a slice
    that can't be one, `BadName`, `UnknownProject`,
    `NoFolder` or `NoRound` for a round that can't be found, `Refused` with every
    condition that does not hold, and `RoundsError` when git or the file system fails
    to make a folder. A refusal makes nothing and writes nothing. A slice declared
    here over the project's `slice_minutes` is recorded as given, and named in
    `Commissioned.oversized`, as a freezing names one."""
    if not isinstance(builder, str) or not builder.strip():
        raise BadArgument("the build has no builder", "name who builds it: --builder NAME")
    declaring = _declaring(name, stage_id, repo=repo, branch=branch, slices=slices)
    project, slug = folders.split(name)
    settings = own.load() if settings is None else settings
    home = folders.home(project, settings)
    name = f"{home.project}/{slug}"
    round_dir = home.round_dir(slug)
    if declaring is None:
        again = f"moxi rounds commission {name} {stage_id} --builder {builder}"
    else:
        again = shlex.join([
            "moxi", "rounds", "commission", name, "--repo", declaring.repo, "--branch",
            declaring.branch, "--builder", builder,
            *(word for one in declaring.slices
              for word in ("--slice", f"{one['id']}={one['what']}", "--minutes",
                           str(one["minutes"])))])
    if existing:
        again += " --existing"
    looked: dict[int, _Look] = {}

    def look(view) -> _Look:
        # One look for one reading of the log: each gate and the act speak of it.
        if id(view) not in looked:
            looked[id(view)] = _look(view, name=name, round_dir=round_dir, stage_id=stage_id,
                                     declaring=declaring, builder=builder, by=by,
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
        names = {"round": name, "stage": found.stage, "repo": found.repo,
                 "branch": found.branch}
        if found.adopt is None:
            _add_worktree(found, existing=existing)
            git_dir = places.worktree_git_dir(found.worktree)
            if git_dir is None:
                raise RoundsError(f"git made no worktree at {found.worktree}",
                                  f"look at {found.worktree}, remove it, then: {again}")
            _mark(git_dir / places.WORKTREE_MARKER, places.marker(build_id, **names),
                  again)
        brought = private_files.bring(
            found.root, found.worktree, found.private, branch=found.branch,
            is_new_branch=not existing, again=again)
        scratch = places.scratch_place(found.repo, found.branch, build_id, env)
        if not (found.adopt is not None and _is_own_scratch(scratch, build_id, names)):
            _make_scratch(scratch, places.marker(build_id, **names), again)
        return build_id, scratch, found.adopt is not None, brought

    def event(view, result) -> list[write.Entry]:
        found = look(view)
        build_id, scratch, _adopted, brought = result
        entries = []
        if found.declared is not None:
            stage = found.declared
            entries.append(write.Entry("stage.declared", {
                "id": stage.id, "repo": stage.repo, "branch": stage.branch, "after": [],
                "slices": [dict(one) for one in stage.slices]}))
        fields = {"stage": found.stage, "repo": found.repo, "branch": found.branch,
                  "base": found.base, "target": found.target,
                  "worktree": places.contract_home(found.worktree),
                  "scratch": places.contract_home(scratch),
                  "builder": builder, "host": places.host(), "build": build_id}
        if record is not None:
            fields["record"] = record
        if brought:
            fields["brought"] = brought
        return [*entries, write.Entry("build.commissioned", fields)]

    written = write.record(
        write.Where(home.project, home.rounds_dir, slug), by=by,
        gates=[ending.takes_new_work(name, "a new build"), ready, has_a_pointer],
        act=make, event=event, wait_s=wait_s, at=at)
    one = written.events[-1]
    # The state the block is read from is the log as it stood under the lock with
    # this verb's own events: a stage declared here is in it.
    after = states.fold([*written.view.events, *written.events])
    worktree = Path(places.expand_home(one["worktree"]))
    scratch = Path(places.expand_home(one["scratch"]))
    made = commission_block.block(
        after, written.view.project_state, name=name, stage=one["stage"],
        repo=one["repo"], branch=one["branch"], base=one["base"], target=one["target"],
        worktree=worktree, scratch=scratch, settings=settings,
        brought=one.get("brought") or ())
    limit = own.round_setting(settings, home.project, sizing.LIMIT)
    declared = [event for event in written.events if event["type"] == "stage.declared"]
    return Commissioned(
        name=name, stage=one["stage"], repo=one["repo"], branch=one["branch"],
        base=one["base"], target=one["target"], worktree=worktree,
        scratch=scratch, build=one["build"], host=one["host"],
        builder=builder, record=record, adopted=written.result[2], event=one,
        block=made, oversized=freezing.oversized({"stage": declared}, limit),
        slice_minutes=limit, failed=written.failed,
        brought=tuple(one.get("brought") or ()))


# -- what is read under the lock ---------------------------------------------------------

def _declaring(name: str, stage_id: str | None, *, repo: str | None, branch: str | None,
               slices: Sequence[tuple[str, str, int]]) -> Declaring | None:
    """What the form with no stage declares, or None for the form that names one.
    Raises `BadArgument` for a mix of the two forms or a slice that can't be one."""
    if stage_id is not None:
        if repo is not None or branch is not None or slices:
            raise BadArgument(
                f"{stage_id} is a stage of the plan, and --repo, --branch and --slice "
                "declare a stage of their own",
                f"moxi rounds commission {name} {stage_id} --builder NAME, or leave "
                "the stage out to declare one")
        return None
    if not repo or not branch:
        raise BadArgument(
            "no stage is named, so the stage is declared here, and that needs both "
            "the repo and the branch",
            f"moxi rounds commission {name} --repo KEY --branch NAME --builder NAME, "
            f"or name a stage of the plan: moxi rounds commission {name} B<n> "
            "--builder NAME")
    found: list[dict] = []
    for slice_id, what, minutes in slices:
        if not isinstance(slice_id, str) or not design_block.SLICE_ID.fullmatch(slice_id):
            raise BadArgument(f"{slice_id!r} is no slice id: letters, digits, '.', '_' "
                              "and '-', starting with a letter or digit",
                              "--slice ID=TEXT, as --slice 1a=the log")
        if any(one["id"] == slice_id for one in found):
            raise BadArgument(f"the slice {slice_id} is given twice",
                              "give each slice once, under an id of its own")
        if not isinstance(what, str) or not what.strip() or "\n" in what:
            raise BadArgument(f"the slice {slice_id} says nothing it builds",
                              f"--slice {slice_id}=TEXT, one line")
        if isinstance(minutes, bool) or not isinstance(minutes, int) or minutes < 1:
            raise BadArgument(f"the slice {slice_id} is given {minutes!r} minutes, and "
                              "a slice is given a whole number of minutes, 1 or more",
                              f"--slice {slice_id}=TEXT --minutes N")
        found.append({"id": slice_id, "what": what.strip(), "minutes": minutes})
    return Declaring(repo=repo, branch=branch, slices=tuple(found))


def _look(view, *, name: str, round_dir: Path, stage_id: str | None,
          declaring: Declaring | None, builder: str, by: str, existing: bool,
          settings, again: str, env) -> _Look:
    found = _Look()
    state = view.state
    show = f"moxi rounds show {name}"
    if not stepping.has_step(state, BUILD):
        flow = stepping.current(state).name
        found.unmet.append(Unmet(
            f"the flow {flow} of {name} has no {BUILD} step, so nothing in it is "
            "built; a flow that has one is the lead's to move it to",
            f"moxi rounds mark {name} --flow NAME"))
        return found
    if declaring is not None:
        stage = _to_declare(found, state, name=name, declaring=declaring,
                            builder=builder, by=by)
        if stage is None:
            return found
        found.declared = stage
        state = stages.with_stage(state, stage)
        stage_id = stage.id
    stage = stages.find(state, stage_id)
    found.stage = stage_id
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
    step = steps.one(state, steps.COMMISSION, stage_id, name=name,
                     register=view.project_state)
    if step is not None and step.state == steps.DONE:
        found.unmet.append(Unmet(
            f"{stage_id} has a build that is open or finished, and a stage is built by "
            "one build at a time", show))
        return found
    if step is not None:
        waits = list(step.waits)
        if found.declared is not None:
            # A question holds a stage the moment it is declared, and nothing records a
            # skip of a stage that is not declared: say the operator's ways past it.
            held = set(steps.held_by(state, stage_id, name=name))
            waits = [one for one in waits if one not in held]
            waits += steps.held_unbuilt(state, stage_id, name=name)
        found.unmet += waits
    if (stepping.has_step(state, steps.DESIGN)
            and not skips.is_skipped(state, steps.DESIGN)
            and versions.current(state) is not None):
        moved = versions.unchanged(state, round_dir, name)
        if moved is not None:
            found.unmet.append(moved)
    found.unmet += _git(found, view, name=name, stage=stage, existing=existing,
                        settings=settings, again=again, env=env)
    return found


def _to_declare(found: _Look, state: states.State, *, name: str,
                declaring: Declaring, builder: str, by: str) -> states.Stage | None:
    """The stage the form with no stage declares, or None with what refuses it in
    `found`."""
    if stepping.has_step(state, stages.VERIFIES):
        found.unmet.append(Unmet(
            f"the flow {state.flow} of {name} verifies its design, so each of its "
            "stages is declared in the design and commissioned by its id",
            f"give {versions.DESIGN_FILE} a [[stage]] for it, then moxi rounds fold "
            f"{name}"))
        return None
    try:
        repo, _root = folders.root(declaring.repo)
    except RoundsError as exc:
        found.unmet.append(Unmet(exc.reason, exc.fix or "moxi projects list"))
        return None
    unbuilt = stages.unbuilt_in(state, repo)
    if unbuilt is not None:
        found.unmet.append(Unmet(
            f"{unbuilt.id} is a stage of {name} in {repo} with no open or finished "
            "build, and a declared stage is commissioned by its id: a second stage "
            "would be the same work twice",
            f"moxi rounds commission {name} {unbuilt.id} --builder {builder}"))
        return None
    return states.Stage(id=stages.next_id(state), repo=repo, branch=declaring.branch,
                        round=None, after=(), slices=declaring.slices, why=None,
                        version=None, actor=by, at="")


def _git(found: _Look, view, *, name: str, stage, existing: bool, settings, again: str,
         env) -> list[Unmet]:
    """What git and the folders say of the build: its target and base, its branch, and
    what is at the worktree's place. Fills `found` as it goes."""
    try:
        repo, root = folders.root(stage.repo)
    except RoundsError as exc:
        return [Unmet(exc.reason, exc.fix or "moxi projects list")]
    found.repo, found.root, found.branch = repo, root, stage.branch
    git = Git(root)
    try:
        return _read_git(found, git, view, name=name, stage=stage, existing=existing,
                         settings=settings, again=again, env=env)
    except GitError as exc:
        return [Unmet(exc.reason, exc.fix or again)]


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
    holders, _unread = by_branch.open_on(repo, branch, settings=settings)
    if holders:
        holder = holders[0]
        return [Unmet(f"the branch {branch} of {repo} is the branch of the open build "
                      f"{holder.stage} of {holder.round}, and a repo's branch has one "
                      "open build at a time: that build is finished or dropped from "
                      "its own round first", f"moxi rounds show {holder.round}")]
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
    if found.adopt is not None:
        moved = _adopted_base(found, git, tip, again=again)
        if moved is not None:
            return [*unmet, moved]
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
                         settings=settings, again=again)
    found.private = tuple(private_files.listed(settings, repo))
    checks_out = tip if (found.adopt is not None or existing) else found.base
    if found.private and checks_out is not None:
        unmet += private_files.unmet(git, found.private, checks_out, repo=repo,
                                     again=again)
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


def _adopted_base(found: _Look, git: Git, tip: str | None, *, again: str) -> Unmet | None:
    """Set the base of an adopted rerun to the merge base of its branch's tip and the
    target, the commit the stopped run cut the branch from. The target's head now
    would move the base when the target moved between the runs. Returns the unmet
    condition when there is no single such commit."""
    fix = f"look at {found.worktree}, remove it and its branch, then: {again}"
    if tip is None:
        return Unmet(f"the worktree at {found.worktree} was left by a stopped run of "
                     f"this commission, and its branch {found.branch} is gone", fix)
    bases = git.merge_bases(tip, found.base)
    if len(bases) != 1:
        said = "no commit" if not bases else f"{len(bases)} merge bases"
        return Unmet(f"the branch {found.branch}, left by a stopped run of this "
                     f"commission, has {said} in common with the target "
                     f"{found.target}, so the commit it was cut from can't be read", fix)
    found.base = bases[0]
    return None


def _shares(found: _Look, git: Git, *, name: str, stage, opened, settings,
            again: str) -> list[Unmet]:
    """The unmet condition of a branch taken with `--existing`, read as a build cut at
    the base now: one that overlaps another open build of the repo and target, or that
    holds nothing of its own because another build's landing on record brought it onto
    the target. A branch strictly inside or around another build's is taken: the
    finish gate orders the two."""
    candidate = builds.Build(stage=stage.id, repo=found.repo, branch=found.branch,
                             base=found.base, target=found.target, n=0)
    homes, _unresolved = folders.homes(settings)
    landed = index.landings(homes, repo=found.repo, target=found.target)
    status = builds.read(candidate, git, landings=[one.landing for one in landed])
    if status.state == builds.EMPTY and not status.has_own_commits:
        return _landed_already(found, git, status, landed, name=name, again=again)
    if status.state not in builds.RANGED:
        return []
    statuses = {(one.round, one.build.stage): one.status for one in opened().builds
                if one.status is not None and one.build.target == found.target}
    key = (name, stage.id)
    statuses[key] = status
    shared = [other for other, how in builds.sharing(git, statuses)[key]
              if how == builds.OVERLAPS]
    if not shared:
        return []
    said = ", ".join(f"{other} {other_stage}" for other, other_stage in shared)
    return [Unmet(f"the branch {found.branch} overlaps the open build of {said}: they "
                  "share commits and neither range holds the other whole, so neither "
                  "could finish; a build's branch holds no other build's work but a "
                  "stack inside or around it",
                  f"cut the work onto a branch of its own, or end that build first: "
                  f"moxi rounds show {shared[0][0]}")]


def _landed_already(found: _Look, git: Git, status: builds.Status, landed, *, name: str,
                    again: str) -> list[Unmet]:
    """The unmet condition of a branch taken with `--existing` that reads empty with
    nothing of its own, when a landing on record brought its head onto the target: the
    build it would make could never finish. None when no landing did."""
    head = status.head
    under = [one for one in landed if head is not None
             and git.is_ancestor(head, one.landing.landing)
             and not git.is_ancestor(head, one.landing.target_before)]
    if not under:
        return []
    by = under[0]
    return [Unmet(f"the branch {found.branch} came onto {found.target} by the finish of "
                  f"{by.round} {by.landing.stage}, at {by.landing.landing[:12]}: a build "
                  "of it would hold nothing of its own and could not finish",
                  f"drop the round, as its work is on the target: moxi rounds drop {name} "
                  "--by lead --record PTR; or, for work still to do, delete the branch, "
                  f"whose commits {found.target} holds, then cut one of its own: git -C "
                  f"{found.root} branch -d {found.branch}, then "
                  f"{again.replace(' --existing', '')}")]


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
                        env=gitreads.git_env(), stdin_data="")
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GitError(argv, f"could not run: {exc}") from None
    if done.returncode != 0:
        said = " ".join((done.stderr or "").split())[:200]
        raise GitError(argv, f"exited {done.returncode}" + (f": {said}" if said else ""))


def _mark(path: Path, marker: places.Marker, again: str) -> None:
    try:
        places.write_marker(path, marker)
    except OSError as exc:
        raise RoundsError(f"the marker {path} can't be written: {exc}",
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
    except OSError as exc:
        raise RoundsError(f"the scratch folder {place} can't be made: {exc}",
                          f"make {place.parent} one that can be written to, then: "
                          f"{again}") from None
    _mark(place / places.SCRATCH_MARKER, marker, again)


def _is_own_scratch(place: Path, build_id: str, names: dict) -> bool:
    """Whether `place` is a scratch folder whose marker names this build."""
    marker = places.read_marker(place / places.SCRATCH_MARKER)
    return (not place.is_symlink() and place.is_dir() and marker is not None
            and marker.build == build_id and marker.names(**names))


__all__ = ["BUILD", "Commissioned", "commission"]

"""Builds: each build a round's log records, and its state as read from git.

**A build** is one commission of a stage and the records written for that stage while
it was open: from its own `build.commissioned` up to its drop, its finish, or the next
commission of the stage. A stage commissioned again after a drop is a new build, and the
dropped build's records count for nothing in it. `builds(state)` gives every build of a
round's log, `current(state, stage)` the last one of a stage.

**Its readings.** Each record that read the build at a commit holds that commit `h` and
`x`, the merge-base of `h` and the target's head at that moment: a check's run and a
verdict on a report of the build. `READINGS` names the field of each. A reading is
off-target when `h` is not `x`, so the branch held commits the target did not, and it
shows work when, also, the tree of `h` differs from the tree of `x`. A reading whose
merge-base git gave as none or several holds no `x`: it is not off-target and shows no
work.

**Its state** is derived on every read, from trees and ancestry, and nothing stores it.
No rule asks who made a commit, and no commit is assigned to a build. `read` applies the
rules in order, and the first that fits gives the state:

1. A recorded end: a build with a drop on record is `dropped`, one whose finish is done
   is `finished`. These are acts, and git is not read.
2. The head `H` is the branch's tip when the branch exists. With no branch, it is the
   one commit the build's records hold (a reading's commit, or a finish's tip) that git
   still has and that has all the others as ancestors; when no one has, the records lie
   on lines that parted and the build is `unclear`. Nothing is picked by the order of
   the records. When that commit is on the target, off its first-parent line, and the
   merge that brought it there is no landing, `H` is instead that merge's parent, other
   than the first, that holds it: what was merged, which may hold work made after the
   last record. With no branch and no such commit the build is `gone`.
3. `H` off the target: with no branch, `gone`. Otherwise, with `X` the merge base of
   `H` and the target's head, `empty` when the tree of `H` is the tree of `X` (the branch
   would change nothing on the target), else `building`. An `empty` build holds commits
   of its own when a commit that is not a merge is on the branch and not the target.

`H` on the target is read by the landing rules, which this module leaves to its caller:
`read` gives such a build no state, only its head.

Where git gives no single answer (no merge base or several, or a merge with more than
one other parent that holds the head), the build is `unclear`, and no later rule is
tried. It reads so for as long as git does.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass

from . import state as states
from .errors import RoundsError
from .gitreads import Git, is_id

#: A build with a drop on record.
DROPPED = "dropped"
#: A build whose finish is done.
FINISHED = "finished"
#: A build with no branch and nothing on record that git still has, or whose branch is
#: gone with its head off the target.
GONE = "gone"
#: A build whose branch would change nothing on the target.
EMPTY = "empty"
#: A build whose branch holds work the target does not.
BUILDING = "building"
#: A build git can't give one answer for.
UNCLEAR = "unclear"

#: What gave a build its state, so a reader can tell two `unclear` builds apart.
#: An end on record.
ENDED = "ended"
#: The branch is gone and nothing on record is a commit git still has.
NO_HEAD = "no head"
#: The head is off the target.
OFF_TARGET = "off the target"
#: The head is on the target; the landing rules read it.
ON_TARGET = "on the target"
#: git gave no single answer where one was needed.
NO_ANSWER = "no single answer"
#: The build's records lie on lines that parted.
PARTED = "parted"

#: Each type of record that reads a build at a commit: its field for the commit `h`,
#: and its field for `x`, the merge-base of `h` and the target's head then.
READINGS = {
    "check.ran": ("commit", "merge_base"),
    "verdict.recorded": ("head", "merge_base"),
}
#: Each type of record that holds a finish's tip, and its field for it.
TIPS = {"finish.action": "tip", "finish.done": "tip"}


@dataclass(frozen=True)
class Reading:
    """One record that read a build at commit `commit`: the record's `type` and its line
    `n`, and `merge_base`, the merge-base of that commit and the target's head, None when
    git gave none or several."""

    type: str
    n: int
    commit: str
    merge_base: str | None

    @property
    def off_target(self) -> bool:
        """Whether the branch held commits the target did not, when this was read."""
        return self.merge_base is not None and self.commit != self.merge_base


@dataclass(frozen=True)
class Build:
    """One build: its `stage`, the `repo` and `branch` it was cut in, its `base` (the
    target's head at its commission) and `target`, `n` the line of its commission, the
    `readings` and finish `tips` recorded for it, oldest first, and `ended`, `DROPPED`
    or `FINISHED` when one is on record, else None."""

    stage: str
    repo: str
    branch: str
    base: str
    target: str
    n: int
    readings: tuple[Reading, ...] = ()
    tips: tuple[str, ...] = ()
    ended: str | None = None

    def commits(self) -> tuple[str, ...]:
        """Every commit the build's records hold, each once: its readings' and its
        finish tips."""
        held = [one.commit for one in self.readings] + list(self.tips)
        return tuple(dict.fromkeys(held))


@dataclass(frozen=True)
class Status:
    """A build's state as read now. `state` is one of the states above, or None for a
    build whose head is on the target, which the landing rules read. `by` says what gave
    it, `why` the same in words. `head` is `H` and `target_head` the target's head `M`,
    both None for a build whose end is on record. `merge_base` is `X` for a head off the
    target. `own_commits` is true for an `empty` build that holds commits of its own."""

    state: str | None
    by: str
    why: str
    head: str | None = None
    target_head: str | None = None
    merge_base: str | None = None
    own_commits: bool = False


def builds(round_state: states.State) -> tuple[Build, ...]:
    """Every build the round's log records, in the order commissioned."""
    found: list[dict] = []
    open_by_stage: dict[str, dict] = {}
    for event in round_state.history:
        stage = event.get("stage")
        if not isinstance(stage, str):
            continue
        kind = event["type"]
        if kind == "build.commissioned":
            one = {"event": event, "readings": [], "tips": [], "ended": None}
            found.append(one)
            open_by_stage[stage] = one
            continue
        one = open_by_stage.get(stage)
        if one is None:
            continue
        if kind in READINGS:
            reading = _reading(event)
            if reading is not None:
                one["readings"].append(reading)
        if kind in TIPS and is_id(event.get(TIPS[kind])):
            one["tips"].append(event[TIPS[kind]])
        if kind in ("build.dropped", "finish.done"):
            one["ended"] = DROPPED if kind == "build.dropped" else FINISHED
            del open_by_stage[stage]
    return tuple(_build(one) for one in found)


def current(round_state: states.State, stage_id: str) -> Build | None:
    """The last build of the stage `stage_id`, or None when it was never commissioned."""
    last = None
    for one in builds(round_state):
        if one.stage == stage_id:
            last = one
    return last


def _reading(event: dict) -> Reading | None:
    commit_field, base_field = READINGS[event["type"]]
    commit = event.get(commit_field)
    if not is_id(commit):
        return None
    base = event.get(base_field)
    return Reading(event["type"], event["n"], commit, base if is_id(base) else None)


def _build(one: dict) -> Build:
    event = one["event"]

    def text(name: str) -> str:
        value = event.get(name)
        return value if isinstance(value, str) else ""

    return Build(stage=event["stage"], repo=text("repo"), branch=text("branch"),
                 base=text("base"), target=text("target"), n=event["n"],
                 readings=tuple(one["readings"]), tips=tuple(one["tips"]),
                 ended=one["ended"])


def shows_work(git: Git, reading: Reading) -> bool:
    """Whether `reading` is off-target and the tree of its commit differs from the tree
    of its merge-base. A reading whose commits git no longer has shows nothing."""
    if not reading.off_target:
        return False
    if len(git.present((reading.commit, reading.merge_base))) != 2:
        return False
    return git.tree(reading.commit) != git.tree(reading.merge_base)


class NoTarget(RoundsError):
    """The build's target branch is not in its repo, so nothing of it can be read from
    git."""


def read(build: Build, git: Git, *, landings: Collection[str] = ()) -> Status:
    """The state of `build`, read from the repo `git` now. `landings` are the landing
    commits on record for the repo's target: a merge among them that brought the
    build's recorded head onto the target's line was a finish's, not a hand merge.
    Raises `NoTarget` when the target branch is not in the repo."""
    if build.ended is not None:
        return Status(build.ended, ENDED, f"its {_END_WORD[build.ended]} is on record")
    target_head = git.branch(build.target)
    if target_head is None:
        raise NoTarget(f"the target branch {build.target} is not in {git.root}",
                       f"bring the branch {build.target} back into the repo")
    tip = git.branch(build.branch)
    if tip is not None:
        head = tip
    else:
        recorded = git.present(build.commits())
        if not recorded:
            return Status(GONE, NO_HEAD, f"the branch {build.branch} is gone, and no "
                          "commit on record for the build is still in git",
                          target_head=target_head)
        head = git.newest(recorded)
        if head is None:
            return Status(UNCLEAR, PARTED, f"the branch {build.branch} is gone, and the "
                          "build's commits on record lie on lines that parted: no one "
                          "of them has the others as ancestors", target_head=target_head)
        if git.is_ancestor(head, target_head):
            merge = git.bringing_merge(head, target_head)
            if merge is not None and merge not in landings:
                holders = [one for one in git.parents(merge)[1:]
                           if git.is_ancestor(head, one)]
                if len(holders) != 1:
                    return Status(UNCLEAR, NO_ANSWER, f"the merge {merge[:12]} that "
                                  f"brought the build's head {head[:12]} onto "
                                  f"{build.target} has {len(holders)} parents besides "
                                  "its first that hold it", head=head,
                                  target_head=target_head)
                head = holders[0]
    if git.is_ancestor(head, target_head):
        return Status(None, ON_TARGET, f"its head {head[:12]} is on {build.target}",
                      head=head, target_head=target_head)
    if tip is None:
        return Status(GONE, OFF_TARGET, f"the branch {build.branch} is gone, and its "
                      f"last head {head[:12]} is not on {build.target}", head=head,
                      target_head=target_head)
    bases = git.merge_bases(head, target_head)
    if len(bases) != 1:
        said = "no merge base" if not bases else f"{len(bases)} merge bases"
        return Status(UNCLEAR, NO_ANSWER, f"git gives {said} for {build.branch} and "
                      f"{build.target}", head=head, target_head=target_head)
    base = bases[0]
    if git.tree(head) == git.tree(base):
        own = git.holds_non_merge(target_head, head)
        return Status(EMPTY, OFF_TARGET, f"{build.branch} would change nothing on "
                      f"{build.target}" + (", though it holds commits of its own"
                                           if own else ""),
                      head=head, target_head=target_head, merge_base=base,
                      own_commits=own)
    return Status(BUILDING, OFF_TARGET, f"{build.branch} holds work {build.target} does "
                  "not", head=head, target_head=target_head, merge_base=base)


_END_WORD = {DROPPED: "drop", FINISHED: "finish"}


__all__ = ["DROPPED", "FINISHED", "GONE", "EMPTY", "BUILDING", "UNCLEAR", "ENDED",
           "NO_HEAD", "OFF_TARGET", "ON_TARGET", "NO_ANSWER", "PARTED", "READINGS",
           "TIPS", "Reading", "Build", "Status", "NoTarget", "builds", "current",
           "shows_work", "read"]

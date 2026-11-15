"""Builds: each build a round's log records, and its state as read from git.

**A build** is one commission of a stage and the records written for that stage while
it was open: from its own `build.commissioned` up to its drop, its finish, its round's
drop, or the next commission of the stage. A stage commissioned again after a drop is a new build, and the
dropped build's records count for nothing in it. `builds(state)` gives every build of a
round's log, `current(state, stage)` the last one of a stage. Its base is the target's
head at its commission, until a repair moves it while the build is open: the lead's
resync after the target was rewritten, which names the base it moves.

**Its landings.** Each merge a finish recorded for the build, as made (`ok`) or as
found already on the target (`found`), is a landing: the commit the target's head
became (the merge commit, or the tip when the target was fast-forwarded onto the
branch), and the target's head before it. A landing stays the build's whatever became
of the build since. `landings(state)` gives every landing of a round's log with the
repo and target of its build.

**Its readings.** Each record that read the build at a commit holds that commit `h` and
`x`, the merge-base of `h` and the target's head at that moment: a check's run and a
verdict on a report of the build. `READINGS` names the field of each. The operator's
credit of a fast-forward (`credit:B<n>`, a ruling naming the build by its commission's
line) is one more, while it stands: `h` is the head it recorded and `x` the build's
base, so a build it credits reads `merged` with the base as `P`. A reading is
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
4. `H` on the target by the build's own finish: a landing of the build is on record and
   `H` is that landing's tip or a descendant of it: `merged`. Of several landings, the
   one read is the one `H` landed under (reachable from its landing and not from the
   target before it); under none or under more than one, the build is `unclear`.
5. `H` on the target with nothing of its own: `empty`, when `H` is the base or under
   it; when `H` is off the target's first-parent line and the merge that brought it is
   another build's landing; or when `H` is on that line and every commit of the line
   after the base up to `H` is another build's merge landing, or one that build's
   fast-forward landing brought.
6. `H` on the target otherwise: some commit reached the target with no finish. Off
   the target's line, someone merged it by hand: `empty` when the tree of `H` is the
   tree of its merge base with the merge's first parent; else `merged` when the merge
   changed the target (its tree differs from its first parent's), and `unclear` when
   it did not (a merge that put nothing on the target credits nothing). On the line,
   the target was fast-forwarded and no commit says which side moved: `merged` when the
   build has an off-target reading that shows work, at `H` or under it, that landed
   under no other build's landing, and `unclear` when it has none.

Where git gives no single answer (no merge base or several, a merge with more than one
other parent that holds the head, a base the target no longer holds because it was
rewritten, or no one newest candidate for the target before the landing), the build is
`unclear`, and no later rule is tried. It reads so for as long as git does.

**The target before the landing, `P`**, answers what the target was when the build
reached it: the target's head for a build that is not `merged`; for one merged by its
finish, the target before its landing; for a hand merge, the merge's first parent; for
a fast-forward, the newest of the base and the merge-base of each off-target reading
under `H`. A landing is never a candidate.

A commit that is in no landing, reading or base git still has is not read: a landing,
reading or base whose commits git no longer has counts for nothing.

**Sharing.** A build that reads `building` or `merged` has a range: the commits
reachable from `H` and not from `P` (`span`). Two such builds of one repo and target,
in any rounds, share when their ranges have a commit in common (`sharing`). git does
not say which branch a commit was made on, so sharing is read from both sides alike:
no order of records, commissions or reads changes it, and neither build is given the
shared commits or loses them. A build that ended has no range.

**Idle** qualifies `building`: a build reads idle when the commit date of its head is
older than the `idle_days` of its round's project (`idle`).

**Who may drop it** (`drop_by`) is read from its state: the lead, for a build with no
work a drop could lose (`gone`, or `empty` holding no commit of its own) that shares
with no other; the operator, for every other. `credit_refusal` says why a build can't
be credited: only one `unclear` by a fast-forward with no reading, whose head's tree
differs from its base's, can be.

**Open builds.** A build is open while no end is on record for it, it is the last
build of its stage, and its round is open: the builds still open when a round ends end
with it, and a round's drop on record is a drop of each. `opened(state)` lists them for the index, and `from_item` makes a `Build`
again from what the index keeps.
"""

from __future__ import annotations

from collections.abc import Hashable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TypeVar

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
#: A build whose work is on the target.
MERGED = "merged"

#: What gave a build its state, so a reader can tell two `unclear` builds apart.
#: An end on record.
ENDED = "ended"
#: The branch is gone and nothing on record is a commit git still has.
NO_HEAD = "no head"
#: The head is off the target.
OFF_TARGET = "off the target"
#: git gave no single answer where one was needed.
NO_ANSWER = "no single answer"
#: The build's records lie on lines that parted.
PARTED = "parted"
#: The build has several landings, and its head landed under none of them or several.
NO_LANDING = "no one landing"
#: The head is on the target by the build's own finish.
OWN_LANDING = "its own landing"
#: The head is on the target, and what the target gained up to it was the target's or
#: came by other builds' finishes.
NOTHING_OWN = "nothing of its own"
#: The head is on the target by a merge no finish made.
HAND_MERGE = "a hand merge"
#: The head is on the target's first-parent line, which no finish moved there.
FAST_FORWARD = "a fast-forward"

#: Each type of record that reads a build at a commit: its field for the commit `h`,
#: and its field for `x`, the merge-base of `h` and the target's head then.
READINGS = {
    "check.ran": ("commit", "merge_base"),
    "verdict.recorded": ("head", "merge_base"),
}
#: Each type of record that holds a finish's tip, and its field for it.
TIPS = {"finish.action": "tip", "finish.done": "tip"}
#: What starts the item of the operator's credit of a fast-forward; the stage follows.
CREDIT = "credit:"
#: Who may drop a build.
LEAD = "lead"
OPERATOR = "operator"

#: The kind of finish action that lands a build, and the states of its record that say
#: it did: made by the finish, or found already on the target.
MERGE = "merge"
LANDED = ("ok", "found")
# The fields of a merge's record a landing is read from: a record that lacks one of
# them as a full commit id is no landing.
_LANDING = ("landing", "target_before", "tip")


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
class Landing:
    """One merge a finish recorded for a build: `landing` the commit the target's head
    became, `target_before` its head before, `tip` the build's tip the finish ran at,
    `stage` and `build` the stage and the line of the build's commission, `n` the line
    of the record, and the build's `repo` and `target`."""

    stage: str
    build: int
    n: int
    repo: str
    target: str
    landing: str
    target_before: str
    tip: str

    @property
    def fast_forward(self) -> bool:
        """Whether the target was fast-forwarded onto the branch: no merge commit was
        made, and the landing is the tip."""
        return self.landing == self.tip


@dataclass(frozen=True)
class Build:
    """One build: its `stage`, the `repo` and `branch` it was cut in, its `base` (the
    target's head at its commission) and `target`, `n` the line of its commission, the
    `readings`, finish `tips` and `landings` recorded for it, oldest first, and `ended`,
    `DROPPED` or `FINISHED` when one is on record, else None."""

    stage: str
    repo: str
    branch: str
    base: str
    target: str
    n: int
    readings: tuple[Reading, ...] = ()
    tips: tuple[str, ...] = ()
    landings: tuple[Landing, ...] = ()
    ended: str | None = None

    def commits(self) -> tuple[str, ...]:
        """Every commit the build's records hold, each once: its readings' and its
        finish tips."""
        held = [one.commit for one in self.readings] + list(self.tips)
        return tuple(dict.fromkeys(held))


@dataclass(frozen=True)
class Status:
    """A build's state as read now. `state` is one of the states above; `by` says what
    gave it, `why` the same in words. `head` is `H`, `target_head` the target's head
    `M` and `target_before` is `P`, each None for a build whose end is on record, and
    `head` for one with no head. `merge_base` is `X` for a head off the target.
    `own_commits` is true for an `empty` build that holds commits of its own.
    `hand_merge` is the merge that brought a head merged by hand onto the target."""

    state: str
    by: str
    why: str
    head: str | None = None
    target_head: str | None = None
    merge_base: str | None = None
    own_commits: bool = False
    target_before: str | None = None
    hand_merge: str | None = None


def builds(round_state: states.State) -> tuple[Build, ...]:
    """Every build the round's log records, in the order commissioned."""
    found: list[dict] = []
    open_by_stage: dict[str, dict] = {}
    credited: list[dict] = []
    taken_back: set[str] = set()
    for event in round_state.history:
        kind = event["type"]
        if kind == "ruling.recorded":
            if isinstance(event.get("revokes"), str):
                taken_back.add(event["revokes"])
            if (str(event.get("rules") or "").startswith(CREDIT)
                    and is_id(event.get("head")) and isinstance(event.get("build"), int)):
                credited.append(event)
        if kind == "round.dropped":
            for one in open_by_stage.values():
                one["ended"] = DROPPED
            open_by_stage.clear()
        stage = event.get("stage")
        if not isinstance(stage, str):
            continue
        if kind == "build.commissioned":
            one = {"event": event, "readings": [], "tips": [], "landings": [],
                   "ended": None}
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
        if (kind == "finish.action" and event.get("kind") == MERGE
                and event.get("state") in LANDED
                and all(is_id(event.get(name)) for name in _LANDING)):
            one["landings"].append(event)
        if (kind == "repair" and event.get("what") == states.BASE
                and _base_of(one) == _was(event)):
            one["base"] = event["fields"]["base"]["now"]
        if kind in ("build.dropped", "finish.done"):
            one["ended"] = DROPPED if kind == "build.dropped" else FINISHED
            del open_by_stage[stage]
    by_line = {one["event"]["n"]: one for one in found}
    for credit in credited:
        one = by_line.get(credit["build"])
        if one is not None and credit.get("id") not in taken_back and is_id(_base_of(one)):
            one["readings"].append(Reading(credit["type"], credit["n"], credit["head"],
                                           _base_of(one)))
            one["readings"].sort(key=lambda reading: reading.n)
    return tuple(_build(one) for one in found)


def opened(round_state: states.State):
    """Each open build of the round, as `(stage, body)` for the index, in the order
    commissioned. The body holds the build's commission and its readings and finish
    tips; its landings are in the index apart, with the build's line."""
    if round_state.status != states.OPEN:
        return
    last = {one.stage: one for one in builds(round_state)}
    for one in sorted(last.values(), key=lambda one: one.n):
        if one.ended is None:
            yield one.stage, {
                "stage": one.stage, "n": one.n, "repo": one.repo, "branch": one.branch,
                "base": one.base, "target": one.target,
                "readings": [{"type": reading.type, "n": reading.n,
                              "commit": reading.commit,
                              "merge_base": reading.merge_base}
                             for reading in one.readings],
                "tips": list(one.tips)}


def from_item(body: Mapping, landed: Iterable[Landing]) -> Build:
    """The open build an index item's `body` holds, as `opened` wrote it, with those of
    the round's landings `landed` that are its own."""
    return Build(stage=body["stage"], repo=body["repo"], branch=body["branch"],
                 base=body["base"], target=body["target"], n=body["n"],
                 readings=tuple(Reading(one["type"], one["n"], one["commit"],
                                        one["merge_base"]) for one in body["readings"]),
                 tips=tuple(body["tips"]),
                 landings=tuple(one for one in landed
                                if one.build == body["n"] and one.stage == body["stage"]))


def landings(round_state: states.State) -> tuple[Landing, ...]:
    """Every landing the round's log records, in the log's order."""
    return tuple(landing for one in builds(round_state) for landing in one.landings)


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


def _was(event: dict) -> str | None:
    """The base a repair of a build's base moved, when it holds a full commit id for
    both the old base and the new; else None, and nothing moves."""
    change = (event.get("fields") or {}).get("base") if isinstance(
        event.get("fields"), dict) else None
    if (isinstance(change, dict) and is_id(change.get("was"))
            and is_id(change.get("now"))):
        return change["was"]
    return None


def _base_of(one: dict) -> str:
    base = one.get("base", one["event"].get("base"))
    return base if isinstance(base, str) else ""


def _build(one: dict) -> Build:
    event = one["event"]

    def text(name: str) -> str:
        value = event.get(name)
        return value if isinstance(value, str) else ""

    repo, target = text("repo"), text("target")
    landed = tuple(
        Landing(stage=event["stage"], build=event["n"], n=merge["n"], repo=repo,
                target=target, landing=merge["landing"],
                target_before=merge["target_before"], tip=merge["tip"])
        for merge in one["landings"])
    return Build(stage=event["stage"], repo=repo, branch=text("branch"),
                 base=_base_of(one), target=target, n=event["n"],
                 readings=tuple(one["readings"]), tips=tuple(one["tips"]),
                 landings=landed, ended=one["ended"])


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


def read(build: Build, git: Git, *, landings: Iterable[Landing] = (),
         tip: str | None = None) -> Status:
    """The state of `build`, read from the repo `git` now. `landings` are the landings
    on record for the build's repo and target, of every build in any round; the
    build's own among them are told by `build.landings`. `tip` is the commit to read as
    the branch's tip, the branch's tip as it is now when left out: `finish` reads its
    build at the tip its merge took, though the branch may have moved since. Raises
    `NoTarget` when the target branch is not in the repo."""
    if build.ended is not None:
        return Status(build.ended, ENDED, f"its {_END_WORD[build.ended]} is on record")
    target_head = git.branch(build.target)
    if target_head is None:
        raise NoTarget(f"the target branch {build.target} is not in {git.root}",
                       f"bring the branch {build.target} back into the repo")
    every = (*build.landings, *(one for one in landings if one not in build.landings))
    have = set(git.present(commit for one in every
                           for commit in (one.landing, one.target_before, one.tip)))
    every = tuple(one for one in every
                  if {one.landing, one.target_before, one.tip} <= have)
    seen = _Seen(build, git, target_head,
                 own=tuple(one for one in every if one in build.landings),
                 others=tuple(one for one in every if one not in build.landings))
    tip = git.branch(build.branch) if tip is None else tip
    if tip is not None:
        head = tip
    else:
        recorded = git.present(build.commits())
        if not recorded:
            return seen.status(GONE, NO_HEAD, f"the branch {build.branch} is gone, and "
                               "no commit on record for the build is still in git")
        head = git.newest(recorded)
        if head is None:
            return seen.status(UNCLEAR, PARTED, f"the branch {build.branch} is gone, and "
                               "the build's commits on record lie on lines that parted: "
                               "no one of them has the others as ancestors")
        if git.is_ancestor(head, target_head):
            merge = git.bringing_merge(head, target_head)
            if merge is not None and merge not in {one.landing for one in every}:
                held = seen.held_by(merge, head)
                if held is None:
                    return seen.octopus(merge, head)
                head = held
    if git.is_ancestor(head, target_head):
        return seen.on_target(head)
    if tip is None:
        return seen.status(GONE, OFF_TARGET, f"the branch {build.branch} is gone, and "
                           f"its last head {head[:12]} is not on {build.target}",
                           head=head)
    bases = git.merge_bases(head, target_head)
    if len(bases) != 1:
        return seen.no_base(bases, build.branch, head)
    base = bases[0]
    if git.tree(head) == git.tree(base):
        own = git.holds_non_merge(target_head, head)
        return seen.status(EMPTY, OFF_TARGET, f"{build.branch} would change nothing on "
                           f"{build.target}" + (", though it holds commits of its own"
                                                if own else ""),
                           head=head, merge_base=base, own_commits=own)
    return seen.status(BUILDING, OFF_TARGET, f"{build.branch} holds work "
                       f"{build.target} does not", head=head, merge_base=base)


class _Seen:
    """What one read of a build has found so far: the build, the repo, the target's
    head, and the landings git still has, the build's `own` and the `others`."""

    def __init__(self, build: Build, git: Git, target_head: str, *,
                 own: tuple[Landing, ...], others: tuple[Landing, ...]):
        self.build, self.git, self.target_head = build, git, target_head
        self.own, self.others = own, others

    def status(self, state: str, by: str, why: str, **more) -> Status:
        more.setdefault("target_before", self.target_head)
        return Status(state, by, why, target_head=self.target_head, **more)

    def held_by(self, merge: str, head: str) -> str | None:
        """The one parent of `merge`, other than the first, that holds `head`, or None
        when not exactly one does."""
        holders = [one for one in self.git.parents(merge)[1:]
                   if self.git.is_ancestor(head, one)]
        return holders[0] if len(holders) == 1 else None

    def octopus(self, merge: str, head: str) -> Status:
        return self.status(UNCLEAR, NO_ANSWER, f"the merge {merge[:12]} that brought "
                           f"{head[:12]} onto {self.build.target} has more than one "
                           "parent besides its first that holds it", head=head)

    def no_base(self, bases: tuple[str, ...], branch: str, head: str) -> Status:
        said = "no merge base" if not bases else f"{len(bases)} merge bases"
        return self.status(UNCLEAR, NO_ANSWER, f"git gives {said} for {branch} and "
                           f"{self.build.target}", head=head)

    def landed_under(self, commit: str, landing: Landing) -> bool:
        return (self.git.is_ancestor(commit, landing.landing)
                and not self.git.is_ancestor(commit, landing.target_before))

    def on_target(self, head: str) -> Status:
        git, build, target = self.git, self.build, self.build.target
        if self.own:
            under = [one for one in self.own if self.landed_under(head, one)]
            if len(self.own) > 1 and len(under) != 1:
                return self.status(UNCLEAR, NO_LANDING, f"the build has {len(self.own)} "
                                   f"landings, and its head {head[:12]} landed under "
                                   f"{len(under) or 'none'} of them", head=head)
            landing = under[0] if len(self.own) > 1 else self.own[0]
            if git.is_ancestor(landing.tip, head):
                return self.status(MERGED, OWN_LANDING, f"its finish landed it on {target} "
                                   f"at {landing.landing[:12]}", head=head,
                                   target_before=landing.target_before)
        base = build.base
        if not (git.present((base,)) and git.is_ancestor(base, self.target_head)):
            return self.status(UNCLEAR, NO_ANSWER, f"its base {base[:12]} is no longer on "
                               f"{target}: the target was rewritten", head=head)
        if git.is_ancestor(head, base):
            return self.status(EMPTY, NOTHING_OWN, f"all of {build.branch} was on "
                               f"{target} at its commission", head=head)
        merge = git.bringing_merge(head, self.target_head)
        if merge is not None:
            return self._merged_in(head, merge)
        return self._on_the_line(head)

    def _merged_in(self, head: str, merge: str) -> Status:
        git, target = self.git, self.build.target
        if merge in {one.landing for one in self.others}:
            return self.status(EMPTY, NOTHING_OWN, f"{head[:12]} came onto {target} by "
                               f"another build's finish, at {merge[:12]}", head=head)
        if self.held_by(merge, head) is None:
            return self.octopus(merge, head)
        first = git.parents(merge)[0]
        bases = git.merge_bases(head, first)
        if len(bases) != 1:
            said = "no merge base" if not bases else f"{len(bases)} merge bases"
            return self.status(UNCLEAR, NO_ANSWER, f"git gives {said} for {head[:12]} and "
                               f"the target before the hand merge {merge[:12]}", head=head)
        if git.tree(head) == git.tree(bases[0]):
            return self.status(EMPTY, HAND_MERGE, f"the hand merge {merge[:12]} brought "
                               f"no change of {self.build.branch} onto {target}",
                               head=head, hand_merge=merge)
        if git.tree(merge) == git.tree(first):
            return self.status(UNCLEAR, HAND_MERGE, f"the hand merge {merge[:12]} left "
                               f"the tree of {target} as it was, so it credits nothing",
                               head=head, hand_merge=merge)
        return self.status(MERGED, HAND_MERGE, f"merged into {target} by hand at "
                           f"{merge[:12]}", head=head, target_before=first,
                           hand_merge=merge)

    def _on_the_line(self, head: str) -> Status:
        git, build, target = self.git, self.build, self.build.target
        brought = {one.landing for one in self.others if not one.fast_forward}
        for one in self.others:
            if one.fast_forward:
                brought.update(git.between(one.target_before, one.landing))
        line = git.between(build.base, head, first_parent=True)
        if all(commit in brought for commit in line):
            return self.status(EMPTY, NOTHING_OWN, f"all {target} gained up to "
                               f"{head[:12]} came by other builds' finishes", head=head)
        readings = [one for one in build.readings
                    if len(git.present((one.commit, one.merge_base or ""))) == 2]
        worked = [one for one in readings
                  if shows_work(git, one) and git.is_ancestor(one.commit, head)
                  and not any(self.landed_under(one.commit, other)
                              for other in self.others)]
        if not worked:
            return self.status(UNCLEAR, FAST_FORWARD, f"{target} was fast-forwarded to "
                               f"{head[:12]}, and no reading of the build says its branch "
                               "held that work first: the commits are on the target "
                               "under no build", head=head)
        candidates = [build.base] + [one.merge_base for one in readings
                                     if one.off_target
                                     and git.is_ancestor(one.merge_base, head)]
        before = git.newest(candidates)
        if before is None:
            return self.status(UNCLEAR, NO_ANSWER, "no one of the target's heads the "
                               "build's readings were taken against has the others as "
                               "ancestors", head=head)
        return self.status(MERGED, FAST_FORWARD, f"{target} was fast-forwarded onto "
                           f"{build.branch}'s work", head=head, target_before=before)


_END_WORD = {DROPPED: "drop", FINISHED: "finish"}


def drop_by(status: Status, shares: Iterable = ()) -> str:
    """Who may drop a build read as `status`, sharing with the builds `shares`: `LEAD`
    for one with no work a drop could lose, else `OPERATOR`."""
    if shares:
        return OPERATOR
    if status.state == GONE or (status.state == EMPTY and not status.own_commits):
        return LEAD
    return OPERATOR


def credit_refusal(git: Git, build: Build, status: Status) -> str | None:
    """Why the operator's credit of a fast-forward is not taken for `build` read as
    `status`, in words; None when it is: the build is `unclear` because its target was
    fast-forwarded with no reading of the build's, and the tree of its head differs
    from the tree of its base."""
    if status.state != UNCLEAR:
        return f"it reads {status.state}, and only an unclear build is credited"
    if status.by != FAST_FORWARD:
        return (f"it is unclear because {status.why}, and only a fast-forward with no "
                "reading is credited")
    if git.tree(status.head) == git.tree(build.base):
        return (f"its head {status.head[:12]} holds the tree of its base "
                f"{build.base[:12]}, so there is no work to credit")
    return None

#: The states whose builds have a range, and so may share.
RANGED = (BUILDING, MERGED)

_Key = TypeVar("_Key", bound=Hashable)


def idle(git: Git, status: Status, *, idle_days: int, now: datetime) -> bool:
    """Whether a build read as `status` is idle at `now`: it reads `building` and the
    commit date of its head is more than `idle_days` days before `now`."""
    if status.state != BUILDING or status.head is None:
        return False
    committed = datetime.fromtimestamp(git.committed_at(status.head), timezone.utc)
    return now - committed > timedelta(days=idle_days)


def span(git: Git, status: Status) -> frozenset[str]:
    """The range of a build read as `status`: the commits reachable from its head `H`
    and not from `P`, the target before its landing. Empty for a build in a state
    other than `building` or `merged`, which has no range."""
    if (status.state not in RANGED or status.head is None
            or status.target_before is None):
        return frozenset()
    return frozenset(git.between(status.target_before, status.head))


def sharing(git: Git, statuses: Mapping[_Key, Status]) -> dict[_Key, tuple[_Key, ...]]:
    """Which builds share commits. `statuses` are builds of one repo and one target, in
    any rounds, each read now and under the key the caller names it by. Gives each key
    the keys of the other builds whose range has a commit in common with its own,
    sorted, so the answer is the same in whatever order the builds are given; a build
    that shares with none, or has no range, gets none."""
    ranges = {key: span(git, status) for key, status in statuses.items()}
    found: dict[_Key, tuple[_Key, ...]] = {}
    for key, own in ranges.items():
        found[key] = tuple(sorted(other for other, theirs in ranges.items()
                                  if other != key and own & theirs))
    return found


__all__ = ["CREDIT", "LEAD", "OPERATOR", "DROPPED", "FINISHED", "GONE", "EMPTY",
           "BUILDING", "UNCLEAR", "MERGED", "ENDED", "NO_HEAD", "OFF_TARGET", "NO_ANSWER", "PARTED", "NO_LANDING",
           "OWN_LANDING", "NOTHING_OWN", "HAND_MERGE", "FAST_FORWARD", "READINGS",
           "TIPS", "MERGE", "LANDED", "Reading", "Landing", "Build", "Status",
           "NoTarget", "RANGED", "builds", "opened", "from_item", "landings", "current",
           "shows_work", "read", "idle", "span", "sharing", "drop_by",
           "credit_refusal"]

"""What `finish` of a stage waits for: its eleven gates, read at the tip `T` being finished.

`unmet(state, stage, seen=...)` gives every gate that does not hold, in order, each with
its number, what is not met and the command that meets it. Nothing here reads git, a
file or the settings: what was read of the build is given as `steps.Seen`, so the
`finish` step's row, the verb and `verdict`'s note of what finish waits for give one
answer.

1. **Verification.** The `verify-build` step is done at `T`, or skipped for the stage,
   or the flow has no such step.
2. **Findings.** No finding of the stage's build is open. Each open one is named.
3. **The check.** The `check` step is done at `T` (the full check, or CI's form apart
   from the target), or skipped, or the repo declares no check.
4. **Questions.** No question holds the stage, in any flow.
5. **Drift.** The drift rule owes no re-check against `P`: not for the build's
   verification, nor for a report step of the user's own read on its own reports. A
   skip of the step a re-check would be a pass of, or a flow without that step, owes
   none: a re-check is that step's pass.
6. **The target.** `P` is an ancestor of `T`, when the repo's `check_on` is `merged` and
   whenever gate 3 is met only by CI's form. With `check_on = "tip"` and gate 3 met by
   the full check, a skip or no check at all, this gate is off. In a queue project
   (`land = "queue"`) it is off too: the build joins the queue rather than landing, and
   the lander's candidate holds the target by construction.
7. **The worktree and the branch.** The worktree, while it exists, holds no change to a
   tracked file and no untracked path, read as the removal rule reads git, so no
   setting of the user's hides one; ignored paths are left to the removal. One made on
   another machine can't be read here and does not hold. The branch, while it exists,
   is at `T`.
8. **Steps of the user's own.** Each is done at `T`, or skipped for the stage.
9. **Work.** The build reads `building` or `merged`.
10. **Sharing.** The build overlaps no other build, and is held by none
    (`builds.order`): of two builds one strictly inside the other, the inner one is
    held while the outer reads merged, until the outer's finish records the landing;
    the outer one is held, unless it reads merged, until the inner's work is on the
    target.
11. **Private files.** No commit reachable from `T` and not from the target's head holds
    a private file of the repo: a `path` the build's event records `brought`, or an
    entry of the repo's `private_files` now (`private_files.held`, read into `Seen`).

Gates 9, 10 and 11 are not steps, so no skip meets them, and nothing here reads a skip of
`finish`: the operator can't skip it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from . import builds, checking, findings, flows, private_files, report_block, skips
from .settings import LAND_QUEUE
from . import state as states
from .errors import Unmet

VERIFIED = 1
FINDINGS = 2
CHECKED = 3
QUESTIONS = 4
DRIFT = 5
TARGET = 6
WORKTREE = 7
USER_STEPS = 8
WORK = 9
SHARING = 10
PRIVATE = 11
#: The gates, by number, each with a word for it.
GATES = {VERIFIED: "verification", FINDINGS: "findings", CHECKED: "check",
         QUESTIONS: "questions", DRIFT: "drift", TARGET: "target",
         WORKTREE: "worktree", USER_STEPS: "user steps", WORK: "work",
         SHARING: "sharing", PRIVATE: "private files"}

#: `check_on`: the check passed on the tip merged with the target, or on the tip alone.
MERGED = "merged"
TIP = "tip"

#: What `Seen.tree` says of a build's worktree.
CLEAN = "clean"
CHANGED = "changed"
ELSEWHERE = "elsewhere"


@dataclass(frozen=True)
class Gate:
    """One gate of `finish` that does not hold: its `number` (1 to 11) and what is not
    met, with the command that meets it."""

    number: int
    unmet: Unmet

    @property
    def word(self) -> str:
        return GATES[self.number]


def _short(commit: str | None) -> str:
    return "none" if commit is None else commit[:12]


def _skip(name: str, step: str, stage: str) -> str:
    return (f"the operator's skip: moxi rounds rule {name} {skips.item(step, stage)} "
            "--record PTR")


def _why(found) -> str:
    said = [one.reason for one in found.waits] + list(found.owes)
    return f": {'; '.join(said)}" if said else ""


def unmet(state: states.State, stage: str, *, seen, tip: str | None = None,
          name: str = "ROUND", on_closed: Callable[[str], bool] | None = None,
          register: states.State | None = None) -> tuple[Gate, ...]:
    """Every gate of `finish` of the stage `stage` that does not hold at `tip`, in
    order. `seen` is what was read of the stage's open build (`steps.Seen`); `tip` is
    the tip the gates are read at, the branch's tip as read when left out, else the
    build's head. `name`, `on_closed` and `register` are as `steps.of` takes them.

    The stage is one with an open build; `seen` holds no problem."""
    from . import steps

    build = builds.current(state, stage)
    at = tip or seen.tip or seen.head
    every = {"name": name, "on_closed": on_closed, "register": register,
             "seen": {stage: seen}}
    found: list[Gate] = []

    def add(number: int, reason: str, fix: str) -> None:
        found.append(Gate(number, Unmet(reason, fix)))

    verifying = steps.one(state, findings.VERIFY_BUILD, stage, **every)
    if verifying is not None and not verifying.settled:
        add(VERIFIED, f"the verification of {stage} does not count at {_short(at)}"
            f"{_why(verifying)}",
            f"a publishable build report read at the tip: moxi rounds verdict {name} "
            f"<your report>; or {_skip(name, findings.VERIFY_BUILD, stage)}")

    for one in findings.of_stage(state, stage):
        if one.open:
            add(FINDINGS, f"finding {one.key}: {one.severity}, open: {one.text}",
                f"fix it, and a report sets it fixed: moxi rounds verdict {name} "
                f"<your report>; a minor or a note: moxi rounds mark {name} {one.key} "
                "--fixed --commit SHA --no-behaviour-change")

    checked = steps.one(state, checking.CHECK, stage, **every)
    if checked is not None and not checked.settled:
        add(CHECKED, f"the check of {stage} has not passed at {_short(at)}"
            f"{_why(checked)}",
            f"moxi rounds check {name} {stage}; or {_skip(name, checking.CHECK, stage)}")

    for one in steps.held_by(state, stage, name=name, on_closed=on_closed):
        add(QUESTIONS, one.reason, one.fix)

    for step, moved in _drifts(state, stage, seen):
        ranges = moved.ranges
        reads = ", ".join(f"{side} {span}" for side, span in
                          (("the target's change", ranges["target"]),
                           ("the build's diff", ranges["build"])) if span)
        pass_ = (report_block.DRIFT if step == findings.VERIFY_BUILD
                 else report_block.RECHECK)
        add(DRIFT, f"the target moved under {stage} since the newest report of {step}: "
            f"{moved.why or 'a re-check is owed'}",
            f"merge the target into the branch (git merge --no-ff {build.target} in the "
            f"build's worktree), then a re-check with pass = \"{pass_}\""
            + (f", reading {reads}" if reads else "")
            + f": moxi rounds verdict {name} <your report>; or {_skip(name, step, stage)}")

    only_ci = (checked is not None and checked.state == steps.DONE
               and seen.check is not None and seen.head is not None
               and not checking.passes(state, stage, checking.CHECK, tip=seen.head,
                                       argv=seen.check))
    if (seen.land != LAND_QUEUE and (seen.check_on == MERGED or only_ci)
            and seen.holds_target is not True):
        why = ("the check is met only by CI, which ran on the tip and not on the "
               "target merged with it" if only_ci else
               "the repo's check_on is merged: the check must have run on the tip "
               "merged with the target")
        held = ("can't be read from git" if seen.target_before is None
                else f"as it stands at {_short(seen.target_before)}")
        add(TARGET, f"{stage}'s tip {_short(at)} does not hold the target "
            f"{build.target} {held}; {why}",
            f"merge the target into the branch (git merge --no-ff {build.target} in the "
            f"build's worktree), then moxi rounds check {name} {stage}")

    if seen.tree == CHANGED:
        add(WORKTREE, f"the worktree of {stage} holds a change not committed: a tracked "
            "change or an untracked path",
            "commit or put away what is not committed, in the build's worktree")
    elif seen.tree == ELSEWHERE:
        add(WORKTREE, f"the worktree of {stage} was made on another machine and can't "
            "be read here",
            "finish on the machine that made the worktree")
    if seen.tip is not None and at is not None and seen.tip != at:
        add(WORKTREE, f"the branch {build.branch} is at {_short(seen.tip)}, not at "
            f"{_short(at)}, the tip the gates were read at",
            f"read the gates again at the branch's tip: moxi rounds next {name} {stage}")

    for entry in state.steps:
        if not flows.is_user_step(entry):
            continue
        step = str(entry.get("step"))
        own = steps.one(state, step, stage, **every)
        if own is None or own.settled:
            continue
        meets = (f"moxi rounds check {name} {stage} --step {step}"
                 if entry.get("does") == flows.COMMAND else
                 f"a publishable {step} report read at the tip: moxi rounds verdict "
                 f"{name} <your report>")
        add(USER_STEPS, f"the step {step} of {stage} is not done at {_short(at)}"
            f"{_why(own)}", f"{meets}; or {_skip(name, step, stage)}")

    if seen.state not in checking.WITH_WORK:
        fix = ("commit the build's work on its branch" if seen.state == builds.EMPTY
               else f"moxi rounds show {name} says why")
        add(WORK, f"{stage} has no work to finish: it reads {seen.state}, and only a "
            "build that reads building or merged finishes, under any skip", fix)

    merged = seen.state == builds.MERGED
    overlapping = [one for one in seen.shares
                   if builds.order(one, merged=merged) == builds.REFUSED]
    if overlapping:
        said = ", ".join(f"{one.round} {one.stage}" for one in overlapping)
        add(SHARING, f"{stage} shares commits with {said}; a build finishes only with "
            "work of its own", f"keep each build's work on its own branch, or end the "
            f"other build first: moxi rounds show {overlapping[0].round}")
    for one in seen.shares:
        held = builds.order(one, merged=merged)
        other = f"{one.round} {one.stage}"
        if held == builds.HELD_FOR_LANDING:
            said = (f"{stage} is held until {other}'s finish records the landing that "
                    f"brought {stage}'s work onto the target")
        elif held == builds.HELD_FOR_WORK:
            said = (f"{stage} is held until {other}'s work is on the target, which its "
                    "finish does")
        else:
            continue
        add(SHARING, said, f"finish that build first: moxi rounds next {other}")

    if seen.private:
        reason, fix = private_files.words(seen.private, repo=build.repo,
                                          target=build.target, round_name=name)
        add(PRIVATE, reason, fix)
    return tuple(found)


def _drifts(state: states.State, stage: str, seen):
    """Each step whose newest report owes a drift re-check, with its drift: the
    build's verification, then each report step of the user's own. A step skipped
    for the stage, or one the flow does not have, owes none."""
    if (seen.drift is not None and getattr(seen.drift, "owed", False)
            and flows.has(state.steps, findings.VERIFY_BUILD)
            and not skips.is_skipped(state, findings.VERIFY_BUILD, stage)):
        yield findings.VERIFY_BUILD, seen.drift
    for step in checking.report_steps(state):
        _since, moved = (seen.reported or {}).get(step, (None, None))
        if (moved is not None and getattr(moved, "owed", False)
                and not skips.is_skipped(state, step, stage)):
            yield step, moved


__all__ = ["VERIFIED", "FINDINGS", "CHECKED", "QUESTIONS", "DRIFT", "TARGET",
           "WORKTREE", "USER_STEPS", "WORK", "SHARING", "PRIVATE", "GATES", "MERGED", "TIP",
           "CLEAN", "CHANGED", "ELSEWHERE", "Gate", "unmet"]

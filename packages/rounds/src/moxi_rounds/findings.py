"""The findings of a build: what the reports of its verification found, and the state
each is in now.

A finding is a record of one build, keyed `B2.L1` outside the report that raised it: its
severity, its state, its text, and the step whose report raised it (`verify-build` for a
report of the build). `of_build(state, build)` gives each finding of a build in the order
raised, as the reports recorded for it leave it.

**The newest report decides.** A report of a build lists every finding the build holds as
open, in some state, so a report can't drop one by leaving it out. The newest report that
lists a finding sets its state, its severity and its text. So a finding is `fixed` because
a report that may speak for it said so, not because of where it sits on a page.

- `open`: not fixed at the head the report read.
- `fixed`: fixed.
- `no-action`: the checker judged it needs none.

**The lead's mark.** The lead may mark a `minor` or `note` finding fixed without a
re-check, naming every commit of the fix (`marking.fix`). A mark sets the finding's state
as a report would, and the newest of the reports and marks that speak of a finding
decides. The commits a mark names are the build's lead-marked commits.

**When verification counts at a tip** (`why_not_counted`). The build's verification
counts at tip `T` when its newest build verdict is `publishable`, read at head `V`, and
every commit in `V..T` is named by a lead's mark of the build or is part of a merge of
the target into the branch that the drift rule accepts (`drift`). Otherwise a re-check
at `T` is owed.

**A build's own.** A build is one commission and the records written for its stage while
it was open. A stage commissioned again after a drop is a new build, and the dropped
build's findings are not its findings. Their ids stay taken all the same: a new finding,
waiting item or decision takes an id no earlier report of the stage used (`taken`), so an
old id never names a new thing.

Nothing here reads a file, the config or git.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace

from . import builds, report_block
from . import state as states

#: The step a report of a build is for, and the step its open findings hold.
VERIFY_BUILD = "verify-build"
FINISH = "finish"

#: The event of the lead's mark of a finding.
MARKED = "finding.marked"

#: The lists of a report's event whose ids are taken for the stage.
_LISTS = ("findings", "waits", "decisions")


@dataclass(frozen=True)
class Finding:
    """One finding of a build as the reports leave it: its `stage` and `id`, the
    `severity`, `state` and `text` the newest report listing it gave, the `step` whose
    report raised it, and `raised` and `set_by`, the lines of the report that raised it
    and of the newest that listed it."""

    stage: str
    id: str
    severity: str
    state: str
    text: str
    step: str
    raised: int
    set_by: int

    @property
    def key(self) -> str:
        """Its id outside the report: `B2.L1`."""
        return f"{self.stage}.{self.id}"

    @property
    def open(self) -> bool:
        return self.state == report_block.OPEN

    def data(self) -> dict:
        return {"id": self.key, "severity": self.severity, "state": self.state,
                "text": self.text, "step": self.step, "raised": self.raised,
                "set_by": self.set_by}


def within(state: states.State, build: builds.Build, n: int | None) -> bool:
    """Whether the line `n` was written while `build` was its stage's build: after its
    commission and before its stage's next one."""
    ends = [one.n for one in builds.builds(state)
            if one.stage == build.stage and one.n > build.n]
    until = min(ends, default=None)
    return n is not None and n > build.n and (until is None or n < until)


def verdicts_of(state: states.State, build: builds.Build) -> tuple[states.Verdict, ...]:
    """The verdicts recorded for `build`, oldest first: those of its stage after its
    commission and before its stage's next one."""
    return tuple(one for one in state.verdicts
                 if one.stage == build.stage and within(state, build, one.n))


def marks_of(state: states.State, build: builds.Build) -> tuple[dict, ...]:
    """The lead's marks of findings of `build`, oldest first, as their events hold
    them."""
    prefix = f"{build.stage}."
    return tuple(event for event in state.history
                 if event.get("type") == MARKED and within(state, build, event.get("n"))
                 and str(event.get("id") or "").startswith(prefix))


def step_of(verdict: states.Verdict) -> str:
    """The step a report of a build is for: `verify-build` for a report of the build
    itself, the step's name for a report of a step of the user's own."""
    if verdict.kind == report_block.BUILD:
        return VERIFY_BUILD
    return str(verdict.step if verdict.kind == report_block.STEP else verdict.kind)


def of_build(state: states.State, build: builds.Build) -> tuple[Finding, ...]:
    """Each finding of `build`, in the order raised, as its newest listing or the
    lead's newest mark left it."""
    found: dict[str, Finding] = {}
    marks = {event["n"]: event for event in marks_of(state, build)}
    said = sorted([(one.n, one) for one in verdicts_of(state, build)]
                  + list(marks.items()), key=lambda pair: pair[0])
    for n, what in said:
        if n in marks:
            id_ = str(what["id"]).split(".", 1)[1]
            was = found.get(id_)
            if was is not None:
                found[id_] = replace(was, state=str(what.get("state")), set_by=n)
            continue
        for one in what.findings:
            id_ = one.get("id")
            if not isinstance(id_, str):
                continue
            was = found.get(id_)
            found[id_] = Finding(
                stage=build.stage, id=id_, severity=str(one.get("severity")),
                state=str(one.get("state")), text=str(one.get("text")),
                step=step_of(what) if was is None else was.step,
                raised=what.n if was is None else was.raised, set_by=what.n)
    return tuple(found.values())


def marked_commits(state: states.State, build: builds.Build) -> frozenset[str]:
    """Every commit a lead's mark of a finding of `build` named."""
    return frozenset(one for event in marks_of(state, build)
                     for one in (event.get("commits") or ()) if isinstance(one, str))


def why_not_counted(state: states.State, build: builds.Build, *, tip: str | None,
                    since: tuple[str, ...] | None, covered: Iterable[str] = (),
                    step: str = VERIFY_BUILD) -> str | None:
    """Why the verification of `build` does not count at `tip`, or None when it does;
    with `step` a report step of the user's own, why that step is not done there.

    It counts when the newest verdict on a report of `step` for the build is
    `publishable` and every commit from the head it read to `tip` (`since`, as git gave
    them) is one a lead's mark of the build named or one of `covered`, the commits a
    merge of the target into the branch brought that the drift rule accepts. Each step
    is read on its own reports only. `since` None means git could not give them."""
    listed = [one for one in verdicts_of(state, build) if step_of(one) == step]
    of = build.stage if step == VERIFY_BUILD else f"the step {step} of {build.stage}"
    if not listed:
        return f"no report of {of} is recorded"
    newest = listed[-1]
    if newest.verdict != report_block.PUBLISHABLE:
        return (f"the newest report of {of}, at {str(newest.head)[:7]}, is "
                f"{newest.verdict}: a re-check is owed")
    if tip is None:
        return f"{build.stage} has no tip to read"
    if since is None:
        return (f"the commits from {str(newest.head)[:7]} to {tip[:7]} can't be read "
                "from git: a re-check at the tip is owed")
    known = marked_commits(state, build) | frozenset(covered)
    left = [one for one in since if one not in known]
    if not left:
        return None
    return (f"{len(left)} commit{'' if len(left) == 1 else 's'} since the verdict at "
            f"{str(newest.head)[:7]} {'is' if len(left) == 1 else 'are'} neither named by "
            "a lead's mark nor a merge of the target the drift rule accepts: a re-check "
            f"at {tip[:7]} is owed, reading {newest.head}..{tip}")


def of_stage(state: states.State, stage: str) -> tuple[Finding, ...]:
    """Each finding of the stage's last build; none when it was never commissioned."""
    build = builds.current(state, stage)
    return () if build is None else of_build(state, build)


def taken(state: states.State, stage: str) -> frozenset[str]:
    """Every finding, waiting item and decision id a report of any build of `stage`
    used, a dropped build's included."""
    used = set()
    for event in states.with_rereads(state):
        if event.get("type") != "verdict.recorded":
            continue
        for name in _LISTS:
            listed = event.get(name)
            # A majors report covers several stages, and gives each decision its own.
            used.update(one["id"] for one in (listed if isinstance(listed, list) else ())
                        if isinstance(one, dict) and isinstance(one.get("id"), str)
                        and (one.get("stage") or event.get("stage")) == stage)
    return frozenset(used)


__all__ = ["VERIFY_BUILD", "FINISH", "MARKED", "Finding", "within", "verdicts_of",
           "marks_of", "step_of", "of_build", "of_stage", "marked_commits",
           "why_not_counted", "taken"]

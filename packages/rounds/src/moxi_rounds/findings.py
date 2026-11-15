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

**A build's own.** A build is one commission and the records written for its stage while
it was open. A stage commissioned again after a drop is a new build, and the dropped
build's findings are not its findings. Their ids stay taken all the same: a new finding,
waiting item or decision takes an id no earlier report of the stage used (`taken`), so an
old id never names a new thing.

Nothing here reads a file, the config or git.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import builds, report_block
from . import state as states

#: The step a report of a build is for, and the step its open findings hold.
VERIFY_BUILD = "verify-build"
FINISH = "finish"

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


def verdicts_of(state: states.State, build: builds.Build) -> tuple[states.Verdict, ...]:
    """The verdicts recorded for `build`, oldest first: those of its stage after its
    commission and before its stage's next one."""
    ends = [one.n for one in builds.builds(state)
            if one.stage == build.stage and one.n > build.n]
    until = min(ends, default=None)
    return tuple(one for one in state.verdicts
                 if one.stage == build.stage and one.n is not None and one.n > build.n
                 and (until is None or one.n < until))


def _step_of(verdict: states.Verdict) -> str:
    return VERIFY_BUILD if verdict.kind == report_block.BUILD else str(verdict.kind)


def of_build(state: states.State, build: builds.Build) -> tuple[Finding, ...]:
    """Each finding of `build`, in the order raised, as its newest listing left it."""
    found: dict[str, Finding] = {}
    for verdict in verdicts_of(state, build):
        for one in verdict.findings:
            id_ = one.get("id")
            if not isinstance(id_, str):
                continue
            was = found.get(id_)
            found[id_] = Finding(
                stage=build.stage, id=id_, severity=str(one.get("severity")),
                state=str(one.get("state")), text=str(one.get("text")),
                step=_step_of(verdict) if was is None else was.step,
                raised=verdict.n if was is None else was.raised, set_by=verdict.n)
    return tuple(found.values())


def of_stage(state: states.State, stage: str) -> tuple[Finding, ...]:
    """Each finding of the stage's last build; none when it was never commissioned."""
    build = builds.current(state, stage)
    return () if build is None else of_build(state, build)


def taken(state: states.State, stage: str) -> frozenset[str]:
    """Every finding, waiting item and decision id a report of any build of `stage`
    used, a dropped build's included."""
    used = set()
    for event in state.history:
        if event.get("type") != "verdict.recorded" or event.get("stage") != stage:
            continue
        for name in _LISTS:
            listed = event.get(name)
            used.update(one["id"] for one in (listed if isinstance(listed, list) else ())
                        if isinstance(one, dict) and isinstance(one.get("id"), str))
    return frozenset(used)


__all__ = ["VERIFY_BUILD", "FINISH", "Finding", "verdicts_of", "of_build", "of_stage",
           "taken"]

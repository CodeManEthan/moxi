"""Recording a report's verdict: `verdict`, run by whoever wrote the report.

`record("<project>/<slug>", report, actor=...)` reads the data block of the report file
and appends one event, `verdict.recorded`. That event is the whole of the verdict: it
holds the kind of report, what the pass was, the verdict's word, the version of the
design the pass read, the model and the author the report names, the report's file
and the SHA-256 of its bytes, every finding of its block, and what the verdict takes
out of the record. The report's file is read and never written.

Only a report of a design is recorded here. A report of a build, of a step of the
user's own, of a round's major decisions or a retrospective is refused, since nothing
here records one.

**Against the version it read.** A report names the frozen version its pass read, and
its verdict is recorded for that version, whether or not a later one was frozen since
and whatever the design's file holds now. So nothing that happens during a pass makes
its report unrecordable. A version that was never frozen is refused.

**Once.** A report whose bytes were recorded already is refused: it says nothing new.
The same file with other content, or a copy of it that was edited, is a new report.

**What a publishable verdict applies.** What a version's block took away waits with
that version. A verdict that finds the version publishable takes out of the record
what of it still stands: a question still asked is withdrawn, a question holds up
what the block says, a stage with no build leaves the plan, a stage waits for fewer.
A stage whose build is open or finished stays, and the verdict names it. A verdict
that sends the design back applies nothing: the pass that objects to a removal is not
the thing that grants it.

**Noted, not refused.** One full pass reads a design. A report of a full pass
recorded after another one is recorded all the same, with the flag
`brake.EXTRA_FULL_PASS`. A report whose model the `models` setting places in a weaker
tier than the step names for its pass is recorded with `model_tiers.BELOW_TIER`.

The standing of a design, which its verdicts give, is `versions.standing`.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from . import (brake, ending, folders, model_tiers, questions, report_block, stages,
               stepping, versions, write)
from . import settings as own
from . import state as states
from .errors import BadBlock, NoLog, NoRound, Problem, Refused, Unmet

#: The step a round's flow must have for a pass to verify its design.
VERIFIES = stages.VERIFIES


@dataclass(frozen=True)
class Recorded:
    """A verdict as it was recorded: the round's `name`, the `verdict` as the log now
    holds it, the design's `standing` after it, and the `event` written. `failed`
    pairs each after-append callback that raised with what it raised."""

    name: str
    verdict: states.Verdict
    standing: versions.Standing
    event: dict
    failed: tuple[tuple[Callable, Exception], ...] = ()


@dataclass
class _Read:
    """The report as the verb read it under the lock: its `block`, or the `error`
    that stopped the reading."""

    block: report_block.ReportBlock | None = None
    error: BadBlock | None = None


def applies(state: states.State, n: int, *, name: str = "the round"
            ) -> tuple[tuple[states.Pending, ...], tuple[tuple[states.Pending, str], ...]]:
    """What a verdict that finds version `n` publishable takes out of the record as it
    is now, and what it leaves, each with the reason: of what the version took away
    and still stands, everything but a stage that can't leave the plan."""
    applied, left = [], []
    for one in versions.outstanding(state, n):
        why = (stages.cannot_leave(state, one.id, name=name)
               if one.what == states.STAGE else None)
        if why is None:
            applied.append(one)
        else:
            left.append((one, why))
    return tuple(applied), tuple(left)


def _entry(one: states.Pending) -> dict:
    entry: dict[str, object] = {"what": one.what, "id": one.id}
    if one.gone:
        entry["gone"] = list(one.gone)
    if one.blocks is not None:
        entry["blocks"] = questions.stored(one.blocks)
    return entry


def _path(report: Path, folder: Path) -> str:
    """The report's file as the log names it: from the round's folder when it is in
    it, so the record reads the same wherever the folder is, else the whole path."""
    whole = Path(os.path.abspath(report))
    try:
        return whole.relative_to(Path(os.path.abspath(folder))).as_posix()
    except ValueError:
        return str(whole)


def record(name: str, report: Path | str, *, actor: str, settings=None,
           wait_s: float = write.WAIT_S, at: str | None = None) -> Recorded:
    """Record the verdict of the report file `report` on the round `name`,
    `<project>/<slug>`.

    - `actor`: who records it, the event's actor.
    - `settings`: rounds' settings as `settings.load()` gives them, read now when left
      out.
    - `wait_s`: how long to wait for the round's lock; `at`: the event's time.

    Raises `BadName` for a name that can't be used, `UnknownProject`, `NoFolder` or
    `NoRound` for a round that can't be found, `BadBlock` when the report's block
    can't be read or names a version of the design that was never frozen, and
    `Refused` when the round has ended, the report is of a kind that is not recorded
    here, the round's flow has no step that verifies its design, or a report with
    these bytes was recorded already. A refusal leaves the log as it was.
    """
    project, slug = folders.split(name)
    settings = own.load() if settings is None else settings
    home = folders.home(project, settings)
    name = f"{home.project}/{slug}"
    folder = home.round_folder(slug)
    report = Path(report)
    shown = f"moxi rounds show {name}"

    def read(where) -> _Read:
        try:
            return _Read(block=report_block.read(report))
        except BadBlock as error:
            return _Read(error=error)

    def recorded(view, result):
        if view.facts.error is not None:
            raise view.facts.error
        block = view.facts.block
        state = view.state
        if block.kind != report_block.DESIGN:
            raise Refused([Unmet(
                f"{report} is a {block.kind} report, and only a report of a design "
                "is recorded: nothing records this one yet", shown)])
        unmet = []
        if not stepping.has_step(state, VERIFIES):
            flow = stepping.current(state).name
            unmet.append(Unmet(
                f"the flow {flow} of {name} has no {VERIFIES} step, so no pass "
                "verifies its design; a flow that has one is the lead's to move it to",
                f"moxi rounds mark {name} --flow NAME"))
        if versions.find(state, block.design) is None:
            have = versions.current(state)
            frozen = ("no version of the design was frozen" if have is None else
                      f"the versions frozen are 1 to {have.n}" if have.n > 1 else
                      "the one version frozen is 1")
            raise BadBlock(block.file, report_block.WHAT, [Problem(
                block.line_of("design"),
                f"the report read version {block.design} of the design of {name}, "
                f"which was never frozen: {frozen}",
                "give design the number of the frozen version the pass read")])
        again = next((one for one in state.verdicts if one.sha256 == block.sha256), None)
        if again is not None:
            unmet.append(Unmet(
                f"a report with these bytes was recorded already, at {again.at} by "
                f"{again.actor} as {again.report}: its verdict is in the record and "
                "is not recorded twice", shown))
        if unmet:
            raise Refused(unmet)
        fields: dict[str, object] = {
            "kind": block.kind, "pass": block.pass_, "verdict": block.verdict,
            "design": block.design, "model": block.model, "by": block.by,
            "report": _path(report, folder), "sha256": block.sha256,
            "findings": [{"id": one.id, "severity": one.severity, "state": one.state,
                          "text": one.text} for one in block.findings]}
        if block.verdict == report_block.PUBLISHABLE:
            applied, left = applies(state, block.design, name=name)
            fields["applied"] = [_entry(one) for one in applied]
            fields["not_applied"] = [{**_entry(one), "why": why} for one, why in left]
        noted = brake.flags(state, block.pass_) + model_tiers.flags(
            settings["models"], state.steps, VERIFIES, pass_=block.pass_,
            model=block.model)
        if noted:
            fields["flags"] = noted
        return write.Entry("verdict.recorded", fields)

    try:
        written = write.record(
            write.Where(home.project, home.folder, slug), actor=actor,
            gates=[ending.takes_new_work(name, "a report's verdict")],
            event=recorded, facts=read, wait_s=wait_s, at=at)
    except NoLog:
        raise NoRound(name) from None
    after = states.fold(written.round_events)
    return Recorded(name=name, verdict=after.verdicts[-1],
                    standing=versions.standing(after), event=written.events[0],
                    failed=written.failed)


__all__ = ["VERIFIES", "Recorded", "applies", "record"]

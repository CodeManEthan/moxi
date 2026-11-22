"""Recording a report's verdict: `verdict`, run by whoever wrote the report.

`record("<project>/<slug>", report, actor=...)` reads the data block of the report file
and appends one event, `verdict.recorded`. That event is the whole of the verdict: it
holds the kind of report, what the pass was, the verdict's word, the version of the
design the pass read, the model and the author the report names, the report's file
and the SHA-256 of its bytes, every finding of its block, and what the verdict takes
out of the record. The report's file is read and never written.

A report of a design, of a build, of a step of the user's own, of a round's major
decisions or a retrospective is recorded here.

**A retro report** (`kind = "retro"`) is the round's written retrospective: it carries
no verdict word and no lists, and its event holds who wrote it, the model, and the
report's path and hash. It is refused in a flow with no `retro` step, which gets the
numbers only. Recorded, it makes the round's `retro` step done, and the round closes
on it once nothing else is waited for.

**A step report** (`kind = "step"`) is for a report step of the user's own that the
round's flow has, and reads one build as a report of the build does, by the same
rules of head and author, and is recorded under its step's name. Its findings are
findings of the build, each kept with the step that raised it. It lists every open
finding its own step's reports raised and no finding any other report raised: it sets
its own step's findings only. A report of the build lists and sets every open finding,
a step's included.

**A majors report** declares the decisions no build report will carry, each with its
stage, and names in `stages` the builds it covers, so it can say a build has none. Each
stage it names has a build, and its author built none of the stages it names: a
builder of another stage of the round may write it.

**A report of a build** reads one build, the stage's open one, at a head that is the
tip of its branch or an ancestor of it. The verdict is recorded against the head it
read, even when the branch moved since: whether later commits are covered is the
finish's question, so a fix committed before the verdict is recorded does not make the
report unrecordable. The event holds that head and its merge-base with the target as
it was before the build reached it (the target's head while the build is off it),
None when git gives none or several: a reading of the build. A report by the build's
builder is refused; the name is the report's own word, so this catches a mistake, not
bad faith. The report lists every finding the build holds as open, in some state, and
a finding new to the build takes an id no earlier report of the stage used
(`findings`). Each waiting item the report lists is an open waiting item of the round
from the moment it is recorded, and takes an id no earlier report of the stage used;
a later report of the same build may list an item again with the sentence on record,
and it is the same item (`waits`). Each decision it lists is a record of the round in a
question's basic form, and stays on it: a decision listed again keeps its sentence and
may be as heavy or heavier, never lighter (`decisions`). A report whose model the
`models` setting places in a weaker tier than the `verify-build` step names for its
pass is recorded with `model_tiers.BELOW_TIER`.

**Against the version it read.** A report names the frozen version its pass read, and
its verdict is recorded for that version, whether or not a later one was frozen since
and whatever the design's file holds now. So nothing that happens during a pass makes
its report unrecordable. A version that was never frozen is refused.

**Once.** A report whose bytes were recorded already is refused: it says nothing new.
The same file with other content, or a copy of it that was edited, is a new report.

**What a publishable verdict applies.** What a version's block took away waits with
that version. A verdict that finds the version publishable takes out of the record
what of it still stands: a question still asked is withdrawn, a hold loses the
stages the block took off it, a stage with no build leaves the plan, a stage waits
for fewer. A stage whose build is open or finished stays, and the verdict names it.
Only the stages the version took off a hold leave it, so a stage the hold gained
since stays held. Where a later version is the current one, the verdict applies only
what that version's block still makes: a question or a stage it declares again, a
wait it has again and a hold it widened again are left, and the verdict names each
with the reason. A verdict
that sends the design back applies nothing: the pass that objects to a removal is not
the thing that grants it.

**The trace check.** A design report is refused on a version the check of its trace
rows did not run on, and a publishable one on a version that fails it; a fold-first
report on a version that fails it is recorded (`tracing.outcome`). A version frozen
where the check was not asked for refuses nothing.

**Noted, not refused.** One full pass reads a design. A report of a full pass
recorded after another one is recorded all the same, with the flag
`brake.EXTRA_FULL_PASS`. A report whose model the `models` setting places in a weaker
tier than the step names for its pass is recorded with `model_tiers.BELOW_TIER`.

The standing of a design, which its verdicts give, is `versions.standing`.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path

from . import (brake, builds, checking, decisions, ending, findings, finish_gates, flows,
               folders, model_tiers, open_builds, places, questions, report_block, stages,
               stepping, tracing, versions, waits, write)
from . import settings as own
from . import state as states
from .errors import BadBlock, NoLog, NoRound, Problem, Refused, RoundsError, Unmet
from .gitreads import Git

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
    #: For a report of a build: each finding of the build after it, and what the
    #: build's finish still waits for, a line each.
    findings: tuple[findings.Finding, ...] = ()
    waits: tuple[str, ...] = ()
    #: For a report of a build: the waiting items it declared, open from now on.
    opened: tuple[waits.Wait, ...] = ()
    #: For a report of a build or of majors: each decision it lists, as the record
    #: now holds it.
    decisions: tuple[decisions.Decision, ...] = ()
    #: Of those not yet ruled, by key, the line said under each: whether it is his to
    #: rule and the command that records his ruling (`decisions.open_line`).
    open_as: Mapping[str, str] = field(default_factory=dict)


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
    and still stands, everything but what the current version's block put back and a
    stage that can't leave the plan."""
    applied, left = [], []
    stands = {(one.what, one.id): one for one in versions.outstanding(state, n)}
    finished = questions.finished_stages(state) - questions.left_stages(state)
    for taken in versions.pending_removals(state, n):
        one = stands.get((taken.what, taken.id))
        if one is None:
            if taken.what == states.STAGE and taken.id in finished:
                # Its removal changes nothing any more, and the verdict says why it
                # stays, as it does of a stage with an open build.
                left.append((taken, stages.cannot_leave(state, taken.id, name=name)))
            continue
        kept, back, why = _still_made(state, one, n)
        if back is not None:
            left.append((back, why))
        if kept is None:
            continue
        why = (stages.cannot_leave(state, kept.id, name=name)
               if kept.what == states.STAGE else None)
        if why is None:
            applied.append(kept)
        else:
            left.append((kept, why))
    return tuple(applied), tuple(left)


def _still_made(state: states.State, one: states.Pending, n: int
                ) -> tuple[states.Pending | None, states.Pending | None, str]:
    """`one`, which version `n` took away, as the part of it the current version's
    block still makes, the part that block put back, and why that part is left. The
    pass read version `n`: what the design has declared again since is not what it
    found publishable."""
    now = versions.current(state)
    if now is None or now.n == n:
        return one, None, ""
    said = {kind: {entry.get("id"): entry for entry in now.declared.get(kind) or ()}
            for kind in (states.QUESTION, states.STAGE)}
    since = f"version {now.n} of the design"
    stays = f"a pass that read version {n} does not take out what the design says now"
    if one.what == states.QUESTION:
        if one.id in said[states.QUESTION]:
            return None, one, f"{since} asks {one.id} again, and {stays}"
    elif one.what == states.STAGE:
        if one.id in said[states.STAGE]:
            return None, one, f"{since} plans {one.id} again, and {stays}"
    elif one.what == states.AFTER:
        waits = (said[states.STAGE].get(one.id) or {}).get("after") or ()
        back = tuple(stage for stage in one.gone if stage in waits)
        if back:
            rest = tuple(stage for stage in one.gone if stage not in waits)
            return (replace(one, gone=rest) if rest else None, replace(one, gone=back),
                    f"{since} has {one.id} wait for {', '.join(back)} again, and "
                    f"{stays}")
    else:
        asked = said[states.QUESTION].get(one.id)
        then = next((entry for entry in
                     versions.declarations(state, n).get(states.QUESTION) or ()
                     if entry.get("id") == one.id), None)
        if asked is not None and then is not None and not questions.holds_every(
                _hold(then), _hold(asked)):
            return None, one, (f"{since} has {one.id} hold up more than version {n} "
                               f"did, and {stays}")
    return one, None, ""


def _hold(entry: dict) -> str | tuple[str, ...]:
    """What a block's entry for a question says it holds up."""
    blocks = entry.get("blocks", questions.ALL)
    return blocks if isinstance(blocks, str) else tuple(blocks)


def _entry(one: states.Pending) -> dict:
    entry: dict[str, object] = {"what": one.what, "id": one.id}
    if one.gone:
        entry["gone"] = list(one.gone)
    if one.blocks is not None:
        entry["blocks"] = questions.stored(one.blocks)
    return entry


def path(report: Path, round_dir: Path) -> str:
    """The report's file as the log names it: from the round's folder when it is in
    it, so the record reads the same wherever the folder is, else the whole path, under
    the home folder as `places.contract_home` writes it."""
    whole = Path(os.path.abspath(report))
    try:
        return whole.relative_to(Path(os.path.abspath(round_dir))).as_posix()
    except ValueError:
        return places.contract_home(whole)


def _again(state: states.State, block: report_block.ReportBlock,
           shown: str) -> list[Unmet]:
    """The refusal of a report whose bytes were recorded already: it says nothing new."""
    again = next((one for one in state.verdicts if one.sha256 == block.sha256), None)
    if again is None:
        return []
    return [Unmet(f"a report with these bytes was recorded already, at {again.at} by "
                  f"{again.actor} as {again.report}: its verdict is in the record and "
                  "is not recorded twice", shown)]


def _of_build(state: states.State, block: report_block.ReportBlock, *, name: str,
              settings, report: str) -> dict:
    """The fields of the verdict on a report of a build, or of a step of the user's own
    for a build, read from git now; refused with every unmet rule when it can't be
    recorded."""
    shown = f"moxi rounds show {name}"
    stage = block.stage
    build = builds.current(state, stage)
    if build is None or build.ended is not None:
        how = ("was never commissioned" if build is None
               else f"has no open build: its last one is {build.ended}")
        raise Refused([Unmet(f"{stage} of {name} {how}, so there is no build for the "
                             "report to have read", shown)])
    unmet = []
    # A report of a step of the user's own speaks for its own step: it lists the
    # findings that step's reports raised, and only those.
    step = block.step if block.kind == report_block.STEP else findings.VERIFY_BUILD
    if step != findings.VERIFY_BUILD and step not in checking.report_steps(state):
        flow = stepping.current(state).name
        unmet.append(Unmet(
            f"the report is for the step {step}, and the flow {flow} of {name} has no "
            f'step {step} with does = "{flows.REPORT}": nothing would read its verdict',
            f"give step the name of a report step of the flow ({shown})"))
    builder = checking.commission_of(state, build).get("builder")
    if block.by == builder:
        unmet.append(Unmet(
            f"the report is by {block.by}, who built {stage}: a builder's own report "
            "does not verify the build", "have a checker who did not build it read "
            "the build and write the report"))
    held = findings.of_build(state, build)
    listed = {one.id for one in block.findings}
    for one in held:
        if one.open and one.id not in listed and (
                step == findings.VERIFY_BUILD or one.step == step):
            unmet.append(Unmet(
                f"the finding {one.key} is open on {stage}'s record and the report "
                "does not list it: a report can't drop a finding by leaving it out",
                f'list {one.id} with the state it has at the head the pass read: '
                f'"{report_block.OPEN}", "{report_block.FIXED}" or '
                f'"{report_block.NO_ACTION}"'))
        elif one.id in listed and step != findings.VERIFY_BUILD and one.step != step:
            unmet.append(Unmet(
                f"the finding {one.key} was raised by a report of {one.step}, and this "
                f"report of the step {step} lists it: a step's report sets only the "
                "findings its own step raised", f"leave {one.id} out; a report of "
                f"{one.step}, or the build's verification, sets its state"))
    own_ids = {one.id for one in held}
    used = findings.taken(state, stage) - own_ids
    for one in block.findings:
        if one.id in used:
            unmet.append(Unmet(
                f"the finding {one.id} is new to {stage}'s build, and an earlier report "
                f"of {stage} used the id {one.id}: an old id never names a new finding",
                "give it an id no report of the stage used"))
    # A report restates its lists in full, so an item an earlier report of this build
    # declared may be listed again; its id then names that item and no other.
    declared = {one.id: one for one in waits.of_build(state, build)}
    for one in block.waits:
        was = declared.get(one.id)
        if was is not None:
            if was.text != one.text:
                unmet.append(Unmet(
                    f"the waiting item {was.key} is on record as {was.text!r}, from "
                    f"{was.report}, and the report lists {one.id} as {one.text!r}: an "
                    "id names one item, and a new item takes a new id",
                    f"list {one.id} with the sentence on record, or give the new item "
                    "an id no report of the stage used"))
            continue
        if one.id in used:
            unmet.append(Unmet(
                f"the waiting item {one.id} is new, and an earlier report of {stage} "
                f"used the id {one.id}: an old id never names a new waiting item, and "
                "one on record stays open until it is marked done",
                f"give it an id no report of the stage used; a waiting item on record "
                f"is marked done with {waits.mark_line(name, f'{stage}.{one.id}')}"))
    unmet += _decisions_unmet(state, [(stage, one) for one in block.decisions])
    unmet += _again(state, block, shown)
    merge_base = None
    try:
        _key, root = folders.root(build.repo)
        git = Git(root)
        now = open_builds.of_round(name, [build.repo], settings=settings).get(stage)
        if now is None or now.build.n != build.n or now.status is None:
            why = (now.problem.reason if now is not None and now.problem is not None
                   else "its state can't be read")
            unmet.append(Unmet(f"{stage} can't be read from git: {why}", shown))
        else:
            tip = git.branch(build.branch)
            reads = tip if tip is not None else now.status.head
            if reads is None:
                unmet.append(Unmet(
                    f"{stage} has no head to read: its branch {build.branch} is gone "
                    "and git holds no commit of it", shown))
            elif not git.present([block.head]):
                unmet.append(Unmet(
                    f"the report read {block.head[:12]}, which {build.repo} does not "
                    "hold", "give head the whole hash of the commit the pass read"))
            elif block.head != reads and not git.is_ancestor(block.head, reads):
                where = (f"the tip of {build.branch}, {reads[:12]}" if tip is not None
                         else f"{stage}'s head {reads[:12]}")
                unmet.append(Unmet(
                    f"the report read {block.head[:12]}, which is neither {where} nor "
                    "an ancestor of it: it is not a commit of the build",
                    "give head the commit of the build the pass read"))
            else:
                # The target before the build reached it: its head while the build is
                # off it, else what it was just before the merge.
                bases = git.merge_bases(block.head, now.status.target_before)
                merge_base = bases[0] if len(bases) == 1 else None
    except RoundsError as exc:
        unmet.append(Unmet(exc.reason, exc.fix or shown))
    if unmet:
        raise Refused(unmet)
    fields: dict[str, object] = {"kind": block.kind}
    if step != findings.VERIFY_BUILD:
        fields["step"] = step
    fields |= {
        "pass": block.pass_, "verdict": block.verdict,
        "stage": stage, "head": block.head, "merge_base": merge_base,
        "model": block.model, "by": block.by,
        "report": report, "sha256": block.sha256}
    fields |= lists(block)
    noted = model_tiers.flags(settings["models"], state.steps, step,
                              pass_=block.pass_, model=block.model)
    if noted:
        fields["flags"] = noted
    return fields


def _traced(state: states.State, block: report_block.ReportBlock, *,
            name: str) -> Unmet | None:
    """What the check of the trace rows of the version a design report read refuses
    of it: every report where the check did not run on that version, a publishable
    one where it fails. None where it refuses nothing."""
    version = versions.find(state, block.design)
    found = tracing.outcome(version)
    fold = f"moxi rounds fold {name}"
    if found == tracing.NOT_RUN:
        return Unmet(f"{tracing.said(version)}; no report of v{version.n} is recorded, "
                     "since a check that did not run found nothing on the design",
                     f"freeze the design again and verify the new version: {fold}")
    if found == tracing.FAILS and block.verdict == report_block.PUBLISHABLE:
        return Unmet(f"{tracing.said(version)}: a publishable report of a design is "
                     "recorded only on a version that passes the check; a fold-first "
                     "one is recorded",
                     f"give {versions.DESIGN_FILE} the rows, then {fold}")
    return None


def lists(block: report_block.ReportBlock) -> dict[str, list[dict]]:
    """The findings, waiting items and decisions of a report's block as its verdict's
    event holds them, each list its kind carries; a design or build report's findings
    even when it has none, and its waiting items and decisions only when it lists
    some."""
    said: dict[str, list[dict]] = {}
    if block.kind != report_block.MAJORS:
        said["findings"] = [{"id": one.id, "severity": one.severity, "state": one.state,
                             "text": one.text} for one in block.findings]
    if block.waits:
        said["waits"] = [{"id": one.id, "text": one.text} for one in block.waits]
    if block.decisions:
        said["decisions"] = [_decision(one) for one in block.decisions]
    return said


def _decision(one: report_block.Decision) -> dict:
    """A decision of a report's block as its verdict's event holds it."""
    entry = {"id": one.id, "weight": one.weight, "forecloses": one.forecloses,
             "text": one.text}
    if one.stage is not None:
        entry["stage"] = one.stage
    return entry


def _decisions_unmet(state: states.State,
                     declared: list[tuple[str, report_block.Decision]]) -> list[Unmet]:
    """What refuses the decisions a report declares, each with its stage: one on record
    listed with another sentence or lighter, and a new one under an id an earlier
    report of its stage used."""
    unmet = []
    for stage, one in declared:
        key = f"{stage}.{one.id}"
        held = decisions.find(state, key)
        if held is not None and held.text != one.text:
            # An id names one decision: the operator rules the sentence on record, so a
            # new sentence under the same id would be ruled as the old one.
            unmet.append(Unmet(
                f"the decision {key} is on record as {held.text!r}, from {held.origin}, "
                f"and the report lists {one.id} as {one.text!r}: an id names one "
                "decision, and a new decision takes a new id",
                f"list {one.id} with the sentence on record, or give the new decision "
                "an id no report of the stage used"))
        elif held is not None:
            lighter = decisions.lighter(one.weight, forecloses=one.forecloses, than=held)
            if lighter:
                unmet.append(Unmet(
                    f"the decision {key} is on record as {held.weight}"
                    f"{', foreclosing' if held.forecloses else ''}, and the report "
                    f"lists it lighter: {' and '.join(lighter)}. A report can't "
                    "remove or lower a decision",
                    f"list {one.id} with weight {held.weight!r} and forecloses "
                    f"{str(held.forecloses).lower()}, or heavier, or leave it out: "
                    "it stays on record either way"))
        elif one.id in findings.taken(state, stage):
            unmet.append(Unmet(
                f"the decision {one.id} is new to {stage}, and an earlier report of "
                f"{stage} used the id {one.id}: an old id never names a new decision",
                "give it an id no report of the stage used"))
    return unmet


def _of_majors(state: states.State, block: report_block.ReportBlock, *, name: str,
               report: str) -> dict:
    """The fields of the verdict on a majors report: the decisions no build report
    carries, of the builds it names. Refused with every unmet rule when it can't be
    recorded."""
    shown = f"moxi rounds show {name}"
    unmet = []
    builders = {}
    for stage in block.stages:
        build = builds.current(state, stage)
        if build is None:
            unmet.append(Unmet(f"{stage} of {name} was never commissioned, so it has "
                               "no build whose decisions the report could declare",
                               shown))
            continue
        for one in builds.builds(state):
            if one.stage == stage:
                builders[checking.commission_of(state, one).get("builder")] = stage
    if block.by in builders:
        unmet.append(Unmet(
            f"the report is by {block.by}, who built {builders[block.by]}: a majors "
            "report is written by a checker who built none of the stages it names",
            f"have a checker who built none of {', '.join(block.stages)} write it"))
    unmet += _decisions_unmet(state, [(one.stage, one) for one in block.decisions])
    unmet += _again(state, block, shown)
    if unmet:
        raise Refused(unmet)
    return {"kind": block.kind, "model": block.model, "by": block.by,
            "report": report, "sha256": block.sha256, "stages": list(block.stages),
            "decisions": [_decision(one) for one in block.decisions]}


def _of_retro(state: states.State, block: report_block.ReportBlock, *, name: str,
              settings, report: str) -> dict:
    """The fields of the verdict on a retro report: who wrote it, with which model,
    and the report's path and hash. Refused in a flow with no retro step, and for a
    report recorded already."""
    shown = f"moxi rounds show {name}"
    unmet = []
    if not stepping.has_step(state, report_block.RETRO):
        flow = stepping.current(state).name
        unmet.append(Unmet(
            f"the flow {flow} of {name} has no {report_block.RETRO} step, so no "
            "retrospective is recorded on it: the round gets its numbers only; a flow "
            "that has one is the lead's to move it to",
            f"moxi rounds mark {name} --flow NAME"))
    unmet += _again(state, block, shown)
    if unmet:
        raise Refused(unmet)
    fields: dict[str, object] = {"kind": block.kind, "model": block.model,
                                 "by": block.by, "report": report,
                                 "sha256": block.sha256}
    # One pass writes a retrospective, so it is held to the step's own tier.
    noted = model_tiers.flags(settings["models"], state.steps, report_block.RETRO,
                              pass_=report_block.FULL, model=block.model)
    if noted:
        fields["flags"] = noted
    return fields


def record(name: str, report: Path | str, *, by: str, settings=None,
           wait_s: float = write.WAIT_S, at: str | None = None) -> Recorded:
    """Record the verdict of the report file `report` on the round `name`,
    `<project>/<slug>`.

    - `by`: who records it, the event's actor.
    - `settings`: rounds' settings as `settings.load()` gives them, read now when left
      out.
    - `wait_s`: how long to wait for the round's lock; `at`: the event's time.

    Raises `BadName` for a name that can't be used, `UnknownProject`, `NoFolder` or
    `NoRound` for a round that can't be found, `BadBlock` when the report's block
    can't be read or names a version of the design that was never frozen, and
    `Refused` when the round has ended, the round's flow has no step that verifies
    its design (a design report) or no retro step (a retro report), a report with
    these bytes was recorded already, or a report of a build breaks a rule above. A
    refusal
    leaves the log as it was.
    """
    project, slug = folders.split(name)
    settings = own.load() if settings is None else settings
    home = folders.home(project, settings)
    name = f"{home.project}/{slug}"
    round_dir = home.round_dir(slug)
    report = Path(report)
    shown = f"moxi rounds show {name}"

    def read(where) -> _Read:
        try:
            return _Read(block=report_block.read(report))
        except BadBlock as exc:
            return _Read(error=exc)

    def recorded(view, result):
        if view.facts.error is not None:
            raise view.facts.error
        block = view.facts.block
        state = view.state
        if block.kind in (report_block.BUILD, report_block.STEP):
            return write.Entry("verdict.recorded", _of_build(
                state, block, name=name, settings=settings,
                report=path(report, round_dir)))
        if block.kind == report_block.MAJORS:
            return write.Entry("verdict.recorded", _of_majors(
                state, block, name=name, report=path(report, round_dir)))
        if block.kind == report_block.RETRO:
            return write.Entry("verdict.recorded", _of_retro(
                state, block, name=name, settings=settings,
                report=path(report, round_dir)))
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
        unmet += _again(state, block, shown)
        traced = _traced(state, block, name=name)
        if traced is not None:
            unmet.append(traced)
        if unmet:
            raise Refused(unmet)
        fields: dict[str, object] = {
            "kind": block.kind, "pass": block.pass_, "verdict": block.verdict,
            "design": block.design, "model": block.model, "by": block.by,
            "report": path(report, round_dir), "sha256": block.sha256,
            **lists(block)}
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
            write.Where(home.project, home.rounds_dir, slug), by=by,
            gates=[ending.takes_new_work(name, "a report's verdict")],
            event=recorded, facts=read, wait_s=wait_s, at=at)
    except NoLog:
        raise NoRound(name) from None
    after = states.fold(written.round_events)
    one = after.verdicts[-1]
    held, finish, opened = (), (), ()
    if one.kind in (report_block.BUILD, report_block.STEP):
        held = findings.of_stage(after, one.stage)
        finish = finish_waits(after, one.stage, name=name, settings=settings)
        opened = tuple(wait for wait in waits.of_round(after) if wait.raised == one.n)
    declared = tuple(decision for decision in decisions.of_round(after)
                     if decision.key in {f"{stage}.{entry['id']}" for stage, entry in
                                         decisions.listed(written.events[0])})
    why = decisions.why_not(after, str(own.round_setting(settings, home.project,
                                                         "ratify")))
    open_as = {decision.key: decisions.open_line(name, decision.key,
                                                 why=why[decision.key], ended=False)
               for decision in declared if decision.key in why}
    return Recorded(name=name, verdict=one, standing=versions.standing(after),
                    event=written.events[0], failed=written.failed, findings=held,
                    waits=finish, opened=opened, decisions=declared, open_as=open_as)


def finish_waits(state: states.State, stage: str, *, name: str,
                 settings) -> tuple[str, ...]:
    """What the finish of `stage` still waits for, a line each: every gate of
    `finish_gates` that does not hold at the build's tip, with its number, or what
    kept the build from being read. Empty once every gate holds. Reads git."""
    from . import steps

    seen = checking.seen_of(state, name, settings=settings)
    found = steps.one(state, findings.FINISH, stage, name=name, seen=seen)
    if found is None or found.settled:
        return ()
    if not found.gates:
        return tuple(one.reason for one in found.waits)
    return tuple(f"gate {number}, {finish_gates.GATES[number]}: {one.reason}"
                 for number, one in zip(found.gates, found.waits))


__all__ = ["VERIFIES", "Recorded", "applies", "finish_waits", "lists", "path", "record"]

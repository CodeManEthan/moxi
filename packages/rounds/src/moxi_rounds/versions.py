"""The frozen versions of a round's design, as its log holds them.

A design is frozen into `versions/design-v<N>.md` in the round's folder, `N` from 1, and
the log holds each version: its number, the SHA-256 of the frozen file, what the design's
block declared, and what the block took away. The functions here read those records
from a round's folded state:

- `current(state)`, `find(state, n)`: the newest version, and the one numbered `n`;
- `digest(state, n=None)`: a version's digest, the current one's when no number is given;
- `declarations(state, n=None)`: what a version's block declared;
- `pending_removals(state, n)`: what version `n` took away, `outstanding(state, n)`
  what of that would still change the record, and `said(pending)` one of those in a
  line;
- `standing(state)`: what the design's verification stands at for its current
  version, and `verified(state)` the newest version a pass read;
- `unchanged(state, folder, name)`: the condition that `design.md` is as it was when the
  current version was frozen, for a gate that needs the design verified.

What a block takes away (a question, a stage, a stage another waited for, a stage a
question held up) changes nothing when it is frozen. It is kept with the version that
carries it, and is applied by a verdict that finds that version publishable or by the
operator's ruling.

**The standing.** A design's standing is what its verification stands at for its
current version: `none`, `publishable@v<N>`, `fold-first@v<N>` or `lead-decision@v<N>`.
It is read from the log in order, and the newest of these that speaks of the current
version gives it:

- a verdict recorded for that version gives its word;
- freezing the design again leaves it at `none`, until a pass reads the new version;
- freezing it as a change of records only gives the new version what the version
  before stood at then: `publishable` and `lead-decision` are carried, `fold-first`
  becomes `lead-decision` (the freezing is the lead's decision that what the pass
  found were records), and `none` stays `none`;
- a verdict that finds an earlier version publishable also stands for every later
  version reached from it by changes of records only, whenever it is recorded;
- the lead's decision on a thread gives `lead-decision` at the version current then.

A verdict recorded for an earlier version otherwise stays in the record and is not
the standing.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from dataclasses import dataclass, replace

from . import questions, report_block
from . import state as states
from .errors import Unmet

#: The file a round's design is written in, in the round's folder.
DESIGN_FILE = "design.md"


def file(folder: Path | str, n: int) -> Path:
    """Where version `n` of the design of the round in `folder` is frozen."""
    return Path(folder) / states.FROZEN_FILE.format(n=n)


def current(state: states.State) -> states.Version | None:
    """The newest frozen version, or None for a design never frozen."""
    return state.versions[-1] if state.versions else None


def find(state: states.State, n: int) -> states.Version | None:
    """The version numbered `n`, or None when no such version was frozen."""
    for version in state.versions:
        if version.n == n:
            return version
    return None


def _one(state: states.State, n: int | None) -> states.Version | None:
    return current(state) if n is None else find(state, n)


def digest(state: states.State, n: int | None = None) -> str | None:
    """The SHA-256 of the frozen file of version `n`, the current version when `n` is
    left out; None when there is no such version."""
    version = _one(state, n)
    return None if version is None else version.digest


def declarations(state: states.State, n: int | None = None) -> dict:
    """What the block of version `n` declared, the current version's when `n` is left
    out: `question`, `stage`, `trace` and `commitment`, each a list of entries as the
    block gave them. Empty when there is no such version."""
    version = _one(state, n)
    return {} if version is None else version.declared


def pending_removals(state: states.State, n: int) -> tuple[states.Pending, ...]:
    """What version `n` took away, as it was found when the version was frozen: each
    question and stage its block left out, each stage one of its stages waits for no
    longer, and each question whose block entry holds up fewer stages than the record.
    None of it is applied by the freezing. Whether each still stands when it comes to
    be applied (a question since ruled, a stage since built) is for whoever applies
    it to read from the record as it is then."""
    version = find(state, n)
    return () if version is None else version.pending


def outstanding(state: states.State, n: int) -> tuple[states.Pending, ...]:
    """What version `n` took away that would still change the record as it is now: a
    question still asked, a stage still in the plan, the stages a stage still waits
    for, and a hold that is still wider than the block's. The rest was applied
    already, by a verdict or a ruling, or was overtaken (a question since ruled)."""
    asked = (questions.OPEN, questions.PARKED)
    left = questions.left_stages(state) | questions.finished_stages(state)
    found = []
    for one in pending_removals(state, n):
        question = questions.find(state, one.id)
        if one.what == states.QUESTION:
            if question is not None and question.state in asked:
                found.append(one)
        elif one.what == states.BLOCKS:
            if (question is not None and question.state in asked
                    and one.blocks is not None
                    and not questions.holds_every(one.blocks, question.blocks)):
                found.append(one)
        elif one.what == states.STAGE:
            if one.id in questions.recorded_stages(state) and one.id not in left:
                found.append(one)
        elif one.what == states.AFTER:
            waits = next((stage.after for stage in state.stages if stage.id == one.id),
                         ())
            still = tuple(stage for stage in one.gone if stage in waits)
            if still:
                found.append(states.Pending(states.AFTER, one.id, gone=still))
    return tuple(found)


# -- the standing ------------------------------------------------------------------------

#: The kind of report whose verdict is of a design.
DESIGN_REPORT = "design"

#: The standing of a design no verdict was recorded for at its current version.
NONE = "none"


#: The standing the lead's own decision gives: a thread decided, or a change of
#: records only frozen after a verdict that sent the design back.
LEAD_DECISION = "lead-decision"

#: The standings at which a design counts as verified.
VERIFIED = (report_block.PUBLISHABLE, LEAD_DECISION)


@dataclass(frozen=True)
class Standing:
    """What a design's verification stands at: `word` is `NONE`, a verdict's word or
    `LEAD_DECISION`, and `version` the current version's number (None for a design
    never frozen). What gives it: `verdict`, the verdict whose word it is, or the one
    that sent the design back before the lead decided by a change of records only;
    `decision`, the lead's decision on a thread; `fold`, the number of the version
    whose freezing as a change of records only made it the lead's decision."""

    word: str
    version: int | None = None
    verdict: states.Verdict | None = None
    decision: states.Decided | None = None
    fold: int | None = None

    def __str__(self) -> str:
        return self.word if self.word == NONE else f"{self.word}@v{self.version}"


def design_verdicts(state: states.State, n: int | None = None
                    ) -> tuple[states.Verdict, ...]:
    """The verdicts recorded of the design, oldest first; with `n`, the ones that read
    version `n`."""
    return tuple(one for one in state.verdicts if one.kind == DESIGN_REPORT
                 and (n is None or one.design == n))


def verified(state: states.State) -> int | None:
    """The number of the newest version of the design that a recorded verdict read,
    or None when no pass read any."""
    read = [one.design for one in design_verdicts(state)
            if find(state, one.design) is not None]
    return max(read) if read else None


def _carried(was: Standing | None, version: states.Version) -> Standing:
    """What a version frozen as a change of records only stands at, from what the
    version before it stood at then."""
    if was is None or was.word == NONE:
        return Standing(NONE, version.n)
    if was.word in VERIFIED:
        return replace(was, version=version.n)
    return Standing(LEAD_DECISION, version.n, verdict=was.verdict, fold=version.n)


def standing(state: states.State) -> Standing:
    """The standing of the round's design at its current version, `NONE` when nothing
    gives it one or no version was frozen. The module's docstring has the rule."""
    if current(state) is None:
        return Standing(NONE)
    acts = sorted([*((one.event or 0, one) for one in state.versions),
                   *((one.n or 0, one) for one in design_verdicts(state)),
                   *((one.n or 0, one) for one in state.threads)],
                  key=lambda act: act[0])
    stands: dict[int, Standing] = {}
    only: dict[int, bool] = {}
    newest = None
    for _number, one in acts:
        if isinstance(one, states.Version):
            stands[one.n] = (_carried(stands.get(newest), one) if one.records_only
                             else Standing(NONE, one.n))
            only[one.n] = one.records_only
            newest = one.n
        elif isinstance(one, states.Verdict):
            if one.design not in stands:
                continue
            stands[one.design] = Standing(one.verdict or NONE, one.design, one)
            later = one.design + 1
            while (one.verdict == report_block.PUBLISHABLE and only.get(later)):
                stands[later] = Standing(one.verdict, later, one)
                later += 1
        elif newest is not None:
            stands[newest] = Standing(LEAD_DECISION, newest, decision=one)
    return stands[newest]


def said(pending: states.Pending) -> str:
    """One thing a version took away, in a line."""
    if pending.what == states.QUESTION:
        return f"the question {pending.id} is gone from the block"
    if pending.what == states.STAGE:
        return f"the stage {pending.id} is gone from the block"
    if pending.what == states.AFTER:
        return f"{pending.id} waits for {', '.join(pending.gone)} no longer"
    blocks = pending.blocks
    holds = blocks if isinstance(blocks, str) else ", ".join(blocks or ())
    return f"{pending.id} holds up fewer stages: {holds}"


# -- what the index keeps of a round's plan ----------------------------------------------

def stages(state: states.State):
    """Each stage of the round's plan, as `(id, body)`, for the index: what it builds
    on or the round it stands for, and what it waits for. A stage that left the plan
    is not listed."""
    out = questions.left_stages(state)
    for stage in state.stages:
        if stage.id not in out:
            yield stage.id, {"repo": stage.repo, "branch": stage.branch,
                             "round": stage.round, "after": list(stage.after)}


def newest(state: states.State):
    """The current version, as one `(id, body)` for the index, or nothing for a design
    never frozen."""
    version = current(state)
    if version is not None:
        yield f"v{version.n}", {"version": version.n, "digest": version.digest,
                                "at": version.at, "by": version.actor,
                                "records_only": version.records_only,
                                "standing": str(standing(state)),
                                # What the check of its trace rows found when it
                                # was frozen, as the event holds it.
                                "trace": version.trace}


def verdicts(state: states.State):
    """Each verdict recorded of the design, as `(id, body)` for the index: the version
    it read, its word, what the pass was, who wrote the report and with which model,
    the report's file, and what is noted of it."""
    for place, one in enumerate(design_verdicts(state), 1):
        yield f"d{place}", {"version": one.design, "verdict": one.verdict,
                            "pass": one.pass_, "by": one.by, "model": one.model,
                            "report": one.report, "at": one.at,
                            "flags": list(one.flags)}


def threads(state: states.State):
    """Each decision the lead made on a thread of the design's verification, as
    `(id, body)` for the index."""
    for place, one in enumerate(state.threads, 1):
        yield f"t{place}", {"thread": one.thread, "decision": one.decision,
                            "version": one.version, "record": one.record,
                            "by": one.actor, "at": one.at}


def waiting(state: states.State):
    """What the current version took away and still waits for a verdict or a ruling,
    each as `(id, body)` for the index."""
    version = current(state)
    for one in () if version is None else outstanding(state, version.n):
        yield f"v{version.n}:{one.what}:{one.id}", {
            "version": version.n, "what": one.what, "id": one.id,
            "gone": list(one.gone),
            "blocks": list(one.blocks) if isinstance(one.blocks, tuple) else one.blocks,
            "said": said(one)}


def unchanged(state: states.State, folder: Path | str, name: str) -> Unmet | None:
    """The unmet condition of a design that is not the one frozen last, or None when
    `design.md` in `folder`, the folder of the round `name`, has the bytes the current
    version was frozen from."""
    again = f"moxi rounds fold {name}"
    version = current(state)
    if version is None:
        return Unmet(f"the design of {name} was never frozen", again)
    try:
        now = hashlib.sha256((Path(folder) / DESIGN_FILE).read_bytes()).hexdigest()
    except OSError:
        now = None
    if now == version.digest:
        return None
    return Unmet(f"{DESIGN_FILE} changed since v{version.n} was frozen", again)


__all__ = ["DESIGN_FILE", "file", "current", "find", "digest", "declarations",
           "pending_removals", "outstanding", "said", "DESIGN_REPORT", "NONE",
           "LEAD_DECISION", "VERIFIED", "Standing", "design_verdicts", "verified",
           "standing", "stages", "newest", "verdicts",
           "threads", "waiting", "unchanged"]

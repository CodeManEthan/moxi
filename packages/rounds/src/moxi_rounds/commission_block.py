"""The commission block: what a builder reads first, and what the lead is told beside it.

`block(...)` gives the block of one build as `commission` made it, read from the round
and its project's log as they stood under the commission's lock. It holds only what
the builder needs: the round, stage and flow; the repo, branch, base and target; the
two folders, the scratch folder said to be for logs and probes only; the commands the
builder runs and the one way a question reaches the operator (through the lead); who
finishes and what must be true first; what the build step reads; the rulings the
build follows; the stage's slices; the operator's skips that reach it; and the repo's
own `brief` lines.

**The rulings a build follows** are exactly the ones the design's rows cite for the
stage, each as it stands now (`tracing.given`): a cited ruling that was replaced is
given as the one in its place, marked with the id the row cites, and one that was
taken back is left out. Nothing else from the register is in the block.

**What the lead is told** after the block, by the same call that commissioned the
build, so neither is a gate: each of the block's rulings the operator has not been
shown, named with the round or project that holds it, with the `mark --shown` line for
each holder; and each standing ruling that stands, names the stage's repo in its scope
and is cited by no row, for the lead to check.

**What changed since the block.** `changed(...)` gives, for a stage with an open build,
each ruling recorded after the block was printed that applies to the stage: a new one,
one that replaces a ruling that applied, and one that takes such a ruling back. `next`
prints them with the stage, so a correction reaches a build already commissioned.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from . import corrections, flows, ruling, skips, stages, tracing
from . import settings as own
from . import shown as showings
from . import state as states

#: The steps the block speaks of.
BUILD = "build"
CHECK = "check"
VERIFY_BUILD = "verify-build"
FINISH = "finish"

#: Who runs a builder's commands, as a flow's step entries name the role.
BUILDER = "builder"

_NUMBERS = {1: "one", 2: "two", 3: "three"}
_ROUND = "C"
_STANDING = "S"


@dataclass(frozen=True)
class Unshown:
    """One of the block's rulings the operator has not been shown: the `holder`, the
    round or project whose log has it, and the `ruling`."""

    holder: str
    ruling: states.Ruling


@dataclass(frozen=True)
class Block:
    """A build's commission block: its `lines`, the rulings it `gives`, and for the
    lead the ones of those not yet `unshown` to the operator and the standing rulings
    in the repo's scope no row cites (`uncited`)."""

    lines: tuple[str, ...]
    gives: tuple[tracing.Given, ...]
    unshown: tuple[Unshown, ...]
    uncited: tuple[states.Ruling, ...]

    def lead_lines(self) -> list[str]:
        """What is printed to the lead after the block."""
        said = []
        if self.unshown:
            listed = "; ".join(f"{one.holder} {one.ruling.id} {_text(one.ruling)}"
                               for one in self.unshown)
            said.append(f"not yet shown to the operator: {listed}")
            holders: dict[str, list[str]] = {}
            for one in self.unshown:
                holders.setdefault(one.holder, []).append(one.ruling.id)
            said += [f"  then: moxi rounds mark {holder} {','.join(ids)} --shown "
                     "--record PTR" for holder, ids in holders.items()]
        said += [f"not cited, check: {one.id} {_text(one)}" for one in self.uncited]
        return said


def _text(one: states.Ruling) -> str:
    return one.text if one.text is not None else (one.rules or "")


def _entry(state: states.State, step: str) -> Mapping:
    return next((one for one in state.steps if one.get("step") == step), None) or {}


def _reads(entry: Mapping) -> list[str]:
    reads = entry.get("reads") or ()
    return [str(line) for line in ([reads] if isinstance(reads, str) else reads)]


def _commands(state: states.State, settings, repo: str) -> list[str]:
    """The commands a builder runs, from the worktree: `next` always, `check` where the
    repo declares one, and `finish` where the flow's finish is the builder's."""
    said = ["moxi rounds next"]
    if own.build_setting(settings, repo, "check") is not None:
        said.append("moxi rounds check")
    if _entry(state, FINISH).get("by") == BUILDER:
        said.append("moxi rounds finish")
    return said


def _finish_line(state: states.State, settings, *, repo: str, stage: str,
                 target: str) -> list[str]:
    """Who finishes and what must be true first, as two facts."""
    did, merged = [], False
    for action in own.build_setting(settings, repo, "finish"):
        kind = action.get("do")
        if kind == "merge":
            merged = True
            did.append(f"merge into {target}")
        elif kind == "push":
            did.append(f"push {target if merged else 'the branch'} to "
                       f"{action.get('remote')}")
        else:
            did.append(f"run {action.get('id')}")
    who = _entry(state, FINISH).get("by")
    first = []
    verifies = (flows.has(state.steps, VERIFY_BUILD)
                and not skips.is_skipped(state, VERIFY_BUILD, stage))
    if verifies:
        checker = _entry(state, VERIFY_BUILD).get("by")
        first.append("its verification is recorded publishable"
                     + ("" if checker is None else f" by the {checker}"))
    if (own.build_setting(settings, repo, "check") is not None
            and not skips.is_skipped(state, CHECK, stage)):
        first.append("the check passes at the tip")
    head = f"finish: {', then '.join(did) or 'nothing to do'}"
    if who is not None:
        head += f", by {who}"
    head += f", after {' and '.join(first)}." if first else ", once the build has work."
    lines = [head]
    if verifies:
        lines.append("        You do not record a verdict or mark a finding.")
    return lines


def _ruling_line(one: tracing.Given) -> str:
    named = one.ruling.id if one.replaces is None else (
        f"{one.ruling.id} replaces {one.replaces}")
    pointer = "" if one.ruling.record is None else f" ({one.ruling.record})"
    return f"  {named}  {_text(one.ruling)}{pointer}"


def _slices(stage: states.Stage) -> str:
    parts = []
    for one in stage.slices:
        minutes = one.get("minutes")
        part = f"{one.get('id')} {one.get('what')}".strip()
        parts.append(part if minutes is None else f"{part} ({minutes} min)")
    return "; ".join(parts) or "none"


def _skipped(state: states.State, stage: str) -> str:
    found = [f"{skips.said(one.step, one.stage)} ({one.id})" for one in skips.live(state)
             if one.stage in (None, stage)]
    return "; ".join(found) or "none"


def _uncited(state: states.State, register: states.State, stage: states.Stage,
             gives: Sequence[tracing.Given]) -> tuple[states.Ruling, ...]:
    """The standing rulings that stand, say what is built, name the stage's repo in
    their scope, and are neither cited by a row nor given in a cited one's place."""
    cited = {one["id"] for one in tracing.rows(state)}
    cited |= {one.ruling.id for one in gives}
    return tuple(one for one in corrections.live(register.rulings)
                 if one.kind == ruling.SUBSTANCE and one.id not in cited
                 and any(part.get("repo") == stage.repo for part in one.scope))


def block(state: states.State, register: states.State, *, name: str, stage: str,
          repo: str, branch: str, base: str, target: str, worktree: Path,
          scratch: Path, settings) -> Block:
    """The commission block of the build of `stage` in the round `name`, read from
    `state`, the round's state, and `register`, its project's, as they stood when the
    build was commissioned. The other arguments are the build's, as recorded."""
    planned = stages.find(state, stage)
    gives = tracing.given(state, register, stage)
    commands = _commands(state, settings, repo)
    lines = [
        f"round {name}   stage {stage}   flow {state.flow}",
        f"repo {repo}   branch {branch}   base {base[:7]}   target {target}",
        f"worktree {worktree}",
        f"scratch  {scratch}",
        "         (logs and probes only: removed when the build ends)",
        f"you run {_NUMBERS.get(len(commands), len(commands))} commands, from the "
        f"worktree: {', '.join(commands)}",
        "a question for the operator: tell your lead",
        *_finish_line(state, settings, repo=repo, stage=stage, target=target),
        f"reads: {'; '.join(_reads(_entry(state, BUILD))) or 'nothing named'}",
        "rulings this build follows:" + ("" if gives else " none"),
        *(_ruling_line(one) for one in gives),
        f"slices: {'none' if planned is None else _slices(planned)}",
        f"skipped by the operator: {_skipped(state, stage)}",
        *(str(line) for line in own.build_setting(settings, repo, "brief")),
    ]
    project = name.partition("/")[0]
    unshown = tuple(
        Unshown(holder=name if one.ruling.id.startswith(_ROUND) else project,
                ruling=one.ruling)
        for one in gives if not showings.is_shown(one.ruling, register))
    uncited = (() if planned is None or planned.repo is None
               else _uncited(state, register, planned, gives))
    return Block(lines=tuple(lines), gives=gives, unshown=unshown, uncited=uncited)


# -- what changed since a block ----------------------------------------------------------

@dataclass(frozen=True)
class Change:
    """A ruling recorded after a build's block that reaches its stage: its `ruling`,
    the id it `replaces` or `revokes` (each None when it does neither), and the
    sentence of the ruling it takes back, `was`, when it takes one back."""

    ruling: states.Ruling
    replaces: str | None = None
    revokes: str | None = None
    was: str | None = None

    def data(self) -> dict:
        return {"id": self.ruling.id, "text": self.ruling.text,
                "record": self.ruling.record, "replaces": self.replaces,
                "revokes": self.revokes, "was": self.was}


def line(change: Mapping) -> str:
    """A change in words, as `next` prints it to the build."""
    if change["revokes"] is not None:
        was = "" if change["was"] is None else f": {change['was']}"
        return (f"changed since your block: {change['id']} takes back "
                f"{change['revokes']}{was}")
    named = change["id"] if change["replaces"] is None else (
        f"{change['id']} replaces {change['replaces']}")
    return f"changed since your block: {named}: {change['text']}"


def _commissioned(state: states.State, stage: str) -> dict | None:
    """The event of the stage's open build, or None when it has none."""
    if stages.build_of(state, stage) != stages.BUILDING:
        return None
    return next((event for event in reversed(state.history)
                 if event["type"] == "build.commissioned" and event.get("stage") == stage),
                None)


def _reached(state: states.State, register: states.State | None,
             one: states.Ruling, stage: str) -> bool:
    """Whether `one` applied to the stage: a round's ruling that says what is built and
    names every stage or this one; a standing ruling, or one it replaced, that the
    round was opened with or a row cites, and whose scope reaches the stage's repo."""
    if one.id is None:
        return False
    if one.id.startswith(_ROUND):
        return one.kind == ruling.SUBSTANCE and tracing.applies_to(one, stage)
    if register is None or one.kind != ruling.SUBSTANCE:
        return False
    owed = set(state.inherits) | {row["id"] for row in tracing.rows(state)}
    seen, back = set(), one
    while back is not None and back.id not in seen:
        if back.id in owed:
            return tracing.scope_reaches(one, state, stage)
        seen.add(back.id)
        back = (None if back.supersedes is None
                else corrections.find(register, back.supersedes))
    return False


def changed(state: states.State, register: states.State | None,
            stage: str) -> tuple[Change, ...]:
    """What changed for the open build of `stage` since its block was printed: each
    ruling recorded after its commission, in the round's log or the register, that
    applies to the stage, replaces a ruling that did, or takes one back. Empty for a
    stage with no open build. `register` is the project's state; with None only the
    round's rulings are read."""
    event = _commissioned(state, stage)
    if event is None:
        return ()
    later = {one["id"] for one in state.history
             if one["type"] == "ruling.recorded" and one["n"] > event["n"]}
    # The register's times are of another log: a ruling of the same second as the
    # commission may have come after it, so it is told rather than left out.
    found = [(one, state) for one in state.rulings if one.id in later]
    if register is not None:
        found += [(one, register) for one in register.rulings
                  if one.id is not None and one.at >= event["at"]]
    said = []
    for one, held in sorted(found, key=lambda pair: pair[0].at):
        if one.revokes is not None:
            back = corrections.find(held, one.revokes)
            if back is not None and _reached(state, register, back, stage):
                said.append(Change(one, revokes=back.id, was=back.text))
            continue
        if one.kind != ruling.SUBSTANCE:
            continue
        earlier = None if one.supersedes is None else corrections.find(held, one.supersedes)
        if earlier is not None and (_reached(state, register, earlier, stage)
                                    or _reached(state, register, one, stage)):
            said.append(Change(one, replaces=earlier.id))
        elif earlier is None and _reached(state, register, one, stage):
            said.append(Change(one))
    return tuple(said)


__all__ = ["BUILD", "CHECK", "VERIFY_BUILD", "FINISH", "BUILDER", "Unshown", "Block",
           "block", "Change", "changed", "line"]

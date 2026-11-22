"""The lead's repairs: a record derived again from what it was derived from. A repair
never supplies a value, and records no step's completion.

- `resync(round, stage, actor=...)` reads git again for the one git fact the log holds
  for later reads, the base of the stage's open build. A base must be a commit of the
  target, and stops being one only when the target's history was rewritten. Then, and
  only then, the base moves to the merge base of the old base and the target's head:
  the newest commit of the old base the target still has, so an ancestor of the old
  base. A lower base only adds commits for the build's state to account for, so a
  resync turns no build `empty` and none `merged`. When git gives no merge base, or
  more than one, nothing moves and a build on the target stays `unclear`. A base still
  on the target is left as it is. Either way nothing is written, and the result says
  so. A rebase of the branch needs no repair: the state reads the tip against the
  target, not against the base.
- `reread(round, report, actor=...)` parses a recorded report again, at the SHA-256 its
  verdict recorded, with the current reader, and adds to that verdict's record the
  findings, waiting items and decisions the block holds and the record lacks. It only
  adds: one that would take an item away or change one on record, or change any other
  field of the verdict, its word included, is refused, naming what. A report whose
  bytes changed since it was recorded is refused: it is a new report, recorded with
  `verdict`. An item it would add takes an id no other report of its stage used. The
  block is read as `verdict` reads one, so a publishable report gains no open blocker
  or major.

Each writes one `repair` event, the whole of the repair: what was derived again, the
old and new value of each field, and what was read. A ruling, a skip and a verdict
word are the operator's or a report's, and each repair refuses to name one as what it
repairs.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from . import (builds, ending, findings, folders, places, questions, report_block,
               verdicts, write)
from . import settings as own
from . import state as states
from .errors import BadArgument, BadBlock, NoLog, NoRound, Refused, RoundsError, Unmet
from .gitreads import Git

#: The two repairs, as the `what` of their event.
BASE = states.BASE
REPORT = states.REPORT
#: The event a repair writes.
REPAIRED = "repair"

# A ruling's id, of a round or of a project; several are separated by commas.
_RULING = re.compile(r"[CS][1-9][0-9]*(,[CS][1-9][0-9]*)*")
# What starts a ruling that skips a step or a finish action.
_SKIPS = ("skip:", "skip-action:")
# What each list of a verdict holds, in words.
_CALLED = {"findings": "finding", "waits": "waiting item", "decisions": "decision"}


@dataclass(frozen=True)
class Repaired:
    """A repair as it went: the round's `name`, `what` it derived again, `said`, a line
    that says what it found and did, and the `event` written, None when nothing was
    changed and nothing written. `failed` pairs each after-append callback that raised
    with what it raised."""

    name: str
    what: str
    said: str
    event: dict | None
    failed: tuple[tuple[Callable, Exception], ...] = ()


def not_repaired(given: str, name: str) -> Refused | None:
    """The refusal of `given` as what a repair names, when it is a ruling, a skip or a
    verdict word: the operator's or a report's, which no repair re-derives."""
    if _RULING.fullmatch(given):
        return Refused([Unmet(
            f"{given} names a ruling, and a ruling is the operator's: a repair derives "
            "a record again from git or from a report, and never changes a ruling",
            f"the operator corrects a ruling with a later one: moxi rounds rule {name} "
            "ITEM --supersedes ID --record PTR, or rule ... revoke:ID --record PTR")])
    if given.startswith(_SKIPS):
        return Refused([Unmet(
            f"{given} is a skip, and a skip is the operator's ruling: no repair adds, "
            "changes or takes back one",
            f"the operator takes a skip back: moxi rounds rule {name} revoke:C<n> "
            "--record PTR")])
    if given in report_block.VERDICTS:
        return Refused([Unmet(
            f"{given} is a verdict word, and a verdict word is a report's as recorded: "
            "no repair changes one",
            f"a new report records a new verdict: moxi rounds verdict {name} REPORT.md")])
    return None


def resync(target: str, stage_id: str, *, by: str, settings=None,
           wait_s: float = write.WAIT_S, at: str | None = None) -> Repaired:
    """Read git again for the base of the open build of `stage_id`, `B<n>`, of the
    round `target`, and move it when the target was rewritten under it.

    Raises `BadArgument` for a stage id that can't be one; `UnknownProject`,
    `NoFolder` or `NoRound` for a round that can't be found; and `Refused` for a
    ruling, a skip or a verdict word, when the round has ended, the stage has no open
    build, or git can't read the build's repo or target. A refusal leaves the log as
    it was."""
    project, slug = folders.split(target)
    name = f"{project}/{slug}"
    refused = not_repaired(stage_id or "", name)
    if refused is not None:
        raise refused
    if not questions.STAGE.fullmatch(stage_id or ""):
        raise BadArgument(f"{stage_id!r} is not a stage", "name the stage whose base is "
                          "read again, as B<n>")
    settings = own.load() if settings is None else settings
    home = folders.home(project, settings)
    name = f"{home.project}/{slug}"
    shown = f"moxi rounds show {name}"
    went: dict[str, str] = {}

    def open_build(view):
        build = builds.current(view.state, stage_id)
        if build is None:
            return Unmet(f"{stage_id} of {name} was never commissioned, so it has no "
                         "base", shown)
        if build.ended is not None:
            return Unmet(f"the build of {stage_id} is {build.ended}: an ended build is "
                         "not read from git, so its base is read by nothing", shown)
        return None

    def repaired(view, result):
        build = builds.current(view.state, stage_id)
        base, into = build.base, build.target
        try:
            _key, root = folders.root(build.repo)
            git = Git(root)
            head = git.branch(into)
            if head is None:
                raise Refused([Unmet(f"the target branch {into} is not in {build.repo}",
                                     f"bring the branch {into} back into the repo")])
            present = bool(git.present([base]))
            if present and git.is_ancestor(base, head):
                went["said"] = (f"{stage_id} base {base[:12]} is on {into}: nothing to "
                                "repair, and nothing was written")
                return None
            found = git.merge_bases(base, head) if present else ()
        except RoundsError as exc:
            if isinstance(exc, Refused):
                raise
            raise Refused([Unmet(exc.reason, exc.fix or shown)]) from None
        if len(found) != 1:
            answer = ("git no longer holds it" if not present else
                      f"git gives no merge base of it and {into}" if not found else
                      f"git gives {len(found)} merge bases of it and {into}")
            went["said"] = (f"{stage_id} base {base[:12]} is no longer on {into}, and "
                            f"{answer}: nothing was written. The build reads unclear "
                            "until git gives one answer, or the operator drops it")
            return None
        went["said"] = (f"{stage_id} base: {base[:12]} to {found[0][:12]}, the newest "
                        f"commit of the old base that {into} still has, after {into} "
                        "was rewritten")
        return write.Entry(REPAIRED, {
            "what": BASE, "stage": stage_id,
            "fields": {"base": {"was": base, "now": found[0]}},
            "read": {"target": into, "target_head": head,
                     "argv": ["git", "merge-base", "--all", base, head],
                     "output": list(found)}})

    try:
        written = write.record(write.Where(home.project, home.rounds_dir, slug), by=by,
                               gates=[ending.takes_new_work(name, "a repair"),
                                      open_build],
                               event=repaired, wait_s=wait_s, at=at)
    except NoLog:
        raise NoRound(name) from None
    return Repaired(name=name, what=BASE, said=went["said"],
                    event=written.events[0] if written.events else None,
                    failed=written.failed)


@dataclass
class _Read:
    """The report as read under the lock: the SHA-256 of its bytes, and its `block`
    or the `error` that stopped the reading."""

    sha256: str | None = None
    block: report_block.ReportBlock | None = None
    error: RoundsError | None = None


def reread(target: str, report: Path | str, *, by: str, settings=None,
           wait_s: float = write.WAIT_S, at: str | None = None) -> Repaired:
    """Parse the recorded report `report` of the round `target` again with the current
    reader, and add to its verdict's record what its block holds and the record lacks.

    Raises `UnknownProject`, `NoFolder` or `NoRound` for a round that can't be found;
    `BadBlock` when the report can't be read or its block no longer reads; and
    `Refused` for a ruling, a skip or a verdict word, when the round has ended, no
    verdict recorded these bytes, or the reread would take away or change anything. A
    refusal leaves the log as it was."""
    project, slug = folders.split(target)
    name = f"{project}/{slug}"
    refused = not_repaired(str(report), name)
    if refused is not None:
        raise refused
    settings = own.load() if settings is None else settings
    home = folders.home(project, settings)
    name = f"{home.project}/{slug}"
    round_dir = home.round_dir(slug)
    report = Path(report)
    again = f"moxi rounds verdict {name} {report}"
    went: dict[str, str] = {}

    def read(where) -> _Read:
        try:
            raw = report.read_bytes()
        except OSError as exc:
            return _Read(error=RoundsError(
                f"{report} can't be read: {exc.strerror or exc}",
                "name the report's file as it is on this machine"))
        found = _Read(sha256=hashlib.sha256(raw).hexdigest())
        try:
            found.block = report_block.read(report)
        except BadBlock as exc:
            found.error = exc
        return found

    def repaired(view, result):
        facts: _Read = view.facts
        if facts.sha256 is None:
            raise Refused([Unmet(facts.error.reason, facts.error.fix)])
        state = view.state
        verdict = next((one for one in state.verdicts if one.sha256 == facts.sha256),
                       None)
        if verdict is None:
            given = verdicts.path(report, round_dir)
            # A path the record wrote whole and one it wrote from `~` name one file.
            was = next((one for one in state.verdicts
                        if places.expand_home(one.report) == places.expand_home(given)),
                       None)
            if was is not None:
                raise Refused([Unmet(
                    f"{report} changed since its verdict was recorded on line {was.n}: "
                    "a reread reads a report at the bytes its verdict recorded, and a "
                    "changed report is a new report", again)])
            raise Refused([Unmet(f"no verdict of {name} recorded a report with these "
                                 f"bytes, so there is nothing of {report} to read "
                                 "again", again)])
        if facts.error is not None:
            raise facts.error
        block = facts.block
        if block.sha256 != facts.sha256:
            raise Refused([Unmet(f"{report} changed while it was read", "run it again")])
        event = next(one for one in states.with_rereads(state)
                     if one.get("type") == "verdict.recorded" and one.get("n") == verdict.n)
        unmet = _changed(event, block, name=name, report=report)
        added, unmet_items = _added(state, event, block, report=report)
        unmet += unmet_items
        if unmet:
            raise Refused(unmet)
        if not added:
            went["said"] = (f"{report} reads as its verdict on line {verdict.n} "
                            "recorded it: nothing to add, and nothing was written")
            return None
        now = verdicts.lists(block)
        went["said"] = (f"{report}, the verdict on line {verdict.n}: added "
                        + "; ".join(f"{_CALLED[key]}{'' if len(ids) == 1 else 's'} "
                                    f"{', '.join(ids)}" for key, ids in added.items()))
        return write.Entry(REPAIRED, {
            "what": REPORT, "verdict": verdict.n,
            "fields": {key: {"was": list(event.get(key) or ()), "now": now[key]}
                       for key in added},
            "read": {"report": verdict.report, "sha256": facts.sha256}})

    try:
        written = write.record(write.Where(home.project, home.rounds_dir, slug), by=by,
                               gates=[ending.takes_new_work(name, "a repair")],
                               event=repaired, facts=read, wait_s=wait_s, at=at)
    except NoLog:
        raise NoRound(name) from None
    return Repaired(name=name, what=REPORT, said=went["said"],
                    event=written.events[0] if written.events else None,
                    failed=written.failed)


# The fields of a verdict's event a reread reads again beside its lists, each with the
# block's value of it, for the kinds that carry it.
_FIELDS: dict[str, Callable[[report_block.ReportBlock], object]] = {
    "kind": lambda block: block.kind,
    "verdict": lambda block: block.verdict,
    "pass": lambda block: block.pass_,
    "step": lambda block: block.step,
    "design": lambda block: block.design,
    "stage": lambda block: block.stage,
    "head": lambda block: block.head,
    "model": lambda block: block.model,
    "by": lambda block: block.by,
    "stages": lambda block: list(block.stages),
}


def _carries(kind: str) -> tuple[str, ...]:
    """The fields beside the lists a verdict's event of `kind` holds from the block."""
    carried = ["kind", "model", "by"]
    if kind in (report_block.DESIGN, report_block.BUILD, report_block.STEP):
        carried += ["verdict", "pass"]
    if kind == report_block.DESIGN:
        carried.append("design")
    if kind in (report_block.BUILD, report_block.STEP):
        carried += ["stage", "head"]
    if kind == report_block.STEP:
        carried.append("step")
    if kind == report_block.MAJORS:
        carried.append("stages")
    return tuple(carried)


def _changed(event: dict, block: report_block.ReportBlock, *, name: str,
             report: Path) -> list[Unmet]:
    """What of the verdict's fields beside its lists the block now reads otherwise."""
    unmet = []
    again = f"moxi rounds verdict {name} REPORT.md"
    for key in _carries(str(event.get("kind"))):
        was, now = event.get(key), _FIELDS[key](block)
        if was == now:
            continue
        if key == "verdict":
            unmet.append(Unmet(
                f"{report} now reads as {now}, and its verdict was recorded as {was}: a "
                "verdict word is the report's as recorded, and no repair changes one",
                f"a new report records a new verdict: {again}"))
        else:
            unmet.append(Unmet(
                f"{report} now reads {key} as {now!r}, and its verdict recorded "
                f"{was!r}: a reread adds findings, waiting items and decisions, and "
                "changes nothing else of a verdict", f"record a new report: {again}"))
    return unmet


def _added(state: states.State, event: dict, block: report_block.ReportBlock, *,
           report: Path) -> tuple[dict[str, list[str]], list[Unmet]]:
    """The ids of each list the block adds to the verdict's record, and what refuses
    the reread: an item it would take away or change, and an id another report of the
    stage used. A publishable report with an open blocker or major is the reader's to
    refuse, as it refuses one to `verdict`."""
    now = verdicts.lists(block)
    added: dict[str, list[str]] = {}
    unmet = []
    for key in states.REREAD_LISTS:
        was = {one.get("id"): one for one in event.get(key) or () if isinstance(one, dict)}
        said = {one["id"]: one for one in now.get(key, ())}
        for id_, one in was.items():
            called = f"the {_CALLED[key]} {_key(event, one)}"
            if id_ not in said:
                unmet.append(Unmet(
                    f"{report} no longer lists {called}, which its verdict recorded: "
                    "a reread only adds, and never takes an item away",
                    "leave the record as it is; only the operator removes an item"))
            elif said[id_] != one:
                how = ", ".join(f"{field} {one.get(field)!r} to {said[id_].get(field)!r}"
                                for field in sorted(set(one) | set(said[id_]))
                                if one.get(field) != said[id_].get(field))
                unmet.append(Unmet(
                    f"{report} now reads {called} otherwise than its verdict recorded "
                    f"it ({how}): a reread only adds, and never changes or lowers an "
                    "item on record", "leave the record as it is; a new report lists "
                    "the item as it stands"))
        for id_, one in said.items():
            if id_ in was:
                continue
            stage = one.get("stage") or event.get("stage")
            if isinstance(stage, str) and id_ in findings.taken(state, stage):
                unmet.append(Unmet(
                    f"the {_CALLED[key]} {stage}.{id_} that {report} would add takes an "
                    f"id another report of {stage} used: an old id never names a new "
                    "item", "leave the record as it is; a new report lists the item "
                    "under an id no report of the stage used"))
                continue
            added.setdefault(key, []).append(id_)
    return added, unmet


def _key(event: dict, one: dict) -> str:
    """An item's id as the record names it: with its stage, for an item of a build."""
    stage = one.get("stage") or event.get("stage")
    return f"{stage}.{one.get('id')}" if isinstance(stage, str) else str(one.get("id"))


__all__ = ["BASE", "REPORT", "REPAIRED", "Repaired", "not_repaired", "resync", "reread"]

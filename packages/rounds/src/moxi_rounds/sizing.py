"""Sizing a brief: the check that what a worker is handed is one piece of work, small
enough, before the worker starts.

A worker gets one slice: one mechanism or one file group, well under an hour. rounds
starts no worker, so it can't stop a launch; it makes the rule a command the lead runs
on the brief itself, and shows a slice no brief was sized for.

**The brief's block.** A brief carries a data block of the same form as a design's:

    stage = "B2"
    slice = "2a"          # or, in place of slice: verify = "build" | "design" | "retro"

`read(file)` reads it. A block that names both a slice and a verification, or a list
of slices, is refused as a bad block: a brief is for one piece of work. A block that
names neither is the brief of a stage that declares no slices, which is one slice.

**The check.** `size(round, stage, brief, actor=...)` passes when the block names the
stage the command names and either one slice that stage declares, given no more minutes
than the round's project allows (`slice_minutes`), or one verification, or, for a stage
that declares no slices, neither: the whole stage as its one slice, with no minutes to
check. A block naming neither is refused for a stage that declares slices, naming them.
The limit is read when the brief is sized, so a slice over it can't be sized until the
design cuts it. A pass appends one `brief.sized` event with the brief's file and SHA-256; a refusal
writes nothing. The same brief sized again for the same piece writes nothing and says
so, so a rerun after a stop finds its own record. A brief changed since, sized again,
is recorded again.

**Which build a sizing is of.** A brief is handed to a worker of one build. A sizing
counts for the build of its stage that was open when it was recorded, or, with none
open, for the stage's next build: a brief may be sized before the commission it will
carry. So a dropped build's briefs are not its successor's. `of_build` gives what counts
for one build, and `slices_of` each slice of the build's stage with its newest sizing;
a stage that declares no slices gives its one slice, whose id is None.

`oversized_line` is the warning `fold` and `commission` print for a slice they record
over the limit: both go ahead, and the slice's brief is then refused until it is cut.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from . import builds, datablock, ending, folders, questions, stages, verdicts, write
from . import settings as own
from . import state as states
from .datablock import Entry, Key, Table
from .design_block import SLICE_ID
from .errors import BadArgument, BadBlock, NoLog, NoRound, Problem, Unmet

#: How a refusal names the block it reads.
WHAT = "a brief's data block"

#: The event a sized brief appends, and the result it records.
SIZED = "brief.sized"
PASSED = "passed"

#: The verifications a brief may be for, in place of a slice.
VERIFY_BUILD = "build"
VERIFY_DESIGN = "design"
VERIFY_RETRO = "retro"
VERIFIES = (VERIFY_BUILD, VERIFY_DESIGN, VERIFY_RETRO)

#: The setting that caps one slice's minutes.
LIMIT = "slice_minutes"


@dataclass(frozen=True, kw_only=True)
class BriefBlock(Entry):
    """A brief's block as read: the `file` and its `sha256`, the `stage`, and the one
    `slice` or `verify` it names (the other is None; both None for the one slice of a
    stage that declares none)."""

    file: Path
    sha256: str
    stage: str
    slice: str | None
    verify: str | None


def _one_slice(value: object) -> str | None:
    if isinstance(value, str) and SLICE_ID.fullmatch(value):
        return None
    if isinstance(value, list):
        return (f"is {datablock.shown(value)}, a list: a brief names one slice, since a "
                "worker gets one")
    return f"is {datablock.shown(value)}, which is not a slice's id"


#: The schema of a brief's block.
SCHEMA = Table("", keys=(
    datablock.ident("stage", questions.STAGE, "B<n>", "the stage the brief's work is of"),
    Key("slice", _one_slice, '"<a slice id of the stage>", as "2a"',
        "the one slice the brief hands out, as the stage declares it", required=False),
    datablock.word("verify", VERIFIES, "in place of slice: the one verification the "
                                       "brief is for", required=False),
))


def read(file: Path | str) -> BriefBlock:
    """The data block of the brief `file`.

    Raises `BadBlock` when the file has no one data block that is TOML, when the block
    does not fit the schema, and when it names both a slice and a verification.
    """
    found = datablock.find(file, WHAT)
    problems = datablock.check(found, SCHEMA)
    if not problems:
        data = found.data
        if "slice" in data and "verify" in data:
            problems.append(Problem(
                found.line_of("verify"),
                "the block names a slice and a verification, and a brief is for one "
                "piece of work", "keep slice or verify, and write a brief of its own "
                "for the other"))
    if problems:
        raise BadBlock(found.file, WHAT, problems)
    data = found.data
    return BriefBlock(file=found.file, sha256=found.sha256, stage=data["stage"],
                      slice=data.get("slice"), verify=data.get("verify"),
                      line=found.line, lines=found.lines((), data))


# -- the records ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Sized:
    """One `brief.sized` record: the `stage`, the `slice` or `verify` sized, the `brief`
    as the log names it and its `sha256`, the line `n`, and who sized it and when."""

    stage: str
    slice: str | None
    verify: str | None
    brief: str
    sha256: str
    n: int
    actor: str
    at: str

    @property
    def piece(self) -> str:
        """What was sized, in words: `slice 2a`, `verify build`, or `one slice` for a
        stage that declares none."""
        return piece_of(self.slice, self.verify)

    def data(self) -> dict:
        return {"stage": self.stage, "slice": self.slice, "verify": self.verify,
                "brief": self.brief, "sha256": self.sha256, "n": self.n,
                "actor": self.actor, "at": self.at}


#: How the one slice of a stage that declares none is named.
ONE_SLICE = "one slice"


def piece_of(slice_: str | None, verify: str | None) -> str:
    """A sized piece in words: `slice 2a`, `verify build`, or `one slice`."""
    if slice_ is not None:
        return f"slice {slice_}"
    return f"verify {verify}" if verify is not None else ONE_SLICE


def oversized_line(one: dict, limit: int) -> str:
    """The warning for a slice recorded over the limit, `one` as `{"stage", "slice",
    "minutes"}`."""
    return (f"warning: the slice {one['slice']} of {one['stage']} is given "
            f"{one['minutes']} minutes, over the {limit} of {LIMIT}: a worker gets one "
            "piece, well under an hour, and a brief for it is refused until it is cut")


def _sized(event: dict) -> Sized | None:
    stage = event.get("stage")
    if not isinstance(stage, str):
        return None
    slice_ = event.get("slice")
    verify = event.get("verify")
    return Sized(stage=stage, slice=slice_ if isinstance(slice_, str) else None,
                 verify=verify if isinstance(verify, str) else None,
                 brief=str(event.get("brief")), sha256=str(event.get("sha256")),
                 n=event["n"], actor=event["actor"], at=event["at"])


def _placed(state: states.State) -> tuple[dict[int, list[Sized]], dict[str, list[Sized]]]:
    """Every sizing of the round placed with its build: by the line of the build's
    commission, and, by stage, the ones no build has taken yet."""
    open_of: dict[str, int] = {}
    taken: dict[int, list[Sized]] = {}
    waiting: dict[str, list[Sized]] = {}
    for event in state.history:
        if event["type"] == "round.dropped":
            # The builds still open end with their round.
            open_of.clear()
        stage = event.get("stage")
        if not isinstance(stage, str):
            continue
        kind = event["type"]
        if kind == "build.commissioned":
            open_of[stage] = event["n"]
            taken[event["n"]] = waiting.pop(stage, [])
        elif kind in ("build.dropped", "finish.done"):
            open_of.pop(stage, None)
        elif kind == SIZED:
            one = _sized(event)
            if one is None:
                continue
            if stage in open_of:
                taken[open_of[stage]].append(one)
            else:
                waiting.setdefault(stage, []).append(one)
    return taken, waiting


def of_build(state: states.State, build: builds.Build) -> list[Sized]:
    """The sizings that count for `build`, oldest first."""
    return list(_placed(state)[0].get(build.n, ()))


def _counts_now(state: states.State, stage_id: str) -> list[Sized]:
    """The sizings that count for the build a sizing recorded now would count for:
    the stage's open build, or its next one."""
    taken, waiting = _placed(state)
    build = builds.current(state, stage_id)
    if build is not None and build.ended is None:
        return list(taken.get(build.n, ()))
    return list(waiting.get(stage_id, ()))


def slices_of(state: states.State, build: builds.Build) -> list[dict]:
    """Each slice of the build's stage, in the order declared, as `{"id", "what",
    "minutes", "sized"}`, `sized` the newest sizing of it that counts for the build,
    or None. A stage that declares no slices gives its one slice, with every field
    but `sized` None."""
    stage = stages.find(state, build.stage)
    if stage is None:
        return []
    counted = of_build(state, build)
    listed = []
    for piece in stage.slices or ({"id": None, "what": None, "minutes": None},):
        newest = None
        for one in counted:
            if one.slice == piece.get("id") and one.verify is None:
                newest = one
        listed.append({"id": piece.get("id"), "what": piece.get("what"),
                       "minutes": piece.get("minutes"),
                       "sized": None if newest is None else newest.data()})
    return listed


def verifications_of(state: states.State, build: builds.Build) -> list[dict]:
    """Each verification sized for the build, its newest sizing, in the order of
    `VERIFIES`."""
    newest: dict[str, Sized] = {}
    for one in of_build(state, build):
        if one.verify is not None:
            newest[one.verify] = one
    return [newest[kind].data() for kind in VERIFIES if kind in newest]


def unsized(state: states.State, build: builds.Build) -> list[str]:
    """The build's slices that no sized brief counts for, by id, or `ONE_SLICE` for a
    stage that declares none."""
    return [ONE_SLICE if one["id"] is None else one["id"]
            for one in slices_of(state, build) if one["sized"] is None]


def mark_line(name: str, stage_id: str) -> str:
    """The command that sizes a brief for a stage."""
    return f"moxi rounds mark {name} {stage_id} --brief FILE"


# -- sizing ------------------------------------------------------------------------------

@dataclass(frozen=True)
class Sizing:
    """A brief after `size`: the `name` of its round, the `sized` record as the log has
    it, for a slice the `minutes` its stage gives it and the `limit` read (both None
    for a verification and for the one slice of a stage that declares none), the
    `event` written,
    None when `found`, the same brief sized for the same piece already. `failed` pairs
    each after-append callback that raised with what it raised."""

    name: str
    sized: Sized
    minutes: int | None
    limit: int | None
    event: dict | None
    found: bool = False
    failed: tuple[tuple[Callable, Exception], ...] = ()


def size(target: str, stage_id: str, brief: Path | str, *, by: str, settings=None,
         wait_s: float = write.WAIT_S, at: str | None = None) -> Sizing:
    """Size the brief `brief` for the stage `stage_id` of the round `target`.

    Raises `BadArgument` for a round or stage that can't be one; `BadBlock` for a brief
    whose block can't be read or names two pieces; `UnknownProject`, `NoFolder` or
    `NoRound` for a round that can't be found; and `Refused` when the round has ended,
    the block names another stage, the stage is not the round's or left the plan, the
    slice is not one the stage declares, the block names neither a slice nor a
    verification for a stage that declares slices, or the slice's minutes are over the
    limit. A refusal
    leaves the log as it was. A brief already sized with these bytes for this piece
    and this build is returned with `found` set, and nothing is written.
    """
    if not isinstance(target, str) or "/" not in target:
        raise BadArgument(f"{target!r} is not a round",
                          "name the round the brief is for, as <project>/<slug>")
    if not questions.STAGE.fullmatch(stage_id or ""):
        raise BadArgument(f"{stage_id!r} is not a stage's id",
                          "name the stage as B<n>: mark ROUND B<n> --brief FILE")
    block = read(brief)
    project, slug = folders.split(target)
    settings = own.load() if settings is None else settings
    home = folders.home(project, settings)
    name = f"{home.project}/{slug}"
    limit = own.round_setting(settings, home.project, LIMIT)
    show = f"moxi rounds show {name}"
    again = f"moxi rounds mark {name} {stage_id} --brief {block.file}"

    def names_the_stage(view):
        if block.stage == stage_id:
            return None
        return Unmet(f"the brief's block names {block.stage}, and the command "
                     f"{stage_id}", f'give the block stage = "{stage_id}", or run '
                     f"moxi rounds mark {name} {block.stage} --brief {block.file}")

    def is_a_stage_in_the_plan(view):
        if questions.unrecorded((stage_id,), view.state):
            return Unmet(f"{stage_id} is not a stage of {name}: "
                         f"{questions.stages_said(view.state)}", show)
        if stages.state_of(view.state, stage_id) == stages.LEFT:
            return Unmet(f"{stage_id} left the plan of {name}, and no build of it will "
                         "come", show)
        return None

    def declares_the_slice(view):
        stage = stages.find(view.state, stage_id)
        if block.verify is not None or stage is None:
            return None
        have = ", ".join(str(one.get("id")) for one in stage.slices)
        if block.slice is None:
            if not have:
                return None
            return Unmet(f"the brief's block names no slice and no verification, and "
                         f"{stage_id} declares slices: {have}",
                         f'give the block slice = "<one of {have}>", then {again}')
        if any(one.get("id") == block.slice for one in stage.slices):
            return None
        said = (f"its slices are {have}" if have
                else "it declares none, so it is one slice: leave slice out of the "
                     "block")
        return Unmet(f"{stage_id} declares no slice {block.slice}: {said}",
                     f"name one of its slices in the brief's block, then {again}")

    def within_the_limit(view):
        stage = stages.find(view.state, stage_id)
        if block.slice is None or stage is None:
            return None
        piece = next((one for one in stage.slices if one.get("id") == block.slice), None)
        if piece is None or not isinstance(piece.get("minutes"), int) \
                or piece["minutes"] <= limit:
            return None
        return Unmet(f"the slice {block.slice} of {stage_id} is given "
                     f"{piece['minutes']} minutes, over the limit of {limit} "
                     f"({LIMIT}): a worker gets one piece, well under an hour",
                     f"cut the slice in two in the design and fold it, then size a "
                     f"brief for each; or raise {LIMIT} in the project's settings")

    def sized(view, result):
        if _same(view.state, stage_id, block) is not None:
            return None
        return write.Entry(SIZED, {
            "stage": stage_id, "slice": block.slice, "verify": block.verify,
            "brief": verdicts.path(block.file, home.rounds_dir / slug),
            "sha256": block.sha256, "result": PASSED})

    try:
        written = write.record(
            write.Where(home.project, home.rounds_dir, slug), by=by,
            gates=[ending.takes_new_work(name, "a sized brief"), names_the_stage,
                   is_a_stage_in_the_plan, declares_the_slice, within_the_limit],
            event=sized, wait_s=wait_s, at=at)
    except NoLog:
        raise NoRound(name) from None
    folded = states.fold(written.round_events)
    after = _same(folded, stage_id, block)
    minutes = None
    if block.slice is not None:
        minutes = next(one.get("minutes") for one in stages.find(folded, stage_id).slices
                       if one.get("id") == block.slice)
    return Sizing(name=name, sized=after, minutes=minutes,
                  limit=None if block.slice is None else limit,
                  event=written.events[0] if written.events else None,
                  found=not written.events, failed=written.failed)


def _same(state: states.State, stage_id: str, block: BriefBlock) -> Sized | None:
    """The newest sizing of these bytes for the piece `block` names that counts for
    the build a sizing now would count for, or None."""
    found = None
    for one in _counts_now(state, stage_id):
        if (one.slice, one.verify, one.sha256) == (block.slice, block.verify,
                                                   block.sha256):
            found = one
    return found


__all__ = ["WHAT", "SIZED", "PASSED", "VERIFIES", "LIMIT", "SCHEMA", "BriefBlock",
           "ONE_SLICE", "Sized", "Sizing", "read", "piece_of", "oversized_line",
           "of_build", "slices_of", "verifications_of", "unsized", "mark_line", "size"]

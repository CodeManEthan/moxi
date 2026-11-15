"""Tracing: the rulings a design follows, and the check that it follows them all.

A design's block has one trace row for each ruling the design follows and each
commitment it makes. A row cites a standing ruling of the project (`S<n>`), a ruling of
the round (`C<n>`) or a commitment (`R<n>`), and says what became of it: `carried`,
`amended` or `cut`.

**The trace set** of a round is what its design owes a row: the round's rulings that say
what is built and that stand, and the standing rulings the round was opened with, each
as it stands now. A ruling the round was opened with that was replaced since is in the
set as the ruling that stands in its place, and a row that cites the replaced id is
stale. A ruling that only changed a record (a skip, a parking and the like) is never
in it, and neither is one that was taken back.

**The check** is run over a version's rows when the version is frozen, where the
round's project asks for it. It is arithmetic over ids, and a version passes when:

- every ruling of the trace set has a row;
- every commitment of the block has a row;
- every `amended` or `cut` row has a pointer to where the operator said so, or names a
  question of the round that is open;
- every `carried` row says where in the design it is carried;
- no row cites a ruling that was replaced or taken back. Such a row is stale, and is
  listed with the ruling that stands in its place, when one does.

`check` gives the `Result`; the freezing records it in its event, and `recorded` reads
it back. A version frozen where the check was not asked for records that. The setting
is read when a version is frozen and at no other time: the result of a version stands
as recorded, and turning the check on or off reaches a round at its next freezing.

**After a freezing.** A ruling that says what is built and has no row in the current
version holds up the stages it applies to, and no other stage, until a version cites
it: `holding` gives the rulings that hold up one stage. A ruling that replaces another
is such a ruling. A row gone stale holds up nothing by itself; `stale` lists the rows
as they read now.

**What a build is given.** `given` lists the rulings the current version cites for one
stage, each as it stands now: a cited ruling that was replaced is given as the ruling
that stands in its place, and one that was taken back is not given.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass

from . import corrections, design_block, pointers, questions, ruling, versions
from . import state as states

_STANDING = "S"
_ROUND = "C"
_EVERY = ruling.EVERY_STAGE


@dataclass(frozen=True)
class Stale:
    """A row that cites a ruling which no longer stands: the `id` the row cites, the
    ruling that stands in its place (`replaced_by`, None when none does) and the
    ruling that took it, or the last of what replaced it, back (`revoked_by`)."""

    id: str
    replaced_by: str | None = None
    revoked_by: str | None = None


@dataclass(frozen=True)
class Result:
    """What the check found of one version.

    - `asked`: whether the check was asked for when the version was frozen. Where it
      was not, nothing else is filled in.
    - `missing`: the rulings of the trace set that have no row.
    - `commitments`: the commitments that have no row.
    - `unrecorded`: the `amended` and `cut` rows with neither a pointer nor an open
      question.
    - `unplaced`: the `carried` rows that do not say where.
    - `stale`: the rows that cite a ruling which was replaced or taken back.
    """

    asked: bool = True
    missing: tuple[str, ...] = ()
    commitments: tuple[str, ...] = ()
    unrecorded: tuple[str, ...] = ()
    unplaced: tuple[str, ...] = ()
    stale: tuple[Stale, ...] = ()

    @property
    def passes(self) -> bool:
        """Whether the version passes: true also where the check was not asked for,
        since nothing then waits on it."""
        return not (self.missing or self.commitments or self.unrecorded
                    or self.unplaced or self.stale)

    def stored(self) -> dict:
        """The result as a freezing's event holds it."""
        if not self.asked:
            return {"asked": False}
        return {"asked": True, "passes": self.passes, "missing": list(self.missing),
                "commitments": list(self.commitments),
                "unrecorded": list(self.unrecorded), "unplaced": list(self.unplaced),
                "stale": [stale_data(one) for one in self.stale]}


NOT_ASKED = Result(asked=False)


def stale_data(one: Stale) -> dict:
    """A stale row as data."""
    return {"id": one.id, "replaced_by": one.replaced_by, "revoked_by": one.revoked_by}


def recorded(version: states.Version | None) -> Result | None:
    """The result recorded for `version` when it was frozen. None for no version, and
    for one whose event holds no result: it was frozen before any check was known."""
    found = None if version is None else version.trace
    if not isinstance(found, dict) or not isinstance(found.get("asked"), bool):
        return None
    if not found["asked"]:
        return NOT_ASKED

    def ids(key: str) -> tuple[str, ...]:
        return tuple(one for one in found.get(key) or () if isinstance(one, str))

    return Result(
        missing=ids("missing"), commitments=ids("commitments"),
        unrecorded=ids("unrecorded"), unplaced=ids("unplaced"),
        stale=tuple(Stale(one["id"], one.get("replaced_by"), one.get("revoked_by"))
                    for one in found.get("stale") or ()
                    if isinstance(one, dict) and isinstance(one.get("id"), str)))


# -- the set and the rows ----------------------------------------------------------------

def trace_set(state: states.State, register: states.State | None) -> tuple[str, ...]:
    """The ids the round's design owes a row: the standing rulings the round was
    opened with, each as it stands now, then the round's rulings that say what is
    built and stand. A ruling the round was opened with that was replaced since is
    owed as the ruling that stands at the end of what replaced it, and one that was
    taken back, or whose replacement was, is owed nothing. `register` is the state of
    the project's log; with None, what stands of the project's rulings is not known
    and each one the round was opened with is owed."""
    inherited = [one if register is None else _stands_as(register, one)
                 for one in state.inherits]
    inherited = [one for one in inherited if one is not None]
    own = [one.id for one in corrections.live(state.rulings)
           if one.kind == ruling.SUBSTANCE and one.id is not None]
    return tuple(dict.fromkeys([*inherited, *own]))


def rows(state: states.State, declared: Mapping | None = None) -> tuple[dict, ...]:
    """The trace rows of `declared`, what a version's block declared; the current
    version's when left out. Each is as the freezing's event holds it."""
    if declared is None:
        declared = versions.declarations(state)
    return tuple(one for one in declared.get("trace") or ()
                 if isinstance(one, dict) and isinstance(one.get("id"), str))


def check(state: states.State, register: states.State | None,
          declared: Mapping | None = None, *, asked: Iterable[str] = (),
          closed: Callable[[str], bool] | None = None) -> Result:
    """Check the rows of `declared` against the round as `state` gives it.

    - `register`: the state of the project's log, which says what stands of the
      project's rulings.
    - `declared`: what a version's block declared; the current version's when left
      out.
    - `asked`: the ids of questions to count as open beside the ones on record, for a
      block whose own questions the record does not hold yet.
    - `closed`: answers whether a round, by name, is closed, for a question parked
      until another round closes; `questions.is_closed` when left out.
    """
    if declared is None:
        declared = versions.declarations(state)
    listed = rows(state, declared)
    cited = {one["id"] for one in listed}
    open_now = set(asked) | {
        one.id for one in state.questions
        if questions.current(one, state, closed=closed) == questions.OPEN}
    committed = [one.get("id") for one in declared.get("commitment") or ()
                 if isinstance(one, dict)]
    return Result(
        missing=tuple(one for one in trace_set(state, register) if one not in cited),
        commitments=tuple(one for one in committed if one not in cited),
        unrecorded=tuple(
            one["id"] for one in listed
            if one.get("as") in (design_block.AMENDED, design_block.CUT)
            and pointers.shape(one.get("record")) is None
            and one.get("question") not in open_now),
        unplaced=tuple(one["id"] for one in listed
                       if one.get("as") == design_block.CARRIED
                       and not (one.get("where") or "").strip()),
        stale=stale(state, register, declared))


def stale(state: states.State, register: states.State | None,
          declared: Mapping | None = None) -> tuple[Stale, ...]:
    """The rows of `declared` (the current version's when left out) that cite a ruling
    which was replaced or taken back, as the logs read now."""
    found = []
    for one in rows(state, declared):
        cited = _find(state, register, one["id"])
        if cited is None or corrections.stands(cited):
            continue
        last = _last(state, register, cited)
        found.append(Stale(
            id=one["id"],
            replaced_by=last.id if corrections.stands(last) else None,
            revoked_by=last.revoked_by))
    return tuple(found)


def _log_of(state: states.State, register: states.State | None,
            ruling_id: str) -> states.State | None:
    """The log that holds a ruling of that id: the project's for a standing ruling,
    the round's for one of its own, None for a commitment."""
    if ruling_id.startswith(_STANDING):
        return register
    return state if ruling_id.startswith(_ROUND) else None


def _find(state: states.State, register: states.State | None,
          ruling_id: str) -> states.Ruling | None:
    held = _log_of(state, register, ruling_id)
    return None if held is None else corrections.find(held, ruling_id)


def _last(state: states.State, register: states.State | None,
          cited: states.Ruling) -> states.Ruling:
    """The ruling at the end of what replaced `cited`: the one that stands in its
    place, or the one that was taken back."""
    seen = {cited.id}
    while cited.replaced_by is not None and cited.replaced_by not in seen:
        later = _find(state, register, cited.replaced_by)
        if later is None:
            break
        seen.add(later.id)
        cited = later
    return cited


def _stands_as(register: states.State, ruling_id: str) -> str | None:
    """The id a standing ruling stands as now: its own, the id of the ruling that
    stands in its place, or None when nothing of it stands."""
    found = corrections.find(register, ruling_id)
    if found is None:
        return None
    last = _last(register, register, found)
    return last.id if corrections.stands(last) else None


# -- what a version's check holds up -----------------------------------------------------

def passes(state: states.State) -> bool:
    """Whether the current version passes the trace check, as recorded when it was
    frozen. A version whose check was not asked for passes, as nothing waits on a
    check that was not run; a design never frozen does not."""
    version = versions.current(state)
    if version is None:
        return False
    found = recorded(version)
    return found is None or found.passes


def is_asked(state: states.State) -> bool:
    """Whether the check was asked for when the current version was frozen."""
    found = recorded(versions.current(state))
    return found is not None and found.asked


def applies_to(one: states.Ruling, stage: str) -> bool:
    """Whether a round's ruling applies to `stage`: it names every stage, or names
    that one."""
    if one.stages == _EVERY:
        return True
    return not isinstance(one.stages, str) and stage in (one.stages or ())


def unrowed(state: states.State, stage: str) -> tuple[states.Ruling, ...]:
    """The round's rulings that say what is built, stand, apply to `stage` and have no
    row in the current version, oldest first. None for a design never frozen."""
    if versions.current(state) is None:
        return ()
    cited = {one["id"] for one in rows(state)}
    return tuple(one for one in corrections.live(state.rulings)
                 if one.kind == ruling.SUBSTANCE and one.id not in cited
                 and applies_to(one, stage))


def holding(state: states.State, stage: str) -> tuple[states.Ruling, ...]:
    """The rulings that hold up the stage `stage` until a version of the design cites
    them: what `unrowed` gives, where the check was asked for when the current
    version was frozen, and nothing where it was not. A ruling recorded after the
    freezing is such a ruling, a replacement among them; a ruling that only changed a
    record is never one, and a row gone stale holds up nothing."""
    return unrowed(state, stage) if is_asked(state) else ()


# -- what is shown ------------------------------------------------------------------------

def shown(state: states.State, register: states.State | None) -> dict | None:
    """The current version's check as data, for a listing: its `version`, the result
    as recorded when it was frozen (`asked`, None where the event holds no result,
    and with it `passes` and each list of ids), and `stale_now`, the rows that cite a
    ruling which no longer stands as the logs read now: the ones the freezing found
    and the ones corrected since. None for a design never frozen."""
    version = versions.current(state)
    if version is None:
        return None
    found = recorded(version)
    now = {one.id: one for one in (() if found is None else found.stale)}
    now.update((one.id, one) for one in stale(state, register))
    result = ({"asked": None} if found is None else
              {**Result().stored(), **found.stored()})
    return {"version": version.n, **result,
            "stale_now": [stale_data(one) for one in now.values()]}


def lines(found: Mapping | None) -> list[str]:
    """What `shown` gave, in lines: the result where the check was asked for, with
    the ids of what fails it, then each row that is stale now."""
    if found is None:
        return []
    said = []
    if found["asked"]:
        fails = [f"{what} {', '.join(found[key])}" for key, what in (
            ("missing", "no row for"),
            ("commitments", "no row for the commitment"),
            ("unrecorded", "amended or cut with no pointer and no open question:"),
            ("unplaced", "carried with no where:")) if found[key]]
        if found["stale"]:
            fails.append("stale: " + ", ".join(one["id"] for one in found["stale"]))
        said.append(f"trace: v{found['version']} "
                    + ("passes" if found["passes"] else f"fails: {'; '.join(fails)}"))
    said += [f"stale row: {stale_line(one)}" for one in found["stale_now"]]
    return said


def stale_line(one: Mapping) -> str:
    """A stale row in words: `C7, replaced by C12`."""
    if one["replaced_by"] is not None:
        return f"{one['id']}, replaced by {one['replaced_by']}"
    return f"{one['id']}, taken back by {one['revoked_by']}"


# -- what a build is given ---------------------------------------------------------------

@dataclass(frozen=True)
class Given:
    """One ruling a build is given: the `ruling` as it stands now, and `replaces`, the
    id the design's row cites when that ruling was replaced by this one, else None."""

    ruling: states.Ruling
    replaces: str | None = None


def given(state: states.State, register: states.State | None,
          stage: str) -> tuple[Given, ...]:
    """The rulings a build of `stage` is given, in the order the current version's
    rows cite them: each standing ruling and ruling of the round with a `carried` or
    `amended` row that names no stage or names this one.

    Each is given as it stands now. A cited ruling that was replaced is given as the
    ruling that stands in its place, with the id the row cites, and only when that
    ruling's own scope does not leave the stage out: a round's ruling names every
    stage or this one, a standing ruling names no repo or the repo the stage builds
    in. A cited ruling that was taken back is not given, and neither is one whose
    replacement was.
    """
    found: list[Given] = []
    seen: set[str] = set()
    for one in rows(state):
        if one.get("as") not in (design_block.CARRIED, design_block.AMENDED):
            continue
        named = one.get("stages") or ()
        if named and stage not in named:
            continue
        cited = _find(state, register, one["id"])
        if cited is None or cited.kind != ruling.SUBSTANCE:
            continue
        last = _last(state, register, cited)
        if not corrections.stands(last) or last.id in seen:
            continue
        if last is not cited and not _reaches(last, state, stage):
            continue
        seen.add(last.id)
        found.append(Given(last, replaces=None if last is cited else cited.id))
    return tuple(found)


def _reaches(one: states.Ruling, state: states.State, stage: str) -> bool:
    """Whether the scope of `one`, a ruling that replaced a cited one, names `stage`."""
    if one.stages is not None:
        return applies_to(one, stage)
    if not one.scope:
        return True
    repo = next((had.repo for had in state.stages if had.id == stage), None)
    return repo is not None and any(part.get("repo") == repo for part in one.scope)


__all__ = ["Stale", "Result", "Given", "NOT_ASKED", "trace_set", "rows", "check",
           "stale", "stale_data", "recorded", "passes", "is_asked", "applies_to",
           "unrowed", "holding", "given", "shown", "lines", "stale_line"]

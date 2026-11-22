"""Waiting items: work a build's verification can't finish without the operator, such
as a check on his phone.

A report of a build lists each as a `[[wait]]`. When its verdict is recorded, each is an
open waiting item of the round at once, keyed `B2.W1` outside the report: its stage, its
sentence, the report that declared it and who wrote that report. No later verb picks
it up. The verdict's event is the whole of it.

A waiting item does not hold a stage or the finish of a build. It holds the round's
close, and it is listed among what waits on the operator, until it is marked done with
a pointer to the report, commit or message that shows it done:

    moxi rounds mark ROUND B2.W1 --done --record PTR

`done(...)` appends that mark, one `wait.marked` event: the item's key, the state
`done` and the pointer. A mark run again for an item already done finds it and writes
nothing, so a mark stopped after its append is simply run again.

**Ids.** A report of a build restates its lists in full, so a later report of the same
build may list a waiting item an earlier one declared: the same id with the same
sentence names the same item, and adds no second one. A new item takes an id that no
earlier report of its stage used, a dropped build's included, as a new finding does.
An item keeps its key when its build is dropped: what waits on the operator still does.

**The pointer** that shows an item done is one of the five shapes, or the id of a
commit (7 to 40 hex digits) that the repo of its stage's build holds; the mark records
that commit's whole id.

**A rejected decision** owes a fix, so it is a waiting item too, `B2.M1 rejected: fix
owed`, from the ruling that rejects it until a mark after that ruling names the fix. A
rejection that is taken back or replaced by an acceptance owes nothing.

Nothing here reads a file, the config or git, except `done`, which writes the round's
log and reads git for a commit given as the pointer.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass

from . import builds, decisions, ending, findings, folders, pointers, write
from . import settings as own
from . import state as states
from .errors import BadArgument, NoLog, NoRound, RoundsError, Unmet
from .gitreads import Git

#: The event of a mark of a waiting item.
MARKED = "wait.marked"
#: The states a waiting item is in.
OPEN = "open"
DONE = "done"

#: A commit given as the pointer that shows an item done.
COMMIT = re.compile(r"[0-9a-fA-F]{7,40}")

#: A waiting item's key: `B2.W1` for one a report declared, `B2.M1` for the fix a
#: rejected decision owes.
KEY = re.compile(r"(B[1-9][0-9]*)\.([WM][1-9][0-9]*)")


@dataclass(frozen=True)
class Wait:
    """One waiting item of a round: its `stage` and `id`, its sentence, the report
    that declared it (`report`, `by` its author, `raised` the line of its verdict and
    `at` when that was recorded), its `state`, and once done the mark's pointer
    (`record`), who marked it (`done_by`), when (`done_at`) and its line
    (`set_by`)."""

    stage: str
    id: str
    text: str
    report: str | None
    by: str | None
    raised: int
    at: str
    state: str = OPEN
    record: str | None = None
    done_by: str | None = None
    done_at: str | None = None
    set_by: int | None = None

    @property
    def key(self) -> str:
        """Its key outside the report: `B2.W1`."""
        return f"{self.stage}.{self.id}"

    @property
    def open(self) -> bool:
        return self.state == OPEN

    def data(self) -> dict:
        return {"id": self.key, "stage": self.stage, "text": self.text,
                "state": self.state, "report": self.report, "by": self.by,
                "at": self.at, "raised": self.raised, "record": self.record,
                "done_by": self.done_by, "done_at": self.done_at,
                "set_by": self.set_by}


def of_round(state: states.State) -> tuple[Wait, ...]:
    """Every waiting item of the round `state` is of, in the order declared, as the
    marks left each."""
    found: dict[str, Wait] = {}
    for event in states.with_rereads(state):
        if event.get("type") == "verdict.recorded":
            stage = event.get("stage")
            listed = event.get("waits")
            for one in listed if isinstance(listed, list) else ():
                if not (isinstance(stage, str) and isinstance(one, dict)
                        and isinstance(one.get("id"), str)):
                    continue
                key = f"{stage}.{one['id']}"
                found.setdefault(key, Wait(
                    stage=stage, id=one["id"], text=str(one.get("text")),
                    report=event.get("report"), by=event.get("by"),
                    raised=event["n"], at=event["at"]))
        elif event.get("type") == MARKED and event.get("id") in found:
            was = found[event["id"]]
            if was.open and event.get("state") == DONE:
                found[event["id"]] = Wait(
                    **{**was.__dict__, "state": DONE, "record": event.get("record"),
                       "done_by": event.get("actor"), "done_at": event.get("at"),
                       "set_by": event.get("n")})
    for decision in decisions.of_round(state):
        if decision.chose != decisions.REJECT or decision.ruled is None:
            continue
        # The fix is owed from the ruling that stands: a mark made before it, of a
        # ruling since replaced, does not end it.
        mark = next((event for event in reversed(state.history)
                     if event.get("type") == MARKED and event.get("id") == decision.key
                     and event.get("state") == DONE
                     and event.get("n", 0) > decision.ruled), None)
        owed = Wait(stage=decision.stage, id=decision.id,
                    text=f"{decision.key} rejected: fix owed", report=None,
                    by=decision.ruled_by, raised=decision.ruled, at=decision.ruled_at)
        found[decision.key] = owed if mark is None else Wait(
            **{**owed.__dict__, "state": DONE, "record": mark.get("record"),
               "done_by": mark.get("actor"), "done_at": mark.get("at"),
               "set_by": mark.get("n")})
    return tuple(found.values())


def of_build(state: states.State, build: builds.Build) -> tuple[Wait, ...]:
    """The waiting items the reports of `build` declared, in the order declared."""
    reports = {one.n for one in findings.verdicts_of(state, build)}
    return tuple(one for one in of_round(state)
                 if one.report is not None and one.raised in reports)


def open_of(state: states.State) -> tuple[Wait, ...]:
    """The waiting items of the round that are still open, in the order declared."""
    return tuple(one for one in of_round(state) if one.open)


def items(state: states.State) -> Iterator[tuple[str, Mapping]]:
    """The open waiting items of `state`, each as `(key, body)` for the index: the
    stage, the sentence, the report that declared it, its author and when it was
    recorded."""
    for one in open_of(state):
        yield one.key, {"stage": one.stage, "text": one.text, "report": one.report,
                        "by": one.by, "at": one.at}


def origin_line(one: Mapping) -> str:
    """Where a waiting item, as its data gives it, came from: `from: report.md   by:
    critic   at: TIME`. A fix owed on a rejected decision came from a ruling, not a
    report, so its line has no `from:`."""
    said = [] if one.get("report") is None else [f"from: {one['report']}"]
    return "   ".join([*said, f"by: {one.get('by')}", f"at: {one.get('at')}"])


def mark_line(name: str, key: str) -> str:
    """The command that marks a waiting item done."""
    return f"moxi rounds mark {name} {key} --done --record PTR"


@dataclass(frozen=True)
class Done:
    """A waiting item after its mark: the round's `name`, the item as the log now gives
    it, and the `event` written, None when the item was done already and nothing was
    written (`found`). `failed` pairs each after-append callback that raised with what
    it raised."""

    name: str
    wait: Wait
    event: dict | None
    found: bool = False
    failed: tuple[tuple[Callable, Exception], ...] = ()


def done(target: str, key: str, *, record: str | None, by: str, settings=None,
         wait_s: float = write.WAIT_S, at: str | None = None) -> Done:
    """Mark the waiting item `key`, `B<n>.W<k>`, of the round `target` done, with
    `record` the pointer to what shows it done.

    Raises `BadArgument` for a key that can't be one; `UnknownProject`, `NoFolder` or
    `NoRound` for a round that can't be found; and `Refused` when the round has ended,
    it holds no such waiting item, or the pointer is missing or has no known shape. A
    refusal leaves the log as it was. An item done already is returned with `found`
    set, and nothing is written."""
    if KEY.fullmatch(key or "") is None:
        raise BadArgument(f"{key!r} is not the key of a waiting item",
                          "name the waiting item as show lists it: B<n>.W<k>, or "
                          "B<n>.M<k> for a rejected decision's fix")
    project, slug = folders.split(target)
    settings = own.load() if settings is None else settings
    home = folders.home(project, settings)
    name = f"{home.project}/{slug}"
    again = mark_line(name, key)

    def find(view) -> Wait | None:
        return next((one for one in of_round(view.state) if one.key == key), None)

    def held(view):
        if find(view) is None:
            return Unmet(f"{name} holds no waiting item {key}",
                         f"moxi rounds show {name}")
        return None

    def has_a_pointer(view):
        if record is None:
            return Unmet("a waiting item is marked done with a pointer to the report, "
                         f"commit or message that shows it done; PTR below is one "
                         f"token: {pointers.SHAPES}, or a commit's id", again)
        if COMMIT.fullmatch(record):
            whole = _commit(view.state, key, record)
            return whole if isinstance(whole, Unmet) else None
        return pointers.unmet(record, again, names="what shows it done")

    def marked(view, result):
        if not find(view).open:
            return None
        pointer = record
        if COMMIT.fullmatch(record):
            pointer = _commit(view.state, key, record)
        return write.Entry(MARKED, {"id": key, "state": DONE, "record": pointer})

    try:
        written = write.record(
            write.Where(home.project, home.rounds_dir, slug), by=by,
            gates=[ending.takes_new_work(name, "a mark"), held, has_a_pointer],
            event=marked, wait_s=wait_s, at=at)
    except NoLog:
        raise NoRound(name) from None
    after = next(one for one in of_round(states.fold(written.round_events))
                 if one.key == key)
    if not written.events:
        return Done(name=name, wait=after, event=None, found=True, failed=written.failed)
    return Done(name=name, wait=after, event=written.events[0], failed=written.failed)


def _commit(state: states.State, key: str, given: str) -> str | Unmet:
    """The whole id of the commit `given` names in the repo of the build of the stage
    of the item `key`, or what is unmet when that repo holds no such one commit."""
    stage = key.split(".", 1)[0]
    build = builds.current(state, stage)
    if build is None:
        return Unmet(f"{stage} has no build, so there is no repo to find the commit "
                     f"{given} in", "give a pointer to what shows the item done")
    try:
        _key, root = folders.root(build.repo)
        whole = Git(root).commit_of(given)
    except RoundsError as exc:
        return Unmet(exc.reason, exc.fix or "give a pointer to what shows it done")
    if whole is None:
        return Unmet(f"{given} names no one commit that {build.repo} holds",
                     "give the id of the commit that shows the item done, as git log "
                     "prints it")
    return whole


__all__ = ["MARKED", "OPEN", "DONE", "COMMIT", "KEY", "Wait", "of_round", "of_build",
           "open_of", "items", "origin_line", "mark_line", "Done", "done"]

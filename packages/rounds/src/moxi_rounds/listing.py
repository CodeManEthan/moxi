"""Listings of many rounds: the rounds there are, and what waits on the operator.

Both are computed when they are asked for and record nothing. Each brings the index up
to the logs first, when it can, and then reads from it; where it can't, the logs
themselves answer. A caller that must write nothing at all says `refresh=False`: the
index is then left as it is, and the answer is the same.

**The rounds** (`rounds`): the open rounds of every project, or of one, each with its
flow, the step it is at and its counts; or, when the ended ones are asked for, the
rounds that are open no longer, each with how it ended and when, and for a dropped
one by whom and on what pointer. A round whose
log can't be read is listed as unreadable, in either listing,
since nothing says which it belongs in.

**What waits on the operator** (`waiting`): across every project, or in one, each open
question, each parked question with what it waits for, each open waiting item of an
open round, each major decision of one not yet ruled, each project's rulings not yet
shown to him, and each open build of its rounds that is idle, gone, empty or unclear,
that shares commits with another, or that git could not read. The question rows are the ones a project's questions export holds,
from the same function, so the two never differ. A waiting item is no question, and is
in no export.

**The numbers** (`numbers`): the numbers of every closed round, or of one project's,
summed key by key, read from what each round's closing event holds as the index keeps
it. A dropped round's numbers stay in its drop and its `retro.md`, and are not summed.
A closed round whose closing holds no numbers is named and not summed, and so is a
round whose log can't be read.

**A later kind of record** joins a listing in one place. A count of the rounds listing
is a row of `COUNTS`. A kind of row that waits on the operator is a row of `WAITING`:
what one project gives, how the rows read as data, and how they read as lines.
"""

from __future__ import annotations

import dataclasses
import json
import sqlite3
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from moxi import store

from . import (builds, checking, decisions, export, folders, index, log, open_builds,
               questions, shown, steps, waits)
from . import numbers as counted
from . import settings as own
from . import state as states
from .errors import BadArgument, Refused, RoundsError, Unmet, Unreadable


@dataclass(frozen=True)
class Project:
    """What one project gives a listing, read once: its rounds folder, its own log and
    its rounds as the index answers for them, its question rows, and its rulings not
    yet shown. The last two are None when the project's own log can't be read, and
    `unreadable` then holds its error."""

    home: folders.Home
    rounds: tuple[index.Summary, ...] = ()
    rows: export.Rows | None = None
    back: shown.ReadBack | None = None
    unreadable: Unreadable | None = None
    settings: object = None
    builds: tuple = ()


# -- the rounds --------------------------------------------------------------------------

def _open(summary: index.Summary, project: Project) -> int:
    return sum(entry.state == questions.OPEN
               for entry in export.of_log(summary, settings=project.settings))


def _parked(summary: index.Summary, project: Project) -> int:
    return sum(entry.state == questions.PARKED
               for entry in export.of_log(summary, settings=project.settings))


def _not_shown(summary: index.Summary, project: Project) -> int | None:
    if project.back is None:
        # Whether a ruling is shown is in the project's own log too, which can't be
        # read: the count is not known, and is not given as none.
        return None
    return sum(row.holder == summary.name for row in project.back.rows)


#: What is counted for each round of the rounds listing: the key of the count as
#: data, what it is called in a line, and the function that counts it from the round's
#: summary and its project. A count that can't be known is None.
COUNTS: tuple[tuple[str, str, Callable[[index.Summary, Project], int | None]], ...] = (
    ("open", "open questions", _open),
    ("parked", "parked", _parked),
    ("not_shown", "rulings not shown", _not_shown))


def _step(summary: index.Summary, project: Project,
          opened: Callable[[], open_builds.OpenBuilds]) -> dict | None:
    """The step the round is at, as `{"step", "stage", "state"}`, or None for a round
    at no step. The index keeps it as the round's own log gives it. The log is read
    again where that is not the answer: where the step is one whose state git says,
    which is read then with the open builds `opened` gives; and where a question of
    the round is parked until another round closes and that round has, since the
    question reads as open from then on and may hold the round at an earlier step."""
    kept = next((item.body for item in summary.of(index.STEP)), None)
    if kept is None:
        return None

    def on_closed(name: str) -> bool:
        return questions.is_closed(name, project.settings)

    if kept.get("git") or any(closed(name) for name in kept.get("until", ())):
        try:
            folded = states.fold(log.read_round(log.round_log(summary.log_dir)))
        except RoundsError:
            return None
        read = {one.build.stage: one for one in opened().builds
                if one.round == summary.name}
        seen = checking.seen_of(folded, summary.name, settings=project.settings,
                                read=read)
        at = steps.current(folded, name=summary.name, closed=closed, seen=seen)
        kept = {} if at is None else {"step": at.step, "stage": at.stage,
                                      "state": at.state}
    if kept.get("step") is None:
        return None
    return {key: kept[key] for key in ("step", "stage", "state")}


@dataclass(frozen=True)
class Round:
    """One round of the rounds listing. One that can't be read has its error in
    `unreadable` and no status, flow or counts."""

    name: str
    project: str
    round_dir: str
    status: str | None = None
    flow: str | None = None
    #: The step it is at, as `{"step", "stage", "state"}`: the first of its flow that
    #: is neither done nor skipped. None for a round at no step, and one that ended.
    step: Mapping | None = None
    counts: tuple[tuple[str, int | None], ...] = ()
    #: What the verification of its design stands at, for a round whose design was
    #: frozen; None for one with no version.
    standing: str | None = None
    unreadable: Unreadable | None = None
    #: How a round that is open no longer ended, as the index keeps it: the status it
    #: has since (`how`), whose act it was (`by`), the pointer it carried (`record`)
    #: and when (`at`). None for an open round.
    ended: Mapping | None = None

    def data(self) -> dict:
        if self.unreadable is not None:
            return {"name": self.name, "project": self.project, "folder": self.round_dir,
                    "unreadable": _error(self.unreadable)}
        return {"name": self.name, "project": self.project, "folder": self.round_dir,
                "status": self.status, "flow": self.flow,
                "step": None if self.step is None else dict(self.step),
                "counts": dict(self.counts),
                "standing": self.standing,
                "ended": None if self.ended is None else dict(self.ended)}


@dataclass(frozen=True)
class Rounds:
    """The rounds listing: `project` is the one it was kept to, or None; `ended` says
    it lists the rounds that are open no longer. `unreadable` holds a line for each
    project whose own log can't be read, and `unplaced` one for each project whose
    rounds folder can't be worked out, whose rounds are therefore not listed."""

    computed: str
    project: str | None
    ended: bool
    rounds: tuple[Round, ...]
    unreadable: tuple[str, ...] = ()
    unplaced: tuple[str, ...] = ()

    def data(self) -> dict:
        return {"computed": self.computed, "project": self.project,
                "ended": self.ended, "rounds": [one.data() for one in self.rounds],
                "unreadable": list(self.unreadable), "no_folder": list(self.unplaced)}


def rounds(project: str | None = None, *, ended: bool = False, settings=None,
           now: str | None = None, refresh: bool = True) -> Rounds:
    """The open rounds of every project, or of `project`, each with its flow and its
    counts; with `ended`, the rounds that are open no longer. `settings` is what
    `settings.load()` gives, read now when left out, and `now` the second the listing
    is computed in. With `refresh` false the index is left as it is, and a round it
    is behind on is read from its log.

    Raises `Refused` when `project` names a round, and `UnknownProject` or `NoFolder`
    for a project with no rounds folder."""
    now = log.stamp() if now is None else now
    found, no_folder = _projects(project, settings, now, refresh=refresh)
    looked: list[open_builds.OpenBuilds] = []

    def opened() -> open_builds.OpenBuilds:
        # Read once, the first time a round's step is one git says.
        if not looked:
            looked.append(open_builds.read(
                settings=found[0].settings if found else settings, now=now))
        return looked[0]

    listed = []
    for one in found:
        for summary in one.rounds:
            if summary.unreadable is not None:
                listed.append(Round(summary.name, summary.project, str(summary.log_dir),
                                    unreadable=summary.unreadable))
            elif (summary.status not in (states.OPEN, None)) == ended:
                listed.append(Round(
                    summary.name, summary.project, str(summary.log_dir),
                    status=summary.status, flow=summary.flow, ended=summary.ended,
                    step=_step(summary, one, opened),
                    standing=next((item.body.get("standing")
                                   for item in summary.of(index.VERSION)), None),
                    counts=tuple((key, count(summary, one))
                                 for key, _called, count in COUNTS)))
    return Rounds(computed=now, project=None if project is None else found[0].home.project,
                  ended=ended, rounds=tuple(listed),
                  unreadable=tuple(one.unreadable.reason for one in found
                                   if one.unreadable is not None),
                  unplaced=unplaced)


def rounds_as_text(listing: Rounds) -> str:
    """The rounds listing as lines, ending in a line break."""
    called = {key: name for key, name, _count in COUNTS}
    lines = []
    for one in listing.rounds:
        if one.unreadable is not None:
            lines.append(f"{one.name}   unreadable: {one.unreadable.reason}")
            continue
        counts = "   ".join(f"{called[key]}: {'not known' if count is None else count}"
                            for key, count in one.counts)
        how = one.status
        if one.ended is not None and one.status == states.CLOSED:
            # Closing is nobody's act and carries no pointer.
            how = f"{one.status} at {one.ended.get('at')}"
        elif one.ended is not None:
            how = (f"{one.status} by {one.ended.get('by')} at {one.ended.get('at')}, "
                   f"record {one.ended.get('record')}")
        design = "" if one.standing is None else f"   design: {one.standing}"
        step = "" if one.ended is not None else f"   step {steps.said(one.step)}"
        lines.append(f"{one.name}   {how}   flow {one.flow}{step}{design}   {counts}")
    if not listing.rounds:
        kind = "has ended" if listing.ended else "is open"
        where = "" if listing.project is None else f" of {listing.project}"
        lines.append(f"no round{where} {kind}")
    lines += _trouble(listing.unreadable, listing.unplaced)
    return "\n".join(lines) + "\n"


# -- what waits on the operator ----------------------------------------------------------

@dataclass(frozen=True)
class Kind:
    """One kind of row that waits on the operator: the `key` its rows have as data,
    the `title` they are listed under, what one project gives (`rows`), the rows as
    data (`data`), and the rows as lines, given the second the listing is computed in
    (`lines`)."""

    key: str
    title: str
    rows: Callable[[Project], Sequence]
    data: Callable[[Sequence], list]
    lines: Callable[[Sequence, str], list[str]]


def _questions_in(state: str) -> Callable[[Project], Sequence]:
    return lambda project: [entry for entry in project.rows.entries
                            if entry.state == state and not entry.decision]


def _decisions(project: Project) -> Sequence:
    return [entry for entry in project.rows.entries if entry.decision]


def _decision_lines(entries: Sequence, now: str) -> list[str]:
    lines = []
    for entry in entries:
        weight = entry.weight + (", forecloses" if entry.forecloses else "")
        lines += [f"  {entry.holder} {entry.id}: {entry.text}",
                  f"    from: {entry.origin}   {weight}   by: {entry.by}   at: "
                  f"{entry.at}   age: {age(entry.at, now)}",
                  f"    his to rule: {decisions.rule_line(entry.holder, entry.id)}"]
    return lines


def _entries_data(entries: Sequence) -> list:
    return [entry.data() for entry in entries]


def _question_lines(entries: Sequence, now: str) -> list[str]:
    lines = []
    for entry in entries:
        said = [f"from: {entry.origin}", f"blocks: {questions.said(entry.blocks)}",
                f"state: {entry.state}",
                f"by: {questions.asker(entry.by, entry.builder)}", f"at: {entry.at}"]
        if entry.state == questions.PARKED:
            said.append(f"until: {entry.until or 'nothing named'}")
        else:
            keys = ", ".join(option.key for option in entry.options)
            said += [f"age: {age(entry.at, now)}", f"options: {keys or 'none'}"]
        if entry.moved_from is not None:
            was = ("" if entry.held is None
                   else f", where it held up {questions.said(entry.held)}")
            said.append(f"moved from: {entry.moved_from}{was}")
        lines += [f"  {entry.holder} {entry.id}: {entry.text}",
                  f"    {'   '.join(said)}"]
    return lines


def _lists(project: Project) -> Sequence:
    return [project.back] if project.back.rows else []


def _lists_data(lists: Sequence) -> list:
    return [{"project": back.project, **export.read_back_data(back)} for back in lists]


def _list_lines(lists: Sequence, now: str) -> list[str]:
    lines = []
    for back in lists:
        lines.append(f"  {back.project}, {back.heading}; once he has read them: moxi "
                     f"rounds rule {back.project} {shown.READBACK}{back.through} "
                     "--record PTR")
        for row in back.rows:
            line = (f"    {row.holder} {row.id}: "
                    f"{row.rules if row.text is None else row.text} "
                    f"(record: {row.record}")
            if row.supersedes is not None:
                line += f"; replaces {row.supersedes}"
            if row.revokes is not None:
                line += f"; takes back {row.revokes}"
            lines.append(line + ")")
    return lines


@dataclass(frozen=True)
class WaitRow:
    """An open waiting item of an open round, as the index keeps it: the `round` that
    holds it, its key and the item's body."""

    round: str
    id: str
    body: Mapping

    def data(self) -> dict:
        return {"round": self.round, "id": self.id, **dict(self.body)}


def _waits(project: Project) -> Sequence:
    return [WaitRow(summary.name, item.id, item.body) for summary in project.rounds
            if summary.unreadable is None and summary.status == states.OPEN
            for item in summary.of(index.WAIT)]


def _wait_lines(rows: Sequence, now: str) -> list[str]:
    lines = []
    for row in rows:
        body = row.body
        lines += [f"  {row.round} {row.id}: {body.get('text')}",
                  f"    from: {body.get('report')}   by: {body.get('by')}   at: "
                  f"{body.get('at')}   age: {age(body.get('at'), now)}",
                  f"    once done: {waits.mark_line(row.round, row.id)}"]
    return lines


def _builds_to_look_at(project: Project) -> Sequence:
    return project.builds


def _builds_data(listed: Sequence) -> list:
    return [one.data() for one in listed]


def _build_lines(listed: Sequence, now: str) -> list[str]:
    lines = []
    for one in listed:
        said = one.data()
        if one.problem is not None:
            what = f"not read: {one.problem.reason}"
        else:
            what = [said["state"]] + (["idle"] if one.idle else [])
            what = ", ".join(what)
            if one.shares:
                what += ", shares with " + ", ".join(f"{name} {stage}"
                                                     for name, stage in one.shares)
            what += f"; the {said['drop_by']}'s to drop"
        lines.append(f"  {one.round} {one.build.stage}: {one.build.repo} on "
                     f"{one.build.branch}; {what}")
    return lines


#: The states of an open build that a person looks at: what is there may not be
#: what the build was for.
LOOK_AT = (builds.GONE, builds.EMPTY, builds.UNCLEAR)


def _to_look_at(one) -> bool:
    return (one.problem is not None or one.idle or bool(one.shares)
            or one.status.state in LOOK_AT)


#: The kinds of row that wait on the operator, in the order they are listed. The two
#: kinds of question are the rows a project's questions export holds.
WAITING: tuple[Kind, ...] = (
    Kind("open", "open questions", _questions_in(questions.OPEN), _entries_data,
         _question_lines),
    Kind("parked", "parked questions, not put to him", _questions_in(questions.PARKED),
         _entries_data, _question_lines),
    Kind("waits", "open waiting items", _waits, _builds_data, _wait_lines),
    Kind("decisions", "major decisions not yet ruled", _decisions, _entries_data,
         _decision_lines),
    Kind("not_shown", "rulings not yet shown to him", _lists, _lists_data, _list_lines),
    Kind("builds", "builds idle, gone, empty, unclear, sharing or not read",
         _builds_to_look_at, _builds_data, _build_lines))


@dataclass(frozen=True)
class Waiting:
    """What waits on the operator: `found` holds the rows of each kind of `WAITING`,
    under the kind's key; `project` is the one the listing was kept to, or None.
    `unreadable` holds a line for each log that can't be read, whose rows are not
    here, and `unplaced` one for each project whose rounds folder can't be worked
    out."""

    computed: str
    project: str | None
    found: tuple[tuple[str, tuple], ...]
    unreadable: tuple[str, ...] = ()
    unplaced: tuple[str, ...] = ()

    def data(self) -> dict:
        kinds = {kind.key: kind for kind in WAITING}
        return {"computed": self.computed, "project": self.project,
                **{key: kinds[key].data(rows) for key, rows in self.found},
                "unreadable": list(self.unreadable), "no_folder": list(self.unplaced)}


def waiting(project: str | None = None, *, settings=None,
            now: str | None = None, refresh: bool = True) -> Waiting:
    """What waits on the operator across every project, or in `project`. `settings` is
    what `settings.load()` gives, read now when left out, and `now` the second the
    listing is computed in, which every project's list of rulings not yet shown is
    computed in too. With `refresh` false the index is left as it is, and a log it is
    behind on is read itself.

    Raises `Refused` when `project` names a round, and `UnknownProject` or `NoFolder`
    for a project with no rounds folder."""
    now = log.stamp() if now is None else now
    found, unplaced = _projects(project, settings, now, refresh=refresh)
    readable = [one for one in found if one.unreadable is None]
    looked = open_builds.read(settings=settings, now=now)
    readable = [dataclasses.replace(one, builds=tuple(
        build for build in looked.builds
        if build.round.partition("/")[0] == one.home.project and _to_look_at(build)))
        for one in readable]
    unreadable = [one.unreadable.reason for one in found if one.unreadable is not None]
    for one in readable:
        unreadable += [error.reason
                       for error in (*one.rows.unreadable, *one.back.unreadable)]
    return Waiting(
        computed=now, project=None if project is None else found[0].home.project,
        found=tuple((kind.key, tuple(row for one in readable for row in kind.rows(one)))
                    for kind in WAITING),
        # One line for a log, whichever reading could not read it.
        unreadable=tuple(dict.fromkeys(unreadable)), unplaced=unplaced)


def waiting_as_text(listing: Waiting) -> str:
    """What waits on the operator as lines, ending in a line break."""
    kinds = {kind.key: kind for kind in WAITING}
    where = "" if listing.project is None else f" in {listing.project}"
    lines = [f"waiting on the operator{where}, computed {listing.computed}"]
    for key, rows in listing.found:
        lines += ["", f"{kinds[key].title}:"]
        lines += kinds[key].lines(rows, listing.computed) if rows else ["  none"]
    trouble = _trouble(listing.unreadable, listing.unplaced)
    return "\n".join([*lines, *([""] if trouble else []), *trouble]) + "\n"


def age(at: str | None, now: str) -> str:
    """How long ago `at` was, as of `now`, both as a log writes a time: in whole days,
    hours or minutes, whichever is the largest that is not none."""
    try:
        passed_s = (_time(now) - _time(at)).total_seconds()
    except (TypeError, ValueError):
        return "not known"
    for unit, length_s in (("day", 86400), ("hour", 3600), ("minute", 60)):
        count = int(passed_s // length_s)
        if count >= 1:
            return f"{count} {unit}{'' if count == 1 else 's'}"
    return "under a minute"


def _time(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ")


# -- the numbers -------------------------------------------------------------------------

@dataclass(frozen=True)
class Numbers:
    """The numbers of the closed rounds summed: `project` is the one the listing was
    kept to, or None; `rounds` the closed rounds summed, by name; `uncounted` the
    closed rounds whose closing holds no numbers; `unreadable` a line for each log
    that can't be read, and `no_folder` one for each project whose rounds folder
    can't be worked out, whose rounds are therefore not summed."""

    computed: str
    project: str | None
    rounds: tuple[str, ...]
    numbers: Mapping
    uncounted: tuple[str, ...] = ()
    unreadable: tuple[str, ...] = ()
    no_folder: tuple[str, ...] = ()

    def data(self) -> dict:
        return {"computed": self.computed, "project": self.project,
                "rounds": list(self.rounds), "numbers": dict(self.numbers),
                "uncounted": list(self.uncounted), "unreadable": list(self.unreadable),
                "no_folder": list(self.no_folder)}


def numbers(project: str | None = None, *, settings=None, now: str | None = None,
            refresh: bool = True) -> Numbers:
    """The numbers of the closed rounds of every project, or of `project`, summed.
    `settings`, `now` and `refresh` are as `rounds` takes them, and so are its
    refusals."""
    now = log.stamp() if now is None else now
    homes, no_folder, _settings = _homes(project, settings, refresh=refresh)
    summed, uncounted, unreadable = [], [], []
    for home in homes:
        for summary in index.rounds(home.project, home.folder):
            if summary.unreadable is not None:
                unreadable.append(summary.unreadable.reason)
            elif summary.status == states.CLOSED:
                held = (summary.ended or {}).get("numbers")
                if isinstance(held, Mapping):
                    summed.append((summary.name, held))
                else:
                    uncounted.append(summary.name)
    return Numbers(computed=now, project=None if project is None else homes[0].project,
                   rounds=tuple(name for name, _held in summed),
                   numbers=counted.summed(held for _name, held in summed),
                   uncounted=tuple(uncounted), unreadable=tuple(unreadable),
                   no_folder=no_folder)


def numbers_as_text(listing: Numbers) -> str:
    """The numbers listing as lines, ending in a line break."""
    where = "" if listing.project is None else f" of {listing.project}"
    if listing.rounds:
        count = len(listing.rounds)
        lines = [f"numbers of {count} closed round{'' if count == 1 else 's'}{where}",
                 *(f"  {line}" for line in counted.lines(listing.numbers))]
    else:
        lines = [f"no round{where} has closed with its numbers"]
    if listing.uncounted:
        lines.append("closed with no numbers, not summed: "
                     f"{', '.join(listing.uncounted)}")
    lines += _trouble(listing.unreadable, listing.no_folder)
    return "\n".join(lines) + "\n"


# -- both listings -----------------------------------------------------------------------

def as_json(listing: Rounds | Waiting | Numbers) -> str:
    """A listing as JSON text, ending in a line break."""
    return json.dumps(listing.data(), indent=2, ensure_ascii=False) + "\n"


def _projects(project: str | None, settings, now: str, *,
              refresh: bool = True) -> tuple[list[Project], tuple[str, ...]]:
    """What each project of a listing gives, and a line for each project whose rounds
    folder can't be worked out. With `project`, that one alone. The index is brought
    up to every project's logs first, unless `refresh` is false: the reads below
    answer the same from an index that is behind, and write nothing."""
    homes, no_folder, settings = _homes(project, settings, refresh=refresh)
    return [_project(home, settings, now) for home in homes], no_folder


def _homes(project: str | None, settings, *,
           refresh: bool = True) -> tuple[list[folders.Home], tuple[str, ...], object]:
    """The rounds folder of each project of a listing, or of `project` alone, a line
    for each project whose rounds folder can't be worked out, and the settings read.
    The index is brought up to every project's logs first, unless `refresh` is
    false."""
    if project is not None and (not isinstance(project, str) or not project.strip()):
        raise BadArgument("the project to keep the listing to has no name",
                          "name a project by its key, or leave it out")
    if project is not None and "/" in project:
        raise Refused([Unmet(
            f"{project} is a round, and this lists the rounds of every project or of "
            "one: a round is shown on its own, and only its questions are listed",
            f"moxi rounds show {project}")])
    settings = own.load() if settings is None else settings
    homes, unresolved = folders.homes(settings)
    if refresh:
        try:
            index.rebuild(homes)
        except (sqlite3.Error, OSError, store.StoreError):
            # The index is a cache: what can't be brought up to date is read from the
            # logs.
            pass
    if project is not None:
        homes, unresolved = [folders.home(project, settings)], []
    return homes, tuple(error.reason for error in unresolved), settings


def _project(home: folders.Home, settings, now: str) -> Project:
    found = tuple(index.rounds(home.project, home.rounds_dir))
    try:
        return Project(home, found, export.waiting(home, settings=settings),
                       shown.unshown(home.project, settings=settings, now=now),
                       settings=settings)
    except Unreadable as exc:
        return Project(home, found, unreadable=exc, settings=settings)


def _trouble(unreadable: Sequence[str], unplaced: Sequence[str]) -> list[str]:
    lines = []
    if unreadable:
        lines += ["logs that can't be read, whose records are not listed:",
                  *(f"  {reason}" for reason in unreadable)]
    if unplaced:
        lines += ["projects whose rounds folder can't be worked out:",
                  *(f"  {reason}" for reason in unplaced)]
    return lines


def _error(error: RoundsError) -> dict:
    return {"reason": error.reason, "fix": error.fix}


__all__ = ["Project", "COUNTS", "Round", "Rounds", "rounds", "rounds_as_text", "Kind",
           "WAITING", "Waiting", "waiting", "waiting_as_text", "Numbers", "numbers",
           "numbers_as_text", "age", "as_json"]

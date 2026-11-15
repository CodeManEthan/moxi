"""The questions export: the questions of one round or one project that wait on the
operator, written out as markdown and as data.

rounds draws nothing. Whoever puts the questions to the operator (a lead in session,
someone reading aloud, a page) reads this export, and each answer comes back as a
ruling of its own. The export is computed when it is asked for, from one reading of the
state. It writes nothing to a log or to the index, and nothing reads an export back.

**An entry** is one question at the depth it has: its id and the round or project that
holds it, the sentence, where it came from, what it holds up, its state as it reads now,
who asked it and when; then, each only when the question has it, what a parked one waits
for, the options it offers now, and the record it was moved from with what it held up
there. A bare question is written out bare.

**A round's export** has two views, each a filter on the one kind of record, and with
no view it holds both, the docket first:

- **The docket**: every question of the round that reads as open, whatever else is
  set, and after them the parked ones, which are not put to him.
- **The ratification**: the decisions not yet ruled of the builds that are finished
  when the export is computed. It names those builds under `covers`. What it puts to
  him is the project's `ratify` setting: `majors` (the default) each major decision,
  each an entry of its own; `majors+minors` adds the minor ones as one group, whose
  entries carry `group` and are drawn together under one heading that lists their
  ids; `off` none. A group is a way to draw, not a thing to answer: each id in it is
  ruled on its own. The major decisions of builds that are not finished are listed
  under `later`, which are no entries: they come in a later export, once their build
  is finished. Nothing here waits on a build that is not finished, and no ruling
  waits on an export.

A decision that forecloses a capability is major, and wherever it is written out it
is an entry of its own: its data is marked `alone`, the markdown gives it a heading
of its own, and it is in no group and no summary line.

**A round that has ended** has an empty docket, and its export says how it ended. A
question whose record still reads as open or parked there (a correction made after the
end can leave one so) is listed apart, as waiting for a new round, and is no entry.

**A project's export** holds every open and every parked question on the project's list
and in each open round of the project, and every major decision of those rounds not yet
ruled, each entry naming its holder. A decision's entry adds its stage, its weight and
whether it forecloses; one that forecloses is marked `alone`, an entry of its own. `waiting` gives
those rows, and is the one function anything that lists what waits in a project takes
its question rows from.

Each export ends with the project's list of rulings not yet shown to the operator,
headed with the time it runs through. That list is no entry and takes no answer.

**A later kind of row** (a decision, say) joins in two places: its kind in the index's
`ITEMS`, and here in `ENTRIES` the function that makes an entry from the index's item.
**A later field of an entry** is added to the body the index keeps for the item, to
`Entry`, to `Entry.data` and to `_entry_lines`.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path

from moxi import files

from . import decisions, folders, index, log, questions, report_block, shown
from . import settings as own
from . import state as states
from .errors import (BadArgument, NoLog, NoRound, Refused, RoundsError, SharedFolder,
                     Unmet, Unreadable)

#: The view of a round's export that holds its open questions, the view that holds
#: the decisions of its finished builds, and the views there are.
DOCKET = "docket"
RATIFY = "ratify"
VIEWS = (DOCKET, RATIFY)

#: The values of the project's `ratify` setting: what the ratification puts to him.
MAJORS = "majors"
MAJORS_AND_MINORS = "majors+minors"
OFF = "off"
#: The group the minor decisions are drawn in, under `majors+minors`.
MINORS = "minors"

#: The names of the two files an export is written to.
MARKDOWN_FILE = "questions.md"
JSON_FILE = "questions.json"


@dataclass(frozen=True)
class Option:
    """One option a question offers now: its key and its one line."""

    key: str
    text: str


@dataclass(frozen=True)
class Entry:
    """One question as an export gives it."""

    #: The round, `<project>/<slug>`, or the project whose list holds it. With `id` it
    #: names the question, and is what a ruling of it is recorded on.
    holder: str
    id: str
    #: The one sentence that asks it.
    text: str | None
    #: Where it came from: a pointer.
    origin: str | None
    #: What it holds up: `"all"`, `"none"`, or stage ids.
    blocks: str | tuple[str, ...] | None
    #: `open` or `parked`, as it reads now; `left` for one of a round that has ended.
    state: str
    #: Who asked it, and when.
    by: str | None
    at: str | None
    #: What a parked question waits for, or None.
    until: str | None = None
    #: The options it offers now, in the order they were added; empty for a question
    #: that has none.
    options: tuple[Option, ...] = ()
    #: The record it was moved here from, and what it held up there, or None.
    moved_from: str | None = None
    held: str | tuple[str, ...] | None = None
    #: What the record of a `left` question reads as, `open` or `parked`, or None.
    left_as: str | None = None
    #: The builder of the build it came up in, whom `by` asked it for, or None.
    builder: str | None = None
    #: For a decision: its stage, its weight and whether it forecloses a capability;
    #: None for a question.
    stage: str | None = None
    weight: str | None = None
    forecloses: bool = False
    #: The group it is drawn in (`MINORS`), or None for an entry of its own.
    group: str | None = None

    @property
    def decision(self) -> bool:
        return self.weight is not None

    def data(self) -> dict:
        """The entry as data: the fields every question has, then each further one only
        when the question has it."""
        data = {"holder": self.holder, "id": self.id, "text": self.text,
                "from": self.origin,
                "blocks": None if self.blocks is None else questions.stored(self.blocks),
                "state": self.state, "by": self.by, "at": self.at}
        if self.builder is not None:
            data["builder"] = self.builder
        if self.left_as is not None:
            data["left_as"] = self.left_as
        if self.until is not None:
            data["until"] = self.until
        if self.options:
            data["options"] = [{"key": option.key, "text": option.text}
                               for option in self.options]
        if self.moved_from is not None:
            data["moved_from"] = self.moved_from
        if self.held is not None:
            data["held"] = questions.stored(self.held)
        if self.decision:
            data.update({"stage": self.stage, "weight": self.weight,
                         "forecloses": self.forecloses})
            if self.forecloses:
                # An entry of its own wherever it is drawn: in no group or summary.
                data["alone"] = True
        if self.group is not None:
            data["group"] = self.group
        return data


@dataclass(frozen=True)
class Rows:
    """Question rows: the `entries`, the open ones first and the parked ones after, each
    group in the order of its logs; and the error of each round whose log could not be
    read, whose questions are therefore not among them. The rows of one round that has
    ended are none: `ended` says how it ended, as the index keeps it, and `left` holds
    its questions whose records still read as open or parked. A round's rows that
    hold its ratification give the stages of the builds it covers in `covers`, and in
    `later` the major decisions of builds not yet finished."""

    entries: tuple[Entry, ...]
    unreadable: tuple[Unreadable, ...] = ()
    ended: Mapping | None = None
    left: tuple[Entry, ...] = ()
    covers: tuple[str, ...] | None = None
    later: tuple[Entry, ...] = ()


@dataclass(frozen=True)
class Export:
    """One export: `name` is the round or the project it is of, `round` the round's name
    or None for a project's, `view` the view of a round's export, None for both views
    and for a project's, `computed` the second it was computed in, and `read_back` the
    project's list of rulings not yet shown. `unreadable` says, a line each, which
    logs could not be read. For a round that has ended, `ended` says how (`how`, `by`,
    `record`, `at`) and `left` holds its questions that wait for a new round, which
    are no entries. An export that holds the ratification gives the project's `ratify`
    setting, the stages of the finished builds it `covers`, and the decisions put off
    to a `later` one; for any other these are None, None and empty."""

    name: str
    project: str
    round: str | None
    view: str | None
    computed: str
    entries: tuple[Entry, ...]
    read_back: shown.ReadBack
    unreadable: tuple[str, ...] = ()
    ended: Mapping | None = None
    left: tuple[Entry, ...] = ()
    ratify: str | None = None
    covers: tuple[str, ...] | None = None
    later: tuple[Entry, ...] = ()

    @property
    def has_docket(self) -> bool:
        """Whether it holds the questions: a project's export, or a round's docket."""
        return self.round is None or self.view != RATIFY

    @property
    def has_ratification(self) -> bool:
        """Whether it holds a round's ratification."""
        return self.round is not None and self.view != DOCKET

    @property
    def open(self) -> tuple[Entry, ...]:
        return tuple(entry for entry in self.entries
                     if entry.state == questions.OPEN and not entry.decision)

    @property
    def parked(self) -> tuple[Entry, ...]:
        return tuple(entry for entry in self.entries if entry.state == questions.PARKED)

    @property
    def decisions(self) -> tuple[Entry, ...]:
        return tuple(entry for entry in self.entries if entry.decision)

    @property
    def grouped(self) -> tuple[Entry, ...]:
        """The decisions drawn as the one group of minor decisions."""
        return tuple(entry for entry in self.decisions if entry.group == MINORS)

    def data(self) -> dict:
        """The export as data: what `as_markdown` writes, for a program to read."""
        back = self.read_back
        return {"of": self.name, "project": self.project, "round": self.round,
                "view": self.view, "computed": self.computed,
                "entries": [entry.data() for entry in self.entries],
                "ratify": self.ratify,
                "covers": None if self.covers is None else list(self.covers),
                "later": [entry.data() for entry in self.later],
                "ended": None if self.ended is None else dict(self.ended),
                "left": [entry.data() for entry in self.left],
                "unreadable": list(self.unreadable),
                "read_back": read_back_data(back)}


def read_back_data(back: shown.ReadBack) -> dict:
    """A project's list of rulings not yet shown, as data: the time it runs through
    and its rows, oldest first."""
    return {"through": back.through,
            "rows": [{"holder": row.holder, "id": row.id, "text": row.text,
                      "rules": row.rules, "record": row.record,
                      "supersedes": row.supersedes, "revokes": row.revokes,
                      "at": row.at} for row in back.rows]}


# -- the rows ----------------------------------------------------------------------------

def _question(holder: str, item: index.Item, closed: Callable[[str], bool]) -> Entry:
    body = item.body
    return Entry(
        holder=holder, id=item.id, text=body.get("text"), origin=body.get("from"),
        blocks=_blocks(body.get("blocks")),
        state=questions.listed(body, closed=closed), by=body.get("by"),
        at=body.get("at"), builder=body.get("builder"),
        until=body.get("until"),
        options=tuple(Option(one["key"], one["text"])
                      for one in body.get("options", ())),
        moved_from=body.get("moved_from"), held=_blocks(body.get("held")))


def _decision(holder: str, item: index.Item, closed: Callable[[str], bool]) -> Entry:
    body = item.body
    return Entry(
        holder=holder, id=item.id, text=body.get("text"), origin=body.get("from"),
        blocks=decisions.BLOCKS, state=questions.OPEN, by=body.get("by"),
        at=body.get("at"),
        options=tuple(Option(key, line) for key, line in decisions.OPTIONS),
        stage=body.get("stage"), weight=body.get("weight"),
        forecloses=bool(body.get("forecloses")))


def _blocks(value):
    return tuple(value) if isinstance(value, list) else value


#: What makes an entry of each kind of item the index keeps, as
#: `fn(holder, item, closed) -> Entry`. A kind that is not here gives no row.
ENTRIES: dict[str, Callable[[str, index.Item, Callable[[str], bool]], Entry]] = {
    "question": _question, index.DECISION: _decision}


def _entries(summary: index.Summary, closed: Callable[[str], bool]) -> list[Entry]:
    """The rows one log gives: its items of the kinds in `ENTRIES` that read as open or
    parked, in the log's order. In a round that has ended each is `LEFT`, which
    `_ordered` keeps out of every listing of what waits."""
    ended = summary.slug is not None and summary.ended is not None
    found = []
    for item in summary.items:
        make = ENTRIES.get(item.kind)
        if make is None:
            continue
        entry = make(summary.name, item, closed)
        if entry.state == questions.OPEN and entry.until is not None:
            # Its wait is over: it is put to him as any open question is, with no
            # word of what it waited for.
            entry = replace(entry, until=None)
        if entry.state in (questions.OPEN, questions.PARKED):
            reads = questions.shown_as(entry.state, ended=ended)
            found.append(entry if reads == entry.state
                         else replace(entry, state=reads, left_as=entry.state))
    return found


def _ordered(entries: list[Entry]) -> tuple[Entry, ...]:
    return tuple([entry for entry in entries
                  if entry.state == questions.OPEN and not entry.decision]
                 + [entry for entry in entries if entry.state == questions.PARKED]
                 + [entry for entry in entries
                    if entry.decision and entry.state == questions.OPEN])


def _closed(settings) -> Callable[[str], bool]:
    return lambda name: questions.is_closed(name, settings)


def of_log(summary: index.Summary, *, settings=None) -> tuple[Entry, ...]:
    """The question rows one log gives, as the index answers for it: those that read
    as open, then the parked ones. For a count of what one round holds."""
    return _ordered([entry for entry in _entries(
        summary, _closed(own.load() if settings is None else settings))
        if not entry.decision])


def waiting(home: folders.Home, *, settings=None, env=None) -> Rows:
    """The question rows waiting in a project: every open and every parked question on
    the project's list and in each of its open rounds, the list first and the rounds by
    slug. `home` is the project's rounds folder, as `folders.home` gives it.

    It reads the index where its rows are fresh and the logs where they are not, and
    writes nothing. A round whose log can't be read is in `unreadable`; the project's
    own log unreadable raises `Unreadable`."""
    closed = _closed(own.load() if settings is None else settings)
    listed = index.read(home.project, home.folder, env=env)
    if listed.unreadable is not None:
        raise listed.unreadable
    entries, unreadable = _entries(listed, closed), []
    for summary in index.rounds(home.project, home.folder, env=env):
        if summary.unreadable is not None:
            unreadable.append(summary.unreadable)
        elif summary.status == states.OPEN:
            # Of the decisions, only a major one waits on him here.
            entries += [entry for entry in _entries(summary, closed)
                        if entry.weight in (None, report_block.MAJOR)]
    return Rows(_ordered(entries), tuple(unreadable))


def of_round(home: folders.Home, slug: str, *, view: str | None = None, settings=None,
             env=None) -> Rows:
    """The rows of one round in `view`, both views when None: for the docket, every
    question of it that reads as open, then the parked ones; for the ratification,
    the decisions it puts to him by the project's `ratify` setting, with the builds
    it covers and what it puts off to a later export. A round that has ended has no
    rows: its `ended` and `left` are given in their place. Raises `NoRound` for a
    round that was never opened and `Unreadable` for one whose log can't be read. It
    writes nothing."""
    try:
        summary = index.read(home.project, home.folder, slug, env=env)
    except NoLog:
        raise NoRound(f"{home.project}/{slug}") from None
    if summary.unreadable is not None:
        raise summary.unreadable
    settings = own.load() if settings is None else settings
    found = _entries(summary, _closed(settings))
    asked = [entry for entry in found if not entry.decision]
    rows = Rows(_ordered(asked) if view != RATIFY else (), ended=summary.ended,
                left=tuple(entry for entry in asked if entry.state == questions.LEFT
                           and view != RATIFY))
    if view == DOCKET:
        return rows
    put, covers, later = _ratification(
        summary, [entry for entry in found if entry.decision],
        own.round_setting(settings, home.project, "ratify"))
    return replace(rows, entries=rows.entries + put, covers=covers, later=later)


def _ratification(summary: index.Summary, found: list[Entry], setting: str):
    """What a round's ratification holds, as `(entries, covers, later)`: of the
    decisions not yet ruled, `found`, those of the finished builds that `setting` puts
    to him, the major ones first and then the minor ones as one group; the stages of
    the finished builds; and the major decisions of the builds not yet finished."""
    covers = tuple(item.id for item in summary.of(index.STAGE)
                   if item.body.get("finished") is True)
    unruled = [entry for entry in found if entry.state == questions.OPEN]
    if setting == OFF:
        return (), covers, ()
    majors = [entry for entry in unruled
              if entry.stage in covers and entry.weight == report_block.MAJOR]
    minors = []
    if setting == MAJORS_AND_MINORS:
        minors = [replace(entry, group=MINORS) for entry in unruled
                  if entry.stage in covers and entry.weight != report_block.MAJOR
                  and not entry.forecloses]
    later = tuple(entry for entry in unruled
                  if entry.stage not in covers and entry.weight == report_block.MAJOR)
    return tuple(majors + minors), covers, later


# -- the export --------------------------------------------------------------------------

def questions_of(name: str, *, view: str | None = None, settings=None,
                 now: str | None = None) -> Export:
    """The export of the round or the project `name`.

    - `view`: for a round, `DOCKET`, `RATIFY`, or None for both, the docket first. A
      project's export has no view.
    - `settings`: rounds' settings as `settings.load()` gives them, read now when left
      out.
    - `now`: the second the export is computed in, as a log writes a time; a ruling
      recorded in it is left off the list of rulings not yet shown.

    Raises `BadName` or `BadArgument` for a name or a view that can't be used,
    `UnknownProject` or `NoFolder` for a project with no rounds folder, `NoRound` for
    a round that was never opened, `Unreadable` for a round, or a project's own log,
    that can't be read, and `Refused` for a view given with a project.
    """
    if not isinstance(name, str) or not name.strip():
        raise Refused([Unmet(
            "nothing says whose questions to list: an export is of one round or of "
            "one project", "moxi rounds list ROUND|PROJECT --questions")])
    project, slug = folders.split(name) if "/" in name else (name, None)
    if view is not None and view not in VIEWS:
        raise BadArgument(f"{view!r} is no view of a round's questions",
                          f"leave --view out for both views, or say --view "
                          f"{DOCKET} or --view {RATIFY}")
    settings = own.load() if settings is None else settings
    home = folders.home(project, settings)
    if slug is None and view is not None:
        raise Refused([Unmet(
            f"{home.project} is a project, and a project's export has no view: it "
            "holds every open and every parked question of the project's list and of "
            "its open rounds", f"moxi rounds list {home.project} --questions")])
    now = log.stamp() if now is None else now
    rows = (waiting(home, settings=settings) if slug is None
            else of_round(home, slug, view=view, settings=settings))
    back = shown.unshown(home.project, settings=settings, now=now)
    # One line for a log, whichever of the two readings could not read it.
    unreadable = tuple(dict.fromkeys(
        error.reason for error in (*rows.unreadable, *back.unreadable)))
    return Export(name=home.project if slug is None else f"{home.project}/{slug}",
                  project=home.project,
                  round=None if slug is None else f"{home.project}/{slug}",
                  view=view, computed=now,
                  entries=rows.entries, read_back=back, unreadable=unreadable,
                  ended=rows.ended, left=rows.left,
                  ratify=(own.round_setting(settings, home.project, "ratify")
                          if slug is not None and view != DOCKET else None),
                  covers=rows.covers, later=rows.later)


# -- the two forms -----------------------------------------------------------------------

def as_json(export: Export) -> str:
    """The export as JSON text, ending in a line break."""
    return json.dumps(export.data(), indent=2, ensure_ascii=False) + "\n"


def as_markdown(export: Export) -> str:
    """The export as plain markdown, ending in a line break: the open questions, the
    parked ones under their own heading, then a round's ratification, those of a
    round that has ended under their own heading, the logs that could not be read
    when there are any, and last the rulings not yet shown."""
    opened, parked = export.open, export.parked
    what = f"{export.view}, computed" if export.view else "computed"
    counts = []
    if export.has_docket:
        counts += [f"{len(opened)} open", f"{len(parked)} parked"]
    if export.has_ratification:
        counts.append(f"{len(export.decisions)} to ratify")
    lines = [f"# Questions of {export.name}", "",
             f"{what} {export.computed}: {', '.join(counts)}", ""]
    if export.ended is not None:
        ended = export.ended
        how = f"{ended.get('how')} at {ended.get('at')}"
        if ended.get("how") != states.CLOSED:
            how = (f"{ended.get('how')} by {ended.get('by')} at {ended.get('at')}, "
                   f"record {ended.get('record')}")
        lines += [f"This round has ended: {how}. Nothing of it is put to the "
                  "operator.", ""]
    if export.has_docket:
        lines += ["## Open", ""]
        lines += _group(opened, "No question is open.")
        lines += ["## Parked: not put to the operator", ""]
        lines += _group(parked, "No question is parked.")
    if export.has_ratification:
        lines += _ratification_lines(export)
    if export.round is None:
        lines += ["## Major decisions not yet ruled", ""]
        lines += _group(export.decisions, "No major decision waits.")
    if export.left:
        lines += ["## Waiting for a new round: not put to the operator", "",
                  "The round ended, and a ruling of each was replaced or taken back "
                  "since. None waits on this round; one that is still to be answered "
                  "is asked again in a new round.", ""]
        lines += _group(export.left, "")
    if export.unreadable:
        lines += ["## Logs that can't be read", "",
                  "What these hold is not in this export.", ""]
        lines += [f"- {reason}" for reason in export.unreadable] + [""]
    lines += _read_back_lines(export)
    return "\n".join(lines) + "\n"


_PUTS = {MAJORS: "each major decision not yet ruled",
         MAJORS_AND_MINORS: "each major decision not yet ruled, and the minor ones as "
                            "one group",
         OFF: "no decision: the project's ratify setting is off"}


def _ratification_lines(export: Export) -> list[str]:
    covers = ", ".join(export.covers or ()) or "no build: none is finished"
    majors = tuple(entry for entry in export.decisions if entry.group is None)
    lines = ["## Ratification", "",
             f"Covers the finished builds: {covers}. It puts to the operator "
             f"{_PUTS.get(export.ratify, export.ratify)}.", ""]
    if export.ratify == OFF:
        return lines
    lines += ["## Ratification: major decisions", ""]
    lines += _group(majors, "No major decision of a finished build waits.")
    if export.ratify == MAJORS_AND_MINORS:
        grouped = export.grouped
        ids = ", ".join(entry.id for entry in grouped)
        lines += [f"## Ratification: minor decisions, one group: {ids or 'none'}", ""]
        if grouped:
            lines += ["Drawn together, each ruled on its own:", "",
                      f"    moxi rounds rule {export.round} B<n>.M<k>=accept|reject "
                      "--record PTR", ""]
            lines += [f"- {entry.id}: {entry.text}" for entry in grouped]
            lines.append("")
        else:
            lines += ["No minor decision of a finished build waits.", ""]
    if export.later:
        lines += ["## Later: decisions of builds not yet finished, not put to the operator",
                  ""]
        lines += _group(export.later, "")
    return lines


def _group(entries: tuple[Entry, ...], none: str) -> list[str]:
    if not entries:
        return [none, ""]
    return [line for entry in entries for line in (*_entry_lines(entry), "")]


def _entry_lines(entry: Entry) -> list[str]:
    lines = [f"### {entry.holder} {entry.id}: {entry.text}", "",
             f"- from: {entry.origin}",
             f"- blocks: {questions.said(entry.blocks)}",
             f"- state: {questions.called(entry.state)}"
             + ("" if entry.left_as is None
                else f" (reads as {entry.left_as}, and the round has ended)"),
             f"- by: {questions.asker(entry.by, entry.builder)}",
             f"- at: {entry.at}"]
    if entry.until is not None:
        lines.append(f"- until: {entry.until}")
    if entry.moved_from is not None:
        was = ("" if entry.held is None
               else f", where it held up {questions.said(entry.held)}")
        lines.append(f"- moved from: {entry.moved_from}{was}")
    if entry.decision:
        lines += [f"- stage: {entry.stage}", f"- weight: {entry.weight}",
                  f"- forecloses: {'yes, an entry of its own' if entry.forecloses else 'no'}"]
    if entry.options:
        lines.append("- options:")
        lines += [f"  - {option.key}: {option.text}" for option in entry.options]
    return lines


def _read_back_lines(export: Export) -> list[str]:
    back = export.read_back
    lines = ["## Rulings not yet shown to the operator", ""]
    if not back.rows:
        return [*lines, f"none, of the rulings of {export.project} that could be read",
                ""]
    lines += [back.heading, "",
              "This list is read, not answered, and is none of the questions above. "
              "Once he has read it:", "",
              f"    moxi rounds rule {export.project} {shown.READBACK}{back.through} "
              "--record PTR", ""]
    for row in back.rows:
        line = f"- {row.holder} {row.id}: {row.rules if row.text is None else row.text}"
        line += f" (record: {row.record}"
        if row.supersedes is not None:
            line += f"; replaces {row.supersedes}"
        if row.revokes is not None:
            line += f"; takes back {row.revokes}"
        lines.append(line + ")")
    return [*lines, ""]


def write(export: Export, folder: Path | str, *, settings=None) -> tuple[Path, Path]:
    """Write the export into `folder` as `questions.md` and `questions.json`, both from
    the one export given, and give the two paths. The folder is made when it is not
    there. Each file is put in place whole, so a reader never finds half of one.

    A project's rounds folder, and the folder of a round in one, are refused
    (`Refused`) with nothing written: rounds keeps its own files there, a
    `questions.md` among them. `settings` is what `settings.load()` gives, read now
    when left out."""
    folder = Path(folder).expanduser()
    kept = _kept_there(folder, own.load() if settings is None else settings)
    if kept is not None:
        raise Refused([Unmet(
            f"{folder} is {kept}, where rounds keeps its own files: an export "
            "written there would replace a file made from the logs, or sit among "
            "them; name another folder",
            f"moxi rounds list {export.name} --questions --out ANOTHER-DIR")])
    written = (folder / MARKDOWN_FILE, folder / JSON_FILE)
    try:
        folder.mkdir(parents=True, exist_ok=True)
        # For people and pages to read, so not private as a log is.
        files.atomic_write(written[0], as_markdown(export), mode=0o644)
        files.atomic_write(written[1], as_json(export), mode=0o644)
    except OSError as error:
        raise RoundsError(f"the export can't be written into {folder}: {error}",
                          "name a folder that can be written with --out DIR") from None
    return written


def _kept_there(folder: Path, settings) -> str | None:
    """What `folder` is to rounds, once every link on the way is followed: the rounds
    folder of a project, or the folder of a round in one. None for any other folder."""
    found, failed = folders.homes(settings)
    # Two projects that reach one folder have no home, and the folder is still theirs.
    homes = [(one.project, one.folder) for one in found]
    homes += [(error.project, error.folder) for error in failed
              if isinstance(error, SharedFolder)]
    real = Path(os.path.realpath(folder))
    for project, rounds_folder in homes:
        theirs = Path(os.path.realpath(rounds_folder))
        if real == theirs:
            return f"the rounds folder of the project {project}"
        if real.parent == theirs and log.exists(log.round_log(real)):
            return f"the folder of the round {project}/{real.name}"
    return None


__all__ = ["DOCKET", "RATIFY", "VIEWS", "MAJORS", "MAJORS_AND_MINORS", "OFF", "MINORS",
           "MARKDOWN_FILE", "JSON_FILE", "Option", "Entry", "Rows",
           "Export", "ENTRIES", "read_back_data", "of_log", "waiting", "of_round",
           "questions_of", "as_json", "as_markdown", "write"]

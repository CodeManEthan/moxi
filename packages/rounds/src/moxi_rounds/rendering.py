"""The generated files: what the logs say, written out as markdown for people and
agents to read beside the logs.

    <rounds folder>/<slug>/round-note.md   one round: the flow it follows now, the
                                           skips that stand, its design's newest
                                           version and standing, the lead's
                                           decisions on threads, its stages, its
                                           questions by state, its rulings, the
                                           verdicts of its design and its history
    <rounds folder>/rulings.md             the project's register: its standing rulings
    <rounds folder>/questions.md           the questions on the project's own list

Each file is written whole, in one step, after every write to the log it is made from
(`on_append`, one of the write path's after-append callbacks): the note after a write to
its round, the two project files after a write to the project's log. Nothing reads one
back.

A note also says which of its round's rulings the operator has read back, and that is
in the project's log. So a read-back, when it is recorded, writes again the note of
every round of the project, open or ended, each under its round's lock. A round another
process holds for longer than `NOTE_WAIT_S` is left to it and named as not written. A file is made from its log alone, so a hand edit is lost at the next write, and
the same log gives the same bytes: the only date in a file is that of the log's newest
event. Someone's own notes on a round go in any other file of its folder, which nothing
here touches.

A file that can't be written leaves the record as it is; the next write to the log
writes the file again.

What a file lists is what `showing` gives for the same state: the questions each with
who asked it and when, the rulings each with its pointer, what it replaces or takes
back, and whether the operator was shown it.

**The register.** `rulings.md` holds the rulings of substance: the ones that say what is
built. An act on the process (a parking, a withdrawal, a move, a taking back, a
read-back) changes a record and is no entry; an entry that was taken back names the
ruling that did it. An entry that was replaced or taken back stays, marked.

**A later kind of record** (a stage, a build, a waiting item) joins the note as a row
of `PARTS`: its heading and the lines it gives from the round as `showing.data` has it,
so the record is first a row of `showing.SECTIONS`. What a round is doing now, its
current step, joins the lines of `_flow`.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from moxi import files

from . import questions as asked
from . import folders, log, readback, ruling, showing, write
from . import state as states
from .errors import Locked, NoLog, Unreadable

#: The names of the three files.
NOTE_FILE = "round-note.md"
RULINGS_FILE = "rulings.md"
QUESTIONS_FILE = "questions.md"

#: The order the states of a question are listed in.
_STATES = (asked.OPEN, asked.PARKED, asked.LEFT, asked.RULED, asked.WITHDRAWN,
           asked.MOVED)

#: A header value that reads as itself with no quotes around it.
_PLAIN = re.compile(r"[A-Za-z][A-Za-z0-9._-]*")
_NOT_TEXT = frozenset({"true", "false", "null", "yes", "no", "on", "off", "y", "n"})

#: How long a read-back waits for the lock of each round whose note it writes again,
#: in seconds. Short: the read-back holds the project's lock meanwhile, and a verb that
#: holds a round and waits for the project would otherwise wait on it in turn.
NOTE_WAIT_S = 2.0

Closed = Callable[[str], bool]


@dataclass(frozen=True)
class Part:
    """One part of a round's note: its heading, and the lines under it, made from the
    round as `showing.data` gives it."""

    title: str
    lines: Callable[[dict], list[str]]


# -- the write path's callback -----------------------------------------------------------

def on_append(written) -> None:
    """Write the generated files of the logs a write just touched, and after a
    read-back the note of every other round of the project. Called by the write path
    with its `Written`, the lock still held. It appends to no log.

    Each file is tried whatever became of the one before it; the first failure is
    raised once all were tried."""
    where = written.where
    folder = Path(where.folder)
    wrote_project = len(written.project_events) > len(written.view.project_events)
    work: list[Callable[[], None]] = []

    def put(path: Path, text: Callable[[], str]) -> Callable[[], None]:
        # For people and agents to read, so not private as a log is.
        return lambda: files.atomic_write(path, text(), mode=0o644)

    # A note is written only beside its log: a folder with none is not a round of
    # ours, and a note there may be another tool's.
    if where.slug is not None and log.exists(log.round_log(folder / where.slug)):
        work.append(put(folder / where.slug / NOTE_FILE, lambda: note(
            where.project, where.slug, written.round_events, written.project_events)))
    # A round's verb that only read the project's log holds no lock on it, so that
    # log's files are left for whoever writes it. A project with no log has no files.
    if (where.slug is None or wrote_project) and written.project_events:
        work.append(put(folder / RULINGS_FILE, lambda: rulings(
            where.project, written.project_events)))
        work.append(put(folder / QUESTIONS_FILE, lambda: questions(
            where.project, written.project_events)))
    if _read_back(written.project_events[len(written.view.project_events):]):
        # What each round's note says is shown has changed, in every round there is.
        work += [lambda slug=slug: _note_again(where.project, folder, slug,
                                               written.project_events)
                 for slug in folders.slugs(folder) if slug != where.slug]
    failed = []
    for step in work:
        try:
            step()
        except Exception as error:
            failed.append(error)
    if failed:
        raise failed[0]


def _read_back(appended: Iterable[dict]) -> bool:
    """Whether any of the events just appended to a project's log is a read-back."""
    return any(readback.read_back(one) is not None
               for one in states.fold(appended).rulings)


def _note_again(project: str, folder: Path, slug: str,
                project_events: Iterable[dict]) -> None:
    """Write the note of a round this write did not touch, from its log as it stands,
    under the round's lock so that no verb on the round writes it meanwhile. A round
    whose log is gone or can't be read is passed over: no note can be made from it,
    and the doctor names it. Raises `Locked` for a round held past the wait."""
    path = write.round_lock(project, slug)
    try:
        with files.lock(path, timeout_s=NOTE_WAIT_S):
            try:
                events = log.read_round(log.round_log(folder / slug))
            except (NoLog, Unreadable):
                return
            files.atomic_write(folder / slug / NOTE_FILE,
                               note(project, slug, events, project_events), mode=0o644)
    except files.LockTimeout as stop:
        raise Locked(path, stop.holder, NOTE_WAIT_S) from None


# -- a round's note ----------------------------------------------------------------------

def note(project: str, slug: str, events: Iterable[dict],
         project_events: Iterable[dict] = (), *, closed: Closed | None = None) -> str:
    """The note of the round `<project>/<slug>`, as text: `events` is its whole log and
    `project_events` its project's, which says what the operator has read back.
    `closed` answers whether a round, by name, is closed, for a parked question that
    waits for one; the logs answer when it is left out."""
    found = _read(project, events, project_events, closed, slug=slug)
    shown_as = showing.data(found)
    lines = [*_header("round-note", project, shown_as["description"],
                      ["round", slug], shown_as["history"],
                      round=slug, status=shown_as["status"]),
             f"Generated by moxi rounds from the log of the round {found.name}, and "
             "written again whole after every write to the round. Edits to this file "
             "are lost: write your own notes on the round in another file of this "
             "folder.",
             "", f"# {found.name}", "", *_lines(shown_as["description"]), "",
             f"- status: {shown_as['status']}"]
    if shown_as["parent"] is not None:
        lines.append(f"- parent: {shown_as['parent']}")
    if shown_as["ended"] is not None:
        lines.append(f"- ended: {showing.ended_line(shown_as['ended'])}")
    for part in PARTS:
        lines += ["", f"## {part.title}", "", *part.lines(shown_as)]
    return "\n".join(lines) + "\n"


def _flow(shown_as: dict) -> list[str]:
    steps = ", ".join(str(step.get("step")) for step in shown_as["steps"]) or "no steps"
    # The step the round is at goes here, after the flow's steps, once a function
    # says what step a round is at.
    lines = [f"{shown_as['flow']}: {steps}"]
    if shown_as["inherits"]:
        lines += ["", *_item("starts from the standing rulings "
                             f"{', '.join(shown_as['inherits'])}")]
    if shown_as["skips"]:
        lines.append("")
    for one in shown_as["skips"]:
        lines += _item(f"skipped: {showing.skip_line(one)}")
    planned = [*showing.version_lines(shown_as["versions"], shown_as["standing"],
                                     shown_as["threads"]),
               *(f"stage {showing.stage_line(one)}" for one in shown_as["stages"])]
    if planned:
        lines.append("")
    for line in planned:
        lines += _item(line)
    return lines


def _questions(shown_as: dict) -> list[str]:
    return _questions_by_state(shown_as["questions"], "###")


def _questions_by_state(listed: list, heading: str) -> list[str]:
    """Questions as `showing` gives them, under a heading for each state that has
    one."""
    found = [*_STATES, *dict.fromkeys(
        one["state"] for one in listed if one["state"] not in _STATES)]
    groups = {state: [one for one in listed if one["state"] == state]
              for state in found}
    lines = [", ".join(f"{len(group)} {asked.called(state)}"
                       for state, group in groups.items() if group) or "No question."]
    for state, group in groups.items():
        if not group:
            continue
        lines += ["", f"{heading} {asked.called(state).capitalize()}", ""]
        for one in group:
            lines += _item(f"{one['id']}: {one['text']}")
            said = [f"from: {one['from']}", f"blocks: {_listed(one['blocks'])}",
                    f"by: {one['by']}", f"at: {one['at']}"]
            if "left_as" in one:
                said.append(f"reads as {one['left_as']}, and the round has ended")
            if "recorded" in one:
                said.append(f"recorded as {one['recorded']} until {one.get('until')}, "
                            "which has come")
            elif "until" in one:
                said.append(f"until: {one['until']}")
            said += [f"{called}: {one[key]}" for key, called in (
                ("ruling", "ruling"), ("chose", "chose"), ("record", "record"),
                ("moved_from", "moved from"), ("moved_to", "moved to")) if key in one]
            if "held" in one:
                said.append(f"held up there: {_listed(one['held'])}")
            said += [f"option {option['key']}: {option['text']}"
                     for option in one.get("options", ())]
            if "retired" in one:
                said.append(f"retired: {', '.join(one['retired'])}")
            for line in said:
                lines += _item(line, indent="  ")
    return lines


def _rulings(shown_as: dict) -> list[str]:
    listed = shown_as["rulings"]
    if not listed:
        return ["No ruling."]
    lines = []
    for one in listed:
        said = one["rules"] if one["text"] is None else one["text"]
        lines += _item(f"{one['id']}: {said}")
        for line in _ruling_facts(one, kind=True):
            lines += _item(line, indent="  ")
    return lines


def _ruling_facts(one: dict, *, kind: bool) -> list[str]:
    """What is said of one ruling under its sentence: its pointer, who recorded it and
    when, what it rules, replaces and takes back, what replaced it or took it back,
    whether the operator was shown it, and why."""
    said = [f"record: {one['record']}"]
    if kind:
        said.append(f"kind: {one['kind']}")
    said += [f"by: {one['by']}", f"at: {one['at']}"]
    said += [f"{called}: {_listed(one[key])}" for key, called in (
        ("rules", "rules"), ("chose", "chose"), ("stages", "stages"),
        ("scope", "scope"), ("supersedes", "replaces"), ("revokes", "takes back"),
        ("replaced_by", "replaced by"), ("revoked_by", "taken back by"))
        if one.get(key) is not None]
    if one["shown_record"] is not None:
        said.append(f"shown: {one['shown_record']}")
    elif one["shown"]:
        said.append(f"shown: read back through {one['read_back']}")
    elif one["shown"] is None:
        said.append("shown: not known")
    else:
        said.append("not shown")
    if one["reason"] is not None:
        said.append(f"reason: {one['reason']}")
    return said


def _verdicts(shown_as: dict) -> list[str]:
    listed = shown_as["verdicts"]
    if not listed:
        return ["No verdict."]
    lines = []
    for one in listed:
        lines += _item(f"{one['verdict']} for v{one['version']}: {one['pass']} pass "
                       f"by {one['by']}, model {one['model']}")
        said = [f"report: {one['report']}", f"sha256: {one['sha256']}",
                f"recorded: {one['at']} by {one['recorded_by']}"]
        if one["flags"]:
            said.append(f"flags: {', '.join(one['flags'])}")
        said += [f"finding {finding.get('id')}: {finding.get('severity')}, "
                 f"{finding.get('state')}: {finding.get('text')}"
                 for finding in one["findings"]]
        said += [f"applied: {taken['said']}" for taken in one["applied"]]
        said += [f"not applied: {taken['said']}: {taken['why']}"
                 for taken in one["not_applied"]]
        for line in said:
            lines += _item(line, indent="  ")
    return lines


def _history(shown_as: dict) -> list[str]:
    events = shown_as["history"]
    lines = [f"{len(events)} event{'' if len(events) == 1 else 's'}, oldest first.", ""]
    for event in events:
        of = showing.history_of(event)
        named = f" {of}" if of else ""
        lines.append(f"{event.get('n')}. {event.get('at')} {event.get('actor')} "
                     f"{event.get('type')}{named}")
    return lines


#: The parts of a round's note, in the order they are written.
PARTS: tuple[Part, ...] = (
    Part("Flow", _flow),
    Part("Questions", _questions),
    Part("Rulings", _rulings),
    Part("Verdicts", _verdicts),
    Part("History", _history))


# -- a project's two files ---------------------------------------------------------------

def rulings(project: str, project_events: Iterable[dict]) -> str:
    """The register of `project`, as text: every ruling of substance its log holds,
    oldest first, one that was replaced or taken back marked with the ruling that did
    it."""
    found = _read(project, project_events, project_events, None)
    shown_as = showing.data(found)
    entries = []
    for one, recorded in zip(shown_as["rulings"], found.state.rulings, strict=True):
        if one["kind"] == ruling.SUBSTANCE:
            entries.append({**one, "scope": _scope(recorded.scope)})
    stand = [one for one in entries
             if one["replaced_by"] is None and one["revoked_by"] is None]
    count = (f"{len(entries)} entr{'y' if len(entries) == 1 else 'ies'}, "
             f"{len(stand)} standing" if entries else "No entry.")
    lines = [*_header("rulings", project, f"The standing rulings of {project}",
                      ["rulings"], shown_as["history"]),
             f"Generated by moxi rounds from the log of the project {project}, and "
             "written again whole after every write to it. Edits to this file are "
             "lost.",
             "", f"# Rulings of {project}", "", count]
    sentences = {question["id"]: question["text"] for question in shown_as["questions"]}
    for one in entries:
        marks = [f"{called} {one[key]}" for key, called in (
            ("replaced_by", "replaced by"), ("revoked_by", "taken back by"))
            if one[key] is not None]
        lines += ["", f"## {one['id']}{f' ({marks[0]})' if marks else ''}", "",
                  *_lines(one["rules"] if one["text"] is None else one["text"]), ""]
        if one["rules"] in sentences:
            lines += _item(f"answers {one['rules']}: {sentences[one['rules']]}")
            one = {**one, "rules": None}
        # What replaced it or took it back is in its heading.
        for line in _ruling_facts({**one, "replaced_by": None, "revoked_by": None},
                                  kind=False):
            lines += _item(line)
    return "\n".join(lines) + "\n"


def questions(project: str, project_events: Iterable[dict], *,
              closed: Closed | None = None) -> str:
    """The questions on the list of `project`, as text, by state, each with who asked
    it and when. `closed` is as `note` takes it."""
    found = _read(project, project_events, project_events, closed)
    shown_as = showing.data(found)
    lines = [*_header("questions", project,
                      f"The questions on the list of {project}, by state",
                      ["questions"], shown_as["history"]),
             f"Generated by moxi rounds from the log of the project {project}, and "
             "written again whole after every write to it. Edits to this file are "
             "lost.",
             "", f"# Questions of {project}", "",
             *_questions_by_state(shown_as["questions"], "##")]
    return "\n".join(lines) + "\n"


# -- shared ------------------------------------------------------------------------------

def _read(project: str, events: Iterable[dict], project_events: Iterable[dict],
          closed: Closed | None, *, slug: str | None = None) -> showing.Shown:
    """The logs as `showing` reads a round, so a file lists what `show` shows. A
    project's own log is read as a round with no slug. No folder is named: a file
    says nothing of the machine it was written on."""
    return showing.Shown(
        name=project if slug is None else f"{project}/{slug}", project=project,
        slug=slug or "", folder=Path(),
        state=states.fold(events), register=states.fold(project_events),
        closed=asked.is_closed if closed is None else closed)


def _header(kind: str, project: str, description: str | None, tags: list[str],
            history: list[dict], **more: str | None) -> list[str]:
    """The file's header block and the empty line after it. `updated` is the date of
    the log's newest event, so the same log gives the same header."""
    newest = str(history[-1].get("at") or "") if history else ""
    fields = {"type": kind, "project": _plain(project),
              **{key: _plain(str(value)) for key, value in more.items()},
              "description": json.dumps(description or "", ensure_ascii=False),
              "tags": f"[{', '.join(_plain(tag) for tag in tags)}]",
              "updated": newest[:10]}
    return ["---", *(f"{key}: {value}" for key, value in fields.items()), "---", ""]


def _plain(value: str) -> str:
    if _PLAIN.fullmatch(value) and value.lower() not in _NOT_TEXT:
        return value
    return json.dumps(value, ensure_ascii=False)


def _lines(text: str | None) -> list[str]:
    return str(text).splitlines() or [""]


def _item(text: str, indent: str = "") -> list[str]:
    """`text` as one item of a list; a line break in it stays inside the item."""
    first, *rest = _lines(text)
    return [f"{indent}- {first}", *(f"{indent}  {line}" for line in rest)]


def _listed(value) -> str:
    return ",".join(str(one) for one in value) if isinstance(value, list) else str(value)


def _scope(scope: Iterable[dict]) -> list[str] | None:
    """A register entry's scope as it is typed: each repo, with its path when it has
    one. None for an entry that names none."""
    return [f"{one.get('repo')}:{one['path']}" if one.get("path")
            else str(one.get("repo")) for one in scope] or None


__all__ = ["NOTE_FILE", "RULINGS_FILE", "QUESTIONS_FILE", "NOTE_WAIT_S", "Part", "PARTS",
           "on_append", "note", "rulings", "questions"]

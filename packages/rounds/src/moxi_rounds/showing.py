"""Showing one round: its state and its records, as its log gives them now.

`show(name)` reads the round's log and its project's, and records nothing; it brings
the round's row of the index up to its log when it can, unless it is told to leave
the index as it is. What it gives is printed as
lines (`as_text`) or as data (`as_json`), both from the same reading.

A round is shown with what it is for, its status, the name of the flow it follows now
and the steps recorded for it, the skips that stand, the newest frozen version of its
design with what that version took away, and its stages; then its records, a section
for each kind: its questions by state, each with who asked it and when; its own rulings, each with its pointer and whether
the operator was shown it; and its history, every event, oldest first.

A round that has ended is shown with how it ended. A question whose record still reads
as open or parked there (a correction made after the end can leave one so) is shown as
waiting for a new round, apart from the open and the parked ones: it is put to nobody
on a round that has ended.

A round whose log can't be read is shown as that, with the file and the line, and with
nothing else: not as an empty round, and not as it was when last read. While the log
is known to have changed outside this machine, the round is shown with a line that
says so.

A project is not shown: its rounds are listed.

**A later kind of record** (a stage, a build, a waiting item) is a row of `SECTIONS`:
its key, what it reads from a `Shown`, and how that reads as lines. What a round is
doing now, its current step, joins the head in `_head`.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from moxi import store

from . import folders, index, log, questions, shown, skips, versions
from . import settings as own
from . import state as states
from .errors import BadArgument, NoLog, NoRound, Refused, Unmet, Unreadable

#: The order the states of a question are shown in.
_STATES = (questions.OPEN, questions.PARKED, questions.LEFT, questions.RULED,
           questions.WITHDRAWN, questions.MOVED)


@dataclass(frozen=True)
class Shown:
    """One round as read: its name and folder, the state of its log, and the state of
    its project's log, which says what the operator has read back. `notice` is the
    line to show while the log is known to have changed outside this machine.

    A round whose log can't be read has its error in `unreadable` and no `state`. When
    only the project's log can't be read, `register` is None and its error is in
    `register_unreadable`. `closed` answers whether a round, by name, is closed."""

    name: str
    project: str
    slug: str
    folder: Path
    state: states.State | None = None
    register: states.State | None = None
    notice: str | None = None
    unreadable: Unreadable | None = None
    register_unreadable: Unreadable | None = None
    closed: Callable[[str], bool] | None = None


def show(name: str, *, settings=None, refresh: bool = True) -> Shown:
    """The round `name`, `<project>/<slug>`, as its log gives it now. `settings` is
    what `settings.load()` gives, read now when left out. With `refresh` false the
    index is left as it is, and nothing at all is written.

    Raises `Refused` for a project's key, `BadName` or `BadArgument` for a name that
    is neither, `UnknownProject` or `NoFolder` for a project with no rounds folder,
    and `NoRound` for a round that was never opened. A log that can't be read raises
    nothing: the round is given as unreadable."""
    if not isinstance(name, str) or not name.strip():
        raise BadArgument("nothing says which round to show",
                          "name a round as <project>/<slug>")
    if "/" not in name:
        raise Refused([Unmet(
            f"{name} is a project, and one round is shown at a time: a project's "
            "rounds are listed", f"moxi rounds list {name}")])
    project, slug = folders.split(name)
    settings = own.load() if settings is None else settings
    home = folders.home(project, settings)
    found = {"name": f"{home.project}/{slug}", "project": home.project, "slug": slug,
             "folder": home.round_folder(slug)}
    try:
        summary = (_refreshed(home, slug) if refresh
                   else index.read(home.project, home.folder, slug))
        found["notice"] = summary.notice
        if summary.unreadable is not None:
            return Shown(**found, unreadable=summary.unreadable)
        # The whole state is the log's; the index answers only the summary of it.
        state = states.fold(log.read_round(log.round_log(home.round_folder(slug))))
    except NoLog:
        raise NoRound(found["name"]) from None
    except Unreadable as error:
        return Shown(**found, unreadable=error)
    register, register_unreadable = None, None
    try:
        register = states.fold(log.read(log.project_log(home.folder)))
    except NoLog:
        register = states.State()
    except Unreadable as error:
        register_unreadable = error
    return Shown(**found, state=state, register=register,
                 register_unreadable=register_unreadable,
                 closed=lambda round_name: questions.is_closed(round_name, settings))


def _refreshed(home: folders.Home, slug: str) -> index.Summary:
    try:
        return index.refresh(home.project, home.folder, slug)
    except (sqlite3.Error, OSError, store.StoreError):
        # The index is a cache: when it can't be brought up to date, the log answers.
        return index.read(home.project, home.folder, slug)


# -- the sections ------------------------------------------------------------------------

@dataclass(frozen=True)
class Section:
    """One kind of record a round is shown with: the `key` it has as data, what it
    reads from a round (`data`), and that as lines (`lines`)."""

    key: str
    data: Callable[[Shown], list]
    lines: Callable[[list], list[str]]


def _questions(found: Shown) -> list:
    listed = []
    for question in found.state.questions:
        reads = questions.current(question, found.state, closed=found.closed)
        state = questions.shown_as(reads, ended=found.state.ended is not None)
        data = {"id": question.id, "text": question.text, "from": question.origin,
                "blocks": (None if question.blocks is None
                           else questions.stored(question.blocks)),
                "state": state, "by": question.by, "at": question.at}
        if state != reads:
            # What its record reads as, in a round that has ended.
            data["left_as"] = reads
        if reads != question.state:
            # A parked question whose wait is over reads as open; what the log
            # recorded is kept beside it.
            data["recorded"] = question.state
        more = {"until": question.until,
                "options": [{"key": key, "text": line}
                            for key, line in question.options],
                "retired": list(question.retired), "chose": question.chose,
                "ruling": question.ruling, "record": question.record,
                "moved_from": question.moved_from,
                "held": None if question.held is None else questions.stored(question.held),
                "moved_to": question.moved_to}
        data.update({key: value for key, value in more.items() if value})
        listed.append(data)
    return listed


def _question_lines(listed: list) -> list[str]:
    states_found = [*_STATES, *dict.fromkeys(
        one["state"] for one in listed if one["state"] not in _STATES)]
    count = {state: sum(one["state"] == state for one in listed)
             for state in states_found}
    lines = ["questions: " + (", ".join(f"{number} {questions.called(state)}"
                                        for state, number in count.items() if number)
                              or "none")]
    for state in states_found:
        for one in (one for one in listed if one["state"] == state):
            said = [f"from: {one['from']}", f"blocks: {_blocks(one['blocks'])}",
                    f"state: {questions.called(state)}", f"by: {one['by']}",
                    f"at: {one['at']}"]
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
                said.append(f"held up there: {_blocks(one['held'])}")
            lines += [f"  {one['id']}: {one['text']}", f"    {'   '.join(said)}"]
            lines += [f"    option {option['key']}: {option['text']}"
                      for option in one.get("options", ())]
            if "retired" in one:
                lines.append(f"    retired: {', '.join(one['retired'])}")
    return lines


def _blocks(value) -> str:
    return ",".join(value) if isinstance(value, list) else str(value)


def _rulings(found: Shown) -> list:
    read = None if found.register is None else shown.through(found.register)
    listed = []
    for ruling in found.state.rulings:
        if ruling.shown is not None or (read is not None and ruling.at <= read):
            was_shown = True
        else:
            # With the project's log unreadable his read-backs are not known, so a
            # ruling with no mark is neither shown nor not.
            was_shown = None if found.register is None else False
        listed.append({
            "id": ruling.id, "text": ruling.text, "record": ruling.record,
            "reason": ruling.reason, "kind": ruling.kind, "rules": ruling.rules,
            "stages": (list(ruling.stages) if isinstance(ruling.stages, tuple)
                       else ruling.stages),
            "chose": ruling.chose, "by": ruling.actor, "at": ruling.at,
            "supersedes": ruling.supersedes, "revokes": ruling.revokes,
            "replaced_by": ruling.replaced_by, "revoked_by": ruling.revoked_by,
            "shown": was_shown,
            # Where he was shown it, when a mark says; a read-back names no place.
            "shown_record": ruling.shown,
            "read_back": read if was_shown and ruling.shown is None else None})
    return listed


def _ruling_lines(listed: list) -> list[str]:
    lines = [f"rulings: {len(listed) or 'none'}"]
    for one in listed:
        said = [f"record: {one['record']}", f"kind: {one['kind']}",
                f"by: {one['by']}", f"at: {one['at']}"]
        said += [f"{called}: {_blocks(one[key])}" for key, called in (
            ("rules", "rules"), ("chose", "chose"), ("stages", "stages"),
            ("supersedes", "replaces"), ("revokes", "takes back"),
            ("replaced_by", "replaced by"), ("revoked_by", "taken back by"))
            if one[key] is not None]
        if one["shown_record"] is not None:
            said.append(f"shown: {one['shown_record']}")
        elif one["shown"]:
            said.append(f"shown: read back through {one['read_back']}")
        elif one["shown"] is None:
            said.append("shown: not known")
        else:
            said.append("not shown")
        lines += [f"  {one['id']}: {one['rules'] if one['text'] is None else one['text']}",
                  f"    {'   '.join(said)}"]
        if one["reason"] is not None:
            lines.append(f"    reason: {one['reason']}")
    return lines


def _history(found: Shown) -> list:
    return list(found.state.history)


def history_of(event: dict) -> str | None:
    """What an event of the history is of, in a word or a few: the id it gave or
    names, its stage, for a change of flow the flow left and the one taken up, or
    for a change to a stage what changed."""
    of = event.get("id") or ",".join(event.get("ids") or ()) or event.get("stage")
    if event.get("type") == "flow.changed":
        of = f"{event.get('was')} to {event.get('flow')}, by {event.get('by')}"
    if event.get("type") == "stage.changed" and isinstance(of, str):
        more, gone = event.get("after"), event.get("gone")
        if event.get("removed") is True:
            of += ", left the plan"
        if isinstance(more, list) and more:
            of += f", also after {', '.join(map(str, more))}"
        if isinstance(gone, list) and gone:
            of += f", no longer after {', '.join(map(str, gone))}"
    if event.get("type") == "design.frozen":
        of = f"v{event.get('version')}"
    if event.get("type") == "thread.decided":
        of = f"{event.get('thread')}, {event.get('decision')}"
    if event.get("type") == "verdict.recorded" and event.get("design") is not None:
        of = f"{event.get('verdict')} for v{event.get('design')}"
    return of if isinstance(of, str) and of else None


def _history_lines(events: list) -> list[str]:
    lines = [f"history: {len(events)} event{'' if len(events) == 1 else 's'}"]
    for event in events:
        of = history_of(event)
        lines.append(f"  {event.get('n')}  {event.get('at')}  {event.get('actor')}  "
                     f"{event.get('type')}{f'  {of}' if of else ''}")
    return lines


def _verdicts(found: Shown) -> list:
    """The verdicts recorded of the design, oldest first: the version each read, its
    word, what the pass was, who wrote the report and with which model, the report's
    file and digest, its findings, what is noted of it, and what it took out of the
    record or left there."""
    return [{"n": one.n, "version": one.design, "verdict": one.verdict,
             "pass": one.pass_, "by": one.by, "model": one.model,
             "report": one.report, "sha256": one.sha256,
             "findings": [dict(finding) for finding in one.findings],
             "flags": list(one.flags),
             "applied": [_taken_away(taken) for taken in one.applied],
             "not_applied": [{**_taken_away(taken), "why": why}
                             for taken, why in one.not_applied],
             "recorded_by": one.actor, "at": one.at}
            for one in versions.design_verdicts(found.state)]


def verdict_lines(listed: list) -> list[str]:
    """The design's verdicts in lines, each with its findings and what it applied."""
    count = len(listed)
    lines = [f"verdicts: {count or 'none'}"]
    for one in listed:
        flags = f"   flags: {', '.join(one['flags'])}" if one["flags"] else ""
        lines += [f"  {one['verdict']} for v{one['version']}: {one['pass']} pass by "
                  f"{one['by']}, model {one['model']}{flags}",
                  f"    report: {one['report']}   sha256: {one['sha256']}   "
                  f"recorded: {one['at']} by {one['recorded_by']}"]
        lines += [f"    finding {finding.get('id')}: {finding.get('severity')}, "
                  f"{finding.get('state')}: {finding.get('text')}"
                  for finding in one["findings"]]
        lines += [f"    applied: {taken['said']}" for taken in one["applied"]]
        lines += [f"    not applied: {taken['said']}: {taken['why']}"
                  for taken in one["not_applied"]]
    return lines


#: The kinds of record a round is shown with, in the order they are shown.
SECTIONS: tuple[Section, ...] = (
    Section("questions", _questions, _question_lines),
    Section("rulings", _rulings, _ruling_lines),
    Section("verdicts", _verdicts, verdict_lines),
    Section("history", _history, _history_lines))


# -- the two forms -----------------------------------------------------------------------

def _head(found: Shown) -> dict:
    head = {"name": found.name, "project": found.project, "slug": found.slug,
            "folder": str(found.folder), "notice": found.notice}
    if found.unreadable is not None:
        error = found.unreadable
        return {**head, "unreadable": {"file": str(error.file), "line": error.line,
                                       "problem": error.problem, "reason": error.reason,
                                       "fix": error.fix}}
    state = found.state
    # The round's current step goes here, after the steps recorded for its flow, once
    # a function says what step a round is at.
    return {**head, "status": state.status, "ended": _ended(state),
            "description": state.description,
            "parent": state.parent, "inherits": list(state.inherits),
            "flow": state.flow, "steps": state.steps,
            "skips": _skips(state), "stages": _stages(state),
            "versions": _versions(state), "standing": str(versions.standing(state)),
            "threads": _threads(state),
            "register_unreadable": (None if found.register_unreadable is None
                                    else found.register_unreadable.reason)}


def _stages(state: states.State) -> list[dict]:
    """The stages the round declared: what each builds on or the round it stands for,
    what it waits for, its slices, the design version that first carried it, and
    whether it is finished or left the plan."""
    finished, left = questions.finished_stages(state), questions.left_stages(state)
    return [{"id": stage.id, "repo": stage.repo, "branch": stage.branch,
             "round": stage.round, "after": list(stage.after),
             "slices": [dict(piece) for piece in stage.slices], "why": stage.why,
             "version": stage.version, "by": stage.actor, "at": stage.at,
             "finished": stage.id in finished, "left": stage.id in left}
            for stage in state.stages]


def stage_line(one: dict) -> str:
    """A stage in a line: `B1: proj-a on rounds-core, after B2; 3 slices`."""
    what = (f"the round {one['round']}" if one["round"] is not None else
            f"{one['repo']} on {one['branch']}")
    line = f"{one['id']}: {what}"
    if one["after"]:
        line += f", after {', '.join(one['after'])}"
    if one["slices"]:
        count = len(one["slices"])
        line += f"; {count} slice{'' if count == 1 else 's'}"
    if one["left"]:
        line += "; left the plan"
    elif one["finished"]:
        line += "; finished"
    return line


def _versions(state: states.State) -> list[dict]:
    """The design's frozen versions, oldest first: each one's number and digest, who
    froze it and when, whether as a change of records only, what its trace check
    found, and what it took away, which waits."""
    return [{"version": version.n, "digest": version.digest, "by": version.actor,
             "at": version.at, "records_only": version.records_only,
             "record": version.record, "trace": version.trace,
             "pending": [_taken_away(one) for one in version.pending],
             # What of it would still change the record: the rest was applied by a
             # verdict or a ruling, or was overtaken.
             "outstanding": [_taken_away(one)
                             for one in versions.outstanding(state, version.n)]}
            for version in state.versions]


def _taken_away(one: states.Pending) -> dict:
    return {"what": one.what, "id": one.id, "gone": list(one.gone),
            "blocks": one.blocks if one.blocks is None else questions.stored(one.blocks),
            "said": versions.said(one)}


def _threads(state: states.State) -> list[dict]:
    """The lead's decisions on threads of the design's verification, oldest first:
    the thread, what was decided of it, the version of the design it was decided at,
    the pointer to why, and who recorded it and when."""
    return [{"n": one.n, "thread": one.thread, "decision": one.decision,
             "version": one.version, "record": one.record, "by": one.actor,
             "at": one.at} for one in state.threads]


def thread_line(one: dict) -> str:
    """A decision on a thread in a line."""
    return (f"thread {one['thread']}: {one['decision']} at v{one['version']}, "
            f"{one['at']} by {one['by']}   record: {one['record']}")


def version_lines(listed: list[dict], standing: str | None = None,
                  threads: Sequence[dict] = ()) -> list[str]:
    """The design's versions in lines: the newest with its digest, each version
    frozen as a change of records only with its pointer, the design's `standing`
    when one is given, the lead's decisions on `threads`, and what the newest version
    took away and still waits."""
    if not listed:
        return []
    newest = listed[-1]
    lines = [f"design: v{newest['version']}, frozen {newest['at']} by {newest['by']}, "
             f"sha256 {newest['digest']}"]
    lines += [f"records only: v{one['version']}, {one['at']} by {one['by']}   "
              f"record: {one['record']}" for one in listed if one["records_only"]]
    if standing is not None:
        lines.append(f"standing: {standing}")
    lines += [thread_line(one) for one in threads]
    lines += [f"pending in v{newest['version']}: {one['said']}"
              for one in newest["outstanding"]]
    return lines


def _skips(state: states.State) -> list[dict]:
    """The skips that stand: the ruling, the step, its stage for a step of one stage,
    the pointer and when; a skip of the questions step with the questions it lets its
    stage past now, and the ones it listed and lets past no longer."""
    listed = []
    for skip in skips.live(state):
        one = {"id": skip.id, "step": skip.step, "stage": skip.stage,
               "record": skip.record, "at": skip.at}
        if skip.step == skips.QUESTIONS:
            one.update(covers=list(skip.covers), let_go=list(skip.let_go))
        listed.append(one)
    return listed


def skip_line(one: dict) -> str:
    """A skip that stands, in a line: `check for B2, by C3 (record comms:12)`, and
    for the questions step what it lets its stage past."""
    line = f"{skips.said(one['step'], one['stage'])}, by {one['id']}"
    if "covers" in one:
        line += f", past {', '.join(one['covers']) or 'no question'}"
        if one["let_go"]:
            line += f" and no longer past {', '.join(one['let_go'])}"
    return f"{line} (record {one['record']})"


def _ended(state: states.State) -> dict | None:
    """How the round ended, or None while it is open: the status it has since, whose
    act it was and the pointer it carried, who recorded it and when, and where each
    question that was open or parked went."""
    end = state.ended
    if end is None:
        return None
    return {"how": end.how, "by": end.by, "record": end.record, "actor": end.actor,
            "at": end.at, "questions": dict(end.questions)}


def ended_line(ended: dict) -> str:
    """How a round ended, in a line: `dropped by operator at <time>, record <pointer>`
    and where its questions went."""
    went = ", ".join(f"{one} {'withdrawn' if where == 'withdrawn' else 'to ' + where}"
                     for one, where in ended["questions"].items())
    return (f"{ended['how']} by {ended['by']} at {ended['at']}, record "
            f"{ended['record']}{f'; questions: {went}' if went else ''}")


def data(found: Shown) -> dict:
    """The round as data: what `as_text` writes, for a program to read."""
    if found.unreadable is not None:
        return _head(found)
    return {**_head(found), **{section.key: section.data(found) for section in SECTIONS}}


def as_json(found: Shown) -> str:
    """The round as JSON text, ending in a line break."""
    return json.dumps(data(found), indent=2, ensure_ascii=False) + "\n"


def as_text(found: Shown) -> str:
    """The round as lines, ending in a line break."""
    shown_as = data(found)
    if found.unreadable is not None:
        lines = [f"round {found.name}   unreadable", f"folder: {found.folder}",
                 found.unreadable.reason]
        if found.unreadable.fix:
            lines.append(f"  fix: {found.unreadable.fix}")
        if found.notice is not None:
            lines.append(found.notice)
        return "\n".join(lines) + "\n"
    steps = ", ".join(str(step.get("step")) for step in shown_as["steps"]) or "no steps"
    lines = [f"round {found.name}   {shown_as['status']}   flow {shown_as['flow']} "
             f"({steps})",
             f"description: {shown_as['description']}", f"folder: {found.folder}"]
    lines += [f"skipped: {skip_line(one)}" for one in shown_as["skips"]]
    lines += version_lines(shown_as["versions"], shown_as["standing"],
                           shown_as["threads"])
    lines += [f"stage {stage_line(one)}" for one in shown_as["stages"]]
    if shown_as["parent"] is not None:
        lines.append(f"parent: {shown_as['parent']}")
    if shown_as["inherits"]:
        lines.append(f"inherits: {', '.join(shown_as['inherits'])}")
    if shown_as["ended"] is not None:
        lines.append(f"ended: {ended_line(shown_as['ended'])}")
    if found.notice is not None:
        lines.append(found.notice)
    if found.register_unreadable is not None:
        lines.append("what he has read back is not known: "
                     f"{found.register_unreadable.reason}")
    for section in SECTIONS:
        lines += ["", *section.lines(shown_as[section.key])]
    return "\n".join(lines) + "\n"


__all__ = ["Shown", "Section", "SECTIONS", "show", "data", "as_json", "as_text",
           "ended_line", "skip_line", "stage_line", "version_lines", "thread_line", "verdict_lines", "history_of"]

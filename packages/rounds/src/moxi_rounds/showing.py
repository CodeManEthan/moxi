"""Showing one round: its state and its records, as its log gives them now.

`show(name)` reads the round's log and its project's, and records nothing; it brings
the round's row of the index up to its log when it can, unless it is told to leave
the index as it is. What it gives is printed as
lines (`as_text`) or as data (`as_json`), both from the same reading.

A round is shown with what it is for, its status, its lead (its own, else its
project's, `leads.of`), the name of the flow it follows now and the steps recorded for
it, the checklist marks it made and where they come from (the opening or a frozen
version, `checklist.marks_of`) when it marked any or a project it is weighed against has
a checklist (`checklist.is_said`), the skips that stand, the newest frozen version of its
design with what that version took away, and its stages; then its records, a section
for each kind: its questions by state, each with who asked it and when; its own rulings, each with its pointer and whether
the operator was shown it; and its history, every event, oldest first.

A round that is open is shown with what its close waits for, since no verb closes a
round: it closes on the write that leaves nothing waited for. A round that has ended
is shown with how it ended, each decision of a finished build it left open, and the
numbers its ending event holds. A decision not yet ruled is labelled the operator's to
rule only where it is put to the operator (`decisions.why_not`). A question whose record still reads
as open or parked there (a correction made after the end can leave one so) is shown as
waiting for a new round, apart from the open and the parked ones: it is put to nobody
on a round that has ended.

A round whose log can't be read is shown as that, with the file and the line, and with
nothing else: not as an empty round, and not as it was when last read. While the log
is known to have changed outside this machine, the round is shown with a line that
says so.

A project is not shown: its rounds are listed.

Its builds are shown each with what its commission recorded, the newest run of its
repo's check, of each command step of the user's own and of the local part of a check by
CI, its newest read of CI, and, while it is open, its state as read from git now (`open_builds`):
whether it is idle, the builds it shares commits with, or what kept it from being read;
and, once a report of it is recorded, how far the target moved under it since and
whether that owes a re-check (`drift`). Each build carries its stage's slices, each
with the sized brief that counts for it, and the verifications a brief was sized for
(`sizing`); the lines say which slices of an open build have a sized brief and which
have none. Each of the lead's repairs is shown with what
it derived again and the old and new value of each field it changed.

**A later kind of record** (a stage, a build, a waiting item) is a row of `SECTIONS`:
its key, what it reads from a `Shown`, and how that reads as lines. What a round is
doing now, its current step, is in the head as `steps.current` gives it, with each open
build read from git for the steps whose state git says.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from moxi import store

from . import (builds, checking, checklist, ci, closing, decisions, drift, findings,
               finish_actions, folders,
               index, leads, log, numbers,
               open_builds, places, questions, shown, sizing, skips, steps, tracing,
               versions, waits)
from . import settings as own
from . import state as states
from .errors import (BadArgument, NoLog, NoRound, Refused, RoundsError, Unmet,
                     Unreadable)
from .gitreads import Git

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
    `register_unreadable`. `on_closed` answers whether a round, by name, is closed, and
    `settings` are rounds' settings, read when a build's state is (None: read then).
    `project_lead` is the lead the project's record names, which a round with no lead
    of its own reads."""

    name: str
    project: str
    slug: str
    round_dir: Path
    state: states.State | None = None
    register: states.State | None = None
    notice: str | None = None
    unreadable: Unreadable | None = None
    register_unreadable: Unreadable | None = None
    on_closed: Callable[[str], bool] | None = None
    settings: object = None
    project_lead: str | None = None


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
             "round_dir": home.round_dir(slug)}
    try:
        summary = (_refreshed(home, slug) if refresh
                   else index.read(home.project, home.rounds_dir, slug))
        found["notice"] = summary.notice
        if summary.unreadable is not None:
            return Shown(**found, unreadable=summary.unreadable)
        # The whole state is the log's; the index answers only the summary of it.
        state = states.fold(log.read_round(log.round_log(home.round_dir(slug))))
    except NoLog:
        raise NoRound(found["name"]) from None
    except Unreadable as exc:
        return Shown(**found, unreadable=exc)
    register, register_unreadable = None, None
    try:
        register = states.fold(log.read(log.project_log(home.rounds_dir)))
    except NoLog:
        register = states.State()
    except Unreadable as exc:
        register_unreadable = exc
    return Shown(**found, state=state, register=register,
                 register_unreadable=register_unreadable,
                 on_closed=lambda round_name: questions.is_closed(round_name, settings),
                 settings=settings, project_lead=home.lead)


def _refreshed(home: folders.Home, slug: str) -> index.Summary:
    try:
        return index.refresh(home.project, home.rounds_dir, slug)
    except (sqlite3.Error, OSError, store.StoreError):
        # The index is a cache: when it can't be brought up to date, the log answers.
        return index.read(home.project, home.rounds_dir, slug)


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
        reads = questions.current(question, found.state, on_closed=found.on_closed)
        state = questions.shown_as(reads, ended=found.state.ended is not None)
        data = {"id": question.id, "text": question.text, "from": question.origin,
                "blocks": (None if question.blocks is None
                           else questions.stored(question.blocks)),
                "state": state, "by": question.by, "at": question.at}
        if question.builder is not None:
            data["builder"] = question.builder
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
                "moved_to": question.moved_to,
                "prepared": questions.prepared_data(question)}
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
                    f"state: {questions.called(state)}",
                    f"by: {questions.asker(one['by'], one.get('builder'))}",
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
            lines += [f"    {line}" for line in questions.prepared_lines(
                one.get("prepared"))]
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
            # With the project's log unreadable the operator's read-backs are not
            # known, so a ruling with no mark is neither shown nor not.
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
            # Where the operator was shown it, when a mark says; a read-back names
            # no place.
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
    names, its stage, for a change of flow the flow left and the one taken up, for a
    change of lead the lead before and the new one, or for a change to a stage what
    changed."""
    of = event.get("id") or ",".join(event.get("ids") or ()) or event.get("stage")
    if event.get("type") == "flow.changed":
        of = f"{event.get('was')} to {event.get('flow')}, by {event.get('by')}"
        if isinstance(event.get("marked"), list):
            of = (f"flow {event.get('was')} -> {event.get('flow')}, by the "
                  f"{event.get('by')} at its opening, for the checklist marks "
                  f"{', '.join(map(str, event['marked']))}")
    if event.get("type") == leads.CHANGED:
        of = f"{event.get('was') or 'none'} to {event.get('lead')}"
    if event.get("type") == "stage.changed" and isinstance(of, str):
        more = event.get("after")
        if isinstance(more, list) and more:
            of += f", also after {', '.join(map(str, more))}"
    if event.get("type") == "design.frozen":
        of = f"v{event.get('version')}"
    if event.get("type") == "thread.decided":
        of = f"{event.get('thread')}, {event.get('decision')}"
    if event.get("type") == "repair":
        of = repair_of(event)
    if event.get("type") == finish_actions.ACTION:
        landed = ("" if event.get("landing") is None
                  else f", landing {str(event.get('landing'))[:12]}")
        of = (f"{event.get('stage')} {event.get('id')}: {event.get('state')}{landed}")
    if event.get("type") == "lock.cleared":
        of = f"{event.get('ref')}, held by {event.get('holder')}"
    if event.get("type") == sizing.SIZED:
        piece = sizing.piece_of(event.get("slice"), event.get("verify"))
        of = f"{event.get('stage')} {piece}, {event.get('brief')}"
    if event.get("type") == "verdict.recorded" and event.get("design") is not None:
        of = f"{event.get('verdict')} for v{event.get('design')}"
    if event.get("type") == "verdict.recorded" and event.get("stage") is not None:
        of = (f"{event.get('verdict')} for {event.get('stage')} at "
              f"{str(event.get('head'))[:7]}")
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


def repair_of(event: dict) -> str:
    """What a repair derived again, in a few words: `base of B2`, or `verdict on line
    12`."""
    if event.get("what") == states.BASE:
        return f"base of {event.get('stage')}"
    return f"verdict on line {event.get('verdict')}"


def _repairs(found: Shown) -> list:
    """Each repair of the round, oldest first: what it derived again, each field's
    old and new value, what it read, and who made it and when."""
    return [{"n": event.get("n"), "at": event.get("at"), "actor": event.get("actor"),
             "what": event.get("what"), "stage": event.get("stage"),
             "verdict": event.get("verdict"), "version": event.get("version"),
             "fields": event.get("fields"), "read": event.get("read"),
             "record": event.get("record")}
            for event in found.state.history if event.get("type") == "repair"]


def repair_line(one: dict) -> str:
    """A repair in words: `base of B2: 1a2b3c4d5e6f to 0f9e8d7c6b5a`, `digest of v8:
    1a2b3c4d5e6f to 0f9e8d7c6b5a, record comms:1234`, or `verdict on line 12
    (designs/r.md): added finding L<n>, waiting item W<n>`."""
    fields = one["fields"] if isinstance(one.get("fields"), dict) else {}
    if one.get("what") == states.BASE:
        change = fields.get("base") or {}
        return (f"base of {one.get('stage')}: {str(change.get('was'))[:12]} to "
                f"{str(change.get('now'))[:12]}")
    if one.get("what") == states.DIGEST:
        change = fields.get("digest") or {}
        return (f"digest of v{one.get('version')}: {str(change.get('was'))[:12]} to "
                f"{str(change.get('now'))[:12]}, record {one.get('record')}")
    added = []
    for key, called in (("findings", "finding"), ("waits", "waiting item"),
                        ("decisions", "decision")):
        change = fields.get(key) or {}
        had = {entry.get("id") for entry in change.get("was") or ()
               if isinstance(entry, dict)}
        ids = [str(entry.get("id")) for entry in change.get("now") or ()
               if isinstance(entry, dict) and entry.get("id") not in had]
        if ids:
            added.append(f"{called}{'' if len(ids) == 1 else 's'} {', '.join(ids)}")
    read = one["read"] if isinstance(one.get("read"), dict) else {}
    return (f"verdict on line {one.get('verdict')} ({read.get('report')}): added "
            f"{'; '.join(added) or 'nothing'}")


def _repair_lines(listed: list) -> list[str]:
    lines = [f"repairs: {len(listed) or 'none'}"]
    lines += [f"  {one['n']}  {one['at']}  {one['actor']}  {repair_line(one)}"
              for one in listed]
    return lines


#: The kinds of record a round is shown with, in the order they are shown.
def _builds(found: Shown) -> list:
    """Each build of the round in the order commissioned: what its commission
    recorded, how it ended when it did, and for an open build its state as read from
    git now, whether it is idle and the builds it shares commits with, or the problem
    that kept it from being read."""
    every = builds.builds(found.state)
    if not every:
        return []
    events = {event["n"]: event for event in found.state.history}
    read = open_builds.of_round(found.name, [one.repo for one in every if one.ended is None],
                                settings=found.settings)
    listed = []
    for one in every:
        event = events.get(one.n, {})
        row = {"stage": one.stage, "repo": one.repo, "branch": one.branch,
               "base": one.base, "target": one.target, "n": one.n,
               **{key: event.get(key) for key in ("builder", "record", "worktree",
                                                  "scratch", "host", "build", "at")},
               "ended": one.ended, "state": one.ended, "by": None, "why": None,
               "head": None, "idle": False, "shares": [], "problem": None,
               "drop_by": None, "drop": None, "credit": None,
               "check": check_run(found.state, one),
               "step_runs": [run for run in (
                   check_run(found.state, one, step)
                   for step in [*command_steps(found.state), ci.LOCAL])
                   if run is not None],
               "ci": ci_read(found.state, one),
               "verdict": build_verdict(found.state, one),
               "findings": [finding.data()
                            for finding in findings.of_build(found.state, one)],
               "slices": sizing.slices_of(found.state, one),
               "briefs": sizing.verifications_of(found.state, one),
               "drift": None, "finish": []}
        # A folder is shown as the place it names here, whichever form the record has.
        row.update({key: places.expand_home(row[key]) for key in ("worktree", "scratch")
                    if isinstance(row[key], str)})
        now = read.get(one.stage) if one.ended is None else None
        if now is not None and now.build.n == one.n:
            data = now.data()
            row.update({key: data[key] for key in ("state", "by", "why", "head", "idle",
                                                   "shares", "problem", "drop_by")})
            row["drop"] = (f"moxi rounds drop {found.name} {one.stage} --by "
                           f"{data['drop_by']} --record PTR")
            if data["state"] == builds.UNCLEAR and data["by"] == builds.FAST_FORWARD:
                row["credit"] = (f"moxi rounds rule {found.name} {builds.CREDIT}"
                                 f"{one.stage} --record PTR")
            row["drift"] = _drift(found, one, now.status)
            row["finish"] = finish_actions_of(found.state, one, data["head"])
        elif one.ended is None:
            row["problem"] = {"reason": f"the repo {one.repo} could not be read",
                              "fix": "moxi projects list"}
        listed.append(row)
    return listed


def finish_actions_of(state: states.State, build: builds.Build,
                    head: str | None) -> list[dict]:
    """For a build not finished, the newest record of each of its finish actions at
    its head, as `{"id", "kind", "state", "landing", "n", "at"}`: what a finish did so
    far, and a failed action, which the build's state from git does not show."""
    if build.ended is not None or head is None:
        return []
    return [{"id": event["id"], "kind": event.get("kind"), "state": event.get("state"),
             "landing": event.get("landing"), "n": event["n"], "at": event.get("at")}
            for event in finish_actions.records(state, build, head).values()]


def finish_action_line(one: dict) -> str:
    """A finish action's newest record in words: `finish: merge ok, landing 1a2b3c4d5e6f
    (line 12)`."""
    landed = "" if one["landing"] is None else f", landing {str(one['landing'])[:12]}"
    return f"finish: {one['id']} {one['state']}{landed} (line {one['n']})"


def _drift(found: Shown, build: builds.Build, status: builds.Status | None) -> dict | None:
    """The drift of an open build at its head now, against its newest build verdict,
    as data; None with no such verdict or nothing read from git; `{"problem": ...}`
    when git could not answer."""
    if status is None or drift.newest_verdict(found.state, build) is None:
        return None
    try:
        _key, root = folders.root(build.repo)
        settings = own.load() if found.settings is None else found.settings
        read = drift.of_build(found.state, build, status, Git(root), settings=settings)
    except RoundsError as exc:
        return {"problem": {"reason": exc.reason, "fix": exc.fix}}
    return None if read is None else read.data()


def drift_line(stage: str, one: dict) -> str:
    """A build's drift in words, or what kept it from being read."""
    if "problem" in one:
        return f"drift not read: {one['problem']['reason']}"
    return drift.line(stage, one)


def build_verdict(state: states.State, build: builds.Build) -> dict | None:
    """The newest verdict recorded for `build`, as data: its word, the pass, the head
    it read and that head's merge-base with the target, who wrote the report with
    which model, the report's file and digest, what is noted of it, and when it was
    recorded; None with none."""
    one = drift.newest_verdict(state, build)
    if one is None:
        return None
    return {"n": one.n, "verdict": one.verdict, "pass": one.pass_, "head": one.head,
            "merge_base": one.merge_base, "by": one.by, "model": one.model,
            "report": one.report, "sha256": one.sha256, "flags": list(one.flags),
            "at": one.at}


def build_verdict_line(one: dict) -> str:
    """A build's newest verdict in words: `verdict fold-first at 81c7f5b, full pass by
    critic, model opus`."""
    flags = f", flags: {', '.join(one['flags'])}" if one["flags"] else ""
    return (f"verdict {one['verdict']} at {str(one['head'])[:7]}, {one['pass']} pass by "
            f"{one['by']}, model {one['model']}{flags}")


def finding_line(one: dict) -> str:
    """A finding in words: `finding B<n>.L<n>: major, open: the text`."""
    return f"finding {one['id']}: {one['severity']}, {one['state']}: {one['text']}"


def ci_read(state: states.State, build: builds.Build) -> dict | None:
    """The newest read of CI recorded for `build`, as data: its commit, the whole
    reading, each workflow with its reading and url, and when it was recorded; None
    with no read."""
    found = None
    for event in state.history:
        if (event["n"] > build.n and event.get("stage") == build.stage
                and event["type"] == "build.commissioned"):
            break
        if (event["n"] > build.n and event["type"] == ci.READ
                and event.get("stage") == build.stage):
            found = event
    if found is None:
        return None
    return {key: found.get(key) for key in ("commit", "reading", "workflows", "at")}


def ci_line(read: dict) -> str:
    """The newest read of CI for a build in words: `ci green at 81c7f5b`."""
    return f"ci {read['reading']} at {str(read['commit'])[:7]}"


def command_steps(state: states.State) -> list[str]:
    """The command steps of the user's own the round's flow lists, in its order."""
    return [str(entry.get("step")) for entry in state.steps
            if checking.step_entry(state, str(entry.get("step"))) is not None]


def check_run(state: states.State, build: builds.Build,
               step: str = checking.CHECK) -> dict | None:
    """The newest run of `step` (the repo's check unless named) recorded for `build`, as
    data: the step, its commit, exit code, duration, output file and when it was
    recorded; None with no run."""
    found = None
    for event in state.history:
        if (event["n"] > build.n and event["type"] == checking.RAN
                and event.get("stage") == build.stage
                and event.get("step") == step):
            found = event
        if (event["n"] > build.n and event.get("stage") == build.stage
                and event["type"] == "build.commissioned"):
            break
    if found is None:
        return None
    run = {key: found.get(key) for key in ("step", "commit", "exit_code", "duration_s",
                                           "output", "at")}
    if isinstance(run["output"], str):
        run["output"] = places.expand_home(run["output"])
    return run


def check_line(run: dict) -> str:
    """The newest run of a step for a build in words: `check passed at 81c7f5b` or
    `staging failed (exit 1) at 81c7f5b`."""
    code = run["exit_code"]
    how = ("passed" if code == 0 else "ended at its time limit" if code is None
           else f"failed (exit {code})")
    return f"{run.get('step') or checking.CHECK} {how} at {str(run['commit'])[:7]}"


def build_line(one: dict) -> str:
    """A build in a line: `B1: proj-a on work, base 81c7f5b, target main, builder
    coder; building, idle, inside proj-a/b-round B2 (finishes first)`, each build it
    shares commits with worded by its relation (`builds.share_words`)."""
    line = (f"{one['stage']}: {one['repo']} on {one['branch']}, base "
            f"{(one['base'] or '')[:7]}, target {one['target']}, builder "
            f"{one['builder']}; ")
    if one["problem"] is not None:
        return line + f"not read: {one['problem']['reason']}"
    line += str(one["state"])
    if one["idle"]:
        line += ", idle"
    if one["shares"]:
        line += ", " + builds.share_words(
            ((other["round"], other["stage"], other.get("relation", builds.OVERLAPS))
             for other in one["shares"]),
            merged=one["state"] == builds.MERGED)
    return line


def _build_lines(listed: list) -> list[str]:
    lines = [f"builds: {len(listed)}"]
    for one in listed:
        lines.append(f"  {build_line(one)}")
        lines += [f"    {key}: {one[key]}" for key in ("worktree", "scratch")
                  if one[key] is not None]
        for run in [one.get("check"), *one.get("step_runs", ())]:
            if run is not None:
                lines.append(f"    {check_line(run)}, output {run['output']}")
        if one.get("ci") is not None:
            lines.append(f"    {ci_line(one['ci'])}")
            lines += [f"      {flow['path']}: {flow['reading']}"
                      + ("" if flow.get("url") is None else f"   {flow['url']}")
                      for flow in one["ci"]["workflows"] or ()]
        if one.get("verdict") is not None:
            lines.append(f"    {build_verdict_line(one['verdict'])}")
            lines.append(f"      report: {one['verdict']['report']}")
        if one.get("drift") is not None:
            lines.append(f"    {drift_line(one['stage'], one['drift'])}")
        lines += [f"    {finish_action_line(action)}" for action in one.get("finish", ())]
        lines += [f"    {finding_line(finding)}" for finding in one.get("findings", ())]
        if one["ended"] is None:
            lines += [f"    {line}" for line in briefs_lines(one)]
        lines += [f"    {line}" for line in drop_lines(one)]
        if one["record"] is not None:
            lines.append(f"    go: {one['record']}")
    return lines


def drop_lines(one: dict) -> list[str]:
    """For an open build that is looked at (gone, empty, unclear, idle, sharing or
    not read), why, and who may drop it with the command; for one unclear by a
    fast-forward with no reading, also the operator's credit."""
    if one.get("drop") is None or not (
            one["state"] in (builds.GONE, builds.EMPTY, builds.UNCLEAR)
            or one["idle"] or one["shares"] or one["problem"] is not None):
        return []
    lines = []
    if one.get("why"):
        lines.append(f"why: {one['why']}")
    if one.get("credit") is not None:
        lines.append(f"the operator credits it: {one['credit']}")
    lines.append(f"the {one['drop_by']} drops it: {one['drop']}")
    return lines


def briefs_lines(one: dict) -> list[str]:
    """Which slices of a build have a sized brief, and which verifications:
    `slices: 2a sized, 2b not sized` (`slices: one slice sized` for a stage that
    declares none) and `verification briefs sized: build`."""
    lines = []
    if one.get("slices"):
        lines.append("slices: " + ", ".join(
            f"{sizing.ONE_SLICE if piece['id'] is None else piece['id']} "
            f"{'not sized' if piece['sized'] is None else 'sized'}"
            for piece in one["slices"]))
    if one.get("briefs"):
        lines.append("verification briefs sized: "
                     + ", ".join(str(brief["verify"]) for brief in one["briefs"]))
    return lines


def _waits(found: Shown) -> list:
    """Every waiting item of the round, in the order declared, open or done; an open
    one with `once_done`, the command that marks it done."""
    return [{**one.data(),
             "once_done": waits.mark_line(found.name, one.key) if one.open else None}
            for one in waits.of_round(found.state)]


def wait_line(one: dict) -> str:
    """A waiting item in words: `waiting item B1.W1, open: the text`."""
    return f"waiting item {one['id']}, {one['state']}: {one['text']}"


def wait_facts(one: dict) -> list[str]:
    """What is known of a waiting item beyond its line: the report that declared it,
    and how it was marked done or the command that marks it."""
    said = [waits.origin_line(one)]
    if one["state"] == waits.DONE:
        said.append(f"done: {one['done_at']} by {one['done_by']}   record: "
                    f"{one['record']}")
    else:
        said.append(f"once done: {one['once_done']}")
    return said


def _wait_lines(listed: list) -> list[str]:
    lines = [f"waiting items: {len(listed) or 'none'}"]
    for one in listed:
        lines.append(f"  {wait_line(one)}")
        lines += [f"    {line}" for line in wait_facts(one)]
    return lines


def _decisions(found: Shown) -> list:
    """Every decision of the round, in the order declared, open or ruled. An open one
    has `put`, whether it is put to the operator (`decisions.why_not`), `not_put`, why
    not, `open_as`, the line said under it (`decisions.open_line`), and `to_rule`, the
    command that records the operator's ruling, None once the round has ended and takes no
    ruling."""
    state = found.state
    held = decisions.of_round(state)
    why = {}
    if any(one.state == decisions.OPEN for one in held):
        # Read only here: a project's log, shown for its register, holds no decision.
        settings = own.load() if found.settings is None else found.settings
        why = decisions.why_not(state, str(own.round_setting(settings, found.project,
                                                             closing.RATIFY)))
    ended = state.ended is not None
    listed = []
    for one in held:
        more = {"to_rule": None}
        if one.state == decisions.OPEN:
            more = {"put": why[one.key] is None, "not_put": why[one.key],
                    "open_as": decisions.open_line(found.name, one.key,
                                                   weight=one.weight, why=why[one.key],
                                                   ended=ended),
                    "to_rule": None if ended else decisions.rule_line(found.name,
                                                                      one.key)}
        listed.append({**one.data(), **more})
    return listed


def decision_line(one: dict) -> str:
    """A decision in words: `decision B1.M1: major, forecloses, open: the text`."""
    weight = one["weight"] + (", forecloses" if one["forecloses"] else "")
    return f"decision {one['id']}: {weight}, {one['state']}: {one['text']}"


def decision_facts(one: dict) -> list[str]:
    """What is known of a decision beyond its line: where it came from, and the
    operator's ruling, the lead's settlement, or whose it is and the command that
    records that."""
    said = [f"from: {one['from']}   by: {one['by']}   at: {one['at']}"]
    if one["ruling"] is not None:
        said.append(f"ruled: {one['chose']} by {one['ruling']}   record: "
                    f"{one['record']}")
    elif one["state"] == decisions.SETTLED:
        said.append(decisions.settled_line(one))
    else:
        said.append(one["open_as"])
    return said


def _decision_lines(listed: list) -> list[str]:
    lines = [f"decisions: {len(listed) or 'none'}"]
    for one in listed:
        lines.append(f"  {decision_line(one)}")
        lines += [f"    {line}" for line in decision_facts(one)]
    return lines


SECTIONS: tuple[Section, ...] = (
    Section("builds", _builds, _build_lines),
    Section("waits", _waits, _wait_lines),
    Section("decisions", _decisions, _decision_lines),
    Section("questions", _questions, _question_lines),
    Section("rulings", _rulings, _ruling_lines),
    Section("verdicts", _verdicts, verdict_lines),
    Section("repairs", _repairs, _repair_lines),
    Section("history", _history, _history_lines))


# -- the two forms -----------------------------------------------------------------------

def _head(found: Shown) -> dict:
    head = {"name": found.name, "project": found.project, "slug": found.slug,
            "folder": str(found.round_dir), "notice": found.notice}
    if found.unreadable is not None:
        error = found.unreadable
        return {**head, "unreadable": {"file": str(error.file), "line": error.line,
                                       "problem": error.problem, "reason": error.reason,
                                       "fix": error.fix}}
    state = found.state
    settings = own.load() if found.settings is None else found.settings
    seen = checking.seen_of(state, found.name, settings=settings)
    at = steps.current(state, name=found.name, on_closed=found.on_closed,
                       register=found.register, seen=seen)
    return {**head, "status": state.status,
            "ended": _ended(state, ratify=lambda: closing.ratify_of(settings,
                                                                     found.name)),
            "description": state.description,
            "lead": leads.of(state, found.project_lead),
            "parent": state.parent, "inherits": list(state.inherits),
            "flow": state.flow, "steps": state.steps,
            "checklist": _marks(state, found.project, settings),
            "step": None if at is None else at.data(),
            "close": (None if state.status != states.OPEN
                      else {"waits": list(closing.waits(
                          state, on_closed=found.on_closed,
                          ratify=closing.ratify_of(settings, found.name), seen=seen))}),
            "skips": _skips(state), "stages": _stages(state),
            "versions": _versions(state), "standing": str(versions.standing(state)),
            "trace": tracing.shown(state, found.register),
            "threads": _threads(state),
            "register_unreadable": (None if found.register_unreadable is None
                                    else found.register_unreadable.reason)}


def _marks(state: states.State, home: str, settings) -> dict | None:
    """The round's checklist marks and where they come from (`checklist.marks_of`);
    None when it made none, or when it has no marks and no project it is weighed
    against, a stage's repo included, has a checklist (`checklist.is_said`), as `next`
    says nothing then."""
    marks = checklist.marks_of(state)
    repos = [stage.repo for stage in state.stages if stage.repo is not None]
    if marks.source is None or not checklist.is_said(state, home, settings, repos=repos):
        return None
    return {"marked": list(marks.marked), "from": marks.source}


def marks_line(marks: dict) -> str:
    """The round's checklist marks as a line, with where they come from."""
    source = "the opening" if marks["from"] == "opening" else marks["from"]
    return f"{', '.join(marks['marked']) or 'none marked'} (from {source})"


def step_lines(step: dict | None) -> list[str]:
    """The step a round is at, in lines: the step with its state and the questions
    that make it due, then each thing it waits for with the command that meets it.
    Nothing for a round at no step."""
    if step is None:
        return []
    asked = f"   questions: {', '.join(step['questions'])}" if step["questions"] else ""
    lines = [f"step: {steps.said(step)}{asked}"]
    for wait in step["waits"]:
        lines += [f"  waits for: {wait['reason']}", f"    fix: {wait['fix']}"]
    return lines


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
    """The design's frozen versions, oldest first: each one's number and digest (and
    the digest it was re-stamped with, None for none), who
    froze it and when, whether as a change of records only, what its trace check
    found, and what it took away, which waits."""
    return [{"version": version.n, "digest": version.digest,
             "restamped": version.restamped, "by": version.actor,
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
                  threads: Sequence[dict] = (), trace: dict | None = None) -> list[str]:
    """The design's versions in lines: the newest with its digest, each version
    frozen as a change of records only with its pointer, the design's `standing`
    when one is given, what the check of the newest version's trace rows found
    (`trace`, as `tracing.shown` gives it) with the rows that are stale, the lead's
    decisions on `threads`, and what the newest version took away and still waits."""
    if not listed:
        return []
    newest = listed[-1]
    restamped = newest.get("restamped")
    lines = [f"design: v{newest['version']}, frozen {newest['at']} by {newest['by']}, "
             + (f"sha256 {newest['digest']}" if restamped is None else
                f"sha256 {restamped} (re-stamped from {str(newest['digest'])[:12]})")]
    lines += [f"records only: v{one['version']}, {one['at']} by {one['by']}   "
              f"record: {one['record']}" for one in listed if one["records_only"]]
    if standing is not None:
        lines.append(f"standing: {standing}")
    lines += tracing.lines(trace)
    lines += [thread_line(one) for one in threads]
    lines += [f"pending in v{newest['version']}: {one['said']}"
              for one in newest["outstanding"]]
    return lines


def _skips(state: states.State) -> list[dict]:
    """The skips that stand, oldest first: the ruling, the step, its stage for a step
    of one stage, the pointer and when; a skip of the questions step with the
    questions it lets its stage past now, and the ones it listed and lets past no
    longer. A skip of a finish action is one of `finish` for its stage, with the
    `action`'s id; it skips that action, not the step."""
    listed = []
    for skip in skips.live(state):
        one = {"id": skip.id, "step": skip.step, "stage": skip.stage,
               "record": skip.record, "at": skip.at}
        if skip.step == skips.QUESTIONS:
            one.update(covers=list(skip.covers), let_go=list(skip.let_go))
        listed.append(one)
    for skip in skips.action_live(state):
        listed.append({"id": skip.id, "step": findings.FINISH, "stage": skip.stage,
                       "action": skip.action, "record": skip.record, "at": skip.at})
    return sorted(listed, key=lambda one: one["at"])


def skip_line(one: dict) -> str:
    """A skip that stands, in a line: `check for B2, by C3 (record comms:12)`, and
    for the questions step what it lets its stage past."""
    if "action" in one:
        return (f"the finish action {one['action']} for {one['stage']}, by {one['id']} "
                f"(record {one['record']})")
    line = f"{skips.said(one['step'], one['stage'])}, by {one['id']}"
    if "covers" in one:
        line += f", past {', '.join(one['covers']) or 'no question'}"
        if one["let_go"]:
            line += f" and no longer past {', '.join(one['let_go'])}"
    return f"{line} (record {one['record']})"


def _ended(state: states.State, *,
           ratify: Callable[[], str | None] = lambda: None) -> dict | None:
    """How the round ended, or None while it is open: the status it has since, whose
    act it was and the pointer it carried, who recorded it and when, where each
    question that was open or parked went, and the round's numbers as the event that
    ended it holds them, with the round's time and longest stretch with nothing
    logged computed from its log where the event holds neither, the steps due read
    under the setting `ratify` gives (`numbers.with_clocks`). `left_open` is each
    decision of a finished build it left open, with whether that setting puts it to
    the operator (`decisions.left_open`)."""
    end = state.ended
    if end is None:
        return None
    held = end.numbers
    setting = None
    if isinstance(held, Mapping) and not {"round_time_s", "longest_gap"} <= set(held):
        setting = ratify()
        held = numbers.with_clocks(state, held, ratify=setting)
    left = ()
    if any(one.state == decisions.OPEN for one in decisions.reached(state)):
        left = decisions.left_open(state, ratify() if setting is None else setting)
    return {"how": end.how, "by": end.by, "record": end.record, "actor": end.actor,
            "at": end.at, "questions": dict(end.questions), "numbers": held,
            "left_open": list(left)}


def ended_line(ended: dict) -> str:
    """How a round ended, in a line: `closed at <time>`, or `dropped by operator at
    <time>, record <pointer>` and where its questions went."""
    if ended["how"] == states.CLOSED:
        # Closing is nobody's act and carries no pointer.
        return f"{ended['how']} at {ended['at']}"
    went = ", ".join(f"{one} {'withdrawn' if where == 'withdrawn' else 'to ' + where}"
                     for one, where in ended["questions"].items())
    return (f"{ended['how']} by {ended['by']} at {ended['at']}, record "
            f"{ended['record']}{f'; questions: {went}' if went else ''}")


def lead_line(lead: dict | None, name: str) -> str:
    """A round's lead, as `leads.of` gives it, in a line: `lead: NAME`; for a round
    that reads its project's lead, `lead: NAME (the project's; ...)` with the command
    that records the round's own; `lead: none` with neither."""
    if lead is None:
        return "lead: none"
    if lead["from"] == leads.PROJECT:
        return (f"lead: {lead['name']} (the project's; moxi rounds mark {name} --lead "
                "NAME records the round's own)")
    return f"lead: {lead['name']}"


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
        lines = [f"round {found.name}   unreadable", f"folder: {found.round_dir}",
                 found.unreadable.reason]
        if found.unreadable.fix:
            lines.append(f"  fix: {found.unreadable.fix}")
        if found.notice is not None:
            lines.append(found.notice)
        return "\n".join(lines) + "\n"
    steps = ", ".join(str(step.get("step")) for step in shown_as["steps"]) or "no steps"
    lines = [f"round {found.name}   {shown_as['status']}   flow {shown_as['flow']} "
             f"({steps})",
             f"description: {shown_as['description']}",
             lead_line(shown_as["lead"], found.name), f"folder: {found.round_dir}"]
    lines += step_lines(shown_as["step"])
    if shown_as["close"] is not None:
        lines.append(closing.said(shown_as["close"]["waits"]))
    lines += [f"skipped: {skip_line(one)}" for one in shown_as["skips"]]
    lines += version_lines(shown_as["versions"], shown_as["standing"],
                           shown_as["threads"], shown_as["trace"])
    lines += [f"stage {stage_line(one)}" for one in shown_as["stages"]]
    if shown_as["parent"] is not None:
        lines.append(f"parent: {shown_as['parent']}")
    if shown_as["inherits"]:
        lines.append(f"inherits: {', '.join(shown_as['inherits'])}")
    if shown_as["checklist"] is not None:
        lines.append(f"checklist: {marks_line(shown_as['checklist'])}")
    if shown_as["ended"] is not None:
        lines.append(f"ended: {ended_line(shown_as['ended'])}")
        lines += [f"  {decisions.left_open_line(one)}"
                  for one in shown_as["ended"]["left_open"]]
        if shown_as["ended"]["numbers"] is not None:
            lines += ["numbers:", *(f"  {line}" for line in
                                    numbers.lines(shown_as["ended"]["numbers"]))]
    if found.notice is not None:
        lines.append(found.notice)
    if found.register_unreadable is not None:
        lines.append("what the operator has read back is not known: "
                     f"{found.register_unreadable.reason}")
    for section in SECTIONS:
        lines += ["", *section.lines(shown_as[section.key])]
    return "\n".join(lines) + "\n"


__all__ = ["ci_line", "Shown", "Section", "SECTIONS", "show", "data", "as_json", "as_text",
           "ended_line", "lead_line", "skip_line", "marks_line", "stage_line", "build_line", "build_verdict_line",
           "finding_line", "drift_line", "step_lines", "version_lines", "thread_line",
           "verdict_lines", "history_of", "repair_of", "repair_line", "check_run",
           "ci_read", "build_verdict", "command_steps", "finish_actions_of"]

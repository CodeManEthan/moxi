"""Opening a round: its folder, and the first event of its log.

`open_round("<project>/<slug>", description=..., by=...)` makes the round's folder in
the project's rounds folder and appends `round.opened` through the write path. The event
holds the description, the name of the round's flow and that flow's steps as the config
has them at that moment, the pointer to the operator's go when one is given, and the
round's lead when one is named.

The flow is the one named, else the project's `flow` setting. Its steps are copied into
the log because the round follows what it was opened with: a flow edited in the config
afterwards reaches no round that is already open.

A round may name the standing rulings of its project that it starts from. Each is one
that says what is built and that stands when the round is opened: one that was replaced
or taken back, one that only changed a record, and an id the project's list does not
hold are refused. The ids are kept in the event, and the round's design owes each a row.

A round whose flow has no design step marks the checklist items that apply at its
opening (`checklist`), since it has no design block to mark them in: a bare id for an
item of the home project, `<key>:<id>` for any other project's, or none. Its home
project's checklist not empty, an opening that says nothing is refused, as is a mark of
no item and a mark on a round whose flow has a design step, whose block marks them.

A round is opened once. A folder that already holds a log is refused, as are a flow the
config does not have and a go pointer of no known shape; a project the projects list
does not hold and a rounds folder that can't be worked out are refused before anything
is locked or made.

A rounds folder may be shared with another tool that keeps rounds of its own there,
each with a note of the same name as the one written here and no log. A folder that
holds such a note and no log is that tool's round: opening it is refused and the note is
left as it is, so the two tools never claim one round.

Nothing says that a new round is the work of one that was dropped. So an opening gives
the questions a dropped round of the project left on the project's list, and whoever
opens the round adopts the ones it takes up.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from . import checklist as marks
from . import (corrections, dropping, flows, folders, leads, log, pointers, ruling,
               stepping, write)
from .rendering import NOTE_FILE
from . import settings as own
from .errors import BadArgument, RoundsError, Unmet
from .freezing import DESIGN


@dataclass(frozen=True)
class Opened:
    """A round as it was opened: its name with the project's key as the projects list
    stores it, its folder, its flow's name and steps, and the event written. `left`
    are the questions on the project's list that came from a round since dropped, for
    whoever opens this round to adopt the ones whose work it takes up. `stepped` is
    the `flow.changed` event written with the opening when its checklist marks
    stepped the round up (`flow` and `steps` stay the flow it was opened with), else
    None. `failed` pairs
    each after-append callback that raised with what it raised. `inherits` are the
    standing rulings of the project it starts from, by id."""

    name: str
    round_dir: Path
    flow: str
    steps: tuple[dict, ...]
    event: dict
    inherits: tuple[str, ...] = ()
    left: tuple[dropping.Left, ...] = ()
    failed: tuple[tuple[Callable, Exception], ...] = ()
    stepped: dict | None = None


def open_round(name: str, *, description: str, by: str, flow: str | None = None,
               record: str | None = None, inherits: Iterable[str] | None = None,
               lead: str | None = None, checklist: Iterable[str] | None = None,
               settings=None, wait_s: float = write.WAIT_S,
               at: str | None = None) -> Opened:
    """Open the round `name`, `<project>/<slug>`, and return it as opened.

    - `description`: what the round is for, in a line.
    - `by`: who opens it, the event's actor.
    - `flow`: the flow's name; the project's `flow` setting when left out.
    - `record`: the pointer to where the operator gave the go, kept as given once its
      shape is one a pointer has.
    - `inherits`: the ids of the project's standing rulings the round starts from;
      one named twice is kept once.
    - `lead`: the agent who leads the round, checked for a lead's name's shape before
      anything is locked or made (`leads.check`); when left out the round reads its
      project's lead until one is marked.
    - `checklist`: for a flow with no design step, the checklist marks that apply,
      empty for none; one named twice is kept once. None says nothing. Any mark steps
      the round up to the flow `stepping.FULL` at its opening: the same append writes
      `round.opened`, then `flow.changed` by `stepping.LEAD` with the marks as
      `marked`.
    - `settings`: rounds' settings as `settings.load()` gives them, read now when left
      out.
    - `wait_s`: how long to wait for the round's lock; `at`: the event's time.

    Raises `BadName` or `BadArgument` for a name, description or lead that can't be
    used, `UnknownProject` or `NoFolder` for a project with no rounds folder to open it
    in, and `Refused` when the folder already holds a log, or another tool's note of a
    round and no log, the flow is not one the config has, the pointer has no shape a
    pointer has, an inherited id is not a standing ruling of the project that says
    what is built and still stands, or the checklist marks are wrong: a mark of no
    item, marks on a flow with a design step, none said where the home project has a
    checklist and the flow no design step, or marks whose step up to `stepping.FULL`
    is no step up (`stepping.judge_from`).
    A refusal leaves no folder and no event behind.
    """
    project, slug = folders.split(name)
    if not isinstance(description, str) or not description.strip():
        raise BadArgument("a round needs a description",
                          "say what the round is for with --description TEXT")
    if lead is not None:
        leads.check(lead, f"moxi rounds open {name} --description TEXT --lead NAME")
    settings = own.load() if settings is None else settings
    home = folders.home(project, settings)
    name = f"{home.project}/{slug}"
    round_dir = home.round_dir(slug)
    if round_dir.exists() and not round_dir.is_dir():
        raise RoundsError(f"the round's folder can't be made: {round_dir} is a file",
                          "move the file away, or open the round under another name")
    inherited = tuple(dict.fromkeys(inherits or ()))
    if inherits is not None and not inherited:
        raise BadArgument("--inherits names no ruling",
                          "give the ids, as in --inherits S1,S2, or leave it out")
    named = flow is not None
    flow = flow if named else own.round_setting(settings, home.project, "flow")
    known = flows.read(settings)
    steps = known.get(flow)

    def not_yet_opened(view):
        if log.exists(log.round_log(round_dir)):
            return Unmet(f"the round {name} is already there ({round_dir} holds a log), "
                         "and a round is opened once; a new round needs another name",
                         f"moxi rounds show {name}")
        return None

    def not_another_tools(view):
        # The note is written here only beside a log, so one with no log is not ours,
        # and opening would overwrite it.
        if log.exists(log.round_log(round_dir)) or not (round_dir / NOTE_FILE).exists():
            return None
        return Unmet(f"{round_dir} holds a {NOTE_FILE} and no log, so the round there "
                     "belongs to another tool and is not opened here; pick another "
                     "slug for this round",
                     f"moxi rounds open {home.project}/SLUG --description TEXT")

    def flow_exists(view):
        if steps is None:
            source = (f"there is no flow {flow!r}" if named else
                      f"the flow setting of the project {home.project} names {flow!r}, "
                      "which is no flow")
            return Unmet(f"{source}; NAME below is one of {', '.join(known)}",
                         f"moxi rounds open {name} --flow NAME --description TEXT")
        return None

    marked = None if checklist is None else tuple(dict.fromkeys(checklist))
    again_marked = f"moxi rounds open {name} --description TEXT --checklist LIST|none"

    def marks_what_applies(view):
        if steps is None:
            return None
        if flows.has(steps, DESIGN):
            if marked is None:
                return None
            return Unmet(f"the flow {flow} has a {DESIGN} step, and its design's data "
                         "block marks the checklist items that apply",
                         "mark them in the design's data block, checklist = [...]")
        if marked is None:
            if not marks.items(settings, home.project):
                return None
            return Unmet(f"{home.project} has a checklist, and a round whose flow has no "
                         f"{DESIGN} step says at its opening which items apply",
                         f"moxi rounds open {name} --description TEXT --checklist "
                         "LIST|none")
        return [Unmet(f"--checklist {problem}", f"{fix}: {again_marked}")
                for problem, fix in marks.problems(marked, home.project, settings)]

    def steps_up(view):
        if steps is None or flows.has(steps, DESIGN) or not marked:
            return None
        move = stepping.judge_from(flow, flows.recorded(steps), stepping.FULL, settings)
        if move.up:
            return None
        why = (f"the config has no flow {stepping.FULL}" if move.steps is None
               else move.why or f"{flow} is {stepping.FULL} already")
        return Unmet(f"--checklist marks {', '.join(marked)}, which steps a round whose "
                     f"flow has no {DESIGN} step up to {stepping.FULL}, and {why} is no "
                     "step up",
                     f"open it with --flow NAME, a flow with a {DESIGN} step, and mark "
                     f"the items in its design's block: moxi rounds open {name} "
                     "--description TEXT --flow NAME")

    def make_round_dir(view):
        try:
            round_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise RoundsError(f"the round's folder {round_dir} can't be made: {exc}",
                              "give the project a rounds folder that can be written "
                              "to") from None

    def go_is_a_pointer(view):
        if record is None:
            return None
        return pointers.unmet(record, f"moxi rounds open {name} --description TEXT "
                                      "--record PTR")

    def inherits_what_stands(view):
        again = (f"moxi rounds open {name} --description TEXT --inherits LIST")
        unmet = []
        for one in inherited:
            why = _not_inherited(view.project_state, one, home.project)
            if why is not None:
                unmet.append(Unmet(why, again))
        return unmet

    def opened(view, result):
        fields = {"description": description, "flow": flow, "steps": flows.recorded(steps)}
        if inherited:
            fields["inherits"] = list(inherited)
        if record is not None:
            fields["record"] = record
        if lead is not None:
            fields["lead"] = lead
        if marked is not None:
            fields["marked"] = list(marked)
        entries = [write.Entry("round.opened", fields)]
        if marked and not flows.has(steps, DESIGN):
            move = stepping.judge_from(flow, flows.recorded(steps), stepping.FULL,
                                       settings)
            entries.append(write.Entry("flow.changed", {
                "was": flow, "flow": move.flow, "steps": flows.recorded(move.steps),
                "by": stepping.LEAD, "marked": list(marked)}))
        return entries

    written = write.record(write.Where(home.project, home.rounds_dir, slug), by=by,
                           gates=[not_yet_opened, not_another_tools, flow_exists,
                                  go_is_a_pointer, inherits_what_stands,
                                  marks_what_applies, steps_up],
                           act=make_round_dir,
                           event=opened, opens=True, wait_s=wait_s, at=at)
    return Opened(name=name, round_dir=round_dir, flow=flow, steps=tuple(steps),
                  event=written.events[0], failed=written.failed, inherits=inherited,
                  stepped=written.events[1] if len(written.events) > 1 else None,
                  left=dropping.left_behind(written.view.project_state, home,
                                            settings=settings))


def _not_inherited(register, ruling_id: str, project: str) -> str | None:
    """Why the round can't start from the ruling `ruling_id` of the list of `project`,
    whose log folds to `register`; None when it can."""
    listed = f"LIST below holds standing rulings of {project} that stand"
    if not isinstance(ruling_id, str) or not corrections.ID.fullmatch(ruling_id) \
            or not ruling_id.startswith("S"):
        return (f"{ruling_id!r} is not the id of a standing ruling, S<n>; a round "
                f"starts from rulings of its project's list; {listed}")
    found = corrections.find(register, ruling_id)
    if found is None:
        return f"{project} has no standing ruling {ruling_id}; {listed}"
    if found.revoked_by is not None:
        return (f"{ruling_id} was taken back, by {found.revoked_by}, so no round "
                f"starts from it; {listed}")
    if found.replaced_by is not None:
        return (f"{ruling_id} was replaced by {found.replaced_by}, which is the "
                f"ruling that stands in its place; {listed}")
    if found.kind != ruling.SUBSTANCE:
        return (f"{ruling_id} changed a record and says nothing of what is built, so "
                f"no round starts from it; {listed}")
    return None


__all__ = ["Opened", "open_round"]

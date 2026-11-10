"""Opening a round: its folder, and the first event of its log.

`open_round("<project>/<slug>", description=..., actor=...)` makes the round's folder in
the project's rounds folder and appends `round.opened` through the write path. The event
holds the description, the name of the round's flow and that flow's steps as the config
has them at that moment, and the pointer to the operator's go when one is given.

The flow is the one named, else the project's `flow` setting. Its steps are copied into
the log because the round follows what it was opened with: a flow edited in the config
afterwards reaches no round that is already open.

A round may name the standing rulings of its project that it starts from. Each is one
that says what is built and that stands when the round is opened: one that was replaced
or taken back, one that only changed a record, and an id the project's list does not
hold are refused. The ids are kept in the event, and the round's design owes each a row.

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

from . import corrections, dropping, flows, folders, log, pointers, ruling, write
from .rendering import NOTE_FILE
from . import settings as own
from .errors import BadArgument, RoundsError, Unmet


@dataclass(frozen=True)
class Opened:
    """A round as it was opened: its name with the project's key as the projects list
    stores it, its folder, its flow's name and steps, and the event written. `left`
    are the questions on the project's list that came from a round since dropped, for
    whoever opens this round to adopt the ones whose work it takes up. `failed` pairs
    each after-append callback that raised with what it raised. `inherits` are the
    standing rulings of the project it starts from, by id."""

    name: str
    folder: Path
    flow: str
    steps: tuple[dict, ...]
    event: dict
    inherits: tuple[str, ...] = ()
    left: tuple[dropping.Left, ...] = ()
    failed: tuple[tuple[Callable, Exception], ...] = ()


def open_round(name: str, *, description: str, actor: str, flow: str | None = None,
               record: str | None = None, inherits: Iterable[str] | None = None,
               settings=None, wait_s: float = write.WAIT_S,
               at: str | None = None) -> Opened:
    """Open the round `name`, `<project>/<slug>`, and return it as opened.

    - `description`: what the round is for, in a line.
    - `actor`: who opens it, the event's actor.
    - `flow`: the flow's name; the project's `flow` setting when left out.
    - `record`: the pointer to where the operator gave the go, kept as given once its
      shape is one a pointer has.
    - `inherits`: the ids of the project's standing rulings the round starts from;
      one named twice is kept once.
    - `settings`: rounds' settings as `settings.load()` gives them, read now when left
      out.
    - `wait_s`: how long to wait for the round's lock; `at`: the event's time.

    Raises `BadName` or `BadArgument` for a name or description that can't be used,
    `UnknownProject` or `NoFolder` for a project with no rounds folder to open it in, and
    `Refused` when the folder already holds a log, or another tool's note of a round
    and no log, the flow is not one the config has, the pointer has no shape a
    pointer has, or an inherited id is not a standing ruling of the project that says
    what is built and still stands.
    A refusal leaves no folder and no event behind.
    """
    project, slug = folders.split(name)
    if not isinstance(description, str) or not description.strip():
        raise BadArgument("a round needs a description",
                          "say what the round is for with --description TEXT")
    settings = own.load() if settings is None else settings
    home = folders.home(project, settings)
    name = f"{home.project}/{slug}"
    folder = home.round_folder(slug)
    if folder.exists() and not folder.is_dir():
        raise RoundsError(f"the round's folder can't be made: {folder} is a file",
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
        if log.exists(log.round_log(folder)):
            return Unmet(f"the round {name} is already there ({folder} holds a log), "
                         "and a round is opened once; a new round needs another name",
                         f"moxi rounds show {name}")
        return None

    def not_another_tools(view):
        # The note is written here only beside a log, so one with no log is not ours,
        # and opening would overwrite it.
        if log.exists(log.round_log(folder)) or not (folder / NOTE_FILE).exists():
            return None
        return Unmet(f"{folder} holds a {NOTE_FILE} and no log, so the round there "
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

    def make_folder(view):
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise RoundsError(f"the round's folder {folder} can't be made: {error}",
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
        fields = {"description": description, "flow": flow, "steps": steps}
        if inherited:
            fields["inherits"] = list(inherited)
        if record is not None:
            fields["record"] = record
        return write.Entry("round.opened", fields)

    written = write.record(write.Where(home.project, home.folder, slug), actor=actor,
                           gates=[not_yet_opened, not_another_tools, flow_exists,
                                  go_is_a_pointer, inherits_what_stands],
                           act=make_folder,
                           event=opened, opens=True, wait_s=wait_s, at=at)
    return Opened(name=name, folder=folder, flow=flow, steps=tuple(steps),
                  event=written.events[0], failed=written.failed, inherits=inherited,
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

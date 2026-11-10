"""Opening a round: its folder, and the first event of its log.

`open_round("<project>/<slug>", description=..., actor=...)` makes the round's folder in
the project's rounds folder and appends `round.opened` through the write path. The event
holds the description, the name of the round's flow and that flow's steps as the config
has them at that moment, and the pointer to the operator's go when one is given.

The flow is the one named, else the project's `flow` setting. Its steps are copied into
the log because the round follows what it was opened with: a flow edited in the config
afterwards reaches no round that is already open.

A round is opened once. A folder that already holds a log is refused, as are a flow the
config does not have and a go pointer of no known shape; a project the projects list
does not hold and a rounds folder that can't be worked out are refused before anything
is locked or made.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from . import flows, folders, log, pointers, write
from . import settings as own
from .errors import BadArgument, RoundsError, Unmet


@dataclass(frozen=True)
class Opened:
    """A round as it was opened: its name with the project's key as the projects list
    stores it, its folder, its flow's name and steps, and the event written. `failed`
    pairs each after-append callback that raised with what it raised."""

    name: str
    folder: Path
    flow: str
    steps: tuple[dict, ...]
    event: dict
    failed: tuple[tuple[Callable, Exception], ...] = ()


def open_round(name: str, *, description: str, actor: str, flow: str | None = None,
               record: str | None = None, settings=None, wait_s: float = write.WAIT_S,
               at: str | None = None) -> Opened:
    """Open the round `name`, `<project>/<slug>`, and return it as opened.

    - `description`: what the round is for, in a line.
    - `actor`: who opens it, the event's actor.
    - `flow`: the flow's name; the project's `flow` setting when left out.
    - `record`: the pointer to where the operator gave the go, kept as given once its
      shape is one a pointer has.
    - `settings`: rounds' settings as `settings.load()` gives them, read now when left
      out.
    - `wait_s`: how long to wait for the round's lock; `at`: the event's time.

    Raises `BadName` or `BadArgument` for a name or description that can't be used,
    `UnknownProject` or `NoFolder` for a project with no rounds folder to open it in, and
    `Refused` when the folder already holds a log, the flow is not one the config has,
    or the pointer has no shape a pointer has.
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

    def opened(view, result):
        fields = {"description": description, "flow": flow, "steps": steps}
        if record is not None:
            fields["record"] = record
        return write.Entry("round.opened", fields)

    written = write.record(write.Where(home.project, home.folder, slug), actor=actor,
                           gates=[not_yet_opened, flow_exists, go_is_a_pointer],
                           act=make_folder,
                           event=opened, opens=True, wait_s=wait_s, at=at)
    return Opened(name=name, folder=folder, flow=flow, steps=tuple(steps),
                  event=written.events[0], failed=written.failed)


__all__ = ["Opened", "open_round"]

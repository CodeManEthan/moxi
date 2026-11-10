"""Shown: whether the operator was shown a ruling as it was recorded.

A ruling is recorded by someone else than the operator, in a sentence he may never have
seen. So each ruling is shown, or it is not, and a project's read-back list is the
rulings that are not. Nothing waits on it: no gate reads the mark.

A ruling is shown when one of two records covers it:

- **a mark**: a `ruling.shown` event in the log that holds the ruling, which names its
  id and carries a pointer to where he was shown the sentence. `mark(...)` writes it.
  The mark is the claim of whoever recorded it, not the operator's word, so its pointer
  must be able to be a showing: a message, a moment in a session or a page, never a
  file, never the pointer of a ruling the call names (that is where he said it), and
  never a message that came before one of those rulings was said;
- **a read-back**: a ruling of the project's own, `readback=TIME`, his word with his
  pointer that he read the list through `TIME`. It covers every ruling of the project,
  in its register and in its rounds, recorded at or before `TIME`. Each read-back is a
  ruling of its own, and the latest time among them covers all the earlier ones.

Only a pointer makes a mark: no read, print or export sets one, and none is ever taken
off. A read-back is never taken back either, and is itself never on the list.

`unshown(project)` computes the list each time it is asked and stores nothing: the
index keeps, for each log, the rulings no mark names and the read-backs, and the list
is read from those rows, or from a log itself where its row is stale. A ruling
recorded in the second the list is computed is left for the next list, so no ruling can
later appear at or before a time a list already showed.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass

from . import corrections, folders, index, log, pointers, readback, write
from . import settings as own
from . import state as states
from .errors import BadArgument, NoLog, NoRound, Unmet, Unreadable

#: A read-back's item and its time, and the two readers of them, are `readback`'s; they
#: are named here too, where everything else about showing is.
READBACK = readback.READBACK
TIME = readback.TIME
read_back = readback.read_back
through = readback.through


# -- whether a ruling is shown -----------------------------------------------------------

def is_shown(ruling: states.Ruling, project_state: states.State) -> bool:
    """Whether `ruling`, one of the project's register or of one of its rounds, is
    shown: a mark names it, or a read-back of the project covers the time it was
    recorded. `project_state` is the state of the project's own log, which holds the
    read-backs."""
    if ruling.shown is not None:
        return True
    read = through(project_state)
    return read is not None and ruling.at <= read


# -- the list ----------------------------------------------------------------------------

@dataclass(frozen=True)
class Row:
    """One ruling not yet shown: the `holder` (the round or the project whose log has
    it) and its `id`, which together name it; its `text`, None for one with no
    sentence; the item it `rules`, None for one that stands on its own; the `record`,
    the pointer to where he said it; the ruling it `supersedes` and the one it
    `revokes`, each None when it does neither; and `at`, when it was recorded."""

    holder: str
    id: str | None
    text: str | None
    rules: str | None
    record: str | None
    supersedes: str | None
    revokes: str | None
    at: str


@dataclass(frozen=True)
class ReadBack:
    """A project's read-back list: the rulings not yet shown, oldest first, and
    `through`, the time of the newest of them, which is the time a read-back of this
    list names. `through` is None for an empty list. `unreadable` holds the error of
    each round whose log could not be read: its rulings are not in the list."""

    project: str
    rows: tuple[Row, ...]
    through: str | None
    unreadable: tuple[Unreadable, ...] = ()

    @property
    def heading(self) -> str | None:
        """The line the list is shown under, or None for an empty one."""
        return None if self.through is None else f"not yet shown, through {self.through}"


def unshown(project: str, *, settings=None, now: str | None = None,
            env=None) -> ReadBack:
    """The read-back list of `project`: every ruling of its register and of its rounds,
    open or ended, that is not shown, a read-back itself aside.

    It reads the index where a log's row is fresh and the log itself where not, and
    writes nothing, the index included. `settings` is what `settings.load()` gives,
    read now when left out. `now` is the second the list is computed in, as
    `log.stamp` writes one: a ruling recorded in it, or after it, is left for the
    next list.

    Raises `UnknownProject` or `NoFolder` for a project with no rounds folder, and
    `Unreadable` when the project's own log can't be read, since the read-backs are in
    it.
    """
    home = folders.home(project, own.load() if settings is None else settings)
    now = log.stamp() if now is None else now
    register = index.read(home.project, home.folder, env=env)
    if register.unreadable is not None:
        raise register.unreadable
    read = max((item.body["through"] for item in register.items
                if item.kind == index.READ_BACK), default=None)
    rows, unreadable = [], []
    for summary in (register, *index.rounds(home.project, home.folder, env=env)):
        if summary.unreadable is not None:
            unreadable.append(summary.unreadable)
            continue
        for item in summary.items:
            body = item.body
            if (item.kind == index.RULING and body["at"] < now
                    and (read is None or body["at"] > read)):
                rows.append(Row(holder=summary.name, id=item.id, text=body["text"],
                                rules=body["rules"], record=body["record"],
                                supersedes=body["supersedes"],
                                revokes=body["revokes"], at=body["at"]))
    # Oldest first; rulings of one second stay in the order of their logs.
    rows.sort(key=lambda row: row.at)
    return ReadBack(project=home.project, rows=tuple(rows),
                    through=rows[-1].at if rows else None,
                    unreadable=tuple(unreadable))


def _logs(home: folders.Home):
    """The state of the project's log, every log of the project that could be read as
    `(holder, state)`, the project's own first, and the error of each round's that
    could not."""
    try:
        project_state = states.fold(log.read(log.project_log(home.folder)))
    except NoLog:
        project_state = states.State()
    logs, unreadable = [(home.project, project_state)], []
    for slug in folders.slugs(home.folder):
        try:
            events = log.read(log.round_log(home.round_folder(slug)))
        except NoLog:
            continue
        except Unreadable as error:
            unreadable.append(error)
            continue
        logs.append((f"{home.project}/{slug}", states.fold(events)))
    return project_state, logs, tuple(unreadable)


# -- the read-back -----------------------------------------------------------------------

def ends_a_list(time: str, *, name: str, at: str) -> write.Gate:
    """The gate of a read-back of the project `name` through `time`, recorded at `at`:
    `time` is when some ruling of the project was recorded, shown or not, and is
    earlier than `at`. A list is headed with the time of its newest ruling and never
    holds one of the second it is computed in, so any other time is one no list ended
    at."""
    again = f"moxi rounds rule {name} {READBACK}TIME --record PTR"

    def gate(view):
        home = folders.Home(view.where.project, view.where.folder)
        _state, logs, unreadable = _logs(home)
        # The project's log as read under the lock, not as read again just now.
        logs[0] = (name, view.project_state)
        unmet = []
        if not any(ruling.at == time for _holder, state in logs
                   for ruling in state.rulings if read_back(ruling) is None):
            unread = "".join(f"; {error.reason}" for error in unreadable)
            unmet.append(Unmet(
                f"no ruling of {name} was recorded at {time}, so no list of its "
                f"rulings ended there{unread}; TIME below is the time the list he "
                "read was shown through",
                again))
        if time >= at:
            unmet.append(Unmet(
                f"{time} is not yet past: a ruling may still be recorded in that "
                "second, and a read-back covers only what a list could have shown",
                f"moxi rounds rule {name} {READBACK}{time} --record PTR"))
        return unmet
    return gate


# -- the mark ----------------------------------------------------------------------------

@dataclass(frozen=True)
class Marked:
    """What a mark did: the `name` of the round or project that holds the rulings, the
    ids it `marked` as shown, the ids that were shown `already` and so got no mark,
    the pointer `record`, and the `event` written, None when every ruling was shown
    already. `failed` pairs each after-append callback that raised with what it
    raised."""

    name: str
    marked: tuple[str, ...]
    already: tuple[str, ...]
    record: str
    event: dict | None
    failed: tuple[tuple[Callable, Exception], ...] = ()


def parse_ids(text: str) -> tuple[str, ...]:
    """The ruling ids of `C12,C13`, in the order given. Raises `BadArgument` for an
    empty list, a part that is no ruling's id, or an id named twice."""
    ids = tuple(part.strip() for part in (text or "").split(","))
    wrong = [one for one in ids if not corrections.ID.fullmatch(one)]
    if wrong or len(set(ids)) != len(ids):
        problem = (f"{wrong[0]!r} is not a ruling's id" if wrong else
                   "a ruling is named twice")
        raise BadArgument(f"{text!r} can't be read: {problem}",
                          "name a round's rulings as C<n> and a project's as S<n>, "
                          "separated by commas: C12,C13")
    return ids


def mark(target: str, ids: str | Iterable[str], *, actor: str, record: str | None = None,
         settings=None, wait_s: float = write.WAIT_S, at: str | None = None) -> Marked:
    """Mark rulings as shown to the operator: `ids` of the round `target`,
    `<project>/<slug>`, as `C12,C13`, or of the project `target`'s register, as
    `S4,S5`. `record` points to where he was shown them: the message, the moment in a
    session or the page that put the sentences in front of him.

    One `ruling.shown` event is appended to the log that holds the rulings, naming the
    ids that were not shown yet. When every one was shown already nothing is written.

    Raises `BadName` or `BadArgument` for a value that can't be used, `UnknownProject`,
    `NoFolder` or `NoRound` for a round or project that can't be found, and `Refused`,
    with no ruling of the call marked, when: an id is of the other log (`C<n>` on a
    project, `S<n>` on a round); the log holds no such ruling; one is a read-back;
    there is no pointer, or one of no known shape; the pointer is a file; it is the
    pointer of a ruling the call names; or it is a message numbered lower than the
    message one of those rulings was said in.
    """
    if not isinstance(target, str) or not target.strip():
        raise BadArgument("nothing says whose rulings were shown",
                          "name a round as <project>/<slug>, or a project by its key")
    named = parse_ids(ids) if isinstance(ids, str) else parse_ids(",".join(ids))
    project, slug = folders.split(target) if "/" in target else (target, None)
    home = folders.home(project, own.load() if settings is None else settings)
    name = home.project if slug is None else f"{home.project}/{slug}"
    letter = "C" if slug is not None else "S"
    own_ids = [one for one in named if one.startswith(letter)]
    again = (f"moxi rounds mark {name} {','.join(own_ids) or letter + '<n>'} --shown "
             "--record PTR")

    def held(view):
        return view.state if slug is not None else view.project_state

    def found(view):
        rulings = {ruling.id: ruling for ruling in held(view).rulings}
        return [rulings[one] for one in own_ids if one in rulings]

    def are_named_by_what_holds_them(view):
        other = [one for one in named if not one.startswith(letter)]
        if not other:
            return None
        listed = ",".join(other)
        if slug is None:
            return Unmet(f"{listed} is a round's own ruling, and {name} is a project: "
                         "a ruling is named by the round that holds it",
                         f"moxi rounds mark {name}/<slug> {listed} --shown --record PTR")
        return Unmet(f"{listed} is of the project's register, and {name} is a round: "
                     "a register entry is named by its project",
                     f"moxi rounds mark {home.project} {listed} --shown --record PTR")

    def are_on_record(view):
        have = {ruling.id for ruling in held(view).rulings}
        listing = (f"moxi rounds show {name}" if slug is not None else
                   f"moxi rounds list {name} --waiting")
        return [Unmet(f"{name} has no ruling {one}", listing)
                for one in own_ids if one not in have]

    def are_not_his_own_word(view):
        return [Unmet(f"{ruling.id} is a read-back, the operator's own word that he "
                      "read the list: it is never on the list, and there is nothing "
                      "to show him", again)
                for ruling in found(view) if read_back(ruling) is not None]

    def is_a_showing(view):
        if record is None:
            return Unmet("a mark needs a pointer to where he was shown the sentence; "
                         f"PTR below is one token: {_SHOWINGS}", again)
        shape = pointers.shape(record)
        if shape is None:
            return Unmet(f"{record!r} is not a pointer to where he was shown the "
                         f"sentence; PTR below is one token: {_SHOWINGS}", again)
        if shape == pointers.FILE:
            return Unmet(f"{record} is a file, and a file is not a showing; PTR below "
                         f"is one token: {_SHOWINGS}", again)
        unmet = []
        for ruling in found(view):
            if ruling.record == record:
                unmet.append(Unmet(
                    f"{record} is the pointer of {ruling.id} itself: that is where he "
                    "said it, and a showing is the later message that put the "
                    "sentence as recorded in front of him", again))
            elif (shape == pointers.COMMS
                  and pointers.shape(ruling.record) == pointers.COMMS
                  and _number(record) < _number(ruling.record)):
                unmet.append(Unmet(
                    f"{record} came before {ruling.record}, where he said "
                    f"{ruling.id}: a message from before a ruling can't have shown "
                    "it to him", again))
        return unmet

    marked: list[str] = []
    already: list[str] = []

    def shown(view, result):
        for ruling in found(view):
            (already if is_shown(ruling, view.project_state) else marked).append(
                ruling.id)
        if not marked:
            return None
        return write.Entry("ruling.shown", {"ids": list(marked), "record": record},
                           log=write.ROUND if slug is not None else write.PROJECT)

    try:
        written = write.record(
            write.Where(home.project, home.folder, slug), actor=actor,
            gates=[are_named_by_what_holds_them, are_on_record, are_not_his_own_word,
                   is_a_showing],
            event=shown, wait_s=wait_s, at=at)
    except NoLog:
        raise NoRound(name) from None
    return Marked(name=name, marked=tuple(marked), already=tuple(already),
                  record=record, event=written.events[0] if written.events else None,
                  failed=written.failed)


#: The shapes a pointer to a showing has, in words, for a refusal.
_SHOWINGS = ("comms:<digits> for a message, <8 or more hex digits>@<UTC time> "
             "(2026-01-02T03:04Z) for a moment in a session, or the https:// URL of a "
             "page that listed it")


def _number(pointer: str) -> int:
    return int(pointer.partition(":")[2])


__all__ = ["READBACK", "TIME", "Row", "ReadBack", "Marked", "read_back", "through",
           "is_shown", "unshown", "ends_a_list", "parse_ids", "mark"]

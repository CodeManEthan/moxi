"""The write path: the one way a verb changes a round or a project.

`record(where, actor=..., event=..., gates=..., act=...)` runs the same steps for every
writing verb:

1. take the lock, then read the round's log, the project's log and the verb's own facts;
2. fold each log to its state;
3. evaluate every gate, and refuse with every unmet one (`Refused`), having written
   nothing;
4. do the verb's act outside the log, when it has one;
5. append the verb's events, close the round when nothing is left for it to wait
   for, call the after-append callbacks, and release the lock.

**The locks.** A round's lock file is `locks/rounds/<project>/<slug>.lock` and a project
log's is `locks/projects/<project>.lock`, both in rounds' state directory and never in a
rounds folder, so a lock file is never backed up or pushed with the logs. The two kinds
are in folders of their own because a project's key may hold dots: whatever the keys are,
no project's lock file is another project's folder of round locks, or inside one. A log
is never its own lock file. A verb on a round takes the round's lock; one that also writes the project's
log (`with_project=True`) takes the project's second, always in that order, so two verbs
can't each hold one and wait for the other. A verb on a project alone takes only the
project's. A lock that is still held after `wait_s` seconds stops the verb with `Locked`,
which names the holder's pid.

**Short and long acts.** An act that is short (freezing a file, adding a worktree) runs
at step 4 with the lock held. A long one (`long_act=True`: a command that may run for
minutes) must not keep every other verb off the round, so the lock is released after
step 3, the act runs with no lock held, and then the lock is taken again, the logs are
read again and the events appended. `event` is given what that second read found, since
other verbs may have written meanwhile; what it appends is a fact about the act (this
ran, with this result), which is true whatever else was written.

**After the append.** A round closes itself, here: once a verb's events are appended
to a round, the log as it then stands is asked whether its close waits for anything,
and when it does not the closing event is appended too (`Written.closed`). That is
asked after every write to a round, so a close that stopped short is made by the next
one. Then the callbacks in `ON_APPEND` (or `on_append`) run, with the lock still held.
A close or a callback that raises does not undo the events, which are on disk by then:
the rest still runs, and `Written.failed` names it for the verb to report. Whatever
such a callback keeps is rebuilt by the next verb's write.

**Watching.** `with watching() as writes:` collects the `Written` of every write made
inside it, in order. Whatever runs a verb for a person uses it to say what a write did
beyond the verb's own lines: that it closed the round.

A verb stopped between its act and its append is simply run again, so an act must be safe
to repeat or leave something a rerun finds.
"""

from __future__ import annotations

import contextvars
import dataclasses
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path

from moxi import files

from . import events as event_types
from . import index, log, paths, state
from .errors import Locked, NoLog, Refused, Unmet

#: How long a verb waits for a lock before it refuses, in seconds.
WAIT_S = 30.0

#: The round's log, as the log an `Entry` goes to.
ROUND = "round"
#: The project's log, as the log an `Entry` goes to.
PROJECT = "project"

#: The folder of the locks folder that holds each project's folder of round locks.
ROUND_LOCKS_DIR = "rounds"
#: The folder of the locks folder that holds each project log's lock file.
PROJECT_LOCKS_DIR = "projects"


def round_lock(project: str, slug: str, env=None) -> Path:
    """The lock file of the round `<project>/<slug>`, in rounds' state directory."""
    return paths.locks_dir(env) / ROUND_LOCKS_DIR / project / f"{slug}.lock"


def project_lock(project: str, env=None) -> Path:
    """The lock file of the project's log, in rounds' state directory."""
    return paths.locks_dir(env) / PROJECT_LOCKS_DIR / f"{project}.lock"


@dataclass(frozen=True)
class Where:
    """What a verb writes to: the round `<project>/<slug>`, or with no `slug` the project
    alone. `folder` is the project's rounds folder."""

    project: str
    folder: Path
    slug: str | None = None

    @property
    def round_log(self) -> Path | None:
        """The round's log, or None for a project alone."""
        return None if self.slug is None else log.round_log(Path(self.folder) / self.slug)

    @property
    def project_log(self) -> Path:
        """The project's log."""
        return log.project_log(self.folder)


@dataclass(frozen=True)
class Entry:
    """One event a verb appends: its type, its fields, and the log it goes to (`ROUND` or
    `PROJECT`)."""

    type: str
    fields: Mapping[str, object]
    log: str = ROUND


@dataclass(frozen=True)
class View:
    """What a verb read under the lock: each log's events and the state they fold to, and
    `facts`, whatever the verb's own `facts` callable returned (None without one). A
    project alone has no round events; a project with no log yet has no project events."""

    where: Where
    events: list[dict]
    state: state.State
    project_events: list[dict]
    project_state: state.State
    facts: object = None


@dataclass(frozen=True)
class Written:
    """What a write did. `events` are the verb's events appended, in order, as written;
    `result` is what the act returned; `view` is what was read before the append.
    `closed` is the event that closed the round, when this write did. `round_events`
    and `project_events` are each whole log after it, a closing event included.
    `failed` pairs the close or each after-append callback that raised with what it
    raised."""

    where: Where
    view: View
    events: tuple[dict, ...]
    round_events: tuple[dict, ...]
    project_events: tuple[dict, ...]
    result: object = None
    failed: tuple[tuple[Callable, Exception], ...] = ()
    closed: dict | None = None


Gate = Callable[[View], "Unmet | Iterable[Unmet] | None"]

_WATCHED: contextvars.ContextVar[list[Written] | None] = contextvars.ContextVar(
    "moxi_rounds_watched_writes", default=None)


@contextmanager
def watching() -> Iterator[list[Written]]:
    """Collect every write made inside the block: the list given gains each one's
    `Written` as it is made, in order."""
    writes: list[Written] = []
    token = _WATCHED.set(writes)
    try:
        yield writes
    finally:
        _WATCHED.reset(token)


def _render(written: Written) -> None:
    """Write the generated files of the logs the write touched."""
    # Imported here: what renders a log reads it as the verbs do, and they import
    # this module.
    from . import rendering

    rendering.on_append(written)


def _close(where: Where, round_events: Sequence[dict], *, actor: str,
           at: str | None) -> dict | None:
    """Close the round whose log now holds `round_events`, when its close waits for
    nothing more, and return the closing event; None when the round stays as it is,
    and for a write on a project alone."""
    # Imported here: what says a round's steps reads what the verbs record, and
    # they import this module.
    from . import closing

    if where.slug is None:
        return None
    entry = closing.entry(state.fold(round_events))
    if entry is None:
        return None
    return log.append(where.round_log, entry.type, entry.fields, actor=actor, at=at)


#: The callbacks every write calls after its append, in order, each with the `Written`.
#: A module that keeps something derived from the logs adds its function here.
ON_APPEND: tuple[Callable[[Written], None], ...] = (index.on_append, _render)


def record(where: Where, *, actor: str,
           event: Callable[[View, object], Entry | Iterable[Entry] | None],
           gates: Sequence[Gate] = (),
           act: Callable[[View], object] | None = None,
           facts: Callable[[Where], object] | None = None,
           long_act: bool = False, with_project: bool = False, opens: bool = False,
           wait_s: float = WAIT_S, at: str | None = None,
           on_append: Sequence[Callable[[Written], None]] | None = None,
           env=None) -> Written:
    """Run one verb's write on `where` and return what it wrote.

    - `gates`: each is called with the `View` and returns None when it holds, or the
      `Unmet` (or several) when it does not. All are evaluated; any unmet raises
      `Refused` with every one, before the act and with nothing written.
    - `act`: the verb's work outside the log, called with the `View`. What it returns is
      handed to `event`. If it raises, nothing is appended.
    - `event`: called with the `View` and the act's result; returns the `Entry` or
      entries to append, or None for none. With `long_act` the `View` is the one read
      after the act, and `event` may raise `Refused` to append nothing.
    - `facts`: read under the lock with the logs, for what a verb needs from outside
      them (git, say); the result is `View.facts`. With `long_act` it is read again.
    - `long_act`: the act runs with no lock held (see the module docstring).
    - `with_project`: the verb also writes the project's log, so its lock is taken too.
    - `opens`: the verb makes the round, so a round's log that does not exist reads as
      no events. Without it a missing round's log raises `NoLog`.
    - `wait_s`: how long to wait for each lock before `Locked`.
    - `at`: the events' time, as `log.stamp` writes one; now when left out.
    - `on_append`: the after-append callbacks, in place of `ON_APPEND`.

    An unreadable log raises `Unreadable` at the read, before any gate.
    """
    if long_act and act is None:
        raise ValueError("a long act needs the act")
    callbacks = ON_APPEND if on_append is None else tuple(on_append)

    def hold():
        return _hold(where, with_project=with_project, wait_s=wait_s, env=env)

    def append(view: View, result: object) -> Written:
        entries = _entries(event(view, result), where, with_project=with_project)
        return _append(view, entries, result, actor=actor, at=at, callbacks=callbacks)

    with hold():
        view = _read(where, facts, opens=opens)
        unmet = _unmet(gates, view)
        if unmet:
            raise Refused(unmet)
        if not long_act:
            return append(view, None if act is None else act(view))
    result = act(view)
    with hold():
        return append(_read(where, facts, opens=opens), result)


# -- the locks ---------------------------------------------------------------------------

@contextmanager
def _hold(where: Where, *, with_project: bool, wait_s: float, env) -> Iterator[None]:
    """Hold the locks a write on `where` needs: the round's, then the project's."""
    with ExitStack() as held:
        if where.slug is not None:
            held.enter_context(_lock(round_lock(where.project, where.slug, env), wait_s))
        if with_project or where.slug is None:
            held.enter_context(_lock(project_lock(where.project, env), wait_s))
        yield


@contextmanager
def _lock(path: Path, wait_s: float) -> Iterator[None]:
    held = files.lock(path, timeout_s=wait_s)
    try:
        held.__enter__()
    except files.LockTimeout as stop:
        raise Locked(path, stop.holder, wait_s) from None
    try:
        yield
    finally:
        held.__exit__(None, None, None)


# -- read, gate, append ------------------------------------------------------------------

def _read(where: Where, facts: Callable[[Where], object] | None, *, opens: bool) -> View:
    if where.slug is None:
        round_events = []
    else:
        try:
            # The verb that opens a round reads whatever is there, and its own gate
            # refuses a folder that holds a log; every other verb reads a round.
            read = log.read if opens else log.read_round
            round_events = read(where.round_log)
        except NoLog:
            if not opens:
                raise
            round_events = []
    try:
        project_events = log.read(where.project_log)
    except NoLog:
        # A project has no log until something is first recorded on it.
        project_events = []
    return View(where=where, events=round_events, state=state.fold(round_events),
                project_events=project_events, project_state=state.fold(project_events),
                facts=None if facts is None else facts(where))


def _unmet(gates: Sequence[Gate], view: View) -> list[Unmet]:
    """Every unmet condition of every gate: no gate is skipped because an earlier one
    failed, so one refusal lists all there is to fix."""
    unmet: list[Unmet] = []
    for gate in gates:
        found = gate(view)
        if found is None:
            continue
        for one in (found,) if isinstance(found, Unmet) else tuple(found):
            if not isinstance(one, Unmet):
                raise TypeError(f"a gate returns Unmet conditions, not {one!r}")
            unmet.append(one)
    return unmet


def _entries(found: Entry | Iterable[Entry] | None, where: Where, *,
             with_project: bool) -> list[Entry]:
    """The verb's entries as a list, each checked before any is written: once the first
    is appended a later bad one could not take it back."""
    if found is None:
        entries = []
    elif isinstance(found, Entry):
        entries = [found]
    else:
        entries = list(found)
    for entry in entries:
        if not isinstance(entry, Entry):
            raise TypeError(f"a verb appends Entry values, not {entry!r}")
        if entry.log == ROUND:
            if where.slug is None:
                raise ValueError("a write on a project alone has no round's log to "
                                 "append to")
        elif entry.log == PROJECT:
            if where.slug is not None and not with_project:
                raise ValueError("an event for the project's log needs with_project=True, "
                                 "which takes that log's lock")
        else:
            raise ValueError(f"{entry.log!r} is not a log; use ROUND or PROJECT")
        event_types.check(entry.type, entry.fields)
    return entries


def _append(view: View, entries: list[Entry], result: object, *, actor: str,
            at: str | None, callbacks: Sequence[Callable[[Written], None]]) -> Written:
    where = view.where
    appended = []
    after = {ROUND: list(view.events), PROJECT: list(view.project_events)}
    for entry in entries:
        path = where.round_log if entry.log == ROUND else where.project_log
        written = log.append(path, entry.type, entry.fields, actor=actor, at=at)
        appended.append(written)
        after[entry.log].append(written)
    failed = []
    closed = None
    try:
        closed = _close(where, after[ROUND], actor=actor, at=at)
    except Exception as error:  # the verb's events are on disk; the next write closes
        failed.append((_close, error))
    if closed is not None:
        after[ROUND].append(closed)
    done = Written(where=where, view=view, events=tuple(appended),
                   round_events=tuple(after[ROUND]), project_events=tuple(after[PROJECT]),
                   result=result, closed=closed)
    for callback in callbacks:
        try:
            callback(done)
        except Exception as error:  # the events are on disk; nothing here undoes them
            failed.append((callback, error))
    done = dataclasses.replace(done, failed=tuple(failed)) if failed else done
    watched = _WATCHED.get()
    if watched is not None:
        watched.append(done)
    return done


__all__ = ["WAIT_S", "ROUND", "PROJECT", "ROUND_LOCKS_DIR", "PROJECT_LOCKS_DIR",
           "ON_APPEND", "round_lock", "project_lock",
           "Where", "Entry", "View", "Written", "Gate", "record", "watching"]

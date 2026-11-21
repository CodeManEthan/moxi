"""The sweep of leftover folders: the worktrees and scratch folders that ended builds left
on this machine, removed once rounds can prove each may go.

`finish` and `drop` remove the folders of the build they end under the removal rule
(`removal`), and leave what it keeps: a removal that failed, a worktree that held work, a
scratch folder a process still held. The sweep finds those folders again from the
records and removes them, and nothing else. It changes no record of any round, so it
takes no pointer and writes no round's log.

**Candidates.** Each `build.commissioned` event in a round's log, in the rounds folder of
any project, names two folders. One is a candidate when its build has ended (finished, or
dropped by its own drop or its round's) and something is at the path the record names.
Where several ended builds name one place, the newest names it: a place is not reused
while an earlier build's marked folder is there. A folder no record names is never a
candidate. The rules are read for each, in this order, and the first that fails keeps
the folder and is named in the account:

- A, ownership, as `removal.judge` reads it, and before it the rest of A1: no build that
  has not ended, in any round, records the same place.
- B, no lost work, as `removal.judge` reads it, and the second half of B6: a worktree a
  `finish` with no merge kept stays until its branch's tip is on the target.
- C, every log was read. An open build may be recorded in a log that can't be read, so
  while one can't be, the sweep removes nothing and names the log. Two projects that
  reach one rounds folder count the same way, since neither's logs are read.
- D, no process uses it: no process of the user's has its working folder, an open file,
  a mapped file, an argument word or an environment value at or under the place, read
  through psutil. A field that can't be read counts as nothing; E and B stand behind it.
- E, it is quiet: its marker was written more than `sweep.grace_hours` ago, by the time it
  carries, and no entry under it changed since, by the latest of an entry's modified and
  change times (on Windows its modified and creation times; a program can set the
  modified time back, not the other).

**The arm.** Every sweep is a dry run until `sweep.armed` is true, whatever it is asked:
it reads, removes nothing and writes nothing, and gives the account. An armed run with
`remove` removes each candidate every rule lets go. Just before each removal it reads C,
D and E again, then A and B, so the last read before the removal call is of the work; a
file written in between keeps the folder. The removal is the rule's own (`removal.take`):
`git worktree remove` without `--force` for a worktree, and for a scratch folder a rename
into the trash and a delete there. A run starts no removal once `TIME_LIMIT_S` have passed
since it began; what is left waits for the next run. One run at a time: a run takes the
sweep's lock without waiting, and one that finds it held stops (`Running`). An armed run
with `remove` writes its start time to `sweep.started`.

**The trash.** Before a scratch folder's rename the sweep logs the trash entry's name, and
after its delete a second line. A later run deletes only an entry its log names with no
second line, which is what a run stopped between the two leaves; anything else in the
trash is reported and left. `finish` and `drop` write the same two lines for the scratch
folder of the build they end (`trash_log`), naming A and B as proven, so an entry one of
them left when stopped is the sweep's to delete too.

**The scratch gate.** `finish` and `drop` read D for the scratch folder of each build
they end before they write anything (`scratch_users`), and refuse while a process uses
it. Mapped files are not read there, and the verb's own process and those it runs
under are not counted. Where the processes can't be read the verb says the gate was not
run and goes on.

**What it records.** Nothing in any round's log, which machines share. Each removal, each
trash rename and each trash delete is one line of `sweep.jsonl` in rounds' state
directory, the store `sweeps`: the time, the act, the place, the build (round, stage,
repo, branch, build id) and the rules that were proven. A dry run writes nothing there.
"""

from __future__ import annotations

import os
import stat
import sys
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from moxi import exit as exits
from moxi import files

from . import builds, checking, folders, log, paths, places, removal
from . import settings as own
from . import state as states
from .errors import NoLog, RoundsError, SharedFolder, Unmet, Unreadable
from .gitreads import Git, GitError

#: The command that removes what the dry run says would go.
REMOVE = "moxi rounds drop --leftovers --remove"
#: The command that gives the dry run.
DRY_RUN = "moxi rounds drop --leftovers"
#: A run starts no removal once this long has passed since it began.
TIME_LIMIT_S = 60.0
#: The sweep's lock file, in the folder of lock files.
LOCK_FILE = "sweep.lock"
#: The file an armed run with `remove` writes its start time to, in the state directory.
STARTED_FILE = "sweep.started"
#: The sweep's log, the store `sweeps`, in the state directory.
LOG_FILE = "sweep.jsonl"
#: The log moves aside to `sweep.jsonl.1` once it would pass this size.
LOG_MAX_BYTES = 1_000_000

#: What became of a leftover folder.
WOULD_GO = "would go"
REMOVED = "removed"
KEPT = "kept"
LEFT = "left"
#: What became of a trash entry.
WOULD_DELETE = "would delete"
DELETED = "deleted"

#: The rules a removal proved, as its log line names them.
PROVEN = ("A", "B", "C", "D", "E")
#: The rules `finish` and `drop` prove for the folders of the build they end.
FINISH_PROVEN = ("A", "B")
#: The acts of the sweep's log.
RENAMED = "renamed"
_TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


class Running(RoundsError):
    """Another sweep holds the sweep's lock."""

    exit_code = exits.REFUSED

    def __init__(self, holder: int | None):
        who = f"pid {holder}" if holder else "another process"
        super().__init__(f"another sweep of leftover folders is running ({who}), so this "
                         "one stops", f"run it again once {who} is done; each run takes "
                         "what it can and leaves the rest for the next")


@dataclass(frozen=True)
class Candidate:
    """One folder an ended build's record names: the round `name`, its
    `build.commissioned` event, the folder's `kind` (`removal.WORKTREE` or
    `removal.SCRATCH`), and whether the build finished with no merge (`no_merge`)."""

    name: str
    commissioned: Mapping
    kind: str
    no_merge: bool = False

    @property
    def place(self) -> Path:
        """The path the record names."""
        return Path(str(self.commissioned[self.kind]))

    def names(self) -> dict:
        """The build the folder is of, as the account and the log name it."""
        return _names(self.name, self.commissioned)


def _names(name: str, commissioned: Mapping) -> dict:
    return {"round": name, "stage": str(commissioned.get("stage")),
            "repo": str(commissioned.get("repo")),
            "branch": str(commissioned.get("branch")),
            "build": str(commissioned.get("build"))}


@dataclass(frozen=True)
class Leftover:
    """A candidate as the run left it: its `fate` (`WOULD_GO`, `REMOVED`, `KEPT` or
    `LEFT`), the `place` looked at, and for one kept the `kept` that names the check."""

    candidate: Candidate
    fate: str
    place: Path
    kept: removal.Kept | None = None

    def line(self) -> str:
        """The account's line for it."""
        names = self.candidate.names()
        place_name = "worktree" if self.candidate.kind == removal.WORKTREE else "scratch folder"
        of = (f"the {place_name} of {names['round']} {names['stage']} "
              f"({names['repo']} {names['branch']})")
        if self.fate == KEPT and self.kept is not None:
            return f"{self.fate} {self.place}: {of}; {self.kept.line()}"
        if self.fate == LEFT:
            return (f"{self.fate} {self.place}: {of}; the run passed "
                    f"{TIME_LIMIT_S:g} seconds, so the next run takes it")
        return f"{self.fate} {self.place}: {of}"

    def as_json(self) -> dict:
        return {**self.candidate.names(), "kind": self.candidate.kind,
                "place": str(self.place), "fate": self.fate,
                "check": None if self.kept is None else self.kept.check,
                "why": None if self.kept is None else self.kept.why,
                "work": [] if self.kept is None else list(self.kept.work)}


@dataclass(frozen=True)
class TrashEntry:
    """One entry of the trash: its `name`, its `path`, its `fate` (`WOULD_DELETE`,
    `DELETED` or `LEFT`) and `why`."""

    name: str
    path: Path
    fate: str
    why: str = ""

    def line(self) -> str:
        return f"trash {self.fate} {self.path}{': ' + self.why if self.why else ''}"

    def as_json(self) -> dict:
        return {"name": self.name, "path": str(self.path), "fate": self.fate,
                "why": self.why}


@dataclass(frozen=True)
class Swept:
    """One run's account: whether the sweep is `armed`, whether this run was `removing`,
    each `leftovers` folder and `trash` entry with what became of it, the logs that
    could not be read (`unreadable`), and whether the run reached its time limit."""

    armed: bool
    removing: bool
    leftovers: tuple[Leftover, ...] = ()
    trash: tuple[TrashEntry, ...] = ()
    unreadable: tuple[str, ...] = ()
    timed_out: bool = False

    @property
    def would_go(self) -> tuple[Leftover, ...]:
        """The folders a removing run would take."""
        return tuple(one for one in self.leftovers if one.fate == WOULD_GO)

    @property
    def clear(self) -> bool:
        """Whether no ended build's folder and no trash entry is left on this machine."""
        return not self.leftovers and not self.trash

    def lines(self) -> list[str]:
        """The account, one line each."""
        return ([one.line() for one in self.leftovers]
                + [one.line() for one in self.trash]
                + [f"unreadable: {reason}" for reason in self.unreadable])

    def as_json(self) -> dict:
        return {"armed": self.armed, "removing": self.removing,
                "leftovers": [one.as_json() for one in self.leftovers],
                "trash": [one.as_json() for one in self.trash],
                "unreadable": list(self.unreadable), "timed_out": self.timed_out}


def lock_file(env=None) -> Path:
    """The sweep's lock file, STATE/rounds/locks/sweep.lock."""
    return paths.locks_dir(env) / LOCK_FILE


def started_file(env=None) -> Path:
    """The file an armed run with `remove` writes its start time to."""
    return paths.state_dir(env) / STARTED_FILE


def log_file(env=None) -> Path:
    """The sweep's log, STATE/rounds/sweep.jsonl."""
    return paths.state_dir(env) / LOG_FILE


def account(*, settings=None) -> Swept:
    """The dry run: what a removing run would take and what it would keep, and why.
    Takes no lock and writes nothing; the doctor's row reads it."""
    settings = own.load() if settings is None else settings
    return _sweep(settings, removing=False)


def run(*, remove: bool, settings=None) -> Swept:
    """One run of the sweep under its lock: with `remove`, while `sweep.armed` is true,
    it removes what every rule lets go; otherwise it is the dry run. Raises `Running`
    when another run holds the lock."""
    settings = own.load() if settings is None else settings
    where = lock_file()
    where.parent.mkdir(parents=True, exist_ok=True)
    held = files.lock(where, timeout_s=0)
    try:
        held.__enter__()
    except files.LockTimeout as exc:
        raise Running(exc.holder) from None
    try:
        removing = bool(remove and settings["sweep.armed"])
        if removing:
            files.atomic_write(started_file(), log.stamp() + "\n")
        return _sweep(settings, removing=removing)
    finally:
        held.__exit__(None, None, None)


# -- the run -------------------------------------------------------------------------------

def _monotonic() -> float:
    return time.monotonic()


def _now() -> float:
    return time.time()


def _sweep(settings, *, removing: bool) -> Swept:
    began = _monotonic()
    records = _records(settings)
    trash, timed_out = _trash(removing=removing, began=began)
    processes = _Processes()
    leftovers = []
    for candidate in _by_place(records.candidates):
        found = _judge(candidate, records, settings, processes)
        if found is None:
            continue
        if isinstance(found, removal.Kept):
            leftovers.append(Leftover(candidate, KEPT, found.place, found))
        elif not removing:
            leftovers.append(Leftover(candidate, WOULD_GO, found.place))
        elif _monotonic() - began > TIME_LIMIT_S:
            timed_out = True
            leftovers.append(Leftover(candidate, LEFT, found.place))
        else:
            leftovers.append(_remove(candidate, found, settings))
    return Swept(armed=bool(settings["sweep.armed"]), removing=removing,
                 leftovers=tuple(leftovers), trash=tuple(trash),
                 unreadable=records.unreadable, timed_out=timed_out)


def _remove(candidate: Candidate, found: removal.Proven, settings) -> Leftover:
    """Read the rules again and remove the folder `found`: C, D and E first, then A and
    B, so the read of the work comes last before the removal call."""
    records = _records(settings)
    kept = (_open_here(candidate, records) or _unread(found, records)
            or _in_use(candidate, found, _Processes()) or _busy(found, settings))
    if kept is None:
        again = _owned_and_clean(candidate, settings)
        if again is None:
            return Leftover(candidate, KEPT, found.place,
                            removal.Kept(found.place, "A3", "it is no longer there"))
        if isinstance(again, removal.Kept):
            kept = again
        else:
            found = again
    if kept is not None:
        return Leftover(candidate, KEPT, kept.place, kept)
    names = candidate.names()

    def on_rename(entry: Path) -> None:
        _log(RENAMED, found.place, names, kind=candidate.kind, entry=entry.name)

    def on_delete(entry: Path, gone: bool) -> None:  # noqa: FBT001 -- removal.take's callback protocol passes gone positionally
        _log(DELETED, entry, names, kind=candidate.kind, entry=entry.name, gone=gone)

    removed = removal.take(found, on_rename=on_rename, on_delete=on_delete)
    if isinstance(removed, removal.Kept):
        return Leftover(candidate, KEPT, removed.place, removed)
    if candidate.kind == removal.WORKTREE:
        _log(REMOVED, found.place, names, kind=candidate.kind)
    return Leftover(candidate, REMOVED, found.place)


def _log(act: str, place: Path, names: Mapping, *, proven: Iterable[str] = PROVEN,
         env=None, **more) -> None:
    files.append_record(log_file(env), {"at": log.stamp(), "act": act, "place": str(place),
                                        **names, **more, "proven": list(proven)},
                        max_bytes=LOG_MAX_BYTES, durable=True)


def trash_log(name: str, commissioned: Mapping, *, env=None) -> dict:
    """The `on_rename` and `on_delete` of `removal.take` for `finish` and `drop`, which
    remove a build's scratch folder through the trash under A and B: they write its
    trash entry's lines in this log as a sweep writes its own, so the account names the
    entry and a later sweep deletes one a stopped run left."""
    names = _names(name, commissioned)
    place = Path(str(commissioned.get(removal.SCRATCH)))

    def on_rename(entry: Path) -> None:
        # Raised here, the rename is not made and the folder is kept.
        _log(RENAMED, place, names, proven=FINISH_PROVEN, env=env,
             kind=removal.SCRATCH, entry=entry.name)

    def on_delete(entry: Path, gone: bool) -> None:  # noqa: FBT001 -- removal.take's callback protocol passes gone positionally
        try:
            _log(DELETED, entry, names, proven=FINISH_PROVEN, env=env,
                 kind=removal.SCRATCH, entry=entry.name, gone=gone)
        except OSError:
            # The folder is already out of its place; with no second line a later
            # sweep deletes what is left of the entry, and a line naming a gone entry
            # is never read.
            pass

    return {"on_rename": on_rename, "on_delete": on_delete}


# -- the records ---------------------------------------------------------------------------

@dataclass(frozen=True)
class _Records:
    """What the logs say: the `candidates`, the places builds not ended record (`open`,
    by `_key`, naming the round and stage), and the logs that can't be read."""

    candidates: tuple[Candidate, ...]
    open: Mapping[str, str]
    unreadable: tuple[str, ...]


def _records(settings) -> _Records:
    homes, unresolved = folders.homes(settings)
    candidates, held, unreadable = [], {}, []
    for exc in unresolved:
        if isinstance(exc, SharedFolder):
            unreadable.append(exc.reason)
    for home in homes:
        for slug in folders.slugs(home.rounds_dir):
            name = f"{home.project}/{slug}"
            try:
                events = log.read_round(log.round_log(home.round_dir(slug)))
            except NoLog:  # removed since the folder was listed
                continue
            except Unreadable as exc:
                unreadable.append(exc.reason)
                continue
            except OSError as exc:
                unreadable.append(f"the log of {name} can't be read: {exc}")
                continue
            state = states.fold(events)
            for build in builds.builds(state):
                event = checking.commission_of(state, build)
                for kind in (removal.WORKTREE, removal.SCRATCH):
                    recorded = event.get(kind)
                    if not isinstance(recorded, str) or not recorded:
                        continue
                    if build.ended is None:
                        for key in _keys(Path(recorded)):
                            held[key] = f"{name} {build.stage}"
                    else:
                        candidates.append(Candidate(
                            name, event, kind,
                            no_merge=build.ended == builds.FINISHED and not build.landings))
    return _Records(tuple(candidates), held, tuple(unreadable))


def _keys(path: Path) -> set[str]:
    """The forms a place is matched by: its text and its real path, folded to one case
    where paths compare without it."""
    return {os.path.normcase(str(path)), os.path.normcase(os.path.realpath(path))}


def _by_place(candidates: Iterable[Candidate]) -> list[Candidate]:
    """One candidate per place, the newest record naming it, each with something at its
    place, in the order first recorded."""
    newest: dict[str, Candidate] = {}
    for one in candidates:
        if not os.path.lexists(one.place):
            continue
        key = os.path.normcase(str(one.place))
        before = newest.get(key)
        if before is None or (str(one.commissioned.get("at") or "")
                              >= str(before.commissioned.get("at") or "")):
            newest[key] = one
    return list(newest.values())


# -- the rules -----------------------------------------------------------------------------

def _judge(candidate: Candidate, records: _Records, settings,
           processes: _Processes) -> removal.Proven | removal.Kept | None:
    """A, B, C, D and E for one candidate, in that order: `Proven` when all hold, else
    `Kept` with the first that fails, or None when nothing is at its place."""
    kept = _open_here(candidate, records)
    if kept is not None:
        return kept
    found = _owned_and_clean(candidate, settings)
    if not isinstance(found, removal.Proven):
        return found
    return (_unread(found, records) or _in_use(candidate, found, processes)
            or _busy(found, settings) or found)


def _open_here(candidate: Candidate, records: _Records) -> removal.Kept | None:
    """The rest of A1: no build that has not ended records the same place."""
    for key in _keys(candidate.place):
        holder = records.open.get(key)
        if holder is not None:
            return removal.Kept(candidate.place, "A1", "a build that has not ended "
                                f"records the same place: {holder}")
    return None


def _owned_and_clean(candidate: Candidate, settings) -> removal.Proven | removal.Kept | None:
    """A and B as the removal rule reads them, then the second half of B6."""
    try:
        found = removal.judge(candidate.name, candidate.commissioned, candidate.kind,
                              settings=settings)
    except RoundsError as exc:
        # What can't be read counts as work.
        return removal.Kept(candidate.place, "read", exc.reason)
    if not isinstance(found, removal.Proven):
        return found
    return _kept_by_finish(candidate, found) or found


def _kept_by_finish(candidate: Candidate, found: removal.Proven) -> removal.Kept | None:
    """B6's second half: a worktree a finish with no merge kept stays until its branch's
    tip is on the target."""
    if candidate.kind != removal.WORKTREE or not candidate.no_merge:
        return None
    branch = str(candidate.commissioned.get("branch"))
    target = str(candidate.commissioned.get("target"))
    git = Git(found.root)
    try:
        tip, head = git.branch(branch), git.branch(target)
        on = tip is not None and head is not None and git.is_ancestor(tip, head)
    except GitError as exc:
        return removal.Kept(found.place, "B6", f"git can't read whether {branch} is on "
                            f"{target}: {exc.reason}")
    if on:
        return None
    return removal.Kept(found.place, "B6", f"a finish with no merge kept it, and the tip "
                        f"of {branch} is not on {target}")


def _unread(found: removal.Proven, records: _Records) -> removal.Kept | None:
    """C: every log was read."""
    if not records.unreadable:
        return None
    return removal.Kept(found.place, "C", "a log that can't be read may record a build "
                        "that has not ended here: " + "; ".join(records.unreadable))


def _in_use(candidate: Candidate, found: removal.Proven,
            processes: _Processes) -> removal.Kept | None:
    """D: no process of the user's uses the place."""
    users = processes.using((found.place, candidate.place))
    if not users:
        return None
    return removal.Kept(found.place, "D", "a running process uses it", tuple(users))


def _busy(found: removal.Proven, settings) -> removal.Kept | None:
    """E: the marker was written, and nothing under the place changed, longer than the
    grace ago."""
    hours = int(settings["sweep.grace_hours"])
    since = _now() - hours * 3600
    within = f"within the grace of {hours} hour{'' if hours == 1 else 's'}"
    marker = (places.worktree_marker(found.place) if found.kind == removal.WORKTREE
              else places.read_marker(found.place / places.SCRATCH_MARKER))
    if marker is None:
        return removal.Kept(found.place, "E", "its marker can't be read for its time")
    try:
        written = datetime.strptime(marker.at, _TIME_FORMAT).replace(
            tzinfo=timezone.utc).timestamp()
    except ValueError:
        return removal.Kept(found.place, "E", f"its marker's time {marker.at!r} can't be "
                            "read")
    if written > since:
        return removal.Kept(found.place, "E", f"its marker was written at {marker.at}, "
                            f"{within}")
    changed = _changed_since(found.place, since)
    if changed is not None:
        return removal.Kept(found.place, "E", f"{changed} changed {within}")
    return None


def _changed_since(place: Path, since: float) -> str | None:
    """The first entry at or under `place`, read without following a link, that changed
    after `since`, or one that can't be read."""
    left = [place]
    while left:
        at = left.pop()
        try:
            found = os.lstat(at)
        except OSError:
            return str(at)
        if _latest(found) > since:
            return str(at)
        if stat.S_ISDIR(found.st_mode) and not stat.S_ISLNK(found.st_mode):
            try:
                with os.scandir(at) as entries:
                    left += [Path(entry.path) for entry in entries]
            except OSError:
                return str(at)
    return None


def _latest(found: os.stat_result) -> float:
    """The latest of the times a program can't all set back: on Windows the modified and
    creation times, elsewhere the modified and change times."""
    if sys.platform == "win32":
        born = getattr(found, "st_birthtime", found.st_ctime)
        return max(found.st_mtime, born)
    return max(found.st_mtime, found.st_ctime)


@dataclass(frozen=True)
class ScratchUsers:
    """Who uses a build's scratch folder, as `scratch_users` read it: the folder
    (`place`), each process with a path at or under it (`users`, as `pid <n> (<name>):
    <path>`), and why the processes could not be read (`unread`, None when they were;
    `users` is then empty)."""

    place: Path
    users: tuple[str, ...] = ()
    unread: str | None = None


def scratch_users(commissioned: Mapping) -> ScratchUsers | None:
    """The processes that use the scratch folder a build's commission record
    (`commissioned`) names; None where it names none or no folder is there. `finish`
    and `drop` read it before they move the folder, and refuse while a process uses
    it. The processes are read as D reads them, apart from mapped files, and apart
    from this process and every one it runs under, so the shell or the wrapper that
    runs the verb is not named."""
    named = commissioned.get(removal.SCRATCH)
    if not named or not Path(str(named)).is_dir():
        return None
    place = Path(str(named))
    import psutil

    try:
        lineage = frozenset(one.pid for one in psutil.Process().parents())
        processes = _Processes(skip=lineage, reads=_GATE_READS)
        users = processes.using((place, Path(os.path.realpath(place))))
    except (psutil.Error, OSError) as exc:
        return ScratchUsers(place, unread=f"the processes could not be read: {exc}")
    return ScratchUsers(place, tuple(users))


def scratch_gate(state: states.State, ended: Iterable[builds.Build], *, again: str
                 ) -> tuple[Unmet | None, tuple[str, ...]]:
    """The scratch gate of `finish` and `drop` for the builds the verb ends (`ended`,
    of the round `state` gives): an `Unmet` naming each scratch folder a process uses
    and the first process that uses it, None when no process uses one; and a line for
    each folder whose processes could not be read, saying the gate was not run on it.
    `again` is the command that runs the verb again."""
    used, unread = [], []
    for build in ended:
        found = scratch_users(checking.commission_of(state, build))
        if found is None:
            continue
        if found.unread is not None:
            unread.append(f"the scratch gate was not run on {build.stage}'s scratch "
                          f"folder {found.place}: {found.unread}")
        elif found.users:
            more = (f", and {len(found.users) - 1} more" if len(found.users) > 1
                    else "")
            used.append(f"{build.stage}'s scratch folder {found.place} is in use by "
                        f"{found.users[0]}{more}")
    if not used:
        return None, tuple(unread)
    return (Unmet("; ".join(used), f"end that process or leave the folder, then: {again}"),
            tuple(unread))


class _Processes:
    """The user's processes and the paths each uses, read through psutil once, at the
    first look: every one but this process and those in `skip`, each read for the
    fields `reads` names (every field D reads when left out)."""

    def __init__(self, *, skip: frozenset[int] = frozenset(), reads=None):
        self._table: list[tuple[int, str, tuple[str, ...]]] | None = None
        self._skip = skip
        self._reads = reads

    def using(self, at: Iterable[Path]) -> list[str]:
        """Each process with a path at or under one of `at`, as `pid <n> (<name>):
        <path>`."""
        dirs_at = {_folded(str(one)).rstrip(os.sep) for one in at}
        found = []
        for pid, name, used in self._read():
            for one in used:
                if any(_under(one, at_dir) for at_dir in dirs_at):
                    found.append(f"pid {pid} ({name}): {one}")
                    break
        return found

    def _read(self) -> list[tuple[int, str, tuple[str, ...]]]:
        if self._table is None:
            self._table = _process_table(skip=self._skip, reads=self._reads)
        return self._table


def _folded(path: str) -> str:
    return os.path.normcase(path)


def _under(path: str, parent_dir: str) -> bool:
    path = _folded(path)
    return path == parent_dir or path.startswith(parent_dir + os.sep)


def _process_table(*, skip: frozenset[int] = frozenset(), reads=None
                   ) -> list[tuple[int, str, tuple[str, ...]]]:
    """Each process of the user's but this one and those in `skip`: its pid, its name,
    and the paths its working folder, open files, mapped files, argument words and
    environment values name, or those of the fields `reads` gives. A field that can't
    be read gives nothing."""
    import psutil

    me = os.getpid()
    reads = _READS if reads is None else reads
    try:
        user = psutil.Process(me).username()
    except (psutil.Error, OSError):
        user = None
    table = []
    for process in psutil.process_iter():
        if process.pid == me or process.pid in skip:
            continue
        try:
            if user is not None and process.username() != user:
                continue
            name = process.name()
        except (psutil.Error, OSError):
            continue
        used: list[str] = []
        for read in reads:
            try:
                used += read(process)
            except (psutil.Error, OSError, AttributeError, NotImplementedError,
                    ValueError):
                continue
        table.append((process.pid, name, tuple(one for one in used
                                                if one and os.path.isabs(one))))
    return table


def _cwd(process) -> list[str]:
    return [process.cwd()]


def _open_files(process) -> list[str]:
    return [one.path for one in process.open_files()]


def _mapped(process) -> list[str]:
    return [one.path for one in process.memory_maps()]


def _words(process) -> list[str]:
    return [part for word in process.cmdline() for part in _parts(word)]


def _env_parts(process) -> list[str]:
    return [part for value in process.environ().values() for part in _parts(value)]


#: The fields D reads of a process, and those the scratch gate of `finish` and `drop`
#: reads: all but the mapped files, whose read takes about a second.
_READS = (_cwd, _open_files, _mapped, _words, _env_parts)
_GATE_READS = (_cwd, _open_files, _words, _env_parts)


def _parts(word: str) -> list[str]:
    """A word, what follows its first `=`, and each part of a list of paths, so
    `--out=<path>` and a `PATH` naming a folder under the place both count."""
    found = [word]
    if "=" in word:
        found.append(word.partition("=")[2])
    for one in list(found):
        if os.pathsep in one:
            found += one.split(os.pathsep)
    return found


# -- the trash -----------------------------------------------------------------------------

def _trash(*, removing: bool, began: float) -> tuple[list[TrashEntry], bool]:
    """Each entry of the trash and what became of it: one the sweep's log names as
    renamed with no delete after is deleted by a removing run; anything else is left
    and reported."""
    trash_dir = removal.trash_dir()
    try:
        names = sorted(os.listdir(trash_dir))
    except (FileNotFoundError, NotADirectoryError):
        return [], False
    except OSError as exc:
        return [TrashEntry(trash_dir.name, trash_dir, LEFT, f"it can't be read: {exc}")], False
    lines = files.read_records(log_file())
    renamed = {line.get("entry"): line for line in lines if line.get("act") == RENAMED}
    deleted = {line.get("entry") for line in lines if line.get("act") == DELETED}
    found, timed_out = [], False
    for name in names:
        path = trash_dir / name
        line = renamed.get(name)
        if line is None:
            found.append(TrashEntry(name, path, LEFT, "no line of the sweep's log names "
                                    "it, so it is not the sweep's to delete; look at it "
                                    "and delete it by hand"))
        elif name in deleted:
            found.append(TrashEntry(name, path, LEFT, "the sweep deleted it once and this "
                                    "stayed: an entry on another device, or one it could "
                                    "not delete; look at it and delete it by hand"))
        elif not _real_dir(path):
            found.append(TrashEntry(name, path, LEFT, "it is not the folder the sweep "
                                    "moved there"))
        elif not removing:
            found.append(TrashEntry(name, path, WOULD_DELETE, "a sweep moved it there and "
                                    "stopped before its delete"))
        elif _monotonic() - began > TIME_LIMIT_S:
            timed_out = True
            found.append(TrashEntry(name, path, LEFT, f"the run passed {TIME_LIMIT_S:g} "
                                    "seconds, so the next run deletes it"))
        else:
            removal.delete(path)
            gone = not os.path.lexists(path)
            names_of = {key: line.get(key) for key in ("round", "stage", "repo", "branch",
                                                       "build", "kind")}
            _log(DELETED, path, names_of, entry=name, gone=gone)
            found.append(TrashEntry(name, path, DELETED) if gone else TrashEntry(
                name, path, LEFT, "parts of it could not be deleted"))
    return found, timed_out


def _real_dir(path: Path) -> bool:
    try:
        found = os.lstat(path)
    except OSError:
        return False
    return stat.S_ISDIR(found.st_mode) and not stat.S_ISLNK(found.st_mode)


__all__ = ["REMOVE", "DRY_RUN", "TIME_LIMIT_S", "LOCK_FILE", "STARTED_FILE", "LOG_FILE",
           "LOG_MAX_BYTES", "WOULD_GO", "REMOVED", "KEPT", "LEFT", "WOULD_DELETE",
           "DELETED", "RENAMED", "PROVEN", "FINISH_PROVEN", "Running", "Candidate",
           "Leftover", "TrashEntry", "Swept", "lock_file", "started_file", "log_file",
           "account", "run", "trash_log", "ScratchUsers", "scratch_users", "scratch_gate"]

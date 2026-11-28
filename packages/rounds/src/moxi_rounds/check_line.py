"""The line of runs that wait for the machine's check lock, and the record of who holds it.

`files.lock` alone lets whichever waiter tries first after a release win. So a run that
must wait takes a ticket in the line (`take`), a file `<n>.json` in
`STATE/rounds/locks/check.line/` (`line_dir`), `n` one above the highest there and
zero-padded to twelve digits, holding the waiting process's `pid` and `created` (its
start time, so a pid given to another process since never matches), its `round`,
`stage`, `step` and `repo`, and `since`, when it was taken. Every change to the line is
made under `check.line.lock` beside it, held only while files are listed, written or
removed.

Each poll (`LINE_POLL_S`) a waiter removes every dead ticket (no live process has its
`pid` and `created`, as `moxi.proc.alive` reads it on every system) and touches its own
(`remove_dead`). It is first when no ticket ahead of it is live and fresh: a ticket not
touched for `TICKET_STALE_S` belongs to a waiter that lives but does not run, and is
passed over, not removed, until it is touched again. Only the first tries the check
lock, which stays what keeps two runs apart and still dies with its process. A run
removes its own ticket under the line's lock too (`leave`), so no waiter reads it then.

A lander's ticket holds `first`: it is ahead of every ticket without it, and landers
among themselves keep the order they came in (`order`).

**The check lock is a counting lock** (`slot`): up to `check_lock.at_once` runs hold it
at once, each holding one of its places, a lock file of its own, `check.lock` for the
first and `check-<k>.lock` for the k-th (`slot_path`), each with its holder record beside
it, `check.holder.json` and `check-<k>.holder.json` (`holder_file`). The first live
ticket decides under `check.line.lock`, reading every place there is (`places`): 1 to
the higher of `at_once` and the highest `check-<k>.lock` in the folder. A place is held
when its lock can't be taken, or when its holder record names a run that still lives
though the rounds process that took it is gone (an orphan, `run_alive`). With no place
held the run takes the first at once and reads no memory. With some held, fewer than
`at_once`, it starts beside them only when it and every held place declare
`memory_mib` and the available memory, read then, covers the need (`admit`): the
`need_mib` given when above 0, else its own `memory_mib` plus theirs. Then it takes the
lowest free place from 1 to `at_once`. Every held place counts, whatever `at_once` says
now, so lowering it starts nothing beside a running check. A lock file is never removed.

The run that holds a place writes its holder record (`write_holder`): the ticket's
fields less `since`, `check_lock` (which place), its `memory_mib` when declared, the
run's `run_tag`, then `since`, the time it took the place; it adds `output` (the run's
output file, `update_holder`), and `run_pid` and `run_started` once its command has
started (`name_first`), and removes the record when it releases. A reader trusts it as the
holder's only while its `pid` and `created` name a live process (`holder`). The doctor's
check lock row reads all of this without taking any lock (`tickets`, `holder`,
`lock_pid`, `orphan`).

**Whether a run lives** once its rounds process is gone (`run_alive`) is read from its
holder record: its first process by `run_pid` and `run_started` (`moxi.proc.alive`),
not the run when its environment can be read and carries another `MOXI_RUN_TAG`; and,
on Linux and macOS, any process whose environment carries the record's `run_tag`. On
Windows the first process alone decides, since the run's job ended the rest with its
rounds process. A pid given to another process since never matches: it has another
start time, and no tag.

A removal never ends the run (`remove`). Where another process has the file open, as
the doctor may, Windows refuses to remove it for a moment: it is tried again for
`REMOVE_RETRY_S`, then left. A ticket left is removed by the next poll of any waiter
once its process has ended, and a holder record left is trusted only while its
process lives, and is replaced by the next run's.
"""

from __future__ import annotations

import calendar
import json
import os
import re
import secrets
import sys
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path

from moxi import files, proc

from . import log

_WINDOWS = sys.platform == "win32"

#: The line's folder, the lock its changes are made under, and the first place's holder
#: record, all beside the check lock in rounds' folder of lock files.
LINE_DIR = "check.line"
LINE_LOCK = "check.line.lock"
HOLDER_FILE = "check.holder.json"
#: The k-th place's lock file, for k from 2.
SLOT_FILE = "check-{k}.lock"
#: How often a waiter reads the line, in seconds.
LINE_POLL_S = 0.5
#: How long a ticket may go untouched before it is passed over, in seconds.
TICKET_STALE_S = 60.0
#: How long the holder's output may go unwritten before the doctor warns it may be hung,
#: in seconds.
QUIET_S = 1800.0
#: How long a removal another process's open handle refuses is tried again, in seconds.
REMOVE_RETRY_S = 1.0
#: How many digits a ticket's number is padded to.
_DIGITS = 12


def order(ticket: Ticket) -> tuple[int, int]:
    """Where `ticket` stands in the line: a lander's (`first`) ahead of every other, then
    by number."""
    return (0 if ticket.fields.get("first") else 1, ticket.n)


@dataclass(frozen=True)
class Ticket:
    """A ticket in the line: its number `n`, its file `path`, what it holds (`fields`)
    and when it was last touched (`touched_s`, seconds since the epoch)."""

    n: int
    path: Path
    fields: Mapping
    touched_s: float

    @property
    def dead(self) -> bool:
        """Whether no live process has the ticket's pid and start time."""
        return not _alive(self.fields)

    def stale(self, now_s: float) -> bool:
        """Whether the ticket has gone untouched for `TICKET_STALE_S` at `now_s`."""
        return now_s - self.touched_s >= TICKET_STALE_S


def line_dir(lock_path: Path) -> Path:
    """The line's folder beside the check lock `lock_path`."""
    return lock_path.with_name(LINE_DIR)


def holder_file(lock_path: Path) -> Path:
    """The holder record of the check lock's place whose lock file is `lock_path`:
    `check.holder.json` for `check.lock`, `check-<k>.holder.json` for `check-<k>.lock`."""
    return lock_path.with_name(f"{lock_path.stem}.holder.json")


def slot_path(lock_path: Path, k: int) -> Path:
    """The lock file of the check lock's `k`-th place, beside the first, `lock_path`."""
    return lock_path if k == 1 else lock_path.with_name(SLOT_FILE.format(k=k))


_SLOT_NAME = re.compile(r"check-([0-9]+)\.lock")


def places(lock_path: Path, at_once: int) -> list[int]:
    """Every place of the check lock to read: 1 to the higher of `at_once` and the
    highest `check-<k>.lock` beside `lock_path`."""
    try:
        names = [entry.name for entry in lock_path.parent.iterdir()]
    except FileNotFoundError:
        names = []
    found = [int(match.group(1)) for name in names if (match := _SLOT_NAME.fullmatch(name))]
    return list(range(1, max([at_once, *found]) + 1))


def _alive(fields: Mapping) -> bool:
    try:
        found = proc.ProcId(int(fields["pid"]), float(fields["created"]))
    except (KeyError, TypeError, ValueError):
        return False
    return proc.alive(found)


def me() -> dict:
    """This process's `pid` and `created`, as a ticket or the holder record holds them."""
    found = proc.identity(os.getpid())
    return {"pid": os.getpid(), "created": None if found is None else found.started}


@contextmanager
def changing(lock_path: Path) -> Iterator[None]:
    """Hold the line's own lock for a change to it."""
    line_dir(lock_path).mkdir(parents=True, exist_ok=True)
    with files.lock(lock_path.with_name(LINE_LOCK)):
        yield


def tickets(lock_path: Path) -> list[Ticket]:
    """Every ticket in the line, in order; one that can't be read holds no fields, so
    it reads dead."""
    found = []
    try:
        entries = list(line_dir(lock_path).iterdir())
    except FileNotFoundError:
        return []
    for path in entries:
        if path.suffix != ".json" or not path.stem.isdigit():
            continue
        try:
            fields = json.loads(path.read_text(encoding="utf-8"))
            touched_s = path.stat().st_mtime
        except FileNotFoundError:
            continue
        except (OSError, ValueError):
            fields, touched_s = {}, 0.0
        found.append(Ticket(int(path.stem), path, fields if isinstance(fields, dict)
                            else {}, touched_s))
    return sorted(found, key=lambda one: one.n)


def take(lock_path: Path, fields: Mapping) -> Path:
    """Take a ticket at the end of the line, holding `fields` and `since`; its file."""
    with changing(lock_path):
        n = max((one.n for one in tickets(lock_path)), default=0) + 1
        path = line_dir(lock_path) / f"{n:0{_DIGITS}d}.json"
        files.atomic_write(path, json.dumps({**fields, "since": log.stamp()}))
    return path


def remove(path: Path) -> bool:
    """Remove a ticket or the holder record; whether it is gone. One already gone is no
    matter. A removal refused (Windows refuses one while another process has the file
    open) is tried again for `REMOVE_RETRY_S`, then left where it is: it never raises."""
    deadline = time.monotonic() + REMOVE_RETRY_S
    while True:
        try:
            path.unlink()
            return True
        except FileNotFoundError:
            return True
        except PermissionError:
            if time.monotonic() >= deadline:
                return False
            time.sleep(files.POLL)
        except OSError:
            return False


def leave(lock_path: Path, own: Path) -> None:
    """Under the line's lock, remove this run's own ticket `own`."""
    with changing(lock_path):
        remove(own)


def remove_dead(lock_path: Path, *, own: Path | None = None) -> list[Ticket]:
    """Under the line's lock: remove each dead ticket, touch `own`, and give the live,
    fresh tickets ahead of `own` (every live, fresh one, with `own` None), in order."""
    with changing(lock_path):
        return _remove_dead(lock_path, own=own)


def _remove_dead(lock_path: Path, *, own: Path | None = None) -> list[Ticket]:
    """`remove_dead`, with the line's lock held by the caller."""
    if own is not None:
        try:
            os.utime(own)
        except FileNotFoundError:
            pass
    now_s = time.time()
    found = []
    every = tickets(lock_path)
    mine = next((order(one) for one in every if one.path == own), None)
    for one in every:
        if one.path == own:
            continue
        if one.dead:
            remove(one.path)
        elif (own is None or mine is None or order(one) < mine) and not one.stale(now_s):
            found.append(one)
    return sorted(found, key=order)


def write_holder(lock_path: Path, fields: Mapping) -> None:
    """Record that this process holds the check lock, with `fields` and `since`."""
    files.atomic_write(holder_file(lock_path),
                       json.dumps({**fields, "since": log.stamp()}))


def update_holder(lock_path: Path, **fields) -> None:
    """Add `fields` to this process's holder record."""
    found = holder(lock_path)
    if found is not None and found.get("pid") == os.getpid():
        files.atomic_write(holder_file(lock_path), json.dumps({**found, **fields}))


def name_first(lock_path: Path, first: proc.ProcId) -> None:
    """Name the run's first process, `first`, in this process's holder record of the
    place whose lock file is `lock_path` (`run_pid`, `run_started`): a run's `on_start`
    for `moxi.proc.run`."""
    update_holder(lock_path, run_pid=first.pid, run_started=first.started)


def remove_holder(lock_path: Path) -> None:
    """Remove the holder record, when it is this process's."""
    found = holder(lock_path)
    if found is not None and found.get("pid") == os.getpid():
        remove(holder_file(lock_path))


def holder(lock_path: Path) -> dict | None:
    """The holder record, while a live process has its pid and start time; else None."""
    try:
        found = json.loads(holder_file(lock_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return found if isinstance(found, dict) and _alive(found) else None


def lock_pid(lock_path: Path) -> int | None:
    """The pid the check lock's file holds, written by the process that holds it or
    last died holding it; None when it holds none."""
    try:
        with open(lock_path, "rb") as fh:
            first = fh.readline(32).strip()
    except OSError:
        return None
    return int(first) if first.isdigit() else None


def read_holder(lock_path: Path) -> dict | None:
    """The holder record of the place whose lock file is `lock_path` as the file holds
    it, whether or not its process lives; None when there is none or it can't be read."""
    try:
        found = json.loads(holder_file(lock_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return found if isinstance(found, dict) else None


def run_alive(record: Mapping) -> bool:
    """Whether the run a holder record names still runs, read from the record alone:
    its first process (`run_pid`, `run_started`) is alive, and is not shown carrying
    another `MOXI_RUN_TAG`; or, on Linux and macOS, a process whose environment carries
    the record's `run_tag` runs. A process whose environment can't be read carries no
    tag. A record that names no run reads as not alive."""
    tag = record.get("run_tag")
    tag = tag if isinstance(tag, str) and tag else None
    try:
        first = proc.ProcId(int(record["run_pid"]), float(record["run_started"]))
    except (KeyError, TypeError, ValueError):
        first = None
    if first is not None and proc.alive(first):
        if _WINDOWS:
            return True
        env = proc.process_env(first.pid)
        if env is None or env.get(proc.RUN_TAG_ENV) == tag:
            return True
    if _WINDOWS or tag is None:
        return False
    return _tagged(tag)


def _tagged(tag: str) -> bool:
    """Whether any live process's environment carries `tag` as its `MOXI_RUN_TAG`."""
    import psutil

    for process in psutil.process_iter():
        env = proc.process_env(process.pid)
        if env is None or env.get(proc.RUN_TAG_ENV) != tag:
            continue
        try:
            if process.status() != psutil.STATUS_ZOMBIE:
                return True
        except psutil.Error:
            continue
    return False


def orphan(lock_path: Path) -> dict | None:
    """The holder record of the place whose lock file is `lock_path` when the rounds
    process that wrote it is gone and its run still runs (`run_alive`); else None."""
    found = read_holder(lock_path)
    if found is None or _alive(found) or not run_alive(found):
        return None
    return found


def new_tag() -> str:
    """A run's tag: 32 random hex digits, made for that run alone."""
    return secrets.token_hex(16)


def available_mib() -> int:
    """The memory the system can give without swapping, in MiB: psutil's
    `virtual_memory().available`."""
    import psutil

    return int(psutil.virtual_memory().available // (1024 * 1024))


@dataclass(frozen=True)
class Held:
    """A held place of the check lock: its number `k`, its holder `record` (None when
    the lock is held with none, or by a process that is gone), and whether it is an
    `orphan`'s, a run whose rounds process is gone."""

    k: int
    record: Mapping | None
    orphan: bool = False

    @property
    def memory_mib(self) -> int | None:
        found = None if self.record is None else self.record.get("memory_mib")
        return found if isinstance(found, int) and not isinstance(found, bool) else None

    @property
    def name(self) -> str:
        """Who holds it, in words."""
        if self.record is None:
            return f"check lock {self.k}'s holder"
        return f"{self.record.get('round')} {self.record.get('stage')}"


@dataclass(frozen=True)
class Decision:
    """What the first in line does at one poll: take place `k`, or wait (`k` None), and
    why (`reason`: `alone`, `beside`, `held` when every place it may take is held,
    `memory`, `unknown` when a holder declares no memory_mib, `own` when the run declares
    none); the places `held`; and the memory reading and need, when read."""

    k: int | None
    reason: str
    held: tuple[Held, ...] = ()
    available_mib: int | None = None
    need_mib: int | None = None


def decide(own_mib: int | None, held: list[Held], free: list[int], *, at_once: int,
           need_mib: int, available: Callable[[], int]) -> Decision:
    """Whether the first in line takes a place now, from the places `held` and `free`
    (the free ones from 1 to `at_once`). Alone it takes the first, reading no memory.
    Beside others, fewer than `at_once` held, it takes the lowest free one when it and
    every held place declare memory_mib and `available()` is at least the need:
    `need_mib` when above 0, else its own memory_mib plus every held place's."""
    held_t = tuple(held)
    if not held:
        return Decision(1, "alone")
    if len(held) >= at_once or not free:
        return Decision(None, "held", held_t)
    if own_mib is None:
        return Decision(None, "own", held_t)
    if any(one.memory_mib is None for one in held):
        return Decision(None, "unknown", held_t)
    need = need_mib if need_mib > 0 else own_mib + sum(one.memory_mib for one in held)
    found = available()
    if found < need:
        return Decision(None, "memory", held_t, found, need)
    return Decision(min(free), "beside", held_t, found, need)


@dataclass(frozen=True)
class Taken:
    """A place a run holds: its number `k` of `at_once`, its lock file `path`, the run's
    `tag`, and how it was taken (`decision`)."""

    k: int
    at_once: int
    path: Path
    tag: str
    decision: Decision

    @property
    def beside(self) -> bool:
        """Whether the run started beside others."""
        return self.decision.reason == "beside"


class NotFree(Exception):
    """No place of the check lock can be taken now, and the run does not wait: `words`
    says who holds it, how many wait, and the memory reading when it is short."""

    def __init__(self, words: str):
        super().__init__(words)
        self.words = words


def _look(lock_path: Path, stack: ExitStack, *, at_once: int) -> tuple[list[Held],
                                                                         dict[int, ExitStack]]:
    """Read every place under the line's lock: the held ones, and the free ones from 1
    to `at_once`, each with its lock taken in a stack of its own. A free place's record
    whose run has ended is removed."""
    held: list[Held] = []
    free: dict[int, ExitStack] = {}
    for k in places(lock_path, at_once):
        path = slot_path(lock_path, k)
        one = ExitStack()
        try:
            one.enter_context(files.lock(path, timeout_s=0))
        except files.LockTimeout:
            held.append(Held(k, holder(path)))
            continue
        found = read_holder(path)
        if found is not None and run_alive(found):
            one.close()
            held.append(Held(k, found, orphan=True))
            continue
        if found is not None:
            remove(holder_file(path))
        if k <= at_once:
            free[k] = stack.enter_context(one)
        else:
            one.close()
    return held, free


def _try(lock_path: Path, stack: ExitStack, *, own_mib: int | None, at_once: int,
         need_mib: int, available: Callable[[], int]) -> Decision:
    """Under the line's lock: read the places and decide; the place decided on is left
    held in `stack`, every other free one let go."""
    with ExitStack() as tried:
        held, free = _look(lock_path, tried, at_once=at_once)
        decision = decide(own_mib, held, sorted(free), at_once=at_once, need_mib=need_mib,
                          available=available)
        if decision.k is not None:
            stack.enter_context(free.pop(decision.k).pop_all())
    return decision


def gib(mib: int | None) -> str:
    """A figure in MiB as GiB, one decimal: `7.9G`."""
    return "?" if mib is None else f"{mib / 1024:.1f}G"


def _names(held) -> str:
    return ", ".join(f"{one.name} for {age(one.record.get('since'))}"
                     if one.record is not None and one.record.get("since")
                     else one.name for one in held)


def _one_lock(lock_path: Path, at_once: int) -> bool:
    """Whether the lines read as a single lock's: `at_once` is 1 and no place above the
    first is held, whatever lock files are on disk."""
    return at_once == 1 and all(_free_now(slot_path(lock_path, k))
                                for k in places(lock_path, at_once)[1:])


def _held_words(lock_path: Path, at_once: int, ahead: int) -> str:
    """The line a waiter says while every place it may take is held, or a run ahead of
    it waits. With one place it reads as a single lock's."""
    if _one_lock(lock_path, at_once):
        who = held_by(holder(lock_path), lock_pid(lock_path))
        return f"waiting for the machine's check lock, held by {who}; {ahead} ahead of you"
    held = [Held(k, holder(slot_path(lock_path, k))) for k in places(lock_path, at_once)
            if not _free_now(slot_path(lock_path, k))]
    names = f" ({_names(held)})" if held else ""
    return (f"waiting for the check lock: {len(held)} of {at_once} held{names}; "
            f"{ahead} ahead of you")


def _free_now(path: Path) -> bool:
    """Whether a place reads free without its lock being taken: no live holder record,
    no orphan's run, and no live process under the pid its file holds (a holder killed
    outright leaves its pid there)."""
    pid = lock_pid(path)
    return (holder(path) is None and (pid is None or proc.identity(pid) is None)
            and orphan(path) is None)


def _why_words(decision: Decision, *, at_once: int, own_mib: int | None = None) -> str:
    """The line a first in line says for a wait other than a held lock; the memory line
    names the run's own figure (`own_mib`) first, then each holder's."""
    free_k = next((k for k in range(1, at_once + 1)
                   if k not in {one.k for one in decision.held}), at_once)
    place = f"check lock {free_k} of {at_once} is free"
    if decision.reason == "memory":
        figures = [] if own_mib is None else [f"yours {gib(own_mib)}"]
        figures += [f"{one.name}'s {gib(one.memory_mib)}" for one in decision.held]
        mine = ", ".join(figures)
        return (f"{place}; waiting for memory: {gib(decision.available_mib)} available, a "
                f"second check needs {gib(decision.need_mib)} ({mine}); read every "
                f"{LINE_POLL_S:g} s")
    if decision.reason == "unknown":
        who = next(one for one in decision.held if one.memory_mib is None)
        return (f"{place}, but {who.name} runs with no memory_mib, so nothing starts "
                "beside it")
    return f"{place}, but this check declares no memory_mib, so it runs only alone"


def _orphan_words(one: Held) -> str:
    record = one.record or {}
    return (f"check lock {one.k}'s holder (pid {record.get('pid')}) is gone but its run "
            f"(pid {record.get('run_pid')}) still runs, so it stays taken until that ends")


def _got_words(taken: Taken, waited_s: float) -> str:
    if taken.at_once == 1 and taken.k == 1:
        return f"got the check lock after {waited_s:.1f} s"
    where = f"got check lock {taken.k} of {taken.at_once} after {waited_s:.1f} s"
    decision = taken.decision
    if decision.reason != "beside":
        return f"{where}, alone"
    return (f"{where}, beside {', '.join(one.name for one in decision.held)} "
            f"({gib(decision.available_mib)} available, {gib(decision.need_mib)} needed)")


def _refusal(lock_path: Path, decision: Decision | None, waiting: int, at_once: int) -> str:
    """Why `--no-wait` takes no place: who holds the lock, how many wait, and the
    reading and the need when memory is short."""
    if _one_lock(lock_path, at_once):
        who = held_by(holder(lock_path), lock_pid(lock_path))
    else:
        held = decision.held if decision is not None else ()
        who = _names(held) or "no one"
    words = (f"another check holds or waits for the machine's check lock {lock_path}: "
             f"held by {who}; {waiting} waiting")
    if decision is not None and decision.reason == "memory":
        words += (f"; {gib(decision.available_mib)} available, "
                  f"{gib(decision.need_mib)} needed")
    elif decision is not None and decision.reason in ("own", "unknown"):
        words += "; " + _why_words(decision, at_once=at_once)
    return words


@contextmanager
def slot(lock_path: Path, fields: Mapping, *, memory_mib: int | None = None,
         at_once: int = 1, need_mib: int = 0, wait: bool = True, first: bool = False,
         say: Callable[[str], None] | None = None,
         available: Callable[[], int] = available_mib) -> Iterator[Taken]:
    """Hold a place of the check lock whose first place's lock file is `lock_path` for
    the block, taken in the line's order (`first` for a lander), by the rules above.

    `fields` are what the ticket and the holder record name (the process, round, stage,
    step and repo); `memory_mib` is what the run holds at most, None when it declares
    none. With `wait` false it takes a place only when no live ticket waits and one can
    be taken now, and raises `NotFree` otherwise. `say` gets the waiting lines, each once
    and again only when its reason changes. `available` reads the free memory, in MiB.
    The holder record is written before the block, with the run's tag, and removed after
    it, however it ends."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    say = say or (lambda _line: None)
    tag = new_tag()
    record = {**{key: value for key, value in fields.items() if key != "first"},
              **({} if memory_mib is None else {"memory_mib": memory_mib}), "run_tag": tag}
    options = dict(own_mib=memory_mib, at_once=at_once, need_mib=need_mib,
                   available=available)
    with ExitStack() as stack:
        if not wait:
            with changing(lock_path):
                ahead = _remove_dead(lock_path)
                decision = None if ahead else _try(lock_path, stack, **options)
                if decision is None or decision.k is None:
                    raise NotFree(_refusal(lock_path, decision, len(ahead), at_once))
                taken = _hold(lock_path, decision, record, at_once, tag)
        else:
            taken = _wait(lock_path, stack, record, first=first, say=say, tag=tag,
                          options=options)
        try:
            yield taken
        finally:
            remove_holder(taken.path)


def _hold(lock_path: Path, decision: Decision, record: Mapping, at_once: int,
          tag: str) -> Taken:
    """Write the holder record of the place decided on; under the line's lock."""
    path = slot_path(lock_path, decision.k)
    write_holder(path, {**record, "check_lock": decision.k})
    return Taken(decision.k, at_once, path, tag, decision)


def _wait(lock_path: Path, stack: ExitStack, record: Mapping, *, first: bool, say, tag: str,
          options: dict) -> Taken:
    """Take a ticket and wait until it is first and a place can be taken, then take it
    into `stack`. The ticket is removed however the wait ends; one a killed waiter
    leaves is removed by the next poll of another."""
    at_once = options["at_once"]
    ticket = take(lock_path, {**record, **({"first": True} if first else {})})
    started = time.monotonic()
    said: str | None = None
    orphans: set = set()
    taken = None
    try:
        while True:
            decision = None
            with changing(lock_path):
                ahead = _remove_dead(lock_path, own=ticket)
                if not ahead:
                    decision = _try(lock_path, stack, **options)
                    if decision.k is not None:
                        taken = _hold(lock_path, decision, record, at_once, tag)
                        remove(ticket)
                        break
            for one in () if decision is None else decision.held:
                if one.orphan and (one.k, (one.record or {}).get("run_pid")) not in orphans:
                    orphans.add((one.k, (one.record or {}).get("run_pid")))
                    say(_orphan_words(one))
            reason = "held" if decision is None else decision.reason
            if reason != said:
                say(_held_words(lock_path, at_once, len(ahead)) if reason == "held"
                    else _why_words(decision, at_once=at_once, own_mib=options["own_mib"]))
                said = reason
            time.sleep(LINE_POLL_S)
    finally:
        if taken is None:
            leave(lock_path, ticket)
    if said is not None:
        say(_got_words(taken, time.monotonic() - started))
    return taken


def seconds(since: object) -> int | None:
    """The stamp `since` as seconds since the epoch; None for one that doesn't read."""
    try:
        return calendar.timegm(time.strptime(str(since), "%Y-%m-%dT%H:%M:%SZ"))
    except ValueError:
        return None


def age(since: object, now_s: float | None = None) -> str:
    """How long before `now_s` (now, with none) the stamp `since` was, in whole minutes,
    as words."""
    then = seconds(since)
    if then is None:
        return "an unknown time"
    now_s = time.time() if now_s is None else now_s
    return f"{max(int((now_s - then) // 60), 0)} min"


def held_by(found: Mapping | None, pid: int | None) -> str:
    """Who holds the check lock, in words: from its holder record `found`, else the
    `pid` its file holds, else "no one"."""
    if found is not None:
        return (f"pid {found.get('pid')} ({found.get('round')} {found.get('stage')}, for "
                f"{age(found.get('since'))})")
    return f"pid {pid}" if pid else "no one"


__all__ = ["LINE_DIR", "LINE_LOCK", "HOLDER_FILE", "SLOT_FILE", "LINE_POLL_S",
           "TICKET_STALE_S", "QUIET_S", "REMOVE_RETRY_S", "Ticket", "line_dir",
           "holder_file", "slot_path", "places", "order", "me", "read_holder", "run_alive",
           "orphan", "new_tag", "available_mib", "Held", "Decision", "decide", "Taken",
           "NotFree", "slot", "gib",
           "changing", "tickets", "take", "remove", "leave", "remove_dead",
           "write_holder", "update_holder", "name_first", "remove_holder", "holder", "lock_pid",
           "seconds", "age", "held_by"]

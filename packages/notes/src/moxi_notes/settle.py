"""The settle steps: how a fired pass makes sure an ended session's edits are committed,
and their commit pushed, within a stated time, while the machine stays up.

The bound. When a session's `gone` event fires, every edit it made before the event that a
pass can commit is committed within `snapshot.interval` plus 6 minutes. The push of that
commit is attempted by the pass that made it, or, while the committer's backoff from an
earlier failed push holds it, by the first pass after that backoff if a pass comes; when
the committing pass's own attempt fails, a pass tries once more `push_backoff` later. Each
attempt these steps make starts within `push_backoff` plus 6 minutes of the commit. This
holds with `snapshot.auto` on, the timer off, and a pass's commit part ending within a
minute.

Left outside the bound, each on purpose:

1. a file a pass leaves out by design (a secret, a file over the size cap, a nested
   repository, a file someone else had staged): the pass's record names it;
2. edits whose covering pass ends `refused` or `error`: the committer's record says why,
   and the next pass of any kind tries again;
3. edits still waiting when a settler gives up after five `busy` or `index-busy` passes in
   a row, and a push retry that gives up after five `busy`: the notes doctor record
   `settle` says so;
4. the push of a commit made by a pass not started with `--settle` (the timer,
   `moxi notes snapshot`, `moxi repo commit`): the first pass after the backoff pushes it;
5. a settler or push retry killed before it finished (out of memory, a kill, a logoff
   that takes detached processes): its edits and pushes wait for the next event's pass,
   the timer or `moxi notes snapshot`, as they do when the machine shuts down.

The invariant the steps keep: whenever an ended session's request is open, one live
process holds the settle lock and reads the request counter before it exits. A request is
open until a pass covers it, and a pass covers it only when the pass started after the
request was made and ended other than `throttled`, `busy` or `index-busy`. No step
compares file times.

Requests. The runtime file `settle.json` holds a counter, `asked`, and a generation id
written once when the file is made. A process with a commit duty raises the counter by one
under the file's lock, so each request has its own number. The holder compares the id as
well as the count: a changed or missing id (the runtime folder was cleared, or the file
was unreadable) and a count below one it has read are open requests.

The steps are a generator over an `ops` object, so the same code runs for real
(`run`, with `Real` ops) and under a test scheduler that picks the order of every step.
It yields None at each point where another process may act, and a time (seconds, on the
`ops.now()` clock) to sleep until.

`settle(ops)` runs one pass, then:

- a pass that ended `throttled`, `busy` or `index-busy` saw edits it did not commit: a
  commit duty, handed to the one holder of the settle lock;
- a pass that committed and left its commit unpushed: a push duty, kept by this process;
- otherwise it ends.

The commit duty. Raise the counter, then try the settle lock once; if another process
holds it, end: that holder reads the counter after this raise. The holder keeps `covered`,
the newest request its own passes covered, and loops. It waits: after `busy` or
`index-busy`, until a minute after that pass started; otherwise until the author's last
snapshot commit is an interval old, or less when a push due time comes sooner. It reads
the counter, runs a pass, and when the pass could commit, covers what it read. While the
pass could not commit, or the counter moved past `covered`, it goes round again. Then it
releases the lock and reads the counter once more: a request that came in between makes
it take the lock again (once) and go round, unless another process took it first, which
then reads the counter after that request. Five `busy` or `index-busy` passes in a row,
the first pass included, end the duty with the `settle` record; then too it releases, reads
again, and starts a new streak when a request came after its last read.

The push duty. One due time, `push_backoff` after the pass ended, per commit this
process's passes left unpushed, a holder's included. A pass of this process that took the
committer's lock and ended at or after a due time clears it: that pass ran the push part.
A holder waits for due times inside its wait; after the commit duty the process sleeps to
each one alone, holding no lock. After a `busy` pass it runs again a minute after that
pass started, and five in a row give up with the `settle` record.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

#: Statuses of a pass that could not commit what it saw.
NOT_COVERING = ("throttled", "busy", "index-busy")
#: Statuses of a pass that found the lock or git's index held.
HELD = ("busy", "index-busy")
#: Seconds after a held pass started before the next try.
RETRY = 60.0
#: Held passes in a row before a duty gives up.
GIVE_UP = 5
#: The runtime files.
REQUESTS = "settle.json"
LOCK = "settle.lock"
#: The doctor record's key and text.
RECORD = "settle"
RECORD_TEXT = ("the notes repo's lock or index was held for 5 minutes; edits from ended "
               "sessions wait for the next pass")
PUSH_RECORD_TEXT = ("the notes repo's lock was held for 5 minutes; a snapshot's push "
                    "waits for the next pass")


@dataclass(frozen=True)
class Pass:
    """What one pass did, as the steps read it. `start` and `end` are `ops.now()` times;
    `held_lock` is True when the pass took the committer's lock (so it ran the push
    part); `unpushed` when it committed and the spec's push left the commit unpushed;
    `interval` and `backoff` are the spec's, in seconds."""

    status: str
    start: float
    end: float
    held_lock: bool
    unpushed: bool = False
    interval: float = 0.0
    backoff: float = 300.0
    line: str = ""


def is_open(read, covered) -> bool:
    """True when counter value `read`, `(id, count)`, holds a request `covered` doesn't."""
    if covered is None:
        return read[0] is not None or read[1] > 0
    if read[0] != covered[0]:
        return True
    return read[1] != covered[1]


def settle(ops):
    """The settle steps for one `--settle` process (a generator; see the module text)."""
    first = yield from ops.run_pass()
    dues: list[float] = []
    _discharge(dues, first)
    if first.status in NOT_COVERING:
        yield None
        raised = ops.raise_request()
        yield None
        if ops.try_lock():
            yield from _hold(ops, first, raised, dues)
    yield from _push(ops, dues)


def _discharge(dues: list[float], p: Pass) -> None:
    if p.held_lock:
        dues[:] = [d for d in dues if d > p.end]
    if p.unpushed:
        dues.append(p.end + p.backoff)
    dues.sort()


def _hold(ops, last: Pass, raised, dues: list[float]):
    covered = None
    read = raised
    streak = 1 if last.status in HELD else 0
    while True:
        if last.status not in NOT_COVERING:
            yield None
            if not is_open(ops.read(), covered):
                yield None
                ops.release()
                yield None
                if not is_open(ops.read(), covered):
                    ops.clear_record()
                    return
                yield None
                if not ops.try_lock():
                    return
        if last.status in HELD:
            if streak >= GIVE_UP:
                ops.give_up(RECORD_TEXT)
                yield None
                ops.release()
                yield None
                if not is_open(ops.read(), read):
                    return
                yield None
                if not ops.try_lock():
                    return
                streak = 0
            wake = last.start + RETRY
        else:
            wake = _commit_wake(ops, last)
            if dues:
                wake = min(wake, dues[0])
        yield max(ops.now(), wake)
        yield None
        read = ops.read()
        p = yield from ops.run_pass()
        streak = streak + 1 if p.status in HELD else 0
        _discharge(dues, p)
        if p.status not in NOT_COVERING:
            covered = read
        last = p


def _commit_wake(ops, last: Pass) -> float:
    at = ops.last_commit()
    if at is None:
        return last.start + RETRY if last.status == "throttled" else ops.now()
    return at + last.interval


def _push(ops, dues: list[float]):
    held = 0
    retry = None
    while dues:
        yield max(ops.now(), dues[0], retry or 0.0)
        p = yield from ops.run_pass()
        if not p.held_lock:
            held += 1
            if held >= GIVE_UP:
                ops.give_up(PUSH_RECORD_TEXT)
                return
            retry = p.start + RETRY
            continue
        held = 0
        retry = None
        _discharge(dues, p)


def one(ops):
    """One pass and no duty: the timer and `moxi notes snapshot`."""
    return (yield from ops.run_pass())


def run(steps, *, now=time.time, sleep=time.sleep, slice_s: float = 30.0):
    """Drive a step generator for real: sleep to each time it yields, on the wall clock,
    in slices of at most `slice_s` seconds so a clock that jumps is read again. Returns
    the generator's value."""
    try:
        step = next(steps)
        while True:
            if step is not None:
                while True:
                    left = step - now()
                    if left <= 0:
                        break
                    sleep(min(left, slice_s))
            step = steps.send(None)
    except StopIteration as stop:
        return stop.value


__all__ = ["NOT_COVERING", "HELD", "RETRY", "GIVE_UP", "REQUESTS", "LOCK", "RECORD",
           "RECORD_TEXT", "Pass", "is_open", "settle", "one", "run"]

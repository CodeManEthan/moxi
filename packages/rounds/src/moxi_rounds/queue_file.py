"""A repo's merge queue: the file of its events, and the queue as read from them.

A queue project (`land = "queue"`) keeps one file per repo, `queue.jsonl` in the folder
`queue/<repo>` of rounds' state directory (`queue_dir`). Each line is one event, a JSON
object with `event` (its kind, one of `KINDS`) and `at`; every write appends under the
file's lock (`append`, `appending`), and the file never rolls, since the queue is read
from the whole of it. Nothing else holds the queue's state: `read` folds the file into a
`Queue` each time.

The kinds:

- `join`: a build joined, at its tip `tip` (`T`) with the target's head `main` (`J`),
  `urgent` or not, by `by`.
- `left`: a member left (`reason`: "by hand", "moved", or the gate that failed), by
  `by`.
- `batch`: a candidate was built (`state` "built", with its `id`, `n`, `main` (`M`),
  `branch` and `members`, and `source`, `[batch id, index]`, when it is one of the
  groups an ended batch named) or ended (`state` "ended", with why, and `regroups`, the
  groups of its members, each a list of `[round, stage, tip]`, to be built as batches of
  their own: the halves of a split, or a half rebuilt without a member or on a moved
  target).
- `run`: what the lander read of a batch's CI run or did to it, or, with `head` and no
  batch, of the target's full run at `head`.
- `landed`, `ejected`: a member's end in a batch, with its `landing` or `reason`.
- `waiting`: the lander waits on something outside the queue (`reason`).
- `paused`, `resumed`: the queue stopped taking members that are not urgent, with its
  `reason`, and started again.
- `red`, `green`: a full run on the target as the lander read it.
- `told`: a message the lander sent or could not send, with what comms reported.

Several batches run at once only while a red batch is split (`running_batches`); a
group an ended batch named and no batch has been built from yet is `regrouped`.

**A member** is a build's newest `join` and what came after it. It is waiting until it
leaves, lands or is ejected; while a batch that names it has been built and not ended,
it is in that batch, and a batch that ends without landing or ejecting it puts it back
to waiting in its place. Waiting members are taken urgent first, each group in join
order (`waiting`).
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from moxi import files

from . import log, paths

#: The folder, in rounds' state directory, of each repo's queue folder.
QUEUE_DIR = "queue"
#: The queue file's name in a repo's queue folder.
QUEUE_FILE = "queue.jsonl"

JOIN = "join"
LEFT = "left"
BATCH = "batch"
RUN = "run"
LANDED = "landed"
EJECTED = "ejected"
WAITING = "waiting"
PAUSED = "paused"
RESUMED = "resumed"
RED = "red"
GREEN = "green"
TOLD = "told"
#: Every kind of event the file holds.
KINDS = (JOIN, LEFT, BATCH, RUN, LANDED, EJECTED, WAITING, PAUSED, RESUMED, RED, GREEN,
         TOLD)

#: A batch's states: built and not yet ended, or ended.
BUILT = "built"
ENDED = "ended"

#: A member's states.
QUEUED = "queued"
IN_BATCH = "in batch"
#: The reasons a member leaves that are not a gate: `finish --leave`, and a branch that
#: moved after the member joined.
BY_HAND = "by hand"
MOVED = "moved"


def queue_dir(repo: str, env=None) -> Path:
    """The repo's queue folder: its queue file, the lander's lock and the lander's log."""
    return paths.state_dir(env) / QUEUE_DIR / repo


def queue_file(repo: str, env=None) -> Path:
    return queue_dir(repo, env) / QUEUE_FILE


@dataclass
class Member:
    """One build in the queue, from its newest `join`: the round `name`, the `stage`,
    the tip `tip` it joined at, the target's head `main` then, whether it is `urgent`,
    who joined it (`by`), when (`joined_at`) and its join's line (`line`, from 1).
    `state` is `QUEUED`, `IN_BATCH`, or the kind of its end (`LANDED`, `EJECTED`,
    `LEFT`); `batch` the batch it is in or ended in; `reason` why it left or was
    ejected; `landing` where it landed."""

    name: str
    stage: str
    tip: str
    main: str | None
    urgent: bool
    by: str | None
    joined_at: str | None
    line: int
    state: str = QUEUED
    batch: str | None = None
    reason: str | None = None
    landing: str | None = None
    ended_at: str | None = None

    @property
    def key(self) -> tuple[str, str]:
        return (self.name, self.stage)

    @property
    def waiting(self) -> bool:
        return self.state in (QUEUED, IN_BATCH)


@dataclass
class Batch:
    """One candidate: its `id` (`<M12>-<n>`), number `n`, the target's head `main` it was
    built on, its local `branch`, its `members` as `(name, stage, tip)` in merge order,
    the `candidate` commit, whether it has `ended` and why (`why`), the newest `run`
    event about it and every one (`runs`), the group of an ended batch it was built
    from (`source`), and the groups its end named (`regroups`)."""

    id: str
    n: int
    main: str
    branch: str
    members: tuple[tuple[str, str, str], ...]
    candidate: str | None = None
    ended: bool = False
    why: str | None = None
    run: dict | None = None
    runs: list[dict] = field(default_factory=list)
    source: tuple[str, int] | None = None
    regroups: list[tuple[tuple[str, str, str], ...]] = field(default_factory=list)


@dataclass
class Queue:
    """A repo's queue as its file reads now: every `member` by `(name, stage)` (the newest
    join of each), the `batches` in order, the pause's reason (`paused`, None when not
    paused), the target's full runs read (`mains`, each a `red` or `green` event), what
    the lander did to them (`head_runs`, the `run` events with a `head`), the `told`
    events, the newest `waiting` event (None once a batch moved past it), and every
    event in order (`events`)."""

    repo: str
    members: dict[tuple[str, str], Member] = field(default_factory=dict)
    batches: list[Batch] = field(default_factory=list)
    paused: str | None = None
    mains: list[dict] = field(default_factory=list)
    head_runs: list[dict] = field(default_factory=list)
    told: list[dict] = field(default_factory=list)
    waiting_on: dict | None = None
    events: list[dict] = field(default_factory=list)

    def member(self, name: str, stage: str) -> Member | None:
        return self.members.get((name, stage))

    def waiting(self) -> list[Member]:
        """The members that wait to be taken into a batch: urgent first, each group in
        join order."""
        found = [one for one in self.members.values() if one.state == QUEUED]
        return sorted(found, key=lambda one: (not one.urgent, one.line))

    def queued(self) -> list[Member]:
        """Every member still in the queue, in a batch or waiting: the running batches'
        members first, in their order, then the waiting ones in the order they are
        taken."""
        first = [
            self.members[(name, stage)] for running in self.running_batches()
            for name, stage, _tip in running.members
            if (name, stage) in self.members
            and self.members[(name, stage)].state == IN_BATCH
            and self.members[(name, stage)].batch == running.id]
        return first + self.waiting()

    def position(self, name: str, stage: str) -> int | None:
        """The member's place among those still in the queue, from 1; None when it is
        not in it."""
        for index, one in enumerate(self.queued(), start=1):
            if one.key == (name, stage):
                return index
        return None

    def running(self) -> Batch | None:
        """The newest batch built and not ended, None when none is."""
        return next((one for one in reversed(self.batches) if not one.ended), None)

    def running_batches(self) -> list[Batch]:
        """Every batch built and not ended, oldest first."""
        return [one for one in self.batches if not one.ended]

    def regrouped(self) -> list[tuple[tuple[str, int], list[Member]]]:
        """Each group an ended batch named that no batch has been built from, as its
        source and its members that still wait at the tip the group names; a group none
        of whose members waits is left out."""
        built = {one.source for one in self.batches if one.source is not None}
        found = []
        for batch in self.batches:
            for index, group in enumerate(batch.regroups):
                if (batch.id, index) in built:
                    continue
                members = [self.members[(name, stage)] for name, stage, tip in group
                           if (name, stage) in self.members
                           and self.members[(name, stage)].state == QUEUED
                           and self.members[(name, stage)].tip == tip]
                if members:
                    found.append(((batch.id, index), members))
        return found

    def main_at(self, head: str) -> dict | None:
        """The newest `red` or `green` event of the target's full run at `head`."""
        return next((one for one in reversed(self.mains) if one.get("head") == head), None)

    def batch(self, batch_id: str) -> Batch | None:
        return next((one for one in self.batches if one.id == batch_id), None)


def read(repo: str, env=None) -> Queue:
    """The repo's queue as its file reads now; empty when there is no file."""
    path = queue_file(repo, env)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        text = ""
    return fold(repo, [json.loads(line) for line in text.splitlines() if line.strip()])


def fold(repo: str, events: list[dict]) -> Queue:
    """The queue that `events`, oldest first, leave."""
    queue = Queue(repo=repo, events=list(events))
    for line, event in enumerate(events, start=1):
        _apply(queue, event, line)
    return queue


def _apply(queue: Queue, event: dict, line: int) -> None:
    kind = event.get("event")
    key = (event.get("round"), event.get("stage"))
    member = queue.members.get(key)
    if kind == JOIN:
        queue.members[key] = Member(
            name=str(event.get("round")), stage=str(event.get("stage")),
            tip=str(event.get("tip")), main=event.get("main"),
            urgent=bool(event.get("urgent")), by=event.get("by"),
            joined_at=event.get("at"), line=line)
    elif kind in (LEFT, LANDED, EJECTED) and member is not None and member.waiting:
        member.state = kind
        member.reason = event.get("reason")
        member.landing = event.get("landing")
        member.batch = event.get("batch") or member.batch
        member.ended_at = event.get("at")
    elif kind == BATCH and event.get("state") == BUILT:
        batch = Batch(id=str(event.get("id")), n=int(event.get("n") or 0),
                      main=str(event.get("main")), branch=str(event.get("branch")),
                      members=tuple((str(one["round"]), str(one["stage"]), str(one["tip"]))
                                    for one in event.get("members") or ()),
                      candidate=event.get("candidate"),
                      source=(tuple(event["source"]) if event.get("source") else None))
        queue.batches.append(batch)
        queue.waiting_on = None
        for name, stage, tip in batch.members:
            one = queue.members.get((name, stage))
            if one is not None and one.state == QUEUED and one.tip == tip:
                one.state, one.batch = IN_BATCH, batch.id
    elif kind == BATCH and event.get("state") == ENDED:
        batch = queue.batch(str(event.get("id")))
        if batch is not None:
            batch.ended, batch.why = True, event.get("why")
            batch.regroups = [tuple((str(name), str(stage), str(tip))
                                    for name, stage, tip in group)
                              for group in event.get("regroups") or ()]
            for one in queue.members.values():
                if one.state == IN_BATCH and one.batch == batch.id:
                    one.state = QUEUED
    elif kind == RUN and event.get("head") and not event.get("batch"):
        queue.head_runs.append(event)
    elif kind == RUN:
        batch = queue.batch(str(event.get("batch")))
        if batch is not None:
            batch.run = event
            batch.runs.append(event)
    elif kind == PAUSED:
        queue.paused = str(event.get("reason") or "paused")
    elif kind == RESUMED:
        queue.paused = None
    elif kind == WAITING:
        queue.waiting_on = event
    elif kind in (RED, GREEN):
        queue.mains.append(event)
    elif kind == TOLD:
        queue.told.append(event)


@contextmanager
def appending(repo: str, env=None) -> Iterator[tuple[Queue, Callable[[dict], dict]]]:
    """Hold the queue file's lock for the block and give the queue as it reads under it,
    with an `add(event)` that appends one event (its `at` filled in) and folds it in, so
    a read and the writes that follow from it are one step no other writer splits."""
    path = queue_file(repo, env)
    path.parent.mkdir(parents=True, exist_ok=True)
    with files.lock(path.with_name(path.name + ".lock"), timeout_s=60):
        queue = read(repo, env)

        def add(event: dict) -> dict:
            if event.get("event") not in KINDS:
                raise ValueError(f"{event.get('event')!r} is not a queue event")
            written = {"event": event["event"], "at": event.get("at") or log.stamp(),
                       **{key: value for key, value in event.items()
                          if key not in ("event", "at")}}
            files.append_record(path, written, max_bytes=None, durable=True)
            queue.events.append(written)
            _apply(queue, written, len(queue.events))
            return written

        yield queue, add


def append(repo: str, event: dict, env=None) -> dict:
    """Append one event to the repo's queue file under its lock; the event as written."""
    with appending(repo, env) as (_queue, add):
        return add(event)


__all__ = ["QUEUE_DIR", "QUEUE_FILE", "KINDS", "JOIN", "LEFT", "BATCH", "RUN", "LANDED",
           "EJECTED", "WAITING", "PAUSED", "RESUMED", "RED", "GREEN", "TOLD", "BUILT",
           "ENDED", "QUEUED", "IN_BATCH", "BY_HAND", "MOVED", "queue_dir", "queue_file",
           "Member", "Batch", "Queue", "read", "fold", "appending", "append"]

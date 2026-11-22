"""`finish` in a queue project: a build joins its repo's queue, or leaves it.

In a project whose `land` is `"queue"`, `moxi rounds finish ROUND B<n>` does not merge.
`join` reads the finish gates at the branch's tip `T` as a finish does, with two
changes: gate 6 (the target) is off, since the lander's candidate holds the target by
construction, and gate 3 (the check) is met by the scoped local check at `T`
(`checking`). Gate 5 (drift) is read against the target's head now, `J`, and not again
for anything that lands after (the lander's member finish reads it against `J` too).
A build whose finish actions are anything but one merge is refused, since the lander
only merges. Then, under the round's lock and the queue file's, it appends `join` to
the queue file and `queue.join {stage, tip: T, main: J, urgent}` to the round's log.

A build already waiting at `T` joins nothing new: its place is given. One waiting at an
older tip (its branch moved) is recorded `left` as moved, then joins at `T`, at the
back.

`urgent` joins it as an urgent member, for a fix to a red target: it goes ahead of every
member that is not urgent and is batched only with other urgent members, even while the
queue is paused. Who passed it is recorded (`by`).

`leave` takes a member out at once: `left {reason: "by hand", by}` in the queue file and
`queue.left` in the round's log. A member inside a running batch leaves too; the lander's
landing re-read finds it gone and builds the rest again without it, with a new CI run,
which `Left.rebuilds` names.

Neither writes anything to git; the lander makes every merge.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import checking, finishing, folders, queue_file, queue_log, write
from . import settings as own
from .errors import BadArgument, Refused, Unmet
from .gitreads import Git

#: The round-log events that mirror the queue file's events about a member.
JOINED, LEFT, BATCHED = queue_log.JOINED, queue_log.LEFT, queue_log.BATCHED
LANDED, EJECTED = queue_log.LANDED, queue_log.EJECTED


@dataclass(frozen=True)
class Joined:
    """What `join` did: the round `name`, the `stage`, the tip `tip` it joined at, the
    target's head `main` then, its `position` in the queue (from 1), whether it is
    `urgent`, whether it was `already` waiting at that tip (nothing written), and the
    older tip it `moved` from, when it was waiting at one."""

    name: str
    stage: str
    repo: str
    tip: str
    main: str
    position: int
    urgent: bool
    already: bool = False
    moved: str | None = None


@dataclass(frozen=True)
class Left:
    """What `leave` did: the member that left, its tip, and the running batch it was in
    (`rebuilds`), which the lander builds again without it, None when it only waited."""

    name: str
    stage: str
    repo: str
    tip: str
    rebuilds: str | None = None


def queue_repo(name: str, stage: str, *, settings=None, wait_s: float = write.WAIT_S,
               env=None) -> str | None:
    """The repo of the stage's open build when that repo lands through its queue, else
    None (no such build, or it lands by finish)."""
    from . import builds

    settings = own.load() if settings is None else settings
    where = _where(name, settings)
    build = builds.current(write.look(where, wait_s=wait_s, env=env).state, stage)
    if build is None or not checking.queued(settings, build.repo):
        return None
    return build.repo


def join(name: str, stage: str, *, by: str, urgent: bool = False, settings=None,
         wait_s: float = write.WAIT_S, at: str | None = None, env=None) -> Joined:
    """Join the open build of `stage` in the round `name` to its repo's queue at its
    branch's tip. Raises `Refused` with every gate that does not hold, a repo that does
    not land through its queue, or finish actions other than one merge."""
    settings = own.load() if settings is None else settings
    name = _full(name, settings)
    where = _where(name, settings)
    read: dict = {}

    def gate(view):
        build = finishing.open_build(view.state, name, stage)
        if not checking.queued(settings, build.repo):
            return Unmet(f"{build.repo} lands each build by its own finish and has no "
                         "queue", f"moxi rounds finish {name} {stage}")
        actions = own.build_setting(settings, build.repo, "finish")
        if [one["do"] for one in actions] != [own.MERGE]:
            kinds = ", ".join(one["do"] for one in actions) or "none"
            return Unmet(f"{build.repo}'s finish actions are {kinds}, and the queue's "
                         "lander only merges",
                         f"set finish = [ {{ do = \"merge\" }} ] in [projects.{build.repo}],"
                         " or land = \"finish\"")
        seen = finishing.seen_now(view, name, stage, settings)
        if seen.tip is None:
            return Unmet(f"the branch {build.branch} of {stage} is gone, and a member "
                         "joins at its branch's tip", f"moxi rounds show {name}")
        found = [one.unmet for one in finishing.gates_now(view, name, stage, seen,
                                                           seen.tip, settings)]
        if found:
            return found
        _key, root = folders.root(build.repo)
        main = Git(root).branch(build.target)
        if main is None:
            return Unmet(f"the target {build.target} is not a branch of {build.repo}",
                         f"make {build.target} again, then moxi rounds finish {name} "
                         f"{stage}")
        read.update(build=build, tip=seen.tip, main=main)
        return None

    def act(_view):
        build, tip = read["build"], read["tip"]
        with queue_file.appending(build.repo, env) as (queue, add):
            member = queue.member(name, stage)
            if member is not None and member.waiting and member.tip == tip:
                return (queue.position(name, stage), True, None, member.urgent)
            moved = None
            if member is not None and member.waiting:
                moved = member.tip
                add({"event": queue_file.LEFT, "round": name, "stage": stage,
                     "tip": member.tip, "reason": queue_file.MOVED, "by": by})
            add({"event": queue_file.JOIN, "round": name, "stage": stage, "tip": tip,
                 "main": read["main"], "urgent": urgent, "by": by})
            return (queue.position(name, stage), False, moved, urgent)

    def event(_view, result):
        _position, already, moved, _urgent = result
        if already:
            return None
        entries = []
        if moved is not None:
            entries.append(write.Entry(LEFT, {"stage": stage, "tip": moved,
                                              "reason": queue_file.MOVED, "by": by}))
        entries.append(write.Entry(JOINED, {"stage": stage, "tip": read["tip"],
                                            "main": read["main"], "urgent": urgent,
                                            "by": by}))
        return entries

    written = write.record(where, by=by, gates=[gate], act=act, event=event,
                           wait_s=wait_s, at=at, env=env)
    position, already, moved, was_urgent = written.result
    return Joined(name=name, stage=stage, repo=read["build"].repo, tip=read["tip"],
                  main=read["main"], position=position, urgent=was_urgent,
                  already=already, moved=moved)


def leave(name: str, stage: str, *, by: str, settings=None,
          wait_s: float = write.WAIT_S, at: str | None = None, env=None) -> Left:
    """Take the stage's build out of its repo's queue at once. Raises `Refused` when it
    is not in the queue."""
    from . import builds

    settings = own.load() if settings is None else settings
    name = _full(name, settings)
    where = _where(name, settings)
    read: dict = {}

    def gate(view):
        build = builds.current(view.state, stage)
        if build is None or not checking.queued(settings, build.repo):
            return Unmet(f"{stage} of {name} is in no queue: it has no build in a repo "
                         "that lands through its queue", f"moxi rounds show {name}")
        member = queue_file.read(build.repo, env).member(name, stage)
        if member is None or not member.waiting:
            return Unmet(f"{stage} of {name} is not in {build.repo}'s queue",
                         f"moxi rounds queue --repo {build.repo}")
        read["build"] = build
        return None

    def act(_view):
        repo = read["build"].repo
        with queue_file.appending(repo, env) as (queue, add):
            member = queue.member(name, stage)
            if member is None or not member.waiting:
                raise Refused([Unmet(f"{stage} of {name} left {repo}'s queue meanwhile",
                                     f"moxi rounds queue --repo {repo}")])
            running = queue.running()
            rebuilds = (running.id if running is not None
                        and member.state == queue_file.IN_BATCH
                        and member.batch == running.id else None)
            add({"event": queue_file.LEFT, "round": name, "stage": stage,
                 "tip": member.tip, "reason": queue_file.BY_HAND, "by": by,
                 "batch": rebuilds})
            return member.tip, rebuilds

    def event(_view, result):
        tip, rebuilds = result
        return write.Entry(LEFT, {"stage": stage, "tip": tip,
                                  "reason": queue_file.BY_HAND, "by": by,
                                  "batch": rebuilds})

    written = write.record(where, by=by, gates=[gate], act=act, event=event,
                           wait_s=wait_s, at=at, env=env)
    tip, rebuilds = written.result
    return Left(name=name, stage=stage, repo=read["build"].repo, tip=tip,
                rebuilds=rebuilds)


def _full(name: str, settings) -> str:
    project, slug = folders.split(name)
    return f"{folders.home(project, settings).project}/{slug}"


def _where(name: str, settings) -> write.Where:
    project, slug = folders.split(name)
    home = folders.home(project, settings)
    return write.Where(home.project, home.rounds_dir, slug)


def refuse_flags(*, urgent: bool, leave_: bool, repo: str | None, name: str,
                 stage: str) -> None:
    """Refuse `--urgent` or `--leave` for a build whose repo has no queue (`repo` None)."""
    if repo is None and (urgent or leave_):
        flag = "--urgent" if urgent else "--leave"
        raise BadArgument(f"{flag} is for a build in a repo that lands through its "
                          f"queue, and {stage} of {name} lands by its own finish",
                          f"moxi rounds finish {name} {stage}")


__all__ = ["JOINED", "LEFT", "BATCHED", "LANDED", "EJECTED", "Joined", "Left",
           "queue_repo", "join", "leave", "refuse_flags"]

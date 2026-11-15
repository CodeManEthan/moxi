"""The finish lock: at most one finish runs at a time per repo and target.

The lock is a git ref in the build's repo, `refs/rounds/finishing/<into>`, the target's
name percent-encoded as in a path (`places.encode`), or the repo's `lock_ref` setting in
its place, so that two tools finishing into one repo can share one lock. Refs are the
repo's own, shared by every worktree of it, so whichever checkout a finish runs from,
it meets the same lock.

**The holder's line.** The ref points at a blob written with `git hash-object -w` that
holds one line: `<actor> pid=<pid> host=<host> <UTC time> <round> <stage>`. The time and
the pid make each holder's blob its own, so every git call that names a blob names the
one lock it means.

- `take` creates the ref with git's check that it does not exist yet, so of two takers
  git lets one in. When the ref is there and holds a line in this format whose host is
  this machine and whose pid is no live process, the holder died: the ref is swapped
  from the dead holder's blob to the new one in one call, which git refuses when the
  ref moved meanwhile, so of two finishes that find one dead holder, one clears it.
  `Taken.cleared` is then the dead holder, for the verb to record as `lock.cleared`
  (`cleared_entry`). Anything else at the ref (a live holder, another machine's, a
  commit or a blob in no format this reads) is never cleared here: `take` refuses,
  printing what the ref holds and the one git command that clears it, for the lead to
  run after asking the holder.
- `release` deletes the ref only while it still points at the taker's own blob.
- `found` reads what holds the ref now, and `stale` each lock in this format older than
  `STALE_S`, for the doctor.

A pid read from a line is checked only on this machine, and only on POSIX: elsewhere
every holder counts as live, so a lock is never cleared on a guess.
"""

from __future__ import annotations

import calendar
import os
import re
import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from . import places, write
from . import settings as own
from .errors import Refused, Unmet
from .gitreads import Git, GitError
from .log import stamp

#: The folder of refs the lock of each target is in, when the repo sets no `lock_ref`.
REF_PREFIX = "refs/rounds/finishing/"
#: How old a lock is before the doctor names it, in seconds.
STALE_S = 3600.0
#: The type of the event that records a dead holder's lock cleared.
CLEARED = "lock.cleared"

#: The largest blob read as a holder's line; a larger one is in no format this reads.
_LINE_MAX_BYTES = 4096
#: How many times `take` tries again when the ref changed between two of its git calls.
_TRIES = 5
_TIME = r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ"
_LINE = re.compile(rf"(?P<actor>\S(?:.*\S)?) pid=(?P<pid>[1-9]\d*) host=(?P<host>\S+) "
                   rf"(?P<at>{_TIME}) (?P<round>\S+) (?P<stage>\S+)")


def ref_of(settings, repo: str, into: str) -> str:
    """The lock's ref for builds of the project `repo` that land on `into`: the repo's
    `lock_ref`, else `refs/rounds/finishing/<into>` with `into` encoded."""
    chosen = own.build_setting(settings, repo, "lock_ref")
    return chosen if chosen else REF_PREFIX + places.encode(into)


@dataclass(frozen=True)
class Holder:
    """Who holds a finish lock: as the holder's line says it."""

    actor: str
    pid: int
    host: str
    at: str
    round: str
    stage: str

    @property
    def line(self) -> str:
        """The holder's line, as its blob holds it (less the newline)."""
        return (f"{self.actor} pid={self.pid} host={self.host} {self.at} {self.round} "
                f"{self.stage}")

    def age_s(self, now_s: float | None = None) -> float:
        """How long ago the lock was taken, in seconds."""
        taken = calendar.timegm(time.strptime(self.at, "%Y-%m-%dT%H:%M:%SZ"))
        return (time.time() if now_s is None else now_s) - taken


def holder(by: str, name: str, stage: str, *, at: str | None = None,
           pid: int | None = None, host: str | None = None) -> Holder:
    """The holder's line of a finish of the round `name`'s `stage` run by `by` in
    this process, now (or `at`). Each run of whitespace in the actor's name is one space,
    so the line stays one line."""
    return Holder(" ".join(by.split()) or "-", os.getpid() if pid is None else pid,
                  places.host() if host is None else host, stamp() if at is None else at,
                  name, stage)


def parse(text: str) -> Holder | None:
    """The holder that `text`, a lock blob's content, names; None when it is not one
    line in the holder's format."""
    line = text[:-1] if text.endswith("\n") else text
    found = _LINE.fullmatch(line) if "\n" not in line else None
    if found is None:
        return None
    try:
        time.strptime(found["at"], "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return None
    return Holder(found["actor"], int(found["pid"]), found["host"], found["at"],
                  found["round"], found["stage"])


@dataclass(frozen=True)
class Held:
    """What holds a finish lock now: the ref, the object it points at and that object's
    type, and the holder its blob names (None when it names none in the format)."""

    root: Path
    ref: str
    oid: str
    kind: str
    holder: Holder | None

    @property
    def said(self) -> str:
        """What the lock holds, in words: the holder's line, or what the ref points at
        instead."""
        if self.holder is not None:
            return self.holder.line
        if self.kind == "blob":
            return f"a blob {self.oid} that holds no holder's line"
        return f"a {self.kind} {self.oid}, not a lock a finish takes"

    @property
    def clear(self) -> str:
        """The one git command that clears this lock and no other."""
        return f"git -C {self.root} update-ref -d {self.ref} {self.oid}"


@dataclass(frozen=True)
class Taken:
    """A finish lock this process holds: `blob` is its own holder's blob. `cleared` is
    the dead holder whose lock the take swapped out, or None."""

    root: Path
    ref: str
    blob: str
    holder: Holder
    cleared: Holder | None = None


def take(root: str | os.PathLike, ref: str, *, by: str, name: str,
         stage: str) -> Taken:
    """Take the finish lock `ref` in the repo at `root` for the finish of `name`'s
    `stage`. Raises `Refused` with what holds it and the command that clears it, when
    it is held by a live holder or by anything this does not clear, and `GitError` when
    git can't make the ref."""
    git = _LockGit(root)
    mine = holder(by, name, stage)
    blob = git.hash_blob(mine.line + "\n")
    for _ in range(_TRIES):
        if git.create(ref, blob):
            return Taken(git.root, ref, blob, mine)
        now = found(root, ref)
        if now is None:
            # The holder released it between the two calls: try to create it again.
            continue
        if not _dead(now.holder):
            raise Refused([_held(now)])
        if git.swap(ref, blob, now.oid):
            return Taken(git.root, ref, blob, mine, cleared=now.holder)
        # Another finish swapped it first: what holds it now is read again.
    # The ref kept changing, or git refuses the ref itself: one more try says which.
    git.create(ref, blob, said=True)
    return Taken(git.root, ref, blob, mine)


def release(taken: Taken) -> bool:
    """Release a lock this process took. False, with the ref left as it is, when the
    ref no longer points at the taker's own blob."""
    return _LockGit(taken.root).delete(taken.ref, taken.blob)


def cleared_entry(taken: Taken) -> write.Entry | None:
    """The `lock.cleared` event a take that cleared a dead holder's lock appends to the
    round's log, or None when it cleared none."""
    if taken.cleared is None:
        return None
    return write.Entry(CLEARED, {"ref": taken.ref, "holder": taken.cleared.line})


def found(root: str | os.PathLike, ref: str) -> Held | None:
    """What holds the finish lock `ref` in the repo at `root` now, or None when nothing
    does."""
    git = _LockGit(root)
    oid = git.target(ref)
    return None if oid is None else git.held(ref, oid)


def stale(root: str | os.PathLike, refs: Iterable[str], *,
          now_s: float | None = None) -> list[Held]:
    """Each finish lock in the repo at `root` that holds a line in the holder's format
    taken more than `STALE_S` ago: every ref under `REF_PREFIX` and each of `refs`."""
    git = _LockGit(root)
    old = []
    for ref, oid in git.locks(refs):
        now = git.held(ref, oid)
        if now.holder is not None and now.holder.age_s(now_s) > STALE_S:
            old.append(now)
    return old


def _dead(found_holder: Holder | None) -> bool:
    """Whether the holder is a process of this machine that is no longer running."""
    if found_holder is None or found_holder.host != places.host() or os.name != "posix":
        return False
    try:
        os.kill(found_holder.pid, 0)
    except ProcessLookupError:
        return True
    except (OSError, OverflowError):
        # It runs as another user, or no pid can be that number: it counts as live.
        return False
    return False


def _held(now: Held) -> Unmet:
    return Unmet(f"the finish lock {now.ref} in {now.root} is held: {now.said}. Ask "
                 "the holder; once no finish of theirs runs, the command below clears "
                 "this lock and no other", now.clear)


class _LockGit(Git):
    """The git calls of the lock, in the repo at `root`."""

    def hash_blob(self, text: str) -> str:
        return self._run("hash-object", "-w", "--stdin", stdin_data=text)[1].strip()

    def create(self, ref: str, blob: str, *, said: bool = False) -> bool:
        # The empty old value has git refuse when the ref exists.
        code, _ = self._run("update-ref", ref, blob, "", ok=(0,) if said else (0, 1, 128))
        return code == 0

    def swap(self, ref: str, blob: str, was: str) -> bool:
        code, _ = self._run("update-ref", ref, blob, was, ok=(0, 1, 128))
        return code == 0

    def delete(self, ref: str, blob: str) -> bool:
        code, _ = self._run("update-ref", "-d", ref, blob, ok=(0, 1, 128))
        return code == 0

    def target(self, ref: str) -> str | None:
        code, out = self._run("rev-parse", "--verify", "--quiet", ref, ok=(0, 1, 128))
        return out.strip() if code == 0 and out.strip() else None

    def held(self, ref: str, oid: str) -> Held:
        _, out = self._run("cat-file", "--batch-check", stdin_data=f"{oid}\n")
        parts = out.split()
        kind = parts[1] if len(parts) == 3 else "missing object"
        size = int(parts[2]) if len(parts) == 3 and parts[2].isdigit() else 0
        found_holder = None
        if kind == "blob" and size <= _LINE_MAX_BYTES:
            found_holder = parse(self._run("cat-file", "blob", oid)[1])
        return Held(self.root, ref, oid, kind, found_holder)

    def locks(self, refs: Iterable[str]) -> list[tuple[str, str]]:
        """Each ref under `REF_PREFIX`, and each of `refs` that exists, with the object
        it points at."""
        wanted = [REF_PREFIX, *dict.fromkeys(refs)]
        _, out = self._run("for-each-ref", "--format=%(objectname) %(refname)", *wanted)
        found_refs = {}
        for line in out.splitlines():
            oid, _, ref = line.partition(" ")
            if ref and (ref.startswith(REF_PREFIX) or ref in wanted):
                found_refs[ref] = oid
        return sorted(found_refs.items())


__all__ = ["REF_PREFIX", "STALE_S", "CLEARED", "Holder", "Held", "Taken", "GitError",
           "ref_of", "holder", "parse", "take", "release", "cleared_entry", "found",
           "stale"]

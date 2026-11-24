"""Which session a process is in: the membership rule.

A binding records its session's ROOTS in its claim file (`claimfile`), each a pid, that
pid's start time (field 22 of `/proc/<pid>/stat`) and a reach: `tree` (the root and
everything descended from it) or `self` (that one process only). A process is inside
the session of the first root met walking from the process itself up its ancestry,
among the roots of every live claim file, where a `self` root counts only for the
process itself. A process that meets no root is inside no session.

So descent alone never decides: sessions nest, and a socket somewhere in a process's
ancestry does not admit it. A pid whose start time differs from the recorded one is not
that root, so a reused pid neither admits a stranger nor keeps a dead session's file
live.

Everything here reads processes and the claim files in the runtime dir, nothing on the
daemon. `ProcView` is the one reader of processes: `/proc` where there is one, else
psutil (`PsutilView`, macOS and Windows); a test hands in its own.

`sessions_of(..., strict=True)` is the walk a check that must not guess makes: a process
it can't read (gone, access refused) before it reaches one with no parent raises
`Unplaceable`, where the plain walk stops and reads "inside no session". One exception,
on a view whose platform's tree ends at a gone parent (`ends_at_gone_parent`: psutil's
on Windows, which never gives an orphan a new parent): there an ancestor that is
positively gone (`gone`), or a pid whose start is later than its child's (a later
process reused it; a real parent always starts first), ends the walk as a process with
no parent does. An ancestor that is there but can't be read still raises, and so does
the walk's own process. On Linux and macOS every orphan is given a live parent, so the
walk never meets a gone one but in a race, and it raises there.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass

TREE, SELF = "tree", "self"
REACHES = (TREE, SELF)


@dataclass(frozen=True)
class Root:
    pid: int
    start: str
    reach: str = TREE

    def as_dict(self) -> dict:
        return {"pid": self.pid, "start": self.start, "reach": self.reach}

    @classmethod
    def from_dict(cls, raw) -> "Root | None":
        if not isinstance(raw, dict):
            return None
        pid, start, reach = raw.get("pid"), raw.get("start"), raw.get("reach", TREE)
        if (isinstance(pid, bool) or not isinstance(pid, int) or not isinstance(start, str)
                or reach not in REACHES):
            return None
        return cls(pid, start, reach)


class ProcView:
    """Reads a process's parent and start time from `/proc`. `stat(pid)` answers
    `(ppid, start)` or None when there is no such process (or it cannot be read)."""

    #: Whether a strict walk ends at an ancestor that is gone or whose pid a later
    #: process took, as at a process with no parent. False where an orphan is given a
    #: live parent.
    ends_at_gone_parent = False

    def __init__(self, root: str = "/proc"):
        self.root = root

    def stat(self, pid: int):
        try:
            with open(f"{self.root}/{int(pid)}/stat", "rb") as fh:
                raw = fh.read().decode("utf-8", "replace")
        except (OSError, ValueError):
            return None
        # The command name sits in parentheses and may itself hold spaces or parens;
        # everything after the LAST `)` is space-separated, starting at field 3.
        tail = raw[raw.rfind(")") + 1:].split()
        try:
            return int(tail[1]), tail[19]
        except (IndexError, ValueError):
            return None

    def start_time(self, pid: int) -> str | None:
        st = self.stat(pid)
        return None if st is None else st[1]

    def parent(self, pid: int) -> int | None:
        st = self.stat(pid)
        return None if st is None else st[0]

    def alive(self, pid: int, start: str) -> bool:
        return self.start_time(pid) == start

    def ancestry(self, pid: int, limit: int = 4096, *, strict: bool = False):
        """The process itself, then each ancestor up to (not including) pid 0. With
        `strict`, a process that can't be read, or a walk that loops or runs past
        `limit`, raises `Unplaceable` instead of ending the walk; on a view that
        `ends_at_gone_parent`, an ancestor that is gone, or one that started after its
        child, ends it."""
        seen = set()
        ends = strict and self.ends_at_gone_parent
        child_start = None
        while pid and pid > 0:
            if pid in seen or len(seen) >= limit:
                if strict:
                    raise Unplaceable(pid)
                return
            seen.add(pid)
            st = self.stat(pid)
            if st is None:
                if ends and child_start is not None and gone(self, pid):
                    return
                if strict:
                    raise Unplaceable(pid)
                return
            if ends and child_start is not None and _started_after(st[1], child_start):
                return
            yield pid, st[1]
            child_start = st[1]
            pid = st[0]


class PsutilView(ProcView):
    """Reads a process's parent and start time through psutil: where there is no
    `/proc` (macOS, Windows). The start time is psutil's `create_time()`, spelled as a
    string, so roots written on such a machine compare with what this reads."""

    ends_at_gone_parent = sys.platform == "win32"

    def __init__(self):
        super().__init__(root="")

    def stat(self, pid: int):
        import psutil

        try:
            p = psutil.Process(int(pid))
            return int(p.ppid()), repr(p.create_time())
        except (psutil.Error, OSError, ValueError):
            return None


def gone(proc: ProcView, pid: int) -> bool:
    """`proc.stat` answered nothing: is there positively no such process? A view may
    answer itself (`gone(pid)`); else psutil for `PsutilView` (which counts a process it
    may not open as there), else the view's `/proc` entry (missing is gone). False
    whenever it can't tell."""
    hook = getattr(proc, "gone", None)
    if callable(hook):
        try:
            return hook(pid) is True
        except Exception:                                        # noqa: BLE001
            return False
    if isinstance(proc, PsutilView):
        try:
            import psutil

            return not psutil.pid_exists(int(pid))
        except Exception:                                        # noqa: BLE001
            return False
    base = getattr(proc, "root", "")
    if not base:
        return False
    try:
        os.stat(f"{base}/{int(pid)}")
    except FileNotFoundError:
        return True
    except (OSError, ValueError):
        return False
    return False                      # there, but its stat couldn't be read


def _started_after(start: str, child_start: str) -> bool:
    """Whether `start`, read as a number, is later than `child_start`. A start that
    doesn't read as a number reads as not later."""
    try:
        return float(start) > float(child_start)
    except (TypeError, ValueError):
        return False


class Unplaceable(Exception):
    """A strict walk met a process it can't read before one with no parent: whether
    the walk's process is inside a session can't be told."""

    def __init__(self, pid: int):
        super().__init__(f"process {pid} can't be read")
        self.pid = pid


def _default_view() -> ProcView:
    return ProcView() if os.path.isdir("/proc/self") else PsutilView()


DEFAULT_PROC = _default_view()


def root_of(pid: int, reach: str = TREE, proc: ProcView | None = None) -> Root | None:
    """A root for a running process, with its start time read now."""
    start = (proc or DEFAULT_PROC).start_time(pid)
    return None if start is None else Root(int(pid), start, reach)


def sessions_of(pid: int, files, proc: ProcView | None = None, *,
                strict: bool = False) -> list:
    """The claim-file keys whose session `pid` is inside: the files holding a root at
    the nearest level of `pid`'s ancestry that holds any. More than one key only for a
    holder that is a `self` root of several bindings (a module's own loop holding
    several names, a `ClaimServer` per binding). `files` is an iterable of `(key, roots)`, roots as `Root`s.
    An empty list: inside no session. With `strict`, a walk that can't reach a process
    with no parent, short of a root, raises `Unplaceable` (on a view that
    `ends_at_gone_parent`, a gone ancestor ends the walk there)."""
    proc = proc or DEFAULT_PROC
    table = [(key, [r for r in roots if r is not None]) for key, roots in files]
    for depth, (at, start) in enumerate(proc.ancestry(pid, strict=strict)):
        hits = []
        for key, roots in table:
            for r in roots:
                if r.pid != at or r.start != start:
                    continue
                if r.reach == SELF and depth != 0:
                    continue                  # a self root admits only itself
                hits.append(key)
                break
        if hits:
            return hits
    return []


def inside(pid: int, key: str, files, proc: ProcView | None = None) -> bool:
    """Is `pid` inside the session whose claim file is `key`?"""
    return key in sessions_of(pid, files, proc)


__all__ = ["DEFAULT_PROC", "REACHES", "SELF", "TREE", "ProcView", "PsutilView", "Root",
           "Unplaceable", "gone", "inside", "root_of", "sessions_of"]

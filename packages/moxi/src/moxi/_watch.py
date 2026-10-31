"""The watchdog behind `moxi.proc.spawn(..., lifeline="watch")` on POSIX.

Run as `python -m moxi._watch <pid> <create time>`, in a session of its own, with its
stdin the lifeline's read end. `<pid>` and `<create time>` name the watched child; a
process is taken for one the watchdog watches only when both match, so a pid the system
has given to another process since is never signalled.

Once it has looked at the child, it writes `ready` and a newline on stdout (the parent
waits for that before `spawn` returns) and then sends stdout and stderr to `/dev/null`.

While the lifeline is open, each second it adds the current descendants of the child,
and of every process it already watches, to the set it watches. When stdin reaches EOF
(the parent closed the lifeline, or died) it scans for descendants, sends SIGTERM to the
child and to every watched process still alive, scans again and adds what it finds,
waits up to two seconds for all of them to go, scans once more with the tree held still
(each process stopped with SIGSTOP, so none starts another between that scan and the
kill), sends SIGKILL to every one left, the child first, and exits once none is left. It
exits on its own, sending nothing, when the child is gone and no watched process is
left. A process that has ended and was not yet reaped (a zombie) counts as gone.

A descendant started and moved away from the child's tree between two scans (a wrapper
that forks and exits at once) is never seen, so it is not reached.
"""

from __future__ import annotations

import math
import os
import sys
import time

#: Seconds between SIGTERM and SIGKILL.
GRACE = 2.0
#: How often the child's tree is scanned while the lifeline is open, in seconds.
CHECK = 1.0
#: The line the watchdog writes once it is watching.
READY = b"ready\n"
# How long the watchdog waits for the processes it sent SIGKILL to to go.
_REAP = 5.0
_POLL = 0.05
# The tree is held still for the last scan in at most this many rounds, each waiting at
# most this long for the processes it stopped.
_FREEZE_ROUNDS = 5
_FREEZE_WAIT = 0.5


class Watch:
    """The child and the set of its descendants the watchdog has seen.

    Every process is kept with the start time it had when it was seen, and each later
    look checks that time again, so a pid that has since gone to another process never
    matches.
    """

    def __init__(self, pid: int, started: float):
        self.pid = pid
        self.started = started
        #: pid -> start time, for every descendant seen so far.
        self.watched: dict[int, float] = {}

    def child(self):
        """The child as a `psutil.Process`, or None once it is gone."""
        return _match(self.pid, self.started)

    def live(self) -> list:
        """The watched processes still alive (the child not among them); the gone ones
        are dropped from the set."""
        found = []
        for pid, started in list(self.watched.items()):
            process = _match(pid, started)
            if process is None:
                del self.watched[pid]
            else:
                found.append(process)
        return found

    def scan(self) -> None:
        """Add the current descendants of the child and of every watched process."""
        import psutil

        roots = [p.pid for p in [self.child(), *self.live()] if p is not None]
        if not roots:
            return
        below: dict[int, list[tuple[int, float]]] = {}
        for process in psutil.process_iter(["ppid", "create_time"]):
            ppid, started = process.info["ppid"], process.info["create_time"]
            if ppid is not None and started is not None:
                below.setdefault(ppid, []).append((process.pid, started))
        seen = set(roots)
        todo = list(roots)
        while todo:
            for pid, started in below.get(todo.pop(), ()):
                if pid not in seen:
                    seen.add(pid)
                    todo.append(pid)
                    if pid != self.pid:
                        self.watched[pid] = started

    def done(self) -> bool:
        """True when the child is gone and no watched process is left."""
        return self.child() is None and not self.live()

    def stop(self, grace: float = GRACE) -> None:
        """End the child and everything watched: SIGTERM, then SIGKILL for what is left
        after `grace` seconds."""
        import signal

        # Scan first: a child that dies of the SIGTERM before the scan after it leaves its
        # descendants to init, where no scan finds them. A descendant born between this scan
        # and the SIGTERM, to a child that then dies of it, can still be missed.
        self.scan()
        self._send(signal.SIGTERM)
        self.scan()
        deadline = time.monotonic() + grace
        while time.monotonic() < deadline and not self.done():
            time.sleep(_POLL)
        self._freeze()
        self._send(signal.SIGKILL)
        deadline = time.monotonic() + _REAP
        while time.monotonic() < deadline and not self.done():
            time.sleep(_POLL)

    def _freeze(self) -> None:
        """Scan once more, with the tree held still: SIGSTOP the child and every watched
        process, wait for them to stop, scan, and do the same for what the scan adds. A
        stopped process starts nothing, so nothing is born between the last scan and the
        SIGKILL that follows."""
        import psutil
        import signal

        held: set[int] = set()
        for _ in range(_FREEZE_ROUNDS):
            batch = [p for p in [self.child(), *self.live()]
                     if p is not None and p.pid not in held]
            if not batch:
                return
            for process in batch:
                held.add(process.pid)
                try:
                    process.send_signal(signal.SIGSTOP)
                except psutil.Error:
                    pass
            deadline = time.monotonic() + _FREEZE_WAIT
            while time.monotonic() < deadline and not all(map(_still, batch)):
                time.sleep(0.01)
            self.scan()

    def _send(self, signum: int) -> None:
        import psutil

        for process in [p for p in [self.child(), *self.live()] if p is not None]:
            try:
                process.send_signal(signum)
            except psutil.Error:
                pass  # gone since it was matched


def _still(process) -> bool:
    """True once `process` has stopped or is gone."""
    import psutil

    try:
        return process.status() in (psutil.STATUS_STOPPED, psutil.STATUS_ZOMBIE)
    except psutil.Error:
        return True


def _match(pid: int, started: float):
    """The process `pid` as a `psutil.Process` when it started at `started` and has not
    ended; else None."""
    import psutil

    from .proc import _START_TOLERANCE

    try:
        process = psutil.Process(pid)
        if abs(process.create_time() - started) >= _START_TOLERANCE:
            return None
        if process.status() == psutil.STATUS_ZOMBIE:
            return None
    except psutil.Error:
        return None
    return process


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if sys.platform == "win32":
        print("moxi._watch: Windows holds a watched child in a Job Object instead",
              file=sys.stderr)
        return 2
    parsed = _parse(args)
    if parsed is None:
        print("usage: python -m moxi._watch <pid> <create time>", file=sys.stderr)
        return 2
    watch = Watch(*parsed)
    watch.scan()
    _say_ready()
    while not watch.done():
        if _lifeline_closed(CHECK):
            watch.stop()
            return 0
        watch.scan()
    return 0


def _parse(args: list[str]) -> tuple[int, float] | None:
    if len(args) != 2 or not args[0].isdigit() or int(args[0]) <= 1:
        return None
    try:
        started = float(args[1])
    except ValueError:
        return None
    if not math.isfinite(started) or started < 0:
        return None
    return int(args[0]), started


def _say_ready() -> None:
    """Tell the parent the watch has begun, then write nothing more anywhere: the parent
    stops reading, and the watchdog's output would have nowhere to go."""
    try:
        os.write(1, READY)
    except OSError:
        pass
    quiet = os.open(os.devnull, os.O_WRONLY)
    os.dup2(quiet, 1)
    os.dup2(quiet, 2)
    os.close(quiet)


def _lifeline_closed(wait: float) -> bool:
    """Wait up to `wait` seconds on stdin; True once it reaches EOF (or fails)."""
    import select

    try:
        ready, _, _ = select.select([0], [], [], wait)
        if not ready:
            return False
        return not os.read(0, 4096)
    except InterruptedError:
        return False
    except OSError:
        return True


if __name__ == "__main__":
    sys.exit(main())

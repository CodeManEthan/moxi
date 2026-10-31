"""The fallback worker: the credentials and broad sets on Python's re, in a child process.

Upstream patterns are linear only on RE2; on Python's re some go quadratic. So without
RE2 these sets run in a child (`python -I -m moxi_secrets.fallback`), started at the
first fallback scan and kept for the life of the calling process. Each scan has a
wall-clock deadline of 1 s plus 2 s per MB of the text's UTF-8. At the deadline the
parent kills the child and the scan reports, for each set it asked for, that the text is
unscanned; the next scan starts a fresh child. Every other failure of the child (it will
not start, exits, is killed, or sends a reply that does not parse) ends the same way: a
scan returns the worker's hits for a set, or `None` for it, and never raises.

The child exits when its pipe closes. A child inside an re search sees no closed pipe
until the search ends, so on Linux and macOS it also arms a timer for each text, at the
deadline plus 0.5 s, and exits when it fires; it cancels the timer when the text is done.
Windows has no such timer: there a child whose caller was killed runs until its search
ends. It holds no lock and writes nothing.

The protocol, one request at a time: a JSON line {"sets", "path", "size", "deadline"}
and then `size` bytes of UTF-8; the reply is one JSON line {"hits": {set: [[rule, start,
end], ...]}}.
"""

from __future__ import annotations

import json
import os
import sys
import threading

#: Seconds per scan, and per MB of the text's UTF-8.
BASE_DEADLINE = 1.0
PER_MB = 2.0
#: How long past the deadline the child's own timer fires.
CHILD_GRACE = 0.5
#: How the child exits when its timer fires.
TIMER_EXIT = 3


def deadline(size: int) -> float:
    """The deadline in seconds for a text of `size` UTF-8 bytes."""
    return BASE_DEADLINE + PER_MB * size / 1_000_000


def command() -> list:
    """The child's command line."""
    return [sys.executable, "-I", "-m", "moxi_secrets.fallback"]


class WorkerFailed(Exception):
    """How a worker failed: will not start, exited, out of time, or a bad reply."""

    def __init__(self, how: str):
        super().__init__(how)
        self.how = how


class Worker:
    """One child and the thread that talks to it."""

    def __init__(self, argv=None):
        import queue
        import subprocess

        flags = 0
        if sys.platform == "win32":
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            self.proc = subprocess.Popen(
                argv or command(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, creationflags=flags)
        except (OSError, ValueError) as exc:
            raise WorkerFailed(f"the worker will not start ({type(exc).__name__})") \
                from None
        self.requests: queue.Queue = queue.Queue()
        self.replies: queue.Queue = queue.Queue()
        self.thread = threading.Thread(target=self._talk, daemon=True,
                                       name="moxi-secrets-fallback")
        self.thread.start()

    def _talk(self) -> None:
        stdin, stdout = self.proc.stdin, self.proc.stdout
        while True:
            request = self.requests.get()
            if request is None:
                return
            try:
                stdin.write(request)
                stdin.flush()
                line = stdout.readline()
            except (OSError, ValueError):
                self.replies.put(None)
                return
            self.replies.put(line)
            if not line:
                return

    def ask(self, header: dict, data: bytes, wait: float):
        """The parsed reply, or `WorkerFailed`."""
        import queue

        self.requests.put(json.dumps(header).encode("ascii") + b"\n" + data)
        try:
            line = self.replies.get(timeout=wait)
        except queue.Empty:
            raise WorkerFailed("the scan reached its deadline") from None
        if not line:
            raise WorkerFailed("the worker exited")
        try:
            reply = json.loads(line)
            hits = reply["hits"]
            if not isinstance(hits, dict):
                raise TypeError
            return {name: [(str(r), int(s), int(e)) for r, s, e in found]
                    for name, found in hits.items()}
        except (ValueError, TypeError, KeyError):
            raise WorkerFailed("the worker sent a reply that does not parse") from None

    def alive(self) -> bool:
        return self.proc.poll() is None and self.thread.is_alive()

    def stop(self) -> None:
        try:
            self.proc.kill()
        except OSError:
            pass
        try:
            self.proc.wait(timeout=5)
        except Exception:  # noqa: BLE001
            pass
        self.requests.put(None)
        for stream in (self.proc.stdin, self.proc.stdout):
            try:
                stream.close()
            except Exception:  # noqa: BLE001
                pass


_worker: Worker | None = None
_lock = threading.Lock()
#: How the last scan's worker failed, or None (the doctor reads this).
last_failure: str | None = None


def scan(t, set_names, path) -> dict:
    """{set: [(rule, start, end), ...] or None} for the gitleaks-format sets.

    None for a set means the text is unscanned for it: the deadline passed or the worker
    failed. Never raises.
    """
    global _worker, last_failure
    data = t.etext.encode("utf-8")
    wait = deadline(len(data))
    header = {"sets": list(set_names), "path": path, "size": len(data), "deadline": wait}
    with _lock:
        try:
            if _worker is None or not _worker.alive():
                if _worker is not None:
                    _worker.stop()
                _worker = None
                _worker = Worker()
            reply = _worker.ask(header, data, wait)
            last_failure = None
        except WorkerFailed as exc:
            last_failure = exc.how
            if _worker is not None:
                _worker.stop()
            _worker = None
            return {name: None for name in set_names}
        except Exception as exc:  # noqa: BLE001 - any failure is unscanned, never clean
            last_failure = f"the worker failed ({type(exc).__name__})"
            if _worker is not None:
                _worker.stop()
            _worker = None
            return {name: None for name in set_names}
    return {name: reply.get(name) for name in set_names}


def shutdown() -> None:
    """Stop the worker, if one runs (for tests; a process's exit closes the pipe)."""
    global _worker
    with _lock:
        if _worker is not None:
            _worker.stop()
        _worker = None


# -- the child ------------------------------------------------------------------------------

def _arm(seconds: float) -> None:
    import signal

    if not hasattr(signal, "setitimer"):
        return
    signal.setitimer(signal.ITIMER_REAL, max(seconds, 0.0))


def _install_timer() -> None:
    import signal

    if not hasattr(signal, "setitimer"):
        return

    def fire(signum, frame):
        os._exit(TIMER_EXIT)

    signal.signal(signal.SIGALRM, fire)


def main() -> int:
    from . import engine
    from .scan import run_gitleaks

    stdin, stdout = sys.stdin.buffer, sys.stdout.buffer
    eng = engine.python()
    _install_timer()
    while True:
        header = stdin.readline()
        if not header:
            return 0
        try:
            request = json.loads(header)
            size = int(request["size"])
            wait = float(request["deadline"])
        except (ValueError, KeyError, TypeError):
            return 2
        _arm(wait + CHILD_GRACE)
        data = stdin.read(size)
        if len(data) != size:
            return 0
        t = engine.Text(data.decode("utf-8", "surrogateescape"))
        hits = run_gitleaks(t, tuple(request["sets"]), request.get("path"), eng)
        _arm(0)
        try:
            stdout.write(json.dumps({"hits": hits}).encode("ascii") + b"\n")
            stdout.flush()
        except (BrokenPipeError, OSError):
            return 0


if __name__ == "__main__":
    sys.exit(main())

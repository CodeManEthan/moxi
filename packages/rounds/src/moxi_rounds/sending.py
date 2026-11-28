"""Sending a check to a lane: the near side of a lane's session.

A lane is another machine a check can be sent to (`[lanes.<name>]` in rounds' settings).
`send` opens one ssh session to `<moxi> rounds serve` there (`serving`, the far side)
and holds its stdin as the far run's lifeline; every part of the exchange is a frame
(`frames`). In order:

1. **The session** (`session_argv`): the lane's `ssh` words as given, then `-T`,
   `BatchMode=yes`, `ConnectTimeout` from `connect_timeout_s`, `ServerAliveInterval=15`,
   `ServerAliveCountMax=4`, and `ControlMaster=no` with `ControlPath=none`, so the
   session opens its own connection and every option acts on it; then the host and the
   one far word, `<moxi> rounds serve`. No word of the request goes on ssh's command
   line. It starts through `moxi.proc.spawn` with the stdin lifeline.
2. **The request** (`request_of`), then the far lock's answer: its waiting lines go to
   `say`, each led by `lane <name>:`; `frames.BUSY` raises `Busy`; a refusal, or the
   session's end before an answer, raises `Unreachable`, telling a far side that could
   not be reached, a `serve` that is missing and one that failed to start apart, with
   the last lines ssh wrote to stderr. Asked not to wait, the answer must come within
   `connect_timeout_s` plus `ANSWER_WAIT_S`, else the lane is `Unreachable`.
3. **The send**, once the far side holds the place the run will use: the far side's
   refs, then the bundle of the build's branch and the target's head less what the far
   side holds (`bundle_of`, empty when it lacks nothing), then each brought private file
   the worktree holds now (its path, its `sha256` as read here, its bytes), then
   `frames.FILES_END`. stdin then stays open, unused.
4. **The run**: `frames.NOT_READY` (a brought file, a preflight line or the far tree
   failed) raises `NotReady`; `frames.STARTED`, then the command's output, kept in
   memory and written whole to the output file (`files.atomic_write`) every
   `LOG_EVERY_S` while output arrives, at each waiting line and at the end, so the file
   trails the run by up to that long; then the result.

**How it ends.** After the result, stdin is closed and ssh is waited for. An interrupt
(`KeyboardInterrupt`) closes stdin, which ends the far run by the far side's own end,
waits for ssh up to `checking.GRACE_S` plus `END_WAIT_S`, then kills it; a second
interrupt kills it at once. Once the far side has answered with a place, a session that
ends with no result raises `Lost`, and so does one with no result by the time limit plus
`checking.GRACE_S` plus `BACKSTOP_S` after the command started: the run may have started
there, so it is never sent anywhere else. `Busy`, `Unreachable` and `NotReady` mean
nothing ran.
"""

from __future__ import annotations

import collections
import hashlib
import json
import math
import queue
import subprocess
import threading
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from moxi import files, proc

from . import checking, frames, gitreads, serving
from .errors import RoundsError
from .gitreads import Git

#: How long, beyond `connect_timeout_s`, a session asked not to wait may take to give
#: the far lock's answer, in seconds: the far Moxi's start and its lock's read.
ANSWER_WAIT_S = 60.0
#: How often the output file is written again while output arrives, in seconds.
LOG_EVERY_S = 2.0
#: How long past the time limit and `checking.GRACE_S` a started run may go with no
#: result before the session is ended, in seconds.
BACKSTOP_S = 60.0
#: How long past `checking.GRACE_S` an interrupted session's ssh is waited for, in
#: seconds, before it is killed.
END_WAIT_S = 15.0
#: How many of the last lines ssh wrote to stderr a refusal gives.
STDERR_LINES = 20
#: How long the bundle may take to make, in seconds.
BUNDLE_TIMEOUT_S = 600.0
#: The exit code of an ssh client that could not reach its host.
SSH_FAILED = 255
#: The exit code of a far shell that found no such command.
NOT_FOUND = 127


class LaneError(RoundsError):
    """A lane's run that did not give a result: `reason` and `fix`, and the `lane`'s
    name."""

    def __init__(self, lane: str, reason: str, fix: str | None = None):
        super().__init__(reason, fix)
        self.lane = lane


class Unreachable(LaneError):
    """The lane could not be asked: ssh, the host or its `serve` failed before the far
    lock answered. Nothing ran."""


class Busy(LaneError):
    """The far lock, or the lane's shared lock, could not be taken now, and the run
    asked not to wait. Nothing was sent."""


class NotReady(LaneError):
    """The far side held a place but could not start the run: a brought file, a
    preflight line or its tree failed. Nothing ran."""


class Lost(LaneError):
    """The session ended with no result after the far side held a place: the run may
    have started there."""


@dataclass(frozen=True)
class LaneRan:
    """A lane's run as the far side gave its result: the `exit_code` (None when ended at
    its time limit), the `duration_s`, the far machine's name (`host`), the place of its
    check lock it held (`check_lock`) and the `output` file written here."""

    exit_code: int | None
    duration_s: float
    host: str
    check_lock: int | None
    output: Path


def session_argv(lane: Mapping) -> list[str]:
    """The words that open a lane's session: its `ssh` words as given, the options, its
    `host`, and the one far word `<moxi> rounds serve`."""
    connect_s = max(1, math.ceil(float(lane["connect_timeout_s"])))
    return [*[str(word) for word in lane["ssh"]], "-T", "-o", "BatchMode=yes",
            "-o", f"ConnectTimeout={connect_s}", "-o", "ServerAliveInterval=15",
            "-o", "ServerAliveCountMax=4", "-o", "ControlMaster=no",
            "-o", "ControlPath=none", str(lane["host"]), f"{lane['moxi']} rounds serve"]


def request_of(*, name: str, stage: str, step: str, repo: str, branch: str, tip: str,
               into: str, into_head: str | None, argv: Sequence[str], timeout_s: float,
               memory_mib: int | None, preflight: Iterable[str], wait: bool,
               shared_lock: str | None) -> dict:
    """The request a near side sends, for the build `stage` of the round `name`."""
    return {"version": frames.VERSION, "round": name, "stage": stage, "step": step,
            "repo": repo, "branch": branch, "tip": tip, "into": into,
            "into_head": into_head, "argv": [str(word) for word in argv],
            "timeout_s": timeout_s, "memory_mib": memory_mib,
            "preflight": [str(line) for line in preflight], "wait": wait,
            "from_host": serving.host_name(), "shared_lock": shared_lock}


def bundle_of(worktree: Path, branch: str, into: str | None, held: Iterable[str]) -> bytes:
    """A git bundle of the branch `branch` and, when given and another, the target's
    branch `into`, less every commit of `held` this repository has; `b""` when the far
    side lacks nothing. Raises `RoundsError` with git's words when it fails."""
    refs = [f"refs/heads/{branch}"]
    if into is not None and into != branch:
        refs.append(f"refs/heads/{into}")
    have = Git(worktree).present(list(held))
    argv = ["git", "-C", str(worktree), "bundle", "create", "--quiet", "-", *refs,
            *[f"^{one}" for one in have]]
    try:
        done = proc.run(argv, timeout_s=BUNDLE_TIMEOUT_S, env=gitreads.git_env())
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RoundsError(f"the bundle for the lane could not be made: {exc}") from None
    said = (done.stderr or b"").decode(errors="replace").strip()
    if done.returncode != 0:
        if "empty bundle" in said:
            return b""
        raise RoundsError(f"the bundle for the lane could not be made: {said}")
    return done.stdout or b""


def brought_files(worktree: Path, brought: object) -> list[tuple[dict, bytes]]:
    """Each brought private file the worktree holds now, as a regular file: its path
    and `sha256` as read here, and its bytes. One it no longer holds is left out."""
    found = []
    for entry in brought if isinstance(brought, list) else ():
        if not isinstance(entry, Mapping) or not isinstance(entry.get("path"), str) \
                or not entry["path"]:
            continue
        path = worktree / entry["path"]
        if not path.is_file() or path.is_symlink():
            continue
        try:
            data = path.read_bytes()
        except OSError:
            continue
        found.append(({"path": entry["path"], "sha256": hashlib.sha256(data).hexdigest()},
                      data))
    return found


# -- the session -------------------------------------------------------------------------

class _Late(Exception):
    """No frame came by the deadline."""


class _Session:
    """ssh, started with its stdin as the lifeline: frames read by one thread into a
    queue, the last lines of its stderr kept by another."""

    def __init__(self, argv: list[str]):
        self.popen = proc.spawn(argv, lifeline="stdin", stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE)
        self.frames: queue.Queue = queue.Queue()
        self.err: collections.deque = collections.deque(maxlen=STDERR_LINES)
        self.writer = frames.Writer(self._write)
        self.threads = [threading.Thread(target=self._read_out, daemon=True,
                                         name="rounds-lane-out"),
                        threading.Thread(target=self._read_err, daemon=True,
                                         name="rounds-lane-err")]
        for thread in self.threads:
            thread.start()

    def _write(self, data: bytes) -> None:
        self.popen.stdin.write(data)
        self.popen.stdin.flush()

    def _read_out(self) -> None:
        reader = frames.Reader(self.popen.stdout.read)
        try:
            while True:
                found = reader.frame()
                self.frames.put(found)
                if found is None:
                    return
        except (frames.FrameError, OSError, ValueError) as exc:
            self.frames.put(exc)

    def _read_err(self) -> None:
        for line in iter(self.popen.stderr.readline, b""):
            self.err.append(line.decode(errors="replace").rstrip())

    def next(self, deadline: float | None):
        """The next frame, `(kind, payload)`; None at the session's end. Raises `_Late`
        when none came by `deadline` (a `time.monotonic` time), and `frames.FrameError`
        for a frame that can't be read."""
        while True:
            wait_s = 0.5 if deadline is None else min(0.5, deadline - time.monotonic())
            if wait_s <= 0:
                raise _Late()
            try:
                found = self.frames.get(timeout=wait_s)
            except queue.Empty:
                continue
            if isinstance(found, Exception):
                raise frames.FrameError(str(found))
            return found

    def send(self, kind: str, payload: bytes = b"") -> bool:
        """Write a frame; False when the session's stdin is gone."""
        try:
            self.writer.send(kind, payload)
        except (OSError, ValueError):
            return False
        return True

    def close_stdin(self) -> None:
        try:
            self.popen.stdin.close()
        except (OSError, ValueError):
            pass

    def end(self, wait_s: float) -> int | None:
        """Close stdin, wait up to `wait_s` for ssh, kill it after; its exit code. A
        second interrupt during the wait kills it at once."""
        self.close_stdin()
        try:
            self.popen.wait(timeout=wait_s)
        except subprocess.TimeoutExpired:
            self.popen.kill()
        except KeyboardInterrupt:
            self.popen.kill()
            self.popen.wait()
            raise
        code = self.popen.wait()
        for thread in self.threads:
            thread.join(timeout=5)
        return code

    def left_output(self) -> bytes:
        """The output frames read but not taken yet, as one piece."""
        found = bytearray()
        while True:
            try:
                one = self.frames.get_nowait()
            except queue.Empty:
                return bytes(found)
            if isinstance(one, tuple) and one[0] == frames.OUT:
                found += one[1]

    def stderr_words(self) -> str:
        return " | ".join(line for line in self.err if line.strip())


class _Log:
    """The output received, kept in memory and written whole to `path` every
    `LOG_EVERY_S` while output arrives, and when `write` is called."""

    def __init__(self, path: Path):
        self.path = path
        self.data = bytearray()
        self.written_at = time.monotonic()
        self.write()

    def add(self, chunk: bytes) -> None:
        self.data += chunk
        if time.monotonic() - self.written_at >= LOG_EVERY_S:
            self.write()

    def write(self) -> None:
        files.atomic_write(self.path, bytes(self.data))
        self.written_at = time.monotonic()


def _message(payload: bytes) -> dict:
    found = frames.as_json(payload)
    return found if isinstance(found, dict) else {}


def send(lane_name: str, lane: Mapping, request: Mapping, *, worktree: Path,
         output: Path, brought: object = (),
         say: Callable[[str], None] | None = None) -> LaneRan:
    """Run `request` on the lane `lane_name`, whose `[lanes.<name>]` table, its
    defaults filled (`settings.lane`), is `lane`, by the steps above: send the build's
    commits from `worktree` and its brought private files (`brought`, the commission's
    entries), write the command's output to `output`, and give the result. Raises
    `Busy`, `Unreachable` or `NotReady` when nothing ran, `Lost` when the run may have
    started and no result came, and `RoundsError` when the far side could not start the
    command."""
    say = say or (lambda _line: None)
    host = str(lane["host"])
    try:
        session = _Session(session_argv(lane))
    except OSError as exc:
        raise Unreachable(lane_name, f"lane {lane_name}: the ssh client "
                          f"{lane['ssh'][0]!r} could not start here: {exc}",
                          f"install OpenSSH's client, or set ssh in [lanes.{lane_name}]"
                          ) from None
    log = _Log(output)
    ended = False
    try:
        found = _Exchange(session, lane_name, lane, request, log, say).run(
            worktree=worktree, brought=brought)
        session.end(checking.GRACE_S + END_WAIT_S)
        ended = True
        return LaneRan(exit_code=found.get("exit_code"),
                       duration_s=float(found.get("duration_s") or 0.0),
                       host=str(found.get("host") or host),
                       check_lock=found.get("check_lock"), output=output)
    finally:
        try:
            if not ended:
                session.end(checking.GRACE_S + END_WAIT_S)
        finally:
            # Output read before the session ended but not taken (an interrupt) is kept.
            log.data += session.left_output()
            log.write()


class _Exchange:
    def __init__(self, session: _Session, lane_name: str, lane: Mapping,
                 request: Mapping, log: _Log, say: Callable[[str], None]):
        self.session = session
        self.name = lane_name
        self.lane = lane
        self.host = str(lane["host"])
        self.request = dict(request)
        self.log = log
        self.say = say

    def said(self, line: str) -> None:
        self.say(f"lane {self.name}: {line}")
        self.log.write()

    def unreachable(self, why: str) -> Unreachable:
        """Why the far lock gave no answer, told from how the session ended."""
        try:
            code = self.session.popen.wait(timeout=5)
        except subprocess.TimeoutExpired:
            code = None
        err = self.session.stderr_words()
        moxi = self.lane["moxi"]
        fix = serving.fix_line(self.host)
        if code == SSH_FAILED:
            return Unreachable(self.name, f"lane {self.name}: ssh could not reach "
                               f"{self.host}: {err or why}",
                               f"check that ssh {self.host} works here with no prompt, "
                               f"or fix host in [lanes.{self.name}]")
        if code == NOT_FOUND or "not found" in err or "not recognized" in err:
            return Unreachable(self.name, f"lane {self.name}: {moxi} rounds serve is "
                               f"missing on {self.host}: {err or why}", fix)
        tail = f": {err}" if err else ""
        return Unreachable(self.name, f"lane {self.name}: {moxi} rounds serve failed to "
                           f"start on {self.host} ({why}, exit {code}){tail}", fix)

    def run(self, *, worktree: Path, brought: object) -> dict:
        answer_by = (None if self.request.get("wait") else time.monotonic()
                     + float(self.lane["connect_timeout_s"]) + ANSWER_WAIT_S)
        if not self.session.send(frames.REQUEST, _json(self.request)):
            raise self.unreachable("the session ended before the request was sent")
        placed = self.answer(answer_by)
        refs = self.refs(answer_by, placed)
        self.send_build(worktree, brought, refs)
        return self.result()

    def frame(self, deadline: float | None, *, before: str):
        try:
            return self.session.next(deadline)
        except _Late:
            raise _LateFor(before) from None

    def answer(self, deadline: float | None) -> dict:
        """The far lock's answer: its place, or `Busy`, or `Unreachable`."""
        while True:
            try:
                found = self.frame(deadline, before="the far lock answered")
            except _LateFor:
                self.session.end(0)
                raise Unreachable(self.name, f"lane {self.name}: {self.host} gave no "
                                  "answer from its check lock within "
                                  f"{deadline_words(self.lane)}",
                                  f"check that ssh {self.host} works here, then run the "
                                  "check again") from None
            except frames.FrameError as exc:
                raise self.unreachable(f"its answer can't be read: {exc}") from None
            if found is None:
                raise self.unreachable("the session ended before the far lock answered")
            kind, payload = found
            value = _message(payload) if kind != frames.OUT else {}
            if kind == frames.WAIT:
                self.said(str(value.get("line")))
            elif kind == frames.BUSY:
                raise Busy(self.name, f"lane {self.name} ({self.host}) is busy: "
                           f"{value.get('reason')}")
            elif kind == frames.REFUSED:
                raise Unreachable(self.name, f"lane {self.name}: {self.host} refused the "
                                  f"run: {value.get('reason')}",
                                  value.get("fix") or serving.fix_line(self.host))
            elif kind == frames.PLACED:
                return value
            else:
                raise self.unreachable(f"a {kind} frame came before the far lock's "
                                       "answer")

    def lost(self, why: str) -> Lost:
        return Lost(self.name, f"lane {self.name} ({self.host}) was lost mid-run, so "
                    f"nothing is recorded: {why}",
                    "run the check again; it may have run there")

    def refs(self, deadline: float | None, placed: dict) -> list[str]:
        """The commits the far side holds; its shared lock's answer comes first."""
        while True:
            try:
                found = self.frame(deadline, before="its refs")
            except _LateFor:
                raise self.lost("no refs came from the far side in time") from None
            except frames.FrameError as exc:
                raise self.lost(f"its frames can't be read: {exc}") from None
            if found is None:
                raise self.lost("the session ended before the far side sent its refs")
            kind, payload = found
            value = _message(payload)
            if kind == frames.WAIT:
                self.said(str(value.get("line")))
            elif kind == frames.BUSY:
                raise Busy(self.name, f"lane {self.name} ({self.host}) is busy: "
                           f"{value.get('reason')}")
            elif kind in (frames.REFUSED, frames.NOT_READY):
                raise self.not_ready(value)
            elif kind == frames.REFS:
                refs = value.get("refs")
                return [str(one) for one in refs] if isinstance(refs, list) else []
            else:
                raise self.lost(f"a {kind} frame came where the refs were due")

    def not_ready(self, value: Mapping) -> NotReady:
        reason = str(value.get("reason"))
        tail = value.get("tail")
        if tail:
            reason += "; its output ended:\n" + str(tail)
        return NotReady(self.name, f"lane {self.name} ({self.host}) is not ready, so the "
                        f"check did not run there: {reason}",
                        value.get("fix") or f"fix the lane's preflight or {self.host}, "
                        "then run the check again")

    def send_build(self, worktree: Path, brought: object, refs: list[str]) -> None:
        request = self.request
        into = request.get("into") if request.get("into_head") else None
        try:
            data = bundle_of(worktree, request["branch"], into, refs)
        except RoundsError:
            self.session.end(checking.GRACE_S + END_WAIT_S)
            raise
        # A send that fails means the session ended; the next read says how.
        if not self.session.send(frames.BUNDLE, data):
            return
        for meta, file_data in brought_files(worktree, brought):
            if not self.session.send(frames.FILE, frames.file_payload(meta, file_data)):
                return
        self.session.send(frames.FILES_END)

    def result(self) -> dict:
        deadline = None
        while True:
            try:
                found = self.frame(deadline, before="its result")
            except _LateFor:
                self.session.end(checking.GRACE_S + END_WAIT_S)
                raise self.lost(
                    f"no result came within {float(self.request['timeout_s']):g} s and "
                    f"{checking.GRACE_S + BACKSTOP_S:g} s more after the command "
                    "started") from None
            except frames.FrameError as exc:
                raise self.lost(f"its frames can't be read: {exc}") from None
            if found is None:
                raise self.lost(self.ended_words())
            kind, payload = found
            if kind == frames.OUT:
                self.log.add(payload)
                continue
            value = _message(payload)
            if kind == frames.WAIT:
                self.said(str(value.get("line")))
            elif kind == frames.NOT_READY:
                raise self.not_ready(value)
            elif kind == frames.REFUSED:
                if deadline is None:
                    raise self.not_ready(value)
                raise RoundsError(f"lane {self.name} ({self.host}): "
                                  f"{value.get('reason')}", value.get("fix"))
            elif kind == frames.STARTED:
                deadline = (time.monotonic() + float(self.request["timeout_s"])
                            + checking.GRACE_S + BACKSTOP_S)
            elif kind == frames.RESULT:
                return value
            else:
                raise self.lost(f"a {kind} frame came during the run")

    def ended_words(self) -> str:
        err = self.session.stderr_words()
        return "the session ended with no result" + (f": {err}" if err else "")


class _LateFor(Exception):
    def __init__(self, before: str):
        super().__init__(before)
        self.before = before


def deadline_words(lane: Mapping) -> str:
    return f"{float(lane['connect_timeout_s']) + ANSWER_WAIT_S:g} s"


def _json(value) -> bytes:
    return json.dumps(value).encode("utf-8")


__all__ = ["ANSWER_WAIT_S", "LOG_EVERY_S", "BACKSTOP_S", "END_WAIT_S", "STDERR_LINES",
           "BUNDLE_TIMEOUT_S", "SSH_FAILED", "NOT_FOUND", "LaneError", "Unreachable",
           "Busy", "NotReady", "Lost", "LaneRan", "session_argv", "request_of",
           "bundle_of", "brought_files", "send"]

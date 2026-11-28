"""`moxi rounds serve`: the far side of a lane, run by the near side over ssh.

A lane is another machine a check can be sent to. The near side opens one ssh session
to `<moxi> rounds serve` there and holds its stdin; every part of the exchange is a
frame (`frames`). `serve` imports everything it uses before it reads the request, so a
Moxi whose files change under a long run can't mix two versions in it. In order:

1. **The lifeline.** One thread reads stdin, the only reader of it (`_Stdin`): it hands
   the frames' bytes to the main thread, and at stdin's end it interrupts the main
   thread (`_interrupt_main`: SIGINT to that thread on POSIX, `_thread.interrupt_main`
   on Windows) and does nothing else, no timed exit, so whatever step runs ends by its
   own end: the far command by `proc.run`'s (SIGINT, `checking.GRACE_S`, SIGKILL), the
   wait for the lock by removing its ticket. The interrupt is disarmed once `serve`
   begins its final answer (`_Lifeline`): the near side closes stdin only after reading
   that answer, so the end of a normal run never interrupts the cleanup after it. A
   write to stdout that fails (the near side gone) is dropped, and the run goes on to
   its end.
2. **The request** (`frames.REQUEST`): a version `serve` doesn't speak is refused,
   naming both, with the fix line "update Moxi on <host>, or repair its environment
   there". Then a place of **this machine's own check lock** is taken by
   `check_line.slot` with this machine's `check_lock` settings: the same lock, line and
   holder record a `moxi rounds check` typed here takes. The holder record names the
   round, stage, step and repo as sent, `from_host` (the near machine's name) and
   `tree_dir` (the run's tree). Asked not to wait with no place free now, the answer is
   `frames.BUSY` and nothing more happens; waiting lines go to the near side as
   `frames.WAIT`. Where the request names a `shared_lock` (a lock file this machine's
   own scheduled work holds), it is taken next, the same way, and held to the run's end.
3. **The refs** (`frames.REFS`): the commits held in the bare repository kept for that
   sender and project (`repo_dir`, in rounds' cache folder); then the bundle
   (`frames.BUNDLE`, empty when nothing is missing) is fetched into it, and the build's
   branch and target are set there to the commits the request names. A ref no send has
   named for `REF_IDLE_DAYS` is dropped (`prune_refs`). Then the brought private files,
   one `frames.FILE` each, until `frames.FILES_END`.
4. **The sweep and the tree** (`sweep`): another run's tree is removed only when its
   `serve` is gone (its lock file can be taken) and no process of its run still lives
   (no holder record of this machine's check lock names that tree, or the one that does
   reads not alive by `check_line.run_alive`). Then the run's tree is made: a clone of
   the bare repository sharing its objects, with the build's branch checked out at the
   tip and the target's branch at the target's head; then each brought file is written
   into it, mode 0600, once its bytes match the `sha256` sent. A file that doesn't
   match ends the run as `frames.NOT_READY`, naming it.
5. **The preflight**: each line of the request's `preflight`, split as a shell would,
   runs in the tree through `proc.run` with `PREFLIGHT_TIMEOUT_S`; one that fails ends
   the run as `frames.NOT_READY`, naming the line, its exit code and the end of its
   output.
6. `frames.STARTED`; then the argv, `{root}` filled with the tree's path, runs under the
   held place by `checking.run_held`, exactly as a run on the near side does. Its output
   goes to a file beside the tree, which a thread streams as `frames.OUT` as it grows.
7. **The result** (`frames.RESULT`): the exit code (None when ended at its limit), the
   duration, this machine's name and the place it held. The place frees, the tree is
   removed, and `serve` exits.

The place is held from step 2 to the end, so a run that has its place never loses it to
one that asked later. Every end removes the tree; one a killed `serve` leaves is removed
by a later sweep once its run is gone.

**The probe** (`serve --probe`, for the doctor's lane row): the request names the
preflight lines of each project; then a payload of any bytes, then stdin's end. The
answer (`frames.PROBED`) gives the payload's length and SHA-256 as read, whether the end
was seen, the version, this machine's name, CPU count, available memory, git's version,
the check lock's holders and line, and each preflight line's result, run in a scratch
folder. Nothing is taken or kept.
"""

from __future__ import annotations

import hashlib
import json
import os
import queue
import re
import secrets
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Mapping
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from moxi import files, proc
from moxi.config import ConfigError

from . import check_line, checking, frames, gitreads, paths
from . import settings as own
from .errors import RoundsError

_WINDOWS = sys.platform == "win32"

#: The lanes' folder in rounds' cache folder; it holds `REPOS_DIR` and `TREES_DIR`.
LANES_DIR = "lanes"
#: The bare repositories, one per sending machine and project.
REPOS_DIR = "repos"
#: The run trees, each `<id>` with `<id>.lock` and `<id>.log` beside it.
TREES_DIR = "trees"
#: The file in a bare repository holding when each ref was last named by a send.
REF_TIMES_FILE = "rounds-ref-times.json"
#: How long a preflight line may run, in seconds.
PREFLIGHT_TIMEOUT_S = 60.0
#: How long a ref no send names is kept, in days.
REF_IDLE_DAYS = 14
#: How many lines of a failed preflight line's output its answer gives.
TAIL_LINES = 20
#: How often the output streamer reads the output file, in seconds.
STREAM_POLL_S = 0.2
#: How long the probe waits for stdin's end after its payload, in seconds.
PROBE_EOF_WAIT_S = 10.0
#: How long a run on the main thread waits on stdin before it looks again, in seconds.
_READ_POLL_S = 0.5
#: The exit code of a `serve` ended by its lifeline before its final answer.
INTERRUPTED = 130

_SAFE = re.compile(r"[^A-Za-z0-9._-]")


def fix_line(host: str) -> str:
    """What fixes a far side that can't serve a request: its words, with its host."""
    return f"update Moxi on {host}, or repair its environment there"


def host_name() -> str:
    """This machine's name, as it gives it."""
    return socket.gethostname()


def lanes_dir(env=None) -> Path:
    """The lanes' folder in rounds' cache folder."""
    return paths.cache_dir(env) / LANES_DIR


def repo_dir(sender: str, repo: str, env=None) -> Path:
    """The bare repository kept for the sending machine `sender` and the project
    `repo`, each name with every character outside letters, digits, `.`, `_` and `-`
    written `_`."""
    return (lanes_dir(env) / REPOS_DIR / (_SAFE.sub("_", sender) or "_")
            / f"{_SAFE.sub('_', repo) or '_'}.git")


# -- the lifeline and the pipes ----------------------------------------------------------

def _interrupt_main() -> None:
    """Raise `KeyboardInterrupt` in the main thread, even one blocked in a system call:
    SIGINT to that thread on POSIX, `_thread.interrupt_main` on Windows."""
    if _WINDOWS:
        import _thread

        _thread.interrupt_main()
        return
    import signal

    signal.pthread_kill(threading.main_thread().ident, signal.SIGINT)


class _Lifeline:
    """What stdin's end does: interrupts the main thread once, unless `disarm` ran
    first. `closed` says whether stdin has ended."""

    def __init__(self, *, armed: bool = True, interrupt: Callable[[], None] = _interrupt_main):
        self._lock = threading.Lock()
        self._armed = armed
        self._interrupt = interrupt
        self.closed = threading.Event()

    def on_close(self) -> None:
        with self._lock:
            self.closed.set()
            if self._armed:
                self._armed = False
                self._interrupt()

    def disarm(self) -> None:
        """From now on stdin's end does nothing."""
        with self._lock:
            self._armed = False


class _Stdin:
    """The process's stdin, read by one thread, the only reader of it. `read(n)` gives
    the main thread up to `n` bytes, `b""` at the end. At the end the thread calls
    `on_end`."""

    def __init__(self, fd: int, on_end: Callable[[], None]):
        self._fd = fd
        self._on_end = on_end
        self._chunks: queue.Queue = queue.Queue()
        self._buffer = b""
        self._ended = False
        thread = threading.Thread(target=self._pump, name="rounds-serve-stdin", daemon=True)
        thread.start()

    def _pump(self) -> None:
        while True:
            try:
                chunk = os.read(self._fd, frames.CHUNK_BYTES)
            except OSError:
                chunk = b""
            if not chunk:
                break
            self._chunks.put(chunk)
        self._chunks.put(b"")
        self._on_end()

    def read(self, n: int) -> bytes:
        while not self._buffer:
            if self._ended:
                return b""
            try:
                chunk = self._chunks.get(timeout=_READ_POLL_S)
            except queue.Empty:
                continue
            if not chunk:
                self._ended = True
                return b""
            self._buffer = chunk
        found, self._buffer = self._buffer[:n], self._buffer[n:]
        return found


def _fd_writer(fd: int) -> Callable[[bytes], None]:
    def write(data: bytes) -> None:
        view = memoryview(data)
        while view:
            view = view[os.write(fd, view):]

    return write


def _binary(fd: int) -> None:
    """On Windows, read and write the descriptor `fd` as bytes, with no newline
    translation."""
    if _WINDOWS:
        import msvcrt

        msvcrt.setmode(fd, os.O_BINARY)


# -- the request -------------------------------------------------------------------------

@dataclass(frozen=True)
class Request:
    """What a near side asks: the build (`round`, `stage`, `step`, `repo`, `branch`),
    the commit to run (`tip`), the target's name (`into`) and head (`into_head`, None
    when the near side has none), the `argv` to run, its `timeout_s`, its `memory_mib`,
    the `preflight` lines, whether to `wait` for the far lock, the near machine's name
    (`from_host`) and the `shared_lock` to take with the place (None for none)."""

    round: str
    stage: str
    step: str
    repo: str
    branch: str
    tip: str
    into: str
    into_head: str | None
    argv: tuple[str, ...]
    timeout_s: float
    memory_mib: int | None
    preflight: tuple[str, ...]
    wait: bool
    from_host: str
    shared_lock: str | None = None


class Unreadable(Exception):
    """A request `serve` can't read: `words` says what is wrong with it."""

    def __init__(self, words: str):
        super().__init__(words)
        self.words = words


def _text(found: Mapping, key: str, *, empty: bool = False) -> str:
    value = found.get(key)
    if not isinstance(value, str) or (not empty and not value):
        raise Unreadable(f"its {key} is {value!r}, not text")
    return value


def read_request(found) -> Request:
    """The request a near side sent, `found` being its message read as JSON. Raises
    `Unreadable` naming the first field that doesn't read."""
    if not isinstance(found, Mapping):
        raise Unreadable("it is not a table")
    argv = found.get("argv")
    if not isinstance(argv, list) or not argv or not all(isinstance(one, str)
                                                         for one in argv):
        raise Unreadable(f"its argv is {argv!r}, not a list of words")
    timeout_s = found.get("timeout_s")
    if isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float)) or timeout_s <= 0:
        raise Unreadable(f"its timeout_s is {timeout_s!r}, not a number above 0")
    memory_mib = found.get("memory_mib")
    if memory_mib is not None and (isinstance(memory_mib, bool)
                                   or not isinstance(memory_mib, int)):
        raise Unreadable(f"its memory_mib is {memory_mib!r}, not a whole number")
    preflight = found.get("preflight", [])
    if not isinstance(preflight, list) or not all(isinstance(one, str) for one in preflight):
        raise Unreadable(f"its preflight is {preflight!r}, not a list of lines")
    into_head = found.get("into_head")
    shared_lock = found.get("shared_lock")
    for key, value in (("into_head", into_head), ("shared_lock", shared_lock)):
        if value is not None and (not isinstance(value, str) or not value):
            raise Unreadable(f"its {key} is {value!r}, not text")
    if not isinstance(found.get("wait"), bool):
        raise Unreadable(f"its wait is {found.get('wait')!r}, not true or false")
    tip = _text(found, "tip")
    if not gitreads.is_id(tip) or (into_head is not None and not gitreads.is_id(into_head)):
        raise Unreadable("its tip or into_head is not a full commit id")
    return Request(
        round=_text(found, "round"), stage=_text(found, "stage"), step=_text(found, "step"),
        repo=_text(found, "repo"), branch=_text(found, "branch"), tip=tip,
        into=_text(found, "into"), into_head=into_head, argv=tuple(argv),
        timeout_s=float(timeout_s), memory_mib=memory_mib, preflight=tuple(preflight),
        wait=found["wait"], from_host=_text(found, "from_host"), shared_lock=shared_lock)


def _version_words(found) -> str | None:
    """Why a request's version is not this one's, or None when it is."""
    version = found.get("version") if isinstance(found, Mapping) else None
    if version == frames.VERSION:
        return None
    return (f"the request is of version {version!r}, and this Moxi on {host_name()} "
            f"speaks version {frames.VERSION}")


# -- the bare repository -----------------------------------------------------------------

def _git(args: list[str], *, cwd: Path | None = None, timeout_s: float = 600.0) -> str:
    """Run git with `args`, with this process's environment less every `GIT_` variable;
    its stdout. Raises `RoundsError` with git's words when it fails."""
    try:
        done = proc.run(["git", *args], timeout_s=timeout_s, env=gitreads.git_env(),
                        cwd=cwd)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RoundsError(f"git {shlex.join(args)} could not run: {exc}",
                          fix_line(host_name())) from None
    if done.returncode != 0:
        said = (done.stderr or b"").decode(errors="replace").strip()
        raise RoundsError(f"git {shlex.join(args)} failed: {said or done.returncode}",
                          fix_line(host_name()))
    return (done.stdout or b"").decode(errors="replace")


def held_refs(bare: Path) -> list[str]:
    """The commits the bare repository's refs name, each once, in order."""
    if not bare.is_dir():
        return []
    said = _git(["--git-dir", str(bare), "for-each-ref", "--format=%(objectname)"])
    return list(dict.fromkeys(line.strip() for line in said.splitlines() if line.strip()))


def ensure_bare(bare: Path) -> None:
    """Make the bare repository when it isn't there."""
    if not (bare / "HEAD").is_file():
        bare.mkdir(parents=True, exist_ok=True)
        _git(["init", "--bare", "--quiet", str(bare)])


def _ref_times(bare: Path) -> dict[str, float]:
    try:
        found = json.loads((bare / REF_TIMES_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {str(key): float(value) for key, value in found.items()
            if isinstance(value, (int, float))} if isinstance(found, dict) else {}


def prune_refs(bare: Path, named: list[str], *, now_s: float) -> list[str]:
    """Note that the refs `named` were named by a send at `now_s`, and drop every other
    ref of the bare repository no send has named for `REF_IDLE_DAYS`; a ref with no
    time noted is noted at `now_s`. The refs dropped."""
    times = _ref_times(bare)
    said = _git(["--git-dir", str(bare), "for-each-ref", "--format=%(refname)"])
    refs = [line.strip() for line in said.splitlines() if line.strip()]
    dropped = []
    kept: dict[str, float] = {}
    for ref in refs:
        when = now_s if ref in named else times.get(ref, now_s)
        if now_s - when >= REF_IDLE_DAYS * 86400:
            _git(["--git-dir", str(bare), "update-ref", "-d", ref])
            dropped.append(ref)
        else:
            kept[ref] = when
    files.atomic_write(bare / REF_TIMES_FILE, json.dumps(kept, sort_keys=True))
    return dropped


def take_bundle(bare: Path, bundle: Path | None, request: Request, *,
                now_s: float) -> None:
    """Fetch `bundle` (None when the near side sent none) into the bare repository,
    then set the build's branch to its tip and the target's branch to its head there,
    and prune idle refs. Raises `RoundsError` when the tip or the target's head is not
    in it afterwards."""
    if bundle is not None:
        _git(["--git-dir", str(bare), "fetch", "--quiet", str(bundle),
              "+refs/heads/*:refs/heads/*"])
    named = [f"refs/heads/{request.branch}"]
    pairs = [(request.branch, request.tip)]
    if request.into_head is not None and request.into != request.branch:
        named.append(f"refs/heads/{request.into}")
        pairs.append((request.into, request.into_head))
    for branch, commit in pairs:
        try:
            _git(["--git-dir", str(bare), "cat-file", "-e", f"{commit}^{{commit}}"])
        except RoundsError:
            raise RoundsError(f"the commit {commit[:12]} of {branch} is not in what was "
                              "sent", "run the check again; the near side sends what "
                              "the far side lacks") from None
        _git(["--git-dir", str(bare), "update-ref", f"refs/heads/{branch}", commit])
    prune_refs(bare, named, now_s=now_s)


# -- the run tree and the sweep ----------------------------------------------------------

def trees_dir(env=None) -> Path:
    """The run trees' folder."""
    return lanes_dir(env) / TREES_DIR


def tree_lock(tree: Path) -> Path:
    """The lock file a run tree's `serve` holds while it runs: `<id>.lock` beside it."""
    return tree.with_name(f"{tree.name}.lock")


def tree_log(tree: Path) -> Path:
    """The output file of a run tree's command: `<id>.log` beside it."""
    return tree.with_name(f"{tree.name}.log")


def _remove_tree(tree: Path) -> None:
    """Remove the tree and its output file; what can't be removed now (a file open on
    Windows) is left for a later sweep."""
    shutil.rmtree(tree, ignore_errors=True)
    for path in (tree_log(tree),):
        try:
            path.unlink()
        except OSError:
            pass


def _runs_live(lock_path: Path, at_once: int) -> set[str]:
    """The trees whose run still lives by this machine's check lock: each holder record
    naming a `tree_dir` whose run reads alive (`check_line.run_alive`), or whose `serve`
    is alive."""
    found = set()
    for k in check_line.places(lock_path, at_once):
        record = check_line.read_holder(check_line.slot_path(lock_path, k))
        if record is None or not isinstance(record.get("tree_dir"), str):
            continue
        if check_line.holder(check_line.slot_path(lock_path, k)) is not None or \
                check_line.run_alive(record):
            found.add(record["tree_dir"])
    return found


def sweep(trees: Path, lock_path: Path, *, at_once: int, own: Path | None = None) -> list[Path]:
    """Remove each run tree in `trees` but `own` whose `serve` is gone (its lock file
    can be taken) and whose run no longer lives (`_runs_live`), with its output file and
    lock file; the trees removed. One that fails either test is left for a later sweep."""
    try:
        names = {entry.name.split(".", 1)[0] for entry in trees.iterdir()}
    except FileNotFoundError:
        return []
    live = _runs_live(lock_path, at_once)
    removed = []
    for name in sorted(names):
        tree = trees / name
        if own is not None and tree == own:
            continue
        if str(tree) in live:
            continue
        try:
            with files.lock(tree_lock(tree), timeout_s=0):
                _remove_tree(tree)
        except files.LockTimeout:
            continue
        check_line.remove(tree_lock(tree))
        removed.append(tree)
    return removed


def make_tree(bare: Path, tree: Path, request: Request) -> None:
    """The run's tree: a clone of the bare repository sharing its objects, the build's
    branch checked out at the tip, and the target's branch at its head."""
    _git(["clone", "--quiet", "--shared", "--no-checkout", str(bare), str(tree)])
    _git(["-C", str(tree), "checkout", "--quiet", "-B", request.branch, request.tip])
    if request.into_head is not None and request.into != request.branch:
        _git(["-C", str(tree), "branch", "--force", request.into, request.into_head])


def _brought_path(tree: Path, name: object) -> Path | None:
    """Where in `tree` a brought file's path `name` goes; None for a path that is not
    relative, or leaves the tree."""
    if not isinstance(name, str) or not name:
        return None
    parts = PurePosixPath(name.replace("\\", "/")).parts
    if not parts or parts[0] in ("/", "") or ".." in parts or ":" in parts[0]:
        return None
    return tree.joinpath(*parts)


def write_brought(tree: Path, brought: list[tuple[dict, bytes]]) -> str | None:
    """Write each brought file into the tree, mode 0600, once its bytes match the
    `sha256` sent; why the run is not ready (the first file that doesn't match or can't
    go there), or None."""
    for meta, data in brought:
        where = _brought_path(tree, meta.get("path"))
        if where is None:
            return f"the brought file {meta.get('path')!r} is not a path in the repo"
        if hashlib.sha256(data).hexdigest() != meta.get("sha256"):
            return (f"the brought file {meta.get('path')} arrived with other bytes than "
                    "were sent (its sha256 differs)")
        files.atomic_write(where, data, mode=0o600)
    return None


def _tail(text: str) -> str:
    return "\n".join(text.splitlines()[-TAIL_LINES:])


def preflight(lines, cwd: Path) -> dict | None:
    """Run each line in `cwd`, split as a shell would, through `proc.run` with
    `PREFLIGHT_TIMEOUT_S`; for the first that fails, what failed (`reason`, `line`,
    `exit_code`, `tail`), else None."""
    for line in lines:
        try:
            done = proc.run(shlex.split(line), timeout_s=PREFLIGHT_TIMEOUT_S,
                            env=gitreads.git_env(), cwd=cwd)
        except subprocess.TimeoutExpired as exc:
            said = ((exc.output or b"") + (exc.stderr or b"")).decode(errors="replace")
            return {"reason": f"the preflight line {line!r} ran past "
                              f"{PREFLIGHT_TIMEOUT_S:g} s", "line": line,
                    "exit_code": None, "tail": _tail(said)}
        except (OSError, ValueError) as exc:
            return {"reason": f"the preflight line {line!r} could not start: {exc}",
                    "line": line, "exit_code": None, "tail": ""}
        if done.returncode != 0:
            said = ((done.stdout or b"") + (done.stderr or b"")).decode(errors="replace")
            return {"reason": f"the preflight line {line!r} exited {done.returncode}",
                    "line": line, "exit_code": done.returncode, "tail": _tail(said)}
    return None


# -- streaming ---------------------------------------------------------------------------

class _Streamer:
    """A thread that sends the output file as `frames.OUT` as it grows, until `stop`,
    which sends what is left."""

    def __init__(self, path: Path, out: frames.Writer):
        self._path = path
        self._out = out
        self._done = threading.Event()
        self._at = 0
        self._thread = threading.Thread(target=self._loop, name="rounds-serve-output",
                                        daemon=True)
        self._thread.start()

    def _send_new(self) -> None:
        try:
            with open(self._path, "rb") as fh:
                fh.seek(self._at)
                while chunk := fh.read(frames.CHUNK_BYTES):
                    self._at += len(chunk)
                    self._out.send(frames.OUT, chunk)
        except FileNotFoundError:
            pass

    def _loop(self) -> None:
        while not self._done.wait(STREAM_POLL_S):
            self._send_new()

    def stop(self) -> None:
        self._done.set()
        self._thread.join()
        self._send_new()


# -- serve -------------------------------------------------------------------------------

class _Answered(Exception):
    """The session's final answer is sent; `code` is serve's exit code."""

    def __init__(self, code: int):
        super().__init__(code)
        self.code = code


class _Session:
    def __init__(self, reader: frames.Reader, out: frames.Writer, lifeline: _Lifeline, *,
                 env, settings, clock: Callable[[], float]):
        self.reader = reader
        self.out = out
        self.lifeline = lifeline
        self.env = env
        self.settings = settings
        self.clock = clock

    def final(self, kind: str, value: Mapping, *, code: int = 0) -> _Answered:
        """Disarm the lifeline, then send the final answer; what to raise to end."""
        self.lifeline.disarm()
        self.out.send_json(kind, dict(value))
        return _Answered(code)

    def refuse(self, reason: str, fix: str | None = None) -> _Answered:
        return self.final(frames.REFUSED, {"reason": reason,
                                           "fix": fix or fix_line(host_name())},
                          code=1)

    def expect(self, kind: str) -> bytes:
        """The next frame's payload, which must be of `kind`."""
        found = self.reader.frame()
        if found is None:
            raise self.refuse(f"the session ended before its {kind} frame")
        if found[0] != kind:
            raise self.refuse(f"a {found[0]} frame came where its {kind} frame was due")
        return found[1]

    def request(self) -> Request:
        payload = self.expect(frames.REQUEST)
        try:
            found = frames.as_json(payload)
        except frames.FrameError as exc:
            raise self.refuse(f"the request can't be read: {exc}") from None
        words = _version_words(found)
        if words is not None:
            raise self.refuse(words)
        try:
            return read_request(found)
        except Unreadable as exc:
            raise self.refuse(f"the request can't be read: {exc.words}") from None

    def run(self) -> int:
        request = self.request()
        trees = trees_dir(self.env)
        trees.mkdir(parents=True, exist_ok=True)
        tree = trees / secrets.token_hex(8)
        lock_path = checking.lock_file(self.env)
        at_once = checking.lock_setting(self.settings, "at_once")
        with ExitStack() as stack:
            # Undone last first: the place frees, the tree goes, then its lock.
            stack.callback(check_line.remove, tree_lock(tree))
            stack.enter_context(files.lock(tree_lock(tree), timeout_s=0))
            stack.callback(_remove_tree, tree)
            taken = self.place(stack, request, lock_path, tree, at_once)
            self.out.send_json(frames.PLACED, checking.lock_fields(taken))
            if request.shared_lock is not None:
                self.shared(stack, request)
            bare = repo_dir(request.from_host, request.repo, self.env)
            brought = self.receive(bare, request)
            sweep(trees, lock_path, at_once=at_once, own=tree)
            try:
                with files.lock(bare.with_name(f"{bare.name}.lock")):
                    make_tree(bare, tree, request)
            except RoundsError as exc:
                raise self.refuse(exc.reason, exc.fix) from None
            why = write_brought(tree, brought)
            if why is not None:
                raise self.final(frames.NOT_READY, {"reason": why}, code=1)
            failed = preflight(request.preflight, tree)
            if failed is not None:
                raise self.final(frames.NOT_READY, failed, code=1)
            argv = checking.fill(request.argv, {"root": str(tree)})
            self.out.send_json(frames.STARTED, {"argv": argv})
            streamer = _Streamer(tree_log(tree), self.out)
            try:
                code, duration_s = checking.run_held(
                    taken, argv, cwd=tree, output=tree_log(tree),
                    timeout_s=request.timeout_s, fix=fix_line(host_name()))
            except RoundsError as exc:
                streamer.stop()
                raise self.refuse(exc.reason, exc.fix) from None
            streamer.stop()
            raise self.final(frames.RESULT, {
                "exit_code": code, "duration_s": duration_s, "host": host_name(),
                **checking.lock_fields(taken)})

    def place(self, stack: ExitStack, request: Request, lock_path: Path, tree: Path,
              at_once: int) -> check_line.Taken:
        """Take a place of this machine's check lock into `stack`, or answer busy."""
        fields = {**check_line.me(), "round": request.round, "stage": request.stage,
                  "step": request.step, "repo": request.repo,
                  "from_host": request.from_host, "tree_dir": str(tree)}
        try:
            return stack.enter_context(check_line.slot(
                lock_path, fields, memory_mib=request.memory_mib, at_once=at_once,
                need_mib=checking.lock_setting(self.settings, "need_mib"),
                wait=request.wait, say=self.wait_line))
        except check_line.NotFree as exc:
            raise self.final(frames.BUSY, {"reason": exc.words}, code=1) from None

    def wait_line(self, line: str) -> None:
        self.out.send_json(frames.WAIT, {"line": line})

    def shared(self, stack: ExitStack, request: Request) -> None:
        """Take the shared lock into `stack`: at once, or answer busy without `wait`;
        else waiting, saying so once."""
        path = Path(os.path.expanduser(request.shared_lock))
        said = False
        while True:
            try:
                stack.enter_context(files.lock(path, timeout_s=0))
                break
            except files.LockTimeout as exc:
                words = (f"the shared lock {path} is held by "
                         f"{'pid ' + str(exc.holder) if exc.holder else 'another process'}"
                         ", which this machine's own scheduled work holds")
                if not request.wait:
                    raise self.final(frames.BUSY, {"reason": words}, code=1) from None
                if not said:
                    self.wait_line(f"waiting for {words}")
                    said = True
                time.sleep(check_line.LINE_POLL_S)
            except OSError as exc:
                raise self.refuse(f"the shared lock {path} can't be taken: {exc}",
                                  "fix shared_lock in the lane's settings on the near "
                                  "side") from None
        if said:
            self.wait_line(f"got the shared lock {path}")

    def receive(self, bare: Path, request: Request) -> list[tuple[dict, bytes]]:
        """Send the refs, take the bundle into the bare repository, and read the brought
        files."""
        lock = bare.with_name(f"{bare.name}.lock")
        bare.parent.mkdir(parents=True, exist_ok=True)
        with files.lock(lock):
            try:
                ensure_bare(bare)
                refs = held_refs(bare)
            except RoundsError as exc:
                raise self.refuse(exc.reason, exc.fix) from None
            self.out.send_json(frames.REFS, {"refs": refs})
            header = self.reader.header()
            if header is None or header[0] != frames.BUNDLE:
                raise self.refuse("the bundle frame did not come after the refs")
            bundle = None
            with ExitStack() as gone:
                if header[1]:
                    bundle = bare.with_name(f"{bare.name}.{secrets.token_hex(4)}.bundle")
                    gone.callback(check_line.remove, bundle)
                    files.atomic_write(bundle, self.reader.payload(header[1]))
                try:
                    take_bundle(bare, bundle, request, now_s=self.clock())
                except RoundsError as exc:
                    raise self.refuse(exc.reason, exc.fix) from None
        brought = []
        while True:
            found = self.reader.frame()
            if found is None:
                raise self.refuse("the session ended before its files-end frame")
            kind, payload = found
            if kind == frames.FILES_END:
                return brought
            if kind != frames.FILE:
                raise self.refuse(f"a {kind} frame came among the brought files")
            brought.append(frames.read_file_payload(payload))


def serve(*, fd_in: int = 0, fd_out: int = 1, env=None, settings=None,
          clock: Callable[[], float] = time.time,
          interrupt: Callable[[], None] = _interrupt_main) -> int:
    """Serve one run of a lane over the descriptors `fd_in` and `fd_out`, by the steps
    above; the exit code: 0 once the final answer is sent, 1 for a refusal, a busy or
    not-ready answer, `INTERRUPTED` when stdin ended before the final answer."""
    _binary(fd_in)
    _binary(fd_out)
    lifeline = _Lifeline(interrupt=interrupt)
    out = frames.Writer(_fd_writer(fd_out), drop=True)
    reader = frames.Reader(_Stdin(fd_in, lifeline.on_close).read)
    try:
        settings = own.load() if settings is None else settings
    except ConfigError as exc:
        lifeline.disarm()
        out.send_json(frames.REFUSED, {"reason": f"rounds' settings on {host_name()} "
                                                 f"can't be read: {exc}",
                                       "fix": fix_line(host_name())})
        return 1
    session = _Session(reader, out, lifeline, env=env, settings=settings, clock=clock)
    try:
        session.run()
    except _Answered as exc:
        return exc.code
    except frames.FrameError as exc:
        return session.refuse(f"the session's frames can't be read: {exc}").code
    except KeyboardInterrupt:
        return INTERRUPTED
    return 0


# -- the probe ---------------------------------------------------------------------------

def _lock_line(lock_path: Path, at_once: int) -> dict:
    """The check lock's holders and line, as the probe tells them."""
    holders = []
    for k in check_line.places(lock_path, at_once):
        path = check_line.slot_path(lock_path, k)
        record = check_line.holder(path) or check_line.orphan(path)
        if record is not None:
            holders.append({"check_lock": k, **{key: record.get(key) for key in (
                "round", "stage", "repo", "from_host", "since")}})
    waiting = [one for one in check_line.tickets(lock_path) if not one.dead]
    return {"at_once": at_once, "holders": holders, "waiting": len(waiting)}


def probe(*, fd_in: int = 0, fd_out: int = 1, env=None, settings=None) -> int:
    """Answer the doctor's probe over the descriptors `fd_in` and `fd_out`: read the
    request (the version and `preflight`, each project's lines by key) and the payload,
    wait up to `PROBE_EOF_WAIT_S` for stdin's end, and answer `frames.PROBED`."""
    _binary(fd_in)
    _binary(fd_out)
    lifeline = _Lifeline(armed=False)
    out = frames.Writer(_fd_writer(fd_out), drop=True)
    reader = frames.Reader(_Stdin(fd_in, lifeline.on_close).read)
    try:
        found = reader.frame()
        if found is None or found[0] != frames.REQUEST:
            raise frames.FrameError("the probe's request did not come first")
        request = frames.as_json(found[1])
        words = _version_words(request)
        if words is not None:
            out.send_json(frames.REFUSED, {"reason": words, "fix": fix_line(host_name())})
            return 1
        payload = reader.frame()
        if payload is None or payload[0] != frames.PROBE:
            raise frames.FrameError("the probe's payload did not come after its request")
    except frames.FrameError as exc:
        out.send_json(frames.REFUSED, {"reason": f"the probe can't be read: {exc}",
                                       "fix": fix_line(host_name())})
        return 1
    try:
        eof = lifeline.closed.wait(PROBE_EOF_WAIT_S) and reader.header() is None
    except frames.FrameError:
        eof = False
    try:
        settings = own.load() if settings is None else settings
    except ConfigError as exc:
        out.send_json(frames.REFUSED, {"reason": f"rounds' settings on {host_name()} "
                                                 f"can't be read: {exc}",
                                       "fix": fix_line(host_name())})
        return 1
    at_once = checking.lock_setting(settings, "at_once")
    try:
        git = _git(["--version"]).strip()
    except RoundsError:
        git = None
    lines = request.get("preflight") if isinstance(request.get("preflight"), dict) else {}
    results = {}
    with tempfile.TemporaryDirectory(prefix="rounds-probe-") as scratch:
        for project, project_lines in lines.items():
            if isinstance(project_lines, list):
                results[project] = preflight([str(one) for one in project_lines],
                                             Path(scratch))
    out.send_json(frames.PROBED, {
        "length": len(payload[1]), "sha256": hashlib.sha256(payload[1]).hexdigest(),
        "eof": bool(eof), "version": frames.VERSION, "host": host_name(),
        "cpus": os.cpu_count(), "available_mib": check_line.available_mib(), "git": git,
        "check_lock": _lock_line(checking.lock_file(env), at_once),
        "preflight": results})
    return 0


def main(*, probe_only: bool = False) -> int:
    """`moxi rounds serve`, or with `probe_only` `moxi rounds serve --probe`, on this
    process's stdin and stdout."""
    return probe() if probe_only else serve()


__all__ = ["LANES_DIR", "REPOS_DIR", "TREES_DIR", "REF_TIMES_FILE", "PREFLIGHT_TIMEOUT_S",
           "REF_IDLE_DAYS", "TAIL_LINES", "STREAM_POLL_S", "PROBE_EOF_WAIT_S", "INTERRUPTED",
           "fix_line", "host_name", "lanes_dir", "repo_dir", "Request", "Unreadable",
           "read_request", "held_refs", "ensure_bare", "prune_refs", "take_bundle",
           "trees_dir", "tree_lock", "tree_log", "sweep", "make_tree", "write_brought",
           "preflight", "serve", "probe", "main"]

"""A new Claude adapter for a session whose adapter is gone while `claude` runs.

The adapter `moxi comms claude` forked (`session.launch`) lives as long as `claude`; one
that dies first (a crash, a kill, the OOM killer) leaves its claim file under a live
`claude`, with a socket that answers no one (`claimfile.adapter_gone`). An MCP shim that
Claude Code spawned again bound its session again from its claim file. This does the same
for the adapter: everything a new one needs outlives the old one.

    claude          the claim file's `tree` root: its pid and start time. A pidfd taken
                    on that pid is the adapter's watch once the start time still matches,
                    so a reused pid cannot stand in for it
    the identity    the claim file, which the new shim reads (`ClaudeShim._make_holder`),
                    and the session's stamps
    the tool token  the session's environment, which turns hands comms' listener
    the endpoint    `turns.find(pid=<claude>)`, as any adapter finds it

Two callers start one, through `respawn_adapter`:

    the daemon      at once. A binding whose bind named a claim ends (its connection
                    dropped), and the daemon's `Respawner` reads the claim file: for a
                    `claude-shim` file it reads the claim socket until nothing answers
                    (about `GONE_WAIT_S`), and gives up when it keeps answering (a live
                    adapter that lost its connection binds again by itself), when the
                    file is gone or when `claude` has ended. Once the daemon serves after
                    a start, it sweeps the files whose adapter is gone the same way. The
                    session's environment is `claude`'s own (`session_env`). At most
                    `RESPAWN_CAP` of these per claim key in `RESPAWN_WINDOW_S`, counted
                    in the daemon's memory; past that it logs once and leaves the key.
    the listener    comms' listener, at the session's start and prompt
                    (`listener.respawn_gone_adapter`): the fallback, which finds the
                    adapter alive once the daemon has replaced it.

`respawn_adapter` starts `python -I -m moxi_comms.adapters.respawn` in a session of its
own, in the session's runtime directory: isolated, so neither the caller's working
directory nor a PYTHONPATH or PYTHONHOME in the session's environment can load code into
it. That process forks the adapter and leaves at once, so the adapter is nobody's child.
The caller waits at most `wait_s` for that process to leave and the adapter's word
(`adapters.word`) together. The new adapter holds a lock beside the claim file,
`respawn-<key>.lock`, for its whole life (`moxi.files.lock`): a second one, started by a
second caller while the first binds, finds it held, says `word.STARTING` and ends before
it touches the claim file or its socket.

**The daemon's cgroup.** A daemon that runs in a systemd service would start the adapter
inside the service's cgroup, where a restart of the daemon kills it with the rest of the
unit. So when this process's own cgroup is a `.service` (`in_service`, read from
`/proc/self/cgroup` rather than `INVOCATION_ID`: that variable is inherited by every
process a service starts, wherever it runs now, and the cgroup is what a stop kills) and
`systemd-run` is found, the starter runs under `systemd-run --user --scope --quiet
--collect` in a scope of its own named for the claim key (`unit_name`). A scope's command
is run by `systemd-run` itself, so it keeps the session's environment, the working
directory and the word's pipe; `with_user_manager` fills in the variables that reach the
user manager when the session's environment lacks them. When `systemd-run` fails before
the starter runs (no user manager reached, say), its stderr, cut to
`SYSTEMD_RUN_STDERR_CHARS`, is logged as the reason, and the starter is forked as on a
box without systemd. That adapter is in the daemon's cgroup, so a restart of the daemon's
service ends it, and the sweep at the daemon's next start replaces it; the log line says
so. The listener's starter is already in the session's cgroup and runs as it is.

**The session is told.** An adapter the daemon started leaves a one-shot note in the
session's runtime directory (`NOTE`) with the line its word makes
(`listener.respawn_line`, the time the old one stopped in it). The listener prints it
once, at the session's next start or prompt, and removes it (`listener.take_note`).

**Wake.** Nothing more is needed for held mail: the new adapter's bind releases the
name's waiting rows on a passing read (`Daemon._on_register`), and its first look at an
idle session is a release point (`ClaudeAdapter.look` sends a `look`, which
`Daemon._on_look` releases on), so the queued mail reaches the adapter, which hands it to
the idle session with `deliver`, and that starts a turn with no prompt.
"""

from __future__ import annotations

import contextlib
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

log = logging.getLogger("comms.claude.respawn")

#: The claim file kind of the adapter `moxi comms claude` forks.
CLAUDE_KIND = "claude-shim"
#: The lock a respawned adapter holds while it lives.
LOCK_PREFIX = "respawn-"
#: The one-shot note an adapter the daemon started leaves in the session's runtime dir.
NOTE = "respawn-note.txt"
#: How long the daemon reads a dropped adapter's claim socket for it to stop answering,
#: and how often.
GONE_WAIT_S = 10.0
GONE_POLL_S = 0.25
#: How long the daemon waits for a new adapter's word, on its own thread.
WORD_WAIT_S = 15.0
#: The most daemon respawns of one claim key in `RESPAWN_WINDOW_S`.
RESPAWN_CAP = 3
RESPAWN_WINDOW_S = 600.0
#: The scope unit a daemon in a systemd service starts a new adapter in: this, then the
#: claim key made unit-safe (`unit_name`).
UNIT_PREFIX = "moxi-comms-adapter-"
#: The variables `systemd-run --user` reaches the user manager with.
USER_MANAGER_ENV = ("XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS")
#: The most of `systemd-run`'s stderr a failed scope's log line carries.
SYSTEMD_RUN_STDERR_CHARS = 300
#: `Respawner`'s default: find `systemd-run` when this process runs in a service.
FIND = object()


class CannotRespawn(Exception):
    """No new adapter can take this session: its text says why."""


def lock_path(key: str, env=None) -> Path:
    """The lock a respawned adapter for the session keyed `key` holds, beside its claim
    file."""
    from ..claimfile import claim_dir

    return claim_dir(env) / f"{LOCK_PREFIX}{key}.lock"


def claude_root(record: dict):
    """The record's live `tree` root (the `claude` process), or None."""
    from .. import claimfile
    from ..membership import DEFAULT_PROC, TREE

    for root in claimfile.roots_of(record):
        if root.reach == TREE and DEFAULT_PROC.alive(root.pid, root.start):
            return root
    return None


def respawn_adapter(env: dict, key: str, record: dict, *, wait_s: float,
                    stopped_at: str | None = None, systemd_run: str | None = None):
    """Start a new adapter for the session keyed `key`, whose claim file was read as
    the record given, with the session's environment `env`, and wait at most `wait_s`
    seconds, for the starting process to leave and the adapter's word together.

    `stopped_at` (the daemon's) asks the adapter for the session's note (`NOTE`): the
    old one's stop as epoch seconds, or `-` when it isn't known. `systemd_run`, the
    path of `systemd-run`, starts it in a scope of its own (`starter_argv`); when
    `systemd-run` fails before the starter runs, the reason is logged and the starter is
    forked instead, in what is left of `wait_s`.

    Answers the word, `(code, reason)`, `word.STARTING` among them when a new adapter
    already serves the session; None when it didn't come in time (the adapter goes on
    binding, or the starting process goes on to start it). Raises `CannotRespawn` when
    none can be started."""
    if record.get("kind") != CLAUDE_KIND:
        raise CannotRespawn("its adapter was not one `moxi comms claude` started")
    root = claude_root(record)
    if root is None:
        raise CannotRespawn("its claude process could not be found")
    if not sys.platform.startswith("linux"):
        raise CannotRespawn("this system has no process handle to watch claude with")
    if key in ("", ".", "..") or "/" in key:
        raise CannotRespawn(f"its claim file's key {key!r} names no session directory")
    deadline = time.monotonic() + wait_s
    if systemd_run is not None:
        word, failed = _start(with_user_manager(env), key, root, deadline,
                              stopped_at=stopped_at, systemd_run=systemd_run)
        if failed is None:
            return word
        log.warning("systemd-run could not start the new comms adapter of claim %s in a "
                    "scope of its own (%s); forking it in this daemon's cgroup instead, "
                    "where a restart of this daemon's service ends it and the sweep at "
                    "the daemon's next start replaces it", key, failed)
    word, _failed = _start(env, key, root, deadline, stopped_at=stopped_at)
    return word


def _start(env: dict, key: str, root, deadline: float, *, stopped_at: str | None,
           systemd_run: str | None = None):
    """Run the starter once and wait until `deadline` for it to leave and for the word:
    `(word, None)`, or, under `systemd_run`, `(None, why)` when `systemd-run` failed
    before the starter ran (it could not be run, or it ended nonzero with no word),
    `why` being its exit and the start of its stderr."""
    from .word import FAILED, await_word

    r, w = os.pipe()
    err = tempfile.TemporaryFile() if systemd_run is not None else None
    try:
        argv = starter_argv(key, root, w, stopped_at=stopped_at, systemd_run=systemd_run)
        try:
            starter = subprocess.Popen(
                argv, env=env, cwd=session_dir(key, env), pass_fds=(w,),
                start_new_session=True, stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL if err is None else err)
        except OSError as exc:
            if systemd_run is not None:
                return None, f"it could not be run: {exc.strerror or exc}"
            raise CannotRespawn(f"a new adapter could not be started: {exc}") from exc
        os.close(w)
        w = None
        try:
            starter.wait(max(0.0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            return None, None
        word = await_word(r, max(0.0, deadline - time.monotonic()))
        if err is not None and starter.returncode != 0 and word == (FAILED, ""):
            return None, _stderr_of(err, starter.returncode)
        return word, None
    finally:
        if w is not None:
            os.close(w)
        os.close(r)
        if err is not None:
            err.close()


def _stderr_of(err, code: int) -> str:
    """`exit <code>: <stderr>` from the file `err`, on one line, cut to
    `SYSTEMD_RUN_STDERR_CHARS`."""
    err.seek(0)
    text = " ".join(err.read(4 * SYSTEMD_RUN_STDERR_CHARS).decode(
        "utf-8", "replace").split())
    if len(text) > SYSTEMD_RUN_STDERR_CHARS:
        text = text[:SYSTEMD_RUN_STDERR_CHARS - 3] + "..."
    return f"exit {code}: {text or 'nothing on stderr'}"


def starter_argv(key: str, root, ready_fd: int, *, stopped_at: str | None = None,
                 systemd_run: str | None = None) -> list[str]:
    """The starter's command line: `python -I -m moxi_comms.adapters.respawn <key>
    <claude pid> <start> <ready fd> [<stopped at>]`, run under `systemd_run --user
    --scope --quiet --collect --unit=<unit_name(key)>` when `systemd_run` is given."""
    argv = [sys.executable, "-I", "-m", __name__, key, str(root.pid), root.start,
            str(ready_fd)]
    if stopped_at is not None:
        argv.append(stopped_at)
    if systemd_run is None:
        return argv
    return [systemd_run, "--user", "--scope", "--quiet", "--collect",
            f"--unit={unit_name(key)}", *argv]


def unit_name(key: str) -> str:
    """The scope unit for a new adapter of the session keyed `key`: `UNIT_PREFIX` and
    the key, each byte outside a unit name's letters, digits, `_`, `.` and `-` written
    `\\xNN` as `systemd-escape` writes it, and a leading `.` too."""
    out = []
    for i, byte in enumerate(key.encode("utf-8")):
        char = chr(byte)
        if re.fullmatch(r"[A-Za-z0-9_\-]", char) or (char == "." and i > 0):
            out.append(char)
        else:
            out.append(f"\\x{byte:02x}")
    return f"{UNIT_PREFIX}{''.join(out)}.scope"


def with_user_manager(env: dict) -> dict:
    """`env` with the variables `systemd-run --user` reaches the user manager with
    (`USER_MANAGER_ENV`) taken from this process where `env` has none."""
    out = dict(env)
    for var in USER_MANAGER_ENV:
        if not out.get(var) and os.environ.get(var):
            out[var] = os.environ[var]
    return out


def in_service(cgroup_file: str = "/proc/self/cgroup") -> bool:
    """Whether this process runs in a systemd service: its unified cgroup's last part is
    a `.service`."""
    try:
        lines = Path(cgroup_file).read_text().splitlines()
    except OSError:
        return False
    for line in lines:
        if line.startswith("0::"):
            return line[3:].rstrip("/").rsplit("/", 1)[-1].endswith(".service")
    return False


def session_env(record: dict) -> dict:
    """The session's environment: the one its `claude` (the claim file's `tree` root,
    its start time checked before and after the read) started with, from
    `/proc/<pid>/environ`. Raises `CannotRespawn` unless it carries the identity stamp
    and a claim socket stamp that is the claim file's socket. Its values are never
    logged: it holds the session's tool token."""
    from ..claimhold import CLAIM_SOCKET_ENV
    from ..constants import NAME_ENV
    from ..membership import DEFAULT_PROC

    root = claude_root(record)
    if root is None:
        raise CannotRespawn("its claude process has ended")
    try:
        raw = Path(f"/proc/{root.pid}/environ").read_bytes()
    except OSError as exc:
        raise CannotRespawn(f"claude's environment could not be read: "
                            f"{exc.strerror or exc}") from None
    if not DEFAULT_PROC.alive(root.pid, root.start):
        raise CannotRespawn("its claude process has ended")
    env = {}
    for item in raw.split(b"\0"):
        name, sep, value = item.partition(b"=")
        if sep and name:
            env[name.decode("utf-8", "surrogateescape")] = value.decode(
                "utf-8", "surrogateescape")
    if not env.get(NAME_ENV):
        raise CannotRespawn("claude's environment carries no comms identity stamp")
    if not record.get("socket") or env.get(CLAIM_SOCKET_ENV) != record["socket"]:
        raise CannotRespawn("claude's environment names another claim socket")
    return env


class Respawner:
    """The daemon's side: a new adapter at once for a Claude session whose adapter died
    while `claude` runs (the module's docstring). `stopping` is the daemon's stop event;
    nothing starts once it is set. `systemd_run` is the path the starter runs under, or
    None to fork it; by default `systemd-run` when `in_service`."""

    def __init__(self, stopping: threading.Event, *, systemd_run=FIND,
                 gone_wait_s: float = GONE_WAIT_S, poll_s: float = GONE_POLL_S,
                 word_wait_s: float = WORD_WAIT_S, clock=time.monotonic,
                 start=None):
        if systemd_run is FIND:
            systemd_run = shutil.which("systemd-run") if in_service() else None
        self.systemd_run = systemd_run
        self.stopping = stopping
        self.gone_wait_s, self.poll_s, self.word_wait_s = gone_wait_s, poll_s, word_wait_s
        self.clock = clock
        self._start = start or respawn_adapter
        self._lock = threading.Lock()
        #: claim key -> the clock at each respawn this daemon started inside the window.
        self._recent: dict[str, list[float]] = {}
        #: The keys past the cap whose line was logged.
        self._capped: set[str] = set()
        #: The keys a thread handles now.
        self._busy: set[str] = set()

    def dropped(self, keys) -> list[threading.Thread]:
        """The daemon's bindings whose bind named these claim keys ended: one thread
        each that replaces a gone Claude adapter (`after_drop`). Answers the threads."""
        stopped_at = f"{time.time():.0f}"
        return [self._thread(self.after_drop, key, stopped_at) for key in sorted(keys)]

    def sweep(self) -> threading.Thread:
        """Once the daemon serves after its start: every `claude-shim` file whose adapter
        is gone (`claimfile.gone_records`) is replaced, on one thread."""
        def run() -> None:
            from .. import claimfile

            for key, record in claimfile.gone_records():
                if self.stopping.is_set():
                    return
                if record.get("kind") == CLAUDE_KIND and self._take(key):
                    try:
                        self.replace(key, record, "-")
                    finally:
                        self._give(key)
        return self._thread(run)

    def _thread(self, target, *args) -> threading.Thread:
        t = threading.Thread(target=target, args=args, daemon=True,
                             name="comms-respawn")
        t.start()
        return t

    def _take(self, key: str) -> bool:
        with self._lock:
            if key in self._busy:
                return False
            self._busy.add(key)
            return True

    def _give(self, key: str) -> None:
        with self._lock:
            self._busy.discard(key)

    def after_drop(self, key: str, stopped_at: str) -> object:
        """The binding of the claim keyed `key` dropped: when its file is a
        `claude-shim`'s, wait for its socket to stop answering, then replace it. Answers
        the new adapter's word, or None when none was started."""
        if self.stopping.is_set() or not self._take(key):
            return None
        try:
            record = self.wait_gone(key)
            if record is None:
                return None
            return self.replace(key, record, stopped_at)
        finally:
            self._give(key)

    def wait_gone(self, key: str) -> dict | None:
        """The claim file keyed `key` once its adapter is gone: a `claude-shim` file whose
        socket answers no one while `claude` runs, read every `poll_s` for at most
        `gone_wait_s`. None when the file is not there or not a `claude-shim`'s, when
        `claude` has ended, when the socket still answers at the end, or when the daemon
        stops."""
        from .. import claimfile

        deadline = self.clock() + self.gone_wait_s
        while True:
            record = claimfile.read(key)
            if record is None or record.get("kind") != CLAUDE_KIND:
                return None
            if not claimfile.tree_root_alive(record):
                return None
            if not claimfile.socket_answers(record):
                return record
            if self.clock() >= deadline:
                log.info("the adapter of claim %s lost its daemon connection and still "
                         "answers its claim socket; it binds again by itself", key)
                return None
            if self.stopping.wait(self.poll_s):
                return None

    def replace(self, key: str, record: dict, stopped_at: str) -> object:
        """Start a new adapter for the gone one keyed `key`, inside the cap. Answers its
        word, or None when none was started or it gave none in time."""
        if self.stopping.is_set() or not self.allowed(key):
            return None
        who = (record.get("claim") or {}).get("qualified") or key
        try:
            env = session_env(record)
            word = self._start(env, key, record, wait_s=self.word_wait_s,
                               stopped_at=stopped_at, systemd_run=self.systemd_run)
        except CannotRespawn as exc:
            log.warning("the comms adapter of %s (claim %s) is gone and no new one was "
                        "started: %s", who, key, exc)
            return None
        log.info("the comms adapter of %s (claim %s) was gone; a new one %s", who, key,
                 "gave no word in time" if word is None else f"says {word[0]!r} "
                 f"{word[1]}".rstrip())
        return word

    def allowed(self, key: str) -> bool:
        """Whether the cap lets this daemon start one more adapter for `key` now, and if
        so counts it. Past the cap the key is logged once until the window lets it in
        again."""
        now = self.clock()
        with self._lock:
            for k in [k for k, times in self._recent.items()
                      if not any(now - t < RESPAWN_WINDOW_S for t in times)]:
                del self._recent[k]
            recent = [t for t in self._recent.get(key, ()) if now - t < RESPAWN_WINDOW_S]
            if len(recent) >= RESPAWN_CAP:
                self._recent[key] = recent
                if key not in self._capped:
                    self._capped.add(key)
                    log.warning("the comms adapter of claim %s was replaced %d times in "
                                "%.0f minutes; this daemon replaces it no more for now, "
                                "and the session's next start or prompt says why mail "
                                "has stopped", key, RESPAWN_CAP, RESPAWN_WINDOW_S / 60)
                return False
            self._capped.discard(key)
            recent.append(now)
            self._recent[key] = recent
            return True


def session_dir(key: str, env: dict) -> Path:
    """The runtime directory of the session keyed `key` (0700), made when it is gone:
    the new adapter's working directory, which `Session.write` keeps."""
    from .session import sessions_dir

    path = sessions_dir(env) / key
    path.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    os.chmod(path, 0o700)
    return path


def main(argv=None) -> int:                               # pragma: no cover - forked
    """`python -m moxi_comms.adapters.respawn <key> <claude pid> <start> <ready fd>
    [<stopped at>]`: fork the adapter and leave. The adapter takes the session's respawn
    lock, a pidfd on `claude` whose start time it checks, and serves the session as the
    launch's adapter did. With `<stopped at>` it leaves the session's note at its first
    word (`note_writer`)."""
    args = argv if argv is not None else sys.argv[1:]
    key, pid, start, ready = args[:4]
    stopped_at = args[4] if len(args) > 4 else None
    ready_fd, harness_pid = int(ready), int(pid)
    if os.fork() != 0:
        return 0
    from moxi import files

    from ..membership import DEFAULT_PROC
    from .session import _adapter_process
    from .terminal import pidfd_open
    from .word import FAILED, STARTING, teller

    with contextlib.ExitStack() as held:
        try:
            held.enter_context(files.lock(lock_path(key), timeout_s=0))
        except files.LockTimeout:
            teller(ready_fd)(STARTING)
            return 0
        try:
            handle = pidfd_open(harness_pid)
        except OSError as exc:
            teller(ready_fd)(FAILED, f"claude could not be watched: {exc}")
            return 1
        if DEFAULT_PROC.start_time(harness_pid) != start:
            teller(ready_fd)(FAILED, "its claude process has ended")
            return 1
        env = dict(os.environ)
        session = session_of(key, env)
        session.write()
        on_word = None if stopped_at is None else note_writer(key, session.dir, stopped_at)
        return _adapter_process(session, env, harness_pid, handle, ready_fd,
                                respawned=True, on_word=on_word)


def note_writer(key: str, session_dir: Path, stopped_at: str):
    """`on_word(code, reason)` for an adapter the daemon started: at its first word, the
    session's note (`NOTE`, 0600, replaced whole) holds the line that word makes
    (`listener.respawn_line`). `stopped_at` is the old adapter's stop in epoch seconds,
    or `-`. A `STARTING` word leaves none: the adapter that serves the session says its
    own."""
    from .. import claimfile
    from .word import STARTING

    said = []

    def on_word(code: str, reason: str = "") -> None:
        if said:
            return
        said.append(code)
        if code == STARTING:
            return
        from ..listener import respawn_line

        record = claimfile.read(key) or {}
        who = (record.get("claim") or {}).get("qualified") or "this session"
        at = None if stopped_at == "-" else float(stopped_at)
        write_note(session_dir, respawn_line(who, (code, reason), stopped_at=at))

    return on_word


def write_note(session_dir: Path, line: str) -> Path:
    """Write the session's note, 0600, whole: a temporary file renamed over it."""
    target = Path(session_dir) / NOTE
    tmp = target.with_name(f".{NOTE}.{os.getpid()}")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(line + "\n")
    os.replace(tmp, target)
    return target


def session_of(key: str, env: dict):
    """The launch's `Session` for the session keyed `key`, from the stamps in its
    environment. The claim itself comes from the claim file, which the new shim reads."""
    from .. import directors as directors_mod
    from ..constants import COMMAND_ENV, GLOBAL_ENV, NAME_ENV, PROJECT_ENV, SCOPE_ENV
    from .session import Session, _is_uuid

    session = Session(key, name=env.get(NAME_ENV), scope=env.get(SCOPE_ENV),
                      global_=bool(env.get(GLOBAL_ENV)), project=env.get(PROJECT_ENV),
                      directors=directors_mod.from_env(env), comms=env.get(COMMAND_ENV))
    session.stated_id = key if _is_uuid(key) else None
    return session


__all__ = ["CLAUDE_KIND", "CannotRespawn", "FIND", "GONE_POLL_S", "GONE_WAIT_S",
           "LOCK_PREFIX", "NOTE", "RESPAWN_CAP", "RESPAWN_WINDOW_S", "Respawner",
           "SYSTEMD_RUN_STDERR_CHARS", "UNIT_PREFIX", "USER_MANAGER_ENV", "WORD_WAIT_S", "claude_root", "in_service",
           "lock_path", "main", "note_writer", "respawn_adapter", "session_dir",
           "session_env", "session_of", "starter_argv", "unit_name", "with_user_manager",
           "write_note"]


if __name__ == "__main__":                                 # pragma: no cover
    raise SystemExit(main())

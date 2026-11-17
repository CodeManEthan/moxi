"""Archive folders: older transcripts kept outside a harness's own root, and whether each
answers now.

sessions' config names them per harness, `claude.archives` and `codex.archives`, each a
list of folders laid out as that harness lays out its root (Claude Code's
`<project>/<session>.jsonl`, Codex's `YYYY/MM/DD/rollout-...jsonl`). OpenCode has none: its
records are one database the harness keeps. A folder is read after the live root, in the
order named (`find.corpus`, `find.find(..., include_archives=True)`).

**sessions never writes, creates, moves, renames, deletes or locks anything in an archive
folder, and never changes a mode there.** It opens files there read only. The harness
doesn't know the folder, and nothing cleans it but whoever keeps it.

**The answer test.** A folder answers when it can be listed within `archives.answer_wait_s`. A
call that is about to list or probe archive folders tests every one it will ask that this
process holds no fresh answer for, at once: one child process per folder, all started
together, each listing the folder's top level once and saying whether it held any entry.
The call waits for them together, at most `archives.answer_wait_s` (5 seconds by default),
however many folders don't answer. A folder answers when its child exits 0 within that
wait. A folder that is absent, isn't a directory, raises an error, or gives no answer in
time is offline, with the reason. Offline is not gone: no caller may read a session as
absent from an offline folder.

Past the wait, each child still running is sent SIGKILL and not waited for, since a
process blocked on a hung network mount can't die until the mount answers; a later call
reaps it. The test is a child process, not a thread, because a thread blocked in a hung
`stat` holds its process at exit. A process keeps each folder's answer for `KEEP_S`
seconds, so the calls of one read or walk test each folder once; `fresh=True` takes no
kept answer.

What the wait doesn't bound: once a folder answered, its listing and reads run in the
calling process, and a mount that stops answering partway can hang that call.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from collections.abc import Iterable
from dataclasses import dataclass

from .shape import CLAUDE, CODEX

#: Each harness's archive key in sessions' config. OpenCode has none.
ARCHIVE_KEYS = {CLAUDE: "claude.archives", CODEX: "codex.archives"}
#: The key holding how long a call waits for its folders' answers, in seconds.
WAIT_KEY = "archives.answer_wait_s"
#: How long this process keeps a folder's answer, in seconds.
KEEP_S = 60.0
#: Why a folder is offline when its child gave no answer within the wait.
NO_ANSWER = "no answer within {wait_s:g} s"

#: The child's program: list the folder's top level once; print `empty` or `entries` and
#: exit 0, or print the reason and exit 3.
_CHILD = """\
import os, sys
try:
    with os.scandir(sys.argv[1]) as it:
        first = next(it, None)
except FileNotFoundError:
    print("does not exist"); sys.exit(3)
except NotADirectoryError:
    print("is not a directory"); sys.exit(3)
except OSError as exc:
    print(exc.strerror or str(exc)); sys.exit(3)
print("empty" if first is None else "entries")
"""


@dataclass(frozen=True)
class ArchiveAnswer:
    """One archive folder's answer test.

    * `archive_dir`: the folder as configured, `~` expanded and nothing resolved.
    * `ok`: it was listed within the wait.
    * `reason`: why it is offline, else None.
    * `empty`: it answered and its top level holds no entry at all. A disk that isn't
      mounted can leave its mount point an empty directory, so a caller that has read from
      the folder before treats this as not answering.
    """

    archive_dir: str
    ok: bool
    reason: str | None = None
    empty: bool = False

    def as_dict(self) -> dict:
        return {"archive_dir": self.archive_dir, "ok": self.ok, "reason": self.reason,
                "empty": self.empty}


def archive_dirs(harness: str) -> tuple[str, ...]:
    """`harness`'s archive folders as sessions' config names them, in order, `~` expanded
    and nothing resolved; none for OpenCode. A folder named twice is kept once. Raises
    `moxi.config.ConfigError` when the config file cannot be read."""
    key = ARCHIVE_KEYS.get(harness)
    if key is None:
        return ()
    from .settings import current

    out: list[str] = []
    for item in current()[key]:
        path = os.path.expanduser(item) if item.startswith("~") else item
        if path not in out:
            out.append(path)
    return tuple(out)


def configured_wait_s() -> float:
    """`archives.answer_wait_s`, read at each call. Raises `moxi.config.ConfigError` when the
    config file cannot be read."""
    from .settings import current

    return float(current()[WAIT_KEY])


_lock = threading.Lock()
_kept: dict[str, tuple[float, ArchiveAnswer]] = {}
_unreaped: list[subprocess.Popen] = []


def forget() -> None:
    """Forget every kept answer (tests, and a caller that changed the folders)."""
    with _lock:
        _kept.clear()


def _argv(archive_dir: str) -> list[str]:
    """The child that tests one folder."""
    return [sys.executable, "-I", "-S", "-c", _CHILD, archive_dir]


def _spawn(archive_dir: str) -> subprocess.Popen:
    return subprocess.Popen(_argv(archive_dir), stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            close_fds=True)


def _reap() -> None:
    """Collect the children an earlier call killed and left, once each has exited."""
    with _lock:
        _unreaped[:] = [proc for proc in _unreaped if proc.poll() is None]


def _said(proc: subprocess.Popen) -> str:
    try:
        return proc.stdout.read().decode("utf-8", "replace").strip() if proc.stdout else ""
    except OSError:
        return ""
    finally:
        if proc.stdout is not None:
            proc.stdout.close()


def archive_answers(paths: Iterable[str], *, fresh: bool = False,
            wait_s: float | None = None) -> dict[str, ArchiveAnswer]:
    """Each folder's answer, keyed by the folder as given, testing at once every folder
    this process holds no answer for (every folder with `fresh`), waiting at most
    `wait_s` seconds for all of them together (config `archives.answer_wait_s` when None). A
    relative folder is offline without a test. Never raises for what a folder does;
    raises `moxi.config.ConfigError` when `wait_s` is None and the config file cannot be
    read."""
    wanted = list(dict.fromkeys(str(p) for p in paths))
    out: dict[str, ArchiveAnswer] = {}
    now = time.monotonic()
    to_test: list[str] = []
    with _lock:
        for archive_dir in wanted:
            kept = None if fresh else _kept.get(archive_dir)
            if kept is not None and now - kept[0] < KEEP_S:
                out[archive_dir] = kept[1]
            elif not archive_dir.startswith("/"):
                out[archive_dir] = ArchiveAnswer(archive_dir, ok=False, reason="is not an absolute path")
            else:
                to_test.append(archive_dir)
    if not to_test:
        return {archive_dir: out[archive_dir] for archive_dir in wanted}
    _reap()
    wait = configured_wait_s() if wait_s is None else float(wait_s)
    started: dict[str, subprocess.Popen | None] = {}
    tested: dict[str, ArchiveAnswer] = {}
    for archive_dir in to_test:
        try:
            started[archive_dir] = _spawn(archive_dir)
        except OSError as exc:
            started[archive_dir] = None
            tested[archive_dir] = ArchiveAnswer(archive_dir, ok=False,
                                    reason=f"its test could not start: {exc.strerror or exc}")
    deadline = time.monotonic() + wait
    for archive_dir, proc in started.items():
        if proc is None:
            continue
        try:
            code = proc.wait(timeout=max(0.0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            code = None
        if code is None:
            try:
                proc.kill()
            except OSError:
                pass
            if proc.stdout is not None:
                proc.stdout.close()
            with _lock:
                _unreaped.append(proc)
            tested[archive_dir] = ArchiveAnswer(archive_dir, ok=False, reason=NO_ANSWER.format(wait_s=wait))
            continue
        said = _said(proc)
        if code == 0 and said in ("empty", "entries"):
            tested[archive_dir] = ArchiveAnswer(archive_dir, ok=True, empty=said == "empty")
        else:
            tested[archive_dir] = ArchiveAnswer(archive_dir, ok=False, reason=said or f"its test exited {code}")
    stamp = time.monotonic()
    with _lock:
        for archive_dir, answer in tested.items():
            _kept[archive_dir] = (stamp, answer)
    out.update(tested)
    return {archive_dir: out[archive_dir] for archive_dir in wanted}


__all__ = ["ARCHIVE_KEYS", "WAIT_KEY", "KEEP_S", "NO_ANSWER", "ArchiveAnswer", "archive_dirs",
           "configured_wait_s", "archive_answers", "forget"]

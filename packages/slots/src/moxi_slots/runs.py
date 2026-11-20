"""How each run in a slot ended, recorded by its run window's `pane-died` hook.

A run is one placement of a command in a slot's run pane, by `start` or `respawn`. Every
placement records, in the slot's `last`, a fresh random token for the run (`run`) and the
id of the tmux server it was placed under (`server`, `<boot id>:<tmux pid>`). It also sets
a `pane-died` hook on the run window at `HOOK`, its own index (`launch.HOOK`, which
`wait_ended` sets, is another), with the token written into the hook's command. When the
pane dies, tmux runs `main` in the background with the slot's state directory, the slot
number, the token, `#{pane_dead_status}`, `#{pane_dead_signal}` and
`#{pane_dead_time}`. Under the module lock, waited for up to `LOCK_WAIT_S` seconds, it
writes `last.ended` only when the slot's `last.run` is still that token.

The rule: an ended record is written to the run it belongs to, or not at all. A respawn
places a new run with a new token and sets the hook again, so a late hook from the run
before finds a different token and writes nothing.

The hook prints nothing and always exits 0: tmux shows a `run-shell` command's output, or
its failing status, over the pane. A write it fails (the lock not had, the registry
unreadable) goes as one line to the state directory's `hooks.log`, which the doctor
reports, since the run it missed then reads crashed after the next server start.

A run ended by itself when its `ended` record has status 0 and no signal and the caller
placed it with `status_trusted`. A slot is crashed (`crashed`) when it is not occupied,
its last run was placed under a server (`last.server`) that is not the one answering now,
that run did not end by itself, the slot was not settled, and either no end was ever
recorded (the server went down with the run) or the run ended within `WINDOW_S` seconds
before its server's end (a shutdown that signalled the session just before the server
went). A server's end is, for a server of an earlier boot, that boot's last journal
entry (`journalctl --list-boots`), and for one of this boot, the current server's start
time. Where a server's end can't be read, only a run with no end recorded reads crashed.
The journal is never read under the module lock: `registry.locked` reads it for every
earlier boot the registry names before it takes the lock (`read_journal_first`), and
while the lock is held `journal_end` answers only from what it has read.
Every time compared is from one boot's clock. With no server answering, nothing reads
crashed.
"""

from __future__ import annotations

import secrets
import shlex
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from . import registry, tmux
from .errors import TmuxError
from .model import OCCUPIED, Last, RunEnd
from .panes import ServerNow

#: The run window's `pane-died` hook that records how the run ended.
HOOK = "pane-died[65]"
#: Seconds the hook waits for the module lock.
LOCK_WAIT_S = 60.0
#: Where Linux keeps this boot's id.
BOOT_ID = Path("/proc/sys/kernel/random/boot_id")
#: Seconds before its server's end within which a run's death counts as the shutdown's.
WINDOW_S = 120.0
#: Seconds `journalctl --list-boots` gets.
JOURNAL_TIMEOUT_S = 10.0
#: The size at which the hooks log moves to `hooks.log.1` and starts again.
HOOKS_LOG_BYTES = 100_000

#: Seconds after which a boot the journal did not list is asked about again.
JOURNAL_RETRY_S = 60.0

#: The last journal entry of each earlier boot read in this process, by boot id; a past
#: boot's last entry never changes, so it is read once.
_journal_ends: dict[str, float] = {}
#: When the journal was last read without listing a boot, by boot id (monotonic time):
#: that boot reads unknown until `JOURNAL_RETRY_S` has passed, then the journal is read
#: again, so a long-lived process does not keep one failed read for good.
_journal_misses: dict[str, float] = {}
#: How deep this thread is in `journal_closed` blocks: while it is above 0, the module
#: lock is held and `journal_end` reads no journal.
_closed = threading.local()


def new_token() -> str:
    """A fresh run token."""
    return secrets.token_hex(8)


def boot_id() -> str | None:
    """This boot's id, or None where it can't be read (a system with no
    `/proc/sys/kernel/random/boot_id`)."""
    try:
        text = BOOT_ID.read_text(encoding="ascii").strip()
    except OSError:
        return None
    return text or None


def server_id(pid: int | None, boot: str | None = None) -> str | None:
    """`<boot id>:<tmux pid>` of a server, or None when either is unknown."""
    boot = boot_id() if boot is None else boot
    if boot is None or pid is None:
        return None
    return f"{boot}:{pid}"


def server_pid() -> int:
    """The pid of the server answering on the socket. Raises as `tmux.call` does."""
    out = tmux.call("display-message", "-p", "#{pid}").out.strip()
    if not out.isdigit():
        raise TmuxError(f"tmux gave no server pid: {out!r}")
    return int(out)


def _tmux_quoted(text: str) -> str:
    """`text` as one word of a tmux command, single-quoted (tmux joins quoted parts)."""
    return "'" + text.replace("'", "'\"'\"'") + "'"


def hook_command(number: int, token: str, state_dir: Path | str | None = None) -> str:
    """The `pane-died` hook's command for slot `number`'s run `token`: a `run-shell -b`
    of this module in this Python, its output and status dropped."""
    from . import places

    state_dir = places.state_dir() if state_dir is None else state_dir
    words = [shlex.quote(w).replace("#", "##")
             for w in (sys.executable, "-I", "-m", __name__, str(state_dir), str(number),
                       token)]
    words += ["'#{pane_dead_status}'", "'#{pane_dead_signal}'", "'#{pane_dead_time}'"]
    return "run-shell -b " + _tmux_quoted(" ".join(words) + " >/dev/null 2>&1 || :")


def set_hook_args(window: str, number: int, token: str) -> list[str]:
    """The tmux arguments that set the hook on `window`, to chain after a placement."""
    return ["set-hook", "-w", "-t", window, HOOK, hook_command(number, token)]


def _boot_key(boot: str) -> str:
    return boot.replace("-", "").lower()


def journal_end(boot: str) -> float | None:
    """When boot `boot`'s journal ends (its last entry, epoch seconds), or None when the
    journal can't be read or doesn't list that boot. Inside `journal_closed` it reads no
    journal: a boot not read before is None there."""
    key = _boot_key(boot)
    if key in _journal_ends:
        return _journal_ends[key]
    if getattr(_closed, "depth", 0):
        return None
    missed = _journal_misses.get(key)
    if missed is not None and time.monotonic() - missed < JOURNAL_RETRY_S:
        return None
    _journal_ends.update(_journal_boots())
    if key in _journal_ends:
        _journal_misses.pop(key, None)
        return _journal_ends[key]
    _journal_misses[key] = time.monotonic()
    return None


def _earlier_boot(server: str) -> str | None:
    """The boot of server `server` (`<boot id>:<pid>`) when it is not this boot."""
    boot = server.rsplit(":", 1)[0]
    return None if _boot_key(boot) == _boot_key(boot_id() or "") else boot


def read_journal_first(reg: registry.Registry) -> None:
    """Read the journal's end of every earlier boot a slot of `reg` was last placed under,
    so a crashed reading taken under the module lock finds it read."""
    for row in reg.rows.values():
        boot = None if row.last is None or row.last.server is None \
            else _earlier_boot(row.last.server)
        if boot is not None:
            journal_end(boot)


@contextmanager
def journal_closed() -> Iterator[None]:
    """For the length of the block, this thread's `journal_end` reads no journal."""
    depth = getattr(_closed, "depth", 0)
    _closed.depth = depth + 1
    try:
        yield
    finally:
        _closed.depth = depth


def _journal_boots() -> dict[str, float]:
    import json
    import shutil
    import subprocess

    from moxi import proc

    found = shutil.which("journalctl")
    if found is None:
        return {}
    try:
        done = proc.run([found, "--list-boots", "--output=json", "--no-pager"],
                        timeout_s=JOURNAL_TIMEOUT_S)
        out = done.stdout.decode("utf-8", "replace") if isinstance(done.stdout, bytes) \
            else (done.stdout or "")
        boots = json.loads(out) if done.returncode == 0 else []
    except (OSError, subprocess.SubprocessError, ValueError):
        return {}
    ends = {}
    for boot in boots if isinstance(boots, list) else []:
        if isinstance(boot, dict) and isinstance(boot.get("boot_id"), str) \
                and isinstance(boot.get("last_entry"), int):
            ends[_boot_key(boot["boot_id"])] = boot["last_entry"] / 1e6
    return ends


def server_end(server: str, current: ServerNow) -> float | None:
    """When server `server` (`<boot id>:<pid>`) ended, by `current`'s reading of it:
    this boot's current server start for a server of this boot, else that boot's last
    journal entry; None when unknown."""
    boot = _earlier_boot(server)
    if boot is None:
        return None if current.start_time is None else float(current.start_time)
    return journal_end(boot)


def ended_by_itself(last: Last) -> bool:
    """Whether `last`'s run ended by itself: status 0, no signal, a trusted status."""
    ended = last.ended
    return (ended is not None and ended.settled is None and ended.status == 0
            and ended.signal is None and last.status_trusted)


def crashed(state: str, last: Last | None, current: ServerNow | None) -> bool:
    """Whether a slot in `state` with last launch `last` reads crashed against
    `current`, the server answering now (None: none answers, and nothing is crashed)."""
    if current is None or state == OCCUPIED or last is None or last.server is None:
        return False
    if last.server == server_id(current.pid):
        return False
    if ended_by_itself(last):
        return False
    if last.ended is None:
        return True
    if last.ended.settled is not None:
        return False
    end = server_end(last.server, current)
    return end is not None and last.ended.at >= end - WINDOW_S


def _number(text: str) -> int | None:
    text = text.strip()
    return int(text) if text.lstrip("-").isdigit() else None


def write_ended(number: int, token: str, status: int | None, signal: int | None,
                at: float | None, *, state_dir: Path | None = None,
                timeout_s: float = LOCK_WAIT_S) -> bool:
    """Write `last.ended` for slot `number` when its `last.run` is `token`; True when
    written. `at` None is now. Raises `TryAgain` when the lock is not had in
    `timeout_s` seconds."""
    path = lock = None
    if state_dir is not None:
        path, lock = Path(state_dir) / "slots.json", Path(state_dir) / "slots.lock"
    with registry.locked(timeout_s, path=lock):
        reg = registry.read(path)
        row = reg.rows.get(number)
        if row is None or row.last is None or row.last.run != token:
            return False
        ended = RunEnd(time.time() if at is None else float(at), status, signal)
        row.last = _with_ended(row.last, ended)
        registry.write(reg, path)
        return True


def _with_ended(last: Last, ended: RunEnd) -> Last:
    from dataclasses import replace

    return replace(last, ended=ended)


def main(argv: list[str] | None = None) -> int:
    """`python -m moxi_slots.runs STATE_DIR N TOKEN STATUS SIGNAL TIME`: the hook's entry
    point. Always 0, and prints nothing a person would see."""
    args = sys.argv[1:] if argv is None else argv
    try:
        state_dir, number, token, status, signal, at = args
        write_ended(int(number), token, _number(status), _number(signal),
                    None if _number(at) is None else float(_number(at)),
                    state_dir=Path(state_dir))
    except Exception as exc:  # noqa: BLE001 -- the screen is no place for it; the log is
        _log_failure(args, exc)
    return 0


def _log_failure(args: list[str], exc: Exception) -> None:
    """One line in the state directory's hooks log for a run end the hook could not
    write, for the doctor to report: the run it missed reads crashed later."""
    from moxi import files

    from . import places

    try:
        state_dir = Path(args[0]) if args else None
        files.append_record(places.hooks_log(state_dir), {
            "at": time.time(), "slot": args[1] if len(args) > 1 else None,
            "run": args[2] if len(args) > 2 else None,
            "error": f"{type(exc).__name__}: {exc}"[:500]}, max_bytes=HOOKS_LOG_BYTES)
    except Exception:  # noqa: BLE001 -- nowhere left to say it
        pass


__all__ = ["HOOK", "LOCK_WAIT_S", "BOOT_ID", "WINDOW_S", "JOURNAL_TIMEOUT_S",
           "JOURNAL_RETRY_S", "HOOKS_LOG_BYTES", "new_token", "boot_id", "server_id",
           "server_pid", "hook_command", "set_hook_args", "journal_end",
           "read_journal_first", "journal_closed", "server_end", "ended_by_itself",
           "crashed", "write_ended", "main"]


if __name__ == "__main__":
    sys.exit(main())

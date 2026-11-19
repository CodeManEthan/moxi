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
its failing status, over the pane.
"""

from __future__ import annotations

import secrets
import shlex
import sys
import time
from pathlib import Path

from . import registry, tmux
from .errors import TmuxError
from .model import Last, RunEnd

#: The run window's `pane-died` hook that records how the run ended.
HOOK = "pane-died[65]"
#: Seconds the hook waits for the module lock.
LOCK_WAIT_S = 60.0
#: Where Linux keeps this boot's id.
BOOT_ID = Path("/proc/sys/kernel/random/boot_id")


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
    except Exception:  # noqa: BLE001 -- a hook has no one to tell; it writes nothing
        pass
    return 0


__all__ = ["HOOK", "LOCK_WAIT_S", "BOOT_ID", "new_token", "boot_id", "server_id",
           "server_pid", "hook_command", "set_hook_args", "write_ended", "main"]


if __name__ == "__main__":
    sys.exit(main())

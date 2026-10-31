"""The word a session's adapter gives its wrapper before the harness starts.

A wrapper forks the session's adapter and waits on a pipe for one word: `BOUND` once
the bind has been answered and the claim socket listens, or why the bind failed. Only
`BOUND` starts the harness. Any other word ends the launch there, before the harness
starts, with the exit code `EXIT_OF` gives it: a name the daemon refuses (held,
reserved, retired) is `REFUSED`, and the wrapper prints the daemon's words.

The word is its first character; any text after it is the reason.
"""

from __future__ import annotations

import os
import select

from moxi import exit as moxi_exit

BOUND, OFFLINE, REFUSED, TRY_AGAIN, FAILED = "B", "O", "R", "T", "E"
#: The wrapper's exit code for each word but `BOUND`.
EXIT_OF = {OFFLINE: moxi_exit.TEMPFAIL, REFUSED: moxi_exit.REFUSED,
           TRY_AGAIN: moxi_exit.TEMPFAIL, FAILED: moxi_exit.ERROR}


def word_for(error: BaseException) -> str:
    """The adapter's word for a start that raised `error`."""
    from ..client import DaemonError, DaemonOffline, VersionMismatch

    if isinstance(error, VersionMismatch):
        return FAILED
    if isinstance(error, DaemonOffline):
        return OFFLINE
    if isinstance(error, DaemonError):
        if error.refused:
            return REFUSED
        if error.try_again:
            return TRY_AGAIN
    return FAILED


def exit_of(word: str) -> int:
    """The wrapper's exit code for a launch that ended on `word`."""
    return EXIT_OF.get(word, moxi_exit.ERROR)


def teller(fd: int):
    """`say(code, reason="")` for the adapter's end of the pipe `fd`: the first call
    writes the word and closes the pipe, and any later call says nothing."""
    said = []

    def say(code: str, reason: str = "") -> None:
        if said:
            return
        said.append(code)
        try:
            os.write(fd, (code + reason).encode("utf-8")[:4096])
            os.close(fd)
        except OSError:
            pass

    return say


def await_word(fd: int, timeout: float):
    """The adapter's word, `(code, reason)`, once it has written it; `(FAILED, "")` when
    it ended without one; None when `timeout` passed first."""
    ready, _, _ = select.select([fd], [], [], timeout)
    if not ready:
        return None
    chunks = []
    while True:
        try:
            chunk = os.read(fd, 4096)
        except OSError:
            break
        if not chunk:
            break
        chunks.append(chunk)
        if chunks[0][:1] == BOUND.encode():
            break
    text = b"".join(chunks).decode("utf-8", "replace")
    if not text:
        return (FAILED, "")
    return (text[0], text[1:])


__all__ = ["BOUND", "OFFLINE", "REFUSED", "TRY_AGAIN", "FAILED", "EXIT_OF", "await_word",
           "exit_of", "teller", "word_for"]

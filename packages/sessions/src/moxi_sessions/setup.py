"""sessions' setup, the announcement's `SETUP`: what `moxi install sessions` runs.

It prints how long each harness keeps the transcripts sessions reads (`retention.lines()`),
since every reader of sessions loses what a harness deletes, and says how to keep them
longer. Then each archive folder sessions' config names, and whether it answers now (all
tested together, one `archives.answer_wait_s` at most). It asks nothing and changes no setting,
so it can run again at any time.
"""

from __future__ import annotations

from moxi import exit as codes
from moxi.config import ConfigError

from .archives import ARCHIVE_KEYS, archive_answers, archive_dirs
from .retention import lines


def setup() -> int:
    """Print each harness's retention lines, then each archive folder and whether it
    answers now; 0."""
    for line in lines():
        print(f"sessions: {line}")
    try:
        named = [(harness, archive_dir) for harness in ARCHIVE_KEYS
                 for archive_dir in archive_dirs(harness)]
        said = archive_answers([archive_dir for _, archive_dir in named]) if named else {}
    except ConfigError as exc:
        print(f"sessions: the archive folders are not known: {exc}")
        return codes.OK
    for harness, archive_dir in named:
        answer = said[archive_dir]
        now = ("answers now" if answer.ok else
               f"doesn't answer now ({answer.reason}); a read skips it and says so")
        print(f"sessions: {harness} archive folder {archive_dir}: {now}")
    return codes.OK


__all__ = ["setup"]

"""One way for every Moxi module to log: `moxi.log.setup(module)`.

The logger is `moxi.<module>`. It writes to stderr, which the service manager collects
(the journal, launchd's log file, the gate's log on Windows), and does not pass records
on to the root logger. Its level is `MOXI_<MODULE>_LOG_LEVEL` when that is set, else the
level the caller gives, else `WARNING`. Under journald (`JOURNAL_STREAM` set) a line
carries no time stamp, since the journal stamps each line itself.

When `MOXI_LOG_FILE` names the file stderr is written to (launchd sets it for a Moxi
unit), the file is rolled here, since launchd rolls nothing: past 5 MiB it moves to
`<file>.1` and a fresh file takes over standard output and standard error.

Event logs, records a program reads back, go through `moxi.files.append_record` instead.
"""

from __future__ import annotations

import os
import sys

from . import names

TYPE_CHECKING = False
if TYPE_CHECKING:
    import logging

#: Past this size the file named by `MOXI_LOG_FILE` is rolled to `<file>.1`.
ROLL_BYTES = 5 * 1024 * 1024
FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
JOURNAL_FORMAT = "%(levelname)s %(name)s: %(message)s"


def setup(module: str, level: str | int | None = None) -> logging.Logger:
    """The logger `moxi.<module>`, with its one stderr handler and its level set.

    `level` is a level name (`"info"`, `"DEBUG"`) or number. Calling `setup` again
    returns the same logger and adds no second handler; the level is set again.
    `logging` itself is imported here, not when `moxi.log` is.
    """
    import logging

    variable = f"{names.env_prefix(module)}LOG_LEVEL"
    logger = logging.getLogger(f"moxi.{module}")
    logger.propagate = False
    if not any(getattr(h, "moxi_setup", False) for h in logger.handlers):
        logger.addHandler(_handler_class()(os.environ.get("MOXI_LOG_FILE") or None))
    given = None
    if level is not None and (given := _level(level)) is None:
        raise ValueError(f"{level!r} is not a logging level")
    from_env = os.environ.get(variable)
    chosen = _level(from_env) if from_env else None
    logger.setLevel(next(v for v in (chosen, given, logging.WARNING) if v is not None))
    if from_env and chosen is None:
        logger.warning("%s=%r is not a logging level; using %s", variable, from_env,
                       logging.getLevelName(logger.level))
    return logger


def _level(value: str | int) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if not isinstance(value, str):
        return None
    text = value.strip()
    if text.isdigit():
        return int(text)
    import logging

    return logging.getLevelNamesMapping().get(text.upper())


_handler: type | None = None


def _handler_class() -> type:
    """The handler class, made on first use so that importing `moxi.log` stays light."""
    global _handler
    if _handler is None:
        _handler = _make_handler()
    return _handler


def _make_handler() -> type:
    import logging

    class Handler(logging.StreamHandler):
        """Writes to whatever `sys.stderr` is at the time, and rolls `MOXI_LOG_FILE`."""

        moxi_setup = True

        def __init__(self, log_file: str | None):
            super().__init__()
            self.log_file = log_file
            self.setFormatter(logging.Formatter(
                JOURNAL_FORMAT if os.environ.get("JOURNAL_STREAM") else FORMAT))

        @property
        def stream(self):
            return sys.stderr

        @stream.setter
        def stream(self, value) -> None:
            pass  # always the current sys.stderr

        def emit(self, record: logging.LogRecord) -> None:
            super().emit(record)
            if self.log_file:
                _roll(self.log_file)

    return Handler


def _roll(log_file: str) -> None:
    """Move the log file aside once it passes `ROLL_BYTES`, if stderr writes to it."""
    try:
        st = os.fstat(2)
        if st.st_size <= ROLL_BYTES:
            return
        on_disk = os.stat(log_file)
        if (on_disk.st_dev, on_disk.st_ino) != (st.st_dev, st.st_ino):
            return  # stderr is not the log file (a run by hand); leave the file be
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.flush()
            except (AttributeError, OSError, ValueError):
                pass
        os.replace(log_file, log_file + ".1")
        fd = os.open(log_file, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.dup2(fd, 1)
            os.dup2(fd, 2)
        finally:
            os.close(fd)
    except OSError:
        pass  # a log that cannot roll keeps growing; logging never stops the program


__all__ = ["setup", "ROLL_BYTES"]

"""moxi.log.setup and moxi.exit."""

from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
import textwrap

import pytest

from moxi import exit as codes
from moxi import log


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    for name in list(os.environ):
        if name.startswith("MOXI_") or name == "JOURNAL_STREAM":
            monkeypatch.delenv(name)
    yield
    for name in list(logging.root.manager.loggerDict):
        if name.startswith("moxi.logtest"):
            logger = logging.getLogger(name)
            logger.handlers.clear()
            logger.setLevel(logging.NOTSET)


def test_exit_codes():
    assert (codes.OK, codes.ERROR, codes.USAGE, codes.REFUSED, codes.TEMPFAIL) \
        == (0, 1, 2, 3, 75)


def test_setup_names_the_logger_and_adds_one_stderr_handler(capsys):
    logger = log.setup("logtesta")
    assert logger.name == "moxi.logtesta"
    assert logger.propagate is False
    assert log.setup("logtesta") is logger
    assert len(logger.handlers) == 1
    logger.warning("hello %s", "there")
    err = capsys.readouterr().err
    assert err.count("hello there") == 1
    assert "WARNING moxi.logtesta: hello there" in err


def test_the_default_level_is_warning():
    assert log.setup("logtestb").level == logging.WARNING


def test_the_callers_level_and_the_variable_over_it(monkeypatch):
    assert log.setup("logtestc", "info").level == logging.INFO
    assert log.setup("logtestc", logging.DEBUG).level == logging.DEBUG
    monkeypatch.setenv("MOXI_LOGTESTC_LOG_LEVEL", "error")
    assert log.setup("logtestc", "info").level == logging.ERROR
    monkeypatch.setenv("MOXI_LOGTESTC_LOG_LEVEL", "15")
    assert log.setup("logtestc").level == 15
    monkeypatch.setenv("MOXI_LOGTESTC_LOG_LEVEL", "")
    assert log.setup("logtestc").level == logging.WARNING


def test_a_bad_variable_falls_back_with_a_warning(monkeypatch, capsys):
    monkeypatch.setenv("MOXI_LOGTESTD_LOG_LEVEL", "loud")
    assert log.setup("logtestd", "info").level == logging.INFO
    assert "MOXI_LOGTESTD_LOG_LEVEL='loud' is not a logging level" in capsys.readouterr().err


def test_a_bad_level_argument_raises():
    with pytest.raises(ValueError):
        log.setup("logteste", "loud")


def test_a_bad_module_name_raises():
    with pytest.raises(ValueError):
        log.setup("Not-A-Name")


def test_lines_carry_a_time_stamp_except_under_journald(monkeypatch, capsys):
    log.setup("logtestf").warning("one")
    stamped = capsys.readouterr().err
    assert stamped[:4].isdigit() and stamped.rstrip().endswith("WARNING moxi.logtestf: one")
    monkeypatch.setenv("JOURNAL_STREAM", "8:1234")
    log.setup("logtestg").warning("two")
    assert capsys.readouterr().err == "WARNING moxi.logtestg: two\n"


ROLLER = textwrap.dedent("""
    import os, sys
    from moxi import log
    log.ROLL_BYTES = 2000
    logger = log.setup("logtesth")
    for i in range(int(sys.argv[1])):
        logger.warning("line %04d %s", i, "x" * 60)
    print("stdout after", flush=True)
""")


def run_roller(tmp_path, lines: int, *, named: str | None = None):
    log_file = tmp_path / "unit.log"
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("MOXI_") and k != "JOURNAL_STREAM"}
    env["MOXI_LOG_FILE"] = str(named or log_file)
    with open(log_file, "ab") as out:
        subprocess.run([sys.executable, "-c", ROLLER, str(lines)], stdout=out,
                       stderr=out, env=env, check=True, timeout=60)
    return log_file


@pytest.mark.skipif(sys.platform == "win32", reason="the log file is launchd's (POSIX)")
def test_the_log_file_rolls_past_its_cap(tmp_path):
    log_file = run_roller(tmp_path, 60)
    older = tmp_path / "unit.log.1"
    assert older.exists()
    assert os.path.getsize(log_file) <= 2000 + 200
    assert os.path.getsize(older) <= 2000 + 200
    text = log_file.read_text()
    assert "line 0059" in text and text.endswith("stdout after\n")
    numbers = [[int(w) for w in re.findall(r"line (\d{4})", f.read_text())]
               for f in (older, log_file)]
    assert numbers[0] and numbers[1] and max(numbers[0]) < min(numbers[1])


@pytest.mark.skipif(sys.platform == "win32", reason="the log file is launchd's (POSIX)")
def test_a_log_file_stderr_does_not_write_is_left_alone(tmp_path):
    other = tmp_path / "other.log"
    other.write_text("x" * 5000)
    run_roller(tmp_path, 60, named=str(other))
    assert other.read_text() == "x" * 5000
    assert not (tmp_path / "other.log.1").exists()
    assert not (tmp_path / "unit.log.1").exists()

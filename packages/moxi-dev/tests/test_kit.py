"""The kit's helpers: served, wait_for, param ids, the timed slack, the fsync and port
seams, the SQLite writer, the symlink probe, the markers, and the contract module's
public names."""

from __future__ import annotations

import enum
import os
import socket
import socketserver
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

import pytest

import moxi_dev.contract
from moxi_dev import testing
from moxi_dev.testing import param_id, served, short_dir, wait_for, write_sqlite


# --- served and wait_for ---------------------------------------------------------------

class _Echo(socketserver.BaseRequestHandler):
    def handle(self):
        self.request.sendall(self.request.recv(64))


# Windows has no Unix-socket server class: the name stands on a plain base there, and the
# row that serves one is skipped.
class _Unix(getattr(socketserver, "UnixStreamServer", object)):
    pass


class _Tcp(socketserver.TCPServer):
    allow_reuse_address = True


def _make(kind):
    if kind is _Unix:
        return lambda path: _Unix(str(path), _Echo)
    return lambda path: _Tcp(("127.0.0.1", 0), _Echo)


def _connect(server):
    address = server.server_address
    if isinstance(address, str):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(5)
        s.connect(address)
        return s
    return socket.create_connection(address, timeout=5)


@pytest.mark.parametrize("kind", [
    pytest.param(_Unix, marks=pytest.mark.skipif(sys.platform == "win32",
                                                 reason="Unix sockets")),
    _Tcp], ids=param_id)
def test_served_serves_and_stops_within_its_poll(kind):
    with served(_make(kind)) as server:
        with _connect(server) as c:
            c.sendall(b"ping")
            assert c.recv(64) == b"ping"
        where = None
        if kind is _Unix:
            where = Path(server.server_address).parent
            assert len(server.server_address) < 40
            assert where.is_dir()
        began = time.monotonic()
    stopped = time.monotonic() - began
    assert stopped < 10 * testing.SHUTDOWN_POLL, stopped
    if where is not None:
        assert not where.exists()


def test_the_served_ids_are_the_class_names(request):
    ids = sorted(item.name for item in request.session.items
                 if item.originalname == "test_served_serves_and_stops_within_its_poll")
    if not ids:
        pytest.skip("the served test is not in this run")
    assert ids == ["test_served_serves_and_stops_within_its_poll[_Tcp]",
                   "test_served_serves_and_stops_within_its_poll[_Unix]"]


def test_short_dir_is_short_and_goes():
    with short_dir() as where:
        assert where.is_dir()
        if hasattr(socket, "AF_UNIX"):
            assert len(str(where)) < 30
        else:
            # The bound is for a Unix socket's path, and Python has no Unix sockets here
            # (Windows): the directory is a short name right in the short root.
            assert where.parent == Path(testing._short_root()) and len(where.name) < 16
    assert not where.exists()


def test_wait_for_returns_the_value_and_times_out():
    calls = []
    assert wait_for(lambda: calls.append(1) or (len(calls) >= 3 and "done")) == "done"
    with pytest.raises(AssertionError, match="timed out after 0.05s waiting for the flag"):
        wait_for(lambda: False, timeout=0.05, what="the flag")


class _Colour(enum.Enum):
    RED = 1


@pytest.mark.parametrize("value, expected", [
    (None, "None"), (True, "True"), (3, "3"), (0.5, "0.5"), ("", "empty"), ("a b", "a b"),
    (b"x", "x"), (_Colour.RED, "RED"), (Path("/x/y.toml"), "y.toml"), ((1, "a"), "1-a"),
    (len, "len"), (_Echo, "_Echo"), (object(), "object")],
    ids=["none", "bool", "int", "float", "empty", "str", "bytes", "enum", "path",
         "tuple", "builtin", "class", "object"])
def test_param_id_is_the_same_every_run(value, expected):
    assert param_id(value) == expected
    assert "0x" not in param_id(value)


# --- the fsync seam --------------------------------------------------------------------

# --- timed_slack -------------------------------------------------------------------------

SLACK_PROBE = """
import os

from moxi_dev.testing import TIMED_SLACK_ENV, timed_slack


def test_the_factor(fence):
    assert TIMED_SLACK_ENV not in os.environ
    print(f"slack={timed_slack()!r}")
"""


@pytest.fixture
def slack_probe(pytester):
    pytester.makeconftest('pytest_plugins = ["moxi_dev.testing"]\n')
    pytester.makepyfile(test_slack=SLACK_PROBE)
    return pytester


@pytest.mark.parametrize(("value", "factor"), [(None, 1.0), ("2", 2.0), (" 1.5 ", 1.5)])
def test_a_fenced_test_sees_the_slack_its_run_started_with(slack_probe, monkeypatch,
                                                          value, factor):
    if value is not None:
        monkeypatch.setenv(testing.TIMED_SLACK_ENV, value)
    result = slack_probe.runpytest_subprocess("-p", "no:cacheprovider", "-s")
    result.assert_outcomes(passed=1)
    assert f"slack={factor!r}" in result.stdout.str()


@pytest.mark.parametrize("value", ["0.5", "0", "x", "nan", "inf"])
def test_a_slack_that_is_not_a_factor_of_at_least_one_stops_the_run(slack_probe,
                                                                    monkeypatch, value):
    monkeypatch.setenv(testing.TIMED_SLACK_ENV, value)
    result = slack_probe.runpytest_subprocess("-p", "no:cacheprovider")
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    assert testing.TIMED_SLACK_ENV in result.stderr.str()
    assert repr(value) in result.stderr.str()


# --- a bench module's corpus -------------------------------------------------------------

BENCH_MODULE = """
import shutil

import pytest


@pytest.fixture(scope="module")
def corpus(tmp_path_factory):
    corpus_dir = tmp_path_factory.mktemp("bench")
    (corpus_dir / "transcript.jsonl").write_text("{}\\n" * 1000)
    yield corpus_dir
    shutil.rmtree(corpus_dir, ignore_errors=True)


def test_one(corpus):
    assert (corpus / "transcript.jsonl").exists()


def test_two(corpus):
    assert (corpus / "transcript.jsonl").exists()
"""

AFTER_MODULE = """
def test_the_bench_modules_corpus_is_gone_when_the_next_module_runs(tmp_path_factory):
    base = tmp_path_factory.getbasetemp()
    assert not [path for path in base.iterdir()
                if path.name.startswith("bench") and path.is_dir()]
"""

KEPT_MODULE = """
import pytest


@pytest.fixture(scope="module")
def corpus(tmp_path_factory):
    return tmp_path_factory.mktemp("kept")


def test_one(corpus):
    assert corpus.is_dir()
"""


def corpora(base: Path, prefix: str) -> list[Path]:
    """The directories under `base` named `prefix...`, leaving out pytest's
    `<prefix>current` link to the newest one."""
    return [path for path in base.rglob(f"{prefix}*")
            if path.is_dir() and not path.is_symlink()]


def test_a_bench_module_leaves_no_corpus_behind_in_the_base_temp(pytester):
    pytester.makepyfile(test_a_bench=BENCH_MODULE, test_b_after=AFTER_MODULE,
                        test_c_kept=KEPT_MODULE)
    base = pytester.path / "basetemp"
    result = pytester.runpytest_subprocess("-p", "no:cacheprovider", "-p", "no:randomly",
                                           f"--basetemp={base}")
    result.assert_outcomes(passed=4)
    assert corpora(base, "bench") == []
    assert len(corpora(base, "kept")) == 1          # the listing sees a corpus left behind


def test_fsync_is_a_no_op_in_a_plain_test(tmp_path):
    assert os.fsync is not testing._REAL_FSYNC
    fd = os.open(tmp_path / "f", os.O_WRONLY | os.O_CREAT, 0o600)
    os.write(fd, b"x")
    assert os.fsync(fd) is None
    with open(tmp_path / "f", "rb+") as fh:
        os.fsync(fh)                               # a file object, as os.fsync takes
    os.close(fd)
    with pytest.raises(OSError):
        os.fsync(fd)                              # still refuses a closed descriptor


#: Filesystems that keep files in memory only: an fsync there reaches no disk.
MEMORY_FILESYSTEMS = {"tmpfs", "ramfs"}


def filesystem_type(path: Path) -> str | None:
    """The type of the filesystem `path` is on, from /proc/self/mountinfo (Linux)."""
    try:
        lines = Path("/proc/self/mountinfo").read_text().splitlines()
    except OSError:
        return None
    real, best, kind = os.path.realpath(path), "", None
    for line in lines:
        fields = line.split()
        point = fields[4].replace("\\040", " ")
        fstype = fields[fields.index("-") + 1]
        if (real == point or real.startswith(point.rstrip("/") + "/")) \
                and len(point) >= len(best):
            best, kind = point, fstype
    return kind


@pytest.mark.honest_fsync
def test_an_honest_fsync_reaches_the_disk(tmp_path):
    """What it proves: in a test marked honest_fsync the seam hands over the real
    os.fsync; the kernel accepts it on the file and on its directory; then the file's
    cached pages are dropped (POSIX_FADV_DONTNEED drops clean pages only, so they had
    been written back) and a fresh descriptor reads every byte back. The file is in
    tmp_path, so it is skipped when pytest's temp root is in memory (tmpfs, as /tmp
    often is): there a pass would show nothing reached a disk. Run with a disk-backed
    root (PYTEST_DEBUG_TEMPROOT=/var/tmp) and the read comes from the device. It does
    not prove the bytes survive a power cut: the drive's own cache is beyond what a test
    can see. On Windows a directory can't be opened as a file, so its fsync is left out,
    and there is no call that drops the file's cached pages."""
    kind = filesystem_type(tmp_path)
    if kind in MEMORY_FILESYSTEMS:
        pytest.skip(f"tmp_path is on {kind}, which has no disk; set PYTEST_DEBUG_TEMPROOT "
                    "to a disk-backed directory")
    assert os.fsync is testing._REAL_FSYNC
    data = os.urandom(256 * 1024)
    path = tmp_path / "durable"
    binary = getattr(os, "O_BINARY", 0)     # Windows opens in text mode without it
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | binary, 0o600)
    try:
        assert os.write(fd, data) == len(data)
        os.fsync(fd)
    finally:
        os.close(fd)
    if os.name != "nt":
        dir_fd = os.open(tmp_path, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    fd = os.open(path, os.O_RDONLY | binary)
    try:
        if hasattr(os, "posix_fadvise"):
            os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
        got = b""
        while chunk := os.read(fd, 65536):
            got += chunk
    finally:
        os.close(fd)
    assert got == data


# --- the port seam ---------------------------------------------------------------------

@pytest.fixture
def listener():
    """A socket of this process listening on a free loopback port."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen()
        yield sock.getsockname()[1]


def test_the_plans_connect_finds_nothing_in_a_plain_test(listener):
    from moxi.units import _plan

    assert _plan.answers is testing._nothing_answers
    assert _plan.answers("127.0.0.1", listener) is False


@pytest.mark.honest_ports
def test_an_honest_ports_test_has_the_plans_real_connect(listener):
    from moxi.units import _plan

    assert _plan.answers is not testing._nothing_answers
    assert _plan.answers("127.0.0.1", listener) is True


# --- the SQLite writer -----------------------------------------------------------------

SCHEMA = """
CREATE TABLE item (id INTEGER PRIMARY KEY, name TEXT NOT NULL);
CREATE TABLE tag (item INTEGER, tag TEXT);  -- a comment; and a ; inside 'a;b' text
"""


def test_write_sqlite_writes_an_ordinary_database(tmp_path):
    rows = {"item": [(i, f"n{i}") for i in range(5000)], "tag": [(1, "a;b")]}
    path = write_sqlite(tmp_path / "f.db", SCHEMA, rows)
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT count(*) FROM item").fetchone() == (5000,)
        assert conn.execute("SELECT tag FROM tag").fetchall() == [("a;b",)]
        assert conn.execute("PRAGMA integrity_check").fetchone() == ("ok",)


def test_write_sqlite_is_one_transaction(tmp_path):
    rows = {"item": [(1, "a"), (2, "b"), (1, "again")]}            # the last one clashes
    with pytest.raises(sqlite3.IntegrityError):
        write_sqlite(tmp_path / "f.db", SCHEMA, rows)
    with sqlite3.connect(tmp_path / "f.db") as conn:
        assert conn.execute("SELECT name FROM sqlite_master").fetchall() == []


# --- the symlink probe -----------------------------------------------------------------

@pytest.fixture
def fresh_probe():
    """`can_symlink` with no answer kept, before the test and after it."""
    testing.can_symlink.cache_clear()
    yield
    testing.can_symlink.cache_clear()


def refusing(error):
    """An `os.symlink` that raises `error`."""
    def symlink(*_args, **_kwargs):
        raise error
    return symlink


def with_winerror(error, winerror):
    """`error` with the Windows error code `winerror`, on any system."""
    error.winerror = winerror
    return error


def test_the_symlink_probe_tries_a_real_link_once_and_leaves_nothing(tmp_path, monkeypatch,
                                                                      fresh_probe):
    temp = tmp_path / "temp"
    temp.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(temp))
    tried = []
    real = os.symlink

    def symlink(target, link, *args, **kwargs):
        tried.append(Path(link))
        return real(target, link, *args, **kwargs)

    monkeypatch.setattr(os, "symlink", symlink)
    first = testing.can_symlink()
    assert testing.can_symlink() is first and len(tried) == 1, "the answer is kept"
    assert tried[0].parent.parent == temp, "tried in a temp directory of its own"
    assert list(temp.iterdir()) == []
    if not testing.WINDOWS:
        assert first is True


def test_only_a_missing_privilege_reads_as_no_and_the_skip_says_why(monkeypatch,
                                                                     fresh_probe):
    monkeypatch.setattr(os, "symlink", refusing(with_winerror(OSError(1, "no"), 1314)))
    assert testing.can_symlink() is False
    with pytest.raises(pytest.skip.Exception) as skipped:
        testing.need_symlinks()
    assert skipped.value.msg == testing.NO_SYMLINKS


@pytest.mark.parametrize("error", [
    testing.FenceError("refused by the fence"),
    with_winerror(OSError(1, "access is denied"), 5),
    OSError(1, "no winerror"),
], ids=["fence", "another-windows-error", "posix"])
def test_any_other_error_is_raised_not_taken_for_no(monkeypatch, fresh_probe, error):
    monkeypatch.setattr(os, "symlink", refusing(error))
    with pytest.raises(type(error)) as raised:
        testing.can_symlink()
    assert raised.value is error


# --- markers ---------------------------------------------------------------------------

def test_the_kit_registers_its_markers(pytestconfig):
    registered = {line.split(":", 1)[0].split("(", 1)[0]
                  for line in pytestconfig.getini("markers")}
    assert {"browser", "live", "slow", "build", "touches", "finding",
            "honest_fsync", "honest_ports"} <= registered
    assert set(testing.TIERS) <= registered


def test_a_package_deselects_the_tiers():
    import tomllib

    data = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text())
    addopts = data["tool"]["pytest"]["ini_options"]["addopts"]
    assert addopts == "-m 'not browser and not slow and not build and not live'"


# --- moxi_dev.contract -----------------------------------------------------------------

def test_the_contract_offers_the_check_and_nothing_more():
    assert sorted(moxi_dev.contract.__all__) == ["CheckError", "Finding", "check"]
    from moxi_dev.contract import CheckError, Finding, check
    assert callable(check) and issubclass(CheckError, Exception)
    assert str(Finding("src/a.py", 3, "1", "imports x")) == "src/a.py:3: check 1: imports x"


def test_the_core_no_longer_holds_the_check():
    import importlib.util

    assert importlib.util.find_spec("moxi.contract") is None


# --- stacks ----------------------------------------------------------------------------

#: Imports the kit, says so, reads the stacks time from stdin, arms, prints the file it
#: armed, and sleeps.
ARMED = """\
import os, sys, time
from moxi_dev import testing
print("ready", flush=True)
at = sys.stdin.readline().strip()
print(testing.arm_stacks({**os.environ, testing.STACKS_AT_ENV: at}), flush=True)
time.sleep(60)
"""


def test_a_process_still_running_at_the_stacks_time_writes_every_threads_stack(tmp_path):
    import subprocess

    stacks_dir = tmp_path / "stacks"
    env = {**os.environ, testing.STACKS_ENV: str(stacks_dir), "PYTEST_XDIST_WORKER": "gw3"}
    child = subprocess.Popen([sys.executable, "-c", ARMED], env=env, text=True,
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    try:
        assert child.stdout.readline().strip() == "ready"
        child.stdin.write(f"{time.time() + 0.5}\n")
        child.stdin.flush()
        path = Path(child.stdout.readline().strip())
        assert path == stacks_dir / f"gw3-{child.pid}.txt"
        wait_for(lambda: "most recent call first" in path.read_text(), testing.PROC_WAIT_S,
                 what="the armed process to write its stacks")
        assert 'File "<string>", line 6 in <module>' in path.read_text()
        assert child.poll() is None
    finally:
        child.kill()
        child.communicate()


@pytest.mark.parametrize("given", [
    {},
    {testing.STACKS_AT_ENV: "9999999999"},
    {testing.STACKS_ENV: "{stacks}"},
    {testing.STACKS_ENV: "{stacks}", testing.STACKS_AT_ENV: "{past}"},
    {testing.STACKS_ENV: "{stacks}", testing.STACKS_AT_ENV: "soon"},
])
def test_without_a_stacks_dir_and_a_time_to_come_nothing_is_armed(tmp_path, given):
    stacks_dir = tmp_path / "stacks"
    env = {key: value.format(stacks=stacks_dir, past=time.time() - 1)
           for key, value in given.items()}
    assert testing.arm_stacks(env) is None
    assert not stacks_dir.exists()

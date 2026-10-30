"""The runtime file: the writer, the remover, and the hardened read that never raises."""

from __future__ import annotations

import json
import os
import re
import sys
import threading

import pytest
from moxi.paths import Layout

from moxi_loopback import runtime
from moxi_loopback.check import TOKEN_PATTERN
from moxi_loopback.runtime import (
    RUNTIME_FILE,
    SIZE_MAX,
    RuntimeInfo,
    parse_runtime,
    read_runtime,
    remove_runtime,
    runtime_path,
    write_runtime,
)

MODULE = "demo"
posix = pytest.mark.skipif(sys.platform == "win32", reason="POSIX modes and links")
windows = pytest.mark.skipif(sys.platform != "win32", reason="Windows file sharing")

TOKEN = "A" * 43
SERVER_ID = "0123456789abcdef0123456789abcdef"


def good(**changes):
    data = {"format": 1, "module": MODULE, "server_id": SERVER_ID, "pid": 41237,
            "transport": "unix", "address": "/run/demo/page.sock", "port": None,
            "token": TOKEN, "version": "0.1.0", "started_at": "2026-10-30T12:00:00Z"}
    data.update(changes)
    return data


def drop(key):
    data = good()
    del data[key]
    return data


@pytest.fixture
def run_dir():
    return Layout(MODULE).runtime_dir(create=True)


def write_unix(**kwargs):
    return write_runtime(MODULE, transport="unix", address="/run/demo/page.sock", port=None,
                         version="0.1.0", **kwargs)


def put(run_dir, data, mode=0o600):
    path = run_dir / RUNTIME_FILE
    path.write_bytes(data if isinstance(data, bytes) else json.dumps(data).encode())
    os.chmod(path, mode)
    return path


# --- the writer ---------------------------------------------------------------------


def test_write_then_read_gives_the_same_info(run_dir):
    info = write_unix()
    assert read_runtime(MODULE) == info
    assert info.module == MODULE and info.pid == os.getpid()
    assert info.transport == "unix" and info.port is None and info.version == "0.1.0"
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", info.started_at)


def test_write_mints_a_token_the_reader_accepts_and_new_values_each_time(run_dir):
    first = write_unix()
    second = write_unix()
    assert TOKEN_PATTERN.match(first.token) and TOKEN_PATTERN.match(second.token)
    assert re.fullmatch(r"[0-9a-f]{32}", first.server_id)
    assert first.token != second.token and first.server_id != second.server_id
    assert read_runtime(MODULE) == second


def test_the_file_is_format_one_with_snake_case_keys(run_dir):
    info = write_runtime(MODULE, transport="tcp", address="127.0.0.1", port=40123,
                         version="1.2.3")
    raw = json.loads((run_dir / RUNTIME_FILE).read_text())
    assert raw == {"format": 1, "module": MODULE, "server_id": info.server_id,
                   "pid": os.getpid(), "transport": "tcp", "address": "127.0.0.1",
                   "port": 40123, "token": info.token, "version": "1.2.3",
                   "started_at": info.started_at}
    assert read_runtime(MODULE) == info


@posix
def test_the_file_is_mode_0600(run_dir):
    write_unix()
    assert (run_dir / RUNTIME_FILE).stat().st_mode & 0o777 == 0o600


def test_write_needs_the_runtime_dir_and_never_creates_it():
    with pytest.raises(FileNotFoundError):
        write_unix()
    assert not Layout(MODULE).runtime_dir().exists()


@pytest.mark.parametrize("transport, address, port", [
    ("udp", "127.0.0.1", 1),
    ("tcp", "0.0.0.0", 4000),
    ("tcp", "localhost", 4000),
    ("tcp", "127.0.0.1", 0),
    ("tcp", "127.0.0.1", 65536),
    ("tcp", "127.0.0.1", None),
    ("tcp", "127.0.0.1", True),
    ("unix", "", None),
    ("unix", "/run/demo/page.sock", 4000),
    ("unix", None, None),
])
def test_write_refuses_a_listener_that_does_not_fit_its_transport(run_dir, transport,
                                                                   address, port):
    with pytest.raises(ValueError):
        write_runtime(MODULE, transport=transport, address=address, port=port,
                      version="0.1.0")
    assert not (run_dir / RUNTIME_FILE).exists()


def test_the_token_is_never_in_the_repr(run_dir):
    info = write_unix()
    assert info.token not in repr(info)


# --- the remover --------------------------------------------------------------------


def test_remove_unlinks_only_its_own_file(run_dir):
    info = write_unix()
    assert remove_runtime(MODULE, "f" * 32) is False
    assert (run_dir / RUNTIME_FILE).exists()
    assert remove_runtime(MODULE, info.server_id) is True
    assert not (run_dir / RUNTIME_FILE).exists()
    assert remove_runtime(MODULE, info.server_id) is False


def test_remove_leaves_a_file_a_later_server_wrote(run_dir):
    old = write_unix()
    new = write_unix()
    assert remove_runtime(MODULE, old.server_id) is False
    assert read_runtime(MODULE) == new


@posix
def test_remove_leaves_a_file_the_reader_refuses(run_dir):
    put(run_dir, good(), mode=0o644)
    assert remove_runtime(MODULE, SERVER_ID) is False
    assert (run_dir / RUNTIME_FILE).exists()


# --- the hardened read --------------------------------------------------------------


def test_read_with_no_file_or_no_dir_is_none_and_creates_nothing():
    assert read_runtime(MODULE) is None
    assert not Layout(MODULE).runtime_dir().exists()
    assert read_runtime("not a module") is None


def test_read_keeps_unknown_keys(run_dir):
    put(run_dir, good(log="/x/log", later={"a": 1}))
    info = read_runtime(MODULE)
    assert info is not None
    assert dict(info.extra) == {"log": "/x/log", "later": {"a": 1}}
    with pytest.raises(TypeError):
        info.extra["log"] = "changed"


def test_read_refuses_a_file_written_for_another_module(run_dir):
    put(run_dir, good(module="other"))
    assert read_runtime(MODULE) is None


def test_read_takes_exactly_4096_bytes_and_refuses_4097(run_dir):
    text = json.dumps(good())
    put(run_dir, (text + " " * (SIZE_MAX - len(text))).encode())
    assert read_runtime(MODULE) is not None
    put(run_dir, (text + " " * (SIZE_MAX + 1 - len(text))).encode())
    assert read_runtime(MODULE) is None


@posix
def test_read_refuses_a_symlink(run_dir):
    target = put(run_dir, good())
    real = target.rename(run_dir / "real.json")
    (run_dir / RUNTIME_FILE).symlink_to(real)
    assert read_runtime(MODULE) is None


@posix
def test_read_refuses_a_fifo_without_blocking(run_dir):
    os.mkfifo(run_dir / RUNTIME_FILE, 0o600)
    result = []
    reader = threading.Thread(target=lambda: result.append(read_runtime(MODULE)), daemon=True)
    reader.start()
    reader.join(5)
    assert not reader.is_alive(), "the read blocked on a FIFO"
    assert result == [None]


@posix
@pytest.mark.parametrize("mode", [0o644, 0o640, 0o604, 0o660])
def test_read_refuses_a_file_open_to_others(run_dir, mode):
    put(run_dir, good(), mode=mode)
    assert read_runtime(MODULE) is None


@posix
def test_read_refuses_a_directory_open_to_others(run_dir):
    put(run_dir, good())
    os.chmod(run_dir, 0o750)
    assert read_runtime(MODULE) is None


@posix
def test_read_refuses_a_runtime_dir_that_is_a_link(run_dir, tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir(mode=0o700)
    put(elsewhere, good())
    run_dir.rmdir()
    run_dir.symlink_to(elsewhere)
    assert read_runtime(MODULE) is None


@posix
def test_read_refuses_a_directory_in_the_files_place(run_dir):
    (run_dir / RUNTIME_FILE).mkdir(mode=0o700)
    assert read_runtime(MODULE) is None


def _corpus():
    yield b""
    yield b"\xff\xfe not utf-8"
    yield "[" * 100_000
    yield "null"
    yield "[1, 2]"
    yield '"page"'
    yield "{" + '"a": 1, ' * 10 + "}"
    yield json.dumps(good())[:-1]
    for key in ("format", "module", "server_id", "pid", "transport", "address", "port",
                "token", "version", "started_at"):
        yield json.dumps(drop(key))
    for changes in (
        {"format": 2}, {"format": "1"}, {"format": True}, {"format": 1.0},
        {"module": 7}, {"module": ""},
        {"server_id": "XYZ"}, {"server_id": SERVER_ID.upper()}, {"server_id": None},
        {"pid": 0}, {"pid": -1}, {"pid": True}, {"pid": 1.5}, {"pid": "41237"},
        {"transport": "udp"}, {"transport": None},
        {"token": "A" * 42}, {"token": "A" * 44}, {"token": "A" * 42 + "="},
        {"token": "A" * 42 + "é"}, {"token": 7},
        {"address": 7}, {"address": ""},
        {"port": 4000},
        {"transport": "tcp", "address": "127.0.0.1", "port": 0},
        {"transport": "tcp", "address": "127.0.0.1", "port": 65536},
        {"transport": "tcp", "address": "127.0.0.1", "port": "4000"},
        {"transport": "tcp", "address": "127.0.0.1", "port": None},
        {"transport": "tcp", "address": "0.0.0.0", "port": 4000},
        {"version": 1}, {"started_at": 1700000000},
    ):
        yield json.dumps(good(**changes))


@pytest.mark.parametrize("data", list(_corpus()))
def test_parse_refuses_everything_but_format_one_and_never_raises(data):
    assert parse_runtime(data) is None


@pytest.mark.parametrize("data", [
    good(),
    good(transport="tcp", address="127.0.0.1", port=1),
    good(transport="tcp", address="127.0.0.1", port=65535),
    good(extra_key=[1, 2, {"x": None}]),
])
def test_parse_takes_format_one(data):
    info = parse_runtime(json.dumps(data))
    assert isinstance(info, RuntimeInfo)
    assert parse_runtime(json.dumps(data).encode()) == info


def test_runtime_path_is_in_the_runtime_dir():
    assert runtime_path(MODULE) == Layout(MODULE).runtime_dir() / "page.json"
    assert not Layout(MODULE).runtime_dir().exists()


# --- Windows ------------------------------------------------------------------------


@windows
def test_a_reader_holding_the_file_open_does_not_make_the_next_write_fail(run_dir):
    write_unix()
    handle = runtime._win_open(run_dir / RUNTIME_FILE)
    assert handle is not None
    try:
        second = write_unix()
        assert read_runtime(MODULE) == second
        assert remove_runtime(MODULE, second.server_id) is True
    finally:
        runtime._win_close(handle)


@windows
def test_the_windows_read_refuses_a_large_file(run_dir):
    text = json.dumps(good())
    put(run_dir, (text + " " * (SIZE_MAX + 1 - len(text))).encode())
    assert read_runtime(MODULE) is None

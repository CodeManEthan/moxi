"""The Codex limits feed: the host's read at each accepted load and Codex's pushes handed to
`codex_limits` rows, `usage.codex_limits`, the library's pull and the doctor's row."""

from __future__ import annotations

import json
import shutil
import tempfile
import time
from pathlib import Path

import pytest

import fake_codex
import listener_targets
import moxi_turns
from moxi_turns import codex_appserver, doctor
from moxi_turns import codex_protocol as proto
from moxi_turns.client import Session
from moxi_turns.codex import CodexHarness
from moxi_turns.endpoint import Endpoint
from moxi_turns.listeners import Listener, Registry
from test_codex_host import FakeAppServer, FakeClient


def _registry():
    return Registry((Listener("usage", frozenset({"codex_limits"}),
                             "listener_targets:limits"),))


def _host(turns_env, *, limits=None, limits_error=None):
    made = []
    harness = None

    def clients(**kwargs):
        client = FakeClient(harness, **kwargs)
        made.append(client)
        return client

    harness = CodexHarness(turns_env, window=False, ping=["/env/bin/moxi-turns-ping"],
                           client_factory=clients, app_server_factory=FakeAppServer)
    if limits is not None:
        harness.fake_limits = limits
    if limits_error is not None:
        harness.fake_limits_error = limits_error
    endpoint = Endpoint(harness, session_id=None, registry=_registry(),
                        env=harness.env).open()
    harness.start()
    return endpoint, harness, made


def _told(count, timeout=2.0):
    """The `codex_limits` rows' calls, once there are `count` of them."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        seen = [c for c in listener_targets.CALLS if c[0] == "codex_limits"]
        if len(seen) >= count:
            return seen
        time.sleep(0.01)
    return [c for c in listener_targets.CALLS if c[0] == "codex_limits"]


@pytest.fixture(autouse=True)
def _calls():
    listener_targets.CALLS.clear()
    yield
    listener_targets.CALLS.clear()


def _reads(client):
    return [m for m, _ in client.calls if m == "account/rateLimits/read"]


# -- the host's feed ------------------------------------------------------------------------

def test_each_accepted_load_reads_the_limits_once_and_tells_the_rows(turns_env):
    endpoint, harness, made = _host(turns_env)
    try:
        before = time.time()
        (first,) = _told(1)
        assert first[:3] == ("codex_limits", "read", fake_codex.LIMITS)
        assert first[4:] == ("codex", "thread-1")
        assert first[3] <= time.time() and first[3] > before - 5
        assert _reads(made[0]) == ["account/rateLimits/read"]
        # The read carries no params, and no turn ran for it.
        assert [p for m, p in made[0].calls if m == "account/rateLimits/read"] == [None]
        # A resume (another accepted load) reads again.
        harness._connect()
        assert [c[1] for c in _told(2)] == ["read", "read"]
        assert _reads(made[1]) == ["account/rateLimits/read"]
    finally:
        endpoint.stop("the test ended")


def test_a_push_reaches_the_rows_unparsed_as_updated(turns_env):
    endpoint, harness, made = _host(turns_env)
    try:
        _told(1)
        pushed = {"rateLimits": {"primary": {"usedPercent": 40}}, "accountId": "a",
                  "planType": "plus", "extra": [1, 2]}
        made[0].on_notification("account/rateLimits/updated", pushed, None)
        seen = _told(2)
        assert seen[1][1:3] == ("updated", pushed)
        # A push from a subagent's thread is the same account's: it is told too.
        made[0].on_notification("account/rateLimits/updated",
                                {"threadId": "child", **pushed}, None)
        assert _told(3)[2][1] == "updated"
        # The cached answer is the last reading seen.
        cached = harness.codex_limits(cached=True)
        assert cached["kind"] == "updated" and cached["payload"]["threadId"] == "child"
    finally:
        endpoint.stop("the test ended")


def test_a_load_whose_read_fails_still_loads_and_tells_no_row(turns_env):
    endpoint, harness, made = _host(
        turns_env, limits_error=proto.CodexError(-32603, "not available"))
    try:
        assert harness.session_id == "thread-1" and not harness.loading()
        time.sleep(0.2)
        assert _told(1, timeout=0.1) == []
        assert harness.codex_limits(cached=True) == {
            "payload": None, "received_at": None, "kind": None}
    finally:
        endpoint.stop("the test ended")


def test_usage_codex_limits_on_the_hosts_endpoint(turns_env):
    endpoint, harness, made = _host(turns_env, limits={"rateLimits": {}, "planType": "x"})
    session = Session(endpoint.address, endpoint.session(), directory=endpoint.dir)
    try:
        _told(1)
        assert "usage" in session.capabilities
        fresh = session.codex_limits()
        assert set(fresh) == {"payload", "received_at"}
        assert fresh["payload"] == {"rateLimits": {}, "planType": "x"}
        # A pull is kept as the last reading but not told to the rows.
        time.sleep(0.1)
        assert len(_told(1)) == 1
        cached = session.codex_limits(cached=True)
        assert cached == {**fresh, "kind": "read"}
        harness.fake_limits_error = proto.NoReply("timed out")
        failed = session.codex_limits()
        assert failed["payload"] is None and failed["received_at"] is None
        assert "timed out" in failed["reason"]
        assert session.codex_limits(cached=True) == cached
    finally:
        session.close()
        endpoint.stop("the test ended")


# -- the library's pull ---------------------------------------------------------------------

@pytest.fixture
def bare_path(monkeypatch, tmp_path):
    """A PATH with no `codex` on it, and a short temporary root for a pull's socket."""
    empty = tmp_path / "empty-bin"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    short = Path(tempfile.mkdtemp(prefix="mtl"))
    monkeypatch.setattr(tempfile, "tempdir", str(short))
    yield empty
    shutil.rmtree(short, ignore_errors=True)


@pytest.fixture
def stub_codex(bare_path, monkeypatch, tmp_path):
    """A stub `codex` first on PATH, recording into a folder of its own."""
    bin_dir = tmp_path / "bin"
    fake_codex.install(bin_dir)
    records = tmp_path / "records"
    records.mkdir()
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.setenv("FAKE_CODEX_DIR", str(records))
    return records


def test_the_pull_reads_a_live_hosts_limits(turns_env, bare_path):
    endpoint, harness, made = _host(turns_env)
    try:
        reading = moxi_turns.codex_rate_limits()
        assert isinstance(reading, moxi_turns.CodexLimits)
        assert (reading.payload, reading.via) == (fake_codex.LIMITS, "host")
        assert abs(reading.received_at - time.time()) < 5
    finally:
        endpoint.stop("the test ended")


def test_with_no_host_the_pull_starts_an_app_server_of_its_own(turns_env, stub_codex):
    reading = moxi_turns.codex_rate_limits(timeout=10)
    assert reading is not None and reading.via == "app-server"
    assert reading.payload == fake_codex.LIMITS
    # initialize and the read, on one app-server: no thread, so no message was sent.
    assert [c["method"] for c in fake_codex.calls(stub_codex)] == [
        "initialize", "account/rateLimits/read"]
    assert len(fake_codex.events(stub_codex, "app-server")) == 1
    assert len(fake_codex.events(stub_codex, "app-server-exit")) == 1
    assert list(Path(tempfile.gettempdir()).iterdir()) == []


def test_a_host_that_cannot_read_passes_the_pull_to_an_app_server(turns_env, stub_codex):
    endpoint, harness, made = _host(
        turns_env, limits_error=proto.CodexError(-32603, "not available"))
    try:
        reading = moxi_turns.codex_rate_limits(timeout=10)
        assert reading is not None and reading.via == "app-server"
    finally:
        endpoint.stop("the test ended")


def test_the_pull_is_none_with_no_host_and_no_binary(turns_env, bare_path, caplog):
    caplog.set_level("INFO", logger="moxi.turns.codex.appserver")
    assert moxi_turns.codex_rate_limits() is None
    assert "not on PATH" in caplog.text


def test_the_pull_is_none_when_the_app_server_errs(turns_env, stub_codex, monkeypatch,
                                                   caplog):
    monkeypatch.setenv("FAKE_CODEX_LIMITS", "error")
    caplog.set_level("INFO", logger="moxi.turns.codex.appserver")
    assert moxi_turns.codex_rate_limits(timeout=10) is None
    assert "not available" in caplog.text
    assert len(fake_codex.events(stub_codex, "app-server-exit")) == 1


def test_the_pull_is_none_past_its_timeout(turns_env, stub_codex):
    started = time.monotonic()
    assert moxi_turns.codex_rate_limits(timeout=0) is None
    assert time.monotonic() - started < 2
    assert fake_codex.events(stub_codex, "app-server") == []


# -- the doctor's row ---------------------------------------------------------------------

def _limits_row():
    return next(r for r in doctor.check() if r.name == "codex limits")


def test_the_doctor_row_with_no_live_codex_session(turns_env, bare_path):
    row = _limits_row()
    assert (row.state, row.detail) == ("ok", "no live Codex session")


def test_the_doctor_row_names_the_last_reading_a_live_host_saw(turns_env, bare_path):
    endpoint, harness, made = _host(turns_env)
    try:
        (told,) = _told(1)
        row = _limits_row()
        when = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(told[3]))
        assert row.state == "ok"
        assert row.detail == f"last reading {when} (read), Codex host pid {harness.pid}"
        # The row asked the host's record, not its app-server.
        assert _reads(made[0]) == ["account/rateLimits/read"]
    finally:
        endpoint.stop("the test ended")


def test_the_doctor_row_before_a_hosts_first_reading(turns_env, bare_path):
    endpoint, harness, made = _host(
        turns_env, limits_error=proto.CodexError(-32603, "not available"))
    try:
        row = _limits_row()
        assert (row.state, row.detail) == ("ok", "no reading yet on 1 live Codex host")
    finally:
        endpoint.stop("the test ended")


def test_a_claude_session_answers_usage_codex_limits_with_the_capability_error(
        make_endpoint):
    from fake_harness import FakeHarness
    from moxi import ipc

    from moxi_turns import results

    ep, _ = make_endpoint(FakeHarness(can=results.CAPABILITIES))
    client = ipc.connect(ipc.address("turns", ep.dir.name), timeout=5)
    try:
        client.hello((2, 2))
        with pytest.raises(ipc.RpcError) as caught:
            client.call("usage.codex_limits", {}, timeout=5)
        assert (caught.value.code, caught.value.data) == (
            results.ERROR_CAPABILITY, {"capability": "usage"})
    finally:
        client.close()


def test_codex_limits_is_a_frozen_value():
    value = codex_appserver.CodexLimits({"a": 1}, 1.5, "host")
    with pytest.raises(AttributeError):
        value.via = "x"
    assert json.loads(json.dumps(value.payload)) == {"a": 1}

"""The endpoint's wire: where it serves and what it leaves on disk, the session answer,
finding and sweeping, version 1's directories, watches, the stop handler and the `gone`
rule. Driven over `moxi.ipc` against the fake harness."""

from __future__ import annotations

import contextlib
import json
import os
import socket
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

import moxi_turns as turns
from fake_harness import FakeHarness
from moxi import ipc
from moxi_turns import listeners, protocol
from moxi_turns import results as R
from moxi_turns.results import Passed, StopFacts

pytestmark = pytest.mark.skipif(sys.platform == "win32",
                                reason="moxi.ipc's named pipe server comes later")


def runtime() -> Path:
    from moxi.paths import Layout

    return Layout("turns").runtime_dir()


@pytest.fixture
def connect():
    """`connect(ep)`: a client past `rpc.hello`, closed at the test's end."""
    clients = []

    def make(ep, *, hello=True):
        client = ipc.connect(ep.address, timeout=5)
        clients.append(client)
        if hello:
            client.hello((2, 2))
        return client

    yield make
    for client in clients:
        client.close()


def notes(client, count: int, timeout: float = 5.0) -> list[tuple[str, object]]:
    """The next `count` notifications on `client`."""
    out: list = []
    done = threading.Event()

    def read():
        for note in client.notifications():
            out.append(note)
            if len(out) == count:
                break
        done.set()

    threading.Thread(target=read, daemon=True).start()
    assert done.wait(timeout), f"only {out} of {count} notifications came"
    return out


# -- where the endpoint serves, and what it leaves on disk ------------------------------

def test_the_directory_the_address_and_hello_json(make_endpoint):
    ep, fake = make_endpoint()
    assert ep.dir == runtime() / "v2" / f"fake-{ep.nonce}"
    assert stat.S_IMODE(ep.dir.stat().st_mode) == 0o700
    assert ep.address == ipc.address("turns", f"fake-{ep.nonce}")
    assert stat.S_IMODE(os.stat(ep.address).st_mode) == 0o600
    hello = json.loads(ep.hello_path.read_text())
    assert hello == {"address": ep.address, "protocol": 2, **ep.session()}
    assert hello["session_id"] == "s-1" and hello["pid"] == fake.pid
    assert hello["endpoint_pid"] == os.getpid()


def test_hello_json_follows_the_session_id(make_endpoint):
    ep, _ = make_endpoint()
    ep.ready("clear", "s-2")
    hello = protocol.read_hello(ep.dir)
    assert (hello["session_id"], hello["earlier"]) == ("s-2", ["s-1"])


def test_the_session_answer_names_the_harness_and_the_endpoint(make_endpoint, connect):
    ep, _ = make_endpoint(FakeHarness(pid=4242))
    session = connect(ep).call("session", {}, timeout=5)
    assert (session["pid"], session["endpoint_pid"]) == (4242, os.getpid())
    assert session["harness"] == "fake" and session["nonce"] == ep.nonce
    assert session["session_id"] == "s-1" and "trouble" not in session


def test_the_hello_offers_the_sessions_capabilities_in_the_contracts_order(
        make_endpoint, connect):
    # A name the contract does not have is never offered.
    ep, _ = make_endpoint(FakeHarness(can=("steer", "start", "deliver", "hold")))
    client = connect(ep, hello=False)
    assert client.hello((2, 2)).capabilities == frozenset({"deliver", "steer"})
    assert ep.capabilities == ("deliver", "steer")


def test_stop_takes_the_directory_the_socket_and_its_lock(make_endpoint):
    ep, _ = make_endpoint()
    ep.stop("the test stopped it")
    assert not ep.dir.exists()
    assert not os.path.lexists(ep.address)
    assert not os.path.lexists(ep.address + ".lock")
    with pytest.raises(ipc.NotServing):
        ipc.connect(ep.address, timeout=1)


def test_framing_is_moxi_ipcs(make_endpoint, connect):
    ep, _ = make_endpoint()
    # A version 1 line gets no version 1 answer: nothing at a version 2 address speaks it.
    with socket.socket(socket.AF_UNIX) as raw:
        raw.settimeout(5)
        raw.connect(ep.address)
        raw.sendall(json.dumps(protocol.V1_HELLO).encode() + b"\n")
        answer = json.loads(raw.makefile("rb").readline())
    assert answer["jsonrpc"] == "2.0" and answer["error"]["code"] in (-32600, -32002)
    assert "v" not in answer
    client = connect(ep, hello=False)
    with pytest.raises(ipc.RpcError) as caught:
        client.call("facts", {}, timeout=5)
    assert caught.value.code == -32002
    client = connect(ep)
    with pytest.raises(ipc.RpcError) as caught:
        client.call("deliver", ["x"], timeout=5)
    assert caught.value.code == -32602


def test_probe(turns_env):
    # The hosts' internal legs still judge their own sockets by a probe.
    assert protocol.probe(turns_env / "nothing")[0] == protocol.STALE


# -- version 1's scan never reaches a version 2 endpoint --------------------------------

def _v1_scan(root: Path) -> list[Path]:
    """Version 1's finder, as the 0.1 library runs it: every directory directly under
    the runtime directory with a readable `hello.json`, a hello on its `sock`, and on a
    refused or missing socket a sweep of its files and the directory. Returns the
    directories whose hello answered."""
    found = []
    for directory in root.iterdir():
        try:
            hello = json.loads((directory / "hello.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(hello, dict):
            continue
        if protocol.v1_hello(directory, 1.0) is not None:
            found.append(directory)
            continue
        verdict, _ = protocol.probe(directory / "sock")
        if verdict == protocol.STALE:
            for name in ("hello.json", "sock", "mod.sock", ".hello.json.tmp",
                         "instructions.md"):
                with contextlib.suppress(OSError):
                    (directory / name).unlink()
            with contextlib.suppress(OSError):
                directory.rmdir()
    return found


def _dead_v2(name: str = "fake-dead") -> tuple[Path, str]:
    """A version 2 directory left by an endpoint that died: its `hello.json`, a socket
    file nothing listens on, and no lock holder."""
    directory = runtime() / "v2" / name
    directory.mkdir(parents=True, mode=0o700)
    address = ipc.address("turns", name)
    with socket.socket(socket.AF_UNIX) as s:
        s.bind(address)
    (directory / "hello.json").write_text(json.dumps(
        {"address": address, "protocol": 2, "session_id": "s-dead", "pid": 1}))
    return directory, address


def test_version_1s_scan_finds_and_removes_nothing_of_version_2(make_endpoint):
    ep, _ = make_endpoint()
    dead, dead_address = _dead_v2()
    before = sorted(p for p in runtime().rglob("*"))
    assert _v1_scan(runtime()) == []
    assert sorted(p for p in runtime().rglob("*")) == before
    assert protocol.v1_directories(runtime()) == []
    assert protocol.read_hello(ep.dir)["session_id"] == "s-1"
    assert os.path.lexists(dead_address)


# -- sweeping a version 2 directory -----------------------------------------------------

def test_a_dead_directory_goes_on_sweeps_word(turns_env):
    dead, address = _dead_v2()
    with pytest.raises(ipc.NotServing):
        ipc.connect(address, timeout=1)
    assert protocol.sweep(dead, address) == "removed"
    assert not dead.exists() and not os.path.lexists(address)
    assert not os.path.lexists(address + ".lock")


def test_a_directory_whose_socket_is_gone_goes_too(turns_env):
    dead, address = _dead_v2("fake-nosock")
    os.unlink(address)
    assert protocol.sweep(dead, address) == "absent"
    assert not dead.exists()


def test_a_live_endpoint_is_never_swept(make_endpoint):
    ep, _ = make_endpoint()
    assert protocol.sweep(ep.dir, ep.address) == "live"
    assert protocol.read_hello(ep.dir) is not None and os.path.lexists(ep.address)


def test_a_refused_connect_with_the_lock_held_removes_nothing(turns_env):
    # A server that holds its lock but refuses connects (its backlog, a slow start)
    # is live: the connect probe never decides.
    dead, address = _dead_v2("fake-held")
    holder = subprocess.Popen(
        [sys.executable, "-c",
         "import sys\nfrom moxi import files\n"
         "with files.lock(sys.argv[1], timeout=5):\n"
         "    print('held', flush=True)\n    sys.stdin.read()\n", address + ".lock"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "held"
        with pytest.raises(ipc.NotServing):
            ipc.connect(address, timeout=1)
        assert protocol.sweep(dead, address) == "live"
        assert (dead / "hello.json").exists() and os.path.lexists(address)
    finally:
        holder.stdin.close()
        holder.wait(10)


def test_a_starting_directory_has_no_hello_and_is_listed_as_such(turns_env):
    starting = runtime() / "v2" / "fake-starting"
    starting.mkdir(parents=True)
    assert protocol.v2_directories(runtime()) == [starting]
    assert protocol.read_hello(starting) is None


# -- version 1 directories --------------------------------------------------------------

class V1Endpoint:
    """A version 1 endpoint's directory and socket, answering version 1's hello."""

    def __init__(self, directory: Path, session_id: str):
        directory.mkdir(parents=True)
        self.dir = directory
        self.hello = {"harness": "claude", "pid": 4242, "session_id": session_id,
                      "earlier": [], "protocol": [1]}
        (directory / "hello.json").write_text(json.dumps(self.hello))
        self.server = socket.socket(socket.AF_UNIX)
        self.server.bind(str(directory / "sock"))
        self.server.listen(4)
        self.lines: list[dict] = []
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        while True:
            try:
                conn, _ = self.server.accept()
            except OSError:
                return
            with conn, conn.makefile("rwb") as f:
                line = f.readline()
                if not line:
                    continue
                frame = json.loads(line)
                self.lines.append(frame)
                f.write(json.dumps({"v": 1, "id": frame.get("id"), "ok": True,
                                    "hello": self.hello}).encode() + b"\n")
                f.flush()

    def close(self):
        # shutdown() first: close() alone leaves the thread in accept() holding it open.
        with contextlib.suppress(OSError):
            self.server.shutdown(socket.SHUT_RDWR)
        self.server.close()


def test_a_live_version_1_endpoint_answers_version_1s_hello(turns_env):
    v1 = V1Endpoint(runtime() / "claude-old", "s-old")
    try:
        before = sorted(os.listdir(v1.dir))
        assert protocol.v1_directories(runtime()) == [v1.dir]
        assert protocol.v1_hello(v1.dir)["session_id"] == "s-old"
        assert v1.lines == [protocol.V1_HELLO]
        assert sorted(os.listdir(v1.dir)) == before
    finally:
        v1.close()


def test_a_dead_version_1_directory_reads_as_no_answer_and_stays(turns_env):
    v1 = V1Endpoint(runtime() / "claude-gone", "s-gone")
    v1.close()                                   # its socket file stays, nothing listens
    before = sorted(os.listdir(v1.dir))
    assert protocol.v1_hello(v1.dir, 0.5) is None
    assert sorted(os.listdir(v1.dir)) == before
    assert protocol.v1_directories(runtime()) == [v1.dir]


# -- watches ----------------------------------------------------------------------------

def test_a_watch_gets_its_facts_then_every_notification_in_order(make_endpoint, connect):
    ep, fake = make_endpoint()
    watch = connect(ep)
    first = R.from_wire(watch.call("watch", {}, timeout=5)["facts"])
    assert (first.turn_number, first.turn_open, first.turn) == (0, False, None)
    client = connect(ep)
    ep.ready("startup", "s-1")
    opened = client.call("deliver", {"text": "one", "ref": "r1"}, timeout=5)
    steered = client.call("steer", {"text": "two", "ref": "r2"}, timeout=5)
    fake.end_turn("answer")
    fake.start_turn()                             # the person's own turn
    fake.end_turn("aborted")
    got = notes(watch, 5)
    one = R.DeliveryRef(opened["delivery"], "r1", "opened")
    two = R.DeliveryRef(steered["delivery"], "r2", "steered")
    assert [R.from_notification(m, p) for m, p in got] == [
        R.Ready("startup", "s-1"),
        R.TurnStarted("t1", "s-1", 1, (one,)),
        R.TurnEnded("t1", "s-1", 1, "answer", (one, two)),
        R.TurnStarted("t2", "s-1", 2, ()),
        R.TurnEnded("t2", "s-1", 2, "aborted", ()),
    ]


def test_a_watch_ends_with_session_gone(make_endpoint, connect):
    ep, _ = make_endpoint()
    watch = connect(ep)
    watch.call("watch", {}, timeout=5)
    threading.Timer(0.1, ep.stop, args=("the session ended",)).start()
    assert list(watch.notifications()) == [("session.gone", {"reason": "the session ended"})]


def test_facts_name_the_open_turn_and_the_last_ready(make_endpoint, connect):
    ep, fake = make_endpoint()
    client = connect(ep)
    facts = R.from_wire(client.call("facts", {}, timeout=5))
    assert facts.ready is None and facts.turn is None
    ep.ready("resume", "s-1")
    before = time.time()
    d = client.call("deliver", {"text": "x", "ref": "r1"}, timeout=5)
    facts = R.from_wire(client.call("facts", {}, timeout=5))
    assert facts.turn_open and facts.turn_number == 1
    assert facts.turn.opening == R.DeliveryRef(d["delivery"], "r1")
    assert facts.turn.turn_id == "t1" and facts.turn.command is None
    assert before - 1 <= facts.turn.started_at <= time.time()
    assert (facts.ready.source, facts.ready.session_id) == ("resume", "s-1")
    assert facts.ready.at is not None
    fake.end_turn()
    assert R.from_wire(client.call("facts", {}, timeout=5)).turn is None


def test_a_held_turn_reads_no_turn_until_its_tie(make_endpoint, connect):
    ep, fake = make_endpoint(FakeHarness(auto=False))
    client = connect(ep)
    d = client.call("deliver", {"text": "x", "ref": "r9"}, timeout=5)
    ep.turn_started("t1", held=True)
    facts = R.from_wire(client.call("facts", {}, timeout=5))
    assert facts.turn_open and facts.turn is None
    ep.turn_tied("t1", d["delivery"])
    facts = R.from_wire(client.call("facts", {}, timeout=5))
    assert facts.turn.opening == R.DeliveryRef(d["delivery"], "r9")


def test_every_delivery_value_names_its_delivery_and_its_ref(make_endpoint, connect):
    ep, fake = make_endpoint()
    client = connect(ep)
    d = client.call("deliver", {"text": "x", "ref": "r1"}, timeout=5)
    started = R.from_wire(client.call("wait", {"delivery": d["delivery"],
                                               "until": "started", "timeout_ms": 0},
                                      timeout=5)["result"])
    assert started == R.Started("t1", d["delivery"], "r1")
    by_ref = client.call("wait", {"ref": "r1", "until": "ended", "timeout_ms": 0},
                         timeout=5)
    assert by_ref == {"delivery": d["delivery"], "pending": True}
    fake.end_turn()
    ended = client.call("wait", {"ref": "r1", "until": "ended", "timeout_ms": 1000},
                        timeout=5)
    assert ended["delivery"] == d["delivery"]
    assert R.from_wire(ended["result"]) == R.Ended("t1", "answer", d["delivery"], "r1")
    idle = client.call("steer", {"text": "y", "ref": "r2"}, timeout=5)
    assert R.from_wire(idle["result"]) == R.Refused("idle", idle["delivery"], "r2")


# -- the stop handler -------------------------------------------------------------------

def test_the_stop_handler_answers_each_stop_on_its_connection(make_endpoint, connect):
    ep, _ = make_endpoint()
    handler = connect(ep)
    assert handler.call("stops.handle", {}, timeout=5) == {}
    answers: list = []
    asked = threading.Thread(target=lambda: answers.append(
        ep.on_stop(StopFacts("t1", "s-1", False, ("shell",), 0))))
    asked.start()
    [(method, params)] = notes(handler, 1)
    assert method == "stop.asked"
    assert R.from_wire(params["facts"]) == StopFacts("t1", "s-1", False, ("shell",), 0)
    assert handler.call("stops.answer", {"ask": params["ask"], "reason": "keep going"},
                        timeout=5) == {"taken": True}
    asked.join(5)
    assert answers == ["keep going"]
    assert handler.call("stops.answer", {"ask": params["ask"], "reason": None},
                        timeout=5) == {"taken": False}
    assert handler.call("stops.answer", {"ask": 999, "reason": None},
                        timeout=5) == {"taken": False}


def test_a_late_answer_is_not_taken_and_a_closed_handler_frees_the_slot(
        make_endpoint, connect):
    ep, _ = make_endpoint()
    ep.hold_wait_s = 0.2
    handler = connect(ep)
    handler.call("stops.handle", {}, timeout=5)
    assert ep.on_stop(StopFacts()) is None               # nobody answered in time
    [(_, params)] = notes(handler, 1)
    assert handler.call("stops.answer", {"ask": params["ask"], "reason": "late"},
                        timeout=5) == {"taken": False}
    other = connect(ep)
    with pytest.raises(ipc.RpcError) as caught:
        other.call("stops.handle", {}, timeout=5)
    assert caught.value.code == R.ERROR_STOP_HANDLER_TAKEN
    handler.close()
    deadline = time.monotonic() + 5
    while True:
        try:
            assert other.call("stops.handle", {}, timeout=5) == {}
            break
        except ipc.RpcError:
            assert time.monotonic() < deadline
            time.sleep(0.05)


# -- the gone rule ----------------------------------------------------------------------

class ExitPasses(FakeHarness):
    """Claude Code's way with `/exit`: the gate lets it through and the endpoint answers
    `passed`, while the harness process may stay up (its exit dialog)."""

    def run_command(self, request):
        if request.name == "exit" and self.endpoint.command_entering(request.id) is None:
            self.endpoint.command_settled(request.id, Passed())
            return
        super().run_command(request)


def test_an_exit_that_passes_leaves_the_endpoint_answering(make_endpoint, connect):
    released = threading.Event()
    gone_called = threading.Event()

    def on_gone(event):
        gone_called.set()
        released.wait(10)

    registry = listeners.Registry((listeners.Listener(
        "test", frozenset({"gone"}), "test:on_gone", _fn=on_gone),))
    env = dict(os.environ, MOXI_TURNS_COMMANDS="exit")
    ep, _ = make_endpoint(ExitPasses(can=(*FakeHarness().can, "agents")),
                          registry=registry, env=env)
    ep.listen_wait_s = 10
    watch = connect(ep)
    watch.call("watch", {}, timeout=5)
    client = connect(ep)
    result = client.call("command", {"name": "exit"}, timeout=5)
    assert R.from_wire(result["result"]) == Passed()
    # The harness is still up: every method answers.
    assert R.from_wire(client.call("facts", {}, timeout=5)).session_id == "s-1"
    assert R.from_wire(client.call("agents", {}, timeout=5)).complete is False
    assert "facts" in connect(ep).call("watch", {}, timeout=5)
    assert client.call("session", {}, timeout=5)["nonce"] == ep.nonce
    # The endpoint's own end: from then on each method answers `gone`.
    stopping = threading.Thread(target=ep.stop, args=("the harness process exited",))
    stopping.start()
    try:
        assert gone_called.wait(5)
        for method in ("session", "facts", "agents", "watch"):
            with pytest.raises(ipc.RpcError) as caught:
                client.call(method, {}, timeout=5)
            assert caught.value.code == R.ERROR_GONE
    finally:
        released.set()
        stopping.join(10)
    assert list(watch.notifications()) == [
        ("session.gone", {"reason": "the harness process exited"})]


# -- the doctor's rows and the announcement ---------------------------------------------

def test_check_rows(make_endpoint, turns_env):
    from conftest import write_config

    ep, _ = make_endpoint()
    rows = {r.name: r for r in turns.check()}
    assert rows["config"].ok and "hold.wait_ms 2000" in rows["config"].detail
    assert rows["endpoints"].detail.startswith("1 live")
    assert any(name.startswith("endpoint fake pid") for name in rows)
    write_config(turns_env, "[hold]\nwait_ms = 'soon'\n")
    rows = {r.name: r for r in turns.check()}
    assert not rows["config"].ok and "hold.wait_ms" in rows["config"].detail


def test_the_announcement_loads_with_interface_1():
    from moxi import modules

    modules.reset()
    found = modules.load("turns", interface=1)
    assert found.state == modules.PRESENT, found.reason
    assert found.api is turns and found.settings == "settings.toml"

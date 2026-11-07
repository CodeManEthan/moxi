"""Compaction on the Codex host, and stopping its background terminals with it
(`stop_shells`), against synthetic app-server answers and the host's own hook server."""

from __future__ import annotations

import socket
import sys
import threading
import time

import pytest

from moxi_turns import codex as codex_module
from moxi_turns import codex_protocol as proto
from moxi_turns import protocol
from moxi_turns.client import Session, TurnsError
from moxi_turns.codex import CodexHarness
from moxi_turns.endpoint import Endpoint
from moxi_turns.results import Failed, LeftShell, Ran, Refused, Shells
from conftest import end_endpoint
from test_codex_host import FakeAppServer, FakeClient

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="no pipe server yet")

UNLISTED = LeftShell(None, "unlisted")


def missing(method):
    return proto.CodexError(proto.METHOD_NOT_FOUND, f"method not found: {method}")


def post_compact(harness):
    """Codex's PostCompact hook, as its ping sends it."""
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.connect(str(harness.hook_server.socket_path))
    conn = protocol.LineConn(sock)
    try:
        conn.send({"event": "PostCompact", "session_id": harness.session_id,
                   "source": "compact"})
        return conn.recv(3.0)
    finally:
        conn.close()


class CompactClient(FakeClient):
    """Codex runs `thread/compact/start` as a turn of its own: `turn/started`, the
    PostCompact hook, `turn/completed`. Its background terminals answer the list and
    `terminate` calls."""

    def __init__(self, harness, **kwargs):
        super().__init__(harness, **kwargs)
        self.terminals: list[str] = []
        #: Raised by the next list calls, in order (None: answer).
        self.list_errors: list = []
        #: process id -> an exception to raise, or a `terminated` answer that leaves the
        #: terminal running (False), or a number of seconds to hang.
        self.terminate_answers: dict = {}
        self.compact_error = None
        self.hold_compact = False     # True: the compaction turn waits for `finish()`
        self.compactions = 0
        #: Terminals that start once the compaction has run, before its Ready.
        self.start_at_compact: list[str] = []

    def call(self, method, params=None, timeout=None):
        if method == "thread/backgroundTerminals/list":
            self.calls.append((method, params))
            error = self.list_errors.pop(0) if self.list_errors else None
            if isinstance(error, (int, float)):
                time.sleep(min(error, timeout or error))
                raise proto.NoReply("Codex app-server timed out on the list")
            if error is not None:
                raise error
            return {"data": [{"processId": p, "command": "sleep 99"}
                             for p in self.terminals]}
        if method == "thread/backgroundTerminals/terminate":
            self.calls.append((method, params))
            process_id = params["processId"]
            answer = self.terminate_answers.get(process_id, True)
            if isinstance(answer, BaseException):
                raise answer
            if isinstance(answer, (int, float)) and not isinstance(answer, bool):
                time.sleep(min(answer, timeout or answer))
                raise proto.NoReply("Codex app-server timed out on terminate")
            if answer is True and process_id in self.terminals:
                self.terminals.remove(process_id)
                return {"terminated": True}
            return {"terminated": False}
        if method == "thread/compact/start":
            self.calls.append((method, params))
            if self.compact_error is not None:
                raise self.compact_error
            self.compactions += 1
            turn_id = f"compact-{self.compactions}"
            self.live_turn = turn_id
            self.on_notification("turn/started", {"turn": {"id": turn_id}}, None)
            if not self.hold_compact:
                threading.Thread(target=self.finish, daemon=True).start()
            return {}
        return super().call(method, params, timeout)

    def finish(self):
        """The compaction runs: its Ready (PostCompact), then the turn completes."""
        self.terminals.extend(self.start_at_compact)
        post_compact(self.harness)
        self.complete()


@pytest.fixture
def codex(turns_env, monkeypatch):
    monkeypatch.setenv("MOXI_TURNS_COMMANDS", "compact")
    hosts = []

    def make():
        harness = None
        made = []

        def clients(**kwargs):
            client = CompactClient(harness, **kwargs)
            made.append(client)
            return client

        harness = CodexHarness(
            turns_env, window=False, ping=["/env/bin/moxi-turns-ping"],
            client_factory=clients, app_server_factory=FakeAppServer,
        )
        endpoint = Endpoint(harness, session_id=None).open()
        hosts.append(endpoint)
        harness.start()
        session = Session(endpoint.address, endpoint.session(), directory=endpoint.dir)
        return session, endpoint, harness, made[0]

    yield make
    for endpoint in hosts:
        end_endpoint(endpoint)


def methods(app, *names):
    return [m for m, _ in app.calls if not names or m in names]


def watch(session):
    events = []
    threading.Thread(target=lambda: events.extend(session.watch()), daemon=True).start()
    time.sleep(0.2)
    return events


def test_the_codex_host_offers_compact_and_stop_shells_but_not_clear_or_exit(codex):
    s, ep, harness, app = codex()
    assert {"compact", "stop-shells"} <= s.capabilities
    assert not {"clear", "exit"} & s.capabilities
    with pytest.raises(TurnsError) as caught:
        s.command("clear")
    assert caught.value.code == 1002


def test_compact_runs_as_a_turn_and_settles_ran_at_its_ready(codex):
    s, ep, harness, app = codex()
    events = watch(s)
    app.hold_compact = True
    out = []
    t = threading.Thread(target=lambda: out.append(s.command("compact", timeout=5)),
                         daemon=True)
    t.start()
    assert until(lambda: app.compactions == 1)
    time.sleep(0.2)
    assert out == []              # the send's answer alone settles nothing
    facts = s.facts()
    assert facts.turn_open and facts.turn.command == "compact" and facts.turn.opening is None
    app.finish()
    t.join(5)
    assert out == [Ran(None)]
    assert until(lambda: any(type(e).__name__ == "TurnEnded" for e in events))
    started = next(e for e in events if type(e).__name__ == "TurnStarted")
    assert started.command == "compact" and started.deliveries == ()
    assert [e.source for e in events if type(e).__name__ == "Ready"][-1] == "compact"
    assert methods(app, "thread/compact/start") == ["thread/compact/start"]
    assert ep.ledger.session_id == "thread-1"


def test_a_terminal_alone_holds_a_plain_compact_but_not_one_that_stops_shells(codex):
    s, ep, harness, app = codex()
    app.terminals = ["p1"]
    assert s.command("compact") == Refused("busy-agents")
    assert methods(app, "thread/compact/start") == []
    result = s.command("compact", stop_shells=True, timeout=10)
    assert result == Ran(None, Shells(("p1",), (), True))
    assert app.terminals == []
    # The pair counted once toward the rate.
    assert s.command("compact", stop_shells=True) == Refused("rate")


def test_the_terminals_are_stopped_only_after_the_compaction_took_effect(codex):
    s, ep, harness, app = codex()
    app.terminals = ["p1", "p2"]
    result = s.command("compact", stop_shells=True, timeout=10)
    assert result == Ran(None, Shells(("p1", "p2"), (), True))
    order = methods(app, "thread/backgroundTerminals/list", "thread/compact/start",
                    "thread/backgroundTerminals/terminate")
    first_terminate = order.index("thread/backgroundTerminals/terminate")
    assert order.index("thread/compact/start") < first_terminate
    assert order[-1] == "thread/backgroundTerminals/list"     # the list again


def test_the_stop_lists_the_terminals_again_as_it_begins(codex):
    s, ep, harness, app = codex()
    app.terminals = ["p1"]
    app.start_at_compact = ["p2"]
    result = s.command("compact", stop_shells=True, timeout=10)
    assert result == Ran(None, Shells(("p1", "p2"), (), True))


def test_a_vouched_empty_list_stops_nothing_and_says_so(codex):
    s, ep, harness, app = codex()
    assert s.command("compact", stop_shells=True, timeout=10) == Ran(None, Shells((), (), True))
    assert methods(app, "thread/backgroundTerminals/terminate") == []


def test_a_codex_without_the_list_call_refuses_unsupported_and_runs_nothing(codex):
    s, ep, harness, app = codex()
    # The endpoint's snapshot reads the live facts first; then the host's own list.
    app.list_errors = [None, missing("thread/backgroundTerminals/list")]
    assert s.command("compact", stop_shells=True) == Refused("unsupported")
    assert methods(app, "thread/compact/start") == []


@pytest.mark.parametrize("error", [proto.CodexError(-32603, "internal error"), 2.5],
                         ids=["error", "timeout"])
def test_a_list_that_fails_still_runs_the_compaction_and_is_not_vouched(codex, error):
    s, ep, harness, app = codex()
    app.terminals = ["p1"]
    app.list_errors = [None, error]
    result = s.command("compact", stop_shells=True, timeout=15)
    assert result == Ran(None, Shells((), (UNLISTED,), False))
    assert methods(app, "thread/compact/start") == ["thread/compact/start"]
    assert methods(app, "thread/backgroundTerminals/terminate") == []
    assert app.terminals == ["p1"]


def test_a_list_that_fails_as_the_stop_begins_stops_the_earlier_list(codex):
    s, ep, harness, app = codex()
    app.terminals = ["p1"]
    # The lists in order: the endpoint's snapshot, the host's list before the send, the
    # re-check's live read, then the list as the stop begins, which fails.
    app.list_errors = [None, None, None, proto.CodexError(-32603, "busy")]
    result = s.command("compact", stop_shells=True, timeout=10)
    assert result == Ran(None, Shells(("p1",), (UNLISTED,), False))


def test_a_codex_without_terminate_leaves_each_terminal_unsupported(codex):
    s, ep, harness, app = codex()
    app.terminals = ["p1", "p2"]
    app.terminate_answers["p1"] = missing("thread/backgroundTerminals/terminate")
    result = s.command("compact", stop_shells=True, timeout=10)
    assert result == Ran(None, Shells((), (LeftShell("p1", "unsupported"),
                                           LeftShell("p2", "unsupported")), True))
    assert len(methods(app, "thread/backgroundTerminals/terminate")) == 1


def test_a_terminal_still_listed_or_refused_is_left_with_its_words(codex):
    s, ep, harness, app = codex()
    app.terminals = ["p1", "p2", "p3"]
    app.terminate_answers["p1"] = False
    app.terminate_answers["p2"] = proto.CodexError(-32603, "no such process")
    result = s.command("compact", stop_shells=True, timeout=10)
    assert result.shells.stopped == ("p3",)
    assert result.shells.complete is True
    assert result.shells.left == (LeftShell("p1", "Codex did not terminate it"),
                                  LeftShell("p2", "no such process (code -32603)"))


def test_a_second_list_that_fails_trusts_codexs_terminate_answers(codex):
    s, ep, harness, app = codex()
    app.terminals = ["p1", "p2"]
    app.terminate_answers["p2"] = False
    app.list_errors = [None, None, None, None, proto.CodexError(-32603, "list broke")]
    result = s.command("compact", stop_shells=True, timeout=10)
    assert result == Ran(None, Shells(
        ("p1",), (LeftShell("p2", "list broke (code -32603)"),), True))


def test_a_stop_past_its_bound_leaves_the_rest_timeout(codex, monkeypatch):
    monkeypatch.setattr(codex_module, "SHELL_STOP_S", 0.5)
    s, ep, harness, app = codex()
    app.terminals = ["p1", "p2"]
    app.terminate_answers["p1"] = 5.0       # hangs past the bound
    result = s.command("compact", stop_shells=True, timeout=10)
    assert result.shells.stopped == ()
    assert {(e.id, e.why) for e in result.shells.left} == {("p1", "timeout"),
                                                          ("p2", "timeout")}
    assert result.shells.complete is True


@pytest.mark.parametrize("error,result", [
    (missing("thread/compact/start"), Refused("unsupported")),
    (proto.CodexError(-32603, "compaction refused"), None),
    (proto.AppServerOffline("not connected"), Refused("loading")),
], ids=["method-not-found", "error", "never-sent"])
def test_a_compaction_codex_does_not_run_stops_nothing(codex, error, result):
    s, ep, harness, app = codex()
    app.terminals = ["p1"]
    app.compact_error = error
    answer = s.command("compact", stop_shells=True, timeout=10)
    if result is None:
        assert isinstance(answer, Failed) and "compaction refused" in answer.reason
    else:
        assert answer == result
    assert methods(app, "thread/backgroundTerminals/terminate") == []
    assert app.terminals == ["p1"]


def test_an_open_turn_refuses_the_compaction_before_anything_is_sent(codex):
    s, ep, harness, app = codex()
    delivery = s.deliver("first", only_if_idle=True)
    assert delivery.wait_started(2).turn_id == "turn-1"
    assert s.command("compact", stop_shells=True) == Refused("busy-turn")
    assert methods(app, "thread/compact/start",
                   "thread/backgroundTerminals/terminate") == []
    app.complete()


def test_a_compaction_with_no_ready_expires_and_a_late_ready_stops_nothing(codex):
    s, ep, harness, app = codex()
    ep.pending_ttl_s = 0.5
    app.terminals = ["p1"]
    app.hold_compact = True
    result = s.command("compact", stop_shells=True, timeout=10)
    assert isinstance(result, Failed) and result.reason.startswith("expired")
    app.finish()
    time.sleep(0.3)
    assert methods(app, "thread/backgroundTerminals/terminate") == []
    assert app.terminals == ["p1"]


def until(cond, timeout=3.0):
    deadline = time.monotonic() + timeout
    while not cond() and time.monotonic() < deadline:
        time.sleep(0.01)
    return cond()

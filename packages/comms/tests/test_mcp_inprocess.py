"""comms' tools reach the routing core in the daemon's own process (`mcp.inprocess`).

The same request through `InProcessClient` and over the daemon socket gets the same
answer, the same error and the same effect on the record; the same send through
`sending.run` as a bound sender and as the session's claim gets the same `SendResult`,
refusals included. A bound sender's refused credential is read again once from the
daemon, never from a claim socket. Tool bodies run on the host's pool, at most eight at
once, and the rest of the pool keeps answering. No path sends under a reserved name.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import hashlib
import itertools
import json
import re
import threading
import time
from types import SimpleNamespace

import pytest
from conftest import projects_root, wait_for_payload, wait_until, warm_session

from moxi_comms import api, claimfile, reserved, secrets, sending, wire
from moxi_comms.claimhold import ClaimServer
from moxi_comms.client import DaemonClient, DaemonError, DaemonOffline
from moxi_comms.daemon import Daemon
from moxi_comms.mcp import inprocess, tokens
from moxi_comms.membership import ProcView, Root
from moxi_comms.paths import daemon_socket

PLANNER = "planner@proj-a"
CLAIM = "inproc-planner-1"
CONSOLE = "console"
LONG = "x" * 20000


# --------------------------------------------------------------------------- the rig

class Planner:
    """planner@proj-a, held by a bare daemon client under claim `CLAIM` with its tool
    token registered, and a claim server this process holds for the same binding: the
    socket path sends through the server, the in-process path as the bound sender."""

    def __init__(self, client, answer, server):
        self.client, self.answer, self.server = client, answer, server

    @property
    def token(self) -> str:
        return self.answer["token"]


@pytest.fixture
def planner(rig, bind_answered):
    client, answer = bind_answered(
        name="planner", scope="proj-a", adapter="claude-shim", session_id=warm_session(),
        projects_root=str(projects_root()), turned=True, claim=CLAIM)
    client.mcp_token(tokens.digest(tokens.mint()))
    server = ClaimServer("planner", "proj-a", kind="claude-shim").start()
    server.set_bound(answer["identity"]["tier"], token=answer["token"])
    yield Planner(client, answer, server)
    server.stop()


@pytest.fixture
def reviewer(rig):
    return rig.shim(name="reviewer")


@pytest.fixture
def console(rig):
    """`console`, reserved for the launcher."""
    reserved.reconcile(declared={"launcher": [CONSOLE]}, chain=secrets.from_config())
    return CONSOLE


def args_for(to, text, *, posture="none", reply_to=None, new=False, new_name=False,
             cap=None, check=False):
    """The verb's fields, as `api.send` builds them."""
    args = SimpleNamespace(text=str(text), posture=posture, cap=cap, no_cap=False,
                           audience=None, new=new, held=False, standing=False,
                           new_name=new_name, interrupt=False, expires=None, check=check,
                           reply_to=None, message_id=None)
    tags = None
    if to is None:
        args.message_id = str(reply_to)
    else:
        tags = [t.strip() for t in to.split(",") if t.strip()]
        args.reply_to = None if reply_to is None else str(reply_to)
    return args, tags


def over_socket(planner, to, text, *, check=False, **kw):
    verb = api.check if check else api.send
    return verb(api.Sender.claim(planner.server), to, text, **kw)


def in_process(daemon, to, text, *, sender=None, **kw):
    args, tags = args_for(to, text, **kw)
    sender = sender or inprocess.bound_sender(daemon, CLAIM)
    assert sender is not None
    return sending.run(inprocess.context(daemon, deadline=30.0), args, tags=tags,
                       sender=sender)


def _ids(text: str) -> str:
    return re.sub(r"\b\d{2,}\b", "<id>", text)


def shape(result) -> tuple:
    """A send's result with what differs between two sends (the message id, a cold-at
    second) taken out."""
    refusal = (None if result.refusal is None
               else (result.refusal.code, _ids(result.refusal.text)))
    return (result.outcome, result.exit_code, result.posted, refusal,
            tuple((r.qualified, r.delivery) for r in result.recipients),
            tuple(_ids(n) for n in result.notes), tuple(_ids(s) for s in result.lines()))


def no_cold_at(text: str) -> str:
    head, sep, rest = text.partition("\n")
    return re.sub(r" \[cold-at:[0-9,-]+\]", "", head) + sep + rest


def socket_client() -> DaemonClient:
    return DaemonClient(daemon_socket()).connect()


#: In `both`'s params, the binding's own credential: its token over the socket, the
#: token's hash in process.
OWN = object()


def both(daemon, method, params, planner=None):
    """`method` over the socket and in process: each one's answer, or its error as
    (class, code, data, text). A param given as `OWN` is planner's credential as each
    path presents it."""
    out = []
    for client in (socket_client(), inprocess.InProcessClient(daemon)):
        own = (planner.token if not isinstance(client, inprocess.InProcessClient)
               else daemon.live_credential(CLAIM)[1]) if planner is not None else None
        try:
            out.append(client.call(method, {k: own if v is OWN else v
                                            for k, v in params.items()}))
        except DaemonError as e:
            out.append((type(e), e.code, e.data, str(e)))
        finally:
            client.close()
    return out


# ============================================================= client: one dispatch

@pytest.mark.parametrize("method,params", [
    ("resolve", {"name": "reviewer"}),
    ("resolve", {"name": "nobody-yet"}),
    ("agents", {}),
    ("max_id", {}),
    ("mcp_endpoint", {}),
    ("owner", {"session_id": "no-such-session"}),
    ("attributed_message", {"message_id": "12345"}),
    ("resolve", {}),
])
def test_a_read_answers_the_same_in_process_and_over_the_socket(rig, planner, reviewer,
                                                                 method, params):
    socket_answer, in_process_answer = both(rig.daemon, method, params)
    assert socket_answer == in_process_answer


def test_a_session_read_answers_the_same_both_ways(rig, planner, reviewer):
    socket_answer, in_process_answer = both(rig.daemon, "session", {"identity": "reviewer"})
    assert socket_answer["bound"] and in_process_answer["bound"]
    assert set(socket_answer) == set(in_process_answer)
    steady = ("bound", "adapter", "session_id", "bound_at")
    assert ({k: socket_answer.get(k) for k in steady}
            == {k: in_process_answer.get(k) for k in steady})


def test_refusals_are_the_same_errors_from_the_same_codes(rig, planner, console):
    cases = [
        ("send", {"as": PLANNER, "token": "not-the-token", "content": "::to reviewer\nx"},
         wire.CREDENTIAL),
        ("send", {"as": PLANNER, "token": OWN, "content": LONG}, wire.TOO_LONG),
        ("send", {"as": PLANNER, "token": OWN,
                  "content": f"::to {CONSOLE} [no reply]\nreport"}, wire.NOT_MAILABLE),
        ("send", {"token": OWN, "content": "x"}, wire.INVALID_PARAMS),
        ("delete", {"as": PLANNER, "token": OWN, "message_id": "99999"},
         wire.UNKNOWN_MESSAGE),
        ("delete", {"as": PLANNER, "token": "nope", "message_id": "1"}, wire.CREDENTIAL),
    ]
    for method, params, code in cases:
        socket_answer, in_process_answer = both(rig.daemon, method, params, planner)
        assert socket_answer == in_process_answer, method
        assert socket_answer[0] is DaemonError and socket_answer[1] == code


def test_the_hash_is_no_credential_over_the_socket_nor_the_token_in_process(rig, planner):
    held = rig.daemon.live_credential(CLAIM)[1]
    content = "::to reviewer [no reply]\nx"
    for client, credential in ((socket_client(), held),
                               (inprocess.InProcessClient(rig.daemon), planner.token)):
        try:
            with pytest.raises(DaemonError) as refused:
                client.send(PLANNER, credential, content)
            assert refused.value.code == wire.CREDENTIAL
            with pytest.raises(DaemonError) as refused:
                client.delete(PLANNER, credential, "1")
            assert refused.value.code == wire.CREDENTIAL
        finally:
            client.close()


def test_a_send_through_each_lands_the_same_in_the_record(rig, planner, reviewer):
    content = "::to reviewer [no reply]\nthe same words"
    ids = []
    credentials = (planner.token, rig.daemon.live_credential(CLAIM)[1])
    for client, credential in zip((socket_client(), inprocess.InProcessClient(rig.daemon)),
                                  credentials):
        try:
            ids.append(client.send(PLANNER, credential, content)["message_id"])
        finally:
            client.close()
    first, second = (rig.record.get_message(i) for i in ids)
    assert (first.author_kind, first.author_id, first.content) \
        == (second.author_kind, second.author_id, second.content)
    for message_id in ids:
        payload = wait_for_payload(reviewer, message_id)
        assert payload["author"]["name"] == PLANNER


def test_the_in_process_connection_has_no_peer_and_holds_no_binding(rig):
    client = inprocess.InProcessClient(rig.daemon)
    assert client.connect() is client and client.connected
    assert client._conn.peer is None and client._conn.in_process is True
    assert "mcp" in client.capabilities
    before = dict(rig.daemon.bindings)
    for method, params in (("bind", {"name": "sneaky", "adapter": "claude-shim"}),
                           ("mcp_token", {"hash": tokens.digest("x")}),
                           ("turn_ended", {}), ("open_digest", {})):
        with pytest.raises(DaemonError) as refused:
            client.call(method, params)
        assert refused.value.code == wire.METHOD_NOT_FOUND
    with pytest.raises(DaemonError):
        client.notify("activity", {"identity": "x"})
    assert rig.daemon.bindings == before
    client.close()
    assert client.call("max_id", {}) == socket_client().call("max_id", {})


def test_a_stopped_daemon_is_offline_in_process(rig, planner):
    daemon = rig.daemon
    client = inprocess.InProcessClient(daemon)
    sender = inprocess.bound_sender(daemon, CLAIM)
    daemon.stop()
    assert not client.connected
    with pytest.raises(DaemonOffline):
        client.call("max_id", {})
    got = in_process(daemon, "reviewer", "hi", sender=sender)
    assert (got.outcome, got.refusal.code) == ("offline", "offline")


def test_answers_and_errors_come_through_json(rig, monkeypatch):
    client = inprocess.InProcessClient(rig.daemon)
    monkeypatch.setitem(rig.daemon._methods, "max_id",
                        lambda conn, params: {"ids": ("1", "2"), "seen": params})
    assert client.call("max_id", {"k": ("a",)}) == {"ids": ["1", "2"], "seen": {"k": ["a"]}}


# ============================================================ the reserved belt

def _spy(monkeypatch, daemon, method):
    called = []
    real = daemon._methods[method]

    def spy(conn, params):
        called.append(params)
        return real(conn, params)

    monkeypatch.setitem(daemon._methods, method, spy)
    return called


def test_a_send_or_delete_under_a_reserved_name_is_refused_before_the_daemon(
        rig, console, monkeypatch):
    sends = _spy(monkeypatch, rig.daemon, "send")
    deletes = _spy(monkeypatch, rig.daemon, "delete")
    client = inprocess.InProcessClient(rig.daemon)
    for spelling in (CONSOLE, f"{CONSOLE}@global"):
        with pytest.raises(DaemonError) as refused:
            client.send(spelling, "any", "::to reviewer [no reply]\nx")
        assert refused.value.code == wire.NAME_RESERVED
        assert refused.value.data["name"] == CONSOLE
    with pytest.raises(DaemonError) as refused:
        client.delete(CONSOLE, "any", "1")
    assert refused.value.code == wire.NAME_RESERVED
    assert sends == [] and deletes == []
    # The daemon refuses it on its own too: a connection with no peer can't be placed.
    with pytest.raises(Exception) as daemon_said:
        rig.daemon._call(inprocess.InProcessConnection(), "send",
                         {"as": CONSOLE, "token": "any", "content": "::to reviewer\nx"})
    assert daemon_said.value.code == wire.NAME_RESERVED


def test_the_operators_lane_is_refused_before_the_daemon(rig, monkeypatch):
    sends = _spy(monkeypatch, rig.daemon, "send")
    client = inprocess.InProcessClient(rig.daemon)
    with pytest.raises(DaemonError) as refused:
        client.send("operator", "any", "::to reviewer\nx", user="900001")
    assert refused.value.code == wire.CREDENTIAL
    assert not refused.value.data.get("credential")
    assert sends == []


def test_a_bound_sender_under_a_reserved_name_sends_nothing(rig, console, reviewer):
    before = len(rig.record.history(rig.names_channel))
    got = in_process(rig.daemon, "reviewer", "hi", sender=sending.Sender.bound(CONSOLE, "t"))
    assert got.outcome == "refused" and not got.posted
    assert "reserved" in got.refusal.text
    assert len(rig.record.history(rig.names_channel)) == before


def test_a_reserved_name_never_gets_a_live_credential(rig, console, bind_answered):
    # No binding holds a reserved name, so no row maps to one and no bound sender is
    # made for one.
    with pytest.raises(DaemonError) as refused:
        bind_answered(name=CONSOLE, global_=True, adapter="claude-shim", claim="c-1")
    assert refused.value.code == wire.NAME_RESERVED
    assert rig.daemon.live_credential("c-1") is None
    assert inprocess.bound_sender(rig.daemon, "c-1") is None


# ========================================================= sending.run: parity

@pytest.mark.parametrize("backend", ["mock", "local"], indirect=True)
@pytest.mark.parametrize("case", ["sent", "never-bound", "new-name", "too-long",
                                  "send-only", "posture-required", "would-send"])
def test_the_same_send_gives_the_same_result_both_ways(rig, planner, reviewer, console,
                                                       case):
    kw = {"sent": dict(to="reviewer", text="hi"),
          "never-bound": dict(to="nobody-yet", text="hi"),
          "new-name": dict(to="nobody-yet", text="hi", new_name=True),
          "too-long": dict(to="reviewer", text=LONG),
          "send-only": dict(to=CONSOLE, text="report"),
          "posture-required": dict(to="reviewer", text="hi", posture=None),
          "would-send": dict(to="reviewer", text="hi", check=True)}[case]
    kw.setdefault("posture", "none")
    to, text = kw.pop("to"), kw.pop("text")
    by_socket = over_socket(planner, to, text, **kw)
    by_process = in_process(rig.daemon, to, text, **kw)
    assert shape(by_socket) == shape(by_process), (by_socket.lines(), by_process.lines())
    expected = {"sent": "sent", "new-name": "sent", "would-send": "would-send"}
    assert by_socket.outcome == expected.get(case, "refused")
    if by_socket.posted:
        first, second = (rig.record.get_message(r.message_id)
                         for r in (by_socket, by_process))
        assert (first.author_kind, first.author_id, no_cold_at(first.content)) \
            == (second.author_kind, second.author_id, no_cold_at(second.content))


@pytest.mark.parametrize("backend", ["local"], indirect=True)
def test_a_reply_and_a_closed_thread_give_the_same_result_both_ways(
        rig, planner, reviewer):
    code, out = rig.send(PLANNER, "which way?", "--cap", "1", sender=dict(name="reviewer"),
                         posture="open")
    assert code == 0, out
    root = re.search(r"sent (\d+)", out).group(1)
    ask = over_socket(planner, "reviewer", "a question", posture="once")
    first = over_socket(planner, None, "the one reply", posture=None, reply_to=root)
    assert first.outcome == "sent", first.lines()
    # The thread has had its one reply: closed, both ways alike.
    closed_socket = over_socket(planner, None, "one more", posture=None, reply_to=root)
    closed_process = in_process(rig.daemon, None, "one more", posture=None, reply_to=root)
    assert closed_socket.outcome == "refused"
    assert "closed" in closed_socket.refusal.text
    assert shape(closed_socket) == shape(closed_process)
    # A reply to a message of one's own goes to its recipients, both ways alike.
    mine_socket = over_socket(planner, None, "and a follow-up", posture=None,
                              reply_to=ask.message_id)
    mine_process = in_process(rig.daemon, None, "and a follow-up", posture=None,
                              reply_to=ask.message_id)
    assert mine_socket.outcome == "sent", mine_socket.lines()
    assert shape(mine_socket) == shape(mine_process)


def test_a_delete_gives_the_same_result_both_ways(rig, planner):
    one = over_socket(planner, "later-one", "a", new_name=True, posture="none")
    two = in_process(rig.daemon, "later-one", "a", new_name=True)
    assert one.posted and two.posted
    by_socket = api.delete(api.Sender.claim(planner.server), one.message_id)
    by_process = sending.delete(inprocess.context(rig.daemon), two.message_id,
                                sender=inprocess.bound_sender(rig.daemon, CLAIM))
    assert (by_socket.outcome, by_socket.withdrawn) == ("deleted", ("later-one",))
    assert ((by_process.outcome, by_process.withdrawn, by_process.delivered)
            == (by_socket.outcome, by_socket.withdrawn, by_socket.delivered))
    again_socket = api.delete(api.Sender.claim(planner.server), "99999")
    again_process = sending.delete(inprocess.context(rig.daemon), "99999",
                                   sender=inprocess.bound_sender(rig.daemon, CLAIM))
    assert again_socket.to_dict() == again_process.to_dict()
    assert again_socket.outcome == "refused"


# ===================================================== the bound sender's credential

@pytest.fixture
def no_claim_socket(monkeypatch):
    """A bound sender never reads a claim socket: any read fails the test."""
    from moxi_comms import claimhold

    def refused(*a, **kw):
        raise AssertionError("a bound sender read a claim socket")

    monkeypatch.setattr(claimhold, "call", refused)
    monkeypatch.setattr(sending.claimhold, "call", refused)


def test_the_bound_sender_is_the_live_binding(rig, planner):
    sender = inprocess.bound_sender(rig.daemon, CLAIM)
    held = hashlib.sha256(planner.token.encode()).hexdigest()
    assert (sender.kind, sender.identity, sender.token) == ("bound", PLANNER, held)
    assert held not in repr(sender)
    assert inprocess.bound_sender(rig.daemon, CLAIM, PLANNER).token == held
    assert inprocess.bound_sender(rig.daemon, CLAIM, "someone-else") is None
    assert inprocess.bound_sender(rig.daemon, "no-such-claim") is None
    assert planner.token not in repr(rig.daemon.bindings[PLANNER])


def test_a_binding_that_dropped_gives_no_bound_sender(rig, planner):
    planner.client.close()
    assert wait_until(lambda: PLANNER not in rig.daemon.bindings)
    assert rig.daemon.live_credential(CLAIM) is None
    assert inprocess.bound_sender(rig.daemon, CLAIM) is None


def test_a_refused_token_unchanged_on_the_second_look_is_refused_credential(
        rig, planner, reviewer, no_claim_socket):
    looks = []

    def reread():
        looks.append(1)
        return PLANNER, "stale"

    got = in_process(rig.daemon, "reviewer", "hi",
                     sender=sending.Sender.bound(PLANNER, "stale", reread=reread))
    assert (got.outcome, got.refusal.code) == ("failed", "credential")
    assert looks == [1] and not got.posted


def test_a_refused_token_with_a_new_one_on_the_second_look_is_sent_once_more(
        rig, planner, reviewer, no_claim_socket):
    looks = []

    def reread():
        looks.append(1)
        return rig.daemon.live_credential(CLAIM)

    got = in_process(rig.daemon, "reviewer", "hi",
                     sender=sending.Sender.bound(PLANNER, "stale", reread=reread))
    assert got.outcome == "sent", got.lines()
    assert looks == [1]
    assert wait_for_payload(reviewer, got.message_id)["author"]["name"] == PLANNER


def test_a_refused_token_with_no_binding_on_the_second_look_is_offline(
        rig, planner, reviewer, no_claim_socket):
    sender = inprocess.bound_sender(rig.daemon, CLAIM)
    planner.client.close()
    assert wait_until(lambda: PLANNER not in rig.daemon.bindings)
    got = in_process(rig.daemon, "reviewer", "hi", sender=sender)
    assert (got.outcome, got.exit_code, got.refusal.code) == ("offline", 75, "offline")
    assert "reconnects on its own" in got.refusal.text


def test_a_bound_sender_reads_no_claim_socket(rig, planner, reviewer, no_claim_socket):
    got = in_process(rig.daemon, "reviewer", "hi")
    assert got.outcome == "sent", got.lines()


def test_a_bound_sender_is_comms_own(rig):
    sender = sending.Sender.bound(PLANNER, "t")
    with pytest.raises(TypeError):
        api.send(sender, "reviewer", "hi", posture="none")
    with pytest.raises(TypeError):
        api.delete(sender, "1")
    args, tags = args_for("reviewer", "hi")
    with pytest.raises(TypeError):
        sending.run(sending.Context(), args, tags=tags, sender=api.Sender.session())


def test_a_context_given_a_client_hands_back_that_client(rig):
    client = inprocess.InProcessClient(rig.daemon)
    ctx = sending.Context(daemon=client)
    assert ctx.daemon() is client
    ctx.daemon().close()
    assert ctx.daemon().call("max_id", {}) is not None
    assert sending.Context().daemon() is not client


# ================================================================ the pool

def _gather(host, calls, body, n):
    async def many():
        return await asyncio.gather(*(calls.run(body, i) for i in range(n)))

    return asyncio.run_coroutine_threadsafe(many(), host.loop)


class Gate:
    """Tool bodies that hold their thread until the test opens the gate."""

    def __init__(self):
        self.open = threading.Event()
        self.lock = threading.Lock()
        self.running = 0
        self.peak = 0
        self.threads: set = set()

    def body(self, i):
        with self.lock:
            self.running += 1
            self.peak = max(self.peak, self.running)
            self.threads.add(threading.current_thread().name)
        try:
            self.open.wait(10)
        finally:
            with self.lock:
                self.running -= 1
        return i


def test_at_most_eight_tool_calls_hold_a_thread_and_the_rest_of_the_pool_answers(rig):
    host = rig.daemon.host
    calls = inprocess.Calls(host)
    assert calls.at_once == 8 and wire.WORKERS - calls.at_once >= 24
    gate = Gate()
    done = _gather(host, calls, gate.body, 12)
    try:
        assert wait_until(lambda: gate.running == 8)
        time.sleep(0.2)
        assert gate.running == 8 and calls.running == 8
        # 24 requests at once over the socket, while eight tool calls hold their
        # threads: every one answered.
        with concurrent.futures.ThreadPoolExecutor(24) as askers:
            answers = list(askers.map(
                lambda _: DaemonClient(daemon_socket(), timeout=5.0).connect()
                .call("max_id", {}), range(24)))
        assert len(answers) == 24
        assert gate.running == 8
    finally:
        gate.open.set()
    assert done.result(10) == list(range(12))
    assert gate.peak == 8
    assert all(name.startswith("comms-daemon-wire") for name in gate.threads)
    assert wait_until(lambda: calls.running == 0)


def test_a_cancelled_call_keeps_its_place_until_its_body_returns(rig):
    host = rig.daemon.host
    calls = inprocess.Calls(host, at_once=1)
    first = threading.Event()
    started = []

    def held():
        started.append("first")
        first.wait(10)
        return "first"

    def after():
        started.append("second")
        return "second"

    one = asyncio.run_coroutine_threadsafe(calls.run(held), host.loop)
    assert wait_until(lambda: started == ["first"])
    one.cancel()
    two = asyncio.run_coroutine_threadsafe(calls.run(after), host.loop)
    time.sleep(0.2)
    assert started == ["first"], "a cancelled call's body still holds its thread"
    first.set()
    assert two.result(10) == "second"
    assert started == ["first", "second"]


def test_a_body_that_raises_gives_its_place_back(rig):
    host = rig.daemon.host
    calls = inprocess.Calls(host, at_once=1)

    def fails():
        raise ValueError("no")

    with pytest.raises(ValueError):
        asyncio.run_coroutine_threadsafe(calls.run(fails), host.loop).result(10)
    assert asyncio.run_coroutine_threadsafe(calls.run(lambda: 7),
                                            host.loop).result(10) == 7


def test_eight_tool_calls_sending_in_process_and_adapters_requests_all_finish(
        rig, planner, reviewer):
    host = rig.daemon.host
    calls = inprocess.Calls(host)
    sender = inprocess.bound_sender(rig.daemon, CLAIM)

    def body(i):
        return in_process(rig.daemon, "reviewer", f"tool send {i}", sender=sender).outcome

    done = _gather(host, calls, body, 16)
    with concurrent.futures.ThreadPoolExecutor(24) as askers:
        answers = list(askers.map(
            lambda _: DaemonClient(daemon_socket(), timeout=10.0).connect()
            .call("resolve", {"name": "reviewer"}), range(48)))
    assert done.result(30) == ["sent"] * 16
    assert all(a["qualified"] == "reviewer" for a in answers)


# =========================================================== through the endpoint

_pids = itertools.count(62000, 10)


class View(ProcView):
    def __init__(self):
        super().__init__()
        self.table: dict = {}

    def stat(self, pid):
        return self.table.get(int(pid))

    def gone(self, pid):
        return int(pid) not in self.table


def test_a_tool_sends_as_its_tokens_session_through_the_seam(rig):
    """The seam end to end: the tool's async part reads the identity and the claim on
    the loop, takes a place, and its body sends in process as the bound sender."""
    import http.client

    from mcp.server.auth.middleware.auth_context import get_access_token

    view = View()
    root = next(_pids)
    view.table[root] = (1, f"s{root}")
    key = f"{root}-s{root}"
    claimfile.write(key, {"key": key, "keying": "pid", "state": "bound",
                          "roots": [Root(root, f"s{root}").as_dict()]})
    seen = {}

    def tools(server):
        @server.tool(name="say")
        async def say(to: str, text: str) -> str:
            access = get_access_token()
            identity, claim = access.client_id, access.claims["claim"]
            seen["loop"] = threading.current_thread().name

            def body():
                seen["pool"] = threading.current_thread().name
                sender = inprocess.bound_sender(daemon, claim, identity)
                if sender is None:
                    return "reconnecting"
                got = in_process(daemon, to, text, sender=sender)
                return f"{got.outcome} {got.message_id}"

            return await daemon.tool_calls.run(body)

    rig.daemon.stop()
    daemon = Daemon(rig.record, clock=rig.clock, start_trim_thread=False,
                    serve_tools=True, mcp_port=0)
    daemon.proc_view = view
    daemon.mcp_tools = tools
    rig.daemon = daemon
    daemon.start()
    assert isinstance(daemon.tool_calls, inprocess.Calls)
    assert daemon.tool_calls.at_once == inprocess.TOOLS_AT_ONCE
    reviewer = rig.shim(name="reviewer")
    token = tokens.mint()
    client = DaemonClient(daemon_socket()).connect()
    try:
        client.bind(name="planner", scope="proj-a", adapter="codex", claim=key)
        assert client.mcp_token(tokens.digest(token)) == {"served": True}
        conn = http.client.HTTPConnection("127.0.0.1", daemon.mcp_endpoint.port, timeout=15)
        body = json.dumps({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                           "params": {"name": "say",
                                      "arguments": {"to": "reviewer", "text": "hello"}}})
        try:
            conn.request("POST", "/mcp", body=body, headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
                "Authorization": f"Bearer {token}"})
            response = conn.getresponse()
            answer = json.loads(response.read())
        finally:
            conn.close()
    finally:
        client.close()
    text = answer["result"]["content"][0]["text"]
    outcome, message_id = text.split()
    assert outcome == "sent", answer
    assert seen["loop"].endswith("-loop") and seen["pool"].startswith("comms-daemon-wire")
    assert wait_for_payload(reviewer, message_id)["author"]["name"] == PLANNER

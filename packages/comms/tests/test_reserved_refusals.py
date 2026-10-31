"""The refusals that keep a reserved name a module's own: no session binds it, nobody mails
it, a session's token never sends under it, and the daemon refuses a send under it from
any process inside a live session, or one it can't place."""

from __future__ import annotations

import os
import socket as sock
import sys
from types import SimpleNamespace

import pytest

from moxi import exit as moxi_exit
from moxi.ipc import Peer
from moxi_comms import api, claimfile, machine_line, reserved, secrets, wire
from moxi_comms.client import DaemonClient, DaemonError
from moxi_comms.daemon import Refusal
from moxi_comms.membership import (SELF, TREE, ProcView, PsutilView, Root, Unplaceable,
                                   root_of, sessions_of)
from moxi_comms.paths import daemon_socket
from moxi_comms.store import open_write
from moxi_comms.tokens import TokenStore
from moxi.store import transaction

LEAD = dict(name="captain", scope="proj-a")


def write(sql, params=()):
    conn = open_write()
    try:
        with transaction(conn) as c:
            c.execute(sql, params)
    finally:
        conn.close()


@pytest.fixture
def console(rig):
    """`console` reserved for the launcher, its token in the configured store."""
    reserved.reconcile(declared={"launcher": ["console"]}, chain=secrets.from_config())
    return rig


def console_token():
    return TokenStore().read_secret(reserved.owner("launcher"), reserved.secret_id("console"))


def bind(**kw):
    c = DaemonClient(daemon_socket()).connect()
    try:
        return c.bind(**kw)
    finally:
        c.close()


@pytest.fixture
def client(rig):
    c = DaemonClient(daemon_socket()).connect()
    yield c
    c.close()


def claim_file(key, *roots, socket=None):
    """A claim file whose roots are `roots`, as a session's adapter writes it: live while
    a tree root runs, or while `socket` answers."""
    record = {"roots": [r.as_dict() for r in roots], "state": "bound",
              "claim": {"qualified": "planner@proj-a", "name": "planner"}}
    if socket is not None:
        record["socket"] = socket
    claimfile.write(key, record)


@pytest.fixture
def listening(tmp_path):
    """A socket that answers, as a claim holder's does."""
    path = tmp_path / "c.sock"
    s = sock.socket(sock.AF_UNIX, sock.SOCK_STREAM)
    s.bind(str(path))
    s.listen()
    yield str(path)
    s.close()


def history(rig):
    return rig.record.history(rig.names_channel)


# --------------------------------------------------------------------------- bind

def _reserve(state):
    if state == "active":
        reserved.reconcile(declared={"launcher": ["console"]}, chain=secrets.from_config())
    elif state == "unowned":
        write("INSERT INTO reserved(name, state, note) VALUES ('console', 'unowned', "
              "'kept')")
    else:
        write("INSERT INTO reserved(name, state, note) VALUES ('console', 'conflict', "
              "'two modules declare it')")


@pytest.mark.parametrize("state", ["active", "conflict", "unowned"])
def test_no_session_binds_a_reserved_name_of_any_kind(rig, state):
    _reserve(state)
    for kind in rig.daemon.kinds.names():
        with pytest.raises(DaemonError) as e:
            bind(name="console", global_=True, adapter=kind)
        assert e.value.code == wire.NAME_RESERVED, kind
        assert "no session binds it" in str(e.value)
    assert "console" not in rig.daemon.bindings


def test_a_freed_name_binds_again(rig):
    _reserve("active")
    reserved.reconcile(declared={}, chain=secrets.from_config())
    assert bind(name="console", global_=True)["identity"]["qualified"] == "console"


# --------------------------------------------------------------------------- mail

@pytest.mark.parametrize("state", ["active", "unowned"])
def test_the_send_verb_refuses_mail_to_a_reserved_name(rig, state):
    _reserve(state)
    before = len(history(rig))
    code, out = rig.cli(["send", "--posture", "none", "console", "report"], **LEAD)
    assert code == moxi_exit.REFUSED
    assert "console is a send-only identity and receives no mail" in out
    assert len(history(rig)) == before


def test_a_conflict_row_is_not_a_send_only_name(rig):
    _reserve("conflict")
    assert not rig.daemon.resolve("console").send_only


@pytest.mark.parametrize("state", ["active", "unowned"])
def test_the_daemon_refuses_mail_to_a_reserved_name_with_the_verb_bypassed(
        rig, client, state):
    _reserve(state)
    token = client.bind(name="planner", scope="proj-a")["token"]
    before = len(history(rig))
    for names in (["console"], ["main@proj-a", "console"]):
        with pytest.raises(DaemonError) as e:
            client.send("planner@proj-a", token, machine_line.compose(names, "x"))
        assert e.value.code == wire.NOT_MAILABLE
    assert len(history(rig)) == before


# --------------------------------------------------------------------------- send under

def test_a_binding_token_never_sends_under_a_reserved_name(console, client):
    token = client.bind(name="planner", scope="proj-a")["token"]
    before = len(history(console))
    with pytest.raises(DaemonError) as e:
        client.send("console", token, machine_line.compose(["main@proj-a"], "x"))
    assert e.value.code == wire.NAME_RESERVED
    assert "a session's token never sends under it" in str(e.value)
    assert len(history(console)) == before


def test_send_as_from_under_a_live_bindings_claim_root_is_refused(console):
    claim_file("planner", root_of(os.getppid(), TREE))
    result = api.send_as("launcher", "console", ["main@proj-a"], "x")
    assert (result.outcome, result.refusal.code) == ("refused", "name-reserved")
    assert "from inside a session" in result.refusal.text


def test_send_as_from_a_claim_holder_itself_is_refused(console, listening):
    claim_file("planner", root_of(os.getpid(), SELF), socket=listening)
    result = api.send_as("launcher", "console", ["main@proj-a"], "x")
    assert (result.outcome, result.refusal.code) == ("refused", "name-reserved")


def test_send_as_from_a_child_of_a_self_reach_root_is_sent(console, listening):
    claim_file("loop", root_of(os.getppid(), SELF), socket=listening)
    result = api.send_as("launcher", "console", ["main@proj-a"], "x")
    assert result.outcome == "sent", result.lines()


def test_send_as_from_outside_every_session_is_sent(console):
    claim_file("elsewhere", Root(os.getpid() + 1_000_000, "0", TREE))
    result = api.send_as("launcher", "console", ["main@proj-a"], "x")
    assert result.outcome == "sent", result.lines()


def _send_from(rig, peer):
    return rig.daemon._send(SimpleNamespace(peer=peer), {
        "as": "console", "token": console_token(),
        "content": machine_line.compose(["main@proj-a"], "x")})


def test_a_peer_with_no_pid_is_refused_as_unplaceable(console):
    with pytest.raises(Refusal) as e:
        _send_from(console, Peer(None, None, None))
    assert e.value.code == wire.NAME_RESERVED
    assert "can't tell whether this process is inside a session" in str(e.value)


def test_a_peer_that_has_gone_is_refused_as_unplaceable(console):
    from moxi.proc import ProcId

    with pytest.raises(Refusal) as e:
        _send_from(console, Peer(os.getpid(), os.getuid(), ProcId(os.getpid(), 1.0)))
    assert "can't tell" in str(e.value)


class Blind(ProcView):
    """The real `/proc`, except that one pid can't be read."""

    def __init__(self, hidden):
        super().__init__()
        self.hidden = hidden

    def stat(self, pid):
        return None if pid == self.hidden else super().stat(pid)


def test_a_peer_whose_ancestor_cant_be_read_is_refused(console):
    console.daemon.proc_view = Blind(os.getppid())
    result = api.send_as("launcher", "console", ["main@proj-a"], "x")
    assert (result.outcome, result.refusal.code) == ("refused", "name-reserved")
    assert "can't tell whether this process is inside a session" in result.refusal.text
    console.daemon.proc_view = ProcView()
    assert api.send_as("launcher", "console", ["main@proj-a"], "x").outcome == "sent"


# --------------------------------------------------------------------------- the walk

class Table(ProcView):
    def __init__(self, table):
        super().__init__()
        self.table = table

    def stat(self, pid):
        return self.table.get(pid)


def test_the_strict_walk_raises_on_a_process_it_cant_read_before_the_top():
    view = Table({30: (20, "c"), 20: (10, "b")})          # 10 can't be read
    files = [("k", [Root(99, "z", TREE)])]
    assert sessions_of(30, files, view) == []
    with pytest.raises(Unplaceable) as e:
        sessions_of(30, files, view, strict=True)
    assert e.value.pid == 10


def test_the_strict_walk_ends_at_a_process_with_no_parent():
    view = Table({30: (20, "c"), 20: (1, "b"), 1: (0, "a")})
    assert sessions_of(30, [("k", [Root(99, "z", TREE)])], view, strict=True) == []
    assert sessions_of(30, [("k", [Root(20, "b", TREE)])], view, strict=True) == ["k"]


def test_the_strict_walk_meets_a_root_before_an_unreadable_ancestor():
    view = Table({30: (20, "c"), 20: (10, "b")})
    assert sessions_of(30, [("k", [Root(20, "b", TREE)])], view, strict=True) == ["k"]


@pytest.mark.skipif(not sys.platform.startswith("linux"),
                    reason="compares the psutil reader with Linux's /proc reader")
def test_the_psutil_reader_gives_the_proc_readers_ancestry():
    pids = [p for p, _ in ProcView().ancestry(os.getpid())]
    assert [p for p, _ in PsutilView().ancestry(os.getpid())] == pids
    assert pids[0] == os.getpid() and len(pids) > 1

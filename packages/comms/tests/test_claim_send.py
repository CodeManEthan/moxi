"""Sending as the claim: the send verb takes its identity from its session's claim
socket, not from the launch stamp, on both backends. Ordinary messaging through the
claim prints, exits, delivers and attributes as any ordinary send, and `comms whoami` names what the session holds."""

from __future__ import annotations

import pytest

from claim_kit import World, wait_for
from conftest import clock_reads_warm, wait_for_queue
from moxi_comms import claimfile, wire
from moxi_comms.client import (DaemonClient, DaemonError, DaemonNoAnswer,
                               DaemonOffline)

PLANNER = "planner@proj-a"


@pytest.fixture
def world(rig, monkeypatch):
    clock_reads_warm(monkeypatch, "codex")
    w = World()
    yield w
    w.stop()


def as_claim(s):
    return {"MOXI_COMMS_CLAIM_SOCKET": s.socket}


def last(rig):
    return rig.record.history(rig.names_channel)[-1]


def test_a_send_inside_the_session_is_stamped_as_the_claim(rig, world):
    s = world.real("planner")
    code, out = rig.cli(["send", "--posture", "none", "--new-name", "nobody", "hello"], env=as_claim(s))
    assert code == 0, out
    msg = last(rig)
    assert (msg.author_kind, msg.author_id, msg.author_display) == (
        "identity", PLANNER, PLANNER)


def test_the_stamp_is_not_read_when_the_claim_socket_is_there(rig, world):
    s = world.real("planner")
    code, out = rig.cli(["send", "--posture", "none", "--new-name", "nobody", "hello"], name="stranger",
                        env=as_claim(s))
    assert code == 0, out
    assert last(rig).author_display == PLANNER


def test_a_process_outside_the_session_is_refused(rig, world):
    s = world.session("planner")            # rooted in a fake, unrelated tree
    code, out = rig.cli(["send", "--posture", "none", "nobody", "hello"], env=as_claim(s))
    assert code == 3
    assert "not inside the session" in out and "nothing was sent" in out


def test_a_session_that_holds_nothing_says_why(rig, world):
    s = world.real("planner")
    s.holder.claim, s.holder.state = None, "standby"
    code, out = rig.cli(["send", "--posture", "none", "nobody", "hello"], env=as_claim(s))
    assert code == 3
    assert "this session holds no comms identity — nothing was sent." in out
    s.holder.lost = PLANNER                         # another session took its name
    code, out = rig.cli(["send", "--posture", "none", "nobody", "hello"], env=as_claim(s))
    assert code == 3
    assert f"{PLANNER} is held by another session — nothing was sent." in out


def test_offline_with_the_name_bound_elsewhere_is_refused(rig, world):
    world.real("planner")
    code, out = rig.cli(["send", "--posture", "none", "--new-name", "nobody", "hello"],
                        name="planner", scope="proj-a")
    assert code == 3 and "held by another session" in out


def test_offline_with_the_name_unbound_is_refused(rig, world):
    s = world.real("planner")
    s.client.close()
    assert wait_for(lambda: s.holder.state == "offline")
    code, out = rig.cli(["send", "--posture", "none", "--new-name", "nobody", "hello"], env=as_claim(s))
    assert code == 75
    assert "is offline" in out and "holds no posting credential" in out
    assert rig.record.history(rig.names_channel) == []


def test_offline_refusal_does_not_look_up_another_binding(rig, world, monkeypatch):
    s = world.real("planner")
    s.client.close()
    assert wait_for(lambda: s.holder.state == "offline")
    def refusing_lookup(self, kind, params=None, **kw):
        if kind == "agents":
            raise AssertionError("an offline send must not query bindings")
        return original(self, kind, params, **kw)

    original = DaemonClient.call
    monkeypatch.setattr(DaemonClient, "call", refusing_lookup)
    code, out = rig.cli(["send", "--posture", "none", "--new-name", "nobody", "hello"], env=as_claim(s))
    assert code == 75 and "holds no posting credential" in out


def test_a_bound_claim_posts_with_its_token_without_a_binding_query(
        rig, world, monkeypatch):
    planner = world.real("planner")
    rig.shim(name="reviewer", scope="proj-a")
    original = DaemonClient.call

    def refusing_lookup(self, kind, params=None, **kw):
        if kind == "agents":
            raise AssertionError("a bound send must not query bindings")
        return original(self, kind, params, **kw)

    monkeypatch.setattr(DaemonClient, "call", refusing_lookup)
    code, out = rig.cli(["send", "--posture", "none", "reviewer@proj-a", "hello"],
                        env=as_claim(planner))
    assert code == 0, out
    assert last(rig).author_id == PLANNER


def test_a_dead_adapter_is_refused_while_nobody_holds_the_name(rig, world):
    from moxi_comms import claimhold

    s = world.real("planner")
    token = s.holder.token
    s.client.close()
    s.holder.close(remove_file=False)               # the shim died; Claude lives on
    assert wait_for(lambda: PLANNER not in rig.daemon.bindings)
    answer = claimhold.dead_adapter_answer(s.socket)
    assert answer is not None and token not in str(answer) and "token" not in answer
    code, out = rig.cli(["send", "--posture", "none", "--new-name", "nobody", "hello"], env=as_claim(s))
    assert code == 75
    assert "comms adapter is gone" in out and "holds no posting credential" in out
    assert rig.record.history(rig.names_channel) == []


def test_a_dead_adapter_with_no_live_file_is_refused(rig, world):
    s = world.real("planner")
    s.holder.close()
    code, out = rig.cli(["send", "--posture", "none", "nobody", "hello"], env=as_claim(s))
    assert code == 3 and "comms adapter is gone" in out


def test_a_stamp_is_refused_when_a_live_session_holds_its_name(rig, world):
    world.real("planner")
    code, out = rig.cli(["send", "--posture", "none", "nobody", "hello"], name="planner", scope="proj-a",
                        stamp_only=True)
    assert code == 3 and "not inside the session that holds planner@proj-a" in out
    assert rig.record.history(rig.names_channel) == []


def test_a_stamp_is_refused_while_a_waiting_launch_names_it(rig, world):
    waiting = world.session("newbie", bind=False)
    waiting.holder.name_refused("newbie@proj-a")
    assert waiting.state == "waiting"
    code, out = rig.cli(["send", "--posture", "none", "nobody", "hi"], name="newbie", scope="proj-a",
                        stamp_only=True)
    assert code == 3 and "not inside the session that holds newbie@proj-a" in out


def test_a_stamp_no_live_session_names_is_refused_too(rig):
    """There is no env path. A stamp is refused even when no live claim file names its
    identity, and the refusal names the
    identity the stamp claims. The binding is there; only a session holding it sends."""
    rig.shim(name="planner", scope="proj-a")
    code, out = rig.cli(["send", "--posture", "none", "nobody", "hello"], name="planner", scope="proj-a",
                        stamp_only=True)
    assert code == 3, out
    assert out.strip() == (
        "this process has no comms claim socket, so it sends under no identity — nothing "
        "was sent. Its launch stamp names planner@proj-a, but a stamp is not a live session "
        "holding a name: a session sends as its identity through its own claim socket.")
    assert rig.record.history(rig.names_channel) == []


def test_the_operators_lane_is_refused_inside_a_session(rig, world):
    s = world.real("planner")
    code, out = rig.cli(["send", "--posture", "none", "nobody", "hi"],
                        env={**as_claim(s), "MOXI_COMMS_OPERATOR": rig.prov.operator_user_id})
    assert code == 3 and "operator's lane is its own terminal" in out
    code, out = rig.cli(["send", "--posture", "none", "nobody", "hi"],
                        env={"MOXI_COMMS_OPERATOR": rig.prov.operator_user_id})
    assert code == 3 and "inside an agent's session" in out


# --------------------------------------------------------------------------- replies

def _sent_id(out):
    return out.split("sent ", 1)[1].split()[0]


def test_a_reply_through_the_claim_goes_out_to_its_own_or_anothers_message(rig, world):
    s = world.real("planner")
    code, out = rig.cli(["send", "--posture", "once", "--new-name", "reviewer", "before"], env=as_claim(s))
    assert code == 0, out
    mine = _sent_id(out)
    code, out = rig.cli(["reply", mine, "fine"], env=as_claim(s))     # its own message
    assert code == 0, out
    other = rig.post_as("bob@proj-a", ["planner"], "from someone else")
    code, out = rig.cli(["reply", other.id, "fine"], env=as_claim(s))  # another's
    assert code == 0, out


# --------------------------------------------------------------------------- whoami

def test_whoami_prints_the_claim_and_its_session(rig, world):
    """The first line, then `session <id>` when the claim records its launch
    session, and nothing else."""
    s = world.real("planner")
    token = s.holder.token
    code, out = rig.cli(["whoami"], env=as_claim(s))
    assert code == 0 and out.splitlines() == ["planner@proj-a (bound)"], out
    assert token not in out
    s.holder.launch_session = "0a1b2c3d-sess"
    code, out = rig.cli(["whoami"], env=as_claim(s))
    assert code == 0, out
    assert out.splitlines() == ["planner@proj-a (bound)", "session 0a1b2c3d-sess"]


def test_whoami_outside_any_session(rig):
    code, out = rig.cli(["whoami"])
    assert code == 2 and "no claim socket" in out


def test_whois_and_the_cache_caller_read_the_claim(rig, world, monkeypatch):
    s = world.real("planner")
    from moxi_comms import claimhold
    env = claimhold.claim_overlay({"MOXI_COMMS_CLAIM_SOCKET": s.socket, "MOXI_COMMS_NAME": "stale"})
    assert env["MOXI_COMMS_NAME"] == "planner" and env["MOXI_COMMS_SCOPE"] == "proj-a"
    assert "MOXI_COMMS_SLOT" not in env
    assert s.holder.token not in str(env) and "token" not in env
    s.holder.claim, s.holder.state = None, "standby"
    env = claimhold.claim_overlay({"MOXI_COMMS_CLAIM_SOCKET": s.socket, "MOXI_COMMS_NAME": "stale"})
    assert "MOXI_COMMS_NAME" not in env


def test_a_dead_adapters_file_admits_only_a_process_inside_its_session(rig, world):
    """The claim file of a session whose adapter died answers a sender only when the
    sender is inside that session: a live root that is not this process's ancestor
    admits nothing."""
    import subprocess
    import sys

    from moxi_comms.membership import DEFAULT_PROC, TREE, root_of
    from claim_kit import Session
    other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        s = Session(DEFAULT_PROC, "planner",  root=root_of(other.pid, TREE))
        world.sessions.append(s)
        s.bind()
        s.client.close()
        s.holder.close(remove_file=False)
        assert wait_for(lambda: PLANNER not in rig.daemon.bindings)
        code, out = rig.cli(["send", "--posture", "none", "nobody", "hello"], env=as_claim(s))
        assert code == 3 and "comms adapter is gone" in out
    finally:
        other.kill()


# --------------------------------------------------------------------------- ordinary sends
# Ordinary messaging is no harder for the claim: bare-name sends between agents and sends
# through a claim print, exit, deliver and attribute as any ordinary send.

def sent_line(out, to):
    first = out.splitlines()[0]
    mid = first.split()[1]
    assert first == f"sent {mid} to {to} in #names"
    return mid


def test_bare_name_sends_between_agents_are_unchanged(rig):
    """A bare name is the global identity, so the bare-name case is two global
    agents."""
    a = rig.shim(name="alice")
    b = rig.shim(name="bob")
    code, out = rig.cli(["send", "--posture", "once", "bob", "hello bob"], name="alice")
    assert code == 0 and out.count("\n") == 1, out
    mid = sent_line(out, "bob")
    wait_for_queue(b, 1)
    assert b.queue[0]["message_id"] == mid
    author = b.queue[0]["author"]
    assert (author["name"], author["tier"], author["attribution"]) == (
        "alice", "chosen", "verified")
    code, out = rig.cli(["reply", mid, "hi alice"], name="bob")
    assert code == 0
    assert out.splitlines()[0] == "replying to alice (chosen, verified) in #names"
    wait_for_queue(a, 1)


def test_sends_through_the_claim_print_and_deliver_as_stamped_sends_did(rig, world):
    a = world.real("alice")
    b = rig.shim(name="bob")                  # global: a bare `bob` reaches it
    code, out = rig.cli(["send", "--posture", "none", "bob", "hello"], env={"MOXI_COMMS_CLAIM_SOCKET": a.socket})
    assert code == 0 and out.count("\n") == 1, out
    sent_line(out, "bob")
    wait_for_queue(b, 1)
    author = b.queue[0]["author"]
    assert (author["name"], author["tier"], author["attribution"]) == (
        "alice@proj-a", "chosen", "verified")


def test_a_replaced_token_is_read_again_and_retried_once(rig, world, monkeypatch):
    planner = world.real("planner")
    reviewer = rig.shim(name="reviewer")
    original = DaemonClient.send
    calls = []

    def replaced(client, identity, token, content, **kw):
        calls.append(token)
        if len(calls) == 1:
            planner.client.close()
            assert wait_for(lambda: planner.holder.state == "offline")
            planner.bind()
            raise DaemonError("posting credential refused", wire.CREDENTIAL, {"credential": True})
        return original(client, identity, token, content, **kw)

    monkeypatch.setattr(DaemonClient, "send", replaced)
    code, out = rig.cli(["send", "--posture", "none", "reviewer", "hello"],
                        env=as_claim(planner))
    assert code == 0, out
    assert len(calls) == 2 and calls[0] != calls[1]
    wait_for_queue(reviewer, 1)
    assert last(rig).author_id == "planner@proj-a"


def test_a_refused_unchanged_token_fails_without_a_retry(rig, world, monkeypatch):
    planner = world.real("planner")
    rig.shim(name="reviewer")
    calls = []

    def refused(_client, _identity, token, _content, **_kw):
        calls.append(token)
        raise DaemonError("posting credential refused", wire.CREDENTIAL, {"credential": True})

    monkeypatch.setattr(DaemonClient, "send", refused)
    code, out = rig.cli(["send", "--posture", "none", "reviewer", "hello"],
                        env=as_claim(planner))
    assert code == 1 and "refused this session's posting credential" in out
    assert len(calls) == 1 and rig.record.history(rig.names_channel) == []


@pytest.mark.parametrize(
    "next_state,exit_code,phrase",
    [(claimfile.OFFLINE, 75, "is offline"),
     (claimfile.WAITING, 3, "is held by another session"),
     (claimfile.STANDBY, 3, "is held by another session")])
def test_a_credential_refusal_rereads_the_changed_claim_state(
        rig, world, monkeypatch, next_state, exit_code, phrase):
    planner = world.real("planner")
    rig.shim(name="reviewer")
    calls = []

    def refused(_client, _identity, token, _content, **_kw):
        calls.append(token)
        if next_state == claimfile.OFFLINE:
            planner.holder.went_offline()
        elif next_state == claimfile.WAITING:
            with planner.holder.lock:
                planner.holder.token = None
                planner.holder.state = claimfile.WAITING
                planner.holder.waiting_why = None
                planner.holder.save()
        else:
            planner.holder.name_refused("planner@proj-a")
        raise DaemonError("posting credential refused", wire.CREDENTIAL, {"credential": True})

    monkeypatch.setattr(DaemonClient, "send", refused)
    code, out = rig.cli(["send", "--posture", "none", "reviewer", "hello"],
                        env=as_claim(planner))
    assert code == exit_code and phrase in out
    assert len(calls) == 1
    assert rig.record.history(rig.names_channel) == []


@pytest.mark.parametrize(
    "error, phrase",
    [(DaemonOffline("write failed"), "Nothing was sent"),
     (DaemonNoAnswer("closed after post was sent"), "may have been sent"),
     (DaemonNoAnswer("timeout waiting for post"), "may have been sent")])
def test_post_transport_failures_say_whether_it_may_have_landed(
        rig, world, monkeypatch, error, phrase):
    planner = world.real("planner")
    rig.shim(name="reviewer")

    def failed(*_args, **_kwargs):
        raise error

    monkeypatch.setattr(DaemonClient, "send", failed)
    code, out = rig.cli(["send", "--posture", "none", "reviewer", "hello"],
                        env=as_claim(planner))
    assert code == (75 if isinstance(error, DaemonOffline) else 1)
    assert phrase in out and rig.record.history(rig.names_channel) == []

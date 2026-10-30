"""The `owner` read: which live binding owns one session id now, and whom it awaits.

One read a wrapper can call, given a session id: the binding that owns it now (its
identity as comms renders it), and that identity's `awaiting` list. No acting verb is
added. The daemon answers it as `query kind="owner"`; a caller asks it as
`moxi comms owner <session-id> --json`.

Every rig test here runs on the mock and on the local backend (`BOTH_BACKENDS`).
"""

from __future__ import annotations

import json

import pytest
from conftest import wait_until

from moxi import exit as moxi_exit
from moxi_comms import cli, provision
from moxi_comms.client import DaemonClient, DaemonError, DaemonTimeout
from moxi_comms.paths import daemon_socket

from test_reply_enforcement import (PLANNER, REVIEWER, P, R, indexed, query_owed,
                                    send)


def owner(session_id):
    client = DaemonClient(daemon_socket()).connect()
    try:
        return client.call("owner", {"session_id": session_id})
    finally:
        client.close()


def owner_cli(rig, *argv):
    code, out = rig.cli(["owner", *argv], **PLANNER)
    return code, out


def owner_json(rig, session_id):
    code, out = owner_cli(rig, session_id, "--json")
    return code, json.loads(out)


def bound(rig, qualified):
    assert wait_until(lambda: qualified in rig.daemon.bindings
                      and rig.daemon.bindings[qualified].session_id), qualified


# --------------------------------------------------------------------------- the query

def test_a_shims_session_is_owned_by_its_binding_as_comms_renders_it(rig):
    planner = rig.shim(**PLANNER)
    bound(rig, P)
    answer = owner(planner.session_id)
    assert (answer["owner"], answer["adapter"], answer["awaiting"]) == \
        (P, "claude-shim", [])
    assert answer["session_id"] == planner.session_id


def test_a_global_identity_renders_bare(rig):
    lead = rig.shim(name="lead", global_=True)
    bound(rig, "lead")
    assert owner(lead.session_id)["owner"] == "lead"


def test_a_session_no_binding_holds_answers_no_owner(rig):
    rig.shim(**PLANNER)
    bound(rig, P)
    assert owner("0b9f6a1e-0000-4000-8000-000000000000") == {
        "session_id": "0b9f6a1e-0000-4000-8000-000000000000",
        "owner": None, "adapter": None, "awaiting": []}


def test_a_session_whose_binding_ended_owns_nothing(rig):
    planner = rig.shim(**PLANNER)
    bound(rig, P)
    sid = planner.session_id
    planner.stop()
    assert wait_until(lambda: P not in rig.daemon.bindings)
    assert owner(sid)["owner"] is None


def test_an_empty_session_id_is_refused(rig):
    with pytest.raises(DaemonError, match="non-empty session id"):
        owner("")


def test_a_director_with_a_reply_once_ask_out_awaits_its_worker(rig):
    """The director's own session reads its `awaiting`."""
    reviewer = rig.shim(**REVIEWER)
    rig.shim(**PLANNER)
    ask = send(rig, P, "do it", "--posture", "once", who=REVIEWER)
    indexed(rig, ask.id)
    answer = owner(reviewer.session_id)
    assert (answer["owner"], answer["awaiting"]) == (R, [P])
    assert answer["awaiting"] == query_owed(R)["awaiting"], "the Stop hook's own list"


def test_a_worker_that_asked_its_own_director_once_awaits_the_director(rig):
    """A worker that asked its director with `--posture once` and waits on the
    answer counts too. The director is global, so it is awaited bare."""
    rig.shim(name="lead", global_=True)
    planner = rig.shim(**PLANNER, directors=("lead",))
    bound(rig, "lead")
    ask = send(rig, "lead", "which way?", "--posture", "once", who=PLANNER)
    indexed(rig, ask.id)
    answer = owner(planner.session_id)
    assert (answer["owner"], answer["awaiting"]) == (P, ["lead"])


def test_the_answer_ends_the_wait_and_owner_follows(rig):
    reviewer = rig.shim(**REVIEWER)
    rig.shim(**PLANNER)
    ask = send(rig, P, "do it", "--posture", "once", who=REVIEWER)
    indexed(rig, ask.id)
    assert owner(reviewer.session_id)["awaiting"] == [P]
    from test_reply_enforcement import reply
    answer = reply(rig, ask.id, "done", who=PLANNER)
    indexed(rig, answer.id)
    assert owner(reviewer.session_id)["awaiting"] == []


def test_the_read_changes_nothing(rig):
    """A read: no hold, pend, delivery or ask state moves because it was asked."""
    reviewer = rig.shim(**REVIEWER)
    rig.shim(**PLANNER)
    ask = send(rig, P, "do it", "--posture", "once", who=REVIEWER)
    indexed(rig, ask.id)
    d = rig.daemon

    def state():
        with d._lock:
            return (dict(d.session_owner), {k: dict(v) for k, v in d.queue.items()},
                    {k: list(v) for k, v in d.sent.items()},
                    {q: (b.session_id, b.turned, set(b.delivered))
                     for q, b in d.bindings.items()})

    before = state()
    for _ in range(3):
        owner(reviewer.session_id)
        owner("not-a-session")
    assert state() == before


def test_a_send_only_binding_owns_a_session_only_if_its_register_named_one(rig):
    """No shipped send-only holder reports a session id (`ClaimServer._register`), so it
    owns none; a register that does carry one is answered as any owner, by its kind."""
    code = provision.send_only_main(["relay@proj-a"], record=rig.record,
                                    store=rig.store, out=lambda _l: None)
    assert code == 0
    c = DaemonClient(daemon_socket()).connect()
    try:
        c.bind(name="relay", scope="proj-a",
                   project="proj-a", adapter="send-only",
                   session_id="5e5e5e5e-0000-4000-8000-000000000000")
        answer = owner("5e5e5e5e-0000-4000-8000-000000000000")
        assert (answer["owner"], answer["adapter"], answer["awaiting"]) == \
            ("relay@proj-a", "send-only", [])
    finally:
        c.close()


# --------------------------------------------------------------------------- the verb

def test_the_verb_prints_the_answer_as_json(rig):
    reviewer = rig.shim(**REVIEWER)
    rig.shim(**PLANNER)
    ask = send(rig, P, "do it", "--posture", "once", who=REVIEWER)
    indexed(rig, ask.id)
    code, out = owner_json(rig, reviewer.session_id)
    assert code == moxi_exit.OK
    assert out == {"ok": True, "session_id": reviewer.session_id, "owner": R,
                   "adapter": "claude-shim", "awaiting": [P]}


def test_the_verb_answers_no_owner_with_exit_0(rig):
    code, out = owner_json(rig, "0b9f6a1e-0000-4000-8000-000000000000")
    assert code == moxi_exit.OK
    assert out == {"ok": True, "session_id": "0b9f6a1e-0000-4000-8000-000000000000",
                   "owner": None, "adapter": None, "awaiting": []}


def test_the_verbs_text_form(rig):
    planner = rig.shim(**PLANNER)
    bound(rig, P)
    code, out = owner_cli(rig, planner.session_id)
    assert code == 0
    assert out.strip() == f"{planner.session_id}: owned by {P} (claude-shim); awaiting nobody."
    code, out = owner_cli(rig, "nobody-holds-this")
    assert code == 0 and out.strip() == ("nobody-holds-this: no live binding owns this "
                                         "session.")


def test_the_verb_with_the_daemon_down_is_offline_exit_75(rig):
    rig.daemon.stop()
    code, out = owner_json(rig, "0b9f6a1e-0000-4000-8000-000000000000")
    assert code == moxi_exit.TEMPFAIL
    assert out["ok"] is False and out["error"] == "offline"
    assert "comms offline" in out["detail"]


def test_the_verb_refuses_an_empty_session_id_exit_2_without_asking(rig, monkeypatch):
    monkeypatch.setattr(cli.Context, "daemon",
                        lambda self: pytest.fail("asked the daemon"))
    code, out = owner_json(rig, "  ")
    assert code == moxi_exit.USAGE
    assert out["ok"] is False and out["error"] == "refused"


def test_a_daemon_without_the_query_is_a_failed_read_exit_1(rig, monkeypatch):
    monkeypatch.delitem(rig.daemon._methods, "owner")
    code, out = owner_json(rig, "0b9f6a1e-0000-4000-8000-000000000000")
    assert code == moxi_exit.ERROR
    assert (out["ok"], out["error"]) == (False, "failed")
    assert "method not found: owner" in out["detail"]


def test_a_daemon_that_never_answers_is_a_failed_read_exit_1(rig, monkeypatch):
    def silent(self, method, params=None, **kw):
        raise DaemonTimeout(f"timeout waiting for {method}")
    monkeypatch.setattr(DaemonClient, "call", silent)
    code, out = owner_json(rig, "0b9f6a1e-0000-4000-8000-000000000000")
    assert code == moxi_exit.ERROR
    assert (out["ok"], out["error"]) == (False, "timeout")

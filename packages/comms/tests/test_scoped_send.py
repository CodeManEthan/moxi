"""The scoped send: only the module's token sends under its scoped sender, from outside
every session, to its parent alone; nobody mails it; its messages carry `[no reply]` and
are delivered as any message is."""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

from conftest import wait_until, write_transcript

from moxi import exit as moxi_exit
from moxi_comms import machine_line, reserved, scoped, secrets, sending, wire
from moxi_comms.adapter import frame_line
from moxi_comms.adapters.claude_shim import digest_senders
from moxi_comms.client import DaemonClient, DaemonError
from moxi_comms.membership import SELF, TREE, Root, root_of
from moxi_comms.paths import daemon_socket
from moxi_comms.tokens import TokenStore

from test_cold_sends import HOUR, SESS_REVIEWER, held_of, reviewer_with_session
from test_reserved_refusals import claim_file, listening  # noqa: F401

PARENT = "main@proj-a"
FORMED = "main-digest@proj-a"
DIGEST = {"pager": {"digest": "the nightly digest a session gets"}}


def declare(rig, words=None):
    rig.clock.advance(1)
    return reserved.reconcile(declared={}, words=DIGEST if words is None else words,
                              chain=secrets.from_config(), clock=rig.clock)


@pytest.fixture
def digest(rig):
    """The word `digest` declared by pager, its token in the configured store."""
    declare(rig)
    return rig


def token(word="digest", module="pager"):
    return TokenStore().read_secret(reserved.owner(module), scoped.secret_id(word))


def send(to, text="x", *, parent=PARENT, module="pager", word=None, interrupt=False,
         expires=None):
    args = SimpleNamespace(text=text, posture=machine_line.POSTURE_NONE, cap=None,
                           no_cap=False, audience=None, new=False, held=False,
                           standing=False, new_name=False, interrupt=interrupt,
                           expires=expires, check=False, reply_to=None, message_id=None)
    ctx = sending.Context(env=dict(os.environ), deadline=30.0)
    try:
        return sending.run_scoped(ctx, args, module=module, parent=parent, word=word,
                                  tags=[to] if isinstance(to, str) else list(to))
    finally:
        ctx.close()


@pytest.fixture
def client(rig):
    c = DaemonClient(daemon_socket()).connect()
    yield c
    c.close()


def history(rig):
    return rig.record.history(rig.names_channel)


def refused(result, code):
    assert (result.outcome, result.refusal.code) == ("refused", code), result.lines()
    return result.refusal.text


# --------------------------------------------------------------------------- sent

def test_a_scoped_send_reaches_its_parent_as_a_send_only_name_with_no_reply(digest):
    shim = digest.shim(name="main", scope="proj-a")
    result = send(PARENT, "the digest")
    assert result.outcome == "sent", result.lines()
    mid = result.message_id
    msg = digest.record.get_message(mid)
    assert (msg.author_id, msg.author_kind) == (FORMED, "reserved")
    assert "[no reply]" in msg.content.splitlines()[0]
    assert wait_until(lambda: any(p["message_id"] == mid for p in shim.queue))
    (payload,) = [p for p in shim.queue if p["message_id"] == mid]
    assert payload["author"]["tier"] == "send-only"
    assert frame_line(payload) == f"[comms] from {FORMED} (send-only) · msg {mid} · [no reply]"
    assert digest_senders([payload]) == (FORMED,)


def test_history_shows_it_as_the_author_and_explain_explains_its_delivery(digest):
    shim = digest.shim(name="main", scope="proj-a")
    mid = send(PARENT, "the digest").message_id
    code, out = digest.cli(["history", FORMED])
    assert code == 0, out
    (row,) = [ln for ln in out.splitlines() if ln.startswith(f"{mid} ")]
    assert f"from {FORMED} (send-only, verified)" in row
    assert wait_until(lambda: any(p["message_id"] == mid for p in shim.queue))
    code, out = digest.cli(["explain", PARENT, mid])
    assert code == 0, out
    assert out.startswith(f"{PARENT}   delivered") and f"message {mid}" in out


def test_interrupt_and_expiry_ride_its_line_zero(digest):
    digest.shim(name="main", scope="proj-a")
    result = send(PARENT, interrupt=True, expires="1h")
    assert result.outcome == "sent", result.lines()
    ml = machine_line.parse(digest.record.get_message(result.message_id).content)
    assert ml.interrupt and ml.expires_at is not None
    assert ml.recipients == (PARENT,)


def test_a_cold_parent_holds_it_as_any_message(digest):
    reviewer = reviewer_with_session(digest)
    write_transcript(SESS_REVIEWER, 2 * HOUR + 600)
    result = send("reviewer@proj-a", parent="reviewer@proj-a")
    assert result.outcome == "sent", result.lines()
    assert [r.delivery for r in result.recipients] == ["held-cold"]
    assert wait_until(lambda: result.message_id in held_of(digest))
    assert reviewer.queue == []


def test_a_global_parents_scoped_sender_is_global(digest):
    digest.shim(name="lead", global_=True)
    result = send("lead", parent="lead")
    assert result.outcome == "sent", result.lines()
    assert digest.record.get_message(result.message_id).author_id == "lead-digest"


# --------------------------------------------------------------------------- the parent alone

@pytest.mark.parametrize("to", [["other@proj-a"], [PARENT, "other@proj-a"], ["main"]])
def test_a_send_to_anyone_but_the_parent_is_refused_before_the_post(digest, to):
    before = len(history(digest))
    text = refused(send(to), "scoped-recipient")
    assert f"{FORMED} sends only to {PARENT}" in text
    assert len(history(digest)) == before


@pytest.mark.parametrize("names", [["other@proj-a"], [PARENT, "other@proj-a"]])
def test_the_daemon_refuses_another_recipient_with_the_verb_bypassed(digest, client,
                                                                     names):
    claim_file("elsewhere", Root(os.getpid() + 1_000_000, "0", TREE))
    before = len(history(digest))
    with pytest.raises(DaemonError) as e:
        client.send(FORMED, token(), machine_line.compose(
            names, "x", posture=machine_line.POSTURE_NONE))
    assert e.value.code == wire.SCOPED_RECIPIENT
    assert "other@proj-a" in str(e.value)
    assert len(history(digest)) == before


def test_the_daemon_refuses_a_send_that_names_no_recipient(digest, client):
    with pytest.raises(DaemonError) as e:
        client.send(FORMED, token(), "no machine line")
    assert e.value.code == wire.SCOPED_RECIPIENT


# --------------------------------------------------------------------------- nobody mails it

def test_nobody_sends_to_it_or_replies_to_it(digest):
    digest.shim(name="main", scope="proj-a")
    before = len(history(digest))
    code, out = digest.cli(["send", "--posture", "none", FORMED, "hello"],
                           name="main", scope="proj-a")
    assert code == moxi_exit.REFUSED, out
    assert "receives no mail" in out
    assert len(history(digest)) == before
    mid = send(PARENT, "the digest").message_id
    code, out = digest.cli(["reply", mid, "thanks"], name="main", scope="proj-a")
    assert code == moxi_exit.REFUSED, out
    assert f"{FORMED} is a send-only identity and receives no mail" in out


def test_the_daemon_refuses_mail_to_it_with_the_verb_bypassed(digest, client):
    planner = client.bind(name="planner", scope="proj-a")["token"]
    with pytest.raises(DaemonError) as e:
        client.send("planner@proj-a", planner, machine_line.compose([FORMED], "x"))
    assert e.value.code == wire.NOT_MAILABLE


# --------------------------------------------------------------------------- who sends

def test_a_send_from_inside_a_session_is_refused(digest):
    claim_file("planner", root_of(os.getppid(), TREE))
    text = refused(send(PARENT), "name-reserved")
    assert "from inside a session" in text


def test_a_send_from_a_claim_holder_itself_is_refused(digest, listening):  # noqa: F811
    claim_file("planner", root_of(os.getpid(), SELF), socket=listening)
    refused(send(PARENT), "name-reserved")


def test_a_sessions_token_never_sends_under_it(digest, client):
    planner = client.bind(name="planner", scope="proj-a")["token"]
    with pytest.raises(DaemonError) as e:
        client.send(FORMED, planner, machine_line.compose([PARENT], "x"))
    assert e.value.code == wire.NAME_RESERVED
    assert "a session's token never sends under it" in str(e.value)


def test_a_wrong_token_is_refused_credential(digest):
    TokenStore().write_secret(reserved.owner("pager"), scoped.secret_id("digest"), "nope")
    refused(send(PARENT), "credential")


def test_another_modules_send_is_refused_undeclared(digest):
    text = refused(send(PARENT, module="memory"), "scoped-undeclared")
    assert "memory declares no word" in text


def test_a_missing_token_is_refused_credential(digest):
    TokenStore().delete_secret(reserved.owner("pager"), scoped.secret_id("digest"))
    assert "moxi doctor" in refused(send(PARENT), "credential")


# --------------------------------------------------------------------------- in conflict

def test_a_word_two_modules_declare_sends_nothing(digest, client):
    declare(digest, {**DIGEST, "memory": {"digest": "x"}})
    assert "in conflict" in refused(send(PARENT), "scoped-conflict")
    with pytest.raises(DaemonError) as e:
        client.send(FORMED, "anything", machine_line.compose([PARENT], "x"))
    assert e.value.code == wire.SCOPED_CONFLICT


def test_a_held_name_puts_that_parent_in_conflict(rig, client):
    held = client.bind(name="main-digest", scope="proj-a")["token"]
    declare(rig)
    text = refused(send(PARENT), "scoped-conflict")
    assert "a live session holds it" in text
    # The holder still sends as itself; another parent's scoped sender sends.
    client.send(FORMED, held, machine_line.compose([PARENT], "still mine"))
    rig.shim(name="other", scope="proj-a")
    other = send("other@proj-a", parent="other@proj-a")
    assert other.outcome == "sent", other.lines()


def test_a_program_name_of_the_form_puts_that_parent_in_conflict(rig):
    reserved.register("lead-digest", chain=secrets.from_config())
    declare(rig)
    assert "send-only name" in refused(send("lead", parent="lead"), "scoped-conflict")


# --------------------------------------------------------------------------- an older daemon

def test_a_daemon_without_scoped_senders_is_told_before_the_post(digest, monkeypatch):
    monkeypatch.setattr(sending, "SCOPED_CAPABILITY", "x-not-offered")
    before = len(history(digest))
    result = send(PARENT)
    assert (result.outcome, result.refusal.code) == ("failed", "protocol-mismatch")
    assert "has no scoped senders" in result.refusal.text
    assert len(history(digest)) == before

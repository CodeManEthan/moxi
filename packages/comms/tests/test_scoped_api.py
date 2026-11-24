"""Scoped senders through comms' API (interface 3): `send_scoped`, and the read-back,
`scoped_sender` and `scoped_state`, which read comms' store alone, never the daemon, and
read a name back as a scoped sender only while it is active."""

from __future__ import annotations

import pytest

from moxi_comms import api, reserved, scoped, secrets
from moxi_comms.client import DaemonClient
from moxi_comms.paths import daemon_socket
from moxi_comms.tokens import TokenStore

from test_scoped_send import DIGEST, FORMED, PARENT, declare, digest  # noqa: F401
from test_send_as import doctor_records


@pytest.fixture
def client(rig):
    """A connection that holds what it binds until the test ends."""
    c = DaemonClient(daemon_socket()).connect()
    yield c
    c.close()


# --------------------------------------------------------------------------- the send

def test_send_scoped_reaches_the_parent_with_no_reply(digest):  # noqa: F811
    digest.shim(name="main", scope="proj-a")
    result = api.send_scoped("pager", PARENT, PARENT, "the digest", interrupt=True,
                             expires="1h")
    assert result.outcome == "sent", result.lines()
    msg = digest.record.get_message(result.message_id)
    assert (msg.author_id, msg.author_kind) == (FORMED, "reserved")
    assert "[no reply]" in msg.content.splitlines()[0]


def test_send_scoped_names_the_word_when_asked(digest):  # noqa: F811
    result = api.send_scoped("pager", PARENT, [PARENT], "x", word="digest")
    assert result.outcome == "sent", result.lines()


@pytest.mark.parametrize("to", ["other@proj-a", f"{PARENT},other@proj-a"])
def test_send_scoped_to_anyone_but_the_parent_is_refused(digest, to):  # noqa: F811
    result = api.send_scoped("pager", PARENT, to, "x")
    assert (result.outcome, result.refusal.code) == ("refused", "scoped-recipient")


def test_send_scoped_for_a_module_that_declares_no_word_is_refused(digest):  # noqa: F811
    result = api.send_scoped("memory", PARENT, PARENT, "x")
    assert (result.outcome, result.refusal.code) == ("refused", "scoped-undeclared")


@pytest.mark.parametrize("call", [
    lambda: api.send_scoped("pager", PARENT, "", "x"),
    lambda: api.send_scoped("pager", "not a name", "x", "x"),
    lambda: api.send_scoped("pager", PARENT, PARENT, "x", expires="soon"),
])
def test_send_scoped_raises_for_arguments_it_cant_take(digest, call):  # noqa: F811
    with pytest.raises(ValueError):
        call()


def test_a_token_this_process_cant_read_leaves_a_record_for_the_doctor(
        digest):  # noqa: F811
    """As for a send-only name: the process that sends may read the chain differently
    from the shell the doctor runs in, so its miss is recorded, and a fresh token or a
    send that works clears it."""
    key = scoped.record_key("digest")
    TokenStore().delete_secret(reserved.owner("pager"), scoped.secret_id("digest"))
    result = api.send_scoped("pager", PARENT, PARENT, "x")
    assert result.refusal.code == "credential"
    record = doctor_records()[key]
    assert "comms can't read pager's token for the scoped sender word digest" in \
        record["text"]
    assert record["fix"] == "moxi comms reserved renew --word digest"
    reserved.renew_word("digest", chain=secrets.from_config())
    assert key not in doctor_records()
    TokenStore().write_secret(reserved.owner("pager"), scoped.secret_id("digest"), "nope")
    assert api.send_scoped("pager", PARENT, PARENT, "x").refusal.code == "credential"
    assert key in doctor_records()
    reserved.renew_word("digest", chain=secrets.from_config())
    assert key not in doctor_records()
    TokenStore().delete_secret(reserved.owner("pager"), scoped.secret_id("digest"))
    api.send_scoped("pager", PARENT, PARENT, "x")
    assert key in doctor_records()
    declare(digest, {})                                   # the word freed
    assert key not in doctor_records()


def test_a_send_refused_before_the_token_leaves_no_record(digest):  # noqa: F811
    TokenStore().delete_secret(reserved.owner("pager"), scoped.secret_id("digest"))
    assert api.send_scoped("memory", PARENT, PARENT, "x").refusal.code == \
        "scoped-undeclared"
    assert api.send_scoped("pager", PARENT, "other@proj-a", "x").refusal.code == \
        "scoped-recipient"
    assert scoped.record_key("digest") not in doctor_records()


# --------------------------------------------------------------------------- scoped_sender

@pytest.mark.parametrize("name,parent", [(FORMED, PARENT), ("lead-digest", "lead"),
                                         ("agent-3-digest@acme", "agent-3@acme")])
def test_an_active_scoped_sender_reads_back_with_its_module_and_parent(digest, name,
                                                                       parent):  # noqa: F811
    assert api.scoped_sender(name) == api.ScopedSender(
        qualified=name, module="pager", word="digest", parent=parent)


def test_a_global_name_reads_back_bare(digest):  # noqa: F811
    assert api.scoped_sender("lead-digest@global").qualified == "lead-digest"


def test_the_read_back_asks_no_daemon(digest):  # noqa: F811
    digest.daemon.stop()
    assert api.scoped_sender(FORMED).module == "pager"
    assert api.scoped_state("pager", PARENT).can_send


@pytest.mark.parametrize("name", [PARENT, "main-digests@proj-a", "main-", "digest",
                                  "main-digest@", "", "a b-digest", None, 7])
def test_a_name_not_of_the_form_reads_back_as_none(digest, name):  # noqa: F811
    assert api.scoped_sender(name) is None


def test_a_name_of_a_word_no_module_declares_reads_back_as_none(rig):
    assert api.scoped_sender(FORMED) is None
    declare(rig)
    assert api.scoped_sender(FORMED) is not None
    declare(rig, {})
    assert api.scoped_sender(FORMED) is None


def test_a_word_in_conflict_reads_back_as_none(digest):  # noqa: F811
    declare(digest, {**DIGEST, "memory": {"digest": "x"}})
    assert api.scoped_sender(FORMED) is None


def test_a_name_a_session_holds_reads_back_as_none(rig, client):
    client.bind(name="main-digest", scope="proj-a")
    declare(rig)
    assert api.scoped_sender(FORMED) is None
    assert api.scoped_sender("other-digest@proj-a").parent == "other@proj-a"


def test_a_name_a_program_holds_reads_back_as_none(rig):
    reserved.register("lead-digest", chain=secrets.from_config())
    declare(rig)
    assert api.scoped_sender("lead-digest") is None


def test_a_store_that_cant_be_read_reads_back_as_none(digest, monkeypatch):  # noqa: F811
    import sqlite3

    from moxi_comms.store import open as store_open

    def refuse(*a, **kw):
        raise sqlite3.OperationalError("unable to open database file")

    monkeypatch.setattr(store_open, "open_read", refuse)
    assert api.scoped_sender(FORMED) is None
    got = api.scoped_state("pager", PARENT)
    assert (got.can_send, got.reason) == (False, "unreadable")


# --------------------------------------------------------------------------- scoped_state

def test_an_active_scoped_sender_can_send(digest):  # noqa: F811
    got = api.scoped_state("pager", PARENT)
    assert isinstance(got, api.ScopedState)
    assert (got.can_send, got.qualified, got.word, got.parent, got.reason) == \
        (True, FORMED, "digest", PARENT, None)


def test_a_module_that_declares_no_word_cant_send(digest):  # noqa: F811
    got = api.scoped_state("memory", PARENT)
    assert (got.can_send, got.reason) == (False, "undeclared")
    assert got.why == "memory declares no word for scoped senders"
    got = api.scoped_state("pager", PARENT, word="other")
    assert (got.can_send, got.reason, got.qualified) == \
        (False, "undeclared", "main-other@proj-a")


def test_a_word_in_conflict_cant_send_for_either_module(digest):  # noqa: F811
    declare(digest, {**DIGEST, "memory": {"digest": "x"}})
    for module in ("pager", "memory"):
        got = api.scoped_state(module, PARENT)
        assert (got.can_send, got.reason) == (False, "conflict"), module


def test_a_held_name_puts_only_that_parent_in_conflict(rig, client):
    client.bind(name="main-digest", scope="proj-a")
    declare(rig)
    got = api.scoped_state("pager", PARENT)
    assert (got.can_send, got.reason) == (False, "conflict")
    assert "a live session holds it" in got.why
    assert api.scoped_state("pager", "other@proj-a").can_send


def test_scoped_state_raises_for_a_parent_that_is_no_name_or_an_unnamed_word(rig):
    declare(rig, {"pager": {"digest": "x", "nightly": "y"}})
    with pytest.raises(ValueError):
        api.scoped_state("pager", "not a name")
    with pytest.raises(ValueError):
        api.scoped_state("pager", PARENT)
    assert api.scoped_state("pager", PARENT, word="nightly").qualified == \
        "main-nightly@proj-a"


def test_the_reasons_are_pinned():
    assert api.SCOPED_REASONS == ("undeclared", "conflict", "unreadable")

"""A scoped sender's name and who holds it: comms forms it from the parent and the word,
no session binds and no program registers a name of a declared word's form, a name of
that form held before stays its holder's and puts that one parent's scoped sender in
conflict, which comms' doctor reports."""

from __future__ import annotations

import pytest

from conftest import store_rows, wait_until

from moxi_comms import machine_line, reserved, scoped, secrets, wire
from moxi_comms.client import DaemonClient, DaemonError
from moxi_comms.paths import daemon_socket
from moxi_comms.store import open_read

DIGEST = {"pager": {"digest": "the nightly digest a session gets"}}


def declare(rig=None, words=None):
    if rig is not None:
        rig.clock.advance(1)
    return reserved.reconcile(declared={}, words=DIGEST if words is None else words,
                              chain=secrets.from_config(),
                              **({"clock": rig.clock} if rig is not None else {}))


def bind(**kw):
    c = DaemonClient(daemon_socket()).connect()
    try:
        return c.bind(**kw)
    finally:
        c.close()


@pytest.fixture
def client(rig):
    """A connection that holds what it binds until the test ends."""
    c = DaemonClient(daemon_socket()).connect()
    yield c
    c.close()


def state(module, parent, word=None):
    conn = open_read()
    try:
        return scoped.state(conn, module, parent, word)
    finally:
        conn.close()


# --------------------------------------------------------------------------- the name

@pytest.mark.parametrize("parent,want", [
    ("builder@acme", "builder-digest@acme"),
    ("fleet-lead", "fleet-lead-digest"),
    ("agent-3", "agent-3-digest"),
])
def test_comms_forms_the_name_in_the_parents_scope(parent, want):
    assert scoped.form(parent, "digest") == want
    assert scoped.split(want) == (parent, "digest")


@pytest.mark.parametrize("spelled", ["agent-3@proj-a", "agent-3@global", "agent-3"])
def test_an_ephemeral_parent_is_the_global_one_whatever_scope_it_is_spelled_with(spelled):
    """comms' one identity rule: `agent-N`, however spelled, is the global ephemeral, so
    its scoped sender is the global `agent-3-digest`, and the parent it reads back is
    the identity a send to `agent-3@proj-a` resolves to."""
    assert scoped.parent_of(spelled) == "agent-3"
    assert scoped.form(spelled, "digest") == "agent-3-digest"
    assert scoped.split("agent-3-digest") == ("agent-3", "digest")


def test_a_scoped_spelling_of_an_ephemeral_parents_name_is_not_of_the_form():
    assert scoped.split("agent-3-digest@proj-a") is None
    assert scoped.split("agent-3x-digest@proj-a") == ("agent-3x@proj-a", "digest")


def test_a_scoped_ordinary_parent_keeps_its_scope():
    assert scoped.parent_of("builder@acme") == "builder@acme"
    assert scoped.parent_of("builder@global") == "builder"
    assert scoped.form("builder@global", "digest") == "builder-digest"


@pytest.mark.parametrize("parent", ["", "a b", "x@", "x@a b", None])
def test_a_parent_that_is_no_name_is_refused(parent):
    with pytest.raises(ValueError):
        scoped.form(parent, "digest")


@pytest.mark.parametrize("name", ["digest", "-digest", "x-Digest", "x-"])
def test_a_name_without_a_word_after_a_dash_has_no_scoped_form(name):
    assert scoped.split(name) is None


# --------------------------------------------------------------------------- binds

def test_no_session_binds_a_name_of_a_declared_words_form(rig):
    declare(rig)
    for name, kw in (("lead-digest", {"global_": True}),
                     ("builder-digest", {"scope": "acme"})):
        with pytest.raises(DaemonError) as e:
            bind(name=name, **kw)
        assert e.value.code == wire.NAME_RESERVED
        assert "scoped sender for" in str(e.value) and "no session binds it" in str(e.value)
    assert "lead-digest" not in rig.daemon.bindings


def test_a_word_in_conflict_still_refuses_binds(rig):
    declare(rig, {**DIGEST, "ledger": {"digest": "x"}})
    with pytest.raises(DaemonError) as e:
        bind(name="lead-digest", global_=True)
    assert e.value.code == wire.NAME_RESERVED
    assert "in conflict" in str(e.value)


def test_a_freed_word_lets_the_name_bind_again(rig):
    declare(rig)
    declare(rig, {})
    assert bind(name="lead-digest", global_=True)["identity"]["qualified"] == "lead-digest"


def test_a_name_that_only_ends_like_a_word_of_another_shape_binds(rig):
    declare(rig)
    assert bind(name="digest", global_=True)["identity"]["qualified"] == "digest"
    assert bind(name="lead-digests", global_=True)


def test_no_program_registers_a_name_of_a_declared_words_form(rig):
    declare(rig)
    with pytest.raises(reserved.Refused) as e:
        reserved.register("lead-digest", chain=secrets.from_config())
    assert "form of a scoped sender of the word digest, which pager declares" in str(e.value)
    assert "no program registers it" in str(e.value)


# --------------------------------------------------------------------------- held before

def test_a_name_held_before_the_word_is_its_holders_and_the_parent_is_in_conflict(
        rig, client):
    token = client.bind(name="lead-digest", global_=True)["token"]
    declare(rig)
    assert "lead-digest" in rig.daemon.bindings
    got = state("pager", "lead")
    assert (got.can_send, got.reason, got.qualified) == (False, "conflict", "lead-digest")
    assert "a live session holds it" in got.why
    # Its holder keeps its mail and its sends.
    assert not rig.daemon.resolve("lead-digest").send_only
    client.send("lead-digest", token, machine_line.compose(["lead"], "still mine"))
    # Another parent's scoped sender is unaffected.
    assert state("pager", "other").can_send


def test_a_program_name_of_the_form_puts_the_parent_in_conflict(rig):
    reserved.register("lead-digest", chain=secrets.from_config())
    declare(rig)
    got = state("pager", "lead")
    assert (got.can_send, got.reason) == (False, "conflict")
    assert "send-only name" in got.why


def test_the_doctor_reports_a_parent_in_conflict(rig, client):
    client.bind(name="lead-digest", global_=True)
    declare(rig)
    rows = [r for r in reserved.doctor_rows(declared={}, words=DIGEST,
                                            chain=secrets.from_config())
            if r.name == "scoped"]
    (row,) = rows
    assert row.state == "warn"
    assert row.detail == ("lead-digest is held (a live session holds it), so pager's "
                          "scoped sender for lead is in conflict: a send under it is "
                          "refused")


def test_mail_queued_before_the_word_for_a_name_nothing_holds_is_dropped(rig):
    mid = rig.record.post_as_identity(
        "planner", rig.names_channel,
        machine_line.compose(["lead-digest"], "before the word")).id
    assert wait_until(lambda: mid in rig.daemon.queue.get("lead-digest", {}))
    declare(rig)
    rig.daemon._explain_query({"identity": "lead-digest", "message_id": mid})
    assert rig.daemon.books.dropped_reason("lead-digest", mid) == "reserved"
    assert store_rows("lead-digest") == []

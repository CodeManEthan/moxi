"""`api.send_as`: a module's send under the send-only name reserved for it."""

from __future__ import annotations

import os

import pytest

from conftest import wait_until

from moxi import exit as moxi_exit
from moxi_comms import api, reserved, secrets
from moxi_comms.adapter import frame_line
from moxi_comms.tokens import TokenStore


@pytest.fixture
def console(rig):
    """`console` reserved for the launcher, its token in the configured store."""
    reserved.reconcile(declared={"launcher": ["console"]}, chain=secrets.from_config(),
                       clock=rig.clock)
    return rig


def test_send_as_sends_under_the_reserved_name_with_no_reply(console):
    result = api.send_as("launcher", "console", ["main@proj-a"], "relaunch failed")
    assert result.outcome == "sent", result.lines()
    msg = console.record.get_message(result.message_id)
    assert msg.author_id == "console" and msg.author_kind == "reserved"
    assert "[no reply]" in msg.content.splitlines()[0]


def test_send_as_without_the_token_is_refused_credential(console):
    TokenStore().delete_secret(reserved.owner("launcher"), reserved.secret_id("console"))
    result = api.send_as("launcher", "console", ["main@proj-a"], "x")
    assert (result.outcome, result.refusal.code) == ("refused", "credential")
    assert "moxi doctor" in result.refusal.text


def doctor_records():
    """The records `moxi doctor` shows as comms' rows, by key."""
    import json

    from moxi import doctor, paths
    path = paths._layout("comms").state_dir() / doctor.RECORDS_FILE
    return json.loads(path.read_text()) if path.exists() else {}


def test_a_token_this_process_cant_read_leaves_a_record_for_the_doctor(console):
    """The process that sends (a module's unit, a timer) may read the credential chain
    differently from the shell the doctor runs in, so its failure is recorded where any
    later doctor run finds it, and a send that works again, or a renew, clears it."""
    key = "reserved-token-console"
    TokenStore().delete_secret(reserved.owner("launcher"), reserved.secret_id("console"))
    result = api.send_as("launcher", "console", ["main@proj-a"], "x")
    assert result.refusal.code == "credential"
    record = doctor_records()[key]
    assert "comms can't read the token for console from the environment of" in \
        record["text"]
    assert f"pid {os.getpid()}" in record["text"]
    assert record["fix"] == "moxi comms reserved renew console"
    reserved.renew("console", chain=secrets.from_config())
    assert key not in doctor_records()
    api.send_as("pager", "console", ["main@proj-a"], "x")
    assert key in doctor_records()
    assert api.send_as("launcher", "console", ["main@proj-a"], "x").outcome == "sent"
    assert key not in doctor_records()


def test_send_as_with_a_wrong_token_is_refused_by_the_daemon(console):
    TokenStore().write_secret(reserved.owner("launcher"), reserved.secret_id("console"),
                              "not-the-token")
    result = api.send_as("launcher", "console", ["main@proj-a"], "x")
    assert (result.outcome, result.refusal.code) == ("refused", "credential")


def test_send_as_for_another_module_finds_no_token(console):
    result = api.send_as("pager", "console", ["main@proj-a"], "x")
    assert (result.outcome, result.refusal.code) == ("refused", "credential")


def test_send_as_with_the_daemon_down_is_offline(console):
    console.daemon.stop()
    result = api.send_as("launcher", "console", ["main@proj-a"], "x")
    assert result.outcome == "offline"


def test_a_send_as_message_reads_send_only_in_its_frame_and_history(console):
    shim = console.shim(name="main", scope="proj-a")
    result = api.send_as("launcher", "console", ["main@proj-a"], "relaunch failed")
    assert result.outcome == "sent", result.lines()
    mid = result.message_id
    assert wait_until(lambda: any(p["message_id"] == mid for p in shim.queue))
    (payload,) = [p for p in shim.queue if p["message_id"] == mid]
    assert payload["author"]["tier"] == "send-only"
    want = f"[comms] from console (send-only) · msg {mid} · [no reply]"
    assert frame_line(payload) == want
    code, out = console.cli(["history", "console"])
    assert code == 0, out
    (row,) = [ln for ln in out.splitlines() if ln.startswith(f"{mid} ")]
    assert "from console (send-only, verified)" in row


def test_the_mark_holds_after_the_free_and_a_later_holder_has_none(console):
    sent = api.send_as("launcher", "console", ["main@proj-a"], "while reserved")
    assert sent.outcome == "sent", sent.lines()
    # An older comms wrote a module's post as `identity`: the reserved interval marks it.
    older = console.record.post_as_identity(
        "console", console.names_channel, "::to main@proj-a [no reply]\nan older post")
    console.clock.advance(5)
    reserved.reconcile(declared={}, chain=secrets.from_config(), clock=console.clock)
    console.clock.advance(5)
    later = console.record.post_as_identity(
        "console", console.names_channel, "::to main@proj-a\nfrom a later holder")
    code, out = console.cli(["history", "console"])
    assert code == 0, out
    rows = {ln.split()[0]: ln for ln in out.splitlines() if ln[:1].isdigit()}
    assert "(send-only, verified)" in rows[sent.message_id]
    assert "(send-only, verified)" in rows[older.id]
    assert "from console (chosen, verified)" in rows[later.id]


def test_a_later_holder_of_a_freed_name_cant_delete_the_modules_post(console):
    sent = api.send_as("launcher", "console", ["main@proj-a"], "while reserved")
    assert sent.outcome == "sent", sent.lines()
    console.clock.advance(5)
    reserved.reconcile(declared={}, chain=secrets.from_config(), clock=console.clock)
    console.shim(name="console")
    assert wait_until(lambda: "console" in console.daemon.bindings)
    code, out = console.cli(["remove", sent.message_id], name="console")
    assert code == moxi_exit.REFUSED and "not yours" in out, out
    assert console.record.get_message(sent.message_id).withdrawn_at is None

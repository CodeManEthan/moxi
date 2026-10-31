"""`api.send_as`: a module's send under the send-only name reserved for it."""

from __future__ import annotations

import pytest

from conftest import wait_until

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
    assert msg.author_id == "console" and msg.author_kind == "identity"
    assert "[no reply]" in msg.content.splitlines()[0]


def test_send_as_without_the_token_is_refused_credential(console):
    TokenStore().delete_secret(reserved.owner("launcher"), reserved.secret_id("console"))
    result = api.send_as("launcher", "console", ["main@proj-a"], "x")
    assert (result.outcome, result.refusal.code) == ("refused", "credential")
    assert "moxi doctor" in result.refusal.text


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
    console.clock.advance(5)
    reserved.reconcile(declared={}, chain=secrets.from_config(), clock=console.clock)
    console.clock.advance(5)
    later = console.record.post_as_identity(
        "console", console.names_channel, "::to main@proj-a\nfrom a later holder")
    code, out = console.cli(["history", "console"])
    assert code == 0, out
    rows = {ln.split()[0]: ln for ln in out.splitlines() if ln[:1].isdigit()}
    assert "(send-only, verified)" in rows[sent.message_id]
    assert "from console (chosen, verified)" in rows[later.id]

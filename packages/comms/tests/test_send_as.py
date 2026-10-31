"""`api.send_as`: a module's send under the send-only name reserved for it."""

from __future__ import annotations

import pytest

from moxi_comms import api, reserved, secrets
from moxi_comms.tokens import TokenStore


@pytest.fixture
def console(rig):
    """`console` reserved for the launcher, its token in the configured store."""
    reserved.reconcile(declared={"launcher": ["console"]}, chain=secrets.from_config())
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

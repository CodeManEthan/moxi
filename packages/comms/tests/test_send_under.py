"""A program's send under its send-only name: `moxi comms send --as NAME` and
`api.send_under`, with no claim socket, from outside every session."""

from __future__ import annotations

import os

import pytest

from conftest import wait_until

from moxi import exit as moxi_exit
from moxi_comms import api, claimfile, reserved, secrets, wire
from moxi_comms.adapter import frame_line
from moxi_comms.client import DaemonClient, DaemonError
from moxi_comms.membership import TREE, root_of
from moxi_comms.paths import daemon_socket
from moxi_comms.store import open_write
from moxi_comms.tokens import TokenStore
from moxi.store import transaction

TO = "main@proj-a"


@pytest.fixture
def ticker(rig):
    """`ticker` registered by a program, its token in the configured store; `console`
    reserved for the launcher."""
    reserved.register("ticker", chain=secrets.from_config())
    reserved.reconcile(declared={"launcher": ["console"]}, chain=secrets.from_config())
    return rig


def token():
    return TokenStore().read_secret(reserved.program_owner(),
                                    reserved.program_secret_id("ticker"))


def doctor_records():
    import json

    from moxi import doctor, paths
    path = paths._layout("comms").state_dir() / doctor.RECORDS_FILE
    return json.loads(path.read_text()) if path.exists() else {}


def test_send_under_posts_under_the_name_with_no_reply_and_the_send_only_mark(ticker):
    shim = ticker.shim(name="main", scope="proj-a")
    result = api.send_under("ticker", [TO], "a chain notice")
    assert result.outcome == "sent", result.lines()
    mid = result.message_id
    msg = ticker.record.get_message(mid)
    assert (msg.author_id, msg.author_kind) == ("ticker", "reserved")
    assert wait_until(lambda: any(p["message_id"] == mid for p in shim.queue))
    (payload,) = [p for p in shim.queue if p["message_id"] == mid]
    assert frame_line(payload) == f"[comms] from ticker (send-only) · msg {mid} · [no reply]"


@pytest.mark.parametrize("posture", [[], ["--posture", "none"]])
def test_send_as_on_the_command_line_posts_under_the_name(ticker, posture):
    code, out = ticker.cli(["send", "--as", "ticker", TO, "a notice", *posture])
    assert code == moxi_exit.OK, out
    mid = out.split()[1]
    msg = ticker.record.get_message(mid)
    assert msg.author_id == "ticker" and "[no reply]" in msg.content.splitlines()[0]


@pytest.mark.parametrize("flags, words", [
    (["--posture", "once"], "--as sends with posture none, not once"),
    (["--reply-to", "123"], "--as doesn't take --reply-to"),
    (["--standing"], "--as doesn't take --standing"),
    (["--audience", "operator"], "--as doesn't take --audience"),
    (["--held"], "--as doesn't take --held"),
    (["--cap", "3"], "--as doesn't take --cap"),
    (["--no-cap"], "--as doesn't take --no-cap"),
    (["--new"], "--as doesn't take --new"),
    (["--new-name"], "--as doesn't take --new-name"),
])
def test_send_as_refuses_a_flag_that_shapes_a_reply_as_a_usage_error(ticker, flags,
                                                                      words):
    before = len(ticker.record.history(ticker.names_channel))
    code, out = ticker.cli(["send", "--as", "ticker", TO, "x", *flags])
    assert code == moxi_exit.USAGE and words in out, out
    assert len(ticker.record.history(ticker.names_channel)) == before


def test_send_as_takes_expires_interrupt_and_check(ticker):
    before = len(ticker.record.history(ticker.names_channel))
    code, out = ticker.cli(["send", "--as", "ticker", TO, "x", "--expires", "10m",
                            "--interrupt", "--check"])
    assert code == moxi_exit.OK, out
    assert len(ticker.record.history(ticker.names_channel)) == before
    code, out = ticker.cli(["send", "--as", "ticker", TO, "x", "--expires", "10m",
                            "--interrupt"])
    assert code == moxi_exit.OK, out
    line_zero = ticker.record.get_message(out.split()[1]).content.splitlines()[0]
    assert "[interrupt]" in line_zero and "[expires:" in line_zero, line_zero


def test_send_under_a_modules_name_is_refused_module_reserved(ticker):
    result = api.send_under("console", [TO], "x")
    assert (result.outcome, result.refusal.code) == ("refused", "module-reserved")
    assert result.refusal.text.startswith(
        "console is reserved for launcher, which sends under it from its own code; --as "
        "sends only under a name registered with `moxi comms send-only add`")


def test_send_under_a_name_no_program_registered_is_refused_not_registered(ticker):
    result = api.send_under("nobody", [TO], "x")
    assert (result.outcome, result.refusal.code) == ("refused", "not-registered")
    assert "nobody isn't a registered send-only name: moxi comms send-only add nobody" \
        in result.refusal.text
    conn = open_write()
    try:
        with transaction(conn) as c:
            c.execute("INSERT INTO reserved(name, state) VALUES ('relay', 'unowned')")
    finally:
        conn.close()
    result = api.send_under("relay", [TO], "x")
    assert result.refusal.code == "not-registered"
    assert "(an older comms kept it)" in result.refusal.text


def test_a_missing_token_is_refused_credential_and_leaves_the_doctors_record(ticker):
    key = "reserved-token-ticker"
    kept = token()
    TokenStore().delete_secret(reserved.program_owner(),
                               reserved.program_secret_id("ticker"))
    result = api.send_under("ticker", [TO], "x")
    assert (result.outcome, result.refusal.code) == ("refused", "credential")
    record = doctor_records()[key]
    assert "where a program sends under it" in record["text"]
    assert f"pid {os.getpid()}" in record["text"]
    assert record["fix"] == "moxi comms send-only renew ticker, run where ticker sends from"
    TokenStore().write_secret(reserved.program_owner(),
                              reserved.program_secret_id("ticker"), kept)
    assert api.send_under("ticker", [TO], "x").outcome == "sent"
    assert key not in doctor_records()


def test_renew_and_remove_clear_the_doctors_record(ticker):
    key = "reserved-token-ticker"
    TokenStore().delete_secret(reserved.program_owner(),
                               reserved.program_secret_id("ticker"))
    api.send_under("ticker", [TO], "x")
    assert key in doctor_records()
    reserved.renew_program("ticker", chain=secrets.from_config())
    assert key not in doctor_records()
    TokenStore().delete_secret(reserved.program_owner(),
                               reserved.program_secret_id("ticker"))
    api.send_under("ticker", [TO], "x")
    reserved.remove_program("ticker", chain=secrets.from_config())
    assert key not in doctor_records()


def test_a_renewed_name_refuses_its_old_token(ticker):
    old = token()
    reserved.renew_program("ticker", chain=secrets.from_config())
    c = DaemonClient(daemon_socket()).connect()
    try:
        with pytest.raises(DaemonError) as e:
            c.send("ticker", old, "::to main@proj-a [no reply]\nx")
    finally:
        c.close()
    assert e.value.code == wire.CREDENTIAL
    assert api.send_under("ticker", [TO], "x").outcome == "sent"


def test_send_as_a_module_on_a_programs_name_is_refused_not_reserved(ticker):
    result = api.send_as("launcher", "ticker", [TO], "x")
    assert (result.outcome, result.refusal.code) == ("refused", "not-reserved")
    assert result.refusal.text.startswith(
        "ticker is registered by a program, not reserved for launcher")


def test_send_under_from_inside_a_session_is_refused_before_the_token_is_read(ticker):
    claimfile.write("planner", {"roots": [root_of(os.getppid(), TREE).as_dict()],
                                "state": "bound",
                                "claim": {"qualified": "planner@proj-a",
                                          "name": "planner"}})
    TokenStore().delete_secret(reserved.program_owner(),
                               reserved.program_secret_id("ticker"))
    result = api.send_under("ticker", [TO], "x")
    assert (result.outcome, result.refusal.code) == ("refused", "name-reserved")
    assert "from inside a session" in result.refusal.text
    assert "reserved-token-ticker" not in doctor_records()


def test_send_under_with_the_daemon_down_is_offline(ticker):
    ticker.daemon.stop()
    assert api.send_under("ticker", [TO], "x").outcome == "offline"

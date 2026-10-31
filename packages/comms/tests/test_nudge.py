"""Fanout without a nudge: every post is written by the daemon.

Layer 1 is in-process and layer 3 is the slow poll; both live in `local_record.py` and
are tested there. The middle layer was a `nudge` notification from a CLI process that
wrote the record itself. The operator now posts through the daemon's `send` as agents
do, so the daemon's own record fans every post out in-process, and `nudge` is no longer
a method.

The CLI here still runs on a SECOND `LocalRecord` on the same file (`cli_record`),
because that is what a CLI process is: a post it wrote itself would wait for the poll.
"""

from __future__ import annotations

import time

import pytest
from conftest import wait_for_queue, wait_until

from moxi import ipc
from moxi_comms import cli, wire
from moxi_comms.local_record import LocalRecord


@pytest.fixture
def cli_record(rig):
    """The CLI as its own process: a second record on the same `record.db`.

    Its own pump never runs (nothing subscribes to it), and it resolves the rig's minted
    webhook tokens out of the box's 0600 store, which is where provision wrote them
    -- so this really is what a second process sees.
    """
    record = LocalRecord(clock=rig.clock, poll_interval=3600)
    yield record
    record.close()


@pytest.fixture
def notified(monkeypatch):
    """Every method the CLI hands to `DaemonClient.notify`, in order, delivered as
    usual."""
    ops: list = []
    real = cli.DaemonClient.notify

    def spy(self, method, params=None):
        ops.append(method)
        return real(self, method, params)

    monkeypatch.setattr(cli.DaemonClient, "notify", spy)
    return ops


def quiet_pump(record, interval: float = 5.0) -> None:
    """Put the rig's 20 ms pump on a long wait, and start that wait NOW.

    `nudge()` sets the pump's wake event, so waking it once here makes it fan out, clear
    the event and re-enter the wait at the new interval. Without the realignment a pump
    pass could land inside the measurement by luck.
    """
    record.poll_interval = interval
    record.nudge()
    time.sleep(0.05)


def a_pair(rig):
    return rig.shim(name="planner", scope="proj-a"), \
        rig.shim(name="reviewer", scope="proj-a")


# ------------------------------------------------------------------- fanout at once

@pytest.mark.parametrize("backend", ["local"], indirect=True)
def test_an_agent_post_delivers_within_50ms(rig, cli_record, notified):
    """The daemon writes and fans out an agent post on the same call."""
    planner, reviewer = a_pair(rig)
    quiet_pump(rig.record)

    code, out = rig.cli(["send", "--posture", "none", "reviewer@proj-a",
                         "the daemon carries this"],
                        name="planner", scope="proj-a",
                        env={"MOXI_COMMS_BACKEND": "local"}, record=cli_record)
    t0 = time.monotonic()
    assert code == 0 and "sent" in out
    assert "nudge" not in notified

    assert wait_until(lambda: len(reviewer.queue) >= 1, timeout=0.05), \
        "the daemon's own record committed and fanned out the row"
    assert time.monotonic() - t0 < 0.05
    assert reviewer.queue[0]["text"] == "the daemon carries this"


@pytest.mark.parametrize("backend", ["local"], indirect=True)
def test_an_operator_post_goes_through_the_daemon_and_delivers_without_the_poll(
        rig, cli_record, notified):
    """The operator's post is the daemon's `send`, not a row the CLI writes: it is
    delivered long before the pump's next pass, and the CLI notifies nothing."""
    _planner, reviewer = a_pair(rig)
    quiet_pump(rig.record)

    code, out = rig.cli(["send", "reviewer@proj-a", "the operator's post"],
                        env={"MOXI_COMMS_BACKEND": "local",
                             "MOXI_COMMS_OPERATOR": rig.prov.operator_user_id},
                        record=cli_record)
    t0 = time.monotonic()
    assert code == 0, out
    assert notified == []
    assert wait_until(lambda: len(reviewer.queue) >= 1, timeout=2.0)
    assert time.monotonic() - t0 < 1.0, "the daemon fanned it out, not the 5 s pump"
    assert reviewer.queue[0]["text"] == "the operator's post"


# ------------------------------------------------------------------- an unknown method

def test_nudge_is_no_longer_a_method(rig):
    """Asked as a request, `nudge` is the ordinary method-not-found."""
    with pytest.raises(ipc.RpcError) as refused:
        rig.daemon._call(None, "nudge", {})
    assert refused.value.code == wire.METHOD_NOT_FOUND


def test_an_unknown_method_is_refused_and_the_connection_lives_on(rig):
    """A notification the daemon does not know is dropped; asked as a request it is
    the ordinary method-not-found, and the connection lives on."""
    with pytest.raises(ipc.RpcError) as refused:
        rig.daemon._call(None, "nudge-the-daemon", {})
    assert refused.value.code == wire.METHOD_NOT_FOUND

    client = cli.DaemonClient(rig.daemon.socket_path).connect()
    try:
        client.notify("nudge-the-daemon")
        assert "message_id" in client.call("max_id")
    finally:
        client.close()


# -------------------------------------------------------------------- the mock backend

def test_the_cli_on_the_mock_backend_sends_no_nudge(rig, notified):
    """The mock fans out in-process. The backend is the seam's answer here, not the
    record object's: this rig hands the CLI a record, and the selection chain answers
    mock because the environment names it (the default would be local)."""
    planner, reviewer = a_pair(rig)
    code, out = rig.cli(["send", "--posture", "none", "reviewer@proj-a",
                         "no nudge for the mock"],
                        name="planner", scope="proj-a",
                        env={"MOXI_COMMS_BACKEND": "mock"})
    assert code == 0 and "sent" in out
    assert "nudge" not in notified
    wait_for_queue(reviewer, 1)
    assert reviewer.queue[0]["text"] == "no nudge for the mock"

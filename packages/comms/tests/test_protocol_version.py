"""The wire version is agreed at the hello, never on the `bind` answer.

Every connection to the daemon opens with `rpc.hello`, which agrees one comms wire version.
The `bind` answer names none, and the Claude shim keeps no version notice of its own: a
daemon that can't speak this comms' wire refuses the hello, and the client says which
side is older.
"""

from __future__ import annotations

from moxi_comms import wire
from moxi_comms.adapters import claude_shim


def test_the_bind_answer_names_no_version_and_the_hello_agreed_one(rig):
    shim = rig.shim(name="planner", scope="proj-a")
    assert "protocol" not in shim._last_resp
    assert "slot" not in shim._last_resp
    agreed = shim._client._client.agreed
    assert agreed.version == wire.VERSIONS[1]
    assert set(agreed.capabilities) == set(wire.CAPABILITIES)


def test_the_shim_has_no_version_notice():
    for gone in ("PROTOCOL_DAEMON_OLDER", "PROTOCOL_SHIM_OLDER", "PROTOCOL_VERSION"):
        assert not hasattr(claude_shim, gone), gone
    assert not hasattr(claude_shim.ClaudeShim, "_check_protocol")

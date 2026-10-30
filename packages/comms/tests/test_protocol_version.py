"""The claims-protocol version on the `register` answer.

The daemon answers `register` with `protocol`, at 2. Nothing in comms reads it: the Claude
shim no longer compares it with its own and says nothing about a mismatch, in its log or
in a session.
"""

from __future__ import annotations

from moxi_comms.adapters import claude_shim
from moxi_comms.protocol import PROTOCOL_VERSION


def test_the_daemon_answers_register_with_its_protocol_version(rig):
    shim = rig.shim(name="planner", scope="proj-a")
    assert shim._last_resp["protocol"] == PROTOCOL_VERSION == 2
    assert "slot" not in shim._last_resp


def test_the_shim_has_no_version_notice():
    for gone in ("PROTOCOL_DAEMON_OLDER", "PROTOCOL_SHIM_OLDER", "PROTOCOL_VERSION"):
        assert not hasattr(claude_shim, gone), gone
    assert not hasattr(claude_shim.ClaudeShim, "_check_protocol")

"""The adapters as their sessions' claim holders: a shim respawned inside its live
session comes back holding its recorded claim, a session never inherits its wrapper's claim socket, and the codex app-server is
stamped with its adapter's own."""

from __future__ import annotations

from claim_kit import FakeProc, wait_for
from moxi_comms import claimfile, claimhold
from moxi_comms.adapters import session
from moxi_comms.adapters.codex import CodexAdapter
from moxi_comms.adapters.codex_roster import CodexAgent
from moxi_comms.claimhold import CLAIM_SOCKET_ENV
from moxi_comms.membership import TREE, Root
from fake_turns import FakeSession, fake_launch

PLANNER = "planner@proj-a"


# --------------------------------------------------------------------------- the Claude shim

def test_a_respawned_shim_comes_back_holding_its_recorded_claim(rig):
    """A shim respawned inside its still-live Claude session reads the session's claim
    file and registers what it records, whatever its own arguments say."""
    proc = FakeProc()
    root = proc.spawn()
    roots = [Root(root, f"s{root}", TREE)]
    first = rig.shim(name="planner", scope="proj-a",  claim_key="sess-r",
                     claim_roots=roots, claim_proc=proc)
    assert first.identity == PLANNER and PLANNER in rig.daemon.bindings
    first.stop()                                  # the file stays; the root still lives
    assert wait_for(lambda: PLANNER not in rig.daemon.bindings)
    assert claimfile.read("sess-r")["claim"]["qualified"] == PLANNER
    again = rig.shim(scope="proj-a", claim_key="sess-r", claim_roots=roots,
                     claim_proc=proc)             # no name of its own
    assert again.identity == PLANNER, "the recorded claim, not the respawn's arguments"
    assert again.claims.state == "bound" and again.claims.claim["qualified"] == PLANNER
    assert "slot" not in again.claims.claim
    assert PLANNER in rig.daemon.bindings
    assert claimfile.read("sess-r")["state"] == "bound"


def test_a_session_never_inherits_its_wrappers_claim_socket():
    env = session.stamped_env({CLAIM_SOCKET_ENV: "/parent.sock", "PATH": "/bin"}, name="a")
    assert CLAIM_SOCKET_ENV not in env
    assert env["MOXI_COMMS_NAME"] == "a" and env["PATH"] == "/bin"


# --------------------------------------------------------------------------- codex

def test_the_codex_adapter_stamps_its_own_claim_socket_on_the_app_server(rig, tmp_path):
    codex = CodexAdapter(CodexAgent(qualified="coder@proj-a", name="coder", scope="proj-a",
                                    cwd=str(tmp_path), thread_file=tmp_path / "coder.json"),
                         launch=fake_launch(FakeSession()), autoreconnect=False)
    try:
        codex.start()
        env = codex._launch.calls[0]["env"]
        assert env[CLAIM_SOCKET_ENV] == str(codex.claims.socket_path)
    finally:
        codex.stop()

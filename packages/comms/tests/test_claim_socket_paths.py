"""Where claim sockets live: `moxi.ipc.address("comms", "claim-<key>")`, so a runtime
root too long for a Unix socket path (macOS's `$TMPDIR` is) moves each claim socket to
the core's short path, and every reader, the guard and the cleanup follow it there."""

from __future__ import annotations

import os
import socket
import sys
from pathlib import Path

import pytest

from claim_kit import wait_for
from moxi import paths as core_paths
from moxi_comms import api, claimhold, listener, paths
from moxi_comms.adapters import session
from moxi_comms.claimhold import CLAIM_SOCKET_ENV, ClaimServer
from moxi_comms.membership import TREE, root_of

pytestmark = pytest.mark.skipif(sys.platform == "win32",
                                reason="no Unix socket paths on Windows")

SID = "long-root-session"


@pytest.fixture
def long_root(monkeypatch, tmp_path):
    """A runtime root whose claim socket paths are too long for a Unix socket. The short
    path the core falls back to is the test's own (the `env` fixture's)."""
    root = tmp_path / ("r" * 60) / ("u" * 60)
    (root / "comms").mkdir(parents=True)
    monkeypatch.setenv("MOXI_COMMS_RUNTIME_DIR", str(root))
    monkeypatch.setenv("MOXI_TURNS_RUNTIME_DIR", str(root))
    return root


def is_short(path: Path, root: Path) -> bool:
    return (len(os.fsencode(str(path))) < core_paths.SOCKET_MAX
            and not str(path).startswith(str(root)))


def test_a_claim_socket_is_the_cores_address_for_its_key():
    assert paths.claim_socket("abc") == paths.runtime_dir() / "claim-abc.sock"
    assert session.Session(SID).claim_socket == paths.claim_socket(SID)


def test_under_a_long_runtime_root_a_claude_session_sends_through_its_claim_socket(
        long_root, rig):
    """The wrapper's session: its claim socket takes the short path, the adapter serves
    it, and a send, `caller_identity` and the listener's roster memory all find it."""
    s = session.Session(SID, name="planner", scope="proj-a", project="proj-a")
    s.write()
    assert len(os.fsencode(str(s.dir / "claim.sock"))) >= core_paths.SOCKET_MAX
    assert is_short(s.claim_socket, long_root)
    env = s.env()
    shim = rig.shim(name="planner", scope="proj-a", claim_key=s.id,
                    claim_socket=s.claim_socket, listen_claims=True,
                    claim_roots=[root_of(os.getpid(), TREE)])
    assert s.claim_socket.is_socket()
    assert claimhold.socket_from_env(env) == s.claim_socket
    assert api.caller_identity(env) == "planner@proj-a"
    assert listener.memory_path(env) == s.dir / listener.MEMORY
    code, out = rig.cli(["send", "--posture", "none", "main@proj-a", "from a long root"],
                        env={CLAIM_SOCKET_ENV: env[CLAIM_SOCKET_ENV]})
    assert code == 0, out
    shim.stop()
    assert wait_for(lambda: not os.path.lexists(s.claim_socket))


def test_a_claim_server_under_a_long_runtime_root_takes_the_short_path(long_root, rig):
    srv = ClaimServer("relay", "proj-a", kind="codex").start()
    try:
        srv.set_bound(tier="chosen", token="relay-token")
        assert is_short(srv.socket_path, long_root)
        assert claimhold.call(srv.socket_path, "claim")["token"] == "relay-token"
        assert api.caller_identity(srv.env()) == "relay@proj-a"
    finally:
        srv.stop()


def test_a_short_path_no_claim_file_of_this_comms_records_is_refused(long_root):
    """Another program's socket in the core's short directory is never asked."""
    stray = paths.short_socket_dir() / "0123456789abcdef.sock"
    with socket.socket(socket.AF_UNIX) as s:
        s.bind(str(stray))
        env = {CLAIM_SOCKET_ENV: str(stray)}
        with pytest.raises(paths.OutsideRuntime) as caught:
            claimhold.socket_from_env(env)
        assert CLAIM_SOCKET_ENV in str(caught.value)
        assert api.caller_identity(env) is None
        assert listener.memory_path(env) is None


def test_a_claim_socket_left_by_a_holder_that_died_is_removed_and_a_live_one_kept(
        long_root, rig):
    left = session.Session(SID).claim_socket
    with socket.socket(socket.AF_UNIX) as s:
        s.bind(str(left))
    Path(claimhold.ipc_lock_path(str(left))).touch()
    claimhold.remove_socket(left)
    assert not os.path.lexists(left)
    assert not os.path.lexists(claimhold.ipc_lock_path(str(left)))

    srv = ClaimServer("relay", "proj-a", kind="codex").start()
    try:
        claimhold.remove_socket(srv.socket_path)
        assert srv.socket_path.is_socket()
        assert claimhold.call(srv.socket_path, "state")["ok"] is not False
    finally:
        srv.stop()

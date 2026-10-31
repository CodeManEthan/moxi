"""The two reads a launcher makes: `api.caller_identity` (who the calling process's own
session is, proven by its claim holder) and `api.session_of` (the session behind a
bound name)."""

from __future__ import annotations

import os
import subprocess
import sys
import uuid

import pytest

from moxi_comms import api, reserved, secrets
from moxi_comms.claimhold import CLAIM_SOCKET_ENV, ClaimServer, call
from moxi_comms.client import DaemonOffline
from moxi_comms.paths import runtime_root


def _sender(name):
    from conftest import _stamp_of

    return _stamp_of({"MOXI_COMMS_PROJECT": "proj-a", "MOXI_COMMS_NAME": name})


# --------------------------------------------------------------------------- caller_identity

def test_a_process_inside_a_bound_session_gets_its_qualified_name(rig, monkeypatch):
    rig.shim(name="planner")
    with rig.session_holding(_sender("planner")) as path:
        token = call(path, "claim").get("token")
        got = api.caller_identity({CLAIM_SOCKET_ENV: str(path)})
        monkeypatch.setenv(CLAIM_SOCKET_ENV, str(path))
        assert api.caller_identity() == got
    assert got == "planner"
    assert token is None or token not in got


def test_that_sessions_env_from_a_process_outside_it_proves_nothing(rig):
    rig.shim(name="planner")
    with rig.session_holding(_sender("planner")) as path:
        env = dict(os.environ, **{CLAIM_SOCKET_ENV: str(path)})
        code = ("import os\nfrom moxi_comms import api\n"
                "print(api.caller_identity(dict(os.environ)))\n")
        done = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True,
                              text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "None"


def test_no_socket_named_is_none(env):
    assert api.caller_identity({}) is None


def test_a_socket_outside_the_runtime_directory_is_none(env, tmp_path):
    assert api.caller_identity({CLAIM_SOCKET_ENV: str(tmp_path / "claim.sock")}) is None


def test_a_dead_adapter_is_none(env):
    runtime_root().mkdir(parents=True, exist_ok=True)
    gone = runtime_root() / f"claim-{uuid.uuid4().hex[:8]}.sock"
    assert api.caller_identity({CLAIM_SOCKET_ENV: str(gone)}) is None


def test_a_holder_not_bound_or_refused_is_none(rig):
    server = ClaimServer("helper", "global", kind="claude-shim", global_=True).start()
    env = {CLAIM_SOCKET_ENV: str(server.socket_path)}
    try:
        server.set_bound("chosen", token="t0k3n")
        assert api.caller_identity(env) == "helper"
        server.set_offline()
        assert api.caller_identity(env) is None
        server.set_waiting("helper is refused: the daemon said so")
        assert api.caller_identity(env) is None
    finally:
        server.stop()


# --------------------------------------------------------------------------- session_of

def test_session_of_a_bound_name_is_its_session_id(rig):
    sid = str(uuid.uuid4())
    rig.shim(name="planner", session_id=sid)
    assert api.session_of("planner") == sid


def test_session_of_an_unbound_or_reserved_name_is_none(rig):
    reserved.reconcile(declared={"launcher": ["console"]}, chain=secrets.from_config())
    assert api.session_of("nobody") is None
    assert api.session_of("console") is None


def test_session_of_a_binding_with_no_session_yet_is_none(rig):
    rig.shim(name="planner", session_id=None)
    assert api.session_of("planner") is None


def test_session_of_with_the_daemon_down_raises_offline(rig):
    rig.daemon.stop()
    with pytest.raises(DaemonOffline):
        api.session_of("planner")

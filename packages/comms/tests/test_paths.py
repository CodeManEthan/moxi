"""comms' locations: its own five directories under Moxi's roots, and the runtime refusal.

Root resolution itself is the core's (`moxi.paths`); here we pin the comms subpaths and
the guard on a socket path handed in from the environment.
"""

import os
import sys
from pathlib import Path

import pytest

from moxi_comms import paths


def test_comms_locations(monkeypatch, tmp_path):
    monkeypatch.setenv("MOXI_COMMS_HOME", str(tmp_path))
    monkeypatch.setenv("MOXI_COMMS_DEVICE", "testbox")
    for var in ("MOXI_COMMS_DATA_HOME", "MOXI_COMMS_STATE_HOME", "MOXI_COMMS_RUNTIME_DIR",
                "MOXI_COMMS_CONFIG_HOME", "MOXI_COMMS_CACHE_HOME"):
        monkeypatch.delenv(var, raising=False)
    p = paths
    # comms' one store: STATE and per box, because DATA is the sync surface and a live
    # DB is never continuous-file-synced
    assert p.comms_db() == tmp_path / "state" / "comms" / "comms.db"
    # comms 0.2's three files, which the move into comms.db reads
    assert p.directory_db() == tmp_path / "data" / "comms" / "directory.db"
    assert p.bindings_db() == tmp_path / "data" / "comms" / "bindings.db"
    assert p.record_db() == tmp_path / "state" / "comms" / "record.db"
    # Cards are DATA and device-agnostic -- who someone IS does not depend on which box
    # is asking.
    assert p.cards_dir() == tmp_path / "data" / "comms" / "cards"
    assert p.cursors_dir() == tmp_path / "state" / "comms" / "cursors"
    assert p.tokens_dir() == tmp_path / "state" / "comms" / "tokens"
    # comms writes only under its own five directories, so its logs are in STATE/comms
    assert p.log_dir() == tmp_path / "state" / "comms" / "logs"
    # and its sockets in RUNTIME/comms
    if sys.platform == "win32":
        # There it is a pipe, named by the core.
        from moxi import ipc

        assert p.daemon_socket() == Path(ipc.address("comms", "daemon"))
    else:
        assert p.daemon_socket() == tmp_path / "runtime" / "comms" / "daemon.sock"
    # comms 0.2's socket, which the move only connects to
    assert p.old_daemon_socket() == tmp_path / "runtime" / "comms" / "moxi-comms-daemon.sock"
    assert not hasattr(p, "codex_socket"), "turns' host owns the app-server's socket"
    assert p.opencode_runtime_dir() == tmp_path / "runtime" / "comms" / "opencode"
    assert p.config_dir() == tmp_path / "config" / "comms"


def test_every_location_reads_a_given_env(tmp_path):
    env = {"MOXI_COMMS_HOME": str(tmp_path), "MOXI_COMMS_DEVICE": "box2"}
    assert paths.comms_db(env) == tmp_path / "state" / "comms" / "comms.db"
    # The core's address under that env: a pipe on Windows, else its socket path (the
    # short hashed one when the runtime folder's path is too long).
    from moxi import ipc

    assert paths.daemon_socket(env) == Path(ipc.address("comms", "daemon", env))
    assert paths.daemon_socket(env) != paths.daemon_socket()


def test_a_socket_under_the_runtime_dir_passes():
    sock = paths.runtime_dir() / "comms-claimsrv-x.sock"
    assert paths.refuse_outside_runtime(sock, "MOXI_COMMS_CLAIM_SOCKET") == sock
    assert paths.refuse_outside_runtime(str(paths.daemon_socket()),
                                        "MOXI_COMMS_SOCKET") == paths.daemon_socket()


@pytest.mark.parametrize("variable", ["MOXI_COMMS_CLAIM_SOCKET", "MOXI_COMMS_SOCKET"])
def test_a_socket_outside_the_runtime_dir_is_refused(tmp_path, variable):
    """A session whose stamp names a socket under another program's runtime root: a
    send inside it must not claim that identity against comms' daemon."""
    other = tmp_path / "other-program" / "comms-daemon.sock"
    with pytest.raises(paths.OutsideRuntime) as caught:
        paths.refuse_outside_runtime(other, variable)
    text = str(caught.value)
    assert variable in text and str(paths.runtime_dir()) in text and str(other) in text


def test_the_runtime_dir_itself_and_its_root_are_refused():
    """Only a path inside RUNTIME/comms is comms' own: not the directory itself, and not
    another module's directory under the shared root."""
    for path in (paths.runtime_dir(), paths.LAYOUT.runtime_root() / "turns" / "x.sock",
                 paths.runtime_dir() / ".." / "turns" / "x.sock"):
        with pytest.raises(paths.OutsideRuntime):
            paths.refuse_outside_runtime(path, "MOXI_COMMS_CLAIM_SOCKET")


@pytest.mark.usefixtures("needs_symlinks")
def test_a_link_inside_the_runtime_dir_that_leaves_it_is_refused(tmp_path):
    outside = tmp_path / "elsewhere.sock"
    outside.touch()
    runtime = paths.runtime_dir()
    runtime.mkdir(parents=True, exist_ok=True)
    link = runtime / "looks-local.sock"
    os.symlink(outside, link)
    with pytest.raises(paths.OutsideRuntime):
        paths.refuse_outside_runtime(link, "MOXI_COMMS_CLAIM_SOCKET")


@pytest.mark.skipif(sys.platform != "win32",
                    reason="only Windows names comms' sockets as pipes")
def test_a_pipe_not_comms_own_is_refused_by_its_name_and_never_opened(monkeypatch,
                                                                       tmp_path):
    """A stamp naming a pipe of another runtime folder, or any other pipe, is refused as
    outside; reading such a name as a path would open the pipe (or fail opening it)."""
    from moxi import ipc

    own = paths.claim_socket("probe")
    with monkeypatch.context() as moved:
        moved.setenv("MOXI_COMMS_RUNTIME_DIR", str(tmp_path / "other-runtime"))
        another = ipc.address("comms", "claim-probe")
    assert another != str(own)

    real = os.path.realpath

    def opened(path, *args, **kwargs):
        if str(path).replace("/", "\\").startswith("\\\\.\\"):
            raise AssertionError(f"resolved the pipe {path}")
        return real(path, *args, **kwargs)

    monkeypatch.setattr(os.path, "realpath", opened)
    assert paths.refuse_outside_runtime(own, "MOXI_COMMS_CLAIM_SOCKET") == own
    for stamped in (another, "\\\\.\\pipe\\elsewhere", "//./pipe/elsewhere"):
        with pytest.raises(paths.OutsideRuntime) as caught:
            paths.refuse_outside_runtime(stamped, "MOXI_COMMS_CLAIM_SOCKET")
        assert "MOXI_COMMS_CLAIM_SOCKET" in str(caught.value)

"""comms' mint gate for the connector's sign-in codes, as a unit: `peer` over a fake
process table and claim records, `shell` over environments. And `[connector]`'s
settings, read and refused."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from moxi_comms import config
from moxi_comms.claimhold import CLAIM_SOCKET_ENV
from moxi_comms.connector import gate
from moxi_comms.membership import ProcView

from conftest import write_config


class Table(ProcView):
    """A process table: pid -> (parent, start). A pid missing from it can't be read."""

    def __init__(self, rows):
        super().__init__(root="")
        self.rows = rows

    def stat(self, pid):
        return self.rows.get(int(pid))


#: 1 is init; 100 a terminal; 200 an agent session's harness, 201 a shell under it,
#: 202 a process under that; 300 a process whose parent can't be read.
TABLE = Table({1: (0, "s1"), 100: (1, "s100"), 101: (100, "s101"), 200: (1, "s200"),
               201: (200, "s201"), 202: (201, "s202"), 300: (299, "s300")})
SESSION = [("claude-planner", {"roots": [{"pid": 200, "start": "s200", "reach": "tree"}]})]


def peer(pid):
    return SimpleNamespace(pid=pid, uid=1000, proc=SimpleNamespace(pid=pid))


def check(pid, *, records=SESSION, alive=lambda ident: True):
    return gate.peer(peer(pid), view=TABLE, records=records, alive=alive)


def test_a_peer_outside_every_session_mints():
    assert check(101) is None
    assert check(100, records=[]) is None


@pytest.mark.parametrize("pid", [200, 201, 202])
def test_a_peer_inside_a_live_session_is_refused(pid):
    assert check(pid) == gate.PEER_REFUSED


def test_a_peer_the_walk_cant_place_is_refused_never_read_as_outside():
    assert check(300) == gate.PEER_REFUSED            # its parent can't be read
    assert check(101, alive=lambda ident: False) == gate.PEER_REFUSED   # gone, or reused
    assert gate.peer(SimpleNamespace(pid=None, uid=1000, proc=None)) == gate.PEER_REFUSED
    assert gate.peer(SimpleNamespace(pid=101, uid=1000, proc=None)) == gate.PEER_REFUSED


def test_a_walk_that_fails_refuses():
    def broken(ident):
        raise OSError("no table")
    assert check(101, alive=broken) == gate.PEER_REFUSED


def test_a_self_root_admits_only_its_own_process():
    records = [("srv", {"roots": [{"pid": 100, "start": "s100", "reach": "self"}]})]
    assert check(100, records=records) == gate.PEER_REFUSED
    assert check(101, records=records) is None


def plain(env):
    return env


def test_the_operators_own_shell_passes():
    assert gate.shell({"MOXI_COMMS_OPERATOR": "900001"}, overlay=plain) is None


def test_a_shell_with_a_session_name_is_refused_naming_it():
    got = gate.shell({"MOXI_COMMS_OPERATOR": "900001", "MOXI_COMMS_NAME": "planner"},
                     overlay=plain)
    assert got == gate.SHELL_IN_SESSION.format(name="planner")


def test_a_shell_with_a_claim_socket_is_refused_whatever_the_claim_answers():
    env = {"MOXI_COMMS_OPERATOR": "900001", CLAIM_SOCKET_ENV: "/nowhere/claim.sock"}
    # The claim socket answers nothing: the overlay drops the stamps.
    got = gate.shell(env, overlay=lambda e: {k: v for k, v in e.items()
                                             if k != "MOXI_COMMS_NAME"})
    assert got.startswith("This shell is inside the agent session")
    # It answers a claim: the session's name is the claim's.
    got = gate.shell(env, overlay=lambda e: {**e, "MOXI_COMMS_NAME": "reviewer"})
    assert got == gate.SHELL_IN_SESSION.format(name="reviewer")


def test_a_shell_without_the_operator_stamp_is_refused_naming_the_stamp():
    assert gate.shell({}, overlay=plain) == gate.SHELL_NO_OPERATOR
    assert gate.shell({"MOXI_COMMS_OPERATOR": "  "}, overlay=plain) == gate.SHELL_NO_OPERATOR
    assert "MOXI_COMMS_OPERATOR is not set" in gate.SHELL_NO_OPERATOR


def test_the_default_overlay_reads_a_shell_with_no_claim_socket_unchanged():
    assert gate.shell({"MOXI_COMMS_OPERATOR": "900001"}) is None


# --------------------------------------------------------------------------- settings

def test_connector_settings_default():
    got = config.load().connector
    assert (got.identity, got.wait_default, got.wait_max) == ("claude-app", 45, 90)


def test_connector_settings_read_from_the_file():
    write_config(None, "[connector]\nidentity = \"phone-app\"\nwait_default_s = 20\n"
                       "wait_max_s = 60\n")
    got = config.load().connector
    assert (got.identity, got.wait_default, got.wait_max) == ("phone-app", 20, 60)


@pytest.mark.parametrize("text, key", [
    ('[connector]\nidentity = "operator"\n', "connector.identity"),
    ('[connector]\nidentity = "app@proj-a"\n', "connector.identity"),
    ('[connector]\nidentity = "agent-4"\n', "connector.identity"),
    ('[connector]\nwait_max_s = 100\n', "connector.wait_max_s"),
    ('[connector]\nwait_max_s = 0\n', "connector.wait_max_s"),
    ('[connector]\nwait_default_s = 91\n', "connector.wait_default_s"),
    ('[connector]\nwait_default_s = 0\n', "connector.wait_default_s"),
    ('[provision]\noperator = "boss"\n[connector]\nidentity = "boss"\n',
     "connector.identity"),
])
def test_connector_settings_refused(text, key):
    with pytest.raises(config.ConfigError) as e:
        config.check_text(text)
    assert e.value.key == key

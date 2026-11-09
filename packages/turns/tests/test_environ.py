"""`environ` and `Session.environ`: the named variables of the session's own environment,
read from its endpoint, asked of the Claude endpoint, the Codex host and the OpenCode host
started from one launch environment."""

from __future__ import annotations

import itertools
import os
import sys

import pytest

from claude_runner import FakeRunner, pipe_pair
from conftest import end_endpoint
from moxi import ipc
from moxi_turns import opencode, protocol
from moxi_turns.claude_endpoint import ClaudeHarness
from moxi_turns.client import Session, TurnsError
from moxi_turns.codex import CodexHarness
from moxi_turns.endpoint import Endpoint
from moxi_turns.listeners import Registry
from moxi_turns.results import ENVIRON_MAX_NAMES, ERROR_CAPABILITY
from test_codex_host import FakeAppServer, FakeClient
from test_opencode_host import World

pytestmark = pytest.mark.skipif(sys.platform == "win32",
                                reason="moxi.ipc's named pipe client comes later")

#: The launch's own variables, stamped into the environment every host starts from.
LAUNCH = {"MOXI_T_ENV_ONE": "one", "MOXI_T_ENV_EMPTY": "", "MOXI_T_ENV_SPACE": "a b=c"}
ASKED = [*LAUNCH, "MOXI_T_ENV_UNSET"]
WANT = {**LAUNCH, "MOXI_T_ENV_UNSET": None}


@pytest.fixture
def launch_env(turns_env, monkeypatch):
    for name, value in LAUNCH.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("MOXI_T_ENV_UNSET", raising=False)
    return turns_env


@pytest.fixture
def hosts(launch_env, tmp_path):
    """The three endpoints, each as its launch starts it, with a `Session` on each."""
    stops = []

    # Claude: the endpoint is the harness's child and inherits its environment.
    r, w = pipe_pair()
    claude = ClaudeHarness(w, pid=os.getpid())
    ep = Endpoint(claude, registry=Registry())
    ep.prepare()
    claude.serve(str(ep.dir / protocol.MOD_SOCK))
    runner = FakeRunner(r)
    claude.first_line()
    runner.wait_first()
    runner.session("s-claude")
    ep.open()
    stops.append(lambda: (end_endpoint(ep), w.close()))
    sessions = {"claude": Session(ep.address, ep.session(), endpoint_dir=ep.dir)}

    # Codex: the host is the launch's own process.
    made = []

    def clients(**kwargs):
        client = FakeClient(codex, **kwargs)
        made.append(client)
        return client

    codex = CodexHarness(launch_env, window=False, ping=["/env/bin/moxi-turns-ping"],
                         client_factory=clients, app_server_factory=FakeAppServer)
    cep = Endpoint(codex, env=codex.env).open()
    codex.start()
    stops.append(lambda: end_endpoint(cep))
    sessions["codex"] = Session(cep.address, cep.session(), endpoint_dir=cep.dir)

    # OpenCode: the same, on a fake server.
    world = World(tmp_path)
    oc = opencode.OpenCodeHarness(tmp_path, binary="/x/opencode",
                                  server_factory=world.factory,
                                  backoff_factory=lambda: itertools.repeat(0.05))
    sessions["opencode"] = opencode._run(oc)
    stops.append(lambda: end_endpoint(oc.endpoint))

    yield sessions
    for s in sessions.values():
        s.close()
    for stop in stops:
        stop()


def test_the_three_hosts_answer_the_same_from_one_launch_env(hosts):
    answers = {}
    for harness, s in hosts.items():
        assert s.harness == harness
        assert "environ" in s.capabilities
        answers[harness] = s.environ(ASKED)
    assert answers == {"claude": WANT, "codex": WANT, "opencode": WANT}
    names = [f"MOXI_T_ENV_{n}" for n in range(ENVIRON_MAX_NAMES)]
    for s in hosts.values():
        assert s.environ(names) == dict.fromkeys(names)
        assert s.environ([]) == {}


@pytest.mark.parametrize("names", [
    [f"MOXI_T_ENV_{n}" for n in range(ENVIRON_MAX_NAMES + 1)],
    ["MOXI_T_ENV_ONE", "NOT-A-NAME"],
    ["1ST"],
    [""],
    ["MOXI_T_ENV_ONE", 5],
])
def test_too_many_names_or_a_bad_name_is_invalid_params_on_every_host(hosts, names):
    for s in hosts.values():
        with pytest.raises(TurnsError) as caught:
            s.environ(names)
        assert caught.value.code == -32602


def test_one_string_is_not_a_list_of_names(hosts):
    with pytest.raises(TypeError):
        hosts["claude"].environ("HOME")


def test_the_host_answers_its_own_copy_not_a_later_change(hosts, monkeypatch):
    monkeypatch.setenv("MOXI_T_ENV_ONE", "changed after the start")
    for s in hosts.values():
        assert s.environ(["MOXI_T_ENV_ONE"]) == {"MOXI_T_ENV_ONE": "one"}


def test_a_launch_env_given_to_a_host_is_what_it_answers(launch_env, tmp_path):
    world = World(tmp_path)
    oc = opencode.OpenCodeHarness(tmp_path, binary="/x/opencode",
                                  server_factory=world.factory,
                                  backoff_factory=lambda: itertools.repeat(0.05),
                                  env={"MOXI_T_ENV_ONE": "given"})
    s = opencode._run(oc)
    try:
        assert s.environ(["MOXI_T_ENV_ONE", "OPENCODE_SERVER_PASSWORD"]) == {
            "MOXI_T_ENV_ONE": "given", "OPENCODE_SERVER_PASSWORD": None}
    finally:
        s.close()
        end_endpoint(oc.endpoint)


def test_a_session_without_the_capability_gets_the_capability_error(make_endpoint):
    from fake_harness import FakeHarness

    ep, _ = make_endpoint(FakeHarness(can=("deliver",)))
    s = Session(ep.address, ep.session(), endpoint_dir=ep.dir)
    try:
        assert "environ" not in s.capabilities
        with pytest.raises(TurnsError) as caught:
            s.environ(["HOME"])
        assert caught.value.code == ERROR_CAPABILITY
        assert caught.value.data == {"capability": "environ"}
    finally:
        s.close()
    client = ipc.connect(ipc.address("turns", ep.dir.name), timeout_s=5)
    try:
        client.hello((2, 2))
        with pytest.raises(ipc.RpcError) as caught:
            client.call("environ", {"names": ["HOME"]}, timeout_s=5)
        assert caught.value.code == ERROR_CAPABILITY
    finally:
        client.close()

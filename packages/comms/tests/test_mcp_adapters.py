"""The adapters and comms' tools: the claim on every bind, the tool token registered after
every bind (the first before the ready word), the Codex adapter's `mcp.reload`, the
binding's connection closed when its install fails, and the Codex network default.

The daemon is the rig's (real sockets, a real registry); the Codex session is
`fake_turns.FakeSession`, whose `mcp_reload` records the timeout each call carried.
"""

from __future__ import annotations

import itertools
import os
from types import SimpleNamespace

import pytest
from conftest import clock_reads_warm, wait_until

from fake_app_server import FakeAppServer
from fake_turns import CODEX, CODEX_RELOAD, FakeSession, fake_find
from moxi_comms import codex_threads, wire
from moxi_comms.adapters import binding, claude_adapter, codex, codex_roster, codex_wrapper
from moxi_comms.adapters.binding import ToolToken
from moxi_comms.adapters.codex import CodexAdapter
from moxi_comms.adapters.codex_roster import CodexAgent
from moxi_comms.adapters.codex_wrapper import build_parser, plan
from moxi_comms.adapters.word import BOUND
from moxi_comms.client import DaemonError, DaemonOffline
from moxi_comms.config import ConfigError
from moxi_comms.mcp import tokens
from moxi_comms.mcp.additions import SHELL_REACH_ENV, SHELL_REACH_OFF, TOKEN_ENV, URL_ENV

PLANNER = "planner@proj-a"
REVIEWER = "reviewer@proj-a"
URL = "http://127.0.0.1:8450/mcp"
VALUE = "a-test-token-value-of-no-meaning-0000000000000"


def row_of(rig, value=VALUE):
    """The registry's active row for `value`'s hash, or None."""
    want = tokens.digest(value)
    return next((r for r in rig.daemon.mcp_tokens.active() if r.token_hash == want), None)


def args(*argv):
    return build_parser().parse_args(list(argv))


@pytest.fixture(autouse=True)
def _gate_reads_warm(monkeypatch):
    clock_reads_warm(monkeypatch, "codex")


@pytest.fixture
def launch(rig, tmp_path):
    """A Codex adapter for `planner@proj-a` over a `FakeSession`, unstarted."""
    made: list = []

    def build(*, session=None, token=VALUE, autoreconnect=False, start=True, **kwargs):
        agent = CodexAgent(qualified=PLANNER, name="planner", scope="proj-a",
                           cwd=str(tmp_path), thread_file=codex_threads.path(PLANNER))
        session = session or FakeSession(capabilities=CODEX_RELOAD)
        adapter = CodexAdapter(agent, find=fake_find(session), harness_pid=4242,
                               autoreconnect=autoreconnect, clock=rig.clock,
                               status_sink=lambda text: None, exit_hook=lambda: None,
                               **kwargs)
        adapter.fake = session
        if token is not None:
            adapter.tool_token = ToolToken(tokens.digest(token))
        made.append(adapter)
        if start:
            adapter.start()
        return adapter

    yield build
    for adapter in made:
        adapter.stop()
        adapter.fake.gone("the test ended")


# --------------------------------------------------------------------------- the token

def test_the_token_is_read_from_a_launch_carrying_the_pair_and_only_its_hash_stays():
    env = {TOKEN_ENV: VALUE, URL_ENV: URL}
    token = ToolToken.from_environ(env)
    assert token.hash == tokens.digest(VALUE)
    assert TOKEN_ENV not in env, "the value leaves the environment"
    assert VALUE not in repr(token)


@pytest.mark.parametrize("env", [{}, {TOKEN_ENV: VALUE}, {URL_ENV: URL},
                                 {TOKEN_ENV: "", URL_ENV: URL}])
def test_a_launch_without_the_pair_has_no_token(env):
    before = dict(env)
    assert ToolToken.from_environ(env) is None
    assert env == before


class _Client:
    """A connection stand-in: its hello's capabilities, and what `mcp_token` does."""

    def __init__(self, *answers, capabilities=("mcp",)):
        self.capabilities = frozenset(capabilities)
        self.answers = list(answers)
        self.calls = 0

    def mcp_token(self, token_hash):
        self.calls += 1
        answer = self.answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer


def test_a_daemon_without_the_mcp_capability_is_not_asked():
    client = _Client({"served": True}, capabilities=())
    assert ToolToken(tokens.digest(VALUE)).register(client) is None
    assert client.calls == 0


@pytest.mark.parametrize("code", [wire.MCP_TOKEN_TAKEN, wire.MCP_TOKEN_REVOKED])
def test_a_taken_or_revoked_token_is_final_for_the_launch(code, caplog):
    token = ToolToken(tokens.digest(VALUE))
    client = _Client(DaemonError("refused", code=code), {"served": True})
    with caplog.at_level("WARNING"):
        assert token.register(client) is None
        assert token.register(client) is None
    assert client.calls == 1, "never asked again"
    assert token.refused == wire.CODE_NAMES[code]
    assert len([r for r in caplog.records if "tools are off" in r.getMessage()]) == 1
    assert VALUE not in caplog.text


def test_a_try_again_answer_is_tried_again_and_a_drop_waits_for_the_next_bind(
        monkeypatch):
    monkeypatch.setattr(ToolToken, "PAUSE", 0)
    token = ToolToken(tokens.digest(VALUE))
    client = _Client(DaemonError("busy", code=wire.STORE_BUSY), {"served": True})
    assert token.register(client) is True and client.calls == 2
    gone = _Client(DaemonOffline("closed"))
    assert token.register(gone) is None and token.refused is None


# --------------------------------------------------------------------------- the claim

def test_a_shim_names_its_claim_on_every_bind(rig):
    s = rig.shim(name="reviewer", scope="proj-a")
    assert rig.daemon.bindings[REVIEWER].claim_key == s.claims.key
    s._client.close()
    assert wait_until(lambda: s.offline and REVIEWER not in rig.daemon.bindings)
    assert s.connect()
    assert rig.daemon.bindings[REVIEWER].claim_key == s.claims.key


def test_a_codex_adapter_names_its_claim_on_every_bind(rig, launch):
    adapter = launch()
    assert rig.daemon.bindings[PLANNER].claim_key == adapter.claims.key
    adapter.reregister()
    assert rig.daemon.bindings[PLANNER].claim_key == adapter.claims.key


# --------------------------------------------------------------------------- registering

def test_the_claude_adapter_registers_before_it_says_bound(rig, monkeypatch):
    """`serve` reads the token from the process's environment, and the first
    registration is in the daemon's table when the wrapper is told bound."""
    rig.daemon.mcp_served = True
    monkeypatch.setenv(TOKEN_ENV, VALUE)
    monkeypatch.setenv(URL_ENV, URL)
    shim = rig.shim(name="reviewer", scope="proj-a", start=False)
    r, w = os.pipe()
    seen = []

    def say(code, reason=""):
        if code == BOUND:
            seen.append(row_of(rig))
            os.close(w)                       # `claude` "ends": serve returns

    turns = SimpleNamespace(find=lambda pid: None, TurnsError=RuntimeError)
    try:
        assert claude_adapter.serve(shim, os.getpid(), r, say=say, turns=turns) == 0
    finally:
        os.close(r)
    (row,) = seen
    assert row is not None and row.bound and row.identity == REVIEWER
    assert row.claim_key == shim.claims.key
    assert TOKEN_ENV not in os.environ, "the adapter keeps only the hash"


def test_the_codex_adapter_registers_before_it_says_bound(rig, launch):
    seen = []
    adapter = launch(start=False, on_bound=lambda: seen.append(row_of(rig)))
    adapter.start()
    (row,) = seen
    assert row is not None and row.bound and row.identity == PLANNER
    assert row.claim_key == adapter.claims.key and row.adapter == "codex"


def test_a_launch_without_a_token_registers_nothing(rig, launch):
    launch(token=None)
    assert rig.daemon.mcp_tokens.active() == []


def test_a_rebind_registers_the_same_token_for_the_new_binding(rig, launch):
    adapter = launch()
    first = row_of(rig).binding_id
    adapter.reregister()
    assert wait_until(lambda: row_of(rig).binding_id not in (None, first))
    assert len(rig.daemon.mcp_tokens.active()) == 1


def test_a_shim_started_while_the_daemon_is_down_registers_at_its_first_bind(rig):
    """A launch that serves unbound while the daemon is away registers once its
    supervisor binds."""
    from moxi_comms.daemon import Daemon

    rig.daemon.stop()
    s = rig.shim(name="reviewer", scope="proj-a", start=False, autoreconnect=True,
                 backoff=itertools.repeat(0.05))
    s.tool_token = ToolToken(tokens.digest(VALUE))
    s.start(tolerate_offline=True)
    assert s.offline
    daemon = Daemon(rig.record, clock=rig.clock, start_trim_thread=False)
    rig.daemon = daemon
    daemon.start()
    assert wait_until(lambda: not s.offline, 10)
    row = wait_until(lambda: row_of(rig))
    assert row.identity == REVIEWER and row.bound


# --------------------------------------------------------------------------- mcp.reload

def test_a_served_registration_reloads_the_hosts_servers_once_the_session_is_found(
        rig, launch):
    rig.daemon.mcp_served = True
    order = []
    session = FakeSession(capabilities=CODEX_RELOAD)

    def find(pid, timeout):
        order.append("find")
        return session

    adapter = launch(start=False, session=session)
    adapter._find = find
    adapter.start()
    assert wait_until(lambda: session.reload_calls)
    assert order == ["find"]
    (timeout,) = session.reload_calls
    assert timeout == codex.RELOAD_WAIT and timeout > 20, \
        "above the 20 s a queued settle can take"
    assert wait_until(lambda: adapter.reloads) == [session.reload_answer]


def test_each_served_rebind_reloads_again(rig, launch):
    rig.daemon.mcp_served = True
    adapter = launch()
    assert wait_until(lambda: len(adapter.fake.reload_calls) == 1)
    adapter.reregister()
    assert wait_until(lambda: len(adapter.fake.reload_calls) == 2)


def test_a_rebind_after_a_daemon_restart_registers_and_reloads(rig, launch):
    from moxi_comms.daemon import Daemon

    rig.daemon.mcp_served = True
    adapter = launch(autoreconnect=True, backoff=itertools.repeat(0.05))
    assert wait_until(lambda: len(adapter.fake.reload_calls) == 1)
    rig.daemon.stop()
    assert wait_until(lambda: adapter.offline)
    # Serving the tools from its start, as a restarted daemon settles its endpoint
    # before it answers any bind.
    daemon = Daemon(rig.record, clock=rig.clock, start_trim_thread=False,
                    serve_tools=True, mcp_port=0)
    rig.daemon = daemon
    daemon.start()
    assert wait_until(lambda: not adapter.offline, 10)
    assert wait_until(lambda: row_of(rig) is not None and row_of(rig).bound)
    assert wait_until(lambda: len(adapter.fake.reload_calls) == 2)


def test_no_reload_when_the_daemon_serves_no_tools(rig, launch):
    rig.daemon.mcp_served = False
    adapter = launch()
    adapter.reregister()
    assert row_of(rig) is not None, "registered all the same"
    assert adapter.reloads == [] and adapter.fake.reload_calls == []


def test_no_reload_when_the_hosts_hello_lacks_mcp_token(rig, launch, caplog):
    rig.daemon.mcp_served = True
    with caplog.at_level("INFO", logger="comms.codex"):
        adapter = launch(session=FakeSession(capabilities=CODEX))
        assert wait_until(lambda: "offers no mcp.reload" in caplog.text)
    assert adapter.fake.reload_calls == [] and adapter.reloads == []


def test_a_reload_that_fails_is_logged_and_the_adapter_carries_on(rig, launch, caplog):
    rig.daemon.mcp_served = True
    session = FakeSession(capabilities=CODEX_RELOAD)
    session.reload_answer = TimeoutError("no answer")
    with caplog.at_level("WARNING", logger="comms.codex"):
        adapter = launch(session=session)
        assert wait_until(lambda: "mcp.reload for planner@proj-a failed" in caplog.text)
    assert adapter.reloads == [] and adapter.session is session


# --------------------------------------------------------------------------- the leak

def test_a_bind_whose_claim_write_fails_closes_its_connection(rig, monkeypatch):
    """The install failed after the daemon bound the name: the connection is closed, so
    the name is released and the next bind takes it."""
    s = rig.shim(name="reviewer", scope="proj-a", start=False)
    real = s.claims.bound

    def failing(resp):
        raise OSError("disk full")

    monkeypatch.setattr(s.claims, "bound", failing)
    with pytest.raises(OSError):
        s._connect_once()
    orphan = s._client
    assert not orphan.connected
    assert wait_until(lambda: REVIEWER not in rig.daemon.bindings)
    assert s.offline
    monkeypatch.setattr(s.claims, "bound", real)
    assert s._connect_once()
    assert s._client is not orphan and REVIEWER in rig.daemon.bindings


def test_the_harness_bind_closes_its_connection_too(rig, launch, monkeypatch):
    adapter = launch(start=False)
    adapter._claim_start()

    def failing(resp):
        raise OSError("disk full")

    monkeypatch.setattr(adapter.claims, "bound", failing)
    with pytest.raises(OSError):
        adapter.connect_daemon()
    assert not adapter._daemon.connected
    assert wait_until(lambda: PLANNER not in rig.daemon.bindings)


# --------------------------------------------------------------------------- the network

def named(tmp_path, *more, url=None):
    return plan(args("--name", "coder", *more), cwd=str(tmp_path),
                endpoint=lambda: (url, "" if url else "comms' daemon isn't running"))


def network_of(launch):
    return (launch.options.get("config") or {}).get("sandbox_workspace_write", {}).get(
        "network_access")


def roster_launch(tmp_path, row: str, *, url=None):
    work = tmp_path / "work"
    work.mkdir(exist_ok=True)
    config = tmp_path / "roster.toml"
    config.write_text(f'[codex.agents."planner@proj-a"]\ncwd = "{work}"\n{row}')
    return plan(args("planner@proj-a", "--config", str(config)),
                endpoint=lambda: (url, "" if url else "off"))


def test_a_launch_carrying_the_tools_starts_with_the_network_off(tmp_path):
    launch = named(tmp_path, url=URL)
    assert network_of(launch) is False
    assert "sandbox_workspace_write.network_access=false" in launch.flags
    assert launch.tools_url == URL


def test_a_launch_without_the_tools_keeps_the_network_on(tmp_path):
    launch = named(tmp_path)
    assert network_of(launch) is True and launch.tools_url is None


def test_a_passed_option_turns_it_back_on(tmp_path):
    launch = named(tmp_path, "--option", "sandbox_workspace_write.network_access=true",
                   url=URL)
    assert network_of(launch) is True


def test_a_rows_own_value_wins_over_the_default(tmp_path):
    assert network_of(roster_launch(tmp_path, "network_access = true\n", url=URL)) is True
    assert network_of(roster_launch(tmp_path, "network_access = false\n")) is False


def test_a_row_naming_none_takes_the_launchs_default(tmp_path):
    assert network_of(roster_launch(tmp_path, "", url=URL)) is False
    assert network_of(roster_launch(tmp_path, "")) is True


def test_a_permissions_row_is_untouched(tmp_path):
    launch = roster_launch(tmp_path, 'permissions = "locked"\n', url=URL)
    assert "sandbox_workspace_write" not in (launch.options.get("config") or {})
    assert "network_access" not in " ".join(launch.flags)
    assert not launch.shell_reach_off


def test_the_defaults_table_still_refuses_network_access():
    with pytest.raises(ConfigError):
        codex_roster.from_table({"defaults": {"network_access": False}})


def test_the_roster_leaves_an_unnamed_value_to_the_launch(tmp_path):
    row = CodexAgent(qualified="p@x", name="p", scope="x", cwd=str(tmp_path),
                     thread_file=None)
    assert row.network_access is None
    assert "network_access" not in " ".join(codex_wrapper.row_flags(row))
    off = row.launch_options(network=False)["config"]["sandbox_workspace_write"]
    assert off["network_access"] is False
    row.network_access = True
    on = row.launch_options(network=False)["config"]["sandbox_workspace_write"]
    assert on["network_access"] is True


def test_comms_flags_add_the_default_only_where_nothing_names_it():
    flags = codex_wrapper.comms_flags({"sandbox": "workspace-write"}, network=False)
    assert "sandbox_workspace_write.network_access=false" in flags
    named_ = codex_wrapper.comms_flags(
        {"sandbox": "workspace-write",
         "config": {"sandbox_workspace_write": {"network_access": True}}}, network=False)
    assert not any("network_access" in f for f in named_)


@pytest.mark.parametrize(("more", "url"), [
    ((), URL), ((), None), (("--option", "sandbox_workspace_write.network_access=true"), URL),
    (("--sandbox", "read-only"), URL)])
def test_the_hosts_settings_check_passes_in_each_case(tmp_path, more, url):
    """turns' host compares each load answer with what it asked for: the fake
    app-server's policy for the resolved options passes it."""
    import moxi_turns

    launch = named(tmp_path, *more, url=url)
    answer = {"sandbox": FakeAppServer.sandbox_policy(launch.options), "cwd": str(tmp_path)}
    assert moxi_turns.codex.verify_options(launch.options, answer, str(tmp_path)) is None


@pytest.mark.parametrize(("more", "url", "off"), [
    (("--sandbox", "read-only"), None, True),
    (("--sandbox", "read-only"), URL, True),
    ((), URL, True),
    ((), None, False),
    (("--option", "sandbox_workspace_write.network_access=true"), URL, False),
    (("--sandbox", "danger-full-access"), URL, False),
    (("--permissions", "locked"), URL, False),
], ids=["read-only", "read-only-tools", "workspace-off", "workspace-on", "option-on",
        "full-access", "profile"])
def test_the_shell_reach_stamp(tmp_path, more, url, off):
    launch = named(tmp_path, *more, url=url)
    env = launch.env({SHELL_REACH_ENV: "inherited", "PATH": "/bin"})
    assert env.get(SHELL_REACH_ENV) == (SHELL_REACH_OFF if off else None)


def test_the_start_text_follows_the_stamp(tmp_path):
    from moxi_comms import listener

    env = named(tmp_path, url=URL).env({})
    env.update({TOKEN_ENV: VALUE, URL_ENV: URL})
    assert listener.tools_line(env) == listener.TOOLS_ONLY_LINE
    env = named(tmp_path, "--option", "sandbox_workspace_write.network_access=true",
                url=URL).env({})
    env.update({TOKEN_ENV: VALUE, URL_ENV: URL})
    assert listener.tools_line(env) == listener.TOOLS_LINE


def test_a_launch_with_no_tools_and_no_shell_reach_says_what_it_costs(tmp_path):
    assert "cannot send" in codex_wrapper.reach_note(
        roster_launch(tmp_path, "network_access = false\n"))
    assert codex_wrapper.reach_note(roster_launch(tmp_path, "network_access = false\n",
                                                  url=URL)) is None
    assert codex_wrapper.reach_note(named(tmp_path)) is None


def test_the_dry_run_resolves_the_network_from_the_daemons_answer(tmp_path, capsys,
                                                                   monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(codex_wrapper, "tools_endpoint", lambda *a, **k: (URL, ""))
    assert codex_wrapper.main(["--name", "coder", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "(network OFF," in out and "sends through comms' tools" in out
    assert "sandbox_workspace_write.network_access=false" in out
    monkeypatch.setattr(codex_wrapper, "tools_endpoint", lambda *a, **k: (None, "off"))
    assert codex_wrapper.main(["--name", "coder", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "(network on," in out and "sandbox_workspace_write.network_access=true" in out


def test_a_bad_flag_is_refused_before_the_daemon_is_asked(tmp_path, monkeypatch, capsys):
    asked = []
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(codex_wrapper, "tools_endpoint",
                        lambda *a, **k: asked.append(1) or (URL, ""))
    assert codex_wrapper.main(["--name", "a b", "--dry-run"]) != 0
    assert asked == []


def test_binding_names_the_token_class():
    assert "ToolToken" in binding.__all__

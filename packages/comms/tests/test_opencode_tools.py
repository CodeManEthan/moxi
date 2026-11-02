"""comms' tools in an OpenCode launch: the wrapper asks turns for the launch's additions
(`prepare_launch`) and merges them into the `OPENCODE_CONFIG_CONTENT` it builds, whole or
not at all.

A served launch mints the session's token and stamps it with the daemon's URL, turns
renders comms' server naming the variable, and the wrapper merges `mcp` and `permission`
one level deep beside its own launch config. Additions that set what the launch config
sets, or a call that fails, are skipped whole with a note. The token's value is in the
server's environment as its own variable, in no argv word or file, and in the config text
only when OpenCode does not substitute `{env:...}`.

The daemon is the rig's; the OpenCode server is `fake_opencode.FakeOpenCode`. No real
OpenCode runs.
"""

from __future__ import annotations

import itertools
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import clock_reads_warm, wait_until

import moxi_turns
from fake_opencode import FakeOpenCode
from moxi_comms import context
from moxi_comms.adapters import opencode as oc
from moxi_comms.adapters import opencode_roster
from moxi_comms.adapters.opencode import OpenCodeAdapter
from moxi_comms.adapters.opencode_roster import OpenCodeAgent
from moxi_comms.mcp import tokens
from moxi_comms.mcp.additions import TOKEN_ENV, URL_ENV

IDENTITY = "coder@proj-a"
PASSWORD = "test-password"
MODEL = "opencode/minimax-m3.1-flash-free"
URL = "http://127.0.0.1:18450/mcp"
#: A token value to look for: a real mint's shape, unlike any other word of a launch.
VALUE = "Oc7" + "q" * 37 + "Zk2"
RENDERED = {"type": "remote", "url": URL, "enabled": True,
            "headers": {"Authorization": f"Bearer {{env:{TOKEN_ENV}}}"}}


@pytest.fixture(autouse=True)
def _gate_reads_warm(monkeypatch):
    clock_reads_warm(monkeypatch, "opencode")


class Handle:
    """The fake as the adapter's server: already listening, never respawned."""

    def __init__(self, fake):
        self.fake = fake
        self.returncode = None

    def wait_listening(self, timeout=None):
        return self.fake.url

    def stop(self):
        pass


@pytest.fixture
def fake():
    server = FakeOpenCode(password=PASSWORD, default_model=MODEL, heartbeat=0.1).start()
    yield server
    server.stop()


@pytest.fixture
def adapter(rig, fake, tmp_path, monkeypatch):
    """An unstarted OpenCode adapter for `coder@proj-a` over the fake, with `base_env`
    as the environment the wrapper was started in."""
    monkeypatch.setattr(oc, "FLUSH_RETRY", 0.05)
    made: list = []

    def build(base_env=None):
        agent = OpenCodeAgent(qualified=IDENTITY, name="coder", scope="proj-a",
                              cwd=str(tmp_path), session_file=opencode_roster.session_path(
                                  IDENTITY), models=[MODEL])
        adapter = OpenCodeAdapter(agent, spawn=False, server=Handle(fake), password=PASSWORD,
                                  autoreconnect=True, backoff=itertools.repeat(0.05),
                                  clock=rig.clock, status_sink=lambda text: None,
                                  base_env=dict(os.environ if base_env is None else base_env))
        fake.on_log = adapter.on_log_line
        made.append(adapter)
        return adapter

    yield build
    for adapter in made:
        adapter.stop()


def content(adapter) -> dict:
    return json.loads(adapter.server_env(MODEL)["OPENCODE_CONFIG_CONTENT"])


def own_config(adapter) -> dict:
    return oc.launch_config(MODEL, adapter.layer_one_path())


def row_of(rig, value=VALUE):
    want = tokens.digest(value)
    return next((r for r in rig.daemon.mcp_tokens.active() if r.token_hash == want), None)


def files_holding(value: str, *roots) -> list[Path]:
    found = []
    for root in roots:
        for path in Path(root).rglob("*"):
            try:
                if path.is_file() and not path.is_symlink() and value in path.read_text(
                        errors="ignore"):
                    found.append(path)
            except OSError:
                continue
    return found


def roots(tmp_path) -> list:
    return [tmp_path, os.environ["HOME"], os.environ["MOXI_COMMS_RUNTIME_DIR"]]


def additions(*, options=None, env=None, argv=(), notes=()):
    return moxi_turns.LaunchAdditions("opencode", tuple(argv), dict(env or {}),
                                      dict(options or {}), tuple(notes))


def turns_answers(monkeypatch, answer):
    """turns' `launch_additions` answers `answer` (or raises it); the calls are kept."""
    calls: list = []

    def launch_additions(harness, *, cwd, env, **kwargs):
        calls.append((harness, cwd, dict(env), kwargs))
        if isinstance(answer, BaseException):
            raise answer
        return answer

    monkeypatch.setattr(moxi_turns, "launch_additions", launch_additions)
    return calls


def assert_no_tools(adapter, *, config=None):
    env = adapter.server_env(MODEL)
    assert TOKEN_ENV not in env and URL_ENV not in env
    assert VALUE not in json.dumps(env)
    assert "comms" not in (content(adapter).get("mcp") or {})
    if config is not None:
        assert content(adapter) == config
    assert adapter.tool_token is None and adapter.tools is False


# ----------------------------------------------------------------- through turns' renderer

def test_a_served_launch_merges_comms_server_naming_the_variable(adapter):
    a = adapter()
    assert a.prepare_launch(URL, token=VALUE) is True
    env = a.server_env(MODEL)
    config = json.loads(env["OPENCODE_CONFIG_CONTENT"])
    assert config["mcp"] == {"comms": RENDERED}
    own = own_config(a)
    assert {k: v for k, v in config.items() if k != "mcp"} == own
    assert env[TOKEN_ENV] == VALUE and env[URL_ENV] == URL
    assert VALUE not in env["OPENCODE_CONFIG_CONTENT"]
    assert [k for k, v in env.items() if VALUE in v] == [TOKEN_ENV]
    assert a.tool_token.hash == tokens.digest(VALUE)
    assert not [n for n in a.additions_notes if "comms" in n], a.additions_notes
    # Layer one and the reply reminder take the tools form.
    from moxi_comms import context
    assert a.layer_form == context.TOOLS


def test_turns_is_asked_once_with_the_server_env_and_the_pair(adapter, monkeypatch):
    calls = turns_answers(monkeypatch, additions(options={"mcp": {"comms": RENDERED}}))
    a = adapter()
    a.prepare_launch(URL, token=VALUE)
    (harness, cwd, env, kwargs), = calls
    assert harness == "opencode" and cwd == a.agent.cwd
    # Each module's part is checked alone with the wrapper's own clash check.
    assert kwargs == {"refuse": oc.additions_clash}
    assert env[TOKEN_ENV] == VALUE and env[URL_ENV] == URL
    assert env["OPENCODE_SERVER_PASSWORD"] == PASSWORD
    a.server_env(MODEL)
    a.server_env(None)
    assert len(calls) == 1


def test_a_started_launch_registers_the_token_and_names_the_tools(rig, adapter, tmp_path):
    rig.daemon.mcp_served = True
    a = adapter()
    a.prepare_launch(URL, token=VALUE)
    a.start()
    row = row_of(rig)
    assert row is not None and row.bound and row.identity == IDENTITY
    assert row.claim_key == a.claims.key and row.adapter == "opencode"
    text = a.layer_one_path().read_text()
    assert text.startswith(context.TOOLS_BLOCK)
    assert f"\n\n{context.TOOLS_FALLBACK_LINE} " in text
    assert files_holding(VALUE, *roots(tmp_path)) == []


def test_a_minted_token_is_one_each_launch_and_the_same_across_restarts(adapter):
    first, second = adapter(), adapter()
    first.prepare_launch(URL)
    second.prepare_launch(URL)
    one = first.server_env(MODEL)[TOKEN_ENV]
    assert one and one == first.server_env(None)[TOKEN_ENV] == first.window_env()[TOKEN_ENV]
    assert second.server_env(MODEL)[TOKEN_ENV] != one
    assert first.tool_token.hash == tokens.digest(one)


def test_against_a_serving_daemon_the_launch_takes_its_url(rig, adapter):
    from moxi_comms.adapters.session import tools_endpoint
    from moxi_comms.daemon import Daemon

    rig.daemon.stop()
    daemon = Daemon(rig.record, clock=rig.clock, start_trim_thread=False,
                    serve_tools=True, mcp_port=0)
    rig.daemon = daemon
    daemon.start()
    url, why = tools_endpoint()
    assert url == daemon.mcp_url and why == ""
    a = adapter()
    assert a.prepare_launch(url, why)
    assert content(a)["mcp"]["comms"]["url"] == url
    a.start()
    value = a.server_env(MODEL)[TOKEN_ENV]
    assert wait_until(lambda: row_of(rig, value) is not None and row_of(rig, value).bound)


# --------------------------------------------------------------------------- the merge

def test_a_permission_fragment_merges_one_level_deep_beside_the_launch_configs(
        adapter, monkeypatch):
    turns_answers(monkeypatch, additions(
        options={"mcp": {"comms": RENDERED, "other": {"type": "remote", "url": "http://x"}},
                 "permission": {"bash": {"mymod status*": "allow"}, "comms_send": "allow"},
                 "share": "disabled"},
        env={"MYMOD_FLAG": "1"}))
    a = adapter()
    assert a.prepare_launch(URL, token=VALUE)
    config = content(a)
    own = own_config(a)
    assert config["permission"] == {**own["permission"], "bash": {"mymod status*": "allow"},
                                    "comms_send": "allow"}
    assert set(config["mcp"]) == {"comms", "other"} and config["share"] == "disabled"
    for key in oc.OWN_KEYS:
        assert config[key] == own[key]
    assert a.server_env(MODEL)["MYMOD_FLAG"] == "1"


@pytest.mark.parametrize("clash", [
    {"options": {"model": "paid/one"}},
    {"options": {"small_model": "paid/one"}},
    {"options": {"instructions": ["/elsewhere.md"]}},
    {"options": {"permission": {"external_directory": "deny"}}},
    {"options": {"permission": {"doom_loop": "allow"}}},
    {"options": {"permission": "allow"}},
    {"options": {"mcp": ["comms"]}},
    {"options": {"x": object()}},
    {"env": {"OPENCODE_SERVER_PASSWORD": "theirs"}},
    {"env": {"OPENCODE_CONFIG_CONTENT": "{}"}},
    {"env": {"MOXI_COMMS_NAME": "someone"}},
    {"env": {TOKEN_ENV: "theirs"}},
    {"argv": ("--port", "1")},
], ids=lambda c: next(iter(next(iter(c.values())))) if not isinstance(
    next(iter(c.values())), tuple) else "argv")
def test_additions_that_set_what_the_wrapper_sets_are_skipped_whole(
        adapter, monkeypatch, clash):
    """The clash skips everything turns returned, comms' own server included, with a
    note; nothing of it is half applied."""
    kwargs = {"options": {"mcp": {"comms": RENDERED}, "share": "disabled"}}
    for key, value in clash.items():
        if key == "options":
            kwargs["options"] = {**kwargs["options"], **value}
        else:
            kwargs[key] = value
    turns_answers(monkeypatch, additions(**kwargs))
    a = adapter()
    assert a.prepare_launch(URL, token=VALUE) is False
    assert_no_tools(a, config=own_config(a))
    assert any(n.startswith("turns' additions are skipped whole: ")
               for n in a.additions_notes), a.additions_notes
    assert a.additions_notes[-1].startswith("comms' tools are off for this session")
    assert "theirs" not in json.dumps(a.server_env(MODEL))


@pytest.mark.parametrize("clash", oc.OWN_PERMISSIONS)
def test_another_modules_clash_leaves_that_module_out_and_keeps_comms_tools(
        adapter, monkeypatch, clash):
    """Through turns' real renderer: a module whose part sets a permission the launch
    config sets is left out alone, and comms' own server still goes in."""
    from moxi_comms.mcp import additions as comms_additions

    # turns' renderer, reached as the fallback test below reaches it.
    renderer = sys.modules[moxi_turns.launch_additions.__module__]
    group = renderer.Group((
        renderer.Contributor("alpha", lambda request: moxi_turns.Contribution(
            tool_permissions={"opencode": (clash, "bash alpha status*")})),
        renderer.Contributor("comms", comms_additions.additions)))
    monkeypatch.setattr(renderer, "load", lambda entries=None: group)
    a = adapter()
    assert a.prepare_launch(URL, token=VALUE) is True
    config = content(a)
    assert config["mcp"] == {"comms": RENDERED}
    own = own_config(a)["permission"]
    assert config["permission"][clash] == own[clash]
    assert "alpha" not in json.dumps(config)
    assert [n for n in a.additions_notes if n.startswith("additions of alpha")] == [
        f"additions of alpha skipped: the launcher refuses it: they set "
        f"`permission.{clash}`, which comms' launch config sets"]
    assert a.tool_token.hash == tokens.digest(VALUE) and a.tools


@pytest.mark.parametrize("error", [ValueError("harness 'opencode' is not one of x"),
                                   RuntimeError("turns broke"), ImportError("no turns")])
def test_a_call_that_fails_skips_the_additions_with_a_note(adapter, monkeypatch, error):
    turns_answers(monkeypatch, error)
    a = adapter()
    assert a.prepare_launch(URL, token=VALUE) is False
    assert_no_tools(a, config=own_config(a))
    assert a.additions_notes[0] == (f"turns could not give this launch its additions: "
                                    f"{error}")
    assert "turns' additions are skipped" in a.additions_notes[-1]


def test_a_turns_error_skips_the_additions(adapter, monkeypatch):
    error = moxi_turns.TurnsError("turns' config cannot be read")
    turns_answers(monkeypatch, error)
    a = adapter()
    assert a.prepare_launch(URL, token=VALUE) is False
    assert_no_tools(a, config=own_config(a))


def test_turns_notes_are_kept_and_logged_and_hold_no_value(adapter, monkeypatch, caplog):
    turns_answers(monkeypatch, additions(options={"mcp": {"comms": RENDERED}},
                                         notes=("additions of other skipped: it raised",)))
    a = adapter()
    with caplog.at_level("WARNING", logger="comms.opencode"):
        a.prepare_launch(URL, token=VALUE)
    assert a.additions_notes == ["additions of other skipped: it raised"]
    assert "additions of other skipped" in caplog.text and VALUE not in caplog.text


# ------------------------------------------------------------------ no tools for the launch

def test_a_launch_the_daemon_serves_no_tools_still_takes_the_other_additions(
        adapter, monkeypatch):
    calls = turns_answers(monkeypatch, additions(
        options={"permission": {"bash": {"mymod*": "allow"}}}))
    a = adapter()
    assert a.prepare_launch(None, "comms' daemon isn't running") is False
    (_, _, env, _), = calls
    assert TOKEN_ENV not in env and URL_ENV not in env
    assert content(a)["permission"]["bash"] == {"mymod*": "allow"}
    assert a.tool_token is None and a.tools_why == "comms' daemon isn't running"
    assert a.additions_notes == ["comms' tools are off for this session: comms' daemon "
                                 "isn't running; `moxi comms send` still works"]


def test_turns_leaving_comms_server_out_for_another_reason_drops_the_pair(
        adapter, monkeypatch):
    turns_answers(monkeypatch, additions(notes=(
        "additions of comms: MCP server 'comms' left out: its URL is not an http or "
        "https URL with a host",)))
    a = adapter()
    assert a.prepare_launch(URL, token=VALUE) is False
    assert_no_tools(a)
    assert a.tools_why == "turns left comms' server out of this launch"
    assert a.layer_form is None                    # no tools: the CLI form


def test_a_session_that_launches_this_wrapper_passes_no_token_on(adapter):
    """A wrapper started inside a session with comms' tools inherits that session's
    pair; the identity's stamp scrubs it, so an unserved launch carries none."""
    inherited = {**os.environ, TOKEN_ENV: VALUE, URL_ENV: URL, "MOXI_COMMS_SHELL_REACH": "off"}
    a = adapter(inherited)
    a.prepare_launch(None, "comms' daemon isn't running")
    env = a.server_env(MODEL)
    assert not {TOKEN_ENV, URL_ENV, "MOXI_COMMS_SHELL_REACH"} & set(env)
    assert VALUE not in json.dumps(env)


def test_a_users_own_config_content_is_replaced_by_the_one_the_wrapper_builds(adapter):
    """OpenCode reads one `OPENCODE_CONFIG_CONTENT`; the wrapper's is the launch config
    with turns' additions merged in. One the wrapper inherited goes, as before: its
    keys reach the server only from the person's own config files."""
    theirs = {"model": "paid/one", "mcp": {"mine": {"type": "remote", "url": "http://m"}},
              "permission": {"bash": "allow"}}
    a = adapter({**os.environ, "OPENCODE_CONFIG_CONTENT": json.dumps(theirs)})
    a.prepare_launch(URL, token=VALUE)
    config = content(a)
    assert config == {**own_config(a), "mcp": {"comms": RENDERED}}


# ------------------------------------------------------------------- the {env:...} fallback

def test_where_opencode_does_not_substitute_the_wrapper_writes_the_value_into_the_content(
        rig, adapter, monkeypatch, tmp_path):
    moxi_turns.launch_additions                 # loads the renderer's module
    renderer = sys.modules[moxi_turns.launch_additions.__module__]
    monkeypatch.setattr(renderer, "OPENCODE_SUBSTITUTES_ENV", False)
    rig.daemon.mcp_served = True
    a = adapter()
    assert a.prepare_launch(URL, token=VALUE) is True
    env = a.server_env(MODEL)
    assert json.loads(env["OPENCODE_CONFIG_CONTENT"])["mcp"]["comms"] == {
        "type": "remote", "url": URL, "enabled": True,
        "headers": {"Authorization": f"Bearer {VALUE}"}}
    assert env[TOKEN_ENV] == VALUE, "the content's environment holds the variable itself"
    assert any("does not substitute" in n for n in a.additions_notes)
    assert not [n for n in a.additions_notes if VALUE in n]
    a.start()
    assert row_of(rig) is not None
    assert files_holding(VALUE, *roots(tmp_path)) == []


def test_the_fallback_needs_turns_own_note(adapter, monkeypatch):
    turns_answers(monkeypatch, additions(notes=(
        "additions of other: MCP server 'comms' left out: this OpenCode does not "
        "substitute {env:...} in its config, and turns never writes a token's value",)))
    a = adapter()
    assert a.prepare_launch(URL, token=VALUE) is False
    assert_no_tools(a)


# --------------------------------------------------------------------------- the plan

def test_the_plan_names_the_url_and_the_variable_never_a_value(adapter, capsys):
    a = adapter()
    roster = SimpleNamespace(source="roster.toml")
    oc.print_plan(a.agent, roster, binary="/bin/opencode", version="1.18.34",
                  comms_path="/bin/moxi", tools_url=URL)
    out = capsys.readouterr().out
    assert f"  tools      {URL} (token in {TOKEN_ENV}, minted at launch)" in out
    oc.print_plan(a.agent, roster, binary="/bin/opencode", version="1.18.34",
                  comms_path="/bin/moxi")
    assert "tools" not in capsys.readouterr().out

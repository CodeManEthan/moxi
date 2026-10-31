"""What a launch adds to its harness: turns' own additions, the contributors' group, the
tool-permission rendering, the doctor's rows, and the hosts' one call."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

import pytest

import moxi_turns as turns
from conftest import write_config
from moxi import doctor as moxi_doctor
from moxi import paths
from moxi_turns import additions, codex, opencode
from moxi_turns.additions import Contribution, LaunchAdditions, McpServer


class Entry:
    """A stand-in for one entry of the group: `target` is what loading it gives, or an
    exception to raise."""

    def __init__(self, name, target):
        self.name = name
        self._target = target

    def load(self):
        if isinstance(self._target, Exception):
            raise self._target
        return self._target


@pytest.fixture(autouse=True)
def roots(turns_env):
    """Every test keeps its runtime and config under the test's own roots."""
    return turns_env


@pytest.fixture
def group(monkeypatch):
    """The group `launch_additions` and the doctor read, set by the test."""
    holder = {"entries": []}
    real = additions.load

    def load(entries=None):
        return real(holder["entries"] if entries is None else entries)

    monkeypatch.setattr(additions, "load", load)
    return holder


def contributes(answer):
    seen = []

    def additions_fn(request):
        seen.append(request)
        return answer(request) if callable(answer) else answer

    additions_fn.seen = seen
    return additions_fn


def adds(harness, **kw):
    kw.setdefault("cwd", "/work")
    kw.setdefault("env", {"HOME": "/home/someone"})
    return turns.launch_additions(harness, **kw)


# -- turns' own additions --------------------------------------------------------------


def test_the_public_api_has_the_call_and_not_the_plugin_dirs():
    assert "launch_additions" in turns.__all__ and "claude_plugin_dirs" not in turns.__all__
    assert not hasattr(turns, "claude_plugin_dirs")
    assert turns.LaunchAdditions is LaunchAdditions and turns.Contribution is Contribution


def _tree(root):
    return sorted(str(p.relative_to(root)) for p in root.rglob("*"))


def test_claude_gets_both_plugin_dirs_and_the_endpoint_and_writes_nothing(group, claude_root):
    before = _tree(claude_root)
    got = adds("claude")
    runner = (claude_root / "moxi-turns").resolve()
    gate = (claude_root / "moxi-turns-gate").resolve()
    assert got.argv == ("--plugin-dir", str(runner), "--plugin-dir", str(gate))
    assert got.env == {"MOXI_TURNS_ENDPOINT": str(paths.script("moxi-turns-endpoint"))}
    assert Path(got.env["MOXI_TURNS_ENDPOINT"]).is_absolute()
    assert got.options == {} and got.notes == ()
    assert _tree(claude_root) == before
    assert not (runner / "endpoint.json").exists()


def test_claude_launches_from_a_read_only_install(group, claude_root):
    folders = [claude_root, claude_root / "moxi-turns", claude_root / "moxi-turns-gate"]
    for folder in folders:
        folder.chmod(0o555)
    try:
        got = adds("claude")
    finally:
        for folder in folders:
            folder.chmod(0o755)
    assert "MOXI_TURNS_ENDPOINT" in got.env
    assert not (claude_root / "moxi-turns" / "endpoint.json").exists()


def test_claude_launches_in_parallel_all_succeed(group, claude_root):
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=16) as pool:
        got = list(pool.map(lambda _: adds("claude"), range(64)))
    want = str(paths.script("moxi-turns-endpoint"))
    assert all(g.env["MOXI_TURNS_ENDPOINT"] == want for g in got)
    assert len({g.argv for g in got}) == 1
    assert not list((claude_root / "moxi-turns").glob("*endpoint.json*"))


def test_claude_endpoint_names_the_configured_command(group, turns_env):
    write_config(turns_env, '[claude]\nendpoint = "/opt/elsewhere/moxi-turns-endpoint"\n')
    assert adds("claude").env["MOXI_TURNS_ENDPOINT"] == "/opt/elsewhere/moxi-turns-endpoint"
    write_config(turns_env, '[claude]\nendpoint = "relative/endpoint"\n')
    with pytest.raises(turns.TurnsError, match="absolute"):
        adds("claude")


def test_claude_endpoint_reads_the_config_from_this_process_not_the_launch_env(
        group, turns_env, tmp_path):
    # The launch's env names another config home; the endpoint is still the one this
    # process's own environment gives, as turns' setup reads it.
    (tmp_path / "turns").mkdir()
    (tmp_path / "turns" / "config.toml").write_text(
        '[claude]\nendpoint = "/opt/other/moxi-turns-endpoint"\n')
    got = adds("claude", env={"MOXI_TURNS_CONFIG_HOME": str(tmp_path)})
    assert got.env["MOXI_TURNS_ENDPOINT"] == str(paths.script("moxi-turns-endpoint"))


def test_claude_raises_when_there_is_no_endpoint_script(group, monkeypatch):
    def missing(name):
        raise FileNotFoundError(f"no {name} in this environment")

    monkeypatch.setattr(paths, "script", missing)
    with pytest.raises(turns.TurnsError, match="moxi-turns-endpoint"):
        adds("claude")


def test_claude_raises_when_the_plugin_cannot_be_set_up(group, claude_root):
    import shutil

    shutil.rmtree(claude_root / "moxi-turns")
    with pytest.raises(turns.TurnsError):
        adds("claude")


@pytest.mark.parametrize("harness", ["codex", "opencode"])
def test_the_hosts_get_no_argv_and_no_options(group, harness, claude_root):
    got = adds(harness)
    assert got == LaunchAdditions(harness)
    assert not (claude_root / "moxi-turns" / "endpoint.json").exists()


@pytest.mark.parametrize("harness", ["claude", "codex", "opencode"])
def test_commands_render_the_grant_in_a_fixed_order(group, harness):
    env = dict(adds(harness, commands=("compact", "exit", "compact")).env)
    if harness == "claude":
        assert env.pop("MOXI_TURNS_ENDPOINT")
    assert env =={"MOXI_TURNS_COMMANDS": "exit,compact"}
    assert "MOXI_TURNS_COMMANDS" not in adds(harness, commands=()).env


def test_a_command_or_harness_turns_does_not_know_is_refused(group):
    with pytest.raises(ValueError, match="stop"):
        adds("codex", commands=("exit", "stop"))
    with pytest.raises(TypeError):
        adds("codex", commands="exit")
    with pytest.raises(ValueError, match="gemini"):
        adds("gemini")


# -- contributions ---------------------------------------------------------------------


def test_a_contributor_gets_the_launch_read_only(group):
    fn = contributes(None)
    group["entries"] = [Entry("mymod", fn)]
    got = adds("codex", cwd="/work/here", env={"A": "1"}, session_id="s-9")
    assert got.notes == ()
    (request,) = fn.seen
    assert (request.harness, request.cwd) == ("codex", "/work/here")
    assert request.session_id == "s-9"
    assert dict(request.env) == {"A": "1"}
    with pytest.raises(TypeError):
        request.env["B"] = "2"


def test_claude_rules_become_allowed_tools_words_after_the_plugin_dirs(group):
    group["entries"] = [
        Entry("zeta", contributes(Contribution(tool_permissions={
            "claude": ("Bash(git diff:*)",)}))),
        Entry("alpha", contributes({"tool_permissions": {
            "claude": ["mcp__alpha__read", "Read"], "opencode": ("read",)}})),
    ]
    got = adds("claude")
    assert got.argv[:4][0::2] == ("--plugin-dir", "--plugin-dir")
    assert got.argv[4:] == ("--allowedTools=mcp__alpha__read", "--allowedTools=Read",
                            "--allowedTools=Bash(git diff:*)")
    assert got.options == {} and got.notes == ()


def test_opencode_rules_become_one_permission_fragment(group):
    group["entries"] = [
        Entry("alpha", contributes(Contribution(tool_permissions={
            "opencode": ("bash git diff*", "bash git status", "webfetch")}))),
        Entry("beta", contributes(Contribution(tool_permissions={
            "opencode": ("bash ls *", "edit"), "claude": ("Edit",)}))),
        Entry("gamma", contributes(Contribution(tool_permissions={
            "opencode": ("webfetch https://example.org/*",)}))),
    ]
    got = adds("opencode")
    assert got.argv == () and got.notes == ()
    assert got.options == {"permission": {
        "bash": {"git diff*": "allow", "git status": "allow", "ls *": "allow"},
        "webfetch": "allow", "edit": "allow"}}


def test_codex_rules_skip_the_contribution_with_a_note_and_a_doctor_row(group):
    group["entries"] = [Entry("mymod", contributes(Contribution(tool_permissions={
        "codex": ("git diff",), "claude": ("Bash(git diff:*)",)})))]
    got = adds("codex")
    assert got == LaunchAdditions("codex", notes=got.notes)
    (note,) = got.notes
    assert "mymod" in note and "Codex" in note
    # The same contribution still goes in on Claude.
    assert adds("claude").argv[-1] == "--allowedTools=Bash(git diff:*)"
    row = {r.name: r for r in additions.rows()}["additions mymod"]
    assert row.state == "warn" and "codex:" in row.detail and "adds to claude" in row.detail


# -- MCP servers -----------------------------------------------------------------------

#: A token's value: no test output may hold it but the launch's own environment.
SECRET = "tok-s3cr3t-9f2a7c"
URL = "http://127.0.0.1:8450/mcp"
TOKEN_ENV = "MOXI_COMMS_MCP_TOKEN"


def serves(name="comms", url=URL, token_env=TOKEN_ENV, **more):
    """A contribution of one MCP server (and whatever else `more` names)."""
    return contributes(Contribution(mcp_servers={name: McpServer(url, token_env)}, **more))


def with_token(**more):
    return {"HOME": "/home/someone", TOKEN_ENV: SECRET, **more}


def mcp_config(argv):
    """The servers of the one `--mcp-config=` word in `argv`."""
    words = [w for w in argv if w.startswith("--mcp-config")]
    assert len(words) == 1, words
    word, = words
    assert word.startswith("--mcp-config=")
    return json.loads(word.removeprefix("--mcp-config="))["mcpServers"]


def test_claude_gets_one_joined_mcp_config_word_naming_the_variable(group):
    group["entries"] = [Entry("comms", serves(tool_permissions={
        "claude": ("mcp__comms__send",)}))]
    got = adds("claude", env=with_token())
    assert got.notes == () and got.options == {}
    assert got.argv[4] == "--allowedTools=mcp__comms__send"
    assert got.argv[5].startswith("--mcp-config=") and len(got.argv) == 6
    assert "--mcp-config" not in got.argv and "--strict-mcp-config" not in got.argv
    assert mcp_config(got.argv) == {"comms": {
        "type": "http", "url": URL,
        "headers": {"Authorization": "Bearer ${MOXI_COMMS_MCP_TOKEN}"}}}


def test_claude_takes_every_modules_servers_in_one_word(group):
    group["entries"] = [
        Entry("beta", serves("beta-two", "https://example.org/mcp", "BETA_TOKEN")),
        Entry("alpha", contributes({"mcp_servers": {
            "alpha": {"url": "http://localhost:9/mcp", "token_env": "ALPHA_TOKEN"},
            "alpha-open": {"url": "http://localhost:10/mcp"}}})),
    ]
    got = adds("claude", env=with_token(ALPHA_TOKEN="a", BETA_TOKEN="b"))
    assert got.notes == ()
    servers = mcp_config(got.argv)
    assert list(servers) == ["alpha", "alpha-open", "beta-two"]
    assert servers["alpha-open"] == {"type": "http", "url": "http://localhost:10/mcp"}
    assert servers["beta-two"]["headers"] == {"Authorization": "Bearer ${BETA_TOKEN}"}


def test_codex_gets_the_server_in_the_thread_config(group):
    group["entries"] = [Entry("comms", serves(tool_permissions={
        "claude": ("mcp__comms__send",)}))]
    got = adds("codex", env=with_token())
    assert got.argv == () and got.notes == ()
    assert got.options == {"config": {"mcp_servers": {"comms": {
        "url": URL, "bearer_token_env_var": TOKEN_ENV,
        "default_tools_approval_mode": "approve"}}}}


def test_opencode_gets_the_server_in_its_config_beside_the_permissions(group):
    group["entries"] = [Entry("comms", serves(tool_permissions={
        "opencode": ("comms_send",)}))]
    got = adds("opencode", env=with_token())
    assert got.argv == () and got.notes == ()
    assert got.options == {
        "permission": {"comms_send": "allow"},
        "mcp": {"comms": {"type": "remote", "url": URL, "enabled": True,
                          "headers": {"Authorization": "Bearer {env:MOXI_COMMS_MCP_TOKEN}"}}}}


@pytest.mark.parametrize("harness", ["claude", "codex", "opencode"])
def test_a_server_with_no_token_gets_no_header(group, harness):
    group["entries"] = [Entry("open", serves("open", token_env=None))]
    got = adds(harness)
    assert got.notes == ()
    if harness == "claude":
        assert mcp_config(got.argv) == {"open": {"type": "http", "url": URL}}
    elif harness == "codex":
        assert got.options["config"]["mcp_servers"]["open"] == {
            "url": URL, "default_tools_approval_mode": "approve"}
    else:
        assert got.options["mcp"]["open"] == {"type": "remote", "url": URL,
                                              "enabled": True}


@pytest.mark.parametrize("harness", ["claude", "codex", "opencode"])
def test_a_server_whose_variable_is_absent_is_left_out_and_the_rest_goes_in(
        group, harness):
    group["entries"] = [Entry("comms", serves(tool_permissions={
        "claude": ("Read",), "opencode": ("read",)}))]
    got = adds(harness, env={"HOME": "/home/someone"})
    assert not any(w.startswith("--mcp-config") for w in got.argv)
    assert "mcp" not in got.options and "config" not in got.options
    (note,) = got.notes
    assert note == ("additions of comms: MCP server 'comms' left out: "
                    "MOXI_COMMS_MCP_TOKEN is not in the launch's environment")
    if harness == "claude":
        assert got.argv[-1] == "--allowedTools=Read"
    elif harness == "opencode":
        assert got.options == {"permission": {"read": "allow"}}


def test_an_empty_variable_counts_as_there_since_turns_never_reads_it(group):
    group["entries"] = [Entry("comms", serves())]
    assert "comms" in mcp_config(adds("claude", env=with_token(**{TOKEN_ENV: ""})).argv)


class PresenceOnly(Mapping):
    """An environment that answers which names it holds and refuses every value read."""

    def __init__(self, names):
        self._names = frozenset(names)

    def __contains__(self, name):
        return name in self._names

    def __getitem__(self, name):
        raise AssertionError(f"the renderer read the value of {name}")

    def __iter__(self):
        return iter(self._names)

    def __len__(self):
        return len(self._names)


@pytest.mark.parametrize("harness", ["claude", "codex", "opencode"])
def test_the_renderer_asks_only_whether_the_variable_is_there(harness):
    contribution = Contribution(mcp_servers={"comms": McpServer(URL, TOKEN_ENV)})
    rendered = additions.render(contribution, harness, PresenceOnly({TOKEN_ENV}))
    assert list(rendered.servers) == ["comms"] and rendered.left_out == ()
    rendered = additions.render(contribution, harness, PresenceOnly({"OTHER"}))
    assert rendered.servers == {} and len(rendered.left_out) == 1


def _files_holding(root, value):
    return [p for p in Path(root).rglob("*")
            if p.is_file() and value.encode() in p.read_bytes()]


@pytest.mark.parametrize("harness", ["claude", "codex", "opencode"])
def test_the_value_is_in_no_argv_word_option_or_file(group, harness, turns_env,
                                                     claude_root):
    group["entries"] = [Entry("comms", serves(tool_permissions={
        "claude": ("mcp__comms__send",)}))]
    got = adds(harness, env=with_token(), session_id="s-1", commands=("exit",))
    assert got.argv or got.options
    assert not any(SECRET in word for word in got.argv)
    assert not any(SECRET in value for value in got.env.values())
    assert SECRET not in json.dumps(got.options)
    assert SECRET not in "".join(got.notes)
    assert TOKEN_ENV in (" ".join(got.argv) + json.dumps(got.options))
    for root in (turns_env, claude_root):
        assert _files_holding(root, SECRET) == []


@pytest.mark.parametrize("harness", ["codex", "opencode"])
def test_a_host_carries_the_variable_and_puts_its_value_in_no_option(
        group, harness, tmp_path, built, turns_env):
    group["entries"] = [Entry("comms", serves())]
    _launch(harness, tmp_path, env={TOKEN_ENV: SECRET})
    host = built["harness"]
    assert host.env[TOKEN_ENV] == SECRET        # carried in the launch's environment
    assert SECRET not in json.dumps(host.options)
    if harness == "opencode":
        server_env = host.server_env()
        content = server_env["OPENCODE_CONFIG_CONTENT"]
        assert SECRET not in content
        assert json.loads(content)["mcp"]["comms"]["headers"] == {
            "Authorization": "Bearer {env:MOXI_COMMS_MCP_TOKEN}"}
        assert [k for k, v in server_env.items() if SECRET in v] == [TOKEN_ENV]
    else:
        assert host.options["config"]["mcp_servers"]["comms"][
            "bearer_token_env_var"] == TOKEN_ENV
    for root in (tmp_path, turns_env):
        assert _files_holding(root, SECRET) == []


@pytest.mark.parametrize("name,url,token_env,why", [
    ("Comms", URL, TOKEN_ENV, "lowercase letters, digits and hyphens"),
    ("comms_x", URL, TOKEN_ENV, "lowercase letters, digits and hyphens"),
    ("", URL, TOKEN_ENV, "lowercase letters, digits and hyphens"),
    ("comms", "ftp://127.0.0.1/mcp", TOKEN_ENV, "http or https"),
    ("comms", "/mcp", TOKEN_ENV, "http or https"),
    ("comms", "http:///mcp", TOKEN_ENV, "http or https"),
    ("comms", "http://127.0.0.1/${HOME}", TOKEN_ENV, "'$'"),
    ("comms", "http://127.0.0.1/{env:HOME}", TOKEN_ENV, "'$'"),
    ("comms", "http://127.0.0.1/ mcp", TOKEN_ENV, "whitespace"),
    ("comms", URL, "BAD-NAME", "not a variable name"),
    ("comms", URL, "X}${HOME", "not a variable name"),
])
def test_a_bad_name_url_or_variable_leaves_the_server_out(group, name, url, token_env,
                                                         why):
    group["entries"] = [Entry("comms", serves(name, url, token_env)),
                        Entry("other", serves("other"))]
    env = with_token(**{"BAD-NAME": "x", "X}${HOME": "x"})
    got = adds("claude", env=env)
    assert list(mcp_config(got.argv)) == ["other"]
    (note,) = got.notes
    assert note.startswith("additions of comms: MCP server") and why in note
    row = {r.name: r for r in additions.rows()}["additions comms: mcp_servers"]
    assert row.state == "warn" and why in row.detail


def test_a_server_name_an_earlier_module_has_is_left_out(group):
    group["entries"] = [Entry("beta", serves("shared", "http://localhost:2/mcp", None)),
                        Entry("alpha", serves("shared", "http://localhost:1/mcp", None))]
    for harness in ("claude", "codex", "opencode"):
        got = adds(harness)
        (note,) = got.notes
        assert note == ("additions of beta: MCP server 'shared' left out: alpha already "
                        "adds a server of that name")
    assert mcp_config(adds("claude").argv)["shared"]["url"] == "http://localhost:1/mcp"
    rows = {r.name: r for r in additions.rows()}
    assert "additions alpha: mcp_servers" not in rows
    assert rows["additions beta: mcp_servers"].detail.startswith("claude, codex, opencode: ")


@pytest.mark.parametrize("servers", [
    {"comms": URL},
    {"comms": {"url": URL, "token": TOKEN_ENV}},
    {"comms": {"token_env": TOKEN_ENV}},
    {"comms": McpServer(8450)},
    {"comms": McpServer(URL, 3)},
])
def test_a_malformed_server_refuses_the_contribution(group, servers):
    group["entries"] = [Entry("comms", contributes({"mcp_servers": servers,
                                                    "tool_permissions": {
                                                        "claude": ("Read",)}}))]
    got = adds("claude", env=with_token())
    assert not any(w.startswith(("--mcp-config", "--allowedTools")) for w in got.argv)
    (note,) = got.notes
    assert note.startswith("additions of comms skipped: its MCP server 'comms'")
    assert {r.name: r for r in additions.rows()}["additions comms"].state == "fail"


def test_codex_rules_still_skip_a_contribution_with_servers_on_codex(group):
    group["entries"] = [Entry("mymod", serves(tool_permissions={"codex": ("x",)}))]
    got = adds("codex", env=with_token())
    assert got.options == {}
    (note,) = got.notes
    assert note.startswith("additions of mymod skipped:") and "Codex" in note


def test_opencode_that_does_not_substitute_leaves_the_tokened_server_out(group,
                                                                        monkeypatch):
    monkeypatch.setattr(additions, "OPENCODE_SUBSTITUTES_ENV", False)
    group["entries"] = [Entry("comms", contributes(Contribution(mcp_servers={
        "comms": McpServer(URL, TOKEN_ENV), "open": McpServer(URL)})))]
    got = adds("opencode", env=with_token())
    assert list(got.options["mcp"]) == ["open"]
    (note,) = got.notes
    assert "{env:...}" in note and SECRET not in note
    # Claude and Codex are unaffected.
    assert list(mcp_config(adds("claude", env=with_token()).argv)) == ["comms", "open"]


def test_the_doctor_shows_a_server_left_out_and_none_when_all_go_in(group, monkeypatch):
    group["entries"] = [Entry("comms", serves(tool_permissions={"claude": ("Read",)}))]
    monkeypatch.delenv(TOKEN_ENV, raising=False)
    rows = {r.name: r for r in additions.rows()}
    assert rows["additions comms"].state == "ok"
    assert rows["additions comms"].detail == "adds to claude"
    row = rows["additions comms: mcp_servers"]
    assert row.state == "warn" and row.module == "turns"
    assert row.detail == ("claude, codex, opencode: MCP server 'comms' left out: "
                          "MOXI_COMMS_MCP_TOKEN is not in the launch's environment")
    assert row.fix
    monkeypatch.setenv(TOKEN_ENV, SECRET)
    rows = {r.name: r for r in additions.rows()}
    assert "additions comms: mcp_servers" not in rows
    assert rows["additions comms"].detail == "adds to claude, codex, opencode"
    assert all(SECRET not in (r.detail or "") for r in rows.values())


def test_the_package_exports_mcp_server():
    assert turns.McpServer is McpServer and "McpServer" in turns.__all__


@pytest.mark.parametrize("permissions", [
    {"claude": ("",)},
    {"claude": ("Read", "   ")},
    {"claude": ("Read", 3)},
    {"claude": "Read"},
    {"gemini": ("Read",)},
])
def test_a_bad_rule_refuses_the_contribution_on_every_harness(group, permissions):
    group["entries"] = [
        Entry("bad", contributes(Contribution(tool_permissions={
            **permissions, "opencode": ("edit",)}))),
        Entry("good", contributes(Contribution(tool_permissions={"opencode": ("read",)}))),
    ]
    got = adds("opencode")
    assert got.options == {"permission": {"read": "allow"}}
    (note,) = got.notes
    assert note.startswith("additions of bad skipped")
    row = {r.name: r for r in additions.rows()}["additions bad"]
    assert row.state == "fail"


@pytest.mark.parametrize("answer", [
    {"tool_permissions": {}, "hooks": ["x"]},
    ("claude", "Read"),
    Contribution(tool_permissions=("Read",)),
])
def test_a_malformed_contribution_is_skipped(group, answer):
    group["entries"] = [Entry("mymod", contributes(answer))]
    (note,) = adds("codex").notes
    assert "mymod" in note


def test_a_contributor_that_raises_costs_only_its_own_additions(group):
    def boom(request):
        raise RuntimeError("no luck")

    group["entries"] = [Entry("bad", boom),
                        Entry("good", contributes(Contribution(tool_permissions={
                            "claude": ("Read",)})))]
    got = adds("claude")
    assert got.argv[-1] == "--allowedTools=Read"
    (note,) = got.notes
    assert "bad" in note and "no luck" in note
    row = {r.name: r for r in additions.rows()}["additions bad"]
    assert row.state == "fail" and "no luck" in row.detail


def test_entries_that_do_not_load_are_notes_and_failed_rows(group):
    group["entries"] = [Entry("broken", ImportError("no module named broken")),
                        Entry("notfn", {"tool_permissions": {}}),
                        Entry("twice", contributes(None)),
                        Entry("twice", contributes(None)),
                        Entry("Not A Name", contributes(None)),
                        Entry("fine", contributes(None))]
    got = adds("codex")
    assert len(got.notes) == 4
    assert all(note.startswith("additions of ") and " are off: " in note
               for note in got.notes)
    rows = {r.name: r for r in additions.rows()}
    for name in ("broken", "notfn", "twice", "Not A Name"):
        assert rows[f"additions {name}"].state == "fail", name
    assert rows["additions fine"].state == "ok"
    assert rows["additions fine"].detail == "adds nothing here"


def test_the_doctor_carries_the_additions_rows(group):
    from moxi_turns import doctor

    group["entries"] = [Entry("broken", ImportError("gone"))]
    rows = [r for r in doctor.check() if r.name == "additions broken"]
    assert len(rows) == 1 and isinstance(rows[0], moxi_doctor.Row)
    assert rows[0].module == "turns" and rows[0].state == "fail"


def test_the_group_reads_the_installed_entry_points(monkeypatch):
    from moxi import modules

    asked = []

    def extensions(name):
        asked.append(name)
        return [Entry("mymod", contributes(None))]

    monkeypatch.setattr(modules, "extensions", extensions)
    loaded = additions.load()
    assert asked == ["moxi.turns.additions"]
    assert [c.module for c in loaded.contributors] == ["mymod"]


# -- the hosts -------------------------------------------------------------------------


class _Stop(Exception):
    """Raised where a host would start its endpoint: what it was built with is all a
    test needs."""


@pytest.fixture
def calls(monkeypatch):
    """Counts the hosts' calls of `launch_additions`, answering with `answer`."""
    holder = {"count": 0, "asked": [], "answer": None}

    def launch_additions(harness, **kw):
        holder["count"] += 1
        holder["asked"].append((harness, kw))
        return holder["answer"] or LaunchAdditions(harness)

    monkeypatch.setattr(additions, "launch_additions", launch_additions)
    return holder


@pytest.fixture
def built(monkeypatch):
    """The harness each host built, its launch stopped before anything starts."""
    holder = {}

    class Endpoint:
        def __init__(self, harness, **kw):
            holder["harness"] = harness
            raise _Stop

    def run(harness):
        holder["harness"] = harness
        raise _Stop

    monkeypatch.setattr(codex, "Endpoint", Endpoint)
    monkeypatch.setattr(opencode, "_run", run)
    return holder


def _launch(harness, tmp_path, **kw):
    host = codex if harness == "codex" else opencode
    with pytest.raises(_Stop):
        host.launch(tmp_path, **kw)


@pytest.mark.parametrize("harness", ["codex", "opencode"])
def test_a_host_asks_once_without_additions(harness, tmp_path, calls, built):
    calls["answer"] = LaunchAdditions(harness, env={"MOXI_TURNS_COMMANDS": "compact"},
                                      notes=("additions of x skipped: why",))
    _launch(harness, tmp_path, env={"MINE": "1"})
    assert calls["count"] == 1
    (asked_harness, kw), = calls["asked"]
    assert asked_harness == harness and kw["cwd"] == tmp_path.resolve()
    assert kw["env"]["MINE"] == "1"
    assert built["harness"].env["MOXI_TURNS_COMMANDS"] == "compact"


@pytest.mark.parametrize("harness", ["codex", "opencode"])
def test_a_host_never_asks_when_given_additions(harness, tmp_path, calls, built):
    given = LaunchAdditions(harness, env={"MOXI_TURNS_COMMANDS": "exit", "MINE": "theirs"})
    _launch(harness, tmp_path, env={"MINE": "mine"}, additions=given)
    assert calls["count"] == 0
    env = built["harness"].env
    assert env["MOXI_TURNS_COMMANDS"] == "exit" and env["MINE"] == "theirs"


def test_opencode_options_join_under_the_callers(tmp_path, calls, built):
    given = LaunchAdditions("opencode", options={"permission": {
        "edit": "allow", "bash": {"git diff*": "allow"}, "webfetch": "allow"}})
    mine = {"model": "p/m",
            "permission": {"external_directory": "allow", "webfetch": "deny"}}
    _launch("opencode", tmp_path, options=mine, additions=given)
    assert built["harness"].options == {"model": "p/m", "permission": {
        "external_directory": "allow", "webfetch": "deny", "edit": "allow",
        "bash": {"git diff*": "allow"}}}
    assert mine == {"model": "p/m", "permission": {"external_directory": "allow",
                                                   "webfetch": "deny"}}


def test_codex_options_join_under_the_callers(tmp_path, calls, built):
    given = LaunchAdditions("codex", options={"config": {"a": {"b": 1}, "c": 2}})
    _launch("codex", tmp_path, options={"sandbox": "read-only", "config": {"c": 3}},
            additions=given)
    assert built["harness"].options == {"sandbox": "read-only",
                                        "config": {"a": {"b": 1}, "c": 3}}


@pytest.mark.parametrize("harness,options", [("codex", {"threadId": "t"}),
                                             ("opencode", {"instructions": ["x"]})])
def test_a_key_turns_owns_in_the_additions_is_refused(harness, options, tmp_path, calls,
                                                     built):
    with pytest.raises(ValueError, match="turns owns"):
        (codex if harness == "codex" else opencode).launch(
            tmp_path, additions=LaunchAdditions(harness, options=options))
    assert "harness" not in built


def test_opencode_refuses_additions_naming_its_own_variables(tmp_path, calls, built):
    with pytest.raises(ValueError, match="OPENCODE_SERVER_PASSWORD"):
        opencode.launch(tmp_path, additions=LaunchAdditions(
            "opencode", env={"OPENCODE_SERVER_PASSWORD": "x"}))


@pytest.mark.parametrize("given,error", [
    (LaunchAdditions("claude"), ValueError),
    (LaunchAdditions("codex", argv=("--flag",)), ValueError),
    ({"env": {}}, TypeError),
])
def test_additions_a_host_cannot_apply_are_refused(given, error, tmp_path, calls, built):
    with pytest.raises(error):
        codex.launch(tmp_path, additions=given)
    assert calls["count"] == 0 and "harness" not in built

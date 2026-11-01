"""comms' tools in a launch: the wrappers mint the session's token and stamp it with the
URL the daemon answered, comms' contribution to turns' launch additions adds the server
when, and only when, a launch carries both, and the token's value is in no argv word, no
file and no line a launch prints.

Also here: the variables comms stamps for its tools are on its `SESSION_ENV`,
`INTERNAL_ENV` and the wrapper's scrub; the start text names the tools; and the Claude
wrapper binds a launcher's grant of session commands to the id it passes to `claude`.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import wait_until, write_config

import moxi_turns
from moxi_comms import listener
from moxi_comms import module as announcement
from moxi_comms.adapters import codex_wrapper, session, shim_roster
from moxi_comms.mcp import additions as contributor
from moxi_comms.mcp import tokens

NAME = "launched"
SID = "5e55a1d0-0000-4000-8000-0000000000a1"
OTHER = "5e55a1d0-0000-4000-8000-0000000000a2"
URL = "http://127.0.0.1:18450/mcp"
#: A token value to look for: a real mint's shape, unlike any other word of a launch.
VALUE = "Tq9" + "x" * 37 + "Zk1"
STAMPS = ("MOXI_COMMS_MCP_TOKEN", "MOXI_COMMS_MCP_URL", "MOXI_COMMS_SHELL_REACH")


def request(env, harness="claude"):
    return moxi_turns.AdditionsRequest(harness, "/work", dict(env), None)


def pair(token=VALUE, url=URL) -> dict:
    return {"MOXI_COMMS_MCP_TOKEN": token, "MOXI_COMMS_MCP_URL": url}


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
    return [tmp_path, os.environ["HOME"], os.environ["MOXI_COMMS_RUNTIME_DIR"],
            os.environ["CLAUDE_CONFIG_DIR"]]


# --------------------------------------------------------------------- the contributor

@pytest.mark.parametrize("env", [
    {}, {"MOXI_COMMS_MCP_TOKEN": VALUE}, {"MOXI_COMMS_MCP_URL": URL},
    pair(token=""), pair(url=""),
], ids=["neither", "token-only", "url-only", "empty-token", "empty-url"])
def test_the_contributor_adds_nothing_without_both_stamps(env):
    assert contributor.additions(request(env)) is None


def test_with_both_it_adds_the_server_at_the_stamped_url_and_six_claude_rules():
    from moxi_comms.mcp import tools as mcp_tools

    got = contributor.additions(request(pair()))
    assert got == moxi_turns.Contribution(
        mcp_servers={"comms": moxi_turns.McpServer(url=URL,
                                                   token_env="MOXI_COMMS_MCP_TOKEN")},
        tool_permissions={"claude": ("mcp__comms__send", "mcp__comms__reply",
                                     "mcp__comms__agents", "mcp__comms__whoami",
                                     "mcp__comms__history", "mcp__comms__delete")})
    assert contributor.TOOLS == mcp_tools.NAMES_OF_TOOLS, "the rules name the tools served"
    assert VALUE not in repr(got), "it never returns the value"


def test_the_url_is_the_stamped_one_whatever_comms_config_says():
    """The daemon read `[mcp]` at its start, and the stamp is its answer: the contributor
    reads neither key."""
    write_config(None, "[mcp]\nenabled = false\nport = 9999\n")
    got = contributor.additions(request(pair()))
    assert got.mcp_servers["comms"].url == URL


def test_the_entry_point_names_the_contributor():
    from importlib.metadata import entry_points

    (found,) = entry_points(group="moxi.turns.additions", name="comms")
    assert found.load() is contributor.additions


def launch_additions(harness, env, tmp_path):
    return moxi_turns.launch_additions(harness, cwd=tmp_path, env=env)


def test_through_turns_renderer_claude_gets_one_word_naming_the_variable(tmp_path):
    adds = launch_additions("claude", {**pair(), "PATH": "/bin"}, tmp_path)
    (word,) = [w for w in adds.argv if w.startswith("--mcp-config=")]
    config = json.loads(word.split("=", 1)[1])
    assert config == {"mcpServers": {"comms": {
        "type": "http", "url": URL,
        "headers": {"Authorization": "Bearer ${MOXI_COMMS_MCP_TOKEN}"}}}}
    rules = [w.split("=", 1)[1] for w in adds.argv if w.startswith("--allowedTools=")]
    assert rules == list(contributor.CLAUDE_RULES)
    assert not [n for n in adds.notes if "comms" in n], adds.notes


def test_through_turns_renderer_codex_and_opencode_name_the_variable(tmp_path):
    codex = launch_additions("codex", pair(), tmp_path)
    assert codex.options["config"]["mcp_servers"]["comms"] == {
        "url": URL, "bearer_token_env_var": "MOXI_COMMS_MCP_TOKEN",
        "default_tools_approval_mode": "approve"}
    assert codex.options["config"]["shell_environment_policy"] == {
        "filters": {"MOXI_COMMS_MCP_TOKEN": "exclude"}}
    assert codex.options["config"]["features"] == {"shell_snapshot": False}
    opencode = launch_additions("opencode", pair(), tmp_path)
    assert opencode.options["mcp"]["comms"] == {
        "type": "remote", "url": URL, "enabled": True,
        "headers": {"Authorization": "Bearer {env:MOXI_COMMS_MCP_TOKEN}"}}


@pytest.mark.parametrize("harness", ["claude", "codex", "opencode"])
def test_the_value_is_in_no_argv_word_variable_option_note_or_file(harness, tmp_path):
    adds = launch_additions(harness, pair(), tmp_path)
    assert not [w for w in adds.argv if VALUE in w]
    assert not [v for v in adds.env.values() if VALUE in v]
    assert VALUE not in json.dumps(adds.options) and VALUE not in "".join(adds.notes)
    assert files_holding(VALUE, *roots(tmp_path)) == []


def test_a_launch_without_the_pair_gets_no_comms_entry(tmp_path):
    adds = launch_additions("claude", {"PATH": "/bin"}, tmp_path)
    assert not [w for w in adds.argv if "mcp" in w]
    assert not [n for n in adds.notes if "comms" in n], adds.notes


# ------------------------------------------------------------------- the env lists

def test_the_stamps_are_comms_session_and_internal_variables():
    assert set(announcement.SESSION_ENV) == set(STAMPS)
    assert set(STAMPS) <= set(announcement.INTERNAL_ENV)
    assert contributor.STAMPS == STAMPS
    from moxi import modules

    found = modules.load("comms")
    assert found.state == "present", found.reason
    assert set(found.session_env) == set(STAMPS)


def test_the_wrapper_scrubs_each_inherited_stamp():
    assert set(STAMPS) <= set(session.INHERITED_COMMS_ENV)
    inherited = {**pair(), "MOXI_COMMS_SHELL_REACH": "off", "PATH": "/bin"}
    env = session.stamped_env(inherited, name="inner", global_=True)
    assert not set(STAMPS) & set(env) and env["PATH"] == "/bin"


# --------------------------------------------------------------------- the daemon's read

@pytest.fixture
def serving(rig):
    """The rig's daemon started again, serving comms' tools on a free loopback port."""
    from moxi_comms.daemon import Daemon

    rig.daemon.stop()
    daemon = Daemon(rig.record, clock=rig.clock, start_trim_thread=False,
                    serve_tools=True, mcp_port=0)
    rig.daemon = daemon
    return daemon.start()


def test_the_wrapper_reads_the_url_a_serving_daemon_answers(serving):
    url, why = session.tools_endpoint()
    assert (url, why) == (serving.mcp_url, "") and url.startswith("http://127.0.0.1:")


def test_a_daemon_that_serves_no_tools_says_why(rig):
    url, why = session.tools_endpoint()
    assert url is None and why.startswith("comms' daemon doesn't serve them")


def test_no_daemon_is_no_tools_at_once():
    assert session.tools_endpoint() == (None, "comms' daemon isn't running")


# ------------------------------------------------------------------ the Claude wrapper

@pytest.fixture
def launched(monkeypatch, tmp_path):
    """The wrapper run to its exec with turns' real additions: (main, the captures). Each
    `launch_additions` call's environment is kept too."""
    binary = tmp_path / "claude"
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)
    seen = {"asked": []}
    real = moxi_turns.launch_additions

    def asked(harness, **kw):
        seen["asked"].append(dict(kw["env"]))
        return real(harness, **kw)

    def launch(s, found, cmd, env, *, prog="moxi-comms-claude", **_):
        seen.update(session=s, cmd=cmd, env=env)
        return 0

    monkeypatch.setattr(moxi_turns, "launch_additions", asked)
    monkeypatch.setattr(session, "launch", launch)

    def run(*passthrough, own=SID, dry_run=False):
        argv = ["--name", NAME, "--claude", str(binary)]
        argv += ["--session-id", own] if own else []
        argv += ["--dry-run"] if dry_run else []
        return session.main([*argv, "--", *passthrough]), seen
    return run


def served(monkeypatch, url=URL):
    monkeypatch.setattr(session, "tools_endpoint", lambda *a, **k: (url, ""))


def test_a_served_launch_mints_and_stamps_before_turns_is_asked(monkeypatch, launched,
                                                                capsys, tmp_path):
    served(monkeypatch)
    code, seen = launched("--model", "haiku")
    assert code == 0
    env = seen["env"]
    token = env["MOXI_COMMS_MCP_TOKEN"]
    assert len(token) >= 43 and env["MOXI_COMMS_MCP_URL"] == URL
    (asked,) = seen["asked"]
    assert asked["MOXI_COMMS_MCP_TOKEN"] == token and asked["MOXI_COMMS_MCP_URL"] == URL
    (word,) = [w for w in seen["cmd"] if w.startswith("--mcp-config=")]
    assert "${MOXI_COMMS_MCP_TOKEN}" in word
    assert not [w for w in seen["cmd"] if token in w], "never in argv"
    out = capsys.readouterr()
    assert token not in out.out and token not in out.err
    assert f"  tools      {URL} (token in MOXI_COMMS_MCP_TOKEN" in out.out
    assert files_holding(token, *roots(tmp_path)) == []


def test_two_launches_get_two_tokens(monkeypatch, launched):
    served(monkeypatch)
    first = launched()[1]["env"]["MOXI_COMMS_MCP_TOKEN"]
    second = launched(own=OTHER)[1]["env"]["MOXI_COMMS_MCP_TOKEN"]
    assert first != second


def test_a_dry_run_mints_nothing_and_shows_the_entry_naming_only_the_variable(
        monkeypatch, launched, capsys):
    served(monkeypatch)
    monkeypatch.setattr(tokens, "mint", lambda: pytest.fail("a dry run mints nothing"))
    code, seen = launched(dry_run=True)
    assert code == 0 and "env" not in seen, "nothing launched"
    (asked,) = seen["asked"]
    assert asked["MOXI_COMMS_MCP_TOKEN"] == session.MINTED_AT_LAUNCH
    out = capsys.readouterr().out
    (line,) = [ln for ln in out.splitlines() if ln.startswith("  launch")]
    assert '"Authorization":"Bearer ${MOXI_COMMS_MCP_TOKEN}"' in line
    assert URL in line and session.MINTED_AT_LAUNCH not in line


def test_a_launch_the_daemon_serves_no_tools_stamps_nothing_and_says_so(
        monkeypatch, launched, capsys):
    """A wrapper run inside a session (its token and URL inherited) whose daemon now
    serves no tools passes neither on: the inner session never sends as the outer."""
    for key, value in {**pair(), "MOXI_COMMS_SHELL_REACH": "off"}.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(session, "tools_endpoint",
                        lambda *a, **k: (None, "comms' daemon isn't running"))
    code, seen = launched()
    assert code == 0
    assert not set(STAMPS) & set(seen["env"]) and not set(STAMPS) & set(seen["asked"][0])
    assert not [w for w in seen["cmd"] if "mcp" in w]
    err = capsys.readouterr().err
    assert ("moxi-comms-claude: comms' tools are off for this session: comms' daemon "
            "isn't running; `moxi comms send` still works") in err


def test_a_launch_against_a_serving_daemon_carries_its_url(serving, launched):
    code, seen = launched()
    assert code == 0 and seen["env"]["MOXI_COMMS_MCP_URL"] == serving.mcp_url


# ------------------------------------------------------------- the grant, bound

def grant_of(seen) -> str | None:
    return seen["env"].get("MOXI_TURNS_COMMANDS")


def test_a_fresh_launch_binds_the_launchers_grant_to_the_id_it_names(monkeypatch,
                                                                     launched):
    monkeypatch.setenv("MOXI_TURNS_COMMANDS", "exit,compact")
    code, seen = launched()
    assert code == 0
    assert grant_of(seen) == f"{SID}:exit,compact", "turns' bound form, its order"
    assert seen["cmd"][1:3] == ["--session-id", SID], "the id the grant is bound to"


@pytest.mark.parametrize("passthrough", [["--resume", SID], ["-r", SID.upper()]],
                         ids=["resume", "r-upper"])
def test_a_resume_binds_the_grant_to_the_id_it_resumes(monkeypatch, launched, passthrough):
    monkeypatch.setenv("MOXI_TURNS_COMMANDS", "exit")
    code, seen = launched(*passthrough, own=None)
    assert code == 0 and grant_of(seen) == f"{SID}:exit"
    assert seen["cmd"][-4:-2] == passthrough, "the resume passes through unchanged"


def test_a_fork_binds_the_grant_to_the_new_id_the_wrapper_names(monkeypatch, launched):
    monkeypatch.setenv("MOXI_TURNS_COMMANDS", "exit")
    code, seen = launched("--resume", SID, "--fork-session", own=None)
    assert code == 0
    named = seen["cmd"][seen["cmd"].index("--session-id") + 1]
    assert named != SID and grant_of(seen) == f"{named}:exit"


def test_an_unnamed_resume_keeps_the_grant_bare(monkeypatch, launched):
    monkeypatch.setenv("MOXI_TURNS_COMMANDS", "exit")
    code, seen = launched("--continue", own=None)
    assert code == 0 and grant_of(seen) == "exit"


def test_a_grant_already_bound_passes_whole(monkeypatch, launched):
    monkeypatch.setenv("MOXI_TURNS_COMMANDS", f"{OTHER}:exit")
    code, seen = launched()
    assert code == 0 and grant_of(seen) == f"{OTHER}:exit"


def test_a_name_that_is_no_session_command_is_dropped_not_refused(monkeypatch, launched):
    monkeypatch.setenv("MOXI_TURNS_COMMANDS", "exit, bogus")
    code, seen = launched()
    assert code == 0 and grant_of(seen) == f"{SID}:exit"


def test_no_grant_stamps_none(launched):
    code, seen = launched()
    assert code == 0 and grant_of(seen) is None


# ------------------------------------------------------------------- the start text

def event(name, env, harness="claude"):
    return moxi_turns.SessionEvent(event=name, harness=harness, session_id="s-1",
                                   env=dict(env))


def session_env(**extra) -> dict:
    s = session.Session("ls-tools", name="reviewer", scope="proj-a", project="proj-a")
    s.write()
    return dict(s.env(), **extra)


def test_the_start_text_names_the_tools_when_the_launch_carries_them(capsys):
    plain = session_env()
    today = listener.answer(event("start", plain))
    assert listener.TOOLS_LINE not in today and today.startswith(shim_roster.BLOCK)
    with_pair = listener.answer(event("start", session_env(**pair())))
    assert with_pair == f"{today}\n\n{listener.TOOLS_LINE}"
    assert listener.TOOLS_LINE == ("Send with the comms tools (`send`, `reply`); "
                                   "`moxi comms` does the same from a shell.")
    capsys.readouterr()


def test_a_shell_that_cant_reach_comms_changes_the_second_clause(capsys):
    env = session_env(**pair(), MOXI_COMMS_SHELL_REACH="off")
    text = listener.answer(event("start", env))
    assert text.endswith("\n\nSend with the comms tools (`send`, `reply`); this "
                         "session's shell can't reach comms.")
    assert listener.tools_line({**pair(), "MOXI_COMMS_SHELL_REACH": "on"}) == \
        listener.TOOLS_LINE
    assert listener.tools_line({"MOXI_COMMS_SHELL_REACH": "off"}) is None, \
        "no pair, no line"
    capsys.readouterr()


# ------------------------------------------------------------------- the Codex wrapper

def test_a_codex_dry_run_shows_the_tools_and_mints_nothing(tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(codex_wrapper, "tools_endpoint", lambda *a, **k: (URL, ""))
    monkeypatch.setattr(tokens, "mint", lambda: pytest.fail("a dry run mints nothing"))
    assert codex_wrapper.main(["--name", "coder", "--dry-run"]) == 0
    out = capsys.readouterr()
    assert f"  tools      {URL} (token in MOXI_COMMS_MCP_TOKEN, minted at launch)" in out.out
    assert "tools are off" not in out.err


def test_a_codex_launch_without_tools_says_so(tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert codex_wrapper.main(["--name", "coder", "--dry-run"]) == 0
    out = capsys.readouterr()
    assert "  tools " not in out.out
    assert ("moxi-comms-codex: comms' tools are off for this session: comms' daemon "
            "isn't running; `moxi comms send` still works") in out.err


@pytest.fixture
def fake_host(tmp_path):
    """A stand-in for turns' Codex host: it records what it got and waits."""
    out = tmp_path / "host.out"
    script = tmp_path / "moxi-turns-codex-host"
    script.write_text(textwrap.dedent(f"""\
        #!/bin/sh
        echo "token $MOXI_COMMS_MCP_TOKEN" >> {out}
        echo "url $MOXI_COMMS_MCP_URL" >> {out}
        echo "reach $MOXI_COMMS_SHELL_REACH" >> {out}
        echo "args $*" >> {out}
        while [ "$1" != "--instructions-file" ]; do shift; done
        cp "$2" {tmp_path}/instructions.seen
        echo started >> {out}
        while [ ! -e {tmp_path}/release ]; do sleep 0.05; done
        """))
    script.chmod(0o755)
    return SimpleNamespace(script=script, out=out, release=tmp_path / "release",
                           instructions=tmp_path / "instructions.seen")


def test_a_codex_launch_stamps_the_pair_for_the_host_and_names_the_tools(
        serving, fake_host):
    code = ("import moxi.paths, moxi_turns\n"
            f"moxi.paths.script = lambda name: __import__('pathlib').Path("
            f"{str(fake_host.script)!r})\n"
            "moxi_turns.codex_host_available = lambda: True\n"
            "from moxi_comms.adapters import codex_wrapper\n"
            "raise SystemExit(codex_wrapper.main(['--name', 'coder', '--no-window']))\n")
    proc = subprocess.Popen([sys.executable, "-c", code], env=dict(os.environ),
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert wait_until(lambda: fake_host.out.exists()
                          and "started" in fake_host.out.read_text(), timeout=20), \
            proc.stderr.read() if proc.poll() is not None else "the host did not start"
        lines = dict(ln.split(" ", 1) for ln in fake_host.out.read_text().splitlines()
                     if " " in ln)
        token = lines["token"]
        assert len(token) >= 43 and lines["url"] == serving.mcp_url
        assert token not in lines["args"], "never in the host's argv"
        # The launch carries the tools, so its network is off and its shell can't reach
        # comms: the instructions say the tools are the way to send.
        assert "sandbox_workspace_write.network_access=false" in lines["args"]
        assert lines["reach"] == "off"
        assert fake_host.instructions.read_text().endswith(
            f"\n\n{listener.TOOLS_ONLY_LINE}")
        # The adapter registered the token's hash for its binding before it said bound.
        row = serving.mcp_tokens.verify(token)
        assert row is not None and row.identity == "coder" and row.bound
    finally:
        fake_host.release.touch()
        out, err = proc.communicate(timeout=20)
    assert proc.returncode == 0, err
    assert token not in out and token not in err
    assert f"  tools      {serving.mcp_url}" in out
    assert wait_until(lambda: "coder" not in serving.bindings, 15)


# ------------------------------------------------------------------- import cost

@pytest.mark.timed
def test_the_contributor_and_listener_import_light():
    """turns imports both at every launch and session start: under 10 ms together, and
    none of the endpoint's libraries."""
    mods = ("moxi_comms", "moxi_comms.mcp", "moxi_comms.mcp.additions",
            "moxi_comms.listener")
    code = ("import sys, moxi_comms.mcp.additions as a, moxi_comms.listener\n"
            "a.additions(type('R', (), {'env': {}})())\n"
            "print(sorted({n.split('.')[0] for n in sys.modules} & "
            "{'mcp', 'uvicorn', 'starlette'}))")
    done = subprocess.run([sys.executable, "-I", "-X", "importtime", "-c", code],
                          capture_output=True, text=True, timeout=120, env=dict(os.environ))
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "[]"
    total = 0
    for line in done.stderr.splitlines():
        parts = line.split("|")
        if len(parts) == 3 and parts[2].strip() in mods:
            total += int(parts[1])
    assert 0 < total < 10_000, total

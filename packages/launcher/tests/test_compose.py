"""The composers against golden argv: every row in every mode it composes into, the
opening prompt before every flag, the modes each harness launches in, the identity block
and system prompt file, and what a launch records."""

from __future__ import annotations

import json

import pytest

from moxi_launcher import catalog, compose
from moxi_launcher.compose import Additions, Context, claude, codex, common

SID = "5f0c3a1e-7f43-4c8e-9e0a-1d2b3c4d5e6f"
PF = "/state/runs/r1.prompt.md"
OF = "/state/runs/r1.options.json"
HOST = "/env/bin/moxi-turns-codex-host"
CLAUDE_HEAD = ["--session-id", SID, "--append-system-prompt-file", PF]
CLAUDE_TAIL = ["--permission-mode", "default"]
CODEX_HEAD = ["-c", "check_for_update_on_startup=false", "-C", "/work"]
HOST_HEAD = ["--cwd", "/work"]
UPDATE_OPTION = ["--option", "check_for_update_on_startup=false"]
BRIEF = "Brief from launcher, launched by ann\n\nfix the build"


def ctx(**fields) -> Context:
    base = {"request_id": "r1", "session_id": SID, "actor": "ann", "runs_dir": "/state/runs",
            "moxi": "moxi"}
    base.update(fields)
    return Context(**base)


def claude_values(**values):
    return {"harness": "claude", "cwd": "/work", **values}


def codex_values(**values):
    return {"harness": "codex", "cwd": "/work", **values}


# -- Claude ---------------------------------------------------------------------------------


CLAUDE_ROWS = [
    ({}, []),
    ({"agent": "reviewer"}, ["--agent", "reviewer"]),
    ({"model": "opus"}, ["--model", "opus"]),
    ({"effort": "high"}, ["--effort", "high"]),
    ({"tools": ["Bash", "Read"]}, ["--tools", "Bash,Read"]),
    ({"allowed_tools": ["Bash(git:*)", "Read"]},
     ["--allowedTools", "Bash(git:*)", "--allowedTools", "Read"]),
    ({"disallowed_tools": ["WebFetch"]}, ["--disallowedTools", "WebFetch"]),
    ({"mcp_config": ["/m/a.json", "/m/b.json"]},
     ["--mcp-config", "/m/a.json", "--mcp-config", "/m/b.json"]),
    ({"add_dir": ["/a", "/b"]}, ["--add-dir", "/a", "--add-dir", "/b"]),
    ({"native_name": "planner"}, ["-n", "planner"]),
]


@pytest.mark.parametrize("mode_values", [{}, {"target": "headless"}],
                         ids=["interactive", "print"])
@pytest.mark.parametrize("values, flags", CLAUDE_ROWS)
def test_each_claude_row_in_both_modes(values, flags, mode_values):
    print_ = bool(mode_values)
    got = claude.compose(claude_values(**values, **mode_values), ctx(brief="fix the build"))
    head = ["claude", "-p", BRIEF] if print_ else ["claude", BRIEF]
    assert list(got.argv) == head + CLAUDE_HEAD + flags + CLAUDE_TAIL
    assert got.mode == ("print" if print_ else "interactive")


@pytest.mark.parametrize("mode_name", ["default", "acceptEdits", "bypassPermissions", "plan"])
def test_the_claude_permission_mode_goes_last_before_the_extra_flags(mode_name):
    got = claude.compose(claude_values(permission_mode=mode_name, extra_flags=["--verbose"]),
                         ctx())
    assert list(got.argv[-3:]) == ["--permission-mode", mode_name, "--verbose"]


@pytest.mark.parametrize("value, tokens", [(True, ["--remote-control"]),
                                           ("phone", ["--remote-control=phone"])])
def test_remote_control_goes_before_the_permission_mode_with_no_bare_value(value, tokens):
    got = claude.compose(claude_values(remote_control=value), ctx())
    assert list(got.argv) == ["claude", *CLAUDE_HEAD, *tokens, *CLAUDE_TAIL]


def test_remote_control_by_default_is_left_out_of_print_and_chosen_is_refused():
    got = claude.compose(claude_values(target="headless", remote_control=True),
                         ctx(brief="b"))
    assert "--remote-control" not in got.argv and got.not_applied == ("remote_control",)
    with pytest.raises(catalog.NotTaken, match="remote_control is not taken by claude -p"):
        claude.compose(claude_values(target="headless", remote_control=True),
                       ctx(brief="b", chosen=frozenset({"remote_control"})))


def test_every_claude_row_composed_at_once_in_its_order():
    values = claude_values(
        agent="reviewer", model="opus", effort="high", tools=["Bash"],
        allowed_tools=["Read"], disallowed_tools=["WebFetch"], mcp_config=["/m.json"],
        add_dir=["/a"], native_name="lead", remote_control=True,
        permission_mode="acceptEdits", extra_flags=["--verbose", "--debug", "api"])
    additions = Additions(argv=("--plugin-dir", "/p/runner", "--plugin-dir", "/p/gate"),
                          env={"MOXI_TURNS_COMMANDS": "exit"})
    got = claude.compose(values, ctx(modules=frozenset({"turns"}), additions=additions,
                                     brief="b"))
    assert list(got.argv) == [
        "claude", "--session-id", SID, "--append-system-prompt-file", PF,
        "--agent", "reviewer", "--model", "opus", "--effort", "high", "--tools", "Bash",
        "--allowedTools", "Read", "--disallowedTools", "WebFetch",
        "--plugin-dir", "/p/runner", "--plugin-dir", "/p/gate", "--mcp-config", "/m.json",
        "--add-dir", "/a", "-n", "lead", "--remote-control",
        "--permission-mode", "acceptEdits", "--verbose", "--debug", "api"]
    # With turns the brief is the first turn, not the opening prompt.
    assert got.prompt is None and got.first == "Brief from launcher, launched by ann\n\nb"
    assert got.env == {"MOXI_TURNS_COMMANDS": "exit", "MOXI_LAUNCHER_REQUEST": "r1"}


def test_the_claude_order_covers_every_row_but_resume():
    names = {row.name for row in catalog.load("claude")}
    assert set(claude.ORDER) - {claude.ADDITIONS} == names - {"resume"}


def test_resume_is_refused():
    with pytest.raises(catalog.Refused, match="resume"):
        claude.compose(claude_values(resume=SID), ctx())


def test_a_passed_on_flag_that_spells_an_owned_row_is_refused():
    with pytest.raises(catalog.Owned):
        claude.compose(claude_values(extra_flags=["--session-id", SID]), ctx())


def test_claude_with_comms_runs_the_wrapper_with_the_claude_argv_after_the_separator():
    values = claude_values(identity="planner", scope="moxi", director=["lead", "ops"],
                           native_name="Planner", model="opus", add_dir=["/a"],
                           extra_flags=["--verbose"])
    additions = Additions(argv=("--plugin-dir", "/p/runner"))
    got = claude.compose(values, ctx(modules=frozenset({"comms"}), additions=additions,
                                     brief="b"))
    assert list(got.argv) == [
        "moxi", "comms", "claude", "--name", "planner", "--scope", "moxi",
        "--director", "lead", "--director", "ops", "--session-id", SID,
        "--native-name", "Planner", "--",
        "Brief from launcher, launched by ann\n\nb",
        "--append-system-prompt-file", PF, "--model", "opus", "--add-dir", "/a",
        "--permission-mode", "default", "--verbose"]
    # The wrapper asks turns for the additions itself, and sets -n.
    assert "/p/runner" not in got.argv and "-n" not in got.argv


def test_an_identity_without_comms_is_ignored_with_a_warning():
    got = claude.compose(claude_values(identity="planner"), ctx())
    assert got.argv[0] == "claude"
    assert got.warnings == ("identity planner is ignored: comms is not installed",)
    assert "identity:" not in got.prompt_file.text


def test_a_headless_claude_launch_with_an_identity_waits_on_comms():
    with pytest.raises(common.NotYet, match="headless wrapper flags"):
        claude.compose(claude_values(identity="planner", target="headless"),
                       ctx(modules=frozenset({"comms"}), brief="b"))


@pytest.mark.parametrize("target", ["terminal", "slot"])
def test_claude_is_interactive_in_this_terminal_and_a_slot(target):
    assert claude.compose(claude_values(target=target), ctx()).mode == "interactive"


def test_a_headless_launch_needs_a_brief():
    with pytest.raises(common.NoBrief):
        claude.compose(claude_values(target="headless"), ctx())
    with pytest.raises(common.NoBrief):
        codex.compose(codex_values(target="headless"), ctx(session_id=None))


def test_a_claude_launch_needs_its_session_id_and_an_absolute_directory():
    with pytest.raises(ValueError, match="session id"):
        claude.compose(claude_values(), ctx(session_id=None))
    with pytest.raises(ValueError, match="absolute"):
        claude.compose(claude_values(cwd="work"), ctx())


# -- Codex ----------------------------------------------------------------------------------


CODEX_ROWS = {
    "interactive": [
        ({}, ["-s", "workspace-write", "-a", "on-request"]),
        ({"model": "gpt-5"}, ["-m", "gpt-5", "-s", "workspace-write", "-a", "on-request"]),
        ({"sandbox": "read-only"}, ["-s", "read-only", "-a", "on-request"]),
        ({"approval": "never"}, ["-s", "workspace-write", "-a", "never"]),
        ({"profile": "fast"}, ["-s", "workspace-write", "-a", "on-request", "-p", "fast"]),
        ({"search": True}, ["-s", "workspace-write", "-a", "on-request", "--search"]),
        ({"add_dir": ["/a", "/b"]},
         ["-s", "workspace-write", "-a", "on-request", "--add-dir", "/a", "--add-dir", "/b"]),
        ({"config": ["model_reasoning_effort=high", "x.y=1"]},
         ["-s", "workspace-write", "-a", "on-request",
          "-c", "model_reasoning_effort=high", "-c", "x.y=1"]),
        ({"trust_cwd": True},
         ["-s", "workspace-write", "-a", "on-request",
          "-c", 'projects."/work".trust_level="trusted"']),
    ],
    "exec": [
        ({}, ["-s", "workspace-write"]),
        ({"model": "gpt-5"}, ["-m", "gpt-5", "-s", "workspace-write"]),
        ({"sandbox": "danger-full-access"}, ["-s", "danger-full-access"]),
        ({"scratch": True}, ["-s", "workspace-write", "--skip-git-repo-check"]),
        ({"profile": "fast"}, ["-s", "workspace-write", "-p", "fast"]),
        ({"add_dir": ["/a"]}, ["-s", "workspace-write", "--add-dir", "/a"]),
        ({"config": ["x.y=1"]}, ["-s", "workspace-write", "-c", "x.y=1"]),
        ({"trust_cwd": True},
         ["-s", "workspace-write", "-c", 'projects."/work".trust_level="trusted"']),
    ],
    "host": [
        ({}, ["--sandbox", "workspace-write", *UPDATE_OPTION]),
        ({"model": "gpt-5"}, ["--model", "gpt-5", "--sandbox", "workspace-write",
                              *UPDATE_OPTION]),
        ({"sandbox": "read-only"}, ["--sandbox", "read-only", *UPDATE_OPTION]),
        ({"add_dir": ["/a", "/b"]},
         ["--sandbox", "workspace-write", *UPDATE_OPTION, "--options-file", OF]),
        ({"config": ["x.y=1"]},
         ["--sandbox", "workspace-write", *UPDATE_OPTION, "--option", "x.y=1"]),
        ({"trust_cwd": True},
         ["--sandbox", "workspace-write", *UPDATE_OPTION, "--options-file", OF]),
    ],
}
CODEX_CASES = [(mode, values, flags) for mode, rows in CODEX_ROWS.items()
               for values, flags in rows]


@pytest.mark.parametrize("mode, values, flags", CODEX_CASES)
def test_each_codex_row_in_each_mode(mode, values, flags):
    links = frozenset({"turns"}) if mode == "host" else frozenset()
    target = "headless" if mode == "exec" else "terminal"
    got = codex.compose(codex_values(target=target, **values),
                        ctx(session_id=None, modules=links, host=HOST, brief="fix the build"))
    assert got.mode == mode
    if mode == "interactive":
        assert list(got.argv) == ["codex", BRIEF, *CODEX_HEAD, *flags]
    elif mode == "exec":
        assert list(got.argv) == ["codex", "exec", BRIEF, *CODEX_HEAD, *flags]
    else:
        assert list(got.argv) == [HOST, *HOST_HEAD, *flags]
        assert got.prompt is None and got.first == BRIEF


@pytest.mark.parametrize("row, value, mode", [
    ("approval", "never", "exec"), ("approval", "never", "host"),
    ("search", True, "exec"), ("search", True, "host"), ("profile", "fast", "host")])
def test_a_codex_row_its_mode_lacks_is_left_out_as_a_default_and_refused_when_chosen(
        row, value, mode):
    links = frozenset({"turns"}) if mode == "host" else frozenset()
    target = "headless" if mode == "exec" else "terminal"
    values = codex_values(target=target, **{row: value})
    context = ctx(session_id=None, modules=links, host=HOST, brief="b")
    got = codex.compose(values, context)
    assert row in got.not_applied and row in got.values["not_applied"]
    with pytest.raises(catalog.NotTaken, match=f"{row} is not taken by"):
        codex.compose(values, ctx(session_id=None, modules=links, host=HOST, brief="b",
                                  chosen=frozenset({row})))


def test_the_shipped_approval_default_never_reaches_exec_or_the_host():
    for target, links in (("headless", frozenset()), ("terminal", frozenset({"turns"}))):
        got = codex.compose(codex_values(target=target),
                            ctx(session_id=None, modules=links, host=HOST, brief="b"))
        assert "-a" not in got.argv and got.not_applied == ("approval",)


def test_the_codex_orders_cover_every_row():
    names = {row.name for row in catalog.load("codex")}
    assert set(codex.ORDER) == names
    assert set(codex.HOST_ORDER) == {row.name for row in catalog.load("codex")
                                     if "host" in row.modes}


def test_every_codex_row_composed_at_once_in_each_mode():
    values = codex_values(model="gpt-5", sandbox="read-only", approval="never",
                          profile="fast", search=True, add_dir=["/a"], config=["x=1"],
                          trust_cwd=True, extra_flags=["--image", "/i.png"])
    got = codex.compose(values, ctx(session_id=None, brief="b"))
    assert list(got.argv) == [
        "codex", "Brief from launcher, launched by ann\n\nb", *CODEX_HEAD,
        "-m", "gpt-5", "-s", "read-only", "-a", "never", "-p", "fast", "--search",
        "--add-dir", "/a", "-c", "x=1", "-c", 'projects."/work".trust_level="trusted"',
        "--image", "/i.png"]


@pytest.mark.parametrize("cwd", ["/work", "/home/me/my.repo", '/a"b.c'])
def test_under_the_host_trust_and_add_dir_go_in_the_options_file_as_nested_config(cwd):
    # The host's --option splits its key at each dot and keeps quotes, so the launch's
    # directory goes as one key of a JSON object, whatever it holds.
    got = codex.compose(codex_values(cwd=cwd, trust_cwd=True, add_dir=["/a", "/b.c"],
                                     config=["sandbox_workspace_write.network_access=true"]),
                        ctx(session_id=None, modules=frozenset({"turns"}), host=HOST))
    assert got.mode == "host" and got.options_file.path == OF
    assert list(got.argv) == [HOST, "--cwd", cwd, "--sandbox", "workspace-write",
                              *UPDATE_OPTION, "--option",
                              "sandbox_workspace_write.network_access=true",
                              "--options-file", OF]
    assert json.loads(got.options_file.text) == {"config": {
        "projects": {cwd: {"trust_level": "trusted"}},
        "sandbox_workspace_write": {"writable_roots": ["/a", "/b.c"]}}}
    assert not any("trust_level" in word or "writable_roots" in word for word in got.argv)


def test_without_trust_or_add_dir_the_host_gets_no_options_file():
    got = codex.compose(codex_values(config=["x=1"]),
                        ctx(session_id=None, modules=frozenset({"turns"}), host=HOST))
    assert got.options_file is None and "--options-file" not in got.argv
    for mode, links in (("interactive", frozenset()), ("exec", frozenset())):
        other = codex.compose(codex_values(trust_cwd=True, add_dir=["/a"],
                                           target="headless" if mode == "exec" else
                                           "terminal"),
                              ctx(session_id=None, modules=links, brief="b"))
        assert other.mode == mode and other.options_file is None


@pytest.mark.parametrize("values", [
    {"config": ["sandbox_mode=read-only"]},
    {"config": ["x=1", " sandbox_mode = \"danger-full-access\""]},
    {"extra_flags": ["--option", "sandbox_mode=read-only"]},
    {"extra_flags": ["--option=sandbox_mode=read-only"]},
])
def test_under_the_host_a_config_key_naming_the_sandbox_is_refused(values):
    with pytest.raises(catalog.PostureKey) as raised:
        codex.compose(codex_values(**values),
                      ctx(session_id=None, modules=frozenset({"turns"}), host=HOST))
    assert raised.value.row == "sandbox" and raised.value.code == "owned"
    assert raised.value.exit_code == 3 and "--sandbox" in raised.value.hint


def test_outside_the_host_a_config_key_naming_the_sandbox_is_codexs_own():
    got = codex.compose(codex_values(config=["sandbox_mode=read-only"]),
                        ctx(session_id=None, brief="b"))
    assert got.mode == "interactive" and "sandbox_mode=read-only" in got.argv
    # A key that only starts with it is another key.
    host = codex.compose(codex_values(config=["sandbox_mode_x=1"]),
                         ctx(session_id=None, modules=frozenset({"turns"}), host=HOST))
    assert "sandbox_mode_x=1" in host.argv


def test_a_codex_launch_under_the_host_passes_extra_flags_unchanged():
    got = codex.compose(codex_values(extra_flags=["--no-window", "--option", "x=1"]),
                        ctx(session_id=None, modules=frozenset({"turns"}), host=HOST))
    assert list(got.argv) == [HOST, *HOST_HEAD, "--sandbox", "workspace-write",
                              *UPDATE_OPTION, "--no-window", "--option", "x=1"]


@pytest.mark.parametrize("tail", [["--permissions", "dev"], ["--permissions=dev"]])
@pytest.mark.parametrize("links, host", [(frozenset({"turns"}), HOST), (frozenset(), None)])
def test_a_permissions_profile_passed_on_is_refused_like_the_sandbox_row(tail, links, host):
    # The host's --permissions sets the sandbox, as the posture row does; with no row of
    # its own the posture check can't see it, so it is refused in every mode.
    with pytest.raises(catalog.PostureFlag) as raised:
        codex.compose(codex_values(extra_flags=tail),
                      ctx(session_id=None, modules=links, host=host))
    assert raised.value.row == "sandbox" and raised.value.exit_code == 3
    assert "needs a catalog row" in raised.value.hint


def test_the_codex_host_with_comms_takes_the_hosts_flags_without_its_directory():
    values = codex_values(identity="coder", scope="moxi", director=["lead"],
                          model="gpt-5", add_dir=["/a"], extra_flags=["--no-window"],
                          sandbox="")
    got = codex.compose(values, ctx(session_id=None, modules=frozenset({"turns", "comms"}),
                                    host=HOST, comms_codex=True, brief="b"))
    assert list(got.argv) == [
        "moxi", "comms", "codex", "--name", "coder", "--scope", "moxi",
        "--director", "lead", "--model", "gpt-5", *UPDATE_OPTION,
        "--options-file", OF, "--no-window"]
    assert json.loads(got.options_file.text) == {
        "config": {"sandbox_workspace_write": {"writable_roots": ["/a"]}}}
    assert got.mode == "host" and got.first is not None


def test_a_codex_launch_with_an_identity_waits_on_comms():
    links = frozenset({"turns", "comms"})
    with pytest.raises(common.NotYet, match="Codex wrapper"):
        codex.compose(codex_values(identity="coder"),
                      ctx(session_id=None, modules=links, host=HOST))
    with pytest.raises(common.NotYet, match="headless wrapper flags"):
        codex.compose(codex_values(identity="coder", target="headless"),
                      ctx(session_id=None, modules=links, host=HOST, brief="b"))
    with pytest.raises(common.NotYet, match="turns' Codex host"):
        codex.compose(codex_values(identity="coder"),
                      ctx(session_id=None, modules=frozenset({"comms"}), comms_codex=True))


def test_codex_with_turns_but_no_host_runs_without_turns_and_says_so():
    got = codex.compose(codex_values(),
                        ctx(session_id=None, modules=frozenset({"turns"}), host=None,
                            brief="b"))
    assert got.mode == "interactive" and got.argv[0] == "codex"
    assert got.prompt is not None and got.first is None
    assert "turns' Codex host can't start here" in got.warnings[0]
    # Still a launch made with turns installed: the grant is stamped.
    assert got.env["MOXI_TURNS_COMMANDS"] == "exit"


def test_a_codex_seat_without_the_host_gets_its_posture_on_the_prompt_and_a_warning():
    got = codex.compose(codex_values(posture="go"),
                        ctx(session_id=None, brief="b", instructions="be careful"))
    assert got.prompt == "Brief from launcher, launched by ann\nstart posture: go\n\nb"
    assert any("instructions" in line for line in got.warnings)
    under_host = codex.compose(codex_values(posture="go"),
                               ctx(session_id=None, modules=frozenset({"turns"}), host=HOST,
                                   brief="b", instructions="be careful"))
    assert "start posture" not in under_host.first and not under_host.warnings


def test_a_codex_launch_has_no_system_prompt_file():
    assert codex.compose(codex_values(), ctx(session_id=None)).prompt_file is None


# -- the opening prompt ---------------------------------------------------------------------


PROMPT_CASES = [
    ("claude", {}, frozenset(), ["claude"]),
    ("claude", {"target": "headless"}, frozenset(), ["claude", "-p"]),
    ("claude", {"target": "headless", "add_dir": ["/a"], "tools": ["Bash"]},
     frozenset({"turns"}), ["claude", "-p"]),
    ("claude", {"identity": "p"}, frozenset({"comms"}),
     ["moxi", "comms", "claude", "--name", "p", "--session-id", SID, "--"]),
    ("codex", {}, frozenset(), ["codex"]),
    ("codex", {"target": "headless", "add_dir": ["/a"]}, frozenset({"turns"}),
     ["codex", "exec"]),
]


@pytest.mark.parametrize("harness, values, links, command", PROMPT_CASES)
def test_the_opening_prompt_comes_before_every_flag(harness, values, links, command):
    values = {"harness": harness, "cwd": "/work", "extra_flags": ["--x", "y"],
              "mcp_config": ["/m.json"] if harness == "claude" else [], **values}
    got = compose.compose(values, ctx(modules=links, brief="-p --not-a-flag",
                                      session_id=SID if harness == "claude" else None))
    assert list(got.argv[:len(command) + 1]) == [*command, got.prompt]
    assert got.prompt.startswith("Brief from launcher")
    assert got.prompt not in got.argv[len(command) + 1:]


def test_the_first_turn_text_is_the_first_turns_own_with_the_posture_second():
    from moxi_launcher import first

    assert common.first_turn("ann") is None
    for kwargs in ({"brief": "go"},
                   {"brief": "then this", "handoff": "/h/2026-10-30-lead.md",
                    "handoff_text": "state of play\n"}):
        assert common.first_turn("ann", **kwargs) == first.text(actor="ann", **kwargs)
    assert common.first_turn("ann", brief="go", posture="operator") == (
        "Brief from launcher, launched by ann\nstart posture: operator\n\ngo")


def test_a_handoff_alone_owes_a_first_turn():
    got = claude.compose(claude_values(handoff="/h/x.md"),
                         ctx(handoff_text="notes", modules=frozenset({"turns"})))
    assert got.first == "Handoff from launcher, launched by ann: /h/x.md\n\nnotes"


# -- the identity block and the system prompt file -----------------------------------------


def test_the_identity_block_shows_only_lines_with_values():
    assert common.identity_block(session_id=SID, cwd="/work", posture="") == (
        f"{common.HEADER}\nsession id:    {SID}\nproject cwd:   /work")
    full = common.identity_block(session_id=SID, cwd="/work", posture="with:lead",
                                 project_key="moxi", identity="planner", scope="moxi",
                                 handoff_dir="/h", agent_role="reviewer")
    assert full.splitlines()[1:] == [
        f"session id:    {SID}", "project cwd:   /work", "project key:   moxi",
        "identity:      planner@moxi", "handoff dir:   /h", "start posture: with:lead",
        "agent role:    reviewer"]


def test_the_claude_prompt_file_holds_the_block_then_the_instructions():
    got = claude.compose(
        claude_values(identity="planner", scope="moxi", posture="go", agent="reviewer"),
        ctx(modules=frozenset({"comms"}), project_key="moxi", handoff_dir="/h",
            role_found=False, instructions="\nYou plan.\n"))
    assert got.prompt_file.path == PF
    assert got.prompt_file.text == "\n".join([
        common.HEADER, f"session id:    {SID}", "project cwd:   /work",
        "project key:   moxi", "identity:      planner@moxi", "handoff dir:   /h",
        "start posture: go", "agent role:    reviewer, requested, did not resolve", "",
        "You plan."])
    # Neither the block nor the instructions is on the argv.
    assert not any("You plan." in word or common.HEADER in word for word in got.argv)


def test_the_codex_listener_renders_the_same_block():
    from moxi_launcher import listen_codex

    assert listen_codex.identity_block is common.identity_block
    assert listen_codex.HEADER == common.HEADER


def test_write_prompt_is_private(tmp_path):
    import os
    import stat

    target = tmp_path / "runs" / "r1.prompt.md"
    common.write_prompt(common.PromptFile(str(target), "block"))
    assert target.read_text() == "block"
    if os.name != "nt":
        assert stat.S_IMODE(target.stat().st_mode) == 0o600
        assert stat.S_IMODE(target.parent.stat().st_mode) == 0o700


# -- what a launch records -----------------------------------------------------------------


def test_the_recorded_values_carry_the_mode_and_what_was_not_applied():
    got = codex.compose(codex_values(target="headless", off_posture=True),
                        ctx(session_id=None, brief="b"))
    assert got.values["mode"] == "exec" and got.values["not_applied"] == ["approval"]
    assert got.values["off_posture"] is True and got.values["sandbox"] == "workspace-write"
    # Rows the launcher owns are not values a user set.
    assert "update_check" not in got.values and "skip_git_check" not in got.values
    host = codex.compose(codex_values(),
                         ctx(session_id=None, modules=frozenset({"turns"}), host=HOST))
    assert host.values["mode"] == "host"
    assert claude.compose(claude_values(), ctx()).values["mode"] == "interactive"
    assert "session_id" not in claude.compose(claude_values(), ctx()).values


def test_the_dispatcher_picks_the_harness():
    assert compose.compose(claude_values(), ctx()).harness == "claude"
    assert compose.compose(codex_values(), ctx(session_id=None)).harness == "codex"
    with pytest.raises(ValueError):
        compose.compose({"harness": "opencode", "cwd": "/work"}, ctx())

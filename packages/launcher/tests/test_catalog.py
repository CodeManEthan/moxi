"""The flag catalogs: the shipped files load and hold the rows, each mode spells its rows,
the modes rule, the generated parser, pass-through refusals and the `--help` diff."""

from __future__ import annotations

import tomllib

import pytest

from moxi.config import Key

from moxi_launcher import catalog
from moxi_launcher.catalog import (BadValue, CatalogError, NeedsModule, NotTaken, Owned,
                                   Spelling)


def everything(module: str) -> bool:
    return True


def nothing(module: str) -> bool:
    return False


# -- the shipped catalogs --------------------------------------------------------------

CLAUDE_ROWS = {"model", "effort", "agent", "tools", "allowed_tools", "disallowed_tools",
               "permission_mode", "add_dir", "remote_control", "native_name",
               "mcp_config", "session_id", "append_system_prompt_file", "plugin_dir",
               "resume"}
CODEX_ROWS = {"model", "sandbox", "approval", "profile", "search", "add_dir", "config",
              "cwd", "update_check", "skip_git_check", "trust_cwd"}
LAUNCHER_ROWS = {"harness", "cwd", "project", "scratch", "identity", "scope", "director",
                 "instructions", "instructions_file", "brief", "brief_file", "handoff",
                 "posture", "target", "slot", "extra_flags", "notes_dir", "warn_at"}


def test_each_shipped_catalog_holds_exactly_its_rows():
    assert set(catalog.load("claude").rows) == CLAUDE_ROWS
    assert set(catalog.load("codex").rows) == CODEX_ROWS
    assert set(catalog.launcher().rows) == LAUNCHER_ROWS


def test_an_unknown_harness_has_no_catalog():
    with pytest.raises(ValueError, match="opencode"):
        catalog.load("opencode")


def test_every_harness_row_composes_only_in_its_harness_modes():
    for harness, modes in catalog.MODES.items():
        for row in catalog.load(harness):
            assert row.harness == harness
            assert row.modes and set(row.modes) <= set(modes), row.name


def test_launcher_rows_have_no_flag_and_no_modes():
    for row in catalog.launcher():
        assert row.harness is None and row.flag is None and not row.modes


@pytest.mark.parametrize(("harness", "name", "default"), [
    ("claude", "permission_mode", "default"),
    ("claude", "allowed_tools", []),
    ("claude", "disallowed_tools", []),
    ("codex", "sandbox", "workspace-write"),
    ("codex", "approval", "on-request"),
])
def test_the_posture_rows_and_their_shipped_defaults(harness, name, default):
    row = catalog.load(harness)[name]
    assert row.posture and row.default == default


def test_only_the_posture_rows_are_posture():
    posture = {(r.harness, r.name) for h in catalog.HARNESSES for r in catalog.load(h)
               if r.posture}
    assert posture == {("claude", "permission_mode"), ("claude", "allowed_tools"),
                       ("claude", "disallowed_tools"), ("codex", "sandbox"),
                       ("codex", "approval")}


def test_the_owned_rows():
    owned = {(r.harness, r.name) for h in catalog.HARNESSES for r in catalog.load(h)
             if r.owned}
    assert owned == {("claude", "session_id"), ("claude", "append_system_prompt_file"),
                     ("claude", "plugin_dir"), ("claude", "resume"), ("codex", "cwd"),
                     ("codex", "update_check"), ("codex", "skip_git_check")}


def test_remote_control_is_interactive_only_and_every_other_claude_row_prints_too():
    for row in catalog.load("claude"):
        expected = {"interactive"} if row.name == "remote_control" else {"interactive",
                                                                           "print"}
        assert set(row.modes) == expected, row.name


def test_the_appended_prompt_file_is_hidden():
    assert catalog.load("claude")["append_system_prompt_file"].hidden


@pytest.mark.parametrize(("name", "requires"), [("director", "comms"), ("slot", "slots"),
                                                ("notes_dir", "notes"),
                                                ("warn_at", "usage")])
def test_the_launcher_rows_that_need_a_module(name, requires):
    assert catalog.launcher()[name].requires == requires


def test_a_row_reads_as_a_config_key():
    key = catalog.load("claude")["permission_mode"].key
    assert isinstance(key, Key)
    assert key.type == "enum" and key.default == "default" and key.read == "use"
    assert "bypassPermissions" in key.values
    assert catalog.launcher()["slot"].key.needs == "slots"


# -- spellings per mode ---------------------------------------------------------------

# Codex, as the design's table spells each row in each mode (the host's sandbox as
# turns' host grammar takes it). None: the mode does not take the row.
CODEX_SPELLINGS = {
    "model": ("-m", "-m", "--model"),
    "sandbox": ("-s", "-s", "--sandbox"),
    "approval": ("-a", None, None),
    "profile": ("-p", "-p", None),
    "search": ("--search", None, None),
    "add_dir": ("--add-dir", "--add-dir", "--option"),
    "config": ("-c", "-c", "--option"),
    "cwd": ("-C", "-C", "--cwd"),
    "update_check": ("-c", "-c", "--option"),
    "skip_git_check": (None, "--skip-git-repo-check", None),
    "trust_cwd": ("-c", "-c", "--option"),
}


@pytest.mark.parametrize("name", sorted(CODEX_SPELLINGS))
def test_each_codex_row_is_spelled_per_mode(name):
    row = catalog.load("codex")[name]
    for mode, flag in zip(("interactive", "exec", "host"), CODEX_SPELLINGS[name]):
        spelled = row.modes.get(mode)
        assert (spelled.flag if spelled else None) == flag, mode


TOKENS = [
    ("codex", "model", "interactive", "o3", ["-m", "o3"]),
    ("codex", "model", "host", "o3", ["--model", "o3"]),
    ("codex", "sandbox", "host", "read-only", ["--sandbox", "read-only"]),
    ("codex", "add_dir", "exec", ["/a", "/b"], ["--add-dir", "/a", "--add-dir", "/b"]),
    ("codex", "add_dir", "host", ["/a", "/b"],
     ["--option", 'sandbox_workspace_write.writable_roots=["/a", "/b"]']),
    ("codex", "config", "interactive", ["a=1", "b=2"], ["-c", "a=1", "-c", "b=2"]),
    ("codex", "config", "host", ["a=1"], ["--option", "a=1"]),
    ("codex", "update_check", "interactive", True,
     ["-c", "check_for_update_on_startup=false"]),
    ("codex", "update_check", "host", True,
     ["--option", "check_for_update_on_startup=false"]),
    ("codex", "skip_git_check", "exec", True, ["--skip-git-repo-check"]),
    ("codex", "skip_git_check", "exec", False, []),
    ("codex", "search", "interactive", True, ["--search"]),
    ("claude", "tools", "print", ["Bash", "Edit"], ["--tools", "Bash,Edit"]),
    ("claude", "allowed_tools", "interactive", ["Bash(git *)", "Edit"],
     ["--allowedTools", "Bash(git *)", "--allowedTools", "Edit"]),
    ("claude", "add_dir", "interactive", ["/x"], ["--add-dir", "/x"]),
    ("claude", "remote_control", "interactive", True, ["--remote-control"]),
    ("claude", "remote_control", "interactive", "laptop", ["--remote-control=laptop"]),
    ("claude", "remote_control", "interactive", False, []),
    ("claude", "native_name", "print", "planner", ["-n", "planner"]),
    ("claude", "permission_mode", "interactive", "plan", ["--permission-mode", "plan"]),
    ("claude", "model", "interactive", "", []),
]


@pytest.mark.parametrize(("harness", "name", "mode", "value", "tokens"), TOKENS,
                         ids=[f"{h}-{n}-{m}-{v}" for h, n, m, v, _ in TOKENS])
def test_a_row_spells_its_value_in_a_mode(harness, name, mode, value, tokens):
    assert catalog.load(harness)[name].tokens(mode, value) == tokens


def test_the_trust_override_names_the_launch_directory_as_a_toml_key():
    # codex -c splits its key at every dot, so the directory goes as the key of an
    # inline table, the value of the one-part key `projects`.
    row = catalog.load("codex")["trust_cwd"]
    assert row.tokens("interactive", True, cwd="/home/me/my.repo") == [
        "-c", 'projects={"/home/me/my.repo"={trust_level="trusted"}}']
    assert row.tokens("host", True, cwd='/a"b') == [
        "--option", 'projects."/a\\"b".trust_level="trusted"']
    assert row.tokens("exec", True, cwd="/{value}") == [
        "-c", 'projects={"/{value}"={trust_level="trusted"}}']
    word = row.tokens("exec", True, cwd='/a"b.c d')[1]
    key, _, value = word.partition("=")
    assert key == "projects"
    assert tomllib.loads(f"v = {value}")["v"] == {'/a"b.c d': {"trust_level": "trusted"}}
    # TOML wants DEL escaped in a string, which a JSON string leaves bare.
    word = row.tokens("host", True, cwd="/a\x7fb.c")[1]
    assert tomllib.loads(word)["projects"] == {"/a\x7fb.c": {"trust_level": "trusted"}}
    with pytest.raises(ValueError, match="directory"):
        row.tokens("exec", True)


def test_a_mode_that_does_not_take_the_row_has_no_tokens():
    with pytest.raises(KeyError):
        catalog.load("codex")["approval"].tokens("exec", "never")


def test_an_optional_value_never_leaves_a_bare_token_after_its_flag():
    row = catalog.load("claude")["remote_control"]
    for value in (True, "laptop"):
        tokens = row.tokens("interactive", value)
        assert len(tokens) == 1 and tokens[0].startswith("--remote-control")


# -- values ---------------------------------------------------------------------------

def test_check_types_and_enum_values():
    claude = catalog.load("claude")
    assert claude["permission_mode"].check("plan") == "plan"
    with pytest.raises(BadValue, match="cannot be 'yolo'") as raised:
        claude["permission_mode"].check("yolo")
    assert "bypassPermissions" in raised.value.hint
    with pytest.raises(BadValue, match="not a list"):
        claude["tools"].check("Bash")
    with pytest.raises(BadValue, match="item 2"):
        claude["tools"].check(["Bash", 3])
    assert claude["remote_control"].check(True) is True
    assert claude["remote_control"].check("phone") == "phone"
    with pytest.raises(BadValue, match="or true"):
        claude["remote_control"].check(3)


def test_a_string_rows_values_are_suggestions():
    assert catalog.load("claude")["model"].check("claude-opus-9") == "claude-opus-9"


def test_a_path_expands_home_and_must_be_absolute(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    row = catalog.launcher()["brief_file"]
    assert row.check("~/brief.md") == str(tmp_path / "brief.md")
    assert row.check("") is None
    with pytest.raises(BadValue, match="relative path"):
        row.check("brief.md")
    assert catalog.load("codex")["add_dir"].check(["~/x"]) == [str(tmp_path / "x")]


def test_is_set():
    assert not any(map(catalog.is_set, (None, "", False, [], {})))
    assert all(map(catalog.is_set, ("x", True, 0, ["a"])))


# -- the modes rule ---------------------------------------------------------------------

def test_a_default_the_mode_cannot_carry_is_left_out_and_named():
    values = {"sandbox": "workspace-write", "approval": "on-request", "model": "o3"}
    fit = catalog.fit("codex", "exec", values)
    assert dict(fit.applied) == {"sandbox": "workspace-write", "model": "o3"}
    assert fit.not_applied == ("approval",)


def test_the_shipped_approval_default_never_reaches_exec_or_the_host():
    values = {"sandbox": "workspace-write", "approval": "on-request"}
    for mode in ("exec", "host"):
        fit = catalog.fit("codex", mode, values)
        assert "approval" not in fit.applied and fit.not_applied == ("approval",)
    assert catalog.fit("codex", "interactive", values).applied["approval"] == "on-request"


@pytest.mark.parametrize(("harness", "mode", "name", "value", "said"), [
    ("codex", "exec", "approval", "never", "approval is not taken by codex exec"),
    ("codex", "host", "approval", "never", "approval is not taken by turns' Codex host"),
    ("codex", "host", "profile", "work", "profile is not taken by turns' Codex host"),
    ("codex", "exec", "search", True, "search is not taken by codex exec"),
    ("claude", "print", "remote_control", True, "remote_control is not taken by claude -p"),
])
def test_a_chosen_value_the_mode_cannot_carry_is_refused(harness, mode, name, value, said):
    with pytest.raises(NotTaken) as raised:
        catalog.fit(harness, mode, {name: value}, chosen={name})
    assert str(raised.value.problem) == said
    assert raised.value.row == name and raised.value.mode == mode
    assert raised.value.code == "not-taken" and raised.value.exit_code == 3


def test_an_unset_row_is_neither_applied_nor_refused():
    fit = catalog.fit("codex", "exec", {"search": False, "profile": ""},
                      chosen={"search", "profile"})
    assert dict(fit.applied) == {} and fit.not_applied == ()


def test_launcher_rows_are_left_to_the_caller_but_codex_cwd_takes_the_launch_directory():
    fit = catalog.fit("codex", "host", {"cwd": "/work", "brief": "go", "target": "slot"},
                      chosen={"cwd", "brief", "target"})
    assert dict(fit.applied) == {"cwd": "/work"}


def test_fit_refuses_a_mode_the_harness_lacks():
    with pytest.raises(ValueError):
        catalog.fit("claude", "exec", {})


# -- the parser -------------------------------------------------------------------------

def test_the_parser_reads_typed_rows_and_leaves_the_rest_none():
    parsed = catalog.parse(["--model", "opus", "--permission-mode", "plan", "--scratch",
                            "--target", "headless"], "claude", available=everything)
    assert dict(parsed.values) == {"model": "opus", "permission_mode": "plan",
                                   "scratch": True, "target": "headless"}
    assert parsed.unknown == () and parsed.extra == () and parsed.warning is None


def test_a_bool_row_can_be_turned_off_over_a_preset():
    parsed = catalog.parse(["--no-trust-cwd"], "codex", available=everything)
    assert dict(parsed.values) == {"trust_cwd": False}


def test_repeatable_and_variadic_rows_collect_their_items():
    parsed = catalog.parse(["--director", "a", "--director", "b", "--tools", "Bash",
                            "Edit", "--tools", "Read"], "claude", available=everything)
    assert parsed.values["director"] == ["a", "b"]
    assert parsed.values["tools"] == ["Bash", "Edit", "Read"]


def test_an_optional_value_row_takes_a_value_or_none():
    bare = catalog.parse(["--remote-control"], "claude", available=everything)
    named = catalog.parse(["--remote-control", "phone"], "claude", available=everything)
    assert bare.values["remote_control"] is True
    assert named.values["remote_control"] == "phone"


def test_a_typed_relative_path_is_read_against_this_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    parsed = catalog.parse(["--cwd", "sub", "--add-dir", "../x"], "codex",
                           available=everything)
    assert parsed.values["cwd"] == str(tmp_path / "sub")
    assert parsed.values["add_dir"] == [str(tmp_path.parent / "x")]


def test_an_unknown_flag_passes_with_one_warning_and_goes_last():
    parsed = catalog.parse(["--fallback-model", "sonnet", "--model", "opus", "--verbose",
                            "--", "--debug", "hooks"], "claude", available=everything)
    assert dict(parsed.values) == {"model": "opus"}
    assert parsed.unknown == ("--fallback-model", "sonnet", "--verbose")
    assert parsed.extra == ("--fallback-model", "sonnet", "--verbose", "--debug", "hooks")
    assert parsed.warning.count("\n") == 0
    assert parsed.warning.endswith("--fallback-model sonnet --verbose")


def test_nothing_is_abbreviated():
    parsed = catalog.parse(["--mod", "opus"], "claude", available=everything)
    assert parsed.values == {} and parsed.unknown == ("--mod", "opus")


def test_a_bad_typed_value_is_refused_not_exited():
    with pytest.raises(BadValue, match="invalid choice") as raised:
        catalog.parse(["--sandbox", "wide-open"], "codex", available=everything)
    assert raised.value.exit_code == 2


def test_another_harnesss_rows_are_not_offered():
    parsed = catalog.parse(["--sandbox", "read-only"], "claude", available=everything)
    assert parsed.values == {} and parsed.unknown == ("--sandbox", "read-only")


def test_owned_rows_are_never_offered():
    parser = catalog.build_parser("claude", available=everything)
    options = {opt for action in parser._actions for opt in action.option_strings}
    assert "--session-id" not in options and "--resume" not in options
    assert "--extra-flags" not in options
    assert {"--model", "--harness", "--brief-file", "--no-scratch"} <= options


def test_a_row_whose_module_is_absent_is_not_offered():
    with_slots = catalog.parse(["--slot", "3"], "claude", available=everything)
    assert with_slots.values["slot"] == "3"
    parser = catalog.build_parser("claude", available=nothing)
    options = {opt for action in parser._actions for opt in action.option_strings}
    assert not {"--slot", "--director", "--notes-dir", "--warn-at"} & options
    with pytest.raises(NeedsModule, match="slot needs slots") as raised:
        catalog.parse(["--slot", "3"], "claude", available=nothing)
    assert "moxi-slots" in raised.value.hint


def test_offered_is_the_launcher_rows_then_the_harnesss_settable_ones():
    rows = catalog.offered("codex", available=nothing)
    assert "slot" not in rows and "update_check" not in rows and "extra_flags" in rows
    assert rows["cwd"].harness is None
    assert {"sandbox", "approval", "harness", "brief"} <= set(rows)


def test_row_finds_the_launcher_row_first():
    assert catalog.row("codex", "cwd").harness is None
    assert catalog.row("codex", "sandbox").harness == "codex"
    assert catalog.row("claude", "sandbox") is None


def test_typed_harness_reads_only_before_the_tail():
    assert catalog.typed_harness(["--model", "o3", "--harness", "codex"]) == "codex"
    assert catalog.typed_harness(["--model", "o3"]) is None
    assert catalog.typed_harness(["--", "--harness", "codex"]) is None


# -- pass-through refusals ----------------------------------------------------------------

@pytest.mark.parametrize(("harness", "tail", "row"), [
    ("claude", ["--session-id", "x"], "session_id"),
    ("claude", ["--resume", "x"], "resume"),
    ("claude", ["-r"], "resume"),
    ("claude", ["--append-system-prompt-file=/f"], "append_system_prompt_file"),
    ("claude", ["--plugin-dir", "/p"], "plugin_dir"),
    ("claude", ["--permission-mode", "bypassPermissions"], "permission_mode"),
    ("claude", ["--allowed-tools", "Bash"], "allowed_tools"),
    ("codex", ["-C", "/x"], "cwd"),
    ("codex", ["--cd=/x"], "cwd"),
    ("codex", ["--cwd", "/x"], "cwd"),
    ("codex", ["-c", "check_for_update_on_startup=true"], "update_check"),
    ("codex", ["-ccheck_for_update_on_startup=true"], "update_check"),
    ("codex", ["--skip-git-repo-check"], "skip_git_check"),
    ("codex", ["--sandbox", "danger-full-access"], "sandbox"),
    ("codex", ["-a", "never"], "approval"),
])
def test_a_pass_through_flag_spelling_an_owned_or_posture_row_is_refused(harness, tail,
                                                                         row):
    with pytest.raises(Owned) as raised:
        catalog.parse(["--", *tail], harness, available=everything)
    assert raised.value.row == row and raised.value.exit_code == 3


def test_an_owned_refusal_names_the_row_and_a_posture_one_names_its_option():
    with pytest.raises(Owned) as owned:
        catalog.refuse_passthrough("claude", ["--session-id", "x"])
    assert "session_id" in str(owned.value) and "launcher sets itself" in str(owned.value)
    with pytest.raises(Owned) as posture:
        catalog.refuse_passthrough("codex", ["-s", "read-only"])
    assert "--sandbox" in posture.value.hint


@pytest.mark.parametrize("argv", [["--resume", "thread-1"], ["--", "--resume=thread-1"],
                                  ["--no-window"], ["--", "--options-file", "/o.json"],
                                  ["--", "--instructions-file", "/i.md"]])
def test_a_host_flag_no_launch_passes_on_is_refused(argv):
    with pytest.raises(catalog.HeldFlag) as raised:
        catalog.parse(argv, "codex", available=everything)
    token = next(t for t in argv if t != "--").partition("=")[0]
    assert raised.value.token == token and raised.value.code == "owned"
    assert raised.value.exit_code == 3
    assert raised.value.hint == catalog.HELD_FLAGS["codex"][token]


def test_the_launchers_own_instructions_file_is_still_taken_before_the_dashes():
    got = catalog.parse(["--instructions-file", "/i.md"], "codex", available=everything)
    assert got.values["instructions_file"] == "/i.md"


def test_refusing_the_git_check_skip_says_how_to_run_outside_a_git_repository():
    with pytest.raises(Owned) as raised:
        catalog.refuse_passthrough("codex", ["--skip-git-repo-check"], "exec")
    assert raised.value.row == "skip_git_check"
    assert "--scratch" in raised.value.hint and "git repository" in raised.value.hint
    # Every row a hint is kept for is an owned row of its harness.
    assert all(catalog.row(h, name).owned for h, name in catalog.OWNED_HINTS)


@pytest.mark.parametrize("argv", [["--", "--permissions", "dev"], ["--permissions=dev"]])
def test_a_flag_without_a_row_that_sets_a_posture_rows_value_is_refused(argv):
    with pytest.raises(catalog.PostureFlag) as raised:
        catalog.parse(argv, "codex", available=everything)
    assert raised.value.row == "sandbox" and raised.value.exit_code == 3
    assert raised.value.token == "--permissions" and raised.value.code == "owned"
    assert "set sandbox with --sandbox" in raised.value.hint
    # Every flag, key and setting it names stands in for a posture row of its harness.
    for harness, flags in catalog.POSTURE_FLAGS.items():
        assert all(catalog.row(harness, row).posture for row in flags.values())
    for harness, (_, keys) in catalog.POSTURE_KEYS.items():
        assert all(catalog.row(harness, row).posture for row in keys.values())
    for harness, entries in catalog.POSTURE_SETTINGS.items():
        assert all(catalog.row(harness, row).posture for row in entries.values())


@pytest.mark.parametrize("harness, argv, row", [
    ("claude", ["--", "--dangerously-skip-permissions"], "permission_mode"),
    ("claude", ["--allow-dangerously-skip-permissions"], "permission_mode"),
    ("codex", ["--", "--dangerously-bypass-approvals-and-sandbox"], "sandbox"),
    ("codex", ["--approve-for-me"], "approval"),
    # Hidden aliases: Codex's --help never shows them.
    ("codex", ["--yolo"], "sandbox"),
    ("codex", ["--", "--yolo"], "sandbox"),
    ("codex", ["--not-so-yolo"], "approval"),
])
def test_a_flag_that_skips_or_hands_off_the_permission_checks_is_refused(harness, argv,
                                                                         row):
    with pytest.raises(catalog.PostureFlag) as raised:
        catalog.parse(argv, harness, available=everything)
    assert raised.value.row == row and raised.value.exit_code == 3
    assert raised.value.token == argv[-1]
    assert f"with --{row.replace('_', '-')} instead" in raised.value.hint


def test_with_the_mode_not_known_the_hint_names_where_the_row_is_taken():
    # Only plain codex takes approval, so the hint says where its option applies.
    with pytest.raises(catalog.PostureKey) as raised:
        catalog.parse(["--", "-c", "approval_policy=never"], "codex", available=everything)
    assert raised.value.hint == ("leave approval_policy out; where approval is taken "
                                 "(codex), set it with --approval instead, so the posture "
                                 "check sees it")
    with pytest.raises(catalog.PostureKey) as raised:
        catalog.parse(["--", "-c", "sandbox_mode=read-only"], "codex", available=everything)
    assert raised.value.hint == ("leave sandbox_mode out, and set sandbox with --sandbox "
                                 "instead, so the posture check sees it")


@pytest.mark.parametrize("argv, key, row", [
    (["--", "-c", "sandbox_mode=danger-full-access"], "sandbox_mode", "sandbox"),
    (["--", "--config=approval_policy=never"], "approval_policy", "approval"),
    (["--", "--option", "sandbox_mode=read-only"], "sandbox_mode", "sandbox"),
])
def test_a_codex_config_override_passed_on_that_sets_a_posture_row_is_refused(argv, key,
                                                                               row):
    with pytest.raises(catalog.PostureKey) as raised:
        catalog.parse(argv, "codex", available=everything)
    assert (raised.value.row, raised.value.token) == (row, key)
    assert raised.value.exit_code == 3 and f"--{row}" in raised.value.hint
    # Another key passes, and so does a value that only names one.
    catalog.parse(["--", "-c", "model_reasoning_effort=high", "-c",
                   "notify=sandbox_mode"], "codex", available=everything)


@pytest.mark.parametrize("settings, entry, row", [
    ('{"permissions": {"defaultMode": "bypassPermissions"}}', "defaultMode",
     "permission_mode"),
    ('{"permissions": {"allow": ["Bash"]}}', "allow", "allowed_tools"),
    ('{"permissions": {"deny": ["Bash"]}}', "deny", "disallowed_tools"),
])
def test_claude_settings_json_that_sets_a_posture_row_is_refused(settings, entry, row):
    for argv in (["--", "--settings", settings], [f"--settings={settings}"]):
        with pytest.raises(catalog.PostureKey) as raised:
            catalog.parse(argv, "claude", available=everything)
        assert (raised.value.row, raised.value.token) == (row, f"permissions.{entry}")


@pytest.mark.parametrize("settings", [
    '{"env": {"A": "1"}}', '{"permissions": {"additionalDirectories": ["/a"]}}',
    "/home/someone/settings.json", "[1]", '{"permissions": "x"}'])
def test_claude_settings_without_a_posture_entry_pass(settings):
    parsed = catalog.parse(["--", "--settings", settings], "claude", available=everything)
    assert parsed.extra == ("--settings", settings)


def test_an_unknown_flag_before_the_tail_is_held_to_the_same_refusal():
    with pytest.raises(Owned):
        catalog.parse(["--session-id", "x"], "claude", available=everything)


@pytest.mark.parametrize(("harness", "tail"), [
    ("codex", ["-c", "model_reasoning_effort=high"]),
    ("codex", ["-c", 'projects."/x".trust_level="trusted"']),
    ("codex", ["--option", "x=1"]),
    ("claude", ["--verbose", "--model", "opus"]),
    ("claude", ["-c"]),
])
def test_other_pass_through_flags_pass(harness, tail):
    parsed = catalog.parse(["--", *tail], harness, available=everything)
    assert parsed.extra == tuple(tail)


# -- the catalog format --------------------------------------------------------------------

META = """
[catalog]
harness = "codex"
checked = "1.0.0"
[catalog.help.codex]
command = ["codex", "--help"]
modes = ["interactive"]
[catalog.help.exec]
command = ["codex", "exec", "--help"]
modes = ["exec"]
"""

GOOD = """
[model]
type = "string"
default = ""
doc = "The model."
flag = "-m"
modes = ["interactive", "exec"]
"""


def parse_codex(text: str):
    return catalog.parse_text(text + META, harness="codex", source="test.toml")


def test_a_well_formed_catalog_parses():
    parsed = parse_codex(GOOD)
    assert parsed.checked == "1.0.0"
    assert parsed["model"].modes["exec"] == Spelling("-m")
    assert [h.name for h in parsed.helps] == ["codex", "exec"]


BAD_ROWS = [
    ('[x]\ntype = "string"\ndoc = "d"\nflag = "-x"\nmodes = ["exec"]\n', "lacks default"),
    ('[x]\ntype = "str"\ndefault = ""\ndoc = "d"\nflag = "-x"\nmodes = ["exec"]\n',
     "has type 'str'"),
    ('[x]\ntype = "string"\ndefault = ""\ndoc = "d"\nflag = "-x"\nmodes = ["exec"]\n'
     'color = "red"\n', "unknown field"),
    ('[x]\ntype = "string"\ndefault = ""\ndoc = "d"\nflag = "-x"\nmodes = ["print"]\n',
     "names mode 'print'"),
    ('[x]\ntype = "string"\ndefault = ""\ndoc = "d"\nmodes = ["exec"]\n', "no flag"),
    ('[x]\ntype = "string"\ndefault = ""\ndoc = "d"\nflag = "-x"\n', "has no modes"),
    ('[x]\ntype = "string"\ndefault = ""\ndoc = "d"\nflag = "x"\nmodes = ["exec"]\n',
     "not a flag"),
    ('[x]\ntype = "enum"\ndefault = "a"\ndoc = "d"\nflag = "-x"\nmodes = ["exec"]\n',
     "enum without values"),
    ('[x]\ntype = "enum"\ndefault = "c"\nvalues = ["a"]\ndoc = "d"\nflag = "-x"\n'
     'modes = ["exec"]\n', "default that does not fit"),
    ('[x]\ntype = "int"\ndefault = "1"\ndoc = "d"\nflag = "-x"\nmodes = ["exec"]\n',
     "default that does not fit"),
    ('[x]\ntype = "bool"\ndefault = false\nvalues = ["a"]\ndoc = "d"\nflag = "-x"\n'
     'modes = ["exec"]\n', "has values"),
    ('[x]\ntype = "string"\ndefault = ""\nrepeatable = true\ndoc = "d"\nflag = "-x"\n'
     'modes = ["exec"]\n', "not a list"),
    ('[x]\ntype = "string"\ndefault = ""\noptional_value = true\ndoc = "d"\nflag = "-x"\n'
     'modes = ["exec"]\n', "optional value"),
    ('[x]\ntype = "string"\ndefault = ""\nowned = true\nposture = true\ndoc = "d"\n'
     'flag = "-x"\nmodes = ["exec"]\n', "both owned and a posture row"),
    ('[x]\ntype = "string"\ndefault = ""\nrequires = "No Such"\ndoc = "d"\nflag = "-x"\n'
     'modes = ["exec"]\n', "not a module name"),
    ('[x]\ntype = "string"\ndefault = ""\nenv = "CODEX_MODEL"\ndoc = "d"\nflag = "-x"\n'
     'modes = ["exec"]\n', "MOXI_LAUNCHER_"),
    ('[x]\ntype = "string"\ndefault = ""\ndoc = "d"\nflag = "-x"\n'
     'modes = { exec = { flag = "-x", how = "y" } }\n', "spells mode exec"),
    ('[x]\ntype = "string"\ndefault = ""\ndoc = "two\\nlines"\nflag = "-x"\n'
     'modes = ["exec"]\n', "one-line doc"),
    ('[x]\ntype = "string"\ndefault = ""\nsince = "new"\ndoc = "d"\nflag = "-x"\n'
     'modes = ["exec"]\n', "not a version"),
    ('[Bad]\ntype = "string"\ndefault = ""\ndoc = "d"\nflag = "-x"\nmodes = ["exec"]\n',
     "not a row name"),
    ('x = 1\n', "not a row's table"),
]


@pytest.mark.parametrize(("text", "said"), BAD_ROWS, ids=[s for _, s in BAD_ROWS])
def test_a_malformed_row_is_refused(text, said):
    with pytest.raises(CatalogError, match=said) as raised:
        parse_codex(text)
    assert raised.value.source == "test.toml"


@pytest.mark.parametrize(("meta", "said"), [
    ("", "lacks its \\[catalog\\] table"),
    ('[catalog]\nharness = "claude"\n', "names harness 'claude'"),
    ('[catalog]\nharness = "codex"\n', "do not cover every mode"),
    ('[catalog]\nharness = "codex"\n[catalog.help.x]\ncommand = ["codex"]\n'
     'modes = ["host"]\n', "host excluded"),
    ('[catalog]\nharness = "codex"\n[catalog.help.x]\ncommand = ["codex"]\n'
     'modes = ["interactive", "exec"]\nothers = ["verbose"]\n', "not a list of flags"),
])
def test_a_malformed_catalog_table_is_refused(meta, said):
    with pytest.raises(CatalogError, match=said):
        catalog.parse_text(GOOD + meta, harness="codex", source="test.toml")


def test_the_launcher_rows_take_no_flags_and_no_catalog_table():
    with pytest.raises(CatalogError, match="takes no flag"):
        catalog.parse_text('[x]\ntype = "string"\ndefault = ""\ndoc = "d"\nflag = "-x"\n',
                           harness=catalog.LAUNCHER)
    with pytest.raises(CatalogError, match="no \\[catalog\\] table"):
        catalog.parse_text('[catalog]\nharness = "launcher"\n', harness=catalog.LAUNCHER)


def test_a_retired_row_is_refused_when_typed_and_when_checked():
    text = ('[old]\ntype = "string"\ndefault = ""\ndoc = "d"\nflag = "-o"\n'
            'modes = ["exec"]\nretired = "use --model"\n')
    old = parse_codex(text)["old"]
    assert not old.offered(everything) and not old.settable
    with pytest.raises(BadValue, match="retired"):
        old.check("x")


def test_a_templated_spelling_matches_only_its_own_key():
    spelling = Spelling("-c", 'projects.{cwd_toml}.trust_level="trusted"')
    pattern = spelling.key_pattern()
    assert pattern.fullmatch('projects."/x".trust_level')
    assert not pattern.fullmatch("model")
    assert Spelling("-m").key_pattern() is None


# -- the --help diff -------------------------------------------------------------------------

CLAUDE_STYLE = """\
Usage: claude [options] [command] [prompt]

Arguments:
  prompt                                Your prompt

Options:
  --add-dir <directories...>            Additional directories to allow tool
                                        access to
  --allowedTools, --allowed-tools <tools...>
      Comma or space-separated list of tool names to allow (e.g. "Bash(git *)
      Edit")
  -d, --debug [filter]                  Enable debug mode with optional category
                                        filtering (e.g., --debug=hooks,mcp)
  -h, --help                            Display help for command

Commands:
  agents [options]                      Manage background agents
  --not-an-option                       under Commands
"""

CODEX_STYLE = """\
Usage: codex [OPTIONS] [PROMPT]

Commands:
  exec              Run Codex non-interactively [aliases: e]

Options:
  -c, --config <key=value>
          Override a configuration value.

          Examples: - `-c model="o3"`

      --enable <FEATURE>
          Enable a feature (repeatable)

  -a, --ask-for-approval <APPROVAL_POLICY>
          Possible values:
          - on-request: The model decides when to ask the user for approval
          - never:      Never ask
  -i, --image <FILE>...
          Images
"""


def test_help_flags_reads_the_options_section_of_either_style():
    assert catalog.help_flags(CLAUDE_STYLE) == {"--add-dir", "--allowedTools",
                                                "--allowed-tools", "-d", "--debug", "-h",
                                                "--help"}
    assert catalog.help_flags(CODEX_STYLE) == {"-c", "--config", "--enable", "-a",
                                               "--ask-for-approval", "-i", "--image"}


def test_help_flags_without_an_options_header_reads_every_line():
    assert catalog.help_flags("  --x  thing\n  -y, --yes  ok\n") == {"--x", "-y", "--yes"}


def expected_flags(harness: str, help_name: str) -> set:
    """Every flag the shipped catalog expects or knows in one help: what a matching
    `--help` lists."""
    cat = catalog.load(harness)
    help_ = next(h for h in cat.helps if h.name == help_name)
    flags = set(help_.others)
    for row in cat:
        for mode in help_.modes:
            spelled = row.modes.get(mode)
            if spelled and not row.hidden:
                flags.add(spelled.flag)
                if spelled.flag == row.flag:
                    flags.update(row.aliases)
    return flags


def help_text(flags) -> str:
    return "Options:\n" + "".join(f"  {flag}  something\n" for flag in sorted(flags))


class FakeHarness:
    """`read` for `diff`: a help text per command, and a version."""

    def __init__(self, helps: dict, version: str = "9.9.9", missing: bool = False,
                 fails: bool = False):
        self.helps, self.version, self.missing, self.fails = helps, version, missing, fails
        self.calls: list = []

    def __call__(self, argv):
        self.calls.append(list(argv))
        if self.missing:
            return None
        if self.fails:
            raise OSError("permission denied")
        if argv[-1] == "--version":
            return f"tool {self.version}\n"
        return self.helps[tuple(argv)]


def matching(harness: str) -> dict:
    return {tuple(h.command): help_text(expected_flags(harness, h.name))
            for h in catalog.load(harness).helps}


def test_a_help_that_matches_diffs_clean():
    for harness in catalog.HARNESSES:
        fake = FakeHarness(matching(harness))
        result = catalog.diff(harness, read=fake)
        assert result.clean, result
        assert all(h.version == "9.9.9" for h in result.helps)


def test_the_version_is_read_once_per_command():
    fake = FakeHarness(matching("codex"))
    catalog.diff("codex", read=fake)
    assert fake.calls.count(["codex", "--version"]) == 1


def test_a_flag_a_row_spells_that_is_gone_is_removed():
    helps = matching("codex")
    exec_help = ("codex", "exec", "--help")
    helps[exec_help] = help_text(expected_flags("codex", "exec") - {"-s"})
    result = catalog.diff("codex", read=FakeHarness(helps))
    by_name = {h.help.name: h for h in result.helps}
    assert by_name["exec"].removed == ("-s",)
    assert by_name["codex"].removed == ()


def test_a_new_flag_is_added_and_a_known_other_that_left_is_gone():
    helps = matching("claude")
    flags = expected_flags("claude", "claude") - {"--bare"} | {"--brand-new"}
    helps[("claude", "--help")] = help_text(flags)
    (result,) = catalog.diff("claude", read=FakeHarness(helps)).helps
    assert result.added == ("--brand-new",)
    assert result.gone == ("--bare",)
    assert result.removed == ()


def test_hidden_rows_are_not_expected_and_the_host_is_not_diffed():
    (result,) = catalog.diff("claude", read=FakeHarness(matching("claude"))).helps
    assert "--append-system-prompt-file" not in result.removed
    commands = [h.command for h in catalog.load("codex").helps]
    assert commands == [("codex", "--help"), ("codex", "exec", "--help")]


def test_a_row_newer_than_the_installed_harness_is_not_expected(monkeypatch):
    cat = parse_codex(GOOD.replace('flag = "-m"', 'flag = "-m"\nsince = "2.0.0"'))
    monkeypatch.setattr(catalog, "load", lambda harness: cat)
    helps = {("codex", "--help"): help_text({"-h"}),
             ("codex", "exec", "--help"): help_text({"-h"})}
    old_harness = catalog.diff("codex", read=FakeHarness(helps, version="1.5.0"))
    new_harness = catalog.diff("codex", read=FakeHarness(helps, version="2.1.0"))
    assert all(h.removed == () for h in old_harness.helps)
    assert all(h.removed == ("-m",) for h in new_harness.helps)


def test_a_harness_not_on_path_is_missing_and_one_that_fails_says_why():
    missing = catalog.diff("claude", read=FakeHarness({}, missing=True))
    assert missing.missing and not missing.clean
    failed = catalog.diff("claude", read=FakeHarness({}, fails=True))
    assert "permission denied" in failed.helps[0].error


def test_a_help_that_lists_no_flags_is_an_error():
    helps = {("claude", "--help"): "Usage: claude\n"}
    (result,) = catalog.diff("claude", read=FakeHarness(helps)).helps
    assert result.error == "claude --help lists no flags"


@pytest.mark.live
def test_the_installed_harnesses_match_the_catalog():
    """The real `claude --help`, `codex --help` and `codex exec --help` against the
    catalog: no row's flag removed, nothing new unrecorded. A harness that is not
    installed is skipped."""
    ran = False
    for harness in catalog.HARNESSES:
        result = catalog.diff(harness)
        if result.missing:
            continue
        ran = True
        for h in result.helps:
            assert h.error is None, h.error
            assert h.removed == (), f"{h.command} no longer lists {h.removed}"
            assert h.added == (), f"{h.command} lists new flags {h.added}"
            assert h.gone == (), f"{h.command} no longer lists {h.gone}"
    if not ran:
        pytest.skip("neither claude nor codex is on PATH")

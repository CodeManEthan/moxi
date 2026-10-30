"""The child's environment, scrubbed then stamped, and the size limits a launch is refused
past."""

from __future__ import annotations

import pytest
from moxi import modules

from moxi_launcher import compose, store
from moxi_launcher.compose import Additions, Context, claude, codex, common

SID = "5f0c3a1e-7f43-4c8e-9e0a-1d2b3c4d5e6f"


def ctx(**fields) -> Context:
    base = {"request_id": "r1", "session_id": SID, "actor": "ann", "runs_dir": "/runs",
            "windows": False}
    base.update(fields)
    return Context(**base)


# -- scrub ----------------------------------------------------------------------------------


CALLER = {
    "PATH": "/usr/bin", "HOME": "/home/u", "LANG": "C.UTF-8",
    "CLAUDECODE": "1", "CLAUDE_CODE_ENTRYPOINT": "cli", "CLAUDE_PROJECT_DIR": "/p",
    "AI_AGENT": "claude",
    "MOXI_LAUNCHER_REQUEST": "parent-request", "MOXI_LAUNCHER_RELAUNCH": "1",
    "MOXI_LAUNCHER_STATE_HOME": "/state", "MOXI_LAUNCHER_CONFIG_HOME": "/config",
    "MOXI_COMMS_NAME": "parent", "MOXI_COMMS_SCOPE": "moxi", "MOXI_COMMSX": "kept",
    "MOXI_TURNS_COMMANDS": "clear", "MOXI_HOME": "/moxi",
}


def test_the_scrub_takes_claude_names_the_agent_marker_and_each_session_stamp():
    session_env = frozenset({"MOXI_LAUNCHER_REQUEST", "MOXI_LAUNCHER_RELAUNCH",
                             "MOXI_COMMS_", "MOXI_TURNS_COMMANDS"})
    assert common.scrub(CALLER, session_env, windows=False) == {
        "PATH": "/usr/bin", "HOME": "/home/u", "LANG": "C.UTF-8",
        "MOXI_LAUNCHER_STATE_HOME": "/state", "MOXI_LAUNCHER_CONFIG_HOME": "/config",
        "MOXI_COMMSX": "kept", "MOXI_HOME": "/moxi"}


def test_the_launchers_own_stamps_are_named_exactly_so_its_configuration_stays():
    found = modules.load("launcher")
    assert set(found.session_env) == {"MOXI_LAUNCHER_REQUEST", "MOXI_LAUNCHER_RELAUNCH"}
    assert not any(name.endswith("_") for name in found.session_env)
    kept = common.scrub(CALLER, modules.session_env(), windows=False)
    assert "MOXI_LAUNCHER_REQUEST" not in kept and "MOXI_LAUNCHER_RELAUNCH" not in kept
    assert kept["MOXI_LAUNCHER_STATE_HOME"] == "/state"
    assert kept["MOXI_LAUNCHER_CONFIG_HOME"] == "/config"


def test_the_scrub_scrubs_the_whole_installed_set(monkeypatch):
    monkeypatch.setattr(modules, "session_env",
                        lambda: frozenset({"MOXI_LAUNCHER_REQUEST", "MOXI_COMMS_"}))
    got = common.scrub(CALLER, modules.session_env(), windows=False)
    assert "MOXI_COMMS_NAME" not in got and "MOXI_COMMS_SCOPE" not in got


def test_on_windows_names_compare_without_case():
    got = common.scrub({"Claude_X": "1", "ai_agent": "x", "Path": "p",
                        "moxi_launcher_request": "r"},
                       frozenset({"MOXI_LAUNCHER_REQUEST"}), windows=True)
    assert got == {"Path": "p"}


def test_the_scrub_leaves_the_callers_environment_alone():
    caller = dict(CALLER)
    common.scrub(caller, frozenset({"MOXI_COMMS_"}))
    assert caller == CALLER


# -- stamp ----------------------------------------------------------------------------------


def test_every_launch_stamps_its_request():
    assert common.stamps("r1") == {"MOXI_LAUNCHER_REQUEST": "r1"}


def test_the_stamps_with_turns_the_additions_usage_and_notes():
    additions = Additions(env={"MOXI_TURNS_COMMANDS": "exit", "MOXI_X": "1",
                               "MOXI_LAUNCHER_REQUEST": "not-this"})
    assert common.stamps("r1", turns=True, additions=additions, warn_at="1.5x",
                         notes_dir="/notes") == {
        "MOXI_TURNS_COMMANDS": "exit", "MOXI_X": "1", "MOXI_USAGE_WARN_AT": "1.5x",
        "MOXI_NOTES_DIR": "/notes", "MOXI_LAUNCHER_REQUEST": "r1"}


@pytest.mark.parametrize("harness, extra", [("claude", {}), ("codex", {})])
def test_a_launch_stamps_the_grant_only_with_turns(harness, extra):
    values = {"harness": harness, "cwd": "/work"}
    sid = SID if harness == "claude" else None
    without = compose.compose(values, ctx(session_id=sid))
    assert without.env == {"MOXI_LAUNCHER_REQUEST": "r1"}
    with_turns = compose.compose(values, ctx(session_id=sid, modules=frozenset({"turns"}),
                                             host="/host"))
    assert with_turns.env == {"MOXI_TURNS_COMMANDS": "exit", "MOXI_LAUNCHER_REQUEST": "r1"}


def test_the_rows_that_stamp():
    values = {"harness": "codex", "cwd": "/work", "warn_at": "1x", "notes_dir": "/n"}
    got = codex.compose(values, ctx(session_id=None))
    assert got.env == {"MOXI_USAGE_WARN_AT": "1x", "MOXI_NOTES_DIR": "/n",
                       "MOXI_LAUNCHER_REQUEST": "r1"}


def test_the_additions_env_goes_in_only_for_claude_without_comms():
    additions = Additions(env={"MOXI_TURNS_COMMANDS": "exit", "MOXI_EXTRA": "1"})
    values = {"harness": "claude", "cwd": "/work"}
    plain = claude.compose(values, ctx(modules=frozenset({"turns"}), additions=additions))
    assert plain.env["MOXI_EXTRA"] == "1"
    wrapped = claude.compose({**values, "identity": "p"},
                             ctx(modules=frozenset({"turns", "comms"}), additions=additions))
    assert "MOXI_EXTRA" not in wrapped.env


def test_the_childs_environment_is_the_scrubbed_caller_with_the_stamps():
    composition = compose.compose({"harness": "claude", "cwd": "/work"}, ctx())
    env = common.environment(CALLER, composition, frozenset({"MOXI_LAUNCHER_REQUEST"}),
                             windows=False)
    assert env["MOXI_LAUNCHER_REQUEST"] == "r1" and env["PATH"] == "/usr/bin"
    assert not any(name.startswith("CLAUDE") for name in env) and "AI_AGENT" not in env
    # A launch records the stamps only, never the caller's environment.
    assert composition.env == {"MOXI_LAUNCHER_REQUEST": "r1"}


# -- sizes ----------------------------------------------------------------------------------


def test_a_brief_past_the_cap_is_refused_and_one_at_the_cap_is_kept_whole():
    values = {"harness": "claude", "cwd": "/work"}
    links = frozenset({"turns"})            # the brief is the first turn, off the argv
    at_cap = "é" * (store.CAP // 2)
    got = claude.compose(values, ctx(modules=links, brief=at_cap))
    assert got.first.endswith(at_cap)
    with pytest.raises(store.TooLarge) as refused:
        claude.compose(values, ctx(modules=links, brief=at_cap + "x"))
    assert refused.value.field == "brief"


def test_values_and_argv_past_the_cap_are_refused():
    with pytest.raises(store.TooLarge) as refused:
        claude.compose({"harness": "claude", "cwd": "/work",
                        "instructions": "x" * store.CAP}, ctx())
    assert refused.value.field == "values_json"
    with pytest.raises(store.TooLarge) as refused:
        common.check_sizes(["claude", "y" * store.CAP], values={}, windows=False)
    assert refused.value.field == "argv_json"


def test_one_word_past_the_system_limit_is_refused():
    common.check_sizes(["claude"], values={}, env={"X": "a" * (common.WORD - 3)},
                       windows=False)
    with pytest.raises(common.TooLong, match="variable X"):
        common.check_sizes(["claude"], values={}, env={"X": "a" * (common.WORD - 2)},
                           windows=False)


def test_on_windows_the_whole_line_is_limited():
    brief = "w" * (common.WINDOWS_LINE - 200)
    values = {"harness": "codex", "cwd": "/work"}
    codex.compose(values, ctx(session_id=None, brief=brief[:20_000], windows=True))
    with pytest.raises(common.TooLong, match="the command line"):
        codex.compose(values, ctx(session_id=None, brief=brief + "w" * 400, windows=True))

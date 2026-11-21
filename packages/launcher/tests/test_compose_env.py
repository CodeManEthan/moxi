"""The child's environment, scrubbed then stamped, and the size limits a launch is refused
past."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from moxi import modules
from moxi.paths import Layout

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
    "PATH": "/usr/bin", "HOME": "/users/u", "LANG": "C.UTF-8",
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
    assert common.scrub(CALLER, session_env, is_windows=False) == {
        "PATH": "/usr/bin", "HOME": "/users/u", "LANG": "C.UTF-8",
        "MOXI_LAUNCHER_STATE_HOME": "/state", "MOXI_LAUNCHER_CONFIG_HOME": "/config",
        "MOXI_COMMSX": "kept", "MOXI_HOME": "/moxi"}


def test_the_launchers_own_stamps_are_named_exactly_so_its_configuration_stays():
    found = modules.load("launcher")
    assert set(found.session_env) == {"MOXI_LAUNCHER_REQUEST", "MOXI_LAUNCHER_RELAUNCH",
                                     "MOXI_LAUNCHER_KEEP_WARM",
                                     "MOXI_LAUNCHER_RECYCLE_ON_EXPIRY",
                                     "MOXI_USAGE_CROSSING_AT", "MOXI_USAGE_WARN_AT"}
    assert not any(name.endswith("_") for name in found.session_env)
    kept = common.scrub(CALLER, modules.session_env(), is_windows=False)
    assert "MOXI_LAUNCHER_REQUEST" not in kept and "MOXI_LAUNCHER_RELAUNCH" not in kept
    assert kept["MOXI_LAUNCHER_STATE_HOME"] == "/state"
    assert kept["MOXI_LAUNCHER_CONFIG_HOME"] == "/config"


def test_the_scrub_scrubs_the_whole_installed_set(monkeypatch):
    monkeypatch.setattr(modules, "session_env",
                        lambda: frozenset({"MOXI_LAUNCHER_REQUEST", "MOXI_COMMS_"}))
    got = common.scrub(CALLER, modules.session_env(), is_windows=False)
    assert "MOXI_COMMS_NAME" not in got and "MOXI_COMMS_SCOPE" not in got


def test_the_scrub_keeps_the_users_claude_configuration_and_sign_in():
    # Where the configuration lives, the sign-in and the provider: without them the
    # launched Claude reads another folder or can't sign in.
    caller = {**CALLER, "CLAUDE_CONFIG_DIR": "/home/u/.claude-work",
              "CLAUDE_CODE_USE_BEDROCK": "1", "CLAUDE_CODE_USE_VERTEX": "1",
              "CLAUDE_CODE_OAUTH_TOKEN": "t", "CLAUDE_CODE_SUBPROCESS_ENV_SCRUB": "1",
              # A session's own stamps, and a name no list knows yet, still go.
              "CLAUDE_CODE_SESSION_ID": "s", "CLAUDE_CODE_CHILD_SESSION": "1",
              "CLAUDE_PID": "4242", "CLAUDE_CODE_SOMETHING_NEW": "x",
              # A posture with no row of its own is never carried in.
              "CLAUDE_CODE_EVEN_MORE_DANGEROUSLY_SKIP_PERMISSIONS": "1"}
    got = common.scrub(caller, frozenset(), is_windows=False)
    assert {name for name in got if name.startswith("CLAUDE")} == {
        "CLAUDE_CONFIG_DIR", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX",
        "CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CODE_SUBPROCESS_ENV_SCRUB"}
    assert got["CLAUDE_CONFIG_DIR"] == "/home/u/.claude-work"
    # Every kept name is a Claude name, and none is one a session stamps.
    assert all(name.startswith("CLAUDE") for name in common.CLAUDE_KEEP)
    assert not common.CLAUDE_KEEP & {"CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT",
                                     "CLAUDE_CODE_SSE_PORT", "CLAUDE_CODE_SESSION_ID",
                                     "CLAUDE_CODE_CHILD_SESSION", "CLAUDE_PID",
                                     "CLAUDE_EFFORT", "CLAUDE_ENV_FILE"}


def test_a_composed_claude_launch_runs_on_the_users_configuration_dir():
    caller = {**CALLER, "CLAUDE_CONFIG_DIR": "/home/u/.claude-work",
              "CLAUDE_CODE_OAUTH_TOKEN": "t"}
    composition = compose.compose({"harness": "claude", "cwd": "/work"}, ctx())
    env = common.child_env(caller, composition, modules.session_env(), is_windows=False)
    assert env["CLAUDE_CONFIG_DIR"] == "/home/u/.claude-work"
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "t"
    # The sign-in reaches the child only: the launch records its stamps alone.
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in composition.env


def test_on_windows_names_compare_without_case():
    got = common.scrub({"Claude_X": "1", "ai_agent": "x", "Path": "p",
                        "moxi_launcher_request": "r", "Claude_Config_Dir": "c"},
                       frozenset({"MOXI_LAUNCHER_REQUEST"}), is_windows=True)
    assert got == {"Path": "p", "Claude_Config_Dir": "c"}


def test_the_scrub_leaves_the_callers_env_alone():
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


def test_a_value_for_a_module_that_cannot_load_is_not_stamped_and_the_launch_goes_on(
        monkeypatch):
    # A replayed row can carry warn_at or notes_dir after usage or notes was removed.
    real = modules.load
    monkeypatch.setattr(modules, "load", lambda name, **kw: (
        SimpleNamespace(ok=False, api=None) if name in ("usage", "notes")
        else real(name, **kw)))
    assert common.stamps("r1", warn_at="1.5x", notes_dir="/notes") == {
        "MOXI_LAUNCHER_REQUEST": "r1"}
    assert common.option_stamps({"recycle_at_limit": "compact", "limit_at": "1x"}) == {}


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


def test_the_childs_env_is_the_scrubbed_caller_with_the_stamps():
    composition = compose.compose({"harness": "claude", "cwd": "/work"}, ctx())
    env = common.child_env(CALLER, composition, frozenset({"MOXI_LAUNCHER_REQUEST"}),
                             is_windows=False)
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
        common.check_sizes(["claude", "y" * store.CAP], values={}, is_windows=False)
    assert refused.value.field == "argv_json" and refused.value.largest is None


def test_a_brief_under_its_cap_that_fills_values_json_is_named_in_the_refusal():
    # The inline brief is kept in values_json as well, so one under its own cap can
    # still fill it: the refusal names the brief as what to shorten.
    brief = "b" * (store.CAP - 100)
    with pytest.raises(store.TooLarge) as refused:
        common.check_sizes(["claude"], values={"harness": "claude", "brief": brief,
                                               "instructions": "i" * 200},
                           brief=brief, is_windows=False)
    assert refused.value.field == "values_json"
    assert refused.value.largest == ("brief", len(brief) + 2)
    assert f"the largest part brief ({len(brief) + 2} bytes)" in str(refused.value)


def test_one_word_past_the_system_limit_is_refused():
    common.check_sizes(["claude"], values={}, env={"X": "a" * (common.WORD - 3)},
                       is_windows=False)
    with pytest.raises(common.TooLong, match="variable X"):
        common.check_sizes(["claude"], values={}, env={"X": "a" * (common.WORD - 2)},
                           is_windows=False)


def test_on_windows_the_whole_line_is_limited():
    brief = "w" * (common.WINDOWS_LINE - 200)
    values = {"harness": "codex", "cwd": "/work"}
    codex.compose(values, ctx(session_id=None, brief=brief[:20_000], windows=True))
    with pytest.raises(common.TooLong, match="the command line"):
        codex.compose(values, ctx(session_id=None, brief=brief + "w" * 400, windows=True))


# -- the limit mark -------------------------------------------------------------------------


@pytest.fixture
def usage_default(monkeypatch):
    """usage's config `marks.default` as the launcher reads it, set per test."""
    def set_to(mark):
        monkeypatch.setattr(common, "usage_default_mark", lambda: mark)
    return set_to


@pytest.mark.parametrize("values, default, mark", [
    # limit_at set to a mark wins over everything.
    ({"limit_at": "1.5x", "warn_at": "2x"}, "2x", "1.5x"),
    # Unset, it follows the launch's warn_at.
    ({"limit_at": "", "warn_at": "2x"}, "1x", "2x"),
    ({"warn_at": "1.5x"}, None, "1.5x"),
    # No warn_at: usage warns at its config default, so the line comes with it.
    ({"limit_at": "", "warn_at": ""}, "1.5x", "1.5x"),
    ({}, "2x", "2x"),
    # warn_at off, or usage warns at none: 1x.
    ({"warn_at": "off"}, "2x", "1x"),
    ({"warn_at": ""}, None, "1x"),
    ({}, "off", "1x"),
])
def test_the_limit_mark_follows_limit_at_then_warn_at_then_usage(usage_default, values,
                                                                  default, mark):
    usage_default(default)
    assert common.limit_mark(values) == mark
    for recycle in ("relaunch", "compact"):
        stamped = common.option_stamps({"recycle_at_limit": recycle, **values})
        assert stamped == {"MOXI_USAGE_CROSSING_AT": mark}
    assert common.option_stamps({"recycle_at_limit": "off", **values}) == {}


def test_usage_s_default_mark_is_read_from_its_config():
    path = Layout("usage").config_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('[marks]\ndefault = "1.5x"\n', encoding="utf-8")
    assert common.usage_default_mark() == "1.5x"
    assert common.limit_mark({"warn_at": ""}) == "1.5x"
    path.write_text('[marks]\ndefault = "off"\n', encoding="utf-8")
    assert common.limit_mark({"warn_at": ""}) == "1x"


def test_without_usage_there_is_no_default_mark(monkeypatch):
    real = modules.available
    monkeypatch.setattr(modules, "available", lambda name: name != "usage" and real(name))
    assert common.usage_default_mark() is None

"""The command grant bound to the launch's session id: rendered by `launch_additions` when
the launch names its id, read by `config.allowed` against the ids the endpoint's session
has had, its current one and the earlier ones."""

from __future__ import annotations

import logging
import os
import sys
import threading
import time

import pytest

import moxi_turns as turns
from conftest import write_config
from moxi_turns import additions, config, endpoint

ENV = "MOXI_TURNS_COMMANDS"


# -- rendering -----------------------------------------------------------------------------


@pytest.fixture
def no_contributors(monkeypatch):
    real = additions.load
    monkeypatch.setattr(additions, "load", lambda entries=None: real([]))


def adds(harness, **kw):
    return turns.launch_additions(harness, cwd="/work", env={"HOME": "/home/someone"}, **kw)


@pytest.mark.parametrize("harness", ["claude", "codex", "opencode"])
def test_a_launch_that_names_its_id_binds_the_grant_to_it(no_contributors, harness):
    got = adds(harness, session_id="5e1c0d1e-0000-4000-8000-00000000abcd",
               commands=("compact", "exit"))
    assert got.env[ENV] == "5e1c0d1e-0000-4000-8000-00000000abcd:exit,compact"


@pytest.mark.parametrize("sid", [None, ""])
def test_a_launch_with_no_id_renders_the_bare_list(no_contributors, sid):
    assert adds("claude", session_id=sid, commands=("exit",)).env[ENV] == "exit"


def test_an_id_with_no_commands_renders_no_grant(no_contributors):
    assert ENV not in adds("claude", session_id="abc").env


@pytest.mark.parametrize("sid", ["a:b", "a,b", "a b", "a\tb"])
def test_an_id_a_grant_cannot_be_read_back_from_is_refused(no_contributors, sid):
    with pytest.raises(ValueError, match="cannot bind"):
        adds("claude", session_id=sid, commands=("exit",))
    assert ENV not in adds("claude", session_id=sid).env      # no grant, nothing to bind


def test_grant_is_the_one_spelling_and_refuses_an_unknown_command():
    assert config.grant(("exit", "clear"), "id-1") == "id-1:clear,exit"
    assert config.grant(("exit", "clear")) == "clear,exit"
    with pytest.raises(ValueError, match="stop"):
        config.grant(("stop",), "id-1")


# -- reading -------------------------------------------------------------------------------


@pytest.mark.parametrize("stamp, ids, want", [
    ("s-1:clear,exit", ("s-1",), {"clear", "exit"}),            # the current id
    ("s-1:clear,exit", ("s-3", "s-1", "s-2"), {"clear", "exit"}),   # an earlier id
    ("s-1:clear,exit", ("s-2",), set()),                         # another session's id
    ("s-1:clear,exit", (), set()),                               # no id learned yet
    ("s-1:clear,exit", (None,), set()),
    (" s-1 : clear , exit ", ("s-1",), {"clear", "exit"}),
    ("s-1:", ("s-1",), set()),
    ("s-1:stop,exit", ("s-1",), {"exit"}),                       # unknown names dropped
    ("clear,exit", (), {"clear", "exit"}),                       # bare: as it always was
    ("clear,exit", ("anything",), {"clear", "exit"}),
    (" clear , stop ", (), {"clear"}),
    ("", ("s-1",), set()),
])
def test_granted_honours_a_bound_grant_only_for_an_id_the_session_has_had(stamp, ids, want):
    assert config.granted(stamp, ids) == want


@pytest.mark.parametrize("stamp", [":clear,exit", "  :exit", "s-1:clear:exit"])
def test_a_malformed_bound_grant_counts_as_absent_with_a_log_line(stamp, caplog):
    with caplog.at_level(logging.WARNING, logger="moxi.turns.config"):
        assert config.granted(stamp, ("s-1", "")) == frozenset()
    assert any("malformed" in r.message for r in caplog.records)


def test_allowed_joins_the_config_and_a_grant_that_counts_here():
    env = {ENV: "s-1:clear"}
    assert config.allowed(["exit", "nope"], env, ("s-1",)) == {"clear", "exit"}
    assert config.allowed(["exit"], env, ("s-9",)) == {"exit"}   # only commands.allow
    assert config.allowed(["exit"], env) == {"exit"}
    assert config.allowed(None, {ENV: "compact"}) == {"compact"}


# -- on the endpoint ------------------------------------------------------------------------

endpoint_only = pytest.mark.skipif(sys.platform == "win32", reason="no pipe server yet")


@pytest.fixture
def no_rate(monkeypatch):
    monkeypatch.setattr(endpoint, "COMMAND_RATE_S", 0.0)


@endpoint_only
def test_a_grant_bound_to_the_session_survives_clear_and_exit_runs(make_endpoint, no_rate):
    ep, fake = make_endpoint(env=dict(os.environ, **{ENV: "s-1:clear,exit"}))
    s = turns.find(session_id="s-1")
    assert s.command("clear") == turns.Ran("s-2")
    assert ep.session_ids() == ("s-2", "s-1")
    assert s.command("exit") == turns.Ran(None)      # the launch id is now an earlier one
    assert [c.name for c in fake.commands] == ["clear", "exit"]


@endpoint_only
def test_a_grant_bound_to_another_id_is_refused_as_ungranted(make_endpoint):
    """A `claude` started by hand inside a launched session inherits the launch's grant
    under an id of its own: its endpoint never had the launch's id."""
    ep, fake = make_endpoint(env=dict(os.environ, **{ENV: "outer-launch:clear,exit"}))
    s = turns.find(session_id="s-1")
    assert s.command("clear") == turns.Refused("not-allowed")
    assert s.command("exit") == turns.Refused("not-allowed")
    assert fake.commands == []


@endpoint_only
def test_commands_allow_still_applies_beside_a_grant_that_does_not_count(
        make_endpoint, turns_env):
    write_config(turns_env, '[commands]\nallow = ["exit"]\n')
    ep, fake = make_endpoint(env=dict(os.environ, **{ENV: "outer-launch:clear"}))
    s = turns.find(session_id="s-1")
    assert s.command("clear") == turns.Refused("not-allowed")
    assert s.command("exit") == turns.Ran(None)


@endpoint_only
def test_a_bare_grant_works_as_before(make_endpoint, no_rate):
    ep, fake = make_endpoint(env=dict(os.environ, **{ENV: "clear,exit"}))
    s = turns.find(session_id="s-1")
    assert s.command("clear") == turns.Ran("s-2")
    assert s.command("exit") == turns.Ran(None)


@endpoint_only
def test_a_malformed_grant_is_refused_with_a_log_line(make_endpoint, caplog):
    ep, fake = make_endpoint(env=dict(os.environ, **{ENV: ":clear"}))
    s = turns.find(session_id="s-1")
    with caplog.at_level(logging.WARNING, logger="moxi.turns.config"):
        assert s.command("clear") == turns.Refused("not-allowed")
    assert any("malformed" in r.message for r in caplog.records)


@endpoint_only
@pytest.mark.parametrize("resumed, want", [
    ("launch-id", turns.Ran("s-2")),           # a `--resume` that names the launch's id
    ("other-id", turns.Refused("not-allowed")),    # one that names another session
])
def test_the_wrappers_resume_path_with_a_bound_grant(make_endpoint, resumed, want):
    """comms' Claude wrapper passes the id it gives `claude` and no commands; a grant
    bound to the launch's id reaches the session from the launcher's stamp. When the
    wrapper runs `claude --resume <id>`, the session's first id is the resumed one: the
    grant counts only when that is the id it was bound to."""
    grant = config.grant(("clear",), "launch-id")
    ep, fake = make_endpoint(session_id=resumed, env=dict(os.environ, **{ENV: grant}))
    assert turns.find(session_id=resumed).command("clear") == want


@endpoint_only
def test_an_endpoint_started_again_after_a_clear_has_lost_the_launch_id(make_endpoint):
    """The ids live in one endpoint process. One started again after a `/clear` (a plugin
    reload spawns a new endpoint) learns only the current id, so a grant bound to the
    launch's first id reads absent there."""
    env = dict(os.environ, **{ENV: "s-1:clear,exit"})
    ep, fake = make_endpoint(env=env)
    assert turns.find(session_id="s-1").command("clear") == turns.Ran("s-2")
    ep.stop("the plugin reloaded")
    again, _ = make_endpoint(session_id="s-2", env=env)
    assert again.session_ids() == ("s-2",)
    assert turns.find(session_id="s-2").command("exit") == turns.Refused("not-allowed")


@endpoint_only
def test_the_ids_are_copied_under_the_lock_and_handed_to_allowed(make_endpoint, monkeypatch):
    ep, fake = make_endpoint(env=dict(os.environ, **{ENV: "s-1:exit"}))
    seen = []
    real = config.allowed
    monkeypatch.setattr(endpoint.turns_config, "allowed",
                        lambda allow, env, ids=(): seen.append(ids) or real(allow, env, ids))
    ep.learn_session("s-2")
    assert ep.command("exit") == turns.Ran(None)
    assert seen == [("s-2", "s-1")]

    # The copy waits for the lock: a session change in progress is never read half done.
    got: list = []
    held, release = threading.Event(), threading.Event()

    def hold():
        with ep._lock:
            held.set()
            release.wait(5)

    holder = threading.Thread(target=hold, daemon=True)
    holder.start()
    assert held.wait(5)
    reader = threading.Thread(target=lambda: got.append(ep.session_ids()), daemon=True)
    reader.start()
    time.sleep(0.1)
    assert got == []
    release.set()
    reader.join(5)
    holder.join(5)
    assert got == [("s-2", "s-1")]

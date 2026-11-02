"""`context()` and `moxi usage context`: the newest request's whole prompt over the
window, through sessions alone, for a main session and for one of its agents. turns is
not installed in any of these tests."""

import json
import sys
from pathlib import Path

import pytest

from moxi_sessions import api as sessions
from moxi_usage import api, cli

from conftest import write_config

ClaudeTranscript = sessions.testing.ClaudeTranscript
CodexRollout = sessions.testing.CodexRollout
token_usage = sessions.testing.token_usage
usage = sessions.testing.usage

SESSION = "0190b000-0000-7000-8000-0000000000c1"
THREAD = "0190c000-0000-7000-8000-00000000c0a1"
AGENT = "a0190b00000000c1"
WINDOW = 'id = "claude-model-x"\ninput = 3.0\noutput = 15.0\nwindow = 1000000\n'


@pytest.fixture(autouse=True)
def without_turns(monkeypatch):
    monkeypatch.setitem(sys.modules, "moxi_turns", None)


def transcript():
    t = ClaudeTranscript(Path(sessions.root("claude")), session_id=SESSION)
    t.user("go")
    t.assistant("ok", usage=usage(input=330, cache_read=400_000, cache_write=12_000))
    t.user("more")
    t.assistant("ok", usage=usage(input=330, cache_read=410_000, cache_write=2_000))
    t.synthetic()                                  # passed over
    child = t.subagent(AGENT)
    child.user("look")
    child.assistant("found", usage=usage(input=5, cache_read=20_000, cache_write=1_000))
    t.write()
    return t


def test_a_main_sessions_context_is_its_newest_requests_whole_prompt():
    write_config(f"[[model]]\n{WINDOW}")
    transcript()
    found = api.context(SESSION)
    assert (found.harness, found.tokens, found.window) == ("claude", 412_330, 1_000_000)
    assert found.percent == pytest.approx(41.233)
    assert found.model == "claude-model-x" and found.reason is None
    assert found.text() == "context 412,330 / 1,000,000 (41%)"
    assert found.as_dict()["tokens"] == 412_330 and "unknown" not in found.as_dict()


def test_an_agents_context_is_its_own_files_newest_request():
    write_config(f"[[model]]\n{WINDOW}")
    transcript()
    found = api.context(SESSION, harness="claude", agent_id=AGENT)
    assert (found.agent_id, found.tokens) == (AGENT, 21_005)
    assert found.path.endswith(f"agent-{AGENT}.jsonl")


def test_an_unknown_window_prints_the_tokens_and_says_so():
    transcript()
    found = api.context(SESSION)
    assert found.tokens == 412_330 and found.window is api.UNKNOWN
    assert found.text() == "context 412,330 / window unknown"
    assert found.reason == "the model table gives no window for claude-model-x"
    assert set(found.as_dict()["unknown"]) == {"window", "percent"}


def test_codexs_reported_window_wins_over_the_table():
    r = CodexRollout(Path(sessions.root("codex")), thread_id=THREAD)
    r.turn(context_window=200_000)
    r.user("go")
    r.request(token_usage(input=50_000, cached=40_000), response_id="resp_c1",
              context_window=200_000)
    r.turn_end()
    r.write()
    found = api.context(THREAD)
    assert (found.harness, found.tokens, found.window) == ("codex", 50_000, 200_000)
    assert found.text() == "context 50,000 / 200,000 (25%)"


def test_a_session_or_agent_not_found_is_unknown_with_why():
    missing = api.context(SESSION)
    assert not missing.known and missing.harness is None
    assert missing.reason.startswith(f"no session {SESSION} found in claude, codex, "
                                     "opencode")
    transcript()
    no_agent = api.context(SESSION, agent_id="a0190b00000000ff")
    assert no_agent.reason.startswith("no agent a0190b00000000ff found")
    with pytest.raises(ValueError):
        api.context(SESSION, harness="emacs")


def test_a_session_with_no_request_yet_is_unknown():
    t = ClaudeTranscript(Path(sessions.root("claude")), session_id=SESSION)
    t.user("go")
    t.write()
    found = api.context(SESSION)
    assert found.harness == "claude" and found.reason == "the session holds no request yet"
    assert found.text() == "context unknown: the session holds no request yet"


def test_the_verb_prints_the_line_and_exits_by_whether_it_is_known(capsys):
    write_config(f"[[model]]\n{WINDOW}")
    transcript()
    assert cli.main(["context", SESSION]) == 0
    assert capsys.readouterr().out == "context 412,330 / 1,000,000 (41%)\n"
    assert cli.main(["context", SESSION, "--agent", AGENT, "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["tokens"] == 21_005
    assert cli.main(["context", "0190b000-0000-7000-8000-0000000000ff"]) == 1
    assert "context unknown: no session" in capsys.readouterr().out


def test_the_verb_without_a_session_is_a_usage_error(capsys):
    assert cli.main(["context"]) == 2
    assert "give a SESSION id" in capsys.readouterr().err

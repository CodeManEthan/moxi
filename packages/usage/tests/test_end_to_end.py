"""usage end to end, with nothing of usage's own faked: harness files written by sessions'
builders, then `moxi usage index`, a report, `limits` and `index --check` through the
command line, with turns and projects not installed."""

import json
import sys
import time
from pathlib import Path

import pytest

from moxi import modules
from moxi_sessions import api as sessions
from moxi_usage import cli, readings

ClaudeTranscript = sessions.testing.ClaudeTranscript
CodexRollout = sessions.testing.CodexRollout
rate_limits = sessions.testing.rate_limits
token_usage = sessions.testing.token_usage
usage = sessions.testing.usage

HOUR = 3600.0
SESSIONS = ("0190b000-0000-7000-8000-0000000000e1", "0190b000-0000-7000-8000-0000000000e2")
THREADS = ("0190c000-0000-7000-8000-00000000e0a1", "0190c000-0000-7000-8000-00000000e0b2")


@pytest.fixture
def without_turns_or_projects(monkeypatch):
    for name in ("moxi_turns", "moxi_projects"):
        monkeypatch.setitem(sys.modules, name, None)       # neither can be found
    assert not modules.available("turns") and not modules.available("projects")


def corpus(now):
    """Two Claude sessions and two Codex rollouts, two requests each; every Codex request
    carries a limits reading, the later rollout's at the higher percent."""
    for n, session in enumerate(SESSIONS):
        t = ClaudeTranscript(Path(sessions.root("claude")), session_id=session,
                             project=f"-work-p{n}", start=now - 2 * HOUR)
        for m in range(2):
            t.user("go")
            t.assistant("ok", usage=usage(output=10 + m), request_id=f"req_e{n}{m}")
        t.write()
    for n, (thread, used) in enumerate(zip(THREADS, (20.0, 30.0))):
        r = CodexRollout(Path(sessions.root("codex")), thread_id=thread,
                         start=now - HOUR + n * 60)
        r.turn()
        r.user("go")
        for m in range(2):
            limits = rate_limits(primary=(used + m, 300, now + 2 * HOUR),
                                 secondary=(5.0, 10080, now + 3 * 24 * HOUR))
            r.request(token_usage(output=40 + m), response_id=f"resp_e{n}{m}",
                      limits=limits)
        r.turn_end()
        r.write()


def test_index_then_a_report_limits_and_the_check(without_turns_or_projects, capsys):
    now = time.time()
    corpus(now)

    assert cli.main(["index"]) == 0
    out = capsys.readouterr().out
    assert "4 read" in out and "index: 4 files, 8 requests" in out

    conn = readings.open_store()
    try:
        stored = conn.execute("SELECT DISTINCT session_id FROM readings "
                              "WHERE harness = 'codex' AND source = 'rollout'").fetchall()
    finally:
        conn.close()
    assert {row[0] for row in stored} == set(THREADS)      # the pass sent both rollouts'

    assert cli.main(["daily", "--no-pass", "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["total"]["requests"] == 8 and not report["index"]["passed"]

    assert cli.main(["limits", "--no-pull", "--json"]) == 0
    found = json.loads(capsys.readouterr().out)
    rows = {(row["harness"], row["label"]): row for row in found["rows"]}
    five = rows[("codex", "5h")]
    assert (five["state"], five["used_percent"]) == ("fresh", 31.0)
    assert rows[("codex", "7d")]["used_percent"] == 5.0
    assert rows[("claude", "5h")]["reason"] == "turns is not installed"
    assert "used_percent" in rows[("claude", "5h")]["unknown"]
    assert found["pull"] is None

    assert cli.main(["index", "--check"]) == 0
    out = capsys.readouterr().out
    assert "0 read" in out and "agree" in out

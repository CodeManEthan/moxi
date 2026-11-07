"""`moxi sessions read`: the last turns, the turns around a time, the whole session, as text
and as JSON, for each harness, with what secrets redacts and what no flag prints."""

from __future__ import annotations

import json
import time
from datetime import datetime

import pytest

from moxi import modules
from moxi_sessions import cli, view
from moxi_sessions.read import records
from moxi_sessions.shape import SHAPE, SUMMARY_CHARS, tool_summary
from moxi_sessions.source import Source
from moxi_sessions.testing import ClaudeTranscript, CodexRollout, OpenCodeDb
from moxi_sessions.testing.claude import START

#: A token secrets' credentials set knows by its shape: `ghp_` and 36 more characters.
TOKEN = "ghp_" + "aB3dE5fG7hJ9kL1mN3pQ5rS7tU9vW1xY3zA5"
#: What would show if a cut kept the token's start: its first 20 characters.
TOKEN_HEAD = TOKEN[:20]
#: Text that only a tool's whole input holds, past its summary's argument.
INPUT_ONLY = "INPUT-ONLY-WORDS"
#: Text that only a tool's output holds.
OUTPUT_ONLY = "OUTPUT-ONLY-WORDS"


@pytest.fixture
def env(monkeypatch, tmp_path):
    """A home of the test's own, holding each harness's root."""
    home = tmp_path / "home"
    (home / ".claude" / "projects").mkdir(parents=True)
    (home / ".codex" / "sessions").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    for name in ("CLAUDE_CONFIG_DIR", "CODEX_HOME", "XDG_DATA_HOME"):
        monkeypatch.delenv(name, raising=False)
    return home


def run(capsys, *argv) -> tuple[int, str, str]:
    code = cli.main(list(argv), prog="moxi sessions")
    out, err = capsys.readouterr()
    return code, out, err


def claude(home, turns: int = 7) -> ClaudeTranscript:
    """A Claude Code session of `turns` turns, one second a line from `START`. Each turn
    asks `question N`, calls Bash with a long input that holds `INPUT_ONLY` past its
    summary key, gets `OUTPUT_ONLY` back, and answers `answer N`."""
    t = ClaudeTranscript(home / ".claude" / "projects")
    for n in range(turns):
        t.user(f"question {n}")
        call = t.request().tool_use("Bash", {"command": f"echo {n}",
                                             "description": f"step {n} {INPUT_ONLY}"})
        t.tool_result(call, f"{OUTPUT_ONLY} {n}")
        t.assistant(f"answer {n}")
    t.write()
    return t


def codex(home, turns: int = 3) -> CodexRollout:
    r = CodexRollout(home / ".codex" / "sessions")
    for n in range(turns):
        r.turn()
        r.user(f"question {n}")
        call = r.tool_call("shell", {"command": f"echo {n}", "note": INPUT_ONLY},
                           kind="function_call")
        r.tool_output(call, f"{OUTPUT_ONLY} {n}", kind="function_call_output")
        r.assistant(f"answer {n}")
        r.request()
        r.turn_end()
    r.write()
    return r


def opencode(home, turns: int = 3) -> str:
    db = OpenCodeDb(home / ".local" / "share" / "opencode")
    session = db.session()
    for n in range(turns):
        session.user(f"question {n}")
        answer = session.assistant()
        answer.tool("bash", {"command": f"echo {n}", "description": INPUT_ONLY},
                    output=f"{OUTPUT_ONLY} {n}")
        answer.text(f"answer {n}")
        answer.complete()
    db.write()
    return session.id


def questions(out: str) -> list[str]:
    return [line.strip() for line in out.splitlines() if line.strip().startswith("question")]


# --- the window ----------------------------------------------------------------------------

def test_the_default_is_the_last_five_turns(env, capsys):
    t = claude(env)
    code, out, err = run(capsys, "read", t.session_id)
    assert code == 0 and err == ""
    assert questions(out) == [f"question {n}" for n in range(2, 7)]
    assert f"session {t.session_id}" in out.splitlines()[0]
    assert "showing: the last 5 turns; earlier turns are not shown" in out


def test_turns_sets_how_many(env, capsys):
    t = claude(env)
    assert questions(run(capsys, "read", t.session_id, "--turns", "2")[1]) == [
        "question 5", "question 6"]
    out = run(capsys, "read", t.session_id, "--turns", "50")[1]
    assert questions(out) == [f"question {n}" for n in range(7)]
    assert "that is the whole session" in out


@pytest.mark.parametrize("turns", ["0", "51", "-1"])
def test_turns_out_of_range_is_refused(env, capsys, turns):
    t = claude(env)
    code, out, err = run(capsys, "read", t.session_id, "--turns", turns)
    assert code == 2 and out == "" and "--turns is 1 to 50" in err


def test_around_takes_the_turns_around_a_time(env, capsys):
    t = claude(env)
    # Each turn is four lines, a second apart: turn 3 runs from START + 12 to + 15.
    at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(START + 13))
    code, out, _ = run(capsys, "read", t.session_id, "--around", at, "--turns", "3")
    assert code == 0
    assert questions(out) == ["question 2", "question 3", "question 4"]
    assert "earlier and later turns are not shown" in out


def test_around_takes_epoch_seconds_and_a_local_time(env, capsys):
    t = claude(env)
    by_epoch = run(capsys, "read", t.session_id, "--around", str(START + 13), "--turns", "1")
    local = datetime.fromtimestamp(START + 13).strftime("%Y-%m-%dT%H:%M:%S")
    by_local = run(capsys, "read", t.session_id, "--around", local, "--turns", "1")
    assert questions(by_epoch[1]) == questions(by_local[1]) == ["question 3"]


def test_around_near_an_end_fills_from_the_other_side(env, capsys):
    t = claude(env)
    before = run(capsys, "read", t.session_id, "--around", str(START - 1000), "--turns", "3")
    assert questions(before[1]) == ["question 0", "question 1", "question 2"]
    assert "later turns are not shown" in before[1] and "earlier" not in before[1]
    after = run(capsys, "read", t.session_id, "--around", str(START + 10_000), "--turns", "3")
    assert questions(after[1]) == ["question 4", "question 5", "question 6"]


def test_a_time_that_is_not_one_is_refused(env, capsys):
    t = claude(env)
    code, out, err = run(capsys, "read", t.session_id, "--around", "yesterday-ish")
    assert code == 2 and out == "" and "is not a time" in err


@pytest.mark.parametrize("at", ["99999999999999999999", "9" * 400, "253402300800"],
                         ids=["past-time_t", "past-float", "year-10000"])
def test_a_time_out_of_range_is_refused(env, capsys, at):
    t = claude(env)
    code, out, err = run(capsys, "read", t.session_id, "--around", at)
    assert code == 2 and out == "" and "is not a time" in err


def test_the_last_second_of_year_9999_is_a_time(env, capsys):
    t = claude(env)
    code, out, _ = run(capsys, "read", t.session_id, "--around", "253402300799")
    assert code == 0 and "around 9999-12-31T23:59:59Z" in out


def test_all_is_the_whole_session(env, capsys):
    t = claude(env)
    code, out, _ = run(capsys, "read", t.session_id, "--all")
    assert code == 0
    assert questions(out) == [f"question {n}" for n in range(7)]
    assert out.rstrip().endswith("records")


def test_all_goes_with_neither_turns_nor_around(env, capsys):
    t = claude(env)
    assert run(capsys, "read", t.session_id, "--all", "--turns", "3")[0] == 2
    with pytest.raises(SystemExit) as exit_:
        cli.main(["read", t.session_id, "--all", "--around", str(START)])
    assert exit_.value.code == 2


def test_a_session_no_root_holds_exits_1_and_says_where_it_looked(env, capsys):
    code, out, err = run(capsys, "read", "aaaaaaaa-1111-4111-8111-111111111111")
    assert code == 1 and out == ""
    assert "no claude session aaaaaaaa-1111-4111-8111-111111111111 was found" in err
    assert str(env / ".claude" / "projects") in err


def test_an_id_that_is_not_one_is_refused(env, capsys):
    code, _, err = run(capsys, "read", "../etc/passwd")
    assert code == 2 and "is not a session id" in err


def test_a_compressed_rollout_alone_exits_1_with_the_reason(env, capsys):
    r = codex(env)
    r.path.rename(r.path.with_name(r.path.name + ".zst"))
    code, out, err = run(capsys, "read", r.thread_id, "--harness", "codex")
    assert code == 1 and out == "" and "compressed rollout" in err


# --- each harness, and what no flag prints --------------------------------------------------

def harness_session(env, harness: str) -> str:
    if harness == "claude":
        return claude(env, turns=3).session_id
    if harness == "codex":
        return codex(env).thread_id
    return opencode(env)


FLAGS = [(), ("--turns", "1"), ("--around", str(START + 3)), ("--all",), ("--json",),
         ("--all", "--json"), ("--around", str(START + 3), "--json")]


@pytest.mark.parametrize("harness", ["claude", "codex", "opencode"])
@pytest.mark.parametrize("flags", FLAGS, ids=lambda f: " ".join(f) or "default")
def test_no_flag_prints_a_tools_input_past_its_summary_or_its_output(env, capsys, harness,
                                                                      flags):
    session = harness_session(env, harness)
    code, out, err = run(capsys, "read", session, "--harness", harness, *flags)
    assert code == 0, err
    assert "question" in out
    assert "echo" in out                      # the call shows, as its summary
    assert INPUT_ONLY not in out
    assert OUTPUT_ONLY not in out
    if "--json" in flags:
        data = json.loads(out)
        for row in data["records"]:
            assert "input" not in row and "output" not in row and "raw" not in row
            assert "extra" not in row


@pytest.mark.parametrize("harness", ["claude", "codex", "opencode"])
def test_each_harness_shows_a_call_as_its_summary_and_a_result_as_its_size(env, capsys,
                                                                            harness):
    session = harness_session(env, harness)
    out = run(capsys, "read", session, "--harness", harness, "--turns", "1")[1]
    calls = [line for line in out.splitlines() if "echo 2" in line]
    assert len(calls) == 1 and calls[0].endswith(")")
    results = [line for line in out.splitlines() if "Result:" in line]
    assert results and all(line.split("Result: ")[1].endswith(" B") for line in results)
    assert "answer 2" in out


@pytest.mark.parametrize("harness", ["claude", "codex", "opencode"])
def test_without_redaction_a_summary_is_the_records_own(env, harness):
    session = harness_session(env, harness)
    from moxi_sessions.find import find

    source = find(harness, session).hits[0].source
    with records(source, kinds=["tool_call"]) as read:
        calls = list(read)
    assert calls
    for call in calls:
        assert view.summary(call) == call.summary
        assert view.summary(call, lambda text: text) == call.summary


def test_json_is_one_object_with_the_shape_first(env, capsys):
    t = claude(env, turns=3)
    data = json.loads(run(capsys, "read", t.session_id, "--json", "--turns", "2")[1])
    assert list(data)[0] == "shape" and data["shape"] == SHAPE
    assert data["harness"] == "claude" and data["session"] == t.session_id
    assert data["mode"] == "last" and data["turns"] == 2 and data["earlier"] is True
    assert data["redacted"] is True
    assert data["header"]["cwd"] == "/work/project"
    kinds = {row["kind"] for row in data["records"]}
    assert {"message", "tool_call", "tool_result"} <= kinds
    calls = [row["summary"] for row in data["records"] if row["kind"] == "tool_call"]
    assert calls == ["Bash(echo 1)", "Bash(echo 2)"]
    sizes = [row["size_bytes"] for row in data["records"] if row["kind"] == "tool_result"]
    assert sizes == [len(f"{OUTPUT_ONLY} 1"), len(f"{OUTPUT_ONLY} 2")]


def test_json_around_names_the_time_and_both_sides(env, capsys):
    t = claude(env)
    data = json.loads(run(capsys, "read", t.session_id, "--json", "--around",
                          str(START + 13), "--turns", "1")[1])
    assert data["mode"] == "around" and data["around"] == START + 13
    assert data["earlier"] is True and data["later"] is True


# --- redaction -----------------------------------------------------------------------------

def secret_session(env) -> ClaudeTranscript:
    t = ClaudeTranscript(env / ".claude" / "projects")
    t.user(f"my key is {TOKEN}")
    request = t.request()
    request.thinking(f"the key {TOKEN}")
    # The command is longer than the summary's cut, and the token crosses it.
    command = "echo " + "x" * (SUMMARY_CHARS - 40) + " " + TOKEN
    call = request.tool_use("Bash", {"command": command})
    t.tool_result(call, "ok")
    t.assistant(f"saved {TOKEN}")
    t.title(f"about {TOKEN}")
    t.write()
    return t


@pytest.mark.parametrize("flags", FLAGS, ids=lambda f: " ".join(f) or "default")
def test_every_flag_redacts_messages_calls_and_titles(env, capsys, flags):
    t = secret_session(env)
    code, out, err = run(capsys, "read", t.session_id, *flags)
    assert code == 0 and err == ""
    assert TOKEN_HEAD not in out
    assert "[REDACTED:github-pat]" in out


@pytest.mark.parametrize("flags", [f for f in FLAGS if "--json" in f],
                         ids=lambda f: " ".join(f))
def test_json_redacts_a_tool_calls_name_and_server(env, capsys, flags):
    t = ClaudeTranscript(env / ".claude" / "projects")
    t.user("go")
    call = t.request().tool_use(f"mcp__{TOKEN}__tool", {"query": "x"})
    t.tool_result(call, "ok")
    t.write()
    code, out, _ = run(capsys, "read", t.session_id, *flags)
    assert code == 0 and TOKEN_HEAD not in out
    row = next(r for r in json.loads(out)["records"] if r["kind"] == "tool_call")
    assert row["server"].startswith("[REDACTED") and row["name"].startswith("[REDACTED")


def test_the_summary_is_redacted_before_it_is_cut(env):
    """A token that crosses the 160-character cut: cut first, its first part would no
    longer match the token's pattern and would print."""
    command = "echo " + "x" * (SUMMARY_CHARS - 40) + " " + TOKEN
    assert TOKEN_HEAD in tool_summary("Bash", {"command": command})
    secrets = modules.load("secrets", interface=1)
    assert secrets.ok, secrets.reason
    cut_first = secrets.api.redact(tool_summary("Bash", {"command": command}),
                                   view.SETS)
    assert TOKEN_HEAD in cut_first                       # the order the read must not use
    redacted = view.redactor().redact
    line = tool_summary("Bash", {"command": command}, redact=redacted)
    assert TOKEN_HEAD not in line
    assert "[REDACTED" in line and len(line) < len(command)


def test_a_message_is_redacted_before_it_is_cut(env):
    text = "a" * (view.TEXT_MAX - 25) + TOKEN
    t = ClaudeTranscript(env / ".claude" / "projects")
    t.user(text)
    t.write()
    with records(Source("claude", str(t.path), t.session_id)) as read:
        message = next(r for r in read if r.kind == "message")
    plain = view.view_row(message)
    assert plain["truncated"] and TOKEN_HEAD in plain["text"]
    row = view.view_row(message, redact=view.redactor().redact)
    assert TOKEN_HEAD not in row["text"] and "[REDACTED:github-pat]" in row["text"]
    assert not row["truncated"]


def test_without_secrets_the_read_prints_as_written_and_says_so_once(env, capsys,
                                                                     monkeypatch):
    real = modules.load

    def load(name, **kw):
        if name == "secrets":
            return modules.Found(name="secrets", state=modules.ABSENT,
                                 reason="moxi-secrets is not installed")
        return real(name, **kw)

    monkeypatch.setattr(modules, "load", load)
    t = secret_session(env)
    code, out, err = run(capsys, "read", t.session_id)
    assert code == 0
    assert TOKEN in out
    assert err.count("not redacted") == 1 and "secrets module is absent" in err
    data = json.loads(run(capsys, "read", t.session_id, "--json")[1])
    assert data["redacted"] is False


def test_the_read_redacts_with_credentials_and_broad():
    assert view.SETS == ("credentials", "broad")
    redact = view.redactor().redact
    assert redact("password = 'hunter2hunter2'") == "password = '[REDACTED:credential-assignment]'"

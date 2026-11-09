"""Setup and teardown: the roots and their sessions, the timer line, the retention lines,
the auto-memory offer and its write rules, the first full ingest, and a fresh
environment with no harness roots. Every Claude Code settings file here is in the test's
own home."""

import io
import json
import os
import stat

import pytest
from moxi_sessions import api

from moxi_memory import setup, start

T = api.testing


class Asker:
    """Stands in for the keyboard: answers in turn, and what setup had said by then."""

    def __init__(self, *answers, out=None):
        self.answers = list(answers)
        self.questions = []
        self.said = []
        self.out = out

    def __call__(self, question):
        self.questions.append(question)
        self.said.append(self.out.getvalue() if self.out is not None else "")
        return self.answers.pop(0) if self.answers else ""


@pytest.fixture
def spawned(monkeypatch):
    calls = []
    monkeypatch.setattr(start, "spawn", lambda args: calls.append(args) or 4242)
    return calls


@pytest.fixture
def settings_file():
    return os.path.join(os.environ["CLAUDE_CONFIG_DIR"], "settings.json")


def write_settings(path, data, mode=0o644):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        fh.write(data if isinstance(data, str) else json.dumps(data))
    os.chmod(path, mode)


def write_config(text):
    from moxi.paths import Layout

    path = Layout("memory").config_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def run(ask=None, env=None):
    out = io.StringIO()
    if isinstance(ask, Asker):
        ask.out = out
    code = setup.setup(ask=ask, out=out, env=env)
    return code, out.getvalue()


# -- a fresh environment ------------------------------------------------------------------

def test_setup_and_teardown_on_a_fresh_environment_with_no_harness_roots(spawned,
                                                                       settings_file):
    code, said = run()
    assert code == 0
    for name in ("Claude Code", "Codex", "OpenCode"):
        assert f"memory: {name}: no sessions found" in said
    assert "not started through turns" not in said
    assert "Claude Code's auto memory is on" in said
    assert f"start the first full ingest with: {setup.INGEST_LINE}" in said
    assert spawned == []
    assert not os.path.lexists(settings_file)
    out = io.StringIO()
    assert setup.teardown(out=out) == 0
    assert "auto memory off, it stays off" in out.getvalue()
    assert not os.path.lexists(settings_file)


# -- the roots ------------------------------------------------------------------------------

def test_the_roots_and_their_sessions_and_the_codex_sessions_turns_never_saw(spawned):
    for i in range(2):
        t = T.ClaudeTranscript(api.root("claude"), session_id=f"0190b000-0000-7000-8000-00000000c1a{i}")
        t.user("hello")
        t.write()
    threads = [("019a0000-0000-7000-8000-000000000001", "moxi-turns", False),
               ("019a0000-0000-7000-8000-000000000002", "codex_exec", False),
               ("019a0000-0000-7000-8000-000000000003", "codex-tui", False),
               ("019a0000-0000-7000-8000-000000000004", "codex_exec", True)]
    for thread, originator, imported in threads:
        r = T.CodexRollout(api.root("codex"), thread_id=thread, originator=originator,
                           imported=imported)
        r.turn()
        r.user("typed", kinds=["user.text"])
        r.write()
    code, said = run()
    assert code == 0
    assert "memory: Claude Code: 2 sessions in " in said
    assert "memory: Codex: 3 sessions in " in said
    assert "2 Codex sessions here were not started through turns" in said
    assert setup.TIMER_LINE in said

    write_config("[ingest]\ntimer = true\n")
    code, said = run()
    assert "2 Codex sessions here were not started through turns" in said
    assert setup.TIMER_LINE not in said


# -- retention ------------------------------------------------------------------------------

def test_the_retention_lines_come_from_sessions(spawned, settings_file):
    write_settings(settings_file, {"cleanupPeriodDays": 90, "autoMemoryEnabled": False})
    code, said = run()
    for item in api.retention():
        for line in item.lines:
            assert f"memory: {line}" in said
    assert "cleanupPeriodDays is 90" in said
    assert "Setup changes no retention setting" in said


# -- Claude Code's auto memory ----------------------------------------------------------------

def test_auto_memory_is_offered_only_when_it_is_on(spawned, settings_file):
    write_settings(settings_file, {"autoMemoryEnabled": False})
    ask = Asker("n")
    code, said = run(ask)
    assert "auto memory" not in said.lower()
    assert all("auto memory" not in q for q in ask.questions)

    write_settings(settings_file, {"autoMemoryEnabled": True})
    ask = Asker()
    code, said = run(ask, env=dict(os.environ, CLAUDE_CODE_DISABLE_AUTO_MEMORY="1"))
    assert "auto memory" not in said.lower()

    code, said = run(Asker("n"))
    assert "Claude Code's auto memory is on" in said
    assert "Bringing Claude Code's saved memories into Moxi may come later" in said


def test_no_setting_is_written_without_a_yes(spawned, settings_file):
    write_settings(settings_file, {"autoMemoryEnabled": True, "theme": "dark"})
    before = open(settings_file, "rb").read()
    for answer in ("n", "", "maybe"):
        code, said = run(Asker(answer, "", ""))
        assert code == 0
        assert open(settings_file, "rb").read() == before
        assert "auto memory stays on" in said
        assert f"set \"autoMemoryEnabled\": false in {settings_file}" in said


def test_a_setup_that_is_not_interactive_writes_nothing(spawned, settings_file):
    write_settings(settings_file, {"theme": "dark"})
    before = open(settings_file, "rb").read()
    code, said = run(None)
    assert code == 0
    assert open(settings_file, "rb").read() == before
    assert f"to turn auto memory off, set \"autoMemoryEnabled\": false in {settings_file}" \
        in said


def test_a_settings_file_that_does_not_parse_is_not_written(spawned, settings_file):
    write_settings(settings_file, '{"theme": "dark",')
    before = open(settings_file, "rb").read()
    ask = Asker("y", "y")
    code, said = run(ask)
    assert open(settings_file, "rb").read() == before
    assert "can't be parsed" in said and "setup won't change it" in said
    assert all("auto memory" not in q for q in ask.questions)


def test_a_yes_changes_only_auto_memory_and_keeps_the_mode(spawned, settings_file):
    data = {"theme": "dark", "cleanupPeriodDays": 90, "nested": {"a": [1, 2.5, None]},
            "autoMemoryEnabled": True, "zeta": "z"}
    write_settings(settings_file, data, mode=0o644)
    ask = Asker("y", "n")
    code, said = run(ask)
    assert code == 0
    written = json.load(open(settings_file))
    assert written == dict(data, autoMemoryEnabled=False)
    assert stat.S_IMODE(os.stat(settings_file).st_mode) == 0o644
    assert "auto memory is off" in said
    # Setup said that the key order may change before it asked.
    assert "key order" in ask.said[0] and "auto memory" in ask.questions[0]


def test_a_settings_file_that_is_a_link_stays_a_link_and_its_target_is_written(
        spawned, settings_file, tmp_path):
    target = tmp_path / "dotfiles" / "claude-settings.json"
    write_settings(str(target), {"theme": "dark"}, mode=0o640)
    os.makedirs(os.path.dirname(settings_file), exist_ok=True)
    os.symlink(target, settings_file)
    code, said = run(Asker("y", "n"))
    assert os.path.islink(settings_file)
    assert os.readlink(settings_file) == str(target)
    assert json.load(open(target)) == {"theme": "dark", "autoMemoryEnabled": False}
    assert stat.S_IMODE(os.stat(target).st_mode) == 0o640


def test_teardown_leaves_the_settings_file(spawned, settings_file):
    write_settings(settings_file, {"autoMemoryEnabled": False, "theme": "dark"})
    before = open(settings_file, "rb").read()
    out = io.StringIO()
    assert setup.teardown(out=out) == 0
    assert open(settings_file, "rb").read() == before
    assert "left as they are" in out.getvalue()
    assert "set autoMemoryEnabled to true there to turn it back on" in out.getvalue()


# -- the first full ingest ------------------------------------------------------------------

def test_the_first_full_ingest_starts_detached_only_on_a_yes(spawned, settings_file):
    write_settings(settings_file, {"autoMemoryEnabled": False})
    code, said = run(Asker("n"))
    assert spawned == [] and f"start it later with: {setup.INGEST_LINE}" in said
    code, said = run(Asker("y"))
    assert spawned == [["--all"]]
    assert f"watch it with: {setup.STATUS_LINE}" in said


def test_the_fast_engine_line_shows_only_where_it_is_missing(monkeypatch):
    out = io.StringIO()
    monkeypatch.setattr(setup, "RE2_MODULE", "no_such_engine_here")
    setup.re2_line(out)
    assert out.getvalue() == f"memory: {setup.RE2_LINE}\n"
    out = io.StringIO()
    monkeypatch.setattr(setup, "RE2_MODULE", "json")
    setup.re2_line(out)
    assert out.getvalue() == ""

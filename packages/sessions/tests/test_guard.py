"""The transcript-read guard: sessions' guard for turns, its judge per tool and harness,
the `moxi sessions` and `moxi memory` escape, its switch and install question, and the
doctor's `guard` row. Calls are turns' own `ToolCall`; turns' guard loader reads the
installed entry points."""

from __future__ import annotations

import importlib
import logging
from pathlib import Path

import pytest
from conftest import write_config

from moxi import config, modules
from moxi.paths import Layout
from moxi.units import switch_problem
from moxi_sessions import doctor, readguard, settings

turns = modules.load("turns").api

SESSION = "0190b000-0000-7000-8000-0000000004c1"


@pytest.fixture(autouse=True)
def fresh():
    readguard.reset()
    yield
    readguard.reset()


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    """A home of the test's own, with each harness's root made under it."""
    home = tmp_path / "own-home"
    for root in (".claude/projects/-work-project", ".codex/sessions/2026/11/07",
                 ".local/share/opencode"):
        (home / root).mkdir(parents=True)
    (home / ".local/share/opencode/opencode.db").write_bytes(b"")
    monkeypatch.setenv("HOME", str(home))
    for name in ("CLAUDE_CONFIG_DIR", "CODEX_HOME", "XDG_DATA_HOME"):
        monkeypatch.delenv(name, raising=False)
    return home


def call(tool, tool_input, *, harness="claude", cwd="/work/project", home=None):
    env = {"HOME": str(home)} if home is not None else {}
    return turns.ToolCall(harness=harness, session_id=SESSION, agent_id=None, tool=tool,
                          tool_use_id="toolu_1", input=tool_input, cwd=cwd, env=env)


def refused(tool, tool_input, home, **kw) -> bool:
    answer = readguard.judge(call(tool, tool_input, home=home, **kw))
    if answer is None:
        return False
    assert isinstance(answer, turns.Deny)
    assert "moxi sessions read <session>" in answer.reason
    return True


def transcript(home: Path) -> str:
    return str(home / ".claude/projects/-work-project" / f"{SESSION}.jsonl")


def loader():
    """turns' guard loader, which turns' own endpoint runs."""
    return importlib.import_module("moxi_turns.guards")


def write_turns_config(text: str) -> None:
    path = Layout("turns").config_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


# --- the declaration --------------------------------------------------------------------

def test_the_guard_is_declared_as_turns_reads_it():
    from moxi_sessions.guards import GUARDS

    (g,) = GUARDS
    assert isinstance(g, turns.Guard)
    assert (g.name, g.who, g.judge, g.switch, g.on_error) == (
        "transcript-read", "any", "moxi_sessions.readguard:judge",
        "guard.transcript_read", "allow")
    assert g.calls == {"claude": ("Read", "Grep", "Glob", "Bash"), "codex": ("Bash",)}
    assert g.problems() == []


def test_the_entry_point_loads_through_turns_loader_and_is_off_by_default(own_config):
    got = loader().load(on=[])
    assert readguard.GUARD_ID in {f.id for f in got.off}
    assert got.get(readguard.GUARD_ID) is None
    assert not [p for p in got.problems if p.id in (readguard.GUARD_ID, "sessions")]


def test_the_switch_turns_it_on_in_turns_loader_and_the_judge_is_ours(own_config):
    write_config(own_config, "[guard]\ntranscript_read = true\n")
    found = loader().load(on=[]).get(readguard.GUARD_ID)
    assert found is not None and found.by == ("switch",)
    assert loader().judge_of(found.found) is readguard.judge


def test_naming_it_on_turns_list_turns_it_on(own_config):
    found = loader().load(on=[readguard.GUARD_ID]).get(readguard.GUARD_ID)
    assert found is not None and found.by == ("list",)


# --- the judge, per tool and harness ----------------------------------------------------

def test_claude_read_under_a_root_is_refused_and_elsewhere_runs(home):
    assert refused("Read", {"file_path": transcript(home)}, home)
    assert refused("Read", {"file_path": "~/.claude/projects/-work-project/x.jsonl"}, home)
    assert not refused("Read", {"file_path": "/work/project/README.md"}, home)
    assert not refused("Read", {"file_path": str(home / ".claude/projects-old/x")}, home)
    assert not refused("Read", {"file_path": str(home / ".claude/settings.json")}, home)


def test_claude_grep_by_its_path_or_the_working_directory(home):
    root = str(home / ".claude/projects")
    assert refused("Grep", {"pattern": "error", "path": root}, home)
    assert refused("Grep", {"pattern": "error"}, home, cwd=root + "/-work-project")
    assert not refused("Grep", {"pattern": "error"}, home)
    assert not refused("Grep", {"pattern": str(home / ".claude/projects"), "path": "/work"},
                       home)


def test_claude_glob_by_its_pattern_joined_to_its_path(home):
    root = str(home / ".claude/projects")
    assert refused("Glob", {"pattern": root + "/**/*.jsonl"}, home)
    assert refused("Glob", {"pattern": "**/*.jsonl", "path": root}, home)
    assert refused("Glob", {"pattern": "*.jsonl"}, home, cwd=root)
    assert not refused("Glob", {"pattern": "**/*.py"}, home)
    # Cut at the first wildcard: a pattern whose fixed part stops above the root runs.
    assert not refused("Glob", {"pattern": str(home / ".claude") + "/*/x.jsonl"}, home)


def test_claude_bash_words_naming_a_root_path_are_refused(home):
    t = transcript(home)
    for command in (f"cat {t}", f"tail -n 5 '{t}'", f"wc -l < {t}",
                    "grep -r error ~/.claude/projects", "ls $HOME/.claude/projects",
                    "jq . ${HOME}/.claude/projects/-work-project/a.jsonl",
                    f"python3 tool.py --file={t}", f"echo hi && head {t}",
                    f"x=$(cat {t})", f"ls `dirname {t}`",
                    "ls ~/.claude/projects/*/*.jsonl"):
        assert refused("Bash", {"command": command}, home), command
    for command in ("ls ~/.claude", "cat /work/project/notes.md", "git status",
                    "echo projects", "grep -r .claude/projects src"):
        assert not refused("Bash", {"command": command}, home), command


def test_codex_shell_calls_under_its_root_are_refused(home):
    rollout = str(home / ".codex/sessions/2026/11/07/rollout.jsonl")
    assert refused("Bash", {"command": f"cat {rollout}"}, home, harness="codex")
    assert refused("Bash", {"command": ["bash", "-lc", f"cat {rollout}"]}, home,
                   harness="codex")
    assert refused("Bash", {"command": f"sqlite3 {home}/.local/share/opencode/opencode.db"},
                   home, harness="codex")
    assert not refused("Bash", {"command": "cat README.md"}, home, harness="codex")


def test_a_shell_c_script_is_judged_as_commands_of_its_own(home):
    t = transcript(home)
    assert refused("Bash", {"command": f"bash -lc 'cat {t}'"}, home)
    assert not refused("Bash", {"command": f"bash -lc 'moxi sessions find {t}'"}, home)


def test_a_tool_it_does_not_cover_runs(home):
    assert not refused("Write", {"file_path": transcript(home), "content": ""}, home)
    assert not refused("Read", {"file_path": transcript(home)}, home, harness="codex")
    assert not refused("Read", {"file_path": transcript(home)}, home, harness="opencode")


def test_a_link_into_a_root_is_refused_by_its_real_path(home, tmp_path):
    link = tmp_path / "elsewhere"
    link.symlink_to(home / ".claude/projects")
    assert refused("Read", {"file_path": str(link / "-work-project/a.jsonl")}, home)


def test_the_refusal_names_the_path_its_root_and_the_reader(home):
    answer = readguard.judge(call("Read", {"file_path": transcript(home)}, home=home))
    assert transcript(home) in answer.reason
    assert str(home / ".claude/projects") in answer.reason
    assert "moxi sessions read <session>" in answer.reason


# --- the escape -------------------------------------------------------------------------

def test_moxi_sessions_and_moxi_memory_pass_whatever_paths_they_name(home):
    t = transcript(home)
    for command in (f"moxi sessions find {t}", f"moxi memory add --from {t}",
                    f"MOXI_HOME=/x moxi sessions read {SESSION} --root {t}",
                    f"{readguard.own_moxi()} sessions read {SESSION} {t}"):
        assert not refused("Bash", {"command": command}, home), command


def test_the_command_after_the_escape_is_judged_on_its_own(home):
    t = transcript(home)
    for command in (f"moxi sessions root; cat {t}", f"moxi sessions root && cat {t}",
                    f"moxi sessions read {SESSION} | tee {t}",
                    f"moxi sessions root\ncat {t}",
                    f"moxi sessions find `cat {t}`", f"moxi sessions find $(cat {t})"):
        assert refused("Bash", {"command": command}, home), command


def test_another_moxi_or_another_verb_is_no_escape(home):
    t = transcript(home)
    for command in (f"/opt/other/bin/moxi sessions read {t}", f"moxi usage {t}",
                    f"echo moxi sessions {t}"):
        assert refused("Bash", {"command": command}, home), command


# --- the roots --------------------------------------------------------------------------

def test_a_root_moved_by_config_is_guarded(home, own_config, tmp_path):
    moved = tmp_path / "moved-projects"
    (moved / "-p").mkdir(parents=True)
    write_config(own_config, f'[claude]\nprojects = "{moved}"\n')
    assert refused("Read", {"file_path": str(moved / "-p/a.jsonl")}, home)
    assert refused("Bash", {"command": f"cat {moved}/-p/a.jsonl"}, home)
    assert ("claude", str(moved)) in readguard.transcript_roots({"HOME": str(home)})


def test_the_roots_follow_the_launch_environment(home, tmp_path):
    other = tmp_path / "claude-config"
    env = {"HOME": str(home), "CLAUDE_CONFIG_DIR": str(other)}
    path = str(other / "projects/-p/a.jsonl")
    got = readguard.judge(turns.ToolCall(harness="claude", session_id=SESSION,
                                         agent_id=None, tool="Read",
                                         input={"file_path": path}, cwd="/work", env=env))
    assert isinstance(got, turns.Deny)


# --- faults allow -----------------------------------------------------------------------

def test_a_config_that_cannot_be_read_allows_the_call(home, own_config, caplog):
    write_config(own_config, "[claude]\nunknown = 1\n")
    with caplog.at_level(logging.WARNING, logger="moxi.sessions"):
        assert readguard.judge(call("Read", {"file_path": transcript(home)},
                                    home=home)) is None
    assert "it was allowed" in caplog.text


def test_any_judge_fault_allows_the_call(home, monkeypatch, caplog):
    monkeypatch.setattr(readguard, "decide", lambda *a, **k: 1 / 0)
    with caplog.at_level(logging.WARNING, logger="moxi.sessions"):
        assert readguard.judge(call("Read", {"file_path": transcript(home)},
                                    home=home)) is None
    assert "it was allowed" in caplog.text


def test_input_it_cannot_read_runs(home):
    assert not refused("Read", {"file_path": 7}, home)
    assert not refused("Bash", {"command": None}, home)
    assert not refused("Bash", {"command": "cat .claude/projects/x"}, home, cwd=None)


# --- the switch and the install question ------------------------------------------------

def test_the_switch_passes_the_cores_check_and_is_the_install_question():
    assert switch_problem(settings.schema(), "guard.transcript_read") is None
    found = modules.load("sessions")
    assert found.ok, found.reason
    text = found.questions["guard.transcript_read"]
    assert text.startswith("the transcript-read guard (") and "\n" not in text


def test_the_switch_is_off_until_someone_turns_it_on(own_config):
    now = settings.current()
    assert now["guard.transcript_read"] is False
    assert now.source("guard.transcript_read") == config.DEFAULT
    assert settings.schema().keys["guard.transcript_read"].default is False


# --- the doctor -------------------------------------------------------------------------

def test_the_doctor_row_is_off_by_default(home, own_config):
    row = doctor.guard_row()
    assert (row.module, row.name, row.state) == ("sessions", "guard", "off")
    assert "moxi config set sessions guard.transcript_read true" in row.detail
    assert list(doctor.rows())[-1] == row


def test_the_doctor_row_is_ok_when_on_and_turns_enforces_it(home, own_config):
    write_config(own_config, "[guard]\ntranscript_read = true\n")
    row = doctor.guard_row()
    assert row.state == "ok", row.detail
    assert "guard.transcript_read" in row.detail
    assert str(home / ".claude/projects") in row.detail


def test_the_doctor_row_is_ok_when_turns_list_alone_turns_it_on(home, own_config):
    write_turns_config('[guards]\non = ["sessions.transcript-read"]\n')
    row = doctor.guard_row()
    assert row.state == "ok", row.detail
    assert "turns' guards.on" in row.detail


def test_the_doctor_row_warns_when_on_and_turns_is_absent(home, own_config, monkeypatch):
    write_config(own_config, "[guard]\ntranscript_read = true\n")
    real = modules.available
    monkeypatch.setattr(modules, "available",
                        lambda name, *a, **k: False if name == "turns" else real(name, *a, **k))
    row = doctor.guard_row()
    assert row.state == "warn"
    assert "nothing enforces it" in row.detail
    assert row.fix == "moxi install sessions --with turns"


def test_the_doctor_row_warns_when_the_switch_cannot_be_read(home, own_config):
    write_config(own_config, "[guard]\ntranscript_read = \"maybe\"\n")
    row = doctor.guard_row()
    assert row.state == "warn" and "cannot be read" in row.detail

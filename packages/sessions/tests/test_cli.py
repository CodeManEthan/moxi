"""`moxi sessions find` and `moxi sessions root`."""

from __future__ import annotations

import json

import pytest
from conftest import THREAD, assistant_line, write_config, write_rollout, write_transcript

from moxi import cli as umbrella
from moxi_sessions import cli

SID = "aaaaaaaa-1111-4111-8111-111111111111"


@pytest.fixture
def env(monkeypatch, tmp_path):
    """A home of the test's own: Claude's projects root and Codex's sessions root in it."""
    home = tmp_path / "home"
    (home / ".claude" / "projects").mkdir(parents=True)
    (home / ".codex" / "sessions").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.delenv("CODEX_HOME", raising=False)
    return home


def run(capsys, *argv) -> tuple[int, str, str]:
    code = cli.main(list(argv), prog="moxi sessions")
    out, err = capsys.readouterr()
    return code, out, err


def test_find_prints_the_transcripts_path(env, capsys):
    path = write_transcript(env / ".claude" / "projects", "p", SID, [assistant_line()])
    assert run(capsys, "find", SID) == (0, f"{path}\n", "")


def test_find_with_none_prints_every_root_probed_and_why(env, capsys, tmp_path):
    code, out, _ = run(capsys, "find", SID, "--root", str(tmp_path / "unmounted"))
    assert code == 1
    assert f"{env / '.claude' / 'projects'}: miss" in out
    assert f"{tmp_path / 'unmounted'}: unreadable:" in out


def test_find_probes_an_added_root_after_the_usual_one(env, capsys, tmp_path):
    archive = tmp_path / "archive"
    path = write_transcript(archive, "p", SID, [assistant_line()])
    code, out, _ = run(capsys, "find", SID, "--root", str(archive), "--json")
    data = json.loads(out)
    assert code == 0 and data["found"] and data["paths"] == [str(path)]
    assert [p["outcome"] for p in data["probes"]] == ["miss", "hit"]


def test_find_reads_the_configured_root(env, capsys, own_config, tmp_path):
    stated = tmp_path / "stated"
    path = write_transcript(stated, "p", SID, [assistant_line()])
    write_config(own_config, f'[claude]\nprojects = "{stated}"\n')
    assert run(capsys, "find", SID)[:2] == (0, f"{path}\n")


def test_find_a_codex_thread(env, capsys):
    path = write_rollout(env / ".codex" / "sessions")
    assert run(capsys, "find", THREAD, "--harness", "codex")[:2] == (0, f"{path}\n")


def test_find_a_codex_thread_under_codex_home(env, capsys, monkeypatch, tmp_path):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    (tmp_path / "codex" / "sessions").mkdir(parents=True)
    path = write_rollout(tmp_path / "codex" / "sessions")
    code, out, _ = run(capsys, "find", THREAD, "--harness", "codex", "--json")
    assert code == 0 and json.loads(out)["paths"] == [str(path)]


def test_find_prints_every_codex_candidate(env, capsys):
    root = env / ".codex" / "sessions"
    one = write_rollout(root)
    two = write_rollout(root, day="2026/10/01", zst=True)
    code, out, _ = run(capsys, "find", THREAD, "--harness", "codex")
    assert code == 0 and out.split() == sorted([str(one), str(two)])


def test_find_a_codex_thread_with_none(env, capsys):
    code, out, _ = run(capsys, "find", THREAD, "--harness", "codex")
    assert code == 1 and "no rollout" in out and ": miss" in out


def test_find_refuses_an_id_that_is_not_one(env, capsys):
    code, _, err = run(capsys, "find", "../etc")
    assert code == 2 and "not a session id" in err


def test_root_names_the_default(env, capsys):
    code, out, _ = run(capsys, "root")
    assert code == 0
    assert out.splitlines()[0] == str(env / ".claude" / "projects")
    assert "CLAUDE_CONFIG_DIR is unset" in out


def test_root_names_claude_config_dir(env, capsys, monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "cfg"))
    code, out, _ = run(capsys, "root", "--json")
    assert code == 0
    assert json.loads(out) == {"root": str(tmp_path / "cfg" / "projects"),
                               "source": "environment",
                               "detail": "CLAUDE_CONFIG_DIR/projects"}


def test_root_names_the_config_file(env, capsys, own_config, tmp_path):
    file = write_config(own_config, f'[claude]\nprojects = "{tmp_path / "stated"}"\n')
    code, out, _ = run(capsys, "root", "--json")
    data = json.loads(out)
    assert code == 0 and data["root"] == str(tmp_path / "stated")
    assert data["source"] == "config" and str(file) in data["detail"]


def test_a_bad_config_file_is_reported_and_exits_2(env, capsys, own_config):
    write_config(own_config, "[claude]\nunknown = 1\n")
    code, _, err = run(capsys, "root")
    assert code == 2 and "unknown" in err


def test_the_umbrella_dispatches_to_sessions(env, capsys):
    path = write_transcript(env / ".claude" / "projects", "p", SID, [assistant_line()])
    assert umbrella.main(["sessions", "find", SID]) == 0
    assert capsys.readouterr().out == f"{path}\n"


def test_help_reads_as_the_umbrella_names_it(capsys):
    with pytest.raises(SystemExit):
        cli.main(["--help"], prog="moxi sessions")
    assert capsys.readouterr().out.startswith("usage: moxi sessions")

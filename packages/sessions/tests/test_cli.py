"""`moxi sessions find`, `moxi sessions root` and `moxi sessions list`."""

from __future__ import annotations

import json
import os
import shutil
import time

import pytest
from conftest import THREAD, assistant_line, write_config, write_rollout, write_transcript

from moxi import cli as umbrella, modules
from moxi_sessions import catalog, cli, view
from moxi_sessions.shape import SHAPE
from moxi_sessions.testing import ClaudeTranscript, CodexRollout, OpenCodeDb

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
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
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


def test_both_json_documents_carry_the_shape_version_first(env, capsys, tmp_path):
    archive = tmp_path / "archive"
    write_transcript(archive, "p", SID, [assistant_line()])
    for argv in (("find", SID, "--root", str(archive), "--json"),
                 ("find", SID, "--json"), ("root", "--json")):
        data = json.loads(run(capsys, *argv)[1])
        assert list(data)[0] == "shape" and data["shape"] == SHAPE == 1, argv


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


def test_find_marks_a_compressed_rollout_in_json(env, capsys):
    root = env / ".codex" / "sessions"
    plain = write_rollout(root)
    packed = write_rollout(root, day="2026/10/01", zst=True)
    code, out, _ = run(capsys, "find", THREAD, "--harness", "codex", "--json")
    data = json.loads(out)
    assert code == 0 and data["paths"] == sorted([str(plain), str(packed)])
    assert data["compressed"] == [str(packed)]


def test_find_reads_the_configured_codex_root(env, capsys, own_config, tmp_path):
    stated = tmp_path / "stated-codex"
    path = write_rollout(stated)
    write_config(own_config, f'[codex]\nsessions = "{stated}"\n')
    assert run(capsys, "find", THREAD, "--harness", "codex")[:2] == (0, f"{path}\n")


def test_find_an_opencode_session(env, capsys):
    db = OpenCodeDb(env / ".local" / "share" / "opencode")
    session = db.session()
    db.write()
    code, out, _ = run(capsys, "find", session.id, "--harness", "opencode")
    assert (code, out) == (0, f"{db.path}\n")


def test_find_an_opencode_session_in_a_further_database(env, capsys, tmp_path):
    db = OpenCodeDb(tmp_path / "elsewhere")
    session = db.session()
    db.write()
    code, out, _ = run(capsys, "find", session.id, "--harness", "opencode", "--root",
                       str(db.path), "--json")
    data = json.loads(out)
    assert code == 0 and data["paths"] == [str(db.path)]
    assert [p["outcome"].split(":")[0] for p in data["probes"]] == ["unreadable", "hit"]


def test_find_an_opencode_session_with_none(env, capsys):
    db = OpenCodeDb(env / ".local" / "share" / "opencode")
    db.session()
    db.write()
    code, out, _ = run(capsys, "find", "ses_missing", "--harness", "opencode")
    assert code == 1 and "no session row for ses_missing" in out and ": miss" in out


def test_find_refuses_an_id_that_is_not_one(env, capsys):
    code, _, err = run(capsys, "find", "../etc")
    assert code == 2 and "not a session id" in err


def test_root_names_every_harness_and_its_default(env, capsys):
    code, out, _ = run(capsys, "root")
    lines = out.splitlines()
    assert code == 0
    assert lines[0] == f"claude: {env / '.claude' / 'projects'}"
    assert "CLAUDE_CONFIG_DIR is unset" in lines[1]
    assert lines[2] == f"codex: {env / '.codex' / 'sessions'}"
    assert "CODEX_HOME is unset" in lines[3]
    assert lines[4].startswith("opencode: ") and lines[4].endswith("opencode.db")
    assert lines[5].startswith("  from: ")


def test_root_names_claude_config_dir(env, capsys, monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "cfg"))
    code, out, _ = run(capsys, "root", "--json")
    assert code == 0
    assert json.loads(out)["claude"] == {"root": str(tmp_path / "cfg" / "projects"),
                                         "source": "environment",
                                         "detail": "CLAUDE_CONFIG_DIR/projects"}


def test_root_names_codex_home_and_xdg_data_home(env, capsys, monkeypatch, tmp_path):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    data = json.loads(run(capsys, "root", "--json")[1])
    assert set(data) == {"shape", "claude", "codex", "opencode"}
    assert data["codex"] == {"root": str(tmp_path / "codex" / "sessions"),
                             "source": "environment", "detail": "CODEX_HOME/sessions"}
    assert data["opencode"] == {"root": str(tmp_path / "data" / "opencode" / "opencode.db"),
                                "source": "environment",
                                "detail": "XDG_DATA_HOME/opencode/opencode.db"}


def test_root_names_the_config_file(env, capsys, own_config, tmp_path):
    file = write_config(own_config, f'[claude]\nprojects = "{tmp_path / "stated"}"\n'
                                    f'[opencode]\ndatabase = "{tmp_path / "oc.db"}"\n')
    code, out, _ = run(capsys, "root", "--json")
    data = json.loads(out)
    assert code == 0 and data["claude"]["root"] == str(tmp_path / "stated")
    assert data["claude"]["source"] == "config" and str(file) in data["claude"]["detail"]
    assert data["opencode"]["root"] == str(tmp_path / "oc.db")
    assert data["opencode"]["source"] == "config"
    assert data["codex"]["source"] == "default"


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


# --- list -----------------------------------------------------------------------------

#: A token secrets' credentials set knows by its shape: `ghp_` and 36 more characters.
TOKEN = "ghp_" + "aB3dE5fG7hJ9kL1mN3pQ5rS7tU9vW1xY3zA5"
HOUR = 3600.0


def sid(n: int) -> str:
    return f"0190b000-0000-7000-8000-{n:012x}"


@pytest.fixture
def listed(env):
    """The home with an OpenCode database too, and a process that has listed nothing."""
    OpenCodeDb(env / ".local" / "share" / "opencode").write()
    catalog.reset()
    yield env
    catalog.reset()


@pytest.fixture
def bare(env):
    """The home with no harness in it, and a process that has listed nothing."""
    shutil.rmtree(env / ".codex")
    shutil.rmtree(env / ".claude")
    catalog.reset()
    yield env
    catalog.reset()


def changed_at(path, at: float) -> None:
    stamp = int(at * 1_000_000_000)
    os.utime(path, ns=(stamp, stamp))


def claude_session(home, n: int, at: float, *, cwd: str = "/work/proj-a",
                   name: str | None = None, said: str = "Done.") -> ClaudeTranscript:
    t = ClaudeTranscript(home / ".claude" / "projects", session_id=sid(n), cwd=cwd,
                         project=cwd.replace("/", "-"))
    if name is not None:
        t.title(name)
    t.user("Do the thing.", origin="human")
    t.assistant(said)
    changed_at(t.write(), at)
    return t


def codex_session(home, at: float) -> CodexRollout:
    r = CodexRollout(home / ".codex" / "sessions", thread_id=THREAD, cwd="/work/proj-b")
    r.turn()
    r.user("Do the thing.")
    r.assistant("Done.")
    changed_at(r.write(), at)
    return r


def test_list_prints_one_line_a_session_newest_first(listed, capsys):
    now = time.time()
    t = claude_session(listed, 1, now - 3 * HOUR, name="fix the tests")
    claude_session(listed, 2, now - HOUR)
    codex_session(listed, now - 2 * HOUR)

    code, out, err = run(capsys, "list")

    assert (code, err) == (0, "")
    lines = [line.split("  ") for line in out.splitlines()]
    assert [line[2:] for line in lines] == [
        ["claude", "/work/proj-a", sid(2)],
        ["codex", "/work/proj-b", THREAD],
        ["claude", "fix the tests", sid(1)]]
    assert lines[2][0] == view.stamp(t.clock - 2 * t.step)
    assert lines[2][1] == view.stamp(now - 3 * HOUR)
    assert "Done." not in out and "Do the thing." not in out


def test_list_prints_what_was_said_only_with_text(listed, capsys):
    claude_session(listed, 1, time.time() - HOUR, said="All fixed.\nNothing else.")

    code, out, _ = run(capsys, "list", "--text")

    assert code == 0
    first, second = out.splitlines()
    assert first.endswith(sid(1)) and second == "    agent: All fixed. Nothing else."


def test_list_narrows_by_harness_since_folder_and_limit(listed, capsys):
    now = time.time()
    for n in range(1, 5):
        claude_session(listed, n, now - n * HOUR, cwd=f"/work/proj-{'ab'[n % 2]}")
    codex_session(listed, now - 30)

    def ids(*argv) -> list[str]:
        return [line.split("  ")[-1] for line in run(capsys, "list", *argv)[1].splitlines()]

    assert ids("--harness", "codex") == [THREAD]
    assert ids("--harness", "claude", "--folder", "/work/proj-a") == [sid(2), sid(4)]
    assert ids("--since", str(now - 2.5 * HOUR)) == [THREAD, sid(1), sid(2)]
    assert ids("--since", view.stamp(now - 1.5 * HOUR)) == [THREAD, sid(1)]
    code, out, err = run(capsys, "list", "--limit", "2")
    assert code == 0 and len(out.splitlines()) == 2
    assert "the newest 2 of 5 sessions" in err


def test_list_shows_thirty_sessions_unless_asked(listed, capsys):
    now = time.time()
    for n in range(1, 33):
        claude_session(listed, n, now - n * 60)

    assert len(run(capsys, "list")[1].splitlines()) == 30
    assert len(run(capsys, "list", "--limit", "40")[1].splitlines()) == 32


def test_list_json_is_the_list_document_and_holds_text_only_when_asked(listed, capsys):
    now = time.time()
    claude_session(listed, 1, now - HOUR / 2, name="fix the tests")

    data = json.loads(run(capsys, "list", "--json")[1])

    assert list(data)[0] == "shape" and data["shape"] == SHAPE
    assert (data["all"], data["total"], data["limit"], data["text"]) == (1, 1, 30, False)
    assert data["roots"] == [] and data["unreadable"] == 0 and data["before"] is None
    assert [h["count"] for h in data["harnesses"]] == [1, 0, 0]
    row, = data["rows"]
    assert (row["harness"], row["session"], row["name"]) == ("claude", sid(1),
                                                             "fix the tests")
    assert row["changed"] == pytest.approx(now - HOUR / 2, abs=0.001)
    assert row["recent"] is True
    assert set(row["activity"]) == {"at", "who"} and set(row["prompt"]) == {"at"}

    with_text = json.loads(run(capsys, "list", "--json", "--text")[1])
    row, = with_text["rows"]
    assert with_text["text"] is True
    assert row["activity"] == {"at": row["activity"]["at"], "who": "agent",
                               "text": "Done."}
    assert row["prompt"] == {"at": row["prompt"]["at"], "text": "Do the thing.",
                             "truncated": False}


def test_list_prints_a_dash_for_what_was_not_read(listed, capsys):
    t = ClaudeTranscript(listed / ".claude" / "projects", session_id=sid(1))
    t.other("mode", mode="default")
    at = time.time() - HOUR
    changed_at(t.write(), at)

    code, out, _ = run(capsys, "list", "--text")

    assert (code, out) == (0, f"-  {view.stamp(at)}  claude  -  {sid(1)}\n")


def test_list_redacts_names_and_text_with_secrets(listed, capsys):
    claude_session(listed, 1, time.time() - HOUR, name=f"rotate {TOKEN}",
                   said=f"The token is {TOKEN}.")

    code, out, err = run(capsys, "list", "--text")
    assert code == 0 and TOKEN not in out and "not redacted" not in err
    data = json.loads(run(capsys, "list", "--text", "--json")[1])
    assert data["redacted"] is True and TOKEN not in json.dumps(data)


def test_list_without_secrets_says_so_once_when_it_prints_text(listed, capsys,
                                                               monkeypatch):
    real = modules.load

    def load(name, **kw):
        if name == "secrets":
            return modules.Found(name="secrets", state=modules.ABSENT,
                                 reason="moxi-secrets is not installed")
        return real(name, **kw)

    monkeypatch.setattr(modules, "load", load)
    claude_session(listed, 1, time.time() - HOUR, said=f"The token is {TOKEN}.")

    code, out, err = run(capsys, "list", "--text")
    assert code == 0 and TOKEN in out and err.count("not redacted") == 1
    code, out, err = run(capsys, "list")
    assert (code, err) == (0, "") and TOKEN not in out
    assert json.loads(run(capsys, "list", "--json")[1])["redacted"] is False


def test_list_names_a_configured_root_it_could_not_list_and_lists_the_others(
        listed, capsys, own_config, tmp_path):
    claude_session(listed, 1, time.time() - HOUR)
    write_config(own_config, f'[codex]\nsessions = "{tmp_path / "nowhere"}"\n')

    code, out, err = run(capsys, "list")

    assert code == 0 and out.splitlines()[0].endswith(sid(1))
    assert f"codex: {tmp_path / 'nowhere'} could not be listed" in err
    data = json.loads(run(capsys, "list", "--json")[1])
    assert [r["harness"] for r in data["roots"]] == ["codex"]
    assert [h["harness"] for h in data["harnesses"]] == ["claude", "opencode"]
    assert data["unreadable"] is None


def test_list_says_nothing_of_a_harness_that_is_not_on_the_machine(bare, capsys):
    claude_session(bare, 1, time.time() - HOUR)       # the home has Claude Code alone

    code, out, err = run(capsys, "list")

    assert (code, err) == (0, "") and out.splitlines()[0].endswith(sid(1))
    data = json.loads(run(capsys, "list", "--json")[1])
    assert data["roots"] == [] and data["harnesses"] == [{"harness": "claude",
                                                          "count": 1}]


def test_list_on_a_machine_with_no_harness_prints_nothing(bare, capsys):
    assert run(capsys, "list") == (0, "", "")
    data = json.loads(run(capsys, "list", "--json")[1])
    assert (data["all"], data["roots"], data["harnesses"], data["rows"]) == (0, [], [], [])


def test_list_names_a_default_root_that_exists_and_cannot_be_read(listed, capsys):
    claude_session(listed, 1, time.time() - HOUR)
    sessions = listed / ".codex" / "sessions"
    sessions.rmdir()
    sessions.write_text("not a folder")

    code, out, err = run(capsys, "list")

    assert code == 0 and out.splitlines()[0].endswith(sid(1))
    assert f"codex: {sessions} could not be listed" in err


@pytest.mark.skipif(os.geteuid() == 0, reason="root lists a folder without permission")
def test_list_says_how_many_parts_of_a_root_it_could_not_list(listed, capsys):
    claude_session(listed, 1, time.time() - HOUR)
    unlisted = listed / ".claude" / "projects" / "-work-proj-c"
    unlisted.mkdir()
    unlisted.chmod(0)
    try:
        code, out, err = run(capsys, "list")
    finally:
        unlisted.chmod(0o700)

    assert code == 0 and out.splitlines()[0].endswith(sid(1))
    assert (f"claude: 1 folders or files under {listed / '.claude' / 'projects'} could "
            "not be listed; their sessions are not counted") in err


def test_list_refuses_arguments_that_are_not_ones(listed, capsys):
    for argv, said in ((("--since", "soon"), "is not a time"),
                       (("--folder", "proj-a"), "absolute path"),
                       (("--limit", "0"), "1 or more")):
        code, out, err = run(capsys, "list", *argv)
        assert (code, out) == (2, "") and said in err, argv


def test_list_reads_the_recent_setting(listed, capsys, own_config):
    claude_session(listed, 1, time.time() - HOUR / 2)

    assert json.loads(run(capsys, "list", "--json")[1])["rows"][0]["recent"] is True
    write_config(own_config, "[list]\nrecent_s = 60\n")
    catalog.reset()
    assert json.loads(run(capsys, "list", "--json")[1])["rows"][0]["recent"] is False

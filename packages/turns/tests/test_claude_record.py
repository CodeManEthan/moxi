"""The name typed for a Claude Code session, read from Claude Code's own record of its
process. Every record here is one the test writes under its own folder; no live record is
read."""

from __future__ import annotations

import json
import sys

import pytest

from moxi_turns import claude_record

PID = 4242
SID = "0b7f6c1e-1111-4222-8333-944455556666"


def write(config_dir, record, pid=PID):
    sessions_dir = config_dir / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    path = sessions_dir / f"{pid}.json"
    path.write_text(record if isinstance(record, str) else json.dumps(record))
    return path


def record(**over):
    out = {"pid": PID, "sessionId": SID, "cwd": "/work/orders", "startedAt": 1,
           "kind": "interactive", "status": "idle", "name": "orders-skeleton",
           "nameSource": "user"}
    out.update(over)
    return {k: v for k, v in out.items() if v is not ...}


@pytest.fixture
def env(tmp_path):
    return {"CLAUDE_CONFIG_DIR": str(tmp_path / "claude"), "HOME": str(tmp_path / "home"),
            "USERPROFILE": str(tmp_path / "home")}


def config_dir(env):
    from pathlib import Path

    return Path(env["CLAUDE_CONFIG_DIR"])


def test_the_record_is_under_claude_codes_own_folder(env, tmp_path):
    assert claude_record.record_path(PID, env) == tmp_path / "claude" / "sessions" / "4242.json"
    del env["CLAUDE_CONFIG_DIR"]
    assert claude_record.record_path(PID, env) == (
        tmp_path / "home" / ".claude" / "sessions" / "4242.json")


@pytest.mark.skipif(sys.platform == "win32", reason="a leading ~ is a POSIX shell's")
def test_a_config_dir_under_the_home_is_read_from_the_home_given(env, tmp_path):
    env["CLAUDE_CONFIG_DIR"] = "~/elsewhere"
    assert claude_record.record_path(PID, env) == (
        tmp_path / "home" / "elsewhere" / "sessions" / "4242.json")


@pytest.mark.parametrize("source", ["user", ...])
def test_a_name_given_at_start_or_typed_with_rename_is_read(env, source):
    write(config_dir(env), record(nameSource=source))
    assert claude_record.typed_name(PID, SID, env) == "orders-skeleton"


@pytest.mark.parametrize("source", ["derived", "auto", "collision", "peer", "hook", 7])
def test_a_name_the_person_did_not_type_is_not_read(env, source):
    write(config_dir(env), record(nameSource=source))
    assert claude_record.typed_name(PID, SID, env) is None


def test_a_record_of_another_session_id_is_not_read(env):
    write(config_dir(env), record(sessionId="another"))
    assert claude_record.typed_name(PID, SID, env) is None
    write(config_dir(env), record(sessionId=...))
    assert claude_record.typed_name(PID, SID, env) is None


@pytest.mark.parametrize(("pid", "session_id"), [(None, SID), (0, SID), (-1, SID), (PID, None),
                                                 (PID, "")])
def test_no_pid_or_session_id_reads_nothing(env, pid, session_id):
    write(config_dir(env), record())
    assert claude_record.typed_name(pid, session_id, env) is None


@pytest.mark.parametrize("name", [..., None, 3, "", "   "])
def test_no_name_reads_as_none(env, name):
    write(config_dir(env), record(name=name))
    assert claude_record.typed_name(PID, SID, env) is None


def test_a_name_is_read_as_written_less_its_outer_spaces(env):
    write(config_dir(env), record(name="  billing export "))
    assert claude_record.typed_name(PID, SID, env) == "billing export"


@pytest.mark.parametrize("text", ["", "not json", "[1, 2]", '"text"'])
def test_a_record_that_is_not_an_object_reads_as_none(env, text):
    write(config_dir(env), text)
    assert claude_record.typed_name(PID, SID, env) is None


def test_no_record_reads_as_none(env):
    assert claude_record.typed_name(PID, SID, env) is None
    write(config_dir(env), record(), pid=PID + 1)
    assert claude_record.typed_name(PID, SID, env) is None


def test_a_record_past_the_size_claude_code_reads_is_not_read(env):
    write(config_dir(env), json.dumps(record(pad="x" * claude_record.RECORD_MAX_BYTES)))
    assert claude_record.typed_name(PID, SID, env) is None


def test_nothing_is_written(env):
    path = write(config_dir(env), record())
    before = path.read_bytes(), path.stat().st_mtime_ns
    claude_record.typed_name(PID, SID, env)
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before
    assert sorted(p.name for p in path.parent.iterdir()) == ["4242.json"]

"""The Codex builder writes rollouts the way Codex writes them, where the finder finds
them, and reads no environment."""

from __future__ import annotations

import json
import os

from moxi_sessions import codex
from moxi_sessions.testing import CodexRollout, write_imports


def test_a_rollout_is_where_the_finder_looks(sessions):
    r = CodexRollout(sessions)
    path = r.write()
    assert codex.find_rollout(sessions, r.thread_id) == [path]
    assert path.parent == sessions / "2026" / "10" / "30"


def test_line_one_holds_the_keys_codex_writes(sessions):
    r = CodexRollout(sessions)
    line = json.loads(r.lines[0])
    assert line["type"] == "session_meta" and line["ordinal"] == 0
    assert set(line["payload"]) <= set(codex.META_KEYS)


def test_lines_are_stamped_numbered_and_offset(sessions):
    r = CodexRollout(sessions, step=2.0)
    r.turn()
    r.request()
    lines = [json.loads(text) for text in r.lines]
    assert [line["ordinal"] for line in lines] == list(range(len(lines)))
    assert lines[0]["timestamp"] == "2026-10-30T10:00:00.000Z"
    assert lines[1]["timestamp"] == "2026-10-30T10:00:02.000Z"
    data = r.write().read_bytes()
    assert data[r.offset(3):].startswith(r.lines[2].encode())


def test_a_request_is_a_usage_record_then_its_token_count(sessions):
    r = CodexRollout(sessions)
    r.turn()
    rid = r.request()
    *_, record, count = [json.loads(text) for text in r.lines]
    assert record["type"] == "token_usage_record"
    assert record["payload"]["response_id"] == rid
    assert count["payload"]["type"] == "token_count"
    assert count["payload"]["rate_limits"]["limit_id"] == "codex"


def test_a_legacy_rollout_writes_token_counts_alone(sessions):
    r = CodexRollout(sessions, cli_version="0.145.0")
    r.turn()
    r.request()
    r.request()
    kinds = [json.loads(text)["type"] for text in r.lines]
    assert "token_usage_record" not in kinds
    last = json.loads(r.lines[-1])["payload"]["info"]
    assert last["total_token_usage"]["input_tokens"] == 2 * last["last_token_usage"][
        "input_tokens"]


def test_an_imported_thread_looks_like_one(sessions):
    r = CodexRollout(sessions, imported=True)
    turn = r.turn()
    assert json.loads(r.lines[0])["payload"]["originator"] == "Codex Desktop"
    assert turn.startswith("external-import-turn-")
    assert all(json.loads(text)["type"] != "turn_context" for text in r.lines)


def test_flush_appends_in_place(sessions):
    r = CodexRollout(sessions)
    path = r.write(terminated=False)
    inode = os.stat(path).st_ino
    r.turn()
    r.flush()
    assert os.stat(path).st_ino == inode
    assert path.read_text().splitlines() == r.lines


def test_the_imports_file_sits_beside_the_sessions_root(sessions):
    path = write_imports(sessions, ["t-1"])
    assert path == CodexRollout(sessions).imports_path
    data = json.loads(path.read_text())
    assert [rec["imported_thread_id"] for rec in data["records"]] == ["t-1"]

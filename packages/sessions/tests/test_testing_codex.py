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


CHILD = "0190c000-0000-7000-8000-0000000c0de3"
GRANDCHILD = "0190c000-0000-7000-8000-0000000c0de5"


def test_a_spawn_writes_the_call_its_items_and_the_childs_line_one(sessions):
    r = CodexRollout(sessions)
    r.turn()
    child = r.spawn(CHILD, "explore", call_id="call_x")
    lines = [json.loads(text) for text in r.lines]
    kinds = [(line["type"], line["payload"]["type"]) for line in lines[-4:]]
    assert kinds == [("response_item", "function_call"), ("event_msg", "item_completed"),
                     ("response_item", "function_call_output"),
                     ("event_msg", "item_completed")]
    assert lines[-4]["payload"]["name"] == "spawn_agent"
    started, done = lines[-3]["payload"], lines[-1]["payload"]
    assert started["thread_id"] == r.thread_id
    assert started["item"] == {"type": "SubAgentActivity", "id": "call_x",
                               "kind": "started", "agent_thread_id": CHILD,
                               "agent_path": "/root/explore"}
    assert done["item"]["kind"] == "completed" and done["item"]["agent_thread_id"] == CHILD
    meta = json.loads(child.lines[0])["payload"]
    assert meta["id"] == CHILD and meta["session_id"] == r.thread_id
    assert meta["parent_thread_id"] == meta["forked_from_id"] == r.thread_id
    assert meta["source"]["subagent"]["thread_spawn"] == {
        "parent_thread_id": r.thread_id, "depth": 1, "agent_path": "/root/explore",
        "agent_nickname": "Hopper", "agent_role": None}
    assert meta["thread_source"] == "subagent" and meta["multi_agent_version"] == "v2"
    assert child.path.parent == r.path.parent


def test_a_childs_spawn_is_one_deeper(sessions):
    r = CodexRollout(sessions)
    grand = r.spawn(CHILD, "explore").spawn(GRANDCHILD, "look")
    spawn = json.loads(grand.lines[0])["payload"]["source"]["subagent"]["thread_spawn"]
    assert (spawn["parent_thread_id"], spawn["depth"], spawn["agent_path"]) == (
        CHILD, 2, "/root/explore/look")

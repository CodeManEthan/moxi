"""The Claude builder in `moxi_sessions.testing`: the layouts it writes, as Claude Code
writes them, so other modules' tests can stand on it."""

from __future__ import annotations

import json

from moxi_sessions import claude
from moxi_sessions.testing import ClaudeTranscript, usage


def entries(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_a_transcript_lives_where_the_finder_looks(projects):
    t = ClaudeTranscript(projects, session_id="0190b000-0000-7000-8000-000000000001",
                         project="-home-me-work")
    t.user("hi")
    path = t.write()
    assert path == projects / "-home-me-work" / "0190b000-0000-7000-8000-000000000001.jsonl"
    assert claude.probe_root(projects, t.session_id).path == str(path)


def test_each_block_of_a_request_is_its_own_line_with_the_same_usage(projects):
    t = ClaudeTranscript(projects)
    req = t.request(usage_block=usage(input=5))
    req.thinking()
    req.text()
    req.tool_use("Read", {"file_path": "/x"})
    lines = entries(t.write())
    assert len(lines) == 3
    assert {line["requestId"] for line in lines} == {req.request_id}
    assert {line["message"]["id"] for line in lines} == {req.message_id}
    assert all(line["message"]["usage"] == usage(input=5) for line in lines)
    assert [line["message"]["content"][0]["type"] for line in lines] == [
        "thinking", "text", "tool_use"]


def test_lines_carry_a_clock_a_chain_and_the_session(projects):
    t = ClaudeTranscript(projects, start=100.0, step=2.0)
    t.user("a")
    t.assistant()
    first, second = entries(t.write())
    assert claude.parse_timestamp(first["timestamp"]) == 100.0
    assert claude.parse_timestamp(second["timestamp"]) == 102.0
    assert second["parentUuid"] == first["uuid"]
    assert first["sessionId"] == second["sessionId"] == t.session_id
    assert first["isSidechain"] is False
    assert first["promptId"]


def test_a_subagent_file_and_its_sidecar(projects):
    t = ClaudeTranscript(projects)
    sub = t.subagent("a1", agent_type="Explore", description="find it",
                     tool_use_id="toolu_7", spawn_depth=2, parent_agent_id="a0")
    sub.user("task")
    sub.assistant()
    t.write()
    assert sub.path == t.path.parent / t.session_id / "subagents" / "agent-a1.jsonl"
    assert json.loads(sub.sidecar_path.read_text()) == {
        "agentType": "Explore", "description": "find it", "toolUseId": "toolu_7",
        "spawnDepth": 2, "parentAgentId": "a0"}
    lines = entries(sub.path)
    assert all(line["isSidechain"] is True for line in lines)
    assert all(line["agentId"] == "a1" for line in lines)
    assert all(line["sessionId"] == t.session_id for line in lines)


def test_a_workflow_agent_file_a_missing_sidecar_and_a_broken_one(projects):
    t = ClaudeTranscript(projects)
    flow = t.subagent("a2", workflow="wf_run1", sidecar=False)
    broken = t.subagent("a3", sidecar="{not json")
    flow.user("step")
    broken.user("step")
    t.write()
    assert flow.path == (t.path.parent / t.session_id / "subagents" / "workflows"
                         / "wf_run1" / "agent-a2.jsonl")
    assert flow.path.is_file() and not flow.sidecar_path.exists()
    assert broken.sidecar_path.read_text() == "{not json"


def test_flush_appends_only_new_lines_and_offsets_match(projects):
    t = ClaudeTranscript(projects)
    t.user("one")
    path = t.write(terminated=False)
    inode = path.stat().st_ino
    assert not path.read_bytes().endswith(b"\n")
    t.user("two")
    t.flush()
    assert path.stat().st_ino == inode
    data = path.read_bytes()
    assert data == t.text().encode()
    assert data[t.offset(2):].startswith(b'{"parentUuid"')
    assert t.offset(1) == 0


def test_a_compaction_writes_the_boundary_and_the_summary(projects):
    t = ClaudeTranscript(projects)
    number = t.compaction(trigger="manual", pre_tokens=9)
    boundary, summary = entries(t.write())
    assert number == 1
    assert (boundary["type"], boundary["subtype"]) == ("system", "compact_boundary")
    assert boundary["compactMetadata"] == {"trigger": "manual", "preTokens": 9}
    assert summary["isCompactSummary"] is True


def test_a_synthetic_line_has_no_request_id_or_requested_model(projects):
    t = ClaudeTranscript(projects)
    t.synthetic()
    (line,) = entries(t.write())
    assert line["message"]["model"] == "<synthetic>"
    assert "requestId" not in line and "requestedModel" not in line

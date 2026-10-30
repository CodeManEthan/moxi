"""Claude Code's transcript into the one shape, over files the test kit's builder writes.

These carry the mapping's rules: one request record per request key, its values from its
newest line, `final` until a line of another request or a turn's end follows; token
classes summed and split as the shape defines them; unknown for what Claude Code does not
write; every line yielded as some record, a line that will not read as `malformed`; and a
`kinds` filter that decodes fewer lines but gives the same records.
"""

from __future__ import annotations

import json

import pytest

from moxi_sessions import claude
from moxi_sessions.shape import (UNKNOWN, Malformed, Marker, Message, Other, Request,
                                 ToolCall, ToolResult)
from moxi_sessions.testing import ClaudeTranscript, usage


def read(path, **kw):
    return list(claude.read_records(path, **kw))


def requests(records):
    return [r for r in records if isinstance(r, Request)]


@pytest.fixture
def transcript(projects) -> ClaudeTranscript:
    return ClaudeTranscript(projects)


# --- requests ---------------------------------------------------------------------------

def test_a_request_maps_its_token_classes(transcript):
    transcript.user("go")
    transcript.assistant("ok", usage=usage(input=3, cache_read=900, cache_write=200,
                                           cache_write_1h=150, output=40, thinking=25,
                                           service_tier="priority", speed="fast"))
    (request,) = requests(read(transcript.write()))
    assert request.input_tokens == 3 + 900 + 200          # the whole prompt
    assert request.cache_read_input_tokens == 900
    assert request.cache_creation_input_tokens == 200
    assert request.cache_creation_1h_input_tokens == 150
    assert request.cache_creation_5m_input_tokens == 50
    assert request.output_tokens == 40
    assert request.reasoning_output_tokens == 25
    assert request.service_tier == "priority"
    assert request.speed == "fast"
    assert request.harness == "claude"
    assert request.session_id == transcript.session_id
    assert request.agent_id is None
    assert request.raw_kind == "assistant"


def test_the_one_hour_write_is_clamped_to_the_total(transcript):
    block = usage(cache_write=100)
    block["cache_creation"]["ephemeral_1h_input_tokens"] = 400
    transcript.assistant(usage=block)
    (request,) = requests(read(transcript.write()))
    assert request.cache_creation_1h_input_tokens == 100
    assert request.cache_creation_5m_input_tokens == 0


def test_with_no_split_written_the_tiers_are_unknown(transcript):
    transcript.assistant(usage=usage(cache_write=100, breakdown=False))
    (request,) = requests(read(transcript.write()))
    assert request.cache_creation_input_tokens == 100
    assert request.cache_creation_5m_input_tokens is UNKNOWN
    assert request.cache_creation_1h_input_tokens is UNKNOWN


def test_what_claude_code_does_not_write_is_unknown(transcript):
    transcript.assistant(usage=usage(thinking=None))
    (request,) = requests(read(transcript.write()))
    for name in ("reasoning_output_tokens", "cost_usd", "context_window", "start_time",
                 "provider"):
        assert getattr(request, name) is UNKNOWN, name


def test_model_is_the_requested_model_never_the_answering_one(transcript):
    transcript.assistant(model="claude-answered", requested_model="claude-asked")
    transcript.assistant(model="claude-answered", requested_model=None)
    first, second = requests(read(transcript.write()))
    assert (first.model, first.response_model) == ("claude-asked", "claude-answered")
    assert second.model is UNKNOWN
    assert second.response_model == "claude-answered"


def test_a_synthetic_request_is_marked_and_has_no_provider(transcript):
    transcript.synthetic()
    (request,) = requests(read(transcript.write()))
    assert request.synthetic is True
    assert request.provider is None
    assert request.response_model == "<synthetic>"
    assert request.input_tokens == 0
    message = [r for r in read(transcript.path) if isinstance(r, Message)][0]
    assert message.synthetic is True


def test_repeated_lines_give_one_request_with_the_newest_line_s_values(transcript):
    transcript.user("go")
    req = transcript.request()
    first = req.thinking()
    req.text("part one")
    call = req.tool_use("Read", {"file_path": "/w/a"})
    result = transcript.tool_result(call, "contents")
    last = req.text("part two")
    transcript.assistant("next")
    records = read(transcript.write())
    mine = [r for r in requests(records) if r.request_id == req.request_id]
    assert len(mine) == 1
    (request,) = mine
    assert request.at == {"line": last, "offset": transcript.offset(last)}
    assert request.time == pytest.approx(claude.parse_timestamp(
        json.loads(transcript.lines[last - 1])["timestamp"]))
    assert request.final is True
    # It comes where it ended: after its own lines and the tool result between them.
    position = records.index(request)
    assert all(r.at["line"] <= last for r in records[:position])
    assert records[position + 1].at["line"] == last + 1
    assert first < result < last


def test_a_request_ends_at_another_request_or_a_turn_end_and_the_last_is_not_final(
        transcript):
    transcript.user("go")
    a = transcript.assistant("a")
    b = transcript.assistant("b")
    transcript.turn_duration()
    transcript.user("again")
    c = transcript.assistant("c")
    finals = {r.request_id: r.final for r in requests(read(transcript.write()))}
    assert finals == {a.request_id: True, b.request_id: True, c.request_id: False}


def test_a_new_prompt_does_not_end_a_request(transcript):
    req = transcript.request()
    req.text("one")
    transcript.user("interjection")
    req.text("two")
    (request,) = requests(read(transcript.write()))
    assert request.final is False


@pytest.mark.parametrize("request_id, message_id, expected", [
    ("req_X", "msg_X", "req_X"),
    (None, "msg_X", "msg:msg_X"),
])
def test_the_request_key(transcript, request_id, message_id, expected):
    transcript.assistant(request_id=request_id, message_id=message_id)
    (request,) = requests(read(transcript.write()))
    assert request.request_id == expected


def test_the_request_key_falls_back_to_uuid_then_line():
    assert claude.request_key({"uuid": "u1", "message": {}}, 4) == "uuid:u1"
    assert claude.request_key({"message": {}}, 4) == "line:4"


def test_turn_id_is_the_prompt_the_line_is_in(transcript):
    transcript.user("one", prompt_id="p1")
    transcript.assistant("a")
    transcript.user("two", prompt_id="p2")
    transcript.assistant("b")
    first, second = requests(read(transcript.write()))
    assert (first.turn_id, second.turn_id) == ("p1", "p2")


def test_a_progress_line_s_nested_usage_is_not_read(transcript):
    transcript.other("progress", data={"message": {"type": "assistant", "message": {
        "model": "m", "usage": usage(input=999)}}})
    records = read(transcript.write())
    assert requests(records) == []
    assert isinstance(records[0], Other) and records[0].recognised is True


# --- content ----------------------------------------------------------------------------

def test_content_blocks_map_to_messages_tool_calls_and_results(transcript):
    transcript.user("please read it", origin="human")
    req = transcript.request()
    req.thinking("which file")
    req.text("reading")
    call = req.tool_use("mcp__notes__search", {"query": "cache lifetime"})
    transcript.tool_result(call, [{"type": "text", "text": "found"},
                                  {"type": "text", "text": "twice"}], is_error=True)
    records = [r for r in read(transcript.write()) if not isinstance(r, Request)]
    user, thought, text, tool_call, tool_result = records
    assert isinstance(user, Message)
    assert (user.role, user.text, user.origin) == ("user", "please read it", "human")
    assert user.reasoning is False and user.synthetic is False
    assert user.model is UNKNOWN
    assert thought.reasoning is True and thought.text == "which file"
    assert thought.content_kinds == ("thinking",)
    assert (text.role, text.text, text.content_kinds) == ("assistant", "reading", ("text",))
    assert isinstance(tool_call, ToolCall)
    assert tool_call.call_id == call
    assert tool_call.name == "mcp__notes__search"
    assert tool_call.server == "notes"
    assert tool_call.input == {"query": "cache lifetime"}
    assert tool_call.summary == "mcp__notes__search(cache lifetime)"
    assert isinstance(tool_result, ToolResult)
    assert tool_result.call_id == call
    assert tool_result.output == "found\ntwice"
    assert tool_result.is_error is True
    assert tool_result.size_bytes == len("found\ntwice")


def test_a_result_with_no_error_key_is_not_an_error(transcript):
    transcript.tool_result("toolu_1", "fine")
    (result,) = read(transcript.write())
    assert result.is_error is False
    assert result.call_id == "toolu_1"


def test_a_user_line_s_booleans_are_its_flags_and_stay_out_of_extra(transcript):
    transcript.user("skill body", isMeta=True)
    (message,) = read(transcript.write())
    assert message.flags == {"isMeta": True}
    assert "isMeta" not in message.extra and "isSidechain" not in message.extra
    assert message.origin == "external"          # userType when no origin is written


def test_extra_holds_the_keys_the_mapping_does_not_name(transcript):
    transcript.assistant()
    records = read(transcript.write())
    for record in records:
        assert record.extra["cwd"] == "/work/project"
        assert record.extra["version"] == "2.1.314"
        assert record.extra["entrypoint"] == "cli"
        for named in ("type", "timestamp", "sessionId", "message", "requestId",
                      "requestedModel", "isSidechain"):
            assert named not in record.extra
        assert record.raw is None


def test_keep_raw_hands_back_the_decoded_line(transcript):
    transcript.user("hi")
    (message,) = read(transcript.write(), keep_raw=True)
    assert message.raw == json.loads(transcript.lines[0])


# --- markers and other lines ------------------------------------------------------------

def test_compaction_turn_end_and_titles_are_markers(transcript):
    boundary = transcript.compaction(trigger="manual", pre_tokens=123456)
    transcript.turn_duration(ms=4321)
    transcript.title("my title")
    transcript.title("their title", ai=True)
    records = read(transcript.write())
    compaction, summary, turn_end, custom, ai = records
    assert isinstance(compaction, Marker)
    assert compaction.at["line"] == boundary
    assert (compaction.marker, compaction.trigger, compaction.pre_tokens) == (
        "compaction", "manual", 123456)
    assert compaction.raw_kind == "system/compact_boundary"
    assert isinstance(summary, Message) and summary.flags["isCompactSummary"] is True
    assert (turn_end.marker, turn_end.duration_ms) == ("turn_end", 4321)
    assert (custom.marker, custom.title, custom.source) == ("title", "my title", "custom")
    assert (ai.marker, ai.title, ai.source) == ("title", "their title", "ai")


def test_other_lines_say_whether_their_kind_is_known(transcript):
    transcript.other("attachment", attachment={"type": "date_change"})
    transcript.other("brand-new-kind")
    transcript.system("stop_hook_summary", hookCount=1)
    transcript.system("brand_new_subtype")
    records = read(transcript.write())
    assert [type(r) for r in records] == [Other] * 4
    assert [r.recognised for r in records] == [True, False, True, False]
    assert [r.raw_kind for r in records] == [
        "attachment", "brand-new-kind", "system/stop_hook_summary",
        "system/brand_new_subtype"]


# --- malformed lines --------------------------------------------------------------------

def test_lines_that_will_not_read_are_malformed_with_why(transcript):
    transcript.add_raw("{not json")
    transcript.add_raw("[1, 2]")
    transcript.add({"type": "assistant", "message": "not an object", "sessionId": "s"})
    transcript.add({"type": "user", "message": None})
    transcript.add_raw("{\"type\": \"user\", \"cut")
    records = read(transcript.write(terminated=False))
    assert [type(r) for r in records] == [Malformed] * 5
    assert [r.why for r in records] == ["undecodable", "not_object", "bad_structure",
                                        "bad_structure", "undecodable"]
    assert records[0].raw == b"{not json"
    assert [r.unterminated for r in records] == [False] * 4 + [True]
    assert records[2].raw_kind == "assistant"


def test_a_decodable_unterminated_last_line_reads_as_its_kind(transcript):
    transcript.user("hi")
    (record,) = read(transcript.write(terminated=False))
    assert isinstance(record, Message)


def test_bytes_appended_after_the_read_opened_are_not_read(transcript):
    transcript.user("one")
    path = transcript.write()
    reader = claude.read_records(path)
    first = next(reader)
    transcript.user("two")
    transcript.flush()
    assert list(reader) == []
    assert first.text == "one"


# --- subagent files ---------------------------------------------------------------------

def test_a_subagent_file_s_records_carry_its_agent_and_are_sidechain(transcript):
    sub = transcript.subagent("a77", tool_use_id="toolu_9")
    sub.user("task")
    sub.assistant("done")
    transcript.write()
    records = read(sub.path, agent_id="a77")
    (request,) = requests(records)
    assert request.sidechain is True
    assert request.session_id == transcript.session_id
    assert {r.agent_id for r in records} == {"a77"}
    assert {r.agent_id for r in read(sub.path)} == {"a77"}      # from the line itself


# --- the forward mapper across reads ----------------------------------------------------

def feed(mapper, transcript, first, last):
    """Lines first..last (1-based, inclusive) through `mapper`, as one read."""
    out = []
    for number in range(first, last + 1):
        raw = (transcript.lines[number - 1] + "\n").encode()
        out.extend(mapper.feed(raw, line=number, offset=transcript.offset(number)))
    return out


def test_a_request_split_across_reads_comes_again_whole_and_nothing_else_does(
        transcript):
    transcript.user("go")
    req = transcript.request()
    req.text("one")
    call = req.tool_use("Bash", {"command": "ls"})
    transcript.tool_result(call, "a b")
    req.text("two")
    other = transcript.assistant("next")

    first_read = claude.Mapper()
    batch1 = feed(first_read, transcript, 1, 3) + first_read.finish()
    assert [r.request_id for r in requests(batch1)] == [req.request_id]
    assert requests(batch1)[0].final is False
    assert first_read.open_key == req.request_id

    second_read = claude.Mapper(prompt_id=first_read.prompt_id,
                                open_key=first_read.open_key)
    batch2 = feed(second_read, transcript, 4, 6) + second_read.finish()
    lines1 = {r.at["line"] for r in batch1 if not isinstance(r, Request)}
    lines2 = {r.at["line"] for r in batch2 if not isinstance(r, Request)}
    assert not lines1 & lines2                     # no line's records come twice
    again = [r for r in requests(batch2) if r.request_id == req.request_id]
    assert len(again) == 1 and again[0].final is True
    assert again[0].at["line"] == 5
    assert second_read.closed == []                # yielded final, so not also closed
    assert requests(batch2)[-1].request_id == other.request_id
    assert all(r.turn_id == first_read.prompt_id for r in batch2)


def test_a_request_left_open_and_ended_with_no_new_line_is_closed(transcript):
    transcript.user("go")
    req = transcript.assistant("a")
    transcript.turn_duration()

    first_read = claude.Mapper()
    feed(first_read, transcript, 1, 2)
    first_read.finish()
    second_read = claude.Mapper(prompt_id=first_read.prompt_id,
                                open_key=first_read.open_key)
    batch = feed(second_read, transcript, 3, 3) + second_read.finish()
    assert requests(batch) == []
    assert second_read.closed == [req.request_id]
    assert second_read.open_key is None


def test_a_request_still_open_after_a_read_with_no_line_of_it_stays_open(transcript):
    transcript.user("go")
    req = transcript.request()
    req.tool_use("Bash", {"command": "ls"})
    transcript.tool_result("toolu_x", "a b")

    first_read = claude.Mapper()
    feed(first_read, transcript, 1, 2)
    first_read.finish()
    second_read = claude.Mapper(prompt_id=first_read.prompt_id,
                                open_key=first_read.open_key)
    batch = feed(second_read, transcript, 3, 3) + second_read.finish()
    assert requests(batch) == []
    assert second_read.closed == []
    assert second_read.open_key == req.request_id


# --- the kinds filter -------------------------------------------------------------------

def every_kind_transcript(root) -> ClaudeTranscript:
    t = ClaudeTranscript(root)
    t.title("t")
    t.user("go")
    req = t.request(usage_block=usage(thinking=3))
    req.thinking()
    req.text("one")
    call = req.tool_use("Read", {"file_path": "/w/a"})
    t.tool_result(call, "x")
    req.text("two")
    t.assistant("b")
    t.turn_duration()
    t.user("again")
    t.synthetic()
    t.add_raw("{broken")
    t.other("attachment")
    t.compaction()
    t.assistant("after compaction")
    t.other("progress", data={"message": {"message": {"usage": usage()}}})
    t.title("ai", ai=True)
    t.user("last")
    t.request().text("open at the end")
    return t


@pytest.mark.parametrize("kinds", [{"request"}, {"marker"}, {"request", "marker"},
                                   {"limits"}, {"request", "message"}])
def test_the_kinds_filter_gives_the_same_records_final_and_closed(projects, kinds):
    t = every_kind_transcript(projects)
    path = t.write()
    whole = [r for r in read(path) if r.kind in kinds]
    assert read(path, kinds=kinds) == whole
    # Split into two reads at every line: the same records, `final` and `closed`.
    for split in range(1, len(t.lines)):
        results = []
        for prefilter in (False, True):
            first, out = claude.Mapper(), []
            for number in range(1, len(t.lines) + 1):
                if number == split + 1:
                    out.extend(first.finish())
                    first = claude.Mapper(prompt_id=first.prompt_id,
                                          open_key=first.open_key)
                raw = (t.lines[number - 1] + "\n").encode()
                if prefilter and not claude.needs_decode(raw, kinds):
                    continue
                out.extend(first.feed(raw, line=number, offset=t.offset(number)))
            out.extend(first.finish())
            # The request state matters only when requests are asked for.
            state = (first.closed, first.open_key) if "request" in kinds else ()
            results.append(([r for r in out if r.kind in kinds], state))
        assert results[0] == results[1], split


def test_the_filter_decodes_fewer_lines(projects):
    t = every_kind_transcript(projects)
    raws = [(line + "\n").encode() for line in t.lines]
    decoded = [raw for raw in raws if claude.needs_decode(raw, {"request"})]
    assert 0 < len(decoded) < len(raws)
    assert all(claude.needs_decode(raw, None) for raw in raws)
    assert all(claude.needs_decode(raw, {"message"}) for raw in raws)

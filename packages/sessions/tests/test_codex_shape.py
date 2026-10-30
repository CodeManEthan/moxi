"""Codex's rollout into the one shape, over files the test kit's builder writes.

These carry the mapping's rules: a request per `token_usage_record` (or, in a legacy
version's rollout, per counted `token_count`), its model from its own turn and its
provider and window from the newest line that said them; a limits record for every
`token_count` carrying `rate_limits`, and the same record from the app-server's payload;
every line yielded as some record, recognised or not by the lists, a line that will not
read as `malformed`; a state that carries between reads; a `kinds` filter that decodes
fewer lines but gives the same records; and the three import marks, each as found.
"""

from __future__ import annotations

import json

import pytest

from moxi_sessions import codex
from moxi_sessions.shape import (UNKNOWN, Limits, Malformed, Marker, Message, Other,
                                 Request, ToolCall, ToolResult)
from moxi_sessions.testing import CodexRollout, rate_limits, token_usage, write_imports


def read(path, **kw):
    return list(codex.read_records(path, **kw))


def of(records, cls):
    return [r for r in records if isinstance(r, cls)]


def markers(records, name):
    return [r for r in records if isinstance(r, Marker) and r.marker == name]


@pytest.fixture
def rollout(sessions) -> CodexRollout:
    return CodexRollout(sessions, provider="openai")


# --- requests ---------------------------------------------------------------------------

def test_a_request_maps_its_token_classes(rollout):
    turn = rollout.turn("model-a", context_window=200000)
    rid = rollout.request(token_usage(input=1200, cached=1000, cache_write=0, output=40,
                                      reasoning=15))
    [req] = of(read(rollout.write()), Request)
    assert req.request_id == rid
    assert (req.input_tokens, req.cache_read_input_tokens, req.cache_creation_input_tokens,
            req.output_tokens, req.reasoning_output_tokens) == (1200, 1000, 0, 40, 15)
    assert req.model == "model-a" and req.provider == "openai"
    assert req.context_window == 200000
    assert req.turn_id == turn and req.session_id == rollout.thread_id
    assert req.final is True and req.sidechain is False and req.synthetic is False
    assert req.raw_kind == "token_usage_record" and req.at == {"line": 4,
                                                                "offset": rollout.offset(4)}
    assert req.time == pytest.approx(1793354400.0 + 3)


def test_what_codex_does_not_write_is_unknown(rollout):
    rollout.turn()
    rollout.request()
    [req] = of(read(rollout.write()), Request)
    for name in ("response_model", "cache_creation_5m_input_tokens",
                 "cache_creation_1h_input_tokens", "cost_usd", "start_time", "speed",
                 "service_tier"):
        assert getattr(req, name) is UNKNOWN, name


def test_the_thread_s_own_key_rides_in_extra(rollout):
    rollout.turn()
    rollout.request()
    [req] = of(read(rollout.write()), Request)
    assert req.extra["thread_id"] == rollout.thread_id
    assert req.extra["session_id"] == rollout.thread_id
    assert "usage" not in req.extra and "response_id" not in req.extra
    assert req.extra["ordinal"] == 3


def test_provider_is_the_newest_settings_at_or_before_else_line_one(sessions):
    r = CodexRollout(sessions, provider="line-one")
    r.turn()
    r.request(response_id="a")
    r.settings(provider="later")
    r.request(response_id="b")
    reqs = of(read(r.write()), Request)
    assert [q.provider for q in reqs] == ["line-one", "later"]


def test_model_is_the_request_s_own_turn_s_context(rollout):
    first = rollout.turn("model-a")
    rollout.turn("model-b")
    rollout.line("token_usage_record", {"thread_id": rollout.thread_id,
                                        "session_id": rollout.thread_id,
                                        "turn_id": first, "response_id": "late",
                                        "usage": token_usage()})
    rollout.line("token_usage_record", {"turn_id": "turn-never-seen",
                                        "response_id": "lost", "usage": token_usage()})
    reqs = of(read(rollout.write()), Request)
    assert [(q.request_id, q.model) for q in reqs] == [("late", "model-a"),
                                                       ("lost", UNKNOWN)]
    assert reqs[0].turn_id == first


def test_context_window_is_the_newest_one_written(rollout):
    rollout.turn(context_window=100)
    rollout.request(response_id="a", context_window=300)
    rollout.request(response_id="b")
    reqs = of(read(rollout.write()), Request)
    assert [q.context_window for q in reqs] == [100, 300]


def test_service_tier_is_the_newest_one_written(rollout):
    rollout.turn()
    rollout.request(response_id="a")
    rollout.settings(service_tier="flex")
    rollout.request(response_id="b")
    rollout.turn(service_tier=None)
    rollout.request(response_id="c")
    reqs = of(read(rollout.write()), Request)
    assert [q.service_tier for q in reqs] == [UNKNOWN, "flex", None]


def test_a_request_with_no_response_id_is_keyed_by_thread_and_line(rollout):
    rollout.turn()
    number = rollout.line("token_usage_record", {"turn_id": rollout.turn_id,
                                                 "usage": token_usage()})
    [req] = of(read(rollout.write()), Request)
    assert req.request_id == f"tc:{rollout.thread_id}:{number}"


def test_a_usage_record_with_no_usage_object_is_malformed(rollout):
    rollout.turn()
    rollout.line("token_usage_record", {"turn_id": rollout.turn_id, "usage": "lots"})
    [bad] = of(read(rollout.write()), Malformed)
    assert bad.why == "bad_structure" and bad.raw_kind == "token_usage_record"
    assert bad.raw.startswith(b"{")


def test_a_record_mode_token_count_gives_limits_and_no_request(rollout):
    rollout.turn()
    rollout.request()
    records = read(rollout.write())
    assert len(of(records, Request)) == 1
    assert len(of(records, Limits)) == 1


# --- legacy mode -------------------------------------------------------------------------

@pytest.mark.parametrize("version", ["0.145.0", "0.146.0", "0.146.3"])
def test_a_legacy_rollout_s_requests_come_from_token_count(sessions, version):
    r = CodexRollout(sessions, cli_version=version)
    turn = r.turn("model-old")
    r.request(token_usage(input=500, cached=100, output=20))     # counted
    r.token_count(info=None)                                   # null info: none
    r.request(token_usage(input=500, cached=100, output=20))     # counted
    r.token_count(info=r.info)                                   # a repeat: none
    r.request(token_usage(input=0, cached=0, output=0, reasoning=0))  # zero: none
    path = r.write()
    reqs = of(read(path), Request)
    count_lines = [n for n, line in enumerate(r.lines, 1) if '"token_count"' in line]
    assert [q.request_id for q in reqs] == [f"tc:{r.thread_id}:{count_lines[0]}",
                                            f"tc:{r.thread_id}:{count_lines[2]}"]
    assert all(q.model == "model-old" and q.turn_id == turn for q in reqs)
    assert reqs[0].input_tokens == 500 and reqs[0].cache_read_input_tokens == 100
    assert reqs[0].raw_kind == "event_msg/token_count"
    assert "total_token_usage" in reqs[0].extra
    assert '"token_usage_record"' not in r.text()


def test_legacy_requests_come_through_the_request_filter(sessions):
    r = CodexRollout(sessions, cli_version="0.146.0")
    r.turn()
    r.request()
    r.user("more")
    r.request(token_usage(input=2000))
    path = r.write()
    whole = of(read(path), Request)
    assert len(whole) == 2
    assert read(path, kinds={"request"}) == whole


def test_an_unlisted_version_reads_in_record_mode(sessions):
    r = CodexRollout(sessions, cli_version="0.154.0-alpha.6.2", imported=True)
    r.turn()
    r.token_count(info={"last_token_usage": token_usage(input=0, cached=0, output=0),
                        "model_context_window": None}, limits=None)
    r.token_count(info={"last_token_usage": token_usage(), "model_context_window": None},
                  limits=None)
    assert of(read(r.write()), Request) == []


# --- limits ------------------------------------------------------------------------------

def test_every_rate_limits_reading_is_a_limits_record(rollout):
    rollout.turn()
    rollout.request(limits=rate_limits(primary=(5.0, 300, 111), secondary=(40.0, 10080, 222),
                                       reached="rate_limit_reached"))
    rollout.request(limits=rate_limits(limit_id="premium", primary=None, secondary=None))
    rollout.request(limits=rate_limits(primary=(7.0, 10080, 333), secondary=None,
                                       tertiary=(1.0, 60, 444)))
    limits = of(read(rollout.write()), Limits)
    assert len(limits) == 3
    first = limits[0]
    assert first.source == "rollout" and first.limit_id == "codex"
    assert first.observed_at == first.time and first.session_id == rollout.thread_id
    assert first.plan_type == "plus" and first.reached_type == "rate_limit_reached"
    assert first.limit_name is None
    assert [(w.name, w.used_percent, w.window_minutes, w.resets_at)
            for w in first.windows] == [("primary", 5.0, 300, 111),
                                        ("secondary", 40.0, 10080, 222)]
    assert first.extra["credits"]["has_credits"] is False
    assert "primary" not in first.extra and "limit_id" not in first.extra
    assert limits[1].limit_id == "premium" and limits[1].windows == ()
    assert [w.name for w in limits[2].windows] == ["primary", "tertiary"]
    assert limits[2].extra["secondary"] is None


def test_a_token_count_with_null_limits_and_no_request_is_recognised_other(rollout):
    rollout.turn()
    rollout.request(limits=None)
    [other] = [r for r in read(rollout.write()) if r.raw_kind == "event_msg/token_count"]
    assert isinstance(other, Other) and other.recognised is True


def test_app_server_limits_one_per_limit_id():
    payload = {
        "accountId": "acct",
        "rateLimits": {"limitId": "codex"},
        "rateLimitsByLimitId": {
            "codex": {"limitId": "codex", "limitName": None, "planType": "plus",
                      "primary": {"usedPercent": 12, "windowDurationMins": 300,
                                  "resetsAt": 1793370000},
                      "secondary": {"usedPercent": 40, "windowDurationMins": None,
                                    "resetsAt": None},
                      "credits": {"hasCredits": False, "unlimited": False},
                      "rateLimitReachedType": None},
            "premium": {"primary": None},
        },
    }
    codex_limits, premium = codex.limits_from_app_server(payload, 1793354400.5)
    assert codex_limits.source == "app-server" and codex_limits.harness == "codex"
    assert codex_limits.observed_at == 1793354400.5 == codex_limits.time
    assert codex_limits.session_id is None
    assert codex_limits.limit_id == "codex" and codex_limits.plan_type == "plus"
    assert codex_limits.limit_name is None and codex_limits.reached_type is None
    assert [(w.name, w.used_percent, w.window_minutes, w.resets_at)
            for w in codex_limits.windows] == [("primary", 12, 300, 1793370000),
                                               ("secondary", 40, None, None)]
    assert codex_limits.extra == {"credits": {"hasCredits": False, "unlimited": False}}
    assert premium.limit_id == "premium" and premium.windows == ()
    assert premium.plan_type is UNKNOWN and premium.extra == {"primary": None}


def test_app_server_limits_from_the_single_snapshot():
    payload = {"rateLimits": {"limitId": "codex", "primary": {"usedPercent": 3},
                              "secondary": None}}
    [reading] = codex.limits_from_app_server(payload, 5.0)
    assert reading.at == {"app_server": "rateLimits"}
    [window] = reading.windows
    assert window.used_percent == 3 and window.window_minutes is UNKNOWN
    assert codex.limits_from_app_server({"rateLimits": {}, "rateLimitsByLimitId": {}},
                                        5.0)[0].limit_id is UNKNOWN


@pytest.mark.parametrize("payload", [None, {}, {"rateLimits": None},
                                     {"rateLimitsByLimitId": {"codex": 3}}])
def test_app_server_limits_refuses_a_payload_with_no_snapshot(payload):
    with pytest.raises(ValueError):
        codex.limits_from_app_server(payload, 1.0)


# --- markers -----------------------------------------------------------------------------

def test_line_one_is_a_session_meta_marker(sessions):
    r = CodexRollout(sessions, meta_fields={"forked_from_id": "parent", "new_key": 1})
    [meta] = markers(read(r.write()), "session_meta")
    assert meta.history_mode == "paginated" and meta.originator == "codex_exec"
    assert meta.cli_version == "0.153.0" and meta.source == "exec"
    assert meta.model_provider == "openai"
    assert meta.context_window == {"window_id": r.thread_id[:-4] + "cafe"}
    assert meta.forked_from_id == "parent"
    assert meta.unlisted_keys == ("forked_from_id", "new_key")
    assert meta.extra["cwd"] == "/work/project" and meta.extra["id"] == r.thread_id
    assert meta.turn_id is UNKNOWN


def test_a_whole_line_one_lists_no_keys_and_says_no_fork(rollout):
    [meta] = markers(read(rollout.write()), "session_meta")
    assert meta.unlisted_keys == () and meta.forked_from_id is UNKNOWN


def test_settings_turn_and_rollback_markers(rollout):
    turn = rollout.turn("model-a", service_tier="fast")
    rollout.settings("model-b", provider="other", service_tier="flex")
    rollout.abort()
    rollout.rollback()
    rollout.turn_end(duration_ms=900)
    records = read(rollout.write())
    [start] = markers(records, "turn_start")
    assert start.model_context_window == 258400 and start.turn_id == turn
    context, applied = markers(records, "settings")
    assert (context.model, context.provider, context.service_tier) == (
        "model-a", UNKNOWN, "fast")
    assert (applied.model, applied.provider, applied.service_tier) == (
        "model-b", "other", "flex")
    assert applied.extra == {"ordinal": 3, "thread_id": rollout.thread_id}
    assert markers(records, "turn_aborted")[0].extra["reason"] == "interrupted"
    assert markers(records, "rollback")[0].turn_id == turn
    [end] = markers(records, "turn_end")
    assert end.duration_ms == 900


@pytest.mark.parametrize("kind", ["compacted", "context_compacted", "compaction",
                                  "context_compaction"])
def test_each_compaction_line_is_a_compaction_marker(rollout, kind):
    rollout.turn()
    rollout.compaction(kind)
    [mark] = markers(read(rollout.write()), "compaction")
    assert mark.trigger is UNKNOWN and mark.pre_tokens is UNKNOWN


# --- messages and tools ------------------------------------------------------------------

def test_messages_reasoning_tool_calls_and_results(rollout):
    turn = rollout.turn()
    rollout.user("read the notes")
    rollout.reasoning("which file?")
    call = rollout.tool_call("exec", "cat notes.md")
    rollout.tool_output(call, "the notes")
    fcall = rollout.tool_call("shell", {"command": "ls -la"}, kind="function_call")
    rollout.tool_output(fcall, {"content": "denied", "success": False},
                        kind="function_call_output")
    rollout.assistant("done")
    records = read(rollout.write())
    user, thought, said = of(records, Message)
    assert (user.role, user.text, user.content_kinds) == ("user", "read the notes",
                                                          ("input_text",))
    assert user.turn_id == turn and "internal_chat_message_metadata_passthrough" in \
        user.extra
    assert thought.reasoning is True and thought.text == "which file?"
    assert said.role == "assistant" and said.text == "done" and said.reasoning is False
    custom, function = of(records, ToolCall)
    assert (custom.call_id, custom.name, custom.input) == (call, "exec", "cat notes.md")
    assert custom.summary.startswith("exec(")
    assert function.input == json.dumps({"command": "ls -la"})
    assert function.summary == "shell(ls -la)"
    assert "arguments" not in function.extra and "call_id" not in function.extra
    out, failed = of(records, ToolResult)
    assert (out.call_id, out.output, out.is_error, out.size_bytes) == (
        call, "the notes", UNKNOWN, 9)
    assert (failed.output, failed.is_error) == ("denied", True)


def test_other_lines_say_whether_their_kind_is_known(rollout):
    rollout.turn()
    rollout.world_state()
    rollout.event("item_completed", item={"type": "UserMessage"})
    rollout.item("agent_message", content=[])
    rollout.line("realtime_item", {"x": 1})
    rollout.event("brand_new_event")
    rollout.item("brand_new_item")
    rollout.add({"timestamp": "2026-10-30T10:00:00Z", "payload": {}})
    others = of(read(rollout.write()), Other)
    assert [(o.raw_kind, o.recognised) for o in others] == [
        ("world_state", True), ("event_msg/item_completed", True),
        ("response_item/agent_message", True), ("realtime_item", False),
        ("event_msg/brand_new_event", False), ("response_item/brand_new_item", False),
        ("", False)]


def test_lines_that_will_not_read_are_malformed_with_why(rollout):
    rollout.add_raw("{broken")
    rollout.add_raw("[1, 2]")
    rollout.add({"timestamp": "2026-10-30T10:00:00Z", "type": "event_msg",
                 "payload": "text"})
    rollout.add({"timestamp": "2026-10-30T10:00:00Z", "type": "event_msg",
                 "payload": {"no": "type"}})
    rollout.add_raw('{"type": "event_msg", "payl')
    bad = of(read(rollout.write(terminated=False)), Malformed)
    assert [(b.why, b.unterminated) for b in bad] == [
        ("undecodable", False), ("not_object", False), ("bad_structure", False),
        ("bad_structure", False), ("undecodable", True)]
    assert bad[0].raw == b"{broken"
    assert bad[0].session_id == rollout.thread_id


def test_a_listed_line_without_its_time_as_text_is_malformed(rollout):
    """Codex writes every line with its `timestamp` as text: a line of a listed kind
    without one is bad structure, while a time that is text but not a time reads as an
    unknown time."""
    rollout.turn()
    rollout.add({"type": "turn_context", "payload": {"turn_id": "t", "model": "m"}})
    rollout.add({"timestamp": 1793354400, "type": "world_state", "payload": {}})
    rollout.add({"timestamp": "not a time", "type": "world_state", "payload": {}})
    records = read(rollout.write())
    bad = of(records, Malformed)
    assert [(b.raw_kind, b.why) for b in bad] == [("turn_context", "bad_structure"),
                                                  ("world_state", "bad_structure")]
    assert records[-1].raw_kind == "world_state" and records[-1].time is UNKNOWN


def test_keep_raw_hands_back_the_decoded_line(rollout):
    rollout.turn()
    records = read(rollout.write(), keep_raw=True)
    assert records[0].raw["type"] == "session_meta"
    assert all(r.raw is None for r in read(rollout.path))


def test_a_source_s_session_and_agent_ids_win(rollout):
    rollout.turn()
    rollout.request()
    records = read(rollout.write(), session_id="given", agent_id="child")
    assert {(r.session_id, r.agent_id) for r in records} == {("given", "child")}


def test_bytes_appended_after_the_read_opened_are_not_read(rollout):
    rollout.turn()
    path = rollout.write()
    reader = codex.read_records(path)
    first = next(reader)
    rollout.request()
    rollout.flush()
    rest = list(reader)
    assert first.marker == "session_meta"
    assert of(rest, Request) == []


def test_a_compressed_rollout_is_not_read(sessions):
    with pytest.raises(ValueError):
        list(codex.read_records(sessions / "rollout-x.jsonl.zst"))


# --- the state between reads -------------------------------------------------------------

def test_state_round_trips_through_json(rollout):
    for n in range(10):
        rollout.turn(f"model-{n}")
    rollout.settings(provider="p", service_tier="flex")
    rollout.request()
    mapper = codex.Mapper()
    for number, line in enumerate(rollout.lines, 1):
        mapper.feed(line, line=number, offset=rollout.offset(number))
    state = mapper.state
    assert len(state.models) == codex.TURNS_KEPT
    assert state.models[-1] == (rollout.turn_id, "model-9")
    again = codex.State.from_json(json.loads(json.dumps(state.to_json())))
    assert again == state
    assert codex.State.from_json({}) == codex.State()


@pytest.mark.parametrize("bad", [None, [], {"models": "x"}, {"models": [["t"]]},
                                 {"models": [[1, "m"]]}])
def test_state_from_json_refuses_what_it_did_not_write(bad):
    with pytest.raises(ValueError):
        codex.State.from_json(bad)


def every_kind_rollout(root, version="0.153.0") -> CodexRollout:
    r = CodexRollout(root, cli_version=version)
    r.world_state()
    r.turn("model-a")
    r.user("go")
    r.reasoning()
    call = r.tool_call()
    r.tool_output(call)
    r.request()
    r.settings(provider="other")
    r.request(token_usage(input=3000), limits=None)
    r.token_count(info=None)
    r.token_count(info=r.info)
    r.add_raw("{broken")
    r.event("item_completed", item={"type": "AgentMessage"})
    r.compaction()
    r.compaction("context_compacted")
    r.turn_end()
    r.turn("model-b", context_window=128000)
    r.request(token_usage(input=0, cached=0, output=0, reasoning=0))
    r.abort()
    r.rollback()
    r.compaction("compaction")
    r.turn("model-c")
    r.request(limits=rate_limits(limit_id="premium", primary=None, secondary=None))
    r.assistant("done")
    r.turn_end()
    return r


KIND_SETS = [{"request"}, {"marker"}, {"limits"}, {"request", "limits"},
             {"request", "limits", "marker"}, {"request", "message"}, {"malformed"}]


@pytest.mark.parametrize("version", ["0.153.0", "0.146.0"])
@pytest.mark.parametrize("kinds", KIND_SETS)
def test_the_kinds_filter_gives_the_same_records(sessions, version, kinds):
    r = every_kind_rollout(sessions, version)
    path = r.write()
    whole = [x for x in read(path) if x.kind in kinds]
    assert read(path, kinds=kinds) == whole
    # Split into two reads at every line, the state carried as JSON: the same records,
    # every request final and nothing closed.
    for split in range(1, len(r.lines)):
        results = []
        for prefilter in (False, True):
            mapper, out = codex.Mapper(), []
            for number in range(1, len(r.lines) + 1):
                if number == split + 1:
                    out.extend(mapper.finish())
                    state = codex.State.from_json(json.loads(json.dumps(
                        mapper.state.to_json())))
                    mapper = codex.Mapper(state=state)
                raw = (r.lines[number - 1] + "\n").encode()
                if prefilter and not codex.needs_decode(raw, kinds,
                                                        legacy=mapper.state.legacy):
                    continue
                out.extend(mapper.feed(raw, line=number, offset=r.offset(number)))
            out.extend(mapper.finish())
            assert mapper.closed == [] and mapper.open_key is None
            results.append([x for x in out if x.kind in kinds])
        assert results[0] == results[1] == whole, split
    assert all(q.final for q in of(whole, Request))


def test_the_filter_decodes_fewer_lines(sessions):
    r = every_kind_rollout(sessions)
    raws = [(line + "\n").encode() for line in r.lines]
    for kinds in ({"request"}, {"limits"}, {"marker"}):
        decoded = [raw for raw in raws if codex.needs_decode(raw, kinds)]
        assert 0 < len(decoded) < len(raws), kinds
    assert all(codex.needs_decode(raw, None) for raw in raws)
    assert all(codex.needs_decode(raw, {"tool_call"}) for raw in raws)


def test_legacy_mode_decodes_every_token_count(sessions):
    raw = b'{"type":"event_msg","payload":{"type":"token_count","info":null}}\n'
    assert not codex.needs_decode(raw, {"request"})
    assert codex.needs_decode(raw, {"request"}, legacy=True)
    assert codex.needs_decode(raw, {"marker"}, legacy=True)


# --- import marks ------------------------------------------------------------------------

def test_an_imported_thread_carries_all_three_marks(sessions):
    r = CodexRollout(sessions, imported=True, cli_version="0.154.0-alpha.6.2")
    r.turn()
    r.user("hello")
    path = r.write()
    write_imports(sessions, [r.thread_id, "someone-else"])
    imports = codex.read_imports(codex.imports_path(sessions))
    marks = codex.import_marks(codex.read_head(path), imports)
    assert (marks.originator, marks.turn_id, marks.listed) == (True, True, True)
    assert marks.raw_originator == "Codex Desktop" and marks.listed_reason is None
    assert marks.imports_path == str(sessions.parent / codex.IMPORTS_FILE)
    assert marks.as_dict()["listed"] is True


def test_a_thread_of_its_own_carries_none(rollout, sessions):
    rollout.turn()
    path = rollout.write()
    write_imports(sessions, ["someone-else"])
    marks = codex.import_marks(codex.read_head(path),
                               codex.read_imports(codex.imports_path(sessions)))
    assert (marks.originator, marks.turn_id, marks.listed) == (False, False, False)


def test_a_missing_imports_file_leaves_listed_unknown(rollout, sessions):
    rollout.turn()
    imports = codex.read_imports(codex.imports_path(sessions))
    assert imports.ids is None and imports.reason == "imports file not found"
    marks = codex.import_marks(codex.read_head(rollout.write()), imports)
    assert marks.listed is UNKNOWN and marks.listed_reason == "imports file not found"
    assert marks.as_dict()["unknown"] == ["listed"]


@pytest.mark.parametrize("text, reason", [
    ("{not json", "imports file unreadable: it is not JSON"),
    ("[1]", "imports file unreadable: it is not a JSON object"),
    ('{"other": []}', "imports file unreadable: it holds no records list"),
])
def test_an_imports_file_that_will_not_parse_says_why(sessions, text, reason):
    write_imports(sessions, text=text)
    imports = codex.read_imports(codex.imports_path(sessions))
    assert imports.ids is None and imports.reason == reason


def test_an_imports_file_that_cannot_be_read_says_why(sessions):
    codex.imports_path(sessions).mkdir()
    imports = codex.read_imports(codex.imports_path(sessions))
    assert imports.ids is None
    assert imports.reason.startswith("imports file unreadable: ")


def test_before_any_turn_the_turn_mark_is_unknown(rollout):
    head = codex.read_head(rollout.write())
    assert head.meta["id"] == rollout.thread_id and head.first_turn_id is None
    marks = codex.import_marks(head, codex.Imports("x", frozenset()))
    assert marks.turn_id is UNKNOWN and marks.originator is False


def test_with_no_line_one_the_originator_mark_is_unknown(sessions):
    r = CodexRollout(sessions, meta=False)
    r.turn()
    head = codex.read_head(r.write())
    assert head.meta is None and head.first_turn_id == r.turn_id
    marks = codex.import_marks(head, codex.Imports("x", frozenset({r.thread_id})))
    assert marks.originator is UNKNOWN and marks.raw_originator is UNKNOWN
    assert marks.listed is UNKNOWN
    assert codex.import_marks(head, codex.Imports("x", frozenset({r.thread_id})),
                              thread_id=r.thread_id).listed is True


def test_the_subagent_listing_has_a_reason_to_give():
    assert codex.SUBAGENTS_UNSUPPORTED.startswith("sessions does not yet find")

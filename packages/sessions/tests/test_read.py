"""The reads over one source: `records`, `records_back`, `last_request`, `records_since`
and `window`, on Claude Code transcripts, Codex rollouts and OpenCode databases built by
the test kit."""

from __future__ import annotations

import base64
import json
from dataclasses import replace
from pathlib import Path

import pytest

from moxi_sessions import claude, codex
from moxi_sessions.errors import Unreadable
from moxi_sessions.opencode import MissingSession
from moxi_sessions.read import (
    CURSOR_VERSION, last_request, records, records_back, records_since, window)
from moxi_sessions.shape import UNKNOWN, to_json
from moxi_sessions.source import Source
from moxi_sessions.testing import ClaudeTranscript, CodexRollout, OpenCodeDb
from moxi_sessions.testing.claude import usage
from moxi_sessions.testing.codex import CONTEXT_WINDOW, token_usage


# --- helpers -------------------------------------------------------------------------------

def plain(record, *, line: bool = True) -> str:
    """A record as sortable text; without `line`, its `at` keeps the offset alone, as a
    backward read gives it."""
    data = to_json(record)
    if not line and "line" in data.get("at", {}):
        data["at"] = {**data["at"], "line": None}
    return json.dumps(data, sort_keys=True, default=str)


def plains(recs, *, line: bool = True) -> list[str]:
    return [plain(r, line=line) for r in recs]


def requests(recs) -> list:
    return [r for r in recs if r.kind == "request"]


def claude_source(t: ClaudeTranscript) -> Source:
    return Source("claude", str(t.path), t.session_id, t.agent_id)


def codex_source(r: CodexRollout) -> Source:
    return Source("codex", str(r.path), r.thread_id)


def every_claude_line(t: ClaudeTranscript) -> ClaudeTranscript:
    """A transcript with a line of every kind the builder writes."""
    t.other("file-history-snapshot", messageId="m0", snapshot={})
    t.user("read the notes")
    req = t.request(usage_block=usage(input=3, cache_read=900, output=40))
    req.thinking("which file?")
    call = req.tool_use("Read", {"file_path": "/work/project/notes.md"})
    t.tool_result(call, "the notes")
    call2 = req.tool_use("Bash", {"command": "ls"})
    t.other("attachment", attachment={"type": "x"})
    t.tool_result(call2, "a b", is_error=False)
    # A `promptId` nested in a tool's result names no turn of this transcript.
    t.tool_result(call2, "c", toolUseResult={"promptId": "a-nested-prompt"})
    req.text("read them")
    t.assistant("done", usage=usage(output=12))
    t.turn_duration()
    t.title("A title")
    t.title("An ai title", ai=True)
    t.user("again", isMeta=True)
    t.synthetic()
    t.compaction()
    t.assistant("no ids", request_id=None)
    t.assistant("a side line").block({"type": "text", "text": "side"}, isSidechain=True)
    t.add_raw("{not json")
    t.add_raw("[1, 2]")
    t.add({"type": "assistant", "message": "not an object", "sessionId": t.session_id})
    t.assistant("no usage", usage=None)
    t.system("local_command", content="ls")
    t.user("third")
    t.assistant("last")
    return t


def every_codex_line(r: CodexRollout) -> CodexRollout:
    """A rollout with a line of every kind the builder writes."""
    r.settings(provider="openai")
    r.world_state()
    r.turn()
    r.user("read the notes")
    r.reasoning()
    call = r.tool_call("exec", "cat notes.md")
    r.tool_output(call, "the notes")
    r.request(token_usage(input=1200, cached=1000, output=40))
    r.request(token_usage(input=1300, cached=1200, output=5))
    r.assistant("done")
    r.token_count(info=None)
    r.turn_end()
    r.turn(model="codex-model-y")
    r.request()
    r.compaction("compacted")
    r.compaction("context_compacted")
    r.compaction("compaction")
    r.abort()
    r.rollback()
    r.add_raw("{not json")
    r.turn()
    r.request()
    r.settings(provider="other-provider")
    r.request()
    r.turn_end()
    return r


@pytest.fixture
def projects(tmp_path) -> Path:
    return tmp_path / "projects"


@pytest.fixture
def codex_root(tmp_path) -> Path:
    return tmp_path / "codex" / "sessions"


@pytest.fixture
def db(tmp_path) -> OpenCodeDb:
    return OpenCodeDb(tmp_path / "opencode")


# --- records -------------------------------------------------------------------------------

def test_records_are_the_mappings_own_forward_read(projects, codex_root):
    t = every_claude_line(ClaudeTranscript(projects))
    t.write()
    got = list(records(claude_source(t)))
    assert plains(got) == plains(claude.read_records(t.path, session_id=t.session_id))
    r = every_codex_line(CodexRollout(codex_root))
    r.write()
    got = list(records(codex_source(r), kinds={"request", "limits"}))
    assert plains(got) == plains(codex.read_records(r.path, session_id=r.thread_id,
                                                    kinds={"request", "limits"}))


def test_records_come_with_the_header_from_the_files_head(projects, codex_root):
    t = ClaudeTranscript(projects, cwd="/work/here", version="2.1.314", entrypoint="sdk-cli")
    t.user()
    t.assistant()
    t.write()
    with records(claude_source(t)) as read:
        assert read.header.cwd == "/work/here"
        assert read.header.entrypoint == "sdk-cli"
        assert len(list(read)) == 3
    r = CodexRollout(codex_root, source="vscode")
    r.turn()
    r.request()
    r.write()
    header = records(codex_source(r)).header
    assert header.codex_source == "vscode" and header.version == "0.153.0"
    assert header.session_id == r.thread_id


def test_an_unreadable_source_raises_unreadable_never_an_empty_read(tmp_path, codex_root):
    with pytest.raises(Unreadable) as missing:
        records(Source("claude", str(tmp_path / "gone.jsonl"), "s"))
    assert "cannot be read" in missing.value.reason
    zst = codex_root / "2026" / "10" / "30" / "rollout-x.jsonl.zst"
    zst.parent.mkdir(parents=True)
    zst.write_bytes(b"\x28\xb5\x2f\xfd")
    for read in (records, records_back, last_request, records_since, window):
        with pytest.raises(Unreadable, match="zstd"):
            read(Source("codex", str(zst), "t"))


def test_a_source_read_has_no_listed_size_bound_but_a_listing_entry_does(projects):
    class Entry:
        def __init__(self, source, size):
            self.source, self.size = source, size

    t = ClaudeTranscript(projects)
    t.user()
    t.assistant("first")
    t.write()
    listed = Entry(claude_source(t), t.path.stat().st_size)
    t.assistant("written after the listing")
    t.flush()
    assert len(requests(records(claude_source(t)))) == 2
    assert len(requests(records(listed))) == 1
    assert len(requests(records_back(listed))) == 1
    assert last_request(listed).request.request_id == requests(records(listed))[0].request_id


def test_opencode_records_read_the_session_in_one_snapshot(db):
    s = db.session(title="the title")
    s.user()
    a = s.assistant()
    a.step(input=5, output=3, text="hi")
    a.complete()
    path = db.write()
    with records(Source("opencode", str(path), s.id)) as read:
        assert read.header.title == "the title"
        kinds = [r.kind for r in read]
    assert "request" in kinds and kinds[0] == "message"
    with pytest.raises(MissingSession):
        records(Source("opencode", str(path), "ses_missing"))
    with pytest.raises(ValueError):
        records(Source("opencode", str(path), None))


# --- records_back --------------------------------------------------------------------------

def test_a_backward_read_gives_the_forward_records_newest_first(projects):
    t = every_claude_line(ClaudeTranscript(projects))
    t.write()
    src = claude_source(t)
    forward = list(records(src))
    back = records_back(src)
    got = list(back)
    assert back.ended == "start"
    assert sorted(plains(got, line=False)) == sorted(plains(forward, line=False))
    # One request per key, newest first, with the forward read's values and `final`.
    assert [r.request_id for r in requests(got)] == \
        [r.request_id for r in reversed(requests(forward))]
    assert plains(requests(got), line=False) == \
        plains(reversed(requests(forward)), line=False)
    offsets = [r.at["offset"] for r in got if r.kind != "request"]
    assert offsets == sorted(offsets, reverse=True)


def test_a_request_keyed_by_its_line_number_keeps_the_forward_key(projects):
    t = ClaudeTranscript(projects)
    t.user()
    t.add({"type": "assistant", "sessionId": t.session_id, "timestamp": "2026-10-30T10:00Z",
           "message": {"role": "assistant", "model": "m", "content": [],
                       "usage": usage()}})
    t.assistant("after")
    t.write()
    forward = requests(records(claude_source(t)))
    back = requests(records_back(claude_source(t)))
    assert forward[0].request_id == "line:2"
    assert back[1].request_id == "line:2" and back[1].at == {"line": 2,
                                                             "offset": t.offset(2)}
    assert back[0].at["line"] is None


def test_a_backward_codex_read_maps_each_turn_from_its_own_start(codex_root):
    for version in ("0.153.0", "0.146.0"):
        r = every_codex_line(CodexRollout(codex_root / version, cli_version=version))
        r.write()
        src = codex_source(r)
        forward = list(records(src))
        back = records_back(src)
        got = list(back)
        assert back.ended == "start"
        assert sorted(plains(got, line=False)) == sorted(plains(forward, line=False))
        assert [r.request_id for r in requests(got)] == \
            [r.request_id for r in reversed(requests(forward))]
        with_head = records_back(src, head=True)
        rest = list(with_head)
        assert [m.marker for m in with_head.first] == ["session_meta"]
        assert len(rest) == len(got) - 1


def test_a_legacy_codex_backward_read_keeps_the_repeat_rule_across_turns(codex_root):
    r = CodexRollout(codex_root, cli_version="0.145.0")
    r.turn()
    r.request()
    r.turn()
    r.token_count(info=r.info)                 # a repeat of the turn before's reading
    r.request(token_usage(input=7, output=1))
    r.write()
    forward = requests(records(codex_source(r)))
    back = requests(records_back(codex_source(r), kinds={"request"}))
    assert [q.request_id for q in back] == [q.request_id for q in reversed(forward)]
    assert len(back) == 2


def test_a_backward_read_with_kinds_gives_the_same_records_as_a_whole_one(projects,
                                                                          codex_root):
    t = every_claude_line(ClaudeTranscript(projects))
    t.write()
    r = every_codex_line(CodexRollout(codex_root))
    r.write()
    for src in (claude_source(t), codex_source(r)):
        whole = list(records_back(src))
        for kinds in ({"request"}, {"limits"}, {"marker"}, {"request", "message"}):
            assert plains(records_back(src, kinds=kinds)) == \
                plains(x for x in whole if x.kind in kinds)


def test_a_backward_read_stopped_by_its_limit_says_so(projects):
    t = ClaudeTranscript(projects)
    for n in range(20):
        t.user(f"turn {n}")
        t.assistant(f"answer {n}")
    t.write()
    back = records_back(claude_source(t), block=512, limit=1024)
    got = list(back)
    assert back.ended == "limit" and 0 < len(got) < 60
    # Lines cut off from the line that names their turn have no turn.
    assert got[-1].turn_id is UNKNOWN or got[-1].raw_kind == "user"


def test_an_unterminated_last_line_that_does_not_decode_is_malformed(projects):
    t = ClaudeTranscript(projects)
    t.user()
    t.add_raw('{"type": "assistant", "mess')
    t.write(terminated=False)
    first = next(iter(records_back(claude_source(t))))
    assert first.kind == "malformed" and first.unterminated is True


def test_opencode_records_back_is_the_exact_reverse_with_line_one_as_first(db):
    s = db.session()
    s.user("one")
    a = s.assistant()
    a.step(input=5, output=3)
    a.complete()
    s.user("two")
    path = db.write()
    src = Source("opencode", str(path), s.id)
    forward = plains(records(src))
    back = records_back(src)
    assert plains(back) == list(reversed(forward)) and back.ended == "start"
    headed = records_back(src, head=True)
    assert [r.kind for r in headed.first] == ["message"]
    assert plains(headed) == list(reversed(forward))[:-1]


# --- last_request --------------------------------------------------------------------------

def test_last_request_on_a_built_subagent_file_is_its_newest_request(projects):
    t = ClaudeTranscript(projects)
    t.user()
    t.assistant("main")
    sub = t.subagent("a0190b00000000aa")
    sub.user("do it")
    sub.assistant("first", usage=usage(input=5))
    newest = sub.assistant("second", usage=usage(input=9))
    t.write()
    got = last_request(claude_source(sub))
    assert got.request.request_id == newest.request_id
    assert got.request.sidechain is True and got.request.agent_id == "a0190b00000000aa"
    assert got.header.labels["agent_type"] == "general-purpose"


def test_last_request_on_a_main_transcript_passes_over_sidechain_and_synthetic(projects):
    t = ClaudeTranscript(projects)
    t.user()
    own = t.assistant("own", usage=usage(input=100))
    t.request().text("inline subagent", isSidechain=True)
    t.synthetic()
    t.write()
    src = claude_source(t)
    assert last_request(src).request.request_id == own.request_id
    assert last_request(src, synthetic=True).request.synthetic is True
    assert last_request(src).request.input_tokens == 100 + 10 + 0


def test_last_request_says_how_a_read_that_found_none_ended(projects):
    t = ClaudeTranscript(projects)
    t.user()
    t.write()
    got = last_request(claude_source(t))
    assert got.request is None and got.ended == "start"
    for n in range(10):
        t.user("x" * 200)
    t.flush()
    t2 = ClaudeTranscript(projects, session_id="0190b000-0000-7000-8000-00000000c1a1")
    t2.assistant("old")
    for n in range(10):
        t2.user("x" * 200)
    t2.write()
    got = last_request(claude_source(t2), limit=600, block=200)
    assert got.request is None and got.ended == "limit"


def test_codex_last_request_carries_its_turns_model_and_provider(codex_root):
    r = CodexRollout(codex_root, provider="first")
    r.turn(model="model-a")
    r.request()
    r.turn(model="model-b")
    newest = r.request()
    r.write()
    got = last_request(codex_source(r))
    assert got.request.request_id == newest
    assert got.request.model == "model-b" and got.request.provider == "first"
    assert got.request.context_window == CONTEXT_WINDOW


def test_opencode_last_request_leaves_an_open_step_out(db):
    s = db.session()
    s.user()
    a = s.assistant()
    start, _ = a.step(input=5, output=3)
    a.step_start()
    path = db.write()
    got = last_request(Source("opencode", str(path), s.id))
    assert got.request.request_id == start and got.request.final is True
    assert got.ended is None and got.header.session_id == s.id


# --- records_since: files ------------------------------------------------------------------

def cursor_of(obj: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()


def open_cursor(cursor: str) -> dict:
    return json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))


def test_reads_in_batches_give_every_record_once_and_requests_again(projects, codex_root):
    t = every_claude_line(ClaudeTranscript(projects))
    t.write()
    r = every_codex_line(CodexRollout(codex_root))
    r.write()
    for src in (claude_source(t), codex_source(r)):
        whole = records_since(src)
        assert whole.complete and not whole.reset
        cursor, got, final = None, [], {}
        while True:
            batch = records_since(src, cursor, max_bytes=1)
            if not batch.records and batch.cursor == cursor:
                break
            cursor = batch.cursor
            for rec in batch.records:
                if rec.kind == "request":
                    final[rec.request_id] = rec
                else:
                    got.append(rec)
            for key in batch.closed:
                final[key] = replace(final[key], final=True)
        assert plains(got) == plains(x for x in whole.records if x.kind != "request")
        assert sorted(plains(final.values())) == sorted(plains(requests(whole.records)))


def test_a_claude_request_split_by_tool_results_comes_back_once_per_new_line(projects):
    t = ClaudeTranscript(projects)
    t.user()
    t.write()
    src = claude_source(t)
    cursor = records_since(src).cursor
    req = t.request()
    steps = []
    call = req.tool_use("Read")
    steps.append("own")
    t.tool_result(call)
    steps.append("result")
    call = req.tool_use("Bash")
    steps.append("own")
    t.tool_result(call)
    steps.append("result")
    req.text("done")
    steps.append("own")
    t.assistant("next request")
    steps.append("next")
    lines = t.lines[:]
    t.lines = lines[:1]
    for n, step in enumerate(steps, start=2):
        t.lines = lines[:n]
        t.flush()
        batch = records_since(src, cursor)
        cursor = batch.cursor
        got = [(x.kind, getattr(x, "request_id", None)) for x in batch.records]
        if step == "own":
            assert got[-1] == ("request", req.request_id)
            assert batch.records[-1].final is False
            assert [k for k, _ in got[:-1]] in (["tool_call"], ["message"])
            assert batch.closed == ()
        elif step == "result":
            assert got == [("tool_result", None)] and batch.closed == ()
        else:
            assert batch.closed == (req.request_id,)
            assert req.request_id not in [key for _, key in got]


def test_a_request_yielded_final_is_never_also_named_closed(projects, codex_root, db):
    # Claude: a request left open, then a line of another request, then the first one's
    # line again (inline subagent lines of old versions interleave like this).
    t = ClaudeTranscript(projects)
    t.user()
    a = t.request()
    a.text("a, first line")
    t.write()
    src = claude_source(t)
    cursor = records_since(src).cursor
    b = t.request()
    b.text("b")
    a.text("a, again")
    t.assistant("c")
    t.flush()
    batch = records_since(src, cursor)
    yielded = {r.request_id: r.final for r in requests(batch.records)}
    assert yielded[a.request_id] is True
    assert a.request_id not in batch.closed
    # Codex: every request is final and nothing is closed.
    r = CodexRollout(codex_root)
    r.turn()
    r.request()
    r.write()
    first = records_since(codex_source(r))
    r.request()
    r.flush()
    batch = records_since(codex_source(r), first.cursor)
    assert all(q.final for q in requests(first.records + batch.records))
    assert first.closed == batch.closed == ()
    # OpenCode: an open step comes again whole, final, once it finishes; closed stays
    # empty.
    s = db.session()
    s.user()
    m = s.assistant()
    start = m.step_start()
    path = db.write()
    src = Source("opencode", str(path), s.id)
    first = records_since(src)
    assert [q.final for q in requests(first.records)] == [False]
    m.step_finish(input=4, output=2)
    db.write()
    batch = records_since(src, first.cursor)
    assert [(q.request_id, q.final) for q in requests(batch.records)] == [(start, True)]
    assert batch.closed == ()


def test_a_file_under_4_kib_that_grows_is_not_a_rewrite(projects):
    t = ClaudeTranscript(projects)
    t.user("hi")
    t.write()
    assert t.path.stat().st_size < 4096
    src = claude_source(t)
    first = records_since(src)
    assert open_cursor(first.cursor)["fp_len"] == t.path.stat().st_size
    for n in range(30):
        t.assistant(f"answer {n}")
    t.flush()
    assert t.path.stat().st_size > 4096
    batch = records_since(src, first.cursor)
    assert batch.reset is False
    assert [x.kind for x in batch.records][:1] == ["message"]
    assert all(x.at["line"] > 1 for x in batch.records)
    assert open_cursor(batch.cursor)["fp_len"] == 4096


def test_a_rewritten_file_reads_from_the_start_with_a_reset(projects):
    t = ClaudeTranscript(projects)
    t.user("hi")
    t.assistant("there")
    t.write()
    src = claude_source(t)
    cursor = records_since(src).cursor
    # Other bytes at the start, file as long or longer: the fingerprint tells.
    t.path.write_bytes(t.path.read_bytes().replace(b"hi", b"ho") + b"\n")
    batch = records_since(src, cursor)
    assert batch.reset and batch.records[0].at["line"] == 1
    # A shorter file.
    cursor = batch.cursor
    t.path.write_bytes(t.path.read_bytes()[: t.offset(2)])
    batch = records_since(src, cursor)
    assert batch.reset and [x.at["line"] for x in batch.records] == [1]
    # Another file at the path.
    cursor = batch.cursor
    data = t.path.read_bytes()
    t.path.unlink()
    t.path.write_bytes(data)
    assert records_since(src, cursor).reset


def test_nothing_new_reads_nothing_and_hands_the_cursor_back(projects):
    t = ClaudeTranscript(projects)
    t.user()
    t.assistant()
    t.write()
    src = claude_source(t)
    first = records_since(src)
    assert first.header.cwd == "/work/project"
    again = records_since(src, first.cursor)
    assert again.records == () and again.cursor == first.cursor
    assert again.header is None and not again.reset and again.complete


def test_an_unterminated_last_line_is_left_for_the_next_call(projects):
    t = ClaudeTranscript(projects)
    t.user()
    t.assistant("being written")
    t.write(terminated=False)
    src = claude_source(t)
    first = records_since(src)
    assert [x.at["line"] for x in first.records] == [1] and first.complete
    assert records_since(src, first.cursor).records == ()
    t.assistant("next")
    t.flush()
    batch = records_since(src, first.cursor)
    assert {x.at["line"] for x in batch.records} == {2, 3}


def test_max_bytes_bounds_a_call_but_always_reads_a_line(projects):
    t = ClaudeTranscript(projects)
    for n in range(3):
        t.user(f"line {n}")
    t.write()
    src = claude_source(t)
    batch = records_since(src, max_bytes=1)
    assert [x.at["line"] for x in batch.records] == [1] and not batch.complete
    batch = records_since(src, batch.cursor, max_bytes=t.offset(4))
    assert [x.at["line"] for x in batch.records] == [2, 3] and batch.complete
    with pytest.raises(ValueError):
        records_since(src, max_bytes=0)


@pytest.mark.parametrize("bad", [
    "not base64 !!", cursor_of([1, 2]), "bm90IGpzb24",              # not an object; not JSON
    cursor_of({"v": 0, "harness": "claude"}),                       # an older version
    cursor_of({"v": True, "harness": "claude"}),                     # not a number
    cursor_of({"harness": "claude"}),                                # no version
    cursor_of({"v": CURSOR_VERSION, "harness": "claude"}),          # fields missing
])
def test_a_cursor_the_reader_cannot_trust_reads_as_a_reset(projects, bad):
    t = ClaudeTranscript(projects)
    t.user()
    t.write()
    batch = records_since(claude_source(t), bad)
    assert batch.reset and [x.at["line"] for x in batch.records] == [1]


def test_a_cursor_for_another_harness_or_source_reads_as_a_reset(projects, codex_root):
    t = ClaudeTranscript(projects)
    t.user()
    t.write()
    other = ClaudeTranscript(projects, session_id="0190b000-0000-7000-8000-00000000c1a2")
    other.user("other")
    other.write()
    r = CodexRollout(codex_root)
    r.turn()
    r.write()
    claude_cursor = records_since(claude_source(t)).cursor
    codex_cursor = records_since(codex_source(r)).cursor
    assert records_since(codex_source(r), claude_cursor).reset
    assert records_since(claude_source(t), codex_cursor).reset
    moved = records_since(claude_source(other), claude_cursor)
    assert moved.reset and moved.records[0].at["line"] == 1
    # A Codex cursor whose mapping state does not parse.
    obj = open_cursor(codex_cursor)
    obj["state"] = {"models": "nope"}
    assert records_since(codex_source(r), cursor_of(obj)).reset


def test_a_cursor_from_a_newer_sessions_raises_whatever_else_it_holds(projects, db):
    t = ClaudeTranscript(projects)
    t.user()
    t.write()
    path = db.write()
    for newer in ({"v": CURSOR_VERSION + 1, "harness": "claude"},
                  {"v": CURSOR_VERSION + 1, "harness": "codex"},
                  {"v": CURSOR_VERSION + 1}):
        with pytest.raises(Unreadable) as raised:
            records_since(claude_source(t), cursor_of(newer))
        assert raised.value.reason == "cursor from a newer sessions"
        with pytest.raises(Unreadable):
            records_since(Source("opencode", str(path), None), cursor_of(newer))


# Every builder line, read in two batches split at each line, with and without the
# prefilter: the same records, `final` and `closed`.

KIND_SETS = ({"request"}, {"limits"}, {"marker"}, {"request", "limits"}, {"message"},
             {"tool_call", "tool_result"}, {"request", "marker", "other", "malformed"})


def two_batches(src: Source, data: bytes, cut: int, kinds):
    path = Path(src.path)
    path.write_bytes(data[:cut])
    first = records_since(src, kinds=kinds)
    with path.open("ab") as handle:
        handle.write(data[cut:])
    second = records_since(src, first.cursor, kinds=kinds)
    assert not second.reset
    return first, second


def check_prefilter(src: Source, builder) -> None:
    data = Path(src.path).read_bytes()
    cuts = [builder.offset(n) for n in range(2, len(builder.lines) + 1)]
    for cut in cuts:
        whole = two_batches(src, data, cut, None)
        for kinds in KIND_SETS:
            got = two_batches(src, data, cut, kinds)
            for full, part in zip(whole, got):
                assert plains(part.records) == \
                    plains(x for x in full.records if x.kind in kinds), (cut, kinds)
                if "request" in kinds:
                    assert part.closed == full.closed, (cut, kinds)


def test_every_claude_builder_line_reads_the_same_with_and_without_the_prefilter(projects):
    t = every_claude_line(ClaudeTranscript(projects))
    t.write()
    check_prefilter(claude_source(t), t)


def test_every_codex_builder_line_reads_the_same_with_and_without_the_prefilter(
        codex_root):
    for version in ("0.153.0", "0.146.0"):
        r = every_codex_line(CodexRollout(codex_root / version, cli_version=version))
        r.write()
        check_prefilter(codex_source(r), r)


def test_legacy_codex_requests_come_through_a_request_cursor(codex_root):
    r = CodexRollout(codex_root, cli_version="0.146.0")
    r.turn()
    r.request()
    r.request()
    r.write()
    batch = records_since(codex_source(r), kinds={"request"})
    assert [q.request_id.startswith("tc:") for q in batch.records] == [True, True]


# --- records_since: OpenCode ---------------------------------------------------------------

def test_an_opencode_cursor_read_across_a_deleted_tail_and_new_rows_misses_none(db):
    s = db.session()
    kept = s.user("kept")
    tail = [s.user(f"tail {n}") for n in range(3)]
    path = db.write()
    src = Source("opencode", str(path), s.id)
    first = records_since(src)
    assert open_cursor(first.cursor)["harness"] == "opencode"
    for message in tail:
        db.delete(message.id)
    db.write()
    fresh = [s.user(f"new {n}") for n in range(2)]
    db.write()
    batch = records_since(src, first.cursor)
    read_ids = {x.at["id"] for x in batch.records if x.at["table"] == "message"}
    assert read_ids == {m.id for m in fresh} and kept.id not in read_ids
    assert batch.gone == 3 and not batch.reset and batch.complete
    again = records_since(src, batch.cursor)
    assert again.records == () and again.gone == 0
    # The read-only open can touch the write-ahead log once; after that, a call with
    # nothing new reads nothing.
    still = records_since(src, again.cursor)
    assert still.records == () and still.header is None


def test_an_opencode_cursor_for_another_session_or_with_a_bad_body_resets(db):
    s = db.session()
    s.user()
    other = db.session()
    other.user()
    path = db.write()
    cursor = records_since(Source("opencode", str(path), s.id)).cursor
    assert records_since(Source("opencode", str(path), other.id), cursor).reset
    broken = cursor_of({"v": CURSOR_VERSION, "harness": "opencode", "db": {"ino": "x"}})
    batch = records_since(Source("opencode", str(path), s.id), broken)
    assert batch.reset and batch.records
    whole = records_since(Source("opencode", str(path), None))
    assert whole.header.session_id is None and len(whole.records) == 2


# --- window --------------------------------------------------------------------------------

def page_all(src: Source, turns: int, **kw) -> list[list]:
    pages = []
    before = None
    while True:
        w = window(src, turns=turns, before=before, **kw)
        pages.append(list(w.records))
        if not w.has_earlier:
            return pages
        before = w.before


def test_window_pages_back_through_every_turn_once(projects, codex_root):
    t = ClaudeTranscript(projects)
    t.other("file-history-snapshot", messageId="m0", snapshot={})
    for n in range(5):
        t.user(f"turn {n}")
        req = t.request()
        call = req.tool_use("Read")
        t.tool_result(call)
        req.text("done")
        t.turn_duration()
    t.write()
    r = CodexRollout(codex_root)
    for n in range(5):
        r.turn()
        r.user(f"turn {n}")
        r.request()
        r.turn_end()
    r.write()
    for src in (claude_source(t), codex_source(r)):
        whole = list(records(src))
        pages = page_all(src, 2)
        assert len(pages) == 3
        assert [len({x.turn_id for x in page if x.turn_id is not UNKNOWN})
                for page in pages] == [2, 2, 1]
        flat = [x for page in reversed(pages) for x in page]
        assert sorted(plains(flat, line=False)) == sorted(plains(whole, line=False))
        # Oldest record first within a page.
        offsets = [x.at["offset"] for x in pages[0]]
        assert offsets == sorted(offsets)


def test_a_window_cut_by_its_limit_leaves_out_the_turn_it_cut(projects):
    t = ClaudeTranscript(projects)
    # The limit is checked before each 64 KiB block, so the file is several blocks long.
    for n in range(30):
        t.user(f"turn {n} " + "x" * 3000)
        t.assistant("y" * 3000)
    t.write()
    w = window(claude_source(t), turns=50, limit=70000)
    assert w.ended == "limit" and w.has_earlier and w.before is not None
    assert all(x.turn_id is not UNKNOWN for x in w.records)
    users = [x for x in w.records if x.kind == "message" and x.role == "user"]
    assert len(users) == len({x.turn_id for x in w.records})


def test_opencode_window_pages_by_message(db):
    s = db.session()
    for n in range(3):
        s.user(f"turn {n}")
        a = s.assistant()
        a.step(input=5, output=3)
        a.complete()
    path = db.write()
    src = Source("opencode", str(path), s.id)
    first = window(src, turns=2)
    assert first.has_earlier and isinstance(first.before, str)
    second = window(src, turns=2, before=first.before)
    assert not second.has_earlier and second.before is None
    assert sorted(plains(second.records + first.records)) == sorted(plains(records(src)))
    with pytest.raises(ValueError):
        window(src, before="msg_nothing")
    with pytest.raises(ValueError):
        window(src, turns=0)

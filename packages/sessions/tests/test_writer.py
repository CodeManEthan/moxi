"""Who wrote a message (`api.writer`) and what a message opens with (`api.OPENING`,
`api.opening`).

The rule the tests carry: the writer comes from the record's role, `injected`,
`origin_kind` and flags, never from its text, and `peer` and `coordinator` only from the
line's own `origin` object. Every test of what a message opens with skips one set of
characters first, the same set in every place.
"""

from __future__ import annotations

import sys
import unicodedata

import pytest

from moxi_sessions import api, claude, codex
from moxi_sessions.opencode import Snapshot
from moxi_sessions.shape import UNKNOWN, Message, from_json, to_json
from moxi_sessions.testing import ClaudeTranscript, CodexRollout, OpenCodeDb


@pytest.fixture
def transcript(projects) -> ClaudeTranscript:
    return ClaudeTranscript(projects)


@pytest.fixture
def rollout(sessions) -> CodexRollout:
    return CodexRollout(sessions)


@pytest.fixture
def db(tmp_path) -> OpenCodeDb:
    return OpenCodeDb(tmp_path / "opencode")


def messages(recs) -> list[Message]:
    return [r for r in recs if isinstance(r, Message) and not r.reasoning]


def claude_user(transcript, text="Hello.", **fields) -> Message:
    transcript.user(text, **fields)
    return messages(claude.read_records(transcript.write()))[-1]


def codex_user(rollout, text="Hello.", **kw) -> Message:
    rollout.user(text, **kw)
    return messages(codex.read_records(rollout.write()))[-1]


def opencode_messages(db, session_id) -> list[Message]:
    with Snapshot(db.write()) as snap:
        return messages(snap.records(session_id))


# --- the opening characters ---------------------------------------------------------------

def _expected() -> set[str]:
    out = set()
    for code in range(sys.maxunicode + 1):
        c = chr(code)
        if (code <= 0x20 or 0x7F <= code <= 0xA0 or 0x2000 <= code <= 0x200A
                or code in (0x1680, 0x2028, 0x2029, 0x202F, 0x205F, 0x3000)
                or unicodedata.category(c) == "Cf"):
            out.add(c)
    return out


def test_opening_is_exactly_the_published_set():
    assert len(api.OPENING) == len(set(api.OPENING))
    assert set(api.OPENING) == _expected()


def test_opening_holds_every_space_python_takes_and_every_character_a_scrub_removes():
    chars = set(api.OPENING)
    for code in range(sys.maxunicode + 1):
        c = chr(code)
        if c.isspace():
            assert c in chars, hex(code)
        scrubbed = ((code < 0x20 and c not in "\n\t") or 0x7F <= code <= 0x9F
                    or unicodedata.category(c) == "Cf")
        if scrubbed:
            assert c in chars, hex(code)


def test_opening_skips_exactly_the_set_and_nothing_after_it():
    for c in api.OPENING:
        assert api.opening(c + "x") == "x", hex(ord(c))
    assert api.opening(api.OPENING + "<a>" + api.OPENING) == "<a>" + api.OPENING
    assert api.opening("") == "" and api.opening(api.OPENING) == ""
    assert api.opening("a b") == "a b"
    for c in "a<[.é一\U0001f600":
        assert api.opening(c) == c


def test_opening_skips_what_str_lstrip_and_sqlite_ltrim_leave():
    # A format character: lstrip keeps it.
    assert "​<bash-stdout>".lstrip() == "​<bash-stdout>"
    assert api.opening("​<bash-stdout>") == "<bash-stdout>"
    assert api.opening("﻿ \t\n<x") == "<x"
    assert api.opening("\U000e0041<x") == "<x"          # a tag character


def test_a_tag_is_lt_and_an_ascii_letter_after_the_opening_characters():
    assert api.opens_with_tag("​\n<command-name>/clear")
    assert api.opens_with_tag("<A")
    assert not api.opens_with_tag("< a")
    assert not api.opens_with_tag("<​a")       # a character inside the tag
    assert not api.opens_with_tag("<1")
    assert not api.opens_with_tag("fix <a>")
    assert not api.opens_with_tag("<")
    assert not api.opens_with_tag("")


def test_the_output_wrappers_are_both_harness_lists_and_the_shared_one():
    assert api.OUTPUT_WRAPPERS == (*claude.WRAPPER_TAGS, *codex.WRAPPER_TAGS,
                                   "<bash-stderr>", "<task-notification>", "<hook_prompt",
                                   "<user_shell_command>", "<system-reminder>",
                                   "<local-command-caveat>")
    assert api.opens_with_output(" <system-reminder>x")
    assert api.opens_with_output("​<hook_prompt hook=1>")
    assert not api.opens_with_output("why <system-reminder>?")


@pytest.mark.parametrize("lead", ["\n", "\t", " ", "​", "﻿", "⁠"])
def test_a_claude_wrapper_after_any_opening_character_marks_the_message_injected(
        transcript, lead):
    assert claude_user(transcript, lead + "<bash-stdout>x</bash-stdout>").injected is True


@pytest.mark.parametrize("lead", ["\n", " ", "​", "⁠"])
def test_a_codex_wrapper_after_any_opening_character_marks_the_message_injected(
        rollout, lead):
    text = lead + "<environment_context>x</environment_context>"
    assert codex_user(rollout, text).injected is True


def test_a_character_inside_the_tag_is_not_skipped(transcript):
    assert claude_user(transcript, "<​bash-stdout>x").injected is UNKNOWN


# --- the writer: Claude Code ---------------------------------------------------------------

@pytest.mark.parametrize("origin, expected", [
    ("human", "person"), ("peer", "peer"), ("coordinator", "coordinator"),
    ("channel", "channel"), ("plugin", "hook"), ("task-notification", "task"),
    ("some-new-kind", "unknown"),
])
def test_each_claude_origin_kind_names_its_writer(transcript, origin, expected):
    message = claude_user(transcript, "hi", origin=origin)
    assert message.origin_kind == origin
    assert api.writer(message) == expected


def test_a_claude_line_with_no_origin_is_an_unknown_writer(transcript):
    message = claude_user(transcript, "hi")
    assert message.origin_kind is None
    assert api.writer(message) == "unknown"


def test_a_user_type_of_peer_with_no_origin_object_is_never_peer_mail(transcript):
    plain = claude_user(transcript, "hi", userType="peer")
    assert plain.origin == "peer" and plain.origin_kind is None
    assert api.writer(plain) == "unknown"
    wrapped = claude_user(transcript, "<bash-stdout>x</bash-stdout>", userType="peer")
    assert wrapped.injected is True and api.writer(wrapped) == "harness"
    interrupted = claude_user(transcript, "[Request interrupted by user]", userType="peer")
    assert api.writer(interrupted) == "harness"


def test_the_writer_never_reads_the_text(transcript):
    assert api.writer(claude_user(transcript, "<system-reminder>x", origin="human")) == "person"
    assert api.writer(claude_user(transcript, "I am the person", origin="plugin")) == "hook"


def test_composed_claude_lines_are_the_harness_s_or_a_summary(transcript):
    assert api.writer(claude_user(transcript, "x", origin="human", isMeta=True)) == "harness"
    summary = claude_user(transcript, "This session is being continued",
                          isCompactSummary=True)
    assert api.writer(summary) == "summary"


def test_the_model_s_turn_thoughts_and_calls_are_the_model_s(transcript):
    transcript.user("go", origin="human")
    req = transcript.request()
    req.thinking("which file")
    req.text("reading")
    transcript.request().tool_use("Read", {"file_path": "/x"})
    transcript.synthetic("No response requested.")
    recs = list(claude.read_records(transcript.write()))
    thought = next(r for r in recs if isinstance(r, Message) and r.reasoning)
    turn, made_up = [m for m in messages(recs) if m.role == "assistant"]
    call = next(r for r in recs if r.kind == "tool_call")
    assert api.writer(thought) == "model" and api.writer(turn) == "model"
    assert api.writer(call) == "model"
    assert api.writer(made_up) == "harness"


def test_a_tool_result_and_a_marker_have_no_writer(transcript):
    req = transcript.request()
    call = req.tool_use("Read", {"file_path": "/x"})
    transcript.tool_result(call, "contents")
    transcript.turn_duration()
    recs = list(claude.read_records(transcript.write()))
    for record in recs:
        if record.kind in ("tool_result", "marker", "request"):
            assert api.writer(record) is None


def test_origin_kind_is_written_in_the_json_form_and_read_back(transcript):
    message = claude_user(transcript, "hi", origin="peer")
    assert from_json(to_json(message)).origin_kind == "peer"
    none = claude_user(transcript, "hi")
    assert to_json(none)["origin_kind"] is None
    assert from_json(to_json(none)).origin_kind is None


# --- the writer: Codex and OpenCode ---------------------------------------------------------

def test_codex_writers(rollout):
    assert api.writer(codex_user(rollout, "fix the build")) == "unknown"
    assert api.writer(codex_user(rollout, "fix", kinds=["user.text"])) == "unknown"
    assert api.writer(codex_user(rollout, "ctx", kinds=["agents_md.instructions"])) == "harness"
    assert api.writer(codex_user(rollout, "<environment_context>x")) == "harness"
    assert api.writer(codex_user(rollout, "rules", role="developer")) == "harness"
    assert api.writer(codex_user(rollout, "rules", role="system")) == "harness"
    answer = codex_user(rollout, "done", role="assistant")
    assert answer.origin_kind is UNKNOWN and api.writer(answer) == "model"


def test_opencode_writers(db):
    s = db.session()
    s.user("run the tests")
    synthetic = s.user(None)
    synthetic.text("Called the Read tool", synthetic=True)
    a = s.assistant()
    a.text("all green")
    summary = s.assistant(agent="compaction")
    summary.text("What we did so far")
    flagged = s.assistant(summary=True)
    flagged.text("Summed up")
    typed, added, answer, by_agent, by_flag = opencode_messages(db, s.id)
    assert api.writer(typed) == "unknown"
    assert api.writer(added) == "harness"
    assert api.writer(answer) == "model"
    assert by_agent.injected is False and api.writer(by_agent) == "summary"
    assert api.writer(by_flag) == "summary"


def test_every_writer_is_in_the_published_list():
    assert api.WRITERS == ("person", "model", "peer", "coordinator", "unknown", "channel",
                           "hook", "task", "harness", "summary")

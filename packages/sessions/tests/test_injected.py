"""Who submitted a message: `Message.injected` for Claude Code, Codex and OpenCode.

The rule the tests carry: the mark follows who submitted the message, read from the field
the harness writes about where it came from. True when that field names a submitter other
than the person at the session's prompt (the harness, a plugin, a channel, a peer session,
a background task), or the harness marks the message as one it composed or summarised;
false when it names the person, or the message is the model's own; `UNKNOWN` when the
harness writes no such field, or a value sessions does not know. A message's text never
makes it false. Where no source field is written, a fixed wrapper from each harness's
closed list makes it true. Each value and each wrapper has its own test, which names its
choice.
"""

from __future__ import annotations

import json

import pytest

from moxi_sessions import claude, codex
from moxi_sessions.opencode import Snapshot
from moxi_sessions.read import records, records_back
from moxi_sessions.shape import UNKNOWN, Message, from_json, to_json
from moxi_sessions.source import Source
from moxi_sessions.testing import ClaudeTranscript, CodexRollout, OpenCodeDb


def messages(recs) -> list[Message]:
    return [r for r in recs if isinstance(r, Message)]


def only(recs) -> Message:
    [message] = [m for m in messages(recs) if not m.reasoning]
    return message


def last(recs) -> Message:
    return [m for m in messages(recs) if not m.reasoning][-1]


@pytest.fixture
def transcript(projects) -> ClaudeTranscript:
    return ClaudeTranscript(projects)


@pytest.fixture
def rollout(sessions) -> CodexRollout:
    return CodexRollout(sessions)


@pytest.fixture
def db(tmp_path) -> OpenCodeDb:
    return OpenCodeDb(tmp_path / "opencode")


def claude_user(transcript: ClaudeTranscript, text="Hello.", **fields) -> Message:
    """The message of a user line added to the transcript, read back."""
    transcript.user(text, **fields)
    return last(claude.read_records(transcript.write()))


def codex_user(rollout: CodexRollout, text="Hello.", **kw) -> Message:
    """The message of a `response_item` message added to the rollout, read back."""
    rollout.user(text, **kw)
    return last(codex.read_records(rollout.write()))


def opencode_records(db: OpenCodeDb, session_id: str) -> list:
    with Snapshot(db.write()) as snap:
        return list(snap.records(session_id))


# --- Claude Code: origin.kind ------------------------------------------------------------

#: Every `origin.kind` value measured on Claude Code's transcripts, with its choice.
CLAUDE_ORIGINS = [
    ("human", False),             # the person typed it at the session's prompt
    ("plugin", True),             # a plugin submitted it, the person's words relayed too
    ("channel", True),            # a channel delivered it
    ("peer", True),               # another session sent it
    ("task-notification", True),  # a background task's result, its words in its own file
    ("coordinator", True),        # a coordinator sent it
]


def test_the_origin_list_is_exactly_the_values_tested():
    assert dict(CLAUDE_ORIGINS) == dict(claude.ORIGIN_KINDS)


@pytest.mark.parametrize("kind,choice", CLAUDE_ORIGINS, ids=[k for k, _ in CLAUDE_ORIGINS])
def test_each_claude_origin_names_its_choice(transcript, kind, choice):
    message = claude_user(transcript, "do the thing", origin=kind)
    assert message.injected is choice
    assert message.origin == kind


def test_an_origin_sessions_does_not_know_is_unknown(transcript):
    assert claude_user(transcript, "hi", origin="satellite").injected is UNKNOWN


def test_an_origin_with_no_kind_is_unknown_and_no_wrapper_decides_it(transcript):
    transcript.add({"type": "user", "sessionId": transcript.session_id,
                    "timestamp": "2026-11-01T10:00:00.000Z", "origin": {},
                    "message": {"role": "user", "content": "<bash-stdout>x</bash-stdout>"}})
    assert only(claude.read_records(transcript.write())).injected is UNKNOWN


def test_a_plugin_s_slash_command_line_is_injected_by_its_origin(transcript):
    text = "<command-name>/clear</command-name>\n<command-message>clear</command-message>"
    assert claude_user(transcript, text, origin="plugin").injected is True


def test_a_plugin_s_command_output_is_injected_by_its_origin(transcript):
    text = "<local-command-stdout>cleared</local-command-stdout>"
    assert claude_user(transcript, text, origin="plugin").injected is True


# --- Claude Code: the harness's own booleans ---------------------------------------------

def test_a_meta_line_is_injected_whatever_its_origin(transcript):
    assert claude_user(transcript, "context", isMeta=True).injected is True
    assert claude_user(transcript, "relay", origin="channel", isMeta=True).injected is True


def test_a_human_line_marked_meta_is_injected(transcript):
    # The harness's mark that it added the line wins over the origin it names.
    assert claude_user(transcript, "hi", origin="human", isMeta=True).injected is True


def test_a_compaction_summary_is_injected(transcript):
    message = claude_user(transcript, "This session is being continued from a previous "
                                      "one.", isCompactSummary=True)
    assert message.injected is True
    assert message.flags["isCompactSummary"] is True


def test_a_false_flag_marks_nothing(transcript):
    assert claude_user(transcript, "hi", origin="human", isMeta=False).injected is False


# --- Claude Code: no origin, and the closed list of wrappers ------------------------------

def test_the_claude_wrapper_list_is_exactly_the_tags_tested():
    assert claude.WRAPPER_TAGS == ("<local-command-stdout>", "<local-command-stderr>",
                                   "<bash-stdout>")
    assert claude.INTERRUPT_LINES == ("[Request interrupted by user]",
                                      "[Request interrupted by user for tool use]")


@pytest.mark.parametrize("text", [
    "<local-command-stdout>Set model to opus</local-command-stdout>",
    "<local-command-stderr>Error: no such command</local-command-stderr>",
    "<bash-stdout>3 files</bash-stdout><bash-stderr></bash-stderr>",
    "\n<local-command-stdout></local-command-stdout>",
], ids=["local-command-stdout", "local-command-stderr", "bash-stdout", "leading-blank"])
def test_each_claude_wrapper_marks_an_origin_less_message_injected(transcript, text):
    assert claude_user(transcript, text).injected is True


@pytest.mark.parametrize("line", ["[Request interrupted by user]",
                                  "[Request interrupted by user for tool use]"])
def test_each_interrupt_line_marks_an_origin_less_message_injected(transcript, line):
    assert claude_user(transcript, [{"type": "text", "text": line}]).injected is True


def test_an_interrupt_line_with_more_text_is_not_the_fixed_line(transcript):
    text = "[Request interrupted by user] and then I said more"
    assert claude_user(transcript, text).injected is UNKNOWN


def test_a_human_message_that_opens_with_a_wrapper_tag_stays_false(transcript):
    text = "<local-command-stdout>pasted this</local-command-stdout> why?"
    assert claude_user(transcript, text, origin="human").injected is False
    assert claude_user(transcript, "[Request interrupted by user]",
                       origin="human").injected is False


def test_a_typed_slash_command_line_is_unknown(transcript):
    # Claude Code writes no origin on a command line the person typed.
    text = "<command-name>/clear</command-name>\n<command-message>clear</command-message>"
    assert claude_user(transcript, text).injected is UNKNOWN


def test_a_shell_line_the_person_typed_is_unknown(transcript):
    assert claude_user(transcript, "<bash-input>ls</bash-input>").injected is UNKNOWN


def test_a_prompt_sent_through_the_sdk_is_unknown(projects):
    # No origin on an SDK prompt, whoever composed it.
    t = ClaudeTranscript(projects, entrypoint="sdk-cli")
    assert claude_user(t, "The current time, so you can plan.").injected is UNKNOWN


def test_a_wrapper_tag_inside_the_text_is_not_a_wrapper(transcript):
    text = "what does <local-command-stdout> mean?"
    assert claude_user(transcript, text).injected is UNKNOWN


# --- Claude Code: the model's own messages ------------------------------------------------

def test_the_model_s_messages_and_thoughts_are_not_injected(transcript):
    transcript.user("go", origin="human")
    req = transcript.request()
    req.thinking("which file")
    req.text("reading")
    user, thought, text = messages(claude.read_records(transcript.write()))
    assert user.injected is False
    assert thought.reasoning is True and thought.injected is False
    assert text.role == "assistant" and text.injected is False


def test_an_assistant_message_claude_code_made_up_itself_is_injected(transcript):
    transcript.user("go", origin="human")
    transcript.synthetic("No response requested.")
    user, made_up = messages(claude.read_records(transcript.write()))
    assert made_up.synthetic is True and made_up.injected is True


def test_a_tool_result_line_gives_no_message_to_mark(transcript):
    req = transcript.request()
    call = req.tool_use("Read", {"file_path": "/x"})
    transcript.tool_result(call, "contents")
    assert [m for m in messages(claude.read_records(transcript.write()))
            if m.role == "user"] == []


# --- Codex: content kinds ------------------------------------------------------------------

#: Every content kind measured on a Codex role-user message, with its choice.
CODEX_KINDS = [
    ("plugins.recommendations", True),
    ("agents_md.instructions", True),
    ("environments.environment_context", True),
    ("generic.turn_aborted", True),
    ("user.text", UNKNOWN),       # names no submitter: the person, a plugin or a program
    ("user.image", UNKNOWN),
    ("unknown", UNKNOWN),
]


def test_the_injected_kinds_are_exactly_the_true_ones_tested():
    assert {k for k, v in CODEX_KINDS if v is True} == codex.INJECTED_KINDS


@pytest.mark.parametrize("kind,choice", CODEX_KINDS, ids=[k for k, _ in CODEX_KINDS])
def test_each_codex_content_kind_names_its_choice(rollout, kind, choice):
    assert codex_user(rollout, "some text", kinds=[kind]).injected is choice


def test_codex_s_usual_context_message_is_injected(rollout):
    kinds = ["plugins.recommendations", "agents_md.instructions",
             "environments.environment_context"]
    message = codex_user(rollout, "<recommended_plugins>...</recommended_plugins>",
                         kinds=kinds)
    assert message.injected is True


def test_a_message_mixing_the_person_s_kind_with_an_added_one_is_unknown(rollout):
    kinds = ["user.text", "environments.environment_context"]
    assert codex_user(rollout, "hi", kinds=kinds).injected is UNKNOWN


def test_a_kind_sessions_does_not_know_is_unknown(rollout):
    kinds = ["environments.environment_context", "plugins.something_new"]
    assert codex_user(rollout, "x", kinds=kinds).injected is UNKNOWN


@pytest.mark.parametrize("kinds", [[], "user.text", [7]], ids=["empty", "not-a-list",
                                                              "not-text"])
def test_kinds_written_but_unreadable_are_unknown_and_no_wrapper_decides(rollout, kinds):
    message = codex_user(rollout, "<environment_context>x</environment_context>",
                         kinds=kinds)
    assert message.injected is UNKNOWN


def test_written_kinds_decide_alone_over_an_opening_tag(rollout):
    message = codex_user(rollout, "<environment_context>pasted</environment_context>",
                         kinds=["user.text"])
    assert message.injected is UNKNOWN


# --- Codex: older rollouts with no kinds, and the closed list of tags -----------------------

def test_the_codex_wrapper_list_is_exactly_the_tags_tested():
    assert codex.WRAPPER_TAGS == ("<recommended_plugins>", "<environment_context>",
                                  "<turn_aborted>")


@pytest.mark.parametrize("text", [
    "<recommended_plugins>\n- docs\n</recommended_plugins>",
    "<environment_context>\n  <cwd>/w</cwd>\n</environment_context>",
    "<turn_aborted>\nThe user interrupted.\n</turn_aborted>",
], ids=["recommended_plugins", "environment_context", "turn_aborted"])
def test_each_codex_tag_marks_a_message_with_no_kinds_injected(rollout, text):
    assert codex_user(rollout, text).injected is True


def test_a_codex_user_message_with_no_kinds_and_no_tag_is_unknown(rollout):
    assert codex_user(rollout, "fix the build").injected is UNKNOWN


def test_a_tag_inside_the_text_is_not_a_wrapper(rollout):
    assert codex_user(rollout, "what is <environment_context>?").injected is UNKNOWN


# --- Codex: other roles --------------------------------------------------------------------

def test_codex_s_own_instructions_are_injected(rollout):
    assert codex_user(rollout, "permissions", role="developer",
                      kinds=["permissions.instructions"]).injected is True
    assert codex_user(rollout, "instructions", role="developer").injected is True
    assert codex_user(rollout, "instructions", role="system").injected is True


def test_the_model_s_codex_messages_and_thoughts_are_not_injected(rollout):
    rollout.turn()
    rollout.user("go", kinds=["user.text"])
    rollout.reasoning("which suite")
    rollout.user("done", role="assistant", kinds=["unknown"])
    user, thought, answer = messages(codex.read_records(rollout.write()))
    assert user.injected is UNKNOWN
    assert thought.reasoning is True and thought.injected is False
    assert answer.role == "assistant" and answer.injected is False


# --- OpenCode: synthetic text parts ---------------------------------------------------------

def opencode_user(db: OpenCodeDb, parts) -> Message:
    s = db.session()
    user = s.user(None)
    for text, synthetic in parts:
        user.text(text, synthetic=synthetic)
    return only(opencode_records(db, s.id))


def test_a_user_message_whose_text_parts_are_all_synthetic_is_injected(db):
    message = opencode_user(db, [("Called the Read tool", True), ("file body", True)])
    assert message.injected is True
    assert message.text == "Called the Read tool\nfile body"


def test_a_user_message_with_one_text_part_not_synthetic_is_unknown(db):
    assert opencode_user(db, [("added", True), ("typed", None)]).injected is UNKNOWN


def test_a_user_message_with_no_synthetic_part_is_unknown(db):
    assert opencode_user(db, [("typed", None)]).injected is UNKNOWN
    assert opencode_user(db, [("typed", False)]).injected is UNKNOWN


def test_a_user_message_with_no_text_part_is_unknown(db):
    assert opencode_user(db, []).injected is UNKNOWN


def test_the_model_s_opencode_messages_and_thoughts_are_not_injected(db):
    s = db.session()
    s.user("run the tests")
    a = s.assistant()
    a.step_start()
    a.reasoning("which suite?")
    a.text("all green")
    a.step_finish(input=5, output=6)
    user, answer, thought = messages(opencode_records(db, s.id))
    assert user.injected is UNKNOWN
    assert answer.role == "assistant" and answer.injected is False
    assert thought.reasoning is True and thought.injected is False


def test_an_opencode_summary_message_is_injected(db):
    s = db.session()
    s.user("go")
    a = s.assistant(agent="compaction", summary=True)
    a.text("What we did so far")
    user, summary = messages(opencode_records(db, s.id))
    assert summary.flags["summary"] is True and summary.injected is True


# --- the field itself ------------------------------------------------------------------------

def test_injected_is_written_in_the_json_form_and_read_back():
    for value in (True, False):
        obj = json.loads(json.dumps(to_json(Message(harness="claude", injected=value))))
        assert obj["injected"] is value
        assert from_json(obj).injected is value
    obj = to_json(Message(harness="codex"))
    assert "injected" not in obj and "injected" in obj["unknown"]
    assert from_json(obj).injected is UNKNOWN


def test_a_reader_from_before_the_field_reads_it_as_unknown():
    assert from_json({"kind": "message", "harness": "claude", "role": "user"}).injected \
        is UNKNOWN


def test_a_backward_read_marks_each_message_as_the_forward_read_does(projects, sessions):
    t = ClaudeTranscript(projects)
    t.user("typed", origin="human")
    t.user("<local-command-stdout>x</local-command-stdout>")
    t.user("note", isMeta=True)
    t.user("hi")
    t.assistant("ok")
    t.write()
    r = CodexRollout(sessions)
    r.turn()
    r.user("<environment_context>x</environment_context>")
    r.user("ctx", kinds=["environments.environment_context"])
    r.user("go", kinds=["user.text"])
    r.assistant("ok")
    r.write()
    for src in (Source("claude", str(t.path), t.session_id, None),
                Source("codex", str(r.path), r.thread_id)):
        forward = [(m.at["offset"], m.injected) for m in messages(records(src))]
        back = [(m.at["offset"], m.injected) for m in messages(records_back(src))]
        assert sorted(back, key=lambda p: p[0]) == forward
        assert {v if v is not UNKNOWN else "unknown" for _, v in forward} == \
            {True, False, "unknown"}

"""The one shape: `UNKNOWN`, the record kinds, the shape version and the JSON form.

The JSON tests carry the reader's rule: an unknown field is left out and named in
`"unknown"`, an unknown kind reads as `other`, and a field the reader does not know is
ignored, so a document a newer sessions wrote still reads.
"""

from __future__ import annotations

import copy
import json
import pickle
from pathlib import Path

import pytest

from moxi_sessions.shape import (KINDS, MARKERS, OTEL_CONVENTIONS, SHAPE, UNKNOWN,
                                 LimitWindow, Limits, Malformed, Marker, Message, Other,
                                 Request, ToolCall, document, from_json, known, to_json,
                                 tool_summary)


# --- unknown ----------------------------------------------------------------------------

def test_unknown_is_neither_none_nor_zero():
    assert UNKNOWN is not None
    assert UNKNOWN != 0
    assert UNKNOWN != None  # noqa: E711 - the comparison is the point
    assert known(0) and known(None) and not known(UNKNOWN)


def test_unknown_has_no_truth_value_so_or_zero_fails_loudly():
    with pytest.raises(TypeError):
        bool(UNKNOWN)
    with pytest.raises(TypeError):
        UNKNOWN or 0  # noqa: B018
    with pytest.raises(TypeError):
        UNKNOWN + 1


def test_unknown_survives_copy_and_pickle_as_itself():
    assert copy.copy(UNKNOWN) is UNKNOWN
    assert copy.deepcopy(UNKNOWN) is UNKNOWN
    assert pickle.loads(pickle.dumps(UNKNOWN)) is UNKNOWN
    assert type(UNKNOWN)() is UNKNOWN
    assert repr(UNKNOWN) == "UNKNOWN"


# --- the records ------------------------------------------------------------------------

def test_every_kind_has_its_class_and_kind_reads_like_a_field():
    assert set(KINDS) == {"request", "message", "tool_call", "tool_result", "marker",
                          "limits", "malformed", "other"}
    for kind, cls in KINDS.items():
        assert cls(harness="claude").kind == kind


def test_records_are_frozen():
    record = Request(harness="claude", request_id="r")
    with pytest.raises(AttributeError):
        record.request_id = "s"  # type: ignore[misc]


def test_a_marker_reads_its_own_fields_as_attributes():
    marker = Marker(harness="claude", marker="compaction",
                    fields={"trigger": "auto", "pre_tokens": UNKNOWN})
    assert marker.trigger == "auto"
    assert marker.pre_tokens is UNKNOWN
    with pytest.raises(AttributeError):
        marker.duration_ms  # noqa: B018
    assert set(MARKERS["compaction"]) == {"trigger", "pre_tokens"}


# --- the JSON form ----------------------------------------------------------------------

def test_an_unknown_field_is_left_out_and_named_in_unknown():
    record = Request(harness="claude", session_id="s", request_id="r", input_tokens=7,
                     output_tokens=0, provider=None)
    obj = to_json(record)
    assert obj["kind"] == "request"
    assert obj["input_tokens"] == 7
    assert obj["output_tokens"] == 0                  # zero is written, not unknown
    assert obj["provider"] is None                    # a null is written, not unknown
    assert "cache_read_input_tokens" not in obj
    assert "cache_read_input_tokens" in obj["unknown"]
    assert "raw" not in obj
    json.dumps(obj)                                   # plain JSON throughout


@pytest.mark.parametrize("record", [
    Request(harness="claude", request_id="r", input_tokens=7, sidechain=False,
            at={"line": 3, "offset": 120}, time=1.5, extra={"cwd": "/w"}),
    Message(harness="claude", role="user", text="hi", content_kinds=("text", "image"),
            flags={"isMeta": True}),
    ToolCall(harness="claude", call_id="c", name="Read", input={"file_path": "/x"},
             summary="Read(/x)"),
    Marker(harness="claude", marker="turn_end", fields={"duration_ms": 12}),
    Marker(harness="claude", marker="compaction",
           fields={"trigger": UNKNOWN, "pre_tokens": 5}),
    Limits(harness="codex", source="rollout", limit_id="codex",
           windows=(LimitWindow(name="primary", used_percent=3.0, window_minutes=300),)),
    Malformed(harness="claude", raw=b"{not json", why="undecodable"),
    Malformed(harness="claude", raw=b"\xff\xfe{", why="undecodable", unterminated=True),
    Other(harness="claude", recognised=True, raw_kind="mode"),
], ids=lambda r: f"{r.kind}")
def test_a_record_comes_back_from_its_json(record):
    obj = json.loads(json.dumps(to_json(record)))
    assert from_json(obj) == record


def test_malformed_bytes_that_are_not_utf8_are_written_as_base64():
    obj = to_json(Malformed(harness="claude", raw=b"\xff{", why="undecodable"))
    assert "raw" not in obj
    assert obj["raw_base64"] == "/3s="


def test_an_unknown_kind_reads_as_other_not_recognised():
    record = from_json({"kind": "heartbeat", "harness": "claude", "raw_kind": "beat",
                        "session_id": "s", "pulse": 3})
    assert isinstance(record, Other)
    assert record.recognised is False
    assert record.session_id == "s"


def test_a_field_the_reader_does_not_know_is_ignored_and_a_missing_one_is_unknown():
    record = from_json({"kind": "request", "harness": "claude", "request_id": "r",
                        "input_tokens": 4, "invented_later": 1})
    assert record.input_tokens == 4
    assert record.output_tokens is UNKNOWN
    assert not hasattr(record, "invented_later")


def test_a_marker_keeps_fields_this_reader_has_no_list_for():
    record = from_json({"kind": "marker", "harness": "codex", "marker": "rewound",
                        "steps": 2})
    assert isinstance(record, Marker)
    assert record.steps == 2


def test_from_json_needs_a_harness():
    with pytest.raises(ValueError):
        from_json({"kind": "request"})


def test_a_document_starts_with_the_shape_version():
    doc = document(records=[Other(harness="claude")], cursor="abc")
    assert SHAPE == 1
    assert list(doc)[0] == "shape" and doc["shape"] == SHAPE
    assert doc["records"][0]["kind"] == "other"
    assert doc["cursor"] == "abc"


# --- tool summaries ---------------------------------------------------------------------

def test_a_summary_names_the_tool_and_its_identifying_argument():
    assert tool_summary("Read", {"file_path": "/w/a.py", "limit": 5}) == "Read(/w/a.py)"
    assert tool_summary("Bash", {"command": "ls  -la\n  /w"}) == "Bash(ls -la /w)"
    assert tool_summary("Grep", {"pattern": "x", "path": "/w"}) == "Grep(/w)"
    assert tool_summary("Odd", {"b": 1, "a": 2}) == 'Odd({"a": 2, "b": 1})'
    assert tool_summary("Empty", {}) == "Empty()"
    assert tool_summary(None, None) == "?()"


def test_a_summary_cuts_its_argument_at_160_characters_and_says_so():
    summary = tool_summary("Bash", {"command": "x" * 200})
    assert summary == "Bash(" + "x" * 160 + "... [+40 chars])"


def test_the_readme_names_the_shape_version_and_the_conventions_release():
    """The README tells a stranger the record shape: its kinds, the shape version and the
    OpenTelemetry release the names were checked against, as the code has them."""
    readme = (Path(__file__).parents[1] / "README.md").read_text()
    assert f"conventions' {OTEL_CONVENTIONS} release" in readme
    assert f"`SHAPE` is that shape version, an integer, now {SHAPE}." in readme
    assert f'{{"shape": {SHAPE}, ...}}' in readme
    for kind in KINDS:
        assert f"`{kind}`" in readme, kind
    for read in ("records", "records_back", "last_request", "records_since", "window"):
        assert f"- `{read}`" in readme, read

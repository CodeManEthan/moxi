"""`Source`, `Header`, the head scans and the three roots."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from conftest import write_config
from moxi_sessions.opencode import Snapshot
from moxi_sessions.shape import UNKNOWN
from moxi_sessions.source import (
    HEAD_LINES, Header, Source, claude_header, codex_head, codex_header, codex_root,
    file_header, opencode_header, opencode_root, root, root_origin, sidecar_labels, unpack)
from moxi_sessions.testing import ClaudeTranscript, CodexRollout, OpenCodeDb
from moxi_sessions.testing.codex import write_imports


def header_of(source: Source) -> Header:
    path = Path(source.path)
    with path.open("rb") as handle:
        return file_header(source, handle, path.stat().st_size)


# --- Source and unpack -----------------------------------------------------------------

def test_a_source_names_one_store_unit_and_says_it_has_no_listed_bound():
    src = Source("claude", "/x.jsonl", "s")
    assert src.agent_id is None and tuple(src) == ("claude", "/x.jsonl", "s", None)
    assert "no listed-size bound" in Source.__doc__


def test_unpack_takes_a_source_or_a_listing_entry_and_nothing_else():
    src = Source("codex", "/r.jsonl", "t")
    assert unpack(src) == (src, None)
    assert unpack(SimpleNamespace(source=src, size=120)) == (src, 120)
    with pytest.raises(ValueError):
        unpack(("codex", "/r.jsonl", "t"))
    with pytest.raises(ValueError):
        unpack(Source("gemini", "/x", "s"))


# --- Claude headers ----------------------------------------------------------------------

def test_a_claude_header_takes_the_first_facts_its_head_writes(tmp_path):
    t = ClaudeTranscript(tmp_path, cwd="/work/a", version="2.1.314", entrypoint="sdk-cli")
    t.other("file-history-snapshot", messageId="m0", snapshot={})
    t.user()
    t.write()
    header = header_of(Source("claude", str(t.path), None))
    assert (header.cwd, header.version, header.entrypoint) == ("/work/a", "2.1.314",
                                                               "sdk-cli")
    assert header.session_id == t.session_id      # from the file when the source names none
    assert header.parent is None and header.labels is None
    assert header.title is UNKNOWN
    data = header.as_dict()
    assert data["cwd"] == "/work/a" and "title" in data["unknown"]


def test_a_fact_past_the_heads_lines_is_unknown(tmp_path):
    t = ClaudeTranscript(tmp_path)
    for _ in range(HEAD_LINES):
        t.other("attachment")
    t.user()
    t.write()
    header = header_of(Source("claude", str(t.path), t.session_id))
    assert header.cwd is UNKNOWN and header.session_id == t.session_id


def test_a_subagents_header_carries_its_parent_and_sidecar_labels(tmp_path):
    t = ClaudeTranscript(tmp_path)
    sub = t.subagent("a01", agent_type="Explore", tool_use_id="toolu_1", spawn_depth=2,
                     parent_agent_id="a00")
    sub.user()
    wf = t.subagent("a02", workflow="wf_abc", sidecar="{broken")
    wf.user()
    bare = t.subagent("a03", sidecar=False)
    bare.user()
    t.user()
    t.write()
    header = header_of(Source("claude", str(sub.path), t.session_id, "a01"))
    assert header.parent == t.session_id and header.agent_id == "a01"
    assert header.labels == {"agent_type": "Explore", "description": "Look into it",
                             "tool_use_id": "toolu_1", "spawn_depth": 2,
                             "parent_agent_id": "a00", "workflow_id": None}
    # A sidecar that cannot be read costs its labels, not the header.
    broken = header_of(Source("claude", str(wf.path), t.session_id, "a02")).labels
    assert broken["agent_type"] is None and broken["workflow_id"] == "wf_abc"
    assert sidecar_labels(bare.path)["description"] is None


# --- Codex headers -----------------------------------------------------------------------

def test_a_codex_header_holds_line_ones_facts_and_the_import_marks(tmp_path):
    root = tmp_path / "codex" / "sessions"
    r = CodexRollout(root, source="vscode", provider="openai",
                     meta_fields={"forked_from_id": "parent-thread", "new_key": 1})
    r.turn()
    r.write()
    write_imports(root, [r.thread_id])
    header = header_of(Source("codex", str(r.path), r.thread_id))
    assert header.codex_source == "vscode" and header.version == "0.153.0"
    assert header.cwd == "/work/project" and header.parent == "parent-thread"
    assert header.meta["model_provider"] == "openai"
    assert header.meta["unlisted_keys"] == ("forked_from_id", "new_key")
    marks = header.import_marks
    assert (marks.originator, marks.turn_id, marks.listed) == (False, False, True)
    assert header.as_dict()["import_marks"]["listed"] is True


def test_an_imported_codex_thread_shows_its_marks_and_a_missing_file_is_unknown(tmp_path):
    root = tmp_path / "codex" / "sessions"
    r = CodexRollout(root, imported=True)
    r.turn()
    r.write()
    with r.path.open("rb") as handle:
        head = codex_head(handle, r.path.stat().st_size)
    assert head.first_turn_id.startswith("external-import-turn-")
    marks = codex_header(Source("codex", str(r.path), None), head).import_marks
    assert marks.originator is True and marks.turn_id is True
    assert marks.listed is UNKNOWN and marks.listed_reason == "imports file not found"


def test_a_codex_header_without_line_one_knows_nothing_of_it(tmp_path):
    r = CodexRollout(tmp_path / "s", meta=False)
    r.turn()
    r.write()
    header = header_of(Source("codex", str(r.path), r.thread_id))
    assert header.meta is None and header.codex_source is UNKNOWN
    assert header.session_id == r.thread_id


# --- OpenCode headers --------------------------------------------------------------------

def test_an_opencode_header_is_its_session_row(tmp_path):
    db = OpenCodeDb(tmp_path)
    s = db.session(title="parent")
    child = s.child(title="child")
    path = db.write()
    with Snapshot(path) as snap:
        row, child_row = snap.session(s.id), snap.session(child.id)
    header = opencode_header(Source("opencode", str(path), s.id), row)
    assert header.title == "parent" and header.parent is None and header.agent_id is None
    assert header.session_row is row
    child_header = opencode_header(Source("opencode", str(path), child.id), child_row)
    assert child_header.parent == s.id and child_header.agent_id == child.id
    whole = opencode_header(Source("opencode", str(path), None), None)
    assert whole.session_id is None and whole.session_row is None
    assert json.dumps(header.as_dict())


# --- roots -------------------------------------------------------------------------------

def test_the_codex_and_opencode_roots_follow_the_environment_handed_in():
    assert codex_root({"HOME": "/h"}) == Path("/h/.codex/sessions")
    assert codex_root({"HOME": "/h", "CODEX_HOME": "~/cx"}) == Path("/h/cx/sessions")
    assert opencode_root({"HOME": "/h"}) == Path("/h/.local/share/opencode/opencode.db")
    assert opencode_root({"HOME": "/h", "XDG_DATA_HOME": "/d"}) == \
        Path("/d/opencode/opencode.db")
    assert root("codex", {"HOME": "/h"}) == Path("/h/.codex/sessions")
    with pytest.raises(ValueError):
        root("gemini", {})


def test_a_configured_root_wins_and_says_where_it_came_from(own_config, tmp_path,
                                                            monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("CODEX_HOME", raising=False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    assert root_origin("codex").source == "default"
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    origin = root_origin("opencode")
    assert origin.source == "environment"
    assert origin.path == tmp_path / "data" / "opencode" / "opencode.db"
    write_config(own_config, f'[codex]\nsessions = "{tmp_path / "rollouts"}"\n')
    origin = root_origin("codex")
    assert origin.configured and origin.path == tmp_path / "rollouts"
    assert root("codex") == tmp_path / "rollouts"
    assert origin.as_dict()["source"] == "config"

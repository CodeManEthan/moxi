"""The Claude decoder, the finder and the projects root.

The decoder and stream tests carry the decoder's contract: the same bytes give the same
entries whole-file or streamed, a line that will not decode is yielded rather than
dropped, and a resumed stream keeps absolute line numbers. The finder tests carry
search-never-derive and the probe outcomes, with the clock's own finder rules folded in:
the id check, the newest file when two projects hold the id, and the per-process memo.
"""

from __future__ import annotations

import io
import json
import os
from pathlib import Path

import pytest
from conftest import assistant_line, user_line, write_config, write_transcript

from moxi.config import ConfigError
from moxi_sessions.api import claude, projects_root

T0 = "2026-08-12T11:00:00.000Z"
T1 = "2026-08-12T11:00:20.000Z"

META_USER = json.dumps({
    "type": "user", "isMeta": True, "uuid": "u-meta", "timestamp": T1,
    "isSidechain": False, "sessionId": "sess",
    "message": {"role": "user", "content": "skill body"}})

LINES = [
    user_line(uuid="u1", ts=T0),
    "{not json at all",
    assistant_line(request_id="req_A", uuid="a1", ts=T0),
    META_USER,
    "[]",
    assistant_line(request_id="req_B", uuid="a2", ts=T1),
]


def _stream(path: Path, **kw) -> list[dict]:
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        return list(claude.stream_entries(handle, **kw))


# --- parse_line and parse_timestamp ---------------------------------------------------

def test_parse_line_gives_an_object_or_none():
    assert claude.parse_line('{"type": "user"}') == {"type": "user"}
    assert claude.parse_line("   ") is None
    assert claude.parse_line("[1, 2]") is None
    assert claude.parse_line('"text"') is None
    assert claude.parse_line("{broken") is None


def test_parse_line_takes_raw_bytes_as_read_back_hands_them():
    assert claude.parse_line(b'{"type": "assistant"}\r') == {"type": "assistant"}
    assert claude.parse_line(b"\xff\xfe{") is None
    assert claude.parse_line(b"  ") is None


@pytest.mark.parametrize("value,want", [
    ("2026-08-12T11:00:00.000Z", 1786532400.0),
    ("2026-08-12T11:00:00+00:00", 1786532400.0),
    ("2026-08-12T11:00:00", 1786532400.0),          # no zone is UTC
    ("2026-08-12T13:00:00+02:00", 1786532400.0),
])
def test_parse_timestamp_reads_iso_stamps(value, want):
    assert claude.parse_timestamp(value) == pytest.approx(want)


@pytest.mark.parametrize("value", [None, "", "not a time", 12345, ["x"], {}])
def test_parse_timestamp_never_raises(value):
    assert claude.parse_timestamp(value) is None


# --- the stream and the whole-file decoder: same bytes, same entries ------------------

def test_the_same_bytes_yield_the_same_entries_as_whole_file_mode(projects):
    path = write_transcript(projects, "p", "s-stream", LINES)
    assert _stream(path) == list(claude.iter_entries(path.read_text(encoding="utf-8")))


def test_a_trailing_newlineless_last_line_still_matches(projects):
    path = write_transcript(projects, "p", "s-partial", LINES, newline_at_end=False)
    assert _stream(path) == list(claude.iter_entries(path.read_text(encoding="utf-8")))


def test_entry_keys_survive_the_stream_verbatim(projects):
    path = write_transcript(projects, "p", "s-keys", LINES)
    meta = [e for e in _stream(path) if e.get("uuid") == "u-meta"][0]
    assert meta["isMeta"] is True and meta["type"] == "user" and meta["timestamp"] == T1
    assert meta["sessionId"] == "sess" and meta["lineno"] == 4


def test_malformed_lines_are_yielded_so_the_caller_can_count_them(projects):
    path = write_transcript(projects, "p", "s-malformed", LINES)
    bad = [e for e in _stream(path) if e["type"] == claude.MALFORMED]
    assert [(e["lineno"], e["raw"]) for e in bad] == [(2, "{not json at all"), (5, "[]")]


def test_blank_lines_are_skipped_but_counted_in_line_numbers():
    text = '{"a": 1}\n\n   \n{"b": 2}\n'
    assert [e["lineno"] for e in claude.iter_entries(text)] == [1, 4]
    assert [e["lineno"] for e in claude.stream_entries(io.StringIO(text))] == [1, 4]


def test_start_line_resumes_where_the_last_read_stopped(projects):
    path = write_transcript(projects, "p", "s-resume", LINES)
    whole = _stream(path)
    assert _stream(path, start_line=3) == [e for e in whole if e["lineno"] > 3]
    head = whole[:4]
    assert head + _stream(path, start_line=head[-1]["lineno"]) == whole


def test_a_start_line_past_the_end_is_empty_not_an_error(projects):
    path = write_transcript(projects, "p", "s-past", LINES)
    assert _stream(path, start_line=9999) == []


def test_the_line_numbers_stay_absolute_after_a_resume(projects):
    path = write_transcript(projects, "p", "s-abs", LINES)
    assert min(e["lineno"] for e in _stream(path, start_line=5)) == 6


def test_the_stream_takes_any_line_iterable_not_only_a_file():
    entries = list(claude.stream_entries(iter([user_line(uuid="u") + "\n"])))
    assert [e["uuid"] for e in entries] == ["u"]


def test_a_lineno_the_line_carries_is_kept():
    assert next(claude.iter_entries('{"lineno": 99}'))["lineno"] == 99


# --- finding a session ----------------------------------------------------------------

SID = "aaaaaaaa-1111-4111-8111-111111111111"


def test_find_for_session_searches_never_derives(projects):
    write_transcript(projects, "-work-weird.dir", SID, [assistant_line()])
    assert claude.find_for_session(projects, SID).name == f"{SID}.jsonl"
    assert claude.find_for_session(projects, "bbbbbbbb-2222") is None


def test_a_probe_records_a_hit_and_a_miss(projects):
    path = write_transcript(projects, "p", SID, [assistant_line()])
    hit = claude.probe_root(projects, SID)
    assert hit.hit and hit.outcome == "hit" and hit.path == str(path)
    miss = claude.probe_root(projects, "cccccccc-3333")
    assert not miss.hit and miss.outcome == "miss" and miss.path is None
    assert miss.as_dict() == {"root": str(projects), "outcome": "miss", "path": None}


def test_an_absent_root_is_unreadable_never_a_miss(tmp_path):
    probe = claude.probe_root(tmp_path / "unmounted", SID)
    assert probe.outcome.startswith("unreadable:")
    assert claude.find_for_session(tmp_path / "unmounted", SID) is None


def test_a_file_in_place_of_a_root_is_unreadable(tmp_path):
    (tmp_path / "file").write_text("x")
    assert claude.probe_root(tmp_path / "file", SID).outcome.startswith("unreadable:")


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads any directory")
def test_a_root_without_permission_is_unreadable(tmp_path):
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0)
    try:
        assert claude.probe_root(locked, SID).outcome.startswith("unreadable:")
    finally:
        locked.chmod(0o700)


@pytest.mark.parametrize("bad", ["", "../escape", "a/b", ".hidden", "a*b", "a?b", "[ab]"])
def test_an_id_the_search_refuses_looks_at_nothing(projects, bad):
    probe = claude.probe_root(projects, bad)
    assert probe.outcome.startswith("refused:") and probe.path is None
    assert claude.find_for_session(projects, bad) is None


def test_a_non_string_id_is_refused(projects):
    assert claude.probe_root(projects, None).outcome.startswith("refused:")  # type: ignore


def test_the_newest_file_wins_when_two_projects_hold_the_id(projects):
    older = write_transcript(projects, "old-project", SID, [assistant_line()])
    newer = write_transcript(projects, "new-project", SID, [assistant_line()])
    os.utime(older, (1_000_000, 1_000_000))
    os.utime(newer, (2_000_000, 2_000_000))
    assert claude.find_for_session(projects, SID) == newer


def test_a_found_path_is_kept_and_searched_again_once_gone(projects):
    first = write_transcript(projects, "a-project", SID, [assistant_line()])
    assert claude.find_for_session(projects, SID) == first
    second = write_transcript(projects, "b-project", SID, [assistant_line()])
    os.utime(second, (first.stat().st_mtime + 100,) * 2)
    assert claude.find_for_session(projects, SID) == first, "the kept path stands"
    first.unlink()
    assert claude.find_for_session(projects, SID) == second
    second.unlink()
    assert claude.probe_root(projects, SID).outcome == "miss"


def test_a_subagent_file_is_not_the_sessions_transcript(projects):
    sub = projects / "p" / "parent" / "subagents"
    sub.mkdir(parents=True)
    (sub / f"{SID}.jsonl").write_text("{}\n")
    assert claude.probe_root(projects, SID).outcome == "miss"


def test_search_roots_has_no_default_live_root():
    with pytest.raises(TypeError):
        claude.search_roots()  # type: ignore[call-arg]


def test_search_roots_orders_live_archives_extra_and_drops_repeats(tmp_path):
    home = Path("~").expanduser()
    roots = claude.search_roots(live=tmp_path / "live", archives=["~/archive", tmp_path / "live"],
                                extra=["/mnt/x", "~/archive"])
    assert roots == [tmp_path / "live", home / "archive", Path("/mnt/x")]


# --- the projects root ----------------------------------------------------------------

def test_the_root_follows_claude_config_dir_in_the_environment_handed():
    assert projects_root({"CLAUDE_CONFIG_DIR": "/cfg", "HOME": "/h"}) == Path("/cfg/projects")


def test_without_claude_config_dir_the_root_is_under_home():
    assert projects_root({"HOME": "/h"}) == Path("/h/.claude/projects")
    assert projects_root({"HOME": "/h", "CLAUDE_CONFIG_DIR": ""}) == Path("/h/.claude/projects")


def test_a_tilde_takes_the_handed_environments_home():
    """The root is a function of the environment handed in, the session's, not this
    process's."""
    assert projects_root({"HOME": "/s", "CLAUDE_CONFIG_DIR": "~/alt"}) == Path("/s/alt/projects")


def test_the_root_ignores_this_processs_environment_when_handed_one(monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "/mine")
    assert projects_root({"HOME": "/h"}) == Path("/h/.claude/projects")


def test_with_none_and_no_config_the_root_is_the_rule_over_this_process(monkeypatch,
                                                                        tmp_path):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "cfg"))
    assert projects_root(None) == tmp_path / "cfg" / "projects"
    assert projects_root() == tmp_path / "cfg" / "projects"
    monkeypatch.delenv("CLAUDE_CONFIG_DIR")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    assert projects_root(None) == tmp_path / "home" / ".claude" / "projects"


def test_with_none_the_config_key_names_the_root(own_config, monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "cfg"))
    write_config(own_config, f'[claude]\nprojects = "{tmp_path / "stated"}"\n')
    assert projects_root(None) == tmp_path / "stated"
    assert projects_root(dict(os.environ)) == tmp_path / "cfg" / "projects", \
        "a session's own environment is never overridden by the reader's config"


def test_the_config_key_expands_a_tilde_and_refuses_a_relative_path(own_config,
                                                                    monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    write_config(own_config, '[claude]\nprojects = "~/stated"\n')
    assert projects_root(None) == tmp_path / "stated"
    write_config(own_config, '[claude]\nprojects = "relative/path"\n')
    with pytest.raises(ConfigError):
        projects_root(None)


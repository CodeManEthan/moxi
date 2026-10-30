"""The Codex finder and line stream, on synthetic rollouts.

`find_rollout` takes every file whose name parses as the thread's rollout, `.jsonl` or
`.jsonl.zst`, a revert's suffixed file included; deciding what none, one, a compressed
one or two mean is the caller's. An empty answer is said only of a root that was read.
"""

from __future__ import annotations

import os

import pytest
from conftest import ROLLOUT_ID, THREAD, THREAD_B, rollout_lines, write_rollout

from moxi_sessions.api import claude, codex


def test_a_thread_finds_its_rollout(sessions):
    path = write_rollout(sessions)
    assert codex.find_rollout(sessions, THREAD) == [path]


def test_a_root_with_none_is_an_empty_list(sessions):
    assert codex.find_rollout(sessions, THREAD) == []


def test_a_thread_sharing_a_prefix_is_not_a_candidate(sessions):
    write_rollout(sessions, THREAD_B)
    assert codex.find_rollout(sessions, THREAD) == []
    assert codex.find_rollout(sessions, THREAD[:8]) == []


def test_every_candidate_comes_back_compressed_and_suffixed_alike(sessions):
    plain = write_rollout(sessions)
    zst = write_rollout(sessions, day="2026/10/01", zst=True)
    revert = write_rollout(sessions, day="2026/10/07", suffix=f"_{ROLLOUT_ID}")
    assert codex.find_rollout(sessions, THREAD) == sorted([plain, zst, revert])


def test_a_name_that_does_not_parse_is_not_a_candidate(sessions):
    day = sessions / "2026" / "10" / "06"
    day.mkdir(parents=True)
    for name in (f"rollout-{THREAD}.jsonl",                       # no timestamp
                 f"rollout-2026-10-06T10-00-00-{THREAD}.json",    # wrong suffix
                 f"rollout-2026-10-06T10-00-00-{THREAD}.jsonl.bak",
                 f"rollout-2026-10-06T10-00-00-{THREAD}_notanid.jsonl"):
        (day / name).write_text("{}\n")
    assert codex.find_rollout(sessions, THREAD) == []


def test_a_rollout_outside_the_day_directories_is_not_a_candidate(sessions):
    (sessions / f"rollout-2026-10-06T10-00-00-{THREAD}.jsonl").write_text("{}\n")
    deep = sessions / "2026" / "10" / "06" / "extra"
    deep.mkdir(parents=True)
    (deep / f"rollout-2026-10-06T10-00-00-{THREAD}.jsonl").write_text("{}\n")
    assert codex.find_rollout(sessions, THREAD) == []


def test_a_directory_named_like_a_rollout_is_not_a_candidate(sessions):
    (sessions / "2026" / "10" / "06" / f"rollout-2026-10-06T10-00-00-{THREAD}.jsonl").mkdir(
        parents=True)
    assert codex.find_rollout(sessions, THREAD) == []


def test_an_absent_root_raises_rather_than_answering_none(tmp_path):
    with pytest.raises(OSError):
        codex.find_rollout(tmp_path / "nowhere", THREAD)


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads any directory")
def test_an_unreadable_root_raises(tmp_path):
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0)
    try:
        with pytest.raises(OSError):
            codex.find_rollout(locked, THREAD)
    finally:
        locked.chmod(0o700)


@pytest.mark.parametrize("bad", ["", "../x", "a/b", ".h", "a*", None])
def test_an_id_the_search_refuses_raises(sessions, bad):
    with pytest.raises(ValueError):
        codex.find_rollout(sessions, bad)


def test_a_safe_id_that_is_no_thread_finds_none(sessions):
    write_rollout(sessions)
    assert codex.find_rollout(sessions, "no-such-thread") == []


# --- the line stream ------------------------------------------------------------------

def test_the_stream_yields_every_entry_through_the_one_decoder(sessions):
    path = write_rollout(sessions)
    entries = list(codex.stream_rollout(path))
    assert [e["type"] for e in entries] == ["session_meta", "event_msg", "turn_context",
                                            "token_usage_record"]
    assert [e["lineno"] for e in entries] == [1, 2, 3, 4]
    with path.open(encoding="utf-8") as handle:
        assert entries == list(claude.stream_entries(handle))


def test_a_line_being_written_is_yielded_malformed_never_dropped(sessions):
    lines = rollout_lines()
    path = write_rollout(sessions, lines=lines[:-1] + [lines[-1][:30]])
    path.write_bytes(path.read_bytes().rstrip(b"\n"))
    entries = list(codex.stream_rollout(path))
    assert entries[-1]["type"] == claude.MALFORMED and entries[-1]["lineno"] == 4


def test_the_stream_resumes_from_a_start_line(sessions):
    path = write_rollout(sessions)
    assert [e["lineno"] for e in codex.stream_rollout(path, start_line=2)] == [3, 4]


def test_undecodable_bytes_are_replaced_not_fatal(sessions):
    path = write_rollout(sessions)
    path.write_bytes(path.read_bytes() + b'{"type": "x", "v": "\xff"}\n')
    assert list(codex.stream_rollout(path))[-1]["type"] == "x"


def test_a_compressed_rollout_is_refused(sessions):
    path = write_rollout(sessions, zst=True)
    with pytest.raises(ValueError):
        codex.stream_rollout(path)


def test_a_missing_rollout_raises(tmp_path):
    with pytest.raises(OSError):
        list(codex.stream_rollout(tmp_path / "rollout-x.jsonl"))


def test_the_codex_reader_decodes_through_the_one_decoder():
    """Nothing in `codex` decodes a line itself (one decoder for both harnesses)."""
    source = codex.__file__
    with open(source, encoding="utf-8") as f:
        text = f.read()
    assert "json.loads" not in text and "import json" not in text

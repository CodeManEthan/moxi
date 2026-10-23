"""`read_back`: lines newest first, the unterminated flag, line 1, and how the read ended.

Each case a caller tells apart has a test here: a limit hit with no qualifying line, a
read to the start, an unterminated last line that parses and one that does not, and
Codex's line 1. The parity tests at the end run `read_back` beside two independent
reference readers (`reference_readers.py`) over generated files.
"""

from __future__ import annotations

import json
import random

import pytest
from conftest import assistant_line, user_line
from reference_readers import claude_walk, codex_walk

from moxi_sessions.api import claude, read_back
from moxi_sessions.reader import LIMIT, START

BLOCK = 64 * 1024
LIMIT_BYTES = 64 * 1024 * 1024


def write(tmp_path, body: bytes, name: str = "record.jsonl"):
    path = tmp_path / name
    path.write_bytes(body)
    return path


def qualifies(raw: bytes) -> bool:
    """A stand-in for a caller's rule (an assistant line), judged by the caller."""
    entry = claude.parse_line(raw)
    return bool(entry) and entry.get("type") == "assistant"


# --- lines, newest first --------------------------------------------------------------

def test_every_non_blank_line_comes_back_newest_first_as_bytes(tmp_path):
    path = write(tmp_path, b'{"n": 1}\n\n{"n": 2}\n   \n{"n": 3}\n')
    with read_back(path, block=BLOCK, limit=LIMIT_BYTES) as reader:
        got = list(reader)
    assert got == [(b'{"n": 3}', False), (b'{"n": 2}', False), (b'{"n": 1}', False)]
    assert all(isinstance(raw, bytes) for raw, _ in got)


def test_lines_longer_than_a_block_are_whole(tmp_path):
    long = json.dumps({"pad": "x" * 300}).encode()
    path = write(tmp_path, b"\n".join([long, b'{"short": 1}', long]) + b"\n")
    with read_back(path, block=7, limit=LIMIT_BYTES) as reader:
        assert [raw for raw, _ in reader] == [long, b'{"short": 1}', long]


def test_no_line_is_judged_or_skipped_for_what_it_holds(tmp_path):
    path = write(tmp_path, b'{"ok": 1}\nnot json\n[1, 2]\n')
    with read_back(path, block=BLOCK, limit=LIMIT_BYTES) as reader:
        assert [raw for raw, _ in reader] == [b"[1, 2]", b"not json", b'{"ok": 1}']


def test_the_lines_decode_through_the_one_decoder(tmp_path):
    path = write(tmp_path, (user_line() + "\n" + assistant_line() + "\n").encode())
    with read_back(path, block=BLOCK, limit=LIMIT_BYTES) as reader:
        types = [claude.parse_line(raw)["type"] for raw, _ in reader]
    assert types == ["assistant", "user"]


def test_a_missing_file_raises_at_the_call(tmp_path):
    with pytest.raises(FileNotFoundError):
        read_back(tmp_path / "absent.jsonl", block=BLOCK, limit=LIMIT_BYTES)


@pytest.mark.parametrize("block,limit", [(0, 10), (-1, 10), (10, -1), (True, 10)])
def test_a_block_under_one_or_a_negative_limit_is_refused(tmp_path, block, limit):
    path = write(tmp_path, b"{}\n")
    with pytest.raises(ValueError):
        read_back(path, block=block, limit=limit)


def test_block_and_limit_have_no_default(tmp_path):
    """The limit is the caller's policy; sessions sets none."""
    path = write(tmp_path, b"{}\n")
    with pytest.raises(TypeError):
        read_back(path)  # type: ignore[call-arg]


def test_bytes_appended_after_the_open_are_not_read(tmp_path):
    path = write(tmp_path, b'{"n": 1}\n')
    with read_back(path, block=BLOCK, limit=LIMIT_BYTES) as reader:
        with open(path, "ab") as f:
            f.write(b'{"n": 2}\n')
        assert [raw for raw, _ in reader] == [b'{"n": 1}']


# --- how the read ended ---------------------------------------------------------------

def test_a_read_to_the_start_ends_start(tmp_path):
    """The whole file read with no qualifying line: the caller may read that as fresh."""
    path = write(tmp_path, (user_line() + "\n" + user_line(uuid="u2") + "\n").encode())
    with read_back(path, block=16, limit=LIMIT_BYTES) as reader:
        assert not any(qualifies(raw) for raw, _ in reader)
        assert reader.ended == START


def test_a_limit_hit_with_no_qualifying_line_ends_limit(tmp_path):
    """The limit stopped the read with lines left: never a read of the whole file."""
    lines = [assistant_line()] + [user_line(uuid=f"u{n}") for n in range(200)]
    path = write(tmp_path, ("\n".join(lines) + "\n").encode())
    with read_back(path, block=1024, limit=4096) as reader:
        seen = list(reader)
        assert not any(qualifies(raw) for raw, _ in seen)
        assert reader.ended == LIMIT
    assert 0 < len(seen) < 201


def test_the_limit_is_checked_before_each_block(tmp_path):
    """A limit equal to the file's size reads it all; one byte less leaves a block."""
    body = b"".join(b'{"n": %d}\n' % n for n in range(40))
    path = write(tmp_path, body)
    with read_back(path, block=len(body) // 4, limit=len(body)) as reader:
        assert len(list(reader)) == 40 and reader.ended == START
    block = len(body) // 4 + 1                     # four blocks do not reach the start
    with read_back(path, block=block, limit=4 * block) as reader:
        assert reader.ended is None
        list(reader)
        assert reader.ended == START
    with read_back(path, block=block, limit=3 * block) as reader:
        list(reader)
        assert reader.ended == LIMIT
    with read_back(path, block=block, limit=3 * block - 1) as reader:
        list(reader)
        assert reader.ended == LIMIT


def test_a_limit_of_zero_reads_nothing(tmp_path):
    path = write(tmp_path, b"{}\n")
    with read_back(path, block=BLOCK, limit=0) as reader:
        assert list(reader) == [] and reader.ended == LIMIT


def test_ended_is_none_while_lines_remain_and_when_the_caller_stops(tmp_path):
    path = write(tmp_path, (user_line() + "\n" + assistant_line() + "\n"
                            + user_line(uuid="u2") + "\n").encode())
    with read_back(path, block=BLOCK, limit=LIMIT_BYTES) as reader:
        assert reader.ended is None                       # nothing read yet
        for raw, _ in reader:
            if qualifies(raw):
                break                                     # the caller stops first
        assert reader.ended is None


def test_ended_is_set_while_the_caller_holds_the_last_line(tmp_path):
    path = write(tmp_path, b'{"n": 1}\n{"n": 2}\n')
    with read_back(path, block=4, limit=LIMIT_BYTES) as reader:
        endings = [reader.ended for _ in reader]
    assert endings == [None, START]


def test_an_empty_file_ends_start_with_no_line(tmp_path):
    path = write(tmp_path, b"")
    with read_back(path, block=BLOCK, limit=LIMIT_BYTES) as reader:
        assert list(reader) == [] and reader.ended == START
    with read_back(path, block=BLOCK, limit=LIMIT_BYTES, head=True) as reader:
        assert list(reader) == [] and reader.first == b"" and reader.ended == START


def test_iterating_to_the_end_closes_the_file(tmp_path):
    path = write(tmp_path, b"{}\n")
    reader = read_back(path, block=BLOCK, limit=LIMIT_BYTES)
    list(reader)
    assert reader._file.closed


def test_a_reader_closed_early_yields_no_more(tmp_path):
    path = write(tmp_path, b'{"n": 1}\n{"n": 2}\n{"n": 3}\n')
    reader = read_back(path, block=4, limit=LIMIT_BYTES)
    next(reader)
    reader.close()
    rest = list(reader)
    assert len(rest) <= 1 and reader.ended is None


# --- the unterminated last line -------------------------------------------------------

def test_a_terminated_file_flags_no_line(tmp_path):
    path = write(tmp_path, b'{"n": 1}\n{"n": 2}\n')
    with read_back(path, block=BLOCK, limit=LIMIT_BYTES) as reader:
        assert [flag for _, flag in reader] == [False, False]


def test_an_unterminated_last_line_that_parses_comes_with_its_flag(tmp_path):
    """A caller may judge it like any other line; the flag lets it tell."""
    path = write(tmp_path, (user_line() + "\n" + assistant_line()).encode())
    with read_back(path, block=BLOCK, limit=LIMIT_BYTES) as reader:
        got = list(reader)
    assert got[0] == (assistant_line().encode(), True)
    assert qualifies(got[0][0])
    assert [flag for _, flag in got[1:]] == [False]


def test_an_unterminated_last_line_that_does_not_parse_is_still_yielded(tmp_path):
    """A harness writing its last line as the read runs: the caller decides (it may skip
    the line, or count it as a line that would not parse)."""
    partial = assistant_line()[:40].encode()
    path = write(tmp_path, (user_line() + "\n").encode() + partial)
    with read_back(path, block=BLOCK, limit=LIMIT_BYTES) as reader:
        got = list(reader)
    assert got[0] == (partial, True)
    assert claude.parse_line(got[0][0]) is None
    assert got[1][1] is False


def test_an_unterminated_last_line_longer_than_a_block_keeps_its_flag(tmp_path):
    tail = json.dumps({"pad": "y" * 200}).encode()
    path = write(tmp_path, b'{"n": 1}\n' + tail)
    with read_back(path, block=16, limit=LIMIT_BYTES) as reader:
        assert list(reader) == [(tail, True), (b'{"n": 1}', False)]


def test_only_the_files_last_line_carries_the_flag(tmp_path):
    """A file ending in blanks with no newline: the blank tail is the last line, and it is
    not yielded, so no line carries the flag."""
    path = write(tmp_path, b'{"n": 1}\n{"n": 2}\n   ')
    with read_back(path, block=BLOCK, limit=LIMIT_BYTES) as reader:
        assert list(reader) == [(b'{"n": 2}', False), (b'{"n": 1}', False)]


# --- line 1 ---------------------------------------------------------------------------

def test_head_reads_line_1_first_and_the_backward_read_stops_at_line_2(tmp_path):
    path = write(tmp_path, b'{"meta": 1}\n{"n": 2}\n{"n": 3}\n')
    with read_back(path, block=5, limit=LIMIT_BYTES, head=True) as reader:
        assert reader.first == b'{"meta": 1}'
        assert [raw for raw, _ in reader] == [b'{"n": 3}', b'{"n": 2}']
        assert reader.ended == START


def test_without_head_the_read_runs_to_line_1(tmp_path):
    path = write(tmp_path, b'{"meta": 1}\n{"n": 2}\n')
    with read_back(path, block=5, limit=LIMIT_BYTES) as reader:
        assert reader.first is None
        assert [raw for raw, _ in reader] == [b'{"n": 2}', b'{"meta": 1}']


def test_a_file_of_line_1_alone_yields_nothing_and_ends_start(tmp_path):
    for body in (b'{"meta": 1}\n', b'{"meta": 1}'):
        path = write(tmp_path, body)
        with read_back(path, block=4, limit=LIMIT_BYTES, head=True) as reader:
            assert reader.first == b'{"meta": 1}'
            assert list(reader) == [] and reader.ended == START


def test_line_1_is_not_yielded_even_when_it_is_long(tmp_path):
    meta = json.dumps({"meta": "m" * 500}).encode()
    path = write(tmp_path, meta + b'\n{"n": 2}\n')
    with read_back(path, block=16, limit=LIMIT_BYTES, head=True) as reader:
        assert reader.first == meta
        assert [raw for raw, _ in reader] == [b'{"n": 2}']


def test_line_1_that_does_not_end_within_the_limit_is_not_read(tmp_path):
    """No line 1 within the limit: no line 2 to stop at, so the read ends at the limit."""
    meta = json.dumps({"meta": "m" * 500}).encode()
    path = write(tmp_path, meta + b'\n{"n": 2}\n')
    with read_back(path, block=16, limit=64, head=True) as reader:
        assert reader.first is None
        assert list(reader) == [] and reader.ended == LIMIT


def test_the_backward_limit_counts_only_backward_bytes(tmp_path):
    """Line 1 is read under a limit of its own; the backward read gets the whole limit."""
    meta = b'{"meta": "' + b"m" * 20 + b'"}'        # ends inside the head's first block
    rest = [b'{"n": %d}' % n for n in range(5)]
    path = write(tmp_path, b"\n".join([meta] + rest) + b"\n")
    backward = sum(len(r) + 1 for r in rest)
    with read_back(path, block=backward, limit=backward, head=True) as reader:
        assert len(list(reader)) == 5 and reader.ended == START


def test_an_unterminated_line_after_line_1_carries_the_flag(tmp_path):
    path = write(tmp_path, b'{"meta": 1}\n{"n": 2}\n{"n": 3')
    with read_back(path, block=BLOCK, limit=LIMIT_BYTES, head=True) as reader:
        assert list(reader) == [(b'{"n": 3', True), (b'{"n": 2}', False)]


# --- parity with the reference readers ------------------------------------------------

def _random_body(rng: random.Random, *, head_line: bool) -> bytes:
    lines = []
    if head_line:
        lines.append(json.dumps({"meta": "h" * rng.randint(0, 30)}))
    for _ in range(rng.randint(0, 25)):
        kind = rng.random()
        if kind < 0.1:
            lines.append("")
        elif kind < 0.15:
            lines.append(" " * rng.randint(1, 3))
        elif kind < 0.25:
            lines.append("not json " + "z" * rng.randint(0, 90))
        else:
            lines.append(json.dumps({"n": rng.randint(0, 9), "pad": "p" * rng.randint(0, 120)}))
    body = "\n".join(lines)
    if not lines:
        return b""
    if rng.random() < 0.3 and lines[-1].strip():
        return body.encode()                   # an unterminated last line
    return (body + "\n").encode()


@pytest.mark.parametrize("seed", range(40))
def test_parity_with_the_walk_to_line_1(tmp_path, seed):
    rng = random.Random(seed)
    for case in range(25):
        body = _random_body(rng, head_line=False)
        path = write(tmp_path, body, f"c{case}.jsonl")
        block = rng.randint(1, 80)
        limit = rng.randint(0, len(body) + block)
        want_lines, want_end = claude_walk(path, block, limit)
        with read_back(path, block=block, limit=limit) as reader:
            got = list(reader)
            ended = reader.ended
        assert [raw for raw, _ in got] == want_lines, (seed, case, block, limit)
        assert ended == (START if want_end == "start" else LIMIT), (seed, case)


@pytest.mark.parametrize("seed", range(40))
def test_parity_with_the_walk_that_reads_line_1_first(tmp_path, seed):
    rng = random.Random(1000 + seed)
    for case in range(25):
        body = _random_body(rng, head_line=True)
        path = write(tmp_path, body, f"x{case}.jsonl")
        block = rng.randint(1, 80)
        # Line 1 ends within the head's limit (the reference reads up to a block past it).
        limit = rng.randint(body.index(b"\n") + 1 if b"\n" in body else len(body) + 1,
                            len(body) + block + 1)
        first, pairs, reached_start, hit_limit = codex_walk(path, block, limit)
        with read_back(path, block=block, limit=limit, head=True) as reader:
            got = list(reader)
            assert reader.first == first, (seed, case)
            ended = reader.ended
        assert got == pairs, (seed, case, block, limit)
        assert (ended == START) == reached_start and (ended == LIMIT) == hit_limit

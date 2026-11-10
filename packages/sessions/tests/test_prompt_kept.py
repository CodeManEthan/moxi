"""The prompt's kept read. The rule every test here checks: what a catalog gives as a
session's prompt is what reading the whole file at that moment gives, where the file is
its bytes up to its last newline."""

from __future__ import annotations

import hashlib
import json
import os
import random

import pytest

from moxi_sessions import catalog as catalog_module
from moxi_sessions.catalog import WINDOW, Catalog, Kept, Said, read_prompt
from moxi_sessions.errors import Unreadable
from moxi_sessions.read import BACK_LIMIT, records
from moxi_sessions.shape import CLAUDE, Message
from moxi_sessions.testing import ClaudeTranscript
from moxi_sessions.testing.claude import START


def sid(n: int) -> str:
    return f"0190b000-0000-7000-8000-{n:012x}"


@pytest.fixture
def roots(tmp_path):
    projects = tmp_path / "projects"
    projects.mkdir()
    return {CLAUDE: projects}


@pytest.fixture
def cat(roots) -> Catalog:
    """A catalog that lists its root again on every call."""
    return Catalog(roots, reuse_s=0)


def transcript(roots, n: int = 1, **kw) -> ClaudeTranscript:
    return ClaudeTranscript(roots[CLAUDE], session_id=sid(n), **kw)


def filler(t: ClaudeTranscript, lines: int = 12) -> None:
    """Lines with no typed message, more than `WINDOW` bytes of them for 12."""
    for n in range(lines):
        t.user(f"notice {n} " + "x" * 400, origin="task-notification")
        t.assistant(f"Reply {n}.")


def entry_of(cat: Catalog, n: int = 1):
    return next(e for e in cat.gather().entries if e.session == sid(n))


def whole(cat: Catalog, n: int = 1) -> Said | None:
    """What reading the whole file now gives, with nothing kept."""
    return read_prompt(entry_of(cat, n).listed).kept.prompt


def prompt(cat: Catalog, n: int = 1) -> Said | None:
    """The catalog's prompt for the session, checked against a whole read."""
    found = cat.details(entry_of(cat, n)).prompt
    assert found == whole(cat, n)
    return found


def kept_of(cat: Catalog, t: ClaudeTranscript) -> Kept:
    return cat._prompts[str(t.path)]


def later(path, seconds: int = 1) -> None:
    """Move the file's mtime on, so that no two states of it share one."""
    stamp = os.stat(path).st_mtime_ns + seconds * 1_000_000_000
    os.utime(path, ns=(stamp, stamp))


def rewrite(path, data: bytes) -> None:
    """Write the file again in place: the same inode, other bytes."""
    with open(path, "r+b") as handle:
        handle.write(data)
        handle.truncate()
    later(path)


def reads(cat: Catalog) -> tuple[int, int, int]:
    return (cat.prompt_reads, cat.prompt_whole, cat.prompt_bytes)


# --- the whole read ---------------------------------------------------------------------------

def test_a_whole_read_agrees_with_the_session_readers_records(roots, cat):
    t = transcript(roots)
    t.user("an older prompt", origin="human")
    t.assistant("A reply.")
    t.user("the newest prompt", origin="human")
    t.user("a channel delivery", origin="channel")
    t.user("a hook's text", origin="human", isMeta=True)
    t.user("a summary", origin="human", isCompactSummary=True)
    t.user("who sent this")
    t.user("a kind nobody listed", origin="something-new")
    t.add_raw("{not json")
    t.add_raw("")
    t.assistant("The last reply.")
    t.write()
    typed = [r for r in records(entry_of(cat).listed, kinds="message")
             if isinstance(r, Message) and r.role == "user" and r.injected is False]

    assert [r.text for r in typed] == ["an older prompt", "the newest prompt"]
    assert whole(cat) == Said(typed[-1].time, "you", "the newest prompt")
    assert prompt(cat) == whole(cat)


def test_newest_is_last_in_the_files_order_whatever_the_times_say(roots, cat):
    t = transcript(roots)
    t.user("written first, dated later", origin="human")
    t.user("written last, dated earlier", origin="human")
    first, last = (json.loads(line) for line in t.lines)
    first["timestamp"], last["timestamp"] = last["timestamp"], first["timestamp"]
    t.lines = [json.dumps(first), json.dumps(last)]
    t.write()

    assert prompt(cat) == Said(START, "you", "written last, dated earlier")


def test_a_typed_message_more_than_the_backward_reads_limit_from_the_end_is_found(
        roots, cat):
    t = transcript(roots)
    t.user("typed long ago", origin="human")
    path = t.write()
    blank = b" " * (1024 * 1024 - 1) + b"\n"
    with open(path, "ab") as handle:
        for _ in range(BACK_LIMIT // len(blank) + 1):
            handle.write(blank)
    size = path.stat().st_size
    assert size > BACK_LIMIT

    assert prompt(cat) == Said(START, "you", "typed long ago")
    assert reads(cat) == (1, 1, size)


# --- a file that only grew -------------------------------------------------------------------

def test_a_grown_file_is_read_from_the_kept_offset_and_a_new_typed_message_wins(
        roots, cat):
    t = transcript(roots)
    t.user("the first prompt", origin="human")
    filler(t)
    size = t.write().stat().st_size
    assert prompt(cat).text == "the first prompt"
    assert reads(cat) == (1, 1, size)

    t.assistant("More.")
    t.user("the second prompt", origin="human")
    t.assistant("A reply.")
    grown = t.flush().stat().st_size

    assert prompt(cat).text == "the second prompt"
    assert reads(cat) == (2, 1, grown)               # only the new bytes were read
    assert kept_of(cat, t).offset == grown


def test_the_kept_prompt_stands_when_the_new_lines_hold_no_typed_message(roots, cat):
    t = transcript(roots)
    t.user("the first prompt", origin="human")
    filler(t)
    t.write()
    first = prompt(cat)

    for n in range(3):
        t.user(f"a later notice {n}", origin="task-notification")
        t.assistant("A reply.")
        t.flush()
        assert prompt(cat) == first
    assert cat.prompt_whole == 1


def test_a_session_with_no_typed_message_stays_without_one_until_one_is_written(
        roots, cat):
    t = transcript(roots)
    filler(t)
    t.write()
    assert prompt(cat) is None

    t.assistant("More.")
    t.flush()
    assert prompt(cat) is None

    t.user("typed at last", origin="human")
    t.flush()
    assert prompt(cat).text == "typed at last"
    assert cat.prompt_whole == 1


def test_the_digest_covers_the_window_before_the_offset_after_a_short_growth(roots, cat):
    t = transcript(roots)
    filler(t)
    t.write()
    prompt(cat)

    t.assistant("ok")                                # far fewer bytes than the window
    path = t.flush()
    prompt(cat)
    kept = kept_of(cat, t)
    data = path.read_bytes()

    assert kept.offset == len(data)
    assert kept.digest == hashlib.sha256(data[-WINDOW:]).digest()

    t.user("typed after it", origin="human")
    t.flush()
    assert prompt(cat).text == "typed after it"
    assert cat.prompt_whole == 1                     # the digest still held


# --- a last line still being written ---------------------------------------------------------

def test_a_last_line_with_no_newline_is_not_read_until_it_is_complete(roots, cat):
    t = transcript(roots)
    t.user("the first prompt", origin="human")
    filler(t)
    complete = t.write().stat().st_size
    assert prompt(cat).text == "the first prompt"

    t.user("typed, and still being written", origin="human")
    line = t.lines[-1].encode()
    with open(t.path, "ab") as handle:
        handle.write(line[:40])                      # the line's first bytes
    later(t.path)
    assert prompt(cat).text == "the first prompt"
    assert kept_of(cat, t).offset == complete

    with open(t.path, "ab") as handle:
        handle.write(line[40:])                      # whole JSON, no newline yet
    later(t.path)
    assert prompt(cat).text == "the first prompt"
    assert kept_of(cat, t).offset == complete
    assert cat.prompt_bytes == complete              # nothing past the last newline

    with open(t.path, "ab") as handle:
        handle.write(b"\n")
    later(t.path)
    assert prompt(cat).text == "typed, and still being written"
    assert kept_of(cat, t).offset == complete + len(line) + 1
    assert reads(cat)[1:] == (1, complete + len(line) + 1)


def test_a_whole_read_stops_at_the_last_newline_too(roots, cat):
    t = transcript(roots)
    t.user("complete", origin="human")
    t.user("no newline yet", origin="human")
    t.write(terminated=False)

    assert whole(cat).text == "complete"
    assert prompt(cat).text == "complete"

    with open(t.path, "ab") as handle:
        handle.write(b"\n")
    later(t.path)
    assert prompt(cat).text == "no newline yet"


def test_a_file_with_no_complete_line_has_no_prompt(roots, cat):
    t = transcript(roots)
    t.user("no newline yet", origin="human")
    t.write(terminated=False)

    assert prompt(cat) is None
    assert kept_of(cat, t).offset == 0 and cat.prompt_bytes == 0


# --- a file that is not the one that was read ----------------------------------------------

def test_a_file_that_shrank_is_read_whole(roots, cat):
    t = transcript(roots)
    t.user("the first prompt", origin="human")
    filler(t)
    t.user("the second prompt", origin="human")
    data = t.write().read_bytes()
    assert prompt(cat).text == "the second prompt"

    cut = data.rindex(b"\n", 0, len(data) - 1) + 1   # without its last line
    with open(t.path, "r+b") as handle:
        handle.truncate(cut)
    later(t.path)

    assert prompt(cat).text == "the first prompt"
    assert reads(cat) == (2, 2, len(data) + cut)


def test_a_file_written_again_in_place_and_ending_shorter_is_read_whole(roots, cat):
    t = transcript(roots)
    t.user("the first prompt", origin="human")
    filler(t, 20)
    t.write()
    assert prompt(cat).text == "the first prompt"

    other = transcript(roots)
    other.user("another prompt", origin="human")
    filler(other, 14)
    rewrite(t.path, other.text().encode())

    assert prompt(cat).text == "another prompt"
    assert cat.prompt_whole == 2


def test_a_file_written_again_in_place_and_ending_longer_is_read_whole(roots, cat):
    t = transcript(roots)
    t.user("the first prompt", origin="human")
    filler(t)
    before = t.write().stat()
    assert prompt(cat).text == "the first prompt"

    other = transcript(roots)                        # longer, and no typed message
    filler(other, 30)
    rewrite(t.path, other.text().encode())
    after = t.path.stat()
    assert (after.st_ino, after.st_size > before.st_size) == (before.st_ino, True)

    assert prompt(cat) is None                       # the kept one did not stand
    assert cat.prompt_whole == 2


def test_a_same_length_change_inside_the_window_is_read_whole(roots, cat):
    t = transcript(roots)
    t.user("the first prompt", origin="human")
    filler(t)
    data = t.write().read_bytes()
    assert prompt(cat).text == "the first prompt"

    at = data.rindex(b"Reply 11.")
    rewrite(t.path, data[:at] + b"Other 11." + data[at + 9:])
    with open(t.path, "ab") as handle:
        handle.write(b"\n")
    later(t.path)

    assert prompt(cat).text == "the first prompt"
    assert cat.prompt_whole == 2


def test_a_file_replaced_by_another_of_the_same_name_is_read_whole(roots, cat):
    t = transcript(roots)
    t.user("the first prompt", origin="human")
    filler(t)
    before = t.write().stat()
    assert prompt(cat).text == "the first prompt"

    other = transcript(roots)
    filler(other, 30)
    other.write()                                    # a new file put in its place
    after = t.path.stat()
    assert after.st_ino != before.st_ino and after.st_size > before.st_size

    assert prompt(cat) is None
    assert cat.prompt_whole == 2


def test_a_replacement_of_the_same_size_and_mtime_is_seen_by_its_inode(roots, cat):
    t = transcript(roots)
    t.user("the first prompt", origin="human")
    filler(t)
    before = t.write().stat()
    assert prompt(cat).text == "the first prompt"

    other = transcript(roots)
    other.user("another's prompt.", origin="human")  # one byte longer, made up below
    filler(other)
    data = other.text().encode().replace(b"x" * 400, b"x" * 399, 1)
    assert len(data) == before.st_size
    spare = t.path.with_suffix(".new")               # made while the old file is there
    spare.write_bytes(data)
    os.utime(spare, ns=(before.st_atime_ns, before.st_mtime_ns))
    os.replace(spare, t.path)
    after = t.path.stat()
    assert (after.st_size, after.st_mtime_ns) == (before.st_size, before.st_mtime_ns)
    assert after.st_ino != before.st_ino

    assert prompt(cat).text == "another's prompt."
    assert cat.prompt_whole == 2


def test_a_kept_offset_under_the_window_is_read_whole_each_time(roots, cat):
    t = transcript(roots)
    t.user("the first prompt", origin="human")
    t.assistant("A reply.")
    small = t.write().stat().st_size
    assert small < WINDOW
    assert prompt(cat).text == "the first prompt"
    assert kept_of(cat, t).digest is None

    t.assistant("More.")
    grown = t.flush().stat().st_size
    assert grown < WINDOW
    assert prompt(cat).text == "the first prompt"
    assert reads(cat) == (2, 2, small + grown)

    filler(t)                                        # now past the window
    full = t.flush().stat().st_size
    assert prompt(cat).text == "the first prompt"
    assert reads(cat) == (3, 3, small + grown + full)

    t.user("the second prompt", origin="human")
    last = t.flush().stat().st_size
    assert prompt(cat).text == "the second prompt"
    assert reads(cat) == (4, 3, small + grown + full + last - full)


# --- a file that did not change ---------------------------------------------------------------

def test_an_unchanged_file_is_not_opened_again(roots, cat, monkeypatch):
    t = transcript(roots)
    t.user("the first prompt", origin="human")
    filler(t)
    t.write()
    calls = []
    real = catalog_module.read_prompt

    def counted(listed, kept=None):
        calls.append(listed.size)
        return real(listed, kept)

    monkeypatch.setattr(catalog_module, "read_prompt", counted)

    for _ in range(4):
        assert cat.details(entry_of(cat)).prompt.text == "the first prompt"
    assert len(calls) == 1 and reads(cat)[:2] == (1, 1)


def test_a_listing_older_than_the_kept_read_reads_nothing(roots, cat):
    t = transcript(roots)
    t.user("the first prompt", origin="human")
    filler(t)
    t.write()
    old = entry_of(cat)
    cat.details(old)

    t.user("the second prompt", origin="human")
    t.flush()
    assert prompt(cat).text == "the second prompt"
    counted = reads(cat)

    # The older listing names a size the file no longer has; the opened file is the
    # one that was kept, so nothing is read and the answer is the file's prompt now.
    assert cat.details(old).prompt.text == "the second prompt"
    assert reads(cat) == counted


def test_the_two_changes_the_kept_read_does_not_see(roots, cat):
    t = transcript(roots)
    t.user("the first prompt", origin="human")
    filler(t)
    path = t.write()
    data, st = path.read_bytes(), path.stat()
    assert cat.details(entry_of(cat)).prompt.text == "the first prompt"
    changed = data.replace(b"the first prompt", b"THE FIRST PROMPT")
    assert data.index(b"the first prompt") < len(data) - WINDOW

    # Written again with the same device, inode, size and mtime: not read.
    with open(path, "r+b") as handle:
        handle.write(changed)
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))
    assert cat.details(entry_of(cat)).prompt.text == "the first prompt"
    assert whole(cat).text == "THE FIRST PROMPT"

    # The same length, and only bytes before the window: read from the offset.
    later(path)
    assert cat.details(entry_of(cat)).prompt.text == "the first prompt"
    assert reads(cat) == (2, 1, len(data))


# --- reads that fail, and reads that end late ----------------------------------------------

def test_a_failed_read_keeps_what_was_kept_and_the_next_read_checks_it(roots, cat,
                                                                       monkeypatch):
    t = transcript(roots)
    t.user("the first prompt", origin="human")
    filler(t)
    t.write()
    prompt(cat)
    kept = kept_of(cat, t)
    t.user("the second prompt", origin="human")
    t.flush()
    real = catalog_module.read_prompt

    def broken(listed, kept=None):
        raise Unreadable("the file cannot be read")

    monkeypatch.setattr(catalog_module, "read_prompt", broken)
    assert cat.details(entry_of(cat)).prompt is None
    assert kept_of(cat, t) is kept

    monkeypatch.setattr(catalog_module, "read_prompt", real)
    assert prompt(cat).text == "the second prompt"
    assert reads(cat)[:2] == (3, 1)                  # the failed one counted, none whole


def test_a_read_that_ends_before_the_files_size_fails_and_moves_nothing(roots, cat,
                                                                        monkeypatch):
    t = transcript(roots)
    t.user("the first prompt", origin="human")
    filler(t)
    t.write()
    prompt(cat)
    kept = kept_of(cat, t)
    t.user("the second prompt", origin="human")
    t.flush()
    entry = entry_of(cat)
    real = os.fstat

    class Longer:
        """The opened file's stat, with a size 100 bytes past its end."""

        def __init__(self, st):
            self.st = st

        def __getattr__(self, name):
            value = getattr(self.st, name)
            return value + 100 if name == "st_size" else value

    monkeypatch.setattr(catalog_module.os, "fstat", lambda fd: Longer(real(fd)))
    with pytest.raises(Unreadable):
        read_prompt(entry.listed, kept)
    with pytest.raises(Unreadable):
        read_prompt(entry.listed)
    assert cat._prompt(entry, None) is None
    assert kept_of(cat, t) is kept

    monkeypatch.setattr(catalog_module.os, "fstat", real)
    assert prompt(cat).text == "the second prompt"
    assert cat.prompt_whole == 1


def test_a_file_that_cannot_be_opened_fails_the_read(roots, cat):
    t = transcript(roots)
    t.user("the first prompt", origin="human")
    t.write()
    entry = entry_of(cat)
    t.path.unlink()

    with pytest.raises(Unreadable):
        read_prompt(entry.listed)
    assert cat.details(entry).prompt is None


def test_a_read_that_ends_late_does_not_replace_a_newer_one(roots, cat, monkeypatch):
    t = transcript(roots)
    t.user("the first prompt", origin="human")
    filler(t)
    t.write()
    prompt(cat)
    t.user("the second prompt", origin="human")
    t.flush()
    slow = entry_of(cat)
    real = catalog_module.read_prompt
    nested = []

    def late(listed, kept=None):
        """The read of the file as it is now, handed back only after the file has grown
        and another read of it has been kept."""
        mine = real(listed, kept)
        if not nested:
            nested.append(True)
            t.user("the third prompt", origin="human")
            t.flush()
            assert cat.details(entry_of(cat)).prompt.text == "the third prompt"
        return mine

    monkeypatch.setattr(catalog_module, "read_prompt", late)

    assert cat.details(slow).prompt.text == "the second prompt"   # one moment, its own
    newest = kept_of(cat, t)
    assert newest.prompt.text == "the third prompt"
    assert newest.offset == newest.size == t.path.stat().st_size
    assert prompt(cat).text == "the third prompt"


def test_what_is_kept_of_a_file_is_one_reads(roots, cat):
    t = transcript(roots)
    t.user("the first prompt", origin="human")
    filler(t)
    t.write()
    for n in range(5):
        t.user(f"prompt {n}", origin="human")
        path = t.flush()
        prompt(cat)
        kept, st, data = kept_of(cat, t), path.stat(), path.read_bytes()
        assert kept == Kept(st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, len(data),
                            hashlib.sha256(data[-WINDOW:]).digest(),
                            Said(kept.prompt.at, "you", f"prompt {n}"))


def test_only_the_most_recently_used_prompts_are_kept_and_a_dropped_one_is_read_whole(
        roots):
    cat = Catalog(roots, reuse_s=0, tail_max=2)
    for n in (1, 2, 3):
        t = transcript(roots, n)
        t.user(f"prompt {n}", origin="human")
        filler(t)
        t.write()

    for n in (1, 2, 1, 3):                           # 2 is the least recently used
        assert prompt(cat, n).text == f"prompt {n}"
    assert len(cat._prompts) == 2 and cat.prompt_whole == 3
    prompt(cat, 1)
    prompt(cat, 3)
    assert cat.prompt_whole == 3
    assert prompt(cat, 2).text == "prompt 2"
    assert cat.prompt_whole == 4


# --- every file -------------------------------------------------------------------------------

def test_the_kept_prompt_is_the_whole_reads_through_a_long_run_of_changes(roots, cat):
    rng = random.Random(20261110)
    serial = [0]
    path = transcript(roots).path
    path.parent.mkdir(parents=True)

    def lines(count: int) -> bytes:
        """`count` lines, no two ever the same, about one in six a typed message."""
        t = transcript(roots)
        for _ in range(count):
            serial[0] += 1
            text = f"line {serial[0]} " + "x" * rng.randrange(0, 900)
            kind = rng.randrange(6)
            if kind == 0:
                t.user(text, origin="human")
            elif kind == 1:
                t.user(text, origin=rng.choice(["task-notification", "channel", None]))
            else:
                t.assistant(text)
        return t.text().encode()

    path.write_bytes(lines(3))
    partial = b""
    seen = set()
    for step in range(400):
        op = rng.choice(["grow", "grow", "grow", "grow", "partial", "shrink", "rewrite",
                         "replace", "nothing", "empty"])
        size = path.stat().st_size
        if op == "grow" or (op == "partial" and partial):
            with open(path, "ab") as handle:         # a line being written is ended first
                handle.write(partial + lines(rng.randrange(1, 12)))
            partial = b""
        elif op == "partial":
            line = lines(1)
            cut = rng.randrange(1, len(line))        # whole JSON when only "\n" is cut
            with open(path, "ab") as handle:
                handle.write(line[:cut])
            partial = line[cut:]
        elif op == "shrink" and size:
            with open(path, "r+b") as handle:
                handle.truncate(rng.randrange(0, size))
            partial = b""
        elif op == "rewrite":
            # The whole file, in place, ending shorter or longer. (Writing over its
            # first bytes alone is the change the read does not see, tested above.)
            with open(path, "r+b") as handle:
                handle.write(lines(rng.randrange(1, 40)))
                handle.truncate()
            partial = b""
        elif op == "replace":
            spare = path.with_suffix(".new")
            spare.write_bytes(lines(rng.randrange(1, 40)))
            os.replace(spare, path)
            partial = b""
        elif op == "empty":
            path.write_bytes(b"")
            partial = b""
        stamp = (1_800_000_000 + step) * 1_000_000_000
        os.utime(path, ns=(stamp, stamp))
        seen.add(op)

        assert cat.details(entry_of(cat)).prompt == whole(cat), (step, op)

    assert len(seen) == 7
    assert cat.prompt_whole < cat.prompt_reads       # some reads were from the offset

"""The catalog's name read over roots the test kit writes: what names a session, how
little of it is read, and what is kept of that read beside the last-turn facts."""

from __future__ import annotations

import os

import pytest

from moxi_sessions import catalog as catalog_module
from moxi_sessions.catalog import NAME_MAX, Catalog, Facts, Said
from moxi_sessions.errors import Unreadable
from moxi_sessions.shape import CLAUDE, CODEX, OPENCODE
from moxi_sessions.source import HEAD_LINES
from moxi_sessions.testing import ClaudeTranscript, CodexRollout, OpenCodeDb
from moxi_sessions.testing.claude import START


def sid(n: int) -> str:
    return f"0190b000-0000-7000-8000-{n:012x}"


@pytest.fixture
def roots(tmp_path):
    """A root per harness, each empty: a projects root, a sessions root and a database."""
    projects = tmp_path / "projects"
    rollouts = tmp_path / "codex" / "sessions"
    projects.mkdir()
    rollouts.mkdir(parents=True)
    database = OpenCodeDb(tmp_path / "opencode").write()
    return {CLAUDE: projects, CODEX: rollouts, OPENCODE: database}


def transcript(roots, n: int = 1, **kw) -> ClaudeTranscript:
    return ClaudeTranscript(roots[CLAUDE], session_id=sid(n), **kw)


def past_head(t: ClaudeTranscript) -> None:
    """Write turns enough that what follows is past the lines a head read takes."""
    for n in range(HEAD_LINES // 2 + 1):
        t.user(f"prompt {n}", origin="human")
        t.assistant()


def touch(path, seconds: float) -> None:
    stamp = int((1_800_000_000 + seconds) * 1_000_000_000)
    os.utime(path, ns=(stamp, stamp))


def only(cat: Catalog):
    entries = cat.gather().entries
    assert len(entries) == 1
    return entries[0]


def facts(roots) -> Facts:
    cat = Catalog(roots)
    return cat.facts(only(cat))


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def turns_read(monkeypatch):
    """The arguments of every turn read the catalog makes, in order."""
    reads = []
    real = catalog_module.window

    def counted(source, **kw):
        reads.append(kw)
        return real(source, **kw)

    monkeypatch.setattr(catalog_module, "window", counted)
    return reads


# --- what names a session --------------------------------------------------------------------

def test_a_typed_name_comes_with_the_folder_and_the_start(roots):
    t = transcript(roots, cwd="/work/proj-a")
    t.user("please fix them", origin="human")
    t.title("fix the tests")
    t.assistant("All fixed.")
    t.write()

    assert facts(roots) == Facts(name="fix the tests", folder="/work/proj-a",
                                 started=START)


def test_a_typed_name_wins_over_a_newer_ai_title(roots):
    t = transcript(roots)
    t.user("go", origin="human")
    t.title("typed by hand")
    t.assistant()
    t.title("made up by the harness", ai=True)
    t.write()

    assert facts(roots).name == "typed by hand"


def test_an_ai_title_is_not_a_name(roots):
    t = transcript(roots, cwd="/work/proj-a")
    t.title("made up by the harness", ai=True)
    t.user("first", origin="human")
    t.title("made up again", ai=True)
    t.assistant()
    t.write()

    assert facts(roots) == Facts(name=None, folder="/work/proj-a", started=START)


def test_a_name_written_only_in_the_head_is_found(roots):
    t = transcript(roots)
    t.title("given at launch")
    for n in range(6):
        t.user(f"prompt {n}", origin="human")
        t.assistant()
    t.write()

    assert facts(roots).name == "given at launch"


def test_a_name_in_the_last_turn_wins_over_the_head_name(roots):
    t = transcript(roots)
    t.title("given at launch")
    for n in range(3):
        t.user(f"prompt {n}", origin="human")
        t.assistant()
    t.title("renamed later")
    t.write()

    assert facts(roots).name == "renamed later"


def test_a_session_whose_record_says_nothing_has_no_fact(roots):
    t = transcript(roots)
    t.other("mode", mode="default")
    t.write()

    assert facts(roots) == Facts()


def test_a_codex_session_has_its_folder_and_start_and_no_name(roots, turns_read):
    r = CodexRollout(roots[CODEX], thread_id="0190c000-0000-7000-8000-000000000001",
                     cwd="/work/proj-b", start=START + 5)
    r.turn()
    r.user("do it")
    r.write()
    cat = Catalog(roots)

    assert cat.facts(only(cat)) == Facts(name=None, folder="/work/proj-b",
                                         started=START + 5)
    assert turns_read == [] and cat.name_reads == 0      # no turn holds a name to read


def test_an_opencode_session_has_its_folder_and_start_and_no_name(roots, turns_read):
    db = OpenCodeDb(roots[OPENCODE].parent)
    session = db.session(title="a title of unknown hand", directory="/work/proj-b",
                         at=START + 7)
    session.user("hello")
    db.write()
    cat = Catalog(roots)

    assert cat.facts(only(cat)) == Facts(name=None, folder="/work/proj-b",
                                         started=START + 7)
    assert turns_read == [] and cat.name_reads == 0


# --- how little is read ------------------------------------------------------------------------

def test_one_turn_is_read_and_none_before_it(roots, turns_read):
    t = transcript(roots)
    past_head(t)
    t.user("the real prompt", origin="human")
    t.title("named in an earlier turn")
    t.assistant()
    for n in range(3):
        t.user(f"notice {n}", origin="task-notification")
        t.assistant()
    t.write()
    cat = Catalog(roots)

    assert cat.facts(only(cat)).name is None     # the last turn holds no name
    assert turns_read == [{"turns": 1}]
    assert (cat.head_reads, cat.name_reads, cat.tail_reads) == (1, 1, 0)


def test_an_unchanged_file_is_read_once_and_a_changed_one_again(roots, turns_read):
    t = transcript(roots)
    t.user("one", origin="human")
    t.write()
    clock = Clock()
    cat = Catalog(roots, clock=clock)

    assert cat.facts(only(cat)).name is None
    clock.now += 5
    assert cat.facts(only(cat)).name is None     # a session with no name is kept too
    assert (cat.head_reads, cat.name_reads) == (1, 1)

    t.title("named since")
    t.flush()
    clock.now += 5
    assert cat.facts(only(cat)).name == "named since"
    assert cat.name_reads == 2 and len(turns_read) == 2


def test_a_failed_turn_read_gives_no_name_and_is_tried_again(roots, monkeypatch):
    t = transcript(roots, cwd="/work/proj-a")
    past_head(t)
    t.user("one", origin="human")
    t.title("typed by hand")
    t.write()
    cat = Catalog(roots)
    real = catalog_module.window

    def broken(source, **kw):
        raise Unreadable("the file cannot be read")

    monkeypatch.setattr(catalog_module, "window", broken)
    assert cat.facts(only(cat)) == Facts(name=None, folder="/work/proj-a",
                                         started=START)

    monkeypatch.setattr(catalog_module, "window", real)
    assert cat.facts(only(cat)).name == "typed by hand"
    assert cat.name_reads == 2


def test_a_failed_head_read_leaves_the_folder_and_start_unread(roots, monkeypatch):
    t = transcript(roots, cwd="/work/proj-a")
    past_head(t)
    t.user("one", origin="human")
    t.title("typed by hand")
    t.write()
    cat = Catalog(roots)
    monkeypatch.setattr(catalog_module, "read_head", lambda listed, imports=None: None)

    assert cat.facts(only(cat)) == Facts(name="typed by hand")


def test_a_file_that_is_gone_has_no_fact(roots):
    t = transcript(roots)
    t.title("given at launch")
    t.user()
    t.write()
    cat = Catalog(roots)
    entry = only(cat)
    t.path.unlink()

    assert cat.facts(entry) == Facts()


# --- what is kept, beside the last-turn facts ----------------------------------------------

def test_a_session_a_row_was_read_of_is_named_with_no_further_read(roots, turns_read):
    t = transcript(roots)
    t.user("my own words", origin="human")
    t.title("typed by hand")
    t.write()
    cat = Catalog(roots)
    entry = only(cat)

    cat.details(entry)
    read = len(turns_read)
    assert cat.facts(entry).name == "typed by hand"
    assert len(turns_read) == read
    assert (cat.head_reads, cat.tail_reads, cat.name_reads) == (1, 1, 0)


def test_a_row_read_after_the_name_still_carries_every_fact(roots):
    t = transcript(roots)
    t.user("my own words", origin="human")
    t.title("typed by hand")
    t.assistant("An answer.", model="claude-model-y")
    t.write()
    cat = Catalog(roots)
    entry = only(cat)

    assert cat.facts(entry).name == "typed by hand"
    found = cat.details(entry)

    assert found.prompt == Said(START, "you", "my own words")
    assert found.activity == Said(START + 1, "agent", "An answer.")
    assert found.model == "claude-model-y" and found.name == "typed by hand"
    assert (cat.name_reads, cat.tail_reads) == (1, 1)
    assert found == Catalog(roots).details(entry)        # as a catalog that named nothing


def test_last_turn_facts_of_an_older_file_do_not_name_it(roots):
    t = transcript(roots)
    t.user("one", origin="human")
    t.title("the first name")
    t.write()
    clock = Clock()
    cat = Catalog(roots, clock=clock)
    cat.details(only(cat))

    t.title("the second name")
    t.flush()
    clock.now += 5

    assert cat.facts(only(cat)).name == "the second name"
    assert cat.name_reads == 1


def test_only_a_name_is_kept_of_the_turn_read_for_it(roots):
    t = transcript(roots)
    t.user("words nobody asked to keep", origin="human")
    t.title("typed by hand")
    t.assistant("An answer nobody asked to keep.")
    t.write()
    cat = Catalog(roots)
    entry = only(cat)

    cat.facts(entry)

    listed = entry.listed
    assert dict(cat._names) == {entry.path: ((listed.size, listed.mtime_ns),
                                             "typed by hand")}
    assert not cat._tails


def test_only_the_most_recently_used_names_are_kept(roots):
    for n in (1, 2, 3):
        t = transcript(roots, n)
        t.user("go", origin="human")
        touch(t.write(), n)
    cat = Catalog(roots, name_max=2)
    third, second, first = cat.gather().entries

    for entry in (first, second, first, third):     # `second` is the least recently used
        cat.facts(entry)
    assert cat.name_reads == 3
    cat.facts(first)
    cat.facts(third)
    assert cat.name_reads == 3
    cat.facts(second)
    assert cat.name_reads == 4


def test_a_catalog_keeps_five_thousand_names_unless_told():
    assert NAME_MAX == 5000 and Catalog()._name_max == NAME_MAX

"""The catalog over roots the test kit writes: the main sessions of each harness, their
order, the details of one, and what a catalog keeps of what it read."""

from __future__ import annotations

import json
import os
import threading
import time

import pytest

from moxi_sessions import catalog as catalog_module
from moxi_sessions.catalog import (ACTIVITY_BYTES, Catalog, Details, Said, Unread,
                                   by_program, cut, reset, shared)
from moxi_sessions.errors import Unreadable
from moxi_sessions.shape import CLAUDE, CODEX, OPENCODE, UNKNOWN
from moxi_sessions.source import HEAD_LINES
from moxi_sessions.view import TEXT_MAX
from moxi_sessions.testing import ClaudeTranscript, CodexRollout, OpenCodeDb
from moxi_sessions.testing.claude import START
from moxi_sessions.testing.codex import spawn_meta

from conftest import write_config


def sid(n: int) -> str:
    return f"0190b000-0000-7000-8000-{n:012x}"


def tid(n: int) -> str:
    return f"0190c000-0000-7000-8000-{n:012x}"


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


def rollout(roots, n: int = 1, **kw) -> CodexRollout:
    return CodexRollout(roots[CODEX], thread_id=tid(n), **kw)


def database(roots) -> OpenCodeDb:
    return OpenCodeDb(roots[OPENCODE].parent)


def touch(path, seconds: float) -> None:
    """Set the file's mtime to `seconds` past an arbitrary day."""
    stamp = int((1_800_000_000 + seconds) * 1_000_000_000)
    os.utime(path, ns=(stamp, stamp))


def only(cat: Catalog):
    entries = cat.gather().entries
    assert len(entries) == 1
    return entries[0]


def details(roots) -> Details:
    cat = Catalog(roots)
    return cat.details(only(cat))


def without(t: ClaudeTranscript, key: str) -> None:
    """Take `key` off every line the builder holds."""
    t.lines = [json.dumps({k: v for k, v in json.loads(line).items() if k != key})
               for line in t.lines]


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


# --- the entries ---------------------------------------------------------------------------

def test_claude_lists_main_sessions_with_their_subagent_counts(roots):
    one = transcript(roots, 1)
    one.user()
    one.subagent("a01").user("look")
    one.subagent("a02").user("look")
    one.subagent("a03", workflow="wf_run1").user("look")
    one.write()
    two = transcript(roots, 2, project="-work-proj-b")
    two.user()
    two.write()

    entries = {e.session: e for e in Catalog(roots).gather().entries}

    assert set(entries) == {sid(1), sid(2)}
    assert entries[sid(1)].subagents == 3 and entries[sid(2)].subagents == 0
    entry = entries[sid(1)]
    assert entry.harness == CLAUDE and entry.path == str(one.path)
    assert entry.size == one.path.stat().st_size
    assert entry.changed_ns == one.path.stat().st_mtime_ns
    assert entry.changed == pytest.approx(one.path.stat().st_mtime)


def test_opencode_lists_sessions_with_no_parent_and_counts_their_children(roots):
    db = database(roots)
    parent = db.session(at=START + 50)
    parent.child()
    parent.child()
    alone = db.session(at=START + 60)
    db.write()

    entries = {e.session: e for e in Catalog(roots).gather().entries}

    assert set(entries) == {parent.id, alone.id}
    assert entries[parent.id].subagents == 2 and entries[alone.id].subagents == 0
    assert entries[parent.id].harness == OPENCODE
    assert entries[parent.id].path == str(roots[OPENCODE])
    assert entries[parent.id].size is None
    assert entries[alone.id].changed == START + 60


def test_codex_leaves_spawned_threads_out_and_keeps_a_fork(roots):
    main = rollout(roots, 1)
    main.turn()
    main.write()
    for n in (2, 3):
        child = rollout(roots, n, meta_fields=spawn_meta(tid(1)))
        child.turn()
        child.write()
    fork = rollout(roots, 4, meta_fields={"forked_from_id": tid(1)})
    fork.turn()
    fork.write()

    gathered = Catalog(roots).gather()
    entries = {e.session: e for e in gathered.entries}

    assert set(entries) == {tid(1), tid(4)}
    assert entries[tid(1)].subagents == 2 and entries[tid(4)].subagents == 0
    assert entries[tid(1)].harness == CODEX and entries[tid(1)].path == str(main.path)
    assert gathered.unreadable == 0
    assert gathered.listed == (CLAUDE, CODEX, OPENCODE)


def test_compressed_rollouts_are_counted_and_not_listed(roots):
    main = rollout(roots, 1)
    main.turn()
    main.write()
    for n in (2, 3):
        packed = main.path.with_name(f"rollout-2026-10-30T10-00-00-{tid(n)}.jsonl.zst")
        packed.write_bytes(b"not read")

    gathered = Catalog(roots).gather()

    assert [e.session for e in gathered.entries] == [tid(1)]
    assert gathered.unreadable == 2


def test_a_rollout_whose_first_line_is_missing_is_still_listed(roots):
    headless = rollout(roots, 1, meta=False)
    headless.turn()
    headless.write()

    cat = Catalog(roots)

    assert only(cat).session == tid(1)
    assert cat.kept_head(only(cat)).spawn is UNKNOWN


def test_entries_are_newest_first_then_by_harness_then_by_session(roots):
    for n, seconds in ((1, 10), (2, 30), (3, 20), (4, 20)):
        t = transcript(roots, n)
        t.user()
        touch(t.write(), seconds)
    r = rollout(roots, 9)
    r.turn()
    touch(r.write(), 20)

    entries = Catalog(roots).gather().entries

    assert [(e.harness, e.session) for e in entries] == [
        (CLAUDE, sid(2)), (CLAUDE, sid(3)), (CLAUDE, sid(4)), (CODEX, tid(9)),
        (CLAUDE, sid(1))]
    assert [e.order for e in entries] == sorted(e.order for e in entries)


def test_a_root_that_cannot_be_listed_is_reported_and_the_others_answer(roots, tmp_path):
    t = transcript(roots)
    t.user()
    t.write()
    db = database(roots)
    session = db.session()
    db.write()
    roots[CODEX] = tmp_path / "nowhere"

    gathered = Catalog(roots).gather()

    assert {e.session for e in gathered.entries} == {sid(1), session.id}
    assert len(gathered.roots) == 1
    failure = gathered.roots[0]
    assert failure.harness == CODEX and failure.root == str(tmp_path / "nowhere")
    assert "does not exist" in failure.reason
    assert gathered.listed == (CLAUDE, OPENCODE)
    assert gathered.unreadable is None           # no count of a root that was not read


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A home with no harness in it, and an environment that names no root: each
    harness's root is its default one, and none exists."""
    home = tmp_path / "bare-home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    for name in ("CLAUDE_CONFIG_DIR", "CODEX_HOME", "XDG_DATA_HOME"):
        monkeypatch.delenv(name, raising=False)
    return home


def test_a_machine_with_no_harness_gathers_nothing_and_no_failure(home):
    gathered = Catalog().gather()

    assert gathered.entries == () and gathered.roots == () and gathered.listed == ()
    assert gathered.unreadable is None


def test_a_harness_whose_default_root_does_not_exist_is_absent_not_a_failure(home):
    t = ClaudeTranscript(home / ".claude" / "projects", session_id=sid(1))
    t.user()
    t.write()

    gathered = Catalog().gather()

    assert [e.session for e in gathered.entries] == [sid(1)]
    assert gathered.roots == () and gathered.listed == (CLAUDE,)


def test_a_root_the_environment_names_that_does_not_exist_is_absent(home, monkeypatch,
                                                                    tmp_path):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "nowhere"))

    gathered = Catalog().gather()

    assert gathered.roots == () and gathered.listed == ()


@pytest.mark.parametrize("harness, key", [(CLAUDE, "claude.projects"),
                                          (CODEX, "codex.sessions"),
                                          (OPENCODE, "opencode.database")])
def test_a_configured_root_that_does_not_exist_is_a_failure(home, own_config, tmp_path,
                                                            harness, key):
    table, name = key.split(".")
    write_config(own_config, f'[{table}]\n{name} = "{tmp_path / "nowhere"}"\n')

    gathered = Catalog().gather()

    failure, = gathered.roots
    assert failure.harness == harness and failure.root == str(tmp_path / "nowhere")
    assert failure.reason
    assert gathered.listed == ()


def test_a_default_root_that_exists_and_cannot_be_read_is_a_failure(home):
    (home / ".claude").mkdir()
    (home / ".claude" / "projects").write_text("not a folder")
    database = home / ".local" / "share" / "opencode" / "opencode.db"
    database.parent.mkdir(parents=True)
    database.write_text("not a database")

    gathered = Catalog().gather()

    assert [f.harness for f in gathered.roots] == [CLAUDE, OPENCODE]
    assert all(f.reason for f in gathered.roots)
    assert gathered.listed == ()


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads a folder without permission")
def test_a_default_root_without_permission_is_a_failure(home):
    locked = home / ".codex" / "sessions"
    locked.mkdir(parents=True)
    locked.chmod(0)
    try:
        gathered = Catalog().gather()
    finally:
        locked.chmod(0o700)

    failure, = gathered.roots
    assert failure.harness == CODEX and "unreadable" in failure.reason


not_root = pytest.mark.skipif(os.geteuid() == 0,
                              reason="root lists a folder without permission")


@pytest.fixture
def locked():
    """`locked(path)` takes every permission off a folder or file until the test ends."""
    taken = []

    def lock(path):
        taken.append((path, path.stat().st_mode))
        path.chmod(0)

    yield lock
    for path, mode in reversed(taken):
        path.chmod(mode)


@not_root
def test_a_project_folder_that_cannot_be_listed_is_counted_as_an_unread_part(roots,
                                                                             locked):
    seen = transcript(roots, 1)
    seen.user()
    seen.write()
    hidden = transcript(roots, 2, project="-work-proj-b")
    hidden.user()
    locked(hidden.write().parent)

    gathered = Catalog(roots).gather()

    assert [e.session for e in gathered.entries] == [sid(1)]
    assert gathered.roots == (Unread(CLAUDE, str(roots[CLAUDE]), None, 1),)
    assert CLAUDE in gathered.listed


@not_root
def test_a_session_whose_subagents_folder_cannot_be_listed_has_no_count(roots, locked):
    one = transcript(roots, 1)
    one.user()
    sub = one.subagent("a0190b0000000001")
    sub.user("look")
    path = one.write()
    two = transcript(roots, 2)
    two.user()
    two.write()
    locked(path.with_suffix("") / "subagents")

    gathered = Catalog(roots).gather()
    entries = {e.session: e for e in gathered.entries}

    assert entries[sid(1)].subagents is None and entries[sid(2)].subagents == 0
    assert gathered.roots == (Unread(CLAUDE, str(roots[CLAUDE]), None, 1),)


@not_root
def test_a_codex_day_that_cannot_be_listed_is_counted_as_an_unread_part(roots, locked):
    r = rollout(roots, 1)
    r.user("Do the thing.")
    locked(r.write().parent)

    gathered = Catalog(roots).gather()

    assert gathered.entries == () and gathered.unreadable == 0
    assert gathered.roots == (Unread(CODEX, str(roots[CODEX]), None, 1),)


@not_root
def test_a_rollout_that_cannot_be_opened_is_listed_and_counted_as_unread(roots, locked):
    r = rollout(roots, 1)
    r.user("Do the thing.")
    locked(r.write())

    gathered = Catalog(roots).gather()

    assert [e.session for e in gathered.entries] == [tid(1)]
    assert gathered.roots == (Unread(CODEX, str(roots[CODEX]), None, 1),)


def test_a_gather_is_reused_for_two_seconds(roots):
    clock = Clock()
    cat = Catalog(roots, clock=clock)
    first = cat.gather()
    t = transcript(roots)
    t.user()
    t.write()

    clock.now += 1.9
    assert cat.gather() is first
    clock.now += 0.2
    assert [e.session for e in cat.gather().entries] == [sid(1)]


def test_callers_that_arrive_during_a_gather_take_its_result(roots, monkeypatch):
    t = transcript(roots)
    t.user()
    t.write()
    calls = []
    real = catalog_module.sources

    def slow(harness, **kw):
        calls.append(harness)
        time.sleep(0.05)
        return real(harness, **kw)

    monkeypatch.setattr(catalog_module, "sources", slow)
    cat = Catalog(roots)
    results = []
    threads = [threading.Thread(target=lambda: results.append(cat.gather()))
               for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(calls) == 3                      # one listing of each root
    assert all(result is results[0] for result in results)


def test_the_process_keeps_one_catalog_until_it_is_reset():
    reset()
    one = shared()
    assert shared() is one
    reset()
    assert shared() is not one
    reset()


# --- the details: head facts ---------------------------------------------------------------

def test_a_claude_session_gives_every_detail(roots):
    t = transcript(roots, cwd="/work/proj-a")
    t.title("fix the tests")
    t.user("please fix them", origin="human")
    t.assistant("All fixed.", model="claude-model-y")
    t.write()

    assert details(roots) == Details(
        folder="/work/proj-a", started=START, by_program=None, name="fix the tests",
        model="claude-model-y", activity=Said(START + 1, "agent", "All fixed."),
        prompt=Said(START, "you", "please fix them"))


def test_a_session_whose_record_says_nothing_gives_no_detail(roots):
    t = transcript(roots)
    t.other("mode", mode="default")
    t.write()

    assert details(roots) == Details()


def test_a_codex_session_gives_its_folder_start_and_model_and_no_name(roots):
    r = rollout(roots, cwd="/work/proj-b", start=START + 5)
    r.turn(model="codex-model-y")
    r.user("do it")
    r.assistant("Did it.")
    r.write()

    found = details(roots)

    assert (found.folder, found.started) == ("/work/proj-b", START + 5)
    assert found.by_program is None and found.name is None
    assert found.model == "codex-model-y"
    assert found.activity.who == "agent" and found.activity.text == "Did it."
    assert found.prompt is None                 # Codex names no submitter


def test_an_opencode_session_gives_its_folder_start_and_model_and_no_name(roots):
    db = database(roots)
    session = db.session(title="a title of unknown hand", directory="/work/proj-b",
                         at=START + 7)
    session.user("hello")
    answer = session.assistant(model=("opencode", "model-z"))
    answer.step(text="Hello back.")
    answer.complete()
    db.write()

    found = details(roots)

    assert (found.folder, found.started) == ("/work/proj-b", START + 7)
    assert found.by_program is None and found.name is None
    assert found.model == "model-z"
    assert found.activity.who == "agent" and found.activity.text == "Hello back."
    assert found.prompt is None                 # OpenCode names no submitter


@pytest.mark.parametrize("entrypoint, expected", [
    ("sdk-cli", True), ("sdk-py", True), ("cli", None), ("claude-vscode", None)])
def test_a_program_is_told_by_the_entrypoint_the_head_writes(roots, entrypoint, expected):
    t = transcript(roots, entrypoint=entrypoint)
    t.user("go")
    t.write()

    assert details(roots).by_program is expected
    assert by_program(entrypoint) is expected


def test_a_head_with_no_entrypoint_names_no_program(roots):
    t = transcript(roots)
    t.user("go", origin="human")
    without(t, "entrypoint")
    t.write()

    found = details(roots)

    assert found.by_program is None and by_program(UNKNOWN) is None
    assert found.activity.who == "you"


# --- the details: the name -------------------------------------------------------------------

def test_a_typed_name_wins_over_a_newer_ai_title(roots):
    t = transcript(roots)
    t.user("first", origin="human")
    t.assistant()
    t.user("second", origin="human")
    t.title("typed by hand")
    t.assistant()
    t.title("made up by the harness", ai=True)
    t.write()

    assert details(roots).name == "typed by hand"


def test_an_ai_title_alone_is_not_a_name(roots):
    t = transcript(roots)
    t.title("made up by the harness", ai=True)
    t.user("first", origin="human")
    t.title("made up again", ai=True)
    t.assistant()
    t.write()

    assert details(roots).name is None


def test_a_name_written_only_in_the_head_is_found(roots):
    t = transcript(roots)
    t.title("an older name")
    t.title("given at launch")
    for n in range(6):
        t.user(f"prompt {n}", origin="human")
        t.assistant()
    t.write()

    assert details(roots).name == "given at launch"


def test_a_name_in_the_last_turn_wins_over_the_head_name(roots):
    t = transcript(roots)
    t.title("given at launch")
    for n in range(3):
        t.user(f"prompt {n}", origin="human")
        t.assistant()
    t.title("renamed later")
    t.write()

    assert details(roots).name == "renamed later"


# --- the details: activity and prompt ----------------------------------------------------------

def test_a_typed_prompt_is_marked_as_the_persons(roots):
    t = transcript(roots)
    t.user("my own words", origin="human")
    t.write()

    found = details(roots)

    assert found.activity == Said(START, "you", "my own words")
    assert found.prompt == Said(START, "you", "my own words")


def test_an_injected_message_is_shown_with_no_speaker(roots):
    t = transcript(roots)
    t.user("my own words", origin="human")
    t.assistant()
    t.user("a task finished", origin="task-notification")
    t.write()

    found = details(roots)

    assert found.activity == Said(START + 2, None, "a task finished")
    assert found.prompt == Said(START, "you", "my own words")


def test_a_kept_prompt_is_no_longer_than_a_caller_is_sent(roots):
    t = transcript(roots)
    t.user("é" * TEXT_MAX, origin="human")          # twice the bytes an answer carries
    t.assistant("Done.")
    t.write()
    cat = Catalog(roots)

    prompt = cat.details(only(cat)).prompt

    assert prompt.truncated is True
    assert prompt.text == "é" * (TEXT_MAX // 2)
    (_, kept), = cat._tails.values()
    assert len(kept.prompt.text.encode()) == TEXT_MAX

    short = transcript(roots, 2)
    short.user("my own words", origin="human")
    short.write()
    assert details_of(Catalog(roots), sid(2)).prompt.truncated is False


def details_of(cat: Catalog, session: str) -> Details:
    return cat.details(next(e for e in cat.gather().entries if e.session == session))


def test_a_message_with_no_origin_is_shown_with_no_speaker_and_is_no_prompt(roots):
    t = transcript(roots)
    t.user("who sent this")
    t.write()

    found = details(roots)

    assert found.activity == Said(START, None, "who sent this")
    assert found.prompt is None


def test_activity_is_the_newest_message_with_text(roots):
    t = transcript(roots)
    t.user("read it", origin="human")
    request = t.request()
    request.text("Reading.")
    call = request.tool_use("Read", {"file_path": "/work/project/notes.md"})
    t.tool_result(call, "the notes")
    t.assistant().thinking("what next?")
    t.write()

    assert details(roots).activity.text == "Done."


def test_activity_text_is_cut_at_a_character_boundary(roots):
    t = transcript(roots)
    t.user("go", origin="human")
    t.assistant("ab" + "é" * 400)
    t.write()

    text = details(roots).activity.text

    assert text == "ab" + "é" * 149
    assert len(text.encode()) == ACTIVITY_BYTES
    assert cut("a" + "é" * 400) == "a" + "é" * 149      # 299 bytes: no half character
    assert cut("short") == "short"


def test_the_prompt_is_found_behind_injected_turns(roots):
    t = transcript(roots)
    t.user("the real prompt", origin="human")
    t.assistant()
    for n in range(4):
        t.user(f"notice {n}", origin="task-notification")
        t.assistant()
    t.write()

    found = details(roots)

    assert found.prompt == Said(START, "you", "the real prompt")
    assert found.activity.who == "agent"


def test_the_prompt_is_not_looked_for_past_four_more_turns(roots):
    t = transcript(roots)
    t.user("the real prompt", origin="human")
    t.assistant()
    for n in range(5):
        t.user(f"notice {n}", origin="task-notification")
        t.assistant()
    t.write()

    assert details(roots).prompt is None


def test_a_program_session_has_no_prompt_no_speaker_and_reads_one_turn(roots, monkeypatch):
    t = transcript(roots, entrypoint="sdk-py")
    t.user("typed, the record says", origin="human")
    t.assistant()
    t.user("typed again", origin="human")
    t.write()
    reads = []
    real = catalog_module.window

    def counted(source, **kw):
        reads.append(kw)
        return real(source, **kw)

    monkeypatch.setattr(catalog_module, "window", counted)

    found = details(roots)

    assert found.by_program is True and found.prompt is None
    assert found.activity == Said(START + 2, None, "typed again")
    assert reads == [{"turns": 1}]

    t.assistant("An answer.")
    t.write()
    assert details(roots).activity == Said(START + 3, None, "An answer.")


def test_a_model_the_harness_made_up_is_skipped(roots):
    t = transcript(roots)
    t.user("go", origin="human")
    t.assistant("A real answer.", model="claude-model-y")
    t.synthetic("No response requested.")
    t.write()

    found = details(roots)

    assert found.model == "claude-model-y"
    assert found.activity.text == "A real answer."


def test_a_turn_that_names_only_a_made_up_model_gives_none(roots):
    t = transcript(roots)
    t.user("go", origin="human")
    t.synthetic()
    t.write()

    found = details(roots)

    assert found.model is None
    assert found.activity == Said(START, "you", "go")


# --- what is kept ----------------------------------------------------------------------------

def test_an_unchanged_session_is_read_once_and_a_changed_one_again(roots):
    t = transcript(roots)
    t.user("one", origin="human")
    t.write()
    clock = Clock()
    cat = Catalog(roots, clock=clock)

    first = cat.details(only(cat))
    clock.now += 5
    assert cat.details(only(cat)) == first
    assert (cat.head_reads, cat.tail_reads) == (1, 1)

    t.assistant("two")
    t.flush()
    clock.now += 5
    assert cat.details(only(cat)).activity.text == "two"
    assert cat.tail_reads == 2


def test_an_unchanged_opencode_session_is_read_once_and_a_changed_one_again(roots):
    db = database(roots)
    session = db.session()
    session.user("hello")
    db.write()
    clock = Clock()
    cat = Catalog(roots, clock=clock)

    assert cat.details(only(cat)).activity.text == "hello"
    clock.now += 5
    cat.details(only(cat))
    assert (cat.head_reads, cat.tail_reads) == (0, 1)

    answer = session.assistant()
    answer.step(text="Hello back.")
    session.update()
    db.write()
    clock.now += 5
    assert cat.details(only(cat)).activity.text == "Hello back."
    assert cat.tail_reads == 2


def test_a_failed_last_turn_read_gives_nulls_and_is_tried_again(roots, monkeypatch):
    t = transcript(roots, cwd="/work/proj-a")
    t.user("one", origin="human")
    t.write()
    cat = Catalog(roots)
    real = catalog_module.window

    def broken(source, **kw):
        raise Unreadable("the file cannot be read")

    monkeypatch.setattr(catalog_module, "window", broken)
    failed = cat.details(only(cat))
    assert failed == Details(folder="/work/proj-a", started=START)

    monkeypatch.setattr(catalog_module, "window", real)
    assert cat.details(only(cat)).activity.text == "one"
    assert cat.tail_reads == 2


def test_a_failed_head_read_gives_nulls_and_is_tried_again(roots, monkeypatch):
    t = transcript(roots, cwd="/work/proj-a")
    t.title("given at launch")
    t.user("zero", origin="human")
    t.user("one", origin="human")
    t.write()
    cat = Catalog(roots)
    real = catalog_module.read_head
    monkeypatch.setattr(catalog_module, "read_head", lambda listed, imports=None: None)

    failed = cat.details(only(cat))
    assert (failed.folder, failed.started, failed.name) == (None, None, None)
    assert failed.activity.text == "one"
    assert cat.kept_head(only(cat)) is None

    monkeypatch.setattr(catalog_module, "read_head", real)
    found = cat.details(only(cat))
    assert (found.folder, found.name) == ("/work/proj-a", "given at launch")
    assert cat.head_reads == 2


def test_a_file_that_cannot_be_opened_has_no_head(roots):
    t = transcript(roots)
    t.user()
    t.write()
    cat = Catalog(roots)
    entry = only(cat)
    t.path.unlink()

    assert cat.head(entry) is None
    assert cat.details(entry) == Details()


def test_only_the_most_recently_used_sessions_are_kept(roots):
    for n in (1, 2, 3):
        t = transcript(roots, n)
        t.user("go", origin="human")
        touch(t.write(), n)
    cat = Catalog(roots, tail_max=2)
    third, second, first = cat.gather().entries

    for entry in (first, second, first, third):     # `second` is the least recently used
        cat.details(entry)
    assert cat.tail_reads == 3
    cat.details(first)
    cat.details(third)
    assert cat.tail_reads == 3
    cat.details(second)
    assert cat.tail_reads == 4


def test_a_short_head_is_read_again_when_the_file_grows(roots):
    t = transcript(roots)
    t.other("mode", mode="default")
    t.write()
    clock = Clock()
    cat = Catalog(roots, clock=clock)

    assert cat.head(only(cat)).folder is None
    assert cat.head(only(cat)).settled is False
    assert cat.head_reads == 1                  # the file has not grown

    t.user("go", origin="human")
    t.title("named a little later")
    t.flush()
    clock.now += 5
    head = cat.head(only(cat))

    assert (head.folder, head.started, head.name) == (
        "/work/project", START, "named a little later")
    assert cat.head_reads == 2


def test_a_head_the_file_has_outgrown_is_read_once(roots):
    t = transcript(roots)
    for n in range(HEAD_LINES):
        t.user(f"prompt {n}", origin="human")
    t.write()
    clock = Clock()
    cat = Catalog(roots, clock=clock)
    assert cat.head(only(cat)).settled is True

    t.user("more", origin="human")
    t.title("a name past the head")
    t.flush()
    clock.now += 5

    assert cat.head(only(cat)).name is None
    assert cat.head_reads == 1
    assert cat.details(only(cat)).name == "a name past the head"   # the last turn has it


def test_a_file_written_anew_under_the_same_path_is_read_again(roots):
    t = transcript(roots, cwd="/work/proj-a")
    for n in range(HEAD_LINES):
        t.user(f"prompt {n}", origin="human")
    t.write()
    clock = Clock()
    cat = Catalog(roots, clock=clock)
    assert cat.head(only(cat)).folder == "/work/proj-a"

    again = transcript(roots, cwd="/work/proj-b")
    again.user("go", origin="human")
    again.write()                               # a new file, with a new inode
    clock.now += 5

    assert cat.head(only(cat)).folder == "/work/proj-b"
    assert cat.head_reads == 2


def test_a_file_written_again_in_place_and_smaller_has_its_head_read_again(roots):
    t = transcript(roots, cwd="/work/proj-a")
    for n in range(HEAD_LINES):
        t.user(f"prompt {n}", origin="human")
    path = t.write()
    clock = Clock()
    cat = Catalog(roots, clock=clock)
    assert cat.head(only(cat)).settled is True

    other = ClaudeTranscript(roots[CLAUDE], session_id=sid(2), cwd="/work/proj-b")
    other.user("go", origin="human")
    lines = other.write().read_bytes()
    other.path.unlink()
    with open(path, "r+b") as handle:           # the same file, the same inode
        handle.truncate(0)
        handle.write(lines)
    clock.now += 5

    assert cat.head(only(cat)).folder == "/work/proj-b"
    assert cat.head_reads == 2


def test_a_gather_drops_the_heads_of_files_it_no_longer_lists(roots):
    kept, gone = transcript(roots, 1), transcript(roots, 2)
    thread = rollout(roots, 1)
    for each in (kept, gone, thread):
        each.user("go")
        each.write()
    clock = Clock()
    cat = Catalog(roots, clock=clock)
    cat.read_heads()
    assert set(cat._heads) == {str(kept.path), str(gone.path), str(thread.path)}

    gone.path.unlink()
    thread.path.unlink()
    clock.now += 5
    cat.gather()

    assert set(cat._heads) == {str(kept.path)}


def test_a_root_that_was_not_listed_keeps_its_heads(roots):
    t = transcript(roots, 1)
    t.user("go")
    t.write()
    clock = Clock()
    cat = Catalog(roots, clock=clock)
    cat.read_heads()

    moved = roots[CLAUDE].with_name("moved")
    roots[CLAUDE].rename(moved)                  # the root is away for one gather
    clock.now += 5
    assert [u.harness for u in cat.gather().roots] == [CLAUDE]
    assert set(cat._heads) == {str(t.path)}

    moved.rename(roots[CLAUDE])
    clock.now += 5
    assert cat.head(only(cat)).folder == t.cwd
    assert cat.head_reads == 1


def test_a_rollout_head_is_read_once_and_one_with_no_first_line_again(roots):
    whole = rollout(roots, 1)
    whole.turn()
    whole.write()
    headless = rollout(roots, 2, meta=False)
    headless.turn()
    headless.write()
    clock = Clock()
    cat = Catalog(roots, clock=clock)

    cat.gather()
    assert cat.head_reads == 2
    for each in (whole, headless):
        each.user("more")
        each.flush()
    clock.now += 5
    cat.gather()

    assert cat.head_reads == 3


def test_reading_some_heads_reads_those_and_no_others(roots):
    for n in (1, 2, 3):
        t = transcript(roots, n, cwd=f"/work/proj-{n}")
        t.user()
        touch(t.write(), n)
    cat = Catalog(roots)
    third, second, first = cat.gather().entries

    cat.read_heads([first, third])

    assert cat.head_reads == 2
    assert cat.kept_head(first).folder == "/work/proj-1"
    assert cat.kept_head(second) is None
    cat.read_heads([first, third])
    assert cat.head_reads == 2


def test_two_callers_asking_for_every_head_read_each_head_once(roots, monkeypatch):
    for n in range(1, 21):
        t = transcript(roots, n, cwd=f"/work/proj-{n}")
        t.user()
        t.write()
    r = rollout(roots, 1)
    r.turn()
    r.write()
    seen = []
    real = catalog_module.read_head

    def slow(listed, imports=None):
        seen.append(listed.source.path)
        time.sleep(0.002)
        return real(listed, imports)

    monkeypatch.setattr(catalog_module, "read_head", slow)
    cat = Catalog(roots)
    start = threading.Barrier(2)

    def ask():
        start.wait()
        cat.read_heads()

    threads = [threading.Thread(target=ask) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(seen) == 21 and len(set(seen)) == 21
    assert cat.head_reads == 21
    assert all(cat.kept_head(e).folder is not None for e in cat.gather().entries)
    cat.read_heads()
    assert cat.head_reads == 21

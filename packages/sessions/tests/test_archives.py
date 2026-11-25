"""Archive folders: the live root first and then the archive folders in order, shadowing,
the answer test (one wait per call, offline never `miss`), `corpus`'s listings, the read
and find verbs over archives, nothing changed in an archive folder, the guard over archive
folders by their written path, setup, the doctor's `archives` row and `retention()`."""

from __future__ import annotations

import json
import os
import stat
import sys
import time
from pathlib import Path

import pytest
from conftest import write_config

from moxi import modules
from moxi_sessions import archives, cli, doctor, find, readguard, retention, setup
from moxi_sessions.testing import ClaudeTranscript, CodexRollout

ONE = "0190b000-0000-7000-8000-0000000000a1"
TWO = "0190b000-0000-7000-8000-0000000000a2"
THREE = "0190b000-0000-7000-8000-0000000000a3"
GONE = "0190b000-0000-7000-8000-0000000000ff"
#: A short answer wait, so a folder that never answers costs a test little.
WAIT_S = 0.6
#: What a call may take past one wait: starting the children and reading the folders.
SLACK_S = 1.5


@pytest.fixture(autouse=True)
def fresh():
    archives.forget()
    readguard.reset()
    yield
    archives.forget()
    readguard.reset()


@pytest.fixture
def spawned(monkeypatch) -> list[str]:
    """The folders each answer test started a child for, in order."""
    seen: list[str] = []
    real = archives._spawn

    def counting(archive_dir):
        seen.append(archive_dir)
        return real(archive_dir)

    monkeypatch.setattr(archives, "_spawn", counting)
    return seen


@pytest.fixture
def never_answers(monkeypatch):
    """Swap the answer test for a child that never answers."""
    monkeypatch.setattr(archives, "_argv", lambda archive_dir: [
        sys.executable, "-I", "-S", "-c", "import time; time.sleep(60)", archive_dir])


@pytest.fixture
def home(tmp_path, monkeypatch, own_config) -> Path:
    """A home of the test's own with each harness's live root, and two archive folders
    per harness configured under it."""
    home = tmp_path / "home"
    for path in (".claude/projects", ".codex/sessions", "arch/claude-1", "arch/claude-2",
                 "arch/codex-1", "arch/codex-2"):
        (home / path).mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    for name in ("CLAUDE_CONFIG_DIR", "CODEX_HOME", "XDG_DATA_HOME"):
        monkeypatch.delenv(name, raising=False)
    configure(own_config, home)
    return home


def configure(own_config: Path, home: Path, *, claude=None, codex=None,
              wait_s: float = WAIT_S) -> None:
    claude = [str(home / "arch/claude-1"), str(home / "arch/claude-2")] if claude is None else claude
    codex = [str(home / "arch/codex-1"), str(home / "arch/codex-2")] if codex is None else codex
    write_config(own_config, (
        f"[claude]\narchives = {claude!r}\n[codex]\narchives = {codex!r}\n"
        f"[archives]\nanswer_wait_s = {wait_s}\n").replace("'", '"'))
    archives.forget()


def live(home: Path, harness: str) -> Path:
    return home / (".claude/projects" if harness == "claude" else ".codex/sessions")


def arch(home: Path, harness: str, n: int) -> Path:
    return home / f"arch/{harness}-{n}"


def session(root: Path, harness: str, session_id: str, *, mtime: float | None = None) -> Path:
    """One session written under `root` as its harness lays it out."""
    if harness == "claude":
        t = ClaudeTranscript(root, session_id=session_id)
        t.user(f"hello from {session_id}")
        t.assistant("hi")
    else:
        t = CodexRollout(root, thread_id=session_id)
        t.event("user_message", message=f"hello from {session_id}")
    path = t.write()
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def ids(listing) -> list[str]:
    return sorted({entry.source.session_id for entry in listing})


def run(capsys, *argv) -> tuple[int, str, str]:
    code = cli.main(list(argv), prog="moxi sessions")
    out, err = capsys.readouterr()
    return code, out, err


# --- order and shadowing -----------------------------------------------------------------

@pytest.mark.parametrize("harness", ["claude", "codex"])
def test_corpus_lists_the_live_root_then_each_archive_and_shadows_whatever_the_time(
        home, harness):
    old, new = time.time() - 86400 * 30, time.time()
    session(live(home, harness), harness, ONE)
    session(arch(home, harness, 1), harness, ONE, mtime=old)
    session(arch(home, harness, 1), harness, TWO, mtime=old)
    session(arch(home, harness, 2), harness, ONE, mtime=new)
    session(arch(home, harness, 2), harness, TWO, mtime=new)
    session(arch(home, harness, 2), harness, THREE, mtime=new)
    # The first archive's last walk is after its files' times: it lists nothing new, and
    # still shadows the newer copies in the second archive.
    since = {str(arch(home, harness, 1)): new - 60}
    listings = find.corpus(harness, since=since)
    assert [listing.root for listing in listings] == [
        str(live(home, harness)), str(arch(home, harness, 1)), str(arch(home, harness, 2))]
    assert [listing.is_archive for listing in listings] == [False, True, True]
    assert all(listing.ok for listing in listings)
    assert ids(listings[0]) == [ONE]
    assert ids(listings[1]) == [] and listings[1].shadowed == 1
    assert ids(listings[2]) == [THREE] and listings[2].shadowed == 2


@pytest.mark.parametrize("harness", ["claude", "codex"])
def test_find_with_archives_asks_the_live_root_then_each_archive_in_order(home, harness):
    session(arch(home, harness, 1), harness, TWO)
    session(arch(home, harness, 2), harness, TWO)
    found = find.find(harness, TWO, include_archives=True)
    assert found.found and not found.unknown
    assert [p.root for p in found.probes] == [str(live(home, harness)),
                                             str(arch(home, harness, 1))]
    assert [p.outcome for p in found.probes] == ["miss", "hit"]
    assert found.hits[0].source.path.startswith(str(arch(home, harness, 1)) + os.sep)


def test_find_without_archives_and_sources_ask_the_live_root_only_and_test_nothing(
        home, spawned):
    session(arch(home, "claude", 1), "claude", TWO)
    found = find.find("claude", TWO)
    assert not found.found and [p.root for p in found.probes] == [
        str(live(home, "claude"))]
    assert find.sources("claude").root == str(live(home, "claude"))
    assert spawned == []
    session(live(home, "claude"), "claude", ONE)
    assert find.find("claude", ONE, include_archives=True).found
    assert spawned == []


def test_an_archive_written_with_a_trailing_slash_is_one_archive_dir(home, own_config):
    one = arch(home, "claude", 1)
    configure(own_config, home, claude=[f"{one}/", f"{one}//.", str(one)], codex=[])
    assert archives.archive_dirs("claude") == (str(one),)
    old = time.time() - 86400 * 30
    session(one, "claude", TWO, mtime=old)
    listings = find.corpus("claude", since={str(one): old + 60})
    assert [listing.root for listing in listings] == [str(live(home, "claude")), str(one)]
    assert listings[1].ok and ids(listings[1]) == []        # its last walk was taken
    assert find.find("claude", TWO, include_archives=True).found


def test_the_testing_hold_on_archive_tests_is_put_back(home):
    from moxi_sessions import api
    from moxi_sessions.testing import archive_tests, forget_archive_answers

    one, two = str(arch(home, "claude", 1)), str(arch(home, "claude", 2))
    started: list[str] = []
    real = (archives._argv, archives._spawn, archives.KEEP_S, archives.archive_answers)
    api_had = vars(api).get("archive_answers")

    def asleep(archive_dir, *, fresh):
        return None if fresh or archive_dir != one else archives.ArchiveAnswer(
            archive_dir, ok=False, reason="asleep")

    with archive_tests(keep_s=0, on_test=started.append, stand_in=asleep):
        said = api.archive_answers([one, two])
        assert not said[one].ok and said[one].reason == "asleep" and said[two].ok
        assert started == [two]
        listings = find.corpus("claude")
        assert not listings[1].ok and "asleep" in listings[1].reason
        assert api.archive_answers([one], fresh=True)[one].ok
        assert started == [two, two, one]                 # nothing kept
    with archive_tests(never_answer=True):
        assert "no answer" in archives.archive_answers([one], wait_s=0.3)[one].reason
    assert (archives._argv, archives._spawn, archives.KEEP_S,
            archives.archive_answers) == real
    assert vars(api).get("archive_answers") is api_had
    archives.archive_answers([one])
    forget_archive_answers()
    assert archives._kept == {}


def test_a_kept_answer_tests_each_dir_once(home, spawned):
    find.corpus("claude")
    find.find("claude", THREE, include_archives=True)
    assert sorted(spawned) == sorted([str(arch(home, "claude", 1)),
                                      str(arch(home, "claude", 2))])


# --- offline ------------------------------------------------------------------------------

def test_a_dir_that_never_answers_is_offline_within_the_wait_never_a_miss(
        home, never_answers):
    session(live(home, "claude"), "claude", ONE)
    began = time.monotonic()
    found = find.find("claude", TWO, include_archives=True)
    took = time.monotonic() - began
    assert took < WAIT_S + SLACK_S
    assert not found.found and found.unknown
    outcomes = [p.outcome for p in found.probes]
    assert outcomes[0] == "miss"
    assert outcomes[1:] == [f"unreadable: no answer within {WAIT_S:g} s"] * 2
    listings = find.corpus("claude")
    assert listings[0].ok and ids(listings[0]) == [ONE]
    assert [listing.ok for listing in listings[1:]] == [False, False]
    assert all("no answer within" in listing.reason for listing in listings[1:])


def test_three_dirs_that_never_answer_cost_one_wait(home, own_config, never_answers):
    asked_dirs = [str(home / f"arch/claude-{n}") for n in (1, 2, 3)]
    configure(own_config, home, claude=asked_dirs, wait_s=1.0)
    began = time.monotonic()
    listings = find.corpus("claude")
    took = time.monotonic() - began
    assert [listing.ok for listing in listings] == [True, False, False, False]
    assert 1.0 <= took < 1.0 + SLACK_S


def test_read_without_harness_waits_once_for_every_harness_offline_and_says_unknown(
        home, capsys, own_config, never_answers):
    configure(own_config, home, wait_s=1.0)
    began = time.monotonic()
    code, out, err = run(capsys, "read", TWO)
    took = time.monotonic() - began
    assert code == 1 and out == ""
    assert f"session {TWO} is unknown" in err and "not found" not in err
    for harness in ("claude", "codex"):
        assert str(arch(home, harness, 1)) in err and str(arch(home, harness, 2)) in err
    assert took < 1.0 + SLACK_S


def test_an_absent_archive_dir_is_offline(home, own_config):
    missing = str(home / "arch/not-there")
    configure(own_config, home, claude=[missing])
    found = find.find("claude", TWO, include_archives=True)
    assert found.unknown
    assert found.probes[-1].outcome == "unreadable: does not exist"
    assert archives.archive_answers([missing])[missing].ok is False


def test_a_relative_archive_dir_is_offline_without_a_test(home, own_config, spawned):
    configure(own_config, home, claude=["arch/claude-1"])
    found = find.find("claude", TWO, include_archives=True)
    assert found.probes[-1].outcome == "unreadable: is not an absolute path"
    assert spawned == []


def test_an_absent_live_root_lists_as_absent_and_its_archives_are_listed(home):
    live(home, "claude").rmdir()
    session(arch(home, "claude", 1), "claude", TWO)
    listings = find.corpus("claude")
    assert not listings[0].ok and listings[0].absent and len(listings[0]) == 0
    assert listings[1].ok and ids(listings[1]) == [TWO]


@pytest.mark.skipif(os.name != "posix" or os.geteuid() == 0,
                    reason="Windows has no POSIX modes to take a read away with, "
                           "and root lists a folder whatever its mode")
def test_a_live_root_that_cant_be_listed_lists_no_archive(home, spawned):
    session(arch(home, "claude", 1), "claude", TWO)
    root = live(home, "claude")
    root.chmod(0)
    try:
        listings = find.corpus("claude")
    finally:
        root.chmod(0o755)
    assert not listings[0].ok and not listings[0].absent
    assert [listing.ok for listing in listings[1:]] == [False, False]
    assert all(listing.reason.startswith("not asked") for listing in listings[1:])
    assert spawned == []


# --- what a listing records ---------------------------------------------------------------

@pytest.mark.skipif(os.name != "posix" or os.geteuid() == 0,
                    reason="Windows has no POSIX modes to take a read away with, "
                           "and root lists a folder whatever its mode")
def test_corpus_records_a_subdir_below_the_top_it_cant_list(home):
    session(arch(home, "claude", 1), "claude", TWO)
    project = next(arch(home, "claude", 1).iterdir())
    project.chmod(0)
    try:
        listing = find.corpus("claude")[1]
    finally:
        project.chmod(0o755)
    assert listing.ok
    assert [path for path, _ in listing.unreadable] == [project.name]


def test_corpus_records_an_entry_listed_and_then_gone(home, monkeypatch):
    path = session(arch(home, "claude", 1), "claude", TWO)

    def vanished(source, handle, end):
        raise FileNotFoundError(2, "No such file or directory")

    monkeypatch.setattr(find, "claude_header", vanished)
    listing = find.corpus("claude")[1]
    assert listing.ok
    assert listing.unreadable == ((path.relative_to(arch(home, "claude", 1)).as_posix(),
                                   find.GONE),)
    # `sources` keeps its own rule: a file gone between listing and reading is skipped.
    assert find.sources("claude", root=arch(home, "claude", 1)).unreadable == ()


def test_corpus_marks_an_empty_top_level_and_the_answer_test_says_it(home):
    listings = find.corpus("claude")
    assert listings[1].ok and listings[1].empty
    session(arch(home, "claude", 2), "claude", TWO)
    archive_dir = str(arch(home, "claude", 2))
    assert listings[2].empty and not find.corpus("claude")[2].empty
    assert archives.archive_answers([archive_dir])[archive_dir].empty is True  # the answer kept
    assert archives.archive_answers([archive_dir], fresh=True)[archive_dir].empty is False


def test_a_fresh_test_sees_a_mount_point_left_empty(home):
    archive_dir = arch(home, "claude", 1)
    path = session(archive_dir, "claude", TWO)
    said = archives.archive_answers([str(archive_dir)])[str(archive_dir)]
    assert said.ok and not said.empty
    path.unlink()
    path.parent.rmdir()
    kept = archives.archive_answers([str(archive_dir)])[str(archive_dir)]
    again = archives.archive_answers([str(archive_dir)], fresh=True)[str(archive_dir)]
    assert not kept.empty
    assert again.ok and again.empty


# --- the verbs ----------------------------------------------------------------------------

def test_read_without_harness_asks_archives_only_when_no_live_root_holds_it(
        home, capsys, spawned):
    session(live(home, "claude"), "claude", ONE)
    code, out, _ = run(capsys, "read", ONE)
    assert code == 0 and spawned == []
    path = session(arch(home, "codex", 2), "codex", TWO)
    code, out, err = run(capsys, "read", TWO)
    assert code == 0, err
    assert f"read from: {path}" in out
    assert len(spawned) == 4


def test_read_with_harness_reads_from_an_archive(home, capsys):
    path = session(arch(home, "claude", 2), "claude", TWO)
    code, out, err = run(capsys, "read", TWO, "--harness", "claude")
    assert code == 0, err
    assert f"read from: {path}" in out


def test_read_of_a_session_no_dir_holds_says_not_found(home, capsys):
    code, _, err = run(capsys, "read", GONE)
    assert code == 1 and "was found in any harness" in err and "unknown" not in err


def test_read_names_another_harness_archive_it_couldnt_ask(home, capsys, own_config):
    missing = str(home / "arch/not-there")
    configure(own_config, home, codex=[missing])
    path = session(arch(home, "claude", 1), "claude", TWO)
    code, out, err = run(capsys, "read", TWO)
    assert code == 0 and f"read from: {path}" in out
    assert missing in err and "didn't answer" in err


def test_find_verb_reads_archives_and_says_unknown(home, capsys, own_config):
    path = session(arch(home, "codex", 1), "codex", TWO)
    code, out, _ = run(capsys, "find", TWO, "--harness", "codex")
    assert code == 0 and out.strip() == str(path)
    missing = str(home / "arch/not-there")
    configure(own_config, home, claude=[missing])
    code, out, _ = run(capsys, "find", TWO)
    assert code == 1 and f"{TWO} is unknown" in out and missing in out
    code, out, _ = run(capsys, "find", TWO, "--json")
    assert code == 1 and '"unknown": true' in out


def test_root_verb_names_each_archive_and_tests_none(home, capsys, spawned):
    code, out, _ = run(capsys, "root")
    assert code == 0 and spawned == []
    for harness in ("claude", "codex"):
        for n in (1, 2):
            assert f"  archive: {arch(home, harness, n)}" in out
    code, out, _ = run(capsys, "root", "--json")
    assert json.dumps(str(arch(home, "codex", 2))) in out


# --- nothing changes in an archive folder -------------------------------------------------

def _snapshot(root: Path) -> dict[str, tuple]:
    out = {}
    for path in [root, *root.rglob("*")]:
        st = os.lstat(path)
        out[str(path)] = (st.st_mode, st.st_mtime_ns, st.st_size, st.st_ino)
    return out


def test_nothing_in_a_read_only_archive_dir_changes(home, capsys):
    archive_dir = arch(home, "claude", 1)
    session(archive_dir, "claude", TWO)
    for path in [*archive_dir.rglob("*"), archive_dir]:
        path.chmod(path.stat().st_mode & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))
    try:
        before = _snapshot(archive_dir)
        find.corpus("claude")
        found = find.find("claude", TWO, include_archives=True)
        find.subagents(found.hits[0].source)
        assert run(capsys, "read", TWO, "--all")[0] == 0
        assert run(capsys, "find", TWO)[0] == 0
        setup.setup()
        list(doctor.rows())
        assert _snapshot(archive_dir) == before
    finally:
        for path in [archive_dir, *archive_dir.rglob("*")]:
            path.chmod(path.stat().st_mode | stat.S_IWUSR)


# --- the guard ----------------------------------------------------------------------------

def test_the_guard_refuses_a_path_under_an_archive_without_resolving_it(home, monkeypatch):
    turns = modules.load("turns").api
    archive_dir = str(arch(home, "claude", 2))
    resolved: list[str] = []
    real = os.path.realpath

    def watching(path, *args, **kw):
        resolved.append(str(path))
        return real(path, *args, **kw)

    monkeypatch.setattr(readguard.os.path, "realpath", watching)
    named = f"{archive_dir}/-work-project/{TWO}.jsonl"
    answer = readguard.judge(turns.ToolCall(
        harness="claude", session_id=ONE, agent_id=None, tool="Read",
        tool_use_id="toolu_1", input={"file_path": named}, cwd="/", env={"HOME": str(home)}))
    assert isinstance(answer, turns.Deny)
    assert archive_dir in answer.reason
    assert not any(path.startswith(archive_dir) for path in resolved)
    shell = readguard.judge(turns.ToolCall(
        harness="codex", session_id=ONE, agent_id=None, tool="Bash", tool_use_id="toolu_2",
        input={"command": f"cat {arch(home, 'codex', 1).as_posix()}/2026/x.jsonl"}, cwd="/",
        env={"HOME": str(home)}))
    assert isinstance(shell, turns.Deny)


# --- setup, the doctor and retention ------------------------------------------------------

def test_setup_names_each_archive_and_whether_it_answers(home, own_config, capsys):
    missing = str(home / "arch/not-there")
    configure(own_config, home, codex=[missing])
    assert setup.setup() == 0
    out = capsys.readouterr().out
    for n in (1, 2):
        assert f"claude archive folder {arch(home, 'claude', n)}: answers now" in out
    assert f"codex archive folder {missing}: doesn't answer now (does not exist)" in out


def test_the_doctor_archives_row(home, own_config):
    assert doctor.archives_row().state == "ok"
    missing = str(home / "arch/not-there")
    configure(own_config, home, codex=[missing])
    row = doctor.archives_row()
    assert row.state == "warn" and missing in row.detail and row.fix == doctor.ARCHIVES_FIX
    configure(own_config, home, claude=[], codex=[])
    assert doctor.archives_row().state == "off"
    assert [row.name for row in doctor.rows()][3] == "archives"


def test_retention_names_each_archive_and_touches_none(home, spawned, monkeypatch):
    opened: list[str] = []
    real = os.scandir

    def watching(path=".", *args):
        opened.append(str(path))
        return real(path, *args)

    monkeypatch.setattr(os, "scandir", watching)
    said = {item.harness: item for item in retention.retention()}
    assert said["claude"].archive_dirs == (str(arch(home, "claude", 1)),
                                           str(arch(home, "claude", 2)))
    assert said["codex"].archive_dirs == (str(arch(home, "codex", 1)),
                                          str(arch(home, "codex", 2)))
    assert said["opencode"].archive_dirs == ()
    assert any("never writes, moves or deletes" in line for line in said["claude"].lines)
    assert spawned == []
    assert not any(path.startswith(str(home / "arch")) for path in opened)

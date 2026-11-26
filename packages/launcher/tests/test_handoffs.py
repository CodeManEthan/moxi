"""Handoffs: the key, the folder (notes' or the launcher's own), the file name, what is
still waiting, the current session's folder, and taking a handoff."""

from __future__ import annotations

import os
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest
from moxi import modules
from moxi.paths import Layout

from moxi_launcher import handoffs, store
from moxi_launcher.cli import command_line

CLAUDE_ID = "5f0c3a1e-7f43-4c8e-9e0a-1d2b3c4d5e6f"


@pytest.fixture(autouse=True)
def without_notes(monkeypatch):
    """notes not installed, whatever this environment holds: the folder is the launcher's own
    unless a test fakes notes with the `notes` fixture."""
    real = modules.available
    monkeypatch.setattr(modules, "available", lambda name: name != "notes" and real(name))


def row(**fields):
    base = {"identity": None, "scope": None, "preset_kind": None, "preset_name": None,
            "project_key": None, "cwd": "/work", "values": {}}
    base.update(fields)
    return SimpleNamespace(**base)


# -- the key -------------------------------------------------------------------------------


@pytest.mark.parametrize("fields, key", [
    ({"identity": "planner", "preset_kind": "seat", "preset_name": "p", "project_key": "k"},
     "planner"),
    ({"identity": "planner@moxi"}, "planner"),
    ({"preset_kind": "seat", "preset_name": "lead", "project_key": "moxi"}, "lead"),
    ({"preset_kind": "project", "preset_name": "moxi-preset", "project_key": "moxi"},
     "moxi"),
    ({"project_key": "moxi"}, "moxi"),
    ({}, None),
    ({"preset_kind": "seat", "preset_name": "../up"}, None),
])
def test_the_key_of_a_launch(fields, key):
    assert handoffs.key_of_row(row(**fields)) == key


def test_the_key_of_a_session_by_hand_is_its_comms_name_without_the_scope():
    assert handoffs.key_of_env({"MOXI_COMMS_NAME": "planner"}) == "planner"
    assert handoffs.key_of_env({"MOXI_COMMS_NAME": "planner@moxi"}) == "planner"
    assert handoffs.key_of_env({"PWD": "/work"}) is None


# -- file names ----------------------------------------------------------------------------


def test_file_names_take_a_count_from_the_second():
    assert handoffs.file_name("lead", "2026-10-30") == "2026-10-30-lead.md"
    assert handoffs.file_name("lead", "2026-10-30", 2) == "2026-10-30-lead-2.md"


@pytest.mark.parametrize("name, order", [
    ("2026-10-30-lead.md", ("2026-10-30", 1)),
    ("2026-10-30-lead-2.md", ("2026-10-30", 2)),
    ("2026-10-30-lead-12.md", ("2026-10-30", 12)),
    ("2026-10-30-leader.md", None),
    ("2026-10-30-lead-1.md", None),
    ("2026-10-30-lead.txt", None),
    ("lead.md", None),
])
def test_a_file_belongs_to_a_key_by_its_name(name, order):
    found = handoffs.parse(f"/h/{name}", "lead")
    assert (found.order if found else None) == order


def test_handoffs_sort_by_date_then_count(tmp_path):
    for name in ("2026-10-29-lead-2.md", "2026-10-30-lead.md", "2026-10-29-lead.md",
                 "2026-10-30-other.md", "notes.md"):
        (tmp_path / name).write_text("x", encoding="utf-8")
    (tmp_path / "2026-10-31-lead.md").mkdir()
    assert [h.path.name for h in handoffs.handoffs(tmp_path, "lead")] == [
        "2026-10-30-lead.md", "2026-10-29-lead-2.md", "2026-10-29-lead.md"]
    assert handoffs.handoffs(tmp_path / "missing", "lead") == []


def test_waiting_is_what_is_newer_than_the_newest_pick(tmp_path):
    for name in ("2026-10-28-lead.md", "2026-10-29-lead.md", "2026-10-29-lead-2.md",
                 "2026-10-30-lead.md"):
        (tmp_path / name).write_text("x", encoding="utf-8")
    left = handoffs.unpicked(tmp_path, "lead", [str(tmp_path / "2026-10-28-lead.md"),
                                                 "/elsewhere/2026-10-29-lead.md"])
    assert [h.path.name for h in left] == ["2026-10-30-lead.md", "2026-10-29-lead-2.md"]
    assert handoffs.unpicked(tmp_path, "lead",
                             [str(tmp_path / "2026-10-30-lead.md")]) == []
    assert len(handoffs.unpicked(tmp_path, "lead", ["/a/brief.md"])) == 4


def test_a_handoff_with_a_shown_or_taken_pick_waits_no_longer(tmp_path):
    conn = store.connect()
    try:
        (tmp_path / "2026-10-30-lead.md").write_text("x", encoding="utf-8")
        found = handoffs.waiting(conn, tmp_path, "lead")
        assert found.date == "2026-10-30"
        store.add_pick(conn, str(found.path), "lead", "shown")
        assert handoffs.waiting(conn, tmp_path, "lead") is None
        assert handoffs.waiting(None, tmp_path, "lead") is not None
        (tmp_path / "2026-10-31-lead.md").write_text("x", encoding="utf-8")
        assert handoffs.waiting(conn, tmp_path, "lead").date == "2026-10-31"
        store.add_pick(conn, str(tmp_path / "2026-10-31-lead.md"), "lead", "taken")
        assert handoffs.waiting(conn, tmp_path, "lead") is None
    finally:
        conn.close()


def test_the_pointer_names_the_path_and_date_and_how_to_take_it(tmp_path):
    path = tmp_path / "2026-10-30-lead-2.md"
    found = handoffs.parse(path, "lead")
    assert handoffs.pointer(found) == (
        f"Handoff waiting: {path} (2026-10-30). Read it before other work, then run: "
        f"{command_line(['moxi', 'launcher', 'handoff', 'take', str(path)])}")


def test_the_take_command_quotes_a_path_with_a_space(tmp_path):
    path = tmp_path / "my notes" / "2026-10-30-lead.md"
    line = handoffs.take_command(path)
    assert line == command_line(["moxi", "launcher", "handoff", "take", str(path)])
    assert line != f"moxi launcher handoff take {path}"


# -- other seats' handoffs -----------------------------------------------------------------


def files(handoff_dir, *names):
    for name in names:
        (handoff_dir / name).write_text("x", encoding="utf-8")


def test_a_trailing_count_is_a_count_only_beside_the_name_without_it(tmp_path):
    files(tmp_path, "2026-10-29-lead.md", "2026-10-29-lead-2.md", "2026-10-29-worker-7.md",
          "2026-10-30-lead-3.md", "2026-10-29-pair-1.md", "notes.md", "2026-10-29-.md")
    read = sorted((key, h.path.name, h.count) for key, h in handoffs.keyed(tmp_path))
    assert read == [("lead", "2026-10-29-lead-2.md", 2), ("lead", "2026-10-29-lead.md", 1),
                    ("lead-3", "2026-10-30-lead-3.md", 1),
                    ("pair-1", "2026-10-29-pair-1.md", 1),
                    ("worker-7", "2026-10-29-worker-7.md", 1)]
    assert handoffs.keyed(tmp_path / "missing") == []


def test_others_are_each_other_keys_newest_waiting_handoff_newest_first(tmp_path):
    files(tmp_path, "2026-10-27-lead.md", "2026-10-28-lead.md", "2026-10-26-planner.md",
          "2026-10-30-worker-7.md", "2026-10-29-builder.md", "2026-10-29-builder-2.md")
    found = handoffs.others(None, tmp_path, "lead")
    assert [(key, h.path.name) for key, h in found] == [
        ("worker-7", "2026-10-30-worker-7.md"), ("builder", "2026-10-29-builder-2.md"),
        ("planner", "2026-10-26-planner.md")]
    assert [key for key, _ in handoffs.others(None, tmp_path, None)] == [
        "worker-7", "builder", "lead", "planner"]


def test_on_one_date_the_file_written_last_comes_first(tmp_path):
    files(tmp_path, "2026-10-29-alpha.md", "2026-10-29-beta.md")
    os.utime(tmp_path / "2026-10-29-alpha.md", ns=(2_000_000_000, 2_000_000_000))
    os.utime(tmp_path / "2026-10-29-beta.md", ns=(1_000_000_000, 1_000_000_000))
    assert [key for key, _ in handoffs.others(None, tmp_path, None)] == ["alpha", "beta"]


def test_a_file_the_seat_reads_as_its_own_is_not_another_seats(tmp_path):
    files(tmp_path, "2026-10-29-lead-2.md")
    assert handoffs.others(None, tmp_path, "lead") == []
    assert [key for key, _ in handoffs.others(None, tmp_path, "planner")] == ["lead-2"]


def test_a_seat_named_with_a_trailing_count_shares_its_files_with_the_shorter_key(tmp_path):
    """Seat `lead-2`'s first handoff has the name of seat `lead`'s second: each seat reads
    it as its own, and `lead` never lists it as another seat's."""
    files(tmp_path, "2026-10-29-lead-2.md")
    own = tmp_path / "2026-10-29-lead-2.md"
    assert handoffs.waiting(None, tmp_path, "lead").path == own
    assert handoffs.waiting(None, tmp_path, "lead-2").path == own
    assert handoffs.others(None, tmp_path, "lead") == []
    assert handoffs.others(None, tmp_path, "lead-2") == []
    assert [key for key, _ in handoffs.others(None, tmp_path, "planner")] == ["lead-2"]
    # With the name without the count on the same date, a read without a key gives it to
    # `lead`, and `lead-2` still reads it as its own.
    files(tmp_path, "2026-10-29-lead.md")
    assert [key for key, _ in handoffs.others(None, tmp_path, "planner")] == ["lead"]
    assert handoffs.waiting(None, tmp_path, "lead").path == own
    assert handoffs.waiting(None, tmp_path, "lead-2").path == own
    assert [(key, h.path.name) for key, h in handoffs.others(None, tmp_path, "lead-2")] \
        == [("lead", "2026-10-29-lead.md")]


def test_another_key_picked_up_to_its_newest_is_not_listed(tmp_path):
    conn = store.connect()
    try:
        files(tmp_path, "2026-10-29-planner.md", "2026-10-30-builder.md")
        store.add_pick(conn, str(tmp_path / "2026-10-29-planner.md"), "planner", "shown")
        assert [key for key, _ in handoffs.others(conn, tmp_path, "lead")] == ["builder"]
    finally:
        conn.close()


def test_other_lines_show_five_then_count_the_rest(tmp_path):
    found = [(f"seat{n}", handoffs.Handoff(tmp_path / f"2026-10-2{n}-seat{n}.md",
                                           f"2026-10-2{n}", 1)) for n in range(7, 0, -1)]
    lines = handoffs.other_lines(found)
    assert lines[0] == (f"Another seat's handoff: {tmp_path / '2026-10-27-seat7.md'} "
                        "(seat7, 2026-10-27)")
    assert len(lines) == 6 and lines[5] == "… and 2 more"
    assert len(handoffs.other_lines(found[:5])) == 5
    assert handoffs.other_lines([]) == []


# -- the folder ----------------------------------------------------------------------------


def test_without_notes_the_dir_is_the_launchers_own_and_private():
    handoff_dir = handoffs.handoff_dir("/work", notes_dir="/notes")
    assert handoff_dir == Layout("launcher").data_dir() / "handoffs"
    assert handoff_dir.is_dir()
    if os.name == "posix":
        assert stat.S_IMODE(handoff_dir.stat().st_mode) == 0o700


def test_the_dir_is_not_made_when_create_is_off():
    handoff_dir = handoffs.handoff_dir("/work", create=False)
    assert not handoff_dir.exists()


@pytest.fixture
def notes(monkeypatch, tmp_path):
    calls = []

    def handoffs_dir(path, *, create=False, override=None):
        calls.append((path, create, override))
        return answer["folder"]

    answer = {"folder": tmp_path / "notes" / "handoffs"}
    api = SimpleNamespace(handoffs_dir=handoffs_dir)
    found = SimpleNamespace(state=modules.PRESENT, api=api, reason="")
    monkeypatch.setattr(modules, "available", lambda name: name == "notes")
    monkeypatch.setattr(modules, "load", lambda name, interface=None: found)
    return SimpleNamespace(calls=calls, answer=answer, found=found)


def test_with_notes_the_dir_is_notes_with_the_launchs_override(notes):
    launch = row(cwd="/work/sub", values={"notes_dir": "/srv/notes-moxi"})
    assert handoffs.handoff_dir_of_row(launch) == notes.answer["folder"]
    assert notes.calls == [("/work/sub", True, "/srv/notes-moxi")]


def test_with_notes_a_launch_without_notes_dir_passes_no_override(notes):
    handoffs.handoff_dir_of_row(row(values={"notes_dir": ""}))
    assert notes.calls == [("/work", True, None)]


def test_notes_with_no_dir_for_the_launch_gives_no_handoff_dir(notes):
    # A launch with no notes directory has no handoffs/ to read from, and the launcher's
    # own folder is not used in its place while notes is installed.
    notes.answer["folder"] = None
    with pytest.raises(handoffs.NoNotesDir) as caught:
        handoffs.handoff_dir("/work")
    assert caught.value.problem == ("this launch has no notes directory (no notes_dir and "
                                    "none for /work), so it has no handoffs/ to read from")
    assert caught.value.code == "no-notes-dir"
    assert not handoffs.own_handoffs_dir(create=False).exists()


def test_a_broken_notes_gives_no_handoff_dir(notes):
    notes.found.state = modules.BROKEN
    with pytest.raises(handoffs.NoNotesDir):
        handoffs.handoff_dir("/work")
    assert notes.calls == []
    assert not handoffs.own_handoffs_dir(create=False).exists()


def test_a_notes_that_raises_gives_no_handoff_dir(notes, monkeypatch):
    def broken(path, *, create=False, override=None):
        raise OSError("disk gone")

    monkeypatch.setattr(notes.found.api, "handoffs_dir", broken)
    with pytest.raises(handoffs.NoNotesDir) as caught:
        handoffs.handoff_dir("/work")
    assert "OSError: disk gone" in caught.value.problem


def test_a_launchs_dir_carries_the_refusal(notes):
    notes.answer["folder"] = None
    with pytest.raises(handoffs.NoNotesDir):
        handoffs.handoff_dir_of_row(row(identity="planner"))


# -- the current session -------------------------------------------------------------------


def test_the_current_dir_of_a_launched_seat():
    conn = store.connect()
    try:
        store.insert(conn, request_id="r1", by="a", harness="claude", target="slot",
                     cwd="/work", values={}, argv=["claude"], env={}, posture="go",
                     session_id=CLAUDE_ID, preset_kind="seat", preset_name="lead")
    finally:
        conn.close()
    found = handoffs.current({"MOXI_LAUNCHER_REQUEST": "r1"})
    assert (found.key, found.handoff_dir, found.request_id) == ("lead", handoffs.own_handoffs_dir(),
                                                            "r1")


def test_the_current_dir_of_a_session_by_hand(tmp_path):
    found = handoffs.current({"MOXI_COMMS_NAME": "planner@moxi"}, cwd=tmp_path)
    assert (found.key, found.request_id) == ("planner", None)


def test_a_session_with_no_key_has_no_dir():
    with pytest.raises(handoffs.NoKey):
        handoffs.current({})
    with pytest.raises(handoffs.NoKey):
        handoffs.current({"MOXI_LAUNCHER_REQUEST": "unknown"})
    assert not store.path().exists()


# -- a key's own folder ----------------------------------------------------------------------


def launched(conn, rid, at, cwd="/work", values=None, **fields):
    store.insert(conn, request_id=rid, by="ann", harness="codex", target="headless",
                 cwd=cwd, values=values or {}, argv=["codex"], env={}, posture="", at=at,
                 **fields)


def test_a_keys_newest_launch_is_the_newest_row_whose_key_it_is():
    conn = store.connect()
    try:
        launched(conn, "r-1", "2026-11-01T00:00:00Z", identity="lead@home")
        launched(conn, "r-2", "2026-11-02T00:00:00Z", preset_kind="seat",
                 preset_name="lead")
        launched(conn, "r-3", "2026-11-03T00:00:00Z", identity="coder", project_key="lead")
        launched(conn, "r-4", "2026-11-04T00:00:00Z", identity="leader@home")
        launched(conn, "r-5", "2026-11-05T00:00:00Z", identity="lead_x")
    finally:
        conn.close()
    assert handoffs.newest_launch("lead").request_id == "r-2"
    assert handoffs.newest_launch("coder").request_id == "r-3"
    assert handoffs.newest_launch("lead_x").request_id == "r-5"
    assert handoffs.newest_launch("lea") is None


def test_with_no_launch_log_a_key_has_no_launch_and_none_is_made():
    assert handoffs.newest_launch("lead") is None
    assert not store.path().exists()


def test_a_keys_dir_is_its_newest_launchs_else_the_callers(notes):
    found = handoffs.handoff_dir_of_key("lead", cwd="/here")
    assert (found.handoff_dir, found.launch) == (notes.answer["folder"], None)
    assert notes.calls == [("/here", False, None)]
    conn = store.connect()
    try:
        launched(conn, "r-1", "2026-11-01T00:00:00Z", cwd="/src/moxi", identity="lead",
                 values={"notes_dir": "/srv/notes"})
    finally:
        conn.close()
    found = handoffs.handoff_dir_of_key("lead", cwd="/here")
    assert found.launch.request_id == "r-1"
    assert notes.calls[-1] == ("/src/moxi", False, "/srv/notes")


# -- taking a handoff --------------------------------------------------------------------------


LAUNCHED = {"MOXI_LAUNCHER_REQUEST": "r1"}


@pytest.fixture
def seat():
    """A launch of seat `lead` (request `r1`), and its handoff folder."""
    conn = store.connect()
    try:
        store.insert(conn, request_id="r1", by="a", harness="claude", target="slot",
                     cwd="/work", values={}, argv=["claude"], env={}, posture="go",
                     session_id=CLAUDE_ID, preset_kind="seat", preset_name="lead")
    finally:
        conn.close()
    return handoffs.own_handoffs_dir()


def write(handoff_dir, name):
    path = handoff_dir / name
    path.write_text("where the work is\n", encoding="utf-8")
    return path


def picks():
    conn = store.connect()
    try:
        return conn.execute("SELECT path, key, request_id, how FROM picks").fetchall()
    finally:
        conn.close()


def test_take_with_no_file_takes_the_seats_waiting_handoff(seat):
    write(seat, "2026-10-29-lead.md")
    newest = write(seat, "2026-10-30-lead.md")
    taken = handoffs.take(env=LAUNCHED)
    assert (taken.key, taken.path, taken.handoff_dir, taken.request_id) == (
        "lead", newest, seat, "r1")
    assert picks() == [(str(newest), "lead", "r1", "taken")]
    with pytest.raises(handoffs.TakeRefused) as refused:
        handoffs.take(env=LAUNCHED)
    assert refused.value.code == "nothing-waiting"
    assert len(picks()) == 1


def test_take_of_a_file_of_the_seat(seat):
    older = write(seat, "2026-10-29-lead.md")
    newer = write(seat, "2026-10-30-lead.md")
    assert handoffs.take(older, env=LAUNCHED).path == older
    conn = store.connect()
    try:
        assert handoffs.waiting(conn, seat, "lead").path == newer
    finally:
        conn.close()


def test_a_session_by_hand_takes_under_its_comms_name(tmp_path):
    waiting = write(handoffs.own_handoffs_dir(), "2026-10-29-planner.md")
    taken = handoffs.take(env={"MOXI_COMMS_NAME": "planner@moxi"}, cwd=tmp_path)
    assert (taken.key, taken.path, taken.request_id) == ("planner", waiting, None)


def test_take_with_no_key_refuses_and_writes_nothing():
    with pytest.raises(handoffs.TakeRefused) as refused:
        handoffs.take(env={})
    assert (refused.value.code, refused.value.problem, refused.value.hint) == (
        "no-seat", "this session has no seat", "pass --key")
    assert not store.path().exists()


def test_take_with_nothing_waiting_and_no_file_refuses(seat):
    with pytest.raises(handoffs.TakeRefused) as refused:
        handoffs.take(env=LAUNCHED)
    assert refused.value.code == "nothing-waiting"
    assert picks() == []


def test_a_file_of_another_key_is_refused_unless_key_names_it(seat):
    other = write(seat, "2026-10-29-planner.md")
    with pytest.raises(handoffs.TakeRefused) as refused:
        handoffs.take(other, env=LAUNCHED)
    assert refused.value.code == "another-key"
    with pytest.raises(handoffs.TakeRefused) as refused:
        handoffs.take(other, key="coder", env=LAUNCHED)
    assert refused.value.code == "another-key"
    assert picks() == []
    taken = handoffs.take(other, key="planner", env=LAUNCHED)
    assert (taken.key, taken.request_id) == ("planner", "r1")
    assert picks() == [(str(other), "planner", "r1", "taken")]


def test_a_file_outside_the_seats_handoff_dir_is_refused_unless_key_names_its_key(
        seat, tmp_path):
    elsewhere = write(tmp_path, "2026-10-29-lead.md")
    with pytest.raises(handoffs.TakeRefused) as refused:
        handoffs.take(elsewhere, env=LAUNCHED)
    assert refused.value.code == "another-folder"
    assert picks() == []
    assert handoffs.take(elsewhere, key="lead", env=LAUNCHED).path == elsewhere


def test_a_file_that_is_not_there_is_refused(seat):
    with pytest.raises(handoffs.TakeRefused) as refused:
        handoffs.take(seat / "2026-10-29-lead.md", env=LAUNCHED)
    assert refused.value.code == "no-handoff"
    assert picks() == []


def test_take_with_a_key_and_no_file_takes_that_keys_waiting_handoff(seat):
    waiting = write(seat, "2026-10-29-planner.md")
    taken = handoffs.take(key="planner", env={})
    assert (taken.key, taken.path, taken.request_id) == ("planner", waiting, None)
    with pytest.raises(handoffs.TakeRefused) as refused:
        handoffs.take(key="no/key", env={})
    assert refused.value.code == "bad-key"


def test_start_dir_is_the_cwd_turns_knows_for_this_session(monkeypatch):
    import sys
    import types

    from moxi_launcher import first

    closed = []

    class Found:
        info = {"cwd": "/started/here"}

        def close(self):
            closed.append(True)

    fake = types.ModuleType("fake_turns_for_start")
    fake.find = lambda *, pid: Found() if pid == os.getpid() else None
    monkeypatch.setitem(sys.modules, "fake_turns_for_start", fake)
    monkeypatch.setenv(first.TURNS_ENV, "fake_turns_for_start")
    assert handoffs.start_dir() == "/started/here" and closed == [True]
    fake.find = lambda *, pid: None
    assert handoffs.start_dir() is None

    def broken(*, pid):
        raise OSError("no runtime dir")
    fake.find = broken
    assert handoffs.start_dir() is None


def test_a_by_hand_take_reads_the_start_folders_handoffs(monkeypatch):
    asked = []
    own = handoffs.own_handoffs_dir()

    def handoff_dir(cwd, **kw):
        asked.append(str(cwd))
        return own
    monkeypatch.setattr(handoffs, "comms_name_env", lambda: "TEST_COMMS_NAME")
    monkeypatch.setattr(handoffs, "handoff_dir", handoff_dir)
    monkeypatch.setattr(handoffs, "start_dir", lambda: "/started/here")
    path = write(own, "2026-10-29-lead.md")
    taken = handoffs.take(path, env={"TEST_COMMS_NAME": "lead"})
    assert taken.path == path and asked == ["/started/here"]
    monkeypatch.setattr(handoffs, "start_dir", lambda: None)
    handoffs.take(path, env={"TEST_COMMS_NAME": "lead"})
    assert asked[-1] == str(Path.cwd())

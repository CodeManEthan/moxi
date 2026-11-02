"""Handoffs: the key, the folder (notes' or the launcher's own), the file name, what is
still waiting, and the current session's folder."""

from __future__ import annotations

import os
import stat
from types import SimpleNamespace

import pytest
from moxi import modules
from moxi.paths import Layout

from moxi_launcher import handoffs, store

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


def test_a_handoff_shown_once_waits_no_longer(tmp_path):
    conn = store.connect()
    try:
        (tmp_path / "2026-10-30-lead.md").write_text("x", encoding="utf-8")
        found = handoffs.waiting(conn, tmp_path, "lead")
        assert found.date == "2026-10-30"
        store.add_pick(conn, str(found.path), "lead", "shown")
        assert handoffs.waiting(conn, tmp_path, "lead") is None
        assert handoffs.waiting(None, tmp_path, "lead") is not None
    finally:
        conn.close()


def test_the_pointer_names_the_path_and_date(tmp_path):
    found = handoffs.parse(tmp_path / "2026-10-30-lead-2.md", "lead")
    assert handoffs.pointer(found) == (
        f"Handoff waiting: {tmp_path / '2026-10-30-lead-2.md'} (2026-10-30). "
        "Read it before other work.")


# -- the folder ----------------------------------------------------------------------------


def test_without_notes_the_folder_is_the_launchers_own_and_private():
    folder = handoffs.folder("/work", notes_dir="/notes")
    assert folder == Layout("launcher").data_dir() / "handoffs"
    assert folder.is_dir()
    if os.name == "posix":
        assert stat.S_IMODE(folder.stat().st_mode) == 0o700


def test_the_folder_is_not_made_when_create_is_off():
    folder = handoffs.folder("/work", create=False)
    assert not folder.exists()


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


def test_with_notes_the_folder_is_notes_with_the_launchs_override(notes):
    launch = row(cwd="/work/sub", values={"notes_dir": "/srv/notes-moxi"})
    assert handoffs.folder_of_row(launch) == notes.answer["folder"]
    assert notes.calls == [("/work/sub", True, "/srv/notes-moxi")]


def test_with_notes_a_launch_without_notes_dir_passes_no_override(notes):
    handoffs.folder_of_row(row(values={"notes_dir": ""}))
    assert notes.calls == [("/work", True, None)]


def test_notes_with_no_folder_falls_back_to_the_launchers_own(notes):
    notes.answer["folder"] = None
    assert handoffs.folder("/work") == handoffs.own_folder()


def test_a_broken_notes_falls_back_to_the_launchers_own(notes):
    notes.found.state = modules.BROKEN
    assert handoffs.folder("/work") == handoffs.own_folder()
    assert notes.calls == []


# -- the current session -------------------------------------------------------------------


def test_the_current_folder_of_a_launched_seat():
    conn = store.connect()
    try:
        store.insert(conn, request_id="r1", actor="a", harness="claude", target="slot",
                     cwd="/work", values={}, argv=["claude"], env={}, posture="go",
                     session_id=CLAUDE_ID, preset_kind="seat", preset_name="lead")
    finally:
        conn.close()
    found = handoffs.current({"MOXI_LAUNCHER_REQUEST": "r1"})
    assert (found.key, found.folder, found.request_id) == ("lead", handoffs.own_folder(),
                                                            "r1")


def test_the_current_folder_of_a_session_by_hand(tmp_path):
    found = handoffs.current({"MOXI_COMMS_NAME": "planner@moxi"}, cwd=tmp_path)
    assert (found.key, found.request_id) == ("planner", None)


def test_a_session_with_no_key_has_no_folder():
    with pytest.raises(handoffs.NoKey):
        handoffs.current({})
    with pytest.raises(handoffs.NoKey):
        handoffs.current({"MOXI_LAUNCHER_REQUEST": "unknown"})
    assert not store.path().exists()

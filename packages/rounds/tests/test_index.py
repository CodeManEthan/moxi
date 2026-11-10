"""The index: the rows a write leaves, a rebuild from the logs, reads that write nothing,
and the notice a log changed outside this machine carries."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from moxi_rounds import folders, index, log, paths, write
from moxi_rounds.errors import NoLog, Unreadable

OPENED = {"description": "a placeholder round", "flow": "fix",
          "steps": [{"step": "build", "by": "builder"}]}
SHOWN = {"ids": ["S1"], "record": "comms:12"}
AT = "2026-01-02T03:04:05Z"


@pytest.fixture
def home(tmp_path) -> folders.Home:
    """The project `proj-a` with a rounds folder of the test's own, made and empty."""
    folder = tmp_path / "rounds"
    folder.mkdir()
    return folders.Home("proj-a", folder)


def where(home, slug=None) -> write.Where:
    return write.Where(home.project, home.folder, slug)


def open_round(home, slug="a-round", flow="fix") -> write.Where:
    """Open a round through the write path, as a verb would."""
    home.round_folder(slug).mkdir()
    write.record(where(home, slug), actor="planner", opens=True, at=AT,
                 event=lambda view, result: write.Entry("round.opened",
                                                        {**OPENED, "flow": flow}))
    return where(home, slug)


def finish(at: write.Where, stage="B1") -> None:
    write.record(at, actor="planner", at=AT, event=lambda view, result: write.Entry(
        "finish.done", {"stage": stage, "tip": "0" * 40}))


def show(home) -> None:
    write.record(where(home), actor="planner", at=AT, event=lambda view, result:
                 write.Entry("ruling.shown", SHOWN, log=write.PROJECT))


def rows(*, times: bool = True) -> dict[str, list[tuple]]:
    """Every row of the index, by table; without `times`, all but when each was made."""
    conn = sqlite3.connect(paths.index_file())
    try:
        logs = "project, slug, path, length, sha256, readable, derived, changed_since"
        return {
            "logs": conn.execute(f"SELECT {logs}{', seen_at' if times else ''} FROM logs "
                                 "ORDER BY project, slug").fetchall(),
            "rounds": conn.execute("SELECT * FROM rounds ORDER BY project, slug").fetchall(),
            "items": conn.execute("SELECT * FROM items ORDER BY project, slug, position")
                         .fetchall()}
    finally:
        conn.close()


def seen_at(slug="a-round") -> str:
    conn = sqlite3.connect(paths.index_file())
    try:
        return conn.execute("SELECT seen_at FROM logs WHERE slug = ?", (slug,)).fetchone()[0]
    finally:
        conn.close()


def files(*folders_: Path) -> dict[str, tuple[bytes, int]]:
    """Every file under the folders, with its bytes and the time it was last changed."""
    return {str(path): (path.read_bytes(), path.stat().st_mtime_ns)
            for folder in folders_ for path in sorted(folder.rglob("*")) if path.is_file()}


def stages_done(folded):
    """A stand-in kind of item: each finished stage, with its tip."""
    return [(event["stage"], {"tip": event["tip"], "n": event["n"]})
            for event in folded.history if event["type"] == "finish.done"]


@pytest.fixture
def done_items(monkeypatch):
    monkeypatch.setitem(index.ITEMS, "done", stages_done)


def rewrite_first_line(path: Path) -> None:
    """Change bytes this machine already read, leaving a log that still reads."""
    data = path.read_bytes()
    assert b"planner" in data.split(b"\n")[0]
    path.write_bytes(data.replace(b"planner", b"curator", 1))


# -- what a write leaves -----------------------------------------------------------------

def test_every_write_runs_the_indexs_update():
    assert index.on_append in write.ON_APPEND


def test_no_index_exists_until_something_is_written(home):
    assert not paths.index_file().exists()
    open_round(home)
    assert paths.index_file().is_file()


def test_a_write_leaves_the_rounds_folder_status_and_flow_and_its_logs_digest(home):
    import hashlib

    at = open_round(home, flow="small")
    finish(at)
    data = at.round_log.read_bytes()
    found = rows(times=False)
    assert found["logs"] == [("proj-a", "a-round", str(at.round_log), len(data),
                              hashlib.sha256(data).hexdigest(), 1, index.DERIVED, None)]
    assert found["rounds"] == [("proj-a", "a-round", str(home.folder / "a-round"), "open",
                                "small", None)]
    assert found["items"] == []


def test_a_write_on_one_round_leaves_the_others_rows_alone(home):
    open_round(home, "a-round")
    other = open_round(home, "b-round")
    before = [row for row in rows()["logs"] if row[1] == "a-round"]
    finish(other)
    assert [row for row in rows()["logs"] if row[1] == "a-round"] == before


def test_a_write_to_a_projects_log_is_indexed_under_the_project_alone(home):
    show(home)
    found = rows()
    assert [(row[0], row[1], row[2]) for row in found["logs"]] == [
        ("proj-a", "", str(log.project_log(home.folder)))]
    assert found["rounds"] == []


def test_a_rounds_write_that_leaves_the_projects_log_alone_leaves_its_row_alone(home):
    show(home)
    before = rows()["logs"][0]
    log.append(log.project_log(home.folder), "ruling.shown", SHOWN, actor="pilot")
    open_round(home)
    assert rows()["logs"][0] == before


def test_what_a_log_holds_open_is_kept_by_kind_in_the_logs_order(home, done_items):
    at = open_round(home)
    finish(at, "B2")
    finish(at, "B1")
    assert [row[2:] for row in rows()["items"]] == [
        ("done", "B2", 0, '{"n": 2, "tip": "' + "0" * 40 + '"}'),
        ("done", "B1", 1, '{"n": 3, "tip": "' + "0" * 40 + '"}')]
    assert index.read("proj-a", home.folder, "a-round").items == (
        index.Item("done", "B2", {"tip": "0" * 40, "n": 2}),
        index.Item("done", "B1", {"tip": "0" * 40, "n": 3}))


# -- a rebuild ---------------------------------------------------------------------------

def test_a_rebuilt_index_equals_the_one_the_writes_made(home, done_items):
    at = open_round(home, "a-round")
    finish(at)
    finish(open_round(home, "b-round", flow="full"), "B4")
    show(home)
    written = rows(times=False)
    assert len(written["logs"]) == 3 and len(written["items"]) == 2

    paths.index_file().unlink()
    index.rebuild([home])
    assert rows(times=False) == written
    index.rebuild([home])
    assert rows(times=False) == written


def test_a_rebuild_takes_in_a_round_that_arrived_and_drops_one_that_left(home):
    import shutil

    open_round(home, "a-round")
    open_round(home, "b-round")
    shutil.rmtree(home.folder / "a-round")
    (home.folder / "c-round").mkdir()
    log.append(log.round_log(home.folder / "c-round"), "round.opened", OPENED,
               actor="pilot")
    index.rebuild([home])
    assert [row[1] for row in rows()["rounds"]] == ["b-round", "c-round"]
    assert [row[1] for row in rows()["logs"]] == ["b-round", "c-round"]


def test_a_rebuild_drops_the_rows_of_a_project_it_was_not_given(home, tmp_path):
    open_round(home)
    other = folders.Home("proj-b", tmp_path / "other")
    other.folder.mkdir()
    open_round(other)
    index.rebuild([other])
    assert [row[0] for row in rows()["rounds"]] == ["proj-b"]


# -- reads -------------------------------------------------------------------------------

def test_a_round_is_read_from_its_rows(home):
    at = open_round(home, flow="small")
    assert index.read("proj-a", home.folder, "a-round") == index.Summary(
        "proj-a", "a-round", home.folder / "a-round", status="open", flow="small")
    # The answer is the row's, not the log's: a row made to differ shows through.
    conn = sqlite3.connect(paths.index_file())
    conn.execute("UPDATE rounds SET flow = 'from-the-row'")
    conn.commit()
    conn.close()
    assert index.read("proj-a", home.folder, "a-round").flow == "from-the-row"
    assert at.round_log.is_file()


def test_a_stale_row_still_gives_a_right_read_and_nothing_on_disk_changes(
        home, done_items):
    at = open_round(home)
    # Another machine's verb: a line the index here has not seen.
    log.append(at.round_log, "finish.done", {"stage": "B1", "tip": "1" * 40}, actor="pilot")
    before = files(home.folder, paths.data_dir(), paths.state_dir())

    found = index.read("proj-a", home.folder, "a-round")
    listed = index.rounds("proj-a", home.folder)
    assert found.items == (index.Item("done", "B1", {"tip": "1" * 40, "n": 2}),)
    assert found.status == "open" and found.notice is None
    assert listed == [found]
    assert index.disagreements([home]) != []
    assert files(home.folder, paths.data_dir(), paths.state_dir()) == before


def test_a_read_with_no_index_answers_from_the_log_and_makes_none(home):
    at = open_round(home)
    paths.index_file().unlink()
    assert index.read("proj-a", home.folder, "a-round").status == "open"
    assert index.rounds("proj-a", home.folder)[0].name == "proj-a/a-round"
    assert index.disagreements([home]) == []
    assert not paths.index_file().exists()
    assert at.round_log.is_file()


def test_a_read_does_not_trust_an_index_of_another_schema(home):
    open_round(home, flow="small")
    conn = sqlite3.connect(paths.index_file())
    conn.execute("UPDATE rounds SET flow = 'from-the-row'")
    conn.execute(f"PRAGMA user_version = {len(index.STEPS) + 1}")
    conn.commit()
    conn.close()
    assert index.read("proj-a", home.folder, "a-round").flow == "small"


def test_an_index_of_the_first_schema_is_brought_up_by_the_next_write(home):
    from moxi import store

    paths.index_file().parent.mkdir(parents=True, exist_ok=True)
    conn = store.connect(paths.index_file())
    store.migrate(conn, index.STEPS[:1])
    conn.close()

    at = open_round(home)
    write.record(at, actor="planner", at=AT, event=lambda view, result: write.Entry(
        "round.dropped", {"by": "operator", "record": "comms:30", "questions": {}}))

    conn = sqlite3.connect(paths.index_file())
    assert conn.execute("PRAGMA user_version").fetchone() == (len(index.STEPS),)
    [(status, ended)] = conn.execute("SELECT status, ended FROM rounds").fetchall()
    conn.close()
    assert status == "dropped"
    assert json.loads(ended) == {"how": "dropped", "by": "operator",
                                 "record": "comms:30", "at": AT}
    assert index.disagreements([home]) == []


def test_a_row_made_before_more_was_derived_is_stale(home, monkeypatch):
    open_round(home)
    monkeypatch.setattr(index, "DERIVED", index.DERIVED + 1)
    monkeypatch.setitem(index.ITEMS, "done", lambda folded: [("B9", {})])
    assert index.read("proj-a", home.folder, "a-round").items == (
        index.Item("done", "B9", {}),)
    assert [one.problem for one in index.disagreements([home])] == [
        "has a log that changed since the index last read it"]


def test_refreshing_a_round_brings_its_row_up_to_the_log(home, done_items):
    at = open_round(home)
    log.append(at.round_log, "finish.done", {"stage": "B1", "tip": "1" * 40}, actor="pilot")
    found = index.refresh("proj-a", home.folder, "a-round")
    assert found == index.read("proj-a", home.folder, "a-round")
    assert [row[3] for row in rows()["items"]] == ["B1"]
    assert index.disagreements([home]) == []


def test_the_rounds_of_a_project_are_read_by_slug(home):
    open_round(home, "b-round", flow="small")
    open_round(home, "a-round")
    (home.folder / "no-log-here").mkdir()
    assert [(one.name, one.flow) for one in index.rounds("proj-a", home.folder)] == [
        ("proj-a/a-round", "fix"), ("proj-a/b-round", "small")]


def test_a_round_with_no_log_is_not_an_empty_round(home):
    with pytest.raises(NoLog):
        index.read("proj-a", home.folder, "a-round")
    with pytest.raises(NoLog):
        index.refresh("proj-a", home.folder, "a-round")


def test_a_project_with_no_log_holds_nothing(home):
    empty = index.Summary("proj-a", None, home.folder)
    assert index.read("proj-a", home.folder) == empty
    assert index.refresh("proj-a", home.folder) == empty
    assert empty.name == "proj-a"


def test_a_projects_own_log_is_read_like_a_rounds(home, monkeypatch):
    monkeypatch.setitem(index.ITEMS, "shown", lambda folded: [
        (ruling, {}) for event in folded.history for ruling in event.get("ids", ())])
    show(home)
    found = index.read("proj-a", home.folder)
    assert (found.slug, found.folder, found.status) == (None, home.folder, None)
    assert found.items == (index.Item("shown", "S1", {}),)


# -- a log that can't be read ------------------------------------------------------------

@pytest.mark.parametrize("content", [b"", b'{"v":1,"n":1,"at":"2026-01-02T03:04:05Z",'
                                          b'"actor":"pilot","type":"repair"}\n'])
def test_a_log_that_opened_no_round_is_unreadable_and_no_round_with_no_status(
        home, content):
    home.round_folder("a-round").mkdir(parents=True)
    file = log.round_log(home.round_folder("a-round"))
    file.write_bytes(content)

    for found in (index.read("proj-a", home.folder, "a-round"),
                  index.refresh("proj-a", home.folder, "a-round"),
                  index.read("proj-a", home.folder, "a-round"),
                  index.rounds("proj-a", home.folder)[0]):
        assert isinstance(found.unreadable, Unreadable)
        assert (found.unreadable.file, found.unreadable.line) == (file, 1)
    assert [row[5] for row in rows()["logs"]] == [0]
    # A project's own log opens no round, and is read as it always was.
    log.project_log(home.folder).write_bytes(content)
    assert index.read("proj-a", home.folder).unreadable is None


def test_an_unreadable_log_is_shown_as_unreadable_and_not_as_an_empty_round(home):
    at = open_round(home)
    with at.round_log.open("ab") as file:
        file.write(b'{"v":1,"n":5,"at":"2026-01-02T03:04:05Z","actor":"pilot",'
                   b'"type":"finish.done"}\n')
    for found in (index.read("proj-a", home.folder, "a-round"),
                  index.refresh("proj-a", home.folder, "a-round"),
                  index.read("proj-a", home.folder, "a-round"),
                  index.rounds("proj-a", home.folder)[0]):
        assert isinstance(found.unreadable, Unreadable)
        assert (found.unreadable.file, found.unreadable.line) == (at.round_log, 2)
        assert found.status is None and found.name == "proj-a/a-round"
    assert [row[5] for row in rows()["logs"]] == [0]
    assert index.disagreements([home]) == []


# -- changed outside this machine --------------------------------------------------------

def test_lines_appended_elsewhere_are_read_as_events_and_give_no_notice(home, done_items):
    at = open_round(home)
    log.append(at.round_log, "finish.done", {"stage": "B1", "tip": "1" * 40}, actor="pilot")
    for found in (index.read("proj-a", home.folder, "a-round"),
                  index.refresh("proj-a", home.folder, "a-round"),
                  index.read("proj-a", home.folder, "a-round")):
        assert found.changed_since is None and found.notice is None
        assert [item.id for item in found.items] == ["B1"]


def test_a_log_whose_earlier_bytes_changed_carries_the_notice_and_is_still_read(home):
    at = open_round(home)
    finish(at)
    last_seen = seen_at()
    rewrite_first_line(at.round_log)
    found = index.read("proj-a", home.folder, "a-round")
    assert found.notice == f"log changed outside this machine since {last_seen}"
    assert found.status == "open" and found.unreadable is None


def test_a_log_that_got_shorter_carries_the_notice(home):
    at = open_round(home)
    finish(at)
    last_seen = seen_at()
    first, _rest = at.round_log.read_bytes().split(b"\n", 1)
    at.round_log.write_bytes(first + b"\n")
    found = index.read("proj-a", home.folder, "a-round")
    assert found.notice == f"log changed outside this machine since {last_seen}"
    assert found.status == "open"


def test_a_changed_log_that_also_grew_carries_the_notice(home):
    at = open_round(home)
    last_seen = seen_at()
    rewrite_first_line(at.round_log)
    log.append(at.round_log, "finish.done", {"stage": "B1", "tip": "1" * 40}, actor="pilot")
    assert index.read("proj-a", home.folder, "a-round").changed_since == last_seen


def test_the_notice_stays_through_every_read_and_rebuild_until_the_next_write_here(home):
    at = open_round(home)
    last_seen = seen_at()
    rewrite_first_line(at.round_log)
    assert index.refresh("proj-a", home.folder, "a-round").changed_since == last_seen
    index.rebuild([home])
    # The row now holds the new bytes, and still says since when they are not the old.
    assert index.disagreements([home]) == []
    assert index.read("proj-a", home.folder, "a-round").changed_since == last_seen
    assert index.rounds("proj-a", home.folder)[0].notice == (
        f"log changed outside this machine since {last_seen}")

    finish(at)
    assert index.read("proj-a", home.folder, "a-round").notice is None
    assert rows()["logs"][0][7] is None


def test_a_write_straight_after_the_change_ends_the_notice(home):
    at = open_round(home)
    rewrite_first_line(at.round_log)
    assert index.read("proj-a", home.folder, "a-round").notice is not None
    finish(at)
    assert index.read("proj-a", home.folder, "a-round").notice is None


def test_a_write_that_appends_nothing_keeps_the_notice(home):
    at = open_round(home)
    last_seen = seen_at()
    rewrite_first_line(at.round_log)
    write.record(at, actor="planner", event=lambda view, result: None)
    assert index.read("proj-a", home.folder, "a-round").changed_since == last_seen


def test_a_projects_log_changed_outside_carries_the_notice_too(home):
    show(home)
    last_seen = seen_at("")
    rewrite_first_line(log.project_log(home.folder))
    assert index.read("proj-a", home.folder).changed_since == last_seen


# -- whether the index and the logs agree ------------------------------------------------

def test_an_index_the_writes_made_agrees_with_the_logs(home, done_items):
    finish(open_round(home))
    show(home)
    assert index.disagreements([home]) == []


def test_each_way_the_index_and_the_logs_differ_is_named(home):
    import shutil

    open_round(home, "a-round")
    open_round(home, "b-round")
    open_round(home, "c-round")
    open_round(home, "d-round")
    shutil.rmtree(home.folder / "a-round")
    log.append(log.round_log(home.folder / "b-round"), "finish.done",
               {"stage": "B1", "tip": "1" * 40}, actor="pilot")
    conn = sqlite3.connect(paths.index_file())
    conn.execute("UPDATE rounds SET status = 'closed' WHERE slug = 'c-round'")
    conn.execute("DELETE FROM logs WHERE slug = 'd-round'")
    conn.commit()
    conn.close()
    assert index.disagreements([home]) == [
        index.Disagreement("proj-a/b-round",
                           "has a log that changed since the index last read it"),
        index.Disagreement("proj-a/c-round",
                           "has rows in the index that its log does not give"),
        index.Disagreement("proj-a/d-round", "has a log and no row in the index"),
        index.Disagreement("proj-a/a-round", "has a row in the index and no log")]
    index.rebuild([home])
    assert index.disagreements([home]) == []

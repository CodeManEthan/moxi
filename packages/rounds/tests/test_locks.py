"""The lock files: where they are, what the core's lock does with one, the order two are
taken in, and what a verb does when one is held past its wait."""

from __future__ import annotations

import time

import pytest

from moxi import exit as exits
from moxi import files
from moxi_rounds import log, paths, write
from moxi_rounds.errors import Locked, RoundsError

OPENED = {"description": "a placeholder round", "flow": "fix",
          "steps": [{"step": "build", "by": "builder"}]}
DONE = {"stage": "B1", "tip": "0" * 40}

@pytest.fixture
def where(tmp_path):
    """The round `proj-a/a-round`, opened, in a rounds folder of the test's own."""
    folder = tmp_path / "rounds"
    (folder / "a-round").mkdir(parents=True)
    where = write.Where("proj-a", folder, "a-round")
    log.append(where.round_log, "round.opened", OPENED, actor="planner")
    return where


def done(view, result):
    return write.Entry("finish.done", DONE)


def shown(view, result):
    return write.Entry("ruling.shown", {"ids": ["S1"], "record": "comms:12"},
                       log=write.PROJECT)


def files_under(folder):
    return sorted(str(path.relative_to(folder)) for path in folder.rglob("*")
                  if path.is_file())


# -- the core's lock ---------------------------------------------------------------------

def test_the_cores_lock_locks_the_file_it_is_given_and_writes_the_holders_pid(
        tmp_path, holder):
    folder = tmp_path / "locks"
    path = folder / "a.lock"
    proc = holder(path)

    assert files_under(folder) == ["a.lock"]
    assert path.read_text().split() == [str(proc.pid)]
    with files.lock(folder / "another.lock", timeout_s=0):
        pass  # only the file given is locked, not its folder


def test_the_cores_lock_names_the_holders_pid_when_the_wait_runs_out(tmp_path, holder):
    path = tmp_path / "locks" / "a.lock"
    proc = holder(path)

    started = time.monotonic()
    with pytest.raises(files.LockTimeout) as caught:
        with files.lock(path, timeout_s=0.2):
            pass

    assert time.monotonic() - started >= 0.2
    assert caught.value.holder == proc.pid and caught.value.path == path
    assert f"pid {proc.pid}" in str(caught.value)


def test_the_cores_lock_is_free_again_once_its_holder_is_gone(tmp_path, holder):
    path = tmp_path / "locks" / "a.lock"
    proc = holder(path)
    proc.communicate(timeout=30)

    with files.lock(path, timeout_s=0):
        assert path.read_text().split() != [str(proc.pid)]


# -- where the lock files are ------------------------------------------------------------

def test_a_rounds_lock_and_a_projects_lock_are_in_the_state_directory(tmp_path):
    env = {"MOXI_ROUNDS_STATE_HOME": str(tmp_path / "state")}
    locks = tmp_path / "state" / "rounds" / "locks"

    assert (write.round_lock("proj-a", "a-round", env)
            == locks / "rounds" / "proj-a" / "a-round.lock")
    assert write.project_lock("proj-a", env) == locks / "projects" / "proj-a.lock"


def test_no_projects_key_puts_one_lock_at_or_inside_another(holder):
    # A key may hold dots, so one project's key can end in the very words another
    # project's lock file is named with.
    keys = ["x", "x.project.lock", "x.lock", "projects", "rounds"]
    locks = ([write.project_lock(key) for key in keys]
             + [write.round_lock(key, slug) for key in keys for slug in ("a-round", "x")])

    assert len(set(locks)) == len(locks)
    for one in locks:
        for other in locks:
            assert one is other or not one.is_relative_to(other)

    held = [holder(path) for path in locks]

    assert all(path.is_file() and path.read_text().split() == [str(proc.pid)]
               for path, proc in zip(locks, held, strict=True))


def test_a_project_keyed_like_anothers_lock_file_is_written_beside_it(tmp_path, holder):
    folders = {key: tmp_path / key for key in ("x", "x.project.lock")}
    for key, folder in folders.items():
        holder(write.project_lock(key))
        (folder / "a-round").mkdir(parents=True)

    for key, folder in folders.items():
        written = write.record(write.Where(key, folder, "a-round"), actor="planner",
                               opens=True, wait_s=0,
                               event=lambda view, result: write.Entry("round.opened",
                                                                      OPENED))
        assert written.events[0]["n"] == 1


def test_a_write_leaves_no_lock_file_in_the_rounds_folder(where):
    write.record(where, actor="planner", event=done)
    write.record(where, actor="planner", event=shown, with_project=True)
    write.record(write.Where("proj-a", where.folder), actor="planner", event=shown)

    assert files_under(where.folder) == [
        "a-round/round-note.md", "a-round/round.jsonl", "project.jsonl",
        "questions.md", "rulings.md"]
    assert files_under(paths.locks_dir()) == ["projects/proj-a.lock",
                                               "rounds/proj-a/a-round.lock"]
    assert not where.folder.is_relative_to(paths.locks_dir())


def test_a_lock_file_holds_nothing_once_the_write_is_over(where):
    write.record(where, actor="planner", event=done)

    assert write.round_lock("proj-a", "a-round").read_bytes() == b""


# -- which locks, in which order ---------------------------------------------------------

@pytest.fixture
def taken(monkeypatch):
    """The lock files `write` takes and frees through the core's lock, in order, as
    `("take", name)` and `("free", name)`."""
    seen = []
    real = files.lock

    class Recorded:
        def __init__(self, path, **options):
            self.path, self.lock = path, real(path, **options)

        def __enter__(self):
            self.lock.__enter__()
            seen.append(("take", self.path.name))

        def __exit__(self, *raised):
            seen.append(("free", self.path.name))
            return self.lock.__exit__(*raised)

    monkeypatch.setattr(write.files, "lock", Recorded)
    return seen


def test_a_verb_that_writes_both_logs_takes_the_rounds_lock_first(where, taken):
    write.record(where, actor="planner", event=shown, with_project=True)

    assert taken == [("take", "a-round.lock"), ("take", "proj-a.lock"),
                     ("free", "proj-a.lock"), ("free", "a-round.lock")]


def test_a_verb_on_a_round_alone_takes_only_the_rounds_lock(where, taken):
    write.record(where, actor="planner", event=done)

    assert taken == [("take", "a-round.lock"), ("free", "a-round.lock")]


def test_a_verb_on_a_project_alone_takes_only_the_projects_lock(where, taken):
    write.record(write.Where("proj-a", where.folder), actor="planner", event=shown)

    assert taken == [("take", "proj-a.lock"), ("free", "proj-a.lock")]


def test_a_verb_refused_on_the_projects_lock_lets_the_rounds_lock_go(
        where, holder, taken):
    holder(write.project_lock("proj-a"))

    with pytest.raises(Locked) as caught:
        write.record(where, actor="planner", event=shown, with_project=True, wait_s=0.1)

    assert caught.value.file == write.project_lock("proj-a")
    assert taken == [("take", "a-round.lock"), ("free", "a-round.lock")]
    assert not where.project_log.exists()


# -- a lock that is held -----------------------------------------------------------------

def test_a_verb_that_finds_the_lock_held_refuses_after_its_wait_and_names_the_pid(
        where, holder):
    proc = holder(write.round_lock("proj-a", "a-round"))
    before = where.round_log.read_bytes()

    started = time.monotonic()
    with pytest.raises(Locked) as caught:
        write.record(where, actor="planner", event=done, wait_s=0.2)
    waited = time.monotonic() - started

    error = caught.value
    assert 0.2 <= waited < 10
    assert error.holder == proc.pid and error.waited_s == 0.2
    assert error.file == write.round_lock("proj-a", "a-round")
    assert f"pid {proc.pid}" in error.reason and f"pid {proc.pid}" in error.fix
    assert isinstance(error, RoundsError) and error.exit_code == exits.REFUSED == 3
    assert where.round_log.read_bytes() == before


def test_a_held_lock_stops_the_verb_before_its_gates_and_its_act(where, holder):
    holder(write.round_lock("proj-a", "a-round"))
    ran = []

    with pytest.raises(Locked):
        write.record(where, actor="planner", event=done, wait_s=0,
                     gates=[lambda view: ran.append("gate")],
                     act=lambda view: ran.append("act"),
                     facts=lambda where: ran.append("facts"))

    assert ran == []


def test_a_verb_waits_thirty_seconds_unless_told_otherwise():
    assert write.WAIT_S == 30


def test_a_held_project_lock_does_not_stop_a_verb_on_the_round_alone(where, holder):
    holder(write.project_lock("proj-a"))

    written = write.record(where, actor="planner", event=done, wait_s=0)

    assert [event["n"] for event in written.events] == [2]


def test_a_held_project_lock_stops_a_verb_on_the_project(where, holder):
    proc = holder(write.project_lock("proj-a"))

    with pytest.raises(Locked) as caught:
        write.record(write.Where("proj-a", where.folder), actor="planner", event=shown,
                     wait_s=0)

    assert caught.value.holder == proc.pid
    assert not where.project_log.exists()


def test_one_rounds_lock_does_not_hold_another_round(where, holder):
    holder(write.round_lock("proj-a", "a-round"))
    (where.folder / "b-round").mkdir()
    other = write.Where("proj-a", where.folder, "b-round")

    written = write.record(other, actor="planner", opens=True, wait_s=0,
                           event=lambda view, result: write.Entry("round.opened", OPENED))

    assert written.events[0]["n"] == 1

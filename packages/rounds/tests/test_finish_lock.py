"""The finish lock: taken and released as a ref in the repo, a live holder refused and
named, a dead holder on this machine cleared by exactly one of two finishes, anything
else at the ref left alone, `lock_ref` honoured, and the doctor's row for an old lock."""

from __future__ import annotations

import subprocess
import sys
import textwrap

import pytest
from moxi.doctor import Row

from moxi_rounds import doctor, events, finish_lock, places, settings, showing
from moxi_rounds.errors import Refused

REF = "refs/rounds/finishing/main"
ROUND = "proj-a/a-round"


def points_at(repo, ref: str) -> str | None:
    done = subprocess.run(["git", "-C", str(repo.root), "rev-parse", "--verify", "--quiet",
                           ref], capture_output=True, text=True, env=repo.env(),
                          check=False)
    return done.stdout.strip() or None


def blob_of(repo, text: str) -> str:
    done = subprocess.run(["git", "-C", str(repo.root), "hash-object", "-w", "--stdin"],
                          input=text, capture_output=True, text=True, env=repo.env(),
                          check=True)
    return done.stdout.strip()


def dead_pid() -> int:
    """The pid of a process that ran and has ended."""
    done = subprocess.Popen([sys.executable, "-c", "pass"])
    done.wait()
    return done.pid


def dead_line(pid: int | None = None, host: str | None = None) -> str:
    return finish_lock.holder("coder", ROUND, "B1", at="2026-01-02T03:04:05Z",
                              pid=dead_pid() if pid is None else pid,
                              host=places.host() if host is None else host).line + "\n"


_TAKER = textwrap.dedent("""
    import sys
    from moxi_rounds import finish_lock
    from moxi_rounds.errors import Refused
    try:
        taken = finish_lock.take(sys.argv[1], sys.argv[2], actor=sys.argv[3],
                                 name="proj-a/a-round", stage="B1")
    except Refused as error:
        print("refused", error.unmet[0].reason.replace("\\n", " "), flush=True)
    else:
        print("taken", taken.cleared is not None, flush=True)
    sys.stdin.read()
""")


@pytest.fixture
def taker():
    """`taker(root, ref, actor)`: a real process that tries to take the lock and stays
    alive until the test ends. Returns what it printed: `taken <cleared>` or `refused
    <reason>`."""
    procs = []

    def start(root, ref, actor):
        proc = subprocess.Popen([sys.executable, "-c", _TAKER, str(root), ref, actor],
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        procs.append(proc)
        return proc

    def said(proc) -> str:
        return proc.stdout.readline().strip()

    start.said = said
    yield start
    for proc in procs:
        proc.communicate(timeout=30)


# -- the ref and the holder's line -----------------------------------------------------------

def test_the_lock_is_a_ref_per_target_with_the_target_encoded(write_config):
    write_config("")
    read = settings.load()
    assert finish_lock.ref_of(read, "proj-a", "main") == REF
    assert (finish_lock.ref_of(read, "proj-a", "release/1.0")
            == "refs/rounds/finishing/release%2F1.0")
    assert (finish_lock.ref_of(read, "proj-a", "release")
            != finish_lock.ref_of(read, "proj-a", "release/1.0"))


def test_a_repos_lock_ref_replaces_the_name_for_every_target(write_config):
    write_config("""
        [projects.proj-a]
        lock_ref = "refs/locks/merging"
    """)
    read = settings.load()
    assert finish_lock.ref_of(read, "proj-a", "main") == "refs/locks/merging"
    assert finish_lock.ref_of(read, "proj-a", "next") == "refs/locks/merging"
    assert finish_lock.ref_of(read, "proj-b", "main") == REF


def test_the_holders_line_reads_back_and_stays_one_line():
    made = finish_lock.holder("lead  of\nproj-a", ROUND, "B2", at="2026-01-02T03:04:05Z",
                              pid=4321, host="box-1")
    assert made.line == "lead of proj-a pid=4321 host=box-1 2026-01-02T03:04:05Z " \
                        f"{ROUND} B2"
    assert finish_lock.parse(made.line + "\n") == made


@pytest.mark.parametrize("text", [
    "", "held by hand\n", "coder pid=12 host=box-1 2026-01-02T03:04:05Z proj-a/a-round\n",
    "coder pid=0 host=box-1 2026-01-02T03:04:05Z proj-a/a-round B1\n",
    "coder pid=12 host=box-1 2026-13-02T03:04:05Z proj-a/a-round B1\n",
    "coder pid=12 host=box-1 2026-01-02T03:04:05Z proj-a/a-round B1\nmore\n"])
def test_a_text_not_in_the_holders_format_names_no_holder(text):
    assert finish_lock.parse(text) is None


# -- take and release --------------------------------------------------------------------------

def test_a_take_points_the_ref_at_a_blob_with_the_holders_line_and_release_removes_it(
        git_repo):
    taken = finish_lock.take(git_repo.root, REF, actor="coder", name=ROUND, stage="B1")
    assert taken.cleared is None and finish_lock.cleared_entry(taken) is None
    assert points_at(git_repo, REF) == taken.blob
    assert git_repo.git("cat-file", "-t", taken.blob) == "blob"
    held = git_repo.git("cat-file", "blob", taken.blob)
    assert finish_lock.parse(held) == taken.holder
    assert taken.holder.round == ROUND and taken.holder.stage == "B1"
    assert taken.holder.host == places.host()

    assert finish_lock.release(taken) is True
    assert points_at(git_repo, REF) is None
    assert finish_lock.found(git_repo.root, REF) is None


def test_a_release_leaves_a_lock_that_is_no_longer_the_takers_own(git_repo):
    taken = finish_lock.take(git_repo.root, REF, actor="coder", name=ROUND, stage="B1")
    other = blob_of(git_repo, "someone else\n")
    git_repo.git("update-ref", REF, other)
    assert finish_lock.release(taken) is False
    assert points_at(git_repo, REF) == other


def test_a_lock_held_by_a_live_holder_is_refused_naming_it_and_the_command(git_repo):
    first = finish_lock.take(git_repo.root, REF, actor="coder", name=ROUND, stage="B1")
    with pytest.raises(Refused) as refused:
        finish_lock.take(git_repo.root, REF, actor="pilot", name="proj-a/b-round",
                         stage="B2")
    (unmet,) = refused.value.unmet
    assert first.holder.line in unmet.reason and "Ask the holder" in unmet.reason
    assert unmet.fix == f"git -C {git_repo.root} update-ref -d {REF} {first.blob}"
    assert points_at(git_repo, REF) == first.blob


def test_two_finishes_at_once_one_takes_the_lock_and_the_other_names_it(git_repo,
                                                                         taker):
    first = taker(git_repo.root, REF, "coder")
    assert taker.said(first) == "taken False"
    second = taker(git_repo.root, REF, "pilot")
    said = taker.said(second)
    assert said.startswith("refused") and f"coder pid={first.pid} " in said
    assert git_repo.git("cat-file", "blob", points_at(git_repo, REF)).startswith(
        f"coder pid={first.pid} ")


def test_the_command_a_refusal_prints_clears_that_lock(git_repo):
    first = finish_lock.take(git_repo.root, REF, actor="coder", name=ROUND, stage="B1")
    with pytest.raises(Refused) as refused:
        finish_lock.take(git_repo.root, REF, actor="pilot", name=ROUND, stage="B1")
    argv = refused.value.unmet[0].fix.split()
    subprocess.run(argv, check=True, env=git_repo.env())
    assert points_at(git_repo, REF) is None
    assert finish_lock.release(first) is False


# -- a dead holder -----------------------------------------------------------------------------

def test_a_dead_holder_on_this_machine_is_cleared_in_one_swap_and_recorded(git_repo):
    line = dead_line()
    dead = blob_of(git_repo, line)
    git_repo.git("update-ref", REF, dead)
    taken = finish_lock.take(git_repo.root, REF, actor="coder", name=ROUND, stage="B1")
    assert taken.cleared == finish_lock.parse(line)
    assert points_at(git_repo, REF) == taken.blob

    entry = finish_lock.cleared_entry(taken)
    assert entry.type == "lock.cleared"
    assert entry.fields == {"ref": REF, "holder": line.strip()}
    events.check(entry.type, entry.fields)


def test_of_two_finishes_that_find_a_dead_holder_one_clears_it(git_repo, taker):
    git_repo.git("update-ref", REF, blob_of(git_repo, dead_line()))
    procs = [taker(git_repo.root, REF, actor) for actor in ("coder", "pilot")]
    said = {proc: taker.said(proc) for proc in procs}
    (winner,) = [proc for proc in procs if said[proc] == "taken True"]
    (loser,) = [proc for proc in procs if said[proc].startswith("refused")]
    assert f"pid={winner.pid} " in said[loser]
    assert git_repo.git("cat-file", "blob", points_at(git_repo, REF)).split()[1] \
        == f"pid={winner.pid}"


def test_a_swap_git_refuses_because_another_finish_cleared_first_reads_the_new_holder(
        git_repo, taker, monkeypatch):
    git_repo.git("update-ref", REF, blob_of(git_repo, dead_line()))
    other = []
    dead_now = finish_lock._dead

    def first_another_clears(found_holder):
        # Between this finish's read of the dead holder and its swap, another finish
        # clears the lock and keeps it.
        if not other:
            other.append(taker(git_repo.root, REF, "pilot"))
            assert taker.said(other[0]) == "taken True"
        return dead_now(found_holder)

    monkeypatch.setattr(finish_lock, "_dead", first_another_clears)
    with pytest.raises(Refused) as refused:
        finish_lock.take(git_repo.root, REF, actor="coder", name=ROUND, stage="B1")
    assert f"pilot pid={other[0].pid} " in refused.value.unmet[0].reason


def test_a_holder_of_another_machine_is_not_cleared_even_with_a_dead_pid(git_repo):
    held = blob_of(git_repo, dead_line(host="box-1"))
    git_repo.git("update-ref", REF, held)
    with pytest.raises(Refused) as refused:
        finish_lock.take(git_repo.root, REF, actor="coder", name=ROUND, stage="B1")
    assert "host=box-1" in refused.value.unmet[0].reason
    assert points_at(git_repo, REF) == held


@pytest.mark.parametrize("text", ["held by hand\n", "coder pid=1 host=box-1\n"])
def test_a_blob_in_no_holders_format_is_not_cleared(git_repo, text):
    held = blob_of(git_repo, text)
    git_repo.git("update-ref", REF, held)
    with pytest.raises(Refused) as refused:
        finish_lock.take(git_repo.root, REF, actor="coder", name=ROUND, stage="B1")
    (unmet,) = refused.value.unmet
    assert f"a blob {held} that holds no holder's line" in unmet.reason
    assert unmet.fix.endswith(f"update-ref -d {REF} {held}")
    assert points_at(git_repo, REF) == held


def test_a_lock_taken_by_hand_at_a_commit_excludes_finish_and_is_not_cleared(
        git_repo, write_config):
    write_config("""
        [projects.proj-a]
        lock_ref = "refs/locks/merging"
    """)
    ref = finish_lock.ref_of(settings.load(), "proj-a", "main")
    head = git_repo.rev()
    git_repo.git("update-ref", ref, head)
    with pytest.raises(Refused) as refused:
        finish_lock.take(git_repo.root, ref, actor="coder", name=ROUND, stage="B1")
    (unmet,) = refused.value.unmet
    assert f"a commit {head}, not a lock a finish takes" in unmet.reason
    assert points_at(git_repo, ref) == head
    git_repo.git("update-ref", "-d", ref, head)
    taken = finish_lock.take(git_repo.root, ref, actor="coder", name=ROUND, stage="B1")
    assert points_at(git_repo, "refs/locks/merging") == taken.blob
    assert points_at(git_repo, REF) is None


def test_locks_of_two_targets_are_apart(git_repo):
    main = finish_lock.take(git_repo.root, REF, actor="coder", name=ROUND, stage="B1")
    other = finish_lock.take(git_repo.root, "refs/rounds/finishing/release%2F1.0",
                             actor="coder", name=ROUND, stage="B2")
    assert {points_at(git_repo, REF), points_at(git_repo, other.ref)} == {main.blob,
                                                                          other.blob}


def test_a_lock_taken_in_a_worktree_is_the_lock_of_the_repo(git_repo, tmp_path):
    git_repo.git("worktree", "add", "-q", "-b", "side", str(tmp_path / "side"))
    taken = finish_lock.take(tmp_path / "side", REF, actor="coder", name=ROUND,
                             stage="B1")
    assert points_at(git_repo, REF) == taken.blob
    with pytest.raises(Refused):
        finish_lock.take(git_repo.root, REF, actor="pilot", name=ROUND, stage="B1")


def test_a_ref_name_git_refuses_raises_what_git_said(git_repo):
    with pytest.raises(finish_lock.GitError) as failed:
        finish_lock.take(git_repo.root, "refs/rounds/finishing/a..b", actor="coder",
                         name=ROUND, stage="B1")
    assert "update-ref" in failed.value.reason


# -- the history and the doctor --------------------------------------------------------------

def test_a_cleared_lock_is_named_in_the_history_with_its_holder():
    line = dead_line(pid=4321).strip()
    assert showing.history_of({"type": "lock.cleared", "ref": REF, "holder": line}) \
        == f"{REF}, held by {line}"


def lock_rows() -> list[Row]:
    return [row for row in doctor.rows() if row.name.startswith("finish lock")]


def held_at(repo, ref: str, at: str, *, pid: int = 4321) -> str:
    line = finish_lock.holder("coder", ROUND, "B1", at=at, pid=pid, host="box-1").line
    blob = blob_of(repo, line + "\n")
    repo.git("update-ref", ref, blob)
    return blob


@pytest.fixture
def repo_project(git_repo, add_project, tmp_path):
    """The project `proj-a` with its root at the test's git repo."""
    root = add_project("proj-a", notes_root=tmp_path / "notes-a")
    root.rmdir()
    git_repo.root.rename(root)
    git_repo.root = root
    return git_repo


def test_the_doctor_is_fine_with_no_old_lock(repo_project):
    held_at(repo_project, REF, finish_lock.holder("coder", ROUND, "B1").at)
    (row,) = lock_rows()
    assert (row.name, row.state) == ("finish locks", "ok")
    assert "in the 1 repo read" in row.detail


def test_the_doctor_names_a_lock_older_than_an_hour_with_its_holder_and_command(
        repo_project):
    blob = held_at(repo_project, "refs/rounds/finishing/release%2F1.0",
                   "2026-01-02T03:04:05Z")
    (row,) = lock_rows()
    assert row.state == "warn"
    assert row.name == "finish lock proj-a refs/rounds/finishing/release%2F1.0"
    assert "coder pid=4321 host=box-1 2026-01-02T03:04:05Z" in row.detail
    assert row.fix.endswith(f"git -C {repo_project.root} update-ref -d "
                            f"refs/rounds/finishing/release%2F1.0 {blob}")


def test_the_doctor_reads_a_repos_lock_ref_and_leaves_a_hand_lock_alone(
        repo_project, write_config):
    write_config("""
        [projects.proj-a]
        lock_ref = "refs/locks/merging"
    """)
    held_at(repo_project, "refs/locks/merging", "2026-01-02T03:04:05Z")
    (row,) = lock_rows()
    assert row.name == "finish lock proj-a refs/locks/merging" and row.state == "warn"

    repo_project.git("update-ref", "refs/locks/merging", repo_project.rev())
    (row,) = lock_rows()
    assert row.state == "ok"


def test_the_doctor_skips_a_project_whose_root_is_no_repo(add_project, tmp_path):
    add_project("proj-b", notes_root=tmp_path / "notes-b")
    (row,) = lock_rows()
    assert row.state == "ok" and "in the 0 repos read" in row.detail


def test_the_doctor_changes_no_lock(repo_project):
    blob = held_at(repo_project, REF, "2026-01-02T03:04:05Z", pid=dead_pid())
    lock_rows()
    assert points_at(repo_project, REF) == blob

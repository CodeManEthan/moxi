"""The committer's doctor records: each commit and push key is written on its condition,
replaced by the next, and cleared by each way its condition ends; the passes that are not
an ending leave it. The record, its ledger key and the state file go together."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

import moxi_repo
from moxi import doctor, files, paths
from moxi_repo import _records, api, cli, committer
from tests.conftest import POSIX_ONLY, git
from tests.test_committer import AUTHOR, deny, make_remote, spec, write

pytestmark = POSIX_ONLY


def doctor_records() -> dict[str, dict]:
    path = paths.Layout("repo").state_dir() / doctor.RECORDS_FILE
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}


def cgd(repo: Path) -> Path:
    return repo / ".git"


def commit_key(repo: Path, name: str = "demo/snap") -> str:
    return _records.commit_key(cgd(repo), name)


def push_key(repo: Path, branch: str = "main", name: str = "demo/snap") -> str:
    return _records.push_key(cgd(repo), name, branch)


def is_open(key: str) -> set[str]:
    """Which of "record" and "ledger" hold the key."""
    out = set()
    if key in doctor_records():
        out.add("record")
    if key in _records.ledger():
        out.add("ledger")
    return out


OPEN = {"record", "ledger"}


def text(key: str) -> str:
    return doctor_records()[key]["text"]


def state(repo: Path, name: str = "demo/snap") -> dict:
    return committer.read_state(cgd(repo))["specs"].get(name, {})


@pytest.fixture
def clock(monkeypatch):
    now = [time.time()]
    monkeypatch.setattr(committer, "_now", lambda: now[0])
    return now


# -- the commit key ---------------------------------------------------------------------------

def test_a_refusal_writes_one_record_that_counts_its_passes_and_a_good_pass_clears(demo):
    repo = demo.repo()
    api.add(repo, ["demo/gate"])
    demo.act(gate="refuse")
    write(repo, "notes/a.md")
    first = moxi_repo.commit(spec(repo))
    assert first.status == "refused"
    key = commit_key(repo)
    assert is_open(key) == OPEN
    assert "refused since" in text(key) and "1 passes" in text(key)
    assert "gate says no" in text(key)
    assert doctor_records()[key]["fix"].endswith(f"moxi repo specs remove {repo} demo/snap")
    moxi_repo.commit(spec(repo))
    mine = [k for k in doctor_records() if "::spec::demo/snap::" in k]
    assert mine == [key] and "2 passes" in text(key)
    assert state(repo)["refused_passes"] == 2
    since = state(repo)["refused_since"]
    demo.act(gate="ok")
    assert moxi_repo.commit(spec(repo)).status == "committed"
    assert is_open(key) == set()
    assert "refused_since" not in state(repo) and since


def test_throttled_busy_and_index_busy_passes_leave_the_record(demo):
    repo = demo.repo()
    write(repo, "notes/a.md")
    assert moxi_repo.commit(spec(repo)).status == "committed"
    api.add(repo, ["demo/gate"])
    demo.act(gate="refuse")
    write(repo, "notes/b.md")
    assert moxi_repo.commit(spec(repo)).status == "refused"
    key = commit_key(repo)
    assert is_open(key) == OPEN
    demo.act(gate="ok")
    assert moxi_repo.commit(spec(repo, min_interval=3600)).status == "throttled"
    assert is_open(key) == OPEN
    with files.lock(cgd(repo) / "moxi-repo" / "lock"):
        assert moxi_repo.commit(spec(repo)).status == "busy"
    assert is_open(key) == OPEN
    (cgd(repo) / "index.lock").write_text("", encoding="utf-8")
    try:
        assert moxi_repo.commit(spec(repo)).status == "index-busy"
    finally:
        (cgd(repo) / "index.lock").unlink()
    assert is_open(key) == OPEN and "1 passes" in text(key)
    assert moxi_repo.commit(spec(repo)).status == "committed"
    assert is_open(key) == set()


def test_a_drop_an_unjudged_path_and_hook_lines_each_write_the_record(demo, monkeypatch):
    repo = demo.repo()
    key = commit_key(repo)
    write(repo, "notes/a.md")
    write(repo, "notes/key.txt")
    dropped = moxi_repo.commit(spec(repo, prepare=lambda ctx: moxi_repo.Prepared(
        drop=[("notes/key.txt", "a token")])))
    assert dropped.status == "committed"
    assert "notes/key.txt (dropped: a token)" in text(key)
    write(repo, "notes/b.md")
    assert moxi_repo.commit(spec(repo)).status == "committed"
    assert is_open(key) == set()
    # A post-commit notice on a background pass: committed, and the line is in the record.
    api.add(repo, ["demo/watch"])
    demo.act(watch="notice")
    write(repo, "notes/c.md")
    noticed = moxi_repo.commit(spec(repo))
    assert noticed.status == "committed" and noticed.hook_lines
    assert "watch noticed something" in text(key)
    demo.act(watch="ok")
    # A piece that stages on a committer commit: the path is named unjudged.
    api.add(repo, ["demo/gate"])
    demo.act(gate="stage")
    write(repo, "notes/d.md")
    staged = moxi_repo.commit(spec(repo))
    assert staged.unjudged == ("staged-by-piece.txt",)
    assert "committed unjudged 1: staged-by-piece.txt" in text(key)


def test_git_index_lock_held_five_passes_in_a_row_is_recorded(demo):
    repo = demo.repo()
    key = commit_key(repo)
    write(repo, "notes/a.md")
    lock = cgd(repo) / "index.lock"
    lock.write_text("", encoding="utf-8")
    try:
        for n in range(committer.STALE_INDEX_PASSES - 1):
            assert moxi_repo.commit(spec(repo)).status == "index-busy"
        assert is_open(key) == set()
        assert moxi_repo.commit(spec(repo)).status == "index-busy"
        assert is_open(key) == OPEN and "index.lock" in text(key)
        assert str(lock) in doctor_records()[key]["fix"]
    finally:
        lock.unlink()
    assert moxi_repo.commit(spec(repo)).status == "committed"
    assert is_open(key) == set() and "index_busy_passes" not in state(repo)


def test_an_error_before_the_lock_has_no_key_and_a_bad_spec_in_a_repo_has_one(demo, tmp_path):
    plain = tmp_path / "not-a-repo"
    plain.mkdir()
    result = moxi_repo.commit(spec(plain))
    assert result.status == "error" and "not in a git work tree" in result.detail
    assert _records.ledger() == {} and doctor_records() == {}
    repo = demo.repo()
    bad = moxi_repo.commit(spec(repo, paths=tuple(f"p{i}" for i in range(70))))
    assert bad.status == "error"
    assert is_open(commit_key(repo)) == OPEN and "70 paths" in text(commit_key(repo))
    good = moxi_repo.commit(spec(repo))
    assert good.status == "clean" and is_open(commit_key(repo)) == set()


def test_an_unreadable_saved_spec_errors_with_a_record(demo):
    repo = demo.repo()
    moxi_repo.save_spec(spec(repo))
    git(repo, "config", "moxi-repo.spec.demo/snap.author", "nobody")
    result = moxi_repo.commit_saved(repo, "demo/snap")
    assert result.status == "error" and "author" in result.detail
    assert is_open(commit_key(repo)) == OPEN
    missing = moxi_repo.commit_saved(repo, "demo/none")
    assert missing.status == "error" and is_open(commit_key(repo, "demo/none")) == set()


def test_remove_spec_forgets_its_section_state_and_records(demo, clock, capsys):
    repo = demo.repo()
    bare = make_remote(demo, repo)
    deny(bare, True)
    s = spec(repo, push="origin")
    moxi_repo.save_spec(s)
    api.add(repo, ["demo/gate"])
    demo.act(gate="refuse")
    write(repo, "notes/a.md")
    assert moxi_repo.commit_saved(repo, "demo/snap").status == "refused"
    demo.act(gate="ok")
    clock[0] += 1
    assert moxi_repo.commit_saved(repo, "demo/snap").status == "committed"
    demo.act(gate="refuse")
    write(repo, "notes/b.md")
    assert moxi_repo.commit_saved(repo, "demo/snap").status == "refused"
    assert is_open(commit_key(repo)) == OPEN and is_open(push_key(repo)) == OPEN
    assert cli.main(["specs", "remove", str(repo), "demo/snap"]) == 0
    assert is_open(commit_key(repo)) == set() and is_open(push_key(repo)) == set()
    assert state(repo) == {} and committer.saved_names(cgd(repo)) == []
    assert cli.main(["specs", "remove", str(repo), "demo/snap"]) == 2
    capsys.readouterr()


# -- the push key -----------------------------------------------------------------------------

def failed_push(demo, repo: Path, clock, *, branch: str = "main"):
    """A commit on `branch` whose push fails; returns the remote."""
    bare = demo.tmp / f"{repo.name}-remote.git"
    if not bare.exists():
        make_remote(demo, repo)
    deny(bare, True)
    write(repo, f"notes/{branch}-{clock[0]}.md")
    result = moxi_repo.commit(spec(repo, push="origin"))
    assert result.status == "committed" and result.push_error, result
    assert is_open(push_key(repo, branch)) == OPEN
    return bare


def test_a_push_record_is_replaced_by_the_next_failure_and_cleared_by_a_push(demo, clock):
    repo = demo.repo()
    bare = failed_push(demo, repo, clock)
    key = push_key(repo)
    assert "remote says no" in text(key)
    clock[0] += 400
    again = moxi_repo.commit(spec(repo, push="origin"))
    assert again.push_error and is_open(key) == OPEN
    assert [k for k in doctor_records() if "::push::" in k] == [key]
    deny(bare, False)
    clock[0] += 400
    assert moxi_repo.commit(spec(repo, push="origin")).pushed == ("main",)
    assert is_open(key) == set() and state(repo)["push"] == {}


def test_a_branch_no_longer_ahead_clears_its_push_record(demo, clock):
    repo = demo.repo()
    bare = failed_push(demo, repo, clock)
    deny(bare, False)
    git(repo, "push", "-q", "origin", "main")
    result = moxi_repo.commit(spec(repo, push="origin"))
    assert result.pushed == () and is_open(push_key(repo)) == set()


def test_push_unset_clears_its_push_record(demo, clock):
    repo = demo.repo()
    failed_push(demo, repo, clock)
    moxi_repo.commit(spec(repo))
    assert is_open(push_key(repo)) == set() and state(repo)["push"] == {}


def test_a_deleted_branch_clears_its_push_record(demo, clock):
    repo = demo.repo()
    git(repo, "switch", "-q", "-c", "a")
    failed_push(demo, repo, clock, branch="a")
    git(repo, "switch", "-q", "main")
    git(repo, "branch", "-q", "-D", "a")
    moxi_repo.commit(spec(repo, push="origin"))
    assert is_open(push_key(repo, "a")) == set()


def test_a_removed_remote_replaces_the_record_until_it_is_back(demo, clock):
    repo = demo.repo()
    bare = failed_push(demo, repo, clock)
    key = push_key(repo)
    git(repo, "remote", "remove", "origin")
    gone = moxi_repo.commit(spec(repo, push="origin"))
    assert "remote origin is not configured" in gone.push_error and gone.exit_code == 1
    assert "not configured" in text(key)
    assert "add the remote origin" in doctor_records()[key]["fix"]
    git(repo, "remote", "add", "origin", str(bare))
    deny(bare, False)
    clock[0] += 400
    back = moxi_repo.commit(spec(repo, push="origin"))
    assert back.pushed == ("main",) and is_open(key) == set()


def test_a_detached_head_is_not_an_ending(demo, clock):
    repo = demo.repo()
    failed_push(demo, repo, clock)
    git(repo, "switch", "-q", "--detach")
    clock[0] += 10
    assert moxi_repo.commit(spec(repo, push="origin")).detached
    assert is_open(push_key(repo)) == OPEN
    clock[0] += 400
    retried = moxi_repo.commit(spec(repo, push="origin"))
    assert retried.push_error and is_open(push_key(repo)) == OPEN


# -- the sweep's spec rows ---------------------------------------------------------------------

def test_the_sweep_clears_an_unsaved_specs_keys_once_its_state_file_is_gone(demo):
    repo = demo.repo()
    api.add(repo, ["demo/gate"])
    demo.act(gate="refuse")
    write(repo, "notes/a.md")
    moxi_repo.commit(spec(repo))
    key = commit_key(repo)
    assert _records.judge() == []
    (cgd(repo) / "moxi-repo" / "state.json").unlink()
    [found] = _records.judge()
    assert found.key == key and "neither saved nor in the repo's state file" in found.reason
    api.specs(repo)
    assert is_open(key) == set()


def test_the_sweep_clears_a_push_key_whose_saved_spec_stops_pushing_or_branch_goes(demo, clock):
    repo = demo.repo()
    moxi_repo.save_spec(spec(repo, push="origin"))
    git(repo, "switch", "-q", "-c", "a")
    failed_push(demo, repo, clock, branch="a")
    git(repo, "switch", "-q", "main")
    failed_push(demo, repo, clock, branch="main")
    git(repo, "branch", "-q", "-D", "a")
    [found] = _records.judge()
    assert (found.key, found.reason) == (push_key(repo, "a"), "its branch is gone")
    api.specs(repo)
    assert is_open(push_key(repo, "a")) == set() and is_open(push_key(repo)) == OPEN
    git(repo, "config", "--unset", "moxi-repo.spec.demo/snap.push")
    [found] = _records.judge()
    assert found.reason == "its saved spec no longer pushes"
    api.refresh(repo)
    assert is_open(push_key(repo)) == set()


def test_the_record_part_writes_the_state_entry_before_the_ledger_key(demo, monkeypatch):
    repo = demo.repo()
    api.add(repo, ["demo/gate"])
    demo.act(gate="refuse")
    real = _records.open_record
    seen = []

    def check(key, **kw):
        seen.append(committer.read_state(cgd(repo))["specs"].get("demo/snap", {}).get("last"))
        return real(key, **kw)

    monkeypatch.setattr(_records, "open_record", check)
    write(repo, "notes/a.md")
    moxi_repo.commit(spec(repo))
    assert seen and seen[0]["status"] == "refused"


def test_a_sweep_leaves_spec_keys_while_a_pass_holds_the_lock(demo):
    """The window between a first pass's state entry and its record can't be swept: the
    sweep judges spec keys only under the committer's lock."""
    repo = demo.repo()
    key = commit_key(repo, "demo/new")
    _records.open_record(key, cgd=cgd(repo), kind="commit", subject={"spec": "demo/new"},
                         text="a first pass, its state entry not written yet")
    took, release = threading.Event(), threading.Event()

    def hold():
        with files.lock(cgd(repo) / "moxi-repo" / "lock"):
            took.set()
            release.wait(10)

    holder = threading.Thread(target=hold)
    holder.start()
    try:
        assert took.wait(10)
        listed = api.specs(repo)
        assert listed[0]["root"] == str(repo)
        assert is_open(key) == OPEN
    finally:
        release.set()
        holder.join(10)
    api.specs(repo)
    assert is_open(key) == set()


def test_specs_lists_saved_and_unsaved_specs_with_their_last_result(demo, capsys):
    repo = demo.repo()
    moxi_repo.save_spec(spec(repo, push="origin"))
    write(repo, "notes/a.md")
    moxi_repo.commit(spec(repo, name="demo/loose", paths=("loose",)))
    moxi_repo.commit_saved(repo, "demo/snap")
    [listed] = api.specs(repo)
    by_name = {x["name"]: x for x in listed["specs"]}
    assert by_name["demo/snap"]["saved"] and by_name["demo/snap"]["spec"]["push"] == "origin"
    assert by_name["demo/snap"]["last"]["status"] == "committed"
    assert by_name["demo/snap"]["push"]["main"]["error"] == "remote origin is not configured"
    assert by_name["demo/snap"]["records"] == [push_key(repo)]
    assert not by_name["demo/loose"]["saved"]
    assert by_name["demo/loose"]["last"]["status"] == "clean"
    assert api.specs() == [listed]
    assert cli.main(["specs", str(repo)]) == 0
    out = capsys.readouterr().out
    assert "demo/snap  saved  last: committed" in out and "demo/loose  not saved" in out
    assert cli.main(["specs", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == [dict(listed, project=None)]
    assert AUTHOR == tuple(by_name["demo/snap"]["spec"]["author"])

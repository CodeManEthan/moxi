"""The doctor records the runner writes for a piece, and every way the verbs and the sweep
clear one: the record, its ledger key and the piece's `recorded` flag go together."""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

import moxi.cli
from moxi import doctor, paths

from moxi_repo import _records, api, gitconf, run
from tests.conftest import POSIX_ONLY, change, commit, doctor as doctor_rows, git, problems

pytestmark = POSIX_ONLY


def doctor_records() -> dict[str, dict]:
    path = paths.Layout("repo").state_dir() / doctor.RECORDS_FILE
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}


def key(repo: Path, piece: str) -> str:
    return _records.piece_key(gitconf.resolve(repo).common_dir, piece)


def left(repo: Path, piece: str) -> set[str]:
    """What is open of a piece's record: any of "record", "ledger" and "flag"."""
    k = key(repo, piece)
    out = set()
    if k in doctor_records():
        out.add("record")
    if k in _records.ledger():
        out.add("ledger")
    if repo.exists():
        section = gitconf.read(gitconf.resolve(repo)).pieces.get(piece, {})
        if "recorded" in section:
            out.add("flag")
    return out


OPEN = {"record", "ledger", "flag"}


def test_one_record_per_key_the_newer_replacing_the_older(demo):
    repo = demo.repo()
    api.add(repo, ["demo/watch"])
    demo.act(watch="record")
    change(repo)
    commit(repo)
    assert left(repo, "demo/watch") == OPEN
    assert "watch recorded something" in doctor_records()[key(repo, "demo/watch")]["text"]
    demo.act(watch="raise")
    change(repo, text="2\n")
    commit(repo)
    mine = [k for k in doctor_records() if k.endswith("::piece::demo/watch")]
    assert mine == [key(repo, "demo/watch")]
    assert "watch fell over" in doctor_records()[mine[0]]["text"]
    assert len(_records.ledger()) == 1


def test_a_refuse_run_and_a_notice_run_each_clear_the_record(demo):
    repo = demo.repo()
    api.add(repo, ["demo/gate", "demo/watch"])
    demo.act(gate="raise")
    change(repo)
    assert commit(repo).returncode != 0
    assert left(repo, "demo/gate") == OPEN
    demo.act(gate="refuse")
    assert commit(repo).returncode != 0
    assert left(repo, "demo/gate") == set()
    demo.act(gate="ok", watch="record")
    assert commit(repo).returncode == 0
    assert left(repo, "demo/watch") == OPEN
    demo.act(watch="notice")
    change(repo, text="2\n")
    done = commit(repo)
    assert "watch noticed something" in done.stderr
    assert left(repo, "demo/watch") == set()
    assert doctor_records() == {} and _records.ledger() == {}


def test_enable_after_disable_writes_no_record(demo):
    repo = demo.repo()
    api.add(repo, ["demo/lint"])
    demo.act(lint="raise")
    change(repo)
    commit(repo)
    api.disable(repo, "demo/lint")
    assert left(repo, "demo/lint") == set()
    api.enable(repo, "demo/lint")
    assert left(repo, "demo/lint") == set()


def test_a_hand_edit_of_enabled_is_cleared_by_the_sweep(demo):
    repo = demo.repo()
    api.add(repo, ["demo/lint"])
    demo.act(lint="raise")
    change(repo)
    commit(repo)
    assert left(repo, "demo/lint") == OPEN
    git(repo, "config", "moxi-repo.piece.demo/lint.enabled", "false")
    [found] = _records.judge(gitconf.resolve(repo).common_dir)
    assert (found.key, found.action, found.reason) == (
        key(repo, "demo/lint"), "clear", "its piece is disabled")
    # judge reads only: the doctor's row stands until a verb sweeps.
    assert left(repo, "demo/lint") == OPEN
    [row] = problems("ledger ")
    assert (row.state, row.name, row.fix) == (
        "warn", f"ledger {key(repo, 'demo/lint')}", f"moxi repo git-hooks refresh {repo}")
    assert row.detail == "its piece is disabled; the next sweep clears its record"
    assert left(repo, "demo/lint") == OPEN
    api.add(repo)
    assert left(repo, "demo/lint") == set()
    assert problems() == []


def test_a_hand_removed_section_is_cleared_by_the_sweep(demo):
    repo = demo.repo()
    api.add(repo, ["demo/watch"])
    demo.act(watch="record")
    change(repo)
    commit(repo)
    git(repo, "config", "--remove-section", "moxi-repo.piece.demo/watch")
    [found] = _records.judge()
    assert found.reason == "its piece is no longer registered"
    api.refresh(repo)
    assert left(repo, "demo/watch") == set()
    assert _records.judge() == []


def test_a_flag_write_that_meets_a_held_config_lock(demo):
    repo = demo.repo()
    api.add(repo, ["demo/watch"])
    demo.act(watch="record")
    lock = repo / ".git" / "config.lock"
    lock.write_text("", encoding="utf-8")
    change(repo)
    started = time.monotonic()
    done = commit(repo)
    took = time.monotonic() - started
    assert done.returncode == 0, done.stderr
    # Three tries, 20 ms and 50 ms apart, then the ledger key and the record anyway.
    assert took >= sum(run.FLAG_RETRY)
    assert left(repo, "demo/watch") == {"record", "ledger"}
    [found] = _records.judge()
    assert (found.action, found.reason) == ("flag", "its piece has no recorded flag")
    [row] = problems("ledger ")
    assert row.state == "warn" and "no recorded flag" in row.detail
    lock.unlink()
    # The repo's next committer pass sets the flag, from its record part's sweep.
    import moxi_repo

    result = moxi_repo.commit(moxi_repo.Spec(
        name="demo/snap", root=repo, paths=("README.md",),
        author=("snap-bot", "snap@example.invalid")))
    assert result.status == "clean", result
    assert left(repo, "demo/watch") == OPEN
    assert _records.judge() == []
    demo.act(watch="ok")
    change(repo, text="2\n")
    commit(repo)
    assert left(repo, "demo/watch") == set()


def test_a_cleared_record_then_a_good_run_leaves_nothing(demo):
    repo = demo.repo()
    api.add(repo, ["demo/lint"])
    demo.act(lint="raise")
    change(repo)
    commit(repo)
    [row] = [r for r in doctor_rows() if r.name == f"record {key(repo, 'demo/lint')}"]
    assert row.state == "fail" and row.fix.endswith(
        f"then: moxi doctor --clear repo {key(repo, 'demo/lint')}")
    assert moxi.cli.main(["doctor", "--clear", "repo", key(repo, "demo/lint")]) == 0
    assert left(repo, "demo/lint") == {"ledger", "flag"}
    demo.act(lint="ok")
    change(repo, text="2\n")
    assert commit(repo).returncode == 0
    assert left(repo, "demo/lint") == set()
    assert doctor_records() == {} and _records.ledger() == {}


def test_refresh_keeps_the_flag_and_the_next_good_run_clears(demo):
    repo = demo.repo()
    api.add(repo, ["demo/lint"])
    demo.act(lint="raise")
    change(repo)
    commit(repo)
    api.refresh(repo)
    api.refresh()
    assert left(repo, "demo/lint") == OPEN
    demo.act(lint="ok")
    change(repo, text="2\n")
    commit(repo)
    assert left(repo, "demo/lint") == set()


def test_remove_path_piece_clears_its_record(demo):
    repo = demo.repo()
    api.add(repo, ["demo/lint", "demo/watch"])
    demo.act(lint="raise", watch="record")
    change(repo)
    commit(repo)
    api.remove(repo, ["demo/lint"])
    assert left(repo, "demo/lint") == set()
    assert left(repo, "demo/watch") == OPEN


def test_remove_path_clears_every_key_of_that_repo_only(demo):
    repo, other = demo.repo("alpha-notes"), demo.repo("beta-code")
    for r in (repo, other):
        api.add(r, ["demo/lint", "demo/watch"])
    demo.act(lint="raise", watch="record")
    for r in (repo, other):
        change(r)
        commit(r)
    assert len(_records.ledger()) == 4
    api.remove(repo)
    assert set(_records.ledger()) == {key(other, "demo/lint"), key(other, "demo/watch")}
    assert set(doctor_records()) == set(_records.ledger())


def test_a_deleted_repo_shows_in_judge_then_the_sweep_clears_it(demo):
    repo, other = demo.repo("alpha-notes"), demo.repo("beta-code")
    for r in (repo, other):
        api.add(r, ["demo/lint"])
    demo.act(lint="raise")
    for r in (repo, other):
        change(r)
        commit(r)
    gone, kept = key(repo, "demo/lint"), key(other, "demo/lint")
    shutil.rmtree(repo)
    [found] = _records.judge()
    assert (found.key, found.action, found.reason) == (gone, "clear", "its repo is gone")
    assert gone in doctor_records()
    # The doctor's warn row, until the sweep or `remove` on the old path.
    [row] = problems("ledger ")
    assert (row.name, row.state, row.fix) == (
        f"ledger {gone}", "warn", f"moxi repo git-hooks remove {repo}")
    assert row.detail == f"its repo {repo} is gone; the next sweep clears its record"
    assert any(r.name == f"hooks {repo}" and "no longer a git work tree" in r.detail
               for r in problems())
    swept = _records.sweep()
    assert [f.key for f in swept] == [gone]
    assert set(_records.ledger()) == {kept} and set(doctor_records()) == {kept}


def test_remove_on_a_deleted_repos_old_path_clears_its_records(demo):
    repo, other = demo.repo("alpha-notes"), demo.repo("beta-code")
    for r in (repo, other):
        api.add(r, ["demo/lint"])
    demo.act(lint="raise")
    for r in (repo, other):
        change(r)
        commit(r)
    kept = key(other, "demo/lint")
    shutil.rmtree(repo)
    api.remove(repo)
    assert set(_records.ledger()) == {kept} and set(doctor_records()) == {kept}
    assert set(api.indexed()) == {gitconf.resolve(other).key}

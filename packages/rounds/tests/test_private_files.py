"""A project's private files: `commission` copies each one its `private_files` lists into
the new worktree, refusing before it makes anything when one can't be brought; a copy
reads as ignored to every reader, and the removal rule lets it go while nobody changed
it; and no commit finish would bring may hold one."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path

import pytest

from moxi_rounds import commissioning, doctor, log, opening, places, private_files
from moxi_rounds.errors import Refused, RoundsError
from moxi_rounds.gitreads import Git

ROUND = "proj-a/a-round"
SECRET = "the private notes\n"


@pytest.fixture
def repo(git_repo, tmp_path):
    """The project `proj-a`, whose root is the test's git repo with `main` checked out
    and `AGENTS.md` there, a file `info/exclude` lists. Returns the repo."""
    from moxi import cli

    assert cli.main(["projects", "add", "proj-a", "--root", str(git_repo.root)]) == 0
    assert cli.main(["projects", "set", "proj-a", "--notes-root",
                     str(tmp_path / "notes")]) == 0
    exclude(git_repo, "AGENTS.md", ".venv")
    (git_repo.root / "AGENTS.md").write_text(SECRET, encoding="utf-8")
    return git_repo


def exclude(repo, *lines: str) -> None:
    path = repo.root / ".git" / "info" / "exclude"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write("".join(f"{line}\n" for line in lines))


@pytest.fixture
def listing(write_config):
    """`listing(*entries)`: the project's `private_files`."""

    def write(*entries: str) -> None:
        write_config(f"[projects.proj-a]\nprivate_files = {json.dumps(list(entries))}\n")

    return write


@pytest.fixture
def opened(repo, tmp_path, listing):
    """The round, open with the stage B1 on `work`; its log. The project lists
    `AGENTS.md`."""
    listing("AGENTS.md")
    opening.open_round(ROUND, description="a placeholder round", by="planner",
                       flow="fix")
    path = log.round_log(tmp_path / "notes" / "rounds" / "a-round")
    log.append(path, "stage.declared",
               {"id": "B1", "repo": "proj-a", "branch": "work", "after": []},
               by="planner")
    return path


def commission(**given) -> commissioning.Commissioned:
    return commissioning.commission(ROUND, "B1", builder="coder", by="planner", **given)


def refused(**given) -> list[tuple[str, str]]:
    with pytest.raises(Refused) as caught:
        commission(**given)
    return [(one.reason, one.fix) for one in caught.value.unmet]


def commissioned(path: Path) -> list[dict]:
    return [one for one in log.read(path) if one["type"] == "build.commissioned"]


def worktree_place() -> Path:
    from moxi_rounds import settings as own

    return places.worktree_place(own.load(), "proj-a", "work", None)


def force_commit(repo, name: str, text: str = "tracked") -> str:
    """Write `text` to `name` in the repo's work tree, add it whatever ignores it, and
    commit it."""
    path = repo.root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text + "\n", encoding="utf-8")
    repo.git("add", "-f", "--", name)
    repo.git("commit", "-q", "-m", f"add {name}")
    return repo.rev()


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


# -- bringing a file in ---------------------------------------------------------------------

def test_commission_copies_each_private_file_and_records_it(repo, opened):
    os.chmod(repo.root / "AGENTS.md", 0o640)

    made = commission()

    copy = made.worktree / "AGENTS.md"
    assert copy.is_file() and not copy.is_symlink()
    assert copy.read_text(encoding="utf-8") == SECRET
    if os.name != "nt":
        assert (os.stat(copy).st_mode & 0o777) == 0o640
    [event] = commissioned(opened)
    assert event["brought"] == [{"path": "AGENTS.md",
                                 "from": places.contract_home(repo.root / "AGENTS.md"),
                                 "sha256": sha(SECRET)}]
    assert made.brought == tuple(event["brought"])
    # An edit of the copy never reaches the project's own file.
    copy.write_text("changed here\n", encoding="utf-8")
    assert (repo.root / "AGENTS.md").read_text(encoding="utf-8") == SECRET


def test_a_nested_file_is_copied_with_its_parents(repo, opened, listing):
    exclude(repo, "notes/local.md")
    (repo.root / "notes").mkdir()
    (repo.root / "notes" / "local.md").write_text("local\n", encoding="utf-8")
    listing("AGENTS.md", "notes/local.md")

    made = commission()

    assert (made.worktree / "notes" / "local.md").read_text(encoding="utf-8") == "local\n"
    assert [one["path"] for one in commissioned(opened)[0]["brought"]] == [
        "AGENTS.md", "notes/local.md"]


def test_a_project_that_lists_none_records_nothing_brought(repo, opened, write_config):
    write_config("")

    made = commission()

    assert "brought" not in commissioned(opened)[0]
    assert made.brought == ()
    assert not (made.worktree / "AGENTS.md").exists()


def test_a_source_that_is_a_link_to_a_file_is_copied_as_a_file(repo, opened, tmp_path):
    real = tmp_path / "elsewhere.md"
    real.write_text("linked\n", encoding="utf-8")
    (repo.root / "AGENTS.md").unlink()
    try:
        os.symlink(real, repo.root / "AGENTS.md")
    except OSError:
        pytest.skip("this system makes no symlink here")

    made = commission()

    copy = made.worktree / "AGENTS.md"
    assert not copy.is_symlink() and copy.read_text(encoding="utf-8") == "linked\n"


# -- what refuses a commission before anything is made ---------------------------------------

def nothing_made(path: Path) -> None:
    assert not os.path.lexists(worktree_place())
    assert commissioned(path) == []


def test_a_missing_source_refuses_and_makes_nothing(repo, opened):
    (repo.root / "AGENTS.md").unlink()

    [(reason, fix)] = refused()

    assert "the private file AGENTS.md of proj-a is not at" in reason
    assert "take AGENTS.md out of private_files" in fix
    nothing_made(opened)


def test_a_source_that_is_no_file_refuses(repo, opened, listing):
    exclude(repo, "kit")
    (repo.root / "kit").mkdir()
    (repo.root / "kit" / "a.md").write_text("a\n", encoding="utf-8")
    listing("kit")

    [(reason, _fix)] = refused()

    assert "the private file kit of proj-a is not a file" in reason
    nothing_made(opened)


def test_a_file_tracked_at_the_base_refuses(repo, opened):
    repo.git("add", "-f", "AGENTS.md")
    repo.git("commit", "-q", "-m", "track it")

    [(reason, fix)] = refused()

    assert "the private file AGENTS.md of proj-a is tracked, as AGENTS.md" in reason
    assert fix.startswith("take AGENTS.md out of private_files in [projects.proj-a]")
    nothing_made(opened)


def test_a_case_only_twin_is_tracked_where_the_repo_ignores_case(repo, opened, listing):
    repo.git("config", "core.ignorecase", "true")
    (repo.root / "AGENTS.md").unlink()
    force_commit(repo, "agents.md")
    (repo.root / "agents.md").unlink()
    (repo.root / "AGENTS.md").write_text(SECRET, encoding="utf-8")
    listing("AGENTS.md")

    [(reason, _fix)] = refused()

    assert "is tracked, as agents.md" in reason
    nothing_made(opened)


def test_a_non_ascii_entry_tracked_at_the_base_refuses(repo, opened, listing):
    exclude(repo, "é.md")
    force_commit(repo, "é.md")
    listing("é.md")

    [(reason, _fix)] = refused()

    assert "the private file é.md of proj-a is tracked" in reason
    nothing_made(opened)


def test_a_file_where_the_entry_needs_a_parent_refuses(repo, opened, listing):
    exclude(repo, "notes/local.md")
    repo.commit("notes", "a file where the folder would be")
    listing("notes/local.md")
    (repo.root / "notes").unlink()
    (repo.root / "notes").mkdir()
    (repo.root / "notes" / "local.md").write_text("local\n", encoding="utf-8")

    [(reason, _fix)] = refused()

    assert "the private file notes/local.md of proj-a is tracked, as notes" in reason
    nothing_made(opened)


def test_a_file_git_at_the_root_does_not_ignore_refuses_naming_the_exclude_file(
        repo, opened, listing):
    (repo.root / "LOOSE.md").write_text("loose\n", encoding="utf-8")
    listing("LOOSE.md")

    [(reason, fix)] = refused()

    assert "does not read the private file LOOSE.md of proj-a as ignored" in reason
    assert fix.startswith(
        f"add the line LOOSE.md to {repo.root / '.git' / 'info' / 'exclude'}")
    nothing_made(opened)


# -- the worktree must read every entry as ignored --------------------------------------------

def test_no_pattern_in_the_worktree_stops_naming_the_exclude_file_and_a_rerun_brings(
        repo, opened, listing):
    repo.commit(".gitignore", ".venv")
    (repo.root / ".gitignore").write_text(".venv\nlocal.md\n", encoding="utf-8")
    (repo.root / "local.md").write_text("local\n", encoding="utf-8")
    listing("AGENTS.md", "local.md")

    with pytest.raises(RoundsError) as caught:
        commission()

    assert "does not read the private file local.md as ignored: no pattern" in (
        caught.value.reason)
    assert caught.value.fix.startswith(
        f"add the line local.md to {repo.root / '.git' / 'info' / 'exclude'}")
    place = worktree_place()
    assert place.is_dir() and not (place / "AGENTS.md").exists()
    assert commissioned(opened) == []

    exclude(repo, "local.md")
    made = commission()

    assert made.adopted and made.worktree == place
    assert (place / "local.md").read_text(encoding="utf-8") == "local\n"
    assert (place / "AGENTS.md").read_text(encoding="utf-8") == SECRET


def test_a_committed_negation_stops_naming_its_line_and_the_remove_fix(repo, opened):
    repo.switch("work", new_from="main")
    repo.commit(".gitignore", "!AGENTS.md")
    repo.switch("main")

    for _again in range(2):
        with pytest.raises(RoundsError) as caught:
            commission(existing=True)
        assert ".gitignore:1:!AGENTS.md un-ignores it" in caught.value.reason
        assert f"git -C {repo.root} worktree remove {worktree_place()}" in (
            caught.value.fix)
        assert "branch -D" not in caught.value.fix
        assert not (worktree_place() / "AGENTS.md").exists()
        # The exclude file alone does not win over the branch's own .gitignore.
        exclude(repo, "AGENTS.md")
    assert commissioned(opened) == []


# -- a rerun -------------------------------------------------------------------------------

def stopped_before_the_scratch_dir(monkeypatch) -> None:
    """Run the commission to a stop after its worktree, before its scratch folder."""

    def stop(*_args):
        raise RoundsError("stopped here", "run it again")

    with monkeypatch.context() as patch:
        patch.setattr(commissioning, "_make_scratch", stop)
        with pytest.raises(RoundsError):
            commission()


def test_a_rerun_keeps_its_own_unchanged_copy(repo, opened, monkeypatch):
    stopped_before_the_scratch_dir(monkeypatch)

    made = commission()

    assert made.adopted
    assert (made.worktree / "AGENTS.md").read_text(encoding="utf-8") == SECRET
    assert commissioned(opened)[0]["brought"][0]["sha256"] == sha(SECRET)


@pytest.mark.parametrize("what", ["other bytes", "a folder"])
def test_a_rerun_never_overwrites_what_is_at_the_place(repo, opened, monkeypatch, what):
    stopped_before_the_scratch_dir(monkeypatch)
    place = worktree_place() / "AGENTS.md"
    place.unlink()
    if what == "a folder":
        place.mkdir()
    else:
        place.write_text("mine\n", encoding="utf-8")

    with pytest.raises(RoundsError) as caught:
        commission()

    assert f"{place} is there and is not a copy of" in caught.value.reason
    assert "rounds never overwrites it" in caught.value.reason
    assert caught.value.fix.startswith(f"look at {place}, remove it, then:")
    assert commissioned(opened) == []


def test_a_stop_on_rounds_own_earlier_copy_says_removing_it_is_safe(repo, tmp_path):
    worktree = tmp_path / "wt"
    Git(repo.root)._run("worktree", "add", "-q", "-b", "side", str(worktree))
    (worktree / "AGENTS.md").write_text("as it was\n", encoding="utf-8")

    with pytest.raises(RoundsError) as caught:
        private_files.bring(repo.root, worktree, ["AGENTS.md"], branch="side",
                            is_new_branch=True, earlier={sha("as it was\n")},
                            again="again")

    assert "is rounds' own earlier copy of" in caught.value.reason
    assert "removing it is safe" in caught.value.reason
    assert (worktree / "AGENTS.md").read_text(encoding="utf-8") == "as it was\n"


def test_earlier_copies_are_read_from_the_repos_commission_events():
    history = [
        {"type": "build.commissioned", "repo": "proj-a",
         "brought": [{"path": "AGENTS.md", "from": "~/x", "sha256": "aa"}]},
        {"type": "build.commissioned", "repo": "proj-b",
         "brought": [{"path": "AGENTS.md", "from": "~/y", "sha256": "bb"}]},
        {"type": "build.commissioned", "repo": "proj-a", "brought": "junk"},
        {"type": "build.commissioned", "repo": "proj-a"}]
    assert private_files.earlier_copies(history, "proj-a") == {"aa"}


def test_a_copy_that_fails_stops_naming_the_entry_and_a_rerun_brings(repo, opened,
                                                                    monkeypatch):
    def fail(*_args, **_kwargs):
        raise OSError(28, "No space left on device")

    with monkeypatch.context() as patch:
        patch.setattr(shutil, "copyfile", fail)
        with pytest.raises(RoundsError) as caught:
            commission()
    assert "the private file AGENTS.md can't be copied" in caught.value.reason

    made = commission()

    assert made.adopted
    assert (made.worktree / "AGENTS.md").read_text(encoding="utf-8") == SECRET


# -- the doctor --------------------------------------------------------------------------------

def rows() -> dict:
    return {row.name: row for row in doctor.rows()}


def test_the_doctor_names_each_entry_commission_would_refuse(repo, listing):
    repo.commit("TRACKED.md", "tracked")
    exclude(repo, "TRACKED.md", "GONE.md")
    (repo.root / "LOOSE.md").write_text("loose\n", encoding="utf-8")
    listing("AGENTS.md", "GONE.md", "TRACKED.md", "LOOSE.md")

    row = rows()["private files proj-a"]

    assert row.state == "warn"
    assert "AGENTS.md" not in row.detail
    assert "GONE.md is not at" in row.detail
    assert "TRACKED.md is tracked at HEAD" in row.detail
    assert "LOOSE.md is not ignored: add the line LOOSE.md to" in row.detail


def test_the_doctor_is_fine_with_a_listed_file_commission_would_bring(repo, listing):
    listing("AGENTS.md")

    row = rows()["private files proj-a"]

    assert row.state == "ok" and "private files" not in rows()


# -- a copy reads as ignored, and goes with the worktree while nobody changed it ------------

def event_of(path: Path) -> dict:
    [event] = commissioned(path)
    return event


def judge(event: dict):
    from moxi_rounds import removal
    from moxi_rounds import settings as own

    return removal.judge(ROUND, event, removal.WORKTREE, settings=own.load())


def test_a_copy_reads_as_ignored_to_every_reader_and_goes_with_the_worktree(
        repo, opened):
    from moxi_rounds import checking, removal
    from moxi_rounds import settings as own

    made = commission()
    tree = type(repo)(made.worktree)
    tree.commit("work.txt", "the work")
    tree.git("add", "-A")

    assert removal.changes(made.worktree) == ()
    assert checking.read_tip(made.worktree, "work").clean
    assert not Git(made.worktree).has_changes()
    assert tree.git("status", "--porcelain") == ""
    event = event_of(opened)
    assert isinstance(judge(event), removal.Proven)

    assert removal.remove(ROUND, event, removal.WORKTREE,
                          settings=own.load()) == made.worktree

    assert not made.worktree.exists()
    assert (repo.root / "AGENTS.md").read_text(encoding="utf-8") == SECRET


def test_an_edited_copy_keeps_the_worktree_at_b3_naming_it(repo, opened):
    made = commission()
    (made.worktree / "AGENTS.md").write_text("edited\n", encoding="utf-8")

    found = judge(event_of(opened))

    assert (found.check, found.work) == ("B3", ("AGENTS.md",))


def test_a_copy_equal_to_the_source_now_goes_though_its_hash_moved_on(repo, opened):
    from moxi_rounds import removal

    made = commission()
    (repo.root / "AGENTS.md").write_text("changed at the root\n", encoding="utf-8")
    (made.worktree / "AGENTS.md").write_text("changed at the root\n", encoding="utf-8")

    assert isinstance(judge(event_of(opened)), removal.Proven)


def test_a_copy_replaced_by_a_link_is_work(repo, opened):
    made = commission()
    copy = made.worktree / "AGENTS.md"
    copy.unlink()
    try:
        os.symlink(repo.root / "AGENTS.md", copy)
    except OSError:
        pytest.skip("this system makes no symlink here")

    assert judge(event_of(opened)).check == "B3"


def test_an_ignored_parent_holding_only_copies_and_globbed_paths_goes(repo, opened,
                                                                      listing):
    from moxi_rounds import removal

    exclude(repo, "notes/")
    (repo.root / "notes").mkdir()
    (repo.root / "notes" / "local.md").write_text("local\n", encoding="utf-8")
    listing("AGENTS.md", "notes/local.md")
    made = commission()
    (made.worktree / "notes" / ".venv").mkdir()
    (made.worktree / "notes" / ".venv" / "x").write_text("", encoding="utf-8")

    assert isinstance(judge(event_of(opened)), removal.Proven)

    (made.worktree / "notes" / "mine.md").write_text("mine\n", encoding="utf-8")
    found = judge(event_of(opened))
    assert (found.check, found.work) == ("B3", ("notes/",))


def test_a_drop_removes_the_worktree_of_a_build_with_an_unchanged_copy(repo, opened):
    from moxi_rounds import dropping

    made = commission()

    dropped = dropping.drop_build(ROUND, "B1", by="operator", record="comms:1234567",
                                  run_by="planner")

    assert made.worktree in dropped.removed and dropped.kept == ()
    assert (repo.root / "AGENTS.md").read_text(encoding="utf-8") == SECRET


def test_a_kept_worktree_may_go_once_its_copy_is_restored(repo, opened):
    from moxi_rounds import dropping, removal

    made = commission()
    (made.worktree / "AGENTS.md").write_text("edited\n", encoding="utf-8")
    dropped = dropping.drop_build(ROUND, "B1", by="operator", record="comms:1234567",
                                  run_by="planner")
    [kept] = dropped.kept
    assert (kept.check, kept.work) == ("B3", ("AGENTS.md",))

    # The sweep reads the same rule, again at each run.
    shutil.copyfile(repo.root / "AGENTS.md", made.worktree / "AGENTS.md")
    assert isinstance(judge(event_of(opened)), removal.Proven)

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
    # Bytes, so the file holds what `sha(SECRET)` hashes on every system's line endings.
    (git_repo.root / "AGENTS.md").write_bytes(SECRET.encode())
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


def test_the_block_names_each_copy_what_an_edit_does_and_how_to_let_it_go(repo, opened):
    made = commission()

    source = places.contract_home(repo.root / "AGENTS.md")
    line = (f"brought in, not in the repo: AGENTS.md, a copy of {source}: an edit "
            "stays here and keeps the worktree when the build ends; restore the file "
            "from its source, or delete it, then end the build")
    lines = list(made.block.lines)
    assert lines[lines.index(line) - 1] == (
        "         (logs and probes only: removed when the build ends)")


def test_a_block_with_nothing_brought_has_no_such_line(repo, opened, write_config):
    write_config("")

    made = commission()

    assert not any(one.startswith("brought in") for one in made.block.lines)


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
def test_a_rerun_stops_at_anything_else_at_the_place(repo, opened, monkeypatch, what):
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
    assert "commission stops instead of writing over it" in caught.value.reason
    assert caught.value.fix.startswith(f"look at {place}, remove it, then:")
    assert commissioned(opened) == []


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


# -- no private file reaches the target -------------------------------------------------------

@pytest.fixture
def building(repo, tmp_path, write_config, capsys):
    """`building(*entries)`: the round in `fix` with B1 commissioned on `work`, the repo
    declaring a check and listing `entries` (`AGENTS.md` when none), and one commit of
    work. Returns `(log, worktree repo)`."""
    import sys

    repo.git("config", "user.name", "coder")
    repo.git("config", "user.email", "coder@example.test")
    check = json.dumps(f'"{sys.executable}" -c "pass"')

    def make(*entries: str):
        configure(write_config, check, *(entries or ("AGENTS.md",)))
        opening.open_round(ROUND, description="a placeholder round", by="planner",
                           flow="fix")
        path = log.round_log(tmp_path / "notes" / "rounds" / "a-round")
        log.append(path, "stage.declared",
                   {"id": "B1", "repo": "proj-a", "branch": "work", "after": []},
                   by="planner")
        made = commission()
        tree = type(repo)(made.worktree)
        tree.commit("work.txt", "the work")
        capsys.readouterr()
        return path, tree

    make.check = check
    return make


def configure(write_config, check: str, *entries: str) -> None:
    write_config(f"[projects.proj-a]\ncheck = {{ command = {check} }}\n"
                 f"private_files = {json.dumps(list(entries))}\n")


def verify(path: Path, head: str) -> None:
    from moxi_rounds import verdicts

    report = path.parent / "build-report.md"
    report.write_text("# report\n\n```toml rounds\n" + "\n".join([
        'kind = "build"', 'verdict = "publishable"', 'pass = "full"', 'by = "critic"',
        'model = "a-model"', 'stage = "B1"', f'head = "{head}"']) + "\n```\n",
        encoding="utf-8")
    verdicts.record(ROUND, report, by="critic")


def finish_gates_now(path: Path):
    from moxi_rounds import checking, finish_gates
    from moxi_rounds import settings as own
    from moxi_rounds import state as states

    state = states.fold(log.read_round(path))
    seen = checking.seen_of(state, ROUND, settings=own.load())["B1"]
    return finish_gates.unmet(state, "B1", seen=seen, name=ROUND)


def refused_by_gate_11(path: Path, tree, repo, *said: str) -> str:
    """Assert that the check, `next`'s finish step and `finish` each refuse on gate 11
    with words holding each of `said`, that nothing was merged, and that the root's
    private files are as they were. Gives gate 11's reason."""
    from moxi_rounds import checking, finish_gates, finishing, steps
    from moxi_rounds import settings as own
    from moxi_rounds import state as states

    with pytest.raises(Refused) as caught:
        checking.check(ROUND, "B1", by="coder")
    reasons = [one.reason for one in caught.value.unmet]
    assert any(all(part in one for part in said) for one in reasons), reasons

    [gate] = [one for one in finish_gates_now(path) if one.number == finish_gates.PRIVATE]
    assert all(part in gate.unmet.reason for part in said), gate.unmet.reason
    state = states.fold(log.read_round(path))
    seen = checking.seen_of(state, ROUND, settings=own.load())
    step = steps.one(state, "finish", "B1", name=ROUND, seen=seen)
    assert finish_gates.PRIVATE in step.gates
    assert gate.unmet.reason in [one.reason for one in step.waits]

    verify(path, tree.rev())
    before = repo.rev("main")
    with pytest.raises(Refused) as caught:
        finishing.finish(ROUND, "B1", by="coder")
    assert gate.unmet.reason in [one.reason for one in caught.value.unmet]
    assert repo.rev("main") == before
    source = repo.root / "AGENTS.md"
    assert not source.is_symlink() and source.read_text(encoding="utf-8") == SECRET
    return gate.unmet.reason


def test_a_copy_un_ignored_and_added_is_refused(building, repo):
    path, tree = building()
    tree.root.joinpath(".gitignore").write_text("!AGENTS.md\n", encoding="utf-8")
    tree.git("add", "-A")
    tree.git("commit", "-q", "-m", "un-ignore it")

    reason = refused_by_gate_11(path, tree, repo, "AGENTS.md is a private file of "
                                "proj-a (brought by commission, or listed in "
                                "private_files), and commits finish would bring into "
                                f"main hold it: {tree.rev()[:12]}")
    assert "1 in all" not in reason


def test_a_copy_added_by_force_is_refused_with_the_fix(building, repo):
    path, tree = building()
    tree.git("add", "-f", "AGENTS.md")
    tree.git("commit", "-q", "-m", "add it")

    refused_by_gate_11(path, tree, repo, f"main hold it: {tree.rev()[:12]}")
    [gate] = [one for one in finish_gates_now(path) if one.number == 11]
    assert gate.unmet.fix == (
        "make a branch none of whose commits holds AGENTS.md. One way: git rm --cached "
        "-- AGENTS.md if the tip holds it, then git reset --soft $(git merge-base main "
        "HEAD), then git commit, which leaves one commit with the work. This rewrites "
        "the branch: its tip changes, so the build "
        "verification, the check and any report step read at the old tip are owed "
        "again; and a commit it shared with "
        "another build becomes a copy of it, so finish that build first. Then: moxi "
        f"rounds next {ROUND}")


def test_a_copy_added_then_untracked_is_refused_naming_the_first_commit(building, repo):
    path, tree = building()
    tree.git("add", "-f", "AGENTS.md")
    tree.git("commit", "-q", "-m", "add it")
    first = tree.rev()
    tree.git("rm", "-q", "--cached", "AGENTS.md")
    tree.git("commit", "-q", "-m", "untrack it")

    reason = refused_by_gate_11(path, tree, repo, f"main hold it: {first[:12]}")
    assert tree.rev()[:12] not in reason


def test_a_rename_onto_the_path_is_refused(building, repo):
    path, tree = building()
    tree.commit("x.md", "renamed")
    tree.git("mv", "-f", "x.md", "AGENTS.md")
    tree.git("commit", "-q", "-m", "rename it")

    refused_by_gate_11(path, tree, repo, f"main hold it: {tree.rev()[:12]}")


def test_a_case_only_twin_is_refused_where_the_repo_ignores_case(building, repo):
    repo.git("config", "core.ignorecase", "true")
    path, tree = building()
    (tree.root / "AGENTS.md").unlink()
    force_commit(tree, "agents.md")

    refused_by_gate_11(path, tree, repo, "AGENTS.md (as agents.md) is a private file")


def test_a_side_commit_a_merge_dropped_is_refused_naming_it(building, repo):
    path, tree = building()
    tree.git("switch", "-q", "-c", "side", "main")
    tree.git("add", "-f", "AGENTS.md")
    tree.git("commit", "-q", "-m", "add it on the side")
    side = tree.rev()
    tree.git("switch", "-q", "work")
    tree.git("merge", "-q", "--no-ff", "--no-commit", "side")
    tree.git("rm", "-q", "--cached", "AGENTS.md")
    tree.git("commit", "-q", "-m", "merge the side, less the file")
    assert "AGENTS.md" not in tree.git("ls-tree", "-r", "--name-only", "HEAD")

    refused_by_gate_11(path, tree, repo, f"main hold it: {side[:12]}")


def test_a_non_ascii_private_file_committed_is_refused(building, repo):
    exclude(repo, "é.md")
    (repo.root / "é.md").write_text("accent\n", encoding="utf-8")
    path, tree = building("AGENTS.md", "é.md")
    tree.git("add", "-f", "é.md")
    tree.git("commit", "-q", "-m", "add it")

    refused_by_gate_11(path, tree, repo, "é.md is a private file of proj-a")


def test_an_entry_listed_after_the_commission_counts(building, repo, write_config):
    path, tree = building()
    tree.commit("LATER.md", "later")
    assert not [one for one in finish_gates_now(path) if one.number == 11]
    configure(write_config, building.check, "AGENTS.md", "LATER.md")

    refused_by_gate_11(path, tree, repo, "LATER.md is a private file of proj-a")


def test_a_target_that_holds_the_path_is_named(building, repo):
    path, tree = building()
    repo.git("add", "-f", "AGENTS.md")
    repo.git("commit", "-q", "-m", "the target takes it")
    tree.git("merge", "-q", "--no-ff", "--no-edit", "main")

    refused_by_gate_11(path, tree, repo, "and main holds it, so every commit finish "
                       "would bring holds it")
    [gate] = [one for one in finish_gates_now(path) if one.number == 11]
    assert gate.unmet.fix == (
        "take AGENTS.md out of private_files in [projects.proj-a], or out of main. "
        f"Then: moxi rounds next {ROUND}")


def test_after_the_fixs_squash_the_gate_holds_and_the_tip_reads_are_owed_again(
        building, repo):
    from moxi_rounds import finish_gates

    path, tree = building()
    tree.git("add", "-f", "AGENTS.md")
    tree.git("commit", "-q", "-m", "add it")
    verify(path, tree.rev())

    tree.git("rm", "-q", "--cached", "--", "AGENTS.md")
    base = tree.git("merge-base", "main", "HEAD")
    tree.git("reset", "-q", "--soft", base)
    tree.git("commit", "-q", "-m", "the work")

    numbers = [one.number for one in finish_gates_now(path)]
    assert finish_gates.PRIVATE not in numbers
    assert finish_gates.VERIFIED in numbers and finish_gates.CHECKED in numbers
    assert (tree.root / "AGENTS.md").read_text(encoding="utf-8") == SECRET


def test_the_lock_reads_the_gate_against_the_targets_head_then(building, repo,
                                                               monkeypatch):
    from moxi_rounds import checking, finish_lock, finishing

    path, tree = building()
    base = repo.rev("main")
    repo.git("add", "-f", "AGENTS.md")
    repo.git("commit", "-q", "-m", "the target takes it")
    held = repo.rev()
    repo.git("rm", "-q", "--cached", "AGENTS.md")
    repo.git("commit", "-q", "-m", "and lets it go")
    tree.git("merge", "-q", "--no-ff", "--no-edit", "main")
    checking.check(ROUND, "B1", by="coder")
    verify(path, tree.rev())
    assert not [one for one in finish_gates_now(path) if one.number == 11]
    take = finish_lock.take

    def moved_then_take(*args, **kwargs):
        # The target moves back between the first read and the lock.
        repo.git("update-ref", "refs/heads/main", base)
        return take(*args, **kwargs)

    monkeypatch.setattr(finish_lock, "take", moved_then_take)
    with pytest.raises(Refused) as caught:
        finishing.finish(ROUND, "B1", by="coder")

    [reason] = [one.reason for one in caught.value.unmet if "private file" in one.reason]
    assert held[:12] in reason
    assert repo.rev("main") == base


def test_the_lock_reads_gate_11_and_the_gates_number_eleven():
    from moxi_rounds import finish_gates, finishing

    assert finish_gates.PRIVATE in finishing.UNDER_LOCK
    assert sorted(finish_gates.GATES) == list(range(1, 12))
    assert finish_gates.GATES[finish_gates.PRIVATE] == "private files"


def test_a_file_where_the_entrys_parent_would_be_is_refused(building, repo):
    exclude(repo, "notes/local.md")
    (repo.root / "notes").mkdir()
    (repo.root / "notes" / "local.md").write_text("local\n", encoding="utf-8")
    path, tree = building("AGENTS.md", "notes/local.md")
    shutil.rmtree(tree.root / "notes")
    tree.commit("notes", "a file")

    refused_by_gate_11(path, tree, repo, "notes/local.md (as notes) is a private file")
    assert (repo.root / "notes" / "local.md").read_text(encoding="utf-8") == "local\n"


def test_a_path_under_the_entry_is_refused(building, repo):
    path, tree = building()
    (tree.root / "AGENTS.md").unlink()
    force_commit(tree, "AGENTS.md/x")

    refused_by_gate_11(path, tree, repo, "AGENTS.md is a private file", tree.rev()[:12])
    assert (repo.root / "AGENTS.md").is_file()


def test_a_tracked_sibling_of_an_entry_changed_on_the_branch_passes(building, repo):
    from moxi_rounds import checking

    repo.commit("notes/readme.md", "tracked")
    exclude(repo, "notes/local.md")
    (repo.root / "notes" / "local.md").write_text("local\n", encoding="utf-8")
    path, tree = building("AGENTS.md", "notes/local.md")
    tree.commit("notes/readme.md", "changed")

    assert not [one for one in finish_gates_now(path) if one.number == 11]
    checking.check(ROUND, "B1", by="coder")


def test_the_read_walks_only_the_trees_on_the_private_paths(git_repo):
    """A branch of 300 commits over 3,000 files under 60 subtrees: one root tree per commit
    and the one `notes` tree they share are all that is read."""
    dirs_n, files_n, commits_n = 60, 50, 300
    lines = ["blob", "mark :1", "data 2", "x", ""]
    lines += ["commit refs/heads/big", "mark :2", "committer c <c@e.t> 1700000000 +0000",
              "data 4", "base", "from refs/heads/main"]
    lines += [f"M 100644 :1 d{one}/f{two}" for one in range(dirs_n)
              for two in range(files_n)]
    lines += ["M 100644 :1 notes/readme.md", ""]
    for at in range(commits_n):
        lines += [f"blob", f"mark :{at + 10_000}", f"data {len(str(at)) + 1}", str(at), "",
                  "commit refs/heads/big", f"committer c <c@e.t> {1700000001 + at} +0000",
                  "data 1", "c",
                  f"M 100644 :{at + 10_000} d{at % dirs_n}/f{at % files_n}", ""]
    stream = ("\n".join(lines) + "\n").encode()
    import subprocess

    subprocess.run(["git", "-C", str(git_repo.root), "fast-import", "--quiet"],
                   input=stream, env=git_repo.env(), check=True)
    git = Git(git_repo.root)
    tip, head = git_repo.rev("big"), git_repo.rev("main")

    with private_files.Trees(git_repo.root) as trees:
        found = private_files.held(git, ["AGENTS.md", "notes/local.md"], tip, head,
                                   trees=trees)

    assert found == []
    assert trees.read == commits_n + 1 + 1

"""The drift rule: whether the target's move under a build since its verdict owes one more
re-check, read on built histories, and its line in `show` and the round note."""

from __future__ import annotations

import pytest

from moxi_rounds import builds, drift, log, opening, showing, state, verdicts
from moxi_rounds.gitreads import Git

ROUND = "proj-a/a-round"


@pytest.fixture
def built(git_repo, tmp_path, capsys):
    """`proj-a` rooted at the test's git repo, its round in the `fix` flow, and B1 on the
    branch `work` commissioned to `coder` with `work.txt` and a change to `README` made
    on it. Returns `(round folder, the repo, the worktree's repo)`."""
    from moxi import cli
    from moxi_rounds import commissioning

    assert cli.main(["projects", "add", "proj-a", "--root", str(git_repo.root)]) == 0
    assert cli.main(["projects", "set", "proj-a", "--notes-root",
                     str(tmp_path / "notes")]) == 0
    opening.open_round(ROUND, description="a placeholder round", by="planner",
                       flow="fix")
    round_dir = tmp_path / "notes" / "rounds" / "a-round"
    log.append(log.round_log(round_dir), "stage.declared",
               {"id": "B1", "repo": "proj-a", "branch": "work", "after": []},
               by="planner")
    made = commissioning.commission(ROUND, "B1", builder="coder", by="planner")
    tree = type(git_repo)(made.worktree)
    tree.commit("work.txt", "the work")
    tree.commit("README", "a placeholder repo, built on")
    capsys.readouterr()
    return round_dir, git_repo, tree


def verify(round_dir, head, *, name="build-report.md"):
    """Record a publishable report of B1 read at `head`, written as `name`."""
    path = round_dir / name
    path.write_text(f"# {name}\n\n```toml rounds\n" + "\n".join([
        'kind = "build"', 'verdict = "publishable"', 'pass = "full"', 'by = "critic"',
        'model = "a-model"', 'stage = "B1"', f'head = "{head}"']) + "\n```\n",
        encoding="utf-8")
    return verdicts.record(ROUND, path, by="critic")


def drift_now(round_dir, repo) -> drift.Drift:
    """B1's drift at its tip as git reads it now."""
    from moxi_rounds import open_builds

    round_state = state.fold(log.read(log.round_log(round_dir)))
    [build] = builds.builds(round_state)
    now = open_builds.of_round(ROUND, ["proj-a"])["B1"]
    read = drift.of_build(round_state, build, now.status, Git(repo.root),
                          settings=_settings())
    assert read is not None
    return read


def _settings():
    from moxi_rounds import settings

    return settings.load()


def test_a_target_that_has_not_moved_reads_nothing_more(built):
    round_dir, repo, tree = built
    verify(round_dir, tree.rev())

    read = drift_now(round_dir, repo)

    assert (read.rule, read.owed, read.moved) == (drift.NOT_MOVED, False, 0)
    assert "target has not moved under B1" in showing.as_text(showing.show(ROUND))


def test_a_target_that_moved_apart_from_the_build_owes_no_recheck(built):
    round_dir, repo, tree = built
    verify(round_dir, tree.rev())
    repo.commit("other.txt")
    repo.commit("more.txt")

    read = drift_now(round_dir, repo)

    assert (read.rule, read.owed, read.moved, read.meeting) == (drift.APART, False, 2, ())
    assert "target moved 2 commits under B1; 0 paths meet\n" in showing.as_text(
        showing.show(ROUND))


def test_a_target_that_changed_a_path_of_the_build_owes_a_recheck(built):
    round_dir, repo, tree = built
    verdict_base = repo.rev("main")
    verify(round_dir, tree.rev())
    repo.commit("README", "a placeholder repo, changed on main")
    moved_to = repo.rev("main")

    read = drift_now(round_dir, repo)

    assert (read.rule, read.owed, read.meeting) == (drift.OWED, True, ("README",))
    assert read.ranges == {"target": f"{verdict_base}..{moved_to}",
                           "build": f"{verdict_base}..{tree.rev()}"}
    assert ("target moved 1 commit under B1; 1 path meets: re-check owed"
            in showing.as_text(showing.show(ROUND)))


def test_a_fix_made_after_the_verdict_is_among_the_build_s_paths(built):
    round_dir, repo, tree = built
    verify(round_dir, tree.rev())
    tree.commit("fix.txt", "a fix after the verdict")
    repo.commit("fix.txt", "the same name changed on main")

    read = drift_now(round_dir, repo)

    assert read.owed and read.meeting == ("fix.txt",)


def test_a_file_the_target_renamed_meets_the_build_under_its_old_name(built):
    round_dir, repo, tree = built
    verify(round_dir, tree.rev())
    repo.git("mv", "README", "README.md")
    repo.git("commit", "-q", "-m", "rename the readme")

    read = drift_now(round_dir, repo)

    assert read.owed and read.meeting == ("README",)


@pytest.mark.parametrize("path", ["uv.lock", "packages/a-package/package-lock.json"])
def test_a_lockfile_the_target_changed_owes_a_recheck_where_no_path_meets(built, path):
    round_dir, repo, tree = built
    verify(round_dir, tree.rev())
    repo.commit(path, "a lock")

    read = drift_now(round_dir, repo)

    assert (read.owed, read.meeting, read.shared) == (True, (), (path,))
    assert ("target moved 1 commit under B1; 0 paths meet, 1 shared path changed: "
            "re-check owed" in showing.as_text(showing.show(ROUND)))


def test_a_repo_s_own_shared_paths_add_to_the_lockfiles(built, write_config):
    round_dir, repo, tree = built
    write_config("""
        [projects.proj-a]
        shared_paths = ["schema/*.sql"]
    """)
    verify(round_dir, tree.rev())
    repo.commit("schema/tables.sql", "a table")
    repo.commit("deeper/schema/tables.sql", "not under the glob")

    read = drift_now(round_dir, repo)

    assert read.shared == ("schema/tables.sql",)
    assert drift.shared_globs(_settings(), "proj-a")[-1] == "schema/*.sql"


def test_the_target_merged_into_the_branch_apart_from_the_build_counts_as_verified(built):
    round_dir, repo, tree = built
    read_at = tree.rev()
    verify(round_dir, read_at)
    repo.commit("other.txt")
    merge = tree.merge("main")

    read = drift_now(round_dir, repo)

    assert (read.rule, read.owed, read.merged_in) == (drift.MERGED_IN, False, True)
    assert set(read.covered) == {merge, repo.rev("main")}
    assert "1 commit under B1; 0 paths meet, merged into the branch\n" in (
        showing.as_text(showing.show(ROUND)))


def test_a_commit_after_the_verdict_beside_a_merge_of_the_target_owes_a_recheck(built):
    round_dir, repo, tree = built
    verify(round_dir, tree.rev())
    repo.commit("other.txt")
    tree.merge("main")
    fix = tree.commit("fix.txt", "a fix after the verdict")

    read = drift_now(round_dir, repo)

    assert (read.rule, read.owed, read.covered) == (drift.OWED, True, ())
    assert f"is no merge of the target ({fix[:12]})" in read.why
    assert (f"target moved 1 commit under B1; 0 paths meet, merged into the branch: "
            f"re-check owed: {read.why}\n") in showing.as_text(showing.show(ROUND))


def test_a_merge_of_the_target_that_changes_a_shared_path_owes_a_recheck(built):
    round_dir, repo, tree = built
    verify(round_dir, tree.rev())
    repo.commit("uv.lock", "a lock")
    tree.merge("main")

    read = drift_now(round_dir, repo)

    assert read.owed and read.merged_in and read.shared == ("uv.lock",)
    assert "the merge changes 1 path of the build or shared (uv.lock)" in read.why


def test_the_target_moved_again_after_its_merge_into_the_branch_is_still_read(built):
    round_dir, repo, tree = built
    verify(round_dir, tree.rev())
    repo.commit("other.txt")
    tree.merge("main")
    repo.commit("README", "a placeholder repo, changed on main")

    read = drift_now(round_dir, repo)

    assert read.owed and read.meeting == ("README",)


def test_a_recheck_at_a_tip_that_holds_the_target_reads_as_not_moved(built):
    round_dir, repo, tree = built
    verify(round_dir, tree.rev())
    repo.commit("README", "a placeholder repo, changed on main")
    tree.git("merge", "-q", "--no-ff", "--no-edit", "-X", "theirs", "main")
    verify(round_dir, tree.rev(), name="recheck.md")

    read = drift_now(round_dir, repo)

    assert (read.rule, read.owed) == (drift.NOT_MOVED, False)


def test_a_verdict_with_no_single_merge_base_owes_the_recheck(git_repo):
    head = git_repo.rev()

    read = drift.read(Git(git_repo.root), head=head, merge_base=None, tip=head,
                      target_before=head, globs=drift.LOCKFILES)

    assert read.owed and "no single merge-base" in read.why
    assert drift.line("B1", read.data()).endswith(": re-check owed")


def test_a_verdict_head_git_does_not_hold_owes_the_recheck(git_repo):
    first = git_repo.rev()
    moved = git_repo.commit("other.txt")

    read = drift.read(Git(git_repo.root), head="e" * 40, merge_base=first, tip=moved,
                      target_before=moved, globs=())

    assert read.owed and f"does not hold {'e' * 12}" in read.why


@pytest.mark.parametrize("path, glob, shared", [
    ("uv.lock", "uv.lock", True),
    ("packages/a-package/uv.lock", "uv.lock", True),
    ("uv.lock.bak", "uv.lock", False),
    ("schema/tables.sql", "schema/*.sql", True),
    ("deeper/schema/tables.sql", "schema/*.sql", False),
    ("locks/a.lock", "*.lock", True),
])
def test_a_glob_without_a_slash_matches_a_path_s_last_part(path, glob, shared):
    assert drift.is_shared(path, [glob]) is shared


def test_show_s_data_and_the_round_note_carry_the_drift(built):
    round_dir, repo, tree = built
    verify(round_dir, tree.rev())
    repo.commit("README", "a placeholder repo, changed on main")
    # Any write to the round writes its note again with what git reads then.
    verify(round_dir, tree.rev(), name="second.md")

    [row] = showing.data(showing.show(ROUND))["builds"]
    note = (round_dir / "round-note.md").read_text(encoding="utf-8")

    assert row["drift"]["owed"] is True
    assert row["drift"]["meeting"] == ["README"]
    assert "target moved 1 commit under B1; 1 path meets: re-check owed" in note


def test_a_build_with_no_verdict_shows_no_drift(built):
    _round_dir, repo, _tree = built
    repo.commit("README", "a placeholder repo, changed on main")

    [row] = showing.data(showing.show(ROUND))["builds"]

    assert row["drift"] is None
    assert "under B1" not in showing.as_text(showing.show(ROUND))



def test_next_names_the_drift_pass_and_both_ranges_once_the_target_is_merged_in(built):
    from moxi_rounds import next_step

    round_dir, repo, tree = built
    verify(round_dir, tree.rev())
    repo.commit("README", "a placeholder repo, changed on main")
    tree.git("merge", "-q", "--no-ff", "--no-commit", "-X", "ours", "main")
    (tree.root / "README").write_text("both\n", encoding="utf-8")
    tree.git("add", "README")
    tree.git("commit", "-q", "--no-edit")
    read = drift_now(round_dir, repo)

    said = next_step.read(ROUND)
    [group] = said["groups"]
    text = next_step.as_text(said)

    assert (group["step"], group["pass"], group["ranges"]) == (
        "verify-build", "drift", read.ranges)
    assert f"pass:  drift: {read.why}" in text
    assert (f"reads: the target's change {read.ranges['target']}; the build's diff "
            f"{read.ranges['build']}") in text


def test_next_names_no_drift_pass_while_the_target_has_not_moved(built):
    from moxi_rounds import next_step

    round_dir, _repo, tree = built
    verify(round_dir, tree.rev())
    tree.commit("fix.txt", "a fix after the verdict")

    [group] = next_step.read(ROUND)["groups"]

    assert group["step"] == "verify-build" and "pass" not in group

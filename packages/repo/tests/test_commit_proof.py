"""The commit-proof piece: the line, where it is read, what passes without one and what
is refused.

The reading and the check are run first as functions, then through real git commits,
where the piece reads its salt through the runner from the repo's git config.
"""

from __future__ import annotations

import hashlib
import re

from conftest import POSIX_ONLY, change, commit, git, head

from moxi_repo import api, cli, commit_proof, gitconf, settings
from moxi_repo.commit_proof import check_message, proof_line, proof_value, read_lines

pytestmark = POSIX_ONLY

PIECE = "repo/commit-proof"
TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


def run(capsys, *argv: str) -> tuple[int, str, str]:
    code = cli.main(list(argv))
    out, err = capsys.readouterr()
    return code, out, err


def write_config(text: str) -> None:
    from moxi import paths

    path = paths.Layout("repo").config_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def with_piece(demo, name: str = "alpha-notes"):
    repo = demo.repo(name)
    api.add(repo, [PIECE])
    return repo


def salt_of(repo) -> str:
    return gitconf.read(gitconf.resolve(repo)).pieces[PIECE]["salt"][-1]


def line_for(repo) -> str:
    """The line `moxi repo rules` prints for what is staged in `repo` now."""
    return api.rules(repo)[-1]


def message(*trailers: str, subject: str = "Add a note") -> str:
    return subject + "\n\nWhy it was needed.\n\n" + "\n".join(trailers) + "\n"


def refused(done) -> str:
    assert done.returncode != 0, done.stdout
    assert f"commit-msg refused by {PIECE}: " in done.stderr, done.stderr
    return done.stderr


# -- the line and the message, as functions -----------------------------------------------

def test_the_value_is_twelve_hex_characters_of_a_hash_over_the_salt_and_the_tree():
    want = hashlib.sha256(b"commit-check\x000123456789abcdef\x00" + TREE.encode()).hexdigest()
    assert proof_value("0123456789abcdef", TREE) == want[:12]
    assert proof_line("0123456789abcdef", TREE) == f"Commit-Check: {want[:12]}"
    assert proof_value("fedcba9876543210", TREE) != want[:12]
    assert proof_value("0123456789abcdef", TREE.replace("4", "5")) != want[:12]


def test_a_new_salt_is_sixteen_random_hex_characters():
    one, two = commit_proof.new_salt(), commit_proof.new_salt()
    assert re.fullmatch(r"[0-9a-f]{16}", one) and re.fullmatch(r"[0-9a-f]{16}", two)
    assert one != two


def test_the_line_is_read_from_every_trailer_paragraph_after_the_subject():
    assert read_lines("Fix it\n\nCommit-Check: aaaaaaaaaaaa\n") == ([(3, "aaaaaaaaaaaa")], [])
    # Among other trailers, in an earlier trailer paragraph, and with a continuation line.
    text = ("Fix it\n\nBody.\n\nCommit-Check: aaaaaaaaaaaa\nAcked-by: alice\n\n"
            "Signed-off-by: bob <bob@example.invalid>\n  and a second line\n"
            "commit-check: bbbbbbbbbbbb\n")
    assert read_lines(text) == ([(5, "aaaaaaaaaaaa"), (10, "bbbbbbbbbbbb")], [])


def test_a_line_beside_prose_or_in_the_subject_paragraph_is_not_read():
    assert read_lines("Fix it\nCommit-Check: aaaaaaaaaaaa\n") == ([], [2])
    assert read_lines("Fix it\n\nThe old line was\nCommit-Check: aaaaaaaaaaaa\n") == ([], [4])
    assert read_lines("Fix it\n\n  indented first\nCommit-Check: aaaaaaaaaaaa\n") == ([], [4])
    # Comment lines and what is under the scissors line are not the message.
    text = ("Fix it\n\n# Commit-Check: aaaaaaaaaaaa\nCommit-Check: bbbbbbbbbbbb\n"
            "# ------------------------ >8 ------------------------\n"
            "\nCommit-Check: cccccccccccc\n")
    assert read_lines(text) == ([(4, "bbbbbbbbbbbb")], [])


def test_each_refusal_names_the_rules_command_and_the_work_tree():
    top = "/srv/proj-a"
    want = "aaaaaaaaaaaa"
    assert check_message(f"Fix it\n\nCommit-Check: {want}\n", want, top) == []
    for text, said in (
            ("Fix it\n", "the message has no Commit-Check line"),
            (f"Fix it\n\nCommit-Check: {want}\n\nCommit-Check: {want}\n",
             "the message has 2 Commit-Check lines (lines 3, 5)"),
            ("Fix it\n\nCommit-Check: not-the-line\n", "line 3 is not a Commit-Check line"),
            ("Fix it\n\nCommit-Check: AAAAAAAAAAAA\n", "line 3 is not a Commit-Check line"),
            ("Fix it\n\nCommit-Check: aaaaaaaaaaaaa\n", "line 3 is not a Commit-Check line"),
            ("Fix it\n\nCommit-Check: bbbbbbbbbbbb\n",
             "line 3 doesn't match the tree being committed in /srv/proj-a")):
        out = check_message(text, want, top)
        assert len(out) == 1 and said in out[0], (text, out)
        assert "moxi repo rules /srv/proj-a" in out[0], out
    # A line that is there and not read is pointed at.
    out = check_message(f"Fix it\n\nSee the line\nCommit-Check: {want}\n", want, top)
    assert len(out) == 2 and "line 4 is not read" in out[1]
    # A work tree whose path needs quoting is quoted in the command.
    assert "moxi repo rules '/srv/proj a'" in check_message("Fix it\n", want, "/srv/proj a")[0]


# -- registering --------------------------------------------------------------------------

def test_it_is_declared_on_commit_msg_fail_closed_after_the_rules_piece(demo, capsys):
    found = {p["id"]: p for p in api.available()["pieces"]}
    assert (found[PIECE]["event"], found[PIECE]["fail"], found[PIECE]["order"]) == (
        "commit-msg", "closed", 20)
    assert found[PIECE]["order"] > found["repo/commit-rules"]["order"]
    assert found[PIECE]["settings"] == {"salt": "one"}
    # The rules piece keeps the rule setting to itself, so --set rule names one piece.
    repo = demo.repo()
    assert run(capsys, "git-hooks", "add", str(repo), PIECE, "repo/commit-rules",
               "--set", "rule=a rule")[0] == 0
    assert "rule" not in gitconf.read(gitconf.resolve(repo)).pieces[PIECE]


def test_the_salt_is_written_when_the_piece_is_first_registered_and_then_kept(demo, capsys):
    alpha, beta = with_piece(demo), with_piece(demo, "beta-code")
    first = salt_of(alpha)
    assert re.fullmatch(r"[0-9a-f]{16}", first)
    assert salt_of(beta) != first
    stored = git(alpha, "config", "--local", "--get-all", f"moxi-repo.piece.{PIECE}.salt")
    assert stored.stdout.splitlines() == [first]
    for argv in (("add", str(alpha), PIECE), ("refresh", str(alpha)), ("refresh",),
                 ("disable", str(alpha), PIECE), ("enable", str(alpha), PIECE)):
        assert run(capsys, "git-hooks", *argv)[0] == 0
        assert salt_of(alpha) == first, argv
    # Removing the piece takes its salt with it; adding it again makes a new one.
    assert run(capsys, "git-hooks", "remove", str(alpha), PIECE)[0] == 0
    assert run(capsys, "git-hooks", "add", str(alpha), PIECE)[0] == 0
    assert re.fullmatch(r"[0-9a-f]{16}", salt_of(alpha)) and salt_of(alpha) != first
    # A salt given with --set is the one kept.
    assert run(capsys, "git-hooks", "add", str(beta), PIECE,
               "--set", "salt=0123456789abcdef")[0] == 0
    assert salt_of(beta) == "0123456789abcdef"


def test_the_config_key_registers_it_in_each_repo_add_and_refresh_touch(demo, capsys):
    alpha, beta, off = demo.repo("alpha-notes"), demo.repo("beta-code"), demo.repo("gamma")
    outside = demo.repo("outside")
    assert run(capsys, "git-hooks", "add", str(alpha), "demo/gate")[0] == 0
    assert run(capsys, "git-hooks", "add", str(off), PIECE)[0] == 0
    assert run(capsys, "git-hooks", "disable", str(off), PIECE)[0] == 0
    kept = salt_of(off)
    assert settings.load()["commit_proof.everywhere"] is False
    write_config("[commit_proof]\neverywhere = true\n")
    code, out, _ = run(capsys, "git-hooks", "refresh")
    assert code == 0
    assert f"added {PIECE} (commit-msg, closed) in {alpha} (commit_proof.everywhere)" in out
    assert re.fullmatch(r"[0-9a-f]{16}", salt_of(alpha))
    # The rules piece has its own key, which is off.
    assert "repo/commit-rules" not in gitconf.read(gitconf.resolve(alpha)).pieces
    # A disabled registration is left alone, and a repo outside the index never gets it.
    section = gitconf.read(gitconf.resolve(off)).pieces[PIECE]
    assert section["enabled"] == ["false"] and section["salt"] == [kept]
    assert str(off) + " (commit_proof" not in out
    assert PIECE not in gitconf.read(gitconf.resolve(outside)).pieces
    # add touches a repo too.
    code, out, _ = run(capsys, "git-hooks", "add", str(beta))
    assert code == 0 and f"in {beta} (commit_proof.everywhere)" in out
    change(beta)
    assert "no Commit-Check line" in refused(commit(beta, message="Add a note"))
    # Both keys on: both pieces, the rules piece first.
    write_config("[commit_proof]\neverywhere = true\n[commit_rules]\neverywhere = true\n")
    code, out, _ = run(capsys, "git-hooks", "add", str(outside))
    assert code == 0
    assert [line.split()[1] for line in out.splitlines() if line.startswith("added")] == [
        "repo/commit-rules", PIECE]


# -- through real commits -----------------------------------------------------------------

def test_a_commit_with_the_line_for_the_staged_tree_passes(demo):
    repo = with_piece(demo)
    change(repo)
    line = line_for(repo)
    tree = git(repo, "write-tree").stdout.strip()
    assert line == proof_line(salt_of(repo), tree)
    before = head(repo)
    done = commit(repo, message=message(line))
    assert done.returncode == 0, done.stderr
    assert head(repo) != before
    assert git(repo, "rev-parse", "HEAD^{tree}").stdout.strip() == tree
    # Beside other trailers, and in a trailer paragraph that is not the last.
    change(repo)
    done = commit(repo, message=message("Acked-by: alice", line_for(repo) + "\n",
                                        "Signed-off-by: bob <bob@example.invalid>"))
    assert done.returncode == 0, done.stderr


def test_a_missing_line_is_refused_naming_the_command_and_the_work_tree(demo):
    repo = with_piece(demo)
    change(repo)
    before = head(repo)
    said = refused(commit(repo, message="Add a note"))
    assert "the message has no Commit-Check line" in said
    assert f"moxi repo rules {repo}" in said
    assert head(repo) == before
    # A line quoted in the body's prose is not the line.
    said = refused(commit(repo, message=f"Add a note\n\nThe line was\n{line_for(repo)}\n"))
    assert "the message has no Commit-Check line" in said and "line 4 is not read" in said


def test_a_stale_line_is_refused(demo):
    repo = with_piece(demo)
    change(repo)
    line = line_for(repo)
    change(repo, "second.md")
    said = refused(commit(repo, message=message(line)))
    assert f"line 5 doesn't match the tree being committed in {repo}" in said
    assert f"moxi repo rules {repo}" in said
    # The line of the commit before is stale too.
    assert commit(repo, message=message(line_for(repo))).returncode == 0
    old = line_for(repo)
    change(repo, "third.md")
    assert "doesn't match" in refused(commit(repo, message=message(old)))


def test_commit_all_over_unstaged_changes_is_refused_as_stale(demo):
    repo = with_piece(demo)
    change(repo)
    line = line_for(repo)
    (repo / "README.md").write_text("changed and not staged\n", encoding="utf-8")
    assert "doesn't match" in refused(commit(repo, "-a", message=message(line)))
    # With everything staged, -a commits the tree the line was printed for.
    git(repo, "add", "README.md")
    done = commit(repo, "-a", message=message(line_for(repo)))
    assert done.returncode == 0, done.stderr


def test_a_commit_of_one_path_is_judged_by_the_tree_that_lands(demo, tmp_path):
    repo = with_piece(demo)
    change(repo, "one.md")
    change(repo, "two.md")
    whole = line_for(repo)
    # The line for the whole index is not the line for a commit of one path.
    assert "doesn't match" in refused(commit(repo, "one.md", message=message(whole)))
    # The tree `git commit one.md` makes: HEAD and that path, built in an index apart.
    apart = {"GIT_INDEX_FILE": str(tmp_path / "apart-index")}
    git(repo, "read-tree", "HEAD", env=apart)
    git(repo, "add", "one.md", env=apart)
    tree = git(repo, "write-tree", env=apart).stdout.strip()
    done = commit(repo, "one.md", message=message(proof_line(salt_of(repo), tree)))
    assert done.returncode == 0, done.stderr
    assert git(repo, "rev-parse", "HEAD^{tree}").stdout.strip() == tree
    # The other change is still staged, and the whole index's line now commits it.
    assert git(repo, "diff", "--cached", "--name-only").stdout.split() == ["two.md"]
    assert line_for(repo) == whole
    assert commit(repo, message=message(whole)).returncode == 0


def test_two_lines_are_refused_even_when_both_match(demo):
    repo = with_piece(demo)
    change(repo)
    line = line_for(repo)
    said = refused(commit(repo, message=message(line, line)))
    assert "the message has 2 Commit-Check lines (lines 5, 6)" in said
    assert f"moxi repo rules {repo}" in said
    said = refused(commit(repo, message=message(line + "\n", "Commit-Check: 000000000000")))
    assert "the message has 2 Commit-Check lines (lines 5, 7)" in said


def test_another_repos_line_for_the_same_tree_is_refused(demo):
    alpha, beta = with_piece(demo), with_piece(demo, "beta-code")
    change(alpha)
    change(beta)
    assert git(alpha, "write-tree").stdout == git(beta, "write-tree").stdout
    assert line_for(alpha) != line_for(beta)
    said = refused(commit(beta, message=message(line_for(alpha))))
    assert f"doesn't match the tree being committed in {beta}" in said
    assert "another repo's" in said
    assert commit(beta, message=message(line_for(beta))).returncode == 0


def test_a_malformed_line_is_refused(demo):
    repo = with_piece(demo)
    change(repo)
    value = line_for(repo).split()[1]
    for bad in (value[:11], value + "0", value.upper() if value != value.upper() else "X",
                "see above", f"{value} and more"):
        said = refused(commit(repo, message=message(f"Commit-Check: {bad}")))
        assert "line 5 is not a Commit-Check line" in said, bad
        assert f"moxi repo rules {repo}" in said


def test_a_repo_whose_salt_is_gone_is_refused_with_the_way_back(demo, capsys):
    repo = with_piece(demo)
    change(repo)
    line = line_for(repo)
    git(repo, "config", "--unset-all", f"moxi-repo.piece.{PIECE}.salt")
    said = refused(commit(repo, message=message(line)))
    assert f"{PIECE} has no salt in this repo" in said
    assert f"moxi repo git-hooks remove {repo} {PIECE}" in said
    assert run(capsys, "rules", str(repo)) == (0, "\n".join([
        f"commit rules for {repo}",
        f"no Commit-Check line: {PIECE} has no salt in this repo; remove the piece and add "
        f"it again: moxi repo git-hooks remove {repo} {PIECE}, then "
        f"moxi repo git-hooks add {repo} {PIECE}"]) + "\n", "")


# -- what passes without a line -----------------------------------------------------------

def test_the_committers_own_commits_pass_without_a_line(demo):
    repo = with_piece(demo)
    change(repo)
    done = commit(repo, message="Add a note", env={"MOXI_REPO_COMMITTER": "notes/save"})
    assert done.returncode == 0, done.stderr


def _diverge(repo) -> None:
    """`side` and `main` each change the first line of README.md, so replaying or merging
    one onto the other conflicts."""
    git(repo, "checkout", "-q", "-b", "side")
    (repo / "README.md").write_text("from side\n", encoding="utf-8")
    git(repo, "commit", "-q", "-am", "Change it on side", env={"MOXI_REPO_COMMITTER": "x/y"})
    git(repo, "checkout", "-q", "main")
    (repo / "README.md").write_text("from main\n", encoding="utf-8")
    git(repo, "commit", "-q", "-am", "Change it on main", env={"MOXI_REPO_COMMITTER": "x/y"})


def _resolve(repo) -> None:
    (repo / "README.md").write_text("from both\n", encoding="utf-8")
    git(repo, "add", "README.md")


def test_a_cherry_pick_and_a_revert_in_progress_pass_without_a_line(demo):
    repo = with_piece(demo)
    api.add(repo, ["demo/msg"])
    _diverge(repo)
    assert git(repo, "cherry-pick", "side", check=False).returncode != 0
    _resolve(repo)
    demo.runs()
    done = git(repo, "-c", "core.editor=true", "commit", "-q", check=False)
    assert done.returncode == 0, done.stderr
    # The commit-msg event did run: the piece passed it, nothing skipped it.
    assert "demo/msg" in demo.ids()
    assert git(repo, "revert", "--no-commit", "HEAD").returncode == 0
    done = commit(repo, message="Take it back")
    assert done.returncode == 0, done.stderr
    # With nothing in progress the same commit needs its line again.
    change(repo)
    assert "no Commit-Check line" in refused(commit(repo, message="Add a note"))


def test_a_rebase_in_progress_passes_without_a_line(demo):
    repo = with_piece(demo)
    api.add(repo, ["demo/msg"])
    _diverge(repo)
    git(repo, "checkout", "-q", "side")
    assert git(repo, "rebase", "main", check=False).returncode != 0
    _resolve(repo)
    demo.runs()
    # The stopped pick is committed by hand, which runs commit-msg, then the rebase ends.
    done = git(repo, "-c", "core.editor=true", "commit", "-q", check=False)
    assert done.returncode == 0, done.stderr
    assert "demo/msg" in demo.ids()
    done = git(repo, "-c", "core.editor=true", "rebase", "--continue", check=False)
    assert done.returncode == 0, done.stderr
    assert git(repo, "log", "-1", "--format=%s").stdout.strip() == "Change it on side"


def test_a_clean_merge_passes_without_a_line(demo):
    repo = with_piece(demo)
    api.add(repo, ["demo/msg"])
    git(repo, "checkout", "-q", "-b", "side")
    change(repo, "side.md")
    assert commit(repo, message=message(line_for(repo))).returncode == 0
    git(repo, "checkout", "-q", "main")
    change(repo, "main.md")
    assert commit(repo, message=message(line_for(repo))).returncode == 0
    demo.runs()
    done = git(repo, "merge", "--no-ff", "-q", "-m", "Merge side", "side", check=False)
    assert done.returncode == 0, done.stderr
    assert "demo/msg" in demo.ids()
    assert len(git(repo, "rev-list", "--parents", "-1", "HEAD").stdout.split()) == 3


def test_a_merge_with_changes_of_its_own_needs_the_line(demo):
    repo = with_piece(demo)
    # A clean merge that someone adds to before committing it.
    git(repo, "checkout", "-q", "-b", "side")
    change(repo, "side.md")
    assert commit(repo, message=message(line_for(repo))).returncode == 0
    git(repo, "checkout", "-q", "main")
    change(repo, "main.md")
    assert commit(repo, message=message(line_for(repo))).returncode == 0
    git(repo, "merge", "--no-ff", "--no-commit", "-q", "side")
    change(repo, "extra.md")
    assert "no Commit-Check line" in refused(commit(repo, message="Merge side"))
    done = commit(repo, message=message(line_for(repo), subject="Merge side"))
    assert done.returncode == 0, done.stderr
    # A merge whose conflict was resolved by hand.
    other = with_piece(demo, "beta-code")
    _diverge(other)
    assert git(other, "merge", "side", check=False).returncode != 0
    _resolve(other)
    assert "no Commit-Check line" in refused(commit(other, message="Merge side"))
    done = commit(other, message=message(line_for(other), subject="Merge side"))
    assert done.returncode == 0, done.stderr


def test_a_disabled_piece_refuses_nothing(demo, capsys):
    repo = with_piece(demo)
    assert run(capsys, "git-hooks", "disable", str(repo), PIECE)[0] == 0
    change(repo)
    assert commit(repo, message="Add a note").returncode == 0


# -- a linked work tree -------------------------------------------------------------------

def test_a_linked_work_tree_commits_with_the_line_for_its_own_index(demo):
    repo = with_piece(demo)
    linked = demo.tmp / "linked"
    git(repo, "worktree", "add", "-q", "-b", "side", str(linked))
    change(repo, "main.md")
    change(linked, "side.md")
    # One salt, two indexes, so two lines.
    assert line_for(linked) == proof_line(salt_of(repo),
                                          git(linked, "write-tree").stdout.strip())
    assert line_for(linked) != line_for(repo)
    said = refused(commit(linked, message=message(line_for(repo))))
    assert f"doesn't match the tree being committed in {linked}" in said
    assert f"moxi repo rules {linked}" in said
    assert commit(linked, message=message(line_for(linked))).returncode == 0
    assert commit(repo, message=message(line_for(repo))).returncode == 0

"""`moxi repo rules`: what it prints for a repo's commit rules, and the `rule` setting."""

from __future__ import annotations

from conftest import POSIX_ONLY, change, commit, git

from moxi_repo import api, cli, gitconf
from moxi_repo.commit_proof import proof_line
from moxi_repo.commit_rules import rule_lines

pytestmark = POSIX_ONLY

TAG = r"\bTAG-[0-9]{1,3}[a-z]?\b"
PIECE = "repo/commit-rules"


PROOF = "repo/commit-proof"
#: What stands above a disabled rules piece's lines.
DISABLED = ("repo/commit-rules is disabled in this repo: nothing checks a commit against "
            "the lines below")


def run(capsys, *argv: str) -> tuple[int, str, str]:
    code = cli.main(list(argv))
    out, err = capsys.readouterr()
    return code, out, err


def test_a_repo_with_no_rules_piece_says_so_and_exits_zero(demo, capsys):
    repo = demo.repo()
    assert run(capsys, "rules", str(repo)) == (0, f"no commit rules for {repo}\n", "")
    # A piece that isn't a rules piece gives the repo no commit rules.
    assert run(capsys, "git-hooks", "add", str(repo), "demo/msg")[0] == 0
    assert run(capsys, "rules", str(repo)) == (0, f"no commit rules for {repo}\n", "")


def test_it_prints_the_generated_lines_then_the_repos_own_rules(demo, capsys):
    repo = demo.repo()
    assert run(capsys, "git-hooks", "add", str(repo), PIECE,
               "--set", "refused-trailer=Round", "--set", "refused-trailer=Reconciles",
               "--set", f"tag-pattern={TAG}", "--set", r"tag-pattern=\bWIP\b",
               "--set", "rule=say what changed and why in plain words",
               "--set", "rule=one change per commit: no drive-by edits")[0] == 0
    assert run(capsys, "rules", str(repo)) == (0, "\n".join([
        f"commit rules for {repo}",
        "- trailers this repo refuses: Round, Reconciles",
        f"- no text matching: {TAG}",
        r"- no text matching: \bWIP\b",
        "- say what changed and why in plain words",
        "- one change per commit: no drive-by edits"]) + "\n", "")


def test_each_kind_of_line_prints_only_when_its_setting_has_a_value(demo, capsys):
    repo = demo.repo()
    assert run(capsys, "git-hooks", "add", str(repo), PIECE)[0] == 0
    assert run(capsys, "rules", str(repo))[1] == f"commit rules for {repo}\n"
    assert run(capsys, "git-hooks", "add", str(repo), PIECE,
               "--set", "rule=keep the subject under 72 characters")[0] == 0
    assert run(capsys, "rules", str(repo))[1] == (
        f"commit rules for {repo}\n- keep the subject under 72 characters\n")
    assert run(capsys, "git-hooks", "add", str(repo), PIECE,
               "--set", "refused-trailer=Round")[0] == 0
    assert run(capsys, "rules", str(repo))[1] == (
        f"commit rules for {repo}\n- trailers this repo refuses: Round\n"
        "- keep the subject under 72 characters\n")
    assert run(capsys, "git-hooks", "add", str(repo), PIECE, "--set", "rule=")[0] == 0
    assert run(capsys, "rules", str(repo))[1] == (
        f"commit rules for {repo}\n- trailers this repo refuses: Round\n")


def test_the_rule_setting_lives_in_the_repos_git_config_and_survives_refresh(demo, capsys):
    repo = demo.repo()
    assert run(capsys, "git-hooks", "add", str(repo), PIECE,
               "--set", "Rule=first rule", "--set", "rule=second = rule")[0] == 0
    stored = git(repo, "config", "--local", "--get-all", f"moxi-repo.piece.{PIECE}.rule")
    assert stored.stdout.splitlines() == ["first rule", "second = rule"]
    assert run(capsys, "git-hooks", "refresh", str(repo))[0] == 0
    section = gitconf.read(gitconf.resolve(repo)).pieces[PIECE]
    assert section["rule"] == ["first rule", "second = rule"]
    code, out, _ = run(capsys, "git-hooks", "list", str(repo))
    assert code == 0 and "settings: rule=first rule,second = rule" in out


def test_a_rule_is_printed_and_never_checked_against_a_message(demo, capsys):
    repo = demo.repo()
    assert run(capsys, "git-hooks", "add", str(repo), PIECE,
               "--set", "rule=never write the word parser")[0] == 0
    change(repo)
    done = commit(repo, message="Fix the parser")
    assert done.returncode == 0, done.stderr


def test_a_disabled_piece_is_still_registered_and_removing_it_ends_the_rules(demo, capsys):
    repo = demo.repo()
    assert run(capsys, "git-hooks", "add", str(repo), PIECE, "--set", "rule=a rule")[0] == 0
    assert run(capsys, "git-hooks", "disable", str(repo), PIECE)[0] == 0
    assert run(capsys, "rules", str(repo))[1] == (
        f"commit rules for {repo}\n{DISABLED}\n- a rule\n")
    assert run(capsys, "git-hooks", "remove", str(repo), PIECE)[0] == 0
    assert run(capsys, "rules", str(repo))[1] == f"no commit rules for {repo}\n"


def test_with_no_path_it_reads_the_repo_you_are_in(demo, capsys):
    repo = demo.repo()
    assert run(capsys, "git-hooks", "add", str(repo), PIECE, "--set", "rule=a rule")[0] == 0
    inside = repo / "docs"
    inside.mkdir()
    demo.mp.chdir(inside)
    assert run(capsys, "rules") == (0, f"commit rules for {repo}\n- a rule\n", "")


def test_a_path_outside_any_repo_is_a_usage_error(demo, capsys):
    code, out, err = run(capsys, "rules", str(demo.tmp / "nowhere"))
    assert code == 2 and out == "" and "no git work tree" in err


def test_the_api_gives_the_same_lines_and_each_rules_piece_adds_its_own_last(demo, capsys):
    repo = demo.repo()
    assert api.rules(repo) == [f"no commit rules for {repo}"]
    assert run(capsys, "git-hooks", "add", str(repo), PIECE, "demo/msg",
               "--set", "rule=a rule")[0] == 0
    assert api.rules(repo) == [f"commit rules for {repo}", "- a rule"]
    # A second rules piece prints after the first, in the table's order, and only where
    # it is registered.
    demo.mp.setitem(api.RULE_LINES, "demo/msg",
                    lambda where, settings, **more: [f"closing line for {where.root.name}"])
    assert api.rules(repo) == [f"commit rules for {repo}", "- a rule",
                               "closing line for alpha-notes"]
    assert run(capsys, "git-hooks", "remove", str(repo), PIECE)[0] == 0
    assert api.rules(repo) == [f"commit rules for {repo}", "closing line for alpha-notes"]
    assert run(capsys, "git-hooks", "remove", str(repo), "demo/msg")[0] == 0
    assert api.rules(repo) == [f"no commit rules for {repo}"]


def proof_of(repo) -> str:
    """The line for what is staged in `repo`, made here from its salt and its index."""
    salt = gitconf.read(gitconf.resolve(repo)).pieces[PROOF]["salt"][-1]
    return proof_line(salt, git(repo, "write-tree").stdout.strip())


def test_a_repo_with_only_the_proof_piece_prints_the_heading_and_the_line(demo, capsys):
    repo = demo.repo()
    assert run(capsys, "git-hooks", "add", str(repo), PROOF)[0] == 0
    assert run(capsys, "rules", str(repo)) == (
        0, f"commit rules for {repo}\n{proof_of(repo)}\n", "")
    # The line follows what is staged.
    before = proof_of(repo)
    change(repo)
    assert proof_of(repo) != before
    assert run(capsys, "rules", str(repo))[1] == f"commit rules for {repo}\n{proof_of(repo)}\n"


def test_with_both_pieces_the_proof_line_prints_last(demo, capsys):
    repo = demo.repo()
    assert run(capsys, "git-hooks", "add", str(repo), PROOF)[0] == 0
    assert run(capsys, "git-hooks", "add", str(repo), PIECE,
               "--set", "refused-trailer=Round", "--set", f"tag-pattern={TAG}",
               "--set", "rule=say what changed and why in plain words")[0] == 0
    change(repo)
    assert run(capsys, "rules", str(repo)) == (0, "\n".join([
        f"commit rules for {repo}",
        "- trailers this repo refuses: Round",
        f"- no text matching: {TAG}",
        "- say what changed and why in plain words",
        proof_of(repo)]) + "\n", "")
    # The line it prints is the one a commit there takes.
    line = api.rules(repo)[-1]
    done = commit(repo, message=f"Add a note\n\n{line}\n")
    assert done.returncode == 0, done.stderr
    # Without the rules piece the line stays; without both the repo has no commit rules.
    assert run(capsys, "git-hooks", "remove", str(repo), PIECE)[0] == 0
    assert run(capsys, "rules", str(repo))[1] == f"commit rules for {repo}\n{proof_of(repo)}\n"
    assert run(capsys, "git-hooks", "remove", str(repo), PROOF)[0] == 0
    assert run(capsys, "rules", str(repo))[1] == f"no commit rules for {repo}\n"


def test_a_disabled_rules_piece_prints_its_lines_under_a_line_saying_nothing_checks_them(
        demo, capsys):
    repo = demo.repo()
    assert run(capsys, "git-hooks", "add", str(repo), PIECE, PROOF,
               "--set", "refused-trailer=Round", "--set", f"tag-pattern={TAG}",
               "--set", "rule=a rule")[0] == 0
    enabled = run(capsys, "rules", str(repo))[1]
    assert DISABLED not in enabled
    assert run(capsys, "git-hooks", "disable", str(repo), PIECE)[0] == 0
    assert run(capsys, "rules", str(repo)) == (0, "\n".join([
        f"commit rules for {repo}",
        DISABLED,
        "- trailers this repo refuses: Round",
        f"- no text matching: {TAG}",
        "- a rule",
        proof_of(repo)]) + "\n", "")
    # And nothing does check them: a refused trailer and a tag go through.
    change(repo)
    done = commit(repo, message=f"Fix TAG-12\n\nRound: one\n{api.rules(repo)[-1]}\n")
    assert done.returncode == 0, done.stderr
    assert run(capsys, "git-hooks", "enable", str(repo), PIECE)[0] == 0
    assert DISABLED not in run(capsys, "rules", str(repo))[1]


def test_a_disabled_proof_piece_still_prints_its_line(demo, capsys):
    repo = demo.repo()
    assert run(capsys, "git-hooks", "add", str(repo), PROOF)[0] == 0
    enabled = run(capsys, "rules", str(repo))
    assert run(capsys, "git-hooks", "disable", str(repo), PROOF)[0] == 0
    assert run(capsys, "rules", str(repo)) == enabled
    assert enabled == (0, f"commit rules for {repo}\n{proof_of(repo)}\n", "")
    # Both disabled: the rules piece's lines stand under their line, the proof line last.
    assert run(capsys, "git-hooks", "add", str(repo), PIECE, "--set", "rule=a rule")[0] == 0
    assert run(capsys, "git-hooks", "disable", str(repo), PIECE)[0] == 0
    assert run(capsys, "rules", str(repo))[1] == (
        f"commit rules for {repo}\n{DISABLED}\n- a rule\n{proof_of(repo)}\n")


def test_in_a_linked_work_tree_the_heading_and_the_line_are_that_work_trees(demo, capsys):
    repo = demo.repo()
    assert run(capsys, "git-hooks", "add", str(repo), PIECE, PROOF,
               "--set", "rule=a rule")[0] == 0
    linked = demo.tmp / "linked"
    git(repo, "worktree", "add", "-q", "-b", "side", str(linked))
    change(linked, "side.md")
    assert proof_of(linked) != proof_of(repo)
    assert run(capsys, "rules", str(linked)) == (
        0, f"commit rules for {linked}\n- a rule\n{proof_of(linked)}\n", "")
    assert run(capsys, "rules", str(repo))[1] == (
        f"commit rules for {repo}\n- a rule\n{proof_of(repo)}\n")
    # From a directory inside it, with no path.
    (linked / "docs").mkdir()
    demo.mp.chdir(linked / "docs")
    assert run(capsys, "rules")[1] == (
        f"commit rules for {linked}\n- a rule\n{proof_of(linked)}\n")
    # With no rules piece the line naming the repo names the work tree asked about too.
    assert run(capsys, "git-hooks", "remove", str(repo), PIECE, PROOF)[0] == 0
    assert run(capsys, "rules", str(linked))[1] == f"no commit rules for {linked}\n"


def test_an_index_with_unresolved_conflicts_has_no_line_and_says_why(demo, capsys):
    repo = demo.repo()
    assert run(capsys, "git-hooks", "add", str(repo), PROOF)[0] == 0
    git(repo, "checkout", "-q", "-b", "side")
    (repo / "README.md").write_text("from side\n", encoding="utf-8")
    git(repo, "commit", "-q", "-am", "Change it on side",
        env={"MOXI_REPO_COMMITTER": "notes/save"})
    git(repo, "checkout", "-q", "main")
    (repo / "README.md").write_text("from main\n", encoding="utf-8")
    git(repo, "commit", "-q", "-am", "Change it on main",
        env={"MOXI_REPO_COMMITTER": "notes/save"})
    assert git(repo, "merge", "side", check=False).returncode != 0
    assert run(capsys, "rules", str(repo)) == (0, "\n".join([
        f"commit rules for {repo}",
        f"no Commit-Check line: in {repo}, the index has unresolved conflicts; resolve "
        "them, git add the files, and run this again"]) + "\n", "")


def test_rule_lines_skips_empty_values():
    assert rule_lines({}) == []
    assert rule_lines({"refused-trailer": ["", "Round"], "tag-pattern": [""],
                       "rule": ["", "a rule"]}) == [
        "- trailers this repo refuses: Round", "- a rule"]

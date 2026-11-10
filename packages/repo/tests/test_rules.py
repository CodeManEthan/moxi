"""`moxi repo rules`: what it prints for a repo's commit rules, and the `rule` setting."""

from __future__ import annotations

from conftest import POSIX_ONLY, change, commit, git

from moxi_repo import api, cli, gitconf
from moxi_repo.commit_rules import rule_lines

pytestmark = POSIX_ONLY

TAG = r"\bTAG-[0-9]{1,3}[a-z]?\b"
PIECE = "repo/commit-rules"


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
    assert run(capsys, "rules", str(repo))[1] == f"commit rules for {repo}\n- a rule\n"
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
                    lambda where, settings: [f"closing line for {where.root.name}"])
    assert api.rules(repo) == [f"commit rules for {repo}", "- a rule",
                               "closing line for alpha-notes"]
    assert run(capsys, "git-hooks", "remove", str(repo), PIECE)[0] == 0
    assert api.rules(repo) == [f"commit rules for {repo}", "closing line for alpha-notes"]
    assert run(capsys, "git-hooks", "remove", str(repo), "demo/msg")[0] == 0
    assert api.rules(repo) == [f"no commit rules for {repo}"]


def test_rule_lines_skips_empty_values():
    assert rule_lines({}) == []
    assert rule_lines({"refused-trailer": ["", "Round"], "tag-pattern": [""],
                       "rule": ["", "a rule"]}) == [
        "- trailers this repo refuses: Round", "- a rule"]

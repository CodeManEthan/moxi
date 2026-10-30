"""The commit-rules piece: its checks on a message, how it is turned on, and its settings.

The message checks are run first as a function, then through real git commits, where the
piece reads its settings through the runner from the repo's git config.
"""

from __future__ import annotations

import pytest
from conftest import POSIX_ONLY, change, commit, git, head

from moxi_repo import api, cli, commit_rules, gitconf, settings
from moxi_repo.commit_rules import check_message

TAG = r"\bTAG-[0-9]{1,3}[a-z]?\b"
PIECE = "repo/commit-rules"


def problems(text: str, **kw) -> list[str]:
    kw.setdefault("patterns", [TAG])
    return check_message(text, **kw)


# -- the checks, as a function ------------------------------------------------------------

def test_a_clean_message_passes():
    assert problems("Fix the parser\n\nIt read one byte too many.\n\n"
                    "Signed-off-by: sample-bot <bot@example.invalid>\nAcked-by: someone\n") == []
    assert problems("One line only\n") == []
    assert problems("") == []


def test_prose_in_the_trailer_block_is_refused():
    out = problems("Fix it\n\nSigned-off-by: a\nand then some prose\n")
    assert out == ["trailer shape: line 4 is prose in the trailer block (the last paragraph); "
                   "a trailer block holds only 'Key: value' lines, so put a blank line "
                   "before the trailers"]


def test_a_last_paragraph_with_no_trailer_is_body():
    assert problems("Fix it\n\nThis is prose.\nSo is this.\n") == []


def test_the_subject_paragraph_is_never_a_trailer_block():
    assert problems("Fix: the parser\n") == []
    assert problems("Fix: the parser\nNote: more\n") == []


def test_a_trailer_stranded_in_the_body_is_refused():
    out = problems("Fix it\n\nReviewed-by: a\nsome text\n\nmore\n\nReviewed-by: b\n")
    assert out == ["trailer shape: line 3 has the trailer Reviewed-by stranded in the body; "
                   "move it into the trailer block, the last paragraph"]
    # Matched ignoring case, as git matches trailer keys.
    assert len(problems("Fix it\n\nreviewed-BY: a\n\nReviewed-by: b\n")) == 1


def test_a_trailer_without_a_value_is_refused_unless_it_continues():
    assert problems("Fix it\n\nAcked-by:\n") == [
        "trailer shape: line 3: the trailer Acked-by has no value"]
    assert problems("Fix it\n\nAcked-by:   \n") == [
        "trailer shape: line 3: the trailer Acked-by has no value"]
    assert problems("Fix it\n\nNote:\n  folded onto the next line\n") == []


def test_a_refused_key_is_refused_in_the_block_and_in_the_body():
    refused = ["Co-Authored-By", "round"]
    out = problems("Fix it\n\nround: in the body\n\nco-authored-by: x\nSigned-off-by: y\n",
                   refused=refused)
    assert out == [
        "refused trailer: line 3 has round, which this repo refuses; remove it",
        "refused trailer: line 5 has co-authored-by, which this repo refuses; remove it"]
    assert problems("Fix it\n\nCo-Authored-By: x\n") == []


def test_tags_in_the_subject_and_the_body_are_refused():
    out = problems("Fix TAG-12\n\nAs TAG-3a said, and TAG-4.\n")
    assert out == ['no tags: line 1 has "TAG-12"; say it in plain words',
                   'no tags: line 3 has "TAG-3a"; say it in plain words']


def test_the_trailer_block_comments_and_scissors_are_not_tag_checked():
    text = ("Fix the parser\n# a comment with TAG-12\n\nBody.\n\nRef: TAG-7\n"
            "# ------------------------ >8 ------------------------\n"
            "diff --git a/x b/x\n+TAG-9\n")
    assert problems(text) == []
    # Another comment prefix, as core.commentChar sets it.
    assert problems("Fix\n; see TAG-12\n", comment=";") == []
    assert problems("Fix\n# see TAG-12\n", comment=";") == [
        'no tags: line 2 has "TAG-12"; say it in plain words']
    assert problems("Fix\n; ------------------------ >8 ------------------------\nTAG-1\n",
                    comment=";") == []


def test_no_tag_pattern_means_no_tag_check():
    assert problems("Fix TAG-12\n", patterns=[]) == []
    assert problems("Fix TAG-12\n", patterns=[""]) == []


def test_a_bad_pattern_is_named_with_its_fix():
    [line] = problems("Fix it\n", patterns=["[unclosed"], root="/r")
    assert line.startswith("tag-pattern '[unclosed' is not a regular expression")
    assert line.endswith("fix it with: moxi repo git-hooks add /r repo/commit-rules "
                         "--set tag-pattern=REGEX")


def test_a_cherry_pick_line_may_sit_among_trailers():
    assert problems("Fix it\n\nSigned-off-by: a\n(cherry picked from commit abc)\n") == []


def test_line_numbers_count_crlf_messages_as_written():
    assert problems("Fix it\r\n\r\nSigned-off-by: a\r\nprose\r\n") == [
        "trailer shape: line 4 is prose in the trailer block (the last paragraph); a "
        "trailer block holds only 'Key: value' lines, so put a blank line before the "
        "trailers"]


# -- the config ---------------------------------------------------------------------------

def write_config(text: str) -> None:
    from moxi import paths

    path = paths.Layout("repo").config_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_the_config_check_refuses_a_pattern_that_will_not_compile():
    from moxi.config import ConfigError

    settings.check_text("[commit_rules]\ntag_patterns = ['\\bTAG-[0-9]+\\b']\n")
    with pytest.raises(ConfigError) as caught:
        settings.check_text("[commit_rules]\ntag_patterns = ['[unclosed']\n")
    assert caught.value.key == "commit_rules.tag_patterns"
    assert "not a regular expression" in str(caught.value)
    with pytest.raises(ConfigError):
        settings.check_text("[commit_rules]\neverywhere = 'yes please'\n")


def test_the_announcement_names_the_config_check():
    from moxi_repo import module

    assert module.CONFIG_CHECK == "moxi_repo.settings:check_text"


# -- through git --------------------------------------------------------------------------

def run(capsys, *argv: str) -> tuple[int, str, str]:
    code = cli.main(list(argv))
    out, err = capsys.readouterr()
    return code, out, err


def piece_config(repo) -> dict[str, list[str]]:
    state = gitconf.read(gitconf.resolve(repo))
    return state.pieces.get(PIECE, {})


def refused_line(done) -> list[str]:
    return [line for line in done.stderr.splitlines() if "repo/commit-rules" in line]


@POSIX_ONLY
def test_it_is_declared_and_off_unless_added(demo, capsys):
    code, out, _ = run(capsys, "git-hooks", "available")
    assert code == 0
    assert ("repo/commit-rules  commit-msg, closed, order 10, settings: refused-trailer "
            "(multi), tag-pattern (multi)") in out
    repo = demo.repo()
    write_config("[commit_rules]\ntag_patterns = ['\\bTAG-[0-9]+\\b']\n")
    assert run(capsys, "git-hooks", "add", str(repo), "demo/msg")[0] == 0
    change(repo)
    assert commit(repo, message="Fix TAG-12").returncode == 0
    assert PIECE not in gitconf.read(gitconf.resolve(repo)).pieces


@POSIX_ONLY
@pytest.mark.parametrize("mode", ["config", "shim"])
def test_settings_given_in_mixed_case_reach_the_piece_through_the_runner(demo, capsys, mode):
    repo = demo.repo()
    code, _, err = run(capsys, "git-hooks", "add", str(repo), PIECE, "--mode", mode,
                       "--set", "Refused-Trailer=Co-Authored-By",
                       "--set", f"TAG-PATTERN={TAG}")
    assert code == 0, err
    assert piece_config(repo)["refused-trailer"] == ["Co-Authored-By"]
    assert piece_config(repo)["tag-pattern"] == [TAG]
    change(repo)
    done = commit(repo, message="Fix TAG-12 in the parser")
    assert done.returncode != 0
    assert refused_line(done) == [
        'repo: commit-msg refused by repo/commit-rules: no tags: line 1 has "TAG-12"; say it '
        "in plain words"]
    done = commit(repo, message="Fix the parser\n\nCo-authored-by: someone <x@example.invalid>")
    assert done.returncode != 0
    assert "refused trailer: line 3 has Co-authored-by" in done.stderr
    before = head(repo)
    done = commit(repo, message="Fix the parser\n\nSigned-off-by: sample-bot")
    assert done.returncode == 0, done.stderr
    assert head(repo) != before


@POSIX_ONLY
def test_comment_lines_and_the_scissors_line_are_ignored_through_git(demo, capsys):
    repo = demo.repo()
    assert run(capsys, "git-hooks", "add", str(repo), PIECE, "--set",
               f"tag-pattern={TAG}")[0] == 0
    change(repo)
    message = repo.parent / "message.txt"
    message.write_text("Fix the parser\n# a comment naming TAG-12\n", encoding="utf-8")
    done = git(repo, "commit", "-q", "--cleanup=strip", "-F", str(message), check=False)
    assert done.returncode == 0, done.stderr
    change(repo, text="more\n")
    message.write_text("Fix it again\n\n# ------------------------ >8 "
                       "------------------------\nbelow the line: TAG-12\n", encoding="utf-8")
    done = git(repo, "commit", "-q", "--cleanup=scissors", "-F", str(message), check=False)
    assert done.returncode == 0, done.stderr
    git(repo, "config", "core.commentChar", ";")
    change(repo, text="and more\n")
    message.write_text("Fix a third time\n; a comment naming TAG-12\n", encoding="utf-8")
    done = git(repo, "commit", "-q", "--cleanup=strip", "-F", str(message), check=False)
    assert done.returncode == 0, done.stderr


@POSIX_ONLY
def test_merge_messages_are_checked(demo, capsys):
    repo = demo.repo()
    assert run(capsys, "git-hooks", "add", str(repo), PIECE, "--set",
               f"tag-pattern={TAG}")[0] == 0
    git(repo, "switch", "-q", "-c", "side")
    change(repo, "side.md")
    assert commit(repo, message="On the side").returncode == 0
    git(repo, "switch", "-q", "main")
    change(repo, "main.md")
    assert commit(repo, message="On main").returncode == 0
    done = git(repo, "merge", "-q", "--no-ff", "-m", "Merge the side TAG-3", "side",
               check=False)
    assert done.returncode != 0
    assert 'no tags: line 1 has "TAG-3"' in done.stderr
    git(repo, "merge", "--abort", check=False)
    done = git(repo, "merge", "-q", "--no-ff", "-m", "Merge the side", "side", check=False)
    assert done.returncode == 0, done.stderr


@POSIX_ONLY
def test_no_tag_pattern_set_means_no_tag_check_through_git(demo, capsys):
    repo = demo.repo()
    assert run(capsys, "git-hooks", "add", str(repo), PIECE)[0] == 0
    assert "tag-pattern" not in piece_config(repo)
    change(repo)
    assert commit(repo, message="Fix TAG-12").returncode == 0
    change(repo, text="x\n")
    done = commit(repo, message="Fix\n\nAcked-by:")
    assert done.returncode != 0 and "the trailer Acked-by has no value" in done.stderr


@POSIX_ONLY
def test_the_seed_is_copied_once_and_later_config_changes_reach_no_registered_repo(
        demo, capsys):
    alpha, beta = demo.repo("alpha-notes"), demo.repo("beta-code")
    write_config("[commit_rules]\ntag_patterns = ['\\bTAG-[0-9]+\\b']\n")
    assert run(capsys, "git-hooks", "add", str(alpha), PIECE)[0] == 0
    assert piece_config(alpha)["tag-pattern"] == [r"\bTAG-[0-9]+\b"]
    assert "refused-trailer" not in piece_config(alpha)
    write_config("[commit_rules]\ntag_patterns = ['\\bNEW-[0-9]+\\b']\n")
    assert run(capsys, "git-hooks", "refresh")[0] == 0
    assert run(capsys, "git-hooks", "add", str(alpha), PIECE)[0] == 0
    assert piece_config(alpha)["tag-pattern"] == [r"\bTAG-[0-9]+\b"]
    assert run(capsys, "git-hooks", "add", str(beta), PIECE)[0] == 0
    assert piece_config(beta)["tag-pattern"] == [r"\bNEW-[0-9]+\b"]
    # Taking the new pattern is a command that names the repo.
    assert run(capsys, "git-hooks", "add", str(alpha), PIECE,
               "--set", r"tag-pattern=\bNEW-[0-9]+\b")[0] == 0
    assert piece_config(alpha)["tag-pattern"] == [r"\bNEW-[0-9]+\b"]


@POSIX_ONLY
def test_clearing_the_tag_pattern_and_its_edges(demo, capsys):
    repo = demo.repo()
    write_config("[commit_rules]\ntag_patterns = ['\\bTAG-[0-9]+\\b']\n")
    # A first registration given the clearing form isn't seeded.
    assert run(capsys, "git-hooks", "add", str(repo), PIECE, "--set", "tag-pattern=")[0] == 0
    assert "tag-pattern" not in piece_config(repo)
    assert run(capsys, "git-hooks", "add", str(repo), PIECE, "--set", f"tag-pattern={TAG}",
               "--set", "tag-pattern=x")[0] == 0
    assert piece_config(repo)["tag-pattern"] == [TAG, "x"]
    code, _, err = run(capsys, "git-hooks", "add", str(repo), PIECE, "--set", "tag-pattern=",
                       "--set", "tag-pattern=x")
    assert code == 2 and "tag-pattern" in err
    assert piece_config(repo)["tag-pattern"] == [TAG, "x"]
    assert run(capsys, "git-hooks", "add", str(repo), PIECE, "--set", "tag-pattern=")[0] == 0
    assert "tag-pattern" not in piece_config(repo)
    change(repo)
    assert commit(repo, message="Fix TAG-12").returncode == 0


@POSIX_ONLY
def test_a_pattern_that_will_not_compile_is_refused_before_anything_is_written(demo, capsys):
    repo = demo.repo()
    code, _, err = run(capsys, "git-hooks", "add", str(repo), PIECE,
                       "--set", "tag-pattern=[unclosed")
    assert code == 2
    assert "repo/commit-rules: --set tag-pattern: '[unclosed' is not a regular expression" in err
    assert "nothing was written" in err
    assert PIECE not in gitconf.read(gitconf.resolve(repo)).pieces
    write_config("[commit_rules]\ntag_patterns = ['[unclosed']\n")
    code, _, err = run(capsys, "git-hooks", "add", str(repo), PIECE)
    assert code == 1 and "repo's config can't be read" in err
    assert "commit_rules.tag_patterns" in err


@POSIX_ONLY
def test_everywhere_adds_it_on_refresh_with_the_config_patterns_and_no_refused_trailers(
        demo, capsys):
    alpha, beta = demo.repo("alpha-notes"), demo.repo("beta-code")
    outside = demo.repo("outside")
    assert run(capsys, "git-hooks", "add", str(alpha), "demo/gate")[0] == 0
    assert run(capsys, "git-hooks", "add", str(beta))[0] == 0
    assert PIECE not in gitconf.read(gitconf.resolve(beta)).pieces
    write_config("[commit_rules]\neverywhere = true\n"
                 "tag_patterns = ['\\bTAG-[0-9]+\\b']\n")
    code, out, _ = run(capsys, "git-hooks", "refresh")
    assert code == 0
    for repo in (alpha, beta):
        section = piece_config(repo)
        assert section["tag-pattern"] == [r"\bTAG-[0-9]+\b"]
        assert "refused-trailer" not in section
        assert section["enabled"] == ["true"]
        assert f"added repo/commit-rules (commit-msg, closed) in {repo} " \
               "(commit_rules.everywhere)" in out
    # A repo outside the index never gets it.
    assert PIECE not in gitconf.read(gitconf.resolve(outside)).pieces
    change(beta)
    done = commit(beta, message="Fix TAG-12")
    assert done.returncode != 0 and 'no tags: line 1 has "TAG-12"' in done.stderr
    # add touches a repo too: one it names, with a piece or only to index it.
    gamma = demo.repo("gamma")
    assert run(capsys, "git-hooks", "add", str(gamma))[0] == 0
    assert piece_config(gamma)["tag-pattern"] == [r"\bTAG-[0-9]+\b"]
    delta = demo.repo("delta")
    assert run(capsys, "git-hooks", "add", str(delta), "demo/gate")[0] == 0
    assert PIECE in gitconf.read(gitconf.resolve(delta)).pieces


@POSIX_ONLY
def test_everywhere_leaves_a_disabled_piece_and_a_repos_own_settings_alone(demo, capsys):
    alpha, beta = demo.repo("alpha-notes"), demo.repo("beta-code")
    assert run(capsys, "git-hooks", "add", str(alpha), PIECE,
               "--set", "refused-trailer=Co-Authored-By",
               "--set", "refused-trailer=Round")[0] == 0
    assert run(capsys, "git-hooks", "add", str(beta), PIECE)[0] == 0
    assert run(capsys, "git-hooks", "disable", str(beta), PIECE)[0] == 0
    write_config("[commit_rules]\neverywhere = true\n"
                 "tag_patterns = ['\\bTAG-[0-9]+\\b']\n")
    assert run(capsys, "git-hooks", "refresh")[0] == 0
    assert run(capsys, "git-hooks", "refresh", str(alpha))[0] == 0
    assert run(capsys, "git-hooks", "add", str(alpha), "demo/gate")[0] == 0
    assert piece_config(alpha)["refused-trailer"] == ["Co-Authored-By", "Round"]
    assert "tag-pattern" not in piece_config(alpha)
    assert piece_config(beta)["enabled"] == ["false"]
    assert "tag-pattern" not in piece_config(beta)
    # alpha refuses the trailers it names; beta, with the piece disabled, doesn't, and a
    # third repo with the piece enabled and no refused list doesn't either.
    gamma = demo.repo("gamma")
    assert run(capsys, "git-hooks", "add", str(gamma), PIECE)[0] == 0
    message = "Fix the parser\n\nCo-Authored-By: someone\nRound: x"
    for repo, refused in ((alpha, True), (beta, False), (gamma, False)):
        change(repo)
        done = commit(repo, message=message)
        assert (done.returncode != 0) is refused, (repo, done.stderr)


@POSIX_ONLY
def test_a_committer_pass_over_tag_shaped_file_names_passes_the_piece(demo, capsys):
    import moxi_repo

    repo = demo.repo()
    assert run(capsys, "git-hooks", "add", str(repo), PIECE, "--set",
               r"tag-pattern=\bTAG-[0-9]+\b")[0] == 0
    notes = repo / "notes"
    notes.mkdir()
    (notes / "TAG-12.md").write_text("a note\n", encoding="utf-8")
    (notes / "about TAG-3 and TAG-4.md").write_text("another\n", encoding="utf-8")
    result = moxi_repo.commit(moxi_repo.Spec(
        name="demo/snap", root=repo, paths=("notes",),
        author=("sample-bot", "bot@example.invalid")))
    assert result.status == "committed", result.line()
    subject = git(repo, "log", "-1", "--format=%s").stdout.strip()
    assert subject == "demo/snap: 2 paths"


def test_the_module_keeps_to_the_standard_library():
    """The piece runs inside the git hook: its module imports nothing outside the standard
    library but the runner's own `moxi_repo.pieces`."""
    import ast
    import sys

    tree = ast.parse(open(commit_rules.__file__, encoding="utf-8").read())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    outside = {m for m in imported if m.split(".")[0] not in sys.stdlib_module_names}
    assert outside == {"moxi_repo.pieces"}


def test_seeds_and_setting_checks_are_registered_for_the_piece():
    assert api.COMMIT_RULES == PIECE
    assert PIECE in api.SEEDS and PIECE in api.SETTING_CHECKS
    assert api.SETTING_CHECKS[PIECE]("refused-trailer", ["[unclosed"]) is None
    assert api.SETTING_CHECKS[PIECE]("tag-pattern", ["[ok]"]) is None

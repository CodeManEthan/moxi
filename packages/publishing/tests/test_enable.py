"""`moxi publishing enable`: the flag, the three pieces and their seeded settings."""

from __future__ import annotations

import pytest
from conftest import git, write_config

from moxi_publishing import cli, flag

PIECES = ("publishing/message", "publishing/content", "publishing/push")


def config(repo, key):
    done = git(repo, "config", "--get-all", key, check=False)
    return done.stdout.splitlines()


def test_enable_marks_the_repo_and_registers_the_three_pieces_with_seeds(repo, names):
    from moxi_repo import api

    lines = flag.enable(repo)
    assert config(repo, "moxi-publishing.public") == ["true"]
    assert config(repo, "moxi-publishing.interpreter") == [api.interpreter()]
    have = flag.registered(str(repo))
    assert sorted(have) == sorted(PIECES)
    assert all(piece["enabled"] for piece in have.values())
    # The key another commit-msg hook may read to know whether Commit-Check is dropped.
    assert config(repo, "moxi-repo.piece.publishing/message.drop-trailer") == [
        "Claude-Session", "Commit-Check"]
    assert config(repo, "moxi-repo.piece.publishing/message.pass-trailer") == []
    assert have["publishing/push"]["settings"] == {"main": ["main"],
                                                    "word-required": ["false"]}
    assert lines[0].startswith(f"marked {repo} public")


def test_set_replaces_the_seed_of_its_key_and_leaves_the_others(repo, names):
    flag.enable(repo, sets=["drop-trailer=Claude-Session", "drop-trailer=Co-Authored-By",
                            "pass-trailer=Commit-Check", "word-required=true"])
    have = flag.registered(str(repo))
    assert have["publishing/message"]["settings"] == {
        "drop-trailer": ["Claude-Session", "Co-Authored-By"],
        "pass-trailer": ["Commit-Check"]}
    assert have["publishing/push"]["settings"] == {"main": ["main"],
                                                    "word-required": ["true"]}


def test_enable_again_without_set_keeps_the_settings_the_repo_has(repo, names):
    flag.enable(repo, sets=["drop-trailer=Claude-Session", "pass-trailer=Commit-Check"])
    flag.enable(repo)
    assert config(repo, "moxi-repo.piece.publishing/message.drop-trailer") == [
        "Claude-Session"]
    assert config(repo, "moxi-repo.piece.publishing/message.pass-trailer") == [
        "Commit-Check"]


def test_after_disable_enable_seeds_again(repo, names):
    flag.enable(repo, sets=["drop-trailer=Claude-Session"])
    flag.disable(repo)
    flag.enable(repo)
    assert config(repo, "moxi-repo.piece.publishing/message.drop-trailer") == [
        "Claude-Session", "Commit-Check"]


def test_seeds_come_from_publishings_config(repo, names):
    write_config("publishing", '[defaults]\ndrop_trailers = ["X-Trace"]\nmain = "trunk"\n'
                               'word_required = true\n')
    flag.enable(repo)
    have = flag.registered(str(repo))
    assert have["publishing/message"]["settings"] == {"drop-trailer": ["X-Trace"]}
    assert have["publishing/push"]["settings"] == {"main": ["trunk"],
                                                    "word-required": ["true"]}


def test_a_set_key_alone_with_no_value_removes_the_seed(repo, names):
    flag.enable(repo, sets=["drop-trailer="])
    assert config(repo, "moxi-repo.piece.publishing/message.drop-trailer") == []


def test_github_is_given_or_read_from_a_github_origin(repo, names, tmp_path):
    git(repo, "remote", "add", "origin", "git@github.com:alice/proj-a.git")
    flag.enable(repo)
    assert config(repo, "moxi-publishing.github") == ["alice/proj-a"]
    flag.enable(repo, github="bob/proj-b")
    assert config(repo, "moxi-publishing.github") == ["bob/proj-b"]


def test_no_github_is_said(repo, names):
    lines = flag.enable(repo)
    assert any("no GitHub repo is known" in line for line in lines)


def test_a_github_repo_already_named_is_kept_and_not_asked_for(repo, names):
    git(repo, "remote", "add", "origin", "git@github.com:alice/proj-a.git")
    flag.enable(repo, github="bob/proj-b")
    lines = flag.enable(repo, sets=["main=main"])
    assert config(repo, "moxi-publishing.github") == ["bob/proj-b"]
    assert not any("no GitHub repo is known" in line for line in lines)


@pytest.mark.parametrize("pattern", [
    "refs/heads/main", "refs/heads/*", "refs/*", "refs/h*", "*", "refs/tags/*",
    "refs/tags/proj-a-v1.0.0", "refs/tags/docs-*", "refs/t?gs/x", "heads/x",
])
def test_free_ref_refuses_a_pattern_that_could_match_a_branch_or_release_tag(
        repo, names, pattern):
    with pytest.raises(flag.Failure) as caught:
        flag.enable(repo, sets=[f"free-ref={pattern}"])
    assert caught.value.code == 2
    assert "nothing was written" in caught.value.text
    assert config(repo, "moxi-publishing.public") == []
    assert flag.registered(str(repo)) == {}


@pytest.mark.parametrize("pattern", [
    "refs/notes/*", "refs/tags/docs/*", "refs/tags/docs", "refs/pull/*/head",
])
def test_free_ref_takes_a_pattern_outside_branches_and_release_tags(repo, names, pattern):
    flag.enable(repo, sets=[f"free-ref={pattern}"])
    assert config(repo, "moxi-repo.piece.publishing/push.free-ref") == [pattern]


def test_word_required_takes_true_or_false(repo, names):
    with pytest.raises(flag.Failure) as caught:
        flag.enable(repo, sets=["word-required=maybe"])
    assert caught.value.code == 2


@pytest.mark.parametrize("sets", [["no-such=1"], ["main=a", "main=b"],
                                  ["drop-trailer=", "drop-trailer=X"]])
def test_a_set_repo_refuses_writes_nothing(repo, names, sets):
    with pytest.raises(flag.Failure) as caught:
        flag.enable(repo, sets=sets)
    assert caught.value.code == 2
    assert git(repo, "config", "--get-regexp", "^moxi-", check=False).stdout == ""


def test_enable_warns_when_the_private_names_list_is_empty(repo):
    lines = flag.enable(repo)
    assert any("private names list is empty" in line for line in lines)


def test_enable_says_nothing_of_names_when_the_list_has_some(repo, names):
    lines = flag.enable(repo)
    assert not any("private names" in line for line in lines)


def test_the_command_prints_and_exits(repo, names, capsys):
    assert cli.main(["enable", str(repo)]) == 0
    assert f"marked {repo} public" in capsys.readouterr().out
    assert cli.main(["enable", str(repo.parent / "nowhere")]) == 2
    assert "no git work tree" in capsys.readouterr().err


def test_the_help_says_set_replaces(capsys):
    with pytest.raises(SystemExit):
        cli.main(["enable", "--help"])
    out = " ".join(capsys.readouterr().out.split())
    assert "replacing its seed" in out
    assert "A key on pass-trailer is never dropped" in out

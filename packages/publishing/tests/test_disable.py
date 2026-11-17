"""`moxi publishing disable`: the pieces go, then the flag."""

from __future__ import annotations

from conftest import commit, git

from moxi_publishing import cli, flag


def test_disable_removes_the_pieces_then_the_flag(repo, names, capsys):
    flag.enable(repo)
    assert cli.main(["disable", str(repo)]) == 0
    assert flag.registered(str(repo)) == {}
    assert git(repo, "config", "--get", "moxi-publishing.public", check=False).stdout == ""
    assert "no longer marked public" in capsys.readouterr().out
    # Nothing guards the repo now: a commit with a dropped key keeps it.
    assert commit(repo, "Subject\n\nClaude-Session: x\n").returncode == 0


def test_disable_on_a_repo_never_enabled_is_fine(repo):
    assert flag.disable(repo)[-1].endswith("is no longer marked public")


def test_disable_takes_out_a_piece_left_alone(repo, names):
    from moxi_repo import api

    api.add(repo, "publishing/message")
    flag.disable(repo)
    assert flag.registered(str(repo)) == {}

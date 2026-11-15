"""`moxi publishing list`: every indexed repo marked public, with its pieces."""

from __future__ import annotations

import json
import shutil

from moxi_publishing import cli, flag


def test_list_shows_flagged_repos_with_each_piece(repo, names, template, tmp_path, capsys):
    from moxi_repo import api

    other = tmp_path / "proj-b"
    shutil.copytree(template, other)
    api.add(other)  # indexed, not public
    flag.enable(repo, sets=["pass-trailer=Commit-Check"])
    api.disable(repo, "publishing/content")
    assert cli.main(["list", "--json"]) == 0
    repos = json.loads(capsys.readouterr().out)
    assert [r["root"] for r in repos] == [str(repo)]
    pieces = repos[0]["pieces"]
    assert pieces["publishing/content"]["enabled"] is False
    assert pieces["publishing/message"]["settings"]["pass-trailer"] == ["Commit-Check"]
    assert cli.main(["list"]) == 0
    out = capsys.readouterr().out
    assert "publishing/content: disabled" in out
    assert "pass-trailer=Commit-Check" in out


def test_list_with_none_says_so(capsys):
    assert cli.main(["list"]) == 0
    assert capsys.readouterr().out.strip() == "no repo is marked public"

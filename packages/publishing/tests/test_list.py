"""`moxi publishing list`: every indexed repo with clean history, push approval or the
public flag, with its pieces."""

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
    assert capsys.readouterr().out.strip() == (
        "no repo keeps a clean history, has push approval, is marked public or is "
        "left out of project add")


def test_list_shows_each_repos_three_choices(repo, names, template, tmp_path, capsys):
    from moxi_publishing import approval, clean

    private = tmp_path / "proj-b"
    shutil.copytree(template, private)
    clean.enable(private)
    approval.enable(private, ["main", "release/*"])
    flag.enable(repo)
    assert cli.main(["list", "--json"]) == 0
    by_root = {r["root"]: r for r in json.loads(capsys.readouterr().out)}
    assert (by_root[str(repo)]["public"], by_root[str(repo)]["clean"],
            by_root[str(repo)]["approval"]) == (True, True, None)
    mine = by_root[str(private)]
    assert (mine["public"], mine["clean"], mine["approval"]) == (False, True,
                                                                ["main", "release/*"])
    assert set(mine["pieces"]) == {"publishing/message", "publishing/content",
                                   "publishing/approval"}
    assert cli.main(["list"]) == 0
    out = capsys.readouterr().out
    assert "  public: no; clean history: yes; push approval: main release/*" in out
    assert "  public: yes; clean history: yes; push approval: off" in out


def test_list_shows_a_repo_with_approval_alone(repo, capsys):
    from moxi_publishing import approval

    approval.enable(repo, ["*"])
    [entry] = flag.listing()
    assert (entry["public"], entry["clean"], entry["approval"]) == (False, False, ["*"])
    assert entry["pieces"] == {"publishing/approval": entry["pieces"]["publishing/approval"]}


def test_list_shows_a_repo_left_out_of_project_add(repo, names, capsys):
    from moxi_publishing import approval, projects_link

    approval.enable(repo, ["main"])
    assert flag.listing()[0]["left_out"] is False
    projects_link.leave_out(str(repo))
    [entry] = flag.listing()
    assert entry["left_out"] is True
    assert cli.main(["list"]) == 0
    assert ("  public: no; clean history: no; push approval: main; project add: left out"
            in capsys.readouterr().out)

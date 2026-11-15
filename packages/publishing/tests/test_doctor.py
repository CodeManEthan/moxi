"""The doctor's rows for flagged repos and stray pieces."""

from __future__ import annotations

from conftest import git

from moxi_publishing import doctor, flag


def states(root):
    return [(row.state, row.detail) for row in doctor.rows()
            if row.name == f"repo {root}"]


def test_a_flagged_repo_with_every_piece_is_ok(repo, names):
    flag.enable(repo)
    assert [s for s, _ in states(repo)] == ["ok"]


def test_the_flag_with_a_piece_missing_or_disabled_fails(repo, names):
    from moxi_repo import api

    flag.enable(repo)
    api.disable(repo, "publishing/push")
    [(state, detail)] = states(repo)
    assert state == "fail" and "disabled: publishing/push" in detail
    api.remove(repo, "publishing/content")
    [(state, detail)] = states(repo)
    assert "missing: publishing/content" in detail


def test_a_piece_without_the_flag_fails(repo, names):
    flag.enable(repo)
    git(repo, "config", "--unset", "moxi-publishing.public")
    [(state, detail)] = states(repo)
    assert state == "fail" and "isn't marked public" in detail


def test_with_nothing_public_one_ok_row():
    rows = list(doctor.rows())
    assert [(r.name, r.state) for r in rows] == [("repos", "ok")]

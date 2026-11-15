"""The doctor's rows for flagged repos and stray pieces: each row both ways, in repo's
config mode and its shim mode."""

from __future__ import annotations

import os
import stat

import pytest
from conftest import git, write_config

from moxi_publishing import doctor, flag, push, release

OTHER_HOOK = "#!/bin/sh\n# a commit-msg check of the repo's own\nexit 0\n"


@pytest.fixture(params=["config", "shim"])
def mode(request):
    write_config("repo", f'[git_hooks]\nmode = "{request.param}"\n')
    return request.param


def states(root):
    return [(row.state, row.detail) for row in doctor.rows()
            if row.name == f"repo {root}"]


def enabled(repo, *sets):
    flag.enable(repo, github="operator/proj-a", sets=list(sets))
    return repo


def later_hook(repo):
    hook = repo / ".git" / "hooks" / "commit-msg"
    hook.parent.mkdir(exist_ok=True)
    hook.write_text(OTHER_HOOK)
    hook.chmod(hook.stat().st_mode | stat.S_IXUSR)


def test_a_flagged_repo_with_every_piece_is_ok(repo, names, mode):
    enabled(repo)
    assert states(repo) == [("ok", "marked public; the three pieces are enabled")]


def test_the_flag_with_a_piece_missing_or_disabled_fails(repo, names):
    from moxi_repo import api

    enabled(repo)
    api.disable(repo, "publishing/push")
    [(state, detail)] = states(repo)
    assert state == "fail" and "disabled: publishing/push" in detail
    api.remove(repo, "publishing/content")
    [(state, detail)] = states(repo)
    assert "missing: publishing/content" in detail


def test_a_piece_without_the_flag_fails(repo, names):
    enabled(repo)
    git(repo, "config", "--unset", "moxi-publishing.public")
    [(state, detail)] = states(repo)
    assert state == "fail" and "isn't marked public" in detail


def test_with_nothing_public_one_ok_row():
    rows = list(doctor.rows())
    assert [(r.name, r.state) for r in rows] == [("repos", "ok")]


def test_hooks_run_by_another_interpreter_fail(repo, names, mode):
    enabled(repo)
    git(repo, "config", "moxi-publishing.interpreter", "/opt/other/bin/python")
    [(state, detail)] = states(repo)
    assert state == "fail"
    assert detail.startswith("the commit-msg, pre-commit, pre-push hook commands do not "
                             "run /opt/other/bin/python")


def test_an_unrecorded_interpreter_fails(repo, names):
    enabled(repo)
    git(repo, "config", "--unset", "moxi-publishing.interpreter")
    [(state, detail)] = states(repo)
    assert state == "fail" and "moxi-publishing.interpreter is unset" in detail


def test_a_later_commit_msg_hook_fails_while_the_seed_drops_the_checked_trailer(
        repo, names, mode):
    later_hook(repo)
    enabled(repo)
    rows = states(repo)
    assert [s for s, _ in rows] == ["fail"]
    assert "a commit-msg hook runs after the pieces" in rows[0][1]
    assert "drop-trailer names Commit-Check" in rows[0][1]


def test_a_later_hook_passes_once_the_checked_trailer_is_not_dropped(repo, names, mode):
    later_hook(repo)
    enabled(repo, "drop-trailer=Claude-Session", "pass-trailer=Commit-Check")
    assert [s for s, _ in states(repo)] == ["ok"]


def test_a_drop_setting_with_no_value_reads_as_the_seed(repo, names):
    later_hook(repo)
    enabled(repo)
    git(repo, "config", "--replace-all", "moxi-repo.piece.publishing/message.drop-trailer", "")
    [(state, detail)] = states(repo)
    assert state == "fail" and "holds no value, which reads as the seed" in detail


@pytest.mark.parametrize("values, drops", [
    ([], True), ([""], True), (["Claude-Session"], False),
    (["Claude-Session", "commit-check"], True)])
def test_drops_checked_trailer(values, drops):
    assert doctor.drops_checked_trailer(values) is drops


def test_no_later_hook_lets_the_seed_stand(repo, names):
    enabled(repo)
    assert [s for s, _ in states(repo)] == ["ok"]


def test_an_unset_github_repo_warns(repo, names):
    flag.enable(repo)
    assert [s for s, _ in states(repo)] == ["warn", "ok"]
    assert "moxi-publishing.github is unset" in states(repo)[0][1]


def test_a_workflow_file_unlike_its_template_warns_and_a_missing_one_doesnt(repo, names):
    enabled(repo)
    release.workflows_write(str(repo))
    assert [s for s, _ in states(repo)] == ["ok"]
    (repo / ".github" / "workflows" / "release.yml").write_text("name: mine\n")
    os.remove(repo / ".github" / "workflows" / "publishing.yml")
    assert states(repo) == [
        ("warn", ".github/workflows/release.yml differs from publishing's template"),
        ("ok", "marked public; the three pieces are enabled")]


def test_an_empty_names_list_and_an_unwritable_push_log_warn_once(repo, tmp_path,
                                                                     monkeypatch):
    enabled(repo)
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o500)
    monkeypatch.setattr(push, "log_path", lambda: locked / "state" / "pushes.jsonl")
    try:
        once = [(r.name, r.state) for r in doctor.rows() if not r.name.startswith("repo ")]
    finally:
        locked.chmod(0o700)
    assert once == [("private names", "warn"), ("push log", "warn")]

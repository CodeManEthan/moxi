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
    """A repo set up whole: flagged, with its GitHub repo and both workflow files."""
    flag.enable(repo, github="operator/proj-a", sets=list(sets))
    release.workflows_write(str(repo))
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
    git(repo, "config", "--unset", "moxi-publishing.clean")
    [(state, detail)] = states(repo)
    assert state == "fail" and "marked neither clean nor public" in detail


def test_the_push_piece_in_a_clean_repo_that_isnt_public_fails(repo, names):
    enabled(repo)
    git(repo, "config", "--unset", "moxi-publishing.public")
    [(state, detail)] = states(repo)
    assert state == "fail"
    assert detail == ("publishing/push is registered, but the repo isn't marked public, "
                      "so it refuses every push")


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
    release.workflows_write(str(repo))
    assert [s for s, _ in states(repo)] == ["warn", "ok"]
    assert "moxi-publishing.github is unset" in states(repo)[0][1]


def test_a_workflow_file_unlike_its_template_warns(repo, names):
    enabled(repo)
    assert [s for s, _ in states(repo)] == ["ok"]
    (repo / ".github" / "workflows" / "release.yml").write_text("name: mine\n")
    assert states(repo) == [
        ("warn", ".github/workflows/release.yml differs from publishing's template"),
        ("ok", "marked public; the three pieces are enabled")]


def test_a_missing_scan_workflow_warns_and_a_missing_release_one_does_not(repo, names):
    enabled(repo)
    os.remove(repo / ".github" / "workflows" / "release.yml")
    assert [s for s, _ in states(repo)] == ["ok"]
    os.remove(repo / ".github" / "workflows" / "publishing.yml")
    found = [r for r in doctor.rows() if r.name == f"repo {repo}"]
    assert [(r.state, r.detail) for r in found] == [
        ("warn", ".github/workflows/publishing.yml is missing, so GitHub scans no push for "
                 "what the pieces refuse"),
        ("ok", "marked public; the three pieces are enabled")]
    assert found[0].fix.startswith("moxi publishing workflows write ")


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


def test_a_committer_other_than_secrets_author_warns(repo, names):
    enabled(repo)
    git(repo, "config", "user.email", "operator@users.example.test")
    found = states(repo)
    assert [s for s, _ in found] == ["warn", "ok"]
    assert found[0][1].startswith("the repo's user.email (operator@users.example.test) "
                                  "isn't secrets' author.email (operator@example.test)")
    git(repo, "config", "user.email", "OPERATOR@example.test")
    assert [s for s, _ in states(repo)] == ["ok"]


def test_with_no_author_email_the_committer_name_is_compared(repo):
    write_config("secrets", '[private_names]\nwords = ["bobsecret"]\n'
                            '[author]\nname = "operator"\n')
    enabled(repo)
    assert [s for s, _ in states(repo)] == ["ok"]
    git(repo, "config", "user.name", "alice")
    found = states(repo)
    assert [s for s, _ in found] == ["warn", "ok"]
    assert "user.name (alice) isn't secrets' author.name (operator)" in found[0][1]


def test_with_no_author_every_committer_is_the_repos_own(repo):
    write_config("secrets", '[private_names]\nwords = ["bobsecret"]\n')
    enabled(repo)
    git(repo, "config", "user.email", "alice@example.test")
    assert [s for s, _ in states(repo)] == ["ok"]


def later_hook_row(repo):
    return next(row for row in doctor.rows()
                if row.name == f"repo {repo}" and "runs after the pieces" in row.detail)


def apply_fix(repo, fix):
    """Run the row's `enable` as a person pasting it would, through `flag.enable`."""
    import shlex

    words = shlex.split(fix[fix.index("moxi publishing enable"):])
    assert words[3] == str(repo)
    sets = [words[i + 1] for i, word in enumerate(words) if word == "--set"]
    flag.enable(repo, sets=sets)
    return sets


@pytest.mark.parametrize("sets", [
    (),                                                   # the seed
    ("drop-trailer=Commit-Check",),                       # the checked trailer alone
    ("drop-trailer=Commit-Check", "drop-trailer=X-Note",
     "pass-trailer=Signed-off-by"),                       # another key, and a pass key
])
def test_the_later_hook_fix_clears_its_row_and_keeps_the_pass_keys(repo, names, sets):
    later_hook(repo)
    enabled(repo, *sets)
    applied = apply_fix(repo, later_hook_row(repo).fix)
    assert [s for s, _ in states(repo)] == ["ok"]
    assert "pass-trailer=Commit-Check" in applied
    if "pass-trailer=Signed-off-by" in sets:
        assert "pass-trailer=Signed-off-by" in applied
        assert "drop-trailer=X-Note" in applied


def test_a_drop_list_left_empty_is_fixed_with_the_seeds_other_keys(repo, names):
    later_hook(repo)
    enabled(repo)
    git(repo, "config", "--replace-all", "moxi-repo.piece.publishing/message.drop-trailer", "")
    applied = apply_fix(repo, later_hook_row(repo).fix)
    assert "drop-trailer=Claude-Session" in applied
    assert [s for s, _ in states(repo)] == ["ok"]


def test_with_no_other_key_anywhere_the_fix_says_to_name_one(repo, names):
    later_hook(repo)
    enabled(repo, "drop-trailer=Commit-Check")
    write_config("publishing", '[defaults]\ndrop_trailers = ["Commit-Check"]\n')
    fix = later_hook_row(repo).fix
    assert fix.startswith("name a key other than Commit-Check on drop-trailer")
    assert fix.endswith("--set pass-trailer=Commit-Check --set drop-trailer=KEY")


# -- standing branches --------------------------------------------------------------------

def standing(repo, url="git@github.com:operator/proj-a.git"):
    """`enabled` with `standing-branch=ci/*` and `origin` at `url`, when one is given."""
    if url is not None:
        git(repo, "remote", "add", "origin", url)
    return enabled(repo, "standing-branch=ci/*", "standing-branch=ci-full/*")


@pytest.mark.parametrize("url", ["git@github.com:operator/proj-a.git",
                                 "https://github.com/operator/proj-a",
                                 "ssh://git@github.com:22/operator/proj-a.git"])
def test_standing_branches_to_a_reducing_origin_are_named_on_the_ok_row(repo, names, url):
    standing(repo, url)
    assert states(repo) == [("ok", "marked public; the three pieces are enabled; standing "
                                   "branches: ci/* ci-full/*")]


def test_a_bad_standing_pattern_fails_naming_the_fix(repo, names):
    standing(repo)
    git(repo, "config", "--add", "moxi-repo.piece.publishing/push.standing-branch", "ci*")
    [row] = [r for r in doctor.rows() if r.name == f"repo {repo}"]
    assert row.state == "fail"
    assert "'ci*' is not a fixed prefix" in row.detail
    assert "refuses every push" in row.detail
    assert row.fix == f"moxi publishing enable {repo} --set 'standing-branch=ci/*' (or " \
                      "--set standing-branch= to remove it)"


def test_a_standing_pattern_without_the_github_repo_fails(repo, names):
    standing(repo)
    git(repo, "config", "--unset", "moxi-publishing.github")
    rows = [r for r in doctor.rows() if r.name == f"repo {repo}"]
    [failed] = [r for r in rows if r.state == "fail"]
    assert "moxi-publishing.github is unset, so no push of main waits for CI on a " \
           "standing branch" in failed.detail
    assert failed.fix == f"moxi publishing enable {repo} --github OWNER/REPO"


@pytest.mark.parametrize("url", ["git@github.com/operator/proj-a.git",
                                 "http://github.com/operator/proj-a.git",
                                 "git@github.com:operator/proj-a.git/",
                                 "git@github.com:operator/other.git"])
def test_an_origin_that_does_not_reduce_warns_naming_its_url(repo, names, url):
    standing(repo, url)
    assert states(repo)[0] == (
        "warn", f"no push URL of origin ({url}) reduces to operator/proj-a "
                "(moxi-publishing.github) in one of the three forms git sends to GitHub, "
                "so a push of main there waits for no CI on ci/* ci-full/*")
    assert states(repo)[-1][0] == "ok"


def test_the_push_url_is_read_as_git_pushes_to_it(repo, names):
    standing(repo, "gh:operator/proj-a")
    assert states(repo)[0][0] == "warn"
    # insteadOf rewrites the URL git pushes to and hands the hook.
    git(repo, "config", "url.git@github.com:.insteadOf", "gh:")
    assert states(repo)[0][0] == "ok"
    # pushInsteadOf rewrites it for a push only, and before insteadOf.
    git(repo, "config", "url.git@example.test:.pushInsteadOf", "gh:")
    assert "no push URL of origin (git@example.test:operator/proj-a)" in states(repo)[0][1]
    git(repo, "config", "--unset", "url.git@example.test:.pushInsteadOf")
    # A pushurl is where the push goes, whatever url says.
    git(repo, "config", "remote.origin.pushurl", "git@example.test:operator/proj-a.git")
    assert "no push URL of origin (git@example.test:operator/proj-a.git)" in \
        states(repo)[0][1]


def test_one_reducing_push_url_of_several_is_enough(repo, names):
    standing(repo)
    git(repo, "config", "--add", "remote.origin.pushurl", "git@example.test:mirror.git")
    git(repo, "config", "--add", "remote.origin.pushurl", "git@github.com:operator/proj-a")
    assert states(repo) == [("ok", "marked public; the three pieces are enabled; standing "
                                   "branches: ci/* ci-full/*")]


def test_no_origin_warns(repo, names):
    standing(repo, None)
    assert states(repo)[0] == (
        "warn", "the repo has no remote origin with a URL, so no push of main there "
                "waits for CI on ci/* ci-full/*")


def test_a_shown_push_url_drops_its_userinfo(repo, names):
    url = "https:/" + "/alice:" + "pw" + "@example.test/operator/proj-a.git"
    standing(repo, url)
    detail = states(repo)[0][1]
    assert "pw" not in detail and "(https://example.test/operator/proj-a.git)" in detail


def test_a_leftover_word_required_fails_and_names_both_commands(repo, names):
    enabled(repo)
    git(repo, "config", "moxi-repo.piece.publishing/push.word-required", "true")
    [row] = [r for r in doctor.rows() if r.name == f"repo {repo}" and r.state == "fail"]
    assert "still holds word-required = true, which is retired" in row.detail
    assert row.fix == (f"moxi publishing approval enable {repo} --branch PATTERN, then "
                       f"moxi publishing enable {repo} --set word-required=")


def test_a_leftover_word_required_that_is_false_warns(repo, names):
    enabled(repo)
    git(repo, "config", "moxi-repo.piece.publishing/push.word-required", "false")
    assert states(repo)[0] == ("warn", "the push piece still holds the retired "
                                       "word-required (false), which nothing reads")
    assert states(repo)[-1][0] == "ok"

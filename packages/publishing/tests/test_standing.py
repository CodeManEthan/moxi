"""Standing branches and `main`'s CI rule in the push piece: the URL main's check reads CI
for, the pattern rule, each rule for a standing branch and its refusals, the log, the
retired word-required, and a push of `main` that waits for its check on a standing
branch."""

from __future__ import annotations

import json

import pytest
from conftest import CHECK_TOML, commit, git, public_repo, write

from moxi_publishing import push as piece

PIECE = "publishing/push"
GITHUB = "operator/proj-a"
URL = f"git@github.com:{GITHUB}.git"
ZERO = "0" * 40
#: USER may hold these and nothing else.
USER_BYTES = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")


def run(repo, *args, env=None):
    return git(repo, *args, check=False, env=env)


def refused(done, *texts):
    assert done.returncode != 0, done.stderr
    for text in texts:
        assert text in done.stderr, (text, done.stderr)


def passed(done):
    assert done.returncode == 0, done.stderr


def head(repo, rev="HEAD"):
    return git(repo, "rev-parse", rev).stdout.strip()


def log_lines():
    path = piece.log_path()
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def standing_repo(repo, *sets):
    return public_repo(repo, PIECE, sets=("main=main", "standing-branch=ci/*", *sets))


# -- the URL main's check reads CI for -----------------------------------------------

@pytest.mark.parametrize("url, reduced", [
    ("git@github.com:O-1/r.x_y-z.git", "O-1/r.x_y-z"),
    ("git@github.com:O/R", "O/R"),
    ("ssh://git@github.com/O/R.git", "O/R"),
    ("ssh://git@github.com:22/O/R.git", "O/R"),
    ("ssh://git@github.com:443/O/R", "O/R"),
    ("https://github.com/O/R.git", "O/R"),
    ("https://github.com/O/R", "O/R"),
    ("https://alice@github.com/O/R.git", "O/R"),
    ("https://a.b_c-9@github.com/O/R", "O/R"),
    # One trailing `.git` is stripped, no more.
    ("git@github.com:O/R.git.git", "O/R.git"),
])
def test_the_three_forms_reduce(url, reduced):
    assert piece.github_repo(url) == reduced


@pytest.mark.parametrize("url", [
    "git@github.com/O/R.git",               # no colon: git takes it for a directory
    "http://github.com/O/R.git",
    "https://github.com/O/R.git/",
    "https://github.com/O/R/",
    "git@github.com:O/R.git\n",
    "git@github.com:O/R\n",
    "ssh://git@github.com:O/R.git",         # a colon where the port goes, and no port
    "ssh://git@github.com:/O/R.git",
    "ssh://alice@github.com/O/R.git",
    "ssh://github.com/O/R.git",
    "alice@github.com:O/R.git",
    "git@GitHub.com:O/R.git",
    "git@GITHUB.COM:O/R.git",
    "https://GitHub.com/O/R.git",
    "ssh://git@Github.com/O/R.git",
    "git@github.com.evil.example:O/R.git",
    "https://evil.example/github.com/O/R.git",
    "https://evil.example#@github.com/O/R",
    "https://evil.example?@github.com/O/R",
    "https://evil.example\\@github.com/O/R",
    "https://u:p@github.com/O/R",
    "https://@github.com/O/R",
    "git@github.com:O/R/extra.git",
    "git@github.com:O_x/R.git",              # OWNER takes no `_` or `.`
    "git@github.com:O.x/R.git",
    "git@github.com:/R.git",
    " git@github.com:O/R.git",
    "/srv/git@github.com:O/R.git",
    "",
])
def test_anything_else_reduces_to_nothing(url):
    assert piece.github_repo(url) is None


@pytest.mark.parametrize("byte", range(256))
def test_https_user_takes_only_letters_digits_dot_underscore_and_dash(byte):
    char = chr(byte)
    url = f"https://u{char}x@github.com/O/R.git"
    assert (piece.github_repo(url) == "O/R") is (char in USER_BYTES), repr(char)


@pytest.mark.parametrize("byte", range(256))
def test_owner_and_repo_take_only_their_bytes(byte):
    char = chr(byte)
    owner_ok = char.isascii() and (char.isalnum() or char == "-")
    repo_ok = char.isascii() and (char.isalnum() or char in "._-")
    assert (piece.github_repo(f"git@github.com:a{char}b/R") == f"a{char}b/R") is owner_ok
    assert (piece.github_repo(f"git@github.com:O/a{char}b") == f"O/a{char}b") is repo_ok


# -- the pattern rule -----------------------------------------------------------------------

@pytest.mark.parametrize("value", ["ci/*", "ci-full/*", "a/b/*", "ci.x_y/*"])
def test_a_good_pattern(value):
    assert piece.standing_problem(value, "main", set()) is None


@pytest.mark.parametrize("value", [
    "ci*", "ci/", "ci", "*", "/*", "ci/**", "ci/x*", "ci//*", "c?/*", "c[i]/*", "c i/*",
    "c\\i/*", "ci/*/x", "ci\t/*",
])
def test_a_pattern_that_is_no_fixed_prefix_then_star_is_bad(value):
    assert "is not a fixed prefix ending in / followed by *" in piece.standing_problem(
        value, "main", set())


def test_a_pattern_that_matches_main_or_a_protected_branch_is_bad():
    assert "matches release/main, which is main or protected" in piece.standing_problem(
        "release/*", "release/main", set())
    assert "matches ci/keep" in piece.standing_problem("ci/*", "main", {"ci/keep"})
    # Only a branch with something after the prefix is under it.
    assert piece.standing_problem("ci/*", "ci", {"ci"}) is None


def test_a_standing_branch_is_a_branch_under_a_prefix_with_something_after_it():
    standing = piece.Standing({"standing-branch": ["ci/*", " ci-full/* ", "", "ci/*"]})
    assert standing.patterns == ["ci/*", "ci-full/*"] and standing.problems == []
    assert standing.match("refs/heads/ci/x") == "ci/*"
    assert standing.match("refs/heads/ci/a/b") == "ci/*"
    assert standing.match("refs/heads/ci-full/main") == "ci-full/*"
    for ref in ("refs/heads/ci/", "refs/heads/ci", "refs/heads/cix/y", "refs/tags/ci/x",
                "refs/notes/ci/x", "ci/x"):
        assert standing.match(ref) is None, ref


# -- each rule for a standing branch ---------------------------------------------------------

def test_a_standing_branch_goes_out_at_its_local_branchs_tip_and_may_be_forced(
        repo, names, remote):
    standing_repo(repo)
    git(repo, "switch", "-q", "-c", "topic")
    commit(repo, "Topic")
    passed(run(repo, "push", "-q", "origin", "topic:refs/heads/ci/topic"))
    git(repo, "commit", "-q", "--amend", "-m", "Topic, reworded")
    passed(run(repo, "push", "-q", "-f", "origin", "topic:refs/heads/ci/topic"))
    assert head(remote, "refs/heads/ci/topic") == head(repo, "topic")
    git(repo, "switch", "-q", "-c", "a/b")
    passed(run(repo, "push", "-q", "origin", "a/b:refs/heads/ci/a/b"))


def test_a_standing_branch_refuses_anything_but_its_local_branchs_tip(repo, names, remote):
    standing_repo(repo)
    git(repo, "switch", "-q", "-c", "topic")
    commit(repo, "Topic")
    behind = head(repo, "HEAD~1")
    refused(run(repo, "push", "-q", "origin", "HEAD~1:refs/heads/ci/topic"),
            f"refs/heads/ci/topic: a branch under ci/* goes out only at the tip of "
            f"refs/heads/topic, which is at {head(repo)[:12]}, not {behind[:12]}")
    refused(run(repo, "push", "-q", "origin", "topic:refs/heads/ci/elsewhere"),
            "the tip of refs/heads/elsewhere, which does not exist here")
    git(repo, "tag", "-a", "-m", "an annotated tag", "docs/t", "topic")
    refused(run(repo, "push", "-q", "origin", "docs/t:refs/heads/ci/topic"),
            "is a tag object; a standing branch takes a commit")
    assert run(remote, "rev-parse", "--verify", "-q", "refs/heads/ci/topic").returncode != 0


@pytest.mark.parametrize("lookalike", ["refs/tags/refs/heads/elsewhere",
                                       "refs/remotes/refs/heads/elsewhere",
                                       "refs/refs/heads/elsewhere"])
def test_a_standing_branch_reads_only_a_local_branch_of_its_name(
        repo, names, remote, lookalike):
    # git's lookup of `refs/heads/elsewhere` falls back to these when there is no branch
    # `elsewhere`; the piece reads the branch itself.
    standing_repo(repo)
    git(repo, "switch", "-q", "-c", "topic")
    commit(repo, "Topic")
    tip = head(repo)
    git(repo, "update-ref", lookalike, tip)
    assert head(repo, "refs/heads/elsewhere") == tip
    refused(run(repo, "push", "-q", "origin", f"{tip}:refs/heads/ci/elsewhere"),
            "the tip of refs/heads/elsewhere, which does not exist here")
    git(repo, "branch", "elsewhere", "main")
    refused(run(repo, "push", "-q", "origin", f"{tip}:refs/heads/ci/elsewhere"),
            f"refs/heads/elsewhere, which is at {head(repo, 'main')[:12]}")
    git(repo, "branch", "-f", "elsewhere", tip)
    passed(run(repo, "push", "-q", "origin", f"{tip}:refs/heads/ci/elsewhere"))


def test_a_standing_branch_takes_no_branch_below_its_name(repo, names, remote):
    standing_repo(repo)
    git(repo, "switch", "-q", "-c", "x/y")
    commit(repo, "Below")
    refused(run(repo, "push", "-q", "origin", "x/y:refs/heads/ci/x"),
            "the tip of refs/heads/x, which does not exist here")


def test_a_standing_branch_is_deleted_whatever_its_tip_with_no_fetch(repo, names, remote):
    standing_repo(repo)
    git(repo, "switch", "-q", "-c", "topic")
    commit(repo, "Never merged")
    passed(run(repo, "push", "-q", "origin", "topic", "topic:refs/heads/ci/topic"))
    git(repo, "update-ref", "-d", "refs/remotes/origin/main")
    passed(run(repo, "push", "-q", "origin", ":refs/heads/ci/topic"))
    # Another branch still waits for its merge into main, and so for a fetch here.
    refused(run(repo, "push", "-q", "origin", ":topic"), "fetch first")


def test_what_a_standing_branch_sends_is_scanned_whole(repo, names, remote):
    standing_repo(repo)
    git(repo, "switch", "-q", "-c", "topic")
    commit(repo, "A name", text="bobsecret\n")
    named = head(repo)
    commit(repo, "Clean", text="")
    refused(run(repo, "push", "-q", "origin", "topic:refs/heads/ci/topic"),
            f"{named[:12]} note.md:1: private-names/private-name")
    git(repo, "branch", "bobsecret", "main")
    done = run(repo, "push", "-q", "origin", "bobsecret:refs/heads/ci/bobsecret")
    refused(done, "the ref name refs/heads/ci/[private-name]: private-names/")
    assert "goes out only at the tip" not in done.stderr


def test_a_forced_standing_branch_sends_what_the_remote_lacks(repo, names, remote):
    standing_repo(repo)
    git(repo, "switch", "-q", "-c", "topic")
    commit(repo, "One")
    passed(run(repo, "push", "-q", "origin", "topic:refs/heads/ci/topic"))
    assert log_lines()[-1]["commits"] == [head(repo)]
    git(repo, "reset", "-q", "--hard", "main")
    commit(repo, "Two")
    passed(run(repo, "push", "-q", "-f", "origin", "topic:refs/heads/ci/topic"))
    assert log_lines()[-1]["commits"] == [head(repo)]


@pytest.mark.parametrize("sets, problem", [
    (("standing-branch=ci*",), "'ci*' is not a fixed prefix"),
    (("standing-branch=*",), "'*' is not a fixed prefix"),
    (("protected=ci/keep",), "'ci/*' matches ci/keep, which is main or protected"),
])
def test_a_bad_pattern_refuses_every_push(repo, names, remote, sets, problem):
    standing_repo(repo)
    for key_value in sets:
        key, _, value = key_value.partition("=")
        git(repo, "config", "--add", f"moxi-repo.piece.publishing/push.{key}", value)
    git(repo, "branch", "topic")
    for args in (("topic",), ("topic:refs/heads/ci/topic",), ("main",)):
        refused(run(repo, "push", "-q", "origin", *args), problem,
                "no push goes out while standing-branch holds a value that is not a good "
                "pattern", "--set 'standing-branch=ci/*'")
    assert log_lines() == []


def test_a_hand_edited_pattern_cannot_open_main(repo, names, remote):
    public_repo(repo, PIECE, sets=("main=main",))
    git(repo, "config", "--add", "moxi-repo.piece.publishing/push.main", "release/main")
    git(repo, "config", "--add", "moxi-repo.piece.publishing/push.standing-branch",
        "release/*")
    git(repo, "branch", "release/main")
    refused(run(repo, "push", "-q", "origin", "release/main"),
            "'release/*' matches release/main")


def test_a_tag_under_a_prefix_is_no_standing_branch(repo, names, remote):
    standing_repo(repo)
    git(repo, "tag", "ci/x")
    refused(run(repo, "push", "-q", "origin", "refs/tags/ci/x"), "free-ref")


def test_the_log_names_the_pattern_of_each_standing_ref(repo, names, remote):
    standing_repo(repo, "standing-branch=ci-full/*")
    git(repo, "branch", "topic")
    passed(run(repo, "push", "-q", "origin", "topic", "topic:refs/heads/ci-full/topic"))
    refs = {r["remote_ref"]: r for r in log_lines()[-1]["refs"]}
    assert refs["refs/heads/ci-full/topic"]["standing"] == "ci-full/*"
    assert "standing" not in refs["refs/heads/topic"]
    assert log_lines()[-1]["word"] is None


# -- the push piece asks for no word; a leftover word-required refuses ------------------------

def hook(repo, url, *lines, word=None, monkeypatch, leftover=False):
    """The push piece as git's pre-push hook runs it in `repo`, pushing `lines` (each
    `local_ref local_sha remote_ref remote_sha`) to `origin` at `url`."""
    from moxi_repo.api import HookContext

    monkeypatch.chdir(repo)
    if word is None:
        monkeypatch.delenv(piece.WORD_ENV, raising=False)
    else:
        monkeypatch.setenv(piece.WORD_ENV, word)
    settings = {"main": ["main"], "standing-branch": ["ci/*"]}
    if leftover:
        settings["word-required"] = ["true"]
    return piece.pre_push(HookContext("pre-push", args=("origin", url),
                                      stdin=("\n".join(lines) + "\n").encode(),
                                      settings=settings))


@pytest.fixture
def standing(repo, names, remote):
    standing_repo(repo)
    git(repo, "config", "moxi-publishing.github", GITHUB)
    git(repo, "branch", "topic")
    return repo


def ci_line(repo, branch="topic"):
    return f"refs/heads/{branch} {head(repo, branch)} refs/heads/ci/{branch} {ZERO}"


def said(outcome):
    return "\n".join(outcome.lines)


@pytest.mark.parametrize("url", [URL, "git@example.test:operator/proj-a.git",
                                 "https://evil.example#@github.com/operator/proj-a"])
def test_a_standing_push_needs_no_word_wherever_it_goes(standing, monkeypatch, url):
    outcome = hook(standing, url, ci_line(standing), monkeypatch=monkeypatch)
    assert outcome.kind == "ok", said(outcome)
    last = log_lines()[-1]
    assert last["word"] is None and last["refs"][0]["standing"] == "ci/*"


def test_a_push_of_another_branch_needs_no_word_from_the_push_piece(standing, monkeypatch):
    other = f"refs/heads/topic {head(standing)} refs/heads/topic {ZERO}"
    assert hook(standing, URL, other, monkeypatch=monkeypatch).kind == "ok"


def test_a_word_given_is_logged_as_given(standing, monkeypatch):
    assert hook(standing, URL, ci_line(standing), word="comms:7",
                monkeypatch=monkeypatch).kind == "ok"
    assert log_lines()[-1]["word"] == "comms:7"
    assert hook(standing, URL, ci_line(standing), word="  ",
                monkeypatch=monkeypatch).kind == "ok"
    assert log_lines()[-1]["word"] is None


def test_a_leftover_word_required_refuses_every_push(standing, monkeypatch):
    for word in (None, "comms:1"):
        outcome = hook(standing, URL, ci_line(standing), word=word, leftover=True,
                       monkeypatch=monkeypatch)
        assert outcome.kind == "refuse"
        assert "still holds word-required = true, which is retired" in said(outcome)
        assert f"moxi publishing approval enable {standing} --branch PATTERN" in said(
            outcome)
        assert f"moxi publishing enable {standing} --set word-required=" in said(outcome)
    assert log_lines() == []


def test_a_leftover_word_required_refuses_a_real_push(standing):
    git(standing, "config", "moxi-repo.piece.publishing/push.word-required", "true")
    refused(run(standing, "push", "-q", "origin", "topic:refs/heads/ci/topic"),
            "which is retired")


def test_a_standing_push_still_goes_out_only_at_the_tip(standing, monkeypatch):
    base = head(standing)
    git(standing, "switch", "-q", "topic")
    commit(standing, "Ahead")
    line = f"refs/heads/topic {base} refs/heads/ci/topic {ZERO}"
    outcome = hook(standing, URL, line, monkeypatch=monkeypatch)
    assert outcome.kind == "refuse"
    assert "goes out only at the tip of refs/heads/topic" in said(outcome)


def test_a_standing_push_is_still_scanned(standing, monkeypatch):
    git(standing, "switch", "-q", "topic")
    commit(standing, "A name", text="bobsecret\n")
    named = head(standing)
    outcome = hook(standing, URL, ci_line(standing), monkeypatch=monkeypatch)
    assert outcome.kind == "refuse"
    assert f"{named[:12]} note.md:1: private-names/private-name" in said(outcome)
    assert log_lines() == []


# -- main moves only to a commit whose check passed on a standing branch -------------------

SSH = """#!/bin/sh
# git's ssh (GIT_SSH_VARIANT=simple: the host, then the command): run the command asked of
# github.com against the bare remote instead.
cmd=${2%% *}
exec git "${cmd#git-}" "$BARE"
"""


def at_github(repo, remote, monkeypatch, url=URL):
    """`repo`'s `origin` at the GitHub URL `url`, which git reaches through a stand-in ssh
    that serves `remote`; git hands the hook `url`."""
    ssh = remote.parent / "ssh"
    ssh.write_text(SSH.replace("$BARE", str(remote)), encoding="utf-8")
    ssh.chmod(0o755)
    monkeypatch.setenv("GIT_SSH_COMMAND", str(ssh))
    monkeypatch.setenv("GIT_SSH_VARIANT", "simple")
    git(repo, "remote", "set-url", "origin", url)
    return repo


@pytest.fixture
def checked(repo, names, served):
    """`repo` naming its check in a commit; GitHub faked, with no runs."""
    write(repo, "pyproject.toml", CHECK_TOML)
    git(repo, "add", "pyproject.toml")
    git(repo, "commit", "-q", "-m", "Name the check")
    return repo


@pytest.fixture
def mainline(checked, remote, served, monkeypatch):
    """`checked` on `origin` at its GitHub URL, with the push piece on,
    `standing-branch=ci/*`, its GitHub repo set, and one commit `origin` lacks."""
    standing_repo(checked)
    git(checked, "config", "moxi-publishing.github", GITHUB)
    at_github(checked, remote, monkeypatch)
    commit(checked, "Add")
    return checked


def test_main_moves_to_a_commit_whose_check_passed_on_a_standing_branch(
        mainline, remote, served):
    tip = head(mainline)
    refused(run(mainline, "push", "-q", "origin", "main"),
            f"refs/heads/main: no ci.yml run from a push to ci/* at {tip[:12]} has linux "
            "concluded success",
            "git push origin +main:refs/heads/ci/main",
            "git push origin <commit>:refs/heads/main")
    served.green(tip, branch="ci/main")
    passed(run(mainline, "push", "-q", "origin", "main"))
    assert served.queries[-1] == "per_page=100"   # the jobs of the run's latest attempt
    assert head(remote, "refs/heads/main") == tip


@pytest.mark.parametrize("job, conclusion, event, branch", [
    ("linux", "success", "push", "main"),
    ("linux", "success", "push", "topic"),
    ("linux", "success", "push", "ci"),
    ("linux", "success", "pull_request", "ci/main"),
    ("linux", "failure", "push", "ci/main"),
    ("linux", "cancelled", "push", "ci/main"),
    ("linuxy", "success", "push", "ci/main"),
    ("web", "success", "push", "ci/main"),
])
def test_no_other_run_lets_main_move(mainline, served, job, conclusion, event, branch):
    served.green(head(mainline), job, conclusion, event=event, branch=branch)
    refused(run(mainline, "push", "-q", "origin", "main"), "no ci.yml run")


def test_a_matrix_job_and_a_second_pattern_count(mainline, served):
    git(mainline, "config", "--add", "moxi-repo.piece.publishing/push.standing-branch",
        "ci-full/*")
    served.green(head(mainline), "linux (ubuntu-latest)", branch="ci-full/main")
    passed(run(mainline, "push", "-q", "origin", "main"))


def test_main_may_move_to_an_earlier_commit_that_passed(mainline, served):
    passed_at = head(mainline)
    served.green(passed_at, branch="ci/main")
    commit(mainline, "Later")
    refused(run(mainline, "push", "-q", "origin", "main"), "no ci.yml run")
    passed(run(mainline, "push", "-q", "origin", f"{passed_at}:refs/heads/main"))


def test_main_moved_from_any_local_ref_waits_for_the_check(mainline, served):
    tip = head(mainline)
    git(mainline, "branch", "topic")
    for source in (tip, "topic"):
        refused(run(mainline, "push", "-q", "origin", f"{source}:refs/heads/main"),
                f"no ci.yml run from a push to ci/* at {tip[:12]}")


def test_a_run_that_gives_another_commit_does_not_count(mainline, served):
    tip = head(mainline)
    served.runs[tip] = [{"event": "push", "branch": "ci/main", "head_sha": "f" * 40,
                         "jobs": [("linux", "success")]}]
    refused(run(mainline, "push", "-q", "origin", "main"), "no ci.yml run")


def test_main_without_its_settings_is_refused(mainline, served):
    write(mainline, "pyproject.toml", '[tool.moxi-publishing]\ncheck-workflow = "ci.yml"\n')
    git(mainline, "commit", "-q", "-am", "No job")
    served.green(head(mainline), branch="ci/main")
    refused(run(mainline, "push", "-q", "origin", "main"),
            "[tool.moxi-publishing] at", "has no check-job")
    git(mainline, "rm", "-q", "pyproject.toml")
    git(mainline, "commit", "-q", "-m", "No file")
    served.green(head(mainline), branch="ci/main")
    refused(run(mainline, "push", "-q", "origin", "main"), "pyproject.toml is missing")


@pytest.mark.parametrize("broken", ["/repos/operator/proj-a/actions/workflows",
                                    "/repos/operator/proj-a/actions/runs"])
def test_a_failed_read_of_github_refuses_main(mainline, served, broken):
    served.green(head(mainline), branch="ci/main")
    served.broken.add(broken)
    refused(run(mainline, "push", "-q", "origin", "main"), "can't be read: HTTP 500",
            "can't be told")


def test_a_leftover_word_required_refuses_main_even_after_its_check(mainline, served):
    git(mainline, "config", "moxi-repo.piece.publishing/push.word-required", "true")
    served.green(head(mainline), branch="ci/main")
    refused(run(mainline, "push", "-q", "origin", "main", env={piece.WORD_ENV: "comms:1"}),
            "which is retired")


def test_only_a_push_of_main_waits_for_the_check(mainline, served):
    git(mainline, "branch", "topic")
    passed(run(mainline, "push", "-q", "origin", "topic"))
    passed(run(mainline, "push", "-q", "origin", "topic:refs/heads/ci/topic"))
    refused(run(mainline, "push", "-q", "origin", ":main"), "main is never deleted")
    assert not any("/actions/" in path for path in served.asked)


# -- where main's check does not apply: judged as before the setting existed ---------------

def no_ci_read(repo, remote, served):
    """A push of `main` with no run anywhere passes, and GitHub's runs are never asked."""
    tip = head(repo)
    passed(run(repo, "push", "-q", "origin", "main"))
    assert head(remote, "refs/heads/main") == tip
    assert not any("/actions/" in path for path in served.asked)


def test_main_in_a_repo_with_no_standing_pattern_reads_no_ci(
        checked, remote, served, monkeypatch):
    public_repo(checked, PIECE, sets=("main=main",))
    git(checked, "config", "moxi-publishing.github", GITHUB)
    at_github(checked, remote, monkeypatch)
    commit(checked, "Add")
    no_ci_read(checked, remote, served)
    # It still goes forward only.
    git(checked, "reset", "-q", "--hard", "HEAD~1")
    commit(checked, "Elsewhere")
    refused(run(checked, "push", "-q", "-f", "origin", "main"), "would rewrite main")


@pytest.mark.parametrize("url", [None, "git@example.test:operator/proj-a.git",
                                 "git@github.com:operator/other.git",
                                 "ssh://git@github.com/operator/proj-a.git/"])
def test_main_to_any_other_url_reads_no_ci(mainline, remote, served, monkeypatch, url):
    if url is None:   # the bare remote's own path
        git(mainline, "remote", "set-url", "origin", str(remote))
    else:
        at_github(mainline, remote, monkeypatch, url)
    no_ci_read(mainline, remote, served)


def test_main_reads_no_ci_while_the_github_repo_is_unset(mainline, remote, served):
    git(mainline, "config", "--unset", "moxi-publishing.github")
    no_ci_read(mainline, remote, served)


def test_main_ci_refuses_with_no_pattern_or_no_github_repo(checked):
    from moxi_publishing import tags

    run_git, tip = tags.repo_git(str(checked)), head(checked)
    [why] = piece.main_ci(run_git, tip, main="main", patterns=[], github=GITHUB,
                          root=str(checked))
    assert "the push piece's standing-branch setting names none" in why
    assert "--set 'standing-branch=ci/*'" in why
    [why] = piece.main_ci(run_git, tip, main="main", patterns=["ci/*"], github=None)
    assert "moxi-publishing.github is unset" in why

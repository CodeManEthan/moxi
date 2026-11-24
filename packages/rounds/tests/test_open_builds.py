"""The open builds of every project, read from git: what the read API's `builds` gives,
with each build's state, whether it is idle, and the builds it shares commits with."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from moxi_rounds import api, dropping, errors, log, opening, paths
from moxi_rounds.cli import main

A = "proj-a/a-round"
B = "proj-a/b-round"
T1 = "2026-01-02T03:04:01Z"
#: The fixture repo dates its commits a second apart from 2023-11-14T22:13:20Z.
SOON = "2023-11-20T00:00:00Z"
LATER = "2023-12-01T00:00:00Z"


@pytest.fixture
def repo(git_repo, tmp_path):
    """The project `proj-a`, whose root is the test's git repo, and the project
    `proj-b`, whose root is a folder with no repo; each keeps its rounds under a notes
    folder of its own. Returns the repo."""
    from moxi import cli

    other = tmp_path / "roots" / "proj-b"
    other.mkdir(parents=True)
    for key, root in (("proj-a", git_repo.root), ("proj-b", other)):
        assert cli.main(["projects", "add", key, "--root", str(root)]) == 0
        assert cli.main(["projects", "set", key, "--notes-root",
                         str(tmp_path / f"notes-{key}")]) == 0
    return git_repo


@pytest.fixture
def logs(repo, tmp_path):
    """The logs of two open rounds of `proj-a`, by name."""
    found = {}
    for name in (A, B):
        opening.open_round(name, description="a placeholder round", by="planner",
                           flow="fix", at=T1)
        slug = name.split("/")[1]
        found[name] = log.round_log(tmp_path / "notes-proj-a" / "rounds" / slug)
    return found


def add(path: Path, type_: str, **fields) -> None:
    log.append(path, type_, fields, by="planner")


def commission(path: Path, stage="B1", *, branch="work", base, repo="proj-a",
               target="main") -> None:
    add(path, "build.commissioned", stage=stage, repo=repo, branch=branch, base=base,
        target=target, builder="coder")


def work_on(git_repo, branch="work", name="feature.txt", start="main") -> str:
    git_repo.switch(branch, new_from=start)
    return git_repo.commit(name)


def listed(found: dict) -> list[tuple]:
    return [(one["round"], one["stage"], one["state"]) for one in found["builds"]]


# -- what is listed ----------------------------------------------------------------------

def test_an_open_build_is_given_with_its_state_read_from_git(repo, logs):
    base = repo.rev("main")
    commission(logs[A], base=base)
    tip = work_on(repo)

    found = api.builds()

    assert found["repo"] is None and found["unreadable"] == [] and found["no_folder"] == []
    assert found["builds"] == [{
        "round": A, "stage": "B1", "repo": "proj-a", "branch": "work", "target": "main",
        "base": base, "state": "building", "by": "off the target",
        "why": "work holds work main does not", "head": tip, "target_head": base,
        "target_before": base, "merge_base": base, "has_own_commits": False,
        "hand_merge": None, "idle": True, "drop_by": "operator", "shares": [],
        "problem": None}]
    assert json.loads(json.dumps(found)) == found
    # The read API's docstring names every key a build's data holds.
    named = re.search(r'Each of `builds` is `\{(.*?)\}`', api.builds.__doc__, re.S)
    assert re.findall(r'"(\w+)"', named.group(1)) == list(found["builds"][0])


def test_only_the_last_open_build_of_each_stage_of_an_open_round_is_listed(repo, logs):
    base = repo.rev("main")
    commission(logs[A], base=base, branch="first")
    commission(logs[A], base=base, branch="again")
    commission(logs[A], "B2", base=base, branch="dropped")
    add(logs[A], "build.dropped", stage="B2", by="lead", record="comms:1234567")
    commission(logs[A], "B3", base=base, branch="finished")
    add(logs[A], "finish.done", stage="B3", tip=base)
    commission(logs[B], base=base, branch="in-a-dropped-round")
    dropping.drop(B, by="operator", record="comms:7654321", run_by="planner")

    assert [(one["round"], one["stage"], one["branch"])
            for one in api.builds()["builds"]] == [(A, "B1", "again")]


def test_builds_are_kept_to_one_repo_when_it_is_named(repo, logs):
    base = repo.rev("main")
    commission(logs[A], base=base)
    commission(logs[B], base=base, repo="proj-b")

    assert [one["round"] for one in api.builds("PROJ-A")["builds"]] == [A]
    assert api.builds("proj-a")["repo"] == "proj-a"
    assert [one["round"] for one in api.builds("proj-b")["builds"]] == [B]
    with pytest.raises(errors.UnknownProject):
        api.builds("no-project")
    with pytest.raises(errors.BadArgument):
        api.builds(" ")


# -- idle ---------------------------------------------------------------------------------

def test_a_building_build_reads_idle_once_its_head_is_older_than_idle_days(repo, logs):
    from moxi_rounds import open_builds

    commission(logs[A], base=repo.rev("main"))
    work_on(repo)

    assert [one.idle for one in open_builds.read(now=SOON).builds] == [False]
    assert [one.idle for one in open_builds.read(now=LATER).builds] == [True]


def test_idle_days_is_read_from_the_round_s_project(repo, logs, write_config):
    from moxi_rounds import open_builds

    write_config("""
        [projects.proj-a]
        idle_days = 30
    """)
    commission(logs[A], base=repo.rev("main"))
    work_on(repo)

    assert [one.idle for one in open_builds.read(now=LATER).builds] == [False]


def test_only_a_building_build_reads_idle(repo, logs):
    from moxi_rounds import open_builds

    commission(logs[A], base=repo.rev("main"))
    repo.switch("work", new_from="main")

    [one] = open_builds.read(now=LATER).builds
    assert (one.status.state, one.idle) == ("empty", False)


def _signed_commit(git_repo, tmp_path, name: str) -> str:
    """Commit `name` signed with a fresh SSH key, in a repo whose config shows each
    commit's signature check wherever git prints a commit."""
    key = tmp_path / "signing-key"
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "coder",
                    "-f", str(key)], check=True, capture_output=True)
    git_repo.git("config", "gpg.format", "ssh")
    git_repo.git("config", "user.signingkey", str(key))
    git_repo.git("config", "log.showSignature", "true")
    (git_repo.root / name).write_text(name + "\n", encoding="utf-8")
    git_repo.git("add", "--", name)
    git_repo.git("commit", "-q", "-S", "-m", f"change {name}")
    return git_repo.rev()


@pytest.mark.skipif(shutil.which("ssh-keygen") is None, reason="needs ssh-keygen")
def test_a_signed_head_shown_with_its_signature_still_reads_idle(repo, logs, tmp_path):
    from moxi_rounds import open_builds

    commission(logs[A], base=repo.rev("main"))
    commission(logs[B], base=repo.rev("main"), branch="other")
    repo.switch("work", new_from="main")
    _signed_commit(repo, tmp_path, "feature.txt")
    repo.switch("other", new_from="main")
    repo.commit("other.txt")

    found = open_builds.read(now=LATER).builds

    assert [(one.status.state, one.idle, one.problem) for one in found] == [
        ("building", True, None), ("building", True, None)]


@pytest.mark.skipif(shutil.which("ssh-keygen") is None, reason="needs ssh-keygen")
def test_a_signed_commit_s_date_is_read_whatever_git_shows_with_it(git_repo, tmp_path):
    from moxi_rounds.gitreads import Git

    signed = _signed_commit(git_repo, tmp_path, "feature.txt")

    assert Git(git_repo.root).committed_at(signed) == int(
        git_repo.git("log", "-1", "--no-show-signature", "--format=%ct", signed))


def test_a_commit_git_lacks_has_no_date_and_says_so(git_repo):
    from moxi_rounds.gitreads import Git, GitError

    with pytest.raises(GitError):
        Git(git_repo.root).committed_at("0" * 40)


# -- sharing and landings across rounds ---------------------------------------------------

def test_builds_of_two_rounds_that_share_commits_name_each_other(repo, logs):
    base = repo.rev("main")
    commission(logs[A], base=base)
    commission(logs[B], base=base, branch="other")
    tip = work_on(repo)
    repo.git("branch", "other", tip)

    found = api.builds()["builds"]

    assert [(one["round"], one["state"], one["shares"]) for one in found] == [
        (A, "building", [{"round": B, "stage": "B1", "relation": "overlaps"}]),
        (B, "building", [{"round": A, "stage": "B1", "relation": "overlaps"}])]


def test_builds_of_one_repo_into_two_targets_never_share(repo, logs):
    base = repo.rev("main")
    repo.git("branch", "release", base)
    commission(logs[A], base=base)
    commission(logs[B], base=base, branch="other", target="release")
    tip = work_on(repo)
    repo.git("branch", "other", tip)

    assert [one["shares"] for one in api.builds()["builds"]] == [[], []]


def test_a_landing_another_round_recorded_is_read(repo, logs):
    base = repo.rev("main")
    commission(logs[A], base=base)
    commission(logs[B], base=base, branch="other")
    tip = work_on(repo, branch="other")
    repo.git("branch", "work", tip)
    repo.switch("main")
    landing = repo.merge("other")
    add(logs[B], "finish.action", stage="B1", tip=tip, id="merge", kind="merge",
        state="ok", landing=landing, target_before=base)
    add(logs[B], "finish.done", stage="B1", tip=tip)

    [one] = api.builds()["builds"]
    assert (one["round"], one["state"], one["by"]) == (A, "empty", "nothing of its own")


# -- what can't be read -------------------------------------------------------------------

def test_a_build_that_can_t_be_read_is_given_with_its_problem_and_the_rest_are_read(
        repo, logs):
    base = repo.rev("main")
    commission(logs[A], base=base, target="gone-branch")
    commission(logs[A], "B2", base=base, repo="proj-b")
    commission(logs[A], "B3", base=base, repo="proj-c")
    commission(logs[B], base=base)

    found = {one["stage"] if one["round"] == A else "other": one
             for one in api.builds()["builds"]}

    assert found["other"]["state"] == "gone" and found["other"]["problem"] is None
    for stage in ("B1", "B2", "B3"):
        assert found[stage]["state"] is None and found[stage]["head"] is None
        assert found[stage]["problem"]["reason"] and found[stage]["problem"]["fix"]
    assert "gone-branch" in found["B1"]["problem"]["reason"]
    assert "proj-c" in found["B3"]["problem"]["reason"]


def test_a_round_whose_log_can_t_be_read_is_named_and_its_builds_left_out(repo, logs):
    commission(logs[A], base=repo.rev("main"))
    commission(logs[B], base=repo.rev("main"))
    with logs[B].open("ab") as fh:
        fh.write(b"not an event\n")

    found = api.builds()

    assert listed(found) == [(A, "B1", "gone")]
    [reason] = found["unreadable"]
    assert str(logs[B]) in reason


# -- a read writes nothing ----------------------------------------------------------------

def everything(tmp_path):
    return {str(path): (path.read_bytes(), path.stat().st_mtime_ns)
            for checked_dir in {tmp_path, Path.home(), paths.data_dir(), paths.state_dir()}
            for path in sorted(checked_dir.rglob("*")) if path.is_file()}


@pytest.mark.parametrize("fresh", [False, True])
def test_the_read_changes_no_file_and_gives_the_same_from_the_index_or_the_logs(
        repo, logs, tmp_path, capsys, fresh):
    base = repo.rev("main")
    commission(logs[A], base=base)
    commission(logs[B], base=base, branch="other")
    work_on(repo)
    add(logs[A], "check.ran", stage="B1", step="check", commit=repo.rev(),
        merge_base=base, argv=["true"], exit_code=0, duration_s=1.0)
    from_logs = api.builds()["builds"]
    if fresh:
        assert main(["list"]) == 0
        capsys.readouterr()
    before = everything(tmp_path)

    assert api.builds()["builds"] == from_logs
    assert everything(tmp_path) == before

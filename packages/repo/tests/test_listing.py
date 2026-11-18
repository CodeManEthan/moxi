"""What a listing reads beside the text it prints: what kind of problem it names
(`Problem.code`, its piece and event, whether a refresh fixes it), the path of the user's
own git hook that runs after the pieces (`existing_hook`), and the events where only his
own hook runs (`own_hooks`). The text each problem prints is unchanged."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from moxi_repo import api, gitconf, shim
from tests.conftest import NEEDS_CONFIG_GIT, POSIX_ONLY, git

pytestmark = POSIX_ONLY


def listed(repo: Path) -> dict:
    [found] = api.status(repo)
    return found


def problem(repo: Path | None, code: str) -> api.Problem:
    """The one problem of `code` the listing names, of `repo` or of the whole index."""
    found = [p for r in (api.status() if repo is None else [listed(repo)])
             for p in r["problems"] if p.code == code]
    assert len(found) == 1, [(p.code, str(p)) for r in api.status() for p in r["problems"]]
    return found[0]


def parts(p: api.Problem) -> tuple:
    return (p.code, p.piece, p.event, p.refresh, p.state)


def hook_file(path: Path, text: str = "#!/bin/sh\nexit 0\n", *, runnable: bool = True) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755 if runnable else 0o644)
    return path


def test_every_code_is_named_once():
    assert len(set(api.PROBLEM_CODES)) == len(api.PROBLEM_CODES) == 15


def test_a_problem_still_prints_as_before_and_carries_no_kind_by_default():
    p = api.Problem("it broke", "moxi repo git-hooks refresh /r")
    assert str(p) == "it broke; run: moxi repo git-hooks refresh /r"
    assert (p.code, p.piece, p.event, p.refresh) == (None, None, None, False)


# -- the index's own repos -------------------------------------------------------------------

def test_a_repo_whose_work_tree_is_gone_is_gone(demo):
    repo = demo.repo()
    api.add(repo, "demo/gate")
    shutil.rmtree(repo)
    p = problem(None, "gone")
    assert parts(p) == ("gone", None, None, False, "warn")
    assert p.detail == api.gone_text(str(repo))
    assert p.fix == f"moxi repo git-hooks remove {repo}"


def test_a_repo_whose_git_dir_is_gone_is_gone(demo):
    repo = demo.repo()
    api.add(repo, "demo/gate")
    shutil.rmtree(repo / ".git")
    assert problem(None, "gone").detail == api.gone_text(str(repo))


def test_a_repo_whose_git_config_is_broken_is_unreadable_with_gits_reason(demo):
    repo = demo.repo()
    api.add(repo, "demo/gate")
    (repo / ".git" / "config").write_text("[core\n\tbroken = \n", encoding="utf-8")
    p = problem(None, "unreadable")
    assert parts(p) == ("unreadable", None, None, False, "warn")
    assert p.detail.startswith(f"{repo}: ") and "no longer a git work tree" not in p.detail
    assert p.fix == f"moxi repo git-hooks remove {repo}"


def test_a_work_tree_now_holding_another_repo_is_unreadable_with_todays_words(demo):
    repo, gitdir = demo.tmp / "split", demo.tmp / "split.git"
    git(demo.tmp, "init", "-q", "--separate-git-dir", str(gitdir), str(repo))
    api.add(repo, "demo/gate")
    (repo / ".git").unlink()
    git(repo, "init", "-q")
    p = problem(None, "unreadable")
    assert p.detail.startswith(api.gone_text(str(repo)))


# -- a repo's own problems -------------------------------------------------------------------

def test_a_piece_no_module_declares_names_its_piece_and_event(demo):
    repo = demo.repo()
    api.add(repo, "demo/gate")
    for var, value in (("event", "commit-msg"), ("target", "gone.mod:fn"),
                       ("fail", "closed"), ("order", "10"), ("enabled", "true")):
        git(repo, "config", gitconf.piece_key("gone/check", var), value)
    assert parts(problem(repo, "no-module")) == (
        "no-module", "gone/check", "commit-msg", False, "fail")


def test_a_piece_on_no_git_event_names_its_piece_and_the_name_it_gives(demo):
    repo = demo.repo()
    api.add(repo, "demo/gate")
    git(repo, "config", gitconf.piece_key("demo/gate", "event"), "pre-nothing")
    assert parts(problem(repo, "not-an-event")) == (
        "not-an-event", "demo/gate", "pre-nothing", False, "warn")


def test_a_git_too_old_is_fixed_by_a_refresh_only_when_not_pinned_to_config(demo):
    pinned, unpinned = demo.repo("pinned"), demo.repo("unpinned")
    demo.mp.setattr(gitconf, "git_version", lambda: (2, 56, 0))
    api.add(pinned, ["demo/gate"], mode="config")
    api.add(unpinned, ["demo/gate"])
    demo.mp.setattr(gitconf, "git_version", lambda: (2, 43, 0))
    p = problem(pinned, "git-too-old")
    assert (p.refresh, p.event, p.piece) == (False, None, None)
    assert "--mode shim" in p.fix
    p = problem(unpinned, "git-too-old")
    assert p.refresh is True and p.fix == f"moxi repo git-hooks refresh {unpinned}"


@NEEDS_CONFIG_GIT
def test_a_config_mode_event_with_no_entry_is_fixed_by_a_refresh(demo):
    repo = demo.repo()
    api.add(repo, ["demo/gate"], mode="config")
    git(repo, "config", "--remove-section", f"hook.{gitconf.HOOK_PREFIX}pre-commit")
    assert parts(problem(repo, "no-entry")) == ("no-entry", None, "pre-commit", True, "fail")


@pytest.mark.parametrize("mode", [pytest.param("config", marks=NEEDS_CONFIG_GIT), "shim"])
def test_the_guards_paths_each_have_a_code_fixed_by_a_refresh(demo, mode):
    repo = demo.repo()
    gone = str(demo.tmp / "gone-env" / "bin" / "python")
    with demo.mp.context() as mp:
        mp.setattr(gitconf, "interpreter", lambda: gone)
        api.add(repo, ["demo/gate"], mode=mode)
    assert parts(problem(repo, "interpreter-missing")) == (
        "interpreter-missing", None, "pre-commit", True, "fail")
    with demo.mp.context() as mp:
        mp.setattr(gitconf, "runner_file", lambda: str(demo.tmp / "gone-run.py"))
        api.refresh(repo)
    assert parts(problem(repo, "runner-missing")) == (
        "runner-missing", None, "pre-commit", True, "fail")
    with demo.mp.context() as mp:
        mp.setattr(gitconf, "interpreter", lambda: str(demo.hook_env["bare"]))
        api.refresh(repo)
    assert parts(problem(repo, "other-environment")) == (
        "other-environment", None, "pre-commit", True, "warn")


def test_a_shim_another_tool_moved_aside_and_runs_is_ok_and_not_a_refresh(demo):
    repo = demo.repo()
    api.add(repo, ["demo/gate"], mode="shim")
    hooks = repo / ".git" / "hooks"
    (hooks / "pre-commit").rename(hooks / "pre-commit.legacy")
    hook_file(hooks / "pre-commit", "#!/bin/sh\nexec \"$(dirname \"$0\")/pre-commit.legacy\"\n")
    assert parts(problem(repo, "shim-moved")) == ("shim-moved", None, "pre-commit", False, "ok")


def test_a_shim_another_tool_replaced_is_missing_and_fixed_by_a_refresh(demo):
    repo = demo.repo()
    api.add(repo, ["demo/gate"], mode="shim")
    hook_file(repo / ".git" / "hooks" / "pre-commit")
    assert parts(problem(repo, "shim-missing")) == (
        "shim-missing", None, "pre-commit", True, "fail")


def test_a_missing_launcher_is_fixed_by_a_refresh(demo):
    repo = demo.repo()
    git(repo, "config", "core.hooksPath", ".githooks")
    api.add(repo, ["demo/gate"], mode="shim")
    gitconf.resolve(repo).launcher.unlink()
    assert parts(problem(repo, "launcher-missing")) == (
        "launcher-missing", None, "pre-commit", True, "warn")


def test_what_a_hooks_dir_change_left_behind_is_named_by_whether_a_refresh_fixes_it(demo):
    repo = demo.repo()
    hooks = repo / ".git" / "hooks"
    hook_file(hooks / "pre-commit")
    api.add(repo, ["demo/gate"], mode="shim")
    assert (hooks / "pre-commit.local").exists()
    git(repo, "config", "core.hooksPath", ".githooks")
    assert parts(problem(repo, "left-shim")) == ("left-shim", None, "pre-commit", True, "warn")
    assert parts(problem(repo, "left-hook")) == ("left-hook", None, "pre-commit", True, "warn")
    hook_file(hooks / "pre-commit")
    assert parts(problem(repo, "left-hook-taken")) == (
        "left-hook-taken", None, "pre-commit", False, "warn")


def test_a_healthy_repo_names_no_problem(demo):
    repo = demo.repo()
    api.add(repo, ["demo/gate"], mode="shim")
    assert listed(repo)["problems"] == []


# -- existing_hook --------------------------------------------------------------------------

@NEEDS_CONFIG_GIT
def test_in_config_mode_his_own_hook_runs_after_the_pieces(demo):
    repo = demo.repo()
    api.add(repo, ["demo/gate"], mode="config")
    assert listed(repo)["events"]["pre-commit"]["existing_hook"] is None
    hook = hook_file(repo / ".git" / "hooks" / "pre-commit")
    event = listed(repo)["events"]["pre-commit"]
    assert event["existing_hook"] == str(hook)
    assert event["existing"] == f"{hook}, run by git after the pieces"


def test_in_shim_mode_his_own_hook_is_the_one_moved_aside(demo):
    repo = demo.repo()
    hooks = repo / ".git" / "hooks"
    hook_file(hooks / "pre-commit")
    api.add(repo, ["demo/gate"], mode="shim")
    assert shim.shim_event(hooks / "pre-commit") == "pre-commit"
    assert listed(repo)["events"]["pre-commit"]["existing_hook"] == str(
        hooks / "pre-commit.local")


def test_with_every_piece_off_his_own_hook_runs_alone(demo):
    repo = demo.repo()
    hooks = repo / ".git" / "hooks"
    hook = hook_file(hooks / "pre-commit")
    api.add(repo, ["demo/gate"], mode="shim")
    api.disable(repo, "demo/gate")
    assert listed(repo)["events"]["pre-commit"]["existing_hook"] == str(hook)


def test_a_hook_that_cant_run_is_not_his_own_hook(demo):
    repo = demo.repo()
    hook_file(repo / ".git" / "hooks" / "pre-commit", runnable=False)
    api.add(repo, ["demo/gate"], mode="shim")
    assert listed(repo)["events"]["pre-commit"]["existing_hook"] is None


def test_in_launcher_mode_the_other_tools_hook_is_not_his_own_hook(demo):
    repo = demo.repo()
    git(repo, "config", "core.hooksPath", ".githooks")
    report = api.add(repo, ["demo/gate"], mode="shim")
    [(_, _, hook, line, _)] = report.chain
    hook_file(Path(hook), f"#!/bin/sh\n{line}\n")
    event = listed(repo)["events"]["pre-commit"]
    assert event["via"] == "launcher" and event["existing"] == hook
    assert event["existing_hook"] is None


# -- own_hooks ------------------------------------------------------------------------------

def test_own_hooks_are_his_runnable_hooks_on_events_with_no_piece(demo):
    repo = demo.repo()
    hooks = repo / ".git" / "hooks"
    api.add(repo, ["demo/gate"], mode="shim")
    assert listed(repo)["own_hooks"] == []
    hook_file(hooks / "pre-push")
    hook_file(hooks / "post-merge", runnable=False)
    hook_file(hooks / "commit-msg.sample")
    hook_file(hooks / "not-an-event")
    # The pre-commit shim is repo's, and its event has a piece anyway.
    assert listed(repo)["own_hooks"] == ["pre-push"]
    hook_file(hooks / "post-checkout", (hooks / "pre-commit").read_text(encoding="utf-8"))
    assert listed(repo)["own_hooks"] == ["pre-push"]


def test_own_hooks_skip_an_event_whose_pieces_are_off(demo):
    repo = demo.repo()
    hooks = repo / ".git" / "hooks"
    hook_file(hooks / "pre-commit")
    api.add(repo, ["demo/gate"], mode="shim")
    api.disable(repo, "demo/gate")
    found = listed(repo)
    assert found["own_hooks"] == []
    assert found["events"]["pre-commit"]["existing_hook"] == str(hooks / "pre-commit")


def test_own_hooks_of_a_launcher_repo_are_read_in_the_hooks_dir_git_reads(demo):
    repo = demo.repo()
    git(repo, "config", "core.hooksPath", ".githooks")
    api.add(repo, ["demo/gate"], mode="shim")
    hook_file(repo / ".githooks" / "pre-push")
    hook_file(repo / ".git" / "hooks" / "post-merge")
    assert listed(repo)["own_hooks"] == ["pre-push"]


def test_a_gone_repo_has_no_own_hooks(demo):
    repo = demo.repo()
    api.add(repo, "demo/gate")
    shutil.rmtree(repo)
    [found] = api.status()
    assert found["own_hooks"] == []

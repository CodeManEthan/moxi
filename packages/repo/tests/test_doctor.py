"""repo's doctor rows through the core's own runner, and `SETUP` on `moxi install repo`."""

from __future__ import annotations

import hashlib
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

import moxi.modules
import moxi_repo
from moxi import paths
from moxi_repo import _records, api, checks, committer, gitconf
from moxi_repo import install as repo_install
from tests.conftest import POSIX_ONLY, change, commit, doctor, git, problems
from tests.test_commit_records import doctor_records, failed_push, push_key
from tests.test_committer import AUTHOR, deny, spec, write

pytestmark = POSIX_ONLY


@pytest.fixture
def clock(monkeypatch):
    """The committer's clock, which a test moves on by hand."""
    now = [time.time()]
    monkeypatch.setattr(committer, "_now", lambda: now[0])
    return now


def rows_named(name: str) -> list:
    return [r for r in doctor() if r.name == name]


def fake_git(tmp: Path, version: str) -> Path:
    path = tmp / f"git-{version}" / "git"
    path.parent.mkdir()
    path.write_text(f"#!/bin/sh\necho 'git version {version}'\n", encoding="utf-8")
    path.chmod(0o755)
    return path


# -- git --------------------------------------------------------------------------------------

def test_the_git_row_reads_the_floor_and_the_mode(demo):
    [row] = rows_named("git")
    version = gitconf.version_text(gitconf.git_version())
    assert row.state == "ok" and row.detail.startswith(f"git {version} (")
    assert row.detail.endswith("a repo not pinned gets config mode")
    demo.mp.setattr(gitconf, "git_version", lambda: (2, 34, 1))
    [row] = rows_named("git")
    # Nothing is indexed yet, so nothing depends on it.
    assert row.state == "warn" and "git 2.34.1" in row.detail and "older than 2.39.0" in \
        row.detail
    assert row.fix == "install git 2.39.0 or later"
    demo.mp.setattr(gitconf, "git_version", lambda: None)
    [row] = rows_named("git")
    assert row.state == "warn" and row.detail.startswith("git is not on PATH")


def test_a_git_too_old_for_an_indexed_repo_fails(demo):
    repo = demo.repo()
    api.add(repo, ["demo/gate"])
    demo.mp.setattr(gitconf, "git_version", lambda: (2, 34, 1))
    [row] = rows_named("git")
    assert row.state == "fail"
    # Above the floor and below config entries: the config-mode repo runs no piece.
    demo.mp.setattr(gitconf, "git_version", lambda: (2, 43, 0))
    [row] = problems(f"hooks {repo}")
    assert row.state == "fail"
    assert row.detail == "git 2.43.0 runs no config entries, so no piece runs here"
    assert row.fix == f"moxi repo git-hooks refresh {repo}"


def test_a_config_mode_repo_under_an_old_git_is_told_the_fix_that_moves_it(demo):
    pinned, unpinned = demo.repo("my notes"), demo.repo("beta-code")
    api.add(pinned, ["demo/gate"], mode="config")
    demo.mp.setattr(gitconf, "git_version", lambda: (2, 56, 0))
    api.add(unpinned, ["demo/gate"])
    demo.mp.setattr(gitconf, "git_version", lambda: (2, 43, 0))
    # Pinned to config, refresh would keep the config entries: the fix moves it to shims.
    [row] = problems(f"hooks {pinned}")
    assert row.detail == "git 2.43.0 runs no config entries, so no piece runs here"
    assert row.fix == pasted("moxi", "repo", "git-hooks", "add", pinned, "--mode", "shim")
    # Not pinned, refresh picks shims for this git.
    [row] = problems(f"hooks {unpinned}")
    assert row.fix == f"moxi repo git-hooks refresh {unpinned}"
    assert api.refresh(unpinned).ok and problems(f"hooks {unpinned}") == []
    # Run as printed, the pinned repo's fix clears its row.
    assert api.add(pinned, mode="shim").ok and problems(f"hooks {pinned}") == []


def test_another_git_too_old_for_config_entries_beside_a_config_mode_repo(demo):
    other = fake_git(demo.tmp, "2.43.0")
    demo.mp.setattr(checks, "OTHER_GITS", {sys.platform: (str(other),)})
    [row] = rows_named(f"git {other}")
    assert row.state == "ok" and "no indexed repo uses config mode" in row.detail
    repo, second = demo.repo("alpha-notes"), demo.repo("beta-code")
    api.add(repo, ["demo/gate"], mode="config")
    [row] = rows_named(f"git {other}")
    assert row.state == "warn"
    assert row.detail == (f"git 2.43.0 runs no config entries, so a commit it makes runs no "
                          f"piece in the config-mode repos {repo}")
    assert row.fix == f"moxi repo git-hooks add {repo} --mode shim"
    api.add(second, ["demo/gate"], mode="config")
    [row] = rows_named(f"git {other}")
    assert str(second) in row.detail and row.fix.endswith("for each of them")
    for r in (repo, second):
        api.add(r, mode="shim")
    [row] = rows_named(f"git {other}")
    assert row.state == "ok"
    newer = fake_git(demo.tmp, "2.56.1")
    demo.mp.setattr(checks, "OTHER_GITS", {sys.platform: (str(newer),)})
    [row] = rows_named(f"git {newer}")
    assert row.state == "ok" and row.detail == "git 2.56.1 runs config entries"


# -- git hooks --------------------------------------------------------------------------------

@pytest.mark.parametrize("mode", ["config", "shim", "launcher"])
def test_the_interpreter_and_the_runner_are_checked(demo, mode):
    repo = demo.repo()
    if mode == "launcher":
        git(repo, "config", "core.hooksPath", ".githooks")
    gone = str(demo.tmp / "gone-env" / "bin" / "python")
    with demo.mp.context() as mp:
        mp.setattr(gitconf, "interpreter", lambda: gone)
        report = api.add(repo, ["demo/gate"], mode="config" if mode == "config" else "shim")
    if mode == "launcher":
        [(_, _, hook, line, _)] = report.chain
        Path(hook).parent.mkdir(exist_ok=True)
        Path(hook).write_text(f"#!/bin/sh\n{line}\n", encoding="utf-8")
        Path(hook).chmod(0o755)
    [row] = problems()
    assert (row.name, row.state) == (f"hooks {repo}", "fail")
    assert row.detail == f"pre-commit: the interpreter {gone} is missing"
    assert row.fix == f"moxi repo git-hooks refresh {repo}"
    api.refresh(repo)
    assert problems() == []
    # Another environment's interpreter that exists still runs the pieces: a warning.
    with demo.mp.context() as mp:
        mp.setattr(gitconf, "interpreter", lambda: str(demo.hook_env["bare"]))
        api.refresh(repo)
    [row] = problems()
    assert row.state == "warn"
    assert row.detail == "pre-commit: written for another interpreter or runner"
    missing = str(demo.tmp / "gone-run.py")
    with demo.mp.context() as mp:
        mp.setattr(gitconf, "runner_file", lambda: missing)
        api.refresh(repo)
    [row] = problems()
    assert row.state == "fail" and row.detail == f"pre-commit: the runner {missing} is missing"
    api.refresh(repo)
    assert problems() == []


def test_a_shim_a_manager_renamed_other_than_legacy_fails(demo):
    repo = demo.repo()
    hooks = repo / ".git" / "hooks"
    api.add(repo, ["demo/gate"], mode="shim")
    (hooks / "pre-commit").rename(hooks / "pre-commit.old")
    (hooks / "pre-commit").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    (hooks / "pre-commit").chmod(0o755)
    [row] = problems()
    assert row.state == "fail" and "moved repo's shim to pre-commit.old" in row.detail
    assert "the pieces run only if that tool runs it" in row.detail


def test_a_registered_piece_no_installed_module_declares(demo):
    repo = demo.repo()
    api.add(repo, ["demo/gate"])
    for pid, fail in (("gone/closed", "closed"), ("gone/open", "open")):
        for var, value in (("event", "pre-commit"), ("target", "moxi_gone.hooks:fn"),
                           ("fail", fail), ("order", "40"), ("enabled", "true")):
            git(repo, "config", f"moxi-repo.piece.{pid}.{var}", value)
    found = {r.detail.split()[0]: r for r in problems()}
    assert set(found) == {"gone/closed", "gone/open"}
    assert found["gone/closed"].state == "fail" and found["gone/open"].state == "warn"
    assert found["gone/closed"].detail == ("gone/closed is registered but no installed "
                                           "module declares it")
    assert found["gone/closed"].fix == f"moxi repo git-hooks remove {repo} gone/closed"
    api.disable(repo, "gone/closed")
    assert {r.state for r in problems()} == {"warn"}
    api.remove(repo, ["gone/closed", "gone/open"])
    assert problems() == []


def test_an_indexed_repo_with_no_piece_and_one_with_pieces(demo):
    bare, full = demo.repo("alpha-notes"), demo.repo("beta-code")
    api.add(bare)
    api.add(full, ["demo/gate", "demo/lint", "demo/watch"], mode="shim")
    [row] = rows_named(f"hooks {bare}")
    assert (row.state, row.detail) == ("ok", "indexed, with no piece registered")
    [row] = rows_named(f"hooks {full}")
    version = gitconf.version_text(gitconf.git_version())
    assert (row.state, row.detail) == (
        "ok", f"shim mode (pinned), git {version}; post-commit: 1 piece through shim; "
              "pre-commit: 2 pieces through shim")


# -- specs and uncovered files ----------------------------------------------------------------

def test_uncovered_files_are_named_with_their_count(demo):
    repo = demo.repo()
    write(repo, "notes/a.md")
    (repo / ".gitignore").write_text("*.log\n", encoding="utf-8")
    git(repo, "add", ".gitignore")
    git(repo, "commit", "-q", "-m", "ignore logs")
    write(repo, "build.log")
    # Without a saved spec there is nothing to cover.
    moxi_repo.commit(spec(repo))
    assert rows_named(f"uncovered {repo}") == []
    moxi_repo.save_spec(spec(repo))
    for n in range(11):
        write(repo, f"src/f{n:02}.py")
    (repo / "README.md").write_text("changed\n", encoding="utf-8")
    write(repo, "notes/b.md")
    [row] = rows_named(f"uncovered {repo}")
    assert row.state == "warn"
    assert row.detail == ("12 changed or untracked files no saved spec covers: README.md, "
                          + ", ".join(f"src/f{n:02}.py" for n in range(9))
                          + " and 2 more")
    shutil.rmtree(repo / "src")
    git(repo, "checkout", "-q", "--", "README.md")
    assert rows_named(f"uncovered {repo}") == []


@pytest.mark.parametrize("path", ["./notes", "notes//", "notes/./", "./"])
def test_a_spec_path_git_reads_the_same_covers_the_same_files(demo, path):
    repo = demo.repo()
    moxi_repo.save_spec(spec(repo, paths=(path,)))
    write(repo, "notes/b.md")
    assert rows_named(f"uncovered {repo}") == []
    assert checks._covers(path, "notes/b.md")
    assert not checks._covers(path, "notesx/b.md") or path == "./"


def test_paths_left_out_over_an_hour_are_shown_and_gone_once_committed(demo, clock):
    repo = demo.repo()
    moxi_repo.save_spec(spec(repo))
    write(repo, "notes/a.md")
    write(repo, "notes/b.md")
    git(repo, "add", "notes/b.md")  # someone else's staging
    result = moxi_repo.commit_saved(repo, "demo/snap")
    assert result.status == "committed" and result.left == (("notes/b.md",
                                                              "staged by someone else"),)
    demo.mp.setattr(checks, "_now", lambda: clock[0] + 60)
    assert problems(f"spec {repo} demo/snap left out") == []
    demo.mp.setattr(checks, "_now", lambda: clock[0] + 3601)
    [row] = problems(f"spec {repo} demo/snap left out")
    since = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(clock[0]))
    assert row.state == "warn"
    assert row.detail == (f"1 path left out over an hour: notes/b.md (staged by someone "
                          f"else, since {since})")
    git(repo, "commit", "-q", "-m", "b by hand")
    clock[0] += 10
    assert moxi_repo.commit_saved(repo, "demo/snap").status == "clean"
    assert problems(f"spec {repo} demo/snap left out") == []


def test_changed_paths_waiting_past_the_interval(demo, clock):
    repo = demo.repo()
    moxi_repo.save_spec(spec(repo, min_interval=600))
    write(repo, "notes/a.md")
    assert moxi_repo.commit_saved(repo, "demo/snap").status == "committed"
    write(repo, "notes/b.md")
    [row] = rows_named(f"spec {repo} demo/snap")
    assert row.state == "ok" and "interval 600 s; last pass committed at" in row.detail
    real = time.time()
    demo.mp.setattr(checks, "_now", lambda: real + 600 + 3601 + 5)
    [row] = rows_named(f"spec {repo} demo/snap")
    assert row.state == "warn"
    assert row.detail.startswith("1 changed path waiting (notes/b.md); the last commit by "
                                 "snap-bot was at ")
    assert row.fix == f"moxi repo commit {repo} --spec demo/snap"


def test_commits_not_pushed_are_a_warn_row_until_the_push(demo, clock):
    repo = demo.repo()
    moxi_repo.save_spec(spec(repo, push="origin"))
    bare = failed_push(demo, repo, clock)
    [row] = problems(f"spec {repo} demo/snap push")
    assert row.state == "warn"
    assert row.detail.startswith("main has never been pushed to origin; the last push "
                                 "failed: ")
    assert "remote says no" in row.detail
    assert row.fix == f"moxi repo commit {repo} --spec demo/snap"
    deny(bare, False)
    clock[0] += 400
    assert moxi_repo.commit_saved(repo, "demo/snap").pushed == ("main",)
    assert problems(f"spec {repo}") == []
    clock[0] += 400
    failed_push(demo, repo, clock)
    [row] = problems(f"spec {repo} demo/snap push")
    assert row.detail.startswith("1 commit on main not pushed to origin; the last push "
                                 "failed: ")


def test_an_unsaved_specs_push_record_names_its_unseen_ending(demo, clock):
    repo = demo.repo()
    failed_push(demo, repo, clock)
    [row] = problems(f"spec {repo} demo/snap push")
    assert row.detail == ("the spec isn't saved, so only its caller's passes retry the push "
                          "of main; if nothing runs it any more, forget it")
    assert row.fix == f"moxi repo specs remove {repo} demo/snap"


def test_a_saved_specs_push_record_on_a_branch_no_pass_pushes(demo, clock):
    repo = demo.repo()
    moxi_repo.save_spec(spec(repo, push="origin"))
    git(repo, "switch", "-q", "-c", "a")
    failed_push(demo, repo, clock, branch="a")
    git(repo, "switch", "-q", "main")
    # While the state file holds the failed push, every pass retries it.
    assert [r for r in problems(f"spec {repo}") if "no pass retries" in r.detail] == []
    (repo / ".git" / "moxi-repo" / "state.json").unlink()
    [row] = [r for r in problems(f"spec {repo}") if "no pass retries" in r.detail]
    assert row.detail == ("no pass retries the push of a: it isn't checked out, and the "
                          "repo's state file holds no failed push of it")
    assert row.fix.endswith(f"moxi doctor --clear repo {push_key(repo, 'a')}")


# -- a root with a space ----------------------------------------------------------------------

def pasted(*words) -> str:
    """A command as a person pastes it: each word quoted only where it needs quoting."""
    return shlex.join(str(w) for w in words)


def test_git_and_spec_fix_lines_quote_a_root_with_a_space(demo, clock):
    other = fake_git(demo.tmp, "2.43.0")
    demo.mp.setattr(checks, "OTHER_GITS", {sys.platform: (str(other),)})
    repo = demo.repo("my notes")
    api.add(repo, ["demo/gate"], mode="config")
    [row] = rows_named(f"git {other}")
    assert row.fix == pasted("moxi", "repo", "git-hooks", "add", repo, "--mode", "shim")

    # A spec that ran without being saved, whose push failed: its row and its record.
    failed_push(demo, repo, clock)
    [row] = problems(f"spec {repo} demo/snap push")
    assert row.fix == pasted("moxi", "repo", "specs", "remove", repo, "demo/snap")
    fix = doctor_records()[push_key(repo)]["fix"]
    assert fix.endswith(f"({pasted('moxi', 'repo', 'commit', repo)})")

    # Saved, with changed paths waiting past the interval and commits not pushed.
    moxi_repo.save_spec(spec(repo, push="origin", min_interval=600))
    write(repo, "notes/waiting.md")
    now = checks._now
    demo.mp.setattr(checks, "_now", lambda: time.time() + 600 + 3601 + 5)
    retry = pasted("moxi", "repo", "commit", repo, "--spec", "demo/snap")
    assert {r.fix for r in problems(f"spec {repo} demo/snap")} == {retry}
    demo.mp.setattr(checks, "_now", now)

    # A saved push on a branch no pass pushes: every command in the line pastes whole.
    git(repo, "switch", "-q", "-c", "side")
    failed_push(demo, repo, clock, branch="side")
    git(repo, "switch", "-q", "main")
    (repo / ".git" / "moxi-repo" / "state.json").unlink()
    [row] = [r for r in problems(f"spec {repo}") if "no pass retries" in r.detail]
    switch, rest = row.fix.split(", then: ")
    again, clear = rest.split("; or: ")
    assert shlex.split(switch) == ["git", "-C", str(repo), "switch", "side"]
    assert shlex.split(again) == ["moxi", "repo", "commit", str(repo), "--spec", "demo/snap"]
    assert shlex.split(clear) == ["moxi", "doctor", "--clear", "repo", push_key(repo, "side")]

    # A saved spec that can't be read.
    git(repo, "config", "moxi-repo.spec.demo/snap.min-interval", "soon")
    [row] = [r for r in problems(f"spec {repo} demo/snap") if "can't be read" in r.detail]
    assert row.fix == pasted("moxi", "repo", "specs", "remove", repo, "demo/snap")


def test_ledger_and_commit_record_fix_lines_quote_a_root_with_a_space(demo):
    repo, gone = demo.repo("my notes"), demo.repo("old notes")
    api.add(repo, ["demo/gate", "demo/lint", "demo/watch"])
    api.add(gone, ["demo/lint"])
    demo.act(gate="refuse", lint="raise", watch="record")
    write(repo, "notes/a.md")
    assert moxi_repo.commit(spec(repo)).status == "refused"
    fix = doctor_records()[_records.commit_key(repo / ".git", "demo/snap")]["fix"]
    assert fix.endswith(pasted("moxi", "repo", "specs", "remove", repo, "demo/snap"))
    demo.act(gate="ok")
    for r in (repo, gone):
        change(r)
        commit(r)
    shutil.rmtree(gone)
    git(repo, "config", "moxi-repo.piece.demo/lint.enabled", "false")
    git(repo, "config", "--unset-all", "moxi-repo.piece.demo/watch.recorded")
    refresh = pasted("moxi", "repo", "git-hooks", "refresh", repo)
    assert {row.name: row.fix for row in problems("ledger ")} == {
        f"ledger {_records.piece_key(repo / '.git', 'demo/lint')}": refresh,
        f"ledger {_records.piece_key(repo / '.git', 'demo/watch')}": refresh,
        f"ledger {_records.piece_key(gone / '.git', 'demo/lint')}":
            pasted("moxi", "repo", "git-hooks", "remove", gone)}


# -- the doctor only reads --------------------------------------------------------------------

def _snapshot(*roots: Path) -> dict[str, str]:
    out = {}
    for root in roots:
        for path in sorted(root.rglob("*")):
            if path.is_file():
                out[str(path)] = (hashlib.sha256(path.read_bytes()).hexdigest()
                                  + f" {path.stat().st_mtime_ns}")
            else:
                out[str(path)] = "dir"
    return out


def test_the_doctor_writes_and_clears_nothing(demo, clock):
    repo, gone = demo.repo("alpha-notes"), demo.repo("beta-code")
    api.add(repo, ["demo/gate", "demo/watch"])
    api.add(gone, ["demo/lint"])
    demo.act(watch="record", lint="raise")
    for r in (repo, gone):
        change(r)
        commit(r)
    moxi_repo.save_spec(spec(repo, push="origin"))
    failed_push(demo, repo, clock)
    write(repo, "loose.txt")
    shutil.rmtree(gone)
    git(repo, "config", "--unset-all", "moxi-repo.piece.demo/watch.recorded")
    before = _snapshot(paths.Layout("repo").state_dir(), repo / ".git")
    rows = doctor()
    assert {r.state for r in rows} >= {"ok", "warn", "fail"}
    assert _snapshot(paths.Layout("repo").state_dir(), repo / ".git") == before


# -- SETUP ------------------------------------------------------------------------------------

def _stale_entries(demo) -> tuple[list[Path], str]:
    """Three repos, in config mode, shim mode and through the launcher, whose entries name
    an interpreter that is gone, as after an install that moved the environment."""
    repos = [demo.repo(n) for n in ("alpha-notes", "beta-code", "gamma-docs")]
    git(repos[2], "config", "core.hooksPath", ".githooks")
    gone = str(demo.tmp / "old-env" / "bin" / "python")
    with demo.mp.context() as mp:
        mp.setattr(gitconf, "interpreter", lambda: gone)
        api.add(repos[0], ["demo/gate"], mode="config")
        api.add(repos[1], ["demo/gate"], mode="shim")
        [(_, _, hook, line, _)] = api.add(repos[2], ["demo/gate"], mode="shim").chain
    Path(hook).parent.mkdir()
    Path(hook).write_text(f"#!/bin/sh\n{line}\n", encoding="utf-8")
    Path(hook).chmod(0o755)
    assert len([r for r in problems() if "the interpreter" in r.detail]) == 3
    return repos, gone


def _pieces_run(demo, repos: list[Path]) -> None:
    demo.act(gate="refuse")
    for r in repos:
        change(r)
        assert "gate says no" in commit(r).stderr


def test_the_announcement_declares_setup_and_doctor():
    found = moxi.modules.load("repo")
    assert found.setup == "moxi_repo.install:setup"
    assert found.doctor == "moxi_repo.checks:rows"


def test_setup_rewrites_every_entry_shim_and_launcher(demo, capsys):
    repos, _ = _stale_entries(demo)
    capsys.readouterr()
    assert repo_install.setup() == 0
    out = capsys.readouterr().out
    assert all(f"refreshed {r}" in out for r in repos)
    assert problems() == []
    _pieces_run(demo, repos)


def test_moxi_install_runs_setup_and_the_doctor_holds_no_version_row(demo):
    """`moxi install repo` on an installed repo runs its settle step again, in a child of
    the environment's own Python; that child runs SETUP and records the version the
    doctor's version check reads. This environment's moxi is an editable install, which
    `moxi install` refuses, so the test starts the same child the installer starts."""
    repos, _ = _stale_entries(demo)
    done = subprocess.run([str(demo.hook_env["python"]), "-m", "moxi._settle", "repo"],
                          capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "setup repo: running moxi_repo.install:setup" in done.stdout
    assert all(f"refreshed {r}" in done.stdout for r in repos)
    assert "setup repo: done" in done.stdout
    assert [r for r in doctor() if r.name == "setup version"] == []
    assert problems() == []
    _pieces_run(demo, repos)


def test_setup_with_nothing_indexed_needs_no_git(demo, capsys):
    demo.mp.setattr(gitconf, "git_version", lambda: None)
    assert repo_install.setup() == 0
    assert capsys.readouterr().out == ("repo: no repo is indexed yet, so there is nothing "
                                       "to rewrite\n")


def test_setup_reports_one_repos_problem_and_still_succeeds(demo, capsys):
    repo, gone = demo.repo("alpha-notes"), demo.repo("beta-code")
    for r in (repo, gone):
        api.add(r, ["demo/gate"])
    shutil.rmtree(gone)
    capsys.readouterr()
    assert repo_install.setup() == 0
    out, err = capsys.readouterr()
    assert f"refreshed {repo}" in out
    assert (f"repo: {gone} is no longer a git work tree; run: moxi repo git-hooks remove "
            f"{gone}") in err


def test_setup_fails_without_a_usable_git_while_repos_are_indexed(demo, capsys):
    api.add(demo.repo())
    demo.mp.setattr(gitconf, "git_version", lambda: (2, 34, 1))
    assert repo_install.setup() == 1
    assert "git 2.34.1 is older than 2.39.0" in capsys.readouterr().err


@pytest.mark.parametrize("garbled", [b'{"repos": {"/a/.git": {"root"', b"[1, 2]",
                                     b'{"repos": 3}'])
def test_an_unreadable_index_is_a_fail_row_and_nothing_overwrites_it(demo, capsys, garbled):
    repo, other = demo.repo("alpha-notes"), demo.repo("beta-code")
    api.add(repo, "demo/gate")
    index = api.index_path()
    index.write_bytes(garbled)
    [row] = doctor("index")
    assert row.state == "fail" and str(index) in row.detail
    assert "moxi repo git-hooks add" in row.fix
    capsys.readouterr()
    assert repo_install.setup() == 1
    assert str(index) in capsys.readouterr().err
    with pytest.raises(api.Failure) as caught:
        api.add(other, "demo/gate")
    assert str(index) in caught.value.text and "the index was not changed" in caught.value.text
    with pytest.raises(api.Failure):
        api.status()
    assert index.read_bytes() == garbled


def test_an_empty_index_file_reads_as_no_repo(demo):
    api.index_path().parent.mkdir(parents=True, exist_ok=True)
    api.index_path().write_bytes(b"")
    assert api.indexed() == {} and doctor("index") == []
    api.add(demo.repo(), "demo/gate")
    assert len(api.indexed()) == 1

"""The dispatcher end to end: real git, real git hooks, in config mode and in shim mode."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import moxi_repo
from moxi_repo import _records, api, gitconf, pieces
from tests.conftest import POSIX_ONLY, change, commit, git, head

pytestmark = POSIX_ONLY

MODES = ["config", "shim"]


def existing_hook(repo: Path, event: str, log: Path) -> None:
    hook = repo / ".git" / "hooks" / event
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text(f"#!/bin/sh\necho '{{\"id\": \"existing\", \"stdin\": \"'\"$(cat)\"'\"}}' "
                    f">> '{log}'\n", encoding="utf-8")
    hook.chmod(0o755)


@pytest.mark.parametrize("mode", MODES)
def test_pieces_run_in_order_then_the_existing_git_hook(demo, mode):
    repo = demo.repo()
    existing_hook(repo, "pre-commit", demo.log)
    api.add(repo, ["demo/lint", "demo/gate", "demo/first"], mode=mode)
    change(repo)
    assert commit(repo).returncode == 0
    assert demo.ids() == ["demo/first", "demo/gate", "demo/lint", "existing"]


@pytest.mark.parametrize("mode", MODES)
def test_a_refusal_blocks_every_piece_still_runs_and_every_refusal_prints(demo, mode):
    repo = demo.repo()
    api.add(repo, ["demo/first", "demo/gate", "demo/lint"], mode=mode)
    demo.act(first="refuse", gate="refuse")
    change(repo)
    before = head(repo)
    done = commit(repo)
    assert done.returncode != 0 and head(repo) == before
    assert demo.ids() == ["demo/first", "demo/gate", "demo/lint"]
    assert "repo: pre-commit refused by demo/first: first says no" in done.stderr
    assert "repo: pre-commit refused by demo/gate: gate says no" in done.stderr
    assert "  a second line" in done.stderr


@pytest.mark.parametrize("mode", MODES)
def test_pre_push_stdin_reaches_every_piece_and_the_existing_git_hook(demo, mode):
    repo = demo.repo()
    remote = demo.tmp / "remote.git"
    git(demo.tmp, "init", "-q", "--bare", "remote.git")
    git(repo, "remote", "add", "origin", str(remote))
    existing_hook(repo, "pre-push", demo.log)
    api.add(repo, ["demo/push-check"], mode=mode)
    git(repo, "push", "-q", "origin", "main")
    runs = demo.runs()
    line = f"refs/heads/main {head(repo)} refs/heads/main {'0' * 40}"
    assert [r["id"] for r in runs] == ["demo/push-check", "existing"]
    assert runs[0]["stdin"].strip() == line and runs[1]["stdin"].strip() == line
    assert runs[0]["updates"] == ["refs/heads/main"]
    demo.act(push_check="refuse")
    change(repo)
    commit(repo)
    assert git(repo, "push", "-q", "origin", "main", check=False).returncode != 0


@pytest.mark.parametrize("mode", MODES)
def test_a_linked_worktree_runs_the_pieces(demo, mode):
    repo = demo.repo()
    api.add(repo, ["demo/gate"], mode=mode)
    git(repo, "worktree", "add", "-q", str(demo.tmp / "wt"), "-b", "side")
    change(demo.tmp / "wt")
    assert commit(demo.tmp / "wt").returncode == 0
    assert demo.ids() == ["demo/gate"]


@pytest.mark.parametrize("mode", MODES)
def test_the_switches_that_skip_git_hooks_for_one_command(demo, mode):
    repo = demo.repo()
    existing_hook(repo, "pre-commit", demo.log)
    api.add(repo, ["demo/gate"], mode=mode)
    change(repo, text="1\n")
    assert commit(repo, "--no-verify").returncode == 0
    assert demo.ids() == []
    change(repo, text="2\n")
    assert commit(repo, pre=("-c", "hook.moxi-repo-pre-commit.enabled=false")).returncode == 0
    assert demo.ids() == (["existing"] if mode == "config" else ["demo/gate", "existing"])
    (demo.tmp / "other-hooks").mkdir()
    change(repo, text="3\n")
    done = commit(repo, pre=("-c", f"core.hooksPath={demo.tmp / 'other-hooks'}"))
    assert done.returncode == 0
    assert demo.ids() == (["demo/gate"] if mode == "config" else [])


@pytest.mark.parametrize("mode", MODES)
def test_a_piece_that_stages_a_file_gets_it_committed(demo, mode):
    repo = demo.repo()
    api.add(repo, ["demo/gate"], mode=mode)
    demo.act(gate="stage")
    change(repo)
    assert commit(repo).returncode == 0
    files = git(repo, "show", "--name-only", "--format=", "HEAD").stdout.split()
    assert sorted(files) == ["note.md", "staged-by-piece.txt"]


@pytest.mark.parametrize("mode", MODES)
def test_what_a_piece_prints_on_ok_reaches_the_committer_and_ctx_git_takes_input(demo, mode):
    repo = demo.repo()
    api.add(repo, ["demo/gate", "demo/lint"], mode=mode)
    demo.act(gate="print", lint="input")
    change(repo)
    done = commit(repo)
    assert done.returncode == 0
    assert "gate printed this" in done.stderr
    blob = git(repo, "hash-object", "--stdin", input="hello\n").stdout.strip()
    assert f"hash {blob}" in done.stderr


def test_fail_policy_closed_open_and_post_events(demo):
    repo = demo.repo()
    api.add(repo, ["demo/gate", "demo/lint", "demo/watch"])
    demo.act(gate="raise")
    change(repo)
    done = commit(repo)
    assert done.returncode != 0
    assert ("repo: pre-commit refused: demo/gate broke (ValueError: gate fell over); "
            f"fix it or run: moxi repo git-hooks disable {repo} demo/gate") in done.stderr
    assert "Traceback" not in done.stderr
    demo.act(gate="ok", lint="raise", watch="notice")
    done = commit(repo)
    assert done.returncode == 0, done.stderr
    assert "repo: pre-commit went on: demo/lint broke (ValueError: lint fell over)" in done.stderr
    assert "repo: post-commit, demo/watch: watch noticed something" in done.stderr
    cgd = str(repo / ".git")
    assert _records.piece_key(cgd, "demo/lint") in _records.ledger()
    # The gate broke on the first commit and passed on the second, which cleared it.
    assert _records.piece_key(cgd, "demo/gate") not in _records.ledger()
    demo.act(lint="ok", watch="record", gate="ok")
    change(repo, text="more\n")
    done = commit(repo)
    assert done.returncode == 0 and "watch recorded something" not in done.stderr
    ledger = _records.ledger()
    assert _records.piece_key(cgd, "demo/lint") not in ledger
    assert _records.piece_key(cgd, "demo/gate") not in ledger
    assert _records.piece_key(cgd, "demo/watch") in ledger
    state = gitconf.read(gitconf.resolve(repo))
    assert "recorded" in state.pieces["demo/watch"]
    assert "recorded" not in state.pieces["demo/lint"]
    api.disable(repo, "demo/watch")
    assert _records.piece_key(cgd, "demo/watch") not in _records.ledger()


def test_a_closed_piece_whose_target_cant_import_refuses(demo):
    repo = demo.repo()
    api.add(repo, ["demo/ghost"])
    change(repo)
    done = commit(repo)
    assert done.returncode != 0
    assert "demo/ghost broke (ModuleNotFoundError" in done.stderr


@pytest.mark.parametrize("mode", MODES)
def test_a_missing_interpreter_or_runner_gives_one_line(demo, mode):
    repo = demo.repo()
    demo.mp.setattr(gitconf, "interpreter", lambda: str(demo.tmp / "gone" / "python"))
    api.add(repo, ["demo/gate", "demo/watch"], mode=mode)
    change(repo)
    done = commit(repo)
    assert done.returncode != 0 and "Traceback" not in done.stderr
    assert "git hooks can't run here" in done.stderr
    api.remove(repo, ["demo/gate"])
    assert commit(repo).returncode == 0
    demo.mp.setattr(gitconf, "interpreter", lambda: str(demo.hook_env["bare"]))
    api.add(repo, ["demo/gate"])
    change(repo, text="x\n")
    done = commit(repo)
    assert done.returncode != 0 and "Traceback" not in done.stderr


def test_shims_move_the_existing_git_hook_and_back_and_refuse_a_foreign_local(demo):
    repo = demo.repo()
    hooks = repo / ".git" / "hooks"
    existing_hook(repo, "pre-commit", demo.log)
    original = (hooks / "pre-commit").read_text()
    api.add(repo, ["demo/gate"], mode="shim")
    assert (hooks / "pre-commit.local").read_text() == original
    api.remove(repo, ["demo/gate"])
    assert (hooks / "pre-commit").read_text() == original
    assert not (hooks / "pre-commit.local").exists()
    (hooks / "pre-commit.local").write_text("#!/bin/sh\n")
    with pytest.raises(api.Refused):
        api.add(repo, ["demo/gate"], mode="shim")
    assert gitconf.read(gitconf.resolve(repo)).pieces == {}


def test_a_renamed_shim_still_runs_local_and_refresh_rewrites_it_in_place(demo):
    repo = demo.repo()
    hooks = repo / ".git" / "hooks"
    existing_hook(repo, "pre-commit", demo.log)
    api.add(repo, ["demo/gate"], mode="shim")
    (hooks / "pre-commit").rename(hooks / "pre-commit.legacy")
    (hooks / "pre-commit").write_text("#!/bin/sh\nexec \"$(dirname \"$0\")/pre-commit.legacy\""
                                      " \"$@\"\n")
    (hooks / "pre-commit").chmod(0o755)
    change(repo)
    assert commit(repo).returncode == 0
    assert demo.ids() == ["demo/gate", "existing"]
    assert any("moved repo's shim to pre-commit.legacy" in p
               for p in api.status(repo)[0]["problems"])
    demo.mp.setattr(gitconf, "interpreter", lambda: str(demo.hook_env["python"]) + "-new")
    api.refresh(repo)
    assert "-new" in (hooks / "pre-commit.legacy").read_text()


def test_shim_mode_outside_the_git_dir_writes_a_launcher_and_a_chain_line(demo):
    repo = demo.repo()
    tracked = repo / ".githooks"
    tracked.mkdir()
    git(repo, "config", "core.hooksPath", ".githooks")
    report = api.add(repo, ["demo/gate", "demo/push-check"], mode="shim")
    assert list(tracked.iterdir()) == []
    assert (repo / ".git" / "moxi-repo" / "hook").exists()
    lines = {event: line for _, event, _, line, present in report.chain if not present}
    assert set(lines) == {"pre-commit", "pre-push"}
    assert api.status(repo)[0]["events"]["pre-commit"]["chain_line"] == "needed"
    (tracked / "pre-commit").write_text(f"#!/bin/sh\n{lines['pre-commit']}\n")
    (tracked / "pre-commit").chmod(0o755)
    assert api.status(repo)[0]["events"]["pre-commit"]["chain_line"] == "present"
    demo.act(gate="refuse")
    change(repo, ".githooks/pre-commit", "")
    assert commit(repo).returncode != 0
    demo.act(gate="ok")
    assert commit(repo).returncode == 0
    clone = demo.tmp / "teammate"
    git(demo.tmp, "clone", "-q", str(repo), str(clone))
    git(clone, "config", "core.hooksPath", ".githooks")
    demo.act(gate="refuse")
    change(clone)
    assert commit(clone).returncode == 0
    (repo / ".git" / "moxi-repo" / "hook").unlink()
    assert any("launcher" in p for p in api.status(repo)[0]["problems"])


def test_refresh_after_a_git_upgrade_moves_shims_to_config_entries(demo):
    repo = demo.repo()
    existing_hook(repo, "pre-commit", demo.log)
    demo.mp.setattr(gitconf, "git_version", lambda: (2, 43, 0))
    api.add(repo, ["demo/gate"])
    pinned = demo.repo("beta-code")
    api.add(pinned, ["demo/gate"], mode="shim")
    assert gitconf.read(gitconf.resolve(repo)).mode == "shim"
    demo.mp.setattr(gitconf, "git_version", lambda: (2, 56, 0))
    api.refresh()
    state = gitconf.read(gitconf.resolve(repo))
    assert state.mode == "config" and "pre-commit" in state.entries
    hooks = repo / ".git" / "hooks"
    assert not (hooks / "pre-commit.local").exists() and "existing" in (
        hooks / "pre-commit").read_text()
    assert gitconf.read(gitconf.resolve(pinned)).mode == "shim"


def test_settings_reach_the_piece_and_set_edges(demo):
    repo = demo.repo()
    api.add(repo, ["demo/gate"], sets=["Refused-Name=Co-Authored-By", "REFUSED-NAME=Round",
                                       "label=one"])
    change(repo)
    commit(repo)
    assert demo.runs()[0]["settings"] == {"refused-name": ["Co-Authored-By", "Round"],
                                          "label": ["one"]}
    with pytest.raises(api.Usage):
        api.add(repo, ["demo/gate"], sets=["refused-name=", "refused-name=x"])
    with pytest.raises(api.Usage):
        api.add(repo, ["demo/gate"], sets=["label=a", "label=b"])
    api.add(repo, ["demo/gate"], sets=["refused-name="])
    api.disable(repo, "demo/gate")
    api.refresh(repo)
    section = gitconf.read(gitconf.resolve(repo)).pieces["demo/gate"]
    assert "refused-name" not in section and section["label"] == ["one"]
    assert section["enabled"] == ["false"]


def test_declarations_refuse_bad_setting_names_and_reserved_ones(demo):
    refused = api.available()["refused"]
    assert "refusedTrailer" in refused["demo/camel"]
    assert "registration" in refused["demo/reserved"]
    with pytest.raises(api.Refused):
        api.add(demo.repo(), ["demo/reserved"])


def test_status_lists_each_piece_with_id_event_and_enabled(demo):
    repo = demo.repo()
    api.add(repo, ["demo/gate", "demo/watch"])
    listed = {p["id"]: p for p in api.status(repo)[0]["pieces"]}
    assert listed["demo/gate"]["event"] == "pre-commit" and listed["demo/gate"]["enabled"]


def test_remove_everything_and_a_vanished_repo(demo):
    repo = demo.repo()
    api.add(repo, ["demo/gate"], mode="shim")
    api.remove(repo)
    assert gitconf.read(gitconf.resolve(repo)).sections == set()
    assert api.indexed() == {}
    other = demo.repo("beta-code")
    api.add(other, ["demo/gate"])
    import shutil
    shutil.rmtree(other)
    api.remove(other)
    assert api.indexed() == {}


def test_exports_are_not_module_names_and_resolve_after_every_import():
    package = Path(moxi_repo.__file__).parent
    modules = {p.stem for p in package.glob("*.py")} - {"__init__"}
    assert not set(moxi_repo.EXPORTS) & (modules | {"committer"})
    import importlib
    for name in modules:
        importlib.import_module(f"moxi_repo.{name}")
    for _ in range(2):
        assert moxi_repo.Outcome is pieces.Outcome
        assert moxi_repo.HookContext is pieces.HookContext
        assert callable(moxi_repo.git)


def test_chain_lines_are_the_designs():
    from moxi_repo import shim
    assert shim.chain_line("pre-commit", False) == (
        'h="$(git rev-parse --path-format=absolute --git-common-dir)/moxi-repo/hook"; '
        'if [ -f "$h" ]; then sh "$h" pre-commit "$@" || exit 1; fi')
    assert json.dumps(shim.chain_line("pre-push", True)).count("mktemp") == 1

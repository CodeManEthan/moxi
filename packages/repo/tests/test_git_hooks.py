"""The dispatcher end to end: real git, real git hooks, in config mode and in shim mode."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import moxi_repo
from moxi_repo import _records, api, gitconf, pieces
from tests.conftest import POSIX_ONLY, change, commit, doctor, git, head, problems

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
    api.add(repo, ["demo/lint", "demo/gate", "demo/first", "demo/zz-early"], mode=mode)
    change(repo)
    assert commit(repo).returncode == 0
    # By order (5, 10, 20, 60), not by id.
    assert demo.ids() == ["demo/zz-early", "demo/first", "demo/gate", "demo/lint", "existing"]


@pytest.mark.parametrize("mode", MODES)
def test_an_existing_git_hook_that_refuses_refuses_after_the_pieces_ran(demo, mode):
    repo = demo.repo()
    existing_hook(repo, "pre-commit", demo.log)
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.write_text(hook.read_text() + "exit 1\n")
    api.add(repo, "demo/gate", mode=mode)
    change(repo)
    before = head(repo)
    assert commit(repo).returncode != 0 and head(repo) == before
    assert demo.ids() == ["demo/gate", "existing"]


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


def test_an_outcome_of_the_wrong_kind_is_a_broken_piece(demo):
    repo = demo.repo()
    api.add(repo, "demo/gate", "demo/watch")
    demo.act(gate="notice")
    change(repo)
    done = commit(repo)
    assert done.returncode != 0
    assert ("repo: pre-commit refused: demo/gate broke (returned notice, which only post-* "
            "git hooks take)") in done.stderr
    demo.act(gate="ok", watch="refuse")
    done = commit(repo)
    assert done.returncode == 0, done.stderr
    assert ("repo: post-commit: demo/watch broke (returned refuse, which only pre-* and "
            "commit-msg git hooks take); the doctor keeps a record") in done.stderr
    cgd = gitconf.resolve(repo).common_dir
    assert _records.piece_key(cgd, "demo/watch") in _records.ledger()


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
    # A manager that keeps `.legacy` runs it, so the doctor says so and finds nothing wrong.
    [row] = [r for r in doctor(f"hooks {repo}") if "pre-commit.legacy" in r.detail]
    assert row.state == "ok" and problems() == []
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
    needed = {r.detail.split(":")[0]: r for r in problems(f"hooks {repo}")}
    assert set(needed) == {"pre-commit", "pre-push"}
    assert needed["pre-commit"].state == "warn"
    assert needed["pre-commit"].detail.startswith("pre-commit: chain line needed in ")
    assert needed["pre-commit"].fix.endswith(lines["pre-commit"])
    (tracked / "pre-commit").write_text(f"#!/bin/sh\n{lines['pre-commit']}\n")
    (tracked / "pre-commit").chmod(0o755)
    assert api.status(repo)[0]["events"]["pre-commit"]["chain_line"] == "present"
    (tracked / "pre-push").write_text(f"#!/bin/sh\n{lines['pre-push']}\n")
    (tracked / "pre-push").chmod(0o755)
    # The doctor reads both lines present.
    assert problems() == []
    [row] = doctor(f"hooks {repo}")
    assert row.state == "ok" and "pre-commit: 1 piece through launcher, chain line present" \
        in row.detail
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
    # With the chain line in place the pieces now skip quietly: a fail row each.
    rows = problems(f"hooks {repo}")
    assert {r.state for r in rows} == {"fail"} and len(rows) == 2
    assert all("launcher" in r.detail and r.fix == f"moxi repo git-hooks refresh {repo}"
               for r in rows)
    api.refresh(repo)
    assert problems() == []


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


def test_chain_lines_name_no_path():
    from moxi_repo import shim
    assert shim.chain_line("pre-commit", False) == (
        'h="$(git rev-parse --path-format=absolute --git-common-dir)/moxi-repo/hook"; '
        'if [ -f "$h" ]; then sh "$h" pre-commit "$@" || exit 1; fi')
    assert json.dumps(shim.chain_line("pre-push", True)).count("mktemp") == 1


# -- managers: a hooks dir repo doesn't own ------------------------------------------------

def manager_hook(path: Path, log: Path, name: str, *, before: str = "") -> None:
    """A manager's git hook at `path`: `before` (a chain line), then one log line."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!/bin/sh\n{before}\necho '{{\"id\": \"{name}\"}}' >> '{log}'\n",
                    encoding="utf-8")
    path.chmod(0o755)


def test_config_mode_under_a_managers_hooks_path_runs_both(demo):
    repo = demo.repo()
    husky = repo / ".husky" / "_"
    manager_hook(husky / "pre-commit", demo.log, "husky")
    git(repo, "config", "core.hooksPath", ".husky/_")
    api.add(repo, ["demo/gate"], mode="config")
    assert sorted(p.name for p in husky.iterdir()) == ["pre-commit"]
    change(repo)
    assert commit(repo).returncode == 0
    assert demo.ids() == ["demo/gate", "husky"]
    demo.act(gate="refuse")
    change(repo, text="2\n")
    assert commit(repo).returncode != 0
    # git runs the manager's hook whatever our entry returned, as with its own hooks.
    assert demo.ids() == ["demo/gate", "husky"]


def test_a_hooks_dir_two_repos_share_gets_nothing_written_and_each_its_own_launcher(demo):
    shared = demo.tmp / "shared-hooks"
    shared.mkdir()
    alpha, beta = demo.repo("alpha-notes"), demo.repo("beta-code")
    for repo in (alpha, beta):
        git(repo, "config", "core.hooksPath", str(shared))
    report = api.add(alpha, ["demo/gate"], mode="shim")
    api.add(beta, ["demo/lint"], mode="shim")
    assert list(shared.iterdir()) == []
    assert (alpha / ".git" / "moxi-repo" / "hook").exists()
    assert (beta / ".git" / "moxi-repo" / "hook").exists()
    [(_, event, hook, line, present)] = report.chain
    assert (event, hook, present) == ("pre-commit", str(shared / "pre-commit"), False)
    for repo in (alpha, beta):
        assert api.status(repo)[0]["events"]["pre-commit"]["chain_line"] == "needed"
    manager_hook(shared / "pre-commit", demo.log, "manager", before=line)
    for repo in (alpha, beta):
        assert api.status(repo)[0]["events"]["pre-commit"]["chain_line"] == "present"
    demo.act(gate="refuse", lint="refuse")
    change(alpha)
    assert commit(alpha).returncode != 0
    assert demo.ids() == ["demo/gate"]
    change(beta)
    # lint is open, so its refusal refuses too: each repo runs only its own pieces.
    assert commit(beta).returncode != 0
    assert demo.ids() == ["demo/lint"]
    demo.act(gate="ok", lint="ok")
    assert commit(alpha).returncode == 0 and commit(beta).returncode == 0
    assert demo.ids() == ["demo/gate", "manager", "demo/lint", "manager"]


def _temp_files() -> set[str]:
    import os
    return {p for p in os.listdir(os.environ["TMPDIR"]) if p.startswith("tmp.")}


def test_the_pre_push_chain_line_leaves_stdin_to_the_managers_lines(demo):
    repo = demo.repo()
    remote = demo.tmp / "remote.git"
    git(demo.tmp, "init", "-q", "--bare", "remote.git")
    git(repo, "remote", "add", "origin", str(remote))
    git(repo, "config", "core.hooksPath", ".githooks")
    report = api.add(repo, ["demo/push-check"], mode="shim")
    [(_, _, hook, line, _)] = report.chain
    seen = demo.tmp / "manager-stdin"
    Path(hook).parent.mkdir()
    Path(hook).write_text(f"#!/bin/sh\n{line}\ncat > '{seen}'\n", encoding="utf-8")
    Path(hook).chmod(0o755)
    before = _temp_files()
    git(repo, "push", "-q", "origin", "main")
    ref_line = f"refs/heads/main {head(repo)} refs/heads/main {'0' * 40}"
    [run] = demo.runs()
    assert run["stdin"].strip() == ref_line
    assert seen.read_text(encoding="utf-8").strip() == ref_line
    assert _temp_files() == before
    seen.unlink()
    demo.act(push_check="refuse")
    change(repo)
    commit(repo)
    done = git(repo, "push", "-q", "origin", "main", check=False)
    assert done.returncode != 0 and "push-check says no" in done.stderr
    assert not seen.exists()
    assert _temp_files() == before


# -- shims ---------------------------------------------------------------------------------

def test_a_shim_another_tool_replaced_shows_in_the_listing(demo):
    repo = demo.repo()
    api.add(repo, ["demo/gate"], mode="shim")
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    [listed] = api.status(repo)
    assert any("repo's shim is missing or another tool replaced it" in p
               for p in listed["problems"])
    [row] = problems()
    assert (row.state, row.name, row.fix) == (
        "fail", f"hooks {repo}", f"moxi repo git-hooks refresh {repo}")
    assert "repo's shim is missing or another tool replaced it" in row.detail
    # refresh puts the shim back, and the other tool's git hook runs after the pieces.
    api.refresh(repo)
    assert api.status(repo)[0]["problems"] == []
    assert problems() == []
    assert (hook.parent / "pre-commit.local").read_text() == "#!/bin/sh\nexit 0\n"


# -- the interpreter and the runner ----------------------------------------------------------

VIAS = ["config", "shim", "launcher"]


def _set_up(demo, via: str, pieces_: list[str]) -> Path:
    """A repo with `pieces_` reached through a config entry, a shim, or the launcher and its
    chain line in a tracked `.githooks/`."""
    repo = demo.repo()
    if via == "launcher":
        git(repo, "config", "core.hooksPath", ".githooks")
        report = api.add(repo, pieces_, mode="shim")
        lines = {event: line for _, event, _, line, _ in report.chain}
        (repo / ".githooks").mkdir()
        hook = repo / ".githooks" / "pre-commit"
        hook.write_text(f"#!/bin/sh\n{lines['pre-commit']}\n", encoding="utf-8")
        hook.chmod(0o755)
    else:
        api.add(repo, pieces_, mode=via)
    return repo


@pytest.mark.parametrize("via", VIAS)
@pytest.mark.parametrize("lost", ["interpreter", "runner"])
def test_a_lost_interpreter_or_runner_gives_the_guards_one_line(demo, via, lost):
    good = str(demo.hook_env["python"])
    if lost == "interpreter":
        gone = str(demo.tmp / "gone" / "python")
        demo.mp.setattr(gitconf, "interpreter", lambda: gone)
        python = gone
    else:
        gone = str(demo.tmp / "gone" / "moxi_repo" / "run.py")
        demo.mp.setattr(gitconf, "runner_file", lambda: gone)
        python = good
    repo = _set_up(demo, via, ["demo/gate", "demo/lint"])
    root = gitconf.resolve(repo).root
    what = (f"{python} is missing" if lost == "interpreter"
            else f"the environment of {python} has no moxi_repo")
    line = (f"repo: git hooks can't run here: {what}; run: moxi install repo "
            f"(or: moxi repo git-hooks remove {root})")
    change(repo)
    done = commit(repo)
    assert done.returncode != 0
    assert line in done.stderr.splitlines()
    assert "Traceback" not in done.stderr and demo.ids() == []
    # With only an open piece left, the event goes on, still saying the line.
    api.remove(repo, ["demo/gate"])
    done = commit(repo)
    assert done.returncode == 0, done.stderr
    assert line in done.stderr.splitlines() and demo.ids() == []
    # refresh writes this environment's paths again, and the pieces run.
    demo.mp.setattr(gitconf, "interpreter", lambda: good)
    demo.mp.setattr(gitconf, "runner_file", lambda: REAL_RUNNER_FILE())
    api.add(repo, ["demo/gate"])
    api.refresh(repo)
    change(repo, text="2\n")
    done = commit(repo)
    assert done.returncode == 0, done.stderr
    assert "can't run here" not in done.stderr
    assert demo.ids() == ["demo/gate", "demo/lint"]


REAL_INTERPRETER = gitconf.interpreter
REAL_RUNNER_FILE = gitconf.runner_file


def test_the_paths_written_are_the_environments_python_and_its_runner(demo):
    from importlib.util import find_spec

    from moxi import paths
    from moxi_repo import shim

    demo.mp.setattr(gitconf, "interpreter", REAL_INTERPRETER)
    python = Path(paths.python()).as_posix()
    runner = Path(find_spec("moxi_repo.run").origin).as_posix()
    assert gitconf.interpreter() == python and gitconf.runner_file() == runner
    assert Path(runner).is_file() and Path(runner).name == "run.py"
    repo = demo.repo()
    api.add(repo, ["demo/gate"], mode="config")
    command = gitconf.read(gitconf.resolve(repo)).entries["pre-commit"]
    assert f"[ -x {shim.sh_quote(python)} ]" in command
    assert f"[ -f {shim.sh_quote(runner)} ]" in command
    assert command.endswith(f"{shim.sh_quote(python)} -I -m moxi_repo.run pre-commit")


# -- the other events the first users need -----------------------------------------------------

@pytest.mark.parametrize("mode", MODES)
def test_a_commit_msg_piece_reads_the_message_and_can_refuse(demo, mode):
    repo = demo.repo()
    api.add(repo, ["demo/msg"], mode=mode)
    change(repo)
    assert commit(repo, message="a message").returncode == 0
    [run] = demo.runs()
    assert run["id"] == "demo/msg" and run["event"] == "commit-msg"
    assert run["message"] == "a message\n"
    demo.act(msg="refuse")
    change(repo, text="2\n")
    before = head(repo)
    done = commit(repo)
    assert done.returncode != 0 and head(repo) == before
    assert "repo: commit-msg refused by demo/msg: msg says no" in done.stderr
    assert commit(repo, "--no-verify").returncode == 0
    assert demo.ids() == ["demo/msg"]


@pytest.mark.parametrize("mode", MODES)
def test_a_post_merge_piece_runs_after_a_merge_and_never_stops_it(demo, mode):
    repo = demo.repo()
    api.add(repo, ["demo/after-merge"], mode=mode)
    git(repo, "checkout", "-q", "-b", "side")
    change(repo)
    commit(repo)
    git(repo, "checkout", "-q", "main")
    demo.act(after_merge="raise")
    done = git(repo, "merge", "-q", "--no-ff", "--no-edit", "side", check=False)
    assert done.returncode == 0, done.stderr
    [run] = demo.runs()
    assert run["id"] == "demo/after-merge" and run["args"] == ["0"]
    assert ("repo: post-merge: demo/after-merge broke (ValueError: after-merge fell over); "
            "the doctor keeps a record") in done.stderr
    cgd = gitconf.resolve(repo).common_dir
    assert _records.piece_key(cgd, "demo/after-merge") in _records.ledger()
    demo.act(after_merge="notice")
    git(repo, "checkout", "-q", "side")
    change(repo, text="2\n")
    commit(repo)
    git(repo, "checkout", "-q", "main")
    done = git(repo, "merge", "-q", "--no-edit", "side", check=False)
    assert done.returncode == 0
    assert "repo: post-merge, demo/after-merge: after-merge noticed something" in done.stderr
    assert _records.piece_key(cgd, "demo/after-merge") not in _records.ledger()


# -- the verbs' call forms ------------------------------------------------------------------

def test_add_and_remove_take_piece_ids_as_arguments_or_as_a_list(demo):
    repo = demo.repo()

    def registered() -> set[str]:
        return set(gitconf.read(gitconf.resolve(repo)).pieces)

    api.add(repo, "demo/gate")
    api.add(repo, "demo/lint", "demo/watch", mode="config")
    api.add(repo, ["demo/first"], sets=())
    assert registered() == {"demo/gate", "demo/lint", "demo/watch", "demo/first"}
    api.remove(repo, "demo/gate")
    api.remove(repo, "demo/lint", "demo/watch")
    api.remove(repo, piece="demo/first")
    assert registered() == set()
    assert gitconf.resolve(repo).key in api.indexed()


# -- the index: a repo is its common git dir -------------------------------------------------

def _entries_name(repo: Path, python: str) -> bool:
    return python in gitconf.read(gitconf.resolve(repo)).entries["pre-commit"]


def test_a_repo_added_through_a_linked_worktree_outlives_that_worktree(demo):
    repo = demo.repo()
    wt = demo.tmp / "wt"
    git(repo, "worktree", "add", "-q", str(wt), "-b", "side")
    api.add(wt, "demo/gate", "demo/watch", mode="config")
    [entry] = api.indexed().values()
    assert entry["root"] == str(repo)
    # The guard's fallback names the main work tree, not the linked one.
    assert f"remove {repo})" in gitconf.read(gitconf.resolve(repo)).entries["pre-commit"]
    git(repo, "worktree", "remove", str(wt))
    [listed] = api.status()
    assert listed["root"] == str(repo) and listed["problems"] == []
    moved = str(demo.hook_env["python"]) + "-moved"
    demo.mp.setattr(gitconf, "interpreter", lambda: moved)
    report = api.refresh()
    assert report.ok and f"refreshed {repo}" in report.lines
    assert _entries_name(repo, moved)
    # Its old path is not the repo: remove there refuses and leaves the repo alone.
    with pytest.raises(api.Usage):
        api.remove(wt)
    assert set(gitconf.read(gitconf.resolve(repo)).pieces) == {"demo/gate", "demo/watch"}


def test_an_index_entry_that_names_a_removed_linked_worktree_finds_the_repo(demo):
    repo = demo.repo()
    wt = demo.tmp / "wt"
    git(repo, "worktree", "add", "-q", str(wt), "-b", "side")
    api.add(repo, "demo/gate", mode="config")
    # An index written with the linked worktree as the repo's root.
    key = gitconf.resolve(repo).key
    api._index_change(lambda repos: repos[key].update(root=str(wt)))
    git(repo, "worktree", "remove", str(wt))
    assert [s["problems"] for s in api.status()] == [[]]
    assert problems(f"hooks {repo}") == [] and problems(f"hooks {wt}") == []
    moved = str(demo.hook_env["python"]) + "-moved"
    demo.mp.setattr(gitconf, "interpreter", lambda: moved)
    assert api.refresh().ok and _entries_name(repo, moved)
    assert api.indexed()[key]["root"] == str(repo)
    # remove on the old path names the live repo and leaves it alone.
    api._index_change(lambda repos: repos[key].update(root=str(wt)))
    report = api.remove(wt)
    assert not report.ok and f"moxi repo git-hooks remove {repo}" in report.problems[0]
    assert set(gitconf.read(gitconf.resolve(repo)).pieces) == {"demo/gate"}
    assert api.indexed()[key]["root"] == str(repo)


def test_an_indexed_path_now_inside_another_repo_is_that_repo_gone(demo):
    outer = demo.repo("outer")
    inner = demo.repo("outer/inner")
    api.add(outer, "demo/gate")
    api.add(inner, "demo/lint")
    inner_key = gitconf.resolve(inner).key
    import shutil
    shutil.rmtree(inner / ".git")
    listed = {s["root"]: s for s in api.status()}
    assert set(listed) == {str(outer), str(inner)}
    assert "no longer a git work tree" in listed[str(inner)]["problems"][0]
    assert listed[str(outer)]["problems"] == []
    with pytest.raises(api.Usage):
        api.remove(inner, "demo/gate")
    report = api.remove(inner)
    assert report.ok and inner_key not in api.indexed()
    assert set(gitconf.read(gitconf.resolve(outer)).pieces) == {"demo/gate"}
    assert gitconf.resolve(outer).key in api.indexed()


# -- shims left in the default hooks dir after core.hooksPath moved ----------------------------

def test_refresh_after_core_hookspath_moved_takes_the_old_shim_away_and_puts_back_the_hook(demo):
    repo = demo.repo()
    hooks = repo / ".git" / "hooks"
    existing_hook(repo, "pre-commit", demo.log)
    original = (hooks / "pre-commit").read_text()
    api.add(repo, "demo/gate", mode="shim")
    (repo / ".githooks").mkdir()
    git(repo, "config", "core.hooksPath", ".githooks")
    said = [p for p in api.status(repo)[0]["problems"] if str(hooks) in p.detail]
    assert said and all(p.fix == f"moxi repo git-hooks refresh {repo}" for p in said)
    assert any(r.state == "warn" and str(hooks) in r.detail for r in problems(f"hooks {repo}"))
    report = api.refresh(repo)
    assert report.ok
    assert (hooks / "pre-commit").read_text() == original
    assert not (hooks / "pre-commit.local").exists()
    assert gitconf.read(gitconf.resolve(repo)).moved == set()
    assert not [p for p in api.status(repo)[0]["problems"] if str(hooks) in p.detail]
    # Back in the default hooks dir, the next refresh places the shim again.
    git(repo, "config", "--unset", "core.hooksPath")
    api.refresh(repo)
    change(repo)
    assert commit(repo).returncode == 0
    assert demo.ids() == ["demo/gate", "existing"]


def test_a_moved_hook_stays_recorded_until_it_is_back(demo):
    repo = demo.repo()
    hooks = repo / ".git" / "hooks"
    existing_hook(repo, "pre-commit", demo.log)
    original = (hooks / "pre-commit").read_text()
    api.add(repo, "demo/gate", mode="shim")
    (repo / ".githooks").mkdir()
    git(repo, "config", "core.hooksPath", ".githooks")
    # Another tool takes the slot in the old dir: the moved hook can't go back yet.
    (hooks / "pre-commit").write_text("#!/bin/sh\n")
    api.refresh(repo)
    assert (hooks / "pre-commit.local").read_text() == original
    assert gitconf.read(gitconf.resolve(repo)).moved == {"pre-commit"}
    assert any("pre-commit.local" in p.detail for p in api.status(repo)[0]["problems"])
    (hooks / "pre-commit").unlink()
    api.refresh(repo)
    assert (hooks / "pre-commit").read_text() == original
    assert gitconf.read(gitconf.resolve(repo)).moved == set()


def test_remove_after_core_hookspath_moved_cleans_the_default_hooks_dir(demo):
    repo = demo.repo()
    hooks = repo / ".git" / "hooks"
    existing_hook(repo, "pre-commit", demo.log)
    original = (hooks / "pre-commit").read_text()
    api.add(repo, "demo/gate", mode="shim")
    (repo / ".githooks").mkdir()
    git(repo, "config", "core.hooksPath", ".githooks")
    api.remove(repo)
    assert (hooks / "pre-commit").read_text() == original
    assert not (hooks / "pre-commit.local").exists()
    assert gitconf.read(gitconf.resolve(repo)).sections == set()
    git(repo, "config", "--unset", "core.hooksPath")
    change(repo)
    assert commit(repo).returncode == 0 and demo.ids() == ["existing"]


def test_a_shim_left_in_the_default_dir_goes_when_another_inside_dir_takes_over(demo):
    repo = demo.repo()
    hooks = repo / ".git" / "hooks"
    api.add(repo, "demo/gate", mode="shim")
    git(repo, "config", "core.hooksPath", str(repo / ".git" / "own-hooks"))
    api.refresh(repo)
    assert shim_files(repo / ".git" / "own-hooks") == ["pre-commit"]
    assert shim_files(hooks) == []


def shim_files(where: Path) -> list[str]:
    from moxi_repo import shim
    return sorted(shim.our_shims(where))


@pytest.mark.parametrize("mode", MODES)
def test_an_event_whose_pieces_are_all_disabled_loses_its_entry_or_shim(demo, mode):
    repo = demo.repo()
    hooks = repo / ".git" / "hooks"
    existing_hook(repo, "pre-commit", demo.log)
    original = (hooks / "pre-commit").read_text()
    api.add(repo, "demo/gate", "demo/lint", "demo/watch", mode=mode)
    api.disable(repo, "demo/gate")
    api.disable(repo, "demo/lint")
    state = gitconf.read(gitconf.resolve(repo))
    assert set(state.entries) == ({"post-commit"} if mode == "config" else set())
    assert (hooks / "pre-commit").read_text() == original
    assert not (hooks / "pre-commit.local").exists()
    [listed] = api.status(repo)
    assert listed["problems"] == [] and problems(f"hooks {repo}") == []
    assert listed["events"]["pre-commit"]["via"] is None
    api.enable(repo, "demo/lint")
    change(repo)
    assert commit(repo).returncode == 0
    assert demo.ids() == ["demo/lint", "existing", "demo/watch"]

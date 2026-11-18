"""What a sync would write, before it writes (`gitconf.plan_sync`), and the sync writing
exactly that; and the per-repo lock every verb that writes a repo's git hooks holds.

Each sync test reads every key of repo's in the repo's config and every file in its hooks
dirs and launcher before and after the sync, and checks that what changed is what the plan
named, item by item."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from moxi_repo import api, gitconf, pieces, shim
from tests.conftest import POSIX_ONLY, git

pytestmark = POSIX_ONLY


def config_keys(repo: Path) -> dict[str, list[str]]:
    done = git(repo, "config", "--local", "--list", check=False)
    out: dict[str, list[str]] = {}
    for line in done.stdout.splitlines():
        key, _, value = line.partition("=")
        if key.startswith(("moxi-repo.", "hook.moxi-repo-")):
            out.setdefault(key, []).append(value)
    return out


def files_of(where: gitconf.Repo) -> dict[str, tuple[str, int]]:
    out = {}
    for hooks_dir in {where.hooks_dir, where.common_dir / "hooks"}:
        if hooks_dir.is_dir():
            for path in hooks_dir.iterdir():
                if path.is_file():
                    out[str(path)] = (path.read_text(encoding="utf-8", errors="replace"),
                                      path.stat().st_mode & 0o777)
    if where.launcher.exists():
        out[str(where.launcher)] = (where.launcher.read_text(encoding="utf-8"),
                                    where.launcher.stat().st_mode & 0o777)
    return out


def snapshot(where: gitconf.Repo) -> tuple[dict, dict]:
    return config_keys(where.root), files_of(where)


def changed(before: tuple[dict, dict], after: tuple[dict, dict]) -> set[str]:
    out = set()
    for old, new in zip(before, after):
        out |= {key for key in set(old) | set(new) if old.get(key) != new.get(key)}
    return out


def touched(writes: list[dict]) -> set[str]:
    """The config keys and files a plan's items name as written."""
    out = set()
    for item in writes:
        if item["place"] == "config":
            out.add(item["key"])
        elif item["place"] == "hooks":
            out |= {path for path in (item["from"], item["to"]) if path}
    return out


def synced(where: gitconf.Repo, state: gitconf.State) -> tuple[list[dict], gitconf.Sync]:
    """Plan the sync, run it, and check it wrote exactly what the plan named."""
    plan = gitconf.plan_sync(where, state)
    before = snapshot(where)
    done = gitconf.sync(where, state, plan)
    assert changed(before, snapshot(where)) == touched(plan.writes)
    # Nothing is left to write after it.
    assert gitconf.plan_sync(where, gitconf.read(where)).writes == []
    return plan.writes, done


def declared(pid: str) -> pieces.Piece:
    return pieces.declared()[0][pid]


def pending(repo: Path, *pids: str, mode: str) -> tuple[gitconf.Repo, gitconf.State]:
    """The repo's State with `pids` registered and `mode` set, before any sync."""
    where = gitconf.resolve(repo)
    state = gitconf.read(where)
    gitconf.set_mode(where, state, mode=mode, mode_set="auto")
    for pid in pids:
        gitconf.register(where, declared(pid), state)
    return where, state


def own_hook(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o755)
    return path


# -- the plan, and the sync writing it ------------------------------------------------------

def test_a_first_config_entry_is_two_keys_from_nothing(demo):
    where, state = pending(demo.repo(), "demo/gate", mode="config")
    writes, _ = synced(where, state)
    assert [(i["place"], i["event"], i["key"], i["from"]) for i in writes] == [
        ("config", "pre-commit", "hook.moxi-repo-pre-commit.command", None),
        ("config", "pre-commit", "hook.moxi-repo-pre-commit.event", None)]
    assert writes[1]["to"] == "pre-commit" and "moxi_repo.run" in writes[0]["to"]
    assert all(i["piece"] is None and i["seed"] is False for i in writes)


def test_a_synced_repo_plans_nothing_and_its_sync_writes_nothing(demo):
    repo = demo.repo()
    api.add(repo, ["demo/gate", "demo/watch"], mode="shim")
    where = gitconf.resolve(repo)
    config = (where.common_dir / "config").read_bytes()
    stamp = (where.hooks_dir / "pre-commit").stat().st_mtime_ns
    assert gitconf.plan_sync(where, gitconf.read(where)).writes == []
    assert gitconf.sync(where, gitconf.read(where)).lines == []
    assert (where.common_dir / "config").read_bytes() == config
    assert (where.hooks_dir / "pre-commit").stat().st_mtime_ns == stamp


def test_an_entry_removed_by_hand_is_planned_back(demo):
    repo = demo.repo()
    api.add(repo, ["demo/gate"], mode="config")
    git(repo, "config", "--remove-section", "hook.moxi-repo-pre-commit")
    where = gitconf.resolve(repo)
    writes, _ = synced(where, gitconf.read(where))
    assert [(i["key"], i["from"]) for i in writes] == [
        ("hook.moxi-repo-pre-commit.command", None), ("hook.moxi-repo-pre-commit.event", None)]


def test_his_hook_moves_aside_for_the_shim_and_the_move_is_recorded(demo):
    repo = demo.repo()
    hooks = repo / ".git" / "hooks"
    own_hook(hooks / "pre-commit")
    where, state = pending(repo, "demo/gate", mode="shim")
    writes, done = synced(where, state)
    assert [(i["place"], i["event"], i["key"], i["from"], i["to"]) for i in writes] == [
        ("hooks", "pre-commit", "hook", str(hooks / "pre-commit"),
         str(hooks / "pre-commit.local")),
        ("hooks", "pre-commit", "shim", None, str(hooks / "pre-commit")),
        ("config", None, "moxi-repo.moved", None, "pre-commit")]
    assert done.lines == [f"moved the existing git hook {hooks / 'pre-commit'} to "
                          "pre-commit.local"]
    assert shim.shim_event(hooks / "pre-commit") == "pre-commit"


def test_shim_mode_to_config_takes_the_shim_and_puts_his_hook_back(demo):
    repo = demo.repo()
    hooks = repo / ".git" / "hooks"
    own_hook(hooks / "pre-commit")
    api.add(repo, ["demo/gate"], mode="shim")
    where = gitconf.resolve(repo)
    state = gitconf.read(where)
    gitconf.set_mode(where, state, mode="config")
    writes, _ = synced(where, state)
    assert [(i["place"], i["key"], i["from"], i["to"]) for i in writes] == [
        ("config", "hook.moxi-repo-pre-commit.command", None, writes[0]["to"]),
        ("config", "hook.moxi-repo-pre-commit.event", None, "pre-commit"),
        ("hooks", "shim", str(hooks / "pre-commit"), None),
        ("hooks", "hook", str(hooks / "pre-commit.local"), str(hooks / "pre-commit")),
        ("config", "moxi-repo.moved", "pre-commit", None)]
    assert shim.shim_event(hooks / "pre-commit") is None


def test_every_piece_of_an_event_off_takes_its_shim_away(demo):
    repo = demo.repo()
    api.add(repo, ["demo/gate", "demo/watch"], mode="shim")
    where = gitconf.resolve(repo)
    state = gitconf.read(where)
    gitconf.set_enabled(where, "demo/watch", enabled=False, state=state)
    writes, _ = synced(where, state)
    assert [(i["event"], i["key"], i["to"]) for i in writes] == [
        ("post-commit", "shim", None)]


def test_a_changed_shim_is_planned_as_rewritten_in_place(demo):
    repo = demo.repo()
    api.add(repo, ["demo/gate"], mode="shim")
    where = gitconf.resolve(repo)
    hook = where.hooks_dir / "pre-commit"
    hook.write_text(hook.read_text(encoding="utf-8") + "# an older shim\n", encoding="utf-8")
    writes, _ = synced(where, gitconf.read(where))
    assert [(i["key"], i["from"], i["to"]) for i in writes] == [
        ("shim", str(hook), str(hook))]


def test_launcher_mode_plans_the_launcher_and_the_chain_line_it_doesnt_write(demo):
    repo = demo.repo()
    git(repo, "config", "core.hooksPath", ".githooks")
    (repo / ".githooks").mkdir()
    where, state = pending(repo, "demo/gate", mode="shim")
    writes, done = synced(where, state)
    hook, line, present = gitconf.chain_state(where, "pre-commit")
    assert [(i["place"], i["key"], i["from"], i["to"]) for i in writes] == [
        ("hooks", "launcher", None, str(where.launcher)),
        ("chain", None, str(hook), line)]
    assert not present and not hook.exists()
    assert done.chain == {"pre-commit": (hook, line, False)}
    # Once he adds the line, nothing is left to plan.
    own_hook(hook).write_text(f"#!/bin/sh\n{line}\n", encoding="utf-8")
    assert gitconf.plan_sync(where, gitconf.read(where)).writes == []


def test_a_shim_that_cant_be_placed_is_a_problem_and_no_item(demo):
    repo = demo.repo()
    hooks = repo / ".git" / "hooks"
    own_hook(hooks / "pre-commit")
    own_hook(hooks / "pre-commit.local")
    where, state = pending(repo, "demo/gate", mode="shim")
    plan = gitconf.plan_sync(where, state)
    assert plan.writes == []
    [problem] = plan.problems
    assert problem.startswith("pre-commit: ") and "both exist" in problem
    assert gitconf.sync(where, state, plan).problems == plan.problems


# -- the per-repo lock ----------------------------------------------------------------------

HOLD = """
import sys
from moxi import files
with files.lock(sys.argv[1]):
    print("held", flush=True)
    sys.stdin.read()
"""


@pytest.fixture
def held(monkeypatch):
    """Hold a repo's lock from another process while the block runs."""
    monkeypatch.setattr(api, "LOCK_WAIT_S", 0.2)
    holders = []

    def hold(repo: Path) -> subprocess.Popen:
        lock = gitconf.resolve(repo).own_dir / api.HOOKS_LOCK
        proc = subprocess.Popen([sys.executable, "-c", HOLD, str(lock)], stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, text=True)
        holders.append(proc)
        assert proc.stdout.readline().strip() == "held"
        return proc

    yield hold
    for proc in holders:
        proc.stdin.close()
        proc.wait(timeout=30)


def test_each_verb_that_writes_a_repo_is_busy_while_another_holds_its_lock(demo, held):
    repo = demo.repo()
    api.add(repo, ["demo/gate", "demo/watch"], mode="shim")
    config = (repo / ".git" / "config").read_bytes()
    held(repo)
    for verb in (lambda: api.add(repo, ["demo/lint"]), lambda: api.add(repo, mode="config"),
                 lambda: api.remove(repo, "demo/watch"), lambda: api.remove(repo),
                 lambda: api.enable(repo, "demo/watch"),
                 lambda: api.disable(repo, "demo/watch"), lambda: api.refresh(repo)):
        with pytest.raises(api.Busy) as caught:
            verb()
        assert caught.value.code == 75
        assert str(repo) in caught.value.text and "try again" in caught.value.text
    for report in (api.refresh(), api.install_module("demo"), api.uninstall_module("demo"),
                   api.teardown()):
        assert any(str(repo) in p and "changing repo's git hooks" in p
                   for p in report.problems), report.problems
    assert (repo / ".git" / "config").read_bytes() == config
    assert api.indexed_at(repo)


def test_indexing_alone_takes_no_lock_and_writes_nothing_in_the_repo(demo, held):
    repo, other = demo.repo(), demo.repo("other")
    api.add(other, ["demo/gate"], mode="shim")
    held(other)
    assert api.add(repo).lines == [f"indexed {repo}"]
    assert not (repo / ".git" / "moxi-repo").exists()


def test_no_verb_takes_a_repos_lock_twice(demo, monkeypatch):
    from moxi import files

    repo, other = demo.repo(), demo.repo("other")
    taken: list[str] = []
    lock = files.lock

    def counting(path, **kwargs):
        if Path(path).name == api.HOOKS_LOCK:
            taken.append(str(Path(path).parent.parent))
        return lock(path, **kwargs)

    monkeypatch.setattr(files, "lock", counting)
    gits = {str(gitconf.resolve(r).common_dir) for r in (repo, other)}
    for verb in (lambda: api.add(repo, ["demo/gate", "demo/watch"], mode="shim"),
                 lambda: api.add(other, ["demo/gate"]),
                 lambda: api.disable(repo, "demo/watch"),
                 lambda: api.enable(repo, "demo/watch"),
                 lambda: api.remove(repo, "demo/watch"), lambda: api.refresh(repo)):
        taken.clear()
        verb()
        assert len(taken) == 1 and taken[0] in gits
    for verb in (api.refresh, lambda: api.install_module("demo"),
                 lambda: api.uninstall_module("demo"), api.teardown):
        taken.clear()
        verb()
        assert sorted(taken) == sorted(set(taken)), taken

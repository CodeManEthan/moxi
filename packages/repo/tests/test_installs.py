"""Other modules' installs, upgrades and uninstalls, through repo's `moxi.installs`
listener, and repo's own uninstall, through its `TEARDOWN`.

The listener is called the way the core calls it: every entry of the `moxi.installs` group,
loaded, and its `on_install(module, found)` or `on_uninstall(module)` called. The module
installed and upgraded here is a fake one, `ears`, in the test's own site: its pieces'
declarations live there, and their targets are the demo pieces' functions, which the git
hooks' own environment can import. An upgrade rewrites its entry points to point at a new
declaration module, as a new version's metadata would.

`moxi uninstall repo` refuses this workspace's editable `moxi`, so the uninstall's steps
are driven one by one: the teardown child the core starts (`python -m moxi._settle
--teardown repo`), then the listeners.
"""

from __future__ import annotations

import importlib
import json
import subprocess
from pathlib import Path

import pytest

import moxi.modules
import moxi_repo
from moxi_repo import _records, api, committer, gitconf
from moxi_repo import install as repo_install
from tests.conftest import POSIX_ONLY, change, commit, git, problems
from tests.test_committer import deny, make_remote, spec, write
from tests.test_records import OPEN, doctor_records, key, left

pytestmark = POSIX_ONLY

V1 = '''
GATE = {"event": "pre-commit", "target": "demo_pieces.hooks:gate", "fail": "closed",
        "order": 20, "doc": "A closed gate.", "settings": {"label": "one"}}
WATCH = {"event": "post-commit", "target": "demo_pieces.hooks:watch", "fail": "open",
         "order": 50, "doc": "Watches commits."}
OLD = {"event": "post-commit", "target": "demo_pieces.hooks:watch", "fail": "open",
       "order": 60, "doc": "Dropped by the next version."}
'''

# The next version: the gate's function and order move, the watcher's function moves, and
# the old piece is no longer declared.
V2 = '''
GATE = {"event": "pre-commit", "target": "demo_pieces.hooks:lint", "fail": "closed",
        "order": 25, "doc": "A closed gate.", "settings": {"label": "one"}}
WATCH = {"event": "post-commit", "target": "demo_pieces.hooks:after_merge", "fail": "open",
         "order": 50, "doc": "Watches commits."}
'''


class Ears:
    """The fake module `ears`, in the test's site, at one version or the next."""

    def __init__(self, site):
        self.site = site
        site.module("ears", package="ears_module")
        site.write("ears_v1.py", V1)
        site.write("ears_v2.py", V2)
        self.entries({"gate": "ears_v1:GATE", "watch": "ears_v1:WATCH",
                      "old": "ears_v1:OLD"})

    def entries(self, pieces: dict[str, str]) -> None:
        self.site.write("moxi_ears-0.1.0.dist-info/entry_points.txt",
                        "[moxi.modules]\nears = ears_module.module\n\n"
                        "[moxi.repo.git_hooks]\n"
                        + "".join(f"ears.{name} = {target}\n"
                                  for name, target in pieces.items()))
        moxi.modules.reset()
        importlib.invalidate_caches()

    def upgrade(self) -> None:
        self.entries({"gate": "ears_v2:GATE", "watch": "ears_v2:WATCH"})


@pytest.fixture
def ears(site) -> Ears:
    return Ears(site)


def installed(module: str) -> None:
    """What the core does last in a module's settle step."""
    found = moxi.modules.load(module)
    for ext in moxi.modules.extensions("moxi.installs"):
        getattr(ext.load(), "on_install")(module, found)


def uninstalled(module: str) -> None:
    """What the core does after a module's `TEARDOWN` accepts."""
    for ext in moxi.modules.extensions("moxi.installs"):
        getattr(ext.load(), "on_uninstall")(module)


def section(repo: Path, piece: str) -> dict[str, list]:
    return gitconf.read(gitconf.resolve(repo)).pieces.get(piece, {})


def ours_in_config(repo: Path) -> str:
    """What repo left in the repo's local config: every moxi-repo key and hook entry."""
    done = git(repo, "config", "--local", "--get-regexp", r"^(moxi-repo\.|hook\.moxi-repo-)",
               check=False)
    return done.stdout


def test_repo_registers_its_listener_and_teardown():
    [ext] = [e for e in moxi.modules.extensions("moxi.installs") if e.module == "repo"]
    assert ext.target == "moxi_repo.install"
    assert callable(ext.load().on_install) and callable(ext.load().on_uninstall)
    assert moxi.modules.load("repo").teardown == "moxi_repo.install:teardown"


# -- on_install ----------------------------------------------------------------------------

def test_an_upgrade_that_moves_targets_is_rewritten_and_keeps_every_other_key(
        demo, ears, capsys):
    alpha, beta = demo.repo("alpha-notes"), demo.repo("beta-code")
    api.add(alpha, ["ears/gate"], sets=["label=kept"])
    api.add(alpha, ["demo/gate", "ears/watch", "ears/old"])
    api.add(beta, ["ears/gate"])
    api.disable(beta, "ears/gate")
    demo.act(watch="record")
    change(alpha)
    assert commit(alpha).returncode == 0
    assert demo.ids() == ["demo/gate", "demo/gate", "demo/watch", "demo/watch"]
    assert left(alpha, "ears/watch") == OPEN and left(alpha, "ears/old") == OPEN
    before = {pid: section(alpha, pid) for pid in ("demo/gate", "ears/gate")}
    capsys.readouterr()

    ears.upgrade()
    installed("ears")
    out = capsys.readouterr().out.splitlines()
    assert out == [f"rewrote ears/gate (pre-commit, closed) in {alpha}",
                   f"removed ears/old from {alpha}: ears no longer declares it",
                   f"rewrote ears/watch (post-commit, open) in {alpha}",
                   f"rewrote ears/gate (pre-commit, closed) in {beta}"]
    gate = section(alpha, "ears/gate")
    assert gate == {**before["ears/gate"], "target": ["demo_pieces.hooks:lint"],
                    "order": ["25"]}
    assert gate["label"] == ["kept"] and gate["enabled"] == ["true"]
    assert section(alpha, "demo/gate") == before["demo/gate"]
    assert section(alpha, "ears/watch")["target"] == ["demo_pieces.hooks:after_merge"]
    assert section(alpha, "ears/old") == {}
    assert section(beta, "ears/gate")["enabled"] == ["false"]
    # The dropped piece went as `remove` takes it: record, ledger key and flag. The moved
    # one keeps its flag and record until it next runs.
    assert left(alpha, "ears/old") == set()
    assert left(alpha, "ears/watch") == OPEN

    demo.act(watch="ok")
    change(alpha, text="2\n")
    assert commit(alpha).returncode == 0
    assert demo.ids() == ["demo/gate", "demo/lint", "demo/after-merge"]
    assert left(alpha, "ears/watch") == set()
    assert doctor_records() == {} and _records.ledger() == {}

    # Nothing changed since, so a second install writes and prints nothing.
    installed("ears")
    assert capsys.readouterr().out == ""
    change(beta)
    assert commit(beta).returncode == 0 and demo.ids() == []


def test_a_moved_closed_target_no_longer_refuses_once_rewritten(demo, ears, capsys):
    """Without the rewrite, a closed piece whose function moved refuses every commit."""
    repo = demo.repo()
    api.add(repo, ["ears/gate"])
    git(repo, "config", "moxi-repo.piece.ears/gate.target", "ears_gone.hooks:gate")
    change(repo)
    assert commit(repo).returncode != 0
    ears.upgrade()
    installed("ears")
    assert commit(repo).returncode == 0
    assert demo.ids() == ["demo/lint"]


def test_on_install_of_a_module_with_no_pieces_or_with_nothing_indexed(demo, ears, capsys):
    installed("ears")
    assert capsys.readouterr() == ("", "")
    repo = demo.repo()
    api.add(repo, ["demo/gate"])
    before = ours_in_config(repo)
    installed("ears")
    installed("repo")
    assert capsys.readouterr() == ("", "")
    assert ours_in_config(repo) == before


def test_on_install_names_a_refused_declaration_and_leaves_it(demo, ears, capsys):
    repo = demo.repo()
    api.add(repo, ["ears/gate"])
    ears.site.write("ears_v3.py", "GATE = {'event': 'pre-commit'}\n")
    ears.entries({"gate": "ears_v3:GATE"})
    before = section(repo, "ears/gate")
    installed("ears")
    out, err = capsys.readouterr()
    assert out == ""
    assert err.startswith(f"repo: {repo}: ears/gate's declaration is refused (")
    assert err.rstrip().endswith("its registration is left as it is")
    assert section(repo, "ears/gate") == before


def test_on_install_goes_on_past_a_repo_whose_config_cannot_be_read(demo, ears, capsys):
    alpha, beta = demo.repo("alpha-notes"), demo.repo("beta-code")
    for r in (alpha, beta):
        api.add(r, ["ears/gate"])
    config = alpha / ".git" / "config"
    config.write_text(config.read_text(encoding="utf-8") + "[broken\n", encoding="utf-8")
    ears.upgrade()
    installed("ears")
    out, err = capsys.readouterr()
    assert out == f"rewrote ears/gate (pre-commit, closed) in {beta}\n"
    assert err.startswith(f"repo: {alpha}: ") and "bad config line" in err
    assert "so it is left as it is" in err


def test_an_unreadable_index_fails_the_listener(demo, ears):
    api.add(demo.repo(), ["ears/gate"])
    api.index_path().write_text("[1, 2]", encoding="utf-8")
    with pytest.raises(api.IndexUnreadable):
        installed("ears")


# -- on_uninstall --------------------------------------------------------------------------

def test_on_uninstall_removes_the_modules_pieces_and_specs_with_their_records(
        demo, ears, capsys):
    alpha, beta = demo.repo("alpha-notes"), demo.repo("beta-code")
    clock = [1000.0]
    demo.mp.setattr(committer, "_now", lambda: clock[0])
    bare = make_remote(demo, alpha)
    deny(bare, True)
    api.add(alpha, ["ears/gate", "ears/watch", "demo/lint"])
    api.add(beta, ["ears/gate"])
    moxi_repo.save_spec(spec(alpha, name="ears/snap", push="origin"))
    # A refused pass, then one that commits and fails to push, then a refused one: the
    # saved spec has a commit record and a push record.
    demo.act(gate="refuse", watch="record", lint="raise")
    write(alpha, "notes/a.md")
    assert moxi_repo.commit_saved(alpha, "ears/snap").status == "refused"
    demo.act(gate="ok")
    clock[0] += 1
    assert moxi_repo.commit_saved(alpha, "ears/snap").status == "committed"
    demo.act(gate="refuse")
    write(alpha, "notes/b.md")
    assert moxi_repo.commit_saved(alpha, "ears/snap").status == "refused"
    # An unsaved spec of ears, and one of demo, which stays.
    assert moxi_repo.commit(spec(alpha, name="ears/scratch")).status == "refused"
    assert moxi_repo.commit(spec(alpha, name="demo/snap")).status == "refused"
    cgd = alpha / ".git"
    ears_keys = {_records.commit_key(cgd, "ears/snap"),
                 _records.push_key(cgd, "ears/snap", "main"),
                 _records.commit_key(cgd, "ears/scratch"), key(alpha, "ears/watch")}
    kept = {_records.commit_key(cgd, "demo/snap"), key(alpha, "demo/lint")}
    assert set(_records.ledger()) == ears_keys | kept
    change(beta)
    assert commit(beta).returncode != 0
    capsys.readouterr()

    uninstalled("ears")
    out = capsys.readouterr().out
    for line in (f"removed ears/gate from {alpha}: ears is uninstalled",
                 f"removed ears/watch from {alpha}: ears is uninstalled",
                 f"removed ears/gate from {beta}: ears is uninstalled",
                 f"removed the saved spec ears/snap from {alpha}",
                 f"removed ears/snap's state from {alpha}",
                 f"removed ears/scratch's state from {alpha}"):
        assert line in out, out
    assert set(_records.ledger()) == kept and set(doctor_records()) == kept
    assert left(alpha, "ears/watch") == set()
    assert set(gitconf.read(gitconf.resolve(alpha)).pieces) == {"demo/lint"}
    assert gitconf.read(gitconf.resolve(beta)).pieces == {}
    assert committer.saved_names(cgd) == []
    assert set(committer.read_state(cgd)["specs"]) == {"demo/snap"}
    # beta's only piece is gone, and so is its entry; nothing refuses a commit there.
    assert commit(beta).returncode == 0
    assert not [r for r in problems() if r.name.startswith(("hooks ", "ledger "))]
    # Uninstalling it again finds nothing.
    uninstalled("ears")
    assert capsys.readouterr().out == ""


def test_a_piece_whose_module_went_is_reported_and_on_uninstall_removes_it(demo, ears):
    """As when a module was uninstalled without repo's listener running (installed while
    repo wasn't): the listing, which the doctor's `hooks` row reads, names its closed piece
    as a failure, and `on_uninstall` takes it out. (The test site lets the core load only
    its own modules, so this reads the listing rather than `moxi doctor`.)"""
    repo = demo.repo()
    api.add(repo, ["ears/gate", "demo/lint"])
    # Its package went with it, so its target no longer imports.
    git(repo, "config", "moxi-repo.piece.ears/gate.target", "ears_gone.hooks:gate")
    ears.entries({})
    [problem] = api.status(repo)[0]["problems"]
    assert problem.state == "fail"
    assert problem.detail == "ears/gate is registered but no installed module declares it"
    change(repo)
    assert commit(repo).returncode != 0
    uninstalled("ears")
    assert api.status(repo)[0]["problems"] == []
    assert commit(repo).returncode == 0


# -- TEARDOWN ------------------------------------------------------------------------------

def _everything(demo):
    """Repos of every kind with records open: alpha in shim mode over an existing git hook,
    beta in config mode, gamma through the launcher with its chain line in the manager's
    git hook, and delta indexed and then deleted. Returns them and alpha's own hook text."""
    alpha, beta = demo.repo("alpha-notes"), demo.repo("beta-code")
    gamma, delta = demo.repo("gamma-docs"), demo.repo("delta-gone")
    mine = "#!/bin/sh\necho the existing hook of alpha\n"
    hook = alpha / ".git" / "hooks" / "pre-commit"
    hook.write_text(mine, encoding="utf-8")
    hook.chmod(0o755)
    api.add(alpha, ["demo/gate", "demo/lint"], mode="shim")
    assert (alpha / ".git" / "hooks" / "pre-commit.local").read_text() == mine
    api.add(beta, ["demo/lint", "demo/watch"], mode="config")
    git(gamma, "config", "core.hooksPath", ".githooks")
    report = api.add(gamma, ["demo/gate", "demo/push-check"], mode="shim")
    managed = gamma / ".githooks" / "pre-commit"
    managed.parent.mkdir()
    [(_, _, _, line, _)] = [c for c in report.chain if c[1] == "pre-commit"]
    managed.write_text(f"#!/bin/sh\n{line}\necho a line of the manager\n", encoding="utf-8")
    managed.chmod(0o755)
    git(gamma, "add", ".githooks/pre-commit")
    api.add(delta, ["demo/lint"])
    demo.act(lint="raise", watch="record")
    for r in (alpha, beta, delta):
        change(r)
        commit(r)
    moxi_repo.save_spec(spec(beta, name="demo/snap"))
    import shutil

    shutil.rmtree(delta)
    # A key of a repo the index doesn't name: every ledger key goes all the same.
    _records.open_record(_records.piece_key(demo.tmp / "elsewhere.git", "demo/lint"),
                         cgd=demo.tmp / "elsewhere.git", kind="piece",
                         subject={"piece": "demo/lint"}, text="a stray record")
    assert len(_records.ledger()) == 5
    demo.runs()
    return (alpha, beta, gamma, delta), mine, managed


def _cleaned(demo, repos, mine, managed, out: str) -> None:
    alpha, beta, gamma, delta = repos
    assert f"dropped {delta} from the index: {delta} is no longer a git work tree" in out
    for r in (alpha, beta, gamma):
        assert f"removed repo's git hooks from {r}" in out
        assert ours_in_config(r) == ""
        assert not (r / ".git" / "moxi-repo").exists()
    assert (f"{managed} still holds repo's chain line for pre-commit, which does nothing "
            "now; delete that line") in out
    assert out.count("still holds repo's chain line") == 1
    hooks = alpha / ".git" / "hooks"
    assert (hooks / "pre-commit").read_text() == mine
    assert not (hooks / "pre-commit.local").exists()
    assert api.indexed() == {}
    assert _records.ledger() == {} and doctor_records() == {}
    assert not [r for r in problems() if r.name.startswith(("hooks ", "ledger ", "record "))]
    # Commits go on with nothing of repo's: alpha's own hook runs, gamma's chain line skips.
    demo.act(lint="raise", gate="refuse")
    for r in (alpha, beta, gamma):
        change(r, text="after\n")
        done = commit(r)
        assert done.returncode == 0, (r, done.stderr)
    assert demo.ids() == []


def test_teardown_removes_everything_and_restores_each_local(demo, capsys):
    repos, mine, managed = _everything(demo)
    capsys.readouterr()
    assert repo_install.teardown() == 0
    out, err = capsys.readouterr()
    assert err == ""
    _cleaned(demo, repos, mine, managed, out)


def test_the_uninstall_steps_teardown_child_then_listeners(demo, capsys):
    """`moxi uninstall repo`'s steps as the core runs them: the teardown in a child of the
    environment's own Python, then every listener's `on_uninstall("repo")`."""
    repos, mine, managed = _everything(demo)
    done = subprocess.run([str(demo.hook_env["python"]), "-m", "moxi._settle", "--teardown",
                           "repo"], capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stdout + done.stderr
    uninstalled("repo")
    out, err = capsys.readouterr()
    assert out == "" and err == ""
    _cleaned(demo, repos, mine, managed, done.stdout)


def test_teardown_with_nothing_indexed_clears_the_ledger(demo, capsys):
    _records.open_record("x::piece::demo/lint", cgd=demo.tmp / "x", kind="piece",
                         subject={"piece": "demo/lint"}, text="a stray record")
    assert repo_install.teardown() == 0
    assert capsys.readouterr() == ("", "")
    assert _records.ledger() == {} and doctor_records() == {}


@pytest.mark.parametrize("mode", ["config", "shim"])
def test_teardown_cleans_a_git_dir_left_with_no_work_tree(demo, mode, capsys):
    """A bare repo whose only worktree was removed keeps repo's keys in its config, and a
    worktree added later would run them; the teardown takes them out through
    `git --git-dir`. (Writing config entries needs no new git; only running them does.)"""
    src = demo.repo("src")
    bare, wt = demo.tmp / "proj.git", demo.tmp / "wt"
    git(demo.tmp, "clone", "-q", "--bare", str(src), str(bare))
    mine = "#!/bin/sh\necho the existing hook of proj\n"
    hook = bare / "hooks" / "pre-commit"
    hook.write_text(mine, encoding="utf-8")
    hook.chmod(0o755)
    git(bare, "worktree", "add", "-q", str(wt), "-b", "side")
    api.add(wt, ["demo/gate"], mode=mode)
    git(bare, "worktree", "remove", str(wt))
    theirs = git(bare, "config", "--local", "--get-regexp", r"^(moxi-repo\.|hook\.moxi-repo-)",
                 check=False).stdout
    assert "moxi-repo.piece.demo/gate.event pre-commit" in theirs
    capsys.readouterr()
    assert repo_install.teardown() == 0
    out, err = capsys.readouterr()
    assert err == ""
    assert (f"dropped {wt} from the index: {wt} is no longer a git work tree, and git lists "
            f"no other work tree of its git dir {bare}; removed repo's config keys and git "
            f"hooks from {bare} too") in out
    assert git(bare, "config", "--local", "--get-regexp",
               r"^(moxi-repo\.|hook\.moxi-repo-)", check=False).stdout == ""
    assert hook.read_text() == mine and not (bare / "hooks" / "pre-commit.local").exists()
    assert not (bare / "moxi-repo").exists()
    assert api.indexed() == {}


def test_teardown_drops_a_git_dir_left_with_nothing_of_repos_in_it(demo, capsys):
    src = demo.repo("src")
    bare, wt = demo.tmp / "proj.git", demo.tmp / "wt"
    git(demo.tmp, "clone", "-q", "--bare", str(src), str(bare))
    git(bare, "worktree", "add", "-q", str(wt), "-b", "side")
    api.add(wt, ["demo/gate"])
    gitconf.remove_all(gitconf.resolve(wt))
    git(bare, "worktree", "remove", str(wt))
    capsys.readouterr()
    assert repo_install.teardown() == 0
    out = capsys.readouterr().out
    assert out == (f"dropped {wt} from the index: {wt} is no longer a git work tree, and git "
                   f"lists no other work tree of its git dir {bare}\n")


def _two_with_records(demo):
    alpha, beta = demo.repo("alpha-notes"), demo.repo("beta-code")
    for r in (alpha, beta):
        api.add(r, ["demo/lint"])
    demo.act(lint="raise")
    for r in (alpha, beta):
        change(r)
        commit(r)
    return alpha, beta


def test_a_repo_teardown_cannot_read_stops_the_uninstall_before_anything_is_removed(
        demo, capsys):
    alpha, beta = _two_with_records(demo)
    before = ours_in_config(beta)
    config = alpha / ".git" / "config"
    config.write_text(config.read_text(encoding="utf-8") + "[broken\n", encoding="utf-8")
    capsys.readouterr()
    assert repo_install.teardown() == 1
    out, err = capsys.readouterr()
    assert out == ""
    assert err.startswith(f"repo: {alpha}: ") and "bad config line" in err
    assert "so repo can't clean it" in err
    assert err.splitlines()[-1] == ("repo: fix the problem above and run moxi uninstall "
                                    "repo again")
    # beta, which git can read, was left as it was too.
    assert ours_in_config(beta) == before and before
    assert set(api.indexed()) == {gitconf.key_of(r / ".git") for r in (alpha, beta)}
    assert set(_records.ledger()) == {_records.piece_key(r / ".git", "demo/lint")
                                      for r in (alpha, beta)}


def test_a_step_that_fails_once_removing_began_stops_there_and_names_the_repos_cleaned(
        demo, capsys):
    alpha, beta = _two_with_records(demo)
    real = gitconf.remove_all

    def remove_all(repo, state=None):
        if repo.root == beta:
            raise gitconf.GitError("git config could not remove section moxi-repo: locked")
        return real(repo, state)

    demo.mp.setattr(gitconf, "remove_all", remove_all)
    capsys.readouterr()
    assert repo_install.teardown() == 1
    out, err = capsys.readouterr()
    assert out == f"removed repo's git hooks from {alpha}\n"
    assert err.startswith(f"repo: {beta}: git config could not remove section")
    assert "repo stopped there" in err
    last = err.splitlines()[-1]
    assert last.startswith("repo: already cleaned and dropped from the index")
    assert last.endswith(f": {alpha}")
    assert ours_in_config(alpha) == ""
    assert set(api.indexed()) == {gitconf.key_of(beta / ".git")}
    assert set(_records.ledger()) == {_records.piece_key(beta / ".git", "demo/lint")}


def test_an_unreadable_index_fails_the_teardown(demo, capsys):
    api.add(demo.repo(), ["demo/lint"])
    api.index_path().write_text("[1, 2]", encoding="utf-8")
    assert repo_install.teardown() == 1
    assert "repo's index" in capsys.readouterr().err
    assert json.loads(api.index_path().read_text()) == [1, 2]

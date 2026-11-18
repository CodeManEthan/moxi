"""A copy of Moxi's folder (`within`): each plan and each verb of add, turn on and refresh
acts on a repo inside it and refuses one outside it before it takes a lock or writes; and
the page server's reading of that folder from `MOXI_ANCHOR_HOME`."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from moxi_repo import api, gitconf, pagehandlers
from tests.conftest import POSIX_ONLY, git

pytestmark = POSIX_ONLY


def lock_of(repo: Path) -> Path:
    return gitconf.resolve(repo).own_dir / api.HOOKS_LOCK


def written(repo: Path) -> tuple:
    """What an act could write: the repo's config, its hooks folders' listings and the
    index, as bytes or names; and whether its lock file is there."""
    where = gitconf.resolve(repo)
    index = api.index_path()
    return ((where.common_dir / "config").read_bytes(),
            {str(d): sorted(p.name for p in d.iterdir())
             for d in {where.hooks_dir, where.common_dir / "hooks"} if d.is_dir()},
            index.read_bytes() if index.exists() else None,
            lock_of(repo).exists())


def ready(repo: Path) -> None:
    """Pieces registered as the terminal registers them (no `within`), with one off and
    a hand edit, so each act has something to write; then no lock file."""
    api.add(repo, ["demo/gate", "demo/watch"], mode="config")
    api.disable(repo, "demo/watch")
    git(repo, "config", "moxi-repo.piece.demo/gate.fail", "open")
    lock_of(repo).unlink()


ACTS = {
    "add": (lambda p, **kw: api.plan_add(p, ["demo/lint"], **kw),
            lambda p, **kw: api.add(p, ["demo/lint"], **kw)),
    "enable": (lambda p, **kw: api.plan_enable(p, "demo/watch", **kw),
               lambda p, **kw: api.enable(p, "demo/watch", **kw)),
    "refresh": (lambda p, **kw: api.plan_refresh(p, **kw),
                lambda p, **kw: api.refresh(p, **kw)),
}


def refused_everywhere(path: Path, within: str, text: str) -> None:
    """Each plan and each verb refuses `path` as `outside-copy` with `text`, and nothing
    is written, not even the lock file."""
    before = written(path)
    for act, (plan_of, verb) in ACTS.items():
        plan = plan_of(path, within=within)
        assert plan["refusal"] == {"code": "outside-copy", "text": text}, act
        assert plan["writes"] == [] and plan["digest"] is None
        with pytest.raises(api.Refused) as caught:
            verb(path, within=within)
        assert (caught.value.reason, caught.value.text) == ("outside-copy", text), act
        assert written(path) == before, act
    assert not lock_of(path).exists()


def words(within: str, outside: str) -> str:
    return (f"This page runs in a copy of Moxi whose home is {within}. It changes no repo "
            f"outside that folder, and {outside} is outside it.")


@pytest.mark.parametrize("act", sorted(ACTS))
def test_a_repo_inside_the_copys_home_is_planned_and_written(demo, act):
    copy = demo.tmp / "copy"
    repo = demo.repo("copy/alpha-notes")
    ready(repo)
    plan_of, verb = ACTS[act]
    plan = plan_of(repo, within=str(copy))
    assert plan["refusal"] is None and plan["writes"]
    report = verb(repo, within=str(copy), expect=plan["digest"])
    assert not report.already
    assert plan_of(repo, within=str(copy))["writes"] == []


def test_a_repo_outside_the_copys_home_is_refused_and_the_terminal_still_acts(demo):
    copy = demo.tmp / "copy"
    copy.mkdir()
    repo = demo.repo()
    ready(repo)
    refused_everywhere(repo, str(copy), words(str(copy), str(repo)))
    # The terminal's verbs, given no folder, act on it.
    assert api.add(repo, ["demo/lint"]).lines
    assert api.enable(repo, "demo/watch").lines == [f"enabled demo/watch in {repo}"]


def test_a_sibling_whose_name_starts_with_the_copys_home_is_outside_it(demo):
    copy = demo.tmp / "copy"
    copy.mkdir()
    repo = demo.repo("copy-2")
    ready(repo)
    refused_everywhere(repo, str(copy), words(str(copy), str(repo)))


def test_a_worktree_inside_whose_main_work_tree_is_outside_is_refused(demo):
    copy = demo.tmp / "copy"
    copy.mkdir()
    repo = demo.repo()
    ready(repo)
    linked = copy / "wt"
    git(repo, "worktree", "add", "-q", "-b", "side", str(linked))
    refused_everywhere(linked, str(copy), words(str(copy), str(repo)))


def test_a_repo_inside_whose_hooks_dir_is_outside_is_refused(demo):
    copy = demo.tmp / "copy"
    repo = demo.repo("copy/alpha-notes")
    hooks = demo.tmp / "elsewhere-hooks"
    hooks.mkdir()
    git(repo, "config", "core.hooksPath", str(hooks))
    ready(repo)
    refused_everywhere(repo, str(copy), words(
        str(copy), f"the hooks folder of {repo}, {hooks},"))


def test_a_link_into_the_copys_home_is_read_by_where_it_leads(demo):
    real = demo.tmp / "copy"
    real.mkdir()
    link = demo.tmp / "copy-link"
    link.symlink_to(real)
    inside = demo.repo("copy/alpha-notes")
    ready(inside)
    assert api.plan_add(inside, ["demo/lint"], within=str(link))["refusal"] is None
    outside = demo.repo("beta-notes")
    via = real / "beta"
    via.symlink_to(outside)
    ready(outside)
    refused_everywhere(via, str(link), words(str(link), str(outside)))


def test_a_copys_home_that_isnt_a_full_path_refuses_every_plan_and_act(demo):
    repo = demo.repo("copy/alpha-notes")
    ready(repo)
    demo.mp.chdir(demo.tmp)
    refused_everywhere(repo, "copy", "This page runs in a copy of Moxi whose home is copy. "
                       "It changes no repo, since that is not a full path.")


def test_a_refresh_given_a_copys_home_names_its_repo(demo):
    with pytest.raises(api.Usage):
        api.refresh(within=str(demo.tmp))


# -- the page server's reading --------------------------------------------------------------

def test_the_page_server_keeps_to_the_home_its_anchor_names(tmp_path):
    real = tmp_path / "copy"
    real.mkdir()
    link = tmp_path / "copy-link"
    link.symlink_to(real)
    assert pagehandlers.within({"MOXI_ANCHOR_HOME": str(link)}) == str(real)
    assert pagehandlers.within({"MOXI_ANCHOR_HOME": "copy"}) == "copy"
    assert pagehandlers.within({"MOXI_ANCHOR_HOME": ""}) is None
    assert pagehandlers.within({"HOME": str(tmp_path)}) is None


def test_the_page_server_reads_its_own_env_without_one(tmp_path, monkeypatch):
    monkeypatch.setenv("MOXI_ANCHOR_HOME", str(tmp_path))
    assert pagehandlers.within() == os.path.realpath(tmp_path)
    monkeypatch.delenv("MOXI_ANCHOR_HOME")
    assert pagehandlers.within() is None

"""The Git hooks page's acts pressed in a browser, end to end, through a real shell that
starts repo's real page server: add a piece, add one after the repo changed under the
confirm, turn one on, refresh, add a repo, the copy's refusal of a repo outside its home,
and `Turn off`, which runs nothing. Each test has a shell of its own, since acts change
its repos, and every repo it acts on is made inside the shell's anchor folder.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import repo_page_kit as kit
from playwright.sync_api import expect

pytestmark = [pytest.mark.browser, pytest.mark.timeout(240),
              pytest.mark.skipif(not sys.platform.startswith("linux"),
                                 reason="repo's page is checked in a browser on Linux; the "
                                        "Windows and macOS runs are owed")]

ADD = ("repo", "git-hooks", "add")


@pytest.fixture(scope="module")
def ui_shell_options(tmp_path_factory, hook_env) -> dict:
    return kit.shell_options(tmp_path_factory, hook_env)


@pytest.fixture
def world(ui_shells, ui_shell_options, ui_contexts, ui_browser):
    """A shell of the test's own with a typed terminal, atlas-api holding commit-rules, and
    a page signed in on it."""
    shell = kit.own_shell(ui_shells, ui_shell_options)
    kit.typed(shell)
    atlas = kit.make_repo(shell, "code/atlas-api")
    kit.moxi(shell, *ADD, str(atlas), "repo/commit-rules")
    page = ui_contexts(shell, **kit.clipboard_args(ui_browser)).pages[0]
    yield SimpleNamespace(shell=shell, page=page, atlas=atlas)
    shell.stop()


def config(world, repo: Path, key: str) -> str | None:
    done = kit.git(world.shell, repo, "config", "--get", key, check=False)
    return done.stdout.strip() or None


def enabled(world, repo: Path, piece: str) -> str | None:
    return config(world, repo, f"moxi-repo.piece.{piece}.enabled")


def test_a_piece_added_from_the_page_is_in_the_repos_git_config(world):
    panel = kit.open_page(world.page, "repo/~!2Fcode!2Fatlas-api/add")
    listed = panel.locator("[data-part=add-piece]")
    expect(listed.locator("[data-line=question]")).to_have_text("Add a piece to ~/code/atlas-api")
    listed.locator('[data-piece="demo/gate"] [data-action=pick]').click()
    confirm = panel.locator("[data-part=confirm][data-act=add]")
    expect(confirm.locator("[data-line=question]")).to_have_text("Add gate to ~/code/atlas-api?")
    expect(confirm.locator("[data-line=writes]").first).to_have_text(
        "Writesinto this repo's git config · gate, new · the hook entries for before a commit")
    expect(confirm.locator("[data-line=then]")).to_have_text("ThenA closed gate.")
    assert enabled(world, world.atlas, "demo/gate") is None
    confirm.locator("[data-action=go]").click()
    expect(confirm.locator("[data-line=said]")).to_have_text(
        f"added demo/gate (pre-commit, closed) in {world.atlas}")
    assert enabled(world, world.atlas, "demo/gate") == "true"
    expect(kit.row(panel, world.atlas)).to_have_attribute("where", "gate · commit-rules")


def test_an_add_whose_repo_changed_under_its_confirm_shows_the_new_plan_first(world):
    panel = kit.open_page(world.page, "repo/~!2Fcode!2Fatlas-api/add")
    listed = panel.locator("[data-part=add-piece]")
    listed.locator('[data-piece="demo/zz-early"] [data-action=pick]').click()
    listed.locator('[data-piece="demo/first"] [data-action=pick]').click()
    confirm = panel.locator("[data-part=confirm][data-act=add]")
    expect(confirm.locator("[data-line=writes]").first).to_contain_text("zz-early, new · first, new")
    kit.moxi(world.shell, *ADD, str(world.atlas), "demo/first")
    before = kit.git(world.shell, world.atlas, "config", "--list", "--local").stdout
    confirm.locator("[data-action=go]").click()
    expect(confirm.locator("[data-line=answer]")).to_have_text("This changed since you looked:")
    expect(confirm.locator("[data-line=writes]").first).to_contain_text("zz-early, new")
    expect(confirm.locator("[data-line=writes]").first).not_to_contain_text("first, new")
    assert kit.git(world.shell, world.atlas, "config", "--list", "--local").stdout == before
    confirm.locator("[data-action=go]").click()
    expect(confirm.locator("[data-line=said]").first).to_have_text(
        f"added demo/zz-early (pre-commit, open) in {world.atlas}")
    assert enabled(world, world.atlas, "demo/zz-early") == "true"


def test_a_piece_turned_on_from_the_page_runs_again(world):
    kestrel = kit.make_repo(world.shell, "code/kestrel-web")
    kit.moxi(world.shell, *ADD, str(kestrel), "demo/gate")
    kit.moxi(world.shell, "repo", "git-hooks", "disable", str(kestrel), "demo/gate")
    panel = kit.open_page(world.page)
    opened = kit.opened(panel, kestrel)
    opened.locator('[data-piece="demo/gate"] [data-action=turn-on]').click()
    confirm = opened.locator("[data-part=confirm][data-act=enable]")
    expect(confirm.locator("[data-line=question]")).to_have_text("Turn gate on in ~/code/kestrel-web?")
    expect(confirm.locator("[data-line=writes]").first).to_contain_text("gate turned on")
    confirm.locator("[data-action=go]").click()
    expect(confirm.locator("[data-line=said]")).to_have_text(f"enabled demo/gate in {kestrel}")
    assert enabled(world, kestrel, "demo/gate") == "true"
    expect(kit.row(panel, kestrel)).to_have_attribute("where", "gate")


def test_refresh_puts_back_an_entry_taken_out_by_hand(world):
    key = "hook.moxi-repo-commit-msg.command"
    entry = config(world, world.atlas, key)
    kit.git(world.shell, world.atlas, "config", "--unset-all", key)
    panel = kit.open_page(world.page)
    item = panel.locator("[data-group=hooks] .prob").filter(has_text="~/code/atlas-api")
    item.locator("[data-action=refresh]").click()
    confirm = panel.locator("[data-part=confirm][data-act=refresh]")
    expect(confirm.locator("[data-line=question]")).to_have_text("Refresh ~/code/atlas-api?")
    expect(confirm.locator("[data-line=writes]").first).to_contain_text(
        "the hook entries for on the message")
    confirm.locator("[data-action=go]").click()
    expect(confirm.locator("[data-line=said]")).to_have_text(f"refreshed {world.atlas}")
    assert config(world, world.atlas, key) == entry
    expect(panel.locator("[data-group=hooks] .prob")).to_have_count(0)


def test_a_repo_added_from_the_page_joins_the_list_with_its_pieces(world):
    orbit = kit.make_repo(world.shell, "code/orbit-cli")
    panel = kit.open_page(world.page, "add")
    adder = panel.locator("[data-part=add-repo]")
    adder.locator("[data-line=folder]").fill("~/code/orbit-cli")
    expect(adder.locator("[data-line=folder-said]")).to_have_text(f"a git repo · git {kit.git_version()}")
    expect(adder.locator("[data-action=go]")).to_have_text("Add to the list")
    adder.locator('[data-piece="repo/commit-rules"] [data-action=switch]').click()
    expect(adder.locator("[data-action=go]")).to_have_text("Add 1 piece")
    adder.locator("[data-action=go]").click()
    expect(adder.locator("[data-line=said]")).to_have_text(
        f"added repo/commit-rules (commit-msg, closed) in {orbit}")
    assert enabled(world, orbit, "repo/commit-rules") == "true"
    expect(kit.row(panel, orbit)).to_have_attribute("where", "commit-rules")


def test_a_repo_outside_the_copys_home_is_refused_and_left_alone(world, tmp_path):
    outside = tmp_path / "elsewhere"
    kit.git(world.shell, tmp_path, "init", "-q", outside.name)
    before = (outside / ".git" / "config").read_bytes()
    panel = kit.open_page(world.page, "add")
    adder = panel.locator("[data-part=add-repo]")
    adder.locator('[data-piece="repo/commit-rules"] [data-action=switch]').click()
    adder.locator("[data-line=folder]").fill(str(outside))
    home = world.shell.env["MOXI_ANCHOR_HOME"]
    expect(adder.locator("[data-line=folder-said]")).to_have_text(
        f"This page runs in a copy of Moxi whose home is {home}. It changes no repo outside "
        f"that folder, and {outside} is outside it.")
    expect(adder.locator("[data-action=go]")).to_have_count(0)
    assert (outside / ".git" / "config").read_bytes() == before
    assert not (outside / ".git" / "moxi-repo").exists()


def test_turn_off_gives_the_commands_and_runs_nothing(world):
    sent = []
    world.page.on("request", lambda request: sent.append(request.url)
                  if request.method != "GET" else None)
    panel = kit.open_page(world.page)
    opened = kit.opened(panel, world.atlas)
    piece = opened.locator('[data-piece="repo/commit-rules"]')
    piece.locator("[data-action=turn-off]").click()
    block = piece.locator("[data-part=turn-off]")
    expect(block.locator(".q")).to_have_text("Turn commit-rules off in ~/code/atlas-api?")
    expect(block).to_contain_text("Stays: its place and settings · Turn on brings it back as it was")
    toggle = f"moxi repo git-hooks disable {world.atlas} repo/commit-rules"
    remove = f"moxi repo git-hooks remove {world.atlas} repo/commit-rules"
    commands = block.locator("[data-line=command]")
    expect(commands.nth(0).locator("code")).to_have_text(toggle)
    expect(commands.nth(1).locator("code")).to_have_text(remove)
    kit.copied(world.page, commands.nth(0).locator("[data-action=copy]"), toggle)
    expect(block.locator("[data-line=note]")).to_have_text(
        "Taking a check away is done at your terminal. This page adds checks and turns them on.")
    assert enabled(world, world.atlas, "repo/commit-rules") == "true"
    assert sent == []

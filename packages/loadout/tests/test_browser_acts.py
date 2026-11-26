"""loadout's acts from its page in a browser, through a real shell that starts loadout's
real page server, whose acts run as real page jobs, each test on a fresh seeded home
(`page_kit`) with projects and repo installed:

- Replace in each case: the agent file edited by hand (kept, then written; an edit made
  after the confirm opened is kept the same way), something in the way (moved aside),
  and an entry edited in a harness's file (the whole file kept). Each copy lies under
  the home's `displaced/`, byte-equal to what was there, and is named under the act with
  the command that puts it back, which, run as given, does.
- Add from Not listed, and Add by name with nothing unlisted; Turn off and on for one
  harness; Turn on for every harness.
- Every address the page writes (an item in a section named by its path, Replace's
  confirm, Add, the global file, the filtered view) loads by navigation.
- The global mode, and Turn off through its confirm, where Cancel sends nothing.
- Every control off while a job runs and after a reload; an interrupted job; a job whose
  read is refused.
- At 1280 and 400 px, in light and dark: nothing past the edge, no heading, no colour but
  the text tokens (no accent, no warning colour), no word of an item's line broken, and at
  1280 the grid's drawn pitch and columns.

After each act, every harness file loadout names lies under the shell's home.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path
from types import SimpleNamespace

import page_kit as kit
import pytest
from playwright.sync_api import expect

pytestmark = [pytest.mark.browser, pytest.mark.timeout(240, method="thread"),
              pytest.mark.skipif(not sys.platform.startswith("linux"),
                                 reason="loadout's page is checked in a browser on Linux; the "
                                        "Windows and macOS runs are owed")]

JOB = "**/m/loadout/api/job?*"


@pytest.fixture(scope="module")
def ui_shell_options(tmp_path_factory) -> dict:
    return kit.shell_options(tmp_path_factory)


@pytest.fixture
def world(ui_shells, ui_shell_options, ui_contexts):
    """A shell of the test's own on the seeded home, and a page signed in on it."""
    shell = ui_shells(**ui_shell_options)
    loadout_dir = kit.seed(shell, projects=True)
    page = ui_contexts(shell).pages[0]
    yield SimpleNamespace(shell=shell, page=page, dir=loadout_dir,
                          home=Path(shell.env["HOME"]))
    shell.stop()


def written_under_home(shell) -> None:
    """Every harness file loadout names, its placements and the global files, lies under
    the shell's home."""
    home = Path(shell.env["HOME"]).resolve()
    paths = [Path(row["target"]) for row in kit.status(shell)["rows"] if row["target"]]
    shown = json.loads(kit.moxi(shell, "loadout", "global", "show", "--json").stdout)
    paths += [Path(f["file"]) for f in shown["files"]]
    assert paths
    for path in paths:
        assert path.is_relative_to(home) or path.resolve().is_relative_to(home), path


def run_back(shell, back: str) -> None:
    """The way back, run as the page shows it, in the user's terminal."""
    subprocess.run(["sh", "-c", back], env=shell.env, cwd=shell.env["HOME"], check=True,
                   capture_output=True, timeout=60)


def problem(panel, sentence: str):
    return panel.locator('[data-group="placed"] .prob').filter(
        has=panel.page.locator('[data-line="sentence"]', has_text=sentence))


def test_replace_keeps_the_edited_agent_file_with_an_edit_made_after_the_confirm_opened(world):
    panel = kit.open_page(world.page)
    target = kit.target_of(world.shell, "agent", "code-reviewer", "codex")
    changed = problem(panel, "The agent code-reviewer was changed by hand")
    changed.locator('[data-action="replace"]').click()
    confirm = changed.locator('[data-part="confirm"]')
    expect(confirm.locator('[data-line="question"]')).to_have_text(
        "Put loadout's version of code-reviewer back for Codex?")
    expect(confirm.locator('[data-line="what"]')).to_contain_text(
        "the edited file as it is now in")
    expect(confirm.locator('[data-line="diff"]')).to_contain_text("-# my own change")
    # He edits the file again while the confirm is open: that edit is kept too.
    with target.open("a", encoding="utf-8") as fh:
        fh.write("# a later change\n")
    edited = target.read_bytes()
    confirm.locator('[data-action="confirm-replace"]').click()
    kit.job_line(panel, "moxi loadout install --replace=agent:code-reviewer@codex --accept-diff "
                        "--user: done")
    [copy] = kit.displaced(world.home, target.name)
    assert copy.read_bytes() == edited
    assert target.read_bytes() != edited
    kept = panel.locator('[data-part="kept"]')
    expect(kept.locator('[data-line="kept"]')).to_contain_text("as it was is kept at")
    expect(kept.locator('[data-line="kept"]')).to_contain_text(copy.name)
    back = kept.locator('[data-line="back"] code')
    expect(back).to_contain_text("cp ")
    expect(kept.locator('[data-line="back"]')).to_contain_text(
        "puts it back as it was: loadout then reads it changed and leaves it")
    expect(kit.cell(panel, "agent", "code-reviewer", "codex")).to_have_text("placed")
    written_under_home(world.shell)
    run_back(world.shell, back.inner_text())
    assert target.read_bytes() == edited
    expect(kit.cell(panel, "agent", "code-reviewer", "codex")).to_have_text("changed",
                                                                             timeout=70_000)


def test_replace_moves_what_is_in_the_way_and_says_to_turn_it_off_first(world):
    target = kit.target_of(world.shell, "skill", "release-checklist", "claude")
    target.mkdir(parents=True)
    own = b"the user's own release-checklist\n"
    (target / "SKILL.md").write_bytes(own)
    # Without the hand edit, the run over Your items ends done.
    kit.take_back_edit(world.shell)
    panel = kit.open_page(world.page)
    blocked = problem(panel, "Something else is where the skill release-checklist goes")
    expect(blocked.locator('[data-line="word"]')).to_have_text("in the way")
    blocked.locator('[data-action="replace"]').click()
    confirm = blocked.locator('[data-part="confirm"]')
    expect(confirm.locator('[data-line="question"]')).to_have_text(
        "Put the folder's version of release-checklist back for Claude Code?")
    expect(confirm.locator('[data-line="what"]')).to_contain_text("what is there to")
    confirm.locator('[data-action="confirm-replace"]').click()
    kit.job_line(panel, "moxi loadout install --replace=skill:release-checklist@claude "
                        "--accept-diff --user: done")
    [copy] = kit.displaced(world.home, "release-checklist")
    assert (copy / "SKILL.md").read_bytes() == own
    assert target.is_symlink()
    kept = panel.locator('[data-part="kept"]')
    expect(kept.locator('[data-line="back"]')).to_contain_text(
        "This turns release-checklist off for Claude Code first, then")
    back = kept.locator('[data-line="back"] code')
    expect(back).to_contain_text("moxi loadout disable skill:release-checklist@claude --user")
    expect(back).to_contain_text("mv ")
    expect(kit.cell(panel, "skill", "release-checklist", "claude")).to_have_text("placed")
    written_under_home(world.shell)


def test_replace_of_an_edited_entry_keeps_the_whole_harness_file(world):
    # Its command stays, so the group is still loadout's; a timeout is added by hand.
    settings = kit.target_of(world.shell, "hook", "guard-rm", "claude")
    doc = json.loads(settings.read_text(encoding="utf-8"))
    [group] = doc["hooks"]["PreToolUse"]
    group["hooks"][0]["timeout"] = 5
    settings.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    edited = settings.read_bytes()
    panel = kit.open_page(world.page)
    changed = problem(panel, "The hook guard-rm was changed by hand")
    changed.locator('[data-action="replace"]').click()
    confirm = changed.locator('[data-part="confirm"]')
    expect(confirm.locator('[data-line="what"]')).to_contain_text(
        f"a copy of ~/{settings.relative_to(world.home).as_posix()} as it is now in")
    confirm.locator('[data-action="confirm-replace"]').click()
    # The run over Your items leaves the edited agent file, which still needs him; the
    # Replace itself did its work.
    kit.job_line(panel, "moxi loadout install --replace=hook:guard-rm@claude --accept-diff --user: "
                        "done, but "
                        "the agent code-reviewer still needs you")
    [copy] = kit.displaced(world.home, settings.name)
    assert copy.read_bytes() == edited
    assert "timeout" not in json.loads(settings.read_text(encoding="utf-8"))["hooks"]["PreToolUse"][0]["hooks"][0]
    expect(panel.locator('[data-part="kept"] [data-line="back"]')).to_contain_text(
        "puts it back as it was: the whole file, so later changes to it go too")
    written_under_home(world.shell)


def test_add_from_not_listed_lists_the_skill_and_places_it(world):
    panel = kit.open_page(world.page)
    unlisted = panel.locator('[data-group="unlisted"]')
    unlisted.locator('[data-action="add"]').click()
    add = panel.locator('[data-part="add"]')
    expect(add.get_by_label("Add to")).to_have_value("")
    add.locator('[data-action="confirm-add"]').click()
    kit.job_line(panel, "moxi loadout add skill bump-version: done")
    for harness in ("claude", "codex", "opencode"):
        expect(kit.cell(panel, "skill", "bump-version", harness)).to_have_text("placed")
    expect(unlisted.locator(".ir")).to_have_count(0)
    assert '"bump-version"' in (world.dir / "loadout.toml").read_text(encoding="utf-8")
    written_under_home(world.shell)


def test_add_by_name_is_there_with_nothing_unlisted_and_lists_an_item_in_another_section(world):
    shutil.rmtree(world.dir / "skills" / "bump-version")
    panel = kit.open_page(world.page)
    unlisted = panel.locator('[data-group="unlisted"]')
    expect(unlisted.locator('[data-action="by-name"]')).to_have_text(
        "Add something by its name instead")
    expect(unlisted.locator(".ir")).to_have_count(0)
    unlisted.locator('[data-action="by-name"]').click()
    add = panel.locator('[data-part="add"]')
    add.get_by_label("Kind").select_option("skill")
    add.get_by_label("Name").fill("tidy-notes")
    add.get_by_label("Add to").select_option("atlas-api")
    add.locator('[data-action="confirm-add"]').click()
    kit.job_line(panel, "moxi loadout add skill tidy-notes --project=atlas-api: done")
    for harness in ("claude", "codex", "opencode"):
        expect(kit.cell(panel, "skill", "tidy-notes", harness, "atlas-api")).to_have_text("placed")
    # Add closed; its line is there again.
    expect(unlisted.locator('[data-action="by-name"]')).to_be_visible()
    toml = tomllib.loads((world.dir / "loadout.toml").read_text(encoding="utf-8"))
    assert toml["project"]["atlas-api"]["skills"] == ["release-checklist", "tidy-notes"], toml
    written_under_home(world.shell)


def reloaded(page, path: str):
    """loadout's page loaded at `path` by navigation, as a reload, a pasted address or a
    bookmark loads it."""
    assert "%" not in path, path
    answer = page.goto(path)
    assert answer is not None and answer.status == 200, (path, answer and answer.status)
    panel = page.locator("#moxi-panel > moxi-loadout-page")
    expect(panel.locator('[data-part="groups"]')).to_be_visible()
    return panel


def test_every_address_the_page_writes_loads_by_navigation(world):
    # A section named by its path, beside the key section.
    (world.home / "code" / "kestrel").mkdir(parents=True)
    with (world.dir / "loadout.toml").open("a", encoding="utf-8") as fh:
        fh.write('\n[project."~/code/kestrel"]\nskills = ["review-diff"]\n')
    page = world.page
    path = "~/code/kestrel"
    panel = kit.open_page(page)

    def written() -> str:
        return page.evaluate("location.pathname")

    # An item in the path section.
    kit.item_row(panel, "skill", "review-diff", path).locator('[data-action="open"]').click()
    expect(panel.locator('[data-part="opened"]')).to_be_visible()
    panel = reloaded(page, written())
    expect(panel.locator(f"[data-part='opened'][data-item='{kit.item_key('skill', 'review-diff', path)}']")
           ).to_be_visible()
    # Replace's confirm.
    problem(panel, "The agent code-reviewer was changed by hand").locator(
        '[data-action="replace"]').click()
    expect(panel.locator('[data-part="confirm"]')).to_be_visible()
    assert written() == "/m/loadout/replace/user/agent:code-reviewer@codex"
    panel = reloaded(page, written())
    expect(panel.locator('[data-part="confirm"] [data-line="question"]')).to_have_text(
        "Put loadout's version of code-reviewer back for Codex?")
    # Add on a file, and Add by name.
    panel.locator('[data-group="unlisted"] [data-action="add"]').click()
    expect(panel.locator('[data-part="add"] [data-line="from"]')).to_be_visible()
    panel = reloaded(page, written())
    expect(panel.locator('[data-part="add"] [data-line="from"]')).to_have_text(
        "~/my-loadout/skills/bump-version")
    panel.locator('[data-action="by-name"]').click()
    expect(panel.locator('[data-part="add"]').get_by_label("Name")).to_be_visible()
    panel = reloaded(page, written())
    expect(panel.locator('[data-part="add"]').get_by_label("Name")).to_be_visible()
    # The global file, an item of Your items, and the filtered view.
    for route, shown in (("global", '[data-part="opened"][data-item="global"]'),
                         ("item/user/hook/guard-rm",
                          f"[data-part='opened'][data-item='{kit.item_key('hook', 'guard-rm')}']"),
                         ("project/atlas-api", '[data-part="filter"]')):
        panel = reloaded(page, f"/m/loadout/{route}")
        expect(panel.locator(shown)).to_be_visible()
        assert written() == f"/m/loadout/{route}"


def test_turn_off_and_on_for_one_harness(world):
    target = kit.target_of(world.shell, "skill", "tidy-notes", "codex")
    panel = kit.open_page(world.page)
    kit.item_row(panel, "skill", "tidy-notes").locator('[data-action="open"]').click()
    opened = panel.locator('[data-part="opened"]')
    codex = opened.locator('.r[data-harness="codex"]')
    codex.locator('[data-action="turn-off"]').click()
    kit.job_line(opened, "moxi loadout disable skill:tidy-notes@codex --user: done")
    expect(kit.cell(panel, "skill", "tidy-notes", "codex")).to_have_text("off")
    assert not target.exists()
    assert '"skill:tidy-notes@codex"' in (world.dir / "loadout.toml").read_text(encoding="utf-8")
    codex.locator('[data-action="turn-on"]').click()
    kit.job_line(opened, "moxi loadout enable skill:tidy-notes@codex --user: done")
    expect(kit.cell(panel, "skill", "tidy-notes", "codex")).to_have_text("placed")
    assert target.exists()
    written_under_home(world.shell)


def test_turn_on_for_every_harness(world):
    kit.moxi(world.shell, "loadout", "disable", "skill:tidy-notes@*", "--user")
    panel = kit.open_page(world.page, "item/user/skill/tidy-notes")
    opened = panel.locator('[data-part="opened"]')
    expect(opened.locator('[data-action="turn-off"], [data-action="turn-on"]')).to_have_count(0)
    expect(opened.locator('[data-line="enable-all"]')).to_have_text(
        "Claude Code, Codex and OpenCode come back")
    opened.locator('[data-action="enable-all"]').click()
    kit.job_line(opened, "moxi loadout enable 'skill:tidy-notes@*' --user: done")
    for harness in ("claude", "codex", "opencode"):
        expect(kit.cell(panel, "skill", "tidy-notes", harness)).to_have_text("placed")


def test_the_global_mode_and_turn_off_through_its_confirm(world):
    claude_md = world.home / ".claude" / "CLAUDE.md"
    assert "global.md" in claude_md.read_text(encoding="utf-8")
    posts: list = []
    world.page.on("request", lambda request: posts.append(request.url)
                  if request.method == "POST" and request.url.endswith("/api/global-remove")
                  else None)
    panel = kit.open_page(world.page, "global")
    opened = panel.locator('[data-part="opened"][data-item="global"]')
    opened.locator('button[data-mode="if-absent"]').click()
    kit.job_line(opened, "moxi loadout global mode if-absent: done")
    expect(opened.locator('[data-mode="if-absent"]')).to_have_attribute("aria-current", "true")
    opened.locator('button[data-mode="managed"]').click()
    kit.job_line(opened, "moxi loadout global mode managed: done")
    expect(opened.locator('[data-mode="managed"]')).to_have_attribute("aria-current", "true")

    opened.locator('[data-action="global-off"]').click()
    confirm = opened.locator('[data-part="confirm"]')
    expect(confirm.locator('[data-line="question"]')).to_have_text(
        "Turn off takes loadout's part out of each file and leaves your text")
    confirm.locator('[data-action="cancel"]').click()
    expect(confirm).to_have_count(0)
    assert posts == []
    opened.locator('[data-action="global-off"]').click()
    opened.locator('[data-action="confirm-off"]').click()
    kit.job_line(opened, "moxi loadout global remove: done")
    expect(opened.locator('[data-mode="off"]')).to_have_attribute("aria-current", "true")
    assert len(posts) == 1
    assert "global.md" not in claude_md.read_text(encoding="utf-8") if claude_md.exists() else True
    written_under_home(world.shell)


#: Every act's control on the page.
ACTS = "button[data-action], button[data-mode]"
#: Controls that act on nothing, which stay on while a job runs: Replace and Add open
#: their confirm, whose own button is off.
FREE = {"open", "close", "show", "show-all", "copy", "diff", "cancel", "project", "settings",
        "by-name", "add", "replace"}


def acting(panel) -> list:
    return [control for control in panel.locator(ACTS).all()
            if (control.get_attribute("data-action") or "") not in FREE]


def test_every_control_is_off_while_a_job_runs_and_after_a_reload(world):
    page = world.page
    panel = kit.open_page(page, "item/user/skill/tidy-notes")
    held = {"job": "held", "argv": ["install"], "state": "running", "exit": None,
            "outcome": None, "lines": []}
    page.route(JOB, lambda route: route.fulfill(json=held))
    panel.locator('[data-group="placed"] [data-action="install"]').first.click()
    expect(panel.locator('[data-part="job"] [data-line="job"]').first).to_contain_text(": running")
    controls = acting(panel)
    assert len(controls) >= 3
    for control in controls:
        expect(control).to_be_disabled()
    page.reload()
    panel = kit.open_page(page, "item/user/skill/tidy-notes")
    expect(panel.locator('[data-part="job"] [data-line="job"]').first).to_contain_text(": running")
    for control in acting(panel):
        expect(control).to_be_disabled()
    page.unroute(JOB)
    expect(panel.locator('[data-part="job"] [data-line="job"]').first).not_to_contain_text(
        ": running", timeout=60_000)
    for control in acting(panel):
        expect(control).to_be_enabled()


def test_an_interrupted_job_and_one_whose_read_is_refused_say_so(world):
    page = world.page
    panel = kit.open_page(page)
    install = panel.locator('[data-group="placed"] [data-action="install"]').first
    cut = {"job": "cut", "argv": ["install", "--user"], "state": "interrupted", "exit": None,
           "outcome": None, "lines": ["the job stopped before it ended; run it again"]}
    page.route(JOB, lambda route: route.fulfill(json=cut))
    install.click()
    kit.job_line(panel, "moxi loadout install --user: failed")
    panel.locator('[data-part="job"] [data-action="show"]').click()
    expect(panel.locator('[data-part="show"] [data-line="show-line"]')).to_have_text(
        "the job stopped before it ended; run it again")
    expect(install).to_be_enabled()
    page.unroute(JOB)

    line = "there is no page job held; a job's record is kept for a day"
    page.route(JOB, lambda route: route.fulfill(status=422, json={"refused": line}))
    install.click()
    kit.job_line(panel, f"moxi loadout install --user: {line}")
    expect(install).to_be_enabled()
    page.unroute(JOB)


def test_both_widths_in_both_themes_draw_only_text_colours_and_no_heading(world, ui_width_check):
    page = world.page
    panel = kit.open_page(page, "item/user/agent/code-reviewer")
    expect(panel.locator('[data-part="opened"]')).to_be_visible()
    expect(panel.get_by_role("heading")).to_have_count(0)
    texts = set()
    for scheme in ("light", "dark"):
        page.emulate_media(color_scheme=scheme)
        seen = page.evaluate(f"({kit.REACHED})()")
        texts.add(seen["text"])
        refused = {token: where for token, where in seen["reached"].items()
                   if token not in kit.TEXT_TOKENS}
        assert not refused, f"{scheme}: colour from {refused}"
        assert {"text", "text-dim"} <= set(seen["reached"]), (scheme, seen["reached"])
        for width in (1280, 400):
            page.set_viewport_size({"width": width, "height": 900})
            assert page.evaluate(kit.PAST_THE_EDGE) == {"past": [], "scroll": 0}, (scheme, width)
            grid = page.evaluate(f"({kit.GRID})()")
            # No word of a name or kind breaks, and each harness head reads whole.
            assert grid["broken"] == [] and grid["cut"] == [], (scheme, width, grid)
            if width == 1280:
                # As drawn: a line every 34 px, three 94 px columns and Open's 44 px.
                assert grid["pitch"] == 34 and grid["columns"] == 3 * 94 + 44, grid
    assert len(texts) == 2, f"the text colour is the same in both themes: {texts}"
    ui_width_check(page, module="loadout")

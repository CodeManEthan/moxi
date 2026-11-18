"""The questions the Git hooks page is there to answer, each answered from the page the
way a person would, in a browser, through a real shell that starts repo's real page
server, on five repos made inside the shell's anchor folder (`repo_page_kit.seed`):

1. Does anything need me? The items first, each with its fix.
2. What runs in a repo's commits and pushes? An opened repo's events.
3. Where is a piece on, and where isn't it? `Pieces`' where, and each row's.
4. What does commit-rules check here? `Rules`, with no Commit-Check line, and no object
   written to the repo by reading it.
"""

from __future__ import annotations

import re
import sys

import pytest
import repo_page_kit as kit
from playwright.sync_api import expect

pytestmark = [pytest.mark.browser, pytest.mark.timeout(240),
              pytest.mark.skipif(not sys.platform.startswith("linux"),
                                 reason="repo's page is checked in a browser on Linux; the "
                                        "Windows and macOS runs are owed")]


@pytest.fixture(scope="module")
def ui_shell_options(tmp_path_factory, hook_env) -> dict:
    return kit.shell_options(tmp_path_factory, hook_env)


@pytest.fixture(scope="module")
def cast(ui_shell, hook_env, tmp_path_factory) -> dict:
    return kit.seed(ui_shell, hook_env, kit.old_site(tmp_path_factory))


@pytest.fixture
def page(cast, ui_browser, ui_contexts):
    return ui_contexts(**kit.clipboard_args(ui_browser)).pages[0]


def rows(panel):
    return panel.locator("[data-group=repos] moxi-ui-row")


def test_what_needs_him_comes_first_each_with_its_fix(cast, page):
    panel = kit.open_page(page)
    lumen = cast["lumen-docs"]
    expect(panel.locator("[data-line=verdict]")).to_have_text("2 things need you")
    expect(panel.locator("[data-group=hooks] .prob")).to_have_count(2)

    orphan = kit.item(panel, "~/code/lumen-docs refuses every commit: prose-check has no module")
    expect(orphan).to_have_attribute("data-weight", "need")
    expect(orphan.locator("[data-line=dim]")).to_have_text("no installed module declares it")
    fix = orphan.locator("[data-line=fix]")
    expect(fix.locator(".lbl")).to_have_text("Take the piece out")
    command = f"moxi repo git-hooks remove {lumen} {kit.OLD_PIECE}"
    expect(fix.locator("code")).to_have_text(command)
    kit.copied(page, fix.locator("[data-action=copy]"), command)

    broke = kit.item(panel, "~/code/lumen-docs: lint broke")
    expect(broke).to_have_attribute("data-weight", "need")
    expect(broke.locator("[data-line=time]")).to_have_text(re.compile(r"^\d{1,2}:\d{2}"))
    expect(broke.locator("[data-line=dim]")).to_have_text(
        "pre-commit git hook: demo/lint broke (ValueError: lint fell over)")
    expect(broke.locator("[data-line=fix] code")).to_contain_text(
        f"moxi repo git-hooks disable {lumen} demo/lint")
    expect(broke.locator("[data-line=record]")).to_have_count(0)
    broke.locator("[data-action=show-record]").click()
    expect(broke.locator("[data-line=record]")).to_contain_text("ValueError: lint fell over")

    # The repo they're in comes first among the rows, its fact heavy, the break its news.
    first = rows(panel).first
    expect(first).to_have_attribute("data-key", str(lumen))
    expect(first).to_have_attribute("name", "~/code/lumen-docs")
    expect(first).to_have_attribute("fact", "a piece broke")
    expect(first).to_have_attribute("fact-heavy", "")
    expect(first).to_have_attribute("lead", re.compile(r"^lint broke at \d{1,2}:\d{2}"))
    for other in ("atlas-api", "harbor-infra", "kestrel-web", "my-loadout"):
        expect(kit.row(panel, cast[other])).not_to_have_attribute("fact", re.compile("."))


def test_an_opened_repo_says_what_runs_in_its_commits_and_pushes(cast, page):
    panel = kit.open_page(page)
    atlas = cast["atlas-api"]
    expect(kit.row(panel, atlas)).to_have_attribute(
        "where", "gate · commit-rules · commit-proof · push-check")
    opened = kit.opened(panel, atlas)
    page.wait_for_url("**/m/repo/repo/~!2Fcode!2Fatlas-api")
    events = opened.locator("[data-event]")
    expect(events).to_have_count(3)
    for event, label, lines in (
            ("pre-commit", "Before a commit", ["gate · A closed gate."]),
            ("commit-msg", "On the message",
             ["commit-rules · Check a commit message's trailer shape, and refuse tags in its "
              "subject and body.",
              "commit-proof · Refuse a commit whose message lacks the Commit-Check line moxi "
              "repo rules prints for the staged tree."]),
            ("pre-push", "Before a push", ["push-check · Checks a push."])):
        one = opened.locator(f"[data-event={event}]")
        expect(one.locator(".k")).to_have_text(label)
        pieces = one.locator("[data-piece]")
        expect(pieces).to_have_count(len(lines))
        for n, line in enumerate(lines):
            expect(pieces.nth(n).locator(".line").first).to_contain_text(line)
            expect(pieces.nth(n).locator("[data-action=turn-off]")).to_have_text("Turn off")
    expect(opened.locator("[data-line=own]")).to_have_count(0)
    expect(opened.locator("[data-line=git]")).to_have_text(re.compile(
        rf"^\d+\.\d+(\.\d+)? · hooks run from the repo's own git config · {re.escape(str(atlas))}$"))


def test_pieces_and_the_rows_say_where_a_piece_is_on_and_where_it_isnt(cast, page):
    panel = kit.open_page(page)
    pieces = panel.locator("[data-group=pieces]")
    expect(pieces.locator("[data-piece]")).to_have_count(0)
    pieces.locator("[data-action=show-pieces]").click()
    page.wait_for_url("**/m/repo/pieces")
    gate = pieces.locator('[data-piece="demo/gate"]')
    expect(gate.locator("[data-line=where]")).to_have_text("in 3 repos, off in 1")
    expect(gate).to_contain_text("gate from demo")
    expect(gate).to_contain_text("before a commit")
    expect(pieces.locator('[data-piece="demo/first"] [data-line=where]')).to_have_text("in no repo")
    expect(pieces.locator('[data-piece="repo/commit-rules"] [data-line=where]')).to_have_text(
        "in 4 repos")
    expect(pieces.locator("[data-line=sentence]")).to_contain_text(
        "A piece is in no repo until you add it there.")
    for name, where in (("atlas-api", "gate · commit-rules · commit-proof · push-check"),
                        ("harbor-infra", "gate, then its own hook · commit-rules"),
                        ("kestrel-web", "gate off · commit-rules"),
                        ("lumen-docs", "prose-check · lint"),
                        ("my-loadout", "commit-rules")):
        expect(kit.row(panel, cast[name])).to_have_attribute("where", where)
    # Opened, the piece that is off says so and offers to turn it on.
    opened = kit.opened(panel, cast["kestrel-web"])
    off = opened.locator('[data-piece="demo/gate"]')
    expect(off.locator(".line").first).to_contain_text("gate is off · A closed gate.")
    expect(off.locator("[data-action=turn-on]")).to_have_text("Turn on")


def test_rules_say_what_commit_rules_checks_and_reading_them_writes_nothing(cast, page):
    atlas = cast["atlas-api"]
    before = kit.objects(atlas)
    panel = kit.open_page(page, "repo/~!2Fcode!2Fatlas-api")
    opened = panel.locator(f'moxi-ui-row[data-key="{atlas}"] [data-part=opened]')
    rules = opened.locator("[data-part=rules]")
    expect(rules.locator("[data-line=rules]")).to_have_text(
        "1 line a committer is told before each commit")
    expect(rules.locator("[data-line=rules-text]")).to_have_count(0)
    rules.locator("[data-action=show-rules]").click()
    text = rules.locator("[data-line=rules-text]")
    expect(text).to_have_text(f"- no text matching: {kit.TAG_PATTERN}")
    expect(rules).not_to_contain_text("Commit-Check:")
    expect(rules).to_contain_text("What moxi repo rules prints for a committer here.")
    expect(rules).to_contain_text("Where commit-proof is added it ends with a Commit-Check line "
                                  "for what is staged; the page leaves that line out.")
    # The tag pattern is commit-rules' setting, shown on its line.
    rules_piece = opened.locator('[data-piece="repo/commit-rules"]')
    rules_piece.locator("[data-action=show-settings]").click()
    expect(rules_piece.locator("[data-line=setting]")).to_have_text(
        f"tag-pattern: {kit.TAG_PATTERN}")
    assert kit.objects(atlas) == before

    # A repo with commit-rules and no setting: nothing is printed for a committer there.
    harbor = kit.opened(panel, cast["harbor-infra"])
    expect(harbor.locator("[data-line=rules]")).to_have_text(
        "none set: nothing is printed for a committer here")
    expect(harbor.locator("[data-action=show-rules]")).to_have_count(0)
    assert kit.objects(atlas) == before

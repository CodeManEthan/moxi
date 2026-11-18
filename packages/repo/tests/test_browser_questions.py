"""The questions the Git hooks page is there to answer, each answered from the page the
way a person would, in a browser, through a real shell that starts repo's real page
server, on five repos made inside the shell's anchor folder (`repo_page_kit.seed`):

1. Does anything need me? The items first, each with its fix.
2. What runs in a repo's commits and pushes? An opened repo's events.
3. Where is a piece on, and where isn't it? `Pieces`' where, and each row's.
4. What does commit-rules check here? `Rules`, with no Commit-Check line, and no object
   written to the repo by reading it.
5. Did a gate refuse anything today? Moxi keeps no refusal, so none is drawn; a piece
   that broke twice shows its last break.
6. Will the pieces run under the git I use? Each repo's `Git` line.
7. Did the auto-commit commit and push? A refused pass, a throttled one after it, and a
   failed push, with the shas under the opened `Auto-commit` row.
8. What else runs on a repo's commits? Its own hook, after the pieces or on its own.

Questions 5 and 7 make their own shell, since they commit.
"""

from __future__ import annotations

import json
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
    disable = f"moxi repo git-hooks disable {lumen} demo/lint"
    expect(broke.locator("[data-line=fix] code")).to_have_text(
        f"{disable} (or: moxi repo git-hooks remove {lumen} demo/lint)")
    # Copy takes the command that runs as it stands, not the alternative.
    kit.copied(page, broke.locator("[data-line=fix] [data-action=copy]"), disable)
    expect(broke.locator("[data-line=record]")).to_have_count(0)
    broke.locator("[data-action=show-record]").click()
    expect(broke.locator("[data-line=record]")).to_contain_text("ValueError: lint fell over")

    # The repo they're in comes first among the rows, its fact heavy: the piece that stops
    # every commit leads over the newer break, which is its news.
    first = rows(panel).first
    expect(first).to_have_attribute("data-key", str(lumen))
    expect(first).to_have_attribute("name", "~/code/lumen-docs")
    expect(first).to_have_attribute("fact", "refuses every commit")
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
                        ("my-loadout", "commit-rules · its own hook before a commit")):
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


def test_a_refusal_leaves_no_line_and_a_break_shows_its_last_time(
        ui_shells, ui_shell_options, ui_contexts, hook_env):
    shell = kit.own_shell(ui_shells, ui_shell_options)
    atlas = kit.make_repo(shell, "code/atlas-api")
    kit.hooked(shell, hook_env, atlas, "demo/gate", "demo/lint")
    refused = kit.commit(shell, atlas, "a.md", {"DEMO_GATE": "refuse"})
    assert refused.returncode != 0 and "gate says no" in refused.stderr
    assert kit.commit(shell, atlas, "b.md", {"DEMO_LINT": "raise"}).returncode == 0
    # The first break is put two hours back, so the page's time tells the two apart.
    store = kit.records_file(shell)
    records = json.loads(store.read_text(encoding="utf-8"))
    (key,) = records
    records[key]["at"] = "2026-01-01T03:00:00Z"
    store.write_text(json.dumps(records), encoding="utf-8")
    assert kit.commit(shell, atlas, "c.md", {"DEMO_LINT": "raise"}).returncode == 0
    last = json.loads(store.read_text(encoding="utf-8"))[key]["at"]
    assert last != "2026-01-01T03:00:00Z"
    # Written again with the shell's Python, the entries read as this environment's.
    kit.moxi(shell, "repo", "git-hooks", "refresh", str(atlas))

    page = ui_contexts(shell).pages[0]
    panel = kit.open_page(page)
    expect(panel.locator("[data-line=verdict]")).to_have_text("1 thing needs you")
    hooks = panel.locator("[data-group=hooks]")
    expect(hooks.locator(".prob")).to_have_count(1)
    broke = kit.item(panel, "~/code/atlas-api: lint broke")
    expect(broke.locator("[data-line=time]")).to_have_text(kit.at_of(page, last))
    row = kit.row(panel, atlas)
    expect(row).to_have_attribute("lead", f"lint broke at {kit.at_of(page, last)}")
    opened = kit.opened(panel, atlas)
    expect(opened.locator("[data-line=today]")).to_have_count(1)
    for text in ("says no", "refused"):
        expect(hooks).not_to_contain_text(text)
        expect(panel.locator("[data-group=repos]")).not_to_contain_text(text)


def test_each_repo_says_which_git_runs_its_pieces_and_how(cast, page):
    version = kit.git_version()
    panel = kit.open_page(page)
    for name in ("atlas-api", "harbor-infra", "kestrel-web", "lumen-docs", "my-loadout"):
        opened = kit.opened(panel, cast[name])
        expect(opened.locator("[data-line=git]")).to_have_text(
            f"{version} · hooks run from the repo's own git config · {cast[name]}")


SAVE = """import sys, moxi_repo
moxi_repo.save_spec(moxi_repo.Spec(
    root=sys.argv[1], name="notes/snapshot", paths=("notes",),
    author=("snap-bot", "snap@example.invalid"), push="origin",
    prepare="demo_pieces.committing:prepare", min_interval_s=float(sys.argv[2])))
"""


def test_an_auto_commit_says_what_it_committed_and_what_it_pushed(
        ui_shells, ui_shell_options, ui_contexts, tmp_path):
    shell = kit.own_shell(ui_shells, ui_shell_options)
    notes = kit.make_repo(shell, "notes")
    bare = tmp_path / "notes-remote.git"
    kit.git(shell, tmp_path, "init", "-q", "--bare", str(bare))
    deny = bare / "hooks" / "pre-receive"
    deny.write_text("#!/bin/sh\necho 'notes.git is over its quota' >&2\nexit 1\n",
                    encoding="utf-8")
    kit.git(shell, notes, "remote", "add", "origin", str(bare))

    def save(interval_s: int) -> None:
        kit.run(shell, sys.executable, "-c", SAVE, str(notes), str(interval_s))

    def run_pass(name: str | None, env: dict | None = None) -> str:
        if name is not None:
            (notes / "notes").mkdir(exist_ok=True)
            (notes / "notes" / name).write_text(name, encoding="utf-8")
        done = kit.moxi(shell, "repo", "commit", str(notes), "--spec", "notes/snapshot",
                        env=env, check=False)
        return done.stdout + done.stderr

    save(0)
    assert "pushed main" in run_pass("a.md")
    pushed = kit.git(shell, notes, "rev-parse", "HEAD").stdout.strip()
    deny.chmod(0o755)
    assert "push failed" in run_pass("b.md")
    last = kit.git(shell, notes, "rev-parse", "HEAD").stdout.strip()
    assert "refused" in run_pass("c.md", {"DEMO_PREPARE": "refuse"})
    save(3600)
    assert "throttled" in run_pass("d.md")

    page = ui_contexts(shell).pages[0]
    panel = kit.open_page(page)
    expect(panel.locator("[data-line=verdict]")).to_have_text("2 things need you")
    refused = kit.item(panel, "Auto-commit notes/snapshot is refused")
    expect(refused.locator("[data-line=time]")).to_have_text(re.compile(r"^since "))
    expect(refused.locator("[data-line=dim]")).to_contain_text(
        "demo prepare says no", )
    expect(refused.locator("[data-line=dim]")).to_contain_text("~/notes · refused 1 pass · last commit ")
    expect(refused.locator("[data-line=fix]")).to_have_text("Its next pass that commits clears this.")
    push = kit.item(panel, "Auto-commit notes/snapshot can't push")
    expect(push.locator("[data-line=dim]")).to_contain_text("~/notes · 1 commit on main not pushed")
    expect(push.locator("[data-line=fix]")).to_have_text("The next pass pushes.")

    row = kit.row(panel, notes)
    expect(row).to_have_attribute("fact", "auto-commit refused")
    expect(row).to_have_attribute("lead", re.compile(
        r"^Auto-commit notes/snapshot: refused since .+ · last commit "))
    auto = kit.opened(panel, notes).locator('[data-part=auto-commit] [data-spec="notes/snapshot"]')
    expect(auto.locator("[data-line=state]")).to_contain_text("refused since ")
    lines = auto.locator("[data-line=auto]")
    expect(lines).to_have_count(2)
    expect(lines.nth(0)).to_have_text(re.compile(rf"^last commit {last[:7]} at "))
    expect(lines.nth(1)).to_have_text(f"main pushed {pushed[:7]}")


def test_a_repo_says_what_else_runs_on_its_commits(cast, page):
    panel = kit.open_page(page)
    harbor = cast["harbor-infra"]
    expect(kit.row(panel, harbor)).to_have_attribute("where", "gate, then its own hook · commit-rules")
    opened = kit.opened(panel, harbor)
    expect(opened.locator("[data-event=pre-commit] [data-line=own]")).to_have_text(
        "then its own hook, ~/code/harbor-infra/.git/hooks/pre-commit")
    expect(opened.locator("[data-event=commit-msg] [data-line=own]")).to_have_count(0)
    loadout = cast["my-loadout"]
    expect(kit.row(panel, loadout)).to_have_attribute(
        "where", "commit-rules · its own hook before a commit")

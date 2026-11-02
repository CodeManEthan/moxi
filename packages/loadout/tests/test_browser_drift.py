"""loadout's drift block and page in a browser, through a real shell that starts loadout's
real page server: the contract check on both, the count of items that aren't ok and the
harness homes' note, both gone once the user installs in a terminal, a manifest that
can't be read, the block's link to the page from the shell's home, and the 360 px width
check.

The loadout folder, its manifest and every `moxi` command here use the shell's own
environment, so loadout's config, ledger and every harness file are under the shell's
home. The shell has a fake `claude` and `codex` first on its `PATH`, which fail if run (no
item here needs them), and a global git config of the test's own with no system config.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from playwright.sync_api import expect

pytestmark = [pytest.mark.browser,
              pytest.mark.skipif(not sys.platform.startswith("linux"),
                                 reason="loadout's drift block is checked in a browser on "
                                        "Linux; the Windows and macOS runs are owed")]

DRIFT = "/m/loadout/api/drift"
FINE = {"ok", "off"}
#: The event loadout's page sends on the document when an act has ended.
CHANGED = "moxi-loadout-changed"
FAKE_CLI = "#!/bin/sh\necho \"fake $(basename \"$0\"): no test here runs it\" >&2\nexit 1\n"


@pytest.fixture(scope="module")
def ui_shell_options(tmp_path_factory) -> dict:
    """A fake `claude` and `codex` first on the shell's `PATH`, which its page servers
    inherit, and git with the test's own global config and no system config."""
    bin_dir = tmp_path_factory.mktemp("fake-clis")
    for name in ("claude", "codex"):
        path = bin_dir / name
        path.write_text(FAKE_CLI, encoding="utf-8")
        path.chmod(0o755)
    gitconfig = tmp_path_factory.mktemp("git") / "gitconfig"
    gitconfig.write_text("[user]\n\tname = sample-bot\n\temail = bot@example.invalid\n",
                         encoding="utf-8")
    return {"env": {"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
                    "GIT_CONFIG_GLOBAL": str(gitconfig), "GIT_CONFIG_NOSYSTEM": "1"}}


def moxi(shell, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    """`moxi ARGS` as the user would run it in a terminal with the shell's environment."""
    done = subprocess.run([sys.executable, "-m", "moxi", *args], env=shell.env,
                          cwd=shell.env["HOME"], stdin=subprocess.DEVNULL,
                          capture_output=True, text=True, timeout=120)
    if check and done.returncode != 0:
        raise AssertionError(f"moxi {' '.join(args)} failed ({done.returncode}):\n"
                             f"{done.stdout}{done.stderr}")
    return done


def skill(folder: Path, name: str) -> None:
    (folder / "skills" / name).mkdir(parents=True, exist_ok=True)
    (folder / "skills" / name / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: A skill.\n---\nDo the thing.\n", encoding="utf-8")


@pytest.fixture(scope="module")
def folder(ui_shell) -> Path:
    """The configured loadout folder, under the shell's home, with two skills in its
    manifest and nothing installed yet."""
    found = Path(ui_shell.env["HOME"]) / "my-loadout"
    for name in ("tidy-notes", "review-diff"):
        skill(found, name)
    (found / "loadout.toml").write_text('[user]\nskills = ["tidy-notes", "review-diff"]\n',
                                        encoding="utf-8")
    moxi(ui_shell, "config", "set", "loadout", "source", str(found))
    return found


def not_ok(shell) -> list[dict]:
    """The rows `moxi loadout status --json` gives that aren't ok."""
    done = moxi(shell, "loadout", "status", "--json", check=False)
    return [row for row in json.loads(done.stdout)["rows"] if row["state"] not in FINE]


def count_line(count: int) -> str:
    if count == 0:
        return "Every item is ok."
    return "1 item isn't ok." if count == 1 else f"{count} items aren't ok."


def test_the_drift_block_keeps_the_contract(folder, ui_page, ui_component_check):
    check = ui_component_check(ui_page, "loadout", "drift", keep=True)
    assert check.element == "moxi-loadout-drift"
    assert any(url.endswith(DRIFT) for url in check.requests), check.requests
    expect(check.locator.locator(".drift")).to_have_attribute("data-state", "up")


def test_the_page_keeps_the_contract_and_holds_the_drift_block(folder, ui_page,
                                                                  ui_component_check):
    check = ui_component_check(ui_page, "loadout", "page", keep=True)
    assert check.element == "moxi-loadout-page"
    expect(check.locator.locator("h1")).to_have_text("Loadout")
    expect(check.locator.locator('[data-part="drift"] moxi-loadout-drift .drift')
           ).to_have_attribute("data-state", "up")


def test_the_count_is_the_items_not_ok_and_goes_once_the_user_installs(folder, ui_shell,
                                                                         ui_page):
    skill(folder, "fresh-skill")
    (folder / "loadout.toml").write_text(
        '[user]\nskills = ["tidy-notes", "review-diff", "fresh-skill"]\n', encoding="utf-8")
    rows = not_ok(ui_shell)
    assert rows and {row["state"] for row in rows} == {"missing"}, rows
    ui_page.goto("/m/ui/")
    block = ui_page.locator("#moxi-panel > moxi-ui-page #home moxi-loadout-drift")
    drift = block.locator(".drift")
    expect(drift).to_have_attribute("data-count", str(len(rows)))
    expect(block.locator('[data-line="count"]')).to_have_text(count_line(len(rows)))
    # No terminal run has recorded the harness homes yet, so the block says so, and the
    # line is never counted.
    expect(block.locator('[data-line="note"]')).to_contain_text(
        "run moxi loadout install once in a terminal")

    moxi(ui_shell, "loadout", "install")
    assert not_ok(ui_shell) == []
    ui_page.evaluate(f"() => document.dispatchEvent(new CustomEvent('{CHANGED}'))")
    expect(drift).to_have_attribute("data-count", "0")
    expect(block.locator('[data-line="count"]')).to_have_text("Every item is ok.")
    expect(block.locator('[data-line="note"]')).to_have_count(0)
    # The terminal's install wrote only under the shell's home.
    home = Path(ui_shell.env["HOME"]).resolve()
    status = json.loads(moxi(ui_shell, "loadout", "status", "--json").stdout)
    targets = [Path(row["target"]) for row in status["rows"] if row["target"]]
    assert targets
    for target in targets:
        assert target.resolve().is_relative_to(home) or target.is_relative_to(home), target


def test_a_manifest_that_cant_be_read_shows_its_line_and_no_count(folder, ui_page,
                                                                    ui_component_check):
    manifest = folder / "loadout.toml"
    kept = manifest.read_text(encoding="utf-8")
    manifest.write_text("[user\n", encoding="utf-8")
    try:
        check = ui_component_check(ui_page, "loadout", "drift", keep=True)
        drift = check.locator.locator(".drift")
        expect(drift).to_have_attribute("data-state", "problem")
        expect(check.locator.locator('[data-line="problem"]')).to_contain_text("loadout:")
        expect(check.locator.locator('[data-line="count"]')).to_have_count(0)
    finally:
        manifest.write_text(kept, encoding="utf-8")


def test_the_link_on_the_home_opens_the_page_which_it_then_marks(folder, ui_page):
    ui_page.goto("/m/ui/")
    link = ui_page.locator("#moxi-panel > moxi-ui-page #home moxi-loadout-drift h2 a")
    expect(link).to_have_text("Loadout")
    expect(link).not_to_have_attribute("aria-current", "page")
    link.click()
    panel = ui_page.locator("#moxi-panel > moxi-loadout-page")
    expect(panel.locator("h1")).to_have_text("Loadout")
    assert ui_page.evaluate("location.pathname") == "/m/loadout/"
    expect(panel.locator("moxi-loadout-drift h2 a")).to_have_attribute("aria-current", "page")


def test_the_block_and_the_page_fit_a_phone(folder, ui_page, ui_width_check):
    ui_width_check(ui_page, module="loadout")
    expect(ui_page.locator("#moxi-panel > moxi-loadout-page moxi-loadout-drift .drift")
           ).to_be_visible()
    ui_width_check(ui_page)
    ui_width_check(ui_page, module="loadout", name="drift")

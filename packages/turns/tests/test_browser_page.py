"""turns' panel in a browser, through a real shell that starts turns' real page server: the
contract check and the 360 px width check.

The sessions are the fake harness's endpoints (`make_endpoint`), run in the test's own
process with turns' runtime root pointed at the shell's, so the page server the shell
starts finds them where it looks. No live session is read.
"""

from __future__ import annotations

import sys

import pytest
from playwright.sync_api import expect

from fake_harness import FakeHarness
from moxi_dev.testing import short_dir
from moxi_turns.results import Agent

pytestmark = [pytest.mark.browser, pytest.mark.timeout(120),
              pytest.mark.skipif(not sys.platform.startswith("linux"),
                                 reason="turns' panel is checked in a browser on Linux; the "
                                        "Windows and macOS runs are owed")]

SID = "aaaaaaaa-1111-4111-8111-111111111111"
CWD = "/srv/work/sample-project"
SESSIONS = "/m/turns/api/sessions"


@pytest.fixture(scope="module")
def ui_shell(ui_shells, ui_shell_options):
    """The test shell, its home in a short directory rather than pytest's tmp. The
    endpoints the tests open put their sockets under the shell's runtime root, and under
    pytest's root (a numbered directory per run, a worker's directory, a counted one per
    shell) a socket's path can pass the core's limit, which moves it to a shared directory
    outside the test. The shell is stopped before its home is removed."""
    with short_dir("tb-") as home:
        shell = ui_shells(home=home, **ui_shell_options)
        try:
            yield shell
        finally:
            shell.stop()


@pytest.fixture
def shell(ui_shell, turns_env, monkeypatch):
    """The module's shell, with this process's turns runtime root set to the shell's, so
    every endpoint `make_endpoint` opens is one the shell's page server finds."""
    monkeypatch.setenv("MOXI_TURNS_RUNTIME_DIR", str(ui_shell.runtime_dir("turns").parent))
    return ui_shell


def harness(**over) -> FakeHarness:
    fake = FakeHarness()
    fake.cwd = CWD
    for name, value in over.items():
        setattr(fake, name, value)
    return fake


def rows(panel):
    return panel.locator("moxi-ui-row[data-key]")


def test_the_page_keeps_the_contract(shell, make_endpoint, ui_page, ui_component_check):
    make_endpoint(harness(), session_id=SID)
    check = ui_component_check(ui_page, "turns", "page")
    assert check.element == "moxi-turns-page"
    assert any(SESSIONS in url for url in check.requests), check.requests


def test_the_page_fits_a_phone(shell, make_endpoint, ui_page, ui_width_check):
    long = "a-very-long-description-with-no-spaces-" * 6
    fake = harness(trouble="t" * 200, entries=[Agent("a" * 80, "subagent", "running", final=False, description=long)])
    fake.cwd = "/srv/" + "deep/" * 40 + "project"
    make_endpoint(fake, session_id="b" * 120)
    make_endpoint(harness(), session_id=SID)
    panel = ui_page.locator("#moxi-panel > moxi-turns-page")
    ui_width_check(ui_page, module="turns")
    expect(rows(panel)).to_have_count(2)
    ui_width_check(ui_page)

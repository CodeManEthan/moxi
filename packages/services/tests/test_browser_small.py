"""services' page on the small install in a browser: a shell over ui and services alone,
its supervisor holding ui's shell and no service of his (`browser_kit.fake_site`). The page reads its empty state
from the real rows (`Nothing is down`, `Yours` saying what a service is, with `Add a
service`, and ui's shell alone under `Moxi`), and fits 1280 and 400 px wide, with a row
opened and with the panel open.
"""

from __future__ import annotations


import pytest
from playwright.sync_api import expect

from browser_kit import MARKS, fake_site, get

pytestmark = MARKS

#: The modules the small install holds; the core is under every install.
SMALL = ("services", "ui")
SHELL = "ui/shell"
#: The fake supervisor's units: ui's shell running, and nothing of his; services' page
#: server finds the small install's modules alone, as the shell's snapshot does.
UNITS = {"written": {}, "modules": {SHELL: {"kind": "daemon", "enabled": True, "active": True}},
         "health": {SHELL: ["ok", "the shell answers"]}, "installed": list(SMALL)}


def others() -> list[str]:
    """Every installed module the small install leaves out."""
    from moxi import modules

    return [found.name for found in modules.scan() if found.name not in SMALL]


@pytest.fixture(scope="module")
def small(ui_shells, tmp_path_factory):
    return ui_shells(leave_out=others(), site=[str(fake_site(tmp_path_factory.mktemp("units"), UNITS))])


@pytest.fixture
def small_page(small, ui_contexts):
    return ui_contexts(small).pages[0]


def panel_of(page):
    page.goto("/m/services/")
    panel = page.locator("#moxi-panel > moxi-services-page")
    expect(panel.locator('section[data-part="list"]')).to_have_attribute("data-state", "up")
    return panel


def ids(items) -> list[str]:
    return [item.get_attribute("data-id") for item in items.all()]


def test_the_small_install_says_what_a_service_is_and_lists_ui_shell_alone_under_moxi(small, small_page):
    listed = get(small, "api/rows")
    assert [r["id"] for r in listed["rows"]] == [SHELL], listed
    panel = panel_of(small_page)
    first = panel.locator('section[data-part="first"]')
    expect(first.locator('[data-line="sentence"]')).to_have_text(
        "Nothing is down. You have added no services yet.")
    expect(first.locator('[data-line="counts"]')).to_have_count(0)
    expect(first.locator(".item")).to_have_count(0)
    groups = panel.locator('section[data-part="group"]')
    expect(groups).to_have_count(2)
    yours, moxi = groups.first, groups.last
    expect(yours).to_have_attribute("data-group", "yours")
    expect(yours.locator('[data-line="group"]')).to_have_text("Yours")
    expect(yours.locator('[data-line="what"]')).to_have_text(
        "A service is a command this computer keeps running, such as a dev server, or runs on a "
        "schedule, such as a nightly backup.")
    expect(yours.get_by_role("button", name="Add a service", exact=True)).to_have_count(1)
    expect(yours.locator(".item")).to_have_count(0)
    expect(moxi).to_have_attribute("data-group", "moxi")
    expect(moxi.locator('[data-line="group"]')).to_have_text("Moxi")
    expect(moxi.locator(".item")).to_have_count(1)
    assert ids(moxi.locator(".item")) == [SHELL]
    expect(moxi.locator(f'.item[data-id="{SHELL}"] moxi-ui-row')).to_have_attribute(
        "where", "ui")
    expect(panel.locator('[data-line="module-problem"]')).to_have_count(0)


@pytest.mark.parametrize("width", [1280, 400])
def test_the_small_install_fits_the_width(small, small_page, ui_width_check, width):
    small_page.set_viewport_size({"width": width, "height": 800})
    panel = panel_of(small_page)
    ui_width_check(small_page, width=width)
    name = get(small, "api/rows")["rows"][0]["name"]
    item = panel.locator(f'.item[data-id="{SHELL}"]')
    item.get_by_role("button", name=f"Open {name}", exact=True).click()
    expect(item.locator('[data-part="opened"]')).to_be_visible()
    ui_width_check(small_page, width=width)
    panel.get_by_role("button", name="Add a service", exact=True).click()
    expect(panel.locator('moxi-services-panel [data-part="panel"]')).to_be_visible()
    ui_width_check(small_page, width=width)

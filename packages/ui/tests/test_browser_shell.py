"""The shell document in a browser: sign-in, the modules view, the top line, the band of
pages and panels, the contract check, a cookie with no key, the page policy, and the
layout at 900 and 360 px.

Each test opens a fresh context on the module's one test shell, which serves `ui` and the
`fixture` module (tests/conftest.py)."""

from __future__ import annotations

import re

import pytest
from moxi import modules
from playwright.sync_api import expect

pytestmark = [pytest.mark.browser, pytest.mark.timeout(60)]

# The doctor runs `moxi doctor --json` in a child; give it longer than a click.
DOCTOR_MS = 20_000


def _modules(page):
    return page.locator("#moxi-panel > moxi-ui-page")


def _button(page):
    return page.locator("#moxi-pages-button")


def _band(page):
    return page.locator("#moxi-pages")


def _pick(page, module: str, route: str = "") -> None:
    """Open the band and pick `module`'s page there."""
    _button(page).click()
    page.locator(f"#moxi-pages a[data-module={module}][data-route='{route}']").click()
    expect(_band(page)).to_be_hidden()


# Names added to the band's first group until it holds twelve, and put back whenever the
# shell draws the band again: the shell's own two and the fixture's are always there, and
# the rest depends on the modules installed beside ui.
_TWELVE_NAMES = """() => {
  const band = document.getElementById("moxi-pages");
  const fill = () => {
    const group = band.querySelector("[data-group=daily]");
    if (group === null || group.querySelector("[data-extra]") !== null) return;
    const more = ["Launching", "Sittings", "Live sittings", "Budget", "Letters", "Stores",
                  "Folders", "Outfit", "Commit hooks"];
    for (const name of more.slice(0, Math.max(0, 12 - band.querySelectorAll("a").length))) {
      const link = document.createElement("a");
      link.textContent = name;
      link.href = "/m/ui/";
      link.dataset.extra = "";
      group.append(link);
    }
  };
  new MutationObserver(fill).observe(band, {childList: true});
  fill();
}"""

# Each name's box, and how far the document scrolls sideways.
_NAME_BOXES = """() => ({
  boxes: [...document.querySelectorAll("#moxi-pages a")].map((a) => {
    const box = a.getBoundingClientRect();
    return {top: box.top, left: box.left, right: box.right, height: box.height};
  }),
  line: parseFloat(getComputedStyle(document.querySelector("#moxi-pages a")).lineHeight),
  scroll: document.documentElement.scrollWidth - document.documentElement.clientWidth,
})"""


def test_signing_in_from_the_ticket_url_stores_the_key_and_leaves_a_clean_url(ui_shell, ui_contexts):
    context = ui_contexts(sign_in=False)
    page = context.new_page()
    url = ui_shell.signin_url()
    assert "#t=" in url and "?n=" in url
    page.goto(url)
    page.wait_for_url(ui_shell.url + "/m/ui/")
    expect(_modules(page)).to_be_attached()
    assert page.url == ui_shell.url + "/m/ui/"
    assert page.evaluate("location.hash + location.search") == ""
    cookies = {c["name"]: c for c in context.cookies()}
    session = cookies["moxi-ui"]
    assert session["httpOnly"] is True and session["sameSite"] == "Strict"
    assert session["path"] == "/"
    # HttpOnly: the page's script can't read it.
    assert "moxi-ui=" not in page.evaluate("document.cookie")
    key = page.evaluate("localStorage.getItem('moxi-ui-key')")
    assert isinstance(key, str) and len(key) >= 32


def test_a_used_ticket_url_does_not_sign_in(ui_shell, ui_contexts):
    url = ui_shell.signin_url()
    ui_contexts(sign_in=False).new_page().goto(url)
    page = ui_contexts(sign_in=False).new_page()
    page.goto(url)
    expect(page.locator("#say")).to_contain_text("was used or has expired")
    assert page.evaluate("localStorage.getItem('moxi-ui-key')") is None


def test_the_modules_view_lists_ui_and_fixture_and_check_shows_doctor_rows(ui_page):
    view = _modules(ui_page)
    for name in ("ui", "fixture"):
        card = view.locator(f".card[data-module={name}]")
        expect(card.locator(".head .tag")).to_have_text("installed")
    fixture = view.locator(".card[data-module=fixture]")
    expect(fixture).to_contain_text("A page module the UI's tests install")
    # The component written for a later contract is listed with the upgrade it needs.
    expect(fixture).to_contain_text("later")
    expect(fixture).to_contain_text(re.compile("upgrade moxi-ui", re.I))
    fixture.get_by_role("button", name="Check").click()
    rows = fixture.locator("ul.rows > li")
    expect(rows.first).to_be_visible(timeout=DOCTOR_MS)
    tones = rows.evaluate_all("items => items.map((li) => li.className)")
    assert tones and set(tones) <= {"ok", "off", "warn", "fail"}
    order = {"fail": 0, "warn": 1, "off": 2, "ok": 3}
    assert [order[t] for t in tones] == sorted(order[t] for t in tones), tones
    expect(rows.first.locator("code").first).not_to_be_empty()


def test_the_home_holds_the_servers_block_and_the_fixture_block(ui_page):
    home = _modules(ui_page).locator("#home")
    expect(home.locator("section[data-block='ui.servers'] moxi-ui-servers")).to_be_attached()
    block = home.locator("section[data-block='fixture.block']")
    expect(block.locator("h2")).to_have_text("Fixture count")
    expect(block.locator("moxi-fixture-block").get_by_test_id("count")).to_have_text(
        re.compile(r"^Count: \d+$"))


def test_the_band_opens_the_fixture_panel_in_the_same_document_and_back_returns(ui_shell, ui_page):
    ui_page.evaluate("window.__sameDocument = true")
    button, band = _button(ui_page), _band(ui_page)
    expect(button).to_have_accessible_name("Home")
    expect(button).to_have_attribute("aria-expanded", "false")
    expect(band).to_be_hidden()
    button.click()
    expect(button).to_have_attribute("aria-expanded", "true")
    links = band.locator("a")
    # Every installed module with a page has a name between the shell's own two.
    expect(links.first).to_have_text("Home")
    expect(links.last).to_have_text("Modules")
    expect(links.last).to_have_attribute("href", "/m/ui/modules")
    expect(band.locator("a[data-module=fixture]")).to_have_text("Fixture")
    expect(band.locator("a[aria-current=page]")).to_have_text("Home")
    # Settings is in the top line, alone at the right, and not in the band.
    expect(band.get_by_text("Settings")).to_have_count(0)
    expect(ui_page.locator(".moxi-top").get_by_role("link", name="Settings")).to_be_visible()
    band.locator("a[data-module=fixture]").click()
    # A pick closes the band and leaves the focus on its button.
    expect(band).to_be_hidden()
    expect(button).to_be_focused()
    panel = ui_page.locator("#moxi-panel > moxi-fixture-page")
    expect(panel.get_by_test_id("route")).to_have_text("Route: ")
    expect(ui_page).to_have_url(ui_shell.url + "/m/fixture/")
    expect(button).to_have_accessible_name("Fixture")
    expect(band.locator("a[aria-current=page]")).to_have_text("Fixture")
    # The panel's own inline tag, placed by the panel.
    expect(panel.locator("moxi-fixture-tag")).to_have_text("on the page")
    ui_page.go_back()
    expect(ui_page).to_have_url(ui_shell.url + "/m/ui/")
    expect(_modules(ui_page).locator(".card[data-module=fixture]")).to_be_visible()
    ui_page.go_forward()
    # The URL first: a failure here is the browser not going forward, and one on the
    # panel below is the shell not showing the entry it went forward to.
    expect(ui_page).to_have_url(ui_shell.url + "/m/fixture/")
    expect(panel).to_be_attached()
    # The modules view's own link uses navigate(), with no reload either.
    panel.get_by_role("button", name="Modules").click()
    _modules(ui_page).locator(".card[data-module=fixture]").get_by_role(
        "link", name="Open its panel").click()
    expect(panel).to_be_attached()
    assert ui_page.evaluate("window.__sameDocument") is True


def test_the_band_closes_on_escape_on_a_click_outside_and_when_the_focus_leaves(ui_page):
    button, band = _button(ui_page), _band(ui_page)
    button.focus()
    ui_page.keyboard.press("Enter")
    expect(band).to_be_visible()
    # Tab reaches Settings, then the band's names in order.
    ui_page.keyboard.press("Tab")
    expect(ui_page.locator("#moxi-settings")).to_be_focused()
    ui_page.keyboard.press("Tab")
    expect(band.locator("a").first).to_be_focused()
    ui_page.keyboard.press("Escape")
    expect(band).to_be_hidden()
    expect(button).to_be_focused()
    expect(button).to_have_attribute("aria-expanded", "false")
    # The button closes what it opened.
    button.click()
    expect(band).to_be_visible()
    button.click()
    expect(band).to_be_hidden()
    # A click on the page under the band.
    button.click()
    expect(band).to_be_visible()
    ui_page.locator("#moxi-panel").click(position={"x": 5, "y": 300})
    expect(band).to_be_hidden()
    # The focus moving on into the page.
    button.click()
    band.locator("a").last.focus()
    ui_page.keyboard.press("Tab")
    expect(band).to_be_hidden()
    # A name picked from the keyboard.
    button.focus()
    ui_page.keyboard.press("Enter")
    band.locator("a[data-module=fixture]").focus()
    ui_page.keyboard.press("Enter")
    expect(ui_page.locator("#moxi-panel > moxi-fixture-page")).to_be_attached()
    expect(band).to_be_hidden()


def test_the_top_line_names_the_page_on_every_route(ui_shell, ui_page):
    button, band = _button(ui_page), _band(ui_page)
    settings = ui_page.locator("#moxi-settings")

    def shows(name: str, title: str, current: str | None) -> None:
        expect(button).to_have_accessible_name(name)
        expect(button).to_have_text(f"{name}▾")
        expect(ui_page).to_have_title(title)
        expect(band.locator("a[aria-current=page]")).to_have_text([current] if current else [])

    shows("Home", "Home - Moxi", "Home")
    _pick(ui_page, "ui", "modules")
    expect(ui_page).to_have_url(ui_shell.url + "/m/ui/modules")
    shows("Modules", "Modules - Moxi", "Modules")
    expect(_modules(ui_page).locator(".card[data-module=fixture]")).to_be_visible()
    _pick(ui_page, "fixture")
    shows("Fixture", "Fixture - Moxi", "Fixture")
    # On the settings no name in the band is current; the top line's Settings is.
    expect(settings).not_to_have_attribute("aria-current", "page")
    settings.click()
    expect(ui_page).to_have_url(ui_shell.url + "/m/ui/settings")
    shows("Settings", "Settings - Moxi", None)
    expect(settings).to_have_attribute("aria-current", "page")
    ui_page.goto("/m/ui/settings/fixture")
    shows("Settings", "Settings - Moxi", None)
    # A deeper route of a module's page keeps the page's name.
    ui_page.goto("/m/fixture/deep/route")
    shows("Fixture", "Fixture - Moxi", "Fixture")
    # Where the panel holds a message and no page, the button reads Pages.
    for path in ("/m/nosuch/", "/m/ui/elsewhere"):
        ui_page.goto(path)
        shows("Pages", "Moxi", None)
    # The brand goes Home.
    ui_page.locator(".moxi-brand").click()
    expect(ui_page).to_have_url(ui_shell.url + "/m/ui/")
    shows("Home", "Home - Moxi", "Home")


def test_the_shells_own_views_carry_no_title_of_their_own(ui_page):
    for route in ("", "modules"):
        ui_page.goto(f"/m/ui/{route}")
        expect(_modules(ui_page).locator(".card[data-module=ui]")).to_be_visible()
        expect(_modules(ui_page).locator("h1")).to_have_count(0)
    ui_page.goto("/m/ui/settings")
    view = _modules(ui_page)
    expect(view.locator("section.card[data-module=fixture]")).to_be_visible(timeout=10_000)
    expect(view.locator("h1")).to_have_count(0)
    # The two views still link to each other.
    view.get_by_role("link", name="Modules").click()
    expect(ui_page).to_have_url(re.compile(r"/m/ui/modules$"))
    _modules(ui_page).get_by_role("link", name="Settings").click()
    expect(ui_page).to_have_url(re.compile(r"/m/ui/settings$"))


def test_a_module_that_is_not_installed_says_so(ui_shell, ui_page):
    ui_page.goto("/m/nosuch/")
    expect(ui_page.locator("#moxi-panel h1")).to_have_text("No module named nosuch")
    ui_page.goto("/m/ui/elsewhere")
    expect(_modules(ui_page).locator("h1")).to_have_text("Nothing is here")


def test_the_contract_check_passes_on_the_fixture_block_and_tag_and_the_servers_block(
        ui_page, ui_component_check):
    block = ui_component_check(ui_page, "fixture", "block")
    assert block.tokens == ["accent"]
    assert all("/m/fixture/" in url or "/m/ui/api/push" in url for url in block.requests)
    ui_component_check(ui_page, "fixture", "tag", {"label": "x"})
    ui_component_check(ui_page, "ui", "servers", tokens=("ok",))


def test_a_context_with_the_cookie_and_no_key_gets_403_on_routes(ui_shell, ui_page):
    ui_page.evaluate("localStorage.removeItem('moxi-ui-key')")
    statuses = ui_page.evaluate("""async () => {
      const out = {};
      for (const path of ["/m/ui/api/modules", "/m/fixture/api/summary", "/m/ui/api/push?topics=ui.servers"]) {
        const answer = await fetch(path, {credentials: "same-origin"});
        out[path] = [answer.status, await answer.text()];
      }
      const wrong = await fetch("/m/ui/api/modules", {headers: {"X-Moxi-Key": "0".repeat(43)}});
      out.wrong = [wrong.status, await wrong.text()];
      const act = await fetch("/m/fixture/api/add", {method: "POST", body: "{}",
                                                     headers: {"Content-Type": "application/json"}});
      out.act = [act.status, await act.text()];
      return out;
    }""")
    assert all(value == [403, ""] for value in statuses.values()), statuses
    # Assets and navigations need the cookie only: the document still loads, and its
    # script, finding no key, says this browser is signed out.
    ui_page.reload()
    expect(ui_page.locator("#moxi-notice")).to_be_visible()
    expect(ui_page.locator("#moxi-notice")).to_contain_text("moxi ui open")
    answer = ui_page.evaluate("""async () => {
      const client = await import("/m/ui/client.js");
      return await client.api("fixture", "api/summary");
    }""")
    assert answer == {"status": 403, "data": None}


def test_a_shell_refusal_of_the_key_shows_the_signed_out_notice(ui_page):
    ui_page.evaluate("localStorage.setItem('moxi-ui-key', 'A'.repeat(43))")
    answer = ui_page.evaluate("""async () => {
      const client = await import("/m/ui/client.js");
      return await client.api("ui", "api/modules");
    }""")
    assert answer["status"] == 403
    expect(ui_page.locator("#moxi-notice")).to_be_visible()


def test_a_navigation_without_the_cookie_gets_the_signed_out_page(ui_shell, ui_contexts):
    page = ui_contexts(sign_in=False).new_page()
    for path in ("/m/ui/", "/m/fixture/", "/m/ui/settings", "/"):
        page.goto(path)
        expect(page).to_have_title("Moxi: signed out")
        expect(page.locator("body")).to_contain_text("moxi ui open")


def test_the_pages_run_under_the_policy_with_no_violation_or_error(ui_shell, ui_page):
    seen: list[str] = []
    ui_page.on("console", lambda msg: seen.append(f"console {msg.type}: {msg.text}")
               if msg.type == "error" else None)
    ui_page.on("pageerror", lambda err: seen.append(f"page error: {err}"))
    ui_page.add_init_script("""
      document.addEventListener("securitypolicyviolation", (event) => {
        console.error(`policy: ${event.violatedDirective} ${event.blockedURI}`);
      });""")
    response = ui_page.goto("/m/ui/")
    policy = response.headers["content-security-policy"]
    assert "require-trusted-types-for 'script'" in policy and "style-src 'self'" in policy
    expect(_modules(ui_page).locator("#home moxi-fixture-block")).to_be_attached()
    _pick(ui_page, "fixture")
    expect(ui_page.locator("moxi-fixture-page moxi-fixture-tag")).to_have_text("on the page")
    ui_page.locator("#moxi-settings").click()
    expect(ui_page.locator("moxi-ui-page section.card[data-module=fixture] moxi-ui-form")
           .locator("[data-control=limit]")).to_be_attached(timeout=10_000)
    assert ui_page.evaluate("document.documentElement.dataset.theme") == "system"
    assert ui_page.evaluate("document.documentElement.dataset.themeName") == "plain"
    assert seen == []


def test_at_360_px_nothing_scrolls_sideways_and_the_band_is_one_name_per_line(ui_page, ui_width_check):
    ui_width_check(ui_page)
    button, band = _button(ui_page), _band(ui_page)
    expect(button).to_be_visible()
    expect(ui_page.locator("#moxi-settings")).to_be_visible()
    expect(band).to_be_hidden()
    ui_page.evaluate(_TWELVE_NAMES)
    button.click()
    expect(button).to_have_attribute("aria-expanded", "true")
    expect(band.locator("a[data-module=fixture]")).to_be_visible()
    ui_width_check(ui_page)
    seen = ui_page.evaluate(_NAME_BOXES)
    assert len(seen["boxes"]) >= 12
    tops = [box["top"] for box in seen["boxes"]]
    assert tops == sorted(set(tops)), "one name per line, in order"
    assert len({box["left"] for box in seen["boxes"]}) == 1
    band.locator("a[data-module=fixture]").click()
    expect(band).to_be_hidden()
    ui_width_check(ui_page, module="fixture")
    ui_width_check(ui_page, module="ui")
    ui_width_check(ui_page, module="ui", route="modules")
    ui_width_check(ui_page, module="ui", route="settings")
    form = ui_page.locator("moxi-ui-page section.card[data-module=fixture] moxi-ui-form")
    # Every control type of the form is on that page, the TOML box among them.
    for key in ("greeting", "limit", "ratio", "loud", "colour", "folder", "tags", "extra",
                "rows.backup"):
        expect(form.locator(f"[data-control='{key}']")).to_be_attached()
    expect(form.locator("textarea[data-control=extra]")).to_be_visible()


def test_twelve_names_wrap_onto_a_second_row_at_900_px_and_no_name_splits(ui_page):
    ui_page.set_viewport_size({"width": 1280, "height": 800})
    button, band = _button(ui_page), _band(ui_page)
    ui_page.evaluate(_TWELVE_NAMES)
    button.click()
    expect(band.locator("a[data-extra], a[data-module]").nth(11)).to_be_visible()
    wide = ui_page.evaluate(_NAME_BOXES)
    assert len(wide["boxes"]) >= 12
    ui_page.set_viewport_size({"width": 900, "height": 800})
    seen = ui_page.evaluate(_NAME_BOXES)
    rows = sorted({box["top"] for box in seen["boxes"]})
    assert len(rows) >= 2 and len(rows) > len({box["top"] for box in wide["boxes"]})
    assert all(box["height"] <= seen["line"] + 1 for box in seen["boxes"]), "no name splits"
    assert all(box["left"] >= 32 and box["right"] <= 900 - 32 for box in seen["boxes"])
    assert seen["scroll"] == 0
    # Further rows start at the left edge, like the first.
    assert {min(box["left"] for box in seen["boxes"] if box["top"] == top) for top in rows} == {32}


def test_with_three_pages_the_band_is_one_row_over_the_page_with_its_groups_apart(
        ui_shells, fixture_site, ui_contexts):
    # A small install: the shell's own two pages and the fixture's.
    others = [found.name for found in modules.scan()
              if found.ok and found.page and found.name not in ("ui", "fixture")]
    shell = ui_shells(site=[fixture_site], leave_out=["tunnel", *others])
    page = ui_contexts(shell).pages[0]
    page.set_viewport_size({"width": 1280, "height": 800})
    button, band = _button(page), _band(page)
    panel = page.locator("#moxi-panel")
    closed = panel.bounding_box()["y"]
    button.click()
    expect(band.locator("a")).to_have_text(["Home", "Fixture", "Modules"])
    seen = page.evaluate(_NAME_BOXES)
    assert len({box["top"] for box in seen["boxes"]}) == 1
    # Home is alone in the first group; the fixture's page, which names no group, is with
    # Modules in the second. The groups are set apart by more room than names within one.
    home, fixture, last = seen["boxes"]
    assert home["left"] == 32
    assert fixture["left"] - home["right"] == pytest.approx(44, abs=0.5)
    assert last["left"] - fixture["right"] == pytest.approx(20, abs=0.5)
    expect(band.locator("[data-group]")).to_have_count(2)
    # The band lies over the page: it starts at the top edge, the top line stays in
    # sight, and nothing below moves.
    assert band.bounding_box()["y"] == 0
    assert panel.bounding_box()["y"] == closed
    expect(page.locator(".moxi-brand")).to_be_visible()
    expect(page.locator("#moxi-settings")).to_be_visible()
    page.locator(".moxi-brand").click(trial=True)


def test_the_gateway_section_shows_the_audit_log_and_each_devices_sign_ins(ui_shell, ui_page):
    from moxi_ui.gateway.audit import Audit
    from moxi_ui.gateway.devices import Devices

    state = ui_shell.state_dir("ui")
    devices = Devices.of(state)
    device = devices.add(name="Test phone", credential=None, public_key=None, sign_count=0,
                         host="gw.example.test", provider="tailscale", change="r1.1")
    writer = Audit(state)
    writer("signin", {"device": device.id, "session": "a" * 12})
    writer.request({"id": "r1", "method": "GET", "path": "/m/fixture/api/read", "status": 200,
                    "device": device.id}, signed_in=True)
    writer.close()
    try:
        ui_page.goto("/m/ui/")
        section = _modules(ui_page).locator("moxi-ui-gateway section[data-section=gateway]")
        expect(section.locator(f"li[data-device='{device.id}']")).to_contain_text("Sign-ins:")
        log = section.locator("details.log")
        log.locator("summary").click()
        newest = log.locator("li").first
        expect(newest).to_contain_text("request")
        expect(newest).to_contain_text("path=/m/fixture/api/read")
        expect(log.locator("li")).to_have_count(2)
    finally:
        devices.revoke(device.id)

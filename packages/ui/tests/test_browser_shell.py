"""The shell document in a browser: sign-in, the modules view, the menu and panels, the
contract check, a cookie with no key, the page policy, and the 360 px layout.

Each test opens a fresh context on the module's one test shell, which serves `ui` and the
`fixture` module (tests/conftest.py)."""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import expect

pytestmark = [pytest.mark.browser, pytest.mark.timeout(60)]

# The doctor runs `moxi doctor --json` in a child; give it longer than a click.
DOCTOR_MS = 20_000


def _modules(page):
    return page.locator("#moxi-panel > moxi-ui-page")


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


def test_the_menu_opens_the_fixture_panel_in_the_same_document_and_back_returns(ui_shell, ui_page):
    ui_page.evaluate("window.__sameDocument = true")
    links = ui_page.locator("#moxi-menu a")
    expect(links).to_have_text(["Modules", "Fixture", "Settings"])
    ui_page.locator("#moxi-menu a[data-module=fixture]").click()
    panel = ui_page.locator("#moxi-panel > moxi-fixture-page")
    expect(panel.get_by_test_id("route")).to_have_text("Route: ")
    expect(ui_page).to_have_url(ui_shell.url + "/m/fixture/")
    expect(ui_page.locator("#moxi-menu a[aria-current=page]")).to_have_text("Fixture")
    # The panel's own inline tag, placed by the panel.
    expect(panel.locator("moxi-fixture-tag")).to_have_text("on the page")
    ui_page.go_back()
    expect(ui_page).to_have_url(ui_shell.url + "/m/ui/")
    expect(_modules(ui_page).locator(".card[data-module=fixture]")).to_be_visible()
    ui_page.go_forward()
    expect(panel).to_be_attached()
    # The modules view's own link uses navigate(), with no reload either.
    panel.get_by_role("button", name="Modules").click()
    _modules(ui_page).locator(".card[data-module=fixture]").get_by_role(
        "link", name="Open its panel").click()
    expect(panel).to_be_attached()
    assert ui_page.evaluate("window.__sameDocument") is True


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
    ui_page.locator("#moxi-menu a[data-module=fixture]").click()
    expect(ui_page.locator("moxi-fixture-page moxi-fixture-tag")).to_have_text("on the page")
    ui_page.locator("#moxi-menu a", has_text="Settings").click()
    expect(ui_page.locator("moxi-ui-page section.card[data-module=fixture] moxi-ui-form")
           .locator("[data-control=limit]")).to_be_attached(timeout=10_000)
    assert ui_page.evaluate("document.documentElement.dataset.theme") == "system"
    assert seen == []


def test_at_360_px_nothing_scrolls_sideways_and_the_menu_is_one_button(ui_page, ui_width_check):
    ui_width_check(ui_page)
    button = ui_page.locator("#moxi-menu-button")
    expect(button).to_be_visible()
    expect(ui_page.locator("#moxi-menu")).to_be_hidden()
    button.click()
    expect(button).to_have_attribute("aria-expanded", "true")
    expect(ui_page.locator("#moxi-menu a[data-module=fixture]")).to_be_visible()
    ui_width_check(ui_page)
    ui_page.locator("#moxi-menu a[data-module=fixture]").click()
    expect(ui_page.locator("#moxi-menu")).to_be_hidden()
    ui_width_check(ui_page, module="fixture")
    ui_width_check(ui_page, module="ui")
    ui_width_check(ui_page, module="ui", route="settings")
    form = ui_page.locator("moxi-ui-page section.card[data-module=fixture] moxi-ui-form")
    # Every control type of the form is on that page, the TOML box among them.
    for key in ("greeting", "limit", "ratio", "loud", "colour", "folder", "tags", "extra",
                "rows.backup"):
        expect(form.locator(f"[data-control='{key}']")).to_be_attached()
    expect(form.locator("textarea[data-control=extra]")).to_be_visible()
    ui_page.set_viewport_size({"width": 1024, "height": 800})
    expect(ui_page.locator("#moxi-menu-button")).to_be_hidden()
    expect(ui_page.locator("#moxi-menu")).to_be_visible()

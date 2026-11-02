"""`confirm()` in a browser, against the `fixture` module's confirmed act: the sheet, the
key, one post however often Confirm is clicked, `api()`'s refusal, and the outcome of
every kind of answer, from the fixture, from a page server that fails or dies, and from a
shell that is gone.

The answers a real shell gives only after a wait or a race (a page server's own 503 past
its cap of requests, the shell's 503 for a stopping server, its 502 for a server it
can't reach and its 504 after 30 s) are given to the browser as the shell sends them, by
Playwright; the proxy's own tests show the shell sends them so.

The test shell serves `ui` and the `fixture` module (tests/conftest.py)."""

from __future__ import annotations

import json
import re

import pytest
from playwright.sync_api import expect

import fixture_kit

pytestmark = [pytest.mark.browser, pytest.mark.timeout(90)]

KEY = re.compile(r"^[A-Za-z0-9_-]{43}$")
NOT_SENT = "the module didn't get the act"
MAY_HAVE_ACTED = "the module may have acted; check its page"
ACT = "**/m/fixture/api/confirm"


@pytest.fixture(scope="module")
def ui_shell_options(fixture_site):
    """The shell over the fixture module, with tunnel left out of what it lists: the desk's
    confirmed acts need no tunnel, and the workspace installs one."""
    return {"site": [fixture_site], "leave_out": ["tunnel"]}


def _lines(shell, name: str) -> list[str]:
    try:
        return (shell.state_dir("fixture") / name).read_text().splitlines()
    except FileNotFoundError:
        return []


def _posts(page) -> list:
    """Every request the page sends to the confirmed act, from now on."""
    seen: list = []
    page.on("request", lambda request: seen.append(request)
            if request.url.split("?")[0].endswith("/m/fixture/api/confirm") else None)
    return seen


def _start(page, body, *, path="api/confirm", lines=("Line one.",)):
    """Call `confirm()` in the page without waiting for it; `_outcome` reads its result."""
    page.evaluate("""([path, body, lines]) => {
      window.__outcome = undefined;
      import("/m/ui/client.js")
        .then((client) => client.confirm("fixture", path, body, lines))
        .then((outcome) => { window.__outcome = outcome; },
              (err) => { window.__outcome = {thrown: err.name, message: err.message}; });
    }""", [path, body, list(lines)])


def _outcome(page) -> dict:
    page.wait_for_function("() => window.__outcome !== undefined")
    return page.evaluate("() => window.__outcome")


def _confirmed(page, body) -> dict:
    _start(page, body)
    page.locator("moxi-ui-confirm").get_by_role("button", name="Confirm").click()
    return _outcome(page)


def test_the_sheet_shows_the_act_and_a_double_click_posts_once(ui_shell, ui_page):
    errors: list[str] = []
    ui_page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)
    ui_page.on("pageerror", lambda err: errors.append(str(err)))
    ui_page.evaluate("""() => document.addEventListener("securitypolicyviolation", (event) => {
      console.error(`policy: ${event.violatedDirective}`);
    })""")
    ui_page.locator("#moxi-menu a[data-module=fixture]").click()
    panel = ui_page.locator("#moxi-panel > moxi-fixture-page")
    posts = _posts(ui_page)
    runs = len(_lines(ui_shell, "keys"))
    panel.get_by_role("button", name="Confirmed add").click()
    sheet = ui_page.locator("moxi-ui-confirm")
    dialog = sheet.get_by_role("dialog", name="Fixture")
    expect(dialog).to_be_visible()
    expect(dialog).to_contain_text("Adds one to the fixture's count.")
    expect(dialog.locator("pre")).to_have_text(json.dumps({"by": 1}, indent=2))
    assert posts == []
    dialog.get_by_role("button", name="Confirm").dblclick()
    expect(panel.get_by_test_id("status")).to_have_text(re.compile(r"^confirm: accepted /api/job\?job=j\d+$"))
    expect(sheet).to_have_count(0)
    assert len(posts) == 1
    key = posts[0].headers["idempotency-key"]
    assert KEY.match(key)
    # The module ran once, with the key the browser sent: it reached the page server.
    assert _lines(ui_shell, "keys")[runs:] == [key]
    # The sheet runs under the shell's policy with no violation or error.
    assert errors == []


def test_each_confirm_mints_its_own_key_and_resolves_with_the_status_path(ui_shell, ui_page):
    posts = _posts(ui_page)
    first = _confirmed(ui_page, {"by": 1})
    second = _confirmed(ui_page, {"by": 1})
    for outcome in (first, second):
        assert outcome["outcome"] == "accepted" and outcome["status"].startswith("/api/job?job=")
    keys = [request.headers["idempotency-key"] for request in posts]
    assert len(keys) == 2 and keys[0] != keys[1] and all(KEY.match(k) for k in keys)
    status, job = ui_shell.session().get("/m/fixture" + second["status"])
    assert status == 200 and job["state"] == "done"


def test_cancel_and_escape_send_nothing(ui_page):
    posts = _posts(ui_page)
    _start(ui_page, {"by": 1})
    ui_page.locator("moxi-ui-confirm").get_by_role("button", name="Cancel").click()
    assert _outcome(ui_page) == {"outcome": "cancelled"}
    _start(ui_page, {"by": 1})
    expect(ui_page.locator("moxi-ui-confirm").get_by_role("dialog")).to_be_visible()
    ui_page.keyboard.press("Escape")
    assert _outcome(ui_page) == {"outcome": "cancelled"}
    expect(ui_page.locator("moxi-ui-confirm")).to_have_count(0)
    assert posts == []


def test_api_refuses_a_confirmed_acts_path_and_confirm_takes_only_one(ui_page):
    posts = _posts(ui_page)
    thrown = ui_page.evaluate("""async () => {
      const client = await import("/m/ui/client.js");
      const out = [];
      for (const call of [
        () => client.api("fixture", "api/confirm", {by: 1}),
        () => client.api("fixture", "api/confirm?x=1", {by: 1}),
        () => client.api("fixture", "api/confirm"),
        () => client.confirm("fixture", "api/add", {by: 1}),
        () => client.confirm("fixture", "api/confirm", null),
        () => client.confirm("fixture", "api/confirm", {}, [1]),
        () => client.confirm("fixture", "../ui/api/stop", {}),
      ]) {
        try { await call(); out.push("no throw"); } catch (err) { out.push(err.name); }
      }
      out.push((await client.api("fixture", "api/add", {by: 0})).status);
      return out;
    }""")
    assert thrown == ["TypeError"] * 7 + [200]
    assert posts == []
    expect(ui_page.locator("moxi-ui-confirm")).to_have_count(0)


def test_the_fixtures_refusals_and_failure_give_their_outcomes(ui_page):
    assert _confirmed(ui_page, {"mode": "refuse"}) == {
        "outcome": "refused", "code": 422, "text": "the fixture refuses this act"}
    assert _confirmed(ui_page, {"mode": "conflict"}) == {
        "outcome": "refused", "code": 409, "text": "the fixture is busy with another act"}
    assert _confirmed(ui_page, {"nope": 1}) == {
        "outcome": "refused", "code": 400, "text": "unknown key: nope"}
    assert _confirmed(ui_page, {"pad": "x" * 70_000}) == {
        "outcome": "refused", "code": 413, "text": "the request was too large for the module"}
    assert _confirmed(ui_page, {"mode": "fail"}) == {"outcome": "unknown", "text": MAY_HAVE_ACTED}


@pytest.mark.parametrize(("status", "body", "headers", "outcome"), [
    # The page server's own 503, past its cap of requests: sent before any handler.
    (503, "", {"Retry-After": "1"},
     {"outcome": "refused", "code": 503, "text": "the module was too busy to take the act; try again"}),
    (503, {"error": "stopping", "module": "fixture"}, {},
     {"outcome": "refused", "code": 503, "text": NOT_SENT}),
    (502, {"error": "unreachable", "module": "fixture", "log": "shell.log"}, {},
     {"outcome": "refused", "code": 502, "text": NOT_SENT}),
    (504, {"error": "timeout", "module": "fixture"}, {},
     {"outcome": "unknown", "text": MAY_HAVE_ACTED}),
], ids=["module-503", "stopping-503", "unreachable-502", "timeout-504"])
def test_the_shells_answers_that_take_a_wait_give_their_outcomes(ui_page, status, body, headers,
                                                                 outcome):
    def answer(route):
        assert KEY.match(route.request.headers["idempotency-key"])
        if isinstance(body, dict):
            route.fulfill(status=status, json=body, headers=headers)
        else:
            route.fulfill(status=status, body=body, headers=headers)

    ui_page.route(ACT, answer)
    assert _confirmed(ui_page, {"by": 1}) == outcome


def test_a_page_server_that_fails_to_start_or_dies_and_a_shell_that_is_gone(
        ui_shells, ui_contexts, fixture_site):
    shell = ui_shells(site=[fixture_site], failed_for=0.5, leave_out=["tunnel"])
    page = ui_contexts(shell).pages[0]
    assert _confirmed(page, {"by": 1})["outcome"] == "accepted"
    # Sent, and the server ended before it answered: the module may have acted.
    assert _confirmed(page, {"mode": "die"}) == {"outcome": "unknown", "text": MAY_HAVE_ACTED}
    # A server that can't start: nothing reached the module.
    fixture_kit.fail_start(shell)
    try:
        if shell.server("fixture") and shell.server("fixture")["state"] == "running":
            shell.kill("fixture")
        assert _confirmed(page, {"by": 1}) == {"outcome": "refused", "code": 502, "text": NOT_SENT}
    finally:
        fixture_kit.fail_start(shell, on=False)
    # The shell itself gone: no answer at all.
    shell.stop()
    assert _confirmed(page, {"by": 1}) == {"outcome": "unknown", "text": MAY_HAVE_ACTED}


def test_tunnel_is_left_out_for_these_tests(ui_shell):
    """The desk's confirmed acts need no tunnel: every test here runs with none in the
    shell's view."""
    _, view = ui_shell.session().get("/m/ui/api/modules")
    assert "tunnel" not in {row["name"] for row in view["modules"]}

"""moxi_ui.testing: the plugin's load, the test shell over the fixture module, and the
contract and width checks in a browser."""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from moxi_dev.testing import wait_for
from playwright.sync_api import expect

import fixture_kit


def test_loading_the_plugin_imports_no_moxi_ui_submodule_and_no_playwright(tmp_path):
    (tmp_path / "test_plain.py").write_text(textwrap.dedent("""
        import sys

        def test_plain(request):
            assert request.config.pluginmanager.has_plugin("moxi_ui")
            loaded = sorted(m for m in sys.modules if m.split(".")[0] in ("moxi_ui", "moxi",
                            "moxi_loopback", "playwright"))
            assert loaded == ["moxi_ui", "moxi_ui.testing"]
    """))
    done = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                           str(tmp_path)], cwd=tmp_path, capture_output=True, text=True,
                          timeout=60)
    assert done.returncode == 0, done.stdout + done.stderr


@pytest.mark.browser
def test_browser_tests_and_asyncio_run_share_one_pytest_run(tmp_path, ui_browser):
    # The last test uses no browser, so the browser closes in a teardown that runs with
    # Playwright's loop hidden. The child finds the browsers this run uses: under the
    # fence its home is a temporary one.
    browsers = Path(ui_browser.browser_type.executable_path).parents[2]
    (tmp_path / "test_mixed.py").write_text(textwrap.dedent("""
        import asyncio

        async def two():
            return 2

        def test_a_page(ui_browser):
            page = ui_browser.new_page()
            assert page.evaluate("1 + 1") == 2
            page.close()

        def test_asyncio_after_a_browser():
            assert asyncio.run(two()) == 2

        def test_a_page_again(ui_browser):
            test_a_page(ui_browser)

        def test_asyncio_last():
            assert asyncio.run(two()) == 2
    """))
    done = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                           "--ui-browser", ui_browser.browser_type.name, str(tmp_path)],
                          cwd=tmp_path, capture_output=True, text=True, timeout=60,
                          env={**os.environ, "PLAYWRIGHT_BROWSERS_PATH": str(browsers)})
    assert done.returncode == 0, done.stdout + done.stderr
    assert "4 passed" in done.stdout, done.stdout


def test_the_test_shell_serves_the_fixture_and_reaches_its_page_server(ui_shell):
    session = ui_shell.session()
    status, view = session.get("/m/ui/api/modules")
    modules = {m["name"]: m for m in view["modules"]}
    assert status == 200 and modules["fixture"]["mounted"]
    assert {c["name"] for c in modules["fixture"]["components"]} == {"page", "block", "tag"}
    assert [r["name"] for r in modules["fixture"]["refused"]] == ["later"]
    before = fixture_kit.acts(ui_shell)
    status, answer = session.post("/m/fixture/api/add", {"by": 2})
    assert status == 200 and fixture_kit.acts(ui_shell) == before + 1
    status, summary = session.get("/m/fixture/api/summary")
    assert status == 200 and summary["count"] == answer["count"]
    row = ui_shell.wait_server("fixture", "running")
    with session.stream("/m/ui/api/push?topics=fixture.count") as events:
        assert events.until(lambda name, data: name == "fixture.count")[1] == {"n": answer["count"]}
        assert ui_shell.kill("fixture") == row["pid"]
        assert events.until(lambda name, data: name == "moxi.state")[1]["state"] == "down"
        assert events.until(lambda name, data: name == "moxi.state"
                            and data["state"] == "up")[1]["module"] == "fixture"
    assert ui_shell.wait_server("fixture", lambda r: r["crashes"] >= 1)["crashes"] == 1


def test_a_fixture_that_fails_its_start_is_a_502_naming_exit_3(ui_shells, fixture_site):
    shell = ui_shells(site=[fixture_site], child_args=["--idle-s", "0.5"])
    fixture_kit.fail_start(shell)
    status, body = shell.session().get("/m/fixture/api/summary")
    assert status == 502 and body["error"] == "start" and body["exit"] == 3
    assert shell.wait_server("fixture", "failed")["failed_in_row"] == 1


def test_a_left_out_module_is_not_in_the_snapshot(ui_shells, fixture_site):
    shell = ui_shells(site=[fixture_site], leave_out=["cache"])
    status, view = shell.session().get("/m/ui/api/modules")
    names = {m["name"] for m in view["modules"]}
    assert status == 200 and "fixture" in names and "cache" not in names
    assert shell.session().get("/m/cache/")[0] == 404


@pytest.mark.browser
def test_the_fixture_components_keep_the_contract_and_fit_a_phone(ui_page, ui_component_check,
                                                                 ui_width_check):
    block = ui_component_check(ui_page, "fixture", "block")
    assert block.element == "moxi-fixture-block" and block.tokens == ["accent"]
    # The modules view placed the block already; the tag's script is fetched now.
    tag = ui_component_check(ui_page, "fixture", "tag", {"label": "x"}, keep=True)
    assert any(url.endswith("/m/fixture/tag.js") for url in tag.requests)
    assert tag.locator.evaluate("e => e.shadowRoot.textContent") == "x"
    with pytest.raises(AssertionError, match="did not place"):
        ui_component_check(ui_page, "fixture", "later")
    with pytest.raises(AssertionError, match="--moxi-warn reaches no colour"):
        ui_component_check(ui_page, "fixture", "block", tokens=("warn",))
    ui_width_check(ui_page)
    ui_width_check(ui_page, module="fixture")
    ui_width_check(ui_page, module="ui", route="settings")


def _pushed(page, held, without=()):
    """Wait until the last push stream `page` asked for holds `held` and none of
    `without`: the stream reopens a moment after a panel's components subscribe."""
    def last():
        urls = [r.url for r in page.requests() if "/m/ui/api/push?" in r.url]
        return urls[-1] if urls else ""
    wait_for(lambda: all(t in last() for t in held) and not any(t in last() for t in without),
             10, interval=0.05, what=lambda: f"a push stream for {held}, not {without}: {last()}")


@pytest.mark.browser
def test_the_contract_check_counts_against_a_component_only_the_push_topics_it_adds(
        ui_page, ui_component_check):
    # The push channel is one stream for the page, reopened with every topic the page
    # holds whenever a component subscribes. `elsewhere` stands for another module with a
    # component on the page: its topics are no topic of the fixture's. The settings panel
    # holds no fixture topic, so placing the fixture's block reopens the stream.
    ui_page.evaluate("async () => (await import('/m/ui/client.js')).navigate('ui', 'settings')")
    ui_page.evaluate("""async () => {
      const {subscribe} = await import("/m/ui/client.js");
      subscribe("elsewhere", "held", () => {});
    }""")
    _pushed(ui_page, ["elsewhere.held"], without=["fixture.count"])
    check = ui_component_check(ui_page, "fixture", "block")
    assert any("elsewhere.held" in url and "fixture.count" in url for url in check.requests)
    # A foreign topic that comes with the placement is still the component's, and refused.
    _pushed(ui_page, ["elsewhere.held"], without=["fixture.count"])
    ui_page.evaluate("""async () => {
      const {subscribe} = await import("/m/ui/client.js");
      new MutationObserver((changes, observer) => {
        if (changes.some((c) => [...c.addedNodes].some((n) => n.dataset?.moxiCheck))) {
          observer.disconnect();
          subscribe("elsewhere", "fresh", () => {});
        }
      }).observe(document.getElementById("moxi-panel"), {childList: true});
    }""")
    with pytest.raises(AssertionError, match=r"outside /m/fixture/: .*elsewhere\.fresh") as err:
        ui_component_check(ui_page, "fixture", "block")
    assert "could be read" not in str(err.value)


@pytest.mark.browser
def test_the_contract_check_says_when_it_could_not_read_the_held_topics(
        ui_page, ui_component_check, monkeypatch):
    # Without a page's recent requests (Playwright before 1.56), a topic the page held
    # before the placement counts against the component, and the failure says why.
    ui_page.evaluate("async () => (await import('/m/ui/client.js')).navigate('ui', 'settings')")
    ui_page.evaluate("""async () => {
      const {subscribe} = await import("/m/ui/client.js");
      subscribe("elsewhere", "held", () => {});
    }""")
    _pushed(ui_page, ["elsewhere.held"], without=["fixture.count"])
    monkeypatch.delattr(type(ui_page), "requests")
    with pytest.raises(AssertionError, match=r"elsewhere\.held.*no earlier push request of "
                                             r"the page could be read"):
        ui_component_check(ui_page, "fixture", "block")


@pytest.mark.browser
def test_the_width_check_names_what_reaches_past_the_edge(ui_page, ui_width_check):
    ui_page.evaluate("""() => {
      const wide = document.createElement("div");
      wide.id = "too-wide";
      wide.style.width = "500px";
      document.getElementById("moxi-panel").append(wide);
    }""")
    with pytest.raises(AssertionError, match="div#too-wide"):
        ui_width_check(ui_page)


@pytest.mark.browser
def test_each_context_is_fresh_from_one_stored_sign_in_and_a_refused_one_signs_in_again(
        ui_shells, fixture_site, ui_contexts):
    shell = ui_shells(site=[fixture_site])
    first, second = ui_contexts(shell), ui_contexts(shell)
    assert first is not second
    cookie = {c["name"]: c["value"] for c in first.cookies()}
    assert {c["name"]: c["value"] for c in second.cookies()} == cookie
    # A context of its own: what one test leaves in its storage, the next doesn't see.
    first.pages[0].evaluate("localStorage.setItem('left', '1')")
    assert ui_contexts(shell).pages[0].evaluate("localStorage.getItem('left')") is None
    status, _ = shell.call("POST", "/m/ui/api/signout", {})
    assert status == 200
    third = ui_contexts(shell)
    assert {c["name"]: c["value"] for c in third.cookies()} != cookie
    expect(third.pages[0].locator("#moxi-panel > moxi-ui-page")).to_be_attached()
    status = third.pages[0].evaluate("async () => (await fetch('/m/ui/api/modules', "
                                     "{headers: {'X-Moxi-Key': localStorage.getItem("
                                     "'moxi-ui-key')}})).status")
    assert status == 200

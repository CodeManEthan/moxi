"""moxi_ui.testing: the plugin's load, the test shell over the fixture module, and the
contract and width checks in a browser."""

from __future__ import annotations

import subprocess
import sys
import textwrap

import pytest
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
